# Intraday 9:20 Live Signal Monitoring - Documentation

## Table of Contents
1. [Overview](#overview)
2. [How It Starts](#how-it-starts)
3. [Architecture](#architecture)
4. [Step-by-Step Execution Flow](#step-by-step-execution-flow)
5. [Monitoring Loop Details](#monitoring-loop-details)
6. [Signal Detection Logic](#signal-detection-logic)
7. [Trade Management](#trade-management)
8. [Usage Examples](#usage-examples)
9. [API Reference](#api-reference)
10. [Troubleshooting](#troubleshooting)

---

## Overview

The **Intraday 9:20 Live Signal Monitoring System** (`intraday_9_20_live_signal.py`) is a real-time trading signal detection system that:

- **Monitors every 30 seconds** during market hours
- **Market hours**: 9:15 AM - 3:30 PM IST (Monday-Friday only)
- **Detects entry signals** for CE (Call) and PE (Put) options
- **Tracks active trades** with entry/exit prices and P&L
- **Runs in background** as a daemon thread
- **Logs all activities** for audit trail

---

## How It Starts

### 1. **Application Startup** (`main.py`)

```
python main.py
├── Flask app initializes
├── Create app instance
│
└── Background Thread "Intraday920Monitor" starts
    └── start_intraday_9_20_monitoring()
```

### 2. **Initialization Function** - `start_intraday_9_20_monitoring()` in main.py

```python
# Location: main.py, lines 18-57

def start_intraday_9_20_monitoring():
    """Initialize and start the Intraday 9:20 live signal monitoring."""
    
    # Step 1: Import required modules
    from intraday_9_20_live_signal import Intraday920LiveSignal
    from token_manager import get_kite
    
    # Step 2: Get Kite connection
    kite = get_kite()
    if not kite:
        logger.warning("Kite connection not available")
        return
    
    # Step 3: Create monitors for each symbol
    symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
    
    # Step 4: For each symbol:
    for symbol in symbols:
        monitor = Intraday920LiveSignal(kite, symbol=symbol)
        
        # Step 5: Check if market day (Mon-Fri)
        if monitor.is_market_day():
            
            # Step 6: Start monitoring
            if monitor.start_monitoring():
                live_monitors[symbol] = monitor
                logger.info(f"✅ Live monitoring started for {symbol}")
```

**Console Output Example:**
```
🚀 Starting Intraday 9:20 Live Signal Monitoring...
✅ Live monitoring started for NIFTY
✅ Live monitoring started for BANKNIFTY
✅ Live monitoring started for FINNIFTY
✅ Intraday 9:20 Live Signal Monitoring active for: NIFTY, BANKNIFTY, FINNIFTY
```

---

## Architecture

### Class Structure

```
Intraday920LiveSignal
├── Initialization (__init__)
│   ├── kite_instance
│   ├── symbol
│   ├── strategy (Intraday920Strategy)
│   ├── is_monitoring (boolean)
│   ├── monitor_thread (Thread)
│   ├── active_trades (dict)
│   └── today_signals (list)
│
├── Market Validation
│   ├── is_market_hours()
│   ├── is_market_day()
│   └── should_check_now()
│
├── Monitoring
│   ├── start_monitoring()
│   ├── stop_monitoring()
│   └── _monitor_loop() [MAIN LOOP]
│
├── Signal Detection
│   ├── fetch_live_data()
│   ├── check_entry_signal_live()
│   └── _analyze_live_entry()
│
├── Trade Management
│   ├── update_active_trades()
│   ├── close_trade()
│   └── reset_daily()
│
└── Data Retrieval
    ├── get_active_trades()
    └── get_today_signals()
```

---

## Step-by-Step Execution Flow

### **Phase 1: Class Initialization**

```python
# Location: intraday_9_20_live_signal.py, lines 35-65

monitor = Intraday920LiveSignal(kite_instance, symbol='NIFTY')
```

**What Happens:**
```
1. __init__ method called
   ├── Store kite instance
   ├── Store symbol
   ├── Create Intraday920Strategy instance
   ├── Initialize is_monitoring = False
   ├── Initialize monitor_thread = None
   ├── Initialize active_trades = {} (empty dict)
   ├── Initialize today_signals = [] (empty list)
   └── Log initialization message
       └── "Intraday 9:20 Live Signal Monitor initialized for NIFTY"

Result: Monitor object created, ready to start
```

---

### **Phase 2: Starting Monitoring**

```python
# Location: intraday_9_20_live_signal.py, lines 448-471

monitor.start_monitoring()
```

**What Happens:**

```
1. Check if already monitoring
   └── If yes, log warning and return False

2. Check if market day (Mon-Fri)
   ├── Get current date
   ├── Check weekday (0=Mon, 4=Fri, 5=Sat, 6=Sun)
   └── If weekend, log warning and return False

3. Set is_monitoring = True

4. Create background thread
   ├── Target: _monitor_loop() method
   ├── Name: "Intraday920Monitor-NIFTY"
   ├── daemon=True (closes when main program closes)
   └── Start thread
       └── Thread begins executing _monitor_loop()

5. Log success message
   └── "Live signal monitoring started for NIFTY"

6. Return True (success)

Result: Background monitoring thread running
```

**Console Output:**
```
Live signal monitoring started for NIFTY
```

---

### **Phase 3: Main Monitoring Loop** (MOST IMPORTANT)

```python
# Location: intraday_9_20_live_signal.py, lines 355-430
# This runs in background, every 30 seconds

def _monitor_loop(self):
    """Main monitoring loop - runs in background thread."""
```

**Execution Timeline:**

```
TIME: 9:15:00 - 9:15:29
└─ Loop starts
   └─ Check should_check_now()
      └─ current_second = 15
      └─ Return False (not 0 or 30)
   └─ Sleep 1 second
   └─ Loop back

TIME: 9:15:30 ✓ FIRST CHECK
└─ Loop iteration
   ├─ Check should_check_now()
   │  └─ current_second = 30 ✓
   │  └─ Return True
   │
   ├─ Check is_market_hours()
   │  ├─ Current time: 9:15:30 IST
   │  ├─ Between 9:15:00 and 15:30:00? YES ✓
   │  └─ Return True
   │
   ├─ Fetch live data
   │  ├─ Call strategy.get_intraday_920_data('NIFTY')
   │  ├─ Get current prices
   │  ├─ Get first 5-min candle high/low
   │  ├─ Calculate CE/PE strikes
   │  └─ Return strike data with tokens
   │
   ├─ Extract strike information
   │  ├─ high_strike data (CE/PE tokens, high/low)
   │  └─ low_strike data (CE/PE tokens, high/low)
   │
   ├─ Check HIGH STRIKE signals
   │  ├─ Call check_entry_signal_live()
   │  │  ├─ Fetch latest 5-min candles for CE/PE
   │  │  ├─ Analyze CE entry (vs PE high reference)
   │  │  ├─ Analyze PE entry (vs CE high reference)
   │  │  └─ Return signals dict
   │  │
   │  ├─ If signal found:
   │  │  ├─ Update active trades
   │  │  │  ├─ Add CE trade if CE signal
   │  │  │  └─ Add PE trade if PE signal
   │  │  │
   │  │  ├─ Log signal
   │  │  │  └─ Add to today_signals list
   │  │  │
   │  │  └─ Log message
   │  │     └─ "📊 HIGH STRIKE CE SIGNAL: 245.50"
   │
   ├─ Check LOW STRIKE signals
   │  └─ (Same process as HIGH STRIKE)
   │
   └─ Sleep 30 seconds
      └─ Wait until next 30-second mark

TIME: 9:16:00 ✓ SECOND CHECK
└─ (Process repeats)

TIME: 9:16:30 ✓ THIRD CHECK
└─ (Process repeats)

[Continues every 30 seconds until 3:30 PM]

TIME: 3:24:30 ✓ LAST CHECK
└─ (Process repeats)

TIME: 3:25:00 - 3:30:00
└─ Loop continues checking
   └─ is_market_hours() returns False
   └─ Sleep 1 second
   └─ Loop back
   └─ Continues until stop_monitoring() called or app closes
```

---

## Monitoring Loop Details

### **Check Interval Logic**

```python
def should_check_now(self) -> bool:
    """
    Determine if we should check for signals now.
    Returns True only at 0 or 30 second marks.
    """
    current_second = datetime.now().second
    return current_second == 0 or current_second == 30
```

**Examples:**
```
9:15:29 → second=29 → False → Sleep 1s
9:15:30 → second=30 → True  → CHECK ✓
9:15:31 → second=31 → False → Sleep 1s
...
9:16:00 → second=0  → True  → CHECK ✓
9:16:01 → second=1  → False → Sleep 1s
```

### **Market Hours Validation**

```python
def is_market_hours(self, check_time=None) -> bool:
    """Check if current time is within 9:15 AM - 3:30 PM IST."""
    
    if check_time is None:
        check_time = datetime.now()
    
    current_time = check_time.time()
    return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE
    # MARKET_OPEN = 9:15:00
    # MARKET_CLOSE = 15:30:00 (3:30 PM in 24-hour format)
```

**Examples:**
```
9:14:59 → False (before market open)
9:15:00 → True  ✓
9:15:30 → True  ✓
12:00:00 → True ✓
15:29:59 → True ✓
15:30:00 → True ✓
15:30:01 → False (after market close)
```

---

## Signal Detection Logic

### **Entry Signal Detection** - `_analyze_live_entry()`

```python
# Location: intraday_9_20_live_signal.py, lines 192-234

def _analyze_live_entry(self, candles, reference_high, side):
    """Analyze live entry for CE or PE."""
```

**Process:**

```
INPUT:
├── candles: Latest 5-minute candle data
├── reference_high: Reference level (PE high for CE, CE high for PE)
└── side: 'CE' or 'PE'

PROCESSING:
1. Get latest candle from candles list
   └─ latest_candle = candles[-1]

2. Extract candle data
   ├─ candle_low = latest_candle['low']
   ├─ candle_close = latest_candle['close']
   └─ entry_threshold = reference_high + 5

3. Check entry conditions
   
   Condition 1: candle_low < reference_high
   └─ Price must touch or cross below reference level
   
   Condition 2: candle_close > (reference_high + 5)
   └─ Close must be above reference + 5 points
   
   Condition 3: |candle_close - reference_high| < 20
   └─ Close must be within 20 points of reference
   
   All 3 conditions must be TRUE

4. If ALL conditions met:
   ├─ Signal FOUND ✓
   ├─ entry_price = candle_close
   ├─ Calculate SL and Target
   │  └─ SL calculation based on entry and reference
   │  └─ Target = entry + 2 * (entry - SL) [1:2 ratio]
   ├─ Return signal with details
   └─ Log: "CE LIVE ENTRY SIGNAL: Price 245.50, SL 240.25, Target 250.75"

5. If conditions NOT met:
   ├─ No signal
   └─ Return reason (which conditions failed)

OUTPUT:
{
    'side': 'CE',
    'has_signal': True/False,
    'entry_price': 245.50,
    'sl': 240.25,
    'target': 250.75,
    'signal_time': '2026-01-24T09:15:30.123456',
    'reason': 'Entry conditions not met...' (if no signal)
}
```

### **Example Entry Detection**

```
Reference High (PE High for CE): 24500.00
Latest CE Candle:
├─ High: 24515.50
├─ Low: 24495.25
└─ Close: 24506.75

Checking Conditions:
1. Low (24495.25) < Reference (24500.00)? YES ✓
2. Close (24506.75) > Reference+5 (24505.00)? YES ✓
3. |Close (24506.75) - Reference (24500.00)| = 6.75 < 20? YES ✓

Result: ENTRY SIGNAL DETECTED ✓
Entry Price: 24506.75
SL: 24500.00 - 10 = 24490.00
Target: 24506.75 + 2*(24506.75-24490.00) = 24540.50
```

---

## Trade Management

### **Phase 1: Trade Opened - `update_active_trades()`**

```python
# Location: intraday_9_20_live_signal.py, lines 236-258

When entry signal detected:

BEFORE:
self.active_trades = {}

AFTER:
self.active_trades = {
    'CE': {
        'entry_price': 24506.75,
        'sl': 24490.00,
        'target': 24540.50,
        'entry_time': '2026-01-24T09:15:30.123456',
        'status': 'OPEN'
    }
}

Console Log:
🟢 CE trade opened at 24506.75
```

### **Phase 2: Trade Monitored**

```
While monitoring continues:

TIME: 9:16:00
├─ CE Current Price: 24510.00 (profit: 3.25)
├─ Check SL: 24510.00 > 24490.00 ✓ (not hit)
├─ Check Target: 24510.00 < 24540.50 ✓ (not hit)
└─ Trade remains OPEN

TIME: 9:16:30
├─ CE Current Price: 24543.00 (profit: 36.25)
├─ Check SL: 24543.00 > 24490.00 ✓ (not hit)
├─ Check Target: 24543.00 >= 24540.50 ✓ (TARGET HIT!)
└─ Trade auto-closes at target
```

### **Phase 3: Trade Closed - `close_trade()`**

```python
# Location: intraday_9_20_live_signal.py, lines 497-527

monitor.close_trade('CE', exit_price=24540.50, exit_reason='Target Hit')

PROCESSING:
1. Get active CE trade
   └─ entry_price = 24506.75

2. Calculate P&L
   ├─ pnl = exit_price - entry_price
   ├─ pnl = 24540.50 - 24506.75 = 33.75
   ├─ pnl_pct = (33.75 / 24506.75) * 100 = 0.1377%
   └─ Rounded: pnl = 33.75, pnl_pct = 0.14%

3. Update trade record
   ├─ exit_price: 24540.50
   ├─ exit_reason: "Target Hit"
   ├─ exit_time: "2026-01-24T09:16:30.123456"
   ├─ pnl: 33.75
   ├─ pnl_pct: 0.14
   └─ status: "CLOSED"

4. Log result
   └─ 🔴 CE trade closed: Entry 24506.75, Exit 24540.50, PnL 33.75 (0.14%)

ACTIVE TRADES AFTER:
self.active_trades = {
    'CE': {
        'entry_price': 24506.75,
        'sl': 24490.00,
        'target': 24540.50,
        'entry_time': '2026-01-24T09:15:30',
        'exit_price': 24540.50,
        'exit_reason': 'Target Hit',
        'exit_time': '2026-01-24T09:16:30',
        'pnl': 33.75,
        'pnl_pct': 0.14,
        'status': 'CLOSED'
    }
}
```

### **Phase 4: Daily Reset - `reset_daily()`**

```python
# Location: intraday_9_20_live_signal.py, lines 535-541

Called at market close (3:30 PM) or next day opening:

monitor.reset_daily()

PROCESSING:
1. Clear active trades
   └─ active_trades = {}

2. Clear today's signals
   └─ today_signals = []

3. Ready for next trading day
   └─ Log: "Daily monitoring reset for NIFTY"

Result: Fresh start for next day
```

---

## Usage Examples

### **Example 1: Basic Initialization and Monitoring**

```python
from trading_app.app.intraday_option.intraday_9_20_live_signal import Intraday920LiveSignal
from trading_app.app.utils.token_manager import get_kite

# Get Kite connection
kite = get_kite()

# Create monitor for NIFTY
monitor = Intraday920LiveSignal(kite, symbol='NIFTY')

# Start monitoring (runs in background)
if monitor.start_monitoring():
    print("✅ Monitoring started")
else:
    print("❌ Could not start monitoring")

# Keep program running
import time
while True:
    time.sleep(1)
```

**Output:**
```
Starting live signal monitoring for NIFTY
✅ Monitoring started
```

### **Example 2: Check Active Trades During Monitoring**

```python
import time

# Start monitoring
monitor.start_monitoring()

# Wait a few minutes
time.sleep(300)  # 5 minutes

# Get active trades
active_trades = monitor.get_active_trades()

for side, trade in active_trades.items():
    print(f"{side} Trade:")
    print(f"  Entry: {trade['entry_price']}")
    print(f"  SL: {trade['sl']}")
    print(f"  Target: {trade['target']}")
    print(f"  Status: {trade['status']}")
```

**Output:**
```
CE Trade:
  Entry: 245.50
  SL: 240.25
  Target: 250.75
  Status: OPEN
```

### **Example 3: Manual Trade Closure**

```python
# Close CE trade manually
result = monitor.close_trade(
    side='CE',
    exit_price=248.00,
    exit_reason='Manual Exit - Taking Profit'
)

if result['success']:
    trade = result['trade']
    print(f"Trade closed:")
    print(f"  P&L: {trade['pnl']} ({trade['pnl_pct']}%)")
```

**Output:**
```
🔴 CE trade closed: Entry 245.50, Exit 248.00, PnL 2.50 (1.02%)
Trade closed:
  P&L: 2.50 (1.02%)
```

### **Example 4: Get All Today's Signals**

```python
# Get all signals generated today
all_signals = monitor.get_today_signals()

print(f"Total signals today: {len(all_signals)}")

for i, signal in enumerate(all_signals, 1):
    print(f"\nSignal {i}:")
    print(f"  Time: {signal['timestamp']}")
    
    ce_sig = signal['ce_signal']
    if ce_sig.get('has_signal'):
        print(f"  CE Entry: {ce_sig['entry_price']}")
    
    pe_sig = signal['pe_signal']
    if pe_sig.get('has_signal'):
        print(f"  PE Entry: {pe_sig['entry_price']}")
```

**Output:**
```
Total signals today: 5

Signal 1:
  Time: 2026-01-24T09:15:30.123456
  CE Entry: 245.50

Signal 2:
  Time: 2026-01-24T09:16:00.234567
  PE Entry: 252.75
```

### **Example 5: Stop Monitoring**

```python
# Stop monitoring
monitor.stop_monitoring()

print("Monitoring stopped")

# Get final state
trades = monitor.get_active_trades()
signals = monitor.get_today_signals()

print(f"Final trades: {len(trades)}")
print(f"Final signals: {len(signals)}")
```

---

## API Reference

### **Initialization**

```python
monitor = Intraday920LiveSignal(kite_instance, symbol='NIFTY')
```

**Parameters:**
- `kite_instance`: KiteConnect object for market data
- `symbol`: Trading symbol ('NIFTY', 'BANKNIFTY', 'FINNIFTY')

---

### **Market Validation Methods**

```python
# Check if within market hours
is_trading = monitor.is_market_hours()
# Returns: True (9:15 AM - 3:30 PM IST) or False

# Check if market trading day
is_open = monitor.is_market_day()
# Returns: True (Mon-Fri) or False (Sat-Sun)

# Check if should check now (0 or 30 second)
should_check = monitor.should_check_now()
# Returns: True or False
```

---

### **Monitoring Control**

```python
# Start monitoring
success = monitor.start_monitoring()
# Returns: True (started) or False (already running or not market day)

# Stop monitoring
monitor.stop_monitoring()
# No return value

# Reset daily state
monitor.reset_daily()
# No return value
```

---

### **Signal Detection**

```python
# Fetch live market data
data = monitor.fetch_live_data()
# Returns: Dict with strike information or error

# Check for entry signals
signals = monitor.check_entry_signal_live(ce_token, pe_token, ce_high, pe_high)
# Returns: Dict with CE and PE signals
```

---

### **Trade Management**

```python
# Get active trades
trades = monitor.get_active_trades()
# Returns: Dict of active trades {side: trade_details}

# Get today's signals
signals = monitor.get_today_signals()
# Returns: List of all signals generated today

# Close a trade
result = monitor.close_trade(side='CE', exit_price=250.00, exit_reason='Target Hit')
# Returns: Dict with success status and trade details
```

---

## Troubleshooting

### **Problem: Monitoring not starting**

**Solution:**
1. Check if it's a weekday (Mon-Fri)
2. Check if market hours (9:15 AM - 3:30 PM IST)
3. Verify Kite connection: `get_kite()`
4. Check logs for error messages

```python
if not monitor.is_market_day():
    print("Not a market day - monitoring won't start")

if not monitor.is_market_hours():
    print("Outside market hours - monitoring won't start")
```

### **Problem: No signals detected**

**Possible Reasons:**
1. Entry conditions not met (check candle low/close vs reference)
2. No option data available
3. Market volatility preventing entry conditions

**Debug:**
```python
# Check live data availability
data = monitor.fetch_live_data()
if data.get('success'):
    print(f"High Strike: {data['high_strike']}")
else:
    print(f"Error: {data.get('error')}")
```

### **Problem: Signals but no trades opening**

**Check:**
1. Verify entry signal conditions being met
2. Check active_trades dictionary: `monitor.get_active_trades()`
3. Review logs for signal detection messages

```python
# Get all signals to verify they were detected
signals = monitor.get_today_signals()
for sig in signals:
    ce = sig['ce_signal']
    pe = sig['pe_signal']
    print(f"CE Signal: {ce.get('has_signal')}, PE Signal: {pe.get('has_signal')}")
```

### **Problem: Wrong P&L calculation**

**Verify:**
1. Entry price is correct: `trade['entry_price']`
2. Exit price is correct: `trade['exit_price']`
3. P&L = Exit - Entry
4. P&L % = (P&L / Entry) * 100

```python
trade = monitor.get_active_trades().get('CE')
if trade:
    calculated_pnl = trade['exit_price'] - trade['entry_price']
    print(f"Expected P&L: {calculated_pnl}")
    print(f"Recorded P&L: {trade['pnl']}")
```

---

## Performance Notes

- **Memory**: Stores up to 100 signals in memory (older ones removed)
- **Thread Safety**: Uses daemon thread (safe for Flask apps)
- **CPU**: Minimal - only checks every 30 seconds
- **Network**: Requires live Kite API connection

---

## Security Notes

- Monitor runs in background thread automatically
- No authentication required (uses existing Kite connection)
- P&L calculations are immediate (no external API calls)
- All signals and trades logged locally

---

## Summary

| Stage | Timing | What Happens |
|-------|--------|--------------|
| Initialization | App startup | Monitor object created |
| Start | `start_monitoring()` | Background thread begins |
| Check | Every 30s (0 or 30 sec mark) | Fetch data, analyze signals |
| Signal | When conditions met | Create trade record |
| Monitor | Continuously | Track price against SL/Target |
| Close | When SL/Target hit or manual | Calculate P&L, close trade |
| Stop | `stop_monitoring()` or app close | Thread terminates cleanly |

