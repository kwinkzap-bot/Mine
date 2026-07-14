"""
Monthly Expiry Breakout Backtest Engine.

Rule (any number of trades per monthly-expiry cycle, long and/or short):
  1. Take the High and Low of each monthly F&O expiry day's DAILY candle —
     this is the SL reference for both sides (Low for Long, High for
     Short); it is not itself configurable.
  2. Starting the next trading session, scan 1-hour candles for SIGNAL
     candles (whichever side triggers first, at any point in the cycle)
     that satisfy BOTH of the following simultaneously:
       a) EXPIRY LEVEL — the candle's High/Low range must straddle the
          expiry-day High (Long) / Low (Short), i.e. a genuine touch, not
          just a clean gap-through, AND its CLOSE must clear that level
          (above the High for Long, below the Low for Short).
       b) EMA (ma_timeframe: '1hour' | '1day', default '1hour' — exactly
          one timeframe's 4 EMAs, never a combined set):
            TOUCH — the candle's range must straddle AT LEAST ONE of
                    that timeframe's EMA 20/50/100/200 (a pullback that
                    tested an EMA as support/resistance).
            ALIGN — the candle's CLOSE must clear EVERY one of that
                    timeframe's 4 EMAs, in the trade's direction (all
                    above for Long, all below for Short).
     Whichever side satisfies (a) and (b) first wins that entry:
       - BUY  (Long):  touches + closes above the expiry High, AND
         touches >= 1 selected EMA, AND closes above all 4 of them.
       - SELL (Short): touches + closes below the expiry Low, AND
         touches >= 1 selected EMA, AND closes below all 4 of them (the
         exact mirror).
     The ACTUAL ENTRY is filled at the OPEN of the NEXT hourly candle
     after the signal candle (not the signal candle's own close) — if the
     signal candle is the last bar available in the cycle's window, there
     is no next candle to fill on and that signal is dropped. Once a
     position exits (SL/TARGET), scanning for a fresh signal resumes on
     the very next bar — so a single cycle can contain multiple trades,
     one after another, back to back, with no cap on how many.
     The Daily EMAs use the most recently COMPLETED daily candle (i.e.
     yesterday's close) — today's daily candle is still forming intraday,
     so using it directly would be look-ahead bias. The Hourly EMAs use
     the candle being evaluated itself (same convention as this engine's
     other same-timeframe checks). Until 200 bars of history exist on the
     selected timeframe(s) the EMA(200) is undefined and no entry can
     trigger — this is the same natural warmup gate used elsewhere in
     this app.
  3. SL and Target are both a PERCENTAGE of the entry price (sl_pct /
     target_pct, independent dropdowns — not tied to the expiry level or
     to each other):
       - Long:  sl_level = entry × (1 − sl_pct/100), tp_level = entry × (1 + target_pct/100)
       - Short: sl_level = entry × (1 + sl_pct/100), tp_level = entry × (1 − target_pct/100)
     Exit on whichever comes first (SL checked before Target within a bar):
       - Long:  LOW drops to/through sl_level   → 'SL'
                HIGH rises to/through tp_level  → 'TARGET'
       - Short: HIGH rises to/through sl_level  → 'SL'
                LOW drops to/through tp_level   → 'TARGET'
       - the next month's expiry day arrives — force-closed at the close
         of the last hourly candle on the trading day immediately before
         that next expiry day begins                      → 'EXPIRY'
  4. If the fetched data ends before a next monthly expiry can be found
     (i.e. this is the last cycle in the requested range), an open
     position is closed at the last available hourly candle → 'DATA_END'.
  Long and Short can each be toggled off (enable_long / enable_short) to
  run one-sided. sl_pct (default 1.0) and target_pct (default 3.0) set
  the SL / Target distance from the entry price, each as a percentage.

Monthly expiry-day detection: there is no historical NSE expiry/holiday
calendar in this app, so the expiry day is auto-detected as the last
occurrence of NIFTY's monthly-expiry weekday in each calendar month,
snapped to the latest actual trading day on/before it (this absorbs expiry
holidays without a full holiday list). NSE changed that weekday in 2025:
  - up to and including August 2025 expiry  → last THURSDAY of the month
  - from September 2025 expiry onward       → last TUESDAY of the month
    (NSE circular dated 2025-06-25, effective 2025-09-01; an earlier
    March-2025 plan to move to Monday was announced then deferred and
    never took effect)
Any further NSE expiry-day change needs a new entry in
_EXPIRY_WEEKDAY_REGIMES below.
"""
from datetime import date, timedelta
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# NSE monthly-expiry weekday regime changes: (cutover_date, python_weekday).
# A month's expiry uses the weekday of the LAST cutover whose date is
# <= that month's last calendar day. weekday(): Mon=0 … Tue=1 … Thu=3.
_EXPIRY_WEEKDAY_REGIMES = [
    (date(1900, 1, 1), 3),   # Thursday — the long-standing rule
    (date(2025, 9, 1), 1),   # Tuesday  — NSE circular 2025-06-25, eff. 2025-09-01
]


