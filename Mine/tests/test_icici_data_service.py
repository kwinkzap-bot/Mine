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


def test_one_second_is_raw_and_thirty_second_still_aggregates():
    """The 1-second key must not disturb the 30-second one built off it.

    Factor 1 is what makes _history_for_info pass bar_seconds=None into
    _second_history, which is what skips _resample and returns Breeze's base
    series untouched. A factor of 30 on the same base interval is the
    30-second bar the live algos trade off — if adding the raw key ever
    changed that entry, every 30-second signal candle would move.
    """
    assert ids._INTERVAL_MAP['1second'] == ('1second', 1)
    assert ids._INTERVAL_MAP['30second'] == ('1second', 30)


def test_flush_second_windows_only_drops_that_symbols_raw_windows():
    """The tape flushes its own 1-second windows so they cannot evict the
    aggregated day caches the live 30-second algos depend on."""
    ids._CHUNK_CACHE.clear()
    ids._chunk_cache_candles = 0
    ids._chunk_cache_put('NSE:AFUT:1second:2026-09-09T09:15:09:30', [{'x': 1}])
    ids._chunk_cache_put('NSE:AFUT:30s:2026-09-09', [{'x': 2}])
    ids._chunk_cache_put('NSE:BFUT:1second:2026-09-09T09:15:09:30', [{'x': 3}])

    assert ids.flush_second_windows('NSE:AFUT') == 1
    assert 'NSE:AFUT:30s:2026-09-09' in ids._CHUNK_CACHE          # untouched
    assert 'NSE:BFUT:1second:2026-09-09T09:15:09:30' in ids._CHUNK_CACHE
    assert ids._chunk_cache_candles == 2                          # count kept honest
    ids._CHUNK_CACHE.clear()
    ids._chunk_cache_candles = 0


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
    # The quote cache is process-global on purpose (provider_logic rebuilds the
    # adapter per request and shouldn't throw the data away), and quote() now
    # SERVES from it within _QUOTE_TTL_SEC. Two tests in this file quote the
    # same contract, so without this each would inherit the other's price.
    with ids._QUOTE_LOCK:
        ids._QUOTE_CACHE.clear()
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


# ── Quote budget: the read-through cache ──────────────────────────────────

def test_a_repeat_quote_inside_the_ttl_costs_no_breeze_request(loaded_master, monkeypatch):
    """Six algo threads poll the same NIFTY spot on a 1s loop. Before the
    read-through cache each poll was its own Breeze request, which is how a
    ~100/min budget went to duplicates and the daily quota died before noon."""
    adapter = _adapter_with([], monkeypatch)
    monkeypatch.setattr(adapter, '_resolve', lambda s: {
        'stock_code': s, 'exchange_code': 'NSE', 'product_type': 'cash',
        'expiry_date': None, 'right': None, 'strike_price': None, 'symbol': s})
    calls = []
    monkeypatch.setattr(adapter, '_get_quote_row',
                        lambda info: calls.append(info['symbol']) or {'ltp': 100.0})

    assert adapter.ltp(['NSE:SBIN-EQ'])['NSE:SBIN-EQ']['last_price'] == 100.0
    assert adapter.ltp(['NSE:SBIN-EQ'])['NSE:SBIN-EQ']['last_price'] == 100.0
    assert adapter.ltp(['NSE:SBIN-EQ', 'NSE:SBIN-EQ'])            # deduped
    assert len(calls) == 1


def test_max_age_zero_always_refetches(loaded_master, monkeypatch):
    adapter = _adapter_with([], monkeypatch)
    monkeypatch.setattr(adapter, '_resolve', lambda s: {
        'stock_code': s, 'exchange_code': 'NSE', 'product_type': 'cash',
        'expiry_date': None, 'right': None, 'strike_price': None, 'symbol': s})
    calls = []
    monkeypatch.setattr(adapter, '_get_quote_row',
                        lambda info: calls.append(info['symbol']) or {'ltp': 100.0})

    adapter.quote(['NSE:SBIN-EQ'], max_age=0)
    adapter.quote(['NSE:SBIN-EQ'], max_age=0)
    assert len(calls) == 2


# ── An index's option root is not its spot body ───────────────────────────

def test_index_spot_body_maps_to_its_option_root():
    """'NSE:NIFTY50-INDEX' carries the body NIFTY50; the F&O master files its
    options under NIFTY. Equity roots pass through untouched."""
    assert ids._option_root('NIFTY50') == 'NIFTY'
    assert ids._option_root('NIFTYBANK') == 'BANKNIFTY'
    assert ids._option_root('SBIN') == 'SBIN'


