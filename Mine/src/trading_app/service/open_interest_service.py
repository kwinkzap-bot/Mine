"""
Open Interest Service - Fetches and processes open interest data from Zerodha Kite
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from kiteconnect import KiteConnect
from kiteconnect.exceptions import NetworkException, TokenException

logger = logging.getLogger(__name__)


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
                'name': 'NIFTY 50',
                'instrument_key': 'NSE:NIFTY 50',
                'lot_size': 50,
                'strike_diff': 50
            },
            'BANKNIFTY': {
                'name': 'NIFTY BANK',
                'instrument_key': 'NSE:NIFTY BANK',
                'lot_size': 25,
                'strike_diff': 100
            },
            'FINNIFTY': {
                'name': 'NIFTY FIN SERVICE',
                'instrument_key': 'NSE:NIFTY FIN SERVICE',
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
            
            return {
                'success': True,
                'symbol': symbol,
                'timestamp': datetime.now().isoformat(),
                'current_price': current_price,
                'strikes': oi_data['strikes'],
                'ce_summary': oi_data['ce_summary'],
                'pe_summary': oi_data['pe_summary']
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
            
            # Filter to symbol options only - use direct key access like options_chart_service does
            symbol_options = []
            for inst in instruments:
                try:
                    if inst['name'] == proper_name and inst['instrument_type'] in ['CE', 'PE']:
                        symbol_options.append(inst)
                except (KeyError, TypeError):
                    continue
            
            logger.info(f"Found {len(symbol_options)} total options for {symbol}")
            
            if not symbol_options:
                logger.error(f"No instruments found for {symbol} with name '{proper_name}'")
                # Log sample names for debugging
                all_names = set()
                for inst in instruments:
                    try:
                        all_names.add(inst['name'])
                    except (KeyError, TypeError):
                        pass
                logger.error(f"Available names in instruments: {list(all_names)[:20]}")
                return []
            
            # Get current/nearest future expiry - use direct key access
            expiries_set = set()
            for inst in symbol_options:
                try:
                    if inst['expiry']:
                        expiries_set.add(inst['expiry'])
                except (KeyError, TypeError):
                    continue
            
            expiries = sorted(list(expiries_set))
            
            if not expiries:
                logger.warning(f"No expiries found for {symbol}")
                return []
            
            # Select nearest future expiry
            today = datetime.now().date()
            current_expiry = None
            
            for expiry_str in expiries:
                try:
                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                    if expiry_date >= today:
                        current_expiry = expiry_str
                        break
                except (ValueError, TypeError):
                    continue
            
            if not current_expiry:
                current_expiry = expiries[-1]
            
            logger.info(f"Using expiry: {current_expiry}")
            
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
            
            # Sort by distance to current price and keep nearest 20
            available_strikes.sort(key=lambda x: x['distance'])
            nearby_strikes = available_strikes[:20]
            
            logger.info(f"Selected {len(nearby_strikes)} strikes near current price {current_price}")
            return nearby_strikes
            
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
            # Collect all tokens to fetch
            all_tokens = []
            
            for strike_info in strikes_data:
                ce_token = strike_info['ce_token']
                pe_token = strike_info['pe_token']
                
                if ce_token:
                    all_tokens.append(ce_token)
                if pe_token:
                    all_tokens.append(pe_token)
            
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
                
                # Get CE data
                ce_token = strike_info['ce_token']
                if ce_token in all_quotes:
                    try:
                        ce_quote = all_quotes[ce_token]
                        if isinstance(ce_quote, dict):
                            strikes_oi[strike]['ce_oi'] = int(ce_quote.get('open_interest', 0) or 0)
                            strikes_oi[strike]['ce_iv'] = float(ce_quote.get('implied_volatility', 0) or 0)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error parsing CE quote for strike {strike}: {e}")
                
                # Get PE data
                pe_token = strike_info['pe_token']
                if pe_token in all_quotes:
                    try:
                        pe_quote = all_quotes[pe_token]
                        if isinstance(pe_quote, dict):
                            strikes_oi[strike]['pe_oi'] = int(pe_quote.get('open_interest', 0) or 0)
                            strikes_oi[strike]['pe_iv'] = float(pe_quote.get('implied_volatility', 0) or 0)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Error parsing PE quote for strike {strike}: {e}")
            
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
