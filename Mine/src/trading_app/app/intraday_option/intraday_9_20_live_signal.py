"""
Intraday 9:20 Strategy - Live Signal Monitoring
Monitors entry signals every 30 seconds during market hours (9:15 AM - 3:30 PM IST)
"""

from datetime import datetime, timedelta, time
from typing import Dict, List, Any, Optional
import logging
import threading
import time as time_module
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from .intraday_9_20 import Intraday920Strategy
from ...service.kite_order_services import KiteService

# Add utils to path for excel_logger
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from utils.excel_logger import excel_logger

logger = logging.getLogger(__name__)


def log_order_placement(order_data: Dict[str, Any]) -> None:
    """
    Log order placement details to Excel file.
    
    Args:
        order_data: Dictionary containing order details
    """
    try:
        # Determine order type and status for Excel logging
        side = order_data.get('side', 'N/A')
        status = order_data.get('status', 'N/A')
        
        # Map status to Excel logger status
        excel_status = status
        if 'SUCCESS' in status.upper():
            excel_status = side  # BUY or SELL
        elif 'FAILED' in status.upper() or 'ERROR' in status.upper():
            excel_status = 'FAILED'
        elif 'DEMO' in order_data.get('mode', ''):
            excel_status = f'{side}_DEMO'
        
        # Prepare notes with additional details
        notes = []
        if order_data.get('mode'):
            notes.append(f"Mode: {order_data['mode']}")
        if order_data.get('error'):
            notes.append(f"Error: {order_data['error']}")
        if order_data.get('details'):
            notes.append(f"Details: {order_data['details']}")
        if order_data.get('order_type'):
            notes.append(f"Type: {order_data['order_type']}")
        
        notes_str = " | ".join(notes) if notes else ""
        
        # Extract option type from side (side contains 'BUY CE', 'SELL PE', etc.)
        option_type = 'N/A'
        if 'CE' in str(side):
            option_type = 'CE'
        elif 'PE' in str(side):
            option_type = 'PE'
        
        # Get strike as integer
        strike_val = order_data.get('strike', 0)
        try:
            strike = int(strike_val) if strike_val else 0
        except (ValueError, TypeError):
            strike = 0
        
        # Get entry price as float
        entry_price_val = order_data.get('entry_price', 0)
        try:
            if isinstance(entry_price_val, str):
                entry_price = float(entry_price_val)
            else:
                entry_price = float(entry_price_val) if entry_price_val else 0.0
        except (ValueError, TypeError):
            entry_price = 0.0
        
        # Get target and SL, handle 'N/A' strings
        target_val = order_data.get('target')
        sl_val = order_data.get('sl')
        
        target_price = None
        if target_val not in ['N/A', None, '']:
            try:
                target_price = float(target_val) if isinstance(target_val, (int, float, str)) else None
            except (ValueError, TypeError):
                target_price = None
        
        sl_price = None
        if sl_val not in ['N/A', None, '']:
            try:
                sl_price = float(sl_val) if isinstance(sl_val, (int, float, str)) else None
            except (ValueError, TypeError):
                sl_price = None
        
        # Log to Excel
        excel_logger.log_trade(
            order_type=side,
            option_type=option_type,
            strike=strike,
            entry_price=entry_price,
            current_price=entry_price,  # Same as entry on placement
            target=target_price,
            stop_loss=sl_price,
            pnl=None,  # No P&L on order placement
            status=excel_status,
            order_id=order_data.get('order_id'),
            notes=notes_str
        )
        
        logger.info(f"✅ Order logged to Excel: {side}")
        
    except Exception as e:
        logger.error(f"Failed to write order placement log: {e}", exc_info=True)


