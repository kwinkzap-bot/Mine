"""ICICI Direct (Breeze) as a data provider, behind the Kite/Fyers interface.

Why this exists: Fyers' intraday history has gone out on us before (2026-08-03,
see live_candle_fallback) and Kite has no historical subscription on any of the
four apps. Breeze gives 1-second-to-daily history *including open interest* on
options, on a plain retail login.

Vocabulary
----------
Every caller in this app addresses instruments with Fyers-style symbol strings
('NSE:NIFTY2590924500CE', 'NSE:NIFTY50-INDEX', 'NSE:RELIANCE-EQ') — the OI
caches, the algo state files and the option-token map all store them. Breeze
speaks a different language entirely: (stock_code, exchange_code, product_type,
expiry_date, right, strike_price).

So this adapter keeps the app's vocabulary and translates on the way out:

    symbol  --Fyers public symbol master-->  (root, expiry, strike, CE/PE)
            --ICICI security master------->  Breeze stock_code
            ------------------------------>  Breeze request

The Fyers symbol master is a public unauthenticated CSV download, so leaning on
it here costs nothing and buys the one property that matters: a symbol resolved
under DATA_PROVIDER=FYERS still means the same instrument under
DATA_PROVIDER=ICICI. Switching providers does not invalidate a cache or a
live position's stored token.

Rate limit
----------
Breeze allows 100 requests/minute (5000/day) — an order of magnitude tighter
than Fyers' 10/s. That is the design constraint behind quote(): option symbols
are grouped by (root, expiry, right) and answered from ONE option-chain call
each instead of one call per strike. A 40-strike OI Profile refresh is 2
requests, not 40.

Status: written against the documented Breeze API. Every symbol-mapping path is
verified against the real security master; the response parsing is defensive
(each field read under several possible key names) but has NOT been exercised
against a live Breeze session — no credentials were available at build time.
"""

import logging
import threading
import time
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from trading_app.service import icici_symbol_master as master

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Breeze publishes 100 req/min. Pace at 1.5/s (90/min) to leave headroom for
# the login/session calls that do not go through here.
_RATE_LIMIT_PER_SEC = 1.5


class BreezeRateLimiter:
    """Thread-safe pacer, same shape as FyersRateLimiter."""

    def __init__(self, requests_per_second: float = _RATE_LIMIT_PER_SEC):
        self.delay = 1.0 / requests_per_second
        self.next_call = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        sleep_for = 0.0
        with self.lock:
            now = time.time()
            if now < self.next_call:
                sleep_for = self.next_call - now
                self.next_call += self.delay
            else:
                self.next_call = now + self.delay
        if sleep_for > 0:
            time.sleep(sleep_for)


_rate_limiter = BreezeRateLimiter()

# Caches. Kept process-global so the adapter can be rebuilt per request (which
# provider_logic does) without throwing the data away.
_QUOTE_CACHE: Dict[str, Tuple[Dict[str, Any], datetime]] = {}
_QUOTE_LOCK = threading.Lock()
_HIST_CACHE: Dict[str, Tuple[List[Dict[str, Any]], datetime]] = {}
_HIST_LOCK = threading.Lock()
_CHAIN_CACHE: Dict[str, Tuple[Dict[Any, Dict], datetime]] = {}
_CHAIN_LOCK = threading.Lock()
_HIST_ERROR = threading.local()

# Kite instrument tokens / Kite index names → the canonical Fyers-style symbol,
# so a caller still holding a Kite token gets translated the same way the Fyers
# adapter translates it.
from trading_app.service.fyers_data_service import (  # noqa: E402  (cycle-free: no reverse import)
    _BSE_FUTURE_ROOTS,
    _KITE_TO_FYERS_INDICES,
    FyersDataServiceAdapter,
)

# Breeze intervals: 1minute, 5minute, 30minute, 1day — the four
# get_historical_data_v2 documents. Everything else the app asks for is built by
# aggregating the next-finest one that divides it.
#
# '1second' is listed here because the 30-second timeframe is built from it, but
# v2 does not serve it within any usable request budget (see _BREEZE_UNSUPPORTED
# below), so a 30s chart on ICICI is refused with a reason rather than drawn from
# a silently truncated window.
#   app interval -> (breeze interval, how many of them make one bar)
_INTERVAL_MAP: Dict[str, Tuple[str, int]] = {
    '30second': ('1second', 30),
    'minute':   ('1minute', 1),
    '1minute':  ('1minute', 1),
    '2minute':  ('1minute', 2),
    '3minute':  ('1minute', 3),
    '5minute':  ('5minute', 1),
    '10minute': ('5minute', 2),
    '15minute': ('5minute', 3),
    '30minute': ('30minute', 1),
    '60minute': ('30minute', 2),
    '2hour':    ('30minute', 4),
    '4hour':    ('30minute', 8),
    'day':      ('1day', 1),
    'week':     ('1day', 0),    # 0 = calendar grouping, not a fixed multiple
    'month':    ('1day', 0),
}

