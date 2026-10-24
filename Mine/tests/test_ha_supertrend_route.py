"""/api/backtest/ha-supertrend — start, poll, and the rupee shape the Backtest page reads.

No broker and no create_app(): a bare Flask() with the real URL map, Fyers and the daily
store faked."""
import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from route_app import build_route_app
import trading_app.app.routes.api as api
import trading_app.filters.candle_store as candle_store


def _daily():
    idx = pd.date_range('2024-01-01', periods=260, freq='B')
    c = np.concatenate([np.linspace(200, 120, 90), np.linspace(120, 230, 90), np.linspace(230, 110, 80)])
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({'open': o, 'high': np.maximum(o, c) + 1, 'low': np.minimum(o, c) - 1,
                         'close': c, 'volume': 1000}, index=idx)


@pytest.fixture
def client(monkeypatch):
    app = build_route_app()
    app.secret_key = 'test'
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    monkeypatch.setattr(api, 'get_fyers_adapter', lambda: object())
    monkeypatch.setattr(api, '_fno_lot_sizes', lambda: {'AAA': 10, 'BBB': 20, 'NIFTY': 75})
    monkeypatch.setattr(candle_store, 'get_daily_history',
                        lambda fyers, token, key, days, end_date=None: _daily() if key.endswith('AAA') else None)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def _run(client, **body):
    body = {'start_date': '2024-03-01', 'end_date': '2025-01-10', **body}
    r = client.post('/api/backtest/ha-supertrend', json=body)
    assert r.status_code == 200, r.get_json()
    tid = r.get_json()['task_id']
    for _ in range(100):
        d = client.get(f'/api/backtest/ha-supertrend/status/{tid}').get_json()
        if d['status'] != 'running':
            return d
        time.sleep(0.05)
    raise AssertionError('task never finished')


def test_run_returns_sized_rupee_trades_and_a_summary(client):
    d = _run(client)
    assert d['success'] and d['trades']
    s = d['summary']
    assert s['symbols_scanned'] == 2 and s['symbols_no_data'] == 1 and s['symbols_with_trades'] == 1
    t = d['trades'][0]
    assert t['symbol'] == 'AAA' and t['signal_time'] >= '2024-03-01'
    assert abs(t['pnl_rupees'] - (t['gross_pnl_rupees'] - t['brokerage'])) < 0.02
    assert s['total_trades'] == len(d['trades'])
    assert abs(s['total_pnl_rupees'] - sum(x['pnl_rupees'] for x in d['trades'])) < 0.05
    assert s['pnl_basis'] == 'equity' and 'cagr_pct' in s and 'expectancy_r' in s


def test_cost_turns_a_scratch_into_a_loss_and_lowers_net(client):
    cheap = _run(client, cost_pct=0)['summary']['total_pnl_rupees']
    dear = _run(client, cost_pct=1.0)['summary']['total_pnl_rupees']
    assert dear < cheap


def test_unknown_symbol_and_bad_dates_are_rejected(client):
    assert client.post('/api/backtest/ha-supertrend',
                       json={'start_date': '2024-01-01', 'end_date': '2025-01-01', 'symbol': 'ZZZ'}).status_code == 400
    assert client.post('/api/backtest/ha-supertrend', json={'start_date': 'x'}).status_code == 400


def test_status_of_an_unknown_task_is_404(client):
    assert client.get('/api/backtest/ha-supertrend/status/nope').status_code == 404


def test_only_the_daily_timeframe_is_accepted_and_the_run_is_positional(client):
    r = client.post('/api/backtest/ha-supertrend',
                    json={'start_date': '2024-03-01', 'end_date': '2025-01-10', 'interval': '15minute'})
    assert r.status_code == 400 and 'DAILY' in r.get_json()['error']
    s = _run(client, interval='day')['summary']
    assert s['timeframe'] == '1 Day' and s['holding'] == 'positional' and s['avg_days_held'] > 1
