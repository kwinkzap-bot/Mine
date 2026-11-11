"""Candle Breakout OPT P&L priced from recorded option candles.

No broker is reached: the shared pricer (attach_option_legs) is replaced by a
fake that records what it was asked and returns a premium per request, and the
route is driven on a bare Flask app (tests/route_app.py), never create_app().
"""
import time

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import second_candle_options as sco


def _minutes(day='2026-10-05'):
    idx = pd.date_range(f'{day} 09:15', f'{day} 15:29', freq='1min')
    close = 24000 + np.arange(len(idx)) * 1.0
    return pd.DataFrame({'open': close - 0.5, 'high': close + 0.5,
                         'low': close - 0.5, 'close': close, 'volume': 0}, index=idx)


def _fake_attach(calls):
    def attach(symbol, trades, premium, bar_minutes, progress, calendar=None):
        calls.append((symbol, [dict(t) for t in trades], premium, calendar))
        legs = []
        for t in trades:
            if t.get('unpriceable'):
                continue
            legs.append(dict(t, entry_premium=250.0, exit_premium=280.0, pnl=30.0, result='WIN',
                             strike=23800, option_type='CE' if t['type'] == 'Long' else 'PE',
                             expiry='2026-10-07', index_pnl=t['pnl']))
        return legs, {'premium': premium, 'index_trades': len(trades), 'unpriced':
                      sum(1 for t in trades if t.get('unpriceable')), 'truncated': 0}
    return attach


def _trade(**kw):
    t = {'entry_time': '2026-10-05 09:30:00', 'exit_time': '2026-10-05 10:00:00',
         'type': 'Long', 'entry_price': 24017.0, 'exit_price': 24047.0,
         'pnl': 30.0, 'result': 'WIN', 'exit_reason': 'TG Hit'}
    t.update(kw)
    return t


def test_a_5_minute_trade_is_priced_at_the_minute_the_index_traded_the_level():
    calls = []
    priced, info = sco.price_trades('NIFTY', [_trade()], 5, _minutes(), attach=_fake_attach(calls))
    req = calls[0][1][0]
    # 24017 is the 09:32 bar's close (09:15 + 17 min) — inside the 09:30 bar, not its stamp
    assert req['entry_time'] == '2026-10-05 09:32:00' and req['intrabar_entry']
    assert req['exit_time'] == '2026-10-05 10:02:00' and req['intrabar_exit']
    assert calls[0][2] == 250.0
    row = priced[0]
    # the row keeps the index's own times and prices, the leg rides beside
    assert row['entry_time'] == '2026-10-05 09:30:00' and row['entry_price'] == 24017.0
    assert row['entry_premium'] == 250.0 and row['exit_premium'] == 280.0
    assert row['pnl'] == 30.0 and row['index_pnl'] == 30.0
    assert row['option_entry_time'] == '09:32' and row['option_exit_time'] == '10:02'
    assert row['strike'] == 23800 and row['option_type'] == 'CE' and row['expiry'] == '2026-10-07'


def test_30_second_bars_keep_their_own_minute_and_a_time_exit_reads_the_minute_before():
    calls = []
    t = _trade(entry_time='2026-10-05 09:16:30', exit_time='2026-10-05 15:25:00',
               exit_reason='Time Exit')
    priced, _ = sco.price_trades('NIFTY', [t], 1, _minutes(), attach=_fake_attach(calls))
    req = calls[0][1][0]
    assert req['entry_time'] == '2026-10-05 09:16:30'          # its own minute, 09:16
    assert not req.get('intrabar_exit')                         # cut-off open = close of 15:24
    assert priced[0]['option_entry_time'] == '09:16' and priced[0]['option_exit_time'] == '15:24'


def test_the_cap_prices_the_newest_trades_and_unpriced_ones_are_counted():
    calls = []
    trades = [_trade(entry_time=f'2026-10-0{d} 09:30:00', exit_time=f'2026-10-0{d} 10:00:00')
              for d in (5, 6, 7)]
    trades[2]['unpriceable'] = True
    priced, info = sco.price_trades('NIFTY', trades, 1, None, max_legs=2,
                                    attach=_fake_attach(calls))
    assert [t['entry_time'][:10] for t in calls[0][1]] == ['2026-10-06', '2026-10-07']
    assert len(priced) == 1 and info['truncated'] == 1 and info['unpriced'] == 1
    assert info['index_trades'] == 3


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from route_app import build_route_app
    from trading_app.app.routes import api

    class Provider:
        def historical_data(self, **kw):
            raise AssertionError('minute bars must come from the store')
    monkeypatch.setattr(api, 'get_data_provider', lambda *a, **k: Provider())
    monkeypatch.setattr(api, 'get_fyers_adapter', lambda *a: object())
    days = pd.concat([_minutes(d) for d in ('2026-10-05', '2026-10-06', '2026-10-07')])
    monkeypatch.setattr(api, '_sbr_load_minutes', lambda *a, **k: (days, 0))
    calls = []
    real = sco.price_trades
    monkeypatch.setattr(sco, 'price_trades',
                        lambda *a, **k: real(*a, **dict(k, attach=_fake_attach(calls))))
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c, calls


def _post(c, **kw):
    body = {'symbol': 'NIFTY', 'start_date': '2026-10-05', 'end_date': '2026-10-07',
            'interval': '5minute', 'candle_index': 1, 'rr_ratio': 1.0,
            'exit_hour': 15, 'exit_minute': 25}
    body.update(kw)
    return c.post('/api/backtest/second-candle', json=body).get_json()


def test_index_basis_answers_at_once_from_the_store(client):
    c, calls = client
    body = _post(c)
    assert body['success'] and body['summary']['pnl_basis'] == 'index' and body['trades']
    assert calls == []


def test_opt_pnl_prices_every_trade_from_recorded_candles_in_the_background(client):
    c, calls = client
    first = _post(c, option_pnl=True, premium=250)
    assert first['success'] and first['task_id']
    for _ in range(200):
        st = c.get(f"/api/backtest/st-bb-rsi/status/{first['task_id']}").get_json()
        if st['status'] != 'running':
            break
        time.sleep(0.05)
    assert st['success'] and st['summary']['pnl_basis'] == 'option'
    assert st['summary']['option_source'] == 'recorded' and st['summary']['premium'] == 250.0
    assert len(st['trades']) == len(calls[0][1]) > 0
    assert all(t['entry_premium'] == 250.0 and t['exit_premium'] == 280.0 for t in st['trades'])
    assert st['summary']['total_pnl'] == pytest.approx(30.0 * len(st['trades']))
