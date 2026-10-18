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
    assert t['signal']['rsi'] > 70 and t['signal']['pivot_level'] < t['signal']['close']
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


def _t(i, pts):
    return {'entry_time': f'2025-01-{i:02d} 09:18:00', 'pnl': pts}


def test_compounding_starts_at_one_lot_and_steps_up_at_50k():
    from trading_app.Backtest.st_bb_rsi_engine import apply_compounding, brokerage_for
    trades = [_t(1, 400), _t(2, 400), _t(3, 400)]      # +26,000 gross on 1 lot (lot size 65)
    out = apply_compounding(trades, 65, start_capital=25000, per_lot=25000)
    assert trades[0]['lots'] == 1
    assert trades[0]['pnl_rupees'] == 400 * 65 - brokerage_for(1)       # 25,897
    # balance 25,000 + 25,897 = 50,897 → 2 lots from the next trade
    assert trades[1]['lots'] == 2
    assert out['compounding'] and out['max_lots_used'] >= 2
    assert out['total_pnl_rupees'] == round(trades[-1]['balance_after'] - 25000, 2)


def test_compounding_never_below_one_lot_and_steps_down_after_losses():
    from trading_app.Backtest.st_bb_rsi_engine import apply_compounding
    trades = [_t(1, 500), _t(2, -400), _t(3, -400), _t(4, 10)]
    apply_compounding(trades, 65, 25000, 25000)
    assert [t['lots'] for t in trades][0] == 1
    assert all(t['lots'] >= 1 for t in trades)
    assert trades[3]['lots'] == 1                      # balance fell back under 50k


def test_compounding_respects_max_lots():
    from trading_app.Backtest.st_bb_rsi_engine import apply_compounding
    trades = [_t(i, 600) for i in range(1, 15)]
    apply_compounding(trades, 65, 25000, 25000, max_lots=3)
    assert max(t['lots'] for t in trades) == 3


def test_short_is_the_mirror_and_exits_when_supertrend_turns_up():
    base = [200.0] * 75
    day2 = [200.0] * 25 + list(np.linspace(200, 100, 25)) + list(np.linspace(100, 200, 28))
    f = _frame([('2025-01-01', base), ('2025-01-02', day2)])
    assert StBbRsiEngine(f).run()[0] == []                       # long only: a fall is no signal
    trades, _ = StBbRsiEngine(f, direction='short').run()
    assert len(trades) == 1
    t = trades[0]
    assert t['type'] == 'Short' and t['pattern'].endswith('S1')
    assert t['signal']['rsi'] < 30 and t['signal']['close'] < t['signal']['pivot_level']
    assert t['exit_reason'] in ('SuperTrend Up', 'Time Exit', 'EOD Exit')
    assert t['pnl'] > 0 and abs(t['pnl'] - (t['entry_price'] - t['exit_price'])) < 0.02   # a short earns when price falls


def test_both_takes_one_trade_a_day_whichever_side_fires():
    base = [150.0] * 75
    up = [150.0] * 25 + list(np.linspace(150, 250, 25)) + list(np.linspace(250, 150, 28))
    f = _frame([('2025-01-01', base), ('2025-01-02', up)])
    trades, _ = StBbRsiEngine(f, direction='both').run()
    assert [t['type'] for t in trades] == ['Long']


def test_fixed_lots_books_rupees_with_brokerage_without_compounding():
    from trading_app.Backtest.st_bb_rsi_engine import apply_compounding, brokerage_for
    trades = [_t(1, 100), _t(2, -40)]
    out = apply_compounding(trades, 65, 100000.0, 25000.0, fixed_lots=2)
    assert [t['lots'] for t in trades] == [2, 2]
    gross = (100 - 40) * 65 * 2
    assert out['total_gross_pnl_rupees'] == gross
    assert out['total_brokerage'] == 2 * brokerage_for(2)
    assert out['total_pnl_rupees'] == gross - 2 * brokerage_for(2)       # the final, net figure
    assert out['compounding'] is False and out['server_rupees'] is True
