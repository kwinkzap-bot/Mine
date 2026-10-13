"""Weekly CPR Breakout backtest — the /scanners "W CPR Open" rule, traded.

Per symbol, per session D in [start, end], the same four conditions the live
scanner applies (filters/weekly_cpr_open_scanner.py — the levels and the
verdict are ITS functions, weekly_levels / opens_inside / judge_first_hour,
never a copy):

  1. D opens inside the weekly CPR in force that week;
  2–4. the first-hour verdict (judge_first_hour, `signal`, `candle_type`):
     'candle' (default) — any bearish (red) candle type is a SELL, any
     bullish (green) one a BUY (a doji is neither), optionally narrowed to
     one shape; its low / high — the entry level — is more than 0.5 % short
     of weekly S3 / R3; it need not close outside the CPR. 'close' — the
     earlier rule: it closes below BC as a red candle (SELL) / above TC as a
     green one (BUY), short of S3 / R3;
  5. quality: the weekly CPR is narrow (≤ 0.5× its 10-week average width)
     and the first hour's range is ≥ 1.6 % of price (passes_quality).

The trade:

  * entry  — a BREAKOUT of the first-hour candle after 10:15: a BUY when a
             1-minute bar trades above its HIGH, a SELL when one trades below
             its LOW. Filled at that level, or at the bar's open when the bar
             gaps straight through it. No break before `exit_time` = no trade.
             (Until 2026-10-13 the entry was the first hour's close at 10:15.)
  * stop   — the first-hour candle's other extreme: its LOW for a BUY, its
             HIGH for a SELL — so the risk is the candle's whole range;
  * target — `rr` × that range from the entry level (1:1.5 by default);
  * exit   — whichever of stop / target a 1-minute bar touches first,
             starting with the entry bar itself. A bar touching both is
             booked as the STOP: one bar cannot say which came first, and
             assuming the worse one keeps the result honest. Neither by
             `exit_time` (15:20) closes at that bar.

One trade per symbol per day. The weekly levels come from DAILY bars (the
official close, as the scanner and the chart popup use); the open, first
hour and the exit walk come from 1-minute bars.
"""
from __future__ import annotations

import itertools
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from trading_app.filters.weekly_cpr_open_scanner import (
    DEFAULT_CANDLE_TYPE, DEFAULT_MAX_CPR_RATIO, DEFAULT_MIN_HOUR_RANGE_PCT, DEFAULT_SIGNAL,
    hour_range_pct,
    judge_first_hour, opens_inside, passes_quality, weekly_levels)
from trading_app.service.cpr_service import cpr_width_pct

# 1:1.5 and out at 15:20 — chosen with the quality filters on 2026-10-13
# (see the scanner module's note): over every F&O stock since 2017 1:1.5 was
# the best target and holding to 15:20 beat 15:15 in-sample AND out-of-sample.
DEFAULT_RR = 1.5
DEFAULT_EXIT = (15, 20)
_CONTEXT_WEEKS = 10
_MIN_CONTEXT = 4
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
    widths = [cpr_width_pct(float(r.high), float(r.low), float(r.close))
              for r in weeks.itertuples()]
    table: Dict[pd.Timestamp, Dict] = {}
    prev = None
    for k, (period, row) in enumerate(weeks.iterrows()):
        if prev is not None:
            table[period.start_time.normalize()] = prev
        # Width against the 10 weeks before it — the scanner's _weekly_cpr
        # measure, so "narrow" means the same thing in both.
        ctx = [w for w in widths[max(0, k - _CONTEXT_WEEKS):k] if w is not None]
        avg = sum(ctx) / len(ctx) if len(ctx) >= _MIN_CONTEXT else None
        prev = {'week': str(period),
                'ratio': (widths[k] / avg) if (avg and widths[k] is not None) else None,
                **weekly_levels(float(row['high']), float(row['low']), float(row['close']))}
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


