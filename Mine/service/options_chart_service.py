import logging
from datetime import datetime, timedelta
import time
import random
from service.kite_service import KiteService
from typing import Tuple, Dict, Any, List, Optional, Union
import pytz
from concurrent.futures import ThreadPoolExecutor
import threading
import json
import os

class OptionsChartService:
    def __init__(self, kite_instance):
        self.kite_service = KiteService(kite_instance)
        # Cache for historical data - {(ce_token, pe_token, timeframe): (ce_data, pe_data, pdh_pdl)}
        self._chart_data_cache: Dict[Tuple[int, int, str], Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Optional[float]]]] = {}
        self._cache_lock = threading.Lock()
        # Cache for instruments - {expiry: instruments}
        self._instruments_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._instruments_lock = threading.Lock()
        self._instruments_expiry = 0  # Timestamp when instruments cache expires (1 hour)
        # Simple request spacing to avoid hitting Kite rate limits across threads
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0
        # Disk cache path
        self._nfo_cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', 'nfo_instruments.json')
        os.makedirs(os.path.dirname(self._nfo_cache_file), exist_ok=True)
        # Pre-cache timezone for repeated use
        self._ist = pytz.timezone('Asia/Kolkata')

    def _respect_rate_limit(self, min_gap_seconds: float = 0.25):
        """Ensure a minimum gap between outbound Kite API requests.
        This is a coarse client-side throttle to reduce 429s.
        """
        with self._rate_lock:
            now = time.time()
            elapsed = now - self._last_request_ts
            if elapsed < min_gap_seconds:
                time.sleep(min_gap_seconds - elapsed)
            self._last_request_ts = time.time()
    
    def _load_nfo_from_disk_cache(self) -> Optional[List[Dict[str, Any]]]:
        """Load NFO instruments from disk cache if available and recent."""
        try:
            if os.path.exists(self._nfo_cache_file):
                stat = os.stat(self._nfo_cache_file)
                age = time.time() - stat.st_mtime
                # Cache is valid if less than 24 hours old
                if age < 86400:
                    with open(self._nfo_cache_file, 'r') as f:
                        data = json.load(f)
                    logging.info(f"✓ Loaded NFO instruments from disk cache ({age/3600:.1f}h old, {len(data)} records)")
                    return data
        except Exception as e:
            logging.warning(f"Error loading disk cache: {e}")
        return None
    
    def _save_nfo_to_disk_cache(self, instruments: List[Dict[str, Any]]) -> None:
        """Save NFO instruments to disk cache."""
        try:
            with open(self._nfo_cache_file, 'w') as f:
                json.dump(instruments, f)
            logging.info(f"✓ Saved {len(instruments)} NFO instruments to disk cache")
        except Exception as e:
            logging.warning(f"Error saving to disk cache: {e}")

    def _historical_with_retry(self, instrument_token: int, from_date: datetime, to_date: datetime, interval: str, max_retries: int = 5):
        """Call kite.historical_data with exponential backoff, jitter, and basic 429 handling."""
        from kiteconnect.exceptions import NetworkException
        attempt = 0
        while True:
            try:
                self._respect_rate_limit()
                return self.kite_service.kite.historical_data(
                    instrument_token=int(instrument_token),
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval
                )
            except NetworkException as e:
                msg = str(e) if e else ""
                if attempt >= max_retries:
                    logging.error(f"historical_data failed after {attempt} retries for token {instrument_token}: {e}")
                    raise
                # Backoff with jitter; treat 'Too many requests' specially but backoff either way
                base = 0.5 * (2 ** attempt)
                sleep_s = min(8.0, base + random.uniform(0, 0.4))
                logging.warning(f"NetworkException on historical_data (attempt {attempt+1}/{max_retries}) for token {instrument_token}: {msg}. Backing off {sleep_s:.2f}s")
                time.sleep(sleep_s)
                attempt += 1

    def _quote_with_retry(self, tokens, max_retries: int = 5):
        """Call kite.quote with backoff and jitter."""
        from kiteconnect.exceptions import NetworkException
        attempt = 0
        while True:
            try:
                self._respect_rate_limit()
                return self.kite_service.kite.quote(tokens)
            except NetworkException as e:
                if attempt >= max_retries:
                    raise
                base = 0.5 * (2 ** attempt)
                sleep_s = min(8.0, base + random.uniform(0, 0.4))
                logging.warning(f"NetworkException on quote (attempt {attempt+1}/{max_retries}) for tokens {tokens}. Backing off {sleep_s:.2f}s: {e}")
                time.sleep(sleep_s)
                attempt += 1
            except Exception as e:
                msg = str(e)
                is_rate = 'Too many requests' in msg or '429' in msg
                if not is_rate and attempt >= max_retries:
                    raise
                if attempt >= max_retries:
                    logging.error(f"quote failed after {attempt} retries for tokens {tokens}: {e}")
                    raise
                base = 0.5 * (2 ** attempt)
                sleep_s = min(8.0, base + random.uniform(0, 0.4))
                logging.warning(f"Exception on quote (attempt {attempt+1}/{max_retries}) for tokens {tokens}: {msg}. Backing off {sleep_s:.2f}s")
                time.sleep(sleep_s)
                attempt += 1
    
    def _calculate_default_strikes(self, base_price: Union[float, int], symbol: str) -> Tuple[float, float]:
        """Calculate default CE and PE strikes based on new logic."""
        
        # 1. Round PDC to the nearest 50
        rounded_base = round(base_price / 50) * 50
        
        # 2. Determine offset
        if rounded_base % 100 == 50:
            # e.g., 26850, 26950
            diff = 150
        else:
            # e.g., 26800, 26900
            diff = 200
        
        # 3. Calculate target strikes
        ce_strike_price = rounded_base - diff
        pe_strike_price = rounded_base + diff
        
        return float(ce_strike_price), float(pe_strike_price) # Explicit cast to float

    def get_strikes_for_symbol(self, symbol: str, price_source: str = 'previous_close', skip_pricing: bool = False) -> Dict[str, Any]:
        """
        Fast method to get strikes. Skip pricing data if not needed (skip_pricing=True).
        Returns strikes immediately without waiting for price data.
        """
        import time as time_module
        start_time = time_module.time()
        
        try:
            # STEP 1: Load NFO instruments (try disk cache first, then Kite API)
            instruments = None
            
            # Try disk cache first (24h validity)
            instruments = self._load_nfo_from_disk_cache()
            
            if not instruments:
                # Try memory cache (1h validity)
                now = time_module.time()
                with self._instruments_lock:
                    if self._instruments_cache.get('NFO') and now < self._instruments_expiry:
                        instruments = self._instruments_cache['NFO']
                        logging.info(f"✓ Using memory-cached NFO instruments ({len(instruments)} records)")
                
                # If still no cache, fetch from Kite
                if not instruments:
                    logging.info("Fetching NFO instruments from Kite API (5-10s)...")
                    fetch_start = time_module.time()
                    instruments = self.kite_service.kite.instruments("NFO")
                    fetch_time = time_module.time() - fetch_start
                    logging.info(f"✓ Fetched NFO from API in {fetch_time:.1f}s ({len(instruments)} records)")
                    
                    # Save to both caches
                    with self._instruments_lock:
                        self._instruments_cache['NFO'] = instruments
                        self._instruments_expiry = now + 3600
                    self._save_nfo_to_disk_cache(instruments)
            
            # STEP 2: Filter to symbol + expiry (FAST - no API call)
            symbol_upper = symbol.upper()
            symbol_instruments = [
                inst for inst in instruments
                if inst['name'].upper() == symbol_upper and inst['instrument_type'] in ['CE', 'PE']
            ]
            
            if not symbol_instruments:
                return {'strikes': [], 'default_ce_token': None, 'default_pe_token': None}
            
            # Get current expiry
            expiries = sorted(list(set(inst['expiry'] for inst in symbol_instruments)))
            current_expiry = expiries[0] if expiries else None
            
            if not current_expiry:
                return {'strikes': [], 'default_ce_token': None, 'default_pe_token': None}
            
            # Filter by current expiry
            current_expiry_instruments = [
                inst for inst in symbol_instruments
                if inst['expiry'] == current_expiry
            ]
            
            # Build strikes dict
            strikes_dict: Dict[float, Dict[str, Any]] = {}
            for inst in current_expiry_instruments:
                strike = float(inst['strike'])
                if strike not in strikes_dict:
                    strikes_dict[strike] = {'strike': strike, 'ce_token': None, 'pe_token': None}
                
                if inst['instrument_type'] == 'CE':
                    strikes_dict[strike]['ce_token'] = inst['instrument_token']
                elif inst['instrument_type'] == 'PE':
                    strikes_dict[strike]['pe_token'] = inst['instrument_token']
            
            # Only include strikes that have both CE and PE
            strikes = sorted(
                [s for s in strikes_dict.values() if s['ce_token'] and s['pe_token']],
                key=lambda x: x['strike']
            )
            
            # STEP 3: Quick return if pricing not needed
            if skip_pricing or not strikes:
                logging.info(f"✓ get_strikes_for_symbol({symbol}) completed in {time_module.time() - start_time:.2f}s (no pricing)")
                return {
                    'strikes': strikes,
                    'default_ce_token': strikes[len(strikes)//2]['ce_token'] if strikes else None,
                    'default_pe_token': strikes[len(strikes)//2]['pe_token'] if strikes else None
                }
            
            # STEP 4: Fetch pricing data in parallel (with tight timeouts)
            default_ce_strike = None
            default_pe_strike = None
            base_price = None
            
            try:
                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing") as executor:
                    # Fetch ONLY previous close (faster than both LTP and quote)
                    pdc_future = executor.submit(self.kite_service.get_previous_close, symbol)
                    
                    try:
                        base_price = pdc_future.result(timeout=3)  # 3s timeout
                    except Exception:
                        logging.warning(f"Timeout fetching price for {symbol}, using mid-strike")
                        pass
            except Exception as e:
                logging.warning(f"Error fetching pricing: {e}")
            
            # Use midpoint strike as fallback
            if not base_price and strikes:
                base_price = strikes[len(strikes) // 2]['strike']
            
            if base_price:
                default_ce_strike, default_pe_strike = self._calculate_default_strikes(base_price, symbol)
            
            # Find tokens for default strikes
            default_ce_token = None
            default_pe_token = None
            for s in strikes:
                if default_ce_strike and s['strike'] == default_ce_strike:
                    default_ce_token = s['ce_token']
                if default_pe_strike and s['strike'] == default_pe_strike:
                    default_pe_token = s['pe_token']
            
            # Mark ATM strike
            if base_price:
                atm_strike = min(strikes, key=lambda x: abs(x['strike'] - base_price))
                for s in strikes:
                    s['is_atm'] = (s['strike'] == atm_strike['strike'])
            
            elapsed = time_module.time() - start_time
            logging.info(f"✓ get_strikes_for_symbol({symbol}) completed in {elapsed:.2f}s")
            
            return {
                'strikes': strikes,
                'default_ce_token': default_ce_token,
                'default_pe_token': default_pe_token,
                'base_price': base_price
            }
        
        except Exception as e:
            logging.error(f"Error in get_strikes_for_symbol: {e}", exc_info=True)
            raise
    
    def get_tokens_for_strikes(self, symbol: str, ce_strike: float, pe_strike: float) -> Tuple[Optional[int], Optional[int]]:
        """Get CE and PE instrument tokens for given strike prices."""
        try:
            instruments = self.kite_service.kite.instruments("NFO")
            
            symbol_instruments = [
                inst for inst in instruments
                if inst['name'].upper() == symbol.upper() and inst['instrument_type'] in ['CE', 'PE']
            ]
            
            expiries = sorted(list(set(inst['expiry'] for inst in symbol_instruments)))
            if not expiries:
                return None, None
            
            current_expiry = expiries[0]
            
            ce_token = None
            pe_token = None

            for inst in symbol_instruments:
                if inst['expiry'] == current_expiry:
                    if inst['instrument_type'] == 'CE' and inst['strike'] == ce_strike:
                        ce_token = inst['instrument_token']
                    if inst['instrument_type'] == 'PE' and inst['strike'] == pe_strike:
                        pe_token = inst['instrument_token']
                if ce_token and pe_token:
                    break
            
            return ce_token, pe_token
        except Exception as e:
            logging.error(f"Error getting tokens for strikes: {e}", exc_info=True)
            return None, None

    def _fetch_prev_day_ohlc(self, token: int) -> Dict[str, Optional[float]]:
        """Fetch previous day's OHLC using daily historical data to avoid intraday highs."""
        try:
            ist = pytz.timezone('Asia/Kolkata')
            today_ist = datetime.now(ist).date()
            # Pull last 5 days to be safe and pick latest bar strictly before today
            from_dt = datetime.combine(today_ist - timedelta(days=5), datetime.min.time()).replace(tzinfo=None)
            to_dt = datetime.combine(today_ist, datetime.max.time()).replace(tzinfo=None)

            data = self._historical_with_retry(
                instrument_token=int(token),
                from_date=from_dt,
                to_date=to_dt,
                interval='day'
            )

            if not data:
                return {'high': None, 'low': None, 'open': None, 'close': None}

            # Find the last complete day before today
            prev_bar = None
            for bar in reversed(data):
                bar_date = bar.get('date')
                if bar_date is None:
                    continue
                # bar_date may be tz-aware; convert to date in IST if needed
                if bar_date.tzinfo:
                    bar_local = bar_date.astimezone(ist)
                else:
                    bar_local = ist.localize(bar_date)
                if bar_local.date() < today_ist:
                    prev_bar = bar
                    break

            if not prev_bar:
                return {'high': None, 'low': None, 'open': None, 'close': None}

            return {
                'high': float(prev_bar.get('high')) if prev_bar.get('high') is not None else None,
                'low': float(prev_bar.get('low')) if prev_bar.get('low') is not None else None,
                'open': float(prev_bar.get('open')) if prev_bar.get('open') is not None else None,
                'close': float(prev_bar.get('close')) if prev_bar.get('close') is not None else None
            }
        except Exception as e:
            logging.error(f"Error fetching previous day OHLC for token {token}: {e}", exc_info=True)
            return {'high': None, 'low': None, 'open': None, 'close': None}

    def _fetch_pdh_pdl_from_tokens(self, ce_token: int, pe_token: int) -> Dict[str, Optional[float]]:
        """Fetch previous day high/low using daily historical bars (avoids live-day highs/lows)."""
        ce = self._fetch_prev_day_ohlc(ce_token)
        pe = self._fetch_prev_day_ohlc(pe_token)

        return {
            'ce_pdh': ce.get('high'),
            'ce_pdl': ce.get('low'),
            'pe_pdh': pe.get('high'),
            'pe_pdl': pe.get('low')
        }

    def get_pdh_pdl(self, ce_token: int, pe_token: int) -> Dict[str, Optional[float]]:
        """Public method to fetch PDH/PDL using instrument tokens."""
        return self._fetch_pdh_pdl_from_tokens(ce_token, pe_token)

    def _convert_candle_to_dict(self, candle: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a single candle to formatted dict with Unix timestamp (IST)."""
        try:
            date_val = candle.get('date')
            if not date_val:
                raise KeyError("No date field in candle")
            
            # Handle timezone-aware dates
            if date_val.tzinfo is None:
                timestamp = int(self._ist.localize(date_val).timestamp())
            else:
                timestamp = int(date_val.timestamp())
            
            return {
                'date': timestamp,
                'open': float(candle.get('open', 0)),
                'high': float(candle.get('high', 0)),
                'low': float(candle.get('low', 0)),
                'close': float(candle.get('close', 0)),
                'volume': int(candle.get('volume', 0))
            }
        except Exception as e:
            logging.error(f"Error converting candle: {candle}, Error: {e}")
            raise

    def _extract_ohlc_from_quote(self, quote: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Extract OHLC values from a kite.quote() response."""
        default_ohlc: Dict[str, Optional[float]] = {'high': None, 'low': None, 'open': None, 'close': None}
        
        if not quote or 'ohlc' not in quote:
            return default_ohlc
        
        ohlc = quote.get('ohlc', {})
        if not ohlc:
            return default_ohlc
        
        return {
            'high': float(ohlc.get('high')) if ohlc.get('high') is not None else None,
            'low': float(ohlc.get('low')) if ohlc.get('low') is not None else None,
            'open': float(ohlc.get('open')) if ohlc.get('open') is not None else None,
            'close': float(ohlc.get('close')) if ohlc.get('close') is not None else None
        }

    def _fetch_quote_safe(self, tokens: list) -> Dict[str, Optional[float]]:
        """Safely fetch quotes and extract PDH/PDL for both CE and PE tokens."""
        pdh_pdl_dict: Dict[str, Optional[float]] = {
            'ce_pdh': None, 'ce_pdl': None,
            'pe_pdh': None, 'pe_pdl': None
        }
        
        try:
            ce_token_int, pe_token_int = int(tokens[0]), int(tokens[1])
            self._respect_rate_limit(min_gap_seconds=0.35)
            quotes = self._quote_with_retry([ce_token_int, pe_token_int])
            
            if not quotes or not isinstance(quotes, dict):
                return pdh_pdl_dict
            
            # Try both int and str keys (kite API can return either)
            # Cast to Dict[str, Any] for type safety
            ce_quote: Dict[str, Any] = quotes.get(ce_token_int) or quotes.get(str(ce_token_int)) or {}  # type: ignore
            pe_quote: Dict[str, Any] = quotes.get(pe_token_int) or quotes.get(str(pe_token_int)) or {}  # type: ignore
            
            ce_ohlc = self._extract_ohlc_from_quote(ce_quote)
            pe_ohlc = self._extract_ohlc_from_quote(pe_quote)
            
            pdh_pdl_dict = {
                'ce_pdh': ce_ohlc.get('high'),
                'ce_pdl': ce_ohlc.get('low'),
                'pe_pdh': pe_ohlc.get('high'),
                'pe_pdl': pe_ohlc.get('low')
            }
            
            logging.info(f"✓ Fetched PDH/PDL for tokens {ce_token_int}, {pe_token_int}")
        except Exception as e:
            logging.error(f"Error fetching quotes: {e}", exc_info=True)
        
        return pdh_pdl_dict

    def get_chart_data(self, ce_token: int, pe_token: int, timeframe: str, use_cache: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """Get historical data for CE and PE strikes using parallel API calls.
        
        Args:
            ce_token: Call option instrument token
            pe_token: Put option instrument token
            timeframe: Timeframe for chart data ('1minute', '5minute', 'day', 'week', 'month', etc.)
            use_cache: Whether to use in-memory cache
        
        Returns: (ce_data, pe_data, pdh_pdl_dict) where pdh_pdl_dict contains:
        {
            'ce_pdh': CE previous day high, 'ce_pdl': CE previous day low,
            'pe_pdh': PE previous day high, 'pe_pdl': PE previous day low
        }
        """
        # Normalize timeframe for KiteConnect API
        # Valid intervals: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day, week, month
        kite_timeframe = timeframe.replace('1minute', 'minute').replace('1day', 'day').replace('1week', 'week').replace('1month', 'month')
        cache_key = (ce_token, pe_token, timeframe)
        
        try:
            # Check cache first unless explicitly disabled
            if use_cache:
                with self._cache_lock:
                    if cache_key in self._chart_data_cache:
                        logging.info(f"✓ Cache hit for tokens {ce_token}, {pe_token}")
                        return self._chart_data_cache[cache_key]
            
            # Calculate date range based on timeframe (avoid excessive API calls)
            to_date = datetime.now()
            if timeframe in ['1minute', 'minute']:
                from_date = to_date - timedelta(days=5)  # ~7200 candles max
            elif timeframe in ['5minute', '5minute']:
                from_date = to_date - timedelta(days=7)  # ~2000 candles max
            elif timeframe in ['day', '1day']:
                from_date = to_date - timedelta(days=90)  # ~90 daily candles
            elif timeframe in ['week', '1week']:
                from_date = to_date - timedelta(days=365)  # ~52 weekly candles
            elif timeframe in ['month', '1month']:
                from_date = to_date - timedelta(days=1095)  # ~36 monthly candles
            else:
                from_date = to_date - timedelta(days=14)  # Default fallback
            
            # Fetch CE and PE data in parallel
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart_data") as executor:
                ce_future = executor.submit(
                    self._historical_with_retry,
                    int(ce_token), from_date, to_date, kite_timeframe
                )
                self._respect_rate_limit(min_gap_seconds=0.25)
                pe_future = executor.submit(
                    self._historical_with_retry,
                    int(pe_token), from_date, to_date, kite_timeframe
                )
                
                try:
                    ce_data = ce_future.result(timeout=30) or []
                    pe_data = pe_future.result(timeout=30) or []
                except Exception as e:
                    logging.error(f"Timeout or error fetching futures for tokens {ce_token}, {pe_token}: {e}")
                    raise
            
            # Validate data
            if not ce_data:
                logging.warning(f"No CE data returned for token {ce_token}")
                ce_data = []
            if not pe_data:
                logging.warning(f"No PE data returned for token {pe_token}")
                pe_data = []
            
            logging.info(f"✓ Fetched chart data: CE={len(ce_data)} candles, PE={len(pe_data)} candles")
            
            # Format candles efficiently using list comprehension with helper
            ce_formatted = [self._convert_candle_to_dict(c) for c in ce_data] if ce_data else []
            pe_formatted = [self._convert_candle_to_dict(c) for c in pe_data] if pe_data else []
            
            # Fetch PDH/PDL in parallel while not blocking
            pdh_pdl_dict = self._fetch_quote_safe([ce_token, pe_token])
            
            result = (ce_formatted, pe_formatted, pdh_pdl_dict)
            
            # Cache the result if allowed
            if use_cache:
                with self._cache_lock:
                    self._chart_data_cache[cache_key] = result
            
            return result
        
        except Exception as e:
            logging.error(f"Error getting chart data for tokens {ce_token}, {pe_token}: {e}", exc_info=True)
            raise e