"""
Parity test: SecondCandleAlgo._check_breakout vs the backtest engine's _run_day.

Feeds a crafted 30-second OHLC series to both the live breakout detector and the
backtest's single-day simulator and asserts they agree on direction + SL/Target.

Run:
    PYTHONPATH=src python3 src/trading_app/algo/second_candle/test_second_candle_signal.py
"""
import sys
import pandas as pd

from trading_app.algo.second_candle.second_candle_algo import (
    SecondCandleAlgo, normalise_params,
)
from trading_app.Backtest.second_candle_engine import SecondCandleBacktestEngine


def _build_candles(base: pd.Timestamp, rows):
    """rows: list of (o, h, l, c). Returns (candles_for_engine, df_for_algo)."""
    candles, dt_rows = [], []
    for i, (o, h, l, c) in enumerate(rows):
        ts = base + pd.Timedelta(seconds=30 * i)
        candles.append({'date': ts.strftime('%Y-%m-%d %H:%M:%S'),
                        'open': o, 'high': h, 'low': l, 'close': c, 'volume': 100})
        dt_rows.append({'datetime': ts, 'open': o, 'high': h, 'low': l, 'close': c})
    df_algo = pd.DataFrame(dt_rows)
    # `base` is already tz-aware, so the datetime column is too — just normalise tz.
    df_algo['datetime'] = pd.to_datetime(df_algo['datetime']).dt.tz_convert('Asia/Kolkata')
    return candles, df_algo


def _run_case(name, rows, params, expect_direction):
    # Anchor mid-day today so bar minutes stay below any sane cut-off
    base = pd.Timestamp.now(tz='Asia/Kolkata').normalize() + pd.Timedelta(hours=10)
    candles, df_algo = _build_candles(base, rows)
    now_ist = base + pd.Timedelta(seconds=30 * len(rows) + 60)  # all bars completed

    p = normalise_params(params)

    # ── Backtest reference ──
    engine = SecondCandleBacktestEngine(
        df=pd.DataFrame(candles),
        candle_index=p['candle_index'], rr_ratio=p['rr_ratio'],
        exit_hour=p['exit_hour'], exit_minute=p['exit_minute'],
        enable_long=p['enable_long'], enable_short=p['enable_short'],
    )
    trades, _ = engine.run()

    # ── Live detector ──
    algo = SecondCandleAlgo('test')
    sig = algo._check_breakout(df_algo, p, now_ist=now_ist)

    if expect_direction is None:
        assert not trades, f"[{name}] backtest unexpectedly traded: {trades}"
        assert sig is None, f"[{name}] live unexpectedly signalled: {sig}"
        print(f"  ✓ {name}: no trade (both agree)")
        return

    assert trades, f"[{name}] backtest produced no trade"
    assert sig is not None, f"[{name}] live produced no signal"
    bt = trades[0]
    bt_dir = 'BUY' if bt['type'] == 'Long' else 'SELL'

    assert sig['direction'] == expect_direction == bt_dir, \
        f"[{name}] direction mismatch live={sig['direction']} bt={bt_dir} expect={expect_direction}"
    assert abs(sig['entry_ref'] - bt['entry_price']) < 0.01, \
        f"[{name}] entry mismatch live={sig['entry_ref']} bt={bt['entry_price']}"
    assert abs(sig['sl_level'] - bt['sl_price']) < 0.01, \
        f"[{name}] SL mismatch live={sig['sl_level']} bt={bt['sl_price']}"
    assert abs(sig['target_level'] - bt['target_price']) < 0.01, \
        f"[{name}] target mismatch live={sig['target_level']} bt={bt['target_price']}"
    print(f"  ✓ {name}: {sig['direction']} entry={sig['entry_ref']} "
          f"sl={sig['sl_level']} tgt={sig['target_level']} (matches backtest)")


