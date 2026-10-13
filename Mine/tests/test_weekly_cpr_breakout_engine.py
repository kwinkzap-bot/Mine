"""Weekly CPR Breakout backtest engine — synthetic bars, no broker.

The previous week (2026-10-05..09) has high 110 / low 90 / close 103:
BC 100, PP 101, TC 102, R3 108.5, S3 97.5 — the same week the scanner
tests use, so the two read one set of numbers. The session traded is
Wednesday 2026-10-14.
"""
from datetime import datetime

import pandas as pd

from trading_app.Backtest.weekly_cpr_breakout_engine import run_symbol, weekly_level_table

DAY = pd.Timestamp('2026-10-14')
AFTER = datetime(2026, 10, 30, 18, 0)


def _daily():
    idx = pd.bdate_range('2026-09-28', '2026-10-13')
    rows = [{'open': 100.0, 'high': 110.0, 'low': 90.0,
             'close': 103.0 if d == pd.Timestamp('2026-10-09') else 100.0} for d in idx]
    return pd.DataFrame(rows, index=idx)


def _minutes(day_open=101.0, hour_close=104.0, hour_high=105.0, hour_low=100.5,
             after=None, day=DAY):
    """One session of 1-minute bars. The first hour runs day_open -> hour_close
    with the given extremes (default H 105 / L 100.5); everything after 10:15
    sits flat at hour_close unless `after` overrides it with
    (HH:MM, high, low, close) or (HH:MM, open, high, low, close)."""
    stamps = pd.date_range(day + pd.Timedelta(hours=9, minutes=15),
                           day + pd.Timedelta(hours=15, minutes=29), freq='1min')
    df = pd.DataFrame({'open': hour_close, 'high': hour_close, 'low': hour_close,
                       'close': hour_close}, index=stamps)
    df.iloc[0] = {'open': day_open, 'high': hour_high, 'low': hour_low, 'close': hour_close}
    for bar in (after or []):
        hm, rest = bar[0], bar[1:]
        o, h, l, c = rest if len(rest) == 4 else (hour_close, *rest)
        ts = day + pd.Timedelta(hours=int(hm[:2]), minutes=int(hm[3:]))
        df.loc[ts] = {'open': o, 'high': h, 'low': l, 'close': c}
    return df


def _run(**kw):
    """The trade mechanics, with the quality filters OFF (this two-week daily
    fixture has no 10-week context for a narrowness ratio) — the filters have
    their own tests at the bottom."""
    rr = kw.pop('rr', 2.0)
    direction = kw.pop('direction', 'both')
    opts = dict(max_ratio=kw.pop('max_ratio', None), min_range_pct=kw.pop('min_range_pct', 0),
                signal=kw.pop('signal', 'close'), candle_type=kw.pop('candle_type', 'all'))
    return run_symbol(_daily(), _minutes(**kw), '2026-10-12', '2026-10-16',
                      rr=rr, direction=direction, now=AFTER, **opts)


# BUY setup used below: hour H 105 / L 100.5 / C 104 -> trigger 105, SL 100.5,
# risk 4.5, target 105 + 2 x 4.5 = 114.
BREAK_UP = ('11:00', 105.2, 104.0, 105.1)


def test_levels_are_the_previous_weeks():
    lv = weekly_level_table(_daily())[pd.Timestamp('2026-10-12')]
    assert (round(lv['bc'], 2), round(lv['pp'], 2), round(lv['tc'], 2)) == (100.0, 101.0, 102.0)
    assert (round(lv['r3'], 2), round(lv['s3'], 2)) == (108.5, 97.5)


def test_buy_enters_on_break_of_hour_high_stop_hour_low_target_two_r():
    (t,) = _run(after=[BREAK_UP, ('12:00', 114.5, 105.0, 114.2)])
    assert t['direction'] == 'BUY' and t['entry_time'] == '2026-10-14T11:00:00'
    assert (t['entry_price'], t['sl_price'], t['trigger']) == (105.0, 100.5, 105.0)
    assert t['target_price'] == 114.0
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('Target hit', 114.0, 9.0)


def test_no_break_no_trade():
    """The hour closed above TC, but price never cleared its high again."""
    assert _run() == []
    assert _run(after=[('11:00', 104.9, 103.0, 104.0)]) == []


