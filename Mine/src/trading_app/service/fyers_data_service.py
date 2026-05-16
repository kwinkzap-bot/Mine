import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
import requests
import re

import time
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import threading

class FyersRateLimiter:
    """Thread-safe rate limiter for Fyers API (10 requests/second)."""
    def __init__(self, requests_per_second: float = 10.0):
        self.delay = 1.0 / requests_per_second
        self.next_call = 0.0
        self.priority_next_call = 0.0 # Dedicated slot tracking for critical requests
        self.lock = threading.Lock()

    def wait(self, priority: int = 0):
        """
        priority 1: High (Charts, LTP) - Faster slots
        priority 0: Low (Quotes, Chain) - Standard slots
        """
        sleep_time = 0
        with self.lock:
            now = time.time()
            # High priority (Charts) get nearly instant slots
            effective_delay = self.delay if priority == 0 else self.delay * 0.1
            
            target_next = self.priority_next_call if priority == 1 else self.next_call
            
            if now < target_next:
                sleep_time = target_next - now
                new_next = target_next + effective_delay
            else:
                sleep_time = 0
                new_next = now + effective_delay
            
            # Synchronize both clocks
            self.next_call = max(self.next_call, new_next)
            self.priority_next_call = max(self.priority_next_call, new_next)
        
        if sleep_time > 0:
            time.sleep(sleep_time)

_rate_limiter = FyersRateLimiter(5.0) # Optimized: 5 req/s (Fyers limit is 10)
logger = logging.getLogger(__name__)

# Global Static Caches to survive transient Adapter regenerations from api.py routes
_GLOBAL_INSTRUMENTS_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_GLOBAL_INSTRUMENTS_TIMESTAMP: Dict[str, datetime] = {}
_csv_fetch_lock = threading.Lock()

_GLOBAL_OI_CHAIN_CACHE: Dict[str, Dict[str, Dict[str, float]]] = {}
_GLOBAL_OI_CHAIN_TIMESTAMP: Dict[str, datetime] = {}

# Global Token Mapping (Root:Strike:Type -> FyersSymbol) for high-speed resolution
_FYERS_OPTION_TOKEN_CACHE: Dict[str, str] = {}
_FYERS_OPTION_TOKEN_LOCK = threading.Lock()

# Historical Data Cache for Fyers
_FYERS_HIST_CACHE: Dict[str, tuple] = {}
_FYERS_HIST_LOCK = threading.Lock()

# Quotes Cache (Symbol -> (QuoteDict, Timestamp))
_FYERS_SYMBOL_CACHE: Dict[str, tuple] = {}
_FYERS_SYMBOL_LOCK = threading.Lock()

# Global API Locks to prevent parallel requests per endpoint (Serialization)
_GLOBAL_FYERS_QUOTE_LOCK = threading.Lock()
_GLOBAL_FYERS_HIST_LOCK = threading.Lock()

# Monkeypatch Fyers V3 SDK - Disabled as api-t1 is currently active and working
# try:
#     from fyers_apiv3.fyersModel import Config
#     if "api-t1" in Config.API:
#         logger.info(f"Monkeypatching Fyers API URL from {Config.API} to https://api.fyers.in/api/v3")
#         Config.API = 'https://api.fyers.in/api/v3'
#     if "api-t1" in Config.DATA_API:
#         logger.info(f"Monkeypatching Fyers DATA_API URL from {Config.DATA_API} to https://api.fyers.in/data")
#         Config.DATA_API = 'https://api.fyers.in/data'
# except Exception as e:
#     logger.warning(f"Could not monkeypatch Fyers Config: {e}")

