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

import bisect
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
MUHURAT_FROM = 13 * 60  # a session whose first candle is at/after this minute is a Muhurat one
_CHUNK = 4           # trades per legs_at call — small, so the calls spread over the workers
_WORKERS = 6


def _r(v, nd: int = 2):
    """Round a signal-card number; a warm-up NaN is None, not invalid JSON."""
    v = float(v)
    return round(v, nd) if np.isfinite(v) else None


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


def williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Williams %R: -100 * (highest high - close) / (highest high - lowest low) over `period`."""
    hh = pd.Series(high).rolling(period).max().to_numpy()
    ll = pd.Series(low).rolling(period).min().to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        return -100.0 * (hh - close) / (hh - ll)


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


HTF_CHOICES = ('off', '1hour', 'daily', 'weekly', 'monthly')
# Calendar days of run-in the higher-timeframe SuperTrend needs before its first value
# (ATR length 10 → ~10 completed periods, plus slack).
HTF_WARMUP_DAYS = {'off': 45, '1hour': 45, 'daily': 45, 'weekly': 150, 'monthly': 400}


def htf_direction(df: pd.DataFrame, tf: str, period: int, mult: float) -> np.ndarray:
    """Per intraday bar: the SuperTrend direction (+1 / -1, 0 = not yet defined) of the
    LAST COMPLETED 1-hour (NSE-anchored, 09:15-10:15 ...) / daily / weekly / monthly candle. The still-forming period is never
    read, so a bar cannot see a close that happens later in its own day/week/month."""
    if tf == '1hour':
        key = (df.index - pd.Timedelta(hours=9, minutes=15)).floor('60min')
        g = df.groupby(key).agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last'))
        _, direction = supertrend(g['high'].to_numpy(float), g['low'].to_numpy(float),
                                  g['close'].to_numpy(float), period, mult)
        prev = pd.Series(direction, index=g.index).shift(1).fillna(0).astype(int)
        return prev.reindex(key).fillna(0).astype(int).to_numpy()
    d = df.groupby(pd.Series(df.index.normalize(), index=df.index)).agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'), close=('close', 'last'))
    if tf == 'weekly':
        per = d.index.to_period('W-SUN')
    elif tf == 'monthly':
        per = d.index.to_period('M')
    else:
        per = d.index.to_period('D')
    g = d.groupby(per).agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last'))
    _, direction = supertrend(g['high'].to_numpy(float), g['low'].to_numpy(float),
                              g['close'].to_numpy(float), period, mult)
    prev = pd.Series(direction, index=g.index).shift(1).fillna(0).astype(int)
    keys = df.index.to_period({'weekly': 'W-SUN', 'monthly': 'M'}.get(tf, 'D'))
    return prev.reindex(keys).fillna(0).astype(int).to_numpy()


# ── Engine ─────────────────────────────────────────────────────────────────────

class StBbRsiEngine:
    """Signals and exits on the index. Indicator series are computed once per
    (period, multiplier) and kept, so a parameter sweep (`optimise`) pays for each
    SuperTrend / band set a single time and only re-runs the cheap simulation."""

    def __init__(self, df: pd.DataFrame, rsi_period: int = 14, rsi_level: float = 70.0,
                 bb_period: int = 20, bb_mult: float = 2.0, bb_offset: int = 0,
                 st_period: int = 10, st_mult: float = 2.0,
                 entry_start: str = '09:15', entry_end: str = '15:14',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 1,
                 require_green: bool = False, direction: str = 'long',
                 rsi_lower: Optional[float] = None, htf: str = 'off',
                 use_rsi: bool = True, use_bb: bool = True, use_st: bool = True,
                 use_pivot: bool = True, positional: bool = False,
                 use_wr: bool = False, wr_period: int = 14, wr_level: float = -30.0,
                 stop_mode: str = 'close', minutes: Optional[pd.DataFrame] = None):
        self.df = df.copy()
        # The SuperTrend exit: 'close' = a candle closes past the line (sold at the next open);
        # 'touch' = the index trades through the PREVIOUS candle's line, sold there inside the bar
        # (ST Touch Break's stop). `minutes` (the 1-minute candles, optional) times that touch to
        # the minute for the option leg; without them it is the bar's own stamp.
        self.stop_mode = stop_mode if stop_mode in ('close', 'touch') else 'close'
        self.minutes = minutes
        # Positional: no square-off and no end-of-day exit; a trade is carried across
        # sessions until SuperTrend turns against it (the 1-hour timeframe), and
        # `max_trades_per_day` then caps ENTRIES per day.
        self.positional = bool(positional)
        # Each indicator can be switched out of the rule. Off, it neither gates the entry
        # nor (SuperTrend) ends the trade: the exit is then the square-off clock only.
        self.use_rsi, self.use_bb = bool(use_rsi), bool(use_bb)
        self.use_st, self.use_pivot = bool(use_st), bool(use_pivot)
        # Williams %R gate (Quantman's "Williams R is above -30"): long needs %R above
        # `wr_level`, the short mirror needs it below -100 - wr_level. Off by default so
        # the live algos, which share signals(), are unchanged.
        self.use_wr, self.wr_period, self.wr_level = bool(use_wr), max(2, int(wr_period)), float(wr_level)
        # Higher-timeframe SuperTrend gate on the trade's side (same length/factor as
        # the entry SuperTrend): 'off' | 'daily' | 'weekly' | 'monthly'.
        self.htf = htf if htf in HTF_CHOICES else 'off'
        self.rsi_period = max(2, int(rsi_period))
        self.rsi_level = float(rsi_level)
        # The short side's RSI line; unset it is the mirror, 100 - rsi_level.
        self.rsi_lower = float(rsi_lower) if rsi_lower is not None else 100.0 - self.rsi_level
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
        self.direction = direction if direction in ('long', 'short', 'both') else 'long'
        self._prepare()
        self._base_cache: Optional[Dict[str, Any]] = None
        self._ind_cache: Dict[tuple, Any] = {}
        self._exit_cache: Dict[str, Any] = {}

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            col = 'date' if 'date' in self.df.columns else 'datetime'
            self.df = self.df.set_index(col)
            self.df.index = pd.to_datetime(self.df.index)
        self.df.columns = [c.lower() for c in self.df.columns]
        self.df = self.df.sort_index()
        self.df = self.df[~self.df.index.duplicated(keep='last')]
        # Diwali Muhurat sessions (one evening hour, e.g. 24 Oct 2022 18:15-19:14) are not a
        # trading day to Quantman: the next morning's pivots come from the last REGULAR session
        # and its indicators do not run through that hour. Kept, they moved R1 and %R enough to
        # drop the 17 Nov 2020 and 25 Oct 2022 entries. A session opening at/after 13:00 is one.
        if len(self.df):
            idx = self.df.index
            first = pd.Series(idx.hour * 60 + idx.minute, index=idx).groupby(idx.normalize()).transform('min')
            self.df = self.df[first.to_numpy() < MUHURAT_FROM]

    # ── Series that do not depend on any swept knob ───────────────────────────

    def _base(self) -> Dict[str, Any]:
        if self._base_cache is None:
            df = self.df
            idx = df.index
            date_key = pd.Series(idx.date, index=idx)
            daily = df.groupby(date_key).agg(d_high=('high', 'max'), d_low=('low', 'min'),
                                             d_close=('close', 'last'))
            pdh, pdl, pdc = (daily[k].shift(1) for k in ('d_high', 'd_low', 'd_close'))
            pivot = (pdh + pdl + pdc) / 3.0
            day_id = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
            bounds = np.flatnonzero(np.diff(day_id)) + 1
            starts = np.concatenate(([0], bounds))
            ends = np.concatenate((bounds, [len(df)])) - 1
            self._base_cache = {
                'o': df['open'].to_numpy(dtype=float), 'h': df['high'].to_numpy(dtype=float),
                'l': df['low'].to_numpy(dtype=float), 'c': df['close'].to_numpy(dtype=float),
                'r1': date_key.map(2 * pivot - pdl).to_numpy(dtype=float),
                's1': date_key.map(2 * pivot - pdh).to_numpy(dtype=float),
                'tmin': (idx.hour * 60 + idx.minute).to_numpy(),
                'ts': idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy(),
                'starts': starts, 'ends': ends,
            }
        return self._base_cache

    def _ind(self, kind: str, *key):
        k = (kind,) + key
        if k not in self._ind_cache:
            b = self._base()
            if kind == 'rsi':
                self._ind_cache[k] = rsi(b['c'], key[0])
            elif kind == 'bb':
                up, _, lo = bollinger(b['c'], key[0], key[1], key[2])
                self._ind_cache[k] = (up, lo)
            elif kind == 'wr':
                self._ind_cache[k] = williams_r(b['h'], b['l'], b['c'], key[0])
            elif kind == 'htf':
                self._ind_cache[k] = htf_direction(self.df, key[0], key[1], key[2])
            else:
                self._ind_cache[k] = supertrend(b['h'], b['l'], b['c'], key[0], key[1])
        return self._ind_cache[k]

    def _exit_tables(self, st_dir: np.ndarray, cutoff: int) -> Tuple[Dict[int, np.ndarray], np.ndarray]:
        """({side: per bar, the first bar at/after it whose SuperTrend direction is `side`
        (len = none)}, per bar, the first bar of its session at/after the `cutoff` minute
        (the session's last bar + 1 = none)). Built once per SuperTrend / cutoff and kept
        (only the latest: a sweep walks one SuperTrend through all its other numbers)."""
        cache = self._exit_cache
        hit = cache.get('flip')
        if hit is None or hit[0] is not st_dir:
            n = len(st_dir)
            ar = np.arange(n, dtype=np.int64)
            cache['flip'] = hit = (st_dir, {v: np.minimum.accumulate(np.where(st_dir == v, ar, n)[::-1])[::-1]
                                            for v in (-1, 1)})
        cut = cache.get('cut')
        if cut is None or cut[0] != cutoff:
            b = self._base()
            tmin, starts, ends = b['tmin'], b['starts'], b['ends']
            first = np.minimum.reduceat(np.where(tmin >= cutoff, np.arange(len(tmin)), len(tmin)), starts)
            first = np.minimum(first, ends + 1)
            cache['cut'] = cut = (cutoff, np.repeat(first, ends - starts + 1))
        return hit[1], cut[1]

    def _touch_tables(self, st_dir: np.ndarray, st_line: np.ndarray) -> Dict[int, np.ndarray]:
        """{side: per bar, the first bar at/after it that trades through the previous bar's
        SuperTrend line while that line was on `side` (len = none)} — a long's stop (side 1, the
        low reaches the line) and a short's (side -1, the high does). Cached like `_exit_tables`."""
        hit = self._exit_cache.get('touch')
        if hit is None or hit[0] is not st_dir:
            b = self._base()
            n = len(st_dir)
            ar = np.arange(n, dtype=np.int64)
            prev_dir = np.concatenate(([0], st_dir[:-1]))
            prev = np.concatenate(([np.nan], st_line[:-1]))
            with np.errstate(invalid='ignore'):
                on = {1: (prev_dir == 1) & (b['l'] <= prev), -1: (prev_dir == -1) & (b['h'] >= prev)}
            self._exit_cache['touch'] = hit = (st_dir, {v: np.minimum.accumulate(np.where(m, ar, n)[::-1])[::-1]
                                                         for v, m in on.items()})
        return hit[1]

    def _cross_minute(self, k: int, level: float, falling: bool) -> str:
        """The first minute of bar k that traded through `level` (down when `falling`), from
        `self.minutes`; the bar's own stamp without them (or when no minute shows it)."""
        ts = self._base()['ts']
        m = self.minutes
        if m is None or not len(m):
            return ts[k]
        hit = self._exit_cache.get('minutes')
        if hit is None or hit[0] is not m:
            mi = m.set_index('date' if 'date' in m.columns else 'datetime') if not isinstance(
                m.index, pd.DatetimeIndex) else m
            self._exit_cache['minutes'] = hit = (m, pd.to_datetime(mi.index).to_numpy(dtype='datetime64[ns]'),
                                                 mi['low'].to_numpy(dtype=float), mi['high'].to_numpy(dtype=float))
        _, m_ts, m_lo, m_hi = hit
        start = np.datetime64(ts[k].replace(' ', 'T'), 'ns')
        same_day = k + 1 < len(ts) and ts[k + 1][:10] == ts[k][:10]
        end = np.datetime64(ts[k + 1].replace(' ', 'T'), 'ns') if same_day else start + np.timedelta64(7, 'h')
        lo, hi = np.searchsorted(m_ts, start, 'left'), np.searchsorted(m_ts, end, 'left')
        seg = (m_lo[lo:hi] <= level) if falling else (m_hi[lo:hi] >= level)
        at = np.flatnonzero(seg)
        return pd.Timestamp(m_ts[lo + at[0]]).strftime('%Y-%m-%d %H:%M:%S') if len(at) else ts[k]

    # ── Simulation ────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if self.df.empty:
            return [], summarise([])
        trades = self.simulate()
        return trades, summarise(trades)

    def _params(self, over: Dict[str, Any]) -> Dict[str, Any]:
        P = dict(rsi_period=self.rsi_period, rsi_level=self.rsi_level, rsi_lower=self.rsi_lower, bb_period=self.bb_period,
                 bb_mult=self.bb_mult, bb_offset=self.bb_offset, st_period=self.st_period,
                 st_mult=self.st_mult, entry_start=self.entry_start, entry_end=self.entry_end,
                 exit_cutoff=self.exit_cutoff, max_trades_per_day=self.max_trades_per_day,
                 require_green=self.require_green, direction=self.direction, htf=self.htf,
                 use_rsi=self.use_rsi, use_bb=self.use_bb, use_st=self.use_st, use_pivot=self.use_pivot,
                 positional=self.positional, use_wr=self.use_wr, wr_period=self.wr_period,
                 wr_level=self.wr_level, stop_mode=self.stop_mode)
        P.update(over)
        if 'rsi_level' in over and 'rsi_lower' not in over:     # a swept upper line keeps the mirror
            P['rsi_lower'] = 100.0 - P['rsi_level']
        return P

    def signals(self, **over) -> Dict[str, Any]:
        """Per bar: +1 long signal, -1 short signal, 0 none (before the entry window
        is applied), plus the series a decision needs. The short side is the exact
        mirror: RSI below (100 - level), close under the LOWER band, SuperTrend on
        the down side, close under S1 (2P - PDH); an optional red candle. Shared by
        the backtest (`simulate`) and the live algo, so the two cannot drift."""
        P = self._params(over)
        b = self._base()
        o, c = b['o'], b['c']
        rsi_v = self._ind('rsi', P['rsi_period'])
        bb_up, bb_lo = self._ind('bb', P['bb_period'], P['bb_mult'], P['bb_offset'])
        st_line, st_dir = self._ind('st', P['st_period'], P['st_mult'])
        n = len(c)
        yes = np.ones(n, dtype=bool)
        with np.errstate(invalid='ignore'):
            long_sig, short_sig = yes.copy(), yes.copy()
            if P['use_rsi']:
                long_sig &= rsi_v > P['rsi_level']
                short_sig &= rsi_v < P['rsi_lower']
            if P['use_bb']:
                long_sig &= c > bb_up
                short_sig &= c < bb_lo
            if P['use_st']:
                long_sig &= (st_dir == 1) & np.isfinite(st_line)
                short_sig &= (st_dir == -1) & np.isfinite(st_line)
            if P['use_pivot']:
                long_sig &= c > b['r1']
                short_sig &= c < b['s1']
            if P['use_wr']:
                wr_v = self._ind('wr', P['wr_period'])
                long_sig &= wr_v > P['wr_level']
                short_sig &= wr_v < -100.0 - P['wr_level']
            if not (P['use_rsi'] or P['use_bb'] or P['use_st'] or P['use_pivot'] or P['use_wr']
                    or P['htf'] != 'off' or P['require_green']):
                # Nothing left to decide a side: a signal on every bar is not a strategy.
                long_sig, short_sig = ~yes, ~yes
        if P['htf'] != 'off':                           # trade only WITH the higher-timeframe trend
            h_dir = self._ind('htf', P['htf'], P['st_period'], P['st_mult'])
            long_sig = long_sig & (h_dir == 1)
            short_sig = short_sig & (h_dir == -1)
        if P['require_green']:                          # candle closes WITH the trade
            long_sig = long_sig & (c > o)
            short_sig = short_sig & (c < o)
        if P['direction'] == 'long':
            short_sig = np.zeros(len(c), dtype=bool)
        elif P['direction'] == 'short':
            long_sig = np.zeros(len(c), dtype=bool)
        return {'signal': np.where(long_sig, 1, np.where(short_sig, -1, 0)), 'rsi': rsi_v,
                'st_line': st_line, 'st_dir': st_dir,
                'lv': {'bb_up': bb_up, 'bb_lo': bb_lo, 'r1': b['r1'], 's1': b['s1']}}

    def simulate(self, **over) -> List[Dict[str, Any]]:
        """Trades for this engine's settings, any of which `over` replaces. `lean=True` (a
        sweep, which reads only times and P&L) leaves each trade's `signal` card out."""
        P = self._params(over)
        b = self._base()
        o, c, tmin = b['o'], b['c'], b['tmin']
        sg = self.signals(**over)
        rsi_v, st_line, st_dir, lv = sg['rsi'], sg['st_line'], sg['st_dir'], sg['lv']
        window = (tmin >= P['entry_start']) & (tmin < P['entry_end'])
        signal = sg['signal'] * window
        cand = np.flatnonzero(signal)                   # only these bars can start a trade

        trades: List[Dict[str, Any]] = []
        if not len(cand):
            return trades
        starts, ends = b['starts'], b['ends']
        if P['positional']:                             # one continuous run, no day boundaries
            return self._day(0, len(c) - 1, cand, o, c, signal, st_dir, st_line, rsi_v, lv,
                             tmin, b['ts'], P)
        # candidates are sorted, so each day's are one slice of them
        day_of = np.searchsorted(starts, cand, side='right') - 1
        cuts = np.flatnonzero(np.diff(day_of)) + 1
        cand_l = cand.tolist()
        for lo, hi in zip([0] + cuts.tolist(), cuts.tolist() + [len(cand_l)]):
            d = day_of[lo]
            trades.extend(self._day(int(starts[d]), int(ends[d]), cand_l[lo:hi], o, c, signal, st_dir,
                                    st_line, rsi_v, lv, tmin, b['ts'], P))
        return trades

    def _day(self, a, b, idxs, o, c, signal, st_dir, st_line, rsi_v, lv, tmin, ts, P):
        """The trades of bars a..b (one session, or the whole run when positional) from the
        sorted signal bars `idxs`. A trade is filled at the next open and held until the first
        of: the square-off minute (sold at that bar's open), SuperTrend closing against it (sold
        at the next open), or bar b. Each exit is looked up, not walked to bar by bar: the sweep
        runs this tens of thousands of times on 1-minute candles."""
        out = []
        idxs = idxs.tolist() if isinstance(idxs, np.ndarray) else list(idxs)
        if not idxs:
            return out
        pos = P['positional']
        cutoff = 10 ** 9 if pos else P['exit_cutoff']   # positional: no clock exit
        nxt, t_cuts = self._exit_tables(st_dir, P['exit_cutoff'])
        touch = self._touch_tables(st_dir, st_line) if P['use_st'] and P['stop_mode'] == 'touch' else None
        # the first bar at/after the square-off minute (times only rise inside a session)
        t_cut = b + 1 if pos else int(t_cuts[a])
        lean = P.get('lean', False)
        per_day: Dict[str, int] = {}
        j, n_idx = 0, len(idxs)
        while j < n_idx:
            i = idxs[j]
            if i >= b:
                break
            if pos:
                if per_day.get(ts[i][:10], 0) >= P['max_trades_per_day']:
                    j += 1
                    continue
            elif len(out) >= P['max_trades_per_day']:
                break
            e = i + 1                                   # filled at the next open
            if tmin[e] >= cutoff:
                break
            is_long = signal[i] == 1
            exit_flip = -1 if is_long else 1             # SuperTrend turning against the trade
            entry = o[e]
            exit_k, exit_px, reason = b, c[b], ('Still Open' if pos else 'EOD Exit')
            k_flip = int(nxt[exit_flip][e]) if P['use_st'] else b + 1
            # touch stop: from the bar after the entry, the index trading through the previous line
            k_touch = int(touch[1 if is_long else -1][e + 1]) if touch is not None and e + 1 <= b else b + 1
            intrabar = False
            if t_cut <= b and t_cut <= min(k_flip, k_touch):    # t_cut > e: the entry bar is before the cutoff
                exit_k, exit_px, reason = t_cut, o[t_cut], 'Time Exit'
            elif k_touch <= b and k_touch <= k_flip:    # touched first (at the flip bar too: inside it)
                line = st_line[k_touch - 1]
                exit_k, intrabar = k_touch, True
                exit_px = min(o[k_touch], line) if is_long else max(o[k_touch], line)
                reason = 'SuperTrend stop'
            elif k_flip <= b:                           # SuperTrend broke against us on this close
                k = k_flip
                name = 'SuperTrend Down' if is_long else 'SuperTrend Up'
                if k < b and tmin[k + 1] < cutoff:
                    exit_k, exit_px, reason = k + 1, o[k + 1], name
                else:
                    # The flip is only known when this candle closes, which is at or
                    # after the square-off minute: by then the clock has already
                    # squared the trade off (the live algo does exactly that).
                    exit_k, exit_px, reason = k, c[k], ('Still Open' if pos else 'Time Exit')
            pnl = (exit_px - entry) if is_long else (entry - exit_px)
            if pos:
                per_day[ts[i][:10]] = per_day.get(ts[i][:10], 0) + 1
            d_in, d_out = ts[e][:10], ts[exit_k][:10]
            if intrabar:
                # gapped through the line: out at the open; else at the minute it crossed (not lean)
                gapped = (o[exit_k] <= exit_px) if is_long else (o[exit_k] >= exit_px)
                out_ts = ts[exit_k] if gapped or lean else self._cross_minute(exit_k, exit_px, is_long)
            out.append({
                'entry_time': ts[e],
                # A square-off is a clock event, not a candle one: Quantman sells at
                # 15:14 itself, not at the open of the next 3-minute bar (15:15).
                'exit_time': (ts[exit_k][:11] + f"{P['exit_cutoff'] // 60:02d}:{P['exit_cutoff'] % 60:02d}:00"
                              if reason == 'Time Exit' else (out_ts if intrabar else ts[exit_k])),
                **({'intrabar_exit': True} if intrabar else {}),
                'days_held': 0 if d_in == d_out else (pd.Timestamp(d_out) - pd.Timestamp(d_in)).days,
                'type': 'Long' if is_long else 'Short',
                'pattern': 'ST+BB+RSI+R1' if is_long else 'ST+BB+RSI+S1',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': None, 'target_price': None,
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': None if lean else {
                    'rsi': _r(rsi_v[i], 1), 'close': _r(c[i]),
                    'bb_band': _r((lv['bb_up'] if is_long else lv['bb_lo'])[i]),
                    'supertrend': _r(st_line[i]),
                    'pivot_level': _r((lv['r1'] if is_long else lv['s1'])[i])},
            })
            # a new trade cannot start before this one ended
            j = bisect.bisect_left(idxs, max(exit_k, e) + 1, j)
        return out


