import numpy as np
import pandas as pd

from trading_app.Backtest.st_rsi_pp_bb_engine import StRsiPpBbEngine


def _frame(days):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=3 * k), 'open': cl, 'high': cl + 0.5,
                         'low': cl - 0.5, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _rally_day():
    # flat, a hard run-up through yesterday's R1 and the upper band, then flat to the close
    return [100.0] * 30 + list(np.linspace(100, 140, 14)) + [140.0] * 56


def test_defaults_are_the_quantman_run():
    e = StRsiPpBbEngine(_frame([('2025-01-01', [100.0] * 100)]))
    assert (e.st_period, e.st_mult) == (10, 2.3)
    assert (e.bb_period, e.bb_mult) == (15, 3.0)
    assert (e.rsi_period, e.rsi_level) == (14, 70.0)
    assert e.entry_start == 9 * 60 + 31 and e.max_trades_per_day == 5 and e.direction == 'long'


def test_long_entry_on_the_closed_candle_fills_next_open():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    trades, _ = StRsiPpBbEngine(df).run()
    assert trades and all(t['type'] == 'Long' for t in trades)
    t = trades[0]
    assert t['entry_time'] > '2025-01-02 09:31'
    sig = t['signal']
    assert sig['rsi'] > 70 and sig['close'] > sig['bb_band'] and sig['close'] > sig['pivot_level']
    assert sig['close'] > sig['supertrend']


def test_no_entry_before_the_window_opens():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', list(np.linspace(100, 140, 14)) + [140.0] * 86)])
    for t in StRsiPpBbEngine(df).run()[0]:
        assert t['entry_time'][11:16] >= '09:31'


def test_max_trades_per_day_and_trade_after_are_capped():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    e = StRsiPpBbEngine(df)
    first = e.run()[0][0]
    assert e.trade_after('2025-01-02', first['entry_time'], taken=5) is None
    nxt = e.trade_after('2025-01-02', '2025-01-02 09:00:00', taken=1)
    assert nxt is not None and nxt['entry_time'] > '2025-01-02 09:00:00'
    # strictly after: a stop inside the entry minute does not re-enter on that candle
    later = e.trade_after('2025-01-02', first['entry_time'], taken=1)
    assert later is None or later['entry_time'] > first['entry_time']
    assert e.trade_after('2030-01-01', '2030-01-01 09:00:00', taken=1) is None


def test_future_run_defaults_and_green_candle_gate():
    from trading_app.Backtest.st_rsi_pp_bb_fut_engine import StRsiPpBbFutEngine
    e = StRsiPpBbFutEngine(_frame([('2025-01-01', [100.0] * 100)]))
    assert (e.st_period, e.st_mult) == (10, 1.8)
    assert (e.rsi_period, e.rsi_level) == (20, 68.0)
    assert (e.bb_period, e.bb_mult) == (20, 3.3)
    assert e.entry_start == 9 * 60 + 15 and e.max_trades_per_day == 5
    assert e.direction == 'long' and e.require_green is True


def test_future_run_needs_a_green_candle():
    from trading_app.Backtest.st_rsi_pp_bb_fut_engine import StRsiPpBbFutEngine
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _rally_day())])
    green = StRsiPpBbFutEngine(df)
    assert all(t['signal']['close'] > 0 for t in green.run()[0])
    # a candle that closes under its own open never signals
    red = df.copy()
    red['open'] = red['close'] + 1.0
    assert StRsiPpBbFutEngine(red).run()[0] == []
