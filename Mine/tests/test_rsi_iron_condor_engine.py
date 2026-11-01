"""NIFTY RSI(14) iron condor / 1:2:1 backtest (Backtest/rsi_iron_condor_engine.py).

Premiums come from Black-Scholes on a scripted spot path, and the RSI column is
set by hand, so each test drives exactly the branch it names."""
from datetime import date

import pandas as pd
import pytest

from trading_app.Backtest import rsi_iron_condor_engine as ric
from trading_app.Backtest.second_candle_engine import _bs_price

EXPIRIES = [date(2025, 1, 30), date(2025, 2, 27), date(2025, 3, 27)]
VOL = 0.15


def expiry_after(d):
    return next((e for e in EXPIRIES if e > d), None)


def frame(spots, rsis, start='2025-01-01'):
    idx = pd.bdate_range(start, periods=len(spots))
    return pd.DataFrame({'close': spots, 'rsi': rsis}, index=idx)


def pricer(daily):
    spot = {ts.date(): float(c) for ts, c in daily['close'].items()}

    def price_on(expiry, strike, right, day):
        t = max((expiry - day).days, 0) / 365 + 0.3 / 365
        return float(_bs_price('c' if right == 'CE' else 'p', spot[day], strike, t, 0.065, VOL))
    return price_on


def run(daily, **kw):
    kw.setdefault('brokerage_per_leg', 0.0)
    kw.setdefault('slippage', 0.0)
    return ric.run(daily, daily.index[0].date(), daily.index[-1].date(), expiry_after,
                   pricer(daily), **kw)


def test_zone_of():
    assert ric.zone_of(61) == 'BULL'
    assert ric.zone_of(39.9) == 'BEAR'
    assert ric.zone_of(60) == ric.zone_of(40) == 'IC'


def test_condor_is_the_farthest_one_still_paying_one_to_one():
    day, exp = date(2025, 1, 1), date(2025, 1, 30)
    t = (exp - day).days / 365
    price = lambda k, right: float(_bs_price('c' if right == 'CE' else 'p', 23500, k, t, 0.065, VOL))
    c = ric.pick_condor(23500, 200, 1.0, price, slip=0)
    assert c['credit'] >= 200 - c['credit']                    # 1:1 holds…
    o = (c['offset'] + 1) * 50
    nxt = (price(23500 - o, 'PE') - price(23500 - o - 200, 'PE')
           + price(23500 + o, 'CE') - price(23500 + o + 200, 'CE'))
    assert nxt < 200 - nxt                                     # …and one strike further it does not
    assert c['pb'] == c['ps'] - 200 and c['cb'] == c['cs'] + 200


def test_sideways_month_holds_the_condor_to_expiry_then_rolls_into_the_next():
    n = 30
    daily = frame([23500.0] * n, [50.0] * n)
    res = run(daily)
    first = res['trades'][0]
    assert first['kind'] == 'IC' and first['signal'] == 'Start'
    assert first['exit_reason'] == 'Expiry' and first['exit_date'] == '2025-01-30'
    assert first['pnl'] == pytest.approx(first['net_premium'])     # pinned inside: the whole credit
    assert res['cycles'][0]['expiry'] == '2025-01-30' and res['cycles'][0]['complete']
    nxt = res['trades'][1]
    assert nxt['entry_date'] == '2025-01-30' and nxt['expiry'] == '2025-02-27'
    assert nxt['exit_reason'] == 'Open (marked)' and not res['cycles'][-1]['complete']


def test_rsi_under_40_turns_the_condor_into_a_bearish_121():
    spots = [23500.0] * 5 + [23400.0] * 5
    rsis = [50.0] * 5 + [38.0] * 5
    res = run(frame(spots, rsis), book_pct=100, month_sl_pct=0)
    ic, bear = res['trades'][0], res['trades'][1]
    assert ic['exit_reason'] == 'RSI < 40' and ic['exit_date'] == '2025-01-08'
    assert bear['kind'] == 'BEAR' and bear['entry_date'] == '2025-01-08'
    assert bear['legs'] == '+1 23350PE  -2 23200PE  +1 23150PE'      # ATM 23400 −50 / −200 / −250


