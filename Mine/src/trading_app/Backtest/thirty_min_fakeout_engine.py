"""
30-Minute Opening Fakeout Breakdown/Breakout Backtest Engine.

Every trading day, using 30-minute candles aligned to the 09:15 session
open (candle 1 = 09:15-09:44, candle 2 = 09:45-10:14, candle 3 =
10:15-10:44):

  SHORT setup (breakdown):
    1. Candle 1 is green (close > open).
    2. Candle 2 is green AND Candle 2's High > Candle 1's High AND
       Candle 2's CLOSE is above Candle 1's HIGH (not just above Candle
       1's High intrabar — it closes there).
    3. Candle 3's High crosses ABOVE Candle 2's High, but Candle 3's
       CLOSE is back below Candle 2's High — a fakeout rejection candle.
    4. Candle 3's CLOSE must not be below the 50% level (midpoint of
       High/Low) of Candle 2 — too deep a close invalidates the setup.
    5. Candle 3's lower wick must not be bigger than its upper wick
       (lower_wick <= upper_wick) — a SELL entry needs a rejection
       candle with a dominant upper wick, not a hammer-like tail.
    6. If Candle 3 is itself GREEN (against the SELL direction), its
       body must not exceed 1/4 of its High-Low range — a small,
       indecisive green body is fine, a decisively green Candle 3
       contradicts the breakdown.
    7. Candle 3's Low, minus a 0.05% buffer, becomes the breakdown
       trigger — price must clear the Low by that much, not just touch
       it. The first 1-minute bar (from 10:45 onward) whose Low trades
       through that trigger fires the SELL entry — fills at the
       trigger level, or at the bar's open if it gaps straight through.
    SL     = Candle 3's High (the real High, no buffer applied)
    Target = the day's session Low — only knowable in hindsight, this
             is a backtest-only "how far did the day actually fall"
             target, not a level that could be set at entry time live.
    Exit on whichever comes first (SL checked before Target within a
    bar); force-closed at the cutoff time (default 15:18 IST) if
    neither is hit by then.

  LONG setup (breakout) — the exact mirror:
    Candle 1 red. Candle 2 red AND Candle 2's Low < Candle 1's Low AND
    Candle 2's CLOSE is below Candle 1's LOW (closes there, not just
    wicks below it). Candle 3's Low crosses below Candle 2's Low but
    closes back above Candle 2's Low, and
    Candle 3's CLOSE must not be above the 50%
    level of Candle 2. Candle 3's upper wick must not be bigger than
    its lower wick (upper_wick <= lower_wick). If Candle 3 is itself
    RED (against the BUY direction), its body must not exceed 1/4 of
    its High-Low range. Candle 3's High, plus a 0.05% buffer, is the
    breakout trigger -> BUY. SL = Candle 3's Low (the real Low, no
    buffer), Target = the day's session High.

Only one setup (and one trade) per day. No new entries at/after the
cutoff time.
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

_SESSION_START_MIN = 9 * 60 + 15   # 09:15
_THIRD_CANDLE_END_MIN = _SESSION_START_MIN + 90  # 10:45 — watch window starts here

# Entry trigger buffer: the breakdown/breakout must clear Candle 3's Low/High
# by this much (not just touch it) before the entry fires — filters a bar
# that merely wicks the exact level without a real break.
_ENTRY_TRIGGER_BUFFER_PCT = 0.0005  # 0.05%


class ThirtyMinFakeoutEngine:

    def __init__(
        self,
        df: pd.DataFrame,
        exit_hour: int = 15,
        exit_minute: int = 18,
        enable_long: bool = True,
        enable_short: bool = True,
        use_entry_buffer: bool = True,
        use_body_filter: bool = True,
        use_c2_close_filter: bool = True,
    ):
        self.df = df.copy()
        self.exit_hour = exit_hour
        self.exit_minute = exit_minute
        self.enable_long = enable_long
        self.enable_short = enable_short
        # Optional filters, all on by default:
        #   use_entry_buffer   — require price to clear Candle 3's Low/High
        #     by _ENTRY_TRIGGER_BUFFER_PCT before the entry fires, instead
        #     of the raw (unbuffered) Low/High.
        #   use_body_filter    — reject a Candle 3 that's a decisive
        #     against-direction color (green for SELL, red for BUY) with a
        #     body bigger than 1/4 of its High-Low range.
        #   use_c2_close_filter — require Candle 2's CLOSE beyond Candle 1's
        #     CLOSE (above it for SELL, below it for BUY) — Candle 2 must
        #     be closing further into the move, not just have a wider
        #     High/Low than Candle 1.
        self.use_entry_buffer = use_entry_buffer
        self.use_body_filter = use_body_filter
        self.use_c2_close_filter = use_c2_close_filter
        self.df = _prepare_df(self.df)

    def run(self):
        if self.df.empty:
            return [], _summary([])

        cutoff = self.exit_hour * 60 + self.exit_minute
        trades = []
        for day_df, thirty in iter_days_with_candles(self.df):
            t = _run_day(day_df, thirty, cutoff, self.enable_long, self.enable_short,
                         self.use_entry_buffer, self.use_body_filter, self.use_c2_close_filter)
            if t is not None:
                trades.append(t)

        return trades, _summary(trades)


# ── Single-day simulation ──────────────────────────────────────────────────────

def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw OHLC dataframe: datetime index, sorted, deduped,
    lower-cased columns. Split out from the engine so a caller sweeping
    many filter combos against the same symbol (e.g. the "Find Best
    Params" grid search) can prepare once and reuse via
    iter_days_with_candles instead of re-preparing per combo."""
    if not pd.api.types.is_datetime64_any_dtype(df.index):
        if 'date' in df.columns:
            df = df.set_index('date')
        elif 'datetime' in df.columns:
            df = df.set_index('datetime')
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='last')]
    df.columns = [c.lower() for c in df.columns]
    return df


