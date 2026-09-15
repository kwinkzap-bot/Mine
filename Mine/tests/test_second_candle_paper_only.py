"""The 2nd 30-Sec Candle algo is PAPER ONLY (since 2026-09-15).

It used to place real option orders at every broker with BROKER_N_SC_ACTIVE.
That path is gone, not flagged off: this pins that the module carries no
order-placement code and that an entry/exit never so much as reads a
BROKER_* variable, so no env edit can bring live orders back by accident.

Hermetic: the algo is built with __new__ (no thread, no provider, no files);
the state/history paths are redirected into tmp_path.
"""
import inspect
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.algo.second_candle.second_candle_algo as mod
from trading_app.algo.second_candle.second_candle_algo import SecondCandleAlgo


FYERS_SYM = 'NSE:NIFTY25SEP23500CE'


class _Provider:
    """Quotes only — has no place_order, so any order attempt raises."""
    def __init__(self, ltp):
        self._ltp = ltp
        self.calls = []

    def ltp(self, symbols):
        self.calls.append(list(symbols))
        return {s: {'last_price': self._ltp} for s in symbols}


@pytest.fixture
def algo(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, '_STATE_FILE',       str(tmp_path / 'sc_state.json'))
    monkeypatch.setattr(mod, '_HISTORY_FILE',     str(tmp_path / 'sc_trades_history.json'))
    monkeypatch.setattr(mod, '_ALL_HISTORY_FILE', str(tmp_path / 'sc_trades_all_history.json'))

    a = SecondCandleAlgo.__new__(SecondCandleAlgo)
    a.username = 'test-user'
    a._state_lock = __import__('threading').Lock()
    a._expiry = date.today()
    a._lot_size = 65
    a._spot_fail_count = 0
    # A broker variable being read at all is the failure this file exists for.
    a._uvar = lambda key, default='': (
        pytest.fail(f'paper algo read a broker variable: {key}')
        if key.startswith('BROKER_') else default)
    a._strike_mode = lambda: 'premium250'
    a._fetch_fyers_chain_deltas = lambda provider, spot, t: {}
    a._select_strike = lambda opt_type, spot, provider, deltas: (
        23500.0, {'instrument_token': FYERS_SYM, 'tradingsymbol': 'NIFTY25SEP23500CE'})
    return a


def test_module_carries_no_order_placement_code():
    for name in ('_place_order', '_init_broker_svc', '_get_active_brokers'):
        assert not hasattr(SecondCandleAlgo, name), f'{name} is back — the algo can place orders again'
    # Everything after the module docstring (which may mention the old flags
    # to say they are dead) must be free of broker-order vocabulary.
    body = inspect.getsource(mod).split('"""', 2)[2]
    for token in ('place_order(', '_broker_map', 'BROKER_'):
        assert token not in body, f'{token!r} is back in second_candle_algo.py'


def test_entry_is_booked_as_paper_with_no_broker_legs(algo):
    provider = _Provider(ltp=250.0)
    algo._enter_trade('BUY', 23480.0, 23440.0, 23600.0, provider, spot=23482.5)

    state = algo._load_state()
    trade = state['active_trade']
    assert trade['paper'] is True
    assert trade['broker_entries'] == []
    assert trade['fyers_sym'] == FYERS_SYM
    assert trade['total_quantity'] == 65
    assert trade['opt_entry_price'] == 250.0
    assert state['traded_today'] is True
    # The only thing it asked the provider for was the option's LTP.
    assert provider.calls == [[FYERS_SYM]]


def test_exit_books_the_paper_pnl_and_places_nothing(algo):
    algo._enter_trade('SELL', 23480.0, 23520.0, 23360.0, _Provider(ltp=200.0), spot=23478.0)
    algo._provider = _Provider(ltp=236.0)

    algo._exit_trade('TARGET', 23360.0)

    state = algo._load_state()
    assert state['active_trade'] is None
    assert state['last_exit']['reason'] == 'TARGET'
    history = json.load(open(mod._HISTORY_FILE))
    rec = history[0]
    assert rec['paper'] is True
    assert rec['broker_entries'] == []
    assert rec['opt_entry_price'] == 200.0 and rec['opt_exit_price'] == 236.0
    assert rec['opt_pnl_pts'] == 36.0
    assert rec['opt_pnl_inr'] == 36.0 * mod._SINGLE_LOT_QTY


def test_exit_of_a_pre_paper_trade_still_prices_off_its_broker_leg(algo):
    """A state file written before 2026-09-15 has the symbol only on its
    broker_entries — the exit must still find it (and still not trade)."""
    state = algo._load_state()
    state['active_trade'] = {
        'direction': 'BUY', 'entry_spot': 23400.0, 'entry_time': '2026-09-15T09:16:30',
        'sl_level': 23380.0, 'target_level': 23460.0, 'strike': 23400, 'option_type': 'CE',
        'lot_size': 65, 'expiry': str(date.today()), 'opt_entry_price': 180.0,
        'broker_entries': [{'broker_idx': 1, 'broker_type': 'zerodha', 'order_id': '1',
                            'entry_success': True, 'fyers_sym': FYERS_SYM, 'quantity': 325}],
    }
    algo._save_state(state)
    algo._provider = _Provider(ltp=190.0)

    algo._exit_trade('MANUAL', 23430.0)

    rec = json.load(open(mod._HISTORY_FILE))[0]
    assert rec['opt_exit_price'] == 190.0
    assert algo._load_state()['active_trade'] is None
