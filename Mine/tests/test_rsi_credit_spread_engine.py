"""NIFTY RSI(9) credit-spread rule — Backtest/rsi_credit_spread_engine.py."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import rsi_credit_spread_engine as rcs
from trading_app.Backtest.second_candle_engine import _bs_delta, _bs_price

DAY = date(2026, 10, 16)
EXPIRY = date(2026, 10, 20)
SPOT = 24000.0
IV = 0.15


def ts(hm):
    return pd.Timestamp(f'{DAY} {hm}')


def day_rsi(values):
    """RSI per 15-minute candle from 09:15, in order."""
    return [(ts('09:15') + i * rcs.CANDLE, float(v)) for i, v in enumerate(values)]


def bs(strike, right, t=None):
    t = rcs.years_to_expiry(ts('09:30'), EXPIRY) if t is None else t
    return _bs_price('c' if right == 'CE' else 'p', SPOT, strike, t, rcs.RISK_FREE, IV)


def flat_bars(price):
    idx = pd.date_range(ts('09:15'), ts('15:29'), freq='1min')
    return pd.DataFrame({'open': price, 'high': price, 'low': price, 'close': price}, index=idx)


def chain(overrides=None):
    """Every strike flat at its 09:30 Black-Scholes price, bar the overridden ones."""
    overrides = overrides or {}
    cache = {}

    def bars_for(strike, right):
        key = (strike, right)
        if key not in cache:
            cache[key] = overrides.get(key, flat_bars(round(bs(strike, right), 2)))
        return cache[key]
    return bars_for


def short_strike(right):
    t = rcs.years_to_expiry(ts('09:30'), EXPIRY)
    return rcs.pick_short_strike(SPOT, t, right, lambda k: bs(k, right))['strike']


# ── signal ──
def test_crossings_and_prev_day_bias():
    d = rcs.decisions(day_rsi([55, 62, 58, 45, 38]), prev_rsi=50)
    assert [x['signal'] for x in d] == [None, 'bull', None, None, 'bear']
    assert d[1]['ts'] == ts('09:45')                    # acted on at the candle's close
    # No crossing on the first candle: yesterday's last RSI decides.
    assert rcs.decisions(day_rsi([55]), prev_rsi=65)[0]['signal'] == 'bull'
    assert rcs.decisions(day_rsi([55]), prev_rsi=35)[0]['signal'] == 'bear'
    assert rcs.decisions(day_rsi([55]), prev_rsi=50)[0]['signal'] is None
    assert rcs.decisions(day_rsi([55]), prev_rsi=65, prev_day_bias=False)[0]['signal'] is None
    # A first candle that crosses against yesterday goes with the crossing.
    assert rcs.decisions(day_rsi([35]), prev_rsi=65)[0]['signal'] == 'bear'


def test_candles_15m_and_rsi():
    idx = pd.date_range('2026-10-16 09:15', '2026-10-16 15:29', freq='1min')
    px = np.linspace(100, 200, len(idx))
    c = rcs.candles_15m(pd.DataFrame({'open': px, 'high': px + 1, 'low': px - 1, 'close': px}, index=idx))
    assert len(c) == 25 and c.index[0] == ts('09:15') and c.index[-1] == ts('15:15')
    assert c['open'].iloc[0] == px[0] and c['close'].iloc[0] == px[14]
    r = rcs.with_rsi(c, 9)['rsi']
    assert r.iloc[:9].isna().all() and r.iloc[9] == 100.0        # a straight rally


# ── option maths ──
def test_implied_vol_round_trip():
    t = 3 / 365
    for right, k in (('CE', 24300), ('PE', 23700)):
        p = _bs_price('c' if right == 'CE' else 'p', SPOT, k, t, rcs.RISK_FREE, 0.17)
        assert rcs.implied_vol(p, right, SPOT, k, t) == pytest.approx(0.17, abs=1e-4)
    assert rcs.implied_vol(0.0, 'CE', SPOT, 24300, t) is None


@pytest.mark.parametrize('right', ['CE', 'PE'])
def test_picks_the_strike_nearest_20_delta(right):
    t = rcs.years_to_expiry(ts('09:30'), EXPIRY)
    asked = []

    def price_of(k):
        asked.append(k)
        return bs(k, right, t)
    got = rcs.pick_short_strike(SPOT, t, right, price_of, 0.20)
    flag = 'c' if right == 'CE' else 'p'
    otm = range(24000, 25500, 50) if right == 'CE' else range(22500, 24050, 50)
    best = min(otm, key=lambda k: abs(abs(_bs_delta(flag, SPOT, k, t, rcs.RISK_FREE, IV)) - 0.20))
    assert got['strike'] == best
    assert got['delta'] == pytest.approx(0.20, abs=0.05) and got['iv'] == pytest.approx(IV, abs=1e-3)
    assert len(set(asked)) <= 5                         # a few contracts, not the chain


# ── one session ──
def test_put_spread_from_prev_day_bias_held_to_1515():
    res = rcs.run_day(DAY, day_rsi([55] * 25), 65, lambda t: SPOT, EXPIRY, chain())
    (t,) = res['trades']
    k = short_strike('PE')
    assert t['side'] == 'bull' and t['right'] == 'PE' and t['short_strike'] == k
    assert t['hedge_strike'] == k - 1000
    assert t['entry_time'] == ts('09:30') and t['exit_time'] == ts('15:15')
    assert t['reason'] == 'Time exit' and t['points'] == pytest.approx(0, abs=1e-9)


def test_stop_closes_the_spread_then_one_reentry_at_cost():
    k = short_strike('PE')
    p = round(bs(k, 'PE'), 2)
    b = flat_bars(p)
    b.loc[ts('10:00'), ['open', 'high', 'close']] = [p, p * 1.35, p * 1.3]   # stop at 1.3×
    b.loc[ts('10:01'):ts('10:19'), ['open', 'high', 'low', 'close']] = p * 1.2
    b.loc[ts('10:20'), ['open', 'low']] = [p * 1.1, p * 0.98]                 # back to cost
    b.loc[ts('10:21'):, ['open', 'high', 'low', 'close']] = p * 0.8
    res = rcs.run_day(DAY, day_rsi([55] * 25), 65, lambda t: SPOT, EXPIRY, chain({(k, 'PE'): b}))
    first, second = res['trades']
    assert first['reason'] == 'SL hit' and first['exit_time'] == ts('10:00')
    assert first['short_exit'] == pytest.approx(p * 1.3)
    assert second['reentry'] and second['entry_time'] == ts('10:20')
    assert second['short_entry'] == pytest.approx(p) and second['reason'] == 'Time exit'
    assert second['short_exit'] == pytest.approx(p * 0.8)


def test_no_reentry_when_switched_off():
    k = short_strike('PE')
    p = round(bs(k, 'PE'), 2)
    b = flat_bars(p)
    b.loc[ts('10:00'), 'high'] = p * 1.5
    res = rcs.run_day(DAY, day_rsi([55] * 25), 65, lambda t: SPOT, EXPIRY, chain({(k, 'PE'): b}),
                      reentry=False)
    assert [t['reason'] for t in res['trades']] == ['SL hit']


def test_far_band_reverses_into_a_call_spread():
    # 09:15 crosses 60 (bull at 09:30); the 10:00 candle closes under 40 → reverse at 10:15.
    res = rcs.run_day(DAY, day_rsi([62, 58, 50, 38] + [45] * 21), 50, lambda t: SPOT, EXPIRY, chain())
    put, call = res['trades']
    assert put['side'] == 'bull' and put['reason'] == 'RSI reversal' and put['exit_time'] == ts('10:15')
    assert call['side'] == 'bear' and call['right'] == 'CE' and call['entry_time'] == ts('10:15')
    assert call['hedge_strike'] == call['short_strike'] + 1000 and call['reason'] == 'Time exit'


def test_no_new_spread_after_1445():
    rsi = [50] * 25
    rsi[22] = 65                       # the 14:45 candle closes above 60 → 15:00, too late
    assert rcs.run_day(DAY, day_rsi(rsi), 50, lambda t: SPOT, EXPIRY, chain())['trades'] == []
    rsi = [50] * 25
    rsi[21] = 65                       # the 14:30 candle → 14:45, still allowed
    (t,) = rcs.run_day(DAY, day_rsi(rsi), 50, lambda t: SPOT, EXPIRY, chain())['trades']
    assert t['entry_time'] == ts('14:45')


def test_unpriced_signal_is_skipped_not_traded():
    res = rcs.run_day(DAY, day_rsi([55] * 25), 65, lambda t: SPOT, EXPIRY, lambda k, r: None)
    assert res['trades'] == [] and len(res['skipped']) == 1


def test_rows_and_summary():
    res = rcs.run_day(DAY, day_rsi([62, 58, 50, 38] + [45] * 21), 50, lambda t: SPOT, EXPIRY, chain())
    rows = [rcs.trade_row(t, 65, 1, 65, 100) for t in res['trades']]
    assert rows[0]['side'] == 'Bull put' and rows[0]['brokerage'] == 200
    s = rcs.summarise(rows)
    assert s['total_trades'] == 2 and s['reversals'] == 1 and s['time_exits'] == 1
    assert s['by_year'][0]['year'] == '2026' and s['by_year'][0]['trades'] == 2
    assert s['total_pnl_rupees'] == pytest.approx(-400)


def test_month_table_sums_net_by_month():
    def row(d, net):
        return {'date': d, 'pnl_rupees': net, 'gross_pnl_rupees': net, 'brokerage': 0, 'pnl': net,
                'exit_reason': 'Time exit', 'reentry': False, 'side': 'Bull put', 'short_delta': 0.2}
    s = rcs.summarise([row('2025-01-02', 100), row('2025-01-09', -40), row('2025-03-04', 5),
                       row('2026-02-02', 7)])
    assert s['by_month'] == [{'year': '2025', 'total': 65, 'm01': 60, 'm03': 5},
                             {'year': '2026', 'total': 7, 'm02': 7}]
