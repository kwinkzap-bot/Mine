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
globals, its own pickle under data/tape/ and the SQLite archive beside it.

HISTORY
-------
The pickle is a restart snapshot: today's tape, parked so a LaunchAgent
respawn does not drop the morning. It is not a history — only today's file is
ever read back and old ones are pruned. `archive_snapshots` folds each day's
finished pickle into data/tape/tape.db, one row per print, kept forever, which
is what Round Strike reads to tag big prints on earlier days and replayed
windows. The archive is only as complete as the tape was: a day nobody watched
has no rows, and a day's rows are keyed on whatever contract was front month
THAT day, not the one being charted now.
"""

import logging
import os
import pickle
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
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
# The durable copy. Each day's pickle is folded in once it is final (see
# archive_snapshots) and never pruned.
_ARCHIVE_DB = os.path.join(_TAPE_DIR, 'tape.db')
# Bumped on every archive write. Round Strike keys its cached response on it,
# so a replayed window that was served untagged is rebuilt once the day lands.
_ARCHIVE_GEN = 0

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

# How often history is walked forward over the live prints it now covers. See
# _topup for why it has to keep catching up all session rather than stopping
# where the opening backfill did.
#
# Cost: one Breeze request per pass on a tape that is up to date, out of the
# app-wide 5,000/day the live algos share, and only while a tab is actually
# watching (HOT_TTL_SEC winds it down 45s after the last poll). A whole session
# watched end to end is ~375 requests.
_TOPUP_EVERY_SEC = 60.0
# Breeze answers a failed window with nothing, and retrying it every minute
# would spend the budget on an outage. Back off to this instead.
_TOPUP_BACKOFF_MAX = 300.0
# Never ask for the second still forming: its bar is half-built, and freezing a
# partial volume into the tape is not something a later pass would correct.
_TOPUP_LAG_SEC = 3
# Windows per pass. A tab opened at 14:20 is five hours behind, and fetching
# all of that in one pass would hold the fetch open for ~17s at Breeze's 1.5
# req/s; instead it catches up over consecutive passes, oldest first.
_TOPUP_MAX_REQUESTS = 8
# Long enough to mean "not again today". The day rollover clears it.
_DONE_FOR_THE_DAY = 6 * 3600.0


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
# symbol -> how many times its tape has been rebuilt. A top-up replaces live
# prints with the Σ bars covering the same seconds, which renumbers every seq
# after the seam — so a client's cursor stops meaning anything and it has to be
# told to refetch rather than stitch onto a number that has moved under it.
_EPOCH: Dict[str, int] = {}
# symbol -> counters that must survive that rebuild. The rail's print count and
# the empty-filter message describe what the LIVE FEED has seen all session,
# which is no longer answerable from the rows once history has replaced them.
_SEEN: Dict[str, Dict[str, int]] = {}
# symbol -> monotonic() at which the next top-up may run, and the symbols one
# is already running for.
_TOPUP_AT: Dict[str, float] = {}
_TOPUP_BUSY: set = set()

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
            _EPOCH.clear()
            _SEEN.clear()
            _TOPUP_AT.clear()
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


def _note_row(symbol: str, row: Dict[str, Any]) -> None:
    """Fold one NEW row into the running session counters.

    Called once per row when it first enters the tape, and never again — a
    top-up renumbers rows it is keeping, and re-counting those would double
    every figure here. Nothing in it is derivable from the tape afterwards,
    which is the point: a top-up deletes the live prints whose seconds history
    has since covered, and the rail must still be able to say how many prints
    the feed actually saw.
    """
    seen = _SEEN.setdefault(symbol, {'prints': 0, 'max_tick_qty': 0,
                                     'max_bar_qty': 0})
    qty = row['qty'] or 0
    if row['src'] == 'tick':
        seen['prints'] += 1
        seen['max_tick_qty'] = max(seen['max_tick_qty'], qty)
    else:
        seen['max_bar_qty'] = max(seen['max_bar_qty'], qty)

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
    if row['src'] == 'tick' and row['vol_delta'] > 0:
        cov = _COVERAGE.setdefault(symbol, [0, 0])
        cov[0] += qty
        cov[1] += row['vol_delta']


def _append_row(symbol: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Append one seq-less row under the caller's lock and hand it back."""
    seq = _SEQ.get(symbol, 0) + 1
    _SEQ[symbol] = seq
    row['seq'] = seq
    dq = _PRINTS.get(symbol)
    if dq is None:
        dq = deque(maxlen=MAX_ROWS_PER_SYMBOL)
        _PRINTS[symbol] = dq
    dq.append(row)
    _note_row(symbol, row)
    return row


