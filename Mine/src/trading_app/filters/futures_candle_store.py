"""Per-contract daily-candle store for monthly FUTURES.

The spot twin of `filters/candle_store`, and it exists for a sharper reason
than speed. A backtest that prices its fills on the future has to read the
contract that was actually trading back then — August's future for an August
fill — and by September that contract is gone from every instrument master we
have. Only ICICI (Breeze) will serve it, because Breeze addresses a contract
by its FIELDS (stock_code, expiry, product_type) rather than by a token, so an
expired series is still reachable (see `IciciDataServiceAdapter.historical_future`).

Breeze is also the tightest budget in the app — 100 requests/minute, 5000 a
day, paced at 1.5/s and shared with the live algos. An All-Stocks EMA
Confluence scan spans ~150 symbols over nine years; even fetching only the
contracts its trades actually touch, a cold run is thousands of requests. So:

  * A SETTLED contract can never change, and is cached forever on disk. The
    second run of the same backtest costs nothing.
  * A contract that has NOT expired is refreshed at most every REFRESH_TTL_SEC.
  * A contract Breeze serves nothing for is remembered as such for
    MISSING_RETRY_SEC, so a symbol with no futures history does not re-spend
    the budget on every run.

Breeze's own coverage of older contracts is incomplete, and no amount of
re-requesting fixes it: verified 2026-09-05, the Mar-2019 NIFTY future returns
nothing after 2019-02-20 whether asked for in a wide window or a narrow one,
though it traded to its 2019-03-28 expiry. A leg landing in such a gap is
priced at a zero basis (i.e. at the spot price) and counted in
`unpriced_legs` — see Backtest/ema_futures_pricing. Recent contracts come back
complete.
  * Callers pass a `budget` (see `Fetcher`) that caps how many live requests
    one run may make. Past it the store still serves everything already on
    disk and simply reports how many it had to skip — a slow scan degrades to
    a partly-unpriced one, never to a hung request.

One pickle per root holds every contract for that root:
    data/futures/{ROOT}.pkl -> {"contracts": {expiry_iso: DataFrame|None},
                                "fetched_at": {expiry_iso: iso-ts}}
"""
import logging
import os
import pickle
import re
import threading
from datetime import date, datetime, timedelta
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "futures")

REFRESH_TTL_SEC   = 6 * 3600        # live contract: min gap between broker hits
MISSING_RETRY_SEC = 7 * 24 * 3600   # contract that served nothing: retry weekly

# A monthly contract lists ~3 months before it expires; ask for a little more
# so the first bar is always inside the window.
_CONTRACT_HISTORY_DAYS = 130

_mem: Dict[str, Dict] = {}
_mem_lock = threading.Lock()
_root_locks: Dict[str, threading.Lock] = {}


def _root_lock(root: str) -> threading.Lock:
    with _mem_lock:
        if root not in _root_locks:
            _root_locks[root] = threading.Lock()
        return _root_locks[root]


def _file_path(root: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-&]", "_", root)
    return os.path.join(DATA_DIR, f"{safe}.pkl")


def _load(root: str) -> Dict:
    with _mem_lock:
        entry = _mem.get(root)
    if entry is not None:
        return entry
    entry = {"contracts": {}, "fetched_at": {}}
    path = _file_path(root)
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                loaded = pickle.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("contracts"), dict):
                entry = loaded
                entry.setdefault("fetched_at", {})
    except Exception as e:
        logger.warning(f"Futures store read failed ({root}): {e}")
    with _mem_lock:
        _mem[root] = entry
    return entry


def _save(root: str, entry: Dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = _file_path(root)
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(entry, f)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"Futures store write failed ({root}): {e}")


