"""30-Sec Option Breakout backtest engine.

The 2nd-candle breakout rule, but read on the OPTION's own candles instead of
the index's — which is the whole difference from `second_candle_engine`, and it
is not a cosmetic one.

Every trading day:

  1. NIFTY's session open picks the nearest strike — an open of 23885 on a 50
     ladder rounds to 23900.
  2. BOTH legs of that strike are watched: 23900 CE and 23900 PE.
  3. Each leg's own 2nd 30-second candle (09:15:30–09:16:00 for a 09:15 open)
     is its breakout range.
  4. When a leg's premium breaks ABOVE that candle's high, the leg is BOUGHT at
     that high.
        SL     = the 2nd candle's Low − `sl_buffer` (₹1 by default)
        Target = Entry + rr_ratio × (Entry − SL)     — 1:1, 1:2 or 1:3
  5. The legs are independent: on a day where both break out, both are taken,
     which is the point of watching the pair. A leg trades at most once a day.

So every price on a trade row — entry, exit, SL, target, P&L — is a PREMIUM,
and one point of P&L is one rupee of premium per unit of the lot.

Why this needs Breeze
---------------------
The premiums are real recorded candles for the contract that was actually
trading on the day, fetched by (root, expiry, strike, right). They are NOT the
Black-Scholes model `second_candle_engine`'s `option_pnl` flag uses — that flag
exists precisely because an index-signalled backtest has no chain to read. Here
the signal itself lives on the premium, so a modelled price would be inventing
the entry, not just re-pricing it. The caller does the fetching; this engine
only simulates what it is handed.

Exits (first trigger wins, SL checked before Target within a bar):
  SL Hit   : premium trades at/below the buffered range low
  TG Hit   : premium trades at/above the target
  Time Exit: cut-off time force close (at bar open)
  EOD Exit : last bar of the day force close (at bar close)
"""

import logging

import pandas as pd

# The same statistics the 2nd-candle run reports, because the results screen
# reads the identical keys off both. Shared rather than copied so the two
# cannot drift into disagreeing about what "profit factor" means.
from trading_app.Backtest.second_candle_engine import _summary

logger = logging.getLogger(__name__)

# A range candle worth trading has to have some height: a leg that printed
# O=H=L=C for its whole 2nd candle (a deep OTM strike nobody touched at 09:15)
# would otherwise enter on any tick with a zero-width stop and an instant
# target.
_MIN_RANGE = 0.05


class OptionBreakoutEngine:

    def __init__(
        self,
        legs,                        # [{'day','option_type','strike','expiry','spot_open','candles'}]
        candle_index: int = 2,       # 1-based: which session candle is the range
        rr_ratio: float = 2.0,       # Target = rr_ratio × risk  (1:2 → 2.0)
        sl_buffer: float = 1.0,      # SL sits this far BELOW the range low
        exit_hour: int = 15,
        exit_minute: int = 25,
    ):
        self.legs = list(legs or [])
        self.candle_index = max(1, int(candle_index))
        self.rr = float(rr_ratio)
        self.sl_buffer = max(0.0, float(sl_buffer))
        self.cutoff = int(exit_hour) * 60 + int(exit_minute)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        prepared = prepare_legs(self.legs)
        trades = [t for t in (
            _simulate(leg, self.candle_index, self.rr, self.sl_buffer, self.cutoff)
            for leg in prepared) if t is not None]
        return trades, summarise(trades, legs_scanned=len(self.legs),
                                 legs_with_data=len(prepared),
                                 days_scanned=len({l.get('day') for l in self.legs}))


# ── Summary ────────────────────────────────────────────────────────────────────

def summarise(trades, legs_scanned=0, legs_with_data=0, days_scanned=0):
    """The statistics the results screen reads, plus the coverage figures that
    only mean something for an option-native run."""
    trades.sort(key=lambda t: (t['entry_time'], t['option_type']))
    summary = _summary(trades)
    summary['pnl_basis']      = 'option_premium'
    summary['legs_scanned']   = legs_scanned
    summary['legs_with_data'] = legs_with_data
    summary['days_scanned']   = days_scanned
    # Both legs breaking out is the case the pair exists to catch, so it is
    # worth reporting rather than left to be counted off the table.
    by_day: dict = {}
    for t in trades:
        by_day[t['day']] = by_day.get(t['day'], 0) + 1
    summary['both_legs_days'] = sum(1 for n in by_day.values() if n > 1)
    return summary