def _append(symbol: str, ts: int, price: float, qty: Optional[int],
            side: str, side_rule: str, src: str, vol_delta: int) -> Dict[str, Any]:
    """Append one row under the caller's lock and hand it back."""
    return _append_row(symbol, {
        'ts': int(ts),
        'price': round(float(price), 2),
        'qty': int(qty) if qty else None,
        'side': side,
        'side_rule': side_rule,
        'src': src,
        'vol_delta': int(vol_delta or 0),
    })


def _bar_rows(bars: List[Dict[str, Any]],
              prev_side: str = 'flat') -> List[Dict[str, Any]]:
    """ICICI 1-second OHLCV bars as seq-less tape rows.

    A 1-second bar's volume is every trade in that second added together, so
    these rows are aggregated and are marked src='bar'. They are never
    presented as single prints.

    Shared by the opening backfill and every top-up after it, so the two lay
    down rows that are identical in every respect but when they were fetched —
    a seam the reader can see is a seam in the data, not in the code.
    """
    out: List[Dict[str, Any]] = []
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
            side = 'buy'
        elif close < op:
            side = 'sell'
        else:
            side = prev_side or 'flat'
        out.append({'ts': ts, 'price': round(float(close), 2), 'qty': vol,
                    'side': side, 'side_rule': 'bar', 'src': 'bar',
                    'vol_delta': vol})
        prev_side = side
    return out


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

            # Not gated on the market being open: the last minute before the
            # bell is live prints like any other, and one pass after it is what
            # settles them into Σ bars. _topup stops on its own once the
            # frontier reaches the close.
            for symbol in hot:
                _maybe_topup(symbol)

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

    This is the FIRST pass, not the only one: _topup keeps walking the same
    history forward for the rest of the session. Everything about how a bar
    becomes a row lives in _bar_rows, which both share.
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

        rows = _bar_rows(bars)
        with _lock:
            if _PRINTS.get(symbol):
                # Something is already here (a snapshot restore, or a second
                # tab racing us). Re-laying history would duplicate every row.
                # Whatever it holds, _topup carries it forward from its own
                # high-water mark, so nothing is lost by standing down.
                _BACKFILL[symbol] = 'ready'
                return
            for row in rows:
                _append_row(symbol, row)
            _BACKFILL_HIGH_TS[symbol] = rows[-1]['ts'] if rows else 0
            _LAST.setdefault(symbol, {
                'ltt': None,
                'price': rows[-1]['price'] if rows else None,
                'side': rows[-1]['side'] if rows else 'flat',
                'cum_vol': None})
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


def _session_bounds(day: date) -> Tuple[int, int]:
    """Today's session open and close as epoch seconds."""
    return (int(datetime.combine(day, MARKET_OPEN, IST).timestamp()),
            int(datetime.combine(day, MARKET_CLOSE, IST).timestamp()))


