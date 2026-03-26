import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta, date
from kiteconnect import KiteConnect
from dataclasses import dataclass
import os
from dotenv import load_dotenv
import logging
from typing import Optional, List, Dict, Tuple, cast, Union
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from trading_app.service.cpr_service import CPRService
from trading_app.service.kite_order_services import get_global_rate_limiter

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global persistent cache (survives between filter requests)
_global_cache = {}
_global_cache_lock = threading.Lock()

# Global cache for per-stock Historical Volatility data (valid 24h)
_global_hv_cache = {}

# Use the global rate limiter from kite_order_services
_rate_limiter = get_global_rate_limiter()

@dataclass
class CPRLevels:
    daily_pp: float
    daily_bc: float
    daily_tc: float
    weekly_pp: float
    weekly_bc: float
    weekly_tc: float
    monthly_pp: float
    monthly_bc: float
    monthly_tc: float
    monthly_s1: float
    monthly_r1: float
    monthly_s05: float
    monthly_r05: float
    monthly_cam_r3: float
    monthly_cam_s3: float
    yearly_pp: float
    yearly_bc: float
    yearly_tc: float
    current_price: float
    current_open: float
    current_high: float
    current_low: float
    previous_close: float
    prev_month_high: float
    prev_month_low: float

# Type aliases for clearer payload structure
SignalPayload = Dict[str, Union[float, str]]
WeeklyCrossPayload = Dict[str, List[SignalPayload]]
FilterResult = Dict[str, Union[List[SignalPayload], WeeklyCrossPayload, Dict[str, List[SignalPayload]]]]

