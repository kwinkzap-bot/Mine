"""ICICI Direct (Breeze) adapter — the parts that can be tested without a session.

The Breeze API itself needs a daily login, so what is asserted here is
everything that decides *what* gets asked for and *how the answer is shaped*:
the security-master translation (RELIANCE → RELIND, BANKNIFTY → CNXBAN) and
the candle aggregation that builds the intervals Breeze does not serve.

Getting either wrong is silent: a bad stock_code fetches another instrument's
candles, and a bad aggregation anchor shifts every 3/15/60-minute bar off the
09:15 session open, which would move live signal candles.
"""

from datetime import datetime, timedelta

import pytest

from trading_app.service import icici_data_service as ids
from trading_app.service import icici_symbol_master as master


# Two rows from each real master file (2026-09-02), headers verbatim.
NSE_MASTER = (
    '"Token", "ShortName", "Series", "CompanyName", "ticksize", "Lotsize", "DateOfListing", '
    '"DateOfDeListing", "IssuePrice", "FaceValue", "ISINCode", "52WeeksHigh", "52WeeksLow", '
    '"LifeTimeHigh", "LifeTimeLow", "HighDate", "LowDate", "Symbol", "InstrumentType", "ExchangeCode"\n'
    '"2885","RELIND","EQ","RELIANCE INDUSTRIES",0.05,1,"","",0,10,"INE002A01018",0,0,0,0,"","","",0,"RELIANCE"\n'
    '"NIFTY 50","NIFTY","0","NIFTY 50",0,1,"","",0,0,"",0,0,0,0,"","","",0,"NIFTY 50"\n'
    '"NIFTY BANK","CNXBAN","0","NIFTY BANK",0,1,"","",0,0,"",0,0,0,0,"","","",0,"NIFTY BANK"\n'
)

FONSE_MASTER = (
    '"Token","InstrumentName","ShortName","Series","ExpiryDate","StrikePrice","OptionType",'
    '"CALevel","PermittedToTrade","IssueCapital","WarningQty","FreezeQty","CreditRating",'
    '"NormalMarketStatus","OddLotMarketStatus","SpotMarketStatus","AuctionMarketStatus",'
    '"NormalMarketEligibility","OddLotMarketEligibility","SpotMarketEligibility",'
    '"AuctionMarketEligibility","IssueRate","IssueStartDate","InterestPaymentDate",'
    '"IssueMaturityDate","MarginPercentage","MinimumLotQty","LotSize","TickSize","CompanyName",'
    '"ExchangeCode"\n'
    '"68407","FUTIDX","NIFTY","FUTURE","29-Sep-2026","0","XX",0,0,0,0,0,"",0,0,0,0,"","","","",0,'
    '"","","",16051815,0,65,10,"NIFTY 50","NIFTY 50"\n'
    '"35000","OPTIDX","CNXBAN","OPTION","29-Sep-2026","72600","CE",0,0,0,0,0,"",0,0,0,0,"","","","",0,'
    '"","","",0,0,30,5,"NIFTY BANK","NIFTY BANK"\n'
    '"36342","OPTSTK","RELIND","OPTION","27-Oct-2026","700","CE",0,0,0,0,0,"",0,0,0,0,"","","","",0,'
    '"","","",0,0,500,5,"RELIANCE INDUSTRIES","RELIANCE"\n'
)

FOBSE_MASTER = FONSE_MASTER.split("\n")[0] + "\n" + (
    '"842701","OPTIND","BSESEN","OPTION","03-Sep-2026","81000","PE",0,0,0,0,0,"",0,0,0,0,"","","","",0,'
    '"","","",0,0,20,5,"SENSEX","SENSEX"\n'
)


@pytest.fixture
def loaded_master(monkeypatch):
    """Load the module's table from the fixture text instead of downloading."""
    files = {
        'NSEScripMaster.txt': NSE_MASTER,
        'BSEScripMaster.txt': '',
        'FONSEScripMaster.txt': FONSE_MASTER,
        'FOBSEScripMaster.txt': FOBSE_MASTER,
    }
    monkeypatch.setattr(master, '_download', lambda: files)
    monkeypatch.setattr(master, '_read_disk_cache', lambda: None)
    monkeypatch.setattr(master, '_write_disk_cache', lambda table: None)
    monkeypatch.setattr(master, '_TABLE', None)
    monkeypatch.setattr(master, '_LOADED_AT', None)
    master.load(force=True)
    yield
    master._TABLE = None
    master._LOADED_AT = None


