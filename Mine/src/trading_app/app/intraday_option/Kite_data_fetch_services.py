"""Kite Data Fetch Services for Intraday Option Trading"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
import threading
import random

logger = logging.getLogger(__name__)


class KiteDataFetchService:
    """Service to fetch real-time option data from Kite API"""

    def __init__(self, kite_instance):
        """
        Initialize Kite Data Fetch Service
        
        Args:
            kite_instance: KiteConnect instance for API calls
        """
        self.kite = kite_instance
        self.cache = {}
        self.cache_duration = 5  # Cache for 5 seconds
        self.instruments_cache = {}  # Cache for instruments list
        self.instruments_cache_expiry = 0  # Timestamp when cache expires (1 hour)
        # Disk cache for NFO instruments
        self._nfo_cache_file = os.path.join(os.path.dirname(__file__), '..', '..', '.cache', 'nfo_instruments.json')
        os.makedirs(os.path.dirname(self._nfo_cache_file), exist_ok=True)
        # Rate limiting
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0

    def _load_nfo_from_disk_cache(self) -> Optional[List[Dict[str, Any]]]:
        """Load NFO instruments from disk cache if available and recent (24h)."""
        try:
            if os.path.exists(self._nfo_cache_file):
                stat = os.stat(self._nfo_cache_file)
                age = time.time() - stat.st_mtime
                # Cache is valid if less than 24 hours old
                if age < 86400:
                    with open(self._nfo_cache_file, 'r') as f:
                        data = json.load(f)
                    # Convert expiry strings back to datetime objects
                    for inst in data:
                        if 'expiry' in inst and isinstance(inst['expiry'], str):
                            try:
                                inst['expiry'] = datetime.strptime(inst['expiry'], '%Y-%m-%d')
                            except:
                                pass  # Keep as string if conversion fails
                    logger.info(f"✓ Loaded NFO instruments from disk cache ({age/3600:.1f}h old, {len(data)} records)")
                    return data
        except Exception as e:
            logger.warning(f"Error loading disk cache: {e}")
        return None

    def _save_nfo_to_disk_cache(self, instruments: List[Dict[str, Any]]) -> None:
        """Save NFO instruments to disk cache."""
        try:
            # Convert datetime objects to strings for JSON serialization
            serializable_instruments = []
            for inst in instruments:
                inst_copy = inst.copy()
                if 'expiry' in inst_copy and inst_copy['expiry']:
                    expiry = inst_copy['expiry']
                    if hasattr(expiry, 'isoformat'):
                        inst_copy['expiry'] = expiry.isoformat()
                    elif hasattr(expiry, 'strftime'):
                        inst_copy['expiry'] = expiry.strftime('%Y-%m-%d')
                serializable_instruments.append(inst_copy)
            
            with open(self._nfo_cache_file, 'w') as f:
                json.dump(serializable_instruments, f)
            logger.info(f"✓ Saved {len(serializable_instruments)} NFO instruments to disk cache")
        except Exception as e:
            logger.warning(f"Error saving to disk cache: {e}")

    def _respect_rate_limit(self, min_gap_seconds: float = 0.25):
        """Ensure a minimum gap between outbound Kite API requests to avoid rate limiting."""
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_ts
            if elapsed < min_gap_seconds:
                time.sleep(min_gap_seconds - elapsed)
            self._last_request_ts = time.time()

    def _historical_data_with_retry(
        self, 
        instrument_token: int, 
        from_date: datetime, 
        to_date: datetime, 
        interval: str, 
        max_retries: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical data with exponential backoff retry and rate limiting.
        
        Implements pattern from OptionsChartService for better reliability.
        """
        attempt = 0
        while True:
            try:
                self._respect_rate_limit(min_gap_seconds=0.25)
                logger.debug(f"Fetching historical data: token={instrument_token}, from={from_date.date()}, to={to_date.date()}, interval={interval}")
                
                candles = self.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval
                )
                
                logger.debug(f"✓ Successfully fetched {len(candles)} candles for token {instrument_token}")
                return candles
                
            except Exception as e:
                msg = str(e) if e else "Unknown error"
                is_rate_limit = 'Too many requests' in msg or '429' in msg
                
                # Determine if we should retry
                if attempt >= max_retries:
                    logger.error(f"historical_data failed after {max_retries} retries for token {instrument_token}: {msg}")
                    return []
                
                # Exponential backoff with jitter
                base = 0.5 * (2 ** attempt)
                sleep_seconds = min(8.0, base + random.uniform(0, 0.4))
                
                if is_rate_limit:
                    logger.warning(f"Rate limited on attempt {attempt+1}/{max_retries}. Backing off {sleep_seconds:.2f}s")
                else:
                    logger.warning(f"Error on attempt {attempt+1}/{max_retries} for token {instrument_token}: {msg}. Backing off {sleep_seconds:.2f}s")
                
                time.sleep(sleep_seconds)
                attempt += 1

    def _quote_with_retry(
        self, 
        tokens: List[int], 
        max_retries: int = 3
    ) -> Dict[int, Dict[str, Any]]:
        """
        Fetch quotes with exponential backoff retry and rate limiting.
        
        Implements pattern from OptionsChartService for better reliability.
        """
        attempt = 0
        while True:
            try:
                self._respect_rate_limit(min_gap_seconds=0.2)
                logger.debug(f"Fetching quotes for tokens: {tokens}")
                
                quotes = self.kite.quote(tokens)
                logger.debug(f"Raw quotes response: {quotes}")
                logger.debug(f"✓ Successfully fetched quotes for {len(quotes)} tokens")
                return quotes
                
            except Exception as e:
                msg = str(e) if e else "Unknown error"
                is_rate_limit = 'Too many requests' in msg or '429' in msg
                
                # Determine if we should retry
                if attempt >= max_retries:
                    logger.error(f"quote failed after {max_retries} retries for tokens {tokens}: {msg}")
                    return {}
                
                # Exponential backoff with jitter
                base = 0.5 * (2 ** attempt)
                sleep_seconds = min(8.0, base + random.uniform(0, 0.4))
                
                if is_rate_limit:
                    logger.warning(f"Rate limited on quote attempt {attempt+1}/{max_retries}. Backing off {sleep_seconds:.2f}s")
                else:
                    logger.warning(f"Error on quote attempt {attempt+1}/{max_retries}: {msg}. Backing off {sleep_seconds:.2f}s")
                
                time.sleep(sleep_seconds)
                attempt += 1

    def _load_or_fetch_nfo_instruments(self) -> List[Dict[str, Any]]:
        """Load NFO instruments from cache (disk or memory) or fetch from Kite API."""
        # Try disk cache first (24h validity)
        instruments = self._load_nfo_from_disk_cache()
        if instruments:
            return instruments

        # Try memory cache (1h validity)
        now = time.time()
        if 'NFO' in self.instruments_cache and now < self.instruments_cache_expiry:
            logger.info(f"✓ Using memory-cached NFO instruments ({len(self.instruments_cache['NFO'])} records)")
            return self.instruments_cache['NFO']

        # Fetch from Kite API
        logger.info("Fetching NFO instruments from Kite API (5-10s)...")
        fetch_start = time.time()
        instruments = self.kite.instruments("NFO")
        fetch_time = time.time() - fetch_start
        logger.info(f"✓ Fetched NFO from API in {fetch_time:.1f}s ({len(instruments)} records)")

        # Save to both caches
        self.instruments_cache['NFO'] = instruments
        self.instruments_cache_expiry = now + 3600  # 1 hour validity
        self._save_nfo_to_disk_cache(instruments)

        return instruments

    def get_option_chain(self, symbol: str, expiry_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch option chain data from Kite API
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            expiry_date: Expiry date in format YYYY-MM-DD (defaults to current expiry)
            
        Returns:
            Dictionary containing option chain data
        """
        try:
            # Construct instrument tokens for CE and PE
            # Format: NIFTY25JAN23200CE, NIFTY25JAN23200PE
            
            # Get current quote for underlying
            underlying_quote = self._get_underlying_quote(symbol)
            
            # Get option chain data
            option_chain = {
                'symbol': symbol,
                'underlying_price': underlying_quote.get('last_price', 0),
                'timestamp': datetime.now().isoformat(),
                'ce_data': {},
                'pe_data': {}
            }
            
            return option_chain
            
        except Exception as e:
            logger.error(f"Error fetching option chain for {symbol}: {str(e)}")
            return {
                'symbol': symbol,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

    def get_candlestick_data(
        self, 
        instrument_token: int, 
        interval: str = 'minute',
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        days_back: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch candlestick data for a specific instrument with retry logic.
        
        Uses same pattern as OptionsChartService for reliability.
        
        NOTE: Kite API has a maximum limit of 100 days per request.
        
        Args:
            instrument_token: Kite instrument token
            interval: Candle interval (minute, 3minute, 5minute, 15minute, 30minute, 60minute, day)
            from_date: Start date (defaults to 100 days ago if not specified)
            to_date: End date (defaults to now)
            days_back: Number of days to fetch history for (e.g., 30, 90, 100). If set, overrides from_date.
                      Maximum: 100 days (Kite API limit)
            
        Returns:
            List of candlestick data dictionaries with OHLC values (all available data)
        """
        try:
            # Determine date range
            if days_back is not None and days_back > 0:
                # Fetch historical data for specified number of days
                # Cap at 100 days (Kite API limit)
                days_to_fetch = min(days_back, 100)
                from_date = datetime.now() - timedelta(days=days_to_fetch)
                if days_back > 100:
                    logger.warning(f"days_back={days_back} exceeds Kite API limit of 100 days. Fetching 100 days instead.")
            elif from_date is None:
                # Default: fetch 100 days of historical data (Kite API maximum)
                from_date = datetime.now() - timedelta(days=100)
                logger.info(f"No date range specified. Fetching 100 days of historical data (Kite API max) for token {instrument_token}")
            
            if to_date is None:
                to_date = datetime.now()
            
            # Validate date range doesn't exceed 100 days
            date_diff = (to_date - from_date).days
            if date_diff > 100:
                logger.warning(f"Date range ({date_diff} days) exceeds Kite API limit of 100 days. Truncating from_date.")
                from_date = to_date - timedelta(days=100)
            
            logger.info(f"Fetching candlestick data from {from_date.date()} to {to_date.date()} (interval: {interval}, token: {instrument_token})")
            
            # Fetch historical data from Kite WITH RETRY LOGIC
            candles = self._historical_data_with_retry(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                max_retries=5
            )
            
            if not candles:
                logger.warning(f"No candle data returned for token {instrument_token}. Possible reasons:")
                logger.warning(f"  1. Market is closed (open 9:15 AM - 3:30 PM IST)")
                logger.warning(f"  2. No trading activity during the specified time range")
                logger.warning(f"  3. Invalid instrument token")
                logger.warning(f"  4. API connectivity issue or rate limit exceeded")
                return []
            
            # Format candlestick data
            # Add IST offset to timestamps so Lightweight Charts displays correct IST time
            # When Lightweight Charts interprets timestamps as UTC, the IST offset adjustment
            # ensures displayed time matches the actual IST candle time
            ist_offset_seconds = int(5.5 * 3600)  # 19800 seconds (IST is UTC+5:30)
            
            formatted_candles = []
            for candle in candles:
                try:
                    # Get Unix timestamp from datetime
                    utc_timestamp = int(candle['date'].timestamp())
                    # Add IST offset for correct display in Lightweight Charts
                    adjusted_timestamp = utc_timestamp + ist_offset_seconds
                    
                    formatted_candles.append({
                        'time': adjusted_timestamp,
                        'open': candle['open'],
                        'high': candle['high'],
                        'low': candle['low'],
                        'close': candle['close'],
                        'volume': candle.get('volume', 0)
                    })
                except Exception as format_error:
                    logger.warning(f"Error formatting candle: {str(format_error)}, candle data: {candle}")
                    continue
            
            if formatted_candles:
                logger.info(f"✓ Retrieved {len(formatted_candles)} candles for token {instrument_token} ({from_date.date()} to {to_date.date()})")
            else:
                logger.warning(f"No valid candles could be formatted from {len(candles)} raw candles")
            
            return formatted_candles
            
        except Exception as e:
            logger.error(f"Error fetching candlestick data for {instrument_token}: {str(e)}", exc_info=True)
            return []

    def get_ltp_quote(self, instrument_tokens: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        Get last traded price and other quote data for instruments with retry logic.
        
        Uses same pattern as OptionsChartService for reliability.
        
        Args:
            instrument_tokens: List of Kite instrument tokens
            
        Returns:
            Dictionary with instrument tokens as keys and quote data as values
        """
        try:
            if not instrument_tokens:
                logger.warning("No instrument tokens provided for quote fetch")
                return {}
            
            logger.debug(f"Fetching quotes for tokens: {instrument_tokens}")
            
            # Fetch quotes WITH RETRY LOGIC
            quotes = self._quote_with_retry(
                tokens=instrument_tokens,
                max_retries=3
            )
            
            if not quotes:
                logger.warning(f"No quote data returned for tokens {instrument_tokens}. Market may be closed or tokens invalid.")
                return {}
            
            # Log quote details for debugging
            for token, quote_data in quotes.items():
                if quote_data and quote_data.get('last_price'):
                    logger.debug(f"Token {token}: LTP={quote_data.get('last_price')}, Bid={quote_data.get('bid')}, Ask={quote_data.get('ask')}")
                else:
                    logger.warning(f"Token {token}: No price data in response. Quote: {quote_data}")
            
            return quotes
            
        except Exception as e:
            logger.error(f"Error fetching quotes: {str(e)}", exc_info=True)
            return {}

    def get_symbol_token(self, symbol: str, exchange: str = 'NFO') -> Optional[int]:
        """
        Get instrument token for a symbol
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY for NSE)
            exchange: Exchange (NFO for options, NSE for equity)
            
        Returns:
            Instrument token or None if not found
        """
        try:
            # Map shorthand symbols to NSE symbols
            if exchange == 'NSE':
                symbol_map = {
                    'NIFTY': 'NIFTY 50',
                    'BANKNIFTY': 'NIFTY BANK',
                    'FINNIFTY': 'NIFTY FIN SERVICE'
                }
                search_symbol = symbol_map.get(symbol, symbol)
            else:
                search_symbol = symbol
            
            # Get instruments from cache or fetch from Kite
            if exchange not in self.instruments_cache:
                logger.info(f"Fetching instruments list for {exchange} exchange...")
                instruments = self.kite.instruments(exchange=exchange)
                self.instruments_cache[exchange] = instruments
                logger.info(f"Cached {len(instruments)} instruments for {exchange}")
            else:
                instruments = self.instruments_cache[exchange]
            
            # Try exact match first
            for instrument in instruments:
                if instrument['tradingsymbol'] == search_symbol:
                    logger.info(f"Found token for {symbol}: {instrument['instrument_token']}")
                    return instrument['instrument_token']
            
            # If exact match not found, log what we're looking for vs what we found
            logger.warning(f"Symbol '{symbol}' (searched as '{search_symbol}') not found in {exchange} instruments")
            
            # For NFO (options), try to provide suggestions
            if exchange == 'NFO' and 'NIFTY' in search_symbol:
                # Get sample options to show format
                nifty_options = [i for i in instruments if 'NIFTY' in i.get('tradingsymbol', '') and 'CE' in i.get('tradingsymbol', '')]
                if nifty_options:
                    samples = [i['tradingsymbol'] for i in nifty_options[:3]]
                    logger.info(f"Available NIFTY option format examples: {samples}")
                    logger.info(f"Searched for: {search_symbol}, but it doesn't exist in this format")
            
            return None
            
        except Exception as e:
            logger.error(f"Error fetching instrument token for {symbol}: {str(e)}")
            return None

    def get_available_strikes(self, symbol: str, range_size: int = 10) -> List[Dict[str, Any]]:
        """
        Get list of available strikes for a symbol using multi-layered caching.
        Implements the same pattern as options_chart_service.py for better performance and reliability.
        
        Args:
            symbol: Base symbol (NIFTY, BANKNIFTY, FINNIFTY)
            range_size: Number of strikes above/below ATM to check
            
        Returns:
            List of available strikes with their tokens and symbols
        """
        try:
            # Try to get underlying quote to determine ATM
            underlying_price = 0
            try:
                underlying_data = self._get_underlying_quote(symbol)
                underlying_price = underlying_data.get('last_price', 0)
                logger.info(f"Got underlying price for {symbol}: {underlying_price}")
            except Exception as e:
                logger.warning(f"Could not get underlying price for {symbol}: {str(e)}")
                # Continue anyway - we'll return all strikes and let caller filter
            
            # Determine strike range (NIFTY uses 100-point intervals)
            strike_interval = 100 if symbol == 'NIFTY' else 100 if symbol == 'BANKNIFTY' else 50
            base_strike = int(underlying_price / strike_interval) * strike_interval if underlying_price > 0 else 0
            
            logger.info(f"Checking available strikes for {symbol}, underlying={underlying_price}")
            
            # STEP 1: Load NFO instruments with multi-layered caching
            nfo_instruments = self._load_or_fetch_nfo_instruments()
            
            # STEP 2: Filter for symbol options
            symbol_options = [
                i for i in nfo_instruments 
                if i.get('name', '') == symbol and 
                   i.get('instrument_type', '') in ['CE', 'PE']
            ]
            
            logger.info(f"Found {len(symbol_options)} total options for {symbol}")
            
            if not symbol_options:
                logger.warning(f"No instruments found for {symbol}")
                return []
            
            # STEP 3: Get current expiry (nearest future expiry date)
            expiries = sorted([exp for exp in set(inst.get('expiry') for inst in symbol_options if inst.get('expiry')) if exp is not None])
            if not expiries:
                logger.warning(f"No expiries found for {symbol}")
                return []
            
            # Automatically select the nearest future expiry to handle expired options
            from datetime import datetime
            today = datetime.now().date()
            valid_expiries = []
            
            for exp in expiries:
                try:
                    # Convert to date if it's a datetime object
                    if hasattr(exp, 'date'):
                        exp_date = exp.date()
                    else:
                        exp_date = datetime.strptime(str(exp), '%Y-%m-%d').date()
                    
                    # Only consider expiries that are today or in the future
                    if exp_date >= today:
                        valid_expiries.append((exp_date, exp))
                except ValueError:
                    valid_expiries.append((None, exp))
            
            # Select the nearest future expiry, or fallback to earliest if all are in the past
            if valid_expiries:
                valid_expiries.sort(key=lambda x: x[0] if x[0] else datetime.max.date())
                current_expiry = valid_expiries[0][1]
            else:
                current_expiry = expiries[0]
            
            expiry_str = current_expiry.strftime('%Y-%m-%d') if hasattr(current_expiry, 'strftime') else str(current_expiry)
            logger.info(f"get_available_strikes: Selected current expiry {expiry_str} for {symbol} (available: {[e.strftime('%Y-%m-%d') if hasattr(e, 'strftime') else str(e) for e in expiries]})")
            
            # STEP 4: Filter instruments by current expiry
            expiry_instruments = [
                inst for inst in symbol_options
                if inst.get('expiry') == current_expiry
            ]
            
            logger.info(f"Found {len(expiry_instruments)} instruments for {symbol} with current expiry")
            
            # STEP 5: Build strikes dictionary with CE/PE pairs
            strikes_dict: Dict[float, Dict[str, Any]] = {}
            
            for inst in expiry_instruments:
                strike_price = inst.get('strike')
                instrument_type = inst.get('instrument_type', '')
                
                if strike_price and instrument_type:
                    if strike_price not in strikes_dict:
                        strikes_dict[strike_price] = {
                            'strike': strike_price,
                            'ce_symbol': None,
                            'pe_symbol': None,
                            'ce_token': None,
                            'pe_token': None
                        }
                    
                    if instrument_type == 'CE':
                        strikes_dict[strike_price]['ce_symbol'] = inst.get('tradingsymbol')
                        strikes_dict[strike_price]['ce_token'] = inst.get('instrument_token')
                    elif instrument_type == 'PE':
                        strikes_dict[strike_price]['pe_symbol'] = inst.get('tradingsymbol')
                        strikes_dict[strike_price]['pe_token'] = inst.get('instrument_token')
            
            # STEP 6: Filter to complete CE/PE pairs and sort
            available_strikes = sorted(
                [s for s in strikes_dict.values() if s['ce_token'] and s['pe_token']],
                key=lambda x: x['strike']
            )
            
            if available_strikes:
                logger.info(f"✓ Found {len(available_strikes)} complete CE/PE pairs for {symbol}")
                strike_range = f"{int(available_strikes[0]['strike'])} - {int(available_strikes[-1]['strike'])}"
                logger.info(f"Strike range: {strike_range}")
                if len(available_strikes) >= 5:
                    sample = [int(s['strike']) for s in available_strikes[::len(available_strikes)//4]]
                    logger.info(f"Sample strikes: {sample}")
                return available_strikes
            else:
                logger.warning(f"No complete CE/PE pairs found for {symbol}")
                return []
            
        except Exception as e:
            logger.error(f"Error getting available strikes for {symbol}: {str(e)}")
            import traceback
            traceback.print_exc()
            return []


    def get_strike_tokens(
        self, 
        symbol: str, 
        strike_price: float,
        expiry_date: Optional[str] = None
    ) -> Dict[str, Optional[int]]:
        """
        Get CE and PE instrument tokens for a specific strike.
        Uses discovered strikes from get_available_strikes() for accuracy.
        
        Args:
            symbol: Base symbol (NIFTY, BANKNIFTY, FINNIFTY)
            strike_price: Strike price
            expiry_date: Expiry date in format YYYY-MM-DD (not used - uses actual expiry from instruments)
            
        Returns:
            Dictionary with 'ce_token', 'pe_token', 'ce_symbol', 'pe_symbol'
        """
        try:
            logger.info(f"Getting tokens for {symbol} strike {strike_price}")
            
            # Get all available strikes for this symbol
            available_strikes = self.get_available_strikes(symbol, range_size=50)
            logger.info(f"Found {len(available_strikes)} available strikes for {symbol}")
            
            if not available_strikes:
                logger.error(f"No available strikes found for {symbol}")
                return {'ce_token': None, 'pe_token': None, 'ce_symbol': None, 'pe_symbol': None}
            
            # Find the exact strike or closest match
            matching_strike = None
            for strike_obj in available_strikes:
                if strike_obj['strike'] == strike_price:
                    matching_strike = strike_obj
                    break
            
            # If exact match not found, find closest
            if not matching_strike and available_strikes:
                matching_strike = min(available_strikes, key=lambda x: abs(x['strike'] - strike_price))
                logger.warning(f"Exact strike {strike_price} not found for {symbol}, using closest: {matching_strike['strike']}")
            
            if matching_strike:
                logger.info(f"✓ Strike {symbol} {strike_price}: CE={matching_strike['ce_symbol']}, PE={matching_strike['pe_symbol']}")
                return {
                    'ce_token': matching_strike['ce_token'],
                    'pe_token': matching_strike['pe_token'],
                    'ce_symbol': matching_strike['ce_symbol'],
                    'pe_symbol': matching_strike['pe_symbol']
                }
            else:
                logger.error(f"Strike {strike_price} not found for {symbol}")
                return {'ce_token': None, 'pe_token': None, 'ce_symbol': None, 'pe_symbol': None}
            
        except Exception as e:
            logger.error(f"Error getting strike tokens for {symbol} {strike_price}: {str(e)}")
            return {'ce_token': None, 'pe_token': None, 'ce_symbol': None, 'pe_symbol': None}

    def _get_underlying_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get quote data for underlying symbol (fresh from API, not cached)
        
        Uses retry logic for better reliability.
        
        Args:
            symbol: Symbol name (NIFTY, BANKNIFTY, etc.)
            
        Returns:
            Quote data dictionary with last_price from live API
        """
        try:
            # Map to NSE symbols
            symbol_map = {
                'NIFTY': 'NIFTY 50',
                'BANKNIFTY': 'NIFTY BANK',
                'FINNIFTY': 'NIFTY FIN SERVICE'
            }
            
            nse_symbol = symbol_map.get(symbol, symbol)
            logger.info(f"Fetching underlying quote for {symbol} (NSE: {nse_symbol})")
            
            # Fetch from NSE
            try:
                instruments = self.kite.instruments(exchange='NSE')
                logger.debug(f"Loaded {len(instruments)} NSE instruments")
            except Exception as inst_error:
                logger.error(f"Error fetching NSE instruments: {str(inst_error)}")
                return {'last_price': 0, 'bid': 0, 'ask': 0}
            
            # Find the token for this symbol
            token = None
            for instrument in instruments:
                if instrument['tradingsymbol'] == nse_symbol:
                    token = instrument['instrument_token']
                    logger.info(f"Found token {token} for {nse_symbol}")
                    break
            
            if not token:
                logger.error(f"Could not find NSE symbol '{nse_symbol}' in {len(instruments)} instruments")
                # Log sample of available NSE symbols for debugging
                nse_samples = [inst.get('tradingsymbol') for inst in instruments[:5] if 'NIFTY' in inst.get('tradingsymbol', '')]
                if nse_samples:
                    logger.warning(f"Sample NSE symbols found: {nse_samples}")
                return {'last_price': 0, 'bid': 0, 'ask': 0}
            
            # Fetch fresh quote from API WITH RETRY LOGIC
            logger.info(f"Fetching quote for token {token} ({nse_symbol})")
            quotes = self._quote_with_retry([token], max_retries=3)
            
            logger.debug(f"Quotes response type: {type(quotes)}, content: {quotes}")
            
            if not quotes:
                logger.warning(f"Empty quotes response for token {token}")
                return {'last_price': 0, 'bid': 0, 'ask': 0}
            
            data = quotes.get(token, {})
            logger.debug(f"Quote data for token {token}: {data}")
            
            if data and data.get('last_price') and float(data.get('last_price', 0)) > 0:
                logger.info(f"✓ Successfully fetched {symbol} price: {data.get('last_price')}")
                logger.debug(f"Full quote data: LTP={data.get('last_price')}, Bid={data.get('bid')}, Ask={data.get('ask')}, Volume={data.get('volume')}")
                return data
            else:
                logger.warning(f"No price data in response for {nse_symbol}. Quote: {data}")
                logger.warning(f"Possible reason: Market may be closed or no trading activity")
                logger.debug(f"Quote keys available: {list(data.keys()) if data else 'None'}")
                return {'last_price': 0, 'bid': 0, 'ask': 0}
            
        except Exception as e:
            logger.error(f"Error fetching underlying quote for {symbol}: {str(e)}", exc_info=True)
            return {'last_price': 0, 'bid': 0, 'ask': 0}

    def _get_current_expiry(self) -> str:
        """
        Get current option expiry date (nearest Thursday)
        
        Logic:
        - Find the next Thursday
        - If today is Thursday and before market close (15:00), use today's expiry
        - If today is Thursday and after market close, use next Thursday's expiry
        
        Returns:
            Expiry date in format YYYY-MM-DD
        """
        today = datetime.now()
        current_hour = today.hour
        
        # Check if today is Thursday (weekday 3)
        if today.weekday() == 3:  # Thursday
            # If before market close (15:00), use today's expiry
            if current_hour < 15:
                logger.info(f"Today is Thursday before market close. Using today's expiry: {today.strftime('%Y-%m-%d')}")
                return today.strftime('%Y-%m-%d')
            else:
                # After market close, get next Thursday
                days_until_thursday = 7
                logger.info(f"Today is Thursday after market close. Using next Thursday's expiry")
        else:
            # Find next Thursday
            days_until_thursday = (3 - today.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
        
        expiry_date = today + timedelta(days=days_until_thursday)
        logger.info(f"Current expiry date: {expiry_date.strftime('%Y-%m-%d')} ({expiry_date.strftime('%d%b').upper()})")
        return expiry_date.strftime('%Y-%m-%d')

    def calculate_pdh_pdl(self, candles: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate PDH (Previous Day High) and PDL (Previous Day Low) from candles
        
        Args:
            candles: List of candlestick data
            
        Returns:
            Dictionary with 'pdh' and 'pdl' values
        """
        if not candles:
            return {'pdh': 0, 'pdl': 0}
        
        high = max(candle['high'] for candle in candles)
        low = min(candle['low'] for candle in candles)
        
        return {
            'pdh': high,
            'pdl': low,
            'high': high,
            'low': low
        }
