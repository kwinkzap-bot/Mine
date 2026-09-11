"""Contract-month enumeration on both provider adapters.

find_future_symbol / get_future_symbol were refactored to delegate to a new
list_future_contracts (which the EMA Confluence roll needs for real expiry
dates). Parity with the old front-month-only behaviour is the point of these
tests — every other caller in the app still goes through the old entry points.

Both adapters drop contracts whose expiry is already past, so these tests are
only meaningful relative to a *known* today. They pin one: ``_TODAY`` below is
frozen into both service modules by the fixtures, and every expiry in the
fixture rows is derived from it. Nothing here reads the wall clock, so the
calendar moving on cannot make them fail. (They previously hardcoded August
2026 expiries and started failing the moment real time passed them.)
"""
from datetime import date, datetime, timedelta

import pytest

from trading_app.service import fyers_data_service, kite_order_services
from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.kite_order_services import KiteService


# The frozen "today": mid-August, i.e. after the JUL contract expired and
# before the AUG one does, so AUG is the front month.
_TODAY = date(2026, 8, 10)

_JUL_EXPIRY = date(2026, 7, 28)   # already expired at _TODAY
_AUG_EXPIRY = date(2026, 8, 25)   # front month
_SEP_EXPIRY = date(2026, 9, 29)
_OCT_EXPIRY = date(2026, 10, 27)


class _FrozenDate(date):
    """A ``date`` whose ``today()`` is ``_TODAY``.

    Subclassing keeps every comparison against the real ``date`` objects in
    the fixture rows working exactly as it does in production.
    """

    @classmethod
    def today(cls):
        return _TODAY


def _freeze(monkeypatch):
    """Pin ``date.today()`` inside both adapters to ``_TODAY``."""
    monkeypatch.setattr(fyers_data_service, 'date', _FrozenDate)
    monkeypatch.setattr(kite_order_services, 'date', _FrozenDate)


def test_frozen_clock_actually_reaches_the_adapters(monkeypatch):
    """Guard on the freezing mechanism itself, so that a failure below reads as
    a real behaviour change rather than a clock that quietly came unstuck.

    Both adapters must resolve ``date`` at module scope for ``_freeze`` to
    reach them; a function-local ``from datetime import date`` would bypass it
    and put the real clock back (which is how this file rotted before)."""
    _freeze(monkeypatch)
    assert fyers_data_service.date.today() == _TODAY
    assert kite_order_services.date.today() == _TODAY
    assert _JUL_EXPIRY < _TODAY < _AUG_EXPIRY < _SEP_EXPIRY < _OCT_EXPIRY


def _fyers_rows():
    """Three monthly NHPC futures out of order, plus rows that must be ignored."""
    return [
        {'instrument_token': 'NSE:NHPC26OCTFUT', 'tradingsymbol': 'NHPC26OCTFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': _OCT_EXPIRY, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26AUGFUT', 'tradingsymbol': 'NHPC26AUGFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': _AUG_EXPIRY, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26SEPFUT', 'tradingsymbol': 'NHPC26SEPFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': _SEP_EXPIRY, 'lot_size': 6000},
        # ignored: expired, unparseable expiry, an option, and another root
        {'instrument_token': 'NSE:NHPC26JULFUT', 'tradingsymbol': 'NHPC26JULFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': _JUL_EXPIRY, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26NOVFUT', 'tradingsymbol': 'NHPC26NOVFUT',
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': None, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPC26AUG80CE', 'tradingsymbol': 'NHPC26AUG80CE',
         'name': 'NHPC', 'instrument_type': 'CE', 'expiry': _AUG_EXPIRY, 'lot_size': 5400},
        {'instrument_token': 'NSE:NHPCLTD26AUGFUT', 'tradingsymbol': 'NHPCLTD26AUGFUT',
         'name': 'NHPCLTD', 'instrument_type': 'FUT', 'expiry': _AUG_EXPIRY, 'lot_size': 100},
    ]


@pytest.fixture
def fyers(monkeypatch):
    adapter = FyersDataServiceAdapter.__new__(FyersDataServiceAdapter)
    rows = {'NFO': _fyers_rows(), 'BFO': [
        {'instrument_token': 'BSE:SENSEX26AUGFUT', 'tradingsymbol': 'SENSEX26AUGFUT',
         'name': 'SENSEX', 'instrument_type': 'FUT', 'expiry': _AUG_EXPIRY, 'lot_size': 20},
    ]}
    monkeypatch.setattr(FyersDataServiceAdapter, 'instruments',
                        lambda self, exchange: rows.get(exchange, []))
    # today is frozen before the AUG expiry, so the fixture means something
    _freeze(monkeypatch)
    return adapter


