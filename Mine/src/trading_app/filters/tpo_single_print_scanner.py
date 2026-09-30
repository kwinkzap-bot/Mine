"""TPO single-print scanner — one session, every futures stock.

A single print is a run of price rows that only ONE 30-minute TPO period
reached, with busier rows on BOTH sides of it: the gap a fast move tore
through the body of the profile. It is the level the auction skipped, and
the one it tends to come back and fill, which is why it is worth a
shortlist of its own.

The rule here is deliberately the SAME one ``static/js/components/mine_tpo.js``
draws, ported function for function, because the popup chart the scanner's
rows open is that engine: a row in this grid and the band on that chart
have to be the same band, or the scan is telling the user about something
they cannot see. In particular:

* periods are 30 minutes anchored on 09:15, not on the bar grid;
* rows are ABSOLUTE — row i spans [i*step, (i+1)*step) — so the profile
  lines up on a fixed price grid rather than on its own low;
* the row step is ``niceStep(session range / TPO_ROWS_TARGET)``, matching
  the chart's auto step for a one-session window;
* a run that touches the profile's own top or bottom is a TAIL, not a
  single print. Every session has one at each end, so counting those would
  match every symbol and say nothing.

Only the session on the requested date is read — "created as per the date
selection" — from 30-minute bars, which is one broker request per symbol.
Reuses CPRFilterService for tokens, rate limiting and the thread pool so
this scan queues behind the same limiter as the others.
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from trading_app.filters.cpr_filter import CPRFilterService

logger = logging.getLogger(__name__)

# The TPO period. 30 minutes is the standard market-profile letter and the
# timeframe the popup chart opens on, so the two agree by construction.
TPO_PERIOD_SECS = 30 * 60
TPO_INTERVAL = '30minute'

# NSE's session, as minutes past midnight. Bars outside it are pre-open or
# post-close prints and are not part of the profile — mine_tpo.js never sees
# them because multichart_service filters them out on the way to the chart.
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30

# Rows a session comes out around when the step is auto — mine_tpo.js's
# tpoRowsTarget. 30 puts a NIFTY day on 10-point rows, the box height the
# reference chart uses.
TPO_ROWS_TARGET = 30

# Value area, percent of the session's TPOs (mine_tpo.js tpoVA).
TPO_VA_PCT = 70

# A guard on the row loop, never reached by a real session (mine_tpo.js
# MAX_ROWS): a bad print with a 1000x high would otherwise build a profile
# of a hundred thousand rows.
MAX_ROWS = 400

# A session needs enough periods for "one period reached this row" to mean
# anything. Below four, almost every row is a single print by arithmetic.
MIN_PERIODS = 4

# Calendar days of 30-minute history asked for, ending on the requested
# date. Only that date's session is used; the window just has to reach it
# across a long weekend, and a short window keeps the payload small.
_LOOKBACK_DAYS = 5


# ── row geometry (mine_tpo.js) ───────────────────────────────────────────

def _row_of(price: float, step: float) -> int:
    """Absolute row holding `price`. Row i spans [i*step, (i+1)*step)."""
    return math.floor(price / step)


def _step_decimals(step: float) -> int:
    """Digits `step` itself carries, so a row price does not come back as
    7.5200000000000005 — these prices are what the bands are reported in."""
    text = repr(float(step))
    if 'e' in text or 'E' in text:
        return 8
    dot = text.find('.')
    if dot < 0:
        return 0
    return min(8, len(text) - dot - 1)


def _price_at(rows: float, step: float) -> float:
    return round(rows * step, _step_decimals(step))


def nice_step(raw: float) -> float:
    """1, 2, 2.5 or 5 times a power of ten — so NIFTY's ~250 point session
    lands on 5-point rows rather than 4.83-point ones. mine_tpo.js
    niceStep()."""
    if not raw > 0:
        return 1.0
    power = math.pow(10, math.floor(math.log10(raw)))
    for mult in (1, 2, 2.5, 5):
        if raw <= mult * power:
            return mult * power
    return 10 * power


# ── one session's profile ────────────────────────────────────────────────

def build_profile(bars: pd.DataFrame, session_open: pd.Timestamp,
                  step: Optional[float] = None) -> Optional[Dict]:
    """The single prints and levels of one session, or None if unreadable.

    `bars` is that session's 30-minute candles in order; `session_open` is
    its 09:15. Periods are cut off 09:15 rather than off the first bar, so a
    symbol that started trading late in the day keeps its real letters — the
    same anchoring mine_tpo.js uses, and the reason a hole in the feed does
    not relabel everything after it.
    """
    if bars.empty:
        return None

    # Periods, keyed by their CLOCK index: A is always the 09:15 period.
    periods: Dict[int, Dict[str, float]] = {}
    for stamp, bar in bars.iterrows():
        idx = int((stamp - session_open).total_seconds() // TPO_PERIOD_SECS)
        if idx < 0:
            continue
        period = periods.get(idx)
        if period is None:
            periods[idx] = {'high': float(bar['high']), 'low': float(bar['low'])}
        else:
            period['high'] = max(period['high'], float(bar['high']))
            period['low'] = min(period['low'], float(bar['low']))
    if len(periods) < MIN_PERIODS:
        return None

    high = max(p['high'] for p in periods.values())
    low = min(p['low'] for p in periods.values())
    if not high > low:
        return None

    # One session, so the chart's "median session range" is this range.
    if step is None:
        step = nice_step((high - low) / TPO_ROWS_TARGET)
    if not step > 0:
        return None

    counts: Dict[int, int] = {}
    total = 0
    min_row, max_row = None, None
    for period in periods.values():
        lo, hi = _row_of(period['low'], step), _row_of(period['high'], step)
        if hi - lo > MAX_ROWS:
            continue                      # a bad print, not a session
        for row in range(lo, hi + 1):
            counts[row] = counts.get(row, 0) + 1
            total += 1
            min_row = row if min_row is None else min(min_row, row)
            max_row = row if max_row is None else max(max_row, row)
    if not total or min_row is None or max_row is None:
        return None

    # POC: the busiest row; nearest the session's middle wins a tie, which
    # keeps it off a lone spike at an extreme.
    #
    # Iterated in FIRST-TOUCH order — period by period, low to high inside a
    # period — because that is the order mine_tpo.js walks its Map in, and
    # the tie-break is a strict "closer than the incumbent". Two rows equally
    # far from the middle therefore leave the incumbent standing, and which
    # one that is depends on this order. Walking the rows in price order
    # instead put RELIANCE's 2026-09-22 POC one row below the chart's.
    mid = (min_row + max_row) / 2
    poc, best = min_row, -1
    for row, count in counts.items():
        if count > best or (count == best and abs(row - mid) < abs(poc - mid)):
            poc, best = row, count

    # Value area: out from the POC in row pairs, heavier side first.
    count_of = lambda r: counts.get(r, 0)
    target = total * TPO_VA_PCT / 100
    inside, up, dn = count_of(poc), poc + 1, poc - 1
    while inside < target and (up <= max_row or dn >= min_row):
        above = count_of(up) + count_of(up + 1) if up <= max_row else -1
        below = count_of(dn) + count_of(dn - 1) if dn >= min_row else -1
        if above < 0 and below < 0:
            break
        if above >= below:
            inside += above
            up += 2
        else:
            inside += below
            dn -= 2
    vah_row, val_row = min(max_row, up - 1), max(min_row, dn + 1)

    # Single prints: runs of one-period rows bounded by busier rows on BOTH
    # sides. A run reaching the profile's own top or bottom is the session's
    # buying / selling tail — every session has one, so it is not news.
    singles: List[Tuple[int, int]] = []
    run_lo: Optional[int] = None
    for row in range(min_row, max_row + 2):
        one = row <= max_row and count_of(row) == 1
        if one and run_lo is None:
            run_lo = row
        elif not one and run_lo is not None:
            hi_row = row - 1
            if run_lo > min_row and hi_row < max_row:
                singles.append((run_lo, hi_row))
            run_lo = None

    return {
        'step': step,
        'periods': len(periods),
        'singles': [{'low': _price_at(lo, step), 'high': _price_at(hi + 1, step),
                     'rows': hi - lo + 1}
                    for lo, hi in singles],
        'poc': _price_at(poc, step),
        'vah': _price_at(vah_row + 1, step),
        'val': _price_at(val_row, step),
        'session_high': round(high, 2),
        'session_low': round(low, 2),
    }


# ── one symbol ───────────────────────────────────────────────────────────

def _session_bars(df: pd.DataFrame, day: datetime.date) -> pd.DataFrame:
    """`day`'s bars, inside the 09:15–15:30 session, oldest first.

    The broker stamps an intraday bar at its OPEN, so 15:15 is the last one
    of the day and 15:30 is already the next session's problem.
    """
    stamps = pd.to_datetime(df.index)
    if getattr(stamps, 'tz', None) is not None:
        stamps = stamps.tz_convert('Asia/Kolkata').tz_localize(None)
    frame = df.copy()
    frame.index = stamps
    minutes = stamps.hour * 60 + stamps.minute
    mask = ((stamps.date == day)
            & (minutes >= SESSION_OPEN_MIN) & (minutes < SESSION_CLOSE_MIN))
    return frame[mask].sort_index()


def get_tpo_row(cpr_service: "CPRFilterService", symbol: str,
                root_date: datetime, token: Optional[int] = None,
                is_index: bool = False) -> Optional[Dict]:
    """One symbol's single prints for `root_date`'s session.

    None means the session could not be read — no intraday history, too few
    periods, or a degenerate range. A readable session with no single print
    comes back as a row with an empty `singles`, so the caller can report how
    much of the universe was actually scanned.
    """
    df = cpr_service.get_hist_data(symbol, days=_LOOKBACK_DAYS,
                                   interval=TPO_INTERVAL, end_date=root_date,
                                   token=token)
    if df is None or df.empty:
        return None

    bars = _session_bars(df, root_date.date())
    if bars.empty:
        return None

    session_open = pd.Timestamp(root_date.date()) + timedelta(
        minutes=SESSION_OPEN_MIN)
    profile = build_profile(bars, session_open)
    if profile is None:
        return None

    singles = profile['singles']
    # Tallest band first inside a row: it is the one a person looks at, and
    # the grid ranks symbols by it.
    singles = sorted(singles, key=lambda s: s['high'] - s['low'], reverse=True)
    tallest = (singles[0]['high'] - singles[0]['low']) if singles else 0.0
    close = float(bars['close'].iloc[-1])

    return {
        'symbol': symbol,
        'is_index': is_index,
        'current_price': round(close, 2),
        'session_date': root_date.strftime('%Y-%m-%d'),
        'periods': profile['periods'],
        'row_step': profile['step'],
        'single_count': len(singles),
        'tallest_pts': round(tallest, 2),
        'tallest_pct': round(tallest / close * 100, 3) if close else 0.0,
        # Every band, low→high, so the grid can name them and the chart can
        # be checked against the row.
        'singles': singles,
        'bands': ' · '.join(f"{s['low']:g}–{s['high']:g}" for s in singles),
        'poc': profile['poc'],
        'vah': profile['vah'],
        'val': profile['val'],
        'session_high': profile['session_high'],
        'session_low': profile['session_low'],
    }


# ── the scan ─────────────────────────────────────────────────────────────

def select_singles(rows: List[Dict]) -> List[Dict]:
    """The rows to show — the sessions that printed at least one single —
    indices first, then the tallest band first.

    Kept out of the scan so a cached scan can be re-read without re-fetching
    214 symbols of intraday history.
    """
    matched = [r for r in rows if r.get('single_count')]
    return sorted(matched, key=lambda r: (not r['is_index'], -r['tallest_pct'],
                                          r['symbol']))


def filter_tpo_singles(cpr_service: "CPRFilterService",
                       root_date: Optional[datetime] = None) -> Dict:
    """Scan futures stocks + indices for single prints in `root_date`'s
    session.

    Returns {'rows': [...], 'scanned': n, 'skipped': n} with EVERY readable
    session, matched or not — select_singles() picks the ones with a band.
    """
    if root_date is None:
        root_date = datetime.now()
    # A session only exists on a trading day; roll a weekend date back to
    # Friday, as the Camarilla scan does for the same reason.
    if root_date.weekday() == 5:
        root_date -= timedelta(days=1)
    elif root_date.weekday() == 6:
        root_date -= timedelta(days=2)

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
            executor.submit(get_tpo_row, cpr_service, sym, root_date, tok, idx): sym
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
                logger.error(f"TPO single-print scan failed for {symbol}: {e}")

    matched = len(select_singles(rows))
    logger.info(
        f"TPO single-print scan ({root_date.date()}, {TPO_INTERVAL} periods) "
        f"complete: {len(rows)} sessions read, {matched} with a single print, "
        f"{skipped} skipped, in {time.time() - start_time:.1f}s"
    )
    return {'rows': sorted(rows, key=lambda r: r['symbol']),
            'scanned': len(rows) + skipped, 'skipped': skipped,
            'date': root_date.strftime('%Y-%m-%d')}
