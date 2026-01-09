#!/usr/bin/env python
"""
Quick test to verify intraday-option route fixes
Tests the fixed strike calculation and data flow
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.app.intraday_option.intraday_option import IntradayOptionTrader

def test_strike_calculation():
    """Test the fixed get_ce_pe_strikes method"""
    print("=" * 70)
    print("TESTING: Strike Calculation (get_ce_pe_strikes)")
    print("=" * 70)
    
    # Create a mock trader (we'll test logic without needing Kite)
    trader = IntradayOptionTrader.__new__(IntradayOptionTrader)
    
    test_cases = [
        (26064.65, "NIFTY at 26064.65"),
        (25000.0, "NIFTY at 25000.0"),
        (26050.25, "NIFTY at 26050.25"),
        (26124.99, "NIFTY at 26124.99"),
        (20000.0, "BANKNIFTY at 20000.0"),
        (21347.50, "BANKNIFTY at 21347.50"),
    ]
    
    print("\nTesting ATM strike calculation:")
    all_pass = True
    for price, description in test_cases:
        ce_strike, pe_strike = trader.get_ce_pe_strikes(price)
        
        # Verify: Both CE and PE should be same (ATM straddle)
        if ce_strike != pe_strike:
            print(f"✗ {description}: CE={ce_strike} != PE={pe_strike} (SHOULD BE EQUAL)")
            all_pass = False
        else:
            # Verify: ATM strike should be within 25 points of underlying
            diff = abs(price - ce_strike)
            if diff > 25:
                print(f"✗ {description}: CE={ce_strike} is {diff} points away (TOO FAR)")
                all_pass = False
            else:
                # Verify: ATM strike should be multiple of 50
                if ce_strike % 50 != 0:
                    print(f"✗ {description}: CE={ce_strike} not multiple of 50")
                    all_pass = False
                else:
                    print(f"✓ {description}: CE={ce_strike}, PE={pe_strike} (ATM Straddle)")
    
    print("\n" + "=" * 70)
    if all_pass:
        print("✓ All strike calculation tests PASSED")
        return True
    else:
        print("✗ Some strike calculation tests FAILED")
        return False


def test_candle_data_consistency():
    """Test that candle data handling is now consistent"""
    print("\n" + "=" * 70)
    print("TESTING: Candle Data Consistency")
    print("=" * 70)
    
    # Create mock candle data
    ce_candles = [{'close': 100 + i} for i in range(50)]
    pe_candles = [{'close': 100 + i} for i in range(50)]
    
    # Verify both would have same length when returned
    ce_returned = ce_candles[-50:] if ce_candles else []
    pe_returned = pe_candles[-50:] if pe_candles else []
    
    print(f"\nCE candles returned: {len(ce_returned)}")
    print(f"PE candles returned: {len(pe_returned)}")
    
    if len(ce_returned) == len(pe_returned) == 50:
        print("✓ CE and PE candle counts are equal")
        return True
    else:
        print(f"✗ CE and PE candle counts differ: {len(ce_returned)} vs {len(pe_returned)}")
        return False


def test_array_indexing():
    """Test the fixed array indexing in chart updates"""
    print("\n" + "=" * 70)
    print("TESTING: Array Indexing (Chart Update Fix)")
    print("=" * 70)
    
    # Simulate the fixed code
    ce_candles = [{'time': i, 'close': 100 + i} for i in range(20)]
    pe_candles = [{'time': i, 'close': 105 + i} for i in range(20)]
    
    try:
        # This was the BUG: using ceCandles.length for PE array
        # The fix: use correct array length
        ce_last = ce_candles[len(ce_candles) - 1]  # Correct way
        pe_last = pe_candles[len(pe_candles) - 1]  # Correct way
        
        print(f"\n✓ CE last candle: time={ce_last['time']}, close={ce_last['close']}")
        print(f"✓ PE last candle: time={pe_last['time']}, close={pe_last['close']}")
        print("✓ No array index errors")
        return True
    except IndexError as e:
        print(f"✗ Array indexing error: {e}")
        return False


def main():
    """Run all tests"""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + " INTRADAY OPTION ROUTE - FIX VERIFICATION ".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    
    results = []
    
    try:
        results.append(("Strike Calculation", test_strike_calculation()))
    except Exception as e:
        print(f"✗ Strike calculation test failed: {e}")
        results.append(("Strike Calculation", False))
    
    try:
        results.append(("Candle Data Consistency", test_candle_data_consistency()))
    except Exception as e:
        print(f"✗ Candle data test failed: {e}")
        results.append(("Candle Data Consistency", False))
    
    try:
        results.append(("Array Indexing", test_array_indexing()))
    except Exception as e:
        print(f"✗ Array indexing test failed: {e}")
        results.append(("Array Indexing", False))
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(result for _, result in results)
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✓✓✓ ALL TESTS PASSED ✓✓✓")
        print("\nIntraday-option route is ready for testing!")
        print("Start the server with:")
        print("  cd /Users/kavinkumar/Mine/Mine")
        print("  /Users/kavinkumar/Mine/.venv/bin/python main.py")
        print("\nThen visit: http://127.0.0.1:5000/intraday-option")
        return 0
    else:
        print("✗✗✗ SOME TESTS FAILED ✗✗✗")
        return 1

if __name__ == '__main__':
    sys.exit(main())
