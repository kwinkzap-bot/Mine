"""Rule tests for the Weekly CPR Open scanner.

Synthetic bars; a stub stands in for CPRFilterService so nothing reaches
ICICI. Every bar has high 110 / low 90. Context weeks close at 108:
PP 102.67, BC 100, TC 105.33, width 4.94 %. The in-force week (2026-10-05..09)
closes at 103: PP 101, BC 100, TC 102, width 1.94 % — 0.39× its normal, so
very narrow at 0.5× but not at 0.3×. Its Camarilla: R3 = 103 + 20 × 1.1/4 =
108.5, S3 = 97.5.
"""
from datetime import datetime

import pandas as pd

from trading_app.filters.weekly_cpr_open_scanner import (
    evaluate, filter_weekly_cpr_open, get_weekly_cpr_open_row, select_matches)

MONDAY = datetime(2026, 10, 12)
WED = datetime(2026, 10, 14)
BC, TC, R3, S3 = 100.0, 102.0, 108.5, 97.5
AFTER_HOUR = datetime(2026, 10, 30, 12, 0)     # every first hour here has closed


def _daily(prev_close=101.0, today=MONDAY, today_open=101.0, in_force_close=103.0,
           context_close=108.0):
    """Daily bars from July to the day before `today`, plus `today`'s bar.
    `prev_close` overrides the last close before `today` on a mid-week day."""
    idx = pd.bdate_range('2026-07-06', today - pd.Timedelta(days=1))
    rows = []
    for d in idx:
        last_of_week = d.weekday() == 4
        in_force = pd.Timestamp('2026-10-05') <= d <= pd.Timestamp('2026-10-09')
        close = (in_force_close if in_force else context_close) if last_of_week else 100.0
        if d >= pd.Timestamp('2026-10-12'):
            close = 101.0
        rows.append({'open': 100.0, 'high': 110.0, 'low': 90.0, 'close': close})
    if today.weekday() != 0:
        rows[-1]['close'] = prev_close
    df = pd.DataFrame(rows, index=idx)
    df.loc[pd.Timestamp(today)] = {'open': today_open, 'high': today_open + 1,
                                   'low': today_open - 1, 'close': today_open + 0.5}
    return df


def _session(day=MONDAY, first_open=101.0, hour_close=104.0):
    """`day`'s 30-minute bars: the 09:15 bar opens at `first_open`, the 09:45
    one closes the first hour at `hour_close`."""
    stamps = pd.date_range(pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=15),
                           periods=13, freq='30min')
    closes = [101.0, hour_close] + [hour_close] * 11
    return pd.DataFrame({'open': [first_open] + closes[:-1],
                         'high': [max(first_open, c) + 0.2 for c in closes],
                         'low': [min(first_open, c) - 0.2 for c in closes],
                         'close': closes}, index=stamps)


def _eval(day=MONDAY, hour_close=104.0, now=AFTER_HOUR, first_open=101.0, **kw):
    return evaluate(_daily(today=day, **kw), day.date(),
                    intraday=_session(day, first_open=first_open, hour_close=hour_close),
                    now=now)


# ── levels ───────────────────────────────────────────────────────────────

def test_band_and_camarilla_are_previous_week():
    r = _eval()
    assert (r['w_bc'], r['w_pp'], r['w_tc']) == (BC, 101.0, TC)
    assert (r['w_r3'], r['w_s3']) == (R3, S3)
    assert r['cpr_week'].startswith('2026-10-05')


def test_monday_prev_close_is_friday():
    r = _eval()
    assert r['prev_date'] == '2026-10-09' and r['prev_close'] == 103.0


def test_prev_close_no_longer_decides():
    """The prev-close-at-CPR condition was removed: a previous close far
    below the band still matches."""
    r = dict(_eval(WED, prev_close=95.0), symbol='X', is_index=False)
    assert select_matches([r])


def test_open_must_be_inside():
    for o, ok in ((100.0, True), (102.0, True), (101.0, True), (102.3, False), (99.9, False)):
        r = dict(_eval(WED, today_open=o), symbol='X', is_index=False)
        assert r['open_ok'] is ok, o
        assert bool(select_matches([r])) is ok, o


def test_no_bar_on_session_date_is_unreadable():
    assert evaluate(_daily().iloc[:-1], MONDAY.date()) is None


# ── CPR width: shown, no longer a condition ──────────────────────────

def test_width_ratio_is_against_ten_weeks_before():
    r = _eval(WED)
    assert r['w_width_pct'] == 1.942 and r['w_avg_width_pct'] == 4.938
    assert r['w_ratio'] == 0.39


def test_cpr_width_no_longer_decides():
    """The very-narrow condition was removed: a normal-width week, and one
    with too little history to average, match on the breakout alone."""
    wide = dict(_eval(WED, in_force_close=108.0, hour_close=106.0), symbol='X', is_index=False)
    assert wide['w_ratio'] == 1.0 and select_matches([wide])
    assert select_matches([dict(wide, w_ratio=None)])


# ── the conditions: first hour closes outside, short of R3 / S3 ─────────

def test_first_hour_above_tc_with_room_matches():
    r = _eval(WED, hour_close=104.0)
    assert (r['fh_close'], r['fh_side'], r['fh_ok']) == (104.0, 'above', True)
    assert r['fh_room_pct'] == 4.33                     # (108.5 - 104) / 104
    assert select_matches([dict(r, symbol='X', is_index=False)])


