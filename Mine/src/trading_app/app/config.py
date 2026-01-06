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
    
    # Session - Relaxed for development
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 days
    
    # Rate Limiting - Disabled for development
    RATELIMIT_ENABLED = False  # No rate limiting in development
    
    # Cache
    CACHE_DURATION = 60  # seconds
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Market Hours (IST)
    MARKET_OPEN = 9 * 60 + 15  # 9:15 AM in minutes
    MARKET_CLOSE = 15 * 60  # 3:00 PM in minutes
    
    # Threading
    MAX_WORKERS = 8
    THREAD_POOL_WORKERS = 2

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True

class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False

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
