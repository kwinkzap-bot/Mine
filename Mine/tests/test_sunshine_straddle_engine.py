"""Sunshine Straddle (Backtest/sunshine_straddle_engine.py) on synthetic minutes.

Nothing here reaches a broker: option bars come from a fake fetch, index bars are built
by hand, and the route test patches the store and Breeze readers out.
"""
from datetime import date

import pytest

from trading_app.Backtest import sunshine_straddle_engine as ss


def _hm(m):
    return f'{m // 60:02d}:{m % 60:02d}'


def bars(start='09:15', end='15:29', price=100.0, moves=None):
    """Flat minute bars at `price`; moves = {HH:MM: (open, high, low, close)}."""
    out = []
    a, b = ss._mins(start), ss._mins(end)
    for m in range(a, b + 1):
        hm = _hm(m)
        o, h, l, c = (moves or {}).get(hm, (price, price, price, price))
        out.append({'time': hm, 'open': o, 'high': h, 'low': l, 'close': c})
    return out


P = {**ss.DEFAULTS, 'taxes': False, 'brokerage': 0.0}


def fake_fetch(table):
    """table: {(strike, type): bars}; anything else is a flat ₹2 wing."""
    def fetch(con, day):
        return table.get((con['strike'], con['option_type']), bars(price=2.0))
    return fetch


SPOT = ss.index_bars(bars(price=25010.0))       # ATM 25000 on NIFTY's 50 step


def test_a_stopped_leg_closes_alone_at_its_level_and_the_other_rides_to_the_exit():
    ce = bars(price=100.0, moves={'09:40': (101, 160, 100, 150)})       # 150 = +50 % of 100
    pe = bars(price=80.0, moves={'10:15': (80, 80, 60, 60)})
    row = ss.run_window(date(2026, 11, 2), 'NIFTY', date(2026, 11, 3), '09:22', '10:16', SPOT,
                        fake_fetch({(25000, 'CE'): ce, (25000, 'PE'): pe}), P, False, 0, 1)
    assert row['strike'] == 25000 and row['credit'] == 180
    assert row['ce_exit'] == 150 and row['reason'] == 'CE stopped'
    ce_leg = next(g for g in row['legs'] if g['name'] == 'CE')
    assert ce_leg['exit_time'] == '09:41'
    # PE exits at the close of the 10:15 minute (the price "at" 10:16).
    assert row['pe_exit'] == 60
    assert row['points'] == pytest.approx((100 - 150) + (80 - 60))
    assert row['gross'] == pytest.approx(row['points'] * 65)


def test_a_gap_through_the_stop_fills_at_the_open():
    ce = bars(price=100.0, moves={'09:30': (170, 175, 165, 170)})
    row = ss.run_window(date(2026, 11, 2), 'NIFTY', date(2026, 11, 3), '09:22', '10:16', SPOT,
                        fake_fetch({(25000, 'CE'): ce, (25000, 'PE'): bars(price=100.0)}), P, False, 0, 1)
    assert row['ce_exit'] == 170 and row['legs'][0]['why'] == 'Stop (gap)'


def test_entry_is_the_close_of_the_minute_before_and_a_minute_at_entry_counts_for_the_stop():
    ce = bars(price=100.0, moves={'09:21': (100, 100, 100, 90), '09:22': (90, 140, 90, 120)})
    row = ss.run_window(date(2026, 11, 2), 'NIFTY', date(2026, 11, 3), '09:22', '10:16', SPOT,
                        fake_fetch({(25000, 'CE'): ce, (25000, 'PE'): bars(price=100.0)}), P, False, 0, 1)
    assert row['ce_entry'] == 90 and row['ce_exit'] == 135 and row['reason'] == 'CE stopped'


def test_hedges_are_bought_a_minute_early_at_the_strikes_out_and_count_in_the_points():
    wing_ce = bars(price=5.0, moves={'09:20': (5, 5, 5, 6)})
    row = ss.run_window(date(2026, 11, 2), 'NIFTY', date(2026, 11, 3), '09:22', '10:16', SPOT,
                        fake_fetch({(25000, 'CE'): bars(price=100.0), (25000, 'PE'): bars(price=100.0),
                                    (26000, 'CE'): wing_ce, (24000, 'PE'): bars(price=4.0)}), P, False, 20, 1)
    hedges = {g['name']: g for g in row['legs'] if g['side'] == 'buy'}
    assert hedges['Hedge CE']['strike'] == 26000 and hedges['Hedge PE']['strike'] == 24000
    assert hedges['Hedge CE']['entry'] == 6 and row['hedge_cost'] == 10
    assert row['points'] == pytest.approx((5 - 6) + (4 - 4))


