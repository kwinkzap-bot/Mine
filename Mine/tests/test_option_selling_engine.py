"""NIFTY monthly ATM short straddle — Backtest/option_selling_engine.py, on synthetic bars."""
from datetime import date, datetime, timedelta

import pandas as pd

from trading_app.Backtest import option_selling_engine as ose

QTY = 65


def _weekdays(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _weekdays(date(2025, 1, 1), date(2025, 3, 31))
ALL5 = pd.DatetimeIndex([ts for d in SESSIONS for ts in pd.date_range(
    datetime(d.year, d.month, d.day, 9, 15), datetime(d.year, d.month, d.day, 15, 25), freq='5min')])
SPOT = pd.Series(23010.0, index=ALL5)            # ATM 23000
ENTRY = date(2025, 1, 31)                        # last Friday of January → Feb 27 expiry


def _frames(price_fn):
    """frame_for(expiry, strike, right): close = price_fn(right, bar start), None = no bar."""
    def f(expiry, strike, right):
        rows = [(ts, price_fn(right, ts)) for ts in ALL5]
        rows = [(ts, px) for ts, px in rows if px is not None]
        if not rows:
            return None
        idx = pd.DatetimeIndex([ts for ts, _ in rows])
        px = [v for _, v in rows]
        return pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px}, index=idx)
    return f


def _after(ts, day, hm):
    return ts >= pd.Timestamp(datetime(day.year, day.month, day.day, *hm))


def test_entry_is_last_friday_15_15_on_next_months_atm():
    seen = []

    def price(right, ts):
        if ts.date() == ENTRY and ts.hour == 15 and ts.minute == 10:
            return 300.0 if right == 'CE' else 280.0          # the bar that ends at 15:15
        return 999.0 if ts.date() == ENTRY and ts.hour == 15 and ts.minute == 15 else 290.0

    def frames(expiry, strike, right):
        seen.append((expiry, strike, right))
        return _frames(price)(expiry, strike, right)

    assert ose.entry_days(SESSIONS, date(2025, 1, 1), date(2025, 1, 31)) == [ENTRY]
    m = ose.open_month(ENTRY, SESSIONS, SPOT, frames, ose.DEFAULTS)
    assert m['ok'] and m['expiry'] == date(2025, 2, 27) and m['strike'] == 23000
    assert m['entry_px'] == {'CE': 300.0, 'PE': 280.0} and m['premium'] == 580.0
    assert set(seen) == {(date(2025, 2, 27), 23000, 'CE'), (date(2025, 2, 27), 23000, 'PE')}


def test_target_is_five_percent_of_premium_collected():
    # 300 + 300 = 600 collected; 5 % = 30 pts. Premiums sag to 284 each on Feb 5
    # mid-session — the bar closes at +32, the fill is AT +30.
    def price(right, ts):
        return 284.0 if _after(ts, date(2025, 2, 5), (11, 0)) else 300.0
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price))
    assert res['status'] == 'traded' and res['reason'] == 'Target'
    assert res['pnl_points'] == 30.0
    assert res['exit_px']['CE'] + res['exit_px']['PE'] == 570.0
    assert res['exit_ts'] == pd.Timestamp('2025-02-05 11:05')        # that bar's close
    row = ose.trade_row(res, QTY, 1, QTY, 100)
    assert row['premium'] == 600.0 and row['pnl_rupees'] == 30 * QTY - 200
    assert row['pnl_pct'] == round(100 * (30 * QTY - 200) / (600 * QTY), 2)


def test_stop_is_minus_five_percent_on_the_whole_straddle():
    # CE runs +40, PE gives back 5 inside one bar: −35 pts on 600 → filled AT −30.
    def price(right, ts):
        if _after(ts, date(2025, 2, 10), (10, 0)):
            return 340.0 if right == 'CE' else 295.0
        return 300.0
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price))
    assert res['reason'] == 'Stop loss' and res['pnl_points'] == -30.0
    assert abs(res['exit_px']['CE'] - 300 - 40 * 30 / 35) < 1e-9
    assert abs(res['exit_px']['CE'] + res['exit_px']['PE'] - 630.0) < 1e-9
    row = ose.trade_row(res, QTY, 1, QTY, 100)
    assert row['trough_pct'] == -5.0


def test_a_session_opening_past_the_stop_fills_at_the_open():
    # Monday Feb 3 opens with the CE 45 higher: −45 on 600 before any stop could act.
    def price(right, ts):
        return 345.0 if right == 'CE' and ts.date() >= date(2025, 2, 3) else 300.0
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price))
    assert res['reason'] == 'Stop loss (gap)' and res['pnl_points'] == -45.0
    assert res['exit_px'] == {'CE': 345.0, 'PE': 300.0}


