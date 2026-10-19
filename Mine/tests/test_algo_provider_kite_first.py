"""algo_provider keeps Zerodha candles when no Fyers login exists."""
from trading_app.service import multichart_service as mc


def test_algo_provider_survives_missing_fyers(monkeypatch):
    def boom():
        raise mc.ProviderUnavailable('no fyers')
    monkeypatch.setattr(mc, 'provider', boom)
    ap = mc.algo_provider()
    rows = [{'date': 1, 'close': 2.0}]
    monkeypatch.setattr(mc.ZerodhaFirstHistory, '_from_kite', staticmethod(lambda *a: rows))
    assert ap.historical_data('NSE:NIFTY50-INDEX', '2026-10-19', '2026-10-19', 'minute') == rows


def test_algo_provider_without_fyers_or_kite_is_empty(monkeypatch):
    def boom():
        raise mc.ProviderUnavailable('no fyers')
    monkeypatch.setattr(mc, 'provider', boom)
    monkeypatch.setattr(mc.ZerodhaFirstHistory, '_from_kite', staticmethod(lambda *a: None))
    import trading_app.service.provider_logic as pl
    monkeypatch.setattr(pl, 'get_icici_adapter', lambda *a, **k: None)
    assert mc.algo_provider().historical_data('NSE:NIFTY50-INDEX', 'a', 'b', 'minute') == []
