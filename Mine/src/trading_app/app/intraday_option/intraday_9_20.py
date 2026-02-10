"""Intraday 9:20 Strategy - First 5 Minute Candle High/Low Based Trading"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import os
import time
from .Kite_data_fetch_services import KiteDataFetchService
from kiteconnect.exceptions import TokenException, NetworkException
from trading_app.app.utils.token_manager import get_access_token, save_access_token

logger = logging.getLogger(__name__)


class Intraday920Strategy:
    """
    Trading strategy based on first 5-minute candle of the day.
    
    Gets the High and Low from the first 5-minute candle and finds CE/PE strikes
    for both high and low levels.
    """

    def __init__(self, kite_instance):
        """
        Initialize 9:20 Strategy
        
        Args:
            kite_instance: KiteConnect instance
        """
        self.kite = kite_instance
        self.data_service = KiteDataFetchService(kite_instance)


    def _refresh_kite_access_token(self) -> bool:
        """Refresh Kite access token and update clients.

        Flow:
        1) Use cached/env access token if available.
        2) If missing/invalid, attempt to generate a new access token
           using REQUEST_TOKEN + API_SECRET (if present).
        """
        try:
            new_token = get_access_token()
            if new_token:
                self.kite.set_access_token(new_token)
                # Keep data service in sync
                self.data_service.kite = self.kite
                logger.info("Kite access token refreshed from cache/env")
                return True

            # Attempt to generate a new access token if request token is available
            try:
                from dotenv import load_dotenv  # type: ignore
                from kiteconnect import KiteConnect  # type: ignore
            except Exception:
                logger.warning("python-dotenv or kiteconnect not available for auto token refresh")
                return False

            load_dotenv()
            api_key = os.getenv("API_KEY")
            api_secret = os.getenv("API_SECRET")
            request_token = os.getenv("REQUEST_TOKEN")

            if not api_key or not api_secret or not request_token:
                logger.warning("REQUEST_TOKEN/API_SECRET not available for auto token refresh")
                return False

            kite = KiteConnect(api_key=api_key)
            session = kite.generate_session(request_token, api_secret)
            # session.generate_session returns a dict with access_token
            access_token = session.get("access_token") if isinstance(session, dict) else None

            if not access_token:
                logger.warning("Failed to generate access token from request token")
                return False

            os.environ["ACCESS_TOKEN"] = access_token
            save_access_token(access_token, request_token)

            self.kite.set_access_token(access_token)
            self.data_service.kite = self.kite
            logger.info("Kite access token refreshed via request token")
            return True

        except Exception as e:
            logger.warning(f"Failed to refresh Kite access token: {e}")
            return False

    def get_first_5min_high_low(self, symbol: str, target_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get the first 5-minute candle's high and low for a specific date.
        
        If no date is selected, starts with current date (12 Jan 2026) and falls back to 
        previous trading days until data is found.
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            target_date: Optional datetime object for specific date. If None, uses current date and falls back.
            
        Returns:
            Dictionary with first_5min_high, first_5min_low from the date with available data
        """
        try:
            # Get symbol token
            symbol_token = self.data_service.get_symbol_token(symbol, exchange='NSE')
            if not symbol_token:
                logger.error(f"Could not get token for {symbol}")
                return {
                    'symbol': symbol,
                    'error': f'Could not get token for {symbol}',
                    'success': False
                }
            
            # Determine starting date
            if target_date:
                # Use the provided target date
                current_check_date = target_date.replace(hour=9, minute=20, second=0, microsecond=0)
                logger.info(f"Fetching first 5-minute candle for {symbol} from {current_check_date.date()}")
            else:
                # Start with current date (12 Jan 2026 or today)
                now = datetime.now()
                current_check_date = now.replace(hour=9, minute=20, second=0, microsecond=0)
                logger.info(f"Fetching first 5-minute candle for {symbol} starting from {current_check_date.date()}")
            
            # Try to fetch data, falling back to previous days if no data found
            max_date_retries = 30  # Try up to 30 days back
            for date_attempt in range(max_date_retries):
                # Skip weekends
                while current_check_date.weekday() in [5, 6]:  # Saturday=5, Sunday=6
                    current_check_date -= timedelta(days=1)
                
                # Fetch from 9:15 AM to 9:20 AM of current check date
                from_date = current_check_date.replace(minute=15)
                to_date = current_check_date
                
                # Retry logic for connection issues
                max_connection_retries = 3
                for conn_attempt in range(max_connection_retries):
                    try:
                        logger.info(f"Fetching candles: Date attempt {date_attempt + 1}/{max_date_retries}, Connection attempt {conn_attempt + 1}/{max_connection_retries}")
                        logger.info(f"  Period: {from_date} to {to_date}")
                        
                        candles = self.data_service.get_candlestick_data(
                            symbol_token,
                            interval='5minute',
                            from_date=from_date,
                            to_date=to_date
                        )
                        
                        if candles and len(candles) > 0:
                            # Found data! Get the first candle
                            first_candle = candles[0]
                            first_5min_high = first_candle.get('high', 0)
                            first_5min_low = first_candle.get('low', 0)
                            first_5min_close = first_candle.get('close', 0)
                            
                            logger.info(f"✓ Found first 5min candle for {symbol} on {current_check_date.date()}: High={first_5min_high}, Low={first_5min_low}, Close={first_5min_close}")
                            
                            return {
                                'symbol': symbol,
                                'first_5min_high': round(first_5min_high, 2),
                                'first_5min_low': round(first_5min_low, 2),
                                'first_5min_close': round(first_5min_close, 2),
                                'timestamp': datetime.now().isoformat(),
                                'success': True,
                                'data_date': current_check_date.date().isoformat()
                            }
                        else:
                            # No data on this date, try previous date
                            logger.debug(f"No candles found for {symbol} on {current_check_date.date()}")
                            break  # Break connection retry loop, move to previous date
                        
                    except Exception as e:
                        error_str = str(e).lower()
                        is_retriable = any(keyword in error_str for keyword in [
                            'connection reset', 'connection aborted', 'connection refused',
                            'timeout', 'gateway', '504', '503', 'broken pipe'
                        ])
                        
                        if is_retriable and conn_attempt < max_connection_retries - 1:
                            logger.warning(f"Connection error (attempt {conn_attempt + 1}/{max_connection_retries}): {e}")
                            time.sleep(0.5 * (2 ** conn_attempt))  # Exponential backoff
                            continue
                        else:
                            logger.warning(f"Failed to fetch candles for {current_check_date.date()}: {e}")
                            break  # Break connection retry loop, move to previous date
                
                # Move to previous day and retry
                current_check_date -= timedelta(days=1)
            
            # No data found after max retries
            logger.error(f"No candle data found for {symbol} after {max_retries} days")
            return {
                'symbol': symbol,
                'error': f'No candle data found for {symbol} in the last {max_retries} days',
                'success': False
            }
            
        except Exception as e:
            logger.error(f"Error getting first 5min high/low for {symbol}: {str(e)}")
            return {
                'symbol': symbol,
                'error': str(e),
                'success': False
            }

    def get_ce_pe_strikes(self, underlying_price: float) -> tuple:
        """
        Calculate CE and PE strike prices based on nearest 25-point level.
        
        Args:
            underlying_price: Current price of underlying
            
        Returns:
            Tuple of (CE strike, PE strike)
        """
        last_two_digits = int(underlying_price) % 100
        base_100 = (int(underlying_price) // 100) * 100
        
        if last_two_digits <= 12:
            near_level = 0
        elif last_two_digits <= 37:
            near_level = 25
        elif last_two_digits <= 62:
            near_level = 50
        elif last_two_digits <= 87:
            near_level = 75
        else:
            near_level = 100
        
        if near_level == 100:
            rounded_number = base_100 + 100
        else:
            rounded_number = base_100 + near_level
        
        if near_level == 0 or near_level == 50 or near_level == 100:
            ce_strike = rounded_number
            pe_strike = rounded_number
        else:  # near_level == 25 or 75
            ce_strike = rounded_number - 25
            pe_strike = rounded_number + 25
        
        logger.info(f"Strike calculation for {underlying_price:.2f}: CE={ce_strike}, PE={pe_strike}")
        return int(ce_strike), int(pe_strike)

    def get_strike_data(self, symbol: str, strike_price: float, fetch_candles: bool = False, reference_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get CE and PE tokens and data for a given strike price.
        
        Args:
            symbol: Trading symbol
            strike_price: Strike price
            fetch_candles: Whether to fetch historical candle data (slow, use only for live updates)
            reference_date: Date to fetch candles from (defaults to today if not specified)
            
        Returns:
            Dictionary with CE and PE strike tokens and info
        """
        try:
            ce_strike, pe_strike = self.get_ce_pe_strikes(strike_price)
            
            logger.info(f"Fetching tokens for {symbol} strike {strike_price} → CE:{ce_strike}, PE:{pe_strike}")
            
            # Get tokens for both CE and PE
            ce_token = None
            pe_token = None
            ce_high = 0
            ce_low = 0
            pe_high = 0
            pe_low = 0
            
            try:
                # Get tokens for CE strike (returns CE option for ce_strike)
                ce_strike_data = self.data_service.get_strike_tokens(symbol, ce_strike)
                ce_token = ce_strike_data.get('ce_token')
                
                # Get tokens for PE strike (returns PE option for pe_strike)
                pe_strike_data = self.data_service.get_strike_tokens(symbol, pe_strike)
                pe_token = pe_strike_data.get('pe_token')
                
                logger.info(f"Got tokens - CE strike {ce_strike}: {ce_token}, PE strike {pe_strike}: {pe_token}")
                
                # Only fetch candles if explicitly requested (skip during initial load for speed)
                if fetch_candles:
                    # Get FIRST 5-MIN candle high/low for both CE and PE on the reference date
                    # Use fallback logic: if no data for reference_date, try previous trading days
                    if reference_date is None:
                        reference_date = datetime.now()
                    
                    current_check_date = reference_date.replace(hour=9, minute=20, second=0, microsecond=0)
                    logger.info(f"Fetching first 5-min candles starting from {current_check_date.date()}")
                    
                    # Try to fetch first 5-min candles, with fallback to previous days
                    max_retries = 30
                    for attempt in range(max_retries):
                        # Skip weekends
                        while current_check_date.weekday() in [5, 6]:  # Saturday=5, Sunday=6
                            current_check_date -= timedelta(days=1)
                        
                        # Fetch ONLY first 5-minute candle (9:15 AM to 9:20 AM)
                        from_time = current_check_date.replace(minute=15)
                        to_time = current_check_date
                        
                        logger.info(f"Attempt {attempt + 1}: Fetching first 5-min candles from {from_time} to {to_time}")
                        
                        try:
                            if ce_token and not ce_high:
                                ce_candles = self.data_service.get_candlestick_data(
                                    ce_token, 
                                    interval='5minute',
                                    from_date=from_time,
                                    to_date=to_time
                                )
                                if ce_candles and len(ce_candles) > 0:
                                    # Use ONLY first 5-min candle high/low
                                    first_candle = ce_candles[0]
                                    ce_high = first_candle.get('high', 0)
                                    ce_low = first_candle.get('low', 0)
                                    logger.info(f"CE {ce_strike} first 5-min ({current_check_date.date()}): High={ce_high}, Low={ce_low}")
                                else:
                                    logger.warning(f"No CE candles found for {ce_strike} on {current_check_date.date()}, trying previous day...")
                            
                            if pe_token and not pe_high:
                                pe_candles = self.data_service.get_candlestick_data(
                                    pe_token,
                                    interval='5minute',
                                    from_date=from_time,
                                    to_date=to_time
                                )
                                if pe_candles and len(pe_candles) > 0:
                                    # Use ONLY first 5-min candle high/low
                                    first_candle = pe_candles[0]
                                    pe_high = first_candle.get('high', 0)
                                    pe_low = first_candle.get('low', 0)
                                    logger.info(f"PE {pe_strike} first 5-min ({current_check_date.date()}): High={pe_high}, Low={pe_low}")
                                else:
                                    logger.warning(f"No PE candles found for {pe_strike} on {current_check_date.date()}, trying previous day...")
                            
                            # If we found data for both, break out of retry loop
                            if ce_high and pe_high:
                                break
                        
                        except Exception as e:
                            logger.warning(f"Error fetching candles for {current_check_date.date()}: {str(e)}, trying previous day...")
                        
                        # Move to previous day and retry
                        current_check_date -= timedelta(days=1)
            except Exception as e:
                logger.warning(f"Could not fetch strike tokens: {str(e)}", exc_info=True)
            
            return {
                'strike_price': strike_price,
                'ce_strike': ce_strike,
                'ce_token': ce_token,
                'ce_high': round(ce_high, 2),
                'ce_low': round(ce_low, 2),
                'pe_strike': pe_strike,
                'pe_token': pe_token,
                'pe_high': round(pe_high, 2),
                'pe_low': round(pe_low, 2),
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error getting strike data for {symbol}: {str(e)}", exc_info=True)
            return {
                'error': str(e),
                'success': False
            }

    def get_intraday_920_data(self, symbol: str, target_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get complete 9:20 strategy data.
        
        Fetches current price and calculates CE/PE strikes.
        If market is open (9:15 AM - 3:20 PM), fetches first 5-minute candle.
        Otherwise, uses current price as reference.
        
        Args:
            symbol: Trading symbol
            target_date: Optional date to fetch data for (format: datetime object).
                        If None, uses last trading day's data.
            
        Returns:
            Complete strategy data with strikes
        """
        try:
            # Step 1: Get current price (always available during market hours)
            symbol_map = {
                'NIFTY': 'NSE:NIFTY 50',
                'BANKNIFTY': 'NSE:NIFTY BANK',
                'FINNIFTY': 'NSE:NIFTY FIN SERVICE'
            }
            instrument_key = symbol_map.get(symbol)
            
            if not instrument_key:
                return {
                    'symbol': symbol,
                    'error': f'Unknown symbol: {symbol}',
                    'timestamp': datetime.now().isoformat(),
                    'success': False
                }
            
            # Fetch current quote data from Kite with retry logic
            quote = None
            max_retries = 5
            retry_delay = 3  # seconds (increased for server timeouts)
            
            for attempt in range(max_retries):
                try:
                    quote = self.kite.quote([instrument_key])
                    break  # Success, exit retry loop
                except TokenException:
                    logger.warning(f"Access token invalid (attempt {attempt + 1}/{max_retries}). Attempting refresh and retry...")
                    if self._refresh_kite_access_token():
                        try:
                            quote = self.kite.quote([instrument_key])
                            break
                        except Exception as e:
                            logger.warning(f"Quote fetch failed after token refresh: {e}")
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                    else:
                        return {
                            'symbol': symbol,
                            'error': 'Failed to refresh access token',
                            'timestamp': datetime.now().isoformat(),
                            'success': False
                        }
                except NetworkException as e:
                    logger.warning(f"Network error fetching quote (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    else:
                        return {
                            'symbol': symbol,
                            'error': f'Network error fetching quote after {max_retries} retries: {str(e)}',
                            'timestamp': datetime.now().isoformat(),
                            'success': False
                        }
                except (ValueError, Exception) as e:
                    # Catches JSON parsing errors (504, 503 gateway timeouts), connection resets, and other retriable errors
                    error_str = str(e).lower()
                    is_retriable = any(keyword in error_str for keyword in [
                        '504', '503', 'gateway', 'timeout', 'couldn\'t parse', 'json',
                        'connection reset', 'connection aborted', 'connection refused',
                        'broken pipe', 'reset by peer'
                    ])
                    
                    if is_retriable:
                        logger.warning(f"Retriable error fetching quote (attempt {attempt + 1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            # Use longer delay for server timeouts
                            delay = retry_delay * (attempt + 1) if any(k in error_str for k in ["504", "503"]) else retry_delay
                            time.sleep(delay)
                        else:
                            return {
                                'symbol': symbol,
                                'error': f'Error fetching quote after {max_retries} retries: {str(e)}',
                                'timestamp': datetime.now().isoformat(),
                                'success': False
                            }
                    else:
                        logger.error(f"Error fetching quote (non-retriable): {e}")
                        return {
                            'symbol': symbol,
                            'error': f'Error fetching quote: {str(e)}',
                            'timestamp': datetime.now().isoformat(),
                            'success': False
                        }
            
            if quote is None:
                return {
                    'symbol': symbol,
                    'error': 'Failed to fetch quote data after all retries',
                    'timestamp': datetime.now().isoformat(),
                    'success': False
                }
            
            quote_data = quote.get(instrument_key, {})
            
            if not quote_data:
                return {
                    'symbol': symbol,
                    'error': f'No quote data for {symbol}',
                    'timestamp': datetime.now().isoformat(),
                    'success': False
                }
            
            current_price = float(quote_data.get('last_price', 0))
            
            logger.info(f"Got current price for {symbol}: {current_price}")
            
            # Step 2: Try to get first 5-minute candle (if market is open)
            first_5min = self.get_first_5min_high_low(symbol, target_date=target_date)
            
            # Determine the reference date for strike data
            # If target_date provided, use that; otherwise start with current date
            # The get_strike_data method will handle fallback to previous days if needed
            if target_date:
                reference_date = target_date.replace(hour=9, minute=20, second=0, microsecond=0)
            else:
                # Start with CURRENT date (not yesterday)
                # get_strike_data will fall back to previous days if no data found
                reference_date = datetime.now().replace(hour=9, minute=20, second=0, microsecond=0)
            
            if first_5min.get('success'):
                # Use actual 5-minute high/low
                high = first_5min.get('first_5min_high', current_price)
                low = first_5min.get('first_5min_low', current_price)
                close = first_5min.get('first_5min_close', current_price)
            else:
                # Market not open yet or no data - use current price as reference
                logger.warning(f"No 5-minute candle data, using current price: {first_5min.get('error')}")
                high = current_price
                low = current_price
                close = current_price
                first_5min = {
                    'first_5min_high': current_price,
                    'first_5min_low': current_price,
                    'first_5min_close': current_price
                }
            
            # Step 3: Get strike data for both high and low in PARALLEL for speed
            # IMPORTANT: fetch_candles=False for live monitoring to avoid timeout
            # Only fetch candles for backtesting, not for live data during trading hours
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # Determine if we should fetch candles (only for historical/backtest mode)
            # During live trading (9:15-15:20), skip candle fetching - it's too slow and times out
            from datetime import time as dt_time
            now = datetime.now().time()
            is_live_market = dt_time(9, 15) <= now <= dt_time(15, 20)
            should_fetch_candles = not is_live_market  # Only fetch candles if market is closed
            
            with ThreadPoolExecutor(max_workers=2) as executor:
                high_future = executor.submit(self.get_strike_data, symbol, high, should_fetch_candles, reference_date)
                low_future = executor.submit(self.get_strike_data, symbol, low, should_fetch_candles, reference_date)
                
                high_strike_data = high_future.result()
                low_strike_data = low_future.result()
            
            return {
                'symbol': symbol,
                'current_price': current_price,
                'first_5min_high': high,
                'first_5min_low': low,
                'first_5min_close': close,
                'high_strike': high_strike_data if high_strike_data.get('success') else {},
                'low_strike': low_strike_data if low_strike_data.get('success') else {},
                'timestamp': datetime.now().isoformat(),
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error in get_intraday_920_data: {str(e)}", exc_info=True)
            return {
                'symbol': symbol,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
                'success': False
            }

    def get_candle_data(self, token: int, interval: str = '5minute', days_back: int = 1) -> Dict[str, Any]:
        """
        Get candlestick data for a token.
        
        Args:
            token: Instrument token
            interval: Candle interval
            days_back: Days to fetch
            
        Returns:
            Candlestick data
        """
        try:
            logger.info(f"Fetching candle data for token {token}")
            
            candles = self.data_service.get_candlestick_data(
                token,
                interval=interval,
                days_back=days_back
            )
            
            if not candles:
                return {
                    'token': token,
                    'candles': [],
                    'success': False,
                    'error': 'No candles found'
                }
            
            return {
                'token': token,
                'candles': candles,
                'count': len(candles),
                'timestamp': datetime.now().isoformat(),
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error fetching candle data: {str(e)}")
            return {
                'token': token,
                'error': str(e),
                'success': False
            }

    def calculate_sl_for_entry(self, entry_price: float, reference_high: float, ratio: str = '1:2') -> Dict[str, Any]:
        """
        Calculate stop loss based on entry price and reference high.
        
        SL Logic:
        - If (Entry Price - Reference High) > 10 points: SL = Reference High - 10
        - If (Entry Price - Reference High) <= 10 points: SL = Reference High - 20
        
        Ratio Logic:
        - '1:2': Target = Entry + 2 * (Entry - SL)  [1:2 ratio]
        - '1:3': Target = Entry + 3 * (Entry - SL)  [1:3 ratio]
        - '1:2-trail': Target = Entry + 2 * (Entry - SL), with trailing SL after target
        
        Args:
            entry_price: Price at which entry occurred
            reference_high: Reference high (PE high for CE entry, CE high for PE entry)
            ratio: Risk/reward ratio ('1:2', '1:3', or '1:2-trail')
            
        Returns:
            Dictionary with SL, target, and entry details
        """
        try:
            price_diff = entry_price - reference_high
            
            # Determine SL based on price difference
            if price_diff > 10:
                sl = reference_high - 10
            else:
                sl = reference_high - 20
            
            # Determine target based on selected ratio
            profit = entry_price - sl
            if ratio == '1:3':
                # 1:3 ratio: profit is 3x the risk
                target = entry_price + (3 * profit)
            else:
                # Default to 1:2 ratio (both '1:2' and '1:2-trail' use 1:2)
                target = entry_price + (2 * profit)
            
            logger.info(f"Entry: {entry_price}, Ref High: {reference_high}, Price Diff: {price_diff:.2f}")
            logger.info(f"SL: {sl}, Target: {target}, Profit: {profit}, Ratio: {ratio}")
            
            return {
                'entry_price': round(entry_price, 2),
                'reference_high': round(reference_high, 2),
                'sl': round(sl, 2),
                'target': round(target, 2),
                'profit_points': round(profit, 2),
                'ratio': ratio,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error calculating SL: {str(e)}")
            return {
                'error': str(e),
                'success': False
            }

    def check_entry_signal(self, ce_token: int, pe_token: int, ce_high: float, pe_high: float, 
                          symbol: str = 'NIFTY', target_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Check for entry signals on CE and PE sides.
        
        CE Entry Condition:
        - Latest 5-min candle low < PE High
        - Latest 5-min candle close > PE High
        
        PE Entry Condition:
        - Latest 5-min candle low < CE High
        - Latest 5-min candle close > CE High
        
        Args:
            ce_token: CE option token
            pe_token: PE option token
            ce_high: CE first 5-min high
            pe_high: PE first 5-min high
            symbol: Trading symbol
            target_date: Optional date to fetch candles from
            
        Returns:
            Dictionary with entry signals for CE and PE sides
        """
        try:
            # Fetch latest 5-minute candles for both CE and PE
            ce_candles = self._get_latest_5min_candle(ce_token, target_date)
            pe_candles = self._get_latest_5min_candle(pe_token, target_date)
            
            ce_signal = {
                'side': 'CE',
                'has_signal': False,
                'entry_price': None,
                'sl': None,
                'target': None,
                'reason': 'No signal'
            }
            
            pe_signal = {
                'side': 'PE',
                'has_signal': False,
                'entry_price': None,
                'sl': None,
                'target': None,
                'reason': 'No signal'
            }
            
            # Check CE Entry Signal
            if ce_candles and len(ce_candles) > 0:
                latest_ce = ce_candles[-1]  # Latest candle
                ce_low = latest_ce.get('low', 0)
                ce_close = latest_ce.get('close', 0)
                
                logger.info(f"CE Latest Candle - Low: {ce_low}, Close: {ce_close}, PE High: {pe_high}")
                
                if ce_low < pe_high and ce_close > (pe_high + 5) and (ce_close - pe_high) < 20:
                    # CE Entry Signal - Price crossed above PE High + 5 points
                    sl_data = self.calculate_sl_for_entry(ce_close, pe_high)
                    if sl_data.get('success'):
                        ce_signal = {
                            'side': 'CE',
                            'has_signal': True,
                            'entry_price': sl_data['entry_price'],
                            'entry_high': pe_high,
                            'sl': sl_data['sl'],
                            'target': sl_data['target'],
                            'reason': f'Low {ce_low:.2f} < PE High {pe_high:.2f}, Close {ce_close:.2f} > PE High + 5'
                        }
                        logger.info(f"CE ENTRY SIGNAL: {ce_signal}")
            
            # Check PE Entry Signal
            if pe_candles and len(pe_candles) > 0:
                latest_pe = pe_candles[-1]  # Latest candle
                pe_low = latest_pe.get('low', 0)
                pe_close = latest_pe.get('close', 0)
                
                logger.info(f"PE Latest Candle - Low: {pe_low}, Close: {pe_close}, CE High: {ce_high}")
                
                if pe_low < ce_high and pe_close > (ce_high + 5) and (pe_close - ce_high) < 20:
                    # PE Entry Signal - Price crossed above CE High + 5 points
                    sl_data = self.calculate_sl_for_entry(pe_close, ce_high)
                    if sl_data.get('success'):
                        pe_signal = {
                            'side': 'PE',
                            'has_signal': True,
                            'entry_price': sl_data['entry_price'],
                            'entry_high': ce_high,
                            'sl': sl_data['sl'],
                            'target': sl_data['target'],
                            'reason': f'Low {pe_low:.2f} < CE High {ce_high:.2f}, Close {pe_close:.2f} > CE High + 5'
                        }
                        logger.info(f"PE ENTRY SIGNAL: {pe_signal}")
            
            return {
                'timestamp': datetime.now().isoformat(),
                'ce_signal': ce_signal,
                'pe_signal': pe_signal,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error checking entry signal: {str(e)}", exc_info=True)
            return {
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
                'success': False
            }

    def _get_latest_5min_candle(self, token: int, target_date: Optional[datetime] = None) -> List[Dict]:
        """
        Get the latest 5-minute candle for a token.
        
        Args:
            token: Instrument token
            target_date: Optional date to fetch from
            
        Returns:
            List of candles (latest first)
        """
        try:
            if target_date is None:
                target_date = datetime.now()

            # Use the last completed 5-minute candle
            end_time = target_date.replace(second=0, microsecond=0)
            end_minute = (end_time.minute // 5) * 5
            end_time = end_time.replace(minute=end_minute)

            # If we're exactly on the boundary, use the candle that just closed
            # Otherwise, still use the last fully closed candle
            start_time = end_time - timedelta(minutes=5)
            
            logger.info(f"Fetching candles from {start_time} to {end_time} for token {token}")
            
            candles = self.data_service.get_candlestick_data(
                token,
                interval='5minute',
                from_date=start_time,
                to_date=end_time
            )
            
            return candles if candles else []
            
        except Exception as e:
            logger.warning(f"Error fetching latest candle for token {token}: {str(e)}")
            return []
    def backtest_full_day(self, ce_token: int, pe_token: int, ce_high: float, pe_high: float,
                         symbol: str = 'NIFTY', target_date: Optional[datetime] = None,
                         risk_reward_ratio: str = '1:2-trail', entry_mode: str = 'candle_open') -> Dict[str, Any]:
        """
        Run comprehensive backtest checking all 5-minute candles from 9:20 to 3:20.
        
        Entry Modes:
        - "candle_open": Entry when low < PDH AND close > (PDH + 5 points)
        - "high_cross": Entry when 5min high + 5 crosses (penetrates) the reference level
        
        IMPORTANT: ce_high and pe_high parameters represent NIFTY's PDH (Previous Day High)
        for BOTH CE and PE sides. This is the reference level that the strategy trades around.
        
        Identifies:
        - Entry point (based on selected entry_mode)
        - Exit point (when hits SL or target)
        - Exit reason (SL hit, Target hit, or no exit)
        
        Args:
            ce_token: CE option token
            pe_token: PE option token
            ce_high: NIFTY's Previous Day High (PDH) - reference level for entry
            pe_high: NIFTY's Previous Day High (PDH) - reference level for entry
            symbol: Trading symbol (NIFTY)
            target_date: Date to backtest (datetime object or None for today)
            risk_reward_ratio: Risk/reward ratio ('1:2', '1:3', or '1:2-trail')
            entry_mode: Entry signal mode ('candle_open' or 'high_cross')
            
        Returns:
            Detailed backtest results for both CE and PE
            
        NOTE: Both CE and PE use the SAME reference level (NIFTY's PDH), not their own strikes' PDH
        """
        try:
            # Determine date to use
            if target_date is None:
                # No date selected - use current date
                target_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                logger.info(f"Backtest: No date selected, using current date: {target_date.date()}")
            else:
                # Date was selected - ensure it's at midnight for consistency
                target_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                logger.info(f"Backtest: Using selected date: {target_date.date()}")
            
            # Fetch all candles from 9:20 to 3:20 for both CE and PE
            ce_candles = self._get_all_trading_candles(ce_token, target_date)
            pe_candles = self._get_all_trading_candles(pe_token, target_date)
            
            # If no candles found, fall back to previous trading days
            actual_date = target_date
            fallback_count = 0
            max_fallback_days = 10
            
            while (not ce_candles or not pe_candles) and fallback_count < max_fallback_days:
                actual_date = actual_date - timedelta(days=1)
                # Skip weekends (Saturday=5, Sunday=6)
                if actual_date.weekday() >= 5:
                    continue
                ce_candles = self._get_all_trading_candles(ce_token, actual_date)
                pe_candles = self._get_all_trading_candles(pe_token, actual_date)
                fallback_count += 1
                if ce_candles or pe_candles:
                    logger.info(f"Backtest: Fallback successful. Using {actual_date.date()} instead of {target_date.date()}")
            
            logger.info(f"Backtest ({actual_date.date()}): Got {len(ce_candles)} CE candles and {len(pe_candles)} PE candles, Entry Mode: {entry_mode}")
            
            # Analyze CE side (entry condition: low < pe_high AND close > pe_high)
            ce_result = self._analyze_entry_exit(
                ce_candles, pe_high, 'CE',
                ce_high, pe_high, symbol, risk_reward_ratio, entry_mode
            )
            
            # Analyze PE side (entry condition: low < ce_high AND close > ce_high)
            pe_result = self._analyze_entry_exit(
                pe_candles, ce_high, 'PE',
                ce_high, pe_high, symbol, risk_reward_ratio, entry_mode
            )
            
            # Results already have correct timestamps (UTC with close time adjustment)
            # No need to remove offset - we handle it inline in _analyze_entry_exit
            
            return {
                'success': True,
                'ce_analysis': ce_result,
                'pe_analysis': pe_result,
                'symbol': symbol,
                'requested_date': target_date.strftime('%Y-%m-%d'),
                'actual_date': actual_date.strftime('%Y-%m-%d'),
                'used_fallback': actual_date != target_date,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in backtest_full_day: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _get_all_trading_candles(self, token: int, target_date: datetime) -> List[Dict]:
        """
        Fetch all 5-minute candles from 9:15 AM to 3:20 PM.
        
        Args:
            token: Instrument token
            target_date: Date to fetch candles from
            
        Returns:
            List of candles sorted by time
        """
        try:
            # Market hours: 9:15 AM to 3:20 PM (includes first 5-min candle 9:15-9:20)
            start_time = target_date.replace(hour=9, minute=15, second=0, microsecond=0)
            end_time = target_date.replace(hour=15, minute=20, second=0, microsecond=0)
            
            candles = self.data_service.get_candlestick_data(
                token,
                interval='5minute',
                from_date=start_time,
                to_date=end_time
            )
            
            if not candles:
                logger.warning(f"No candles found for token {token} from {start_time} to {end_time}")
                return []
            
            return sorted(candles, key=lambda x: x.get('time', x.get('date', 0)))
            
        except Exception as e:
            logger.error(f"Error fetching trading candles for token {token}: {str(e)}")
            return []

    def _analyze_entry_exit(self, candles: List[Dict], reference_high: float, side: str,
                           ce_high: float, pe_high: float, symbol: str, risk_reward_ratio: str = '1:2-trail',
                           entry_mode: str = 'candle_open') -> Dict[str, Any]:
        """
        Analyze candles to find entry and exit points.
        
        Entry Modes:
        - "candle_open": Entry when low < reference_high AND close > (reference_high + 5)
        - "high_cross": Entry when 5-min high + 5 crosses above (penetrates) the reference level
        
        Args:
            candles: List of 5-minute candles
            reference_high: Reference high (PE high for CE, CE high for PE)
            side: 'CE' or 'PE'
            ce_high: CE first 5-min high
            pe_high: PE first 5-min high
            symbol: Trading symbol
            risk_reward_ratio: Risk/reward ratio ('1:2', '1:3', or '1:2-trail')
            entry_mode: Entry signal mode ('candle_open' or 'high_cross')
            
        Returns:
            Entry and exit analysis
        """
        result = {
            'side': side,
            'has_entry': False,
            'entry_time': None,
            'entry_price': None,
            'entry_high': reference_high,
            'sl': None,
            'target': None,
            'exit_time': None,
            'exit_price': None,
            'exit_reason': None,
            'pnl': None,
            'candle_count': len(candles)
        }
        
        if not candles:
            return result
        
        # Log first candle for debugging
        if candles:
            first_candle = candles[0]
            day_open = first_candle.get('close', 0)  # First candle close = day open
            logger.info(f"{side} First candle - Time: {first_candle.get('time', 'N/A')}, Low: {first_candle.get('low')}, Close: {day_open}")
            logger.info(f"{side} Day open: {day_open}, Reference high: {reference_high}, Open above ref: {day_open > reference_high}, Entry Mode: {entry_mode}")
        
        # Search for entry point based on entry_mode
        entry_candle_idx = None
        
        if entry_mode == 'candle_open':
            # CANDLE OPEN MODE: Entry when low < ref_high AND close > (ref_high + 5)
            day_open = candles[0].get('close', 0) if candles else 0
            requires_close_below = day_open > reference_high  # If day opened above ref high, need a close below first
            has_closed_below = False  # Track if we've seen a close below reference high
            
            for idx, candle in enumerate(candles):
                candle_low = candle.get('low', 0)
                candle_close = candle.get('close', 0)
                
                # EXCEPTION: First candle (idx=0) is exempt from close-below requirement
                is_first_candle = (idx == 0)
                
                # If day opened above ref high, first check if this candle closes below ref high
                if requires_close_below and not has_closed_below and not is_first_candle:
                    logger.info(f"{side} Candle {idx}: Waiting for close < {reference_high}. Current close: {candle_close}")
                    if candle_close < reference_high:
                        has_closed_below = True
                        logger.info(f"{side} Candle {idx} closed below {reference_high}, entry condition now valid")
                    continue
                
                # Entry condition: low < ref_high AND close > (ref_high + 5 points)
                entry_threshold = reference_high + 5
                close_ref_diff = abs(candle_close - reference_high)
                logger.info(f"{side} Candle {idx}: Low={candle_low:.2f}, Close={candle_close:.2f}, Threshold={entry_threshold:.2f}, Close-Ref Diff={close_ref_diff:.2f}")
                
                if candle_low < reference_high and candle_close > entry_threshold and close_ref_diff < 20:
                    if close_ref_diff >= 20:
                        logger.info(f"{side} Candle {idx}: Entry rejected - Close-Ref diff: {close_ref_diff:.2f} >= 20 points")
                        continue
                    
                    if requires_close_below and not has_closed_below and not is_first_candle:
                        logger.info(f"{side} Candle {idx}: Close below needed, skipping")
                        continue
                    
                    entry_candle_idx = idx
                    result['has_entry'] = True
                    raw_entry_time = candle.get('time', candle.get('date'))
                    if raw_entry_time is None:
                        logger.error(f"{side} Entry time is None for candle {idx}")
                        continue
                    ist_offset_seconds = int(5.5 * 3600)
                    true_utc_time = raw_entry_time - ist_offset_seconds
                    close_time = true_utc_time + 300
                    result['entry_time'] = close_time
                    result['entry_price'] = candle_close
                    
                    ratio_type = '1:2' if risk_reward_ratio in ['1:2', '1:2-trail'] else '1:3'
                    sl_data = self.calculate_sl_for_entry(candle_close, reference_high, ratio=ratio_type)
                    result['sl'] = sl_data.get('sl')
                    result['target'] = sl_data.get('target')
                    
                    entry_reason = "Entry (Candle Open)"
                    if is_first_candle:
                        entry_reason += " [first candle]"
                    elif requires_close_below:
                        entry_reason += " [after close below]"
                    logger.info(f"{side} {entry_reason} at {result['entry_time']}: Price {candle_close}, SL {result['sl']}, Target {result['target']}")
                    break
            
            if not result['has_entry']:
                if requires_close_below and not has_closed_below:
                    result['reason'] = f'Waiting for close below {reference_high} (day opened above)'
                else:
                    result['reason'] = f'No entry: low < {reference_high}, close > {reference_high + 5} not met'
                return result
        
        elif entry_mode == 'high_cross':
            # HIGH CROSS MODE: Entry when 5-min high + 5 crosses above the reference level
            # WITH ALL existing Candle Open validations applied
            
            day_open = candles[0].get('close', 0) if candles else 0
            requires_close_below = day_open > reference_high  # If day opened above ref high, need a close below first
            has_closed_below = False  # Track if we've seen a close below reference high
            previous_high_plus_5 = None
            
            for idx, candle in enumerate(candles):
                candle_high = candle.get('high', 0)
                candle_close = candle.get('close', 0)
                candle_low = candle.get('low', 0)
                is_first_candle = (idx == 0)
                current_high_plus_5 = candle_high + 5
                
                # If day opened above ref high, first check if this candle closes below ref high
                if requires_close_below and not has_closed_below and not is_first_candle:
                    logger.info(f"{side} Candle {idx}: Waiting for close < {reference_high}. Current close: {candle_close}")
                    if candle_close < reference_high:
                        has_closed_below = True
                        logger.info(f"{side} Candle {idx} closed below {reference_high}, entry condition now valid")
                    previous_high_plus_5 = current_high_plus_5
                    continue
                
                # Check ALL conditions for High Cross entry:
                # 1. High + 5 must cross above reference level
                # 2. Close must be within 20 points of reference_high
                # 3. Low < reference_high (price touched/crossed the level)
                entry_threshold = reference_high + 5
                close_ref_diff = abs(candle_close - reference_high)
                high_crossed = previous_high_plus_5 is not None and previous_high_plus_5 <= reference_high and current_high_plus_5 > reference_high
                
                logger.info(f"{side} Candle {idx}: High={candle_high:.2f}, High+5={current_high_plus_5:.2f}, Ref={reference_high:.2f}, Low={candle_low:.2f}, Close={candle_close:.2f}")
                logger.info(f"{side}   Cross={high_crossed}, CloseRefDiff={close_ref_diff:.2f}, LowCheck={candle_low < reference_high}")
                
                # Entry signal: ALL conditions met
                if (high_crossed and 
                    candle_low < reference_high and 
                    candle_close > entry_threshold and 
                    close_ref_diff < 20):
                    
                    entry_candle_idx = idx
                    result['has_entry'] = True
                    raw_entry_time = candle.get('time', candle.get('date'))
                    if raw_entry_time is None:
                        logger.error(f"{side} Entry time is None for candle {idx}")
                        continue
                    ist_offset_seconds = int(5.5 * 3600)
                    true_utc_time = raw_entry_time - ist_offset_seconds
                    close_time = true_utc_time + 300
                    result['entry_time'] = close_time
                    result['entry_price'] = candle_close  # Entry at current candle close
                    
                    ratio_type = '1:2' if risk_reward_ratio in ['1:2', '1:2-trail'] else '1:3'
                    sl_data = self.calculate_sl_for_entry(candle_close, reference_high, ratio=ratio_type)
                    result['sl'] = sl_data.get('sl')
                    result['target'] = sl_data.get('target')
                    
                    entry_reason = "Entry (High Cross)"
                    if is_first_candle:
                        entry_reason += " [first candle]"
                    elif requires_close_below:
                        entry_reason += " [after close below]"
                    logger.info(f"{side} {entry_reason} at candle {idx}: High {candle_high} + 5 crossed > {reference_high}, Price {candle_close}, SL {result['sl']}, Target {result['target']}")
                    break
                
                previous_high_plus_5 = current_high_plus_5
            
            if not result['has_entry']:
                result['reason'] = f'No entry: High + 5 never crossed above {reference_high}'
                return result
        
        # Search for exit point (from entry candle onwards)
        if entry_candle_idx is not None:
            initial_sl = result['sl']
            initial_target = result['target']
            sl_distance = result['entry_price'] - initial_sl  # Distance between entry and SL
            target_hit = False  # Track if target was hit for trailing SL (only for trailing mode)
            trailed_sl = initial_sl  # Current trailed SL value
            use_trailing_sl = (risk_reward_ratio == '1:2-trail')  # Only use trailing SL for '1:2-trail' mode
            
            for idx in range(entry_candle_idx + 1, len(candles)):
                candle = candles[idx]
                candle_high = candle.get('high', 0)
                candle_low = candle.get('low', 0)
                candle_close = candle.get('close', 0)
                candle_time = candle.get('time', candle.get('date'))
                
                # Check if it's the last candle (3:20 PM) - auto-exit if still open
                is_last_candle = (idx == len(candles) - 1)
                
                # For non-trailing modes (1:2 and 1:3), check target and SL first
                if not use_trailing_sl:
                    # Check if target is hit (exit at target price for non-trailing modes)
                    if candle_high >= initial_target:
                        raw_exit_time = candle_time
                        if raw_exit_time is None:
                            logger.error(f"{side} Exit time is None for candle {idx}")
                            continue
                        ist_offset_seconds = int(5.5 * 3600)
                        true_utc_time = raw_exit_time - ist_offset_seconds
                        close_time = true_utc_time + 300
                        result['exit_time'] = close_time
                        result['exit_price'] = initial_target
                        result['exit_reason'] = 'Target Hit'
                        result['pnl'] = initial_target - result['entry_price']
                        logger.info(f"{side} Exit at {result['exit_time']}: Target hit at {initial_target}, PnL {result['pnl']}")
                        break
                    
                    # Check if SL is hit (for non-trailing modes)
                    if candle_low <= initial_sl:
                        raw_exit_time = candle_time
                        if raw_exit_time is None:
                            logger.error(f"{side} Exit time is None for candle {idx}")
                            continue
                        ist_offset_seconds = int(5.5 * 3600)
                        true_utc_time = raw_exit_time - ist_offset_seconds
                        close_time = true_utc_time + 300
                        result['exit_time'] = close_time
                        result['exit_price'] = initial_sl
                        result['exit_reason'] = 'SL Hit'
                        result['pnl'] = -(result['entry_price'] - initial_sl)
                        logger.info(f"{side} Exit at {result['exit_time']}: SL hit, PnL {result['pnl']}")
                        break
                else:
                    # For trailing mode (1:2-trail), implement trailing SL after target is hit
                    
                    # If target was hit, implement trailing stop loss
                    if target_hit:
                        # Calculate how much price has moved above entry since target was hit
                        price_above_entry = candle_high - result['entry_price']
                        
                        # Trail SL by sl_distance for every sl_distance movement above entry
                        if price_above_entry > 0:
                            num_trails = int(price_above_entry / sl_distance)
                            new_trailed_sl = result['entry_price'] + (num_trails * sl_distance)
                            
                            if new_trailed_sl > trailed_sl:
                                trailed_sl = new_trailed_sl
                                logger.info(f"{side} Trailing SL updated: {trailed_sl} (price high: {candle_high})")
                        else:
                            # MISSING FIX: If price pulls back below entry after target hit, 
                            # ensure SL stays at least at entry_price (lock in the gain)
                            if trailed_sl < result['entry_price']:
                                trailed_sl = result['entry_price']
                                logger.info(f"{side} SL locked at entry: {result['entry_price']} (price pulled back to {candle_high})")
                        
                        # Check if trailed SL is hit
                        if candle_low <= trailed_sl:
                            raw_exit_time = candle_time
                            if raw_exit_time is None:
                                logger.error(f"{side} Exit time is None for candle {idx}")
                                continue
                            ist_offset_seconds = int(5.5 * 3600)
                            true_utc_time = raw_exit_time - ist_offset_seconds
                            close_time = true_utc_time + 300
                            result['exit_time'] = close_time
                            result['exit_price'] = trailed_sl
                            result['exit_reason'] = f'Trailed SL Hit ({trailed_sl})'
                            result['pnl'] = trailed_sl - result['entry_price']
                            logger.info(f"{side} Exit at {result['exit_time']}: Trailed SL hit at {trailed_sl}, PnL {result['pnl']}")
                            break
                    else:
                        # Before target is hit, check for SL hit in trailing mode
                        if candle_low <= initial_sl:
                            raw_exit_time = candle_time
                            if raw_exit_time is None:
                                logger.error(f"{side} Exit time is None for candle {idx}")
                                continue
                            ist_offset_seconds = int(5.5 * 3600)
                            true_utc_time = raw_exit_time - ist_offset_seconds
                            close_time = true_utc_time + 300
                            result['exit_time'] = close_time
                            result['exit_price'] = initial_sl
                            result['exit_reason'] = 'SL Hit'
                            result['pnl'] = -(result['entry_price'] - initial_sl)
                            logger.info(f"{side} Exit at {result['exit_time']}: SL hit, PnL {result['pnl']}")
                            break
                        
                        # Check if target is hit (activate trailing SL for trailing mode)
                        if candle_high >= initial_target:
                            target_hit = True
                            trailed_sl = result['entry_price']  # Move SL to entry price when target hit
                            logger.info(f"{side} Target hit at {candle_high}, trailing SL activated at {result['entry_price']}")
                            continue  # Don't exit yet, continue to look for trailing SL exit or better prices
                
                # Check if it's the last candle (3:20 PM) - exit at market close
                if is_last_candle:
                    raw_exit_time = candle_time
                    if raw_exit_time is None:
                        logger.error(f"{side} Exit time is None for last candle {idx}")
                        continue
                    ist_offset_seconds = int(5.5 * 3600)
                    true_utc_time = raw_exit_time - ist_offset_seconds
                    close_time = true_utc_time + 300
                    result['exit_time'] = close_time
                    result['exit_price'] = candle_close
                    result['exit_reason'] = 'Market Close (3:20 PM)'
                    result['pnl'] = candle_close - result['entry_price']
                    logger.info(f"{side} Exit at {result['exit_time']}: Market close at 3:20 PM, Price {candle_close}, PnL {result['pnl']}")
                    break
        
        # If no exit found by end of day (shouldn't happen now with 3:20 PM exit)
        if not result['exit_time'] and result['has_entry']:
            last_candle = candles[-1] if candles else {}
            result['exit_reason'] = 'No Exit'
            result['exit_price'] = last_candle.get('close', 0)
            result['pnl'] = result['exit_price'] - result['entry_price']
            logger.warning(f"{side} Trade still open at end of day - should have exited at 3:20 PM")
        
        return result