def iter_days_with_candles(df: pd.DataFrame):
    """Yield (day_df, thirty) for every trading day in an already-prepared
    df (see _prepare_df) that has all of Candle 1/2/3 present. Resampling
    to 30-min candles is the expensive, filter-combo-invariant part of a
    day's pattern check — computing it here once and feeding the same
    (day_df, thirty) pairs into _run_day for several different filter
    combos (as the optimiser route does) avoids redoing it once per combo."""
    for _, day_df in df.groupby(df.index.normalize()):
        day_df = day_df.sort_index()
        thirty = _resample_30min(day_df)
        if all(b in thirty.index for b in (0, 1, 2)):
            yield day_df, thirty


def _resample_30min(day_df: pd.DataFrame) -> pd.DataFrame:
    """30-min OHLC candles aligned to the 09:15 session open (not epoch-
    aligned floor(), which would misalign 30-min buckets against a 09:15
    start)."""
    idx = day_df.index
    minute_of_day = idx.hour * 60 + idx.minute - _SESSION_START_MIN
    bucket = minute_of_day // 30
    grouped = day_df.groupby(bucket).agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'),
    )
    return grouped


def _detect_setup(c1, c2, c3, enable_long: bool, enable_short: bool,
                   use_entry_buffer: bool = True, use_body_filter: bool = True,
                   use_c2_close_filter: bool = True):
    """The pattern check itself, isolated from day-simulation — c1/c2/c3 are
    each a row (dict-like with open/high/low/close) for that day's first
    three 30-min candles. Returns {'direction': 'short'|'long', 'trigger':
    float, 'sl_level': float} or None. Shared by the backtest's _run_day
    and the live algo (tmf_algo.py) so live entries can never silently
    drift from what the backtest considers a valid setup."""
    direction = None
    trigger = sl_level = None

    c3_upper_wick = c3['high'] - max(c3['open'], c3['close'])
    c3_lower_wick = min(c3['open'], c3['close']) - c3['low']
    c3_body  = abs(c3['close'] - c3['open'])
    c3_range = c3['high'] - c3['low']
    # An "against-direction" colored Candle 3 (green for a SELL, red for a
    # BUY) is only acceptable if it's small-bodied/indecisive — a decisive
    # opposite-colored candle with a big body contradicts the fakeout.
    # Disabled (use_body_filter=False) simply always passes.
    c3_against_short_body_ok = (not use_body_filter) or not (c3['close'] > c3['open'] and c3_body * 4 > c3_range)
    c3_against_long_body_ok  = (not use_body_filter) or not (c3['close'] < c3['open'] and c3_body * 4 > c3_range)
    # Candle 2 must CLOSE beyond Candle 1's High/Low (not just wick past it,
    # which the c2.high>c1.high / c2.low<c1.low gates already require) —
    # above Candle 1's High for a SELL, below Candle 1's Low for a BUY.
    # Disabled (use_c2_close_filter=False) simply always passes.
    c2_close_short_ok = (not use_c2_close_filter) or (c2['close'] > c1['high'])
    c2_close_long_ok  = (not use_c2_close_filter) or (c2['close'] < c1['low'])

    entry_buffer = _ENTRY_TRIGGER_BUFFER_PCT if use_entry_buffer else 0.0

    if (enable_short and c1['close'] > c1['open'] and c2['close'] > c2['open']
            and c2['high'] > c1['high'] and c2_close_short_ok):
        c2_mid = (c2['high'] + c2['low']) / 2
        if (c3['high'] > c2['high'] and c3['close'] < c2['high'] and c3['close'] >= c2_mid
                and c3_lower_wick <= c3_upper_wick and c3_against_short_body_ok):
            direction = 'short'
            trigger  = c3['low'] * (1 - entry_buffer)
            sl_level = c3['high']

    if (direction is None and enable_long and c1['close'] < c1['open'] and c2['close'] < c2['open']
            and c2['low'] < c1['low'] and c2_close_long_ok):
        c2_mid = (c2['high'] + c2['low']) / 2
        if (c3['low'] < c2['low'] and c3['close'] > c2['low'] and c3['close'] <= c2_mid
                and c3_upper_wick <= c3_lower_wick and c3_against_long_body_ok):
            direction = 'long'
            trigger  = c3['high'] * (1 + entry_buffer)
            sl_level = c3['low']

    if direction is None:
        return None
    return {'direction': direction, 'trigger': trigger, 'sl_level': sl_level}