def test_break_after_cutoff_is_not_taken():
    assert _run(after=[('15:20', 106.0, 104.0, 105.5)]) == []


def test_sell_enters_on_break_of_hour_low_stop_hour_high():
    # Hour H 101.5 / L 98.5 / C 99 -> trigger 98.5, SL 101.5, risk 3, target 92.5.
    (t,) = _run(day_open=101.0, hour_close=99.0, hour_high=101.5, hour_low=98.5,
                after=[('11:00', 99.0, 98.4, 98.6), ('12:00', 101.6, 98.0, 101.0)])
    assert (t['direction'], t['entry_price'], t['sl_price'], t['target_price']) == \
        ('SELL', 98.5, 101.5, 92.5)
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('SL hit', 101.5, -3.0)


def test_gap_through_the_trigger_fills_at_the_open():
    (t,) = _run(after=[('11:00', 106.0, 106.5, 105.8, 106.2)])
    assert t['entry_price'] == 106.0
    assert t['target_price'] == 114.0                  # planned off the trigger


def test_bar_touching_both_is_booked_as_the_stop():
    (t,) = _run(after=[BREAK_UP, ('11:30', 114.5, 100.0, 105.0)])
    assert t['exit_reason'] == 'SL hit' and t['pnl'] == -4.5


def test_neither_hit_exits_at_cutoff():
    (t,) = _run(after=[BREAK_UP])
    assert t['exit_reason'] == 'Time exit' and t['exit_time'].endswith('15:20:00')
    assert t['exit_price'] == 104.0 and t['pnl'] == -1.0


def test_rr_moves_the_target():
    (t,) = _run(after=[BREAK_UP], rr=1.0)
    assert t['target_price'] == 109.5


def test_scanner_conditions_gate_the_entry():
    # Each case breaks out, so it is the scanner condition that rejects it.
    assert _run(after=[BREAK_UP])                        # control
    assert _run(day_open=102.5, after=[BREAK_UP]) == []  # opened above TC
    assert _run(hour_close=101.0, after=[BREAK_UP]) == []  # hour closed inside
    assert _run(hour_close=108.2, hour_high=108.3,       # within 0.5 % of R3 108.5
                after=[('11:00', 108.4, 108.0, 108.3)]) == []


def test_direction_candle_follows_from_open_inside():
    """The hour opens at the day's open. Inside the CPR (<= TC) and closing
    above TC, it is green by construction — so the direction condition never
    rejects a session the open-inside condition let through; it is kept for
    the scanner's sake, where it is the same shared verdict."""
    (t,) = _run(day_open=102.0, hour_close=102.5, hour_high=102.6, hour_low=101.9,
                after=[('11:00', 102.7, 102.4, 102.6)])
    assert t['direction'] == 'BUY'


def test_direction_filter():
    assert _run(after=[BREAK_UP], direction='sell') == []
    assert len(_run(after=[BREAK_UP], direction='buy')) == 1


def test_session_still_trading_is_skipped():
    mid = datetime(2026, 10, 14, 11, 0)
    assert run_symbol(_daily(), _minutes(after=[BREAK_UP]), '2026-10-12', '2026-10-16',
                      now=mid, max_ratio=None, min_range_pct=0, signal='close') == []


# ── quality filters (narrow weekly CPR, first-hour range) ─────────────────

def _long_daily(in_force_close=103.0):
    """Ten weeks of wide context (weeks close at 108: width 4.94 %) before the
    in-force week (close 103: width 1.94 %, 0.39× normal) — the scanner
    tests' numbers."""
    idx = pd.bdate_range('2026-07-06', '2026-10-13')
    rows = []
    for d in idx:
        fri = d.weekday() == 4
        in_force = pd.Timestamp('2026-10-05') <= d <= pd.Timestamp('2026-10-09')
        close = (in_force_close if in_force else 108.0) if fri else 100.0
        rows.append({'open': 100.0, 'high': 110.0, 'low': 90.0, 'close': close})
    return pd.DataFrame(rows, index=idx)


def _filtered(daily, **kw):
    return run_symbol(daily, _minutes(**kw), '2026-10-12', '2026-10-16', rr=2.0, now=AFTER,
                      signal='close')


