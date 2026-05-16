"""
Utility constants and helpers.
"""
import re
from datetime import datetime
from typing import Tuple

# Market Hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# Indices
INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']

# Instrument Tokens
INDEX_TOKENS = {
    'NIFTY': 256265,
    'BANKNIFTY': 260105,
    'FINNIFTY': 257801
}

# Strike Rounding Units
BANKNIFTY_ROUNDING = 100
NIFTY_FINNIFTY_ROUNDING = 50

# Strike Offsets
BANKNIFTY_STRIKE_OFFSET = 100
NIFTY_FINNIFTY_STRIKE_STEP = 150

def is_market_hours() -> bool:
    """Check if current time is within market hours (9:15 AM - 3:30 PM)."""
    now = datetime.now()
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= now < market_close

def is_trading_day() -> bool:
    """Check if today is a trading day (Monday-Friday)."""
    # 0 is Monday, 4 is Friday, 5 is Saturday, 6 is Sunday
    return datetime.now().weekday() < 5

def extract_symbol_from_tradingsymbol(trading_symbol: str) -> str:
    """Extract base symbol from trading symbol."""
    match = re.match(r'^([A-Z]+)', trading_symbol)
    if match:
        return match.group(1)
    return trading_symbol

def calculate_cpr(high: float, low: float, close: float) -> Tuple[float, float, float]:
    """Calculate Central Pivot Range (PP, BC, TC)."""
    pp = (high + low + close) / 3
    bc = (high + low) / 2
    tc = (2 * pp) - bc
    return pp, min(bc, tc), max(bc, tc)