# ── Leg preparation ────────────────────────────────────────────────────────────

def prepare_legs(legs):
    """Broker candles → numpy arrays, once.

    Split out of the run because the optimiser sweeps ~150 combinations over the
    SAME candles: re-deriving the arrays inside each combination made the sweep
    a DataFrame benchmark rather than a strategy one. Legs whose fetch came back
    empty are dropped here, so `len()` of the result is the leg count that had
    data.
    """
    out = []
    for leg in legs:
        df = _frame(leg.get('candles'))
        if df is None or df.empty:
            continue
        out.append({
            'meta':   leg,
            'open':   df['open'].to_numpy(dtype=float),
            'high':   df['high'].to_numpy(dtype=float),
            'low':    df['low'].to_numpy(dtype=float),
            'close':  df['close'].to_numpy(dtype=float),
            'tmin':   (df.index.hour * 60 + df.index.minute).to_numpy(),
            'stamp':  df.index.strftime('%Y-%m-%d %H:%M:%S').to_numpy(),
        })
    return out


# ── One leg of one day ─────────────────────────────────────────────────────────

# What a walk over a PARTIAL session ended in. The distinction only exists for
# the incremental fetcher: on a complete day, running out of bars is the close,
# and on a partial one it means the answer is still in the bars nobody has paid
# for yet.
DONE, NO_ENTRY_YET, OPEN = 'done', 'no_entry_yet', 'open'


class LegWalk:
    """The outcome of simulating one leg over the bars available so far.

    `trade` is the finished trade, or None. `state` says whether that None is
    final. `range_high` is filled as soon as the range candle is known, and
    `sl`/`tp`/`entry_min` once a position is open — those are what the
    incremental fetcher asks the cheap 1-minute series about.
    """

    __slots__ = ('trade', 'state', 'sl', 'tp', 'range_high', 'entry_min')

    def __init__(self, trade=None, state=DONE, sl=None, tp=None,
                 range_high=None, entry_min=None):
        self.trade, self.state = trade, state
        self.sl, self.tp = sl, tp
        self.range_high, self.entry_min = range_high, entry_min


def walk_leg(leg, candle_index, rr, sl_buffer, cutoff, whole_session=True):
    """Simulate one CE or PE over the bars `leg` holds.

    `whole_session=False` says these are only the part of the day fetched so
    far. Without that distinction a half-fetched day reports a confident EOD
    Exit at 09:30.
    """
    opens, highs, lows, closes = leg['open'], leg['high'], leg['low'], leg['close']
    tmins, stamps, meta = leg['tmin'], leg['stamp'], leg['meta']
    n = len(opens)
    if n <= candle_index:            # range candle + at least one watch bar
        return LegWalk(state=DONE if whole_session else NO_ENTRY_YET)

    rc = candle_index - 1
    range_high = highs[rc]
    range_low  = lows[rc]
    if not (range_high > range_low + _MIN_RANGE):
        return LegWalk(state=DONE)   # a flat range candle is never tradable

    sl_level = range_low - sl_buffer
    entry_i = entry = tp_level = None
    for i in range(candle_index, n):
        if tmins[i] >= cutoff:       # no new entries at/after the cut-off
            return LegWalk(state=DONE, range_high=range_high)
        if highs[i] < range_high:
            continue
        # A bar that opens beyond the trigger fills at its open, not at a price
        # that was never available once the gap had happened.
        entry = opens[i] if opens[i] > range_high else range_high
        risk = entry - sl_level
        if risk <= 0:                # gapped straight through the stop
            return LegWalk(state=DONE)
        tp_level = entry + rr * risk
        entry_i = i
        break

    if entry_i is None:
        # Nothing triggered in the bars we have — final only if that was the
        # whole day.
        return LegWalk(state=DONE if whole_session else NO_ENTRY_YET,
                       range_high=range_high)

    base = {
        'day':         str(meta.get('day')),
        'option_type': meta.get('option_type'),
        'strike':      meta.get('strike'),
        'expiry':      str(meta.get('expiry')) if meta.get('expiry') else None,
        'spot_open':   meta.get('spot_open'),
        'entry_time':  stamps[entry_i],
        'entry_price': entry,
        'sl':          sl_level,
        'tp':          tp_level,
        'range_high':  range_high,
        'range_low':   range_low,
    }
    done = lambda ts, px, why: LegWalk(_make_trade(base, ts, px, why), DONE)

    for i in range(entry_i, n):
        # Force square-off at the cut-off (never on the entry bar itself).
        if tmins[i] >= cutoff and i > entry_i:
            return done(stamps[i], opens[i], 'Time Exit')
        # SL before Target within a bar — the conservative read, and the same
        # order second_candle_engine uses.
        if lows[i] <= sl_level:
            return done(stamps[i], sl_level, 'SL Hit')
        if highs[i] >= tp_level:
            return done(stamps[i], tp_level, 'TG Hit')

    if not whole_session:
        # Still open at the end of what we have: one of the day's unfetched
        # bars decides this one.
        return LegWalk(state=OPEN, sl=sl_level, tp=tp_level,
                       range_high=range_high, entry_min=int(tmins[entry_i]))

    return done(stamps[n - 1], closes[n - 1], 'EOD Exit')


