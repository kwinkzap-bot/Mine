"""
Opening OI Cache Module - Stores OI values at market open (9:15 AM)
"""
import json
import os
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class OpeningOICache:
    """
    Caches opening OI values (captured at 9:15 AM) for each trading day.
    Format: {symbol: {date: {strike: {ce_oi: X, pe_oi: Y}}}}
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(__file__), '.cache')
        
        self.cache_dir = cache_dir
        self.cache_file = os.path.join(cache_dir, 'opening_oi.json')
        os.makedirs(cache_dir, exist_ok=True)
        self._cache = self._load_cache()
    
    def _load_cache(self) -> Dict:
        """Load cache from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load opening OI cache: {e}")
        return {}
    
    def _save_cache(self):
        """Save cache to disk"""
        try:
            logger.debug(f"Saving cache to: {self.cache_file}")
            with open(self.cache_file, 'w') as f:
                json.dump(self._cache, f, indent=2)
            logger.debug(f"✅ Cache saved successfully")
        except Exception as e:
            logger.error(f"❌ Failed to save opening OI cache at {self.cache_file}: {e}", exc_info=True)
    
    def _get_today_key(self) -> str:
        """Get today's date as cache key (YYYY-MM-DD)"""
        return datetime.now().strftime('%Y-%m-%d')
    
    def get_opening_oi(self, symbol: str, strike: float, option_type: str) -> Optional[int]:
        """
        Get opening OI for a strike (cached at 9:15 AM).
        
        Args:
            symbol: Trading symbol (NIFTY, BANKNIFTY, etc.)
            strike: Strike price
            option_type: 'CE' or 'PE'
            
        Returns:
            Opening OI value or None if not cached
        """
        today = self._get_today_key()
        strike_key = str(strike)
        
        try:
            opening_ois = self._cache.get(symbol, {}).get(today, {})
            if not opening_ois:
                logger.debug(f"Cache miss: {symbol}/{today} - no data")
                return None
            
            value = opening_ois.get(strike_key, {}).get(option_type)
            if value is not None:
                logger.debug(f"Cache hit: {symbol} {strike} {option_type} = {value}")
            else:
                logger.debug(f"Cache miss: {symbol} {strike} {option_type} - strike not found")
            return value
        except Exception as e:
            logger.warning(f"Error getting opening OI for {symbol} {strike} {option_type}: {e}")
            return None
    
    def cache_opening_oi(self, symbol: str, strikes_data: Dict[float, Dict]):
        """
        Cache opening OI values for all strikes (called at 9:15 AM).
        
        Args:
            symbol: Trading symbol
            strikes_data: Dict with format {strike: {ce_oi: X, pe_oi: Y}}
        """
        today = self._get_today_key()
        logger.info(f"📝 Starting cache_opening_oi: symbol={symbol}, date={today}, strikes={len(strikes_data)}")
        
        if symbol not in self._cache:
            self._cache[symbol] = {}
            logger.debug(f"Created new symbol entry: {symbol}")
        
        if today not in self._cache[symbol]:
            self._cache[symbol][today] = {}
            logger.debug(f"Created new date entry: {today}")
        
        for strike, oi_values in strikes_data.items():
            strike_key = str(strike)
            self._cache[symbol][today][strike_key] = {
                'CE': oi_values.get('ce_oi', 0),
                'PE': oi_values.get('pe_oi', 0),
                'timestamp': datetime.now().isoformat()
            }
        
        logger.debug(f"Cache data prepared: {list(self._cache[symbol][today].items())[:3]}")
        self._save_cache()
        logger.info(f"✅ Cached opening OI for {symbol} on {today}: {len(strikes_data)} strikes")
    
    def is_cached_today(self, symbol: str) -> bool:
        """Check if opening OI is already cached for today"""
        today = self._get_today_key()
        return bool(self._cache.get(symbol, {}).get(today))
    
    def clear_old_caches(self, days_to_keep: int = 5):
        """Remove cache entries older than N days"""
        from datetime import datetime, timedelta
        
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d')
        
        for symbol in list(self._cache.keys()):
            dates_to_remove = [d for d in self._cache[symbol].keys() if d < cutoff_date]
            for date in dates_to_remove:
                del self._cache[symbol][date]
                logger.info(f"Cleared old cache for {symbol} on {date}")
        
        self._save_cache()

# Global instance
_opening_oi_cache = None

def get_opening_oi_cache() -> OpeningOICache:
    """Get or create global opening OI cache instance"""
    global _opening_oi_cache
    if _opening_oi_cache is None:
        cache_dir = os.path.join(os.path.dirname(__file__), '.cache')
        _opening_oi_cache = OpeningOICache(cache_dir)
    return _opening_oi_cache
