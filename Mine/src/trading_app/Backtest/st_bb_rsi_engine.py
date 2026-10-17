"""
SuperTrend + Bollinger + RSI + Pivot momentum backtest ("ST·BB·RSI·R1").

A long-only momentum entry on the INDEX, taken on the CALL option whose premium
is nearest 250. Four indicators must agree on the same closed candle:

  1. RSI(14) is above 70                         — momentum is stretched up
  2. Close is above the upper Bollinger Band     — BB(20, 2, offset 0)
  3. SuperTrend(10, 2) is on the UP side         — close above the ST line
  4. Close is above R1 of yesterday's floor pivots
         P = (PDH + PDL + PDC) / 3,  R1 = 2P − PDL

There is NO target and NO stop. The one exit is the SuperTrend breaking down
(the candle closes back below the ST line, so its direction turns down). The
session cut-off (`exit_cutoff`, default 15:15) squares off anything still open,
because this is an intraday system (it mirrors the Quantman run "Pivot Points copy-2": 3-minute candles, ST 10/2, BB 20/2, RSI 14 > 70, close above R1, trade window 09:15-15:14, one trade a day, Current-week CE ≈250, exit when the close is below SuperTrend). `require_green` is an optional extra, off by default, because that run has no such rule — it is a safety net, not a rule of the trade.

Fills: a signal is read on a CLOSED candle and filled at the NEXT candle's open
(entry, and the exit after a SuperTrend break). The engine itself books INDEX
points; `attach_option_legs` then re-prices every trade on the ≈250 premium CE
(service/cpr_option_service: the weekly contract running that day, strike walked
to the premium nearest 250 at the entry minute, premium read off the contract's
own 1-minute candles) and replaces `pnl` with the premium points. That needs an
ICICI Breeze login, since only Breeze serves expired contracts.

Indicator conventions are TradingView's: Wilder (RMA) smoothing for ATR and RSI,
population standard deviation for the bands. Indicators run continuously across
days (not reset each morning), as they do on a chart.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Backstop on legs priced per run. Each costs Breeze requests the first time
# (pick_strike walks the strike ladder) and that budget is shared with the live
# algos, so the run is a background task and settled sessions are disk-cached:
# a second run over the same trades is nearly free. The most recent are kept.
MAX_OPTION_LEGS = 3000
_CHUNK = 4             # trades per legs_at call — small, so the calls spread over the workers
_WORKERS = 6


def _parse_hhmm(value, default_mins: int) -> int:
    if value is None:
        return default_mins
    text = str(value).strip()
    if not text:
        return default_mins
    try:
        hh, mm = text.split(':')[:2]
        return int(hh) * 60 + int(mm)
    except (TypeError, ValueError):
        return default_mins


# ── Indicators ─────────────────────────────────────────────────────────────────

def _rma(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (Pine ta.rma): seeded with the SMA of the first
    `period` values, NaN before that."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    out[period - 1] = np.nanmean(values[:period])
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = loss[0] = np.nan
    ag = np.full(len(close), np.nan)
    al = np.full(len(close), np.nan)
    if len(close) > period:
        ag[period] = gain[1:period + 1].mean()
        al[period] = loss[1:period + 1].mean()
        for i in range(period + 1, len(close)):
            ag[i] = (ag[i - 1] * (period - 1) + gain[i]) / period
            al[i] = (al[i - 1] * (period - 1) + loss[i]) / period
    with np.errstate(divide='ignore', invalid='ignore'):
        rs = ag / al
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((al == 0) & np.isfinite(ag), 100.0, out)
    return out


def bollinger(close: np.ndarray, period: int = 20, mult: float = 2.0, offset: int = 0
              ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    s = pd.Series(close)
    mid = s.rolling(period).mean()
    sd = s.rolling(period).std(ddof=0)
    up, mid, lo = mid + mult * sd, mid, mid - mult * sd
    if offset:                       # TradingView's Offset: plot shifted by N bars
        up, mid, lo = up.shift(offset), mid.shift(offset), lo.shift(offset)
    return up.to_numpy(), mid.to_numpy(), lo.to_numpy()


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 10, mult: float = 3.0) -> Tuple[np.ndarray, np.ndarray]:
    """(line, direction) with direction +1 = up side (line under price), −1 = down."""
    n = len(close)
    prev_close = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum.reduce([high - low,
                            np.abs(high - prev_close),
                            np.abs(low - prev_close)])
    tr[0] = high[0] - low[0]
    atr = _rma(tr, period)
    hl2 = (high + low) / 2.0
    up_basic = hl2 - mult * atr
    dn_basic = hl2 + mult * atr
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)
    line = np.full(n, np.nan)
    for i in range(n):
        if not np.isfinite(atr[i]):
            continue
        if i == 0 or not np.isfinite(up[i - 1]):
            up[i], dn[i], direction[i] = up_basic[i], dn_basic[i], 1
        else:
            up[i] = max(up_basic[i], up[i - 1]) if close[i - 1] > up[i - 1] else up_basic[i]
            dn[i] = min(dn_basic[i], dn[i - 1]) if close[i - 1] < dn[i - 1] else dn_basic[i]
            if direction[i - 1] == -1 and close[i] > dn[i - 1]:
                direction[i] = 1
            elif direction[i - 1] == 1 and close[i] < up[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
        line[i] = up[i] if direction[i] == 1 else dn[i]
    return line, direction


# ── Engine ─────────────────────────────────────────────────────────────────────

class StBbRsiEngine:

    def __init__(self, df: pd.DataFrame, rsi_period: int = 14, rsi_level: float = 70.0,
                 bb_period: int = 20, bb_mult: float = 2.0, bb_offset: int = 0,
                 st_period: int = 10, st_mult: float = 2.0,
                 entry_start: str = '09:15', entry_end: str = '15:14',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 1,
                 require_green: bool = False):
        self.df = df.copy()
        self.rsi_period = max(2, int(rsi_period))
        self.rsi_level = float(rsi_level)
        self.bb_period = max(2, int(bb_period))
        self.bb_mult = float(bb_mult)
        self.bb_offset = int(bb_offset or 0)
        self.st_period = max(2, int(st_period))
        self.st_mult = float(st_mult)
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 15)
        self.entry_end = _parse_hhmm(entry_end, 15 * 60 + 14)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 14)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self.require_green = bool(require_green)
        self._prepare()

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            col = 'date' if 'date' in self.df.columns else 'datetime'
            self.df = self.df.set_index(col)
            self.df.index = pd.to_datetime(self.df.index)
        self.df.columns = [c.lower() for c in self.df.columns]
        self.df = self.df.sort_index()
        self.df = self.df[~self.df.index.duplicated(keep='last')]

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if self.df.empty:
            return [], summarise([])
        df = self.df
        o, h, l, c = (df[k].to_numpy(dtype=float) for k in ('open', 'high', 'low', 'close'))
        idx = df.index
        date_key = pd.Series(idx.date, index=idx)

        rsi_v = rsi(c, self.rsi_period)
        bb_up, _, _ = bollinger(c, self.bb_period, self.bb_mult, self.bb_offset)
        st_line, st_dir = supertrend(h, l, c, self.st_period, self.st_mult)

        daily = df.groupby(date_key).agg(d_high=('high', 'max'), d_low=('low', 'min'),
                                         d_close=('close', 'last'))
        pdh, pdl, pdc = (daily[k].shift(1) for k in ('d_high', 'd_low', 'd_close'))
        pivot = (pdh + pdl + pdc) / 3.0
        r1 = (2 * pivot - pdl)
        r1_bar = date_key.map(r1).to_numpy(dtype=float)

        tmin = (idx.hour * 60 + idx.minute).to_numpy()
        ts = idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy()
        day_id = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()

        with np.errstate(invalid='ignore'):
            signal = ((rsi_v > self.rsi_level) & (c > bb_up) & (st_dir == 1)
                      & np.isfinite(st_line) & (c > r1_bar))
            if self.require_green:                      # signal candle closes above its open
                signal = signal & (c > o)

        trades: List[Dict[str, Any]] = []
        n = len(df)
        start = 0
        while start < n:
            end = start
            while end + 1 < n and day_id[end + 1] == day_id[start]:
                end += 1
            trades.extend(self._day(start, end, o, c, signal, st_dir, st_line,
                                    rsi_v, bb_up, r1_bar, tmin, ts))
            start = end + 1
        return trades, summarise(trades)

    def _day(self, a, b, o, c, signal, st_dir, st_line, rsi_v, bb_up, r1_bar, tmin, ts):
        out = []
        i = a
        while i < b and len(out) < self.max_trades_per_day:
            if not (signal[i] and self.entry_start <= tmin[i] < self.entry_end):
                i += 1
                continue
            e = i + 1                                   # filled at the next open
            if tmin[e] >= self.exit_cutoff:
                break
            entry = o[e]
            exit_k, exit_px, reason = b, c[b], 'EOD Exit'
            for k in range(e, b + 1):
                if k > e and tmin[k] >= self.exit_cutoff:
                    exit_k, exit_px, reason = k, o[k], 'Time Exit'
                    break
                if st_dir[k] == -1:                      # SuperTrend broke down on this close
                    if k < b and tmin[k + 1] < self.exit_cutoff:
                        exit_k, exit_px, reason = k + 1, o[k + 1], 'SuperTrend Down'
                    else:
                        exit_k, exit_px, reason = k, c[k], 'SuperTrend Down'
                    break
            pnl = exit_px - entry
            out.append({
                'entry_time': ts[e], 'exit_time': ts[exit_k],
                'type': 'Long', 'pattern': 'ST+BB+RSI+R1',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': None, 'target_price': None,
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': {'rsi': round(float(rsi_v[i]), 1), 'close': round(float(c[i]), 2),
                           'bb_upper': round(float(bb_up[i]), 2),
                           'supertrend': round(float(st_line[i]), 2),
                           'r1': round(float(r1_bar[i]), 2)},
            })
            i = max(exit_k, e) + 1
        return out


# ── Option leg ─────────────────────────────────────────────────────────────────

def _stamp_to_hhmm(stamp: str) -> str:
    return stamp[11:16]


def attach_option_legs(symbol: str, trades: List[Dict[str, Any]], premium: float = 250.0,
                       bar_minutes: int = 5, progress=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Re-price each index trade on the ≈`premium` CE. Returns (priced trades,
    info). A priced trade keeps its index move in `index_*` and carries the
    premium points in `pnl`; a trade whose leg could not be priced is dropped
    from the list and counted in info['unpriced']."""
    from trading_app.service import cpr_option_service as cos

    info: Dict[str, Any] = {'premium': premium, 'index_trades': len(trades),
                            'unpriced': 0, 'truncated': 0}
    if not trades:
        return [], info
    work = trades[-MAX_OPTION_LEGS:]
    info['truncated'] = len(trades) - len(work)

    def exit_minute(t):
        # An exit on the final bar of the session has no "next open"; use its last minute.
        hhmm = _stamp_to_hhmm(t['exit_time'])
        return hhmm

    reqs = []
    for n, t in enumerate(work):
        reqs.append({'key': n, 'date': t['entry_time'][:10], 'side': 'BUY',
                     'level': t['entry_price'], 'entry_time': _stamp_to_hhmm(t['entry_time']),
                     'exit_time': exit_minute(t), 'exit_price': t['exit_price']})
    # Breeze meters requests app-wide, so threads cannot beat that pace on a cold
    # run — but each strike probe also spends most of its time waiting on the
    # network, and a warm (disk-cached) run is CPU/IO bound. Small chunks across
    # a few workers hide that latency; map() keeps the trades in order.
    chunks = [reqs[i:i + _CHUNK] for i in range(0, len(reqs), _CHUNK)]
    results = []
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(cos.legs_at, symbol, ch, premium=premium, span_min=1)
                   for ch in chunks]
        for n, fut in enumerate(futures, 1):
            results.append(fut.result())
            if progress:
                progress(min(n * _CHUNK, len(reqs)), len(reqs))
    legs: List[Dict[str, Any]] = []
    for res in results:
        legs.extend(res['legs'])
        info['lot'] = res.get('lot')

    priced = []
    for t, leg in zip(work, legs):
        if leg.get('pnl') is None:
            info['unpriced'] += 1
            continue
        pnl = leg['pnl']
        # entry_price / exit_price stay the INDEX's — they are what the chart
        # shows and what the signal and the SuperTrend exit were decided on.
        # Only the order is the option: its premiums and P&L ride beside them.
        priced.append(dict(
            t,
            index_pnl=t['pnl'],
            entry_premium=leg['entry'], exit_premium=leg['exit'], pnl=pnl,
            result='WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            strike=leg['strike'], option_type=leg['option_type'], expiry=leg['expiry'],
        ))
    return priced, info


# ── Summary ────────────────────────────────────────────────────────────────────

def summarise(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0,
                'total_pnl': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0,
                'profit_factor': 0.0, 'max_drawdown': 0.0}
    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses))
    running = peak = max_dd = 0.0
    for t in trades:
        running += t['pnl']
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return {
        'total_trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'total_pnl': round(sum(t['pnl'] for t in trades), 2),
        'avg_win': round(gp / len(wins), 2) if wins else 0.0,
        'avg_loss': round(gl / len(losses), 2) if losses else 0.0,
        'profit_factor': round(gp / gl, 2) if gl > 0 else (999.0 if gp > 0 else 0.0),
        'max_drawdown': round(max_dd, 2),
    }
