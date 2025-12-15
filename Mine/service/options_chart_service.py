import logging
from datetime import datetime, timedelta
from service.kite_service import KiteService
from typing import Tuple, Dict, Any, List, Optional, Union # Added typing imports

class OptionsChartService:
    def __init__(self, kite_instance):
        self.kite_service = KiteService(kite_instance)
    
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

    def get_strikes_for_symbol(self, symbol: str, price_source: str = 'previous_close') -> Dict[str, Any]:
        """
        Get available CE and PE strikes for a symbol and return default selections.
        
        Args:
            symbol: The trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            price_source: 'ltp' for current price or 'previous_close' for previous day close
        """
        try:
            instruments = self.kite_service.kite.instruments("NFO")
            
            # FIX: Use name.upper() for robustness
            symbol_instruments = [
                inst for inst in instruments
                if inst['name'].upper() == symbol.upper() and inst['instrument_type'] in ['CE', 'PE']
            ]
            
            expiries = sorted(list(set(inst['expiry'] for inst in symbol_instruments)))
            if not expiries:
                return {'strikes': [], 'default_ce_token': None, 'default_pe_token': None}
            
            current_expiry = expiries[0]
            
            current_expiry_instruments = [
                inst for inst in symbol_instruments
                if inst['expiry'] == current_expiry
            ]
            
            strikes_dict: Dict[Union[int, float], Dict[str, Any]] = {}
            for inst in current_expiry_instruments:
                strike = float(inst['strike']) # FIX: Ensure strike is float for comparison
                if strike not in strikes_dict:
                    strikes_dict[strike] = {'strike': strike, 'ce_token': None, 'pe_token': None}
                
                if inst['instrument_type'] == 'CE':
                    strikes_dict[strike]['ce_token'] = inst['instrument_token']
                elif inst['instrument_type'] == 'PE':
                    strikes_dict[strike]['pe_token'] = inst['instrument_token']
            
            strikes: List[Dict[str, Any]] = sorted([s for s in strikes_dict.values() if s['ce_token'] and s['pe_token']], key=lambda x: x['strike'])
            
            instrument_key = f'NSE:{symbol}'
            if symbol.upper() == 'NIFTY':
                instrument_key = 'NSE:NIFTY 50'
            elif symbol.upper() == 'BANKNIFTY':
                instrument_key = 'NSE:NIFTY BANK'
            elif symbol.upper() == 'FINNIFTY':
                instrument_key = 'NSE:NIFTY FIN SERVICE'

            base_price = 0.0
            
            # Fetch price based on price_source parameter
            if price_source == 'ltp':
                # Get current LTP
                try:
                    base_price = self.kite_service.get_current_ltp(symbol)
                    if base_price:
                        logging.info(f"Using current LTP for {symbol}: {base_price}")
                    else:
                        raise ValueError("Could not fetch LTP")
                except Exception as ltp_error:
                    logging.warning(f"Error getting LTP for {symbol}: {ltp_error}. Falling back to previous close.")
                    try:
                        base_price = self.kite_service.get_previous_close(symbol)
                        if base_price:
                            logging.info(f"Using previous close for {symbol}: {base_price}")
                    except Exception as pdc_error:
                        logging.warning(f"Error getting previous close: {pdc_error}. Falling back to mid strike.")
                        if strikes:
                            base_price = strikes[len(strikes) // 2]['strike']
            else:  # previous_close (default)
                # Get previous close (PDC)
                try:
                    base_price = self.kite_service.get_previous_close(symbol)
                    if base_price:
                        logging.info(f"Using previous close for {symbol}: {base_price}")
                    else:
                        raise ValueError("Could not fetch previous close")
                except Exception as pdc_error:
                    logging.warning(f"Error getting previous close for {symbol}: {pdc_error}. Falling back to LTP or mid strike.")
                    try:
                        base_price = self.kite_service.get_current_ltp(symbol)
                        if base_price:
                            logging.info(f"Using current LTP as fallback for {symbol}: {base_price}")
                    except Exception as ltp_error:
                        logging.warning(f"Error getting LTP: {ltp_error}. Falling back to mid strike.")
                        if strikes:
                            base_price = strikes[len(strikes) // 2]['strike']
            
            if not base_price and strikes:
                # Final fallback: use mid strike
                base_price = strikes[len(strikes) // 2]['strike']
            elif not base_price:
                raise ValueError("Could not determine base price for strike calculation and no strikes found.")
            
            default_ce_strike, default_pe_strike = self._calculate_default_strikes(base_price, symbol)

            default_ce_token: Optional[int] = None
            default_pe_token: Optional[int] = None

            for s in strikes:
                if s['strike'] == default_ce_strike:
                    default_ce_token = s['ce_token']
                if s['strike'] == default_pe_strike:
                    default_pe_token = s['pe_token']

            # Find the At-The-Money (ATM) strike
            if not strikes:
                atm_strike_obj = None
            else:
                 atm_strike_obj = min(strikes, key=lambda x: abs(x['strike'] - base_price))

            atm_index = -1
            if atm_strike_obj:
                 try:
                    # Find index of ATM strike in the original full list
                    atm_index = strikes.index(atm_strike_obj)
                 except ValueError:
                    pass

            # Slice strikes around ATM (15 up, 15 down + ATM = 31 strikes)
            start_index = 0
            end_index = len(strikes)
            if atm_index != -1:
                start_index = max(0, atm_index - 15)
                end_index = min(len(strikes), atm_index + 16)
            
            result_strikes = strikes[start_index:end_index]

            # Add 'is_atm' flag to the ATM strike in the final list
            if atm_strike_obj:
                for s in result_strikes:
                    if s['strike'] == atm_strike_obj['strike']:
                        s['is_atm'] = True
                    else:
                        s['is_atm'] = False
            
            return {'strikes': result_strikes, 'default_ce_token': default_ce_token, 'default_pe_token': default_pe_token}
        
        except Exception as e:
            logging.error(f"Error getting strikes: {e}", exc_info=True)
            raise e
    
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

    def get_chart_data(self, ce_token: int, pe_token: int, timeframe: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Get historical data for CE and PE strikes"""
        try:
            import pytz
            ist = pytz.timezone('Asia/Kolkata')
            
            to_date = datetime.now()
            from_date = to_date - timedelta(days=7)
            
            ce_data = self.kite_service.kite.historical_data(
                instrument_token=int(ce_token),
                from_date=from_date,
                to_date=to_date,
                interval=timeframe
            )
            
            pe_data = self.kite_service.kite.historical_data(
                instrument_token=int(pe_token),
                from_date=from_date,
                to_date=to_date,
                interval=timeframe
            )
            
            # Using list comprehension for cleaner data transformation
            # Kite API returns naive datetime objects (assumed to be IST)
            # We need to make them timezone-aware before converting to timestamp
            ce_formatted = [
                {
                    # Make datetime IST-aware if it's naive, then convert to Unix timestamp
                    'date': int(ist.localize(c['date']).timestamp()) if c['date'].tzinfo is None else int(c['date'].timestamp()),
                    'open': c['open'], 
                    'high': c['high'], 
                    'low': c['low'], 
                    'close': c['close'], 
                    'volume': c['volume']
                } 
                for c in ce_data
            ]
            pe_formatted = [
                {
                    # Make datetime IST-aware if it's naive, then convert to Unix timestamp
                    'date': int(ist.localize(c['date']).timestamp()) if c['date'].tzinfo is None else int(c['date'].timestamp()),
                    'open': c['open'], 
                    'high': c['high'], 
                    'low': c['low'], 
                    'close': c['close'], 
                    'volume': c['volume']
                } 
                for c in pe_data
            ]
            
            return ce_formatted, pe_formatted
        
        except Exception as e:
            logging.error(f"Error getting chart data: {e}", exc_info=True)
            raise e