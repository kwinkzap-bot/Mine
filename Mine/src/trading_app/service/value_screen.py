"""Value screener — the /scanners Value tab.

A snapshot, not a strategy: Yahoo serves only TODAY's fundamentals, so there
is no honest way to backtest what this list would have picked in an earlier
year. Read it as a shortlist of annual reports to open, nothing more.

Where each number comes from
----------------------------
* **Fundamentals** — Screener.in's company page first (``service/screener_in.py``:
  P/E on profit before exceptional items, NSE's industry classification, 12
  years of EPS, borrowings, CFO, FCF, 3-year profit growth, all in INR), and
  Yahoo (yfinance ``Ticker.info``) only for a symbol Screener has no page for —
  each record carries ``source``. Cached in ``value_fundamentals``
  (``oi_data.db``) for ``CACHE_TTL``; a refresh is a background task
  (``start_refresh``), ~1 page a second. No broker is called, so it is safe in
  market hours.
* **The stock's own P/E history** — daily closes from ``data/stocks/*.pkl``
  (``candle_store._load_disk``, no network) over the trailing-twelve-month EPS
  in force each day: Screener's annual + quarterly EPS (``screener_in.eps_steps``),
  or on the Yahoo fallback Yahoo's statements (``watchlist_service._eps_steps``,
  which also drops a series that disagrees with today's EPS). Yahoo reaches
  back ~4 annual statements, so "own history" is about four years, never ten.
  With no reported series the factor is left out rather than faked from a
  flat EPS (which would just be the price line rescaled).

Yahoo's units: ``debtToEquity`` is a percent (45.3 = 0.453×) and
``dividendYield`` is already a percent (0.44 = 0.44 %). Both are converted
here, once, in ``_record_from_info``.

Scoring (``score`` — pure, no I/O)
----------------------------------
Quality gates, each shown pass/fail so a row says WHY it fails:
ROE ≥ ``min_roe``; and for non-financials debt/equity ≤ ``max_de`` and
operating cash flow ≥ ``min_cash_conv`` × net profit; earnings growth > 0.
Banks/NBFCs/insurers (Yahoo sector "Financial Services") skip D/E and cash
conversion — borrowing is their raw material and a lender's operating cash
flow swings negative with its loan book.

Value score = the mean percentile (0 = dearest, 100 = cheapest across the
stocks scored) of whichever of these the stock has, needing ``MIN_FACTORS``:

    pe_vs_sector    P/E ÷ its peers' median P/E           lower = cheaper
    pe_vs_own       P/E ÷ its own median P/E (~4 yrs)       lower = cheaper
    ev_vs_sector    EV/EBITDA ÷ peer median (non-fin)       lower = cheaper
    pb_vs_sector    P/B ÷ peer median (financials only)     lower = cheaper
    fcf_yield       free cash flow ÷ market cap (non-fin)   higher = cheaper
    peg             P/E ÷ earnings growth %                 lower = cheaper

Peers are the Yahoo INDUSTRY when it has ``MIN_PEERS`` positive values among the
stocks scored, else the SECTOR (``_peer_medians``; the factor names keep "sector");
loss-makers have no P/E and take no P/E factor. Earnings growth is Yahoo's
latest quarter year-on-year, so PEG is noisy — one factor of six.

Graham number √(22.5 × EPS × book value/share) and the margin of safety
(Graham − price) ÷ Graham are shown beside the score, not in it: 22.5 is
P/E 15 × P/B 1.5, far under what Indian large caps usually trade at, so
few names clear it.
"""

import json
import math
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from trading_app.app.utils.logger import logger

# Fundamentals move quarterly; a week keeps an All-NSE list (2,657 Screener
# pages, ~2 h at the polite pace) from being re-read every day. The PRICE is
# not held to that: ``screen`` re-prices every row from the local daily file.
CACHE_TTL = 7 * 24 * 3600
WORKERS = 3              # Screener is paced one request at a time anyway
MIN_PEERS = 3
MIN_FACTORS = 3
MIN_OWN_PE_DAYS = 250        # a year of daily P/E before "own history" counts
OWN_PE_YEARS = 5

FINANCIAL_SECTORS = {'Financial Services'}

DEFAULT_GATES = {
    'min_roe': 15.0,          # %
    'max_de': 0.5,            # ×
    'min_cash_conv': 0.8,     # operating cash flow ÷ net profit
    'mos': 20.0,              # % margin of safety under fair value for the buy price
}

