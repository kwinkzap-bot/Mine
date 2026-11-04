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
    assert len(res) == ose.sweep_size(cadences=('monthly',))
    for rule, entry in entries.items():
        for tp, sp in [(5, 5), (10, 5), (10, 10), (30, 30), (50, 50), (2, 30)]:
            bt = ose.trade_row(ose.run_trade(entry, SESSIONS, SPOT, frames,
                                             {'entry_day': rule, 'target_pct': tp, 'stop_pct': sp}),
                               QTY, 1, QTY, 100)
            status, net, pct, reason = res[('monthly', rule, False, 0, 'none', 0, tp, sp, 0, 0)]
            assert (status, reason) == (bt['status'], bt['exit_reason']), (rule, tp, sp)
            assert round(net, 2) == bt['pnl_rupees'], (rule, tp, sp)
    rows = ose.rank([res])
    assert rows[0]['net_pnl_rupees'] == round(max(r[1] for r in res.values() if r[0] == 'traded'), 2)
    assert rows[0]['entry_day'] in ose.ENTRY_DAYS and set(ose.SWEEP_KEYS) <= set(rows[0])
    assert rows[0]['cadence'] == 'monthly'


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


def test_hedge_roll_buys_the_tested_side_and_rolls_the_profit_leg():
    res = ose.run_trade(ENTRY, SESSIONS, SPOT_UP, _strike_frames(_rally_prices),
                        {**WIDE, 'adjust': 'hedge_roll'})
    (a,) = res['adjustments']
    assert a['closed'] == [('PE', 23000)]
    assert a['opened'] == [('PE', 23250, -1, 320.0), ('CE', 23250, 1, 310.0)]
    # CE −100, PE 23000 +80 booked, PE 23250 320 → 260 (+60), bought CE 23250 310 → 330 (+20).
    assert res['pnl_points'] == 60.0 and res['legs_traded'] == 4
    assert res['exit_strikes'] == {'CE': 23000, 'PE': 23250}


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
    assert len(res) == len(ose.WINGS) * len(ose.ADJUST_VARIANTS) * len(ose.exit_combos())
    for kind, pt in ose.ADJUST_VARIANTS:
        for tp, sp in [(5, 5), (10, 50), (50, 50), (3, 3)]:
            bt = ose.trade_row(ose.run_trade(ENTRY, SESSIONS, SPOT_UP, frames,
                                             {'adjust': kind, 'adjust_pts': pt, 'target_pct': tp, 'stop_pct': sp}),
                               QTY, 1, QTY, 100)
            status, net, pct, reason = res[('monthly', 'last_friday', False, 0, kind, pt, tp, sp, 0, 0)]
            assert (status, reason, round(net, 2)) == (bt['status'], bt['exit_reason'], bt['pnl_rupees']), \
                (kind, pt, tp, sp)


# ── Wings and profit lock ─────────────────────────────────────────────────────
def _by_strike(fn):
    """frame_for whose close is fn(right, strike, bar start)."""
    def f(expiry, strike, right):
        px = [fn(right, strike, ts) for ts in ALL5]
        return pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px}, index=ALL5)
    return f


def test_wings_are_bought_at_entry_and_the_percents_are_of_the_net_credit():
    # ATM 23000 sold at 300 + 300, wings 23500 CE / 22500 PE bought at 50 + 50 → 500 net.
    # Feb 5 11:00: the shorts lose 20 each (+40), the wings 10 each (−20) → +20 = 4 % of 500.
    def px(right, strike, ts):
        later = _after(ts, date(2025, 2, 5), (11, 0))
        if strike == 23000:
            return 280.0 if later else 300.0
        return 40.0 if later else 50.0
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _by_strike(px),
                        {'wings': 500, 'target_pct': 4, 'stop_pct': 50})
    assert res['reason'] == 'Target' and res['pnl_points'] == 20.0
    assert res['premium'] == 500.0 and res['sold_premium'] == 600.0
    assert [(lg['right'], lg['strike'], lg['side']) for lg in res['legs']] == [
        ('CE', 23000, 'SELL'), ('PE', 23000, 'SELL'), ('CE', 23500, 'BUY'), ('PE', 22500, 'BUY')]
    row = ose.trade_row(res, QTY, 1, QTY, 100)
    assert row['brokerage'] == 400 and row['premium'] == 500.0
    assert row['wings'] == 'CE 23500 @50.0 / PE 22500 @50.0'