def _trade(bars: pd.DataFrame, direction: str, trigger: float, sl: float,
           target: float, exit_at: dt_time
           ) -> Optional[Tuple[pd.Timestamp, float, pd.Timestamp, float, str]]:
    """Walk the bars after 10:15: wait for the break of `trigger`, then for
    stop / target. Returns (entry time, entry price, exit time, exit price,
    reason), or None when price never broke out before `exit_at`."""
    buy = direction == 'BUY'
    after = bars[bars.index.time >= _FIRST_HOUR_END]
    entry_ts = entry_px = None
    for ts, bar in after.iterrows():
        if ts.time() >= exit_at:
            if entry_ts is None:
                return None                     # never broke out
            return entry_ts, entry_px, ts, float(bar['open']), 'Time exit'
        if entry_ts is None:
            broke = bar['high'] >= trigger if buy else bar['low'] <= trigger
            if not broke:
                continue
            # A bar that opens beyond the level gaps through it: filled at
            # the open, not at a price that never traded.
            entry_ts = ts
            entry_px = max(trigger, float(bar['open'])) if buy else min(trigger, float(bar['open']))
        hit_sl = bar['low'] <= sl if buy else bar['high'] >= sl
        hit_tg = bar['high'] >= target if buy else bar['low'] <= target
        if hit_sl:                              # both in one bar -> the stop
            return entry_ts, entry_px, ts, sl, 'SL hit'
        if hit_tg:
            return entry_ts, entry_px, ts, target, 'Target hit'
    if entry_ts is None:
        return None
    return entry_ts, entry_px, after.index[-1], float(after['close'].iloc[-1]), 'Time exit'