UNIVERSES = ('fno', 'nifty500', 'all')

# Factor -> True when a HIGHER value is cheaper.
FACTORS = {
    'pe_vs_sector': False,
    'pe_vs_own':    False,
    'ev_vs_sector': False,
    'pb_vs_sector': False,
    'fcf_yield':    True,
    'peg':          False,
}


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(v: Any) -> Optional[float]:
    f = _finite(v)
    return f if f is not None and f > 0 else None


# ── scoring (pure) ───────────────────────────────────────────────────────

def _percentiles(values: Dict[str, float], higher_is_cheaper: bool) -> Dict[str, float]:
    """symbol -> 0..100, 100 = cheapest. Ties share their mean rank."""
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: kv[1], reverse=not higher_is_cheaper)
    # items[0] is the DEAREST; walk groups of equal value.
    n = len(items)
    out: Dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2.0
        pct = 100.0 if n == 1 else rank / (n - 1) * 100.0
        for k in range(i, j + 1):
            out[items[k][0]] = pct
        i = j + 1
    return out


def _peer_medians(records: List[Dict[str, Any]], field: str,
                  only: Optional[Callable[[Dict[str, Any]], bool]] = None
                  ) -> Dict[str, Dict[str, Any]]:
    """symbol -> {'median', 'group'}: the stock's INDUSTRY when that has
    MIN_PEERS values, else its SECTOR, else nothing.

    Yahoo's sectors are too coarse to compare against on their own:
    "Financial Services" holds PSU banks at P/E ~6 and insurers / AMCs at 40+,
    so against the sector every bank read as deep value.
    """
    by_ind: Dict[str, List[float]] = {}
    by_sec: Dict[str, List[float]] = {}
    for r in records:
        if only and not only(r):
            continue
        v = _pos(r.get(field))
        if v is None:
            continue
        if r.get('industry'):
            by_ind.setdefault(r['industry'], []).append(v)
        if r.get('sector'):
            by_sec.setdefault(r['sector'], []).append(v)
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        if only and not only(r):
            continue
        ind, sec = r.get('industry'), r.get('sector')
        if ind and len(by_ind.get(ind, [])) >= MIN_PEERS:
            out[r['symbol']] = {'median': median(by_ind[ind]), 'group': ind}
        elif sec and len(by_sec.get(sec, [])) >= MIN_PEERS:
            out[r['symbol']] = {'median': median(by_sec[sec]), 'group': sec}
    return out


def is_financial(r: Dict[str, Any]) -> bool:
    return r.get('sector') in FINANCIAL_SECTORS


def gates(r: Dict[str, Any], g: Dict[str, float]) -> Dict[str, Optional[bool]]:
    """Each quality gate: True pass, False fail, None no data / not applied."""
    fin = is_financial(r)
    roe = _finite(r.get('roe'))
    de = _finite(r.get('de'))
    ocf = _finite(r.get('ocf'))
    ni = _finite(r.get('net_income'))
    growth = _finite(r.get('earnings_growth'))
    out: Dict[str, Optional[bool]] = {
        'roe': None if roe is None else roe * 100 >= g['min_roe'],
        'growth': None if growth is None else growth > 0,
        'de': None, 'cash': None,
    }
    if not fin:
        out['de'] = None if de is None else de <= g['max_de']
        if ocf is not None and ni is not None:
            # A loss-maker fails cash conversion outright: there is no profit
            # for the cash to back.
            out['cash'] = ni > 0 and ocf >= g['min_cash_conv'] * ni
    return out


