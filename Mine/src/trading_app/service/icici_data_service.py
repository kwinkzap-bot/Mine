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

Status: exercised against a live Breeze session on 2026-09-05 across cash,
index and derivative symbols at every interval the backtest routes ask for.
Two things that session established, both handled below: Breeze truncates any
request over 1000 rows to its LAST 1000 without saying so (see
_BREEZE_MAX_ROWS), and it pads intraday series either side of the trading
session with filler bars Kite and Fyers do not return (see _in_session).
"""

import logging
import os
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

# Per-CHUNK cache for windows that have already closed.
#
# _HIST_CACHE above is keyed by the whole requested range and expires in 15s, so
# two overlapping requests share nothing and a repeat of the same one re-fetches
# everything. That is fine for a few days; it is not fine for a long window.
# 1-minute data chunks at 2 days (_CHUNK_DAYS), so three months is 47 chunks,
# and Breeze is paced at 1.5 req/s — every load spends ~30s in the pacer before
# a single byte of network time, competing with the rest of the app for the same
# budget. That is what makes a long-window chart look like it has hung.
#
# A chunk that ends before today can never change, so it is cached on its own
# key and reused across ranges: re-opening the same window, or moving the as-of
# date by a day, then costs one live chunk instead of 47. The chunk containing
# today is always re-fetched — its last bar is still forming.
#
# Bounded by candle count rather than entry count: chunks hold wildly different
# numbers of bars (750 for 2 days of 1-minute, a handful for 1-day bars), so
# capping entries would bound memory very poorly. ~150k candles is roughly three
# full three-month 1-minute windows.
_CHUNK_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_CHUNK_CACHE_MAX_CANDLES = 150_000
_chunk_cache_candles = 0
_CHAIN_CACHE: Dict[str, Tuple[Dict[Any, Dict], datetime]] = {}
_CHAIN_LOCK = threading.Lock()
_HIST_ERROR = threading.local()

# Kite instrument tokens / Kite index names → the canonical Fyers-style symbol,
# so a caller still holding a Kite token gets translated the same way the Fyers
# adapter translates it.
from trading_app.service.fyers_data_service import (  # noqa: E402  (cycle-free: no reverse import)
    _KITE_TO_FYERS_INDICES,
    FyersDataServiceAdapter,
)

# Breeze intervals: 1second, 1minute, 5minute, 30minute, 1day. Everything else
# the app asks for is built by aggregating the next-finest one that divides it.
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
# ask for in a single request. Breeze truncates long windows silently, so the
# chunk sizes keep every request well under ~1000 candles.
_BREEZE_BAR_SECONDS = {'1second': 1, '1minute': 60, '5minute': 300,
                       '30minute': 1800, '1day': 86400}
# 1second is absent on purpose: it is windowed inside the day instead.
_CHUNK_DAYS = {'1minute': 2, '5minute': 10, '30minute': 60, '1day': 500}

# NSE/BSE cash and derivatives both open at 09:15; anchoring the aggregation
# there is what makes a 15-minute bar here line up with Kite's and with the
# backtest engines' bars. An anchor of midnight would shift every odd
# multiple (3, 15, 2h) by 15 minutes and silently change signal candles.
_SESSION_OPEN = (9, 15)
_SESSION_CLOSE = (15, 30)

# Breeze pads its intraday series either side of the session: a flat
# O=H=L=C row from ~09:05 (pre-open), gaps in the closing minutes, and a
# 15:31 row carrying the whole closing-auction volume — sometimes with the
# preceding bar's volume written NEGATIVE. Kite and Fyers both stop at the
# session, so leaving the padding in makes the same backtest return a
# different bar count, and a different "2nd candle of the day", depending
# only on which broker served it. Bars are kept on their START stamp, so a
# kept day runs 09:15..15:29 at 1-minute and 09:15..15:25 at 5-minute —
# byte-for-byte the window Fyers returns.
_SESSION_OPEN_MINUTE = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
_SESSION_CLOSE_MINUTE = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]

# get_historical_data_v2 answers with at most 1000 rows and, when the window
# holds more, returns the LAST 1000 silently — no error, no truncation flag.
# Verified 2026-09-05: one full day of NIFTY 1-second came back as
# 15:23:20..15:39:59, i.e. the final 16m40s of the session presented as the
# whole day. Every other interval stays under the cap at the chunk sizes in
# _CHUNK_DAYS (two days of 1-minute is ~780 rows), so only the 1-second base
# has to be windowed — see _second_history.
_BREEZE_MAX_ROWS = 1000
_SECOND_WINDOW_SECONDS = 900

# One trading day of 1-second data is 25 windows, i.e. 25 requests, against an
# app-wide Breeze budget of 5000/day that the live algos share.
#
# This used to be a hard 5-day LOOKBACK clip, which meant a 30-second backtest
# silently saw the last 5 days of whatever range was asked for — a year-long
# range and a week-long one returned the same three trading days. What actually
# needs protecting is the request budget, not the calendar, so the cap is now
# on requests: the range is walked NEWEST-FIRST and stops when the budget for
# this call is spent, and the caller learns where it stopped from
# last_history_error(). Settled days are cached at the aggregated bar size, so
# re-running the same range picks up where the last one left off.
#
# 800 requests ≈ 32 fresh trading days per call. Raise ICICI_SECOND_MAX_REQUESTS
# to pull more in one go; 0 means no limit (a multi-year 30-second range can
# then spend the whole day's Breeze budget, which the live algos share).
_SECOND_MAX_REQUESTS = max(0, int(os.getenv('ICICI_SECOND_MAX_REQUESTS', '800') or 0))
_SECOND_DAY_REQUESTS = 25


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

    def _resolve(self, symbol: Union[int, str]) -> Optional[Dict[str, Any]]:
        """Translate one app symbol into the pieces Breeze needs.

        Returns a dict with root / exchange_code / product_type / expiry_date /
        right / strike_price, or None when the symbol cannot be placed.
        """
        sym = str(symbol).strip()
        sym = _KITE_TO_FYERS_INDICES.get(sym, sym)
        upper = sym.upper()

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
        return self._history_for_info(info, from_date, to_date, interval, oi,
                                      use_cache, cache_ttl)

    def historical_option(self, root: str, expiry: dt_date, strike: float,
                          option_type: str, from_date: str, to_date: str,
                          interval: str, exchange_code: str = 'NFO',
                          oi: bool = False, use_cache: bool = True,
                          cache_ttl: Optional[float] = None) -> List[Dict[str, Any]]:
        """Candles for one option contract named by (root, expiry, strike, type).

        The reason this exists alongside historical_data: Breeze addresses a
        contract by its FIELDS, not by an instrument token, so it will serve an
        already-expired series. Every token-based path in this app goes through a
        broker instrument master, and both masters drop expired rows (Kite's
        get_option_symbol filters `expiry >= today`, Fyers' find_option_symbol
        the same), which is why a past expiry is unreachable through them —
        _resolve above would fail on the symbol lookup before Breeze was ever
        asked. Building the request fields directly skips that dead end.

        Verified against Breeze on 2026-09-04: NIFTY 24050 CE expiring
        2026-09-01 returned full 5-minute OHLC + volume + OI both on its expiry
        day and mid-life, as did a 5-week-old July expiry.
        """
        code = master.stock_code(root, exchange_code)
        if not code:
            _HIST_ERROR.msg = f"No ICICI stock_code for {root} on {exchange_code}"
            return []
        right = 'call' if str(option_type).upper() in ('CE', 'CALL') else 'put'
        info = {
            'root': (root or '').upper(),
            'stock_code': code,
            'exchange_code': exchange_code.upper(),
            'product_type': 'options',
            'expiry_date': _breeze_expiry(expiry),
            'right': right,
            'strike_price': str(int(float(strike))),
            'symbol': f"{root}:{expiry.isoformat()}:{int(float(strike))}:{right}",
        }
        _HIST_ERROR.msg = None
        return self._history_for_info(info, from_date, to_date, interval, oi,
                                      use_cache, cache_ttl)

    def historical_future(self, root: str, expiry: dt_date, from_date: str, to_date: str,
                          interval: str, exchange_code: str = 'NFO',
                          use_cache: bool = True,
                          cache_ttl: Optional[float] = None) -> List[Dict[str, Any]]:
        """Candles for one FUTURES contract named by (root, expiry).

        The futures twin of historical_option, and it exists for the same
        reason: a August future is gone from the instrument masters by
        September, so a token lookup cannot reach it. Replaying August volume
        needs the contract that was actually trading in August, not whichever
        month happens to be the front one today.
        """
        code = master.stock_code(root, exchange_code)
        if not code:
            _HIST_ERROR.msg = f"No ICICI stock_code for {root} on {exchange_code}"
            return []
        info = {
            'root': (root or '').upper(),
            'stock_code': code,
            'exchange_code': exchange_code.upper(),
            'product_type': 'futures',
            'expiry_date': _breeze_expiry(expiry),
            'right': None,
            'strike_price': None,
            'symbol': f"{root}:{expiry.isoformat()}:FUT",
        }
        _HIST_ERROR.msg = None
        return self._history_for_info(info, from_date, to_date, interval, False,
                                      use_cache, cache_ttl)

    def _history_for_info(self, info: Dict[str, Any], from_date: str, to_date: str,
                          interval: str, oi: bool = False, use_cache: bool = True,
                          cache_ttl: Optional[float] = None) -> List[Dict[str, Any]]:
        """Fetch + chunk + resample + cache, for an already-resolved contract."""
        if interval not in _INTERVAL_MAP:
            _HIST_ERROR.msg = f"Unsupported interval {interval!r}"
            logger.error("[IciciAdapter] unsupported interval %r", interval)
            return []
        breeze_interval, factor = _INTERVAL_MAP[interval]

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
        if breeze_interval == '1second':
            # Aggregate each day to the target bar as it arrives. Holding the
            # raw 1-second rows for the whole range instead — 22,500 a day —
            # is what made a wide 30-second range a memory problem, and this is
            # a live trading process.
            bar_seconds = (_BREEZE_BAR_SECONDS['1second'] * factor) if factor > 1 else None
            raw, stopped_at = self._second_history(info, fd, td, bar_seconds)
            if stopped_at is not None:
                _HIST_ERROR.msg = (
                    f"ICICI {interval} reaches back to {stopped_at}; {from_date} was "
                    f"requested. One trading day of 1-second data costs "
                    f"{_SECOND_DAY_REQUESTS} Breeze requests out of the app-wide "
                    f"5,000/day the live algos share, so this call stopped at its "
                    f"{_SECOND_MAX_REQUESTS}-request budget. Days already fetched are "
                    f"cached — re-run to walk further back, or raise "
                    f"ICICI_SECOND_MAX_REQUESTS.")
                logger.warning("[IciciAdapter] %s", _HIST_ERROR.msg)
            return self._finish_history(raw, info, cache_key, fd, td, interval,
                                        # already aggregated above when bar_seconds is set
                                        breeze_interval, 1 if bar_seconds else factor,
                                        oi, use_cache)

        step = _CHUNK_DAYS.get(breeze_interval, 5)
        chunk = timedelta(days=step)
        # Chunk boundaries sit on a FIXED grid (every `step` days since the
        # ordinal epoch), not on wherever this particular range happens to start.
        # Anchoring them to `fd` gave every range its own boundaries, so two
        # windows that overlap by three months still shared no cache keys at all
        # — moving the as-of date by a single day re-fetched everything. On a
        # grid, any two ranges at the same interval line up. The cost is that the
        # first chunk can begin up to `step - 1` days before `fd`; the trim below
        # drops that overshoot.
        cursor = dt_date.fromordinal(fd.toordinal() - (fd.toordinal() % step))
        today = datetime.now(IST).date()
        while cursor <= td:
            chunk_end = min(cursor + chunk - timedelta(days=1), td)
            # Settled = wholly in the past, so its bars are final. Anything
            # touching today still has a forming bar and is always re-fetched.
            settled = chunk_end < today
            ckey = f"{info['symbol']}:{breeze_interval}:{cursor}:{chunk_end}"
            part = _chunk_cache_get(ckey) if settled else None
            if part is None:
                part = self._history_chunk(info, breeze_interval, cursor, chunk_end)
                if settled and part:
                    _chunk_cache_put(ckey, part)
            raw.extend(part)
            cursor = chunk_end + timedelta(days=1)

        return self._finish_history(raw, info, cache_key, fd, td, interval,
                                    breeze_interval, factor, oi, use_cache)

    def _finish_history(self, raw: List[Dict[str, Any]], info: Dict[str, Any],
                        cache_key: str, fd: dt_date, td: dt_date, interval: str,
                        breeze_interval: str, factor: int, oi: bool,
                        use_cache: bool) -> List[Dict[str, Any]]:
        """Dedupe, trim, aggregate and cache what the chunk loop collected.

        Shared by both fetch paths — the day-chunked one above and the
        intra-day windowed one 1-second history needs."""
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

        # Trim the grid overshoot at the left edge back to what was asked for, so
        # aligning the chunks stays invisible to callers.
        candles = [c for c in candles if fd <= _as_date(c['date']) <= td]

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

    def _second_history(self, info: Dict[str, Any], fd: dt_date, td: dt_date,
                        bar_seconds: Optional[int] = None
                        ) -> Tuple[List[Dict[str, Any]], Optional[dt_date]]:
        """1-second bars, fetched in windows small enough to clear the row cap.

        `bar_seconds` aggregates each day to that bar before it is kept (and
        cached), which is what lets a wide range run at all: 750 30-second bars
        a day instead of 22,500 1-second rows.

        Returns (candles, stopped_at) — stopped_at is the oldest day actually
        fetched when the request budget ran out before reaching `fd`, else None.
        Days are walked NEWEST-FIRST so a budget-limited call returns the most
        recent stretch of the range rather than an arbitrary middle.

        Unlike the day-chunked path, a window is cached as soon as its END is
        in the past rather than only once the whole day is: the live 30-second
        algos re-ask for "today so far" every few seconds, and re-fetching all
        25 of today's windows each time would spend the app's entire Breeze
        budget before lunch. Only the window still forming is ever re-fetched.
        """
        now = datetime.now(IST)

        days = []
        day = fd
        while day <= td:
            if day.weekday() < 5:      # Breeze answers weekends with nothing
                days.append(day)
            day += timedelta(days=1)

        out: List[Dict[str, Any]] = []
        spent = 0
        oldest_served: Optional[dt_date] = None
        stopped_at: Optional[dt_date] = None
        for day in reversed(days):
            settled_day = day < now.date()
            # A settled day at a fixed bar size is final — cache it whole, so a
            # re-run of the same range costs nothing for the days it already
            # walked. Keyed by bar size: 30-second bars must never answer a
            # request for another interval built off the same 1-second base.
            dkey = f"{info['symbol']}:{bar_seconds or 1}s:{day}" if bar_seconds else None
            cached = _chunk_cache_get(dkey) if (dkey and settled_day) else None
            if cached is not None:
                out.extend(cached)
                oldest_served = day
                continue

            windows = [w for w in _session_windows(day) if w[0] <= now]
            wkey = lambda w: (f"{info['symbol']}:1second:"
                              f"{w[0]:%Y-%m-%dT%H:%M}:{w[1]:%H:%M}")
            # Fetching half a day would cache a day-shaped hole, so a day is
            # either affordable whole or left for the next run.
            todo = sum(1 for w in windows
                       if not (w[1] < now and _chunk_cache_has(wkey(w))))
            if _SECOND_MAX_REQUESTS and todo and spent + todo > _SECOND_MAX_REQUESTS:
                # Name the oldest day actually served, not the one we stopped
                # before — "reaches back to <a Sunday>" helps nobody.
                stopped_at = oldest_served or day
                break

            day_rows: List[Dict[str, Any]] = []
            for w_start, w_end in windows:
                settled = w_end < now
                ckey = wkey((w_start, w_end))
                part = _chunk_cache_get(ckey) if settled else None
                if part is None:
                    part = self._history_chunk(info, '1second', w_start, w_end)
                    spent += 1
                    # An empty window is cached only when Breeze answered
                    # and had nothing (a holiday), never when the request
                    # failed — caching a failure would make it permanent.
                    #
                    # On a settled day being aggregated, the day cache below
                    # holds the same information at 1/30th the size, and
                    # caching both would evict the aggregated days (which is
                    # what makes a re-run cheap) under the candle cap. Today's
                    # windows are still cached individually: that is what keeps
                    # the live 30-second algos from re-fetching the whole
                    # session on every poll.
                    if (settled and not (settled_day and bar_seconds)
                            and (part or getattr(_HIST_ERROR, 'clean_empty', False))):
                        _chunk_cache_put(ckey, part)
                day_rows.extend(part)

            if bar_seconds:
                day_rows = _resample(day_rows, bar_seconds)
                if settled_day and dkey:
                    _chunk_cache_put(dkey, day_rows)
            if day_rows:
                oldest_served = day
            out.extend(day_rows)

        # Walked newest-first; _finish_history sorts, but return it in order
        # anyway so a direct caller is not handed a reversed series.
        out.sort(key=lambda c: c['date'])
        return out, stopped_at

    def _history_chunk(self, info: Dict[str, Any], breeze_interval: str,
                       start: Union[dt_date, datetime],
                       end: Union[dt_date, datetime]) -> List[Dict[str, Any]]:
        """One Breeze request. Dates cover the whole day; datetimes are taken
        as the exact IST wall-clock bounds (what _second_history needs)."""
        # datetime is a subclass of date, so it has to be tested first.
        from_stamp = (start.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                      if isinstance(start, datetime)
                      else f"{start.isoformat()}T00:00:00.000Z")
        to_stamp = (end.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    if isinstance(end, datetime)
                    else f"{end.isoformat()}T23:59:59.000Z")
        kwargs = {
            'interval': breeze_interval,
            'from_date': from_stamp,
            'to_date': to_stamp,
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
        _HIST_ERROR.clean_empty = False
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
                # Breeze answered, the window simply holds nothing — a market
                # holiday, or a contract that had not started trading yet.
                _HIST_ERROR.clean_empty = True
            return []

        if len(rows) >= _BREEZE_MAX_ROWS and breeze_interval != '1second':
            # 1-second is windowed to stay clear of the cap; anything else
            # hitting it means _CHUNK_DAYS has grown past what Breeze will
            # answer and the caller is being handed a silently clipped tail.
            logger.error("[IciciAdapter] %s [%s-%s] at %s returned the %d-row cap "
                         "— older bars in this chunk were dropped by Breeze",
                         info['symbol'], start, end, breeze_interval, _BREEZE_MAX_ROWS)

        intraday = breeze_interval != '1day'
        out: List[Dict[str, Any]] = []
        for row in rows:
            stamp = _parse_stamp(row.get('datetime') or row.get('date'))
            if stamp is None:
                continue
            if intraday and not _in_session(stamp):
                continue
            entry: Dict[str, Any] = {
                'date': stamp,
                'open': _f(row.get('open')),
                'high': _f(row.get('high')),
                'low': _f(row.get('low')),
                'close': _f(row.get('close')),
                # Breeze writes the closing-auction quantity as a NEGATIVE on the
                # bar before it (SBIN, 2026-09-04 15:28: -6,305,822). A negative
                # goes straight into VWAP's price x volume sum and into any
                # volume filter, so floor it at zero rather than propagate it.
                'volume': max(0, int(_f(row.get('volume')))),
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

def _breeze_expiry(day: dt_date) -> str:
    """Breeze wants an ISO instant, not a date: '2026-09-29T06:00:00.000Z'."""
    return f"{day.isoformat()}T06:00:00.000Z"


def _chunk_cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    """Copy of a cached settled chunk, or None.

    Copied on the way out because callers downstream dedupe, resample and
    setdefault('oi', ...) over these dicts — handing out the cached objects
    themselves would let one caller's post-processing rewrite what the next one
    reads."""
    with _HIST_LOCK:
        hit = _CHUNK_CACHE.get(key)
        return [dict(c) for c in hit] if hit is not None else None


def _chunk_cache_has(key: str) -> bool:
    """Is this chunk cached? Cheaper than _chunk_cache_get when the answer is
    all the caller wants — get() copies every candle on the way out."""
    with _HIST_LOCK:
        return key in _CHUNK_CACHE


def _chunk_cache_put(key: str, candles: List[Dict[str, Any]]) -> None:
    global _chunk_cache_candles
    with _HIST_LOCK:
        if key in _CHUNK_CACHE:
            return
        _CHUNK_CACHE[key] = [dict(c) for c in candles]
        _chunk_cache_candles += len(candles)
        # Oldest-first eviction (dicts keep insertion order), by candle count.
        while _chunk_cache_candles > _CHUNK_CACHE_MAX_CANDLES and _CHUNK_CACHE:
            oldest = next(iter(_CHUNK_CACHE))
            _chunk_cache_candles -= len(_CHUNK_CACHE.pop(oldest))


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
    if not isinstance(resp, dict):
        return str(resp)[:200]
    return str(resp.get('Error') or resp.get('error') or resp.get('Status') or '')


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


def _in_session(stamp: datetime) -> bool:
    """True for a bar whose START falls inside the 09:15-15:30 trading session."""
    return _SESSION_OPEN_MINUTE <= stamp.hour * 60 + stamp.minute < _SESSION_CLOSE_MINUTE


def _session_windows(day: dt_date) -> List[Tuple[datetime, datetime]]:
    """`day`'s session sliced into _SECOND_WINDOW_SECONDS request windows."""
    open_at = datetime(day.year, day.month, day.day,
                       _SESSION_OPEN[0], _SESSION_OPEN[1], tzinfo=IST)
    close_at = datetime(day.year, day.month, day.day,
                        _SESSION_CLOSE[0], _SESSION_CLOSE[1], tzinfo=IST)
    windows = []
    cursor = open_at
    while cursor < close_at:
        # Breeze treats to_date as inclusive, so stop one second short of the
        # next window's first bar instead of fetching it twice.
        end = min(cursor + timedelta(seconds=_SECOND_WINDOW_SECONDS), close_at)
        windows.append((cursor, end - timedelta(seconds=1)))
        cursor = end
    return windows


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
