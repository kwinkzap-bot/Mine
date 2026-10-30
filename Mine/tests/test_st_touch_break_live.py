"""ST Touch Break live algo (Mine → NIFTY): the decisions (service/st_touch_break_live_engine.py)
against the Backtest engine they wrap, and the paper-mode thread on the Simple Trader chassis.
No broker, no network."""
import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine
from trading_app.service import st_touch_break_live_engine as tb

DAY = '2026-10-27'


def _bars(rows, start=f'{DAY} 09:15', step=3):
    t = pd.Timestamp(start)
    return pd.DataFrame([{'date': t + pd.Timedelta(minutes=step * k), 'open': o, 'high': h, 'low': l,
                          'close': c, 'volume': 0} for k, (o, h, l, c) in enumerate(rows)]).set_index('date')


def _uptrend(n=40):
    return [(100 + k, 101 + k, 99.5 + k, 100.6 + k) for k in range(n)]


S = dict(st_period=10, st_mult=2.0, direction='long', stop_touch=0, min_wick_pct=0.0, touch_tol_pct=0.0,
         max_trades=5, entry_start='09:15', entry_end='15:14', exit_cutoff='15:14')


def _touch_setup():
    """A steady climb, then a candle whose wick tags the SuperTrend line and closes back above it."""
    rows = _uptrend()
    e = StTouchBreakEngine(_bars(rows))
    line = e.indicators()['line'][len(rows) - 1]
    last = rows[-1]
    rows.append((last[3], last[3] + 1.0, line - 0.2, last[3] + 0.5))          # the touch candle
    return rows, last, line


def _hhmm_of(df, k):
    return df.index[k].strftime('%H:%M')


def test_a_touch_candle_arms_the_next_candle_and_orders_nothing_yet():
    rows, last, _ = _touch_setup()
    df = _bars(rows)
    day = tb.new_day(DAY)
    ev = tb.on_bar(day, df, S)
    assert [e['event'] for e in ev] == ['armed']
    t = day['trades'][0]
    assert t['status'] == 'armed' and t['type'] == 'Long'
    assert t['level'] == round(rows[-1][1], 2)                                  # the touch candle's HIGH
    assert t['break_start'] == df.index[-1].hour * 60 + df.index[-1].minute + 3


def test_the_order_goes_out_when_the_index_crosses_the_level_inside_the_break_candle():
    rows, _, _ = _touch_setup()
    df = _bars(rows)
    day = tb.new_day(DAY)
    tb.on_bar(day, df, S)
    t = day['trades'][0]
    start = t['break_start']
    hh = lambda m: f'{m // 60:02d}:{m % 60:02d}'
    assert tb.on_price(day, t['level'] - 0.5, hh(start + 1), S) == []           # not yet through it
    assert tb.on_price(day, t['level'] + 0.3, hh(start - 1), S) == []           # before its candle began
    ev = tb.on_price(day, t['level'] + 0.3, hh(start + 1), S)
    assert [e['event'] for e in ev] == ['entry'] and t['status'] == 'open'
    assert tb.on_price(day, t['level'] + 9, hh(start + 2), S) == []             # one entry, not two


def test_a_setup_that_never_crosses_expires_when_its_break_candle_closes():
    rows, last, _ = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), S)
    t = day['trades'][0]
    rows.append((rows[-1][3], t['level'] - 0.2, rows[-1][3] - 1, rows[-1][3] - 0.5))   # never trades above the level
    ev = tb.on_bar(day, _bars(rows), S)
    assert [e['event'] for e in ev if e['event'] == 'expired'] and t['status'] == 'expired'
    assert tb.on_price(day, t['level'] + 5, '09:50', S) == []                   # and cannot fill later


def test_a_price_far_past_the_level_is_missed_not_chased():
    rows, _, line = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), S)
    t = day['trades'][0]
    risk = t['level'] - t['line']
    hh = f"{t['break_start'] // 60:02d}:{t['break_start'] % 60:02d}"
    ev = tb.on_price(day, t['level'] + tb.MAX_CHASE_RISK_FRAC * risk + 5, hh, S)
    assert [e['event'] for e in ev] == ['missed'] and t['status'] == 'missed'


