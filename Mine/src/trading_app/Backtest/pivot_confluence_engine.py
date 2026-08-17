"""
Pivot Confluence Backtest Engine ("yesterday decides today").

Mechanised from the Tamil interview with full-time trader Syed on the VJ Kanmani
channel (https://www.youtube.com/watch?v=b_mEcAjFTtg). The trader's whole method
is four stacked filters he says he trades and nothing else — no SuperTrend, ADX,
Bollinger, MACD, RSI, PCR or option greeks, all of which he calls lagging or
expiry-day-only noise.

The four formulas, in his words, and how each becomes a rule here:

  1. "Yesterday's market decides today's market."
     Classic floor pivots off the PREVIOUS day's H/L/C:
         P  = (PDH + PDL + PDC) / 3
         R1 = 2P − PDL          S1 = 2P − PDH
         R2 = P + (PDH − PDL)   S2 = P − (PDH − PDL)
         R3 = PDH + 2(P − PDL)  S3 = PDL − 2(PDH − P)
     Above the pivot he takes CALLS only, below it PUTS only — never both.

  2. The probability ladder. He quotes 76% for P→R1, 33% for R1→R2 and only 12%
     for R2→R3 (mirrored on the downside), so a fresh entry with price already
     parked at R2/S2 is the one he refuses to take. That is `block_beyond_r2`,
     and it is also why `target_mode='next_level'` aims at the NEXT rung of the
     ladder rather than at a fixed multiple of risk.

  3. Yesterday's High / Low with a FULL BODY beyond it.
     "A full candle body above yesterday's high → the day is an uptrend day.
      A full body below yesterday's low, wick and all → the market only goes
      down." `full_body` requires the whole real body beyond the level (the
     default); `require_no_wick` tightens it to his stricter phrasing, where the
     entire candle including its wicks is clear of the level.

  4. VWAP + 20 EMA confluence.
     "20 EMA above VWAP, and a candle formed above the 20 EMA → go call side.
      20 EMA below the pivot and the candle below the 20 → go put side."
     That stack is `confluence='vwap_ema'`. `require_golden_cross` adds his
     20/200 golden-cross / death-cross bias on top.

  TIMING (he is explicit, and it is the part most strategies here lack):
     "9:15 to 9:30 is uncertainty — algo premium decay. Enter after 9:30 and
      close the trade by 9:45. If you want a second trade, go at 1:30 or 2:30,
      when the UK market opens and the rally is good." Hence two entry windows
      and max_trades_per_day = 2 (one per window).

     ONE DELIBERATE DEVIATION: he says wrap the morning trade up BY 9:45, which
     as a mechanical rule would square off a 9:44 entry one bar later and make
     every morning trade a Time Exit. `morning_exit` therefore defaults to
     10:00, giving the trade room to actually reach a level; the optimiser
     sweeps 09:45 / 10:00 / 10:30 / off so his literal reading is still testable.

Targets: he cites the daily travel of each index (NIFTY ~100 points either way,
BANKNIFTY ~200, SENSEX ~500) and tells the viewer to sit for 50–100 points.
`target_mode='points'` with `target_points` is that; 'next_level' (default) uses
the pivot ladder; 'rr' uses a multiple of the stop, for comparison against every
other engine in this package.

The first-candle Fibonacci ("first candle high = 0%, low = 100%; the market
rarely goes past them, and a real move needs a 127%/162% break") is available as
`fib_ext` — 0 disables it, 1.27 requires the trigger candle to have extended the
first candle's range by 27% in the trade's direction.

Exits (first trigger wins; SL is checked before Target inside a bar):
  SL Hit / TRAIL_SL : stop taken out
  TG Hit            : target reached
  Morning Exit      : morning-window force close (at bar open)
  Time Exit         : exit_cutoff force close (at bar open)
  EOD Exit          : last bar of the day force close (at bar close)

Deliberately NOT implemented (he mentions them; this engine cannot use them):
  - Option greeks (gamma > 0.03, delta > 0.45–0.5). He says himself they only
    work on expiry day and calls them a lottery ticket. This engine is fed index
    OHLC only, no option chain.
  - PCR — he spends two minutes explaining why he does NOT trade it.
  - The actual option leg. P&L is in INDEX SPOT POINTS, the same convention
    every other intraday engine here uses; the UI converts to ₹ via lot value.
    His "small lots, small risk" sizing advice is the Lots field, not a rule.

NOTE ON VWAP FOR INDICES. Index candles come back with volume 0, so VWAP would
be undefined. As in vwap_engine.py, zero volume is treated as 1, which turns
VWAP into a cumulative average of the session's typical price — the same series
TradingView shows on a spot index chart.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

_TRIGGERS = ('yhl', 'pivot', 'either')
_CONFLUENCE = ('vwap_ema', 'vwap', 'ema', 'none')
_TARGET_MODES = ('next_level', 'rr', 'points')
_SL_MODES = ('candle', 'level', 'points')
_WINDOWS = ('both', 'morning', 'afternoon', 'all_day')
_DIRECTIONS = ('both', 'long', 'short')


def _parse_hhmm(value, default_mins: int) -> int:
    """'09:30' / '0930' / 570 → minutes past midnight. Falls back to default."""
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


def _hhmm_str(mins: int) -> str:
    return f'{int(mins) // 60:02d}:{int(mins) % 60:02d}'


class PivotConfluenceEngine:

    def __init__(
        self,
        df: pd.DataFrame,
        entry_trigger: str = 'either',
        full_body: bool = True,
        require_no_wick: bool = False,
        confluence: str = 'vwap_ema',
        ema_period: int = 20,
        ema_slow_period: int = 200,
        require_golden_cross: bool = False,
        block_beyond_r2: bool = True,
        fib_ext: float = 0.0,
        target_mode: str = 'next_level',
        rr_ratio: float = 2.0,
        target_points: float = 0.0,
        sl_mode: str = 'candle',
        sl_points: float = 0.0,
        max_sl_points: float = 0.0,
        trail_points: float = None,
        entry_windows: str = 'both',
        entry_start: str = '09:30',
        morning_end: str = '09:45',
        morning_exit: str = '10:00',
        afternoon_start: str = '13:30',
        afternoon_end: str = '15:00',
        exit_cutoff: str = '15:15',
        max_trades_per_day: int = 2,
        direction: str = 'both',
    ):
        self.df = df.copy()
        self.entry_trigger = entry_trigger if entry_trigger in _TRIGGERS else 'either'
        self.full_body = bool(full_body)
        self.require_no_wick = bool(require_no_wick)
        self.confluence = confluence if confluence in _CONFLUENCE else 'vwap_ema'
        self.ema_period = max(2, int(ema_period))
        self.ema_slow_period = max(3, int(ema_slow_period))
        self.require_golden_cross = bool(require_golden_cross)
        self.block_beyond_r2 = bool(block_beyond_r2)
        # 0 disables the first-candle Fibonacci extension gate entirely.
        self.fib_ext = float(fib_ext or 0.0)
        self.target_mode = target_mode if target_mode in _TARGET_MODES else 'next_level'
        self.rr = float(rr_ratio)
        self.target_points = float(target_points or 0.0)
        self.sl_mode = sl_mode if sl_mode in _SL_MODES else 'candle'
        self.sl_points = float(sl_points or 0.0)
        # 0 / None means "no ceiling" — the stop-width filter can be lifted to
        # see what it was costing.
        self.max_sl_points = float(max_sl_points) if max_sl_points else 0.0
        self.trail_points = float(trail_points) if trail_points else None
        self.entry_windows = entry_windows if entry_windows in _WINDOWS else 'both'
        self.entry_start = _parse_hhmm(entry_start, 9 * 60 + 30)
        self.morning_end = _parse_hhmm(morning_end, 9 * 60 + 45)
        # 0 / 'off' = let the morning trade run to its SL/Target like any other.
        self.morning_exit = _parse_hhmm(morning_exit, 10 * 60) if morning_exit else 0
        self.afternoon_start = _parse_hhmm(afternoon_start, 13 * 60 + 30)
        self.afternoon_end = _parse_hhmm(afternoon_end, 15 * 60)
        self.exit_cutoff = _parse_hhmm(exit_cutoff, 15 * 60 + 15)
        self.max_trades_per_day = max(1, int(max_trades_per_day))
        self.direction = direction if direction in _DIRECTIONS else 'both'
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
        # would double-count into VWAP and shift the day's first candle.
        self.df = self.df[~self.df.index.duplicated(keep='last')]
        self.df.columns = [c.lower() for c in self.df.columns]
        if 'volume' not in self.df.columns:
            self.df['volume'] = 0.0

    # ── Indicators ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        # Pine ta.ema() uses alpha = 2/(period+1) — matches ewm(span=period).
        return series.ewm(span=period, adjust=False).mean()

    # ── Array prep (shared by run + optimiser) ─────────────────────────────────

    def _arrays(self) -> dict:
        """
        Compute every price-derived series ONCE.

        Nothing in here depends on the knobs the optimiser sweeps (trigger,
        confluence, targets, windows, direction), so each combination only
        re-runs the per-day simulation instead of recomputing pivots and VWAP.
        """
        df = self.df
        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        idx = df.index
        n = len(df)

        # ── Session VWAP (rule 4) ─────────────────────────────────────────
        date_key = pd.Series(idx.date, index=idx)
        typical = (df['high'] + df['low'] + df['close']) / 3.0
        # Index candles carry volume 0 → treat as 1, same as vwap_engine.py.
        vol = df['volume'].where(df['volume'] > 0, 1.0)
        pv = typical * vol
        vwap = (pv.groupby(date_key).cumsum() / vol.groupby(date_key).cumsum()).to_numpy(dtype=float)

        ema_fast = self._ema(df['close'], self.ema_period).to_numpy(dtype=float)
        ema_slow = self._ema(df['close'], self.ema_slow_period).to_numpy(dtype=float)

        # ── Floor pivots off YESTERDAY's daily candle (rule 1) ────────────
        daily = df.groupby(date_key).agg(
            d_high=('high', 'max'), d_low=('low', 'min'), d_close=('close', 'last')
        )
        pdh = daily['d_high'].shift(1)
        pdl = daily['d_low'].shift(1)
        pdc = daily['d_close'].shift(1)
        pivot = (pdh + pdl + pdc) / 3.0
        rng = pdh - pdl
        levels = pd.DataFrame({
            'pdh': pdh, 'pdl': pdl,
            'pivot': pivot,
            'r1': 2 * pivot - pdl, 's1': 2 * pivot - pdh,
            'r2': pivot + rng,     's2': pivot - rng,
            'r3': pdh + 2 * (pivot - pdl),
            's3': pdl - 2 * (pdh - pivot),
        })
        # Broadcast each day's levels onto that day's bars
        per_bar = {col: date_key.map(levels[col]).to_numpy(dtype=float) for col in levels.columns}

        # ── First candle of the day (Fibonacci extension gate) ────────────
        first_high = df['high'].groupby(date_key).transform('first').to_numpy(dtype=float)
        first_low = df['low'].groupby(date_key).transform('first').to_numpy(dtype=float)

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

        arr = {
            'open': o, 'high': h, 'low': l, 'close': c,
            'vwap': vwap, 'ema_fast': ema_fast, 'ema_slow': ema_slow,
            'first_high': first_high, 'first_low': first_low,
            'time_mins': time_mins, 'ts_strs': ts_strs,
            'day_positions': day_positions,
        }
        arr.update(per_bar)
        return arr

    def _params(self) -> dict:
        """The knobs the optimiser varies, bundled for _run_day."""
        return {
            'entry_trigger': self.entry_trigger,
            'full_body': self.full_body,
            'require_no_wick': self.require_no_wick,
            'confluence': self.confluence,
            'require_golden_cross': self.require_golden_cross,
            'block_beyond_r2': self.block_beyond_r2,
            'fib_ext': self.fib_ext,
            'target_mode': self.target_mode,
            'rr': self.rr,
            'target_points': self.target_points,
            'sl_mode': self.sl_mode,
            'sl_points': self.sl_points,
            'max_sl_points': self.max_sl_points,
            'trail_points': self.trail_points,
            'entry_windows': self.entry_windows,
            'entry_start': self.entry_start,
            'morning_end': self.morning_end,
            'morning_exit': self.morning_exit,
            'afternoon_start': self.afternoon_start,
            'afternoon_end': self.afternoon_end,
            'exit_cutoff': self.exit_cutoff,
            'max_trades_per_day': self.max_trades_per_day,
            'direction': self.direction,
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

def _window_of(tmin, params):
    """Which entry window a bar time falls in: 'morning', 'afternoon' or None."""
    mode = params['entry_windows']
    if mode in ('both', 'morning', 'all_day'):
        end = params['afternoon_end'] if mode == 'all_day' else params['morning_end']
        if params['entry_start'] <= tmin < end:
            return 'morning' if mode != 'all_day' else 'allday'
    if mode in ('both', 'afternoon'):
        if params['afternoon_start'] <= tmin < params['afternoon_end']:
            return 'afternoon'
    return None


def _last_entry_minute(params):
    """The minute after which no window can open again — lets the day loop break."""
    mode = params['entry_windows']
    if mode == 'morning':
        return params['morning_end']
    if mode == 'all_day':
        return params['afternoon_end']
    return params['afternoon_end']   # 'both' and 'afternoon'


def _beyond(o, h, l, c, level, is_long, params):
    """Is this candle clear of `level` in the trade's direction? (rule 3)"""
    if not np.isfinite(level):
        return False
    if is_long:
        if params['require_no_wick']:
            return l > level
        if params['full_body']:
            return min(o, c) > level
        return c > level
    if params['require_no_wick']:
        return h < level
    if params['full_body']:
        return max(o, c) < level
    return c < level


