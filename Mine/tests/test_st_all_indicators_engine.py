import numpy as np
import pandas as pd

from trading_app.Backtest.st_all_indicators_engine import StAllIndicatorsEngine
from trading_app.Backtest.st_ema_rsi_cci_engine import cci, ema


def _frame(days, step=3):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=step * k), 'open': cl, 'high': cl + 0.5,
                         'low': cl - 0.5, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _rally_day():
    return [100.0] * 30 + list(np.linspace(100, 140, 14)) + [140.0] * 31


def _df():
    return _frame([('2025-01-01', [100.0] * 75), ('2025-01-02', _rally_day())])


LOOSE = dict(ema_period=20, rsi_level=55, rsi_cap=101, wr_level=-40, cpr_narrow=5.0, cci_level=0.0)


def test_defaults_ask_for_every_indicator():
    e = StAllIndicatorsEngine(_frame([('2025-01-01', [100.0] * 75)]))
    assert (e.st_period, e.st_mult) == (10, 2.0)
    assert (e.rsi_level, e.rsi_cap, e.wr_level, e.cci_level, e.ema_period) == (60.0, 85.0, -30.0, 100.0, 200)
    assert all((e.use_st, e.use_rsi, e.use_bb, e.use_wr, e.use_cci, e.use_ema, e.use_pivot))
    assert e.direction == 'long'


def test_trades_and_labels_the_pattern():
    trades, _ = StAllIndicatorsEngine(_df(), **LOOSE).run()
    assert trades and all(t['pattern'] == 'ST+EMA+RSI+CCI+WR+CPR+PP+BB' for t in trades)
    assert all(t['type'] == 'Long' for t in trades)


def test_cci_and_ema_gates_only_narrow_the_rule():
    on = StAllIndicatorsEngine(_df(), **LOOSE).signals()['signal']
    off = StAllIndicatorsEngine(_df(), use_cci=False, use_ema=False, **LOOSE).signals()['signal']
    assert (on != 0).sum() <= (off != 0).sum() and not ((on != 0) & (off == 0)).any()


def test_unreachable_cci_blocks_and_its_switch_restores():
    kw = dict(LOOSE, cci_level=1e6)
    assert StAllIndicatorsEngine(_df(), **kw).run()[0] == []
    assert StAllIndicatorsEngine(_df(), use_cci=False, **kw).run()[0]


def test_ema_gate_requires_close_above_the_ema():
    e = StAllIndicatorsEngine(_df(), **LOOSE)
    sg = e.signals()
    c = e._base()['c']
    hit = sg['signal'] == 1
    assert hit.any() and (c[hit] > ema(c, 20)[hit]).all()
    b = e._base()
    assert (sg['cci'][hit] > 0).all() and np.allclose(sg['cci'][hit], cci(b['h'], b['l'], b['c'], 20)[hit])


def test_rsi_off_drops_the_band_too():
    e = StAllIndicatorsEngine(_df(), use_rsi=False, rsi_cap=1.0)
    assert e.rsi_cap > 100


def _walk_frame(days=60, seed=3):
    rng = np.random.default_rng(seed)
    rows, px = [], 100.0
    for d in pd.bdate_range('2025-01-01', periods=days):
        t = pd.Timestamp(f'{d.date()} 09:15')
        for k in range(75):
            px *= 1 + rng.normal(0.0002, 0.003)
            rows.append({'date': t + pd.Timedelta(minutes=3 * k), 'open': px, 'high': px * 1.001,
                         'low': px * 0.999, 'close': px, 'volume': 0})
    return pd.DataFrame(rows)


