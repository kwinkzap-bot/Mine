import numpy as np
import pandas as pd

from trading_app.Backtest.hilega_milega_engine import HilegaMilegaEngine, ema, vwma
from trading_app.Backtest.st_bb_rsi_engine import rsi


def _frame(days, volume=0):
    rows = []
    for d, closes in days:
        t = pd.Timestamp(f'{d} 09:15')
        for k, cl in enumerate(closes):
            rows.append({'date': t + pd.Timedelta(minutes=5 * k), 'open': cl - 0.2,
                         'high': cl + 1, 'low': cl - 1, 'close': cl, 'volume': volume})
    return pd.DataFrame(rows)


def _rally_then_drop():
    chop = [100 + 2 * np.sin(k / 2.0) for k in range(40)]
    up = list(np.linspace(100, 160, 25))
    down = list(np.linspace(160, 90, 10))
    return _frame([('2025-01-01', chop + up + down)])


def test_ema_is_sma_seeded():
    out = ema(np.arange(1.0, 11.0), 3)
    assert np.isnan(out[1]) and out[2] == 2.0
    assert abs(out[3] - (4 * 0.5 + 2.0 * 0.5)) < 1e-9


def test_vwma_weights_by_volume_and_falls_back_without_it():
    v = np.array([1.0, 2.0, 3.0, 4.0])
    assert abs(vwma(v, np.array([1.0, 1.0, 1.0, 9.0]), 2)[3] - (3 + 36) / 10) < 1e-9
    assert abs(vwma(v, np.zeros(4), 2)[3] - (3 * 1 + 4 * 2) / 3) < 1e-9      # linear WMA


def test_flat_market_takes_no_trade():
    trades, s = HilegaMilegaEngine(_frame([('2025-01-01', [100.0] * 90)])).run()
    assert trades == [] and s['total_trades'] == 0


def test_rally_goes_long_and_exits_with_the_stop_or_the_zone():
    trades, _ = HilegaMilegaEngine(_rally_then_drop(), entry_start='09:15').run()
    longs = [t for t in trades if t['type'] == 'Long']
    assert longs
    t = longs[0]
    assert t['entry_time'] < t['exit_time']
    assert t['sl_price'] < t['entry_price']
    assert t['exit_reason'] in ('Red leaves zone', 'Stop Loss', 'Time Exit', 'EOD Exit')
    assert t['signal']['rsi'] > 50


def test_direction_filter_and_mirror():
    f = _rally_then_drop()
    assert HilegaMilegaEngine(f, direction='short', entry_start='09:15').run()[0] == [] or \
        all(t['type'] == 'Short' for t in HilegaMilegaEngine(f, direction='short', entry_start='09:15').run()[0])
    mirrored = f.copy()
    for c in ('open', 'high', 'low', 'close'):
        mirrored[c] = 400 - f[c]
    mirrored['high'], mirrored['low'] = 400 - f['low'], 400 - f['high']
    shorts = HilegaMilegaEngine(mirrored, entry_start='09:15').run()[0]
    assert shorts and shorts[0]['type'] == 'Short' and shorts[0]['sl_price'] > shorts[0]['entry_price']


def test_signal_fills_on_the_next_open_and_stop_is_the_signal_candle():
    f = _rally_then_drop()
    eng = HilegaMilegaEngine(f, entry_start='09:15')
    sig = eng.signals()['signal']
    i = int(np.flatnonzero(sig == 1)[0])
    t = eng.run()[0][0]
    assert t['entry_time'] == str(f['date'].iloc[i + 1]).replace('T', ' ')[:19]
    assert t['entry_price'] == round(float(f['open'].iloc[i + 1]), 2)
    assert t['sl_price'] == round(float(f['low'].iloc[i]), 2)


def test_target_in_r_multiples():
    t = HilegaMilegaEngine(_rally_then_drop(), rr=1.0, entry_start='09:15').run()[0][0]
    risk = t['entry_price'] - t['sl_price']
    assert abs(t['target_price'] - (t['entry_price'] + risk)) < 0.02


