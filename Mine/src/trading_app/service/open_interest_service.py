"""
Open Interest Service - Fetches and processes open interest data from Zerodha Kite
"""
import logging
import math
from datetime import datetime, timedelta, time
from typing import Dict, List, Any, Optional
from kiteconnect import KiteConnect
from kiteconnect.exceptions import NetworkException, TokenException
from trading_app.app.utils.opening_oi_cache import get_opening_oi_cache

logger = logging.getLogger(__name__)


def _normal_cdf(x: float) -> float:
    """Approximate normal CDF using error function approximation."""
    # Using Abramowitz and Stegun approximation
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    
    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2)
    
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    
    return 0.5 * (1.0 + sign * y)


def _black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculate Black-Scholes call option price."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    call_price = S * _normal_cdf(d1) - K * math.exp(-r * T) * _normal_cdf(d2)
    return call_price


def _black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculate Black-Scholes put option price."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    put_price = K * math.exp(-r * T) * _normal_cdf(-d2) - S * _normal_cdf(-d1)
    return put_price


def _get_oi_from_historical_data(kite: KiteConnect, instrument_token: int) -> Optional[Dict[str, Any]]:
    """
    Fetch open interest from historical data endpoint.
    
    Note: Historical data is finalized end-of-day. During market hours or immediately
    after close, today's data may not be available yet. Use oi_day_low from quotes instead.
    
    Args:
        kite: KiteConnect client instance
        instrument_token: Token of the instrument
        
    Returns:
        Dictionary with 'current_oi', 'opening_oi', and 'timestamp' or None
    """
    try:
        # Fetch last 3 days of data to handle delayed finalization
        to_date = datetime.now()
        from_date = to_date - timedelta(days=3)
        
        data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date.strftime('%Y-%m-%d'),
            to_date=to_date.strftime('%Y-%m-%d'),
            interval='day',
            oi=True
        )
        
        if not data or len(data) == 0:
            logger.debug(f"Historical data empty for token {instrument_token}")
            return None
        
        # Get most recent available data
        latest_candle = data[-1]
        current_oi = latest_candle.get('oi', 0)
        
        # Get previous day's closing OI (proxy for opening)
        opening_oi = None
        if len(data) > 1:
            opening_oi = data[-2].get('oi', 0)
        
        return {
            'current_oi': current_oi,
            'opening_oi': opening_oi,
            'timestamp': latest_candle.get('date')
        }
        
    except Exception as e:
        logger.debug(f"Historical data failed for {instrument_token}: {e}")
        return None


def _estimate_oi_change_from_depth(quote: Dict[str, Any]) -> Optional[float]:
    """
    Estimate OI change when oi_day_low is not available.
    Uses bid-ask depth and volume indicators as proxy.
    
    Args:
        quote: Quote dictionary from Kite API
        
    Returns:
        Estimated OI change or None if cannot estimate
    """
    try:
        # Check for bid-ask spread data (depth)
        if 'depth' not in quote:
            return None
        
        depth = quote.get('depth', {})
        if not depth or 'buy' not in depth or 'sell' not in depth:
            return None
        
        buy_depth = depth.get('buy', [])
        sell_depth = depth.get('sell', [])
        
        if not buy_depth or not sell_depth:
            return None
        
        # Get bid-ask volume imbalance (positive = more buyers, negative = more sellers)
        buy_volume = sum(item.get('quantity', 0) for item in buy_depth if item)
        sell_volume = sum(item.get('quantity', 0) for item in sell_depth if item)
        
        # Volume imbalance as percentage (can indicate OI change direction)
        if buy_volume + sell_volume == 0:
            return None
        
        imbalance_ratio = (buy_volume - sell_volume) / (buy_volume + sell_volume)
        
        # Use current OI as base for estimation
        current_oi = quote.get('oi', 0)
        if current_oi <= 0:
            return None
        
        # Estimate OI change as a small percentage of current OI based on imbalance
        estimated_change = int(current_oi * imbalance_ratio * 0.1)  # Conservative 10% of imbalance
        
        return estimated_change
    except Exception as e:
        logger.debug(f"Could not estimate OI change from depth: {e}")
        return None


