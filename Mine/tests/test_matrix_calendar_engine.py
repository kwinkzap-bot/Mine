"""NIFTY Matrix Calendar backtest engine, on synthetic bars priced by Black-76."""
from datetime import date, datetime, timedelta

import pandas as pd

from trading_app.Backtest import matrix_calendar_engine as mce

QTY = 65
CAPITAL = 225000.0
ENTRY = date(2026, 1, 5)            # a Monday; weekly Tue 01-13, monthly Tue 01-27
WEEKLY, MONTHLY = date(2026, 1, 13), date(2026, 1, 27)


def _weekdays(start, end, holidays=()):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _weekdays(date(2026, 1, 1), date(2026, 2, 27))
BARS = pd.DatetimeIndex([ts for d in SESSIONS for ts in pd.date_range(
    datetime(d.year, d.month, d.day, 9, 15), datetime(d.year, d.month, d.day, 15, 25), freq='5min')])
SPOT = pd.Series(25000.0, index=BARS)


def _book(vol=0.25, marks=None):
    """frame_for: every contract priced at `vol` as of the entry, flat through time, a
    `marks[(expiry, strike, right)]` series of (from_ts, price) overriding it later."""
    asked = []
    t0 = datetime(2026, 1, 5, 15, 15)

    def frame_for(expiry, strike, right='CE'):
        asked.append((expiry, strike, right))
        t = (datetime(expiry.year, expiry.month, expiry.day, 15, 30) - t0).total_seconds() / (365 * 86400)
        base = mce.black_price(25000.0, strike, t, vol, right)
        vals = pd.Series(base, index=BARS)
        for ts, price in (marks or {}).get((expiry, strike, right), []):
            vals[vals.index >= pd.Timestamp(ts)] = price
        return pd.DataFrame({'open': vals, 'high': vals, 'low': vals, 'close': vals})
    frame_for.asked = asked
    return frame_for


def _run(frame_for, **params):
    return mce.run_trade(ENTRY, SESSIONS, SPOT, frame_for, QTY, CAPITAL, params)


def test_expiries_are_the_eight_day_weekly_and_the_monthly_after_it():
    march = _weekdays(date(2026, 3, 1), date(2026, 5, 1), holidays={date(2026, 3, 3), date(2026, 3, 31)})
    # The video's own week: Mon 03-02 is not the 1-DTE contract but Tue 03-10; the monthly is 03-30.
    assert mce.sold_expiry(date(2026, 3, 2), march) == date(2026, 3, 10)
    assert mce.monthly_expiry(date(2026, 3, 10), march) == date(2026, 3, 30)
    # A weekly that IS the monthly: the calendar leg goes to the month after.
    assert mce.sold_expiry(date(2026, 3, 23), march) == date(2026, 3, 30)
    assert mce.monthly_expiry(date(2026, 3, 30), march) == date(2026, 4, 28)
    # Thursday regime: 10 days out, not the 3-day contract.
    june = _weekdays(date(2025, 6, 1), date(2025, 7, 31))
    assert mce.sold_expiry(date(2025, 6, 2), june) == date(2025, 6, 12)


def test_entry_days_are_session_mondays_only():
    days = _weekdays(date(2026, 1, 1), date(2026, 1, 31), holidays={date(2026, 1, 12)})
    assert mce.entry_days(days, date(2026, 1, 1), date(2026, 1, 31)) == [
        date(2026, 1, 5), date(2026, 1, 19), date(2026, 1, 26)]


def test_black_round_trip_and_delta():
    px = mce.black_price(25000, 25500, 8 / 365, 0.22, 'CE')
    assert abs(mce.implied_vol(px, 25000, 25500, 8 / 365, 'CE') - 0.22) < 1e-6
    assert mce.implied_vol(1.0, 25000, 24000, 8 / 365, 'CE') is None        # below intrinsic
    call = mce.abs_delta(25000, 25000, 8 / 365, 0.22, 'CE')
    put = mce.abs_delta(25000, 25000, 8 / 365, 0.22, 'PE')
    assert abs(call + put - 1.0) < 1e-9


