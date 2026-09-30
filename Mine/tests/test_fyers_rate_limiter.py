"""The app-wide Fyers pacer — the per-minute budget and its priority lanes.

Fyers meters ~10 req/s AND 200 req/min. Until 2026-09-30 only the first was
honoured, so 8 req/s (= 480/min) ran the account at more than twice the minute
cap all session: 5,227 × 429 before lunch on 30 Sep, arriving in bursts on a
perfect 3-minute period as the OI Crossover sweep landed on top of the chart
polls.

Demand that day was genuinely over the budget (~113 calls/min of charts, ~71 of
sweep, ~12 of OI recorder, against 178), so what is asserted here is not that
everything fits — it does not — but that the RIGHT thing gives: the charts and
the orders keep their share, the sweep yields and stretches, and no lane can
starve another to nothing.

The per-second smoothing is neutralised in most of these (`delay = 0`) so a
test is measuring the minute window and not sleeping through the pacer.
"""

import time

import pytest

from trading_app.service.fyers_data_service import (
    FyersRateLimiter,
    PRIORITY_BULK,
    PRIORITY_CHART,
    PRIORITY_CRITICAL,
)


@pytest.fixture
def limiter():
    lim = FyersRateLimiter(8.0)
    lim.delay = 0.0            # isolate the minute window from the 8 req/s pace
    return lim


def spend(lim, n, priority):
    """n calls that must all be allowed."""
    for i in range(n):
        assert lim.wait(priority, max_wait=0.0) is True, f'refused at {i}'


def test_the_shares_add_up_to_the_budget(limiter):
    assert sum(FyersRateLimiter.SHARE.values()) == FyersRateLimiter.PER_MIN_CAP
    # Under the broker's published 200/min, with headroom for calls the pacer
    # cannot see (the SDK's own token refresh, library-internal retries).
    assert FyersRateLimiter.PER_MIN_CAP < 200
    # A lane may only borrow into budget the lanes above it are not holding.
    assert (FyersRateLimiter.BORROW_CEIL[PRIORITY_BULK]
            < FyersRateLimiter.BORROW_CEIL[PRIORITY_CHART]
            <= FyersRateLimiter.BORROW_CEIL[PRIORITY_CRITICAL]
            == FyersRateLimiter.PER_MIN_CAP)


def test_a_busy_chart_window_cannot_starve_the_sweep(limiter):
    """The reason this model replaced a plain per-lane ceiling. The charts alone
    want ~113/min — more than a 104-wide bulk ceiling would have left — so a
    ceiling on the TOTAL meant the crossover sweep never ran again during market
    hours. Its guaranteed share is what stops that."""
    spend(limiter, 113, PRIORITY_CHART)
    spend(limiter, FyersRateLimiter.SHARE[PRIORITY_BULK], PRIORITY_BULK)


def test_the_sweep_stops_at_its_share_once_the_minute_is_busy(limiter):
    """It keeps its share; it does not get to keep going."""
    spend(limiter, 113, PRIORITY_CHART)
    spend(limiter, FyersRateLimiter.SHARE[PRIORITY_BULK], PRIORITY_BULK)
    assert limiter.wait(PRIORITY_BULK, max_wait=0.05) is False


def test_the_sweep_borrows_the_whole_budget_when_nothing_else_wants_it(limiter):
    """Out of hours, or on a page nobody has open, the sweep should finish in
    its usual ~100s rather than being rationed against demand that isn't there."""
    spend(limiter, FyersRateLimiter.BORROW_CEIL[PRIORITY_BULK], PRIORITY_BULK)
    assert limiter.wait(PRIORITY_BULK, max_wait=0.05) is False
    # …and the charts still find room on top of a sweep that has borrowed.
    assert limiter.wait(PRIORITY_CHART, max_wait=0.05) is True