def main():
    params = {'candle_index': 2, 'rr_ratio': 3.0, 'exit_hour': 23, 'exit_minute': 59,
              'enable_long': True, 'enable_short': True}

    # Range candle = bar #2 → high=110, low=90. Then a bar breaks above 110.
    long_rows = [
        (100, 105, 95, 102),   # bar 1
        (102, 110, 90, 100),   # bar 2 (range candle): H=110 L=90
        (100, 108, 99, 107),   # bar 3: no breakout
        (108, 115, 107, 114),  # bar 4: high 115 >= 110 → BUY @110, SL=90, risk=20, TGT=110+60=170
        (114, 120, 113, 118),
    ]
    _run_case('long breakout', long_rows, params, 'BUY')

    # Range candle high=110 low=90; a bar breaks below 90 first.
    short_rows = [
        (100, 105, 95, 102),
        (102, 110, 90, 100),   # range: H=110 L=90
        (100, 101, 85, 88),    # low 85 <= 90 → SELL @90, SL=110, risk=20, TGT=90-60=30
        (88, 92, 80, 84),
    ]
    _run_case('short breakout', short_rows, params, 'SELL')

    # No breakout: everything stays inside the range.
    none_rows = [
        (100, 105, 95, 102),
        (102, 110, 90, 100),   # range: H=110 L=90
        (100, 108, 92, 104),
        (104, 109, 93, 106),
    ]
    _run_case('no breakout', none_rows, params, None)

    # Long-only: a downside break is ignored.
    lo_params = dict(params, enable_short=False)
    _run_case('long-only ignores short', short_rows, lo_params, None)

    _late_arm_cases()
    _stale_spot_case()

    print("\nAll parity checks passed ✅")


# ── Late-arm regression (real NIFTY 30-sec bars, 2026-08-10) ──────────────────
# The app restarted at 09:18 and the algo took the 09:16:30 breakout, then a
# stale quote (24644.85 — above the day's actual high of 24620.95) fired an
# instant "TARGET" exit 1.4s after entry. Both halves are covered here.
_0810_ROWS = [
    (24581.25, 24591.50, 24569.00, 24572.90),   # 09:15:00
    (24571.30, 24572.85, 24557.95, 24566.95),   # 09:15:30  range candle: H/L
    (24567.85, 24571.15, 24560.05, 24565.95),   # 09:16:00
    (24566.60, 24584.90, 24565.55, 24579.45),   # 09:16:30  first breakout
    (24579.35, 24586.00, 24575.65, 24578.35),   # 09:17:00
    (24577.55, 24592.20, 24577.15, 24589.60),   # 09:17:30
    (24589.40, 24598.30, 24587.15, 24596.55),   # 09:18:00
    (24596.60, 24601.15, 24594.40, 24599.40),   # 09:18:30
    (24599.40, 24607.75, 24598.85, 24606.20),   # 09:19:00
    (24606.65, 24607.95, 24597.50, 24602.70),   # 09:19:30
    (24602.85, 24620.95, 24602.85, 24613.40),   # 09:20:00  target 24617.55 hit
    (24612.95, 24614.35, 24607.75, 24609.65),   # 09:20:30
]


def _late_arm_cases():
    base = pd.Timestamp.now(tz='Asia/Kolkata').normalize() + pd.Timedelta(hours=9, minutes=15)
    _, df = _build_candles(base, _0810_ROWS)
    p = normalise_params({'candle_index': 2, 'rr_ratio': 3.0,
                          'enable_long': True, 'enable_short': False})
    algo = SecondCandleAlgo('test')

    # Armed 09:18:11 — the breakout is old but its trade is still open → take it.
    now = base + pd.Timedelta(minutes=3, seconds=11)
    sig = algo._check_breakout(df, p, now)
    assert sig and sig['direction'] == 'BUY', f"expected a BUY signal, got {sig}"
    assert (sig['entry_ref'], sig['sl_level'], sig['target_level']) == \
           (24572.85, 24557.95, 24617.55), f"levels drifted from the backtest: {sig}"
    assert algo._signal_already_resolved(df, sig, now) is None, \
        "trade was still open at 09:18:11 — entry must be allowed"
    print("  ✓ late arm (09:18:11): backtest levels, trade still open → entry allowed")

    # Armed 09:21:00 — the target already printed → the day's trade is gone.
    now = base + pd.Timedelta(minutes=6)
    sig = algo._check_breakout(df, p, now)
    assert algo._signal_already_resolved(df, sig, now) == 'TARGET', \
        "breakout resolved at 09:20 must be reported as already finished"
    print("  ✓ late arm (09:21:00): breakout already hit target → skipped, not chased")


def _stale_spot_case():
    from datetime import datetime, timedelta

    class _Q:
        def __init__(self, lp, age_secs):
            self.lp, self.age = lp, age_secs

        def quote(self, symbols, priority=0):
            return {symbols[0]: {'last_price': self.lp,
                                 'timestamp': datetime.now() - timedelta(seconds=self.age)}}

    algo = SecondCandleAlgo('test')
    assert algo._get_nifty_spot(_Q(24644.85, 64800)) is None, \
        "an 18-hour-old quote must never be used as the live spot"
    assert algo._get_nifty_spot(_Q(24596.55, 1)) == 24596.55, \
        "a fresh quote must be accepted"
    print("  ✓ stale spot quote rejected, fresh quote accepted")


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f"\n❌ FAILED: {e}")
        sys.exit(1)
