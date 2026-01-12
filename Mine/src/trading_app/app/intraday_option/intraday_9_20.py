"""Intraday 9:20 Strategy - First 5 Minute Candle High/Low Based Trading"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
from .Kite_data_fetch_services import KiteDataFetchService

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
            max_retries = 30  # Try up to 30 days back
            for attempt in range(max_retries):
                # Skip weekends
                while current_check_date.weekday() in [5, 6]:  # Saturday=5, Sunday=6
                    current_check_date -= timedelta(days=1)
                
                # Fetch from 9:15 AM to 9:20 AM of current check date
                from_date = current_check_date.replace(minute=15)
                to_date = current_check_date
                
                logger.info(f"Attempt {attempt + 1}: Fetching candles from {from_date} to {to_date}")
                
                try:
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
                        
                        logger.info(f"Found first 5min candle for {symbol} on {current_check_date.date()}: High={first_5min_high}, Low={first_5min_low}, Close={first_5min_close}")
                        
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
                        logger.warning(f"No candles found for {symbol} on {current_check_date.date()}, trying previous day...")
                
                except Exception as e:
                    logger.warning(f"Error fetching candles for {current_check_date.date()}: {str(e)}, trying previous day...")
                
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
                strike_data = self.data_service.get_strike_tokens(symbol, ce_strike, pe_strike)
                ce_token = strike_data.get('ce_token')
                pe_token = strike_data.get('pe_token')
                
                logger.info(f"Got tokens - CE Token: {ce_token}, PE Token: {pe_token}")
                
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
            
            # Fetch current quote data from Kite
            quote = self.kite.quote([instrument_key])
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
            
            # Determine the reference date (date of the first 5-min candle we're using)
            if target_date:
                reference_date = target_date.replace(hour=9, minute=20, second=0, microsecond=0)
            else:
                now = datetime.now()
                reference_date = now.replace(hour=9, minute=20, second=0, microsecond=0)
                
                # Calculate the same last trading day used in get_first_5min_high_low
                if now.weekday() == 0:  # Monday - get Friday
                    reference_date = (now - timedelta(days=3)).replace(hour=9, minute=20, second=0, microsecond=0)
                elif now.weekday() in [5, 6]:  # Saturday, Sunday - get Friday
                    days_back = now.weekday() - 4
                    reference_date = (now - timedelta(days=days_back)).replace(hour=9, minute=20, second=0, microsecond=0)
                else:  # Weekday
                    if now.hour < 9 or (now.hour == 9 and now.minute < 20):
                        reference_date = (now - timedelta(days=1)).replace(hour=9, minute=20, second=0, microsecond=0)
                    else:
                        reference_date = (now - timedelta(days=1)).replace(hour=9, minute=20, second=0, microsecond=0)
            
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
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            with ThreadPoolExecutor(max_workers=2) as executor:
                high_future = executor.submit(self.get_strike_data, symbol, high, True, reference_date)
                low_future = executor.submit(self.get_strike_data, symbol, low, True, reference_date)
                
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
