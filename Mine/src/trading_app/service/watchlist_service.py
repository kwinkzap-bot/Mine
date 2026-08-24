"""Watchlist — user-defined tabs of symbols, priced live and shown with the
fundamentals a broker terminal doesn't carry (52-week range, P/E, market cap).

Where each number comes from, and why
-------------------------------------
Three sources, split by what each one is actually good at:

* **Live price / day change** — the app's own data provider (Fyers), through
  the same batched ``quote()`` every other page uses. It batches 50 symbols
  per call and holds a 3-second cache during market hours, so a 40-row
  watchlist refreshing on 60s costs one request a minute out of the shared
  8 req/s budget. Nothing here is worth starving an algo for.

* **52-week range, P/E, EPS, market cap, sector** — Yahoo, via yfinance, cached
  in sqlite for ``FUNDAMENTALS_TTL`` seconds. No Indian broker API publishes a
  price-to-earnings ratio, and these move on a quarterly result, not on a tick.

* **Price / P/E history for the drilldown chart** — Yahoo daily candles. Using
  the broker here instead would cost one history request per chart open for a
  series that is identical every day after the close.

The symbol universe for the search box is the Fyers NSE cash master, which is
a plain public CSV — no broker login involved, so a user can build a watchlist
before the day's token exists. It carries equities and the ~124 NSE indices in
one file, which is exactly the pick-list this page wants.

P/E history is derived, not reported
------------------------------------
P/E here is price to earnings: the day's close over earnings per share.
Nobody publishes a daily P/E series, so this computes ``close / EPS`` per day,
where EPS is the trailing-twelve-month figure in force on that date — built by
summing four consecutive reported quarters where Yahoo has them, and falling
back to today's TTM EPS held flat where it doesn't. The response says which
basis was used (``eps_basis``) and the chart footnote repeats it, because a
flat-EPS P/E line is just the price line rescaled and should not be read as a
re-rating.
"""

import csv
import io
import math
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from trading_app.app.utils.logger import logger

_BASEDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
DB_PATH = os.path.join(_BASEDIR, 'oi_data.db')

# The Fyers NSE cash master. Public, unauthenticated, and refreshed daily by
# Fyers — the same file FyersDataServiceAdapter.instruments() reads, fetched
# directly here so symbol search keeps working with no broker session.
NSE_MASTER_URL = 'https://public.fyers.in/sym_details/NSE_CM.csv'
UNIVERSE_TTL = 6 * 3600

# Fundamentals move on a quarterly result. Six hours means a watchlist opened
# in the morning and again after the close does at most two fetches a day per
# symbol, and a fresh row is never more than one refresh away.
FUNDAMENTALS_TTL = 6 * 3600

# One Yahoo .info call runs ~1s. Six at a time keeps a 40-symbol cold load
# near seven seconds without opening a connection per symbol.
_FUNDAMENTALS_WORKERS = 6

# A cold load must not hang the grid. Whatever hasn't returned by then is
# served from its stale row (or blank) and picked up on the next refresh.
_FUNDAMENTALS_DEADLINE = 25.0

# Yahoo tickers for the indices worth watching. The NSE master names them
# (NSE:NIFTY50-INDEX) but Yahoo has its own symbology and no mapping endpoint,
# so the ones a watchlist actually holds are listed. An index that isn't here
# still prices live off the broker — it just shows no 52-week range or P/E.
INDEX_YF: Dict[str, str] = {
    'NSE:NIFTY50-INDEX':          '^NSEI',
    'NSE:NIFTYBANK-INDEX':        '^NSEBANK',
    'NSE:FINNIFTY-INDEX':         'NIFTY_FIN_SERVICE.NS',
    'NSE:MIDCPNIFTY-INDEX':       'NIFTY_MIDCAP_100.NS',
    'NSE:NIFTYMIDCAP150-INDEX':   'NIFTY_MIDCAP_100.NS',
    'NSE:INDIAVIX-INDEX':         '^INDIAVIX',
    'NSE:NIFTYIT-INDEX':          '^CNXIT',
    'NSE:NIFTYAUTO-INDEX':        '^CNXAUTO',
    'NSE:NIFTYPHARMA-INDEX':      '^CNXPHARMA',
    'NSE:NIFTYFMCG-INDEX':        '^CNXFMCG',
    'NSE:NIFTYMETAL-INDEX':       '^CNXMETAL',
    'NSE:NIFTYPSUBANK-INDEX':     '^CNXPSUBANK',
    'NSE:NIFTY500-INDEX':         '^CRSLDX',
    'BSE:SENSEX-INDEX':           '^BSESN',
}

# TradingView's own symbology for the indices, alongside INDEX_YF above so
# the three namings of the same instrument (Fyers / Yahoo / TradingView) sit
# together. Equities need no table — TradingView takes NSE:<ticker> as-is.
INDEX_TV: Dict[str, str] = {
    'NSE:NIFTY50-INDEX':          'NSE:NIFTY',
    'NSE:NIFTYBANK-INDEX':        'NSE:BANKNIFTY',
    'NSE:FINNIFTY-INDEX':         'NSE:CNXFINANCE',
    'NSE:MIDCPNIFTY-INDEX':       'NSE:NIFTYMIDSELECT',
    'NSE:NIFTYMIDCAP150-INDEX':   'NSE:NIFTYMIDCAP150',
    'NSE:INDIAVIX-INDEX':         'NSE:INDIAVIX',
    'NSE:NIFTYIT-INDEX':          'NSE:CNXIT',
    'NSE:NIFTYAUTO-INDEX':        'NSE:CNXAUTO',
    'NSE:NIFTYPHARMA-INDEX':      'NSE:CNXPHARMA',
    'NSE:NIFTYFMCG-INDEX':        'NSE:CNXFMCG',
    'NSE:NIFTYMETAL-INDEX':       'NSE:CNXMETAL',
    'NSE:NIFTYPSUBANK-INDEX':     'NSE:NIFTYPSUBANK',
    'NSE:NIFTY500-INDEX':         'NSE:CNX500',
    'BSE:SENSEX-INDEX':           'BSE:SENSEX',
}

