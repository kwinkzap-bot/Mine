"""Regression tests for the EMA Confluence Breakout engine.

These pin down the two defects that made the same symbol report completely
different trades depending only on the backtest's start date:
  1. an armed-but-never-filled breakout order blocked every later signal, and
  2. EMAs seeded off the first fetched bar gave the same date different values.
"""
import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest.ema_pullback_engine import EmaPullbackEngine

# Mirrors _EMA_BT_WARMUP_DAYS in trading_app/app/routes/api.py.
WARMUP_DAYS = 1200


@pytest.fixture(scope='module')
def daily():
    """A long synthetic daily series, deterministic across runs."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range('2012-01-01', '2026-07-28')
    close = 200 * np.exp(np.cumsum(rng.normal(0.0004, 0.016, len(dates))))
    wick = np.abs(rng.normal(0, 0.011, len(dates)))
    op = close * (1 + rng.normal(0, 0.006, len(dates)))
    return pd.DataFrame({'date': dates, 'open': op,
                         'high': np.maximum(op, close) * (1 + wick),
                         'low': np.minimum(op, close) * (1 - wick),
                         'close': close, 'volume': 1000})


def _run(daily, start):
    """Run the engine the way the API does: fetch warm-up, trade from `start`."""
    fetch_from = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)
    df = daily[daily['date'] >= fetch_from].reset_index(drop=True)
    return EmaPullbackEngine(daily_df=df, enable_long=False, enable_short=True,
                             target_pct=15, start_date=start).run()


def _ident(t):
    return (t['signal_time'][:10], t['entry_time'][:10], t['entry_price'],
            t['exit_time'][:10], t['exit_reason'])


def test_start_date_does_not_change_overlapping_trades(daily):
    """The core bug: a later start date must not invent a different history."""
    long_trades, _ = _run(daily, '2017-01-01')
    short_trades, _ = _run(daily, '2020-01-01')

    expected = [_ident(t) for t in long_trades if t['entry_time'] >= '2020-01-01']
    assert expected, 'fixture must produce trades in the overlap to be meaningful'
    assert [_ident(t) for t in short_trades] == expected


def test_unfilled_order_does_not_block_later_signals(daily):
    """A signal that never breaks out must not freeze the strategy forever."""
    trades, _ = _run(daily, '2017-01-01')
    # Before the fix a stuck pending order ended all trading in 2019; the run
    # must keep taking trades right through to the end of the data.
    assert max(t['entry_time'][:4] for t in trades) >= '2025'


def test_newer_signal_supersedes_the_armed_level(daily):
    """Entries must fire off the most recent confluence candle, not a stale one."""
    trades, _ = _run(daily, '2017-01-01')
    for t in trades:
        assert t['signal_time'] <= t['entry_time']
    # Each signal is used at most once.
    signals = [t['signal_time'] for t in trades]
    assert len(signals) == len(set(signals))


def test_ema_is_stable_across_fetch_windows(daily):
    """Same date, different fetch start -> same EMA200 inside the traded window."""
    def emas(start):
        fetch_from = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)
        df = daily[daily['date'] >= fetch_from].reset_index(drop=True)
        eng = EmaPullbackEngine(daily_df=df, target_pct=15, start_date=start)
        return eng.daily_df.set_index('datetime')[['ema200']]

    joined = emas('2017-01-01').join(emas('2023-01-01'), how='inner',
                                     lsuffix='_a', rsuffix='_b').dropna()
    traded = joined[joined.index >= pd.Timestamp('2023-01-01', tz='Asia/Kolkata')]
    drift = (traded['ema200_b'] - traded['ema200_a']).abs() / traded['ema200_a'] * 100
    assert len(traded) > 500
    # Was ~0.8% before warm-up + SMA seeding — far more than enough to flip the
    # razor-thin "range touches all four EMAs" test.
    assert drift.max() < 0.01


def test_ema_matches_sma_seeded_reference(daily):
    """_ema must equal TradingView's ta.ema (SMA-seeded recursion)."""
    closes = daily['close'].to_numpy(dtype=float)
    n = 50
    alpha = 2.0 / (n + 1.0)
    ref = np.full(len(closes), np.nan)
    prev = closes[:n].mean()
    ref[n - 1] = prev
    for i in range(n, len(closes)):
        prev = alpha * closes[i] + (1 - alpha) * prev
        ref[i] = prev

    got = EmaPullbackEngine._ema(daily['close'], n).to_numpy()
    assert np.isnan(got[:n - 1]).all()
    np.testing.assert_allclose(got[n - 1:], ref[n - 1:], rtol=1e-12)


