"""Rule tests for the weekly / monthly Narrow CPR scanner.

Synthetic daily OHLC, so the period the CPR is built from and the context
it is judged against can be moved one at a time. Nothing here constructs
CPRFilterService (and so nothing reaches a broker) — a stub supplies the
handful of methods the scanner actually calls on it.

The scan reads the CURRENT period's CPR — the week / month root_date falls
in, still forming mid-period — not the completed one before it. That is the
distinction these tests are mostly about.

The arithmetic every case below leans on: with high 102 and low 98,
TC - BC = (2C - H - L)/3, so a period closing at 100 has a zero-width CPR
and one closing at 102 has the widest it can (~1.31% of close).
"""
from datetime import date, datetime

import pandas as pd
import pytest

from trading_app.filters.narrow_cpr_scanner import (
    DEFAULT_NARROW_RATIO, _periods, _timeframe_reading, filter_narrow_cpr,
    get_narrow_cpr_row, matches_narrow, select_narrow)

HIGH, LOW = 102.0, 98.0
WIDE_CLOSE   = 102.0   # closes on its high -> widest CPR the range allows
NARROW_CLOSE = 100.2   # closes mid-range   -> CPR collapses towards a line
ROOT = datetime(2026, 8, 26)                          # a Wednesday
THIS_WEEK = (date(2026, 8, 24), date(2026, 8, 30))    # the week ROOT is in


class _StubService:
    """The slice of CPRFilterService the scanner uses."""

    MAX_WORKERS = 2

    class _Kite:
        pass

    def __init__(self, frame, fo_stocks=('ACME',)):
        self.frame = frame
        self.kite = self._Kite()
        self._fo_stocks = list(fo_stocks)

    def get_hist_data(self, symbol, days=400, interval='day', end_date=None, token=None):
        return self.frame

    def get_fo_stocks(self):
        return self._fo_stocks


def _frame(close_for=None, start='2026-01-01', end='2026-08-26'):
    """Daily bars over a business-day range. `close_for(d)` overrides the
    close for a given date; everything else closes on its high."""
    idx = pd.bdate_range(start, end)
    closes = [(close_for(d.date()) if close_for else None) or WIDE_CLOSE for d in idx]
    return pd.DataFrame(
        {'open': [100.0] * len(idx), 'high': [HIGH] * len(idx),
         'low': [LOW] * len(idx), 'close': closes},
        index=idx,
    )


def _narrow_this_week(d):
    return NARROW_CLOSE if THIS_WEEK[0] <= d <= THIS_WEEK[1] else None


def _narrow_august(d):
    return NARROW_CLOSE if d.month == 8 else None


def _widening_frame(end='2026-08-05'):
    """Every month starts tight and widens session by session: on its n-th
    session the range is 100 +/- n and it closes on its high. A month's CPR
    width therefore grows all month, which is what makes a 3-session stub
    look narrow against completed months."""
    idx = pd.bdate_range('2026-01-01', end)
    seen: dict = {}
    highs, lows, closes = [], [], []
    for ts in idx:
        n = seen.get((ts.year, ts.month), 0)
        seen[(ts.year, ts.month)] = n + 1
        highs.append(100.0 + n)
        lows.append(100.0 - n)
        closes.append(100.0 + n)
    return pd.DataFrame({'open': closes, 'high': highs, 'low': lows, 'close': closes},
                        index=idx)


def _narrow_week_inside_a_wide_month():
    """A tight, mid-range week sitting inside a month that is nothing of the
    kind — the case the Weekly-only dropdown position exists for.

    Every session runs 95-130 and closes on its high, so weeks are wide; the
    18th session of each month closes near the month's mid, which is what the
    forming August is compared against. The current week is 99-101 closing at
    100, so its CPR collapses while August's — 130 high, 95 low, closing at
    100 — is as far from mid as it gets.
    """
    idx = pd.bdate_range('2026-01-01', '2026-08-26')
    highs, lows, closes = [], [], []
    seen: dict = {}
    for ts in idx:
        d = ts.date()
        n = seen.get((d.year, d.month), 0)
        seen[(d.year, d.month)] = n + 1
        if THIS_WEEK[0] <= d <= THIS_WEEK[1]:
            highs.append(101.0); lows.append(99.0); closes.append(100.0)
        elif n == 17:
            highs.append(130.0); lows.append(95.0); closes.append(113.5)
        else:
            highs.append(130.0); lows.append(95.0); closes.append(130.0)
    return pd.DataFrame({'open': closes, 'high': highs, 'low': lows, 'close': closes},
                        index=idx)