def _run_day(day_df: pd.DataFrame, thirty: pd.DataFrame, cutoff: int, enable_long: bool, enable_short: bool,
             use_entry_buffer: bool = True, use_body_filter: bool = True, use_c2_close_filter: bool = True):
    """day_df: one day's 1-min bars, already sorted. thirty: that same
    day's 30-min candles (from iter_days_with_candles) — resampling
    happens once per day upstream and is reused across combos, not
    redone here."""
    c1, c2, c3 = thirty.loc[0], thirty.loc[1], thirty.loc[2]

    setup_hit = _detect_setup(c1, c2, c3, enable_long, enable_short,
                               use_entry_buffer, use_body_filter, use_c2_close_filter)
    if setup_hit is None:
        return None
    direction = setup_hit['direction']
    trigger   = setup_hit['trigger']
    sl_level  = setup_hit['sl_level']

    time_mins = day_df.index.hour * 60 + day_df.index.minute

    # Session Low/High (the target level) over the tradable window up to
    # the cutoff — a level only knowable in hindsight.
    session_df = day_df[time_mins < cutoff]
    if session_df.empty:
        return None
    target_level = session_df['low'].min() if direction == 'short' else session_df['high'].max()

    watch = day_df[time_mins >= _THIRD_CANDLE_END_MIN]
    if watch.empty:
        return None
    watch_time_mins = (watch.index.hour * 60 + watch.index.minute).to_numpy()

    # ── Find the breakout entry ──
    # If price reaches the SL level before ever triggering the entry, the
    # setup is invalidated for the day — entering at that point would mean
    # entering with the stop already hit. SL is checked before the trigger
    # within the same bar (conservative, same convention used post-entry).
    entry = None
    entry_ts = entry_pos = None
    for pos in range(len(watch)):
        tmin = watch_time_mins[pos]
        if tmin >= cutoff:
            break
        bar = watch.iloc[pos]
        o, h, l = bar['open'], bar['high'], bar['low']
        if direction == 'short':
            if h >= sl_level:
                return None
            if l <= trigger:
                entry = o if o < trigger else trigger
                entry_ts, entry_pos = watch.index[pos], pos
                break
        else:
            if l <= sl_level:
                return None
            if h >= trigger:
                entry = o if o > trigger else trigger
                entry_ts, entry_pos = watch.index[pos], pos
                break

    if entry is None:
        return None

    setup = {
        'direction': direction, 'entry': entry, 'entry_ts': entry_ts,
        'sl_level': sl_level, 'target_level': target_level,
        'c1': c1, 'c2': c2, 'c3': c3,
    }

    # ── Manage the position from the entry bar onward ──
    # Exits as soon as any bar's High/Low crosses the SL/Target level
    # (equivalent to a 30-min candle's High/Low touching it, since a
    # 30-min candle's High/Low is just the max/min of its constituent
    # 1-min bars — no extra aggregation needed to get that).
    for pos in range(entry_pos, len(watch)):
        tmin = watch_time_mins[pos]
        bar = watch.iloc[pos]
        o, h, l = bar['open'], bar['high'], bar['low']

        if tmin >= cutoff and pos > entry_pos:
            exit_price = o
            pnl = (entry - exit_price) if direction == 'short' else (exit_price - entry)
            return _make_trade(setup, watch.index[pos], exit_price, pnl, 'Time Exit')

        if direction == 'short':
            if h >= sl_level:
                return _make_trade(setup, watch.index[pos], sl_level, entry - sl_level, 'SL Hit')
            if l <= target_level:
                return _make_trade(setup, watch.index[pos], target_level, entry - target_level, 'Target Hit')
        else:
            if l <= sl_level:
                return _make_trade(setup, watch.index[pos], sl_level, sl_level - entry, 'SL Hit')
            if h >= target_level:
                return _make_trade(setup, watch.index[pos], target_level, target_level - entry, 'Target Hit')

    # Data ran out before the cutoff/SL/Target — close at the last available bar.
    last_ts = watch.index[-1]
    last_close = watch.iloc[-1]['close']
    pnl = (entry - last_close) if direction == 'short' else (last_close - entry)
    return _make_trade(setup, last_ts, last_close, pnl, 'EOD Exit')