def test_direction_long_ignores_a_downtrend_touch():
    rows = [(200 - k, 200.6 - k, 199 - k, 199.4 - k) for k in range(40)]       # a steady fall
    e = StTouchBreakEngine(_bars(rows))
    line = e.indicators()['line'][len(rows) - 1]
    last = rows[-1]
    rows.append((last[3], line + 0.2, last[3] - 1.0, last[3] - 0.5))           # wick up onto the line, closes under it
    day = tb.new_day(DAY)
    assert tb.on_bar(day, _bars(rows), S) == []                                 # BUY only: nothing armed
    day2 = tb.new_day(DAY)
    assert [e['event'] for e in tb.on_bar(day2, _bars(rows), dict(S, direction='both'))] == ['armed']
    assert day2['trades'][0]['type'] == 'Short' and tb.option_type(day2['trades'][0]) == 'PE'


def test_an_open_trade_exits_on_the_close_that_flips_the_supertrend():
    rows, _, line = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), S)
    t = day['trades'][0]
    t['status'] = 'open'                                                        # as the price poll would have
    rows.append((rows[-1][3], t['level'] + 2, rows[-1][3], t['level'] + 1.5))   # the break candle, holds
    assert [e for e in tb.on_bar(day, _bars(rows), S) if e['event'] == 'exit'] == []
    rows.append((rows[-1][3], rows[-1][3], line - 30, line - 28))               # a close far under the line
    ev = tb.on_bar(day, _bars(rows), S)
    assert [e['event'] for e in ev] == ['exit'] and ev[0]['reason'] == 'SuperTrend Down'


def test_the_touch_stop_exits_between_candles_but_not_on_the_entry_candle():
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    s = dict(S, stop_touch=1)
    tb.on_bar(day, _bars(rows), s)
    t = day['trades'][0]
    t['status'] = 'open'
    rows.append((rows[-1][3], t['level'] + 2, rows[-1][3], t['level'] + 1.5))
    tb.on_bar(day, _bars(rows), s)
    assert t['stop_line'] is not None
    start = t['entry_bar_start']
    hh = lambda m: f'{m // 60:02d}:{m % 60:02d}'
    assert tb.on_price(day, t['stop_line'] - 1, hh(start + 1), s) == []         # still the entry candle
    ev = tb.on_price(day, t['stop_line'] - 1, hh(start + 3), s)
    assert ev and ev[0]['reason'] == 'SuperTrend stop'
    assert tb.on_price(day, t['stop_line'] - 1, hh(start + 3), dict(s, stop_touch=0)) == []


def test_a_wick_through_the_stop_that_the_poll_missed_exits_at_that_candles_close():
    """A candle whose low pokes through the stop line and closes back above it — no SuperTrend flip — is
    the backtest's touch exit; live, the price poll may never have seen it, so the close is the net."""
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    s = dict(S, stop_touch=1)
    tb.on_bar(day, _bars(rows), s)
    t = day['trades'][0]
    t['status'] = 'open'
    rows.append((rows[-1][3], t['level'] + 2, rows[-1][3], t['level'] + 1.5))     # the entry candle
    assert [e for e in tb.on_bar(day, _bars(rows), s) if e['event'] == 'exit'] == []
    sl = t['stop_line']
    last = rows[-1][3]
    rows.append((last, last + 0.5, sl - 0.5, last + 0.2))                          # wick through, closes back up
    df = _bars(rows)
    eng = StTouchBreakEngine(df, st_period=S['st_period'], st_mult=S['st_mult'])
    assert eng.indicators()['trend'][-1] == 1                                      # the SuperTrend did not flip
    for st, out in ((0, False), (1, True)):
        d = {**day, 'trades': [dict(t)], 'last_bar': day['last_bar']}
        ev = [e for e in tb.on_bar(d, df, dict(s, stop_touch=st)) if e['event'] == 'exit']
        assert bool(ev) is out
        if out:
            assert ev[0]['reason'] == 'SuperTrend stop' and ev[0]['index_close'] == pytest.approx(last + 0.2)
    # and the backtest exits on that same candle
    tr = StTouchBreakEngine(df, st_period=S['st_period'], st_mult=S['st_mult'], stop_mode='touch',
                            modes='bounce', direction='long', outside_bar='first').simulate()
    assert tr[-1]['exit_reason'] == 'SuperTrend stop' and tr[-1]['exit_time'][:16] >= df.index[-1].strftime('%Y-%m-%d %H:%M')


