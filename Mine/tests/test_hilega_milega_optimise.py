import numpy as np
import pandas as pd

from trading_app.Backtest import hilega_milega_optimise as hmo


def _hourly(n_days=260, seed=3):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range('2024-01-01', periods=n_days)
    idx = [d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(hours=h) for d in days for h in range(7)]
    c = 20000 + np.cumsum(rng.normal(0, 40, len(idx)))
    return pd.DataFrame({'date': idx, 'open': c + rng.normal(0, 10, len(idx)), 'close': c,
                         'high': c + abs(rng.normal(30, 10, len(idx))),
                         'low': c - abs(rng.normal(30, 10, len(idx))), 'volume': 0.0})


PAGE = dict(positional=True, entry_start='10:15', max_trades_per_day=1, direction='both',
            rsi_period=9, red_len=21, arm_bars=5, min_gap=0, entry_mode='either', exit_mode='green', rr=0)


def test_board_shape_and_current_row(monkeypatch):
    monkeypatch.setattr(hmo, 'GRID', [('rsi_period', (7, 9)), ('red_len', (14, 21)), ('arm_bars', (3, 5)),
                                      ('min_gap', (0.0,)), ('entry_mode', ('breakout', 'either')),
                                      ('exit_mode', ('red', 'green')), ('rr', (0.0, 2.0))])
    res = hmo.optimise(_hourly(), PAGE, 60, from_date='2024-03-01')
    assert res['mode'] == 'strategy' and res['strategy'] == 'hilega_milega'
    walked = ['green_len', 'pb_band', 'chop_lookback', 'htf', 'sl_on_close', 'direction', 'entry_start',
              'max_trades_per_day']                  # positional: no entry cut-off or square-off
    assert res['swept'] == hmo.NAMES + walked and res['total_combos_tested'] >= 64
    assert res['positional'] and res['min_trades'] == hmo.POSITIONAL_MIN_TRADES
    cur = res['current']
    assert {k: cur[k] for k in hmo.NAMES} == dict(rsi_period=9, red_len=21, arm_bars=5, min_gap=0.0,
                                                  entry_mode='either', exit_mode='green', rr=0.0)
    assert res['results'] and all('ret_dd' in r for r in res['results'])
    assert all(set(res['swept']) <= set(r) for r in res['results'])


def test_board_holds_15_distinct_runs_and_the_walk_can_only_improve():
    res = hmo.optimise(_hourly(), PAGE, 60, from_date='2024-03-01')
    rows = res['results']
    assert len(rows) == hmo.BOARD_ROWS
    assert len({(r['total_trades'], r['total_pnl'], r['max_drawdown']) for r in rows}) == len(rows)
    grid_only = [r for r in rows if all(r[k] == res['current'][k] for k, _ in hmo.WALK if k in r)]
    assert rows[0]['ret_dd'] >= max((r['ret_dd'] for r in grid_only), default=0)


def test_sweep_row_reproduces_the_run():
    """A board row, applied to a freshly built engine, is the same trades the run would book."""
    df = _hourly()
    res = hmo.optimise(df, PAGE, 60, from_date='2024-03-01', top=3)
    row = res['results'][0]
    eng = hmo.build(df, {**PAGE, **{k: row[k] for k in res['swept']}}, 60)
    trades = [t for t in eng.simulate() if t['entry_time'][:10] >= '2024-03-01']
    assert len(trades) == row['total_trades']
    assert round(sum(t['pnl'] for t in trades), 2) == row['total_pnl']