def test_every_entry_falls_inside_its_contracts_fetch_window():
    # 2026-09-25 is the last Friday but September's expiry is the 29th, so it
    # opens OCTOBER's contract — before the window that used to start 09-26.
    from trading_app.Backtest import call_ratio_engine as cre
    days = [date(2017, 1, 2) + timedelta(i) for i in range(3600)]
    sessions = [d for d in days if d.weekday() < 5 and d <= date(2026, 10, 30)]
    assert date(2026, 9, 25) in ose.entry_days(sessions, date(2017, 1, 1), date(2026, 10, 30))
    for rule in ose.ENTRY_DAYS:                  # every choice in the Entry Day box
        for e in ose.entry_days(sessions, date(2017, 1, 1), date(2026, 10, 30), rule):
            exp = cre.contract_expiry(e, sessions)
            assert cre.contract_window(exp)[0] <= e < exp, (rule, e)


def test_entry_day_picks_that_weekday_of_the_last_week():
    span = (date(2025, 1, 1), date(2025, 3, 31))
    assert ose.entry_days(SESSIONS, *span, 'last_monday') == [
        date(2025, 1, 27), date(2025, 2, 24), date(2025, 3, 31)]
    assert ose.entry_days(SESSIONS, *span, 'last_wednesday') == [
        date(2025, 1, 29), date(2025, 2, 26), date(2025, 3, 26)]
    # A holiday on the last Thursday moves to the Wednesday before it.
    no_jan30 = [d for d in SESSIONS if d != date(2025, 1, 30)]
    assert ose.entry_days(no_jan30, date(2025, 1, 1), date(2025, 1, 31), 'last_thursday') == [date(2025, 1, 29)]
    # Last Monday Jan 27 sells February (Jan 30 is < 14 days away); the backtest follows the box.
    res = ose.run_trade(date(2025, 1, 27), SESSIONS, SPOT, _frames(lambda r, ts: 300.0),
                        {'entry_day': 'last_monday'})
    assert res['expiry'] == date(2025, 2, 27)


def test_no_time_exit_held_until_the_contract_expires():
    def price(right, ts):
        return 290.0 if ts.date() > ENTRY else 300.0                  # +20 pts, inside 5 %
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price))
    # Held through every session; only the expiry day's last bar (15:25 → 15:30) ends it.
    assert res['reason'] == 'Expiry' and res['exit_ts'] == pd.Timestamp('2025-02-27 15:30')
    assert res['pnl_points'] == 20.0


def test_a_target_on_the_expiry_day_afternoon_is_still_taken():
    def price(right, ts):
        return 280.0 if _after(ts, date(2025, 2, 27), (15, 20)) else 300.0
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price))
    assert res['reason'] == 'Target' and res['pnl_points'] == 30.0


def test_missing_leg_skips_the_month():
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(lambda r, ts: 300.0 if r == 'CE' else None))
    assert res['status'] == 'skipped' and '23000 PE' in res['reason']
    row = ose.trade_row(res, QTY, 1, QTY, 100)
    assert row['result'] == 'SKIP' and row['pnl_rupees'] is None


def test_open_when_data_ends_before_the_exit():
    short = [d for d in SESSIONS if d <= date(2025, 2, 14)]
    spot = SPOT[SPOT.index.date <= date(2025, 2, 14)]
    res = ose.run_trade(ENTRY, short + [date(2025, 2, 27)], spot, _frames(lambda r, ts: 300.0))
    assert res['status'] == 'open'


def test_sweep_row_matches_the_backtest_of_its_params():
    def price(right, ts):
        d = ts.date()
        if d >= date(2025, 2, 20):
            return 260.0
        if d >= date(2025, 2, 12):
            return 320.0 if right == 'CE' else 300.0
        return 300.0
    frames = _frames(price)
    by_month = ose.entries_by_month(SESSIONS, date(2025, 1, 1), date(2025, 1, 31))
    entries = by_month[(2025, 1)]
    assert set(entries) == set(ose.ENTRY_DAYS)            # every Entry Day choice is scored
    assert entries['last_friday'] == ENTRY and entries['last_monday'] == date(2025, 1, 27)
    res = ose.score_month(entries, SESSIONS, SPOT, frames, QTY, {}, 100)
    assert len(res) == ose.sweep_size()
    for rule, entry in entries.items():
        for tp, sp in [(5, 5), (10, 5), (10, 10), (30, 30), (50, 50), (2, 30)]:
            bt = ose.trade_row(ose.run_trade(entry, SESSIONS, SPOT, frames,
                                             {'entry_day': rule, 'target_pct': tp, 'stop_pct': sp}),
                               QTY, 1, QTY, 100)
            status, net, pct, reason = res[(rule, 'none', 0, tp, sp)]
            assert (status, reason) == (bt['status'], bt['exit_reason']), (rule, tp, sp)
            assert round(net, 2) == bt['pnl_rupees'], (rule, tp, sp)
    rows = ose.rank([res])
    assert rows[0]['net_pnl_rupees'] == round(max(r[1] for r in res.values() if r[0] == 'traded'), 2)
    assert rows[0]['entry_day'] in ose.ENTRY_DAYS and set(ose.SWEEP_KEYS) <= set(rows[0])


# ── Adjustments: NIFTY 23010 rallies to 23260 on Feb 10 at 10:00 ─────────────
RALLY = date(2025, 2, 10)
SPOT_UP = pd.Series([23260.0 if _after(ts, RALLY, (10, 0)) else 23010.0 for ts in ALL5], index=ALL5)