def _simulate(leg, candle_index, rr, sl_buffer, cutoff):
    """The trade one leg produced over a COMPLETE session, or None."""
    return walk_leg(leg, candle_index, rr, sl_buffer, cutoff).trade


# ── Helpers ────────────────────────────────────────────────────────────────────

def _frame(candles):
    """Broker candles → a time-indexed OHLC frame, or None if unusable."""
    if not candles:
        return None
    df = pd.DataFrame(candles)
    if df.empty:
        return None
    df.columns = [c.lower() for c in df.columns]
    key = 'date' if 'date' in df.columns else ('datetime' if 'datetime' in df.columns else None)
    if key is None or not {'open', 'high', 'low', 'close'} <= set(df.columns):
        return None
    df = df.set_index(pd.to_datetime(df[key])).sort_index()
    # Breeze can repeat a window's edge bar across two requests; a duplicated
    # first bar would silently become the "2nd candle" and move the range.
    return df[~df.index.duplicated(keep='last')]


def _make_trade(base, exit_ts, exit_price, exit_reason):
    """A trade row. Every price on it is a PREMIUM — the leg is always bought,
    so P&L is simply exit − entry."""
    pnl = exit_price - base['entry_price']
    return {
        'day':          base['day'],
        'option_type':  base['option_type'],
        'strike':       base['strike'],
        'expiry':       base['expiry'],
        'spot_open':    base['spot_open'],
        'entry_time':   base['entry_time'],
        'exit_time':    exit_ts,
        # Buying a CE and buying a PE are both long the option; the leg column
        # is what says which side of the index the trade was betting on.
        'type':         'Long',
        'entry_price':  round(base['entry_price'], 2),
        'exit_price':   round(exit_price, 2),
        'sl_price':     round(base['sl'], 2),
        'target_price': round(base['tp'], 2),
        'range_high':   round(base['range_high'], 2),
        'range_low':    round(base['range_low'], 2),
        'pnl':          round(pnl, 2),
        'result':       'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason':  exit_reason,
    }


# ── Optimiser ──────────────────────────────────────────────────────────────────

# The four free parameters. The buffer is the one that has no counterpart in any
# other engine here: the rule names "candle low − 1", and whether that ₹1 is
# protection or just a wider stop is exactly the kind of question a sweep
# answers. 0 puts the stop on the low itself.
_CANDLE_GRID = [1, 2, 3, 4]
_RR_GRID     = [1.0, 1.5, 2.0, 3.0]
_BUFFER_GRID = [0.0, 1.0, 2.0]
# Which legs are watched. 'CE & PE' is the rule as written; the single-leg rows
# are there to show whether the pair is actually earning its second trade.
_LEG_GRID    = [('CE & PE', None), ('CE only', 'CE'), ('PE only', 'PE')]

