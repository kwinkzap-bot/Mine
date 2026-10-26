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
square-off (`exit_cutoff`) — unless `positional`.

Positional (`positional=True`): the talk holds an hourly trade for "two-three days"
and a daily one far longer, ending it only on the stop or the red line leaving the
zone. So there is no square-off and no end-of-day exit: the trade is carried across
sessions and the exit fills at the next candle's open, which may be tomorrow's gap.
A trade still open when the data ends is closed at the last close as 'Still Open'.
Daily candles (bar_minutes >= 1440) are always positional: the signal is read at the
close and the order fills at the next open.
`max_trades_per_day` then caps ENTRIES per day.

Like the ST+BB+RSI engine this books INDEX points; `attach_option_legs` (shared,
imported from there) re-prices every intraday trade on the ≈premium CE (long) / PE
(short) and needs an ICICI Breeze login. A positional trade is re-priced by
`attach_positional_legs` below, which holds ONE contract across the sessions.

Indicator conventions are TradingView's: Wilder smoothing for the RSI, an
SMA-seeded EMA, and ta.vwma = sma(x·volume) / sma(volume). They run continuously
across days, as on a chart.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import (
    _WORKERS, MAX_OPTION_LEGS, _parse_hhmm, _prior_minute, _r, rsi, summarise)

