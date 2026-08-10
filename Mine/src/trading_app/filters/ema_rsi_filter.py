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

# EMA Narrow scanner: all EMAs of the selected set must sit inside a tight
# % band of price on EVERY timeframe of the selected group
NARROW_EMA_SETS = {
    "20_50_100_200": [20, 50, 100, 200],
    "20_50_100":     [20, 50, 100],
}
NARROW_DEFAULT_EMA_SET = "20_50_100"
NARROW_DEFAULT_THRESHOLD_PCT = 3.0

NARROW_GROUPS = {
    "mwd": ["monthly", "weekly", "daily"],
    "mw":  ["monthly", "weekly"],
    "wd":  ["weekly", "daily"],
    "d":   ["daily"],
    "w":   ["weekly"],
    "m":   ["monthly"],
}
# Daily-bar history needed for a stable longest-EMA on each resampled timeframe,
# keyed by the largest period of the selected EMA set.
# daily[100] is deliberately trimmed to ~350 days (vs. the 900+/1400+ used
# elsewhere) so it fits inside Fyers' single 364-day chunk cap — every equity
# stock hits this tier on the default combo, so keeping it to 1 API chunk
# instead of 2 roughly halves the dominant cost of a full-universe scan.
# Trade-off: ~240 trading days of warm-up (2.4x the period) converges the
# EMA to within ~5% of its asymptotic value, vs <1% with the deeper 900-day
# window — an accepted trade for scan speed on this specific tier only.
NARROW_FETCH_DAYS = {
    "daily":   {100: 350,  200: 900},
    "weekly":  {100: 1400, 200: 2500},
    "monthly": {100: 3700, 200: 7000},
}
NARROW_RESAMPLE = {"daily": None, "weekly": "W", "monthly": "M"}

# EMA Touch All scanner: the LAST daily candle's own range must contain every
# one of EMA 20/50/100/200 (low <= EMA <= high) — a single bar sweeping all
# four averages. Strict wick containment, no % tolerance, same "touch"
# definition as RSI_CROSSOVER's sibling above.
TOUCH_ALL_PERIODS = [20, 50, 100, 200]
# Daily history depth: reuse the tier the Narrow scanner already uses for a
# stable daily EMA(200), so the pre-warmed candle store covers this scan too.
TOUCH_ALL_FETCH_DAYS = NARROW_FETCH_DAYS["daily"][200]