# ── Find best params ───────────────────────────────────────────────────────────

MIN_TRADES = 30          # a handful of lucky trades is not a result


# Simple Trader (Quantman "BB PP ST RSI WR") sweep. The page's own filters — which
# indicators are on, direction, %R length, entry end, square-off, trades a day — are
# held as set; only the numbers Quantman's run picked by eye are varied.
_SIMPLE_RSI = (65, 70, 75, 80)
_SIMPLE_ST_PERIOD = (7, 10, 14)
_SIMPLE_ST_MULT = (1.5, 2.0, 2.5, 3.0)
_SIMPLE_BB_PERIOD = (15, 20)
_SIMPLE_BB_MULT = (2.0, 2.5, 3.0)
_SIMPLE_WR = (-20, -30, -40)
_SIMPLE_START = ('09:15', '09:30')


def simple_grid_size() -> int:
    return (len(_SIMPLE_RSI) * len(_SIMPLE_ST_PERIOD) * len(_SIMPLE_ST_MULT) * len(_SIMPLE_BB_PERIOD)
            * len(_SIMPLE_BB_MULT) * len(_SIMPLE_WR) * len(_SIMPLE_START))


MIN_WIN_RATE = 50.0      # the Simple Trader board keeps combinations that win at least this often
MIN_BOARD_TRADES = 150   # ...and that traded enough for a profit factor to mean something (30 trades gave PF 10)


