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


# ── 2nd 30s Candle and 30-Min Fakeout (2026-11-19) ──

def test_sc_live_rows_come_off_history_and_the_open_trade():
    hist = [{'date': '2026-11-19', 'direction': 'BUY', 'entry_spot': 25194.8, 'exit_spot': 25249.8,
             'reason': 'TARGET', 'entry_time': '2026-11-19T09:16:02', 'exit_time': '2026-11-19T09:18:27',
             'strike': 25000, 'option_type': 'CE', 'opt_entry_price': 248.6, 'opt_exit_price': 292.1, 'paper': True},
            {'date': '2026-11-18', 'direction': 'BUY', 'entry_time': '2026-11-18T09:16:02'}]
    (t,) = lvb.live_sc(hist, {'active_trade': None}, '2026-11-19')
    assert (t['side'], t['entry_time'], t['exit_time'], t['mode']) == ('BUY', '09:16', '09:18', 'paper')
    assert t['entry_premium'] == 248.6 and t['index_exit'] == 25249.8


def _sc_bars():
    """09:15 session of 30-second bars: candle 2 (09:15:30) is the range, 09:16:00 breaks its high."""
    t0 = pd.Timestamp('2026-11-19 09:15:00')
    rows = []
    px = [(100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101.5, 103.5), (103.5, 112, 103, 111)]
    for i, (o, h, l, c) in enumerate(px):
        rows.append({'date': (t0 + pd.Timedelta(seconds=30 * i)).isoformat(), 'open': o, 'high': h, 'low': l, 'close': c})
    for i in range(4, 40):
        rows.append({'date': (t0 + pd.Timedelta(seconds=30 * i)).isoformat(), 'open': 111, 'high': 111.5, 'low': 110.5, 'close': 111})
    return rows


def test_sc_backtest_runs_the_engine_with_the_live_params():
    got = lvb.backtest_sc(_sc_bars(), {'candle_index': 2, 'rr_ratio': 2.5, 'exit_hour': 15, 'exit_minute': 25,
                                       'enable_long': True, 'enable_short': True}, '2026-11-19')
    (b,) = lvb.bt_rows_sc(got['trades'])
    assert b['side'] == 'BUY' and b['entry_time'] == '09:16' and lvb._norm_reason(b['reason']) == 'target'
    assert got['priced'] is False


def test_sc_pricing_failure_keeps_the_index_trade():
    def boom(trades):
        raise RuntimeError('no ICICI session')
    got = lvb.backtest_sc(_sc_bars(), {'candle_index': 2, 'rr_ratio': 2.5}, '2026-11-19', price_fn=boom)
    assert len(got['trades']) == 1 and 'no ICICI session' in got['warning']


def _tmf_day(direction='long'):
    """One stock's 1-minute day: c1/c2 red, c3 fakes below c2's low and closes back, 10:50 breaks c3's high."""
    idx = pd.date_range('2026-11-19 09:15', '2026-11-19 15:29', freq='1min')
    df = pd.DataFrame({'open': 100.0, 'high': 100.2, 'low': 99.8, 'close': 100.0}, index=idx)
    def seg(a, b, o, h, l, c):
        m = (df.index >= pd.Timestamp(f'2026-11-19 {a}')) & (df.index < pd.Timestamp(f'2026-11-19 {b}'))
        df.loc[m, ['open', 'high', 'low', 'close']] = [o, h, l, c]
    seg('09:15', '09:45', 110, 111, 104, 105)       # c1 red, the session high 111
    seg('09:45', '10:15', 104.5, 105, 98, 99)       # c2 red, closes under c1's low
    seg('10:15', '10:45', 99, 101, 97, 100)         # c3 dips under c2's low, closes back in the lower half
    seg('10:45', '10:50', 100, 100.5, 99.5, 100)
    seg('10:50', '11:30', 101, 106, 100.5, 105)     # breaks c3's high 101
    seg('11:30', '15:30', 105, 111.5, 104, 111)     # reaches the pre-entry high 111
    return df


def test_tmf_engine_live_target_is_the_session_extreme_before_the_entry():
    from trading_app.Backtest.thirty_min_fakeout_engine import ThirtyMinFakeoutEngine
    df = _tmf_day()
    (t,) = ThirtyMinFakeoutEngine(df, exit_hour=15, exit_minute=5, use_entry_buffer=False,
                                  target_rule='to_entry').run()[0]
    assert t['type'] == 'Long' and t['target_price'] == 111.0 and t['exit_reason'] == 'Target Hit'
    (h,) = ThirtyMinFakeoutEngine(df, exit_hour=15, exit_minute=5, use_entry_buffer=False).run()[0]
    assert h['target_price'] == 111.5                # the Backtest page's hindsight target is unchanged