def _expiry_weekday_for(last_of_month: date) -> int:
    weekday = _EXPIRY_WEEKDAY_REGIMES[0][1]
    for cutover, wd in _EXPIRY_WEEKDAY_REGIMES:
        if cutover <= last_of_month:
            weekday = wd
        else:
            break
    return weekday


# EMA filter: the signal candle must TOUCH at least one EMA of these
# lengths AND its close must clear EVERY one of them, on the single
# selected timeframe ('1hour' or '1day' — never combined).
_MA_PERIODS = [20, 50, 100, 200]


class ExpiryBreakoutEngine:

    def __init__(self, daily_df: pd.DataFrame, hourly_df: pd.DataFrame = None,
                enable_long: bool = True, enable_short: bool = True,
                sl_pct: float = 1.0, target_pct: float = 3.0, ma_timeframe: str = '1hour',
                ema_touch: str = 'touch'):
        self.daily_df  = daily_df.copy()
        self.hourly_df = (hourly_df.copy() if hourly_df is not None
                          else pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close']))
        self.enable_long  = bool(enable_long)
        self.enable_short = bool(enable_short)
        # SL and Target are both a percentage of the entry price, set
        # independently (not tied to the expiry level or to each other):
        #   Long:  sl_level = entry × (1 − sl_pct/100), tp_level = entry × (1 + target_pct/100)
        #   Short: sl_level = entry × (1 + sl_pct/100), tp_level = entry × (1 − target_pct/100)
        self.sl_pct     = float(sl_pct or 1.0)
        self.target_pct = float(target_pct or 3.0)
        # Which EMA columns the signal candle must touch >= 1 of, AND
        # clear (close beyond) ALL of, in the direction of the trade.
        # Only one timeframe at a time (no combined "both" option) —
        # default 1 Hour.
        self.ma_timeframe = ma_timeframe if ma_timeframe in ('1hour', '1day') else '1hour'
        if self.ma_timeframe == '1day':
            self._ma_cols = [f'd_ema{n}' for n in _MA_PERIODS]
        else:
            self._ma_cols = [f'h_ema{n}' for n in _MA_PERIODS]
        # scan_hl_signals() EMA gate: 'touch' (default) requires the candle
        # to touch >= 1 selected EMA; 'not_touch' requires it to touch NONE
        # of them; 'both' applies no EMA condition at all (every
        # touch-and-clear-level signal, regardless of EMA touch status).
        self.ema_touch = ema_touch if ema_touch in ('touch', 'not_touch', 'both') else 'touch'
        self._prepare()

    # ── Data prep ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if 'date' in df.columns:
            dt = pd.to_datetime(df['date'])
        elif 'datetime' in df.columns:
            dt = pd.to_datetime(df['datetime'])
        else:
            dt = pd.to_datetime(df.index)
        df['datetime'] = dt
        df = df.sort_values('datetime').drop_duplicates(subset=['datetime'])
        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize(
                'Asia/Kolkata', ambiguous='infer', nonexistent='shift_forward')
        else:
            df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata')
        return df.reset_index(drop=True)

    def _prepare(self):
        self.daily_df  = self._normalise(self.daily_df)
        self.hourly_df = self._normalise(self.hourly_df)
        self.daily_df['day'] = self.daily_df['datetime'].dt.date
        self.hourly_df['day'] = self.hourly_df['datetime'].dt.date

        # Daily-timeframe EMAs, lagged by one day: today's daily candle is
        # still forming intraday, so an entry taken today must reference
        # the most recently COMPLETED daily close (yesterday's) — using
        # today's own still-forming daily bar would be look-ahead bias.
        d_ma_cols = [f'd_ema{n}' for n in _MA_PERIODS]
        for n, col in zip(_MA_PERIODS, d_ma_cols):
            self.daily_df[col] = (self.daily_df['close']
                                   .ewm(span=n, adjust=False, min_periods=n).mean().shift(1))

        # Carry those lagged daily EMAs onto every hourly bar of the same
        # calendar day ('day' is a unique key in daily_df, so this is a
        # plain lookup — no row duplication).
        if not self.hourly_df.empty:
            self.hourly_df = self.hourly_df.merge(
                self.daily_df[['day'] + d_ma_cols], on='day', how='left'
            ).sort_values('datetime').reset_index(drop=True)
        else:
            for col in d_ma_cols:
                self.hourly_df[col] = pd.Series(dtype=float)

        # Hourly-timeframe EMAs, same-bar (no lag) — the candle being
        # evaluated for a breakout is itself the completed hourly close.
        for n in _MA_PERIODS:
            self.hourly_df[f'h_ema{n}'] = (self.hourly_df['close']
                                            .ewm(span=n, adjust=False, min_periods=n).mean())

    # ── Monthly expiry-day detection ────────────────────────────────────────

    def _monthly_expiry_days(self):
        """Sorted list of `date` objects: the auto-detected NIFTY monthly
        F&O expiry day for each calendar month present in daily_df (last
        Thursday through Aug-2025, last Tuesday from Sep-2025 onward)."""
        days = sorted(self.daily_df['day'].unique())
        if not days:
            return []
        overall_last = days[-1]

        by_month = {}
        for d in days:
            by_month.setdefault((d.year, d.month), []).append(d)

        expiries = []
        for (y, m), month_days in by_month.items():
            if m == 12:
                last_of_month = date(y, 12, 31)
            else:
                last_of_month = date(y, m + 1, 1) - timedelta(days=1)
            target_wd = _expiry_weekday_for(last_of_month)
            offset = (last_of_month.weekday() - target_wd) % 7
            target_date = last_of_month - timedelta(days=offset)

            # If the fetched range ends before this month's expiry would
            # actually occur, we can't confirm it happened — skip the month
            # rather than mistakenly treating the range's last bar as expiry.
            if target_date > overall_last:
                continue

            # Snap to the closest actual trading day on/before the target
            # weekday (absorbs an expiry-day holiday without a holiday list).
            candidates = [d for d in month_days if d <= target_date]
            if candidates:
                expiries.append(max(candidates))

        return sorted(expiries)

    def scan_hl_signals(self):
        """All historical touch-then-close-beyond-expiry-High/Low events
        across every monthly cycle in the data. The candle's CLOSE must
        ALSO clear EVERY selected EMA 20/50/100/200 in the trade's
        direction — above all of them for BUY, below all of them for
        SELL (unconditional, regardless of ema_touch). ema_touch
        ADDITIONALLY gates on whether the candle touched those EMAs:
        'touch' (default) requires touching at least one of them (a
        pullback that tested an EMA as support/resistance before closing
        through); 'not_touch' requires touching NONE of them (closed
        beyond the whole stack without ever testing it); 'both' applies
        no touch condition, only the close-beyond-all-EMA requirement.
        No SL/Target, no trade simulation, no one-per-cycle limit — just
        every signal hit, used by the Monthly Expiry Breakout filter."""
        expiries = self._monthly_expiry_days()
        if not expiries or self.hourly_df.empty:
            return []
        daily_by_day = self.daily_df.set_index('day')
        hdf = self.hourly_df
        signals = []

        for i, expiry_day in enumerate(expiries):
            row = daily_by_day.loc[expiry_day]
            if isinstance(row, pd.DataFrame):   # duplicate-day guard
                row = row.iloc[-1]
            exp_high = float(row['high'])
            exp_low  = float(row['low'])

            next_expiry_day = expiries[i + 1] if i + 1 < len(expiries) else None
            window = hdf[hdf['day'] > expiry_day]
            if next_expiry_day is not None:
                window = window[window['day'] < next_expiry_day]

            for _, c in window.iterrows():
                # Warmup gate: every selected EMA must exist — the
                # close-beyond-all-EMA requirement needs real values, and
                # (for 'not_touch') a NaN EMA would wrongly pass as
                # "never touched".
                if any(pd.isna(c[col]) for col in self._ma_cols):
                    continue
                if self.ema_touch != 'both':
                    touched = _touches_any_ma(c, self._ma_cols)
                    if self.ema_touch == 'touch' and not touched:
                        continue
                    if self.ema_touch == 'not_touch' and touched:
                        continue
                if (_touches_and_clears_level(c, exp_high, above=True)
                        and _clears_all_mas(c, self._ma_cols, above=True)):
                    direction = 'BUY'
                elif (_touches_and_clears_level(c, exp_low, above=False)
                        and _clears_all_mas(c, self._ma_cols, above=False)):
                    direction = 'SELL'
                else:
                    continue
                signals.append({
                    'time':        c['datetime'].isoformat(),
                    'direction':   direction,
                    'price':       round(float(c['close']), 2),
                    'expiry_high': round(exp_high, 2),
                    'expiry_low':  round(exp_low, 2),
                    'expiry_date': expiry_day.isoformat(),
                })

        return signals

    def expiry_levels(self):
        """List of {expiry_date, high, low} for every detected monthly
        expiry day — used by the "Expiry Levels" preview (no hourly data
        or entry/exit simulation needed)."""
        expiries = self._monthly_expiry_days()
        if not expiries:
            return []
        daily_by_day = self.daily_df.set_index('day')
        out = []
        for d in expiries:
            row = daily_by_day.loc[d]
            if isinstance(row, pd.DataFrame):   # duplicate-day guard
                row = row.iloc[-1]
            out.append({
                'expiry_date': d.isoformat(),
                'high': round(float(row['high']), 2),
                'low':  round(float(row['low']), 2),
            })
        return out

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        expiries = self._monthly_expiry_days()
        if not expiries or self.hourly_df.empty:
            return [], _summarise([])

        daily_by_day = self.daily_df.set_index('day')
        hdf = self.hourly_df
        trades = []

        for i, expiry_day in enumerate(expiries):
            row = daily_by_day.loc[expiry_day]
            if isinstance(row, pd.DataFrame):   # duplicate-day guard
                row = row.iloc[-1]
            exp_high = float(row['high'])
            exp_low  = float(row['low'])

            next_expiry_day = expiries[i + 1] if i + 1 < len(expiries) else None

            window = hdf[hdf['day'] > expiry_day]
            if next_expiry_day is not None:
                window = window[window['day'] < next_expiry_day]
            if window.empty:
                continue

            window = window.reset_index(drop=True)

            entry       = None
            pending_dir = None   # signal fired, awaiting fill at the next candle's open
            for pos in range(len(window)):
                c = window.iloc[pos]
                if entry is None:
                    if pending_dir is not None:
                        # Fill on THIS candle's open — the bar right after the signal.
                        entry_price = float(c['open'])
                        if pending_dir == 'Long':
                            entry = {'entry_time': c['datetime'], 'entry_price': entry_price,
                                     'direction': 'Long',
                                     'sl_level': entry_price * (1 - self.sl_pct / 100),
                                     'tp_level': entry_price * (1 + self.target_pct / 100)}
                        else:
                            entry = {'entry_time': c['datetime'], 'entry_price': entry_price,
                                     'direction': 'Short',
                                     'sl_level': entry_price * (1 + self.sl_pct / 100),
                                     'tp_level': entry_price * (1 - self.target_pct / 100)}
                        pending_dir = None
                        continue
                    if (self.enable_long
                            and _touches_and_clears_level(c, exp_high, above=True)
                            and _touches_any_ma(c, self._ma_cols)
                            and _clears_all_mas(c, self._ma_cols, above=True)):
                        pending_dir = 'Long'
                    elif (self.enable_short
                            and _touches_and_clears_level(c, exp_low, above=False)
                            and _touches_any_ma(c, self._ma_cols)
                            and _clears_all_mas(c, self._ma_cols, above=False)):
                        pending_dir = 'Short'
                    continue
                # In position — SL checked before Target within the same bar
                # (conservative), skipping the fill bar via the `continue` above.
                # On exit, reset to `None` so scanning for a fresh signal
                # resumes on the very next bar — a cycle can hold any number
                # of back-to-back trades, not just one.
                sl_level = entry['sl_level']
                tp_level = entry['tp_level']
                exit_reason = exit_price = None
                if entry['direction'] == 'Long':
                    if c['low'] <= sl_level:
                        exit_reason, exit_price = 'SL', sl_level
                    elif c['high'] >= tp_level:
                        exit_reason, exit_price = 'TARGET', tp_level
                else:
                    if c['high'] >= sl_level:
                        exit_reason, exit_price = 'SL', sl_level
                    elif c['low'] <= tp_level:
                        exit_reason, exit_price = 'TARGET', tp_level
                if exit_reason is not None:
                    trades.append(_make_trade(
                        entry, expiry_day, next_expiry_day, exp_high, exp_low,
                        c['datetime'], exit_price, exit_reason,
                    ))
                    entry = None

            if entry is not None:
                # Still in position when the cycle's window runs out —
                # force-close at the last hourly candle (the trading day
                # right before the next expiry, or the end of the data).
                exit_row    = window.iloc[-1]
                exit_reason = 'EXPIRY' if next_expiry_day is not None else 'DATA_END'
                exit_price  = float(exit_row['close'])
                trades.append(_make_trade(
                    entry, expiry_day, next_expiry_day, exp_high, exp_low,
                    exit_row['datetime'], exit_price, exit_reason,
                ))

        return trades, _summarise(trades)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _touches_and_clears_level(c, level: float, above: bool) -> bool:
    """True only if the candle's range straddles `level` (a genuine touch,
    not a clean gap-through) AND its close clears it in the given
    direction. Used for the expiry-day High (Long) / Low (Short) itself,
    the same touch-then-close pattern applied to the moving averages."""
    if not (c['low'] <= level <= c['high']):
        return False
    return bool(c['close'] > level) if above else bool(c['close'] < level)


def _touches_any_ma(c, ma_cols) -> bool:
    """True if the candle's High/Low range straddles (low <= EMA <= high)
    at least one of the given EMA columns. A NaN EMA (EMA200 warmup not
    yet complete) never satisfies the comparison, so this naturally
    requires enough history before any entry can trigger."""
    lo, hi = c['low'], c['high']
    return any(lo <= c[col] <= hi for col in ma_cols)


def _clears_all_mas(c, ma_cols, above: bool) -> bool:
    """True only if the candle's CLOSE clears every EMA in ma_cols — above
    all of them (Long) or below all of them (Short). A NaN EMA (EMA200
    warmup not yet complete) never satisfies the comparison, so this
    naturally blocks entries until enough history exists."""
    close = c['close']
    if above:
        return all(close > c[col] for col in ma_cols)
    return all(close < c[col] for col in ma_cols)


def _make_trade(entry, expiry_day, next_expiry_day, exp_high, exp_low,
                exit_dt, exit_price, exit_reason):
    entry_price = entry['entry_price']
    direction   = entry['direction']
    pnl = (exit_price - entry_price) if direction == 'Long' else (entry_price - exit_price)
    return {
        'entry_time':       entry['entry_time'].isoformat(),
        'exit_time':        exit_dt.isoformat(),
        'type':             direction,
        'entry_price':      round(entry_price, 2),
        'exit_price':       round(exit_price, 2),
        'sl_price':         round(entry['sl_level'], 2),
        'target_price':     round(entry['tp_level'], 2),
        'pnl':              round(pnl, 2),
        'result':           'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
        'exit_reason':      exit_reason,
        'expiry_date':      expiry_day.isoformat(),
        'next_expiry_date': next_expiry_day.isoformat() if next_expiry_day else None,
        'expiry_high':      round(exp_high, 2),
        'expiry_low':       round(exp_low, 2),
    }


def _summarise(trades):
    if not trades:
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0.0, 'total_pnl': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'max_drawdown': 0.0,
            'sl_exits': 0, 'target_exits': 0, 'expiry_exits': 0, 'data_end_exits': 0,
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
        'sl_exits':          sum(1 for t in trades if t['exit_reason'] == 'SL'),
        'target_exits':      sum(1 for t in trades if t['exit_reason'] == 'TARGET'),
        'expiry_exits':      sum(1 for t in trades if t['exit_reason'] == 'EXPIRY'),
        'data_end_exits':    sum(1 for t in trades if t['exit_reason'] == 'DATA_END'),
    }
