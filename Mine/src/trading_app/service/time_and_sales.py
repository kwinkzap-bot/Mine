"""Time and Sales — a live trade tape for the dashboard.

Dhan's DEXT panel shows one row per trade: time, price, quantity, coloured by
which side crossed the spread. This module rebuilds that from our own brokers.

WHY A WEBSOCKET AND NOT THE QUOTE POLLER
----------------------------------------
The rest of this app is HTTP polling, and the obvious move would have been to
reuse live_candle_fallback's sampler. That cannot work here: Fyers' REST
/quotes carries no last-traded-quantity and no exchange last-traded-time at
all, so a polled tape could only ever show "the price when we happened to
look", with the quantity guessed from a volume delta. The fields a tape needs
exist only on the Fyers *data websocket* (full mode, `SymbolUpdate`).

The socket is also the cheap option. It costs one HTTP call to fetch the URL
and then nothing: no slots on the 8 req/s pacer that the four live algos and
the 1 Hz round-strike block already share. A polled tape would have cost a
standing 1 req/s per symbol.

WHAT THE FEED ACTUALLY GIVES US (measured 2026-09-09, NSE:NIFTY26SEPFUT)
-----------------------------------------------------------------------
  * ~1.4 pushes/sec — server-throttled snapshots, NOT every trade
  * last_traded_time advanced 15 times in 70s -> ~0.21 real prints/sec
  * the ltq at those advances summed to 41.5% of the volume actually traded

So this tape is a true but INCOMPLETE record: every row is a real exchange
print with a real exchange timestamp, and roughly three fifths of the traded
volume happens between the snapshots we are shown. That is why every row
carries `vol_delta` — the cumulative-volume move since the previous row. It
makes the part we cannot attribute visible instead of silently missing, and
`coverage()` reports the ratio so the UI can state it plainly rather than
implying parity with Dhan.

ISOLATION
---------
Nothing here can reach the live algos. This module imports provider_logic and
nothing else from the app: no algo package, no order service, no scheduler. It
never calls live_candle_fallback.record_tick/record_quote, so it cannot inject
anything into the tick store behind `allow_synthetic` bars. It places no
orders and has no code path that could. The only writes are its own module
globals and its own pickle under data/tape/.
"""

import logging
import os
import pickle
import threading
from collections import deque
from datetime import date, datetime, time as dt_time, timedelta, timezone
from time import monotonic, sleep
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

# Where the day's tape is parked between restarts. This app is restarted
# several times a session under the LaunchAgent; without a snapshot every
# restart would drop the morning's prints and force a fresh 25-request Breeze
# backfill to get them back.
_TAPE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'tape')
_SNAPSHOT_EVERY_SEC = 30.0
_last_snapshot_at = 0.0

# 09:15-15:30 at the measured ~0.2 prints/sec is ~4,500 rows; the cap is set
# well above that so a genuinely busy day (or a busier instrument) still fits
# a whole session without evicting the open.
MAX_ROWS_PER_SYMBOL = 25_000

# Don't quote a coverage percentage until this much volume has gone past. Two
# or three prints tell you nothing about the feed's capture rate, and a wildly
# wrong number in the UI is worse than no number.
_COVERAGE_MIN_VOLUME = 2_000

# A symbol stops being taped this long after the last request for it, so
# closing the tab winds the subscription down on its own.
HOT_TTL_SEC = 45.0

# Hard ceiling on subscriptions. A tape is a foreground panel — you watch one
# instrument — and each extra symbol is another stream to fold into the store.
# Two rather than one only so switching contract does not blank the tape while
# the old symbol drains out of _HOT.
MAX_TAPED_SYMBOLS = 2

# The supervisor wakes this often to expire hot symbols, snapshot, and check
# the socket is still delivering.
_SUPERVISOR_TICK_SEC = 5.0

# If the market is open, a hot symbol is subscribed and nothing has arrived for
# this long, the socket is treated as dead and rebuilt. The feed pushes ~1.4/s,
# so 90s of silence is far outside normal jitter.
_STALL_SECONDS = 90.0