def test_tmf_backtest_pairs_per_stock(monkeypatch):
    from trading_app.Backtest import tmf_symbol_universe as u
    combo = {'direction': 'both', 'use_entry_buffer': False, 'use_body_filter': False, 'use_c2_close_filter': True}
    monkeypatch.setitem(u.TMF_SYMBOL_DEFAULTS, 'AAA', combo)
    monkeypatch.setitem(u.TMF_SYMBOL_DEFAULTS, 'BBB', combo)
    got = lvb.backtest_tmf(['AAA', 'BBB', 'CCC'], lambda s: _tmf_day() if s == 'AAA' else None, '2026-11-19')
    (b,) = got['trades']
    assert b['symbol'] == 'AAA' and b['qty'] == 500000 // 101
    assert 'BBB' in got['warning']
    hist = [{'symbol': 'AAA', 'direction': 'BUY', 'qty': 4950, 'entry_price': 101.05, 'exit_price': 111.0,
             'sl_price': 97.0, 'target_price': 111.0, 'reason': 'Target Hit', 'entry_time': '2026-11-19T10:50:20',
             'exit_time': b['exit_time'][:11] + b['exit_time'][11:16] + ':10', 'mode': 'paper', 'date': '2026-11-19'},
            {'symbol': 'BBB', 'direction': 'BUY', 'qty': 10, 'entry_price': 50, 'exit_price': 51,
             'reason': 'Time Exit', 'entry_time': '2026-11-19T10:50:00', 'exit_time': '2026-11-19T15:05:00',
             'date': '2026-11-19'}]
    rows = lvb.match(lvb.live_tmf(hist, '2026-11-19'), lvb.bt_rows_tmf(got['trades']))
    by = {(r['live'] or r['backtest'])['symbol']: r['status'] for r in rows}
    assert by == {'AAA': 'match', 'BBB': 'live_only'}


def test_a_stock_price_gap_is_read_against_its_own_price():
    lv = {'symbol': 'X', 'side': 'BUY', 'entry_time': '10:50', 'index_entry': 1000.0, 'px_tol': 1.0}
    (row,) = lvb.match([lv], [dict(lv, index_entry=1002.5)])
    assert row['status'] == 'differs' and row['diffs'][0]['field'] == 'Price entry'


def test_sc_legs_priced_on_fyers_30s_bars_at_the_strike_nearest_250():
    """The Compare tab reads 2nd 30s Candle's option off the weekly's own 30-second bars: strike
    nearest ₹250 at the entry bar, premium where the index crossed inside that bar."""
    idx = [{'date': '2026-11-19 09:16:00', 'open': 25190.0, 'high': 25200, 'low': 25188, 'close': 25200.0},
           {'date': '2026-11-19 09:18:00', 'open': 25240.0, 'high': 25255, 'low': 25238, 'close': 25260.0}]
    base = {25200: 200.0, 25150: 230.0, 25100: 262.0, 25050: 290.0}       # CE: ITM is dearer
    calls = []

    def fetch(k, ot):
        calls.append((k, ot))
        p = base.get(k)
        return [] if p is None else [
            {'date': '2026-11-19T09:16:00+05:30', 'open': p, 'high': p + 12, 'low': p - 1, 'close': p + 10},
            {'date': '2026-11-19T09:18:00+05:30', 'open': p + 40, 'high': p + 52, 'low': p + 39, 'close': p + 50}]
    t = {'type': 'Long', 'entry_time': '2026-11-19 09:16:00', 'exit_time': '2026-11-19 09:18:00',
         'entry_price': 25195.0, 'exit_price': 25250.0}
    got = lvb.price_sc_fyers([t], idx, fetch)
    (p,) = got['trades']
    assert p['strike'] == 25150 and p['option_type'] == 'CE'           # 235 at the cross (25100: 267)
    # read on the bar's own path (open → low → high → close on an up bar), as the backtest reads a fill:
    # 25,195 is 7/12 of the 25,188 → 25,200 leg, where the CE went 229 → 242
    assert p['entry_premium'] == round(229 + 7 / 12 * 13, 2)
    assert p['exit_premium'] == round(269 + 12 / 17 * 13, 2)             # 25,250 on 25,238 → 25,255; CE 269 → 282
    assert got['unpriced'] == 0 and all(o == 'CE' for _, o in calls)


def test_sc_leg_with_no_fyers_bars_stays_on_the_index():
    t = {'type': 'Short', 'entry_time': '2026-11-19 09:16:00', 'exit_time': '2026-11-19 09:18:00',
         'entry_price': 25195.0, 'exit_price': 25150.0}
    got = lvb.price_sc_fyers([t], [], lambda k, ot: [])
    assert got['unpriced'] == 1 and got['trades'] == [t]
