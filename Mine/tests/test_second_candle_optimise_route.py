"""The 2nd-Candle Find Best Params route reads its minutes from the disk store.

Until 2026-11-11 it pulled 2017→today from the backtest provider (Breeze) once
per interval with use_cache=False — thousands of two-day requests — and the
page gave up after 10 minutes. These tests hold that the minute timeframes are
built from one stored 1-minute series and that no provider history call is made
for them. Built on a bare Flask app (tests/route_app.py), never create_app().
"""
import time

import numpy as np
import pandas as pd
import pytest

from route_app import build_route_app
from trading_app.app.routes import api


def _minutes(days=8):
    """`days` sessions of 1-minute bars rising through the day, plus a stray
    09:14 pre-open print the route must drop."""
    frames = []
    for d in pd.bdate_range('2026-10-01', periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=14),
                            d + pd.Timedelta(hours=15, minutes=29), freq='1min')
        k = np.arange(len(idx))
        # a drift plus a swing, different every day, so SLs and targets both get hit
        close = 24000 + k * 0.5 + 40 * np.sin(k / (9 + d.day % 5))
        frames.append(pd.DataFrame({'open': close - 0.2, 'high': close + 1,
                                    'low': close - 1, 'close': close,
                                    'volume': 0}, index=idx))
    return pd.concat(frames)


class _NoMinuteProvider:
    def __init__(self):
        self.calls = []

    def historical_data(self, **kw):
        self.calls.append(kw.get('interval'))
        return []


