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


def test_settings_default_to_the_backtest_form_buy_only_bounce_touch_stop_no_premium_stop(paper8, monkeypatch):
    s = algo.settings('u', algo.PROFILES[8])
    assert (s['mode'], s['direction'], s['premium'], s['sl_pct'], s['tsl_x']) == ('paper', 'long', 250, 0, 0)
    assert (s['st_period'], s['st_mult'], s['stop_touch'], s['min_wick_pct']) == (7, 1.5, 1, 25.0)
    paper8.update(ST8_DIRECTION='both', ST8_ST_MULT='2.5', ST8_STOP_TOUCH='0')
    s = algo.settings('u', algo.PROFILES[8])
    assert s['direction'] == 'both' and s['st_mult'] == 2.5 and s['stop_touch'] == 0
    paper8['ST8_DIRECTION'] = 'sideways'                                   # a bad value falls back, never trades both ways
    assert algo.settings('u', algo.PROFILES[8])['direction'] == 'long'


def test_paper_day_arm_then_cross_then_flip_books_a_history_row(paper8):
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
    assert t['status'] == 'closed' and t['result'] == 'SuperTrend Down'
    assert t['legs']['paper']['exit'] == 262.0
    hist = algo.load_history(algo.PROFILES[8])
    assert len(hist) == 1 and hist[0]['result'] == 'SuperTrend Down' and hist[0]['pnl'] > 0 and hist[0]['mode'] == 'paper'


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
