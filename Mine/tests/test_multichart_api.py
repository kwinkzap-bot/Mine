"""/api/multichart — the Multichart page's three reads.

The provider is a fake recording every historical_data() call, so these run
without a broker and never touch create_app(). They pin the contract the
page's JS relies on: bar shape on the fake-IST grid, session-only bars, the
lookback per interval, and the live endpoint asking for today's minute bars
with the short cache TTL.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.multichart_service as svc
from route_app import build_route_app

IST = timezone(timedelta(hours=5, minutes=30))


def _row(y, m, d, hh, mm, o=100.0, h=101.0, l=99.0, c=100.5, v=10):
    return {'date': datetime(y, m, d, hh, mm, tzinfo=IST),
            'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}


class FakeFyersDataServiceAdapter:
    """Records calls; answers with whatever `rows` holds."""

    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = []

    def historical_data(self, symbol, from_date, to_date, interval, **kw):
        self.calls.append({'symbol': symbol, 'from': from_date, 'to': to_date,
                           'interval': interval, **kw})
        return list(self.rows)

    def last_history_error(self):
        return self.error

    def instruments(self, exchange):
        return [{'name': 'RELIANCE', 'instrument_type': 'FUT'},
                {'name': 'ACC', 'instrument_type': 'FUT'},
                {'name': 'NIFTY', 'instrument_type': 'FUT'},
                {'name': 'RELIANCE', 'instrument_type': 'CE'}]


@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


@pytest.fixture
def fake(monkeypatch):
    adapter = FakeFyersDataServiceAdapter()
    monkeypatch.setattr(svc, 'provider', lambda: adapter)
    return adapter


# ── symbol resolution ─────────────────────────────────────────────────────

def test_resolve_symbol_maps_indices_and_equities():
    assert svc.resolve_symbol('nifty') == 'NSE:NIFTY50-INDEX'
    assert svc.resolve_symbol('SENSEX') == 'BSE:SENSEX-INDEX'
    assert svc.resolve_symbol('RELIANCE') == 'NSE:RELIANCE-EQ'
    assert svc.resolve_symbol('M&M') == 'NSE:M&M-EQ'


@pytest.mark.parametrize('bad', ['', '../x', 'NSE:SBIN-EQ', 'a b', 'X' * 21])
def test_resolve_symbol_rejects_garbage(bad):
    with pytest.raises(svc.BadRequest):
        svc.resolve_symbol(bad)


# ── /candles ──────────────────────────────────────────────────────────────

def test_candles_requires_params(client):
    assert client.get('/api/multichart/candles').status_code == 400
    r = client.get('/api/multichart/candles?symbol=NIFTY&interval=7minute')
    assert r.status_code == 400
    assert 'interval' in r.get_json()['error']


def test_candles_401_when_no_provider(client, monkeypatch):
    def boom():
        raise svc.ProviderUnavailable('Fyers login required')
    monkeypatch.setattr(svc, 'provider', boom)
    r = client.get('/api/multichart/candles?symbol=NIFTY&interval=minute')
    assert r.status_code == 401
    body = r.get_json()
    assert body['success'] is False and body['auth_required'] is True


def test_candles_bar_shape_fake_ist_grid_and_session_filter(client, fake):
    fake.rows = [
        _row(2026, 9, 10, 9, 8),            # pre-open — dropped
        _row(2026, 9, 10, 9, 15, o=1, h=2, l=0.5, c=1.5, v=7),
        _row(2026, 9, 10, 15, 29),
        _row(2026, 9, 10, 15, 30),          # post-close auction — dropped
        {'date': datetime(2026, 9, 10, 9, 16, tzinfo=IST), 'open': None,
         'high': 1, 'low': 1, 'close': 1},  # broken row — dropped
    ]
    r = client.get('/api/multichart/candles?symbol=nifty&interval=3minute')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] and body['symbol'] == 'NIFTY'
    assert body['fy_symbol'] == 'NSE:NIFTY50-INDEX'
    assert body['seconds'] == 180
    bars = body['candles']
    assert [datetime.utcfromtimestamp(b['time']).strftime('%H:%M') for b in bars] == ['09:15', '15:29']
    first = bars[0]
    assert first == {'time': first['time'], 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 7}
    # 09:15 IST on the fake grid is 09:15 "UTC"
    assert datetime.utcfromtimestamp(first['time']).date() == date(2026, 9, 10)
    assert body['fetch_error'] is None


def test_candles_lookback_and_cache_ttl_per_interval(client, fake):
    fake.rows = [_row(2026, 9, 10, 10, 0)]
    client.get('/api/multichart/candles?symbol=RELIANCE&interval=60minute')
    hist, daily = fake.calls
    today = date.today()
    assert hist['symbol'] == 'NSE:RELIANCE-EQ'
    assert hist['interval'] == '60minute'
    assert hist['to'] == today.isoformat()
    assert hist['from'] == (today - timedelta(days=99)).isoformat()
    assert hist['cache_ttl'] == svc.HISTORY_CACHE_TTL and hist['allow_synthetic'] is True
    # The daily bars ride along so CPR anchors on exchange daily H/L/C.
    assert daily['interval'] == 'day' and daily['cache_ttl'] == 300.0


def test_candles_daily_interval_reuses_its_own_rows(client, fake):
    # Fyers stamps daily bars 05:30 IST (midnight UTC); ICICI at midnight IST.
    fake.rows = [_row(2026, 9, 9, 5, 30, o=1, h=3, l=0.5, c=2), _row(2026, 9, 10, 0, 0)]
    body = client.get('/api/multichart/candles?symbol=NIFTY&interval=day').get_json()
    assert len(fake.calls) == 1                      # no second fetch for daily
    assert len(body['candles']) == 2                 # no session filter on daily bars
    assert body['daily'][0] == {'date': '2026-09-09', 'o': 1, 'h': 3, 'l': 0.5, 'c': 2}
    # Both land on midnight of their day on the fake-IST grid, whatever the
    # broker's own stamp — the live merge builds today's bar at 00:00.
    stamps = [datetime.utcfromtimestamp(b['time']).strftime('%Y-%m-%d %H:%M') for b in body['candles']]
    assert stamps == ['2026-09-09 00:00', '2026-09-10 00:00']


def test_candles_passes_fetch_error_through_when_empty(client, fake):
    fake.rows = []
    fake.error = 'rate limited'
    body = client.get('/api/multichart/candles?symbol=NIFTY&interval=minute').get_json()
    assert body['candles'] == [] and body['fetch_error'] == 'rate limited'


def test_candles_synthetic_flag_survives(client, fake):
    row = _row(2026, 9, 10, 10, 0); row['synthetic'] = True
    fake.rows = [row]
    body = client.get('/api/multichart/candles?symbol=NIFTY&interval=minute').get_json()
    assert body['candles'][0]['synthetic'] is True


# ── /live ─────────────────────────────────────────────────────────────────

def test_live_asks_for_todays_minute_bars_with_short_ttl(client, fake, monkeypatch):
    monkeypatch.setattr(svc, 'market_open', lambda: True)
    fake.rows = [_row(2026, 9, 10, 9, 15, c=10), _row(2026, 9, 10, 9, 16, c=11)]
    r = client.get('/api/multichart/live?symbol=banknifty')
    assert r.status_code == 200
    body = r.get_json()
    (call,) = fake.calls
    today = date.today().isoformat()
    assert call['symbol'] == 'NSE:NIFTYBANK-INDEX'
    assert call['interval'] == 'minute' and call['from'] == today and call['to'] == today
    assert call['cache_ttl'] == svc.LIVE_CACHE_TTL and call['allow_synthetic'] is True
    assert body['ltp'] == 11 and body['market_open'] is True
    assert len(body['candles']) == 2


def test_live_requires_symbol(client):
    assert client.get('/api/multichart/live').status_code == 400


# ── /symbols ──────────────────────────────────────────────────────────────

def test_symbols_indices_first_then_sorted_futures(client, fake, monkeypatch):
    monkeypatch.setattr(svc, '_symbols_cache', {'data': None, 'ts': 0.0})
    import trading_app.filters.stock_list_store as store
    monkeypatch.setattr(store, 'get_fo_stocks', lambda kite, force_refresh=False: ['RELIANCE', 'ACC'])
    body = client.get('/api/multichart/symbols').get_json()
    names = [s['symbol'] for s in body['symbols']]
    assert names[:5] == ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX']
    assert names[5:] == ['ACC', 'RELIANCE']
    assert body['symbols'][0]['kind'] == 'INDEX' and body['symbols'][-1]['kind'] == 'FUT'


def test_symbols_requires_login(client):
    with client.session_transaction() as sess:
        sess.clear()
    assert client.get('/api/multichart/symbols').status_code == 401
