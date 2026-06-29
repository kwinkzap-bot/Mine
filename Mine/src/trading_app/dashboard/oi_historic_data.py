"""
OI Historic Data — daily end-of-day CE/PE open interest snapshot.

Records are stored in per-symbol JSON files at the project root:
  Nifty_oi_historic_data.json, BankNifty_oi_historic_data.json
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

_SYMBOLS = ['NIFTY', 'BANKNIFTY']

# Per-symbol JSON filenames at the project root
_SYMBOL_FILE = {
    'NIFTY':     'Nifty_oi_historic_data.json',
    'BANKNIFTY': 'BankNifty_oi_historic_data.json',
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
        'NIFTY':     'NSE:NIFTY50-INDEX',
        'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
    },
    'kite': {
        'NIFTY':     'NSE:NIFTY 50',
        'BANKNIFTY': 'NSE:NIFTY BANK',
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



def _fetch_nse_bhavcopy_oi(date_str: str, symbols: List[str]) -> Dict[str, Dict]:
    """
    Download NSE F&O bhavcopy ZIP for date_str and return per-symbol data:
      { symbol: { ce_oi, pe_oi, idx_fut_oi, open, high, low, close } }

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
    expiry_map:     Dict[str, Any] = {}
    fut_expiry_map: Dict[str, Any] = {}

    for row in rows:
        instr = row.get('INSTRUMENT', '').strip()
        if instr not in ('OPTIDX', 'FUTIDX'):
            continue
        sym = row.get('SYMBOL', '').strip()
        if sym not in sym_set:
            continue
        try:
            exp_dt = datetime.strptime(row['EXPIRY_DT'].strip(), '%d-%b-%Y').date()
        except (ValueError, KeyError):
            continue
        if exp_dt >= today_dt:
            if instr == 'OPTIDX':
                if sym not in expiry_map or exp_dt < expiry_map[sym]:
                    expiry_map[sym] = exp_dt
            else:
                if sym not in fut_expiry_map or exp_dt < fut_expiry_map[sym]:
                    fut_expiry_map[sym] = exp_dt

    result: Dict[str, Dict] = {
        s: {'ce_oi': 0, 'pe_oi': 0, 'idx_fut_oi': 0,
            'open': 0.0, 'high': 0.0, 'low': 0.0, 'close': 0.0}
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
            if sym in fut_expiry_map and exp_dt == fut_expiry_map[sym]:
                try:
                    result[sym]['open']  = round(float(row.get('OPEN',      0) or 0), 2)
                    result[sym]['high']  = round(float(row.get('HIGH',      0) or 0), 2)
                    result[sym]['low']   = round(float(row.get('LOW',       0) or 0), 2)
                    result[sym]['close'] = round(float(
                        row.get('SETTLE_PR', 0) or row.get('CLOSE', 0) or 0), 2)
                except (ValueError, TypeError):
                    pass
    return result


def _parse_new_bhavcopy(rows: List[Dict], symbols: List[str],
                         sym_set: set, today_dt) -> Dict[str, Dict]:
    """Parse the post-2024-07-08 bhavcopy format (FinInstrmTp / TckrSymb / XpryDt columns)."""
    expiry_map:     Dict[str, Any] = {}
    fut_expiry_map: Dict[str, Any] = {}

    for row in rows:
        instr = row.get('FinInstrmTp', '').strip()
        if instr not in ('IDO', 'IDF'):
            continue
        sym = row.get('TckrSymb', '').strip()
        if sym not in sym_set:
            continue
        try:
            exp_dt = datetime.strptime(row['XpryDt'].strip(), '%Y-%m-%d').date()
        except (ValueError, KeyError):
            continue
        if exp_dt >= today_dt:
            if instr == 'IDO':
                if sym not in expiry_map or exp_dt < expiry_map[sym]:
                    expiry_map[sym] = exp_dt
            else:
                if sym not in fut_expiry_map or exp_dt < fut_expiry_map[sym]:
                    fut_expiry_map[sym] = exp_dt

    result: Dict[str, Dict] = {
        s: {'ce_oi': 0, 'pe_oi': 0, 'idx_fut_oi': 0,
            'open': 0.0, 'high': 0.0, 'low': 0.0, 'close': 0.0}
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
            if sym in fut_expiry_map and exp_dt == fut_expiry_map[sym]:
                try:
                    result[sym]['open']  = round(float(row.get('OpnPric', 0) or 0), 2)
                    result[sym]['high']  = round(float(row.get('HghPric', 0) or 0), 2)
                    result[sym]['low']   = round(float(row.get('LwPric',  0) or 0), 2)
                    result[sym]['close'] = round(float(
                        row.get('SttlmPric', 0) or row.get('ClsPric', 0) or 0), 2)
                except (ValueError, TypeError):
                    pass
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

        # Prefer bhavcopy OHLC (official SETTLE_PR); broker OHLC as fallback
        if bhav.get('close'):
            ohlc = {k: bhav[k] for k in ('open', 'high', 'low', 'close')}
        else:
            ohlc = _fetch_day_ohlc(symbol, today, provider)

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

        ohlc = _fetch_day_ohlc(symbol, date_str, provider)
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

        with _lock:
            records = _load_records()

        updated = 0
        for i, r in enumerate(records):
            d = r.get('date', '')
            if d in fii_map:
                records[i] = {**r, 'FII_Index_futures': fii_map[d]}
                updated += 1

        with _lock:
            _save_records(records)

        dates = sorted(fii_map)
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

        ohlc = _fetch_day_ohlc(symbol, d, provider) if provider else {}

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

                ohlc = _fetch_day_ohlc(symbol, day, provider) if provider else {}

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
    Single-pass background worker — one bhavcopy download per day covers
    CE/PE OI + Fut OI + OHLC (near-month futures SETTLE_PR as close proxy).
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
                            'open':              sym_oi.get('open', 0),
                            'high':              sym_oi.get('high', 0),
                            'low':               sym_oi.get('low', 0),
                            'close':             sym_oi.get('close', 0),
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
                                'open':       sym_oi.get('open', 0),
                                'high':       sym_oi.get('high', 0),
                                'low':        sym_oi.get('low', 0),
                                'close':      sym_oi.get('close', 0),
                                'idx_fut_oi': sym_oi.get('idx_fut_oi') or None,
                            }
                            changed = True
                            updated += 1
                        else:
                            patch: Dict[str, Any] = {}
                            if not float(rec.get('close', 0) or 0) and sym_oi.get('close'):
                                patch.update({
                                    'open':  sym_oi.get('open', 0),
                                    'high':  sym_oi.get('high', 0),
                                    'low':   sym_oi.get('low', 0),
                                    'close': sym_oi.get('close', 0),
                                })
                            if rec.get('idx_fut_oi') is None and sym_oi.get('idx_fut_oi'):
                                patch['idx_fut_oi'] = sym_oi['idx_fut_oi']
                            if patch:
                                records[idx_map[key]] = {**rec, **patch}
                                changed = True
                                updated += 1

            # ── Broker OHLC fallback ─────────────────────────────────────────
            # Only runs when bhavcopy failed (post-2024 archive cutoff or holiday).
            # Normal mode: fills missing OHLC only.
            # Force/recalculate mode: refreshes OHLC for all existing records.
            if provider and not oi_data:
                for symbol in symbols:
                    key = (day, symbol)
                    if key not in existing_keys:
                        continue
                    rec = records[idx_map[key]]
                    if float(rec.get('close', 0) or 0) > 0 and not force:
                        continue  # has OHLC in normal mode — skip
                    _backfill_status['message'] = f'OHLC {symbol} {day}…'
                    ohlc = _fetch_day_ohlc(symbol, day, provider)
                    if ohlc.get('close'):
                        records[idx_map[key]] = {**records[idx_map[key]], **ohlc}
                        changed = True
                        updated += 1

            if changed:
                with _lock:
                    _save_records(records)

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
    # Best-effort: a Moneycontrol hiccup must not abort the daily record.
    try:
        todays_fii_flow = _fetch_moneycontrol_fii_map().get(today)
    except Exception as e:
        logger.warning(f"[HistoricOI] Moneycontrol FII fetch failed: {e}")
        todays_fii_flow = None

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
    return results


def refresh_record(date: str, symbol: str, provider=None) -> Dict[str, Any]:
    """
    Refresh every field of a single (date, symbol) row from all sources in one
    pass — so the user clicks once instead of juggling Record / Load / Sync FII:
      • NSE bhavcopy    → ce_oi, pe_oi, OHLC, idx_fut_oi
      • broker candle   → OHLC fallback when bhavcopy is unavailable (post-archive / holiday)
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

        # ── Bhavcopy: CE/PE OI + OHLC + idx_fut_oi ──────────────────────────
        sym_oi = (_fetch_nse_bhavcopy_oi(date, [symbol]) or {}).get(symbol, {})

        # ── OHLC: prefer bhavcopy SETTLE_PR, broker candle as fallback ──────
        if sym_oi.get('close'):
            ohlc = {k: sym_oi.get(k, 0) for k in ('open', 'high', 'low', 'close')}
        elif provider:
            ohlc = _fetch_day_ohlc(symbol, date, provider)
        else:
            ohlc = {}

        # ── FII net index-futures OI (NSE participant report) ───────────────
        fii_fut_oi = _fetch_nse_fii_fut_oi(date)

        # ── FII index-futures flow in Cr (Moneycontrol, ~30-day window) ─────
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