def _rebuild(symbol: str, bars: List[Dict[str, Any]], frontier: int) -> bool:
    """Swap the Σ bars for `bars` in over the live prints they cover.

    Under the lock, and in one shot, because a push landing halfway through
    would be classified against a tape that is half old and half new.

    Rows are renumbered from 1 rather than continuing the counter. Seq has to
    stay dense and ascending in TIME order — the client renders the tape by
    reversing it — and rows are being removed from the middle, so there is no
    numbering that both continues and stays ordered. The epoch is what tells a
    client its cursor no longer points where it thinks.
    """
    with _lock:
        dq = _PRINTS.get(symbol)
        settled = _BACKFILL_HIGH_TS.get(symbol, 0)
        held = list(dq) if dq else []
        # Below the old frontier is history we already trust; above the new one
        # are the live prints running ahead of it. Everything between is what
        # these bars replace — which is exactly the double-count that made
        # appending them impossible.
        #
        # A print inside that span with no bar on its second is dropped rather
        # than kept: `bars` is what history says traded there, and a row it has
        # no second for would contradict the series it now sits in.
        merged = ([r for r in held if r['ts'] <= settled]
                  + bars
                  + [r for r in held if r['ts'] > frontier])
        if not merged:
            return False
        for row in bars:
            _note_row(symbol, row)

        fresh: Deque[Dict[str, Any]] = deque(maxlen=MAX_ROWS_PER_SYMBOL)
        fresh.extend(merged)
        for seq, row in enumerate(fresh, 1):     # after maxlen has done its trimming
            row['seq'] = seq
        _PRINTS[symbol] = fresh
        _SEQ[symbol] = len(fresh)
        _BACKFILL_HIGH_TS[symbol] = frontier
        _EPOCH[symbol] = _EPOCH.get(symbol, 0) + 1
        return True


def _topup(symbol: str) -> None:
    """Walk the Σ series forward over the live prints it now covers.

    WHY THIS EXISTS
    ---------------
    The opening backfill stops wherever the tab was first opened and hands over
    to the socket, and the two are not the same measurement. A backfilled row is
    one second of the exchange's whole volume; a live row is one throttled
    snapshot's last-traded-quantity. Measured on 2026-09-10, NIFTY26SEPFUT:

        Σ bars   464 rows, 09:15-09:29, median qty   260, 56 cleared 1,000
        prints 4,084 rows, 09:29-14:45, median qty    65,  1 cleared 1,000

    So the Qty column silently changed meaning partway down the tape, and a
    min-qty filter that reads sensibly against bars hid essentially every live
    row below the seam — which is what a full day of tape looked like: a wall
    of Σ rows from the open, then nothing until a lone 1,300-lot print hours
    later. Aggregating the live feed into seconds does not fix it: it already
    delivers ~1.0 rows per second and reaches only 21% of the session's
    seconds, so its idea of "a second" is a fifth of the real one.

    History is therefore authoritative and keeps catching up. The socket holds
    only the last minute, ahead of the frontier, where nothing else can answer
    yet.

    Failure is deliberately inert: a Breeze hiccup leaves the frontier where it
    is and the live prints in place, so the tape degrades to what it did before
    this existed rather than going blank, and the next pass tries again.
    """
    delay = _TOPUP_EVERY_SEC
    try:
        with _lock:
            day = _tape_day or _today()
            settled = _BACKFILL_HIGH_TS.get(symbol, 0)
        open_ts, close_ts = _session_bounds(day)
        now_ts = int(datetime.now(IST).timestamp())
        frontier = min(now_ts - _TOPUP_LAG_SEC, close_ts)
        start_ts = max(settled + 1, open_ts)
        if frontier < start_ts:
            return                               # nothing has settled since

        from trading_app.service.provider_logic import get_icici_adapter
        adapter = get_icici_adapter('Mine')
        if adapter is None:
            delay = _TOPUP_BACKOFF_MAX
            return

        bars, complete = adapter.historical_seconds_range(
            symbol,
            datetime.fromtimestamp(start_ts, IST),
            datetime.fromtimestamp(frontier, IST),
            max_requests=_TOPUP_MAX_REQUESTS)
        if not bars:
            # Either Breeze refused, or the window genuinely held no trade. The
            # frontier stays put either way: moving it on an empty answer would
            # delete the live prints covering those seconds and put nothing in
            # their place.
            #
            # Past the close with nothing left to fetch, that is not a retry —
            # the session is over and those seconds will never fill. Standing
            # down matters because the tab stays open: at the backoff rate this
            # would be a Breeze request every five minutes until someone closed
            # it, for a tape that cannot change again.
            delay = (_DONE_FOR_THE_DAY if frontier >= close_ts
                     else _TOPUP_BACKOFF_MAX)
            return

        rows = _bar_rows(bars)
        if not rows:
            # Breeze answered with nothing but flat filler bars — the same
            # situation as an empty answer, and it stands down the same way.
            delay = (_DONE_FOR_THE_DAY if frontier >= close_ts
                     else _TOPUP_BACKOFF_MAX)
            return
        # The frontier only ever advances to the last bar actually returned,
        # never to the second that was asked for. _rebuild deletes the live
        # prints below it, so claiming a window Breeze answered short of would
        # delete prints and leave the seconds they covered empty — turning a
        # cosmetic seam into a real hole. Quiet seconds at the tail simply get
        # re-asked next pass, which costs one request inside the window that
        # was being fetched anyway.
        reached = rows[-1]['ts']
        if _rebuild(symbol, rows, reached):
            logger.info(f"[TimeAndSales] topped {symbol} up to "
                        f"{datetime.fromtimestamp(reached, IST):%H:%M:%S} "
                        f"(+{len(rows)} bars, epoch {_EPOCH.get(symbol)})")
        # Still behind: come straight back rather than idle a minute per pass.
        if not complete:
            delay = 0.0
    except Exception as e:
        logger.warning(f"[TimeAndSales] top-up of {symbol} failed: {e}")
        delay = _TOPUP_BACKOFF_MAX
    finally:
        with _lock:
            _TOPUP_BUSY.discard(symbol)
            _TOPUP_AT[symbol] = monotonic() + delay
        # Standing down for the day means the tape is final: history has been
        # walked to the close and nothing will change it again. That is the
        # moment to make it permanent — the 15:45 cron and the startup sweep
        # only exist for the days this line never ran (tab closed early, or
        # the process was down). Off the lock, on this thread.
        if delay >= _DONE_FOR_THE_DAY:
            try:
                save_snapshot(force=True)
                archive_snapshots()
            except Exception as e:
                logger.warning(f"[TimeAndSales] end-of-day archive of {symbol} failed: {e}")


