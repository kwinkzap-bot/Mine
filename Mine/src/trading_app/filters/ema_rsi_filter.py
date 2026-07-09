"""
EMA/RSI 208 Filter Service
Scans all F&O stocks to find:
  - Weekly candle touching EMA(208) OR RSI(208) in [49, 51] range
  - Daily candle touching EMA(208) OR RSI(208) in [49, 51] range
"""
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logger = logging.getLogger(__name__)

# Global cache shared across requests
_ema_filter_cache: Dict = {}
_ema_cache_lock = threading.Lock()

_GLOBAL_EMA_HIST_CACHE = {}
_GLOBAL_EMA_CACHE_LOCK = threading.Lock()

# Weekly params
EMA_PERIOD = 208
RSI_PERIOD = 208

# Daily params (separate periods)
DAILY_EMA_PERIOD = 88
DAILY_RSI_PERIOD = 88

# Shared
# Changed from 49-51 range to crossed above 51
RSI_CROSSOVER = 51.0
# EMA "touch" = current candle's LOW <= EMA <= HIGH (actual wick cross)
# No % tolerance — strict candle range only

# EMA Narrow scanner: all these EMAs must sit inside a tight % band of price
# on EVERY timeframe of the selected group (monthly/weekly/daily combos)
NARROW_EMA_PERIODS = [20, 50, 100, 200]
NARROW_DEFAULT_THRESHOLD_PCT = 1.5

NARROW_GROUPS = {
    "mwd": ["monthly", "weekly", "daily"],
    "mw":  ["monthly", "weekly"],
    "wd":  ["weekly", "daily"],
}
# Daily-bar history needed for a stable EMA(200) on each resampled timeframe
NARROW_FETCH_DAYS = {"daily": 900, "weekly": 2500, "monthly": 7000}
NARROW_RESAMPLE   = {"daily": None, "weekly": "W", "monthly": "M"}


def _calc_ema(prices: List[float], period: int) -> List[float]:
    """Exponential Moving Average (EMA) — strictly matches TradingView's standard EMA indicator.
    TradingView initializes the first EMA value using a Simple Moving Average (SMA) of the first 'period' bars.
    """
    if len(prices) < period:
        return [float("nan")] * len(prices)
        
    result = [float("nan")] * len(prices)
    k = 2.0 / (period + 1)
    
    # Init first calculated value using SMA
    sma_seed = sum(prices[:period]) / period
    result[period - 1] = sma_seed
    
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
        
    return result


def _calc_rsi(prices: List[float], period: int = 208) -> List[float]:
    """Wilder's RSI — matches TradingView RSI calculation."""
    if len(prices) <= period:
        return [float("nan")] * len(prices)

    diffs = np.diff(prices)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)

    result = [float("nan")] * len(prices)
    alpha = 1.0 / period
    up_rma = np.mean(gains[:period])
    dn_rma = np.mean(losses[:period])
    result[period] = 100.0 if dn_rma == 0 else 100.0 - 100.0 / (1 + up_rma / dn_rma)

    for i in range(period + 1, len(prices)):
        up_rma = alpha * gains[i - 1] + (1 - alpha) * up_rma
        dn_rma = alpha * losses[i - 1] + (1 - alpha) * dn_rma
        result[i] = 100.0 if dn_rma == 0 else 100.0 - 100.0 / (1 + up_rma / dn_rma)

    return result