def test_square_off_ends_the_day_flat():
    chop = [100 + 2 * np.sin(k / 2.0) for k in range(40)]
    up = list(np.linspace(100, 190, 33))                # rises to the close; nothing flips
    trades, _ = HilegaMilegaEngine(_frame([('2025-01-01', chop + up)]), entry_start='09:15').run()
    assert trades and all(t['exit_reason'] in ('Time Exit', 'EOD Exit', 'Red leaves zone') for t in trades)
    assert all(t['exit_time'][:10] == '2025-01-01' for t in trades)


def test_rsi_matches_wilder_reference():
    r = rsi(np.arange(1.0, 30.0), 9)
    assert r[9] == 100.0


def _three_days():
    chop = [100 + 2 * np.sin(k / 2.0) for k in range(40)]
    d1 = chop + list(np.linspace(100, 150, 30))              # entry on day 1, still rising at the close
    d2 = list(np.linspace(150, 200, 70))                      # carried through day 2
    d3 = list(np.linspace(200, 90, 40))                       # the break comes on day 3
    return _frame([('2025-01-01', d1), ('2025-01-02', d2), ('2025-01-03', d3)])


def test_positional_trade_is_carried_overnight_and_ends_on_the_zone_or_stop():
    f = _three_days()
    pos = HilegaMilegaEngine(f, entry_start='09:15', positional=True).run()[0]
    assert pos
    t = pos[0]
    assert t['exit_time'][:10] > t['entry_time'][:10] and t['days_held'] >= 1
    assert t['exit_reason'] in ('Red leaves zone', 'Stop Loss', 'Still Open')
    assert 'Time Exit' not in {x['exit_reason'] for x in pos}


def test_intraday_mode_still_squares_off_the_same_day():
    f = _three_days()
    intra = HilegaMilegaEngine(f, entry_start='09:15').run()[0]
    assert intra and all(t['exit_time'][:10] == t['entry_time'][:10] for t in intra)


def test_positional_caps_entries_per_day_not_in_total():
    f = _three_days()
    many = HilegaMilegaEngine(f, entry_start='09:15', positional=True, max_trades_per_day=1).run()[0]
    per_day = {}
    for t in many:
        per_day[t['entry_time'][:10]] = per_day.get(t['entry_time'][:10], 0) + 1
    assert all(n <= 1 for n in per_day.values())


def test_positional_legs_hold_one_contract_across_sessions(monkeypatch):
    from datetime import date, timedelta
    from trading_app.Backtest import hilega_milega_engine as hm, st_bb_rsi_engine as sb
    from trading_app.service import cpr_option_service as cos

    seen = []

    def fetch(con, day):                       # premium falls 10 a session: the exit is read on its own day
        seen.append((con['strike'], con['expiry'], day.isoformat()))
        base = 250.0 - 10 * (day - date(2026, 1, 5)).days
        return [{'time': f'{h:02d}:{m:02d}', 'close': base} for h in range(9, 16) for m in range(60)]

    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda symbol: fetch)
    monkeypatch.setattr(sb, '_listed_expiries', lambda symbol, after: [])
    cal = [date(2026, 1, 5) + timedelta(days=k) for k in range(40) if (date(2026, 1, 5) + timedelta(days=k)).weekday() < 5]
    trade = {'entry_time': '2026-01-05 11:15:00', 'exit_time': '2026-01-06 10:15:00', 'type': 'Long',
             'entry_price': 23000.0, 'exit_price': 23100.0, 'pnl': 100.0, 'result': 'WIN'}
    priced, info = hm.attach_positional_legs('NIFTY', [trade], 250.0, calendar=cal)
    assert info['unpriced'] == 0 and len(priced) == 1
    p = priced[0]
    assert p['entry_premium'] == 250.0 and p['exit_premium'] == 240.0 and p['pnl'] == -10.0
    assert p['index_pnl'] == 100.0 and p['option_type'] == 'CE' and p['rolls'] == 0
    assert date.fromisoformat(p['expiry']) >= cal[1]                  # at least 1 session after the entry
    assert {(s, e) for s, e, d in seen if d == '2026-01-06'} == {(p['strike'], p['expiry'])}


