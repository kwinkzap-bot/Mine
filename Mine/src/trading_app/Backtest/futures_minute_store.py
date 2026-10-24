"""
Continuous 1-minute series of the NIFTY monthly FUTURE, for backtests that must read
the same candles Quantman does ("Fields: Future" on every indicator of its run).

Why not the index: checked against Quantman's own trade list of the ST EMA RSI CCI WR
CPR BB run, the signal conditions hold on the candle before the entry for 53 of 56
entries when the indicators are read off the future, and 33 of 56 when read off the
spot index. The two differ by the basis, which drifts day to day and steps at every
roll, and these thresholds (RSI < 30, CCI < -200, close under S1) are all marginal.

The series is the front contract through its expiry day, then the next one, joined raw
(no back-adjustment). A contract's slice is (previous expiry, this expiry]. Breeze is
the only provider that serves an expired future, by (root, expiry), so:

  * a SETTLED month is fetched once and kept forever — data/futures_minutes/NIFTY_YYYY-MM.pkl;
  * the running month is refreshed at most every REFRESH_TTL_SEC;
  * a month Breeze returns nothing for is left out and reported by `coverage()`.

Monthly NIFTY futures expire on the last Thursday until Aug 2025 and the last Tuesday
from Sep 2025, moved back a day at a time when that is a holiday — each month probes
candidates until Breeze answers, so no holiday calendar is needed.
"""
from __future__ import annotations

import calendar
import logging
import os
import pickle
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'futures_minutes')
REFRESH_TTL_SEC = 900
_TUESDAY_FROM = (2025, 9)                 # NIFTY monthly expiry moved Thursday -> Tuesday
_lock = threading.Lock()


def _last_weekday(y: int, m: int, weekday: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _candidates(y: int, m: int) -> List[date]:
    weekday = 1 if (y, m) >= _TUESDAY_FROM else 3
    last = _last_weekday(y, m, weekday)
    return [last - timedelta(days=k) for k in range(0, 4)]


def _path(root: str, y: int, m: int) -> str:
    return os.path.join(DATA_DIR, f'{root}_{y:04d}-{m:02d}.pkl')


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'rb') as fh:
            return pickle.load(fh)
    except Exception:                     # noqa: BLE001 — missing/corrupt: refetch
        return None


def _save(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        pickle.dump(obj, fh)
    os.replace(tmp, path)


def _to_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    return df.set_index('date')[['open', 'high', 'low', 'close']].sort_index()


def _month_slice(adapter, root: str, y: int, m: int, prev_expiry: date,
                 today: date) -> Tuple[Optional[date], Optional[pd.DataFrame]]:
    """(expiry, bars in (prev_expiry, expiry]) of month (y, m), probing the expiry candidates."""
    for cand in _candidates(y, m):
        if cand <= prev_expiry:
            continue
        end = min(cand, today)
        try:
            rows = adapter.historical_future(root, cand, (prev_expiry + timedelta(days=1)).isoformat(),
                                             end.isoformat(), '1minute')
        except Exception as e:            # noqa: BLE001 — one dead probe is not the run
            logger.warning('[FutMinutes] %s %s probe failed: %s', root, cand, e)
            rows = []
        if rows:
            df = _to_frame(rows)
            df = df[(df.index.date > prev_expiry) & (df.index.date <= cand)]
            if len(df):
                return cand, df
    return None, None


def continuous_minutes(root: str, start: date, end: date, adapter=None,
                       progress=None) -> pd.DataFrame:
    """The roll-joined future's 1-minute bars covering [start, end]."""
    root = root.upper()
    if adapter is None:
        from trading_app.service.provider_logic import get_icici_adapter
        adapter = get_icici_adapter()
        if adapter is None or not hasattr(adapter, 'historical_future'):
            raise RuntimeError('The futures candles come from ICICI Breeze (the only provider that '
                               'serves an expired contract). Log in at /auth/login/icici.')
    today = date.today()
    frames: List[pd.DataFrame] = []
    # the contract running on `start` expired in or after its month; begin the month before
    y, m = start.year, start.month
    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    prev_expiry = date(y, m, 1) - timedelta(days=1)
    with _lock:
        for _ in range(400):
            path = _path(root, y, m)
            cached = _load(path)
            fresh = cached is not None and (cached.get('settled') or
                                            time.time() - cached.get('at', 0) < REFRESH_TTL_SEC)
            if fresh and cached.get('prev') == prev_expiry.isoformat():
                expiry = date.fromisoformat(cached['expiry']) if cached.get('expiry') else None
                df = cached.get('df')
            else:
                expiry, df = _month_slice(adapter, root, y, m, prev_expiry, today)
                _save(path, {'expiry': expiry.isoformat() if expiry else None, 'df': df,
                             'prev': prev_expiry.isoformat(),
                             'settled': bool(expiry and expiry < today), 'at': time.time()})
            if progress:
                progress(y, m)
            if expiry is None or df is None:
                # a month Breeze has nothing for: leave the hole and step over it by the nominal date
                prev_expiry = _candidates(y, m)[0]
            else:
                prev_expiry = expiry
                frames.append(df)
            if prev_expiry >= end:
                break
            y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    if not frames:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close'])
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep='last')].sort_index()
    return out[(out.index.date >= start) & (out.index.date <= end)]


def coverage(root: str = 'NIFTY') -> List[Dict[str, Any]]:
    """What is on disk: one row per month — expiry, bars, first/last day."""
    rows = []
    if not os.path.isdir(DATA_DIR):
        return rows
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.startswith(root + '_') and fn.endswith('.pkl'):
            c = _load(os.path.join(DATA_DIR, fn)) or {}
            df = c.get('df')
            rows.append({'month': fn[len(root) + 1:-4], 'expiry': c.get('expiry'),
                         'bars': 0 if df is None else len(df),
                         'first': None if df is None or not len(df) else str(df.index.min().date()),
                         'last': None if df is None or not len(df) else str(df.index.max().date())})
    return rows