def test_find_best_params_searches_which_indicators_stay_on(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto
    monkeypatch.setattr(sto, 'MIN_TRADES', 3)
    monkeypatch.setattr(sto, 'GATE_SHORTLIST', 2)
    res = sto.optimise('st_all_indicators', _walk_frame(), {}, from_date='2025-01-01')
    assert res['swept'][0] == 'gates' and res['gate_subsets'] > 100
    assert res['results'], 'no board'
    gate_sets = {r['gates'] for r in res['results']}
    assert len(gate_sets) > 1                              # not only "all eight on"
    for r in res['results']:
        on = [g for g in sto._GATES if r['use_' + g]]
        assert r['gates'] == '+'.join(sto.GATE_LABEL[g] for g in on)
        assert ('st_period' in r) == r['use_st'] and ('cci_level' in r) == r['use_cci']
    assert res['current']['gates'] == 'ST+RSI+BB+%R+CCI+EMA+PP+CPR'


def test_rank_quality_prefers_rupee_drawdown_and_net():
    from trading_app.Backtest.st_bb_rsi_engine import rank_quality
    mk = lambda dd, net: dict(win_rate=60, recent_pnl=1, total_trades=200, profit_factor=2.0,
                              max_drawdown=1, total_pnl=1, max_dd_rs=dd, net_rs=net)
    rows = [mk(5000, 9000), mk(3000, 4000), mk(3000, 8000), mk(1000, -50)] * 2
    ordered, passed = rank_quality(rows)
    assert passed == 6 and (ordered[0]['max_dd_rs'], ordered[0]['net_rs']) == (3000, 8000)


def _hourly_walk(days=400, seed=5):
    rng = np.random.default_rng(seed)
    rows, px = [], 100.0
    for d in pd.bdate_range('2024-01-01', periods=days):
        t = pd.Timestamp(f'{d.date()} 09:15')
        for k in range(6):
            px *= 1 + rng.normal(0.0004, 0.004)
            rows.append({'date': t + pd.Timedelta(hours=k), 'open': px, 'high': px * 1.002,
                         'low': px * 0.998, 'close': px, 'volume': 0})
    return pd.DataFrame(rows)


def test_positional_carries_trades_across_sessions():
    kw = dict(use_ema=False, use_cci=False, use_wr=False, use_pivot=False, use_cpr_narrow=False,
              use_rsi=False, rsi_cap=101)
    df = _hourly_walk()
    held = StAllIndicatorsEngine(df, positional=True, **kw).run()[0]
    intraday = StAllIndicatorsEngine(df, positional=False, **kw).run()[0]
    assert held and max(t['days_held'] for t in held) >= 1
    assert all(t['days_held'] == 0 for t in intraday)


def test_positional_board_uses_looser_trade_floors(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto
    monkeypatch.setattr(sto, 'GATE_SHORTLIST', 1)
    res = sto.optimise('st_all_indicators', _hourly_walk(), {'positional': True}, from_date='2024-01-01')
    assert res['positional'] is True
    assert (res['min_trades'], res['min_board_trades']) == (sto.POSITIONAL_MIN_TRADES, sto.POSITIONAL_MIN_BOARD)
    assert res['results'] is not None


def _fake_feed(monkeypatch, per_day):
    """attach_positional_legs against a scripted option feed: {date: [(HH:MM, o, h, l, c), ...]}."""
    from datetime import date
    from trading_app.Backtest import hilega_milega_engine as hm, st_bb_rsi_engine as sb
    from trading_app.service import cpr_option_service as cos
    bars = lambda rows: [dict(time=t, open=o, high=h, low=l, close=c) for t, o, h, l, c in rows]
    feed = {date.fromisoformat(d): bars(r) for d, r in per_day.items()}
    expiry = '2025-02-28'
    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda sym: (lambda con, d: feed.get(d, [])))
    monkeypatch.setattr(cos, 'contract', lambda sym, d, side, lvl, cal: dict(
        strike=100, option_type='CE' if side == 'BUY' else 'PE', expiry=expiry))
    monkeypatch.setattr(cos, 'lot_size', lambda sym: 65)
    monkeypatch.setattr(sb, '_listed_expiries', lambda sym, after: [])
    return hm, sorted(feed)


def _trade(entry='2025-01-02 09:15:00', exit_='2025-01-06 09:15:00'):
    return dict(entry_time=entry, exit_time=exit_, type='Long', entry_price=100.0, exit_price=110.0,
                pnl=10.0, result='WIN')


def test_positional_premium_stop_ends_a_multi_day_hold(monkeypatch):
    hm, days = _fake_feed(monkeypatch, {
        '2025-01-02': [('09:15', 100, 100, 100, 100), ('10:00', 100, 101, 99, 100)],
        '2025-01-03': [('09:15', 98, 99, 97, 98), ('11:00', 98, 98, 80, 82)],          # low 80 <= stop 85
        '2025-01-06': [('09:15', 120, 125, 118, 122)]})
    priced, info = hm.attach_positional_legs('NIFTY', [_trade()], 0.0, None, calendar=days,
                                             sl_pct=15.0, tsl_x=0.0, tsl_y=0.0)
    (t,) = priced
    assert t['exit_reason'] == 'Stop Loss' and t['exit_premium'] == 85.0 and t['pnl'] == -15.0
    assert t['exit_time'] == '2025-01-03 11:00:00' and t['days_held'] == 1 and info['stops'] == 1
    assert t['index_exit_time'] == '2025-01-06 09:15:00'


def test_positional_without_a_stop_rides_to_the_index_exit(monkeypatch):
    hm, days = _fake_feed(monkeypatch, {
        '2025-01-02': [('09:15', 100, 100, 100, 100), ('10:00', 100, 101, 99, 100)],
        '2025-01-03': [('09:15', 98, 99, 97, 98), ('11:00', 98, 98, 80, 82)],
        '2025-01-06': [('09:15', 120, 125, 118, 122)]})
    priced, info = hm.attach_positional_legs('NIFTY', [_trade()], 0.0, None, calendar=days)
    (t,) = priced
    assert t['exit_premium'] == 122 and t['pnl'] == 22.0 and 'exit_reason' not in t and 'stops' not in info


def test_positional_trailing_stop_carries_across_sessions(monkeypatch):
    hm, days = _fake_feed(monkeypatch, {
        '2025-01-02': [('09:15', 100, 100, 100, 100), ('10:00', 100, 130, 100, 128)],   # +30%: stop trails to 100+10%
        '2025-01-03': [('09:15', 125, 126, 104, 110), ('11:00', 110, 111, 108, 110)],
        '2025-01-06': [('09:15', 115, 120, 112, 118)]})
    priced, _ = hm.attach_positional_legs('NIFTY', [_trade()], 0.0, None, calendar=days,
                                          sl_pct=15.0, tsl_x=10.0, tsl_y=10.0)
    (t,) = priced
    # 3 steps of 10 % -> stop 85 + 3*10 = 115 rests overnight; the next morning's low 104 hits it
    assert t['exit_reason'] == 'Trailing SL' and t['exit_premium'] == 115.0 and t['exit_time'].startswith('2025-01-03 09:15')


def test_positional_engine_has_no_window_and_one_entry_a_day():
    e = StAllIndicatorsEngine(_hourly_walk(5), positional=True, entry_start='10:30', max_trades_per_day=5)
    assert (e.max_trades_per_day, e.entry_start, e.entry_end) == (1, 9 * 60 + 15, 23 * 60 + 59)


def _minute_walk(days=40, seed=11):
    rng = np.random.default_rng(seed)
    rows, px = [], 100.0
    for d in pd.bdate_range('2025-01-01', periods=days):
        t = pd.Timestamp(f'{d.date()} 09:15')
        for k in range(375):
            px *= 1 + rng.normal(0.00004, 0.0008)
            rows.append({'date': t + pd.Timedelta(minutes=k), 'open': px, 'high': px * 1.0004,
                         'low': px * 0.9996, 'close': px, 'volume': 0})
    return pd.DataFrame(rows).set_index('date')


def _bars_for(minutes):
    """The route's resample (api._sbr_bars): one candle per `tf`, anchored 09:15."""
    mins = {'minute': 1, '2minute': 2, '3minute': 3, '5minute': 5, '15minute': 15, '60minute': 60, 'day': 1440}

    def bars_for(tf):
        anchor = pd.Timedelta(hours=9, minutes=15)
        key = (minutes.index - anchor).floor(f'{mins[tf]}min') + anchor
        b = minutes.groupby(key).agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                                     close=('close', 'last'), volume=('volume', 'sum'))
        b.index.name = 'date'
        return b.reset_index()
    return bars_for