def test_sold_strikes_bracket_the_delta_and_the_legs_hang_off_them():
    r = _run(_book(), target_pct=50, stop_pct=50)
    assert r['status'] == 'traded'
    legs = r['legs']
    ce, pe = legs['sell_ce']['strike'], legs['sell_pe']['strike']
    assert ce % 100 == 0 and pe % 100 == 0
    assert ce > 25000 > pe
    assert 0.17 < r['ce_delta'] < 0.29 and 0.17 < r['pe_delta'] < 0.29
    assert (legs['hedge_ce']['strike'], legs['hedge_pe']['strike']) == (ce + 500, pe - 500)
    assert (legs['cal_ce']['strike'], legs['cal_pe']['strike']) == (ce, pe)
    assert [legs[k]['weight'] for k in ('sell_ce', 'sell_pe', 'hedge_ce', 'hedge_pe', 'cal_ce', 'cal_pe')] == [-2, -2, 1, 1, 1, 1]
    assert sum(abs(leg['weight']) for leg in legs.values()) == 8     # four sold, four bought
    assert r['iv'] > 24


def test_only_the_calendar_leg_touches_the_monthly():
    f = _book()
    r = _run(f, target_pct=50, stop_pct=50)
    monthly = {x for x in f.asked if x[0] == MONTHLY}
    assert monthly == {(MONTHLY, r['legs']['cal_ce']['strike'], 'CE'), (MONTHLY, r['legs']['cal_pe']['strike'], 'PE')}
    # The ladder is not walked from the ATM: ATM ±, a few candidates, the hedges.
    assert len({x for x in f.asked if x[0] == WEEKLY}) <= 10


def test_balance_takes_the_closest_premiums_and_off_takes_the_nearest_delta():
    ce = [{'strike': 25400, 'price': 120.0, 'delta': 0.26}, {'strike': 25500, 'price': 90.0, 'delta': 0.22}]
    pe = [{'strike': 24600, 'price': 125.0, 'delta': 0.24}, {'strike': 24500, 'price': 92.0, 'delta': 0.195}]
    # 90 vs 92 is the closest pair although 0.24/0.22 are the nearest deltas
    c, p = mce.choose_pair(ce, pe, 0.23, True)
    assert (c['strike'], p['strike']) == (25500, 24500)
    c, p = mce.choose_pair(ce, pe, 0.23, False)
    assert (c['strike'], p['strike']) == (25500, 24600)


def test_low_iv_is_skipped_before_any_strike_is_fetched():
    f = _book(vol=0.12)
    r = _run(f)
    assert r['status'] == 'skipped' and r['low_iv'] and 'below 20' in r['reason']
    assert len(f.asked) == 2                # the ATM call and put, nothing else
    assert _run(_book(vol=0.12), min_iv=0, target_pct=50, stop_pct=50)['status'] == 'traded'


def _sold(frame_for=None):
    r = _run(frame_for or _book(), target_pct=50, stop_pct=50)
    return r['legs'], r['entry_px']


def test_target_books_the_tripping_close_at_its_real_time():
    legs, px = _sold()
    k = (WEEKLY, legs['sell_ce']['strike'], 'CE')
    # The sold CE falls 30 pts on Tue 10:00: 2 × 30 × 65 = ₹3,900 > 1.5 % of ₹2.25 L (₹3,375).
    r = _run(_book(marks={k: [('2026-01-06 10:00', px['sell_ce'] - 30)]}))
    assert r['reason'] == 'Target' and r['status'] == 'traded'
    assert r['exit_ts'] == pd.Timestamp('2026-01-06 10:05')
    assert abs(r['pnl_points'] - 60.0) < 1e-6
    assert r['sessions_held'] == 1


def test_stop_books_past_the_level_on_a_gap():
    legs, px = _sold()
    k = (WEEKLY, legs['sell_pe']['strike'], 'PE')
    # 2 % of ₹2.25 L = ₹4,500 = 69 per-unit pts; the gap is 100 and books all of it.
    r = _run(_book(marks={k: [('2026-01-07 09:15', px['sell_pe'] + 50)]}))
    assert r['reason'] == 'Stop loss'
    assert r['exit_ts'] == pd.Timestamp('2026-01-07 09:20')
    assert abs(r['pnl_points'] + 100.0) < 1e-6


