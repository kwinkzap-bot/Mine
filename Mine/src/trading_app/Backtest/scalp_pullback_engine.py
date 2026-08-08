"""
2-Min EMA Scalp Pullback Backtest Engine.

Mechanised from the Dhan scalping video (Mukul Chaudhary,
https://www.youtube.com/watch?v=pvmvkiS1cx4). Designed for 2-minute index spot
candles (3/5-minute also work); options are traded live but backtested here as
spot points, the same convention every other intraday engine in this package uses.

Rules, in the order the engine applies them:

  1. TIMEFRAME  2-minute (3/5 acceptable).
  2. BIAS       EMA10 > EMA20 and close above both  → long only.
                EMA10 < EMA20 and close below both  → short only.
                Anything else → no trades. Only EMA 10/20 are used; no oscillators.
  3. TREND      The market must have TRENDED first. The bias must have held for
                >= trend_min_bars, and the impulse leg (bias flip → running
                extreme) must exceed min_impulse_atr x ATR. Kills range entries.
  4. PULLBACK   Price must come BACK to the moving averages — the bar's low
                (long) / high (short) pierced the chosen EMA within the last
                pullback_window bars. touch_level picks ema20 (the video's own
                "take it at the 20, not the 10" filter), ema10, or either.
  5. SL HUNT    Before entering, price must have swept a prior swing low (long) /
                swing high (short), or breached the impulse leg's 50% level,
                within sl_hunt_lookback bars. Toggleable via require_sl_hunt.
  6. PATTERN    Entry candles are ONLY Morning Star / Bullish Engulfing (long)
                and Evening Star / Bearish Engulfing (short). Dojis are rejected
                as triggers (body must be >= _MIN_BODY_FRAC of the range). The
                day's first trade can be restricted to the 3-candle star via
                first_trade_three_candle.
  7. NO CHASE   A trigger whose range exceeds big_candle_atr_mult x ATR is
                skipped — the move has already happened.
  8. ENTRY      Stop entry at the pattern's high (long) / low (short), live for
                entry_valid_bars bars. A bar gapping through fills at its open.
  9. RISK       SL is the pattern's opposite extreme. risk > max_sl_points skips
                the trade outright.

                NOTE ON THE CEILING. The video's "stop loss should not be more
                than 15 to 18 points" is quoted on the ITM OPTION PREMIUM, for a
                delta 0.55-0.6 option — not on the index. This engine measures
                risk in SPOT points, so the equivalent ceiling is roughly
                18 / 0.575 ~= 31 spot points, hence the default of 30. It also
                scales with the index: 30 points is ~0.13% of NIFTY but only
                ~0.05% of BANKNIFTY, so the optimiser sweeps this value rather
                than assuming one number fits every symbol. 0 disables it.
 10. TARGET     entry +/- rr_ratio x risk, with an optional step trailing SL.
 11. DAY CAPS   max_trades_per_day (3 in the video) and no new entries at/after
                entry_cutoff (11:30). Open positions square off at exit_cutoff.

Exits (first trigger wins; SL is checked before Target within a bar):
  SL Hit / TRAIL_SL : stop taken out
  TG Hit            : rr_ratio x risk reached
  Time Exit         : exit_cutoff force close (at bar open)
  EOD Exit          : last bar of the day force close (at bar close)

Deliberately NOT implemented (the video mentions them, the engine cannot):
  - India VIX > 15-16 "don't scalp option buying" gate. No backtest engine here
    is fed a VIX series; the symbol's own OHLC is all this engine receives.
  - "On a gap day the first trade is always on the downside." That is advice
    rather than a mechanical rule, and rule 2's bias already yields exactly that
    on a gap-down open.
  - Simulating the actual ITM option leg (100-200 points ITM, delta 0.55-0.6).
    P&L is in spot points; the UI converts to rupees via the lot value.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# A trigger candle must have a real body of at least this fraction of its
# total range, which is what disqualifies dojis (rule 6).
_MIN_BODY_FRAC = 0.30

# Fractal swing detection: N bars either side must be higher/lower.
_SWING_LOOKBACK = 2

_TOUCH_LEVELS = ('ema20', 'ema10', 'either')
_DIRECTIONS = ('both', 'long', 'short')


def _parse_hhmm(value, default_mins: int) -> int:
    """'11:30' / '1130' / 690 → minutes past midnight. Falls back to default."""
    if value is None:
        return default_mins
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if not text:
        return default_mins
    try:
        if ':' in text:
            hh, mm = text.split(':')[:2]
            return int(hh) * 60 + int(mm)
        if len(text) == 4:
            return int(text[:2]) * 60 + int(text[2:])
        return int(text)
    except (TypeError, ValueError):
        return default_mins


class ScalpPullbackEngine:

    def __init__(
        self,
        df: pd.DataFrame,
        ema_fast: int = 10,
        ema_slow: int = 20,
        touch_level: str = 'ema20',
        rr_ratio: float = 2.0,
        trail_points: float = None,
        max_sl_points: float = 30.0,
        max_trades_per_day: int = 3,
        entry_cutoff: str = '11:30',
        exit_cutoff: str = '15:15',
        require_sl_hunt: bool = True,
        first_trade_three_candle: bool = True,
        direction: str = 'both',
        pullback_window: int = 3,
        sl_hunt_lookback: int = 20,
        trend_min_bars: int = 5,
        min_impulse_atr: float = 1.5,
        big_candle_atr_mult: float = 2.0,
        entry_valid_bars: int = 2,
        atr_period: int = 14,
    ):
        self.df = df.copy()
        self.ema_fast = max(2, int(ema_fast))
        self.ema_slow = max(3, int(ema_slow))
        self.touch_level = touch_level if touch_level in _TOUCH_LEVELS else 'ema20'
        self.rr = float(rr_ratio)
        self.trail_points = float(trail_points) if trail_points else None
        # 0 / None means "no ceiling" — the stop-width rule is a filter the user
        # can lift to see what it was costing. See the RISK note in the module
        # docstring for why the default is 30 spot points, not the video's 18.
        self.max_sl_points = float(max_sl_points) if max_sl_points else 0.0
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self.entry_cutoff = _parse_hhmm(entry_cutoff, 11 * 60 + 30)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 15)
        self.require_sl_hunt = bool(require_sl_hunt)
        self.first_trade_three_candle = bool(first_trade_three_candle)
        self.direction = direction if direction in _DIRECTIONS else 'both'
        self.pullback_window = max(1, int(pullback_window))
        self.sl_hunt_lookback = max(1, int(sl_hunt_lookback))
        self.trend_min_bars = max(1, int(trend_min_bars))
        self.min_impulse_atr = float(min_impulse_atr)
        self.big_candle_atr_mult = float(big_candle_atr_mult)
        self.entry_valid_bars = max(1, int(entry_valid_bars))
        self.atr_period = max(2, int(atr_period))
        self._prepare()

    # ── Data prep ──────────────────────────────────────────────────────────────

    def _prepare(self):
        if not pd.api.types.is_datetime64_any_dtype(self.df.index):
            if 'date' in self.df.columns:
                self.df = self.df.set_index('date')
            elif 'datetime' in self.df.columns:
                self.df = self.df.set_index('datetime')
            self.df.index = pd.to_datetime(self.df.index)
        self.df = self.df.sort_index()
        # Fyers can return the current day's candles twice; a duplicated bar
        # would double-count in the EMA and shift every pattern index.
        self.df = self.df[~self.df.index.duplicated(keep='last')]
        self.df.columns = [c.lower() for c in self.df.columns]

    # ── Indicators (Pine-compatible) ───────────────────────────────────────────

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        # Pine ta.ema() uses alpha = 2/(period+1) — matches ewm(span=period).
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        # Wilder smoothing, same as rtp_backtest_engine._atr
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()

    # ── Array prep (shared by run + optimiser) ─────────────────────────────────

    def _arrays(self) -> dict:
        """
        Compute every price-derived series ONCE.

        Everything in here depends only on OHLC and the EMA/ATR periods, never on
        the parameters the optimiser sweeps (rr, touch level, direction, SL hunt,
        cut-offs). That lets optimise_scalp_pullback() re-run just the day loop
        per combination instead of recomputing indicators 180 times.
        """
        df = self.df
        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        idx = df.index

        ema_f = self._ema(df['close'], self.ema_fast).to_numpy(dtype=float)
        ema_s = self._ema(df['close'], self.ema_slow).to_numpy(dtype=float)
        atr = self._atr(df['high'], df['low'], df['close'], self.atr_period).to_numpy(dtype=float)

        n = len(df)
        body = np.abs(c - o)
        rng = h - l
        # Doji guard (rule 6): a trigger needs a real body.
        with np.errstate(divide='ignore', invalid='ignore'):
            body_frac = np.where(rng > 0, body / rng, 0.0)
        solid = body_frac >= _MIN_BODY_FRAC

        green = c > o
        red = c < o

        # ── 2-candle patterns ─────────────────────────────────────────────
        bull_engulf = np.zeros(n, dtype=bool)
        bear_engulf = np.zeros(n, dtype=bool)
        if n >= 2:
            bull_engulf[1:] = (
                green[1:] & red[:-1]
                & (c[1:] >= o[:-1]) & (o[1:] <= c[:-1])
                & solid[1:]
            )
            bear_engulf[1:] = (
                red[1:] & green[:-1]
                & (c[1:] <= o[:-1]) & (o[1:] >= c[:-1])
                & solid[1:]
            )

        # ── 3-candle patterns ─────────────────────────────────────────────
        # Morning star: a real red candle, a small-bodied pause, then a green
        # candle closing back above the midpoint of the first candle's body.
        morning_star = np.zeros(n, dtype=bool)
        evening_star = np.zeros(n, dtype=bool)
        if n >= 3:
            first_body = body[:-2]
            mid_body = body[1:-1]
            small_mid = mid_body <= 0.5 * first_body
            has_first_body = first_body > 0

            first_mid_price_dn = (o[:-2] + c[:-2]) / 2.0   # midpoint of a red body
            morning_star[2:] = (
                red[:-2] & has_first_body & small_mid
                & green[2:] & solid[2:]
                & (c[2:] > first_mid_price_dn)
            )
            evening_star[2:] = (
                green[:-2] & has_first_body & small_mid
                & red[2:] & solid[2:]
                & (c[2:] < first_mid_price_dn)
            )

        # ── Fractal swings (for the SL-hunt test) ─────────────────────────
        k = _SWING_LOOKBACK
        swing_low = np.zeros(n, dtype=bool)
        swing_high = np.zeros(n, dtype=bool)
        for i in range(k, n - k):
            window_l = l[i - k:i + k + 1]
            window_h = h[i - k:i + k + 1]
            if l[i] == window_l.min() and (window_l < l[i]).sum() == 0:
                swing_low[i] = True
            if h[i] == window_h.max() and (window_h > h[i]).sum() == 0:
                swing_high[i] = True

        # "Most recent confirmed swing low/high strictly before bar i". A fractal
        # at bar j is only confirmed k bars later, so it becomes visible at j+k.
        prior_swing_low = np.full(n, np.nan)
        prior_swing_high = np.full(n, np.nan)
        last_lo, last_hi = np.nan, np.nan
        for i in range(n):
            prior_swing_low[i] = last_lo
            prior_swing_high[i] = last_hi
            j = i - k
            if j >= 0 and swing_low[j]:
                last_lo = l[j]
            if j >= 0 and swing_high[j]:
                last_hi = h[j]

        day_ids = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy()
        time_mins = (idx.hour * 60 + idx.minute).to_numpy()
        ts_strs = idx.strftime('%Y-%m-%d %H:%M:%S').to_numpy()

        # df is sorted, so day_ids is non-decreasing → contiguous groups
        day_positions = []
        if n:
            start = 0
            for i in range(1, n):
                if day_ids[i] != day_ids[start]:
                    day_positions.append(np.arange(start, i))
                    start = i
            day_positions.append(np.arange(start, n))

        return {
            'open': o, 'high': h, 'low': l, 'close': c,
            'ema_fast': ema_f, 'ema_slow': ema_s, 'atr': atr,
            'bull_engulf': bull_engulf, 'bear_engulf': bear_engulf,
            'morning_star': morning_star, 'evening_star': evening_star,
            'prior_swing_low': prior_swing_low, 'prior_swing_high': prior_swing_high,
            'time_mins': time_mins, 'ts_strs': ts_strs,
            'day_positions': day_positions,
        }

    def _params(self) -> dict:
        """The knobs the optimiser varies, bundled for _run_day."""
        return {
            'touch_level': self.touch_level,
            'rr': self.rr,
            'trail_points': self.trail_points,
            'max_sl_points': self.max_sl_points,
            'max_trades_per_day': self.max_trades_per_day,
            'entry_cutoff': self.entry_cutoff,
            'exit_cutoff': self.exit_cutoff,
            'require_sl_hunt': self.require_sl_hunt,
            'first_trade_three_candle': self.first_trade_three_candle,
            'direction': self.direction,
            'pullback_window': self.pullback_window,
            'sl_hunt_lookback': self.sl_hunt_lookback,
            'trend_min_bars': self.trend_min_bars,
            'min_impulse_atr': self.min_impulse_atr,
            'big_candle_atr_mult': self.big_candle_atr_mult,
            'entry_valid_bars': self.entry_valid_bars,
        }

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        if self.df.empty:
            return [], _summary([])

        arr = self._arrays()
        params = self._params()

        trades = []
        for day_pos in arr['day_positions']:
            trades.extend(_run_day(day_pos, arr, params))

        return trades, _summary(trades)


# ── Signal helpers ─────────────────────────────────────────────────────────────

def _bias_at(i, arr):
    """
    +1 long-only, -1 short-only, 0 no trades (rule 2).

    Deliberately keyed on the EMA ORDER alone, not on which side of the EMA10
    the close currently sits. The video's "price is trading above it" describes
    the trending state, and a pullback into the EMA20 is by definition a close
    BELOW the EMA10 — testing price here would reject every setup the strategy
    exists to take. "Price led the move" is enforced instead by
    _trend_and_impulse(), which checks it over the trending stretch.
    """
    ef, es = arr['ema_fast'][i], arr['ema_slow'][i]
    if not (np.isfinite(ef) and np.isfinite(es)):
        return 0
    if ef > es:
        return 1
    if ef < es:
        return -1
    return 0


def _touched_ema(i, arr, params, is_long):
    """
    Rule 4 — price came back to the moving average within pullback_window bars.

    Long needs a bar LOW at or below the EMA (price dipped into it); short needs
    a bar HIGH at or above it.
    """
    level = params['touch_level']
    start = max(0, i - params['pullback_window'] + 1)
    for j in range(start, i + 1):
        if is_long:
            px = arr['low'][j]
            if level in ('ema20', 'either') and px <= arr['ema_slow'][j]:
                return True
            if level in ('ema10', 'either') and px <= arr['ema_fast'][j]:
                return True
        else:
            px = arr['high'][j]
            if level in ('ema20', 'either') and px >= arr['ema_slow'][j]:
                return True
            if level in ('ema10', 'either') and px >= arr['ema_fast'][j]:
                return True
    return False


def _trend_and_impulse(i, arr, params, is_long):
    """
    Rule 3 — the market must have trended before this pullback.

    Walks back while the bias stays in the trade's direction. Returns
    (ok, leg_low, leg_high, extreme_idx) where the leg runs from the trend's
    origin to its extreme and extreme_idx is that extreme's absolute bar index;
    ok is False when the bias held for fewer than trend_min_bars or the leg is
    smaller than min_impulse_atr x ATR (i.e. we are inside a range).
    """
    want = 1 if is_long else -1
    bars = 0
    led = False   # did price actually trade beyond the EMA10 during the stretch?
    j = i
    # Cap the walk-back so a single all-day trend doesn't scan thousands of bars.
    limit = max(params['sl_hunt_lookback'], params['trend_min_bars']) * 4
    # The leg is measured over a LOCAL window only. Letting it span an entire
    # day-long trend would drag the 50% retracement level hundreds of points
    # away, making that arm of the SL-hunt test unreachable — the video means
    # the 50% of the recent impulse, not of the whole session.
    leg_window = params['sl_hunt_lookback']
    while j >= 0 and bars < limit:
        if _bias_at(j, arr) != want:
            break
        # This is the video's "price is trading above/below it" — asserted over
        # the trend, not at the pullback bar (see _bias_at).
        if is_long and arr['close'][j] > arr['ema_fast'][j]:
            led = True
        elif (not is_long) and arr['close'][j] < arr['ema_fast'][j]:
            led = True
        bars += 1
        j -= 1

    if bars < params['trend_min_bars'] or not led:
        return False, np.nan, np.nan, -1

    # ── Measure the impulse leg ───────────────────────────────────────────
    # The leg must run from the trend's origin to its EXTREME, stopping there —
    # it must NOT extend through the pullback. Including the pullback bars would
    # make the pullback's own low the leg low, so "price traded below the 50%
    # level" would be true by construction and the SL-hunt gate a no-op.
    lo_bound = max(0, i - leg_window + 1, i - bars + 1)
    win_hi = arr['high'][lo_bound:i + 1]
    win_lo = arr['low'][lo_bound:i + 1]
    if not len(win_hi):
        return False, np.nan, np.nan, -1

    if is_long:
        ext = int(np.argmax(win_hi))            # the leg's high
        leg_hi = win_hi[ext]
        leg_lo = win_lo[:ext + 1].min()         # lowest point on the way up
    else:
        ext = int(np.argmin(win_lo))            # the leg's low
        leg_lo = win_lo[ext]
        leg_hi = win_hi[:ext + 1].max()         # highest point on the way down

    if not np.isfinite(leg_hi) or not np.isfinite(leg_lo):
        return False, np.nan, np.nan, -1

    atr = arr['atr'][i]
    if not np.isfinite(atr) or atr <= 0:
        return False, np.nan, np.nan, -1
    if (leg_hi - leg_lo) < params['min_impulse_atr'] * atr:
        return False, np.nan, np.nan, -1
    return True, leg_lo, leg_hi, lo_bound + ext


def _sl_hunt_done(i, arr, params, is_long, leg_lo, leg_hi, ext_idx):
    """
    Rule 5 — weak hands must have been stopped out first.

    Satisfied when, after the impulse leg topped out, price either swept the
    swing low (long) / high (short) that was standing AT THAT MOMENT, or traded
    through the leg's 50% retracement.

    Both references are frozen at ext_idx — the leg's extreme — on purpose. That
    is where the trend-followers' stops actually sit. Reading the swing level
    live at each bar instead would track the pullback's own descending lows, so
    "price broke the prior swing low" would be true by construction and the gate
    would admit every setup (which is exactly what it did before this fix).
    """
    if ext_idx < 0:
        return False
    ref = arr['prior_swing_low'][ext_idx] if is_long else arr['prior_swing_high'][ext_idx]
    mid = (leg_lo + leg_hi) / 2.0

    # Only bars AFTER the extreme can be the hunt — the leg itself is not one.
    start = max(ext_idx + 1, i - params['sl_hunt_lookback'] + 1)
    for j in range(start, i + 1):
        if is_long:
            if np.isfinite(ref) and arr['low'][j] < ref:
                return True
            if np.isfinite(mid) and arr['low'][j] <= mid:
                return True
        else:
            if np.isfinite(ref) and arr['high'][j] > ref:
                return True
            if np.isfinite(mid) and arr['high'][j] >= mid:
                return True
    return False


def _pattern_at(i, arr, is_long, three_only):
    """Rule 6 — returns the pattern name, or None. Stars outrank engulfings."""
    if is_long:
        if arr['morning_star'][i]:
            return 'Morning Star'
        if not three_only and arr['bull_engulf'][i]:
            return 'Bullish Engulfing'
    else:
        if arr['evening_star'][i]:
            return 'Evening Star'
        if not three_only and arr['bear_engulf'][i]:
            return 'Bearish Engulfing'
    return None


def _pattern_extent(i, arr, name):
    """High/low of the whole pattern — 3 bars for a star, 2 for an engulfing."""
    span = 3 if 'Star' in name else 2
    lo = i - span + 1
    return arr['high'][lo:i + 1].max(), arr['low'][lo:i + 1].min()


# ── Single-day simulation ──────────────────────────────────────────────────────

def _run_day(day_pos, arr, params):
    """Simulate one trading day. Returns a list of trade dicts (may be empty)."""
    trades = []
    n = len(day_pos)
    if n < 3:
        return trades

    direction = params['direction']
    allow_long = direction in ('both', 'long')
    allow_short = direction in ('both', 'short')

    k = 0
    while k < n and len(trades) < params['max_trades_per_day']:
        i = day_pos[k]

        # No new entries at/after the entry cut-off (rule 11)
        if arr['time_mins'][i] >= params['entry_cutoff']:
            break

        bias = _bias_at(i, arr)
        if bias == 0 or (bias > 0 and not allow_long) or (bias < 0 and not allow_short):
            k += 1
            continue
        is_long = bias > 0

        # Pattern needs its full span inside the day
        if k < 2:
            k += 1
            continue

        three_only = params['first_trade_three_candle'] and not trades
        name = _pattern_at(i, arr, is_long, three_only)
        if not name:
            k += 1
            continue

        if not _touched_ema(i, arr, params, is_long):
            k += 1
            continue

        ok, leg_lo, leg_hi, ext_idx = _trend_and_impulse(i, arr, params, is_long)
        if not ok:
            k += 1
            continue

        if params['require_sl_hunt'] and not _sl_hunt_done(
                i, arr, params, is_long, leg_lo, leg_hi, ext_idx):
            k += 1
            continue

        # Rule 7 — the move already happened, don't chase it
        atr = arr['atr'][i]
        if np.isfinite(atr) and atr > 0:
            if (arr['high'][i] - arr['low'][i]) > params['big_candle_atr_mult'] * atr:
                k += 1
                continue

        pat_high, pat_low = _pattern_extent(i, arr, name)
        trigger = pat_high if is_long else pat_low
        sl_level = pat_low if is_long else pat_high

        # Rule 9 — reject a stop wider than the ceiling before looking for a fill
        risk_est = (trigger - sl_level) if is_long else (sl_level - trigger)
        if risk_est <= 0:
            k += 1
            continue
        if params['max_sl_points'] > 0 and risk_est > params['max_sl_points']:
            k += 1
            continue

        # Rule 8 — stop entry, live for entry_valid_bars bars after the pattern
        entry_k = None
        entry_price = None
        for m in range(k + 1, min(k + 1 + params['entry_valid_bars'], n)):
            j = day_pos[m]
            if arr['time_mins'][j] >= params['entry_cutoff']:
                break
            if is_long and arr['high'][j] >= trigger:
                entry_price = arr['open'][j] if arr['open'][j] > trigger else trigger
                entry_k = m
                break
            if (not is_long) and arr['low'][j] <= trigger:
                entry_price = arr['open'][j] if arr['open'][j] < trigger else trigger
                entry_k = m
                break

        if entry_k is None:
            k += 1
            continue

        risk = (entry_price - sl_level) if is_long else (sl_level - entry_price)
        if risk <= 0 or (params['max_sl_points'] > 0 and risk > params['max_sl_points']):
            k += 1
            continue

        tp_level = entry_price + params['rr'] * risk if is_long else entry_price - params['rr'] * risk

        trade, exit_k = _manage(
            day_pos, arr, params, entry_k, is_long, entry_price, sl_level, tp_level, name,
        )
        trades.append(trade)
        # Next scan starts after the exit — no overlapping positions.
        k = max(exit_k + 1, entry_k + 1)

    return trades


def _manage(day_pos, arr, params, entry_k, is_long, entry, sl_level, tp_level, pattern):
    """Walk the position forward from its entry bar. Returns (trade, exit_k)."""
    n = len(day_pos)
    trail = params['trail_points']
    best = entry            # best price seen, for the trailing stop
    cur_sl = sl_level

    def _finish(exit_ts, exit_price, reason, k):
        pnl = (exit_price - entry) if is_long else (entry - exit_price)
        return {
            'entry_time': arr['ts_strs'][day_pos[entry_k]],
            'exit_time': exit_ts,
            'type': 'Long' if is_long else 'Short',
            'pattern': pattern,
            'entry_price': round(entry, 2),
            'exit_price': round(exit_price, 2),
            'sl_price': round(sl_level, 2),
            'target_price': round(tp_level, 2),
            'pnl': round(pnl, 2),
            'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            'exit_reason': reason,
        }, k

    for k in range(entry_k, n):
        i = day_pos[k]
        o, h, l, c = arr['open'][i], arr['high'][i], arr['low'][i], arr['close'][i]

        # Force square-off at the exit cut-off (never on the entry bar itself)
        if arr['time_mins'][i] >= params['exit_cutoff'] and k > entry_k:
            return _finish(arr['ts_strs'][i], o, 'Time Exit', k)

        # Ratchet the trailing stop on favourable excursion, using the same
        # step arithmetic as rtp_backtest_engine so TRAIL_SL means one thing.
        if trail:
            if is_long and h > best:
                best = h
                steps = int((best - entry) / trail)
                if steps > 0:
                    cur_sl = max(cur_sl, sl_level + steps * trail)
            elif (not is_long) and l < best:
                best = l
                steps = int((entry - best) / trail)
                if steps > 0:
                    cur_sl = min(cur_sl, sl_level - steps * trail)

        reason = 'TRAIL_SL' if (trail and cur_sl != sl_level) else 'SL Hit'

        # SL is checked before Target within a bar (conservative)
        if is_long:
            if l <= cur_sl:
                return _finish(arr['ts_strs'][i], cur_sl, reason, k)
            if h >= tp_level:
                return _finish(arr['ts_strs'][i], tp_level, 'TG Hit', k)
        else:
            if h >= cur_sl:
                return _finish(arr['ts_strs'][i], cur_sl, reason, k)
            if l <= tp_level:
                return _finish(arr['ts_strs'][i], tp_level, 'TG Hit', k)

    last = day_pos[-1]
    return _finish(arr['ts_strs'][last], arr['close'][last], 'EOD Exit', n - 1)


# ── Summary ────────────────────────────────────────────────────────────────────

def _summary(trades):
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'total_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0,
        }

    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']

    total_pnl = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in losses))

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    running, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        running += t['pnl']
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(pf, 2),
        'max_drawdown': round(max_dd, 2),
    }


# ── Optimiser ──────────────────────────────────────────────────────────────────

_RR_GRID = [1.0, 1.5, 2.0, 2.5, 3.0]
_TOUCH_GRID = ['ema20', 'ema10', 'either']
_DIR_GRID = ['both', 'long', 'short']
_HUNT_GRID = [True, False]
# The video says stop by 11:30-12:00; 15:15 is "let it run all session" as a control.
_ENTRY_CUTOFF_GRID = [11 * 60 + 30, 12 * 60]
# Swept because the right stop width is symbol-dependent — see the RISK note in
# the module docstring. 30 is the NIFTY-equivalent of the video's option-premium
# rule; BANKNIFTY/SENSEX need more. 0 = no ceiling.
_SL_GRID = [20.0, 30.0, 50.0, 0.0]


def _opt_score(s: dict) -> float:
    pf = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < 5 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_scalp_pullback(df, exit_cutoff: str = '15:15',
                            max_trades_per_day: int = 3, trail_points: float = None,
                            min_trades: int = 5) -> list:
    """
    Sweep (SL:Target x EMA touch x direction x SL-hunt x entry cut-off x max SL).

    Indicators and patterns are computed once by _arrays(); each combination only
    re-runs the per-day simulation. Returns results sorted best-first.

    max_sl_points is part of the grid rather than a fixed input: the right stop
    width depends on the index's own scale (see the RISK note in the module
    docstring), so pinning one value would hand every symbol NIFTY's answer.
    """
    base = ScalpPullbackEngine(df=df, exit_cutoff=exit_cutoff,
                               max_trades_per_day=max_trades_per_day,
                               trail_points=trail_points)
    if base.df.empty:
        return []

    arr = base._arrays()
    results = []

    for rr in _RR_GRID:
        for touch in _TOUCH_GRID:
            for dlabel in _DIR_GRID:
                for hunt in _HUNT_GRID:
                    for cutoff in _ENTRY_CUTOFF_GRID:
                        for max_sl in _SL_GRID:
                            params = base._params()
                            params.update({
                                'rr': rr,
                                'touch_level': touch,
                                'direction': dlabel,
                                'require_sl_hunt': hunt,
                                'entry_cutoff': cutoff,
                                'max_sl_points': max_sl,
                            })
                            trades = []
                            for day_pos in arr['day_positions']:
                                trades.extend(_run_day(day_pos, arr, params))
                            s = _summary(trades)
                            if s['total_trades'] < min_trades:
                                continue
                            results.append({
                                'rr_ratio': rr,
                                'touch_level': touch,
                                'direction': dlabel,
                                'require_sl_hunt': hunt,
                                'entry_cutoff': f'{cutoff // 60:02d}:{cutoff % 60:02d}',
                                'max_sl_points': max_sl,
                                'total_trades': s['total_trades'],
                                'wins': s['wins'],
                                'losses': s['losses'],
                                'win_rate': s['win_rate'],
                                'total_pnl': s['total_pnl'],
                                'profit_factor': s['profit_factor'],
                                'max_drawdown': s['max_drawdown'],
                                'avg_win': s['avg_win'],
                                'avg_loss': s['avg_loss'],
                                'score': round(_opt_score(s), 2),
                            })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results