EXIT_MODES = ('red', 'green')
ENTRY_MODES = ('breakout', 'pullback', 'either')
HTF_MODES = ('off', 'daily')


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
                 exit_cutoff: str = '15:14', max_trades_per_day: int = 2, bar_minutes: Optional[int] = None,
                 positional: bool = False, entry_mode: str = 'breakout', pb_band: float = 5.0,
                 chop_lookback: int = 0, chop_max: int = 2, htf: str = 'off'):
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
        self.positional = bool(positional)
        self.entry_mode = entry_mode if entry_mode in ENTRY_MODES else 'breakout'
        self.pb_band = max(0.0, float(pb_band))
        self.chop_lookback = max(0, int(chop_lookback or 0))
        self.chop_max = max(0, int(chop_max))
        self.htf = htf if htf in HTF_MODES else 'off'
        self._prepare()
        if bar_minutes:
            self.bar_minutes = int(bar_minutes)
        elif len(self.df) > 1:
            step = self.df.index.to_series().diff().dropna()
            self.bar_minutes = max(1, int(round(step.median().total_seconds() / 60)))
        else:
            self.bar_minutes = 5
        if self.bar_minutes >= 1440:                     # a daily candle: no clock to gate on, and it is carried
            self.entry_start, self.entry_end = 0, 24 * 60
            self.positional = True

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

    def _daily_bias(self, df_idx) -> Tuple[np.ndarray, np.ndarray]:
        """(bull, bear) per candle from the DAILY RSI(9) of the last COMPLETED session:
        above `mid` allows longs, below it shorts. The day's own candle is never used."""
        d = self.df
        day = pd.Series(d.index.normalize(), index=d.index)
        daily = d.groupby(day).agg(close=('close', 'last'))
        r = pd.Series(rsi(daily['close'].to_numpy(dtype=float), self.rsi_period), index=daily.index).shift(1)
        mapped = day.map(r).to_numpy(dtype=float)
        with np.errstate(invalid='ignore'):
            return mapped > self.mid, mapped < self.mid

    @staticmethod
    def _flips(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
        """Crossings of `a` over/under `b` within the last n candles (the chop count)."""
        with np.errstate(invalid='ignore'):
            side = np.sign(a - b)
        side = np.where(np.isfinite(side), side, 0)
        cross = np.concatenate(([0], (np.diff(side) != 0) & (side[1:] != 0) & (side[:-1] != 0))).astype(int)
        return pd.Series(cross).rolling(n, min_periods=1).sum().to_numpy()

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
            brk_l = bull & (age_bull <= self.arm_bars) & (c > prev_h)
            brk_s = bear & (age_bear <= self.arm_bars) & (c < prev_l)
            # Pullback: inside an ongoing zone the red line has come back to within pb_band of 50 (the
            # retracement the talk buys), and the candle closes beyond the previous one — it resumes.
            pb_l = bull & ((red - self.mid) <= self.pb_band) & (c > prev_h)
            pb_s = bear & ((self.mid - red) <= self.pb_band) & (c < prev_l)
        if self.entry_mode == 'breakout':
            long_sig, short_sig = brk_l, brk_s
        elif self.entry_mode == 'pullback':
            long_sig, short_sig = pb_l, pb_s
        else:
            long_sig, short_sig = brk_l | pb_l, brk_s | pb_s
        if self.chop_lookback:                          # the sideways "no trade zone": the lines keep crossing
            chop = self._flips(r, red, self.chop_lookback) + self._flips(r, np.full(len(r), self.mid), self.chop_lookback)
            quiet = chop <= self.chop_max
            long_sig, short_sig = long_sig & quiet, short_sig & quiet
        if self.htf == 'daily':
            hb, hs = self._daily_bias(None)
            long_sig, short_sig = long_sig & hb, short_sig & hs
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
        if self.positional:                              # one continuous run, no day boundaries
            return self._day(0, len(df) - 1, cand, o, h, l, c, signal, sg, tmin, ts)
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
        cutoff = 10 ** 9 if self.positional else self.exit_cutoff    # positional: no clock exit
        per_day: Dict[str, int] = {}
        for i in idxs:
            if self.positional:
                if per_day.get(ts[i][:10], 0) >= self.max_trades_per_day:
                    continue
            elif len(out) >= self.max_trades_per_day:
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

            exit_k, exit_px, reason, stamp = b, c[b], ('Still Open' if self.positional else 'EOD Exit'), None
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
                        exit_k, exit_px, reason = k, c[k], ('Still Open' if self.positional else 'Time Exit')
                    break
            pnl = (exit_px - entry) if is_long else (entry - exit_px)
            if reason == 'Time Exit':
                exit_time = self._clock_exit_stamp(ts[exit_k])
            else:
                exit_time = stamp or ts[exit_k]
            if self.positional:
                per_day[ts[i][:10]] = per_day.get(ts[i][:10], 0) + 1
            out.append({
                'entry_time': ts[e], 'exit_time': exit_time,
                'days_held': (pd.Timestamp(exit_time[:10]) - pd.Timestamp(ts[e][:10])).days,
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


# ── Positional option legs ─────────────────────────────────────────────────────

POSITIONAL_MIN_SESSIONS = 1     # buy an expiry that is at least this many sessions after the entry
ROLL_MINUTE = '15:15'           # the expiry day's minute a contract is sold and the next one bought
ROLL_MODES = ('same', 'premium')


def _fill_minute(stamp: str) -> str:
    """The minute a fill reads its premium at: the close of the minute BEFORE the stamp
    (as `attach_option_legs`), except an order at the opening bell (a daily candle's next
    open, 09:15) which has no minute before it and is read at 09:15 itself."""
    hhmm = stamp[11:16]
    return hhmm if hhmm <= '09:15' else _prior_minute(stamp)


def attach_positional_legs(symbol: str, trades: List[Dict[str, Any]], premium: float = 250.0,
                           progress=None, calendar=None, min_sessions: int = POSITIONAL_MIN_SESSIONS,
                           roll: str = 'same', roll_minute: str = ROLL_MINUTE,
                           sl_pct: float = 0.0, tsl_x: float = 0.0, tsl_y: float = 0.0
                           ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Re-price each positional index trade on an option held, and rolled, across its sessions.

    `attach_option_legs` prices a trade inside one session on the weekly expiry running that
    day: the wrong contract for a trade held for days. Here the trade is bought on the nearest
    expiry at least `min_sessions` sessions after the entry (known on the entry day, so no
    look-ahead), the strike being the one nearest `premium` at the entry minute. When that
    contract expires and the signal is STILL ON (the index trade has not exited), it is sold at
    `roll_minute` on the expiry day and the NEXT expiry bought at that minute:
      roll='same'     the same strike, carried to the next expiry (the default)
      roll='premium'  the strike nearest `premium` again at that minute
    and so on until the index trade's own exit, when the current contract is sold. The trade's
    `pnl` is the sum of the legs' (exit - entry); `legs` lists them and `rolls` counts the
    rolls. Entry/exit premium shown are the first leg's entry and the last leg's exit. A trade
    any leg of which cannot be priced is left out and counted in info['unpriced'].
    `sl_pct` / `tsl_x` / `tsl_y` put the premium stop-loss / trailing SL on the held contract
    (as `attach_option_legs` does inside one session): every session of the hold is scanned,
    the trailed stop carries from one session to the next, and a stop ends the whole trade at
    its price (`exit_reason` 'Stop Loss' / 'Trailing SL'). There is no re-entry: one entry per
    signal. A roll re-bases the stop on the new contract's entry premium. 0 = no stop.
    Returns (priced trades, info) shaped like `attach_option_legs`."""
    from trading_app.service import cpr_option_service as cos

    roll = roll if roll in ROLL_MODES else 'same'
    info: Dict[str, Any] = {'premium': premium, 'index_trades': len(trades), 'unpriced': 0,
                            'truncated': 0, 'lot': cos.lot_size(symbol), 'positional': True,
                            'roll': roll, 'rolls': 0}
    if not trades:
        return [], info
    work = trades[-MAX_OPTION_LEGS:]
    info['truncated'] = len(trades) - len(work)
    fetch = cos._option_minutes_fetcher(symbol)               # raises without a Breeze login
    days = sorted({_date.fromisoformat(t['entry_time'][:10]) for t in work}
                  | {_date.fromisoformat(t['exit_time'][:10]) for t in work})
    cal = sorted(set(calendar)) if calendar else cos._trading_days(symbol, days[0], days[-1])
    from trading_app.Backtest.st_bb_rsi_engine import _listed_expiries
    cal = sorted(set(cal) | set(_listed_expiries(symbol, max(cal))))

    def price(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        d_in = _date.fromisoformat(t['entry_time'][:10])
        d_out = _date.fromisoformat(t['exit_time'][:10])
        side = 'SELL' if t.get('type') == 'Short' else 'BUY'
        ahead = [d for d in cal if d >= d_in]
        anchor = ahead[min(min_sessions, len(ahead) - 1)] if ahead else d_in
        con = cos.contract(symbol, anchor, side, t['entry_price'], cal)
        if con is None:
            return None
        t_in, t_out = _fill_minute(t['entry_time']), _fill_minute(t['exit_time'])
        if premium:
            con, bars, _ = cos.pick_strike(symbol, con, d_in, t_in, premium, fetch)
        else:                                                 # 0 = the ATM strike (a stock)
            bars = fetch(con, d_in)
        b = cos.bar_at(bars, t_in)
        if not b:
            return None
        day, entry_px, legs, stopped, t_from = d_in, b['close'], [], None, t_in

        def stop_scan(con_, bars_, day0, t_start, day_end, t_end, entry_):
            """The premium stop over every session of this contract's hold, or None."""
            carry: Dict[str, float] = {}
            for d in (x for x in cal if day0 <= x <= day_end):
                if d == day0:
                    bs = bars_
                else:
                    try:
                        bs = fetch(con_, d)
                    except cos.OptionDataUnavailable:
                        raise
                    except Exception:                         # noqa: BLE001 — a dead session is skipped
                        bs = []
                hit = cos._premium_stop(bs or [], entry_, t_start if d == day0 else '00:00',
                                        t_end if d == day_end else '23:59', sl_pct, tsl_x, tsl_y, carry)
                if hit:
                    return dict(hit, day=d)
            return None

        while True:
            expiry = _date.fromisoformat(con['expiry'])
            leg_end = min(d_out, expiry)
            if sl_pct > 0:
                hit = stop_scan(con, bars, day, t_from, leg_end,
                                t_out if d_out <= expiry else roll_minute, entry_px)
                if hit:
                    legs.append({'strike': con['strike'], 'expiry': con['expiry'], 'entry': entry_px,
                                 'exit': hit['price'], 'exit_day': hit['day'].isoformat(), 'stop': hit['kind']})
                    stopped = hit
                    break
            if d_out <= expiry:                               # the index trade ends on this contract
                bars_out = bars if day == d_out else fetch(con, d_out)
                bo = cos.bar_at(bars_out, t_out)
                if not bo:
                    return None
                legs.append({'strike': con['strike'], 'expiry': con['expiry'], 'entry': entry_px,
                             'exit': bo['close'], 'exit_day': d_out.isoformat()})
                break
            # The contract expires with the signal still on: sell it, buy the next expiry.
            bars_e = bars if day == expiry else fetch(con, expiry)
            sold = cos.bar_at(bars_e, roll_minute)
            later = [d for d in cal if d > expiry]
            if not sold or not later:
                return None
            nxt = cos.contract(symbol, later[0], side, float(con['strike']), cal)   # level = strike: same strike
            if nxt is None:
                return None
            if roll == 'premium' and premium:
                nxt, bars, _ = cos.pick_strike(symbol, nxt, expiry, roll_minute, premium, fetch)
            else:
                bars = fetch(nxt, expiry)
            bought = cos.bar_at(bars, roll_minute)
            if not bought:
                return None
            legs.append({'strike': con['strike'], 'expiry': con['expiry'], 'entry': entry_px,
                         'exit': sold['close'], 'exit_day': expiry.isoformat()})
            con, day, entry_px, t_from = nxt, expiry, bought['close'], roll_minute
        return {'legs': [dict(g, entry=round(g['entry'], 2), exit=round(g['exit'], 2)) for g in legs],
                'option_type': con['option_type'], 'stopped': stopped}

    done = [0]

    def run(t):
        try:
            return price(t)
        except cos.OptionDataUnavailable:
            raise
        except Exception:                                      # noqa: BLE001 — one dead leg is unpriced, not fatal
            return None
        finally:
            done[0] += 1
            if progress:
                progress(done[0], len(work))

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(run, work))
    priced = []
    for t, res in zip(work, results):
        if not res:
            info['unpriced'] += 1
            continue
        legs = res['legs']
        pnl = round(sum(g['exit'] - g['entry'] for g in legs), 2)
        info['rolls'] += len(legs) - 1
        row = dict(
            t, index_pnl=t['pnl'], entry_premium=legs[0]['entry'], exit_premium=legs[-1]['exit'], pnl=pnl,
            result='WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            strike=legs[0]['strike'], option_type=res['option_type'], expiry=legs[-1]['expiry'],
            rolls=len(legs) - 1, legs=legs)
        hit = res.get('stopped')
        if hit:                       # the premium stop ended the hold before the index trade did
            row.update(index_exit_time=t['exit_time'], exit_time=f"{hit['day'].isoformat()} {hit['time']}:00",
                       exit_reason=hit['kind'],
                       days_held=(hit['day'] - _date.fromisoformat(t['entry_time'][:10])).days)
            info['stops'] = info.get('stops', 0) + 1
        priced.append(row)
    return priced, info
