"""Every live algo reads candles in its own ALGO_<NAME>_DATA_PROVIDER order."""
from trading_app.service import multichart_service as mc
from trading_app.service import provider_logic as pl


class _Adapter:
    def __init__(self, name, rows):
        self.name, self.rows, self.calls = name, rows, 0

    def historical_data(self, *a, **kw):
        self.calls += 1
        return list(self.rows)

    def find_future_symbol(self, root):
        return f'{self.name}:{root}FUT'


def _setup(monkeypatch, env, fyers_rows=(), icici_rows=(), kite_rows=None):
    from trading_app.app.utils.user_env import UserEnvManager
    monkeypatch.setattr(UserEnvManager, 'get_user_var',
                        staticmethod(lambda u, k, d='': env.get(k, d)))
    fy, ic = _Adapter('fyers', fyers_rows), _Adapter('icici', icici_rows)
    monkeypatch.setattr(pl, '_get_fyers_adapter', lambda u: fy)
    monkeypatch.setattr(pl, '_get_icici_adapter', lambda u: ic)
    monkeypatch.setattr(mc.ZerodhaFirstHistory, '_from_kite',
                        staticmethod(lambda *a: kite_rows))
    return fy, ic


def test_chain_reads_the_algos_own_flag_then_data_provider(monkeypatch):
    _setup(monkeypatch, {'ALGO_ST4_DATA_PROVIDER': 'ICICI,KITE', 'DATA_PROVIDER': 'FYERS'})
    assert mc.algo_chain('Mine', 'algo_st4') == ['ICICI', 'KITE', 'FYERS']
    assert mc.algo_chain('Mine', 'algo_cpr') == ['FYERS']          # unset -> DATA_PROVIDER


def test_default_order_is_fyers_kite_icici(monkeypatch):
    _setup(monkeypatch, {})
    assert mc.algo_chain('Mine', 'algo_cpr') == ['FYERS', 'KITE', 'ICICI']


def test_fyers_first_serves_when_it_has_bars(monkeypatch):
    fy, ic = _setup(monkeypatch, {'ALGO_CPR_DATA_PROVIDER': 'FYERS,KITE,ICICI'},
                    fyers_rows=[{'close': 1}], kite_rows=[{'close': 2}])
    rows = mc.algo_provider('algo_cpr', 'Mine').historical_data('NSE:NIFTY50-INDEX', 'a', 'b', 'minute')
    assert rows == [{'close': 1}] and ic.calls == 0


def test_empty_broker_hands_over_to_the_next_in_order(monkeypatch):
    fy, ic = _setup(monkeypatch, {'ALGO_CPR_DATA_PROVIDER': 'FYERS,KITE,ICICI'},
                    fyers_rows=[], kite_rows=None, icici_rows=[{'close': 3}])
    rows = mc.algo_provider('algo_cpr', 'Mine').historical_data('NSE:NIFTY50-INDEX', 'a', 'b', 'minute')
    assert rows == [{'close': 3}] and fy.calls == 1


def test_other_lookups_go_to_the_first_symbol_broker(monkeypatch):
    _setup(monkeypatch, {'ALGO_ST5_DATA_PROVIDER': 'KITE,ICICI,FYERS'})
    assert mc.algo_provider('algo_st5', 'Mine').find_future_symbol('NIFTY') == 'icici:NIFTYFUT'