# Fixed futures watchlist this scanner runs on, with each symbol's default
# trade options. Deliberately an explicit table rather than the NFO dump, so the
# universe and its defaults are exactly what the user configured and don't drift
# as the exchange adds/drops F&O names.
#   direction  — which signals are actionable for this symbol:
#                "BUY" (long only), "SELL" (short only), "BOTH".
#   target_pct — default profit target, percent of the entry (candle close).
# A DOWN candle reads as a SELL signal and an UP candle as a BUY, so `direction`
# is what decides whether a match is tradeable — see _touch_all_signal().
TOUCH_ALL_CONFIG = {
    "NIFTY":       ("BUY", 7),
    "BANKNIFTY":   ("BOTH", 8),
    "SENSEX":      ("BOTH", 2.5),
    "SBIN":        ("BUY", 2.5),
    "RELIANCE":    ("SELL", 15),
    "HDFCBANK":    ("BUY", 15),
    "360ONE":      ("BUY", 2),
    "ABB":         ("BUY", 15),
    "ABCAPITAL":   ("BUY", 4),
    "ADANIENSOL":  ("BUY", 15),
    "ADANIENT":    ("SELL", 7),
    "ADANIGREEN":  ("BOTH", 3),
    "ADANIPORTS":  ("BOTH", 2),
    "ADANIPOWER":  ("BUY", 12),
    "ALKEM":       ("BUY", 15),
    "AMBER":       ("BUY", 15),
    "AMBUJACEM":   ("BOTH", 6),
    "ANGELONE":    ("BUY", 3),
    "APLAPOLLO":   ("BUY", 3.5),
    "APOLLOHOSP":  ("BUY", 15),
    "ASTRAL":      ("BOTH", 12),
    "AUBANK":      ("SELL", 5),
    "AXISBANK":    ("BUY", 8),
    "BAJAJ-AUTO":  ("BOTH", 12),
    "BAJAJFINSV":  ("BOTH", 15),
    "BAJAJHLDNG":  ("BUY", 12),
    "BAJFINANCE":  ("SELL", 7),
    "BANDHANBNK":  ("SELL", 8),
    "BANKBARODA":  ("BOTH", 3.5),
    "BDL":         ("BUY", 8),
    "BEL":         ("BOTH", 10),
    "BHARTIARTL":  ("BUY", 10),
    "BIOCON":      ("BOTH", 15),
    "BLUESTARCO":  ("SELL", 3),
    "BOSCHLTD":    ("BUY", 12),
    "BPCL":        ("BUY", 15),
    "BRITANNIA":   ("BOTH", 15),
    "CAMS":        ("BOTH", 4),
    "CIPLA":       ("BOTH", 12),
    "COALINDIA":   ("SELL", 2.5),
    "COCHINSHIP":  ("BOTH", 15),
    "COFORGE":     ("BUY", 10),
    "COLPAL":      ("BUY", 5),
    "CROMPTON":    ("SELL", 12),
    "DALBHARAT":   ("SELL", 8),
    "DELHIVERY":   ("SELL", 2.5),
    "DMART":       ("BUY", 15),
    "DRREDDY":     ("BOTH", 2.5),
    "EICHERMOT":   ("SELL", 8),
    "ETERNAL":     ("BOTH", 4),
    "FEDERALBNK":  ("BUY", 2.5),
    "GLENMARK":    ("SELL", 2.5),
    "GMRAIRPORT":  ("BUY", 12),
    "GODFRYPHLP":  ("BUY", 15),
    "GODREJPROP":  ("BUY", 4),
    "GRASIM":      ("BOTH", 15),
    "HAL":         ("BOTH", 15),
    "HAVELLS":     ("SELL", 3.5),
    "HCLTECH":     ("BUY", 15),
    "HDFCAMC":     ("BOTH", 4),
    "HDFCLIFE":    ("BOTH", 3),
    "HINDALCO":    ("BUY", 3),
    "HINDUNILVR":  ("BOTH", 12),
    "HINDZINC":    ("SELL", 2),
    "ICICIGI":     ("BUY", 10),
    "ICICIPRULI":  ("SELL", 3),
    "IDEA":        ("SELL", 15),
    "IDFCFIRSTB":  ("BOTH", 7),
    "IEX":         ("BOTH", 8),
    "INDHOTEL":    ("BOTH", 7),
    "INDIANB":     ("SELL", 5),
    "INDIGO":      ("BOTH", 15),
    "INDUSINDBK":  ("BOTH", 15),
    "INFY":        ("SELL", 15),
    "IOC":         ("BOTH", 12),
    "IRFC":        ("SELL", 15),
    "ITC":         ("BUY", 15),
    "JUBLFOOD":    ("BUY", 8),
    "KALYANKJIL":  ("BOTH", 12),
    "KEI":         ("BOTH", 15),
    "KFINTECH":    ("BOTH", 15),
    "LAURUSLABS":  ("BUY", 12),
    "LT":          ("SELL", 12),
    "LTF":         ("BOTH", 15),
    "LTM":         ("BUY", 15),
    "M&M":         ("BOTH", 15),
    "MANAPPURAM":  ("BUY", 10),
    "MANKIND":     ("BOTH", 3.5),
    "MARICO":      ("BUY", 6),
    "MAZDOCK":     ("BOTH", 1.5),
    "MFSL":        ("BOTH", 4),
    "MOTHERSON":   ("BUY", 15),
    "MOTILALOFS":  ("BOTH", 15),
    "MUTHOOTFIN":  ("SELL", 2),
    "NAUKRI":      ("BOTH", 12),
    "NESTLEIND":   ("BUY", 3),
    "NHPC":        ("SELL", 8),
    "NMDC":        ("BUY", 7),
    "NYKAA":       ("BUY", 15),
    "OBEROIRLTY":  ("BUY", 3),
    "OFSS":        ("BUY", 4),
    "OIL":         ("BOTH", 8),
    "ONGC":        ("BUY", 15),
    "PAGEIND":     ("BUY", 3.5),
    "PAYTM":       ("BOTH", 6),
    "PFC":         ("BUY", 15),
    "PIIND":       ("BUY", 10),
    "PNB":         ("SELL", 8),
    "PNBHOUSING":  ("BOTH", 8),
    "POLYCAB":     ("BOTH", 2.5),
    "POWERGRID":   ("SELL", 6),
    "PREMIERENE":  ("SELL", 3),
    "PRESTIGE":    ("BOTH", 12),
    "RADICO":      ("BUY", 15),
    "RBLBANK":     ("SELL", 15),
    "RECLTD":      ("BOTH", 10),
    "RVNL":        ("BOTH", 8),
    "SBICARD":     ("SELL", 4),
    "SBILIFE":     ("BUY", 15),
    "SHREECEM":    ("BUY", 12),
    "SHRIRAMFIN":  ("BOTH", 7),
    "SOLARINDS":   ("BUY", 12),
    "SONACOMS":    ("BUY", 15),
    "SRF":         ("BUY", 6),
    "SUNPHARMA":   ("BUY", 3.5),
    "SUPREMEIND":  ("SELL", 15),
    "SUZLON":      ("BUY", 15),
    "TATAELXSI":   ("SELL", 15),
    "TATAPOWER":   ("BUY", 5),
    "TATASTEEL":   ("SELL", 2),
    "TCS":         ("SELL", 15),
    "TECHM":       ("BUY", 15),
    "TIINDIA":     ("SELL", 15),
    "TITAN":       ("BUY", 7),
    "TMPV":        ("SELL", 4),
    "TORNTPHARM":  ("BUY", 8),
    "TRENT":       ("BUY", 10),
    "TVSMOTOR":    ("BUY", 10),
    "ULTRACEMCO":  ("BOTH", 8),
    "UNIONBANK":   ("SELL", 15),
    "UPL":         ("BOTH", 5),
    "VOLTAS":      ("BUY", 8),
    "WAAREEENER":  ("BOTH", 12),
    "WIPRO":       ("BOTH", 8),
    "ZYDUSLIFE":   ("BOTH", 3),
}