def test_lock_level_follows_the_video():
    p = {'trail_at': 2000, 'trail_lock': 750, 'trail_step': 20, 'trail_by': 10}
    assert ss.lock_level(1999, p) is None
    assert ss.lock_level(2000, p) == 750
    assert ss.lock_level(2019, p) == 750
    assert ss.lock_level(2020, p) == 760
    assert ss.lock_level(2040, p) == 770


def test_profit_lock_closes_every_leg_at_the_first_close_at_or_under_the_lock():
    # SENSEX lot 20: 100 points of credit decay = ₹2,000 a lot. Peak 110 pts → ₹2,200,
    # lock 750 + 10×10 = ₹850 = 42.5 pts. Falls back to 40 pts of profit at 10:00.
    spot = ss.index_bars(bars(price=82020.0))
    ce = bars(price=300.0, moves={**{_hm(m): (300, 300, 190, 190) for m in range(ss._mins('09:40'), ss._mins('10:00'))},
                                  **{_hm(m): (260, 260, 260, 260) for m in range(ss._mins('10:00'), ss._mins('10:30'))}})
    pe = bars(price=300.0, moves={_hm(m): (300, 300, 300, 300) for m in range(ss._mins('09:15'), ss._mins('15:30'))})
    row = ss.run_window(date(2026, 11, 12), 'SENSEX', date(2026, 11, 12), '09:22', '10:30', spot,
                        fake_fetch({(82000, 'CE'): ce, (82000, 'PE'): pe}), P, True, 0, 1)
    assert row['reason'] == 'Profit lock'
    assert row['peak_lot'] == 2200 and row['lock_lot'] == 850
    assert row['ce_exit'] == 260 and row['legs'][0]['exit_time'] == '10:01'
    assert row['gross'] == pytest.approx(40 * 20)


def test_charges_count_every_order_and_the_taxes():
    legs = [{'side': 'sell', 'entry': 100.0, 'exit': 50.0}, {'side': 'buy', 'entry': 5.0, 'exit': 2.0}]
    flat = ss.charges(legs, 'NIFTY', 1, date(2026, 1, 5), {**P, 'brokerage': 20.0, 'taxes': False})
    assert flat == 80.0
    taxed = ss.charges(legs, 'NIFTY', 1, date(2026, 1, 5), {**P, 'brokerage': 20.0, 'taxes': True})
    stt = (100 + 2) * 65 * 0.001
    assert taxed > flat + stt


def test_plan_days_fixed_weekdays_and_expiry_days():
    sessions = [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9, 11, 12, 13)]
    exp = {'NIFTY': {d: date(2026, 11, 3) if d <= date(2026, 11, 3) else date(2026, 11, 9) for d in sessions},
           'SENSEX': {d: date(2026, 11, 5) if d <= date(2026, 11, 5) else date(2026, 11, 12) for d in sessions}}
    ef = lambda idx, d: exp[idx].get(d)
    plan = ss.plan_days(sessions, sessions[0], sessions[-1], {**P, 'nf_on': True, 'sx_on': True}, ef)
    assert [(d.day, i) for d, i, _ in plan] == [(2, 'NIFTY'), (3, 'NIFTY'), (4, 'SENSEX'), (5, 'SENSEX'),
                                                (9, 'NIFTY'), (11, 'SENSEX'), (12, 'SENSEX')]
    dte = ss.plan_days(sessions, sessions[0], sessions[-1], {**P, 'schedule': 'dte'}, ef)
    # 11-09 is NIFTY's expiry (11-10 a holiday): it and the session before (11-06) trade.
    assert (date(2026, 11, 6), 'NIFTY') in [(d, i) for d, i, _ in dte]
    assert (date(2026, 11, 9), 'NIFTY') in [(d, i) for d, i, _ in dte]
    assert all(i != 'SENSEX' or d.day in (4, 5, 11, 12) for d, i, _ in dte)
    assert ss.parse_days('Mon, tue,xyz') == (0, 1)


