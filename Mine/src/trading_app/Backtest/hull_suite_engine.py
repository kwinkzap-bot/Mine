"""
Hull Suite System (Animesh Kumar, PhonePe Invest video series) — three Hull moving averages on
a NIFTY intraday chart, traded on the break of a candle's high / low, on the INDEX and
(optionally) re-priced on the option.

Source: youtube.com/watch?v=xVF85pCj1YQ (transcript read 2026-11-15). The three lines, as the
video sets them on a 5-minute chart:

  slow    HMA(200)   red    the TREND: above it the odds are up, below it down
  medium  HMA(100)   blue   the PULLBACK: "trade only when price is coming from near the
                            medium line"; it also shows a trend exhausting while still
                            above the red one
  fast    HMA(50)    black  the TRIGGER: which candle to trade

  HMA(n) = WMA(2 · WMA(close, n/2) − WMA(close, n), ⌊√n⌋)      (TradingView ta.hma)

What the video says, and how it is read here (anything the video leaves open is a box):

* "If the higher average says down and the lower says up, the market is flat — no trade."
  A line's DIRECTION is its slope over `slope_bars` candles (2, the TradingView Hull Suite's
  `hull > hull[2]`). A long needs the medium and fast lines rising and the close above the slow
  line; with `slow_slope` (default on) the slow line must be rising too. Shorts mirror.
* "Its high was breached, and it had crossed all moving averages." The SIGNAL candle is the
  first candle to CLOSE above all three lines (the candle before it did not), with the lines
  agreeing as above.
* "At least coming from near the medium moving average" (`pullback_bars`, `pullback_pct`):
  within the last `pullback_bars` candles (signal included) a low came within `pullback_pct` %
  of the medium line (a high, for a short). 0 bars = off.
* "We will always plan our trade on the high or low of the candle breaking." The order is a
  stop at the signal candle's high (low for a short); it is live for the next `max_wait`
  candles. If price trades through the stop level first, the setup is `invalidated`; if the
  window passes, it has `expired`. A gap through the level fills at that bar's open.
* "Set the previous candle's low as the stop loss … a 1 : 1.5 trade." Stop = the signal
  candle's low (`stop_mode='signal'`) or the lower of it and the candle before it
  (`'two_bar'`), minus `stop_buffer` points; target = `rr` × risk (0 = none, ride to the
  square-off). `max_risk` > 0 skips a setup whose risk is wider than that many points.
  `breakeven_rr` > 0 moves the stop to cost once the trade is that many R in profit (the
  video's "cost-to-cost exit is possible").
* Square-off at `exit_cutoff` (15:15). One trade at a time, `max_trades_per_day`.

Fills are walked on 1-MINUTE bars when the caller passes them (`minutes`), so the order of the
break, the stop and the target inside a 5-minute candle is the market's, not a guess; the
lines and the signal are always the chart timeframe's closed candles. Without minutes the
chart's own bars are walked and a bar that reaches both the stop and the target (or the entry
and the stop) is booked the worse way. All averages run continuously across days, as on a
chart — HMA(200) on 5 minutes needs ~3 sessions of run-in.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import _parse_hhmm, _r, summarise

STOP_MODES = ('signal', 'two_bar')


def wma(values: np.ndarray, period: int) -> np.ndarray:
    """TradingView ta.wma: linear weights, the newest candle weighted `period`."""
    x = np.asarray(values, dtype=float)
    out = np.full(len(x), np.nan)
    n = max(1, int(period))
    finite = np.flatnonzero(np.isfinite(x))
    if not len(finite):
        return out
    f = int(finite[0])
    y = x[f:]
    if len(y) < n:
        return out
    w = np.arange(1, n + 1, dtype=float)
    out[f + n - 1:] = np.convolve(y, w[::-1], mode='valid') / w.sum()
    return out


def hma(values: np.ndarray, period: int) -> np.ndarray:
    """TradingView ta.hma: WMA(2·WMA(x, n/2) − WMA(x, n), ⌊√n⌋)."""
    n = max(2, int(period))
    raw = 2.0 * wma(values, max(1, n // 2)) - wma(values, n)
    return wma(raw, max(1, int(np.floor(np.sqrt(n)))))


class HullSuiteEngine:
    def __init__(self, df: pd.DataFrame, minutes: Optional[pd.DataFrame] = None,
                 slow_len: int = 200, medium_len: int = 100, fast_len: int = 50,
                 slope_bars: int = 2, slow_slope: bool = True,
                 pullback_bars: int = 12, pullback_pct: float = 0.1,
                 max_wait: int = 3, stop_mode: str = 'signal', stop_buffer: float = 0.0,
                 max_risk: float = 0.0, rr: float = 1.5, breakeven_rr: float = 0.0,
                 direction: str = 'both', entry_start: str = '09:20', entry_end: str = '14:30',
                 exit_cutoff: str = '15:15', max_trades_per_day: int = 2,
                 bar_minutes: Optional[int] = None):
        self.df = self._frame(df)
        self.minutes = self._frame(minutes) if minutes is not None and len(minutes) else None
        self.slow_len = max(2, int(slow_len))
        self.medium_len = max(2, int(medium_len))
        self.fast_len = max(2, int(fast_len))
        self.slope_bars = max(1, int(slope_bars))
        self.slow_slope = bool(slow_slope)
        self.pullback_bars = max(0, int(pullback_bars or 0))
        self.pullback_pct = max(0.0, float(pullback_pct or 0))
        self.max_wait = max(1, int(max_wait or 1))
        self.stop_mode = stop_mode if stop_mode in STOP_MODES else 'signal'
        self.stop_buffer = max(0.0, float(stop_buffer or 0))
        self.max_risk = max(0.0, float(max_risk or 0))
        self.rr = max(0.0, float(rr or 0))
        self.breakeven_rr = max(0.0, float(breakeven_rr or 0))
        self.direction = direction if direction in ('long', 'short', 'both') else 'both'
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 20)
        self.entry_end = _parse_hhmm(entry_end, 14 * 60 + 30)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 15)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        if bar_minutes:
            self.bar_minutes = int(bar_minutes)
        elif len(self.df) > 1:
            step = self.df.index.to_series().diff().dropna()
            self.bar_minutes = max(1, int(round(step.median().total_seconds() / 60)))
        else:
            self.bar_minutes = 5

    @staticmethod
    def _frame(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df.index):
            col = 'date' if 'date' in df.columns else 'datetime'
            df = df.set_index(col)
            df.index = pd.to_datetime(df.index)
        if getattr(df.index, 'tz', None) is not None:
            df.index = df.index.tz_localize(None)
        df.columns = [c.lower() for c in df.columns]
        df = df.sort_index()
        return df[~df.index.duplicated(keep='last')]

    # ── Lines and signals ──────────────────────────────────────────────────────

    def indicators(self) -> Dict[str, np.ndarray]:
        c = self.df['close'].to_numpy(dtype=float)
        return {'slow': hma(c, self.slow_len), 'medium': hma(c, self.medium_len),
                'fast': hma(c, self.fast_len)}

    def signals(self) -> Dict[str, Any]:
        """+1 / −1 on a closed chart candle that is a long / short signal candle."""
        ind = self.indicators()
        s, m, f = ind['slow'], ind['medium'], ind['fast']
        h = self.df['high'].to_numpy(dtype=float)
        l = self.df['low'].to_numpy(dtype=float)
        c = self.df['close'].to_numpy(dtype=float)
        idx = self.df.index
        tmin = (idx.hour * 60 + idx.minute).to_numpy()
        n = len(c)
        k = self.slope_bars

        def lag(a, by):
            out = np.full(n, np.nan)
            if by < n:
                out[by:] = a[:-by]
            return out

        with np.errstate(invalid='ignore'):
            ok = np.isfinite(s) & np.isfinite(m) & np.isfinite(f)
            s_up, s_dn = s > lag(s, k), s < lag(s, k)
            m_up, m_dn = m > lag(m, k), m < lag(m, k)
            f_up, f_dn = f > lag(f, k), f < lag(f, k)
            above = ok & (c > s) & (c > m) & (c > f)
            below = ok & (c < s) & (c < m) & (c < f)
            bull = above & m_up & f_up & (s_up if self.slow_slope else True)
            bear = below & m_dn & f_dn & (s_dn if self.slow_slope else True)
            fresh_up = bull & ~np.concatenate(([True], above[:-1]))
            fresh_dn = bear & ~np.concatenate(([True], below[:-1]))
            if self.pullback_bars > 0:
                tol = self.pullback_pct / 100.0
                near_lo = (l <= m * (1 + tol)).astype(float)
                near_hi = (h >= m * (1 - tol)).astype(float)
                w = self.pullback_bars
                came_lo = pd.Series(near_lo).rolling(w, min_periods=1).max().to_numpy() > 0
                came_hi = pd.Series(near_hi).rolling(w, min_periods=1).max().to_numpy() > 0
                fresh_up &= came_lo
                fresh_dn &= came_hi
        in_window = (tmin >= self.entry_start) & (tmin < self.entry_end)
        sig = np.zeros(n, dtype=np.int64)
        if self.direction in ('long', 'both'):
            sig[fresh_up & in_window] = 1
        if self.direction in ('short', 'both'):
            sig[fresh_dn & in_window] = -1
        return {'signal': sig, **ind}

    # ── Trades ─────────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        trades = self.simulate()
        return trades, summarise(trades)

    def simulate(self) -> List[Dict[str, Any]]:
        df = self.df
        if len(df) < self.fast_len + 2:
            return []
        self.setups = {'signals': 0, 'filled': 0, 'expired': 0, 'invalidated': 0,
                       'too_wide': 0, 'busy': 0}
        idx = df.index
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        sg = self.signals()

        fine = self.minutes if self.minutes is not None else df
        fstep = 1 if self.minutes is not None else self.bar_minutes
        fi = fine.index
        fo = fine['open'].to_numpy(dtype=float)
        fh = fine['high'].to_numpy(dtype=float)
        fl = fine['low'].to_numpy(dtype=float)
        fc = fine['close'].to_numpy(dtype=float)
        ftmin = (fi.hour * 60 + fi.minute).to_numpy()
        fday = (fi.year * 10000 + fi.month * 100 + fi.day).to_numpy()
        fts = fi.strftime('%Y-%m-%d %H:%M:%S').to_numpy()
        ends = np.concatenate((np.flatnonzero(np.diff(fday)), [len(fine) - 1]))
        bar = pd.Timedelta(minutes=self.bar_minutes)
        cutoff = self.exit_cutoff
        intrabar = self.minutes is not None

        trades: List[Dict[str, Any]] = []
        per_day: Dict[str, int] = {}
        busy_until = pd.Timestamp.min
        for i in np.flatnonzero(sg['signal']):
            self.setups['signals'] += 1
            is_long = sg['signal'][i] == 1
            sign = 1.0 if is_long else -1.0
            sig_close = idx[i] + bar
            day = str(idx[i].date())
            if sig_close <= busy_until:
                self.setups['busy'] += 1
                continue
            if per_day.get(day, 0) >= self.max_trades_per_day:
                continue
            level = h[i] if is_long else l[i]
            if self.stop_mode == 'two_bar' and i > 0 and idx[i - 1].date() == idx[i].date():
                base = min(l[i], l[i - 1]) if is_long else max(h[i], h[i - 1])
            else:
                base = l[i] if is_long else h[i]
            stop = base - sign * self.stop_buffer
            risk = sign * (level - stop)
            if risk <= 0:
                continue
            if self.max_risk > 0 and risk > self.max_risk:
                self.setups['too_wide'] += 1
                continue

            k0 = int(fi.searchsorted(sig_close))
            k1 = int(fi.searchsorted(sig_close + bar * self.max_wait))
            if k0 >= len(fine):
                continue
            day_end = int(ends[np.searchsorted(ends, k0)])
            if fday[k0] != int(idx[i].strftime('%Y%m%d')):
                self.setups['expired'] += 1           # the window falls in the next session
                continue
            k1 = min(k1, day_end + 1)
            e = None
            outcome = 'expired'
            for k in range(k0, k1):
                if ftmin[k] >= cutoff:
                    break
                hit_entry = (fh[k] >= level) if is_long else (fl[k] <= level)
                hit_stop = (fl[k] <= stop) if is_long else (fh[k] >= stop)
                if hit_entry:
                    e = k
                    break
                if hit_stop:
                    outcome = 'invalidated'
                    break
            if e is None:
                self.setups[outcome] += 1
                continue
            self.setups['filled'] += 1
            entry = max(fo[e], level) if is_long else min(fo[e], level)
            # A gap past the level: the stop order fills at the open, and the stop/target
            # stay where the signal candle put them (the risk taken is the larger one).
            target = entry + sign * self.rr * risk if self.rr > 0 else None
            be_at = level + sign * self.breakeven_rr * risk if self.breakeven_rr > 0 else None
            cur_stop = stop
            exit_k, exit_px, reason, intra_x = day_end, fc[day_end], 'EOD Exit', False
            for k in range(e, day_end + 1):
                if k > e and ftmin[k] >= cutoff:
                    exit_k, exit_px, reason, intra_x = k, fo[k], 'Time Exit', False
                    break
                hit_stop = (fl[k] <= cur_stop) if is_long else (fh[k] >= cur_stop)
                if hit_stop:                          # the stop first: the worse case inside a bar
                    if k > e:
                        px = min(fo[k], cur_stop) if is_long else max(fo[k], cur_stop)
                    else:
                        px = cur_stop
                    exit_k, exit_px, intra_x = k, px, True
                    reason = 'Breakeven' if cur_stop != stop else 'Stop Loss'
                    break
                if target is not None and ((fh[k] >= target) if is_long else (fl[k] <= target)):
                    px = (max(fo[k], target) if is_long else min(fo[k], target)) if k > e else target
                    exit_k, exit_px, reason, intra_x = k, px, 'Target', True
                    break
                if be_at is not None and ((fh[k] >= be_at) if is_long else (fl[k] <= be_at)):
                    cur_stop = entry                  # from the next bar: cost to cost
            pnl = sign * (exit_px - entry)
            if reason == 'Time Exit':
                exit_time = fts[exit_k][:11] + f"{cutoff // 60:02d}:{cutoff % 60:02d}:00"
            elif reason == 'EOD Exit':
                exit_time = str(fi[exit_k] + pd.Timedelta(minutes=fstep))
            else:
                exit_time = fts[exit_k]
            per_day[day] = per_day.get(day, 0) + 1
            busy_until = fi[exit_k]
            trades.append({
                'entry_time': fts[e], 'exit_time': exit_time,
                'intrabar_entry': intrabar, 'intrabar_exit': intrabar and intra_x,
                'type': 'Long' if is_long else 'Short',
                'pattern': 'Hull break up' if is_long else 'Hull break down',
                'level': round(float(level), 2),
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': round(float(stop), 2),
                'target_price': round(float(target), 2) if target is not None else None,
                'risk_points': round(float(risk), 2),
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': {'time': str(idx[i]), 'slow': _r(sg['slow'][i]), 'medium': _r(sg['medium'][i]),
                           'fast': _r(sg['fast'][i]), 'high': _r(h[i]), 'low': _r(l[i])},
            })
        return trades