# ──────────────────────────────────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────────────────────────────────

# symbol -> newest-last deque of tape rows.
_PRINTS: Dict[str, Deque[Dict[str, Any]]] = {}
# symbol -> next sequence number. Dense and monotonic per symbol, which is what
# lets the endpoint answer "everything after N" without scanning the tape.
_SEQ: Dict[str, int] = {}
# symbol -> dedupe/classification state carried between pushes.
_LAST: Dict[str, Dict[str, Any]] = {}
# symbol -> monotonic() when it was last asked for.
_HOT: Dict[str, float] = {}
# symbol -> 'idle' | 'running' | 'ready' | 'unavailable:<why>'
_BACKFILL: Dict[str, str] = {}
# symbol -> newest backfilled bar timestamp, so a live print that the backfill
# already covered is dropped rather than duplicated.
_BACKFILL_HIGH_TS: Dict[str, int] = {}
# Running totals for the honesty metric: attributed qty vs real volume moved.
_COVERAGE: Dict[str, List[int]] = {}

_socket: Any = None
_socket_started_at: float = 0.0
_last_push_at: float = 0.0
_subscribed: set = set()
_supervisor: Optional[threading.Thread] = None
_tape_day: Optional[date] = None

# An RLock, like live_candle_fallback's, so the snapshot/rollover helpers can
# re-enter it from inside a held section.
_lock = threading.RLock()


def _today() -> date:
    return datetime.now(IST).date()


def _market_is_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _roll_day_if_needed() -> date:
    """Clear the store when the calendar day turns.

    The app runs for days at a time under the LaunchAgent, so without this
    Monday's tab would open showing Friday's prints.
    """
    global _tape_day
    today = _today()
    with _lock:
        if _tape_day != today:
            _tape_day = today
            _PRINTS.clear()
            _SEQ.clear()
            _LAST.clear()
            _HOT.clear()
            _BACKFILL.clear()
            _BACKFILL_HIGH_TS.clear()
            _COVERAGE.clear()
            _subscribed.clear()
    return today


# ──────────────────────────────────────────────────────────────────────────
# Aggressor classification
# ──────────────────────────────────────────────────────────────────────────

def _classify(price: float, bid: Optional[float], ask: Optional[float],
              prev_price: Optional[float], prev_side: str) -> Tuple[str, str]:
    """Which side crossed the spread. Returns (side, rule_that_fired).

    Lee-Ready: a trade at or above the ask was a buyer lifting the offer, at or
    below the bid a seller hitting the bid. Inside the spread — or when the
    feed gave us no book — fall back to the tick test against the previous
    print, and on an unchanged price inherit the previous side so a flat run
    does not flicker green/red at random.
    """
    if bid and ask and bid > 0 and ask > 0:
        if price >= ask:
            return 'buy', 'quote'
        if price <= bid:
            return 'sell', 'quote'
    if prev_price is not None:
        if price > prev_price:
            return 'buy', 'tick'
        if price < prev_price:
            return 'sell', 'tick'
        return (prev_side or 'flat'), 'tick'
    return 'flat', 'tick'