def test_121_booked_at_its_target_sits_out_until_expiry():
    # Bearish from day one, then the index walks onto the sold strike. A monthly
    # 1:2:1 keeps most of its time value until the last days, so the 80 % default
    # books late; 20 % makes the test reach it.
    spots = [23500.0, 23450, 23400, 23350, 23300] + [23300.0] * 15
    res = run(frame(spots, [35.0] * 20), month_sl_pct=0, book_pct=20)
    t = res['trades'][0]
    assert t['kind'] == 'BEAR' and t['exit_reason'] == 'Booked 20%'
    assert t['pnl'] >= 0.2 * t['max_profit']
    assert len(res['trades']) == 1                                 # nothing more before 30 Jan
    assert res['cycles'][-1]['ended'] == 'booked'


def test_back_in_band_closes_the_121_and_opens_a_condor():
    rsis = [65.0] * 3 + [55.0] * 3
    res = run(frame([23500.0] * 6, rsis), book_pct=100, month_sl_pct=0)
    assert [t['kind'] for t in res['trades']] == ['BULL', 'IC']
    assert res['trades'][0]['exit_reason'] == 'RSI back in band'


def test_breakeven_breach_with_rsi_inside_rolls_a_new_condor():
    spots = [23500.0] * 3 + [26000.0] * 3
    res = run(frame(spots, [50.0] * 6), month_sl_pct=0)
    first, roll = res['trades'][0], res['trades'][1]
    assert first['exit_reason'] == 'Breakeven breached' and first['pnl'] < 0
    assert roll['kind'] == 'IC' and roll['signal'] == 'Roll' and roll['spot_in'] == 26000.0
    assert roll['be_lo'] < 26000 < roll['be_hi']


def test_month_stop_flattens_and_waits_for_the_expiry():
    spots = [23500.0] * 3 + [26000.0] * 10
    res = run(frame(spots, [50.0] * 13), roll_on_breakeven=False, month_sl_pct=1.0,
              capital_per_lot=110000, lot_size=65)
    t = res['trades'][0]
    assert t['exit_reason'] == 'Month stop'
    assert len(res['trades']) == 1 and res['cycles'][-1]['ended'] == 'month stop'


def test_expiry_settles_at_intrinsic():
    spots = [23500.0] * 21 + [23000.0]                       # 30 Jan closes 500 lower
    rsis = [50.0] * 22
    res = run(frame(spots, rsis), roll_on_breakeven=False, month_sl_pct=0)
    t = res['trades'][0]
    assert t['exit_date'] == '2025-01-30' and t['exit_reason'] == 'Expiry'
    legs = t['legs'].split('  ')
    exits = [float(x) for x in t['exits'].split('  ')]
    for leg, px in zip(legs, exits):
        q, kr = leg.split(' ')
        k, right = int(kr[:-2]), kr[-2:]
        assert px == pytest.approx(ric.intrinsic(right, k, 23000.0))


def test_costs_and_rupees():
    res = run(frame([23500.0] * 3, [50.0] * 3), brokerage_per_leg=100.0, slippage=1.0,
              lot_size=65, lots=2)
    t = res['trades'][0]
    assert t['brokerage'] == 400.0 and t['qty'] == 130
    assert t['pnl_rupees'] == pytest.approx(t["pnl"] * 130 - 400, abs=1.0)


def test_summary_tables():
    res = run(frame([23500.0] * 45, [50.0] * 45))
    s = ric.summarise(res['trades'], res['cycles'], 110000)
    assert s['cycles'] == 3 and s['iron_condors'] == len(res['trades'])
    assert s['by_month_pct'][0]['year'] == '2025' and 'm01' in s['by_month_pct'][0]
    assert s['by_year'][0]['cycles'] == 3