def test_positional_legs_roll_to_the_next_expiry_while_the_signal_is_on(monkeypatch):
    from datetime import date, timedelta
    from trading_app.Backtest import hilega_milega_engine as hm, st_bb_rsi_engine as sb
    from trading_app.service import cpr_option_service as cos

    fetched = []

    def fetch(con, day):                        # a contract is quoted at 100 + its strike/100 - the days to expiry
        fetched.append((con['strike'], con['expiry'], day.isoformat()))
        left = (date.fromisoformat(con['expiry']) - day).days
        return [{'time': f'{h:02d}:{m:02d}', 'close': 200.0 + 5 * left - (con['strike'] - 23000) / 10}
                for h in range(9, 16) for m in range(60)]

    monkeypatch.setattr(cos, '_option_minutes_fetcher', lambda symbol: fetch)
    monkeypatch.setattr(sb, '_listed_expiries', lambda symbol, after: [])
    cal = [date(2026, 1, 5) + timedelta(days=k) for k in range(60) if (date(2026, 1, 5) + timedelta(days=k)).weekday() < 5]
    # held from Mon 5 Jan to Mon 26 Jan: three weekly expiries pass under it
    trade = {'entry_time': '2026-01-05 11:15:00', 'exit_time': '2026-01-26 10:15:00', 'type': 'Long',
             'entry_price': 23000.0, 'exit_price': 23500.0, 'pnl': 500.0, 'result': 'WIN'}
    priced, info = hm.attach_positional_legs('NIFTY', [trade], 250.0, calendar=cal, roll='same')
    p = priced[0]
    assert p['rolls'] >= 2 and info['rolls'] == p['rolls'] and len(p['legs']) == p['rolls'] + 1
    assert len({g['strike'] for g in p['legs']}) == 1                  # same strike carried forward
    expiries = [g['expiry'] for g in p['legs']]
    assert expiries == sorted(expiries) and len(set(expiries)) == len(expiries)
    for a, b in zip(p['legs'], p['legs'][1:]):
        assert a['exit_day'] == a['expiry']                              # sold on its expiry day
        assert date.fromisoformat(b['expiry']) > date.fromisoformat(a['expiry'])
    assert p['pnl'] == round(sum(g['exit'] - g['entry'] for g in p['legs']), 2)
    assert p['entry_premium'] == p['legs'][0]['entry'] and p['exit_premium'] == p['legs'][-1]['exit']


def test_daily_candles_are_positional_and_ignore_the_clock_window():
    days = []
    for k in range(80):
        px = 100 + 2 * np.sin(k / 2.0) if k < 40 else 100 + (k - 39) * 3.0
        days.append({'date': pd.Timestamp('2025-01-01 09:15') + pd.Timedelta(days=k), 'open': px - 0.2,
                     'high': px + 1, 'low': px - 1, 'close': px, 'volume': 0})
    eng = HilegaMilegaEngine(pd.DataFrame(days), bar_minutes=1440, entry_start='10:15')
    assert eng.positional and eng.entry_start == 0
    trades, _ = eng.run()
    assert trades and trades[0]['days_held'] >= 1


def test_pullback_entry_fires_inside_a_running_zone():
    f = _three_days()
    pb = HilegaMilegaEngine(f, entry_start='09:15', positional=True, entry_mode='pullback', pb_band=60).run()[0]
    brk = HilegaMilegaEngine(f, entry_start='09:15', positional=True, entry_mode='breakout').run()[0]
    either = HilegaMilegaEngine(f, entry_start='09:15', positional=True, entry_mode='either', pb_band=60).run()[0]
    assert pb and len(either) >= len(brk)
    assert HilegaMilegaEngine(f, entry_start='09:15', entry_mode='pullback', pb_band=0).signals()['signal'].tolist() \
        != HilegaMilegaEngine(f, entry_start='09:15', entry_mode='pullback', pb_band=60).signals()['signal'].tolist()


def test_sideways_filter_blocks_chop_and_daily_bias_gates_direction():
    f = _three_days()
    base = HilegaMilegaEngine(f, entry_start='09:15', positional=True).signals()['signal']
    quiet = HilegaMilegaEngine(f, entry_start='09:15', positional=True, chop_lookback=40, chop_max=0).signals()['signal']
    assert (quiet != 0).sum() <= (base != 0).sum()
    daily = HilegaMilegaEngine(f, entry_start='09:15', positional=True, htf='daily').signals()['signal']
    assert (daily != 0).sum() <= (base != 0).sum()
