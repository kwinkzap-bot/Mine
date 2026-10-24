import numpy as np
import pandas as pd

from trading_app.Backtest.ha_supertrend_engine import (
    HaSupertrendEngine, heikin_ashi, summarise_r, supertrend)


def _frame(closes, spread=1.0, gap=0.0):
    idx = pd.date_range('2025-01-01', periods=len(closes), freq='B')
    c = np.asarray(closes, dtype=float)
    o = np.concatenate(([c[0]], c[:-1])) + gap
    return pd.DataFrame({'open': o, 'high': np.maximum(o, c) + spread,
                         'low': np.minimum(o, c) - spread, 'close': c, 'volume': 1000}, index=idx)


def _down_up_down():
    return _frame(list(np.linspace(200, 120, 40)) + list(np.linspace(120, 220, 40))
                  + list(np.linspace(220, 110, 30)))


def test_heikin_ashi_formula():
    df = _frame([10, 12, 11, 15])
    ha = heikin_ashi(df)
    r = df.iloc[0]
    assert abs(ha['close'].iloc[0] - (r.open + r.high + r.low + r.close) / 4) < 1e-9
    assert abs(ha['open'].iloc[0] - (r.open + r.close) / 2) < 1e-9
    assert abs(ha['open'].iloc[1] - (ha['open'].iloc[0] + ha['close'].iloc[0]) / 2) < 1e-9
    assert (ha['high'] >= ha[['open', 'close']].max(axis=1) - 1e-9).all()


def test_supertrend_follows_the_trend_and_flips():
    df = _down_up_down()
    _, d = supertrend(df['high'].to_numpy(), df['low'].to_numpy(), df['close'].to_numpy(), 10, 3.0)
    assert d[30] == -1 and d[75] == 1 and d[-1] == -1
    assert (d[:9] == 0).all()                              # no ATR yet


def test_flat_market_takes_no_trade():
    trades, s = HaSupertrendEngine(_frame([100.0] * 80)).run()
    assert trades == [] and s['total_trades'] == 0


def test_rally_gives_a_long_with_a_partial_and_a_trailed_exit():
    trades, _ = HaSupertrendEngine(_down_up_down(), direction='long').run()
    assert trades
    t = trades[0]
    assert t['type'] == 'Long' and t['entry_time'] > t['signal_time']
    assert t['sl_price'] < t['entry_price'] < t['target_price']
    assert t['partial_price'] is not None and t['partial_price'] >= t['target_price']
    assert t['exit_reason'] in ('SuperTrend flip', '2 opposite HA candles', 'Still Open')
    assert t['pnl'] > 0 and t['r_multiple'] > 1


def test_entry_fills_at_the_next_open_not_the_signal_close():
    df = _down_up_down()
    t = HaSupertrendEngine(df, direction='long').run()[0][0]
    assert t['entry_price'] == round(float(df.loc[t['entry_time'], 'open']), 2)


def test_direction_filter_and_both_sides_present():
    f = _down_up_down()
    assert all(t['type'] == 'Short' for t in HaSupertrendEngine(f, direction='short').run()[0])
    assert {t['type'] for t in HaSupertrendEngine(f, direction='both').run()[0]} == {'Long', 'Short'}


def test_one_trade_at_a_time():
    trades = HaSupertrendEngine(_down_up_down()).run()[0]
    for a, b in zip(trades, trades[1:]):
        assert b['entry_time'] > a['exit_time']


def _failed_bounce():
    return _frame(list(np.linspace(200, 120, 40)) + [125, 135, 150, 90, 85, 80])


def test_stop_is_on_the_close_and_fills_the_next_open_never_the_entry_day():
    t = HaSupertrendEngine(_failed_bounce(), direction='long', max_risk_pct=90).run()[0]
    stops = [x for x in t if x['exit_reason'] == 'Stop Loss']
    assert stops and stops[0]['result'] == 'LOSS' and stops[0]['partial_price'] is None
    assert all(x['exit_time'] > x['entry_time'] for x in t)               # positional: never same day


def test_intrabar_stop_is_still_available_and_fills_at_the_stop():
    t = HaSupertrendEngine(_failed_bounce(), direction='long', max_risk_pct=90, stop_on_close=False).run()[0]
    stops = [x for x in t if x['exit_reason'] == 'Stop Loss']
    assert stops and stops[0]['exit_price'] <= stops[0]['sl_price'] + 1e-9


def test_wide_stop_is_skipped():
    assert HaSupertrendEngine(_down_up_down(), direction='long', max_risk_pct=0.01).run()[0] == []


def test_summary_reports_expectancy():
    s = summarise_r([
        {'result': 'WIN', 'pnl_pct': 10.0, 'r_multiple': 2.0, 'bars_held': 20},
        {'result': 'LOSS', 'pnl_pct': -5.0, 'r_multiple': -1.0, 'bars_held': 4},
    ])
    assert s['win_rate'] == 50.0 and s['expectancy_r'] == 0.5
    assert s['profit_factor'] == 2.0 and s['max_drawdown'] == 5.0


def test_no_trade_ends_on_its_entry_day_by_default():
    for f in (_down_up_down(), _failed_bounce()):
        assert all(t['exit_time'] > t['entry_time'] or t['exit_reason'] == 'Still Open'
                   for t in HaSupertrendEngine(f, max_risk_pct=90).run()[0])