def test_simulate_skips_a_window_it_cannot_price_and_summarise_adds_up():
    sessions = [date(2026, 11, 2), date(2026, 11, 3)]
    plan = [(sessions[0], 'NIFTY', sessions[1]), (sessions[1], 'NIFTY', sessions[1])]

    def fetch(con, day):
        if day == sessions[1] and con['option_type'] == 'PE':
            return []
        return bars(price=100.0, moves={'10:15': (100, 100, 80, 80)})

    res = ss.simulate(plan, sessions, lambda idx, d: SPOT, lambda idx: fetch,
                      {**P, 'nf_hedge_strikes': 0, 'nf_pm_in': ''})
    assert len(res['trades']) == 1 and len(res['skipped']) == 1
    t = res['trades'][0]
    assert t['dte'] == 1 and t['window'] == 'AM' and t['pnl_rupees'] == pytest.approx(40 * 65)
    s = ss.summarise(res['trades'], 300000, sessions[0], sessions[1])
    assert s['total_trades'] == 1 and s['total_pnl_rupees'] == pytest.approx(2600)
    assert s['by_window'][0]['window'] == 'NIFTY AM' and s['by_year'][0]['nifty'] == pytest.approx(2600)


def test_route_runs_end_to_end_on_patched_data(monkeypatch):
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    from trading_app.service import cpr_option_service as cos
    from trading_app.service import icici_data_service as ids
    import trading_app.Backtest.st_bb_rsi_engine as sbr

    days = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06']
    spot = {'NIFTY': bars(price=25010.0), 'SENSEX': bars(price=82020.0)}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(cos, '_spot_minutes', lambda idx, a, b: {d: spot[idx] for d in days})
    monkeypatch.setattr(sbr, '_listed_expiries', lambda *a, **k: [])
    monkeypatch.setattr(ids, 'cached_option_minutes', lambda *a, **k: None)
    calls = []

    def live(con, day):
        calls.append(con)
        return bars(price=100.0)

    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda idx: live)
    app = build_route_app()
    body = {'start_date': '2026-11-02', 'end_date': '2026-11-06', 'sx_hedge_strikes': 0}
    with app.test_request_context(json=body):
        task_id = api_mod.run_sunshine_straddle_backtest_api.__wrapped__().get_json()['task_id']
    for _ in range(300):
        task = api_mod._ss_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    s = task['payload']['summary']
    # Mon + Tue NIFTY, Wed + Thu SENSEX, two windows each.
    assert s['total_trades'] == 8 and s['days_planned'] == 4
    assert {(t['index'], t['window']) for t in task['payload']['trades']} == {
        ('NIFTY', 'AM'), ('NIFTY', 'PM'), ('SENSEX', 'AM'), ('SENSEX', 'PM')}
    assert any(c['strike'] == 26000 for c in calls)          # NIFTY hedges 20 strikes out
    with app.test_request_context(json={**body, 'nf_am_in': '9.22'}):
        assert api_mod.run_sunshine_straddle_backtest_api.__wrapped__()[1] == 400


# ── Find Best Params ─────────────────────────────────────────────────────────

SMALL_GRID = {'sl_pct': [0.0, 30.0, 50.0], 'am_out': ['10:00', '10:16'], 'pm_out': ['15:00', '15:14'],
              'lock': [(1000.0, 400.0), (2000.0, 750.0)]}


def _market():
    """Eight sessions of moving premiums: rises that trip some stops, decays that arm locks."""
    import random
    rng = random.Random(7)
    sessions = [date(2026, 10, d) for d in (5, 6, 7, 8, 12, 13, 14, 15)]
    plan = []
    for d in sessions:
        if d.weekday() in (0, 1):
            plan.append((d, 'NIFTY', sessions[sessions.index(d) + (1 - d.weekday())]))
        elif d.weekday() in (2, 3):
            plan.append((d, 'SENSEX', sessions[sessions.index(d) + (3 - d.weekday())]))
    paths = {}

    def fetch(con, day):
        k = (con['strike'], con['option_type'], con['expiry'], day)
        if k not in paths:
            px, out = 100.0 if abs(con['strike'] % 1000) < 1 or True else 5.0, []
            if con['strike'] not in (25000, 82000):
                px = 3.0
            for m in range(ss._mins('09:15'), ss._mins('15:30')):
                o = px
                px = max(0.5, px * (1 + rng.gauss(-0.002, 0.03)))
                out.append({'time': _hm(m), 'open': o, 'high': max(o, px) * (1 + abs(rng.gauss(0, 0.01))),
                            'low': min(o, px), 'close': px})
            paths[k] = out
        return paths[k]

    spot = {'NIFTY': ss.index_bars(bars(price=25010.0)), 'SENSEX': ss.index_bars(bars(price=82020.0))}
    return sessions, plan, (lambda idx, d: spot[idx]), (lambda idx: fetch)


