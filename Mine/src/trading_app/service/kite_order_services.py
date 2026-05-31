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
_global_instruments_cache: Dict[str, Any] = {
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

def _is_socks5_healthy(host: str, port: int, timeout: float = 2.0) -> bool:
    """Verify SOCKS5 tunnel is functional by completing the greeting handshake.

    A TCP-only check allows zombie SSH processes (port open, no forwarding) to
    pass. Sending the SOCKS5 method-selection request and expecting a valid
    server-choice response confirms the proxy is actually forwarding.
    """
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b'\x05\x01\x00')      # VER=5, NMETHODS=1, METHOD=NO_AUTH
            resp = s.recv(2)
            return len(resp) == 2 and resp[0] == 0x05 and resp[1] == 0x00
    except OSError:
        return False

def ensure_ssh_tunnel() -> bool:
    """Start the SOCKS5 SSH tunnel if KITE_PROXY_URL is set and port is not yet listening.

    Returns True if tunnel is up (either was already running or just started).
    """
    import subprocess, time
    proxy_url = os.getenv('KITE_PROXY_URL', '').strip()
    if not proxy_url:
        return True  # no proxy required

    try:
        from urllib.parse import urlparse
        port: int = urlparse(proxy_url).port or 1080
        host: str = urlparse(proxy_url).hostname or '127.0.0.1'
    except Exception:
        host, port = '127.0.0.1', 1080

    if _is_socks5_healthy(host, port):
        return True  # already running

    ssh_key = os.path.expanduser(os.getenv('SSH_TUNNEL_KEY', ''))
    ssh_host = os.getenv('SSH_TUNNEL_HOST', '')
    if not ssh_key or not ssh_host or not os.path.exists(ssh_key):
        logging.warning(f'[Proxy] SSH_TUNNEL_KEY or SSH_TUNNEL_HOST not set — cannot auto-start tunnel')
        return False

    # Kill whatever process is holding the port (by port, not by name).
    # pkill by name misses zombies; killing by port owner is reliable.
    try:
        _pids = subprocess.run(['lsof', '-t', f'-i:{port}'], capture_output=True, text=True).stdout.split()
        for _pid in _pids:
            subprocess.run(['kill', _pid.strip()], capture_output=True)
        if _pids:
            time.sleep(1)
    except Exception:
        pass

    # Pre-flight: verify Oracle SSH daemon is up by reading its banner.
    # Only hard-fail if TCP connects but sends NO banner (instance stopped/starting).
    # A timeout just means Oracle is slow — SSH itself has ConnectTimeout=10, so
    # we let it proceed and let the SSH process handle genuine unreachability.
    import socket as _sock
    _oracle_host = ssh_host.split('@')[-1]
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(8)
        s.connect((_oracle_host, 22))
        s.settimeout(8)
        banner = s.recv(64)
        s.close()
        if not banner:
            logging.error('[Proxy] Oracle SSH (port 22) accepts TCP but sends no banner — instance may be stopped. Check Oracle Cloud Console.')
            return False
        logging.info(f'[Proxy] Oracle SSH banner: {banner.decode(errors="replace").strip()}')
    except Exception as e:
        try: s.close()
        except Exception: pass
        logging.warning(f'[Proxy] Pre-flight could not verify Oracle SSH ({e}) — attempting SSH anyway...')

    cmd = [
        'ssh', '-i', ssh_key, '-N', f'-D127.0.0.1:{port}', ssh_host,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=6',
        '-o', 'TCPKeepAlive=yes',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=10',
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(15):  # wait up to 15 seconds
            time.sleep(1)
            if _is_socks5_healthy(host, port):
                logging.info(f'[Proxy] SSH tunnel started → {ssh_host} (port {port})')
                return True
            if proc.poll() is not None:  # process already exited
                stderr_out = (proc.stderr.read() if proc.stderr else b'').decode(errors='replace').strip()
                logging.error(f'[Proxy] SSH exited (code {proc.returncode}): {stderr_out}')
                return False
        if proc.stderr:
            proc.stderr.close()
        logging.warning(f'[Proxy] SSH running but port {port} not open after 15s')
        return False
    except Exception as e:
        logging.warning(f'[Proxy] Could not start SSH tunnel: {e}')
        return False

_tunnel_watchdog_started = False

# Cache tunnel status so apply_kite_proxy never blocks on a known-dead tunnel.
# Watchdog resets _tunnel_known_down=False when it succeeds, so recovery is automatic.
_tunnel_known_down: bool = False
_tunnel_status_lock = threading.Lock()

def start_tunnel_watchdog(interval: int = 10) -> None:
    """Start a background thread that keeps the SSH tunnel alive.

    Checks port 1080 every `interval` seconds. If the tunnel has dropped
    (Oracle Cloud kills idle SSH connections), it calls ensure_ssh_tunnel()
    to bring it back up automatically.
    """
    global _tunnel_watchdog_started
    if _tunnel_watchdog_started:
        return
    if not os.getenv('KITE_PROXY_URL', '').strip():
        return

    _tunnel_watchdog_started = True

    def _watch():
        global _tunnel_known_down
        while True:
            time.sleep(interval)
            try:
                from urllib.parse import urlparse
                _port: int = urlparse(os.getenv('KITE_PROXY_URL', '')).port or 1080
                _host: str = urlparse(os.getenv('KITE_PROXY_URL', '')).hostname or '127.0.0.1'
            except Exception:
                _host, _port = '127.0.0.1', 1080

            if not _is_socks5_healthy(_host, _port):
                logging.warning('[Proxy] Watchdog: tunnel dropped — restarting...')
                recovered = False
                for _attempt in range(3):
                    if ensure_ssh_tunnel():
                        recovered = True
                        break
                    if _attempt < 2:
                        logging.warning(f'[Proxy] Watchdog restart attempt {_attempt + 1}/3 failed, retrying in 3s...')
                        time.sleep(3)
                with _tunnel_status_lock:
                    _tunnel_known_down = not recovered
            else:
                with _tunnel_status_lock:
                    if _tunnel_known_down:
                        logging.info('[Proxy] Watchdog: tunnel is back up')
                    _tunnel_known_down = False

    t = threading.Thread(target=_watch, daemon=True, name='ssh-tunnel-watchdog')
    t.start()
    logging.info(f'[Proxy] Tunnel watchdog started (checks every {interval}s)')

def apply_kite_proxy(kite, raise_if_unreachable: bool = False) -> bool:
    """Apply authenticated HTTPS static IP proxy to a KiteConnect instance.

    Reads STATIC_IP_KEY, STATIC_IP_SECRET, STATIC_IP_HOST from env.
    Returns True if proxy was applied, False if env vars are not set.
    """
    if not isinstance(kite, KiteConnect):
        return False
    key = os.getenv("STATIC_IP_KEY", "").strip()
    secret = os.getenv("STATIC_IP_SECRET", "").strip()
    host = os.getenv("STATIC_IP_HOST", "").strip()
    if not all([key, secret, host]):
        return False
    proxy_url = f"https://{key}:{secret}@{host}"
    # KiteConnect._request passes proxies=self.proxies, NOT reqsession.proxies
    kite.proxies = {'http': proxy_url, 'https': proxy_url}
    logging.info(f"[KiteService] Static IP proxy active: {host}")
    return True

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
        if kite_instance is not None:
            apply_kite_proxy(self.kite)
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
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', f'{p_type}_instruments_v3.pkl')
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
        cache_file = os.path.join(os.path.dirname(__file__), '..', '.cache', f'{p_type}_instruments_v3.pkl')
        try:
            logging.info("[KiteService] Fetching instruments (NSE+NFO)...")
            nse_raw = self.kite.instruments('NSE') or []
            nfo_raw = self.kite.instruments('NFO') or []
            bse_raw = self.kite.instruments('BSE') or []
            bfo_raw = self.kite.instruments('BFO') or []
            logging.info(f"[KiteService] Fetched: NSE({len(nse_raw)}), NFO({len(nfo_raw)}), BSE({len(bse_raw)}), BFO({len(bfo_raw)})")
            
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
            
            # Process BSE Cash
            for i in bse_raw:
                s, n, t = i.get('tradingsymbol'), i.get('name'), i.get('instrument_token')
                if s and t: self._instrument_tokens_by_symbol[s] = t
                if n and t: self._instrument_tokens_by_name[n.lower()] = t

            # Process BSE Options (BFO)
            for i in bfo_raw:
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
            nse_raw = None; nfo_raw = None; bse_raw = None; bfo_raw = None
            
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
        if not self._instrument_tokens_by_symbol: self._load_instruments_from_cache_or_api()
        token = self._instrument_tokens_by_symbol.get(symbol)
        if token: return token
        index_map = {
            'NIFTY': 'NSE:NIFTY 50', 
            'BANKNIFTY': 'NSE:NIFTY BANK', 
            'FINNIFTY': 'NSE:NIFTY FIN SERVICE',
            'SENSEX': 'BSE:SENSEX',
            'NIFTY MIDCAP 150': 'NSE:NIFTY MIDCAP 150',
            'NIFTY AUTO':      'NSE:NIFTY AUTO',
            'NIFTY Smallcap 100': 'NSE:NIFTY SMLCAP 100',
            'NIFTY SMLCAP 100': 'NSE:NIFTY SMLCAP 100',
            'NIFTY FMCG':      'NSE:NIFTY FMCG',
            'NIFTY METAL':     'NSE:NIFTY METAL',
            'NIFTY PHARAMA':   'NSE:NIFTY PHARMA',
            'NIFTY PHARMA':    'NSE:NIFTY PHARMA',
            'NIFTY PSU BANK':  'NSE:NIFTY PSU BANK',
            'NIFTY IT':        'NSE:NIFTY IT',
        }
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
            quote: Dict[Any, Any] = self.kite.quote([token]) or {}
            token_key = token if token in quote else str(token)
            if token_key in quote:
                return float((quote[token_key] or {}).get('last_price', 0))
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
        """O(1) lookup for options of a symbol (NFO or BFO)."""
        name_lower = name.lower()
        res = self._nfo_by_name.get(name_lower, [])
        if not res and name_lower == 'sensex':
            res = self._nfo_by_name.get('bsesensex', [])
        return res

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
        logging.warning(f"[KiteService] Lot size for {symbol} not found in NFO cache. Falling back to 1.")
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
            exchange = self.kite.EXCHANGE_BFO if symbol.upper() == 'SENSEX' else self.kite.EXCHANGE_NFO

            try:
                # Use internal _safe_place_order to include market_protection parameter
                order_id = self._safe_place_order(
                    tradingsymbol=ts,
                    exchange=exchange,
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
                    prefix = 'BFO' if symbol.upper() == 'SENSEX' else 'NFO'
                    _ltp_raw = self.kite.ltp(f"{prefix}:{ts}")
                    ltp_res: Dict[str, Any] = _ltp_raw if isinstance(_ltp_raw, dict) else {}
                    if f"{prefix}:{ts}" in ltp_res:
                        current_price = (ltp_res.get(f"{prefix}:{ts}") or {}).get('last_price', 0)
                        
                        # Pad by 5% to practically act as a market order safely
                        if txn_const == self.kite.TRANSACTION_TYPE_BUY:
                            safe_price = round(current_price * 1.05, 1)  # Buy up to 5% higher
                        else:
                            safe_price = round(current_price * 0.95, 1)  # Sell down to 5% lower
                            
                        logging.info(f"[KiteService] Retrying as LIMIT. LTP: {current_price}, Safe Padded Price: {safe_price}")
                        order_id = self.kite.place_order(
                            tradingsymbol=ts,
                            transaction_type=txn_const,
                            quantity=quantity,
                            order_type=self.kite.ORDER_TYPE_LIMIT,
                            price=safe_price,
                            product=mapped_product,
                            variety=self.kite.VARIETY_REGULAR,
                            exchange=exchange
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
            # Determine exchange from tradingsymbol or fallback to NFO
            exchange = self.kite.EXCHANGE_BFO if tradingsymbol.startswith('SENSEX') else self.kite.EXCHANGE_NFO
            
            order_id = self._safe_place_order(
                tradingsymbol=tradingsymbol,
                exchange=exchange,
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

    @staticmethod
    def _is_socks_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in ('socks', 'connect timeout', 'connectionerror', 'proxies', 'proxyerror', 'connect timed out'))

    def _safe_place_order(self, variety: str, exchange: str, tradingsymbol: str, transaction_type: str, quantity: int, product: str, order_type: str, price: Optional[float] = None, trigger_price: Optional[float] = None, tag: Optional[str] = None, market_protection: Optional[int] = -1) -> Any:
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
        params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(2):
            try:
                result = self.kite._post("order.place", url_args={"variety": variety}, params=params)
                return str(result) if isinstance(result, bytes) else result
            except Exception as exc:
                if attempt == 0 and self._is_socks_error(exc):
                    logging.warning(f"[KiteService] SOCKS error on order attempt 1 — restarting tunnel and retrying: {exc}")
                    if ensure_ssh_tunnel():
                        apply_kite_proxy(self.kite)
                    time.sleep(1)
                    continue
                raise

    def _create_kite_instance(self) -> KiteConnect:
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        kite = KiteConnect(api_key=api_key, timeout=30)
        apply_kite_proxy(kite)
        if access_token: kite.set_access_token(access_token)
        return kite

    def _historical_with_retry(self, instrument_token: Union[int, str], from_date, to_date, interval: str, retries: int = 3) -> List[Dict[str, Any]]:
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