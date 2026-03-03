import logging
from kiteconnect import KiteConnect
import pandas as pd
from datetime import datetime, timedelta, date
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional, Tuple, Union
import re
import threading
import time
import random
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

# Global singleton cache for instruments (shared across all KiteService instances)
_global_instruments_cache = {
    'nse': None,
    'nfo': None,
    'tokens_by_symbol': {},
    'tokens_by_name': {},
    'cache_date': None,
    'lock': threading.Lock()
}

# Global historical data cache with LRU eviction
class HistoricalDataCache:
    """Thread-safe LRU cache for historical data with TTL."""
    
    def __init__(self, max_size: int = 500, default_ttl: int = 300):
        self._cache: OrderedDict[str, Tuple[Any, float, int]] = OrderedDict()  # key -> (data, timestamp, ttl)
        self._lock = threading.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
    
    def _make_key(self, token: int, from_date: str, to_date: str, interval: str) -> str:
        return f"{token}:{from_date}:{to_date}:{interval}"
    
    def get(self, token: int, from_date: str, to_date: str, interval: str) -> Optional[pd.DataFrame]:
        key = self._make_key(token, from_date, to_date, interval)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            data, timestamp, ttl = self._cache[key]
            if time.time() - timestamp > ttl:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Move to end (LRU)
            self._cache.move_to_end(key)
            self._hits += 1
            return data.copy() if isinstance(data, pd.DataFrame) else data
    
    def set(self, token: int, from_date: str, to_date: str, interval: str, 
            data: pd.DataFrame, ttl: Optional[int] = None) -> None:
        key = self._make_key(token, from_date, to_date, interval)
        ttl = ttl or self._default_ttl
        
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            
            self._cache[key] = (data, time.time(), ttl)
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                'size': len(self._cache),
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': f'{hit_rate:.1f}%'
            }
    
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0


# Global cache instance
_historical_cache = HistoricalDataCache(max_size=500, default_ttl=300)

# GLOBAL rate limiter (shared across ALL Kite API calls in entire application)
# Kite Historical API limit: 3 requests/second
class GlobalRateLimiter:
    """
    Thread-safe global rate limiter for Kite API.
    
    IMPORTANT: Kite Historical API allows only 3 requests/second.
    This limiter ensures ALL API calls across the entire application
    respect this limit by using a single shared instance.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        self._request_lock = threading.Lock()
        self._last_request_ts = 0.0
        self._min_gap = 0.5  # 0.5s = 2 req/sec (conservative, Kite limit is 3/sec)
        self._request_count = 0
    
    def wait(self) -> None:
        """Block until it's safe to make another request."""
        with self._request_lock:
            now = time.time()
            elapsed = now - self._last_request_ts
            if elapsed < self._min_gap:
                sleep_time = self._min_gap - elapsed
                time.sleep(sleep_time)
            self._last_request_ts = time.time()
            self._request_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            'total_requests': self._request_count,
            'min_gap': self._min_gap,
            'last_request': self._last_request_ts
        }

# Singleton instance - import this in other modules
_global_rate_limiter = GlobalRateLimiter()

def get_global_rate_limiter() -> GlobalRateLimiter:
    """Get the global rate limiter instance. Use this in all Kite API calls."""
    return _global_rate_limiter