def test_narrow_week_with_a_wide_first_hour_trades_by_default():
    # Hour H 105 / L 100.5 / C 104: range 4.33 % of the close.
    (t,) = _filtered(_long_daily(), after=[BREAK_UP])
    assert t['cpr_ratio'] == 0.39 and t['fh_range_pct'] == 4.33


def test_a_normal_width_week_is_skipped():
    # The in-force week closes at 108 like its context: 1.0× normal.
    assert _filtered(_long_daily(in_force_close=108.0),
                     day_open=103.0, hour_close=106.0, hour_high=106.5, hour_low=102.8,
                     after=[('11:00', 106.6, 106.0, 106.5)]) == []


def test_a_quiet_first_hour_is_skipped():
    # Hour H 102.9 / L 101.6 / C 102.8: range 1.26 % < 1.6 %.
    kw = dict(day_open=101.8, hour_close=102.8, hour_high=102.9, hour_low=101.6,
              after=[('11:00', 103.0, 102.7, 102.95)])
    assert _filtered(_long_daily(), **kw) == []
    assert len(run_symbol(_long_daily(), _minutes(**kw), '2026-10-12', '2026-10-16',
                          now=AFTER, min_range_pct=1.0, signal='close')) == 1


def test_too_little_history_for_a_ratio_is_skipped_while_the_filter_is_on():
    assert _filtered(_daily(), after=[BREAK_UP]) == []


# ── Find Best Params scoring ─────────────────────────────────────────────

def test_score_combos_applies_filters_to_the_unfiltered_replay():
    from trading_app.Backtest.weekly_cpr_breakout_engine import OPT_COMBOS, score_combos
    # 40 narrow wide-range SELL winners, 40 wide-CPR BUY losers, 5 with no ratio.
    rows = ([(f'2025-01-{1 + i % 28:02d}T11:00', 'SELL', 0.4, 2.5, 1000.0, 1300.0) for i in range(40)]
            + [(f'2025-02-{1 + i % 28:02d}T11:00', 'BUY', 1.5, 2.5, -800.0, -500.0) for i in range(40)]
            + [('2025-03-03T11:00', 'SELL', None, 2.5, 500.0, 800.0)] * 5)
    res = score_combos({rr: rows for rr in (1.0, 1.5, 2.0, 3.0)}, recent_from='2025-02-01')
    pick = lambda **kw: next(r for r in res if all(r[k] == v for k, v in kw.items()))
    best = res[0]
    # SELL with the width filter off takes the 40 narrow winners AND the 5
    # ratio-less ones; with the filter on, only the 40.
    assert (best['direction'], best['max_cpr_ratio']) == ('sell', 'any')
    assert (best['total_trades'], best['net_pnl_rupees'], best['win_rate']) == (45, 42500.0, 100.0)
    assert pick(direction='sell', max_cpr_ratio=0.5, min_range_pct=0.0, rr_ratio=1.0)['total_trades'] == 40
    both = pick(direction='both', max_cpr_ratio='any', min_range_pct=0.0, rr_ratio=1.0)
    assert (both['total_trades'], both['recent_trades']) == (85, 45)     # Feb + Mar are recent
    assert not any(r['max_cpr_ratio'] == 0.3 for r in res)               # nothing that narrow
    assert all(r['total_trades'] >= 30 for r in res) and len(res) <= OPT_COMBOS


def test_symbol_box_accepts_indices_and_stocks():
    from trading_app.app.routes.api import _wcb_token, _wcb_universe
    lots = {'NIFTY': 65, 'SENSEX': 20, 'TITAN': 175, 'SBIN': 750}
    assert _wcb_universe('', lots) == (['SBIN', 'TITAN'], None)      # ALL = stocks only
    assert _wcb_universe('NIFTY', lots) == (['NIFTY'], None)
    assert _wcb_universe('BANKNIFTY', lots) == (['BANKNIFTY'], None)  # even without a lot row
    assert _wcb_universe('TITAN', lots) == (['TITAN'], None)
    assert _wcb_universe('XYZ', lots)[0] is None
    assert _wcb_token('NIFTY') == 'NSE:NIFTY50-INDEX' and _wcb_token('TITAN') == 'NSE:TITAN-EQ'


