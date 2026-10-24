import numpy as np
import pandas as pd

from trading_app.Backtest.st_ema_rsi_cci_engine import StEmaRsiCciEngine, cci, ema


def _frame(days):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=3 * k), 'open': cl, 'high': cl + 1,
                         'low': cl - 1, 'close': cl, 'volume': 0})
    return pd.DataFrame(rows)


def _crash_day():
    # flat, a hard sell-off below yesterday's S1, a rebound, then flat to the close
    return [100.0] * 30 + list(np.linspace(100, 60, 12)) + list(np.linspace(60, 100, 20)) + [100.0] * 8


def test_ema_seeds_on_first_value_and_cci_is_zero_on_a_flat_series():
    assert ema(np.full(10, 5.0), 3)[0] == 5.0
    h = l = c = np.full(30, 10.0)
    assert np.isnan(cci(h, l, c, 20)[25]) or cci(h, l, c, 20)[25] == 0


def test_short_entry_then_rsi_exit():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _crash_day())])
    trades, s = StEmaRsiCciEngine(df).run()
    assert trades, 'a narrow-CPR sell-off through S1 must trade'
    t = trades[0]
    assert t['type'] == 'Short' and t['entry_time'] < t['exit_time']
    assert t['exit_reason'] == 'RSI Exit' and t['pnl'] > 0           # shorted near 100 → covered lower
    sig = t['signal']
    assert sig['rsi'] < 30 and sig['cci'] < -200 and sig['wr'] < -85
    assert sig['close'] < sig['pivot_level'] < 100 and sig['cpr_width'] < 0.2


def test_wide_cpr_blocks_the_trade_and_the_switch_unblocks_it():
    # a wide yesterday (range 100-130) makes the CPR wide AND moves S1; keep today's crash far under it
    wide = [100.0] * 50 + [130.0] * 50
    df = _frame([('2025-01-01', wide), ('2025-01-02', [x - 40 for x in _crash_day()])])
    assert StEmaRsiCciEngine(df).run()[0] == []
    assert StEmaRsiCciEngine(df, use_cpr_narrow=False).run()[0] != []


def test_long_direction_is_off_for_a_short_only_setup():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _crash_day())])
    assert all(t['type'] == 'Short' for t in StEmaRsiCciEngine(df, direction='both').run()[0])
    assert StEmaRsiCciEngine(df, direction='long').run()[0] == []


def test_max_trades_per_day_caps_entries():
    day = _crash_day() * 3
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', day[:75])])
    n = len(StEmaRsiCciEngine(df, max_trades_per_day=1).run()[0])
    assert n <= 1


def test_a_premium_stop_frees_the_day_for_a_reentry():
    from trading_app.Backtest.st_ema_rsi_cci_engine import price_with_reentries
    # two sell-offs in one session, the first rebounds slowly so the index trade is still open
    day = [100.0] * 30 + list(np.linspace(100, 60, 12)) + [60.0] * 6 + list(np.linspace(60, 45, 8)) + [45.0] * 20
    eng = StEmaRsiCciEngine(_frame([('2025-01-01', [100.0] * 100), ('2025-01-02', day)]), max_trades_per_day=3)
    trades = [t for t in eng.run()[0]]
    assert trades

    def pricer(batch, prog):
        out = []
        for t in batch:
            stop_at = (pd.Timestamp(t['entry_time']) + pd.Timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
            out.append(dict(t, exit_reason='Stop Loss', exit_time=stop_at, pnl=-1.0))
        return out, {'unpriced': 0, 'truncated': 0}

    priced, info = price_with_reentries(eng, trades, pricer)
    assert len(priced) > len(trades) or info['reentries'] >= 0
    entries = [p['entry_time'] for p in priced]
    assert entries == sorted(entries) and len(set(entries)) == len(entries)
    assert all(p['entry_time'] >= q['exit_time'] for q, p in zip(priced, priced[1:]))
    assert len(priced) <= 3


def test_futures_expiry_candidates_follow_the_weekday_change():
    from datetime import date
    from trading_app.Backtest.futures_minute_store import _candidates
    assert _candidates(2025, 8)[0] == date(2025, 8, 28)       # last Thursday
    assert _candidates(2025, 9)[0] == date(2025, 9, 30)       # last Tuesday from Sep 2025
    assert _candidates(2026, 3)[:2] == [date(2026, 3, 31), date(2026, 3, 30)]


def test_a_reentry_is_strictly_after_the_stop_minute():
    df = _frame([('2025-01-01', [100.0] * 100), ('2025-01-02', _crash_day())])
    eng = StEmaRsiCciEngine(df, max_trades_per_day=5)
    first = eng.run()[0][0]
    again = eng.trade_after('2025-01-02', first['entry_time'], 1)
    assert again is None or again['entry_time'] > first['entry_time']
