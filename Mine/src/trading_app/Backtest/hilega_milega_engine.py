"""
Hilega Milega (HM) backtest — RSI(9) with a 3-EMA and a volume-weighted 21-MA on
the RSI itself, traded on the INDEX and (optionally) re-priced on the option.

The three lines, all of them the RSI's:

  RSI    RSI(9). Above 50 is bullish strength, below 50 is bearish.
  green  EMA(3) of the RSI — it hugs the RSI; when the RSI falls back onto it,
         the move is tiring.
  red    volume-weighted MA(21) of the RSI (a plain WMA where the symbol has no
         volume, e.g. a spot index). The line that decides the trade.

  Bull zone  RSI > 50, red > 50 and red under the RSI line
  Bear zone  RSI < 50, red < 50 and red over the RSI line

Entry. The zone turns on and stays "armed" for `arm_bars` closed candles. The
first candle in that window to CLOSE beyond the previous candle's high (long) /
low (short) is the signal; the order fills at the NEXT candle's open.

Stop. The signal candle's low (long) / high (short). The talk speaks of closing
bases throughout, so by default the stop is hit when a candle CLOSES beyond it
(`sl_on_close`) and the exit is the next open, exactly like the SuperTrend exit
of the ST+BB+RSI engine; with it off the stop is intrabar and fills at the stop
(at the open when the bar gaps through it).

Exit. First of: the stop; the optional `rr` target; the red line leaving the zone
(`exit_mode='red'`: red crosses to the wrong side of the RSI, or the RSI loses 50)
or the RSI falling back onto the green line (`exit_mode='green'`); the intraday
square-off (`exit_cutoff`).

Like the ST+BB+RSI engine this books INDEX points; `attach_option_legs` (shared,
imported from there) re-prices every trade on the ≈premium CE (long) / PE (short)
and needs an ICICI Breeze login.

Indicator conventions are TradingView's: Wilder smoothing for the RSI, an
SMA-seeded EMA, and ta.vwma = sma(x·volume) / sma(volume). They run continuously
across days, as on a chart.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import _parse_hhmm, _r, rsi, summarise

EXIT_MODES = ('red', 'green')


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Pine ta.ema: seeded with the SMA of the first `period` valid values."""
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(np.isfinite(values))
    if not len(valid) or len(values) - valid[0] < period:
        return out
    f = int(valid[0])
    k = 2.0 / (period + 1)
    out[f + period - 1] = values[f:f + period].mean()
    for i in range(f + period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def vwma(values: np.ndarray, volume: np.ndarray, period: int) -> np.ndarray:
    """Pine ta.vwma. Where the window carries no volume (a spot index) the plain
    linear WMA of the same window stands in for it."""
    s = pd.Series(values)
    v = pd.Series(volume).fillna(0.0)
    num = (s * v).rolling(period).sum()
    den = v.rolling(period).sum()
    with np.errstate(divide='ignore', invalid='ignore'):
        out = (num / den).where(den > 0).to_numpy()
    w = np.arange(1, period + 1, dtype=float)
    wma = s.rolling(period).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True).to_numpy()
    return np.where(np.isfinite(out), out, wma)


