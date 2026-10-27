"""The 💡 Logic popup's chart: one session's candles, SuperTrend line and entries/exits."""
import numpy as np
import pandas as pd
import pytest
from flask import Flask


def _minutes(days=6):
    """Rising 1-minute NIFTY-like bars over a few sessions with a dip that touches the line."""
    rng = np.random.default_rng(3)
    frames = []
    for d in pd.bdate_range(end='2026-10-27', periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=375, freq='min')
        c = 22000 + np.cumsum(rng.normal(0.4, 6, len(idx)))
        frames.append(pd.DataFrame({'open': c - 1, 'high': c + 4, 'low': c - 4, 'close': c, 'volume': 0}, index=idx))
    return pd.concat(frames)


@pytest.fixture
def client(monkeypatch):
    from trading_app.app.routes import api
    from trading_app.app.routes.api import api_bp
    minutes = _minutes()

    from trading_app.app.utils import user_auth
    monkeypatch.setattr(user_auth, 'is_user_authenticated', lambda: True)
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    monkeypatch.setattr(api, 'get_fyers_adapter', lambda: object())
    monkeypatch.setattr(api, '_sp_instrument_token', lambda *_: 1)
    monkeypatch.setattr(api, '_sbr_load_bars',
                        lambda p, t, sym, s, e, interval, warmup_days=45: (minutes, *api._sbr_bars(minutes, interval, s)[:1], 0, 0))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_bp, url_prefix='/api')
    return app.test_client()


def test_chart_returns_the_session_candles_line_and_trades(client):
    r = client.post('/api/backtest/st-touch-break/chart', json={'symbol': 'NIFTY', 'interval': '3minute',
                    'direction': 'both', 'modes': 'both', 'date': '2026-10-27'})
    j = r.get_json()
    assert j['success'], j
    assert j['date'] == '2026-10-27'
    assert len(j['candles']) == 125                      # 375 minutes / 3
    assert j['bar_seconds'] == 180
    assert j['line'] and set(j['line'][0]) == {'time', 'value', 'trend'}
    for t in j['trades']:
        assert t['entry_time'][:10] == '2026-10-27'
        assert t['entry_t'] >= j['candles'][0]['time']


def test_chart_without_a_date_picks_a_session_with_a_signal(client):
    j = client.post('/api/backtest/st-touch-break/chart', json={'direction': 'both', 'modes': 'both'}).get_json()
    assert j['success'], j
    if j['trade_days']:
        assert j['date'] == j['trade_days'][0]


def test_chart_rejects_a_day_with_no_candles(client):
    j = client.post('/api/backtest/st-touch-break/chart', json={'date': '2026-10-24'}).get_json()
    assert j['success'] is False
    assert 'No candles' in j['error']


def test_run_route_accepts_a_stock_and_keeps_premium_zero_as_atm(monkeypatch, client):
    """A stock is allowed, and premium 0 reaches the option pricer as ATM rather than the 250 default."""
    from trading_app.app.routes import api
    seen = {}

    def fake_attach(symbol, batch, premium, *a, **kw):
        seen['premium'] = premium
        seen['symbol'] = symbol
        raise RuntimeError('no ICICI login')          # keep the index result; we only want the arguments
    import trading_app.Backtest.st_bb_rsi_engine as eng
    monkeypatch.setattr(eng, 'attach_option_legs', fake_attach)
    import time
    body = {'symbol': 'RELIANCE', 'interval': '3minute', 'start_date': '2026-10-13', 'end_date': '2026-10-27',
            'direction': 'both', 'modes': 'both', 'premium': 0, 'price_options': True}
    r = client.post('/api/backtest/st-touch-break', json=body).get_json()
    assert r['success'], r
    assert 'task_id' in r, 'the fake series produced no signals, so the pricing arguments were never checked'
    if 'task_id' in r:
        for _ in range(50):
            st = client.get(f"/api/backtest/st-bb-rsi/status/{r['task_id']}").get_json()
            if st.get('status') == 'complete':
                break
            time.sleep(0.1)
        assert seen.get('premium') is None and seen.get('symbol') == 'RELIANCE'     # None = ATM


def test_run_route_still_refuses_an_unknown_symbol(client):
    r = client.post('/api/backtest/st-touch-break', json={'symbol': 'NOTASTOCK', 'start_date': '2026-10-13',
                                                          'end_date': '2026-10-27'})
    assert r.status_code == 400
