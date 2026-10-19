"""KiteService._historical_with_retry: refusals are final, 30-second windows fit Kite's cap."""
from datetime import datetime

import pytest

from trading_app.service import kite_order_services as kos


class InputException(Exception):
    pass


class NetworkException(Exception):
    pass


class _FakeKite:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def historical_data(self, token, from_date, to_date, interval):
        self.calls.append((from_date, to_date, interval))
        if self.errors:
            raise self.errors.pop(0)
        return [{'date': to_date}]


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(kos.time, 'sleep', lambda s: None)
    monkeypatch.setattr(kos, '_global_rate_limiter', type('L', (), {'wait': lambda self: None})())
    monkeypatch.setattr(kos, '_historical_cache', kos.HistoricalDataCache())
    s = kos.KiteService.__new__(kos.KiteService)
    return s


def test_input_exception_is_not_retried(svc):
    svc.kite = _FakeKite([InputException('interval exceeds max limit: 2 days')] * 3)
    with pytest.raises(InputException):
        svc._historical_with_retry(1, datetime(2026, 10, 1), datetime(2026, 10, 19, 15), 'minute')
    assert len(svc.kite.calls) == 1


def test_transient_error_still_retried(svc):
    svc.kite = _FakeKite([NetworkException('boom')])
    out = svc._historical_with_retry(1, datetime(2026, 10, 19), datetime(2026, 10, 19, 15), 'minute')
    assert out and len(svc.kite.calls) == 2


def test_30second_window_is_clamped_to_two_days(svc):
    svc.kite = _FakeKite([])
    to = datetime(2026, 10, 19, 15, 30)
    svc._historical_with_retry(1, datetime(2026, 10, 9, 9), to, '30second')
    assert svc.kite.calls[0][0] == datetime(2026, 10, 17, 0, 0)


def test_other_intervals_keep_their_window(svc):
    svc.kite = _FakeKite([])
    start = datetime(2026, 10, 9, 9)
    svc._historical_with_retry(1, start, datetime(2026, 10, 19, 15, 30), '3minute')
    assert svc.kite.calls[0][0] == start


def test_long_window_is_split_to_kites_cap_and_stitched(svc):
    svc.kite = _FakeKite([])
    out = svc._historical_with_retry(1, datetime(2026, 1, 1, 9), datetime(2026, 10, 19, 15, 30), '5minute')
    calls = svc.kite.calls
    assert len(calls) == 3                       # 292 days in windows of <= 98
    assert all((to - frm).days < 100 for frm, to, _ in calls)
    assert calls[0][0] == datetime(2026, 1, 1, 9) and calls[-1][1] == datetime(2026, 10, 19, 15, 30)
    assert all(a[1] == b[0] for a, b in zip(calls, calls[1:]))     # contiguous
    assert len(out) == 3
