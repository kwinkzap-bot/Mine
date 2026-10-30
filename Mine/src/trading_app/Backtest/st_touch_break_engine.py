"""
SuperTrend touch-and-break backtest ("ST Touch Break").

Two entries read off the user's NIFTY 3-minute screenshots of 27 Oct 2026, SuperTrend
(ATR 10, factor 2). Both are the same shape — a TOUCH candle, then a BREAK of its range:

  Touch candle  A closed candle whose wick reaches the SuperTrend line while its close
                stays on the trend's own side (so the trend has not flipped on it):
                  uptrend   (line under price)  low  <= line  -> a long lower wick on the line
                  downtrend (line over price)   high >= line  -> a long upper wick on the line
  Break         The NEXT candle trades beyond the touch candle's high (long) or low (short).
                The order fills at the break level, or at the open if the candle gaps
                through it. A candle that breaks BOTH sides (an outside bar — 14:33 does)
                takes the side it CLOSES on (`outside_bar='close'`, the chart reading: a
                strong green candle is the long), or the side that broke first by the 1-minute
                path (`outside_bar='first'`, no look-ahead; 14:33 then shorts at 14:33 and the
                long at 14:35 is lost, as the short is open).

  Bounce   (27 Oct 12:21, uptrend)   the wick tests the line from above, the next candle
           breaks the touch candle's HIGH -> long, with the trend.
  Through  (27 Oct 14:33, downtrend) the wick tests the line from below, the next candle is
           strong the other way and breaks the touch candle's HIGH -> long; it closes over
           the line, so the SuperTrend flips on it. Mirror for shorts.

  Stop     The SuperTrend breaking the OTHER way: a candle that closes beyond the line, i.e.
           the direction flips against the trade, exits at that close. `stop_mode='touch'`
           exits intrabar instead, at the previous candle's line. A trade whose entry candle
           does not close on the right side of the line is out at that same close.
  Exit     Only the stop, or the `exit_cutoff` square-off (default 15:14). There is no
           target: the trade rides the SuperTrend.

`min_wick_pct` (share of the touch candle's range that is wick on the line's side) and
`min_body_pct` (body share of the breaking candle's range, closing the way it broke) are the
"long wick" / "strong candle" of the description. Both default to OFF — the screenshots do not
fix a number.

Filled at the close (since 2026-10-30). `min_body_pct` above 0 and `outside_bar='close'` both
read the break candle's CLOSE, so with either on the trade is entered AT that close (stamped the
next bar's first minute, priced off the close), and dropped if the SuperTrend flipped against it
on that candle — exactly what the live Mine tabs do. Until then it filled at the break level with
the close already known: on 2020-26 NIFTY that look-ahead was roughly half to two-thirds of every
Mine row's index points (tab 10: +15,626 vs +5,591), and the live tabs could never earn it. With
both off (`min_body_pct` 0, `outside_bar='first'`) the fill is still at the level, intrabar.

P&L is in INDEX points here; the page re-prices each trade on a CE (long) / PE (short) with
`attach_option_legs`, exactly as the Simple Trader runs do, and the option's own stop-loss /
trailing stop can end a trade first — `trade_after` then finds the next setup (re-entry). Fills
carry the MINUTE inside the bar the level was crossed (read off `minutes`, the 1-minute candles
the bars were built from), so the option is priced when the trade happened, not at the bar's
first minute. One position at a time; indicators run continuously across days, as on
a chart. This is a backtest only: it has no order path and nothing live reads it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import _parse_hhmm, supertrend


class StTouchBreakEngine:
    def __init__(self, df: pd.DataFrame, st_period: int = 10, st_mult: float = 2.0,
                 touch_tol_pct: float = 0.0, min_wick_pct: float = 0.0, min_body_pct: float = 0.0,
                 modes: str = 'both', direction: str = 'both', stop_mode: str = 'close',
                 entry_start: str = '09:15', entry_end: str = '15:14',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 5,
                 minutes: Optional[pd.DataFrame] = None, bar_minutes: int = 3,
                 outside_bar: str = 'close'):
        self.df = df.copy()
        self.bar_minutes = max(1, int(bar_minutes or 1))
        self.outside_bar = outside_bar if outside_bar in ('close', 'first') else 'close'
        self._minutes = minutes
        self._ind = None
        self._m_ts = self._m_hi = self._m_lo = None
        if minutes is not None and len(minutes):
            self._m_ts = minutes.index.to_numpy(dtype='datetime64[ns]')
            self._m_hi = minutes['high'].to_numpy(dtype=float)
            self._m_lo = minutes['low'].to_numpy(dtype=float)
        self.st_period, self.st_mult = max(2, int(st_period)), float(st_mult)
        self.touch_tol = max(0.0, float(touch_tol_pct or 0)) / 100.0
        self.min_wick = min(1.0, max(0.0, float(min_wick_pct or 0) / 100.0))
        self.min_body = min(1.0, max(0.0, float(min_body_pct or 0) / 100.0))
        self.modes = modes if modes in ('both', 'bounce', 'through') else 'both'
        self.direction = direction if direction in ('both', 'long', 'short') else 'both'
        self.stop_mode = stop_mode if stop_mode in ('close', 'touch') else 'close'
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 15)
        self.entry_end = _parse_hhmm(entry_end, 15 * 60 + 14)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 14)
        self.max_trades_per_day = int(max_trades_per_day) if max_trades_per_day and int(max_trades_per_day) > 0 else 999
        self._prepare()

    def _prepare(self):
        d = self.df
        if 'date' in d.columns:
            d = d.set_index('date')
        d.index = pd.to_datetime(d.index)
        d.columns = [c.lower() for c in d.columns]
        d = d.sort_index()
        self.df = d[~d.index.duplicated(keep='last')]
        self._cols = None

    def _columns(self) -> Dict[str, np.ndarray]:
        """The bars as arrays, plus each bar's session (YYYYMMDD) and minute of day. Built once
        per frame: a sweep calls simulate() thousands of times on the same bars, and building the
        per-bar day/minute lists was ~85 % of each call."""
        df = self.df
        if self._cols is None or self._cols[0] is not df:
            idx = df.index
            cols = {k: df[k].to_numpy(dtype=float) for k in ('open', 'high', 'low', 'close')}
            cols['day'] = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy(dtype=np.int64)
            cols['tmin'] = (idx.hour * 60 + idx.minute).to_numpy(dtype=np.int64)
            self._cols = (df, cols)
        return self._cols[1]

    # ── Signals ────────────────────────────────────────────────────────────────

    def indicators(self) -> Dict[str, np.ndarray]:
        key = (self.st_period, self.st_mult)
        if self._ind is None or self._ind[0] != key:        # a sweep walks the same lines many times
            h, l, c = (self.df[k].to_numpy(dtype=float) for k in ('high', 'low', 'close'))
            line, trend = supertrend(h, l, c, self.st_period, self.st_mult)
            self._ind = (key, {'line': line, 'trend': trend})
        return self._ind[1]

    def touches(self, ind: Optional[Dict[str, np.ndarray]] = None) -> np.ndarray:
        """Per bar: +1 touch of an UPtrend line (low on it), -1 of a DOWNtrend line, else 0."""
        ind = ind or self.indicators()
        line, trend = ind['line'], ind['trend']
        cols = self._columns()
        o, h, l, c = (cols[k] for k in ('open', 'high', 'low', 'close'))
        rng = h - l
        prev = np.concatenate(([0], trend[:-1]))
        live = (trend != 0) & (prev == trend) & np.isfinite(line)     # a line, and no flip on this candle
        safe = np.where(rng > 0, rng, np.nan)
        with np.errstate(invalid='ignore'):
            up = live & (trend == 1) & (l <= line * (1 + self.touch_tol)) & ((np.minimum(o, c) - l) / safe >= self.min_wick)
            dn = live & (trend == -1) & (h >= line * (1 - self.touch_tol)) & ((h - np.maximum(o, c)) / safe >= self.min_wick)
        return np.where(up, 1, np.where(dn, -1, 0)).astype(np.int64)

    # ── Trades ─────────────────────────────────────────────────────────────────

    @property
    def confirm_at_close(self) -> bool:
        """Does the entry read the break candle's close (a body filter, or an outside bar going the
        way it closes)? Then the trade is entered AT that close, as the live tab does — not at the
        level, which was crossed before the close was known."""
        return self.min_body > 0 or self.outside_bar == 'close'

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        trades = self.simulate()
        return trades, summarise_points(trades)

    def _cross_minute(self, bar_ts: pd.Timestamp, level: float, is_long: bool) -> pd.Timestamp:
        """The minute inside the bar at which price first traded through `level`; the bar's own
        stamp when there are no minutes (or none crosses)."""
        if self._m_ts is None:
            return bar_ts
        lo = np.searchsorted(self._m_ts, np.datetime64(bar_ts, 'ns'), side='left')
        hi = np.searchsorted(self._m_ts, np.datetime64(bar_ts + pd.Timedelta(minutes=self.bar_minutes), 'ns'), side='left')
        if hi <= lo:
            return bar_ts
        seg = (self._m_hi[lo:hi] > level) if is_long else (self._m_lo[lo:hi] < level)
        hit = np.flatnonzero(seg)
        return pd.Timestamp(self._m_ts[lo + hit[0]]) if len(hit) else bar_ts

    def trade_after(self, day: str, after_ts: str, taken: int) -> Optional[Dict[str, Any]]:
        """The next trade of `day` entered STRICTLY after `after_ts` ('YYYY-MM-DD HH:MM:SS'),
        for when the option's own stop ended the previous one before the SuperTrend did."""
        if taken >= self.max_trades_per_day:
            return None
        out = self.simulate(day=day, after_ts=after_ts, limit=1)
        return out[0] if out else None

    def simulate(self, day: Optional[str] = None, after_ts: Optional[str] = None,
                 limit: Optional[int] = None) -> List[Dict[str, Any]]:
        df = self.df
        n = len(df)
        if n < self.st_period + 3:
            return []
        idx = df.index
        cols = self._columns()
        o, h, l, c = (cols[k] for k in ('open', 'high', 'low', 'close'))
        ind = self.indicators()
        line, trend = ind['line'], ind['trend']
        touch = self.touches(ind)
        day_arr, tmin = cols['day'], cols['tmin']
        want_day = int(day.replace('-', '')) if day else None
        stamp = lambda k: idx[k].strftime('%Y-%m-%d %H:%M:00')   # noqa: E731
        bar = pd.Timedelta(minutes=self.bar_minutes)
        trades: List[Dict[str, Any]] = []
        free_from = 0
        taken: Dict[Any, int] = {}
        confirm = self.confirm_at_close
        cands = np.flatnonzero(touch)
        if want_day is not None:                           # trade_after: only that session's touches
            cands = cands[day_arr[cands] == want_day]
        for t in cands:
            e = int(t) + 1
            if e >= n or e < free_from or day_arr[e] != day_arr[t]:
                continue
            if want_day is not None and day_arr[e] != want_day:
                continue
            if limit and len(trades) >= limit:
                break
            if tmin[e] < self.entry_start or tmin[e] > self.entry_end or tmin[e] >= self.exit_cutoff:
                continue
            if not day and taken.get(day_arr[e], 0) >= self.max_trades_per_day:
                continue
            up_break, dn_break = h[e] > h[t], l[e] < l[t]
            if not (up_break or dn_break):
                continue
            if up_break and dn_break:                      # an outside bar: see the module note
                if self.outside_bar == 'close' and c[e] != o[e]:
                    up_break = c[e] > o[e]
                else:
                    t_up = self._cross_minute(idx[e], h[t], True)
                    t_dn = self._cross_minute(idx[e], l[t], False)
                    up_break = (t_up < t_dn) if t_up != t_dn else abs(h[t] - o[e]) <= abs(o[e] - l[t])
                dn_break = not up_break
            is_long = up_break
            bounce = (touch[t] == 1) == is_long            # break AWAY from the line vs through it
            if (self.direction == 'long' and not is_long) or (self.direction == 'short' and is_long):
                continue
            if (self.modes == 'bounce' and not bounce) or (self.modes == 'through' and bounce):
                continue
            rng = h[e] - l[e]
            body = abs(c[e] - o[e])
            if self.min_body and (rng <= 0 or body / rng < self.min_body
                                  or (c[e] > o[e]) != is_long):
                continue
            level = h[t] if is_long else l[t]
            side = 1 if is_long else -1
            if confirm:                                    # judged on the close, so filled at it
                if trend[e] == -side:                      # the line flipped against it on that close
                    continue
                entry = c[e]
                entry_ts = idx[e] + bar                    # known at the close: the next bar's first minute
            else:
                entry = max(o[e], level) if is_long else min(o[e], level)
                entry_ts = idx[e] if entry == o[e] and o[e] != level else self._cross_minute(idx[e], level, is_long)
            if after_ts and entry_ts.strftime('%Y-%m-%d %H:%M:%S') <= after_ts:
                continue
            exit_ts = None
            exit_k, exit_px, reason = n - 1, c[n - 1], 'Still Open'
            for k in range(e, n):
                if day_arr[k] != day_arr[e] or tmin[k] >= self.exit_cutoff:
                    exit_k, exit_px, reason = k - 1, c[k - 1], 'Square-off'
                    exit_ts = idx[k - 1] + bar
                    break
                prev = line[k - 1]
                if (self.stop_mode == 'touch' and k > e and trend[k - 1] == side and np.isfinite(prev)
                        and ((l[k] <= prev) if is_long else (h[k] >= prev))):
                    exit_k, exit_px, reason = k, (min(o[k], prev) if is_long else max(o[k], prev)), 'SuperTrend stop'
                    gapped = (o[k] <= prev) if is_long else (o[k] >= prev)
                    exit_ts = idx[k] if gapped else self._cross_minute(idx[k], prev, not is_long)
                    break
                if trend[k] == -side:                      # the SuperTrend broke the other way
                    exit_k, exit_px, reason = k, c[k], 'SuperTrend flip'
                    exit_ts = idx[k] + bar                 # known at the close: the next bar's first minute
                    break
            else:
                exit_k, exit_px, reason = n - 1, c[n - 1], 'Still Open'
            if exit_k < e:                                 # square-off before the entry bar: never filled
                continue
            if exit_ts is None:                            # the data ended with the trade open
                exit_ts = idx[exit_k] + bar
            if exit_ts <= entry_ts:
                exit_ts = entry_ts + pd.Timedelta(minutes=1)
            pnl = (exit_px - entry) if is_long else (entry - exit_px)
            risk = abs(entry - line[t])
            free_from = exit_k + 1
            taken[day_arr[e]] = taken.get(day_arr[e], 0) + 1
            trades.append({
                'entry_time': entry_ts.strftime('%Y-%m-%d %H:%M:%S'),
                'exit_time': exit_ts.strftime('%Y-%m-%d %H:%M:%S'), 'signal_time': stamp(int(t)),
                'type': 'Long' if is_long else 'Short',
                'setup': 'Bounce' if bounce else 'Through',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': round(float(line[t]), 2),
                'touch_high': round(float(h[t]), 2), 'touch_low': round(float(l[t]), 2),
                'pnl': round(float(pnl), 2), 'risk': round(float(risk), 2),
                'r_multiple': round(float(pnl / risk), 2) if risk > 0 else 0.0,
                'bars_held': int(exit_k - e + 1),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                # A level / line fill happens INSIDE its minute (the index trades through the break
                # level / the SuperTrend line), so the option leg is priced mid-minute, not at the close
                # of the minute before — see attach_option_legs. A close entry is that minute-before close.
                'intrabar_entry': not confirm, 'intrabar_exit': reason == 'SuperTrend stop',
            })
        return trades


def summarise_points(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Win rate, profit factor and drawdown in index points."""
    if not trades:
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0, 'total_pnl': 0.0,
                'avg_win': 0.0, 'avg_loss': 0.0, 'profit_factor': 0.0, 'max_drawdown': 0.0}
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] < 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses))
    running = peak = dd = 0.0
    for t in trades:
        running += t['pnl']
        peak = max(peak, running)
        dd = max(dd, peak - running)
    return {
        'total_trades': len(trades), 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'total_pnl': round(sum(t['pnl'] for t in trades), 2),
        'avg_win': round(gp / len(wins), 2) if wins else 0.0,
        'avg_loss': round(gl / len(losses), 2) if losses else 0.0,
        'profit_factor': round(gp / gl, 2) if gl > 0 else (999.0 if gp > 0 else 0.0),
        'max_drawdown': round(dd, 2),
    }
