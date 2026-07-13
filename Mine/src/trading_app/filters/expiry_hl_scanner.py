"""
Expiry High/Low Breakout Scanner.

Anchors on each F&O stock's current monthly-expiry-cycle daily High/Low
(the same detection ExpiryBreakoutEngine uses for the backtest) and checks
whether the latest candle on the selected timeframe ('60minute' | 'day')
has crossed it: a genuine touch (candle range straddles the level) AND a
close beyond it — BUY above the expiry High, SELL below the expiry Low.

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
                          timeframe: str = '60minute') -> Optional[Dict]:
    """Returns a BUY/SELL signal payload for a single stock, or None if
    neither the expiry High nor Low has been crossed."""
    daily_data = cpr_service.get_hist_data(symbol, days=200, interval='day', end_date=root_date)
    if daily_data is None or len(daily_data) < 20:
        return None

    from trading_app.Backtest.expiry_breakout_engine import ExpiryBreakoutEngine
    levels = ExpiryBreakoutEngine(daily_df=daily_data).expiry_levels()
    if not levels:
        return None
    latest_level = levels[-1]
    exp_high = latest_level['high']
    exp_low = latest_level['low']

    if timeframe == 'day':
        candle_df = daily_data
    else:
        candle_df = cpr_service.get_hist_data(symbol, days=10, interval='60minute', end_date=root_date)
    if candle_df is None or candle_df.empty:
        return None

    last_candle = candle_df.iloc[-1]
    c_high, c_low, c_close = float(last_candle['high']), float(last_candle['low']), float(last_candle['close'])

    direction = None
    if c_low <= exp_high <= c_high and c_close > exp_high:
        direction = 'BUY'
    elif c_low <= exp_low <= c_high and c_close < exp_low:
        direction = 'SELL'
    if direction is None:
        return None

    return {
        'symbol': symbol,
        'direction': direction,
        'current_price': round(c_close, 2),
        'expiry_high': round(exp_high, 2),
        'expiry_low': round(exp_low, 2),
        'expiry_date': latest_level['expiry_date'],
    }


def filter_expiry_hl_breakout(cpr_service: "CPRFilterService", root_date: Optional[datetime] = None,
                               timeframe: str = '60minute') -> Dict:
    """Scans all F&O stocks for a monthly-expiry-cycle High/Low breakout on
    the selected timeframe. Returns {'buy': [...], 'sell': [...]}."""
    stocks = cpr_service.get_fo_stocks()
    if root_date is None:
        root_date = datetime.now()
    if root_date.weekday() == 5:  # Saturday
        root_date = root_date - timedelta(days=1)
    elif root_date.weekday() == 6:  # Sunday
        root_date = root_date - timedelta(days=2)

    timeframe = timeframe if timeframe in ('60minute', 'day') else '60minute'

    buy_signals: List[Dict] = []
    sell_signals: List[Dict] = []
    start_time = time.time()

    workers_count = cpr_service.MAX_WORKERS
    if cpr_service.kite.__class__.__name__ == 'FyersDataServiceAdapter':
        workers_count = 15

    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        futures = {
            executor.submit(get_expiry_hl_signal, cpr_service, symbol, root_date, timeframe): symbol
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
