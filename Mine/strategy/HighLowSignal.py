import pandas as pd
from datetime import datetime, time
from typing import Optional, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class HighLowSignal:
    def __init__(self):
        self.stop_loss_level: Optional[float] = None
        self.entry_price: Optional[float] = None
        self.current_sl: Optional[float] = None
        self.in_trade: bool = False
        self.entry_option_type: Optional[str] = None  # Track which option we entered

    def is_valid_entry_time(self, current_time: time) -> bool:
        """Check if current time is within valid entry window: 9:15 AM to 3:20 PM IST"""
        market_open = time(9, 15)
        market_close = time(15, 20)  # 3:20 PM
        return market_open <= current_time <= market_close

    def is_five_minute_candle(self, candle_time: datetime) -> bool:
        """Check if candle is at 5-minute intervals (9:15, 9:20, 9:25, etc.)"""
        minutes = candle_time.minute
        return minutes % 5 == 0

    def is_market_close_time(self, current_time: time) -> bool:
        """Check if it's 3:20 PM IST (market close - exit all trades)"""
        market_close = time(15, 20)
        return current_time >= market_close

    def check_ce_buy_conditions(self, ce_data: pd.DataFrame, pe_data: pd.DataFrame, ce_prev_high: float, ce_prev_low: float, pe_prev_high: float, pe_prev_low: float) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check CE buy conditions with High-Low logic
        
        Entry logic between 9:15 AM to 3:20 PM IST, every 5 minutes (9:15, 9:20, 9:25, ..., 3:15, 3:20)
        
        CE Entry Condition:
        - If Open Price < PE PDH:
            - CE crossed above and Closed above PE PDH (CE open <= PE PDH and CE close > PE PDH)
            - PE must trade below CE PDL
        - If Open Price >= PE PDH:
            - CE touched and Closed above PE PDH (CE high >= PE PDH and CE close > PE PDH)
            - PE must trade below CE PDL
            OR
            - CE crossed above and Closed above PE PDH (CE open <= PE PDH and CE close > PE PDH)
            - PE must trade below CE PDL
        
        Target: CE PDH
        Stop Loss: 20 points from entry
        Trailing SL: Every 20 points profit
        """
        self.stop_loss_level = None
        self.is_Current_day_touch_CE_PDH = False
        
        if ce_data.empty or pe_data.empty:
            return False, None
        
        min_len = min(len(ce_data), len(pe_data))
        ce_open = ce_data['open'].iloc[0]  # Day's opening price for CE
        
        for i in range(1, min_len):
            current_time = pd.Timestamp(ce_data.index[i]).time()
            candle_time = pd.Timestamp(ce_data.index[i])
            
            # Check if within valid entry time (9:15 AM to 3:20 PM IST)
            if not self.is_valid_entry_time(current_time):
                continue
            
            # Check if on 5-minute interval (including 3:20 PM)
            if not self.is_five_minute_candle(candle_time):
                continue
            
            # Exit all trades at 3:20 PM IST
            if self.is_market_close_time(current_time):
                if self.in_trade:
                    logger.info("Exiting trade at market close (3:20 PM IST)")
                    self.in_trade = False
                continue
            
            # Only one trade at a time
            if self.in_trade or self.is_Current_day_touch_CE_PDH:
                continue
            
            ce_current = ce_data['close'].iloc[i]
            ce_high = ce_data['high'].iloc[i]
            ce_low = ce_data['low'].iloc[i]
            ce_open_candle = ce_data['open'].iloc[i]
            pe_low = pe_data['low'].iloc[i]
            
            # CE Entry Logic: Only if CE PDH > PE PDH
            # if ce_prev_high < pe_prev_high or pe_prev_high > ce_prev_low:
            if ce_prev_high < pe_prev_high:
                continue
            
            # Check if CE has touched its PDH on the current day
            if ce_high >= ce_prev_high:
                self.is_Current_day_touch_CE_PDH = True
                continue
            
            # Case 1: If Day's Open Price is below PE PDH
            if ce_open < pe_prev_high:
                # CE crossed above and Closed above PE PDH
                # Crossed = current candle open <= PE PDH, close > PE PDH, and low <= PE PDH
                if ce_open_candle <= pe_prev_high and ce_current > pe_prev_high and ce_low <= pe_prev_high and pe_low < ce_prev_low:
                    logger.info(f"CE Entry: Open < PE_PDH, crossed above PE_PDH at {ce_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = ce_current
                    self.current_sl = ce_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'CE'
                    
                    return True, {
                        'entry_time': ce_data.index[i],
                        'entry_price': ce_current,
                        'option_type': 'CE',
                        'target': ce_prev_high,  # Target is CE PDH
                        'stop_loss': self.current_sl
                    }
            
            # Case 2: If Day's Open Price is >= PE PDH
            elif ce_open >= pe_prev_high:
                # Sub-case A: CE touched and Closed above PE PDH
                # Touched = high >= PE PDH, low <= PE PDH and close > PE PDH
                if ce_high >= pe_prev_high and ce_low <= pe_prev_high and ce_current > pe_prev_high and pe_low < ce_prev_low:
                    logger.info(f"CE Entry: Open >= PE_PDH, touched and closed above PE_PDH at {ce_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = ce_current
                    self.current_sl = ce_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'CE'
                    
                    return True, {
                        'entry_time': ce_data.index[i],
                        'entry_price': ce_current,
                        'option_type': 'CE',
                        'target': ce_prev_high,  # Target is CE PDH
                        'stop_loss': self.current_sl
                    }
                
                # Sub-case B: CE crossed above and Closed above PE PDH
                # Crossed = open <= PE PDH, close > PE PDH, and low <= PE PDH
                elif ce_open_candle <= pe_prev_high and ce_current > pe_prev_high and ce_low <= pe_prev_high and pe_low < ce_prev_low:
                    logger.info(f"CE Entry: Open >= PE_PDH, crossed above PE_PDH at {ce_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = ce_current
                    self.current_sl = ce_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'CE'
                    
                    return True, {
                        'entry_time': ce_data.index[i],
                        'entry_price': ce_current,
                        'option_type': 'CE',
                        'target': ce_prev_high,  # Target is CE PDH
                        'stop_loss': self.current_sl
                    }
        
        return False, None
    
    def check_pe_buy_conditions(self, pe_data: pd.DataFrame, ce_data: pd.DataFrame, pe_prev_high: float, pe_prev_low: float, ce_prev_high: float, ce_prev_low: float) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check PE buy conditions with High-Low logic
        
        Entry logic between 9:15 AM to 3:20 PM IST, every 5 minutes (9:15, 9:20, 9:25, ..., 3:15, 3:20)
        
        PE Entry Condition:
        - If Open Price < CE PDH:
            - PE crossed above and Closed above CE PDH (PE open <= CE PDH and PE close > CE PDH)
            - CE must trade below PE PDL
        - If Open Price >= CE PDH:
            - PE touched and Closed above CE PDH (PE high >= CE PDH and PE close > CE PDH)
            - CE must trade below PE PDL
            OR
            - PE crossed above and Closed above CE PDH (PE open <= CE PDH and PE close > CE PDH)
            - CE must trade below PE PDL
        
        Target: PE PDH
        Stop Loss: 20 points from entry
        Trailing SL: Every 20 points profit
        """
        self.stop_loss_level = None
        self.is_Current_day_touch_PE_PDH = False
        
        if pe_data.empty or ce_data.empty:
            return False, None
        
        min_len = min(len(pe_data), len(ce_data))
        pe_open = pe_data['open'].iloc[0]  # Day's opening price for PE
        
        for i in range(1, min_len):
            current_time = pd.Timestamp(pe_data.index[i]).time()
            candle_time = pd.Timestamp(pe_data.index[i])
            
            # Check if within valid entry time (9:15 AM to 3:20 PM IST)
            if not self.is_valid_entry_time(current_time):
                continue
            
            # Check if on 5-minute interval (including 3:20 PM)
            if not self.is_five_minute_candle(candle_time):
                continue
            
            # Exit all trades at 3:20 PM IST
            if self.is_market_close_time(current_time):
                if self.in_trade:
                    logger.info("Exiting trade at market close (3:20 PM IST)")
                    self.in_trade = False
                continue
            
            # Only one trade at a time
            if self.in_trade or self.is_Current_day_touch_PE_PDH:
                continue
            
            pe_current = pe_data['close'].iloc[i]
            pe_high = pe_data['high'].iloc[i]
            pe_low = pe_data['low'].iloc[i]
            pe_open_candle = pe_data['open'].iloc[i]
            ce_low = ce_data['low'].iloc[i]
            
            # PE Entry Logic: Only if PE PDH > CE PDH
            # if pe_prev_high <= ce_prev_high or pe_prev_high < ce_prev_low:
            if pe_prev_high <= ce_prev_high:
                continue
            
            # Check if PE has touched its PDH on the current day
            if pe_high >= pe_prev_high:
                self.is_Current_day_touch_PE_PDH = True
                continue
            
            # Case 1: If Day's Open Price is below CE PDH
            if pe_open < ce_prev_high:
                # PE crossed above and Closed above CE PDH
                # Crossed = current candle open <= CE PDH, close > CE PDH, and low <= CE PDH
                if pe_open_candle <= ce_prev_high and pe_current > ce_prev_high and pe_low <= ce_prev_high and ce_low < pe_prev_low:
                    logger.info(f"PE Entry: Open < CE_PDH, crossed above CE_PDH at {pe_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = pe_current
                    self.current_sl = pe_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'PE'
                    
                    return True, {
                        'entry_time': pe_data.index[i],
                        'entry_price': pe_current,
                        'option_type': 'PE',
                        'target': pe_prev_high,  # Target is PE PDH
                        'stop_loss': self.current_sl
                    }
            
            # Case 2: If Day's Open Price is >= CE PDH
            elif pe_open >= ce_prev_high:
                # Sub-case A: PE touched and Closed above CE PDH
                # Touched = high >= CE PDH, low <= CE PDH and close > CE PDH
                if pe_high >= ce_prev_high and pe_low <= ce_prev_high and pe_current > ce_prev_high and ce_low < pe_prev_low:
                    logger.info(f"PE Entry: Open >= CE_PDH, touched and closed above CE_PDH at {pe_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = pe_current
                    self.current_sl = pe_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'PE'
                    
                    return True, {
                        'entry_time': pe_data.index[i],
                        'entry_price': pe_current,
                        'option_type': 'PE',
                        'target': pe_prev_high,  # Target is PE PDH
                        'stop_loss': self.current_sl
                    }
                
                # Sub-case B: PE crossed above and Closed above CE PDH
                # Crossed = open <= CE PDH, close > CE PDH, and low <= CE PDH
                elif pe_open_candle <= ce_prev_high and pe_current > ce_prev_high and pe_low <= ce_prev_high and ce_low < pe_prev_low:
                    logger.info(f"PE Entry: Open >= CE_PDH, crossed above CE_PDH at {pe_current:.2f}, Entry Time: {candle_time.strftime('%H:%M')}")
                    self.entry_price = pe_current
                    self.current_sl = pe_current - 20  # 20 points SL
                    self.in_trade = True
                    self.entry_option_type = 'PE'
                    
                    return True, {
                        'entry_time': pe_data.index[i],
                        'entry_price': pe_current,
                        'option_type': 'PE',
                        'target': pe_prev_high,  # Target is PE PDH
                        'stop_loss': self.current_sl
                    }
        
        return False, None
    
    def execute_trade(self, entry_signal: Dict[str, Any], option_data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Execute trade with target, SL, and trailing SL logic
        
        Entry: entry_signal dictionary with entry_price, target, stop_loss
        Target: entry_signal['target'] (same option PDH)
        SL: 20 points from entry
        Trailing SL: Every 20 points profit, SL trails by 20 points
        Exit Checks: Only at 5-minute intervals AND at 3:20 PM IST
        Exit: At 3:20 PM IST market close
        
        Check priority:
        1. Market close (3:20 PM) - Always exit
        2. Stop loss hit
        3. Target hit
        4. Trailing SL update
        """
        if not entry_signal or option_data.empty:
            return None
        
        entry_price = entry_signal['entry_price']
        target = entry_signal.get('target', None)
        current_sl = entry_signal.get('stop_loss', entry_price - 20)
        self.stop_loss_level = current_sl  # Store for logging
        
        # Find entry index
        entry_idx = 0
        for i, ts in enumerate(option_data.index):
            if ts == entry_signal['entry_time']:
                entry_idx = i
                break
        
        # Iterate through candles after entry
        for i in range(entry_idx + 1, len(option_data)):
            candle_time = pd.Timestamp(option_data.index[i])
            current_time = candle_time.time()
            candle_high = option_data['high'].iloc[i]
            candle_low = option_data['low'].iloc[i]
            candle_close = option_data['close'].iloc[i]
            
            # ALWAYS check market close (3:20 PM) - exit immediately at market close
            if self.is_market_close_time(current_time):
                self.in_trade = False
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': candle_close,
                    'exit_reason': 'Market Close',
                    'pnl': candle_close - entry_price
                }
            
            # Only check exit conditions at 5-minute intervals
            if not self.is_five_minute_candle(candle_time):
                continue
            
            # Check stop loss hit first (use LOW of candle)
            # SL is hit if low touches or goes below SL level
            if candle_low <= current_sl:
                self.in_trade = False
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': current_sl,  # Exit at SL level
                    'exit_reason': 'Stop Loss',
                    'pnl': current_sl - entry_price
                }
            
            # Check target hit (use HIGH of candle)
            # Target is hit if high touches or goes above target level
            if target and candle_high >= target:
                self.in_trade = False
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': target,  # Exit at target level
                    'exit_reason': 'Target',
                    'pnl': target - entry_price
                }
            
            # Trailing SL: Every 20 points of profit, trail SL by 20 points
            # Calculate profit based on current close
            profit = candle_close - entry_price
            if profit >= 20:
                # Number of completed 20-point increments
                increments = int(profit / 20)
                # New SL should be at: entry_price + (increments - 1) * 20
                # E.g., at 20 pts profit: SL moves to entry (increments=1, so entry + 0*20)
                # E.g., at 40 pts profit: SL moves to entry + 20 (increments=2, so entry + 1*20)
                new_sl = entry_price + (increments - 1) * 20
                if new_sl > current_sl:
                    current_sl = new_sl
                    self.stop_loss_level = current_sl
                    logger.info(f"Trailing SL updated to {current_sl:.2f} at {candle_time.strftime('%H:%M')} (Price: {candle_close:.2f}, Profit: {profit:.2f})")
        
        # No exit found - use last 5-min candle close as final exit
        final_price = option_data['close'].iloc[-1]
        self.in_trade = False
        return {
            'exit_time': option_data.index[-1],
            'exit_price': final_price,
            'exit_reason': 'EOD',
            'pnl': final_price - entry_price
        }
