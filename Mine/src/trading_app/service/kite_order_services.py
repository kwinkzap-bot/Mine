import logging
from kiteconnect import KiteConnect
import pandas as pd
from datetime import datetime, timedelta, date
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional, Tuple, Union
import re
import threading
import time
import pickle
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc

load_dotenv()

# Global singleton cache for instruments (shared across all KiteService instances)
# Optimized to store pruned structures and indexed groups instead of full lists
_global_instruments_cache = {
    'tokens_by_symbol': {},
    'tokens_by_name': {},
    'nfo_by_name': {},  # Mapping: symbol_name -> list of pruned instrument dicts
    'cache_date': None,
    'lock': threading.Lock()
}

# ── RATE LIMITING ────────────────────────────────────────────────────────
class GlobalRateLimiter:
    """Thread-safe global rate limiter for Kite API (3 requests/second)."""
    def __init__(self, requests_per_second: float = 3.0):
        self.delay = 1.0 / requests_per_second
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self.last_call = time.time()

_global_rate_limiter = GlobalRateLimiter(3.0)

def get_global_rate_limiter():
    return _global_rate_limiter

# ── HISTORICAL CACHE ─────────────────────────────────────────────────────
class HistoricalDataCache:
    """Thread-safe LRU cache for historical data with TTL."""
    def __init__(self, max_size: int = 500, default_ttl: int = 300):
        self._cache: OrderedDict[str, Tuple[Any, float, int]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
    
    def _make_key(self, token: int, from_date: str, to_date: str, interval: str) -> str:
        return f"{token}:{from_date}:{to_date}:{interval}"
    
    def get(self, token: int, from_date: str, to_date: str, interval: str) -> Optional[Any]:
        key = self._make_key(token, from_date, to_date, interval)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            data, timestamp, ttl = self._cache[key]
            if time.time() - timestamp > ttl:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return data.copy() if isinstance(data, pd.DataFrame) else data
    
    def set(self, token: int, from_date: str, to_date: str, interval: str, data: Any, ttl: Optional[int] = None) -> None:
        key = self._make_key(token, from_date, to_date, interval)
        ttl = ttl or self._default_ttl
        with self._lock:
            if key in self._cache: del self._cache[key]
            while len(self._cache) >= self._max_size: self._cache.popitem(last=False)
            self._cache[key] = (data, time.time(), ttl)

_historical_cache = HistoricalDataCache(max_size=500, default_ttl=300)

# ── KITE SERVICE ─────────────────────────────────────────────────────────
class KiteService:
    """Optimized KiteConnect service with memory-efficient instrument caching."""
    
    def __init__(self, kite_instance: Optional[KiteConnect] = None):
        self.kite: KiteConnect = kite_instance or self._create_kite_instance()
        self._instrument_tokens_by_symbol: Dict[str, int] = {}
        self._instrument_tokens_by_name: Dict[str, int] = {}
        self._nfo_by_name: Dict[str, List[Dict[str, Any]]] = {}
        self._nfo_cache_asof: Optional[date] = None
        self._nfo_option_symbol_cache: Dict[str, str] = {}
        self._quote_cache: Dict[str, tuple] = {}
        self._quote_cache_ttl = 5
        self._quote_lock = threading.Lock()
        
        self._load_instruments_from_cache_or_api()

    def _load_instruments_from_cache_or_api(self):
        global _global_instruments_cache
        today = date.today()
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', 'kite_instruments_v2.pkl')
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        
        with _global_instruments_cache['lock']:
            if _global_instruments_cache['cache_date'] == today:
                self._instrument_tokens_by_symbol = _global_instruments_cache['tokens_by_symbol']
                self._instrument_tokens_by_name = _global_instruments_cache['tokens_by_name']
                self._nfo_by_name = _global_instruments_cache['nfo_by_name']
                self._nfo_cache_asof = today
                return
        
        if os.path.exists(cache_file):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(cache_file)).date()
                if mtime == today:
                    with open(cache_file, 'rb') as f:
                        disk_cache = pickle.load(f)
                    self._instrument_tokens_by_symbol = disk_cache['tokens_by_symbol']
                    self._instrument_tokens_by_name = disk_cache['tokens_by_name']
                    self._nfo_by_name = disk_cache.get('nfo_by_name', {})
                    self._nfo_cache_asof = today
                    with _global_instruments_cache['lock']:
                        _global_instruments_cache['tokens_by_symbol'] = self._instrument_tokens_by_symbol
                        _global_instruments_cache['tokens_by_name'] = self._instrument_tokens_by_name
                        _global_instruments_cache['nfo_by_name'] = self._nfo_by_name
                        _global_instruments_cache['cache_date'] = today
                    return
            except Exception as e: logging.warning(f"Disk cache load error: {e}")
        
        self._load_instruments()

    def _load_instruments(self):
        """Fetch and prune instruments for memory efficiency (~70% reduction)."""
        global _global_instruments_cache
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', 'kite_instruments_v2.pkl')
        try:
            logging.info("[KiteService] Fetching instruments (NSE+NFO)...")
            nse_raw = self.kite.instruments('NSE') or []
            nfo_raw = self.kite.instruments('NFO') or []
            
            self._instrument_tokens_by_symbol = {}
            self._instrument_tokens_by_name = {}
            self._nfo_by_name = {}

            # Pruning to save memory
            for i in nse_raw:
                s, n, t = i.get('tradingsymbol'), i.get('name'), i.get('instrument_token')
                if s and t: self._instrument_tokens_by_symbol[s] = t
                if n and t: self._instrument_tokens_by_name[n.lower()] = t

            for i in nfo_raw:
                s, n, t = i.get('tradingsymbol'), i.get('name'), i.get('instrument_token')
                if s and t: self._instrument_tokens_by_symbol[s] = t
                if n:
                    nl = n.lower()
                    if nl not in self._nfo_by_name: self._nfo_by_name[nl] = []
                    self._nfo_by_name[nl].append({
                        'tradingsymbol': s, 'name': n, 'instrument_token': t,
                        'strike': i.get('strike'), 'instrument_type': i.get('instrument_type'),
                        'expiry': i.get('expiry')
                    })
            
            # CRITICAL: Free up raw memory immediately
            nse_raw = None
            nfo_raw = None
            gc.collect()
            
            self._nfo_cache_asof = date.today()
            disk_data = {
                'tokens_by_symbol': self._instrument_tokens_by_symbol,
                'tokens_by_name': self._instrument_tokens_by_name,
                'nfo_by_name': self._nfo_by_name
            }
            with open(cache_file, 'wb') as f: pickle.dump(disk_data, f)
            with _global_instruments_cache['lock']:
                _global_instruments_cache['tokens_by_symbol'] = self._instrument_tokens_by_symbol
                _global_instruments_cache['tokens_by_name'] = self._instrument_tokens_by_name
                _global_instruments_cache['nfo_by_name'] = self._nfo_by_name
                _global_instruments_cache['cache_date'] = self._nfo_cache_asof
            logging.info(f"[KiteService] Memory-efficient cache built: {len(self._instrument_tokens_by_symbol)} items")
        except Exception as e: logging.error(f"Instrument fetch error: {e}")

    def get_instrument_token(self, symbol: str) -> Optional[int]:
        return self._instrument_tokens_by_symbol.get(symbol)

    def get_nfo_instruments(self, name: str) -> List[Dict[str, Any]]:
        """O(1) lookup for options of a symbol."""
        return self._nfo_by_name.get(name.lower(), [])

    def get_option_symbol(self, name: str, strike: float, option_type: str) -> Optional[str]:
        # Fast lookup in indexed map
        options = self.get_nfo_instruments(name)
        if not options: return None
        # Filter for specific strike and type
        for o in options:
            if o.get('strike') == strike and o.get('instrument_type') == option_type:
                return o.get('tradingsymbol')
        return None

    def _create_kite_instance(self) -> KiteConnect:
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        kite = KiteConnect(api_key=api_key)
        kite.timeout = 30
        if access_token: kite.set_access_token(access_token)
        return kite

    def _historical_with_retry(self, instrument_token: int, from_date, to_date, interval: str, retries: int = 3) -> List[Dict[str, Any]]:
        # Apply rate limiting
        _global_rate_limiter.wait()
        
        # Check cache first
        cached = _historical_cache.get(instrument_token, str(from_date), str(to_date), interval)
        if cached is not None: return cached
        
        for attempt in range(retries):
            try:
                data = self.kite.historical_data(instrument_token, from_date, to_date, interval)
                _historical_cache.set(instrument_token, str(from_date), str(to_date), interval, data)
                return data
            except Exception as e:
                if attempt == retries - 1: raise e
                time.sleep(1)
        return []

def clear_global_instruments_cache():
    global _global_instruments_cache
    with _global_instruments_cache['lock']:
        _global_instruments_cache['cache_date'] = None
        _global_instruments_cache['tokens_by_symbol'] = {}
        _global_instruments_cache['tokens_by_name'] = {}
        _global_instruments_cache['nfo_by_name'] = {}