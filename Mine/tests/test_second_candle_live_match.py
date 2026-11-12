"""2nd 30s Candle: the live algo and the Candle Breakout backtest on the same
days, from the real NIFTY 30-second bars of 2026-11-11 / 12 and the Muhurat
session of Sun 2026-11-08.

* 11-12 both booked 25234.45 → 25318.32 TARGET — the engine still does.
* 11-11 the backtest bought the 09:16:00 bar's open (25337.15, above the
  25336.85 range high) and was stopped at 09:16:22. Live missed that window and
  at 09:19:19 bought on a live-spot cross with no bars to check — a trade that
  was already over. Live must now wait for bars that reach back, and then skip.
* 11-08 Muhurat (14:20 open) is not a session live ever trades.

Hermetic: no provider, no app, no create_app().
"""
import pandas as pd

from trading_app.algo.second_candle.second_candle_algo import (
    SecondCandleAlgo, normalise_params,
)
from trading_app.Backtest.second_candle_engine import SecondCandleBacktestEngine

IST = 'Asia/Kolkata'
P = normalise_params({'candle_index': 2, 'rr_ratio': 2.5, 'exit_hour': 15, 'exit_minute': 25,
                      'enable_long': True, 'enable_short': True})

_1111 = [
    (25337.80, 25337.80, 25263.80, 25319.95),   # 09:15:00
    (25317.55, 25336.85, 25315.05, 25336.85),   # 09:15:30  range candle
    (25337.15, 25344.45, 25309.20, 25314.15),   # 09:16:00  opens above the high, hits the low
    (25315.05, 25344.65, 25315.05, 25340.90),   # 09:16:30
    (25341.35, 25347.05, 25325.55, 25328.80),   # 09:17:00
    (25329.55, 25338.05, 25320.90, 25328.80),   # 09:17:30
    (25329.70, 25345.85, 25326.05, 25345.30),   # 09:18:00
    (25345.60, 25352.15, 25341.35, 25350.55),   # 09:18:30
    (25351.75, 25365.50, 25349.95, 25363.40),   # 09:19:00
]

_1112 = [
    (25213.5, 25216.9, 25142.95, 25204.8),      # 09:15:00
    (25203.15, 25234.45, 25200.9, 25229.6),     # 09:15:30  range candle
    (25229.4, 25250.9, 25219.8, 25245.75),      # 09:16:00  breakout
    (25251.2, 25257.05, 25239.55, 25240.7),
    (25245.5, 25266.15, 25236.9, 25261.55),
    (25266.1, 25272.55, 25255.4, 25256.5),
    (25260.8, 25285.6, 25258.75, 25278.55),
    (25281.6, 25295.7, 25277.85, 25294.6),
    (25299.4, 25299.45, 25284.9, 25285.35),
    (25287.1, 25301.55, 25284.55, 25301.15),
    (25303.05, 25312.4, 25295.7, 25295.7),
    (25298.1, 25298.1, 25275.85, 25281.45),
    (25285.75, 25291.45, 25282.35, 25288.05),
    (25289.8, 25292.3, 25282.9, 25287.75),
    (25289.95, 25295.35, 25280.8, 25281.0),
    (25282.3, 25282.3, 25271.4, 25272.85),
    (25275.25, 25299.7, 25266.55, 25297.55),
    (25300.85, 25300.85, 25289.35, 25292.55),
    (25294.5, 25294.5, 25286.1, 25293.6),
    (25295.4, 25300.8, 25291.95, 25298.45),
    (25301.15, 25306.9, 25298.9, 25304.95),
    (25306.7, 25313.75, 25304.75, 25313.1),
    (25316.55, 25326.05, 25313.45, 25322.35),   # 09:26:00  target 25318.32
    (25324.5, 25331.85, 25322.1, 25331.3),
]


def _frames(day, rows, start='09:15'):
    base = pd.Timestamp(f'{day} {start}', tz=IST)
    stamps = [base + pd.Timedelta(seconds=30 * i) for i in range(len(rows))]
    engine_df = pd.DataFrame([{'date': ts.tz_localize(None), 'open': o, 'high': h, 'low': l,
                               'close': c, 'volume': 1} for ts, (o, h, l, c) in zip(stamps, rows)])
    algo_df = pd.DataFrame([{'datetime': ts, 'open': o, 'high': h, 'low': l, 'close': c}
                            for ts, (o, h, l, c) in zip(stamps, rows)])
    return base, engine_df, algo_df


