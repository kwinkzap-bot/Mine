"""
Monthly Expiry Breakout Backtest Engine.

Rule (one trade per monthly-expiry cycle, long and/or short):
  1. Take the High and Low of each monthly F&O expiry day's DAILY candle —
     this is the SL reference for both sides (Low for Long, High for
     Short); it is not itself configurable.
  2. Starting the next trading session, scan 1-hour candles for the FIRST
     breakout (whichever side triggers first). The SAME touch-then-close
     pattern is required at TWO separate levels — the expiry-day High/Low
     itself, and the moving averages:
       a) EXPIRY LEVEL — the candle's High/Low range must straddle the
          expiry-day High (Long) / Low (Short), i.e. a genuine touch, not
          just a clean gap-through, AND its CLOSE must clear that level
          (above the High for Long, below the Low for Short).
       b) MOVING AVERAGES (ma_timeframe: '1hour' | '1day' | 'both',
          default 'both' — the union of both sets, 8 MAs):
            TOUCH — the candle's range must straddle AT LEAST ONE
                    selected SMA 20/50/100/200 (a pullback that tested
                    an MA as support/resistance).
            ALIGN — the candle's CLOSE must clear EVERY selected MA, in
                    the trade's direction (all above for Long, all below
                    for Short) — confirming the candle closed back
                    through the whole MA stack after the touch.
     ALL of (a) and (b) must hold simultaneously on the same candle:
       - BUY  (Long):  touches + closes above the expiry High, AND
         touches >= 1 selected MA, AND closes above all of them. Fill at
         the breakout candle's close. Target = entry + rr_ratio × risk,
         risk = entry − expiry Low.
       - SELL (Short): touches + closes below the expiry Low, AND
         touches >= 1 selected MA, AND closes below all of them (the
         exact mirror). Fill at the breakout candle's close. Target =
         entry − rr_ratio × risk, risk = expiry High − entry.
     The Daily SMAs use the most recently COMPLETED daily candle (i.e.
     yesterday's close) — today's daily candle is still forming intraday,
     so using it directly would be look-ahead bias. The Hourly SMAs use
     the candle being evaluated itself (same convention as this engine's
     other same-timeframe checks). Until 200 bars of history exist on the
     selected timeframe(s) the SMA(200) is undefined and no entry can
     trigger — this is the same natural warmup gate used elsewhere in
     this app.
  3. Exit on whichever comes first (SL checked before Target within a bar):
       - Long:  LOW drops to/through the expiry-day Low   → 'LOW_BREACH'
                HIGH rises to/through the target level     → 'TARGET'
       - Short: HIGH rises to/through the expiry-day High → 'HIGH_BREACH'
                LOW drops to/through the target level      → 'TARGET'
       - the next month's expiry day arrives — force-closed at the close
         of the last hourly candle on the trading day immediately before
         that next expiry day begins                      → 'EXPIRY'
  4. If the fetched data ends before a next monthly expiry can be found
     (i.e. this is the last cycle in the requested range), an open
     position is closed at the last available hourly candle → 'DATA_END'.
  Long and Short can each be toggled off (enable_long / enable_short) to
  run one-sided. rr_ratio (default 3.0, i.e. 1:3) sets how far the target
  sits beyond the risk defined by the expiry-day SL level.

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


# Moving-average filter: the entry candle must TOUCH at least one SMA of
# these lengths AND its close must clear EVERY one of them, on the
# selected timeframe(s).
_MA_PERIODS = [20, 50, 100, 200]


class ExpiryBreakoutEngine:

    def __init__(self, daily_df: pd.DataFrame, hourly_df: pd.DataFrame = None,
                enable_long: bool = True, enable_short: bool = True,
                rr_ratio: float = 3.0, ma_timeframe: str = 'both'):
        self.daily_df  = daily_df.copy()
        self.hourly_df = (hourly_df.copy() if hourly_df is not None
                          else pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close']))
        self.enable_long  = bool(enable_long)
        self.enable_short = bool(enable_short)
        # Target = entry ± rr_ratio × risk, where risk is the distance from
        # entry to the expiry-day stop level (Low for Long, High for Short)
        # — the SL itself is not configurable, only how far the target sits
        # beyond it.
        self.rr_ratio = float(rr_ratio or 3.0)
        # Which MA columns the entry candle must touch >= 1 of, AND clear
        # (close beyond) ALL of, in the direction of the trade.
        self.ma_timeframe = ma_timeframe if ma_timeframe in ('1hour', '1day', 'both') else 'both'
        if self.ma_timeframe == '1hour':
            self._ma_cols = [f'h_sma{n}' for n in _MA_PERIODS]
        elif self.ma_timeframe == '1day':
            self._ma_cols = [f'd_sma{n}' for n in _MA_PERIODS]
        else:
            self._ma_cols = [f'h_sma{n}' for n in _MA_PERIODS] + [f'd_sma{n}' for n in _MA_PERIODS]
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

        # Daily-timeframe SMAs, lagged by one day: today's daily candle is
        # still forming intraday, so an entry taken today must reference
        # the most recently COMPLETED daily close (yesterday's) — using
        # today's own still-forming daily bar would be look-ahead bias.
        d_ma_cols = [f'd_sma{n}' for n in _MA_PERIODS]
        for n, col in zip(_MA_PERIODS, d_ma_cols):
            self.daily_df[col] = self.daily_df['close'].rolling(n).mean().shift(1)

        # Carry those lagged daily SMAs onto every hourly bar of the same
        # calendar day ('day' is a unique key in daily_df, so this is a
        # plain lookup — no row duplication).
        if not self.hourly_df.empty:
            self.hourly_df = self.hourly_df.merge(
                self.daily_df[['day'] + d_ma_cols], on='day', how='left'
            ).sort_values('datetime').reset_index(drop=True)
        else:
            for col in d_ma_cols:
                self.hourly_df[col] = pd.Series(dtype=float)

        # Hourly-timeframe SMAs, same-bar (no lag) — the candle being
        # evaluated for a breakout is itself the completed hourly close.
        for n in _MA_PERIODS:
            self.hourly_df[f'h_sma{n}'] = self.hourly_df['close'].rolling(n).mean()

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

            entry       = None
            exit_row    = None
            exit_reason = None
            exit_price  = None
            for _, c in window.iterrows():
                if entry is None:
                    if (self.enable_long
                            and _touches_and_clears_level(c, exp_high, above=True)
                            and _touches_any_ma(c, self._ma_cols)
                            and _clears_all_mas(c, self._ma_cols, above=True)):
                        entry_price = float(c['close'])
                        risk        = entry_price - exp_low
                        entry = {'entry_time': c['datetime'], 'entry_price': entry_price,
                                 'direction': 'Long', 'sl_level': exp_low,
                                 'tp_level': entry_price + self.rr_ratio * risk}
                    elif (self.enable_short
                            and _touches_and_clears_level(c, exp_low, above=False)
                            and _touches_any_ma(c, self._ma_cols)
                            and _clears_all_mas(c, self._ma_cols, above=False)):
                        entry_price = float(c['close'])
                        risk        = exp_high - entry_price
                        entry = {'entry_time': c['datetime'], 'entry_price': entry_price,
                                 'direction': 'Short', 'sl_level': exp_high,
                                 'tp_level': entry_price - self.rr_ratio * risk}
                    continue
                # In position — SL checked before Target within the same bar
                # (conservative), skipping the entry bar via the `continue` above.
                tp_level = entry['tp_level']
                if entry['direction'] == 'Long':
                    if c['low'] <= exp_low:
                        exit_row, exit_reason, exit_price = c, 'LOW_BREACH', exp_low
                        break
                    if c['high'] >= tp_level:
                        exit_row, exit_reason, exit_price = c, 'TARGET', tp_level
                        break
                else:
                    if c['high'] >= exp_high:
                        exit_row, exit_reason, exit_price = c, 'HIGH_BREACH', exp_high
                        break
                    if c['low'] <= tp_level:
                        exit_row, exit_reason, exit_price = c, 'TARGET', tp_level
                        break

            if entry is None:
                continue   # no breakout this cycle

            if exit_row is None:
                # Never hit the stop level within the window — force-close
                # at the last hourly candle (the trading day right before
                # the next expiry, or the end of the fetched data).
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
    """True if the candle's High/Low range straddles (low <= MA <= high)
    at least one of the given MA columns. A NaN MA (SMA200 warmup not yet
    complete) never satisfies the comparison, so this naturally requires
    enough history before any entry can trigger."""
    lo, hi = c['low'], c['high']
    return any(lo <= c[col] <= hi for col in ma_cols)


def _clears_all_mas(c, ma_cols, above: bool) -> bool:
    """True only if the candle's CLOSE clears every MA in ma_cols — above
    all of them (Long) or below all of them (Short). A NaN MA (SMA200
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
            'low_breach_exits': 0, 'high_breach_exits': 0, 'target_exits': 0, 'expiry_exits': 0, 'data_end_exits': 0,
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
        'low_breach_exits':  sum(1 for t in trades if t['exit_reason'] == 'LOW_BREACH'),
        'high_breach_exits': sum(1 for t in trades if t['exit_reason'] == 'HIGH_BREACH'),
        'target_exits':      sum(1 for t in trades if t['exit_reason'] == 'TARGET'),
        'expiry_exits':      sum(1 for t in trades if t['exit_reason'] == 'EXPIRY'),
        'data_end_exits':    sum(1 for t in trades if t['exit_reason'] == 'DATA_END'),
    }
