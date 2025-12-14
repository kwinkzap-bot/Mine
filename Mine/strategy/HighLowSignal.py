import pandas as pd
from typing import Optional, Dict, Any, Tuple

class HighLowSignal:
    def __init__(self):
        self.stop_loss_level: Optional[float] = None
    
    def check_ce_buy_conditions(self, ce_data: pd.DataFrame, pe_data: pd.DataFrame, ce_prev_high: float, ce_prev_low: float, pe_prev_high: float, pe_prev_low: float) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check CE buy conditions"""
        self.stop_loss_level = None # Reset SL for the new check
        
        if ce_data.empty or pe_data.empty:
            return False, None
            
        min_len = min(len(ce_data), len(pe_data))
        ce_open = ce_data['open'].iloc[0] # Day's opening price for CE
        
        for i in range(1, min_len):
            # Check if time is after 3:00 PM IST - no new trades
            current_time = ce_data.index[i].time()
            if current_time >= pd.Timestamp('15:00:00').time():
                continue
            
            ce_current = ce_data['close'].iloc[i]
            pe_current = pe_data['close'].iloc[i]
            
            # Check stop loss level condition for PE data (opposite option)
            pe_open_current = pe_data['open'].iloc[i]
            pe_low_current = pe_data['low'].iloc[i]
            pe_high_current = pe_data['high'].iloc[i]
            
            # This logic sets SL based on PE breaking its previous high
            if (pe_current > pe_prev_high and 
                pe_open_current < pe_prev_high and 
                pe_low_current < pe_prev_high and 
                pe_high_current > pe_prev_high):
                # Stop Loss is set to the low of the candle that crosses the previous day's high
                self.stop_loss_level = pe_low_current 
            
            # CE buy conditions
            if (ce_current > ce_open and 
                ce_current > pe_prev_high and 
                pe_current < ce_prev_low and 
                ce_prev_high > pe_prev_high and
                self.stop_loss_level is not None):
                
                return True, {
                    'entry_time': ce_data.index[i],
                    'entry_price': ce_current,
                    'option_type': 'CE',
                    'target': ce_prev_high # Target is CE's previous day high
                }
        return False, None
    
    def check_pe_buy_conditions(self, pe_data: pd.DataFrame, ce_data: pd.DataFrame, pe_prev_high: float, pe_prev_low: float, ce_prev_high: float, ce_prev_low: float) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check PE buy conditions"""
        self.stop_loss_level = None # Reset SL for the new check
        
        if pe_data.empty or ce_data.empty:
            return False, None
            
        min_len = min(len(pe_data), len(ce_data))
        pe_open = pe_data['open'].iloc[0] # Day's opening price for PE
        
        for i in range(1, min_len):
            # Check if time is after 3:00 PM IST - no new trades
            current_time = pe_data.index[i].time()
            if current_time >= pd.Timestamp('15:00:00').time():
                continue
            
            pe_current = pe_data['close'].iloc[i]
            ce_current = ce_data['close'].iloc[i]
            
            # Check stop loss level condition for CE data (opposite option)
            ce_open_current = ce_data['open'].iloc[i]
            ce_low_current = ce_data['low'].iloc[i]
            ce_high_current = ce_data['high'].iloc[i]
            
            # This logic sets SL based on CE breaking its previous high
            if (ce_current > ce_prev_high and 
                ce_open_current < ce_prev_high and 
                ce_low_current < ce_prev_high and 
                ce_high_current > ce_prev_high):
                # Stop Loss is set to the low of the candle that crosses the previous day's high
                self.stop_loss_level = ce_low_current
            
            # PE buy conditions
            if (pe_current > pe_open and 
                pe_current > ce_prev_high and 
                ce_current < pe_prev_low and 
                pe_prev_high > ce_prev_high and
                self.stop_loss_level is not None):
                
                return True, {
                    'entry_time': pe_data.index[i],
                    'entry_price': pe_current,
                    'option_type': 'PE',
                    'target': pe_prev_high # Target is PE's previous day high
                }
        return False, None
    
    def execute_trade(self, entry_signal: Dict[str, Any], option_data: pd.DataFrame) -> Dict[str, Any]:
        """Execute trade with target and stop loss"""
        try:
            entry_idx = option_data.index.get_loc(entry_signal['entry_time'])
        except KeyError:
            # If exact timestamp not found, find nearest
            entry_idx = option_data.index.get_indexer([entry_signal['entry_time']], method='nearest')[0]
        
        stop_loss_candle_low = None
        
        for i in range(entry_idx + 1, len(option_data)):
            current_price = option_data['close'].iloc[i]
            
            # Force exit at 3:25 PM (5 minutes before market close)
            current_time = option_data.index[i].time()
            if current_time >= pd.Timestamp('15:25:00').time():
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': current_price,
                    'exit_reason': 'Market Close',
                    'pnl': current_price - entry_signal['entry_price']
                }
            
            # Check target hit (assumes exit at target price)
            if current_price >= entry_signal['target']:
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': entry_signal['target'],
                    'exit_reason': 'Target',
                    'pnl': entry_signal['target'] - entry_signal['entry_price']
                }
            
            # Check if current candle crossed above the opposite option's previous day high
            if self.stop_loss_level is not None and option_data['high'].iloc[i] >= self.stop_loss_level and stop_loss_candle_low is None:
                stop_loss_candle_low = option_data['low'].iloc[i]
            
            # Stop loss: 5min candle closed below the candle low that crossed above the opposite high
            if stop_loss_candle_low is not None and current_price < stop_loss_candle_low:
                return {
                    'exit_time': option_data.index[i],
                    'exit_price': current_price,
                    'exit_reason': 'Stop Loss',
                    'pnl': current_price - entry_signal['entry_price']
                }
        
        # No exit found - use EOD close as final exit
        final_price = option_data['close'].iloc[-1]
        return {
            'exit_time': option_data.index[-1],
            'exit_price': final_price,
            'exit_reason': 'EOD',
            'pnl': final_price - entry_signal['entry_price']
        }