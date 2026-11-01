"""NIFTY short-strangle rule — Backtest/option_selling_engine.py."""
from datetime import date, datetime

import pandas as pd
import pytest

from trading_app.Backtest.option_selling_engine import (
    atm_strike, run_day, simulate_leg, strikes_for, summarise, trade_row)

DAY = '2026-10-16'
ENTRY = pd.Timestamp(f'{DAY} 09:30')
EXIT = pd.Timestamp(f'{DAY} 10:30')


def bars(rows):
    """rows: {'HH:MM': (open, high, low, close)}."""
    idx = pd.to_datetime([f'{DAY} {k}' for k in rows])
    return pd.DataFrame(list(rows.values()), index=idx, columns=['open', 'high', 'low', 'close'])


def flat(price, start=(9, 30), n=60):
    out = {}
    for i in range(n):
        m = start[0] * 60 + start[1] + i
        out[f'{m // 60:02d}:{m % 60:02d}'] = (price, price, price, price)
    return out


def test_time_exit_when_stop_never_hit():
    b = bars({**flat(100), '10:30': (80, 80, 80, 80)})
    r = simulate_leg(b, ENTRY, EXIT, 60)
    f = r['fills']
    assert len(f) == 1 and f[0]['reason'] == 'Time exit'
    assert f[0]['entry'] == 100 and f[0]['exit'] == 80
    assert r['pnl'] == 20 and r['stops'] == 0 and not r['reentered']


def test_stop_at_160_percent_and_reentry_at_cost():
    rows = flat(100)
    rows['09:40'] = (100, 165, 100, 160)      # stop 160 hit
    rows['09:41'] = (160, 160, 150, 150)
    # the flat-100 filler is already at cost from 09:42 → re-sold there
    rows['10:30'] = (70, 70, 70, 70)
    r = simulate_leg(bars(rows), ENTRY, EXIT, 60)
    a, c = r['fills']
    assert (a['entry'], a['exit'], a['reason']) == (100, 160, 'SL hit')
    assert (c['entry'], c['exit'], c['reason']) == (100, 70, 'Time exit')
    assert c['entry_time'] == pd.Timestamp(f'{DAY} 09:42')
    assert r['pnl'] == -60 + 30 and r['stops'] == 1 and r['reentered']


def test_reentry_is_only_once():
    rows = flat(100)
    rows['09:40'] = (100, 170, 100, 160)
    rows['09:45'] = (100, 100, 100, 100)     # re-sold at 100
    rows['09:50'] = (100, 165, 100, 160)     # stopped again at 160
    rows['09:55'] = (100, 100, 90, 90)       # would qualify again — must not
    r = simulate_leg(bars(rows), ENTRY, EXIT, 60)
    assert len(r['fills']) == 2 and r['stops'] == 2
    assert r['pnl'] == -120


def test_no_reentry_when_off_or_price_never_returns():
    rows = flat(100)
    rows['09:40'] = (100, 170, 100, 160)
    rows['09:41'] = (160, 170, 150, 160)     # stays above cost afterwards
    rows.update({f'09:{m}': (150, 150, 150, 150) for m in range(42, 60)})
    rows.update({f'10:{m:02d}': (150, 150, 150, 150) for m in range(0, 31)})
    r = simulate_leg(bars(rows), ENTRY, EXIT, 60)
    assert len(r['fills']) == 1 and r['pnl'] == -60 and not r['reentered']
    rows2 = flat(100)
    rows2['09:40'] = (100, 170, 100, 160)
    assert len(simulate_leg(bars(rows2), ENTRY, EXIT, 60, reentry=False)['fills']) == 1


def test_stop_bar_itself_does_not_trigger_reentry():
    rows = flat(100)
    rows['09:40'] = (100, 170, 95, 100)      # spikes through stop, low below cost, same minute
    rows['09:41'] = (120, 120, 120, 120)
    r = simulate_leg(bars(rows), ENTRY, EXIT, 60)
    assert r['stops'] == 1
    assert r['reentered'] and r['fills'][1]['entry_time'] > pd.Timestamp(f'{DAY} 09:40')


def test_gap_through_stop_fills_at_open():
    rows = flat(100)
    rows['09:40'] = (180, 190, 175, 185)
    r = simulate_leg(bars(rows), ENTRY, EXIT, 60)
    assert r['fills'][0]['exit'] == 180


