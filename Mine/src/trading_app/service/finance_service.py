"""
Finance dashboard — reads the manually-maintained 'Mine' Google Sheet.

The sheet is updated by hand each day and has six tabs:
  Collections  — per-loan daily/weekly payment series, stored as repeating
                 2-row blocks: a header row of dates, then a row of amounts.
  Track        — one row per daily/weekly loan (principal, EMIs, collected).
  Monthly      — one row per monthly interest-only loan.
  Invested     — several small tables side by side; headline capital figures
                 plus a physical cash-denomination tally.
  Withdraw     — one row per cash withdrawal (date, amount, reason).
  Merged Data  — month- and year-wise collection/profit rollups, already
                 computed by sheet formulas.

Access is read-only via a service account (GOOGLE_SHEETS_CREDENTIALS_PATH).
Results are cached briefly so switching tabs doesn't re-hit Google's quota.

Money is parsed with Decimal rather than float: these are real ledger figures
and float drift would show up in the totals.
"""

import logging
import os
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))

# The sheet is hand-edited a few times a day; a short TTL keeps the tab
# responsive without burning the Sheets API read quota on every switch.
_CACHE_TTL_SEC = 60

_cache: Dict[str, Any] = {'payload': None, 'ts': 0.0}
_cache_lock = threading.Lock()

_TAB_COLLECTIONS = 'Collections'
_TAB_TRACK = 'Track'
_TAB_MONTHLY = 'Monthly'
_TAB_INVESTED = 'Invested'
_TAB_WITHDRAW = 'Withdraw'
_TAB_MERGED = 'Merged Data'


class FinanceConfigError(RuntimeError):
    """Raised when the service account key or sheet id isn't configured."""


def _credentials_path() -> str:
    raw = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH', '').strip()
    if not raw:
        raise FinanceConfigError(
            'GOOGLE_SHEETS_CREDENTIALS_PATH is not set in env/Mine.env'
        )
    path = raw if os.path.isabs(raw) else os.path.join(_PROJECT_ROOT, raw)
    if not os.path.exists(path):
        raise FinanceConfigError(f'Service account key not found at {path}')
    return path


def _sheet_id() -> str:
    sid = os.getenv('FINANCE_SHEET_ID', '').strip()
    if not sid:
        raise FinanceConfigError('FINANCE_SHEET_ID is not set in env/Mine.env')
    return sid


def _num(raw: Any) -> Optional[Decimal]:
    """Parse a sheet cell into a Decimal.

    Handles '1,234.50', '12.5%', '-519300', blanks and stray spaces.
    Returns None for anything that isn't a number, so callers can tell
    'not entered yet' apart from a real zero.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(',', '').replace('%', '').replace('₹', '').strip()
    if not s or s in {'-', '#REF!', '#DIV/0!', '#VALUE!', '#N/A'}:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _f(d: Optional[Decimal]) -> Optional[float]:
    """Decimal -> float at the JSON boundary (display only)."""
    return float(d) if d is not None else None


def _cell_right_of(rows: List[List[str]], label: str) -> Optional[Decimal]:
    """Find `label` in any cell, return the first numeric cell to its right.

    The Invested tab places scalars next to their captions at fixed spots,
    but scanning by label means inserting a row above won't silently point
    the dashboard at the wrong number.
    """
    target = label.strip().lower()
    for row in rows:
        for i, cell in enumerate(row):
            if str(cell).strip().lower() == target:
                for nxt in row[i + 1:]:
                    val = _num(nxt)
                    if val is not None:
                        return val
    return None


def _text_right_of(rows: List[List[str]], label: str) -> Optional[str]:
    """Same as _cell_right_of but returns the first non-empty text."""
    target = label.strip().lower()
    for row in rows:
        for i, cell in enumerate(row):
            if str(cell).strip().lower() == target:
                for nxt in row[i + 1:]:
                    if str(nxt).strip():
                        return str(nxt).strip()
    return None


def _open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(_credentials_path(), scopes=_SCOPES)
    return gspread.authorize(creds).open_by_key(_sheet_id())


def _nonblank(rows: List[List[str]]) -> List[List[str]]:
    return [r for r in rows if any(str(c).strip() for c in r)]


def _parse_merged(rows: List[List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Merged Data: cols A-C are month rollups, D-F are year rollups.

    These are read rather than recomputed. The sheet's own formulas are the
    figures the user reconciles against, so recomputing here would risk the
    dashboard quietly disagreeing with the spreadsheet it claims to mirror.
    """
    monthly: List[Dict[str, Any]] = []
    yearly: List[Dict[str, Any]] = []

    for row in rows[1:]:  # skip header
        cells = list(row) + [''] * 6

        label = str(cells[0]).strip()
        collected = _num(cells[1])
        if label and collected is not None:
            month, _, year = label.partition('-')
            try:
                sort_key = (int(year), int(month))
            except (TypeError, ValueError):
                sort_key = (0, 0)
            monthly.append({
                'label': label,
                'sort': sort_key,
                'collected': _f(collected),
                'profit': _f(_num(cells[2])),
            })

        ylabel = str(cells[3]).strip()
        ycollected = _num(cells[4])
        if ylabel and ycollected is not None:
            yearly.append({
                'label': ylabel,
                'collected': _f(ycollected),
                'profit': _f(_num(cells[5])),
            })

    monthly.sort(key=lambda m: m['sort'])
    for m in monthly:
        m.pop('sort', None)
    yearly.sort(key=lambda y: y['label'])
    return {'monthly': monthly, 'yearly': yearly}


