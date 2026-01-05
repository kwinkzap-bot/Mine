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
    
    # CSRF Protection - Allow localhost/127.0.0.1
    WTF_CSRF_CHECK_DEFAULT = False  # Disable CSRF check by default
    WTF_CSRF_SSL_STRICT = False  # Allow non-SSL in development
    
    # Session
    SESSION_COOKIE_SECURE = False  # Allow in development
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'  # Allow same-site requests
    
    # Rate Limiting
    RATELIMIT_DEFAULT = "200 per day, 50 per hour"
    
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
