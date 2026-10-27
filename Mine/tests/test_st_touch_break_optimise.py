import numpy as np
import pandas as pd

from trading_app.Backtest import st_touch_break_optimise as stbo


def _bars(days=40):
    rng = np.random.default_rng(7)
    rows = []
    price = 100.0
    for d in pd.bdate_range('2026-01-01', periods=days):
        t = d + pd.Timedelta(hours=9, minutes=15)
        for k in range(100):
            o = price
            price = o + rng.normal(0.02, 0.6)
            rows.append({'date': t + pd.Timedelta(minutes=3 * k), 'open': o,
                         'high': max(o, price) + abs(rng.normal(0, 0.3)),
                         'low': min(o, price) - abs(rng.normal(0, 0.3)), 'close': price, 'volume': 0})
    return pd.DataFrame(rows)


def test_grid_size_matches_the_swept_axes():
    assert stbo.grid_size() == 3 * 4 * 3 * 2 * 3


def test_optimise_returns_a_board_shaped_like_the_simple_trader_ones():
    res = stbo.optimise(_bars(), {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=10)
    assert res['mode'] == 'strategy' and res['strategy'] == 'st_touch_break'
    assert res['swept'] == stbo.SWEPT and res['total_combos_tested'] == stbo.grid_size()
    assert res['results'] and len(res['results']) <= 10
    row = res['results'][0]
    for k in stbo.SWEPT + ['total_trades', 'win_rate', 'total_pnl', 'profit_factor', 'max_drawdown', 'recent_pnl']:
        assert k in row
    cur = res['current']
    assert (cur['st_period'], cur['st_mult'], cur['modes'], cur['stop_mode']) == (10, 2.0, 'both', 'close')


def test_a_board_row_reproduces_as_a_plain_run():
    df = _bars()
    res = stbo.optimise(df, {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=5)
    row = res['results'][0]
    eng = stbo.build(df, {})
    stbo._apply(eng, row)
    trades = stbo._trades(eng, '2026-01-01')
    assert len(trades) == row['total_trades']
    assert round(sum(t['pnl'] for t in trades), 2) == row['total_pnl']
