"""
MA Cross + HTF (Pawan Sharma, Upsurge Club) — the 11/21 smoothed-MA crossover, taken only
in the direction of a higher-timeframe moving average, traded on the INDEX and (optionally)
re-priced on the option.

The three lines:

  fast   SMMA(11) of the close on the chart's timeframe          (green)
  slow   SMMA(21) of the close                                   (red)
  htf    MA(21) of a HIGHER timeframe's close, drawn on the chart (black)
         (intraday: 3-min chart / 15-min HTF; swing: 15-min chart / 1-hour HTF)

Entry. The fast line crosses the slow one — that candle is the crossover. The trade is only
taken in the HTF's direction (close above `htf` for a long, below for a short), and only once
the move is CONFIRMED: from `confirm_bars` candles after the crossover (default 1 = the 2nd
candle) up to `max_wait` (default 5), the first candle to CLOSE with the fast line still on
the right side of the slow one and price beyond all three lines is the signal; the order fills
at the NEXT candle's open. A crossover whose window passes without that is skipped — "the move
has already run". A crossover on a candle before `entry_start` (09:30) is ignored, however
good it looks: the open is the most volatile stretch.

Exit. First of: the fixed stop `sl_points` (intrabar, filled at the stop, at the open when the
bar gaps through it); the opposite crossover (`exit_on_cross`, exit at the next open); the
square-off (`exit_cutoff`) unless `positional`; `trail_points` > 0 adds a trailing stop that sits that far behind the
best price once the trade is `trail_start` points in profit. No target: the speaker lets the move run. With
`partial_pct` > 0, that share is booked at `partial_points` in profit and the stop on the rest
moves to cost ("book half, let the other ride").

Trend-day filters (all off by default; each only SKIPS entries): `adx_min` (Wilder ADX of the chart at least this),
`chop_lookback`/`chop_max` (the fast/slow lines crossed no more than `chop_max` times in the last `chop_lookback`
candles: a sideways day crosses all the time) and `day_bias` (price is on the trade's side of the day's open).
Measured on NIFTY 3-min (fit 2024-01..2025-06, check 2025-07..2026-10) no trailing stop helped, and only ADX >= 20
improved the profit factor in both halves; they are here to be tried, not because they are known to work.

Partial booking is an INDEX-points feature: the option re-pricing (`attach_option_legs`) prices
one entry and one exit per trade, so the API refuses to combine the two.

The HTF value at a candle is what a chart shows at that candle's close: `htf_calc='live'` (the
default) puts the in-progress HTF candle's close into the average, `'closed'` uses the last
COMPLETED HTF candle only. Neither looks ahead. All averages run continuously across days.

Indicator conventions are TradingView's: SMMA = Wilder's RMA seeded with the SMA, EMA seeded
with the SMA.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.hilega_milega_engine import ema
from trading_app.Backtest.st_bb_rsi_engine import _parse_hhmm, _r, summarise

MA_TYPES = ('sma', 'ema', 'smma')
HTF_CALCS = ('live', 'closed')


def smma(values: np.ndarray, period: int) -> np.ndarray:
    """Pine ta.rma / TradingView SMMA: seeded with the SMA of the first `period` values."""
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(np.isfinite(values))
    if not len(valid) or len(values) - valid[0] < period:
        return out
    f = int(valid[0])
    out[f + period - 1] = values[f:f + period].mean()
    for i in range(f + period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's ADX (TradingView ta.dmi): the strength of a trend, whichever way it runs."""
    n = len(close)
    out = np.full(n, np.nan)
    if n < 2 * period + 1:
        return out
    up = np.concatenate(([0.0], high[1:] - high[:-1]))
    dn = np.concatenate(([0.0], low[:-1] - low[1:]))
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = high[0] - low[0]
    str_, sp, sm = smma(tr, period), smma(plus, period), smma(minus, period)
    with np.errstate(divide='ignore', invalid='ignore'):
        pdi, mdi = 100 * sp / str_, 100 * sm / str_
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return smma(dx, period)


def _ma(values: np.ndarray, period: int, kind: str) -> np.ndarray:
    if kind == 'ema':
        return ema(values, period)
    if kind == 'smma':
        return smma(values, period)
    return pd.Series(values).rolling(period).mean().to_numpy()


