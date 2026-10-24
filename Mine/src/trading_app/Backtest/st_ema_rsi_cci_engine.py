"""
SuperTrend + EMA + RSI + CCI + Williams %R + CPR + Bollinger backtest
("ST EMA RSI CCI WR CPR BB with SL").

Mirrors the Quantman run "ST EMA RSI CCI WR CPR BB with SL NIFTY PE": 3-minute
candles, trade window 09:15-15:14, at most 5 transactions a day, and a
current-week PE bought at the premium nearest 250 with a premium stop-loss and a
trailing stop. The trade is read on the INDEX and the order is an option, exactly
as the ST + BB + RSI backtest does it (`attach_option_legs` re-prices it).

Short (PE) entry — ALL on one CLOSED candle:

  1. close is below SuperTrend(10, 2)
  2. close is below EMA(200)
  3. RSI(20) is below 30
  4. CCI(20) is below -200
  5. Williams %R(14) is below -85
  6. close is below S1 of today's CPR (traditional pivots from yesterday's H/L/C)
  7. today's CPR is NARROW  (width < `cpr_narrow`, 0.2 %)
  8. close is below the lower Bollinger Band(20, 2)

Exit: a closed candle with RSI(20) ABOVE 45 (the mirror of the entry's oversold
read), the premium stop-loss / trailing stop (priced in `attach_option_legs`), or
the 15:14 square-off. There is no index stop or target. `direction='long'` runs
the exact mirror (CE: above everything, RSI > 70, CCI > 200, %R > -15, close
above R1, exit RSI < 55); the screenshot only has the PE side, so the default
is 'short'.

Fills: a signal is read on a closed candle and filled at the NEXT candle's open,
exits likewise. One trade open at a time; the next signal can only come on a
candle after the previous exit.

Conventions are TradingView's (Wilder RMA for the ATR, population stdev for the
bands, EMA seeded on its first value, CCI = (tp - sma) / (0.015 x mean abs dev))
and the indicators run continuously across days, as on a chart.

Two readings are INFERRED from the screenshot, which shows only the settings:
  * CPR width % = |TC - BC| / pivot x 100, "narrow" when under `cpr_narrow`
    (Quantman lists Narrow 0.2 / Moderately 0.5 / Wide 0.5);
  * the "Future" badge on every indicator means Quantman computed them on the
    NIFTY future; this run, like the ST + BB + RSI one, reads the index candles.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import (
    _parse_hhmm, _r, bollinger, rsi, supertrend, williams_r)


def ema(close: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(close).ewm(span=period, adjust=False).mean().to_numpy()


def cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 20) -> np.ndarray:
    tp = pd.Series((high + low + close) / 3.0)
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda w: np.mean(np.abs(w - w.mean())), raw=True)
    with np.errstate(divide='ignore', invalid='ignore'):
        return ((tp - sma) / (0.015 * mad)).to_numpy()


class StEmaRsiCciEngine:
    def __init__(self, df: pd.DataFrame,
                 st_period: int = 10, st_mult: float = 2.0,
                 ema_period: int = 200,
                 rsi_period: int = 20, rsi_entry: float = 30.0, rsi_exit: float = 45.0,
                 cci_period: int = 20, cci_level: float = -200.0,
                 wr_period: int = 14, wr_level: float = -85.0,
                 bb_period: int = 20, bb_mult: float = 2.0,
                 cpr_narrow: float = 0.2, use_cpr_narrow: bool = True,
                 entry_start: str = '09:15', entry_end: str = '15:14',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 5,
                 direction: str = 'short'):
        self.df = df.copy()
        self.st_period, self.st_mult = max(2, int(st_period)), float(st_mult)
        self.ema_period = max(2, int(ema_period))
        self.rsi_period = max(2, int(rsi_period))
        self.rsi_entry, self.rsi_exit = float(rsi_entry), float(rsi_exit)
        self.cci_period, self.cci_level = max(2, int(cci_period)), float(cci_level)
        self.wr_period, self.wr_level = max(2, int(wr_period)), float(wr_level)
        self.bb_period, self.bb_mult = max(2, int(bb_period)), float(bb_mult)
        self.cpr_narrow, self.use_cpr_narrow = float(cpr_narrow), bool(use_cpr_narrow)
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 15)
        self.entry_end = _parse_hhmm(entry_end, 15 * 60 + 14)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 14)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self.direction = direction if direction in ('long', 'short', 'both') else 'short'
        self._prepare()

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            col = 'date' if 'date' in self.df.columns else 'datetime'
            self.df = self.df.set_index(col)
            self.df.index = pd.to_datetime(self.df.index)
        self.df.columns = [c.lower() for c in self.df.columns]
        self.df = self.df.sort_index()
        self.df = self.df[~self.df.index.duplicated(keep='last')]

    # ── Series ────────────────────────────────────────────────────────────────

    def _cpr(self) -> Dict[str, np.ndarray]:
        """Per bar: today's traditional CPR from yesterday's H/L/C — pivot, BC, TC,
        S1 = 2P - H, R1 = 2P - L, and the width as a % of the pivot."""
        df, idx = self.df, self.df.index
        date_key = pd.Series(idx.date, index=idx)
        daily = df.groupby(date_key).agg(h=('high', 'max'), l=('low', 'min'), c=('close', 'last'))
        pdh, pdl, pdc = (daily[k].shift(1) for k in ('h', 'l', 'c'))
        p = (pdh + pdl + pdc) / 3.0
        bc = (pdh + pdl) / 2.0
        tc = 2.0 * p - bc
        out = {'s1': 2.0 * p - pdh, 'r1': 2.0 * p - pdl, 'width': (tc - bc).abs() / p * 100.0}
        return {k: date_key.map(v).to_numpy(dtype=float) for k, v in out.items()}

    def signals(self) -> Dict[str, Any]:
        """Per bar +1 long / -1 short / 0 none (before the entry window), with the
        series a decision needs."""
        df = self.df
        o, h, l, c = (df[k].to_numpy(dtype=float) for k in ('open', 'high', 'low', 'close'))
        st_line, _ = supertrend(h, l, c, self.st_period, self.st_mult)
        ema_v = ema(c, self.ema_period)
        rsi_v = rsi(c, self.rsi_period)
        cci_v = cci(h, l, c, self.cci_period)
        wr_v = williams_r(h, l, c, self.wr_period)
        bb_up, _, bb_lo = bollinger(c, self.bb_period, self.bb_mult)
        cpr = self._cpr()
        narrow = (cpr['width'] < self.cpr_narrow) if self.use_cpr_narrow else np.ones(len(c), dtype=bool)
        with np.errstate(invalid='ignore'):
            short = ((c < st_line) & (c < ema_v) & (rsi_v < self.rsi_entry)
                     & (cci_v < self.cci_level) & (wr_v < self.wr_level)
                     & (c < cpr['s1']) & narrow & (c < bb_lo))
            long_ = ((c > st_line) & (c > ema_v) & (rsi_v > 100.0 - self.rsi_entry)
                     & (cci_v > -self.cci_level) & (wr_v > -100.0 - self.wr_level)
                     & (c > cpr['r1']) & narrow & (c > bb_up))
        if self.direction == 'long':
            short = np.zeros(len(c), dtype=bool)
        elif self.direction == 'short':
            long_ = np.zeros(len(c), dtype=bool)
        return {'signal': np.where(long_, 1, np.where(short, -1, 0)),
                'o': o, 'c': c, 'rsi': rsi_v, 'st_line': st_line, 'ema': ema_v, 'cci': cci_v,
                'wr': wr_v, 'bb_up': bb_up, 'bb_lo': bb_lo, 'cpr': cpr}

    # ── Simulation ────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        from trading_app.Backtest.st_bb_rsi_engine import summarise
        if self.df.empty:
            return [], summarise([])
        trades = self.simulate()
        return trades, summarise(trades)

    def simulate(self) -> List[Dict[str, Any]]:
        ctx = self._context()
        cand, starts, ends = ctx['cand'], ctx['starts'], ctx['ends']
        if not len(cand):
            return []
        day_of = np.searchsorted(starts, cand, side='right') - 1
        trades: List[Dict[str, Any]] = []
        for d in np.unique(day_of):
            trades.extend(self._day(int(starts[d]), int(ends[d]), cand[day_of == d], ctx))
        return trades

    def _context(self) -> Dict[str, Any]:
        if getattr(self, '_ctx', None) is None:
            sg = self.signals()
            idx = self.df.index
            tmin = (idx.hour * 60 + idx.minute).to_numpy()
            window = (tmin >= self.entry_start) & (tmin < self.entry_end)
            signal = sg['signal'] * window
            day_id = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
            bounds = np.flatnonzero(np.diff(day_id)) + 1
            n = len(sg['c'])
            self._ctx = {'sg': sg, 'o': sg['o'], 'c': sg['c'], 'rsi_v': sg['rsi'], 'tmin': tmin,
                         'ts': idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy(), 'signal': signal,
                         'cand': np.flatnonzero(signal),
                         'starts': np.concatenate(([0], bounds)),
                         'ends': np.concatenate((bounds, [n])) - 1}
        return self._ctx

    def trade_after(self, day: str, after_ts: str, taken: int) -> Optional[Dict[str, Any]]:
        """The next trade of `day` entered at/after `after_ts` ('YYYY-MM-DD HH:MM:SS'),
        for when the option's own stop ended the previous one before the index rule
        did: Quantman re-enters on the next candle that still signals. The new entry
        must be STRICTLY after the stop's minute: a stop at 09:24:29 is followed by
        the 09:27 candle, never the 09:24 one it happened inside. None once
        `max_trades_per_day` is used up or no signal is left."""
        ctx = self._context()
        if taken >= self.max_trades_per_day:
            return None
        ts = ctx['ts']
        for d, a in enumerate(ctx['starts']):
            if ts[a][:10] == day:
                b = int(ctx['ends'][d])
                idxs = [i for i in ctx['cand'] if a <= i < b and ts[i + 1] > after_ts]
                out = self._day(int(a), b, idxs, ctx, limit=1)
                return out[0] if out else None
        return None

    def _day(self, a, b, idxs, ctx, limit=None):
        o, c, signal, rsi_v = ctx['o'], ctx['c'], ctx['signal'], ctx['rsi_v']
        tmin, ts, sg = ctx['tmin'], ctx['ts'], ctx['sg']
        out: List[Dict[str, Any]] = []
        free_from = a                                   # a new trade cannot start before the last one ended
        cutoff = self.exit_cutoff
        for i in idxs:
            if len(out) >= (limit or self.max_trades_per_day):
                break
            if i < free_from or i >= b:
                continue
            e = i + 1                                   # filled at the next open
            if tmin[e] >= cutoff:
                break
            is_long = signal[i] == 1
            entry = o[e]
            exit_k, exit_px, reason = b, c[b], 'EOD Exit'
            for k in range(e, b + 1):
                if k > e and tmin[k] >= cutoff:
                    exit_k, exit_px, reason = k, o[k], 'Time Exit'
                    break
                # RSI has come back: above `rsi_exit` ends a short, below 100 - rsi_exit a long.
                if (rsi_v[k] < 100.0 - self.rsi_exit) if is_long else (rsi_v[k] > self.rsi_exit):
                    if k < b and tmin[k + 1] < cutoff:
                        exit_k, exit_px, reason = k + 1, o[k + 1], 'RSI Exit'
                    else:
                        # Known only when this candle closes, at/after the square-off minute:
                        # the clock has already squared the trade off.
                        exit_k, exit_px, reason = k, c[k], 'Time Exit'
                    break
            pnl = (exit_px - entry) if is_long else (entry - exit_px)
            out.append({
                'entry_time': ts[e],
                # A square-off is a clock event: Quantman sells at 15:14 itself.
                'exit_time': (ts[exit_k][:11] + f"{cutoff // 60:02d}:{cutoff % 60:02d}:00"
                              if reason == 'Time Exit' else ts[exit_k]),
                'days_held': 0,
                'type': 'Long' if is_long else 'Short',
                'pattern': 'ST+EMA+RSI+CCI+WR+CPR+BB',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': None, 'target_price': None,
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': {'close': _r(c[i]), 'rsi': _r(rsi_v[i], 1), 'cci': _r(sg['cci'][i], 1),
                           'wr': _r(sg['wr'][i], 1), 'supertrend': _r(sg['st_line'][i]),
                           'ema': _r(sg['ema'][i]),
                           'bb_band': _r((sg['bb_up'] if is_long else sg['bb_lo'])[i]),
                           'pivot_level': _r(sg['cpr']['r1' if is_long else 's1'][i]),
                           'cpr_width': _r(sg['cpr']['width'][i], 3)},
            })
            free_from = max(exit_k, e) + 1
        return out


_STOPS = ('Stop Loss', 'Trailing SL')


def price_with_reentries(engine: 'StEmaRsiCciEngine', trades: List[Dict[str, Any]], price_fn,
                         progress=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Price `trades` on the option and let the option's own stop free the day.

    The index rule only knows its RSI exit, so it holds the day until then. But a
    premium stop-loss / trailing stop can end the trade much earlier, and Quantman
    then re-enters on the next candle that still signals ("ReEntry: Next Minute").
    Rounds: price each day's current trade; if the premium stop ended it before the
    index exit did, the day's remaining index trades are discarded and the engine
    looks for the next signal after the stop (`trade_after`); otherwise the day goes
    on with its next index trade. `price_fn(trades) -> (priced, info)` is
    attach_option_legs bound to its settings."""
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        by_day.setdefault(t['entry_time'][:10], []).append(t)
    queue = {d: list(v) for d, v in by_day.items()}
    taken = {d: 0 for d in by_day}
    done: List[Dict[str, Any]] = []
    info: Dict[str, Any] = {'index_trades': len(trades), 'unpriced': 0, 'truncated': 0, 'reentries': 0}
    live = {d: q.pop(0) for d, q in queue.items() if q}
    base = 0
    while live:
        batch = list(live.values())
        priced, inf = price_fn(batch, (lambda dn, tot, base=base: progress(base + dn, base + tot)) if progress else None)
        base += len(batch)
        info['unpriced'] += inf.get('unpriced', 0)
        info['truncated'] += inf.get('truncated', 0)
        info.update({k: v for k, v in inf.items() if k not in ('index_trades', 'unpriced', 'truncated')})
        priced_by_entry = {p['entry_time']: p for p in priced}
        nxt: Dict[str, Dict[str, Any]] = {}
        for d, t in live.items():
            p = priced_by_entry.get(t['entry_time'])
            taken[d] += 1
            if p is None:                       # no option candles: the index trade stands, but is not counted
                n = queue[d].pop(0) if queue[d] else None
            else:
                done.append(p)
                if p.get('exit_reason') in _STOPS and p['exit_time'] < t['exit_time']:
                    queue[d] = []
                    n = engine.trade_after(d, p['exit_time'], taken[d])
                    info['reentries'] += 1 if n else 0
                else:
                    n = queue[d].pop(0) if queue[d] else None
            if n and taken[d] < engine.max_trades_per_day:
                nxt[d] = n
        live = nxt
    done.sort(key=lambda x: x['entry_time'])
    return done, info


def with_spot_level(trades: List[Dict[str, Any]], symbol: str = 'NIFTY') -> List[Dict[str, Any]]:
    """Give each trade the SPOT price at its entry minute as `level`.

    The signal and the fills are on the future, but the option strike is walked out
    from the index (its ATM), and the future sits a basis above it — starting there
    walked to a different strike than Quantman bought on a third of the trades. Read
    off the stored spot minutes; a trade with none keeps the future's price."""
    from trading_app.Backtest import minute_candle_store as mcs
    try:
        entry = mcs._load_disk(mcs._cache_key('fyers', symbol))
        spot = entry['df']['close'] if entry else None
    except Exception:                       # noqa: BLE001 — a refinement, not a requirement
        spot = None
    if spot is None:
        return trades
    out = []
    for t in trades:
        ts = pd.Timestamp(t['entry_time']) - pd.Timedelta(minutes=1)      # the minute the order fills at
        try:
            lvl = float(spot.asof(ts))
        except Exception:                   # noqa: BLE001
            lvl = float('nan')
        out.append(dict(t, level=round(lvl, 2)) if np.isfinite(lvl) else t)
    return out