def _row_params(r):
    p = {**ss.DEFAULTS, 'taxes': True, 'sl_pct': r['sl_pct'], 'nf_trail': r['nf_trail'], 'sx_trail': r['sx_trail']}
    if r['trail_at'] is not None:
        p['trail_at'], p['trail_lock'] = r['trail_at'], r['trail_lock']
    for w in ('nf_am', 'nf_pm', 'sx_am', 'sx_pm'):
        a, b = (r[w].split('-') if r[w] else ('', ''))
        p[f'{w}_in'], p[f'{w}_out'] = a, b
    return p


def test_every_sweep_row_equals_a_direct_backtest_of_its_own_settings():
    sessions, plan, spot_for, fetch_for = _market()
    base = {**ss.DEFAULTS, 'taxes': True}
    res = ss.sweep(plan, sessions, spot_for, fetch_for, base, grid=SMALL_GRID)
    rows = res['rows']
    assert len(rows) == len(ss.sweep_combos(SMALL_GRID)) and res['unpriced'] == 0
    assert len({r['net_pnl_rupees'] for r in rows}) > 50          # the market really moves the combos apart
    for r in rows[::97]:
        p = _row_params(r)
        sim = ss.simulate(plan, sessions, spot_for, fetch_for, p)
        s = ss.summarise(sim['trades'], 300000, sessions[0], sessions[-1])
        assert s['total_trades'] == r['total_trades']
        assert s['total_pnl_rupees'] == pytest.approx(r['net_pnl_rupees'], abs=0.05)
        assert s['max_drawdown_rupees'] == pytest.approx(r['max_drawdown_rupees'], abs=0.05)
        assert s['wins'] == r['wins'] and s['losing_months'] == r['losing_months']
    b = ss.board(rows, min_trades=1, size=5)
    assert b and len(b) <= 15
    nd = [x['net_dd'] for x in b if x['net_dd'] is not None]
    assert nd == sorted(nd, reverse=True)


def test_combos_do_not_multiply_settings_nothing_uses():
    combos = ss.sweep_combos(SMALL_GRID)
    keys = {(c['sl_pct'], c['exits'], c['nf_trail'], c['sx_trail'], c['lock']) for c in combos}
    assert len(keys) == len(combos)
    assert all(c['lock'] is None for c in combos if not (c['nf_trail'] or c['sx_trail']))
    assert not any(c['nf_trail'] for c in combos if c['exits'][0] is None and c['exits'][1] is None)
    assert len(ss.sweep_combos()) == 34181


def test_optimise_route_reads_only_the_cache(monkeypatch):
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    from trading_app.service import cpr_option_service as cos
    from trading_app.service import icici_data_service as ids
    import trading_app.Backtest.st_bb_rsi_engine as sbr

    days = ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08']
    spot = {'NIFTY': bars(price=25010.0), 'SENSEX': bars(price=82020.0)}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(cos, '_spot_minutes', lambda idx, a, b: {d: spot[idx] for d in days})
    monkeypatch.setattr(sbr, '_listed_expiries', lambda *a, **k: [])
    # NIFTY cached, SENSEX not: SENSEX windows are left out, not fetched.
    monkeypatch.setattr(ids, 'cached_option_minutes',
                        lambda root, expiry, strike, right, day: (
                            [dict(b, date=b['date'].replace(year=day.year, month=day.month, day=day.day))
                             for b in [{'date': __import__('datetime').datetime(2026, 10, 5, 9, 15) +
                                        __import__('datetime').timedelta(minutes=m),
                                        'open': 100, 'high': 100, 'low': 100, 'close': 100 - m * 0.1}
                                       for m in range(375)]]
                            if root == 'NIFTY' else None))
    calls = []
    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda idx: (lambda con, day: calls.append(con) or []))
    monkeypatch.setattr(ss.sweep, '__defaults__', (SMALL_GRID, None))     # the small grid, as the other route tests shrink theirs
    app = build_route_app()
    body = {'start_date': '2026-10-05', 'end_date': '2026-10-08', 'min_trades': 1}
    with app.test_request_context(json=body):
        task_id = api_mod.run_sunshine_straddle_optimise.__wrapped__().get_json()['task_id']
    for _ in range(500):
        task = api_mod._ss_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    out = task['payload']
    assert calls == []                                   # never a Breeze request
    assert out['windows'] == 4 and out['unpriced'] == 4   # NIFTY Mon/Tue × AM/PM; SENSEX not cached
    assert out['results'] and all(r['sx_am'] == '' and r['sx_pm'] == '' for r in out['results'])