def test_fyers_lists_every_live_contract_ascending(fyers):
    got = fyers.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        'NSE:NHPC26AUGFUT', 'NSE:NHPC26SEPFUT', 'NSE:NHPC26OCTFUT']
    assert [c['expiry'] for c in got] == [_AUG_EXPIRY, _SEP_EXPIRY, _OCT_EXPIRY]
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


def _kite_rows():
    return [
        {'tradingsymbol': 'NHPC26OCTFUT', 'instrument_type': 'FUT',
         'expiry': datetime(_OCT_EXPIRY.year, _OCT_EXPIRY.month, _OCT_EXPIRY.day),
         'lot_size': 5400},                                            # datetime, not date
        {'tradingsymbol': 'NHPC26AUGFUT', 'instrument_type': 'FUT',
         'expiry': _AUG_EXPIRY, 'lot_size': 5400},
        {'tradingsymbol': 'NHPC26SEPFUT', 'instrument_type': 'FUT',
         'expiry': _SEP_EXPIRY, 'lot_size': 6000},
        {'tradingsymbol': 'NHPC26JULFUT', 'instrument_type': 'FUT',
         'expiry': _JUL_EXPIRY, 'lot_size': 5400},                     # expired
        {'tradingsymbol': 'NHPC26AUG80CE', 'instrument_type': 'CE',
         'expiry': _AUG_EXPIRY, 'lot_size': 5400},                     # not a future
    ]


@pytest.fixture
def kite(monkeypatch):
    svc = KiteService.__new__(KiteService)
    rows = _kite_rows()
    monkeypatch.setattr(KiteService, 'get_nfo_instruments',
                        lambda self, name: rows if name.lower() == 'nhpc' else [])
    _freeze(monkeypatch)
    return svc


def test_kite_lists_every_live_contract_ascending(kite):
    got = kite.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        'NHPC26AUGFUT', 'NHPC26SEPFUT', 'NHPC26OCTFUT']
    # the datetime expiry is normalised to a plain date (a datetime would not
    # compare equal here), like the old code did
    assert [c['expiry'] for c in got] == [_AUG_EXPIRY, _SEP_EXPIRY, _OCT_EXPIRY]


def test_kite_get_future_symbol_still_returns_the_front_month(kite):
    assert kite.get_future_symbol('NHPC') == 'NHPC26AUGFUT'
    assert kite.get_future_symbol('NOSUCH') is None


@pytest.mark.parametrize('today, expected_front', [
    (_JUL_EXPIRY - timedelta(days=1), 'NHPC26JULFUT'),
    (_JUL_EXPIRY,                     'NHPC26JULFUT'),   # expiry day still trades
    (_JUL_EXPIRY + timedelta(days=1), 'NHPC26AUGFUT'),   # rolled
    (_AUG_EXPIRY,                     'NHPC26AUGFUT'),
    (_AUG_EXPIRY + timedelta(days=1), 'NHPC26SEPFUT'),
    (_OCT_EXPIRY + timedelta(days=1), None),             # nothing left listed
])
def test_front_month_rolls_the_day_after_expiry(monkeypatch, today, expected_front):
    """The rollover itself, on both adapters: the front month changes the day
    *after* expiry, never on expiry day (the contract trades until its close)."""
    class _At(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(fyers_data_service, 'date', _At)
    monkeypatch.setattr(kite_order_services, 'date', _At)

    rows = _fyers_rows()
    fy = FyersDataServiceAdapter.__new__(FyersDataServiceAdapter)
    monkeypatch.setattr(FyersDataServiceAdapter, 'instruments',
                        lambda self, exchange: rows if exchange == 'NFO' else [])

    kt = KiteService.__new__(KiteService)
    kite_rows = _kite_rows()
    monkeypatch.setattr(KiteService, 'get_nfo_instruments',
                        lambda self, name: kite_rows if name.lower() == 'nhpc' else [])

    fyers_expected = f'NSE:{expected_front}' if expected_front else None
    assert fy.find_future_symbol('NHPC') == fyers_expected
    assert kt.get_future_symbol('NHPC') == expected_front
