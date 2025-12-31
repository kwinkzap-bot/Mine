import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from dataclasses import dataclass
import os
from dotenv import load_dotenv
import logging
from typing import Optional, List, Dict, Tuple, cast, Union
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from service.cpr_service import CPRService

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
    current_price: float
    current_high: float
    current_low: float
    previous_close: float

# Type aliases for clearer payload structure
SignalPayload = Dict[str, Union[float, str]]
WeeklyCrossPayload = Dict[str, List[SignalPayload]]
FilterResult = Dict[str, Union[List[SignalPayload], WeeklyCrossPayload]]

class CPRFilterService:
    PERCENTAGE_DIFF_THRESHOLD = 3.0
    INDEX_SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
    API_RATE_LIMIT_DELAY = 0.05  # Reduced from 0.1 - works better with thread pool
    MAX_WORKERS = 4  # Reduced from 8 to avoid API throttling

    CROSS_ABOVE_WEEKLY = "↗ CROSSED ABOVE WEEKLY CPR"
    CROSS_BELOW_WEEKLY = "↘ CROSSED BELOW WEEKLY CPR"

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

    def get_hist_data(self, symbol: str, days: int, interval='day') -> Optional[pd.DataFrame]:
        key = f"{symbol}_{days}_{interval}"
        with self._cache_lock:
            if key in self._historical_data_cache: 
                return self._historical_data_cache[key]

        token = self.get_token(symbol)
        if not token:
            return None

        self._rate_limit()
        try:
            start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            end = datetime.now().strftime('%Y-%m-%d')
            data = self.kite.historical_data(token, start, end, interval)
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



    def get_prev_week_range(self) -> Tuple[datetime, datetime]:
        today = datetime.now()
        days_to_fri = 3 if today.weekday() == 0 else today.weekday() + 2
        fri = today - timedelta(days=days_to_fri)
        mon = fri - timedelta(days=4)
        return mon, fri

    def get_prev_month_range(self) -> Tuple[datetime, datetime]:
        today = datetime.now()
        first_current = today.replace(day=1)
        last_prev = first_current - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
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

    def calc_cpr_levels(self, symbol: str) -> Optional[CPRLevels]:
        # Daily CPR (prev day)
        logger.debug(f"Fetching daily data for {symbol}...")
        daily_df = self.get_hist_data(symbol, 5)
        if daily_df is None or len(daily_df) < 2: 
            logger.debug(f"Insufficient daily data for {symbol}")
            return None
        h, l, c = float(daily_df.iloc[-2]['high']), float(daily_df.iloc[-2]['low']), float(daily_df.iloc[-2]['close'])
        d_pp, d_bc, d_tc = CPRService.calculate_cpr(h, l, c)
        
        # Weekly CPR (prev week Mon-Fri)
        logger.debug(f"Fetching weekly data for {symbol}...")
        mon, fri = self.get_prev_week_range()
        week_df = self.get_hist_range(symbol, mon, fri)
        if week_df is None: 
            logger.debug(f"No weekly data for {symbol} ({mon.date()} to {fri.date()})")
            return None
        w_h, w_l, w_c = float(week_df['high'].max()), float(week_df['low'].min()), float(week_df['close'].iloc[-1])
        w_pp, w_bc, w_tc = CPRService.calculate_cpr(w_h, w_l, w_c)
        
        # Monthly CPR (prev month)
        logger.debug(f"Fetching monthly data for {symbol}...")
        mon_start, mon_end = self.get_prev_month_range()
        month_df = self.get_hist_range(symbol, mon_start, mon_end)
        if month_df is None: 
            logger.debug(f"No monthly data for {symbol} ({mon_start.date()} to {mon_end.date()})")
            return None
        m_h, m_l, m_c = float(month_df['high'].max()), float(month_df['low'].min()), float(month_df['close'].iloc[-1])
        m_pp, m_bc, m_tc = CPRService.calculate_cpr(m_h, m_l, m_c)
        
        # Current candle
        curr_price, curr_high, curr_low = [float(daily_df.iloc[-1][col]) for col in ['close', 'high', 'low']]
        
        logger.debug(f"CPR levels calculated for {symbol}")
        return CPRLevels(d_pp, d_bc, d_tc, w_pp, w_bc, w_tc, m_pp, m_bc, m_tc, 
                        curr_price, curr_high, curr_low, c)

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
        
        buy_cond = cpr.weekly_bc <= cpr.daily_tc
        if (self.is_above_all_tc(cpr.current_price, cpr.daily_tc, cpr.weekly_tc, cpr.monthly_tc) and
            cpr.weekly_bc > cpr.monthly_bc and wbc_mtc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
            buy_cond and ((cpr.current_low <= cpr.weekly_tc <= cpr.current_price) or 
                         (cpr.current_low <= cpr.monthly_tc <= cpr.current_price))):
            return "✅ ABOVE CPR TC"
        
        sell_cond = cpr.weekly_tc >= cpr.daily_bc
        if (self.is_below_all_bc(cpr.current_price, cpr.daily_bc, cpr.weekly_bc, cpr.monthly_bc) and
            cpr.weekly_tc < cpr.monthly_tc and wtc_mbc_diff <= self.PERCENTAGE_DIFF_THRESHOLD and
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
        return 0.0, 0.0, 0.0

    def detect_weekly_cross(self, levels: CPRLevels) -> Optional[str]:
        prev_close = levels.previous_close
        cross_above = (
            prev_close <= levels.weekly_pp
            and levels.current_price > levels.weekly_tc
            and levels.current_low <= levels.weekly_pp  # low pierced below then moved above
        )
        cross_below = (
            prev_close >= levels.weekly_pp
            and levels.current_price < levels.weekly_bc
            and levels.current_high >= levels.weekly_pp  # high was above then moved below
        )

        if cross_above and not cross_below:
            return self.CROSS_ABOVE_WEEKLY
        if cross_below and not cross_above:
            return self.CROSS_BELOW_WEEKLY
        return None

    def process_stock(self, symbol: str) -> Optional[Dict]:
        try:
            cpr = self.calc_cpr_levels(symbol)
            if not cpr:
                logger.debug(f"{symbol}: No CPR levels")
                return None
            
            primary_status = self.evaluate_status(cpr)
            weekly_cross_status = self.detect_weekly_cross(cpr)

            payloads: Dict[str, Optional[Dict]] = {'signal': None, 'weekly_cross': None}

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
                        'd_gap': cross_gaps[0],
                        'w_gap': cross_gaps[1],
                        'm_gap': cross_gaps[2]
                    }
                }

            return payloads if payloads['signal'] or payloads['weekly_cross'] else None
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def filter_cpr_stocks(self) -> FilterResult:
        stocks = self.get_fo_stocks()
        # stocks = ["COLPAL"]
        logger.info(f"Filtering {len(stocks)} F&O stocks (cache size: {len(self._historical_data_cache)})...")
        
        signals: List[Dict] = []
        cross_above: List[Dict] = []
        cross_below: List[Dict] = []
        processed = 0
        failed = 0
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(self.process_stock, symbol): symbol for symbol in stocks}
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
            f"{len(cross_above)} crossed above weekly CPR, {len(cross_below)} crossed below weekly CPR "
            f"({failed} failed) in {total_time:.1f}s. Cache: {len(self._historical_data_cache)} entries"
        )
        return {
            'signals': sorted(signals, key=lambda x: x['symbol']),
            'weekly_cross': {
                'crossed_above': sorted(cross_above, key=lambda x: x['symbol']),
                'crossed_below': sorted(cross_below, key=lambda x: x['symbol'])
            }
        }

    def clear_cache(self):
        with self._cache_lock:
            self._historical_data_cache.clear()
            logger.info("Cache cleared")
