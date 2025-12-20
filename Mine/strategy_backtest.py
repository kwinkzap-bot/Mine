import pandas as pd
from datetime import datetime, timedelta, date
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException
import json
import os
import sys
from dotenv import load_dotenv
import logging
from typing import List, Dict, Tuple, Optional, Any
import numpy as np

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Custom JSON encoder to handle numpy and pandas types
class NumpyEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        elif isinstance(o, np.floating):
            return float(o)
        elif isinstance(o, np.ndarray):
            return o.tolist()
        elif isinstance(o, pd.Timestamp):
            return o.isoformat()
        return super().default(o)

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from strategy.HighLowSignal import HighLowSignal
    from service.options_chart_service import OptionsChartService
except ImportError:
    # Fallback for direct execution
    import importlib.util
    spec = importlib.util.spec_from_file_location("HighLowSignal", os.path.join(os.path.dirname(__file__), "strategy", "HighLowSignal.py"))
    if spec and spec.loader:
        HighLowSignal_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(HighLowSignal_module)
        HighLowSignal = HighLowSignal_module.HighLowSignal
    else:
        raise ImportError("Could not load HighLowSignal module")
    
    spec_service = importlib.util.spec_from_file_location("OptionsChartService", os.path.join(os.path.dirname(__file__), "service", "options_chart_service.py"))
    if spec_service and spec_service.loader:
        service_module = importlib.util.module_from_spec(spec_service)
        spec_service.loader.exec_module(service_module)
        OptionsChartService = service_module.OptionsChartService

# Load environment variables
load_dotenv(override=True)

