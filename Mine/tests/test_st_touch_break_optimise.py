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
    assert set(stbo.SWEPT) == {'timeframe', *stbo.CORE, *stbo.EXTRA_AXES}
    # every option in the Logic popup is on the board
    for k in ('touch_tol_pct', 'min_body_pct', 'outside_bar', 'entry_start', 'entry_end', 'exit_cutoff',
              'max_trades_per_day'):
        assert k in stbo.SWEPT


def test_optimise_returns_a_board_shaped_like_the_simple_trader_ones():
    res = stbo.optimise(_bars(), {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=10)
    assert res['mode'] == 'strategy' and res['strategy'] == 'st_touch_break'
    assert res['swept'] == stbo.SWEPT and stbo.grid_size() <= res['total_combos_tested'] <= stbo.grid_size() + stbo.walk_size()
    assert res['results'] and len(res['results']) <= 10
    row = res['results'][0]
    for k in [k for k in stbo.SWEPT if k != 'timeframe'] + ['total_trades', 'win_rate', 'total_pnl', 'profit_factor', 'max_drawdown', 'recent_pnl']:
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


def test_each_direction_is_swept_on_its_own_trades():
    df = _bars()
    assert stbo.DIRECTIONS == ('both', 'long', 'short')
    boards = {d: stbo.optimise(df, {'st_period': 10, 'st_mult': 2, 'direction': d}, from_date='2026-01-01', top=5)
              for d in stbo.DIRECTIONS}
    n = {d: boards[d]['current']['total_trades'] for d in boards}
    # not additive: the two sides share one position slot and the day's trade cap
    assert n['long'] and n['short'] and n['both']
    assert len({n['long'], n['short']}) == 2 or boards['long']['current']['total_pnl'] != boards['short']['current']['total_pnl']


def test_every_logic_option_round_trips_through_a_row():
    df = _bars()
    res = stbo.optimise(df, {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=10 ** 6)
    row = next(r for r in res['results'] if r['entry_start'] != '09:15' or r['max_trades_per_day'] != 5
               or r['min_body_pct'] or r['touch_tol_pct'] or r['outside_bar'] != 'close'
               or r['entry_end'] != '15:14' or r['exit_cutoff'] != '15:14')
    eng = stbo.build(df, {})
    stbo._apply(eng, row)
    assert {k: stbo._params(eng)[k] for k in stbo.SWEPT if k != 'timeframe'} == \
        {k: row[k] for k in stbo.SWEPT if k != 'timeframe'}
    trades = stbo._trades(eng, '2026-01-01')
    assert len(trades) == row['total_trades']


def test_the_walk_reaches_options_the_core_grid_holds_fixed():
    res = stbo.optimise(_bars(), {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=10 ** 6)
    assert res['total_combos_tested'] > stbo.grid_size()
    assert len({r['entry_start'] for r in res['results']}) > 1 or len({r['max_trades_per_day'] for r in res['results']}) > 1


def test_timeframes_are_searched_together_and_ranked_on_one_board():
    base = _bars()
    five = base.set_index('date').resample('15min').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna().reset_index()
    frames = {'3minute': base, '5minute': base, '15minute': five}
    res = stbo.optimise_timeframes(lambda tf: frames.get(tf), {'st_period': 10, 'st_mult': 2, 'interval': '3minute'},
                                   from_date='2026-01-01', top=50)
    assert res['timeframes'] == list(stbo.TIMEFRAMES)
    assert 'timeframe' in res['swept'] and {r['timeframe'] for r in res['results']} >= {'3minute'}
    assert res['current']['timeframe'] == '3minute'


def test_priced_rows_carry_their_book_for_the_combined_popup(monkeypatch):
    # The 🧩 Combined popup merges ticked rows on `book` ([entry minute, net ₹] per trade); with the
    # option leg stubbed to the index trade itself, the book must add up to the row's own net ₹.
    df = _bars()
    res = stbo.optimise(df, {'st_period': 10, 'st_mult': 2}, from_date='2026-01-01', top=5)
    monkeypatch.setattr(stbo, 'attach_option_legs', lambda sym, batch, *a, **k: (batch, {'lot': 65}))
    stbo.verify_on_option(res, df, None, {'st_period': 10, 'st_mult': 2}, 'NIFTY', '3minute', [],
                          '2026-01-01', shortlist=2)
    for row in res['results'] + [res['current']]:
        assert len(row['book']) == row['total_trades']
        assert all(len(e) == len(stbo.BOOK_FIELDS) and len(e[0]) == 16 for e in row['book'])
        net = stbo.BOOK_FIELDS.index('net')
        assert abs(sum(e[net] for e in row['book']) - row['net_rs']) <= len(row['book'])   # ₹1 rounding per trade
