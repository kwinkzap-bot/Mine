import numpy as np
import pandas as pd

from trading_app.Backtest.st_bb_rsi_engine import (
    StBbRsiEngine, bollinger, rsi, supertrend, summarise)


def _frame(days):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=5 * k), 'open': cl - 0.5,
                         'high': cl + 1, 'low': cl - 1, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def test_rsi_all_gains_is_100_and_bounded():
    r = rsi(np.arange(1.0, 40.0), 14)
    assert r[14] == 100.0 and np.nanmax(r) <= 100 and np.nanmin(r) >= 0


def test_bollinger_flat_series_has_zero_width():
    up, mid, lo = bollinger(np.full(30, 10.0), 20, 2)
    assert up[25] == mid[25] == lo[25] == 10.0


def test_supertrend_up_in_rally_then_flips_down_on_drop():
    c = np.concatenate([np.linspace(100, 160, 40), np.linspace(160, 100, 30)])
    line, d = supertrend(c + 1, c - 1, c, 10, 3)
    assert d[35] == 1 and line[35] < c[35]
    assert d[-1] == -1


def test_no_trade_without_all_four_conditions():
    flat = _frame([('2025-01-01', [100.0] * 70), ('2025-01-02', [100.0] * 70)])
    trades, s = StBbRsiEngine(flat).run()
    assert trades == [] and s['total_trades'] == 0


def test_rally_enters_and_exits_on_supertrend_break():
    base = [100.0] * 75                                  # day 1 sets yesterday's pivots
    day2 = [100.0] * 25 + list(np.linspace(100, 200, 25)) + list(np.linspace(200, 100, 28))
    trades, s = StBbRsiEngine(_frame([('2025-01-01', base), ('2025-01-02', day2)])).run()
    assert len(trades) == 1
    t = trades[0]
    assert t['type'] == 'Long' and t['exit_reason'] in ('SuperTrend Down', 'Time Exit', 'EOD Exit')
    assert t['signal']['rsi'] > 70 and t['signal']['r1'] < t['signal']['close']
    assert t['entry_time'] < t['exit_time']


def test_summary_counts():
    s = summarise([{'pnl': 10, 'result': 'WIN'}, {'pnl': -4, 'result': 'LOSS'}])
    assert s['total_pnl'] == 6 and s['win_rate'] == 50.0 and s['profit_factor'] == 2.5


def test_bollinger_offset_shifts_bands():
    c = np.arange(1.0, 41.0)
    up0, _, _ = bollinger(c, 20, 2, 0)
    up2, _, _ = bollinger(c, 20, 2, 2)
    assert up2[25] == up0[23] and np.isnan(up2[20])


def test_signal_candle_green_filter_is_optional():
    base = [100.0] * 75
    day2 = [100.0] * 25 + list(np.linspace(100, 200, 25)) + list(np.linspace(200, 100, 28))
    f = _frame([('2025-01-01', base), ('2025-01-02', day2)])
    f['open'] = f['close'] + 0.5                          # every candle red
    assert StBbRsiEngine(f, require_green=True).run()[0] == []
    assert len(StBbRsiEngine(f).run()[0]) == 1