def _confluence_ok(i, arr, params, is_long):
    """VWAP / 20-EMA stack (rule 4), plus the optional 20-200 cross bias."""
    mode = params['confluence']
    c = arr['close'][i]
    vwap = arr['vwap'][i]
    ema_f = arr['ema_fast'][i]

    if mode in ('vwap_ema', 'vwap') and not np.isfinite(vwap):
        return False
    if mode in ('vwap_ema', 'ema') and not np.isfinite(ema_f):
        return False

    if is_long:
        if mode == 'vwap_ema' and not (c > vwap and ema_f > vwap and c > ema_f):
            return False
        if mode == 'vwap' and not c > vwap:
            return False
        if mode == 'ema' and not c > ema_f:
            return False
    else:
        if mode == 'vwap_ema' and not (c < vwap and ema_f < vwap and c < ema_f):
            return False
        if mode == 'vwap' and not c < vwap:
            return False
        if mode == 'ema' and not c < ema_f:
            return False

    if params['require_golden_cross']:
        ema_s = arr['ema_slow'][i]
        if not np.isfinite(ema_s) or not np.isfinite(ema_f):
            return False
        if is_long and not ema_f > ema_s:
            return False
        if (not is_long) and not ema_f < ema_s:
            return False

    return True


def _fib_ok(i, arr, params, is_long):
    """First-candle Fibonacci extension gate. 0 = off."""
    ext = params['fib_ext']
    if not ext:
        return True
    fh, fl = arr['first_high'][i], arr['first_low'][i]
    if not np.isfinite(fh) or not np.isfinite(fl) or fh <= fl:
        return True
    span = (fh - fl) * (ext - 1.0)
    c = arr['close'][i]
    return c >= fh + span if is_long else c <= fl - span


