import numpy as np
import pandas as pd

from trading_app.Backtest.st_touch_break_engine import StTouchBreakEngine


def _bars(rows, start='2026-10-27 09:15', step=3):
    t = pd.Timestamp(start)
    return pd.DataFrame([{'date': t + pd.Timedelta(minutes=step * k), 'open': o, 'high': h,
                          'low': l, 'close': c, 'volume': 0} for k, (o, h, l, c) in enumerate(rows)])


def _uptrend(n=40):
    """A steady climb: SuperTrend is up and well under price."""
    return [(100 + k, 101 + k, 99.5 + k, 100.6 + k) for k in range(n)]


def test_bounce_long_enters_on_the_break_and_rides_the_supertrend():
    rows = _uptrend()
    e = StTouchBreakEngine(_bars(rows))
    ind = e.indicators()
    line = ind['line'][len(rows) - 1]
    last = rows[-1]
    # touch candle: wick down onto the line, closes back above it
    rows.append((last[3], last[3] + 1.0, line - 0.2, last[3] + 0.5))
    # next candle breaks the touch candle's high
    rows.append((last[3] + 0.5, last[3] + 3.0, last[3] + 0.4, last[3] + 2.5))
    # then the trend breaks: a close well under the line
    rows.append((last[3] + 2.5, last[3] + 2.6, line - 15, line - 14))
    rows.append((line - 14, line - 13, line - 16, line - 15))
    trades, summ = StTouchBreakEngine(_bars(rows), outside_bar='first').run()     # nothing read off the close
    t = [x for x in trades if x['signal_time'][11:16] == _bars(rows)['date'].iloc[len(rows) - 4].strftime('%H:%M')]
    assert t and t[0]['type'] == 'Long' and t[0]['setup'] == 'Bounce'
    assert t[0]['entry_price'] == round(last[3] + 1.0, 2)          # the touch candle's high
    assert t[0]['exit_reason'] == 'SuperTrend flip' and t[0]['result'] == 'LOSS'
    assert summ['total_trades'] == len(trades)


def test_no_break_no_trade():
    rows = _uptrend()
    e = StTouchBreakEngine(_bars(rows))
    line = e.indicators()['line'][len(rows) - 1]
    last = rows[-1]
    rows.append((last[3], last[3] + 1.0, line - 0.2, last[3] + 0.5))     # touch
    rows.append((last[3] + 0.5, last[3] + 0.9, last[3] + 0.1, last[3] + 0.6))   # inside the touch candle
    trades, _ = StTouchBreakEngine(_bars(rows)).run()
    assert not [t for t in trades if t['signal_time'][:16] == _bars(rows)['date'].iloc[-2].strftime('%Y-%m-%d %H:%M')]


def test_touch_is_not_a_flip_candle():
    rows = _uptrend()
    e = StTouchBreakEngine(_bars(rows))
    line = e.indicators()['line'][len(rows) - 1]
    rows.append((rows[-1][3], rows[-1][3] + 0.5, line - 6, line - 5))    # closes under the line: flips
    e2 = StTouchBreakEngine(_bars(rows))
    assert e2.touches()[-1] == 0


def test_wick_filter_drops_a_stubby_touch():
    rows = _uptrend()
    e = StTouchBreakEngine(_bars(rows))
    line = e.indicators()['line'][len(rows) - 1]
    last = rows[-1][3]
    # a candle whose body reaches the line: no real wick
    rows.append((line + 0.1, last + 1, line - 0.05, last + 0.5))
    assert StTouchBreakEngine(_bars(rows), min_wick_pct=0).touches()[-1] == 1
    assert StTouchBreakEngine(_bars(rows), min_wick_pct=50).touches()[-1] == 0


def test_squares_off_at_the_cutoff_and_never_carries_overnight():
    rows = _uptrend(120)                      # 120 x 3 min from 09:15 runs past 15:14
    trades, _ = StTouchBreakEngine(_bars(rows)).run()
    for t in trades:
        assert t['entry_time'][:10] == t['exit_time'][:10]


