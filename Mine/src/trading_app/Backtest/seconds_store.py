"""On-disk 30-second index bars, shared by the Candle Breakout backtest and
its Find Best Params board — so the two always run on the SAME bars.

Why it exists: 30-second history has two sources with different reach. Fyers
serves a rolling ~28 days for free; anything older is Breeze 1-second data at
25 requests a trading day, walked newest-first under a per-call budget and kept
only in process memory. So the backtest (Fyers + whatever Breeze it could
afford) and the board (Fyers only, to keep a sweep cheap) saw different
sessions: on 2026-11-11 the backtest had 36,523 bars from 2026-08-31 and the
board 14,044 — 44 trades against 19 for the same settings.

Now every 30-second fetch lands here (settled sessions only, kept forever,
like minute_candle_store), and both runs read their range back out of the
file. The backtest is still the one that spends Breeze to reach further back;
the board only adds Fyers' free window. Whatever either has gathered, both see.

Also here: `drop_forming`, which removes today's still-forming session while
the market is open. A trade open on it would be booked as an EOD exit at
whatever bar the call happened to see, and two calls a minute apart disagree.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import pickle
import re
import threading
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'second_candles')
IST = ZoneInfo('Asia/Kolkata')
SESSION_END = dt_time(15, 30)

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _thread_lock(path: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


@contextlib.contextmanager
def _lock(path: str):
    """Across threads AND processes: scripts/backfill_30s.py writes the same
    file the app does, and a read-merge-write from each at once loses one side."""
    with _thread_lock(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(f'{path}.lock', 'w') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _path(symbol: str, interval: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9_\-]', '_', f'{symbol}_{interval}')
    return os.path.join(DATA_DIR, f'{safe}.pkl')


def last_settled_day(now: Optional[datetime] = None) -> date:
    """The newest session that has closed: today from 15:30 IST, else yesterday
    (weekends and holidays simply have no bars)."""
    now = now or datetime.now(IST)
    if now.tzinfo is not None:
        now = now.astimezone(IST)
    return now.date() if now.time() >= SESSION_END else now.date() - timedelta(days=1)


def drop_forming(df: Optional[pd.DataFrame], now: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    """`df` (naive IST index) without any session after the last settled one."""
    if df is None or df.empty:
        return df
    cutoff = pd.Timestamp(last_settled_day(now) + timedelta(days=1))
    return df[df.index < cutoff]


def to_frame(candles: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    """Kite-shaped candle dicts → OHLCV frame on a naive IST datetime index."""
    df = pd.DataFrame(list(candles or []))
    if df.empty or 'date' not in df:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    idx = pd.to_datetime(df['date'])
    if getattr(idx.dt, 'tz', None) is not None:
        idx = idx.dt.tz_convert(IST).dt.tz_localize(None)
    df = df.drop(columns=['date']).set_index(pd.DatetimeIndex(idx))
    df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ('open', 'high', 'low', 'close', 'volume') if c in df.columns]
    df = df[keep].apply(pd.to_numeric, errors='coerce').dropna(subset=['close'])
    return df[~df.index.duplicated(keep='last')].sort_index()


def _load(path: str) -> Optional[pd.DataFrame]:
    try:
        with open(path, 'rb') as f:
            df = pickle.load(f)
        return df if isinstance(df, pd.DataFrame) else None
    except FileNotFoundError:
        return None
    except Exception as e:                       # noqa: BLE001 — a bad file is rebuilt
        logger.warning('[SecondsStore] unreadable %s: %s', path, e)
        return None


def _save(path: str, df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'wb') as f:
        pickle.dump(df, f)
    os.replace(tmp, path)


def merge_and_read(symbol: str, candles: Iterable[Dict[str, Any]], start: str, end: str,
                   interval: str = '30second',
                   now: Optional[datetime] = None) -> pd.DataFrame:
    """Add the SETTLED sessions of `candles` to the store, then return every
    stored bar in [start, end] — settled sessions only."""
    path = _path(symbol, interval)
    new = drop_forming(to_frame(candles), now)
    with _lock(path):
        stored = _load(path)
        if new is not None and not new.empty:
            # Only whole sessions are kept: a day the fetch returned is taken
            # as-is (it replaces a stored copy of that day), never spliced.
            days = set(new.index.normalize())
            if stored is not None and not stored.empty:
                stored = stored[~stored.index.normalize().isin(days)]
                merged = pd.concat([stored, new]).sort_index()
            else:
                merged = new
            if stored is None or len(merged) != len(stored):
                _save(path, merged)
            stored = merged
        if stored is None or stored.empty:
            return to_frame([])
        lo = pd.Timestamp(start)
        hi = pd.Timestamp(end) + pd.Timedelta(days=1)
        return drop_forming(stored[(stored.index >= lo) & (stored.index < hi)], now)


def span(symbol: str, interval: str = '30second') -> Tuple[Optional[str], Optional[str], int]:
    """(first day, last day, sessions) held — part of the board's cache key, so a
    store that has grown since a board was built rebuilds it."""
    df = _load(_path(symbol, interval))
    if df is None or df.empty:
        return None, None, 0
    days = df.index.normalize().unique()
    return str(days[0].date()), str(days[-1].date()), len(days)


def records(df: pd.DataFrame) -> list:
    """Back to the candle dicts the engines take."""
    if df is None or df.empty:
        return []
    out = df.reset_index().rename(columns={'index': 'date'})
    if 'date' not in out.columns:
        out = out.rename(columns={out.columns[0]: 'date'})
    return out.to_dict('records')