def test_it_arms_on_the_very_candles_the_backtest_enters_on():
    """The live read is the backtest's own touches(): every touch candle the backtest trades is armed."""
    rng = np.random.default_rng(11)
    rows, price = [], 22000.0
    for k in range(400):
        price += 0.7 + rng.normal(0, 3)
        o = price + rng.normal(0, 2)
        rows.append((o, max(o, price) + abs(rng.normal(0, 5)), min(o, price) - abs(rng.normal(0, 5)), price))
    df = _bars(rows, start='2026-10-26 09:15')
    eng = StTouchBreakEngine(df, modes='bounce', direction='long', outside_bar='first')
    expected = {t['signal_time'] for t in eng.run()[0]}
    armed = set()
    for i in range(len(df)):
        day = tb.new_day(str(df.index[i].date()))
        evs = tb.on_bar(day, df.iloc[:i + 1], S)
        armed |= {e['trade']['signal_bar'][:16] for e in evs if e['event'] == 'armed'}
    # a trade needs its break; an armed candle may not have one, but every traded candle was armed
    assert {x[:16] for x in expected} <= armed


# ── the thread, in paper mode (Simple Trader chassis, profile 8) ─────────────────────────────

from datetime import timedelta, timezone  # noqa: E402

from trading_app.algo.simple_trader import simple_trader_algo as algo  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


class _FakeAlgo(algo.SimpleTraderAlgo):
    def __init__(self, username, profile, frame):
        super().__init__(username, profile)
        self.frame, self.px, self.spot, self.alerts = frame, 250.0, 100.0, []      # the index sits below the setup's level

    def _bars(self, today, now):
        cut = pd.Timestamp(now.replace(tzinfo=None)) - pd.Timedelta(minutes=self.spec.bar, seconds=5)
        return self.frame[self.frame.index <= cut]

    def _spot(self):
        return self.spot

    def _premium(self, strike, option_type):
        return self.px

    def _chain_step(self):
        return 50

    def _alert(self, *a, **k):
        self.alerts.append(a)

    def _kite_premiums(self, strikes, option_type):
        return {}


@pytest.fixture()
def paper8(tmp_path, monkeypatch):
    monkeypatch.setattr(algo, '_DIR', str(tmp_path))
    env = {'ST8_MODE': 'paper'}
    fake = lambda u, name, default='': env.get(name, default)          # noqa: E731
    monkeypatch.setattr(algo, '_uvar', fake)
    monkeypatch.setattr(algo.base, '_uvar', fake)
    monkeypatch.setattr(algo.base, 'accounts', lambda u, p=None: [{'instance': 1, 'type': 'zerodha', 'lots': 2,
                                                                   'name': 'Z1', 'stoppable': True}])
    monkeypatch.setattr(algo.base, '_flag', lambda u, name, default='false': False)
    return env


def _at(hh, mm, ss=10):
    return pd.Timestamp(f'{DAY} {hh:02d}:{mm:02d}:{ss:02d}').to_pydatetime().replace(tzinfo=IST)


def test_profile_8_is_the_mine_algo_with_its_own_flags_and_files():
    pr = algo.PROFILES[8]
    assert pr.pfx == 'ST8' and pr.key == 'st8' and pr.label == 'Mine · ST Touch Break'
    assert pr.state_file().endswith('simple_trader_8_state.json') and pr.history_file().endswith('simple_trader_8_trades_history.json')
    from trading_app.app.routes import api
    assert api._STBB_FLAGS['st8'] == 'ST8'


def test_settings_default_to_the_backtest_form_both_bounce_touch_stop_body_50_no_premium_stop(paper8, monkeypatch):
    # the Backtest form of 2026-10-28: "NIFTY - Both - 3m"
    s = algo.settings('u', algo.PROFILES[8])
    assert (s['mode'], s['direction'], s['premium'], s['sl_pct'], s['tsl_x']) == ('paper', 'both', 250, 0, 0)
    assert (s['st_period'], s['st_mult'], s['stop_touch'], s['min_wick_pct']) == (7, 1.5, 1, 40.0)
    assert (s['min_body_pct'], s['outside_bar'], s['max_trades']) == (50.0, 'close', 5)
    assert algo.PROFILES[8].spec.entry_start == '10:00' and algo.PROFILES[8].spec.bar == 3
    paper8.update(ST8_DIRECTION='long', ST8_ST_MULT='2.5', ST8_STOP_TOUCH='0', ST8_OUTSIDE_BAR='first')
    s = algo.settings('u', algo.PROFILES[8])
    assert s['direction'] == 'long' and s['st_mult'] == 2.5 and s['stop_touch'] == 0 and s['outside_bar'] == 'first'
    paper8.update(ST8_DIRECTION='sideways', ST8_OUTSIDE_BAR='middle')       # a bad value falls back to the default
    s = algo.settings('u', algo.PROFILES[8])
    assert s['direction'] == 'both' and s['outside_bar'] == 'close'


