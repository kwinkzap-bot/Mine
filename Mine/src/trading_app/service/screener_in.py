"""Screener.in company pages — the Value screener's primary source.

Why not Yahoo alone: for Indian stocks Yahoo's ``Ticker.info`` is the
REPORTED trailing twelve months, one-offs included (Crompton's ₹700 Cr
write-off read as a loss, P/E blank; Dalmia Bharat's P/E 59.8 against 29.9
on its earnings before exceptional items), its ROE/growth are often blank,
and it files INFY / HCLTECH in USD. Screener's page carries, in INR crore:

* the top ratios — price, market cap, P/E (on profit before exceptional
  items), book value, dividend yield, ROE, ROCE;
* NSE's four-level classification (macro › sector › industry › basic
  industry) — "Private Sector Bank", "NBFC", "Household Appliances" — a far
  better peer group than Yahoo's;
* ~12 years of annual EPS (split / bonus adjusted) and 13 quarters, so the
  stock's own P/E history needs no second source;
* borrowings, equity, cash from operations, free cash flow, operating
  profit, and 3-year compounded profit growth.

One page per stock (``/company/<SYM>/consolidated/``, the standalone page when
the company files no consolidated accounts). robots.txt allows company pages;
requests are paced (``MIN_GAP`` across threads, a shared cool-down on 429/503)
and cached for a day by the caller, so a NIFTY 500 refresh is ~500 requests
over ~25 minutes.

``parse_page`` is pure (HTML in, record out) and is what the tests exercise.
"""

import html as _html
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

BASE = 'https://www.screener.in/company/{sym}/{view}'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130 Safari/537.36')
MIN_GAP = 3.0          # seconds between requests, across every thread
RETRIES = 4
COOL_DOWN = 60         # a 429/503 pauses EVERY thread this long, doubling per retry
CR = 1e7               # ₹ per crore
OUTLIER = 5            # an annual EPS this many × the recent level is not the same company

_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}

_pace_lock = threading.Lock()
_last_request = [0.0]
_blocked_until = [0.0]


class NotFound(Exception):
    pass


class Throttled(Exception):
    pass


class Unavailable(Exception):
    """The whole site is down ('Screener is undergoing maintenance'): no
    point retrying stock after stock, so a refresh stops at once."""


# ── parsing (pure) ───────────────────────────────────────────────────────

def _text(fragment: str) -> str:
    return re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def _num(s: Optional[str]) -> Optional[float]:
    """'1,234.5' / '12%' / '-8.29' / '' → float or None."""
    if s is None:
        return None
    s = s.replace(',', '').replace('%', '').replace('₹', '').replace('Cr.', '').strip()
    if s in ('', '-', '--'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _month_end(label: str) -> Optional[datetime]:
    m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', label)
    if not m:
        return None
    y, mo = int(m.group(2)), _MONTHS[m.group(1)]
    first_next = datetime(y + (mo == 12), mo % 12 + 1, 1)
    return first_next - timedelta(days=1)


def top_ratios(page: str) -> Dict[str, Optional[float]]:
    m = re.search(r'id="top-ratios"(.*?)</ul>', page, re.S)
    out: Dict[str, Optional[float]] = {}
    if not m:
        return out
    for li in re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), re.S):
        name = re.search(r'class="name">(.*?)</span>', li, re.S)
        num = re.search(r'class="number">(.*?)</span>', li, re.S)
        if name:
            out[_text(name.group(1))] = _num(_text(num.group(1))) if num else None
    return out


