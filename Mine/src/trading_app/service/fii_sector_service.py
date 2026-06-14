"""
Sector-wise FII/FPI investment data from CDSL.

Data source: CDSL fortnightly sector-wise FPI investment report
URL pattern: https://www.cdslindia.com/publications/FII/FortnightlySecWisePages/{Month} {Day}, {Year}.html
Published every 15th and end-of-month (since April 30, 2022).

Columns stored per sector per period:
  sector, auc_inr_cr, net_invest_inr_cr, net_invest_prev_inr_cr,
  period_label, prev_period_label, date (= CDSL report date)
"""

import calendar
import os
import sqlite3
import logging
import time
import threading
from datetime import date, datetime, timedelta
from io import StringIO
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..', 'oi_data.db'))
_CDSL_INDEX  = 'https://www.cdslindia.com/Publications/ForeignPortInvestor.html'
_CDSL_BASE   = 'https://www.cdslindia.com/publications/FII/FortnightlySecWisePages/'
_BULK_START  = date(2022, 4, 30)   # earliest known CDSL report

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.cdslindia.com/',
}


class FIISectorService:
    """Fetch, cache and store sector-wise FPI data from CDSL fortnightly reports."""

    _cache: Dict[str, Any] = {}
    _cache_lock = threading.Lock()
    _TTL = 3600 * 12  # 12 hours

    _bulk_lock = threading.Lock()
    _bulk_status: Dict[str, Any] = {
        'running': False, 'fetched': 0, 'skipped': 0,
        'failed': 0, 'total': 0, 'done': False,
    }

    @classmethod
    def get_bulk_status(cls) -> Dict[str, Any]:
        """Thread-safe snapshot of bulk-fetch progress."""
        with cls._bulk_lock:
            return dict(cls._bulk_status)

    @classmethod
    def _set_bulk_status(cls, **kwargs) -> None:
        with cls._bulk_lock:
            cls._bulk_status.update(kwargs)

    @classmethod
    def _init_bulk_status(cls, **kwargs) -> None:
        with cls._bulk_lock:
            cls._bulk_status = {
                'running': False, 'fetched': 0, 'skipped': 0,
                'failed': 0, 'total': 0, 'done': False,
                **kwargs,
            }

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def get_sector_fpi_data(cls, period: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return sector FPI data for a specific period (YYYY-MM-DD) or the latest.
        If period is given, always loads from DB (no live fetch).
        """
        if period:
            return cls._load_from_db(date_filter=period)

        with cls._cache_lock:
            if 'data' in cls._cache:
                if datetime.now().timestamp() - cls._cache.get('ts', 0) < cls._TTL:
                    return cls._cache['data']

        rows = cls._fetch_cdsl()
        if not rows:
            logger.warning('FIISector: CDSL fetch failed — loading from SQLite')
            rows = cls._load_from_db()

        with cls._cache_lock:
            cls._cache['data'] = rows
            cls._cache['ts'] = datetime.now().timestamp()

        return rows

    @classmethod
    def get_periods(cls) -> List[str]:
        """Return list of CDSL report dates (YYYY-MM-DD) stored in DB, newest first."""
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                cur = conn.execute(
                    'SELECT DISTINCT date FROM fii_sector_limits ORDER BY date DESC'
                )
                return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f'FIISector.get_periods: {e}')
            return []

    @classmethod
    def get_all_data(cls) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return full rows for every stored period.
        Shape: {YYYY-MM-DD: [{sector, auc_inr_cr, net_invest_inr_cr, ...}, ...], ...}
        """
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    '''SELECT date, sector, auc_inr_cr, net_invest_inr_cr,
                              net_invest_prev_inr_cr, period_label, prev_period_label
                       FROM fii_sector_limits
                       ORDER BY date DESC, auc_inr_cr DESC'''
                )
                result: Dict[str, List[Dict]] = {}
                for r in cur.fetchall():
                    d = r['date']
                    if d not in result:
                        result[d] = []
                    result[d].append({
                        'sector':                 r['sector'],
                        'auc_inr_cr':             r['auc_inr_cr'],
                        'net_invest_inr_cr':      r['net_invest_inr_cr'],
                        'net_invest_prev_inr_cr': r['net_invest_prev_inr_cr'],
                        'period':                 r['period_label'] or '',
                        'prev_period':            r['prev_period_label'] or '',
                        'updated':                d,
                    })
                return result
        except Exception as e:
            logger.error(f'FIISector.get_all_data: {e}')
            return {}

    @classmethod
    def get_trend_data(cls, n_periods: Optional[int] = None) -> Dict[str, Any]:
        """
        Return NI and AUC data for all (or last n_periods) × all sectors.
        Returns {periods: [...], sectors: {name: {date: ni_value}}, holdings: {name: {date: auc_value}}}
        """
        limit_sql = f'LIMIT {n_periods}' if n_periods else ''
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    f'''SELECT date, sector, net_invest_inr_cr, auc_inr_cr
                       FROM fii_sector_limits
                       WHERE date IN (
                           SELECT DISTINCT date FROM fii_sector_limits
                           ORDER BY date DESC {limit_sql}
                       )
                       ORDER BY date DESC, sector'''
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.error(f'FIISector.get_trend_data: {e}')
            return {'periods': [], 'sectors': {}, 'holdings': {}}

        periods: List[str] = []
        sectors: Dict[str, Dict[str, float]] = {}
        holdings: Dict[str, Dict[str, float]] = {}
        for r in rows:
            d, sec, ni, auc = r['date'], r['sector'], r['net_invest_inr_cr'], r['auc_inr_cr']
            if d not in periods:
                periods.append(d)
            if sec not in sectors:
                sectors[sec] = {}
            sectors[sec][d] = ni or 0.0
            if sec not in holdings:
                holdings[sec] = {}
            holdings[sec][d] = auc or 0.0

        return {'periods': periods, 'sectors': sectors, 'holdings': holdings}

    @classmethod
    def save_snapshot(cls, rows: List[Dict[str, Any]], report_date: Optional[str] = None) -> None:
        """
        Persist rows to SQLite.
        report_date: the CDSL report date (YYYY-MM-DD); defaults to today if not given.
        """
        if not rows:
            return
        use_date = report_date or datetime.now().date().isoformat()
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.executemany(
                    '''INSERT OR REPLACE INTO fii_sector_limits
                       (date, sector, auc_inr_cr, net_invest_inr_cr,
                        net_invest_prev_inr_cr, period_label, prev_period_label)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    [(use_date, r['sector'], r.get('auc_inr_cr'),
                      r.get('net_invest_inr_cr'), r.get('net_invest_prev_inr_cr'),
                      r.get('period'), r.get('prev_period'))
                     for r in rows]
                )
                conn.commit()
            logger.info(f'FIISector: saved {len(rows)} rows for {use_date}')
        except Exception as e:
            logger.error(f'FIISector: DB save failed: {e}', exc_info=True)

    @classmethod
    def check_and_fetch_latest(cls) -> Dict[str, Any]:
        """
        Fetch any new periods published since the last stored date.
        Returns {new_periods: [...], nothing_new: bool}.
        """
        existing = set(cls.get_periods())
        if existing:
            latest_stored = date.fromisoformat(max(existing))
            start = latest_stored + timedelta(days=1)
        else:
            start = _BULK_START

        pending = [d for d in cls.generate_fortnightly_dates(start, date.today())
                   if d.isoformat() not in existing]

        if not pending:
            return {'new_periods': [], 'nothing_new': True}

        new_periods = []
        for d in pending:
            rows = cls._fetch_for_date(d)
            if rows:
                cls.save_snapshot(rows, report_date=d.isoformat())
                new_periods.append(d.isoformat())

        # Invalidate cache so next call returns fresh data
        with cls._cache_lock:
            cls._cache.clear()

        return {'new_periods': new_periods, 'nothing_new': len(new_periods) == 0}

    # ──────────────────────────────────────────────────────────────
    # Bulk fetch (background)
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def start_bulk_fetch(cls) -> bool:
        """
        Kick off background re-fetch of ALL historic periods (overwrites existing DB rows).
        Returns True if a thread was started, False if already running.
        """
        with cls._bulk_lock:
            if cls._bulk_status['running']:
                return False

        all_dates = cls.generate_fortnightly_dates(_BULK_START, date.today())

        cls._init_bulk_status(running=True, total=len(all_dates))
        threading.Thread(
            target=cls._bulk_fetch_thread, args=(all_dates,), daemon=True
        ).start()
        logger.info(f'FIISector bulk: starting full re-fetch of {len(all_dates)} periods')
        return True

    @classmethod
    def _bulk_fetch_thread(cls, dates: List[date]) -> None:
        for d in dates:
            try:
                rows = cls._fetch_for_date(d)
                if rows:
                    cls.save_snapshot(rows, report_date=d.isoformat())
                    with cls._bulk_lock:
                        cls._bulk_status['fetched'] += 1
                else:
                    with cls._bulk_lock:
                        cls._bulk_status['failed'] += 1
            except Exception as e:
                logger.warning(f'FIISector bulk: {d} failed: {e}')
                with cls._bulk_lock:
                    cls._bulk_status['failed'] += 1
            time.sleep(1.5)  # polite rate limit

        cls._set_bulk_status(running=False, done=True)
        # Invalidate cache
        with cls._cache_lock:
            cls._cache.clear()
        logger.info(
            f'FIISector bulk: done — fetched={cls._bulk_status["fetched"]}, '
            f'failed={cls._bulk_status["failed"]}'
        )

    # ──────────────────────────────────────────────────────────────
    # Date / URL helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def generate_fortnightly_dates(start: date, end: date) -> List[date]:
        """Yield the 15th and last day of every month in [start, end]."""
        dates = []
        year, month = start.year, start.month
        while True:
            last_day = calendar.monthrange(year, month)[1]
            for day in (15, last_day):
                d = date(year, month, day)
                if start <= d <= end:
                    dates.append(d)
            if (year, month) == (end.year, end.month):
                break
            month += 1
            if month > 12:
                month = 1
                year += 1
        return dates

    @staticmethod
    def build_report_url(d: date, ext: str = 'html') -> str:
        """Build the CDSL URL for a given date using the primary (comma+space) pattern."""
        month = d.strftime('%B')
        day   = str(d.day)
        year  = str(d.year)
        filename = quote(f'{month} {day}, {year}.{ext}', safe=',')
        return _CDSL_BASE + filename

    @staticmethod
    def _cdsl_url_candidates(d: date) -> List[str]:
        """Return all URL candidates to try for a given date (6 total: 3 patterns × 2 exts).

        CDSL has used at least three filename formats over the years:
          comma+space  → "May 15, 2026.html"  (standard 2026+, some 2025)
          comma-nospace→ "October 15,2025.html" / .htm  (2023-2025)
          space-only   → "January 31 2026.html"  (observed on Jan 31 2026)
        """
        month = d.strftime('%B')
        day   = str(d.day)
        year  = str(d.year)
        patterns = [
            f'{month} {day}, {year}',   # comma + space  (primary)
            f'{month} {day},{year}',    # comma, no space
            f'{month} {day} {year}',    # space only, no comma
        ]
        urls = []
        for pat in patterns:
            for ext in ('html', 'htm'):
                urls.append(_CDSL_BASE + quote(f'{pat}.{ext}', safe=','))
        return urls

    # ──────────────────────────────────────────────────────────────
    # CDSL scraping
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_for_date(cls, d: date) -> List[Dict[str, Any]]:
        """Fetch and parse one fortnightly report, trying all known URL patterns."""
        for url in cls._cdsl_url_candidates(d):
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=20)
                if resp.status_code == 200:
                    rows = cls._parse_cdsl_html(resp.text)
                    if rows:
                        logger.debug(f'FIISector: fetched {d} via {url} — {len(rows)} sectors')
                        return rows
            except Exception as e:
                logger.debug(f'FIISector: {url} → {e}')
        logger.warning(f'FIISector: no data for {d}')
        return []

    @classmethod
    def _fetch_cdsl(cls) -> List[Dict[str, Any]]:
        """Fetch the latest report by first discovering the URL from the CDSL index."""
        try:
            file_url = cls._get_latest_file_url()
            if not file_url:
                logger.error('FIISector: could not find latest CDSL sector file URL')
                return []
            logger.info(f'FIISector: fetching {file_url}')
            resp = requests.get(file_url, headers=_HEADERS, timeout=20)
            if resp.status_code != 200:
                logger.error(f'FIISector: CDSL returned {resp.status_code}')
                return []
            return cls._parse_cdsl_html(resp.text)
        except Exception as e:
            logger.error(f'FIISector: CDSL fetch error: {e}', exc_info=True)
            return []

    @classmethod
    def _get_latest_file_url(cls) -> str:
        try:
            resp = requests.get(_CDSL_INDEX, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'FortnightlySecWisePages' in href:
                    return urljoin(_CDSL_INDEX, href)
        except Exception as e:
            logger.error(f'FIISector: index fetch error: {e}', exc_info=True)
        return ''

    @classmethod
    def _parse_cdsl_html(cls, html: str) -> List[Dict[str, Any]]:
        """Parse CDSL fortnightly sector FPI HTML — dispatches to new or old format parser."""
        import pandas as pd
        try:
            dfs = pd.read_html(StringIO(html), thousands=',')
        except Exception as e:
            logger.debug(f'FIISector: pandas parse error: {e}')
            return []
        if not dfs:
            return []
        df = dfs[0]
        if isinstance(df.columns[0], tuple):
            return cls._parse_tuple_format(df)
        return cls._parse_row_format(df)

    @staticmethod
    def _cdsl_to_float(v) -> float:
        try:
            return float(str(v).replace(',', '').strip())
        except (ValueError, TypeError):
            return 0.0

    _SKIP_SECTORS = {'nan', 'sectors', 'sector', 'grand total', 'total', 'sr. no.', 'none'}

    @classmethod
    def _parse_tuple_format(cls, df) -> List[Dict[str, Any]]:
        """New format (2025+): pandas returns 4-tuple column headers. All rows are sector data.

        Column tuple structure: (group_label, currency_unit, sub_group, leaf_name)
        INR Cr Total columns: col[1] contains 'INR', col[3] == 'Total'
        Positions: AUC prev=13, NI prev=37, NI latest=61, AUC latest=85
        """
        import re
        cols = list(df.columns)
        inr_total_idxs = [
            i for i, col in enumerate(cols)
            if isinstance(col, tuple) and len(col) >= 4
            and 'inr' in str(col[1]).lower()
            and str(col[3]).strip().lower() == 'total'
        ]
        auc_idxs = [i for i in inr_total_idxs if 'auc' in str(cols[i][0]).lower()]
        ni_idxs  = [i for i in inr_total_idxs if 'net investment' in str(cols[i][0]).lower()]

        if not auc_idxs or not ni_idxs:
            logger.debug(f'FIISector _parse_tuple: no auc/ni columns in {len(cols)}-col table')
            return []

        col_auc  = auc_idxs[-1]
        col_ni   = ni_idxs[-1]
        col_ni_p = ni_idxs[-2] if len(ni_idxs) >= 2 else None

        period_label = str(cols[col_ni][0])
        prev_period  = str(cols[col_ni_p][0]) if col_ni_p is not None else ''
        auc_label    = str(cols[col_auc][0])

        updated = datetime.now().date().isoformat()
        m = re.search(r'(\w+)\s+(\d{1,2}),\s*(\d{4})', auc_label)
        if m:
            try:
                d = datetime.strptime(f'{m.group(1)} {m.group(2)}, {m.group(3)}', '%B %d, %Y')
                updated = d.date().isoformat()
            except ValueError:
                pass

        rows = []
        for row_idx in range(len(df)):
            row = df.iloc[row_idx]
            sector = str(row.iloc[1]).strip()
            if not sector or sector.lower() in cls._SKIP_SECTORS:
                continue
            if not any(c.isalpha() for c in sector):
                continue
            auc  = cls._cdsl_to_float(row.iloc[col_auc])
            ni   = cls._cdsl_to_float(row.iloc[col_ni])
            ni_p = cls._cdsl_to_float(row.iloc[col_ni_p]) if col_ni_p is not None else 0.0
            if auc == 0 and ni == 0 and ni_p == 0:
                continue
            rows.append({
                'sector':                 sector,
                'auc_inr_cr':             round(auc, 2),
                'net_invest_inr_cr':      round(ni, 2),
                'net_invest_prev_inr_cr': round(ni_p, 2),
                'period':                 period_label,
                'prev_period':            prev_period,
                'updated':                updated,
            })
        return rows

    @classmethod
    def _parse_row_format(cls, df) -> List[Dict[str, Any]]:
        """Old format: integer column headers. Rows 0-N are labels; then sector data.

        Two sub-variants:
          - 42-col pages (2022-era): 4 groups × 10 cols (5 INR + 5 USD). Total INR = group_start + 4.
          - 98-col pages (mid-2026): 4 groups × 24 cols (12 INR + 12 USD). Total INR = group_start + 11.
        Group size is detected from adjacent group boundaries; offset = group_size // 2 - 1.
        Data start row is detected by scanning for the first row where col 0 is a number (Sr. No.).
        """
        import re
        n_cols = len(df.columns)
        if n_cols < 12:
            return []

        row0 = list(df.iloc[0].values)
        period_groups: List[tuple] = []
        prev_label = None
        for i, v in enumerate(row0):
            label = str(v).strip() if str(v) not in ('nan', 'NaN') else ''
            if label and label != prev_label:
                period_groups.append((label, i))
                prev_label = label

        auc_groups = [(lbl, s) for lbl, s in period_groups if 'auc' in lbl.lower()]
        ni_groups  = [(lbl, s) for lbl, s in period_groups if 'net investment' in lbl.lower()]

        if not auc_groups or not ni_groups:
            return []

        # Detect group size from adjacent boundaries; half cols are INR, last INR col = Total
        all_starts = sorted(s for _, s in period_groups)
        group_size = (all_starts[1] - all_starts[0]) if len(all_starts) >= 2 else (n_cols - 2)
        total_inr_offset = group_size // 2 - 1  # +4 for 10-col, +11 for 24-col

        col_auc  = auc_groups[-1][1] + total_inr_offset
        col_ni   = ni_groups[-1][1]  + total_inr_offset
        col_ni_p = ni_groups[-2][1]  + total_inr_offset if len(ni_groups) >= 2 else None

        if col_auc >= n_cols or col_ni >= n_cols:
            return []

        # Find first data row: first row where col 0 parses as a positive number (Sr. No.)
        data_start = 3
        for i in range(min(7, len(df))):
            try:
                if float(str(df.iloc[i, 0]).strip()) >= 1:
                    data_start = i
                    break
            except (ValueError, TypeError):
                pass

        period_label = ni_groups[-1][0]
        prev_period  = ni_groups[-2][0] if len(ni_groups) >= 2 else ''
        auc_period   = auc_groups[-1][0]

        updated = datetime.now().date().isoformat()
        m = re.search(r'(\w+)\s+(\d{1,2}),\s*(\d{4})', auc_period)
        if m:
            try:
                d = datetime.strptime(f'{m.group(1)} {m.group(2)}, {m.group(3)}', '%B %d, %Y')
                updated = d.date().isoformat()
            except ValueError:
                pass

        rows = []
        for row_idx in range(data_start, len(df)):
            row = df.iloc[row_idx]
            sector = str(row.iloc[1]).strip()
            if not sector or sector.lower() in cls._SKIP_SECTORS:
                continue
            if not any(c.isalpha() for c in sector):
                continue
            auc  = cls._cdsl_to_float(row.iloc[col_auc])
            ni   = cls._cdsl_to_float(row.iloc[col_ni])
            ni_p = cls._cdsl_to_float(row.iloc[col_ni_p]) if col_ni_p is not None else 0.0
            if auc == 0 and ni == 0 and ni_p == 0:
                continue
            rows.append({
                'sector':                 sector,
                'auc_inr_cr':             round(auc, 2),
                'net_invest_inr_cr':      round(ni, 2),
                'net_invest_prev_inr_cr': round(ni_p, 2),
                'period':                 period_label,
                'prev_period':            prev_period,
                'updated':                updated,
            })
        return rows

    # ──────────────────────────────────────────────────────────────
    # SQLite helpers
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def _load_from_db(cls, date_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Load sector rows for a specific date or the latest available date."""
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                if date_filter:
                    cur = conn.execute(
                        '''SELECT sector, auc_inr_cr, net_invest_inr_cr,
                                  net_invest_prev_inr_cr, period_label, prev_period_label, date
                           FROM fii_sector_limits
                           WHERE date = ?
                           ORDER BY auc_inr_cr DESC''',
                        (date_filter,)
                    )
                else:
                    cur = conn.execute(
                        '''SELECT sector, auc_inr_cr, net_invest_inr_cr,
                                  net_invest_prev_inr_cr, period_label, prev_period_label, date
                           FROM fii_sector_limits
                           WHERE date = (SELECT MAX(date) FROM fii_sector_limits)
                           ORDER BY auc_inr_cr DESC'''
                    )
                return [
                    {
                        'sector':                 r['sector'],
                        'auc_inr_cr':             r['auc_inr_cr'],
                        'net_invest_inr_cr':      r['net_invest_inr_cr'],
                        'net_invest_prev_inr_cr': r['net_invest_prev_inr_cr'],
                        'period':                 r['period_label'] or '',
                        'prev_period':            r['prev_period_label'] or '',
                        'updated':                r['date'],
                    }
                    for r in cur.fetchall()
                ]
        except Exception as e:
            logger.error(f'FIISector: DB load failed: {e}', exc_info=True)
            return []

    @classmethod
    def delete_period(cls, date_str: str) -> bool:
        """Delete all rows for a specific date from SQLite and invalidate cache."""
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.execute('DELETE FROM fii_sector_limits WHERE date = ?', (date_str,))
                conn.commit()
            with cls._cache_lock:
                cls._cache.pop(date_str, None)
            logger.info(f'FIISector: deleted period {date_str}')
            return True
        except Exception as e:
            logger.error(f'FIISector: delete_period {date_str}: {e}', exc_info=True)
            return False