def _signal_at(i, arr, params, allow_long, allow_short):
    """
    Test one candle for a complete setup.

    Returns (is_long, level, trigger_name) or None. The pivot decides the side
    (rule 1), so a bar can only ever produce one direction.
    """
    o, h, l, c = arr['open'][i], arr['high'][i], arr['low'][i], arr['close'][i]
    pivot = arr['pivot'][i]
    if not np.isfinite(pivot):
        return None

    if c > pivot:
        is_long = True
    elif c < pivot:
        is_long = False
    else:
        return None
    if (is_long and not allow_long) or ((not is_long) and not allow_short):
        return None

    # Probability ladder (rule 2): no fresh entry once price is already at the
    # far rung — only 12% of the time does R2→R3 / S2→S3 follow.
    if params['block_beyond_r2']:
        r2, s2 = arr['r2'][i], arr['s2'][i]
        if is_long and np.isfinite(r2) and c >= r2:
            return None
        if (not is_long) and np.isfinite(s2) and c <= s2:
            return None

    if not _confluence_ok(i, arr, params, is_long):
        return None
    if not _fib_ok(i, arr, params, is_long):
        return None

    trigger = params['entry_trigger']
    candidates = []
    if trigger in ('yhl', 'either'):
        candidates.append(('YH/YL', arr['pdh'][i] if is_long else arr['pdl'][i]))
    if trigger in ('pivot', 'either'):
        candidates.append(('Pivot', pivot))

    for name, level in candidates:
        if _beyond(o, h, l, c, level, is_long, params):
            return is_long, level, name
    return None


