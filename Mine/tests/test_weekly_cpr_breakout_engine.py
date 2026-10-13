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


def _minutes(day_open=101.0, hour_close=104.0, hour_high=None, hour_low=None,
             after=None, day=DAY):
    """One session of 1-minute bars. The first hour runs day_open -> hour_close
    with the given extremes; `after` is a list of (HH:MM, high, low, close)
    overriding bars from 10:15 on — everything else after 10:15 sits flat at
    hour_close."""
    stamps = pd.date_range(day + pd.Timedelta(hours=9, minutes=15),
                           day + pd.Timedelta(hours=15, minutes=29), freq='1min')
    hi = hour_high if hour_high is not None else max(day_open, hour_close) + 0.2
    lo = hour_low if hour_low is not None else min(day_open, hour_close) - 0.2
    rows = []
    for ts in stamps:
        if ts.time() < pd.Timestamp('10:15').time():
            rows.append({'open': hour_close, 'high': hour_close, 'low': hour_close,
                         'close': hour_close})
        else:
            rows.append({'open': hour_close, 'high': hour_close, 'low': hour_close,
                         'close': hour_close})
    df = pd.DataFrame(rows, index=stamps)
    df.iloc[0] = {'open': day_open, 'high': hi, 'low': lo, 'close': hour_close}
    for hm, h, l, c in (after or []):
        ts = day + pd.Timedelta(hours=int(hm[:2]), minutes=int(hm[3:]))
        df.loc[ts] = {'open': hour_close, 'high': h, 'low': l, 'close': c}
    return df


def _run(**kw):
    rr = kw.pop('rr', 2.0)
    direction = kw.pop('direction', 'both')
    return run_symbol(_daily(), _minutes(**kw), '2026-10-12', '2026-10-16',
                      rr=rr, direction=direction, now=AFTER)


def test_levels_are_the_previous_weeks():
    lv = weekly_level_table(_daily())[pd.Timestamp('2026-10-12')]
    assert (round(lv['bc'], 2), round(lv['pp'], 2), round(lv['tc'], 2)) == (100.0, 101.0, 102.0)
    assert (round(lv['r3'], 2), round(lv['s3'], 2)) == (108.5, 97.5)


def test_buy_enters_at_1015_stop_hour_low_target_two_r():
    (t,) = _run(hour_low=100.5, after=[('11:00', 111.0, 103.9, 110.5)])
    assert t['direction'] == 'BUY' and t['entry_time'] == '2026-10-14T10:15:00'
    assert (t['entry_price'], t['sl_price']) == (104.0, 100.5)
    assert t['target_price'] == 111.0                   # 104 + 2 × 3.5
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('Target hit', 111.0, 7.0)


def test_sell_stop_is_hour_high():
    (t,) = _run(day_open=101.0, hour_close=99.0, hour_high=101.5,
                after=[('12:00', 101.6, 98.8, 101.0)])
    assert (t['direction'], t['sl_price'], t['target_price']) == ('SELL', 101.5, 94.0)
    assert (t['exit_reason'], t['exit_price'], t['pnl']) == ('SL hit', 101.5, -2.5)


def test_bar_touching_both_is_booked_as_the_stop():
    (t,) = _run(hour_low=100.5, after=[('11:00', 112.0, 100.0, 105.0)])
    assert t['exit_reason'] == 'SL hit' and t['pnl'] == -3.5


def test_neither_hit_exits_at_cutoff():
    (t,) = _run(hour_low=100.5)
    assert t['exit_reason'] == 'Time exit' and t['exit_time'].endswith('15:15:00')
    assert t['pnl'] == 0.0


def test_rr_moves_the_target():
    (t,) = _run(hour_low=100.5, rr=1.0)
    assert t['target_price'] == 107.5


def test_scanner_conditions_gate_the_entry():
    assert _run(day_open=102.5) == []                    # opened above TC
    assert _run(hour_close=101.0) == []                  # hour closed inside
    assert _run(day_open=101.0, hour_close=104.0, hour_low=100.5)  # control
    assert _run(hour_close=108.2) == []                  # within 0.5 % of R3 108.5


def test_direction_candle_follows_from_open_inside():
    """The hour opens at the day's open. Inside the CPR (<= TC) and closing
    above TC, it is green by construction — so the direction condition never
    rejects a session the open-inside condition let through; it is kept for
    the scanner's sake, where it is the same shared verdict."""
    (t,) = _run(day_open=102.0, hour_close=102.5, hour_low=101.9)
    assert t['direction'] == 'BUY'


def test_direction_filter():
    assert _run(hour_low=100.5, direction='sell') == []
    assert len(_run(hour_low=100.5, direction='buy')) == 1


def test_session_still_trading_is_skipped():
    mid = datetime(2026, 10, 14, 11, 0)
    assert run_symbol(_daily(), _minutes(hour_low=100.5), '2026-10-12', '2026-10-16',
                      now=mid) == []