def _weekly_resample(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV data to weekly (Mon–Fri)."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    weekly = df.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    return weekly


def _monthly_resample(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV data to monthly."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    try:
        monthly = df.resample("ME").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
    except ValueError:
        monthly = df.resample("M").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
    return monthly


class EmaRsiFilterService:
    """Filter F&O stocks by EMA-208 touch or RSI-208 in 49–51 range (Weekly + Daily)."""

    INDEX_SYMBOLS = {
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
        "NIFTY MIDCAP 150", "NIFTY AUTO", "NIFTY Smallcap 100", "NIFTY SMLCAP 100",
        "NIFTY FMCG", "NIFTY METAL", "NIFTY PHARAMA", "NIFTY PHARMA",
        "NIFTY PSU BANK", "NIFTY IT"
    }
    MAX_WORKERS = 3

    TIMEFRAME_CONFIG = {
        "1minute":  {"interval": "minute",   "period": 60,  "fetch_days": 3,    "resample": None},
        "5minute":  {"interval": "5minute",  "period": 75,  "fetch_days": 10,   "resample": None},
        "15minute": {"interval": "15minute", "period": 125, "fetch_days": 20,   "resample": None},
        "60minute": {"interval": "60minute", "period": 137, "fetch_days": 60,   "resample": None},
        "daily":    {"interval": "day",      "period": 88,  "fetch_days": 364,  "resample": None},
        "weekly":   {"interval": "day",      "period": 208, "fetch_days": 1600, "resample": "W"},
        "monthly":  {"interval": "day",      "period": 48,  "fetch_days": 1800, "resample": "M"},
    }


    def __init__(self, kite_instance=None):
        from trading_app.service.provider_logic import get_data_provider
        self.kite = kite_instance or get_data_provider()
        self._instruments: List[Dict] = []
        self._fo_stocks: Optional[List[str]] = None
        self._hist_cache: Dict = _ema_filter_cache
        self._cache_lock = _ema_cache_lock
        self._load_instruments()

    def _load_instruments(self):
        if not self._instruments:
            try:
                self._instruments = self.kite.instruments("NSE")
            except Exception as e:
                logger.error(f"Instrument load failed: {e}")
                self._instruments = []

    def _get_token(self, symbol: str) -> Optional[Union[int, str]]:
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            return f"NSE:{symbol}-EQ"
            
        for inst in self._instruments:
            if inst.get("instrument_type") == "EQ" and (inst.get("tradingsymbol") == symbol or inst.get("name") == symbol):
                return inst.get("instrument_token")
        return None

    def _fetch_hist(self, symbol: str, days: int, interval: str = "day",
                    end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        end   = end_date if end_date else datetime.now()
        start = end - timedelta(days=days)
        cache_key = f"{symbol}_{start.date()}_{end.date()}_{interval}_{days}"

        with _GLOBAL_EMA_CACHE_LOCK:
            cached = _GLOBAL_EMA_HIST_CACHE.get(cache_key)
            if cached is not None:
                return cached

        token = self._get_token(symbol)
        if not token:
            return None
        try:
            # Chunk historical data to bypass the 2000-day API limit strictly
            # and achieve TradingView-like mathematical accuracy for EMA
            max_days_per_req = 1960
            all_data = []
            
            curr_end = end
            curr_start = max(start, curr_end - timedelta(days=max_days_per_req))
            
            while curr_start < curr_end:
                try:
                    chunk = self.kite.historical_data(token, curr_start.strftime("%Y-%m-%d"), curr_end.strftime("%Y-%m-%d"), interval)
                    if chunk:
                        all_data.extend(chunk)
                except Exception as e:
                    # Proceed silently on sub-chunk errors (like "Too many requests") to at least yield partial data
                    logger.debug(f"Chunk fetch error for {symbol}: {e}")
                    pass
                
                curr_end = curr_start
                curr_start = max(start, curr_end - timedelta(days=max_days_per_req))
                if not all_data:
                    # If the very first recent chunk fails, stop entirely
                    break
                    
            if not all_data:
                return None
                
            df = pd.DataFrame(all_data).set_index("date")
            # Clear duplicate overlaps across chunks
            df = df[~df.index.duplicated(keep='last')]
            df = df.sort_index()
            df.index = pd.to_datetime(df.index)
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["close"])
            with _GLOBAL_EMA_CACHE_LOCK:
                _GLOBAL_EMA_HIST_CACHE[cache_key] = df
            return df
        except Exception as e:
            logger.warning(f"[hist] {symbol} ({interval}, {days}d): {e}")
            return None

    def _get_fo_stocks(self) -> List[str]:
        if self._fo_stocks is not None:
            return self._fo_stocks
        try:
            from trading_app.filters.stock_list_store import get_fo_stocks
            self._fo_stocks = get_fo_stocks(self.kite)
            return self._fo_stocks
        except Exception as e:
            logger.error(f"FO stocks load failed: {e}")
            return []

    def _get_equity_stocks(self) -> List[str]:
        """All NSE equity stocks (file-cached list) — used by the EMA Narrow scanner."""
        try:
            from trading_app.filters.stock_list_store import get_equity_stocks
            return get_equity_stocks(self.kite)
        except Exception as e:
            logger.error(f"Equity stocks load failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Core analysis per stock
    # ------------------------------------------------------------------

    def _analyse_timeframe(self, symbol: str, current_price: float, df: Optional[pd.DataFrame], period: int, resample: Optional[str] = None, near_miss_pct: float = 15.0) -> Optional[Dict]:
        if df is None or len(df) < 20:
            return None

        if resample == 'W':
            df = _weekly_resample(df)
        elif resample == 'M':
            df = _monthly_resample(df)

        if len(df) < period:
            return None

        closes = df["close"].tolist()
        highs  = df["high"].tolist()
        lows   = df["low"].tolist()

        ema_vals = _calc_ema(closes, period)
        rsi_vals = _calc_rsi(closes, period)

        last_ema   = ema_vals[-1]
        last_rsi   = rsi_vals[-1]
        prev_rsi   = rsi_vals[-2] if len(rsi_vals) > 1 else float('nan')
        last_high  = highs[-1]
        last_low   = lows[-1]
        last_close = closes[-1]

        if last_ema != last_ema:  # nan check
            return None

        ema_pct_diff = round(abs(last_close - last_ema) / last_ema * 100, 2)
        margin = last_ema * 0.005
        ema_touched  = bool((last_low - margin) <= last_ema <= (last_high + margin))
        rsi_valid    = (last_rsi == last_rsi and prev_rsi == prev_rsi)
        rsi_crossed_above = bool(rsi_valid and prev_rsi <= RSI_CROSSOVER and last_rsi > RSI_CROSSOVER)

        matched = bool(ema_touched or rsi_crossed_above)

        return {
            "symbol":        symbol,
            "current_price": float(current_price),
            "close":         round(float(last_close), 2),
            "ema":           round(float(last_ema), 2),
            "rsi":           round(float(last_rsi), 2) if rsi_valid else None,
            "ema_pct_diff":  ema_pct_diff,
            "ema_touched":   ema_touched,
            "rsi_in_range":  rsi_crossed_above,
            "matched":       matched,
            "trigger": (
                "EMA+RSI" if ema_touched and rsi_crossed_above
                else ("EMA Touch" if ema_touched else "RSI > 51")
            ) if matched else "—",
        }

    def _analyse_ema_narrow(self, symbol: str, current_price: float, df: Optional[pd.DataFrame],
                            resample: Optional[str] = None,
                            threshold_pct: float = NARROW_DEFAULT_THRESHOLD_PCT) -> Optional[Dict]:
        """Check whether EMA(20/50/100/200) are all bunched within threshold_pct of price."""
        if df is None or len(df) < 20:
            return None

        if resample == 'W':
            df = _weekly_resample(df)
        elif resample == 'M':
            df = _monthly_resample(df)

        closes = df["close"].tolist()
        max_period = max(NARROW_EMA_PERIODS)
        if len(closes) < max_period:
            return None

        emas: Dict[int, float] = {}
        for p in NARROW_EMA_PERIODS:
            val = _calc_ema(closes, p)[-1]
            if val != val:  # nan check
                return None
            emas[p] = float(val)

        last_close = float(closes[-1])
        if last_close <= 0:
            return None

        ema_high = max(emas.values())
        ema_low  = min(emas.values())
        spread_pct = round((ema_high - ema_low) / last_close * 100, 2)
        matched = bool(spread_pct <= threshold_pct)
        price_inside = bool(ema_low <= last_close <= ema_high)

        return {
            "symbol":        symbol,
            "current_price": float(current_price),
            "close":         round(last_close, 2),
            "ema20":         round(emas[20], 2),
            "ema50":         round(emas[50], 2),
            "ema100":        round(emas[100], 2),
            "ema200":        round(emas[200], 2),
            "spread_pct":    spread_pct,
            "price_inside":  price_inside,
            "matched":       matched,
        }

    def run_ema_narrow_filter(self, root_date: Optional[datetime] = None, group: str = "mwd",
                              threshold_pct: float = NARROW_DEFAULT_THRESHOLD_PCT,
                              progress_cb=None) -> Dict:
        """Scan ALL NSE equity stocks for EMA(20/50/100/200) compression on EVERY
        timeframe of the selected group ('mwd' = Month+Week+Day, 'mw', 'wd').

        Timeframes are checked cheapest-first (daily → weekly → monthly); the long
        history needed by higher timeframes is only fetched for stocks that are
        still narrow on the lower ones, which keeps the scan fast.

        Stocks are processed in batches of 100; after each batch
        progress_cb(done, total, partial) is invoked, where partial is a
        snapshot {'results': [...], 'nearest': [...]} of everything found so
        far — so callers can show results incrementally while the scan runs."""
        group_tfs = NARROW_GROUPS.get(group, NARROW_GROUPS["mwd"])
        gate_order = list(reversed(group_tfs))  # lowest timeframe first (cheapest fetch)

        stocks = self._get_equity_stocks()
        if not stocks:
            logger.warning("EMA Narrow filter: no equity stocks found")
            return {"results": [], "nearest": []}

        logger.info(f"EMA Narrow filter: scanning {len(stocks)} equity stocks for "
                    f"{'+'.join(group_tfs)} (threshold {threshold_pct}%)...")

        nse_symbols = [f"NSE:{s}" for s in stocks]
        price_map: Dict[str, float] = {}
        batch_size = 500
        for i in range(0, len(nse_symbols), batch_size):
            batch = nse_symbols[i: i + batch_size]
            try:
                quotes = self.kite.quote(batch)
                for sym, data in quotes.items():
                    plain = sym.replace("NSE:", "")
                    price_map[plain] = float(data.get("last_price", 0))
            except Exception as e:
                logger.warning(f"Batch quote failed: {e}")

        results: List[Dict] = []
        all_results: List[Dict] = []

        def process_stock(symbol: str):
            price = price_map.get(symbol, 0.0)

            # Gate cheapest-first: only fetch the deep history a higher timeframe
            # needs when the stock is still narrow on every lower timeframe.
            tf_res: Dict[str, Dict] = {}
            for tf in gate_order:
                df = self._fetch_hist(symbol, days=NARROW_FETCH_DAYS[tf],
                                      interval="day", end_date=root_date)
                r = self._analyse_ema_narrow(symbol, price, df, NARROW_RESAMPLE[tf], threshold_pct)
                if r is None:
                    # Not enough history for EMA(200) on this timeframe — can't verify, skip stock
                    return symbol, None
                tf_res[tf] = r
                if not r["matched"]:
                    break  # failed this gate — no point fetching longer history

            lowest = tf_res[gate_order[0]]
            spreads = {tf: tf_res[tf]["spread_pct"] for tf in tf_res}
            combined = {
                "symbol":          symbol,
                "current_price":   float(price),
                "close":           lowest["close"],
                "spread_daily":    spreads.get("daily"),
                "spread_weekly":   spreads.get("weekly"),
                "spread_monthly":  spreads.get("monthly"),
                "max_spread_pct":  round(max(spreads.values()), 2),
                "price_inside":    lowest["price_inside"],
                "matched":         bool(len(tf_res) == len(group_tfs)
                                        and all(r["matched"] for r in tf_res.values())),
                "tfs":             tf_res,
            }
            return symbol, combined

        workers_count = self.MAX_WORKERS
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            workers_count = 5

        done_count = 0
        total = len(stocks)
        batch_size = 100

        def _snapshot() -> Dict:
            """Sorted copy of everything found so far (tightest compression first)."""
            return {
                "results": sorted(results, key=lambda r: r.get("max_spread_pct", 999)),
                "nearest": sorted(
                    [r for r in all_results if not r.get("matched")],
                    key=lambda r: r.get("max_spread_pct", 999)
                )[:10],
            }

        def _report():
            if progress_cb:
                try:
                    progress_cb(done_count, total, _snapshot())
                except Exception:
                    pass

        _report()

        # Process in batches of 100 so partial results stream to the UI
        for i in range(0, total, batch_size):
            chunk = stocks[i: i + batch_size]
            with ThreadPoolExecutor(max_workers=workers_count) as executor:
                futures = {executor.submit(process_stock, s): s for s in chunk}
                for future in as_completed(futures):
                    try:
                        sym, res = future.result(timeout=300)
                        if res:
                            all_results.append(res)
                            if res.get("matched"):
                                results.append(res)
                    except Exception as e:
                        logger.warning(f"EMA Narrow processing error ({e.__class__.__name__}): {e}")
            done_count = min(i + batch_size, total)
            _report()

        final = _snapshot()
        results = final["results"]
        nearest_stocks = final["nearest"]

        logger.info(f"EMA Narrow filter → {'+'.join(group_tfs)}: {len(results)} matches")

        return {
            "results": results,
            "nearest": nearest_stocks,
        }

    # ------------------------------------------------------------------
    # Price fetch helper
    # ------------------------------------------------------------------

    def _get_current_price(self, symbol: str) -> float:
        try:
            quote = self.kite.quote(f"NSE:{symbol}")
            return float(quote.get(f"NSE:{symbol}", {}).get("last_price", 0))
        except Exception:
            # Fallback: use last close from daily data if quote fails
            try:
                df = self._fetch_hist(symbol, days=10)
                if df is not None and not df.empty:
                    return float(df["close"].iloc[-1])
            except Exception:
                pass
            return 0.0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_filter(self, root_date: Optional[datetime] = None, timeframe: str = "daily") -> Dict:
        stocks = self._get_fo_stocks()
        if not stocks:
            logger.warning("EMA filter: no F&O stocks found")
            return {"results": [], "nearest": []}

        logger.info(f"EMA/RSI 208 filter: scanning {len(stocks)} F&O stocks for {timeframe}...")

        # Batch quote all symbols at once to minimise API calls
        nse_symbols = [f"NSE:{s}" for s in stocks]
        price_map: Dict[str, float] = {}

        batch_size = 500
        for i in range(0, len(nse_symbols), batch_size):
            batch = nse_symbols[i: i + batch_size]
            try:
                quotes = self.kite.quote(batch)
                for sym, data in quotes.items():
                    plain = sym.replace("NSE:", "")
                    price_map[plain] = float(data.get("last_price", 0))
            except Exception as e:
                logger.warning(f"Batch quote failed: {e}")

        results: List[Dict] = []
        all_results: List[Dict] = []  # all records (for near-miss tracking)

        config = self.TIMEFRAME_CONFIG.get(timeframe, self.TIMEFRAME_CONFIG["daily"])

        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            if timeframe == "weekly":
                config = {"interval": "week", "period": 208, "fetch_days": 1600, "resample": None}
            elif timeframe == "monthly":
                config = {"interval": "month", "period": 48, "fetch_days": 1800, "resample": None}

        def process_stock(symbol: str):
            price = price_map.get(symbol, 0.0)
            
            df = self._fetch_hist(symbol, days=config["fetch_days"], interval=config["interval"], end_date=root_date)
            res = self._analyse_timeframe(symbol, price, df, config["period"], config["resample"])
            return symbol, res

        # Limit workers if using Fyers to avoid rate limits
        workers_count = self.MAX_WORKERS
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            workers_count = 5

        with ThreadPoolExecutor(max_workers=workers_count) as executor:
            futures = {executor.submit(process_stock, s): s for s in stocks}
            for future in as_completed(futures):
                try:
                    sym, res = future.result(timeout=60)
                    if res:
                        all_results.append(res)
                        if res.get("matched"):
                            results.append(res)
                except Exception as e:
                    logger.warning(f"Stock processing error ({e.__class__.__name__}): {e}")

        def sort_key(r):
            is_ema_rsi = 1 if r.get("trigger") == "EMA+RSI" else 0
            rsi = r.get("rsi") or 0
            return (-is_ema_rsi, abs(rsi - 51.0))

        results.sort(key=sort_key)

        nearest_stocks = sorted(
            [r for r in all_results if r.get("ema") is not None],
            key=lambda r: r.get("ema_pct_diff", 999)
        )[:10]

        logger.info(f"EMA/RSI filter → {timeframe}: {len(results)} matches")

        return {
            "results": results,
            "nearest": nearest_stocks,
        }