def test_no_entry_bar_means_no_leg():
    assert simulate_leg(bars({'10:00': (50, 50, 50, 50)}), ENTRY, EXIT, 60) is None


def test_strikes_and_day_rows():
    assert atm_strike(25263) == 25250
    assert strikes_for(25263, 2) == {'CE': 25350, 'PE': 25150}
    exp = date(2026, 10, 20)

    def feed(_e, strike, right):
        return bars({**flat(40), '10:30': (30, 30, 30, 30)}) if right == 'CE' else \
            bars({**flat(50), '09:40': (50, 85, 50, 80), '09:50': (50, 50, 49, 50), '10:30': (45, 45, 45, 45)})

    res = run_day(date(2026, 10, 16), 25263, exp, feed)
    row = trade_row(res, qty=65, lots=1, lot_size=65, brokerage_per_leg=100,
                    entry_hm=(9, 30), exit_hm=(10, 30))
    # CE +10; PE: stop at 80 (−30), re-sold 50 → 45 (+5)
    assert row['ce_pnl'] == 10 and row['pe_pnl'] == -25 and row['pnl'] == -15
    assert row['stops'] == 1 and row['reentries'] == 1 and row['result'] == 'LOSS'
    assert row['brokerage'] == 300          # 3 round trips
    assert row['pnl_rupees'] == round(-15 * 65 - 300, 2)
    s = summarise([row])
    assert s['total_trades'] == 1 and s['sl_hits'] == 1 and s['reentries'] == 1


def test_run_day_none_without_bars():
    assert run_day(date(2026, 10, 16), 25000, date(2026, 10, 20), lambda *a: None) is None


def test_sweep_ranks_combos_on_preloaded_bars():
    from trading_app.Backtest.option_selling_engine import sweep
    exp = date(2026, 10, 20)
    sessions = []
    for d in range(5, 10):
        day = date(2026, 10, d)
        stamp = f'2026-10-{d:02d}'
        idx = pd.date_range(f'{stamp} 09:30', f'{stamp} 15:20', freq='min')
        mk = lambda p: pd.DataFrame({'open': p, 'high': p, 'low': p, 'close': p}, index=idx)
        sessions.append({'day': day, 'spot': 25000, 'expiry': exp,
                         'bars': {(25000 + 50 * k, 'CE'): mk(40.0) for k in range(0, 5)}
                                 | {(25000 - 50 * k, 'PE'): mk(40.0) for k in range(0, 5)}})
    res = sweep(sessions, lots=1, lot_size=65, min_trades=3,
                otm_grid=(0, 2), sl_grid=(60,), reentry_grid=(True,), exit_grid=((10, 30),))
    assert len(res) == 2 and all(r['total_trades'] == 5 for r in res)
    # flat premium: no stops, zero points, only brokerage (2 legs × ₹100 × 5 days)
    assert res[0]['gross_pnl_rupees'] == 0 and res[0]['net_pnl_rupees'] == -1000
    assert sweep(sessions, 1, 65, min_trades=99) == []


def test_sl_pct_zero_is_no_stop():
    """The ATM Straddle sells with sl_pct 0: a spike to 5× entry is held to the time exit."""
    rows = flat(100)
    rows['09:40'] = (100, 500, 100, 450)
    b = bars({**rows, '10:30': (120, 120, 120, 120)})
    r = simulate_leg(b, ENTRY, EXIT, 0, reentry=False)
    f = r['fills']
    assert len(f) == 1 and f[0]['reason'] == 'Time exit'
    assert f[0]['exit'] == 120 and r['pnl'] == -20 and r['stops'] == 0


def test_atm_straddle_day_sells_both_atm_legs_to_1514():
    day = date(2026, 10, 16)
    entry = (9, 20)
    exit_hm = (15, 14)
    seen = []

    def bars_for(expiry, strike, right):
        seen.append((strike, right))
        price = 150 if right == 'CE' else 140
        rows = flat(price, start=entry, n=(15 * 60 + 14) - (9 * 60 + 20))
        rows['12:00'] = (price, price * 3, price, price)   # no stop to hit
        rows['15:14'] = (100, 100, 100, 100)
        return bars(rows)

    res = run_day(day, 25_012.0, date(2026, 10, 20), bars_for, entry_hm=entry, exit_hm=exit_hm,
                  otm_strikes=0, sl_pct=0, reentry=False)
    assert sorted(seen) == [(25_000, 'CE'), (25_000, 'PE')]
    assert res['complete']
    row = trade_row(res, 65, 1, 65, 100.0, entry, exit_hm)
    assert row['ce_strike'] == row['pe_strike'] == 25_000
    assert row['ce_entry'] == 150 and row['ce_exit'] == 100
    assert row['pe_entry'] == 140 and row['pe_exit'] == 100
    assert row['pnl'] == 90 and row['stops'] == 0
    assert row['exit_time'].endswith('15:14:00')