def _stale(entry: Dict, key: str, expiry: date, today: date) -> bool:
    """Whether this contract is worth asking the broker about again."""
    stamp = entry["fetched_at"].get(key)
    if stamp is None:
        return True
    try:
        age = (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return True
    if entry["contracts"].get(key) is None:
        return age > MISSING_RETRY_SEC
    # A settled contract's bars are final — never re-fetch them.
    return expiry >= today and age > REFRESH_TTL_SEC


def _fetch(adapter, root: str, expiry: date, exchange_code: str) -> Optional[pd.DataFrame]:
    end = min(expiry, date.today())
    start = expiry - timedelta(days=_CONTRACT_HISTORY_DAYS)
    try:
        candles = adapter.historical_future(
            root, expiry, start.isoformat(), end.isoformat(), 'day',
            exchange_code=exchange_code)
    except Exception as e:
        logger.warning(f"[futures store] {root} {expiry} fetch failed: {e}")
        return None
    if not candles:
        reason = ''
        try:
            reason = adapter.last_history_error() or ''
        except Exception:
            pass
        logger.info(f"[futures store] {root} {expiry}: no candles{' — ' + reason if reason else ''}")
        return None
    df = pd.DataFrame(candles)
    if 'date' not in df.columns or 'close' not in df.columns:
        return None
    df['date'] = pd.to_datetime(df['date'])
    if getattr(df['date'].dt, 'tz', None) is not None:
        df['date'] = df['date'].dt.tz_localize(None)
    return _one_row_per_session(df.set_index('date').sort_index())


def _one_row_per_session(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a contract's history to exactly one bar per trading day.

    Breeze answers a 'day' request for SOME futures contracts with INTRA-DAY
    rows — verified 2026-09-05: one month of the Jan-2020 NIFTY future came
    back as 176 rows stamped 14:00/15:00 rather than ~20 daily ones, while the
    Nov-2019 contract over the same call returned proper daily bars. Left
    alone, that inflates the store and makes a by-date lookup depend on which
    of the day's rows it happens to land on.

    Where a day has a real midnight-stamped daily bar it wins (it is the
    authoritative OHLC for the session); otherwise the day's LAST print is
    kept, which is the closest thing to its close.
    """
    day = pd.DatetimeIndex(df.index).normalize()
    df = df.assign(_day=day, _is_daily=(pd.DatetimeIndex(df.index) == day))
    # Stable sort: within a day the original (time-ordered) order survives, so
    # keep='last' picks the daily bar if there is one and the last print if not.
    df = df.sort_values(['_day', '_is_daily'], kind='stable')
    df = df[~df['_day'].duplicated(keep='last')]
    df.index = pd.DatetimeIndex(df['_day'])
    return df.drop(columns=['_day', '_is_daily'])


class Budget:
    """How many LIVE broker requests one backtest run may spend on futures
    history it does not already hold.

    Shared across every symbol of an All-Stocks scan (and across its worker
    threads), because the thing being rationed is one app-wide Breeze budget,
    not a per-symbol one. Past the limit the store still serves everything on
    disk; `skipped` is what the run reports so a partly-priced result says so
    out loud instead of looking complete.
    """

    def __init__(self, limit: Optional[int] = None):
        self.limit = limit
        self.spent = 0
        self.skipped = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.limit is not None and self.spent >= self.limit:
                self.skipped += 1
                return False
            self.spent += 1
            return True


class Fetcher:
    """A `fetch_close(expiry) -> pd.Series|None` bound to one root."""

    def __init__(self, adapter, root: str, exchange_code: str = 'NFO',
                 budget: Optional[Budget] = None):
        self.adapter = adapter
        self.root = (root or '').upper()
        self.exchange_code = exchange_code
        self.budget = budget if budget is not None else Budget(None)

    def __call__(self, expiry: date) -> Optional[pd.Series]:
        df = get_contract_daily(self.adapter, self.root, expiry,
                                self.exchange_code, self.budget)
        return None if df is None or df.empty else df['close']


def get_contract_daily(adapter, root: str, expiry: date,
                       exchange_code: str = 'NFO',
                       budget: Optional['Budget'] = None) -> Optional[pd.DataFrame]:
    """Daily OHLC for one monthly futures contract, from disk where possible."""
    root = (root or '').upper()
    key = expiry.isoformat()
    entry = _load(root)
    today = date.today()

    with _root_lock(root):
        cached = entry["contracts"].get(key)
        if not _stale(entry, key, expiry, today):
            return cached
        if adapter is None:
            return cached
        if budget is not None and not budget.take():
            return cached
        df = _fetch(adapter, root, expiry, exchange_code)
        # A failed refresh of a contract we already hold keeps the old bars —
        # only a genuinely new miss is recorded as an empty contract.
        entry["contracts"][key] = df if df is not None else cached
        entry["fetched_at"][key] = datetime.now().isoformat()
        _save(root, entry)
        return entry["contracts"][key]