def test_paper_day_arm_then_cross_then_flip_books_a_history_row(paper8):
    paper8.update(ST8_MIN_BODY_PCT='0', ST8_OUTSIDE_BAR='first')           # the intrabar entry
    rows, _, line = _touch_setup()
    df = _bars(rows)
    a = _FakeAlgo('u', algo.PROFILES[8], df)
    n = len(rows)
    touch_start = df.index[-1]
    close_at = touch_start + pd.Timedelta(minutes=3)                       # the touch candle's close
    a.tick(close_at.to_pydatetime().replace(tzinfo=IST) + pd.Timedelta(seconds=10))
    t = a.state['day']['trades'][0]
    assert t['status'] == 'armed' and not t.get('legs')                    # armed: no order, no leg
    level = t['level']
    a.spot = level - 1
    a.tick(close_at.to_pydatetime().replace(tzinfo=IST) + pd.Timedelta(seconds=40))
    assert t['status'] == 'armed'
    a.spot = level + 0.4                                                   # the break
    a.tick(close_at.to_pydatetime().replace(tzinfo=IST) + pd.Timedelta(seconds=70))
    assert t['status'] == 'open' and t['mode'] == 'paper' and t['legs']['paper']['entry'] == 250.0
    assert t['option']['type'] == 'CE'
    # the break candle holds, then a close far under the line ends it
    rows.append((rows[-1][3], level + 2, rows[-1][3], level + 1.5))
    rows.append((rows[-1][3], rows[-1][3], line - 30, line - 28))
    a.frame = _bars(rows)
    a.px = 262.0
    a.tick(close_at.to_pydatetime().replace(tzinfo=IST) + pd.Timedelta(minutes=6, seconds=10))
    # it traded through the stop line before closing past it: the backtest's touch exit comes first
    assert t['status'] == 'closed' and t['result'] == 'SuperTrend stop'
    assert t['legs']['paper']['exit'] == 262.0
    hist = algo.load_history(algo.PROFILES[8])
    assert len(hist) == 1 and hist[0]['result'] == 'SuperTrend stop' and hist[0]['pnl'] > 0 and hist[0]['mode'] == 'paper'


def test_a_setup_that_expires_leaves_no_trade_and_no_order(paper8):
    rows, _, _ = _touch_setup()
    df = _bars(rows)
    a = _FakeAlgo('u', algo.PROFILES[8], df)
    close_at = (df.index[-1] + pd.Timedelta(minutes=3)).to_pydatetime().replace(tzinfo=IST)
    a.tick(close_at + pd.Timedelta(seconds=10))
    t = a.state['day']['trades'][0]
    rows.append((rows[-1][3], t['level'] - 0.3, rows[-1][3] - 1, rows[-1][3] - 0.5))
    a.frame = _bars(rows)
    a.spot = t['level'] - 1
    a.tick(close_at + pd.Timedelta(minutes=3, seconds=10))
    assert t['status'] == 'expired' and not t.get('legs')
    assert algo.load_history(algo.PROFILES[8]) == []


def test_the_other_seven_never_poll_the_price_for_entries(paper8):
    a = _FakeAlgo('u', algo.PROFILES[3], _bars(_uptrend()))
    assert a._price_events(tb.new_day(DAY), _at(10, 0), {}) == []


# ── confirmed at the break candle's close (min body % / an outside bar's close side) ──────────────

SC = dict(S, min_body_pct=50.0, outside_bar='close')