class CPRFilterService:
    PERCENTAGE_DIFF_THRESHOLD = 3.0
    INDEX_SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
    API_RATE_LIMIT_DELAY = 0.34  # Using global rate limiter now
    MAX_WORKERS = 4  # Workers queue behind global rate limiter (3 req/sec max)

    CROSS_ABOVE_WEEKLY = "↗ CROSSED ABOVE WEEKLY CPR"
    CROSS_BELOW_WEEKLY = "↘ CROSSED BELOW WEEKLY CPR"
    BULLISH_REVERSAL = "🐂 BULLISH REVERSAL (M S1/Low)"
    BEARISH_REVERSAL = "🐻 BEARISH REVERSAL (M R1/High)"
    CPR_TOUCH_ABOVE = "🟢 MONTHLY CPR TOUCH (Closed Above)"
    CPR_TOUCH_BELOW = "🔴 MONTHLY CPR TOUCH (Closed Below)"

    def __init__(self, kite_instance=None, api_key=None):
        self.kite = kite_instance or KiteConnect(api_key or os.getenv("API_KEY"))
        if not kite_instance:
            token = os.getenv("ACCESS_TOKEN")
            if token:
                self.kite.set_access_token(token)
        
        self._instruments = []
        self._fo_stocks = None
        # Use global cache for persistence between requests
        self._historical_data_cache = _global_cache
        self._cache_lock = _global_cache_lock
        self._hv_cache = _global_hv_cache  # Shared HV cache across requests
        self._load_instruments()

    def _rate_limit(self):
        """Use the global rate limiter to ensure 3 req/sec across entire application."""
        _rate_limiter.wait()

    def _load_instruments(self):
        if not self._instruments:
            self._rate_limit()
            try:
                self._instruments = self.kite.instruments('NSE')
                logger.info(f"Loaded {len(self._instruments)} instruments")
            except Exception as e:
                logger.error(f"Instruments load failed: {e}")
                self._instruments = []

    def get_token(self, symbol: str) -> Optional[int]:
        if not self._instruments:
            self._load_instruments()
        for inst in self._instruments:
            if inst.get('tradingsymbol') == symbol and inst.get('instrument_type') == 'EQ':
                return inst.get('instrument_token')
        return None

    def get_hist_data(self, symbol: str, days: int = 300, interval='day', end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        """Fetch historical data for a symbol. Default 300 days for CPR calculations."""
        end_dt = end_date if end_date else datetime.now()
        start_dt = end_dt - timedelta(days=days)
        
        key = f"{symbol}_{start_dt.date()}_{end_dt.date()}_{interval}"
        with self._cache_lock:
            if key in self._historical_data_cache: 
                return self._historical_data_cache[key]

        token = self.get_token(symbol)
        if not token:
            return None

        self._rate_limit()
        try:
            start_str = start_dt.strftime('%Y-%m-%d')
            end_str = end_dt.strftime('%Y-%m-%d')
            data = self.kite.historical_data(token, start_str, end_str, interval)
            if not data:
                return None
            
            df = pd.DataFrame(data).set_index('date').astype(float)
            df.index = pd.to_datetime(df.index)
            
            with self._cache_lock:
                self._historical_data_cache[key] = df
            
            return df
        except Exception as e:
            logger.error(f"Hist data failed for {symbol}: {e}")
            return None


    def get_prev_week_range(self, reference_date: datetime) -> Tuple[datetime, datetime]:
        """
        Get the Mon-Fri range of the week BEFORE the reference_date.
        """
        # Calculate the start of the current week (Monday)
        current_week_start = reference_date - timedelta(days=reference_date.weekday())
        # Go back 7 days to get previous week's Monday
        prev_week_start = current_week_start - timedelta(days=7)
        # Previous week's Friday is 4 days after Monday
        prev_week_end = prev_week_start + timedelta(days=4)
        return prev_week_start, prev_week_end

    def get_prev_month_range(self, reference_date: datetime) -> Tuple[datetime, datetime]:
        """
        Get the 1st and last day of the month BEFORE the reference_date.
        """
        # First day of the current month
        first_current = reference_date.replace(day=1)
        # Last day of previous month
        last_prev = first_current - timedelta(days=1)
        # First day of previous month
        first_prev = last_prev.replace(day=1)
        return first_prev, last_prev

    def get_prev_year_range(self, reference_date: datetime) -> Tuple[datetime, datetime]:
        """
        Get the 1st and last day of the year BEFORE the reference_date.
        """
        # First day of current year
        first_current = reference_date.replace(month=1, day=1)
        # Last day of previous year
        last_prev = first_current - timedelta(days=1)
        # First day of previous year
        first_prev = last_prev.replace(month=1, day=1)
        return first_prev, last_prev

    def get_hist_range(self, symbol: str, from_date: datetime, to_date: datetime, interval='day') -> Optional[pd.DataFrame]:
        key = f"{symbol}_{from_date.date()}_{to_date.date()}_{interval}"
        with self._cache_lock:
            if key in self._historical_data_cache:
                return self._historical_data_cache[key]

        token = self.get_token(symbol)
        if not token:
            return None

        self._rate_limit()
        try:
            logger.debug(f"API call for {symbol} {from_date.date()} to {to_date.date()}")
            data = self.kite.historical_data(token, from_date.strftime('%Y-%m-%d'), 
                                           to_date.strftime('%Y-%m-%d'), interval)
            if not data: 
                logger.debug(f"No data returned for {symbol} {from_date.date()} to {to_date.date()}")
                return None
            df = pd.DataFrame(data).set_index('date').astype(float)
            df.index = pd.to_datetime(df.index)
            with self._cache_lock:
                self._historical_data_cache[key] = df
            logger.debug(f"Cached {len(df)} rows for {symbol} {from_date.date()} to {to_date.date()}")
            return df
        except Exception as e:
            logger.error(f"Range data failed for {symbol} {from_date.date()}-{to_date.date()}: {e}")
            return None

    def calc_cpr_levels(self, symbol: str, root_date: datetime) -> Optional[CPRLevels]:
        """
        Calculate CPR levels using a SINGLE historical data fetch.
        OPTIMIZED: Fetches 400 days of data once and slices locally for daily/weekly/monthly/yearly.
        """
        # Fetch exactly 400 days relying on the standard get_hist_data caching mechanism
        all_data = self.get_hist_data(symbol, days=400, interval='day', end_date=root_date)
        
        if all_data is None:
            return None
        
        # Filter to data on or before root_date
        idx_dates = pd.to_datetime(all_data.index)
        df = all_data[idx_dates.date <= root_date.date()]  # type: ignore
        
        if len(df) < 2:
            logger.debug(f"Insufficient data for {symbol}")
            return None
        
        # Daily CPR (prev day) - use last 2 rows
        h, l, c = float(df.iloc[-2]['high']), float(df.iloc[-2]['low']), float(df.iloc[-2]['close'])
        d_pp, d_bc, d_tc = CPRService.calculate_cpr(h, l, c)
        
        # Weekly CPR - slice for prev week Mon-Fri
        mon, fri = self.get_prev_week_range(root_date)
        week_mask = (idx_dates.date >= mon.date()) & (idx_dates.date <= fri.date())  # type: ignore
        week_df = all_data[week_mask]
        if week_df.empty:
            return None
        w_h, w_l, w_c = float(week_df['high'].max()), float(week_df['low'].min()), float(week_df['close'].iloc[-1])
        w_pp, w_bc, w_tc = CPRService.calculate_cpr(w_h, w_l, w_c)
        
        # Monthly CPR - slice for prev month
        mon_start, mon_end = self.get_prev_month_range(root_date)
        month_mask = (idx_dates.date >= mon_start.date()) & (idx_dates.date <= mon_end.date())  # type: ignore
        month_df = all_data[month_mask]
        if month_df.empty:
            return None
        m_h, m_l, m_c = float(month_df['high'].max()), float(month_df['low'].min()), float(month_df['close'].iloc[-1])
        m_pp, m_bc, m_tc = CPRService.calculate_cpr(m_h, m_l, m_c)
        
        # Monthly S1, R1, S0.5, R0.5
        m_s1 = (2 * m_pp) - m_h
        m_r1 = (2 * m_pp) - m_l
        m_s05 = (m_pp + m_s1) / 2
        m_r05 = (m_pp + m_r1) / 2
        
        # Monthly Camarilla R3 and S3
        m_cam_range = m_h - m_l
        m_cam_r3 = m_c + m_cam_range * 1.1 / 4.0
        m_cam_s3 = m_c - m_cam_range * 1.1 / 4.0
        
        # Yearly CPR - slice for prev year
        year_start, year_end = self.get_prev_year_range(root_date)
        year_mask = (idx_dates.date >= year_start.date()) & (idx_dates.date <= year_end.date())  # type: ignore
        year_df = all_data[year_mask]
        if year_df.empty:
            return None
        y_h, y_l, y_c = float(year_df['high'].max()), float(year_df['low'].min()), float(year_df['close'].iloc[-1])
        y_pp, y_bc, y_tc = CPRService.calculate_cpr(y_h, y_l, y_c)
        
        # Current candle (root_date)
        curr_price, curr_open, curr_high, curr_low = [float(df.iloc[-1][col]) for col in ['close', 'open', 'high', 'low']]
        
        return CPRLevels(d_pp, d_bc, d_tc, w_pp, w_bc, w_tc, m_pp, m_bc, m_tc, m_s1, m_r1, m_s05, m_r05,
                        m_cam_r3, m_cam_s3,
                        y_pp, y_bc, y_tc, curr_price, curr_open, curr_high, curr_low, c, m_h, m_l)

    def calculate_rsi(self, prices: List[float], period: int = 21) -> List[float]:
        """Standard Wilder's RSI calculation."""
        if len(prices) <= period:
            return []
        
        deltas = np.diff(prices)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        
        if down == 0:
            rs = 100
        else:
            rs = up / down
            
        rsi = np.zeros_like(prices)
        rsi[:period] = 100.0 - (100.0 / (1.0 + rs))
        
        for i in range(period, len(prices)):
            delta = deltas[i-1]
            if delta > 0:
                up_val, down_val = delta, 0.0
            else:
                up_val, down_val = 0.0, -delta
                
            up = (up * (period - 1) + up_val) / period
            down = (down * (period - 1) + down_val) / period
            
            if down == 0:
                rs = 100
            else:
                rs = up / down
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
            
        return rsi.tolist()

    def calculate_delta_rsi(self, rsi: List[float], window: int = 21, degree: int = 2) -> Tuple[List[float], List[float]]:
        """
        Calculate Delta-RSI (polynomial derivative of RSI) and its signal line (EMA).
        Matches the logic in Delta-RSI Oscillator: 
        D = \sum_{i=1}^{degree} i * a_i * (window-1)^{i-1}
        where [a_degree, ..., a_1, a_0] are the polynomial coefficients.
        """
        if len(rsi) < window:
            return [], []
            
        drsi = [0.0] * len(rsi)
        x = np.arange(window)
        
        # Calculate polynomial derivative at each point where we have enough window
        for i in range(window - 1, len(rsi)):
            y = np.array(rsi[i - window + 1 : i + 1])
            # Polynomial fit: a2*x^2 + a1*x + a0 -> returns [a2, a1, a0]
            poly = np.polyfit(x, y, degree)
            # Derivative at the last point (x = window - 1)
            # D = 2*a2*x + a1
            d_val = 2 * poly[0] * (window - 1) + poly[1]
            drsi[i] = d_val
            
        # Signal Line: 9-period EMA of D-RSI
        signal_len = 9
        signal = [0.0] * len(drsi)
        alpha = 2 / (signal_len + 1)
        
        curr_ema = 0.0
        # Start EMA after D-RSI window starts
        start_idx = window - 1
        for i in range(start_idx, len(drsi)):
            if i == start_idx:
                curr_ema = drsi[i]
            else:
                curr_ema = alpha * drsi[i] + (1 - alpha) * curr_ema
            signal[i] = curr_ema
            
        return drsi, signal

    def get_fo_stocks(self) -> List[str]:
        if self._fo_stocks is not None:
            return self._fo_stocks
        
        self._rate_limit()
        try:
            nfo = self.kite.instruments('NFO')
            fo_set = {inst['name'] for inst in nfo 
                     if inst.get('instrument_type') == 'FUT' and inst.get('name') 
                     and not any(idx in inst['name'] for idx in self.INDEX_SYMBOLS)}
            self._fo_stocks = sorted(fo_set)
            return self._fo_stocks
        except Exception as e:
            logger.error(f"FO stocks failed: {e}")
            return []

    def is_above_all_tc(self, price: float, d_tc: float, w_tc: float, m_tc: float) -> bool:
        return price > d_tc > w_tc > m_tc

    def is_below_all_bc(self, price: float, d_bc: float, w_bc: float, m_bc: float) -> bool:
        return price < d_bc < w_bc < m_bc

    def evaluate_status(self, cpr: CPRLevels) -> str:
        wbc_mtc_diff = abs(cpr.weekly_bc - cpr.monthly_tc) / max(cpr.weekly_bc, 1e-6) * 100
        wtc_mbc_diff = abs(cpr.weekly_tc - cpr.monthly_bc) / max(cpr.weekly_tc, 1e-6) * 100
        mbc_ytc_diff = abs(cpr.monthly_bc - cpr.yearly_tc) / max(cpr.monthly_bc, 1e-6) * 100
        mtc_ybc_diff = abs(cpr.monthly_tc - cpr.yearly_bc) / max(cpr.monthly_tc, 1e-6) * 100
        
        buy_cond = cpr.weekly_bc <= cpr.daily_tc
        if (self.is_above_all_tc(cpr.current_price, cpr.daily_tc, cpr.weekly_tc, cpr.monthly_tc) and
            cpr.current_price > cpr.yearly_tc and
            cpr.weekly_bc > cpr.monthly_bc and cpr.monthly_bc > cpr.yearly_bc and
            wbc_mtc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            mbc_ytc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            buy_cond and ((cpr.current_low <= cpr.weekly_tc <= cpr.current_price) or 
                         (cpr.current_low <= cpr.monthly_tc <= cpr.current_price))):
            # Upper wick <= 25% of candle range
            candle_range = cpr.current_high - cpr.current_low
            if candle_range > 0 and (cpr.current_high - cpr.current_price) / candle_range > 0.25:
                return "🟡 IN CPR"
            return "✅ ABOVE CPR TC"
        
        sell_cond = cpr.weekly_tc >= cpr.daily_bc
        if (self.is_below_all_bc(cpr.current_price, cpr.daily_bc, cpr.weekly_bc, cpr.monthly_bc) and
            cpr.current_price < cpr.yearly_bc and
            cpr.weekly_tc < cpr.monthly_tc and cpr.monthly_tc < cpr.yearly_tc and
            wtc_mbc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            mtc_ybc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            sell_cond and ((cpr.current_high >= cpr.weekly_bc >= cpr.current_price) or 
                          (cpr.current_high >= cpr.monthly_bc >= cpr.current_price))):
            # Lower wick <= 25% of candle range
            candle_range = cpr.current_high - cpr.current_low
            if candle_range > 0 and (cpr.current_price - cpr.current_low) / candle_range > 0.25:
                return "🟡 IN CPR"
            return "❌ BELOW CPR BC"
        
        return "🟡 IN CPR"

    def calc_gaps(self, price: float, status: str, levels: CPRLevels) -> Tuple[float, float, float]:
        if status == "✅ ABOVE CPR TC":
            gaps = tuple(round(abs(price - lvl) / lvl * 100, 2) for lvl in [levels.daily_tc, levels.weekly_tc, levels.monthly_tc])
            return cast(Tuple[float, float, float], gaps)
        elif status == "❌ BELOW CPR BC":
            gaps = tuple(round(abs(price - lvl) / lvl * 100, 2) for lvl in [levels.daily_bc, levels.weekly_bc, levels.monthly_bc])
            return cast(Tuple[float, float, float], gaps)
        elif status == self.CROSS_ABOVE_WEEKLY:
            return 0.0, round(abs(price - levels.weekly_tc) / max(levels.weekly_tc, 1e-6) * 100, 2), 0.0
        elif status == self.CROSS_BELOW_WEEKLY:
            return 0.0, round(abs(price - levels.weekly_bc) / max(levels.weekly_bc, 1e-6) * 100, 2), 0.0
        elif status == self.BULLISH_REVERSAL:
            # For bullish reversal, relevant levels are Monthly S1 and Prev Month Low
            # Use m_gap for distance from Monthly S1
            return 0.0, 0.0, round(abs(price - levels.monthly_s1) / max(levels.monthly_s1, 1e-6) * 100, 2)
        elif status == self.BEARISH_REVERSAL:
            # For bearish reversal, relevant levels are Monthly R1 and Prev Month High
            # Use m_gap for distance from Monthly R1
            return 0.0, 0.0, round(abs(price - levels.monthly_r1) / max(levels.monthly_r1, 1e-6) * 100, 2)
        elif status == self.CPR_TOUCH_ABOVE:
            return 0.0, 0.0, round(abs(price - levels.monthly_pp) / max(levels.monthly_pp, 1e-6) * 100, 2)
        elif status == self.CPR_TOUCH_BELOW:
            return 0.0, 0.0, round(abs(price - levels.monthly_pp) / max(levels.monthly_pp, 1e-6) * 100, 2)
        return 0.0, 0.0, 0.0

    def detect_weekly_cross(self, levels: CPRLevels) -> Optional[str]:
        prev_close = levels.previous_close
        cross_above = (
            prev_close <= levels.weekly_pp
            and levels.current_price > levels.weekly_tc
            and levels.current_low <= levels.weekly_pp  # low pierced below then moved above
            and levels.current_price > levels.monthly_tc  # Price must be above monthly CPR TC
            and levels.current_price > levels.yearly_tc  # Price must be above yearly CPR TC
        )
        cross_below = (
            prev_close >= levels.weekly_pp
            and levels.current_price < levels.weekly_bc
            and levels.current_high >= levels.weekly_pp  # high was above then moved below
            and levels.current_price < levels.monthly_bc  # Price must be below monthly CPR BC
            and levels.current_price < levels.yearly_bc  # Price must be below yearly CPR BC
        )

        if cross_above and not cross_below:
            # Upper wick <= 25% of candle range
            candle_range = levels.current_high - levels.current_low
            if candle_range > 0 and (levels.current_high - levels.current_price) / candle_range > 0.25:
                return None
            return self.CROSS_ABOVE_WEEKLY
        if cross_below and not cross_above:
            # Lower wick <= 25% of candle range
            candle_range = levels.current_high - levels.current_low
            if candle_range > 0 and (levels.current_price - levels.current_low) / candle_range > 0.25:
                return None
            return self.CROSS_BELOW_WEEKLY
        return None

    def detect_bullish_reversal(self, levels: CPRLevels) -> Optional[str]:
        """
        List 1(Cross Above S1 and Previous Low). 
        Daily candle price touch the Monthly S1 or Previous Month Low and close above the monthly S1 and Previous Month Low.
        Added: Price should close above the Monthly S0.5.
        """
        touched_support = (levels.current_low <= levels.monthly_s1) or (levels.current_low <= levels.prev_month_low)
        closed_above_support = (levels.current_price > levels.monthly_s1) and (levels.current_price > levels.prev_month_low)
        closed_above_s05 = (levels.current_price > levels.monthly_s05)
        
        # Condition 3: Green Candle (Close > Open)
        is_green_candle = levels.current_price > levels.current_open

        if touched_support and closed_above_support and closed_above_s05 and is_green_candle:
            # Upper wick <= 25% of candle range
            candle_range = levels.current_high - levels.current_low
            if candle_range <= 0:
                return None
            if (levels.current_high - levels.current_price) / candle_range > 0.25:
                return None
            return self.BULLISH_REVERSAL
        return None

    def detect_bearish_reversal(self, levels: CPRLevels) -> Optional[str]:
        """
        List 2(Cross Below R1 and Previous High). 
        Daily Candle price touch the Monthly R1 or Previous Month High and close below the monthly R1 and Previous Month High.
        Added: Price should close below the Monthly R0.5.
        """
        touched_resistance = (levels.current_high >= levels.monthly_r1) or (levels.current_high >= levels.prev_month_high)
        closed_below_resistance = (levels.current_price < levels.monthly_r1) and (levels.current_price < levels.prev_month_high)
        closed_below_r05 = (levels.current_price < levels.monthly_r05)
        
        # Condition 3: Red Candle (Close < Open)
        is_red_candle = levels.current_price < levels.current_open

        if touched_resistance and closed_below_resistance and closed_below_r05 and is_red_candle:
            # Lower wick <= 25% of candle range
            candle_range = levels.current_high - levels.current_low
            if candle_range <= 0:
                return None
            if (levels.current_price - levels.current_low) / candle_range > 0.25:
                return None
            return self.BEARISH_REVERSAL
        return None

    def detect_cpr_touch_closed_above(self, levels: CPRLevels) -> Optional[str]:
        """
        Daily candle touches the Monthly CPR and closes ABOVE.
        Conditions:
        1. Touch: daily low dipped into CPR zone (low <= monthly_tc)
        2. Close above monthly TC
        3. Green candle (close > open)
        4. Upper wick <= 40% of candle range
        5. Gap between close and Monthly Camarilla R3 > 2%
        6. Monthly CPR must NOT be narrow (TC-BC spread > 0.5% of PP)
        """
        # Monthly CPR must not be narrow
        cpr_spread = abs(levels.monthly_tc - levels.monthly_bc)
        cpr_spread_pct = cpr_spread / max(levels.monthly_pp, 1e-6) * 100
        if cpr_spread_pct < 0.5:
            return None

        touched_cpr = levels.current_low <= levels.monthly_tc
        closed_above = levels.current_price > levels.monthly_tc
        is_green = levels.current_price > levels.current_open

        # Upper wick <= 40% of candle range
        candle_range = levels.current_high - levels.current_low
        if candle_range <= 0:
            return None
        upper_wick = levels.current_high - levels.current_price  # high - close for green candle
        wick_ratio = upper_wick / candle_range
        wick_ok = wick_ratio <= 0.20

        # Gap between close and Monthly Camarilla R3 > 2%
        cam_r3_gap = abs(levels.monthly_cam_r3 - levels.current_price) / max(levels.current_price, 1e-6) * 100
        cam_gap_ok = cam_r3_gap > 3.0

        if touched_cpr and closed_above and is_green and wick_ok and cam_gap_ok:
            return self.CPR_TOUCH_ABOVE
        return None

    def detect_cpr_touch_closed_below(self, levels: CPRLevels) -> Optional[str]:
        """
        Daily candle touches the Monthly CPR and closes BELOW.
        Conditions:
        1. Touch: daily high reached into CPR zone (high >= monthly_bc)
        2. Close below monthly BC
        3. Red candle (close < open)
        4. Lower wick <= 40% of candle range
        5. Gap between close and Monthly Camarilla S3 > 2%
        6. Monthly CPR must NOT be narrow (TC-BC spread > 0.5% of PP)
        """
        # Monthly CPR must not be narrow
        cpr_spread = abs(levels.monthly_tc - levels.monthly_bc)
        cpr_spread_pct = cpr_spread / max(levels.monthly_pp, 1e-6) * 100
        if cpr_spread_pct < 0.5:
            return None

        touched_cpr = levels.current_high >= levels.monthly_bc
        closed_below = levels.current_price < levels.monthly_bc
        is_red = levels.current_price < levels.current_open

        # Lower wick <= 40% of candle range
        candle_range = levels.current_high - levels.current_low
        if candle_range <= 0:
            return None
        lower_wick = levels.current_price - levels.current_low  # close - low for red candle
        wick_ratio = lower_wick / candle_range
        wick_ok = wick_ratio <= 0.20

        # Gap between close and Monthly Camarilla S3 > 2%
        cam_s3_gap = abs(levels.current_price - levels.monthly_cam_s3) / max(levels.current_price, 1e-6) * 100
        cam_gap_ok = cam_s3_gap > 3.0

        if touched_cpr and closed_below and is_red and wick_ok and cam_gap_ok:
            return self.CPR_TOUCH_BELOW
        return None

    def calculate_stock_iv_percentile(self, symbol: str, atm_iv: float) -> Optional[float]:
        """
        Calculate IV Percentile for a stock using the Hybrid method
        (same as OpenInterestService._calculate_hybrid_iv_percentile).
        
        Methodology:
        1. Fetch 1 year of daily OHLC data for the stock.
        2. Calculate 20-day annualized rolling Historical Volatility (HV).
        3. Rank the given ATM IV against the 1-year HV history.
        
        Args:
            symbol: Stock trading symbol
            atm_iv: Current ATM Implied Volatility (decimal, e.g. 0.25 for 25%)
        
        Returns:
            IV Percentile (0-100) or None if data unavailable.
        """
        try:
            if atm_iv <= 0:
                return None

            # ----- Step 1: Get HV rolling volatility (cached 24h) -----
            now = datetime.now()
            cached = self._hv_cache.get(symbol)
            if cached and (now - cached['timestamp']).total_seconds() < 86400:
                rolling_volatility = cached['rolling_volatility']
                logger.debug(f"Using cached HV data for {symbol} ({len(rolling_volatility)} points)")
            else:
                # Find instrument token
                token = self.get_token(symbol)
                if not token:
                    logger.debug(f"IV Percentile: No token for {symbol}")
                    return None
                
                # Fetch 1-year daily OHLC
                to_date = now.date()
                from_date = to_date - timedelta(days=365 + 30)  # +30 buffer for rolling window
                
                self._rate_limit()
                history_data = self.kite.historical_data(
                    instrument_token=token,
                    from_date=from_date.strftime('%Y-%m-%d'),
                    to_date=to_date.strftime('%Y-%m-%d'),
                    interval='day'
                )
                
                if not history_data or len(history_data) < 30:
                    logger.debug(f"IV Percentile: Insufficient history for {symbol}")
                    return None
                
                # Calculate daily log returns
                prices = [x['close'] for x in history_data]
                if len(prices) < 2:
                    return None
                
                log_returns = []
                for i in range(1, len(prices)):
                    if prices[i] > 0 and prices[i-1] > 0:
                        log_returns.append(math.log(prices[i] / prices[i-1]))
                
                # Calculate 20-day annualized rolling volatility
                window = 20
                sqrt_days = math.sqrt(252)
                rolling_volatility = []
                
                if len(log_returns) < window:
                    return None
                
                for i in range(len(log_returns) - window + 1):
                    window_returns = log_returns[i: i + window]
                    mean = sum(window_returns) / window
                    variance = sum((x - mean) ** 2 for x in window_returns) / (window - 1)
                    rolling_volatility.append(math.sqrt(variance) * sqrt_days)
                
                if not rolling_volatility:
                    return None
                
                # Cache for 24 hours
                self._hv_cache[symbol] = {
                    'timestamp': now,
                    'rolling_volatility': rolling_volatility
                }
                logger.debug(f"Cached HV data for {symbol} ({len(rolling_volatility)} points)")
            
            # ----- Step 2: Rank ATM IV against HV history -----
            count_below = sum(1 for hv in rolling_volatility if hv < atm_iv)
            total_count = len(rolling_volatility)
            
            percentile = (count_below / total_count) * 100
            logger.info(f"IV Percentile for {symbol}: {percentile:.1f}% (ATM IV: {atm_iv:.2%}, Current HV: {rolling_volatility[-1]:.2%})")
            return round(percentile, 2)
        
        except Exception as e:
            logger.debug(f"IV Percentile calc failed for {symbol}: {e}")
            return None

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate normal CDF (Abramowitz & Stegun) - matches open_interest_service."""
        a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
        p = 0.3275911
        sign = 1 if x >= 0 else -1
        x = abs(x) / math.sqrt(2)
        t = 1.0 / (1.0 + p * x)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
        return 0.5 * (1.0 + sign * y)

    @staticmethod
    def _normal_pdf(x: float) -> float:
        """Standard normal PDF."""
        return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

    @staticmethod
    def _calculate_iv_newton(S: float, K: float, T: float, r: float, market_price: float, option_type: str) -> float:
        """
        Calculate implied volatility using Newton-Raphson method (Black-Scholes).
        Matches OpenInterestService._calculate_iv_from_price.
        Uses manual normal CDF/PDF (no scipy dependency).
        """
        if market_price <= 0 or T <= 0:
            return 0.0
        
        # Intrinsic value check
        if option_type == 'CE':
            intrinsic = max(S - K, 0)
        else:
            intrinsic = max(K - S, 0)
        if market_price < intrinsic:
            return 0.0
        
        try:
            sigma = 0.5  # Initial guess
            for _ in range(100):
                if sigma <= 0:
                    sigma = 0.001
                d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
                d2 = d1 - sigma * math.sqrt(T)
                
                if option_type == 'CE':
                    price = S * CPRFilterService._normal_cdf(d1) - K * math.exp(-r * T) * CPRFilterService._normal_cdf(d2)
                else:
                    price = K * math.exp(-r * T) * CPRFilterService._normal_cdf(-d2) - S * CPRFilterService._normal_cdf(-d1)
                
                if abs(price - market_price) < 1e-6:
                    return sigma
                
                # Vega
                vega = S * CPRFilterService._normal_pdf(d1) * math.sqrt(T)
                if abs(vega) < 1e-10:
                    break
                
                sigma = sigma - (price - market_price) / vega
                sigma = max(0.001, min(sigma, 2.0))
            
            return sigma
        except Exception:
            return 0.0

    def process_stock(self, symbol: str, root_date: datetime) -> Optional[Dict]:
        try:
            cpr = self.calc_cpr_levels(symbol, root_date)
            if not cpr:
                logger.debug(f"{symbol}: No CPR levels")
                return None
            
            primary_status = self.evaluate_status(cpr)
            weekly_cross_status = self.detect_weekly_cross(cpr)
            bullish_reversal = self.detect_bullish_reversal(cpr)
            bearish_reversal = self.detect_bearish_reversal(cpr)
            cpr_touch_above = self.detect_cpr_touch_closed_above(cpr)
            cpr_touch_below = self.detect_cpr_touch_closed_below(cpr)

            payloads: Dict[str, Optional[Dict]] = {
                'signal': None, 
                'weekly_cross': None,
                'bullish_reversal': None,
                'bearish_reversal': None,
                'cpr_touch_above': None,
                'cpr_touch_below': None,
                'drsi_bullish': None,
                'drsi_bearish': None,
                'drsi_reversal_bullish': None,
                'drsi_reversal_bearish': None
            }

            # Phase D-RSI: Delta-RSI Filter (Daily) - Matches "Delta-RSI Oscillator" exactly
            # Fetch 400 days to ensure RSI(21) and EMA(9) are fully stabilized
            hist_prices = self.get_hist_data(symbol, days=400, interval='day', end_date=root_date)
            if hist_prices is not None and len(hist_prices) > 50:
                closes = hist_prices['close'].tolist()
                rsi_vals = self.calculate_rsi(closes, period=21)
                # degree=2 matches Oscillator's default
                drsi_vals, sig_vals = self.calculate_delta_rsi(rsi_vals, window=21, degree=2)
                
                if len(drsi_vals) >= 3:
                    in_long = False
                    in_short = False
                    prev_long = False
                    prev_short = False
                    trigger_type = ""
                    trigger_today = False
                    trigger_prev = False
                    trigger_type_prev = ""
                    state_t1 = "neutral"
                    state_t2 = "neutral"
                    
                    # Iterate through history to track Buy/Sell states (persistent)
                    for i in range(2, len(drsi_vals)):
                        d = drsi_vals[i]
                        d1 = drsi_vals[i-1]
                        
                        # Conditions from Oscillator study (Sig-Cross and Direction Change Removed)
                        crossup = (d1 <= 0 and d > 0)
                        crossdw = (d1 >= 0 and d < 0)
                        
                        is_last = (i == len(drsi_vals) - 1)
                        is_prev = (i == len(drsi_vals) - 2)
                        
                        if is_last:
                            state_today = "long" if in_long else ("short" if in_short else "neutral")
                        
                        if crossup:
                            in_long = True
                            in_short = False
                            if is_last:
                                trigger_today = True
                                trigger_type = "Zero-Cross"
                            if is_prev:
                                trigger_prev = True
                                trigger_type_prev = "Zero-Cross"
                        elif crossdw:
                            in_short = True
                            in_long = False
                            if is_last:
                                trigger_today = True 
                                trigger_type = "Zero-Cross"
                            if is_prev:
                                trigger_prev = True
                                trigger_type_prev = "Zero-Cross"
                        
                        # Capture state at T-2 (day before previous)
                        if i == len(drsi_vals) - 3:
                            state_t2 = "long" if in_long else ("short" if in_short else "neutral")
                        # Capture state at T-1 (previous day)
                        if i == len(drsi_vals) - 2:
                            state_t1 = "long" if in_long else ("short" if in_short else "neutral")
                    # Watch list for debugging (User reported stocks)
                    watch_stocks = ['DELHIVERY', 'SIEMENS', 'GLENMARK', 'LICHSGFIN', 'DIVISLAB', 
                                    'APOLLOTYRE', 'PAGEIND', 'SRF', 'PERSISTENT', 'ABB', 'ANGELONE', 'BDL', 'MAZDOCK']
                    if symbol in watch_stocks:
                        logger.info(f"DEBUG D-RSI {symbol}: D={drsi_vals[-1]:.4f}, S={sig_vals[-1]:.4f}, RSI={rsi_vals[-1]:.2f}, Long={in_long}, Short={in_short}, TriggerToday={trigger_today} ({trigger_type})")

                    # D-RSI Reversal Logic: Detect flip from yesterday's state to today's state
                    # Bullish Flip-UP: Yesterday (T-1) was Sell (short), Today (T-0) is Buy (long, triggered now)
                    if trigger_today and state_today == "long" and state_t1 == "short":
                        payloads['drsi_reversal_bullish'] = {
                            'symbol': symbol,
                            'current_price': round(cpr.current_price, 2),
                            'rsi': round(rsi_vals[-1], 2),
                            'drsi': round(drsi_vals[-1], 4),
                            'signal': round(sig_vals[-1], 4),
                            'trigger': "Flip-UP"
                        }
                    
                    # Bearish Flip-DOWN: Yesterday (T-1) was Buy (long), Today (T-0) is Sell (short, triggered now)
                    if trigger_today and state_today == "short" and state_t1 == "long":
                        payloads['drsi_reversal_bearish'] = {
                            'symbol': symbol,
                            'current_price': round(cpr.current_price, 2),
                            'rsi': round(rsi_vals[-1], 2),
                            'drsi': round(drsi_vals[-1], 4),
                            'signal': round(sig_vals[-1], 4),
                            'trigger': "Flip-DOWN"
                        }

                    # Strict filter: ONLY Add to payload if the signal triggered TODAY
                    if in_long and trigger_today:
                        payloads['drsi_bullish'] = {
                            'symbol': symbol,
                            'current_price': round(cpr.current_price, 2),
                            'rsi': round(rsi_vals[-1], 2),
                            'drsi': round(drsi_vals[-1], 4),
                            'signal': round(sig_vals[-1], 4),
                            'is_new': trigger_today,
                            'trigger': trigger_type
                        }
                    
                    if in_short and trigger_today:
                        payloads['drsi_bearish'] = {
                            'symbol': symbol,
                            'current_price': round(cpr.current_price, 2),
                            'rsi': round(rsi_vals[-1], 2),
                            'drsi': round(drsi_vals[-1], 4),
                            'signal': round(sig_vals[-1], 4),
                            'is_new': trigger_today,
                            'trigger': trigger_type
                        }


            if primary_status != "🟡 IN CPR":
                gaps = self.calc_gaps(cpr.current_price, primary_status, cpr)
                payloads['signal'] = {
                    'symbol': symbol,
                    'current_price': round(cpr.current_price, 2),
                    'status': primary_status,
                    'daily_tc': round(cpr.daily_tc, 2),
                    'daily_bc': round(cpr.daily_bc, 2),
                    'weekly_tc': round(cpr.weekly_tc, 2),
                    'weekly_bc': round(cpr.weekly_bc, 2),
                    'monthly_tc': round(cpr.monthly_tc, 2),
                    'monthly_bc': round(cpr.monthly_bc, 2),
                    'yearly_tc': round(cpr.yearly_tc, 2),
                    'yearly_bc': round(cpr.yearly_bc, 2),
                    'd_gap': gaps[0],
                    'w_gap': gaps[1],
                    'm_gap': gaps[2]
                }
                logger.debug(f"{symbol}: {primary_status}")

            if weekly_cross_status:
                cross_gaps = self.calc_gaps(cpr.current_price, weekly_cross_status, cpr)
                payloads['weekly_cross'] = {
                    'status': weekly_cross_status,
                    'payload': {
                        'symbol': symbol,
                        'current_price': round(cpr.current_price, 2),
                        'status': weekly_cross_status,
                        'daily_tc': round(cpr.daily_tc, 2),
                        'daily_bc': round(cpr.daily_bc, 2),
                        'weekly_tc': round(cpr.weekly_tc, 2),
                        'weekly_bc': round(cpr.weekly_bc, 2),
                        'monthly_tc': round(cpr.monthly_tc, 2),
                        'monthly_bc': round(cpr.monthly_bc, 2),
                        'yearly_tc': round(cpr.yearly_tc, 2),
                        'yearly_bc': round(cpr.yearly_bc, 2),
                        'd_gap': cross_gaps[0],
                        'w_gap': cross_gaps[1],
                        'm_gap': cross_gaps[2]
                    }
                }

            if bullish_reversal:
                rev_gaps = self.calc_gaps(cpr.current_price, bullish_reversal, cpr)
                payloads['bullish_reversal'] = {
                    'symbol': symbol,
                    'current_price': round(cpr.current_price, 2),
                    'status': bullish_reversal,
                    'monthly_s1': round(cpr.monthly_s1, 2),
                    'prev_month_low': round(cpr.prev_month_low, 2),
                    'monthly_tc': round(cpr.monthly_tc, 2), # Context
                    'm_gap': rev_gaps[2]
                }

            if bearish_reversal:
                rev_gaps = self.calc_gaps(cpr.current_price, bearish_reversal, cpr)
                payloads['bearish_reversal'] = {
                    'symbol': symbol,
                    'current_price': round(cpr.current_price, 2),
                    'status': bearish_reversal,
                    'monthly_r1': round(cpr.monthly_r1, 2),
                    'prev_month_high': round(cpr.prev_month_high, 2),
                    'monthly_bc': round(cpr.monthly_bc, 2), # Context
                    'm_gap': rev_gaps[2]
                }

            if cpr_touch_above:
                touch_gaps = self.calc_gaps(cpr.current_price, cpr_touch_above, cpr)
                payloads['cpr_touch_above'] = {
                    'symbol': symbol,
                    'current_price': round(cpr.current_price, 2),
                    'status': cpr_touch_above,
                    'monthly_tc': round(cpr.monthly_tc, 2),
                    'monthly_pp': round(cpr.monthly_pp, 2),
                    'monthly_bc': round(cpr.monthly_bc, 2),
                    'm_gap': touch_gaps[2]
                }

            if cpr_touch_below:
                touch_gaps = self.calc_gaps(cpr.current_price, cpr_touch_below, cpr)
                payloads['cpr_touch_below'] = {
                    'symbol': symbol,
                    'current_price': round(cpr.current_price, 2),
                    'status': cpr_touch_below,
                    'monthly_tc': round(cpr.monthly_tc, 2),
                    'monthly_pp': round(cpr.monthly_pp, 2),
                    'monthly_bc': round(cpr.monthly_bc, 2),
                    'm_gap': touch_gaps[2]
                }

            return payloads if any(payloads.values()) else None
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def _build_atm_option_map(self, stocks: List[str]) -> Dict[str, Dict]:
        """
        Build a mapping of stock symbol -> nearest-expiry ATM CE option info.
        Pre-loads NFO instruments once and finds the ATM option for each stock.
        Also captures stock volume via kite.quote().
        
        Returns:
            Dict[symbol, {'tradingsymbol': str, 'strike': float, 'expiry': date,
                          'current_price': float, 'volume': int}]
        """
        try:
            if not hasattr(self, '_nfo_instruments') or self._nfo_instruments is None:
                self._rate_limit()
                self._nfo_instruments = self.kite.instruments('NFO')
                logger.info(f"Loaded {len(self._nfo_instruments)} NFO instruments for IV calculation")
            
            # Fetch stock quotes (gives price + volume) instead of just LTP
            ltp_keys = [f"NSE:{s}" for s in stocks]
            stock_prices = {}
            stock_volumes = {}
            stock_day_change_pct = {}
            
            batch_size = 250
            for i in range(0, len(ltp_keys), batch_size):
                batch = ltp_keys[i:i + batch_size]
                self._rate_limit()
                try:
                    quote_data = self.kite.quote(batch)
                    for key, val in quote_data.items():  # type: ignore
                        symbol = str(key).replace('NSE:', '')
                        last_price = val.get('last_price', 0)  # type: ignore
                        stock_prices[symbol] = last_price
                        stock_volumes[symbol] = val.get('volume', 0)  # type: ignore

                        # Current day % change from previous close
                        prev_close = val.get('ohlc', {}).get('close', 0)  # type: ignore
                        if prev_close and prev_close > 0:
                            stock_day_change_pct[symbol] = round(((last_price - prev_close) / prev_close) * 100, 2)
                        else:
                            stock_day_change_pct[symbol] = 0.0
                except Exception as e:
                    logger.warning(f"Batch quote fetch failed: {e}")
            
            logger.info(f"Got prices+volumes for {len(stock_prices)} stocks")
            
            # Build ATM option map
            today = date.today()
            atm_map = {}
            
            # Group NFO options (both CE and PE) by stock name for lookup
            stock_options: Dict[str, List] = {}
            for inst in self._nfo_instruments:
                inst_type = inst.get('instrument_type', '')
                if inst_type in ('CE', 'PE') and inst.get('expiry'):
                    name = inst.get('name', '')
                    if name in stock_prices:
                        expiry = inst['expiry']
                        if hasattr(expiry, 'date'):
                            expiry = expiry.date()
                        if expiry >= today:
                            if name not in stock_options:
                                stock_options[name] = []
                            stock_options[name].append({
                                'tradingsymbol': inst.get('tradingsymbol', ''),
                                'strike': inst.get('strike', 0),
                                'expiry': expiry,
                                'type': inst_type
                            })
            
            for symbol, options in stock_options.items():
                current_price = stock_prices.get(symbol, 0)
                if current_price <= 0 or not options:
                    continue
                
                # Find suitable expiry (skip near-expiry < 5 days for reliable IV)
                unique_expiries = sorted(set(o['expiry'] for o in options))
                
                min_days = 5
                suitable_expiry = None
                for exp in unique_expiries:
                    if (exp - today).days >= min_days:
                        suitable_expiry = exp
                        break
                
                if suitable_expiry is None:
                    suitable_expiry = unique_expiries[0]
                
                expiry_options = [o for o in options if o['expiry'] == suitable_expiry]
                
                # Find closest ATM strike
                all_strikes = set(o['strike'] for o in expiry_options if o['strike'] > 0)
                if not all_strikes:
                    continue
                atm_strike = min(all_strikes, key=lambda s: abs(s - current_price))
                
                # Get both CE and PE tradingsymbols at ATM strike
                ce_ts = None
                pe_ts = None
                for o in expiry_options:
                    if o['strike'] == atm_strike:
                        if o['type'] == 'CE':
                            ce_ts = o['tradingsymbol']
                        elif o['type'] == 'PE':
                            pe_ts = o['tradingsymbol']
                
                if ce_ts or pe_ts:
                    atm_map[symbol] = {
                        'ce_tradingsymbol': ce_ts,
                        'pe_tradingsymbol': pe_ts,
                        'strike': atm_strike,
                        'expiry': suitable_expiry,
                        'current_price': current_price,
                        'volume': stock_volumes.get(symbol, 0),
                        'day_change_pct': stock_day_change_pct.get(symbol, 0.0)
                    }
            
            logger.info(f"Built ATM option map for {len(atm_map)} stocks")
            return atm_map
        
        except Exception as e:
            logger.error(f"Error building ATM option map: {e}")
            return {}

    def _enrich_high_iv_stocks(self, high_iv_stocks: List[Dict], atm_map: Dict, atm_ivs: Dict) -> List[Dict]:
        """
        Enrich high-IV stocks with option chain data: PCR, Max Pain, OI% Change.
        Only fetches data for the ~10-15 stocks that passed the >80% filter.
        """
        if not high_iv_stocks:
            return high_iv_stocks
        
        try:
            logger.info(f"Enriching {len(high_iv_stocks)} high-IV stocks with option chain data...")
            today = date.today()
            
            # For each high-IV stock, collect all CE+PE option tradingsymbols for nearest expiry
            stock_options: Dict[str, List[Dict]] = {}  # symbol -> list of {tradingsymbol, strike, type}
            for stock in high_iv_stocks:
                symbol = stock['symbol']
                if symbol not in atm_map:
                    continue
                
                nearest_expiry = atm_map[symbol]['expiry']
                options = []
                
                for inst in self._nfo_instruments:
                    name = inst.get('name', '')
                    inst_type = inst.get('instrument_type', '')
                    if name == symbol and inst_type in ('CE', 'PE') and inst.get('expiry'):
                        expiry = inst['expiry']
                        if hasattr(expiry, 'date'):
                            expiry = expiry.date()
                        if expiry == nearest_expiry:
                            options.append({
                                'tradingsymbol': inst.get('tradingsymbol', ''),
                                'strike': inst.get('strike', 0),
                                'type': inst_type
                            })
                
                if options:
                    stock_options[symbol] = options
            
            # Batch-fetch quotes for all option instruments across all high-IV stocks
            all_option_keys = []
            for symbol, options in stock_options.items():
                for opt in options:
                    all_option_keys.append(f"NFO:{opt['tradingsymbol']}")
            
            option_quotes = {}  # tradingsymbol -> quote dict
            batch_size = 250
            for i in range(0, len(all_option_keys), batch_size):
                batch = all_option_keys[i:i + batch_size]
                self._rate_limit()
                try:
                    quote_data = self.kite.quote(batch)
                    for key, val in quote_data.items():  # type: ignore
                        ts = str(key).replace('NFO:', '')
                        option_quotes[ts] = val
                except Exception as e:
                    logger.warning(f"Option chain quote batch failed: {e}")
            
            logger.info(f"Got quotes for {len(option_quotes)} options across {len(stock_options)} stocks")
            
            # Compute PCR, Max Pain, OI% Change per stock
            enrichment = {}  # symbol -> {pcr, max_pain, oi_change_pct}
            for symbol, options in stock_options.items():
                current_price = atm_map[symbol]['current_price']
                strikes_data = {}  # strike -> {ce_oi, pe_oi}
                total_oi_today = 0
                total_oi_day_low = 0
                
                for opt in options:
                    ts = opt['tradingsymbol']
                    quote = option_quotes.get(ts, {})
                    oi = quote.get('oi', 0)
                    strike = opt['strike']
                    
                    if strike not in strikes_data:
                        strikes_data[strike] = {'strike': strike, 'ce_oi': 0, 'pe_oi': 0}
                    
                    if opt['type'] == 'CE':
                        strikes_data[strike]['ce_oi'] = oi
                    else:
                        strikes_data[strike]['pe_oi'] = oi
                    
                    total_oi_today += oi
                    # Use oi_day_low for OI change estimation
                    oi_day_low = quote.get('ohlc', {}).get('open', oi)  # Approximate with open
                
                strikes_list = list(strikes_data.values())
                
                # PCR
                total_ce_oi = sum(s['ce_oi'] for s in strikes_list)
                total_pe_oi = sum(s['pe_oi'] for s in strikes_list)
                pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0.0
                
                # Max Pain
                max_pain = self._calculate_max_pain_local(strikes_list, current_price)
                
                # OI% Change (total OI change from previous day)
                # Use previous close OI if available, otherwise estimate
                prev_oi = 0
                for opt in options:
                    ts = opt['tradingsymbol']
                    quote = option_quotes.get(ts, {})
                    # net_change in OI compared to previous day
                    oi_change = quote.get('oi_day_high', 0) - quote.get('oi_day_low', 0) if quote.get('oi_day_high') else 0
                    prev_oi += max(0, quote.get('oi', 0) - oi_change)
                
                oi_change_pct = round(((total_oi_today - prev_oi) / prev_oi) * 100, 2) if prev_oi > 0 else 0.0
                
                enrichment[symbol] = {
                    'pcr': pcr,
                    'max_pain': max_pain,
                    'oi_change_pct': oi_change_pct
                }
            
            # Merge enrichment data into high_iv_stocks
            for stock in high_iv_stocks:
                symbol = stock['symbol']
                stock['atm_iv'] = round(atm_ivs.get(symbol, 0) * 100, 2)  # Convert to %
                stock['volume'] = atm_map.get(symbol, {}).get('volume', 0)
                stock['day_change_pct'] = atm_map.get(symbol, {}).get('day_change_pct', 0.0)
                
                enrich = enrichment.get(symbol, {})
                stock['pcr'] = enrich.get('pcr', 0.0)
                stock['max_pain'] = enrich.get('max_pain', 0.0)
                stock['oi_change_pct'] = enrich.get('oi_change_pct', 0.0)
            
            logger.info(f"Enrichment complete for {len(high_iv_stocks)} stocks")
            return high_iv_stocks
        
        except Exception as e:
            logger.error(f"Enrichment failed: {e}")
            # Return stocks with default values
            for stock in high_iv_stocks:
                symbol = stock['symbol']
                stock.setdefault('atm_iv', round(atm_ivs.get(symbol, 0) * 100, 2))
                stock.setdefault('volume', atm_map.get(symbol, {}).get('volume', 0))
                stock.setdefault('day_change_pct', atm_map.get(symbol, {}).get('day_change_pct', 0.0))
                stock.setdefault('pcr', 0.0)
                stock.setdefault('max_pain', 0.0)
                stock.setdefault('oi_change_pct', 0.0)
            return high_iv_stocks

    @staticmethod
    def _calculate_max_pain_local(strikes: List[Dict], current_price: float) -> float:
        """
        Calculate Max Pain — strike where option writers incur least loss.
        Matches the algorithm in OpenInterestService._calculate_max_pain.
        """
        if not strikes:
            return current_price
        
        sorted_strikes = sorted(strikes, key=lambda x: x['strike'])
        min_loss = float('inf')
        max_pain_strike = current_price
        
        for test in sorted_strikes:
            test_strike = test['strike']
            total_loss = 0
            
            for s in sorted_strikes:
                if s['strike'] < test_strike:
                    total_loss += (test_strike - s['strike']) * s.get('ce_oi', 0)
                if s['strike'] > test_strike:
                    total_loss += (s['strike'] - test_strike) * s.get('pe_oi', 0)
            
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = test_strike
        
        return max_pain_strike

    def _batch_compute_iv_percentiles(self, stocks: List[str]) -> List[Dict]:
        """
        Compute IV percentile for all F&O stocks in a batch-optimized manner:
        1. Build ATM option map (NFO instruments + stock prices)
        2. Batch-fetch all ATM option LTPs in one API call
        3. Compute IV for each stock via Black-Scholes
        4. Compute HV percentile for each stock (with 24h caching, parallelized)
        
        Returns:
            List of {symbol, current_price, iv_percentile} for stocks with percentile > 80
        """
        try:
            logger.info(f"Starting batch IV percentile computation for {len(stocks)} stocks...")
            iv_start = time.time()
            
            # Step 1: Build ATM option map
            atm_map = self._build_atm_option_map(stocks)
            if not atm_map:
                logger.warning("No ATM options found for any stock")
                return []
            
            # Step 2: Batch-fetch all ATM option LTPs (both CE and PE)
            option_keys = []
            for info in atm_map.values():
                if info.get('ce_tradingsymbol'):
                    option_keys.append(f"NFO:{info['ce_tradingsymbol']}")
                if info.get('pe_tradingsymbol'):
                    option_keys.append(f"NFO:{info['pe_tradingsymbol']}")
            
            option_ltp_map = {}  # tradingsymbol -> ltp
            
            batch_size = 250
            for i in range(0, len(option_keys), batch_size):
                batch = option_keys[i:i + batch_size]
                self._rate_limit()
                try:
                    ltp_data = self.kite.ltp(batch)
                    for key, val in ltp_data.items():  # type: ignore
                        ts = str(key).replace('NFO:', '')
                        option_ltp_map[ts] = val.get('last_price', 0)  # type: ignore
                except Exception as e:
                    logger.warning(f"Batch option LTP fetch failed for batch {i}: {e}")
            
            logger.info(f"Got LTPs for {len(option_ltp_map)} ATM options (CE+PE)")
            
            # Step 3: Compute ATM IV for each stock via Black-Scholes (average CE and PE)
            atm_ivs = {}  # symbol -> atm_iv (decimal)
            today = date.today()
            r = 0.07  # Risk-free rate
            
            for symbol, info in atm_map.items():
                S = info['current_price']
                K = info['strike']
                days_to_expiry = (info['expiry'] - today).days
                if days_to_expiry <= 0:
                    days_to_expiry = 1
                T = days_to_expiry / 365.0
                
                # Compute IV for both CE and PE, then average
                valid_ivs = []
                
                ce_ts = info.get('ce_tradingsymbol')
                if ce_ts:
                    ce_ltp = option_ltp_map.get(ce_ts, 0)
                    if ce_ltp > 0:
                        ce_iv = self._calculate_iv_newton(S, K, T, r, ce_ltp, 'CE')
                        if 0 < ce_iv < 2.0:  # Cap at 200%
                            valid_ivs.append(ce_iv)
                
                pe_ts = info.get('pe_tradingsymbol')
                if pe_ts:
                    pe_ltp = option_ltp_map.get(pe_ts, 0)
                    if pe_ltp > 0:
                        pe_iv = self._calculate_iv_newton(S, K, T, r, pe_ltp, 'PE')
                        if 0 < pe_iv < 2.0:  # Cap at 200%
                            valid_ivs.append(pe_iv)
                
                if valid_ivs:
                    atm_ivs[symbol] = sum(valid_ivs) / len(valid_ivs)
            
            logger.info(f"Computed ATM IV (avg CE+PE) for {len(atm_ivs)} stocks")
            
            # Step 4: Compute HV percentile for each stock (parallelized with threading)
            high_iv_stocks = []
            
            def _compute_single_hv(symbol_iv_pair):
                symbol, atm_iv = symbol_iv_pair
                try:
                    iv_pct = self.calculate_stock_iv_percentile(symbol, atm_iv)
                    # Filter for IV Percentile > 80 AND ATM IV >= 40% (0.40)
                    if iv_pct is not None and iv_pct > 80 and atm_iv >= 0.40:
                        return {
                            'symbol': symbol,
                            'current_price': round(atm_map[symbol]['current_price'], 2),
                            'iv_percentile': iv_pct
                        }
                except Exception as e:
                    logger.debug(f"HV percentile failed for {symbol}: {e}")
                return None
            
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = executor.map(_compute_single_hv, atm_ivs.items())
                for result in results:
                    if result:
                        high_iv_stocks.append(result)
            
            iv_time = time.time() - iv_start
            logger.info(f"IV Percentile batch complete: {len(high_iv_stocks)} stocks > 80% & ATM IV >= 40% (of {len(atm_ivs)} with valid IV) in {iv_time:.1f}s")
            
            # Step 5: Enrich high-IV stocks with ATM IV, Volume, PCR, Max Pain, OI% Change
            high_iv_stocks = self._enrich_high_iv_stocks(high_iv_stocks, atm_map, atm_ivs)
            
            return high_iv_stocks
        
        except Exception as e:
            logger.error(f"Batch IV percentile computation failed: {e}")
            return []

    def filter_cpr_stocks(self, root_date: Optional[datetime] = None) -> FilterResult:
        stocks = self.get_fo_stocks()
        # stocks = ["COLPAL"]
        if root_date is None:
            root_date = datetime.now()
            
        # Normalize weekend dates to Friday
        if root_date.weekday() == 5:  # Saturday
            root_date = root_date - timedelta(days=1)
            logger.info(f"Normalized Saturday to Friday: {root_date.date()}")
        elif root_date.weekday() == 6:  # Sunday
            root_date = root_date - timedelta(days=2)
            logger.info(f"Normalized Sunday to Friday: {root_date.date()}")
            
        logger.info(f"Filtering {len(stocks)} F&O stocks for date {root_date.date()} (cache size: {len(self._historical_data_cache)})...")
        
        signals: List[Dict] = []
        cross_above: List[Dict] = []
        cross_below: List[Dict] = []
        bullish_reversal: List[Dict] = []
        bearish_reversal: List[Dict] = []
        cpr_touch_above: List[Dict] = []
        cpr_touch_below: List[Dict] = []
        drsi_bullish: List[Dict] = []
        drsi_bearish: List[Dict] = []
        drsi_reversal_bullish: List[Dict] = []
        drsi_reversal_bearish: List[Dict] = []
        processed = 0
        failed = 0
        start_time = time.time()
        
        # Phase 1: CPR processing (existing logic, unchanged)
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(self.process_stock, symbol, root_date): symbol for symbol in stocks}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result = future.result(timeout=25)  # 25 second timeout per stock
                    if result:
                        if result.get('signal'):
                            signals.append(result['signal'])
                        cross = result.get('weekly_cross')
                        if cross and cross.get('payload'):
                            if cross.get('status') == self.CROSS_ABOVE_WEEKLY:
                                cross_above.append(cross['payload'])
                            elif cross.get('status') == self.CROSS_BELOW_WEEKLY:
                                cross_below.append(cross['payload'])
                        
                        if result.get('bullish_reversal'):
                            bullish_reversal.append(result['bullish_reversal'])
                        
                        if result.get('bearish_reversal'):
                            bearish_reversal.append(result['bearish_reversal'])
                        
                        if result.get('cpr_touch_above'):
                            cpr_touch_above.append(result['cpr_touch_above'])
                        
                        if result.get('cpr_touch_below'):
                            cpr_touch_below.append(result['cpr_touch_below'])
                            
                        if result.get('drsi_bullish'):
                            drsi_bullish.append(result['drsi_bullish'])
                            
                        if result.get('drsi_bearish'):
                            drsi_bearish.append(result['drsi_bearish'])
                            
                        if result.get('drsi_reversal_bullish'):
                            drsi_reversal_bullish.append(result['drsi_reversal_bullish'])
                            
                        if result.get('drsi_reversal_bearish'):
                            drsi_reversal_bearish.append(result['drsi_reversal_bearish'])
                    processed += 1
                    if processed % 10 == 0:
                        elapsed = time.time() - start_time
                        logger.info(f"CPR Progress: {processed}/{len(stocks)} ({failed} failed) in {elapsed:.1f}s")
                except Exception as e:
                    logger.debug(f"Stock {symbol} failed: {e}")
                    failed += 1
                    processed += 1
        
        cpr_time = time.time() - start_time
        logger.info(f"CPR phase complete in {cpr_time:.1f}s. Starting IV percentile computation...")
        
        # Phase 2: IV Percentile computation (batch-optimized, separate from CPR)
        high_iv_stocks = self._batch_compute_iv_percentiles(stocks)
        
        total_time = time.time() - start_time
        logger.info(
            f"Filter complete: {len(signals)} match criteria, "
            f"{len(cross_above)} crossed above weekly CPR, {len(cross_below)} crossed below weekly CPR, "
            f"{len(bullish_reversal)} bullish reversal, {len(bearish_reversal)} bearish reversal, "
            f"{len(cpr_touch_above)} CPR touch above, {len(cpr_touch_below)} CPR touch below, "
            f"{len(high_iv_stocks)} high IV percentile "
            f"({failed} failed) in {total_time:.1f}s. Cache: {len(self._historical_data_cache)} entries"
        )
        return {
            'signals': sorted(signals, key=lambda x: x['symbol']),
            'weekly_cross': {
                'crossed_above': sorted(cross_above, key=lambda x: x['symbol']),
                'crossed_below': sorted(cross_below, key=lambda x: x['symbol'])
            },
            'reversal': {
                'bullish': sorted(bullish_reversal, key=lambda x: x['symbol']),
                'bearish': sorted(bearish_reversal, key=lambda x: x['symbol'])
            },
            'cpr_touch': {
                'closed_above': sorted(cpr_touch_above, key=lambda x: x['symbol']),
                'closed_below': sorted(cpr_touch_below, key=lambda x: x['symbol'])
            },
            'drsi_filter': {
                'bullish': sorted(drsi_bullish, key=lambda x: x['symbol']),
                'bearish': sorted(drsi_bearish, key=lambda x: x['symbol']),
                'reversal_bullish': sorted(drsi_reversal_bullish, key=lambda x: x['symbol']),
                'reversal_bearish': sorted(drsi_reversal_bearish, key=lambda x: x['symbol'])
            },
            'high_iv_stocks': sorted(high_iv_stocks, key=lambda x: x['iv_percentile'], reverse=True)
        }

    def clear_cache(self):
        with self._cache_lock:
            self._historical_data_cache.clear()
            logger.info("Cache cleared")
