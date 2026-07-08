"""Parity test: live RTPAlgo logic vs RTPBacktestEngine, for all four variants.

Feeds identical synthetic candle data through:
  1. RTPBacktestEngine.run()  (the backtest / UI path), and
  2. the REAL live methods (_check_rtp_signal, _replay_today_needs_reset) driven
     bar-by-bar with a fake clock and a stub data provider, replicating the
     monitor loop's sequencing (day-start replay, per-bucket signal checks,
     completed-candle exit checks, pointer updates).

The synthetic data is intraday-gapless (each bar opens exactly at the previous
close), so the engine's "enter at next bar open" equals the live "enter at
signal bar close" and every trade must match EXACTLY: entry bar, direction,
exit bar, exit reason and prices.

Run:  python3 test_rtp_live_vs_backtest.py
"""
import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import time as dt_time
from types import SimpleNamespace

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC  = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine  # noqa: E402
import trading_app.algo.rtp_railway_track.rtp_algo as rtp               # noqa: E402

IST = 'Asia/Kolkata'


# ── Fake clock ────────────────────────────────────────────────────────────────
# rtp_algo calls pd.Timestamp.now(tz=...) internally; we swap the module's `pd`
# reference for a shim whose Timestamp.now returns the simulated time.

class FakeTS:
    current: pd.Timestamp = None  # tz-aware IST

    def __new__(cls, *a, **k):
        return pd.Timestamp(*a, **k)

    @classmethod
    def now(cls, tz=None):
        t = cls.current
        if tz is None:
            return t.tz_localize(None)
        return t.tz_convert(tz)


_pd_shim = SimpleNamespace(
    Timestamp=FakeTS,
    Timedelta=pd.Timedelta,
    DataFrame=pd.DataFrame,
    to_datetime=pd.to_datetime,
)


# ── Synthetic data ────────────────────────────────────────────────────────────

def _tick(x):
    return round(round(x * 20) / 20, 2)  # 0.05 tick


def make_30s_data(seed=7, n_days=7):
    """Gapless-intraday 30s OHLC bars, 9:15:00–15:29:30, regime-switching drift."""
    rng  = np.random.default_rng(seed)
    days = pd.bdate_range('2026-06-25', periods=n_days, tz=IST)
    rows = []
    price = 24000.0
    for d in days:
        price = _tick(price + rng.normal(0, 40))          # overnight gap
        t     = d + pd.Timedelta(hours=9, minutes=15)
        drift = rng.normal(0, 0.6)
        for _ in range(750):                              # 750 × 30s = 9:15–15:30
            if rng.random() < 1 / 90:                     # regime switch
                drift = rng.normal(0, 0.6)
            o = price
            c = _tick(o + drift + rng.normal(0, 1.6))
            h = _tick(max(o, c) + abs(rng.normal(0, 1.0)))
            l = _tick(min(o, c) - abs(rng.normal(0, 1.0)))
            rows.append({'date': t.isoformat(), 'open': o, 'high': h,
                         'low': l, 'close': c, 'volume': 100})
            price = c
            t += pd.Timedelta(seconds=30)
    return rows


def aggregate(rows30, secs):
    if secs == 30:
        return list(rows30)
    df = pd.DataFrame(rows30)
    df['dt'] = pd.to_datetime(df['date'])
    df['bucket'] = df['dt'].dt.floor(f'{secs}s')
    g = df.groupby('bucket', sort=True).agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'), close=('close', 'last'), volume=('volume', 'sum'),
    ).reset_index()
    return [{'date': b.isoformat(), 'open': o, 'high': h, 'low': l,
             'close': c, 'volume': v}
            for b, o, h, l, c, v in g.itertuples(index=False)]


# ── Live simulation (drives the real rtp_algo methods) ───────────────────────

