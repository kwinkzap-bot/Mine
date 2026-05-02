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
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self.last_call = time.time()

_rate_limiter = FyersRateLimiter(10.0)
logger = logging.getLogger(__name__)

# Global Static Caches to survive transient Adapter regenerations from api.py routes
_GLOBAL_INSTRUMENTS_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_GLOBAL_INSTRUMENTS_TIMESTAMP: Dict[str, datetime] = {}
_csv_fetch_lock = threading.Lock()

_GLOBAL_OI_CHAIN_CACHE: Dict[str, Dict[str, Dict[str, float]]] = {}
_GLOBAL_OI_CHAIN_TIMESTAMP: Dict[str, datetime] = {}

# Historical Data Cache for Fyers
_FYERS_HIST_CACHE: Dict[str, tuple] = {}
_FYERS_HIST_LOCK = threading.Lock()

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
        try:
            logger.info(f"[FyersAdapter] Fetching option chain for {symbol} (root={root_name})")
            data = {'symbol': symbol, 'strikecount': 50}
            resp = self.fyers.optionchain(data=data)
            oi_map = {}
            if resp.get('s') == 'ok':
                options = resp.get('data', {}).get('optionsChain', [])
                for row in options:
                    sym = row.get('symbol')
                    if sym:
                        oi_map[sym] = {
                            'oi': row.get('oi', row.get('open_interest', 0)),
                            'oich': row.get('oich', 0)
                        }
            else:
                logger.error(f"[FyersAdapter] optionchain failed for {symbol}: {resp}")
                
            _GLOBAL_OI_CHAIN_CACHE[root_name] = oi_map
            _GLOBAL_OI_CHAIN_TIMESTAMP[root_name] = now
            return oi_map
        except Exception as e:
            logger.error(f"[FyersAdapter] _get_fyers_oi error for {symbol}: {e}")
            return {}

    def set_access_token(self, token: str):
        """Kite-compatibility dummy. Access token is set during init for Fyers."""
        pass

    def quote(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Fetch full quotes with retry and rate-limiting.
        Kite expects: { "NSE:NIFTY50-INDEX": { "last_price": ..., "ohlc": {...}, "oi": ... } }
        """
        max_retries = 3
        backoff = 1.0
        
        for attempt in range(max_retries):
            # Apply global rate limit protection
            _rate_limiter.wait()
            
            try:
                # Map naked EQ symbols back to Fyers EQ suffix
                # Map Kite symbols to Fyers symbols
                KITE_TO_FYERS_MAP = {
                    'NSE:NIFTY 50': 'NSE:NIFTY50-INDEX',
                    'NSE:NIFTY BANK': 'NSE:NIFTYBANK-INDEX',
                    'NSE:NIFTY FIN SERVICE': 'NSE:FINNIFTY-INDEX',
                    'NSE:NIFTY MID SELECT': 'NSE:MIDCPNIFTY-INDEX',
                    'NSE:INDIA VIX': 'NSE:INDIAVIX-INDEX',
                }
                
                fyers_symbols = []
                fyers_to_kite_map = {}
                for s in symbols:
                    fsym = KITE_TO_FYERS_MAP.get(s, s)
                    if fsym.startswith('NSE:') and not any(fsym.endswith(x) for x in ['-EQ', '-INDEX', 'CE', 'PE', 'FUT']):
                        fsym = f"{fsym}-EQ"
                    elif fsym.startswith('BSE:') and not any(fsym.endswith(x) for x in ['-EQ', '-INDEX']):
                        fsym = f"{fsym}-EQ"
                    else:
                        fsym = fsym.replace('NFO:', 'NSE:') if fsym.startswith('NFO:') else fsym
                    
                    fyers_symbols.append(fsym)
                    fyers_to_kite_map[fsym] = s

                formatted_symbols = ",".join(fyers_symbols)
                response = self.fyers.quotes(data={"symbols": formatted_symbols})

                if response.get('s') != 'ok':
                    code = response.get('code')
                    if code == 429 or 'limit reached' in str(response.get('message')).lower():
                        logger.warning(f"[FyersAdapter] Rate limit in quote() [Attempt {attempt+1}/{max_retries}]. Sleeping {backoff}s...")
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    logger.error(f"[FyersAdapter] quotes() failed: {response.get('message')} | code={code}")
                    return {}

                # Pre-fetch option chain OI for options since /quotes API omits OI
                oi_caches = {}
                d_list = response.get('d', [])
                for item in d_list:
                    fsym = item.get('n', '')
                    if fsym.endswith('CE') or fsym.endswith('PE'):
                        short_sym = fsym.split(':')[-1] if ':' in fsym else fsym
                        m = re.match(r'^([A-Z&]+)', short_sym)
                        if m:
                            root = m.group(1)
                            if root not in oi_caches:
                                oi_caches[root] = self._get_fyers_oi(root)
                
                kite_quotes: Dict[str, Any] = {}
                for item in d_list:
                    fsym = item.get('n', '')
                    sym = fyers_to_kite_map.get(fsym, fsym)
                    v = item.get('v', {})
                    
                    if not v: continue
                    
                    option_oi = 0
                    option_oich = 0
                    if fsym.endswith('CE') or fsym.endswith('PE'):
                        short_sym = fsym.split(':')[-1] if ':' in fsym else fsym
                        m = re.match(r'^([A-Z&]+)', short_sym)
                        if m:
                            root = m.group(1)
                            if root in oi_caches and fsym in oi_caches[root]:
                                option_oi = oi_caches[root][fsym].get('oi', 0)
                                option_oich = oi_caches[root][fsym].get('oich', 0)
                    
                    fetched_oi = v.get('open_interest') or v.get('oi') or 0
                    final_oi = option_oi if option_oi > 0 else fetched_oi
                    
                    kite_quotes[sym] = {
                        'last_price':    v.get('lp', 0.0),
                        'high':          v.get('high_price') or v.get('high') or 0.0,
                        'low':           v.get('low_price') or v.get('low') or 0.0,
                        'ohlc': {
                            'open':  v.get('open_price') or v.get('open') or 0.0,
                            'high':  v.get('high_price') or v.get('high') or 0.0,
                            'low':   v.get('low_price') or v.get('low') or 0.0,
                            'close': v.get('prev_close_price') or v.get('prev_close') or 0.0,
                        },
                        'oi':            final_oi,
                        'change_in_oi':  option_oich,
                        'oi_day_high':   v.get('oi_day_high', 0),
                        'oi_day_low':    v.get('oi_day_low', 0),
                        'volume':        v.get('volume') or v.get('vol') or 0,
                        'change':        v.get('ch', 0.0),
                        'change_percent':v.get('chp', 0.0),
                        'last_quantity': v.get('askQty', 0),
                        'timestamp':     datetime.fromtimestamp(int(v['tt'])) if v.get('tt') else datetime.now(),
                    }
                return kite_quotes
            except Exception as e:
                logger.error(f"[FyersAdapter] quote() error in core: {e}")
                if attempt == max_retries - 1: return {}
                time.sleep(backoff)
                backoff *= 2
        return {}
    def ltp(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch LTP only.
        Kite expects: { "NSE:NIFTY50-INDEX": { "last_price": 24000.0 } }
        """
        quotes = self.quote(symbols)
        return {k: {'last_price': v['last_price']} for k, v in quotes.items()}

    # ──────────────────────────────────────────────────────────────────────
    # Historical Data
    # ──────────────────────────────────────────────────────────────────────

    def historical_data(
        self,
        instrument_token: Union[int, str],
        from_date: str,
        to_date: str,
        interval: str,
        oi: bool = False,
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV candle data.

        instrument_token : For Fyers, pass the full symbol string
                           (e.g. "NSE:NIFTY50-INDEX" or "NSE:NIFTY26APR24450CE")
        interval         : Kite-style ('minute','5minute','day', etc.)
        Fyers resolution : '1', '5', '15', '30', '60', '120', '240', 'D', 'W', 'M'
        """
        try:
            # Kite → Fyers interval mapping
            _kite_to_fyers = {
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
            # Pre-process dates to handle both datetime objects and strings
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
                
            # Check cache first (symbol:from:to:res)
            # 5-minute TTL for historical data cache
            cache_key = f"{instrument_token}:{from_date_str}:{to_date_str}:{f_res}"
            if use_cache:
                with _FYERS_HIST_LOCK:
                    if cache_key in _FYERS_HIST_CACHE:
                        data, ts = _FYERS_HIST_CACHE[cache_key]
                        if (datetime.now() - ts).total_seconds() < 300:
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

            # Translate Kite tokens (integer or string) to Fyers symbol strings for Indices
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
                # Apply global rate limit protection
                _rate_limiter.wait()
                
                response = {}
                logger.info(f"[FyersAdapter] Fetching history for {instrument_token} ({c_from} to {c_to}, resolution={f_res})")

                # Boosted to 5 attempts against heavy chunk loads
                for attempt in range(5):
                    # For retry attempts, apply an extra wait
                    if attempt > 0:
                        _rate_limiter.wait()
                        time.sleep(attempt * 0.5)

                    logger.debug(f"[FyersAdapter] Making history call to Fyers for {instrument_token}...")
                    response = self.fyers.history(data={
                        "symbol":     str(instrument_token),
                        "resolution": f_res,
                        "date_format": "1",   # YYYY-MM-DD
                        "range_from": c_from,
                        "range_to":   c_to,
                        "cont_flag":  "1",
                    })

                    if response.get('s') == 'ok':
                        break
                    
                    err_msg = str(response.get('message', '')).lower()
                    logger.warning(f"[FyersAdapter] History call fail for {instrument_token}: {response}")
                    if 'limit' in err_msg or response.get('code') in (429, -99):
                        retry_wait = 1.0 * (attempt + 1) + random.uniform(0.5, 1.5)
                        logger.warning(f"[FyersAdapter] Rate limit hit for {instrument_token} [{c_from}-{c_to}]. Retrying in {retry_wait:.2f}s...")
                        time.sleep(retry_wait)
                    else:
                        break  # Break for non-rate-limit errors

                if response.get('s') != 'ok':
                    logger.error(f"[FyersAdapter] history() failed for {instrument_token} [{c_from}-{c_to}]: {response.get('message')}")
                    continue

                for candle in response.get('candles', []):
                    # Fyers returns Unix timestamp in UTC. Convert to IST (UTC+5:30)
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

        Fyers NSE_FO.csv — NO header row. Column layout (0-indexed):
          0   fyToken
          1   Full Name  (e.g. "TATA MOTORS 23 Nov 30 370 CE")
          2   Instrument Type  (OPTIDX / OPTSTK / FUTIDX / FUTSTK)
          3   Lot Size
          4   Tick Size
          5   Reserved
          6   Trading Session
          7   Last Update Date
          8   Expiry Date  ← UNIX timestamp (seconds)
          9   Symbol       (e.g. NSE:NIFTY26APR24450CE)  ← used as instrument_token
         10   Exchange     (NSE / BSE)
         11   Segment      (NSE_FO)
         12   Script Code
         13   Short Symbol
         14   Root/Underlying  (e.g. NIFTY, BANKNIFTY)  ← name field for filtering
         15   Strike Price
         16   Option Type  (CE / PE)
         17   Underlying fyToken
        """
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
                    resp = requests.get(url, timeout=20)
                    if resp.status_code != 200:
                        continue
                    lines = resp.text.strip().split('\n')
                    logger.info(f"[FyersAdapter][CSV] Parsing {len(lines)} rows from {url}")
                    for line in lines:
                        parts = line.split(',')
                        if len(parts) < 10:
                            continue
                        raw_sym = parts[9].strip()
                        if not raw_sym:
                            continue
                        
                        if ':' in raw_sym:
                            fyers_symbol = raw_sym
                            sym_short = raw_sym.split(':')[-1]
                        else:
                            exch_col = parts[10].strip() if len(parts) > 10 else 'NSE'
                            sym_short = raw_sym
                            fyers_symbol = f"{exch_col or 'NSE'}:{raw_sym}"
                        
                        short_sym_upper = sym_short.upper()
                        if exch_key in ('NSE', 'BSE'):
                            opt_type = 'EQ'
                        elif short_sym_upper.endswith('FUT'):
                            opt_type = 'FUT'
                        elif short_sym_upper.endswith('CE'):
                            opt_type = 'CE'
                        elif short_sym_upper.endswith('PE'):
                            opt_type = 'PE'
                        elif short_sym_upper.endswith('INDEX'):
                            opt_type = 'INDEX'
                        else:
                            continue

                        _m = re.match(r'^([A-Z&]+)', sym_short)
                        root = _m.group(1) if _m else sym_short
                        strike = 0.0
                        try:
                            strike = float(parts[15].strip()) if len(parts) > 15 else 0.0
                        except (ValueError, IndexError):
                            pass
                        
                        expiry_date = None
                        try:
                            expiry_date = datetime.fromtimestamp(int(parts[8].strip())).date()
                        except Exception:
                            pass
                            
                        all_inst.append({
                            'instrument_token': fyers_symbol,
                            'tradingsymbol':    sym_short,
                            'name':             root,
                            'instrument_type':  opt_type,
                            'exchange':         exch_key,
                            'strike':           strike,
                            'expiry':           expiry_date,
                            'lot_size':         0,
                        })
                except Exception as e:
                    logger.warning(f"[FyersAdapter][CSV] Failed to fetch/parse {url}: {e}")

            if all_inst:
                _GLOBAL_INSTRUMENTS_CACHE[exchange] = all_inst
                _GLOBAL_INSTRUMENTS_TIMESTAMP[exchange] = datetime.now()
            
            logger.info(f"[FyersAdapter] instruments() loaded {len(all_inst)} records for {exchange}")
            return all_inst

        logger.info(f"[FyersAdapter] instruments() via CSV fallback: {len(all_inst)} records loaded")
        if not all_inst:
            logger.warning(f"[FyersAdapter] Zero instruments parsed for {exchange}. Overriding memory cache assignment to provoke auto-retry.")
            return all_inst
            
        _GLOBAL_INSTRUMENTS_CACHE[exchange] = all_inst
        _GLOBAL_INSTRUMENTS_TIMESTAMP[exchange] = datetime.now()
            
        return all_inst


    def find_option_symbol(self, root: str, strike: float, option_type: str) -> Optional[str]:
        """
        Find the Fyers instrument_token (symbol string) for a given option.
        Searches the instruments cache for matching root, strike, and option type.

        Args:
            root:        Underlying name (e.g. 'NIFTY', 'BANKNIFTY')
            strike:      Strike price (float)
            option_type: 'CE' or 'PE'

        Returns:
            Fyers symbol string (e.g. 'NSE:NIFTY26APR2424200CE') or None
        """
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

