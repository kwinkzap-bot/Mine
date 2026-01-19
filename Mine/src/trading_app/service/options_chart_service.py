import logging
from datetime import datetime, timedelta
import time
import random
from trading_app.service.kite_service import KiteService
from typing import Tuple, Dict, Any, List, Optional, Union
import pytz
from concurrent.futures import ThreadPoolExecutor
import threading
import json
import os

class OptionsChartService:
    def __init__(self, kite_instance):
        self.kite_service = KiteService(kite_instance)
        # Cache for historical data - {(ce_token, pe_token, timeframe): (ce_data, pe_data)}
        self._chart_data_cache: Dict[Tuple[int, int, str], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
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
        """Calculate default CE and PE strikes based on symbol-specific logic."""
        
        # NIFTY only configuration
        config = {
            'round_to': 50,
            'offset_50': 150,   # When rounded_base % 100 == 50
            'offset_100': 200   # When rounded_base % 100 == 0
        }
        
        # 1. Round to symbol-specific value
        round_to = config['round_to']
        rounded_base = round(base_price / round_to) * round_to
        
        # 2. Determine offset based on symbol-specific logic
        if rounded_base % 100 == 50:
            diff = config['offset_50']
            offset_type = "offset_50"
        else:
            diff = config['offset_100']
            offset_type = "offset_100"
        
        # 3. Calculate target strikes
        ce_strike_price = rounded_base - diff
        pe_strike_price = rounded_base + diff
        
        logging.info(f"[STRIKE_CALC] Symbol={symbol}, BasePrice={base_price:.2f}, RoundTo={round_to}, RoundedBase={rounded_base}, OffsetType={offset_type}, Diff={diff}, CE={ce_strike_price}, PE={pe_strike_price}")
        return float(ce_strike_price), float(pe_strike_price)

    def _load_or_fetch_nfo_instruments(self) -> List[Dict[str, Any]]:
        """Load NFO instruments from cache or fetch from Kite API."""
        import time as time_module
        
        # Try disk cache first (24h validity)
        instruments = self._load_nfo_from_disk_cache()
        if instruments:
            return instruments
        
        # Try memory cache (1h validity)
        now = time_module.time()
        with self._instruments_lock:
            if self._instruments_cache.get('NFO') and now < self._instruments_expiry:
                instruments = self._instruments_cache['NFO']
                logging.info(f"✓ Using memory-cached NFO instruments ({len(instruments)} records)")
                return instruments
        
        # Fetch from Kite API
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
        
        return instruments

    def _get_symbol_expiry_instruments(self, instruments: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
        """Get all instruments for a symbol with current/next expiry.
        
        Automatically selects the nearest future expiry date to handle expired options.
        This ensures we always get the active trading expiry even after expiry days.
        """
        from datetime import datetime
        
        symbol_upper = symbol.upper()
        
        # Filter to symbol and option types
        symbol_instruments = [
            inst for inst in instruments
            if inst['name'].upper() == symbol_upper and inst['instrument_type'] in ['CE', 'PE']
        ]
        
        if not symbol_instruments:
            return []
        
        # Get all unique expiries and sort them
        expiries = sorted(list(set(inst['expiry'] for inst in symbol_instruments)))
        
        if not expiries:
            return []
        
        # Get the nearest future expiry (current or next)
        # Convert expiry strings to datetime for comparison
        today = datetime.now().date()
        valid_expiries = []
        
        for expiry_str in expiries:
            try:
                # Expiry format is typically YYYY-MM-DD
                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                # Only consider expiries that are today or in the future
                if expiry_date >= today:
                    valid_expiries.append((expiry_date, expiry_str))
            except ValueError:
                # If parsing fails, still include it as fallback
                valid_expiries.append((None, expiry_str))
        
        # If we have future expiries, use the nearest one
        if valid_expiries:
            # Sort by date (None values go to end)
            valid_expiries.sort(key=lambda x: x[0] if x[0] else datetime.max.date())
            current_expiry = valid_expiries[0][1]
        else:
            # Fallback to first (earliest) expiry if all are in the past
            current_expiry = expiries[0]
        
        logging.info(f"Selected expiry {current_expiry} for {symbol_upper} (available: {expiries})")
        
        # Filter by current expiry
        return [
            inst for inst in symbol_instruments
            if inst['expiry'] == current_expiry
        ]

    def _build_strikes_from_instruments(self, instruments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build strikes list with CE and PE tokens from instruments."""
        strikes_dict: Dict[float, Dict[str, Any]] = {}
        
        for inst in instruments:
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
        
        return strikes

    def _fetch_base_price(self, symbol: str, price_source: str, target_date: Optional[str] = None) -> Optional[float]:
        """Fetch base price based on price_source parameter. Supports optional target_date for historical data."""
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing") as executor:
            if price_source == 'ltp':
                return self._fetch_ltp_with_fallback(executor, symbol, target_date)
            else:  # previous_close
                return self._fetch_previous_close(executor, symbol, target_date)

    def _fetch_ltp_with_fallback(self, executor, symbol: str, target_date: Optional[str] = None) -> Optional[float]:
        """Fetch LTP with fallback to previous close. For historical dates, only uses previous_close."""
        if target_date:
            # For historical dates, use previous_close instead of LTP
            return self._fetch_previous_close(executor, symbol, target_date)
        
        ltp_future = executor.submit(self.kite_service.get_current_ltp, symbol)
        try:
            base_price = ltp_future.result(timeout=3)
            logging.info(f"[get_strikes] {symbol}: Using LTP price: {base_price}")
            return base_price
        except Exception as e:
            logging.warning(f"[get_strikes] {symbol}: Error fetching LTP: {e}, falling back to previous close")
            return self._fetch_previous_close(executor, symbol, target_date)

    def _fetch_previous_close(self, executor, symbol: str, target_date: Optional[str] = None) -> Optional[float]:
        """Fetch previous trading day close, or historical close for a specific target_date."""
        pdc_future = executor.submit(self.kite_service.get_previous_trading_day_close, symbol, target_date)
        try:
            base_price = pdc_future.result(timeout=5)
            date_label = f" for {target_date}" if target_date else ""
            logging.info(f"[get_strikes] {symbol}: Using previous close price{date_label}: {base_price}")
            return base_price
        except Exception as e:
            logging.warning(f"[get_strikes] {symbol}: Timeout fetching price: {e}, using mid-strike")
            return None

    def _calculate_and_assign_defaults(self, base_price: Optional[float], symbol: str, 
                                       strikes: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], 
                                                                                Optional[int], Optional[int]]:
        """Calculate default CE/PE strikes and find their tokens."""
        default_ce_strike = None
        default_pe_strike = None
        default_ce_token = None
        default_pe_token = None
        
        if not base_price:
            if strikes:
                base_price = strikes[len(strikes) // 2]['strike']
            else:
                return None, None, None, None
        
        # Calculate default strikes
        default_ce_strike, default_pe_strike = self._calculate_default_strikes(base_price, symbol)
        
        # Try exact match first
        for s in strikes:
            if default_ce_strike and s['strike'] == default_ce_strike:
                default_ce_token = s['ce_token']
            if default_pe_strike and s['strike'] == default_pe_strike:
                default_pe_token = s['pe_token']
        
        # If exact matches not found, use closest available strikes
        if not default_ce_token and default_ce_strike and strikes:
            closest_ce = min(strikes, key=lambda x: abs(x['strike'] - default_ce_strike))
            default_ce_token = closest_ce['ce_token']
            default_ce_strike = closest_ce['strike']
            logging.warning(f"CE strike {default_ce_strike} not found, using closest: {closest_ce['strike']}")
        
        if not default_pe_token and default_pe_strike and strikes:
            closest_pe = min(strikes, key=lambda x: abs(x['strike'] - default_pe_strike))
            default_pe_token = closest_pe['pe_token']
            default_pe_strike = closest_pe['strike']
            logging.warning(f"PE strike {default_pe_strike} not found, using closest: {closest_pe['strike']}")
        
        if default_ce_strike and default_pe_strike:
            logging.info(f"Default strikes selected: CE={default_ce_strike}, PE={default_pe_strike}")
        
        return default_ce_strike, default_pe_strike, default_ce_token, default_pe_token

    def get_strikes_for_symbol(self, symbol: str, price_source: str = 'previous_close', skip_pricing: bool = False, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get available strikes for a symbol with default CE/PE selection.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY')
            price_source: 'previous_close' or 'ltp' for calculating default strikes
            skip_pricing: If True, returns immediately with calculated defaults
            target_date: Optional date in YYYY-MM-DD format for historical data
        
        Returns:
            Dict with strikes, default CE/PE strikes and tokens, and base_price
        """
        import time as time_module
        start_time = time_module.time()
        
        try:
            # STEP 1: Load NFO instruments
            instruments = self._load_or_fetch_nfo_instruments()
            
            # STEP 2: Get instruments for this symbol and current expiry
            expiry_instruments = self._get_symbol_expiry_instruments(instruments, symbol)
            
            if not expiry_instruments:
                logging.warning(f"No instruments found for {symbol}")
                return {
                    'strikes': [],
                    'default_ce_strike': None,
                    'default_pe_strike': None,
                    'default_ce_token': None,
                    'default_pe_token': None,
                    'base_price': None
                }
            
            # STEP 3: Build strikes dictionary
            strikes = self._build_strikes_from_instruments(expiry_instruments)
            
            if not strikes:
                logging.warning(f"No complete strikes (CE+PE pairs) found for {symbol}")
                return {
                    'strikes': strikes,
                    'default_ce_strike': None,
                    'default_pe_strike': None,
                    'default_ce_token': None,
                    'default_pe_token': None,
                    'base_price': None
                }
            
            # STEP 4: Fetch base price
            base_price = self._fetch_base_price(symbol, price_source, target_date)
            
            # STEP 5: Calculate and assign default strikes
            default_ce_strike, default_pe_strike, default_ce_token, default_pe_token = \
                self._calculate_and_assign_defaults(base_price, symbol, strikes)
            
            elapsed = time_module.time() - start_time
            logging.info(f"✓ get_strikes_for_symbol({symbol}, target_date={target_date}) completed in {elapsed:.2f}s")
            
            return {
                'strikes': strikes,
                'default_ce_strike': default_ce_strike,
                'default_pe_strike': default_pe_strike,
                'default_ce_token': default_ce_token,
                'default_pe_token': default_pe_token,
                'base_price': base_price
            }
        
        except Exception as e:
            logging.error(f"Error in get_strikes_for_symbol: {e}", exc_info=True)
            raise
    
    def get_tokens_for_strikes(self, symbol: str, ce_strike: float, pe_strike: float) -> Tuple[Optional[int], Optional[int]]:
        """Get CE and PE instrument tokens for given strike prices.
        
        Automatically selects the current/next expiry to handle expired options.
        """
        try:
            from datetime import datetime
            
            instruments = self.kite_service.kite.instruments("NFO")
            
            symbol_instruments = [
                inst for inst in instruments
                if inst['name'].upper() == symbol.upper() and inst['instrument_type'] in ['CE', 'PE']
            ]
            
            expiries = sorted(list(set(inst['expiry'] for inst in symbol_instruments)))
            if not expiries:
                return None, None
            
            # Get the nearest future expiry (current or next)
            today = datetime.now().date()
            valid_expiries = []
            
            for expiry_str in expiries:
                try:
                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                    if expiry_date >= today:
                        valid_expiries.append((expiry_date, expiry_str))
                except ValueError:
                    valid_expiries.append((None, expiry_str))
            
            if valid_expiries:
                valid_expiries.sort(key=lambda x: x[0] if x[0] else datetime.max.date())
                current_expiry = valid_expiries[0][1]
            else:
                current_expiry = expiries[0]
            
            logging.info(f"get_tokens_for_strikes: Selected expiry {current_expiry} for {symbol} (available: {expiries})")
            
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

    def _fetch_prev_day_ohlc(self, token: int, target_date: Optional[str] = None) -> Dict[str, Optional[float]]:
        """Fetch previous day's OHLC using daily historical data to avoid intraday highs.
        
        Args:
            token: Instrument token
            target_date: Optional date in YYYY-MM-DD format. If provided, returns the previous day's OHLC before this date.
        """
        try:
            ist = pytz.timezone('Asia/Kolkata')
            
            if target_date:
                # When target_date is provided, get the previous day's OHLC before that date
                target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
                # Fetch from 10 days before to 1 day before the target (exclude target date itself)
                from_dt = datetime.combine(target_dt - timedelta(days=10), datetime.min.time()).replace(tzinfo=None)
                to_dt = datetime.combine(target_dt - timedelta(days=1), datetime.max.time()).replace(tzinfo=None)
            else:
                # Original logic: get previous day's OHLC before today
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

            # Find the last complete day before the target/today
            prev_bar = None
            if target_date:
                # For target_date, get the most recent bar (which is before target_date by design)
                if data:
                    prev_bar = data[-1]  # Last bar is the most recent before target_date
            else:
                # Original logic: find last day before today
                today_ist = datetime.now(ist).date()
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

    def _fetch_pdh_pdl_from_tokens(self, ce_token: int, pe_token: int, target_date: Optional[str] = None) -> Dict[str, Optional[float]]:
        """Fetch previous day high/low using daily historical bars (avoids live-day highs/lows).
        
        Args:
            ce_token: CE instrument token
            pe_token: PE instrument token
            target_date: Optional date in YYYY-MM-DD format. If provided, returns PDH/PDL from the day before this date.
        """
        ce = self._fetch_prev_day_ohlc(ce_token, target_date)
        pe = self._fetch_prev_day_ohlc(pe_token, target_date)

        return {
            'ce_pdh': ce.get('high'),
            'ce_pdl': ce.get('low'),
            'pe_pdh': pe.get('high'),
            'pe_pdl': pe.get('low')
        }

    def get_pdh_pdl(self, ce_token: int, pe_token: int, target_date: Optional[str] = None) -> Dict[str, Optional[float]]:
        """Public method to fetch PDH/PDL using instrument tokens.
        
        Args:
            ce_token: CE instrument token
            pe_token: PE instrument token
            target_date: Optional date in YYYY-MM-DD format. If provided, returns PDH/PDL from the day before this date.
        """
        return self._fetch_pdh_pdl_from_tokens(ce_token, pe_token, target_date)

    def _is_market_hours(self, date_val) -> bool:
        """Check if a candle timestamp is within market hours (9:15 AM - 3:40 PM IST)."""
        try:
            # Convert to IST if needed
            if date_val.tzinfo is None:
                ist_time = self._ist.localize(date_val)
            else:
                ist_time = date_val.astimezone(self._ist)
            
            # Market hours: 9:15 AM (555 min) to 3:40 PM (940 min)
            hours = ist_time.hour
            minutes = ist_time.minute
            total_minutes = hours * 60 + minutes
            
            # 9:15 AM = 555 minutes, 3:40 PM = 940 minutes
            is_after_open = total_minutes >= 555  # 9:15 AM
            is_before_close = total_minutes <= 940  # 3:40 PM
            
            return is_after_open and is_before_close
        except Exception as e:
            logging.warning(f"Error checking market hours for {date_val}: {e}")
            return True  # Default to including candle if time check fails

    def _convert_candle_to_dict(self, candle: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a single candle to formatted dict with IST-adjusted timestamp for Lightweight Charts.
        
        Kite API returns naive datetimes in IST timezone.
        Lightweight Charts treats timestamps as UTC, so we add the IST offset (5.5 hours) 
        to the Unix timestamp so the displayed time matches the actual IST time.
        """
        try:
            date_val = candle.get('date')
            if not date_val:
                raise KeyError("No date field in candle")
            
            # Kite returns naive datetime in IST - localize to IST to get proper handling
            if date_val.tzinfo is None:
                ist_time = self._ist.localize(date_val)
            else:
                ist_time = date_val.astimezone(self._ist)
            
            # Convert to Unix timestamp (UTC-based representation of IST time)
            utc_timestamp = int(ist_time.timestamp())
            
            # Add IST offset (5.5 hours = 19800 seconds) so Lightweight Charts displays IST time
            # When LC displays this adjusted timestamp as UTC, it will show the correct IST time
            ist_offset_seconds = int(5.5 * 3600)  # 19800 seconds
            adjusted_timestamp = utc_timestamp + ist_offset_seconds
            
            return {
                'date': adjusted_timestamp,
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

    def get_chart_data(self, ce_token: int, pe_token: int, timeframe: str, use_cache: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Get historical data for CE and PE strikes using parallel API calls.
        
        Args:
            ce_token: Call option instrument token
            pe_token: Put option instrument token
            timeframe: Timeframe for chart data ('1minute', '5minute', 'day', 'week', 'month', etc.)
            use_cache: Whether to use in-memory cache
        
        Returns: (ce_data, pe_data) - formatted candlestick data for CE and PE
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
                from_date = to_date - timedelta(days=30)  # Fetch 30 days of minute data
            elif timeframe in ['5minute', '5minute']:
                from_date = to_date - timedelta(days=60)  # Fetch 60 days of 5-minute data (~17280 candles)
            elif timeframe in ['day', '1day']:
                from_date = to_date - timedelta(days=365)  # 1 year of daily candles
            elif timeframe in ['week', '1week']:
                from_date = to_date - timedelta(days=730)  # ~2 years of weekly candles
            elif timeframe in ['month', '1month']:
                from_date = to_date - timedelta(days=2190)  # ~6 years of monthly candles
            else:
                from_date = to_date - timedelta(days=90)  # Default fallback to 90 days
            
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
            
            # Filter candles to market hours (9:15 AM - 3:40 PM IST) and format efficiently
            ce_market_hours = [c for c in ce_data if self._is_market_hours(c.get('date'))]
            pe_market_hours = [c for c in pe_data if self._is_market_hours(c.get('date'))]
            
            logging.info(f"✓ Filtered to market hours: CE={len(ce_market_hours)} candles, PE={len(pe_market_hours)} candles")
            
            # Format candles efficiently using list comprehension with helper
            ce_formatted = [self._convert_candle_to_dict(c) for c in ce_market_hours] if ce_market_hours else []
            pe_formatted = [self._convert_candle_to_dict(c) for c in pe_market_hours] if pe_market_hours else []
            
            result = (ce_formatted, pe_formatted)
            
            # Cache the result if allowed
            if use_cache:
                with self._cache_lock:
                    self._chart_data_cache[cache_key] = result
            
            return result
        
        except Exception as e:
            logging.error(f"Error getting chart data for tokens {ce_token}, {pe_token}: {e}", exc_info=True)
            raise e