# ── Helpers ────────────────────────────────────────────────────────────────────

def summarize_trades(trades):
    """Public entry point for aggregating trades collected from multiple
    separate engine runs (one per symbol, in a universe scan) into a
    single summary — the same stats run() computes for one symbol."""
    return _summary(trades)


def _make_trade(setup, exit_ts, exit_price, pnl, exit_reason):
    direction = setup['direction']
    entry = setup['entry']
    return {
        'entry_time':    setup['entry_ts'].isoformat(),
        'exit_time':     exit_ts.isoformat(),
        'type':          'Short' if direction == 'short' else 'Long',
        'entry_price':   round(entry, 2),
        'exit_price':    round(exit_price, 2),
        'sl_price':      round(setup['sl_level'], 2),
        'target_price':  round(setup['target_level'], 2),
        'pnl':           round(pnl, 2),
        'result':        'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason':   exit_reason,
        'c1_high': round(float(setup['c1']['high']), 2), 'c1_low': round(float(setup['c1']['low']), 2),
        'c2_high': round(float(setup['c2']['high']), 2), 'c2_low': round(float(setup['c2']['low']), 2),
        'c3_high': round(float(setup['c3']['high']), 2), 'c3_low': round(float(setup['c3']['low']), 2),
    }


def _summary(trades):
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'total_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0,
        }

    wins   = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']

    total_pnl    = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss   = abs(sum(t['pnl'] for t in losses))

    avg_win  = gross_profit / len(wins)   if wins   else 0.0
    avg_loss = gross_loss   / len(losses) if losses else 0.0
    pf       = gross_profit / gross_loss  if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    running, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        running += t['pnl']
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        'total_trades':  len(trades),
        'wins':          len(wins),
        'losses':        len(losses),
        'win_rate':      round(len(wins) / len(trades) * 100, 1),
        'total_pnl':     round(total_pnl, 2),
        'avg_win':       round(avg_win, 2),
        'avg_loss':      round(avg_loss, 2),
        'profit_factor': round(pf, 2),
        'max_drawdown':  round(max_dd, 2),
    }
