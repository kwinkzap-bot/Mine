"""
Cache utilities and management.
Thread-safe caching for API responses with LRU eviction.
OPTIMIZED: Added LRU eviction, cache stats, and better memory management.
"""
import threading
import time
from typing import Dict, Tuple, Any, Optional
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

class CacheManager:
    """Thread-safe cache manager for API responses with LRU eviction."""
    
    def __init__(self, ttl: int = 60, max_size: int = 1000):
        """
        Initialize cache with TTL (time to live) in seconds.
        
        Args:
            ttl: Time to live for cache entries in seconds
            max_size: Maximum number of entries (LRU eviction when exceeded)
        """
        self._cache: OrderedDict[str, Tuple[Any, float, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_size = max_size
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired. Moves to end for LRU."""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            entry = self._cache[key]
            if len(entry) == 2:
                value, timestamp = entry
                entry_ttl = self._ttl
            else:
                value, timestamp, entry_ttl = entry
                
            if time.time() - timestamp > entry_ttl:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return value
    
    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        """Set value in cache with current timestamp. Evicts LRU if at capacity."""
        with self._lock:
            # If key exists, remove it first (will be re-added at end)
            if key in self._cache:
                del self._cache[key]
            
            # Evict oldest entries if at capacity
            while len(self._cache) >= self._max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            
            entry_ttl = timeout if timeout is not None else self._ttl
            self._cache[key] = (value, time.time(), entry_ttl)
    
    def delete(self, key: str) -> None:
        """Delete key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self) -> None:
        """Clear all cache."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def cleanup_expired(self) -> int:
        """Remove expired entries from cache. Returns count of removed entries."""
        with self._lock:
            current_time = time.time()
            expired_keys = []
            for key, entry in self._cache.items():
                if len(entry) == 2:
                    _, timestamp = entry
                    entry_ttl = self._ttl
                else:
                    _, timestamp, entry_ttl = entry
                if current_time - timestamp > entry_ttl:
                    expired_keys.append(key)
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'ttl': self._ttl,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': f'{hit_rate:.1f}%'
            }
    
    def __len__(self) -> int:
        """Return current cache size."""
        return len(self._cache)


class SmartCache:
    """
    Smart cache with dynamic TTL based on market hours.
    Shorter TTL during market hours, longer after hours.
    """
    
    def __init__(self, market_ttl: int = 60, after_hours_ttl: int = 600, max_size: int = 500):
        """
        Args:
            market_ttl: TTL during market hours (9:15 AM - 3:30 PM IST)
            after_hours_ttl: TTL outside market hours
            max_size: Maximum cache size
        """
        self._market_ttl = market_ttl
        self._after_hours_ttl = after_hours_ttl
        self._cache = CacheManager(ttl=market_ttl, max_size=max_size)
    
    def _is_market_hours(self) -> bool:
        """Check if current time is within market hours (9:15 AM - 3:30 PM IST)."""
        from datetime import datetime
        now = datetime.now()
        # Check weekday (0=Monday, 6=Sunday)
        if now.weekday() >= 5:  # Weekend
            return False
        minutes = now.hour * 60 + now.minute
        market_open = 9 * 60 + 15  # 9:15 AM
        market_close = 15 * 60 + 30  # 3:30 PM
        return market_open <= minutes <= market_close
    
    def get(self, key: str) -> Optional[Any]:
        """Get with dynamic TTL check."""
        return self._cache.get(key)
    
    def set(self, key: str, value: Any, timeout: Optional[int] = None) -> None:
        """Set with dynamic TTL or custom timeout."""
        current_ttl = timeout if timeout is not None else (self._market_ttl if self._is_market_hours() else self._after_hours_ttl)
        self._cache._ttl = current_ttl
        self._cache.set(key, value, timeout=current_ttl)
    
    def delete(self, key: str) -> None:
        """Delete key from cache."""
        self._cache.delete(key)
        
    def clear(self) -> None:
        self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        stats = self._cache.get_stats()
        stats['market_hours'] = self._is_market_hours()
        stats['current_ttl'] = self._market_ttl if self._is_market_hours() else self._after_hours_ttl
        return stats


# Global cache instances - OPTIMIZED with appropriate TTLs and sizes
options_chart_cache = SmartCache(market_ttl=30, after_hours_ttl=300, max_size=200)
cpr_filter_cache = CacheManager(ttl=600, max_size=50)  # 10 min, CPR data doesn't change fast
intraday_cache = SmartCache(market_ttl=60, after_hours_ttl=600, max_size=100)
quote_cache = CacheManager(ttl=5, max_size=500)  # 5 sec for real-time quotes
instruments_cache = CacheManager(ttl=86400, max_size=10)  # 24 hours for instruments