def run_symbol(daily: pd.DataFrame, minutes: pd.DataFrame, start: str, end: str,
               rr: float = DEFAULT_RR, exit_hm: Tuple[int, int] = DEFAULT_EXIT,
               direction: str = 'both', now: Optional[datetime] = None,
               max_ratio: Optional[float] = DEFAULT_MAX_CPR_RATIO,
               min_range_pct: Optional[float] = DEFAULT_MIN_HOUR_RANGE_PCT,
               signal: str = DEFAULT_SIGNAL,
               candle_type: str = DEFAULT_CANDLE_TYPE) -> List[Dict]:
    """Every trade the rule takes on one symbol between `start` and `end`.

    `daily` must reach back ~11 weeks before `start` — a week for the first
    week's levels, ten more for its narrowness ratio (without them the first
    weeks have no ratio and are skipped while that filter is on).
    `max_ratio` / `min_range_pct` are the quality filters (None / 0 = off). `direction` 'buy' / 'sell' keeps one side only. A session
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
        verdict = judge_first_hour(hour, levels, signal, candle_type)
        if not verdict['ok']:
            continue
        if not passes_quality(levels.get('ratio'), hour, max_ratio, min_range_pct):
            continue
        side = verdict['dir']
        if (direction == 'buy' and side != 'BUY') or (direction == 'sell' and side != 'SELL'):
            continue

        # Break of the first-hour candle: above its high for a BUY, below its
        # low for a SELL; the stop is the other end, so risk = its range.
        trigger = hour['high'] if side == 'BUY' else hour['low']
        sl = hour['low'] if side == 'BUY' else hour['high']
        risk = hour['high'] - hour['low']
        if risk <= 0:
            continue
        target = trigger + rr * risk if side == 'BUY' else trigger - rr * risk
        result = _trade(bars, side, trigger, sl, target, exit_at)
        if result is None:
            continue
        entry_ts, entry, exit_ts, exit_px, reason = result
        pnl = (exit_px - entry) if side == 'BUY' else (entry - exit_px)

        trades.append({
            'entry_time': entry_ts.isoformat(),
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
            'fh_close': round(hour['close'], 2),
            'fh_signal': verdict['side'],
            'fh_type': verdict.get('type'),
            'fh_range_pct': round(hour_range_pct(hour), 2),
            'cpr_ratio': round(levels['ratio'], 2) if levels.get('ratio') is not None else None,
            # Unrounded, for Find Best Params: filtering on the rounded copies
            # let a 0.504× week through a ≤ 0.5× combo the backtest refuses.
            'cpr_ratio_raw': levels.get('ratio'),
            'fh_range_raw': hour_range_pct(hour),
            'trigger': round(trigger, 2),
        })
    return trades


# ── Find Best Params ─────────────────────────────────────────────────────
# Target × W CPR width × min first-hour range × direction. Only the target
# changes how a trade plays out, so the caller replays each stock once per
# OPT_RR with the filters off and direction 'both', and score_combos applies
# the rest to those trades' own fields — the same answer as running every
# combo, at a fraction of the cost.
OPT_RR = (1.0, 1.5, 2.0, 3.0)
OPT_RATIO = (0.3, 0.5, 0.8, None)          # None = any width
OPT_RANGE = (0.0, 1.2, 1.6, 2.0)           # 0 = off
OPT_DIRECTION = ('both', 'buy', 'sell')
OPT_COMBOS = len(OPT_RR) * len(OPT_RATIO) * len(OPT_RANGE) * len(OPT_DIRECTION)
OPT_MIN_TRADES = 30


def score_combos(per_rr: Dict[float, Sequence[tuple]], recent_from: str,
                 min_trades: int = OPT_MIN_TRADES) -> List[Dict]:
    """Rank every combo by net ₹.

    `per_rr[rr]` holds one tuple per trade of the unfiltered replay at that
    target: (entry_time iso, 'BUY'|'SELL', unrounded cpr ratio or None,
    unrounded first-hour range %, net ₹ after brokerage, gross ₹). The
    filters compare exactly as passes_quality does. A trade with no ratio
    (too little history) only counts where the width filter is off. A WIN is
    gross P&L > 0, as on the backtest's cards; the ₹ totals are net.
    `recent_*` is the trades from `recent_from` on, by themselves.
    """
    from trading_app.Backtest.thirty_min_fakeout_engine import summarize_trades
    results = []
    for rr, ratio, rng, direction in itertools.product(OPT_RR, OPT_RATIO, OPT_RANGE, OPT_DIRECTION):
        rows = sorted((r for r in per_rr.get(rr, ())
                       if (ratio is None or (r[2] is not None and r[2] <= ratio))
                       and (not rng or r[3] >= rng)
                       and (direction == 'both' or r[1] == direction.upper())),
                      key=lambda r: r[0])
        if len(rows) < min_trades:
            continue
        s = summarize_trades([{'pnl': r[4], 'result': 'WIN' if r[5] > 0 else 'LOSS'} for r in rows])
        recent = [r[4] for r in rows if r[0] >= recent_from]
        results.append({
            'rr_ratio': rr,
            'max_cpr_ratio': ratio if ratio is not None else 'any',
            'min_range_pct': rng,
            'direction': direction,
            'total_trades': s['total_trades'], 'wins': s['wins'],
            'win_rate': round(s['wins'] / s['total_trades'] * 100, 1),
            'gross_pnl_rupees': round(sum(r[5] for r in rows), 2),
            'net_pnl_rupees': round(s['total_pnl'], 2),
            'avg_pnl_rupees': round(s['total_pnl'] / s['total_trades'], 2),
            'profit_factor_rupees': s['profit_factor'],
            'max_drawdown_rupees': s['max_drawdown'],
            'recent_trades': len(recent),
            'recent_net_rupees': round(sum(recent), 2),
            'recent_win_rate': (round(sum(1 for r in rows if r[0] >= recent_from and r[5] > 0)
                                      / len(recent) * 100, 1) if recent else None),
        })
    results.sort(key=lambda r: r['net_pnl_rupees'], reverse=True)
    return results