# The parameter sets the walk actually has to simulate. Legs are deliberately
# not among them: a CE's trade does not depend on whether the PE was watched, so
# the three leg rows partition ONE set of finished trades rather than tripling
# the work — and, more to the point, tripling the slices bought.
COMBO_GRID = [(ci, rr, buf) for ci in _CANDLE_GRID
              for rr in _RR_GRID for buf in _BUFFER_GRID]

GRID_SIZE = len(COMBO_GRID) * len(_LEG_GRID)


def _opt_score(s: dict, min_trades: int) -> float:
    """P&L weighted by how reliably it was earned. Same shape as the 2nd-candle
    sweep's, with a lower trade floor: a run is a handful of sessions, not
    years, so demanding five trades would empty the board."""
    pf = s.get('profit_factor') or 0
    pnl = s.get('total_pnl', 0)
    if s.get('total_trades', 0) < min_trades or pf <= 0 or pnl <= 0:
        return -999.0
    return pnl * (pf ** 0.5)


def rank_combos(trades_by_combo, min_trades: int = 3) -> list:
    """Rank finished trades, best-first: one row per (combo × leg filter).

    Takes trades rather than candles because the walk already produced them —
    re-simulating here would mean buying the premiums again, and they are the
    only expensive thing in this strategy.
    """
    results = []
    for (ci, rr, buf), trades in trades_by_combo.items():
        for label, only in _LEG_GRID:
            subset = ([t for t in trades if t['option_type'] == only]
                      if only else list(trades))
            s = _summary(subset)
            if s['total_trades'] < min_trades:
                continue
            results.append({
                'candle_index':  ci,
                'rr_ratio':      rr,
                'sl_buffer':     buf,
                'legs':          label,
                'total_trades':  s['total_trades'],
                'wins':          s['wins'],
                'losses':        s['losses'],
                'win_rate':      s['win_rate'],
                'total_pnl':     s['total_pnl'],
                'profit_factor': s['profit_factor'],
                'max_drawdown':  s['max_drawdown'],
                'avg_win':       s['avg_win'],
                'avg_loss':      s['avg_loss'],
                'score':         round(_opt_score(s, min_trades), 2),
            })
    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def optimise_option_breakout(legs, exit_hour: int = 15, exit_minute: int = 25,
                             min_trades: int = 3) -> list:
    """Sweep the grid over ALREADY FETCHED whole sessions, best-first.

    The route does not use this — it walks the sessions incrementally and feeds
    `rank_combos` directly, which is what makes a sweep cost about what a run
    costs. This is the same sweep against candles already in hand, which is how
    the tests pin the two against each other.
    """
    prepared = prepare_legs(legs)
    if not prepared:
        return []
    cutoff = int(exit_hour) * 60 + int(exit_minute)
    by_combo = {
        combo: [t for t in (_simulate(leg, *combo, cutoff) for leg in prepared)
                if t is not None]
        for combo in COMBO_GRID
    }
    return rank_combos(by_combo, min_trades)


# ── Buying only the part of the session the rule reads ─────────────────────────
#
# A contract-day of 30-second premiums is 25 Breeze requests, and Breeze is
# paced app-wide at 1.5 a second, so the whole-day fetch is ~17 seconds a leg.
# Measured over ten contract-days on 2026-09-08, the rule had its answer inside
# the FIRST 900-second slice nine times out of ten — the run was buying six
# hours of premiums to read fifteen minutes of them.
#
# So the session is bought a slice at a time, and the day's 1-MINUTE bars (one
# cheap request, and Breeze serves two days per request) are used as an index
# into the expensive 1-second ones. That works because a 30-second bar's high
# can never exceed its containing minute's, nor its low go under it:
#
#   * no entry is possible before the first minute whose high reaches the range
#     high, so every slice before it can be skipped unread;
#   * once a position is open, it cannot close before the first minute whose low
#     reaches the stop or whose high reaches the target.
#
# Both are bounds, not guesses. A slice the bound skips contains no bar that
# could have changed the outcome, so the trade this produces is the one the
# whole-day fetch produces — `tests/test_option_breakout_engine.py` asserts that
# equality against a full fetch rather than trusting the argument.

_WINDOW_SECONDS = 900       # icici_data_service._SECOND_WINDOW_SECONDS