def _maybe_topup(symbol: str) -> None:
    """Start a top-up for `symbol` if one is due and none is already running."""
    with _lock:
        if symbol in _TOPUP_BUSY:
            return
        if _BACKFILL.get(symbol) != 'ready':
            return                               # the first pass owns the tape
        if _today().weekday() >= 5:
            return                               # Breeze answers weekends with nothing
        if monotonic() < _TOPUP_AT.get(symbol, 0.0):
            return
        _TOPUP_BUSY.add(symbol)
    threading.Thread(target=_topup, args=(symbol,),
                     name=f'tas-topup-{symbol}', daemon=True).start()


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


def large_prints(symbol: str, min_qty: int) -> List[Dict[str, Any]]:
    """Every row on `symbol`'s tape with qty >= `min_qty`, oldest first.

    For overlays that only need to know WHERE the big prints landed — the
    Round Strike volume bars tint a bucket blue when one falls inside it —
    rather than the tape itself. Rows are (ts, price, qty, side, src); `ts` is
    a real epoch, so a caller on the app's fake-IST bar grid adds its offset.
    Backfilled 1-second bars (src='bar') count too: a second that traded
    8,000 lots is a big print whether or not the socket showed it as one.
    Read straight off the deque — it never blocks the collector for long.
    """
    if not symbol or min_qty <= 0:
        return []
    with _lock:
        dq = _PRINTS.get(symbol)
        if not dq:
            return []
        return [{'ts': r['ts'], 'price': r['price'], 'qty': r['qty'],
                 'side': r['side'], 'src': r['src']}
                for r in dq if (r['qty'] or 0) >= min_qty]


