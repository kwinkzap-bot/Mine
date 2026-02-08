"""Live signal trading module for High-Low strategy with real-time position management."""

import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, time as time_type, date
from typing import Optional, Dict, Any, Tuple

import pandas as pd
import schedule
from dotenv import load_dotenv
from kiteconnect import KiteConnect

from trading_app.app.utils.logger import logger

# Import from local HighLowSignal in same folder
from .HighLowSignal import HighLowSignal

# Import excel logger
from utils.excel_logger import ExcelLogger

# Excel logger will be initialized per user when HighLowLiveSignal is instantiated
excel_logger = None

load_dotenv()


def _initialize_options_chart_service():
    """Initialize OptionsChartService with fallback imports."""
    try:
        from trading_app.service.options_chart_service import OptionsChartService
        return OptionsChartService
    except ImportError:
        try:
            import sys
            current_dir = os.path.dirname(os.path.abspath(__file__))
            service_path = os.path.join(os.path.dirname(os.path.dirname(current_dir)), 'service', 'options_chart_service.py')
            spec_service = importlib.util.spec_from_file_location("OptionsChartService", service_path)
            if not spec_service or not spec_service.loader:
                raise ImportError("Could not load OptionsChartService module")
            
            service_module = importlib.util.module_from_spec(spec_service)
            spec_service.loader.exec_module(service_module)
            OptionsChartService = service_module.OptionsChartService
            return OptionsChartService
        except ImportError as e:
            raise ImportError(f"Could not load OptionsChartService: {e}")


OptionsChartService = _initialize_options_chart_service()