def score(records: List[Dict[str, Any]], gate_cfg: Optional[Dict[str, float]] = None
          ) -> List[Dict[str, Any]]:
    """Rows for the grid, one per record, with factors, gates and the score."""
    g = {**DEFAULT_GATES, **(gate_cfg or {})}
    pe_med = _peer_medians(records, 'pe')
    ev_med = _peer_medians(records, 'ev_ebitda', only=lambda r: not is_financial(r))
    pb_med = _peer_medians(records, 'pb', only=is_financial)

    rows: List[Dict[str, Any]] = []
    for r in records:
        fin = is_financial(r)
        sym = r['symbol']
        sector = r.get('sector')
        pe = _pos(r.get('pe'))
        f: Dict[str, Optional[float]] = {k: None for k in FACTORS}
        if pe is not None and sym in pe_med:
            f['pe_vs_sector'] = pe / pe_med[sym]['median']
        own = _pos(r.get('own_pe_median'))
        if pe is not None and own is not None:
            f['pe_vs_own'] = pe / own
        if fin:
            pb = _pos(r.get('pb'))
            if pb is not None and sym in pb_med:
                f['pb_vs_sector'] = pb / pb_med[sym]['median']
        else:
            ev = _pos(r.get('ev_ebitda'))
            if ev is not None and sym in ev_med:
                f['ev_vs_sector'] = ev / ev_med[sym]['median']
            fcf, mcap = _finite(r.get('fcf')), _pos(r.get('market_cap'))
            if fcf is not None and mcap is not None:
                f['fcf_yield'] = fcf / mcap * 100
        growth = _finite(r.get('earnings_growth'))
        if pe is not None and growth is not None and growth > 0:
            f['peg'] = pe / (growth * 100)

        eps, bvps, price = _pos(r.get('eps')), _pos(r.get('book_value')), _pos(r.get('price'))
        graham = math.sqrt(22.5 * eps * bvps) if (eps and bvps) else None
        margin = (graham - price) / graham * 100 if (graham and price) else None

        # Fair value = EPS × the LOWER of the peer and own-history P/E (either
        # alone when only one exists): what the stock is worth if it simply
        # re-rates to its usual multiple, read conservatively. Buy below =
        # that less the margin of safety. A loss-maker has neither.
        peer_pe = (pe_med.get(sym) or {}).get('median')
        fair_pe = min([v for v in (peer_pe, own) if v]) if (peer_pe or own) else None
        fair_value = eps * fair_pe if (eps and fair_pe) else None
        buy_below = fair_value * (1 - g['mos'] / 100) if fair_value else None
        upside = (fair_value / price - 1) * 100 if (fair_value and price) else None

        gt = gates(r, g)
        applied = [v for v in gt.values() if v is not None]
        rows.append({
            'symbol': r['symbol'],
            'company': r.get('company'),
            'sector': sector,
            'industry': r.get('industry'),
            'financial': fin,
            'price': price,
            'price_date': r.get('price_date'),
            'market_cap_cr': (_pos(r.get('market_cap')) or 0) / 1e7 or None,
            'pe': pe,
            'pe_sector': (pe_med.get(sym) or {}).get('median'),
            'peer_group': (pe_med.get(sym) or {}).get('group'),
            'pe_own': own,
            'own_pe_years': r.get('own_pe_years'),
            'pb': _pos(r.get('pb')),
            'roce': _finite(r.get('roce')),
            'source': r.get('source') or 'yahoo',
            'growth_basis': r.get('growth_basis') or 'quarter',
            'ev_ebitda': None if fin else _pos(r.get('ev_ebitda')),
            'roe': None if _finite(r.get('roe')) is None else _finite(r.get('roe')) * 100,
            'de': None if fin else _finite(r.get('de')),
            'cash_conv': (None if fin or not _finite(r.get('net_income'))
                          or _finite(r.get('net_income')) <= 0 or _finite(r.get('ocf')) is None
                          else _finite(r.get('ocf')) / _finite(r.get('net_income'))),
            'earnings_growth': None if growth is None else growth * 100,
            'div_yield': _finite(r.get('div_yield')),
            'graham': graham,
            'fair_pe': fair_pe,
            'fair_value': fair_value,
            'buy_below': buy_below,
            'upside': upside,
            'in_buy_zone': bool(buy_below and price and price <= buy_below),
            'margin': margin,
            'factors': f,
            'gates': gt,
            # Passes = every gate that could be judged passed, and at least two
            # could be — a row with no data is not "quality".
            'quality': bool(applied) and len(applied) >= 2 and all(applied),
            'fetched_at': r.get('fetched_at'),
        })

    for name, higher in FACTORS.items():
        vals = {row['symbol']: row['factors'][name] for row in rows
                if row['factors'][name] is not None}
        pct = _percentiles(vals, higher)
        for row in rows:
            row.setdefault('pct', {})[name] = pct.get(row['symbol'])

    for row in rows:
        have = [v for v in row['pct'].values() if v is not None]
        row['n_factors'] = len(have)
        row['value_score'] = round(sum(have) / len(have), 1) if len(have) >= MIN_FACTORS else None

    for row in rows:
        row.update(rate(row))
    rows.sort(key=lambda x: (-x['rating_tier'], -(x['value_score'] or 0)))
    for i, row in enumerate(rows, 1):
        row['rank'] = i
    return rows