def view(symbol: str, since: int = 0, limit: int = 500, min_qty: int = 0,
         client_epoch: Optional[int] = None) -> Dict[str, Any]:
    """Everything one client poll needs, taken under a SINGLE lock.

    Reading the rows and the epoch in two separate lock sections let a top-up
    land between them: the client would be handed pre-rebuild rows stamped with
    the post-rebuild epoch, believe it was current, and spend the rest of the
    session stitching new rows onto a cursor pointing into a tape that had been
    renumbered underneath it.

    An epoch that does not match ours means exactly that renumbering happened,
    so the answer is the whole tape again and `truncated`, which is the client's
    existing signal to throw away what it holds and start from this response.
    """
    with _lock:
        epoch = _EPOCH.get(symbol, 0)
        rebuilt = client_epoch is not None and client_epoch != epoch
        rows, over_limit = rows_since(symbol, 0 if rebuilt else since,
                                      limit, min_qty)
        state = status(symbol)
        state.update(stats(symbol))
        # A client whose cursor is ahead of ours is holding rows from before a
        # restart (seqs reset with the process). Telling it to reset is better
        # than silently returning nothing forever.
        state['rows'] = rows
        state['truncated'] = over_limit or rebuilt or since > state['next_seq']
        return state


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

    The counts come from _SEEN and the flow from the rows, and the split is not
    arbitrary. A top-up deletes the live prints whose seconds history has since
    covered, so `prints` derived from the rows would fall back toward zero as
    the session went on and report the last minute of feed rather than the day.
    Flow is the opposite case: it has to describe what the tape is SHOWING, and
    summing the rows is what keeps a replaced print from being counted twice —
    once as itself and again inside the Σ bar that now stands for its second.
    """
    with _lock:
        dq = list(_PRINTS.get(symbol) or ())
        seen = dict(_SEEN.get(symbol) or {})
    buy = sell = bars = 0
    for r in dq:
        qty = r['qty'] or 0
        if r['src'] != 'tick':
            bars += 1
        if r['side'] == 'buy':
            buy += qty
        elif r['side'] == 'sell':
            sell += qty
    return {
        'prints': seen.get('prints', 0),
        'bars': bars,
        'flow_buy': buy,
        'flow_sell': sell,
        'max_tick_qty': seen.get('max_tick_qty', 0),
        'max_bar_qty': seen.get('max_bar_qty', 0),
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
            'epoch': _EPOCH.get(symbol, 0),
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
                'coverage': {k: list(v) for k, v in _COVERAGE.items()},
                'epoch': dict(_EPOCH),
                'seen': {k: dict(v) for k, v in _SEEN.items()},
                # Says the totals above already exclude backfilled bars, so a
                # restore can trust them instead of rederiving. See load_snapshot.
                'cov_v': 2}
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


def _derive_seen(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Best-effort session counters for a snapshot written before they existed.

    Only right for a tape no top-up has rebuilt — after one there is no way to
    recover how many prints were deleted — but that is exactly the tape an old
    snapshot holds.
    """
    seen = {'prints': 0, 'max_tick_qty': 0, 'max_bar_qty': 0}
    for r in rows:
        qty = r.get('qty') or 0
        if r.get('src') == 'tick':
            seen['prints'] += 1
            seen['max_tick_qty'] = max(seen['max_tick_qty'], qty)
        else:
            seen['max_bar_qty'] = max(seen['max_bar_qty'], qty)
    return seen


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
        _EPOCH.update(blob.get('epoch') or {})

        # Both of these were once derivable from the rows and no longer are: a
        # top-up deletes the live prints whose seconds history has covered, so
        # rederiving would count only the prints that happen to have survived
        # and report a fraction of the session's feed.
        stored_seen = blob.get('seen') or {}
        trust_totals = blob.get('cov_v', 0) >= 2
        for sym, rows in restored.items():
            if sym in stored_seen:
                _SEEN[sym] = dict(stored_seen[sym])
            else:
                _SEEN[sym] = _derive_seen(rows)
            if trust_totals and sym in (blob.get('coverage') or {}):
                _COVERAGE[sym] = list(blob['coverage'][sym])
                continue
            # An older snapshot's stored total carries backfilled bars' own
            # self-covering volume and would keep reporting ~100% all day.
            # Deriving it from the rows makes that snapshot heal itself.
            attributed = moved = 0
            for r in rows:
                if r.get('src') == 'tick' and (r.get('vol_delta') or 0) > 0:
                    attributed += r.get('qty') or 0
                    moved += r['vol_delta']
            _COVERAGE[sym] = [attributed, moved]
    logger.info(f"[TimeAndSales] restored tape for {len(restored)} symbols from {path}")
    return len(restored)