def _minute_index(minute_bars):
    """The 1-minute oracle as plain arrays: (minutes-since-midnight, high, low)."""
    df = _frame(minute_bars)
    if df is None or df.empty:
        return None
    return ((df.index.hour * 60 + df.index.minute).to_numpy(),
            df['high'].to_numpy(dtype=float),
            df['low'].to_numpy(dtype=float))


def _first_minute(index, after_min, predicate):
    """The first minute at/after `after_min` whose (high, low) satisfies
    `predicate`, or None. None means the rest of the day is provably quiet."""
    tmins, highs, lows = index
    for i in range(len(tmins)):
        if tmins[i] >= after_min and predicate(highs[i], lows[i]):
            return int(tmins[i])
    return None


def _window_of(minute_of_day, session_open_min=9 * 60 + 15):
    """Which 900-second slice a minute falls in."""
    return max(0, (minute_of_day - session_open_min) * 60 // _WINDOW_SECONDS)


def _minute_of_bar(candle):
    """A broker candle's minute of the day. `date` is a datetime from every
    adapter, but a replayed cache can hand back the string form."""
    stamp = candle['date']
    if hasattr(stamp, 'hour'):
        return stamp.hour * 60 + stamp.minute
    return int(str(stamp)[11:13]) * 60 + int(str(stamp)[14:16])


def walk_session(meta, fetch_window, window_count, minute_bars, combos, cutoff):
    """Simulate one contract-day, buying slices only as the rules need them.

    `combos` is a list of (candle_index, rr_ratio, sl_buffer) — one for a plain
    run, forty-eight for the optimiser's sweep. They share one walk because they
    share the bars: a slice is bought once and every combo reads it, and the
    walk ends when the LAST of them is decided.

    `fetch_window(i)` returns slice i's bars. Returns (trades, windows_fetched),
    where `trades` maps each combo to its trade (or None) and `windows_fetched`
    is what the run actually paid for — the number this mechanism exists to
    shrink.

    Degrades safely: with no usable minute index it still walks slice by slice
    and stops once everything is decided, which on its own was 38 requests of
    250 in the measurement above.
    """
    index = _minute_index(minute_bars)
    candles, fetched = [], []
    trades = {c: None for c in combos}
    pending = list(combos)
    i = 0

    while i < window_count and pending:
        slice_bars = fetch_window(i)
        fetched.append(i)
        if slice_bars:
            candles.extend(slice_bars)
        i += 1
        if not candles:
            continue
        prepared = prepare_legs([{**meta, 'candles': candles}])
        if not prepared:
            continue
        leg, last_min = prepared[0], _minute_of_bar(candles[-1])
        final = i >= window_count

        # The earliest slice any still-undecided combo could need. Combos that
        # finish drop out; the walk stops when none are left.
        next_i, still_pending = window_count, []
        for combo in pending:
            ci, rr, buf = combo
            walk = walk_leg(leg, ci, rr, buf, cutoff, whole_session=final)
            if walk.state == DONE:
                trades[combo] = walk.trade
                continue
            if index is None or walk.range_high is None:
                still_pending.append(combo)      # no oracle — take the next slice
                next_i = min(next_i, i)
                continue
            if walk.state == NO_ENTRY_YET:
                trigger = _first_minute(index, last_min + 1,
                                        lambda h, l, rh=walk.range_high: h >= rh)
                if trigger is None or trigger >= cutoff:
                    continue                 # no bar left can trigger it
            else:                            # OPEN — a position to close
                trigger = _first_minute(
                    index, walk.entry_min,
                    lambda h, l, sl=walk.sl, tp=walk.tp: l <= sl or h >= tp)
                # If nothing closes it, the cut-off or the close does — and both
                # live in the session's last slice.
                trigger = cutoff if trigger is None else min(trigger, cutoff)
            still_pending.append(combo)
            next_i = min(next_i, max(i, _window_of(trigger)))

        pending = still_pending
        if pending:
            i = max(i, next_i)

    return trades, fetched


def walk_one(meta, fetch_window, window_count, minute_bars,
             candle_index, rr, sl_buffer, cutoff):
    """`walk_session` for a single parameter set — what a plain run needs."""
    combo = (candle_index, rr, sl_buffer)
    trades, fetched = walk_session(meta, fetch_window, window_count, minute_bars,
                                   [combo], cutoff)
    return trades[combo], fetched
