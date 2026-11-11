import pytest
"""NIFTY Bi-weekly Iron Condor backtest engine, on a synthetic market priced by Black-76.

Every contract is priced at every 5-minute close off that bar's NIFTY and the time left, so
theta and delta are real: a flat NIFTY decays the condor, a rally pushes the call's delta up.
Nothing here reaches a broker."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.special import ndtr

from trading_app.Backtest import biweekly_iron_condor_engine as bic

QTY = 65
CAPITAL = 96000.0
ENTRY = date(2025, 11, 7)           # the video's Friday: sold the Tue 11-18 contract
EXPIRY = date(2025, 11, 18)


def _weekdays(start, end, holidays=()):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _weekdays(date(2025, 10, 20), date(2025, 12, 31))
BARS = pd.DatetimeIndex([ts for d in SESSIONS for ts in pd.date_range(
    datetime(d.year, d.month, d.day, 9, 15), datetime(d.year, d.month, d.day, 15, 25), freq='5min')])


def _spot(base=25500.0, moves=()):
    """NIFTY at `base`, stepped by `moves`: (from_ts, level) — each bar from then on reads it."""
    s = pd.Series(base, index=BARS)
    for ts, level in moves:
        s[s.index >= pd.Timestamp(ts)] = level
    return s


def _book(spot5, vol=0.13):
    """frame_for: each contract's 5-minute closes priced at `vol` off that bar's close."""
    asked = []
    close_ts = BARS + pd.Timedelta(minutes=5)

    def frame_for(expiry, strike, right='CE'):
        asked.append((expiry, strike, right))
        t = ((pd.Timestamp(datetime(expiry.year, expiry.month, expiry.day, 15, 30)) - close_ts)
             .total_seconds().to_numpy() / (365 * 86400))
        live = t > 0
        f = spot5.to_numpy()
        tt = np.where(live, t, 1e-9)
        d1 = (np.log(f / strike) + 0.5 * vol * vol * tt) / (vol * np.sqrt(tt))
        d2 = d1 - vol * np.sqrt(tt)
        px = f * ndtr(d1) - strike * ndtr(d2) if right == 'CE' else strike * ndtr(-d2) - f * ndtr(-d1)
        px = np.maximum(np.round(px, 2), 0.05)
        vals = pd.Series(px[live], index=BARS[live])
        return pd.DataFrame({'open': vals, 'high': vals, 'low': vals, 'close': vals})
    frame_for.asked = asked
    return frame_for


def _vix(level=11.5):
    return pd.Series(level, index=BARS)


def _run(spot5=None, vix=11.5, **params):
    spot5 = _spot() if spot5 is None else spot5
    p = {'min_credit_pct': 0, **params}
    return bic.run_trade(ENTRY, SESSIONS, spot5, _vix(vix), _book(spot5), QTY, CAPITAL, p)


# ── calendar ─────────────────────────────────────────────────────────────────

def test_the_bi_weekly_is_the_second_weekly_after_the_friday():
    # The video's two Fridays, Tuesday expiries.
    assert bic.sold_expiry(date(2025, 10, 31), SESSIONS) == date(2025, 11, 11)
    assert bic.sold_expiry(date(2025, 11, 7), SESSIONS) == date(2025, 11, 18)
    # Thursday regime: 13 days out, not the 6-day contract.
    june = _weekdays(date(2024, 6, 1), date(2024, 7, 31))
    assert bic.sold_expiry(date(2024, 6, 7), june) == date(2024, 6, 20)


def test_entry_is_the_weeks_last_session_on_a_thursday_or_friday():
    days = _weekdays(date(2025, 11, 1), date(2025, 11, 30), holidays={date(2025, 11, 14)})
    assert bic.entry_days(days, date(2025, 11, 1), date(2025, 11, 30)) == [
        date(2025, 11, 7), date(2025, 11, 13), date(2025, 11, 21), date(2025, 11, 28)]


def test_exit_day_is_next_friday_before_the_expiry():
    assert bic.exit_day(ENTRY, EXPIRY, SESSIONS) == date(2025, 11, 14)
    # The data stopping mid-week: no exit day yet (the trade is still open).
    assert bic.exit_day(ENTRY, EXPIRY, [d for d in SESSIONS if d <= date(2025, 11, 12)]) is None


def test_contract_window_reaches_the_entry_in_both_regimes():
    frm, _ = bic.contract_window(date(2025, 11, 18))        # Tuesday: the shared window
    assert frm <= date(2025, 11, 7)
    frm, to = bic.contract_window(date(2024, 6, 20))        # Thursday: widened past 12 days
    assert frm <= date(2024, 6, 7) and to == date(2024, 6, 20)


def test_black_round_trip_and_delta():
    px = bic.black_price(25000, 25500, 11 / 365, 0.13, 'CE')
    assert abs(bic.implied_vol(px, 25000, 25500, 11 / 365, 'CE') - 0.13) < 1e-6
    assert bic.delta_of(1.0, 25000, 24000, 11 / 365, 'CE') == 1.0          # no time value, ITM
    c = bic.abs_delta(25000, 25000, 11 / 365, 0.13, 'CE')
    p = bic.abs_delta(25000, 25000, 11 / 365, 0.13, 'PE')
    assert abs(c + p - 1.0) < 1e-9


# ── entry ────────────────────────────────────────────────────────────────────

def test_sells_near_point_two_delta_on_hundreds_with_hedges_two_hundred_out():
    r = _run(target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0, adjust=False)
    assert r['status'] == 'traded'
    sides = r['entry_sides']
    for right in ('CE', 'PE'):
        assert sides[right]['short'] % 100 == 0
        assert abs(sides[right]['short'] - sides[right]['hedge']) == 200
    assert sides['CE']['short'] > 25500 > sides['PE']['short']
    assert 0.12 < r['ce_delta'] < 0.30 and 0.12 < r['pe_delta'] < 0.30
    assert abs(r['credit'] - (sides['CE']['short_px'] + sides['PE']['short_px']
                              - sides['CE']['hedge_px'] - sides['PE']['hedge_px'])) < 1e-9
    # Flat NIFTY, nothing hit: held to the next Friday's 15:00 and paid by theta.
    assert r['reason'] == 'Time exit'
    assert r['exit_ts'] == pd.Timestamp('2025-11-14 15:00')
    assert r['pnl_points'] > 0


def test_vix_at_or_above_the_ceiling_is_no_trade():
    r = _run(vix=13.0)
    assert r['status'] == 'skipped' and 'VIX' in r['reason']
    assert _run(vix=13.0, max_vix=0)['status'] == 'traded'          # 0 = off


def test_credit_under_the_floor_is_no_trade():
    r = _run(min_credit_pct=50)
    assert r['status'] == 'skipped' and r['reason'].startswith('Net credit')


# ── exits ────────────────────────────────────────────────────────────────────

def test_flat_market_reaches_the_target_on_theta():
    r = _run(target_pct=0.3, stop_pct=5, eod_profit_pct=0, eod_loss_pct=0)
    assert r['reason'] == 'Target (gap)'    # theta lands overnight: booked at Monday's first close
    assert r['pnl_points'] * QTY >= 0.003 * CAPITAL


def test_day_end_takes_a_profit_rather_than_carry_it():
    r = _run(target_pct=5, stop_pct=5, eod_profit_pct=0.1, eod_loss_pct=0)
    assert r['reason'] == 'Day-end profit'
    assert r['exit_ts'].strftime('%H:%M') == '15:15'


def test_crash_through_the_put_is_stopped():
    spot5 = _spot(moves=[('2025-11-11 10:00', 24600.0)])
    r = _run(spot5, adjust=False, target_pct=5, stop_pct=1, eod_profit_pct=0, eod_loss_pct=0)
    assert r['reason'] == 'Stop loss'
    assert r['exit_ts'] == pd.Timestamp('2025-11-11 10:05')
    # A 900-point drop inside the session goes far past the 1 %; it is booked AT the 1 %,
    # and the worst shown is that, not the close beyond it.
    assert abs(r['pnl_points'] * QTY + 0.01 * CAPITAL) < 1e-6
    assert abs(r['trough_rupees'] + 0.01 * CAPITAL) < 1e-6


def test_a_gap_through_the_stop_is_booked_at_the_close_it_opened_on():
    spot5 = _spot(moves=[('2025-11-10 09:15', 24600.0)])     # Monday opens 900 lower
    r = _run(spot5, adjust=False, target_pct=5, stop_pct=1, eod_profit_pct=0, eod_loss_pct=0)
    assert r['reason'] == 'Stop loss (gap)'
    assert r['pnl_points'] * QTY < -0.01 * CAPITAL


# ── adjustments ──────────────────────────────────────────────────────────────

def test_rally_moves_the_call_side_out_and_rolls_the_put_in():
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0)])
    r = _run(spot5, target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0)
    log = r['adjustments']
    assert log, 'a 250-pt rally on a 0.2-delta call must trigger an adjustment'
    first = log[0]
    assert first['ts'] == '2025-11-10T11:05:00'
    ce = [a for a in log if a['side'] == 'CE' and a['move'] == 'out']
    assert ce and ce[0]['to'] > ce[0]['from']
    pe = [a for a in log if a['side'] == 'PE' and a['move'] == 'in']
    assert pe and pe[0]['to'] > pe[0]['from']
    # Each side move closes two legs and opens two.
    assert r['legs_traded'] == 4 + 2 * len(log)


def test_an_adjustment_does_not_move_the_mtm():
    """Closing a side and re-opening one at the same close leaves the P&L where it was."""
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0)])
    m = bic._open_week(ENTRY, SESSIONS, spot5, _vix(), _book(spot5), {**bic.DEFAULTS})
    st = bic._structure(m, 0.20, 200, True, _book(spot5))
    cut = bic._cut(ENTRY, m['expiry'], SESSIONS, bic.DEFAULTS)
    on = bic.build_path(m, st, cut, spot5, _book(spot5), {**bic.DEFAULTS, 'adjust': True})
    off = bic.build_path(m, st, cut, spot5, _book(spot5), {**bic.DEFAULTS, 'adjust': False})
    i = int(on['idx'].get_loc(pd.Timestamp('2025-11-10 11:00')))
    assert on['log'] and on['log'][0]['ts'] == '2025-11-10T11:05:00'
    assert abs(on['mtm'][i] - off['mtm'][i]) < 1e-9          # the bar it happened on
    assert on['strikes']['CE'][i + 1] != off['strikes']['CE'][i + 1]


def test_flat_market_rolls_a_decayed_side_in_without_churning():
    r = _run(target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0)
    # Decay is the only trigger in a flat market: every move is a roll IN, none walks out.
    assert all(a['move'] == 'in' for a in r['adjustments'])
    assert len(r['adjustments']) <= 6


def test_max_adjust_caps_the_moves():
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0), ('2025-11-11 11:00', 26000.0)])
    r = _run(spot5, target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0, max_adjust=1)
    assert len(r['adjustments']) == 1


def test_adjust_off_is_zero_adjustment():
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0)])
    r = _run(spot5, adjust=False, target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0)
    assert r['adjustments'] == [] and r['legs_traded'] == 4


# ── rows, summary, sweep ─────────────────────────────────────────────────────

def test_trade_row_charges_every_leg_traded():
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0)])
    r = _run(spot5, target_pct=50, stop_pct=50, eod_profit_pct=0, eod_loss_pct=0)
    row = bic.trade_row(r, QTY, 1, QTY, CAPITAL, 100)
    assert row['brokerage'] == 100 * r['legs_traded']
    assert row['pnl_rupees'] == round(r['pnl_points'] * QTY - row['brokerage'], 2)
    assert row['adjustments'] == len(r['adjustments']) and row['adjust_log']
    skipped = bic.trade_row(_run(vix=20), QTY, 1, QTY, CAPITAL, 100)
    assert skipped['result'] == 'SKIP' and skipped['high_vix']
    s = bic.summarise([row, skipped])
    assert s['total_trades'] == 1 and s['skipped_high_vix'] == 1
    assert s['by_year'][0]['weeks'] == 2 and s['by_year'][0]['high_vix'] == 1


@pytest.mark.parametrize('capital_pct', [0, 5.0])
def test_a_sweep_row_is_the_backtest_of_its_params(capital_pct):
    spot5 = _spot(moves=[('2025-11-10 11:00', 25750.0)])
    grid = {'max_vix': (12, 13), 'target_delta': (0.20,), 'hedge_gap': (200,), 'adjust': (True, False),
            'min_credit_pct': (0, 50), 'target_pct': (1.0, 50), 'stop_pct': (1.0, 50), 'day_end': (True, False),
            'calm': (True, False)}
    scored = bic.score_week(ENTRY, SESSIONS, spot5, _vix(12.5), _book(spot5), QTY, CAPITAL,
                            {'capital_pct': capital_pct}, 100, grid)
    assert all(k[0] == 13 for k in scored)                   # VIX 12.5 is not below 12
    assert {k[-1] for k in scored} == {True, False}           # a calm week has both rows
    for (vix, delta, gap, adjust, floor, tp, sp, de, cm), (status, net, pct, reason) in scored.items():
        p = {'max_vix': vix, 'target_delta': delta, 'hedge_gap': gap, 'adjust': adjust, 'min_credit_pct': floor,
             'target_pct': tp, 'stop_pct': sp,
             'eod_profit_pct': bic.DEFAULTS['eod_profit_pct'] if de else 0,
             'eod_loss_pct': bic.DEFAULTS['eod_loss_pct'] if de else 0, 'capital_pct': capital_pct,
             'max_rv': bic.DEFAULTS['max_rv'] if cm else 0, 'max_week_move': bic.DEFAULTS['max_week_move'] if cm else 0}
        r = bic.run_trade(ENTRY, SESSIONS, spot5, _vix(12.5), _book(spot5), QTY, CAPITAL, p)
        if r['status'] == 'skipped':
            assert status == 'skipped'
            continue
        row = bic.trade_row(r, QTY, 1, QTY, CAPITAL, 100)
        assert (status, reason) == (r['status'], r['reason'])
        assert abs(net - row['pnl_rupees']) < 0.02
    rows = bic.rank_board([scored], 1, min_trades=1)
    assert rows and set(bic.SWEEP_KEYS) <= set(rows[0])


def test_pair_is_nearest_delta_unless_the_premiums_are_far_apart():
    ce = [{'strike': 25900, 'price': 57.0, 'delta': 0.23}, {'strike': 26000, 'price': 38.0, 'delta': 0.19}]
    pe = [{'strike': 25300, 'price': 70.0, 'delta': 0.24}, {'strike': 25200, 'price': 58.2, 'delta': 0.20}]
    # The video's week: 0.19 CE at 38 against a 58 PE is too far apart — sell the 0.23 CE at 57 instead.
    c, p = bic.choose_pair(ce, pe, 0.20, True)
    assert (c['strike'], p['strike']) == (25900, 25200)
    assert [q['strike'] for q in bic.choose_pair(ce, pe, 0.20, False)] == [26000, 25200]
    near = [{'strike': 26000, 'price': 55.0, 'delta': 0.20}, {'strike': 25900, 'price': 70.0, 'delta': 0.25}]
    assert bic.choose_pair(near, pe, 0.20, True)[0]['strike'] == 26000      # within 20 %: left alone
    # 2026-06-19's candidates: the CE nearer 0.20 stays, the PE matches its premium — not both
    # sides walked in to the 0.24 / 0.22 pair whose premiums happen to be closest.
    ce = [{'strike': 24400, 'price': 63.65, 'delta': 0.238}, {'strike': 24500, 'price': 45.5, 'delta': 0.181}]
    pe = [{'strike': 23700, 'price': 63.6, 'delta': 0.223}, {'strike': 23600, 'price': 47.9, 'delta': 0.174}]
    assert [q['strike'] for q in bic.choose_pair(ce, pe, 0.20, True)] == [24500, 23600]


# ── ATM read ─────────────────────────────────────────────────────────────────

def test_atm_without_a_candle_falls_back_to_the_next_strike_pair():
    """2025-12-05: Breeze had no 26200 candle at the entry, and the whole week was skipped."""
    spot5 = _spot()
    book = _book(spot5)
    atm = int(round(spot5.iloc[0] / bic.STRIKE_STEP) * bic.STRIKE_STEP)

    def holed(expiry, strike, right='CE'):
        return None if strike == atm else book(expiry, strike, right)

    res = bic.run_trade(ENTRY, SESSIONS, spot5, _vix(), holed, QTY, CAPITAL, {'min_credit_pct': 0})
    assert res['status'] != 'skipped', res.get('reason')
    full = _run()
    assert {r: res['entry_sides'][r]['short'] for r in bic.SIDES} == \
        {r: full['entry_sides'][r]['short'] for r in bic.SIDES}



def test_sweep_day_end_on_is_never_the_zeroed_boxes_an_off_row_leaves():
    # Applying a Day-end Off row writes 0 / 0 to the page; the next sweep's "On" rows must still be on.
    b = bic.sweep_base({'eod_profit_pct': 0, 'eod_loss_pct': 0, 'x': 1})
    assert (b['eod_profit_pct'], b['eod_loss_pct'], b['x']) == \
        (bic.DEFAULTS['eod_profit_pct'], bic.DEFAULTS['eod_loss_pct'], 1)
    b = bic.sweep_base({'eod_profit_pct': 0.6, 'eod_loss_pct': 1.2})
    assert (b['eod_profit_pct'], b['eod_loss_pct']) == (0.6, 1.2)


def test_capital_pct_is_that_share_of_nifty_at_the_entry():
    # 5 % of NIFTY 25,500 × 65 = ₹82,875 for the week, whatever the fixed figure says; 0 keeps the fixed one.
    r = _run(capital_pct=5.0)
    assert r['capital'] == pytest.approx(0.05 * r['spot'] * QTY)
    assert r['credit_pct'] == pytest.approx(100.0 * r['credit'] * QTY / r['capital'])
    row = bic.trade_row(r, QTY, 1, QTY, CAPITAL, 100)
    assert row['capital'] == pytest.approx(r['capital'], abs=0.01)
    assert row['pnl_pct'] == pytest.approx(100.0 * row['pnl_rupees'] / r['capital'], abs=0.01)
    assert _run()['capital'] == CAPITAL
    # A lower index is a smaller margin, so the same credit clears a floor it failed on the fixed figure.
    m = {'spot': 18000.0}
    assert bic.week_capital(m, QTY, CAPITAL, {'capital_pct': 5.0}) == pytest.approx(0.05 * 18000 * QTY)
    assert bic.week_capital(m, QTY, CAPITAL, {'capital_pct': 0}) == CAPITAL


def test_calm_filter_reads_only_prices_up_to_the_entry():
    calm = bic.calm_reading(ENTRY, SESSIONS, _spot(), (15, 15))
    assert calm is not None and calm['rv'] < 1 and abs(calm['move']) < 0.01
    # A 3 % drop on the Monday before the Friday: a fast week is not traded, and says why.
    fast = _spot(moves=[('2025-11-03 10:00', 24735.0)])
    r = _run(fast)
    assert r['status'] == 'skipped' and r.get('not_calm') and 'NIFTY' in r['reason']
    assert '5 sessions' in _run(fast, max_rv=0)['reason']           # the move alone also stops it
    assert _run(fast, max_rv=0, max_week_move=0)['status'] != 'skipped'
    # …and the sweep has no calm-on row for that Friday, only the off ones.
    grid = {**bic.SWEEP_GRID, 'max_vix': (13,), 'target_delta': (0.20,), 'hedge_gap': (200,), 'adjust': (False,),
            'min_credit_pct': (0,), 'target_pct': (1.0,), 'stop_pct': (1.0,), 'day_end': (False,)}
    scored = bic.score_week(ENTRY, SESSIONS, fast, _vix(11.5), _book(fast), QTY, CAPITAL, bic.sweep_base({}), 100, grid)
    assert scored and {k[-1] for k in scored} == {False}
    # A move AFTER the entry is not seen at the entry.
    later = _spot(moves=[('2025-11-07 15:20', 24000.0)])
    assert bic.calm_reading(ENTRY, SESSIONS, later, (15, 15)) == pytest.approx(calm)


def test_sweep_calm_on_is_never_the_zeroed_boxes():
    assert bic.sweep_base({'max_rv': 0, 'max_week_move': 0})['max_rv'] == bic.DEFAULTS['max_rv']
    assert bic.sweep_base({'max_rv': 12, 'max_week_move': 1.5})['max_week_move'] == 1.5


def test_summary_says_the_most_the_account_needs():
    rows = [{'date': '2025-11-07', 'status': 'traded', 'capital': 80000.0, 'pnl_rupees': -900.0, 'gross_pnl_rupees': -500.0,
             'brokerage': 400.0, 'pnl_pct': -1.1, 'sessions_held': 2, 'exit_reason': 'Stop loss'},
            {'date': '2025-11-14', 'status': 'traded', 'capital': 85000.0, 'pnl_rupees': -1200.0, 'gross_pnl_rupees': -800.0,
             'brokerage': 400.0, 'pnl_pct': -1.4, 'sessions_held': 2, 'exit_reason': 'Stop loss'},
            {'date': '2025-11-21', 'status': 'skipped', 'capital': 99000.0, 'high_vix': True}]
    s = bic.summarise(rows)
    assert s['max_capital'] == 85000.0                    # a skipped week's capital is never put up
    assert s['amount_needed'] == 85000.0 + 2100.0         # + the worst run of losses
    assert s['max_trade_loss'] == -1200.0


def test_summary_per_month_counts_idle_months_as_zero():
    base = {'gross_pnl_rupees': 0.0, 'brokerage': 0.0, 'sessions_held': 2, 'exit_reason': 'Target', 'capital': 1.0}
    rows = [{**base, 'date': '2025-01-03', 'status': 'traded', 'pnl_rupees': 10.0, 'pnl_pct': 1.5},
            {**base, 'date': '2025-01-17', 'status': 'traded', 'pnl_rupees': 10.0, 'pnl_pct': 1.6},
            {'date': '2025-02-07', 'status': 'skipped', 'high_vix': True},
            {**base, 'date': '2025-03-07', 'status': 'traded', 'pnl_rupees': -10.0, 'pnl_pct': -1.1}]
    s = bic.summarise(rows)
    assert (s['months'], s['months_3pct'], s['losing_months']) == (3, 1, 1)
    assert s['avg_month_pct'] == pytest.approx((3.1 + 0 - 1.1) / 3, abs=0.01)
    assert (s['best_month_pct'], s['worst_month_pct']) == (3.1, -1.1)