# ── rating ───────────────────────────────────────────────────────────────

RATINGS = {5: 'Very Best', 4: 'Good', 3: 'Fair', 2: 'Expensive', 1: 'Avoid', 0: 'Not rated'}
VERY_BEST_SCORE = 70      # value score at least
GOOD_SCORE = 60
EXPENSIVE_SCORE = 30      # value score under this, or …
EXPENSIVE_UPSIDE = -20    # … the price this far over fair value
SUSPECT_UPSIDE = 150      # more upside than this is usually a data artefact (one-off profit)
MIN_FACTORS_BEST = 4      # Very Best needs at least this many readings


def rate(row: Dict[str, Any]) -> Dict[str, Any]:
    """A screen verdict per row — 'rating_tier' 0-5, 'rating', and 'rating_why'
    (every reason it is not one tier higher). Cheap AND sound AND under its
    buy price is Very Best; it is a ranking of the numbers, not advice."""
    why: List[str] = []
    score_v, ups = row.get('value_score'), row.get('upside')
    fails = [k for k, v in (row.get('gates') or {}).items() if v is False]
    eps_ok = row.get('pe') is not None

    if not eps_ok:
        return _rating(1, ['Loss-making: no P/E, no fair value'])
    if len(fails) >= 2:
        return _rating(1, [f"Fails {len(fails)} quality checks ({', '.join(fails)})"])
    if score_v is None:
        return _rating(0, [f"Only {row.get('n_factors', 0)} valuation readings (needs {MIN_FACTORS})"])
    if score_v < EXPENSIVE_SCORE or (ups is not None and ups < EXPENSIVE_UPSIDE):
        return _rating(2, [f"Score {score_v:.0f}" + (f", {ups:.0f}% vs fair value" if ups is not None else '')])

    suspect = ups is not None and ups > SUSPECT_UPSIDE
    if suspect:
        why.append(f"Upside {ups:.0f}% looks too good — check for a one-off profit or bad data")
    if not row.get('quality'):
        why.append('Fails a quality check' + (f" ({', '.join(fails)})" if fails else ' (too little data)'))
    good = score_v >= GOOD_SCORE and row.get('quality') and ups is not None and ups >= 0 and not suspect
    if not good:
        if score_v < GOOD_SCORE:
            why.append(f"Score {score_v:.0f} (Good needs {GOOD_SCORE})")
        if ups is None:
            why.append('No fair value')
        elif ups < 0:
            over = (1 / (1 + ups / 100) - 1) * 100       # price over fair value, as a share of fair value
            why.append(f"Price {over:.1f}% above fair value" if over < 1 else f"Price {over:.0f}% above fair value")
        return _rating(3, why)

    best = (score_v >= VERY_BEST_SCORE and row.get('in_buy_zone')
            and row.get('n_factors', 0) >= MIN_FACTORS_BEST)
    if best:
        disc = (1 - 1 / (1 + ups / 100)) * 100      # price below fair value, as a share of fair value
        return _rating(5, [f"Score {score_v:.0f}, passes quality, price {disc:.0f}% under fair value "
                           f"({ups:.0f}% upside)"])
    if score_v < VERY_BEST_SCORE:
        why.append(f"Score {score_v:.0f} (Very Best needs {VERY_BEST_SCORE})")
    if not row.get('in_buy_zone'):
        why.append('Price above the buy-below level')
    if row.get('n_factors', 0) < MIN_FACTORS_BEST:
        why.append(f"Only {row.get('n_factors')} readings (Very Best needs {MIN_FACTORS_BEST})")
    return _rating(4, why)


def _rating(tier: int, why: List[str]) -> Dict[str, Any]:
    return {'rating_tier': tier, 'rating': RATINGS[tier], 'rating_why': why}


# ── own P/E history ──────────────────────────────────────────────────────

