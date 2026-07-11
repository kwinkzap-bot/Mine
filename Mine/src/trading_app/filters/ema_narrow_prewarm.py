"""
End-of-day pre-warm for the EMA Narrow scanner's local candle store.

Refreshes every NSE equity stock's daily-candle file to the deepest history
any EMA Narrow combination could need (Monthly group + EMA 20/50/100/200 =
~7000 days). Once a stock's file covers that depth, every scanner combination
(any timeframe group, any EMA set, any threshold) is a strict subset of what's
stored — so daytime scans read purely from local files, no broker API calls.

Meant to run once per trading day after market close, via the scheduler
(trading_app/app/scheduler.py). The first run pays the full download cost;
every run after that is a same-day tail refresh (seconds), because
candle_store.get_daily_history only fetches the gap since the last stored bar.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

from trading_app.filters.ema_rsi_filter import NARROW_FETCH_DAYS
from trading_app.filters.stock_list_store import get_equity_stocks

logger = logging.getLogger(__name__)

# Deepest requirement across every group/EMA-set combo the UI offers — priming
# to this depth makes every smaller combination a pure local read afterwards.
MAX_NARROW_DAYS = max(days for per_tf in NARROW_FETCH_DAYS.values() for days in per_tf.values())


def prime_equity_store(kite, progress_cb=None) -> Dict:
    """Refresh every NSE equity stock's local candle file to MAX_NARROW_DAYS deep.
    progress_cb(done, total) is invoked periodically for logging/monitoring."""
    from trading_app.filters.candle_store import get_daily_history

    stocks = get_equity_stocks(kite)
    total = len(stocks)
    if not total:
        return {"total": 0, "done": 0, "failed": 0, "elapsed_sec": 0.0}

    is_fyers = kite.__class__.__name__ == 'FyersDataServiceAdapter'
    token_map: Dict[str, object] = {}
    if not is_fyers:
        try:
            for inst in kite.instruments("NSE"):
                if inst.get("instrument_type") == "EQ" and inst.get("tradingsymbol"):
                    token_map[inst["tradingsymbol"]] = inst.get("instrument_token")
        except Exception as e:
            logger.error(f"[EMA Narrow Prewarm] Instrument load failed: {e}")

    def _token_for(symbol: str):
        return f"NSE:{symbol}-EQ" if is_fyers else token_map.get(symbol)

    def _prime_one(symbol: str) -> bool:
        token = _token_for(symbol)
        if not token:
            return False
        df = get_daily_history(kite, token, symbol, MAX_NARROW_DAYS)
        return df is not None and not df.empty

    workers = 5 if is_fyers else 3
    done = 0
    failed = 0
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_prime_one, s): s for s in stocks}
        for future in as_completed(futures):
            try:
                if not future.result(timeout=300):
                    failed += 1
            except Exception as e:
                failed += 1
                logger.debug(f"[EMA Narrow Prewarm] {futures[future]} failed: {e}")
            finally:
                done += 1
                if progress_cb and (done % 100 == 0 or done == total):
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass

    return {"total": total, "done": done, "failed": failed, "elapsed_sec": time.monotonic() - start}