def _straddle_day(ce_rows, pe_rows, max_loss_points):
    def bars_for(expiry, strike, right):
        return bars(ce_rows if right == 'CE' else pe_rows)
    return run_day(date(2026, 10, 16), 25_000.0, date(2026, 10, 20), bars_for,
                   entry_hm=(9, 30), exit_hm=(10, 30), otm_strikes=0, sl_pct=0,
                   reentry=False, max_loss_points=max_loss_points)


def test_max_loss_per_leg_fills_at_the_level_not_the_close():
    """65 qty, ₹2,000 → stop at entry + 30.77. A CE minute running to 160 and
    closing 150 (the 2026-01-22 shape) costs exactly ₹2,000; the PE rides on."""
    ce = flat(100)
    pe = flat(100)
    ce['09:40'] = (100, 128, 100, 128)      # high under the stop
    ce['09:45'] = (128, 160, 128, 150)      # runs through it inside the minute
    pe['09:45'] = (100, 125, 100, 125)
    ce['10:30'] = pe['10:30'] = (90, 90, 90, 90)
    res = _straddle_day(ce, pe, 2000 / 65)
    c = res['legs']['CE']['fills'][0]
    p = res['legs']['PE']['fills'][0]
    assert c['reason'] == 'Max loss' and c['exit'] == pytest.approx(100 + 2000 / 65)
    assert c['exit_time'] == pd.Timestamp(f'{DAY} 09:45')
    assert p['reason'] == 'Time exit' and p['exit'] == 90
    row = trade_row(res, 65, 1, 65, 100.0, (9, 30), (10, 30))
    assert row['ce_pnl'] * 65 == pytest.approx(-2000, abs=1)
    assert row['max_loss_legs'] == 1 and row['max_loss_hit']
    s = summarise([row])
    assert s['max_loss_days'] == 1 and s['max_loss_legs'] == 1


def test_max_loss_per_leg_caps_the_day_at_twice_the_limit():
    """Both legs stopped: −₹4,000 before brokerage, however far the premiums run."""
    ce = flat(100)
    pe = flat(100)
    ce['09:40'] = (100, 200, 100, 190)
    pe['10:10'] = (100, 300, 100, 280)
    ce['10:30'] = pe['10:30'] = (300, 300, 300, 300)
    res = _straddle_day(ce, pe, 2000 / 65)
    row = trade_row(res, 65, 1, 65, 100.0, (9, 30), (10, 30))
    assert row['max_loss_legs'] == 2
    assert row['gross_pnl_rupees'] == pytest.approx(-4000, abs=1)
    assert row['pnl_rupees'] == pytest.approx(-4200, abs=1)      # + 2 legs' brokerage


def test_max_loss_per_leg_gap_fills_at_the_open():
    ce = flat(100)
    ce['09:40'] = (150, 155, 145, 150)      # opens 50 up, past the 30.77 stop
    pe = flat(100)
    res = _straddle_day(ce, pe, 2000 / 65)
    assert res['legs']['CE']['fills'][0]['exit'] == 150


def test_max_loss_per_leg_one_legs_loss_is_not_offset_but_under_the_limit_is_held():
    ce = flat(100)
    pe = flat(100)
    ce['09:40'] = (100, 125, 100, 110)      # 25 up at worst: under the stop
    pe['09:50'] = (100, 100, 20, 20)
    ce['10:30'] = pe['10:30'] = (90, 90, 90, 90)
    res = _straddle_day(ce, pe, 2000 / 65)
    assert {leg['fills'][0]['reason'] for leg in res['legs'].values()} == {'Time exit'}
    row = trade_row(res, 65, 1, 65, 0.0, (9, 30), (10, 30))
    assert row['pnl'] == 20 and not row['max_loss_hit']


def test_max_loss_per_leg_with_one_leg_priced():
    ce = flat(100)
    ce['09:50'] = (100, 180, 100, 180)
    res = _straddle_day(ce, {}, 2000 / 65)
    assert not res['complete'] and res['legs']['CE']['fills'][0]['reason'] == 'Max loss'