def own_pe_median(closes, steps) -> Optional[Dict[str, float]]:
    """Median daily close ÷ TTM EPS in force, over the span the steps cover.

    ``closes``: pandas Series of daily closes on a DatetimeIndex.
    ``steps``: [(effective datetime, TTM EPS)] ascending, from ``_eps_steps``.
    """
    if closes is None or len(closes) == 0 or not steps:
        return None
    import pandas as pd
    eff = pd.Series([v for _, v in steps],
                    index=pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None).normalize()
                                            if pd.Timestamp(d).tzinfo else pd.Timestamp(d).normalize()
                                            for d, _ in steps]))
    eff = eff[~eff.index.duplicated(keep='last')].sort_index()
    idx = closes.index.normalize() if hasattr(closes.index, 'normalize') else closes.index
    c = pd.Series(closes.values, index=idx)
    # The stock files mix 00:00 and 05:30 stamps, so two bars can share a day.
    c = c[~c.index.duplicated(keep='last')].sort_index()
    c = c[c.index >= eff.index[0]]
    if c.empty:
        return None
    eps = eff.reindex(c.index, method='ffill')
    pe = (c / eps)[eps > 0].dropna()
    if len(pe) < MIN_OWN_PE_DAYS:
        return None
    return {'median': float(pe.median()),
            'years': round((pe.index[-1] - pe.index[0]).days / 365.25, 1)}


# ── fetch + cache ────────────────────────────────────────────────────────

def _db_path() -> str:
    from trading_app.service.watchlist_service import DB_PATH
    return DB_PATH


@contextmanager
def _connect():
    conn = sqlite3.connect(_db_path(), timeout=15)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS value_fundamentals (
                                symbol TEXT PRIMARY KEY,
                                data TEXT NOT NULL,
                                fetched_at REAL NOT NULL)""")
            yield conn
    finally:
        conn.close()


def read_cache(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    with _connect() as conn:
        for i in range(0, len(symbols), 500):
            chunk = symbols[i:i + 500]
            marks = ','.join('?' * len(chunk))
            for sym, data, at in conn.execute(
                    f"SELECT symbol, data, fetched_at FROM value_fundamentals "
                    f"WHERE symbol IN ({marks})", chunk):
                rec = json.loads(data)
                rec['fetched_at'] = at
                out[sym] = rec
    return out


def store(rec: Dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO value_fundamentals (symbol, data, fetched_at) "
                     "VALUES (?, ?, ?)", (rec['symbol'], json.dumps(rec), rec['fetched_at']))


def _record_from_info(symbol: str, info: Dict[str, Any]) -> Dict[str, Any]:
    de = _finite(info.get('debtToEquity'))
    # INFY and HCLTECH file their statements in USD while the quote is INR:
    # Yahoo then divides an INR enterprise value by a USD EBITDA (EV/EBITDA
    # 916) and a USD free cash flow by an INR market cap. Ratios of two
    # statement figures (ROE, cash conversion) are unaffected; the mixed ones
    # are dropped rather than converted at a guessed rate.
    fin_ccy, quote_ccy = info.get('financialCurrency'), info.get('currency')
    mixed = bool(fin_ccy and quote_ccy and fin_ccy != quote_ccy)
    return {
        'symbol': symbol,
        'company': info.get('longName') or info.get('shortName'),
        'sector': info.get('sector'),
        'industry': info.get('industry'),
        'price': _finite(info.get('currentPrice') or info.get('regularMarketPrice')),
        'pe': _finite(info.get('trailingPE')),
        'eps': _finite(info.get('trailingEps')),
        'pb': _finite(info.get('priceToBook')),
        'book_value': _finite(info.get('bookValue')),
        'roe': _finite(info.get('returnOnEquity')),
        'de': None if de is None else de / 100.0,          # Yahoo: percent
        'ev_ebitda': None if mixed else _finite(info.get('enterpriseToEbitda')),
        'fcf': None if mixed else _finite(info.get('freeCashflow')),
        'mixed_currency': fin_ccy if mixed else None,
        'ocf': _finite(info.get('operatingCashflow')),
        'net_income': _finite(info.get('netIncomeToCommon')),
        'earnings_growth': _finite(info.get('earningsGrowth')),
        'market_cap': _finite(info.get('marketCap')),
        'div_yield': _finite(info.get('dividendYield')),    # Yahoo: already %
    }


def _own_pe(symbol: str, steps) -> Optional[Dict[str, float]]:
    """Own P/E over the last OWN_PE_YEARS from the stock file's closes."""
    if not steps:
        return None
    from trading_app.filters.candle_store import _load_disk
    import pandas as pd
    entry = _load_disk(symbol)
    if entry is None:
        return None
    closes = entry['df']['close']
    cutoff = closes.index[-1] - pd.Timedelta(days=int(OWN_PE_YEARS * 365.25))
    return own_pe_median(closes[closes.index >= cutoff], steps)