def _append(symbol: str, ts: int, price: float, qty: Optional[int],
            side: str, side_rule: str, src: str, vol_delta: int) -> Dict[str, Any]:
    """Append one row under the caller's lock and hand it back."""
    seq = _SEQ.get(symbol, 0) + 1
    _SEQ[symbol] = seq
    row = {
        'seq': seq,
        'ts': int(ts),
        'price': round(float(price), 2),
        'qty': int(qty) if qty else None,
        'side': side,
        'side_rule': side_rule,
        'src': src,
        'vol_delta': int(vol_delta or 0),
    }
    dq = _PRINTS.get(symbol)
    if dq is None:
        dq = deque(maxlen=MAX_ROWS_PER_SYMBOL)
        _PRINTS[symbol] = dq
    dq.append(row)
    # Coverage measures how much of the traded volume the LIVE feed manages to
    # attribute to an actual print, so only 'tick' rows count. A backfilled bar
    # carries its own second's volume as its quantity, so counting those would
    # have it trivially "cover" itself and drag the figure to ~100% — which is
    # exactly what it did: a tape showing mostly backfill reported 98% when the
    # live feed's real capture is around 42%.
    #
    # The first row of a session also has no previous cumulative volume, so its
    # vol_delta is 0; counting its qty would attribute against a denominator of
    # nothing and overstate the figure again.
    if src == 'tick' and row['vol_delta'] > 0:
        cov = _COVERAGE.setdefault(symbol, [0, 0])
        cov[0] += row['qty'] or 0
        cov[1] += row['vol_delta']
    return row


# ──────────────────────────────────────────────────────────────────────────
# Websocket ingestion
# ──────────────────────────────────────────────────────────────────────────

def _on_message(msg: Any) -> None:
    """Fold one socket push into the tape.

    The SDK only emits when a field changed, but "changed" includes things a
    tape does not care about (a book update with no trade behind it), so the
    emit rule is specifically that the exchange's last_traded_time advanced.
    A push that repeats the previous second is the same trade seen twice.
    """
    global _last_push_at
    try:
        if not isinstance(msg, dict) or 'ltp' not in msg:
            return
        _last_push_at = monotonic()
        symbol = msg.get('symbol')
        price = msg.get('ltp')
        if not symbol or not price:
            return
        if not _market_is_open():
            # A pre-open push still carries the previous close; recording it
            # would plant a fake 09:00 print at yesterday's price.
            return

        ltt = msg.get('last_traded_time')
        ltq = msg.get('last_traded_qty')
        cum_vol = msg.get('vol_traded_today') or 0
        bid = msg.get('bid_price')
        ask = msg.get('ask_price')

        with _lock:
            _roll_day_if_needed()
            if _BACKFILL.get(symbol) == 'running':
                # Let the backfill finish laying down history before live rows
                # start arriving, so the two can never interleave.
                return
            last = _LAST.get(symbol) or {}
            prev_ltt = last.get('ltt')
            prev_vol = last.get('cum_vol')

            if ltt is not None:
                if prev_ltt is not None and ltt <= prev_ltt:
                    return                      # same trade, seen again
                ts = int(ltt)
            else:
                # No exchange stamp (shouldn't happen on futures, but the
                # index feed omits it). Fall back to "something actually
                # traded", and mark the row so the UI can show qty as unknown.
                if prev_vol is not None and cum_vol <= prev_vol:
                    return
                ts = int(datetime.now(IST).timestamp())
                ltq = None

            if ts <= _BACKFILL_HIGH_TS.get(symbol, 0):
                return                          # the backfill already has it

            vol_delta = max(0, int(cum_vol) - int(prev_vol)) if prev_vol else 0
            side, rule = _classify(price, bid, ask,
                                   last.get('price'), last.get('side', 'flat'))
            _append(symbol, ts, price, ltq, side, rule, 'tick', vol_delta)
            _LAST[symbol] = {'ltt': ltt, 'price': price, 'side': side,
                             'cum_vol': cum_vol}
    except Exception as e:                       # a bad push must never kill the feed
        logger.debug(f"[TimeAndSales] dropped a push: {e}")


def _access_token() -> Optional[str]:
    """The socket wants the full 'APPID:JWT'.

    The REST adapter deliberately strips the prefix because FyersModel prepends
    the client id itself (fyers_data_service.py:240-247); the socket does not,
    and sends the value straight through as the Authorization header.
    """
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        for i in range(1, 21):
            if UserEnvManager.get_user_var('Mine', f'BROKER_{i}_TYPE', '').lower() == 'fyers':
                app_id = UserEnvManager.get_user_var('Mine', f'BROKER_{i}_APP_ID')
                token = UserEnvManager.get_user_var('Mine', f'BROKER_{i}_ACCESS_TOKEN')
                if app_id and token:
                    return f"{app_id}:{str(token).split(':')[-1]}"
                return None
    except Exception as e:
        logger.warning(f"[TimeAndSales] could not read Fyers credentials: {e}")
    return None


