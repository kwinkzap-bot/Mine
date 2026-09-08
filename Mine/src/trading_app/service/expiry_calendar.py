"""Past NSE/BSE expiry dates, derived rather than looked up.

There is no historical expiry/holiday calendar in this app, and no broker gives
us one: both instrument masters list only contracts that are still listed, so
the moment an expiry passes it vanishes from every source we have. (Confirmed on
2026-09-04 — ICICI's cached master held 10 NIFTY expiries, all of them in the
future, none in the past.)

So a past expiry is reconstructed the way `Backtest/expiry_breakout_engine`
reconstructs monthly ones: take the expiry weekday, then snap to the latest
actual trading day on or before it, which absorbs expiry holidays without
needing a holiday list. That engine's copy of the regime table is deliberately
left alone — it is backtest-only and bisect-pinned by its neighbours, so the two
are kept independent rather than shared across the live/backtest boundary.

Two things vary per symbol and are NOT guessed here — the caller reads them off
the broker's live expiry list and passes them in:

  * `cadence`  — NIFTY still lists weeklies; BANKNIFTY, FINNIFTY and MIDCPNIFTY
                 went monthly-only, so generating every week for them would
                 offer dates that never had a contract.
  * `weekday`  — BSE (SENSEX) runs its own schedule and has never matched NSE's.

The one thing that IS baked in is the NSE weekday CHANGE, because a live list
cannot show you what the rule used to be: NSE moved Thursday → Tuesday effective
2025-09-01. Any further change needs a new entry in _EXPIRY_WEEKDAY_REGIMES, and
dates generated before a regime this table does not know about will be wrong.
"""
from datetime import date, timedelta
from typing import Iterable, List, Optional, Set

# (cutover_date, python_weekday) — weekday(): Mon=0 … Tue=1 … Thu=3.
# An expiry uses the weekday of the LAST cutover whose date is <= that expiry.
_EXPIRY_WEEKDAY_REGIMES = [
    (date(1900, 1, 1), 3),   # Thursday — the long-standing rule
    (date(2025, 9, 1), 1),   # Tuesday  — NSE circular 2025-06-25, eff. 2025-09-01
]


def expiry_weekday_for(day: date) -> int:
    """The NSE expiry weekday in force on `day`."""
    weekday = _EXPIRY_WEEKDAY_REGIMES[0][1]
    for cutover, wd in _EXPIRY_WEEKDAY_REGIMES:
        if cutover <= day:
            weekday = wd
        else:
            break
    return weekday


# A holiday shifts an expiry by a day or two, never by a week. Bounding the walk
# keeps it absorbing holidays instead of sliding into a different expiry period:
# unbounded, a monthly expiry still in the FUTURE (September's last Tuesday, seen
# from the 3rd) walked all the way back to the last day we happened to hold and
# offered that as an expiry.
_SNAP_LIMIT_DAYS = 6


def _snap(day: date, trading: Set[date], floor: date) -> Optional[date]:
    """The last trading day on or before `day` — how holidays are absorbed."""
    probe = day
    stop = max(floor, day - timedelta(days=_SNAP_LIMIT_DAYS))
    while probe >= stop:
        if probe in trading:
            return probe
        probe -= timedelta(days=1)
    return None