# ── which period the CPR is built from ───────────────────────────────────

def test_the_current_week_is_the_one_read():
    """The point of the fix: the CPR reported comes from the week root_date is
    in, not the completed week before it. Only this week closes at
    NARROW_CLOSE, so a reading off any other week shows the wide CPR."""
    reading = _timeframe_reading(_frame(_narrow_this_week), 'weekly', ROOT)
    assert reading['period'] == '2026-08-24/2026-08-30'
    assert reading['pp'] == round((HIGH + LOW + NARROW_CLOSE) / 3, 2)


def test_the_current_month_is_the_one_read():
    reading = _timeframe_reading(_frame(_narrow_august), 'monthly', ROOT)
    assert reading['period'] == '2026-08'
    assert reading['pp'] == round((HIGH + LOW + NARROW_CLOSE) / 3, 2)


def test_bars_after_root_date_do_not_leak_in():
    """A frame running past root_date (the provider returns what it has) must
    not make a later period the current one."""
    row = get_narrow_cpr_row(_StubService(_frame(end='2026-09-30')), 'ACME', ROOT)
    assert row['weekly_period'] == '2026-08-24/2026-08-30'
    assert row['monthly_period'] == '2026-08'


def test_a_period_that_has_not_traded_yet_falls_back_to_the_last_that_did():
    """Sunday 1 November: November has no bars, and the band in force is the
    one October closed with."""
    frame = _frame(start='2026-01-01', end='2026-10-30')
    row = get_narrow_cpr_row(_StubService(frame), 'ACME', datetime(2026, 11, 1))
    assert row['monthly_period'] == '2026-10'
    assert row['monthly_forming'] is False


# ── still forming or done ────────────────────────────────────────────────

def test_a_midweek_reading_is_flagged_as_still_forming():
    reading = _timeframe_reading(_frame(), 'weekly', ROOT)   # Wednesday
    assert reading['forming'] is True
    assert reading['bars'] == 3


def test_a_week_is_done_once_friday_has_traded():
    frame = _frame(end='2026-08-28')
    friday   = _timeframe_reading(frame, 'weekly', datetime(2026, 8, 28))
    saturday = _timeframe_reading(frame, 'weekly', datetime(2026, 8, 29))
    assert friday['forming'] is False
    assert saturday['forming'] is False
    assert saturday['period'] == friday['period'] == '2026-08-24/2026-08-30'


def test_a_month_is_still_forming_until_its_last_day_has_traded():
    assert _timeframe_reading(_frame(), 'monthly', ROOT)['forming'] is True


# ── narrow is relative to the symbol's own context ───────────────────────

def test_narrow_weekly_against_its_own_wider_weeks():
    reading = _timeframe_reading(_frame(_narrow_this_week), 'weekly', ROOT)
    assert reading['type'] == 'Narrow'
    assert reading['ratio'] < 0.8
    assert reading['context'] == 10


def test_a_week_matching_its_context_is_not_narrow():
    """Every week identical: the latest one is exactly its own average, so
    nothing here is narrow however wide or thin the band happens to be."""
    reading = _timeframe_reading(_frame(), 'weekly', ROOT)
    assert reading['type'] == 'Medium'
    assert reading['ratio'] == pytest.approx(1.0, abs=1e-6)


def test_narrow_monthly_against_its_own_wider_months():
    reading = _timeframe_reading(_frame(_narrow_august), 'monthly', ROOT)
    assert reading['period'] == '2026-08'
    assert reading['type'] == 'Narrow'


def test_context_is_capped_at_the_configured_window():
    assert _timeframe_reading(_frame(), 'monthly', ROOT)['context'] == 6


def test_absolute_fallback_when_there_is_too_little_context():
    """Two prior weeks is below the minimum to average, so the label comes
    from the absolute scale and there is no ratio to report."""
    frame = _frame(start='2026-08-10', end='2026-08-26')
    reading = _timeframe_reading(frame, 'weekly', ROOT)
    assert reading['context'] < 4
    assert reading['avg_width_pct'] is None and reading['ratio'] is None
    assert reading['type'] in ('Narrow', 'Medium', 'Wide')


