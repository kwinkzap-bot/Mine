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
# Partitioned by provider to avoid token contamination between Kite and Fyers
_global_instruments_cache = {
    'lock': threading.Lock()
    # Structure:
    # 'kite': { 'tokens_by_symbol': {}, 'cache_date': None, ... }
    # 'fyers': { ... }
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
    
    def _make_key(self, token: Union[int, str], from_date: str, to_date: str, interval: str) -> str:
        return f"{token}:{from_date}:{to_date}:{interval}"
    
    def get(self, token: Union[int, str], from_date: str, to_date: str, interval: str) -> Optional[Any]:
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
    
    def set(self, token: Union[int, str], from_date: str, to_date: str, interval: str, data: Any, ttl: Optional[int] = None) -> None:
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
        self._instrument_tokens_by_symbol: Dict[str, Union[int, str]] = {}
        self._instrument_tokens_by_name: Dict[str, Union[int, str]] = {}
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
        
        # Determine provider type for cache partitioning
        p_type = 'fyers' if 'Fyers' in self.kite.__class__.__name__ else 'kite'
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', f'{p_type}_instruments_v2.pkl')
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        
        with _global_instruments_cache['lock']:
            if p_type in _global_instruments_cache:
                p_cache = _global_instruments_cache[p_type]
                if p_cache.get('cache_date') == today:
                    self._instrument_tokens_by_symbol = p_cache['tokens_by_symbol']
                    self._instrument_tokens_by_name = p_cache['tokens_by_name']
                    self._nfo_by_name = p_cache['nfo_by_name']
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
                        _global_instruments_cache[p_type] = {
                            'tokens_by_symbol': self._instrument_tokens_by_symbol,
                            'tokens_by_name': self._instrument_tokens_by_name,
                            'nfo_by_name': self._nfo_by_name,
                            'cache_date': today
                        }
                    return
            except Exception as e: logging.warning(f"Disk cache load error: {e}")
        
        self._load_instruments(p_type)

    def _load_instruments(self, p_type: str = 'kite'):
        """Fetch and prune instruments for memory efficiency (~70% reduction)."""
        global _global_instruments_cache
        today = date.today()
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', f'{p_type}_instruments_v2.pkl')
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
                        'expiry': i.get('expiry'),
                        'lot_size': i.get('lot_size')
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
            disk_data = {'tokens_by_symbol': self._instrument_tokens_by_symbol, 'tokens_by_name': self._instrument_tokens_by_name, 'nfo_by_name': self._nfo_by_name}
            with open(cache_file, 'wb') as f: pickle.dump(disk_data, f)
            with _global_instruments_cache['lock']:
                _global_instruments_cache[p_type] = {
                    'tokens_by_symbol': self._instrument_tokens_by_symbol,
                    'tokens_by_name': self._instrument_tokens_by_name,
                    'nfo_by_name': self._nfo_by_name,
                    'cache_date': today
                }
            logging.info(f"[KiteService] Memory-efficient {p_type} cache built: {len(self._instrument_tokens_by_symbol)} items")
        except Exception as e: logging.error(f"Instrument fetch error: {e}")

    def get_instrument_token(self, symbol: str) -> Optional[Union[int, str]]:
        if not self._instrument_tokens_by_symbol: self._init_instruments()
        token = self._instrument_tokens_by_symbol.get(symbol)
        if token: return token
        index_map = {'NIFTY': 'NSE:NIFTY 50', 'BANKNIFTY': 'NSE:NIFTY BANK', 'FINNIFTY': 'NSE:NIFTY FIN SERVICE'}
        if symbol.upper() in index_map:
            mapped = index_map[symbol.upper()]
            token = self._instrument_tokens_by_symbol.get(mapped)
            if token: return token
            return self._instrument_tokens_by_name.get(symbol.lower())
        return None

    def get_current_ltp(self, symbol: str) -> Optional[float]:
        try:
            token = self.get_instrument_token(symbol)
            if not token: return None
            _global_rate_limiter.wait()
            quote = self.kite.quote([token])
            token_key = token if token in quote else str(token)
            if token_key in quote: return float(quote[token_key]['last_price'])
        except Exception as e:
            logging.warning(f"[KiteService] LTP fetch failed for {symbol}: {e}")
        return None

    def get_previous_trading_day_close(self, symbol: str, target_date: Optional[str] = None) -> Optional[float]:
        try:
            token = self.get_instrument_token(symbol)
            if not token: return None
            ref_date = datetime.strptime(target_date, '%Y-%m-%d') if target_date else datetime.now()
            from_date = ref_date - timedelta(days=10)
            data = self._historical_with_retry(token, from_date, ref_date, 'day')
            if not data: return None
            data.sort(key=lambda x: x['date'], reverse=True)
            ref_date_only = ref_date.date()
            for bar in data:
                bar_date = bar['date'].date() if isinstance(bar['date'], datetime) else bar['date']
                if bar_date < ref_date_only: return float(bar['close'])
        except Exception as e:
            logging.warning(f"[KiteService] PDC fetch failed for {symbol}: {e}")
        return None

    def get_nfo_instruments(self, name: str) -> List[Dict[str, Any]]:
        """O(1) lookup for options of a symbol."""
        return self._nfo_by_name.get(name.lower(), [])

    def get_option_symbol(self, name: str, strike: float, option_type: str) -> Optional[str]:
        # Fast lookup in indexed map
        options = self.get_nfo_instruments(name)
        if not options: return None
        
        today = date.today()
        valid_options = []
        
        # Filter for specific strike, type and ensure it hasn't expired
        for o in options:
            if o.get('strike') == strike and o.get('instrument_type') == option_type:
                expiry = o.get('expiry')
                if expiry:
                    # Handle both date and datetime objects for safety
                    if hasattr(expiry, 'date'):
                        expiry = expiry.date()
                    
                    if expiry >= today:
                        valid_options.append(o)
        
        if not valid_options:
            logging.warning(f"[KiteService] No valid non-expired options found for {name} {strike} {option_type}")
            return None
            
        # Sort by expiry to always return the nearest one (most common for intraday)
        valid_options.sort(key=lambda x: x['expiry'])
        
        ts = valid_options[0].get('tradingsymbol')
        logging.info(f"[KiteService] Resolved {name} {strike} {option_type} to nearest expiry ({valid_options[0]['expiry']}): {ts}")
        return ts

    def get_lot_size(self, symbol: str) -> int:
        """
        Get the lot size (quantity multiplier) for a symbol.
        """
        # 1. Try to look up in our NFO cache
        symbol_upper = symbol.upper()
        options = self.get_nfo_instruments(symbol_upper)
        if options:
            # All instruments for same underlying share the same lot size
            ls = options[0].get('lot_size')
            if ls: return int(ls)

        # 2. Final fallback (Avoid hardcoding specific indices as they change)
        logger.warning(f"[KiteService] Lot size for {symbol} not found in NFO cache. Falling back to 1.")
        return 1

    def get_nearest_option_expiry(self, symbol: str, strike: int, option_type: str) -> Optional[date]:
        """Get the expiry date of the nearest available option matching criteria."""
        options = self.get_nfo_instruments(symbol)
        if not options:
            return None
        valid_expiries = []
        today = date.today()
        for o in options:
            if o.get('strike') == strike and o.get('instrument_type') == option_type:
                exp = o.get('expiry')
                if exp:
                    if hasattr(exp, 'date'):
                        d = exp.date()
                    elif isinstance(exp, str):
                        try:
                            d = datetime.fromisoformat(exp.split('T')[0]).date()
                        except:
                            continue
                    else:
                        d = exp
                    if d >= today:
                        valid_expiries.append(d)
        if valid_expiries:
            return min(valid_expiries)
        return None

    def place_option_order(self, symbol: str, strike: int, option_type: str, transaction_type: str, quantity: int, product: str = 'NRML', tradingsymbol: Optional[str] = None, price: Optional[float] = None) -> Dict[str, Any]:
        try:
            ts = tradingsymbol or self.get_option_symbol(symbol, strike, option_type)
            if not ts:
                return {'success': False, 'error': f'Could not resolve option symbol for {symbol} {strike} {option_type}'}
            
            logging.info(f"[KiteService] Placing option order: {transaction_type} {ts} x {quantity} ({product})")
            # Map string transaction_type to Kite constants if necessary
            txn_const = transaction_type
            if transaction_type.upper() == 'BUY': txn_const = self.kite.TRANSACTION_TYPE_BUY
            elif transaction_type.upper() == 'SELL': txn_const = self.kite.TRANSACTION_TYPE_SELL
            
            mapped_product = self.kite.PRODUCT_NRML if product.upper() in ['NRML', 'CARRYFORWARD'] else self.kite.PRODUCT_MIS
            
            try:
                # Use internal _safe_place_order to include market_protection parameter
                order_id = self._safe_place_order(
                    tradingsymbol=ts,
                    exchange=self.kite.EXCHANGE_NFO,
                    transaction_type=txn_const,
                    quantity=quantity,
                    order_type=self.kite.ORDER_TYPE_LIMIT if price else self.kite.ORDER_TYPE_MARKET,
                    price=price,
                    product=mapped_product,
                    variety=self.kite.VARIETY_REGULAR,
                    market_protection=-1 if not price else None
                )
                return {'success': True, 'order_id': order_id, 'price': price or 0, 'response': {'order_id': order_id}}
            except Exception as e:
                err_str = str(e).lower()
                if "market protection" in err_str or "limit order" in err_str or "market orders are not allowed" in err_str:
                    logging.warning(f"[KiteService] Market order blocked for NFO. Falling back to padded LIMIT order. Error: {e}")
                    
                    # Fetch LTP to calculate padded limit price
                    ltp_res = self.kite.ltp(f"NFO:{ts}")
                    if f"NFO:{ts}" in ltp_res:
                        current_price = ltp_res[f"NFO:{ts}"]['last_price']
                        
                        # Pad by 5% to practically act as a market order safely
                        if txn_const == self.kite.TRANSACTION_TYPE_BUY:
                            safe_price = round(current_price * 1.05, 1)  # Buy up to 5% higher
                        else:
                            safe_price = round(current_price * 0.95, 1)  # Sell down to 5% lower
                            
                        logging.info(f"[KiteService] Retrying as LIMIT. LTP: {current_price}, Safe Padded Price: {safe_price}")
                        order_id = self.kite.place_order(
                            tradingsymbol=ts,
                            exchange=self.kite.EXCHANGE_NFO,
                            transaction_type=txn_const,
                            quantity=quantity,
                            order_type=self.kite.ORDER_TYPE_LIMIT,
                            price=safe_price,
                            product=mapped_product,
                            variety=self.kite.VARIETY_REGULAR
                        )
                        return {'success': True, 'order_id': order_id, 'price': safe_price, 'response': {'order_id': order_id, 'note': 'Executed as padded LIMIT due to NSE Market block'}}
                    else:
                        raise ValueError(f"Could not fetch LTP for fallback pricing on {ts}")
                else:
                    raise e
                    
        except Exception as e:
            logging.error(f"[KiteService] Failed to place option order: {e}")
            return {'success': False, 'error': str(e)}

    def place_stoploss_order(self, tradingsymbol: str, trigger_price: float, quantity: int, product: str = 'NRML', transaction_type: str = 'SELL') -> Dict[str, Any]:
        try:
            logging.info(f"[KiteService] Placing SL order: {transaction_type} {tradingsymbol} x {quantity} @ trigger {trigger_price} ({product})")
            mapped_product = self.kite.PRODUCT_NRML if product.upper() in ['NRML', 'CARRYFORWARD'] else self.kite.PRODUCT_MIS
            
            # Map string transaction_type to Kite constants
            txn_const = self.kite.TRANSACTION_TYPE_SELL
            if transaction_type.upper() == 'BUY': txn_const = self.kite.TRANSACTION_TYPE_BUY
            elif transaction_type.upper() == 'SELL': txn_const = self.kite.TRANSACTION_TYPE_SELL
            
            # Use internal _safe_place_order to include market_protection parameter
            order_id = self._safe_place_order(
                tradingsymbol=tradingsymbol,
                exchange=self.kite.EXCHANGE_NFO,
                transaction_type=txn_const,
                quantity=int(quantity),
                order_type=self.kite.ORDER_TYPE_SLM,
                trigger_price=float(trigger_price),
                product=mapped_product,
                variety=self.kite.VARIETY_REGULAR,
                market_protection=-1  # -1 enables automatic market protection
            )
            return {'success': True, 'order_id': order_id, 'response': {'order_id': order_id}}
        except Exception as e:
            logging.error(f"[KiteService] Failed to place SL order: {e}")
            return {'success': False, 'error': str(e)}

    def _safe_place_order(self, variety: str, exchange: str, tradingsymbol: str, transaction_type: str, quantity: int, product: str, order_type: str, price: Optional[float] = None, trigger_price: Optional[float] = None, tag: Optional[str] = None, market_protection: int = -1) -> str:
        """
        Internal helper to place orders with market_protection parameter.
        Bypasses the standard library's place_order if the version doesn't support the parameter.
        """
        params = {
            "variety": variety,
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "product": product,
            "order_type": order_type,
            "price": price,
            "trigger_price": trigger_price,
            "tag": tag,
            "market_protection": market_protection
        }
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        
        # Access the private _post method to inject market_protection
        # variety is passed in url_args for the /orders/{variety} route
        return self.kite._post("order.place", url_args={"variety": variety}, params=params)

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