def _stop_for(i, arr, params, is_long, entry, level):
    """Stop price for a fresh entry, or None when the setup is unusable."""
    mode = params['sl_mode']
    if mode == 'points' and params['sl_points'] > 0:
        sl = entry - params['sl_points'] if is_long else entry + params['sl_points']
    elif mode == 'level' and np.isfinite(level):
        sl = level
    else:
        sl = arr['low'][i] if is_long else arr['high'][i]

    # A 'level' stop can sit the wrong side of the fill when the next bar gaps
    # back through it — fall back to the signal candle's own extreme.
    if (is_long and sl >= entry) or ((not is_long) and sl <= entry):
        sl = arr['low'][i] if is_long else arr['high'][i]
    if (is_long and sl >= entry) or ((not is_long) and sl <= entry):
        return None

    risk = (entry - sl) if is_long else (sl - entry)
    if risk <= 0:
        return None
    if params['max_sl_points'] and risk > params['max_sl_points']:
        return None
    return sl


def _target_for(i, arr, params, is_long, entry, risk):
    """Target price. 'next_level' walks the pivot ladder (rule 2)."""
    mode = params['target_mode']

    if mode == 'points' and params['target_points'] > 0:
        return entry + params['target_points'] if is_long else entry - params['target_points']

    if mode == 'next_level':
        ladder = ['pivot', 'r1', 'r2', 'r3'] if is_long else ['pivot', 's1', 's2', 's3']
        levels = [arr[k][i] for k in ladder]
        if is_long:
            above = [v for v in levels if np.isfinite(v) and v > entry]
            if above:
                return min(above)
        else:
            below = [v for v in levels if np.isfinite(v) and v < entry]
            if below:
                return max(below)
        # Past the last rung (R3/S3) there is no next level — fall through to
        # the risk multiple rather than skipping an otherwise valid trade.

    return entry + params['rr'] * risk if is_long else entry - params['rr'] * risk


