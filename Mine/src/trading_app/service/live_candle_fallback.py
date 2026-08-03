"""
Today's intraday candles, rebuilt locally when Fyers' history API has none.

Why this exists
---------------
On 2026-08-03 the Fyers `/history` endpoint started answering `s: "no_data"`
for *every* intraday resolution on the current trading day, for every symbol
(index, equity, futures, options), while the same call returned 1500 clean
1-minute candles for the three preceding sessions. Their reply even carried
`nextTime: 1785491940` — 2026-07-31 15:29 IST, the last bar they hold — so
this is Fyers telling us their intraday store simply ends at Friday's close.

Meanwhile `/quotes` and the daily (`D`) resolution stayed live and ticking.
So the exchange data is reachable; only the intraday *candle* store is empty.

Nothing on our side can fix Fyers, but we do have two live sources of today's
prices already flowing through this app:

  1. `oi_history` in oi_data.db — the OI persistence task writes a row per
     symbol every ~30-60s with the index spot (`current_price`) and an
     `active_strikes` JSON carrying `ce_ltp`/`pe_ltp` for ~101 strikes. This
     reaches back to market open, so it can rebuild the part of the session
     that already elapsed before the app was restarted.
  2. The `/quotes` API — still live, already rate-limited and cached by the
     adapter, and polled every few seconds by whatever chart is open.

This module turns those samples into OHLC bars. Sampling is coarser than a
real feed (~60s from the OI task, ~4s while a chart is watching), so a
1-minute bar here may be a single tick with open == high == low == close.
That is a *reconstruction*, not exchange data, which is why:

  - bars carry `'synthetic': True`, and
  - `FyersDataServiceAdapter.historical_data()` only merges them when the
    caller passes `allow_synthetic=True`.

Only the chart/display endpoints opt in. The live algos (RTP, Second Candle,
TMF, EMA Confluence) keep the default and never see these bars — they must
not place real orders off prices we interpolated.
"""
import json
import logging
import os
import pickle
import sqlite3
import threading
from datetime import date, datetime, time as dt_time, timedelta, timezone
from time import monotonic, sleep
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_DB_PATH = os.path.join(_PROJECT_ROOT, 'oi_data.db')

# Where the day's fine-grained ticks are parked between restarts.
#
# Without this, every restart drops the sampled ticks and rebuilds the whole
# elapsed session from the ~60s oi_history snapshots alone — which draws one
# sample per 1-minute bar, i.e. flat open==high==low==close dashes with no
# body. Since this app gets restarted several times a session, that would undo
# the sampler's work every time. Snapshotting to disk keeps the morning's real
# candles intact across a lunchtime restart.
_TICKS_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'live_ticks')
_SNAPSHOT_EVERY_SEC = 30.0
_last_snapshot_at = 0.0

# Kite-style interval name -> bar width in seconds. Mirrors the resolution map
# in fyers_data_service.historical_data(); anything absent here is treated as
# non-intraday and never gets a synthetic fill.
INTERVAL_SECONDS: Dict[str, int] = {
    '30second': 30,
    'minute':   60,
    '2minute':  120,
    '3minute':  180,
    '5minute':  300,
    '10minute': 600,
    '15minute': 900,
    '30minute': 1800,
    '60minute': 3600,
    '2hour':    7200,
    '4hour':    14400,
}

# Fyers index symbol -> the `symbol` column the OI persistence task writes.
_INDEX_TO_OI_SYMBOL: Dict[str, str] = {
    'NSE:NIFTY50-INDEX':    'NIFTY',
    'NSE:NIFTYBANK-INDEX':  'BANKNIFTY',
    'NSE:FINNIFTY-INDEX':   'FINNIFTY',
    'NSE:MIDCPNIFTY-INDEX': 'MIDCPNIFTY',
}

MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

# symbol -> sorted list of (epoch_seconds, price, cumulative_volume).
_ticks: Dict[str, List[Tuple[int, float, float]]] = {}
# symbol -> highest oi_history.id already folded into _ticks, so each refill
# only reads the rows written since the last one.
_oi_cursor: Dict[str, int] = {}
# symbol -> monotonic time it was last asked for by a chart.
_hot: Dict[str, float] = {}
_provider_ref: Any = None
_sampler: Optional[threading.Thread] = None

