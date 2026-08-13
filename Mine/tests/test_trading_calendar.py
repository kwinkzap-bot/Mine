"""Tests for the NSE trading-day calendar.

The parity test is the important one: the holiday list is deliberately
duplicated in static/js/common.js (the browser gate can't call into Python),
so this is what stops the two copies drifting apart.
"""
import os
import re
from datetime import date

import pytest

from trading_app.app.utils import trading_calendar as tc

_JS_PATH = os.path.join(os.path.dirname(__file__), '..', 'static', 'js', 'common.js')


def test_listed_holiday_is_not_a_trading_day():
    assert tc.is_holiday(date(2026, 8, 28))            # Ganesh Chaturthi, a Friday
    assert not tc.is_trading_day(date(2026, 8, 28))


def test_weekend_is_not_a_trading_day_but_is_not_a_holiday():
    assert not tc.is_trading_day(date(2026, 8, 22))    # Saturday
    assert not tc.is_holiday(date(2026, 8, 22))


def test_ordinary_weekday_is_a_trading_day():
    assert tc.is_trading_day(date(2026, 8, 25))        # Tuesday, AUG 2026 expiry


def test_next_trading_day_skips_the_diwali_cluster():
    # 2026-11-11 (Wed) and -12 (Thu) are holidays, -13 is a normal Friday.
    assert tc.next_trading_day(date(2026, 11, 10)) == date(2026, 11, 13)


def test_next_trading_day_inclusive_keeps_a_session_but_moves_a_holiday():
    assert tc.next_trading_day_inclusive(date(2026, 11, 13)) == date(2026, 11, 13)
    # -14 is both a Saturday and a listed holiday; the next session is Monday.
    assert tc.next_trading_day_inclusive(date(2026, 11, 14)) == date(2026, 11, 16)


def test_trading_days_before_the_aug_2026_expiry():
    # Tue 25th -> Mon 24 -> Fri 21 -> Thu 20. This is the live roll date.
    assert tc.trading_days_before(date(2026, 8, 25), 3) == date(2026, 8, 20)
    assert tc.trading_days_before(date(2026, 8, 25), 0) == date(2026, 8, 25)


def test_trading_days_before_skips_a_holiday_not_just_weekends():
    # From Mon 31 Aug 2026: Thu 27 (Fri 28 is Ganesh Chaturthi), Wed 26, Tue 25.
    # A weekend-only rule would have answered Wed 26.
    assert tc.trading_days_before(date(2026, 8, 31), 3) == date(2026, 8, 25)


def test_unknown_year_fails_open_and_warns_once(caplog):
    tc._warned_years.discard(2030)
    with caplog.at_level('WARNING'):
        assert tc.is_trading_day(date(2030, 3, 5))     # a Tuesday
        assert tc.is_trading_day(date(2030, 3, 6))
    hits = [r for r in caplog.records if 'No holiday entries for 2030' in r.getMessage()]
    assert len(hits) == 1, 'the unknown-year warning must fire once, not per call'
    assert not tc.calendar_covers(date(2030, 3, 5))
    assert tc.calendar_covers(date(2026, 3, 5))


def test_unknown_year_still_respects_weekends():
    assert not tc.is_trading_day(date(2030, 3, 9))     # Saturday


def test_python_and_js_holiday_lists_agree():
    """static/js/common.js is the other copy of this list — keep them equal."""
    with open(_JS_PATH, 'r') as f:
        src = f.read()
    block = re.search(r'const NSE_HOLIDAYS = new Set\(\[(.*?)\]\)', src, re.S)
    assert block, 'NSE_HOLIDAYS literal not found in static/js/common.js'
    js_dates = set(re.findall(r"'(\d{4}-\d{2}-\d{2})'", block.group(1)))
    assert js_dates == set(tc.NSE_HOLIDAYS), (
        'holiday lists have drifted — '
        f'only in JS: {sorted(js_dates - set(tc.NSE_HOLIDAYS))}, '
        f'only in Python: {sorted(set(tc.NSE_HOLIDAYS) - js_dates)}')
