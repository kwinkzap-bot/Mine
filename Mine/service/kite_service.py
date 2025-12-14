import logging
from kiteconnect import KiteConnect
import pandas as pd
from datetime import datetime
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional # Added typing imports
import re # FIX: Moved 'import re' to the top for style and efficiency

load_dotenv()

class KiteService:
    def __init__(self, kite_instance: Optional[KiteConnect] = None) -> None:
        """
        Initializes the KiteService.
        """
        self.kite: KiteConnect = kite_instance or self._create_kite_instance()
        self.instruments: Optional[List[Dict[str, Any]]] = None
        self._instrument_tokens_by_symbol: Dict[str, int] = {}
        self._instrument_tokens_by_name: Dict[str, int] = {}
        if self.instruments is None:
            self._load_instruments()
    
    def _load_instruments(self):
        """Loads and processes instruments into lookup dictionaries. Added try/except for robustness."""
        try:
            self.instruments = self.kite.instruments('NSE')
            for instrument in self.instruments:
                symbol = instrument.get('tradingsymbol')
                name = instrument.get('name')
                token = instrument.get('instrument_token')
                if symbol and token:
                    self._instrument_tokens_by_symbol[symbol] = token
                if name and token:
                    self._instrument_tokens_by_name[name.lower()] = token
        except Exception as e:
            logging.error(f"Error loading instruments: {e}")
    

    
    def _create_kite_instance(self) -> KiteConnect:
        """Creates and configures the KiteConnect instance."""
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        kite = KiteConnect(api_key=api_key)
        
        if access_token and isinstance(access_token, str) and access_token.strip():
            kite.set_access_token(access_token)
        else:
            logging.error("ACCESS_TOKEN not found or empty. Kite access may be restricted.")
            
        return kite
    
    def get_instrument_token(self, symbol: str) -> Optional[int]:
        """Get instrument token for NSE equity or indices, including FINNIFTY."""
        try:
            token = self._instrument_tokens_by_symbol.get(symbol)
            if token:
                return token

            # Improved index lookup including FINNIFTY
            if symbol in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
                search_name = symbol.lower().replace('nifty', 'nifty ').strip()
                if symbol == 'NIFTY': search_name = 'nifty 50'
                elif symbol == 'BANKNIFTY': search_name = 'nifty bank'
                elif symbol == 'FINNIFTY': search_name = 'nifty fin service'
                
                token = self._instrument_tokens_by_name.get(search_name)
                if token:
                    return token
            
            logging.warning(f"No instrument found for {symbol}")
            return None
        except Exception as e:
            logging.error(f"Error getting instrument token for {symbol}: {e}")
            return None
    
    def get_fo_stocks(self) -> List[str]:
        """Get list of F&O underlying stocks, including FUTURES and OPTIONS."""
        try:
            nfo_instruments = self.kite.instruments('NFO')
            fo_symbols = set()
            
            for inst in nfo_instruments:
                # FIX: Check for both FUTURES and OPTIONS for comprehensive underlying list
                if inst.get('instrument_type') in ['FUT', 'OPT']: 
                    tsymbol = inst.get('tradingsymbol', '')
                    if tsymbol:
                        # 're' is now imported at the top
                        match = re.match(r'^([A-Z]+)', tsymbol) 
                        if match and len(match.group(1)) > 1:
                            fo_symbols.add(match.group(1))
            
            fo_list = sorted(list(fo_symbols))
            
            # Ensure indices are at the top and avoid duplicates
            indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY'] # FINNIFTY added
            result = [s for s in indices if s in fo_list or s in indices] 
            for symbol in fo_list:
                if symbol not in result:
                    result.append(symbol)
            
            return result

        except Exception as e:
            logging.error(f"Error getting F&O stocks: {e}")
            return []
    
    def get_historical_data(self, symbol: str, from_date: datetime, to_date: datetime, interval: str = 'day') -> Optional[pd.DataFrame]:
        """Fetches historical data, ensuring 'date' column is timezone-naive datetime."""
        try:
            token = self.get_instrument_token(symbol)
            logging.debug(f"Token for {symbol}: {token}")
            if not token:
                return None
            
            # Removed redundant datetime conversion checks since type hints suggest datetime objects
            # Assuming callers pass datetime objects, or adding the check back if needed:
            if isinstance(from_date, str):
                from_date = datetime.strptime(from_date, '%Y-%m-%d')
            if isinstance(to_date, str):
                to_date = datetime.strptime(to_date, '%Y-%m-%d')
            
            data = self.kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
            
            if data:
                df = pd.DataFrame(data)
                # FIX: Ensure 'date' column is a timezone-naive datetime for consistency
                if 'date' in df.columns:
                     df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                return df
            return None
        except Exception as e:
            logging.error(f"Error fetching data for {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None