def _open_socket() -> bool:
    """Build and connect the data socket. Returns True if it started."""
    global _socket, _socket_started_at, _last_push_at
    token = _access_token()
    if not token:
        return False
    try:
        from fyers_apiv3.FyersWebsocket import data_ws
    except Exception as e:
        logger.warning(f"[TimeAndSales] Fyers websocket SDK unavailable: {e}")
        return False

    def on_open():
        try:
            with _lock:
                want = list(_subscribed)
            if want:
                _socket.subscribe(symbols=want, data_type='SymbolUpdate')
            _socket.keep_running()
        except Exception as e:
            logger.warning(f"[TimeAndSales] subscribe on open failed: {e}")

    try:
        _socket = data_ws.FyersDataSocket(
            access_token=token, log_path=None, litemode=False,
            write_to_file=False, reconnect=True, reconnect_retry=5,
            on_connect=on_open,
            on_close=lambda m: logger.info(f"[TimeAndSales] socket closed: {m}"),
            on_error=lambda m: logger.warning(f"[TimeAndSales] socket error: {m}"),
            on_message=_on_message,
        )
        _socket.connect()
        _socket_started_at = monotonic()
        _last_push_at = monotonic()
        logger.info("[TimeAndSales] data socket connected")
        return True
    except Exception as e:
        logger.warning(f"[TimeAndSales] socket connect failed: {e}")
        _socket = None
        return False


def _close_socket() -> None:
    global _socket
    sock, _socket = _socket, None
    if sock is None:
        return
    try:
        sock.close_connection()
    except Exception:
        pass
    with _lock:
        _subscribed.clear()


def _sync_subscriptions(hot: List[str]) -> None:
    """Make the socket's subscriptions match the hot set."""
    global _subscribed
    if _socket is None:
        return
    want = set(hot)
    add, drop = want - _subscribed, _subscribed - want
    try:
        if add:
            _socket.subscribe(symbols=list(add), data_type='SymbolUpdate')
        if drop:
            _socket.unsubscribe(symbols=list(drop), data_type='SymbolUpdate')
    except Exception as e:
        logger.debug(f"[TimeAndSales] subscription sync failed: {e}")
        return
    with _lock:
        _subscribed = want


def _supervisor_loop() -> None:
    """Expire hot symbols, keep subscriptions in step, snapshot, watch for stalls."""
    while True:
        try:
            now = monotonic()
            with _lock:
                _roll_day_if_needed()
                for stale in [s for s, seen in _HOT.items() if now - seen > HOT_TTL_SEC]:
                    _HOT.pop(stale, None)
                hot = sorted(_HOT, key=lambda s: _HOT[s], reverse=True)[:MAX_TAPED_SYMBOLS]

            if not hot:
                # Nothing on screen: drop the connection entirely rather than
                # hold a broker session open all day for nobody.
                if _socket is not None:
                    logger.info("[TimeAndSales] no hot symbols, closing socket")
                    _close_socket()
                sleep(_SUPERVISOR_TICK_SEC)
                continue

            if _market_is_open():
                if _socket is None:
                    _open_socket()
                elif now - _last_push_at > _STALL_SECONDS:
                    logger.warning("[TimeAndSales] no pushes for "
                                   f"{now - _last_push_at:.0f}s, rebuilding socket")
                    _close_socket()
                    _open_socket()
                _sync_subscriptions(hot)
            elif _socket is not None:
                _close_socket()

            save_snapshot()
        except Exception as e:                   # never let the thread die
            logger.debug(f"[TimeAndSales] supervisor iteration failed: {e}")
        sleep(_SUPERVISOR_TICK_SEC)


