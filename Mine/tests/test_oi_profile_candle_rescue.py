"""The OI Profile chart's history fetches keep the stale-cache rescue armed.

A Fyers "request limit reached" burst is routine during market hours (the
history cap is app-wide, 8 req/s, shared with the algos). The adapter answers
it by serving the last good candles for the identical key — but that rescue,
and the single-flight collapsing that halves the calls in the first place, are
both gated on `use_cache`. /api/oi-profile/candles passed `use_cache=False` to
stay live, so a throttled CE/PE leg came back empty and the page toasted
"Broker returned no candles for CE, PE — strike/expiry may be untraded or
wrong", blaming the contract for a throttle.

These pin both halves: the adapter's gating, and the endpoint opting in.
"""

import inspect
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.fyers_data_service as fds
from trading_app.service.fyers_data_service import FyersDataServiceAdapter

IST = timezone(timedelta(hours=5, minutes=30))
TOKEN = 'NSE:NIFTY26O0622700CE'
FROM = TO = '2026-09-30'


class FakeFyers:
    """Answers history() from a script — one entry per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def history(self, data=None):
        self.calls.append(data)
        return self.responses.pop(0) if self.responses else {'s': 'ok', 'candles': []}


def _ok(n=3):
    base = int(datetime(2026, 9, 30, 9, 15, tzinfo=IST).timestamp())
    return {'s': 'ok',
            'candles': [[base + i * 60, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10, 500]
                        for i in range(n)]}


# Deliberately NOT worded as a rate limit and not code -99/'malformed', so the
# fetch loop breaks out on the first attempt instead of sleeping through five
# retries — the rescue below is the same one a real throttle lands in.
_HARD_ERROR = {'s': 'error', 'code': -300, 'message': 'could not fetch history'}


@pytest.fixture(autouse=True)
def _clean_adapter_caches():
    with fds._FYERS_HIST_LOCK:
        fds._FYERS_HIST_CACHE.clear()
    yield
    with fds._FYERS_HIST_LOCK:
        fds._FYERS_HIST_CACHE.clear()


def _fetch(adapter, **kw):
    return adapter.historical_data(TOKEN, FROM, TO, 'minute', **kw)


def test_failed_fetch_serves_the_last_good_candles_when_caching_is_on():
    adapter = FyersDataServiceAdapter(FakeFyers(_ok(3), _HARD_ERROR))

    first = _fetch(adapter, use_cache=True, cache_ttl=0.0)
    assert len(first) == 3

    # cache_ttl=0 never satisfies a hit, so this really goes to the broker —
    # and the broker refuses it. The bars still come back.
    rescued = _fetch(adapter, use_cache=True, cache_ttl=0.0)
    assert len(adapter.fyers.calls) == 2
    assert [c['close'] for c in rescued] == [c['close'] for c in first]
    # The chart is fed, but the caller can still tell the page it went stale.
    assert 'could not fetch history' in (adapter.last_history_error() or '')


def test_use_cache_false_is_what_loses_the_rescue():
    """The regression this endpoint had — pinned so the gating stays visible."""
    adapter = FyersDataServiceAdapter(FakeFyers(_ok(3), _HARD_ERROR))

    assert len(_fetch(adapter, use_cache=True, cache_ttl=0.0)) == 3
    assert _fetch(adapter, use_cache=False) == []


def _code(func):
    """The function's source with comment-only lines dropped — both endpoints
    explain the use_cache=False regression in prose right above the call."""
    return '\n'.join(l for l in inspect.getsource(func).splitlines()
                     if not l.lstrip().startswith('#'))


@pytest.mark.parametrize('endpoint', ['oi_profile_candles', 'oi_profile_round_strike'])
def test_chart_endpoints_opt_into_the_cache(endpoint):
    """Neither block on /oi-profile may go back to use_cache=False."""
    from trading_app.app.routes import api

    src = _code(getattr(api, endpoint))
    assert 'historical_data(' in src, f'no historical_data() call in {endpoint}'
    assert 'use_cache=False' not in src, (
        'use_cache=False disables the adapter stale-cache rescue and its '
        'single-flight collapsing — pass a short cache_ttl instead')
    assert re.search(r'cache_ttl\s*=', src), 'expected an explicit short cache_ttl' 