def _small_grid(monkeypatch, sto):
    monkeypatch.setitem(sto.SPECS, 'st_all_indicators', (sto._all_ind, lambda d: [
        ('st_period', (10,)), ('st_mult', (2.0,)), ('bb_period', (20,)), ('bb_mult', (2.0,)),
        ('rsi_level', (55, 60)), ('wr_level', (-30,)), ('cci_level', (0,))], False))
    for k in ('MIN_TRADES', 'POSITIONAL_MIN_TRADES'):
        monkeypatch.setattr(sto, k, 1)


def test_timeframe_lists_are_the_dropdowns_own():
    from trading_app.Backtest import simple_trader_optimise as sto
    assert sto.TIMEFRAMES == {'intraday': ('minute', '2minute', '3minute', '5minute', '15minute'),
                              'positional': ('60minute', 'day')}


def test_find_best_params_searches_timeframes_on_two_boards(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto
    _small_grid(monkeypatch, sto)
    monkeypatch.setattr(sto, 'GATE_SHORTLIST', 1)
    monkeypatch.setattr(sto, 'TIMEFRAMES', {'intraday': ('3minute', '15minute'), 'positional': ('60minute', 'day')})
    calls = []
    boards = sto.optimise_timeframes('st_all_indicators', _bars_for(_minute_walk()),
                                     {'interval': '15minute', 'ema_period': 20}, from_date='2025-01-01',
                                     progress=lambda done, total, **kw: calls.append(kw))
    intra, pos = boards['intraday'], boards['positional']
    assert intra['swept'][:2] == ['timeframe', 'gates'] and intra['timeframes'] == ['3minute', '15minute']
    assert (intra['positional'], pos['positional']) == (False, True)
    assert {r['timeframe'] for r in intra['results']} == {'3minute', '15minute'}      # every frame gets its floor
    assert {r['timeframe'] for r in pos['results']} <= {'60minute', 'day'}
    assert intra['current']['timeframe'] == '15minute' and pos['current'] is None     # the page's own frame only
    assert {kw['board'] for kw in calls} == {'intraday', 'positional'}
    assert {kw.get('timeframe') for kw in calls} >= {'3minute', '15minute', '60minute'}


def test_a_board_row_puts_its_numbers_but_not_its_timeframe_on_the_engine():
    from trading_app.Backtest import simple_trader_optimise as sto
    e = StAllIndicatorsEngine(_df())
    row = dict(timeframe='5minute', gates='ST+PP', st_period=7, **{'use_' + g: g in ('st', 'pivot') for g in sto._GATES})
    sto._apply_row(e, ['timeframe', 'gates', 'st_period'], row, {})
    assert e.st_period == 7 and e.use_st and e.use_pivot and not e.use_rsi


def _board(mode, rows, current=None):
    return {'results': rows, 'current': current, 'swept': ['timeframe', 'gates'], 'recent_from': '2025-01-01',
            'min_board_trades': 1, 'positional': mode == 'positional'}


def _row(tf, pf, n=0):
    return dict(timeframe=tf, gates=f'ST{n}', profit_factor=pf, max_drawdown=1, total_pnl=pf, win_rate=60,
                recent_pnl=1, total_trades=10)


def test_each_row_is_priced_on_its_own_timeframe(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto
    monkeypatch.setattr(sto, 'GATE_SHORTLIST', 2)
    seen = []

    def fake_verify(strategy, res, df, data, symbol, interval, calendar, on_future, start_date,
                    progress=None, shortlist=None):
        assert all(r['timeframe'] == interval for r in res['results'])
        seen.append((interval, data['positional'], shortlist, res['current'] is not None))
        priced = [dict(r, net_rs=r['profit_factor'] * 100, basis='option') for r in res['results'][:shortlist]]
        cur = dict(res['current'], net_rs=1) if res['current'] else None
        res.update(results=priced, current=cur, verified=len(priced) + (cur is not None))
    monkeypatch.setattr(sto, 'verify_on_option', fake_verify)
    intra = [_row('5minute', 3.0, 1), _row('5minute', 2.9, 2), _row('5minute', 2.8, 3), _row('minute', 1.2, 4)]
    boards = {'intraday': _board('intraday', intra, current=_row('3minute', 1.0, 9)),
              'positional': _board('positional', [_row('day', 2.0, 5), _row('60minute', 1.5, 6)])}
    sto.verify_timeframes('st_all_indicators', boards, lambda tf: None, {}, 'NIFTY', [], '2025-01-01')
    # best two of the board, plus the 1-minute row its floor brings; the ★ settings on their own 3m engine
    assert sorted(seen) == sorted([('5minute', False, 2, False), ('minute', False, 1, False),
                                   ('3minute', False, 0, True), ('day', True, 1, False), ('60minute', True, 1, False)])
    b = boards['intraday']
    assert b['basis'] == 'option' and b['verified'] == 4 and b['current']['net_rs'] == 1
    assert [r['gates'] for r in b['results']] == ['ST1', 'ST2', 'ST4']


def test_a_board_whose_pricing_fails_keeps_its_index_rows(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto

    def boom(*a, **k):
        raise RuntimeError('ICICI not logged in')
    monkeypatch.setattr(sto, 'verify_on_option', boom)
    boards = {'intraday': _board('intraday', [_row('5minute', 2.0)]), 'positional': _board('positional', [])}
    sto.verify_timeframes('st_all_indicators', boards, lambda tf: None, {}, 'NIFTY', [], '2025-01-01')
    assert boards['intraday']['option_error'] == 'ICICI not logged in'
    assert boards['intraday']['results'][0]['profit_factor'] == 2.0 and 'basis' not in boards['positional']


def test_pricing_walks_past_rows_that_trade_alike(monkeypatch):
    from trading_app.Backtest import simple_trader_optimise as sto, st_ema_rsi_cci_engine as sec
    _small_grid(monkeypatch, sto)
    monkeypatch.setattr(sec, 'price_with_reentries', lambda eng, trades, fn: (trades, {'lot': 65}))
    res = sto.optimise('st_all_indicators', _walk_frame(), {}, from_date='2025-01-01')
    first = res['results'][0]
    other = next(r for r in res['results'] if (r['total_trades'], r['total_pnl']) != (first['total_trades'], first['total_pnl']))
    board = dict(res, results=[first, dict(first), other], current=None)
    sto.verify_on_option('st_all_indicators', board, _walk_frame(), {}, 'NIFTY', '3minute', [], False,
                         '2025-01-01', shortlist=2)
    assert board['verified'] == 2 and board['current'] is None and len(board['results']) == 2
    assert {(r['gates'], r['total_trades']) for r in board['results']} == {
        (first['gates'], first['total_trades']), (other['gates'], other['total_trades'])}
