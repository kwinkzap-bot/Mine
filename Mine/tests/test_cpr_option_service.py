"""Trend page — the option leg behind each CPR Manual vs Chart trade.

Pure functions over bar lists with the fetchers injected, so no broker and
no create_app().
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.service import cpr_option_service as svc
from route_app import build_route_app


def _minutes(start_hm, closes, step=1.0):
    """1-minute bars from `start_hm` with the given closes; each bar's range
    straddles its close by `step` so a level inside it is touched."""
    h, m = map(int, start_hm.split(':'))
    out = []
    for i, c in enumerate(closes):
        mins = h * 60 + m + i
        out.append({'time': f'{mins // 60:02d}:{mins % 60:02d}', 'open': c, 'high': c + step,
                    'low': c - step, 'close': c})
    return out


# ── contract naming ───────────────────────────────────────────────────────

def test_contract_is_atm_to_entry_and_calls_for_buys():
    days = [date(2026, 9, 14) + __import__('datetime').timedelta(days=i) for i in range(10)]
    c = svc.contract('NIFTY', date(2026, 9, 17), 'BUY', 23224.0, days)
    assert c == {'strike': 23200, 'option_type': 'CE', 'expiry': '2026-09-22'}   # Tuesday weekly
    p = svc.contract('NIFTY', date(2026, 9, 17), 'SELL', 23176.0, days)
    assert p['strike'] == 23200 and p['option_type'] == 'PE'
    assert svc.contract('NIFTY', date(2026, 9, 17), None, 23224.0, days) is None


def test_minute_setup_ends_the_five_minute_candle():
    assert svc.minute_setup(None) == '09:19'
    assert svc.minute_setup('10:30') == '10:34'


def test_bar_at_takes_the_last_bar_on_or_before():
    bars = _minutes('09:15', [1, 2, 3])
    assert svc.bar_at(bars, '09:16')['close'] == 2
    assert svc.bar_at(bars, '09:30')['close'] == 3          # a missing minute falls back
    assert svc.bar_at(bars, '09:00') is None
    assert svc.bar_at(bars, None) is None


def test_day_delta_is_the_slope_and_signed():
    spot = _minutes('09:15', [100 + i for i in range(30)])
    ce = _minutes('09:15', [10 + 0.5 * i for i in range(30)])
    pe = _minutes('09:15', [10 - 0.4 * i for i in range(30)])
    assert svc.day_delta(spot, ce) == pytest.approx(0.5)
    assert svc.day_delta(spot, pe) == pytest.approx(-0.4)
    assert svc.day_delta(spot[:5], ce[:5]) is None          # too few minutes


# ── the leg ───────────────────────────────────────────────────────────────

def _buy():
    return {'trade': 'BUY', 'entry': 110.0, 'target': 130.0, 'sl': 100.0, 'setup_time': None}


def _con():
    return {'strike': 23200, 'option_type': 'CE', 'expiry': '2026-09-22'}


def test_leg_prices_entry_and_the_level_that_closed_it_and_estimates_the_other():
    # 09:15..09:19 setup, spot rises 100 -> 140 one point a minute from 09:20.
    spot = _minutes('09:15', [100] * 5 + [100 + i for i in range(41)], step=0.4)
    opt = _minutes('09:15', [50] * 5 + [50 + 0.5 * i for i in range(41)])
    leg = svc.option_leg(_buy(), _con(), spot, opt, lot=65)
    assert leg['result'] == 'Target'
    # entry at spot 110 = 09:30 -> option close 55; target at spot 130 = 09:50 -> 65
    assert leg['entry_time'] == '09:30' and leg['entry'] == 55.0
    assert leg['exit_time'] == '09:50' and leg['exit'] == 65.0 and leg['target'] == 65.0
    assert leg['delta'] == pytest.approx(0.5)
    assert leg['sl'] == pytest.approx(55 + 0.5 * (100 - 110)) and leg['estimated'] == ['sl']
    assert leg['pnl'] == 10.0 and leg['pnl_lot'] == 650.0


def test_leg_books_the_stop_at_its_minute():
    spot = _minutes('09:15', [120] * 5 + [120 - i for i in range(30)], step=0.4)   # falls through 110 then 100
    opt = _minutes('09:15', [60] * 5 + [60 - 0.5 * i for i in range(30)])
    leg = svc.option_leg(_buy(), _con(), spot, opt, lot=65)
    assert leg['result'] == 'SL' and leg['entry'] == 55.0 and leg['sl'] == 50.0
    assert leg['estimated'] == ['target'] and leg['pnl'] == -5.0


def test_leg_squares_off_at_the_1515_open():
    # Fills, then drifts: neither level reached by 15:15.
    n = (15 * 60 + 25) - (9 * 60 + 15) + 1
    spot = _minutes('09:15', [100] * 5 + [110 + (i % 3) for i in range(n - 5)], step=0.5)
    opt = _minutes('09:15', [50] * 5 + [55 + 0.2 * (i % 3) for i in range(n - 5)])
    leg = svc.option_leg(_buy(), _con(), spot, opt, lot=65)
    assert leg['result'] == 'EOD' and leg['exit_time'] == '15:15'
    sq = next(b for b in opt if b['time'] == '15:15')
    assert leg['exit'] == sq['open'] and sorted(leg['estimated']) == ['sl', 'target']


def test_leg_without_fill_or_bars_carries_no_prices():
    spot = _minutes('09:15', [100] * 40)                    # never reaches 110
    opt = _minutes('09:15', [50] * 40)
    leg = svc.option_leg(_buy(), _con(), spot, opt, lot=65)
    assert leg['result'] == 'No fill' and leg['entry'] is None and leg['pnl'] is None
    assert svc.option_leg(_buy(), _con(), spot, [], 65)['error'] == 'no option bars'
    assert svc.option_leg(_buy(), _con(), [], opt, 65)['error'] == 'no 1-minute index bars'


def test_legs_for_keys_by_date_and_tallies(monkeypatch):
    spot_day = _minutes('09:15', [100] * 5 + [100 + i for i in range(41)], step=0.4)
    opt_day = _minutes('09:15', [50] * 5 + [50 + 0.5 * i for i in range(41)])
    rows = [{'date': '2026-09-17', 'trades': [{'manual': _buy()}]},
            {'date': '2026-09-16', 'trades': [{'manual': _buy()}]}]
    days = [date(2026, 9, 14 + i) for i in range(10)]
    seen = []

    def fetch(con, day):
        seen.append((con['strike'], con['option_type'], con['expiry'], day))
        return opt_day if day == date(2026, 9, 17) else []

    out = svc.legs_for('NIFTY', rows, days, {'2026-09-17': spot_day, '2026-09-16': spot_day}, fetch)
    assert seen == [(100, 'CE', '2026-09-22', date(2026, 9, 17)), (100, 'CE', '2026-09-22', date(2026, 9, 16))]
    assert out['legs']['2026-09-17'][0]['pnl'] == 10.0
    assert out['legs']['2026-09-16'][0]['error'] == 'no option bars'
    assert out['summary'] == {'pnl': 10.0, 'pnl_lot': 650.0, 'lot': 65, 'trades': 1, 'wins': 1, 'missing': 1}


# ── picking a strike by premium ───────────────────────────────────────────

def _ladder(prices_by_strike, entry_time='09:30'):
    """A fetcher serving a flat premium per strike at every minute, and the
    list of strikes it was asked for."""
    asked = []

    def fetch(con, day):
        asked.append(con['strike'])
        px = prices_by_strike.get(con['strike'])
        return _minutes('09:15', [px] * 30) if px is not None else []
    return fetch, asked


def test_pick_strike_walks_itm_to_the_nearest_premium():
    # CALL ladder: ATM 23200 = 120, every 50 lower adds ~35.
    ladder = {23200: 120, 23150: 155, 23100: 190, 23050: 226, 23000: 262, 22950: 300, 22900: 340}
    fetch, asked = _ladder(ladder)
    con = {'strike': 23200, 'option_type': 'CE', 'expiry': '2026-09-22'}
    c, bars, looked = svc.pick_strike('NIFTY', con, date(2026, 9, 17), '09:30', 250, fetch)
    assert c['strike'] == 23000 and bars[0]['close'] == 262        # 262 is nearer 250 than 226
    assert looked == len(set(asked)) and looked <= 4                # one jump, then a step or two
    assert asked[0] == 23200                                        # the ATM first


def test_pick_strike_walks_otm_when_the_target_is_below_atm():
    ladder = {23200: 180, 23250: 150, 23300: 122, 23350: 98}
    fetch, asked = _ladder(ladder)
    con = {'strike': 23200, 'option_type': 'CE', 'expiry': '2026-09-22'}
    c, _, _ = svc.pick_strike('NIFTY', con, date(2026, 9, 17), '09:30', 150, fetch)
    assert c['strike'] == 23250


def test_pick_strike_goes_up_the_ladder_for_a_put():
    ladder = {23200: 130, 23250: 165, 23300: 200, 23350: 238}
    fetch, asked = _ladder(ladder)
    con = {'strike': 23200, 'option_type': 'PE', 'expiry': '2026-09-22'}
    c, _, _ = svc.pick_strike('NIFTY', con, date(2026, 9, 17), '09:30', 200, fetch)
    assert c['strike'] == 23300


def test_pick_strike_keeps_the_atm_when_nothing_priced():
    fetch, _ = _ladder({})
    con = {'strike': 23200, 'option_type': 'CE', 'expiry': '2026-09-22'}
    c, bars, looked = svc.pick_strike('NIFTY', con, date(2026, 9, 17), '09:30', 250, fetch)
    assert c == con and bars == [] and looked == 1


def test_legs_for_with_a_premium_uses_the_picked_strike():
    spot_day = _minutes('09:15', [100] * 5 + [100 + i for i in range(41)], step=0.4)
    rows = [{'date': '2026-09-17', 'trades': [{'manual': _buy()}]}]
    days = [date(2026, 9, 14 + i) for i in range(10)]
    ladder = {100: 50, 50: 85, 0: 120, -50: 155, -100: 190, -150: 226, -200: 262}   # entry 110 -> ATM 100

    def fetch(con, day):
        px = ladder.get(con['strike'])
        return _minutes('09:15', [px] * 50) if px is not None else []

    out = svc.legs_for('NIFTY', rows, days, {'2026-09-17': spot_day}, fetch, premium=250)
    leg = out['legs']['2026-09-17'][0]
    assert leg['atm'] == 100 and leg['strike'] == -200 and leg['entry'] == 262
    assert out['premium'] == 250


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def test_options_route_serves_the_legs(client, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, 'options', lambda symbol, premium=None: (calls.append(premium) or
                        {'success': True, 'symbol': symbol, 'legs': {}, 'premium': premium}))
    body = client.get('/api/trend/cpr-backtest/options?symbol=nifty').get_json()
    assert body['success'] and body['symbol'] == 'NIFTY' and body['premium'] is None
    body = client.get('/api/trend/cpr-backtest/options?symbol=nifty&premium=250').get_json()
    assert body['premium'] == 250.0 and calls == [None, 250.0]
    assert client.get('/api/trend/cpr-backtest/options?premium=175').status_code == 400
    assert client.get('/api/trend/cpr-backtest/options?premium=abc').status_code == 400


def test_options_route_401s_without_breeze(client, monkeypatch):
    def boom(symbol, premium=None):
        raise svc.OptionDataUnavailable('login')
    monkeypatch.setattr(svc, 'options', boom)
    r = client.get('/api/trend/cpr-backtest/options')
    assert r.status_code == 401 and r.get_json()['icici_required'] is True
