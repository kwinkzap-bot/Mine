"""
Heikin-Ashi + SuperTrend swing backtest — a daily trend-following rule on stocks.

The rule, as a Hindi trading talk gives it (nothing here is tuned):

  Chart    Heikin-Ashi candles, DAILY. SuperTrend (default 10, 3) is computed on those
           same HA candles, as a chart set to HA would draw it.
  Entry    The trend must be NEW: SuperTrend has flipped to our side within the last
           `fresh_bars` closed candles (the talk rejects a flip that comes after the move
           is over) and the HA candle is our colour. The first such candle is the signal;
           the order fills at the NEXT day's raw open. One signal per flip.
  Stop     The swing low (long) / swing high (short) of the last `swing_len` raw candles
           up to and including the signal candle. A stop wider than `max_risk_pct` of the
           entry is not taken — the talk's own examples run 10-20 %.
  Target   `rr` x the risk. `partial` of the position is sold there; the rest rides.
  Trail    The rest is held until the first of: SuperTrend flipping against the trade, or
           `opp_candles` consecutive opposite-colour HA candles ("two red candles back to
           back"), or the stop. The exit fills at the NEXT open (the signal is only known at
           the close), which may be tomorrow's gap. `be_after_t1` lifts the stop to the
           entry once the partial is banked (off by default - the talk does not do it).
  Data end A trade still open on the last candle is closed at that close, 'Still Open'.

Stop (`stop_on_close`, default on - a positional trade is never squared off the day it is
taken): the stop is hit when a candle CLOSES beyond it, and the exit fills at the NEXT open,
like the SuperTrend exit, so no trade ends on its entry day. With it off the stop is intrabar:
it fills at the stop, or at the open if the candle gaps through it, and is assumed to hit before
the target within one candle (pessimistic). One position at a time
per symbol. `pnl` is in price points per share, blended over the two halves;
`r_multiple` is that over the initial risk.

This is a backtest only: it has no order path and nothing live reads it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """HA open/high/low/close from raw OHLC (columns open, high, low, close)."""
    o = df['open'].to_numpy(dtype=float)
    h = df['high'].to_numpy(dtype=float)
    l = df['low'].to_numpy(dtype=float)
    c = df['close'].to_numpy(dtype=float)
    n = len(df)
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(n)
    if n:
        ha_o[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([h, ha_o, ha_c]) if n else h
    ha_l = np.minimum.reduce([l, ha_o, ha_c]) if n else l
    return pd.DataFrame({'open': ha_o, 'high': ha_h, 'low': ha_l, 'close': ha_c}, index=df.index)


def _rma(values: np.ndarray, period: int) -> np.ndarray:
    """Pine ta.rma: Wilder smoothing, seeded with the SMA of the first `period` values."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 10, mult: float = 3.0) -> Tuple[np.ndarray, np.ndarray]:
    """TradingView's ta.supertrend. Returns (line, direction); direction is +1 while the
    trend is up (green, line under price) and -1 while it is down, 0 before the ATR exists."""
    n = len(close)
    prev_close = np.concatenate(([close[0] if n else 0.0], close[:-1]))
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    if n:
        tr[0] = high[0] - low[0]
    atr = _rma(tr, period)
    hl2 = (high + low) / 2.0
    up = hl2 - mult * atr
    dn = hl2 + mult * atr
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int64)
    f_up = f_dn = np.nan
    d = 1
    for i in range(n):
        if not np.isfinite(atr[i]):
            continue
        if not np.isfinite(f_up):                      # the first bar with an ATR
            f_up, f_dn, d = up[i], dn[i], 1
        else:
            f_up = max(up[i], f_up) if close[i - 1] > f_up else up[i]
            f_dn = min(dn[i], f_dn) if close[i - 1] < f_dn else dn[i]
            if d == -1 and close[i] > f_dn:
                d = 1
            elif d == 1 and close[i] < f_up:
                d = -1
        direction[i] = d
        line[i] = f_up if d == 1 else f_dn
    return line, direction