# Chart timeframes offered by the candle popup.
#
#   yf       Yahoo's interval.
#   period   How much history to pull. Yahoo caps intraday hard — 1m to the
#            last 7 days, 5m/15m/30m to the last 60 — and answers a wider
#            window with an error rather than a truncation, so these sit
#            inside the caps deliberately.
#   cpr      The timeframe the CPR/Camarilla levels are computed from, one
#            step up from the candles as every CPR indicator does it:
#            intraday reads against the day, hourly against the week, daily
#            against the month, weekly and monthly against the year.
#   intraday Whether a bar needs a time as well as a date.
#
# 3m and 4h are absent because Yahoo has neither; its intraday set is
# 1m/2m/5m/15m/30m/60m/90m.
INTERVALS: Dict[str, Dict[str, Any]] = {
    '1m':  {'yf': '1m',   'period': '5d',   'cpr': ('D',),        'cpr_label': 'Daily',   'intraday': True},
    '5m':  {'yf': '5m',   'period': '1mo',  'cpr': ('D',),        'cpr_label': 'Daily',   'intraday': True},
    '15m': {'yf': '15m',  'period': '1mo',  'cpr': ('D',),        'cpr_label': 'Daily',   'intraday': True},
    '30m': {'yf': '30m',  'period': '1mo',  'cpr': ('D',),        'cpr_label': 'Daily',   'intraday': True},
    '1h':  {'yf': '1h',   'period': '6mo',  'cpr': ('W',),        'cpr_label': 'Weekly',  'intraday': True},
    '1d':  {'yf': '1d',   'period': '1y',   'cpr': ('ME', 'M'),   'cpr_label': 'Monthly', 'intraday': False},
    '1wk': {'yf': '1wk',  'period': '5y',   'cpr': ('YE', 'A'),   'cpr_label': 'Yearly',  'intraday': False},
    '1mo': {'yf': '1mo',  'period': '10y',  'cpr': ('YE', 'A'),   'cpr_label': 'Yearly',  'intraday': False},
}

# Chart ranges offered by the drilldown, mapped to a Yahoo period.
HISTORY_RANGES: Dict[str, str] = {
    '1m': '1mo', '6m': '6mo', '1y': '1y', '3y': '3y', '5y': '5y',
}

MAX_TABS = 20
MAX_ITEMS_PER_TAB = 60  # one batched quote() call is 50 symbols

_universe_lock = threading.Lock()
_universe: List[Dict[str, str]] = []
_universe_at = 0.0

# yf_symbol -> (fetched_at, payload). The drilldown re-reads the same series on
# every tab and range switch; a daily close series does not need re-fetching
# within a sitting.
_history_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_HISTORY_TTL = 1800.0
_history_lock = threading.Lock()


# ── storage ──────────────────────────────────────────────────────────────