TOUCH_ALL_SYMBOLS = list(TOUCH_ALL_CONFIG)
TOUCH_ALL_DIRECTIONS = ("BUY", "SELL", "BOTH")

# Indices don't trade as "-EQ" and aren't in the equity instrument list, so
# they need the provider's own index identifier for both quote and history.
# Kite is matched by tradingsymbol in that exchange's dump; Fyers by its
# -INDEX symbol. SENSEX is the reason `exchange` exists — it lives on BSE, not
# NSE, so it is absent from the NSE instrument list the service loads at init.
TOUCH_ALL_INDEX_MAP = {
    "NIFTY":     {"fyers": "NSE:NIFTY50-INDEX",   "kite": "NIFTY 50",   "exchange": "NSE"},
    "BANKNIFTY": {"fyers": "NSE:NIFTYBANK-INDEX", "kite": "NIFTY BANK", "exchange": "NSE"},
    "SENSEX":    {"fyers": "BSE:SENSEX-INDEX",    "kite": "SENSEX",     "exchange": "BSE"},
}


# Candlestick pattern scanner: runs over the SAME fixed futures watchlist as
# the EMA Touch All scanner (TOUCH_ALL_SYMBOLS), so the universe is exactly the
# F&O names the user configured — no equity-wide scan here. The pattern is
# checked on the latest candle of the selected timeframe against the one before
# it. Daily bars are the only thing fetched; weekly/monthly are resampled from
# them, same as the EMA Narrow scanner does.
CANDLE_PATTERN_LABELS = {
    "bullish_strong": "Strong Bullish",
    "bearish_strong": "Strong Bearish",
}
CANDLE_PATTERN_SIGNAL = {
    "bullish_strong": "BUY",
    "bearish_strong": "SELL",
}
CANDLE_TIMEFRAMES = {
    "daily":   {"resample": None, "fetch_days": 120},
    "weekly":  {"resample": "W",  "fetch_days": 400},
    "monthly": {"resample": "M",  "fetch_days": 1100},
}
CANDLE_DEFAULT_TIMEFRAME = "daily"

# A candle counts as "strong" when its body covers at least this much of its
# own high-low range — i.e. a decisive move with small wicks. 60% is the usual
# marubozu-ish cut-off; raise it for cleaner (and fewer) matches.
CANDLE_STRONG_BODY_PCT = 60.0


