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
