import pandas as pd
from datetime import datetime, timedelta
import time
import sys
import os
from typing import Optional, Tuple, Dict, Any, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from service.kite_service import KiteService
from service.cpr_service import CPRService
from service.trade_service import TradeService

class MultiCPRLive:
    # FIX: Use constants for clear time handling
    TRADING_START = datetime.strptime('09:15:00', '%H:%M:%S').time()
    EOD_ENTRY_CUTOFF = datetime.strptime('15:15:00', '%H:%M:%S').time()
    EOD_EXIT_TIME = datetime.strptime('15:20:00', '%H:%M:%S').time() 
    EOD_CLOSE = datetime.strptime('15:30:00', '%H:%M:%S').time()

    def __init__(self, kite_instance: KiteConnect, symbol: str = 'NIFTY', timeframe: str = '60minute', quantity: int = 1):
        self.kite_service = KiteService(kite_instance)
        self.cpr_service = CPRService()
        self.trade_service = TradeService(kite_instance)
        self.symbol = symbol
        self.timeframe = timeframe
        self.quantity = quantity
        self.position: Optional[str] = None
        self.entry_price: Optional[float] = None
        self.last_check_hour: int = -1 # Track last hour a signal check was performed
        self.daily_cpr_levels: Dict[str, float] = {} # Cache CPR levels for the day

    def _get_previous_period_ohlc(self, daily_data: pd.DataFrame, lookback_days: int) -> Tuple[float, float, float]:
        """
        FIX (Logic): Calculates OHLC for the specified number of previous *closed* trading days.
        Assumes the last row of daily_data is today's incomplete candle if market is open.
        """
        # We need data from index 0 up to -2 (the day before today's trading day)
        # Then, we slice the last 'lookback_days' from that set.
        
        # If market is open, the last candle is incomplete. We need data up to index -2.
        closed_data = daily_data.iloc[:-1] 
        
        if len(closed_data) < lookback_days: 
            return 0.0, 0.0, 0.0

        # Slice the last 'lookback_days' from the *closed* data
        lookback_data = closed_data.iloc[-lookback_days:]
        
        high = float(lookback_data['high'].max())
        low = float(lookback_data['low'].min())
        # Use the close of the last closed day in the lookback period
        close = float(lookback_data['close'].iloc[-1])
        
        return high, low, close

    def _calculate_multi_cpr(self, daily_data: pd.DataFrame):
        """
        FIX (Logic): Calculates Daily, Weekly (5-day), and Monthly (20-day) CPR based on 
        previous N *closed* trading days.
        """
        if len(daily_data) < 21: # Need 20 closed days + today's incomplete day (total 21 minimum)
             return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

        # --- 1. Daily CPR (Previous Trading Day - index -2 is the last fully closed candle) ---
        prev_candle = daily_data.iloc[-2]
        _, daily_bc, daily_tc = self.cpr_service.calculate_cpr(
            float(prev_candle['high']), float(prev_candle['low']), float(prev_candle['close'])
        )
        
        # --- 2. Weekly CPR (5 previous closed trading days) ---
        week_high, week_low, week_close = self._get_previous_period_ohlc(daily_data, 5)
        _, weekly_bc, weekly_tc = self.cpr_service.calculate_cpr(week_high, week_low, week_close)
        
        # --- 3. Monthly CPR (20 previous closed trading days) ---
        month_high, month_low, month_close = self._get_previous_period_ohlc(daily_data, 20)
        _, monthly_bc, monthly_tc = self.cpr_service.calculate_cpr(month_high, month_low, month_close)
        
        # Cache the result for the current trading day
        self.daily_cpr_levels = {
            'daily_bc': daily_bc, 'daily_tc': daily_tc,
            'weekly_bc': weekly_bc, 'weekly_tc': weekly_tc,
            'monthly_bc': monthly_bc, 'monthly_tc': monthly_tc
        }
        
        return daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc
    
    def _check_entry_signal(self, ltp: float, daily_bc: float, daily_tc: float, weekly_bc: float, weekly_tc: float, monthly_bc: float, monthly_tc: float) -> Optional[str]:
        """Check if entry signal conditions are met based on LTP."""
        # Check if CPRs were successfully calculated
        if any(c == 0.0 for c in [daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc]):
            return None
            
        buy_signal = (
            daily_tc > weekly_bc and
            weekly_tc > monthly_bc and
            ltp > daily_tc and
            ltp > weekly_tc and
            ltp > monthly_tc
        )
        
        sell_signal = (
            daily_bc < weekly_tc and
            weekly_bc < monthly_tc and
            ltp < daily_bc and
            ltp < weekly_bc and
            ltp < monthly_bc
        )
        
        if buy_signal:
            return 'BUY'
        elif sell_signal:
            return 'SELL'
        return None
    
    def _get_ltp(self) -> Optional[float]:
        """Fetches the Last Traded Price (LTP) for the symbol."""
        try:
            # FIX: Use a more robust check for data return
            ltp_data = self.kite_service.kite.ltp([f'NSE:{self.symbol}'])
            
            # Check if the symbol key exists and return the last_price
            return ltp_data.get(f'NSE:{self.symbol}', {}).get('last_price')
        except Exception as e:
            print(f"Error getting LTP: {e}")
            return None
    
    def _is_trading_time(self) -> bool:
        """Checks if current time is within 09:15:00 and 15:30:00."""
        now = datetime.now().time()
        return self.TRADING_START <= now <= self.EOD_CLOSE
    
    def _should_check_signal(self) -> bool:
        """
        Checks if the current time is around the 60-minute candle close time (e.g., HH:15),
        and prevents re-checking multiple times in the same hour.
        """
        now = datetime.now()
        
        # Check happens between 09:15 and 15:15 (inclusive, at the close of the candle)
        # Allow a 2-minute window (15:00 to 17:00 seconds past the hour)
        if (now.minute == 15 or now.minute == 16) and now.hour != self.last_check_hour:
             # Check entry cutoff
            if now.time() < self.EOD_ENTRY_CUTOFF:
                self.last_check_hour = now.hour # Mark this hour as checked
                return True
        
        # Reset the check marker after 17 minutes past the hour
        if now.minute > 17:
             self.last_check_hour = -1

        return False

    def run(self):
        print(f"Starting Multi-CPR Live Strategy for {self.symbol}")
        
        while True:
            try:
                now = datetime.now()
                
                if not self._is_trading_time():
                    print(f"Outside trading hours. Current time: {now.strftime('%H:%M:%S')}. Waiting...")
                    # Smart sleep until next trading day open if outside trading hours
                    if now.time() > self.EOD_CLOSE or now.time() < self.TRADING_START:
                        # Sleep for 10 minutes or until next open
                        time.sleep(600) 
                        continue
                    time.sleep(60)
                    continue

                ltp = self._get_ltp()
                if ltp is None:
                    print("Could not get LTP. Retrying in 30s.")
                    time.sleep(30)
                    continue
                
                # Check for signal only once per candle close, and only if no position is open
                should_check_entry = self.position is None and self._should_check_signal()

                # Get/Calculate CPR levels only when a fresh check is needed or levels are missing/stale
                if should_check_entry or not self.daily_cpr_levels:
                    # Fetch enough historical data for 20-day CPR calculation
                    daily_data_from = now - timedelta(days=45)
                    daily_data = self.kite_service.get_historical_data(
                        self.symbol, daily_data_from, now, 'day'
                    )
                    
                    if daily_data is None or len(daily_data) < 22: # Need at least 20 closed days + today + yesterday
                        print("Insufficient data for CPR calculation. Retrying in 60s.")
                        time.sleep(60)
                        continue
                    
                    # This call populates self.daily_cpr_levels
                    self._calculate_multi_cpr(daily_data)
                
                # Ensure levels are available for entry/exit
                if not self.daily_cpr_levels or any(c == 0.0 for c in self.daily_cpr_levels.values()):
                    time.sleep(60)
                    continue

                daily_bc = self.daily_cpr_levels.get('daily_bc', 0.0)
                daily_tc = self.daily_cpr_levels.get('daily_tc', 0.0)
                
                # --- Entry Logic ---
                if should_check_entry:
                    weekly_bc = self.daily_cpr_levels.get('weekly_bc', 0.0)
                    weekly_tc = self.daily_cpr_levels.get('weekly_tc', 0.0)
                    monthly_bc = self.daily_cpr_levels.get('monthly_bc', 0.0)
                    monthly_tc = self.daily_cpr_levels.get('monthly_tc', 0.0)
                    
                    print(f"Checking Signal at {now.strftime('%H:%M:%S')}. LTP: {ltp:.2f}, Daily BC: {daily_bc:.2f}, Daily TC: {daily_tc:.2f}")
                    
                    signal = self._check_entry_signal(ltp, daily_bc, daily_tc, weekly_bc, weekly_tc, monthly_bc, monthly_tc)
                    
                    if signal:
                        transaction_type = 'BUY' if signal == 'BUY' else 'SELL'
                        order_id = self.trade_service.place_order(self.symbol, transaction_type, self.quantity)
                        
                        if order_id:
                            self.position = signal
                            self.entry_price = ltp
                            print(f"Entered {signal} position at {ltp:.2f}")

                # --- Exit Logic ---
                elif self.position:
                    exit_reason: str = "Stop Loss" 
                    stop_loss_hit = False
                    
                    # Exit: Stop Loss (Daily CPR)
                    if self.position == 'BUY' and ltp < daily_bc and daily_bc != 0.0:
                        stop_loss_hit = True
                        exit_reason = "Stop Loss (Daily BC)"
                    elif self.position == 'SELL' and ltp > daily_tc and daily_tc != 0.0:
                        stop_loss_hit = True
                        exit_reason = "Stop Loss (Daily TC)"
                    
                    # FIX (Feature Add): Forced Exit near EOD
                    if now.time() >= self.EOD_EXIT_TIME and not stop_loss_hit:
                        stop_loss_hit = True 
                        exit_reason = "EOD Forced Exit"
                        
                    if stop_loss_hit:
                        order_id = self.trade_service.exit_position(self.symbol)
                        
                        if order_id and self.entry_price is not None:
                            pnl = ltp - self.entry_price if self.position == 'BUY' else self.entry_price - ltp
                            print(f"{exit_reason}: Exited {self.position} position at {ltp:.2f}, P&L: {pnl:.2f}")
                            self.position = None
                            self.entry_price = None
                        
                # Wait for next tick/minute
                time.sleep(60) 
                
            except Exception as e:
                print(f"Error in live strategy: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)