def return_dd(r: Dict[str, Any]) -> float:
    """Net P&L over max drawdown (rupees when priced on the option, else index points): what the
    sweep earned per unit of the worst fall it had to sit through."""
    net = r.get('net_rs', r['total_pnl']) or 0.0
    dd = abs(r.get('max_dd_rs', r['max_drawdown']) or 0.0)
    return round(net / dd, 2) if dd > 0 else (999.0 if net > 0 else 0.0)


def rank_quality(rows: List[Dict[str, Any]], current: Optional[Dict[str, Any]] = None,
                 min_win: float = MIN_WIN_RATE, min_board: int = MIN_BOARD_TRADES,
                 by: str = 'pf') -> Tuple[List[Dict[str, Any]], int]:
    """Order a sweep the way a trader reads it, not by raw points (raw points reward
    loose, high-trade settings that a premium stop then cuts down): keep win rate
    >= `min_win`, at least `min_board` trades and a profit over the most recent 30 % of the sessions, then rank on
    profit factor, then lowest max drawdown, then highest net P&L (rupees when priced on the option). Falls back to every row when too few
    pass, so the board is never empty. Stamps `current['rank']` (None when the current
    settings do not pass the filter). Returns (ordered rows, how many passed).
    `by='return_dd'` ranks on `return_dd` instead (stamped on every row as `ret_dd`), then net P&L:
    profit factor rewards a rule that hardly trades, this rewards profit for the drawdown taken."""
    # An option-priced row carries the results cards' own rupee figures: drawdown and net P&L are
    # read from those (the numbers BACKTEST shows); an index row falls back to its points.
    def key(r):
        if by == 'return_dd':
            return (-r['ret_dd'], -r.get('net_rs', r['total_pnl']), r.get('max_dd_rs', r['max_drawdown']))
        return (-r['profit_factor'], r.get('max_dd_rs', r['max_drawdown']), -r.get('net_rs', r['total_pnl']))

    if by == 'return_dd':
        for r in rows + ([current] if current is not None else []):
            r['ret_dd'] = return_dd(r)

    def passes(r):
        return (r['win_rate'] >= min_win and r.get('recent_pnl', 0) > 0 and r.get('net_rs', 1) > 0
                and r['total_trades'] >= min_board)

    kept = [r for r in rows if passes(r)]
    passed = len(kept)
    pool = kept if passed >= 5 else rows
    pool = sorted(pool, key=key)
    if current is not None:
        current['rank'] = (1 + sum(1 for r in pool if key(r) < key(current))) if (
            passes(current) or pool is rows) else None
    return pool, passed


