"""
OI Historic Data — daily end-of-day CE/PE open interest snapshot.

Records are stored in a per-symbol JSON file at the project root:
  Nifty_oi_historic_data.json
Each record: { date, symbol, ce_oi, pe_oi, open, high, low, close, idx_fut_oi }
Upsert logic: one record per (date, symbol); re-fetching on the same day overwrites.
"""
import csv
import io
import json
import os
import threading
import time
import zipfile
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import requests
import logging
logger = logging.getLogger(__name__)

_lock = threading.Lock()

_SYMBOLS = ['NIFTY']

# Per-symbol JSON filenames at the project root
_SYMBOL_FILE = {
    'NIFTY': 'Nifty_oi_historic_data.json',
}

_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..')
)

_NSE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': '*/*',
}

# Module-level status for long-running NSE backfill (background thread)
_backfill_status: Dict[str, Any] = {
    'running': False,
    'progress': 0,
    'total': 0,
    'message': '',
    'errors': [],
}


def _get_json_path(symbol: str) -> str:
    return os.path.join(_PROJECT_ROOT, _SYMBOL_FILE[symbol])


def _load_records() -> List[Dict[str, Any]]:
    """Load and merge records from all per-symbol JSON files."""
    records: List[Dict[str, Any]] = []
    for sym in _SYMBOLS:
        path = _get_json_path(sym)
        if not os.path.exists(path):
            continue
        with open(path, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    records.extend(data)
            except (json.JSONDecodeError, ValueError):
                pass
    return records


def _save_records(records: List[Dict[str, Any]]) -> None:
    """Split records by symbol and write each to its own JSON file."""
    by_symbol: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in _SYMBOLS}
    for r in records:
        sym = r.get('symbol', '')
        if sym in by_symbol:
            by_symbol[sym].append(r)
    for sym, recs in by_symbol.items():
        path = _get_json_path(sym)
        with open(path, 'w') as f:
            json.dump(recs, f, indent=2)


def get_all_records() -> List[Dict[str, Any]]:
    """Return all records sorted newest-first."""
    with _lock:
        records = _load_records()
    records.sort(key=lambda r: (r.get('date', ''), r.get('symbol', '')), reverse=True)
    return records


def delete_record(date: str, symbol: str) -> bool:
    """Delete record matching (date, symbol). Returns True if a record was removed."""
    with _lock:
        records = _load_records()
        original_len = len(records)
        records = [
            r for r in records
            if not (r.get('date') == date and r.get('symbol') == symbol)
        ]
        if len(records) == original_len:
            return False
        _save_records(records)
    return True


def get_backfill_status() -> Dict[str, Any]:
    """Return current NSE backfill progress."""
    return dict(_backfill_status)


_SYMBOL_INSTRUMENT_KEY = {
    'fyers': {
        'NIFTY': 'NSE:NIFTY50-INDEX',
    },
    'kite': {
        'NIFTY': 'NSE:NIFTY 50',
    },
}


def _date_to_nse(date_str: str) -> str:
    """Convert YYYY-MM-DD → NSE archive format DDMMMYYYY (e.g. 01JAN2024)."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return dt.strftime('%d%b%Y').upper()


def _fetch_day_ohlc(symbol: str, date_str: str, provider) -> Dict[str, Any]:
    """Fetch the day candle (O/H/L/C) for the underlying index on date_str."""
    try:
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter
        provider_type = 'fyers' if isinstance(provider, FyersDataServiceAdapter) else 'kite'
        instrument_key = _SYMBOL_INSTRUMENT_KEY[provider_type].get(symbol)
        if not instrument_key:
            return {}
        candles = provider.historical_data(instrument_key, date_str, date_str, 'day')
        if candles:
            c = candles[-1]
            return {
                'open':  round(float(c.get('open', 0)), 2),
                'high':  round(float(c.get('high', 0)), 2),
                'low':   round(float(c.get('low', 0)), 2),
                'close': round(float(c.get('close', 0)), 2),
            }
    except Exception as e:
        logger.warning(f"[HistoricOI] OHLC fetch failed for {symbol} on {date_str}: {e}")
    return {}


# Symbol → index name as it appears in NSE's daily index snapshot CSV
_SYMBOL_NSE_INDEX_NAME = {
    'NIFTY': 'Nifty 50',
}


def _fetch_nse_index_spot_ohlc(date_str: str,
                               symbols: Optional[List[str]] = None) -> Dict[str, Dict]:
    """
    Download NSE's daily index snapshot for date_str and return *spot* index
    OHLC per symbol: { symbol: { open, high, low, close } }.

      https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv

    This is the official NIFTY 50 index value (not futures). Returns {} on
    holiday / 404.
    """
    if symbols is None:
        symbols = _SYMBOLS
    try:
        dt  = datetime.strptime(date_str, '%Y-%m-%d')
        url = (f'https://nsearchives.nseindia.com/content/indices/'
               f'ind_close_all_{dt.strftime("%d%m%Y")}.csv')
        resp = requests.get(url, headers=_NSE_HEADERS, timeout=30)
        if resp.status_code != 200:
            return {}
        name_map = {_SYMBOL_NSE_INDEX_NAME[s]: s
                    for s in symbols if s in _SYMBOL_NSE_INDEX_NAME}
        result: Dict[str, Dict] = {}
        reader = csv.DictReader(io.StringIO(resp.content.decode('utf-8', errors='replace')))
        for row in reader:
            sym = name_map.get((row.get('Index Name') or '').strip())
            if not sym or sym in result:
                continue
            try:
                result[sym] = {
                    'open':  round(float(row['Open Index Value']), 2),
                    'high':  round(float(row['High Index Value']), 2),
                    'low':   round(float(row['Low Index Value']), 2),
                    'close': round(float(row['Closing Index Value']), 2),
                }
            except (ValueError, TypeError, KeyError):
                continue
        return result
    except Exception as e:
        logger.warning(f"[HistoricOI] Index spot OHLC fetch failed for {date_str}: {e}")
        return {}


def _fetch_spot_ohlc(symbol: str, date_str: str, provider=None) -> Dict[str, Any]:
    """
    Spot index OHLC for one symbol — NSE index snapshot first (official),
    broker day-candle of the index as fallback. Never futures data.
    """
    ohlc = _fetch_nse_index_spot_ohlc(date_str, [symbol]).get(symbol, {})
    if ohlc.get('close'):
        return ohlc
    if provider:
        return _fetch_day_ohlc(symbol, date_str, provider)
    return {}


def _fetch_nse_bhavcopy_oi(date_str: str, symbols: List[str]) -> Dict[str, Dict]:
    """
    Download NSE F&O bhavcopy ZIP for date_str and return per-symbol data:
      { symbol: { ce_oi, pe_oi, idx_fut_oi } }
    (OHLC is NOT taken from the bhavcopy — futures prices differ from the
     index spot; use _fetch_nse_index_spot_ohlc / _fetch_spot_ohlc instead.)

    CE/PE OI is summed over the nearest expiry *strictly after* date_str: on
    expiry day (Tue currently, Thu before Sep 2025, holiday-shifted otherwise)
    the expiring chain is skipped and next week's chain is recorded instead —
    same roll rule as the broker path (use_next_expiry=True), so CHNG OPT OI
    and PCR are never computed from a chain that dies that evening.

    Two URL formats are tried in order:
      1. Old archive (2021–2024-07-05):
         nsearchives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMMMYYYYbhav.csv.zip
      2. New archive (2024-07-08 onwards):
         nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip

    Returns {} on holiday or if both URLs return 404.
    """
    try:
        dt       = datetime.strptime(date_str, '%Y-%m-%d')
        today_dt = dt.date()
        sym_set  = set(symbols)

        # ── Try old archive format ───────────────────────────────────────────
        nse_dt  = _date_to_nse(date_str)
        old_url = (f'https://nsearchives.nseindia.com/content/historical/DERIVATIVES/'
                   f'{dt.strftime("%Y")}/{dt.strftime("%b").upper()}/'
                   f'fo{nse_dt}bhav.csv.zip')
        resp = requests.get(old_url, headers=_NSE_HEADERS, timeout=30)

        new_format = False
        if resp.status_code != 200:
            # ── Fallback: new archive format (post-July 2024) ────────────────
            ymd     = dt.strftime('%Y%m%d')
            new_url = (f'https://nsearchives.nseindia.com/content/fo/'
                       f'BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip')
            resp = requests.get(new_url, headers=_NSE_HEADERS, timeout=30)
            if resp.status_code != 200:
                return {}  # holiday or date not yet published
            new_format = True

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_bytes = zf.read(zf.namelist()[0])
        rows: List[Dict[str, str]] = list(
            csv.DictReader(io.StringIO(csv_bytes.decode('utf-8', errors='replace')))
        )

        if new_format:
            return _parse_new_bhavcopy(rows, symbols, sym_set, today_dt)
        else:
            return _parse_old_bhavcopy(rows, symbols, sym_set, today_dt)

    except Exception as e:
        logger.warning(f"[HistoricOI] Bhavcopy fetch failed for {date_str}: {e}")
        return {}


def _parse_old_bhavcopy(rows: List[Dict], symbols: List[str],
                         sym_set: set, today_dt) -> Dict[str, Dict]:
    """Parse the pre-2024-07-08 bhavcopy format (INSTRUMENT / SYMBOL / EXPIRY_DT columns)."""
    expiry_map: Dict[str, Any] = {}

    for row in rows:
        instr = row.get('INSTRUMENT', '').strip()
        if instr != 'OPTIDX':
            continue
        sym = row.get('SYMBOL', '').strip()
        if sym not in sym_set:
            continue
        try:
            exp_dt = datetime.strptime(row['EXPIRY_DT'].strip(), '%d-%b-%Y').date()
        except (ValueError, KeyError):
            continue
        # Strictly after the record date: on expiry day the expiring chain is
        # skipped and next expiry's OI is recorded (dynamic — no weekday assumed)
        if exp_dt > today_dt:
            if sym not in expiry_map or exp_dt < expiry_map[sym]:
                expiry_map[sym] = exp_dt

    result: Dict[str, Dict] = {
        s: {'ce_oi': 0, 'pe_oi': 0, 'idx_fut_oi': 0}
        for s in symbols
    }
    for row in rows:
        instr = row.get('INSTRUMENT', '').strip()
        sym   = row.get('SYMBOL', '').strip()
        if sym not in sym_set or instr not in ('OPTIDX', 'FUTIDX'):
            continue
        try:
            exp_dt = datetime.strptime(row['EXPIRY_DT'].strip(), '%d-%b-%Y').date()
        except (ValueError, KeyError):
            continue
        try:
            oi_val = int(float(row.get('OPEN_INT', 0) or 0))
        except (ValueError, TypeError):
            oi_val = 0

        if instr == 'OPTIDX':
            if sym not in expiry_map or exp_dt != expiry_map[sym]:
                continue
            opt_type = row.get('OPTION_TYP', '').strip()
            if opt_type == 'CE':
                result[sym]['ce_oi'] += oi_val
            elif opt_type == 'PE':
                result[sym]['pe_oi'] += oi_val
        elif instr == 'FUTIDX':
            result[sym]['idx_fut_oi'] += oi_val
    return result


def _parse_new_bhavcopy(rows: List[Dict], symbols: List[str],
                         sym_set: set, today_dt) -> Dict[str, Dict]:
    """Parse the post-2024-07-08 bhavcopy format (FinInstrmTp / TckrSymb / XpryDt columns)."""
    expiry_map: Dict[str, Any] = {}

    for row in rows:
        instr = row.get('FinInstrmTp', '').strip()
        if instr != 'IDO':
            continue
        sym = row.get('TckrSymb', '').strip()
        if sym not in sym_set:
            continue
        try:
            exp_dt = datetime.strptime(row['XpryDt'].strip(), '%Y-%m-%d').date()
        except (ValueError, KeyError):
            continue
        # Strictly after the record date: on expiry day the expiring chain is
        # skipped and next expiry's OI is recorded (dynamic — no weekday assumed)
        if exp_dt > today_dt:
            if sym not in expiry_map or exp_dt < expiry_map[sym]:
                expiry_map[sym] = exp_dt

    result: Dict[str, Dict] = {
        s: {'ce_oi': 0, 'pe_oi': 0, 'idx_fut_oi': 0}
        for s in symbols
    }
    for row in rows:
        instr = row.get('FinInstrmTp', '').strip()
        sym   = row.get('TckrSymb', '').strip()
        if sym not in sym_set or instr not in ('IDO', 'IDF'):
            continue
        try:
            exp_dt = datetime.strptime(row['XpryDt'].strip(), '%Y-%m-%d').date()
        except (ValueError, KeyError):
            continue
        try:
            oi_val = int(float(row.get('OpnIntrst', 0) or 0))
        except (ValueError, TypeError):
            oi_val = 0

        if instr == 'IDO':
            if sym not in expiry_map or exp_dt != expiry_map[sym]:
                continue
            opt_type = row.get('OptnTp', '').strip()
            if opt_type == 'CE':
                result[sym]['ce_oi'] += oi_val
            elif opt_type == 'PE':
                result[sym]['pe_oi'] += oi_val
        elif instr == 'IDF':
            result[sym]['idx_fut_oi'] += oi_val
    return result


def _fetch_nse_fii_fut_oi(date_str: str) -> Optional[int]:
    """
    Download the NSE participant-wise OI report for date_str and return the FII
    *net index-futures* open interest = (Future Index Long − Future Index Short),
    in number of contracts. This is a market-wide aggregate across all index
    futures (not per-symbol). The day-over-day change of this value is what the
    "Chng Fut OI" column shows (matches StockMojo's FII Fut OI change).

      https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv

    Returns None on holiday / 404 / unparseable format.
    """
    try:
        dt  = datetime.strptime(date_str, '%Y-%m-%d')
        url = (f'https://nsearchives.nseindia.com/content/nsccl/'
               f'fao_participant_oi_{dt.strftime("%d%m%Y")}.csv')
        resp = requests.get(url, headers=_NSE_HEADERS, timeout=30)
        if resp.status_code != 200:
            return None
        lines   = resp.content.decode('utf-8', errors='replace').splitlines()
        hdr_idx = next((i for i, l in enumerate(lines) if 'Client Type' in l), None)
        if hdr_idx is None:
            return None
        for row in csv.DictReader(lines[hdr_idx:]):
            if (row.get('Client Type') or '').strip() == 'FII':
                try:
                    fut_long  = float(row.get('Future Index Long',  0) or 0)
                    fut_short = float(row.get('Future Index Short', 0) or 0)
                    return int(fut_long - fut_short)
                except (ValueError, TypeError):
                    return None
        return None
    except Exception as e:
        logger.warning(f"[HistoricOI] FII participant OI fetch failed for {date_str}: {e}")
        return None


def _fetch_nse_fii_idx_fut_flow(date_str: str) -> Optional[float]:
    """
    Download NSE's FII Derivative Statistics report for date_str and return the
    FII *net index-futures flow* in Crore INR = (Index Futures Buy Amt − Sell Amt).

      https://nsearchives.nseindia.com/content/fo/fii_stats_DD-Mon-YYYY.xls

    This is the same figure Moneycontrol republishes as `fiiIdxFut`, but NSE
    publishes it the *same evening* (Moneycontrol lags ~1 day), so it fills the
    "FII Fut" column on the day of the 8 PM record instead of leaving it blank.
    Returns None on holiday / 404 / unparseable format.
    """
    try:
        import xlrd
    except ImportError:
        logger.warning("[HistoricOI] xlrd not installed — cannot read NSE FII stats .xls")
        return None
    try:
        dt  = datetime.strptime(date_str, '%Y-%m-%d')
        url = (f'https://nsearchives.nseindia.com/content/fo/'
               f'fii_stats_{dt.strftime("%d-%b-%Y")}.xls')
        resp = requests.get(url, headers=_NSE_HEADERS, timeout=30)
        if resp.status_code != 200 or not resp.content:
            return None
        sheet = xlrd.open_workbook(file_contents=resp.content).sheet_by_index(0)
        for i in range(sheet.nrows):
            if str(sheet.cell_value(i, 0)).strip().upper() == 'INDEX FUTURES':
                buy  = float(str(sheet.cell_value(i, 2)).replace(',', '') or 0)
                sell = float(str(sheet.cell_value(i, 4)).replace(',', '') or 0)
                return round(buy - sell, 2)
        return None
    except Exception as e:
        logger.warning(f"[HistoricOI] NSE FII stats fetch failed for {date_str}: {e}")
        return None


def _business_days(from_date: str, to_date: str) -> List[str]:
    """Return Mon–Fri date strings (YYYY-MM-DD) between from_date and to_date inclusive."""
    start = datetime.strptime(from_date, '%Y-%m-%d').date()
    end   = datetime.strptime(to_date,   '%Y-%m-%d').date()
    days  = []
    cur   = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)
    return days


def fetch_and_store(symbol: str, provider=None,
                    bhavcopy_data: Optional[Dict] = None,
                    fii_flow: Optional[float] = None) -> Dict[str, Any]:
    """
    Fetch today's OI totals for *symbol* from the broker and upsert into JSON.
    bhavcopy_data: pre-fetched bhavcopy dict for this symbol (provides idx_fut_oi).
    fii_flow: today's FII index-futures flow (Cr) from Moneycontrol, if available;
              when None the existing stored value is preserved.
    Uses merge upsert so idx_fut_oi is never silently overwritten.
    """
    try:
        if provider is None:
            from trading_app.service.provider_logic import get_data_provider
            provider = get_data_provider(user='Mine')

        today = datetime.now().strftime('%Y-%m-%d')
        bhav  = bhavcopy_data or {}

        total_ce_oi = total_pe_oi = 0
        broker_ok = False

        # Broker option chain — primary source intraday (before the bhavcopy is
        # published). Optional and best-effort: at EOD the broker session is
        # often expired, but the bhavcopy below already has the full chain, so a
        # broker failure must NOT abort the record (this is what broke the 8 PM
        # auto-record). Only the ~50 ATM strikes are returned, hence fallback-only.
        if provider:
            try:
                from trading_app.service.open_interest_service import OpenInterestService
                oi_service = OpenInterestService(provider)
                # On expiry day, use_next_expiry=True skips today's expiry and records
                # next week's OI instead (non-expiry days: no behavioural difference).
                data = oi_service.get_open_interest_data(symbol, use_next_expiry=True)
                if data.get('success'):
                    ce_summary = data.get('ce_summary', {})
                    pe_summary = data.get('pe_summary', {})
                    if not ce_summary and not pe_summary:
                        total_ce_oi = sum(s.get('oi', 0) for s in data.get('ce_strikes', []))
                        total_pe_oi = sum(s.get('oi', 0) for s in data.get('pe_strikes', []))
                    else:
                        total_ce_oi = ce_summary.get('total_oi', 0)
                        total_pe_oi = pe_summary.get('total_oi', 0)
                    broker_ok = True
                else:
                    logger.warning(f"[HistoricOI] Broker OI fetch failed for {symbol}: "
                                   f"{data.get('error')} — relying on bhavcopy")
            except Exception as e:
                logger.warning(f"[HistoricOI] Broker OI fetch errored for {symbol}: {e} "
                               f"— relying on bhavcopy")

        # Prefer official NSE bhavcopy CE/PE OI (full nearest-expiry chain) so
        # Record stores the same totals — and hence the same CHNG OPT OI / PCR —
        # as the per-row refresh. The broker chain only covers ~50 ATM strikes,
        # which understates CE/PE OI and skews PCR.
        if bhav.get('ce_oi') or bhav.get('pe_oi'):
            total_ce_oi = bhav.get('ce_oi', total_ce_oi)
            total_pe_oi = bhav.get('pe_oi', total_pe_oi)

        # Record only if at least one source produced option OI.
        if not broker_ok and not (bhav.get('ce_oi') or bhav.get('pe_oi')):
            return {'success': False,
                    'error': 'No OI data available from broker or bhavcopy'}

        # OHLC from the NIFTY index *spot* (NSE index snapshot; broker index
        # candle as fallback) — never from futures prices.
        ohlc = _fetch_spot_ohlc(symbol, today, provider)

        record = {
            'date':              today,
            'symbol':            symbol,
            'ce_oi':             int(total_ce_oi),
            'pe_oi':             int(total_pe_oi),
            'open':              ohlc.get('open',  0),
            'high':              ohlc.get('high',  0),
            'low':               ohlc.get('low',   0),
            'close':             ohlc.get('close', 0),
            'idx_fut_oi':        bhav.get('idx_fut_oi') or None,
            'fii_fut_oi':        _fetch_nse_fii_fut_oi(today),
            'FII_Index_futures': fii_flow if fii_flow is not None else 0,
        }

        with _lock:
            records = _load_records()
            updated = False
            for i, r in enumerate(records):
                if r.get('date') == today and r.get('symbol') == symbol:
                    # Merge: keep the freshly fetched FII flow when we have one,
                    # else preserve the existing value; fall back to existing
                    # idx_fut_oi / fii_fut_oi if not fetched today (participant OI
                    # is only published after market close).
                    if fii_flow is None:
                        record['FII_Index_futures'] = r.get('FII_Index_futures', 0)
                    if record['idx_fut_oi'] is None:
                        record['idx_fut_oi'] = r.get('idx_fut_oi')
                    if record['fii_fut_oi'] is None:
                        record['fii_fut_oi'] = r.get('fii_fut_oi')
                    records[i] = record
                    updated = True
                    break
            if not updated:
                records.append(record)
            _save_records(records)

        logger.info(
            f"[HistoricOI] Stored {symbol} on {today}: "
            f"CE={total_ce_oi:,} PE={total_pe_oi:,} "
            f"FutOI={record.get('idx_fut_oi')} close={record.get('close')}"
        )
        return {'success': True, 'record': record}

    except Exception as e:
        logger.error(f"[HistoricOI] Error fetching {symbol}: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def backfill_ohlc(provider=None) -> Dict[str, Any]:
    """
    One-time backfill: for every record missing OHLC data (open/close == 0),
    fetch the day candle from the broker and update the record in-place.
    Returns a summary of how many records were updated vs skipped.
    """
    if provider is None:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider(user='Mine')

    if not provider:
        return {'success': False, 'error': 'No data provider available'}

    updated = 0
    skipped = 0
    errors = []

    with _lock:
        records = _load_records()

    need_ohlc = [i for i, r in enumerate(records)
                 if not (r.get('close') and float(r.get('close', 0)) > 0)]
    total_need = len(need_ohlc)

    for seq, i in enumerate(need_ohlc):
        r        = records[i]
        date_str = r.get('date', '')
        symbol   = r.get('symbol', '')
        _backfill_status['message'] = (
            f'Patching OHLC {seq + 1}/{total_need} — {symbol} {date_str}…'
        )
        if not date_str or not symbol:
            skipped += 1
            continue

        ohlc = _fetch_spot_ohlc(symbol, date_str, provider)
        if not ohlc or ohlc.get('close', 0) == 0:
            errors.append(f"{symbol} {date_str}")
            skipped += 1
            continue

        records[i] = {**r, **ohlc}
        updated += 1

    with _lock:
        _save_records(records)

    logger.info(f"[HistoricOI] OHLC backfill done: {updated} updated, {skipped} skipped, {len(errors)} errors")
    return {
        'success': True,
        'updated': updated,
        'skipped': skipped,
        'errors':  errors,
        'records': get_all_records(),
    }


def backfill_fii() -> Dict[str, Any]:
    """
    One-time backfill: for every record missing idx_fut_oi, download the NSE
    F&O bhavcopy for that date (same file used for CE/PE OI), extract total
    FUTIDX open interest per symbol (all expiries summed), and patch all
    matching records.  NSE archives are available up to ~July 2024; newer
    dates are silently skipped (use the Record button for recent data).
    """
    with _lock:
        records = _load_records()

    # Collect unique dates that need futures OI
    dates_needed = sorted({
        r['date'] for r in records
        if r.get('idx_fut_oi') is None and r.get('date')
    })

    updated = 0
    skipped = sum(1 for r in records if r.get('idx_fut_oi') is not None)
    errors: List[str] = []
    total_dates = len(dates_needed)

    for idx_d, date_str in enumerate(dates_needed):
        _backfill_status['message'] = (
            f'Patching Fut OI {idx_d + 1}/{total_dates} — {date_str}…'
        )
        oi_data = _fetch_nse_bhavcopy_oi(date_str, _SYMBOLS)
        if not oi_data:
            # Bhavcopy unavailable (archive cutoff or holiday) — skip silently
            time.sleep(0.3)
            continue
        for i, r in enumerate(records):
            if r.get('date') != date_str or r.get('idx_fut_oi') is not None:
                continue
            sym    = r.get('symbol', '')
            fut_oi = oi_data.get(sym, {}).get('idx_fut_oi')
            if fut_oi is not None:
                records[i] = {**r, 'idx_fut_oi': fut_oi}
                updated += 1
        time.sleep(1)  # gentle rate limit between bhavcopy downloads

    with _lock:
        _save_records(records)

    logger.info(
        f"[HistoricOI] Fut OI backfill done: {updated} records updated, "
        f"{skipped} already had data, {len(errors)} dates unavailable"
    )
    return {
        'success': True,
        'updated': updated,
        'skipped': skipped,
        'errors':  errors,
        'records': get_all_records(),
    }


def _fetch_moneycontrol_fii_map() -> Dict[str, float]:
    """
    Fetch the last ~30 days of FII index-futures net flow (Crore INR) from
    Moneycontrol. Returns {YYYY-MM-DD: fii_idx_fut_crore}, or {} on any error.
    Shared by sync_fii_from_moneycontrol() and refresh_record().
    """
    from bs4 import BeautifulSoup

    url = 'https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        logger.warning(f'[HistoricOI] Moneycontrol returned {resp.status_code}')
        return {}

    soup = BeautifulSoup(resp.text, 'html.parser')
    script = soup.find('script', id='__NEXT_DATA__')
    if not script or not script.string:
        logger.warning('[HistoricOI] Could not find Moneycontrol __NEXT_DATA__')
        return {}

    page_data  = json.loads(script.string)
    container  = (page_data.get('props', {})
                           .get('pageProps', {})
                           .get('FiiDiiData', {}))
    fii_list   = container.get('fiiDiiData', container.get('list', []))

    def _parse(v):
        if not v or v == '-':
            return None
        try:
            return float(str(v).replace(',', '').strip())
        except ValueError:
            return None

    # Build {YYYY-MM-DD: fii_idx_fut_crore} from the 30-day list
    fii_map: Dict[str, float] = {}
    for row in fii_list:
        date_str = row.get('date', '')          # already YYYY-MM-DD
        val      = _parse(row.get('fiiIdxFut'))
        if date_str and val is not None:
            fii_map[date_str] = val
    return fii_map


def _patch_fii_flow(fii_map: Dict[str, float]) -> int:
    """
    Patch FII_Index_futures (Crore INR) on every stored record whose date is in
    fii_map. Only writes when the value actually changed. Returns the number of
    records updated. Shared by sync_fii_from_moneycontrol(), fetch_and_store_all()
    and the historical load so FII flow is folded into every write path.
    """
    with _lock:
        records = _load_records()
    updated = 0
    for i, r in enumerate(records):
        d = r.get('date', '')
        if d in fii_map and r.get('FII_Index_futures') != fii_map[d]:
            records[i] = {**r, 'FII_Index_futures': fii_map[d]}
            updated += 1
    if updated:
        with _lock:
            _save_records(records)
    return updated


def sync_fii_from_moneycontrol() -> Dict[str, Any]:
    """
    Fetch last ~30 days of FII index futures net flow from Moneycontrol
    and patch FII_Index_futures in matching historic OI records.
    Values are stored in Crore INR (the unit Moneycontrol reports).
    """
    try:
        fii_map = _fetch_moneycontrol_fii_map()
        if not fii_map:
            return {'success': False, 'error': 'No parseable fiiIdxFut values found'}

        updated = _patch_fii_flow(fii_map)
        dates   = sorted(fii_map)
        logger.info(f'[HistoricOI] FII sync: {updated} records updated '
                    f'({dates[0]} → {dates[-1]})')
        return {
            'success':      True,
            'updated':      updated,
            'dates_fetched': len(fii_map),
            'date_range':   f'{dates[0]} to {dates[-1]}',
        }

    except Exception as e:
        logger.error(f'[HistoricOI] FII sync failed: {e}', exc_info=True)
        return {'success': False, 'error': str(e)}


def backfill_fii_fut_oi(force: bool = False) -> Dict[str, Any]:
    """
    Populate `fii_fut_oi` (FII net index-futures OI, in contracts) on every
    record by downloading the NSE participant-wise OI report per unique date.
    The value is a market-wide aggregate, so all symbols on a given date share
    it. Dates already populated are skipped unless force=True. The "Chng Fut OI"
    column displays the day-over-day change of this stored level.
    """
    try:
        with _lock:
            records = _load_records()

        all_dates = sorted({r['date'] for r in records if r.get('date')})
        if not force:
            have = {r['date'] for r in records if r.get('fii_fut_oi') is not None}
            todo = [d for d in all_dates if d not in have]
        else:
            todo = all_dates

        fetched: Dict[str, int] = {}
        for d in todo:
            val = _fetch_nse_fii_fut_oi(d)
            if val is not None:
                fetched[d] = val
            time.sleep(0.4)  # gentle rate limit between NSE requests

        updated = 0
        for i, r in enumerate(records):
            d = r.get('date', '')
            if d in fetched:
                records[i] = {**r, 'fii_fut_oi': fetched[d]}
                updated += 1

        with _lock:
            _save_records(records)

        dates = sorted(fetched)
        rng   = f'{dates[0]} to {dates[-1]}' if dates else ''
        logger.info(f'[HistoricOI] FII Fut OI backfill: {updated} records updated '
                    f'({len(fetched)} dates) {rng}')
        return {
            'success':       True,
            'updated':       updated,
            'dates_fetched': len(fetched),
            'date_range':    rng,
        }
    except Exception as e:
        logger.error(f'[HistoricOI] FII Fut OI backfill failed: {e}', exc_info=True)
        return {'success': False, 'error': str(e)}


def backfill_from_sqlite(symbols: Optional[List[str]] = None,
                         provider=None) -> Dict[str, Any]:
    """
    Fast backfill: extract the last intraday OI snapshot of each day from the
    local oi_data.db SQLite and upsert into the per-symbol JSON files.
    FII participant data is fetched from NSE archives for each unique date found.
    OHLC is fetched via the broker API (provider).
    Existing records are not overwritten.
    """
    import sqlite3

    if symbols is None:
        symbols = _SYMBOLS

    if provider is None:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider(user='Mine')

    db_path = os.path.join(_PROJECT_ROOT, 'oi_data.db')
    if not os.path.exists(db_path):
        return {'success': False, 'error': 'oi_data.db not found'}

    sym_placeholders = ', '.join('?' for _ in symbols)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"""
            SELECT d, symbol, total_ce_oi, total_pe_oi
            FROM (
                SELECT date(timestamp) AS d, symbol, total_ce_oi, total_pe_oi,
                       ROW_NUMBER() OVER (
                           PARTITION BY date(timestamp), symbol
                           ORDER BY timestamp DESC
                       ) AS rn
                FROM oi_history
                WHERE symbol IN ({sym_placeholders})
            )
            WHERE rn = 1
            ORDER BY d ASC
        """, symbols).fetchall()
    finally:
        conn.close()

    inserted = 0
    skipped  = 0
    errors: List[str] = []

    with _lock:
        records = _load_records()
    existing = {(r['date'], r['symbol']) for r in records}

    for (d, symbol, ce_oi, pe_oi) in rows:
        if (d, symbol) in existing:
            skipped += 1
            continue

        ohlc = _fetch_spot_ohlc(symbol, d, provider)

        record = {
            'date':   d,
            'symbol': symbol,
            'ce_oi':  int(ce_oi or 0),
            'pe_oi':  int(pe_oi or 0),
            'open':   ohlc.get('open', 0),
            'high':   ohlc.get('high', 0),
            'low':    ohlc.get('low', 0),
            'close':  ohlc.get('close', 0),
        }
        records.append(record)
        existing.add((d, symbol))
        inserted += 1

    with _lock:
        _save_records(records)

    logger.info(f"[HistoricOI] SQLite backfill: {inserted} inserted, {skipped} skipped")
    return {
        'success':  True,
        'inserted': inserted,
        'skipped':  skipped,
        'errors':   errors,
        'records':  get_all_records(),
    }


def _run_nse_backfill(from_date: str, to_date: str,
                      symbols: List[str], provider) -> None:
    """Internal worker — runs in a background thread."""
    global _backfill_status

    days = _business_days(from_date, to_date)
    _backfill_status.update({
        'running':  True,
        'progress': 0,
        'total':    len(days),
        'message':  'Starting…',
        'errors':   [],
    })

    inserted = 0
    skipped  = 0
    errors: List[str] = []

    with _lock:
        records = _load_records()
    existing = {(r['date'], r['symbol']) for r in records}

    try:
        for i, day in enumerate(days):
            _backfill_status['progress'] = i
            _backfill_status['message']  = f'Processing {day}…'

            # Skip day entirely if all symbols already present
            if all((day, sym) in existing for sym in symbols):
                skipped += len(symbols)
                continue

            oi_data = _fetch_nse_bhavcopy_oi(day, symbols)
            time.sleep(1)  # gentle rate limit between NSE requests

            for symbol in symbols:
                if (day, symbol) in existing:
                    skipped += 1
                    continue

                sym_oi = oi_data.get(symbol, {})
                if not sym_oi.get('ce_oi') and not sym_oi.get('pe_oi'):
                    # Holiday or no bhavcopy data for this symbol/date
                    errors.append(f"{symbol} {day}")
                    continue

                ohlc = _fetch_spot_ohlc(symbol, day, provider)

                record = {
                    'date':        day,
                    'symbol':      symbol,
                    'ce_oi':       sym_oi.get('ce_oi', 0),
                    'pe_oi':       sym_oi.get('pe_oi', 0),
                    'open':        ohlc.get('open', 0),
                    'high':        ohlc.get('high', 0),
                    'low':         ohlc.get('low', 0),
                    'close':       ohlc.get('close', 0),
                    'idx_fut_oi':  sym_oi.get('idx_fut_oi', 0) or None,
                }
                records.append(record)
                existing.add((day, symbol))
                inserted += 1

            # Save incrementally so progress is not lost on crash
            with _lock:
                _save_records(records)

        _backfill_status.update({
            'running':  False,
            'progress': len(days),
            'message':  (f'Done: {inserted} inserted, {skipped} skipped, '
                         f'{len(errors)} errors'),
            'errors':   errors,
        })
        logger.info(
            f"[HistoricOI] NSE backfill done: {inserted} inserted, "
            f"{skipped} skipped, {len(errors)} errors"
        )

    except Exception as e:
        _backfill_status.update({
            'running': False,
            'message': f'Error: {e}',
            'errors':  errors + [str(e)],
        })
        logger.error(f"[HistoricOI] NSE backfill crashed: {e}", exc_info=True)


def backfill_from_nse(from_date: str, to_date: str,
                      symbols: Optional[List[str]] = None,
                      provider=None) -> Dict[str, Any]:
    """
    Start a background thread that downloads NSE F&O bhavcopies for every
    trading day in [from_date, to_date] and backfills OI + FII + OHLC.
    Poll /api/oi-historic/backfill-status for progress.
    Returns immediately with {'started': True}.
    """
    global _backfill_status

    if _backfill_status.get('running'):
        return {'success': False, 'error': 'Backfill already running'}

    if symbols is None:
        symbols = _SYMBOLS

    if provider is None:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider(user='Mine')

    t = threading.Thread(
        target=_run_nse_backfill,
        args=(from_date, to_date, symbols, provider),
        daemon=True,
    )
    t.start()
    return {'success': True, 'started': True}


def _run_load_all(from_date: str, to_date: str,
                  source: str, symbols: List[str], provider,
                  force: bool = False) -> None:
    """
    Single-pass background worker — per day: one bhavcopy download covers
    CE/PE OI + Fut OI, and one NSE index snapshot download covers spot OHLC
    (the grid shows NIFTY index values, never futures prices).
    force=True re-downloads and overwrites existing records (preserving FII_Index_futures).
    """
    global _backfill_status
    try:
        if source == 'sqlite':
            _backfill_status.update({
                'running': True, 'progress': 0, 'total': 1,
                'message': 'Loading from SQLite…', 'errors': [],
            })
            backfill_from_sqlite(symbols, provider)
            _backfill_status.update({'running': False, 'progress': 1, 'message': 'Done!'})
            return

        # NSE bhavcopy — single pass per day
        days = _business_days(from_date, to_date)
        _backfill_status.update({
            'running': True, 'progress': 0, 'total': len(days),
            'message': 'Starting…', 'errors': [],
        })

        with _lock:
            records = _load_records()

        # Build (date, symbol) → list index for fast in-place patching
        idx_map: Dict[tuple, int] = {
            (r['date'], r['symbol']): i for i, r in enumerate(records)
        }
        existing_keys = set(idx_map.keys())
        errors:  List[str] = []
        inserted = 0
        updated  = 0

        for i, day in enumerate(days):
            _backfill_status.update({'progress': i, 'message': f'Processing {day}…'})
            changed = False

            # In normal mode, skip days where every symbol already has OHLC + Fut OI
            if not force:
                present_keys = [(day, sym) for sym in symbols if (day, sym) in existing_keys]
                if (len(present_keys) == len(symbols)
                        and all(float(records[idx_map[k]].get('close', 0) or 0) > 0
                                for k in present_keys)
                        and all(records[idx_map[k]].get('idx_fut_oi') is not None
                                for k in present_keys)):
                    continue  # fully populated — skip bhavcopy and broker calls

            # ── Bhavcopy phase ──────────────────────────────────────────────
            oi_data = _fetch_nse_bhavcopy_oi(day, symbols)
            time.sleep(0.5)

            if oi_data:
                for symbol in symbols:
                    sym_oi = oi_data.get(symbol, {})
                    if not sym_oi.get('ce_oi') and not sym_oi.get('pe_oi'):
                        continue

                    key = (day, symbol)
                    if key not in existing_keys:
                        records.append({
                            'date':              day,
                            'symbol':            symbol,
                            'ce_oi':             sym_oi.get('ce_oi', 0),
                            'pe_oi':             sym_oi.get('pe_oi', 0),
                            'open':              0,
                            'high':              0,
                            'low':               0,
                            'close':             0,
                            'idx_fut_oi':        sym_oi.get('idx_fut_oi') or None,
                            'FII_Index_futures': 0,
                        })
                        idx_map[key] = len(records) - 1
                        existing_keys.add(key)
                        changed = True
                        inserted += 1
                    else:
                        rec = records[idx_map[key]]
                        if force:
                            records[idx_map[key]] = {
                                **rec,
                                'ce_oi':      sym_oi.get('ce_oi', rec.get('ce_oi', 0)),
                                'pe_oi':      sym_oi.get('pe_oi', rec.get('pe_oi', 0)),
                                'idx_fut_oi': sym_oi.get('idx_fut_oi') or None,
                            }
                            changed = True
                            updated += 1
                        else:
                            if rec.get('idx_fut_oi') is None and sym_oi.get('idx_fut_oi'):
                                records[idx_map[key]] = {
                                    **rec, 'idx_fut_oi': sym_oi['idx_fut_oi'],
                                }
                                changed = True
                                updated += 1

            # ── Spot OHLC (NSE index snapshot; broker index candle fallback) ─
            # OHLC always comes from the NIFTY index spot, never futures.
            # Normal mode: fills missing OHLC only.
            # Force/recalculate mode: refreshes OHLC for all existing records.
            spot_map = _fetch_nse_index_spot_ohlc(day, symbols)
            for symbol in symbols:
                key = (day, symbol)
                if key not in existing_keys:
                    continue
                rec = records[idx_map[key]]
                if float(rec.get('close', 0) or 0) > 0 and not force:
                    continue  # has OHLC in normal mode — skip
                ohlc = spot_map.get(symbol) or {}
                if not ohlc.get('close') and provider:
                    _backfill_status['message'] = f'OHLC {symbol} {day}…'
                    ohlc = _fetch_day_ohlc(symbol, day, provider)
                if ohlc.get('close') and any(rec.get(k) != v for k, v in ohlc.items()):
                    records[idx_map[key]] = {**records[idx_map[key]], **ohlc}
                    changed = True
                    updated += 1

            if changed:
                with _lock:
                    _save_records(records)

        # Fold-in FII sync: after a historical load, patch the recent ~30-day
        # FII flow window so the FII Fut column fills in without a separate step.
        try:
            _backfill_status['message'] = 'Syncing FII flow…'
            fii_map = _fetch_moneycontrol_fii_map()
            if fii_map:
                _patch_fii_flow(fii_map)
        except Exception as e:
            logger.warning(f'[HistoricOI] FII sync after load_all failed: {e}')

        if inserted == 0 and updated == 0:
            done_msg = f'Already up to date ({len(days)} days checked, nothing to add)'
        else:
            parts = []
            if inserted: parts.append(f'{inserted} added')
            if updated:  parts.append(f'{updated} updated')
            done_msg = 'Done — ' + ', '.join(parts)

        _backfill_status.update({
            'running':  False,
            'progress': len(days),
            'message':  done_msg,
            'errors':   errors,
        })
        logger.info(f'[HistoricOI] load_all complete: {inserted} inserted, {updated} updated')

    except Exception as e:
        _backfill_status.update({
            'running': False,
            'message': f'Error: {e}',
            'errors':  [str(e)],
        })
        logger.error(f'[HistoricOI] load_all crashed: {e}', exc_info=True)


def load_all(from_date: str = '', to_date: str = '',
             source: str = 'nse',
             symbols: Optional[List[str]] = None,
             provider=None,
             force: bool = False) -> Dict[str, Any]:
    """
    Start the consolidated background load job.
    source='nse'    → download NSE bhavcopies for [from_date, to_date]
    source='sqlite' → extract from local oi_data.db (date range ignored)
    force=True      → re-download and overwrite existing records (preserves FII_Index_futures)
    """
    global _backfill_status
    if _backfill_status.get('running'):
        return {'success': False, 'error': 'Already running'}
    if source == 'nse' and (not from_date or not to_date):
        return {'success': False, 'error': 'from_date and to_date are required for NSE source'}
    if symbols is None:
        symbols = _SYMBOLS
    if provider is None:
        from trading_app.service.provider_logic import get_data_provider
        provider = get_data_provider(user='Mine')
    threading.Thread(
        target=_run_load_all,
        args=(from_date, to_date, source, symbols, provider, force),
        daemon=True,
    ).start()
    return {'success': True, 'started': True}


def fetch_and_store_all(provider=None) -> List[Dict[str, Any]]:
    """Fetch and store records for all tracked symbols. Returns list of result dicts.

    Like the per-row refresh, this pulls every source for today in one go:
    one bhavcopy download (idx_fut_oi + official OHLC) and one Moneycontrol
    fetch (FII index-futures flow), both shared across all symbols.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    todays_bhavcopy = _fetch_nse_bhavcopy_oi(today, _SYMBOLS)
    # FII index-futures flow (Moneycontrol, ~30-day window). Best-effort: a
    # Moneycontrol hiccup must not abort the daily record.
    try:
        fii_map = _fetch_moneycontrol_fii_map()
    except Exception as e:
        logger.warning(f"[HistoricOI] Moneycontrol FII fetch failed: {e}")
        fii_map = {}
    # NSE publishes the FII index-futures flow the same evening; Moneycontrol
    # lags ~1 day. Prefer NSE so today's record is never left blank, and fall
    # back to Moneycontrol's ~30-day map when NSE has no file (holiday/pre-EOD).
    todays_fii_flow = _fetch_nse_fii_idx_fut_flow(today)
    if todays_fii_flow is None:
        todays_fii_flow = fii_map.get(today)

    results = []
    for symbol in _SYMBOLS:
        result = fetch_and_store(
            symbol,
            provider=provider,
            bhavcopy_data=todays_bhavcopy.get(symbol, {}),
            fii_flow=todays_fii_flow,
        )
        result['symbol'] = symbol
        results.append(result)

    # Fold-in FII sync: patch the whole ~30-day window, not just today. This
    # backfills the FII Fut column on prior days that Moneycontrol only publishes
    # on T+1, so the manual button and the 8 PM job never leave a stale gap.
    if fii_map:
        _patch_fii_flow(fii_map)
    return results


# ── Next-session prediction from 5-year conditional statistics ───────────────
#
# Each signal below is bucketed over the full stored history; for every bucket
# we measure how often the NEXT session closed higher and its average return.
# The latest record's buckets are then looked up and blended into an outlook.
# Purely statistical tendencies (in-sample) — shown with sample sizes so the
# user can judge the strength of each reason.

_PRED_SIGNALS = [
    {
        'key':   'pcr',
        'name':  'PCR (PE/CE OI)',
        'edges': [0.7, 0.9, 1.1, 1.3],
        'labels': ['< 0.70 extreme call-heavy', '0.70–0.90 call-heavy',
                   '0.90–1.10 balanced', '1.10–1.30 put-heavy',
                   '≥ 1.30 extreme put-heavy'],
        'why': ['calls dominate the chain — positioning is max-bearish, often a capitulation zone',
                'call writers in control — supply of calls caps upside',
                'options positioning gives no directional edge',
                'put writers in control — sold strikes below act as support',
                'heavy put writing — strong support base, though stretched readings can precede pullbacks'],
        'fmt': lambda v: f'{v:.2f}',
    },
    {
        'key':   'd_diff',
        'name':  'CHNG OPT OI shift (Δ(PE−CE) / total)',
        'edges': [-0.08, -0.02, 0.02, 0.08],
        'labels': ['strong call writing', 'call writing', 'balanced writing',
                   'put writing', 'strong put writing'],
        'why': ['today\'s fresh OI went heavily into calls — writers betting against a rally',
                'net new call OI added — mild bearish positioning',
                'fresh writing was two-sided — no positioning signal',
                'net new put OI added — writers comfortable selling downside',
                'today\'s fresh OI went heavily into puts — aggressive bullish positioning'],
        'fmt': lambda v: f'{v * 100:+.1f}%',
    },
    {
        'key':   'd_fii_oi',
        'name':  'FII Chng Fut OI (contracts)',
        'edges': [-40000, -8000, 8000, 40000],
        'labels': ['heavy long unwind / shorting', 'net selling', 'flat',
                   'net buying', 'aggressive long build-up'],
        'why': ['FIIs dumped index-futures longs or added shorts — institutional bearishness',
                'FIIs trimmed net futures longs',
                'FII futures book barely moved',
                'FIIs added net futures longs',
                'FIIs built index-futures longs aggressively — institutional conviction'],
        'fmt': lambda v: f'{v:+,.0f}',
    },
    {
        'key':   'flow',
        'name':  'FII index-futures flow (₹ Cr)',
        'edges': [-1500, 0, 1500],
        'labels': ['heavy outflow', 'outflow', 'inflow', 'heavy inflow'],
        'why': ['large FII money left index futures',
                'FII money mildly negative in index futures',
                'FII money mildly positive in index futures',
                'large FII money entered index futures'],
        'fmt': lambda v: f'{v:+,.0f} Cr',
    },
    {
        'key':   'ret',
        'name':  'Today\'s NIFTY move',
        'edges': [-0.75, -0.15, 0.15, 0.75],
        'labels': ['sharp fall', 'dip', 'flat', 'rise', 'sharp rally'],
        'why': ['momentum after sharp falls', 'follow-through after dips',
                'no momentum signal from price', 'follow-through after up days',
                'momentum after sharp rallies'],
        'fmt': lambda v: f'{v:+.2f}%',
    },
]


def _pred_bucket(value: float, edges: List[float]) -> int:
    for i, e in enumerate(edges):
        if value < e:
            return i
    return len(edges)


def analyze_and_predict(symbol: str = 'NIFTY') -> Dict[str, Any]:
    """
    5-year analysis of the historic OI records → outlook for the next session.
    Returns { success, prediction, prob_up_pct, base_rate_pct, expected_move_pct,
              backtest_hit_pct, reasons: [...], sample_days, from/to dates, ... }.
    """
    recs = [r for r in get_all_records()
            if r.get('symbol') == symbol and float(r.get('close') or 0) > 0]
    recs.sort(key=lambda r: r['date'])
    if len(recs) < 120:
        return {'success': False, 'error': 'Not enough history for analysis'}

    # ── Feature rows ─────────────────────────────────────────────────────────
    feats: List[Dict[str, Any]] = []
    for i, r in enumerate(recs):
        prev = recs[i - 1] if i > 0 else None
        ce, pe = float(r.get('ce_oi') or 0), float(r.get('pe_oi') or 0)
        tot = ce + pe
        f: Dict[str, Any] = {'date': r['date'], 'close': float(r['close'])}
        f['pcr'] = (pe / ce) if ce > 0 else None
        f['d_diff'] = None
        f['ret']    = None
        f['d_fii_oi'] = None
        if prev:
            p_ce, p_pe = float(prev.get('ce_oi') or 0), float(prev.get('pe_oi') or 0)
            p_tot = p_ce + p_pe
            # Skip Δ on chain-roll days (expiry-day records hold next week's
            # chain, so the day-over-day delta would compare different chains)
            if tot > 0 and p_tot > 0 and tot >= 0.7 * p_tot:
                f['d_diff'] = ((pe - ce) - (p_pe - p_ce)) / tot
            p_close = float(prev.get('close') or 0)
            if p_close > 0:
                f['ret'] = (f['close'] / p_close - 1) * 100
            if r.get('fii_fut_oi') is not None and prev.get('fii_fut_oi') is not None:
                f['d_fii_oi'] = float(r['fii_fut_oi']) - float(prev['fii_fut_oi'])
        flow = r.get('FII_Index_futures')
        f['flow'] = float(flow) if flow else None   # 0 == not published → skip
        feats.append(f)

    for i in range(len(feats) - 1):
        feats[i]['up'] = feats[i + 1]['close'] > feats[i]['close']
        feats[i]['next_ret'] = (feats[i + 1]['close'] / feats[i]['close'] - 1) * 100

    hist   = feats[:-1]           # days with a known next-session outcome
    latest = feats[-1]
    base_rate = sum(1 for f in hist if f['up']) / len(hist)

    # ── Bucket statistics per signal over the full history ──────────────────
    sig_stats: Dict[str, List[Dict[str, float]]] = {}
    for sig in _PRED_SIGNALS:
        buckets = [{'n': 0, 'ups': 0, 'ret': 0.0} for _ in range(len(sig['edges']) + 1)]
        for f in hist:
            v = f.get(sig['key'])
            if v is None:
                continue
            b = buckets[_pred_bucket(v, sig['edges'])]
            b['n']   += 1
            b['ups'] += 1 if f['up'] else 0
            b['ret'] += f['next_ret']
        sig_stats[sig['key']] = buckets

    def _day_probs(f: Dict[str, Any]) -> List[float]:
        out = []
        for sig in _PRED_SIGNALS:
            v = f.get(sig['key'])
            if v is None:
                continue
            b = sig_stats[sig['key']][_pred_bucket(v, sig['edges'])]
            if b['n'] >= 30:                       # ignore thin buckets
                out.append(b['ups'] / b['n'])
        return out

    # ── In-sample backtest of the blended score ──────────────────────────────
    hits = tried = 0
    for f in hist:
        probs = _day_probs(f)
        if not probs:
            continue
        tried += 1
        if (sum(probs) / len(probs) >= 0.5) == f['up']:
            hits += 1
    backtest_hit = (hits / tried * 100) if tried else None

    # ── Latest day → reasons + blended outlook ───────────────────────────────
    reasons = []
    probs, rets = [], []
    for sig in _PRED_SIGNALS:
        v = latest.get(sig['key'])
        if v is None:
            continue
        bi = _pred_bucket(v, sig['edges'])
        b  = sig_stats[sig['key']][bi]
        if b['n'] < 30:
            continue
        p_up    = b['ups'] / b['n']
        avg_ret = b['ret'] / b['n']
        probs.append(p_up)
        rets.append(avg_ret)
        reasons.append({
            'name':        sig['name'],
            'value':       sig['fmt'](v),
            'bucket':      sig['labels'][bi],
            'why':         sig['why'][bi],
            'prob_up_pct': round(p_up * 100, 1),
            'avg_ret_pct': round(avg_ret, 2),
            'n':           b['n'],
            'bullish':     p_up > base_rate,
        })
    if not probs:
        return {'success': False, 'error': 'Latest record has no usable signals'}

    prob_up = sum(probs) / len(probs)
    edge    = (prob_up - base_rate) * 100
    if edge >= 3:
        prediction = 'BULLISH'
    elif edge <= -3:
        prediction = 'BEARISH'
    else:
        prediction = 'SIDEWAYS'
    reasons.sort(key=lambda r: abs(r['prob_up_pct'] - base_rate * 100), reverse=True)

    # Next session = next weekday after the latest record
    nxt = datetime.strptime(latest['date'], '%Y-%m-%d') + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)

    return {
        'success':           True,
        'symbol':            symbol,
        'as_of':             latest['date'],
        'next_session':      nxt.strftime('%Y-%m-%d'),
        'prediction':        prediction,
        'prob_up_pct':       round(prob_up * 100, 1),
        'base_rate_pct':     round(base_rate * 100, 1),
        'edge_pct':          round(edge, 1),
        'expected_move_pct': round(sum(rets) / len(rets), 2),
        'backtest_hit_pct':  round(backtest_hit, 1) if backtest_hit is not None else None,
        'sample_days':       len(hist),
        'from_date':         recs[0]['date'],
        'to_date':           latest['date'],
        'reasons':           reasons,
    }


def refresh_record(date: str, symbol: str, provider=None) -> Dict[str, Any]:
    """
    Refresh every field of a single (date, symbol) row from all sources in one
    pass — so the user clicks once instead of juggling Record / Load / Sync FII:
      • NSE bhavcopy    → ce_oi, pe_oi, idx_fut_oi
      • NSE index snapshot → spot OHLC (NIFTY index values, never futures)
      • broker candle   → spot OHLC fallback when the index snapshot is unavailable
      • NSE participant → fii_fut_oi (market-wide FII net index-futures OI)
      • Moneycontrol    → FII_Index_futures in Cr (only if date falls in the ~30-day window)

    Each field is only overwritten when the source actually returns a value, so a
    partial fetch never wipes existing data. A manually-set FII_Index_futures is
    preserved when Moneycontrol has no value for that date.
    Returns { success, record } or { success: False, error }.
    """
    symbol = symbol.upper()
    if symbol not in _SYMBOLS:
        return {'success': False, 'error': f'Unknown symbol {symbol}'}
    try:
        if provider is None:
            from trading_app.service.provider_logic import get_data_provider
            provider = get_data_provider(user='Mine')

        # ── Bhavcopy: CE/PE OI + idx_fut_oi ─────────────────────────────────
        sym_oi = (_fetch_nse_bhavcopy_oi(date, [symbol]) or {}).get(symbol, {})

        # ── OHLC: NIFTY index spot (NSE index snapshot; broker fallback) ────
        ohlc = _fetch_spot_ohlc(symbol, date, provider)

        # ── FII net index-futures OI (NSE participant report) ───────────────
        fii_fut_oi = _fetch_nse_fii_fut_oi(date)

        # ── FII index-futures flow in Cr — NSE same-day report (authoritative,
        #    available any date), Moneycontrol ~30-day map as fallback ────────
        fii_flow = _fetch_nse_fii_idx_fut_flow(date)
        if fii_flow is None:
            fii_flow = _fetch_moneycontrol_fii_map().get(date)

        with _lock:
            records  = _load_records()
            existing = next(
                (r for r in records
                 if r.get('date') == date and r.get('symbol') == symbol),
                None,
            )

            # Nothing on disk and nothing fetched → genuinely no data for this day
            if existing is None and not sym_oi and not ohlc.get('close'):
                return {'success': False,
                        'error': f'No data available for {symbol} on {date}'}

            merged = dict(existing) if existing else {'date': date, 'symbol': symbol}
            if sym_oi.get('ce_oi'):      merged['ce_oi'] = sym_oi['ce_oi']
            if sym_oi.get('pe_oi'):      merged['pe_oi'] = sym_oi['pe_oi']
            if ohlc.get('close'):        merged.update(ohlc)
            if sym_oi.get('idx_fut_oi'): merged['idx_fut_oi'] = sym_oi['idx_fut_oi']
            if fii_fut_oi is not None:   merged['fii_fut_oi'] = fii_fut_oi
            if fii_flow is not None:     merged['FII_Index_futures'] = fii_flow
            merged.setdefault('idx_fut_oi', None)
            merged.setdefault('FII_Index_futures', 0)

            if existing is not None:
                records[records.index(existing)] = merged
            else:
                records.append(merged)
            _save_records(records)

        logger.info(
            f'[HistoricOI] Refreshed {symbol} {date}: '
            f"CE={merged.get('ce_oi')} PE={merged.get('pe_oi')} "
            f"close={merged.get('close')} futOI={merged.get('idx_fut_oi')} "
            f"fiiFutOI={merged.get('fii_fut_oi')} fiiFlow={merged.get('FII_Index_futures')}"
        )
        return {'success': True, 'record': merged}

    except Exception as e:
        logger.error(f'[HistoricOI] refresh_record failed for {symbol} {date}: {e}',
                     exc_info=True)
        return {'success': False, 'error': str(e)}
