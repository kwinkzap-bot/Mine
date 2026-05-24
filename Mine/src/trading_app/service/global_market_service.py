import requests
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class GlobalMarketService:
    """Service to fetch global stock / indices data concurrently from Yahoo Finance."""
    
    _cache = {}
    _cache_lock = threading.Lock()
    _CACHE_TTL = 10  # 10 seconds cache TTL for ultra real-time live data
    _session = requests.Session()
    
    # Global indices to display
    INDEX_MAP = {
        "^DJI": {"name": "Dow", "id": "dji"},
        "YM=F": {"name": "Dow Futures", "id": "dji_fut"},
        "^GSPC": {"name": "S&P", "id": "gspc"},
        "^N225": {"name": "NIKKEI", "id": "n225"},
        "^HSI": {"name": "HANG SENG", "id": "hsi"},
        "^GDAXI": {"name": "DAX", "id": "daxi"},
        "^FCHI": {"name": "CAC", "id": "fchi"},
        "^KS11": {"name": "KOSPI", "id": "ks11"},
        "^FTSE": {"name": "FTSE 100", "id": "ftse"},
        "^NSEI": {"name": "Gift Nifty", "id": "gift_nifty"},
    }

    @classmethod
    def get_latest_data(cls) -> List[Dict[str, Any]]:
        """Get the latest global index quotes, using cache if available."""
        now = datetime.now().timestamp()
        with cls._cache_lock:
            if 'data' in cls._cache:
                ts = cls._cache.get('timestamp', 0)
                if now - ts < cls._CACHE_TTL:
                    return cls._cache['data']
        
        # Fetch fresh data concurrently
        data = cls._fetch_all_indices()
        
        with cls._cache_lock:
            cls._cache['data'] = data
            cls._cache['timestamp'] = now
            
        return data

    @classmethod
    def _fetch_single_index(cls, symbol: str, meta: Dict[str, str]) -> Dict[str, Any]:
        """Fetch a single index from Yahoo Finance chart endpoint with live cache-busting."""
        import time
        # Dynamic cache-buster query parameter to bypass Yahoo CDN edge cache
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?t={int(time.time())}"
        
        # Force fresh response through proxy/CDN headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
        
        default_item = {
            "id": meta["id"],
            "symbol": symbol,
            "name": meta["name"],
            "price": 0.0,
            "change": 0.0,
            "pChange": 0.0,
            "success": False
        }
        
        try:
            resp = cls._session.get(url, headers=headers, timeout=5)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch {symbol}: Status {resp.status_code}")
                return default_item
                
            res_json = resp.json()
            results = res_json.get("chart", {}).get("result", [])
            if not results:
                logger.warning(f"No chart results in Yahoo Finance response for {symbol}")
                return default_item
                
            meta_data = results[0].get("meta", {})
            price = meta_data.get("regularMarketPrice")
            prev_close = meta_data.get("previousClose")
            
            if price is None or prev_close is None:
                # Try from indicators if meta is incomplete
                indicators = results[0].get("indicators", {}).get("quote", [{}])[0]
                close_prices = [p for p in indicators.get("close", []) if p is not None]
                if close_prices:
                    price = close_prices[-1]
                if meta_data.get("chartPreviousClose") is not None:
                    prev_close = meta_data.get("chartPreviousClose")
                    
            if price is not None and prev_close is not None:
                change = price - prev_close
                pchange = (change / prev_close) * 100 if prev_close else 0.0
                return {
                    "id": meta["id"],
                    "symbol": symbol,
                    "name": meta["name"],
                    "price": price,
                    "change": change,
                    "pChange": pchange,
                    "success": True
                }
            
            logger.warning(f"Incomplete pricing data for {symbol}: price={price}, prev_close={prev_close}")
            return default_item
            
        except Exception as e:
            logger.error(f"Error fetching {symbol} from Yahoo Finance: {e}")
            return default_item

    @classmethod
    def _fetch_all_indices(cls) -> List[Dict[str, Any]]:
        """Fetch all global indices concurrently using ThreadPoolExecutor."""
        results = []
        with ThreadPoolExecutor(max_workers=len(cls.INDEX_MAP)) as executor:
            futures = {
                executor.submit(cls._fetch_single_index, symbol, meta): symbol 
                for symbol, meta in cls.INDEX_MAP.items()
            }
            
            # Preserve the ordering of INDEX_MAP
            ordered_symbols = list(cls.INDEX_MAP.keys())
            temp_results = {}
            
            for future in futures:
                symbol = futures[future]
                try:
                    res = future.result()
                    temp_results[symbol] = res
                except Exception as e:
                    logger.error(f"Thread execution error for {symbol}: {e}")
                    temp_results[symbol] = {
                        "id": cls.INDEX_MAP[symbol]["id"],
                        "symbol": symbol,
                        "name": cls.INDEX_MAP[symbol]["name"],
                        "price": 0.0,
                        "change": 0.0,
                        "pChange": 0.0,
                        "success": False
                    }
            
            for sym in ordered_symbols:
                results.append(temp_results[sym])
                
        return results
