"""Kite WebSocket quote feed — a tick cache with REST as the fallback.

``KiteConnect.quote`` is capped at 1 request/second per API key, so every
poller sharing a key competes for that one slot. ``KiteTicker`` streams the
same fields (last price and the session's high/low) with no per-request
cost: subscribe a token once and read it from memory.

Nothing here places an order, and nothing here is trusted blindly:

* ``get`` returns ``None`` for a token that is not subscribed yet, has not
  ticked yet, or whose last tick is older than ``max_age`` — the caller then
  makes the ordinary REST call. A feed that is down, unauthorised or never
  started therefore degrades to exactly what ran before.
* It is OFF unless ``KITE_WS_QUOTES=true`` (read per call), so landing the
  code changes nothing until the flag is set and the app restarted.
* The session's ``high``/``low`` are the exchange's, carried on every tick in
  QUOTE mode, so a target watched on the high (see ``option_quote``) still
  sees a wick that happened between two reads.
"""
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE = 5.0   # seconds; an illiquid strike may not tick this often → REST


def enabled() -> bool:
    return os.environ.get('KITE_WS_QUOTES', '').strip().lower() in ('1', 'true', 'yes', 'on')


class KiteTickerFeed:
    def __init__(self, api_key: str, access_token: str,
                 ticker_factory: Optional[Callable[..., Any]] = None):
        self._api_key = api_key
        self._access_token = access_token
        self._factory = ticker_factory
        self._ticker = None
        self._lock = threading.Lock()
        self._subscribed: set = set()
        self._ticks: Dict[int, dict] = {}
        self.connected = False

    # ── lifecycle ───────────────────────────────────────────────────────
    def _make_ticker(self):
        factory = self._factory
        if factory is None:
            from kiteconnect import KiteTicker
            factory = KiteTicker
        t = factory(self._api_key, self._access_token)
        # setattr, not `t.on_ticks = …`: kiteconnect declares these class
        # attributes as None, so a type checker rejects the plain assignment
        # though it is exactly how the library expects callbacks to be set.
        for name, handler in (('on_ticks', self._on_ticks),
                              ('on_connect', self._on_connect),
                              ('on_close', self._on_close),
                              ('on_error', self._on_error)):
            setattr(t, name, handler)
        return t

    def start(self) -> None:
        with self._lock:
            if self._ticker is not None:
                return
            self._ticker = self._make_ticker()
            ticker = self._ticker
        # threaded=True: connect() returns at once and the library keeps
        # reconnecting on its own.
        ticker.connect(threaded=True)

    def stop(self) -> None:
        with self._lock:
            ticker, self._ticker = self._ticker, None
            self.connected = False
        if ticker is not None:
            try:
                ticker.close()
            except Exception as exc:
                logger.debug(f"[KiteWS] close: {exc}")

    # ── ticker callbacks (run on the library's thread) ──────────────────
    def _on_connect(self, ws, _response=None):
        with self._lock:
            self.connected = True
            tokens = sorted(self._subscribed)
        if tokens:   # a reconnect must resubscribe; the server forgets
            self._subscribe(ws, tokens)
        logger.info(f"[KiteWS] connected, {len(tokens)} token(s)")

    def _on_close(self, _ws, code=None, reason=None):
        self.connected = False
        logger.warning(f"[KiteWS] closed: {code} {reason}")

    def _on_error(self, _ws, code=None, reason=None):
        logger.warning(f"[KiteWS] error: {code} {reason}")

    def _on_ticks(self, _ws, ticks):
        now = time.monotonic()
        with self._lock:
            for t in ticks or ():
                tok = t.get('instrument_token')
                if tok is None:
                    continue
                ohlc = t.get('ohlc') or {}
                self._ticks[tok] = {
                    'last_price': t.get('last_price'),
                    'high': ohlc.get('high'),
                    'low': ohlc.get('low'),
                    'ts': now,
                }

    @staticmethod
    def _subscribe(ws, tokens) -> None:
        ws.subscribe(list(tokens))
        ws.set_mode(ws.MODE_QUOTE, list(tokens))

    # ── reading ─────────────────────────────────────────────────────────
    def ensure(self, tokens: Iterable[int]) -> None:
        """Subscribe any token not yet subscribed (starts the socket if needed)."""
        want = {int(t) for t in tokens if t}
        with self._lock:
            new = want - self._subscribed
            self._subscribed |= new
            ticker = self._ticker
            connected = self.connected
        if ticker is None:
            self.start()
        elif new and connected:
            try:
                self._subscribe(ticker, sorted(new))
            except Exception as exc:
                logger.warning(f"[KiteWS] subscribe failed: {exc}")

    def get(self, token: int, max_age: float = DEFAULT_MAX_AGE) -> Optional[dict]:
        """The latest tick as ``{'last_price','high','low'}``, or None if absent/stale."""
        with self._lock:
            tick = self._ticks.get(int(token))
        if not tick or not tick.get('last_price'):
            return None
        if time.monotonic() - tick['ts'] > max_age:
            return None
        return {k: tick[k] for k in ('last_price', 'high', 'low')}


_feeds: Dict[str, KiteTickerFeed] = {}
_feeds_lock = threading.Lock()


def get_feed(kite) -> Optional[KiteTickerFeed]:
    """One feed per access token (a new login gets a new socket), or None."""
    api_key = getattr(kite, 'api_key', None)
    token = getattr(kite, 'access_token', None)
    if not api_key or not token:
        return None
    with _feeds_lock:
        feed = _feeds.get(token)
        if feed is None:
            for old in _feeds.values():   # yesterday's token is dead; free its socket
                old.stop()
            _feeds.clear()
            feed = _feeds[token] = KiteTickerFeed(api_key, token)
        return feed


def ws_quote(kite, instrument_token, max_age: float = DEFAULT_MAX_AGE) -> Optional[dict]:
    """A fresh streamed quote for ``instrument_token`` or None (→ use REST).

    Never raises: any failure here must read as "no streamed quote".
    """
    if not enabled() or not instrument_token:
        return None
    try:
        feed = get_feed(kite)
        if feed is None:
            return None
        feed.ensure([instrument_token])
        return feed.get(instrument_token, max_age)
    except Exception as exc:
        logger.warning(f"[KiteWS] unavailable, using REST: {exc}")
        return None


def ws_price(kite, instrument_token, max_age: float = DEFAULT_MAX_AGE) -> float:
    """Streamed last price, or 0.0 when there is none (flag off, no tick, stale)."""
    q = ws_quote(kite, instrument_token, max_age)
    try:
        return float((q or {}).get('last_price') or 0)
    except (TypeError, ValueError):
        return 0.0