def _body_strength_pct(o: float, h: float, l: float, c: float) -> Optional[float]:
    """How much of the candle's high-low range its body covers, in percent.

    None when the range is zero (a flat bar), which can't be judged strong."""
    rng = h - l
    if rng <= 0:
        return None
    return abs(c - o) / rng * 100.0


def _detect_strong_pair(po: float, ph: float, pl: float, pc: float,
                        o: float, h: float, l: float, c: float) -> Optional[str]:
    """Two consecutive strong candles that flip direction, or None.

    Bullish: a red (down) previous candle followed by a green (up) latest one;
    bearish is the mirror image. BOTH candles must be strong — body covering at
    least CANDLE_STRONG_BODY_PCT of their own high-low range. There is no
    engulfing requirement: the latest candle does not have to swallow the
    previous body, the pair just has to be two decisive moves the opposite way.
    A doji has a ~zero body, so it fails the strength test on its own and can
    never be half of a match."""
    prev_strength = _body_strength_pct(po, ph, pl, pc)
    strength      = _body_strength_pct(o, h, l, c)
    if prev_strength is None or strength is None:
        return None
    if prev_strength < CANDLE_STRONG_BODY_PCT or strength < CANDLE_STRONG_BODY_PCT:
        return None
    if pc < po and c > o:
        return "bullish_strong"
    if pc > po and c < o:
        return "bearish_strong"
    return None