def test_with_a_body_filter_nothing_fills_intrabar_and_no_price_is_polled():
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), SC)
    t = day['trades'][0]
    assert t['confirm']
    hh = f"{t['break_start'] // 60:02d}:{t['break_start'] % 60:02d}"
    assert not tb.watching(day, hh, SC) and tb.on_price(day, t['level'] + 0.3, hh, SC) == []
    assert t['status'] == 'armed'


def test_a_strong_break_candle_enters_at_its_close():
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), SC)
    t = day['trades'][0]
    o = rows[-1][3]
    rows.append((o, t['level'] + 3, o - 0.3, t['level'] + 2.8))              # green, body ≫ 50 %, through the high
    ev = tb.on_bar(day, _bars(rows), SC)
    assert [e['event'] for e in ev] == ['entry'] and t['status'] == 'open'
    assert t['index_cross'] == round(t['level'] + 2.8, 2) and t['stop_line'] is not None


def test_a_weak_break_candle_is_dropped_with_the_reason():
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), SC)
    t = day['trades'][0]
    o = rows[-1][3]
    rows.append((o, t['level'] + 3, o - 0.5, o + 0.2))                        # broke the high, closed near its open
    ev = tb.on_bar(day, _bars(rows), SC)
    assert [e['event'] for e in ev] == ['expired'] and 'body' in t['error']


def test_an_outside_bar_closing_down_is_not_a_long():
    rows, _, _ = _touch_setup()
    day = tb.new_day(DAY)
    tb.on_bar(day, _bars(rows), dict(SC, min_body_pct=0.0))
    t = day['trades'][0]
    o = rows[-1][3]
    rows.append((o, t['level'] + 2, t['signal']['touch_low'] - 2, o - 1.5))   # both sides broken, closed red
    ev = tb.on_bar(day, _bars(rows), dict(SC, min_body_pct=0.0))
    assert 'entry' not in [e['event'] for e in ev] and t['status'] == 'expired' and 'outside bar' in t['error']


def test_the_live_close_entries_are_the_backtests_trades():
    """Every candle the backtest (body 50 %, side it closes on, bounce) enters on is entered live at its close."""
    rng = np.random.default_rng(11)
    rows, price = [], 22000.0
    for k in range(355):                                                       # one session of 1-minute bars
        o = price
        price += 0.3 + rng.normal(0, 4)                                        # bodies big enough for the 50 % filter
        rows.append((o, max(o, price) + abs(rng.normal(0, 1.5)), min(o, price) - abs(rng.normal(0, 1.5)), price))
    df = _bars(rows, start=f'{DAY} 09:15', step=1)
    s = dict(SC, direction='both', st_period=7, st_mult=1.5)
    eng = StTouchBreakEngine(df, st_period=7, st_mult=1.5, modes='bounce', direction='both', min_body_pct=50,
                             outside_bar='close', max_trades_per_day=99, bar_minutes=1)
    want = {(tr['entry_time'][:16], tr['entry_price']) for tr in eng.simulate()}     # the close, at the close
    day = tb.new_day(DAY)
    got = set()
    for k in range(12, len(df)):
        for ev in tb.on_bar(day, df.iloc[:k + 1], dict(s, max_trades=99), bar_minutes=1):
            if ev['event'] == 'entry':
                at = pd.Timestamp(ev['bar']) + pd.Timedelta(minutes=1)
                got.add((at.strftime('%Y-%m-%d %H:%M'), ev['trade']['index_cross']))
                ev['trade']['status'] = 'closed'                               # one at a time: free the slot
    assert want and want <= got


# ── The rest of the Mine group: 9-15, the Backtest page's ★ Saved rows of 2026-10-30 ──

from types import SimpleNamespace                                              # noqa: E402

from trading_app.service import simple_trader_live_engine as sim               # noqa: E402