def test_first_hour_below_bc_with_room_matches():
    r = _eval(WED, hour_close=99.0)
    assert (r['fh_side'], r['fh_ok']) == ('below', True)


def test_first_hour_closing_inside_fails():
    r = _eval(WED, hour_close=101.0)
    assert (r['fh_side'], r['fh_ok']) == ('inside', False)


def test_first_hour_near_or_past_r3_fails():
    assert not _eval(WED, hour_close=108.0)['fh_ok']    # within 0.5% of 108.5
    assert _eval(WED, hour_close=107.9)['fh_ok']        # 108.5*0.995 = 107.96
    assert not _eval(WED, hour_close=109.0)['fh_ok']    # already past R3


def test_first_hour_near_or_past_s3_fails():
    assert not _eval(WED, hour_close=97.9)['fh_ok']     # 97.5*1.005 = 97.99
    assert _eval(WED, hour_close=98.1)['fh_ok']
    assert not _eval(WED, hour_close=97.0)['fh_ok']


def test_first_hour_must_be_a_direction_candle_agreeing_with_the_break():
    buy = _eval(WED, first_open=101.0, hour_close=104.0)
    assert (buy['fh_dir'], buy['fh_side'], buy['fh_ok']) == ('BUY', 'above', True)
    sell = _eval(WED, first_open=101.0, hour_close=99.0)
    assert (sell['fh_dir'], sell['fh_side'], sell['fh_ok']) == ('SELL', 'below', True)
    # Red hour closing above TC / green hour closing below BC: fading the break.
    red_above = _eval(WED, first_open=105.0, hour_close=104.0)
    assert (red_above['fh_dir'], red_above['fh_side'], red_above['fh_ok']) == ('SELL', 'above', False)
    green_below = _eval(WED, first_open=98.0, hour_close=99.0)
    assert (green_below['fh_dir'], green_below['fh_side'], green_below['fh_ok']) == ('BUY', 'below', False)
    # A doji is neither.
    doji = _eval(WED, first_open=104.0, hour_close=104.0)
    assert (doji['fh_dir'], doji['fh_ok']) == (None, False)


def test_first_hour_is_pending_until_1015():
    r = _eval(WED, now=datetime(2026, 10, 14, 10, 10))
    assert (r['fh_side'], r['fh_ok'], r['fh_close']) == ('pending', False, None)
    assert _eval(WED, now=datetime(2026, 10, 14, 10, 15))['fh_side'] == 'above'


def test_first_hour_needs_both_bars():
    df = _daily(today=WED)
    only_first = _session(WED).iloc[:1]
    assert evaluate(df, WED.date(), intraday=only_first, now=AFTER_HOUR)['fh_side'] == 'pending'


# ── fetch and scan ──────────────────────────────────────────────────────

class _Kite:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def historical_data(self, token, start, end, interval):
        self.calls.append((interval, start, end))
        df = self.frames.get((token, interval))
        if df is None:
            return []
        df = df[(df.index >= pd.Timestamp(start))
                & (df.index < pd.Timestamp(end) + pd.Timedelta(days=1))]
        return [dict(date=i, **r) for i, r in df.to_dict('index').items()]


class _Svc:
    MAX_WORKERS = 2

    def __init__(self, frames):
        self.kite = _Kite(frames)

    def get_token(self, symbol):
        return symbol

    def get_fo_stocks(self):
        return ['AAA', 'BBB', 'CCC']


def _frames(sym, day, prev_close=101.0, hour_close=104.0, first_open=101.0):
    return {(sym, 'day'): _daily(today=day, prev_close=prev_close).iloc[:-1],
            (sym, '30minute'): _session(day, first_open=first_open, hour_close=hour_close)}


def test_session_comes_from_30_minute_bars_and_daily_stops_before():
    """Breeze has no daily bar for the session until it is over — the open and
    first hour come from 30-minute bars, and the daily range ending the day
    before stays 'settled', so the adapter's disk cache answers re-scans."""
    svc = _Svc(_frames('AAA', WED))
    row = get_weekly_cpr_open_row(svc, 'AAA', WED, now=AFTER_HOUR)
    assert svc.kite.calls[0] == ('day', '2026-06-16', '2026-10-13')
    assert svc.kite.calls[1] == ('30minute', '2026-10-14', '2026-10-14')
    assert row['open'] == 101.0 and row['fh_close'] == 104.0 and row['prev_close'] == 101.0


def test_scan_keeps_every_row_and_rolls_weekend_back(monkeypatch):
    import trading_app.filters.cpr_camarilla_scanner as cam
    monkeypatch.setattr(cam, 'INDEX_TOKENS', {})
    fri = datetime(2026, 10, 16)
    frames = {**_frames('AAA', fri),                          # breaks above TC
              **_frames('BBB', fri, hour_close=101.0)}        # first hour back inside
    seen = []
    out = filter_weekly_cpr_open(_Svc(frames), root_date=datetime(2026, 10, 18),  # Sunday
                                 progress_cb=lambda d, t: seen.append((d, t)), now=AFTER_HOUR)
    assert out['date'] == '2026-10-16'
    assert [r['symbol'] for r in out['rows']] == ['AAA', 'BBB']
    assert [r['symbol'] for r in select_matches(out['rows'])] == ['AAA']
    assert (out['read'], out['skipped'], out['scanned']) == (2, 1, 3)
    assert seen[-1] == (3, 3) and len(seen) == 3