def _backtest(df):
    trades, _ = SecondCandleBacktestEngine(
        df=df, candle_index=P['candle_index'], rr_ratio=P['rr_ratio'],
        exit_hour=P['exit_hour'], exit_minute=P['exit_minute'],
        enable_long=P['enable_long'], enable_short=P['enable_short']).run()
    return trades


def test_2026_11_12_backtest_books_live_levels():
    _, df, _ = _frames('2026-11-12', _1112)
    (t,) = _backtest(df)
    assert (t['entry_price'], round(t['exit_price'], 2), t['exit_reason']) == \
           (25234.45, 25318.32, 'TG Hit')
    assert t['entry_time'].endswith('09:16:00') and t['exit_time'].endswith('09:26:00')


def test_2026_11_11_backtest_is_the_gap_open_stopped_in_its_own_bar():
    _, df, _ = _frames('2026-11-11', _1111)
    (t,) = _backtest(df)
    assert (t['entry_price'], t['exit_price'], t['exit_reason']) == (25337.15, 25315.05, 'SL Hit')


def test_2026_11_11_a_late_live_trigger_without_bars_does_not_enter():
    base, _, algo_df = _frames('2026-11-11', _1111)
    algo = SecondCandleAlgo('test')
    now = base + pd.Timedelta(minutes=4, seconds=19)                 # 09:19:19
    # No bars (or bars that stop short of 09:16:00) cannot vouch for the trigger.
    assert algo._bars_cover(None, P, now) is False
    assert algo._bars_cover(algo_df.iloc[:2], P, now) is False
    gappy = algo_df.drop(index=2)                                     # 09:16:00 missing
    assert algo._bars_cover(gappy, P, now) is False
    # With the bars, the first breakout is the 09:16:00 bar — already stopped.
    assert algo._bars_cover(algo_df, P, now) is True
    sig = algo._check_breakout(algo_df, P, now)
    assert sig['entry_ref'] == 25337.15 and sig['sl_level'] == 25315.05
    assert algo._signal_already_resolved(algo_df, sig, now) == 'SL'


def test_inside_the_first_watch_bar_a_live_trigger_needs_no_bars():
    base, _, _ = _frames('2026-11-11', _1111)
    algo = SecondCandleAlgo('test')
    assert algo._bars_cover(None, P, base + pd.Timedelta(minutes=1, seconds=5)) is True   # 09:16:05
    # 09:16:35: the 09:16:00 bar has only just closed — still allowed missing.
    assert algo._bars_cover(None, P, base + pd.Timedelta(minutes=1, seconds=35)) is True
    # 09:17:05: 09:16:00 must be there now.
    assert algo._bars_cover(None, P, base + pd.Timedelta(minutes=2, seconds=5)) is False


def test_a_live_trigger_in_a_bar_that_opened_past_the_range_enters_at_the_open():
    base, _, algo_df = _frames('2026-11-11', _1111)
    algo = SecondCandleAlgo('test')
    algo._range_high, algo._range_low = 25336.85, 25315.05
    live = algo._eval_live_breakout(25340.0, P)
    assert live['entry_ref'] == 25336.85
    now = base + pd.Timedelta(minutes=1, seconds=3)                   # 09:16:03, bar forming
    forming = algo_df.iloc[:3].copy()
    sig = algo._apply_gap_open(live, forming, P, now)
    assert sig['entry_ref'] == 25337.15                               # the backtest's entry
    assert sig['target_level'] == round(25337.15 + 2.5 * (25337.15 - 25315.05), 2)
    # A bar that opened inside the range keeps the range level.
    base12, _, df12 = _frames('2026-11-12', _1112)
    algo._range_high, algo._range_low = 25234.45, 25200.9
    live = algo._eval_live_breakout(25234.9, P)
    sig = algo._apply_gap_open(live, df12.iloc[:3], P, base12 + pd.Timedelta(minutes=1, seconds=16))
    assert (sig['entry_ref'], sig['target_level']) == (25234.45, 25318.32)


def test_a_muhurat_session_is_not_traded():
    _, muhurat, _ = _frames('2026-11-08', _1112, start='14:20')
    assert _backtest(muhurat) == []
    _, regular, _ = _frames('2026-11-12', _1112)
    assert len(_backtest(pd.concat([muhurat, regular], ignore_index=True))) == 1
