"""
Application configuration module.
Handles environment variables and application settings.
"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration."""
    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-change-in-production")
    
    # API Keys
    API_KEY = os.getenv("API_KEY")
    ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
    
    # CSRF Protection - Disabled for development on localhost
    WTF_CSRF_ENABLED = False  # Disable CSRF entirely for development
    WTF_CSRF_CHECK_DEFAULT = False  
    WTF_CSRF_SSL_STRICT = False
    WTF_CSRF_TIME_LIMIT = None  # Disable token time limit
    
    # Session - Extended timeout to prevent 403 errors during active trading
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 days (604800 seconds)
    SESSION_REFRESH_EACH_REQUEST = True  # Refresh session timeout on each request
    SESSION_COOKIE_NAME = 'trading_session'
    
    # Rate Limiting - Disabled for development
    RATELIMIT_ENABLED = False  # No rate limiting in development
    
    # Cache - Optimized TTLs
    CACHE_DURATION = 60  # seconds
    CACHE_DEFAULT_TTL = 60  # Default cache TTL for API responses
    CACHE_CHART_TTL = 30  # Chart data cache TTL (frequent updates)
    CACHE_CPR_TTL = 600  # CPR filter cache TTL (10 min - data doesn't change fast)
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Market Hours (IST)
    MARKET_OPEN = 9 * 60 + 15  # 9:15 AM in minutes
    MARKET_CLOSE = 15 * 60  # 3:00 PM in minutes
    
    # Threading - Optimized for parallel API calls
    MAX_WORKERS = 8  # Maximum thread pool workers
    THREAD_POOL_WORKERS = 4  # Default workers for parallel operations
    
    # HTTP Client Optimization
    HTTP_TIMEOUT = 15  # Default HTTP timeout in seconds
    HTTP_POOL_CONNECTIONS = 10  # Connection pool size
    HTTP_POOL_MAXSIZE = 20  # Max pool size
    
    # JSON Response Optimization
    JSON_SORT_KEYS = False  # Don't sort keys (faster serialization)
    JSONIFY_PRETTYPRINT_REGULAR = False  # Disable pretty printing (smaller response)

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True

class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    
    # Production-specific optimizations
    SESSION_COOKIE_SECURE = True  # Require HTTPS for cookies
    PREFERRED_URL_SCHEME = 'https'
    
    # Stricter caching in production
    SEND_FILE_MAX_AGE_DEFAULT = 31536000  # 1 year for static files

class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True

# Get configuration based on environment
config_name = os.getenv("FLASK_ENV", "development")
config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}

current_config = config_map.get(config_name, DevelopmentConfig)