@contextmanager
def _connect():
    """A connection that commits on a clean exit and always closes.

    ``with sqlite3.connect(...)`` — the pattern the other services here use —
    commits but never closes. This page polls on a 60-second timer, so the
    descriptors would add up over a session.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


_schema_ready = False
_schema_lock = threading.Lock()


def _ensure_schema() -> None:
    """Create the three tables on first use.

    Lazy rather than at import: this module is imported by the route
    blueprint at app start, and a scanner's DB should not be touched by the
    act of registering a URL.
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS watchlist_tabs (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    username   TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    position   INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ix_wl_tab_user_name
                    ON watchlist_tabs (username, name COLLATE NOCASE);
                -- broker_instance is added below for tables that predate it.

                CREATE TABLE IF NOT EXISTS watchlist_items (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    tab_id    INTEGER NOT NULL,
                    symbol    TEXT NOT NULL,
                    fy_symbol TEXT NOT NULL,
                    yf_symbol TEXT,
                    kind      TEXT NOT NULL DEFAULT 'EQ',
                    company   TEXT,
                    position  INTEGER NOT NULL DEFAULT 0,
                    added_at  TEXT NOT NULL,
                    FOREIGN KEY (tab_id) REFERENCES watchlist_tabs (id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ix_wl_item_tab_symbol
                    ON watchlist_items (tab_id, symbol);

                CREATE TABLE IF NOT EXISTS watchlist_fundamentals (
                    yf_symbol   TEXT PRIMARY KEY,
                    company     TEXT,
                    sector      TEXT,
                    industry    TEXT,
                    pe          REAL,
                    eps         REAL,
                    high52      REAL,
                    low52       REAL,
                    market_cap  REAL,
                    pb          REAL,
                    div_yield   REAL,
                    yf_price    REAL,
                    yf_prev     REAL,
                    fetched_at  REAL NOT NULL
                );
            """)
        # Added after the table shipped, so it is an ALTER rather than part
        # of the CREATE above. Naming a tab after an account was the first
        # way to link the two, and it cannot express "Saran" when both
        # Saranya (Kite) and Saranya (Dhan) exist — this binding can.
        with _connect() as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(watchlist_tabs)")}
            if 'broker_instance' not in columns:
                conn.execute("ALTER TABLE watchlist_tabs ADD COLUMN broker_instance INTEGER")
        _schema_ready = True


# ── symbol universe / search ─────────────────────────────────────────────

def _yf_for(fy_symbol: str, root: str, kind: str) -> Optional[str]:
    if kind == 'INDEX':
        return INDEX_YF.get(fy_symbol)
    # Yahoo uses the plain NSE ticker with a .NS suffix. `root` is the master's
    # underlying column, which is already the bare ticker ("RELIANCE", "M&M").
    return f'{root}.NS' if root else None


def _tv_symbol(fy_symbol: str, symbol: str, kind: str) -> str:
    """The TradingView symbol for a row, e.g. 'NSE:RELIANCE'.

    Unmapped indices fall through to NSE:<root>, which is right often enough
    to be worth trying — TradingView says "invalid symbol" on the few it
    isn't, which is a clearer answer than refusing to open the chart.
    """
    if kind == 'INDEX':
        return INDEX_TV.get(fy_symbol) or f'NSE:{symbol}'
    exchange = fy_symbol.split(':')[0] if ':' in fy_symbol else 'NSE'
    return f'{exchange}:{symbol}'


def _load_universe() -> List[Dict[str, str]]:
    """Parse the NSE cash master into search rows. Cached for UNIVERSE_TTL."""
    global _universe, _universe_at
    with _universe_lock:
        if _universe and (time.time() - _universe_at) < UNIVERSE_TTL:
            return _universe

        rows: List[Dict[str, str]] = []
        try:
            resp = requests.get(NSE_MASTER_URL, timeout=30)
            resp.raise_for_status()
            reader = csv.reader(io.StringIO(resp.text))
            for parts in reader:
                if len(parts) < 17:
                    continue
                fy_symbol = parts[9].strip()
                if not fy_symbol:
                    continue
                upper = fy_symbol.upper()
                if upper.endswith('-INDEX'):
                    kind = 'INDEX'
                elif upper.endswith('-EQ'):
                    kind = 'EQ'
                else:
                    # Bonds, T-bills and the rest of the cash segment. A
                    # watchlist of stocks and indices has no use for them and
                    # they would swamp the search box.
                    continue
                root = (parts[13].strip() or fy_symbol.split(':')[-1].rsplit('-', 1)[0]).upper()
                rows.append({
                    'symbol':    root,
                    'fy_symbol': fy_symbol,
                    'company':   parts[1].strip(),
                    'kind':      kind,
                    'yf_symbol': _yf_for(fy_symbol, root, kind) or '',
                })
        except Exception as e:
            logger.error(f"[Watchlist] symbol master fetch failed: {e}")
            # Keep whatever is already loaded rather than blanking search on a
            # transient network failure.
            return _universe

        if rows:
            _universe = rows
            _universe_at = time.time()
        return _universe


def search(query: str, limit: int = 25) -> List[Dict[str, str]]:
    """Symbols matching `query`, prefix hits first.

    Ranked rather than plain-substring so typing "TAT" opens on TATAMOTORS and
    TATASTEEL instead of on whichever bond happens to contain the letters.
    """
    q = (query or '').strip().upper()
    if len(q) < 1:
        return []

    scored: List[Tuple[int, str, Dict[str, str]]] = []
    for row in _load_universe():
        sym = row['symbol']
        company = row['company'].upper()
        if sym.startswith(q):
            rank = 0
        elif company.startswith(q):
            rank = 1
        elif q in sym:
            rank = 2
        elif q in company:
            rank = 3
        else:
            continue
        # Indices sort ahead of equities at equal rank: a watchlist search for
        # "NIFTY" wants the index, not NIFTYBEES.
        scored.append((rank * 2 + (0 if row['kind'] == 'INDEX' else 1), sym, row))

    scored.sort(key=lambda t: (t[0], len(t[1]), t[1]))
    return [r for _, _, r in scored[:limit]]


def _resolve(symbol: str) -> Optional[Dict[str, str]]:
    """Exact universe row for a symbol, accepting either form the UI holds."""
    key = (symbol or '').strip().upper()
    if not key:
        return None
    for row in _load_universe():
        if key in (row['symbol'], row['fy_symbol'].upper()):
            return row
    return None


# ── broker-owned tabs ────────────────────────────────────────────────────
#
# A tab named for a configured broker ("Devanai Kite" against the broker
# "Devanai (Kite)") is that account's list, not a hand-made one. It is filled
# from the account's holdings, so deleting a row would only mean it comes
# back on the next refresh — and deleting the tab would throw away the link
# to the account. Both are refused here rather than merely hidden in the UI,
# so the rule holds however the endpoint is reached.

def broker_slots(username: str) -> List[Dict[str, Any]]:
    """Configured, active broker slots: instance, type and display name."""
    from trading_app.app.utils.user_env import UserEnvManager
    out: List[Dict[str, Any]] = []
    for i in range(1, 11):
        def env(field: str, default: str = '') -> str:
            return (UserEnvManager.get_user_var(username, f'BROKER_{i}_{field}',
                                                default) or '').strip()
        if env('ACTIVE', 'false').lower() != 'true':
            continue
        broker_type = env('TYPE').lower()
        if not broker_type:
            continue
        out.append({'instance': i, 'type': broker_type,
                    'name': env('NAME') or broker_type.title()})
    return out


def _squash(text: Any) -> str:
    """Letters and digits only — "Devanai Kite" and "Devanai (Kite)" are the
    same name written two ways."""
    return ''.join(c for c in str(text or '').lower() if c.isalnum())


def broker_for_tab(username: str, tab_name: str,
                   slots: Optional[List[Dict[str, Any]]] = None,
                   instance: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The broker a tab follows, or None.

    An explicit binding (``instance``) always wins: it is the user saying
    which account this list is, and it is the only thing that can express
    "Saran" when both Saranya (Kite) and Saranya (Dhan) are configured.

    Failing that the name is read — exact match first, then a partial match
    but only when exactly one broker matches. An ambiguous name belongs to
    no account rather than to a guessed one; this page places orders, and
    guessing the account is the one outcome worth designing against.
    """
    slots = broker_slots(username) if slots is None else slots
    if instance:
        bound = [b for b in slots if b['instance'] == instance]
        if bound:
            return bound[0]

    wanted = _squash(tab_name)
    if not wanted:
        return None

    exact = [b for b in slots if _squash(b['name']) == wanted]
    if len(exact) == 1:
        return exact[0]

    partial = [b for b in slots
               if _squash(b['name']) in wanted or wanted in _squash(b['name'])]
    return partial[0] if len(partial) == 1 else None


# ── tabs ─────────────────────────────────────────────────────────────────

def list_tabs(username: str) -> List[Dict[str, Any]]:
    _ensure_schema()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT t.id, t.name, t.position, t.broker_instance,
                   (SELECT COUNT(*) FROM watchlist_items i WHERE i.tab_id = t.id) AS count
            FROM watchlist_tabs t
            WHERE t.username = ?
            ORDER BY t.position, t.id
        """, (username,)).fetchall()

    # Resolved once for the whole list: broker_for_tab re-reads the env
    # otherwise, and this runs on every poll.
    slots = broker_slots(username)
    tabs = []
    for row in rows:
        tab = dict(row)
        tab['broker'] = broker_for_tab(username, tab['name'], slots,
                                       tab.pop('broker_instance', None))
        tabs.append(tab)
    return tabs


def create_tab(username: str, name: str,
               broker_instance: Optional[int] = None) -> Dict[str, Any]:
    _ensure_schema()
    name = (name or '').strip()
    if not name:
        return {'success': False, 'error': 'Tab name is required'}
    if len(name) > 40:
        return {'success': False, 'error': 'Tab name is too long (max 40)'}

    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM watchlist_tabs WHERE username = ?",
                             (username,)).fetchone()[0]
        if count >= MAX_TABS:
            return {'success': False, 'error': f'At most {MAX_TABS} tabs'}
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM watchlist_tabs "
                           "WHERE username = ?", (username,)).fetchone()[0]
        try:
            cur = conn.execute(
                "INSERT INTO watchlist_tabs (username, name, position, created_at, "
                "broker_instance) VALUES (?, ?, ?, ?, ?)",
                (username, name, pos, datetime.now().isoformat(timespec='seconds'),
                 broker_instance))
        except sqlite3.IntegrityError:
            return {'success': False, 'error': f'A tab named "{name}" already exists'}
        return {'success': True,
                'tab': {'id': cur.lastrowid, 'name': name, 'position': pos, 'count': 0,
                        'broker': broker_for_tab(username, name, None, broker_instance)}}


def rename_tab(username: str, tab_id: int, name: str) -> Dict[str, Any]:
    _ensure_schema()
    name = (name or '').strip()
    if not name:
        return {'success': False, 'error': 'Tab name is required'}
    if len(name) > 40:
        return {'success': False, 'error': 'Tab name is too long (max 40)'}
    with _connect() as conn:
        try:
            cur = conn.execute("UPDATE watchlist_tabs SET name = ? WHERE id = ? AND username = ?",
                               (name, tab_id, username))
        except sqlite3.IntegrityError:
            return {'success': False, 'error': f'A tab named "{name}" already exists'}
        if not cur.rowcount:
            return {'success': False, 'error': 'Tab not found'}
    return {'success': True, 'tab': {'id': tab_id, 'name': name}}


def set_tab_broker(username: str, tab_id: int,
                   instance: Optional[int]) -> Dict[str, Any]:
    """Bind a tab to a broker account, or pass None to unbind it."""
    _ensure_schema()
    if instance is not None:
        if not any(b['instance'] == instance for b in broker_slots(username)):
            return {'success': False, 'error': 'That broker is not active'}
    with _connect() as conn:
        cur = conn.execute("UPDATE watchlist_tabs SET broker_instance = ? "
                           "WHERE id = ? AND username = ?", (instance, tab_id, username))
        if not cur.rowcount:
            return {'success': False, 'error': 'Tab not found'}
    return {'success': True, 'broker': broker_for_tab(username, '', None, instance)}


def delete_tab(username: str, tab_id: int) -> Dict[str, Any]:
    _ensure_schema()
    with _connect() as conn:
        # The FK carries ON DELETE CASCADE, but sqlite only honours it with
        # foreign_keys pragma on per-connection. Deleting both explicitly is
        # one line and doesn't depend on a pragma being set.
        owned = conn.execute("SELECT name, broker_instance FROM watchlist_tabs "
                             "WHERE id = ? AND username = ?",
                             (tab_id, username)).fetchone()
        if not owned:
            return {'success': False, 'error': 'Tab not found'}
        broker = broker_for_tab(username, owned['name'], None, owned['broker_instance'])
        if broker:
            return {'success': False,
                    'error': f"\"{owned['name']}\" belongs to {broker['name']} — "
                             f"rename it first if you want to delete it"}
        conn.execute("DELETE FROM watchlist_items WHERE tab_id = ?", (tab_id,))
        conn.execute("DELETE FROM watchlist_tabs WHERE id = ?", (tab_id,))
    return {'success': True}


def _owns_tab(conn: sqlite3.Connection, username: str, tab_id: int) -> bool:
    return conn.execute("SELECT 1 FROM watchlist_tabs WHERE id = ? AND username = ?",
                        (tab_id, username)).fetchone() is not None


# ── items ────────────────────────────────────────────────────────────────

def add_item(username: str, tab_id: int, symbol: str) -> Dict[str, Any]:
    _ensure_schema()
    row = _resolve(symbol)
    if not row:
        return {'success': False, 'error': f'Unknown symbol "{symbol}"'}

    with _connect() as conn:
        if not _owns_tab(conn, username, tab_id):
            return {'success': False, 'error': 'Tab not found'}
        count = conn.execute("SELECT COUNT(*) FROM watchlist_items WHERE tab_id = ?",
                             (tab_id,)).fetchone()[0]
        if count >= MAX_ITEMS_PER_TAB:
            return {'success': False,
                    'error': f'At most {MAX_ITEMS_PER_TAB} symbols per tab'}
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM watchlist_items "
                           "WHERE tab_id = ?", (tab_id,)).fetchone()[0]
        try:
            cur = conn.execute(
                "INSERT INTO watchlist_items (tab_id, symbol, fy_symbol, yf_symbol, kind, "
                "company, position, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tab_id, row['symbol'], row['fy_symbol'], row['yf_symbol'] or None,
                 row['kind'], row['company'], pos,
                 datetime.now().isoformat(timespec='seconds')))
        except sqlite3.IntegrityError:
            return {'success': False,
                    'error': f"{row['symbol']} is already in this tab"}
        # Read lastrowid inside the block — a cursor outliving its closed
        # connection is not something to rely on.
        return {'success': True,
                'item': {'id': cur.lastrowid, 'symbol': row['symbol'],
                         'company': row['company'], 'kind': row['kind']}}


def add_items(username: str, tab_id: int, symbols: List[str]) -> Dict[str, Any]:
    """Add many symbols at once, reporting what happened to each.

    Additive only: symbols already in the tab are counted, never re-added,
    and nothing already there is removed. Importing a broker's holdings into
    a list somebody has been curating must not quietly delete the rest of it.
    """
    _ensure_schema()
    added: List[str] = []
    already: List[str] = []
    skipped: List[Dict[str, str]] = []

    for symbol in symbols or []:
        result = add_item(username, tab_id, symbol)
        if result.get('success'):
            added.append(result['item']['symbol'])
            continue
        error = result.get('error') or 'could not be added'
        if 'already in this tab' in error:
            already.append((symbol or '').upper())
        else:
            skipped.append({'symbol': (symbol or '').upper(), 'error': error})
            # A tab that has hit its ceiling will reject every remaining
            # symbol for the same reason; say it once and stop.
            if 'At most' in error:
                break

    return {'success': True, 'added': added, 'already': already, 'skipped': skipped}


def remove_item(username: str, item_id: int) -> Dict[str, Any]:
    _ensure_schema()
    with _connect() as conn:
        row = conn.execute("""
            SELECT i.id, i.symbol, t.name AS tab_name, t.broker_instance
            FROM watchlist_items i
            JOIN watchlist_tabs t ON t.id = i.tab_id
            WHERE i.id = ? AND t.username = ?
        """, (item_id, username)).fetchone()
        if not row:
            return {'success': False, 'error': 'Symbol not found'}
        broker = broker_for_tab(username, row['tab_name'], None, row['broker_instance'])
        if broker:
            return {'success': False,
                    'error': f"{row['symbol']} is held at {broker['name']} — "
                             f"this tab follows that account"}
        conn.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
    return {'success': True}


def move_item(username: str, item_id: int, target_tab_id: int) -> Dict[str, Any]:
    """Move one symbol to another of this user's tabs.

    A move rather than a remove-and-re-add: re-adding would go through the
    symbol master, so a scrip that has since been delisted or renamed (NSE
    retired TATAMOTORS for TMCV/TMPV mid-2026) could not be put back, and the
    row would simply vanish. Carrying the existing row across keeps whatever
    it was added as.
    """
    _ensure_schema()
    with _connect() as conn:
        item = conn.execute("""
            SELECT i.* FROM watchlist_items i
            JOIN watchlist_tabs t ON t.id = i.tab_id
            WHERE i.id = ? AND t.username = ?
        """, (item_id, username)).fetchone()
        if not item:
            return {'success': False, 'error': 'Symbol not found'}
        if not _owns_tab(conn, username, target_tab_id):
            return {'success': False, 'error': 'Target tab not found'}
        if item['tab_id'] == target_tab_id:
            return {'success': True, 'moved': False}

        count = conn.execute("SELECT COUNT(*) FROM watchlist_items WHERE tab_id = ?",
                             (target_tab_id,)).fetchone()[0]
        if count >= MAX_ITEMS_PER_TAB:
            return {'success': False,
                    'error': f'That tab already holds {MAX_ITEMS_PER_TAB} symbols'}
        clash = conn.execute(
            "SELECT 1 FROM watchlist_items WHERE tab_id = ? AND symbol = ?",
            (target_tab_id, item['symbol'])).fetchone()
        if clash:
            return {'success': False,
                    'error': f"{item['symbol']} is already in that tab"}

        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM watchlist_items "
                           "WHERE tab_id = ?", (target_tab_id,)).fetchone()[0]
        conn.execute("UPDATE watchlist_items SET tab_id = ?, position = ? WHERE id = ?",
                     (target_tab_id, pos, item_id))
        return {'success': True, 'moved': True, 'symbol': item['symbol']}


# ── fundamentals ─────────────────────────────────────────────────────────

def _finite(v: Any) -> Optional[float]:
    """None for anything that isn't a real number.

    Yahoo returns NaN as readily as it returns a value, and a NaN reaching
    jsonify becomes a literal `NaN` token that JSON.parse rejects — the whole
    grid goes blank over one missing P/E.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fetch_fundamentals(yf_symbol: str) -> Optional[Dict[str, Any]]:
    try:
        import yfinance as yf
        info = yf.Ticker(yf_symbol).info or {}
    except Exception as e:
        logger.warning(f"[Watchlist] fundamentals fetch failed for {yf_symbol}: {e}")
        return None
    if not info:
        return None
    return {
        'yf_symbol':  yf_symbol,
        'company':    info.get('longName') or info.get('shortName'),
        'sector':     info.get('sector'),
        'industry':   info.get('industry'),
        'pe':         _finite(info.get('trailingPE')),
        'eps':        _finite(info.get('trailingEps')),
        'high52':     _finite(info.get('fiftyTwoWeekHigh')),
        'low52':      _finite(info.get('fiftyTwoWeekLow')),
        'market_cap': _finite(info.get('marketCap')),
        'pb':         _finite(info.get('priceToBook')),
        'div_yield':  _finite(info.get('dividendYield')),
        'yf_price':   _finite(info.get('currentPrice') or info.get('regularMarketPrice')),
        # Carried so the day-change column still reads when no broker session
        # exists — Yahoo's own close is delayed, but it is a real previous
        # close rather than a blank.
        'yf_prev':    _finite(info.get('regularMarketPreviousClose') or info.get('previousClose')),
        'fetched_at': time.time(),
    }


def _read_fundamentals(yf_symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    if not yf_symbols:
        return {}
    marks = ','.join('?' * len(yf_symbols))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM watchlist_fundamentals WHERE yf_symbol IN ({marks})",
            yf_symbols).fetchall()
    return {r['yf_symbol']: dict(r) for r in rows}


def _store_fundamentals(records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    with _connect() as conn:
        conn.executemany("""
            INSERT INTO watchlist_fundamentals
                (yf_symbol, company, sector, industry, pe, eps, high52, low52,
                 market_cap, pb, div_yield, yf_price, yf_prev, fetched_at)
            VALUES (:yf_symbol, :company, :sector, :industry, :pe, :eps, :high52,
                    :low52, :market_cap, :pb, :div_yield, :yf_price, :yf_prev,
                    :fetched_at)
            ON CONFLICT(yf_symbol) DO UPDATE SET
                company = excluded.company, sector = excluded.sector,
                industry = excluded.industry, pe = excluded.pe, eps = excluded.eps,
                high52 = excluded.high52, low52 = excluded.low52,
                market_cap = excluded.market_cap, pb = excluded.pb,
                div_yield = excluded.div_yield, yf_price = excluded.yf_price,
                yf_prev = excluded.yf_prev, fetched_at = excluded.fetched_at
        """, records)


def _fundamentals_for(yf_symbols: List[str], force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Cached fundamentals, refreshing whatever has gone stale."""
    cached = _read_fundamentals(yf_symbols)
    now = time.time()
    stale = [s for s in yf_symbols
             if force or (now - (cached.get(s, {}).get('fetched_at') or 0)) > FUNDAMENTALS_TTL]
    if not stale:
        return cached

    fetched: List[Dict[str, Any]] = []
    deadline = now + _FUNDAMENTALS_DEADLINE
    with ThreadPoolExecutor(max_workers=min(_FUNDAMENTALS_WORKERS, len(stale))) as pool:
        futures = {pool.submit(_fetch_fundamentals, s): s for s in stale}
        for fut, sym in futures.items():
            try:
                rec = fut.result(timeout=max(1.0, deadline - time.time()))
            except Exception as e:
                logger.warning(f"[Watchlist] fundamentals timed out for {sym}: {e}")
                continue
            if rec:
                fetched.append(rec)
                cached[sym] = rec

    _store_fundamentals(fetched)
    return cached


# ── live quotes ──────────────────────────────────────────────────────────

def _live_quotes(fy_symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """LTP and previous close per Fyers symbol; empty when no broker session."""
    if not fy_symbols:
        return {}
    try:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider()
        if not provider:
            return {}
        quotes = provider.quote(fy_symbols) or {}
    except Exception as e:
        logger.warning(f"[Watchlist] live quote fetch failed: {e}")
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for sym, q in quotes.items():
        ltp = _finite((q or {}).get('last_price'))
        prev = _finite(((q or {}).get('ohlc') or {}).get('close'))
        if not ltp:
            continue
        out[sym] = {'ltp': ltp, 'prev_close': prev}
    return out


# ── grid rows ────────────────────────────────────────────────────────────

def rows(username: str, tab_id: int, refresh: bool = False) -> Dict[str, Any]:
    """Everything the grid shows for one tab."""
    _ensure_schema()
    with _connect() as conn:
        if not _owns_tab(conn, username, tab_id):
            return {'success': False, 'error': 'Tab not found'}
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM watchlist_items WHERE tab_id = ? ORDER BY position, id",
            (tab_id,)).fetchall()]

    if not items:
        return {'success': True, 'rows': [], 'as_of': datetime.now().isoformat(timespec='seconds')}

    quotes = _live_quotes([i['fy_symbol'] for i in items])
    fundamentals = _fundamentals_for(
        sorted({i['yf_symbol'] for i in items if i['yf_symbol']}), force=refresh)

    out = []
    for item in items:
        f = fundamentals.get(item['yf_symbol'] or '', {})
        q = quotes.get(item['fy_symbol'], {})
        # Yahoo's own last price is the fallback when there is no broker
        # session — delayed, but a blank price column is worse than a late one.
        ltp = q.get('ltp') or f.get('yf_price')
        prev = q.get('prev_close') or (f.get('yf_prev') if not q else None)
        change = (ltp - prev) if (ltp is not None and prev) else None

        high52 = f.get('high52')
        low52 = f.get('low52')
        # "How far below its high / above its low" is the read a 52-week
        # column exists for; the raw pair alone makes you do it in your head.
        from_high = ((ltp - high52) / high52 * 100) if (ltp and high52) else None
        from_low = ((ltp - low52) / low52 * 100) if (ltp and low52) else None
        band = ((ltp - low52) / (high52 - low52) * 100) \
            if (ltp and high52 and low52 and high52 > low52) else None

        out.append({
            'id':          item['id'],
            'symbol':      item['symbol'],
            'fy_symbol':   item['fy_symbol'],
            'yf_symbol':   item['yf_symbol'],
            'kind':        item['kind'],
            'tv_symbol':   _tv_symbol(item['fy_symbol'], item['symbol'], item['kind']),
            'company':     f.get('company') or item['company'] or item['symbol'],
            'sector':      f.get('sector'),
            'ltp':         ltp,
            'prev_close':  prev,
            'change':      change,
            'change_pct':  (change / prev * 100) if (change is not None and prev) else None,
            'low52':       low52,
            'high52':      high52,
            'from_high':   from_high,
            'from_low':    from_low,
            'band52':      band,
            'pe':          f.get('pe'),
            'eps':         f.get('eps'),
            'pb':          f.get('pb'),
            'market_cap':  f.get('market_cap'),
            'live':        bool(q),
            'fetched_at':  f.get('fetched_at'),
        })

    return {'success': True, 'rows': out,
            'as_of': datetime.now().isoformat(timespec='seconds'),
            'live': any(r['live'] for r in out)}


# ── history + derived P/E series ─────────────────────────────────────────

def _eps_label(frame) -> Optional[str]:
    if frame is None or getattr(frame, 'empty', True):
        return None
    return next((i for i in frame.index if str(i) in ('Diluted EPS', 'Basic EPS')), None)


def _dated_eps(frame) -> List[Tuple[datetime, float]]:
    """(period_end, EPS) ascending, from an income statement frame."""
    label = _eps_label(frame)
    if label is None:
        return []
    out = []
    for when, value in frame.loc[label].dropna().items():
        eps = _finite(value)
        if eps is None:
            continue
        out.append((when.to_pydatetime() if hasattr(when, 'to_pydatetime') else when, eps))
    return sorted(out, key=lambda t: t[0])


def _eps_steps(ticker, current_eps: Optional[float]) -> List[Tuple[datetime, float]]:
    """(effective_date, TTM EPS) steps from Yahoo's reported statements.

    First choice is four *consecutive* reported quarters summed and dated at
    the newest of them — the day that TTM figure became the one in force.
    Yahoo's quarterly statement is often gappy, and a window straddling a gap
    would sum three quarters as if they were four, so those windows are
    dropped rather than summed.

    Yahoo's quarterly statement reaches back about five quarters, so on its
    own it leaves a 5-year chart with a P/E line over its last year and
    nothing before it — which reads as a broken chart, not as missing data.
    The annual statement goes back four years, so the two are merged: annual
    steps carry the early part, quarterly steps take over from the first one
    they cover. Where both could apply the quarterly figure wins, being the
    more current of the two.

    Both are then checked against today's reported TTM EPS, because Yahoo
    ships statements that are simply not on the same basis as the quote —
    INFY.NS reports "Diluted EPS 0.8" against a trailing EPS near 77, which
    would draw a P/E line two orders of magnitude off. A series that
    disagrees with the current figure by more than DEVIATION is dropped and
    the flat current-EPS basis is used instead, which is at least honest
    about being an approximation.
    """
    DEVIATION = 2.0

    quarterly: List[Tuple[datetime, float]] = []
    try:
        quarters = _dated_eps(ticker.quarterly_income_stmt)
    except Exception:
        quarters = []
    for i in range(3, len(quarters)):
        window = quarters[i - 3:i + 1]
        spans = [(window[j + 1][0] - window[j][0]).days for j in range(3)]
        if any(span > 130 for span in spans):  # a gap wider than one quarter
            continue
        quarterly.append((window[-1][0], sum(v for _, v in window)))

    try:
        annual = [(when, eps) for when, eps in _dated_eps(ticker.income_stmt) if eps > 0]
    except Exception:
        annual = []

    # Annual up to the first quarterly window, quarterly from there on.
    cutoff = quarterly[0][0] if quarterly else None
    steps = [(when, eps) for when, eps in annual
             if cutoff is None or when < cutoff] + quarterly
    if not steps:
        return []

    if current_eps and current_eps > 0:
        latest = steps[-1][1]
        if not (current_eps / DEVIATION <= latest <= current_eps * DEVIATION):
            logger.info(f"[Watchlist] reported EPS series ({latest}) disagrees with "
                        f"trailing EPS ({current_eps}); using flat current EPS")
            return []
    return steps


# Camarilla's R3/S3 multiplier. The third level is the one that matters for
# the reversal read the CPR filter already looks for — see
# CPRFilterService.detect_camarilla_cpr_reversal.
_CAMARILLA_R3 = 1.1 / 4.0


def _period_levels(frame, freq, stamp=None) -> List[Dict[str, Any]]:
    """CPR (TC/P/BC) and Camarilla R3/S3 per period, from the PREVIOUS
    period's OHLC — which is what makes them levels to trade against rather
    than a restatement of the bar they sit on.

    `freq` is a pandas offset alias, or a tuple of aliases to try in order —
    month-end is 'M' before pandas 2.2 and 'ME' after, and this app is not
    pinned to either. Higher
    timeframe than the daily candles the chart draws, deliberately: a daily
    CPR on a daily chart is one band per candle, which is noise. This mirrors
    what every CPR indicator does — the levels come from one timeframe up.
    """
    from trading_app.service.cpr_service import CPRService

    if frame is None or getattr(frame, 'empty', True):
        return []
    aliases = (freq,) if isinstance(freq, str) else tuple(freq)
    grouped = spans = None
    for alias in aliases:
        try:
            grouped = frame.resample(alias).agg(
                {'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
            spans = frame.resample(alias)
            break
        except ValueError:
            continue          # this pandas does not know that alias
    if grouped is None or len(grouped) < 2:
        return []

    # Which days each period covers, so the client can hold a level flat
    # across its period instead of interpolating between two of them.
    starts = {period: group.index[0] for period, group in spans if len(group)}
    key = stamp or (lambda when: when.strftime('%Y-%m-%d'))

    out: List[Dict[str, Any]] = []
    periods = list(grouped.index)
    for i in range(1, len(periods)):
        prev = grouped.loc[periods[i - 1]]
        high, low, close = (_finite(prev['High']), _finite(prev['Low']), _finite(prev['Close']))
        if None in (high, low, close):
            continue
        pp, bc, tc = CPRService.calculate_cpr(high, low, close)
        span = high - low
        first_day = starts.get(periods[i])
        if first_day is None:
            continue
        out.append({
            'from': key(first_day),
            'p':    round(pp, 2),
            'bc':   round(bc, 2),
            'tc':   round(tc, 2),
            'r3':   round(close + span * _CAMARILLA_R3, 2),
            's3':   round(close - span * _CAMARILLA_R3, 2),
        })
    return out


# symbol|interval -> (fetched_at, payload). Separate from the history cache
# because the candle popup and the drilldown ask different questions of the
# same symbol; an intraday series also goes stale far sooner than a daily one.
_candle_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_candle_lock = threading.Lock()

# Intraday moves within the session, so its cache is short. Daily and above
# only change once a day, and the drilldown's TTL already covers that.
_CANDLE_TTL_INTRADAY = 60.0
_CANDLE_TTL_DAILY = 900.0


def candles(symbol: str, interval: str = '1d') -> Dict[str, Any]:
    """OHLCV bars at one timeframe, with the CPR/Camarilla levels that
    timeframe reads against.

    The CPR period is derived, never chosen: see INTERVALS. Levels a trader
    reads against come from one timeframe up, and picking it by hand is how
    a 5-minute chart ends up carrying yearly pivots.
    """
    _ensure_schema()
    spec = INTERVALS.get((interval or '1d').lower())
    if not spec:
        return {'success': False, 'error': f'Unsupported timeframe "{interval}"'}

    row = _resolve(symbol)
    yf_symbol = (row or {}).get('yf_symbol')
    if not yf_symbol:
        return {'success': False, 'error': f'No price history source mapped for {symbol}'}

    cache_key = f'{yf_symbol}|{interval}'
    ttl = _CANDLE_TTL_INTRADAY if spec['intraday'] else _CANDLE_TTL_DAILY
    with _candle_lock:
        hit = _candle_cache.get(cache_key)
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]

    try:
        import yfinance as yf
        frame = yf.Ticker(yf_symbol).history(period=spec['period'], interval=spec['yf'],
                                             auto_adjust=False)
    except Exception as e:
        logger.error(f"[Watchlist] candles failed for {yf_symbol} {interval}: {e}")
        return {'success': False, 'error': f'Chart data unavailable for {symbol}'}

    if frame is None or frame.empty:
        return {'success': False, 'error': f'No {interval} candles for {symbol}'}

    # Lightweight Charts wants epoch seconds for an intraday series and a
    # plain date for anything daily or wider; mixing them silently drops bars.
    if spec['intraday']:
        def stamp(when):
            return int(when.timestamp())
    else:
        def stamp(when):
            return when.strftime('%Y-%m-%d')

    points = []
    for ts, close in frame['Close'].items():
        close = _finite(close)
        if close is None:
            continue
        bar = {'t': stamp(ts), 'c': round(close, 2)}
        for name, key in (('Open', 'o'), ('High', 'h'), ('Low', 'l')):
            value = _finite(frame[name].get(ts)) if name in frame.columns else None
            bar[key] = round(value, 2) if value is not None else None
        volume = _finite(frame['Volume'].get(ts)) if 'Volume' in frame.columns else None
        bar['v'] = int(volume) if volume is not None else None
        points.append(bar)

    try:
        levels = _period_levels(frame, spec['cpr'], stamp)
    except Exception as e:
        logger.warning(f"[Watchlist] CPR levels failed for {yf_symbol} {interval}: {e}")
        levels = []

    payload = {
        'success':    True,
        'symbol':     row['symbol'],
        'company':    row['company'],
        'kind':       row['kind'],
        'interval':   interval,
        'intraday':   spec['intraday'],
        'cpr_period': spec['cpr_label'],
        'points':     points,
        'levels':     levels,
    }
    with _candle_lock:
        _candle_cache[cache_key] = (time.time(), payload)
    return payload


def history(symbol: str, rng: str = '1y') -> Dict[str, Any]:
    """Daily closes plus a derived price-to-earnings line for the chart."""
    _ensure_schema()  # reached directly by URL, not only after rows()
    row = _resolve(symbol)
    yf_symbol = (row or {}).get('yf_symbol')
    if not yf_symbol:
        return {'success': False,
                'error': f'No price history source mapped for {symbol}'}

    period = HISTORY_RANGES.get((rng or '1y').lower(), '1y')
    cache_key = f'{yf_symbol}|{period}'
    with _history_lock:
        hit = _history_cache.get(cache_key)
        if hit and (time.time() - hit[0]) < _HISTORY_TTL:
            return hit[1]

    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_symbol)
        frame = ticker.history(period=period, interval='1d', auto_adjust=False)
    except Exception as e:
        logger.error(f"[Watchlist] history fetch failed for {yf_symbol}: {e}")
        return {'success': False, 'error': f'History unavailable for {symbol}'}

    if frame is None or frame.empty:
        return {'success': False, 'error': f'No price history for {symbol}'}

    # Indices have no earnings, so no P/E line at all — the chart shows price
    # only and the tab bar says so.
    current_eps = None
    steps: List[Tuple[datetime, float]] = []
    if row['kind'] == 'EQ':
        cached = _read_fundamentals([yf_symbol]).get(yf_symbol) or {}
        current_eps = cached.get('eps')
        if current_eps is None:
            rec = _fetch_fundamentals(yf_symbol)
            if rec:
                _store_fundamentals([rec])
                current_eps = rec.get('eps')
        steps = _eps_steps(ticker, current_eps)

    if steps:
        eps_basis = 'reported'
    elif current_eps:
        eps_basis = 'current'
    else:
        eps_basis = 'none'

    def eps_at(when: datetime) -> Optional[float]:
        # The step in force on that date; before the first reported window
        # there is nothing to hold, so those days carry no P/E rather than
        # borrowing a figure from the future.
        chosen = None
        for eff, val in steps:
            if eff.date() <= when.date():
                chosen = val
            else:
                break
        if chosen is None:
            return current_eps if eps_basis == 'current' else None
        return chosen

    # Full OHLC + volume, not just the close: the same payload feeds the
    # line chart in the docked drilldown and the candlesticks in the chart
    # popup, so opening one after the other costs nothing. The drilldown
    # simply ignores the fields it does not plot.
    columns = {name: frame[name] for name in ('Open', 'High', 'Low', 'Volume')
               if name in frame.columns}
    points = []
    for ts, close in frame['Close'].items():
        close = _finite(close)
        if close is None:
            continue
        when = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
        eps = eps_at(when)
        candle = {}
        for name, key in (('Open', 'o'), ('High', 'h'), ('Low', 'l')):
            value = _finite(columns[name].get(ts)) if name in columns else None
            candle[key] = round(value, 2) if value is not None else None
        volume = _finite(columns['Volume'].get(ts)) if 'Volume' in columns else None
        points.append({
            'ts':    when.strftime('%Y-%m-%d'),
            'close': round(close, 2),
            'pe':    round(close / eps, 2) if (eps and eps > 0) else None,
            **candle,
            'v':     int(volume) if volume is not None else None,
        })

    # Both timeframes, computed once from the same daily frame: switching
    # W/M in the chart is then a redraw rather than another request.
    levels = {}
    for key, freq in (('w', ('W',)), ('m', ('ME', 'M'))):
        try:
            levels[key] = _period_levels(frame, freq)
        except Exception as e:
            logger.warning(f"[Watchlist] {key} CPR levels failed for {yf_symbol}: {e}")
            levels[key] = []

    payload = {
        'success':   True,
        'levels':    levels,
        'symbol':    row['symbol'],
        'company':   row['company'],
        'kind':      row['kind'],
        'yf_symbol': yf_symbol,
        'range':     (rng or '1y').lower(),
        'points':    points,
        'eps_basis': eps_basis,
        'eps':       current_eps,
    }
    with _history_lock:
        _history_cache[cache_key] = (time.time(), payload)
    return payload
