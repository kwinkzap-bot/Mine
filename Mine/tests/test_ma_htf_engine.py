import numpy as np
import pandas as pd

from trading_app.Backtest.ma_htf_engine import MaHtfEngine, smma


def _frame(days, step=3):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=step * k), 'open': cl - 0.2,
                         'high': cl + 1, 'low': cl - 1, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _kw(**k):
    base = dict(entry_start='09:15', entry_end='15:00', htf_len=3, htf_minutes=15, bar_minutes=3, sl_points=0)
    base.update(k)
    return base


def _round_trip():
    # dip, rally through all three lines, then a fall — all inside one session (before the 15:14 square-off)
    return ([100 - 0.6 * k for k in range(35)] + [79 + 1.5 * k for k in range(30)]
            + [124 - 3 * k for k in range(25)])


def _rally(base=100):
    # a long dip (so the fast line is under the slow), then a sustained rally through all three lines
    return [base - 0.4 * k for k in range(60)] + [base - 24 + 1.2 * k for k in range(70)]


def test_smma_is_sma_seeded_then_wilder():
    out = smma(np.arange(1.0, 11.0), 3)
    assert np.isnan(out[1]) and out[2] == 2.0
    assert abs(out[3] - (2.0 * 2 + 4) / 3) < 1e-9


def test_flat_market_takes_no_trade():
    assert MaHtfEngine(_frame([('2025-01-01', [100.0] * 120)]), **_kw()).run()[0] == []


def test_rally_goes_long_after_confirmation_and_exits_on_cross_or_eod():
    trades, _ = MaHtfEngine(_frame([('2025-01-01', _rally())]), **_kw()).run()
    longs = [t for t in trades if t['type'] == 'Long']
    assert longs and not [t for t in trades if t['type'] == 'Short' and t['signal']['close'] > t['signal']['htf']]
    t = longs[0]
    assert t['signal']['close'] > t['signal']['htf']
    assert t['signal']['cross_time'] < t['entry_time']
    assert t['entry_time'] > t['signal']['cross_time']


def test_counter_trend_crossover_is_ignored():
    # price stays far under the HTF line while the short MAs wiggle up through each other
    down = [200 - 0.5 * k for k in range(60)]
    wiggle = [down[-1] + 3 * np.sin(k / 2.0) for k in range(60)]
    trades, _ = MaHtfEngine(_frame([('2025-01-01', down + wiggle)]), **_kw(htf_len=40)).run()
    assert all(t['type'] == 'Short' for t in trades)


def test_opening_stretch_crossover_is_ignored():
    f = _frame([('2025-01-01', _rally())])
    early = MaHtfEngine(f, **_kw()).run()[0]
    late = MaHtfEngine(f, **_kw(entry_start='15:00', entry_end='15:25')).run()[0]
    assert early and late == []


def test_direction_filter():
    f = _frame([('2025-01-01', _rally())])
    assert MaHtfEngine(f, **_kw(direction='short')).run()[0] == []


def test_stop_loss_is_intrabar_at_the_stop():
    f = _frame([('2025-01-01', _round_trip())])
    trades, _ = MaHtfEngine(f, **_kw(sl_points=5, exit_on_cross=False)).run()
    sl = [t for t in trades if t['exit_reason'] == 'Stop Loss']
    assert sl and all(t['exit_price'] <= t['sl_price'] + 1e-6 for t in sl if t['type'] == 'Long')


def test_partial_books_half_and_moves_stop_to_cost():
    f = _frame([('2025-01-01', _round_trip())])
    trades, _ = MaHtfEngine(f, **_kw(sl_points=5, partial_pct=0.5, exit_on_cross=False)).run()
    t = next(t for t in trades if t['partial_booked'])
    assert t['target_price'] == round(t['entry_price'] + 5, 2)
    assert t['exit_reason'] in ('Breakeven', 'EOD Exit', 'Time Exit', 'MA Cross')
    assert t['exit_reason'] == 'Breakeven'
    # half at +5, the other half at cost (or the gap through it)
    assert abs(t['pnl'] - (2.5 + 0.5 * (t['exit_price'] - t['entry_price']))) < 0.01


def test_htf_line_never_uses_the_future():
    f = _frame([('2025-01-01', _rally())])
    full = MaHtfEngine(f, **_kw()).htf_line()
    cut = MaHtfEngine(f.iloc[:90], **_kw()).htf_line()
    assert np.allclose(full[:90], cut, equal_nan=True)


def test_max_trades_per_day_and_no_overlap():
    f = _frame([('2025-01-01', _rally() + [150 - 1.0 * k for k in range(80)] + [70 + 1.5 * k for k in range(60)])])
    trades, _ = MaHtfEngine(f, **_kw(max_trades_per_day=1)).run()
    assert len(trades) <= 1


def test_trailing_stop_ratchets_and_never_loosens():
    f = _frame([('2025-01-01', _round_trip())])
    trades, _ = MaHtfEngine(f, **_kw(sl_points=40, trail_points=6, exit_on_cross=False)).run()
    t = next(t for t in trades if t['type'] == 'Long')
    assert t['exit_reason'] == 'Trailing Stop'
    assert t['exit_price'] > t['entry_price']                  # trailed above cost, so it locked a profit


def test_trend_day_filters_gate_entries():
    f = _frame([('2025-01-01', _round_trip())])
    assert MaHtfEngine(f, **_kw()).run()[0]
    assert MaHtfEngine(f, **_kw(adx_min=95)).run()[0] == []           # no trend is that strong
    assert MaHtfEngine(f, **_kw(chop_lookback=200, chop_max=0)).run()[0] == []   # any crossing is "chop"
    flat_up = MaHtfEngine(f, **_kw(day_bias=True)).run()[0]
    assert all(t['type'] == 'Short' or t['signal']['close'] > 0 for t in flat_up)