class KiteService:
    def __init__(self, kite_instance: Optional[KiteConnect] = None) -> None:
        """
        Initializes the KiteService with optimized caching.
        """
        self.kite: KiteConnect = kite_instance or self._create_kite_instance()
        self.instruments: Optional[List[Dict[str, Any]]] = None
        self._instrument_tokens_by_symbol: Dict[str, int] = {}
        self._instrument_tokens_by_name: Dict[str, int] = {}
        self._nfo_instruments_cache: Optional[List[Dict[str, Any]]] = None
        self._nfo_cache_asof: Optional[date] = None
        self._nfo_option_symbol_cache: Dict[str, str] = {}  # Cache for option symbol lookups
        self._quote_cache: Dict[str, tuple] = {}  # Cache for quotes: {key: (price, timestamp)}
        self._quote_cache_ttl = 5  # Quote cache TTL in seconds
        self._quote_lock = threading.Lock()  # Thread-safe quote cache
        
        # Use global cache if available (optimization: avoid repeated API calls)
        self._load_instruments_from_cache_or_api()
    
    def _load_instruments_from_cache_or_api(self):
        """Load instruments from global cache or API (once per day)."""
        global _global_instruments_cache
        today = date.today()
        
        with _global_instruments_cache['lock']:
            # Check if cache is valid (same day)
            if _global_instruments_cache['cache_date'] == today and _global_instruments_cache['nse']:
                # Use cached data
                self.instruments = (_global_instruments_cache['nse'] or []) + (_global_instruments_cache['nfo'] or [])
                self._instrument_tokens_by_symbol = _global_instruments_cache['tokens_by_symbol'].copy()
                self._instrument_tokens_by_name = _global_instruments_cache['tokens_by_name'].copy()
                self._nfo_instruments_cache = _global_instruments_cache['nfo']
                self._nfo_cache_asof = today
                logging.info(f"[KiteService] Using cached instruments ({len(self.instruments)} total)")
                return
        
        # Cache miss - load from API
        self._load_instruments()
    
    def _load_instruments(self):
        """Loads and processes instruments into lookup dictionaries. Includes both NSE and NFO."""
        global _global_instruments_cache
        try:
            # Load NSE instruments (for indices like NIFTY, BANKNIFTY)
            nse_instruments = self.kite.instruments('NSE')
            logging.info(f"[_load_instruments] Loaded {len(nse_instruments) if nse_instruments else 0} NSE instruments")
            
            # Load NFO instruments (for futures and options) - also cache separately for option lookups
            nfo_instruments = self.kite.instruments('NFO')
            logging.info(f"[_load_instruments] Loaded {len(nfo_instruments) if nfo_instruments else 0} NFO instruments")
            self._nfo_instruments_cache = nfo_instruments  # Cache for fast option symbol lookups
            self._nfo_cache_asof = date.today()
            
            # Combine both
            all_instruments = (nse_instruments or []) + (nfo_instruments or [])
            self.instruments = all_instruments
            
            # Build lookup dictionaries
            for instrument in all_instruments:
                symbol = instrument.get('tradingsymbol')
                name = instrument.get('name')
                token = instrument.get('instrument_token')
                if symbol and token:
                    self._instrument_tokens_by_symbol[symbol] = token
                if name and token:
                    self._instrument_tokens_by_name[name.lower()] = token
            
            logging.info(f"[_load_instruments] Built lookup: {len(self._instrument_tokens_by_symbol)} symbols, {len(self._instrument_tokens_by_name)} names")
            
            # Update global cache for sharing across instances
            with _global_instruments_cache['lock']:
                _global_instruments_cache['nse'] = nse_instruments
                _global_instruments_cache['nfo'] = nfo_instruments
                _global_instruments_cache['tokens_by_symbol'] = self._instrument_tokens_by_symbol.copy()
                _global_instruments_cache['tokens_by_name'] = self._instrument_tokens_by_name.copy()
                _global_instruments_cache['cache_date'] = date.today()
                logging.info(f"[_load_instruments] Updated global cache")
                
        except Exception as e:
            logging.error(f"Error loading instruments: {e}")
    

    
    def _create_kite_instance(self) -> KiteConnect:
        """Creates and configures the KiteConnect instance."""
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        kite = KiteConnect(api_key=api_key)
        
        # Set timeout for HTTP requests (default is 7s, increase to 30s for chart data)
        # This affects all API calls made through KiteConnect
        kite.timeout = 30
        
        if access_token and isinstance(access_token, str) and access_token.strip():
            kite.set_access_token(access_token)
        else:
            logging.error("ACCESS_TOKEN not found or empty. Kite access may be restricted.")
            
        return kite
    
    def get_instrument_token(self, symbol: str) -> Optional[int]:
        """Get instrument token for NSE equity or indices, including FINNIFTY."""
        try:
            # Hardcoded tokens for indices (in case lookup fails)
            HARDCODED_TOKENS = {
                'NIFTY': 256265.0,      # NIFTY 50
                'BANKNIFTY': 260105.0,  # NIFTY Bank
                'FINNIFTY': 257801.0    # NIFTY FIN SERVICE
            }
            
            # First try to get from loaded instruments
            token = self._instrument_tokens_by_symbol.get(symbol)
            if token:
                return token

            # Improved index lookup including FINNIFTY
            if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
                search_name = symbol.lower().replace('nifty', 'nifty ').strip()
                if symbol == 'NIFTY': search_name = 'nifty 50'
                elif symbol == 'BANKNIFTY': search_name = 'nifty bank'
                elif symbol == 'FINNIFTY': search_name = 'nifty fin service'
                
                token = self._instrument_tokens_by_name.get(search_name)
                if token:
                    return token
                
                # Fallback to hardcoded token for known indices
                if symbol in HARDCODED_TOKENS:
                    hardcoded_token = HARDCODED_TOKENS[symbol]
                    logging.warning(f"[get_instrument_token] {symbol}: Lookup failed, using hardcoded token: {hardcoded_token}")
                    return int(hardcoded_token)
            
            logging.warning(f"No instrument found for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting instrument token for {symbol}: {e}")
            return None
    
    def get_current_ltp(self, symbol: str) -> Optional[float]:
        """Get current Last Traded Price (LTP) for a symbol."""
        try:
            # Map symbol to NSE instrument key
            if symbol == 'NIFTY':
                instrument_key = 'NSE:NIFTY 50'
            elif symbol == 'BANKNIFTY':
                instrument_key = 'NSE:NIFTY BANK'
            elif symbol == 'FINNIFTY':
                instrument_key = 'NSE:NIFTY FIN SERVICE'
            else:
                instrument_key = f'NSE:{symbol}'
            
            # Fetch LTP data
            ltp_data = self.kite.ltp([instrument_key])
            if ltp_data and isinstance(ltp_data, dict) and instrument_key in ltp_data:
                ltp = ltp_data[instrument_key].get('last_price')
                if ltp:
                    return float(ltp)
            
            logging.warning(f"Could not fetch LTP for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting current LTP for {symbol}: {e}")
            return None
    
    def get_previous_close(self, symbol: str) -> Optional[float]:
        """Get previous day's close price (PDC) for a symbol."""
        try:
            # Map symbol to NSE instrument key
            if symbol == 'NIFTY':
                instrument_key = 'NSE:NIFTY 50'
            elif symbol == 'BANKNIFTY':
                instrument_key = 'NSE:NIFTY BANK'
            elif symbol == 'FINNIFTY':
                instrument_key = 'NSE:NIFTY FIN SERVICE'
            else:
                instrument_key = f'NSE:{symbol}'
            
            # Fetch quote which contains previous close
            quote_data = self.kite.quote([instrument_key])
            if quote_data and isinstance(quote_data, dict) and instrument_key in quote_data:
                quote_item = quote_data[instrument_key]
                if isinstance(quote_item, dict):
                    ohlc = quote_item.get('ohlc', {})
                    pdc = ohlc.get('close')
                    if pdc:
                        return float(pdc)
            
            logging.warning(f"Could not fetch previous close for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting previous close for {symbol}: {e}")
            return None
    
    def get_previous_trading_day_close(self, symbol: str, target_date: Optional[str] = None) -> Optional[float]:
        """
        Get the previous trading day's close price using historical data.
        
        Handles weekends and market holidays by going back in time until finding a trading day.
        Excludes today to ensure we only get COMPLETE trading days.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY', 'BANKNIFTY')
                target_date: Optional date string in YYYY-MM-DD format. If provided, returns close price from 
                        the PREVIOUS trading day (before the target_date).
        
        Returns:
            Previous trading day's close price, or None if not available
        """
        try:
            # Get the instrument token for the symbol
            instrument_token = self.get_instrument_token(symbol)
            
            # If target_date is provided, MUST use historical data (quote API only has current price)
            if target_date:
                if not instrument_token:
                    logging.error(f"[get_previous_trading_day_close] Cannot fetch historical data for {target_date}: no instrument token found for {symbol}")
                    logging.error(f"[get_previous_trading_day_close] Quote API fallback NOT available for historical dates (only returns current price)")
                    return None
                
                try:
                    # Parse target_date (format: YYYY-MM-DD)
                    target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
                    
                    # IMPORTANT: Fetch data BEFORE the target date to get previous day's close
                    # Fetch from 30 days before target to 1 day before target (covers weekends/holidays)
                    from_date = datetime.combine(target_dt - timedelta(days=30), datetime.min.time())
                    to_date = datetime.combine(target_dt - timedelta(days=1), datetime.max.time())
                    
                    logging.info(f"[get_previous_trading_day_close] {symbol} (target_date={target_date})")
                    logging.info(f"[get_previous_trading_day_close]   Fetching from {from_date.date()} to {to_date.date()}")
                    
                    historical_data = self.kite.historical_data(
                        instrument_token=int(instrument_token),
                        from_date=from_date,
                        to_date=to_date,
                        interval='day'
                    )
                    
                    if historical_data:
                        # Sort by date in descending order to get the most recent trading day before target_date
                        sorted_data = sorted(historical_data, key=lambda x: x['date'], reverse=True)
                        
                        # Log available dates for debugging
                        logging.info(f"[get_previous_trading_day_close]   Found {len(sorted_data)} trading days")
                        for i, data in enumerate(sorted_data[:3]):
                            data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                            logging.info(f"[get_previous_trading_day_close]     [{i}] {data_date}: {data['close']:.2f}")
                        
                        # Get the first entry (most recent) which should be the previous trading day
                        most_recent_data = sorted_data[0]
                        most_recent_date = most_recent_data['date'].date() if hasattr(most_recent_data['date'], 'date') else most_recent_data['date']
                        close_price = float(most_recent_data['close'])
                        
                        logging.info(f"[get_previous_trading_day_close] ✓ target_date={target_date} → using {most_recent_date} close: {close_price:.2f}")
                        return close_price
                    else:
                        logging.warning(f"[get_previous_trading_day_close] No historical data found for target_date {target_date}")
                        return None
                except Exception as e:
                    logging.error(f"[get_previous_trading_day_close] Error fetching historical data for target_date {target_date}: {e}", exc_info=True)
                    return None
            
            # If NO target_date provided and NO instrument token, return None
            if not instrument_token:
                logging.error(f"[get_previous_trading_day_close] Could not find token for {symbol}")
                return None
            
            # Original logic for current date (when target_date is None and we have an instrument token)
            # IMPORTANT: Exclude today's incomplete data
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            
            # Fetch from 20 days ago to yesterday (covers weekends/holidays)
            to_date = datetime.combine(yesterday, datetime.max.time())
            from_date = to_date - timedelta(days=20)
            
            logging.info(f"[get_previous_trading_day_close] {symbol} (current date)")
            logging.info(f"[get_previous_trading_day_close]   Today: {today}, Yesterday: {yesterday}")
            logging.info(f"[get_previous_trading_day_close]   Fetching from {from_date.date()} to {to_date.date()}")
            
            try:
                historical_data = self.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval='day'
                )
                logging.info(f"[get_previous_trading_day_close]   Retrieved {len(historical_data) if historical_data else 0} days of data")
                
                if historical_data:
                    logging.info(f"[get_previous_trading_day_close]   === RAW DATA ===")
                    for i, data in enumerate(historical_data[-3:]):  # Show last 3
                        data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                        logging.info(f"[get_previous_trading_day_close]     {data_date}: {data['close']:.2f}")
            except Exception as api_err:
                logging.error(f"[get_previous_trading_day_close]   Error fetching: {api_err}", exc_info=True)
                return None
            
            if not historical_data:
                logging.warning(f"[get_previous_trading_day_close]   No historical data returned for {symbol}")
                return None
            
            # Sort by date in DESCENDING order to get the most recent
            sorted_data = sorted(historical_data, key=lambda x: x['date'], reverse=True)
            
            logging.info(f"[get_previous_trading_day_close]   === AFTER SORTING ===")
            for i, data in enumerate(sorted_data[:3]):
                data_date = data['date'].date() if hasattr(data['date'], 'date') else data['date']
                logging.info(f"[get_previous_trading_day_close]     [{i}] {data_date}: {data['close']:.2f}")
            
            if len(sorted_data) > 0:
                # Get the most recent trading day's close
                previous_day_data = sorted_data[0]
                previous_close = float(previous_day_data['close'])
                previous_date = previous_day_data['date'].date() if hasattr(previous_day_data['date'], 'date') else previous_day_data['date']
                
                logging.info(f"[get_previous_trading_day_close]   Selected: {previous_date}, Close={previous_close:.2f}")
                logging.info(f"[get_previous_trading_day_close]   Today={today}, Previous={previous_date}, Same? {previous_date == today}")
                
                # Verify we're not getting today's data
                if previous_date == today:
                    logging.warning(f"[get_previous_trading_day_close] ⚠️  Got today's data! Using second-most-recent")
                    if len(sorted_data) > 1:
                        previous_day_data = sorted_data[1]
                        previous_close = float(previous_day_data['close'])
                        previous_date = previous_day_data['date'].date() if hasattr(previous_day_data['date'], 'date') else previous_day_data['date']
                        logging.info(f"[get_previous_trading_day_close]   Fallback to: {previous_date}, Close={previous_close:.2f}")
                    else:
                        logging.error(f"[get_previous_trading_day_close] ❌ Only have today's data")
                        return None
                
                logging.info(f"[get_previous_trading_day_close] ✓ FINAL: {symbol} on {previous_date}: {previous_close:.2f}")
                return previous_close
            else:
                logging.warning(f"[get_previous_trading_day_close]   No data after sorting")
                return None
        except Exception as e:
            logging.error(f"[get_previous_trading_day_close] Exception: {e}", exc_info=True)
            return None

    def get_fo_stocks(self) -> List[str]:
        """Get list of F&O underlying stocks, including FUTURES and OPTIONS."""
        try:
            nfo_instruments = self.kite.instruments('NFO')
            fo_symbols = set()
            
            for inst in nfo_instruments:
                # FIX: Check for both FUTURES and OPTIONS for comprehensive underlying list
                if inst.get('instrument_type') in ['FUT', 'OPT']: 
                    tsymbol = inst.get('tradingsymbol', '')
                    if tsymbol:
                        # 're' is now imported at the top
                        match = re.match(r'^([A-Z]+)', tsymbol) 
                        if match and len(match.group(1)) > 1:
                            fo_symbols.add(match.group(1))
            
            fo_list = sorted(list(fo_symbols))
            
            # Ensure indices are at the top and avoid duplicates
            indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'] # FINNIFTY added
            result = [s for s in indices if s in fo_list or s in indices] 
            for symbol in fo_list:
                if symbol not in result:
                    result.append(symbol)
            
            return result

        except Exception as e:
            logging.error(f"Error getting F&O stocks: {e}")
            return []
    
    # ========== RATE LIMITING ==========
    _rate_lock = threading.Lock()
    _last_request_ts = 0.0
    
    def _respect_rate_limit(self, min_gap: float = 0.1) -> None:
        """Ensure minimum gap between API requests to avoid rate limiting."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_ts
            if elapsed < min_gap:
                time.sleep(min_gap - elapsed)
            self._last_request_ts = time.time()
    
    # ========== IMPROVED HISTORICAL DATA FETCHING ==========
    
    def _historical_with_retry(
        self, 
        instrument_token: int, 
        from_date: datetime, 
        to_date: datetime, 
        interval: str, 
        max_retries: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical data with exponential backoff retry.
        
        Features:
        - Global rate limiting to prevent 429 errors
        - Exponential backoff with jitter (longer for rate limits)
        - Proper error categorization
        """
        global _global_rate_limiter
        attempt = 0
        
        while True:
            try:
                # Use GLOBAL rate limiter (shared across all instances)
                _global_rate_limiter.wait()
                
                data = self.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval
                )
                return data or []
                
            except Exception as e:
                msg = str(e) if e else "Unknown error"
                is_rate_limit = 'Too many requests' in msg or '429' in msg
                
                if attempt >= max_retries:
                    logging.error(f"historical_data failed after {max_retries} retries for token {instrument_token}: {msg}")
                    return []
                
                # Longer backoff for rate limits, shorter for other errors
                if is_rate_limit:
                    # Rate limit: start at 2s, max 30s
                    base = 2.0 * (2 ** attempt)
                    sleep_seconds = min(30.0, base + random.uniform(0, 1.0))
                    logging.warning(f"Rate limited (attempt {attempt+1}/{max_retries}). Backing off {sleep_seconds:.1f}s")
                else:
                    # Other errors: start at 0.5s, max 8s
                    base = 0.5 * (2 ** attempt)
                    sleep_seconds = min(8.0, base + random.uniform(0, 0.4))
                    logging.warning(f"Error (attempt {attempt+1}/{max_retries}) for token {instrument_token}: {msg}. Backing off {sleep_seconds:.1f}s")
                
                time.sleep(sleep_seconds)
                attempt += 1
    
    def get_historical_data(
        self, 
        symbol: str, 
        from_date: Union[datetime, str], 
        to_date: Union[datetime, str], 
        interval: str = 'day',
        use_cache: bool = True,
        cache_ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetches historical data with caching and retry logic.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY 50', 'RELIANCE')
            from_date: Start date (datetime or 'YYYY-MM-DD' string)
            to_date: End date (datetime or 'YYYY-MM-DD' string)
            interval: Candle interval ('minute', '3minute', '5minute', '15minute', '30minute', '60minute', 'day')
            use_cache: Whether to use cached data (default: True)
            cache_ttl: Cache TTL in seconds (default: 300 for day, 60 for intraday)
        
        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        global _historical_cache
        
        try:
            token = self.get_instrument_token(symbol)
            if not token:
                logging.warning(f"No token found for symbol: {symbol}")
                return None
            
            # Normalize dates
            if isinstance(from_date, str):
                from_date = datetime.strptime(from_date, '%Y-%m-%d')
            if isinstance(to_date, str):
                to_date = datetime.strptime(to_date, '%Y-%m-%d')
            
            from_str = from_date.strftime('%Y-%m-%d')
            to_str = to_date.strftime('%Y-%m-%d')
            
            # Determine cache TTL based on interval
            if cache_ttl is None:
                cache_ttl = 60 if 'minute' in interval else 300
            
            # Check cache
            if use_cache:
                cached = _historical_cache.get(token, from_str, to_str, interval)
                if cached is not None:
                    logging.debug(f"Cache hit for {symbol} ({interval})")
                    return cached
            
            # Fetch from API with retry
            data = self._historical_with_retry(token, from_date, to_date, interval)
            
            if not data:
                return None
            
            df = pd.DataFrame(data)
            
            # Normalize date column
            if 'date' in df.columns:
                try:
                    df['date'] = pd.to_datetime(df['date'])
                    if hasattr(df['date'], 'dt'):
                        df['date'] = df['date'].dt.tz_localize(None)
                except Exception:
                    pass
            
            # Cache the result
            if use_cache:
                _historical_cache.set(token, from_str, to_str, interval, df, cache_ttl)
            
            return df
            
        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            return None
    
    def get_historical_data_batch(
        self,
        requests: List[Dict[str, Any]],
        max_workers: int = 4
    ) -> Dict[str, Optional[pd.DataFrame]]:
        """
        Fetch historical data for multiple symbols in parallel.
        
        Args:
            requests: List of dicts with keys: symbol, from_date, to_date, interval (optional)
                     Example: [{'symbol': 'RELIANCE', 'from_date': '2026-01-01', 'to_date': '2026-03-01'}]
            max_workers: Number of parallel workers (default: 4)
        
        Returns:
            Dict mapping symbol to DataFrame (or None if failed)
        """
        results: Dict[str, Optional[pd.DataFrame]] = {}
        
        def fetch_one(req: Dict[str, Any]) -> Tuple[str, Optional[pd.DataFrame]]:
            symbol = req['symbol']
            from_date = req['from_date']
            to_date = req['to_date']
            interval = req.get('interval', 'day')
            
            df = self.get_historical_data(symbol, from_date, to_date, interval)
            return (symbol, df)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, req): req['symbol'] for req in requests}
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    sym, df = future.result()
                    results[sym] = df
                except Exception as e:
                    logging.error(f"Batch fetch failed for {symbol}: {e}")
                    results[symbol] = None
        
        return results
    
    def get_historical_data_by_token(
        self,
        token: int,
        from_date: Union[datetime, str],
        to_date: Union[datetime, str],
        interval: str = 'day',
        use_cache: bool = True,
        cache_ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical data directly by instrument token (faster, no symbol lookup).
        
        Use this when you already have the token from instruments cache.
        """
        global _historical_cache
        
        try:
            # Normalize dates
            if isinstance(from_date, str):
                from_date = datetime.strptime(from_date, '%Y-%m-%d')
            if isinstance(to_date, str):
                to_date = datetime.strptime(to_date, '%Y-%m-%d')
            
            from_str = from_date.strftime('%Y-%m-%d')
            to_str = to_date.strftime('%Y-%m-%d')
            
            # Determine cache TTL
            if cache_ttl is None:
                cache_ttl = 60 if 'minute' in interval else 300
            
            # Check cache
            if use_cache:
                cached = _historical_cache.get(token, from_str, to_str, interval)
                if cached is not None:
                    return cached
            
            # Fetch with retry
            data = self._historical_with_retry(token, from_date, to_date, interval)
            
            if not data:
                return None
            
            df = pd.DataFrame(data)
            
            # Normalize date column
            if 'date' in df.columns:
                try:
                    df['date'] = pd.to_datetime(df['date'])
                    if hasattr(df['date'], 'dt'):
                        df['date'] = df['date'].dt.tz_localize(None)
                except Exception:
                    pass
            
            # Cache result
            if use_cache:
                _historical_cache.set(token, from_str, to_str, interval, df, cache_ttl)
            
            return df
            
        except Exception as e:
            logging.error(f"Error fetching data for token {token}: {e}")
            return None
    
    @staticmethod
    def get_cache_stats() -> Dict[str, Any]:
        """Get historical data cache statistics."""
        global _historical_cache
        return _historical_cache.get_stats()
    
    @staticmethod
    def clear_historical_cache() -> None:
        """Clear the historical data cache."""
        global _historical_cache
        _historical_cache.clear()
        logging.info("Historical data cache cleared")
    
    def get_lot_size(self, symbol: str, exchange: str = 'NFO') -> int:
        """Get the lot size (quantity multiplier) for a symbol.
        
        Uses cached NFO instruments for fast lookup instead of fetching from API.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, FINNIFTY, etc.)
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Lot size (default: 1 if not found)
        """
        try:
            # Use cached NFO instruments instead of fetching from API
            instruments = self._nfo_instruments_cache
            
            # If cache is empty, load it once
            if not instruments:
                logging.warning("NFO instruments cache empty in get_lot_size, loading...")
                instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = instruments
            
            # Look for any option instrument with the given symbol to get lot size
            # All options for the same underlying have the same lot size
            for inst in instruments:
                # Check if this is an option for our symbol
                if (inst.get('name') == symbol and 
                    inst.get('instrument_type') in ['CE', 'PE', 'OPTIDX', 'OPTSTK']):
                    lot_size = inst.get('lot_size')
                    if lot_size and lot_size > 0:
                        logging.info(f"✓ Lot size for {symbol}: {lot_size} (from instruments)")
                        return int(lot_size)
            
            # If not found in loaded instruments, try using hardcoded fallback
            logging.warning(f"⚠️  Lot size not found in instruments for {symbol}, using fallback")
            
            # Default lot sizes (as of Jan 2026) - fallback only
            default_lots = {
                'NIFTY': 65,
                'BANKNIFTY': 25,
                'FINNIFTY': 40,
                'MIDCPNIFTY': 50,
                'SENSEX': 10,
                'BANKEX': 15
            }
            
            lot_size = default_lots.get(symbol, 1)
            logging.warning(f"Using fallback lot size {lot_size} for {symbol}")
            return lot_size
            
        except Exception as e:
            logging.error(f"Error getting lot size for {symbol}: {e}")
            # Emergency fallback
            return {'NIFTY': 65, 'BANKNIFTY': 25, 'FINNIFTY': 40}.get(symbol, 1)
    
    def _refresh_nfo_cache_if_stale(self, exchange: str = 'NFO') -> None:
        """Refresh NFO instruments daily or when cache is missing."""
        try:
            if (self._nfo_instruments_cache is None) or (self._nfo_cache_asof and self._nfo_cache_asof < date.today()):
                logging.info("[KiteService] Refreshing NFO instruments cache…")
                nfo_instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = nfo_instruments
                self._nfo_cache_asof = date.today()
                # Clear per-option cache to avoid stale expiries
                self._nfo_option_symbol_cache.clear()
        except Exception as e:
            logging.error(f"[KiteService] Failed to refresh NFO cache: {e}")

    def get_nearest_option_expiry(self, symbol: str, strike: int, option_type: str, exchange: str = 'NFO') -> Optional[date]:
        """Get the exact expiry date for an option from Kite instruments cache.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            exchange: Exchange (default: 'NFO')
            
        Returns:
            datetime.date of the nearest expiry, or None if not found
        """
        try:
            self._refresh_nfo_cache_if_stale(exchange)
            nfo_instruments = self._nfo_instruments_cache
            if not nfo_instruments:
                logging.warning("NFO instruments cache empty, loading...")
                nfo_instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = nfo_instruments
                from datetime import date as _date_cls
                self._nfo_cache_asof = _date_cls.today()
                
            matching_instruments = []
            for inst in nfo_instruments:
                if (inst.get('name') == symbol and
                    inst.get('instrument_type') == option_type and
                    inst.get('strike') == strike and
                    inst.get('expiry')):
                    
                    expiry_date = inst['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    
                    from datetime import date, time, datetime as dt
                    current_time = dt.now().time()
                    is_market_open = time(9, 15) <= current_time <= time(15, 20)
                    
                    if expiry_date == date.today():
                        if is_market_open:
                            matching_instruments.append(inst)
                    elif expiry_date > date.today():
                        matching_instruments.append(inst)
            
            if matching_instruments:
                matching_instruments.sort(key=lambda x: x['expiry'])
                
                # Prefer Thursday expiries (weekday() == 3 = Thursday in Python)
                # Regular index options expire on Thursdays
                thursday_instruments = [i for i in matching_instruments if i['expiry'].weekday() == 3]
                if thursday_instruments:
                    expiry = thursday_instruments[0]['expiry']
                    selected_note = "Thursday expiry (preferred)"
                else:
                    # Fallback to next available if no Thursday
                    expiry = matching_instruments[0]['expiry']
                    available_weekdays = [i['expiry'].weekday() for i in matching_instruments[:3]]
                    selected_note = f"Non-Thursday expiry fallback (available: {available_weekdays}, no Thu found)"
                
                expiry_date = expiry.date() if hasattr(expiry, 'date') else expiry
                available_expiries = [inst['expiry'].date() if hasattr(inst['expiry'], 'date') else inst['expiry'] 
                                    for inst in matching_instruments[:5]]
                logging.info(f"[KiteService] Found {len(matching_instruments)} instruments for {symbol} {option_type} {strike}. "
                           f"Available expiries: {available_expiries}. Returning: {expiry_date} (weekday={expiry_date.weekday()}, {selected_note})")
                return expiry_date
                
            return None
        except Exception as e:
            logging.error(f"Error getting option expiry for {symbol} {option_type} {strike}: {e}", exc_info=True)
            return None

    def get_option_symbol(self, symbol: str, strike: int, option_type: str, exchange: str = 'NFO') -> Optional[str]:
        """Get the trading symbol for an option.
        
        Uses cached NFO instruments for fast lookup instead of fetching from API.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            exchange: Exchange (default: 'NFO')
            
        Returns:
            Trading symbol or None if not found
        """
        try:
            # Create cache key for this lookup
            cache_key = f"{symbol}_{strike}_{option_type}"
            
            # Check if already cached
            if cache_key in self._nfo_option_symbol_cache:
                return self._nfo_option_symbol_cache[cache_key]
            
            self._refresh_nfo_cache_if_stale(exchange)

            # Use cached NFO instruments instead of fetching from API (saves ~10-15 seconds)
            nfo_instruments = self._nfo_instruments_cache

            # If cache is empty, load it once
            if not nfo_instruments:
                logging.warning("NFO instruments cache empty, loading...")
                nfo_instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = nfo_instruments
                self._nfo_cache_asof = date.today()
            
            matching_instruments = []
            for inst in nfo_instruments:
                if (inst.get('name') == symbol and
                    inst.get('instrument_type') == option_type and
                    inst.get('strike') == strike and
                    inst.get('expiry')):
                    
                    expiry_date = inst['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    
                    from datetime import time as time_cls, datetime as dt
                    # On expiry day: use current expiry if before 3:20 PM (market close)
                    # After 3:20 PM: use next expiry
                    current_time = dt.now().time()
                    is_market_open = time_cls(9, 15) <= current_time <= time_cls(15, 20)
                    
                    if expiry_date == date.today():
                        # Today's expiry - only use if market is still open (before 3:20 PM)
                        if is_market_open:
                            matching_instruments.append(inst)
                    elif expiry_date > date.today():
                        # Future expiry - always include
                        matching_instruments.append(inst)
            
            if matching_instruments:
                # Sort by expiry and get the nearest
                matching_instruments.sort(key=lambda x: x['expiry'])
                
                # Prefer weekly/special expiries over monthly expiries
                # Weekly: Thursdays, Special Mondays like March 2
                # Monthly: 24th of months (avoid these)
                from datetime import datetime as dt_class
                weekly_instruments = []
                for inst in matching_instruments:
                    exp = inst['expiry']
                    if hasattr(exp, 'date'):
                        exp = exp.date()
                    # Exclude 24th (monthly expiry)
                    if exp.day != 24:
                        weekly_instruments.append(inst)
                
                # Use weekly if available, otherwise fall back to all matches
                instruments_to_use = weekly_instruments if weekly_instruments else matching_instruments
                
                tradingsymbol = instruments_to_use[0]['tradingsymbol']
                # Cache the result for future lookups
                self._nfo_option_symbol_cache[cache_key] = tradingsymbol
                logging.debug(f"Found option symbol: {tradingsymbol} for {symbol} {option_type} {strike}")
                return tradingsymbol
            
            # If not found, force-refresh instruments once and retry to avoid stale cache
            try:
                logging.warning(f"No {option_type} option found for {symbol} strike {strike}, refreshing instruments and retrying once")
                nfo_instruments = self.kite.instruments(exchange)
                self._nfo_instruments_cache = nfo_instruments
                self._nfo_cache_asof = date.today()
                matching_instruments = []
                for inst in nfo_instruments:
                    if (inst.get('name') == symbol and
                        inst.get('instrument_type') == option_type and
                        inst.get('strike') == strike and
                        inst.get('expiry')):
                        expiry_date = inst['expiry']
                        if hasattr(expiry_date, 'date'):
                            expiry_date = expiry_date.date()
                        from datetime import time, datetime as dt
                        current_time = dt.now().time()
                        is_market_open = time(9, 15) <= current_time <= time(15, 20)
                        if expiry_date == date.today():
                            if is_market_open:
                                matching_instruments.append(inst)
                        elif expiry_date > date.today():
                            matching_instruments.append(inst)
                if matching_instruments:
                    matching_instruments.sort(key=lambda x: x['expiry'])
                    
                    # Prefer weekly/special expiries over monthly expiries
                    from datetime import date as date_class
                    weekly_instruments = []
                    for inst in matching_instruments:
                        exp = inst['expiry']
                        if hasattr(exp, 'date'):
                            exp = exp.date()
                        # Exclude 24th (monthly expiry)
                        if exp.day != 24:
                            weekly_instruments.append(inst)
                    
                    # Use weekly if available, otherwise fall back to all matches
                    instruments_to_use = weekly_instruments if weekly_instruments else matching_instruments
                    
                    tradingsymbol = instruments_to_use[0]['tradingsymbol']
                    self._nfo_option_symbol_cache[cache_key] = tradingsymbol
                    logging.info(f"[KiteService] Found option after refresh: {tradingsymbol}")
                    return tradingsymbol
            except Exception as retry_e:
                logging.error(f"[KiteService] Retry lookup failed: {retry_e}")

            logging.warning(f"No {option_type} option found for {symbol} strike {strike}")
            return None
            
        except Exception as e:
            logging.error(f"Error getting option symbol for {symbol} {option_type} {strike}: {e}", exc_info=True)
            return None
    
    def place_order(self, tradingsymbol: str, transaction_type: str, price: float, 
                   quantity: int = 65, product: str = 'NRML', order_type: str = 'MARKET',
                   exchange: str = 'NFO', trigger_price: Optional[float] = None) -> Dict[str, Any]:
        """Place an order in Zerodha Kite.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY25D26C25000')
            transaction_type: BUY or SELL (use kite.TRANSACTION_TYPE_BUY/SELL)
            price: Order price (execution price for LIMIT/stoploss orders)
            quantity: Order quantity (default: 75)
            product: Product type - NRML (normal/default), MIS (intraday), CNC (delivery)
            order_type: ORDER_TYPE_MARKET (default) or ORDER_TYPE_LIMIT
            exchange: Exchange - NFO (options), NSE (stocks)
            trigger_price: Trigger price for stoploss orders (optional)
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            # Check if market is open (9:15 AM to 3:30 PM IST)
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            # Determine order variety based on market hours
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
                order_time = "REGULAR"
            else:
                variety = self.kite.VARIETY_AMO
                order_time = "AMO"
            
            # Log with trigger_price info if provided
            if trigger_price:
                logging.info(f"Placing {order_time} {transaction_type} order: {tradingsymbol} @ ₹{price:.2f} (execute) with trigger @ ₹{trigger_price:.2f} x {quantity}")
            else:
                logging.info(f"Placing {order_time} {transaction_type} order: {tradingsymbol} @ ₹{price:.2f} x {quantity}")
            
            # Map product string to Kite constant
            product_map = {
                'MIS': self.kite.PRODUCT_MIS,
                'CNC': self.kite.PRODUCT_CNC,
                'NRML': self.kite.PRODUCT_NRML
            }
            product_type = product_map.get(product, self.kite.PRODUCT_MIS)
            
            # Map order type string to Kite constant
            order_type_map = {
                'LIMIT': self.kite.ORDER_TYPE_LIMIT,
                'MARKET': self.kite.ORDER_TYPE_MARKET
            }
            order_type_const = order_type_map.get(order_type, self.kite.ORDER_TYPE_LIMIT)
            
            # Map exchange string to Kite constant
            exchange_map = {
                'NFO': self.kite.EXCHANGE_NFO,
                'NSE': self.kite.EXCHANGE_NSE,
                'BSE': self.kite.EXCHANGE_BSE
            }
            exchange_const = exchange_map.get(exchange, self.kite.EXCHANGE_NFO)
            
            # Build order parameters
            order_params = {
                'variety': variety,
                'exchange': exchange_const,
                'tradingsymbol': tradingsymbol,
                'transaction_type': transaction_type,
                'quantity': quantity,
                'product': product_type,
                'order_type': order_type_const,
                'price': price
            }
            
            # Add trigger_price if provided (for stoploss orders)
            if trigger_price is not None and trigger_price > 0:
                order_params['trigger_price'] = trigger_price
                logging.info(f"[Stoploss] Adding trigger_price={trigger_price:.2f} to order parameters")
            
            # Place the order with all parameters
            order_id = self.kite.place_order(**order_params)
            
            if trigger_price:
                logging.info(f"✅ {order_time} Stoploss Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ Execute: ₹{price:.2f}, Trigger: ₹{trigger_price:.2f}")
            else:
                logging.info(f"✅ {order_time} Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ ₹{price:.2f}")
            
            return {
                'success': True,
                'order_id': order_id,
                'symbol': tradingsymbol,
                'price': price,
                'quantity': quantity,
                'transaction_type': transaction_type,
                'trigger_price': trigger_price if trigger_price else None
            }
            
        except Exception as e:
            logging.error(f"❌ Error placing order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol
            }
    
    def get_cached_quote(self, instrument_key: str, use_cache: bool = True) -> Optional[float]:
        """
        Fetch quote for an instrument with caching to reduce API calls.
        
        Args:
            instrument_key: Instrument key (e.g., 'NFO:NIFTY23JAN19100CE')
            use_cache: Whether to use cached price if available
            
        Returns:
            Last price as float, or None if unable to fetch
        """
        import time
        
        # Check cache if enabled
        if use_cache and instrument_key in self._quote_cache:
            price, timestamp = self._quote_cache[instrument_key]
            if time.time() - timestamp < self._quote_cache_ttl:
                logging.debug(f"Using cached price for {instrument_key}: {price}")
                return price
        
        try:
            quote = self.kite.quote(instrument_key)
            if isinstance(quote, dict) and instrument_key in quote:
                quote_item = quote[instrument_key]
                if isinstance(quote_item, dict):
                    price = quote_item.get('last_price')
                    if not price:
                        price = quote_item.get('close')
                    
                    if price:
                        # Cache the price
                        self._quote_cache[instrument_key] = (price, time.time())
                        return price
        except Exception as e:
            logging.warning(f"Error fetching quote for {instrument_key}: {e}")
            # Try to return cached price even if expired
            if instrument_key in self._quote_cache:
                price, _ = self._quote_cache[instrument_key]
                logging.warning(f"Using expired cached price for {instrument_key}: {price}")
                return price
        
        return None
    
    def get_current_prices_batch(self, tokens: List[int], max_retries: int = 3) -> Dict[int, Optional[float]]:
        """Fetch current LTP (Last Traded Price) for multiple tokens in a single API call.
        
        CRITICAL: Used by live signal monitoring to check prices every 30 seconds.
        Implements retry logic with exponential backoff for connection resilience.
        
        Args:
            tokens: List of instrument tokens to fetch
            max_retries: Maximum retry attempts on failure (default: 3)
            
        Returns:
            Dictionary mapping token -> price (or None if price unavailable)
        """
        import time
        from kiteconnect.exceptions import NetworkException, TokenException
        
        if not tokens:
            return {}
        
        retry_delay = 0.5  # Start with 500ms
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Format tokens for Kite API (NFO:token_number)
                quote_keys = [f"NFO:{token}" for token in tokens]
                quotes = self.kite.quote(quote_keys)
                
                result = {}
                for key, quote_data in quotes.items():
                    try:
                        # Handle both string keys (e.g., "NFO:12345") and numeric keys
                        if isinstance(key, str) and ':' in key:
                            token = int(key.split(':')[1])
                        else:
                            token = int(key)
                        result[token] = quote_data.get('last_price') if isinstance(quote_data, dict) else None
                    except (ValueError, KeyError, AttributeError, TypeError):
                        pass  # Skip if unable to parse
                
                logging.debug(f"Fetched prices for {len(result)} tokens")
                return result
                
            except TokenException:
                logging.warning(f"[Batch Price Fetch] Access token invalid (attempt {attempt + 1}/{max_retries})")
                last_error = "Token expired"
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    
            except NetworkException as e:
                logging.warning(f"[Batch Price Fetch] Network error (attempt {attempt + 1}/{max_retries}): {e}")
                last_error = f"Network error: {str(e)}"
                if attempt < max_retries - 1:
                    # Exponential backoff: 0.5s, 1s, 2s
                    time.sleep(retry_delay * (2 ** attempt))
                    
            except Exception as e:
                error_str = str(e).lower()
                # Check if error is retriable (connection reset, timeout, gateway errors)
                is_retriable = any(keyword in error_str for keyword in [
                    'connection reset', 'connection aborted', 'connection refused',
                    '504', '503', 'gateway', 'timeout', 'couldn\'t parse', 'json',
                    'broken pipe', 'reset by peer'
                ])
                
                if is_retriable and attempt < max_retries - 1:
                    logging.warning(f"[Batch Price Fetch] Retriable error (attempt {attempt + 1}/{max_retries}): {e}")
                    last_error = f"Retriable error: {str(e)}"
                    # Exponential backoff: 0.5s, 1s, 2s
                    time.sleep(retry_delay * (2 ** attempt))
                else:
                    logging.error(f"[Batch Price Fetch] Non-retriable error: {e}")
                    last_error = f"Error: {str(e)}"
                    break
        
        # After all retries exhausted, log and return empty dict with None values
        logging.error(f"[Batch Price Fetch] Failed after {max_retries} retries. Last error: {last_error}")
        return {token: None for token in tokens}

    
    def get_current_price(self, token: int) -> Optional[float]:
        """Fetch current LTP (Last Traded Price) for a given token.
        
        Args:
            token: Instrument token
            
        Returns:
            Current price or None if failed
        """
        prices = self.get_current_prices_batch([token])
        return prices.get(token)
    
    def place_option_order(self, symbol: str, strike: int, option_type: str, 
                          transaction_type: str, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Place an order for an option contract.
        
        Convenience method that combines option symbol lookup and order placement.
        Uses parallel operations to get lot size, option symbol, and price simultaneously.
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            transaction_type: BUY or SELL
            quantity: Order quantity (default: None - uses lot size from Kite)
            
        Returns:
            Dict with success status and order details
        """
        try:
            # Parallel operation: Get lot size, option symbol, and price at the same time
            from concurrent.futures import ThreadPoolExecutor
            
            def get_tradingsymbol():
                return self.get_option_symbol(symbol, strike, option_type)
            
            def get_price_for_symbol(tradingsymbol: str) -> Optional[float]:
                """Helper to get price with caching"""
                instrument_key = f'NFO:{tradingsymbol}'
                return self.get_cached_quote(instrument_key, use_cache=True)
            
            # Get lot size and trading symbol in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                lot_size_future = executor.submit(self.get_lot_size, symbol) if quantity is None else None
                tradingsymbol_future = executor.submit(get_tradingsymbol)
                
                if lot_size_future:
                    quantity = lot_size_future.result()
                tradingsymbol = tradingsymbol_future.result()
            
            if not tradingsymbol:
                return {
                    'success': False,
                    'error': f'Could not find {option_type} option for {symbol} strike {strike}',
                    'symbol': symbol,
                    'strike': strike,
                    'option_type': option_type
                }
            
            # Get price with caching (fast if cached)
            price = get_price_for_symbol(tradingsymbol)
            
            if not price:
                return {
                    'success': False,
                    'error': f'Could not determine price for {tradingsymbol}',
                    'symbol': tradingsymbol
                }
            
            # Ensure quantity is set
            if quantity is None:
                quantity = 1  # Fallback default
            
            # Place the order
            result = self.place_order(
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                price=price,
                quantity=quantity,
                product='NRML',
                order_type='MARKET',
                exchange='NFO'
            )
            
            if result['success']:
                result['option_type'] = option_type
                result['strike'] = strike
                result['underlying'] = symbol
            
            return result
            
        except Exception as e:
            logging.error(f"Error in place_option_order: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type
            }
    
    def place_stoploss_order(self, tradingsymbol: str, trigger_price: float, 
                            quantity: int, product: str = 'NRML') -> Dict[str, Any]:
        """Place a stop loss (sell) order on Zerodha Kite.
        
        Creates a sell order with a trigger price that automatically executes
        when the price drops to the trigger level. Uses REGULAR variety with trigger.
        
        Args:
            tradingsymbol: Trading symbol (e.g., 'NIFTY25D26C25000')
            trigger_price: SL trigger price (when to activate the order)
            quantity: Order quantity (default: 75)
            product: Product type - NRML (normal/default), MIS (intraday)
            
        Returns:
            Dict with success status, order_id, and details
        """
        try:
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
            else:
                variety = self.kite.VARIETY_AMO
            
            product_map = {
                'MIS': self.kite.PRODUCT_MIS,
                'CNC': self.kite.PRODUCT_CNC,
                'NRML': self.kite.PRODUCT_NRML
            }
            product_type = product_map.get(product, self.kite.PRODUCT_MIS)
            
            # For Zerodha stoploss orders:
            # - trigger_price: Price at which order becomes active
            # - price: Execution limit price (should be AT or VERY CLOSE to trigger)
            # 
            # CRITICAL: If execution price is too far below trigger, and current price 
            # is between execution and trigger, the order executes IMMEDIATELY!
            # 
            # Solution: Set execution price = trigger price (sell at trigger level)
            # This ensures the order ONLY executes when price hits the trigger level
            
            TICK_SIZE = 0.05
            
            # Round trigger_price to proper tick size
            trigger_price = round(int(trigger_price / TICK_SIZE) * TICK_SIZE, 2)
            
            # For SELL stoploss: execution_price = trigger_price
            # This ensures order executes AT the trigger level, not immediately
            execution_price = trigger_price
            
            logging.info(f"Placing SL order: {tradingsymbol} @ ₹{trigger_price:.2f} (trigger & execute) x {quantity}")
            
            # Place stop loss order with trigger price
            # For Zerodha Kite: Use SL order type (not LIMIT)
            # SL = Stop Loss order that waits for trigger_price before executing
            # The trigger_price parameter is critical - it tells Kite when to activate
            order_id = self.kite.place_order(
                variety=variety,
                exchange=self.kite.EXCHANGE_NFO,
                tradingsymbol=tradingsymbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=product_type,
                order_type=self.kite.ORDER_TYPE_SL,  # SL (Stop Loss) order type - CRITICAL!
                price=execution_price,  # Execution price (limit price when triggered)
                trigger_price=trigger_price  # Trigger price (when to activate the order)
            )
            
            logging.info(f"✅ SL Order placed successfully. Order ID: {order_id} | {tradingsymbol} @ ₹{trigger_price:.2f} (stoploss trigger)")
            
            return {
                'success': True,
                'order_id': order_id,
                'symbol': tradingsymbol,
                'trigger_price': trigger_price,
                'execution_price': execution_price,
                'quantity': quantity,
                'order_type': 'STOPLOSS'
            }
            
        except Exception as e:
            logging.error(f"❌ Error placing SL order for {tradingsymbol}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'symbol': tradingsymbol
            }
    
    def modify_stoploss_order(self, order_id: str, new_trigger_price: float, 
                             quantity: Optional[int] = None) -> Dict[str, Any]:
        """Modify an existing stop loss order with a new trigger price.
        
        Used for trailing SL - updates the trigger price as price moves favorably.
        
        Args:
            order_id: Order ID to modify
            new_trigger_price: New SL trigger price
            quantity: Optional quantity update
            
        Returns:
            Dict with success status and details
        """
        try:
            from datetime import time
            now = datetime.now().time()
            market_open = time(9, 15)
            market_close = time(15, 30)
            
            if market_open <= now <= market_close:
                variety = self.kite.VARIETY_REGULAR
            else:
                variety = self.kite.VARIETY_AMO
            
            logging.info(f"Modifying SL order {order_id} to trigger price: ₹{new_trigger_price:.2f}")
            
            # For modifying a LIMIT order with trigger, both price and trigger_price should match
            params = {
                'variety': variety,
                'order_id': order_id,
                'trigger_price': new_trigger_price,
                'price': new_trigger_price  # Use same price as trigger for SL orders
            }
            
            if quantity:
                params['quantity'] = quantity
            
            result_order_id = self.kite.modify_order(**params)
            
            logging.info(f"✅ SL Order modified successfully. Order ID: {result_order_id}")
            
            return {
                'success': True,
                'order_id': result_order_id,
                'new_trigger_price': new_trigger_price,
                'order_type': 'STOPLOSS_MODIFIED'
            }
            
        except Exception as e:
            logging.error(f"❌ Error modifying SL order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }
    
    def cancel_order(self, order_id: str, variety: str = 'regular') -> Dict[str, Any]:
        """Cancel an existing order.
        
        Args:
            order_id: Order ID to cancel
            variety: Order variety (regular, amo, etc.)
            
        Returns:
            Dict with success status and details
        """
        try:
            variety_map = {
                'regular': self.kite.VARIETY_REGULAR,
                'amo': self.kite.VARIETY_AMO
            }
            variety_const = variety_map.get(variety, self.kite.VARIETY_REGULAR)
            
            logging.info(f"Canceling order: {order_id}")
            
            result_order_id = self.kite.cancel_order(
                variety=variety_const,
                order_id=order_id
            )
            
            logging.info(f"✅ Order canceled successfully. Order ID: {result_order_id}")
            
            return {
                'success': True,
                'order_id': result_order_id,
                'action': 'CANCELLED'
            }
            
        except Exception as e:
            logging.error(f"❌ Error canceling order {order_id}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'order_id': order_id
            }