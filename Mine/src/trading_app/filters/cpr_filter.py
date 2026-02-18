import pandas as pd
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

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global persistent cache (survives between filter requests)
_global_cache = {}
_global_cache_lock = threading.Lock()

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
    API_RATE_LIMIT_DELAY = 0.05  # Reduced from 0.1 - works better with thread pool
    MAX_WORKERS = 4  # Reduced from 8 to avoid API throttling

    CROSS_ABOVE_WEEKLY = "↗ CROSSED ABOVE WEEKLY CPR"
    CROSS_BELOW_WEEKLY = "↘ CROSSED BELOW WEEKLY CPR"
    BULLISH_REVERSAL = "🐂 BULLISH REVERSAL (M S1/Low)"
    BEARISH_REVERSAL = "🐻 BEARISH REVERSAL (M R1/High)"

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
        self._last_api_call = 0.0
        self._api_lock = threading.Lock()
        self._load_instruments()

    def _rate_limit(self):
        with self._api_lock:
            elapsed = time.time() - self._last_api_call
            if elapsed < self.API_RATE_LIMIT_DELAY:
                time.sleep(self.API_RATE_LIMIT_DELAY - elapsed)
            self._last_api_call = time.time()

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

    def get_hist_data(self, symbol: str, days: int, interval='day', end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
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
        # Daily CPR (prev day)
        # We need historical data up strictly to root_date
        # get_hist_data with 10 days ensuring we cover enough trading days
        logger.debug(f"Fetching daily data for {symbol} up to {root_date.date()}...")
        daily_df = self.get_hist_data(symbol, 10, end_date=root_date)
        
        if daily_df is None or daily_df.empty:
            logger.debug(f"No daily data for {symbol}")
            return None
            
        # Filter to ensure we only look at data on or before root_date
        # Convert index to date for comparison
        daily_df = daily_df[daily_df.index.date <= root_date.date()]
        
        if len(daily_df) < 2: 
            logger.debug(f"Insufficient daily data for {symbol} (rows={len(daily_df)})")
            return None
            
        # The last row should be the 'current' candle (root_date)
        # The second to last row should be the 'previous' candle (for Daily CPR)
        
        # Check if the last row is actually the root_date
        last_date = daily_df.index[-1].date()
        if last_date != root_date.date():
             # If root_date is a holiday/weekend, we might get data up to previous trading day.
             # But the requirement is likely: "Filter as of root_date". 
             # If root_date is not a trading day, we should probably use the last available trading day?
             # For now, let's assume root_date is a valid trading day or we use the latest available.
             pass

        h, l, c = float(daily_df.iloc[-2]['high']), float(daily_df.iloc[-2]['low']), float(daily_df.iloc[-2]['close'])
        d_pp, d_bc, d_tc = CPRService.calculate_cpr(h, l, c)
        
        # Weekly CPR (prev week Mon-Fri relative to root_date)
        logger.debug(f"Fetching weekly data for {symbol}...")
        mon, fri = self.get_prev_week_range(root_date)
        week_df = self.get_hist_range(symbol, mon, fri)
        if week_df is None or week_df.empty: 
            logger.debug(f"No weekly data for {symbol} ({mon.date()} to {fri.date()})")
            return None
        w_h, w_l, w_c = float(week_df['high'].max()), float(week_df['low'].min()), float(week_df['close'].iloc[-1])
        w_pp, w_bc, w_tc = CPRService.calculate_cpr(w_h, w_l, w_c)
        
        # Monthly CPR (prev month relative to root_date)
        logger.debug(f"Fetching monthly data for {symbol}...")
        mon_start, mon_end = self.get_prev_month_range(root_date)
        month_df = self.get_hist_range(symbol, mon_start, mon_end)
        if month_df is None or month_df.empty: 
            logger.debug(f"No monthly data for {symbol} ({mon_start.date()} to {mon_end.date()})")
            return None
        m_h, m_l, m_c = float(month_df['high'].max()), float(month_df['low'].min()), float(month_df['close'].iloc[-1])
        m_pp, m_bc, m_tc = CPRService.calculate_cpr(m_h, m_l, m_c)
        
        # Calculate Monthly S1 and R1
        m_s1 = (2 * m_pp) - m_h
        m_r1 = (2 * m_pp) - m_l
        
        # Yearly CPR (prev year relative to root_date)
        logger.debug(f"Fetching yearly data for {symbol}...")
        year_start, year_end = self.get_prev_year_range(root_date)
        year_df = self.get_hist_range(symbol, year_start, year_end)
        if year_df is None or year_df.empty: 
            logger.debug(f"No yearly data for {symbol} ({year_start.date()} to {year_end.date()})")
            return None
        y_h, y_l, y_c = float(year_df['high'].max()), float(year_df['low'].min()), float(year_df['close'].iloc[-1])
        y_pp, y_bc, y_tc = CPRService.calculate_cpr(y_h, y_l, y_c)
        
        # Current candle (root_date)
        curr_price, curr_open, curr_high, curr_low = [float(daily_df.iloc[-1][col]) for col in ['close', 'open', 'high', 'low']]
        
        logger.debug(f"CPR levels calculated for {symbol}")
        return CPRLevels(d_pp, d_bc, d_tc, w_pp, w_bc, w_tc, m_pp, m_bc, m_tc, m_s1, m_r1,
                        y_pp, y_bc, y_tc, curr_price, curr_open, curr_high, curr_low, c, m_h, m_l)

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
            return "✅ ABOVE CPR TC"
        
        sell_cond = cpr.weekly_tc >= cpr.daily_bc
        if (self.is_below_all_bc(cpr.current_price, cpr.daily_bc, cpr.weekly_bc, cpr.monthly_bc) and
            cpr.current_price < cpr.yearly_bc and
            cpr.weekly_tc < cpr.monthly_tc and cpr.monthly_tc < cpr.yearly_tc and
            wtc_mbc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            mtc_ybc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            sell_cond and ((cpr.current_high >= cpr.weekly_bc >= cpr.current_price) or 
                          (cpr.current_high >= cpr.monthly_bc >= cpr.current_price))):
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
            return self.CROSS_ABOVE_WEEKLY
        if cross_below and not cross_above:
            return self.CROSS_BELOW_WEEKLY
        return None

    def detect_bullish_reversal(self, levels: CPRLevels) -> Optional[str]:
        """
        List 1(Cross Above S1 and Previous Low). 
        Daily candle price touch the Monthly S1 or Previous Month Low and close above the monthly S1 and Previous Month Low.
        """
        touched_support = (levels.current_low <= levels.monthly_s1) or (levels.current_low <= levels.prev_month_low)
        closed_above_support = (levels.current_price > levels.monthly_s1) and (levels.current_price > levels.prev_month_low)
        
        # Condition 3: Green Candle (Close > Open)
        is_green_candle = levels.current_price > levels.current_open

        if touched_support and closed_above_support and is_green_candle:
            return self.BULLISH_REVERSAL
        return None

    def detect_bearish_reversal(self, levels: CPRLevels) -> Optional[str]:
        """
        List 2(Cross Below R1 and Previous High). 
        Daily Candle price touch the Monthly R1 or Previous Month High and close below the monthly R1 and Previous Month High.
        """
        touched_resistance = (levels.current_high >= levels.monthly_r1) or (levels.current_high >= levels.prev_month_high)
        closed_below_resistance = (levels.current_price < levels.monthly_r1) and (levels.current_price < levels.prev_month_high)
        
        # Condition 3: Red Candle (Close < Open)
        is_red_candle = levels.current_price < levels.current_open

        if touched_resistance and closed_below_resistance and is_red_candle:
            return self.BEARISH_REVERSAL
        return None

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

            payloads: Dict[str, Optional[Dict]] = {
                'signal': None, 
                'weekly_cross': None,
                'bullish_reversal': None,
                'bearish_reversal': None
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

            return payloads if any(payloads.values()) else None
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def filter_cpr_stocks(self, root_date: Optional[datetime] = None) -> FilterResult:
        stocks = self.get_fo_stocks()
        # stocks = ["COLPAL"]
        if root_date is None:
            root_date = datetime.now()
            
        logger.info(f"Filtering {len(stocks)} F&O stocks for date {root_date.date()} (cache size: {len(self._historical_data_cache)})...")
        
        signals: List[Dict] = []
        cross_above: List[Dict] = []
        cross_below: List[Dict] = []
        bullish_reversal: List[Dict] = []
        bearish_reversal: List[Dict] = []
        processed = 0
        failed = 0
        start_time = time.time()
        
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
                    processed += 1
                    if processed % 10 == 0:
                        elapsed = time.time() - start_time
                        logger.info(f"Progress: {processed}/{len(stocks)} ({failed} failed) in {elapsed:.1f}s")
                except Exception as e:
                    logger.debug(f"Stock {symbol} failed: {e}")
                    failed += 1
                    processed += 1
        
        total_time = time.time() - start_time
        logger.info(
            f"Filter complete: {len(signals)} match criteria, "
            f"{len(cross_above)} crossed above weekly CPR, {len(cross_below)} crossed below weekly CPR, "
            f"{len(bullish_reversal)} bullish reversal, {len(bearish_reversal)} bearish reversal "
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
            }
        }

    def clear_cache(self):
        with self._cache_lock:
            self._historical_data_cache.clear()
            logger.info("Cache cleared")
