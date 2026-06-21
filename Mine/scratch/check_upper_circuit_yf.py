"""
Check NIFTY SmallCap 250 stocks for Upper Circuit using yfinance.

Upper Circuit detection logic:
  - Stock closed at or very near its day high (within 0.05%)
  - % change from previous close is within 0.25% of a standard circuit band (2%, 5%, 10%, 20%)

Note: yfinance data is ~15-20 min delayed for NSE during market hours,
      and uses end-of-day close prices after market close.
"""
import json, math, sys
from datetime import datetime
import yfinance as yf

CIRCUIT_BANDS = [2.0, 5.0, 10.0, 20.0]
BAND_TOLERANCE = 0.30   # within ±0.30% of the band counts as at-circuit
CLOSE_TO_HIGH  = 0.05   # last price must be within 0.05% of day_high

with open('/Users/kavinkumar/Mine/Mine/src/trading_app/.cache/index_constituents.json') as f:
    cache = json.load(f)

symbols = cache['constituents']['NIFTY SMALLCAP 250']
print(f"Checking {len(symbols)} stocks from NIFTY SMALLCAP 250 (updated: {cache['last_updated'][:16]})")

yf_symbols = [f"{s}.NS" for s in symbols]
print("Downloading data from Yahoo Finance...")

data = yf.download(yf_symbols, period='2d', interval='1d', progress=False, auto_adjust=False, threads=True)

if data.empty:
    print("ERROR: No data returned")
    sys.exit(1)

today_high  = data['High'].iloc[-1]
today_low   = data['Low'].iloc[-1]
today_close = data['Close'].iloc[-1]
prev_close  = data['Close'].iloc[-2] if len(data) > 1 else None

upper_circuit = []
top_gainers   = []
failed        = []

for sym_ns, sym in zip(yf_symbols, symbols):
    try:
        high = float(today_high[sym_ns])
        low  = float(today_low[sym_ns])
        ltp  = float(today_close[sym_ns])
        pc   = float(prev_close[sym_ns]) if prev_close is not None else None

        if math.isnan(high) or math.isnan(ltp) or pc is None or math.isnan(pc) or pc == 0:
            failed.append(sym)
            continue

        change_pct  = (ltp - pc) / pc * 100
        close_to_high_pct = abs(ltp - high) / pc * 100

        at_high = close_to_high_pct < CLOSE_TO_HIGH
        matched_band = next((b for b in CIRCUIT_BANDS if abs(change_pct - b) <= BAND_TOLERANCE), None)

        row = {
            'symbol': sym, 'ltp': ltp, 'high': high, 'low': low,
            'prev_close': pc, 'change_pct': change_pct,
            'range_pct': (high - low) / pc * 100
        }

        if at_high and matched_band is not None and change_pct > 0:
            row['band'] = matched_band
            upper_circuit.append(row)
        elif change_pct >= 4.0:
            top_gainers.append(row)

    except Exception:
        failed.append(sym)

print(f"\n{'='*70}")
print(f"NIFTY SMALLCAP 250 — UPPER CIRCUIT STOCKS   [{datetime.now():%d %b %Y %H:%M IST}]")
print(f"{'='*70}")

if upper_circuit:
    upper_circuit.sort(key=lambda x: x['change_pct'], reverse=True)
    print(f"\n {'Symbol':<15} {'LTP':>9} {'Prev Close':>11} {'Change%':>9}  {'Band'}")
    print(f" {'-'*58}")
    for s in upper_circuit:
        print(f" {s['symbol']:<15} {s['ltp']:>9.2f} {s['prev_close']:>11.2f} {s['change_pct']:>8.2f}%  {s['band']}% UC  {'(frozen)' if s['range_pct'] < 0.05 else ''}")
    print(f"\n  {len(upper_circuit)} stock(s) at Upper Circuit")
else:
    print("\n  No stocks confirmed at Upper Circuit")

# Show top gainers for context
if top_gainers:
    top_gainers.sort(key=lambda x: x['change_pct'], reverse=True)
    print(f"\n--- Top Gainers in NIFTY SMALLCAP 250 today (not at circuit) ---")
    print(f" {'Symbol':<15} {'LTP':>9} {'Prev Close':>11} {'Change%':>9}")
    print(f" {'-'*50}")
    for s in top_gainers[:15]:
        print(f" {s['symbol']:<15} {s['ltp']:>9.2f} {s['prev_close']:>11.2f} {s['change_pct']:>8.2f}%")

print(f"\n  Checked: {len(symbols) - len(failed)}/{len(symbols)}  |  Failed/No data: {len(failed)}")
if failed:
    print(f"  Missing: {', '.join(failed[:15])}")