# ── Symbol translation ────────────────────────────────────────────────────

@pytest.mark.parametrize("root,exchange,expected", [
    ("RELIANCE", "NSE", "RELIND"),
    ("RELIANCE", "NFO", "RELIND"),
    ("NIFTY", "NFO", "NIFTY"),
    ("BANKNIFTY", "NFO", "CNXBAN"),     # the app's name, not ICICI's
    ("NIFTYBANK", "NSE", "CNXBAN"),     # as it arrives from 'NSE:NIFTYBANK-INDEX'
    ("NIFTY50", "NSE", "NIFTY"),        # as it arrives from 'NSE:NIFTY50-INDEX'
    ("SENSEX", "BFO", "BSESEN"),
])
def test_stock_code_translation(loaded_master, root, exchange, expected):
    assert master.stock_code(root, exchange) == expected


def test_lot_size_comes_from_the_master(loaded_master):
    assert master.lot_size("NIFTY", "NFO") == 65
    assert master.lot_size("BANKNIFTY", "NFO") == 30
    assert master.lot_size("RELIANCE", "NFO") == 500


def test_expiries_are_ascending_iso_dates(loaded_master):
    assert master.expiries("NIFTY", "NFO") == ["2026-09-29"]
    assert master.expiries("BSESEN", "BFO") == ["2026-09-03"]


def test_unknown_root_is_none_not_a_guess(loaded_master):
    """A wrong stock_code silently fetches another instrument — return None."""
    assert master.stock_code("NOTALISTEDNAME", "NFO") is None


def test_index_codes_survive_a_failed_download(monkeypatch):
    """The index chains must keep working when ICICI's master is unreachable."""
    def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(master, '_download', boom)
    monkeypatch.setattr(master, '_read_disk_cache', lambda: None)
    monkeypatch.setattr(master, '_TABLE', None)
    monkeypatch.setattr(master, '_LOADED_AT', None)
    try:
        assert master.stock_code("BANKNIFTY", "NFO") == "CNXBAN"
        assert master.stock_code("NIFTY", "NFO") == "NIFTY"
    finally:
        master._TABLE = None
        master._LOADED_AT = None


# ── Request shaping ───────────────────────────────────────────────────────

def test_breeze_expiry_is_an_instant_not_a_date():
    from datetime import date
    assert ids._breeze_expiry(date(2026, 9, 29)) == "2026-09-29T06:00:00.000Z"


def test_every_app_interval_maps_to_a_breeze_one():
    """A missing interval returns [] at runtime, which reads as 'no data'."""
    breeze_intervals = {'1second', '1minute', '5minute', '30minute', '1day'}
    for app_interval, (breeze, factor) in ids._INTERVAL_MAP.items():
        assert breeze in breeze_intervals, app_interval
        assert factor >= 0


# ── Candle aggregation ────────────────────────────────────────────────────

def _minute_bars(count, start=(9, 15)):
    base = datetime(2026, 9, 2, start[0], start[1], tzinfo=ids.IST)
    return [{
        'date': base + timedelta(minutes=i),
        'open': 100.0 + i, 'high': 101.0 + i, 'low': 99.0 + i, 'close': 100.5 + i,
        'volume': 10, 'oi': 1000 + i,
    } for i in range(count)]


def test_resample_anchors_on_the_session_open():
    """Anchoring at midnight instead of 09:15 shifts every odd multiple —
    a 15-minute bar would run 09:15-09:30 here but 09:15-09:29 there."""
    bars = _resampled = ids._resample(_minute_bars(15), 15 * 60)
    assert len(bars) == 1
    assert bars[0]['date'] == datetime(2026, 9, 2, 9, 15, tzinfo=ids.IST)


def test_resample_builds_correct_ohlcv():
    bars = ids._resample(_minute_bars(6), 3 * 60)
    assert len(bars) == 2
    first = bars[0]
    assert first['open'] == 100.0                      # first bar's open
    assert first['close'] == 102.5                     # last bar's close
    assert first['high'] == 103.0                      # max over the window
    assert first['low'] == 99.0                        # min over the window
    assert first['volume'] == 30                       # summed
    assert first['oi'] == 1002                         # OI is a level: last, not sum


