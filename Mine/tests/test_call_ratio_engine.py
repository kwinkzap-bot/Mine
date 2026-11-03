"""NIFTY Monthly Call Ratio ("No-Brainer") backtest engine, on synthetic bars."""
from datetime import date, datetime, timedelta

import pandas as pd

from trading_app.Backtest import call_ratio_engine as cre

QTY = 65
CAPITAL = 130000.0


def _weekdays(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _weekdays(date(2025, 1, 1), date(2025, 3, 31))


def _bars5(days):
    return pd.DatetimeIndex([ts for d in days for ts in pd.date_range(
        datetime(d.year, d.month, d.day, 9, 15), datetime(d.year, d.month, d.day, 15, 25), freq='5min')])


ALL5 = _bars5(SESSIONS)


def _spot(level=23000.0, jump=None):
    """NIFTY 5-minute closes: flat at `level`; `jump=(day, value)` moves it from that day on."""
    vals = [jump[1] if jump and ts.date() >= jump[0] else level for ts in ALL5]
    return pd.Series(vals, index=ALL5)


def _bars(price_fn):
    """frame_for(expiry, strike): one constant price a session, from price_fn(strike, day)."""
    def f(expiry, strike):
        rows = [(ts, price_fn(strike, ts.date())) for ts in ALL5]
        rows = [(ts, px) for ts, px in rows if px is not None]
        if not rows:
            return None
        idx = pd.DatetimeIndex([ts for ts, _ in rows])
        px = [v for _, v in rows]
        return pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px}, index=idx)
    return f


def _base(strike):
    # 23300 → 250, 23600 → 130, 24600 → 10: downside 2·130 − 250 − 10 = 0.
    return {23300: 250.0, 23600: 130.0, 24600: 10.0}.get(strike, max(1.0, 400 - (strike - 23000) * 0.4))


def test_entry_days_last_friday_and_after_expiry():
    assert cre.entry_days(SESSIONS, date(2025, 1, 1), date(2025, 3, 31)) == [
        date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 28)]
    # Thursday regime: January's expiry is the 30th → the 31st.
    assert cre.entry_days(SESSIONS, date(2025, 1, 1), date(2025, 1, 31), 'after_expiry') == [date(2025, 1, 31)]
    # A holiday on the last Friday moves to the session before it.
    no_jan31 = [d for d in SESSIONS if d != date(2025, 1, 31)]
    assert cre.entry_days(no_jan31, date(2025, 1, 1), date(2025, 1, 31)) == [date(2025, 1, 30)]


def test_contract_is_next_months_expiry():
    assert cre.contract_expiry(date(2025, 1, 31), SESSIONS) == date(2025, 2, 27)


def test_strikes_walk_out_while_credit_is_too_big():
    # At 300 out: 2·200 − 250 − 10 = 140 pts → 7 % credit. Every 100 further cuts it.
    prices = {23300: 250.0, 23600: 200.0, 24600: 10.0,
              23400: 200.0, 23700: 110.0, 24700: 8.0}
    ts = pd.Timestamp(datetime(2025, 1, 31, 15, 16))
    pick = cre.choose_strikes(23000, ts, lambda k: _bars(lambda s, d: prices.get(s))(None, k),
                              QTY, CAPITAL, cre.DEFAULTS)
    assert pick['ok'] and pick['distance'] == 400
    assert pick['strikes'] == {'buy': 23400, 'sell': 23700, 'hedge': 24700}
    assert [t['distance'] for t in pick['tried']] == [300, 400]


def test_big_downside_debit_is_not_deployed():
    prices = {23300: 300.0, 23600: 100.0, 24600: 10.0}       # −110 pts → −5.5 %
    res = cre.run_trade(date(2025, 1, 31), SESSIONS, _spot(), _bars(lambda s, d: prices.get(s)),
                        QTY, CAPITAL)
    assert res['status'] == 'skipped' and 'debit' in res['reason']


def test_target_hit_when_the_short_calls_decay():
    entry = date(2025, 1, 31)

    def px(strike, day):
        p = _base(strike)
        if day > entry and strike == 23600:
            p -= 40.0          # two sold lots × 40 × 65 = ₹5,200 = 4 % > 2.5 %
        return p
    res = cre.run_trade(entry, SESSIONS, _spot(), _bars(px), QTY, CAPITAL)
    assert res['status'] == 'traded' and res['reason'] == 'Target'
    assert res['exit_ts'] == pd.Timestamp(datetime(2025, 2, 3, 9, 15))
    row = cre.trade_row(res, QTY, 1, 65, CAPITAL, 100)
    assert row['pnl'] == 80.0 and row['pnl_rupees'] == 80 * 65 - 300


def test_stop_loss_on_a_rally_below_the_bought_strike():
    entry = date(2025, 1, 31)

    def px(strike, day):
        p = _base(strike)
        if day > entry and strike == 23600:
            p += 60.0          # −2·60·65 = −₹7,800 = −6 %
        return p
    res = cre.run_trade(entry, SESSIONS, _spot(), _bars(px), QTY, CAPITAL)
    assert res['reason'] == 'Stop loss'