class MaHtfEngine:
    def __init__(self, df: pd.DataFrame, fast_len: int = 11, slow_len: int = 21, htf_len: int = 21,
                 htf_minutes: int = 15, htf_type: str = 'sma', htf_calc: str = 'live',
                 confirm_bars: int = 1, max_wait: int = 5, sl_points: float = 35.0,
                 partial_pct: float = 0.0, partial_points: float = 0.0, exit_on_cross: bool = True,
                 direction: str = 'both', entry_start: str = '09:30', entry_end: str = '14:30',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 2,
                 bar_minutes: Optional[int] = None, positional: bool = False,
                 trail_points: float = 0.0, trail_start: float = 0.0, adx_min: float = 0.0,
                 adx_len: int = 14, chop_lookback: int = 0, chop_max: int = 2, day_bias: bool = False):
        self.df = df.copy()
        self.fast_len = max(1, int(fast_len))
        self.slow_len = max(2, int(slow_len))
        self.htf_len = max(2, int(htf_len))
        self.htf_minutes = max(1, int(htf_minutes))
        self.htf_type = htf_type if htf_type in MA_TYPES else 'sma'
        self.htf_calc = htf_calc if htf_calc in HTF_CALCS else 'live'
        self.confirm_bars = max(0, int(confirm_bars))
        self.max_wait = max(self.confirm_bars, int(max_wait))
        self.sl_points = max(0.0, float(sl_points or 0))
        self.partial_pct = min(1.0, max(0.0, float(partial_pct or 0)))
        self.partial_points = max(0.0, float(partial_points or 0))
        if self.partial_points <= 0:                    # "1:1" unless told otherwise
            self.partial_points = self.sl_points
        if self.partial_points <= 0:
            self.partial_pct = 0.0
        self.exit_on_cross = bool(exit_on_cross)
        self.direction = direction if direction in ('long', 'short', 'both') else 'both'
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 30)
        self.entry_end = _parse_hhmm(entry_end, 14 * 60 + 30)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 14)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self.positional = bool(positional)
        self.trail_points = max(0.0, float(trail_points or 0))
        self.trail_start = max(0.0, float(trail_start or 0)) or self.trail_points
        self.adx_min = max(0.0, float(adx_min or 0))
        self.adx_len = max(2, int(adx_len or 14))
        self.chop_lookback = max(0, int(chop_lookback or 0))
        self.chop_max = max(0, int(chop_max))
        self.day_bias = bool(day_bias)
        self._prepare()
        if bar_minutes:
            self.bar_minutes = int(bar_minutes)
        elif len(self.df) > 1:
            step = self.df.index.to_series().diff().dropna()
            self.bar_minutes = max(1, int(round(step.median().total_seconds() / 60)))
        else:
            self.bar_minutes = 3
        self.htf_minutes = max(self.htf_minutes, self.bar_minutes)

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            col = 'date' if 'date' in self.df.columns else 'datetime'
            self.df = self.df.set_index(col)
            self.df.index = pd.to_datetime(self.df.index)
        self.df.columns = [c.lower() for c in self.df.columns]
        self.df = self.df.sort_index()
        self.df = self.df[~self.df.index.duplicated(keep='last')]

    # ── Lines ──────────────────────────────────────────────────────────────────

    def htf_line(self) -> np.ndarray:
        """The HTF moving average as the chart shows it at each base candle's close."""
        idx = self.df.index
        c = self.df['close'].to_numpy(dtype=float)
        anchor = pd.Timedelta(hours=9, minutes=15)
        key = ((idx - anchor).floor(f'{self.htf_minutes}min') + anchor)
        bucket = pd.factorize(key, sort=True)[0]                  # 0..B-1 in time order
        n_b = int(bucket.max()) + 1 if len(bucket) else 0
        hc = np.full(n_b, np.nan)                                  # the HTF candle's final close
        hc[bucket] = c                                             # last write per bucket wins
        n = self.htf_len
        ma = _ma(hc, n, self.htf_type)
        prev_b = bucket - 1
        has_prev = prev_b >= 0
        out = np.full(len(c), np.nan)
        if self.htf_calc == 'closed':
            out[has_prev] = ma[prev_b[has_prev]]
            return out
        if self.htf_type == 'sma':
            head = pd.Series(hc).rolling(n - 1).sum().to_numpy()   # the n-1 completed candles before this one
            out[has_prev] = (head[prev_b[has_prev]] + c[has_prev]) / n
        else:
            k = 2.0 / (n + 1) if self.htf_type == 'ema' else 1.0 / n
            out[has_prev] = c[has_prev] * k + ma[prev_b[has_prev]] * (1 - k)
        return out

    def indicators(self) -> Dict[str, np.ndarray]:
        c = self.df['close'].to_numpy(dtype=float)
        return {'fast': smma(c, self.fast_len), 'slow': smma(c, self.slow_len), 'htf': self.htf_line()}

    # ── Signals ────────────────────────────────────────────────────────────────

    def signals(self) -> Dict[str, Any]:
        ind = self.indicators()
        f, s, h = ind['fast'], ind['slow'], ind['htf']
        c = self.df['close'].to_numpy(dtype=float)
        idx = self.df.index
        tmin = (idx.hour * 60 + idx.minute).to_numpy()
        n = len(c)
        with np.errstate(invalid='ignore'):
            up = (f > s)
            prev_up = np.concatenate(([False], up[:-1]))
            ok = np.isfinite(f) & np.isfinite(s)
            prev_ok = np.concatenate(([False], ok[:-1]))
            cross_up = ok & prev_ok & up & ~prev_up
            cross_dn = ok & prev_ok & ~up & prev_up
            bull = np.isfinite(h) & (c > h) & (c > f) & (c > s) & up
            bear = np.isfinite(h) & (c < h) & (c < f) & (c < s) & ~up & ok
        in_window = (tmin >= self.entry_start) & (tmin < self.entry_end)
        # Trend-day filters (all optional): the entry needs a day that is trending, not whipsawing.
        long_ok = np.ones(n, dtype=bool)
        short_ok = np.ones(n, dtype=bool)
        if self.adx_min > 0:
            a = adx(self.df['high'].to_numpy(dtype=float), self.df['low'].to_numpy(dtype=float), c, self.adx_len)
            with np.errstate(invalid='ignore'):
                strong = a >= self.adx_min
            long_ok &= strong
            short_ok &= strong
        if self.chop_lookback > 0:                          # the lines keep crossing: a sideways day
            flips = pd.Series((cross_up | cross_dn).astype(int)).rolling(self.chop_lookback, min_periods=1).sum().to_numpy()
            quiet = flips <= self.chop_max
            long_ok &= quiet
            short_ok &= quiet
        if self.day_bias:                                   # the day itself is moving the trade's way
            day_open = self.df['open'].groupby(idx.normalize()).transform('first').to_numpy(dtype=float)
            long_ok &= c > day_open
            short_ok &= c < day_open
        bull &= long_ok
        bear &= short_ok

        sig = np.zeros(n, dtype=np.int64)
        cross_bar = np.full(n, -1, dtype=np.int64)
        j_up = j_dn = -1                                   # the live crossover of each side (-1: none)
        for i in range(n):
            if cross_up[i]:
                j_up = i if in_window[i] else -1           # a crossover in the opening stretch is ignored
                j_dn = -1
            elif cross_dn[i]:
                j_dn = i if in_window[i] else -1
                j_up = -1
            if j_up >= 0 and in_window[i] and self.confirm_bars <= i - j_up <= self.max_wait and bull[i]:
                sig[i], cross_bar[i], j_up = 1, j_up, -1   # one signal per crossover
            elif j_dn >= 0 and in_window[i] and self.confirm_bars <= i - j_dn <= self.max_wait and bear[i]:
                sig[i], cross_bar[i], j_dn = -1, j_dn, -1
            if j_up >= 0 and i - j_up >= self.max_wait:
                j_up = -1
            if j_dn >= 0 and i - j_dn >= self.max_wait:
                j_dn = -1
        if self.direction == 'long':
            sig = np.where(sig == 1, 1, 0)
        elif self.direction == 'short':
            sig = np.where(sig == -1, -1, 0)
        return {'signal': sig, 'cross_bar': cross_bar, 'exit_long': cross_dn, 'exit_short': cross_up, **ind}

    # ── Trades ─────────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        trades = self.simulate()
        return trades, summarise(trades)

    def _bar_end(self, ts, k: int) -> str:
        return str(pd.Timestamp(ts[k]) + pd.Timedelta(minutes=self.bar_minutes))

    def simulate(self) -> List[Dict[str, Any]]:
        df = self.df
        if len(df) < self.slow_len + 2:
            return []
        idx = df.index
        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        tmin = (idx.hour * 60 + idx.minute).to_numpy()
        ts = idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy()
        day_id = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
        last_of_day = np.concatenate((np.flatnonzero(np.diff(day_id)), [len(df) - 1]))
        day_end = last_of_day[np.searchsorted(last_of_day, np.arange(len(df)))]

        sg = self.signals()
        cutoff = 10 ** 9 if self.positional else self.exit_cutoff
        trades: List[Dict[str, Any]] = []
        per_day: Dict[str, int] = {}
        free_from = 0
        last = len(df) - 1
        for i in np.flatnonzero(sg['signal']):
            e = i + 1                                      # filled at the next open
            day = ts[i][:10]
            if i < free_from or e > last or (not self.positional and day_id[e] != day_id[i]):
                continue
            if per_day.get(day, 0) >= self.max_trades_per_day or tmin[e] >= cutoff:
                continue
            is_long = sg['signal'][i] == 1
            sign = 1.0 if is_long else -1.0
            entry = o[e]
            sl = (entry - sign * self.sl_points) if self.sl_points > 0 else None
            part_px = entry + sign * self.partial_points if self.partial_pct > 0 else None
            exit_sig = sg['exit_long'] if is_long else sg['exit_short']
            end = last if self.positional else int(day_end[i])

            exit_k, exit_px = end, c[end]
            reason = 'Still Open' if self.positional else 'EOD Exit'
            stamp = None
            booked = False                                 # the partial has been taken
            stop = sl
            best = entry                                   # the furthest the trade has gone our way
            for k in range(e, end + 1):
                if k > e and tmin[k] >= cutoff:
                    exit_k, exit_px, reason = k, o[k], 'Time Exit'
                    break
                if stop is not None:                       # the stop first: the worse case within a bar
                    hit = (l[k] <= stop) if is_long else (h[k] >= stop)
                    if hit:
                        gap = min(o[k], stop) if is_long else max(o[k], stop)
                        exit_k, exit_px = k, (gap if k > e else stop)
                        reason = ('Trailing Stop' if stop != sl and stop != entry else
                                  ('Breakeven' if booked else 'Stop Loss'))
                        stamp = self._bar_end(ts, k)
                        break
                if part_px is not None and not booked:
                    if (h[k] >= part_px) if is_long else (l[k] <= part_px):
                        booked = True
                        stop = entry                       # the rest rides at cost
                if self.trail_points > 0:                  # ratchet from the bar just completed
                    best = max(best, h[k]) if is_long else min(best, l[k])
                    if sign * (best - entry) >= self.trail_start:
                        lvl = best - sign * self.trail_points
                        stop = lvl if stop is None else (max(stop, lvl) if is_long else min(stop, lvl))
                if self.exit_on_cross and exit_sig[k]:
                    if k < end and tmin[k + 1] < cutoff:
                        exit_k, exit_px, reason = k + 1, o[k + 1], 'MA Cross'
                    else:
                        exit_k, exit_px = k, c[k]
                        reason = 'Still Open' if self.positional else 'Time Exit'
                    break
            if booked:
                pnl = self.partial_pct * self.partial_points + (1 - self.partial_pct) * sign * (exit_px - entry)
            else:
                pnl = sign * (exit_px - entry)
            exit_time = (ts[exit_k][:11] + f"{self.exit_cutoff // 60:02d}:{self.exit_cutoff % 60:02d}:00"
                         if reason == 'Time Exit' and not self.positional else (stamp or ts[exit_k]))
            per_day[day] = per_day.get(day, 0) + 1
            trades.append({
                'entry_time': ts[e], 'exit_time': exit_time,
                'days_held': (pd.Timestamp(exit_time[:10]) - pd.Timestamp(ts[e][:10])).days,
                'type': 'Long' if is_long else 'Short',
                'pattern': 'MA Cross Up' if is_long else 'MA Cross Down',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': round(float(sl), 2) if sl is not None else None,
                'target_price': round(float(part_px), 2) if part_px is not None else None,
                'partial_booked': booked,
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': {'fast': _r(sg['fast'][i]), 'slow': _r(sg['slow'][i]),
                           'htf': _r(sg['htf'][i]), 'close': _r(c[i]),
                           'cross_time': ts[int(sg['cross_bar'][i])]},
            })
            free_from = max(exit_k, e) + 1
        return trades