# Sampling cadence.
#
# One snapshot per bar draws a candle with open == high == low == close — a
# flat dash with no body, which is what the ~60s oi_history backfill on its own
# produces. Real bodies need many samples inside each bar, so a background
# thread polls instead of relying on chart traffic.
#
# The adapter's quote() batches every symbol handed to it into ONE Fyers call
# and caches each result for 3s during market hours. So polling all hot legs
# together every 2s costs a single request per tick — no more load than the one
# quote the chart already made — while yielding a fresh price about every 3s:
# ~20 samples per 1-minute bar, ~100 per 5-minute bar.
SAMPLE_INTERVAL_SEC = 2.0
# A symbol stops being sampled this long after the last chart asked for it, so
# closing a chart or switching strike winds its polling down on its own.
HOT_TTL_SEC = 180.0
# Ceiling on symbols per batched request, so a burst of strike switching can't
# grow the quote call without bound.
MAX_HOT_SYMBOLS = 24
# Cached reverse index of the Fyers option master: symbol -> (root, strike, CE/PE).
_option_index: Optional[Dict[str, Tuple[str, float, str]]] = None
_option_index_at: Optional[datetime] = None

_lock = threading.RLock()


# The trading day the store currently holds. This app runs for days at a time
# under the LaunchAgent, so without an explicit rollover yesterday's ticks would
# still be sitting in _ticks tomorrow morning and candles() would emit them as
# today's bars.
_tick_day: Optional[date] = None


def _today() -> date:
    return datetime.now(IST).date()


def _roll_day_if_needed() -> date:
    global _tick_day
    today = _today()
    with _lock:
        if _tick_day != today:
            _tick_day = today
            _ticks.clear()
            _oi_cursor.clear()
            _hot.clear()
    return today


def _session_bounds(day: date) -> Tuple[int, int]:
    start = datetime.combine(day, MARKET_OPEN, IST).timestamp()
    end = datetime.combine(day, MARKET_CLOSE, IST).timestamp()
    return int(start), int(end)


def is_intraday(interval: str) -> bool:
    return interval in INTERVAL_SECONDS


# ──────────────────────────────────────────────────────────────────────────
# Tick ingestion
# ──────────────────────────────────────────────────────────────────────────

def record_tick(symbol: str, ts: int, price: float, volume: float = 0.0) -> None:
    """Add one price sample. Out-of-order and duplicate timestamps are fine —
    the store is kept sorted and deduped by timestamp (last write wins)."""
    if not symbol or price is None:
        return
    try:
        price = float(price)
    except (TypeError, ValueError):
        return
    if price <= 0:
        return

    open_ts, close_ts = _session_bounds(_roll_day_if_needed())
    # Ignore samples outside the session; a pre-open quote still carries the
    # previous close and would otherwise plant a fake 09:15 bar.
    if not (open_ts <= ts <= close_ts + 60):
        return

    with _lock:
        series = _ticks.setdefault(symbol, [])
        if series and series[-1][0] == ts:
            series[-1] = (ts, price, max(volume or 0.0, series[-1][2]))
            return
        if series and series[-1][0] > ts:
            series.append((ts, price, volume or 0.0))
            series.sort(key=lambda t: t[0])
            deduped: Dict[int, Tuple[int, float, float]] = {t[0]: t for t in series}
            _ticks[symbol] = [deduped[k] for k in sorted(deduped)]
            return
        series.append((ts, price, volume or 0.0))


def record_quote(symbol: str, quote: Dict[str, Any]) -> None:
    """Fold one adapter quote dict (Kite-shaped) into the tick store."""
    if not quote:
        return
    price = quote.get('last_price')
    ts = quote.get('timestamp')
    if isinstance(ts, datetime):
        # The quote's own fetch time, not now(). A cached quote replayed by a
        # faster poll then lands on the timestamp it was actually fetched at and
        # collapses onto the existing tick, instead of planting a duplicate
        # price at a later second and flattening the bar it falls into.
        epoch = int(ts.timestamp())
    else:
        epoch = int(datetime.now(IST).timestamp())
    record_tick(symbol, epoch, price, quote.get('volume') or 0.0)


