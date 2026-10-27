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


def test_each_block_reads_its_own_flag_before_the_page_flag():
    from trading_app.app.routes.api import _oi_profile_page_context
    app = Flask(__name__)
    with app.test_request_context('/api/x', headers={'Referer': 'http://x/oi-profile'}):
        for block in ('chart', 'oi', 'premium_strikes', 'cpr', 'round_strike'):
            assert _oi_profile_page_context(block) == (f'oi_profile_{block}', 'oi_profile')


def test_indicator_popups_on_oi_profile_read_the_indicators_flag():
    from trading_app.service import multichart_service as ms
    app = Flask(__name__)
    with app.test_request_context('/api/multichart/live', headers={'Referer': 'http://x/oi-profile'}):
        assert ms._contexts() == ('oi_profile_indicators', 'oi_profile', 'multichart')
    with app.test_request_context('/api/multichart/live', headers={'Referer': 'http://x/multichart'}):
        assert ms._contexts() == ('multichart',)
    assert ms._contexts() == ('multichart',)   # algo threads: no request


def test_get_data_provider_honours_each_block_flag(monkeypatch):
    """The flag a block names is the broker it is served by, in its order."""
    env = {'DATA_PROVIDER': 'FYERS',
           'OI_PROFILE_DATA_PROVIDER': 'FYERS,KITE,ICICI',
           'OI_PROFILE_CHART_DATA_PROVIDER': 'KITE,FYERS',
           'OI_PROFILE_CPR_DATA_PROVIDER': 'ICICI,FYERS',
           'OI_PROFILE_OI_DATA_PROVIDER': 'ICICI,KITE'}
    from trading_app.app.utils.user_env import UserEnvManager
    monkeypatch.setattr(UserEnvManager, 'get_user_var',
                        staticmethod(lambda u, k, d='': env.get(k, d)))
    fyers, icici, kite = object(), object(), object()
    monkeypatch.setattr(provider_logic, '_get_fyers_adapter', lambda u: fyers)
    monkeypatch.setattr(provider_logic, '_get_icici_adapter', lambda u: icici)
    monkeypatch.setattr(provider_logic, 'get_kite', lambda user=None, instance=None: kite)
    monkeypatch.setattr(provider_logic, '_kite_serves_history', lambda k: True)

    get = lambda block, **kw: provider_logic.get_data_provider(
        user='Mine', context=(f'oi_profile_{block}', 'oi_profile'), **kw)
    assert get('chart') is kite
    assert get('cpr') is icici
    assert get('oi', exclude=('ICICI',)) is kite           # ICICI named but skipped
    assert get('premium_strikes') is fyers                  # unset -> page flag

    # The block's first broker is down: the next one in ITS list serves.
    monkeypatch.setattr(provider_logic, '_kite_serves_history', lambda k: False)
    assert get('chart') is fyers