def test_time_exit_on_the_second_session():
    r = _run(_book())
    assert r['reason'] == 'Time exit' and r['pnl_points'] == 0.0
    assert r['exit_ts'] == pd.Timestamp('2026-01-07 15:15')     # Mon → Wed
    assert r['sessions_held'] == 2


def test_the_entry_bar_is_the_last_finished_before_the_time():
    legs, px = _sold()
    k = (WEEKLY, legs['sell_ce']['strike'], 'CE')
    # A print in the 15:15 bar (closes 15:20) is after a 15:16 entry: the MTM's, not the fill's.
    r = _run(_book(marks={k: [('2026-01-05 15:15', px['sell_ce'] - 40)]}))
    assert r['entry_px']['sell_ce'] == px['sell_ce']
    assert r['reason'] == 'Target' and r['exit_ts'] == pd.Timestamp('2026-01-05 15:20')


def test_data_ending_before_the_cut_is_open_not_booked():
    sessions = [d for d in SESSIONS if d <= date(2026, 1, 6)]
    spot = SPOT[SPOT.index.date <= date(2026, 1, 6)]
    r = mce.run_trade(ENTRY, sessions, spot, _book(), QTY, CAPITAL, {})
    assert r['status'] == 'open' and r['reason'] == 'Open (data ends)'
    row = mce.trade_row(r, QTY, 1, 65, CAPITAL, 100)
    assert row['result'] == 'OPEN'
    assert mce.summarise([row])['total_trades'] == 0


def test_a_missing_leg_is_a_skip_naming_it():
    book = _book()

    def f(expiry, strike, right='CE'):
        return None if expiry == MONTHLY else book(expiry, strike, right)
    r = _run(f)
    assert r['status'] == 'skipped' and 'monthly' in r['reason']


def test_row_and_summary_charge_six_legs_and_count_low_iv_skips():
    legs, px = _sold()
    k = (WEEKLY, legs['sell_ce']['strike'], 'CE')
    r = _run(_book(marks={k: [('2026-01-06 10:00', px['sell_ce'] - 30)]}))
    row = mce.trade_row(r, QTY, 1, 65, CAPITAL, 100)
    assert row['brokerage'] == 600
    assert row['pnl_rupees'] == round(60 * QTY - 600, 2)
    assert row['result'] == 'WIN'
    skipped = mce.trade_row(_run(_book(vol=0.12)), QTY, 1, 65, CAPITAL, 100)
    s = mce.summarise([skipped, row])
    assert s['total_trades'] == 1 and s['skipped_months'] == 1 and s['skipped_low_iv'] == 1


def test_route_parses_the_boxes_and_runs_on_the_shared_machinery(monkeypatch):
    """The route's worker, driven directly: no test_client, whose first request would start
    api_bp's order monitor, and no adapters, since _cr_start is the part stubbed out."""
    import inspect

    import pytest
    from flask import Flask

    from trading_app.app.routes import api

    s, e, p, sets, cap = api._mc_parse({'start_date': '2026-01-01', 'end_date': '2026-01-31'})
    assert (p['entry_hm'], p['exit_hm'], p['min_iv'], p['target_delta']) == ((15, 16), (15, 15), 20.0, 0.23)
    assert (p['hedge_gap'], p['target_pct'], p['stop_pct'], p['max_hold_sessions']) == (500, 1.5, 2.0, 2)
    assert p['balance_premium'] is True and (sets, cap) == (1, 225000.0)
    assert api._mc_parse({'start_date': '2026-01-01', 'end_date': '2026-01-31', 'balance_premium': False})[2]['balance_premium'] is False
    for bad in ({'hedge_gap': 550}, {'end_date': '2025-01-01'}):
        with pytest.raises(ValueError):
            api._mc_parse({'start_date': '2026-01-01', 'end_date': '2026-01-31', **bad})

    captured = {}
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    monkeypatch.setattr(api, '_cr_start', lambda data, worker, name, parse=None: captured.update(worker=worker, name=name, parse=parse))
    with Flask(__name__).test_request_context('/', method='POST', json={}):
        inspect.unwrap(api.run_matrix_calendar_backtest_api)()
    assert captured['name'] == 'mc' and captured['parse'] is api._mc_parse

    task = {'progress': {'done': 0, 'total': 0, 'traded': 0, 'contracts': 0}}
    captured['worker']({'params': {**p, 'target_pct': 50, 'stop_pct': 50}, 'sessions': SESSIONS, 'start': ENTRY, 'end': ENTRY,
                        'spot5': SPOT, 'frame_for': _book(), 'qty': QTY, 'capital': CAPITAL,
                        'sets': 1, 'lot_size': 65, 'capital_per_set': CAPITAL}, task)
    assert task['progress']['total'] == 1 and task['progress']['traded'] == 1
    assert task['payload']['summary']['weeks_scanned'] == 1
    assert task['payload']['summary']['params']['entry_hm'] == '15:16'
    assert task['payload']['trades'][0]['exit_reason'] == 'Time exit'


