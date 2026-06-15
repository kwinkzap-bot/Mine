"""
OI Historic Data — daily end-of-day CE/PE open interest snapshot.

Records are stored in oi_historic_data.json at the project root.
Each record: { date, symbol, pe_oi, ce_oi, chng_pe_oi, chng_ce_oi }
Upsert logic: one record per (date, symbol); re-fetching on the same day overwrites.
"""
import json
import os
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import logging
logger = logging.getLogger(__name__)

_lock = threading.Lock()

_SYMBOLS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']


def _get_json_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.normpath(os.path.join(here, '..', '..', '..', '..'))
    return os.path.join(project_root, 'oi_historic_data.json')


def _load_records() -> List[Dict[str, Any]]:
    path = _get_json_path()
    if not os.path.exists(path):
        return []
    with open(path, 'r') as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return []


def _save_records(records: List[Dict[str, Any]]) -> None:
    path = _get_json_path()
    with open(path, 'w') as f:
        json.dump(records, f, indent=2)


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


_SYMBOL_INSTRUMENT_KEY = {
    'fyers': {
        'NIFTY':     'NSE:NIFTY50-INDEX',
        'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
        'FINNIFTY':  'NSE:FINNIFTY-INDEX',
    },
    'kite': {
        'NIFTY':     'NSE:NIFTY 50',
        'BANKNIFTY': 'NSE:NIFTY BANK',
        'FINNIFTY':  'NSE:NIFTY FIN SERVICE',
    },
}


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


def fetch_and_store(symbol: str, provider=None) -> Dict[str, Any]:
    """
    Fetch today's OI totals for *symbol* from the broker and upsert into JSON.

    Change columns (chng_ce_oi, chng_pe_oi) are computed as the difference
    between the live CE/PE OI and the most recent previously stored record for
    the same symbol, so they always reflect a real day-over-day delta rather
    than the static intraday value returned by the broker API.
    """
    try:
        if provider is None:
            from trading_app.service.provider_logic import get_data_provider
            provider = get_data_provider(user='Mine')

        if not provider:
            return {'success': False, 'error': 'No data provider available'}

        from trading_app.service.open_interest_service import OpenInterestService
        oi_service = OpenInterestService(provider)
        # On expiry day, use_next_expiry=True skips today's expiry and records
        # next week's OI instead (non-expiry days: no behavioural difference).
        data = oi_service.get_open_interest_data(symbol, use_next_expiry=True)

        if not data.get('success'):
            return {'success': False, 'error': data.get('error', 'OI fetch failed')}

        ce_summary = data.get('ce_summary', {})
        pe_summary = data.get('pe_summary', {})

        # Fallback: sum from strike lists if summaries are absent
        if not ce_summary and not pe_summary:
            ce_strikes = data.get('ce_strikes', [])
            pe_strikes = data.get('pe_strikes', [])
            total_ce_oi = sum(s.get('oi', 0) for s in ce_strikes)
            total_pe_oi = sum(s.get('oi', 0) for s in pe_strikes)
        else:
            total_ce_oi = ce_summary.get('total_oi', 0)
            total_pe_oi = pe_summary.get('total_oi', 0)

        today = datetime.now().strftime('%Y-%m-%d')

        # Fetch underlying day OHLC
        ohlc = _fetch_day_ohlc(symbol, today, provider)

        # Compute change vs the last stored record for this symbol
        # (skip any record already stored for today so an overwrite stays meaningful)
        with _lock:
            records = _load_records()
            prev = next(
                (r for r in sorted(records, key=lambda x: x.get('date', ''), reverse=True)
                 if r.get('symbol') == symbol and r.get('date', '') < today),
                None
            )

        if prev is not None:
            chng_ce_oi = int(total_ce_oi) - int(prev.get('ce_oi', 0))
            chng_pe_oi = int(total_pe_oi) - int(prev.get('pe_oi', 0))
            logger.info(
                f"[HistoricOI] {symbol} change vs {prev['date']}: "
                f"CE {chng_ce_oi:+,}  PE {chng_pe_oi:+,}"
            )
        else:
            # First-ever record — no baseline to diff against
            chng_ce_oi = 0
            chng_pe_oi = 0
            logger.info(f"[HistoricOI] {symbol}: first record, no previous baseline")

        record = {
            'date':       today,
            'symbol':     symbol,
            'ce_oi':      int(total_ce_oi),
            'pe_oi':      int(total_pe_oi),
            'chng_ce_oi': chng_ce_oi,
            'chng_pe_oi': chng_pe_oi,
            'open':       ohlc.get('open', 0),
            'high':       ohlc.get('high', 0),
            'low':        ohlc.get('low', 0),
            'close':      ohlc.get('close', 0),
        }

        with _lock:
            records = _load_records()
            updated = False
            for i, r in enumerate(records):
                if r.get('date') == today and r.get('symbol') == symbol:
                    records[i] = record
                    updated = True
                    break
            if not updated:
                records.append(record)
            _save_records(records)

        logger.info(f"[HistoricOI] Stored {symbol} on {today}: CE={total_ce_oi:,} PE={total_pe_oi:,}")
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

    for i, r in enumerate(records):
        # Already has OHLC data — skip
        if r.get('close') and float(r['close']) > 0:
            skipped += 1
            continue

        date_str = r.get('date', '')
        symbol   = r.get('symbol', '')
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


def fetch_and_store_all(provider=None) -> List[Dict[str, Any]]:
    """Fetch and store records for all tracked symbols. Returns list of result dicts."""
    results = []
    for symbol in _SYMBOLS:
        result = fetch_and_store(symbol, provider=provider)
        result['symbol'] = symbol
        results.append(result)
    return results