class Intraday920LiveSignal:
    """
    Live signal monitoring for Intraday 9:20 strategy.
    
    Features:
    - Monitors every 30 seconds
    - Only during market hours (9:15 AM - 3:30 PM IST)
    - Tracks entry signals in real-time
    - Maintains trade state across monitoring intervals
    """
    
    # Market hours (IST)
    MARKET_OPEN = time(9, 15, 0)      # 9:15 AM
    MARKET_CLOSE = time(15, 20, 0)    # 3:20 PM (market closes at 3:30 but last candle is 3:20)
    MONITORING_INTERVAL = 3  # seconds
    
    def __init__(self, kite_instance, symbol: str = 'NIFTY', live_trading: bool = True):
        """
        Initialize live signal monitor.
        
        Args:
            kite_instance: KiteConnect instance
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
            live_trading: Whether to place real orders (default: True for live trading)
        """
        self.kite = kite_instance
        self.symbol = symbol
        self.strategy = Intraday920Strategy(kite_instance)
        self.kite_service = KiteService(kite_instance=kite_instance)
        
        # Monitoring state
        self.is_monitoring = False
        self.monitor_thread = None
        self.active_trades = {}  # Track active trades: {side: {entry_info, order_id, target_hit, trailed_sl}}
        self.active_trades_lock = threading.Lock()  # Thread safety for active_trades
        self.today_signals = []  # All signals generated today
        self.daily_entries = {}  # Track entries per side per day: {side: entry_date} to prevent multiple entries per day
        self.daily_entries_lock = threading.Lock()  # Thread safety for daily_entries
        self.last_entry_check_time = None  # Track last entry signal check to prevent duplicates
        self.last_sl_target_check_time = None  # Track last SL/Target check time (for time-based intervals)
        
        # Trading configuration
        self.live_trading = live_trading  # False=demo mode, True=live orders
        self.risk_reward_ratio = '1:2-trail'  # Use 1:2 with trailing SL
        
        # Configuration constants
        self.LIVE_DATA_FETCH_TIMEOUT = 10  # seconds
        self.PRICE_FETCH_BATCH_SIZE = 10  # max tokens per quote call
        
        logger.info(f"Intraday 9:20 Live Signal Monitor initialized for {symbol} (live_trading={live_trading}, ratio={self.risk_reward_ratio})")
    
    def is_market_hours(self, check_time: Optional[datetime] = None) -> bool:
        """
        Check if current time is within market hours (9:15 AM - 3:30 PM IST).
        
        Args:
            check_time: Optional datetime to check. Defaults to now.
            
        Returns:
            True if within market hours, False otherwise
        """
        if check_time is None:
            check_time = datetime.now()
        
        current_time = check_time.time()
        
        # Market hours: 9:15 AM to 3:30 PM
        return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE
    
    def is_market_day(self, check_date: Optional[datetime] = None) -> bool:
        """
        Check if date is a market trading day (Mon-Fri, not weekend).
        
        Args:
            check_date: Optional datetime to check. Defaults to today.
            
        Returns:
            True if trading day, False otherwise
        """
        if check_date is None:
            check_date = datetime.now()
        
        # Monday=0, Sunday=6
        return check_date.weekday() < 5
    
    def should_check_sl_target_now(self) -> bool:
        """
        Determine if we should check for SL/TARGET based on 3-second time intervals.
        Uses time-delta approach instead of second modulo for better accuracy and reliability.
        Prevents race conditions from API delays or loop processing time.
        
        Returns:
            True if 3+ seconds have elapsed since last check, False otherwise
        """
        now = datetime.now()
        
        # First check ever - do it now
        if self.last_sl_target_check_time is None:
            self.last_sl_target_check_time = now
            return True
        
        # Check if 3+ seconds have elapsed
        elapsed = (now - self.last_sl_target_check_time).total_seconds()
        if elapsed >= self.MONITORING_INTERVAL:  # 3 seconds
            self.last_sl_target_check_time = now
            return True
        
        return False
    
    def has_entered_today(self, side: str, strike: int) -> bool:
        """
        Check if entry has already been made for this side+strike combination today.
        Prevents multiple entries for the same strike+side per day.
        e.g., CE_24600, PE_24600 are tracked separately
        
        Args:
            side: 'CE' or 'PE'
            strike: Strike price (e.g., 24600)
            
        Returns:
            True if already entered today for this strike+side, False otherwise
        """
        today = datetime.now().date()
        entry_key = f"{side}_{strike}"  # e.g., 'CE_24600'
        with self.daily_entries_lock:
            if entry_key in self.daily_entries:
                entry_date = self.daily_entries[entry_key]
                # entry_date is a date object
                return entry_date == today
        return False
    
    def mark_entry_today(self, side: str, strike: int) -> None:
        """
        Mark that an entry has been made for this side+strike combination today.
        
        Args:
            side: 'CE' or 'PE'
            strike: Strike price (e.g., 24600)
        """
        today = datetime.now().date()
        entry_key = f"{side}_{strike}"  # e.g., 'CE_24600'
        with self.daily_entries_lock:
            self.daily_entries[entry_key] = today
            logger.info(f"📋 {side} {strike} entry marked for today ({today})")
    
    def reset_daily_entries(self) -> None:
        """
        Reset daily entries tracking (call at market close or new day).
        """
        with self.daily_entries_lock:
            self.daily_entries = {}
            logger.info("🔄 Daily entries tracking reset")
    
    def should_check_entry_signal(self) -> bool:
        """
        Determine if we should check for ENTRY signals now.
        Returns True only at 5-minute marks (9:15, 9:20, 9:25, ..., 3:15, 3:20).
        
        Uses last_entry_check_time to ensure each 5-minute interval is checked exactly once,
        preventing missed checks after hour boundaries or during processing delays.
        
        Entry signals checked only at 5-minute intervals.
        SL/Target checks happen every 30 seconds.
        
        Returns:
            True if we're at a 5-minute mark AND haven't checked this interval yet
        """
        now = datetime.now()
        
        # Check if we're at a 5-minute mark (minute divisible by 5)
        if now.minute % 5 != 0:
            return False
        
        # Calculate current 5-minute interval (e.g., 14:00, 14:05, 14:10)
        current_interval = now.replace(second=0, microsecond=0)
        
        # If we haven't checked yet, or if this is a new 5-minute interval
        if self.last_entry_check_time is None or current_interval > self.last_entry_check_time:
            self.last_entry_check_time = current_interval
            return True
        
        return False
    
    async def fetch_live_data(self) -> Dict[str, Any]:
        """
        Fetch live market data for CE/PE strikes.
        
        Returns:
            Dictionary with current strike data
        """
        try:
            # Get current price and first 5-min data
            intraday_data = self.strategy.get_intraday_920_data(self.symbol)
            
            if not intraday_data.get('success'):
                logger.warning(f"Failed to fetch intraday data: {intraday_data.get('error')}")
                return {'success': False}
            
            return intraday_data
            
        except Exception as e:
            logger.error(f"Error fetching live data for {self.symbol}: {str(e)}")
            return {'success': False, 'error': str(e)}

    def _fetch_live_data_with_refresh(self) -> Dict[str, Any]:
        """Fetch live data and retry once after access-token refresh if needed."""
        live_data = self.strategy.get_intraday_920_data(self.symbol)
        if live_data.get('success'):
            return live_data

        error_msg = str(live_data.get('error', ''))
        if 'Incorrect `api_key` or `access_token`' in error_msg:
            logger.warning("Access token invalid during live fetch. Attempting refresh and retry...")
            refresh_ok = False
            try:
                refresh_ok = self.strategy._refresh_kite_access_token()  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")

            if refresh_ok:
                live_data = self.strategy.get_intraday_920_data(self.symbol)

        return live_data

    def _fetch_live_data_with_timeout(self, timeout_seconds: int = 10) -> Dict[str, Any]:
        """Fetch live data with a timeout to avoid blocking the 5-min log cycle."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._fetch_live_data_with_refresh)
            try:
                return future.result(timeout=timeout_seconds)
            except FuturesTimeoutError:
                logger.warning(f"Live data fetch timed out after {timeout_seconds}s")
                return {
                    'success': False,
                    'error': f'Live data fetch timed out after {timeout_seconds}s'
                }
    
    def check_entry_signals_parallel(self, high_strike: Dict[str, Any], low_strike: Dict[str, Any]) -> tuple:
        """
        Check entry signals for both high and low strikes in PARALLEL for efficiency.
        
        Parallel execution reduces wait time when checking multiple strikes.
        
        Args:
            high_strike: High strike data
            low_strike: Low strike data
            
        Returns:
            Tuple of (high_signals, low_signals)
        """
        with ThreadPoolExecutor(max_workers=2) as executor:
            high_future = executor.submit(
                self.check_entry_signal_live,
                int(high_strike.get('ce_token', 0)),
                int(high_strike.get('pe_token', 0)),
                float(high_strike.get('ce_high', 0)),
                float(high_strike.get('pe_high', 0))
            )
            
            low_future = executor.submit(
                self.check_entry_signal_live,
                int(low_strike.get('ce_token', 0)),
                int(low_strike.get('pe_token', 0)),
                float(low_strike.get('ce_high', 0)),
                float(low_strike.get('pe_high', 0))
            )
            
            try:
                high_signals = high_future.result(timeout=self.LIVE_DATA_FETCH_TIMEOUT)
                low_signals = low_future.result(timeout=self.LIVE_DATA_FETCH_TIMEOUT)
            except FuturesTimeoutError:
                logger.warning("Signal check timed out")
                high_signals = {'success': False, 'error': 'Timeout'}
                low_signals = {'success': False, 'error': 'Timeout'}
            
            return high_signals, low_signals
    
    def _process_strike_signals(self, signals: Dict[str, Any], strike_data: Dict[str, Any], 
                               strike_name: str) -> Dict[str, Any]:
        """
        Process entry signals from a single strike (DRY pattern).
        
        Args:
            signals: Signal data from check_entry_signal_live()
            strike_data: Strike data for order placement
            strike_name: Name for logging ("HIGH STRIKE" or "LOW STRIKE")
            
        Returns:
            Dictionary with signal processing results
        """
        result = {
            'has_ce_signal': False,
            'has_pe_signal': False,
            'ce_entry_price': None,
            'ce_sl': None,
            'ce_target': None,
            'pe_entry_price': None,
            'pe_sl': None,
            'pe_target': None,
            'ce_high_val': strike_data.get('ce_high'),
            'pe_high_val': strike_data.get('pe_high')
        }
        
        if not signals.get('success'):
            return result
        
        ce_sig = signals.get('ce_signal', {})
        pe_sig = signals.get('pe_signal', {})
        
        logger.info(f"{strike_name} Signals - CE has_signal: {ce_sig.get('has_signal')}, PE has_signal: {pe_sig.get('has_signal')}")
        
        if ce_sig.get('has_signal'):
            result['has_ce_signal'] = True
            result['ce_entry_price'] = ce_sig.get('entry_price')
            result['ce_sl'] = ce_sig.get('sl')
            result['ce_target'] = ce_sig.get('target')
            logger.info(f"📊 {strike_name} CE SIGNAL: Entry {ce_sig.get('entry_price')}, SL {ce_sig.get('sl')}, Target {ce_sig.get('target')}")
        else:
            logger.info(f"CE No Signal Reason: {ce_sig.get('reason', 'Unknown')}")
        
        if pe_sig.get('has_signal'):
            result['has_pe_signal'] = True
            result['pe_entry_price'] = pe_sig.get('entry_price')
            result['pe_sl'] = pe_sig.get('sl')
            result['pe_target'] = pe_sig.get('target')
            logger.info(f"📊 {strike_name} PE SIGNAL: Entry {pe_sig.get('entry_price')}, SL {pe_sig.get('sl')}, Target {pe_sig.get('target')}")
        else:
            logger.info(f"PE No Signal Reason: {pe_sig.get('reason', 'Unknown')}")
        
        # Update trades if there are signals
        if result['has_ce_signal'] or result['has_pe_signal']:
            self.update_active_trades(signals, strike_data=strike_data)
            self.log_signal(signals)
        
        return result
    
    def update_active_trades(self, signals: Dict[str, Any], strike_data: Optional[Dict[str, Any]] = None) -> None:
        """
        Update active trade state with new signals from strategy.check_entry_signal().
        Places buy orders when entry signals are detected.
        
        IMPORTANT: Allows multiple sequential entries per side (e.g., 10:15 CE entry, exit at 11:45, then 11:00 CE entry).
        New entries will replace the existing trade on that side only if the previous trade has been closed.
        
        Uses signal data returned by the strategy which includes:
        - has_signal: Whether entry conditions were met
        - entry_price: Entry price at signal
        - sl: Stop loss calculated by strategy.calculate_sl_for_entry()
        - target: Target calculated by strategy.calculate_sl_for_entry()
        
        Thread-safe access to active_trades dictionary.
        
        Args:
            signals: Entry signals from check_entry_signal_live()
            strike_data: Optional strike data for order placement {ce_token, pe_token, ce_high, pe_high}
        """
        ce_signal = signals.get('ce_signal', {})
        pe_signal = signals.get('pe_signal', {})
        
        with self.active_trades_lock:
            # Track CE entry and place buy order
            # Allow new CE entry ONLY if:
            # 1. Signal exists AND (no CE trade exists OR existing CE trade is already closed)
            # 2. AND no entry has been made for this CE STRIKE today (prevent multiple entries per strike per day)
            if ce_signal.get('has_signal'):
                order_id = None
                ce_strike = None
                if strike_data:
                    # Get actual CE strike price (not the candle high)
                    ce_strike_val = strike_data.get('ce_strike')
                    if ce_strike_val:
                        ce_strike = int(ce_strike_val)
                    else:
                        ce_high_val = strike_data.get('ce_high')
                        if ce_high_val:
                            ce_strike = int(ce_high_val)
                
                if ce_strike and self.has_entered_today('CE', ce_strike):
                    logger.info(f"⛔ CE {ce_strike} entry already made today - skipping to prevent multiple entries per strike per day")
                elif 'CE' not in self.active_trades or self.active_trades['CE'].get('status') == 'CLOSED':
                    # Mark entry IMMEDIATELY to prevent race conditions with multiple signals
                    if ce_strike:
                        self.mark_entry_today('CE', ce_strike)
                    
                    if strike_data:
                        
                        if ce_strike:
                            order_id = self.place_buy_order(
                                side='CE',
                                token=strike_data.get('ce_token'),  # type: ignore
                                strike=ce_strike,
                                entry_price=ce_signal.get('entry_price')
                            )
                    
                    # Place SL order on broker
                    sl_order_id = None
                    if self.live_trading and strike_data and ce_strike:
                        try:
                            # Get lot size for this symbol (same as entry order quantity)
                            entry_quantity = self.kite_service.get_lot_size(self.symbol)
                            
                            # Get option trading symbol for SL order
                            option_symbol = self._get_option_symbol(self.symbol, ce_strike, 'CE')
                            if option_symbol:
                                logger.info(f"Placing CE SL order with symbol: {option_symbol}, quantity: {entry_quantity}")
                                sl_result = self.kite_service.place_stoploss_order(
                                    tradingsymbol=option_symbol,
                                    trigger_price=ce_signal.get('sl'),
                                    quantity=entry_quantity,
                                    product='NRML'
                                )
                                if sl_result['success']:
                                    sl_order_id = sl_result['order_id']
                                    logger.info(f"✅ CE SL order placed: {option_symbol} @ {ce_signal.get('sl'):.2f} | SL Order ID: {sl_order_id}")
                                    # Log SL order to Trade sheet
                                    excel_logger.log_trade(
                                        order_type='SL_ORDER',
                                        option_type='CE',
                                        strike=ce_strike,
                                        entry_price=ce_signal.get('sl'),
                                        target=ce_signal.get('target'),
                                        stop_loss=ce_signal.get('sl'),
                                        status='PLACED',
                                        order_id=sl_order_id,
                                        notes=f'SL Order Placed | Trigger: {ce_signal.get("sl"):.2f}'
                                    )
                                else:
                                    logger.error(f"❌ Failed to place CE SL order: {sl_result.get('error')}")
                                    # Log failure to Trade sheet
                                    excel_logger.log_trade(
                                        order_type='SL_ORDER',
                                        option_type='CE',
                                        strike=ce_strike,
                                        entry_price=ce_signal.get('sl'),
                                        target=ce_signal.get('target'),
                                        stop_loss=ce_signal.get('sl'),
                                        status='FAILED',
                                        notes=f'SL Order Failed: {sl_result.get("error")}'
                                    )
                            else:
                                logger.warning(f"Could not find option symbol for CE {ce_strike} - SL order not placed. Will monitor internally.")
                        except Exception as e:
                            logger.error(f"Error placing CE SL order: {e}", exc_info=True)
                    
                    self.active_trades['CE'] = {
                        'entry_price': ce_signal.get('entry_price'),
                        'entry_high': ce_signal.get('entry_high'),  # Reference high used for entry
                        'sl': ce_signal.get('sl'),
                        'target': ce_signal.get('target'),
                        'entry_time': datetime.now().isoformat(),
                        'order_id': order_id,
                        'sl_order_id': sl_order_id,  # Track SL order ID for modifications
                        'token': strike_data.get('ce_token') if strike_data else None,  # type: ignore
                        'strike': ce_strike if strike_data else None,
                        'status': 'OPEN',
                        # Trailing SL state
                        'target_hit': False,  # Track if target was hit
                        'trailed_sl': ce_signal.get('sl'),  # Current trailed SL (starts at initial SL)
                        'sl_distance': ce_signal.get('entry_price') - ce_signal.get('sl')  # Distance between entry and SL
                    }
                    # Entry is already marked above to prevent race conditions
                    logger.info(f"🟢 CE {ce_strike} trade opened at {ce_signal.get('entry_price')} (Entry High: {ce_signal.get('entry_high')}, SL: {ce_signal.get('sl')}, Target: {ce_signal.get('target')}) | Order ID: {order_id if order_id else 'N/A'} | SL Order ID: {sl_order_id if sl_order_id else 'N/A'}")
                else:
                    logger.info(f"⏭️  CE signal detected but trade already OPEN - skipping to allow sequential entries (close existing trade first)")
            
            # Track PE entry and place buy order
            # Allow new PE entry ONLY if:
            # 1. Signal exists AND (no PE trade exists OR existing PE trade is already closed)
            # 2. AND no entry has been made for this PE STRIKE today (prevent multiple entries per strike per day)
            if pe_signal.get('has_signal'):
                order_id = None
                pe_strike = None
                if strike_data:
                    # Get actual PE strike price (not the candle high)
                    pe_strike_val = strike_data.get('pe_strike')
                    if pe_strike_val:
                        pe_strike = int(pe_strike_val)
                    else:
                        pe_high_val = strike_data.get('pe_high')
                        if pe_high_val:
                            pe_strike = int(pe_high_val)
                
                if pe_strike and self.has_entered_today('PE', pe_strike):
                    logger.info(f"⛔ PE {pe_strike} entry already made today - skipping to prevent multiple entries per strike per day")
                elif 'PE' not in self.active_trades or self.active_trades['PE'].get('status') == 'CLOSED':
                    # Mark entry IMMEDIATELY to prevent race conditions with multiple signals
                    if pe_strike:
                        self.mark_entry_today('PE', pe_strike)
                    
                    if strike_data:
                        
                        if pe_strike:
                            order_id = self.place_buy_order(
                                side='PE',
                                token=strike_data.get('pe_token'),  # type: ignore
                                strike=pe_strike,
                                entry_price=pe_signal.get('entry_price')
                            )
                    
                    # Place SL order on broker
                    sl_order_id = None
                    if self.live_trading and strike_data and pe_strike:
                        try:
                            # Get lot size for this symbol (same as entry order quantity)
                            entry_quantity = self.kite_service.get_lot_size(self.symbol)
                            
                            # Get option trading symbol for SL order
                            option_symbol = self._get_option_symbol(self.symbol, pe_strike, 'PE')
                            if option_symbol:
                                logger.info(f"Placing PE SL order with symbol: {option_symbol}, quantity: {entry_quantity}")
                                sl_result = self.kite_service.place_stoploss_order(
                                    tradingsymbol=option_symbol,
                                    trigger_price=pe_signal.get('sl'),
                                    quantity=entry_quantity,
                                    product='NRML'
                                )
                                if sl_result['success']:
                                    sl_order_id = sl_result['order_id']
                                    logger.info(f"✅ PE SL order placed: {option_symbol} @ {pe_signal.get('sl'):.2f} | SL Order ID: {sl_order_id}")
                                    # Log SL order to Trade sheet
                                    excel_logger.log_trade(
                                        order_type='SL_ORDER',
                                        option_type='PE',
                                        strike=pe_strike,
                                        entry_price=pe_signal.get('sl'),
                                        target=pe_signal.get('target'),
                                        stop_loss=pe_signal.get('sl'),
                                        status='PLACED',
                                        order_id=sl_order_id,
                                        notes=f'SL Order Placed | Trigger: {pe_signal.get("sl"):.2f}'
                                    )
                                else:
                                    logger.error(f"❌ Failed to place PE SL order: {sl_result.get('error')}")
                                    # Log failure to Trade sheet
                                    excel_logger.log_trade(
                                        order_type='SL_ORDER',
                                        option_type='PE',
                                        strike=pe_strike,
                                        entry_price=pe_signal.get('sl'),
                                        target=pe_signal.get('target'),
                                        stop_loss=pe_signal.get('sl'),
                                        status='FAILED',
                                        notes=f'SL Order Failed: {sl_result.get("error")}'
                                    )
                            else:
                                logger.warning(f"Could not find option symbol for PE {pe_strike} - SL order not placed. Will monitor internally.")
                        except Exception as e:
                            logger.error(f"Error placing PE SL order: {e}", exc_info=True)
                    
                    self.active_trades['PE'] = {
                        'entry_price': pe_signal.get('entry_price'),
                        'entry_high': pe_signal.get('entry_high'),  # Reference high used for entry
                        'sl': pe_signal.get('sl'),
                        'target': pe_signal.get('target'),
                        'entry_time': datetime.now().isoformat(),
                        'order_id': order_id,
                        'sl_order_id': sl_order_id,  # Track SL order ID for modifications
                        'token': strike_data.get('pe_token') if strike_data else None,  # type: ignore
                        'strike': pe_strike if strike_data else None,
                        'status': 'OPEN',
                        # Trailing SL state
                        'target_hit': False,  # Track if target was hit
                        'trailed_sl': pe_signal.get('sl'),  # Current trailed SL (starts at initial SL)
                        'sl_distance': pe_signal.get('entry_price') - pe_signal.get('sl')  # Distance between entry and SL
                    }
                    # Entry is already marked above to prevent race conditions
                    logger.info(f"🟢 PE {pe_strike} trade opened at {pe_signal.get('entry_price')} (Entry High: {pe_signal.get('entry_high')}, SL: {pe_signal.get('sl')}, Target: {pe_signal.get('target')}) | Order ID: {order_id if order_id else 'N/A'} | SL Order ID: {sl_order_id if sl_order_id else 'N/A'}")
                else:
                    logger.info(f"⏭️  PE signal detected but trade already OPEN - skipping to allow sequential entries (close existing trade first)")
    
    def log_signal(self, signals: Dict[str, Any]) -> None:
        """
        Log signal information for tracking.
        
        Args:
            signals: Entry signals
        """
        signal_entry = {
            'timestamp': signals.get('timestamp'),
            'ce_signal': signals.get('ce_signal'),
            'pe_signal': signals.get('pe_signal')
        }
        self.today_signals.append(signal_entry)
        
        # Keep only last 100 signals in memory
        if len(self.today_signals) > 100:
            self.today_signals.pop(0)
    
    def check_entry_signal_live(self, ce_token: int, pe_token: int, 
                               ce_high: float, pe_high: float) -> Dict[str, Any]:
        """
        Check for live entry signals using the strategy's check_entry_signal method.
        
        Reuses entry detection and SL/Target calculation logic from Intraday920Strategy.
        
        Args:
            ce_token: CE option token
            pe_token: PE option token
            ce_high: Reference high for CE entry (CE first 5-min high)
            pe_high: Reference high for PE entry (PE first 5-min high)
            
        Returns:
            Dictionary with entry signals from strategy.check_entry_signal()
        """
        try:
            # Use strategy's check_entry_signal method which:
            # 1. Fetches latest 5-min candles for both CE and PE
            # 2. Analyzes entry conditions for each side
            # 3. Calculates SL and Target using calculate_sl_for_entry
            # 4. Returns formatted signal data
            
            signals = self.strategy.check_entry_signal(
                ce_token=ce_token,
                pe_token=pe_token,
                ce_high=ce_high,
                pe_high=pe_high,
                symbol=self.symbol
            )
            
            return signals
            
        except Exception as e:
            logger.error(f"Error checking entry signals: {str(e)}")
            return {'success': False, 'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def place_buy_order(self, side: str, token: int, strike: int, entry_price: float) -> Optional[str]:
        """
        Place a buy order for CE or PE option when entry signal detected.
        
        Reuses KiteService.place_option_order() which:
        1. Looks up the option trading symbol
        2. Fetches current market price
        3. Places market order on Zerodha Kite
        
        Logs order placement to dedicated order_placement.log file.
        
        Args:
            side: 'CE' or 'PE'
            token: Option token
            strike: Strike price
            entry_price: Entry price from signal
            
        Returns:
            Order ID or None if failed
        """
        logger.info(f"place_buy_order called: {side} {strike} @ {entry_price:.2f} (live_trading={self.live_trading})")
        
        if not self.live_trading:
            demo_msg = f"DEMO: BUY {side} {strike} @ {entry_price:.2f}"
            logger.info(demo_msg)
            
            # Log DEMO order placement
            log_order_placement({
                'symbol': self.symbol,
                'side': f'BUY {side}',
                'strike': strike,
                'entry_price': f"{entry_price:.2f}",
                'order_type': 'MARKET',
                'mode': 'DEMO',
                'status': 'SUCCESS',
                'order_id': 'DEMO_ORDER',
                'sl': 'N/A',
                'target': 'N/A'
            })
            
            return "DEMO_ORDER"
        
        try:
            result = self.kite_service.place_option_order(
                symbol=self.symbol,
                strike=strike,
                option_type=side,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY
            )
            
            if result['success']:
                logger.info(f"✅ BUY Order placed successfully. Order ID: {result['order_id']} | {side} {strike} @ {entry_price:.2f}")
                
                # Log successful live order placement
                log_order_placement({
                    'symbol': self.symbol,
                    'side': f'BUY {side}',
                    'strike': strike,
                    'entry_price': f"{entry_price:.2f}",
                    'order_type': 'MARKET',
                    'mode': 'LIVE',
                    'status': 'SUCCESS',
                    'order_id': result['order_id'],
                    'sl': 'N/A',
                    'target': 'N/A',
                    'details': f"Order placed successfully on Zerodha"
                })
                
                return result['order_id']
            else:
                logger.error(f"❌ BUY Order failed: {result['error']}")
                
                # Log failed live order placement
                log_order_placement({
                    'symbol': self.symbol,
                    'side': f'BUY {side}',
                    'strike': strike,
                    'entry_price': f"{entry_price:.2f}",
                    'order_type': 'MARKET',
                    'mode': 'LIVE',
                    'status': 'FAILED',
                    'order_id': 'N/A',
                    'sl': 'N/A',
                    'target': 'N/A',
                    'error': result['error']
                })
                
                return None
                
        except Exception as e:
            logger.error(f"Error placing BUY order for {side} {strike}: {e}", exc_info=True)
            
            # Log exception during order placement
            log_order_placement({
                'symbol': self.symbol,
                'side': f'BUY {side}',
                'strike': strike,
                'entry_price': f"{entry_price:.2f}",
                'order_type': 'MARKET',
                'mode': 'LIVE',
                'status': 'EXCEPTION',
                'order_id': 'N/A',
                'sl': 'N/A',
                'target': 'N/A',
                'error': str(e)
            })
            
            return None
    
    def get_current_prices(self, tokens: List[int]) -> Dict[int, Optional[float]]:
        """
        Fetch multiple current prices (LTP) in single API call for efficiency.
        
        Batch fetching reduces API overhead when monitoring multiple tokens.
        
        Args:
            tokens: List of instrument tokens to fetch
            
        Returns:
            Dictionary mapping token -> price
        """
        try:
            if not tokens:
                return {}
            
            # Batch tokens in chunks if necessary
            quote_keys = [f"NFO:{token}" for token in tokens]
            quotes = self.kite.quote(quote_keys)
            
            result = {}
            for key, quote_data in quotes.items():
                try:
                    token = int(key.split(':')[1])
                    result[token] = quote_data.get('last_price')
                except (ValueError, KeyError):
                    pass
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching batch prices for {len(tokens)} tokens: {e}")
            return {}
    
    def get_current_price(self, token: int) -> Optional[float]:
        """
        Fetch current LTP (Last Traded Price) for a given token.
        
        Args:
            token: Instrument token
            
        Returns:
            Current price or None if failed
        """
        prices = self.get_current_prices([token])
        return prices.get(token)
    
    def _get_option_symbol(self, symbol: str, strike: int, option_type: str) -> Optional[str]:
        """
        Get the trading symbol for an option contract.
        e.g., 'NIFTY25D26C25000' for NIFTY, strike 25000, CE, expiry 26-DEC-2025
        
        Args:
            symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Trading symbol or None if not found
        """
        try:
            logger.debug(f"Looking up option symbol for {symbol} {strike} {option_type}")
            
            # Use kite_service to look up the option symbol
            if hasattr(self.kite_service, '_nfo_instruments_cache'):
                nfo_instruments = self.kite_service._nfo_instruments_cache
                if nfo_instruments:
                    logger.debug(f"Searching in {len(nfo_instruments)} NFO instruments")
                    for instrument in nfo_instruments:
                        if (instrument.get('name') == symbol and 
                            instrument.get('strike') == strike and
                            instrument.get('instrument_type') == option_type):
                            trading_symbol = instrument.get('tradingsymbol')
                            logger.info(f"✅ Found option symbol: {trading_symbol} for {symbol} {strike} {option_type}")
                            return trading_symbol
                    logger.warning(f"No match found in NFO cache for {symbol} {strike} {option_type}")
                else:
                    logger.warning("NFO instruments cache is empty")
            else:
                logger.warning("KiteService does not have _nfo_instruments_cache")
            
            # Fallback: Try to get from place_option_order (which may update the cache)
            logger.info(f"Attempting fallback: querying Kite API for {symbol} {strike} {option_type}")
            
            # Use KiteService's existing place_option_order logic which has lookup built-in
            # For now, return None to force reload or use alternative approach
            logger.warning(f"Could not find option symbol for {symbol} {strike} {option_type} - SL order will not be placed")
            return None
            
        except Exception as e:
            logger.error(f"Error getting option symbol for {symbol} {strike} {option_type}: {e}", exc_info=True)
            return None
    
    def check_sl_target_for_active_trades(self, check_timestamp: Optional[datetime] = None) -> None:
        """
        Monitor active trades and check if SL or Target has been hit.
        Implements 1:2 with Trailing SL logic:
        
        1. Before target hit: Exit if price <= initial SL
        2. When target hit: Move SL to entry price (lock in breakeven)
        3. After target hit: Trail SL by sl_distance for every sl_distance price moves above entry
        4. Exit when trailed SL is hit
        
        Automatically places SELL orders when conditions are met.
        Uses batch price fetching for efficiency when monitoring multiple trades.
        
        Args:
            check_timestamp: Timestamp when this check was initiated (for accurate logging/Excel tracking)
        """
        if check_timestamp is None:
            check_timestamp = datetime.now()
        if not self.active_trades:
            return
        
        # Fetch all prices at once (efficient batch operation)
        tokens = [t.get('token') for t in self.active_trades.values() if t.get('token')]
        prices = self.get_current_prices(tokens) if tokens else {}
        
        trades_to_close = []
        
        with self.active_trades_lock:
            for side, trade in list(self.active_trades.items()):
                if trade.get('status') != 'OPEN':
                    continue
                
                token = trade.get('token')
                strike = trade.get('strike')
                
                if not token or not strike or token not in prices:
                    if token and token not in prices:
                        logger.warning(f"Failed to fetch price for {side} {strike}")
                    continue
                
                # Get current price from batch fetch results
                current_price = prices[token]
                
                if current_price is None:
                    logger.warning(f"No price available for {side} {strike}")
                    continue
                
                entry_price = trade.get('entry_price', 0)
                initial_sl = trade.get('sl', 0)
                target = trade.get('target', 0)
                target_hit = trade.get('target_hit', False)
                trailed_sl = trade.get('trailed_sl', initial_sl)
                sl_distance = trade.get('sl_distance', entry_price - initial_sl)
                
                # === 1:2 WITH TRAILING SL LOGIC ===
                if target_hit:
                    # Target already hit - implement trailing SL
                    
                    # Calculate how much price has moved above entry
                    price_above_entry = current_price - entry_price
                    
                    # Trail SL by sl_distance for every sl_distance movement above entry
                    if price_above_entry > 0:
                        num_trails = int(price_above_entry / sl_distance)
                        new_trailed_sl = entry_price + (num_trails * sl_distance)
                        
                        if new_trailed_sl > trailed_sl:
                            old_sl = trailed_sl
                            trailed_sl = new_trailed_sl
                            trade['trailed_sl'] = trailed_sl
                            logger.info(f"📈 {side} Trailing SL updated: {trailed_sl:.2f} (price: {current_price:.2f}, entry: {entry_price:.2f})")
                            
                            # Modify SL order on broker if live trading
                            sl_order_id = trade.get('sl_order_id')
                            if self.live_trading and sl_order_id:
                                try:
                                    modify_result = self.kite_service.modify_stoploss_order(
                                        order_id=sl_order_id,
                                        new_trigger_price=trailed_sl
                                    )
                                    if modify_result['success']:
                                        logger.info(f"✅ {side} SL order modified on broker: {sl_order_id} -> Trigger: {trailed_sl:.2f}")
                                        # Log SL update to Trade sheet
                                        excel_logger.log_trade(
                                            order_type='SL_UPDATE',
                                            option_type=side,
                                            strike=strike,
                                            entry_price=entry_price,
                                            current_price=current_price,
                                            target=target,
                                            stop_loss=trailed_sl,
                                            pnl=current_price - entry_price,
                                            status='SL_TRAILED',
                                            order_id=sl_order_id,
                                            notes=f'SL Trailed from {old_sl:.2f} to {trailed_sl:.2f}'
                                        )
                                    else:
                                        logger.error(f"❌ Failed to modify {side} SL order: {modify_result.get('error')}")
                                except Exception as e:
                                    logger.error(f"Error modifying {side} SL order: {e}", exc_info=True)
                            
                            # Log to Signal Checks sheet
                            excel_logger.log_sl_target_check(
                                timestamp=check_timestamp,
                                side=side,
                                strike=strike,
                                current_price=current_price,
                                entry_price=entry_price,
                                initial_sl=initial_sl,
                                target=target,
                                target_hit=True,
                                trailed_sl=trailed_sl,
                                check_reason="TRAILING"
                            )
                            
                            # Log to Excel
                            profit = current_price - entry_price
                            excel_logger.log_trailing_sl_update(
                                option_type=side,
                                strike=strike,
                                old_sl=old_sl,
                                new_sl=trailed_sl,
                                current_price=current_price,
                                profit=profit
                            )
                    else:
                        # Price pulled back below entry - ensure SL stays at entry (lock in gain)
                        if trailed_sl < entry_price:
                            trailed_sl = entry_price
                            trade['trailed_sl'] = trailed_sl
                            logger.info(f"🔒 {side} SL locked at entry: {entry_price:.2f} (price pulled back to {current_price:.2f})")
                    
                    # Check if trailed SL is hit
                    if current_price <= trailed_sl:
                        logger.info(f"🔴 TRAILED SL HIT for {side}: Current {current_price:.2f} <= Trailed SL {trailed_sl:.2f}")
                        
                        # Log to Signal Checks sheet
                        excel_logger.log_sl_target_check(
                            timestamp=check_timestamp,
                            side=side,
                            strike=strike,
                            current_price=current_price,
                            entry_price=entry_price,
                            initial_sl=initial_sl,
                            target=target,
                            target_hit=True,
                            trailed_sl=trailed_sl,
                            check_reason="SL_HIT"
                        )
                        
                        # Log to Excel
                        pnl = current_price - entry_price
                        excel_logger.log_trade(
                            order_type='SELL',
                            option_type=side,
                            strike=strike,
                            entry_price=entry_price,
                            current_price=current_price,
                            target=target,
                            stop_loss=trailed_sl,
                            pnl=pnl,
                            status='TRAILED_SL_HIT',
                            notes=f"Trailed SL Hit at {trailed_sl:.2f} | Entry: {entry_price:.2f} | P&L: {pnl:+.2f}"
                        )
                        
                        # Place sell order
                        order_id = self.place_sell_order(
                            side=side,
                            strike=strike,
                            exit_price=current_price,
                            exit_reason=f"Trailed SL Hit ({trailed_sl:.2f})"
                        )
                        
                        # Close trade
                        self.close_trade(side, current_price, f"Trailed SL Hit ({trailed_sl:.2f})")
                        trades_to_close.append(side)
                    else:
                        # Still in trade - log status
                        pnl = current_price - entry_price
                        pnl_pct = (pnl / entry_price * 100) if entry_price else 0
                        logger.debug(f"📊 {side} Trade (Target Hit): Entry {entry_price:.2f}, Current {current_price:.2f}, Trailed SL {trailed_sl:.2f} | PnL: {pnl:.2f} ({pnl_pct:.2f}%)")
                        
                else:
                    # Target not yet hit - check for initial SL or target hit
                    
                    # Check if SL hit (before target)
                    if current_price <= initial_sl:
                        logger.info(f"🔴 SL HIT for {side}: Current {current_price:.2f} <= SL {initial_sl:.2f}")
                        
                        # Log to Signal Checks sheet
                        excel_logger.log_sl_target_check(
                            timestamp=check_timestamp,
                            side=side,
                            strike=strike,
                            current_price=current_price,
                            entry_price=entry_price,
                            initial_sl=initial_sl,
                            target=target,
                            target_hit=False,
                            check_reason="SL_HIT"
                        )
                        
                        # Log to Excel
                        pnl = current_price - entry_price
                        excel_logger.log_trade(
                            order_type='SELL',
                            option_type=side,
                            strike=strike,
                            entry_price=entry_price,
                            current_price=current_price,
                            target=target,
                            stop_loss=initial_sl,
                            pnl=pnl,
                            status='SL_HIT',
                            notes=f"Initial SL Hit at {initial_sl:.2f} | Entry: {entry_price:.2f} | P&L: {pnl:+.2f}"
                        )
                        
                        # Place sell order
                        order_id = self.place_sell_order(
                            side=side,
                            strike=strike,
                            exit_price=current_price,
                            exit_reason="SL Hit"
                        )
                        
                        # Close trade
                        self.close_trade(side, current_price, "SL Hit")
                        trades_to_close.append(side)
                        
                    # Check if Target hit (activate trailing SL)
                    elif current_price >= target:
                        logger.info(f"🎯 TARGET HIT for {side}: Current {current_price:.2f} >= Target {target:.2f}")
                        logger.info(f"🔄 Trailing SL activated for {side} - SL moved to entry {entry_price:.2f}")
                        
                        # Log to Signal Checks sheet
                        excel_logger.log_sl_target_check(
                            timestamp=check_timestamp,
                            side=side,
                            strike=strike,
                            current_price=current_price,
                            entry_price=entry_price,
                            initial_sl=initial_sl,
                            target=target,
                            target_hit=False,
                            check_reason="TARGET_HIT"
                        )
                        
                        # Log to Excel
                        pnl = current_price - entry_price
                        excel_logger.log_trade(
                            order_type='UPDATE',
                            option_type=side,
                            strike=strike,
                            entry_price=entry_price,
                            current_price=current_price,
                            target=target,
                            stop_loss=entry_price,  # SL moved to entry
                            pnl=pnl,
                            status='TARGET_HIT',
                            notes=f"Target Hit at {target:.2f} | Trailing SL activated | SL moved to entry {entry_price:.2f} | P&L: {pnl:+.2f}"
                        )
                        
                        # Activate trailing SL - move SL to entry price
                        trade['target_hit'] = True
                        trade['trailed_sl'] = entry_price
                        
                        # Don't exit yet - continue with trailing SL
                        
                    else:
                        # Still waiting for target or SL - log status
                        pnl = current_price - entry_price
                        pnl_pct = (pnl / entry_price * 100) if entry_price else 0
                        logger.debug(f"📊 {side} Trade Status: Entry {entry_price:.2f}, Current {current_price:.2f}, SL {initial_sl:.2f}, Target {target:.2f} | PnL: {pnl:.2f} ({pnl_pct:.2f}%)")
        
        # Mark closed trades as CLOSED (don't delete - allows next entry to detect status)
        with self.active_trades_lock:
            for side in trades_to_close:
                if side in self.active_trades:
                    self.active_trades[side]['status'] = 'CLOSED'
                    logger.info(f"📋 {side} trade marked as CLOSED - allowing new entry on next signal")
    
    def place_sell_order(self, side: str, strike: int, exit_price: float, exit_reason: str = "Manual Exit") -> Optional[str]:
        """
        Place a sell order for CE or PE option to exit trade.
        
        Reuses KiteService.place_option_order() which:
        1. Looks up the option trading symbol
        2. Fetches current market price
        3. Places market order on Zerodha Kite
        
        Logs order placement to dedicated order_placement.log file.
        
        Args:
            side: 'CE' or 'PE'
            strike: Strike price
            exit_price: Exit price
            exit_reason: Reason for exit (Target Hit, SL Hit, Manual, etc.)
            
        Returns:
            Order ID or None if failed
        """
        logger.info(f"place_sell_order called: {side} {strike} @ {exit_price:.2f} | Reason: {exit_reason} (live_trading={self.live_trading})")
        
        if not self.live_trading:
            demo_msg = f"DEMO: SELL {side} {strike} @ {exit_price:.2f} | {exit_reason}"
            logger.info(demo_msg)
            
            # Log DEMO sell order placement
            log_order_placement({
                'symbol': self.symbol,
                'side': f'SELL {side}',
                'strike': strike,
                'entry_price': f"{exit_price:.2f}",
                'order_type': 'MARKET',
                'mode': 'DEMO',
                'status': 'SUCCESS',
                'order_id': 'DEMO_ORDER',
                'sl': 'N/A',
                'target': 'N/A',
                'details': f"Exit Reason: {exit_reason}"
            })
            
            return "DEMO_ORDER"
        
        try:
            result = self.kite_service.place_option_order(
                symbol=self.symbol,
                strike=strike,
                option_type=side,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL
            )
            
            if result['success']:
                logger.info(f"✅ SELL Order placed successfully. Order ID: {result['order_id']} | {side} {strike} @ {exit_price:.2f} | {exit_reason}")
                
                # Log successful live sell order placement
                log_order_placement({
                    'symbol': self.symbol,
                    'side': f'SELL {side}',
                    'strike': strike,
                    'entry_price': f"{exit_price:.2f}",
                    'order_type': 'MARKET',
                    'mode': 'LIVE',
                    'status': 'SUCCESS',
                    'order_id': result['order_id'],
                    'sl': 'N/A',
                    'target': 'N/A',
                    'details': f"Exit Reason: {exit_reason}"
                })
                
                return result['order_id']
            else:
                logger.error(f"❌ SELL Order failed: {result['error']} ({exit_reason})")
                
                # Log failed live sell order placement
                log_order_placement({
                    'symbol': self.symbol,
                    'side': f'SELL {side}',
                    'strike': strike,
                    'entry_price': f"{exit_price:.2f}",
                    'order_type': 'MARKET',
                    'mode': 'LIVE',
                    'status': 'FAILED',
                    'order_id': 'N/A',
                    'sl': 'N/A',
                    'target': 'N/A',
                    'error': result['error'],
                    'details': f"Exit Reason: {exit_reason}"
                })
                
                return None
                
        except Exception as e:
            logger.error(f"Error placing SELL order for {side} {strike}: {e}", exc_info=True)
            
            # Log exception during sell order placement
            log_order_placement({
                'symbol': self.symbol,
                'side': f'SELL {side}',
                'strike': strike,
                'entry_price': f"{exit_price:.2f}",
                'order_type': 'MARKET',
                'mode': 'LIVE',
                'status': 'EXCEPTION',
                'order_id': 'N/A',
                'sl': 'N/A',
                'target': 'N/A',
                'error': str(e),
                'details': f"Exit Reason: {exit_reason}"
            })
            
            return None
    
    def _monitor_loop(self) -> None:
        """
        Main monitoring loop - runs in background thread.
        
        Two-tier monitoring:
        1. ENTRY SIGNALS: Checked only at 5-minute marks (9:15, 9:20, 9:25, ..., 3:15, 3:20)
        2. SL/TARGET MONITORING: Checked every 3 seconds
        
        Uses strategy.check_entry_signal() to detect entry signals.
        """
        logger.info(f"Starting live signal monitoring for {self.symbol}")
        
        while self.is_monitoring:
            try:
                # Check if within market hours
                if not self.is_market_hours():
                    if self.is_market_day():
                        logger.debug("Outside market hours, waiting...")
                    time_module.sleep(1)
                    continue
                
                # ====== ENTRY SIGNAL CHECK (5-minute marks only) ======
                if self.should_check_entry_signal():
                    # Use normalized 5-minute interval time set by should_check_entry_signal
                    check_timestamp = self.last_entry_check_time or datetime.now()
                    logger.info(f"[5-min Check] Checking entry signals for {self.symbol} at {check_timestamp.strftime('%H:%M:%S')}")
                    
                    # Fetch live data (timeout-protected)
                    live_data = self._fetch_live_data_with_timeout()
                    
                    if live_data.get('success'):
                        # Extract strike info
                        high_strike = live_data.get('high_strike', {})
                        low_strike = live_data.get('low_strike', {})
                        
                        logger.info(f"High Strike - CE Token: {high_strike.get('ce_token')}, PE Token: {high_strike.get('pe_token')}, CE High: {high_strike.get('ce_high')}, PE High: {high_strike.get('pe_high')}")
                        logger.info(f"Low Strike - CE Token: {low_strike.get('ce_token')}, PE Token: {low_strike.get('pe_token')}, CE High: {low_strike.get('ce_high')}, PE High: {low_strike.get('pe_high')}")
                        
                        # Initialize data for Excel logging
                        has_ce_signal = False
                        has_pe_signal = False
                        ce_entry_price = None
                        pe_entry_price = None
                        ce_sl = None
                        pe_sl = None
                        ce_target = None
                        pe_target = None
                        ce_high_val = high_strike.get('ce_high')
                        pe_high_val = high_strike.get('pe_high')
                        
                        if high_strike.get('success') and low_strike.get('success'):
                            # Check signals for high strike
                            high_signals = self.check_entry_signal_live(
                                high_strike.get('ce_token'),
                                high_strike.get('pe_token'),
                                high_strike.get('ce_high'),
                                high_strike.get('pe_high')
                            )
                            
                            if high_signals.get('success'):
                                ce_sig = high_signals.get('ce_signal', {})
                                pe_sig = high_signals.get('pe_signal', {})
                                
                                # Log what was received
                                logger.info(f"HIGH STRIKE Signals - CE has_signal: {ce_sig.get('has_signal')}, PE has_signal: {pe_sig.get('has_signal')}")
                                if not ce_sig.get('has_signal'):
                                    logger.info(f"CE No Signal Reason: {ce_sig.get('reason', 'Unknown')}")
                                if not pe_sig.get('has_signal'):
                                    logger.info(f"PE No Signal Reason: {pe_sig.get('reason', 'Unknown')}")
                                
                                # Update Excel logging data if signals exist
                                if ce_sig.get('has_signal'):
                                    has_ce_signal = True
                                    ce_entry_price = ce_sig.get('entry_price')
                                    ce_sl = ce_sig.get('sl')
                                    ce_target = ce_sig.get('target')
                                
                                if pe_sig.get('has_signal'):
                                    has_pe_signal = True
                                    pe_entry_price = pe_sig.get('entry_price')
                                    pe_sl = pe_sig.get('sl')
                                    pe_target = pe_sig.get('target')
                                
                                # Only update trades if there are actual signals
                                if ce_sig.get('has_signal') or pe_sig.get('has_signal'):
                                    self.update_active_trades(high_signals, strike_data=high_strike)
                                    self.log_signal(high_signals)
                                
                                if ce_sig.get('has_signal'):
                                    logger.info(f"📊 HIGH STRIKE CE SIGNAL: Entry {ce_sig.get('entry_price')}, SL {ce_sig.get('sl')}, Target {ce_sig.get('target')} | ✅ Order placed")
                                
                                if pe_sig.get('has_signal'):
                                    logger.info(f"📊 HIGH STRIKE PE SIGNAL: Entry {pe_sig.get('entry_price')}, SL {pe_sig.get('sl')}, Target {pe_sig.get('target')} | ✅ Order placed")
                            
                            # Check signals for low strike (only if high strike didn't have signals)
                            if not has_ce_signal and not has_pe_signal:
                                low_signals = self.check_entry_signal_live(
                                    low_strike.get('ce_token'),
                                    low_strike.get('pe_token'),
                                    low_strike.get('ce_high'),
                                    low_strike.get('pe_high')
                                )
                                
                                if low_signals.get('success'):
                                    ce_sig = low_signals.get('ce_signal', {})
                                    pe_sig = low_signals.get('pe_signal', {})
                                    
                                    # Log what was received
                                    logger.info(f"LOW STRIKE Signals - CE has_signal: {ce_sig.get('has_signal')}, PE has_signal: {pe_sig.get('has_signal')}")
                                    if not ce_sig.get('has_signal'):
                                        logger.info(f"CE No Signal Reason: {ce_sig.get('reason', 'Unknown')}")
                                    if not pe_sig.get('has_signal'):
                                        logger.info(f"PE No Signal Reason: {pe_sig.get('reason', 'Unknown')}")
                                    
                                    # Update Excel logging data if signals exist
                                    if ce_sig.get('has_signal'):
                                        has_ce_signal = True
                                        ce_entry_price = ce_sig.get('entry_price')
                                        ce_sl = ce_sig.get('sl')
                                        ce_target = ce_sig.get('target')
                                        ce_high_val = low_strike.get('ce_high')
                                    
                                    if pe_sig.get('has_signal'):
                                        has_pe_signal = True
                                        pe_entry_price = pe_sig.get('entry_price')
                                        pe_sl = pe_sig.get('sl')
                                        pe_target = pe_sig.get('target')
                                        pe_high_val = low_strike.get('pe_high')
                                    
                                    # Only update trades if there are actual signals
                                    if ce_sig.get('has_signal') or pe_sig.get('has_signal'):
                                        self.update_active_trades(low_signals, strike_data=low_strike)
                                        self.log_signal(low_signals)
                                    
                                    if ce_sig.get('has_signal'):
                                        logger.info(f"📊 LOW STRIKE CE SIGNAL: Entry {ce_sig.get('entry_price')}, SL {ce_sig.get('sl')}, Target {ce_sig.get('target')} | ✅ Order placed")
                                    
                                    if pe_sig.get('has_signal'):
                                        logger.info(f"📊 LOW STRIKE PE SIGNAL: Entry {pe_sig.get('entry_price')}, SL {pe_sig.get('sl')}, Target {pe_sig.get('target')} | ✅ Order placed")
                        
                        # LOG EVERY 5-MINUTE CHECK TO EXCEL (both High + Low strike)
                        excel_logger.log_signal_check(
                            timestamp=check_timestamp,
                            ce_prev_high=ce_high_val,
                            ce_prev_low=None,  # Not tracked currently
                            pe_prev_high=pe_high_val,
                            pe_prev_low=None,  # Not tracked currently
                            ce_signal=has_ce_signal,
                            pe_signal=has_pe_signal,
                            ce_entry_price=ce_entry_price,
                            pe_entry_price=pe_entry_price,
                            ce_sl=ce_sl,
                            pe_sl=pe_sl,
                            ce_target=ce_target,
                            pe_target=pe_target,
                            notes="High Strike Check" if high_strike.get('success') else "High Strike data unavailable"
                        )

                        # Low strike log (even if no signals)
                        low_ce_high = low_strike.get('ce_high')
                        low_pe_high = low_strike.get('pe_high')
                        excel_logger.log_signal_check(
                            timestamp=check_timestamp,
                            ce_prev_high=low_ce_high,
                            ce_prev_low=None,  # Not tracked currently
                            pe_prev_high=low_pe_high,
                            pe_prev_low=None,  # Not tracked currently
                            ce_signal=has_ce_signal,
                            pe_signal=has_pe_signal,
                            ce_entry_price=ce_entry_price,
                            pe_entry_price=pe_entry_price,
                            ce_sl=ce_sl,
                            pe_sl=pe_sl,
                            ce_target=ce_target,
                            pe_target=pe_target,
                            notes="Low Strike Check" if low_strike.get('success') else "Low Strike data unavailable"
                        )
                        
                    else:
                        logger.warning(f"Failed to fetch live data: {live_data.get('error')}")
                        
                        # Log failed check to Excel
                        excel_logger.log_signal_check(
                            timestamp=check_timestamp,
                            notes=f"Failed to fetch data: {live_data.get('error', 'Unknown error')}"
                        )
                
                # ====== SL/TARGET CHECK (3-second intervals) ======
                if self.should_check_sl_target_now():
                    check_time = self.last_sl_target_check_time
                    if check_time:
                        logger.debug(f"[3-sec Check] Monitoring SL/Target for {self.symbol} at {check_time.strftime('%H:%M:%S.%f')[:-3]}")
                    else:
                        logger.debug(f"[3-sec Check] Monitoring SL/Target for {self.symbol}")
                    
                    # Check active trades for SL/Target hits
                    if self.active_trades:
                        logger.debug(f"Active trades to monitor: {list(self.active_trades.keys())}")
                        self.check_sl_target_for_active_trades(check_timestamp=check_time)
                    else:
                        logger.debug("No active trades to monitor")
                
                # ====== MARKET CLOSE CHECK (3:20 PM) ======
                # Force exit all trades at market close time
                current_time = datetime.now().time()
                if current_time >= time(15, 20, 0):  # 3:20 PM IST
                    if self.active_trades:
                        market_close_timestamp = datetime.now()
                        logger.info(f"🔴 Market close (3:20 PM) - Force closing all active trades")
                        with self.active_trades_lock:
                            for side in list(self.active_trades.keys()):
                                trade = self.active_trades.get(side)
                                if trade and trade.get('status') == 'OPEN':
                                    # Get current price for exit
                                    token = trade.get('token')
                                    if token:
                                        current_price = self.get_current_price(token)
                                    else:
                                        current_price = trade.get('entry_price')
                                    
                                    entry_price = trade.get('entry_price', 0)
                                    pnl = current_price - entry_price if current_price else 0
                                    
                                    logger.info(f"🔴 {side} Force Exit at Market Close: Entry {entry_price:.2f}, Exit {current_price:.2f}, P&L: {pnl:+.2f}")
                                    
                                    # Log to Signal Checks sheet
                                    excel_logger.log_sl_target_check(
                                        timestamp=market_close_timestamp,
                                        side=side,
                                        strike=trade.get('strike'),
                                        current_price=current_price if current_price else entry_price,
                                        entry_price=entry_price,
                                        initial_sl=trade.get('sl'),
                                        target=trade.get('target'),
                                        target_hit=trade.get('target_hit'),
                                        check_reason="MARKET_CLOSE"
                                    )
                                    
                                    # Log to Excel trade sheet
                                    excel_logger.log_trade(
                                        order_type='SELL',
                                        option_type=side,
                                        strike=trade.get('strike'),
                                        entry_price=entry_price,
                                        current_price=current_price if current_price else entry_price,
                                        target=trade.get('target'),
                                        stop_loss=trade.get('sl'),
                                        pnl=pnl,
                                        status='MARKET_CLOSE',
                                        notes=f"Forced exit at market close (3:20 PM) | Entry: {entry_price:.2f} | Exit: {current_price:.2f} | P&L: {pnl:+.2f}"
                                    )
                                    
                                    # Close the trade
                                    self.close_trade(side, current_price if current_price else entry_price, "Market Close (3:20 PM)")
                
                # Sleep 1 second and check again
                time_module.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}", exc_info=True)
                time_module.sleep(1)
    
    def start_monitoring(self) -> bool:
        """
        Start live signal monitoring in background thread.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self.is_monitoring:
            logger.warning("Monitoring already running")
            return False
        
        if not self.is_market_day():
            logger.warning("Not a market trading day")
            return False
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name=f"Intraday920Monitor-{self.symbol}",
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Live signal monitoring started for {self.symbol}")
        
        return True
    
    def stop_monitoring(self) -> None:
        """Stop live signal monitoring."""
        if not self.is_monitoring:
            logger.warning("Monitoring not running")
            return
        
        self.is_monitoring = False
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        logger.info(f"Live signal monitoring stopped for {self.symbol}")
    
    def get_active_trades(self) -> Dict[str, Any]:
        """
        Get current active trades.
        
        Returns:
            Dictionary of active trades
        """
        return self.active_trades.copy()
    
    def get_today_signals(self) -> List[Dict[str, Any]]:
        """
        Get all signals generated today.
        
        Returns:
            List of today's signals
        """
        return self.today_signals.copy()
    
    def close_trade(self, side: str, exit_price: float, exit_reason: str = "Manual") -> Dict[str, Any]:
        """
        Close an active trade.
        
        Args:
            side: 'CE' or 'PE'
            exit_price: Exit price
            exit_reason: Reason for exit
            
        Returns:
            Trade summary
        """
        if side not in self.active_trades:
            return {'success': False, 'error': f'No active {side} trade'}
        
        trade = self.active_trades[side]
        entry_price = trade.get('entry_price', 0)
        pnl = exit_price - entry_price
        pnl_pct = (pnl / entry_price * 100) if entry_price else 0
        
        trade['exit_price'] = exit_price
        trade['exit_reason'] = exit_reason
        trade['exit_time'] = datetime.now().isoformat()
        trade['pnl'] = round(pnl, 2)
        trade['pnl_pct'] = round(pnl_pct, 2)
        trade['status'] = 'CLOSED'
        
        logger.info(f"🔴 {side} trade closed: Entry {entry_price}, Exit {exit_price}, PnL {pnl} ({pnl_pct}%)")
        
        return {
            'success': True,
            'trade': trade
        }
    
    def reset_daily(self) -> None:
        """Reset daily signals and trades (call at market open)."""
        self.active_trades = {}
        self.today_signals = []
        self.last_entry_check_time = None
        self.reset_daily_entries()  # Reset daily entry tracking
        logger.info(f"Daily monitoring reset for {self.symbol}")
