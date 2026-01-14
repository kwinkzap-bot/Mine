"""Intraday Option Trading Logic Module"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import logging
from pytz import timezone
from .Kite_data_fetch_services import KiteDataFetchService

logger = logging.getLogger(__name__)

# IST timezone for Indian markets
IST = timezone('Asia/Kolkata')


class IntradayOptionTrader:
    """Main logic for intraday option trading with PDH/PDL crossing strategy"""

    def __init__(self, kite_instance):
        """
        Initialize Intraday Option Trader
        
        Args:
            kite_instance: KiteConnect instance
        """
        self.kite = kite_instance
        self.data_service = KiteDataFetchService(kite_instance)
        self.positions = {}
        self.signals = []
        self.candle_cache = {}

    def get_symbol_payload(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol payload with live data from Kite API
        Fetches current price, PDH, PDL, PDC for the underlying
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            
        Returns:
            Dictionary with current_price, pdh, pdl, pdc from live Kite data
        """
        try:
            # Map symbol to instrument key format (NSE:SYMBOL)
            symbol_map = {
                'NIFTY': 'NSE:NIFTY 50',
                'BANKNIFTY': 'NSE:NIFTY BANK',
                'FINNIFTY': 'NSE:NIFTY FIN SERVICE'
            }
            instrument_key = symbol_map.get(symbol)
            
            if not instrument_key:
                logger.error(f"Unknown symbol: {symbol}")
                return {
                    'symbol': symbol,
                    'error': f'Unknown symbol {symbol}',
                    'success': False
                }
            
            # Fetch current quote data from Kite using instrument key
            quote = self.kite.quote([instrument_key])
            quote_data = quote.get(instrument_key, {})
            
            if not quote_data:
                logger.error(f"No quote data received for {symbol} (key: {instrument_key})")
                return {
                    'symbol': symbol,
                    'error': f'No quote data for {symbol}',
                    'success': False
                }
            
            # Extract data from quote
            current_price = quote_data.get('last_price', 0)
            
            # Get current day's high/low from quote (these are intraday highs/lows)
            # The 'high' and 'low' fields in Kite quote are current day values
            day_high = quote_data.get('high', current_price)
            day_low = quote_data.get('low', current_price)
            
            logger.info(f"Quote data for {symbol}: last_price={current_price}, high={day_high}, low={day_low}")
            
            # Try to get PDC from OHLC data (previous day close)
            ohlc = quote_data.get('ohlc', {})
            pdc = ohlc.get('close', current_price)  # Previous Day Close
            logger.info(f"PDC from OHLC: {pdc}")
            
            # Initialize PDH and PDL as 0 - will be fetched from candlesticks
            pdh = 0
            pdl = 0
            
            # Fetch high and low from daily candlesticks
            # First try today's data, if not available fall back to previous day
            try:
                symbol_token = self.data_service.get_symbol_token(symbol, exchange='NSE')
                if symbol_token:
                    from datetime import datetime as dt, timedelta
                    today_ist = datetime.now(IST).date()
                    
                    # STEP 1: Try to fetch TODAY's daily OHLC first
                    logger.info(f"Attempting to fetch TODAY's OHLC for {symbol} (token: {symbol_token})")
                    
                    from_date_today = dt.combine(today_ist, dt.min.time())
                    to_date_today = dt.combine(today_ist, dt.max.time())
                    
                    daily_candles_today = self.data_service.get_candlestick_data(
                        symbol_token,
                        interval='day',
                        from_date=from_date_today,
                        to_date=to_date_today
                    )
                    
                    if daily_candles_today and len(daily_candles_today) > 0:
                        # Found today's data
                        today_candle = daily_candles_today[-1]
                        pdh = today_candle.get('high', 0)
                        pdl = today_candle.get('low', 0)
                        pdc = today_candle.get('close', pdc)
                        logger.info(f"✓ Using TODAY's OHLC for {symbol}: H={pdh}, L={pdl}, C={pdc}")
                    else:
                        # STEP 2: If today's data not available, fall back to previous trading day
                        logger.info(f"No today's data found, falling back to PREVIOUS DAY's OHLC for {symbol}")
                        
                        # Determine previous trading day
                        prev_trading_date = today_ist - timedelta(days=1)
                        weekday = prev_trading_date.weekday()
                        
                        # Skip weekends
                        if weekday == 6:  # Sunday
                            prev_trading_date = today_ist - timedelta(days=2)  # Friday
                        elif weekday == 5:  # Saturday
                            prev_trading_date = today_ist - timedelta(days=1)  # Friday
                        
                        # Fetch previous day's daily OHLC
                        from_date_prev = dt.combine(prev_trading_date - timedelta(days=1), dt.min.time())
                        to_date_prev = dt.combine(prev_trading_date, dt.max.time())
                        
                        daily_candles_prev = self.data_service.get_candlestick_data(
                            symbol_token,
                            interval='day',
                            from_date=from_date_prev,
                            to_date=to_date_prev
                        )
                        
                        if daily_candles_prev and len(daily_candles_prev) > 0:
                            # Get the most recent daily candle (previous day's data)
                            prev_day_candle = daily_candles_prev[-1]
                            pdh = prev_day_candle.get('high', 0)
                            pdl = prev_day_candle.get('low', 0)
                            pdc = prev_day_candle.get('close', pdc)
                            logger.info(f"✓ Using PREVIOUS DAY's OHLC for {symbol}: H={pdh}, L={pdl}, C={pdc}")
                        else:
                            logger.warning(f"No daily candles found for {symbol} (today or previous day)")
                            pdh = current_price
                            pdl = current_price
                else:
                    logger.warning(f"Could not get symbol token for {symbol}")
                    pdh = current_price
                    pdl = current_price
                    
            except Exception as e:
                logger.warning(f"Could not fetch previous day OHLC for {symbol}: {str(e)}")
                pdh = current_price
                pdl = current_price
            
            # If high/low are still equal to current_price (likely not set), fetch from candlesticks
            if day_high == current_price and day_low == current_price and current_price > 0:
                logger.info(f"Day high/low not in quote, fetching from candlesticks for {symbol}")
                try:
                    symbol_token = self.data_service.get_symbol_token(symbol, exchange='NSE')
                    if symbol_token:
                        from datetime import datetime as dt, timedelta
                        to_date = dt.now()
                        from_date = to_date - timedelta(hours=8)  # Get intraday candles from market open
                        
                        candles = self.data_service.get_candlestick_data(
                            symbol_token,
                            interval='5minute',
                            from_date=from_date,
                            to_date=to_date
                        )
                        
                        if candles:
                            candle_highs = [c.get('high', 0) for c in candles]
                            candle_lows = [c.get('low', 0) for c in candles]
                            if candle_highs and candle_lows:
                                day_high = max(candle_highs)
                                day_low = min(candle_lows)
                                logger.info(f"Updated day_high={day_high}, day_low={day_low} from candles")
                except Exception as e:
                    logger.warning(f"Could not fetch intraday candles for {symbol}: {str(e)}")
            
            # Calculate CE and PE strikes based on current price
            ce_strike, pe_strike = self.get_ce_pe_strikes(current_price)
            
            # Initialize tokens
            ce_token = None
            pe_token = None
            
            # Fetch candlestick data for CE and PE strikes to get their intraday H/L
            logger.info(f"Fetching candlestick data for CE strike {ce_strike} and PE strike {pe_strike}")
            
            # Determine last trading day (skip weekends) - using IST timezone
            now_ist = datetime.now(IST)
            
            # Check if today is weekend in IST (Monday=0, Sunday=6)
            weekday = now_ist.weekday()
            logger.info(f"Current IST date: {now_ist.date()}, Weekday: {weekday} (0=Mon, 6=Sun)")
            
            # For intraday data, ALWAYS use today's date (market open at 9:15 AM)
            # The get_candlestick_data will return available candles from today
            intraday_date = now_ist
            logger.info(f"Fetching intraday candles for today: {intraday_date.date()}")
            
            # For previous day close (PDC) and similar, use previous trading day
            previous_trading_day = now_ist
            if weekday == 0:  # Monday
                previous_trading_day = now_ist - timedelta(days=3)  # Go back to Friday
                logger.info(f"Today is Monday (IST), previous trading day is Friday: {previous_trading_day.date()}")
            elif weekday == 6:  # Sunday (shouldn't happen during market hours but handle it)
                previous_trading_day = now_ist - timedelta(days=2)  # Go back to Friday
                logger.info(f"Today is Sunday (IST), previous trading day is Friday: {previous_trading_day.date()}")
            elif weekday == 5:  # Saturday (shouldn't happen during market hours but handle it)
                previous_trading_day = now_ist - timedelta(days=1)  # Go back to Friday
                logger.info(f"Today is Saturday (IST), previous trading day is Friday: {previous_trading_day.date()}")
            else:
                previous_trading_day = now_ist - timedelta(days=1)  # Go back to yesterday
                logger.info(f"Today is weekday (IST), previous trading day is: {previous_trading_day.date()}")
            
            # Initialize H/L values BEFORE try block
            ce_intraday_hl = {'high': 0, 'low': 0}
            pe_intraday_hl = {'high': 0, 'low': 0}
            
            try:
                # Get tokens for CE strike (get_strike_tokens returns both CE and PE for that strike)
                ce_strike_tokens = self.data_service.get_strike_tokens(symbol, ce_strike)
                ce_token = ce_strike_tokens.get('ce_token')
                ce_symbol = ce_strike_tokens.get('ce_symbol')
                pe_token = ce_strike_tokens.get('pe_token')  # PE token from CE strike
                pe_symbol = ce_strike_tokens.get('pe_symbol')  # PE symbol from CE strike
                
                logger.info(f"CE strike {ce_strike}: CE token={ce_token}, PE token={pe_token}")
                logger.info(f"Symbols - CE: {ce_symbol}, PE: {pe_symbol}")
                
                if ce_token:
                    logger.info(f"Fetching CE candles for token {ce_token} from TODAY {intraday_date.date()}")
                    from_time = intraday_date.replace(hour=9, minute=15, second=0, microsecond=0)
                    to_time = intraday_date.replace(hour=15, minute=30, second=0, microsecond=0)
                    
                    ce_candles = self.data_service.get_candlestick_data(
                        ce_token,
                        interval='5minute',
                        from_date=from_time,
                        to_date=to_time
                    )
                    
                    if ce_candles:
                        ce_high = max([c.get('high', 0) for c in ce_candles], default=0)
                        ce_low = min([c.get('low', 0) for c in ce_candles], default=0)
                        ce_intraday_hl = {'high': ce_high, 'low': ce_low}
                        logger.info(f"CE {ce_strike}: H={ce_high}, L={ce_low}, Candles={len(ce_candles)}")
                    else:
                        logger.warning(f"No CE candles found for {ce_strike} on {intraday_date.date()}")
                        ce_intraday_hl = {'high': 0, 'low': 0}
                else:
                    logger.warning(f"CE token not found for strike {ce_strike}")
                
                if pe_token:
                    logger.info(f"Fetching PE candles for token {pe_token} from TODAY {intraday_date.date()}")
                    from_time = intraday_date.replace(hour=9, minute=15, second=0, microsecond=0)
                    to_time = intraday_date.replace(hour=15, minute=30, second=0, microsecond=0)
                    
                    pe_candles = self.data_service.get_candlestick_data(
                        pe_token,
                        interval='5minute',
                        from_date=from_time,
                        to_date=to_time
                    )
                    
                    if pe_candles:
                        pe_high = max([c.get('high', 0) for c in pe_candles], default=0)
                        pe_low = min([c.get('low', 0) for c in pe_candles], default=0)
                        pe_intraday_hl = {'high': pe_high, 'low': pe_low}
                        logger.info(f"PE {pe_strike}: H={pe_high}, L={pe_low}, Candles={len(pe_candles)}")
                    else:
                        logger.warning(f"No PE candles found for {pe_strike} on {intraday_date.date()}")
                        pe_intraday_hl = {'high': 0, 'low': 0}
                else:
                    logger.warning(f"PE token not found for strike {pe_strike}")

            except Exception as e:
                logger.error(f"Could not fetch CE/PE intraday H/L: {str(e)}", exc_info=True)
                ce_intraday_hl = {'high': 0, 'low': 0}
                pe_intraday_hl = {'high': 0, 'low': 0}
            
            return {
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'pdh': round(pdh, 2),           # Previous Day High
                'pdl': round(pdl, 2),           # Previous Day Low
                'pdc': round(pdc, 2),           # Previous Day Close
                'day_high': round(day_high, 2), # Current Day High (underlying)
                'day_low': round(day_low, 2),   # Current Day Low (underlying)
                'ce_strike': ce_strike,         # Call option strike
                'pe_strike': pe_strike,         # Put option strike
                'ce_token': ce_token,           # CE token for fast API calls
                'pe_token': pe_token,           # PE token for fast API calls
                'ce_high': round(ce_intraday_hl.get('high', 0), 2),  # CE today's intraday high
                'ce_low': round(ce_intraday_hl.get('low', 0), 2),    # CE today's intraday low
                'pe_high': round(pe_intraday_hl.get('high', 0), 2),  # PE today's intraday high
                'pe_low': round(pe_intraday_hl.get('low', 0), 2),    # PE today's intraday low
                'timestamp': datetime.now().isoformat(),
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error getting symbol payload for {symbol}: {str(e)}")
            return {
                'symbol': symbol,
                'error': f'Failed to fetch symbol data: {str(e)}',
                'success': False
            }

    def get_option_data(
        self,
        symbol: str,
        strike_price: Optional[float] = None,
        timeframe: str = '5minute',
        ce_strike: Optional[float] = None,
        pe_strike: Optional[float] = None,
        days_back: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get complete option data for intraday trading
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            strike_price: Strike price (auto-select ATM if None) - legacy parameter
            timeframe: Candle interval (5minute, 15minute, 30minute, 60minute)
            ce_strike: CE strike price (optional, overrides auto-selection)
            pe_strike: PE strike price (optional, overrides auto-selection)
            days_back: Number of days of historical data to fetch (e.g., 30, 90, 180). If None, fetches only today.
            
        Returns:
            Dictionary with CE and PE candlestick data, PDH/PDL, and current quotes
        """
        try:
            # Get underlying price
            logger.info(f"Fetching underlying quote for {symbol}")
            underlying_data = self.data_service._get_underlying_quote(symbol)
            underlying_price = underlying_data.get('last_price', 0)
            logger.info(f"Underlying price for {symbol}: {underlying_price}")
            
            if underlying_price == 0:
                logger.warning(f"Underlying price is 0 for {symbol}. Possible reasons:")
                logger.warning(f"  1. Market is closed (9:15 AM - 3:30 PM IST)")
                logger.warning(f"  2. Quote API is not responding")
                logger.warning(f"  3. Symbol is invalid")
            
            # Determine CE and PE strikes
            if ce_strike is not None and pe_strike is not None:
                # Use explicitly provided CE and PE strikes from UI
                final_ce_strike = ce_strike
                final_pe_strike = pe_strike
            elif strike_price is not None:
                # Legacy: single strike_price provided
                final_ce_strike = strike_price
                _, final_pe_strike = self.get_ce_pe_strikes(underlying_price)
            else:
                # Auto-select ATM strikes
                final_ce_strike, final_pe_strike = self.get_ce_pe_strikes(underlying_price)
            
            # Validate strike prices
            if final_ce_strike <= 0 or final_pe_strike <= 0:
                logger.error(f"Invalid strike prices: CE={final_ce_strike}, PE={final_pe_strike} for {symbol}")
                return {
                    'error': f'Invalid strike prices. Could not fetch underlying price for {symbol}',
                    'symbol': symbol,
                    'ce_strike': final_ce_strike,
                    'pe_strike': final_pe_strike,
                    'success': False
                }
            
            # Get CE and PE tokens for CE strike
            ce_strike_data = self.data_service.get_strike_tokens(symbol, final_ce_strike)
            ce_token = ce_strike_data.get('ce_token')
            ce_symbol = ce_strike_data.get('ce_symbol')
            
            # Get PE token for PE strike
            pe_strike_data = self.data_service.get_strike_tokens(symbol, final_pe_strike)
            pe_token = pe_strike_data.get('pe_token')
            pe_symbol = pe_strike_data.get('pe_symbol')
            
            if not ce_token or not pe_token:
                error_msg = f'Could not find option contracts for {symbol}. '
                if not ce_token:
                    error_msg += f'CE strike {final_ce_strike} not found. '
                if not pe_token:
                    error_msg += f'PE strike {final_pe_strike} not found. '
                error_msg += 'Instrument may not exist or market may be closed.'
                
                logger.warning(f"{error_msg} (CE token: {ce_token}, PE token: {pe_token})")
                
                # Try to get available strikes for suggestion
                available_strikes = self.data_service.get_available_strikes(symbol, range_size=10)
                
                # Build response with suggestions
                response = {
                    'error': error_msg,
                    'symbol': symbol,
                    'ce_strike': final_ce_strike,
                    'pe_strike': final_pe_strike,
                    'ce_symbol': ce_symbol,
                    'pe_symbol': pe_symbol,
                    'success': False,
                    'message': 'Check /api/intraday-option/debug-strikes to see available strikes'
                }
                
                # Add suggestions if available strikes found
                if available_strikes:
                    response['suggestions'] = {
                        'available_strikes': [s['strike'] for s in available_strikes],
                        'try_using': int(available_strikes[0]['strike'])
                    }
                    logger.info(f"Found {len(available_strikes)} available strikes to suggest")
                else:
                    response['suggestions'] = None
                    response['diagnostic'] = {
                        'issue': 'No strikes found for this symbol',
                        'possible_reasons': [
                            'Market is closed',
                            'No options available for this expiry',
                            'Symbol does not have active options',
                            'API token list needs refresh'
                        ],
                        'next_steps': [
                            'Check if NSE market is open (9:15 AM - 3:30 PM IST)',
                            'Try calling debug endpoint: /api/intraday-option/debug-strikes?symbol={symbol}',
                            'Check server logs for detailed error information'
                        ]
                    }
                
                return response
            
            # Fetch candlestick data (with optional historical data)
            logger.info(f"Fetching candlestick data: CE token={ce_token}, PE token={pe_token}, timeframe={timeframe}, days_back={days_back}")
            ce_candles = self.data_service.get_candlestick_data(ce_token, interval=timeframe, days_back=days_back)
            pe_candles = self.data_service.get_candlestick_data(pe_token, interval=timeframe, days_back=days_back)
            
            logger.info(f"Candlestick data retrieved: CE={len(ce_candles)} candles, PE={len(pe_candles)} candles")
            if len(ce_candles) == 0:
                logger.warning(f"No CE candles returned for token {ce_token}. Market may be closed.")
            if len(pe_candles) == 0:
                logger.warning(f"No PE candles returned for token {pe_token}. Market may be closed.")
            
            # Calculate PDH/PDL
            ce_pdh_pdl = self.data_service.calculate_pdh_pdl(ce_candles)
            pe_pdh_pdl = self.data_service.calculate_pdh_pdl(pe_candles)
            logger.debug(f"CE PDH/PDL: {ce_pdh_pdl}, PE PDH/PDL: {pe_pdh_pdl}")
            
            # Calculate today's intraday high/low from candles
            ce_intraday_high_low = self._calculate_today_intraday_high_low(ce_candles)
            pe_intraday_high_low = self._calculate_today_intraday_high_low(pe_candles)
            logger.debug(f"CE Intraday High/Low: {ce_intraday_high_low}, PE Intraday High/Low: {pe_intraday_high_low}")
            
            # Get current quotes
            logger.info(f"Fetching quotes for tokens: {ce_token}, {pe_token}")
            quotes = self.data_service.get_ltp_quote([ce_token, pe_token])
            ce_quote = quotes.get(ce_token, {})
            pe_quote = quotes.get(pe_token, {})
            logger.info(f"CE quote: LTP={ce_quote.get('last_price', 0)}, Bid={ce_quote.get('bid', 0)}, Ask={ce_quote.get('ask', 0)}")
            logger.info(f"PE quote: LTP={pe_quote.get('last_price', 0)}, Bid={pe_quote.get('bid', 0)}, Ask={pe_quote.get('ask', 0)}")
            
            # Check for trading signals
            signal = self._check_trading_signal(ce_candles, pe_candles, ce_pdh_pdl, pe_pdh_pdl)
            
            return {
                'symbol': symbol,
                'ce_strike': final_ce_strike,
                'pe_strike': final_pe_strike,
                'underlying_price': underlying_price,
                'ce_symbol': ce_symbol,
                'pe_symbol': pe_symbol,
                'ce_token': ce_token,
                'pe_token': pe_token,
                'timestamp': datetime.now().isoformat(),
                'ce_data': {
                    'candles': ce_candles if ce_candles else [],  # All candles data
                    'pdh': ce_pdh_pdl.get('pdh', 0),
                    'pdl': ce_pdh_pdl.get('pdl', 0),
                    'current_price': ce_quote.get('last_price', 0),
                    'bid': ce_quote.get('bid', 0),
                    'ask': ce_quote.get('ask', 0),
                    'volume': ce_quote.get('volume', 0),
                    'day_high': ce_intraday_high_low.get('high', 0),  # Today's intraday high
                    'day_low': ce_intraday_high_low.get('low', 0),   # Today's intraday low
                    # Cross-leg reference levels: PE's today's intraday high/low to display on CE chart
                    'pe_day_high': pe_intraday_high_low.get('high', 0),
                    'pe_day_low': pe_intraday_high_low.get('low', 0),
                    'pe_current': pe_quote.get('last_price', 0)
                },
                'pe_data': {
                    'candles': pe_candles if pe_candles else [],  # All candles data
                    'pdh': pe_pdh_pdl.get('pdh', 0),
                    'pdl': pe_pdh_pdl.get('pdl', 0),
                    'current_price': pe_quote.get('last_price', 0),
                    'bid': pe_quote.get('bid', 0),
                    'ask': pe_quote.get('ask', 0),
                    'volume': pe_quote.get('volume', 0),
                    'day_high': pe_intraday_high_low.get('high', 0),  # Today's intraday high
                    'day_low': pe_intraday_high_low.get('low', 0),   # Today's intraday low
                    # Cross-leg reference levels: CE's today's intraday high/low to display on PE chart
                    'ce_day_high': ce_intraday_high_low.get('high', 0),
                    'ce_day_low': ce_intraday_high_low.get('low', 0),
                    'ce_current': ce_quote.get('last_price', 0)
                },
                'reference_levels': {
                    'ce_strike': final_ce_strike,
                    'ce_day_high': ce_intraday_high_low.get('high', 0),
                    'ce_day_low': ce_intraday_high_low.get('low', 0),
                    'ce_current': ce_quote.get('last_price', 0),
                    'pe_strike': final_pe_strike,
                    'pe_day_high': pe_intraday_high_low.get('high', 0),
                    'pe_day_low': pe_intraday_high_low.get('low', 0),
                    'pe_current': pe_quote.get('last_price', 0)
                },
                'signal': signal,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error getting option data for {symbol}: {str(e)}")
            return {
                'error': str(e),
                'symbol': symbol,
                'success': False
            }

    def _check_trading_signal(
        self,
        ce_candles: List[Dict[str, Any]],
        pe_candles: List[Dict[str, Any]],
        ce_pdh_pdl: Dict[str, float],
        pe_pdh_pdl: Dict[str, float]
    ) -> Optional[Dict[str, Any]]:
        """
        Check for trading signals based on PDH/PDL crossing strategy
        
        Entry Logic:
        - CE entry: When CE crosses above CE PDH AND PE trades below CE PDL
        - PE entry: When PE crosses above PE PDH AND CE trades below PE PDL
        
        Args:
            ce_candles: CE candlestick data
            pe_candles: PE candlestick data
            ce_pdh_pdl: CE PDH and PDL values
            pe_pdh_pdl: PE PDH and PDL values
            
        Returns:
            Signal dictionary or None if no signal
        """
        if not ce_candles or not pe_candles:
            return None
        
        ce_current = ce_candles[-1]['close']
        pe_current = pe_candles[-1]['close']
        
        ce_pdh = ce_pdh_pdl.get('pdh', 0)
        ce_pdl = ce_pdh_pdl.get('pdl', 0)
        pe_pdh = pe_pdh_pdl.get('pdh', 0)
        pe_pdl = pe_pdh_pdl.get('pdl', 0)
        
        # CE Entry Signal
        if ce_current > ce_pdh and pe_current < ce_pdl:
            return {
                'type': 'BUY_CE',
                'message': f'CE BUY Signal: CE crossed above PDH ({ce_pdh:.2f}) and PE trading below CE PDL ({ce_pdl:.2f})',
                'ce_price': ce_current,
                'pe_price': pe_current,
                'ce_pdh': ce_pdh,
                'ce_pdl': ce_pdl,
                'pe_pdh': pe_pdh,
                'pe_pdl': pe_pdl,
                'timestamp': datetime.now().isoformat()
            }
        
        # PE Entry Signal
        if pe_current > pe_pdh and ce_current < pe_pdl:
            return {
                'type': 'BUY_PE',
                'message': f'PE BUY Signal: PE crossed above PDH ({pe_pdh:.2f}) and CE trading below PE PDL ({pe_pdl:.2f})',
                'ce_price': ce_current,
                'pe_price': pe_current,
                'ce_pdh': ce_pdh,
                'ce_pdl': ce_pdl,
                'pe_pdh': pe_pdh,
                'pe_pdl': pe_pdl,
                'timestamp': datetime.now().isoformat()
            }
        
        return None

    def _calculate_today_intraday_high_low(self, candles: list) -> dict:
        """
        Calculate today's intraday high and low from candlestick data.
        Filters candles to only today's date and finds the highest high and lowest low.
        
        Args:
            candles: List of candle data with 'time', 'high', 'low' fields
            
        Returns:
            Dictionary with 'high' and 'low' keys for today's intraday price levels
        """
        if not candles:
            return {'high': 0, 'low': 0}
        
        try:
            from datetime import datetime
            import pytz
            
            # Get today's date in IST
            ist = pytz.timezone('Asia/Kolkata')
            now_ist = datetime.now(ist)
            today_date = now_ist.date()
            
            # Filter candles to today only
            today_candles = []
            for candle in candles:
                try:
                    # Convert timestamp to datetime
                    if isinstance(candle.get('time'), (int, float)):
                        candle_dt = datetime.fromtimestamp(candle['time'], tz=ist)
                    else:
                        candle_dt = candle.get('date')
                        if isinstance(candle_dt, str):
                            candle_dt = datetime.fromisoformat(candle_dt)
                        if candle_dt.tzinfo is None:
                            candle_dt = ist.localize(candle_dt)
                        else:
                            candle_dt = candle_dt.astimezone(ist)
                    
                    # Check if candle is from today
                    if candle_dt.date() == today_date:
                        today_candles.append(candle)
                except Exception as e:
                    logger.warning(f"Error processing candle timestamp: {e}")
                    continue
            
            # If no candles from today, use all available candles
            if not today_candles:
                logger.warning("No candles from today found, using all available candles")
                today_candles = candles
            
            # Calculate high and low
            highs = [float(c.get('high', 0)) for c in today_candles if c.get('high')]
            lows = [float(c.get('low', 0)) for c in today_candles if c.get('low')]
            
            intraday_high = max(highs) if highs else 0
            intraday_low = min(lows) if lows else 0
            
            logger.info(f"Today's intraday H/L calculated from {len(today_candles)} candles: H={intraday_high:.2f}, L={intraday_low:.2f}")
            
            return {
                'high': intraday_high,
                'low': intraday_low
            }
        except Exception as e:
            logger.error(f"Error calculating today's intraday H/L: {e}")
            return {'high': 0, 'low': 0}

    def _select_atm_strike(self, underlying_price: float) -> float:
        """
        Auto-select At-The-Money (ATM) strike based on price proximity to 25, 50, 75, 100 levels
        
        Logic: If NIFTY is 26064.65, it's close to 75 (26075 level)
        So CE strike = 26050 and PE strike = 26100
        
        Args:
            underlying_price: Current price of underlying
            
        Returns:
            CE strike price (rounded based on nearest 25-point level)
        """
        ce_strike, _ = self.get_ce_pe_strikes(underlying_price)
        return ce_strike
    
    def get_ce_pe_strikes(self, underlying_price: float) -> tuple:
        """
        Calculate CE and PE strike prices based on nearest 25-point level.
        
        First, determine which 25-point level (00, 25, 50, 75) the price is nearest to.
        Then apply strike selection based on that level:
        - Near 00: CE and PE both at XX00 (straddle)
        - Near 25: CE at XX00, PE at XX50 (25-point strangle)
        - Near 50: CE and PE both at XX50 (straddle)
        - Near 75: CE at XX50, PE at XX00 (next hundred) (25-point strangle)
        
        Examples:
        - 25770 (ends in 70) Near to 75 → CE=25750, PE=25800 ✓
        - 25720 (ends in 20) Near to 25 → CE=25700, PE=25750 ✓
        - 25805 (ends in 05) Near to 00 → CE=25800, PE=25800 ✓
        - 25741 (ends in 41) Near to 50 → CE=25750, PE=25750 ✓
        - 26064.65 (ends in 64) Near to 75 → CE=26050, PE=26100 ✓
        - 26025.10 (ends in 25) Near to 25 → CE=26000, PE=26050 ✓
        
        Args:
            underlying_price: Current price of underlying (e.g., 25770.45)
            
        Returns:
            Tuple of (CE strike, PE strike)
        """
        # underlying_price = float(25685)  # Ensure float
        # Get the last 2 digits (ones and tens place)
        last_two_digits = int(underlying_price) % 100
        
        # Get the base 100 (hundreds place)
        base_100 = (int(underlying_price) // 100) * 100
        
        # Determine which 25-point level the price is nearest to
        # Distance ranges: 0-12.5 → 00, 12.5-37.5 → 25, 37.5-62.5 → 50, 62.5-87.5 → 75, 87.5-100 → 100
        if last_two_digits <= 12:
            near_level = 0  # Nearest to 00
        elif last_two_digits <= 37:
            near_level = 25  # Nearest to 25
        elif last_two_digits <= 62:
            near_level = 50  # Nearest to 50
        elif last_two_digits <= 87:
            near_level = 75  # Nearest to 75
        else:  # 88-99
            near_level = 100  # Nearest to 100 (next hundred)
        
        # Calculate the rounded number based on nearest 25-point level
        if near_level == 100:
            # Nearest to next hundred: round to base_100 + 100
            rounded_number = base_100 + 100
        else:
            # Nearest to current hundred's 25-point level
            rounded_number = base_100 + near_level
        
        # Calculate CE and PE strikes based on nearest 25-point level
        if near_level == 0:
            # Near 00: Both at rounded_number (straddle)
            ce_strike = rounded_number
            pe_strike = rounded_number
        elif near_level == 25:
            # Near 25: CE = rounded_number - 25, PE = rounded_number + 25 (strangle)
            ce_strike = rounded_number - 25
            pe_strike = rounded_number + 25
        elif near_level == 50:
            # Near 50: Both at rounded_number (straddle)
            ce_strike = rounded_number
            pe_strike = rounded_number
        elif near_level == 75:
            # Near 75: CE = rounded_number - 25, PE = rounded_number + 25 (strangle)
            ce_strike = rounded_number - 25
            pe_strike = rounded_number + 25
        else:  # near_level == 100
            # Near 100: Both at rounded_number (straddle)
            ce_strike = rounded_number
            pe_strike = rounded_number
        
        logger.info(f"Strike calculation: {underlying_price:.2f} (ends in {last_two_digits:02d}, near {near_level}, rounded to {rounded_number}) → CE={ce_strike}, PE={pe_strike}")
        
        return int(ce_strike), int(pe_strike)

    def get_multiple_strikes(
        self,
        symbol: str,
        strike_price: float,
        num_strikes: int = 5
    ) -> Dict[str, Any]:
        """
        Get data for multiple strikes around a central strike
        
        Args:
            symbol: Trading symbol
            strike_price: Central strike price
            num_strikes: Number of strikes above and below
            
        Returns:
            Dictionary with data for multiple strikes
        """
        try:
            strikes_data = {}
            
            # Generate strike prices (typically 100 or 500 apart)
            step = 100 if symbol in ['NIFTY', 'BANKNIFTY'] else 50
            
            strikes = [strike_price + (i * step) for i in range(-num_strikes, num_strikes + 1)]
            
            for strike in strikes:
                option_data = self.get_option_data(symbol, strike)
                strikes_data[f"{strike:.0f}"] = option_data
            
            return {
                'symbol': symbol,
                'central_strike': strike_price,
                'strikes': strikes_data,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting multiple strikes for {symbol}: {str(e)}")
            return {'error': str(e), 'symbol': symbol}

    def get_position_info(self) -> Dict[str, Any]:
        """
        Get current position information
        
        Returns:
            Dictionary with current positions and P&L
        """
        try:
            positions = self.kite.positions()
            
            formatted_positions = {
                'open_positions': [],
                'total_pnl': 0,
                'total_unrealised_pnl': 0,
                'timestamp': datetime.now().isoformat()
            }
            
            for position in positions.get('net', []):
                formatted_positions['open_positions'].append({
                    'symbol': position.get('tradingsymbol'),
                    'quantity': position.get('quantity'),
                    'average_price': position.get('average_price'),
                    'last_price': position.get('last_price'),
                    'pnl': position.get('pnl'),
                    'unrealised': position.get('unrealised'),
                    'realised': position.get('realised')
                })
                formatted_positions['total_pnl'] += position.get('pnl', 0)
                formatted_positions['total_unrealised_pnl'] += position.get('unrealised', 0)
            
            return formatted_positions
            
        except Exception as e:
            logger.error(f"Error getting position info: {str(e)}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
