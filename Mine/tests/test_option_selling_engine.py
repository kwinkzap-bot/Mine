"""NIFTY short-strangle rule — Backtest/option_selling_engine.py."""
from datetime import date

import pandas as pd

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