def test_score_combos_filters_on_exact_values_and_counts_gross_wins():
    """Regression: the sweep filtered on ROUNDED ratio / range while the
    backtest uses exact ones, so a 0.504x week sat in a <= 0.5x row the
    backtest then refused. And a win is gross > 0, as on the backtest cards."""
    from trading_app.Backtest.weekly_cpr_breakout_engine import passes_quality, score_combos
    base = [(f'2025-01-{1 + i % 28:02d}T11:00', 'BUY', 0.45, 2.0, 500.0, 800.0) for i in range(30)]
    edge = [('2025-02-03T11:00', 'BUY', 0.504, 1.5996, 200.0, 500.0),
            ('2025-02-04T11:00', 'BUY', 0.45, 2.0, -100.0, 200.0)]   # gross win, net loss
    res = score_combos({rr: base + edge for rr in (1.0, 1.5, 2.0, 3.0)}, '2025-01-01')
    row = next(r for r in res if r['max_cpr_ratio'] == 0.5 and r['min_range_pct'] == 1.6
               and r['direction'] == 'both' and r['rr_ratio'] == 1.0)
    assert row['total_trades'] == 31                       # the 0.504x / 1.5996 % trade is out
    assert not passes_quality(0.504, {'high': 101.5996, 'low': 100.0, 'close': 100.0}, 0.5, 1.6)
    assert row['wins'] == 31                               # gross > 0 on every one
    assert row['net_pnl_rupees'] == 30 * 500.0 - 100.0



# ── any bearish / bullish candle signal (the default) ─────────────────────

def test_red_shooting_star_sells_on_a_break_of_its_low_without_closing_below_bc():
    # Opens 101 (inside 100..102), runs to 104.5, closes 100.6 inside the CPR —
    # red, long upper wick: a shooting star. SELL below 100.5; SL 104.5; risk 4.
    kw = dict(day_open=101.0, hour_close=100.6, hour_high=104.5, hour_low=100.5,
              after=[('11:00', 100.6, 100.4, 100.45), ('12:00', 100.5, 92.0, 93.0)])
    (t,) = _run(signal='candle', **kw)
    assert (t['direction'], t['fh_signal'], t['fh_type']) == ('SELL', 'Shooting star', 'rejection')
    assert (t['entry_price'], t['sl_price'], t['target_price']) == (100.5, 104.5, 92.5)
    assert t['exit_reason'] == 'Target hit'
    assert _run(signal='close', **kw) == []              # closed inside: no 'close' signal


def test_any_red_candle_is_a_sell_and_any_green_a_buy():
    # A plain red bar with no notable wicks — no longer needs to be a hammer.
    (t,) = _run(signal='candle', day_open=102.0, hour_close=100.3, hour_high=102.1,
                hour_low=100.2, after=[('11:00', 100.3, 100.1, 100.15)])
    assert (t['direction'], t['fh_signal']) == ('SELL', 'Bearish marubozu')
    (t,) = _run(signal='candle', day_open=101.0, hour_close=101.4, hour_high=101.5,
                hour_low=97.6, after=[('11:00', 101.6, 101.3, 101.55)])
    assert (t['direction'], t['fh_signal'], t['entry_price'], t['sl_price']) == \
        ('BUY', 'Hammer', 101.5, 97.6)


def test_candle_type_narrows_the_signal():
    kw = dict(day_open=102.0, hour_close=100.3, hour_high=102.1, hour_low=100.2,
              after=[('11:00', 100.3, 100.1, 100.15)])          # a bearish marubozu
    assert len(_run(signal='candle', candle_type='marubozu', **kw)) == 1
    assert _run(signal='candle', candle_type='rejection', **kw) == []


def test_a_doji_has_no_side():
    assert _run(signal='candle', day_open=101.0, hour_close=101.05, hour_high=103.0,
                hour_low=99.0, after=[('11:00', 103.1, 98.9, 101.0)]) == []


def test_entry_level_too_near_s3_is_skipped():
    # S3 97.5; low 97.9 is within 0.5 % of it.
    assert _run(signal='candle', day_open=100.2, hour_close=98.0, hour_high=104.0,
                hour_low=97.9, after=[('11:00', 98.0, 97.8, 97.85)]) == []
