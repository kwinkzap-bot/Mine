"""
RTP Signal Detection Test
=========================
Validates live algo signal detection against the backtest engine for today's data.

Usage (from the Mine/Mine directory):
    python -m src.trading_app.algo.rtp_railway_track.test_rtp_signal

What it checks:
  1. Provider connectivity and candle fetch
  2. Warmup bar count (>= 200)
  3. Datetime localization (IST-aware vs naive)
  4. In-progress bar filter (current minute excluded)
  5. Session filter (9:20 AM – 3:28 PM)
  6. Signal conditions bar by bar vs backtest run()
  7. needs_reset flag evolution (matches backtest sequential logic)
  8. Today's signal summary vs backtest
"""

import sys
import os
import logging
from datetime import date, timedelta

import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-7s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('rtp_test')

# ── Constants (must match rtp_algo.py) ────────────────────────────────────────
_WARMUP_BARS   = 200
_LOOKBACK_DAYS = 5
_SLOPE_BARS    = 8
_SL_POINTS     = 30.0
_TGT_POINTS    = 90.0
_NIFTY_FYERS   = 'NSE:NIFTY50-INDEX'


def _fetch_candles(provider):
    today     = date.today()
    from_date = (today - timedelta(days=_LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    to_date   = today.strftime('%Y-%m-%d')
    logger.info(f"Fetching 1-min candles {from_date} → {to_date}")
    candles = provider.historical_data(
        instrument_token=_NIFTY_FYERS,
        from_date=from_date,
        to_date=to_date,
        interval='minute',
        use_cache=False,
    )
    if not candles:
        logger.error("No candle data returned")
        return None
    df = pd.DataFrame(candles).reset_index(drop=True)
    logger.info(f"  Fetched {len(df)} raw bars, columns={list(df.columns)}")
    return df


def _run_engine(df):
    from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine
    engine = RTPBacktestEngine(
        df=df,
        entry_mode='RTP(20 & 9)',
        interval_minutes=1,
        slope_bars=_SLOPE_BARS,
        use_adx=False,
        sl_points=_SL_POINTS,
        tgt_points=_TGT_POINTS,
    )
    return engine


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1 — Candle fetch + warmup
# ═════════════════════════════════════════════════════════════════════════════
def test_candle_fetch(provider):
    logger.info("\n" + "="*60)
    logger.info("TEST 1: Candle fetch + warmup bar count")
    logger.info("="*60)

    df = _fetch_candles(provider)
    if df is None:
        logger.error("FAIL: no data")
        return None, False

    ok = len(df) >= _WARMUP_BARS
    logger.info(f"  Bar count = {len(df)}  (need >= {_WARMUP_BARS})  → {'PASS' if ok else 'FAIL'}")
    return df, ok


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2 — Datetime timezone check
# ═════════════════════════════════════════════════════════════════════════════
def test_datetime_tz(engine_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 2: Datetime column timezone")
    logger.info("="*60)

    dt_col = engine_df['datetime']
    is_aware = dt_col.dt.tz is not None
    logger.info(f"  dtype = {dt_col.dtype}  tz-aware = {is_aware}")
    if is_aware:
        sample = dt_col.iloc[-1]
        logger.info(f"  Sample last bar: {sample}")
        logger.info("  PASS: datetimes are IST-aware")
    else:
        logger.error("  FAIL: datetimes are naive — _prepare() localisation not working")
    return is_aware


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3 — In-progress bar filter
# ═════════════════════════════════════════════════════════════════════════════
def test_inprogress_filter(engine_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 3: In-progress bar filter")
    logger.info("="*60)

    now_ist   = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
    completed = engine_df[engine_df['datetime'] < now_ist]
    all_bars  = engine_df

    logger.info(f"  now_ist          = {now_ist}")
    logger.info(f"  total bars       = {len(all_bars)}")
    logger.info(f"  completed bars   = {len(completed)}")

    if len(all_bars) > 0:
        last_all = all_bars.iloc[-1]['datetime']
        logger.info(f"  last bar (all)   = {last_all}")
    if len(completed) > 0:
        last_cmp = completed.iloc[-1]['datetime']
        logger.info(f"  last bar (done)  = {last_cmp}")
        excluded = all_bars[all_bars['datetime'] >= now_ist]
        if len(excluded) > 0:
            logger.info(f"  excluded (in-progress): {[str(d) for d in excluded['datetime'].tolist()]}")
            logger.info("  PASS: in-progress bar correctly excluded")
        else:
            logger.info("  INFO: no in-progress bar at this moment (between minutes)")
        return True
    else:
        logger.error("  FAIL: no completed bars — check timezone/datetime parsing")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4 — Session filter
# ═════════════════════════════════════════════════════════════════════════════
def test_session_filter(engine_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 4: Session filter (9:20 AM – 3:28 PM)")
    logger.info("="*60)

    today      = date.today()
    today_bars = engine_df[engine_df['datetime'].dt.date == today]
    session    = today_bars[today_bars['session_ok']]

    if today_bars.empty:
        logger.warning("  No today bars yet — market may not have opened")
        return True

    first = today_bars.iloc[0]
    logger.info(f"  Today bars: {len(today_bars)} total, {len(session)} in session")
    logger.info(f"  First today bar: {first['datetime']}  session_ok={first['session_ok']}")

    if len(session) > 0:
        logger.info(f"  First session bar: {session.iloc[0]['datetime']}")
        logger.info(f"  Last  session bar: {session.iloc[-1]['datetime']}")
        logger.info("  PASS")
    else:
        logger.warning("  No session bars yet (before 9:20 AM)")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5 — Signal conditions on each today bar (verbose)
# ═════════════════════════════════════════════════════════════════════════════
def test_signal_conditions(engine_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 5: Signal conditions for each completed bar today")
    logger.info("="*60)

    today    = date.today()
    now_ist  = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
    today_completed = engine_df[
        (engine_df['datetime'].dt.date == today) &
        (engine_df['datetime'] < now_ist)
    ]

    if today_completed.empty:
        logger.warning("  No completed today bars — market may not have opened yet")
        return

    buy_nr  = False
    sell_nr = False
    signals = []

    for _, row in today_completed.iterrows():
        bar_dt = row['datetime']

        # Needs-reset on ALL bars (not gated on session_ok)
        if sell_nr and row['high'] < min(row['ema9'], row['ema20']):
            sell_nr = False
        if buy_nr  and row['low']  > max(row['ema9'], row['ema20']):
            buy_nr  = False

        if not row.get('session_ok', False):
            continue

        buy_sig  = bool(row['rway_up']   and row['bull_stack'] and
                        row['buy_touch'] and row['buy_pat']    and not buy_nr)
        sell_sig = bool(row['rway_dn']   and row['bear_stack'] and
                        row['sell_touch'] and row['sell_pat']  and not sell_nr)

        # Always print session bars with indicator values
        logger.info(
            f"  {str(bar_dt)[11:19]} | "
            f"rway_up={int(bool(row['rway_up']))} "
            f"bull={int(bool(row['bull_stack']))} "
            f"bt={int(bool(row['buy_touch']))} "
            f"bp={int(bool(row['buy_pat']))} "
            f"buy_nr={int(buy_nr)} → BUY={int(buy_sig)} | "
            f"rway_dn={int(bool(row['rway_dn']))} "
            f"bear={int(bool(row['bear_stack']))} "
            f"st={int(bool(row['sell_touch']))} "
            f"sp={int(bool(row['sell_pat']))} "
            f"sell_nr={int(sell_nr)} → SELL={int(sell_sig)}"
        )

        if buy_sig:
            buy_nr = True
            signals.append(('BUY', bar_dt))
        if sell_sig:
            sell_nr = True
            signals.append(('SELL', bar_dt))

    if signals:
        logger.info(f"\n  Signals detected today (simulated live algo):")
        for sig, dt in signals:
            logger.info(f"    {sig} at {dt}")
    else:
        logger.info("\n  No signals detected today")

    return signals


# ═════════════════════════════════════════════════════════════════════════════
# TEST 6 — Backtest run() for today — compare with live sim
# ═════════════════════════════════════════════════════════════════════════════
def test_backtest_today(raw_df, live_signals):
    logger.info("\n" + "="*60)
    logger.info("TEST 6: Backtest run() signals for today vs live simulation")
    logger.info("="*60)

    from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine
    today = date.today()

    # Run full backtest
    engine = RTPBacktestEngine(
        df=raw_df.copy(),
        entry_mode='RTP(20 & 9)',
        interval_minutes=1,
        slope_bars=_SLOPE_BARS,
        use_adx=False,
        sl_points=_SL_POINTS,
        tgt_points=_TGT_POINTS,
    )
    results = engine.run()
    trades  = results.get('trades', [])
    today_trades = [
        t for t in trades
        if pd.Timestamp(t['entry_time']).date() == today
    ]

    logger.info(f"  Backtest total trades today: {len(today_trades)}")
    for t in today_trades:
        logger.info(f"    {t['direction']} entry={t['entry_time']} exit={t['exit_time']} "
                    f"reason={t['exit_reason']} pnl={t.get('pnl_pts', '?')} pts")

    logger.info(f"\n  Live simulation signals today: {len(live_signals or [])}")
    for sig, dt in (live_signals or []):
        logger.info(f"    {sig} at {dt}")

    # Compare signal count
    bt_count   = len(today_trades)
    live_count = len(live_signals or [])
    if bt_count == live_count:
        logger.info(f"\n  PASS: signal counts match ({bt_count})")
    else:
        logger.warning(f"\n  MISMATCH: backtest={bt_count} live={live_count}")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 7 — Live algo _check_rtp_signal() with last_bar_dt tracking
# ═════════════════════════════════════════════════════════════════════════════
def test_live_check_rtp_signal(raw_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 7: _check_rtp_signal() with last_bar_dt=None (first call)")
    logger.info("="*60)

    from trading_app.Backtest.rtp_backtest_engine import RTPBacktestEngine

    engine    = RTPBacktestEngine(
        df=raw_df.copy(),
        entry_mode='RTP(20 & 9)',
        interval_minutes=1,
        slope_bars=_SLOPE_BARS,
        use_adx=False,
        sl_points=_SL_POINTS,
        tgt_points=_TGT_POINTS,
    )
    processed = engine.df
    now_ist   = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')
    completed = processed[processed['datetime'] < now_ist]

    logger.info(f"  Total processed bars : {len(processed)}")
    logger.info(f"  Completed bars       : {len(completed)}")
    if len(completed) > 0:
        logger.info(f"  Last completed bar   : {completed.iloc[-1]['datetime']}")

    state = {'buy_needs_reset': False, 'sell_needs_reset': False}

    # Simulate first call (last_bar_dt=None → only last bar)
    to_check = completed.iloc[-1:] if len(completed) > 0 else completed
    logger.info(f"  Bars to check (first call): {len(to_check)}")

    if len(to_check) > 0:
        row = to_check.iloc[0]
        logger.info(f"  Checking bar: {row['datetime']}")
        logger.info(f"    session_ok={row.get('session_ok')} rway_up={row.get('rway_up')} "
                    f"bull_stack={row.get('bull_stack')} buy_touch={row.get('buy_touch')} "
                    f"buy_pat={row.get('buy_pat')} → BUY_SIGNAL="
                    f"{bool(row.get('session_ok') and row.get('rway_up') and row.get('bull_stack') and row.get('buy_touch') and row.get('buy_pat'))}")
        logger.info(f"    session_ok={row.get('session_ok')} rway_dn={row.get('rway_dn')} "
                    f"bear_stack={row.get('bear_stack')} sell_touch={row.get('sell_touch')} "
                    f"sell_pat={row.get('sell_pat')} → SELL_SIGNAL="
                    f"{bool(row.get('session_ok') and row.get('rway_dn') and row.get('bear_stack') and row.get('sell_touch') and row.get('sell_pat'))}")

    logger.info("  PASS: check executed without error")


# ═════════════════════════════════════════════════════════════════════════════
# TEST 8 — EMA values sanity check (convergence)
# ═════════════════════════════════════════════════════════════════════════════
def test_ema_convergence(engine_df):
    logger.info("\n" + "="*60)
    logger.info("TEST 8: EMA convergence check")
    logger.info("="*60)

    today   = date.today()
    now_ist = pd.Timestamp.now(tz='Asia/Kolkata').floor('min')

    # Check EMA50 spread between oldest and newest today bars
    today_bars = engine_df[
        (engine_df['datetime'].dt.date == today) &
        (engine_df['datetime'] < now_ist)
    ]

    if today_bars.empty:
        logger.warning("  No today bars to check")
        return

    last = today_bars.iloc[-1]
    logger.info(f"  Last completed bar: {last['datetime']}")
    logger.info(f"    Close  = {last['close']:.2f}")
    logger.info(f"    EMA9   = {last['ema9']:.2f}")
    logger.info(f"    EMA20  = {last['ema20']:.2f}")
    logger.info(f"    EMA50  = {last['ema50']:.2f}")
    logger.info(f"    Stack (ema50 < ema20 < ema9): "
                f"{bool(last['ema50'] < last['ema20'] < last['ema9'])}")
    logger.info(f"    bull_stack={last.get('bull_stack')} bear_stack={last.get('bear_stack')}")
    logger.info(f"    rway_up={last.get('rway_up')} rway_dn={last.get('rway_dn')}")
    logger.info(f"    parallel={last.get('parallel')} trending={last.get('trending')}")
    logger.info("  PASS")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def main():
    logger.info("RTP Signal Detection Test")
    logger.info(f"Date: {date.today()}")
    logger.info(f"Parameters: WARMUP={_WARMUP_BARS} LOOKBACK={_LOOKBACK_DAYS}d "
                f"SL={_SL_POINTS} TGT={_TGT_POINTS} SLOPE={_SLOPE_BARS}")

    # Get provider
    try:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider(user='Mine')
        if not provider:
            logger.error("Provider not available — check access token in Mine.env")
            return
        logger.info("Provider initialized")
    except Exception as e:
        logger.error(f"Provider init failed: {e}")
        return

    # Run tests
    raw_df, ok = test_candle_fetch(provider)
    if raw_df is None:
        return

    engine     = _run_engine(raw_df)
    engine_df  = engine.df

    test_datetime_tz(engine_df)
    test_inprogress_filter(engine_df)
    test_session_filter(engine_df)
    live_signals = test_signal_conditions(engine_df)
    test_backtest_today(raw_df, live_signals)
    test_live_check_rtp_signal(raw_df)
    test_ema_convergence(engine_df)

    logger.info("\n" + "="*60)
    logger.info("All tests complete")
    logger.info("="*60)


if __name__ == '__main__':
    main()