class HilegaMilegaEngine:
    def __init__(self, df: pd.DataFrame, rsi_period: int = 9, green_len: int = 3, red_len: int = 21,
                 mid: float = 50.0, arm_bars: int = 5, min_gap: float = 0.0,
                 exit_mode: str = 'red', rr: float = 0.0, sl_on_close: bool = True,
                 direction: str = 'both', entry_start: str = '09:15', entry_end: str = '15:14',
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 2, bar_minutes: Optional[int] = None):
        self.df = df.copy()
        self.rsi_period = int(rsi_period)
        self.green_len = int(green_len)
        self.red_len = int(red_len)
        self.mid = float(mid)
        self.arm_bars = max(1, int(arm_bars))
        self.min_gap = max(0.0, float(min_gap))
        self.exit_mode = exit_mode if exit_mode in EXIT_MODES else 'red'
        self.rr = max(0.0, float(rr or 0))
        self.sl_on_close = bool(sl_on_close)
        self.direction = direction if direction in ('long', 'short', 'both') else 'both'
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 15)
        self.entry_end = _parse_hhmm(entry_end, 15 * 60 + 14)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 14)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self._prepare()
        if bar_minutes:
            self.bar_minutes = int(bar_minutes)
        elif len(self.df) > 1:
            step = self.df.index.to_series().diff().dropna()
            self.bar_minutes = max(1, int(round(step.median().total_seconds() / 60)))
        else:
            self.bar_minutes = 5

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            col = 'date' if 'date' in self.df.columns else 'datetime'
            self.df = self.df.set_index(col)
            self.df.index = pd.to_datetime(self.df.index)
        self.df.columns = [c.lower() for c in self.df.columns]
        if 'volume' not in self.df.columns:
            self.df['volume'] = 0.0
        self.df = self.df.sort_index()
        self.df = self.df[~self.df.index.duplicated(keep='last')]

    # ── Signals ────────────────────────────────────────────────────────────────

    def indicators(self) -> Dict[str, np.ndarray]:
        c = self.df['close'].to_numpy(dtype=float)
        r = rsi(c, self.rsi_period)
        green = ema(r, self.green_len)
        red = vwma(r, self.df['volume'].to_numpy(dtype=float), self.red_len)
        return {'rsi': r, 'green': green, 'red': red}

    @staticmethod
    def _since_off(state: np.ndarray) -> np.ndarray:
        """Candles since the zone was last OFF (0 on an off candle) — the zone's age."""
        out = np.empty(len(state), dtype=np.int64)
        n = 10 ** 9                                    # never off yet: not a fresh turn-on
        for i, on in enumerate(state):
            n = n + 1 if on else 0
            out[i] = n
        return out

    def signals(self) -> Dict[str, Any]:
        ind = self.indicators()
        r, g, red = ind['rsi'], ind['green'], ind['red']
        h = self.df['high'].to_numpy(dtype=float)
        l = self.df['low'].to_numpy(dtype=float)
        c = self.df['close'].to_numpy(dtype=float)
        with np.errstate(invalid='ignore'):
            bull = (r > self.mid) & (red > self.mid) & (red < r) & ((r - red) >= self.min_gap)
            bear = (r < self.mid) & (red < self.mid) & (red > r) & ((red - r) >= self.min_gap)
        age_bull, age_bear = self._since_off(bull), self._since_off(bear)
        prev_h = np.concatenate(([np.nan], h[:-1]))
        prev_l = np.concatenate(([np.nan], l[:-1]))
        with np.errstate(invalid='ignore'):
            long_sig = bull & (age_bull <= self.arm_bars) & (c > prev_h)
            short_sig = bear & (age_bear <= self.arm_bars) & (c < prev_l)
        sig = np.where(long_sig, 1, np.where(short_sig, -1, 0))
        if self.direction == 'long':
            sig = np.where(sig == 1, 1, 0)
        elif self.direction == 'short':
            sig = np.where(sig == -1, -1, 0)
        with np.errstate(invalid='ignore'):
            if self.exit_mode == 'red':
                exit_long = (red >= r) | (r < self.mid)
                exit_short = (red <= r) | (r > self.mid)
            else:
                exit_long = (r <= g) | (r < self.mid)
                exit_short = (r >= g) | (r > self.mid)
        return {'signal': sig, 'exit_long': exit_long, 'exit_short': exit_short, **ind}

    # ── Trades ─────────────────────────────────────────────────────────────────

    def run(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        trades = self.simulate()
        return trades, summarise(trades)

    def simulate(self) -> List[Dict[str, Any]]:
        df = self.df
        if len(df) < self.red_len + self.rsi_period + 2:
            return []
        idx = df.index
        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        tmin = (idx.hour * 60 + idx.minute).to_numpy()
        ts = idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy()
        day_id = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
        bounds = np.flatnonzero(np.diff(day_id)) + 1
        starts = np.concatenate(([0], bounds))
        ends = np.concatenate((bounds, [len(df)])) - 1

        sg = self.signals()
        window = (tmin >= self.entry_start) & (tmin < self.entry_end)
        signal = sg['signal'] * window
        cand = np.flatnonzero(signal)
        trades: List[Dict[str, Any]] = []
        if not len(cand):
            return trades
        day_of = np.searchsorted(starts, cand, side='right') - 1
        for d in np.unique(day_of):
            trades.extend(self._day(int(starts[d]), int(ends[d]), cand[day_of == d],
                                    o, h, l, c, signal, sg, tmin, ts))
        return trades

    def _clock_exit_stamp(self, stamp: str) -> str:
        return stamp[:11] + f"{self.exit_cutoff // 60:02d}:{self.exit_cutoff % 60:02d}:00"

    def _bar_end(self, ts, k: int) -> str:
        return str(pd.Timestamp(ts[k]) + pd.Timedelta(minutes=self.bar_minutes))

    def _day(self, a, b, idxs, o, h, l, c, signal, sg, tmin, ts):
        out = []
        free_from = a                                    # a new trade cannot start before the last one ended
        cutoff = self.exit_cutoff
        for i in idxs:
            if len(out) >= self.max_trades_per_day:
                break
            if i < free_from or i >= b:
                continue
            e = i + 1                                    # filled at the next open
            if tmin[e] >= cutoff:
                break
            is_long = signal[i] == 1
            sl = l[i] if is_long else h[i]               # the signal candle's far side
            entry = o[e]
            risk = (entry - sl) if is_long else (sl - entry)
            tgt = None
            if self.rr > 0 and risk > 0:
                tgt = entry + risk * self.rr if is_long else entry - risk * self.rr
            exit_sig = sg['exit_long'] if is_long else sg['exit_short']
            sig_name = 'Red leaves zone' if self.exit_mode == 'red' else 'Green touches RSI'

            exit_k, exit_px, reason, stamp = b, c[b], 'EOD Exit', None
            for k in range(e, b + 1):
                if k > e and tmin[k] >= cutoff:
                    exit_k, exit_px, reason = k, o[k], 'Time Exit'
                    break
                if not self.sl_on_close:                  # intrabar: the stop first, then the target
                    hit_sl = (l[k] <= sl) if is_long else (h[k] >= sl)
                    if hit_sl:
                        gap = min(o[k], sl) if is_long else max(o[k], sl)
                        exit_k, exit_px, reason = k, (gap if k > e else sl), 'Stop Loss'
                        stamp = self._bar_end(ts, k)
                        break
                if tgt is not None:
                    hit_t = (h[k] >= tgt) if is_long else (l[k] <= tgt)
                    if hit_t:
                        exit_k, exit_px, reason = k, tgt, 'Target'
                        stamp = self._bar_end(ts, k)
                        break
                closed_through = (c[k] < sl) if is_long else (c[k] > sl)
                sig_out = bool(exit_sig[k]) if np.isfinite(sg['red'][k]) else False
                if (self.sl_on_close and closed_through) or sig_out:
                    name = 'Stop Loss' if (self.sl_on_close and closed_through) else sig_name
                    if k < b and tmin[k + 1] < cutoff:
                        exit_k, exit_px, reason = k + 1, o[k + 1], name
                    else:
                        # Known only at this close, at or after the square-off minute: the clock
                        # has already squared the trade off.
                        exit_k, exit_px, reason = k, c[k], 'Time Exit'
                    break
            pnl = (exit_px - entry) if is_long else (entry - exit_px)
            if reason == 'Time Exit':
                exit_time = self._clock_exit_stamp(ts[exit_k])
            else:
                exit_time = stamp or ts[exit_k]
            out.append({
                'entry_time': ts[e], 'exit_time': exit_time,
                'type': 'Long' if is_long else 'Short',
                'pattern': 'HM Bull' if is_long else 'HM Bear',
                'entry_price': round(float(entry), 2), 'exit_price': round(float(exit_px), 2),
                'sl_price': round(float(sl), 2),
                'target_price': round(float(tgt), 2) if tgt is not None else None,
                'pnl': round(float(pnl), 2),
                'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
                'exit_reason': reason,
                'signal': {'rsi': _r(sg['rsi'][i], 1), 'green': _r(sg['green'][i], 1),
                           'red': _r(sg['red'][i], 1), 'close': _r(c[i])},
            })
            free_from = max(exit_k, e) + 1
        return out