def _thirty_second(fd, td):
    """30-second bars for [fd, td] cut from the same synthetic minutes."""
    m = _minutes()
    m = m[(m.index >= pd.Timestamp(fd)) & (m.index < pd.Timestamp(td) + pd.Timedelta(days=1))]
    out = []
    for ts, r in m.iterrows():
        if ts.hour * 60 + ts.minute < 555:
            continue
        mid = (r['open'] + r['close']) / 2
        out.append({'date': ts.strftime('%Y-%m-%d %H:%M:%S+05:30'), 'open': r['open'],
                    'high': max(r['open'], mid) + 0.5, 'low': min(r['open'], mid) - 0.5,
                    'close': mid, 'volume': 0})
        out.append({'date': (ts + pd.Timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S+05:30'),
                    'open': mid, 'high': r['high'], 'low': r['low'], 'close': r['close'], 'volume': 0})
    return out


@pytest.fixture
def env(monkeypatch, tmp_path):
    from trading_app.Backtest import seconds_store
    monkeypatch.setattr(seconds_store, 'DATA_DIR', str(tmp_path))
    provider = _NoMinuteProvider()
    loads = []
    monkeypatch.setattr(api, 'get_data_provider', lambda *a, **k: provider)
    fyers = object()
    monkeypatch.setattr(api, 'get_fyers_adapter', lambda *a: fyers)

    def load(prov, token, symbol, start, end, warmup_days=45):
        loads.append((token, symbol, start, end, warmup_days))
        return _minutes(), 0
    monkeypatch.setattr(api, '_sbr_load_minutes', load)
    monkeypatch.setattr(api, '_load_opt_cache', lambda: {})
    monkeypatch.setattr(api, '_save_opt_cache', lambda c: None)
    from trading_app.Backtest import seconds_history
    secs = []
    monkeypatch.setattr(seconds_history, 'fetch_seconds_history',
                        lambda prov, token, fd, td, **k: (secs.append((prov is fyers, token, fd, td)),
                                                          (_thirty_second(fd, td), None))[1])
    # The 30-second backfill: Breeze is the synthetic series, every hour has
    # an allowance, and waiting is instant.
    breeze = []
    monkeypatch.setattr(api, 'get_icici_adapter', lambda *a: 'icici')
    monkeypatch.setattr(api, '_sc_breeze_30s',
                        lambda prov, token, fd, td: (breeze.append((fd, td)),
                                                     _thirty_second(fd.isoformat(), td.isoformat()))[1])
    monkeypatch.setattr(api, '_sc_backfill_allowance', lambda now=None: 10 ** 6)
    monkeypatch.setattr(api, '_sc_sleep', lambda s: None)
    monkeypatch.setattr(api, '_sc_empty', {})
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        c.breeze = breeze
        yield c, provider, loads, secs
    _wait_backfill()


def _wait_backfill():
    for _ in range(500):
        if not api._sc_extending:
            return
        time.sleep(0.01)
    raise AssertionError('the 30-second backfill never finished')


def _finish(client, task_id):
    for _ in range(300):
        body = client.get(f'/api/backtest/second-candle/optimise/status/{task_id}').get_json()
        if body['status'] != 'running':
            return body
        time.sleep(0.05)
    raise AssertionError('optimise never finished')


def test_minute_timeframes_come_from_the_store_not_the_provider(env):
    client, provider, loads, secs = env
    r = client.post('/api/backtest/second-candle/optimise', json={
        'symbol': 'NIFTY', 'start_date': '2017-01-01', 'end_date': '2026-11-11',
        'exit_hour': 15, 'exit_minute': 25, 'recalculate': True, 'option_pnl': False})
    body = _finish(client, r.get_json()['task_id'])
    assert body['success'], body
    assert [g['tf_label'] for g in body['timeframes']] == ['1m', '2m', '3m', '5m']
    assert all(g['total'] > 0 for g in body['timeframes'])
    assert provider.calls == []                       # no Breeze pull for any minute interval
    assert loads == [('NSE:NIFTY50-INDEX', 'NIFTY', '2017-01-01', '2026-11-11', 0)]
    # 30-second: Fyers' free window only, never the backtest provider (Breeze).
    assert secs == [(True, 'NSE:NIFTY50-INDEX', '2026-10-14', '2026-11-11')]


def test_no_fyers_login_is_a_clear_error(env, monkeypatch):
    client, provider, _, secs = env
    monkeypatch.setattr(api, 'get_fyers_adapter', lambda *a: None)
    r = client.post('/api/backtest/second-candle/optimise', json={
        'symbol': 'NIFTY', 'start_date': '2026-10-01', 'end_date': '2026-10-31',
        'recalculate': True})
    body = _finish(client, r.get_json()['task_id'])
    assert body['status'] == 'error' and 'Fyers' in body['error']
    assert provider.calls == [] and secs == []


@pytest.mark.parametrize('option_pnl, basis, ranked', [(True, 'option_model', 'option_pnl'),
                                                       (False, 'index', 'index_pnl')])
def test_every_board_row_carries_index_and_option_pnl(env, option_pnl, basis, ranked):
    client, _, _, _ = env
    r = client.post('/api/backtest/second-candle/optimise', json={
        'symbol': 'NIFTY', 'start_date': '2017-01-01', 'end_date': '2026-11-11',
        'recalculate': True, 'option_pnl': option_pnl})
    body = _finish(client, r.get_json()['task_id'])
    assert body['success'] and body['pnl_basis'] == basis
    rows = [row for g in body['timeframes'] for row in g['results']]
    assert rows
    for row in rows:
        assert isinstance(row['index_pnl'], float) and isinstance(row['option_pnl'], float)
        assert row['total_pnl'] == row[ranked]          # the board ranks on the ticked basis


@pytest.mark.parametrize('tf', ['30s', '1m', '2m', '3m', '5m'])
@pytest.mark.parametrize('option_pnl', [False, True])
def test_a_board_row_and_a_backtest_of_it_book_the_same_trades(env, tf, option_pnl):
    """The board and BACKTEST read their bars through one call (_sc_bars), so a
    row's trades / P&L are what a BACKTEST of its settings returns. On
    2026-11-11 the 30s board swept 19 sessions and the backtest 44."""
    client, _, _, _ = env
    rng = {'symbol': 'NIFTY', 'start_date': '2026-10-01', 'end_date': '2026-10-12',
           'exit_hour': 15, 'exit_minute': 25}
    r = client.post('/api/backtest/second-candle/optimise',
                    json=dict(rng, recalculate=True, option_pnl=option_pnl))
    board = _finish(client, r.get_json()['task_id'])
    group = next(g for g in board['timeframes'] if g['tf_label'] == tf)
    assert group['results']
    for row in group['results'][:3]:
        d = row['direction']
        bt = client.post('/api/backtest/second-candle', json=dict(
            rng, interval=row['interval'], candle_index=row['candle_index'],
            rr_ratio=row['rr_ratio'], enable_long=d != 'short', enable_short=d != 'long',
            option_pnl=False)).get_json()
        assert bt['success'], bt
        assert bt['summary']['total_trades'] == row['total_trades']
        assert bt['summary']['total_pnl'] == pytest.approx(row['index_pnl'], abs=0.05)


def test_the_30s_board_sweeps_the_older_sessions_a_backtest_stored(env):
    """The case that broke: the BACKTEST reaches past Fyers' 28-day window (via
    Breeze) and the board only fetches that window. The board must still sweep
    every session the backtest ran on."""
    client, _, _, secs = env
    rng = {'symbol': 'NIFTY', 'start_date': '2026-10-01', 'end_date': '2026-11-11',
           'exit_hour': 15, 'exit_minute': 25}
    run = lambda: client.post('/api/backtest/second-candle', json=dict(
        rng, interval='30second', candle_index=1, rr_ratio=2.0,
        enable_long=True, enable_short=True))
    # The first run answers at once from Fyers' window (nothing there before
    # 14 Oct) and fetches the older sessions in the background…
    first = run()
    assert first.status_code == 404
    for _ in range(200):
        if not api._sc_extending:
            break
        time.sleep(0.02)
    # …which the next run — and the board — then read.
    bt = run().get_json()
    assert bt['success'] and bt['summary']['total_trades'] == 8      # one a session, 1-12 Oct
    r = client.post('/api/backtest/second-candle/optimise', json=dict(rng, recalculate=True))
    board = _finish(client, r.get_json()['task_id'])
    g = next(g for g in board['timeframes'] if g['tf_label'] == '30s')
    assert (g['from'], g['to']) == ('2026-10-01', '2026-10-12')
    assert secs[-1][2] == '2026-10-14'          # the board itself asked Fyers' window only
    row = g['results'][0]
    d = row['direction']
    bt2 = client.post('/api/backtest/second-candle', json=dict(
        rng, interval='30second', candle_index=row['candle_index'], rr_ratio=row['rr_ratio'],
        enable_long=d != 'short', enable_short=d != 'long')).get_json()
    assert bt2['summary']['total_trades'] == row['total_trades']
    assert bt2['summary']['total_pnl'] == pytest.approx(row['index_pnl'], abs=0.05)


def _any_weekday_30s(fd, td):
    """Four 30-second bars for every weekday in [fd, td] — Breeze with no holes."""
    out = []
    for d in pd.bdate_range(fd, td):
        for k in range(4):
            ts = d + pd.Timedelta(hours=9, minutes=15, seconds=30 * k)
            out.append({'date': ts.strftime('%Y-%m-%d %H:%M:%S+05:30'), 'open': 1.0,
                        'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 0})
    return out


def test_30s_history_is_backfilled_to_where_breeze_starts_and_never_before(env, monkeypatch):
    """Both the backtest and the board start a walk back to BREEZE_SECONDS_SINCE
    (2018-04-02 in real life; 2026-06-01 here to keep it small), newest first,
    never more than 800 requests (32 weekdays) a fetch, and nothing before it —
    Breeze has no 1-second data earlier, so that would be 25 requests a session
    for nothing."""
    from datetime import date
    from trading_app.Backtest import seconds_history, seconds_store
    monkeypatch.setattr(seconds_history, 'BREEZE_SECONDS_SINCE', date(2026, 6, 1))
    client, _, _, _ = env
    monkeypatch.setattr(api, '_sc_breeze_30s', lambda prov, token, fd, td: (
        client.breeze.append((fd, td)), _any_weekday_30s(fd, td))[1])
    r = client.post('/api/backtest/second-candle/optimise', json={
        'symbol': 'NIFTY', 'start_date': '2017-01-01', 'end_date': '2026-10-12',
        'recalculate': True})
    _finish(client, r.get_json()['task_id'])
    _wait_backfill()
    assert client.breeze, 'the board started no backfill'
    assert min(fd for fd, _ in client.breeze) == date(2026, 6, 1)
    assert all(api._sc_weekdays(fd, td) * 25 <= 800 for fd, td in client.breeze)
    ends = [td for _, td in client.breeze]
    assert ends == sorted(ends, reverse=True)                  # newest first
    assert seconds_store.span('NIFTY')[0] == '2026-06-01'
    assert api._sc_backfill_gap('NIFTY') is None               # complete: nothing more to ask
    # …and the backtest says where the history starts.
    bt = client.post('/api/backtest/second-candle', json={
        'symbol': 'NIFTY', 'start_date': '2017-01-01', 'end_date': '2026-10-12',
        'interval': '30second', 'candle_index': 1, 'rr_ratio': 2.0,
        'exit_hour': 15, 'exit_minute': 25}).get_json()
    assert '2026-06-01' in (bt.get('warning') or bt.get('error') or '')


def test_the_backfill_spends_only_its_allowance(env, monkeypatch):
    """Nothing in market hours, 3,500 after 15:45, 4,500 at weekends — and the
    day's spend is kept on disk, so a second thread the same day does not reset it."""
    client, _, _, _ = env
    monkeypatch.setattr(api, '_sc_backfill_allowance', lambda now=None: 100)   # four sessions
    class Stop(Exception):
        pass
    def sleep(_):
        raise Stop
    monkeypatch.setattr(api, '_sc_sleep', sleep)
    monkeypatch.setattr(api, '_sc_breeze_30s', lambda prov, token, fd, td: (
        client.breeze.append((fd, td)), _any_weekday_30s(fd, td))[1])
    assert api._sc_backfill_30s('NIFTY', 'NSE:NIFTY50-INDEX')
    _wait_backfill()
    assert len(client.breeze) == 1 and api._sc_weekdays(*client.breeze[0]) == 4
    assert api._sc_spent_today() == 100
    assert api._sc_backfill_30s('NIFTY', 'NSE:NIFTY50-INDEX')     # gap left, so it starts…
    _wait_backfill()
    assert len(client.breeze) == 1                                 # …but spends nothing more today


def test_the_allowance_by_the_clock():
    """Real function (no env fixture): the walk shares Breeze's ~5,000/day with
    the live algos, so it waits for the close on weekdays."""
    from datetime import datetime as dt
    assert api._sc_backfill_allowance(dt(2026, 11, 11, 9, 0)) == 0        # Wed, market
    assert api._sc_backfill_allowance(dt(2026, 11, 11, 15, 44)) == 0
    assert api._sc_backfill_allowance(dt(2026, 11, 11, 15, 45)) == 3500
    assert api._sc_backfill_allowance(dt(2026, 11, 12, 6, 0)) == 0        # Thu morning: its own quota
    assert api._sc_backfill_allowance(dt(2026, 11, 14, 10, 0)) == 4500     # Saturday


def test_an_empty_chunk_never_moves_the_walk_past_it(env, monkeypatch):
    """A month of nothing is Breeze refusing (a dead daily session), not a
    month without trading: the same days are asked again later, never skipped,
    and the thread stands down after three tries."""
    from trading_app.Backtest import seconds_store
    client, _, _, _ = env
    seconds_store.merge_and_read('NIFTY', _any_weekday_30s('2026-09-01', '2026-10-13'),
                                 '2026-09-01', '2026-10-13')
    monkeypatch.setattr(api, '_sc_breeze_30s', lambda prov, token, fd, td: (
        client.breeze.append((fd, td)), [])[1])
    assert api._sc_backfill_30s('NIFTY', 'NSE:NIFTY50-INDEX')
    _wait_backfill()
    assert len(client.breeze) == 3 and len(set(client.breeze)) == 1
    assert seconds_store.span('NIFTY')[0] == '2026-09-01'
