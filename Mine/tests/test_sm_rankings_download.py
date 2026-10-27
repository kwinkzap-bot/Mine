"""Swing Momentum rankings: one shared, serialized Yahoo download per stale symbol."""
import threading

import pandas as pd
import pytest

from trading_app.app.routes import api


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in (api._sm_close_cache, api._sm_rankings_cache, api._sm_rankings_failed_at):
        d.clear()
    yield
    for d in (api._sm_close_cache, api._sm_rankings_cache, api._sm_rankings_failed_at):
        d.clear()


def _frame(symbols, days=300):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return pd.DataFrame({s: [100.0 + i * (1 + n) for i in range(days)]
                         for n, s in enumerate(symbols)}, index=idx)


def _wire(monkeypatch, universe, fail=()):
    calls = []

    def fake_download(symbols):
        calls.append(list(symbols))
        return _frame([s for s in symbols if s not in fail])

    monkeypatch.setattr(api, '_sm_download_closes', fake_download)
    from trading_app.service import dynamic_constituents as dc
    monkeypatch.setattr(dc.DynamicConstituentsService, 'get_constituents',
                        staticmethod(lambda ix: universe[ix]))
    return calls


def test_a_symbol_read_for_one_index_is_not_downloaded_again_for_another(monkeypatch):
    calls = _wire(monkeypatch, {'BIG': ['A', 'B', 'C'], 'SMALL': ['B', 'C']})
    assert len(api._sm_rankings_cached('BIG')) == 3
    assert len(api._sm_rankings_cached('SMALL')) == 2
    assert calls == [['A.NS', 'B.NS', 'C.NS']]


def test_concurrent_requests_for_one_index_share_one_download(monkeypatch):
    calls = _wire(monkeypatch, {'IX': ['A', 'B']})
    ts = [threading.Thread(target=api._sm_rankings_cached, args=('IX',)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls == [['A.NS', 'B.NS']]


def test_order_paths_still_download_fresh(monkeypatch):
    calls = _wire(monkeypatch, {'IX': ['A', 'B']})
    api._sm_compute_today_rankings('IX')
    api._sm_compute_today_rankings('IX')
    assert len(calls) == 2


def test_a_degraded_run_backs_off_instead_of_redownloading(monkeypatch):
    names = [f'S{i}' for i in range(20)]
    calls = _wire(monkeypatch, {'IX': names}, fail={f'S{i}.NS' for i in range(10)})
    ranked = api._sm_rankings_cached('IX')
    first = len(calls)
    assert ranked and len(ranked) == 10          # served, flagged degraded
    api._sm_rankings_cached('IX')
    assert len(calls) == first                   # inside the retry window: no new download
