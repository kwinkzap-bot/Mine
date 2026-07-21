"""
Per-symbol 1-minute candle store for backtests that scan the full F&O
universe (e.g. the 30-Min Opening Fakeout scan).

Keeps one pickle file per (provider, symbol) under
src/trading_app/data/minute_candles/. Closed trading days never change once
fetched, so they're cached on disk forever; only a request whose range
touches today's still-open session re-contacts the provider, and only for
the missing tail — so re-running the same (or an overlapping) backtest is a
local read instead of a multi-minute re-download.

Mirrors the design of filters/candle_store.py (daily bars), adapted for
1-minute resolution and multi-provider (Kite/Fyers) keying.
"""
import logging
import os
import pickle
import re
import threading
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "minute_candles")
_TAIL_REFRESH_TTL_SEC = 900   # only re-hit the provider for "today" once per 15 min

_mem: Dict[str, Dict] = {}
_mem_lock = threading.Lock()
_key_locks: Dict[str, threading.Lock] = {}


def _lock_for(key: str) -> threading.Lock:
    with _mem_lock:
        if key not in _key_locks:
            _key_locks[key] = threading.Lock()
        return _key_locks[key]


def _cache_key(provider_tag: str, symbol: str) -> str:
    return f"{provider_tag}_{symbol}"


def _file_path(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-&]", "_", key)
    return os.path.join(DATA_DIR, f"{safe}.pkl")


def _load_disk(key: str) -> Optional[Dict]:
    path = _file_path(key)
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
        logger.warning(f"Minute candle store read failed ({key}): {e}")
        return None


def _save_disk(key: str, entry: Dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = _file_path(key)
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
        logger.warning(f"Minute candle store write failed ({key}): {e}")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if 'date' in df.columns:
        df = df.set_index('date')
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.columns = [c.lower() for c in df.columns]
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    return df[~df.index.duplicated(keep="last")].sort_index()


def _fetch(provider, token, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        try:
            candles = provider.historical_data(
                instrument_token=token, from_date=start, to_date=end,
                interval='minute', use_cache=False,
            )
        except TypeError:
            # Some providers (raw KiteConnect) don't accept use_cache.
            candles = provider.historical_data(
                instrument_token=token, from_date=start, to_date=end,
                interval='minute',
            )
    except Exception as e:
        logger.warning(f"Minute candle fetch failed ({token}, {start}..{end}): {e}")
        return None
    if not candles:
        return None
    return _normalize(pd.DataFrame(candles))


def _merge(base: Optional[pd.DataFrame], new: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if new is None or new.empty:
        return base
    if base is None or base.empty:
        return new
    combined = pd.concat([base, new])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def get_minute_history(provider, token, symbol: str, start_date: str, end_date: str,
                        provider_tag: str = 'default') -> Optional[pd.DataFrame]:
    """1-minute OHLC for `symbol` covering [start_date, end_date] ('YYYY-MM-DD').

    Serves from the local per-symbol file, contacting the provider only for
    the range not yet stored — a gap at the head (deeper history requested),
    a gap at the tail (newer days requested), or today's still-forming
    session (refreshed at most once per _TAIL_REFRESH_TTL_SEC).
    """
    start = pd.Timestamp(start_date)
    end   = pd.Timestamp(end_date)
    today = pd.Timestamp(datetime.now().date())
    now   = datetime.now()
    key   = _cache_key(provider_tag, symbol)

    with _lock_for(key):
        entry = _mem.get(key)
        if entry is None:
            entry = _load_disk(key)
            if entry is not None:
                with _mem_lock:
                    _mem[key] = entry

        changed = False

        if entry is None:
            fetched = _fetch(provider, token, start_date, end_date)
            if fetched is None:
                return None
            entry = {"df": fetched, "earliest": start, "updated_at": now}
            changed = True
        else:
            # Head gap: deeper history requested than ever fetched.
            if start < entry["earliest"]:
                gap_end = min(entry["earliest"] - pd.Timedelta(days=1), end)
                fetched = _fetch(provider, token, start_date, gap_end.strftime('%Y-%m-%d'))
                if fetched is not None:
                    entry["df"] = _merge(entry["df"], fetched)
                entry["earliest"] = start
                changed = True

            # Tail gap: newer days requested than stored. Closed days are
            # fetched once and kept forever; only re-request from the last
            # stored bar onward, and only if that touches today (needs a
            # live refresh, gated by TTL) or hasn't been fetched at all.
            #
            # last_bar_date and end are both midnight-truncated, so once
            # ANY bar from today is cached, last_bar_date == end == today
            # and `last_bar_date < end` is permanently False for the rest
            # of the day — the TTL check below would never even run,
            # freezing "today" at whatever moment it was first cached. The
            # `end >= today` half of this condition is what actually lets
            # a same-day request keep re-checking staleness all day.
            last_bar_date = entry["df"].index[-1].normalize() if not entry["df"].empty else None
            if last_bar_date is None or last_bar_date < end or end >= today:
                need_tail = True
                if last_bar_date is not None and last_bar_date >= today:
                    need_tail = (now - entry["updated_at"]).total_seconds() > _TAIL_REFRESH_TTL_SEC
                if need_tail:
                    tail_start = (last_bar_date - pd.Timedelta(days=1)) if last_bar_date is not None else start
                    fetched = _fetch(provider, token, tail_start.strftime('%Y-%m-%d'), end_date)
                    entry["updated_at"] = now
                    if fetched is not None:
                        entry["df"] = _merge(entry["df"], fetched)
                    changed = True

        if entry is None or entry["df"].empty:
            return None

        with _mem_lock:
            _mem[key] = entry
        if changed:
            _save_disk(key, entry)

        df = entry["df"]
        end_excl = end + pd.Timedelta(days=1)
        return df[(df.index >= start) & (df.index < end_excl)]