def test_chain_rows_carry_a_tradable_symbol(loaded_master, monkeypatch):
    """Every chain entry needs a symbol: the Second Candle algo skips any
    strike without one, so an unmapped root silently costs it the whole chain
    even when the premiums are perfectly good."""
    adapter = _adapter_with([], monkeypatch)
    monkeypatch.setattr(adapter, '_nearest_expiry', lambda *a: '2026-09-08T06:00:00.000Z')
    monkeypatch.setattr(adapter, '_chain_rows',
                        lambda code, exch, exp, right: {'23850': {'ltp': 90.0}})
    seen_roots = []
    monkeypatch.setattr(adapter, 'find_option_symbol',
                        lambda root, strike, ot, **kw:
                            seen_roots.append(root) or f'NSE:{root}269082{int(strike)}{ot}')

    raw = adapter.get_option_chain_raw('NSE:NIFTY50-INDEX', strikecount=0)
    assert seen_roots and set(seen_roots) == {'NIFTY'}
    assert all(e['symbol'] for e in raw.values())


# ── Daily-quota failover: quotes move to Fyers, candles do not ────────────

@pytest.fixture(autouse=True)
def _reset_quota_flag():
    with ids._QUOTA_LOCK:
        ids._QUOTA_EXHAUSTED_ON = None
    yield
    with ids._QUOTA_LOCK:
        ids._QUOTA_EXHAUSTED_ON = None


def test_only_the_daily_quota_error_sets_the_flag():
    assert ids._note_quota_error('Limit exceed: API call per day: ') is True
    assert ids._quota_exhausted() is True
    with ids._QUOTA_LOCK:
        ids._QUOTA_EXHAUSTED_ON = None
    assert ids._note_quota_error('Limit exceed: API call per minute') is False
    assert ids._note_quota_error('Session expired') is False
    assert ids._quota_exhausted() is False


def test_a_price_breeze_will_not_serve_comes_from_fyers(loaded_master, monkeypatch):
    """Over quota Breeze refuses as a {'Status': 5} body on some endpoints and
    as a non-JSON response on others, so the failover keys off the missing
    price rather than off recognising the error."""
    adapter = _adapter_with([], monkeypatch)
    monkeypatch.setattr(adapter, '_resolve', lambda s: {
        'stock_code': s, 'exchange_code': 'NSE', 'product_type': 'cash',
        'expiry_date': None, 'right': None, 'strike_price': None, 'symbol': s})
    class _OverQuota:                      # the SDK cannot parse Breeze's reply
        def get_quotes(self, **kw):
            raise ValueError('Expecting value: line 1 column 1 (char 0)')
    adapter.breeze = _OverQuota()
    monkeypatch.setattr(ids, '_fyers_quotes',
                        lambda syms: {s: {'last_price': 23766.7} for s in syms})

    got = adapter.ltp(['NSE:NIFTY50-INDEX'])
    assert got['NSE:NIFTY50-INDEX']['last_price'] == 23766.7


def test_a_stale_cache_entry_never_beats_a_live_fyers_price(loaded_master, monkeypatch):
    adapter = _adapter_with([], monkeypatch)
    monkeypatch.setattr(adapter, '_resolve', lambda s: {
        'stock_code': s, 'exchange_code': 'NSE', 'product_type': 'cash',
        'expiry_date': None, 'right': None, 'strike_price': None, 'symbol': s})
    monkeypatch.setattr(adapter, '_get_quote_row', lambda info: {'ltp': 100.0})
    assert adapter.ltp(['NSE:SBIN-EQ'])['NSE:SBIN-EQ']['last_price'] == 100.0

    monkeypatch.setattr(adapter, '_get_quote_row', lambda info: None)   # Breeze goes quiet
    monkeypatch.setattr(ids, '_fyers_quotes', lambda syms: {s: {'last_price': 111.0} for s in syms})
    assert adapter.quote(['NSE:SBIN-EQ'], max_age=0)['NSE:SBIN-EQ']['last_price'] == 111.0


def test_candles_stay_on_breeze_when_quotes_have_failed_over(loaded_master, monkeypatch):
    """The quota ceiling is on the quote endpoints; history keeps answering,
    and Fyers intraday history is what ICICI was brought in to replace."""
    adapter = _adapter_with([], monkeypatch)
    ids._note_quota_error('Limit exceed: API call per day: ')
    monkeypatch.setattr(ids, '_fyers_quotes',
                        lambda syms: pytest.fail('history must not go to Fyers'))
    seen = {}
    monkeypatch.setattr(adapter, '_history_for_info',
                        lambda info, *a, **kw: seen.setdefault('root', info['root']) or [{'close': 1}])
    monkeypatch.setattr(adapter, '_resolve', lambda s: {
        'root': 'NIFTY', 'stock_code': s, 'exchange_code': 'NSE',
        'product_type': 'cash', 'expiry_date': None, 'right': None,
        'strike_price': None, 'symbol': s})

    assert adapter.historical_data('NSE:NIFTY50-INDEX', '2026-09-07', '2026-09-07', 'minute')
    assert seen['root'] == 'NIFTY'