# ── a forming period is compared against equally short history ───────────

def test_a_three_session_month_is_judged_against_three_session_history():
    """Without this the scan is useless for the first week of every month:
    |TC - BC| <= (H - L)/3, so a 3-session stub of a month cannot reach the
    width a full month does and reads Narrow on every symbol."""
    frame = _widening_frame('2026-08-05')          # Aug 3, 4, 5 traded
    reading = _timeframe_reading(frame, 'monthly', datetime(2026, 8, 5))
    assert reading['forming'] is True and reading['bars'] == 3

    full_months = [p['width_pct'] for p in _periods(frame, 'monthly',
                                                    datetime(2026, 8, 5))[:-1]]
    full_avg = sum(full_months[-6:]) / 6
    # The completed months are far wider than their own first three sessions.
    assert reading['avg_width_pct'] < full_avg / 2
    # Judged like for like it is ordinary; against whole months it would have
    # been a false Narrow.
    assert reading['type'] == 'Medium'
    assert reading['width_pct'] / full_avg < 0.8


def test_a_closed_period_is_still_compared_against_closed_periods():
    frame = _widening_frame('2026-08-31')
    reading = _timeframe_reading(frame, 'monthly', datetime(2026, 8, 31))
    context = [p['width_pct'] for p in _periods(frame, 'monthly',
                                                datetime(2026, 8, 31))[-7:-1]]
    assert reading['forming'] is False
    assert reading['avg_width_pct'] == pytest.approx(sum(context) / len(context), abs=1e-4)


# ── the row ──────────────────────────────────────────────────────────────

def test_row_carries_both_readings_with_no_verdict_attached():
    """Narrow week, wide month. The row is the measurement only — which of the
    two counts as narrow depends on the tightness the caller asks for, so a
    verdict baked in here would contradict the dropdown."""
    row = get_narrow_cpr_row(_StubService(_narrow_week_inside_a_wide_month()),
                             'ACME', ROOT)
    assert 'narrow_on' not in row and 'narrow_weekly' not in row
    assert row['weekly_ratio'] < 0.3 < row['monthly_ratio']
    assert row['monthly_type'] == 'Wide'
    assert row['weekly_bc'] <= row['weekly_pp'] <= row['weekly_tc']
    assert row['weekly_period'] == '2026-08-24/2026-08-30'
    assert row['monthly_period'] == '2026-08'


def test_selection_labels_the_row_for_the_threshold_asked_for():
    row = get_narrow_cpr_row(_StubService(_narrow_week_inside_a_wide_month()),
                             'ACME', ROOT)
    [picked] = select_narrow([row], 'weekly', 0.3)
    assert picked['narrow_weekly'] is True
    assert picked['narrow_monthly'] is False
    assert picked['narrow_on'] == 'Weekly'
    assert select_narrow([row], 'both', 0.3) == []


def test_selection_does_not_write_its_labels_back_onto_the_cached_row():
    """The scan is cached and re-filtered per request; one request's tightness
    must not leak into the next one's."""
    row = get_narrow_cpr_row(_StubService(_frame(_narrow_this_week)), 'ACME', ROOT)
    select_narrow([row], 'both', 0.8)
    assert 'narrow_on' not in row


def test_a_series_that_stops_short_is_not_read_as_current():
    """The local candle cache holds entries keyed to today that stop a month
    short. Reporting July's CPR as August's is exactly the bug this scanner
    is meant to avoid, so the symbol is skipped instead."""
    stale = _frame(end='2026-07-24')
    assert get_narrow_cpr_row(_StubService(stale), 'ACME', ROOT) is None
    # One long weekend behind is still current.
    fresh = _frame(end='2026-08-21')
    assert get_narrow_cpr_row(_StubService(fresh), 'ACME', ROOT) is not None


def test_row_is_none_without_history():
    class _NoData(_StubService):
        def get_hist_data(self, *a, **kw):
            return None

    assert get_narrow_cpr_row(_NoData(_frame()), 'ACME', ROOT) is None


def test_row_carries_no_dataframe_into_the_cache():
    """The row is cached and JSON-serialised by the route; the per-period
    frames kept for the like-for-like context must not ride along."""
    import json
    row = get_narrow_cpr_row(_StubService(_frame()), 'ACME', ROOT)
    json.dumps(row)


