"""
Open Interest Service - Fetches and processes open interest data from Zerodha Kite
"""
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from kiteconnect import KiteConnect
from kiteconnect.exceptions import NetworkException, TokenException

logger = logging.getLogger(__name__)


class OpenInterestService:
    """Service to fetch and process open interest data for options."""
    
    OPENING_OI_CACHE_DIR = '/tmp/opening_oi_cache'
    
    def __init__(self, kite_instance: KiteConnect):
        """
        Initialize OpenInterestService.
        
        Args:
            kite_instance: KiteConnect instance
        """
        self.kite = kite_instance
        
        # Create cache directory if it doesn't exist
        os.makedirs(self.OPENING_OI_CACHE_DIR, exist_ok=True)
        
        # Symbol configuration
        self.SYMBOL_CONFIG = {
            'NIFTY': {
                'name': 'NIFTY',  # Direct name from instruments list
                'instrument_key': 'NSE:NIFTY 50',  # For price quote
                'lot_size': 50,
                'strike_diff': 50
            },
            'BANKNIFTY': {
                'name': 'BANKNIFTY',  # Direct name from instruments list
                'instrument_key': 'NSE:NIFTY BANK',  # For price quote
                'lot_size': 25,
                'strike_diff': 100
            },
            'FINNIFTY': {
                'name': 'FINNIFTY',  # Direct name from instruments list
                'instrument_key': 'NSE:NIFTY FIN SERVICE',  # For price quote
                'lot_size': 40,
                'strike_diff': 50
            }
        }
    
    def _get_opening_oi_cache_file(self, symbol: str) -> str:
        """Get the cache file path for opening OI for a symbol."""
        today = datetime.now().strftime('%Y-%m-%d')
        return os.path.join(self.OPENING_OI_CACHE_DIR, f'{symbol}_opening_oi_{today}.json')
    
    def _load_opening_oi_cache(self, symbol: str) -> Dict[int, Dict[str, int]]:
        """
        Load opening OI cache from file.
        
        Returns:
            Dict with strike as key and {'ce_oi': value, 'pe_oi': value} as value
        """
        cache_file = self._get_opening_oi_cache_file(symbol)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cache = json.load(f)
                    logger.info(f"Loaded opening OI cache for {symbol}: {len(cache)} strikes")
                    return cache
            except Exception as e:
                logger.error(f"Error loading opening OI cache: {e}")
        return {}
    
    def _save_opening_oi_cache(self, symbol: str, cache: Dict[int, Dict[str, int]]):
        """Save opening OI cache to file."""
        cache_file = self._get_opening_oi_cache_file(symbol)
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache, f)
                logger.info(f"Saved opening OI cache for {symbol}: {len(cache)} strikes")
        except Exception as e:
            logger.error(f"Error saving opening OI cache: {e}")
    
    def _is_market_open(self) -> bool:
        """Check if market is open (9:15 AM - 3:30 PM IST, weekdays)."""
        now = datetime.now()
        market_open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        is_weekday = now.weekday() < 5  # 0-4 = Mon-Fri
        is_market_hours = market_open_time <= now <= market_close_time
        
        return is_weekday and is_market_hours
    
    def _should_initialize_cache(self) -> bool:
        """Check if we should initialize cache at market open (within first 5 minutes)."""
        now = datetime.now()
        market_open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_open_plus_5min = market_open_time + timedelta(minutes=5)
        
        is_weekday = now.weekday() < 5
        is_near_open = market_open_time <= now <= market_open_plus_5min
        
        return is_weekday and is_near_open
    
    def get_open_interest_data(self, symbol: str = 'NIFTY') -> Dict[str, Any]:
        """
        Get open interest data for options strikes.
        
        Fetches current option chain data and processes open interest information.
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            
        Returns:
            Dictionary with OI data for CE and PE strikes
        """
        try:
            if symbol not in self.SYMBOL_CONFIG:
                return {
                    'success': False,
                    'error': f'Unknown symbol: {symbol}'
                }
            
            config = self.SYMBOL_CONFIG[symbol]
            
            logger.info(f"Fetching open interest data for {symbol}...")
            
            # Step 1: Get current underlying price
            try:
                quote = self.kite.quote([config['instrument_key']])
                quote_data = quote.get(config['instrument_key'], {})
                current_price = float(quote_data.get('last_price', 0)) if isinstance(quote_data, dict) else 0
                logger.info(f"{symbol} current price: {current_price}")
            except Exception as e:
                logger.error(f"Failed to get current price for {symbol}: {e}")
                return {
                    'success': False,
                    'error': f'Failed to get current price: {str(e)}'
                }
            
            # Step 2: Get all NFO instruments and find available strikes
            try:
                instruments = self.kite.instruments('NFO')
            except Exception as e:
                logger.error(f"Failed to get instruments: {e}")
                return {
                    'success': False,
                    'error': f'Failed to get instruments: {str(e)}'
                }
            
            # Step 3: Get available strikes for symbol using KiteService method pattern
            strikes_data = self._get_available_strikes(instruments, symbol, current_price, config)
            
            if not strikes_data:
                return {
                    'success': False,
                    'error': f'No option strikes found for {symbol}'
                }
            
            logger.info(f"Found {len(strikes_data)} available strikes for {symbol}")
            
            # Step 4: Fetch quotes for all strike tokens
            try:
                oi_data = self._fetch_open_interest_data(strikes_data, current_price)
            except Exception as e:
                logger.error(f"Failed to fetch OI data: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': f'Failed to fetch OI data: {str(e)}'
                }
            
            logger.info(f"✅ Successfully fetched OI data for {symbol}")
            
            # Calculate PCR (Put-Call Ratio) and Max Pain
            pcr_oi = self._calculate_pcr(oi_data['strikes'])
            max_pain = self._calculate_max_pain(oi_data['strikes'], current_price)
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'current_price': current_price,
                'strikes': oi_data['strikes'],
                'ce_summary': oi_data['ce_summary'],
                'pe_summary': oi_data['pe_summary'],
                'pcr_oi': pcr_oi,
                'max_pain': max_pain
            }
            
        except Exception as e:
            logger.error(f"Error in get_open_interest_data: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_available_strikes(self, instruments: List[Dict[str, Any]], symbol: str, 
                              current_price: float, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get available strikes for a symbol similar to KiteDataFetchService.get_available_strikes()
        
        Args:
            instruments: List of all NFO instruments
            symbol: Trading symbol (NIFTY, BANKNIFTY, etc.)
            current_price: Current underlying price
            config: Symbol configuration
            
        Returns:
            List of strikes with CE/PE tokens
        """
        try:
            proper_name = config['name']
            logger.info(f"[DEBUG] Looking for instruments with name='{proper_name}' and type in ['CE', 'PE']")
            logger.info(f"[DEBUG] Total instruments in NFO: {len(instruments)}")
            
            # Log sample instruments to see structure
            if instruments:
                logger.info(f"[DEBUG] Sample instrument 0: {instruments[0]}")
                logger.info(f"[DEBUG] Sample instrument keys: {instruments[0].keys() if instruments else 'N/A'}")
            
            # Filter to symbol options only - use direct key access like options_chart_service does
            symbol_options = []
            for inst in instruments:
                try:
                    inst_name = inst['name']
                    inst_type = inst['instrument_type']
                    
                    # Debug: log mismatches
                    if inst_name and 'NIFTY' in inst_name.upper() and inst_type in ['CE', 'PE']:
                        logger.debug(f"[DEBUG] Found NIFTY-like option: {inst_name} ({inst_type})")
                    
                    if inst_name == proper_name and inst_type in ['CE', 'PE']:
                        symbol_options.append(inst)
                except (KeyError, TypeError) as e:
                    continue
            
            logger.info(f"Found {len(symbol_options)} total options for {symbol} (looking for name='{proper_name}')")
            
            if not symbol_options:
                logger.error(f"No instruments found for {symbol} with name '{proper_name}'")
                # Log ALL unique names for debugging
                all_names = set()
                for inst in instruments:
                    try:
                        name = inst['name']
                        if name:
                            all_names.add(name)
                    except (KeyError, TypeError):
                        pass
                logger.error(f"Available names in instruments ({len(all_names)} unique): {sorted(list(all_names))}")
                return []
            
            # Get current/nearest future expiry - use direct key access
            expiries_set = set()
            for inst in symbol_options:
                try:
                    expiry = inst['expiry']
                    if expiry:
                        expiries_set.add(expiry)
                except (KeyError, TypeError):
                    continue
            
            expiries = sorted(list(expiries_set))
            
            if not expiries:
                logger.warning(f"No expiries found for {symbol}")
                return []
            
            # Select nearest future expiry - expiry is already a date object
            today = datetime.now().date()
            current_expiry = None
            
            for expiry_date in expiries:
                # expiry_date is already a datetime.date object
                if expiry_date >= today:
                    current_expiry = expiry_date
                    break
            
            if not current_expiry:
                current_expiry = expiries[-1]
            
            logger.info(f"Using expiry: {current_expiry} (type: {type(current_expiry).__name__})")
            
            # Group by strike and collect CE/PE tokens
            strikes_dict = {}
            
            for inst in symbol_options:
                try:
                    if inst['expiry'] != current_expiry:
                        continue
                        
                    strike = float(inst['strike'])
                    option_type = inst['instrument_type']
                    token = int(inst['instrument_token'])
                    
                    if strike not in strikes_dict:
                        strikes_dict[strike] = {
                            'strike': strike,
                            'ce_token': None,
                            'pe_token': None,
                            'distance': abs(strike - current_price)
                        }
                    
                    if option_type == 'CE':
                        strikes_dict[strike]['ce_token'] = token
                    else:  # PE
                        strikes_dict[strike]['pe_token'] = token
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"Skipping instrument: {e}")
                    continue
            
            # Keep only strikes with both CE and PE tokens
            available_strikes = [
                s for s in strikes_dict.values() 
                if s['ce_token'] is not None and s['pe_token'] is not None
            ]
            
            # Sort by strike price (ascending) - frontend will handle filtering
            available_strikes.sort(key=lambda x: x['strike'])
            
            logger.info(f"Selected {len(available_strikes)} total strikes for {symbol} (frontend will filter based on range)")
            return available_strikes
            
        except Exception as e:
            logger.error(f"Error getting available strikes: {e}", exc_info=True)
            return []
    
    def _fetch_open_interest_data(self, strikes_data: List[Dict[str, Any]], 
                                 current_price: float) -> Dict[str, Any]:
        """
        Fetch OI data from Kite quotes API using pre-collected tokens.
        
        Args:
            strikes_data: List of strikes with CE/PE tokens
            current_price: Current underlying price
            
        Returns:
            Dictionary with OI data organized by strike
        """
        try:
            # Collect all tokens to fetch as strings (kite.quote expects string tokens)
            all_tokens = []
            token_to_strike_info = {}  # Map string token to strike info for later lookup
            
            for strike_info in strikes_data:
                ce_token = strike_info['ce_token']
                pe_token = strike_info['pe_token']
                
                if ce_token:
                    ce_token_str = str(ce_token)
                    all_tokens.append(ce_token_str)
                    token_to_strike_info[ce_token_str] = (strike_info, 'CE')
                if pe_token:
                    pe_token_str = str(pe_token)
                    all_tokens.append(pe_token_str)
                    token_to_strike_info[pe_token_str] = (strike_info, 'PE')
            
            logger.info(f"Fetching quotes for {len(all_tokens)} tokens...")
            
            if not all_tokens:
                logger.warning("No tokens to fetch")
                return {
                    'strikes': [],
                    'ce_summary': {},
                    'pe_summary': {}
                }
            
            # Fetch quotes in batches
            batch_size = 50
            all_quotes = {}
            
            for i in range(0, len(all_tokens), batch_size):
                batch = all_tokens[i:i + batch_size]
                try:
                    logger.info(f"Fetching batch {i//batch_size + 1} with {len(batch)} tokens...")
                    quotes = self.kite.quote(batch)
                    if quotes:
                        all_quotes.update(quotes)
                        logger.info(f"✓ Batch {i//batch_size + 1}: Fetched {len(quotes)} quotes")
                    else:
                        logger.warning(f"Empty response for batch {i//batch_size + 1}")
                except Exception as e:
                    logger.error(f"Error fetching batch {i//batch_size + 1}: {e}", exc_info=True)
                    continue
            logger.info(f"Total quotes fetched: {len(all_quotes)}")
            
            # Load or initialize opening OI cache
            opening_oi_cache = self._load_opening_oi_cache(symbol)
            should_save_cache = False
            
            # Initialize cache at market open (first 5 minutes)
            if self._should_initialize_cache() and not opening_oi_cache:
                logger.info(f"Market open detected - initializing opening OI cache for {symbol}")
                should_save_cache = True
            
            # Log sample quote to see structure
            if all_quotes:
                first_token = list(all_quotes.keys())[0]
                first_quote = all_quotes[first_token]
                logger.debug(f"Sample quote for token {first_token}: keys = {list(first_quote.keys()) if isinstance(first_quote, dict) else 'Not a dict'}")
            # Organize OI data by strike
            strikes_oi = {}
            
            for strike_info in strikes_data:
                strike = strike_info['strike']
                
                strikes_oi[strike] = {
                    'strike': strike,
                    'ce_oi': 0,
                    'ce_change_in_oi': 0,
                    'ce_iv': 0,
                    'pe_oi': 0,
                    'pe_change_in_oi': 0,
                    'pe_iv': 0
                }
                
                # Get CE data - use string token as key
                ce_token = strike_info['ce_token']
                if ce_token:
                    ce_token_str = str(ce_token)
                    if ce_token_str in all_quotes:
                        try:
                            ce_quote = all_quotes[ce_token_str]
                            if isinstance(ce_quote, dict):
                                # Kite returns 'oi' key for open interest
                                oi_val = ce_quote.get('oi')
                                logger.debug(f"CE Token {ce_token_str} Strike {strike}: oi = {oi_val}")
                                
                                # Convert to int, handling None/null
                                strikes_oi[strike]['ce_oi'] = int(oi_val) if oi_val else 0
                                
                                # Calculate change in OI using cache
                                current_ce_oi = int(oi_val) if oi_val else 0
                                
                                # Try to get opening OI from cache first
                                cached_ce_oi = None
                                if str(strike) in opening_oi_cache:
                                    cached_ce_oi = opening_oi_cache[str(strike)].get('ce_oi')
                                
                                if cached_ce_oi is not None:
                                    # Use cached opening OI
                                    change_in_oi = current_ce_oi - cached_ce_oi
                                else:
                                    # Fallback to API values: try oi_day_open, then oi_day_low
                                    oi_day_open = ce_quote.get('oi_day_open')
                                    if not oi_day_open:
                                        oi_day_open = ce_quote.get('oi_day_low', 0) or 0
                                    change_in_oi = current_ce_oi - (int(oi_day_open) if oi_day_open else 0)
                                    
                                    # If this is market open initialization, save current OI to cache
                                    if should_save_cache:
                                        if str(strike) not in opening_oi_cache:
                                            opening_oi_cache[str(strike)] = {}
                                        opening_oi_cache[str(strike)]['ce_oi'] = current_ce_oi
                                
                                strikes_oi[strike]['ce_change_in_oi'] = change_in_oi
                                
                                # Try to get implied_volatility from quote
                                iv_val = ce_quote.get('implied_volatility')
                                strikes_oi[strike]['ce_iv'] = float(iv_val) if iv_val else 0
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing CE quote for strike {strike} token {ce_token_str}: {e}")
                
                # Get PE data - use string token as key
                pe_token = strike_info['pe_token']
                if pe_token:
                    pe_token_str = str(pe_token)
                    if pe_token_str in all_quotes:
                        try:
                            pe_quote = all_quotes[pe_token_str]
                            if isinstance(pe_quote, dict):
                                # Kite returns 'oi' key for open interest
                                oi_val = pe_quote.get('oi')
                                logger.debug(f"PE Token {pe_token_str} Strike {strike}: oi = {oi_val}")
                                
                                # Convert to int, handling None/null
                                strikes_oi[strike]['pe_oi'] = int(oi_val) if oi_val else 0
                                
                                # Calculate change in OI using cache
                                current_pe_oi = int(oi_val) if oi_val else 0
                                
                                # Try to get opening OI from cache first
                                cached_pe_oi = None
                                if str(strike) in opening_oi_cache:
                                    cached_pe_oi = opening_oi_cache[str(strike)].get('pe_oi')
                                
                                if cached_pe_oi is not None:
                                    # Use cached opening OI
                                    change_in_oi = current_pe_oi - cached_pe_oi
                                else:
                                    # Fallback to API values: try oi_day_open, then oi_day_low
                                    oi_day_open = pe_quote.get('oi_day_open')
                                    if not oi_day_open:
                                        oi_day_open = pe_quote.get('oi_day_low', 0) or 0
                                    change_in_oi = current_pe_oi - (int(oi_day_open) if oi_day_open else 0)
                                    
                                    # If this is market open initialization, save current OI to cache
                                    if should_save_cache:
                                        if str(strike) not in opening_oi_cache:
                                            opening_oi_cache[str(strike)] = {}
                                        opening_oi_cache[str(strike)]['pe_oi'] = current_pe_oi
                                
                                strikes_oi[strike]['pe_change_in_oi'] = change_in_oi
                                
                                # Try to get implied_volatility from quote
                                iv_val = pe_quote.get('implied_volatility')
                                strikes_oi[strike]['pe_iv'] = float(iv_val) if iv_val else 0
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing PE quote for strike {strike} token {pe_token_str}: {e}")
                    else:
                        logger.debug(f"PE Token {pe_token_str} NOT found in quotes")
            
            # Save cache if initialized at market open
            if should_save_cache and opening_oi_cache:
                self._save_opening_oi_cache(symbol, opening_oi_cache)
            
            # Convert to list and calculate summaries
            strikes_list = list(strikes_oi.values())
            
            ce_summary = self._calculate_summary(strikes_list, 'CE')
            pe_summary = self._calculate_summary(strikes_list, 'PE')
            
            logger.info(f"✓ Processed {len(strikes_list)} strikes with OI data")
            
            return {
                'strikes': strikes_list,
                'ce_summary': ce_summary,
                'pe_summary': pe_summary
            }
            
        except Exception as e:
            logger.error(f"Error fetching OI data: {e}", exc_info=True)
            raise
    
    def _calculate_summary(self, strikes: List[Dict[str, Any]], option_type: str) -> Dict[str, Any]:
        """
        Calculate summary statistics for CE or PE.
        
        Args:
            strikes: List of strike data
            option_type: 'CE' or 'PE'
            
        Returns:
            Dictionary with summary statistics
        """
        oi_key = f'{option_type.lower()}_oi'
        coi_key = f'{option_type.lower()}_change_in_oi'
        iv_key = f'{option_type.lower()}_iv'
        
        oi_values = [s[oi_key] for s in strikes if s.get(oi_key)]
        coi_values = [s[coi_key] for s in strikes if s.get(coi_key)]
        iv_values = [s[iv_key] for s in strikes if s.get(iv_key)]
        
        total_oi = sum(oi_values) if oi_values else 0
        total_coi = sum(coi_values) if coi_values else 0
        
        # Find max OI strike
        max_oi_strike = None
        max_oi_value = 0
        for strike in strikes:
            if strike[oi_key] > max_oi_value:
                max_oi_value = strike[oi_key]
                max_oi_strike = strike['strike']
        
        avg_iv = sum(iv_values) / len(iv_values) if iv_values else 0
        
        return {
            'total_oi': total_oi,
            'change_in_oi': total_coi,
            'max_oi_strike': max_oi_strike,
            'max_oi_value': max_oi_value,
            'avg_iv': avg_iv
        }
    
    def _calculate_pcr(self, strikes: List[Dict[str, Any]]) -> float:
        """
        Calculate Put-Call Ratio (PCR) based on Open Interest.
        
        PCR = Total PE OI / Total CE OI
        
        Args:
            strikes: List of strike data
            
        Returns:
            PCR value (float)
        """
        try:
            total_pe_oi = sum(s.get('pe_oi', 0) for s in strikes)
            total_ce_oi = sum(s.get('ce_oi', 0) for s in strikes)
            
            if total_ce_oi == 0:
                return 0.0
            
            pcr = total_pe_oi / total_ce_oi
            logger.info(f"PCR calculated: {pcr:.2f} (PE OI: {total_pe_oi}, CE OI: {total_ce_oi})")
            return round(pcr, 2)
        except Exception as e:
            logger.error(f"Error calculating PCR: {e}")
            return 0.0
    
    def _calculate_max_pain(self, strikes: List[Dict[str, Any]], current_price: float) -> float:
        """
        Calculate Max Pain using Open Interest - Zerodha Varsity Method.
        
        Max Pain is the strike price where option writers would incur the LEAST amount of loss
        if the underlying expires at that price. 
        
        Algorithm (from Zerodha Varsity Chapter 13):
        1. For each strike price, assume market expires at that strike
        2. Calculate loss for Call writers: For each CE strike < test_strike, loss = (test_strike - CE_strike) × CE_OI
        3. Calculate loss for Put writers: For each PE strike > test_strike, loss = (PE_strike - test_strike) × PE_OI
        4. Find the strike where TOTAL LOSS IS MINIMUM
        
        Args:
            strikes: List of strike data with 'strike', 'pe_oi', 'ce_oi' keys
            current_price: Current underlying price
            
        Returns:
            Max Pain strike price (float)
        """
        try:
            if not strikes:
                return current_price
            
            # Sort strikes by strike price
            sorted_strikes = sorted(strikes, key=lambda x: x['strike'])
            
            if not sorted_strikes:
                return current_price
            
            # Initialize with maximum loss to find minimum
            min_loss = float('inf')
            max_pain_strike = current_price
            
            # Test each strike as potential max pain level
            for test_strike_data in sorted_strikes:
                test_strike = test_strike_data['strike']
                total_loss = 0
                
                # Calculate loss for Call Option Writers
                # CE writers lose money if price > their strike (intrinsic value = price - strike)
                for strike_data in sorted_strikes:
                    strike = strike_data['strike']
                    ce_oi = strike_data.get('ce_oi', 0)
                    
                    if strike < test_strike:
                        # Call is ITM at test_strike, writer loses (test_strike - strike) per contract
                        loss_per_contract = test_strike - strike
                        ce_loss = loss_per_contract * ce_oi
                        total_loss += ce_loss
                
                # Calculate loss for Put Option Writers
                # PE writers lose money if price < their strike (intrinsic value = strike - price)
                for strike_data in sorted_strikes:
                    strike = strike_data['strike']
                    pe_oi = strike_data.get('pe_oi', 0)
                    
                    if strike > test_strike:
                        # Put is ITM at test_strike, writer loses (strike - test_strike) per contract
                        loss_per_contract = strike - test_strike
                        pe_loss = loss_per_contract * pe_oi
                        total_loss += pe_loss
                
                logger.debug(f"Test Strike {test_strike}: Total Loss = {total_loss}")
                
                # Find the strike with MINIMUM loss (least pain to option writers)
                if total_loss < min_loss:
                    min_loss = total_loss
                    max_pain_strike = test_strike
            
            logger.info(f"Max Pain calculated: {max_pain_strike} (Current Price: {current_price}, Minimum Loss: {min_loss})")
            return max_pain_strike
        except Exception as e:
            logger.error(f"Error calculating Max Pain: {e}")
            return current_price