# ──────────────────────────────────────────────────────────────────────────
# Background sampler
# ──────────────────────────────────────────────────────────────────────────

def _market_is_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _sample_once(provider, symbols: List[str]) -> None:
    """One batched quote call covering every hot symbol."""
    if not symbols:
        return
    try:
        quotes = provider.quote(symbols) or {}
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] batched quote failed: {e}")
        return
    for sym in symbols:
        q = quotes.get(sym)
        if q:
            record_quote(sym, q)


def _sampler_loop() -> None:
    while True:
        try:
            now = monotonic()
            with _lock:
                provider = _provider_ref
                hot = [s for s, seen in _hot.items() if now - seen <= HOT_TTL_SEC]
                for stale in [s for s in _hot if now - _hot[s] > HOT_TTL_SEC]:
                    _hot.pop(stale, None)
            if provider is None or not hot or not _market_is_open():
                sleep(SAMPLE_INTERVAL_SEC if hot else 5.0)
                continue
            _sample_once(provider, hot[:MAX_HOT_SYMBOLS])
            save_snapshot()
        except Exception as e:  # never let the thread die
            logger.debug(f"[LiveCandleFallback] sampler iteration failed: {e}")
        sleep(SAMPLE_INTERVAL_SEC)


def _ensure_sampler(provider) -> None:
    """Register `provider` and start the sampler thread once.

    Held under a single acquisition throughout (_lock is an RLock, so the
    load/prune helpers can re-enter it): the chart fetches every leg on a
    thread pool, so two legs arriving together would otherwise both see no
    live sampler and start one each.
    """
    global _provider_ref, _sampler
    with _lock:
        _provider_ref = provider
        if _sampler is not None and _sampler.is_alive():
            return
        # Pull back anything an earlier run of today already sampled, before
        # the first bars get built, so a restart does not flatten the morning.
        load_snapshot()
        prune_snapshots()
        _sampler = threading.Thread(target=_sampler_loop, name='live-candle-sampler',
                                    daemon=True)
        _sampler.start()
    logger.info("[LiveCandleFallback] started background quote sampler "
                f"({SAMPLE_INTERVAL_SEC}s batched)")


# ──────────────────────────────────────────────────────────────────────────
# Backfill from the OI persistence table
# ──────────────────────────────────────────────────────────────────────────

def _load_option_index(provider) -> Dict[str, Tuple[str, float, str]]:
    """symbol -> (root, strike, CE/PE), built from the Fyers NFO/BFO master.

    The master is already cached globally for an hour by
    FyersDataServiceAdapter.instruments(), so this is a dict rebuild rather
    than a download on all but the first call.
    """
    global _option_index, _option_index_at
    with _lock:
        fresh = (_option_index is not None and _option_index_at is not None
                 and (datetime.now() - _option_index_at).total_seconds() < 3600)
        if fresh:
            return _option_index

    index: Dict[str, Tuple[str, float, str]] = {}
    for exch in ('NFO', 'BFO'):
        try:
            for inst in provider.instruments(exch) or []:
                opt_type = (inst.get('instrument_type') or '').upper()
                if opt_type not in ('CE', 'PE'):
                    continue
                sym = str(inst.get('instrument_token') or '')
                if sym:
                    index[sym] = (str(inst.get('name') or '').upper(),
                                  float(inst.get('strike') or 0.0), opt_type)
        except Exception as e:
            logger.debug(f"[LiveCandleFallback] instruments({exch}) unavailable: {e}")

    with _lock:
        _option_index = index
        _option_index_at = datetime.now()
    return index


def _resolve_symbol(provider, symbol: str) -> Optional[Tuple[str, Optional[float], Optional[str]]]:
    """(oi_history symbol, strike, CE/PE). strike/type are None for an index."""
    if symbol in _INDEX_TO_OI_SYMBOL:
        return _INDEX_TO_OI_SYMBOL[symbol], None, None

    # Strike and type come from the symbol master only — never from parsing the
    # symbol string. "NSE:NIFTY2580324300CE" concatenates the expiry (25803)
    # and the strike (24300) into one digit run with no separator, so any regex
    # has to guess where the boundary falls. Guessing wrong silently plots a
    # different contract's prices under the requested strike's name, which is
    # worse than drawing nothing. If the master can't resolve it, we skip.
    entry = _load_option_index(provider).get(symbol)
    if entry:
        root, strike, opt_type = entry
        if root and strike:
            return root, strike, opt_type
    logger.debug(f"[LiveCandleFallback] {symbol} not in the option master — no synthetic fill")
    return None