# ── Day simulation ─────────────────────────────────────────────────────────────

def _run_day(day_pos, arr, params):
    """Simulate one trading day. Returns a list of trade dicts (may be empty)."""
    trades = []
    n = len(day_pos)
    if n < 3:
        return trades

    direction = params['direction']
    allow_long = direction in ('both', 'long')
    allow_short = direction in ('both', 'short')
    last_minute = _last_entry_minute(params)

    used_windows = set()
    k = 1                       # the 09:15 candle is never a signal bar
    while k < n - 1 and len(trades) < params['max_trades_per_day']:
        i = day_pos[k]
        tmin = arr['time_mins'][i]
        if tmin >= last_minute:
            break

        window = _window_of(tmin, params)
        # One trade per window (his rule: one morning trade, one afternoon
        # trade). 'all_day' has no windows to ration, so there only
        # max_trades_per_day caps the count.
        if window is None or (window in ('morning', 'afternoon') and window in used_windows):
            k += 1
            continue

        sig = _signal_at(i, arr, params, allow_long, allow_short)
        if sig is None:
            k += 1
            continue
        is_long, level, trigger = sig

        # A close-based signal fills at the NEXT bar's open, never at the
        # signal candle's own close.
        entry_k = k + 1
        entry = arr['open'][day_pos[entry_k]]
        if not np.isfinite(entry):
            k += 1
            continue

        sl = _stop_for(i, arr, params, is_long, entry, level)
        if sl is None:
            k += 1
            continue

        risk = (entry - sl) if is_long else (sl - entry)
        tp = _target_for(i, arr, params, is_long, entry, risk)
        if (is_long and tp <= entry) or ((not is_long) and tp >= entry):
            k += 1
            continue

        trade, exit_k = _manage(day_pos, arr, params, entry_k, is_long,
                                entry, sl, tp, trigger, window)
        trades.append(trade)
        used_windows.add(window)
        k = max(exit_k + 1, entry_k + 1)

    return trades