def test_resample_leaves_a_partial_bar_at_the_end():
    """The in-progress bar has to survive, or the newest candle never appears."""
    bars = ids._resample(_minute_bars(4), 3 * 60)
    assert len(bars) == 2
    assert bars[1]['volume'] == 10


def test_group_calendar_weekly():
    days = [{
        'date': datetime(2026, 9, d, tzinfo=ids.IST),
        'open': float(d), 'high': float(d) + 1, 'low': float(d) - 1, 'close': float(d),
        'volume': 5,
    } for d in (1, 2, 3, 4, 7, 8)]          # Tue-Fri, then Mon-Tue of the next week
    weeks = ids._group_calendar(days, 'week')
    assert len(weeks) == 2
    assert weeks[0]['open'] == 1.0 and weeks[0]['close'] == 4.0
    assert weeks[0]['volume'] == 20
    assert weeks[1]['open'] == 7.0


# ── Response shaping ──────────────────────────────────────────────────────

def test_quote_from_row_maps_to_the_kite_shape():
    row = {'ltp': '24500.5', 'open': '24400', 'high': '24600', 'low': '24350',
           'previous_close': '24380', 'total_quantity_traded': '182000',
           'open_interest': '1250000', 'chnge_oi': '-4500'}
    q = ids._quote_from_row(row)
    assert q['last_price'] == 24500.5
    assert q['ohlc'] == {'open': 24400.0, 'high': 24600.0, 'low': 24350.0, 'close': 24380.0}
    assert q['volume'] == 182000
    assert q['oi'] == 1250000
    assert q['change_in_oi'] == -4500


def test_quote_ohlc_close_is_previous_close():
    """Kite's ohlc.close is the PREVIOUS session's close — every % change in
    the app is computed against it. Taking Breeze's 'close' would make the
    change read zero all day."""
    q = ids._quote_from_row({'ltp': '105', 'close': '105', 'previous_close': '100'})
    assert q['ohlc']['close'] == 100.0


def test_success_unwraps_both_shapes():
    assert ids._success({'Success': [{'a': 1}]}) == [{'a': 1}]
    assert ids._success({'Success': {'a': 1}}) == [{'a': 1}]
    assert ids._success({'Success': None, 'Error': 'expired'}) == []
    assert ids._success(None) == []


def test_parse_stamp_returns_ist():
    stamp = ids._parse_stamp('2026-09-02 09:15:00')
    assert stamp == datetime(2026, 9, 2, 9, 15, tzinfo=ids.IST)
    assert ids._parse_stamp('') is None


def test_trim_to_atm_keeps_the_ladder_around_the_middle():
    chain = {(s, 'CE'): {'ltp': 1.0} for s in range(24000, 25050, 50)}
    trimmed = ids._trim_to_atm(chain, 3)
    strikes = sorted({k[0] for k in trimmed})
    assert len(strikes) == 7                     # 3 either side of the midpoint
    assert strikes[3] == 24500


# ── Derivative resolution ─────────────────────────────────────────────────

def _adapter_with(rows, monkeypatch):
    """An inert adapter whose symbol master is the given instrument rows."""
    adapter = ids.IciciDataServiceAdapter(api_key="test")
    monkeypatch.setattr(adapter._symbols, 'instruments',
                        lambda exchange=None: rows if exchange == 'NFO' else [])
    return adapter


def test_option_symbol_resolves_to_breeze_option_params(loaded_master, monkeypatch):
    """The contract is read off the symbol master, never parsed out of the
    symbol text — Fyers writes weekly and monthly expiries differently, and a
    mis-parse would fetch a different expiry's candles without erroring."""
    from datetime import date
    rows = [{'instrument_token': 'NSE:BANKNIFTY26SEP2972600CE',
             'tradingsymbol': 'BANKNIFTY26SEP2972600CE', 'name': 'BANKNIFTY',
             'instrument_type': 'CE', 'strike': 72600.0,
             'expiry': date(2026, 9, 29), 'lot_size': 30}]
    adapter = _adapter_with(rows, monkeypatch)
    info = adapter._resolve('NSE:BANKNIFTY26SEP2972600CE')
    assert info == {
        'root': 'BANKNIFTY', 'stock_code': 'CNXBAN', 'exchange_code': 'NFO',
        'product_type': 'options', 'expiry_date': '2026-09-29T06:00:00.000Z',
        'right': 'call', 'strike_price': '72600',
        'symbol': 'NSE:BANKNIFTY26SEP2972600CE',
    }


