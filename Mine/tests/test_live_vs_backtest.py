"""The Active Trade tab's ⚖ Compare: live trades paired with the backtest's (service/live_vs_backtest.py)."""
import pandas as pd

from trading_app.service import live_vs_backtest as lvb


def _lv(**kw):
    t = {'side': 'BUY', 'entry_time': '11:57', 'exit_time': '12:39', 'index_entry': 25483.3,
         'index_exit': 25461.6, 'strike': 25250, 'option_type': 'CE', 'entry_premium': 251.55,
         'exit_premium': 223.55, 'reason': 'SuperTrend Down', 'status': 'closed'}
    t.update(kw)
    return t


def test_the_same_trade_a_second_apart_is_a_match():
    bt = dict(_lv(), index_entry=25483.7, index_exit=25463.9, entry_premium=252.1, exit_premium=224.0)
    rows = lvb.match([_lv()], [bt])
    assert [r['status'] for r in rows] == ['match']
    assert rows[0]['diffs'] == []


def test_a_late_exit_and_a_different_reason_are_named():
    bt = dict(_lv(), exit_time='13:05', index_exit=25440.0, reason='Time Exit')
    (row,) = lvb.match([_lv()], [bt])
    assert row['status'] == 'differs'
    assert {d['field'] for d in row['diffs']} == {'Exit time', 'Index exit', 'Exit reason'}
    assert next(d for d in row['diffs'] if d['field'] == 'Exit time')['gap'] == '26 min'


def test_a_premium_only_gap_is_a_fill_difference_not_a_logic_one():
    """17 Nov 2026, ST1: same 11:57 BUY, same index, exit and reason; the CE ran 238 -> 252 inside the
    11:56 minute, the backtest booked its 245.65 close and live's order at 11:57:04 filled 251.55."""
    (row,) = lvb.match([_lv()], [dict(_lv(), entry_premium=245.65, exit_premium=223.35)])
    assert row['status'] == 'fill' and [d['field'] for d in row['diffs']] == ['Option entry']
    (row,) = lvb.match([_lv()], [dict(_lv(), entry_premium=245.65, exit_time='13:05')])
    assert row['status'] == 'differs'                  # a different minute is logic, whatever the premium


def test_unpaired_trades_on_either_side_are_reported():
    live = [_lv(), _lv(side='SELL', entry_time='14:10')]
    bt = [_lv(entry_time='11:58'), _lv(side='BUY', entry_time='13:30')]
    rows = lvb.match(live, bt)
    assert [r['status'] for r in rows] == ['match', 'backtest_only', 'live_only']
    assert lvb.summary(rows) == {'match': 1, 'fill': 0, 'differs': 0, 'live_only': 1, 'backtest_only': 1}


def test_a_side_never_pairs_with_the_other_side():
    rows = lvb.match([_lv()], [_lv(side='SELL')])
    assert sorted(r['status'] for r in rows) == ['backtest_only', 'live_only']


def test_a_missed_live_setup_beside_a_backtest_trade_differs():
    lv = _lv(status='missed', entry_time=None, signal_bar='11:54')
    (row,) = lvb.match([lv], [_lv()])
    assert row['status'] == 'differs' and row['diffs'][0]['live'] == 'missed'


def test_live_rows_come_off_the_day_state_and_skip_armed_setups():
    state = {'day': {'date': '2026-11-17', 'trades': [
        {'n': 1, 'type': 'Long', 'status': 'closed', 'mode': 'live', 'entry_time': '11:57', 'exit_time': '12:39',
         'index_entry': 25483.3, 'index_exit': 25461.6, 'result': 'SuperTrend Down',
         'signal_bar': '2026-11-17 11:54:00', 'option': {'strike': 25250, 'type': 'CE'},
         'legs': {'1': {'qty': 65, 'entry': 251.55, 'exit': 223.55}, '2': {'qty': 130, 'entry': 252.0, 'exit': 224.0}}},
        {'n': 2, 'type': 'Short', 'status': 'expired'},
    ]}}
    (t,) = lvb.live_simple(state, '2026-11-17')
    assert t['side'] == 'BUY' and t['signal_bar'] == '11:54'
    assert t['entry_premium'] == 251.85          # quantity-weighted over the two accounts
    assert lvb.live_simple(state, '2026-11-18') == []


def test_backtest_replay_uses_the_live_spec_engine(monkeypatch):
    """A spec is replayed through its own engine class with engine_kwargs, today's trades only."""
    from trading_app.service import simple_trader_live_engine as sim
    seen = {}

    class FakeEngine:
        def __init__(self, df, **kw):
            seen.update(kw)

        def run(self):
            return [{'entry_time': '2026-11-16 10:00:00', 'type': 'Long'},
                    {'entry_time': '2026-11-17 11:57:00', 'exit_time': '2026-11-17 12:39:00', 'type': 'Long',
                     'entry_price': 25483.7, 'exit_price': 25463.9, 'exit_reason': 'SuperTrend Down'}], {}

    spec = sim.SPECS[1]
    monkeypatch.setattr(spec, 'cls', FakeEngine)
    minutes = pd.DataFrame({'open': [1.0], 'high': [1.0], 'low': [1.0], 'close': [1.0]},
                           index=pd.DatetimeIndex(['2026-11-17 09:15']))
    got = lvb.backtest_simple(spec, {'max_trades': 3}, minutes, pd.DataFrame(), '2026-11-17', price=False)
    assert [t['entry_time'][:10] for t in got['trades']] == ['2026-11-17']
    assert seen['max_trades_per_day'] == 3 and seen['entry_start'] == spec.entry_start
    (row,) = lvb.bt_rows_simple(got['trades'])
    assert (row['side'], row['entry_time'], row['exit_time'], row['reason']) == ('BUY', '11:57', '12:39', 'SuperTrend Down')


def test_compare_route_is_registered():
    from tests.route_app import build_route_app
    rules = {r.rule: r for r in build_route_app().url_map.iter_rules()}
    assert 'GET' in rules['/api/algo/compare-today/<key>'].methods