def _manage(day_pos, arr, params, entry_k, is_long, entry, sl_level, tp_level,
            trigger, window):
    """Walk the position forward from its entry bar. Returns (trade, exit_k)."""
    n = len(day_pos)
    trail = params['trail_points']
    best = entry                 # best price seen, for the trailing stop
    cur_sl = sl_level
    # The video's "close the morning trade by 9:45" clock, applied only to the
    # trade that was opened in the morning window.
    morning_exit = params['morning_exit'] if window == 'morning' else 0

    def _finish(exit_ts, exit_price, reason, k):
        pnl = (exit_price - entry) if is_long else (entry - exit_price)
        return {
            'entry_time': arr['ts_strs'][day_pos[entry_k]],
            'exit_time': exit_ts,
            'type': 'Long' if is_long else 'Short',
            'pattern': trigger,
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
        tmin = arr['time_mins'][i]

        # Force square-offs (never on the entry bar itself)
        if k > entry_k:
            if morning_exit and tmin >= morning_exit:
                return _finish(arr['ts_strs'][i], o, 'Morning Exit', k)
            if tmin >= params['exit_cutoff']:
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

_TRIGGER_GRID = ['either', 'yhl', 'pivot']
# 'none' is the control: it shows what the VWAP/EMA stack is actually worth.
_CONFLUENCE_GRID = ['vwap_ema', 'ema', 'none']
_DIR_GRID = ['both', 'long', 'short']
# (target_mode, rr) pairs — rr is meaningless for the pivot-ladder target, so
# pairing them keeps the sweep from testing the same combo three times.
_TARGET_GRID = [('next_level', 2.0), ('rr', 1.5), ('rr', 2.0), ('rr', 3.0)]
# (entry window, morning exit) pairs — his literal timing, the morning half
# alone, and all-day as the control. morning_exit does nothing without a morning
# window, so 'all_day' carries a single entry instead of three identical ones.
# 'off' (0) lets the morning trade run to SL/Target — see the timing note in the
# module docstring for why 09:45 is not the default.
_WINDOW_GRID = [
    ('both', '09:45'), ('both', '10:00'), ('both', 0),
    ('morning', '09:45'), ('morning', '10:00'), ('morning', 0),
    ('all_day', 0),
]


def _opt_score(s: dict) -> float:
    pf = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < 5 or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def optimise_pivot_confluence(df, exit_cutoff: str = '15:15',
                              max_trades_per_day: int = 2,
                              trail_points: float = None,
                              max_sl_points: float = 0.0,
                              min_trades: int = 5) -> list:
    """
    Sweep (trigger × confluence × direction × target × entry-window/morning-exit
    pair) — 756 combinations.

    Pivots, VWAP and EMAs are computed once by _arrays(); each combination only
    re-runs the per-day simulation. Returns results sorted best-first.
    """
    base = PivotConfluenceEngine(df=df, exit_cutoff=exit_cutoff,
                                 max_trades_per_day=max_trades_per_day,
                                 trail_points=trail_points,
                                 max_sl_points=max_sl_points)
    if base.df.empty:
        return []

    arr = base._arrays()
    results = []

    for trigger in _TRIGGER_GRID:
        for conf in _CONFLUENCE_GRID:
            for dlabel in _DIR_GRID:
                for tmode, rr in _TARGET_GRID:
                    for window, mexit in _WINDOW_GRID:
                        params = base._params()
                        params.update({
                            'entry_trigger': trigger,
                            'confluence': conf,
                            'direction': dlabel,
                            'target_mode': tmode,
                            'rr': rr,
                            'entry_windows': window,
                            'morning_exit': _parse_hhmm(mexit, 0) if mexit else 0,
                        })
                        trades = []
                        for day_pos in arr['day_positions']:
                            trades.extend(_run_day(day_pos, arr, params))
                        s = _summary(trades)
                        if s['total_trades'] < min_trades:
                            continue
                        results.append({
                            'entry_trigger': trigger,
                            'confluence': conf,
                            'direction': dlabel,
                            'target_mode': tmode,
                            'rr_ratio': rr,
                            'entry_windows': window,
                            'morning_exit': _hhmm_str(params['morning_exit']) if params['morning_exit'] else 'off',
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