def fetch_screener(symbol: str) -> Optional[Dict[str, Any]]:
    """Screener.in: the primary source (see service/screener_in.py for why)."""
    from trading_app.service import screener_in
    try:
        page = screener_in.fetch_page(symbol)
    except screener_in.NotFound:
        return None
    rec = screener_in.parse_page(symbol, page)
    if rec.get('price') is None:
        return None
    rec['own_pe_median'] = rec['own_pe_years'] = None
    try:
        own = _own_pe(symbol, screener_in.eps_steps(page))
        if own:
            rec['own_pe_median'], rec['own_pe_years'] = own['median'], own['years']
    except Exception as e:
        logger.info(f"[ValueScreen] {symbol}: no own P/E history: {e}")
    rec['fetched_at'] = time.time()
    return rec


def fetch_yahoo(symbol: str) -> Optional[Dict[str, Any]]:
    """Yahoo: the fallback when Screener has no page for the symbol."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f'{symbol}.NS')
        info = ticker.info or {}
    except Exception as e:
        logger.warning(f"[ValueScreen] {symbol}: Yahoo info failed: {e}")
        return None
    if not info or info.get('quoteType') not in (None, 'EQUITY'):
        return None
    rec = _record_from_info(symbol, info)
    rec['source'] = 'yahoo'
    rec['own_pe_median'] = rec['own_pe_years'] = None
    try:
        from trading_app.service.watchlist_service import _eps_steps
        own = _own_pe(symbol, _eps_steps(ticker, rec['eps']))
        if own:
            rec['own_pe_median'], rec['own_pe_years'] = own['median'], own['years']
    except Exception as e:
        logger.info(f"[ValueScreen] {symbol}: no own P/E history: {e}")
    rec['fetched_at'] = time.time()
    return rec


def fetch_one(symbol: str) -> Optional[Dict[str, Any]]:
    """One stock: Screener.in, else Yahoo — but Yahoo ONLY when Screener has
    no page for it. A throttled or failed Screener read returns None, so the
    cached record (if any) stays rather than being swapped for Yahoo's
    reported-TTM numbers."""
    from trading_app.service.screener_in import Unavailable
    try:
        rec = fetch_screener(symbol)
    except Unavailable:
        raise                          # the refresh stops and says so
    except Exception as e:
        logger.warning(f"[ValueScreen] {symbol}: Screener failed, keeping the cache: {e}")
        return None
    return rec or fetch_yahoo(symbol)


def universe(kind: str) -> List[str]:
    if kind == 'nifty500':
        from trading_app.service.dynamic_constituents import DynamicConstituentsService
        syms = DynamicConstituentsService.get_constituents('NIFTY 500')
    elif kind == 'all':
        # Every NSE equity the app knows (the list the nightly stock-file
        # prewarm walks; ETFs and funds already filtered out there).
        from trading_app.filters.stock_list_store import EQUITY_LIST_FILE
        with open(EQUITY_LIST_FILE) as fh:
            syms = json.load(fh).get('symbols') or []
    else:
        from trading_app.filters.stock_list_store import FO_LIST_FILE
        with open(FO_LIST_FILE) as fh:
            syms = json.load(fh).get('symbols') or []
    return sorted({s.strip().upper() for s in syms if s and s.strip()})


# ── background refresh ───────────────────────────────────────────────────

_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()


def _running() -> Optional[Dict[str, Any]]:
    return next((t for t in _tasks.values() if t['status'] == 'running'), None)


def start_refresh(kind: str, force: bool = False,
                  fetch: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                  symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fetch every stale symbol of the universe in a thread; one task at a time."""
    # Looked up at call time, not bound as a default, so a test's patch of
    # fetch_one is what runs (a bound default once let a route test reach Yahoo).
    fetch = fetch or fetch_one
    with _tasks_lock:
        busy = _running()
        if busy:
            return dict(busy)
        syms = symbols if symbols is not None else universe(kind)
        cached = read_cache(syms)
        now = time.time()
        todo = [s for s in syms if force or now - (cached.get(s, {}).get('fetched_at') or 0) > CACHE_TTL]
        task = {'id': uuid.uuid4().hex[:12], 'status': 'running', 'universe': kind,
                'total': len(todo), 'done': 0, 'failed': 0, 'started': now}
        _tasks[task['id']] = task

    def run():
        # Not a `with` block: on an error its exit would wait for every queued
        # symbol (each paced 3 s) before the task could say it had stopped.
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        try:
            for sym, rec in zip(todo, pool.map(fetch, todo)):
                if rec:
                    store(rec)
                else:
                    task['failed'] += 1
                task['done'] += 1
            task['status'] = 'done'
        except Exception as e:
            pool.shutdown(wait=False, cancel_futures=True)
            from trading_app.service.screener_in import Unavailable
            if isinstance(e, Unavailable):
                logger.warning(f"[ValueScreen] {kind}: stopped, {e}")
                task['status'] = 'error'
                task['error'] = (f"{e} — try again later. {task['done']} of {task['total']} "
                                 f"were fetched; the rest keep their cached data.")
                task['finished'] = time.time()
                return
            logger.error(f"[ValueScreen] refresh failed: {e}")
            task['status'] = 'error'
            task['error'] = str(e)
        finally:
            pool.shutdown(wait=False)
        task['finished'] = time.time()
        logger.info(f"[ValueScreen] {kind}: {task['done']} fetched, {task['failed']} failed")

    if todo:
        threading.Thread(target=run, name='value-screen-refresh', daemon=True).start()
    else:
        task['status'] = 'done'
    return dict(task)


