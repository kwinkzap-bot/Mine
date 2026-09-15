"""The two value-history routes, against a bare Flask app (see route_app.py)."""
import json
from datetime import date

import pytest

from route_app import build_route_app
from trading_app.algo.swing_momentum import sm_value_history as vh
from trading_app.app.routes import api as api_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(vh, 'SM_DAILY_VALUES_PATH', str(tmp_path / 'v.csv'))
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def _seed():
    c = {'id': 'a', 'index': 'NIFTY 500',
         'broker': {'instance': 7, 'broker_type': 'zerodha', 'broker_name': 'Kavin (Kite)'}}
    vh.upsert_rows([vh.snapshot_row(c, {'invested': 100, 'current': 110}, date(2026, 9, 15))])


def test_history_all(client):
    _seed()
    r = client.get('/api/algo/swing-momentum/value-history?scope=all')
    d = r.get_json()
    assert r.status_code == 200 and d['success']
    assert d['points'] == [{'date': '2026-09-15', 'invested': 100.0, 'current': 110.0}]


def test_history_broker_and_config(client):
    _seed()
    d = client.get('/api/algo/swing-momentum/value-history?scope=broker&key=7').get_json()
    assert d['points'][0]['current'] == 110.0
    d = client.get('/api/algo/swing-momentum/value-history?scope=config&key=a').get_json()
    assert d['points'][0]['invested'] == 100.0
    d = client.get('/api/algo/swing-momentum/value-history?scope=config&key=zzz').get_json()
    assert d['points'] == []


def test_history_rejects_bad_scope_and_missing_key(client):
    assert client.get('/api/algo/swing-momentum/value-history?scope=galaxy').status_code == 400
    assert client.get('/api/algo/swing-momentum/value-history?scope=broker').status_code == 400


def test_snapshot_writes_todays_rows_without_touching_a_broker(client, monkeypatch):
    cfgs = [{'id': 'a', 'index': 'NIFTY 500', 'investment': 100000,
             'broker': {'instance': 7, 'broker_type': 'zerodha', 'broker_name': 'Kavin (Kite)'},
             'live_entries': [{'symbol': 'X', 'qty': 10, 'entry_price': 100}],
             'monthly_investment_log': [{'date': '2026-08-01', 'amount': 5000}],
             'cash_balance': 250}]
    monkeypatch.setattr(api_mod, '_sm_load_live_configs', lambda: cfgs)
    monkeypatch.setattr(api_mod, '_sm_current_prices', lambda syms: {'X': 120.0})
    r = client.post('/api/algo/swing-momentum/value-history/snapshot')
    d = r.get_json()
    assert r.status_code == 200 and d['success'] and d['recorded'] == 1
    row = vh.load_rows()[0]
    assert row['date'] == d['date'] == str(date.today())
    assert row['invested'] == 105000.0          # capital + SIP
    assert row['current'] == 1200.0             # 10 × LTP, idle cash excluded
    assert row['deployed'] == 1000.0
    assert row['cash'] == 250.0


def test_valuer_returns_none_when_nothing_priced(monkeypatch):
    monkeypatch.setattr(api_mod, '_sm_current_prices', lambda syms: {})
    assert api_mod._sm_value_config({'live_entries': [{'symbol': 'X', 'qty': 1, 'entry_price': 1}]}) is None
    assert api_mod._sm_value_config({'live_entries': []}) is None


def test_ledger_path_is_resolved_per_call(tmp_path, monkeypatch):
    """The routes and the scheduler call the module with no path. A default
    bound at import time would send every test's writes into the real sheet
    (it did, once) — the patched attribute has to be what gets used."""
    monkeypatch.setattr(vh, 'SM_DAILY_VALUES_PATH', str(tmp_path / 'p.csv'))
    _seed()
    assert (tmp_path / 'p.csv').exists()
