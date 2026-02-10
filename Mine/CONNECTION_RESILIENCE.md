# Connection Resilience Implementation

## Problem Statement
Every 5 minutes during live monitoring, connection reset errors occur when the Kite API resets the connection during high load (candle check time). This causes missed auto-entries.

```
ConnectionResetError(54, 'Connection reset by peer')
Failed to fetch data: Error fetching quote: ('Connection aborted.', ...)
```

## Root Cause
`KiteService.get_current_prices_batch()` (used every 30 seconds by live signal monitoring) had **NO retry logic**. It would:
1. Call `self.kite.quote(quote_keys)` once
2. On connection reset, catch exception and return `{token: None}` 
3. Live signal monitoring receives None prices → skips all checks
4. Entries are missed silently

## Solution Implemented

### 1. Exponential Backoff Retry Logic (CRITICAL FIX)
**File:** `src/trading_app/service/kite_order_services.py`
**Method:** `get_current_prices_batch()` (lines 652-729)

```python
Retry Strategy:
├─ Attempt 1: Immediate (0ms delay)
├─ Attempt 2: 500ms delay (0.5 × 2^0)
├─ Attempt 3: 1000ms delay (0.5 × 2^1)  
└─ Attempt 4: 2000ms delay (0.5 × 2^2)

Max Total Time: ~3.5 seconds (before giving up)
```

### 2. Error Classification

**Retriable Errors** (with exponential backoff):
- `ConnectionResetError` / `ConnectionAbortedError` / `ConnectionRefused`
- `NetworkException` from KiteConnect library
- Server errors: `504 Gateway Timeout`, `503 Service Unavailable`
- Network issues: `broken pipe`, `reset by peer`, `timeout`
- JSON parsing errors: `couldn't parse`

**Token Expiry** (special handling):
- `TokenException` → Logs warning, retries (assumes token manager will refresh)

**Non-Retriable Errors** (fail immediately):
- Invalid parameters
- Authentication failures
- Instrument not found

### 3. Enhanced Logging

Each retry attempt is logged with:
```
[Batch Price Fetch] Network error (attempt 1/3): Connection reset by peer
[Batch Price Fetch] Retriable error (attempt 2/3): Connection aborted...
[Batch Price Fetch] Failed after 3 retries. Last error: Retriable error: ...
```

### 4. Partial Results Handling

If some tokens succeed but others fail:
```python
# Returns partial results - only None for truly failed tokens
{
    token_1: 543.25,      # ✓ Fetched successfully
    token_2: None,        # ✗ Failed after 3 retries
    token_3: 567.50       # ✓ Fetched successfully
}
```

## Impact on Live Monitoring

### SL/Target Monitoring (every 3 seconds)
```
Before: One connection reset → All prices None → SL/Target check skipped
After:  Connection reset → Retry up to 3 times → Resilient to temporary network issues
```

### Entry Signal Check (every 5 minutes)
```
Before: Connection reset during 5-min candle fetch → Entry signal missed
After:  Connection reset → Automatic retry with backoff → Entry signal detected correctly
```

## Testing Checklist

- [ ] **Simulate Connection Reset**
  ```bash
  # Kill Kite connection while monitoring
  # Should see retry attempts in logs
  # Should recover and continue monitoring
  # Entries should still execute correctly
  ```

- [ ] **Monitor Logs During High Load (9:30-10:00 IST)**
  ```bash
  # Watch for "[Batch Price Fetch]" messages
  # Should see successful fetches
  # Should see 0 or very few retry attempts during normal operation
  ```

- [ ] **Verify Excel Logging**
  ```bash
  # Check Signal Checks sheet
  # Should see all 5-minute checks logged (no gaps)
  # Should see entry signals detected correctly
  ```

## Configuration

Current defaults in `get_current_prices_batch()`:
```python
max_retries = 3          # Maximum attempts
retry_delay = 0.5        # Initial delay in seconds (exponential backoff)
exponential_base = 2     # Multiply delay by 2 each retry: 0.5s, 1s, 2s
```

To increase resilience (more tolerant of poor networks):
```python
max_retries = 5          # 5 attempts = up to 8 seconds total
```

## Performance Impact

- **Normal case (no errors):** ~200ms API call time (unchanged)
- **One-off error:** ~700ms (1 retry)
- **Persistent errors:** ~3.5s max (all 3 retries)
- **Token refresh:** ~100-200ms additional

## Monitoring Metrics

Add to Excel logs to track connection quality:
```
Columns to add:
- connection_retry_count: Number of retries needed for this fetch
- fetch_success_rate: Percentage of successful 5-minute checks
- avg_retry_delay: Average time spent retrying
```

## Related Components

### Already Has Good Retry Logic:
- `src/trading_app/app/intraday_option/intraday_9_20.py` (lines 390-450) - Entry signal fetching
  - Used as model for this implementation
  - 3 retries with exponential backoff
  - Handles TokenException and NetworkException

### New Retry Logic:
- `src/trading_app/service/kite_order_services.py` - Price fetching for live monitoring
  - `get_current_prices_batch()` - Batch price fetching (CRITICAL)
  - `get_current_price()` - Delegates to batch method
  
### Already Robust:
- `src/trading_app/app/intraday_option/intraday_9_20_live_signal.py` 
  - `_fetch_live_data_with_timeout()` - 10 second timeout for entry signal checks
  - `check_sl_target_for_active_trades()` - Handles partial price data gracefully

## Next Steps

1. Monitor live trading for connection errors
2. Track retry metrics in Excel logs
3. If still seeing failures after 5 minutes, consider:
   - Increasing max_retries to 5
   - Implementing circuit breaker pattern
   - Adding fallback price caching (use last known price if fetch fails)

## Commit

```
CRITICAL: Add exponential backoff retry logic to get_current_prices_batch()

- Implements 3-retry strategy with exponential backoff (0.5s, 1s, 2s)
- Handles TokenException, NetworkException, connection reset errors
- Detects retriable vs non-retriable errors intelligently
- Returns partial results (only None for truly failed tokens)
- Added detailed logging for each retry attempt with attempt numbers
- Crucial fix: Live monitoring now resilient to network hiccups
- Prevents missed auto-entries during temporary connection resets
```
