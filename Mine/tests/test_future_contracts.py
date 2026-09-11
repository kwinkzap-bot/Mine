"""Contract-month enumeration on both provider adapters.

find_future_symbol / get_future_symbol were refactored to delegate to a new
list_future_contracts (which the EMA Confluence roll needs for real expiry
dates). Parity with the old front-month-only behaviour is the point of these
tests — every other caller in the app still goes through the old entry points.

Both adapters filter on the real clock (`date.today()`), so the fixtures name
their contracts by month *offset* from the current month rather than by fixed
calendar dates: a hard-coded front month stops being the front month the day
it expires.
"""
from datetime import date, datetime

import pytest

from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.kite_order_services import KiteService


def _expiry(month_offset: int) -> date:
    """A plausible monthly-future expiry `month_offset` months away.

    Day 25 of a later month is always in the future and any day of an earlier
    month is always in the past, whatever today happens to be — so offset -1
    is reliably expired and offsets 1..3 are reliably live, with no dependence
    on today's day-of-month.
    """
    today = date.today()
    index = today.year * 12 + (today.month - 1) + month_offset
    year, month = divmod(index, 12)
    return date(year, month + 1, 25)


def _fut(root: str, expiry: date) -> str:
    """The tradingsymbol a monthly future on `root` carries, e.g. NHPC26SEPFUT."""
    return f"{root}{expiry:%y%b}FUT".upper()


EXPIRED = _expiry(-1)
FRONT   = _expiry(1)
SECOND  = _expiry(2)
THIRD   = _expiry(3)
FOURTH  = _expiry(4)   # only ever named, never given a usable expiry


def _fyers_rows():
    """Three monthly NHPC futures out of order, plus rows that must be ignored."""
    return [
        {'instrument_token': f"NSE:{_fut('NHPC', THIRD)}", 'tradingsymbol': _fut('NHPC', THIRD),
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': THIRD, 'lot_size': 5400},
        {'instrument_token': f"NSE:{_fut('NHPC', FRONT)}", 'tradingsymbol': _fut('NHPC', FRONT),
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': FRONT, 'lot_size': 5400},
        {'instrument_token': f"NSE:{_fut('NHPC', SECOND)}", 'tradingsymbol': _fut('NHPC', SECOND),
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': SECOND, 'lot_size': 6000},
        # ignored: expired, unparseable expiry, an option, and another root
        {'instrument_token': f"NSE:{_fut('NHPC', EXPIRED)}", 'tradingsymbol': _fut('NHPC', EXPIRED),
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': EXPIRED, 'lot_size': 5400},
        {'instrument_token': f"NSE:{_fut('NHPC', FOURTH)}", 'tradingsymbol': _fut('NHPC', FOURTH),
         'name': 'NHPC', 'instrument_type': 'FUT', 'expiry': None, 'lot_size': 5400},
        {'instrument_token': f"NSE:NHPC{FRONT:%y%b}80CE".upper(),
         'tradingsymbol': f"NHPC{FRONT:%y%b}80CE".upper(),
         'name': 'NHPC', 'instrument_type': 'CE', 'expiry': FRONT, 'lot_size': 5400},
        {'instrument_token': f"NSE:{_fut('NHPCLTD', FRONT)}", 'tradingsymbol': _fut('NHPCLTD', FRONT),
         'name': 'NHPCLTD', 'instrument_type': 'FUT', 'expiry': FRONT, 'lot_size': 100},
    ]


@pytest.fixture
def fyers(monkeypatch):
    adapter = FyersDataServiceAdapter.__new__(FyersDataServiceAdapter)
    rows = {'NFO': _fyers_rows(), 'BFO': [
        {'instrument_token': f"BSE:{_fut('SENSEX', FRONT)}", 'tradingsymbol': _fut('SENSEX', FRONT),
         'name': 'SENSEX', 'instrument_type': 'FUT', 'expiry': FRONT, 'lot_size': 20},
    ]}
    monkeypatch.setattr(FyersDataServiceAdapter, 'instruments',
                        lambda self, exchange: rows.get(exchange, []))
    return adapter


def test_fyers_lists_every_live_contract_ascending(fyers):
    got = fyers.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        f"NSE:{_fut('NHPC', FRONT)}", f"NSE:{_fut('NHPC', SECOND)}", f"NSE:{_fut('NHPC', THIRD)}"]
    assert [c['expiry'] for c in got] == [FRONT, SECOND, THIRD]
    # lot size is read per contract, not carried from the front month
    assert [c['lot_size'] for c in got] == [5400, 6000, 5400]


def test_fyers_find_future_symbol_still_returns_the_front_month(fyers):
    assert fyers.find_future_symbol('NHPC') == f"NSE:{_fut('NHPC', FRONT)}"


def test_fyers_unknown_root_still_returns_none(fyers):
    assert fyers.list_future_contracts('NOSUCH') == []
    assert fyers.find_future_symbol('NOSUCH') is None


def test_fyers_sensex_routes_to_bfo(fyers):
    got = fyers.list_future_contracts('SENSEX')
    assert [c['symbol'] for c in got] == [f"BSE:{_fut('SENSEX', FRONT)}"]
    assert fyers.find_future_symbol('SENSEX') == f"BSE:{_fut('SENSEX', FRONT)}"


@pytest.fixture
def kite(monkeypatch):
    svc = KiteService.__new__(KiteService)
    rows = [
        {'tradingsymbol': _fut('NHPC', THIRD), 'instrument_type': 'FUT',
         'expiry': datetime(THIRD.year, THIRD.month, THIRD.day), 'lot_size': 5400},  # datetime, not date
        {'tradingsymbol': _fut('NHPC', FRONT), 'instrument_type': 'FUT',
         'expiry': FRONT, 'lot_size': 5400},
        {'tradingsymbol': _fut('NHPC', SECOND), 'instrument_type': 'FUT',
         'expiry': SECOND, 'lot_size': 6000},
        {'tradingsymbol': _fut('NHPC', EXPIRED), 'instrument_type': 'FUT',
         'expiry': EXPIRED, 'lot_size': 5400},                                       # expired
        {'tradingsymbol': f"NHPC{FRONT:%y%b}80CE".upper(), 'instrument_type': 'CE',
         'expiry': FRONT, 'lot_size': 5400},                                         # not a future
    ]
    monkeypatch.setattr(KiteService, 'get_nfo_instruments',
                        lambda self, name: rows if name.lower() == 'nhpc' else [])
    return svc


def test_kite_lists_every_live_contract_ascending(kite):
    got = kite.list_future_contracts('NHPC')
    assert [c['symbol'] for c in got] == [
        _fut('NHPC', FRONT), _fut('NHPC', SECOND), _fut('NHPC', THIRD)]
    # the datetime expiry is normalised to a plain date (a datetime would not
    # compare equal here), like the old code did
    assert [c['expiry'] for c in got] == [FRONT, SECOND, THIRD]


def test_kite_get_future_symbol_still_returns_the_front_month(kite):
    assert kite.get_future_symbol('NHPC') == _fut('NHPC', FRONT)
    assert kite.get_future_symbol('NOSUCH') is None