# n: (tab, bar, st len, st factor, setups, stop touch, wick, tol, body, outside bar, entry from, entry until,
#     square-off, max trades, direction) — the saved rows' own order
MINE_ROWS = {
    9:  ('NIFTY - Sell - 5m - #15', 5, 10, 2.5, 'through', 1, 25, 0, 0,  'first', '09:15', '14:30', '15:00', 5, 'short'),
    10: ('NIFTY - Sell - 5m - #9',  5, 14, 2,   'bounce',  1, 0,  0, 25, 'close', '10:30', '14:30', '15:14', 5, 'short'),
    11: ('NIFTY - Sell - 5m - #8',  5, 14, 2,   'bounce',  1, 25, 0, 25, 'close', '10:30', '14:30', '15:14', 5, 'short'),
    12: ('NIFTY - Sell - 3m - #6',  3, 7,  3,   'both',    1, 0,  0, 50, 'first', '10:00', '13:00', '15:00', 5, 'short'),
    13: ('NIFTY - Sell - 3m - #2',  3, 7,  3,   'both',    1, 0,  0, 50, 'first', '10:30', '13:00', '15:00', 5, 'short'),
    14: ('NIFTY - Buy - 3m - #8',   3, 14, 3,   'through', 0, 25, 0, 0,  'first', '10:30', '15:14', '15:14', 1, 'long'),
    15: ('NIFTY - Buy - 5m - #2',   5, 10, 2,   'bounce',  1, 40, 0, 50, 'first', '09:30', '15:14', '15:14', 1, 'long'),
}


@pytest.mark.parametrize('n', sorted(MINE_ROWS))
def test_each_mine_row_is_its_own_paper_algo_with_the_rows_numbers(n, tmp_path, monkeypatch):
    tab, bar, stp, stm, modes, stop, wick, tol, body, outside, start, end, cutoff, mx, direction = MINE_ROWS[n]
    monkeypatch.setattr(algo, '_DIR', str(tmp_path))
    fake = lambda u, name, default='': default                               # noqa: E731 — an empty env file
    monkeypatch.setattr(algo, '_uvar', fake)
    monkeypatch.setattr(algo.base, '_uvar', fake)
    pr = algo.PROFILES[n]
    assert pr.pfx == f'ST{n}' and pr.label == f'Mine · {tab}' and pr.spec.tab == tab and pr.spec.kind == 'tb'
    assert pr.state_file().endswith(f'simple_trader_{n}_state.json')
    s = algo.settings('u', pr)
    assert s['mode'] == 'paper'                                               # nothing live until the tab says so
    assert (s['bar'], s['st_period'], s['st_mult'], s['stop_touch'], s['min_wick_pct']) == (bar, stp, stm, stop, wick)
    assert (s['touch_tol_pct'], s['min_body_pct'], s['outside_bar'], s['max_trades']) == (tol, body, outside, mx)
    assert (s['direction'], s['modes'], s['premium'], s['sl_pct'], s['tsl_x'], s['tsl_y']) == (direction, modes, 250, 0, 0, 0)
    assert algo.SimpleTraderAlgo._window(SimpleNamespace(spec=pr.spec)) == {
        'entry_start': start, 'entry_end': end, 'exit_cutoff': cutoff}
    from trading_app.app.routes import api
    assert api._STBB_FLAGS[f'st{n}'] == f'ST{n}'


def test_the_mine_tabs_are_8_and_the_seven_saved_rows():
    assert [n for n, _, _ in sim.MINE_TABS] == list(range(8, 16))
    assert sim.MINE_TABS[0] == (8, 'mine-nifty', 'mnn')
    assert 16 not in sim.SPECS                                                # the 2026-10-29 rows are gone
    assert len({sim.SPECS[n].tab for n, _, _ in sim.MINE_TABS}) == 8
    assert sim.SPECS[9].tab == 'NIFTY - Sell - 5m - #15' and sim.SPECS[15].tab == 'NIFTY - Buy - 5m - #2'
    assert len({k for _, k, _ in sim.MINE_TABS}) == 8 and len({p for _, _, p in sim.MINE_TABS}) == 8


