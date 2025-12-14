import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import os
import sys
from typing import Dict, Any, List, Tuple, Optional, Union # Added typing imports

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from service.kite_service import KiteService
from service.cpr_service import CPRService

from dotenv import load_dotenv
load_dotenv()

class MultiCPRBacktest:
    # FIX: Define time constants for clarity and control
    TRADING_START = datetime.strptime('09:15:00', '%H:%M:%S').time()
    EOD_ENTRY_CUTOFF = datetime.strptime('15:15:00', '%H:%M:%S').time()
    EOD_EXIT_TIME = datetime.strptime('15:20:00', '%H:%M:%S').time() # Exit before market close (15:30)

    def __init__(self, kite_instance=None):
        # Using get_instance() ensures proper initialization
        self.kite_service = KiteService(kite_instance) 
        self.cpr_service = CPRService()
    
    def _format_datetime(self, dt: Any, default_time: str) -> str:
        """Format datetime with proper time or default"""
        if dt == '-':
            return '-'
        # FIX: Unified date check for cleaner type handling and ensuring time is included
        if isinstance(dt, pd.Timestamp) or isinstance(dt, datetime):
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        return str(dt)
    
    def _create_trade_record(self, entry_date, entry_price, target_price, stop_loss, 
                           exit_date, exit_price, exit_type, pnl, pnl_pct, signal_type=None) -> Dict[str, Any]:
        """Create standardized trade record"""
        return {
            'entry_date': self._format_datetime(entry_date, '09:15:00'),
            'entry_price': round(entry_price, 2),
            'target_price': round(target_price, 2),
            'stop_loss': round(stop_loss, 2),
            'exit_date': self._format_datetime(exit_date, '15:30:00'),
            'exit_price': exit_price if exit_price == '-' else round(exit_price, 2),
            'exit_type': exit_type,
            'pnl': pnl if pnl == '-' else round(pnl, 2),
            'pnl_pct': pnl_pct if pnl_pct == '-' else round(pnl_pct, 2),
            'signal_type': signal_type if signal_type else '-'
        }
    
    def _calculate_multi_cpr(self, data: pd.DataFrame, i: int) -> Tuple[float, float, float, float, float, float]:
        """
        FIX (Logic): Calculate CPR levels based on the *previous N trading days*
        using array slicing for robustness against holidays.
        
        'i' is the index of the current trading day in the daily_data DataFrame.
        We need data from index 0 up to i-1.
        """
        # Ensure enough data exists for daily CPR
        if i < 1:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        
        # --- 1. Daily CPR (previous trading day - index i-1) ---
        prev_candle = data.iloc[i-1]
        _, daily_bc, daily_tc = self.cpr_service.calculate_cpr(
            float(prev_candle['high']), float(prev_candle['low']), float(prev_candle['close'])
        )
        
        # --- 2. Weekly CPR (Previous 5 trading days via slicing) ---
        lookback_days_w = 5
        start_w = max(0, i - lookback_days_w) 
        week_data = data.iloc[start_w:i]
        
        if len(week_data) == lookback_days_w:
            week_high = float(week_data['high'].max())
            week_low = float(week_data['low'].min())
            week_close = float(week_data['close'].iloc[-1])
            _, weekly_bc, weekly_tc = self.cpr_service.calculate_cpr(week_high, week_low, week_close)
        else:
            weekly_bc, weekly_tc = 0.0, 0.0

        # --- 3. Monthly CPR (Previous 20 trading days via slicing) ---
        lookback_days_m = 20
        start_m = max(0, i - lookback_days_m) 
        month_data = data.iloc[start_m:i]

        if len(month_data) == lookback_days_m:
            month_high = float(month_data['high'].max())
            month_low = float(month_data['low'].min())
            month_close = float(month_data['close'].iloc[-1])
            _, monthly_bc, monthly_tc = self.cpr_service.calculate_cpr(month_high, month_low, month_close)
        else:
            monthly_bc, monthly_tc = 0.0, 0.0
        
        return daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc
    
    def _check_entry_signal(self, current_candle, daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc) -> Optional[str]:
        """Check if entry signal conditions are met"""
        # Ensure CPR values are non-zero before checking (implies sufficient lookback data)
        if any(c == 0.0 for c in [daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc]):
            return None
            
        close = float(current_candle['close'])
        low = float(current_candle['low'])
        high = float(current_candle['high'])
        open_price = float(current_candle['open'])
        
        # Standard signal logic remains the same
        candle_body = abs(close - open_price)
        candle_range = high - low
        body_ratio = candle_body / candle_range if candle_range > 0 else 0
        
        is_strong_red = close < open_price and body_ratio > 0.6
        is_strong_green = close > open_price and body_ratio > 0.6
        
        buy_signal = (
            daily_tc > weekly_bc and
            weekly_tc > monthly_bc and
            close > daily_tc and
            close > weekly_tc and
            close > monthly_tc and
            # Low must have touched or crossed one of the CPR tops/bottoms
            (low <= daily_tc or low <= weekly_tc or low <= monthly_tc) and
            not is_strong_red
        )
        
        sell_signal = (
            daily_bc < weekly_tc and
            weekly_bc < monthly_tc and
            close < daily_bc and
            close < weekly_bc and
            close < monthly_bc and
            # High must have touched or crossed one of the CPR tops/bottoms
            (high >= daily_bc or high >= weekly_bc or high >= monthly_bc) and
            not is_strong_green
        )
        
        if buy_signal:
            return 'BUY'
        elif sell_signal:
            return 'SELL'
        else:
            return None
    
    def _calculate_statistics(self, trades: List[Dict[str, Any]]) -> Dict[str, Union[int, float]]:
        """
        FIX (Logic): Calculate backtest statistics by iterating trades chronologically 
        for accurate max consecutive streak calculation.
        """
        closed_trades = [t for t in trades if t['pnl'] != '-']
        total_trades = len(closed_trades)
        if total_trades == 0:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'total_pnl': 0.0, 'win_rate': 0.0, 'avg_pnl': 0.0,
                'max_consecutive_wins': 0, 'max_consecutive_losses': 0,
                'max_consecutive_wins_pnl': 0.0, 'max_consecutive_losses_pnl': 0.0
            }
        
        # FIX: Sort trades chronologically for accurate streak calculation
        chronological_trades = sorted(closed_trades, key=lambda x: x['entry_date'])

        winning_trades = [t for t in closed_trades if t['pnl'] > 0]
        losing_trades = [t for t in closed_trades if t['pnl'] < 0]
        total_pnl = sum([t['pnl'] for t in closed_trades])
        win_rate = (len(winning_trades) / total_trades * 100)
        
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        max_consecutive_wins_pnl = 0.0
        max_consecutive_losses_pnl = 0.0
        
        current_wins = 0
        current_losses = 0
        current_wins_pnl = 0.0
        current_losses_pnl = 0.0
        
        # Iterate chronologically for accurate streak calculation
        for trade in chronological_trades:
            if trade['pnl'] > 0:
                current_wins += 1
                current_wins_pnl += trade['pnl']
                current_losses = 0
                current_losses_pnl = 0.0
                
                if current_wins > max_consecutive_wins:
                    max_consecutive_wins = current_wins
                    max_consecutive_wins_pnl = current_wins_pnl
            else:
                current_losses += 1
                current_losses_pnl += trade['pnl']
                current_wins = 0
                current_wins_pnl = 0.0
                
                if current_losses > max_consecutive_losses:
                    max_consecutive_losses = current_losses
                    max_consecutive_losses_pnl = current_losses_pnl
        
        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'total_pnl': round(total_pnl, 2),
            'win_rate': round(win_rate, 2),
            'avg_pnl': round(total_pnl / total_trades, 2),
            'max_consecutive_wins': max_consecutive_wins,
            'max_consecutive_losses': max_consecutive_losses,
            'max_consecutive_wins_pnl': round(max_consecutive_wins_pnl, 2),
            'max_consecutive_losses_pnl': round(max_consecutive_losses_pnl, 2)
        }
    
    def backtest_multi_cpr(self, symbol: str, from_date: datetime, to_date: datetime, timeframe: str = '60minute') -> Dict[str, Any]:
        """
        Backtest multi-CPR strategy.
        FIX (Performance): Implements CPR caching for O(1) daily lookup.
        """
        try:
            trades: List[Dict[str, Any]] = []
            position: Optional[str] = None
            
            # Fetch data with a wider lookback margin (need at least 20 trading days before start date)
            daily_data_from = from_date - timedelta(days=60)
            fetch_to_date = to_date + timedelta(days=1)
            
            daily_data = self.kite_service.get_historical_data(symbol, daily_data_from, fetch_to_date, 'day')
            timeframe_data = self.kite_service.get_historical_data(symbol, from_date, fetch_to_date, timeframe)
            
            if daily_data is None or timeframe_data is None or len(daily_data) < 25:
                return {'success': False, 'error': 'Insufficient data'}
            
            # FIX (Performance): Pre-process daily data index map
            # Map date (Y-M-D) to its index in daily_data
            daily_date_map = {date.date(): i for i, date in enumerate(daily_data['date'])}
            daily_cpr_cache: Dict[datetime.date, Tuple[float, float, float, float, float, float]] = {}
            
            # --- Main Backtest Loop (High Performance with Caching) ---
            for _, candle in timeframe_data.iterrows():
                current_date: datetime = candle['date']
                current_day: datetime.date = current_date.date()
                
                # Check date range
                if not (from_date.date() <= current_day <= to_date.date()):
                    continue
                
                # Get or calculate CPR levels for the current day (O(1) lookup)
                cpr_levels: Tuple[float, float, float, float, float, float]
                if current_day not in daily_cpr_cache:
                    daily_idx = daily_date_map.get(current_day)
                    
                    if daily_idx is None or daily_idx < 20: 
                        # Skip day if not enough lookback data for Monthly CPR
                        continue
                    
                    # _calculate_multi_cpr uses data up to daily_idx-1
                    cpr_levels = self._calculate_multi_cpr(daily_data, daily_idx)
                    daily_cpr_cache[current_day] = cpr_levels
                else:
                    cpr_levels = daily_cpr_cache[current_day]

                daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc = cpr_levels
                
                # Skip if CPR levels are not valid (due to insufficient lookback)
                if daily_bc == 0.0 or weekly_bc == 0.0 or monthly_bc == 0.0:
                    continue

                if position is None:
                    candle_time = current_date.time()
                    
                    # Entry Cutoff
                    if candle_time < self.EOD_ENTRY_CUTOFF:
                        signal = self._check_entry_signal(candle, daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc)
                        
                        if signal:
                            position = signal
                            entry_price = float(candle['close'])
                            
                            trade = self._create_trade_record(
                                candle['date'], entry_price, 0, 0,
                                '-', '-', 'Open', '-', '-', signal
                            )
                            trades.append(trade)
                
                elif trades and trades[-1]['exit_type'] == 'Open':
                    last_trade = trades[-1]
                    signal_type = last_trade['signal_type']
                    
                    exit_condition_met = False
                    exit_reason = 'Stop Loss'
                    candle_close = float(candle['close'])
                    
                    # Exit: Stop Loss (Daily CPR)
                    if signal_type == 'BUY' and candle_close < daily_bc:
                        exit_condition_met = True
                    elif signal_type == 'SELL' and candle_close > daily_tc:
                        exit_condition_met = True
                    
                    # FIX (Feature Add): Exit: End of Day (Forced exit at EOD_EXIT_TIME)
                    eod_hit = current_date.time() >= self.EOD_EXIT_TIME
                    
                    if eod_hit and not exit_condition_met:
                        exit_condition_met = True
                        exit_reason = 'EOD Exit'
                        
                    if exit_condition_met:
                        exit_price = candle_close
                        pnl = exit_price - last_trade['entry_price'] if signal_type == 'BUY' else last_trade['entry_price'] - exit_price
                        pnl_pct = (pnl / last_trade['entry_price']) * 100 if last_trade['entry_price'] != 0 else 0
                        
                        # Use the actual candle time/date for exit
                        last_trade['exit_date'] = self._format_datetime(candle['date'], '15:30:00')
                        last_trade['exit_price'] = round(exit_price, 2)
                        last_trade['exit_type'] = exit_reason
                        last_trade['pnl'] = round(pnl, 2)
                        last_trade['pnl_pct'] = round(pnl_pct, 2)
                        
                        position = None
            
            # FIX: Ensure final trades list is chronological for standard reporting
            trades.sort(key=lambda x: x['entry_date']) 

            return {
                'success': True,
                'trades': trades,
                'statistics': self._calculate_statistics(trades)
            }
        
        except Exception as e:
            import traceback
            traceback.print_exc() 
            return {'success': False, 'error': str(e)}