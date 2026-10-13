"""Weekly CPR Breakout backtest — the /scanners "W CPR Open" rule, traded.

Per symbol, per session D in [start, end], the same four conditions the live
scanner applies (filters/weekly_cpr_open_scanner.py — the levels and the
verdict are ITS functions, weekly_levels / opens_inside / judge_first_hour,
never a copy):

  1. D opens inside the weekly CPR in force that week;
  2. D's first 1-hour candle (09:15–10:15) closes outside it;
  3. as a direction candle agreeing with the break — green above TC (BUY),
     red below BC (SELL);
  4. more than 0.5 % short of the weekly Camarilla R3 (BUY) / S3 (SELL).

The trade:

  * entry  — at the first hour's close, 10:15;
  * stop   — the first-hour candle's other extreme: its LOW for a BUY, its
             HIGH for a SELL;
  * target — `rr` × that risk from the entry (1:2 by default);
  * exit   — whichever of stop / target a later 1-minute bar touches first.
             A bar touching both is booked as the STOP: one bar cannot say
             which came first, and assuming the worse one keeps the result
             honest. Neither by `exit_time` (15:15) closes at that bar.

One trade per symbol per day. The weekly levels come from DAILY bars (the
official close, as the scanner and the chart popup use); the open, first
hour and the exit walk come from 1-minute bars.
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from trading_app.filters.weekly_cpr_open_scanner import (
    judge_first_hour, opens_inside, weekly_levels)

DEFAULT_RR = 2.0
DEFAULT_EXIT = (15, 15)
_FIRST_HOUR_START = dt_time(9, 15)
_FIRST_HOUR_END = dt_time(10, 15)      # exclusive: the 09:15..10:14 minute bars


def weekly_level_table(daily: pd.DataFrame) -> Dict[pd.Timestamp, Dict]:
    """{Monday of week W: levels in force during W}, from week W−1's daily bars.

    'The previous week' is the previous week WITH bars, so a week after a
    full holiday week still has levels — the same rule the scanner's
    _weekly_cpr applies.
    """
    if daily is None or daily.empty:
        return {}
    weeks = daily.groupby(daily.index.to_period('W-SUN')).agg(
        {'high': 'max', 'low': 'min', 'close': 'last'})
    table: Dict[pd.Timestamp, Dict] = {}
    prev = None
    for period, row in weeks.iterrows():
        if prev is not None:
            table[period.start_time.normalize()] = prev
        prev = {'week': str(period), **weekly_levels(
            float(row['high']), float(row['low']), float(row['close']))}
    # The week after the last one with bars (the one being traded now).
    if prev is not None:
        nxt = (weeks.index[-1] + 1).start_time.normalize()
        table[nxt] = prev
    return table


def _first_hour(bars: pd.DataFrame) -> Optional[Dict]:
    """09:15–10:15 OHLC from one session's 1-minute bars; None if the hour
    is not complete (a late start, or a session cut short)."""
    times = bars.index.time
    hour = bars[(times >= _FIRST_HOUR_START) & (times < _FIRST_HOUR_END)]
    if hour.empty or hour.index[0].time() != _FIRST_HOUR_START:
        return None
    if hour.index[-1].time() < dt_time(10, 10):
        return None
    return {'open': float(hour['open'].iloc[0]), 'high': float(hour['high'].max()),
            'low': float(hour['low'].min()), 'close': float(hour['close'].iloc[-1])}


def _walk(bars: pd.DataFrame, direction: str, sl: float, target: float,
          exit_at: dt_time) -> Tuple[pd.Timestamp, float, str]:
    """(exit time, exit price, reason) for a position opened at 10:15."""
    after = bars[bars.index.time >= _FIRST_HOUR_END]
    for ts, bar in after.iterrows():
        if ts.time() >= exit_at:
            return ts, float(bar['open']), 'Time exit'
        hit_sl = bar['low'] <= sl if direction == 'BUY' else bar['high'] >= sl
        hit_tg = bar['high'] >= target if direction == 'BUY' else bar['low'] <= target
        if hit_sl:                              # both in one bar -> the stop
            return ts, sl, 'SL hit'
        if hit_tg:
            return ts, target, 'Target hit'
    last_ts = after.index[-1] if not after.empty else bars.index[-1]
    last_px = float(after['close'].iloc[-1]) if not after.empty else float(bars['close'].iloc[-1])
    return last_ts, last_px, 'Time exit'


def run_symbol(daily: pd.DataFrame, minutes: pd.DataFrame, start: str, end: str,
               rr: float = DEFAULT_RR, exit_hm: Tuple[int, int] = DEFAULT_EXIT,
               direction: str = 'both', now: Optional[datetime] = None) -> List[Dict]:
    """Every trade the rule takes on one symbol between `start` and `end`.

    `daily` must reach back at least a week before `start` (for the first
    week's levels). `direction` 'buy' / 'sell' keeps one side only. A session
    still trading at `now` (before its exit time) is skipped — its result is
    not known yet.
    """
    if daily is None or daily.empty or minutes is None or minutes.empty:
        return []
    levels_by_week = weekly_level_table(daily)
    exit_at = dt_time(*exit_hm)
    now = now or datetime.now()
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    window = minutes[(minutes.index >= lo) & (minutes.index < hi)]
    trades: List[Dict] = []

    for day, bars in window.groupby(window.index.normalize()):
        if day.date() == now.date() and now.time() < exit_at:
            continue
        week = day - pd.Timedelta(days=day.weekday())
        levels = levels_by_week.get(week)
        if levels is None:
            continue
        if bars.index[0].time() != _FIRST_HOUR_START:
            continue                                # no clean 09:15 open
        if not opens_inside(float(bars['open'].iloc[0]), levels):
            continue
        hour = _first_hour(bars)
        if hour is None:
            continue
        verdict = judge_first_hour(hour, levels)
        if not verdict['ok']:
            continue
        side = verdict['dir']
        if (direction == 'buy' and side != 'BUY') or (direction == 'sell' and side != 'SELL'):
            continue

        entry = hour['close']
        sl = hour['low'] if side == 'BUY' else hour['high']
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        target = entry + rr * risk if side == 'BUY' else entry - rr * risk
        exit_ts, exit_px, reason = _walk(bars, side, sl, target, exit_at)
        pnl = (exit_px - entry) if side == 'BUY' else (entry - exit_px)

        trades.append({
            'entry_time': (day + pd.Timedelta(hours=10, minutes=15)).isoformat(),
            'exit_time': exit_ts.isoformat(),
            'direction': side,
            'type': 'Long' if side == 'BUY' else 'Short',
            'entry_price': round(entry, 2),
            'exit_price': round(exit_px, 2),
            'sl_price': round(sl, 2),
            'target_price': round(target, 2),
            'pnl': round(pnl, 2),
            'result': 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'SCRATCH'),
            'exit_reason': reason,
            'day_open': round(float(bars['open'].iloc[0]), 2),
            'w_bc': round(levels['bc'], 2), 'w_tc': round(levels['tc'], 2),
            'w_r3': round(levels['r3'], 2), 'w_s3': round(levels['s3'], 2),
            'cpr_week': levels['week'],
            'fh_high': round(hour['high'], 2), 'fh_low': round(hour['low'], 2),
        })
    return trades