class OptionsStrategy:
    # --- Constants ---
    # Intervals
    INTERVAL_5MINUTE = '5minute'
    INTERVAL_DAY = 'day'

    # Instrument Tokens
    INDEX_INSTRUMENT_TOKENS = {
        'NIFTY': 256265
    }

    # NIFTY Strike Price Configuration
    NIFTY_ROUNDING_UNIT = 50
    NIFTY_STRIKE_OFFSET = 150  # Default offset for ATM strikes

    # Market Holidays (can be dynamically fetched or updated)
    HOLIDAYS_2024 = [
        '2024-01-26', '2024-03-08', '2024-03-25', '2024-04-11', '2024-04-17',
        '2024-05-01', '2024-06-17', '2024-08-15', '2024-10-02', '2024-11-01',
        '2024-11-15', '2024-12-25'
    ]
    
    HOLIDAYS_2025 = [
        '2025-01-26', '2025-03-14', '2025-03-31', '2025-04-10', '2025-04-14',
        '2025-05-01', '2025-06-06', '2025-08-15', '2025-10-02', '2025-10-31',
        '2025-11-04', '2025-12-25'
    ]

    def __init__(self, kite_instance: Optional[KiteConnect] = None, api_key: Optional[str] = None):
        if kite_instance:
            self.kite = kite_instance
            logger.info("OptionsStrategy: Using existing KiteConnect instance.")
        else:
            # Create new instance with credentials if no existing instance is provided
            self.api_key = api_key or os.getenv("API_KEY")
            self.access_token = os.getenv("ACCESS_TOKEN") # Fetch from environment variable
            
            if not self.api_key:
                raise ValueError("API_KEY not provided or set as environment variable for OptionsStrategy")
            
            self.kite = KiteConnect(api_key=self.api_key)
            if self.access_token and isinstance(self.access_token, str) and self.access_token.strip():
                self.kite.set_access_token(self.access_token)
                logger.info("OptionsStrategy: Created new KiteConnect instance with access token.")
            else:
                logger.info("OptionsStrategy: Created new KiteConnect instance without access token (running unauthenticated or access token is empty).")
        self.trades: List[float] = []
        self.entry_exit_log: List[Dict[str, Any]] = []
        # self.stop_loss_level = None # Removed redundant attribute
        self.instruments = None  # Cache for instruments data
        self.signal_detector = HighLowSignal()
        
    def is_market_holiday(self, date_obj: date) -> bool:
        """Check if date is a market holiday (weekends or Indian holidays)"""
        # Skip weekends
        if date_obj.weekday() >= 5:  # Saturday=5, Sunday=6
            return True
            
        date_str = date_obj.strftime('%Y-%m-%d')
        return date_str in self._get_market_holidays()

    def _get_market_holidays(self) -> List[str]:
        """Returns a combined list of market holidays."""
        return self.HOLIDAYS_2024 + self.HOLIDAYS_2025
                
    # Removed unused _calculate_nifty_finnifty_strike
    
    def get_strike_prices(self, close_price: float, symbol: str = 'NIFTY') -> Tuple[int, int]:
        """Calculate strike prices using OptionsChartService._calculate_default_strikes"""
        # Use the centralized strike calculation from OptionsChartService
        chart_service = OptionsChartService(self.kite)
        ce_strike, pe_strike = chart_service._calculate_default_strikes(close_price, symbol)
        return int(ce_strike), int(pe_strike)
    
    def get_option_data(self, symbol: str, strike: int, option_type: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """Get option data for given parameters"""
        
        try:
            # Load instruments only once and cache
            if self.instruments is None:
                logger.info("Loading NFO instruments (one-time operation)...")
                self.instruments = self.kite.instruments('NFO')
                logger.info(f"Loaded {len(self.instruments)} instruments")
            
            # Find current expiry from Zerodha instruments
            options = []
            for instrument in self.instruments:
                if (instrument['name'] == symbol and 
                    instrument['instrument_type'] == option_type and
                    instrument['strike'] == strike and
                    instrument['expiry']):
                    # Handle both datetime.date and datetime.datetime types
                    expiry_date = instrument['expiry']
                    if hasattr(expiry_date, 'date'):
                        expiry_date = expiry_date.date()
                    if expiry_date >= start_date.date():
                        options.append(instrument)
            
            if not options:
                logger.debug(f"No {option_type} options found for {symbol} strike {strike} on or after {start_date.strftime('%Y-%m-%d')}")
                return pd.DataFrame()
            
            # Get nearest expiry
            options.sort(key=lambda x: x['expiry'])
            option = options[0]
            logger.debug(f"Found: {option['tradingsymbol']} (Expiry: {option['expiry'].strftime('%Y-%m-%d')}) - Token: {option['instrument_token']}")
            
            # If same day, add time range (9:00 AM to 4:00 PM IST for intraday data)
            fetch_start_date = start_date
            fetch_end_date = end_date
            if start_date.date() == end_date.date():
                fetch_start_date = start_date.replace(hour=9, minute=0, second=0, microsecond=0)
                fetch_end_date = end_date.replace(hour=16, minute=0, second=0, microsecond=0)
                logger.debug(f"Same-day fetch: expanding time to {fetch_start_date.strftime('%H:%M')} - {fetch_end_date.strftime('%H:%M')}")
            
            data = self.kite.historical_data(
                instrument_token=option['instrument_token'],
                from_date=fetch_start_date,
                to_date=fetch_end_date,
                interval=self.INTERVAL_5MINUTE
            )
            df = pd.DataFrame(data)
            if not df.empty and 'date' in df.columns:
                df.set_index('date', inplace=True)
                logger.debug(f"Got {len(df)} candles for {option['tradingsymbol']}")
            else:
                logger.debug(f"No historical data returned for {option['tradingsymbol']} between {start_date.strftime('%Y-%m-%d')} and {end_date.strftime('%Y-%m-%d')}")
            return df
        except (KiteException, Exception) as e:
            logger.error(f"Error getting option data for {symbol} {option_type} strike {strike} on {start_date.strftime('%Y-%m-%d')}: {e}")
            return pd.DataFrame()
    

    
    def _fetch_index_data(self, symbol: str, date: datetime) -> Optional[pd.DataFrame]:
        """Fetches historical data for the given index symbol and date."""
        instrument_tokens = self.INDEX_INSTRUMENT_TOKENS
        if symbol not in instrument_tokens:
            logger.error(f"Unsupported symbol for index data fetch: {symbol}")
            return None
        
        logger.info(f"Getting {symbol} data for {date.strftime('%Y-%m-%d')}...")
        try:
            index_data_raw = self.kite.historical_data(
                instrument_token=instrument_tokens[symbol],
                from_date=date,
                to_date=date,
                interval=self.INTERVAL_DAY
            )
            index_data = pd.DataFrame(index_data_raw)
            if index_data.empty:
                logger.info(f"No {symbol} data found for {date.strftime('%Y-%m-%d')}.")
                return None
            return index_data
        except KiteException as e:
            logger.error(f"Kite API error fetching index data for {symbol} on {date.strftime('%Y-%m-%d')}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching index data for {symbol} on {date.strftime('%Y-%m-%d')}: {e}")
            return None

    def _check_entry_signals(self, ce_current_data: pd.DataFrame, pe_current_data: pd.DataFrame,
                             ce_prev_high: float, ce_prev_low: float, 
                             pe_prev_high: float, pe_prev_low: float) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Check for CE and PE entry signals.
        Returns: (signal_type, entry_dict) where signal_type is 'CE', 'PE', or 'NONE'
        """
        # Check CE buy conditions
        ce_signal_found, ce_entry = self.signal_detector.check_ce_buy_conditions(
            ce_current_data, pe_current_data, ce_prev_high, ce_prev_low, pe_prev_high, pe_prev_low
        )
        
        if ce_signal_found and ce_entry:
            return 'CE', ce_entry
        
        # Check PE buy conditions only if CE signal not found
        pe_signal_found, pe_entry = self.signal_detector.check_pe_buy_conditions(
            pe_current_data, ce_current_data, pe_prev_high, pe_prev_low, ce_prev_high, ce_prev_low
        )
        
        if pe_signal_found and pe_entry:
            return 'PE', pe_entry
        
        return 'NONE', None
    
    def _manage_trade_exit(self, signal_type: str, entry_signal: Dict[str, Any], 
                          ce_current_data: pd.DataFrame, pe_current_data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Manage trade exit by checking target and stop loss conditions.
        Returns exit_info dict if trade exits, None otherwise
        """
        if signal_type == 'CE':
            exit_info = self.signal_detector.execute_trade(entry_signal, ce_current_data)
        elif signal_type == 'PE':
            exit_info = self.signal_detector.execute_trade(entry_signal, pe_current_data)
        else:
            return None
        
        return exit_info
    
    def _get_trade_stop_loss(self) -> Optional[float]:
        """Get the current trade's stop loss level from signal detector"""
        return self.signal_detector.stop_loss_level

    def _fetch_option_data_for_strikes(self, symbol: str, ce_strike: int, pe_strike: int, date: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Fetches CE and PE option data for given strikes and date.
        Returns a tuple of (ce_data_df, pe_data_df). Empty DataFrames if no data.
        """
        ce_data = self.get_option_data(symbol, ce_strike, 'CE', date, date)
        pe_data = self.get_option_data(symbol, pe_strike, 'PE', date, date)
        return ce_data, pe_data

    def backtest_strategy(self, start_date: datetime, end_date: datetime, symbol: str = 'NIFTY'):
        """Run backtest for the strategy"""
        # Validate dates are not in future
        today = datetime.now().date()
        if start_date.date() > today or end_date.date() > today:
            raise ValueError(f"Cannot backtest future dates. Today is {today}")
        
        # Test API connection first
        try:
            self.kite.profile()
            logger.info("API connection verified successfully")
        except Exception as e:
            raise ValueError(f"API authentication failed: {e}. Please check your API_KEY and ACCESS_TOKEN")
        
        current_date = start_date
        
        while current_date <= end_date:
            # Skip market holidays
            if self.is_market_holiday(current_date.date()):
                logger.info(f"\n--- Skipping {current_date.strftime('%Y-%m-%d')} (Market Holiday) ---")
                current_date += timedelta(days=1)
                continue
                
            logger.info(f"\n--- Processing {current_date.strftime('%Y-%m-%d')} ---")
            try:
                # Get previous working day (skip holidays)
                prev_date = current_date - timedelta(days=1)
                while self.is_market_holiday(prev_date.date()):
                    prev_date = prev_date - timedelta(days=1)
                
                index_data = self._fetch_index_data(symbol, prev_date)
                if index_data is None:
                    current_date += timedelta(days=1)
                    continue
                
                index_close = index_data['close'].iloc[-1]
                ce_strike, pe_strike = self.get_strike_prices(index_close, symbol)
                logger.info(f"{symbol} Close: {index_close:.2f}, CE Strike: {ce_strike}, PE Strike: {pe_strike}")
                
                # Get previous day option data
                logger.info(f"Fetching previous day ({prev_date.strftime('%Y-%m-%d')}) option data for CE strike {ce_strike} and PE strike {pe_strike}...")
                ce_prev_data, pe_prev_data = self._fetch_option_data_for_strikes(symbol, ce_strike, pe_strike, prev_date)
                logger.debug(f"CE prev data: {len(ce_prev_data)} rows, PE prev data: {len(pe_prev_data)} rows")
                if ce_prev_data.empty or pe_prev_data.empty:
                    logger.warning(f"No previous day option data found for {symbol} on {prev_date.strftime('%Y-%m-%d')}. Skipping...")
                    current_date += timedelta(days=1)
                    continue
                
                ce_prev_high = ce_prev_data['high'].max()
                ce_prev_low = ce_prev_data['low'].min()
                pe_prev_high = pe_prev_data['high'].max()
                pe_prev_low = pe_prev_data['low'].min()
                logger.info(f"CE Prev: H={ce_prev_high:.2f}, L={ce_prev_low:.2f} | PE Prev: H={pe_prev_high:.2f}, L={pe_prev_low:.2f}")

                ce_current_data, pe_current_data = self._fetch_option_data_for_strikes(symbol, ce_strike, pe_strike, current_date)
                logger.debug(f"CE current data: {len(ce_current_data)} rows, PE current data: {len(pe_current_data)} rows")
                if ce_current_data.empty or pe_current_data.empty:
                    logger.warning(f"No current day option data found for {symbol} on {current_date.strftime('%Y-%m-%d')}. Skipping...")
                    current_date += timedelta(days=1)
                    continue
                
                logger.info(f"Current day data: CE has {len(ce_current_data)} candles, PE has {len(pe_current_data)} candles")
                
                # Check entry signals
                signal_type, entry_signal = self._check_entry_signals(
                    ce_current_data, pe_current_data, ce_prev_high, ce_prev_low, pe_prev_high, pe_prev_low
                )
                
                if signal_type == 'NONE' or entry_signal is None:
                    logger.info("No entry signals found")
                    self.log_no_signal_day(current_date, index_close, ce_strike, pe_strike, ce_prev_high, ce_prev_low, pe_prev_high, pe_prev_low)
                else:
                    # Manage trade exit (target and stop loss)
                    logger.info(f"{signal_type} BUY Signal found at {entry_signal['entry_time']} @ {entry_signal['entry_price']:.2f}")
                    exit_info = self._manage_trade_exit(signal_type, entry_signal, ce_current_data, pe_current_data)
                    
                    if exit_info:
                        logger.info(f"{signal_type} EXIT: {exit_info['exit_reason']} at {exit_info['exit_time']} @ {exit_info['exit_price']:.2f}, PnL: {exit_info['pnl']:.2f}")
                        
                        # Determine which strike to use for logging
                        trade_strike = ce_strike if signal_type == 'CE' else pe_strike
                        stop_loss = self._get_trade_stop_loss()
                        
                        self.log_trade(current_date, symbol, trade_strike, signal_type, entry_signal, exit_info, 
                                     index_close, ce_strike, pe_strike, f'BUY {signal_type}', 
                                     ce_prev_high, ce_prev_low, pe_prev_high, pe_prev_low, stop_loss)
                    else:
                        logger.warning(f"{signal_type} exit failed or returned None")
                
                # Reset the signal detector's stop loss level for the next day.
                self.signal_detector.stop_loss_level = None 
                
                
            except Exception as e:
                logger.error(f"Error on {current_date.strftime('%Y-%m-%d')}: {e}")
            
            current_date += timedelta(days=1)
    
    def log_trade(self, date: datetime, symbol: str, strike: int, option_type: str, entry: Dict[str, Any], exit_info: Dict[str, Any], 
                  prev_day_close: float, ce_strike: int, pe_strike: int, signal: str, 
                  ce_prev_high: float, ce_prev_low: float, pe_prev_high: float, pe_prev_low: float, 
                  stop_loss: Optional[float]):
        """Log trade details"""
        # Generate option symbol
        year = date.strftime('%y')
        month = date.strftime('%b').upper()
        # The option symbol generation should be more robust and perhaps use instrument data
        # For now, keeping a simplified version
        option_symbol = f"{symbol.upper()}{year}{month}{strike}{option_type}" # Use dynamic symbol
        
        # Get expiry date from instruments data
        expiry_date = 'N/A'
        if self.instruments:
            for instrument in self.instruments:
                if (
                    instrument['name'] == symbol and # Use dynamic symbol here
                    instrument['instrument_type'] == option_type and
                    instrument['strike'] == strike and
                    instrument['expiry']):
                    expiry_date = instrument['expiry'].strftime('%Y-%m-%d')
                    break
        
        # Convert all numpy types to native Python types
        trade = {
            'date': date.strftime('%Y-%m-%d'),
            'nifty_close': float(prev_day_close),
            'ce_strike': int(ce_strike),
            'pe_strike': int(pe_strike),
            'ce_prev_high': float(ce_prev_high),
            'ce_prev_low': float(ce_prev_low),
            'pe_prev_high': float(pe_prev_high),
            'pe_prev_low': float(pe_prev_low),
            'strike': int(strike),
            'option_type': option_type,
            'option_symbol': option_symbol,
            'expiry_date': expiry_date,
            'signal': signal,
            'entry_time': entry['entry_time'].strftime('%H:%M:%S'),
            'buy_price': float(entry['entry_price']),
            'exit_time': exit_info['exit_time'].strftime('%H:%M:%S'),
            'exit_price': float(exit_info['exit_price']),
            'exit_reason': exit_info['exit_reason'],
            'pnl': float(round(exit_info['pnl'], 2)),
            'target': float(entry['target']) if entry['target'] else None,
            'stop_loss': float(stop_loss) if stop_loss else None
        } # FIX: Added missing closing brace
        
        self.entry_exit_log.append(trade)
        self.trades.append(float(round(exit_info['pnl'], 2)))
    
    def log_no_signal_day(self, date: datetime, prev_day_close: float, ce_strike: int, pe_strike: int, 
                          ce_prev_high: float, ce_prev_low: float, pe_prev_high: float, pe_prev_low: float):
        """Log days when no trading signals are found"""
        no_signal_entry = {
            'date': date.strftime('%Y-%m-%d'),
            'nifty_close': float(prev_day_close),
            'ce_strike': int(ce_strike),
            'pe_strike': int(pe_strike),
            'ce_prev_high': float(ce_prev_high),
            'ce_prev_low': float(ce_prev_low),
            'pe_prev_high': float(pe_prev_high),
            'pe_prev_low': float(pe_prev_low),
            'strike': 0,
            'option_type': 'N/A',
            'option_symbol': 'N/A',
            'expiry_date': 'N/A',
            'signal': 'NO SIGNAL',
            'entry_time': 'N/A',
            'buy_price': 0.0,
            'exit_time': 'N/A',
            'exit_price': 0.0,
            'exit_reason': 'N/A',
            'pnl': 0.0,
            'target': 0.0,
            'stop_loss': 0.0
        }
        self.entry_exit_log.append(no_signal_entry)
    
    def get_results(self) -> Dict[str, float]:
        """Get backtest results"""
        if not self.trades:
            return {
                'total_trades': 0,
                'target_trades': 0,
                'loss_trades': 0,
                'total_points': 0,
                'win_rate': 0,
                'avg_pnl': 0
            }
        
        total_trades = len(self.trades)
        target_trades = len([t for t in self.entry_exit_log if t['exit_reason'] == 'Target'])
        loss_trades = len([t for t in self.entry_exit_log if t['pnl'] < 0])
        total_points = sum(self.trades)
        
        return {
            'total_trades': total_trades,
            'target_trades': target_trades,
            'loss_trades': loss_trades,
            'total_points': round(total_points, 2),
            'win_rate': round((target_trades / total_trades) * 100, 2) if total_trades > 0 else 0,
            'avg_pnl': round(total_points / total_trades, 2) if total_trades > 0 else 0
        }
    
    def save_results(self, filename: str = 'backtest_results.json'):
        """Save results to file"""
        # Convert entry_exit_log to ensure all numpy types are converted
        trades_serializable = []
        for trade in self.entry_exit_log:
            trade_copy = {}
            for key, value in trade.items():
                if isinstance(value, np.integer):
                    trade_copy[key] = int(value)
                elif isinstance(value, np.floating):
                    trade_copy[key] = float(value)
                elif isinstance(value, (pd.Timestamp, datetime)):
                    trade_copy[key] = str(value)
                else:
                    trade_copy[key] = value
            trades_serializable.append(trade_copy)
        
        results = {
            'summary': self.get_results(),
            'trades': trades_serializable
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, cls=NumpyEncoder)
        logger.info(f"✅ Results saved to {filename}")

if __name__ == '__main__':
    logger.info("Debug: Starting script execution")
    strategy = OptionsStrategy()
    
    # Backtest for November 21, 2024 only (using valid historical date)
    start_date = datetime(2024, 11, 21)
    end_date = datetime(2024, 11, 28)  # One week range as required
    
    logger.info(f"Starting backtest for {start_date.strftime('%Y-%m-%d')}...")
    strategy.backtest_strategy(start_date, end_date)
    
    results = strategy.get_results()
    logger.info("\nBacktest Results:")
    logger.info(f"Total Trades: {results['total_trades']}")
    logger.info(f"Target Trades: {results['target_trades']}")
    logger.info(f"Loss Trades: {results['loss_trades']}")
    logger.info(f"Total Points: {results['total_points']}")
    logger.info(f"Win Rate: {results['win_rate']}%")
    logger.info(f"Average PnL: {results['avg_pnl']}")
    
    strategy.save_results()
    logger.info("\nResults saved to backtest_results.json")