def simulate_live(vkey, rows, tmpdir):
    cfg0 = rtp.RTP_VARIANTS[vkey]
    algo = rtp.RTPAlgo('parity_test', vkey)
    algo.cfg = replace(
        cfg0,
        state_file=os.path.join(tmpdir, f'state_{vkey}.json'),
        history_file=os.path.join(tmpdir, f'hist_{vkey}.json'),
        all_history_file=os.path.join(tmpdir, f'allhist_{vkey}.json'),
    )
    history = []
    with open(algo.cfg.history_file, 'w') as f:
        json.dump(history, f)

    bar_td = algo._bar_td
    im     = algo._engine_interval_min
    ts     = [pd.Timestamp(r['date']) for r in rows]
    df_all = pd.DataFrame(rows)
    n      = len(rows)

    def provider_fetch(**kw):
        now = FakeTS.current
        return [r for r, t in zip(rows, ts) if t < now]

    provider = SimpleNamespace(historical_data=provider_fetch)

    state   = {'buy_needs_reset': False, 'sell_needs_reset': False}
    ptr     = None
    active  = None
    trades  = []
    cur_day = ts[rtp._WARMUP_BARS].date()

    def close_trade(j, price, reason):
        nonlocal active, ptr
        trades.append({
            'entry_time':  active['entry_dt'],
            'entry_price': active['entry_price'],
            'direction':   active['direction'],
            'exit_time':   ts[j],
            'exit_price':  round(price, 2),
            'exit_reason': reason,
        })
        history.append({'entry_time': active['entry_dt'].isoformat(),
                        'exit_time':  ts[j].isoformat()})
        with open(algo.cfg.history_file, 'w') as f:
            json.dump(history, f)
        ptr    = ts[j]     # resume signal scan at the bar AFTER the exit bar
        active = None

    for k in range(rtp._WARMUP_BARS, n):
        FakeTS.current = ts[k] + bar_td + pd.Timedelta(seconds=1)

        # Day start → thread restart: replay seeds the flags + scan pointer
        if ts[k].date() != cur_day:
            cur_day = ts[k].date()
            assert active is None, "trade held overnight — EOD close failed"
            b, s  = algo._replay_today_needs_reset(provider)
            state = {'buy_needs_reset': b, 'sell_needs_reset': s}
            ptr   = algo._replay_last_bar_dt

        if active is not None:
            if k < active['entry_bar_idx']:
                continue
            bj, r = ts[k], rows[k]
            eod = (bj.hour == 15 and bj.minute + im > 28) or bj.hour > 15 \
                  or bj.date() != active['entry_date']
            if eod:
                close_trade(k, r['close'], 'EOD')
            elif active['direction'] == 'BUY':
                if r['low'] <= active['sl']:
                    close_trade(k, active['sl'], 'SL')
                elif r['high'] >= active['tgt']:
                    close_trade(k, active['tgt'], 'TARGET')
            else:
                if r['high'] >= active['sl']:
                    close_trade(k, active['sl'], 'SL')
                elif r['low'] <= active['tgt']:
                    close_trade(k, active['tgt'], 'TARGET')
            continue

        # Live loop stops checking signals at 15:28 (EOD branch breaks the loop)
        if FakeTS.current.tz_convert(IST).time() >= dt_time(15, 28):
            continue

        sig, state, ptr, sig_close = algo._check_rtp_signal(
            df_all.iloc[:k + 1], state, ptr)
        if sig:
            assert ptr == ts[k], f"signal bar {ptr} != current bar {ts[k]}"
            if k + 1 >= n:
                continue
            entry_ref = sig_close
            if sig == 'BUY':
                sl, tgt = round(entry_ref - cfg0.sl_points, 2), round(entry_ref + cfg0.tgt_points, 2)
            else:
                sl, tgt = round(entry_ref + cfg0.sl_points, 2), round(entry_ref - cfg0.tgt_points, 2)
            active = {
                'direction':     sig,
                'entry_price':   round(entry_ref, 2),
                'entry_bar_idx': k + 1,
                'entry_dt':      ts[k + 1],
                'entry_date':    ts[k + 1].date(),
                'sl':            sl,
                'tgt':           tgt,
            }

    return trades


# ── Comparison ────────────────────────────────────────────────────────────────

def run_engine(vkey, rows):
    cfg = rtp.RTP_VARIANTS[vkey]
    eng = RTPBacktestEngine(
        df=pd.DataFrame(rows),
        entry_mode='RTP(20 & 9)',
        interval_minutes=max(1, rtp._INTERVAL_SECS[cfg.interval] // 60),
        slope_bars=rtp._SLOPE_BARS,
        use_adx=cfg.use_adx,
        adx_thresh=cfg.adx_thresh,
        sl_points=cfg.sl_points,
        tgt_points=cfg.tgt_points,
    )
    return eng.run()['trades']


def compare(vkey, eng_trades, live_trades):
    print(f"\n{'=' * 72}\n  {vkey}: engine={len(eng_trades)} trades, "
          f"live-sim={len(live_trades)} trades")
    ok = True
    for i in range(max(len(eng_trades), len(live_trades))):
        e = eng_trades[i] if i < len(eng_trades) else None
        l = live_trades[i] if i < len(live_trades) else None
        if e is None or l is None:
            ok = False
            print(f"  #{i + 1} MISSING — engine={e and e['entry_time']} "
                  f"live={l and l['entry_time']}")
            continue
        mismatches = []
        if pd.Timestamp(e['entry_time']) != l['entry_time']:
            mismatches.append(f"entry_time {e['entry_time']} vs {l['entry_time']}")
        if e['direction'] != l['direction']:
            mismatches.append(f"direction {e['direction']} vs {l['direction']}")
        if abs(e['entry_price'] - l['entry_price']) > 0.011:
            mismatches.append(f"entry_price {e['entry_price']} vs {l['entry_price']}")
        if pd.Timestamp(e['exit_time']) != l['exit_time']:
            mismatches.append(f"exit_time {e['exit_time']} vs {l['exit_time']}")
        if e['exit_reason'] != l['exit_reason']:
            mismatches.append(f"exit_reason {e['exit_reason']} vs {l['exit_reason']}")
        if abs(e['exit_price'] - l['exit_price']) > 0.011:
            mismatches.append(f"exit_price {e['exit_price']} vs {l['exit_price']}")
        if mismatches:
            ok = False
            print(f"  #{i + 1} MISMATCH: " + '; '.join(mismatches))
        else:
            print(f"  #{i + 1} match: {l['direction']} "
                  f"{str(l['entry_time'])[:19]} → {str(l['exit_time'])[:19]} "
                  f"{l['exit_reason']}")
    return ok


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(f"seed={seed}")
    rows30 = make_30s_data(seed=seed)
    data = {
        '30s': rows30,
        '1m':  aggregate(rows30, 60),
        '3m':  aggregate(rows30, 180),
        '5m':  aggregate(rows30, 300),
    }

    rtp.pd = _pd_shim          # fake clock for the live-code path
    logging_ok = True
    try:
        import logging
        logging.disable(logging.CRITICAL)
        results = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            for vkey in ('1m', '30s', '3m', '5m'):
                rows = data[vkey]
                live = simulate_live(vkey, rows, tmpdir)
                eng  = run_engine(vkey, rows)
                results[vkey] = compare(vkey, eng, live)
    finally:
        rtp.pd = pd            # restore

    print(f"\n{'=' * 72}")
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"  PARITY FAILED for: {failed}")
        sys.exit(1)
    print("  ALL VARIANTS MATCH — live logic ≡ backtest logic")
    sys.exit(0)


if __name__ == '__main__':
    main()
