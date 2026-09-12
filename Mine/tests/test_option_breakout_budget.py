"""The 30-Sec Option Breakout request budget charges Breeze requests, not
slice reads.

The ceiling exists because the 5,000 Breeze calls a day are shared with the
live algos. Until 2026-09-12 it was settled with the number of slices the walk
READ, cache hits included — so a sweep over a year a run had just walked
charged its ~1,000 cached slices and dropped the oldest three months of
sessions that would have cost nothing. These drive `_ob_walk` with a fake
adapter whose cache is under the test's control.
"""

from datetime import date, datetime, timedelta

from trading_app.app.routes import api
from trading_app.service import icici_data_service as ids

CUTOFF = 15 * 60 + 25
COMBO = (2, 2.0, 1.0)


def _bars(day, n=30):
    """A first slice that settles the trade: a breakout on bar 3, target on
    bar 4, so the walk never needs a second slice."""
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111),
           (111, 140, 110, 139)] + [(139, 139, 139, 139)] * (n - 4)
    t0 = datetime(day.year, day.month, day.day, 9, 15)
    return [{'date': t0 + timedelta(seconds=30 * i), 'open': o, 'high': h,
             'low': l, 'close': c, 'volume': 1} for i, (o, h, l, c) in enumerate(seq)]


class FakeAdapter:
    """Serves every slice; `cached` days answer without a Breeze request."""

    def __init__(self, cached=()):
        self.cached = set(cached)

    def historical_option_minutes(self, *a, **k):
        return []

    def option_session_window_count(self, day):
        return 25

    def historical_option_window(self, root, expiry, strike, right, day, i, **k):
        if day not in self.cached:
            ids._HIST_ERROR.requests = getattr(ids._HIST_ERROR, 'requests', 0) + 1
        return _bars(day) if i == 0 else []

    @staticmethod
    def last_history_error():
        return None

    history_requests_made = staticmethod(ids.IciciDataServiceAdapter.history_requests_made)


def _plan(days):
    return [{'day': d, 'expiry': d, 'strike': 100, 'option_type': r, 'spot_open': 100.0}
            for d in days for r in ('CE', 'PE')]


def _days(n):
    return [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]


def test_cached_slices_cost_nothing_against_the_budget(monkeypatch):
    monkeypatch.setattr(api, '_OB_REQUEST_BUDGET', 4 * api._Budget.RESERVE)
    days = _days(40)
    adapter = FakeAdapter(cached=days)          # everything already on disk
    results, _notes, stats = api._ob_walk(adapter, 'NIFTY', 'NFO', _plan(days),
                                          [COMBO], CUTOFF)
    assert stats['sessions_skipped'] == 0
    assert stats['requests'] == 0
    assert stats['windows'] == 80               # read, but not paid for
    assert all(r['trades'][COMBO] for r in results)


def test_cold_sessions_are_charged_and_the_newest_are_kept(monkeypatch):
    # A cold contract costs one request here, and a session reserves 18 while
    # in flight, so 40 lets twelve sessions through before the ceiling bites.
    monkeypatch.setattr(api, '_OB_REQUEST_BUDGET', 40)
    days = _days(40)
    adapter = FakeAdapter(cached=())
    results, _notes, stats = api._ob_walk(adapter, 'NIFTY', 'NFO', _plan(days),
                                          [COMBO], CUTOFF)
    assert stats['requests'] == stats['sessions_run'] * 2
    assert stats['sessions_skipped'] > 0
    assert stats['ran_to'] == days[-1]          # newest first, oldest dropped
    assert stats['requests'] <= 40
    assert stats['sessions_run'] == 12


def test_the_progress_callback_hears_the_running_request_tally():
    days = _days(3)
    seen = []
    api._ob_walk(FakeAdapter(cached=days[:1]), 'NIFTY', 'NFO', _plan(days),
                 [COMBO], CUTOFF, on_leg=lambda label, paid: seen.append(paid))
    assert len(seen) == 6 and max(seen) == 4    # two cold sessions × two legs