def test_a_missing_wing_skips_the_month():
    def px(right, strike, ts):
        return 300.0 if strike == 23000 else None
    def frames(expiry, strike, right):
        return None if strike != 23000 else _by_strike(lambda r, k, ts: 300.0)(expiry, strike, right)
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, frames, {'wings': 500})
    assert res['status'] == 'skipped' and 'wing' in res['reason']


def test_profit_lock_stops_at_break_even_after_it_has_been_reached():
    # +80 on 600 (13 %) on Feb 5 arms a 10 % → 0 lock; Feb 7 10:00 the legs are back
    # at 310 (−20): the lock fills AT 0 instead of riding to the expiry's −20.
    def price(right, ts):
        if _after(ts, date(2025, 2, 7), (10, 0)):
            return 310.0
        return 260.0 if _after(ts, date(2025, 2, 5), (11, 0)) else 300.0
    p = {'target_pct': 50, 'stop_pct': 50}
    assert ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price), p)['reason'] == 'Expiry'
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price), {**p, 'lock_at': 10, 'lock_to': 0})
    assert res['reason'] == 'Profit lock' and abs(res['pnl_points']) < 1e-9
    assert res['exit_ts'] == pd.Timestamp('2025-02-07 10:05')
    # Not armed (the peak never reached 15 %): no lock.
    res = ose.run_trade(ENTRY, SESSIONS, SPOT, _frames(price), {**p, 'lock_at': 15, 'lock_to': 0})
    assert res['reason'] == 'Expiry'


def test_the_streaming_fold_scores_like_the_shared_rank_and_the_board_keeps_steady_rows():
    from trading_app.Backtest import call_ratio_engine as cre
    months = [
        {('a',): ('traded', 100.0, 10.0, 'Target'), ('b',): ('traded', 40.0, 4.0, 'Target')},
        {('a',): ('traded', -80.0, -8.0, 'Stop loss'), ('b',): ('skipped', 0.0, 0.0, 'x')},
        {('a',): ('traded', 90.0, 9.0, 'Target'), ('b',): ('traded', 30.0, 3.0, 'Expiry')},
        {('a',): ('open', 5.0, 0.5, 'Open (data ends)'), ('b',): ('traded', -5.0, -0.5, 'Stop loss')},
    ]
    f = ose.Fold()
    for m in months:
        f.add(m)
    mine = {r['cadence']: r for r in f.rows()}
    theirs = {r['k']: r for r in cre.rank(months, keys=('k',))}
    for k in ('a', 'b'):
        for field in ('total_trades', 'skipped', 'open', 'win_rate', 'net_pnl_rupees', 'total_pct',
                      'avg_pct', 'worst_pct', 'profit_factor', 'max_drawdown_rupees', 'exits'):
            assert mine[k][field] == theirs[k][field], (k, field)
    assert mine['a']['net_dd'] == round(110 / 80, 2) and mine['b']['net_dd'] == round(65 / 5, 2)
    # 'b' earns well under 'a' but 13× its drawdown — with n=1 the board still keeps it.
    assert [r['cadence'] for r in ose.board(f.rows(), n=1)] == ['a', 'b']


def test_sweep_rows_with_wings_and_a_profit_lock_match_the_backtest():
    frames = _strike_frames(_rally_prices)
    sweep = {'variants': (('none', 0), ('hedge_roll', 200), ('roll_untested', 200)),
             'wings': (0, 500), 'locks': ((0, 0), (3, 0), (10, 5))}
    res = ose.score_month({'last_friday': ENTRY}, SESSIONS, SPOT_UP, frames, QTY, {}, 100, **sweep)
    assert len(res) == ose.sweep_size(**sweep, cadences=('monthly',)) // len(ose.ENTRY_DAYS)
    for w in (0, 500):
        for kind, pt in sweep['variants']:
            for tp, sp, la, lt in [(5, 5, 0, 0), (10, 50, 3, 0), (50, 50, 10, 5), (3, 3, 0, 0)]:
                p = {'wings': w, 'adjust': kind, 'adjust_pts': pt, 'target_pct': tp, 'stop_pct': sp,
                     'lock_at': la, 'lock_to': lt}
                bt = ose.trade_row(ose.run_trade(ENTRY, SESSIONS, SPOT_UP, frames, p), QTY, 1, QTY, 100)
                status, net, pct, reason = res[('monthly', 'last_friday', False, w, kind, pt, tp, sp, la, lt)]
                assert (status, reason, round(net, 2)) == (bt['status'], bt['exit_reason'], bt['pnl_rupees']), p