def optimise_simple_trader(df: pd.DataFrame, top: int = 25, progress=None, from_date: str = '',
                           **fixed) -> Dict[str, Any]:
    """Find Best Params for the Simple Trader strategies: the same sweep idea as
    `optimise_st_bb_rsi`, but the engine is built with the page's filters (`fixed` is
    the constructor's own keywords: direction, use_*, wr_period, htf, ...) so the
    Williams %R gate and the rest of the rule are in every combination. Scored on INDEX
    points — the premium stop / trailing stop needs option candles, a Breeze read per
    trade — so the board is a shortlist to confirm with a normal run."""
    eng = StBbRsiEngine(df, **fixed)
    days = sorted({str(t)[:10] for t in eng._base()['ts'] if str(t)[:10] >= from_date})
    cut = days[int(len(days) * 0.7)] if days else ''
    total = simple_grid_size()
    cur_trades = eng.simulate()                         # the page's own settings, as they stand
    if from_date:
        cur_trades = [t for t in cur_trades if t['entry_time'][:10] >= from_date]
    current = summarise(cur_trades)
    current.update(rsi_level=eng.rsi_level, st_period=eng.st_period, st_mult=eng.st_mult,
                   bb_period=eng.bb_period, bb_mult=eng.bb_mult, wr_level=eng.wr_level,
                   entry_start=f"{eng.entry_start // 60:02d}:{eng.entry_start % 60:02d}",
                   recent_pnl=round(sum(t['pnl'] for t in cur_trades if t['entry_time'][:10] >= cut), 2))
    rows: List[Dict[str, Any]] = []
    done = 0
    for st_period in _SIMPLE_ST_PERIOD:
        for st_mult in _SIMPLE_ST_MULT:
            eng._ind('st', st_period, st_mult)
            for bb_period in _SIMPLE_BB_PERIOD:
                for bb_mult in _SIMPLE_BB_MULT:
                    for rsi_level in _SIMPLE_RSI:
                        for wr_level in _SIMPLE_WR:
                            for start in _SIMPLE_START:
                                trades = eng.simulate(
                                    st_period=st_period, st_mult=st_mult, bb_period=bb_period,
                                    bb_mult=bb_mult, rsi_level=rsi_level, wr_level=float(wr_level),
                                    entry_start=_parse_hhmm(start, 9 * 60 + 15))
                                if from_date:
                                    trades = [t for t in trades if t['entry_time'][:10] >= from_date]
                                done += 1
                                if len(trades) < MIN_TRADES:
                                    continue
                                sm = summarise(trades)
                                sm.update(rsi_level=rsi_level, st_period=st_period, st_mult=st_mult,
                                          bb_period=bb_period, bb_mult=bb_mult, wr_level=wr_level,
                                          entry_start=start,
                                          recent_pnl=round(sum(t['pnl'] for t in trades
                                                               if t['entry_time'][:10] >= cut), 2))
                                rows.append(sm)
            if progress:
                progress(done, total)
    ordered, passed = rank_quality(rows, current)
    return {'total_combos_tested': total, 'qualified': len(rows), 'passed': passed,
            'min_win': MIN_WIN_RATE, 'results': ordered[:top],
            'best': ordered[0] if ordered else None, 'recent_from': cut, 'min_trades': MIN_TRADES,
            'mode': 'simple', 'current': current, '_current_trades': cur_trades}