def test_an_order_always_finds_room(limiter):
    """Nothing below CRITICAL may borrow the last of the minute: an order or an
    algo's quote must never queue behind a chart poll."""
    for lane in (PRIORITY_CHART, PRIORITY_BULK):
        lim = FyersRateLimiter(8.0)
        lim.delay = 0.0
        while lim.wait(lane, max_wait=0.0):
            pass
        assert lim.stats()['last_60s'] <= FyersRateLimiter.BORROW_CEIL[lane]
        assert lim.wait(PRIORITY_CRITICAL, max_wait=0.05) is True


def test_nothing_exceeds_the_broker_cap(limiter):
    """Even the top lane stops — the point is never to be refused by Fyers."""
    spend(limiter, FyersRateLimiter.PER_MIN_CAP, PRIORITY_CRITICAL)
    assert limiter.wait(PRIORITY_CRITICAL, max_wait=0.05) is False
    assert limiter.stats()['last_60s'] == FyersRateLimiter.PER_MIN_CAP


def test_a_refused_call_is_not_charged(limiter):
    """A give-up must not spend budget, or a polling caller would starve the
    lane it was trying to protect."""
    spend(limiter, 113, PRIORITY_CHART)
    spend(limiter, FyersRateLimiter.SHARE[PRIORITY_BULK], PRIORITY_BULK)
    before = limiter.stats()
    for _ in range(5):
        assert limiter.wait(PRIORITY_BULK, max_wait=0.0) is False
    after = limiter.stats()
    assert after['last_60s'] == before['last_60s']
    assert after['by_lane'][PRIORITY_BULK] == before['by_lane'][PRIORITY_BULK]


def test_budget_is_released_as_calls_age_out(limiter):
    """The window slides — it is not a bucket that has to expire on the minute."""
    spend(limiter, FyersRateLimiter.BORROW_CEIL[PRIORITY_BULK], PRIORITY_BULK)
    assert limiter.wait(PRIORITY_BULK, max_wait=0.0) is False

    with limiter.lock:                       # age the two oldest past 60s
        for i in range(2):
            ts, lane = limiter._window[i]
            limiter._window[i] = (ts - 61.0, lane)
    assert limiter.wait(PRIORITY_BULK, max_wait=0.0) is True
    assert limiter.stats()['by_lane'][PRIORITY_BULK] > 0


def test_an_unbounded_caller_waits_rather_than_being_refused(limiter):
    """Without max_wait the contract is 'you will go out, eventually' — an algo
    must never silently lose a call it did not ask to give up."""
    spend(limiter, FyersRateLimiter.PER_MIN_CAP, PRIORITY_CRITICAL)
    with limiter.lock:                       # all but one about to age out
        for i in range(len(limiter._window) - 1):
            ts, lane = limiter._window[i]
            limiter._window[i] = (ts - 61.0, lane)

    started = time.time()
    assert limiter.wait(PRIORITY_CRITICAL) is True
    assert time.time() - started < 1.0


def test_per_second_pace_still_applies_under_the_minute_budget():
    """The minute window replaced nothing — 8 req/s still smooths a burst."""
    lim = FyersRateLimiter(8.0)
    started = time.time()
    for _ in range(9):
        lim.wait(PRIORITY_CHART)
    assert time.time() - started >= 1.0


def test_stats_reports_the_spend_by_lane(limiter):
    """Which lane is spending the minute is the first question when the charts
    start lagging, so it has to be readable without a profiler."""
    spend(limiter, 10, PRIORITY_CHART)
    spend(limiter, 4, PRIORITY_BULK)
    spend(limiter, 1, PRIORITY_CRITICAL)
    stats = limiter.stats()
    assert stats['last_60s'] == 15
    assert stats['by_lane'] == {PRIORITY_CRITICAL: 1, PRIORITY_CHART: 10, PRIORITY_BULK: 4}
    assert stats['per_min_cap'] == FyersRateLimiter.PER_MIN_CAP
