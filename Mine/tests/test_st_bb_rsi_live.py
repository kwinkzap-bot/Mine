"""ST + BB + RSI live algo: the decisions (service/st_bb_rsi_live_engine.py) and the
paper-mode thread (algo/st_bb_rsi/st_bb_rsi_algo.py), with no broker and no network."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest.st_bb_rsi_engine import StBbRsiEngine
from trading_app.algo.st_bb_rsi import st_bb_rsi_algo as algo
from trading_app.service import st_bb_rsi_live_engine as live

IST = timezone(timedelta(hours=5, minutes=30))
D1, D2 = '2025-03-03', '2025-03-04'


def _day(date, closes):
    t0 = pd.Timestamp(f'{date} 09:15')
    return [{'date': t0 + pd.Timedelta(minutes=3 * k), 'open': c - 0.5, 'high': c + 1, 'low': c - 1,
             'close': c, 'volume': 0} for k, c in enumerate(closes)]


def _frame():
    """Day 1 flat; day 2 flat, a rally (signals), then a fall (SuperTrend turns down)."""
    d1 = [100.0] * 125
    d2 = [100.0] * 25 + list(np.linspace(100, 200, 25)) + list(np.linspace(200, 100, 40)) + [100.0] * 35
    return pd.DataFrame(_day(D1, d1) + _day(D2, d2)).set_index('date')


S = dict(rsi_len=14, rsi_level=70.0, bb_len=20, bb_mult=2.0, st_len=10, st_mult=2.0,
         max_trades=2, direction='long', require_green=False)


def _first_signal_bar(frame):
    sg = StBbRsiEngine(frame.reset_index(), **live.engine_kwargs(S)).signals()
    n = len(frame)
    for i in range(125, n):
        if sg['signal'][i] == 1:
            return i
    raise AssertionError('no signal in the synthetic day')


def test_entry_fires_only_on_the_candle_that_just_closed_and_once():
    f = _frame()
    i = _first_signal_bar(f)
    day = live.new_day(D2)
    ev = live.on_bar(day, f.iloc[:i + 1], S)
    assert [e['event'] for e in ev] == ['entry'] and day['trades'][0]['type'] == 'Long'
    assert live.on_bar(day, f.iloc[:i + 1], S) == []            # the same candle again: nothing


def test_open_trade_exits_when_supertrend_turns_and_not_before():
    f = _frame()
    i = _first_signal_bar(f)
    day = live.new_day(D2)
    live.on_bar(day, f.iloc[:i + 1], S)
    t = day['trades'][0]
    exits = []
    for k in range(i + 1, len(f)):
        for e in live.on_bar(day, f.iloc[:k + 1], S):
            assert e['event'] == 'exit'
            exits.append((k, e))
            t['status'] = 'closed'
        if exits:
            break
    assert exits and exits[0][1]['reason'] == 'SuperTrend Down'
    assert exits[0][0] > i


def test_time_exit_squares_off_at_the_cutoff_minute():
    day = live.new_day(D2)
    day['trades'].append({'n': 1, 'type': 'Long', 'status': 'open'})
    assert live.time_exits(day, '15:13', '15:14') == []
    ev = live.time_exits(day, '15:14', '15:14')
    assert len(ev) == 1 and ev[0]['reason'] == 'Time Exit'


def test_max_trades_a_day_is_respected():
    f = _frame()
    i = _first_signal_bar(f)
    day = live.new_day(D2)
    day['trades'] = [{'n': 1, 'status': 'closed'}, {'n': 2, 'status': 'closed'}]
    assert live.on_bar(day, f.iloc[:i + 1], dict(S, max_trades=2)) == []


# ── the thread, in paper mode ────────────────────────────────────────────────

class _FakeAlgo(algo.StBbRsiAlgo):
    def __init__(self, username, frame):
        super().__init__(username)
        self.frame = frame
        self.alerts = []

    def _bars(self, today, now):
        cut = pd.Timestamp(now.replace(tzinfo=None)) - algo._BAR_LAG
        return self.frame[self.frame.index <= cut]

    def _spot(self):
        return 23000.0

    def _premium(self, strike, option_type):
        return 250.0

    def _chain_step(self):
        return 50

    def _alert(self, *a, **k):
        self.alerts.append(a)


@pytest.fixture()
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(algo, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(algo, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    env = {'STBB_MODE': 'paper', 'STBB_DIRECTION': 'long'}
    # one account carrying the flags: BROKER_N_STBB_LOTS = 2
    monkeypatch.setattr(algo, 'accounts', lambda u: [{'instance': 1, 'type': 'zerodha', 'lots': 2,
                                                      'name': 'Z1', 'stoppable': True}])
    monkeypatch.setattr(algo, '_uvar', lambda u, name, default='': env.get(name, default))
    monkeypatch.setattr(algo, '_flag', lambda u, name, default='false': False)
    return env


def _now_for(frame, i):
    ts = frame.index[i] + pd.Timedelta(minutes=live.BAR_MINUTES, seconds=10)
    return ts.to_pydatetime().replace(tzinfo=IST)


def test_paper_thread_enters_then_exits_and_books_a_history_row(paper):
    f = _frame()
    i = _first_signal_bar(f)
    a = _FakeAlgo('u', f)
    a.tick(_now_for(f, i))
    t = a.state['day']['trades'][0]
    assert t['status'] == 'open' and t['mode'] == 'paper'
    assert t['option'] == {'symbol': 'NIFTY', 'strike': 23000, 'type': 'CE', 'quoted': 250.0}
    assert t['legs']['paper']['lots'] == 2 and t['legs']['paper']['entry'] == 250.0
    for k in range(i + 1, len(f)):
        a.tick(_now_for(f, k))
        if a.state['day']['trades'][0]['status'] == 'closed':
            break
    t = a.state['day']['trades'][0]
    assert t['status'] == 'closed' and t['result'] == 'SuperTrend Down'
    rows = algo.load_history()
    assert len(rows) == 1 and rows[0]['mode'] == 'paper' and rows[0]['side'] == 'Long'
    assert rows[0]['net'] == pytest.approx(rows[0]['pnl'] - rows[0]['charges'])


def test_a_signal_read_late_is_missed_not_traded(paper):
    f = _frame()
    i = _first_signal_bar(f)
    a = _FakeAlgo('u', f)
    late = _now_for(f, i) + timedelta(minutes=10)              # the thread woke up 10 minutes late
    a.tick(late)
    trades = a.state['day']['trades']
    assert trades and all(t['status'] != 'open' for t in trades)
    assert trades[0]['status'] == 'missed' and not trades[0]['legs']
    assert algo.load_history() == []


def test_live_mode_is_never_chosen_without_the_toggle(paper):
    assert algo.mode('u') == 'paper'
    paper['STBB_MODE'] = 'live'
    assert algo.mode('u') == 'live'
    paper['STBB_MODE'] = 'anything else'
    assert algo.mode('u') == 'paper'


def test_stbb_orders_route_on_their_own_flag_and_carry_no_automatic_stop(monkeypatch):
    import inspect
    from trading_app.app.routes import api
    src = inspect.getsource(api._dispatch_order_to_brokers)
    assert "_auto_sl = _auto_sl and strategy != 'stbb'" in src
    env = {'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true', 'BROKER_1_STBB_ACTIVE': 'true',
           'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true', 'BROKER_2_CPR_ACTIVE': 'true',
           'BROKER_3_TYPE': 'zerodha', 'BROKER_3_ACTIVE': 'true', 'BROKER_3_OP_ACTIVE': 'true'}
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, k, d='': env.get(k, d)))
    tried = []
    monkeypatch.setattr(api, 'get_kite', lambda instance=None, **k: tried.append(instance))
    res = api._dispatch_order_to_brokers(symbol='NIFTY', strike=24850, option_type='CE', action='BUY',
                                         strategy='stbb', username='u', session_data={},
                                         lots_for=lambda i: 1)
    assert res['brokers_targeted'] == 1 and tried == [1]            # only the STBB-flagged account
    unsized = api._dispatch_order_to_brokers(symbol='NIFTY', strike=24850, option_type='CE', action='BUY',
                                             strategy='stbb', username='u', session_data={})
    assert 'sized by the caller' in unsized['summary'][0]['result']['error']


def test_there_is_no_compounding_or_lot_setting_left():
    assert not hasattr(algo, 'lot_mode') and not hasattr(algo, 'booked_pnl') and not hasattr(algo, 'lots_for_trade')
    assert not hasattr(live, 'compound_lots')
    assert not {'lot_mode', 'lots', 'start_capital', 'per_lot', 'max_lots'} & set(algo.settings('u'))


def test_paper_takes_the_lots_flag_of_the_first_flagged_account_else_one(monkeypatch):
    monkeypatch.setattr(algo, 'accounts', lambda u: [{'lots': None, 'name': 'a'}, {'lots': 3, 'name': 'b'}])
    assert algo.paper_lots('u') == 3
    monkeypatch.setattr(algo, 'accounts', lambda u: [])
    assert algo.paper_lots('u') == 1


def test_a_live_entry_is_the_accounts_own_lots_flag(paper, monkeypatch):
    """The size handed to the broker is BROKER_N_STBB_LOTS as it stands — via lot_chunks
    only to respect the exchange's freeze limit."""
    import inspect
    src = inspect.getsource(algo.StBbRsiAlgo._enter_live)
    assert "lots = a['lots']" in src and 'compound' not in src