def _calculate_iv_from_price(S: float, K: float, T: float, r: float, market_price: float, option_type: str) -> float:
    """
    Calculate implied volatility from option market price using Newton-Raphson method.
    
    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiration (in years)
        r: Risk-free rate
        market_price: Observed market price of option
        option_type: 'CE' for call, 'PE' for put
        
    Returns:
        Implied volatility (as decimal, e.g., 0.25 = 25%)
    """
    if market_price <= 0 or T <= 0:
        return 0.0
    
    # Intrinsic value bounds
    if option_type == 'CE':
        intrinsic = max(S - K, 0)
        if market_price < intrinsic:
            return 0.0
    else:  # PE
        intrinsic = max(K - S, 0)
        if market_price < intrinsic:
            return 0.0
    
    try:
        # Newton-Raphson method to find IV
        sigma = 0.5  # Initial guess: 50% volatility
        max_iterations = 100
        tolerance = 1e-6
        
        for i in range(max_iterations):
            # Calculate option price and vega at current sigma
            if option_type == 'CE':
                price = _black_scholes_call(S, K, T, r, sigma)
            else:
                price = _black_scholes_put(S, K, T, r, sigma)
            
            # Check convergence
            if abs(price - market_price) < tolerance:
                return sigma
            
            # Calculate vega (derivative of price w.r.t. sigma)
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T)) if sigma > 0 else 0
            vega = S * _normal_cdf(d1) * math.sqrt(T)
            
            if abs(vega) < 1e-10:  # Vega too small, avoid division
                break
            
            # Newton-Raphson update
            sigma = sigma - (price - market_price) / vega
            
            # Ensure sigma stays in reasonable bounds
            sigma = max(0.001, min(sigma, 2.0))
        
        return sigma
    except Exception as e:
        logger.debug(f"IV calculation failed: {e}")
        return 0.0