# Seconds in one bar of each Breeze interval, and how many days of it we dare
# ask for in a single request. get_historical_data_v2 documents a hard ceiling
# of 1000 candles per request and truncates silently above it, so each chunk is
# sized to stay under that against a 375-minute session:
#
#   1minute    2 days  ->   750 candles
#   5minute   10 days  ->   750
#   30minute  60 days  ->  ~516 (≈43 trading days x 12)
#   1day     500 days  ->  ~340 trading days
#
# 1second is the exception and cannot be made to fit: one session alone is
# 22,500 one-second candles, 22x the ceiling, and chunking down to ~16-minute
# windows would cost 23 requests per day against a 100 req/min account budget.
# It is refused outright rather than served silently truncated — which is what
# a 1-day chunk was quietly doing. See _INTERVAL_MAP's note.
_BREEZE_MAX_CANDLES = 1000
_BREEZE_BAR_SECONDS = {'1second': 1, '1minute': 60, '5minute': 300,
                       '30minute': 1800, '1day': 86400}
_CHUNK_DAYS = {'1minute': 2, '5minute': 10, '30minute': 60, '1day': 500}
_BREEZE_UNSUPPORTED = {'1second'}

# NSE/BSE cash and derivatives both open at 09:15; anchoring the aggregation
# there is what makes a 15-minute bar here line up with Kite's and with the
# backtest engines' bars. An anchor of midnight would shift every odd
# multiple (3, 15, 2h) by 15 minutes and silently change signal candles.
_SESSION_OPEN = (9, 15)


