"""
Cache utilities and management.
Thread-safe caching for API responses.
"""
import threading
import time
from typing import Dict, Tuple, Any, Optional

class CacheManager:
    """Thread-safe cache manager for API responses."""
    
    def __init__(self, ttl: int = 60):
        """Initialize cache with TTL (time to live) in seconds."""
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self._ttl:
                del self._cache[key]
                return None
            
            return value
    
    def set(self, key: str, value: Any) -> None:
        """Set value in cache with current timestamp."""
        with self._lock:
            self._cache[key] = (value, time.time())
    
    def clear(self) -> None:
        """Clear all cache."""
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> None:
        """Remove expired entries from cache."""
        with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self._cache.items()
                if current_time - timestamp > self._ttl
            ]
            for key in expired_keys:
                del self._cache[key]

# Global cache instances
options_chart_cache = CacheManager(ttl=60)
cpr_filter_cache = CacheManager(ttl=300)