def test_future_symbol_resolves_to_breeze_future_params(loaded_master, monkeypatch):
    from datetime import date
    rows = [{'instrument_token': 'NSE:NIFTY26SEPFUT', 'tradingsymbol': 'NIFTY26SEPFUT',
             'name': 'NIFTY', 'instrument_type': 'FUT', 'strike': 0.0,
             'expiry': date(2026, 9, 29), 'lot_size': 65}]
    adapter = _adapter_with(rows, monkeypatch)
    info = adapter._resolve('NSE:NIFTY26SEPFUT')
    assert info['product_type'] == 'futures'
    assert info['stock_code'] == 'NIFTY'
    assert info['right'] is None and info['strike_price'] is None


def test_unmappable_symbol_returns_none_rather_than_a_wrong_contract(loaded_master, monkeypatch):
    adapter = _adapter_with([], monkeypatch)
    assert adapter._resolve('NSE:NOSUCHTHING26SEP100CE') is None


def test_options_are_batched_into_one_chain_call_per_group(loaded_master, monkeypatch):
    """Breeze allows 100 requests/minute. One call per strike would make a
    40-strike OI Profile refresh cost 40 of them; the chain answers all 40
    in one, and this is the assertion that keeps it that way."""
    from datetime import date
    rows = [{'instrument_token': f'NSE:BANKNIFTY26SEP29{strike}CE',
             'tradingsymbol': f'BANKNIFTY26SEP29{strike}CE', 'name': 'BANKNIFTY',
             'instrument_type': 'CE', 'strike': float(strike),
             'expiry': date(2026, 9, 29), 'lot_size': 30}
            for strike in (72500, 72600, 72700)]
    adapter = _adapter_with(rows, monkeypatch)

    calls = []

    def fake_chain(stock_code, exchange_code, expiry_date, right):
        calls.append((stock_code, exchange_code, expiry_date, right))
        return {str(s): {'ltp': 100.0 + i, 'open_interest': 5000 + i}
                for i, s in enumerate((72500, 72600, 72700))}

    monkeypatch.setattr(adapter, '_chain_rows', fake_chain)
    quotes = adapter.quote([r['instrument_token'] for r in rows])

    assert len(calls) == 1
    assert calls[0] == ('CNXBAN', 'NFO', '2026-09-29T06:00:00.000Z', 'call')
    assert len(quotes) == 3
    assert quotes['NSE:BANKNIFTY26SEP2972500CE']['last_price'] == 100.0
    assert quotes['NSE:BANKNIFTY26SEP2972700CE']['oi'] == 5002


def test_quote_falls_back_to_the_last_good_value(loaded_master, monkeypatch):
    """A blank quote reaching a live algo is worse than a slightly old one."""
    from datetime import date
    rows = [{'instrument_token': 'NSE:BANKNIFTY26SEP2972600CE',
             'tradingsymbol': 'BANKNIFTY26SEP2972600CE', 'name': 'BANKNIFTY',
             'instrument_type': 'CE', 'strike': 72600.0,
             'expiry': date(2026, 9, 29), 'lot_size': 30}]
    adapter = _adapter_with(rows, monkeypatch)
    sym = rows[0]['instrument_token']

    monkeypatch.setattr(adapter, '_chain_rows',
                        lambda *a, **kw: {'72600': {'ltp': 250.0}})
    assert adapter.quote([sym])[sym]['last_price'] == 250.0

    monkeypatch.setattr(adapter, '_chain_rows', lambda *a, **kw: {})   # Breeze goes quiet
    assert adapter.quote([sym])[sym]['last_price'] == 250.0


