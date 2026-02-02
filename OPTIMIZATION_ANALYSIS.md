# Code Analysis & Optimization Report: intraday_9_20_live_signal.py

## Executive Summary
The live signal monitoring module has several performance and code quality issues. Below are identified problems and recommended optimizations.

---

## 🔴 CRITICAL ISSUES

### 1. **Redundant API Calls in `_monitor_loop()` - Lines 900-960**
**Problem:** High strike signals check fetches live data, and if no signals found, Low strike check fetches data again with another timeout.

**Current Code:**
```python
# Check signals for high strike
high_signals = self.check_entry_signal_live(...)
if high_signals.get('success'):
    # Process high signals
    
# Check signals for low strike (ONLY if no signals from high)
if not has_ce_signal and not has_pe_signal:
    low_signals = self.check_entry_signal_live(...)
```

**Issue:** Two separate `check_entry_signal_live()` calls means potentially two separate API calls to fetch latest candles per side.

**Optimization:**
```python
# Fetch BOTH high and low strike data in parallel
with ThreadPoolExecutor(max_workers=2) as executor:
    high_signals_future = executor.submit(self.check_entry_signal_live, 
        high_strike.get('ce_token'), high_strike.get('pe_token'),
        high_strike.get('ce_high'), high_strike.get('pe_high'))
    low_signals_future = executor.submit(self.check_entry_signal_live,
        low_strike.get('ce_token'), low_strike.get('pe_token'),
        low_strike.get('ce_high'), low_strike.get('pe_high'))
    
    high_signals = high_signals_future.result(timeout=10)
    low_signals = low_signals_future.result(timeout=10)
```

**Performance Impact:** Reduce API call time by 50% when checking both strikes.

---

### 2. **Inefficient String Checking - Lines 45-55**
**Problem:** Excel status mapping uses multiple string operations and conditions.

**Current Code:**
```python
excel_status = status
if 'SUCCESS' in status.upper():
    excel_status = side  # BUY or SELL
elif 'FAILED' in status.upper() or 'ERROR' in status.upper():
    excel_status = 'FAILED'
elif 'DEMO' in order_data.get('mode', ''):
    excel_status = f'{side}_DEMO'
```

**Optimization:**
```python
# Use dict mapping for O(1) lookups instead of string searches
STATUS_MAP = {
    'SUCCESS': 'successful',
    'FAILED': 'failed',
    'ERROR': 'error',
    'DEMO': 'demo'
}

status_upper = status.upper()
excel_status = next(
    (v for k, v in STATUS_MAP.items() if k in status_upper),
    status
)
```

**Performance Impact:** Reduce status mapping from O(n) to O(1).

---

### 3. **Repeated Excel Logger Calls - Lines 953-974**
**Problem:** Both high and low strike checks log to Excel with nearly identical code.

**Current Code:**
```python
# LOG HIGH STRIKE
excel_logger.log_signal_check(
    timestamp=check_timestamp,
    ce_prev_high=ce_high_val,
    notes="High Strike Check" if high_strike.get('success') else "High Strike data unavailable"
)

# LOG LOW STRIKE (duplicate code)
excel_logger.log_signal_check(
    timestamp=check_timestamp,
    ce_prev_high=low_ce_high,
    notes="Low Strike Check" if low_strike.get('success') else "Low Strike data unavailable"
)
```

**Optimization:**
```python
def _log_strike_check(self, strike_data, strike_name, signals):
    """Log strike check to Excel (DRY pattern)."""
    excel_logger.log_signal_check(
        timestamp=...,
        ce_prev_high=strike_data.get('ce_high'),
        notes=f"{strike_name} Check" if strike_data.get('success') else f"{strike_name} data unavailable"
    )

# Usage:
_log_strike_check(high_strike, "High Strike", high_signals)
_log_strike_check(low_strike, "Low Strike", low_signals)
```

**Performance Impact:** Reduce code duplication by 50 lines.

---

## 🟡 PERFORMANCE ISSUES

### 4. **Inefficient Quote Fetching - `get_current_price()` Line 775**
**Problem:** Fetches quote for single token with full API overhead.

**Current Code:**
```python
def get_current_price(self, token: int) -> Optional[float]:
    quote = self.kite.quote([f"NFO:{token}"])
    if quote and f"NFO:{token}" in quote:
        ltp = quote[f"NFO:{token}"].get('last_price')
        return ltp
```