def _parse_invested(rows: List[List[str]]) -> Dict[str, Any]:
    """Invested: headline capital figures + the by-type breakdown."""
    by_type: List[Dict[str, Any]] = []
    totals: Dict[str, Any] = {}

    # Rows 2-4 hold Dialy / Monthly / Total against the row-1 header.
    for row in rows[1:6]:
        cells = list(row) + [''] * 8
        label = str(cells[0]).strip()
        if label.lower() not in {'dialy', 'daily', 'monthly', 'total'}:
            continue
        entry = {
            'type': label,
            'invested': _f(_num(cells[1])),
            'in_line': _f(_num(cells[2])),
            'profit': _f(_num(cells[3])),
            'fut_profit': _f(_num(cells[4])),
            'taken_out': _f(_num(cells[5])),
            'in_hand': _f(_num(cells[6])),
        }
        if label.lower() == 'total':
            totals = entry
        else:
            by_type.append(entry)

    return {
        'by_type': by_type,
        'total_invested': totals.get('invested'),
        'total_in_line': totals.get('in_line'),
        'total_profit': totals.get('profit'),
        'fut_profit': totals.get('fut_profit'),
        'taken_out': _f(_cell_right_of(rows, 'Total Takeout')) or totals.get('taken_out'),
        'amt_in_hand': _f(_cell_right_of(rows, 'Amt Inhand')) or totals.get('in_hand'),
        'missed_amt': _f(_cell_right_of(rows, 'Missed Amt')),
        'total_amount_in_line': _f(_cell_right_of(rows, 'Total Amount in Line')),
        # The sheet's own cash-count check. Surfaced as-is: if the physical
        # cash tally fails, every downstream figure is suspect.
        'tally': _text_right_of(rows, 'TALLY'),
    }


def _parse_track(rows: List[List[str]]) -> List[Dict[str, Any]]:
    """Track: daily/weekly/monthly loans, one row each.

    Columns run A..S. Note O..S in particular — the sheet already computes
    the remaining balance, remaining EMIs and its own completion flag, so
    none of those should be re-derived here:
      O 'Rming Amt to colt' — still *scheduled* to collect. Drops to 0 when a
        loan is closed, so it is NOT (to-collect - collected); on a written-off
        loan the shortfall lives in 'Missing Amt' instead.
      R 'Is Complpeted'     — the owner's own Yes/No. Authoritative: 19 loans
        are marked Yes while still short, i.e. closed at a loss, which no
        amount-based rule would infer correctly.
    """
    out = []
    for row in rows[1:]:
        cells = list(row) + [''] * 20
        name = str(cells[0]).strip()
        if not name:
            continue
        completed_raw = str(cells[17]).strip()
        out.append({
            'name': name,
            'contact': str(cells[1]).strip(),
            'given_date': str(cells[2]).strip(),
            'start_date': str(cells[3]).strip(),
            'end_date': str(cells[4]).strip(),
            'loan_type': str(cells[5]).strip(),
            'missing_emi': _f(_num(cells[6])),
            'total_emi': _f(_num(cells[7])),
            'total_collection_amt': _f(_num(cells[8])),
            'emi_amt': _f(_num(cells[9])),
            'percentage': str(cells[10]).strip(),
            'given_amt': _f(_num(cells[11])),
            'collected': _f(_num(cells[12])),
            'missing_amt': _f(_num(cells[13])),
            'remaining_amt': _f(_num(cells[14])),
            'in_profit': _f(_num(cells[15])),
            'remaining_emi': _f(_num(cells[16])),
            # 'Yes'/'yes'/'No' in the sheet; None when blank so the UI can
            # show "unknown" rather than silently guessing "not completed".
            'is_completed': (completed_raw.lower() == 'yes') if completed_raw else None,
            'year': str(cells[18]).strip(),
        })
    return out


def _parse_monthly_tab(rows: List[List[str]]) -> List[Dict[str, Any]]:
    """Monthly: interest-only loans, one row each. Columns A..P.

    Same shape as Track for the tail columns: N 'Rming Amt to colt',
    O 'In Profit', P 'Is Complpeted'.
    """
    out = []
    for row in rows[1:]:
        cells = list(row) + [''] * 18
        name = str(cells[3]).strip()
        if not name:
            continue
        completed_raw = str(cells[15]).strip()
        out.append({
            'given_date': str(cells[0]).strip(),
            'start_date': str(cells[1]).strip(),
            'end_date': str(cells[2]).strip(),
            'name': name,
            'contact': str(cells[4]).strip(),
            'total_emi': _f(_num(cells[5])),
            'total_collection_amt': _f(_num(cells[6])),
            'percentage': str(cells[7]).strip(),
            'given_amt': _f(_num(cells[8])),
            'total_inst': _f(_num(cells[9])),
            'inst_amt': _f(_num(cells[10])),
            'collected': _f(_num(cells[11])),
            'missing_amt': _f(_num(cells[12])),
            'remaining_amt': _f(_num(cells[13])),
            'in_profit': _f(_num(cells[14])),
            'is_completed': (completed_raw.lower() == 'yes') if completed_raw else None,
        })
    return out