# ── Weekly cadence ────────────────────────────────────────────────────────────
def test_weekly_entry_days_and_expiries():
    from trading_app.Backtest import call_ratio_engine as cre
    span = (date(2025, 2, 1), date(2025, 2, 28))
    assert ose.entry_days(SESSIONS, *span, 'friday', 'weekly') == [
        date(2025, 2, 7), date(2025, 2, 14), date(2025, 2, 21), date(2025, 2, 28)]
    # A holiday on the Monday moves nothing into the week before: that week has no Monday entry.
    no_feb10 = [d for d in SESSIONS if d != date(2025, 2, 10)]
    assert date(2025, 2, 7) not in ose.entry_days(no_feb10, *span, 'monday', 'weekly')
    # A holiday Wednesday falls back to the Tuesday of the same week.
    no_feb12 = [d for d in SESSIONS if d != date(2025, 2, 12)]
    assert date(2025, 2, 11) in ose.entry_days(no_feb12, *span, 'wednesday', 'weekly')
    # The session after each Thursday expiry (2025 was the Thursday regime).
    assert ose.entry_days(SESSIONS, *span, 'after_expiry', 'weekly')[:2] == [date(2025, 2, 7), date(2025, 2, 14)]
    # Friday sells the NEXT Thursday; Thursday (expiry day) sells the one a week on.
    assert ose.weekly_expiry(date(2025, 2, 7), SESSIONS) == date(2025, 2, 13)
    assert ose.weekly_expiry(date(2025, 2, 13), SESSIONS) == date(2025, 2, 20)
    # A weekly contract is fetched over its own fortnight; the monthly keeps its window.
    assert cre.is_weekly_expiry(date(2025, 2, 13)) and not cre.is_weekly_expiry(date(2025, 2, 27))
    assert cre.contract_window(date(2025, 2, 13)) == (date(2025, 2, 1), date(2025, 2, 13))
    assert cre.contract_window(date(2025, 2, 27))[0] < date(2025, 1, 25)


def test_a_weekly_trade_sells_the_next_weekly_expiry_and_ends_on_it():
    seen = []

    def frames(expiry, strike, right):
        seen.append((expiry, strike, right))
        return _frames(lambda r, ts: 290.0 if ts.date() > date(2025, 2, 7) else 300.0)(expiry, strike, right)
    p = {'cadence': 'weekly', 'entry_day': 'friday', 'target_pct': 50, 'stop_pct': 50}
    res = ose.run_trade(date(2025, 2, 7), SESSIONS, SPOT, frames, p)
    assert res['expiry'] == date(2025, 2, 13) and res['reason'] == 'Expiry'
    assert res['exit_ts'] == pd.Timestamp('2025-02-13 15:30') and res['pnl_points'] == 20.0
    assert {e for e, _, _ in seen} == {date(2025, 2, 13)}
    # Before NIFTY had weeklies there is nothing to sell.
    old = ose.open_month(date(2018, 6, 1), SESSIONS, SPOT, frames, {**ose.DEFAULTS, **p})
    assert not old['ok'] and 'weekly' in old['reason']


