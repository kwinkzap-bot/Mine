"""Contract-month enumeration on both provider adapters.

find_future_symbol / get_future_symbol were refactored to delegate to a new
list_future_contracts (which the EMA Confluence roll needs for real expiry
dates). Parity with the old front-month-only behaviour is the point of these
tests — every other caller in the app still goes through the old entry points.
"""
from datetime import date, datetime

import pytest

from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.kite_order_services import KiteService


def _fyers_rows():
    """Three monthly NHPC futures out of order, plus rows that must be ignored."""
    return [
        {'instrument_token': 'NSE:NHPC26OCTFUT', 'tradingsymbol': 'NHPC26OCTFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': date(2026, 10, 27), 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26AUGFUT', 'tradingsymbol': 'NHPC26AUGFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': date(2026, 8, 25), 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26SEPFUT', 'tradingsymbol': 'NHPC26SEPFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': date(2026, 9, 29), 'lot_size': 6000},
        # ignored: expired, unparseable expiry, an option, and another root
        {'instrument_token': 'NSE:NHPC26JULFUT', 'tradingsymbol': 'NHPC26JULFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': date(2026, 7, 28), 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26NOVFUT', 'tradingsymbol': 'NHPC26NOVFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': None, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26AUG80CE', 'tradingsymbol': 'NHPC26AUG80CE',
         'name': 'NHPC', 'instrument_type': 'CE', 'expiry': date(2026, 8, 25), 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPCLTD26AUGFUT', 'tradingsymbol': 'NHPCLTD26AUGFUT',
         'name': 'NHPCLTD', 'instrument_type': 'FUT', 'expiry': date(2026, 8, 25), 'lot_size': 100},
    ]


@pytest.fixture
def fyers(monkeypatch):
    adapter = FyersDataServiceAdapter.__new__(FyersDataServiceAdapter)
    rows = {'NFO': _fyers_rows(), 'BFO': [
        {'instrument_token': 'BSE:SENSEX26AUGFUT', 'tradingsymbol': 'SENSEX26AUGFUT',
         'name': 'SENSEX', 'instrument_type': 'FUT', 'expiry': date(2026, 8, 25), 'lot_size': 20},
    ]}
    monkeypatch.setattr(FyersDataServiceAdapter, 'instruments',
                        lambda self, exchange: rows.get(exchange, []))
    # today must be before the AUG expiry for the fixture to mean anything
    return adapter


def test_fyers_lists_every_live_contract_ascending(fyers):
    got = fyers.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        'NSE:NHPC26AUGFUT', 'NSE:NHPC26SEPFUT', 'NSE:NHPC26OCTFUT']
    assert [c['expiry'] for c in got] == [
        date(2026, 8, 25), date(2026, 9, 29), date(2026, 10, 27)]
    # lot size is read per contract, not carried from the front month
    assert [c['lot_size'] for c in got] == [5400, 6000, 5400]


def test_fyers_find_future_symbol_still_returns_the_front_month(fyers):
    assert fyers.find_future_symbol('NHPC') == 'NSE:NHPC26AUGFUT'


def test_fyers_unknown_root_still_returns_none(fyers):
    assert fyers.list_future_contracts('NOSUCH') == []
    assert fyers.find_future_symbol('NOSUCH') is None


def test_fyers_sensex_routes_to_bfo(fyers):
    got = fyers.list_future_contracts('SENSEX')
    assert [c['symbol'] for c in got] == ['BSE:SENSEX26AUGFUT']
    assert fyers.find_future_symbol('SENSEX') == 'BSE:SENSEX26AUGFUT'


@pytest.fixture
def kite(monkeypatch):
    svc = KiteService.__new__(KiteService)
    rows = [
        {'tradingsymbol': 'NHPC26OCTFUT', 'instrument_type': 'FUT',
         'expiry': datetime(2026, 10, 27), 'lot_size': 5400},          # datetime, not date
        {'tradingsymbol': 'NHPC26AUGFUT', 'instrument_type': 'FUT',
         'expiry': date(2026, 8, 25), 'lot_size': 5400},
        {'tradingsymbol': 'NHPC26SEPFUT', 'instrument_type': 'FUT',
         'expiry': date(2026, 9, 29), 'lot_size': 6000},
        {'tradingsymbol': 'NHPC26JULFUT', 'instrument_type': 'FUT',
         'expiry': date(2026, 7, 28), 'lot_size': 5400},               # expired
        {'tradingsymbol': 'NHPC26AUG80CE', 'instrument_type': 'CE',
         'expiry': date(2026, 8, 25), 'lot_size': 5400},               # not a future
    ]
    monkeypatch.setattr(KiteService, 'get_nfo_instruments',
                        lambda self, name: rows if name.lower() == 'nhpc' else [])
    return svc


def test_kite_lists_every_live_contract_ascending(kite):
    got = kite.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        'NHPC26AUGFUT', 'NHPC26SEPFUT', 'NHPC26OCTFUT']
    # the datetime expiry is normalised to a plain date (a datetime would not
    # compare equal here), like the old code did
    assert [c['expiry'] for c in got] == [
        date(2026, 8, 25), date(2026, 9, 29), date(2026, 10, 27)]


def test_kite_get_future_symbol_still_returns_the_front_month(kite):
    assert kite.get_future_symbol('NHPC') == 'NHPC26AUGFUT'
    assert kite.get_future_symbol('NOSUCH') is None
