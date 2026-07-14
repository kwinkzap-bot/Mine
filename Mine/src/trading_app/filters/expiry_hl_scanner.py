"""
Expiry High/Low Breakout Scanner.

Anchors on each F&O stock's current monthly-expiry-cycle daily High/Low
(the same detection ExpiryBreakoutEngine uses for the backtest) and checks
whether the latest candle on the selected timeframe ('60minute' | 'day')
qualifies as a signal under the SAME rule as the Monthly Expiry Breakout
filter (ExpiryBreakoutEngine.scan_hl_signals()): a genuine touch (candle
range straddles the level) AND a close beyond it — BUY above the expiry
High, SELL below the expiry Low — AND the candle's close also clears
EVERY EMA 20/50/100/200 on the same timeframe (above all for BUY, below
all for SELL), AND (ema_touch, default 'touch') the candle touched at
least one of those EMAs.

Reuses CPRFilterService for historical-data fetching/caching, the F&O
stock universe, and rate limiting rather than duplicating that
infrastructure.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from trading_app.filters.cpr_filter import CPRFilterService

logger = logging.getLogger(__name__)


def get_expiry_hl_signal(cpr_service: "CPRFilterService", symbol: str, root_date: datetime,
                          timeframe: str = '60minute', ema_touch: str = 'touch') -> Optional[Dict]:
    """Returns a BUY/SELL signal payload for a single stock if its LATEST
    candle on the selected timeframe qualifies under the Monthly Expiry
    Breakout filter's rule (ExpiryBreakoutEngine.scan_hl_signals()), or
    None otherwise. Fetches enough history for EMA200 warmup regardless
    of timeframe (~200 trading days for 'day', ~200 hourly bars for
    '60minute') — too short a lookback would leave EMA200 permanently
    NaN and silently block every candle."""
    daily_days = 320 if timeframe == 'day' else 200
    daily_data = cpr_service.get_hist_data(symbol, days=daily_days, interval='day', end_date=root_date)
    if daily_data is None or len(daily_data) < 20:
        return None

    if timeframe == 'day':
        candle_df = daily_data
    else:
        candle_df = cpr_service.get_hist_data(symbol, days=70, interval='60minute', end_date=root_date)
    if candle_df is None or candle_df.empty:
        return None

    from trading_app.Backtest.expiry_breakout_engine import ExpiryBreakoutEngine
    ma_timeframe = '1day' if timeframe == 'day' else '1hour'
    engine = ExpiryBreakoutEngine(daily_df=daily_data, hourly_df=candle_df,
                                   ma_timeframe=ma_timeframe, ema_touch=ema_touch)
    signals = engine.scan_hl_signals()
    if not signals:
        return None

    last_candle_time = engine.hourly_df['datetime'].iloc[-1].isoformat()
    last_signal = signals[-1]
    if last_signal['time'] != last_candle_time:
        return None   # most recent candle itself isn't a qualifying signal

    return {
        'symbol': symbol,
        'direction': last_signal['direction'],
        'current_price': last_signal['price'],
        'expiry_high': last_signal['expiry_high'],
        'expiry_low': last_signal['expiry_low'],
        'expiry_date': last_signal['expiry_date'],
    }


def get_expiry_hl_signals_in_range(cpr_service: "CPRFilterService", symbol: str,
                                    start_date: datetime, end_date: datetime,
                                    timeframe: str = '60minute', ema_touch: str = 'touch') -> List[Dict]:
    """Returns every BUY/SELL expiry High/Low touch-then-close signal for a
    single stock across [start_date, end_date] — the Monthly Expiry
    Breakout filter's per-symbol scan. Reuses ExpiryBreakoutEngine's cycle
    detection; the candle's close must ALSO clear every EMA 20/50/100/200
    on the same timeframe (above all for BUY, below all for SELL).
    ema_touch additionally gates on whether the candle touched those
    EMAs: 'touch' (default) requires touching at least one; 'not_touch'
    requires touching none; 'both' applies no touch condition — unlike
    the single-symbol backtest this ignores SL/Target and just lists
    every raw signal hit."""
    # The EMA gate needs ~200 bars of warmup history on the scan's own
    # timeframe REGARDLESS of how narrow [start_date, end_date] is (e.g. a
    # single day) — too short a lookback leaves EMA200 permanently NaN and
    # silently blocks every candle. Daily: ~200 trading days ≈ 300+
    # calendar days when timeframe='day' (its EMAs need daily warmup);
    # otherwise just enough to anchor the prior cycle's expiry.
    span_days = (end_date - start_date).days
    lookback_days = span_days + (320 if timeframe == 'day' else 60)
    daily_data = cpr_service.get_hist_data(symbol, days=lookback_days, interval='day', end_date=end_date)
    if daily_data is None or len(daily_data) < 20:
        return []

    # Hourly: ~200 hourly bars ≈ 40+ trading days ≈ 60+ calendar days.
    hourly_days = span_days + 70
    interval = 'day' if timeframe == 'day' else '60minute'
    hourly_data = (daily_data if timeframe == 'day'
                   else cpr_service.get_hist_data(symbol, days=hourly_days, interval=interval, end_date=end_date))
    if hourly_data is None or hourly_data.empty:
        return []

    from trading_app.Backtest.expiry_breakout_engine import ExpiryBreakoutEngine
    ma_timeframe = '1day' if timeframe == 'day' else '1hour'
    engine = ExpiryBreakoutEngine(daily_df=daily_data, hourly_df=hourly_data,
                                   ma_timeframe=ma_timeframe, ema_touch=ema_touch)
    start_iso = start_date.date().isoformat()
    signals = []
    for sig in engine.scan_hl_signals():
        if sig['time'][:10] < start_iso:
            continue
        sig['symbol'] = symbol
        signals.append(sig)
    return signals


def filter_expiry_hl_breakout_range(cpr_service: "CPRFilterService", start_date: datetime, end_date: datetime,
                                     timeframe: str = '60minute', ema_touch: str = 'touch') -> Dict:
    """Scans all F&O stocks for every expiry High/Low breakout signal
    across [start_date, end_date] on the selected timeframe. Returns
    {'buy': [...], 'sell': [...]}, each entry one signal occurrence
    (a stock can appear many times)."""
    stocks = cpr_service.get_fo_stocks()
    timeframe = timeframe if timeframe in ('60minute', 'day') else '60minute'
    ema_touch = ema_touch if ema_touch in ('touch', 'not_touch', 'both') else 'touch'

    buy_signals: List[Dict] = []
    sell_signals: List[Dict] = []
    start_time = time.time()

    workers_count = cpr_service.MAX_WORKERS
    if cpr_service.kite.__class__.__name__ == 'FyersDataServiceAdapter':
        workers_count = 15

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {
            executor.submit(get_expiry_hl_signals_in_range, cpr_service, symbol, start_date, end_date, timeframe, ema_touch): symbol
            for symbol in stocks
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                for sig in future.result(timeout=60):
                    (buy_signals if sig['direction'] == 'BUY' else sell_signals).append(sig)
            except Exception as e:
                logger.error(f"Expiry H/L breakout range scan failed for {symbol}: {e}")

    logger.info(
        f"Expiry H/L breakout range scan ({timeframe}, ema_touch={ema_touch}, {start_date.date()}..{end_date.date()}) complete: "
        f"{len(buy_signals)} BUY, {len(sell_signals)} SELL in {time.time() - start_time:.1f}s"
    )
    return {
        'buy':  sorted(buy_signals, key=lambda x: x['time'], reverse=True),
        'sell': sorted(sell_signals, key=lambda x: x['time'], reverse=True),
    }


def filter_expiry_hl_breakout(cpr_service: "CPRFilterService", root_date: Optional[datetime] = None,
                               timeframe: str = '60minute', ema_touch: str = 'touch') -> Dict:
    """Scans all F&O stocks for a monthly-expiry-cycle High/Low breakout on
    the selected timeframe, under the Monthly Expiry Breakout filter's
    rule (see get_expiry_hl_signal()). Returns {'buy': [...], 'sell': [...]}."""
    stocks = cpr_service.get_fo_stocks()
    if root_date is None:
        root_date = datetime.now()
    if root_date.weekday() == 5:  # Saturday
        root_date = root_date - timedelta(days=1)
    elif root_date.weekday() == 6:  # Sunday
        root_date = root_date - timedelta(days=2)

    timeframe = timeframe if timeframe in ('60minute', 'day') else '60minute'
    ema_touch = ema_touch if ema_touch in ('touch', 'not_touch', 'both') else 'touch'

    # Today's intraday cache entries go stale as new hourly candles form;
    # clear them so the scan sees the latest finished candle, not whatever
    # was cached earlier in the day (e.g. the first 1-hour candle at 9:15).
    if root_date.date() == datetime.now().date():
        with cpr_service._cache_lock:
            keys_to_del = [k for k in cpr_service._historical_data_cache.keys() if str(root_date.date()) in k]
            for k in keys_to_del:
                del cpr_service._historical_data_cache[k]

    buy_signals: List[Dict] = []
    sell_signals: List[Dict] = []
    start_time = time.time()

    workers_count = cpr_service.MAX_WORKERS
    if cpr_service.kite.__class__.__name__ == 'FyersDataServiceAdapter':
        workers_count = 15

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {
            executor.submit(get_expiry_hl_signal, cpr_service, symbol, root_date, timeframe, ema_touch): symbol
            for symbol in stocks
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result(timeout=25)
                if result:
                    (buy_signals if result['direction'] == 'BUY' else sell_signals).append(result)
            except Exception as e:
                logger.error(f"Expiry H/L breakout failed for {symbol}: {e}")

    logger.info(
        f"Expiry H/L breakout scan ({timeframe}) complete: "
        f"{len(buy_signals)} BUY, {len(sell_signals)} SELL in {time.time() - start_time:.1f}s"
    )
    return {
        'buy': sorted(buy_signals, key=lambda x: x['symbol']),
        'sell': sorted(sell_signals, key=lambda x: x['symbol']),
    }
