"""A data-provider flag may list brokers in fallback order ("KITE,ICICI")."""
from unittest import mock

from trading_app.service import provider_logic as pl


class _K:
    access_token = 'tok'


def _resolve(flags, kite_ok):
    env = dict(flags)
    with mock.patch('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                    side_effect=lambda u, n, d='': env.get(n, d)), \
         mock.patch.object(pl, 'get_kite', return_value=_K()), \
         mock.patch.object(pl, '_kite_serves_history', return_value=kite_ok), \
         mock.patch.object(pl, '_get_icici_adapter', return_value='ICICI'), \
         mock.patch.object(pl, '_get_fyers_adapter', return_value='FYERS'):
        return pl.get_data_provider(user='u', context='oi_profile')


def test_kite_first_when_it_serves_history():
    assert isinstance(_resolve({'OI_PROFILE_DATA_PROVIDER': 'KITE,ICICI # c'}, True), _K)


def test_falls_to_icici_when_kite_refuses():
    assert _resolve({'OI_PROFILE_DATA_PROVIDER': 'KITE,ICICI'}, False) == 'ICICI'


def test_single_kite_is_served_as_is():
    assert isinstance(_resolve({'OI_PROFILE_DATA_PROVIDER': 'KITE', 'DATA_PROVIDER': ''}, False), _K)


def test_exclude_skips_icici_for_the_oi_chain():
    env = {'OI_PROFILE_DATA_PROVIDER': 'ICICI,KITE'}
    with mock.patch('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                    side_effect=lambda u, n, d='': env.get(n, d)), \
         mock.patch.object(pl, 'get_kite', return_value=_K()), \
         mock.patch.object(pl, '_kite_serves_history', return_value=True), \
         mock.patch.object(pl, '_get_icici_adapter', return_value='ICICI'):
        got = pl.get_data_provider(user='u', context='oi_profile', exclude=('ICICI',))
    assert isinstance(got, _K)
