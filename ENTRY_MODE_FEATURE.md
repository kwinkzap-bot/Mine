# Entry Mode Feature - Implementation Summary

## Overview
Added a new dropdown selector to choose between two entry signal modes for the Intraday 9:20 strategy backtest.

## Features Implemented

### 1. Frontend UI (intraday_920.html)
- **Location**: Header section near risk/reward ratio dropdown
- **Dropdown ID**: `entryMode`
- **Options**:
  - `candle_open`: Candle Open Mode (existing logic)
  - `high_cross`: High Cross Mode (new logic)
- **Default**: Candle Open

### 2. Frontend Logic (intraday_920.js)
- Reads entry mode selection from dropdown
- Passes `entry_mode` parameter in the backtest API payload
- No other changes to existing logic

### 3. Backend API (api.py)
- Endpoint: `POST /api/intraday-920/backtest-full-day`
- New parameter: `entry_mode` (string, required)
- Validation: Must be "candle_open" or "high_cross"
- Passes entry_mode to strategy backtest function

### 4. Strategy Logic (intraday_9_20.py)

#### Candle Open Mode (Existing Logic)
```
Entry Condition:
- low < reference_high AND close > (reference_high + 5)
- close is within 20 points of reference_high
- If day opened above reference, waits for close below first

Use Case: Standard intraday trading based on candle patterns
```

#### High Cross Mode (New Logic)
```
Entry Condition:
- 5-minute high + 5 points crosses ABOVE the reference level
- Entry triggered when previous candle had (high + 5) <= reference
  AND current candle has (high + 5) > reference

Use Case: Breakout-style entry when price penetrates the level
```

## API Endpoint Changes

### Request Payload (Updated)
```json
{
    "symbol": "NIFTY",
    "ce_token": 12345678,
    "pe_token": 87654321,
    "ce_high": 24500.50,
    "pe_high": 24500.50,
    "ce_strike_price": 25100,
    "pe_strike_price": 25000,
    "date": "2026-01-12",
    "risk_reward_ratio": "1:2-trail",
    "entry_mode": "candle_open"  // NEW: "candle_open" or "high_cross"
}
```

### Response
- Returns same backtest results format
- entry_mode used internally for entry detection logic
- Not reflected in response (for now)

## Testing Instructions

### Test Case 1: Candle Open Mode
1. Select "Candle Open" from Entry Mode dropdown
2. Run backtest
3. Should use existing entry logic (low < ref, close > ref + 5)

### Test Case 2: High Cross Mode
1. Select "High Cross" from Entry Mode dropdown
2. Run backtest
3. Should enter when high + 5 crosses above reference level
4. Typically enters earlier/later than candle open mode

### Test Case 3: Different Dates & Symbols
1. Test both modes with different dates
2. Test with NIFTY, BANKNIFTY, FINNIFTY
3. Verify logs show correct entry mode being used

## Logging
The strategy logs show:
```
Entry Mode: candle_open
Entry (Candle Open) at timestamp: Price, SL, Target

// OR

Entry Mode: high_cross
Entry (High Cross) at candle 12: High X + 5 > ref, Price, SL, Target
```

## Files Modified
1. `Mine/templates/intraday_920.html` - Added dropdown selector
2. `Mine/static/js/intraday_920.js` - Read and send entry_mode
3. `Mine/static/css/intraday_920.css` - Styled selector
4. `Mine/src/trading_app/app/routes/api.py` - Accept entry_mode parameter
5. `Mine/src/trading_app/app/intraday_option/intraday_9_20.py` - Implement both modes

## Git Commit
- Commit: 423989d
- Message: "Add entry mode dropdown (Candle Open vs High Cross)"

## Future Enhancements
- Add entry_mode to backtest results response
- Store user's preferred entry_mode in session/database
- Add entry mode indicator in UI results
- Add comparison view (both modes side-by-side)
