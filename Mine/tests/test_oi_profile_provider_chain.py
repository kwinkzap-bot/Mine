"""OI Profile data-provider hierarchy: historic context + Fyers<->ICICI fallbacks."""
from datetime import datetime, timedelta

from flask import Flask

from trading_app.service import provider_logic


def _ctx(end_date=None):
    from trading_app.app.routes.api import _oi_profile_page_context
    app = Flask(__name__)
    qs = f'?end_date={end_date}' if end_date else ''
    with app.test_request_context(f'/api/oi-profile/candles{qs}',
                                  headers={'Referer': 'http://x/oi-profile'}):
        return _oi_profile_page_context()


def test_live_request_uses_page_chain():
    assert _ctx() == ('oi_profile',)
    assert _ctx(datetime.now().strftime('%Y-%m-%d')) == ('oi_profile',)


def test_past_window_reads_historic_chain_first():
    past = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    assert _ctx(past) == ('oi_profile_historic', 'oi_profile')


def test_symbol_fallbacks_follow_flag_order_and_skip_kite(monkeypatch):
    env = {'OI_PROFILE_HISTORIC_DATA_PROVIDER': 'ICICI,FYERS,KITE',
           'OI_PROFILE_DATA_PROVIDER': 'FYERS,KITE,ICICI',
           'DATA_PROVIDER': 'FYERS'}
    from trading_app.app.utils.user_env import UserEnvManager
    monkeypatch.setattr(UserEnvManager, 'get_user_var',
                        staticmethod(lambda u, k, d='': env.get(k, d)))
    fyers, icici = object(), object()
    monkeypatch.setattr(provider_logic, '_get_fyers_adapter', lambda u: fyers)
    monkeypatch.setattr(provider_logic, '_get_icici_adapter', lambda u: icici)

    ctx = ('oi_profile_historic', 'oi_profile')
    assert provider_logic.get_symbol_fallbacks(icici, user='Mine', context=ctx) == [fyers]
    assert provider_logic.get_symbol_fallbacks(fyers, user='Mine', context='oi_profile') == [icici]

    monkeypatch.setattr(provider_logic, '_get_icici_adapter', lambda u: None)  # ICICI logged out
    assert provider_logic.get_symbol_fallbacks(fyers, user='Mine', context='oi_profile') == []


def test_round_strike_historic_flag_puts_historic_first():
    from trading_app.app.routes.api import _oi_profile_page_context
    app = Flask(__name__)
    with app.test_request_context('/api/oi-profile/round-strike',
                                  headers={'Referer': 'http://x/oi-profile'}):
        assert _oi_profile_page_context('round_strike', historic=True) == (
            'oi_profile_historic', 'oi_profile_round_strike', 'oi_profile')
        assert _oi_profile_page_context('round_strike', historic=False) == (
            'oi_profile_round_strike', 'oi_profile')
