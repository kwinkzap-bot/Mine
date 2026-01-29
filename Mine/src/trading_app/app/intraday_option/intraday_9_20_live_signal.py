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
        
        # Extract option type from symbol (CE or PE)
        symbol = order_data.get('symbol', '')
        option_type = 'CE' if 'CE' in symbol else ('PE' if 'PE' in symbol else 'N/A')
        
        # Log to Excel
        target_val = order_data.get('target')
        sl_val = order_data.get('sl')
        
        excel_logger.log_trade(
            order_type=side,
            option_type=option_type,
            strike=order_data.get('strike', 0),
            entry_price=float(order_data.get('entry_price', 0)),
            current_price=float(order_data.get('entry_price', 0)),  # Same as entry on placement
            target=float(target_val) if target_val not in ['N/A', None] and target_val != '' else None,  # type: ignore
            stop_loss=float(sl_val) if sl_val not in ['N/A', None] and sl_val != '' else None,  # type: ignore
            pnl=None,  # No P&L on order placement
            status=excel_status,
            order_id=order_data.get('order_id'),
            notes=notes_str
        )
        
        logger.info(f"✅ Order logged to Excel: {side} {symbol}")
        
    except Exception as e:
        logger.error(f"Failed to write order placement log: {e}")


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
    MONITORING_INTERVAL = 30  # seconds
    
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
        self.today_signals = []  # All signals generated today
        self.last_entry_check_time = None  # Track last entry signal check to prevent duplicates
        
        # Trading configuration
        self.live_trading = live_trading  # False=demo mode, True=live orders
        self.risk_reward_ratio = '1:2-trail'  # Use 1:2 with trailing SL
        
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
    
    def should_check_now(self) -> bool:
        """
        Determine if we should check for SL/TARGET now based on 30-second intervals.
        
        Returns:
            True if current second is 0 or 30, False otherwise
        """
        current_second = datetime.now().second
        return current_second == 0 or current_second == 30
    
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
    
    def update_active_trades(self, signals: Dict[str, Any], strike_data: Optional[Dict[str, Any]] = None) -> None:
        """
        Update active trade state with new signals from strategy.check_entry_signal().
        Places buy orders when entry signals are detected.
        
        Uses signal data returned by the strategy which includes:
        - has_signal: Whether entry conditions were met
        - entry_price: Entry price at signal
        - sl: Stop loss calculated by strategy.calculate_sl_for_entry()
        - target: Target calculated by strategy.calculate_sl_for_entry()
        
        Args:
            signals: Entry signals from check_entry_signal_live()
            strike_data: Optional strike data for order placement {ce_token, pe_token, ce_high, pe_high}
        """
        ce_signal = signals.get('ce_signal', {})
        pe_signal = signals.get('pe_signal', {})
        
        # Track CE entry and place buy order
        if ce_signal.get('has_signal') and 'CE' not in self.active_trades:
            order_id = None
            if strike_data:
                order_id = self.place_buy_order(
                    side='CE',
                    token=strike_data.get('ce_token'),  # type: ignore
                    strike=int(strike_data.get('ce_high')),  # type: ignore
                    entry_price=ce_signal.get('entry_price')
                )
            
            self.active_trades['CE'] = {
                'entry_price': ce_signal.get('entry_price'),
                'entry_high': ce_signal.get('entry_high'),  # Reference high used for entry
                'sl': ce_signal.get('sl'),
                'target': ce_signal.get('target'),
                'entry_time': datetime.now().isoformat(),
                'order_id': order_id,
                'token': strike_data.get('ce_token') if strike_data else None,  # type: ignore
                'strike': int(strike_data.get('ce_high')) if strike_data and strike_data.get('ce_high') else None,  # type: ignore
                'status': 'OPEN',
                # Trailing SL state
                'target_hit': False,  # Track if target was hit
                'trailed_sl': ce_signal.get('sl'),  # Current trailed SL (starts at initial SL)
                'sl_distance': ce_signal.get('entry_price') - ce_signal.get('sl')  # Distance between entry and SL
            }
            logger.info(f"🟢 CE trade opened at {ce_signal.get('entry_price')} (Entry High: {ce_signal.get('entry_high')}, SL: {ce_signal.get('sl')}, Target: {ce_signal.get('target')}) | Order ID: {order_id if order_id else 'N/A'}")
        
        # Track PE entry and place buy order
        if pe_signal.get('has_signal') and 'PE' not in self.active_trades:
            order_id = None
            if strike_data:
                order_id = self.place_buy_order(
                    side='PE',
                    token=strike_data.get('pe_token'),  # type: ignore
                    strike=int(strike_data.get('pe_high')),  # type: ignore
                    entry_price=pe_signal.get('entry_price')
                )
            
            self.active_trades['PE'] = {
                'entry_price': pe_signal.get('entry_price'),
                'entry_high': pe_signal.get('entry_high'),  # Reference high used for entry
                'sl': pe_signal.get('sl'),
                'target': pe_signal.get('target'),
                'entry_time': datetime.now().isoformat(),
                'order_id': order_id,
                'token': strike_data.get('pe_token') if strike_data else None,  # type: ignore
                'strike': int(strike_data.get('pe_high')) if strike_data and strike_data.get('pe_high') else None,  # type: ignore
                'status': 'OPEN',
                # Trailing SL state
                'target_hit': False,  # Track if target was hit
                'trailed_sl': pe_signal.get('sl'),  # Current trailed SL (starts at initial SL)
                'sl_distance': pe_signal.get('entry_price') - pe_signal.get('sl')  # Distance between entry and SL
            }
            logger.info(f"🟢 PE trade opened at {pe_signal.get('entry_price')} (Entry High: {pe_signal.get('entry_high')}, SL: {pe_signal.get('sl')}, Target: {pe_signal.get('target')}) | Order ID: {order_id if order_id else 'N/A'}")
    
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
    
    def get_current_price(self, token: int) -> Optional[float]:
        """
        Fetch current LTP (Last Traded Price) for a given token.
        
        Args:
            token: Instrument token
            
        Returns:
            Current price or None if failed
        """
        try:
            quote = self.kite.quote([f"NFO:{token}"])
            if quote and f"NFO:{token}" in quote:
                ltp = quote[f"NFO:{token}"].get('last_price')
                return ltp
        except Exception as e:
            logger.error(f"Error fetching current price for token {token}: {e}")
            return None
    
    def check_sl_target_for_active_trades(self) -> None:
        """
        Monitor active trades and check if SL or Target has been hit.
        Implements 1:2 with Trailing SL logic:
        
        1. Before target hit: Exit if price <= initial SL
        2. When target hit: Move SL to entry price (lock in breakeven)
        3. After target hit: Trail SL by sl_distance for every sl_distance price moves above entry
        4. Exit when trailed SL is hit
        
        Automatically places SELL orders when conditions are met.
        """
        trades_to_close = []
        
        for side, trade in list(self.active_trades.items()):
            if trade.get('status') != 'OPEN':
                continue
            
            token = trade.get('token')
            strike = trade.get('strike')
            
            if not token or not strike:
                logger.warning(f"{side} trade missing token or strike info")
                continue
            
            # Fetch current price
            current_price = self.get_current_price(token)
            
            if current_price is None:
                logger.warning(f"Failed to fetch current price for {side} {strike}")
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
        
        # Remove closed trades from active trades
        for side in trades_to_close:
            if side in self.active_trades:
                del self.active_trades[side]
    
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
        2. SL/TARGET MONITORING: Checked every 30 seconds (0 or 30 second mark)
        
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
                    check_timestamp = datetime.now()
                    logger.info(f"[5-min Check] Checking entry signals for {self.symbol} at {check_timestamp.strftime('%H:%M:%S')}")
                    
                    # Fetch live data
                    live_data = self.strategy.get_intraday_920_data(self.symbol)
                    
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
                        
                        # LOG EVERY 5-MINUTE CHECK TO EXCEL (regardless of signal)
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
                            notes=f"High Strike Check" if high_strike.get('success') else "Strike data unavailable"
                        )
                        
                    else:
                        logger.warning(f"Failed to fetch live data: {live_data.get('error')}")
                        
                        # Log failed check to Excel
                        excel_logger.log_signal_check(
                            timestamp=check_timestamp,
                            notes=f"Failed to fetch data: {live_data.get('error', 'Unknown error')}"
                        )
                
                # ====== SL/TARGET CHECK (30-second intervals) ======
                if self.should_check_now():
                    logger.debug(f"[30-sec Check] Monitoring SL/Target for {self.symbol}")
                    
                    # Check active trades for SL/Target hits
                    if self.active_trades:
                        logger.debug(f"Active trades to monitor: {list(self.active_trades.keys())}")
                        self.check_sl_target_for_active_trades()
                    else:
                        logger.debug("No active trades to monitor")
                
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
        logger.info(f"Daily monitoring reset for {self.symbol}")
