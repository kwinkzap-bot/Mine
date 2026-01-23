"""
Intraday 9:20 Strategy - Live Signal Monitoring
Monitors entry signals every 30 seconds during market hours (9:15 AM - 3:30 PM IST)
"""

from datetime import datetime, timedelta, time
from typing import Dict, List, Any, Optional
import logging
import threading
import time as time_module
from .intraday_9_20 import Intraday920Strategy

logger = logging.getLogger(__name__)


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
    MARKET_CLOSE = time(15, 30, 0)    # 3:30 PM
    MONITORING_INTERVAL = 30  # seconds
    
    def __init__(self, kite_instance, symbol: str = 'NIFTY'):
        """
        Initialize live signal monitor.
        
        Args:
            kite_instance: KiteConnect instance
            symbol: Trading symbol (NIFTY, BANKNIFTY, FINNIFTY)
        """
        self.kite = kite_instance
        self.symbol = symbol
        self.strategy = Intraday920Strategy(kite_instance)
        
        # Monitoring state
        self.is_monitoring = False
        self.monitor_thread = None
        self.active_trades = {}  # Track active trades: {side: {entry_info}}
        self.today_signals = []  # All signals generated today
        
        logger.info(f"Intraday 9:20 Live Signal Monitor initialized for {symbol}")
    
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
    
    def should_check_now(self) -> bool:
        """
        Determine if we should check for signals now based on 30-second intervals.
        
        Returns:
            True if current second is 0 or 30, False otherwise
        """
        current_second = datetime.now().second
        return current_second == 0 or current_second == 30
    
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
    
    def check_entry_signal_live(self, ce_token: int, pe_token: int, 
                               ce_high: float, pe_high: float) -> Dict[str, Any]:
        """
        Check for live entry signals (similar to backtest but for current candle).
        
        Args:
            ce_token: CE option token
            pe_token: PE option token
            ce_high: Reference high for CE
            pe_high: Reference high for PE
            
        Returns:
            Dictionary with entry signals
        """
        try:
            # Get latest 5-minute candle data
            ce_candles = self.strategy._get_latest_5min_candle(ce_token)
            pe_candles = self.strategy._get_latest_5min_candle(pe_token)
            
            # Analyze CE entry
            ce_signal = self._analyze_live_entry(ce_candles, pe_high, 'CE')
            
            # Analyze PE entry
            pe_signal = self._analyze_live_entry(pe_candles, ce_high, 'PE')
            
            return {
                'timestamp': datetime.now().isoformat(),
                'ce_signal': ce_signal,
                'pe_signal': pe_signal,
                'success': True
            }
            
        except Exception as e:
            logger.error(f"Error checking entry signals: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def _analyze_live_entry(self, candles: List[Dict], reference_high: float, side: str) -> Dict[str, Any]:
        """
        Analyze live entry for a single side (CE or PE).
        
        Args:
            candles: Latest candle data
            reference_high: Reference level for entry
            side: 'CE' or 'PE'
            
        Returns:
            Entry signal data
        """
        result = {
            'side': side,
            'has_signal': False,
            'entry_price': None,
            'sl': None,
            'target': None,
            'signal_time': datetime.now().isoformat()
        }
        
        if not candles or len(candles) == 0:
            result['reason'] = 'No candle data available'
            return result
        
        # Get latest candle
        latest_candle = candles[-1]
        candle_low = latest_candle.get('low', 0)
        candle_close = latest_candle.get('close', 0)
        
        # Entry condition: low < ref_high AND close > (ref_high + 5)
        entry_threshold = reference_high + 5
        close_ref_diff = abs(candle_close - reference_high)
        
        if (candle_low < reference_high and 
            candle_close > entry_threshold and 
            close_ref_diff < 20):
            
            result['has_signal'] = True
            result['entry_price'] = candle_close
            
            # Calculate SL and Target
            sl_data = self.strategy.calculate_sl_for_entry(
                candle_close, reference_high, ratio='1:2'
            )
            result['sl'] = sl_data.get('sl')
            result['target'] = sl_data.get('target')
            result['pnl_ratio'] = '1:2'
            
            logger.info(f"{side} LIVE ENTRY SIGNAL: Price {candle_close}, SL {result['sl']}, Target {result['target']}")
        else:
            result['reason'] = f'Entry conditions not met: low={candle_low:.2f}, close={candle_close:.2f}, ref={reference_high:.2f}'
        
        return result
    
    def update_active_trades(self, signals: Dict[str, Any]) -> None:
        """
        Update active trade state with new signals.
        
        Args:
            signals: Entry signals from check_entry_signal_live()
        """
        ce_signal = signals.get('ce_signal', {})
        pe_signal = signals.get('pe_signal', {})
        
        # Track CE entry
        if ce_signal.get('has_signal') and 'CE' not in self.active_trades:
            self.active_trades['CE'] = {
                'entry_price': ce_signal.get('entry_price'),
                'sl': ce_signal.get('sl'),
                'target': ce_signal.get('target'),
                'entry_time': datetime.now().isoformat(),
                'status': 'OPEN'
            }
            logger.info(f"🟢 CE trade opened at {ce_signal.get('entry_price')}")
        
        # Track PE entry
        if pe_signal.get('has_signal') and 'PE' not in self.active_trades:
            self.active_trades['PE'] = {
                'entry_price': pe_signal.get('entry_price'),
                'sl': pe_signal.get('sl'),
                'target': pe_signal.get('target'),
                'entry_time': datetime.now().isoformat(),
                'status': 'OPEN'
            }
            logger.info(f"🟢 PE trade opened at {pe_signal.get('entry_price')}")
    
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
    
    def _monitor_loop(self) -> None:
        """
        Main monitoring loop - runs in background thread.
        Checks every 30 seconds during market hours.
        """
        logger.info(f"Starting live signal monitoring for {self.symbol}")
        
        while self.is_monitoring:
            try:
                # Check if we should check (at 0 or 30 second mark)
                if not self.should_check_now():
                    time_module.sleep(1)
                    continue
                
                # Check if within market hours
                if not self.is_market_hours():
                    if self.is_market_day():
                        logger.debug("Outside market hours, waiting...")
                    time_module.sleep(1)
                    continue
                
                # Fetch live data
                live_data = self.strategy.get_intraday_920_data(self.symbol)
                
                if not live_data.get('success'):
                    logger.warning(f"Failed to fetch live data: {live_data.get('error')}")
                    time_module.sleep(self.MONITORING_INTERVAL)
                    continue
                
                # Extract strike info
                high_strike = live_data.get('high_strike', {})
                low_strike = live_data.get('low_strike', {})
                
                if not (high_strike.get('success') and low_strike.get('success')):
                    logger.warning("Strike data not available")
                    time_module.sleep(self.MONITORING_INTERVAL)
                    continue
                
                # Check signals for high strike
                high_signals = self.check_entry_signal_live(
                    high_strike.get('ce_token'),
                    high_strike.get('pe_token'),
                    high_strike.get('ce_high'),
                    high_strike.get('pe_high')
                )
                
                if high_signals.get('success'):
                    self.update_active_trades(high_signals)
                    self.log_signal(high_signals)
                    
                    ce_sig = high_signals.get('ce_signal', {})
                    pe_sig = high_signals.get('pe_signal', {})
                    
                    if ce_sig.get('has_signal'):
                        logger.info(f"📊 HIGH STRIKE CE SIGNAL: {ce_sig.get('entry_price')}")
                    
                    if pe_sig.get('has_signal'):
                        logger.info(f"📊 HIGH STRIKE PE SIGNAL: {pe_sig.get('entry_price')}")
                
                # Check signals for low strike
                low_signals = self.check_entry_signal_live(
                    low_strike.get('ce_token'),
                    low_strike.get('pe_token'),
                    low_strike.get('ce_high'),
                    low_strike.get('pe_high')
                )
                
                if low_signals.get('success'):
                    self.update_active_trades(low_signals)
                    self.log_signal(low_signals)
                    
                    ce_sig = low_signals.get('ce_signal', {})
                    pe_sig = low_signals.get('pe_signal', {})
                    
                    if ce_sig.get('has_signal'):
                        logger.info(f"📊 LOW STRIKE CE SIGNAL: {ce_sig.get('entry_price')}")
                    
                    if pe_sig.get('has_signal'):
                        logger.info(f"📊 LOW STRIKE PE SIGNAL: {pe_sig.get('entry_price')}")
                
                # Wait for next interval
                time_module.sleep(self.MONITORING_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}", exc_info=True)
                time_module.sleep(self.MONITORING_INTERVAL)
    
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
        logger.info(f"Daily monitoring reset for {self.symbol}")
