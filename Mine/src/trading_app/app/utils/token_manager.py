"""Token management utilities for handling Zerodha access tokens.

Provides persistent token storage and retrieval to handle:
- Socket pool flushes during debugging
- Session resets and cache clears
- Token persistence across application restarts
"""

import os
import json
from datetime import datetime
from pathlib import Path
from trading_app.app.utils.logger import logger


class TokenManager:
    """Manages access token persistence and retrieval."""
    
    def __init__(self):
        """Initialize token manager."""
        self.token_file = Path(os.path.dirname(__file__)) / '..' / '..' / '..' / '.token_cache'
        self.token_file = self.token_file.resolve()
    
    def save_token(self, access_token: str, request_token: str = None) -> bool:
        """Save access token to persistent storage.
        
        Args:
            access_token: The access token from Zerodha
            request_token: Optional request token
            
        Returns:
            True if successful, False otherwise
        """
        try:
            token_data = {
                'access_token': access_token,
                'request_token': request_token,
                'saved_at': datetime.now().isoformat(),
                'expires_at': None  # Zerodha tokens expire at end of day
            }
            
            with open(self.token_file, 'w') as f:
                json.dump(token_data, f, indent=2)
            
            logger.info(f"Token saved to cache: {self.token_file}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to save token to cache: {e}")
            return False
    
    def load_token(self) -> dict:
        """Load access token from persistent storage.
        
        Returns:
            Dictionary with 'access_token' and 'request_token' keys,
            or empty dict if no token found or expired
        """
        try:
            if not self.token_file.exists():
                logger.debug("No cached token file found")
                return {}
            
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
            
            access_token = token_data.get('access_token')
            if access_token:
                logger.info(f"Token loaded from cache (saved at {token_data.get('saved_at')})")
                return token_data
            
            return {}
            
        except Exception as e:
            logger.warning(f"Failed to load token from cache: {e}")
            return {}
    
    def clear_token(self) -> bool:
        """Clear cached token file.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.token_file.exists():
                self.token_file.unlink()
                logger.info("Token cache cleared")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to clear token cache: {e}")
            return False
    
    def get_valid_token(self) -> str:
        """Get a valid access token from environment, session, or cache.
        
        Priority order:
        1. Environment variable (OS environment - used after socket flush)
        2. Token cache file (persistent storage)
        3. Return None if none available
        
        Returns:
            Access token string or None if not found
        """
        # First, check environment variable (highest priority - set after socket flush)
        env_token = os.getenv('ACCESS_TOKEN')
        if env_token and self._is_token_valid(env_token):
            logger.debug("Using access token from environment variable")
            return env_token
        
        # Second, check persistent cache
        cached = self.load_token()
        if cached.get('access_token') and self._is_token_valid(cached['access_token']):
            logger.debug("Using access token from cache")
            # Restore to environment for future use
            os.environ['ACCESS_TOKEN'] = cached['access_token']
            return cached['access_token']
        
        logger.warning("No valid access token available")
        return None
    
    def _is_token_valid(self, token: str) -> bool:
        """Validate token format and basic checks.
        
        Args:
            token: Token string to validate
            
        Returns:
            True if token appears valid, False otherwise
        """
        if not token:
            return False
        
        # Check minimum length (typical Zerodha token is a string)
        if len(token) < 5:
            logger.warning(f"Token too short (length: {len(token)})")
            return False
        
        # Check for common invalid patterns
        if token.lower() == 'none' or token == '':
            logger.warning("Token is None or empty string")
            return False
        
        return True


# Global instance
_token_manager = TokenManager()


def save_access_token(access_token: str, request_token: str = None) -> bool:
    """Save access token to persistent storage.
    
    Args:
        access_token: The access token from Zerodha
        request_token: Optional request token
        
    Returns:
        True if successful
    """
    return _token_manager.save_token(access_token, request_token)


def get_access_token() -> str:
    """Get valid access token from environment, session, or cache.
    
    Returns:
        Access token string or None if not found
    """
    return _token_manager.get_valid_token()


def clear_access_token() -> bool:
    """Clear cached access token.
    
    Returns:
        True if successful
    """
    return _token_manager.clear_token()