class OpenInterestService:
    """Service to fetch and process open interest data for options."""
    
    def __init__(self, kite_instance: KiteConnect):
        """
        Initialize OpenInterestService.
        
        Args:
            kite_instance: KiteConnect instance
        """
        self.kite = kite_instance
        
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
            self._current_symbol = symbol  # Store for use in cache operations
            
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
            
            # Calculate PCR (Put-Call Ratio), Max Pain, and IV Percentile
            pcr_oi = self._calculate_pcr(oi_data['strikes'])
            max_pain = self._calculate_max_pain(oi_data['strikes'], current_price)
            iv_percentile = self._calculate_iv_percentile(oi_data['strikes'], current_price)
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'current_price': current_price,
                'strikes': oi_data['strikes'],
                'ce_summary': oi_data['ce_summary'],
                'pe_summary': oi_data['pe_summary'],
                'pcr_oi': pcr_oi,
                'max_pain': max_pain,
                'iv_percentile': iv_percentile
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
                            'distance': abs(strike - current_price),
                            'expiry': current_expiry  # Add expiry date for IV calculation
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
            # Get opening OI cache
            opening_oi_cache = get_opening_oi_cache()
            
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
                    # Request quote data - Kite API returns oi, oi_day_low, oi_day_high by default
                    quotes = self.kite.quote(batch)  # Standard quote includes OI fields
                    if quotes:
                        all_quotes.update(quotes)
                        logger.info(f"✓ Batch {i//batch_size + 1}: Fetched {len(quotes)} quotes")
                    else:
                        logger.warning(f"Empty response for batch {i//batch_size + 1}")
                except Exception as e:
                    logger.error(f"Error fetching batch {i//batch_size + 1}: {e}", exc_info=True)
                    continue
            logger.info(f"Total quotes fetched: {len(all_quotes)}")
            
            # Log sample quote to see structure and available OI fields
            if all_quotes:
                first_token = list(all_quotes.keys())[0]
                first_quote = all_quotes[first_token]
                logger.debug(f"Sample quote for token {first_token}: keys = {list(first_quote.keys()) if isinstance(first_quote, dict) else 'Not a dict'}")
                logger.info(f"Sample Quote Keys Available: {list(first_quote.keys()) if isinstance(first_quote, dict) else 'Not a dict'}")
                
                # Check for OI-related fields
                if isinstance(first_quote, dict):
                    oi_fields = {k: v for k, v in first_quote.items() if 'oi' in k.lower()}
                    logger.info(f"OI-Related Fields in Quote: {oi_fields}")
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
                                # Log all available keys in first quote
                                if 'ce_iv_logged' not in locals():
                                    logger.info(f"CE Quote Keys: {list(ce_quote.keys())}")
                                    ce_iv_logged = True
                                
                                # Kite returns 'oi' key for open interest
                                oi_val = ce_quote.get('oi')
                                logger.debug(f"CE Token {ce_token_str} Strike {strike}: oi = {oi_val}")
                                
                                # Convert to int, handling None/null
                                current_ce_oi = int(oi_val) if oi_val else 0
                                strikes_oi[strike]['ce_oi'] = current_ce_oi
                                
                                # Get opening OI from cache for accurate change calculation
                                cached_opening_oi = opening_oi_cache.get_opening_oi(self._current_symbol, strike, 'CE')
                                
                                # Get opening OI value - try multiple sources
                                # IMPORTANT: opening_oi should be the OI at market open (9:15 AM)
                                # which is yesterday's closing OI from historical data
                                oi_day_low = ce_quote.get('oi_day_low', 0) or 0
                                oi_day_low = int(oi_day_low) if oi_day_low else 0
                                
                                # Try to get opening OI from any available source
                                opening_oi = None
                                source = "unknown"
                                
                                # Source 1: Cache from 9:15 AM (best if available)
                                if cached_opening_oi is not None:
                                    opening_oi = cached_opening_oi
                                    source = "cache"
                                
                                # Source 2: Historical data (yesterday's closing = today's opening)
                                elif opening_oi is None:
                                    try:
                                        hist_oi_data = _get_oi_from_historical_data(self.kite, ce_token)
                                        if hist_oi_data and hist_oi_data.get('opening_oi') is not None:
                                            opening_oi = hist_oi_data['opening_oi']
                                            source = "historical_data"
                                    except Exception as e:
                                        logger.debug(f"Historical data failed for CE {strike}: {e}")
                                
                                # Source 3: Fallback to oi_day_low (less reliable but better than nothing)
                                if opening_oi is None and oi_day_low > 0:
                                    opening_oi = oi_day_low
                                    source = "oi_day_low"
                                
                                # Calculate OI change: current - opening (can be negative if OI decreased)
                                if opening_oi is not None:
                                    change_in_oi = current_ce_oi - opening_oi
                                else:
                                    # No opening OI data available
                                    change_in_oi = 0
                                    source = "no_opening_data"
                                
                                strikes_oi[strike]['ce_change_in_oi'] = change_in_oi
                                
                                # Calculate IV from option price using Black-Scholes
                                last_price = ce_quote.get('last_price', 0)
                                
                                if last_price > 0 and strike_info.get('expiry'):
                                    # Calculate days to expiration
                                    expiry_date = strike_info['expiry']
                                    if isinstance(expiry_date, str):
                                        expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date()
                                    
                                    days_to_expiry = (expiry_date - datetime.now().date()).days
                                    T = max(days_to_expiry / 365.0, 0.001)  # Time to expiration in years
                                    r = 0.05  # Risk-free rate (5% assumption)
                                    K = strike
                                    S = current_price
                                    
                                    # Calculate IV from last traded price
                                    iv_val = _calculate_iv_from_price(S, K, T, r, last_price, 'CE')
                                    logger.debug(f"CE Strike {strike}: IV = {iv_val:.4f} from LTP={last_price}")
                                else:
                                    iv_val = 0.0
                                    logger.debug(f"CE Strike {strike}: Could not calculate IV (LTP={last_price}, expiry={strike_info.get('expiry')})")
                                
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
                                current_pe_oi = int(oi_val) if oi_val else 0
                                strikes_oi[strike]['pe_oi'] = current_pe_oi
                                
                                # Get opening OI from cache for accurate change calculation
                                cached_opening_oi = opening_oi_cache.get_opening_oi(self._current_symbol, strike, 'PE')
                                
                                # Get opening OI value - try multiple sources
                                # IMPORTANT: opening_oi should be the OI at market open (9:15 AM)
                                # which is yesterday's closing OI from historical data
                                oi_day_low = pe_quote.get('oi_day_low', 0) or 0
                                oi_day_low = int(oi_day_low) if oi_day_low else 0
                                
                                # Try to get opening OI from any available source
                                opening_oi = None
                                source = "unknown"
                                
                                # Source 1: Cache from 9:15 AM (best if available)
                                if cached_opening_oi is not None:
                                    opening_oi = cached_opening_oi
                                    source = "cache"
                                
                                # Source 2: Historical data (yesterday's closing = today's opening)
                                elif opening_oi is None:
                                    try:
                                        hist_oi_data = _get_oi_from_historical_data(self.kite, pe_token)
                                        if hist_oi_data and hist_oi_data.get('opening_oi') is not None:
                                            opening_oi = hist_oi_data['opening_oi']
                                            source = "historical_data"
                                    except Exception as e:
                                        logger.debug(f"Historical data failed for PE {strike}: {e}")
                                
                                # Source 3: Fallback to oi_day_low (less reliable but better than nothing)
                                if opening_oi is None and oi_day_low > 0:
                                    opening_oi = oi_day_low
                                    source = "oi_day_low"
                                
                                # Calculate OI change: current - opening (can be negative if OI decreased)
                                if opening_oi is not None:
                                    change_in_oi = current_pe_oi - opening_oi
                                else:
                                    # No opening OI data available
                                    change_in_oi = 0
                                    source = "no_opening_data"
                                
                                strikes_oi[strike]['pe_change_in_oi'] = change_in_oi
                                
                                # Calculate IV from option price using Black-Scholes
                                last_price = pe_quote.get('last_price', 0)
                                
                                if last_price > 0 and strike_info.get('expiry'):
                                    # Calculate days to expiration
                                    expiry_date = strike_info['expiry']
                                    if isinstance(expiry_date, str):
                                        expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date()
                                    
                                    days_to_expiry = (expiry_date - datetime.now().date()).days
                                    T = max(days_to_expiry / 365.0, 0.001)  # Time to expiration in years
                                    r = 0.05  # Risk-free rate (5% assumption)
                                    K = strike
                                    S = current_price
                                    
                                    # Calculate IV from last traded price
                                    iv_val = _calculate_iv_from_price(S, K, T, r, last_price, 'PE')
                                    logger.debug(f"PE Strike {strike}: IV = {iv_val:.4f} from LTP={last_price}")
                                else:
                                    iv_val = 0.0
                                    logger.debug(f"PE Strike {strike}: Could not calculate IV (LTP={last_price}, expiry={strike_info.get('expiry')})")
                                
                                strikes_oi[strike]['pe_iv'] = float(iv_val) if iv_val else 0
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing PE quote for strike {strike} token {pe_token_str}: {e}")
                    else:
                        logger.debug(f"PE Token {pe_token_str} NOT found in quotes")
            
            # Cache opening OI if it's 9:15 AM - 9:20 AM (first call of the day)
            current_time = datetime.now().time()
            market_open = time(9, 15)
            market_open_end = time(9, 20)
            
            is_cache_window = market_open <= current_time <= market_open_end
            is_already_cached = opening_oi_cache.is_cached_today(self._current_symbol)
            logger.info(f"Cache Status: time={current_time}, window={is_cache_window}, already_cached={is_already_cached}")
            
            if is_cache_window and not is_already_cached:
                logger.info(f"📝 Caching opening OI for {self._current_symbol} at {current_time}")
                opening_oi_data = {strike: {'ce_oi': data['ce_oi'], 'pe_oi': data['pe_oi']} 
                                   for strike, data in strikes_oi.items()}
                logger.debug(f"Opening OI data sample: {list(opening_oi_data.items())[:3]}")
                opening_oi_cache.cache_opening_oi(self._current_symbol, opening_oi_data)
                logger.info(f"✅ Opening OI cached successfully")
            
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
    
    def _calculate_iv_percentile(self, strikes: List[Dict[str, Any]], current_price: float) -> float:
        """
        Calculate IV Percentile - measures current IV relative to its 52-week range.
        
        IV Percentile = (Current IV - Min IV) / (Max IV - Min IV) * 100
        
        Uses the IV of the strike closest to current price.
        
        Args:
            strikes: List of strike data with 'strike', 'ce_iv' and 'pe_iv' keys
            current_price: Current market price
            
        Returns:
            IV Percentile value (0-100)
        """
        try:
            logger.info(f"Starting IV Percentile calculation with {len(strikes)} strikes, current_price: {current_price}")
            
            # Collect all IV values and find ATM
            all_ivs = []
            atm_iv = None
            closest_distance = float('inf')
            
            for i, strike in enumerate(strikes):
                ce_iv = strike.get('ce_iv', 0)
                pe_iv = strike.get('pe_iv', 0)
                strike_price = strike.get('strike', 0)
                
                logger.debug(f"Strike {i}: price={strike_price}, ce_iv={ce_iv}, pe_iv={pe_iv}")
                
                if ce_iv > 0:
                    all_ivs.append(ce_iv)
                if pe_iv > 0:
                    all_ivs.append(pe_iv)
                
                # Find the strike closest to current price (ATM)
                distance = abs(current_price - strike_price)
                if distance < closest_distance:
                    closest_distance = distance
                    # Average the CE and PE IV for this strike
                    if ce_iv > 0 and pe_iv > 0:
                        atm_iv = (ce_iv + pe_iv) / 2
                    else:
                        atm_iv = ce_iv if ce_iv > 0 else pe_iv
                    logger.debug(f"New closest strike found: {strike_price} (distance: {distance}, ATM IV: {atm_iv})")
            
            logger.info(f"Collected {len(all_ivs)} IV values, ATM IV: {atm_iv}")
            
            if not all_ivs:
                logger.warning("No IV values found for percentile calculation")
                return 50.0
            
            if atm_iv is None or atm_iv == 0:
                logger.warning(f"Could not determine ATM IV (atm_iv={atm_iv})")
                return 50.0
            
            # Calculate statistics
            min_iv = min(all_ivs)
            max_iv = max(all_ivs)
            
            logger.info(f"IV Stats - Min: {min_iv}, Max: {max_iv}, ATM: {atm_iv}")
            
            # Avoid division by zero
            if max_iv == min_iv:
                logger.warning(f"Min IV equals Max IV ({min_iv}), returning 50%")
                return 50.0
            
            # Calculate percentile using ATM IV as current IV
            iv_percentile = ((atm_iv - min_iv) / (max_iv - min_iv)) * 100
            iv_percentile = max(0, min(100, iv_percentile))  # Clamp between 0-100
            
            logger.info(f"✓ IV Percentile calculated: {iv_percentile:.2f}% (Min IV: {min_iv:.2f}, ATM IV: {atm_iv:.2f}, Max IV: {max_iv:.2f})")
            return iv_percentile
        except Exception as e:
            logger.error(f"Error calculating IV Percentile: {e}", exc_info=True)
            return 50.0  # Default to middle on error

