"""NSE trading-day calendar.

Until this module existed, every backend "is this a trading day?" check in the
app was `weekday() < 5` — a holiday looked exactly like a normal session. That
is good enough for a scheduler that merely wakes up too often, but not for
anything that must COUNT sessions (the EMA Confluence futures roll fires N
trading days before expiry) or that must not act on a stale quote (a holiday
quote is just the previous close, indistinguishable from a live tick).

The holiday list is a Python mirror of NSE_HOLIDAYS in static/js/common.js —
the browser gate there has to stay a zero-dependency synchronous function, so
it cannot read this one. tests/test_trading_calendar.py scrapes that file and
asserts the two lists are equal, so drift is a red test rather than a silent
divergence. Update BOTH each year from the NSE circular.

FAILS OPEN. A date in a year this list doesn't cover is treated as a normal
trading day (weekday rule only). Guessing at a holiday we don't know about
would silently skip a real session, which is strictly worse than trading on a
day that turns out to be shut — the same reasoning already written into
oi_crossover_service.market_session_state.
"""
import logging
from datetime import date, timedelta
from typing import FrozenSet, Set

logger = logging.getLogger(__name__)

# NSE trading holidays (YYYY-MM-DD, IST date). Weekends are handled separately,
# so entries that fall on a Saturday/Sunday are harmless duplicates of the
# weekday rule. Mirror of NSE_HOLIDAYS in static/js/common.js.
NSE_HOLIDAYS: FrozenSet[str] = frozenset({
    # 2026
    '2026-01-26',  # Republic Day
    '2026-02-26',  # Mahashivratri
    '2026-03-20',  # Holi
    '2026-04-02',  # Ram Navami
    '2026-04-03',  # Good Friday
    '2026-04-14',  # Dr. Ambedkar Jayanti
    '2026-05-01',  # Maharashtra Day
    '2026-06-06',  # Eid ul Adha (Bakri Id)
    '2026-07-31',  # Muharram
    '2026-08-15',  # Independence Day
    '2026-08-28',  # Ganesh Chaturthi
    '2026-10-02',  # Gandhi Jayanti
    '2026-10-22',  # Dussehra
    '2026-11-11',  # Diwali Laxmi Puja
    '2026-11-12',  # Diwali Balipratipada
    '2026-11-14',  # Gurunanak Jayanti
    '2026-12-25',  # Christmas
    # 2025
    '2025-01-26',  # Republic Day
    '2025-03-14',  # Holi
    '2025-04-10',  # Ram Navami
    '2025-04-14',  # Dr. Ambedkar Jayanti
    '2025-04-18',  # Good Friday
    '2025-05-01',  # Maharashtra Day
    '2025-08-15',  # Independence Day
    '2025-08-27',  # Ganesh Chaturthi
    '2025-10-02',  # Gandhi Jayanti / Mahalaya
    '2025-10-20',  # Diwali Laxmi Puja
    '2025-10-21',  # Diwali Balipratipada
    '2025-11-05',  # Gurunanak Jayanti
    '2025-12-25',  # Christmas
})

_KNOWN_YEARS: FrozenSet[int] = frozenset(int(d[:4]) for d in NSE_HOLIDAYS)

# A year is only worth warning about once per process — these helpers are
# called from a 15s poll loop.
_warned_years: Set[int] = set()

# Nothing here may spin: a walker is called from the live algo's tick thread,
# and no real holiday cluster comes close to this many consecutive days.
_MAX_WALK_DAYS = 10


def calendar_covers(d: date) -> bool:
    """True when the holiday list actually has entries for this date's year."""
    return d.year in _KNOWN_YEARS


def is_holiday(d: date) -> bool:
    """True for a listed NSE holiday. Weekends are NOT holidays by this test —
    use is_trading_day for the combined question."""
    return d.isoformat() in NSE_HOLIDAYS


def is_trading_day(d: date) -> bool:
    """True when the NSE holds a session on this date."""
    if d.weekday() >= 5:
        return False
    if not calendar_covers(d):
        if d.year not in _warned_years:
            _warned_years.add(d.year)
            logger.warning(
                "[TradingCalendar] No holiday entries for %d — treating every weekday "
                "that year as a session. Update NSE_HOLIDAYS (here and in "
                "static/js/common.js) from the NSE circular.", d.year)
        return True
    return not is_holiday(d)


def next_trading_day(d: date) -> date:
    """The first session strictly after `d`."""
    return _walk(d, +1)


def next_trading_day_inclusive(d: date) -> date:
    """`d` itself when it is a session, otherwise the next one."""
    return d if is_trading_day(d) else _walk(d, +1)


def previous_trading_day(d: date) -> date:
    """The last session strictly before `d`."""
    return _walk(d, -1)


def trading_days_before(d: date, n: int) -> date:
    """The session `n` sessions before `d` (n=0 returns `d` unchanged).

    `d` itself need not be a session — each step just walks to the previous
    one, so counting back from an expiry date always lands on real sessions.
    """
    out = d
    for _ in range(max(0, n)):
        out = previous_trading_day(out)
    return out


def _walk(d: date, step: int) -> date:
    cur = d
    for _ in range(_MAX_WALK_DAYS):
        cur += timedelta(days=step)
        if is_trading_day(cur):
            return cur
    # Unreachable with any sane list; returning the bound beats hanging a
    # trading thread on a malformed one.
    logger.error("[TradingCalendar] No session within %d days of %s (step %+d) — "
                 "the holiday list looks malformed", _MAX_WALK_DAYS, d, step)
    return cur