# ── Option leg ─────────────────────────────────────────────────────────────────

def _listed_expiries(symbol: str, after) -> List[Any]:
    """Expiries of `symbol` still listed in the Fyers NFO master, dated after
    `after`. The newest sessions' contracts expire BEYOND the candles we hold, so
    there is no calendar to snap a holiday against: the generated Tuesday 20 Oct
    2026 was a market holiday and the contract actually trading expired on Monday
    the 19th, which Breeze/Fyers have no such 20 Oct contract for — those trades
    came back unpriced. The master lists the real dates."""
    try:
        from datetime import date as _d
        from trading_app.service.provider_logic import get_fyers_adapter
        fy = get_fyers_adapter()
        if fy is None:
            return []
        out = set()
        for i in fy.instruments('NFO'):
            if str(i.get('name') or '').upper() == symbol.upper() and i.get('expiry'):
                e = i['expiry'] if isinstance(i['expiry'], _d) else _d.fromisoformat(str(i['expiry'])[:10])
                if e > after:
                    out.add(e)
        return sorted(out)
    except Exception as e:      # noqa: BLE001 — the listing is a refinement, not a requirement
        logger.warning('[ST-BB-RSI] listed expiries unavailable: %s', e)
        return []


def _stamp_to_hhmm(stamp: str) -> str:
    return stamp[11:16]


