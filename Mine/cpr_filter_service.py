import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv
import logging
from typing import Optional, List, Dict, Tuple, Any
import time  # Added for rate limiting
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables once at the module level
load_dotenv()

class CPRFilterService:
    """
    Service class to fetch historical data, calculate multi-timeframe CPR levels,
    and filter F&O stocks based on predefined CPR strategy criteria.
    """
    # --- Constants (Class Attributes for better structure) ---
    HISTORICAL_DATA_DAYS: int = 45        # Max days of data to fetch
    MIN_DATA_RECORDS: int = 25            # Minimum daily records needed for reliable calculation
    MONTHLY_DATA_LOOKBACK: int = 21       # Number of trading days for monthly CPR calculation
    PERCENTAGE_DIFF_THRESHOLD: float = 2.0 # 2.0% for weekly/monthly BC/TC difference (Narrow Confluence)
    INDEX_SYMBOLS: List[str] = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
    API_RATE_LIMIT_DELAY: float = 0.6     # Minimum delay between API requests (seconds) - ~1.7 req/sec, well below Zerodha limit

    def __init__(self, kite_instance: Optional[KiteConnect] = None, api_key: Optional[str] = None):
        """Initializes the service and the KiteConnect instance."""
        if kite_instance:
            self.kite: KiteConnect = kite_instance
            logger.info("CPRFilterService: Using existing KiteConnect instance.")
        else:
            self.api_key: Optional[str] = api_key or os.getenv("API_KEY")
            self.access_token: Optional[str] = os.getenv("ACCESS_TOKEN") 
            
            if not self.api_key:
                raise ValueError("API_KEY not provided or set as environment variable for CPRFilterService")
            
            self.kite = KiteConnect(api_key=self.api_key)
            if self.access_token and self.access_token.strip():
                self.kite.set_access_token(self.access_token)
                logger.info("CPRFilterService: Created new KiteConnect instance with access token.")
            else:
                logger.warning("CPRFilterService: Created new KiteConnect instance without access token. Ensure authentication is handled elsewhere if required for API calls.")
        
        # Caching attributes: Initialize _instruments as an empty list (safe iterable)
        self._instruments: List[Dict[str, Any]] = [] # FIX 1: Initialized as empty list instead of Optional[List]
        self._fo_stocks: Optional[List[str]] = None
        self._historical_data_cache: Dict[str, pd.DataFrame] = {}  # Cache for historical data
        self._cache_lock = threading.Lock()  # Thread-safe caching
        self._last_api_call_time: float = 0.0  # Track last API call for rate limiting
        self._api_call_lock = threading.Lock()  # Lock for thread-safe rate limiting
        self._load_instruments() # Preload instruments for efficiency

    def _load_instruments(self) -> None:
        """Loads and caches all NSE instruments."""
        try:
            # Check if list is empty (i.e., not loaded yet)
            if not self._instruments: 
                logger.info("🔗 API CALL: Loading NSE instruments for symbol mapping...")
                self._apply_rate_limit()
                self._instruments = self.kite.instruments('NSE')
                logger.info(f"✅ API SUCCESS: Loaded {len(self._instruments)} NSE instruments.")
        except Exception as e:
            logger.error(f"❌ API ERROR: Error loading NSE instruments: {e}")
            # FIX 2: Ensure it remains an empty list on failure
            self._instruments = [] 

    @staticmethod
    def calculate_cpr(high: float, low: float, close: float) -> Tuple[float, float, float]:
        """Calculate Central Pivot Range levels (PP, BC, TC)."""
        pp: float = (high + low + close) / 3
        bc: float = (high + low) / 2
        tc: float = (2 * pp) - bc
        return pp, bc, tc
    
    def _apply_rate_limit(self) -> None:
        """Apply rate limiting to prevent 'Too many requests' errors from Zerodha API."""
        with self._api_call_lock:
            elapsed = time.time() - self._last_api_call_time
            if elapsed < self.API_RATE_LIMIT_DELAY:
                sleep_time = self.API_RATE_LIMIT_DELAY - elapsed
                logger.debug(f"Rate limiting: sleeping for {sleep_time:.3f}s")
                time.sleep(sleep_time)
            self._last_api_call_time = time.time()
    
    def get_instrument_token(self, symbol: str) -> Optional[int]:
        """Get instrument token for NSE equity."""
        # FIX 3: Since _instruments is initialized to [], check for emptiness
        if not self._instruments:
             logger.debug(f"🔍 Instruments cache empty, reloading...")
             self._load_instruments()
        
        # self._instruments is now guaranteed to be a list (even if empty)
        for instrument in self._instruments: 
            if instrument.get('tradingsymbol') == symbol and instrument.get('instrument_type') == 'EQ':
                token = instrument.get('instrument_token')
                logger.debug(f"✅ Found token for {symbol}: {token}")
                return token
            
        logger.warning(f"⚠️ No instrument token found for equity symbol: {symbol}")
        return None
    
    def get_historical_data(self, symbol: str, days_back: int, interval: str = 'day') -> Optional[pd.DataFrame]:
        """Get historical data from Zerodha and convert to DataFrame. Uses caching for performance."""
        # Check cache first
        cache_key = f"{symbol}_{days_back}_{interval}"
        with self._cache_lock:
            if cache_key in self._historical_data_cache:
                logger.info(f"💾 CACHE HIT: {symbol} ({days_back}d, {interval}) - Returning cached data")
                return self._historical_data_cache[cache_key]
        
        try:
            token = self.get_instrument_token(symbol)
            if token is None:
                logger.warning(f"⚠️ Could not find instrument token for {symbol}")
                return None
            
            end_date: datetime = datetime.now()
            start_date: datetime = end_date - timedelta(days=days_back)
            
            # Ensure we have a valid KiteConnect instance with access token
            if not hasattr(self.kite, 'access_token') or not self.kite.access_token:
                logger.error(f"❌ No access token set for {symbol}. Cannot fetch historical data. Please login first.")
                # Raise exception so caller knows this is an auth issue
                raise ValueError("No valid access token set on KiteConnect instance. User must be authenticated.")
            
            try:
                # Apply rate limiting before API call
                self._apply_rate_limit()
                
                # Convert datetime to date objects and format as strings (YYYY-MM-DD) for KiteConnect API
                from_date_str: str = start_date.strftime('%Y-%m-%d')
                to_date_str: str = end_date.strftime('%Y-%m-%d')
                
                logger.info(f"🔗 API CALL: Fetching {symbol} historical data | Token: {token} | Period: {from_date_str} to {to_date_str} | Interval: {interval}")
                data: List[Dict[str, Any]] = self.kite.historical_data(
                    instrument_token=token,
                    from_date=from_date_str,
                    to_date=to_date_str,
                    interval=interval,
                    continuous=False
                )
                logger.info(f"✅ API SUCCESS: Received {len(data) if data else 0} candles for {symbol}")
            except Exception as api_error:
                # Check if it's an authentication error
                error_str = str(api_error).lower()
                if 'api_key' in error_str or 'access_token' in error_str or 'unauthorized' in error_str:
                    logger.error(f"❌ API AUTH ERROR: Authentication error for {symbol}: {api_error}. Access token may have expired or is invalid.")
                    raise ValueError(f"Authentication failed for {symbol}: {api_error}")
                elif 'too many requests' in error_str or '429' in error_str:
                    logger.warning(f"⚠️ API RATE LIMIT: Rate limit exceeded for {symbol}. Waiting 2 seconds before retry...")
                    # Increase wait time for retry
                    time.sleep(2.0)
                    return self.get_historical_data(symbol, days_back, interval)  # Retry
                else:
                    logger.error(f"❌ API ERROR: Error fetching historical data for {symbol}: {api_error}")
                return None
            
            if not data:
                logger.info(f"⚠️ No data returned for {symbol} within the date range {from_date_str} to {to_date_str}.")
                return None
            
            df = pd.DataFrame(data)
            required_cols = ['date', 'high', 'low', 'close']
            if not all(col in df.columns for col in required_cols):
                 logger.error(f"❌ Missing required columns in data for {symbol}.")
                 return None
                 
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)

            # Cache the result
            with self._cache_lock:
                self._historical_data_cache[cache_key] = df
            
            logger.info(f"💾 CACHE SAVED: {symbol} ({days_back}d, {interval}) - Stored {len(df)} candles in cache")
            return df
        except ValueError as ve:
            # Re-raise ValueError so it bubbles up to caller
            raise ve
        except Exception as e:
            logger.error(f"❌ ERROR: Error fetching historical data for {symbol}: {e}")
            return None

    def _get_period_cpr_data(self, data_frame: pd.DataFrame, lookback_days: int, offset: int = 0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Helper to get high, low, close for a specific, preceding period.
        """
        if len(data_frame) < lookback_days + offset:
            return None, None, None 
        
        end_slice: Optional[int] = -offset if offset > 0 else None
        period_data: pd.DataFrame = data_frame.iloc[-(lookback_days + offset): end_slice]
        
        high: float = period_data['high'].max()
        low: float = period_data['low'].min()
        close: float = period_data['close'].iloc[-1] 
        
        return high, low, close

    def _check_above_cpr(self, current_price: float, daily_tc: float, weekly_tc: float, monthly_tc: float,
                         daily_bc: float, weekly_bc: float, monthly_bc: float,
                         current_low: float, weekly_bc_monthly_tc_diff: float, buy_signal_condition: bool) -> bool:
        """Helper to check all bullish conditions for ABOVE CPR."""
        
        above_all_tc: bool = (current_price > daily_tc and 
                              current_price > weekly_tc and 
                              current_price > monthly_tc)
        
        trend_confluence: bool = (weekly_bc > monthly_bc)
        
        narrow_confluence: bool = (weekly_bc_monthly_tc_diff <= self.PERCENTAGE_DIFF_THRESHOLD)
        
        initial_above_cpr: bool = (above_all_tc and 
                                   trend_confluence and
                                   narrow_confluence and
                                   buy_signal_condition)
        
        if initial_above_cpr:
            tc_touch_condition: bool = (
                (current_low <= weekly_tc and current_price > weekly_tc) or
                (current_low <= monthly_tc and current_price > monthly_tc)
            )
            return tc_touch_condition
        
        return False

    def _check_below_cpr(self, current_price: float, daily_tc: float, weekly_tc: float, monthly_tc: float,
                         daily_bc: float, weekly_bc: float, monthly_bc: float,
                         current_high: float, weekly_tc_monthly_bc_diff: float, sell_signal_condition: bool) -> bool:
        """Helper to check all bearish conditions for BELOW CPR."""
        
        below_all_bc: bool = (current_price < daily_bc and 
                              current_price < weekly_bc and 
                              current_price < monthly_bc)
        
        trend_confluence: bool = (weekly_tc < monthly_tc)
        
        narrow_confluence: bool = (weekly_tc_monthly_bc_diff <= self.PERCENTAGE_DIFF_THRESHOLD)
        
        initial_below_cpr: bool = (below_all_bc and 
                                   trend_confluence and 
                                   narrow_confluence and
                                   sell_signal_condition)
        
        if initial_below_cpr:
            bc_touch_condition: bool = (
                (current_high >= weekly_bc and current_price < weekly_bc) or
                (current_high >= monthly_bc and current_price < monthly_bc)
            )
            return bc_touch_condition
        
        return False
    
    def _calculate_gap_percentages(self, current_price: float, status: str,
                                   daily_tc: float, daily_bc: float,
                                   weekly_tc: float, weekly_bc: float,
                                   monthly_tc: float, monthly_bc: float) -> Tuple[float, float, float]:
        """Calculates gap percentages based on current price and CPR status."""
        
        if status == "✅ ABOVE CPR TC":
            target_daily_level = daily_tc
            target_weekly_level = weekly_tc
            target_monthly_level = monthly_tc
            is_above = True
        elif status == "❌ BELOW CPR BC":
            target_daily_level = daily_bc
            target_weekly_level = weekly_bc
            target_monthly_level = monthly_bc
            is_above = False
        else:
            return 0.0, 0.0, 0.0
        
        def calculate_gap(price, level, is_above):
            if level <= 0: return 100.0
            if is_above:
                return round(((price - level) / level) * 100, 2)
            else:
                return round(((level - price) / level) * 100, 2)
                
        d_gap: float = calculate_gap(current_price, target_daily_level, is_above)
        w_gap: float = calculate_gap(current_price, target_weekly_level, is_above)
        m_gap: float = calculate_gap(current_price, target_monthly_level, is_above)
            
        return d_gap, w_gap, m_gap

    def _evaluate_cpr_status(self, current_price: float, daily_tc: float, weekly_tc: float, monthly_tc: float,
                             daily_bc: float, weekly_bc: float, monthly_bc: float,
                             current_low: float, current_high: float,
                             weekly_bc_monthly_tc_diff: float, weekly_tc_monthly_bc_diff: float) -> str:
        """Evaluates CPR conditions and returns the stock's status."""
        
        buy_signal_condition: bool = (weekly_bc <= daily_tc)
        logger.debug(f"BUY Signal condition (WBC <= DTC): {buy_signal_condition}")
        
        above_cpr: bool = self._check_above_cpr(current_price, daily_tc, weekly_tc, monthly_tc,
                                           daily_bc, weekly_bc, monthly_bc,
                                           current_low, weekly_bc_monthly_tc_diff, buy_signal_condition)
        
        if above_cpr:
            return "✅ ABOVE CPR TC"
            
        sell_signal_condition: bool = (weekly_tc >= daily_bc)
        logger.debug(f"SELL Signal condition (WTC >= DBC): {sell_signal_condition}")

        below_cpr: bool = self._check_below_cpr(current_price, daily_tc, weekly_tc, monthly_tc,
                                           daily_bc, weekly_bc, monthly_bc,
                                           current_high, weekly_tc_monthly_bc_diff, sell_signal_condition)
        
        if below_cpr:
            return "❌ BELOW CPR BC"
        else:
            return "🟡 IN CPR"


    def get_fo_stocks(self) -> List[str]:
        """
        Get F&O stocks from Zerodha instruments, excluding index symbols.
        Caches the result.
        """
        try:
            if self._fo_stocks is not None:
                logger.info(f"💾 CACHE HIT: F&O stocks - Returning {len(self._fo_stocks)} cached symbols")
                return self._fo_stocks
            
            logger.info(f"🔗 API CALL: Loading NFO instruments for F&O stocks...")
            self._apply_rate_limit()
            nfo_instruments: List[Dict[str, Any]] = self.kite.instruments('NFO')
            logger.info(f"✅ API SUCCESS: Received {len(nfo_instruments)} NFO instruments")
            fo_symbols: set[str] = set()
            
            for instrument in nfo_instruments:
                if instrument.get('instrument_type') == 'FUT' and instrument.get('name'):
                    if not any(idx in instrument['name'] for idx in self.INDEX_SYMBOLS):
                        fo_symbols.add(instrument['name'])
            
            self._fo_stocks = sorted(list(fo_symbols))
            logger.info(f"✅ Found {len(self._fo_stocks)} F&O stocks (excluding indices: {', '.join(self.INDEX_SYMBOLS)})")
            return self._fo_stocks
        except Exception as e: 
            logger.error(f"❌ API ERROR: Error fetching F&O stocks: {e}. Returning empty list.")
            self._fo_stocks = []
            return []
    
    def _calculate_cpr_levels(self, data: pd.DataFrame) -> Optional[Dict[str, float]]:
        """
        Calculates daily, weekly, and monthly CPR levels from historical data.
        Ensures all inputs to calculate_cpr are guaranteed floats.
        """
        if len(data) < self.MIN_DATA_RECORDS:
            return None

        # --- Daily CPR Calculation (Previous Day) ---
        try:
            prev_day: pd.Series = data.iloc[-2]
        except IndexError:
            logger.warning("Dataframe has less than 2 rows for previous day CPR.")
            return None
            
        d_high: float = float(prev_day['high'])
        d_low: float = float(prev_day['low'])
        d_close: float = float(prev_day['close'])

        if pd.isna(d_high) or pd.isna(d_low) or pd.isna(d_close):
             logger.warning("Previous day data contains NaN values for CPR calculation.")
             return None

        daily_pp, daily_bc, daily_tc = self.calculate_cpr(d_high, d_low, d_close)
        
        # --- Weekly CPR Calculation ---
        week_high, week_low, week_close = self._get_period_cpr_data(data, 5, 1) 
        
        if week_high is None or week_low is None or week_close is None: 
            logger.warning("Insufficient data for weekly CPR calculation.")
            return None
        weekly_pp, weekly_bc, weekly_tc = self.calculate_cpr(week_high, week_low, week_close)
        
        # --- Monthly CPR Calculation ---
        month_high, month_low, month_close = self._get_period_cpr_data(data, self.MONTHLY_DATA_LOOKBACK, 1) 
        
        if month_high is None or month_low is None or month_close is None: 
            logger.warning("Insufficient data for monthly CPR calculation.")
            return None
        monthly_pp, monthly_bc, monthly_tc = self.calculate_cpr(month_high, month_low, month_close)

        return {
            'daily_pp': daily_pp, 'daily_bc': daily_bc, 'daily_tc': daily_tc,
            'weekly_pp': weekly_pp, 'weekly_bc': weekly_bc, 'weekly_tc': weekly_tc,
            'monthly_pp': monthly_pp, 'monthly_bc': monthly_bc, 'monthly_tc': monthly_tc,
            'current_day_low': float(data.iloc[-1]['low']),
            'current_day_high': float(data.iloc[-1]['high']),
            'current_price': float(data.iloc[-1]['close'])
        }

    def _process_single_stock(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Process a single stock and return its CPR analysis result.
        This method is designed to run in parallel threads.
        Raises ValueError for authentication errors (to be caught by caller).
        """
        try:
            # 1. Data Fetching (with caching)
            data = self.get_historical_data(symbol, self.HISTORICAL_DATA_DAYS, 'day')
            
            if data is None or len(data) < self.MIN_DATA_RECORDS:
                logger.debug(f"Skipping {symbol}: Insufficient data.")
                return None
            
            # 2. CPR Calculation
            cpr_levels = self._calculate_cpr_levels(data)
            if cpr_levels is None:
                logger.debug(f"Skipping {symbol}: CPR calculation failed.")
                return None
            
            # Unpack levels
            current_price: float = cpr_levels.pop('current_price')
            current_low: float = cpr_levels.pop('current_day_low')
            current_high: float = cpr_levels.pop('current_day_high')
            
            daily_tc: float = cpr_levels['daily_tc']
            daily_bc: float = cpr_levels['daily_bc']
            weekly_tc: float = cpr_levels['weekly_tc']
            weekly_bc: float = cpr_levels['weekly_bc']
            monthly_tc: float = cpr_levels['monthly_tc']
            monthly_bc: float = cpr_levels['monthly_bc']
            
            # 3. Calculate Narrow Confluence Differences
            weekly_bc_monthly_tc_diff: float = abs(weekly_bc - monthly_tc) / max(weekly_bc, 1e-6) * 100
            weekly_tc_monthly_bc_diff: float = abs(weekly_tc - monthly_bc) / max(weekly_tc, 1e-6) * 100
            
            # 4. Evaluate Status
            status: str = self._evaluate_cpr_status(
                current_price, daily_tc, weekly_tc, monthly_tc,
                daily_bc, weekly_bc, monthly_bc,
                current_low, current_high,
                weekly_bc_monthly_tc_diff, weekly_tc_monthly_bc_diff
            )
            
            # 5. Only return if not IN CPR
            if status == "🟡 IN CPR":
                return None
            
            d_gap, w_gap, m_gap = self._calculate_gap_percentages(
                current_price, status,
                daily_tc, daily_bc,
                weekly_tc, weekly_bc,
                monthly_tc, monthly_bc
            )
            
            return {
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'status': status,
                'daily_tc': round(daily_tc, 2),
                'daily_bc': round(daily_bc, 2),
                'weekly_tc': round(weekly_tc, 2),
                'weekly_bc': round(weekly_bc, 2),
                'monthly_tc': round(monthly_tc, 2),
                'monthly_bc': round(monthly_bc, 2),
                'd_gap': d_gap,
                'w_gap': w_gap,
                'm_gap': m_gap,
                'wbc_mtc_diff': round(weekly_bc_monthly_tc_diff, 2),
                'wtc_mbc_diff': round(weekly_tc_monthly_bc_diff, 2)
            }
            
        except ValueError as ve:
            # Re-raise ValueError for authentication errors
            raise ve
        except Exception as e:
            logger.debug(f"Error processing {symbol}: {e}")
            return None

    def filter_cpr_stocks(self, max_workers: int = 8) -> List[Dict[str, Any]]:
        """
        Filter stocks based on CPR criteria using parallel processing.
        Args:
            max_workers: Number of parallel threads (default 8 for balanced performance)
        """
        results: List[Dict[str, Any]] = []
        fo_stocks: List[str] = self.get_fo_stocks()
        
        current_log_level = logger.level
        logger.setLevel(logging.WARNING)
        
        logger.info(f"Starting CPR filter with {max_workers} parallel workers on {len(fo_stocks)} stocks...")
        start_time = time.time()
        
        auth_errors_count = 0
        
        # Use ThreadPoolExecutor for parallel API calls
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all stock processing tasks
            future_to_symbol = {executor.submit(self._process_single_stock, symbol): symbol for symbol in fo_stocks}
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_symbol):
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except ValueError as ve:
                    # Authentication error - bubble it up
                    logger.error(f"Auth error in filter_cpr_stocks: {ve}")
                    auth_errors_count += 1
                    if auth_errors_count > 3:  # If multiple auth errors, stop processing
                        raise ValueError(f"Authentication failed. Your access token may be invalid or expired. Error: {ve}")
                except Exception as e:
                    logger.debug(f"Error processing stock: {e}")
                    pass  # Continue with other stocks
                
                completed += 1
                if completed % 20 == 0:
                    logger.info(f"Processed {completed}/{len(fo_stocks)} stocks...")
        
        logger.setLevel(current_log_level)
        
        elapsed_time = time.time() - start_time
        logger.info(f"CPR Filter completed in {elapsed_time:.2f}s. Found {len(results)} stocks matching criteria.")
        
        return sorted(results, key=lambda x: x['symbol'])
    
    def clear_cache(self) -> None:
        """Clear the historical data cache to force fresh API calls on next run."""
        with self._cache_lock:
            self._historical_data_cache.clear()
            logger.info("Historical data cache cleared.")