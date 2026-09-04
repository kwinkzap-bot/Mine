"""Past-expiry reconstruction — the calendar behind Replay's expiry dropdown.

No network: every case feeds a hand-built trading-day set, which is the whole
point of the module's design (it derives dates from days the index actually
traded rather than from a holiday list).
"""
from datetime import date, timedelta

from trading_app.service.expiry_calendar import (
    expiry_weekday_for,
    past_expiries,
)


def _weekdays(start: date, end: date, drop=()):
    """Mon-Fri between two dates, minus an explicit holiday list."""
    out, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in drop:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_regime_switches_thursday_to_tuesday_on_the_nse_cutover():
    assert expiry_weekday_for(date(2025, 8, 31)) == 3   # Thursday
    assert expiry_weekday_for(date(2025, 9, 1)) == 1    # Tuesday
    assert expiry_weekday_for(date(2026, 9, 4)) == 1


def test_weekly_expiries_are_tuesdays_newest_first():
    days = _weekdays(date(2026, 7, 1), date(2026, 9, 4))
    got = past_expiries(days, cadence='weekly', today=date(2026, 9, 4), limit=4)
    assert got == [date(2026, 9, 1), date(2026, 8, 25),
                   date(2026, 8, 18), date(2026, 8, 11)]
    assert all(d.weekday() == 1 for d in got)


def test_weekly_spans_the_regime_cutover():
    """The list has to cross 2025-09-01 correctly, which is the one thing a
    broker's live expiry list can never tell us — it only shows today's rule."""
    days = _weekdays(date(2025, 8, 1), date(2025, 9, 30))
    got = sorted(past_expiries(days, cadence='weekly', today=date(2025, 10, 1)))
    assert date(2025, 8, 28) in got and date(2025, 8, 28).weekday() == 3   # Thu
    assert date(2025, 9, 2) in got and date(2025, 9, 2).weekday() == 1     # Tue


def test_a_holiday_snaps_the_expiry_back_to_the_previous_session():
    holiday = date(2026, 8, 25)          # a Tuesday the market did not trade
    days = _weekdays(date(2026, 8, 1), date(2026, 9, 4), drop={holiday})
    got = past_expiries(days, cadence='weekly', today=date(2026, 9, 4))
    assert holiday not in got
    assert date(2026, 8, 24) in got      # the Monday before it


def test_monthly_expiries_are_the_last_expiry_weekday_of_each_month():
    days = _weekdays(date(2026, 4, 1), date(2026, 9, 4))
    got = past_expiries(days, cadence='monthly', today=date(2026, 9, 4), limit=4)
    assert got == [date(2026, 8, 25), date(2026, 7, 28),
                   date(2026, 6, 30), date(2026, 5, 26)]


def test_monthly_never_offers_the_current_months_unsettled_expiry():
    """September's last Tuesday (the 29th) has not happened yet on the 4th.

    Regression: the holiday snap used to walk back from that future date across
    the whole month and offer the last day it held (2026-09-03) as an expiry.
    """
    days = _weekdays(date(2026, 6, 1), date(2026, 9, 3))
    got = past_expiries(days, cadence='monthly', today=date(2026, 9, 4))
    assert date(2026, 9, 29) not in got
    assert date(2026, 9, 3) not in got
    assert got[0] == date(2026, 8, 25)


def test_explicit_weekday_pins_a_non_nse_schedule():
    """SENSEX runs BSE's calendar, so the caller passes its weekday rather than
    letting the NSE regime table answer."""
    days = _weekdays(date(2026, 8, 1), date(2026, 9, 4))
    got = past_expiries(days, cadence='weekly', weekday=3,   # Thursday
                        today=date(2026, 9, 4), limit=3)
    assert got == [date(2026, 9, 3), date(2026, 8, 27), date(2026, 8, 20)]


def test_todays_expiry_is_excluded_while_it_is_still_trading():
    days = _weekdays(date(2026, 8, 1), date(2026, 9, 1))
    got = past_expiries(days, cadence='weekly', today=date(2026, 9, 1))
    assert date(2026, 9, 1) not in got   # settles at today's close, not yet done


def test_no_trading_days_yields_no_expiries():
    assert past_expiries([], cadence='weekly') == []


# ── The as-of merge behind /api/oi-profile/expiries ─────────────────────────
# The endpoint answers "what could you have traded on this date" by merging two
# sources: expiries still listed (read off the broker's master, authoritative)
# and ones already settled (reconstructed above). These cover the merge itself.

def _as_of(settled, listed, day, limit=3):
    merged = sorted(set(settled) | set(listed))
    return [d for d in merged if d >= day][:limit]


def test_as_of_today_offers_the_live_expiries_front_first():
    """The worked example: standing on 04 Sep, the answer is 08/15/22 Sep."""
    listed = [date(2026, 9, 8), date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]
    got = _as_of([date(2026, 9, 1), date(2026, 8, 25)], listed, date(2026, 9, 4))
    assert got == [date(2026, 9, 8), date(2026, 9, 15), date(2026, 9, 22)]
    assert got[0] == date(2026, 9, 8)          # what the block opens on


def test_as_of_a_past_date_spans_settled_and_still_listed():
    settled = past_expiries(_weekdays(date(2026, 8, 1), date(2026, 9, 4)),
                            cadence='weekly', today=date(2026, 9, 4))
    got = _as_of(settled, [date(2026, 9, 8)], date(2026, 8, 20))
    assert got == [date(2026, 8, 25), date(2026, 9, 1), date(2026, 9, 8)]


def test_expiry_day_itself_is_still_offered():
    """It settles at that day's close, so on the day it is still the front one."""
    got = _as_of([], [date(2026, 9, 8), date(2026, 9, 15)], date(2026, 9, 8))
    assert got[0] == date(2026, 9, 8)