class HaSupertrendEngine:
    def __init__(self, df: pd.DataFrame, st_period: int = 10, st_mult: float = 3.0,
                 st_source: str = 'ha', swing_len: int = 8, fresh_bars: int = 2,
                 rr: float = 2.0, partial: float = 0.5, opp_candles: int = 2,
                 max_risk_pct: float = 25.0, be_after_t1: bool = False,
                 direction: str = 'both', stop_on_close: bool = True):
        self.st_period = max(1, int(st_period))
        self.st_mult = float(st_mult)
        self.st_source = st_source if st_source in ('ha', 'raw') else 'ha'
        self.swing_len = max(1, int(swing_len))
        self.fresh_bars = max(1, int(fresh_bars))
        self.rr = max(0.1, float(rr))
        self.partial = min(1.0, max(0.0, float(partial)))
        self.opp_candles = max(1, int(opp_candles))
        self.max_risk_pct = max(0.0, float(max_risk_pct or 0))
        self.be_after_t1 = bool(be_after_t1)
        self.stop_on_close = bool(stop_on_close)
        self.direction = direction if direction in ('long', 'short', 'both') else 'both'
        self.df = self._prepare(df)

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(d.index):
            col = 'date' if 'date' in d.columns else 'datetime'
            d = d.set_index(col)
            d.index = pd.to_datetime(d.index)
        d.columns = [c.lower() for c in d.columns]
        d = d.sort_index()
        return d[~d.index.duplicated(keep='last')]

    # ── Signals ────────────────────────────────────────────────────────────────

    def indicators(self) -> Dict[str, np.ndarray]:
        ha = heikin_ashi(self.df)
        src = ha if self.st_source == 'ha' else self.df
        line, trend = supertrend(src['high'].to_numpy(dtype=float), src['low'].to_numpy(dtype=float),
                                 src['close'].to_numpy(dtype=float), self.st_period, self.st_mult)
        return {'ha_open': ha['open'].to_numpy(), 'ha_close': ha['close'].to_numpy(),
                'st': line, 'trend': trend}

    def signals(self, ind: Optional[Dict[str, np.ndarray]] = None) -> np.ndarray:
        """+1 / -1 on the candle that is the entry signal (the order fills the next open)."""
        ind = ind or self.indicators()
        trend, ha_o, ha_c = ind['trend'], ind['ha_open'], ind['ha_close']
        n = len(trend)
        sig = np.zeros(n, dtype=np.int64)
        flip_at = -1                                    # the candle the current trend began on
        taken = False                                   # one signal per flip
        for i in range(1, n):
            if trend[i] == 0:
                continue
            if trend[i] != trend[i - 1] and trend[i - 1] != 0:
                flip_at, taken = i, False
            if flip_at < 0 or taken or i - flip_at >= self.fresh_bars:
                continue
            green, red = ha_c[i] > ha_o[i], ha_c[i] < ha_o[i]
            if trend[i] == 1 and green and self.direction != 'short':
                sig[i], taken = 1, True
            elif trend[i] == -1 and red and self.direction != 'long':
                sig[i], taken = -1, True
        return sig

    # ── Trades ─────────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        trades = self.simulate()
        return trades, summarise_r(trades)

    def simulate(self) -> List[Dict[str, Any]]:
        df = self.df
        n = len(df)
        if n < self.st_period + 3:
            return []
        idx = df.index
        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        ind = self.indicators()
        sig = self.signals(ind)
        trend, ha_o, ha_c = ind['trend'], ind['ha_open'], ind['ha_close']
        stamp = lambda k: idx[k].strftime('%Y-%m-%d')    # noqa: E731
        trades: List[Dict[str, Any]] = []
        free_from = 0                                    # no new trade before the last one is out
        for i in np.flatnonzero(sig):
            e = int(i) + 1
            if e >= n or e < free_from:
                continue
            is_long = sig[i] == 1
            lo = max(0, int(i) - self.swing_len + 1)
            sl = float(l[lo:i + 1].min()) if is_long else float(h[lo:i + 1].max())
            entry = o[e]
            risk = (entry - sl) if is_long else (sl - entry)
            if risk <= 0 or (self.max_risk_pct and risk / entry * 100 > self.max_risk_pct):
                continue
            tgt = entry + risk * self.rr if is_long else entry - risk * self.rr
            stop = sl
            qty_left, booked, t1_px, t1_k = 1.0, 0.0, None, None
            exit_k, exit_px, reason = n - 1, c[n - 1], 'Still Open'
            for k in range(e, n):
                # Intrabar: the stop first, then the target.
                if not self.stop_on_close and ((l[k] <= stop) if is_long else (h[k] >= stop)):
                    gap = min(o[k], stop) if is_long else max(o[k], stop)
                    fill = stop if k == e else gap
                    exit_k, exit_px = k, fill
                    reason = 'Stop Loss' if t1_px is None or stop != entry else 'Breakeven Stop'
                    break
                if t1_px is None and self.partial > 0 and ((h[k] >= tgt) if is_long else (l[k] <= tgt)):
                    t1_px = max(o[k], tgt) if is_long else min(o[k], tgt)
                    t1_k = k
                    booked = self.partial * ((t1_px - entry) if is_long else (entry - t1_px))
                    qty_left = 1.0 - self.partial
                    if self.be_after_t1:
                        stop = entry
                    if qty_left <= 1e-9:
                        exit_k, exit_px, reason = k, t1_px, 'Target'
                        break
                # Signals known at this close: the fill is the next open.
                closed_through = (c[k] < stop) if is_long else (c[k] > stop)
                st_flip = trend[k] != 0 and trend[k] == (-1 if is_long else 1)
                opp = False
                if k - self.opp_candles + 1 >= e:
                    seg = slice(k - self.opp_candles + 1, k + 1)
                    opp = bool(np.all(ha_c[seg] < ha_o[seg])) if is_long else bool(np.all(ha_c[seg] > ha_o[seg]))
                if closed_through or st_flip or opp:
                    why = (('Stop Loss' if t1_px is None or stop != entry else 'Breakeven Stop') if closed_through
                           else 'SuperTrend flip' if st_flip else f'{self.opp_candles} opposite HA candles')
                    if k + 1 < n:
                        exit_k, exit_px, reason = k + 1, o[k + 1], why
                    else:
                        exit_k, exit_px, reason = k, c[k], 'Still Open'
                    break
            tail = (exit_px - entry) if is_long else (entry - exit_px)
            pnl = booked + qty_left * tail
            free_from = exit_k + 1
            trades.append({
                'entry_time': stamp(e), 'exit_time': stamp(exit_k), 'signal_time': stamp(int(i)),
                'type': 'Long' if is_long else 'Short',
                'entry_price': round(float(entry), 2), 'sl_price': round(float(sl), 2),
                'target_price': round(float(tgt), 2),
                'exit_price': round(float(exit_px), 2),
                'partial_price': round(float(t1_px), 2) if t1_px is not None else None,
                'partial_time': stamp(t1_k) if t1_k is not None else None,
                'pnl': round(float(pnl), 2),
                'pnl_pct': round(float(pnl / entry * 100), 2),
                'risk': round(float(risk), 2), 'risk_pct': round(float(risk / entry * 100), 2),
                'r_multiple': round(float(pnl / risk), 2),
                'bars_held': int(exit_k - e + 1),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
            })
        return trades


