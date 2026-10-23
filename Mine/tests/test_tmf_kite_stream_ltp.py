"""30-Min Fakeout: Kite prices come from the WebSocket first, REST for the rest."""
from types import SimpleNamespace

from trading_app.algo.thirty_min_fakeout.tmf_algo import TMFAlgo as Algo
from trading_app.service import kite_ticker_feed as kf


class FakeKite:
    api_key, access_token = 'k', 'tok'

    def __init__(self):
        self.asked = []

    def ltp(self, keys):
        self.asked.append(list(keys))
        return {k: {'last_price': 50.0} for k in keys}


def _algo(monkeypatch, tokens, streamed):
    a = object.__new__(Algo)
    a._kite_token_svc = SimpleNamespace(kite=None, get_instrument_token=tokens.get)
    monkeypatch.setenv('KITE_WS_QUOTES', 'true')
    monkeypatch.setattr(kf, 'ws_quote',
                        lambda kite, tok, max_age=5.0: streamed.get(tok))
    return a


def test_streamed_symbols_skip_rest(monkeypatch):
    kite = FakeKite()
    a = _algo(monkeypatch, {'AAA': 1, 'BBB': 2}, {1: {'last_price': 10.0}, 2: {'last_price': 20.0}})
    a._kite_token_svc.kite = kite
    assert a._get_ltp_batch(kite, ['AAA', 'BBB'], False) == {'AAA': 10.0, 'BBB': 20.0}
    assert kite.asked == []


def test_missing_tick_falls_back_to_rest_for_that_symbol_only(monkeypatch):
    kite = FakeKite()
    a = _algo(monkeypatch, {'AAA': 1, 'BBB': 2}, {1: {'last_price': 10.0}})
    a._kite_token_svc.kite = kite
    out = a._get_ltp_batch(kite, ['AAA', 'BBB'], False)
    assert out['AAA'] == 10.0 and kite.asked == [['NSE:BBB']]


def test_flag_off_is_the_old_path(monkeypatch):
    kite = FakeKite()
    a = _algo(monkeypatch, {'AAA': 1}, {1: {'last_price': 10.0}})
    monkeypatch.setenv('KITE_WS_QUOTES', 'false')
    a._get_ltp_batch(kite, ['AAA'], False)
    assert kite.asked == [['NSE:AAA']]


def test_symbol_providers_never_touch_the_stream(monkeypatch):
    kite = FakeKite()
    a = _algo(monkeypatch, {'AAA': 1}, {1: {'last_price': 10.0}})
    assert a._get_ltp_batch(kite, ['AAA'], True) == {'AAA': 50.0}
    assert kite.asked == [['NSE:AAA-EQ']]


def test_kite_candles_are_fetched_by_instrument_token(monkeypatch):
    """Kite is addressed by numeric token; the bare symbol was 'invalid token'."""
    import pandas as pd
    from trading_app.Backtest import minute_candle_store
    a = object.__new__(Algo)
    a._kite_token = lambda provider, sym: 408065
    seen = {}
    monkeypatch.setattr(minute_candle_store, 'get_minute_history',
                        lambda prov, tok, sym, *a_, **k: seen.setdefault('tok', tok) and pd.DataFrame())
    a._fetch_today_candles(object(), False, 'INFY')
    assert seen['tok'] == 408065


def test_failed_candle_fetch_is_retried_not_no_setup():
    a = object.__new__(Algo)
    a._fetch_today_candles = lambda *args: None
    s = {'phase': 'pending_scan'}
    a._scan_one(object(), False, 'INFY', s, 100000, None)
    assert s['phase'] == 'pending_scan' and s['scan_retry_at'] > 0