def test_the_weekly_sweep_scores_one_week_per_period_and_matches_the_backtest():
    frames = _frames(lambda r, ts: 285.0 if _after(ts, date(2025, 2, 11), (11, 0)) else 300.0)
    by_week = ose.entries_by_month(SESSIONS, date(2025, 2, 3), date(2025, 2, 9), cadence='weekly')
    assert list(by_week) == [(2025, 6)] and set(by_week[(2025, 6)]) == set(ose.WEEKLY_ENTRY_DAYS)
    base = {'cadence': 'weekly'}
    sweep = {'variants': (('none', 0),), 'locks': ((0, 0),)}
    res = ose.score_month(by_week[(2025, 6)], SESSIONS, SPOT, frames, QTY, base, 100, **sweep)
    for rule, entry in by_week[(2025, 6)].items():
        for tp, sp in [(5, 5), (2, 50)]:
            bt = ose.trade_row(ose.run_trade(entry, SESSIONS, SPOT, frames,
                                             {**base, 'entry_day': rule, 'target_pct': tp, 'stop_pct': sp}),
                               QTY, 1, QTY, 100)
            status, net, pct, reason = res[('weekly', rule, False, 0, 'none', 0, tp, sp, 0, 0)]
            assert (status, reason, round(net, 2)) == (bt['status'], bt['exit_reason'], bt['pnl_rupees']), (rule, tp, sp)


def test_find_best_params_sweeps_both_expiries_with_a_half_of_each_to_score():
    assert ose.sweep_size() == 3 * ose.sweep_size(cadences=('monthly',))
    f = ose.Fold()
    for _ in range(3):                        # 3 months, each combo traded
        f.add({('monthly', 'x'): ('traded', 10.0, 1.0, 'Target')})
    for _ in range(5):                        # 12 weeks, the weekly combo trading only 5
        f.add({('weekly', 'y'): ('traded', 10.0, 1.0, 'Target')})
    assert {r['cadence'] for r in f.rows({'monthly': 1, 'weekly': 6})} == {'monthly'}
    assert {r['cadence'] for r in f.rows({'monthly': 1, 'weekly': 5})} == {'monthly', 'weekly'}
    # Weeks before NIFTY had weeklies are not periods of the weekly sweep.
    sessions = [date(2019, 1, 28) + timedelta(i) for i in range(21)]
    sessions = [d for d in sessions if d.weekday() < 5]
    by = ose.entries_by_month(sessions, sessions[0], sessions[-1], cadence='weekly')
    assert min(by) == date(2019, 2, 11).isocalendar()[:2]


def test_the_vectorised_exit_search_agrees_with_first_exit_combo_for_combo():
    import numpy as np
    rng = np.random.default_rng(7)
    idx = pd.DatetimeIndex([ts for ts in ALL5 if date(2025, 2, 3) <= ts.date() <= date(2025, 2, 27)])
    exits = ose.exit_combos(locks=ose.LOCKS + ((10, 0), (5, 2)))
    ex = {k: np.array([e[i] for e in exits], dtype=float) for i, k in enumerate(('tp', 'sp', 'la', 'lt'))}
    for trial in range(30):
        mtm = np.cumsum(rng.normal(0, 4, len(idx)))
        if trial % 5 == 0:
            mtm[:3] = [0.0, 40.0, -40.0]         # ties and early hits
        path = {'idx': idx, 'mtm': mtm}
        cut = pd.Timestamp('2025-02-27 15:30') if trial % 3 else pd.Timestamp('2025-02-20 12:00')
        pos, why = ose.first_exits(path, 300.0, ex, cut)
        hits = {}
        for i, (tp, sp, la, lt) in enumerate(exits):
            assert (pos[i], ose._REASONS[why[i]]) == ose.first_exit(path, 300.0, tp, sp, cut, hits, la, lt), \
                (trial, tp, sp, la, lt)


def test_weekly_expiry_follows_the_thursday_to_tuesday_cutover():
    sessions = [date(2025, 8, 18) + timedelta(i) for i in range(28)]
    sessions = [d for d in sessions if d.weekday() < 5]
    # Thursday regime until 2025-09-01, Tuesday after: Thu 08-28's next weekly is Tue 09-02, not Thu 09-04.
    assert ose.weekly_expiry(date(2025, 8, 21), sessions) == date(2025, 8, 28)
    assert ose.weekly_expiry(date(2025, 8, 28), sessions) == date(2025, 9, 2)
    assert ose.weekly_expiry(date(2025, 8, 29), sessions) == date(2025, 9, 2)
    assert ose.weekly_expiry(date(2025, 9, 2), sessions) == date(2025, 9, 9)
    assert date(2025, 9, 4) not in ose._weekly_expiries(sessions)
    assert ose.weekly_entry_days(sessions, sessions[0], sessions[-1], 'after_expiry')[:3] == [
        date(2025, 8, 22), date(2025, 8, 29), date(2025, 9, 3)]