def _ensure_supervisor() -> None:
    """Start the supervisor once, restoring today's tape before it runs."""
    global _supervisor
    with _lock:
        if _supervisor is not None and _supervisor.is_alive():
            return
        load_snapshot()
        prune_snapshots()
        _supervisor = threading.Thread(target=_supervisor_loop,
                                       name='time-and-sales-supervisor', daemon=True)
        _supervisor.start()
    logger.info("[TimeAndSales] supervisor started")


# ──────────────────────────────────────────────────────────────────────────
# Backfill from ICICI 1-second bars
# ──────────────────────────────────────────────────────────────────────────

def _backfill(symbol: str) -> None:
    """Lay down today's session so far, so the tab is not empty on open.

    A 1-second bar's volume is the sum of every trade in that second, so these
    rows are aggregated and are marked src='bar'. They are never presented as
    single prints.
    """
    try:
        from trading_app.service.provider_logic import get_icici_adapter
        adapter = get_icici_adapter('Mine')
        if adapter is None:
            with _lock:
                _BACKFILL[symbol] = 'unavailable:ICICI not connected'
            return

        today = _today().isoformat()
        bars = adapter.historical_data(symbol, today, today, '1second', use_cache=False)
        if not bars:
            why = ''
            try:
                why = adapter.last_history_error() or ''
            except Exception:
                pass
            with _lock:
                _BACKFILL[symbol] = f'unavailable:{why or "no 1-second history"}'
            return

        with _lock:
            if _PRINTS.get(symbol):
                # Something is already here (a snapshot restore, or a second
                # tab racing us). Re-laying history would duplicate every row.
                _BACKFILL[symbol] = 'ready'
                return
            prev_close, prev_side = None, 'flat'
            high_ts = 0
            for bar in bars:
                vol = int(bar.get('volume') or 0)
                if vol <= 0:
                    continue                     # Breeze pads flat filler bars
                stamp = bar.get('date')
                ts = int(stamp.timestamp()) if hasattr(stamp, 'timestamp') else int(stamp)
                close = bar.get('close')
                if not close:
                    continue
                op = bar.get('open') or close
                if close > op:
                    side, rule = 'buy', 'bar'
                elif close < op:
                    side, rule = 'sell', 'bar'
                else:
                    side, rule = (prev_side or 'flat'), 'bar'
                _append(symbol, ts, close, vol, side, rule, 'bar', vol)
                prev_close, prev_side, high_ts = close, side, max(high_ts, ts)
            _BACKFILL_HIGH_TS[symbol] = high_ts
            _LAST.setdefault(symbol, {'ltt': None, 'price': prev_close,
                                      'side': prev_side, 'cum_vol': None})
            _BACKFILL[symbol] = 'ready'
        logger.info(f"[TimeAndSales] backfilled {symbol} to seq {_SEQ.get(symbol)}")

        # Drop our raw 1-second windows out of the shared Breeze chunk cache.
        # One day of raw 1s is ~22,500 candles against a 150,000 candle cap,
        # and what it would evict is the aggregated 30-second day caches the
        # live algos depend on — sending them back to a broker that has blown
        # its daily quota before. Our own pickle makes a re-backfill rare, so
        # losing this cache costs the tape nothing.
        try:
            from trading_app.service.icici_data_service import flush_second_windows
            flush_second_windows(symbol)
        except ImportError:
            logger.debug("[TimeAndSales] flush_second_windows not available")
    except Exception as e:
        logger.warning(f"[TimeAndSales] backfill of {symbol} failed: {e}")
        with _lock:
            _BACKFILL[symbol] = f'unavailable:{e}'


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def register(symbol: str) -> None:
    """Mark `symbol` as wanted: start the feed, kick its backfill, keep it hot."""
    if not symbol:
        return
    with _lock:
        _roll_day_if_needed()
        _HOT[symbol] = monotonic()
        if len(_HOT) > MAX_TAPED_SYMBOLS:
            for cold in sorted(_HOT, key=lambda s: _HOT[s])[:-MAX_TAPED_SYMBOLS]:
                _HOT.pop(cold, None)
        kick = _BACKFILL.get(symbol, 'idle') == 'idle'
        if kick:
            _BACKFILL[symbol] = 'running'
    _ensure_supervisor()
    if kick:
        threading.Thread(target=_backfill, args=(symbol,),
                         name=f'tas-backfill-{symbol}', daemon=True).start()