def _parse_withdraw(rows: List[List[str]]) -> List[Dict[str, Any]]:
    """Withdraw: date / amount / reason."""
    out = []
    for row in rows[1:]:
        cells = list(row) + [''] * 3
        amount = _num(cells[1])
        if amount is None:
            continue
        out.append({
            'date': str(cells[0]).strip(),
            'amount': _f(amount),
            'reason': str(cells[2]).strip(),
        })
    return out


def _parse_collections(rows: List[List[str]]) -> Dict[str, Any]:
    """Collections: repeating 2-row blocks (dates row, then amounts row).

    Row 2i   -> ['Name'|'Dialy', 'Status'|'Amount', date, date, ...]
    Row 2i+1 -> [customer,        'Yes'|'No',       amt,  amt,  ...]

    A block is one loan cycle, so the same customer appears many times over
    the years. Verified against the live sheet: 152 blocks, 0 pattern breaks.
    Malformed blocks are skipped rather than raising — one bad hand-edited
    row shouldn't take the whole dashboard down.
    """
    blocks: List[Dict[str, Any]] = []
    skipped = 0
    rows = _nonblank(rows)

    for i in range(0, len(rows) - 1, 2):
        header, data = rows[i], rows[i + 1]
        if str(header[0]).strip() not in {'Name', 'Dialy'}:
            skipped += 1
            continue
        name = str(data[0]).strip() if data else ''
        if not name:
            skipped += 1
            continue

        entries = []
        total = Decimal('0')
        for col in range(2, min(len(header), len(data))):
            day = str(header[col]).strip()
            if not day:
                continue
            amt = _num(data[col])
            if amt is None:
                continue
            total += amt
            entries.append({'date': day, 'amount': _f(amt)})

        blocks.append({
            'name': name,
            'status': str(data[1]).strip() if len(data) > 1 else '',
            'entries': entries,
            'total': _f(total),
            'paid_count': sum(1 for e in entries if (e['amount'] or 0) > 0),
            'entry_count': len(entries),
        })

    if skipped:
        logger.warning('Collections: skipped %d malformed block(s)', skipped)

    return {
        'blocks': blocks,
        'skipped_blocks': skipped,
        'customers': sorted({b['name'] for b in blocks}),
    }


class FinanceService:
    """Read-only view of the finance Google Sheet."""

    @staticmethod
    def get_dashboard(force: bool = False) -> Dict[str, Any]:
        """Return the whole dashboard payload, cached for _CACHE_TTL_SEC.

        `force=True` bypasses the cache (the tab's Refresh button).
        """
        with _cache_lock:
            fresh = (time.time() - _cache['ts']) < _CACHE_TTL_SEC
            if not force and _cache['payload'] is not None and fresh:
                cached = dict(_cache['payload'])
                cached['cached'] = True
                return cached

        sheet = _open_sheet()
        tabs = {ws.title: ws for ws in sheet.worksheets()}

        missing = [t for t in (_TAB_TRACK, _TAB_MONTHLY, _TAB_INVESTED,
                               _TAB_WITHDRAW, _TAB_MERGED) if t not in tabs]
        if missing:
            raise FinanceConfigError(
                f"Sheet is missing expected tab(s): {', '.join(missing)}"
            )

        def grab(tab: str) -> List[List[str]]:
            return _nonblank(tabs[tab].get_all_values())

        invested = _parse_invested(grab(_TAB_INVESTED))
        merged = _parse_merged(grab(_TAB_MERGED))
        track = _parse_track(grab(_TAB_TRACK))
        monthly_loans = _parse_monthly_tab(grab(_TAB_MONTHLY))
        withdrawals = _parse_withdraw(grab(_TAB_WITHDRAW))

        collections: Dict[str, Any] = {'blocks': [], 'customers': [], 'skipped_blocks': 0}
        if _TAB_COLLECTIONS in tabs:
            collections = _parse_collections(tabs[_TAB_COLLECTIONS].get_all_values())

        payload = {
            'success': True,
            'sheet_title': sheet.title,
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'cached': False,
            'summary': invested,
            'monthly_series': merged['monthly'],
            'yearly_series': merged['yearly'],
            'track_loans': track,
            'monthly_loans': monthly_loans,
            'withdrawals': withdrawals,
            'collections': collections,
            'counts': {
                'track_loans': len(track),
                'monthly_loans': len(monthly_loans),
                'withdrawals': len(withdrawals),
                'collection_blocks': len(collections['blocks']),
                'customers': len(collections['customers']),
            },
        }

        with _cache_lock:
            _cache['payload'] = payload
            _cache['ts'] = time.time()

        return payload