def backfill_from_oi_history(provider, symbol: str) -> int:
    """Fold today's OI snapshots for `symbol` into the tick store.

    Incremental: each call reads only oi_history rows newer than the last one
    already folded in. Returns the number of samples added.
    """
    resolved = _resolve_symbol(provider, symbol)
    if not resolved:
        return 0
    oi_symbol, strike, opt_type = resolved

    if not os.path.exists(_DB_PATH):
        return 0

    day = _roll_day_if_needed().isoformat()
    with _lock:
        cursor = _oi_cursor.get(symbol, 0)

    try:
        conn = sqlite3.connect(f'file:{_DB_PATH}?mode=ro', uri=True, timeout=5)
        try:
            # Reading only today's rows for one symbol keeps this off the bulk
            # of a 1.4 GB table; date(timestamp) is cheap relative to the ~60
            # rows a session produces per symbol.
            rows = conn.execute(
                "SELECT id, timestamp, current_price, active_strikes "
                "FROM oi_history WHERE symbol = ? AND id > ? AND date(timestamp) = ? "
                "ORDER BY id",
                (oi_symbol, cursor, day),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] oi_history read failed for {symbol}: {e}")
        return 0

    added = 0
    max_id = cursor
    for row_id, ts_raw, spot, strikes_json in rows:
        max_id = max(max_id, row_id)
        try:
            stamp = datetime.fromisoformat(str(ts_raw))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        epoch = int(stamp.timestamp())

        if strike is None:
            if spot:
                record_tick(symbol, epoch, spot)
                added += 1
            continue

        if not strikes_json:
            continue
        try:
            entries = json.loads(strikes_json)
        except (ValueError, TypeError):
            continue
        key = 'ce_ltp' if opt_type == 'CE' else 'pe_ltp'
        for entry in entries:
            try:
                if abs(float(entry.get('strike') or 0) - strike) > 0.01:
                    continue
            except (TypeError, ValueError):
                continue
            ltp = entry.get(key)
            if ltp:
                record_tick(symbol, epoch, ltp)
                added += 1
            break

    if max_id > cursor:
        with _lock:
            _oi_cursor[symbol] = max_id
    return added


# ──────────────────────────────────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────────────────────────────────

