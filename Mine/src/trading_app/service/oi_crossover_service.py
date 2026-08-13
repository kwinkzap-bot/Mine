"""OI Crossover scanner — tracks when a symbol's cumulative CE and PE
open-interest-change lines cross each other during the session.

What "crossover" means here
---------------------------
Every symbol carries two intraday series, both measured from the 9:15 open:

    ce_chg(t) = total call OI now  -  total call OI at open
    pe_chg(t) = total put  OI now  -  total put  OI at open

A crossover is the moment ``pe_chg - ce_chg`` changes sign. Puts pulling
ahead of calls is BULL (writers are stacking puts below the market); calls
pulling ahead is BEAR. Note this is about the *change* lines, not the OI
levels — a symbol can sit at PCR 0.61 (call-heavy) and still be BULL because
puts added more OI today. Levels tell you where the book already is; the
crossing change-lines tell you where today's money went.

Why its own table rather than ``oi_history``
--------------------------------------------
``oi_history`` is the high-fidelity per-minute store for the three index
chains that feed the dashboard PCR tab and the live chain. This scanner
sweeps ~215 symbols on a slower cadence and only ever needs six numbers per
symbol, so it writes ``oi_crossover_series`` instead of diluting a table
other pages depend on with far coarser rows.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from trading_app.app.utils.logger import logger

# Indices are scanned alongside the F&O stock list. Fyers wants the index
# underlying symbol, not "NSE:<root>-EQ" — same mapping the OI chain service
# uses, kept here so the scanner has no import-time dependency on it.
INDEX_UNDERLYINGS: Dict[str, str] = {
    'NIFTY':      'NSE:NIFTY50-INDEX',
    'BANKNIFTY':  'NSE:NIFTYBANK-INDEX',
    'FINNIFTY':   'NSE:FINNIFTY-INDEX',
    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
    'SENSEX':     'BSE:SENSEX-INDEX',
    'BANKEX':     'BSE:BANKEX-INDEX',
}

# Strikes each side of ATM. 25 is what the first full sweep of the F&O list
# ran at (201 of 215 symbols returned a usable chain); leave it there rather
# than widening on a hunch, since a wider chain only adds deep-OTM strikes
# whose OI barely moves.
STRIKE_COUNT = 25

# Fyers reports an expired or missing access token per-request, so a dead
# token looks like 215 individual symbol failures rather than one auth error.
# Matching on the message is the only signal available.
_AUTH_HINTS = ('valid token', 'token', 'unauthor', 'authenticat', 'invalid app')

# Sector tags come from the index-constituents cache the rest of the app
# already refreshes. Keys are the cache's index names; values are the label
# shown in the Sector dropdown.
SECTOR_INDEXES: Dict[str, str] = {
    'NIFTY':          'NIFTY',
    'BANKNIFTY':      'BANKNIFTY',
    'FINNIFTY':       'FINNIFTY',
    'MIDCPNIFTY':     'MIDCAP',
    'NIFTY AUTO':     'AUTO',
    'NIFTY FMCG':     'FMCG',
    'NIFTY IT':       'IT',
    'NIFTY METAL':    'METAL',
    'NIFTY PHARMA':   'PHARMA',
    'NIFTY PSU BANK': 'PSU BANK',
}

# A chain fetch that returns fewer strikes than this is treated as junk —
# illiquid or half-listed contracts produce one-sided totals that fake a cross.
MIN_STRIKES = 6

# The app-wide Fyers limiter paces 8 req/s, which optionchain tolerates for
# about 25 seconds before returning 429 "request limit reached" — the endpoint
# also carries a per-minute quota of roughly 200. Bursting the full universe at
# 8/s therefore lands ~200 symbols and starves the rest, and since the universe
# is alphabetical it starves the *same* tail every scan: TITAN through
# ZYDUSLIFE would never once be recorded. Pacing to ~2.5/s puts the whole
# sweep at ~150 requests/minute, finishing a 215-symbol scan in ~90s — inside
# the 3-minute cadence, and leaving most of the shared budget for the algos.
SCAN_REQUESTS_PER_SEC = 2.5

# 429s can still happen when something else is using the broker at the same
# time. Failed symbols get one more attempt after the quota window rolls over.
RETRY_PAUSE_SEC = 20.0

_BASEDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
DB_PATH = os.path.join(_BASEDIR, 'oi_data.db')

# Historical mode only offers sessions from here on. Everything earlier in the
# table came from backfill_from_oi_history(), which can only reconstruct the
# index chains at a different resolution than the live scanner — offering those
# days next to real ones invites comparing two different things.
HISTORY_FLOOR_DATE = '2026-08-01'
_CONSTITUENTS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '.cache', 'index_constituents.json')
)

# A paced sweep runs ~110s against a 3-minute cadence, and the manual /scan
# endpoint can fire at any moment. Two sweeps at once would double the request
# rate into the same 429 wall they are pacing to avoid, so a second one steps
# aside rather than queueing behind the first.
_SCAN_LOCK = threading.Lock()

# symbol -> (fetched_at, quote). Shared across requests. Short-lived on
# purpose: the quote carries a tradable premium, not just a strike, so it is
# refreshed on roughly the grid's own cadence rather than the scanner's.
_ATM_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_ATM_CACHE_TTL = 20.0  # see atm_quotes()

# symbol -> lot size (None when unknown). Held for the process: a lot size
# changes on an exchange revision, not intraday.
_LOT_SIZE_CACHE: Dict[str, Optional[int]] = {}

# The scanner's own session window. The scheduler already guards its job, but
# the /scan endpoint and any script call scan() directly, and a sweep run when
# the market is shut writes a full set of frozen rows that look exactly like
# live ones — the weekend runs on 8 and 9 Aug 2026 did precisely that. The gate
# belongs here, where every caller passes through it.
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 40)


def market_session_state(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """(is a live session, reason it isn't). Weekday + clock only.

    NSE trading holidays are not covered — the app has no holiday calendar,
    and inventing one risks silently skipping a real trading day. The frozen
    feed check in ``scan`` catches holidays from the data instead, which also
    covers exchange outages and stale feeds that no calendar would predict.
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False, 'weekend'
    if now.time() < MARKET_OPEN:
        return False, 'before the open'
    if now.time() > MARKET_CLOSE:
        return False, 'after the close'
    return True, ''


def _today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


class OICrossoverService:
    """Scans the F&O universe for CE/PE OI-change crossovers."""

    def __init__(self, provider=None):
        from trading_app.service.provider_logic import get_data_provider
        self.provider = provider or get_data_provider()
        self.db_path = DB_PATH
        self._sector_map: Optional[Dict[str, List[str]]] = None
        self._future_symbols: Optional[Dict[str, str]] = None
        self.last_error: Optional[str] = None
        self._init_db()

    @staticmethod
    def underlying_for(symbol: str) -> str:
        """Fyers underlying symbol for a scanner symbol."""
        return INDEX_UNDERLYINGS.get(symbol, f'NSE:{symbol}-EQ')

    # ── futures price ────────────────────────────────────────────────

    def future_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Nearest-expiry futures LTP for each symbol.

        The chart plots price against the two OI-change lines, and futures are
        what the OI actually belongs to — spot and futures diverge by the basis,
        which widens toward expiry exactly when the OI story matters most.

        Cheap despite the breadth: ``find_future_symbol`` reads the hourly
        instrument cache rather than the API, and ``quote`` batches 50 symbols
        per request, so the whole universe costs about five calls. Resolution
        is memoised for the process because the nearest contract only changes
        at expiry.
        """
        provider = self.provider
        if provider is None or not hasattr(provider, 'quote'):
            return {}

        if self._future_symbols is None:
            self._future_symbols = {}
            for symbol in symbols:
                try:
                    fut = provider.find_future_symbol(symbol)
                except Exception as e:
                    logger.debug(f"[OIX] No futures contract for {symbol}: {e}")
                    fut = None
                if fut:
                    self._future_symbols[symbol] = fut

        wanted = {s: f for s, f in self._future_symbols.items() if s in set(symbols)}
        if not wanted:
            return {}

        try:
            quotes = provider.quote(list(wanted.values())) or {}
        except Exception as e:
            logger.warning(f"[OIX] Futures quote fetch failed: {e}")
            return {}

        out: Dict[str, float] = {}
        for symbol, fut in wanted.items():
            q = quotes.get(fut) or {}
            price = q.get('last_price')
            if price:
                out[symbol] = float(price)
        logger.info(f"[OIX] Futures prices for {len(out)}/{len(wanted)} symbols")
        return out

    # ── storage ──────────────────────────────────────────────────────

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('''
                    CREATE TABLE IF NOT EXISTS oi_crossover_series (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_date TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        spot REAL,
                        ce_oi INTEGER,
                        pe_oi INTEGER,
                        ce_chg INTEGER,
                        pe_chg INTEGER,
                        pcr REAL
                    )
                ''')
                c.execute('''CREATE INDEX IF NOT EXISTS idx_xseries_day_sym
                             ON oi_crossover_series (trade_date, symbol, ts)''')
                # Added after the first sessions were recorded, so existing
                # rows keep NULL here and the chart falls back to spot.
                cols = {r[1] for r in c.execute('PRAGMA table_info(oi_crossover_series)')}
                if 'fut_price' not in cols:
                    c.execute('ALTER TABLE oi_crossover_series ADD COLUMN fut_price REAL')
                c.execute('''
                    CREATE TABLE IF NOT EXISTS oi_crossover_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        ce_chg INTEGER,
                        pe_chg INTEGER,
                        UNIQUE (trade_date, symbol, ts)
                    )
                ''')
                c.execute('''CREATE INDEX IF NOT EXISTS idx_xevents_day_sym
                             ON oi_crossover_events (trade_date, symbol, ts)''')
                # The market at the instant the lines crossed. Added after the
                # first sessions were recorded: a cross that fired at 9:30 is a
                # statement about 9:30's prices, and the screen was showing
                # whatever the chain said at render time instead. Older events
                # keep NULL — snapshot() recovers their spot from the series
                # row at the same ts, but no premium was ever stored, so those
                # rows show a dash rather than a price from the wrong moment.
                ev_cols = {r[1] for r in c.execute('PRAGMA table_info(oi_crossover_events)')}
                for col in ('spot', 'atm_strike', 'ce_ltp', 'pe_ltp'):
                    if col not in ev_cols:
                        c.execute(f'ALTER TABLE oi_crossover_events ADD COLUMN {col} REAL')
                # The *rating* of the cross, frozen at the instant it fired.
                # Previously the screen re-derived quality and OI-change-% from
                # the newest scan and filtered on those, so a signal that had
                # not changed drifted in and out of the table all day — and
                # because oi_chg_pct is measured against the 9:15 open it can
                # only ripen late, which put the median cross on screen more
                # than three hours after it fired, priced at a premium from
                # three hours earlier. A cross is a statement about one moment;
                # these columns are that moment, so filter membership can never
                # move again. The live values still travel on the row, but as
                # decay information rather than as the gate.
                for col, decl in (('ce_oi', 'INTEGER'), ('pe_oi', 'INTEGER'),
                                  ('quality', 'TEXT'), ('oi_chg_pct', 'REAL'),
                                  ('separation', 'REAL'),
                                  ('confirm_state', 'INTEGER')):
                    if col not in ev_cols:
                        c.execute(
                            f'ALTER TABLE oi_crossover_events ADD COLUMN {col} {decl}')
                # Every scan attempt, good or bad. Without this a broker-auth
                # failure is indistinguishable from "the market hasn't opened
                # yet" — both leave the series table untouched, and the screen
                # would claim no scan had run when in fact 215 of them failed.
                c.execute('''
                    CREATE TABLE IF NOT EXISTS oi_crossover_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_date TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        scanned INTEGER,
                        failed INTEGER,
                        crossovers INTEGER,
                        elapsed_sec REAL,
                        error TEXT
                    )
                ''')
                c.execute('''CREATE INDEX IF NOT EXISTS idx_xruns_day
                             ON oi_crossover_runs (trade_date, ts)''')
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] DB init failed: {e}", exc_info=True)

    # ── universe & metadata ──────────────────────────────────────────

    def universe(self) -> List[str]:
        """Indices first, then the cached F&O stock list."""
        symbols = list(INDEX_UNDERLYINGS.keys())
        try:
            from trading_app.filters.stock_list_store import get_fo_stocks
            stocks = get_fo_stocks(self.provider) or []
        except Exception as e:
            logger.error(f"[OIX] Could not load F&O list: {e}")
            stocks = []
        seen = set(symbols)
        for s in stocks:
            if s not in seen:
                seen.add(s)
                symbols.append(s)
        return symbols

    def sectors_for(self, symbol: str) -> List[str]:
        if self._sector_map is None:
            self._sector_map = {}
            try:
                with open(_CONSTITUENTS_PATH) as f:
                    data = json.load(f).get('constituents', {})
                for index_name, label in SECTOR_INDEXES.items():
                    for sym in data.get(index_name, []):
                        self._sector_map.setdefault(sym, []).append(label)
            except Exception as e:
                logger.warning(f"[OIX] Sector map unavailable: {e}")
        # An index is its own sector, so filtering by BANKNIFTY shows the
        # index row itself and not only its 14 constituents.
        own = SECTOR_INDEXES.get(symbol)
        tags = list(self._sector_map.get(symbol, []))
        if own and own not in tags:
            tags.insert(0, own)
        return tags

    # ── chain fetch ──────────────────────────────────────────────────

    def _fyers(self):
        """The raw fyers SDK handle, or None if we're not on Fyers."""
        return getattr(self.provider, 'fyers', None)

    def fetch_totals(self, symbol: str) -> Optional[Dict[str, Any]]:
        """One optionchain call → the six numbers a crossover needs.

        Fyers gives ``data.callOi`` / ``data.putOi`` as totals directly, but
        no total for the *change*, so the per-strike ``oich`` values are
        summed here. Returns None when the chain is missing or too thin to
        trust; ``self.last_error`` says which, for the scan's failure report.
        """
        self.last_error = None
        fyers = self._fyers()
        if fyers is None:
            self.last_error = 'no fyers provider'
            return None

        underlying = self.underlying_for(symbol)
        try:
            from trading_app.service.fyers_data_service import _rate_limiter
            _rate_limiter.wait()
            resp = fyers.optionchain(data={'symbol': underlying, 'strikecount': STRIKE_COUNT})
        except Exception as e:
            self.last_error = f'exception: {e}'
            logger.debug(f"[OIX] {symbol}: chain fetch error: {e}")
            return None

        if not isinstance(resp, dict) or resp.get('s') != 'ok':
            self.last_error = str((resp or {}).get('message') or resp)[:120]
            return None

        data = resp.get('data') or {}
        rows = data.get('optionsChain') or []
        if not rows:
            self.last_error = 'empty chain'
            return None

        ce_oi = pe_oi = ce_chg = pe_chg = 0
        strikes = 0
        spot = None
        # (strike, CE/PE, ltp) for every option row, so the ATM premium at this
        # exact moment can be read off the chain we are already holding — see
        # _atm_from_chain. Free: no second call, no second point in time.
        chain: List[Tuple[float, str, Any]] = []
        for row in rows:
            opt_type = (row.get('option_type') or '').upper()
            if opt_type not in ('CE', 'PE'):
                # The chain's first row is the underlying itself — it carries
                # the spot price and no option type.
                if spot is None:
                    spot = row.get('ltp')
                continue
            strikes += 1
            if row.get('strike_price') is not None:
                chain.append((float(row['strike_price']), opt_type, row.get('ltp')))
            oi = int(row.get('oi') or 0)
            oich = int(row.get('oich') or 0)
            if opt_type == 'CE':
                ce_oi += oi
                ce_chg += oich
            else:
                pe_oi += oi
                pe_chg += oich

        if strikes < MIN_STRIKES:
            self.last_error = f'only {strikes} strikes'
            return None
        if ce_oi == 0 and pe_oi == 0:
            self.last_error = 'chain has no open interest'
            return None

        # Prefer the API's own totals when present — they cover the whole
        # chain, while our sum only covers the strikes we asked for.
        ce_oi = int(data.get('callOi') or ce_oi)
        pe_oi = int(data.get('putOi') or pe_oi)

        atm = self._atm_from_chain(chain, spot)

        return {
            'symbol': symbol,
            'spot': spot,
            'ce_oi': ce_oi,
            'pe_oi': pe_oi,
            'ce_chg': ce_chg,
            'pe_chg': pe_chg,
            'pcr': round(pe_oi / ce_oi, 2) if ce_oi else 0.0,
            'atm_strike': atm[0],
            'atm_ce_ltp': atm[1],
            'atm_pe_ltp': atm[2],
        }

    @staticmethod
    def _atm_from_chain(chain: List[Tuple[float, str, Any]], spot: Any
                        ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """(strike, CE premium, PE premium) nearest the money, off chain rows.

        Same rule as ``atm_quote`` — the strike is the chain's own, not spot
        rounded to a step we guessed — but reading a chain the caller already
        has instead of making a call. That is what makes it usable inside a
        215-symbol sweep, and what lets a crossover record the premium at the
        instant it fired rather than whenever the screen next asked.
        """
        if spot is None or not chain:
            return (None, None, None)
        try:
            spot = float(spot)
        except (TypeError, ValueError):
            return (None, None, None)
        strike = min({s for s, _t, _l in chain}, key=lambda s: abs(s - spot))

        def leg(kind: str) -> Optional[float]:
            ltp = next((l for s, t, l in chain if s == strike and t == kind), None)
            # 0 is Fyers' answer for a contract that has not traded, and a
            # premium of zero is not a real price — treat it as no quote.
            return float(ltp) if ltp else None

        return (strike, leg('CE'), leg('PE'))

    # ── ATM quote ────────────────────────────────────────────────────

    def atm_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """The ATM strike and both its premiums, off the live chain.

        The strike is read from the chain rather than rounded off spot: the
        step is not a constant the app can assume (it varies by symbol and
        Fyers changes it as a scrip's price moves), and a strike we invented
        is a strike the broker will reject. ``strikecount=1`` keeps this to
        the rows around the money instead of the 50 a scan pulls.

        Both sides come back because the caller knows the direction and this
        does not — and the CE and PE are in the response either way, so
        returning both costs nothing over returning one.
        """
        fyers = self._fyers()
        if fyers is None:
            return None
        try:
            from trading_app.service.fyers_data_service import _rate_limiter
            _rate_limiter.wait()
            resp = fyers.optionchain(data={'symbol': self.underlying_for(symbol),
                                           'strikecount': 1})
        except Exception as e:
            logger.debug(f"[OIX] {symbol}: ATM chain fetch failed: {e}")
            return None

        if not isinstance(resp, dict) or resp.get('s') != 'ok':
            return None

        rows = (resp.get('data') or {}).get('optionsChain') or []
        spot = next((r.get('ltp') for r in rows
                     if (r.get('option_type') or '').upper() not in ('CE', 'PE')), None)
        options = [r for r in rows
                   if r.get('strike_price') is not None
                   and (r.get('option_type') or '').upper() in ('CE', 'PE')]
        if not options or spot is None:
            return None

        strike = min({float(r['strike_price']) for r in options},
                     key=lambda s: abs(s - float(spot)))

        def leg(kind: str) -> Optional[float]:
            row = next((r for r in options
                        if float(r['strike_price']) == strike
                        and (r.get('option_type') or '').upper() == kind), None)
            # 0 is what Fyers reports for a contract that has not traded, and
            # a premium of zero is not a real price — treat it as no quote.
            ltp = (row or {}).get('ltp')
            return float(ltp) if ltp else None

        return {'strike': strike, 'ce_ltp': leg('CE'), 'pe_ltp': leg('PE'),
                'spot': float(spot), 'lot_size': self.lot_size(symbol)}

    def lot_size(self, symbol: str) -> Optional[int]:
        """Contract lot size for the symbol's derivatives.

        A lookup in the provider's cached instrument dump, not an API call —
        but the dump is scanned per lookup, so the answer is memoised for the
        process: lot sizes change only on an exchange revision.
        """
        if symbol in _LOT_SIZE_CACHE:
            return _LOT_SIZE_CACHE[symbol]
        size = None
        try:
            if hasattr(self.provider, 'get_lot_size'):
                raw = self.provider.get_lot_size(symbol)
                # get_lot_size answers 1 for "not found", which is not a real
                # F&O lot for anything this scanner covers — treat it as
                # unknown rather than showing a wrong quantity beside a price.
                size = int(raw) if raw and int(raw) > 1 else None
        except Exception as e:
            logger.debug(f"[OIX] {symbol}: lot size lookup failed: {e}")
        _LOT_SIZE_CACHE[symbol] = size
        return size

    def atm_quotes(self, symbols: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """ATM strike and premiums per symbol, one chain call each.

        Deliberately caller-driven and not part of ``snapshot``: the grid asks
        only for the rows the user is actually looking at. A snapshot can carry
        200 symbols, and 200 extra chain calls on every 60s auto-refresh would
        eat the app-wide Fyers budget that the scanner and every chart share.

        The short cache only coalesces bursts — several renders inside one
        refresh — rather than holding a price across refreshes: a premium the
        user is about to trade on has to be roughly current, which is why this
        TTL is well under the scanner's own 3-minute cadence.
        """
        now = time.time()
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        for symbol in symbols:
            hit = _ATM_CACHE.get(symbol)
            if hit and now - hit[0] < _ATM_CACHE_TTL:
                out[symbol] = hit[1]
                continue
            quote = self.atm_quote(symbol)
            # Failures cache too, briefly: a symbol with no chain would
            # otherwise be retried on every single refresh.
            _ATM_CACHE[symbol] = (now, quote)
            out[symbol] = quote
        return out

    # ── open positions ───────────────────────────────────────────────

    def positions(self) -> Dict[int, Dict[str, Any]]:
        """Today's filled OI-Crossover orders, valued at the live premium.

        Keyed by the *cross* that was traded, not by the symbol. A symbol
        crosses several times on a normal day and each cross is now its own
        row, so a symbol-keyed P&L would print the same position against every
        one of them and none of them would be the trade actually taken.

        Multiple buys of the same contract on the same signal net into one
        position at the quantity-weighted average entry, so firing the button
        twice reads as one bigger position rather than two rows fighting over
        the same cell.

        The traded strike is priced, not the current ATM: spot drifts, and by
        the afternoon the strike bought at 10 AM may be a step away from the
        money. That is also why this pulls a wider chain than atm_quote does.
        """
        try:
            from trading_app.app.utils.mine_order_store import MineOrderStore
            # The store's own IST day boundary, rather than a second opinion
            # about when "today" starts.
            orders = MineOrderStore.get_today_orders() or []
        except Exception as e:
            logger.error(f"[OIX] positions: order store unreadable: {e}")
            return {}

        oix = [o for o in orders if o.get('strategy') == 'oix'
               and o.get('status') == 'EXECUTED']
        if not oix:
            return {}

        legs: Dict[Tuple[int, float, str], Dict[str, Any]] = {}
        for o in oix:
            qty = int(o.get('quantity') or 0)
            entry = float(o.get('entry_price') or o.get('price') or 0)
            if qty <= 0 or entry <= 0:
                continue
            signal_id = self._signal_for_order(o)
            if signal_id is None:
                continue
            # A SELL closes, so it nets against the buys on the same contract.
            signed = qty if (o.get('action') or 'BUY').upper() == 'BUY' else -qty
            key = (signal_id, float(o['strike']), (o.get('option_type') or '').upper())
            leg = legs.setdefault(key, {'qty': 0, 'cost': 0.0, 'symbol': o['symbol']})
            leg['qty'] += signed
            leg['cost'] += entry * signed

        out: Dict[int, Dict[str, Any]] = {}
        for (signal_id, strike, opt_type), leg in legs.items():
            if leg['qty'] <= 0:
                continue  # flat or closed out — nothing left to value
            entry = leg['cost'] / leg['qty']
            ltp = self.strike_ltp(leg['symbol'], strike, opt_type)
            out[signal_id] = {
                'strike': strike,
                'option_type': opt_type,
                'qty': leg['qty'],
                'entry': round(entry, 2),
                'ltp': ltp,
                'pnl': round((ltp - entry) * leg['qty'], 2) if ltp is not None else None,
            }
        return out

    def _signal_for_order(self, order: Dict[str, Any]) -> Optional[int]:
        """Which cross an order was placed from.

        The screen stamps ``signal_id`` at order time, which is authoritative.
        Orders placed before it did so — and any placed from elsewhere against
        an 'oix' strategy — are attributed to the symbol's most recent cross at
        or before the moment the order went out, which is the one the button
        would have been sitting on.
        """
        stamped = order.get('signal_id')
        if stamped is not None:
            try:
                return int(stamped)
            except (TypeError, ValueError):
                pass

        created = order.get('created_at')
        symbol = order.get('symbol')
        if not created or not symbol:
            return None
        try:
            # The store writes epoch milliseconds; event timestamps are naive
            # local IST strings, so the comparison is done in local time.
            placed = datetime.fromtimestamp(float(created) / 1000.0)
        except (TypeError, ValueError, OSError):
            return None

        try:
            with sqlite3.connect(self.db_path) as conn:
                hit = conn.execute(
                    '''SELECT id FROM oi_crossover_events
                       WHERE trade_date = ? AND symbol = ? AND ts <= ?
                       ORDER BY ts DESC LIMIT 1''',
                    (placed.strftime('%Y-%m-%d'), symbol,
                     placed.strftime('%Y-%m-%dT%H:%M:%S'))).fetchone()
        except Exception as e:
            logger.error(f"[OIX] positions: could not attribute order: {e}")
            return None
        return hit[0] if hit else None

    def strike_ltp(self, symbol: str, strike: float,
                   opt_type: str) -> Optional[float]:
        """Live premium for one specific strike.

        ``strikecount=10`` rather than the 1 an ATM lookup needs: the strike
        being valued was at the money when it was bought, and this has to keep
        finding it after spot has moved away from it.
        """
        fyers = self._fyers()
        if fyers is None:
            return None
        try:
            from trading_app.service.fyers_data_service import _rate_limiter
            _rate_limiter.wait()
            resp = fyers.optionchain(data={'symbol': self.underlying_for(symbol),
                                           'strikecount': 10})
        except Exception as e:
            logger.debug(f"[OIX] {symbol}: strike quote failed: {e}")
            return None
        if not isinstance(resp, dict) or resp.get('s') != 'ok':
            return None
        for row in (resp.get('data') or {}).get('optionsChain') or []:
            if (row.get('option_type') or '').upper() != opt_type:
                continue
            if row.get('strike_price') is None or float(row['strike_price']) != strike:
                continue
            ltp = row.get('ltp')
            return float(ltp) if ltp else None
        return None

    # ── scan ─────────────────────────────────────────────────────────

    def scan(self, symbols: Optional[List[str]] = None,
             force: bool = False) -> Dict[str, Any]:
        """Sweep the universe, persist a series point per symbol, and record
        any crossover that fired since the previous point.

        Outside a live session this is a no-op. ``force`` overrides the clock
        for deliberate testing, but not the frozen-feed check — forcing a scan
        still won't let a motionless market write rows that pretend to be a
        session.
        """
        live, why = market_session_state()
        if not live and not force:
            # Not logged as a run: the scheduler's cron fires either side of
            # the session, and recording nine "skipped" rows a day would bury
            # the failures that actually need looking at.
            logger.debug(f"[OIX] Skipping scan — {why}")
            return {'success': False, 'skipped': True,
                    'error': f'Not a trading session ({why})'}

        if self._fyers() is None:
            return self._log_run(0, 0, 0, 0.0,
                                 'OI crossover scan needs the Fyers provider')

        if not _SCAN_LOCK.acquire(blocking=False):
            logger.info("[OIX] A scan is already running — skipping this one")
            return {'success': False, 'error': 'A scan is already in progress',
                    'skipped': True}
        try:
            return self._scan(symbols)
        finally:
            _SCAN_LOCK.release()

    def _scan(self, symbols: Optional[List[str]]) -> Dict[str, Any]:
        symbols = symbols or self.universe()
        trade_date = _today()
        ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        started = time.time()

        prev = self._last_points(trade_date)
        rows: List[Tuple] = []
        events: List[Tuple] = []
        failures: Dict[str, str] = {}
        gap = 1.0 / SCAN_REQUESTS_PER_SEC

        # Up front, in ~5 batched calls, so every row in this sweep carries the
        # price from the same moment rather than drifting across the 100s the
        # option chains take.
        futures = self.future_prices(symbols)

        def take(symbol: str) -> bool:
            """Fetch one symbol into rows/events. True when it landed."""
            totals = self.fetch_totals(symbol)
            if not totals:
                failures[symbol] = self.last_error or 'unknown'
                return False
            failures.pop(symbol, None)

            rows.append((trade_date, ts, symbol, totals['spot'], totals['ce_oi'],
                         totals['pe_oi'], totals['ce_chg'], totals['pe_chg'],
                         totals['pcr'], futures.get(symbol)))

            diff = totals['pe_chg'] - totals['ce_chg']
            if diff == 0:
                return True
            previous = prev.get(symbol)
            # First reading of the day seeds the sign without claiming a
            # cross — there is nothing yet for the lines to have crossed.
            if previous is None:
                return True
            if (previous > 0) == (diff > 0):
                return True
            # Rate the cross here, against the legs that produced it, and store
            # the rating with it. Re-deriving these later from a newer scan
            # judges the signal by a market it was never a statement about.
            direction = 'BULL' if diff > 0 else 'BEAR'
            events.append((trade_date, symbol, ts, direction,
                           totals['ce_chg'], totals['pe_chg'],
                           totals['spot'], totals['atm_strike'],
                           totals['atm_ce_ltp'], totals['atm_pe_ltp'],
                           totals['ce_oi'], totals['pe_oi'],
                           self.classify(direction, totals['ce_chg'], totals['pe_chg']),
                           self.oi_chg_pct(totals['ce_oi'], totals['pe_oi'],
                                           totals['ce_chg'], totals['pe_chg']),
                           self.separation(totals['ce_chg'], totals['pe_chg']),
                           # Unconfirmed until a later scan shows the sign held.
                           0))
            return True

        for symbol in symbols:
            take(symbol)
            time.sleep(gap)

        # Second pass for whatever the broker refused. Waiting out the quota
        # window first is the point — retrying immediately just burns the same
        # rejection, and these stragglers are the alphabetical tail that would
        # otherwise never appear in the scanner at all.
        stragglers = sorted(failures)
        if stragglers:
            logger.info(f"[OIX] Retrying {len(stragglers)} symbols after {RETRY_PAUSE_SEC}s")
            time.sleep(RETRY_PAUSE_SEC)
            for symbol in stragglers:
                take(symbol)
                time.sleep(gap)

        elapsed = round(time.time() - started, 1)

        # A dead access token fails every symbol individually. Writing the
        # handful that happened to slip through would seed the day's signs
        # from a near-empty universe and manufacture phantom crossovers on the
        # next good scan, so a broken run is recorded and discarded whole.
        auth_failures = sum(1 for why in failures.values()
                            if any(h in why.lower() for h in _AUTH_HINTS))
        if auth_failures and not rows:
            return self._log_run(
                0, len(failures), 0, elapsed,
                f'Broker auth rejected — {auth_failures} of {len(symbols)} symbols '
                f'returned a token error. The Fyers access token needs its daily login.')
        if failures and len(failures) > len(symbols) // 2:
            return self._log_run(
                len(rows), len(failures), 0, elapsed,
                f'Only {len(rows)} of {len(symbols)} symbols returned a chain — '
                f'discarding this scan rather than seeding from a partial universe.')

        # Holiday / outage detection, without a holiday calendar. Across 200-odd
        # symbols a live market always moves *something* in three minutes; a
        # shut one replays the previous session byte for byte. So if not one
        # symbol differs from the last stored reading, this is not a session —
        # which also catches a stalled feed on a day the calendar calls open.
        if rows and self._feed_is_frozen(rows):
            return self._log_run(
                len(rows), len(failures), 0, elapsed,
                f'Feed frozen — all {len(rows)} symbols identical to the previous '
                f'reading, so the market is shut (holiday) or the feed is stale.')

        confirmed: List[Dict[str, Any]] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    '''INSERT INTO oi_crossover_series
                       (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg,
                        pe_chg, pcr, fut_price)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''', rows)
                conn.executemany(
                    '''INSERT OR IGNORE INTO oi_crossover_events
                       (trade_date, symbol, ts, direction, ce_chg, pe_chg,
                        spot, atm_strike, ce_ltp, pe_ltp, ce_oi, pe_oi,
                        quality, oi_chg_pct, separation, confirm_state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', events)
                # Same transaction as the inserts: a scan that dies midway must
                # not leave half the day's crosses judged against a sweep whose
                # series rows were rolled back.
                confirmed = self._resolve_confirmations(conn, trade_date, ts, rows)
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] Persist failed: {e}", exc_info=True)
            return self._log_run(0, len(failures), 0, elapsed, str(e))

        if confirmed:
            self._alert_confirmed(trade_date, confirmed)

        logger.info(f"[OIX] Scan done: {len(rows)}/{len(symbols)} symbols, "
                    f"{len(events)} crossovers, {len(failures)} failed, {elapsed}s")
        if failures:
            logger.info(f"[OIX] Failures: {failures}")
        out = self._log_run(len(rows), len(failures), len(events), elapsed, None)
        out['failures'] = failures
        out['ts'] = ts
        return out

    def _log_run(self, scanned: int, failed: int, crossovers: int,
                 elapsed: float, error: Optional[str]) -> Dict[str, Any]:
        """Record the attempt and shape it as the scan's return value."""
        if error:
            logger.warning(f"[OIX] Scan aborted: {error}")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    '''INSERT INTO oi_crossover_runs
                       (trade_date, ts, scanned, failed, crossovers, elapsed_sec, error)
                       VALUES (?,?,?,?,?,?,?)''',
                    (_today(), datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                     scanned, failed, crossovers, elapsed, error))
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] Could not log run: {e}")
        return {'success': error is None, 'scanned': scanned, 'failed': failed,
                'crossovers': crossovers, 'elapsed_sec': elapsed,
                **({'error': error} if error else {})}

    def last_run(self, trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Most recent scan attempt for a day, successful or not."""
        trade_date = trade_date or _today()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    '''SELECT ts, scanned, failed, crossovers, elapsed_sec, error
                       FROM oi_crossover_runs WHERE trade_date = ?
                       ORDER BY ts DESC LIMIT 1''', (trade_date,)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"[OIX] last_run failed: {e}")
            return None

    def _feed_is_frozen(self, rows: List[Tuple]) -> bool:
        """True when every scanned symbol repeats its last stored reading.

        Compares against the newest row per symbol from any date, so the first
        scan of a holiday morning is caught against the previous session's
        close rather than sailing through unopposed. Needs a prior reading for
        most of the universe to judge — on a genuinely fresh database there is
        nothing to compare and the scan is allowed.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                previous = {
                    sym: (ce_oi, pe_oi, ce_chg, pe_chg)
                    for sym, ce_oi, pe_oi, ce_chg, pe_chg in conn.execute(
                        '''SELECT s.symbol, s.ce_oi, s.pe_oi, s.ce_chg, s.pe_chg
                           FROM oi_crossover_series s
                           JOIN (SELECT symbol, MAX(ts) AS ts
                                 FROM oi_crossover_series GROUP BY symbol) m
                             ON s.symbol = m.symbol AND s.ts = m.ts''')
                }
        except Exception as e:
            logger.error(f"[OIX] Frozen-feed check failed: {e}")
            return False

        # rows are (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr)
        compared = 0
        for row in rows:
            before = previous.get(row[2])
            if before is None:
                continue
            compared += 1
            if before != (row[4], row[5], row[6], row[7]):
                return False

        return compared >= max(5, len(rows) // 2)

    def _last_points(self, trade_date: str) -> Dict[str, int]:
        """Latest ``pe_chg - ce_chg`` per symbol for the day, ignoring zeros.

        Zeros are skipped rather than treated as a sign so a symbol that
        idles at a dead-flat diff for several scans doesn't lose the side it
        was on and then report a phantom cross when it moves again.
        """
        out: Dict[str, int] = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    '''SELECT symbol, pe_chg - ce_chg AS diff
                       FROM oi_crossover_series
                       WHERE trade_date = ? AND pe_chg - ce_chg != 0
                       ORDER BY ts''', (trade_date,))
                for symbol, diff in cur:
                    out[symbol] = diff
        except Exception as e:
            logger.error(f"[OIX] Could not read previous points: {e}")
        return out

    @staticmethod
    def _uvar(key: str, default: str = '') -> str:
        """One env value, read the same way the scheduler's jobs read theirs."""
        try:
            from trading_app.app.utils.user_env import UserEnvManager
            user = os.getenv('MONITORING_USERNAME', 'Mine')
            return (UserEnvManager.get_user_var(user, key, default) or '').strip()
        except Exception:
            return default

    def _alert_confirmed(self, trade_date: str,
                         confirmed: List[Dict[str, Any]]) -> None:
        """Notify on crosses that just held, so a signal doesn't need a human
        watching a tab to be seen.

        The screen polls only while it is open and only every 60s, which makes
        an unwatched cross unreachable no matter how well the table behaves.

        Off unless OIX_NOTIFY or OIX_TELEGRAM is explicitly 'true' — the
        scanner screen is how this is read in practice, and neither the bell
        nor the Telegram message earned its volume.

        When switched on it is gated hard on purpose. The scanner records
        roughly 460 crosses a day and alerting on all of them would be
        indistinguishable from alerting on none; the gates below — held its
        sign, decisively separated legs, both legs agreeing, and the symbol's
        first cross of the day — land at 12-25 a day on recorded sessions.
        Every gate is an env var so the volume can be retuned without a code
        change.
        """
        if self._uvar('OIX_NOTIFY', 'false').lower() != 'true' \
                and self._uvar('OIX_TELEGRAM', 'false').lower() != 'true':
            return

        try:
            min_sep = float(self._uvar('OIX_ALERT_MIN_SEPARATION', '30'))
        except ValueError:
            min_sep = 30.0
        want_quality = self._uvar('OIX_ALERT_QUALITY', 'strong').lower()
        first_only = self._uvar('OIX_ALERT_FIRST_CROSS_ONLY', 'true').lower() != 'false'
        try:
            cap = int(self._uvar('OIX_ALERT_MAX_PER_SCAN', '15'))
        except ValueError:
            cap = 15

        picks = []
        for ev in confirmed:
            if (ev.get('separation') or 0) < min_sep:
                continue
            if want_quality not in ('', 'any') and ev.get('quality') != want_quality:
                continue
            picks.append(ev)
        if not picks:
            return

        if first_only:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    firsts = {r[0] for r in conn.execute(
                        '''SELECT MIN(id) FROM oi_crossover_events
                           WHERE trade_date = ? GROUP BY symbol''', (trade_date,))}
                picks = [e for e in picks if e['id'] in firsts]
            except Exception as e:
                logger.error(f"[OIX] first-cross gate failed: {e}")
        if not picks:
            return

        picks.sort(key=lambda e: -(e.get('separation') or 0))
        extra = max(0, len(picks) - cap)
        picks = picks[:cap]

        at = (picks[0]['ts'] or '')[11:16]
        payload = {
            'trade_date': trade_date,
            'time': at,
            'signals': [{
                'symbol': e['symbol'],
                'direction': e['direction'],
                'quality': e['quality'],
                'separation': e['separation'],
                'oi_chg_pct': e['oi_chg_pct'],
                'cross_time': (e['ts'] or '')[11:16],
                'strike': e['atm_strike'],
                # The premium at the cross, not now: it is the price the
                # signal was a statement about.
                'premium': e['ce_ltp'] if e['direction'] == 'BULL' else e['pe_ltp'],
                'spot': e['spot'],
            } for e in picks],
            'suppressed': extra,
        }

        if self._uvar('OIX_NOTIFY', 'false').lower() == 'true':
            try:
                from trading_app.service.notification_service import create_notification
                bull = sum(1 for e in picks if e['direction'] == 'BULL')
                create_notification(
                    category='oi_crossover',
                    title=f"OI Crossover — {len(picks)} confirmed at {at}",
                    summary=(f"{bull} BULL, {len(picks) - bull} BEAR · "
                             + ', '.join(e['symbol'] for e in picks[:5])
                             + (f" +{len(picks) - 5} more" if len(picks) > 5 else '')),
                    data=payload,
                )
            except Exception as e:
                logger.error(f"[OIX] in-app alert failed: {e}")

        self._send_telegram(payload)

    def _send_telegram(self, payload: Dict[str, Any]) -> None:
        """Fire-and-forget Telegram alert. Off unless OIX_TELEGRAM is 'true';
        credentials come from the user's own env file, and with either unset
        this is silently a no-op even when the flag is on."""
        if self._uvar('OIX_TELEGRAM', 'false').lower() != 'true':
            return
        token = self._uvar('TELEGRAM_BOT_TOKEN')
        chat_id = self._uvar('TELEGRAM_CHAT_ID')
        if not (token and chat_id):
            return

        lines = [f"🔀 OI Crossover — confirmed at {payload['time']}"]
        for s in payload['signals']:
            arrow = '📈' if s['direction'] == 'BULL' else '📉'
            side = 'CE' if s['direction'] == 'BULL' else 'PE'
            strike = '' if s['strike'] is None else f" {int(s['strike'])}{side}"
            prem = '' if s['premium'] is None else f" @ ₹{s['premium']}"
            lines.append(f"{arrow} {s['symbol']} {s['direction']}{strike}{prem}"
                         f"  ({s['cross_time']} · sep {s['separation']:.0f}%)")
        if payload.get('suppressed'):
            lines.append(f"…+{payload['suppressed']} more below the cap")
        message = '\n'.join(lines)

        def _send() -> None:
            try:
                from trading_app.service.telegram_service import TelegramService
                result = TelegramService(token=token, chat_id=chat_id).send_text(message)
                if not result.get('success'):
                    logger.error(f"[OIX] Telegram alert failed: {result.get('error')}")
            except Exception as e:
                logger.error(f"[OIX] Telegram alert failed: {e}")

        # Off-thread: send_text allows a 10s HTTP timeout, which would
        # otherwise sit inside the scan's own 100s budget.
        threading.Thread(target=_send, daemon=True, name='OixNotify').start()

    @staticmethod
    def _resolve_confirmations(conn: sqlite3.Connection, trade_date: str,
                               ts: str, rows: List[Tuple]) -> List[Dict[str, Any]]:
        """Settle every still-pending cross against this sweep.

        A cross cannot be judged on the scan it fires — the sign has only just
        flipped, and about a fifth of them flip straight back. So an event is
        written pending and the *next* sweep to see that symbol decides: the
        diff still on the event's side confirms it, the diff back the other way
        marks it flipped. Both are terminal, so each cross is settled exactly
        once and a symbol that chops afterwards can't relitigate it.

        Returns the crosses that just confirmed, which is what a trader can
        still act on and therefore the only thing worth alerting about.
        """
        # Symbol -> the side this sweep is on. A dead-flat diff states nothing,
        # so those symbols leave their pending crosses pending.
        sides = {row[2]: ('BULL' if row[7] - row[6] > 0 else 'BEAR')
                 for row in rows if row[7] - row[6] != 0}
        if not sides:
            return []

        conn.row_factory = sqlite3.Row
        pending = conn.execute(
            '''SELECT * FROM oi_crossover_events
               WHERE trade_date = ? AND ts < ? AND COALESCE(confirm_state, 0) = 0''',
            (trade_date, ts)).fetchall()

        settled: List[Tuple[int, int]] = []
        out: List[Dict[str, Any]] = []
        for ev in pending:
            side = sides.get(ev['symbol'])
            if side is None:
                continue
            held = (side == ev['direction'])
            settled.append((1 if held else -1, ev['id']))
            if held:
                out.append(dict(ev))

        if settled:
            conn.executemany(
                'UPDATE oi_crossover_events SET confirm_state = ? WHERE id = ?',
                settled)
        return out

    # ── read side ────────────────────────────────────────────────────

    def snapshot(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """The screen's table: one row per symbol that has crossed today."""
        trade_date = trade_date or _today()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                latest = conn.execute(
                    '''SELECT s.* FROM oi_crossover_series s
                       JOIN (SELECT symbol, MAX(ts) AS ts
                             FROM oi_crossover_series WHERE trade_date = ?
                             GROUP BY symbol) m
                         ON s.symbol = m.symbol AND s.ts = m.ts
                       WHERE s.trade_date = ?''', (trade_date, trade_date)).fetchall()

                # The series row at the same ts is the scan the cross was
                # detected on, so its spot *is* the spot at the crossover —
                # which recovers the price for every event recorded before the
                # events table carried one of its own.
                crosses: Dict[str, List[sqlite3.Row]] = {}
                for row in conn.execute(
                        '''SELECT e.*, s.spot AS series_spot
                           FROM oi_crossover_events e
                           LEFT JOIN oi_crossover_series s
                             ON s.trade_date = e.trade_date
                            AND s.symbol = e.symbol AND s.ts = e.ts
                           WHERE e.trade_date = ? ORDER BY e.ts''', (trade_date,)):
                    crosses.setdefault(row['symbol'], []).append(row)

                last_ts = conn.execute(
                    'SELECT MAX(ts) FROM oi_crossover_series WHERE trade_date = ?',
                    (trade_date,)).fetchone()[0]
        except Exception as e:
            logger.error(f"[OIX] snapshot failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

        # Positions are deliberately not joined here: valuing one costs a chain
        # call per holding, and /snapshot is polled every 60s. The grid joins
        # them from /positions on its own cadence, keyed by the same signal id.
        by_symbol = {row['symbol']: row for row in latest}

        out = []
        for symbol, events in crosses.items():
            row = by_symbol.get(symbol)
            for seq, ev in enumerate(events, start=1):
                direction = ev['direction']
                # The premium is only the leg the cross implies (CE on a BULL,
                # PE on a BEAR) — the other one is not the trade, so shipping
                # it would only invite showing it.
                cross_ltp = ev['ce_ltp'] if direction == 'BULL' else ev['pe_ltp']
                # Only the day's most recent cross for a symbol can still be
                # running; the earlier ones were superseded by definition.
                live = self._live_state(ev, row, current=(seq == len(events)))
                out.append({
                    'id': ev['id'],
                    'symbol': symbol,
                    'direction': direction,
                    # ── frozen at the instant the lines crossed ──
                    'cross_time': ev['ts'],
                    'cross_spot': ev['spot'] if ev['spot'] is not None
                                  else ev['series_spot'],
                    'cross_strike': ev['atm_strike'],
                    'cross_ltp': cross_ltp,
                    'quality': ev['quality'],
                    'oi_chg_pct': ev['oi_chg_pct'],
                    'separation': ev['separation'],
                    'confirm_state': ev['confirm_state'],
                    'cross_seq': seq,
                    'cross_total': len(events),
                    # ── the latest scan, for decay only — never filtered on ──
                    'status': live['status'],
                    'status_note': live['note'],
                    'live_quality': live['quality'],
                    'live_oi_chg_pct': live['oi_chg_pct'],
                    'spot': row['spot'] if row else None,
                    'ce_oi': row['ce_oi'] if row else None,
                    'pe_oi': row['pe_oi'] if row else None,
                    'pcr': row['pcr'] if row else None,
                    'ce_chg': row['ce_chg'] if row else None,
                    'pe_chg': row['pe_chg'] if row else None,
                    'sectors': self.sectors_for(symbol),
                })

        out.sort(key=lambda r: r['cross_time'], reverse=True)
        return {'success': True, 'trade_date': trade_date, 'as_of': last_ts,
                'scans': self.scan_count(trade_date), 'symbols': len(latest),
                'last_run': self.last_run(trade_date), 'rows': out}

    def _live_state(self, ev: sqlite3.Row, row: Optional[sqlite3.Row],
                    current: bool) -> Dict[str, Any]:
        """How a cross is holding up, as a label rather than as a deletion.

        The screen used to express decay by dropping the row, which is the one
        thing a trader cannot act on: a signal you took silently vanishing
        tells you nothing about whether to stay in it. Every cross keeps its
        row for the day and reports its own decay instead.
        """
        if not (ev['confirm_state'] or 0) and current:
            return {'status': 'PENDING', 'quality': None, 'oi_chg_pct': None,
                    'note': 'Waiting for the next scan to confirm the sign held'}
        if row is None or row['ce_chg'] is None:
            return {'status': 'UNKNOWN', 'quality': None, 'oi_chg_pct': None,
                    'note': 'No scan since the cross'}

        live_q = self.classify(ev['direction'], row['ce_chg'], row['pe_chg'])
        live_pct = self.oi_chg_pct(row['ce_oi'], row['pe_oi'],
                                   row['ce_chg'], row['pe_chg'])
        base = {'quality': live_q, 'oi_chg_pct': live_pct}
        at = (ev['ts'] or '')[11:16]

        if ev['confirm_state'] == -1 or not current:
            return {**base, 'status': 'FLIPPED',
                    'note': f'The lines crossed back after {at}'}

        diff = row['pe_chg'] - row['ce_chg']
        side = 'BULL' if diff > 0 else 'BEAR'
        if diff and side != ev['direction']:
            return {**base, 'status': 'FLIPPED',
                    'note': f'Now reading {side} against the {at} cross'}

        # Same side, weaker flow. Ranked so "strong -> aggressive" is a fade
        # but "aggressive -> strong" is not.
        rank = {'strong': 3, 'aggressive': 2, 'covering': 1, 'weak': 0}
        if rank.get(live_q, 0) < rank.get(ev['quality'], 0):
            return {**base, 'status': 'FADED',
                    'note': f'Was {ev["quality"]} at {at}, now {live_q}'}
        return {**base, 'status': 'LIVE',
                'note': f'Still {live_q} since the {at} cross'}

    @staticmethod
    def classify(direction: str, ce_chg: int, pe_chg: int) -> str:
        """Rate the cross by what the two legs are actually doing.

        The side that won the cross is the "winner" leg (puts on a BULL,
        calls on a BEAR). Fresh OI on the winner is aggressive positioning;
        OI leaving the loser is unwinding. Both at once is the strongest
        reading, since new money and exiting money agree on direction.
        """
        winner, loser = (pe_chg, ce_chg) if direction == 'BULL' else (ce_chg, pe_chg)
        if winner > 0 and loser < 0:
            return 'strong'
        if winner > 0:
            return 'aggressive'
        if loser < 0:
            return 'covering'
        return 'weak'

    @staticmethod
    def oi_chg_pct(ce_oi: int, pe_oi: int, ce_chg: int, pe_chg: int) -> float:
        """Larger of the two legs' OI change as a % of that leg's opening OI."""
        pct = 0.0
        for oi, chg in ((ce_oi, ce_chg), (pe_oi, pe_chg)):
            base = oi - chg
            if base > 0:
                pct = max(pct, abs(chg) / base * 100.0)
        return round(pct, 2)

    @staticmethod
    def separation(ce_chg: int, pe_chg: int) -> float:
        """How decisively the two lines are apart, 0-100.

        The gap between the legs as a share of the total flow behind them, so
        it reads the same on a 200-lot stock as on NIFTY. This is the strength
        gate the screen actually needs: it is complete the instant the cross
        fires, whereas oi_chg_pct measures against the 9:15 open and so
        mechanically grows all session — a threshold on that is a clock, not a
        filter.

        A third of recorded crosses come in under 5. Those are the sign
        flipping about in the noise band where the two lines sit on top of one
        another, which is chop rather than a signal.
        """
        total = abs(ce_chg) + abs(pe_chg)
        if not total:
            return 0.0
        return round(abs(pe_chg - ce_chg) / total * 100.0, 2)

    def series(self, symbol: str, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """The two change-lines plus every cross — powers the row drilldown."""
        trade_date = trade_date or _today()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                points = [dict(r) for r in conn.execute(
                    '''SELECT ts, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr,
                              fut_price, COALESCE(fut_price, spot) AS price
                       FROM oi_crossover_series
                       WHERE trade_date = ? AND symbol = ? ORDER BY ts''',
                    (trade_date, symbol))]
                events = [dict(r) for r in conn.execute(
                    '''SELECT ts, direction, ce_chg, pe_chg
                       FROM oi_crossover_events
                       WHERE trade_date = ? AND symbol = ? ORDER BY ts''',
                    (trade_date, symbol))]
        except Exception as e:
            logger.error(f"[OIX] series failed for {symbol}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

        # Sessions recorded before futures were captured fall back to spot in
        # the query above; the chart labels the line accordingly rather than
        # calling a spot series "Future".
        has_futures = any(p.get('fut_price') is not None for p in points)
        return {'success': True, 'symbol': symbol, 'trade_date': trade_date,
                'price_source': 'Future' if has_futures else 'Spot',
                'points': points, 'events': events}

    def available_dates(self, limit: int = 60) -> List[str]:
        """Recorded sessions, newest first, no earlier than HISTORY_FLOOR_DATE."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                return [r[0] for r in conn.execute(
                    '''SELECT DISTINCT trade_date FROM oi_crossover_series
                       WHERE trade_date >= ?
                       ORDER BY trade_date DESC LIMIT ?''',
                    (HISTORY_FLOOR_DATE, limit))]
        except Exception as e:
            logger.error(f"[OIX] available_dates failed: {e}")
            return []

    def sector_options(self) -> List[str]:
        """Sector labels that at least one scanned symbol actually carries."""
        self.sectors_for('NIFTY')  # force the map to load
        used = {tag for tags in (self._sector_map or {}).values() for tag in tags}
        used.update(SECTOR_INDEXES.values())
        return sorted(used)

    def backfill_from_oi_history(self, days: int = 30) -> Dict[str, Any]:
        """Reconstruct past sessions from ``oi_history``.

        The OI persistence job has been storing ``total_ce_change`` /
        ``total_pe_change`` per minute for the index chains since long before
        this scanner existed — the same two series the crossover rule reads.
        Replaying them gives Historical mode real content on day one instead
        of starting empty, and it is the only way to see a full session at
        1-minute resolution rather than the live scanner's 3.

        Only dates/symbols absent from ``oi_crossover_series`` are written, so
        this never fights the live scanner for a day it already covered.

        Everything is read in one ordered pass and grouped in Python. Querying
        per (date, symbol) instead reads far better but costs a full scan each
        time — ``date(timestamp)`` can't use an index — which on a few hundred
        pairs turns a one-minute job into a ten-minute one.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        written = {'days': 0, 'points': 0, 'events': 0, 'skipped': 0}
        series: List[Tuple] = []
        events: List[Tuple] = []

        try:
            with sqlite3.connect(self.db_path) as conn:
                have = {(d, s) for d, s in conn.execute(
                    'SELECT DISTINCT trade_date, symbol FROM oi_crossover_series')}

                cur = conn.execute(
                    '''SELECT date(timestamp), symbol, timestamp, current_price,
                              total_ce_oi, total_pe_oi, total_ce_change,
                              total_pe_change, pcr
                       FROM oi_history
                       WHERE date(timestamp) >= ?
                         AND time(timestamp) BETWEEN '09:15' AND '15:40'
                       ORDER BY symbol, timestamp''', (cutoff,))

                group = None          # (trade_date, symbol) currently being built
                prev_diff = None
                seen_minutes = set()
                group_points = 0

                def close_group():
                    nonlocal group_points
                    if group and group_points:
                        written['days'] += 1
                        written['points'] += group_points
                    group_points = 0

                for trade_date, symbol, ts, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr in cur:
                    key = (trade_date, symbol)
                    if key != group:
                        close_group()
                        group = key
                        prev_diff = None
                        seen_minutes = set()
                        if key in have:
                            written['skipped'] += 1
                    if key in have:
                        continue

                    # oi_history can hold several rows for one minute (a retry,
                    # or a page load racing the scheduler). They carry identical
                    # values, so keeping more than one would only pad the series.
                    minute = ts[:16]
                    if minute in seen_minutes:
                        continue
                    seen_minutes.add(minute)

                    ce_chg, pe_chg = int(ce_chg or 0), int(pe_chg or 0)
                    stamp = f'{minute}:00'.replace(' ', 'T')
                    series.append((trade_date, stamp, symbol, spot,
                                   int(ce_oi or 0), int(pe_oi or 0), ce_chg, pe_chg, pcr))
                    group_points += 1

                    diff = pe_chg - ce_chg
                    if diff == 0:
                        continue
                    if prev_diff is not None and (prev_diff > 0) != (diff > 0):
                        events.append((trade_date, symbol, stamp,
                                       'BULL' if diff > 0 else 'BEAR', ce_chg, pe_chg))
                    prev_diff = diff

                close_group()
                written['events'] = len(events)

                conn.executemany(
                    '''INSERT INTO oi_crossover_series
                       (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr)
                       VALUES (?,?,?,?,?,?,?,?,?)''', series)
                conn.executemany(
                    '''INSERT OR IGNORE INTO oi_crossover_events
                       (trade_date, symbol, ts, direction, ce_chg, pe_chg)
                       VALUES (?,?,?,?,?,?)''', events)
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] Backfill failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e), **written}

        logger.info(f"[OIX] Backfill: {written}")
        return {'success': True, **written}

    def backfill_futures_prices(self) -> Dict[str, Any]:
        """Fill in ``fut_price`` for sessions recorded before futures capture.

        Sessions replayed out of ``oi_history`` only ever had spot, so their
        charts fall back to it. Fyers serves 1-minute futures candles, so the
        real front-month price can be recovered for any session whose contract
        is still listed — 385 candles a day, one request per symbol per date.

        What cannot be recovered: expired contracts. Fyers drops them from the
        master and answers "Invalid symbol provided", so a July session's
        July future is simply gone. Those keep spot and stay labelled as such
        rather than being filled with the wrong contract's price — the August
        future did trade in July, but as the far month, which is not the
        series the chart claims to draw.
        """
        provider = self.provider
        if provider is None or not hasattr(provider, 'historical_data'):
            return {'success': False, 'error': 'Futures backfill needs the Fyers provider'}

        try:
            with sqlite3.connect(self.db_path) as conn:
                pending = conn.execute(
                    '''SELECT DISTINCT trade_date, symbol FROM oi_crossover_series
                       WHERE fut_price IS NULL ORDER BY trade_date DESC, symbol''').fetchall()
        except Exception as e:
            logger.error(f"[OIX] Futures backfill query failed: {e}")
            return {'success': False, 'error': str(e)}

        contracts = self._live_futures()
        out = {'filled': 0, 'sessions': 0, 'skipped_expired': 0, 'no_candles': 0}

        for trade_date, symbol in pending:
            contract = self._front_month_for(symbol, trade_date, contracts)
            if not contract:
                out['skipped_expired'] += 1
                continue

            try:
                candles = provider.historical_data(
                    contract, trade_date, trade_date, 'minute', use_cache=False)
            except Exception as e:
                logger.debug(f"[OIX] {symbol} {trade_date}: futures history failed: {e}")
                candles = None
            if not candles:
                out['no_candles'] += 1
                continue

            # minute-of-day → close, so a series point maps to the candle that
            # was forming when it was recorded.
            by_minute = {}
            for c in candles:
                stamp = str(c.get('date') or '')
                hhmm_key = stamp[11:16]
                if hhmm_key:
                    by_minute[hhmm_key] = c.get('close')

            try:
                with sqlite3.connect(self.db_path) as conn:
                    rows = conn.execute(
                        '''SELECT id, ts FROM oi_crossover_series
                           WHERE trade_date = ? AND symbol = ? AND fut_price IS NULL''',
                        (trade_date, symbol)).fetchall()
                    updates = []
                    for row_id, ts in rows:
                        price = by_minute.get(ts[11:16])
                        if price:
                            updates.append((float(price), row_id))
                    if updates:
                        conn.executemany(
                            'UPDATE oi_crossover_series SET fut_price = ? WHERE id = ?',
                            updates)
                        conn.commit()
                        out['filled'] += len(updates)
                        out['sessions'] += 1
            except Exception as e:
                logger.error(f"[OIX] {symbol} {trade_date}: futures update failed: {e}")

        logger.info(f"[OIX] Futures backfill: {out}")
        return {'success': True, **out}

    def _live_futures(self) -> Dict[str, List[Tuple[Any, str]]]:
        """{root: [(expiry, symbol), ...]} for the contracts still listed."""
        out: Dict[str, List[Tuple[Any, str]]] = {}
        try:
            for exchange in ('NFO', 'BFO'):
                for inst in self.provider.instruments(exchange) or []:
                    if (inst.get('instrument_type') or '').upper() != 'FUT':
                        continue
                    root = (inst.get('name') or '').strip().upper()
                    expiry, token = inst.get('expiry'), inst.get('instrument_token')
                    if root and expiry and token:
                        out.setdefault(root, []).append((expiry, token))
            for contracts in out.values():
                contracts.sort()
        except Exception as e:
            logger.error(f"[OIX] Could not list futures contracts: {e}")
        return out

    @staticmethod
    def _front_month_for(symbol: str, trade_date: str,
                         contracts: Dict[str, List[Tuple[Any, str]]]) -> Optional[str]:
        """The contract that was front-month on ``trade_date``, if still listed.

        A contract is front-month strictly after the previous monthly expiry
        and up to its own. For every contract except the earliest still
        listed, the previous expiry is known — it's the one before it in the
        sorted list — so the answer is exact.

        Only the earliest listed contract needs a guess, because whatever
        preceded it has been delisted. A 28-day window before its expiry
        stands in, monthly expiries being about a month apart, and the window
        is exclusive at the far end on purpose: a date exactly 28 days out is
        most likely the previous expiry day itself, when this contract was
        still the next month rather than the front one. Erring toward None
        only leaves that session on spot.
        """
        listed = contracts.get(symbol.upper())
        if not listed:
            return None
        day = datetime.strptime(trade_date, '%Y-%m-%d').date()
        seen_expired = False
        for expiry, token in listed:
            exp = expiry if hasattr(expiry, 'year') else datetime.strptime(
                str(expiry)[:10], '%Y-%m-%d').date()
            if day <= exp:
                # Reaching here past an earlier contract means that one had
                # already expired on `day`, so this is certainly the front
                # month — no guessing needed.
                if seen_expired:
                    return token
                return token if (exp - day).days < 28 else None
            seen_expired = True
        return None

    def backfill_event_metrics(self) -> Dict[str, Any]:
        """Fill the frozen rating on crosses recorded before it was stored.

        Every one of them is recoverable: the series row written by the same
        sweep carries the leg OI and the leg changes the cross was detected
        from, so the rating can be reconstructed exactly rather than
        approximated. Confirmation likewise — the next series row for that
        symbol is what the live scanner would have judged it against.

        Without this, Historical mode would filter every past session down to
        nothing, because the new gates read columns that only today's scans
        populate.

        Idempotent: only rows still missing a rating are touched. A cross whose
        series partner is absent stays NULL and is treated everywhere as
        unrated — never as zero, which would silently sink it below every floor.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                todo = conn.execute(
                    '''SELECT e.id, e.direction, e.ts, e.symbol, e.trade_date,
                              e.confirm_state,
                              COALESCE(e.ce_chg, s.ce_chg) AS ce_chg,
                              COALESCE(e.pe_chg, s.pe_chg) AS pe_chg,
                              s.ce_oi AS ce_oi, s.pe_oi AS pe_oi
                       FROM oi_crossover_events e
                       LEFT JOIN oi_crossover_series s
                         ON s.trade_date = e.trade_date
                        AND s.symbol = e.symbol AND s.ts = e.ts
                       WHERE e.quality IS NULL OR e.separation IS NULL
                          OR e.confirm_state IS NULL''').fetchall()

                rated: List[Tuple] = []
                skipped = 0
                for ev in todo:
                    ce_chg, pe_chg = ev['ce_chg'], ev['pe_chg']
                    if ce_chg is None or pe_chg is None:
                        skipped += 1
                        continue
                    ce_oi, pe_oi = ev['ce_oi'], ev['pe_oi']
                    # The rating needs the OI levels; without the series row
                    # only separation is recoverable, and a partial rating
                    # would filter as though the missing halves were zero.
                    if ce_oi is None or pe_oi is None:
                        skipped += 1
                        continue

                    # What the following sweep saw, which is exactly what the
                    # live path resolves confirmation against.
                    nxt = conn.execute(
                        '''SELECT ce_chg, pe_chg FROM oi_crossover_series
                           WHERE trade_date = ? AND symbol = ? AND ts > ?
                             AND pe_chg - ce_chg != 0
                           ORDER BY ts LIMIT 1''',
                        (ev['trade_date'], ev['symbol'], ev['ts'])).fetchone()
                    if nxt is None:
                        confirm = 0  # last cross of the day, never settled
                    else:
                        side = 'BULL' if nxt['pe_chg'] - nxt['ce_chg'] > 0 else 'BEAR'
                        confirm = 1 if side == ev['direction'] else -1

                    rated.append((
                        ce_oi, pe_oi,
                        self.classify(ev['direction'], ce_chg, pe_chg),
                        self.oi_chg_pct(ce_oi, pe_oi, ce_chg, pe_chg),
                        self.separation(ce_chg, pe_chg),
                        confirm, ce_chg, pe_chg, ev['id'],
                    ))

                conn.executemany(
                    '''UPDATE oi_crossover_events
                       SET ce_oi = ?, pe_oi = ?, quality = ?, oi_chg_pct = ?,
                           separation = ?, confirm_state = ?, ce_chg = ?, pe_chg = ?
                       WHERE id = ?''', rated)
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] Event metric backfill failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

        logger.info(f"[OIX] Rated {len(rated)} past crossovers, {skipped} unrecoverable")
        return {'success': True, 'rated': len(rated), 'skipped': skipped,
                'considered': len(todo)}

    def scan_count(self, trade_date: Optional[str] = None) -> int:
        """Distinct scan timestamps for a day — the empty state needs this to
        tell 'nothing scanned yet' apart from 'scanned, nothing crossed'."""
        trade_date = trade_date or _today()
        try:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute(
                    'SELECT COUNT(DISTINCT ts) FROM oi_crossover_series WHERE trade_date = ?',
                    (trade_date,)).fetchone()[0]
        except Exception:
            return 0

    def purge_non_sessions(self) -> Dict[str, int]:
        """Delete anything recorded against a weekend.

        Scans run before the session gate existed captured Saturday and Sunday
        as if they were trading days. Those rows are the previous close
        repeated, so they seed the next real session's signs from stale values
        and would fire a phantom crossover on Monday's first scan.
        """
        removed = {'series': 0, 'events': 0, 'runs': 0}
        try:
            with sqlite3.connect(self.db_path) as conn:
                dates = [d for (d,) in conn.execute(
                    'SELECT DISTINCT trade_date FROM oi_crossover_series')]
                weekend = [d for d in dates
                           if datetime.strptime(d, '%Y-%m-%d').weekday() >= 5]
                for table in ('oi_crossover_series', 'oi_crossover_events',
                              'oi_crossover_runs'):
                    key = table.split('_')[-1]
                    for d in weekend:
                        cur = conn.execute(
                            f'DELETE FROM {table} WHERE trade_date = ?', (d,))
                        removed[key] += cur.rowcount
                conn.commit()
                if weekend:
                    logger.info(f"[OIX] Purged non-session dates {weekend}: {removed}")
        except Exception as e:
            logger.error(f"[OIX] purge_non_sessions failed: {e}")
        return removed

    def purge(self, keep_days: int = 30) -> int:
        """Drop series points older than ``keep_days``. Events are kept —
        they are tiny and are what the historical view actually reads."""
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    'DELETE FROM oi_crossover_series WHERE trade_date < ?', (cutoff,))
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.error(f"[OIX] purge failed: {e}")
            return 0