def _strike_frames(price_fn):
    """frame_for whose close = price_fn(right, strike, bar start)."""
    def f(expiry, strike, right):
        px = [price_fn(right, strike, ts) for ts in ALL5]
        return pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px}, index=ALL5)
    return f


def _rally_prices(right, strike, ts):
    later = ts.date() >= date(2025, 2, 20)
    if not _after(ts, RALLY, (10, 0)):
        return 300.0
    if strike == 23000:
        return 400.0 if right == 'CE' else 220.0
    if strike == 23250:                                   # the new ATM
        return (260.0 if later else 320.0) if right == 'PE' else (330.0 if later else 310.0)
    return 1.0


WIDE = {'target_pct': 50, 'stop_pct': 50, 'adjust_pts': 200}


def test_roll_untested_moves_the_profitable_put_to_the_new_atm():
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {**WIDE, 'adjust': 'roll_untested'})
    (a,) = res['adjustments']
    assert a['ts'] == pd.Timestamp('2025-02-10 10:05') and a['strike'] == 23250
    assert a['closed'] == [('PE', 23000)] and a['opened'] == [('PE', 23250, -1, 320.0)]
    # CE 300 → 400 (−100), PE 23000 300 → 220 booked (+80), PE 23250 320 → 260 (+60).
    assert res['reason'] == 'Expiry' and res['pnl_points'] == 40.0
    assert res['exit_strikes'] == {'CE': 23000, 'PE': 23250}
    row = ose.trade_row(res, QTY, 1, QTY, 100)
    assert row['brokerage'] == 300 and row['adjust_count'] == 1          # three legs traded
    assert row['pe_exit'] == 260.0 and row['pe_exit_strike'] == 23250
    plain = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices), WIDE)
    assert plain['adjustments'] == [] and plain['pnl_points'] == -20.0   # 300+300 − 400−220


def test_recenter_sells_a_fresh_straddle_at_the_new_atm():
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {**WIDE, 'adjust': 'recenter'})
    (a,) = res['adjustments']
    assert sorted(a['closed']) == [('CE', 23000), ('PE', 23000)]
    # −100 + 80 booked; CE 23250 310 → 330 (−20), PE 23250 320 → 260 (+60).
    assert res['pnl_points'] == 20.0 and res['legs_traded'] == 4


def test_hedge_buys_the_tested_side_once():
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {**WIDE, 'adjust': 'hedge', 'adjust_pts': 100, 'max_adjust': 3})
    # 23260 is 260 off 23000: one hedge at 23250, then 10 off it — nothing more.
    (a,) = res['adjustments']
    assert a['closed'] == [] and a['opened'] == [('CE', 23250, 1, 310.0)]
    # −100 (CE) + 80 (PE) + 20 (the bought CE 310 → 330).
    assert res['pnl_points'] == 0.0


def test_target_and_stop_stay_on_the_first_premium_through_an_adjustment():
    # 600 collected, 10 % = 60: the rolled position reaches +40 by expiry — short of it.
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {'target_pct': 10, 'stop_pct': 50, 'adjust': 'roll_untested', 'adjust_pts': 200})
    assert res['reason'] == 'Expiry'
    # A 3 % stop (18) trips at the rally bar itself — before the roll at its close.
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {'target_pct': 50, 'stop_pct': 3, 'adjust': 'roll_untested', 'adjust_pts': 200})
    assert res['reason'] == 'Stop loss' and res['pnl_points'] == -18.0 and res['adjustments'] == []
    assert ose.trade_row(res, QTY, 1, QTY, 100)['brokerage'] == 200


def test_no_adjustment_on_the_expiry_day():
    spot = pd.Series([23400.0 if ts.date() == date(2025, 2, 27) else 23010.0 for ts in ALL5], index=ALL5)
    res = ose.run_trade(ENTRY, SESSIONS, spot, _strike_frames(lambda r, k, ts: 300.0),
                        {**WIDE, 'adjust': 'recenter'})
    assert res['adjustments'] == [] and res['reason'] == 'Expiry'


def test_sweep_rows_with_adjustments_match_the_backtest():
    frames = _strike_frames(_rally_prices)
    entries = {'last_friday': ENTRY}
    res = ose.score_month(entries, SESSIONS, SPOT_UP, frames, QTY, {}, 100)
    assert len(res) == len(ose.ADJUST_VARIANTS) * 225
    for kind, pt in ose.ADJUST_VARIANTS:
        for tp, sp in [(5, 5), (10, 50), (50, 50), (3, 3)]:
            bt = ose.trade_row(ose.run_trade(ENTRY, SESSIONS, SPOT_UP, frames,
                                             {'adjust': kind, 'adjust_pts': pt, 'target_pct': tp, 'stop_pct': sp}),
                               QTY, 1, QTY, 100)
            status, net, pct, reason = res[('last_friday', kind, pt, tp, sp)]
            assert (status, reason, round(net, 2)) == (bt['status'], bt['exit_reason'], bt['pnl_rupees']), \
                (kind, pt, tp, sp)
