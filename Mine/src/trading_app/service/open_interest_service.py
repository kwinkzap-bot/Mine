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
            
            # Step 2: Get all instruments for the symbol
            try:
                instruments = self.kite.instruments('NFO')
            except Exception as e:
                logger.error(f"Failed to get instruments: {e}")
                return {
                    'success': False,
                    'error': f'Failed to get instruments: {str(e)}'
                }
            
            # Step 3: Filter instruments for the symbol with latest expiry
            option_instruments = self._filter_option_instruments(instruments, symbol, config)
            
            if not option_instruments:
                return {
                    'success': False,
                    'error': f'No option instruments found for {symbol}'
                }
            
            logger.info(f"Found {len(option_instruments)} option instruments for {symbol}")
            
            # Step 4: Get quotes for all instruments to fetch OI data
            try:
                oi_data = self._fetch_open_interest_data(option_instruments, current_price, config)
            except Exception as e:
                logger.error(f"Failed to fetch OI data: {e}")
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
    
    def _filter_option_instruments(self, instruments: List[Dict[str, Any]], symbol: str, 
                                  config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Filter instruments to get only CE and PE for the symbol with latest expiry.
        
        Args:
            instruments: List of all instruments from Kite
            symbol: Trading symbol
            config: Symbol configuration
            
        Returns:
            Filtered list of option instruments
        """
        try:
            logger.info(f"Filtering instruments for {symbol} (name: {config['name']})")
            logger.info(f"Total instruments received: {len(instruments)}")
            
            # Get all unique names to see what we have
            all_names = set(inst.get('name') for inst in instruments if inst.get('name'))
            logger.info(f"All unique instrument names: {all_names}")
            
            # Filter to symbol and option types using the proper name from config
            proper_name = config['name']  # e.g., 'NIFTY 50' instead of 'NIFTY'
            symbol_instruments = [
                inst for inst in instruments
                if inst.get('name') == proper_name and inst.get('instrument_type') in ['CE', 'PE']
            ]
            
            logger.info(f"Found {len(symbol_instruments)} option instruments for {proper_name}")
            
            if not symbol_instruments:
                # Try alternative matching - search for partial name match
                logger.warning(f"Exact match failed, trying partial match for {proper_name}")
                for name in all_names:
                    if symbol in name or name in symbol or symbol.lower() in name.lower():
                        logger.info(f"Trying partial match: {name}")
                        symbol_instruments = [
                            inst for inst in instruments
                            if inst.get('name') == name and inst.get('instrument_type') in ['CE', 'PE']
                        ]
                        if symbol_instruments:
                            logger.info(f"Found {len(symbol_instruments)} instruments with name: {name}")
                            break
            
            if not symbol_instruments:
                logger.error(f"No option instruments found for {symbol} (looking for name: {proper_name})")
                return []
            
            # Get unique expiries and sort to find the latest one
            expiries = sorted([exp for exp in set(inst.get('expiry') for inst in symbol_instruments) if exp is not None])
            
            if not expiries:
                logger.warning(f"No expiries found for {symbol}")
                return []
            
            # Get the latest/nearest future expiry
            today = datetime.now().date()
            current_expiry = None
            
            for expiry_str in expiries:
                try:
                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                    if expiry_date >= today:
                        current_expiry = expiry_str
                        break
                except ValueError:
                    continue
            
            if not current_expiry:
                # If no future expiry, use the latest available
                current_expiry = expiries[-1]
            
            logger.info(f"Using expiry: {current_expiry} for {symbol}")
            
            # Filter by selected expiry
            filtered = [
                inst for inst in symbol_instruments
                if inst.get('expiry') == current_expiry
            ]
            
            return filtered
            
        except Exception as e:
            logger.error(f"Error filtering instruments: {e}")
            return []
    
    def _fetch_open_interest_data(self, instruments: List[Dict[str, Any]], 
                                 current_price: float, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch open interest data from instruments.
        
        Args:
            instruments: Filtered option instruments
            current_price: Current underlying price
            config: Symbol configuration
            
        Returns:
            Dictionary with OI data organized by strike
        """
        try:
            # Group instruments by strike
            strikes_dict = {}
            
            for inst in instruments:
                strike = inst.get('strike')
                option_type = inst.get('instrument_type')  # CE or PE
                open_interest = inst.get('open_interest', 0)
                iv = inst.get('implied_volatility', 0)  # Implied volatility
                
                if strike is None or not isinstance(strike, (int, float)):
                    continue
                    
                strike_float = float(strike)
                if strike not in strikes_dict:
                    strikes_dict[strike] = {
                        'strike': strike,
                        'ce_oi': 0,
                        'ce_change_in_oi': 0,
                        'ce_iv': 0,
                        'pe_oi': 0,
                        'pe_change_in_oi': 0,
                        'pe_iv': 0,
                        'distance': abs(strike_float - current_price)
                    }
                
                if option_type == 'CE':
                    strikes_dict[strike]['ce_oi'] = open_interest
                    strikes_dict[strike]['ce_iv'] = iv
                    # Change in OI is calculated as difference (simplified)
                    # In production, this would come from tick-by-tick data
                    strikes_dict[strike]['ce_change_in_oi'] = 0
                else:  # PE
                    strikes_dict[strike]['pe_oi'] = open_interest
                    strikes_dict[strike]['pe_iv'] = iv
                    strikes_dict[strike]['pe_change_in_oi'] = 0
            
            # Sort by distance to current price
            sorted_strikes = sorted(
                strikes_dict.values(),
                key=lambda x: x['distance']
            )
            
            # Keep only strikes near current price (±10 strikes)
            nearby_strikes = sorted_strikes[:20]  # Top 20 nearest strikes
            
            # Calculate summaries
            ce_summary = self._calculate_summary(nearby_strikes, 'CE')
            pe_summary = self._calculate_summary(nearby_strikes, 'PE')
            
            logger.info(f"Processed {len(nearby_strikes)} strikes")
            
            return {
                'strikes': nearby_strikes,
                'ce_summary': ce_summary,
                'pe_summary': pe_summary
            }
            
        except Exception as e:
            logger.error(f"Error fetching OI data: {e}")
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