def _two_signal_frame():
    """Hand-built series with exactly one tradeable SELL, independent of the
    fixture: a first signal arms a level price never returns to, then a second
    fires much later and fills at 125 with its SL at 135 (risk 10 points)."""
    rows = []

    def bar(o, h, low, c):
        rows.append({'open': o, 'high': h, 'low': low, 'close': c, 'volume': 1000})

    # 250 flat bars at 100 -> all four EMAs converge on 100.
    for _ in range(250):
        bar(100, 100.2, 99.8, 100)
    # Signal A: red, range straddles 100, so it touches all four EMAs.
    # Arms a SELL at its low of 98 — price never trades there again.
    bar(101, 102, 98, 99)
    # Rally away and hold at 130 long enough for the EMAs to reconverge there.
    for i in range(30):
        px = 100 + i
        bar(px, px + 0.5, px - 0.3, px + 1)
    for _ in range(250):
        bar(130, 130.2, 129.8, 130)
    # Signal B: red, and wide enough to straddle all four EMAs up here (the
    # slower ones still trail the rally). Arms a SELL at its low of 125.
    bar(131, 135, 125, 129)
    # Next bar opens above 125 then breaks it -> fills at the trigger itself
    # (not the gap-through path); then drop on to the 15% target.
    bar(125.5, 126, 123, 123.5)
    for _ in range(30):
        bar(105, 106, 104, 105)

    df = pd.DataFrame(rows)
    df.insert(0, 'date', pd.bdate_range('2015-01-01', periods=len(df)))
    return df


def test_stale_order_is_replaced_by_the_next_signal():
    """Proof of the blocking bug: the old engine kept the first order armed
    forever and took NO trade at all; the second signal must win."""
    trades, _ = EmaPullbackEngine(daily_df=_two_signal_frame(), enable_long=False,
                                  enable_short=True, target_pct=15).run()

    assert len(trades) == 1, f'expected the later signal to trade, got {trades}'
    t = trades[0]
    assert t['type'] == 'Short'
    assert t['entry_price'] == pytest.approx(125, abs=0.01)  # Signal B's low
    assert t['sl_price'] == pytest.approx(135, abs=0.01)     # Signal B's high


def test_require_rr_keeps_setups_better_than_one_to_one():
    """Entry 125 / SL 135 risks 10 points; a 15% target pays 18.75, so the
    gate must leave the trade exactly as it was."""
    df = _two_signal_frame()
    gated, summary = EmaPullbackEngine(daily_df=df, enable_long=False,
                                       enable_short=True, target_pct=15,
                                       require_rr=True).run()
    plain, _ = EmaPullbackEngine(daily_df=df, enable_long=False,
                                 enable_short=True, target_pct=15).run()

    assert gated == plain
    assert summary['rr_skipped'] == 0


def test_require_rr_skips_setups_worse_than_one_to_one():
    """Same setup, 7% target: 8.75 points of reward against 10 of risk. The
    trade is taken without the gate and skipped with it."""
    df = _two_signal_frame()
    plain, _ = EmaPullbackEngine(daily_df=df, enable_long=False,
                                 enable_short=True, target_pct=7).run()
    assert len(plain) == 1, 'the ungated run must still take it'

    gated, summary = EmaPullbackEngine(daily_df=df, enable_long=False,
                                       enable_short=True, target_pct=7,
                                       require_rr=True).run()
    assert gated == []
    assert summary['rr_skipped'] == 1


def test_require_rr_defaults_off(daily):
    """Existing runs (and the live algo, which never passes the flag) must be
    byte-identical to before."""
    trades, summary = _run(daily, '2020-01-01')
    assert trades
    assert summary['rr_skipped'] == 0


def test_weekend_session_bar_never_arms_a_setup():
    """NSE's weekend drill sessions print a daily bar with near-zero volume and
    an absurd range — wide enough to 'touch' all four EMAs and fake a confluence
    signal. Live showed Saturday 2026-07-11 as the signal candle on ~8 symbols
    at once, arming triggers at prices no real session ever traded."""
    rows = []
    for _ in range(250):
        rows.append({'open': 100, 'high': 100.2, 'low': 99.8, 'close': 100, 'volume': 1000})
    df = pd.DataFrame(rows)
    df.insert(0, 'date', pd.bdate_range('2026-01-01', periods=len(df)))

    saturday = pd.Timestamp(df['date'].iloc[-1])
    saturday += pd.Timedelta(days=(5 - saturday.dayofweek) % 7 or 7)
    assert saturday.dayofweek == 5
    # The drill bar: 10% wide on 3 lots, straddling every EMA sitting at 100.
    drill = pd.DataFrame([{'date': saturday, 'open': 100, 'high': 110,
                           'low': 95, 'close': 108, 'volume': 3}])

    with_drill = EmaPullbackEngine(daily_df=pd.concat([df, drill], ignore_index=True),
                                   target_pct=5, start_date='2026-01-01')
    with_drill.run()
    assert with_drill.pending_order is None, 'a weekend bar must not arm an order'
    assert (with_drill.daily_df['datetime'].dt.dayofweek < 5).all()

    # Same bar on the next trading day IS a real signal — proving the test frame
    # would otherwise trigger, so the assertion above is about the weekend only.
    monday = drill.assign(date=saturday + pd.Timedelta(days=2))
    with_monday = EmaPullbackEngine(daily_df=pd.concat([df, monday], ignore_index=True),
                                    target_pct=5, start_date='2026-01-01')
    with_monday.run()
    assert with_monday.pending_order is not None
    assert with_monday.pending_order['trigger_level'] == pytest.approx(110)


def test_warmup_bars_are_never_traded(daily):
    """Bars before start_date supply EMA history only."""
    trades, _ = _run(daily, '2020-01-01')
    assert trades
    for t in trades:
        assert t['entry_time'] >= '2020-01-01'
        assert t['signal_time'] >= '2020-01-01'