def task_status(task_id: str) -> Optional[Dict[str, Any]]:
    t = _tasks.get(task_id)
    return dict(t) if t else None


# ── current price ────────────────────────────────────────────────────────

_close_memo: Dict[str, Any] = {}


def latest_close(symbol: str) -> Optional[Dict[str, Any]]:
    """{'close', 'date'} of the last bar in the local daily file, memoised on
    the file's mtime so a page load of 2,657 stocks costs one stat each."""
    import os
    from trading_app.filters import candle_store as cs
    path = cs._file_path(symbol)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _close_memo.get(symbol)
    if hit and hit[0] == mtime:
        return hit[1]
    entry = cs._load_disk(symbol)
    val = None
    if entry is not None:
        df = entry['df']
        close = _pos(df['close'].iloc[-1])
        if close:
            val = {'close': close, 'date': df.index[-1].strftime('%Y-%m-%d')}
    _close_memo[symbol] = (mtime, val)
    return val


def reprice(rec: Dict[str, Any], quote: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A copy of a cached record moved to a newer close: P/E, P/B, market cap,
    EV/EBITDA and dividend yield follow the price; EPS, book value and the
    statements do not. A close from before the fetch is ignored (the fetched
    price is newer)."""
    out = dict(rec)
    old = _pos(rec.get('price'))
    if not quote or not old:
        return out
    fetched_day = time.strftime('%Y-%m-%d', time.localtime(rec.get('fetched_at') or 0))
    if quote['date'] < fetched_day:
        return out
    k = quote['close'] / old
    out['price'], out['price_date'] = quote['close'], quote['date']
    for f in ('pe', 'pb', 'market_cap'):
        if _finite(rec.get(f)) is not None:
            out[f] = rec[f] * k
    if _finite(rec.get('div_yield')) is not None:
        out['div_yield'] = rec['div_yield'] / k
    mcap, borrow, op = rec.get('market_cap'), rec.get('borrowings_cr'), rec.get('op_profit_cr')
    if mcap and op and op > 0:
        out['ev_ebitda'] = (mcap * k / 1e7 + (borrow or 0)) / op       # crore throughout
    elif _finite(rec.get('ev_ebitda')) is not None:
        out['ev_ebitda'] = rec['ev_ebitda'] * k      # Yahoo record: EV ≈ market cap
    return out


def screen(kind: str, gate_cfg: Optional[Dict[str, float]] = None,
           symbols: Optional[List[str]] = None, quotes: Optional[Callable] = None
           ) -> Dict[str, Any]:
    """Score whatever is cached for the universe, at today's close. Never
    fetches from the network."""
    syms = symbols if symbols is not None else universe(kind)
    cached = read_cache(syms)
    quotes = quotes or latest_close
    rows = score([reprice(r, quotes(r['symbol'])) for r in cached.values()], gate_cfg)
    stamps = [r['fetched_at'] for r in cached.values() if r.get('fetched_at')]
    busy = _running()
    return {
        'success': True,
        'universe': kind,
        'total': len(syms),
        'cached': len(cached),
        'oldest': min(stamps) if stamps else None,
        'rows': rows,
        'task': dict(busy) if busy else None,
        'gates': {**DEFAULT_GATES, **(gate_cfg or {})},
    }
