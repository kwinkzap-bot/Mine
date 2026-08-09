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
_CONSTITUENTS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '.cache', 'index_constituents.json')
)

# A paced sweep runs ~110s against a 3-minute cadence, and the manual /scan
# endpoint can fire at any moment. Two sweeps at once would double the request
# rate into the same 429 wall they are pacing to avoid, so a second one steps
# aside rather than queueing behind the first.
_SCAN_LOCK = threading.Lock()

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
        for row in rows:
            opt_type = (row.get('option_type') or '').upper()
            if opt_type not in ('CE', 'PE'):
                # The chain's first row is the underlying itself — it carries
                # the spot price and no option type.
                if spot is None:
                    spot = row.get('ltp')
                continue
            strikes += 1
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

        return {
            'symbol': symbol,
            'spot': spot,
            'ce_oi': ce_oi,
            'pe_oi': pe_oi,
            'ce_chg': ce_chg,
            'pe_chg': pe_chg,
            'pcr': round(pe_oi / ce_oi, 2) if ce_oi else 0.0,
        }

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
            events.append((trade_date, symbol, ts,
                           'BULL' if diff > 0 else 'BEAR',
                           totals['ce_chg'], totals['pe_chg']))
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

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(
                    '''INSERT INTO oi_crossover_series
                       (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg,
                        pe_chg, pcr, fut_price)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''', rows)
                conn.executemany(
                    '''INSERT OR IGNORE INTO oi_crossover_events
                       (trade_date, symbol, ts, direction, ce_chg, pe_chg)
                       VALUES (?,?,?,?,?,?)''', events)
                conn.commit()
        except Exception as e:
            logger.error(f"[OIX] Persist failed: {e}", exc_info=True)
            return self._log_run(0, len(failures), 0, elapsed, str(e))

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

                crosses: Dict[str, List[sqlite3.Row]] = {}
                for row in conn.execute(
                        '''SELECT * FROM oi_crossover_events
                           WHERE trade_date = ? ORDER BY ts''', (trade_date,)):
                    crosses.setdefault(row['symbol'], []).append(row)

                last_ts = conn.execute(
                    'SELECT MAX(ts) FROM oi_crossover_series WHERE trade_date = ?',
                    (trade_date,)).fetchone()[0]
        except Exception as e:
            logger.error(f"[OIX] snapshot failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

        out = []
        for row in latest:
            events = crosses.get(row['symbol'])
            if not events:
                continue  # never crossed today — not a signal, so not a row
            last = events[-1]
            ce_chg, pe_chg = row['ce_chg'], row['pe_chg']
            direction = last['direction']
            out.append({
                'symbol': row['symbol'],
                'direction': direction,
                'spot': row['spot'],
                'ce_oi': row['ce_oi'],
                'pe_oi': row['pe_oi'],
                'pcr': row['pcr'],
                'ce_chg': ce_chg,
                'pe_chg': pe_chg,
                'cross_time': last['ts'],
                'cross_count': len(events),
                'quality': self.classify(direction, ce_chg, pe_chg),
                'oi_chg_pct': self.oi_chg_pct(row['ce_oi'], row['pe_oi'], ce_chg, pe_chg),
                'sectors': self.sectors_for(row['symbol']),
            })

        out.sort(key=lambda r: r['cross_time'], reverse=True)
        return {'success': True, 'trade_date': trade_date, 'as_of': last_ts,
                'scans': self.scan_count(trade_date), 'symbols': len(latest),
                'last_run': self.last_run(trade_date), 'rows': out}

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
        try:
            with sqlite3.connect(self.db_path) as conn:
                return [r[0] for r in conn.execute(
                    '''SELECT DISTINCT trade_date FROM oi_crossover_series
                       ORDER BY trade_date DESC LIMIT ?''', (limit,))]
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
