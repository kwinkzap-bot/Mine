"""Narrow CPR scanner — weekly and monthly.

A narrow CPR says the period closed near the middle of its own range, so
the pivot band is thin and price has no built-in resistance inside it:
the classic continuation / trending-period setup. This scans the same
universe as the Camarilla scanner (F&O futures stocks PLUS the indices)
for symbols whose WEEKLY CPR, MONTHLY CPR, or both are narrow.

The CPR reported is the CURRENT one — built from the week / month
root_date falls in, which mid-period is still forming. That is the
forward-looking reading narrow CPR is traded on: a thin band coming out
of the period being traded now says the NEXT one has no built-in
resistance inside it. It deliberately differs from
CPRFilterService.calc_cpr_levels(), which builds the band already in
force from the PREVIOUS period — one period staler, and on CHOLAFIN in
August 2026 the difference between a 2.96%-wide monthly CPR (July) and a
0.86% one (August).

A still-forming period is flagged `forming`, because its OHLC — and so
its width — moves until the period closes. Early in a month especially,
few bars means a small range and a reading that will not survive the
rest of the month. When root_date falls in a period that has not traded
yet (a Sunday that opens a new month), the latest period WITH bars is
read instead — that band is the one in force.

"Narrow" is relative to the instrument's own recent CPR widths, not an
absolute percentage — see service/cpr_service.classify_cpr_width() for
why an absolute cut-off labels every index period Narrow. Each timeframe
is judged against its own context: the 10 weeks, or 6 months, completed
before the one being read.

Reuses CPRFilterService for historical-data fetching/caching, instrument
tokens and rate limiting rather than duplicating that infrastructure,
and asks it for the same 400-day daily window the Camarilla scan uses so
the two share cache entries.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd

from trading_app.service.cpr_service import (
    CPRService, classify_cpr_width, cpr_width_pct)

if TYPE_CHECKING:
    from trading_app.filters.cpr_filter import CPRFilterService

logger = logging.getLogger(__name__)

# Dropdown positions the route accepts; 'both' means narrow on each.
TIMEFRAMES = ('weekly', 'monthly', 'both')

# How far below its own normal a CPR has to be to count as narrow, as the
# ratio width / average-width. classify_cpr_width()'s own 0.8 cut-off answers
# "narrower than usual", which on 2026-08-30 was 143 of 214 F&O symbols
# monthly — true, and useless as a shortlist. The tighter settings rank the
# same reading; measured on that day they gave, weekly / monthly / both:
#   0.8x -> 107 / 143 / 83     0.5x -> 76 / 105 / 48
#   0.3x ->  50 /  67 / 23     0.2x -> 29 /  46 /  8
# The distribution is smooth, with no natural cliff to snap to, so this is a
# picker rather than a constant. 0.3x is the default: a shortlist a person
# can actually read through.
NARROW_RATIO_CHOICES = (0.8, 0.5, 0.3, 0.2)
DEFAULT_NARROW_RATIO = 0.3

# Pandas period alias per timeframe. 'W-SUN' is a Mon..Sun week, which is
# what get_prev_week_range() means by a week.
_PERIOD_FREQ = {'weekly': 'W-SUN', 'monthly': 'M'}

# Completed periods of context the latest CPR is measured against, and the
# minimum below which classify_cpr_width() falls back to its absolute scale.
# Ten weeks is a quarter; six months is the same span of "recent normal"
# without reaching back into a different regime.
_CONTEXT_PERIODS = {'weekly': 10, 'monthly': 6}
_MIN_CONTEXT     = {'weekly': 4,  'monthly': 3}

# Daily bars fetched per symbol. Six completed months plus the running one is
# ~210 days; 400 leaves room for holidays AND matches the window the Camarilla
# daily scan requests, so the two hit the same cache entry.
_LOOKBACK_DAYS = 400

# A series whose last bar is older than this is not describing the CURRENT
# period, whatever the request asked for — the local candle cache holds a
# handful of entries keyed to today that stop a month short. Reporting a
# July CPR as August's is the exact failure this scanner exists to avoid, so
# those symbols are skipped and counted instead. The longest run of closed
# Indian market days is a long weekend, well inside a week.
_MAX_STALE_DAYS = 7


def _periods(df: pd.DataFrame, timeframe: str,
             root_date: datetime) -> List[Dict]:
    """One aggregated bar per week/month up to `root_date`, oldest first.

    The LAST entry is the period being traded now — the one the reported CPR
    is built from. Bars after root_date are dropped first, so a provider that
    returns more than was asked for cannot make a later period the current
    one. Periods with no bars (a holiday week) simply do not appear rather
    than becoming a zero-width row.
    """
    dates = pd.to_datetime(df.index).date
    df = df[dates <= root_date.date()]  # type: ignore
    if df.empty:
        return []

    freq = _PERIOD_FREQ[timeframe]
    periods = pd.to_datetime(df.index).to_period(freq)

    out: List[Dict] = []
    for period, bars in df.groupby(periods):  # type: ignore[arg-type]
        if bars.empty:
            continue
        high  = float(bars['high'].max())
        low   = float(bars['low'].min())
        close = float(bars['close'].iloc[-1])
        width = cpr_width_pct(high, low, close)
        if width is None:
            continue
        pp, bc, tc = CPRService.calculate_cpr(high, low, close)
        out.append({'period': str(period), 'pp': pp, 'bc': bc, 'tc': tc,
                    'width_pct': width, 'bars': len(bars), 'frame': bars,
                    'end': period.end_time.date(),
                    'last_bar': pd.to_datetime(bars.index[-1]).date()})
    return out


def _width_of_first(period: Dict, sessions: int) -> Optional[float]:
    """The CPR width this period had after its first `sessions` bars.

    Three days into a month, high-low is a fraction of what a full month
    reaches, and |TC - BC| <= (H - L)/3 caps the width with it. Measuring that
    stub against full-month averages makes every symbol look Narrow for the
    first week of every month — a guaranteed false positive, not a signal.
    Truncating the history the same way compares like with like. A period
    with fewer bars than `sessions` is used whole; it is the closest
    comparable there is.
    """
    bars = period['frame'].iloc[:sessions]
    if bars.empty:
        return None
    return cpr_width_pct(float(bars['high'].max()), float(bars['low'].min()),
                         float(bars['close'].iloc[-1]))


def _is_forming(timeframe: str, period: Dict, root_date: datetime) -> bool:
    """Can this period's OHLC still move?

    A week is done once Friday has traded, whatever the weekend says — the
    same rule the Camarilla scanner uses for a partial weekly candle. A month
    is done once its last bar is its last calendar day; without a holiday
    calendar that is the closest honest answer, and it errs towards saying
    "still forming", which is the safe direction for a caller deciding how
    much to trust the width.
    """
    if root_date.date() > period['end']:
        return False
    if timeframe == 'weekly':
        return period['last_bar'].weekday() < 4
    return period['last_bar'] < period['end']


def _timeframe_reading(df: pd.DataFrame, timeframe: str,
                       root_date: datetime) -> Optional[Dict]:
    """The current CPR for `timeframe`, classified against its own context.

    Built from the period root_date falls in — or, when that one has not
    traded yet, the latest that has. None when there is no period to read.
    """
    periods = _periods(df, timeframe, root_date)
    if not periods:
        return None

    latest = periods[-1]
    forming = _is_forming(timeframe, latest, root_date)
    window = _CONTEXT_PERIODS[timeframe]
    previous = periods[-1 - window:-1]

    # A forming period is measured against the same slice of each earlier one
    # — its first N sessions — so the comparison is like for like. A closed
    # period is measured against closed periods, which is the same rule.
    if forming:
        context = [w for w in (_width_of_first(p, latest['bars']) for p in previous)
                   if w is not None]
    else:
        context = [p['width_pct'] for p in previous]
    avg = (sum(context) / len(context)
           if len(context) >= _MIN_CONTEXT[timeframe] else None)
    width = latest['width_pct']

    return {
        'period':        latest['period'],
        'forming':       forming,
        'bars':          latest['bars'],
        'width_pct':     round(width, 4),
        'avg_width_pct': round(avg, 4) if avg else None,
        'ratio':         round(width / avg, 3) if avg else None,
        'type':          classify_cpr_width(width, avg),
        'context':       len(context),
        'pp': round(latest['pp'], 2),
        'bc': round(min(latest['bc'], latest['tc']), 2),
        'tc': round(max(latest['bc'], latest['tc']), 2),
    }


def get_narrow_cpr_row(cpr_service: "CPRFilterService", symbol: str,
                       root_date: datetime, token: Optional[int] = None,
                       is_index: bool = False) -> Optional[Dict]:
    """Both current readings for one symbol, narrow or not.

    No verdict is attached: which readings count as narrow depends on the
    tightness the caller asks select_narrow() for, so one scan answers every
    dropdown position without re-fetching. None means no usable history.
    """
    df = cpr_service.get_hist_data(symbol, days=_LOOKBACK_DAYS, interval='day',
                                   end_date=root_date, token=token)
    if df is None or df.empty:
        return None

    dates = pd.to_datetime(df.index).date
    df = df[dates <= root_date.date()]  # type: ignore
    if df.empty:
        return None
    if (root_date.date() - pd.to_datetime(df.index[-1]).date()).days > _MAX_STALE_DAYS:
        logger.debug(f"Narrow-CPR: {symbol} history stops at {df.index[-1]}, too stale "
                     f"to read a current CPR for {root_date.date()}")
        return None

    weekly  = _timeframe_reading(df, 'weekly', root_date)
    monthly = _timeframe_reading(df, 'monthly', root_date)
    if weekly is None or monthly is None:
        return None

    row = {
        'symbol': symbol,
        'is_index': is_index,
        'current_price': round(float(df['close'].iloc[-1]), 2),
    }
    for name, reading in (('weekly', weekly), ('monthly', monthly)):
        for key, value in reading.items():
            row[f'{name}_{key}'] = value
    return row


def _is_narrow(row: Dict, timeframe: str, max_ratio: float) -> bool:
    """Is this timeframe's CPR at most `max_ratio` times its own normal?

    A row with no ratio never matches: too little history to average means the
    label came from the absolute fallback scale, which is a different
    measurement and cannot be compared against a ratio the user chose. With a
    400-day window that needs a symbol listed weeks ago, so it is rare.
    """
    ratio = row.get(f'{timeframe}_ratio')
    return ratio is not None and ratio <= max_ratio


def matches_narrow(row: Dict, tf: str,
                   max_ratio: float = DEFAULT_NARROW_RATIO) -> bool:
    """Does this row satisfy the dropdown — timeframe and tightness?

    Kept out of the scan itself so a cached scan can be re-filtered when either
    dropdown moves, instead of re-reading 180 symbols of history.
    """
    weekly  = _is_narrow(row, 'weekly', max_ratio)
    monthly = _is_narrow(row, 'monthly', max_ratio)
    if tf == 'weekly':
        return weekly
    if tf == 'monthly':
        return monthly
    return weekly and monthly


def _match_ratio(row: Dict, tf: str) -> float:
    """The ratio the match actually turned on — the one to rank by. For
    'both' that is the weaker of the two, so the top of the list is tight on
    BOTH timeframes rather than very tight on one."""
    weekly  = row.get('weekly_ratio')
    monthly = row.get('monthly_ratio')
    if tf == 'weekly':
        return weekly if weekly is not None else 99.0
    if tf == 'monthly':
        return monthly if monthly is not None else 99.0
    return max(weekly if weekly is not None else 99.0,
               monthly if monthly is not None else 99.0)


def select_narrow(rows: List[Dict], tf: str = 'both',
                  max_ratio: float = DEFAULT_NARROW_RATIO) -> List[Dict]:
    """The rows to show, tightest first, each labelled for the threshold that
    was asked for.

    The label has to be built here rather than in the scan: whether a symbol
    counts as narrow on a timeframe depends on the tightness the user picked,
    so a label baked in at scan time would contradict the dropdown.
    """
    out: List[Dict] = []
    for row in rows:
        if not matches_narrow(row, tf, max_ratio):
            continue
        # Copy: the scan's rows are cached and re-filtered per request, and
        # another request's tightness must not see these labels.
        labelled = dict(row)
        weekly  = _is_narrow(row, 'weekly', max_ratio)
        monthly = _is_narrow(row, 'monthly', max_ratio)
        labelled['narrow_weekly'] = weekly
        labelled['narrow_monthly'] = monthly
        labelled['narrow_on'] = ('Weekly + Monthly' if weekly and monthly
                                 else 'Weekly' if weekly
                                 else 'Monthly' if monthly else '')
        out.append(labelled)
    # Indices first — the handful of them are worth seeing before the stocks —
    # then tightest first, which is the whole point of ranking by ratio.
    return sorted(out, key=lambda r: (not r['is_index'], _match_ratio(r, tf),
                                      r['symbol']))


def filter_narrow_cpr(cpr_service: "CPRFilterService",
                      root_date: Optional[datetime] = None) -> Dict:
    """Scan futures stocks + indices and read every symbol's weekly and
    monthly CPR.

    Returns {'rows': [...], 'scanned': n, 'skipped': n} with ALL readable
    symbols, narrow or not — select_narrow() picks the ones the requested
    timeframe and tightness want. Filtering here instead would make the result
    depend on the dropdowns and cost a full re-scan every time one moved.
    """
    if root_date is None:
        root_date = datetime.now()
    # No weekend roll-back here, deliberately — unlike the Camarilla scan,
    # which judges a candle stamped with root_date and needs a trading day.
    # This one reads the period root_date falls in, which a Saturday shares
    # with the week that just traded, and falls back to the latest period
    # with bars when that period has not opened yet.

    from trading_app.filters.cpr_camarilla_scanner import _scan_universe
    universe = _scan_universe(cpr_service)
    rows: List[Dict] = []
    skipped = 0
    start_time = time.time()

    workers_count = cpr_service.MAX_WORKERS
    if cpr_service.kite.__class__.__name__ == 'FyersDataServiceAdapter':
        workers_count = 15

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {
            executor.submit(get_narrow_cpr_row, cpr_service, sym, root_date, tok, idx): sym
            for sym, tok, idx in universe
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                row = future.result(timeout=25)
                if row is None:
                    skipped += 1
                else:
                    rows.append(row)
            except Exception as e:
                skipped += 1
                logger.error(f"Narrow-CPR scan failed for {symbol}: {e}")

    at_default = len(select_narrow(rows, 'both'))
    logger.info(
        f"Narrow-CPR scan ({root_date.date()}) complete: {len(rows)} symbols read, "
        f"{at_default} narrow on both at {DEFAULT_NARROW_RATIO}x, {skipped} skipped, "
        f"in {time.time() - start_time:.1f}s"
    )
    # Left in arrival-independent order; select_narrow() ranks what it keeps,
    # by the ratio that decided that particular match.
    return {'rows': sorted(rows, key=lambda r: r['symbol']),
            'scanned': len(rows) + skipped, 'skipped': skipped}
