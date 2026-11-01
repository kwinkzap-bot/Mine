import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import ma_htf_optimise as mho


def _three_min(n_days=70, seed=5):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range('2024-01-01', periods=n_days)
    idx = [d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=3 * k) for d in days for k in range(125)]
    c = 22000 + np.cumsum(rng.normal(0, 6, len(idx)))
    return pd.DataFrame({'date': idx, 'open': c + rng.normal(0, 2, len(idx)), 'close': c,
                         'high': c + abs(rng.normal(6, 2, len(idx))),
                         'low': c - abs(rng.normal(6, 2, len(idx))), 'volume': 0.0})


PAGE = dict(fast_len=11, slow_len=21, htf_minutes=15, htf_len=21, htf_type='sma', htf_calc='live',
            confirm_bars=1, max_wait=5, sl_points=35, partial_pct=0, partial_points=0, exit_on_cross=True,
            trail_points=0, trail_start=0, adx_min=0, chop_lookback=0, chop_max=2, day_bias=False,
            direction='both', entry_start='09:30', entry_end='14:30', exit_cutoff='15:14',
            max_trades_per_day=2, positional=False, price_options=True)
SMALL = [('fast_len', (9, 11)), ('slow_len', (21,)), ('htf_minutes', None), ('htf_len', (21,)),
         ('confirm_bars', (1, 2)), ('max_wait', (5,)), ('sl_points', (25.0, 35.0))]


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(mho, 'GRID', SMALL)
    monkeypatch.setattr(mho, 'NAMES', [n for n, _ in SMALL])
    monkeypatch.setattr(mho, 'MIN_TRADES', 5)
    monkeypatch.setattr(mho, 'WALK_SEEDS', 2)
    monkeypatch.setattr(mho, 'WALK_PASSES', 1)


def test_every_page_field_is_swept(small):
    """The grid and the walk between them cover every rule field the page posts."""
    res = mho.optimise(_three_min(), {**PAGE, 'price_options': False}, 3, from_date='2024-02-01')
    rule_fields = {'fast_len', 'slow_len', 'htf_minutes', 'htf_len', 'htf_type', 'htf_calc', 'confirm_bars',
                   'max_wait', 'sl_points', 'partial_pct', 'exit_on_cross', 'trail_points', 'adx_min',
                   'chop_lookback', 'chop_max', 'day_bias', 'direction', 'entry_start', 'entry_end',
                   'exit_cutoff', 'max_trades_per_day'}
    assert rule_fields <= set(res['swept'])
    assert res['mode'] == 'strategy' and res['strategy'] == 'ma_htf'
    cur = res['current']
    assert (cur['fast_len'], cur['slow_len'], cur['sl_points'], cur['entry_start']) == (11, 21, 35.0, '09:30')
    assert all(set(res['swept']) <= set(r) for r in res['results'])


def test_option_and_positional_runs_drop_what_they_ignore(small):
    res = mho.optimise(_three_min(), {**PAGE, 'positional': True}, 3, from_date='2024-02-01')
    assert 'partial_pct' not in res['swept'] and 'exit_cutoff' not in res['swept']


def test_htf_grid_sits_above_the_chart():
    assert mho.htf_values(3) == (15, 30, 60)
    assert mho.htf_values(15) == (30, 60, 120)
    assert mho.htf_values(60) == (120,)


def test_sweep_row_reproduces_the_run(small):
    """A board row, applied to a freshly built engine, is the same trades the run would book."""
    df = _three_min()
    page = {**PAGE, 'price_options': False}
    res = mho.optimise(df, page, 3, from_date='2024-02-01', top=5)
    for row in res['results'][:5]:
        eng = mho.build(df, {**page, **{k: row[k] for k in res['swept']}}, 3)
        trades = [t for t in eng.simulate() if t['entry_time'][:10] >= '2024-02-01']
        assert len(trades) == row['total_trades']
        assert round(sum(t['pnl'] for t in trades), 2) == row['total_pnl']


def test_board_rows_are_distinct_and_the_walk_only_improves(small):
    res = mho.optimise(_three_min(), {**PAGE, 'price_options': False}, 3, from_date='2024-02-01')
    rows = res['results']
    assert rows
    assert len({(r['total_trades'], r['total_pnl'], r['max_drawdown']) for r in rows}) == len(rows)
    walk_keys = [k for k, _ in mho.WALK]
    grid_only = [r for r in rows if all(r[k] == res['current'][k] for k in walk_keys if k in r)]
    assert rows[0]['ret_dd'] >= max((r['ret_dd'] for r in grid_only), default=0)