class IciciDataServiceAdapter:
    """Adapter making the Breeze API look like KiteConnect for data fetching."""

    def __init__(self, api_key: str, api_secret: Optional[str] = None,
                 session_token: Optional[str] = None):
        """
        Args:
            api_key: Breeze app key (from api.icicidirect.com)
            api_secret: Breeze app secret
            session_token: the API session number from the daily Breeze login
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self._session_token = session_token
        self.breeze = None
        # False until a session is actually live. provider_logic refuses to
        # hand out an adapter without it: a provider that answers every fetch
        # with [] is worse than none at all — the caller cannot tell "not
        # configured" from "no candles today", and on 2026-09-03 that took the
        # CPR-width endpoint down with a bare 500.
        self.session_ok = False

        # Symbol-master duties (instruments, find_option_symbol, lot sizes) run
        # off the public Fyers CSVs, which need no Fyers credentials — this
        # instance is constructed with no client on purpose. Set up before the
        # Breeze client, so a missing SDK or a dead session still leaves an
        # adapter that resolves symbols and reports "no data" cleanly instead
        # of raising AttributeError halfway through a quote.
        self._symbols = FyersDataServiceAdapter(None)
        self._reverse: Dict[str, Dict[str, Any]] = {}
        self._reverse_at: Optional[datetime] = None
        self._reverse_lock = threading.Lock()

        try:
            from breeze_connect import BreezeConnect
        except ImportError:
            logger.error("[IciciAdapter] breeze-connect is not installed "
                         "(pip install breeze-connect) — adapter resolves symbols "
                         "but fetches nothing.")
            return

        try:
            self.breeze = BreezeConnect(api_key=api_key)
            if api_secret and session_token:
                self.breeze.generate_session(api_secret=api_secret,
                                             session_token=session_token)
                self.session_ok = True
                logger.info("[IciciAdapter] Breeze session established (api_key=%s...)",
                            str(api_key)[:6])
            else:
                logger.warning("[IciciAdapter] No api_secret/session_token — "
                               "Breeze calls will fail until the daily login runs.")
        except Exception as exc:
            logger.error("[IciciAdapter] generate_session failed: %s", exc)

    # ── KiteConnect surface bits the callers poke at ──────────────────────

    @property
    def access_token(self) -> Optional[str]:
        return self._session_token

    def set_access_token(self, token: str) -> None:
        self._session_token = token

    # ── Symbol master: delegated to the shared public CSVs ────────────────

    def instruments(self, exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._symbols.instruments(exchange)

    def find_option_symbol(self, root: str, strike: float, option_type: str,
                           expiry_type: str = 'nearest') -> Optional[str]:
        return self._symbols.find_option_symbol(root, strike, option_type, expiry_type)

    def list_future_contracts(self, root: str,
                              exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._symbols.list_future_contracts(root, exchange)

    def find_future_symbol(self, root: str, exchange: Optional[str] = None) -> Optional[str]:
        return self._symbols.find_future_symbol(root, exchange)

    def clear_instruments_cache(self) -> None:
        self._symbols.clear_instruments_cache()

    def get_lot_size(self, symbol: str) -> int:
        """Lot size, preferring ICICI's own master so it matches what Breeze fills."""
        inst = self._resolve(symbol)
        if inst:
            lot = master.lot_size(inst['root'], inst['exchange_code'])
            if lot:
                return lot
        return self._symbols.get_lot_size(symbol)

    # ── Symbol → Breeze request parameters ────────────────────────────────

    def _reverse_index(self) -> Dict[str, Dict[str, Any]]:
        """symbol string → its row in the Fyers symbol master, rebuilt hourly."""
        with self._reverse_lock:
            fresh = (self._reverse_at is not None
                     and (datetime.now() - self._reverse_at).total_seconds() < 3600)
            if fresh and self._reverse:
                return self._reverse
            index: Dict[str, Dict[str, Any]] = {}
            for exch in ('NFO', 'BFO', 'NSE', 'BSE'):
                try:
                    for row in self._symbols.instruments(exch):
                        token = row.get('instrument_token')
                        if token:
                            index.setdefault(token, dict(row, _exchange=exch))
                except Exception as exc:
                    logger.warning("[IciciAdapter] instruments(%s) failed: %s", exch, exc)
            if index:
                self._reverse = index
                self._reverse_at = datetime.now()
            return self._reverse

    def _resolve_contract_id(self, sym: str) -> Optional[Dict[str, Any]]:
        """'ICICI:<ROOT>:<YYYY-MM-DD>:<CE|PE>:<strike>' (option, see
        option_contract_id) or 'ICICI:<ROOT>:<YYYY-MM-DD>:FUT' (future, see
        future_contract_id) -> Breeze request parts.

        No symbol-master lookup beyond the root's stock_code, which is a
        property of the underlying and not of any one contract — so this keeps
        working after the contract has expired and been delisted.
        """
        parts = sym.split(':')
        if len(parts) == 4 and parts[3].strip().upper() == 'FUT':
            _, root, exp_raw, _ = parts
            root = root.strip().upper()
            try:
                expiry = datetime.strptime(exp_raw.strip()[:10], '%Y-%m-%d').date()
            except ValueError:
                logger.warning("[IciciAdapter] %r: unparseable expiry", sym)
                return None
            exch = 'BFO' if root in _BSE_FUTURE_ROOTS else 'NFO'
            code = master.stock_code(root, exch)
            if not code:
                logger.warning("[IciciAdapter] no Breeze stock_code for %s on %s", root, exch)
                return None
            return {'root': root, 'stock_code': code, 'exchange_code': exch,
                    'product_type': 'futures', 'expiry_date': _breeze_expiry(expiry),
                    'right': None, 'strike_price': None, 'symbol': sym}

        if len(parts) != 5:
            logger.warning("[IciciAdapter] malformed contract id %r", sym)
            return None
        _, root, exp_raw, right, strike_raw = parts
        root = root.strip().upper()
        right = right.strip().upper()
        if right not in ('CE', 'PE'):
            logger.warning("[IciciAdapter] %r: right must be CE or PE", sym)
            return None
        try:
            expiry = datetime.strptime(exp_raw.strip()[:10], '%Y-%m-%d').date()
            strike = int(float(strike_raw))
        except ValueError:
            logger.warning("[IciciAdapter] %r: unparseable expiry or strike", sym)
            return None

        exch = 'BFO' if root in _BSE_FUTURE_ROOTS else 'NFO'
        code = master.stock_code(root, exch)
        if not code:
            logger.warning("[IciciAdapter] no Breeze stock_code for %s on %s", root, exch)
            return None

        return {'root': root, 'stock_code': code, 'exchange_code': exch,
                'product_type': 'options', 'expiry_date': _breeze_expiry(expiry),
                'right': 'call' if right == 'CE' else 'put',
                'strike_price': str(strike), 'symbol': sym}

    def _resolve(self, symbol: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Translate one app symbol into the pieces Breeze needs.

        Returns a dict with root / exchange_code / product_type / expiry_date /
        right / strike_price, or None when the symbol cannot be placed.
        """
        sym = str(symbol).strip()
        sym = _KITE_TO_FYERS_INDICES.get(sym, sym)
        upper = sym.upper()

        # Explicit contract id (see option_contract_id) — parsed, never looked
        # up, so it resolves for expiries the symbol masters have already
        # dropped. This is the only path that works for a past expiry.
        if upper.startswith('ICICI:'):
            return self._resolve_contract_id(sym)

        # Index spot: 'NSE:NIFTY50-INDEX'
        if upper.endswith('-INDEX'):
            body = upper.split(':')[-1][:-len('-INDEX')]
            exch = 'BSE' if upper.startswith('BSE:') else 'NSE'
            code = master.stock_code(body, exch)
            if not code:
                return None
            return {'root': body, 'stock_code': code, 'exchange_code': exch,
                    'product_type': 'cash', 'expiry_date': None,
                    'right': None, 'strike_price': None, 'symbol': sym}

        # Cash equity: 'NSE:RELIANCE-EQ'
        if upper.endswith('-EQ'):
            body = upper.split(':')[-1][:-len('-EQ')]
            exch = 'BSE' if upper.startswith('BSE:') else 'NSE'
            code = master.stock_code(body, exch)
            if not code:
                return None
            return {'root': body, 'stock_code': code, 'exchange_code': exch,
                    'product_type': 'cash', 'expiry_date': None,
                    'right': None, 'strike_price': None, 'symbol': sym}

        # Derivatives — read the contract off the symbol master rather than
        # parsing the symbol text. Fyers writes weeklies and monthlies with
        # different date encodings, and a mis-parse here would silently fetch
        # the wrong expiry's candles.
        row = self._reverse_index().get(sym)
        if not row:
            logger.warning("[IciciAdapter] %s is not in the symbol master", sym)
            return None

        exch = 'BFO' if row.get('_exchange') == 'BFO' else 'NFO'
        root = (row.get('name') or '').strip().upper()
        code = master.stock_code(root, exch)
        if not code:
            return None

        itype = (row.get('instrument_type') or '').upper()
        expiry = row.get('expiry')
        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry[:10], '%Y-%m-%d').date()
            except ValueError:
                expiry = None
        if expiry is None:
            logger.warning("[IciciAdapter] %s has no expiry in the master", sym)
            return None

        if itype in ('CE', 'PE'):
            return {'root': root, 'stock_code': code, 'exchange_code': exch,
                    'product_type': 'options',
                    'expiry_date': _breeze_expiry(expiry),
                    'right': 'call' if itype == 'CE' else 'put',
                    'strike_price': str(int(float(row.get('strike') or 0))),
                    'symbol': sym}

        return {'root': root, 'stock_code': code, 'exchange_code': exch,
                'product_type': 'futures', 'expiry_date': _breeze_expiry(expiry),
                'right': None, 'strike_price': None, 'symbol': sym}

    # ── Quotes ────────────────────────────────────────────────────────────

    def quote(self, symbols: List[str], priority: int = 0) -> Dict[str, Any]:
        """Kite-shaped quotes: {symbol: {last_price, ohlc, volume, oi, ...}}.

        Options are answered in bulk from the option chain (one request per
        root/expiry/right); everything else costs one request each. Anything
        that fails falls back to the last good quote for that symbol, the same
        stale-cache rescue the Fyers adapter uses — a blank quote reaching an
        algo is far worse than a slightly old one.
        """
        out: Dict[str, Any] = {}
        if not symbols:
            return out

        resolved: Dict[str, Dict[str, Any]] = {}
        for sym in symbols:
            info = self._resolve(sym)
            if info:
                resolved[sym] = info

        # Group options so one chain request answers a whole strike ladder.
        groups: Dict[Tuple[str, str, str, str], List[str]] = {}
        singles: List[str] = []
        for sym, info in resolved.items():
            if info['product_type'] == 'options':
                key = (info['stock_code'], info['exchange_code'],
                       info['expiry_date'], info['right'])
                groups.setdefault(key, []).append(sym)
            else:
                singles.append(sym)

        now = datetime.now()

        for (code, exch, expiry, right), syms in groups.items():
            chain = self._chain_rows(code, exch, expiry, right)
            for sym in syms:
                strike = resolved[sym]['strike_price']
                row = chain.get(str(int(float(strike))))
                if row:
                    out[sym] = _quote_from_row(row)

        for sym in singles:
            row = self._get_quote_row(resolved[sym])
            if row:
                out[sym] = _quote_from_row(row)

        with _QUOTE_LOCK:
            for sym, q in out.items():
                _QUOTE_CACHE[sym] = (q, now)
            for sym in symbols:
                if sym not in out and sym in _QUOTE_CACHE:
                    out[sym] = _QUOTE_CACHE[sym][0]
        return out

    def ltp(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        quotes = self.quote(symbols, priority=1)
        return {k: {'last_price': v.get('last_price', 0.0)} for k, v in quotes.items()}

    def _get_quote_row(self, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.breeze is None:
            return None
        kwargs = {'stock_code': info['stock_code'],
                  'exchange_code': info['exchange_code'],
                  'product_type': info['product_type'],
                  'expiry_date': info['expiry_date'] or '',
                  'right': info['right'] or '',
                  'strike_price': info['strike_price'] or ''}
        _rate_limiter.wait()
        try:
            resp = self.breeze.get_quotes(**kwargs)
        except Exception as exc:
            logger.error("[IciciAdapter] get_quotes(%s) failed: %s", info['symbol'], exc)
            return None
        rows = _success(resp)
        if not rows:
            logger.warning("[IciciAdapter] get_quotes(%s) empty: %s",
                           info['symbol'], _error_of(resp))
            return None
        return rows[0]

    # ── Option chain ──────────────────────────────────────────────────────

    def _chain_rows(self, stock_code: str, exchange_code: str,
                    expiry_date: str, right: str) -> Dict[str, Dict[str, Any]]:
        """One side of an option chain, keyed by strike (as an integer string)."""
        key = f"{stock_code}:{exchange_code}:{expiry_date}:{right}"
        with _CHAIN_LOCK:
            hit = _CHAIN_CACHE.get(key)
            if hit and (datetime.now() - hit[1]).total_seconds() < 10:
                return hit[0]

        if self.breeze is None:
            return {}
        _rate_limiter.wait()
        try:
            resp = self.breeze.get_option_chain_quotes(
                stock_code=stock_code, exchange_code=exchange_code,
                product_type='options', expiry_date=expiry_date, right=right)
        except Exception as exc:
            logger.error("[IciciAdapter] option chain %s failed: %s", key, exc)
            return {}

        rows = _success(resp)
        if not rows:
            logger.warning("[IciciAdapter] option chain %s empty: %s", key, _error_of(resp))
            return {}

        by_strike: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            strike = _num(row, 'strike_price', 'strikePrice', 'strike')
            if strike is None:
                continue
            by_strike[str(int(strike))] = row
        with _CHAIN_LOCK:
            _CHAIN_CACHE[key] = (by_strike, datetime.now())
        return by_strike

    def get_option_chain_raw(self, symbol: str = 'NSE:NIFTY50-INDEX',
                             strikecount: int = 50) -> Dict[tuple, Dict]:
        """Both sides of the nearest-expiry chain, keyed (strike, 'CE'|'PE').

        Same contract as FyersDataServiceAdapter.get_option_chain_raw, with one
        difference the callers have to live with: Breeze's chain carries no
        implied volatility or greeks, so 'iv', 'delta' and 'vega' come back
        None. Callers that need delta must compute it from a spot/IV estimate
        of their own — the Fyers path is the one to use when greeks matter.

        strikecount is accepted for signature compatibility and used to trim the
        ladder around ATM; Breeze always returns the full chain for an expiry.
        """
        info = self._resolve(symbol)
        if not info:
            return {}
        root, exch = info['root'], info['exchange_code']
        fno_exch = 'BFO' if exch in ('BSE', 'BFO') else 'NFO'
        code = master.stock_code(root, fno_exch)
        if not code:
            return {}
        expiry = self._nearest_expiry(code, fno_exch)
        if not expiry:
            logger.warning("[IciciAdapter] No listed expiry for %s on %s", code, fno_exch)
            return {}

        result: Dict[tuple, Dict] = {}
        for right, ot in (('call', 'CE'), ('put', 'PE')):
            for strike, row in self._chain_rows(code, fno_exch, expiry, right).items():
                ltp = _num(row, 'ltp', 'last_price', 'close')
                result[(int(strike), ot)] = {
                    'ltp': round(float(ltp), 2) if ltp else None,
                    'iv': None,
                    'delta': None,
                    'vega': None,
                    'oi': _num(row, 'open_interest', 'oi', 'openInterest') or 0,
                    'change_in_oi': _num(row, 'chnge_oi', 'change_in_oi', 'oi_change') or 0,
                    'symbol': self.find_option_symbol(root, float(strike), ot) or '',
                }

        if strikecount and result:
            result = _trim_to_atm(result, strikecount)
        logger.info("[IciciAdapter] option chain raw: %d entries for %s (expiry %s)",
                    len(result), symbol, expiry[:10])
        return result

    def _nearest_expiry(self, stock_code: str, exchange_code: str) -> Optional[str]:
        today = datetime.now(IST).date().isoformat()
        for iso in master.expiries(stock_code, exchange_code):
            if iso >= today:
                return _breeze_expiry(datetime.strptime(iso, '%Y-%m-%d').date())
        return None

    # ── History ───────────────────────────────────────────────────────────

    def historical_data(self, instrument_token: Union[int, str], from_date: str,
                        to_date: str, interval: str, oi: bool = False,
                        use_cache: bool = True, allow_synthetic: bool = False,
                        cache_ttl: Optional[float] = None) -> List[Dict[str, Any]]:
        """Kite-shaped candles: [{date, open, high, low, close, volume, oi}, ...].

        allow_synthetic is accepted for signature compatibility and ignored —
        the local candle rebuild exists to paper over the Fyers outage, and
        reconstructing bars from a second provider's quotes would only hide
        whether Breeze itself is answering.
        """
        _HIST_ERROR.msg = None
        info = self._resolve(instrument_token)
        if not info:
            _HIST_ERROR.msg = f"{instrument_token} could not be mapped to an ICICI contract"
            return []

        if interval not in _INTERVAL_MAP:
            _HIST_ERROR.msg = f"Unsupported interval {interval!r}"
            logger.error("[IciciAdapter] unsupported interval %r", interval)
            return []
        breeze_interval, factor = _INTERVAL_MAP[interval]
        if breeze_interval in _BREEZE_UNSUPPORTED:
            _HIST_ERROR.msg = (f"ICICI Direct does not serve {interval} candles — its history "
                               f"API caps a request at {_BREEZE_MAX_CANDLES} candles, and one "
                               f"session of 1-second bars is 22,500. Use 1m or coarser.")
            logger.warning("[IciciAdapter] %s refused: %s", interval, _HIST_ERROR.msg)
            return []

        fd = _as_date(from_date)
        td = _as_date(to_date)
        if fd is None or td is None:
            _HIST_ERROR.msg = f"Unparseable date range {from_date}..{to_date}"
            return []

        cache_key = f"{info['symbol']}:{fd}:{td}:{interval}"
        default_ttl = 300.0 if breeze_interval == '1day' else 15.0
        ttl = default_ttl if cache_ttl is None else float(cache_ttl)
        if use_cache:
            with _HIST_LOCK:
                hit = _HIST_CACHE.get(cache_key)
            if hit and (datetime.now() - hit[1]).total_seconds() < ttl:
                return hit[0]

        if self.breeze is None:
            _HIST_ERROR.msg = "breeze-connect not installed / no session"
            return []

        raw: List[Dict[str, Any]] = []
        chunk = timedelta(days=_CHUNK_DAYS.get(breeze_interval, 5))
        cursor = fd
        while cursor <= td:
            chunk_end = min(cursor + chunk - timedelta(days=1), td)
            raw.extend(self._history_chunk(info, breeze_interval, cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)

        if not raw:
            # Same reasoning as the Fyers adapter: returning [] caches nothing,
            # so a 4-second chart poll would retry from scratch forever and keep
            # the (much tighter) Breeze budget saturated. Serve the last good
            # answer for this exact key instead; last_history_error() still says
            # why it is stale.
            if use_cache:
                with _HIST_LOCK:
                    hit = _HIST_CACHE.get(cache_key)
                if hit:
                    age = (datetime.now() - hit[1]).total_seconds()
                    logger.warning("[IciciAdapter] history empty for %s — serving %d "
                                   "cached candles (%.0fs stale)",
                                   info['symbol'], len(hit[0]), age)
                    return hit[0]
            return []

        # Breeze can repeat the in-progress candle across chunk boundaries.
        deduped: Dict[datetime, Dict[str, Any]] = {}
        for candle in raw:
            deduped[candle['date']] = candle
        candles = [deduped[k] for k in sorted(deduped)]

        if interval in ('week', 'month'):
            candles = _group_calendar(candles, interval)
        elif factor > 1:
            candles = _resample(candles, _BREEZE_BAR_SECONDS[breeze_interval] * factor)

        if oi:
            for candle in candles:
                candle.setdefault('oi', 0)

        with _HIST_LOCK:
            _HIST_CACHE[cache_key] = (candles, datetime.now())
            if len(_HIST_CACHE) > 500:
                _HIST_CACHE.pop(next(iter(_HIST_CACHE)))
        return candles

    def _history_chunk(self, info: Dict[str, Any], breeze_interval: str,
                       start: dt_date, end: dt_date) -> List[Dict[str, Any]]:
        kwargs = {
            'interval': breeze_interval,
            'from_date': f"{start.isoformat()}T00:00:00.000Z",
            'to_date': f"{end.isoformat()}T23:59:59.000Z",
            'stock_code': info['stock_code'],
            'exchange_code': info['exchange_code'],
            'product_type': info['product_type'],
            'expiry_date': info['expiry_date'] or '',
            'right': info['right'] or '',
            'strike_price': info['strike_price'] or '',
        }
        _rate_limiter.wait()
        try:
            resp = self.breeze.get_historical_data_v2(**kwargs)
        except Exception as exc:
            logger.error("[IciciAdapter] history %s [%s-%s] raised: %s",
                         info['symbol'], start, end, exc)
            _HIST_ERROR.msg = f"{type(exc).__name__}: {exc}"
            return []

        rows = _success(resp)
        if not rows:
            err = _error_of(resp)
            if err:
                logger.error("[IciciAdapter] history %s [%s-%s]: %s",
                             info['symbol'], start, end, err)
                _HIST_ERROR.msg = f"Breeze history error: {err}"
            else:
                logger.info("[IciciAdapter] no candles for %s [%s-%s] at %s",
                            info['symbol'], start, end, breeze_interval)
                _HIST_ERROR.msg = (f"ICICI has no {breeze_interval} candles for "
                                   f"{info['symbol']} between {start} and {end}")
            return []

        out: List[Dict[str, Any]] = []
        for row in rows:
            stamp = _parse_stamp(row.get('datetime') or row.get('date'))
            if stamp is None:
                continue
            entry: Dict[str, Any] = {
                'date': stamp,
                'open': _f(row.get('open')),
                'high': _f(row.get('high')),
                'low': _f(row.get('low')),
                'close': _f(row.get('close')),
                'volume': int(_f(row.get('volume'))),
            }
            # Present on derivatives only. Left absent rather than zero-filled
            # when Breeze omits it, matching the Fyers adapter — the
            # Change-in-OI histogram draws nothing for an absent key and a real
            # bar for a genuine zero.
            oi_val = _num(row, 'open_interest', 'oi', 'openInterest')
            if oi_val is not None:
                entry['oi'] = int(oi_val)
            out.append(entry)
        return out

    @staticmethod
    def last_history_error() -> Optional[str]:
        """Why the calling thread's most recent historical_data() came back empty."""
        return getattr(_HIST_ERROR, 'msg', None)


# ── Session verification ──────────────────────────────────────────────────

BREEZE_REST = "https://api.icicidirect.com/breezeapi/api/v1"


def verify_session(api_key: str, session_token: str,
                   timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """Return Breeze's customer details when the session token is live, else None.

    Used by the Brokers page to show ICICI as connected or expired, and it is
    the cheapest way to tell a dead daily token from a broken symbol mapping.

    Note the call shape: customerdetails is a **GET carrying a JSON body**,
    which is unusual enough to look like a bug. It is not — verified against
    the live API on 2026-09-03, where POST answers 405.
    """
    if not api_key or not session_token:
        return None
    try:
        import json
        import requests
        resp = requests.get(f"{BREEZE_REST}/customerdetails",
                            data=json.dumps({"SessionToken": session_token,
                                             "AppKey": api_key}),
                            headers={"Content-Type": "application/json"},
                            timeout=timeout)
        payload = resp.json()
    except Exception as exc:
        logger.warning("[IciciAdapter] customerdetails failed: %s", exc)
        return None

    details = payload.get('Success')
    if resp.status_code == 200 and payload.get('Status') == 200 and isinstance(details, dict):
        return details

    logger.info("[IciciAdapter] session token rejected: status=%s error=%s",
                payload.get('Status'), payload.get('Error'))
    return None


# ── Module-level helpers ──────────────────────────────────────────────────

def option_contract_id(root: str, strike: float, option_type: str, expiry: Union[str, dt_date]) -> str:
    """The id _resolve understands for one option contract, live or expired.

    'ICICI:NIFTY:2026-08-25:CE:24000'. It exists because the thing it addresses
    — a contract whose expiry has passed — is gone from every broker's symbol
    master within a day of expiring, so there is no tradingsymbol left to look
    up. Breeze never needs one: get_historical_data_v2 takes stock_code,
    expiry_date, right and strike as separate fields, and all four are in here.
    """
    exp = expiry.isoformat() if hasattr(expiry, 'isoformat') else str(expiry)[:10]
    return f"ICICI:{root.strip().upper()}:{exp}:{option_type.strip().upper()}:{int(float(strike))}"


def future_contract_id(root: str, expiry: Union[str, dt_date]) -> str:
    """The id _resolve understands for one index future, live or expired.

    'ICICI:NIFTY:2026-08-25:FUT'. Same reasoning as option_contract_id: a
    future is dropped from every broker's symbol master within a day of
    expiring too, so a past month's future volume (the Round Strike overlay,
    when the block is looking at a past option expiry) has no tradingsymbol
    left to look up either.
    """
    exp = expiry.isoformat() if hasattr(expiry, 'isoformat') else str(expiry)[:10]
    return f"ICICI:{root.strip().upper()}:{exp}:FUT"


def _breeze_expiry(day: dt_date) -> str:
    """Breeze wants an ISO instant, not a date: '2026-09-29T06:00:00.000Z'."""
    return f"{day.isoformat()}T06:00:00.000Z"


def _as_date(value: Any) -> Optional[dt_date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, dt_date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _num(row: Dict[str, Any], *keys: str) -> Optional[float]:
    """First of `keys` present in `row` with a numeric value."""
    for key in keys:
        if key in row and row[key] not in (None, '', '-'):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _success(resp: Any) -> List[Dict[str, Any]]:
    """Breeze answers {'Success': [...], 'Status': 200, 'Error': None}."""
    if not isinstance(resp, dict):
        return []
    rows = resp.get('Success') or resp.get('success') or []
    if isinstance(rows, dict):
        return [rows]
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _error_of(resp: Any) -> str:
    """The human-readable error Breeze attached to a response, or '' when
    there isn't one. A bare Status code (200 on a benign empty result, e.g.
    a window with no trading in it) is not an error and must not be reported
    as though it were — callers branch error-vs-benign on this being truthy.
    """
    if not isinstance(resp, dict):
        return str(resp)[:200]
    return str(resp.get('Error') or resp.get('error') or '')


def _parse_stamp(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=IST)
    if not raw:
        return None
    text = str(raw).strip().replace('T', ' ').replace('Z', '')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:len('2026-09-02 09:15:00.000000')], fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=IST)
    except ValueError:
        return None


def _quote_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Breeze quote/chain row → the Kite quote shape the callers expect."""
    ltp = _num(row, 'ltp', 'last_price', 'close') or 0.0
    return {
        'last_price': ltp,
        'ohlc': {
            'open': _num(row, 'open') or 0.0,
            'high': _num(row, 'high') or 0.0,
            'low': _num(row, 'low') or 0.0,
            'close': _num(row, 'previous_close', 'prev_close', 'close') or 0.0,
        },
        'volume': int(_num(row, 'total_quantity_traded', 'volume', 'ttq') or 0),
        'oi': int(_num(row, 'open_interest', 'oi', 'openInterest') or 0),
        'change_in_oi': int(_num(row, 'chnge_oi', 'change_in_oi', 'oi_change') or 0),
        'timestamp': datetime.now(),
    }


def _bucket_start(stamp: datetime, bar_seconds: int) -> datetime:
    """Floor a timestamp to its bar, anchored on the 09:15 session open."""
    anchor = stamp.replace(hour=_SESSION_OPEN[0], minute=_SESSION_OPEN[1],
                           second=0, microsecond=0)
    if stamp < anchor:            # pre-open ticks keep their own bar
        anchor = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((stamp - anchor).total_seconds())
    return anchor + timedelta(seconds=(elapsed // bar_seconds) * bar_seconds)


def _resample(candles: List[Dict[str, Any]], bar_seconds: int) -> List[Dict[str, Any]]:
    """Aggregate finer candles into bar_seconds bars (OHLC/volume/OI)."""
    buckets: Dict[datetime, Dict[str, Any]] = {}
    for candle in candles:
        start = _bucket_start(candle['date'], bar_seconds)
        bar = buckets.get(start)
        if bar is None:
            bar = dict(candle)
            bar['date'] = start
            buckets[start] = bar
            continue
        bar['high'] = max(bar['high'], candle['high'])
        bar['low'] = min(bar['low'], candle['low'])
        bar['close'] = candle['close']
        bar['volume'] = bar.get('volume', 0) + candle.get('volume', 0)
        # OI is a level, not a flow — the bar carries its closing value.
        if 'oi' in candle:
            bar['oi'] = candle['oi']
    return [buckets[k] for k in sorted(buckets)]


def _group_calendar(candles: List[Dict[str, Any]], interval: str) -> List[Dict[str, Any]]:
    """Group daily candles into weekly or monthly bars."""
    buckets: Dict[Any, Dict[str, Any]] = {}
    for candle in candles:
        day = candle['date']
        if interval == 'week':
            key = (day.isocalendar()[0], day.isocalendar()[1])
        else:
            key = (day.year, day.month)
        bar = buckets.get(key)
        if bar is None:
            buckets[key] = dict(candle)
            continue
        bar['high'] = max(bar['high'], candle['high'])
        bar['low'] = min(bar['low'], candle['low'])
        bar['close'] = candle['close']
        bar['volume'] = bar.get('volume', 0) + candle.get('volume', 0)
        if 'oi' in candle:
            bar['oi'] = candle['oi']
    return [buckets[k] for k in sorted(buckets)]


def _trim_to_atm(chain: Dict[tuple, Dict], strikecount: int) -> Dict[tuple, Dict]:
    """Keep `strikecount` strikes either side of the ladder's midpoint."""
    strikes = sorted({k[0] for k in chain})
    if len(strikes) <= strikecount * 2:
        return chain
    mid = strikes[len(strikes) // 2]
    lo, hi = mid - strikecount * _step(strikes), mid + strikecount * _step(strikes)
    return {k: v for k, v in chain.items() if lo <= k[0] <= hi}


def _step(strikes: List[int]) -> int:
    """Smallest gap between adjacent strikes — the ladder's step."""
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(gaps) if gaps else 50
