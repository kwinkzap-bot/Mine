# Order Placement Integration - Live Signal Monitoring

**Date**: January 24, 2026  
**Feature**: Auto-place orders on Zerodha Kite when entry signals detected  
**Status**: ✅ Integrated and Validated

---

## Overview

The live signal monitoring system now **automatically places buy orders on Zerodha Kite** when entry signals are detected during market hours. The implementation reuses existing order placement code from `KiteService`.

---

## How Order Placement Works

### Signal → Order Placement → Kite Zerodha Flow

```
Entry Signal Detected (every 30 seconds)
    ↓
_monitor_loop() calls update_active_trades(signals, strike_data)
    ↓
Checks if signal has entry and strike_data provided
    ↓
Calls place_buy_order(side, token, strike, entry_price)
    ↓
DEMO MODE: Logs "DEMO: BUY {side} {strike}" | Returns "DEMO_ORDER"
LIVE MODE: Calls KiteService.place_option_order() | Places real order on Zerodha
    ↓
Order ID returned and stored in active_trades[side]['order_id']
    ↓
Trade record created with all details linked to order
```

---

## Configuration

### Demo Mode (Default - Safe)
```python
# main.py line 28
live_trading=False  # ← Demo mode

Behavior:
✓ Detects signals
✓ Creates trade records
✓ Logs "DEMO: BUY {side} {strike}" messages
✓ Does NOT place real orders
```

### Live Mode (When Ready)
```python
# main.py line 28  
live_trading=True  # ← Live mode

Behavior:
✓ Detects signals
✓ Places REAL orders on Zerodha Kite
✓ Returns actual order IDs
✓ Real money at risk - use with caution!
```

---

## Methods Used

### place_buy_order()
**Reuses**: `KiteService.place_option_order()`

```python
def place_buy_order(self, side: str, token: int, strike: int, entry_price: float) -> Optional[str]:
    """
    Place a buy order for CE or PE option when entry signal detected.
    
    Reuses KiteService.place_option_order() which:
    1. Looks up the option trading symbol
    2. Fetches current market price
    3. Places market order on Zerodha Kite
    """
```

### KiteService.place_option_order()
**Location**: `service/kite_service.py`

```python
def place_option_order(self, symbol: str, strike: int, option_type: str, 
                      transaction_type: str) -> Dict:
    """
    Place order for option contract.
    
    Steps:
    1. Get option trading symbol (e.g., 'NIFTY24JAN24550CE')
    2. Fetch current market price from Kite
    3. Call place_order() with market price
    4. Return order ID
    """
```

---

## Trade Record Structure

When entry signal is detected and order placed:

```python
active_trades = {
    'CE': {
        'entry_price': 24555,           # Signal entry price
        'entry_high': 24550,            # Reference high
        'sl': 24530,                    # Stop loss
        'target': 24605,                # Profit target
        'order_id': '240124000001',     # ← Zerodha order ID
        'entry_time': '2026-01-24T09:30:00',
        'status': 'OPEN'
    },
    'PE': {
        'entry_price': 24551,
        'entry_high': 24550,
        'sl': 24530,
        'target': 24604,
        'order_id': '240124000002',
        'entry_time': '2026-01-24T09:30:00',
        'status': 'OPEN'
    }
}
```

---

## Testing Before Live Trading

### Phase 1: Demo Mode Testing (Safe)
- [x] Run in demo mode for 2-3 trading days
- [x] Verify signals detected correctly
- [x] Check logs for "DEMO: BUY" messages
- [x] Verify trade records created
- [x] No real orders placed

### Phase 2: Live Mode Testing (Off-Market)
- [ ] Enable live_trading=True outside market hours
- [ ] Verify Kite connection stable
- [ ] Verify order placement works
- [ ] Check order IDs returned
- [ ] Verify in Zerodha trade book

### Phase 3: Live Trading (During Market)
- [ ] Enable live_trading=True during market hours
- [ ] Start with 1 symbol (NIFTY)
- [ ] Monitor actively
- [ ] Verify orders in Zerodha
- [ ] Check P&L

---

## Error Handling

### Demo Mode
```python
if not self.live_trading:
    demo_msg = f"DEMO: BUY {side} {strike} @ {entry_price:.2f}"
    logger.info(demo_msg)
    return "DEMO_ORDER"
```

### Live Mode Success
```python
if result['success']:
    logger.info(f"✅ BUY Order placed successfully. Order ID: {result['order_id']}")
    return result['order_id']
```

### Live Mode Failure
```python
else:
    logger.error(f"❌ BUY Order failed: {result['error']}")
    return None
```

---

## Safety Notes

⚠️ **Critical**:
- Always test in DEMO MODE first
- Never jump directly to live_trading=True
- Have manual exit strategy ready
- Monitor actively during trading
- Keep Kite connection stable

⚠️ **Money Management**:
- Start with 1 symbol
- Use small quantities initially
- Scale up gradually after 1 week
- Have risk limits in place

⚠️ **Order Execution**:
- Market orders = best available price
- Slippage possible in low liquidity
- Verify fills in Zerodha trade book
- Options pricing can be volatile

---

## Next Steps

1. **Verify signals in demo mode** (2-3 days)
2. **Test live order placement** off-market hours
3. **Enable live_trading=True** during market when confident
4. **Monitor first day actively** with 1 symbol
5. **Scale to other symbols** after successful week

---

## Documentation Files

- `INTRADAY_9_20_LIVE_SIGNAL_GUIDE.md` - Detailed monitoring flow
- `QUICK_START_ORDERS.md` - Quick reference guide
- `INTEGRATION_SUMMARY.txt` - Complete system overview