def _touch_all_signal(symbol: str, status: str, close: float) -> Dict:
    """Apply a symbol's configured defaults to a matched candle.

    The candle's own direction is the signal — an UP candle (close >= open) is a
    BUY, a DOWN candle a SELL. `allowed` says whether that signal is one the
    symbol is configured to take, and the target price is the configured
    percentage away from the close in the signal's direction."""
    direction, target_pct = TOUCH_ALL_CONFIG.get(symbol, ("BOTH", 0.0))
    signal  = "BUY" if status == "UP" else "SELL"
    allowed = direction == "BOTH" or direction == signal
    target_price = (close * (1 + target_pct / 100) if signal == "BUY"
                    else close * (1 - target_pct / 100))
    return {
        "direction":    direction,
        "target_pct":   target_pct,
        "signal":       signal,
        "allowed":      allowed,
        "target_price": round(target_price, 2),
    }


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
        self._other_instruments: Dict[str, List[Dict]] = {}  # exchange -> dump (BSE, ...)
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
                    end_date: Optional[datetime] = None,
                    token: Optional[Union[int, str]] = None) -> Optional[pd.DataFrame]:
        # `token` overrides the default equity lookup — needed for instruments
        # _get_token can't resolve on its own (indices are not "-EQ").
        # Daily candles go through the per-stock disk store: full download once,
        # then only incremental tail fetches — much faster repeat scans.
        if interval == "day":
            try:
                from trading_app.filters.candle_store import get_daily_history
                token = token or self._get_token(symbol)
                if token:
                    df = get_daily_history(self.kite, token, symbol, days, end_date)
                    if df is not None and not df.empty:
                        return df
                    return None
            except Exception as e:
                logger.warning(f"Candle store failed for {symbol}, falling back to direct fetch: {e}")

        end   = end_date if end_date else datetime.now()
        start = end - timedelta(days=days)
        cache_key = f"{symbol}_{start.date()}_{end.date()}_{interval}_{days}"

        with _GLOBAL_EMA_CACHE_LOCK:
            cached = _GLOBAL_EMA_HIST_CACHE.get(cache_key)
            if cached is not None:
                return cached

        token = token or self._get_token(symbol)
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

        matched = bool(ema_touched and rsi_crossed_above)

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
            "trigger":       "EMA+RSI" if matched else "—",
        }

    def _analyse_ema_narrow(self, symbol: str, current_price: float, df: Optional[pd.DataFrame],
                            resample: Optional[str] = None,
                            threshold_pct: float = NARROW_DEFAULT_THRESHOLD_PCT,
                            periods: Optional[List[int]] = None) -> Optional[Dict]:
        """Check whether all EMAs of the set are bunched within threshold_pct of price."""
        if periods is None:
            periods = NARROW_EMA_SETS[NARROW_DEFAULT_EMA_SET]
        if df is None or len(df) < 20:
            return None

        if resample == 'W':
            df = _weekly_resample(df)
        elif resample == 'M':
            df = _monthly_resample(df)

        closes = df["close"].tolist()
        if len(closes) < max(periods):
            return None

        emas: Dict[int, float] = {}
        for p in periods:
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
            "ema20":         round(emas[20], 2) if 20 in emas else None,
            "ema50":         round(emas[50], 2) if 50 in emas else None,
            "ema100":        round(emas[100], 2) if 100 in emas else None,
            "ema200":        round(emas[200], 2) if 200 in emas else None,
            "spread_pct":    spread_pct,
            "price_inside":  price_inside,
            "matched":       matched,
        }

    def run_ema_narrow_filter(self, root_date: Optional[datetime] = None, group: str = "wd",
                              threshold_pct: float = NARROW_DEFAULT_THRESHOLD_PCT,
                              ema_set: str = NARROW_DEFAULT_EMA_SET,
                              progress_cb=None) -> Dict:
        """Scan ALL NSE equity stocks where every EMA of the selected set
        ('20_50_100_200' or '20_50_100') is compressed on EVERY timeframe of the
        selected group ('mwd' = Month+Week+Day, 'mw', 'wd', or single d/w/m).

        Timeframes are checked cheapest-first (daily → weekly → monthly); the long
        history needed by higher timeframes is only fetched for stocks that are
        still narrow on the lower ones, which keeps the scan fast.

        Stocks are processed in batches of 100; after each batch
        progress_cb(done, total, partial) is invoked, where partial is a
        snapshot {'results': [...], 'nearest': [...]} of everything found so
        far — so callers can show results incrementally while the scan runs."""
        group_tfs = NARROW_GROUPS.get(group, NARROW_GROUPS["wd"])
        gate_order = list(reversed(group_tfs))  # lowest timeframe first (cheapest fetch)
        periods = NARROW_EMA_SETS.get(ema_set, NARROW_EMA_SETS[NARROW_DEFAULT_EMA_SET])
        max_period = max(periods)  # sizes how much history each timeframe needs

        stocks = self._get_equity_stocks()
        if not stocks:
            logger.warning("EMA Narrow filter: no equity stocks found")
            return {"results": [], "nearest": []}

        logger.info(f"EMA Narrow filter: scanning {len(stocks)} equity stocks for "
                    f"{'+'.join(group_tfs)} EMAs {periods} (threshold {threshold_pct}%)...")

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
                df = self._fetch_hist(symbol, days=NARROW_FETCH_DAYS[tf][max_period],
                                      interval="day", end_date=root_date)
                r = self._analyse_ema_narrow(symbol, price, df, NARROW_RESAMPLE[tf],
                                             threshold_pct, periods)
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
    # EMA Touch All (daily candle sweeps EMA 20/50/100/200)
    # ------------------------------------------------------------------

    def _analyse_ema_touch_all(self, symbol: str, current_price: float,
                               df: Optional[pd.DataFrame]) -> Optional[Dict]:
        """Does the latest daily candle's range contain ALL of EMA 20/50/100/200?

        Returns the row for every stock with enough history (matched True/False,
        plus how many of the four it did touch), or None when EMA(200) can't be
        computed. `status` is the candle's own direction: UP when it closed at
        or above its open, DOWN otherwise. The symbol's configured defaults
        (direction, target_pct) and what they imply for this candle (signal,
        allowed, target_price) are merged in — see _touch_all_signal()."""
        if df is None or len(df) < 20:
            return None

        closes = df["close"].tolist()
        if len(closes) < max(TOUCH_ALL_PERIODS):
            return None

        emas: Dict[int, float] = {}
        for p in TOUCH_ALL_PERIODS:
            val = _calc_ema(closes, p)[-1]
            if val != val:  # nan check
                return None
            emas[p] = float(val)

        last = df.iloc[-1]
        try:
            o = float(last["open"])
            h = float(last["high"])
            l = float(last["low"])
            c = float(last["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if o <= 0 or l <= 0:
            return None

        touched = {p: bool(l <= e <= h) for p, e in emas.items()}
        touched_count = sum(touched.values())
        status = "UP" if c >= o else "DOWN"

        return {
            **_touch_all_signal(symbol, status, c),
            "symbol":        symbol,
            "current_price": float(current_price),
            "open":          round(o, 2),
            "high":          round(h, 2),
            "low":           round(l, 2),
            "close":         round(c, 2),
            "ema20":         round(emas[20], 2),
            "ema50":         round(emas[50], 2),
            "ema100":        round(emas[100], 2),
            "ema200":        round(emas[200], 2),
            "touched_count": touched_count,
            "touched":       {str(p): v for p, v in touched.items()},
            "range_pct":     round((h - l) / l * 100, 2),
            "change_pct":    round((c - o) / o * 100, 2),
            "status":        status,
            "candle_date":   str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
            "matched":       bool(touched_count == len(TOUCH_ALL_PERIODS)),
        }

    def _touch_all_quote_symbol(self, symbol: str) -> str:
        """Provider quote key for a watchlist entry — index-aware."""
        idx = TOUCH_ALL_INDEX_MAP.get(symbol)
        if not idx:
            return f"NSE:{symbol}"
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            return idx["fyers"]
        return f"{idx['exchange']}:{idx['kite']}"

    def _exchange_instruments(self, exchange: str) -> List[Dict]:
        """Instrument dump for an exchange, loaded once. NSE is already fetched
        at init; anything else (BSE, for SENSEX) is fetched lazily and cached."""
        if exchange == "NSE":
            return self._instruments
        cached = self._other_instruments.get(exchange)
        if cached is None:
            try:
                cached = self.kite.instruments(exchange) or []
            except Exception as e:
                logger.error(f"{exchange} instrument load failed: {e}")
                cached = []
            self._other_instruments[exchange] = cached
        return cached

    def _touch_all_token(self, symbol: str) -> Optional[Union[int, str]]:
        """History token for a watchlist entry. Equities go through the normal
        equity lookup; indices resolve via their own provider identifier, since
        they are not '-EQ' instruments and may sit on another exchange."""
        idx = TOUCH_ALL_INDEX_MAP.get(symbol)
        if not idx:
            return self._get_token(symbol)
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            return idx["fyers"]
        for inst in self._exchange_instruments(idx["exchange"]):
            if inst.get("tradingsymbol") == idx["kite"] or inst.get("name") == idx["kite"]:
                return inst.get("instrument_token")
        return None

    def run_ema_touch_all_filter(self, root_date: Optional[datetime] = None,
                                 progress_cb=None) -> Dict:
        """Scan the fixed futures watchlist (TOUCH_ALL_SYMBOLS — indices plus F&O
        stocks) for instruments whose latest daily candle touches every one of
        EMA 20 / 50 / 100 / 200.

        Same shape as run_ema_narrow_filter: symbols are processed in batches and
        progress_cb(done, total, partial) is invoked after each one with a
        snapshot of the matches found so far, so the UI fills in live. `nearest`
        holds the closest misses (3 of 4 touched) for when nothing matches
        outright, and `skipped` lists watchlist entries the provider couldn't
        resolve or that lack enough history for EMA(200)."""
        stocks = list(TOUCH_ALL_SYMBOLS)
        logger.info(f"EMA Touch All filter: scanning {len(stocks)} watchlist symbols "
                    f"for a daily candle touching EMA {TOUCH_ALL_PERIODS}...")

        # Quote keys differ per instrument (indices aren't NSE:<symbol>), so keep
        # a reverse map to get back to the plain watchlist name.
        quote_keys = {self._touch_all_quote_symbol(s): s for s in stocks}
        price_map: Dict[str, float] = {}
        keys = list(quote_keys)
        quote_batch = 500
        for i in range(0, len(keys), quote_batch):
            batch = keys[i: i + quote_batch]
            try:
                quotes = self.kite.quote(batch)
                for sym, data in quotes.items():
                    plain = quote_keys.get(sym) or sym.replace("NSE:", "")
                    price_map[plain] = float(data.get("last_price", 0))
            except Exception as e:
                logger.warning(f"Batch quote failed: {e}")

        results: List[Dict] = []
        all_results: List[Dict] = []
        skipped: List[str] = []

        def process_stock(symbol: str):
            price = price_map.get(symbol, 0.0)
            token = self._touch_all_token(symbol)
            if not token:
                return symbol, None
            df = self._fetch_hist(symbol, days=TOUCH_ALL_FETCH_DAYS,
                                  interval="day", end_date=root_date, token=token)
            return symbol, self._analyse_ema_touch_all(symbol, price, df)

        workers_count = self.MAX_WORKERS
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            workers_count = 5

        done_count = 0
        total = len(stocks)
        batch_size = 50

        def _snapshot() -> Dict:
            """Sorted copy of everything found so far (widest sweep first)."""
            return {
                "results": sorted(results, key=lambda r: -r.get("range_pct", 0)),
                "nearest": sorted(
                    [r for r in all_results
                     if not r.get("matched") and r.get("touched_count", 0) >= 3],
                    key=lambda r: -r.get("range_pct", 0)
                )[:10],
                "scanned": len(all_results),
                "total":   total,
                "skipped": sorted(skipped),
            }

        def _report():
            if progress_cb:
                try:
                    progress_cb(done_count, total, _snapshot())
                except Exception:
                    pass

        _report()

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
                        else:
                            # Unresolvable symbol or too little history for
                            # EMA(200) — surfaced so a typo in the watchlist
                            # doesn't silently look like "no match".
                            skipped.append(sym)
                    except Exception as e:
                        futures_sym = futures[future]
                        skipped.append(futures_sym)
                        logger.warning(f"EMA Touch All processing error ({futures_sym}): "
                                       f"{e.__class__.__name__}: {e}")
            done_count = min(i + batch_size, total)
            _report()

        final = _snapshot()
        logger.info(f"EMA Touch All filter → {len(final['results'])} matches "
                    f"from {final['scanned']}/{total} scanned"
                    + (f", skipped: {', '.join(final['skipped'])}" if final['skipped'] else ""))
        return final

    # ------------------------------------------------------------------
    # Candlestick patterns (strong candle pairs) on the futures watchlist
    # ------------------------------------------------------------------

    def _analyse_candlestick(self, symbol: str, current_price: float,
                             df: Optional[pd.DataFrame],
                             resample: Optional[str] = None) -> Optional[Dict]:
        """Latest candle plus the one before it — both strong and flipping direction, or not.

        Returns a row for every symbol with at least two candles (`pattern` is
        None and `matched` False when nothing fires), or None when there isn't
        enough data to judge, so the caller can report it as skipped."""
        if df is None or len(df) < 2:
            return None

        if resample == 'W':
            df = _weekly_resample(df)
        elif resample == 'M':
            df = _monthly_resample(df)
        if len(df) < 2:
            return None

        prev = df.iloc[-2]
        last = df.iloc[-1]
        try:
            po, pc = float(prev["open"]), float(prev["close"])
            ph, pl = float(prev["high"]), float(prev["low"])
            o, h   = float(last["open"]), float(last["high"])
            l, c   = float(last["low"]),  float(last["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if o <= 0 or po <= 0 or l <= 0:
            return None

        pattern    = _detect_strong_pair(po, ph, pl, pc, o, h, l, c)
        prev_body  = abs(pc - po)
        body       = abs(c - o)
        strength      = _body_strength_pct(o, h, l, c)
        prev_strength = _body_strength_pct(po, ph, pl, pc)

        def _vol(row) -> Optional[float]:
            try:
                return float(row["volume"])
            except (KeyError, TypeError, ValueError):
                return None

        volume, prev_volume = _vol(last), _vol(prev)

        return {
            "symbol":         symbol,
            "pattern":        pattern,
            "pattern_label":  CANDLE_PATTERN_LABELS.get(pattern) if pattern else None,
            "signal":         CANDLE_PATTERN_SIGNAL.get(pattern) if pattern else None,
            "current_price":  float(current_price),
            "open":           round(o, 2),
            "high":           round(h, 2),
            "low":            round(l, 2),
            "close":          round(c, 2),
            "prev_open":      round(po, 2),
            "prev_close":     round(pc, 2),
            "prev_high":      round(ph, 2),
            "prev_low":       round(pl, 2),
            "change_pct":     round((c - o) / o * 100, 2),
            "body_pct":       round(body / o * 100, 2),
            # Body-to-range of each candle, and the weaker of the two. Sorting
            # on the weaker one puts the pairs where BOTH candles are decisive
            # at the top, instead of one monster candle carrying a mediocre one.
            "body_strength":      round(strength, 2) if strength is not None else None,
            "prev_body_strength": round(prev_strength, 2) if prev_strength is not None else None,
            "strength_pct":       (round(min(strength, prev_strength), 2)
                                   if strength is not None and prev_strength is not None else None),
            # Size of this body relative to the previous one — no longer part of
            # the rule, kept as a read on whether the move is accelerating.
            "body_ratio":     round(body / prev_body, 2) if prev_body > 0 else None,
            "range_pct":      round((h - l) / l * 100, 2),
            "volume":         volume,
            "volume_ratio":   (round(volume / prev_volume, 2)
                               if volume and prev_volume else None),
            "candle_date":    str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
            "matched":        bool(pattern),
        }

    def run_candlestick_filter(self, root_date: Optional[datetime] = None,
                               timeframe: str = CANDLE_DEFAULT_TIMEFRAME,
                               progress_cb=None) -> Dict:
        """Scan the futures watchlist (TOUCH_ALL_SYMBOLS) for bullish/bearish
        strong candle pairs on the selected timeframe (daily/weekly/monthly).

        Same background-job shape as run_ema_touch_all_filter: symbols are
        processed in batches and progress_cb(done, total, partial) is invoked
        after each batch with a snapshot of the matches so far. `skipped` lists
        watchlist entries the provider couldn't resolve or that lack two
        candles on the timeframe."""
        tf_cfg = CANDLE_TIMEFRAMES.get(timeframe) or CANDLE_TIMEFRAMES[CANDLE_DEFAULT_TIMEFRAME]
        stocks = list(TOUCH_ALL_SYMBOLS)
        logger.info(f"Candlestick filter: scanning {len(stocks)} futures watchlist "
                    f"symbols for strong candle pairs on {timeframe}...")

        # Quote keys differ per instrument (indices aren't NSE:<symbol>), so keep
        # a reverse map to get back to the plain watchlist name.
        quote_keys = {self._touch_all_quote_symbol(s): s for s in stocks}
        price_map: Dict[str, float] = {}
        keys = list(quote_keys)
        quote_batch = 500
        for i in range(0, len(keys), quote_batch):
            batch = keys[i: i + quote_batch]
            try:
                quotes = self.kite.quote(batch)
                for sym, data in quotes.items():
                    plain = quote_keys.get(sym) or sym.replace("NSE:", "")
                    price_map[plain] = float(data.get("last_price", 0))
            except Exception as e:
                logger.warning(f"Batch quote failed: {e}")

        results: List[Dict] = []
        all_results: List[Dict] = []
        skipped: List[str] = []

        def process_stock(symbol: str):
            price = price_map.get(symbol, 0.0)
            token = self._touch_all_token(symbol)
            if not token:
                return symbol, None
            df = self._fetch_hist(symbol, days=tf_cfg["fetch_days"],
                                  interval="day", end_date=root_date, token=token)
            return symbol, self._analyse_candlestick(symbol, price, df, tf_cfg["resample"])

        workers_count = self.MAX_WORKERS
        if self.kite.__class__.__name__ == 'FyersDataServiceAdapter':
            workers_count = 5

        done_count = 0
        total = len(stocks)
        batch_size = 50

        def _snapshot() -> Dict:
            """Sorted copy of everything found so far (strongest pair first)."""
            return {
                "results": sorted(results, key=lambda r: -(r.get("strength_pct") or 0)),
                "scanned": len(all_results),
                "total":   total,
                "skipped": sorted(skipped),
            }

        def _report():
            if progress_cb:
                try:
                    progress_cb(done_count, total, _snapshot())
                except Exception:
                    pass

        _report()

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
                        else:
                            # Unresolvable symbol or fewer than two candles on
                            # this timeframe — surfaced so a typo in the
                            # watchlist doesn't silently read as "no match".
                            skipped.append(sym)
                    except Exception as e:
                        futures_sym = futures[future]
                        skipped.append(futures_sym)
                        logger.warning(f"Candlestick processing error ({futures_sym}): "
                                       f"{e.__class__.__name__}: {e}")
            done_count = min(i + batch_size, total)
            _report()

        final = _snapshot()
        logger.info(f"Candlestick filter ({timeframe}) → {len(final['results'])} matches "
                    f"from {final['scanned']}/{total} scanned"
                    + (f", skipped: {', '.join(final['skipped'])}" if final['skipped'] else ""))
        return final

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