@pytest.mark.parametrize('n', sorted(MINE_ROWS))
def test_each_mine_row_enters_live_on_the_candles_its_backtest_trades(n, monkeypatch):
    """The row's own numbers, setups and window, walked minute by minute over 20 sessions: every trade
    its backtest enters is entered live — a close-confirmed row at that close and price, an intrabar row
    in the same minute on the same side (Through setups included) — and never outside the window."""
    tab, bar, stp, stm, modes, stop, wick, tol, body, outside, start, end, cutoff, mx, direction = MINE_ROWS[n]
    monkeypatch.setattr(tb, 'MAX_CHASE_RISK_FRAC', 1e9)                       # a minute's extreme is not a chase
    anchor = pd.Timedelta(hours=9, minutes=15)
    for seed in range(n, n + 60):                                 # the first data that gives it trades (a Through too)
        rng = np.random.default_rng(seed)
        rows, stamps, price = [], [], 22000.0
        for d in pd.bdate_range('2026-09-01', periods=20):                   # 20 sessions of 1-minute candles
            for k in range(375):
                o = price
                price += rng.normal(0, 4) + (0.6 if (d.day // 3) % 2 else -0.6)
                rows.append((o, max(o, price) + abs(rng.normal(0, 1)), min(o, price) - abs(rng.normal(0, 1)), price))
                stamps.append(pd.Timestamp(f'{d.date()} 09:15') + pd.Timedelta(minutes=k))
        mins = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=pd.DatetimeIndex(stamps))
        key = (mins.index - anchor).floor(f'{bar}min') + anchor
        df = mins.groupby(key).agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                                   close=('close', 'last'))
        eng = StTouchBreakEngine(df.reset_index(names='date'), st_period=stp, st_mult=stm, modes=modes,
                                 direction=direction, min_wick_pct=wick, touch_tol_pct=tol, min_body_pct=body,
                                 outside_bar=outside, stop_mode='touch' if stop else 'close', entry_start=start,
                                 entry_end=end, exit_cutoff=cutoff, max_trades_per_day=99, minutes=mins,
                                 bar_minutes=bar)
        trades = eng.simulate()
        if trades and (modes == 'bounce' or any(tr['setup'] == 'Through' for tr in trades)):
            break
    s = dict(sim.SPECS[n].fixed, **sim.SPECS[n].params)
    s.update(max_trades=99, entry_start=sim.SPECS[n].entry_start)              # as settings() adds it
    confirm = tb.confirm_at_close(s)
    want = {(tr['entry_time'][:16], tr['entry_price'] if confirm else tr['type']) for tr in trades}
    got, day, nxt = set(), None, 0
    bar_starts = list(df.index)
    for ts, m in mins.iterrows():
        date_str = str(ts.date())
        if day is None or day['date'] != date_str:
            day = tb.new_day(date_str)
        while nxt < len(bar_starts) and bar_starts[nxt] + pd.Timedelta(minutes=bar) <= ts:
            for ev in tb.on_bar(day, df.iloc[:nxt + 1], s, bar_minutes=bar):          # a candle closed
                if ev['event'] == 'entry':
                    at = pd.Timestamp(ev['bar']) + pd.Timedelta(minutes=bar)
                    got.add((at.strftime('%Y-%m-%d %H:%M'), ev['trade']['index_cross']))
                    ev['trade']['status'] = 'closed'                           # one at a time: free the slot
            nxt += 1
        for px in (m['high'], m['low']):                                       # the minute's two extremes
            for ev in tb.on_price(day, px, ts.strftime('%H:%M'), s, bar_minutes=bar):
                if ev['event'] == 'entry':
                    got.add((ts.strftime('%Y-%m-%d %H:%M'), ev['trade']['type']))
                    ev['trade']['status'] = 'closed'
    assert want, 'the synthetic sessions gave the backtest nothing to compare'
    assert want <= got
    if modes != 'bounce':
        assert any(tr['setup'] == 'Through' for tr in trades)                  # the new path was exercised
    first = pd.Timedelta(minutes=bar if confirm else 0)
    bad = [t for t, _ in got if not start <= (pd.Timestamp(t) - first).strftime("%H:%M") <= end]
    assert not bad, bad[:5]


def test_a_through_setup_arms_the_side_that_breaks_through_the_line():
    """An uptrend touch on a SELL-only Through row arms a short below the touch LOW; on a Bounce row it
    arms nothing (the bounce side is a buy)."""
    rows, _, _ = _touch_setup()
    for modes, armed in (('through', True), ('bounce', False)):
        day = tb.new_day(DAY)
        ev = tb.on_bar(day, _bars(rows), dict(S, direction='short', modes=modes, outside_bar='first'))
        if armed:
            t = [e['trade'] for e in ev if e['event'] == 'armed'][0]
            assert t['type'] == 'Short' and t['setup'] == 'Through' and t['level'] == round(rows[-1][2], 2)
            assert t['risk'] == round(rows[-1][1] - rows[-1][2], 2)            # the touch candle's range
        else:
            assert not [e for e in ev if e['event'] == 'armed']
