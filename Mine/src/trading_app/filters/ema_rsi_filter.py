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
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logger = logging.getLogger(__name__)

# Global cache shared across requests
_ema_filter_cache: Dict = {}
_ema_cache_lock = threading.Lock()

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


class EmaRsiFilterService:
    """Filter F&O stocks by EMA-208 touch or RSI-208 in 49–51 range (Weekly + Daily)."""

    INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    MAX_WORKERS = 2
    # Kite historical data max is 2000 calendar days per request.
    # To fully mimic TradingView's EMA precision for extremely old stocks (like VBL),
    # we need ~10 years (~3800 days) of data to mathematically converge to TradingView's value. 
    WEEKLY_FETCH_DAYS = 3800
    DAILY_RSI_FETCH_DAYS = 600  # enough for 88 daily bars + rsi warmup

    def __init__(self, kite_instance):
        self.kite = kite_instance
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

    def _get_token(self, symbol: str) -> Optional[int]:
        for inst in self._instruments:
            if inst.get("tradingsymbol") == symbol and inst.get("instrument_type") == "EQ":
                return inst.get("instrument_token")
        return None

    def _fetch_hist(self, symbol: str, days: int, interval: str = "day",
                    end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
        end   = end_date if end_date else datetime.now()
        start = end - timedelta(days=days)
        cache_key = f"{symbol}_{start.date()}_{end.date()}_{interval}"

        with self._cache_lock:
            cached = self._hist_cache.get(cache_key)
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
            with self._cache_lock:
                self._hist_cache[cache_key] = df
            return df
        except Exception as e:
            logger.warning(f"[hist] {symbol} ({interval}, {days}d): {e}")
            return None

    def _get_fo_stocks(self) -> List[str]:
        if self._fo_stocks is not None:
            return self._fo_stocks
        try:
            nfo = self.kite.instruments("NFO")
            fo_set = {
                inst["name"]
                for inst in nfo
                if inst.get("instrument_type") == "FUT"
                and inst.get("name")
                and inst["name"] not in self.INDEX_SYMBOLS
            }
            self._fo_stocks = sorted(fo_set)
            return self._fo_stocks
        except Exception as e:
            logger.error(f"FO stocks load failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Core analysis per stock
    # ------------------------------------------------------------------

    def _analyse_weekly(self, symbol: str, current_price: float,
                        root_date: Optional[datetime] = None,
                        near_miss_pct: float = 15.0) -> Optional[Dict]:
        """
        Weekly analysis: fetch native weekly candles (interval='week').
        Falls back to daily→weekly resample when Zerodha returns fewer than EMA_PERIOD rows.
        Match if:
          - candle low <= EMA208 <= candle high (actual touch), OR close within 5% of EMA OR
          - RSI208 in [49, 51]
        Returns a dict with ema_pct_diff (for near-miss ranking) regardless of match.
        """
        # ── Step 1: Try native weekly candles from Zerodha ────────────────────
        df = self._fetch_hist(symbol, days=self.WEEKLY_FETCH_DAYS,
                              interval="week", end_date=root_date)

        if df is None or len(df) < EMA_PERIOD:
            native_count = len(df) if df is not None else 0
            logger.debug(f"[Weekly] {symbol}: {native_count} weekly bars from Zerodha, "
                         f"need {EMA_PERIOD} — trying daily resample")

            # ── Fallback: fetch daily → resample to weekly ─────────────────
            day_df = self._fetch_hist(symbol, days=self.WEEKLY_FETCH_DAYS,
                                      interval="day", end_date=root_date)
            if day_df is None or len(day_df) < 100:
                return None

            df = _weekly_resample(day_df)
            if len(df) < EMA_PERIOD:
                logger.debug(f"[Weekly] {symbol}: only {len(df)} resampled weekly rows, skipping")
                return None

        closes = df["close"].tolist()
        highs  = df["high"].tolist()
        lows   = df["low"].tolist()

        ema_vals = _calc_ema(closes, EMA_PERIOD)
        rsi_vals = _calc_rsi(closes, RSI_PERIOD)

        last_ema   = ema_vals[-1]
        last_rsi   = rsi_vals[-1]
        prev_rsi   = rsi_vals[-2] if len(rsi_vals) > 1 else float('nan')
        last_high  = highs[-1]
        last_low   = lows[-1]
        last_close = closes[-1]

        if last_ema != last_ema:  # nan check
            return None

        ema_pct_diff = round(abs(last_close - last_ema) / last_ema * 100, 2)

        # Log near-misses so the user can see what's closest
        if ema_pct_diff <= near_miss_pct:
            logger.info(
                f"[Weekly near] {symbol}: close={last_close:.1f}, "
                f"EMA208={last_ema:.1f}, dist={ema_pct_diff:.1f}%"
            )

        # Strict touch with a 0.5% mathematical buffer to account for data history limitations vs TradingView
        margin = last_ema * 0.005
        ema_touched  = bool((last_low - margin) <= last_ema <= (last_high + margin))
        rsi_valid    = (last_rsi == last_rsi and prev_rsi == prev_rsi)
        rsi_crossed_above = bool(rsi_valid and prev_rsi <= RSI_CROSSOVER and last_rsi > RSI_CROSSOVER)

        matched = bool(ema_touched or rsi_crossed_above)

        # Return a full dict always (matched flag lets run_filter separate hits from near-misses)
        return {
            "symbol":        symbol,
            "current_price": float(current_price),
            "weekly_close":  round(float(last_close), 2),
            "ema_208":       round(float(last_ema), 2),
            "rsi_208":       round(float(last_rsi), 2) if rsi_valid else None,
            "ema_pct_diff":  ema_pct_diff,
            "ema_touched":   ema_touched,
            "rsi_in_range":  rsi_crossed_above,
            "matched":       matched,
            "trigger": (
                "EMA+RSI" if ema_touched and rsi_crossed_above
                else ("EMA Touch" if ema_touched else "RSI > 51")
            ) if matched else "—",
        }

    def _analyse_daily(self, symbol: str, current_price: float,
                       root_date: Optional[datetime] = None) -> Optional[Dict]:
        """
        Daily analysis: compute EMA-88 and RSI-88 on daily candles.
        Match if:
          - candle low <= EMA88 <= candle high (touches EMA) OR
          - RSI88 in [49, 51]
        """
        df = self._fetch_hist(symbol, days=self.DAILY_RSI_FETCH_DAYS, end_date=root_date)
        if df is None or len(df) < DAILY_EMA_PERIOD + 50:
            return None

        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()

        ema_vals = _calc_ema(closes, DAILY_EMA_PERIOD)
        rsi_vals = _calc_rsi(closes, DAILY_RSI_PERIOD)

        last_ema = ema_vals[-1]
        last_rsi = rsi_vals[-1]
        prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else float('nan')
        last_high = highs[-1]
        last_low = lows[-1]
        last_close = closes[-1]

        if last_ema != last_ema:
            return None

        # Strict touch: EMA must be within the current candle's actual range (low to high), with 0.5% buffer
        margin = last_ema * 0.005
        ema_touched  = bool((last_low - margin) <= last_ema <= (last_high + margin))
        rsi_valid    = (last_rsi == last_rsi and prev_rsi == prev_rsi)
        rsi_crossed_above = bool(rsi_valid and prev_rsi <= RSI_CROSSOVER and last_rsi > RSI_CROSSOVER)

        if not (ema_touched or rsi_crossed_above):
            return None

        return {
            "symbol": symbol,
            "current_price": float(current_price),
            "daily_close": round(float(last_close), 2),
            "ema_208": round(float(last_ema), 2),
            "rsi_208": round(float(last_rsi), 2) if last_rsi == last_rsi else None,
            "ema_touched": ema_touched,
            "rsi_in_range": rsi_crossed_above,
            "trigger": (
                "EMA+RSI" if ema_touched and rsi_crossed_above
                else ("EMA Touch" if ema_touched else "RSI > 51")
            ),
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

    def run_filter(self, root_date: Optional[datetime] = None) -> Dict:
        """
        Scan all F&O stocks and return:
          {
            "weekly_ema": [...],
            "daily_ema": [...],
          }
        root_date: analyse data up to this date (None = today / live).
        """
        stocks = self._get_fo_stocks()
        if not stocks:
            logger.warning("EMA filter: no F&O stocks found")
            return {"weekly_ema": [], "daily_ema": []}

        logger.info(f"EMA/RSI 208 filter: scanning {len(stocks)} F&O stocks...")

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

        weekly_results: List[Dict] = []
        weekly_all: List[Dict]     = []  # all records (for near-miss tracking)
        daily_results: List[Dict]  = []

        def process_stock(symbol: str):
            price    = price_map.get(symbol, 0.0)
            w_result = self._analyse_weekly(symbol, price, root_date=root_date)
            d_result = self._analyse_daily(symbol, price, root_date=root_date)
            return symbol, w_result, d_result

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(process_stock, s): s for s in stocks}
            for future in as_completed(futures):
                try:
                    sym, w_res, d_res = future.result(timeout=60)
                    if w_res:
                        weekly_all.append(w_res)
                        if w_res.get("matched"):
                            weekly_results.append(w_res)
                    if d_res:
                        daily_results.append(d_res)
                except Exception as e:
                    logger.warning(f"Stock processing error ({e.__class__.__name__}): {e}")

        # Sort matched results: EMA+RSI first, then by RSI proximity to 51.0
        def sort_key(r):
            is_ema_rsi = 1 if r.get("trigger") == "EMA+RSI" else 0
            rsi = r.get("rsi_208") or 0
            return (-is_ema_rsi, abs(rsi - 51.0))

        weekly_results.sort(key=sort_key)
        daily_results.sort(key=sort_key)

        # Top-10 nearest-to-EMA weekly stocks (for diagnostic display)
        nearest_weekly = sorted(
            [r for r in weekly_all if r.get("ema_208") is not None],
            key=lambda r: r.get("ema_pct_diff", 999)
        )[:10]

        # Safe summary log — avoid f-string format spec issues
        if nearest_weekly:
            top = nearest_weekly[0]
            logger.info(
                f"EMA/RSI filter → Weekly: {len(weekly_results)} matches | "
                f"Daily: {len(daily_results)} matches | "
                f"Nearest to EMA208: {top['symbol']} "
                f"({top['ema_pct_diff']} % away)"
            )
        else:
            logger.info(
                f"EMA/RSI filter → Weekly: {len(weekly_results)} | "
                f"Daily: {len(daily_results)} | No weekly data"
            )

        return {
            "weekly_ema":     weekly_results,
            "daily_ema":      daily_results,
            "nearest_weekly": nearest_weekly,
        }