def _snapshot_day(name: str) -> Optional[date]:
    """The day a `tape_YYYY-MM-DD.pkl` filename names, or None for anything else."""
    if not (name.startswith('tape_') and name.endswith('.pkl')):
        return None
    try:
        return date.fromisoformat(name[len('tape_'):-len('.pkl')])
    except ValueError:
        return None


def prune_snapshots(keep_days: int = 5) -> None:
    """Drop snapshot files older than `keep_days` so the directory stays small.

    Only files the archive has already taken are dropped: a pickle is the sole
    copy of its day until archive_snapshots has read it, and this runs under
    _lock from _ensure_supervisor, so it reads the archive's ledger and never
    does the archiving itself.
    """
    try:
        cutoff = _today() - timedelta(days=keep_days)
        archived = _archived_mtimes()
        for name in os.listdir(_TAPE_DIR):
            stamp = _snapshot_day(name)
            if stamp is None or stamp >= cutoff:
                continue
            path = os.path.join(_TAPE_DIR, name)
            if archived.get(stamp.isoformat(), -1.0) < os.path.getmtime(path):
                logger.info(f"[TimeAndSales] keeping {name}: not archived yet")
                continue
            os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"[TimeAndSales] snapshot prune failed: {e}")


# ──────────────────────────────────────────────────────────────────────────
# Archive — the permanent copy
# ──────────────────────────────────────────────────────────────────────────

@contextmanager
def _archive_conn():
    """A connection to the archive, creating it on first use.

    One short-lived connection per call, like the OI history store, committed
    on a clean exit and always closed. WAL so Round Strike's request threads
    can read while the archive thread writes.
    """
    os.makedirs(_TAPE_DIR, exist_ok=True)
    conn = sqlite3.connect(_ARCHIVE_DB, timeout=10)
    try:
        _archive_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def _archive_schema(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prints (
            symbol    TEXT    NOT NULL,
            day       TEXT    NOT NULL,
            ts        INTEGER NOT NULL,
            seq       INTEGER NOT NULL,
            price     REAL,
            qty       INTEGER,
            side      TEXT,
            side_rule TEXT,
            src       TEXT,
            vol_delta INTEGER,
            PRIMARY KEY (symbol, day, seq)
        )""")
    conn.execute('CREATE INDEX IF NOT EXISTS prints_sym_day_qty ON prints (symbol, day, qty)')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archive_runs (
            symbol       TEXT NOT NULL,
            day          TEXT NOT NULL,
            rows         INTEGER,
            source_mtime REAL,
            archived_at  TEXT,
            PRIMARY KEY (symbol, day)
        )""")


def _archived_mtimes() -> Dict[str, float]:
    """day -> the oldest pickle mtime any of that day's symbols was archived from.

    The minimum, because a pickle holds every symbol taped that day and it is
    only safe to drop once each of them has been taken from this version of it.
    """
    if not os.path.exists(_ARCHIVE_DB):
        return {}
    try:
        with _archive_conn() as conn:
            rows = conn.execute(
                'SELECT day, MIN(source_mtime) FROM archive_runs GROUP BY day').fetchall()
        return {day: float(mt or 0.0) for day, mt in rows}
    except Exception as e:
        logger.debug(f"[TimeAndSales] archive ledger read failed: {e}")
        return {}


def archive_snapshots() -> int:
    """Fold every snapshot on disk into the archive. Returns rows written.

    Reads the pickles, not memory, so it serves past days and a process that
    has only just started alike. A (symbol, day) is rewritten whenever its
    pickle is newer than the copy the ledger records, as a delete-and-replace
    in one transaction: a top-up rebuild renumbers every seq after its seam,
    so rows cannot be upserted in place. Never called under _lock.
    """
    global _ARCHIVE_GEN
    try:
        names = sorted(os.listdir(_TAPE_DIR))
    except FileNotFoundError:
        return 0
    written = 0
    for name in names:
        stamp = _snapshot_day(name)
        if stamp is None:
            continue
        path = os.path.join(_TAPE_DIR, name)
        try:
            mtime = os.path.getmtime(path)
            with open(path, 'rb') as fh:
                blob = pickle.load(fh)
        except Exception as e:
            logger.warning(f"[TimeAndSales] archive skipped {name}: {e}")
            continue
        day = stamp.isoformat()
        for symbol, rows in (blob.get('prints') or {}).items():
            if not rows:
                continue
            try:
                written += _archive_day(symbol, day, rows, mtime)
            except Exception as e:
                logger.warning(f"[TimeAndSales] archive of {symbol} {day} failed: {e}")
    if written:
        _ARCHIVE_GEN += 1
        logger.info(f"[TimeAndSales] archived {written} rows into {_ARCHIVE_DB}")
    return written