def table(page: str, section_id: str) -> Tuple[List[str], Dict[str, List[Optional[float]]]]:
    """(column labels, {row name: values}) of a section's data table."""
    sec = re.search(rf'<section[^>]*id="{section_id}"(.*?)</section>', page, re.S)
    if not sec:
        return [], {}
    tb = re.search(r'<table[^>]*class="[^"]*data-table[^"]*"(.*?)</table>', sec.group(1), re.S)
    if not tb:
        return [], {}
    head = re.search(r'<thead>(.*?)</thead>', tb.group(1), re.S)
    cols = [_text(th) for th in re.findall(r'<th[^>]*>(.*?)</th>', head.group(1), re.S)][1:] if head else []
    rows: Dict[str, List[Optional[float]]] = {}
    body = re.search(r'<tbody>(.*?)</tbody>', tb.group(1), re.S)
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', body.group(1) if body else '', re.S):
        cells = [_text(td) for td in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if cells:
            rows[cells[0].rstrip('+').strip()] = [_num(c) for c in cells[1:]]
    return cols, rows


def _row(rows: Dict[str, List[Optional[float]]], *names: str) -> List[Optional[float]]:
    for n in names:
        if n in rows:
            return rows[n]
    return []


def _last(vals: List[Optional[float]]) -> Optional[float]:
    return next((v for v in reversed(vals) if v is not None), None)


def classification(page: str) -> List[str]:
    """NSE's macro › sector › industry › basic industry, from the peers block."""
    sec = re.search(r'<section[^>]*id="peers"(.*?)</section>', page, re.S)
    if not sec:
        return []
    p = re.search(r'<p[^>]*class="sub"[^>]*>(.*?)</p>', sec.group(1), re.S)
    return [_text(a) for a in re.findall(r'<a[^>]*>(.*?)</a>', p.group(1), re.S)][:4] if p else []


def growth(page: str, title: str) -> Dict[str, Optional[float]]:
    """A 'Compounded … Growth' box → {'10 Years': .., '5 Years': .., '3 Years': .., 'TTM': ..}."""
    for tb in re.findall(r'<table[^>]*class="[^"]*ranges-table[^"]*"(.*?)</table>', page, re.S):
        if title not in _text(tb):
            continue
        cells = [_text(td) for td in re.findall(r'<td[^>]*>(.*?)</td>', tb, re.S)]
        return {cells[i].rstrip(':'): _num(cells[i + 1]) for i in range(0, len(cells) - 1, 2)}
    return {}


def eps_steps(page: str) -> List[Tuple[datetime, float]]:
    """[(date it became known, TTM EPS)] — annual EPS from 60 days after each
    fiscal year end, then each quarter's trailing four from 45 days after it.
    Zero rows (years before a company's consolidated accounts) are skipped."""
    steps: Dict[datetime, float] = {}
    cols, rows = table(page, 'profit-loss')
    annual = [(_month_end(label), v) for label, v in zip(cols, _row(rows, 'EPS in Rs'))
              if label != 'TTM' and v]
    annual = [(e, v) for e, v in annual if e]
    # A year before a merger / demerger can carry the old shell's EPS (Dalmia
    # Bharat FY17: 8,800 against ~60 since). Anything more than OUTLIER × the
    # recent level is dropped rather than allowed into the own-P/E median.
    recent = sorted(abs(v) for _e, v in annual[-3:])
    level = recent[len(recent) // 2] if recent else 0
    for end, v in annual:
        if level and abs(v) > OUTLIER * level:
            continue
        steps[end + timedelta(days=60)] = v
    qcols, qrows = table(page, 'quarters')
    q = _row(qrows, 'EPS in Rs')
    for i in range(3, len(q)):
        four = q[i - 3:i + 1]
        end = _month_end(qcols[i]) if i < len(qcols) else None
        if end and all(x is not None for x in four) and any(four):
            steps[end + timedelta(days=45)] = sum(four)
    return sorted(steps.items())


def has_accounts(page: str) -> bool:
    _cols, rows = table(page, 'profit-loss')
    return any(v for v in _row(rows, 'EPS in Rs', 'Net Profit'))


def yoy(series: List[Optional[float]], back: int = 0) -> Optional[float]:
    """% change of a quarter against the same quarter a year before
    (``back`` quarters from the latest). A base at or under zero has no
    meaningful %: a loss turned into a profit reads +100, the reverse -100."""
    i = len(series) - 1 - back
    if i - 4 < 0:
        return None
    now, base = series[i], series[i - 4]
    if now is None or base is None:
        return None
    if base <= 0:
        return 100.0 if now > 0 else (-100.0 if now < base else None)
    return (now / base - 1) * 100


def growth_record(sales_g: Dict[str, Optional[float]], profit_g: Dict[str, Optional[float]],
                  roe_box: Dict[str, Optional[float]], q_sales: List[Optional[float]],
                  q_profit: List[Optional[float]], fy_profit: List[Optional[float]]) -> Dict[str, Any]:
    """The growth / track-record fields the Strength check reads.
    ``fy_profit`` is the full fiscal years only (no TTM column)."""
    last5 = [v for v in fy_profit[-6:] if v is not None]
    ups = sum(1 for a, b in zip(last5, last5[1:]) if b > a)
    return {
        'sales_g3': sales_g.get('3 Years'),
        'sales_g5': sales_g.get('5 Years'),
        'sales_ttm': sales_g.get('TTM'),
        'profit_g3': profit_g.get('3 Years'),
        'profit_g5': profit_g.get('5 Years'),
        'roe_3y': roe_box.get('3 Years'),
        'q_sales_yoy': yoy(q_sales),
        'q_profit_yoy': yoy(q_profit),
        'q_profit_yoy_prev': yoy(q_profit, 1),
        # of the last five fiscal years: how many grew profit on the year
        # before, and whether any was a loss
        'profit_up_years': ups if len(last5) >= 4 else None,
        'profit_years': max(len(last5) - 1, 0),
        'loss_5y': any(v <= 0 for v in last5[-5:]) if last5 else None,
    }


def parse_page(symbol: str, page: str) -> Dict[str, Any]:
    """One Screener page → the Value screener's record (same keys as Yahoo's)."""
    top = top_ratios(page)
    name = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
    cls = classification(page)
    _c, pl = table(page, 'profit-loss')
    _c, bs = table(page, 'balance-sheet')
    _c, cf = table(page, 'cash-flow')
    _c, ratios = table(page, 'ratios')

    price, pe = top.get('Current Price'), top.get('Stock P/E')
    pe = pe if pe and pe > 0 else None
    mcap_cr = top.get('Market Cap')
    profit = _row(pl, 'Net Profit')
    ttm_eps = _last(_row(pl, 'EPS in Rs'))
    # EPS behind Screener's P/E (before exceptional items); a loss-maker has
    # no P/E, so its reported TTM EPS (negative) stands.
    eps = price / pe if (price and pe) else ttm_eps

    equity = (_last(_row(bs, 'Equity Capital')) or 0) + (_last(_row(bs, 'Reserves')) or 0)
    borrow = _last(_row(bs, 'Borrowings', 'Borrowing'))
    # Cash conversion pairs the SAME fiscal year: the last full year's CFO
    # against that year's profit (the P&L's last column is TTM).
    cfo = _row(cf, 'Cash from Operating Activity')
    fy_profit = profit[-2] if len(profit) >= 2 else None
    op_ttm = _last(_row(pl, 'Operating Profit'))
    fcf = _last(_row(cf, 'Free Cash Flow'))
    g = growth(page, 'Compounded Profit Growth')
    g3 = g.get('3 Years') if g.get('3 Years') is not None else g.get('TTM')

    sector = cls[1] if len(cls) > 1 else (cls[0] if cls else None)
    sg = growth(page, 'Compounded Sales Growth')
    roe_box = growth(page, 'Return on Equity')
    _qc, qt = table(page, 'quarters')
    return {
        **growth_record(sg, g, roe_box, _row(qt, 'Sales', 'Revenue'), _row(qt, 'Net Profit'),
                        profit[:-1] if len(profit) > 1 else profit),
        'symbol': symbol,
        'source': 'screener',
        'company': _text(name.group(1)) if name else None,
        'macro': cls[0] if cls else None,
        'sector': sector,
        'industry': cls[3] if len(cls) > 3 else (cls[2] if len(cls) > 2 else None),
        'price': price,
        'pe': pe,
        'eps': eps,
        'pb': (price / top['Book Value']) if (price and top.get('Book Value')) else None,
        'book_value': top.get('Book Value'),
        'roe': None if top.get('ROE') is None else top['ROE'] / 100,
        'roce': top.get('ROCE') if top.get('ROCE') is not None else _last(_row(ratios, 'ROCE %')),
        'de': (borrow / equity) if (borrow is not None and equity > 0) else None,
        # EV ≈ market cap + borrowings (the page has no cash line, so cash-
        # rich companies read a little dearer than they are).
        'ev_ebitda': ((mcap_cr + (borrow or 0)) / op_ttm) if (mcap_cr and op_ttm and op_ttm > 0) else None,
        'fcf': None if fcf is None else fcf * CR,
        'ocf': None if _last(cfo) is None else _last(cfo) * CR,
        'net_income': None if fy_profit is None else fy_profit * CR,
        'earnings_growth': None if g3 is None else g3 / 100,
        'growth_basis': '3y' if g.get('3 Years') is not None else 'ttm',
        'market_cap': None if mcap_cr is None else mcap_cr * CR,
        'div_yield': top.get('Dividend Yield'),
        'mixed_currency': None,
        # kept so EV/EBITDA can follow a newer price (value_screen.reprice)
        'borrowings_cr': borrow,
        'op_profit_cr': op_ttm,
    }


# ── fetching ─────────────────────────────────────────────────────────────

def _get(url: str) -> str:
    """One paced GET. Screener answers bursts with 429 / 503 and no
    Retry-After, so a throttle pauses every thread (``_blocked_until``), not
    just the one that saw it."""
    import requests
    for attempt in range(RETRIES):
        with _pace_lock:
            wait = max(_last_request[0] + MIN_GAP, _blocked_until[0]) - time.time()
            if wait > 0:
                time.sleep(wait)
            _last_request[0] = time.time()
        r = requests.get(url, headers={'User-Agent': UA}, timeout=20)
        if r.status_code == 404:
            raise NotFound(url)
        if r.status_code == 503 and 'maintenance' in (r.text or '').lower():
            raise Unavailable('Screener.in is down for maintenance')
        if r.status_code in (429, 503) or r.status_code >= 500:
            with _pace_lock:
                _blocked_until[0] = max(_blocked_until[0], time.time() + COOL_DOWN * 2 ** attempt)
            continue
        r.raise_for_status()
        return r.text
    raise Throttled(f'{url}: still throttled after {RETRIES} tries')


def fetch_page(symbol: str) -> str:
    """The consolidated page, or the standalone one when there are no
    consolidated accounts."""
    page = _get(BASE.format(sym=symbol, view='consolidated/'))
    # Some consolidated pages carry the accounts but a blank top-ratios list
    # (BANDHANBNK, 2026-11-19: no price, no P/E), which used to send the
    # stock to Yahoo; the standalone page has them.
    if has_accounts(page) and top_ratios(page).get('Current Price') is not None:
        return page
    return _get(BASE.format(sym=symbol, view=''))