# ── Session verification ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_verify_session_accepts_a_live_token(monkeypatch):
    seen = {}

    def fake_get(url, data=None, headers=None, timeout=None):
        seen.update(url=url, body=data, headers=headers)
        return _Resp(200, {'Success': {'idirect_userid': 'X1', 'session_token': 'abc'},
                           'Status': 200, 'Error': None})

    import requests
    monkeypatch.setattr(requests, 'get', fake_get)
    details = ids.verify_session('KEY', '12345678')
    assert details['idirect_userid'] == 'X1'
    # customerdetails is a GET carrying a JSON body — POST answers 405, so a
    # "tidy-up" to requests.post here would break the Brokers page.
    assert seen['url'].endswith('/customerdetails')
    assert seen['headers']['Content-Type'] == 'application/json'
    import json
    assert json.loads(seen['body']) == {'SessionToken': '12345678', 'AppKey': 'KEY'}


def test_verify_session_rejects_an_expired_token(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp(
        200, {'Success': None, 'Status': 500, 'Error': 'Invalid session'}))
    assert ids.verify_session('KEY', 'dead') is None


def test_verify_session_survives_the_api_being_down(monkeypatch):
    """The Brokers page must render 'Token expired', not raise a 500."""
    import requests

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(requests, 'get', boom)
    assert ids.verify_session('KEY', 'tok') is None


def test_verify_session_needs_both_halves():
    assert ids.verify_session('', 'tok') is None
    assert ids.verify_session('KEY', '') is None


# ── Provider selection ────────────────────────────────────────────────────

class _DeadAdapter:
    """An ICICI adapter with no SDK / a dead daily token."""
    session_ok = False

    def __init__(self, **kwargs):
        pass


class _LiveAdapter:
    session_ok = True

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def provider_env(monkeypatch):
    from trading_app.app.utils.user_env import UserEnvManager
    from trading_app.service import provider_logic as pl

    env = {
        'DATA_PROVIDER': 'ICICI',
        'BROKER_3_TYPE': 'fyers', 'BROKER_3_APP_ID': 'APP-100',
        'BROKER_3_ACCESS_TOKEN': 'fyers-token', 'BROKER_3_SECRET_KEY': 'fyers-secret',
        'BROKER_8_TYPE': 'icici', 'BROKER_8_API_KEY': 'key',
        'BROKER_8_SECRET_KEY': 'secret', 'BROKER_8_SESSION_TOKEN': 'token',
    }
    monkeypatch.setattr(UserEnvManager, 'get_user_var',
                        staticmethod(lambda user, key, default='': env.get(key, default)))
    monkeypatch.setattr(UserEnvManager, '_user_env_cache', {})
    monkeypatch.setattr(pl, 'FyersDataServiceAdapter',
                        lambda **kw: _LiveAdapter(broker='fyers', **kw))
    monkeypatch.setattr(pl, '_fyers_adapter_cache', {})
    monkeypatch.setattr(pl, '_icici_adapter_cache', {})
    return pl, env


def test_dead_icici_session_falls_back_to_fyers(provider_env, monkeypatch):
    """A provider that answers every fetch with [] is worse than none: the
    caller reads it as 'no candles today' and breaks. On 2026-09-03 that took
    the CPR-width endpoint down with a 500 the moment DATA_PROVIDER=ICICI was
    set without breeze-connect installed."""
    pl, _ = provider_env
    monkeypatch.setattr(ids, 'IciciDataServiceAdapter', _DeadAdapter)
    provider = pl.get_data_provider(user='Mine')
    assert isinstance(provider, _LiveAdapter)
    assert provider.kwargs['broker'] == 'fyers'


def test_live_icici_session_is_used(provider_env, monkeypatch):
    pl, _ = provider_env
    monkeypatch.setattr(ids, 'IciciDataServiceAdapter', _LiveAdapter)
    provider = pl.get_data_provider(user='Mine')
    assert isinstance(provider, _LiveAdapter)
    assert provider.kwargs.get('broker') != 'fyers'
    assert provider.kwargs['session_token'] == 'token'


def test_icici_without_credentials_never_reaches_the_adapter(provider_env, monkeypatch):
    pl, env = provider_env
    env['BROKER_8_SESSION_TOKEN'] = ''          # before the daily login
    monkeypatch.setattr(ids, 'IciciDataServiceAdapter', _LiveAdapter)
    provider = pl.get_data_provider(user='Mine')
    assert provider.kwargs['broker'] == 'fyers'