def _archive_day(symbol: str, day: str, rows: List[Dict[str, Any]], mtime: float) -> int:
    with _archive_conn() as conn:
        prev = conn.execute(
            'SELECT rows, source_mtime FROM archive_runs WHERE symbol=? AND day=?',
            (symbol, day)).fetchone()
        if prev and (prev[1] or 0.0) >= mtime:
            return 0
        if prev and prev[0] and len(rows) < prev[0] * 0.9:
            # A shorter tape for a day already archived is not what a top-up
            # produces — a reset or a failed restore is. Take it, since the
            # pickle is the source of truth, but say so.
            logger.warning(f"[TimeAndSales] archive of {symbol} {day} shrinks "
                           f"{prev[0]} -> {len(rows)} rows")
        conn.execute('DELETE FROM prints WHERE symbol=? AND day=?', (symbol, day))
        conn.executemany(
            'INSERT OR REPLACE INTO prints (symbol, day, ts, seq, price, qty, side, '
            'side_rule, src, vol_delta) VALUES (?,?,?,?,?,?,?,?,?,?)',
            [(symbol, day, int(r['ts']), int(r['seq']), r.get('price'), r.get('qty'),
              r.get('side'), r.get('side_rule'), r.get('src'), r.get('vol_delta'))
             for r in rows])
        conn.execute(
            'INSERT OR REPLACE INTO archive_runs (symbol, day, rows, source_mtime, archived_at) '
            'VALUES (?,?,?,?,?)',
            (symbol, day, len(rows), mtime, datetime.now(IST).isoformat(timespec='seconds')))
    return len(rows)


def archive_generation() -> int:
    """How many times the archive has been written to in this process."""
    return _ARCHIVE_GEN


def archived_large_prints(symbol: str, min_qty: int, from_day: date,
                          to_day: date) -> List[Dict[str, Any]]:
    """Every archived print of `symbol` at or above `min_qty` on the days in
    [from_day, to_day], oldest first. Same rows as large_prints, so a caller
    can concatenate the two. Empty, never an error, when the archive has
    nothing or cannot be read — a chart without tags beats a chart that fails.
    """
    if not os.path.exists(_ARCHIVE_DB):
        return []
    try:
        with _archive_conn() as conn:
            rows = conn.execute(
                'SELECT ts, price, qty, side, src FROM prints '
                'WHERE symbol=? AND day BETWEEN ? AND ? AND qty >= ? ORDER BY ts',
                (symbol, from_day.isoformat(), to_day.isoformat(), int(min_qty))).fetchall()
    except Exception as e:
        logger.debug(f"[TimeAndSales] archive read failed for {symbol}: {e}")
        return []
    return [{'ts': ts, 'price': price, 'qty': qty, 'side': side, 'src': src}
            for ts, price, qty, side, src in rows]


def reset(symbol: Optional[str] = None) -> None:
    """Drop the tape for one symbol, or all of them. Used by tests."""
    with _lock:
        if symbol:
            targets = [symbol]
        else:
            # Every symbol any table knows, not just the ones holding rows —
            # a backfill state set before a single print arrived would
            # otherwise survive into the next test.
            targets = set(_PRINTS) | set(_BACKFILL) | set(_SEQ) | set(_TOPUP_AT) | set(_SEEN)
        for s in targets:
            _PRINTS.pop(s, None)
            _SEQ.pop(s, None)
            _LAST.pop(s, None)
            _BACKFILL.pop(s, None)
            _BACKFILL_HIGH_TS.pop(s, None)
            _COVERAGE.pop(s, None)
            _EPOCH.pop(s, None)
            _SEEN.pop(s, None)
            _TOPUP_AT.pop(s, None)