def test_entry_carries_the_minute_the_level_was_crossed_and_trade_after_reenters():
    rows = _uptrend()
    e0 = StTouchBreakEngine(_bars(rows))
    line = e0.indicators()['line'][len(rows) - 1]
    last = rows[-1][3]
    rows.append((last, last + 1.0, line - 0.2, last + 0.5))                     # touch
    rows.append((last + 0.5, last + 3.0, last + 0.4, last + 2.5))               # break, bar 09:15 + 3*41
    rows += [(last + 2.5, last + 3.5, last + 2.0, last + 3.0)] * 3
    bars = _bars(rows)
    bar_start = bars['date'].iloc[len(_uptrend()) + 1]
    mins = pd.DataFrame([{'date': bar_start + pd.Timedelta(minutes=m), 'open': 0, 'high': hi, 'low': 0,
                          'close': 0} for m, hi in enumerate([last + 0.8, last + 0.9, last + 1.5])]
                        ).set_index('date')
    eng = StTouchBreakEngine(bars, minutes=mins, bar_minutes=3, outside_bar='first')
    trades, _ = eng.run()
    t = [x for x in trades if x['signal_time'][:16] == (bar_start - pd.Timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M')][0]
    assert t['entry_time'] == (bar_start + pd.Timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S')   # 3rd minute broke the high
    # a stop minute before that entry leaves it available; after it, nothing on that day
    day = t['entry_time'][:10]
    assert eng.trade_after(day, t['entry_time'][:11] + '00:00:00', 1) is not None
    assert eng.trade_after(day, t['entry_time'], 1) is None
    assert eng.trade_after(day, t['entry_time'][:11] + '00:00:00', 999) is None


def test_outside_bar_follows_its_close_by_default():
    rows = [(100 + k * 0.1, 100.5 + k * 0.1, 99.5 + k * 0.1, 100.1 + k * 0.1) for k in range(40)]
    e0 = StTouchBreakEngine(_bars(rows))
    line = e0.indicators()['line'][-1]
    last = rows[-1][3]
    rows.append((last, last + 1.0, line - 0.2, last + 0.3))                  # touch
    rows.append((last + 0.3, last + 3.0, line - 0.5, last + 2.8))            # breaks both, closes green
    long_t = [t for t in StTouchBreakEngine(_bars(rows)).run()[0] if t['signal_time'][:16] == _bars(rows)['date'].iloc[-2].strftime('%Y-%m-%d %H:%M')]
    assert long_t and long_t[0]['type'] == 'Long'


def test_a_break_judged_on_its_close_is_filled_at_that_close():
    """Body filter / outside bar 'close' read the break candle's close, so the fill is that close,
    stamped the next bar's first minute — what the live tab does — never the level crossed before it."""
    rows = _uptrend()
    line = StTouchBreakEngine(_bars(rows)).indicators()['line'][len(rows) - 1]
    last = rows[-1][3]
    rows.append((last, last + 1.0, line - 0.2, last + 0.5))                     # touch
    rows.append((last + 0.5, last + 3.0, last + 0.4, last + 2.5))               # break, body 2.0 of 2.6
    rows += [(last + 2.5, last + 3.5, last + 2.0, last + 3.0)] * 3
    bars = _bars(rows)
    brk = bars['date'].iloc[len(_uptrend()) + 1]
    for kw in (dict(min_body_pct=50, outside_bar='first'), dict(outside_bar='close')):
        trades, _ = StTouchBreakEngine(bars, **kw).run()
        t = [x for x in trades if x['signal_time'][:16] == (brk - pd.Timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M')][0]
        assert t['entry_price'] == round(last + 2.5, 2)                         # the close, not last + 1.0
        assert t['entry_time'] == (brk + pd.Timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
        assert t['intrabar_entry'] is False
    # a long break candle the SuperTrend flips against on its (green) close is no trade at all
    rows[len(_uptrend()) + 1] = (line - 22, last + 3.0, line - 30, line - 20)
    trades, _ = StTouchBreakEngine(_bars(rows), outside_bar='close', modes='bounce').run()
    assert not [x for x in trades if x['signal_time'][:16] == (brk - pd.Timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M')]