def rows_since(symbol: str, since: int = 0, limit: int = 500,
               min_qty: int = 0) -> Tuple[List[Dict], bool]:
    """Rows with seq > `since`, oldest first. Returns (rows, truncated).

    `min_qty` filters HERE rather than in the browser, because the client only
    ever holds a window of the tape. Filtering there searched the few hundred
    rows it happened to have fetched and reported "0 rows" for a session that
    held 28 matching ones — the answer depended on how long the tab had been
    open, which is not a filter.
    """
    with _lock:
        dq = _PRINTS.get(symbol)
        if not dq:
            return [], False
        rows = [r for r in dq
                if r['seq'] > since and (not min_qty or (r['qty'] or 0) >= min_qty)]
    if len(rows) > limit:
        return rows[-limit:], True
    return rows, False


def coverage(symbol: str) -> Optional[float]:
    """Fraction of traded volume the LIVE feed attributes to an actual print.

    Backfilled bars are excluded (see _append) — they account for their own
    volume by construction and would report ~100%. The socket is throttled, so
    a true figure sits around 0.4 on NIFTY futures. Returns None until enough
    live prints have arrived to measure, so the UI can stay quiet rather than
    quote a number derived from one or two rows.
    """
    with _lock:
        cov = _COVERAGE.get(symbol)
    if not cov or cov[1] < _COVERAGE_MIN_VOLUME:
        return None
    return round(cov[0] / cov[1], 3)


def stats(symbol: str) -> Dict[str, Any]:
    """Session totals over the WHOLE tape, not the slice a client happens to hold.

    The browser used to derive these from its own row window, which is capped —
    so the print count simply reported the cap (a suspiciously round 3,000) and
    the order-flow split described the last few hundred trades rather than the
    session.

    `max_tick_qty` and `max_bar_qty` are carried so the UI can explain an empty
    filter instead of just showing nothing: a single exchange print is a handful
    of lots, and only an aggregated 1-second bar reaches the thousands.
    """
    with _lock:
        dq = list(_PRINTS.get(symbol) or ())
    buy = sell = prints = bars = 0
    max_tick = max_bar = 0
    for r in dq:
        qty = r['qty'] or 0
        if r['src'] == 'tick':
            prints += 1
            if qty > max_tick:
                max_tick = qty
            if r['side'] == 'buy':
                buy += qty
            elif r['side'] == 'sell':
                sell += qty
        else:
            bars += 1
            if qty > max_bar:
                max_bar = qty
    return {
        'prints': prints,
        'bars': bars,
        'flow_buy': buy,
        'flow_sell': sell,
        'max_tick_qty': max_tick,
        'max_bar_qty': max_bar,
        'last_price': dq[-1]['price'] if dq else None,
        'last_side': dq[-1]['side'] if dq else None,
    }


def status(symbol: str) -> Dict[str, Any]:
    with _lock:
        dq = _PRINTS.get(symbol)
        return {
            'streaming': _socket is not None and symbol in _subscribed,
            'backfill_state': _BACKFILL.get(symbol, 'idle'),
            'market_open': _market_is_open(),
            'day': (_tape_day or _today()).isoformat(),
            'head_seq': dq[0]['seq'] if dq else 0,
            'next_seq': _SEQ.get(symbol, 0),
            'rows_held': len(dq) if dq else 0,
            'coverage': coverage(symbol),
        }


# ──────────────────────────────────────────────────────────────────────────
# Snapshot persistence
# ──────────────────────────────────────────────────────────────────────────