def _prior_minute(stamp: str) -> str:
    """HH:MM of the minute BEFORE `stamp`. An order placed at 09:18:00 fills at
    the price the market had when 09:18 began — the CLOSE of the 09:17 bar. Read
    off a 389-trade sample of Quantman's own fills this matches to the paisa 31
    times in 36; the close of the 09:18 bar (what this used to read) is a minute
    into the future and was 4.3 points off on average, 2.8 on exits."""
    return (datetime.strptime(stamp[11:16], '%H:%M') - timedelta(minutes=1)).strftime('%H:%M')


def _index_close_at(minutes: Optional[pd.DataFrame], stamp: str) -> Optional[float]:
    """Close of the 1-minute bar stamped `stamp` ('YYYY-MM-DD HH:MM:SS'), else of the
    last one before it on that day; None without minutes."""
    if minutes is None or getattr(minutes, 'empty', True) or 'close' not in minutes:
        return None
    ts = pd.Timestamp(stamp)
    idx = minutes.index
    if getattr(idx, 'tz', None) is not None:
        ts = ts.tz_localize(idx.tz)
    try:
        upto = minutes['close'].loc[:ts]
    except Exception:                       # noqa: BLE001 — an unsorted / odd index: no re-read
        return None
    if upto.empty or upto.index[-1].date() != ts.date():
        return None
    v = float(upto.iloc[-1])
    return v if np.isfinite(v) else None