def test_skip_one_night_drops_only_the_weeks_whose_expiry_is_the_next_session():
    # Thursday regime: a Wednesday entry is one night from Thursday's expiry; a Friday entry is not.
    frames = _frames(lambda r, ts: 300.0)
    p = {'cadence': 'weekly', 'skip_one_night': True}
    wed = ose.run_trade(date(2025, 2, 12), SESSIONS, SPOT, frames, {**p, 'entry_day': 'wednesday'})
    fri = ose.run_trade(date(2025, 2, 7), SESSIONS, SPOT, frames, {**p, 'entry_day': 'friday'})
    assert wed['status'] == 'skipped' and 'one-night' in wed['reason']
    assert fri['status'] != 'skipped'
    sweep = {'variants': (('none', 0),), 'locks': ((0, 0),), 'grid': {'target_pct': (5,), 'stop_pct': (5,)}}
    by = ose.entries_by_month(SESSIONS, date(2025, 2, 10), date(2025, 2, 16), cadence='weekly')
    res = ose.score_month(by[(2025, 7)], SESSIONS, SPOT, frames, QTY, {'cadence': 'weekly'}, 100, **sweep)
    key = lambda rule, skip: ('weekly', rule, skip, 0, 'none', 0, 5, 5, 0, 0)
    assert res[key('wednesday', True)][0] == 'skipped' and res[key('wednesday', False)][0] != 'skipped'
    assert res[key('friday', True)] == res[key('friday', False)]
    for rule, entry in by[(2025, 7)].items():               # the sweep agrees with the backtest
        bt = ose.run_trade(entry, SESSIONS, SPOT, frames, {**p, 'entry_day': rule, 'target_pct': 5, 'stop_pct': 5})
        assert (res[key(rule, True)][0] == 'skipped') == (bt['status'] == 'skipped'), rule


def test_compounding_sizes_one_lot_per_two_lakh_of_closed_balance():
    def row(day, exit_day, pts, status='traded'):
        return {'date': day, 'status': status, 'lots': 1, 'lot_size': 65, 'qty': 65,
                'premium': 300.0, 'entry_time': f'{day}T15:15:00', 'exit_time': f'{exit_day}T15:30:00',
                'gross_pnl_rupees': pts * 65, 'brokerage': 200.0, 'pnl_rupees': pts * 65 - 200,
                'pnl': pts, 'pnl_pct': 0.0, 'result': ''}
    rows = [
        row('2025-01-03', '2025-01-20', 3080.0),     # +₹2,00,000 net at 1 lot → ₹4 L
        row('2025-01-10', '2025-02-20', 100.0),      # entered while the first is still open: 1 lot
        row('2025-01-24', '2025-01-30', 100.0),      # the first has closed: ₹4 L → 2 lots
        {'date': '2025-01-31', 'status': 'skipped', 'exit_reason': 'x', 'lot_size': 65},
    ]
    out = ose.compound(rows, 200000, 200000)
    assert [r.get('lots') for r in out] == [1, 1, 2, None]
    assert out[0]['capital'] == 200000 and out[2]['capital'] == 200000 + 3080 * 65 - 200
    assert out[2]['qty'] == 130 and out[2]['pnl_rupees'] == 100 * 130 - 200        # brokerage per leg, not per lot
    assert out[2]['pnl_pct'] == round(100 * (100 * 130 - 200) / (300 * 130), 2)
    assert out[3]['status'] == 'skipped'
    # Below a lot's capital it still trades 1; at/below zero it stops; never past the freeze size.
    lose = [row('2025-01-03', '2025-01-10', -3100.0), row('2025-01-17', '2025-01-20', 10.0)]
    assert ose.compound(lose, 200000, 200000)[1]['exit_reason'] == 'Capital exhausted'
    rich = [row('2025-01-03', '2025-01-10', 1.0)]
    assert ose.compound(rich, 200000 * 40, 200000)[0]['lots'] == ose.MAX_LOTS