def _random_straddle_sessions(n_days=12, seed=7):
    """Random-walk CE/PE minutes for a few strikes, so the ATM changes with the entry."""
    import numpy as np
    from datetime import timedelta
    from trading_app.Backtest.option_selling_engine import STRADDLE_ENTRIES
    rng = np.random.default_rng(seed)
    sessions = []
    day = date(2026, 9, 1)
    while len(sessions) < n_days:
        day += timedelta(days=1)
        if day.weekday() > 4:
            continue
        idx = pd.date_range(f'{day} 09:15', f'{day} 15:29', freq='1min')
        spot = 25_000 + np.cumsum(rng.normal(0, 8, len(idx)))
        spots = {hm: float(spot[idx.searchsorted(pd.Timestamp(datetime(day.year, day.month, day.day, *hm)))])
                 for hm in STRADDLE_ENTRIES}
        bars_ = {}
        for strike in range(24_850, 25_200, 50):
            for right in ('CE', 'PE'):
                intrinsic = np.maximum(spot - strike, 0) if right == 'CE' else np.maximum(strike - spot, 0)
                close = intrinsic + np.linspace(120, 40, len(idx)) + rng.normal(0, 2, len(idx))
                op = np.r_[close[0], close[:-1]]
                hi = np.maximum(op, close) + rng.uniform(0, 6, len(idx))
                lo = np.maximum(np.minimum(op, close) - rng.uniform(0, 6, len(idx)), 0.05)
                frame = pd.DataFrame({'open': op, 'high': hi, 'low': lo, 'close': close}, index=idx)
                if rng.random() < 0.1:                       # an illiquid minute or two
                    frame = frame.drop(frame.index[rng.integers(0, len(idx), 3)])
                bars_[(strike, right)] = frame
        sessions.append({'day': day, 'expiry': day, 'spots': spots, 'bars': bars_})
    return sessions


def test_straddle_sweep_books_what_the_backtest_would():
    """Every combo of the fast sweep equals run_day + trade_row + summarise for those params."""
    from trading_app.Backtest.option_selling_engine import sweep_straddle
    sessions = _random_straddle_sessions()
    lots, lot_size, brok = 2, 65, 100.0
    entries = ((9, 15), (9, 30), (10, 0))
    exits = ((13, 0), (15, 14))
    losses = (0, 700, 1500, 4000)
    got = {(r['entry_time'], r['exit_time'], r['max_loss_rupees']): r
           for r in sweep_straddle(sessions, lots, lot_size, brok, min_trades=1,
                                   entry_grid=entries, exit_grid=exits, loss_grid=losses)}
    assert len(got) == len(entries) * len(exits) * len(losses)
    hit_any = False
    for en in entries:
        for ex in exits:
            for loss in losses:
                rows = []
                for sess in sessions:
                    b = sess['bars']
                    res = run_day(sess['day'], sess['spots'][en], sess['expiry'],
                                  lambda e, k, r, _b=b: _b.get((k, r)), entry_hm=en, exit_hm=ex,
                                  otm_strikes=0, sl_pct=0, reentry=False,
                                  max_loss_points=loss / (lots * lot_size))
                    rows.append(trade_row(res, lots * lot_size, lots, lot_size, brok, en, ex))
                s = summarise(rows)
                g = got[(f'{en[0]:02d}:{en[1]:02d}', f'{ex[0]:02d}:{ex[1]:02d}', loss)]
                assert g['total_trades'] == s['total_trades']
                assert g['net_pnl_rupees'] == s['total_pnl_rupees']
                assert g['gross_pnl_rupees'] == s['total_gross_pnl_rupees']
                assert g['max_drawdown_rupees'] == s['max_drawdown_rupees']
                assert g['win_rate'] == s['win_rate']
                assert g['max_loss_days'] == s['max_loss_days']
                assert g['max_loss_legs'] == s['max_loss_legs']
                hit_any = hit_any or s['max_loss_days'] > 0
    assert hit_any                       # the data actually exercised the stop


def test_straddle_sweep_is_ranked_by_net():
    from trading_app.Backtest.option_selling_engine import sweep_straddle
    out = sweep_straddle(_random_straddle_sessions(6), 1, 65, min_trades=1)
    assert len(out) == 5 * 5 * 8
    nets = [r['net_pnl_rupees'] for r in out]
    assert nets == sorted(nets, reverse=True)