def candles(symbol: str, interval: str, after: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """OHLC bars for today built from the recorded ticks.

    `after` drops bars at or before that timestamp, so a caller that already
    holds real candles up to some point only gets the missing tail.
    """
    width = INTERVAL_SECONDS.get(interval)
    if not width:
        return []

    _roll_day_if_needed()
    with _lock:
        series = list(_ticks.get(symbol) or [])
    if not series:
        return []

    buckets: Dict[int, List[Tuple[int, float, float]]] = {}
    for ts, price, vol in series:
        buckets.setdefault(ts - (ts % width), []).append((ts, price, vol))

    out: List[Dict[str, Any]] = []
    prev_cum: Optional[float] = None
    for bucket_ts in sorted(buckets):
        samples = sorted(buckets[bucket_ts], key=lambda t: t[0])
        prices = [s[1] for s in samples]
        cum = max((s[2] for s in samples), default=0.0) or 0.0
        # Quotes carry volume cumulative for the day; a bar's own volume is the
        # increase across it. Indices report 0 and stay 0.
        if prev_cum is None:
            bar_volume = 0.0
        else:
            bar_volume = max(0.0, cum - prev_cum)
        if cum:
            prev_cum = cum

        bar_time = datetime.fromtimestamp(bucket_ts, tz=IST)
        if after is not None and bar_time <= after:
            continue
        out.append({
            'date':      bar_time,
            'open':      prices[0],
            'high':      max(prices),
            'low':       min(prices),
            'close':     prices[-1],
            'volume':    int(bar_volume),
            'synthetic': True,
        })
    return out


def fill_today(provider, symbol: str, interval: str,
               after: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Refresh the tick store for `symbol` and return today's synthetic bars.

    Marks the symbol hot so the background sampler keeps building proper OHLC
    for it, and folds in whatever the OI persistence task has written since the
    last call — that backfill is what covers the part of the session that ran
    before this process started.
    """
    if not is_intraday(interval):
        return []
    try:
        backfill_from_oi_history(provider, symbol)
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] backfill failed for {symbol}: {e}")

    with _lock:
        first_sight = symbol not in _hot
        _hot[symbol] = monotonic()
    _ensure_sampler(provider)
    # Sample a newly-charted symbol immediately rather than making it wait for
    # the next sampler tick, so its first bar is never empty.
    if first_sight:
        _sample_once(provider, [symbol])

    return candles(symbol, interval, after=after)


def _snapshot_path(day: date) -> str:
    return os.path.join(_TICKS_DIR, f"ticks_{day.isoformat()}.pkl")


def save_snapshot(force: bool = False) -> None:
    """Park the day's ticks on disk. No-op unless _SNAPSHOT_EVERY_SEC elapsed."""
    global _last_snapshot_at
    now = monotonic()
    with _lock:
        if not force and now - _last_snapshot_at < _SNAPSHOT_EVERY_SEC:
            return
        _last_snapshot_at = now
        day = _tick_day
        payload = {sym: list(series) for sym, series in _ticks.items()}
        cursors = dict(_oi_cursor)
    if not day or not payload:
        return
    try:
        os.makedirs(_TICKS_DIR, exist_ok=True)
        path = _snapshot_path(day)
        tmp = f"{path}.tmp"
        with open(tmp, 'wb') as fh:
            pickle.dump({'ticks': payload, 'oi_cursor': cursors}, fh,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] snapshot write failed: {e}")


def load_snapshot() -> int:
    """Restore today's ticks from disk. Returns the number of symbols restored."""
    day = _roll_day_if_needed()
    path = _snapshot_path(day)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, 'rb') as fh:
            blob = pickle.load(fh)
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] snapshot read failed: {e}")
        return 0

    restored = blob.get('ticks') or {}
    if not restored:
        return 0
    with _lock:
        for sym, series in restored.items():
            existing = {t[0]: t for t in _ticks.get(sym, [])}
            for tick in series:
                existing.setdefault(tick[0], tick)
            _ticks[sym] = [existing[k] for k in sorted(existing)]
        # Resume the oi_history cursors too, so a restart does not re-read (and
        # re-append) snapshots already folded in before it.
        for sym, cur in (blob.get('oi_cursor') or {}).items():
            _oi_cursor[sym] = max(_oi_cursor.get(sym, 0), cur)
    logger.info(f"[LiveCandleFallback] restored ticks for {len(restored)} symbols from {path}")
    return len(restored)


def prune_snapshots(keep_days: int = 5) -> None:
    """Drop snapshot files older than `keep_days` so the directory stays small."""
    try:
        cutoff = _today() - timedelta(days=keep_days)
        for name in os.listdir(_TICKS_DIR):
            if not (name.startswith('ticks_') and name.endswith('.pkl')):
                continue
            try:
                stamp = date.fromisoformat(name[len('ticks_'):-len('.pkl')])
            except ValueError:
                continue
            if stamp < cutoff:
                os.remove(os.path.join(_TICKS_DIR, name))
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"[LiveCandleFallback] snapshot prune failed: {e}")


def reset(symbol: Optional[str] = None) -> None:
    """Drop stored ticks — all of them, or just one symbol's."""
    with _lock:
        if symbol is None:
            _ticks.clear()
            _oi_cursor.clear()
            _hot.clear()
        else:
            _ticks.pop(symbol, None)
            _oi_cursor.pop(symbol, None)
            _hot.pop(symbol, None)


def stats() -> Dict[str, int]:
    """Tick count per symbol, for the diagnostics endpoint."""
    with _lock:
        return {sym: len(series) for sym, series in _ticks.items()}