def test_spot_through_the_bought_strike_exits():
    entry = date(2025, 1, 31)
    res = cre.run_trade(entry, SESSIONS, _spot(jump=(date(2025, 2, 5), 23350.0)),
                        _bars(lambda s, d: _base(s)), QTY, CAPITAL)
    assert res['reason'] == 'Spot crossed buy strike'
    assert res['exit_ts'] == pd.Timestamp(datetime(2025, 2, 5, 9, 15))


def test_time_exit_n_sessions_before_expiry():
    entry = date(2025, 1, 31)
    res = cre.run_trade(entry, SESSIONS, _spot(), _bars(lambda s, d: _base(s)), QTY, CAPITAL)
    # Feb 27 expiry, 5 sessions before → Thu Feb 20, at 15:15.
    assert res['reason'] == 'Time exit'
    assert res['exit_ts'] == pd.Timestamp(datetime(2025, 2, 20, 15, 15))
    assert res['sessions_held'] == 15
    assert res['pnl_points'] == 0


def test_a_missing_leg_carries_its_last_price():
    entry = date(2025, 1, 31)

    def px(strike, day):
        if strike == 24600 and day > entry:
            return None                            # the far hedge never prints again
        return _base(strike)
    res = cre.run_trade(entry, SESSIONS, _spot(), _bars(px), QTY, CAPITAL)
    assert res['reason'] == 'Time exit' and res['exit_px']['hedge'] == 10.0


def test_summarise_scores_closed_trades_only():
    rows = [
        {'status': 'traded', 'pnl_rupees': 3000.0, 'gross_pnl_rupees': 3300.0, 'brokerage': 300.0,
         'pnl_pct': 2.31, 'sessions_held': 8, 'exit_reason': 'Target'},
        {'status': 'traded', 'pnl_rupees': -4000.0, 'gross_pnl_rupees': -3700.0, 'brokerage': 300.0,
         'pnl_pct': -3.08, 'sessions_held': 4, 'exit_reason': 'Stop loss'},
        {'status': 'skipped', 'pnl_rupees': None},
        {'status': 'open', 'pnl_rupees': 100.0},
    ]
    s = cre.summarise(rows)
    assert s['total_trades'] == 2 and s['wins'] == 1 and s['losses'] == 1
    assert s['total_pnl_rupees'] == -1000.0 and s['max_drawdown_rupees'] == 4000.0
    assert s['skipped_months'] == 1 and s['open_trades'] == 1
    assert s['exit_reasons'] == {'Target': 1, 'Stop loss': 1}


def test_contract_window_starts_before_the_previous_expiry():
    assert cre.contract_window(date(2025, 2, 27)) == (date(2025, 1, 27), date(2025, 2, 27))


def test_sweep_row_agrees_with_the_backtest_of_its_params():
    entry = date(2025, 1, 31)

    def px(strike, day):
        p = {23300: 250.0, 23600: 130.0, 24600: 10.0,
                   23200: 300.0, 23500: 170.0, 24500: 12.0}.get(strike, 5.0)
        if day > entry:
            p -= {23600: 15.0, 23500: 20.0}.get(strike, 0.0) * min(5, (day - entry).days)
        return p
    grid = {**cre.SWEEP_GRID, 'entry_day': ('last_friday',), 'min_distance': (200, 300),
            'ratio_gap': (300,), 'hedge_gap': (1000,), 'max_credit_pct': (1.0, 5.0)}
    res = cre.score_expiry({'last_friday': entry}, SESSIONS, _spot(), _bars(px), QTY, CAPITAL,
                           {}, 100.0, grid)
    assert len(res) == cre.sweep_size(grid)
    rows = cre.rank([res], grid=grid)
    for r in rows[:5] + rows[-5:]:
        params = {k: r[k] for k in cre.STRUCTURE_KEYS + cre.EXIT_KEYS}
        one = cre.run_trade(entry, SESSIONS, _spot(), _bars(px), QTY, CAPITAL, params)
        row = cre.trade_row(one, QTY, 1, 65, CAPITAL, 100.0)
        assert round(r['net_pnl_rupees'], 2) == row['pnl_rupees'], params


def test_an_expiry_still_to_come_snaps_to_the_listed_date():
    # 2026-11-24 is a holiday: the exchange lists the 23rd. Past the sessions
    # we hold, the calendar cannot know that.
    sessions = _weekdays(date(2026, 9, 1), date(2026, 11, 3))
    listed = (date(2026, 11, 17), date(2026, 11, 23), date(2026, 12, 29))
    assert cre.contract_expiry(date(2026, 10, 30), sessions) == date(2026, 11, 24)
    assert cre.contract_expiry(date(2026, 10, 30), sessions, 14, listed) == date(2026, 11, 23)
    # A settled month keeps the date its sessions give it.
    assert cre.contract_expiry(date(2026, 9, 25), sessions, 14, listed) == date(2026, 10, 27)