def _snapshot_path(day: date) -> str:
    return os.path.join(_TAPE_DIR, f"tape_{day.isoformat()}.pkl")


def save_snapshot(force: bool = False) -> None:
    """Park the day's tape on disk. No-op unless _SNAPSHOT_EVERY_SEC elapsed."""
    global _last_snapshot_at
    now = monotonic()
    with _lock:
        if not force and now - _last_snapshot_at < _SNAPSHOT_EVERY_SEC:
            return
        _last_snapshot_at = now
        day = _tape_day
        payload = {s: list(dq) for s, dq in _PRINTS.items()}
        blob = {'prints': payload, 'seq': dict(_SEQ), 'last': dict(_LAST),
                'backfill': dict(_BACKFILL), 'high_ts': dict(_BACKFILL_HIGH_TS),
                'coverage': {k: list(v) for k, v in _COVERAGE.items()}}
    if not day or not payload:
        return
    try:
        os.makedirs(_TAPE_DIR, exist_ok=True)
        path = _snapshot_path(day)
        tmp = f"{path}.tmp"
        with open(tmp, 'wb') as fh:
            pickle.dump(blob, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug(f"[TimeAndSales] snapshot write failed: {e}")


def load_snapshot() -> int:
    """Restore today's tape from disk. Returns the number of symbols restored."""
    day = _roll_day_if_needed()
    path = _snapshot_path(day)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, 'rb') as fh:
            blob = pickle.load(fh)
    except Exception as e:
        logger.debug(f"[TimeAndSales] snapshot read failed: {e}")
        return 0
    restored = blob.get('prints') or {}
    if not restored:
        return 0
    with _lock:
        for sym, rows in restored.items():
            dq = deque(maxlen=MAX_ROWS_PER_SYMBOL)
            dq.extend(rows)
            _PRINTS[sym] = dq
        _SEQ.update(blob.get('seq') or {})
        _LAST.update(blob.get('last') or {})
        _BACKFILL.update(blob.get('backfill') or {})
        _BACKFILL_HIGH_TS.update(blob.get('high_ts') or {})
        # Recompute coverage from the restored rows rather than trusting the
        # stored totals. A snapshot written before backfilled bars were
        # excluded carries their self-covering volume in its running sum, and
        # restoring it would keep reporting ~100% for the rest of the day.
        # Deriving it from the rows makes an older snapshot heal itself.
        for sym, rows in restored.items():
            attributed = moved = 0
            for r in rows:
                if r.get('src') == 'tick' and (r.get('vol_delta') or 0) > 0:
                    attributed += r.get('qty') or 0
                    moved += r['vol_delta']
            _COVERAGE[sym] = [attributed, moved]
    logger.info(f"[TimeAndSales] restored tape for {len(restored)} symbols from {path}")
    return len(restored)


def prune_snapshots(keep_days: int = 5) -> None:
    """Drop snapshot files older than `keep_days` so the directory stays small."""
    try:
        cutoff = _today() - timedelta(days=keep_days)
        for name in os.listdir(_TAPE_DIR):
            if not (name.startswith('tape_') and name.endswith('.pkl')):
                continue
            try:
                stamp = date.fromisoformat(name[len('tape_'):-len('.pkl')])
            except ValueError:
                continue
            if stamp < cutoff:
                os.remove(os.path.join(_TAPE_DIR, name))
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"[TimeAndSales] snapshot prune failed: {e}")


def reset(symbol: Optional[str] = None) -> None:
    """Drop the tape for one symbol, or all of them. Used by tests."""
    with _lock:
        targets = [symbol] if symbol else list(_PRINTS)
        for s in targets:
            _PRINTS.pop(s, None)
            _SEQ.pop(s, None)
            _LAST.pop(s, None)
            _BACKFILL.pop(s, None)
            _BACKFILL_HIGH_TS.pop(s, None)
            _COVERAGE.pop(s, None)