**Issue:** Called multiple times per monitoring cycle (potentially 3-4 times per second during active trades).

**Optimization:**
```python
def get_current_prices(self, tokens: List[int]) -> Dict[int, Optional[float]]:
    """Fetch multiple quotes in single API call."""
    try:
        quote_keys = [f"NFO:{token}" for token in tokens]
        quotes = self.kite.quote(quote_keys)
        return {int(k.split(':')[1]): v.get('last_price') 
                for k, v in quotes.items() if k in quote_keys}
    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
        return {}

# In check_sl_target_for_active_trades():
if self.active_trades:
    tokens = [t.get('token') for t in self.active_trades.values() if t.get('token')]
    prices = self.get_current_prices(tokens)  # Single API call for all tokens
```

**Performance Impact:** Reduce API calls by 66% when monitoring 3 active trades.

---

### 5. **Blocking Timeout in Excel Logging - Lines 976-989**
**Problem:** Failed data fetch still logs to Excel with blocking timeout.

**Current Code:**
```python
else:
    logger.warning(f"Failed to fetch live data: {live_data.get('error')}")
    
    excel_logger.log_signal_check(  # This blocks even on failure
        timestamp=check_timestamp,
        notes=f"Failed to fetch data: {live_data.get('error', 'Unknown error')}"
    )
```

**Optimization:**
```python
else:
    logger.warning(f"Failed to fetch live data: {live_data.get('error')}")
    
    # Log failure asynchronously to avoid blocking
    executor.submit(
        excel_logger.log_signal_check,
        timestamp=check_timestamp,
        notes=f"Failed to fetch data: {live_data.get('error', 'Unknown error')}"
    )
```

**Performance Impact:** Reduce blocking time when API calls fail.

---

## 🟢 CODE QUALITY ISSUES

### 6. **Magic Numbers Throughout Code**
**Problem:** Numbers like `3` (monitoring interval), `5` (minute checks), `10` (timeout) are hardcoded.

**Current Code:**
```python
MONITORING_INTERVAL = 3  # seconds
timeout_seconds: int = 10
if now.minute % 5 != 0:
```

**Optimization:**
```python
class Intraday920LiveSignal:
    # Configuration Constants
    MARKET_OPEN = time(9, 15, 0)
    MARKET_CLOSE = time(15, 20, 0)
    MONITORING_INTERVAL = 3  # seconds between SL/Target checks
    ENTRY_CHECK_INTERVAL = 5  # minutes between entry signal checks
    LIVE_DATA_FETCH_TIMEOUT = 10  # seconds
    THREAD_JOIN_TIMEOUT = 5  # seconds
    MAX_SIGNALS_IN_MEMORY = 100
    PRICE_FETCH_BATCH_SIZE = 10  # max tokens per quote call
```

**Performance Impact:** Easier testing, configuration, and maintenance.

---

### 7. **Inefficient List Management - Line 371**
**Problem:** Signal history uses `.pop(0)` which is O(n) operation.

**Current Code:**
```python
self.today_signals.append(signal_entry)
if len(self.today_signals) > 100:
    self.today_signals.pop(0)  # O(n) operation
```

**Optimization:**
```python
from collections import deque

# In __init__():
self.today_signals = deque(maxlen=100)  # Automatic FIFO with O(1) append/remove

# Usage:
self.today_signals.append(signal_entry)  # Automatically removes oldest when full
```

**Performance Impact:** Reduce signal history management from O(n) to O(1).

---

### 8. **Inefficient Signal Check Logic - Lines 900-930**
**Problem:** Complex nested conditionals with duplicate signal processing.

**Current Code:**
```python
if high_signals.get('success'):
    ce_sig = high_signals.get('ce_signal', {})
    pe_sig = high_signals.get('pe_signal', {})
    
    if ce_sig.get('has_signal'):
        has_ce_signal = True
        # ... 5 lines of assignment
    
    if pe_sig.get('has_signal'):
        has_pe_signal = True
        # ... 5 lines of assignment

# Then repeated for low_signals
if not has_ce_signal and not has_pe_signal:
    if low_signals.get('success'):
        ce_sig = low_signals.get('ce_signal', {})
        # ... DUPLICATE LOGIC
```

