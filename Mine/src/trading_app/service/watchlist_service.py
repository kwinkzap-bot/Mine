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
        _schema_ready = True


# ── symbol universe / search ─────────────────────────────────────────────

def _yf_for(fy_symbol: str, root: str, kind: str) -> Optional[str]:
    if kind == 'INDEX':
        return INDEX_YF.get(fy_symbol)
    # Yahoo uses the plain NSE ticker with a .NS suffix. `root` is the master's
    # underlying column, which is already the bare ticker ("RELIANCE", "M&M").
    return f'{root}.NS' if root else None


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


# ── tabs ─────────────────────────────────────────────────────────────────

def list_tabs(username: str) -> List[Dict[str, Any]]:
    _ensure_schema()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT t.id, t.name, t.position,
                   (SELECT COUNT(*) FROM watchlist_items i WHERE i.tab_id = t.id) AS count
            FROM watchlist_tabs t
            WHERE t.username = ?
            ORDER BY t.position, t.id
        """, (username,)).fetchall()
    return [dict(r) for r in rows]


def create_tab(username: str, name: str) -> Dict[str, Any]:
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
                "INSERT INTO watchlist_tabs (username, name, position, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, name, pos, datetime.now().isoformat(timespec='seconds')))
        except sqlite3.IntegrityError:
            return {'success': False, 'error': f'A tab named "{name}" already exists'}
        return {'success': True, 'tab': {'id': cur.lastrowid, 'name': name,
                                         'position': pos, 'count': 0}}


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


def delete_tab(username: str, tab_id: int) -> Dict[str, Any]:
    _ensure_schema()
    with _connect() as conn:
        # The FK carries ON DELETE CASCADE, but sqlite only honours it with
        # foreign_keys pragma on per-connection. Deleting both explicitly is
        # one line and doesn't depend on a pragma being set.
        owned = conn.execute("SELECT 1 FROM watchlist_tabs WHERE id = ? AND username = ?",
                             (tab_id, username)).fetchone()
        if not owned:
            return {'success': False, 'error': 'Tab not found'}
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


def remove_item(username: str, item_id: int) -> Dict[str, Any]:
    _ensure_schema()
    with _connect() as conn:
        row = conn.execute("""
            SELECT i.id FROM watchlist_items i
            JOIN watchlist_tabs t ON t.id = i.tab_id
            WHERE i.id = ? AND t.username = ?
        """, (item_id, username)).fetchone()
        if not row:
            return {'success': False, 'error': 'Symbol not found'}
        conn.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
    return {'success': True}


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

    Falling back to the annual statement when no clean quarterly window
    exists is what gives a 5-year chart a real re-rating line instead of the
    price line rescaled.

    Both are then checked against today's reported TTM EPS, because Yahoo
    ships statements that are simply not on the same basis as the quote —
    INFY.NS reports "Diluted EPS 0.8" against a trailing EPS near 77, which
    would draw a P/E line two orders of magnitude off. A series that
    disagrees with the current figure by more than DEVIATION is dropped and
    the flat current-EPS basis is used instead, which is at least honest
    about being an approximation.
    """
    DEVIATION = 2.0

    steps: List[Tuple[datetime, float]] = []
    try:
        quarters = _dated_eps(ticker.quarterly_income_stmt)
    except Exception:
        quarters = []
    for i in range(3, len(quarters)):
        window = quarters[i - 3:i + 1]
        spans = [(window[j + 1][0] - window[j][0]).days for j in range(3)]
        if any(span > 130 for span in spans):  # a gap wider than one quarter
            continue
        steps.append((window[-1][0], sum(v for _, v in window)))

    if not steps:
        try:
            steps = [(when, eps) for when, eps in _dated_eps(ticker.income_stmt) if eps > 0]
        except Exception:
            steps = []

    if not steps:
        return []

    if current_eps and current_eps > 0:
        latest = steps[-1][1]
        if not (current_eps / DEVIATION <= latest <= current_eps * DEVIATION):
            logger.info(f"[Watchlist] reported EPS series ({latest}) disagrees with "
                        f"trailing EPS ({current_eps}); using flat current EPS")
            return []
    return steps


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

    points = []
    for ts, close in frame['Close'].items():
        close = _finite(close)
        if close is None:
            continue
        when = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
        eps = eps_at(when)
        points.append({
            'ts':    when.strftime('%Y-%m-%d'),
            'close': round(close, 2),
            'pe':    round(close / eps, 2) if (eps and eps > 0) else None,
        })

    payload = {
        'success':   True,
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