def past_expiries(trading_days: Iterable[date], cadence: str = 'weekly',
                  weekday: Optional[int] = None, today: Optional[date] = None,
                  limit: Optional[int] = None) -> List[date]:
    """Past expiry dates, newest first.

    `trading_days` is the set of days the index actually traded — pass the dates
    off a daily-candle fetch. Only days inside that set are ever returned, so a
    generated date that fell on a holiday is snapped back to the previous
    session instead of being offered as a strike-less dead option.

    `weekday` overrides the NSE regime table (pass BSE's own weekday for SENSEX);
    None means "use whatever the regime says for that date", which is the only
    way to span the 2025-09-01 Thursday → Tuesday change correctly.
    """
    trading: Set[date] = {d for d in trading_days if d}
    if not trading:
        return []
    today = today or date.today()
    first = min(trading)
    last = min(max(trading), today - timedelta(days=1))

    def wd_for(d: date) -> int:
        return expiry_weekday_for(d) if weekday is None else weekday

    out: List[date] = []
    seen: Set[date] = set()

    if cadence == 'monthly':
        # Last occurrence of the expiry weekday in each calendar month, walked
        # backwards from the current month.
        cursor = last.replace(day=1)
        while cursor >= first.replace(day=1):
            nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            probe = nxt - timedelta(days=1)          # last day of `cursor`'s month
            while probe.weekday() != wd_for(probe):
                probe -= timedelta(days=1)
            # The current month's expiry has usually not happened yet; offering
            # it would put a contract that is still trading in a list that
            # promises settled ones.
            hit = _snap(probe, trading, first) if probe <= last else None
            if hit and hit <= last and hit not in seen:
                seen.add(hit)
                out.append(hit)
                if limit and len(out) >= limit:
                    break
            cursor = (cursor - timedelta(days=1)).replace(day=1)
        return out

    cursor = last
    while cursor >= first:
        if cursor.weekday() == wd_for(cursor):
            hit = _snap(cursor, trading, first)
            if hit and hit not in seen:
                seen.add(hit)
                out.append(hit)
                if limit and len(out) >= limit:
                    break
        cursor -= timedelta(days=1)
    return out


# Kept as the weekly-only name the first version shipped with.
def past_weekly_expiries(trading_days: Iterable[date], today: Optional[date] = None,
                         limit: Optional[int] = None) -> List[date]:
    return past_expiries(trading_days, 'weekly', None, today, limit)


# How far forward expiry_on_or_after will look for a contract. A weekly is at
# most 7 days out and a monthly at most ~31, so anything past this means the
# generated dates are not landing on sessions at all and walking further would
# only return a date nothing traded on.
_FORWARD_LIMIT_DAYS = 45


def _month_expiry(day: date, wd_for) -> date:
    """The last expiry-weekday of `day`'s calendar month."""
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    probe = nxt - timedelta(days=1)
    while probe.weekday() != wd_for(probe):
        probe -= timedelta(days=1)
    return probe


def expiry_on_or_after(day: date, trading_days: Iterable[date],
                       cadence: str = 'weekly',
                       weekday: Optional[int] = None) -> Optional[date]:
    """The expiry a contract bought on `day` would be held into — the nearest
    one on or after it.

    The forward mirror of `past_expiries`, and it exists because a backtest
    replaying a past day needs the ONE contract that was actually trading then,
    not a list walked backwards from today. Same inputs, same holiday handling:
    a generated date is snapped to the last session on or before it, and if
    that lands before `day` the expiry had already settled, so the next period's
    is taken instead.

    One difference matters for a range that runs up to today: a generated date
    in the FUTURE has no sessions after it to snap against, so it is returned
    as generated rather than dropped. Returns None only when nothing inside
    the forward window resolves.
    """
    trading: Set[date] = {d for d in trading_days if d}
    if not trading:
        return None
    floor = min(trading)
    latest = max(trading)

    def wd_for(d: date) -> int:
        return expiry_weekday_for(d) if weekday is None else weekday

    probe = day
    limit = day + timedelta(days=_FORWARD_LIMIT_DAYS)
    while probe <= limit:
        if cadence == 'monthly':
            cand = _month_expiry(probe, wd_for)
            following = (probe.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            cand = probe + timedelta(days=(wd_for(probe) - probe.weekday()) % 7)
            following = cand + timedelta(days=1)

        if cand >= day:
            # Beyond the days we hold there is nothing to snap against, and the
            # contract is still listed anyway — take the generated date.
            if cand > latest:
                return cand
            hit = _snap(cand, trading, floor)
            if hit is not None and hit >= day:
                return hit
        probe = max(following, probe + timedelta(days=1))
    return None