SMALL_GRID = {'min_iv': (18, 20, 30), 'target_delta': (0.20, 0.23), 'hedge_gap': (300, 500),
              'balance_premium': (True, False), 'target_pct': (0.5, 1.5), 'stop_pct': (0.3, 2.0),
              'max_hold_sessions': (1, 2)}


def _moving_book():
    """Every sold CE strike falls 30 on Tue 10:00 and every sold PE rises 25 on Wed 11:00."""
    book = _book()
    def f(expiry, strike, right='CE'):
        frame = book(expiry, strike, right).copy()
        if expiry == WEEKLY:
            when, move = ('2026-01-06 10:00', -30) if right == 'CE' else ('2026-01-07 11:00', 25)
            frame.loc[frame.index >= pd.Timestamp(when), 'close'] += move
        return frame
    return f


def test_every_sweep_combo_equals_the_backtest_of_its_params():
    import itertools
    book = _moving_book()
    res = mce.score_monday(ENTRY, SESSIONS, SPOT, book, QTY, CAPITAL, {}, 100, SMALL_GRID)
    keys = mce.SWEEP_KEYS
    # IV is 25 here: the 30 floor has no entry, the others do.
    combos = list(itertools.product(*(SMALL_GRID[k] for k in keys)))
    assert {c for c in combos if c[0] < 30} == set(res)
    for combo, (status, net, pct, reason) in res.items():
        run = mce.run_trade(ENTRY, SESSIONS, SPOT, book, QTY, CAPITAL, dict(zip(keys, combo)))
        row = mce.trade_row(run, QTY, 1, 65, CAPITAL, 100)
        assert (status, reason) == (row['status'], row['exit_reason']), combo
        assert abs(net - row['pnl_rupees']) < 0.01 and abs(pct - row['pnl_pct']) < 0.01, combo
    assert len({r[3] for r in res.values()}) >= 2       # the grid really does exit differently


def test_a_quiet_monday_costs_two_contracts_and_no_board_rows():
    f = _book(vol=0.12)
    assert mce.score_monday(ENTRY, SESSIONS, SPOT, f, QTY, CAPITAL, {}, 100, SMALL_GRID) == {}
    assert len(f.asked) == 2


def test_rank_board_counts_mondays_below_the_floor_as_skipped():
    combo = (20, 0.23, 500, True, 1.5, 2.0, 2)
    per_monday = [{combo: ('traded', 1000.0, 0.4, 'Target')}, {}, {combo: ('traded', -400.0, -0.2, 'Stop loss')}, {}]
    row = mce.rank_board(per_monday, 4, min_trades=2)[0]
    assert dict(zip(mce.SWEEP_KEYS, combo)).items() <= row.items()
    assert (row['total_trades'], row['skipped'], row['net_pnl_rupees'], row['max_drawdown_rupees']) == (2, 2, 600.0, 400.0)
    assert mce.rank_board(per_monday, 4, min_trades=3) == []


