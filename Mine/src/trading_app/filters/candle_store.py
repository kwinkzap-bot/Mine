"""
Per-stock daily-candle store.

Keeps one file per stock under src/trading_app/data/stocks/ (e.g. RELIANCE.pkl)
holding its full daily OHLCV history. The first request downloads from the data
provider; afterwards only the missing tail (new candles since the last stored
bar) is fetched — so scans that used to re-download years of history per stock
become mostly local reads.

Each file stores {"df": DataFrame, "earliest": iso-date, "updated_at": iso-ts}:
  - earliest   : the oldest date ever requested — if a request needs history no
                 older than this, local data is known to be complete (even for
                 recently listed stocks that simply have no older candles).
  - updated_at : when the provider was last contacted, used to throttle
                 intraday tail refreshes.
"""
import logging
import os
import pickle
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "stocks")

REFRESH_TTL_SEC = 6 * 3600   # min gap between provider hits for the same stock
_MAX_DAYS_PER_REQ = 1960     # provider limit per historical_data call

_mem: Dict[str, Dict] = {}   # symbol -> {"df", "earliest", "updated_at"}
_mem_lock = threading.Lock()
_symbol_locks: Dict[str, threading.Lock] = {}


def _sym_lock(symbol: str) -> threading.Lock:
    with _mem_lock:
        if symbol not in _symbol_locks:
            _symbol_locks[symbol] = threading.Lock()
        return _symbol_locks[symbol]


def _file_path(symbol: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-&]", "_", symbol)
    return os.path.join(DATA_DIR, f"{safe}.pkl")


def _load_disk(symbol: str) -> Optional[Dict]:
    path = _file_path(symbol)
    try:
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            entry = pickle.load(f)
        if not isinstance(entry.get("df"), pd.DataFrame) or entry["df"].empty:
            return None
        entry["earliest"]   = pd.Timestamp(entry["earliest"])
        entry["updated_at"] = datetime.fromisoformat(entry["updated_at"])
        return entry
    except Exception as e:
        logger.warning(f"Candle store read failed ({symbol}): {e}")
        return None


def _save_disk(symbol: str, entry: Dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = _file_path(symbol)
        payload = {
            "df":         entry["df"],
            "earliest":   str(pd.Timestamp(entry["earliest"]).date()),
            "updated_at": entry["updated_at"].isoformat(),
        }
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(payload, f)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"Candle store write failed ({symbol}): {e}")


def drop_weekend_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Saturday/Sunday bars — they are never a regular trading session.

    NSE does run the odd weekend session (disaster-recovery / mock live drills,
    Muhurat, a Budget Saturday), and the provider hands those back as ordinary
    daily candles. The drill ones are the dangerous kind: barely any volume and
    a range several percent wide because a handful of test orders print at silly
    prices. 2026-07-11 is the example that surfaced this — a Saturday bar whose
    high/low engulfed all four EMAs on ~390 stocks at once, so every daily
    strategy reading this store saw a phantom confluence signal and armed
    breakout levels off prices that never traded in a real session.

    Applied on the way in (new fetches) AND on the way out (files already
    holding such a bar), so no consumer has to know about it.
    """
    if df is None or df.empty:
        return df
    return df[pd.DatetimeIndex(df.index).dayofweek < 5]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    df = drop_weekend_bars(df)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _fetch_range(kite, token, symbol: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    """Chunked daily-candle fetch from the provider (handles the ~2000-day API limit)."""
    all_data = []
    curr_end = end
    curr_start = max(start, curr_end - timedelta(days=_MAX_DAYS_PER_REQ))
    while curr_start < curr_end:
        try:
            chunk = kite.historical_data(token, curr_start.strftime("%Y-%m-%d"),
                                         curr_end.strftime("%Y-%m-%d"), "day")
            if chunk:
                all_data.extend(chunk)
        except Exception as e:
            logger.debug(f"Candle chunk fetch error for {symbol}: {e}")
        curr_end = curr_start
        curr_start = max(start, curr_end - timedelta(days=_MAX_DAYS_PER_REQ))
        if not all_data:
            break  # first (most recent) chunk failed — give up
    if not all_data:
        return None
    df = pd.DataFrame(all_data).set_index("date")
    return _normalize(df)


def _merge(base: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return new
    combined = pd.concat([base, new])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def get_daily_history(kite, token, symbol: str, days: int,
                      end_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    """Daily OHLCV for `symbol` covering `days` back from `end_date` (default now).
    Serves from the local per-stock file, contacting the provider only for
    history not yet stored (deeper past or new candles)."""
    now   = datetime.now()
    end   = end_date or now
    start = end - timedelta(days=days)

    with _sym_lock(symbol):
        entry = _mem.get(symbol)
        if entry is None:
            entry = _load_disk(symbol)
            if entry is not None:
                with _mem_lock:
                    _mem[symbol] = entry

        changed = False

        # 1. Need deeper history than ever fetched -> extend the head.
        # Fetch ONLY the missing older slice [start, entry.earliest] and merge
        # it with what's already stored — never re-download the recent range
        # that's already on file (that used to double- or triple-fetch the
        # same months whenever a stock passed multiple timeframe gates).
        if entry is None:
            fetched = _fetch_range(kite, token, symbol, start, now)
            if fetched is None:
                return None
            entry = {"df": fetched, "earliest": pd.Timestamp(start), "updated_at": now}
            changed = True
        elif pd.Timestamp(start) < entry["earliest"] - pd.Timedelta(days=3):
            gap_end = min(entry["earliest"] + pd.Timedelta(days=3), pd.Timestamp(now))
            fetched = _fetch_range(kite, token, symbol, pd.Timestamp(start), gap_end)
            if fetched is not None:
                entry["df"] = _merge(entry["df"], fetched)
            entry["earliest"]   = pd.Timestamp(start)
            entry["updated_at"] = now
            changed = True

        # 2. Tail refresh: only the candles since the last stored bar.
        # This scanner works off daily/weekly/monthly closes, not intraday
        # ticks — there's no need to chase today's still-forming candle every
        # few minutes. Only check when a full trading day is actually missing
        # (last stored bar is older than today), and even then at most once
        # per REFRESH_TTL_SEC so repeated same-day scans stay purely local.
        # The nightly prewarm job is what keeps the store genuinely fresh.
        elif not entry["df"].empty:
            last_bar = entry["df"].index[-1]
            age_sec  = (now - entry["updated_at"]).total_seconds()
            need_tail = (
                last_bar.date() < end.date()
                and age_sec > REFRESH_TTL_SEC
            )
            if need_tail:
                fetched = _fetch_range(kite, token, symbol,
                                       last_bar.to_pydatetime() - timedelta(days=5), now)
                entry["updated_at"] = now
                if fetched is not None:
                    entry["df"] = _merge(entry["df"], fetched)
                changed = True

        if entry is None or entry["df"].empty:
            return None

        with _mem_lock:
            _mem[symbol] = entry
        if changed:
            _save_disk(symbol, entry)

        # Filtered on the way out too: files written before drop_weekend_bars
        # existed still hold those bars, and rewriting 2000+ pickles to purge
        # them would be a far bigger hammer than never handing them out.
        df = drop_weekend_bars(entry["df"])
        # Include every bar ON the end date (whatever intraday time it's stamped
        # with), but nothing after it — no look-ahead on historical scans.
        end_excl = pd.Timestamp(end.date()) + pd.Timedelta(days=1)
        return df[(df.index >= pd.Timestamp(start)) & (df.index < end_excl)]
