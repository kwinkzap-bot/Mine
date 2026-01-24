# Quick Start: Order Placement Integration

## ✅ Status
Live signal monitoring with **automatic order placement** is ready!

---

## Current Setup

### Demo Mode (Default - Active Now)
```python
# main.py line 28
live_trading=False

✓ Signals detected automatically
✓ Orders logged, not executed  
✓ Safe to run during market hours
✓ Perfect for testing
```

---

## To Enable Live Order Placement

### Step 1: Edit main.py
```python
# File: main.py, line 28

Change from:
live_monitors[symbol] = Intraday920LiveSignal(
    kite, 
    symbol=symbol, 
    live_trading=False  # ← Change this
)

To:
live_monitors[symbol] = Intraday920LiveSignal(
    kite, 
    symbol=symbol, 
    live_trading=True  # ← Changed to True
)
```

### Step 2: Verify Market Connection
- Ensure Kite connection is active
- Zerodha account has sufficient margin
- Trading hours: 9:15 AM - 3:30 PM IST (Mon-Fri)

### Step 3: Run Application
```bash
python main.py
```

---

## What Happens

### Entry Signal Detected
```
Time: 09:30:00 AM
Signal: CE crossed above reference + 5 points
Price: 24555
├─ Strike: 24550 CE
├─ SL: 24530
├─ Target: 24605
└─ Order: PLACED on Zerodha ✅
```

### Order Placement (Live Mode)
1. Signal detected at 09:30:00
2. Strike data extracted
3. `place_buy_order()` called
4. KiteService gets current price: 105.50
5. Market order placed: 1 lot @ 105.50
6. Zerodha confirms: Order ID 240124000001
7. Trade record updated with order ID

### Active Trade Record
```python
{
    'CE': {
        'entry_price': 24555,
        'sl': 24530,
        'target': 24605,
        'order_id': '240124000001',  # ← Linked to Zerodha
        'status': 'OPEN'
    }
}
```

---

## Verification

### Check Logs
```
2026-01-24 09:30:00 - 📊 HIGH STRIKE CE SIGNAL: Entry 24555, SL 24530, Target 24605 | ✅ Order placed
2026-01-24 09:30:00 - ✅ BUY Order placed successfully. Order ID: 240124000001 | CE 24550 @ 24555
```

### Check Zerodha
1. Login to Zerodha
2. Orders → Recent orders
3. Look for NIFTY CE/PE orders
4. Verify quantity: 1 lot (50 for NIFTY)
5. Verify price matches entry

---

## Trade Monitoring

### Get Active Trades
```python
trades = monitor.get_active_trades()
for side, trade in trades.items():
    print(f"{side} Order ID: {trade['order_id']}")
    print(f"  Entry: {trade['entry_price']}")
    print(f"  SL: {trade['sl']}")
    print(f"  Target: {trade['target']}")
```

### Exit Options
1. **Automatic**: SL hit or Target hit (detected by monitoring)
2. **Manual**: Call `close_trade(side, exit_price, exit_reason)`
3. **Zerodha**: Exit directly from Zerodha (removes from live tracking)

---

## Safety Checklist

**Before going live:**
- [ ] Run demo mode for 2+ days
- [ ] Verify signals are accurate
- [ ] Test with off-market orders
- [ ] Check Kite connection stability
- [ ] Have manual exit strategy
- [ ] Start with NIFTY only
- [ ] Monitor actively first day

**During live trading:**
- [ ] Monitor logs in real-time
- [ ] Check Zerodha orders
- [ ] Verify price executions
- [ ] Monitor P&L
- [ ] Be ready for manual exit

---

## Symbols & Coverage

| Symbol | Lot Size | High Strike | Low Strike |
|--------|----------|------------|-----------|
| NIFTY | 50 | +50 points | -50 points |
| BANKNIFTY | 15 | +100 points | -100 points |
| FINNIFTY | 40 | +50 points | -50 points |

---

## Order Details

| Property | Value |
|----------|-------|
| Type | BUY (for entry) |
| Mode | MARKET (best available price) |
| Exchange | NFO (Derivatives) |
| Product | NRML |
| Quantity | Lot size |
| Execution | Automatic on signal |

---

## Example Session

```
TIME: 09:15 AM
├─ Market opens
├─ First candle: 9:15-9:20 AM
└─ Monitoring starts

TIME: 09:30:00 AM
├─ Monitor checks (at :00 mark)
├─ Signal detected: CE entry
├─ Order placed: NIFTY 24550 CE BUY
├─ Order ID: 240124000001
└─ Trade record created

TIME: 10:00:00 AM
├─ Monitor checks (at :00 mark)
├─ Entry still OPEN
├─ Price: 24570 (profit: +15)
└─ Waiting for SL or Target

TIME: 10:30:00 AM
├─ Monitor checks (at :30 mark)
├─ Target hit: 24605
├─ Auto-exit triggered
├─ P&L: +50 points = 2500 rupees
└─ Trade CLOSED

RESULT: ✅ Profitable trade automated!
```

---

## Troubleshooting

**No orders appearing in Zerodha?**
- Check live_trading=True is set
- Verify Kite connection active
- Check logs for errors
- Ensure sufficient margin

**Wrong strike price?**
- Signal uses reference high (PE high for CE)
- Check log to verify strike calculation
- Entry price = current candle close
- All details logged for verification

**Order rejected?**
- Check Kite session active
- Verify sufficient margin
- Check for any trading restrictions
- Review error message in logs

---

## Files Modified

- `main.py` - Line 28: live_trading parameter
- `src/trading_app/app/intraday_option/intraday_9_20_live_signal.py` - Order integration
- All changes committed and ready

---

## Summary

### Before (Demo Mode)
```
Signal → Log "DEMO: BUY" → Demo trade record
```

### After (Live Mode)  
```
Signal → Place order on Zerodha → Real order ID → Trade tracking
```

---

**Ready to start?**  
1. Demo mode active by default (safe!)
2. Run: `python main.py`
3. Signals will be detected automatically
4. When confident, enable live_trading=True

**Questions?**  
See: `INTEGRATION_SUMMARY.txt` or `ORDER_PLACEMENT_INTEGRATION.md`