def test_muhurat_evening_is_no_expiry_day():
    # Tue 2025-10-21 has only an evening session: the week's contract expired Mon the 20th.
    days = _weekdays(date(2025, 10, 1), date(2025, 11, 28), holidays={date(2025, 10, 22)})
    bars = [ts for d in days for ts in pd.date_range(datetime(d.year, d.month, d.day, 9, 15),
                                                     datetime(d.year, d.month, d.day, 15, 25), freq='5min')
            if d != date(2025, 10, 21)]
    bars += list(pd.date_range(datetime(2025, 10, 21, 13, 45), datetime(2025, 10, 21, 14, 40), freq='5min'))
    spot = pd.Series(25000.0, index=pd.DatetimeIndex(sorted(bars)))
    regular = mce.regular_sessions(days, spot)
    assert date(2025, 10, 21) not in regular and date(2025, 10, 20) in regular
    assert mce.sold_expiry(date(2025, 10, 13), regular) == date(2025, 10, 20)


def test_optimise_route_stops_at_the_budget_newest_first(monkeypatch):
    import inspect

    from flask import Flask

    from trading_app.app.routes import api

    captured = {}
    monkeypatch.setattr(api, 'check_auth', lambda: None)
    monkeypatch.setattr(api, '_cr_sweep_budget', lambda: 1)
    monkeypatch.setattr(api, '_cr_workers', lambda: 1)
    monkeypatch.setattr(api, '_cr_start', lambda data, worker, name, **kw: captured.update(worker=worker, name=name, kw=kw))
    with Flask(__name__).test_request_context('/', method='POST', json={}):
        inspect.unwrap(api.run_matrix_calendar_optimise_api)()
    assert captured['name'] == 'mc-opt'
    assert captured['kw'] == {'parse': api._mc_parse, 'background': True, 'one_at_a_time': True}

    state = {'breeze_requests': 0}
    book = _book()

    def frame_for(expiry, strike, right='CE'):
        state['breeze_requests'] += 1       # every read is a real request here
        return book(expiry, strike, right)
    task = {'progress': {'done': 0, 'total': 0, 'traded': 0, 'contracts': 0}}
    _, _, p, _, _ = api._mc_parse({'start_date': '2026-01-01', 'end_date': '2026-01-31'})
    captured['worker']({'params': p, 'sessions': SESSIONS, 'start': date(2026, 1, 1), 'end': date(2026, 1, 12),
                        'spot5': SPOT, 'frame_for': frame_for, 'qty': QTY, 'capital': CAPITAL, 'sets': 1, 'lot_size': 65,
                        'state': state, 'check_stopped': lambda: None}, task)
    out = task['payload']
    assert out['mondays_in_range'] == 2 and out['mondays'] == 1
    assert out['covered_from'] == '2026-01-12'         # the newest was reached, the older was not


def test_an_outage_day_is_still_a_session_and_muhurat_is_not():
    day = date(2021, 2, 24)         # NSE halted from 11:40
    bars = list(pd.date_range(datetime(2021, 2, 23, 9, 15), datetime(2021, 2, 23, 15, 25), freq='5min'))
    bars += list(pd.date_range(datetime(2021, 2, 24, 9, 15), datetime(2021, 2, 24, 11, 35), freq='5min'))
    bars += list(pd.date_range(datetime(2021, 11, 4, 18, 15), datetime(2021, 11, 4, 19, 10), freq='5min'))
    spot = pd.Series(1.0, index=pd.DatetimeIndex(bars))
    assert mce.regular_sessions([date(2021, 2, 23), day, date(2021, 11, 4)], spot) == [date(2021, 2, 23), day]


def test_by_year_shows_a_year_with_no_trades_and_why():
    def row(d, **kw):
        return {'date': d, 'status': 'skipped', 'iv': 10.0, 'pnl_rupees': None, **kw}
    rows = [row('2023-01-02', low_iv=True, iv=12.0), row('2023-01-09', low_iv=True, iv=19.9),
            row('2024-01-01', iv=30.0), row('2024-01-08', status='traded', iv=25.0, pnl_rupees=500.0),
            row('2024-01-15', status='traded', iv=21.0, pnl_rupees=-200.0), row('2024-01-22', status='open', iv=22.0),
            row('2024-01-29', iv=None)]
    y23, y24 = mce.by_year(rows)

    assert (y23['mondays'], y23['low_iv'], y23['traded'], y23['iv_max']) == (2, 2, 0, 19.9)
    assert (y24['mondays'], y24['no_data'], y24['traded'], y24['wins'], y24['open'], y24['net']) == (5, 2, 2, 1, 1, 300.0)