# ── the dropdown filter ──────────────────────────────────────────────────

@pytest.mark.parametrize('w,m,tf,expected', [
    (0.1, 0.9, 'weekly',  True),
    (0.1, 0.9, 'monthly', False),
    (0.1, 0.9, 'both',    False),
    (0.9, 0.1, 'monthly', True),
    (0.1, 0.2, 'both',    True),
    (0.9, 0.9, 'weekly',  False),
])
def test_matches_narrow(w, m, tf, expected):
    row = {'weekly_ratio': w, 'monthly_ratio': m}
    assert matches_narrow(row, tf, 0.3) is expected


def test_a_tighter_threshold_is_a_strict_subset():
    """The whole point of the picker: turning it down can only ever remove
    rows, never introduce one."""
    rows = [{'symbol': f'S{i}', 'is_index': False,
             'weekly_ratio': i / 10, 'monthly_ratio': i / 10} for i in range(1, 11)]
    loose = {r['symbol'] for r in select_narrow(rows, 'both', 0.8)}
    tight = {r['symbol'] for r in select_narrow(rows, 'both', 0.3)}
    assert tight < loose
    assert len(loose) == 8 and len(tight) == 3


def test_a_reading_with_no_ratio_never_matches():
    """Too little history to average means the label came from the absolute
    fallback scale — a different measurement, not comparable to a ratio the
    user picked."""
    row = {'symbol': 'NEW', 'is_index': False,
           'weekly_ratio': None, 'monthly_ratio': None,
           'weekly_type': 'Narrow', 'monthly_type': 'Narrow'}
    assert select_narrow([row], 'weekly', 0.8) == []


def test_selection_ranks_tightest_first_by_the_ratio_that_decided_it():
    """For 'both' that is the weaker of the two — a row very tight on one
    timeframe and marginal on the other is not the tightest pair."""
    rows = [
        {'symbol': 'PAIR',  'is_index': False, 'weekly_ratio': 0.20, 'monthly_ratio': 0.20},
        {'symbol': 'ONESIDED', 'is_index': False, 'weekly_ratio': 0.01, 'monthly_ratio': 0.29},
        {'symbol': 'NIFTY', 'is_index': True,  'weekly_ratio': 0.28, 'monthly_ratio': 0.28},
    ]
    assert [r['symbol'] for r in select_narrow(rows, 'both', 0.3)] == [
        'NIFTY',      # indices first, as everywhere else on this page
        'PAIR', 'ONESIDED']


def test_the_default_threshold_is_the_tight_one():
    """A scan that answers "narrower than usual" matched two thirds of the F&O
    list on 2026-08-30. The default has to be a shortlist."""
    assert DEFAULT_NARROW_RATIO == 0.3
    row = {'symbol': 'X', 'is_index': False, 'weekly_ratio': 0.5, 'monthly_ratio': 0.5}
    assert matches_narrow(row, 'both') is False
    assert matches_narrow(row, 'both', 0.8) is True


# ── the scan ─────────────────────────────────────────────────────────────

def test_scan_returns_every_readable_symbol_so_the_filter_can_move():
    """The scan is timeframe-independent on purpose: the route caches it and
    re-filters, rather than re-reading 180 symbols when the dropdown moves."""
    result = filter_narrow_cpr(_StubService(_frame()), root_date=ROOT)
    symbols = [r['symbol'] for r in result['rows']]
    assert 'ACME' in symbols                    # not narrow, still present
    assert select_narrow(result['rows'], 'both') == []
    assert result['skipped'] == 0
    assert result['scanned'] == len(symbols)


def test_scan_selection_puts_the_indices_first():
    rows = filter_narrow_cpr(_StubService(_frame(_narrow_this_week)), root_date=ROOT)['rows']
    picked = select_narrow(rows, 'weekly', 0.8)
    assert picked[0]['is_index'] is True
    assert picked[-1]['symbol'] == 'ACME'


def test_scan_counts_unreadable_symbols_as_skipped():
    class _NoData(_StubService):
        def get_hist_data(self, *a, **kw):
            return None

    result = filter_narrow_cpr(_NoData(_frame()), root_date=ROOT)
    assert result['rows'] == []
    assert result['skipped'] == result['scanned'] == 5   # ACME + 4 indices