def attach_option_legs(symbol: str, trades: List[Dict[str, Any]], premium: float = 250.0,
                       bar_minutes: int = 5, progress=None, calendar=None,
                       sl_pct: float = 0.0, tsl_x: float = 0.0, tsl_y: float = 0.0,
                       index_minutes: Optional[pd.DataFrame] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Re-price each index trade on the ≈`premium` CE. Returns (priced trades,
    info). A priced trade keeps its index move in `index_*` and carries the
    premium points in `pnl`; a trade whose leg could not be priced is dropped
    from the list and counted in info['unpriced'].

    `index_minutes` is the caller's 1-minute frame of the series the trade was
    decided on (the one `entry_price` is in). When the premium stop ends a trade
    early, its `exit_price` / `index_pnl` are re-read off it at the stop minute."""
    from trading_app.service import cpr_option_service as cos

    info: Dict[str, Any] = {'premium': premium, 'index_trades': len(trades),
                            'unpriced': 0, 'truncated': 0}
    if not trades:
        return [], info
    work = trades[-MAX_OPTION_LEGS:]
    info['truncated'] = len(trades) - len(work)

    def exit_minute(t):
        # An exit on the final bar of the session has no "next open"; use its last minute.
        # A trade flagged `intrabar_exit` left when the index crossed a line INSIDE the minute it is
        # stamped with, so that minute is the one to price — the one before is a price from
        # before the cross.
        return t['exit_time'][11:16] if t.get('intrabar_exit') else _prior_minute(t['exit_time'])

    reqs = []
    for n, t in enumerate(work):
        # Same for an `intrabar_entry`: the order went out when the index traded through the
        # break level, during the stamped minute. Priced at the close of the minute before it
        # bought before the break and sold before the stop — on 28 Oct 2026 that was 2.6 pts
        # better in and 2.5 pts better out than the live fills (₹361 on one lot). Those legs are
        # read at the middle of the minute (open and close averaged), not at an edge of it.
        ent = t['entry_time'][11:16] if t.get('intrabar_entry') else _prior_minute(t['entry_time'])
        reqs.append({'key': n, 'date': t['entry_time'][:10], 'side': 'BUY' if t.get('type') != 'Short' else 'SELL',
                     'level': t.get('level', t['entry_price']), 'entry_time': ent,
                     'exit_time': exit_minute(t), 'exit_price': t['exit_price'],
                     'entry_mid': bool(t.get('intrabar_entry')), 'exit_mid': bool(t.get('intrabar_exit'))})
    # Breeze meters requests app-wide, so threads cannot beat that pace on a cold
    # run — but each strike probe also spends most of its time waiting on the
    # network, and a warm (disk-cached) run is CPU/IO bound. Small chunks across
    # a few workers hide that latency; map() keeps the trades in order.
    chunks = [reqs[i:i + _CHUNK] for i in range(0, len(reqs), _CHUNK)]
    from datetime import date as _date
    days = sorted({_date.fromisoformat(r['date']) for r in reqs})
    # The sessions come from the caller's own candles when it has them: asking
    # Fyers for a daily history failed under its 50-requests-a-minute cap, and a
    # calendar with holes picked the wrong expiry (a Jan 2022 trade priced on
    # the 10 Mar contract), leaving whole years unpriced.
    if not calendar:
        calendar = cos._trading_days(symbol, days[0], days[-1])
    calendar = sorted(set(calendar) | set(_listed_expiries(symbol, max(calendar))))
    results = []
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(cos.legs_at, symbol, ch, premium=premium, span_min=1,
                               trading_days=calendar, sl_pct=sl_pct, tsl_x=tsl_x, tsl_y=tsl_y)
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
        stopped = {}
        index_pnl = t['pnl']
        if leg.get('stop'):         # the premium's own stop (or its trail) ended the trade first
            stopped = dict(exit_reason=leg['stop'], exit_time=f"{t['entry_time'][:10]} {leg['exit_time']}:00")
            # The index exit has to move with it: left alone it is the SuperTrend exit's,
            # hours later — 29 Oct 2026 showed +199 index pts beside a -82 pt stopped CE,
            # where the live leg left with the index at 73,396 (-96).
            px = _index_close_at(index_minutes, stopped['exit_time'])
            if px is not None:
                index_pnl = round((px - t['entry_price']) if t.get('type') != 'Short'
                                  else (t['entry_price'] - px), 2)
                stopped['exit_price'] = round(px, 2)
        priced.append(dict(
            t, **stopped,
            index_pnl=index_pnl,
            entry_premium=leg['entry'], exit_premium=leg['exit'], pnl=pnl,
            result='WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            strike=leg['strike'], option_type=leg['option_type'], expiry=leg['expiry'],
        ))
    return priced, info


# ── Compounding ────────────────────────────────────────────────────────────────

def brokerage_for(lots: int) -> float:
    """Round-trip brokerage for NIFTY options by lot count — the same table the
    Backtest page uses (calcBrokeragePerTrade in static/js/backtest.js)."""
    table = {1: 103, 2: 158, 3: 213, 4: 268, 5: 330}
    return float(table[lots]) if lots <= 5 else 330.0 + (lots - 5) * 62.0


def apply_compounding(trades: List[Dict[str, Any]], lot_size: int, start_capital: float = 25000.0,
                      per_lot: float = 0.0, max_lots: int = 27,
                      fixed_lots: Optional[int] = None) -> Dict[str, Any]:
    """Size every trade from the account's running balance.

    The account starts at `start_capital` and carries `per_lot` of capital per lot,
    so 25,000 buys 1 lot (or, with `per_lot` 0 = Auto, the trade's own entry premium × lot size), and once the balance reaches 50,000 the next trade is
    2 lots, 75,000 → 3, and so on; a losing run steps the size back down, never
    below 1 lot. The balance moves by each trade's NET rupees (premium points ×
    lot size × lots, less brokerage), booked in order, so a trade is sized from
    what the trades before it earned. With `fixed_lots` every trade is that many
    lots instead (no compounding) but is still booked in rupees, brokerage taken
    off, so the page's Total / Brokerage / Net all come from one place. Mutates `trades` (adds lots, gross_pnl_rupees,
    brokerage, pnl_rupees, balance_after) and returns the ₹ summary fields."""
    # per_lot <= 0 is Auto: the capital one lot needs is that trade's own cost, its entry
    # premium × lot size, so the size follows what the option actually costs that day.
    per_lot = max(0.0, float(per_lot or 0))
    balance = float(start_capital)
    peak = balance
    max_dd = 0.0
    gross_total = brok_total = 0.0
    wins_rs: List[float] = []
    losses_rs: List[float] = []
    max_seen = 1
    for t in sorted(trades, key=lambda x: x['entry_time']):
        cost = per_lot or (float(t.get('entry_premium') or 0) * lot_size)
        lots = int(fixed_lots) if fixed_lots else int(max(1, min(max_lots, balance // cost if cost > 0 else 1)))
        gross = t['pnl'] * lot_size * lots
        brok = brokerage_for(lots)
        net = gross - brok
        balance += net
        peak = max(peak, balance)
        max_dd = max(max_dd, peak - balance)
        gross_total += gross
        brok_total += brok
        (wins_rs if net > 0 else losses_rs).append(net)
        max_seen = max(max_seen, lots)
        t.update(lots=lots, gross_pnl_rupees=round(gross, 2), brokerage=round(brok, 2),
                 pnl_rupees=round(net, 2), balance_after=round(balance, 2))
    gp, gl = sum(wins_rs), abs(sum(losses_rs))
    return {
        'server_rupees': True, 'compounding': not fixed_lots,
        'start_capital': float(start_capital), 'per_lot': per_lot,
        'lot_size': lot_size, 'max_lots_used': max_seen, 'final_balance': round(balance, 2),
        'total_gross_pnl_rupees': round(gross_total, 2), 'total_brokerage': round(brok_total, 2),
        'total_pnl_rupees': round(balance - start_capital, 2),
        'max_drawdown_rupees': round(max_dd, 2),
        'avg_win_rupees': round(gp / len(wins_rs), 2) if wins_rs else 0.0,
        'avg_loss_rupees': round(-gl / len(losses_rs), 2) if losses_rs else 0.0,
        'profit_factor_rupees': round(gp / gl, 2) if gl > 0 else (999.0 if gp > 0 else 0.0),
    }


def rupee_card(priced: List[Dict[str, Any]], data: Dict[str, Any], lot: Optional[int] = None) -> Dict[str, Any]:
    """The ₹ figures of the results cards for these priced trades, booked exactly as the run
    books them (`apply_compounding` with the page's Lots / Lot value / Compounding / Capital):
    gross, brokerage, net, average win / loss and max drawdown in rupees. Mutates `priced`."""
    lots = max(1, int(data.get('lots', 1) or 1))
    compounding = bool(data.get('compounding', False))
    ex = apply_compounding(
        priced, float(data.get('lot_value') or 0) or lot or 65,
        float(data.get('start_capital', 25000) or 25000) if compounding else lots * 50000.0,
        float(data.get('per_lot') or 0), int(data.get('max_lots', 27) or 27),
        fixed_lots=None if compounding else lots)
    return {'gross_rs': ex['total_gross_pnl_rupees'], 'brokerage_rs': ex['total_brokerage'],
            'net_rs': ex['total_pnl_rupees'], 'avg_win_rs': ex['avg_win_rupees'],
            'avg_loss_rs': ex['avg_loss_rupees'], 'max_dd_rs': ex['max_drawdown_rupees']}


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