class HighLowLiveSignal:
    """Real-time live trading signal detector for options strategies.
    
    Monitors CE and PE options data, generates buy signals, and manages position exits
    with dynamic stop-loss and trailing stop-loss based on entry price.
    
    Stop Loss Logic (Dynamic based on entry price):
    - Entry Price < 100: SL = 20 points, Trailing = 10 points
    - Entry Price 100-200: SL = 20 points, Trailing = 20 points
    - Entry Price 200-300: SL = 30 points, Trailing = 20 points
    - Entry Price 300-400: SL = 30 points, Trailing = 30 points
    
    Attributes:
        MARKET_OPEN (time_type): Market opening time in IST
        MARKET_CLOSE (time_type): Market closing time in IST
        SIGNAL_CHECK_CUTOFF (time_type): Last time to check for new signals
        ORDER_QUANTITY (int): Default order quantity per trade
        DATA_INTERVAL (str): Kite API interval for data fetching
        NFO_EXCHANGE (str): NSE NFO exchange identifier
    """
    
    # Market timings (IST)
    MARKET_OPEN = datetime.strptime('09:15:00', '%H:%M:%S').time()
    MARKET_CLOSE = datetime.strptime('15:20:00', '%H:%M:%S').time()
    SIGNAL_CHECK_CUTOFF = datetime.strptime('15:25:00', '%H:%M:%S').time()
    
    # Trading parameters (Note: SL and Trailing SL are now calculated dynamically in HighLowSignal.get_sl_and_trailing_points based on entry price)
    ORDER_QUANTITY = 75
    # Dynamic SL calculation based on entry price:
    # - Entry Price < 100: SL = 20, trailing = 10 points
    # - Entry Price 100-200: SL = 20, trailing = 20 points
    # - Entry Price 200-300: SL = 30, trailing = 20 points
    # - Entry Price 300-400: SL = 30, trailing = 30 points
    TRAILING_SL_POINTS = 20  # Deprecated: use HighLowSignal.get_sl_and_trailing_points() instead
    INITIAL_SL_POINTS = 20   # Deprecated: use HighLowSignal.get_sl_and_trailing_points() instead
    
    # API parameters
    DATA_INTERVAL = '5minute'
    NFO_EXCHANGE = 'NFO'
    NIFTY_TOKEN = 256265
    
    def __init__(self, kite_instance: Optional[KiteConnect] = None, symbol: str = 'NIFTY', username: Optional[str] = None):
        """Initialize HighLowLiveSignal instance.
        
        Args:
            kite_instance: Existing KiteConnect instance (optional)
            symbol: Trading symbol (default: 'NIFTY')
            username: Username for Excel logger file naming (optional)
        """
        global excel_logger
        
        self.kite = self._initialize_kite(kite_instance)
        self.symbol = symbol
        self.signal_detector = HighLowSignal()
        
        # Initialize Excel logger with username-specific file
        if not excel_logger or username:
            excel_logger = ExcelLogger(username=username, file_prefix="signal_logs_highlow")
        
        # Initialize KiteService for order placement
        from trading_app.service.kite_order_services import KiteService
        self.kite_service = KiteService(kite_instance=self.kite)
        
        # Cache
        self.instruments: Optional[list] = None
        
        # Position state
        self.active_position: Optional[Dict[str, Any]] = None
        
        # Strike prices
        self.ce_strike: Optional[int] = None
        self.pe_strike: Optional[int] = None
        
        # Previous day OHLC data
        self.ce_prev_high: Optional[float] = None
        self.ce_prev_low: Optional[float] = None
        self.pe_prev_high: Optional[float] = None
        self.pe_prev_low: Optional[float] = None
        
        # Configuration
        self.order_quantity = self.ORDER_QUANTITY
        self.live_trading = True
    
    @staticmethod
    def _initialize_kite(kite_instance: Optional[KiteConnect]) -> KiteConnect:
        """Initialize or return KiteConnect instance.
        
        Args:
            kite_instance: Existing instance or None
            
        Returns:
            KiteConnect instance
        """
        if kite_instance:
            return kite_instance
        
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        
        if not api_key or not access_token:
            raise ValueError("API_KEY and ACCESS_TOKEN must be set in environment")
        
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        return kite
    
    def _is_data_initialized(self) -> bool:
        """Check if all required data is initialized."""
        return all([
            self.ce_strike, self.pe_strike,
            self.ce_prev_high, self.ce_prev_low,
            self.pe_prev_high, self.pe_prev_low
        ])
        
    def get_strike_prices(self, close_price: float) -> Tuple[int, int]:
        """Calculate CE and PE strike prices for given index close.
        
        Args:
            close_price: Index closing price
            
        Returns:
            Tuple of (CE strike, PE strike)
        """
        chart_service = OptionsChartService(self.kite)
        ce_strike, pe_strike = chart_service._calculate_default_strikes(close_price, self.symbol)
        return int(ce_strike), int(pe_strike)
    
    def _filter_instruments_by_params(self, strike: int, option_type: str, 
                                     start_date: datetime) -> list:
        """Filter instruments list by strike, option type, and expiry.
        
        Args:
            strike: Strike price to filter
            option_type: 'CE' or 'PE'
            start_date: Minimum expiry date
            
        Returns:
            List of matching instruments
        """
        matching_instruments = []
        
        if not self.instruments:
            return matching_instruments
        
        for instrument in self.instruments:
            if (instrument['name'] == self.symbol and 
                instrument['instrument_type'] == option_type and
                instrument['strike'] == strike and
                instrument['expiry']):
                
                expiry_date = instrument['expiry']
                if hasattr(expiry_date, 'date'):
                    expiry_date = expiry_date.date()
                
                if expiry_date >= start_date.date():
                    matching_instruments.append(instrument)
        
        return matching_instruments
    
    def get_option_data(self, strike: int, option_type: str, 
                       start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """Fetch option data for given parameters.
        
        Args:
            strike: Strike price
            option_type: 'CE' or 'PE'
            start_date: Start date for data
            end_date: End date for data
            
        Returns:
            DataFrame with OHLC data, empty if fetch fails
        """
        try:
            if self.instruments is None:
                self.instruments = self.kite.instruments(self.NFO_EXCHANGE)
            
            options = self._filter_instruments_by_params(strike, option_type, start_date)
            
            if not options:
                logger.warning(f"No {option_type} instruments found for strike {strike}")
                return pd.DataFrame()
            
            # Get nearest expiry
            options.sort(key=lambda x: x['expiry'])
            option = options[0]
            
            data = self.kite.historical_data(
                instrument_token=option['instrument_token'],
                from_date=start_date,
                to_date=end_date,
                interval=self.DATA_INTERVAL
            )
            
            df = pd.DataFrame(data)
            if not df.empty and 'date' in df.columns:
                df.set_index('date', inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error getting option data for {option_type} {strike}: {e}")
            return pd.DataFrame()
    
    def _get_previous_day_close(self, prev_date: date) -> Optional[float]:
        """Fetch previous day's closing price for index.
        
        Args:
            prev_date: Previous trading date
            
        Returns:
            Closing price or None if fetch fails
        """
        try:
            instrument_tokens = {'NIFTY': self.NIFTY_TOKEN}
            token = instrument_tokens.get(self.symbol)
            
            if not token:
                logger.error(f"No instrument token for {self.symbol}")
                return None
            
            data = self.kite.historical_data(
                instrument_token=token,
                from_date=prev_date,
                to_date=prev_date,
                interval='day'
            )
            
            if not data:
                logger.warning(f"No data for {self.symbol} on {prev_date}")
                return None
            
            return data[0]['close']
            
        except Exception as e:
            logger.error(f"Error fetching previous day close: {e}")
            return None
    
    def _update_strike_data(self, ce_data: pd.DataFrame, pe_data: pd.DataFrame) -> bool:
        """Update previous day high/low for strikes.
        
        Args:
            ce_data: CE option data
            pe_data: PE option data
            
        Returns:
            True if update successful, False otherwise
        """
        if ce_data.empty or pe_data.empty:
            logger.warning("Empty data for CE or PE")
            return False
        
        self.ce_prev_high = ce_data['high'].max()
        self.ce_prev_low = ce_data['low'].min()
        self.pe_prev_high = pe_data['high'].max()
        self.pe_prev_low = pe_data['low'].min()
        
        logger.info(
            f"Initialized: {self.symbol} CE:{self.ce_strike} PE:{self.pe_strike} | "
            f"CE H/L: {self.ce_prev_high:.2f}/{self.ce_prev_low:.2f} | "
            f"PE H/L: {self.pe_prev_high:.2f}/{self.pe_prev_low:.2f}"
        )
        return True
    
    def initialize_daily_data(self) -> bool:
        """Initialize previous day data at market open.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            today = datetime.now().date()
            prev_date = today - timedelta(days=1)
            
            logger.info(f"Initializing data for {today}...")
            
            # Get previous day's index close
            index_close = self._get_previous_day_close(prev_date)
            if index_close is None:
                logger.warning(f"Could not get previous day close for {prev_date}")
                return False
            
            logger.info(f"Previous day close: {index_close}")
            
            # Calculate strike prices
            self.ce_strike, self.pe_strike = self.get_strike_prices(index_close)
            logger.info(f"Strike prices - CE: {self.ce_strike}, PE: {self.pe_strike}")
            
            # Fetch previous day option data
            prev_start = datetime.combine(prev_date, datetime.min.time())
            prev_end = prev_start + timedelta(days=1)
            
            logger.info(f"Fetching previous day data for {prev_date}...")
            ce_prev_data = self.get_option_data(self.ce_strike, 'CE', prev_start, prev_end)
            pe_prev_data = self.get_option_data(self.pe_strike, 'PE', prev_start, prev_end)
            
            result = self._update_strike_data(ce_prev_data, pe_prev_data)
            if result:
                logger.info("✓ Daily data initialization successful")
            else:
                logger.warning("⚠️  Daily data initialization failed")
            return result
            
        except Exception as e:
            logger.error(f"Error initializing daily data: {e}", exc_info=True)
            return False
    
    def _get_current_day_data(self, now: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Fetch current day option data for CE and PE.
        
        Args:
            now: Current datetime
            
        Returns:
            Tuple of (CE data, PE data) DataFrames
        """
        today_start = datetime.combine(now.date(), datetime.min.time())
        
        ce_data = self.get_option_data(self.ce_strike or 0, 'CE', today_start, now)
        pe_data = self.get_option_data(self.pe_strike or 0, 'PE', today_start, now)
        
        return ce_data, pe_data
    
    def _create_position_entry(self, option_type: str, strike: int, 
                              entry_data: Dict[str, Any], order_id: Optional[str],
                              current_data: pd.DataFrame) -> Dict[str, Any]:
        """Create position entry dictionary.
        
        Args:
            option_type: 'CE' or 'PE'
            strike: Strike price
            entry_data: Entry signal data from signal detector
            order_id: Order ID from placement
            current_data: Current option data for trade execution
            
        Returns:
            Position entry dictionary
        """
        return {
            'type': option_type,
            'entry': entry_data,
            'strike': strike,
            'order_id': order_id,
            'data': current_data
        }
    
    def _check_signal(self, option_type: str, ce_data: pd.DataFrame, 
                     pe_data: pd.DataFrame) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check signal for given option type.
        
        Args:
            option_type: 'CE' or 'PE'
            ce_data: CE option data
            pe_data: PE option data
            
        Returns:
            Tuple of (signal_found, entry_dict)
        """
        if option_type == 'CE':
            # Ensure all prev values are not None before calling
            if (self.ce_prev_high is not None and self.ce_prev_low is not None and 
                self.pe_prev_high is not None and self.pe_prev_low is not None):
                return self.signal_detector.check_ce_buy_conditions(
                    ce_data, pe_data, 
                    self.ce_prev_high, self.ce_prev_low, 
                    self.pe_prev_high, self.pe_prev_low
                )
            return False, {}
        else:  # PE
            # Ensure all prev values are not None before calling
            if (self.pe_prev_high is not None and self.pe_prev_low is not None and 
                self.ce_prev_high is not None and self.ce_prev_low is not None):
                return self.signal_detector.check_pe_buy_conditions(
                    pe_data, ce_data, 
                    self.pe_prev_high, self.pe_prev_low, 
                    self.ce_prev_high, self.ce_prev_low
                )
            return False, {}
    
    def check_signals(self) -> None:
        """Check for trading signals every 5 minutes.
        
        Checks both CE and PE buy conditions and places orders if signals found.
        Logs all activity to Excel file for debugging.
        """
        try:
            now = datetime.now()
            
            logger.info(f"🔍 SIGNAL CHECK EXECUTED at {now.strftime('%H:%M:%S')} (Market hours: 9:15-15:25)")
            
            # Skip if market closed
            if now.time() >= self.SIGNAL_CHECK_CUTOFF:
                logger.info(f"⏱️  Market closed - skipping signal check")
                return
            
            # Monitor existing position if any
            if self.active_position:
                logger.debug(f"📊 Monitoring active position: {self.active_position['type']} {self.active_position['strike']}")
                self.monitor_position()
                return
            
            # Check if data is initialized
            if not self._is_data_initialized():
                logger.warning("⚠️  Data not initialized, skipping signal check")
                excel_logger.log_signal_check(
                    timestamp=now,
                    notes="❌ Data not initialized"
                )
                return
            
            # Fetch current data
            ce_data, pe_data = self._get_current_day_data(now)
            
            if ce_data.empty or pe_data.empty:
                logger.warning("⚠️  No current option data available")
                excel_logger.log_signal_check(
                    timestamp=now,
                    ce_prev_high=self.ce_prev_high,
                    ce_prev_low=self.ce_prev_low,
                    pe_prev_high=self.pe_prev_high,
                    pe_prev_low=self.pe_prev_low,
                    notes="❌ No current data available"
                )
                return
            
            # Check CE signal
            ce_signal, ce_entry = self._check_signal('CE', ce_data, pe_data)
            
            if ce_signal and ce_entry and self.ce_strike:
                ce_entry_price = ce_entry['entry_price']
                ce_target = ce_entry.get('target')
                ce_sl = ce_entry.get('stop_loss')
                
                logger.info(f"🎯 CE BUY Signal at {now.strftime('%H:%M:%S')} @ {ce_entry_price:.2f} | Target: {ce_target:.2f} | SL: {ce_sl:.2f}")
                
                order_id = self.place_buy_order('CE', self.ce_strike, ce_entry_price)
                
                # Log to Excel
                excel_logger.log_signal_check(
                    timestamp=now,
                    ce_prev_high=self.ce_prev_high,
                    ce_prev_low=self.ce_prev_low,
                    pe_prev_high=self.pe_prev_high,
                    pe_prev_low=self.pe_prev_low,
                    ce_signal=True,
                    pe_signal=False,
                    ce_entry_price=ce_entry_price,
                    ce_sl=ce_sl,
                    ce_target=ce_target,
                    notes=f"✅ CE Signal | Order ID: {order_id if order_id else 'FAILED'}"
                )
                
                if order_id:
                    self.active_position = self._create_position_entry('CE', self.ce_strike, ce_entry, order_id, ce_data)
                    excel_logger.log_trade(
                        order_type='BUY',
                        option_type='CE',
                        strike=self.ce_strike,
                        entry_price=ce_entry_price,
                        target=ce_target,
                        stop_loss=ce_sl,
                        status='OPEN',
                        order_id=order_id,
                        notes=f"CE Buy signal triggered"
                    )
                return
            
            # Check PE signal only if CE signal not found
            pe_signal, pe_entry = self._check_signal('PE', ce_data, pe_data)
            
            if pe_signal and pe_entry and self.pe_strike:
                pe_entry_price = pe_entry['entry_price']
                pe_target = pe_entry.get('target')
                pe_sl = pe_entry.get('stop_loss')
                
                logger.info(f"🎯 PE BUY Signal at {now.strftime('%H:%M:%S')} @ {pe_entry_price:.2f} | Target: {pe_target:.2f} | SL: {pe_sl:.2f}")
                
                order_id = self.place_buy_order('PE', self.pe_strike, pe_entry_price)
                
                # Log to Excel
                excel_logger.log_signal_check(
                    timestamp=now,
                    ce_prev_high=self.ce_prev_high,
                    ce_prev_low=self.ce_prev_low,
                    pe_prev_high=self.pe_prev_high,
                    pe_prev_low=self.pe_prev_low,
                    ce_signal=False,
                    pe_signal=True,
                    pe_entry_price=pe_entry_price,
                    pe_sl=pe_sl,
                    pe_target=pe_target,
                    notes=f"✅ PE Signal | Order ID: {order_id if order_id else 'FAILED'}"
                )
                
                if order_id:
                    self.active_position = self._create_position_entry('PE', self.pe_strike, pe_entry, order_id, pe_data)
                    excel_logger.log_trade(
                        order_type='BUY',
                        option_type='PE',
                        strike=self.pe_strike,
                        entry_price=pe_entry_price,
                        target=pe_target,
                        stop_loss=pe_sl,
                        status='OPEN',
                        order_id=order_id,
                        notes=f"PE Buy signal triggered"
                    )
                return
            
            # No signals - log to Excel anyway for monitoring
            excel_logger.log_signal_check(
                timestamp=now,
                ce_prev_high=self.ce_prev_high,
                ce_prev_low=self.ce_prev_low,
                pe_prev_high=self.pe_prev_high,
                pe_prev_low=self.pe_prev_low,
                ce_signal=False,
                pe_signal=False,
                notes="✗ No signals"
            )
                
        except Exception as e:
            logger.error(f"❌ Error checking signals: {e}", exc_info=True)
            excel_logger.log_signal_check(
                timestamp=datetime.now(),
                notes=f"❌ Error: {str(e)}"
            )
    
    
    def place_buy_order(self, option_type: str, strike: int, price: float) -> Optional[str]:
        """Place buy order for option.
        
        Args:
            option_type: 'CE' or 'PE'
            strike: Strike price
            price: Entry price
            
        Returns:
            Order ID or None if failed
        """
        logger.info(f"place_buy_order called: {option_type} {strike} @ {price:.2f} (live_trading={self.live_trading})")
        
        if not self.live_trading:
            demo_msg = f"DEMO: BUY {option_type} {strike} @ {price:.2f}"
            logger.info(demo_msg)
            return "DEMO_ORDER"
        
        try:
            result = self.kite_service.place_option_order(
                symbol=self.symbol,
                strike=strike,
                option_type=option_type,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY
                # quantity: None uses dynamic lot size from Kite
            )
            
            if result['success']:
                logger.info(f"✅ BUY Order placed successfully. Order ID: {result['order_id']} | {option_type} {strike} @ {price:.2f}")
                return result['order_id']
            else:
                logger.error(f"❌ BUY Order failed: {result['error']}")
                return None
                
        except Exception as e:
            logger.error(f"Error placing BUY order for {option_type} {strike}: {e}", exc_info=True)
            return None
    
    def place_sell_order(self, option_type: str, strike: int, price: float, exit_reason: str = "Manual Exit") -> Optional[str]:
        """Place sell order for option.
        
        Args:
            option_type: 'CE' or 'PE'
            strike: Strike price
            price: Exit price
            exit_reason: Reason for exit (Stop Loss, Target, Market Close, etc.)
            
        Returns:
            Order ID or None if failed
        """
        logger.info(f"place_sell_order called: {option_type} {strike} @ {price:.2f} | Reason: {exit_reason} (live_trading={self.live_trading})")
        
        if not self.live_trading:
            demo_msg = f"DEMO: SELL {option_type} {strike} @ {price:.2f} | {exit_reason}"
            logger.info(demo_msg)
            return "DEMO_ORDER"
        
        try:
            result = self.kite_service.place_option_order(
                symbol=self.symbol,
                strike=strike,
                option_type=option_type,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL
                # quantity: None uses dynamic lot size from Kite
            )
            
            if result['success']:
                logger.info(f"✅ SELL Order placed successfully. Order ID: {result['order_id']} | {option_type} {strike} @ {price:.2f} | {exit_reason}")
                return result['order_id']
            else:
                logger.error(f"❌ SELL Order failed: {result['error']} ({exit_reason})")
                return None
                
        except Exception as e:
            logger.error(f"Error placing SELL order for {option_type} {strike}: {e}", exc_info=True)
            return None
    
    def is_market_close_time(self, current_time: time_type) -> bool:
        """Check if current time is at market close (3:20 PM IST).
        
        Args:
            current_time: Time to check
            
        Returns:
            True if at or past market close
        """
        return current_time >= self.MARKET_CLOSE
    
    def monitor_position(self) -> None:
        """Monitor active position for exit.
        
        Uses execute_trade from HighLowSignal for comprehensive exit logic:
        - Market close (3:20 PM) - Always exit
        - Stop loss hit (using LOW of candle)
        - Target hit (using HIGH of candle)
        - Trailing SL: Every 20 points of profit, trail SL by 20 points
        """
        if not self.active_position:
            logger.debug("No active position to monitor")
            return
        
        now = datetime.now()
        option_type = self.active_position['type']
        strike = self.active_position['strike']
        entry_signal = self.active_position['entry']
        entry_price = entry_signal['entry_price']
        
        logger.debug(f"📊 Monitoring {option_type} {strike} position. Entry: {entry_price:.2f}")
        
        # Get latest option data
        today_start = datetime.combine(now.date(), datetime.min.time())
        current_data = self.get_option_data(strike, option_type, today_start, now)
        
        if current_data.empty:
            logger.warning(f"⚠️  No data available for {option_type} {strike}")
            return
        
        # Use HighLowSignal's execute_trade for comprehensive exit logic
        exit_info = self.signal_detector.execute_trade(entry_signal, current_data)
        
        if exit_info:
            exit_price = exit_info['exit_price']
            exit_reason = exit_info['exit_reason']
            pnl = exit_info['pnl']
            
            logger.info(
                f"🔔 {option_type} POSITION EXIT TRIGGERED: {exit_reason} @ {exit_price:.2f} | "
                f"Entry: {entry_price:.2f} | PnL: {pnl:+.2f}"
            )
            
            # Log exit to Excel
            excel_logger.log_trade(
                order_type='SELL',
                option_type=option_type,
                strike=strike,
                entry_price=entry_price,
                current_price=exit_price,
                pnl=pnl,
                status=exit_reason,
                notes=f"{exit_reason} - PnL: {pnl:+.2f}"
            )
            
            # Place sell order at exit price with exit reason
            sell_order_id = self.place_sell_order(option_type, strike, exit_price, exit_reason)
            
            # Only clear position if sell order was placed successfully
            if sell_order_id:
                logger.info(f"✅ Position closed successfully. Order ID: {sell_order_id}")
                excel_logger.log_trade(
                    order_type='SELL',
                    option_type=option_type,
                    strike=strike,
                    entry_price=entry_price,
                    current_price=exit_price,
                    pnl=pnl,
                    status='CLOSED',
                    order_id=sell_order_id,
                    notes=f"✅ Closed by {exit_reason}"
                )
                self.active_position = None
            else:
                logger.error(f"❌ Failed to place exit/close order for {option_type} {strike}. Position NOT closed.")
                # Position remains active for retry
        else:
            logger.debug(f"No exit condition met for {option_type} {strike}")
    
    def _schedule_signal_checks(self) -> None:
        """Schedule signal checks at 5-minute intervals during market hours."""
        logger.info("Scheduling signal checks at 5-minute intervals...")
        job_count = 0
        
        for hour in range(9, 16):
            for minute in [20, 25, 30, 35, 40, 45, 50, 55]:
                if hour == 15 and minute > 25:  # Stop at 3:25 PM
                    break
                
                time_str = f"{hour:02d}:{minute:02d}"
                job = schedule.every().day.at(time_str).do(self.check_signals)
                job_count += 1
                logger.info(f"  Scheduled: {time_str} → check_signals()")
        
        logger.info(f"✓ Total {job_count} signal check jobs scheduled")
    
    def _run_monitoring_loop(self) -> None:
        """Run the main monitoring loop.
        
        Continues until KeyboardInterrupt or exception that breaks the loop.
        """
        logger.info("Live monitoring loop started")
        logger.info(f"Scheduled jobs: {len(schedule.jobs)}")
        for job in schedule.jobs:
            logger.info(f"  ⏱️  {job}")
        
        loop_count = 0
        last_log_time = datetime.now()
        
        while True:
            try:
                loop_count += 1
                
                # Log status every 60 iterations (60 seconds)
                now = datetime.now()
                if (now - last_log_time).total_seconds() >= 60:
                    logger.info(f"⏱️  Monitoring active... Loop count: {loop_count}, Scheduled jobs: {len(schedule.jobs)}")
                    last_log_time = now
                
                # Check for pending jobs
                schedule.run_pending()
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Stopping live monitoring...")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                time.sleep(5)  # Wait before retrying
    
    def start_live_monitoring(self) -> None:
        """Start live signal monitoring.
        
        Initializes data at market open, schedules signal checks every 5 minutes,
        and runs the main monitoring loop until interrupted.
        """
        try:
            logger.info(f"Starting live monitoring for {self.symbol}")
            
            # Schedule initialization at market open (non-blocking)
            schedule.every().day.at("09:15").do(self.initialize_daily_data)
            logger.info("Scheduled daily initialization at 09:15 AM")
            
            # Try to initialize immediately if during market hours (in background)
            now = datetime.now()
            if (self.MARKET_OPEN <= now.time() <= 
                datetime.strptime('15:30:00', '%H:%M:%S').time()):
                logger.info("Market hours detected - scheduling immediate initialization in background...")
                import threading
                init_thread = threading.Thread(target=self.initialize_daily_data, daemon=True)
                init_thread.daemon = True  # Don't block monitoring loop
                init_thread.start()
                logger.info("Background initialization started")
            
            # Schedule signal checks
            self._schedule_signal_checks()
            
            logger.info("✓ Live monitoring scheduled - starting monitoring loop...")
            
            # Run monitoring loop (non-blocking initialization)
            self._run_monitoring_loop()
            
        except Exception as e:
            logger.error(f"Error starting live monitoring: {e}")