class FyersDataServiceAdapter:
    """
    Adapter to make Fyers API v3 look like KiteConnect for data fetching.
    Maps Kite-style methods (ltp, quote, historical_data, instruments) to Fyers V3 API.

    Fyers NSE_FO.csv column mapping (0-indexed, NO header row):
      0:  fyToken
      1:  Full Name (e.g., "TATA MOTORS 23 Nov 30 370 CE")
      2:  Instrument Type (e.g., "OPTIDX", "OPTSTK")
      3:  Lot Size
      4:  Tick Size
      5:  Reserved/Empty
      6:  Trading Session
      7:  Last Update Date
      8:  Expiry Date (Unix Timestamp in seconds)
      9:  Symbol (e.g., NSE:NIFTY26APR24450CE)
     10:  Exchange (NSE/BSE)
     11:  Segment (NSE_FO)
     12:  Script Code
     13:  Short Symbol
     14:  Root / Underlying Symbol (e.g., NIFTY, BANKNIFTY) ← key for name filter
     15:  Strike Price
     16:  Option Type (CE / PE)
     17:  Underlying fyToken
    """

    def __init__(self, fyers_instance_or_app_id, access_token=None, secret=None):
        """
        Args:
            fyers_instance_or_app_id: FyersModel instance OR app_id
            access_token: Fyers access token (if first arg is app_id)
            secret: Fyers secret key (if first arg is app_id)
        """
        if access_token and secret:
            from fyers_apiv3 import fyersModel
            log_dir = os.path.join(os.getcwd(), "logs")
            if not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir)
                except Exception:
                    pass
            
            # Use the provided app_id as client_id (Fyers V3 expects full APP_ID, e.g., APP_ID-100)
            client_id = str(fyers_instance_or_app_id)
            
            # The Fyers V3 SDK expects ONLY the JWT part in the token parameter, 
            # because it automatically prepends the client_id to the Authorization header.
            # If we pass "APP_ID:JWT", it becomes "APP_ID:APP_ID:JWT" which fails.
            final_token = str(access_token)
            if ":" in final_token:
                logger.info(f"[FyersAdapter] Token contains App ID prefix. Stripping prefix for SDK initialization.")
                final_token = final_token.split(":")[-1]
            
            # Diagnostic: Show format (obfuscated)
            t_len = len(final_token)
            t_head = final_token[:15]
            logger.info(f"[FyersAdapter] Initializing FyersModel. client_id={client_id}, token_len={t_len}, head={t_head}...")
            
            self.fyers = fyersModel.FyersModel(
                client_id=client_id,
                token=final_token,
                is_async=False,
                log_path=log_dir
            )
        else:
            self.fyers = fyers_instance_or_app_id
        
        self.app_id = fyers_instance_or_app_id if isinstance(fyers_instance_or_app_id, str) else None

    @property
    def access_token(self) -> Optional[str]:
        """Expose token to mimic KiteConnect's access verification natively."""
        return getattr(self.fyers, 'token', None)

    # ──────────────────────────────────────────────────────────────────────
    # Quote / LTP
    # ──────────────────────────────────────────────────────────────────────

    def _get_fyers_oi(self, root_name: str) -> Dict[str, Dict[str, float]]:
        now = datetime.now()
        
        if root_name in _GLOBAL_OI_CHAIN_CACHE:
            if (now - _GLOBAL_OI_CHAIN_TIMESTAMP[root_name]).total_seconds() < 10:
                return _GLOBAL_OI_CHAIN_CACHE[root_name]
        
        UNDERLYING = {
            'NIFTY':       'NSE:NIFTY50-INDEX',
            'BANKNIFTY':   'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':    'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY':  'NSE:MIDCPNIFTY-INDEX',
            'SENSEX':      'BSE:SENSEX-INDEX',
        }
        
        symbol = UNDERLYING.get(root_name, f"NSE:{root_name}-EQ")
        max_retries = 3
        backoff = 2.0
        
        for attempt in range(max_retries):
            _rate_limiter.wait()
            try:
                logger.info(f"[FyersAdapter] Fetching option chain for {symbol} (root={root_name}) [Attempt {attempt+1}]")
                data = {'symbol': symbol, 'strikecount': 50}
                resp = self.fyers.optionchain(data=data)
                
                if resp.get('s') == 'ok':
                    oi_map = {}
                    options = resp.get('data', {}).get('optionsChain', [])
                    for row in options:
                        sym = row.get('symbol')
                        if sym:
                            oi_map[sym] = {
                                'oi': row.get('oi', row.get('open_interest', 0)),
                                'oich': row.get('oich', 0)
                            }
                    _GLOBAL_OI_CHAIN_CACHE[root_name] = oi_map
                    _GLOBAL_OI_CHAIN_TIMESTAMP[root_name] = now
                    return oi_map
                
                code = resp.get('code')
                if code == 429 or 'limit' in str(resp.get('message', '')).lower():
                    logger.warning(f"[FyersAdapter] Rate limit in optionchain({symbol}). Sleeping {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    logger.error(f"[FyersAdapter] optionchain failed for {symbol}: {resp}")
                    break
            except Exception as e:
                logger.error(f"[FyersAdapter] _get_fyers_oi error for {symbol}: {e}")
                time.sleep(backoff)
                backoff *= 2
        
        return {}

    def set_access_token(self, token: str):
        pass

    def quote(self, symbols: List[str], priority: int = 0) -> Dict[str, Any]:
        max_retries = 2
        backoff = 2.0
        
        kite_quotes: Dict[str, Any] = {}
        missing_symbols = []
        now = datetime.now()
        
        with _FYERS_SYMBOL_LOCK:
            for s in symbols:
                if s in _FYERS_SYMBOL_CACHE:
                    cached_q, ts = _FYERS_SYMBOL_CACHE[s]
                    if (now - ts).total_seconds() < 0.5:
                        kite_quotes[s] = cached_q
                        continue
                missing_symbols.append(s)
        
        if not missing_symbols:
            return kite_quotes
            
        logger.info(f"[FyersAdapter] Fetching {len(missing_symbols)} missing symbols from API (already had {len(kite_quotes)})")
        
        KITE_TO_FYERS_MAP = {
            'NSE:NIFTY 50': 'NSE:NIFTY50-INDEX',
            'NSE:NIFTY BANK': 'NSE:NIFTYBANK-INDEX',
            'NSE:NIFTY FIN SERVICE': 'NSE:FINNIFTY-INDEX',
            'NSE:NIFTY MID SELECT': 'NSE:MIDCPNIFTY-INDEX',
            'NSE:INDIA VIX': 'NSE:INDIAVIX-INDEX',
            'BSE:SENSEX': 'BSE:SENSEX-INDEX',
        }
        
        fyers_symbols = []
        f_to_k = {}
        for s in missing_symbols:
            fsym = KITE_TO_FYERS_MAP.get(s, s)
            if fsym.startswith('NSE:') and not any(fsym.endswith(x) for x in ['-EQ', '-INDEX', 'CE', 'PE', 'FUT']):
                fsym = f"{fsym}-EQ"
            elif fsym.startswith('BSE:') and not any(fsym.endswith(x) for x in ['-EQ', '-INDEX', 'CE', 'PE', 'FUT']):
                fsym = f"{fsym}-EQ"
            else:
                fsym = fsym.replace('NFO:', 'NSE:') if fsym.startswith('NFO:') else fsym
            fyers_symbols.append(fsym)
            f_to_k[fsym] = s

        batch_size = 50
        for i in range(0, len(fyers_symbols), batch_size):
            batch = fyers_symbols[i:i+batch_size]
            batch_str = ",".join(batch)
            
            for attempt in range(max_retries):
                _rate_limiter.wait(priority=priority)
                
                resp = None
                with _GLOBAL_FYERS_QUOTE_LOCK:
                    try:
                        resp = self.fyers.quotes(data={"symbols": batch_str})
                    except Exception as e:
                        if "json" in str(e).lower() or "expecting value" in str(e).lower():
                            resp = {'s': 'error', 'code': 429, 'message': 'Malformed JSON'}
                        else:
                            raise e
                
                if resp and resp.get('s') == 'ok':
                    d_list = resp.get('d', [])
                    oi_caches = {}
                    for item in d_list:
                        fsym = item.get('n', '')
                        if fsym.endswith('CE') or fsym.endswith('PE'):
                            m = re.match(r'^([A-Z&]+)', fsym.split(':')[-1])
                            if m and m.group(1) not in oi_caches:
                                oi_caches[m.group(1)] = self._get_fyers_oi(m.group(1))
                    
                    batch_quotes = {}
                    save_now = datetime.now()
                    for item in d_list:
                        fsym = item.get('n', '')
                        sym = f_to_k.get(fsym, fsym)
                        v = item.get('v', {})
                        if not v: continue
                        
                        op_oi = 0; op_oich = 0
                        if fsym.endswith('CE') or fsym.endswith('PE'):
                            m = re.match(r'^([A-Z&]+)', fsym.split(':')[-1])
                            if m and m.group(1) in oi_caches and fsym in oi_caches[m.group(1)]:
                                op_oi = oi_caches[m.group(1)][fsym].get('oi', 0)
                                op_oich = oi_caches[m.group(1)][fsym].get('oich', 0)
                        
                        q = {
                            'last_price': v.get('lp', 0.0),
                            'ohlc': {'open': v.get('open_price', 0.0), 'high': v.get('high_price', 0.0), 'low': v.get('low_price', 0.0), 'close': v.get('prev_close_price', 0.0)},
                            'oi': op_oi or v.get('oi', 0),
                            'change_in_oi': op_oich,
                            'timestamp': datetime.now()
                        }
                        batch_quotes[sym] = q
                        kite_quotes[sym] = q
                    
                    with _FYERS_SYMBOL_LOCK:
                        for s, q in batch_quotes.items():
                            _FYERS_SYMBOL_CACHE[s] = (q, save_now)
                    break
                
                if resp and resp.get('code') == 429:
                    time.sleep(backoff)
                    backoff *= 2
                elif resp and 'malformed' in str(resp.get('message', '')).lower():
                    time.sleep(8.0)
                else:
                    break
            
            time.sleep(1.0)

        return kite_quotes
    def ltp(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        quotes = self.quote(symbols, priority=1)
        return {k: {'last_price': v['last_price']} for k, v in quotes.items()}

    def historical_data(
        self,
        instrument_token: Union[int, str],
        from_date: str,
        to_date: str,
        interval: str,
        oi: bool = False,
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        try:
            _kite_to_fyers = {
                '30second':  '30S',
                'minute':    '1',
                '2minute':   '2',
                '3minute':   '3',
                '5minute':   '5',
                '10minute':  '10',
                '15minute':  '15',
                '30minute':  '30',
                '60minute':  '60',
                '2hour':    '120',
                '4hour':    '240',
                'day':       'D',
                'week':      'W',
                'month':     'M',
            }
            f_res = _kite_to_fyers.get(interval, interval.replace('minute', '') if 'minute' in interval else 'D')

            from datetime import timedelta
            from datetime import date as dt_date
            if isinstance(from_date, (datetime, dt_date)):
                fd = from_date
                from_date_str = fd.strftime('%Y-%m-%d')
            else:
                from_date_str = str(from_date)[:10]
                try:
                    fd = datetime.strptime(from_date_str, '%Y-%m-%d')
                except ValueError:
                    fd = None

            if isinstance(to_date, (datetime, dt_date)):
                td = to_date
                to_date_str = td.strftime('%Y-%m-%d')
            else:
                to_date_str = str(to_date)[:10]
                try:
                    td = datetime.strptime(to_date_str, '%Y-%m-%d')
                except ValueError:
                    td = None
                
            cache_key = f"{instrument_token}:{from_date_str}:{to_date_str}:{f_res}"
            if use_cache:
                with _FYERS_HIST_LOCK:
                    if cache_key in _FYERS_HIST_CACHE:
                        data, ts = _FYERS_HIST_CACHE[cache_key]
                        cache_ttl = 300 if f_res in ['D', 'W', 'M'] else 0.5
                        if (datetime.now() - ts).total_seconds() < cache_ttl:
                            logger.debug(f"[FyersAdapter] Returning cached historical data for {cache_key}")
                            return data

            max_days = 364 if f_res in ['D', '1D', 'W', 'M'] else 99
            
            chunks = []
            if fd and td and (td - fd).days > max_days:
                cur_from = fd
                while cur_from <= td:
                    cur_to = min(cur_from + timedelta(days=max_days), td)
                    chunks.append((cur_from.strftime('%Y-%m-%d'), cur_to.strftime('%Y-%m-%d')))
                    cur_from = cur_to + timedelta(days=1)
            else:
                chunks = [(from_date_str, to_date_str)]

            kite_to_fyers_indices = {
                '256265': 'NSE:NIFTY50-INDEX',
                '260105': 'NSE:NIFTYBANK-INDEX',
                '257801': 'NSE:FINNIFTY-INDEX',
                '288009': 'NSE:MIDCPNIFTY-INDEX',
                '264969': 'NSE:INDIAVIX-INDEX',
                'NSE:NIFTY 50': 'NSE:NIFTY50-INDEX',
                'NSE:NIFTY BANK': 'NSE:NIFTYBANK-INDEX',
                'NSE:NIFTY FIN SERVICE': 'NSE:FINNIFTY-INDEX',
                'NSE:NIFTY MID SELECT': 'NSE:MIDCPNIFTY-INDEX',
            }
            str_token = str(instrument_token)
            if str_token in kite_to_fyers_indices:
                instrument_token = kite_to_fyers_indices[str_token]
                logger.debug(f"[FyersAdapter] Translated Kite token {str_token} to {instrument_token}")

            all_candles = []
            import random
            for c_from, c_to in chunks:
                _rate_limiter.wait()
                
                response = {}
                logger.info(f"[FyersAdapter] Fetching history for {instrument_token} ({c_from} to {c_to}, resolution={f_res})")

                for attempt in range(5):
                    _rate_limiter.wait(priority=1)
                    
                    response = None
                    with _GLOBAL_FYERS_HIST_LOCK:
                        try:
                            if attempt > 0:
                                time.sleep(0.2)
                            
                            response = self.fyers.history(data={
                                "symbol":     str(instrument_token),
                                "resolution": f_res,
                                "date_format": "1",
                                "range_from": c_from,
                                "range_to":   c_to,
                                "cont_flag":  "1",
                            })
                        except Exception as e:
                            if "json" in str(e).lower() or "expecting value" in str(e).lower():
                                response = {'s': 'error', 'code': 429, 'message': 'Malformed JSON'}
                            else:
                                raise e

                    if response.get('s') == 'ok':
                        break
                    
                    err_msg = str(response.get('message', '')).lower()
                    if 'limit' in err_msg or response.get('code') in (429, -99):
                        retry_wait = 1.0 * (attempt + 1) + random.uniform(0.5, 1.5)
                        logger.warning(f"[FyersAdapter] Rate limit hit for {instrument_token}. Retrying in {retry_wait:.2f}s...")
                        time.sleep(retry_wait)
                    elif 'malformed' in err_msg:
                        logger.warning(f"[FyersAdapter] Malformed history response. Sleeping 8s...")
                        time.sleep(8.0)
                    else:
                        break

                if response.get('s') != 'ok':
                    logger.error(f"[FyersAdapter] history() failed for {instrument_token} [{c_from}-{c_to}]: {response.get('message')}")
                    continue

                for candle in response.get('candles', []):
                    from datetime import timezone, timedelta
                    IST = timezone(timedelta(hours=5, minutes=30))
                    
                    entry: Dict[str, Any] = {
                        'date':   datetime.fromtimestamp(candle[0], tz=IST),
                        'open':   candle[1],
                        'high':   candle[2],
                        'low':    candle[3],
                        'close':  candle[4],
                        'volume': candle[5],
                    }
                    if oi:
                        entry['oi'] = candle[6] if len(candle) > 6 else 0
                    all_candles.append(entry)
                
                logger.debug(f"[FyersAdapter] Successfully parsed {len(response.get('candles', []))} candles for {instrument_token}")
            
            # Save to cache
            if all_candles:
                with _FYERS_HIST_LOCK:
                    _FYERS_HIST_CACHE[cache_key] = (all_candles, datetime.now())
                    # Limit cache size
                    if len(_FYERS_HIST_CACHE) > 500:
                        _FYERS_HIST_CACHE.pop(next(iter(_FYERS_HIST_CACHE)))
                    
            return all_candles

        except Exception as e:
            logger.error(f"[FyersAdapter] historical_data() error for {instrument_token}: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Instruments (Symbol Master)
    # ──────────────────────────────────────────────────────────────────────

    def instruments(self, exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch and parse the Fyers Symbol Master CSV.
        """
        import csv
        import io
        now = datetime.now()
        
        with _csv_fetch_lock:
            # Recheck: Verify cache wasn't silently constructed by a preceding thread while bottlenecked in queue
            if (exchange in _GLOBAL_INSTRUMENTS_CACHE
                    and exchange in _GLOBAL_INSTRUMENTS_TIMESTAMP
                    and (datetime.now() - _GLOBAL_INSTRUMENTS_TIMESTAMP[exchange]).total_seconds() < 3600):
                logger.debug(f"[FyersAdapter] Returning {exchange} instruments from global cache")
                return _GLOBAL_INSTRUMENTS_CACHE[exchange]

            URLS = {
                'NSE': "https://public.fyers.in/sym_details/NSE_CM.csv",
                'NFO': "https://public.fyers.in/sym_details/NSE_FO.csv",
                'BSE': "https://public.fyers.in/sym_details/BSE_CM.csv",
                'BFO': "https://public.fyers.in/sym_details/BSE_FO.csv",
            }
            all_inst: List[Dict[str, Any]] = []
            target_urls = [(exchange, URLS[exchange])] if exchange in URLS else []
            for exch_key, url in target_urls:
                try:
                    logger.info(f"[FyersAdapter][CSV] Downloading {url}...")
                    resp = requests.get(url, timeout=30, stream=True)
                    if resp.status_code != 200:
                        continue
                    
                    # Use streaming reader to avoid massive string splits in memory
                    lines = (line.decode('utf-8') for line in resp.iter_lines())
                    reader = csv.reader(lines)
                    
                    count = 0
                    for parts in reader:
                        if len(parts) < 17:
                            continue
                        
                        raw_sym = parts[9].strip()
                        if not raw_sym:
                            continue
                        
                        # Pre-filter for performance: Only keep relevant instruments (Type 10-15 cover EQ/FUT/OPT)
                        inst_type_code = parts[2].strip()
                        if exch_key in ('NFO', 'BFO') and inst_type_code not in ('10', '11', '12', '13', '14', '15'):
                            continue

                        fyers_symbol = parts[9].strip()
                        if not fyers_symbol:
                            continue
                        
                        # Map Fyers columns to Kite-like dictionary for OpenInterestService compatibility
                        expiry_dt = None
                        try:
                            expiry_ts = int(float(parts[8].strip()))
                            expiry_dt = datetime.fromtimestamp(expiry_ts).date()
                        except:
                            pass

                        # Determine Kite-compatible instrument type
                        fyers_type = parts[16].strip()
                        kite_type = fyers_type
                        if fyers_type == 'XX':
                            if exch_key in ('NSE', 'BSE'):
                                kite_type = 'EQ'
                            else:
                                kite_type = 'FUT'

                        all_inst.append({
                            'instrument_token': fyers_symbol, # e.g. NSE:NIFTY26MAY24000CE
                            'tradingsymbol':    fyers_symbol.split(':')[-1], # e.g. NIFTY26MAY24000CE
                            'name':             parts[13].strip(), # e.g. NIFTY
                            'instrument_type':  kite_type, # Map XX to EQ/FUT
                            'strike':           float(parts[15].strip()) if parts[15].strip() else 0.0,
                            'expiry':           expiry_dt,
                            'lot_size':         int(float(parts[3].strip())) if parts[3].strip() else 1,
                        })
                        count += 1
                        
                    logger.info(f"[FyersAdapter][CSV] Parsed {count} instruments from {exch_key}")
                except Exception as e:
                    logger.warning(f"[FyersAdapter][CSV] Failed to fetch/parse {url}: {e}")

            if all_inst:
                _GLOBAL_INSTRUMENTS_CACHE[exchange] = all_inst
                _GLOBAL_INSTRUMENTS_TIMESTAMP[exchange] = datetime.now()
            
            return all_inst


    def find_option_symbol(self, root: str, strike: float, option_type: str) -> Optional[str]:
        """
        Find the Fyers instrument_token (symbol string) for a given option.
        Searches the high-speed memory cache first, then the CSV master.
        """
        root_upper = root.strip().upper()
        opt_upper = option_type.strip().upper()
        cache_key = f"{root_upper}:{int(strike)}:{opt_upper}"

        # 1. Try high-speed token cache (populated by OpenInterestService)
        from trading_app.service.fyers_data_service import _FYERS_OPTION_TOKEN_CACHE, _FYERS_OPTION_TOKEN_LOCK
        with _FYERS_OPTION_TOKEN_LOCK:
            if cache_key in _FYERS_OPTION_TOKEN_CACHE:
                sym = _FYERS_OPTION_TOKEN_CACHE[cache_key]
                logger.debug(f"[FyersAdapter] Resolved option from FAST CACHE: {cache_key} -> {sym}")
                return sym

        # 2. Fallback to CSV Master
        from datetime import date as _date
        instruments = self.instruments('NFO')  # Uses 1-hour cache
        today = _date.today()
        root_upper = root.strip().upper()
        opt_upper = option_type.strip().upper()

        matches = []
        for inst in instruments:
            inst_name = (inst.get('name', '') or '').strip().upper()
            inst_ts   = (inst.get('tradingsymbol', '') or '').strip().upper()
            inst_type = (inst.get('instrument_type', '') or '').strip().upper()
            inst_exp  = inst.get('expiry')
            inst_strk = inst.get('strike', -1)

            if inst_type != opt_upper:
                continue
            if inst_exp is None or inst_exp < today:
                continue
            if abs(inst_strk - strike) >= 0.5:
                continue

            # Match by name OR tradingsymbol prefix (handles Fyers CSV column shifts)
            name_ok = (inst_name == root_upper)
            ts_ok   = (inst_ts.startswith(root_upper) and
                       len(inst_ts) > len(root_upper) and
                       not inst_ts[len(root_upper)].isalpha())
            if name_ok or ts_ok:
                matches.append(inst)

        if not matches:
            logger.warning(f"[FyersAdapter] No option found for {root} {strike}{opt_upper}")
            return None

        # Return the nearest-expiry match
        matches.sort(key=lambda x: x['expiry'])
        sym = matches[0]['instrument_token']
        logger.info(f"[FyersAdapter] Resolved option: {root} {strike} {opt_upper} -> {sym} (expiry={matches[0]['expiry']})")
        return sym

    def get_lot_size(self, symbol: str) -> int:
        """
        Get the lot size for a symbol (Underlying or Trading Symbol).
        """
        # 1. Check NFO and BFO caches
        nfo = self.instruments('NFO')
        bfo = self.instruments('BFO')
        all_derivs = nfo + bfo
        
        symbol_upper = symbol.upper()
        # Common mapping for Sensex
        search_names = [symbol_upper]
        if symbol_upper == 'SENSEX':
            search_names.append('BSESENSEX')
        elif symbol_upper == 'BSESENSEX':
            search_names.append('SENSEX')
            
        # Check if it matches an underlying name (e.g. NIFTY)
        for inst in all_derivs:
            if inst.get('name') in search_names:
                return int(inst.get('lot_size', 1))
        
        # Check if it matches a trading symbol (e.g. NIFTY26MAYFUT)
        for inst in all_derivs:
            if inst.get('tradingsymbol') == symbol_upper or inst.get('instrument_token') == symbol_upper:
                return int(inst.get('lot_size', 1))
        
        return 1
                
        # 2. Check NSE cache
        nse = self.instruments('NSE')
        for inst in nse:
            if inst.get('tradingsymbol') == symbol_upper or inst.get('name') == symbol_upper:
                return int(inst.get('lot_size', 1))
                
        return 1

    def clear_instruments_cache(self):
        """Clear the global instrument caches to force re-download."""
        global _GLOBAL_INSTRUMENTS_CACHE, _GLOBAL_INSTRUMENTS_TIMESTAMP
        with _csv_fetch_lock:
            _GLOBAL_INSTRUMENTS_CACHE.clear()
            _GLOBAL_INSTRUMENTS_TIMESTAMP.clear()
        logger.info("[FyersAdapter] Global instrument cache cleared")