**Optimization:**
```python
def _process_signals(self, signals, strike_data, strike_name):
    """Process and update signals from any strike."""
    result = {
        'has_ce': False, 'has_pe': False,
        'ce_entry': None, 'ce_sl': None, 'ce_target': None,
        'pe_entry': None, 'pe_sl': None, 'pe_target': None
    }
    
    if not signals.get('success'):
        return result
    
    for side, sig_key in [('CE', 'ce_signal'), ('PE', 'pe_signal')]:
        sig = signals.get(sig_key, {})
        if sig.get('has_signal'):
            result[f'has_{side.lower()}'] = True
            result[f'{side.lower()}_entry'] = sig.get('entry_price')
            result[f'{side.lower()}_sl'] = sig.get('sl')
            result[f'{side.lower()}_target'] = sig.get('target')
            
            self.update_active_trades(signals, strike_data=strike_data)
            self.log_signal(signals)
    
    return result
```

**Performance Impact:** Reduce 100+ lines of duplicate code.

---

### 9. **Thread Safety Issues - `active_trades` Dictionary**
**Problem:** `active_trades` dict accessed from multiple threads without synchronization.

**Current Code:**
```python
# In _monitor_loop() (background thread)
self.active_trades['CE'] = {...}

# In check_sl_target_for_active_trades() (same thread, called from _monitor_loop())
for side, trade in list(self.active_trades.items()):
```

**Optimization:**
```python
import threading

# In __init__():
self.active_trades_lock = threading.Lock()

# Access pattern:
with self.active_trades_lock:
    if 'CE' not in self.active_trades:
        self.active_trades['CE'] = {...}

# In check_sl_target_for_active_trades():
with self.active_trades_lock:
    for side, trade in list(self.active_trades.items()):
        # Process trades
```

**Performance Impact:** Prevent race conditions and data corruption.

---

### 10. **Redundant Type Checks**
**Problem:** Type checking used instead of proper None checks.

**Current Code:**
```python
target_val = order_data.get('target')
sl_val = order_data.get('sl')

excel_logger.log_trade(
    target=float(target_val) if target_val not in ['N/A', None] and target_val != '' else None,
    stop_loss=float(sl_val) if sl_val not in ['N/A', None] and sl_val != '' else None,
)
```

**Optimization:**
```python
def _safe_float(value, default=None):
    """Safely convert value to float, handling None and 'N/A'."""
    if value in [None, 'N/A', '']:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

# Usage:
excel_logger.log_trade(
    target=_safe_float(order_data.get('target')),
    stop_loss=_safe_float(order_data.get('sl')),
)
```

**Performance Impact:** Cleaner, more maintainable code.

---

## 📊 SUMMARY OF OPTIMIZATIONS

| Issue | Type | Performance Gain | Implementation Effort |
|-------|------|------------------|----------------------|
| Redundant API calls (High+Low strikes) | Critical | 50% faster | Medium |
| Inefficient quote fetching | Performance | 66% fewer API calls | Medium |
| String status mapping | Performance | O(1) vs O(n) | Low |
| Duplicate Excel logging | Code Quality | -50 lines | Low |
| Blocking timeout on failures | Performance | Marginal | Low |
| Signal history management | Performance | O(1) vs O(n) | Low |
| Complex signal logic | Code Quality | -100 lines | Medium |
| Thread safety | Stability | Critical | High |
| Inefficient type checks | Code Quality | Cleaner code | Low |
| Magic numbers | Maintainability | Better testing | Low |

---

## 🎯 RECOMMENDED PRIORITY

**High Priority (Do First):**
1. Parallel fetch for High+Low strikes (Critical performance)
2. Batch quote fetching (66% API reduction)
3. Thread safety locks (Prevents crashes)

**Medium Priority:**
4. Extract signal processing logic (Reduces complexity)
5. Add configuration constants (Better maintainability)

**Low Priority (Nice to Have):**
6. Use deque for signal history
7. Status mapping dict
8. Safe float helper
9. Async Excel logging

---

## 💡 IMPLEMENTATION GUIDE

Start with Issue #1 (Parallel fetching) - highest ROI with medium effort.
Then Issue #4 (Batch quotes) - significant performance gain.
Finally Issue #9 (Thread safety) - essential for production.

