import json
import os
import logging
from typing import List, Set

logger = logging.getLogger(__name__)

class BotOrderTracker:
    """Tracks symbols traded by the application to distinguish from manual trades.
    Tracking is user-wide to ensure consistent exits across multiple accounts.
    """
    
    _STORAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_orders.json')
    
    @staticmethod
    def _load_data():
        if not os.path.exists(BotOrderTracker._STORAGE_FILE):
            return {}
        try:
            with open(BotOrderTracker._STORAGE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[BotOrderTracker] Load error: {e}")
            return {}

    @staticmethod
    def _save_data(data):
        try:
            with open(BotOrderTracker._STORAGE_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"[BotOrderTracker] Save error: {e}")

    @staticmethod
    def add_symbol(username: str, symbol: str):
        """Register a symbol as being traded by the bot (User-wide)."""
        data = BotOrderTracker._load_data()
        key = username
        if key not in data:
            data[key] = []
        
        if symbol not in data[key]:
            data[key].append(symbol)
            BotOrderTracker._save_data(data)
            logger.info(f"[BotOrderTracker] Added {symbol} for user {key}")

    @staticmethod
    def get_symbols(username: str) -> List[str]:
        """Get all symbols traded by the bot for this user."""
        data = BotOrderTracker._load_data()
        key = username
        return data.get(key, [])

    @staticmethod
    def clear_symbol(username: str, symbol: str):
        """Remove a symbol from tracking."""
        data = BotOrderTracker._load_data()
        key = username
        if key in data and symbol in data[key]:
            data[key].remove(symbol)
            BotOrderTracker._save_data(data)
            logger.info(f"[BotOrderTracker] Removed {symbol} for user {key}")

    @staticmethod
    def clear_all_symbols(username: str):
        """Clear all tracking for this user."""
        data = BotOrderTracker._load_data()
        key = username
        if key in data:
            data[key] = []
            BotOrderTracker._save_data(data)
            logger.info(f"[BotOrderTracker] Cleared all symbols for user {key}")