def summarise_r(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Win rate, expectancy in R and the usual P&L stats over a list of trades."""
    if not trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0, 'total_pnl': 0.0,
                'avg_win': 0.0, 'avg_loss': 0.0, 'profit_factor': 0.0, 'max_drawdown': 0.0,
                'expectancy_r': 0.0, 'avg_r_win': 0.0, 'avg_r_loss': 0.0, 'avg_bars_held': 0.0,
                'avg_pnl_pct': 0.0}
    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']
    gp = sum(t['pnl_pct'] for t in wins)
    gl = abs(sum(t['pnl_pct'] for t in losses))
    running = peak = dd = 0.0
    for t in trades:
        running += t['pnl_pct']
        peak = max(peak, running)
        dd = max(dd, peak - running)
    n = len(trades)
    return {
        'total_trades': n, 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / n * 100, 1),
        'total_pnl': round(sum(t['pnl_pct'] for t in trades), 2),     # sum of per-trade %
        'avg_win': round(gp / len(wins), 2) if wins else 0.0,
        'avg_loss': round(gl / len(losses), 2) if losses else 0.0,
        'profit_factor': round(gp / gl, 2) if gl > 0 else (999.0 if gp > 0 else 0.0),
        'max_drawdown': round(dd, 2),
        'expectancy_r': round(sum(t['r_multiple'] for t in trades) / n, 3),
        'avg_r_win': round(sum(t['r_multiple'] for t in wins) / len(wins), 2) if wins else 0.0,
        'avg_r_loss': round(sum(t['r_multiple'] for t in losses) / len(losses), 2) if losses else 0.0,
        'avg_bars_held': round(sum(t['bars_held'] for t in trades) / n, 1),
        'avg_pnl_pct': round(sum(t['pnl_pct'] for t in trades) / n, 2),
    }
