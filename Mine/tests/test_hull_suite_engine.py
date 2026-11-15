"""Hull Suite System backtest (Backtest/hull_suite_engine.py): the Hull average, the signal
candle, and the break / stop / target walk on chart and 1-minute bars."""
import math

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest.hull_suite_engine import HullSuiteEngine, hma, wma


def _ref_wma(x, n):
    out = [math.nan] * len(x)
    for i in range(n - 1, len(x)):
        win = x[i - n + 1:i + 1]
        if any(math.isnan(v) for v in win):
            continue
        out[i] = sum(v * (j + 1) for j, v in enumerate(win)) / (n * (n + 1) / 2)
    return out


def test_wma_and_hma_match_the_pine_formula():
    rng = np.random.default_rng(3)
    x = list(np.cumsum(rng.normal(0, 1, 300)) + 100)
    assert np.allclose(wma(np.array(x), 9), _ref_wma(x, 9), equal_nan=True)
    n = 50
    raw = [2 * a - b for a, b in zip(_ref_wma(x, n // 2), _ref_wma(x, n))]
    ref = _ref_wma(raw, int(math.floor(math.sqrt(n))))
    assert np.allclose(hma(np.array(x), n), ref, equal_nan=True)


def _bars(closes, start='2026-07-08 09:15', minutes=5, spread=2.0):
    idx = pd.date_range(start, periods=len(closes), freq=f'{minutes}min')
    c = np.asarray(closes, dtype=float)
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({'open': o, 'high': np.maximum(o, c) + spread,
                         'low': np.minimum(o, c) - spread, 'close': c}, index=idx)


def test_every_signal_closes_beyond_all_three_lines_with_them_agreeing():
    rng = np.random.default_rng(7)
    # several days of waves, so both sides get signals
    t = np.arange(75 * 6)
    closes = 24000 + 150 * np.sin(t / 40) + np.cumsum(rng.normal(0, 3, len(t)))
    frames = []
    for d in range(6):
        day = pd.Timestamp('2026-07-06') + pd.Timedelta(days=d)
        if day.weekday() >= 5:
            continue
        frames.append(_bars(closes[d * 75:(d + 1) * 75], start=f'{day.date()} 09:15'))
    df = pd.concat(frames)
    eng = HullSuiteEngine(df, slow_len=40, medium_len=20, fast_len=10, pullback_bars=0)
    sg = eng.signals()
    c = df['close'].to_numpy()
    s, m, f = sg['slow'], sg['medium'], sg['fast']
    longs, shorts = np.flatnonzero(sg['signal'] == 1), np.flatnonzero(sg['signal'] == -1)
    assert len(longs) and len(shorts)
    for i in longs:
        assert c[i] > max(s[i], m[i], f[i])
        assert not c[i - 1] > max(s[i - 1], m[i - 1], f[i - 1])
        assert m[i] > m[i - 2] and f[i] > f[i - 2] and s[i] > s[i - 2]
    for i in shorts:
        assert c[i] < min(s[i], m[i], f[i])
        assert m[i] < m[i - 2] and f[i] < f[i - 2] and s[i] < s[i - 2]
    # the pullback rule only ever removes signals
    eng2 = HullSuiteEngine(df, slow_len=40, medium_len=20, fast_len=10, pullback_bars=6, pullback_pct=0.05)
    kept = np.flatnonzero(eng2.signals()['signal'])
    assert set(kept) <= set(np.flatnonzero(sg['signal']))


def _pinned(df, at, side=1, minutes=None, **kw):
    eng = HullSuiteEngine(df, minutes=minutes, slow_len=3, medium_len=3, fast_len=3, **kw)
    sig = np.zeros(len(eng.df), dtype=np.int64)
    sig[at] = side
    nan = np.full(len(eng.df), np.nan)
    eng.signals = lambda: {'signal': sig, 'slow': nan, 'medium': nan, 'fast': nan}
    return eng


def _ohlc(rows, start='2026-07-08 09:15', minutes=5):
    idx = pd.date_range(start, periods=len(rows), freq=f'{minutes}min')
    return pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=idx)


def test_long_fills_at_the_signal_high_and_books_the_1_5R_target():
    df = _ohlc([(100, 101, 99, 100)] * 4 + [
        (100, 110, 100, 108),      # 4: signal candle, high 110, low 100 → risk 10
        (108, 112, 107, 111),      # 5: breaks 110 → long at 110
        (111, 124, 110, 123),      # 6: reaches 125? no — 124
        (123, 126, 122, 125),      # 7: target 125 hit
    ] + [(125, 126, 124, 125)] * 3)
    tr = _pinned(df, 4).simulate()
    assert len(tr) == 1
    t = tr[0]
    assert (t['type'], t['entry_price'], t['sl_price'], t['target_price']) == ('Long', 110, 100, 125)
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('Target', 125, 15)
    assert t['entry_time'] == '2026-07-08 09:40:00' and t['exit_time'] == '2026-07-08 09:50:00'


def test_short_stop_loss_and_gap_fill():
    df = _ohlc([(100, 101, 99, 100)] * 4 + [
        (100, 101, 90, 92),        # 4: short signal: low 90, high 101 → risk 11
        (88, 89, 85, 86),          # 5: opens 88, below 90 → filled at the open 88
        (86, 103, 85, 102),        # 6: stop 101 hit
    ] + [(100, 101, 99, 100)] * 3)
    t = _pinned(df, 4, side=-1).simulate()[0]
    assert (t['type'], t['entry_price'], t['exit_reason'], t['exit_price']) == ('Short', 88, 'Stop Loss', 101)
    assert t['pnl'] == -13


def test_stop_first_is_invalidated_and_a_quiet_window_expires():
    df = _ohlc([(100, 101, 99, 100)] * 4 + [
        (100, 110, 100, 108),
        (108, 109, 99, 100),       # trades under the stop before breaking 110
        (100, 115, 99, 114),
    ] + [(100, 101, 99, 100)] * 3)
    eng = _pinned(df, 4)
    assert eng.simulate() == [] and eng.setups['invalidated'] == 1
    df2 = _ohlc([(100, 101, 99, 100)] * 4 + [(100, 110, 100, 108)] + [(105, 108, 103, 105)] * 5)
    eng2 = _pinned(df2, 4, max_wait=3)
    assert eng2.simulate() == [] and eng2.setups['expired'] == 1


def test_minutes_decide_which_came_first_inside_a_chart_bar():
    df = _ohlc([(100, 101, 99, 100)] * 4 + [
        (100, 110, 100, 108),
        (108, 126, 99, 120),       # this 5-minute bar holds the entry, the stop AND the target
    ] + [(120, 121, 119, 120)] * 3)
    # on chart bars alone it is booked the worse way: entry then the stop
    t = _pinned(df, 4).simulate()[0]
    assert t['exit_reason'] == 'Stop Loss' and not t['intrabar_entry']
    # 1-minute bars inside 09:40: up through 110 and on to 125 before any dip
    mins = _ohlc([(100, 101, 99, 100)] * 25 + [
        (108, 111, 107, 111), (111, 118, 110, 118), (118, 126, 117, 125),
        (125, 125, 99, 100), (100, 120, 99, 120)] + [(120, 121, 119, 120)] * 15, minutes=1)
    t = _pinned(df, 4, minutes=mins).simulate()[0]
    assert (t['exit_reason'], t['entry_price'], t['exit_price']) == ('Target', 110, 125)
    assert t['entry_time'] == '2026-07-08 09:40:00' and t['exit_time'] == '2026-07-08 09:42:00'
    assert t['intrabar_entry'] and t['intrabar_exit']


def test_breakeven_then_square_off():
    rows = [(100, 101, 99, 100)] * 4 + [(100, 110, 100, 108), (108, 112, 107, 111),
                                         (111, 121, 110, 118), (118, 119, 109, 112)]
    df = _ohlc(rows)
    t = _pinned(df, 4, rr=0, breakeven_rr=1).simulate()[0]
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('Breakeven', 110, 0)
    # no target, no breakeven: rides to the 15:15 square-off at that bar's open
    day = [(100, 101, 99, 100)] * 4 + [(100, 110, 100, 108)] + [(111, 115, 109, 112)] * 70
    t = _pinned(_ohlc(day), 4, rr=0).simulate()[0]
    assert t['exit_reason'] == 'Time Exit' and t['exit_time'] == '2026-07-08 15:15:00'
    assert t['exit_price'] == 111


def test_max_risk_skips_a_wide_candle_and_two_bar_stop_uses_the_lower_low():
    df = _ohlc([(100, 101, 99, 100)] * 3 + [(100, 101, 95, 100), (100, 110, 100, 108),
                                             (108, 112, 107, 111)] + [(111, 112, 110, 111)] * 3)
    assert _pinned(df, 4, max_risk=8).simulate() == []
    t = _pinned(df, 4, stop_mode='two_bar', rr=0).simulate()[0]
    assert t['sl_price'] == 95 and t['risk_points'] == 15


def _wave_days(n_days=8, seed=11):
    rng = np.random.default_rng(seed)
    frames = []
    day = pd.Timestamp('2026-07-06')
    t0 = 0
    while len(frames) < n_days:
        if day.weekday() < 5:
            t = np.arange(t0, t0 + 375)
            closes = 24000 + 120 * np.sin(t / 60) + np.cumsum(rng.normal(0, 1.5, 375))
            frames.append(_bars(closes, start=f'{day.date()} 09:15', minutes=1, spread=1.0))
            t0 += 375
        day += pd.Timedelta(days=1)
    return pd.concat(frames)


def _five(mins):
    a = pd.Timedelta(hours=9, minutes=15)
    k = (mins.index - a).floor('5min') + a
    return mins.groupby(k).agg(open=('open', 'first'), high=('high', 'max'),
                               low=('low', 'min'), close=('close', 'last'))


def test_sweep_rows_equal_a_direct_run_of_their_own_settings(monkeypatch):
    from trading_app.Backtest import hull_suite_optimise as hso
    from trading_app.Backtest.st_bb_rsi_engine import summarise
    monkeypatch.setattr(hso, 'GRID', [('slow_len', (40, 60)), ('medium_len', (20,)), ('fast_len', (10,)),
                                      ('pullback_bars', (0, 6)), ('max_wait', (3,)), ('rr', (1.5, 0.0))])
    monkeypatch.setattr(hso, 'NAMES', [n for n, _ in hso.GRID])
    monkeypatch.setattr(hso, 'WALK', [('slope_bars', (1, 2)), ('direction', ('both', 'long'))])
    monkeypatch.setattr(hso, 'MIN_TRADES', 1)
    mins = _wave_days()
    df = _five(mins)
    page = {'slow_len': 40, 'medium_len': 20, 'fast_len': 10, 'pullback_bars': 0}
    res = hso.optimise(df, page, 5, from_date='', minutes=mins)
    rows = res['results'] + [res['current']]
    assert res['strategy'] == 'hull_suite' and res['total_combos_tested'] >= 8 and rows
    assert sum(r['total_trades'] for r in rows) >= 10
    for r in rows:
        eng = HullSuiteEngine(df, minutes=mins, bar_minutes=5,
                              **{k: r[k] for k in res['swept']})
        sm = summarise(eng.simulate())
        assert (sm['total_trades'], sm['total_pnl'], sm['profit_factor']) == \
               (r['total_trades'], r['total_pnl'], r['profit_factor'])
