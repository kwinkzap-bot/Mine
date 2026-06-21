"""
Check which NIFTY SmallCap 250 stocks have hit the Upper Circuit today.
Uses Zerodha Kite API for live quotes.
"""
import io
import csv
import sys
import requests
from dotenv import dotenv_values

sys.path.insert(0, '/Users/kavinkumar/Mine/Mine/src')

cfg = dotenv_values('/Users/kavinkumar/Mine/Mine/env/Mine.env')

API_KEY = cfg.get('BROKER_2_API_KEY', '')
ACCESS_TOKEN = cfg.get('BROKER_2_ACCESS_TOKEN', '')


def fetch_smallcap250_symbols():
    """Fetch NIFTY SMALLCAP 250 constituents from NSE."""
    url = 'https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        reader = csv.reader(io.StringIO(r.text))
        header = next(reader)
        sym_idx = next((i for i, c in enumerate(header) if 'symbol' in c.lower()), 2)
        symbols = [row[sym_idx].strip() for row in reader if len(row) > sym_idx and row[sym_idx].strip()]
        print(f"Fetched {len(symbols)} symbols from NSE for NIFTY SMALLCAP 250")
        return symbols
    except Exception as e:
        print(f"Failed to fetch from NSE: {e}")
        # Fallback to hardcoded partial list
        return [
            'AARTIIND', 'ACRYSIL', 'AEGISLOG', 'AFFLE', 'AJANTPHARM', 'ANURAS', 'APARINDS',
            'APOLLOTYRE', 'APTUS', 'ASAHIINDIA', 'AVANTIFEED', 'CERA', 'CRAFTSMAN', 'DATAPATTNS',
            'DCBBANK', 'EDELWEISS', 'ELGIEQUIP', 'EPL', 'EQUITAS', 'ESABINDIA', 'FIEMIND',
            'GESHIP', 'GREENPANEL', 'GRINDWELL', 'HAPPSTMNDS', 'INGERRAND', 'JINDALSAW',
            'JKLAKSHMI', 'KIRLOSENG', 'KRBL', 'LALPATHLAB', 'LATENTVIEW', 'MUTHOOTFIN',
            'NUVAMA', 'NUVOCO', 'PNBHOUSING', 'POONAWALLA', 'POWERINDIA', 'RAMCOIND', 'RATNAMANI'
        ]


def check_upper_circuit():
    from kiteconnect import KiteConnect

    if not API_KEY or not ACCESS_TOKEN:
        print("ERROR: Kite API key or access token not found in env")
        return

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    symbols = fetch_smallcap250_symbols()
    if not symbols:
        print("No symbols fetched!")
        return

    # Kite quote() accepts up to 500 instruments at once
    instruments = [f"NSE:{s}" for s in symbols]

    print(f"\nFetching quotes for {len(instruments)} stocks...")
    try:
        quotes = kite.quote(instruments)
    except Exception as e:
        print(f"ERROR fetching quotes: {e}")
        return

    upper_circuit_hits = []
    errors = []

    for inst, data in quotes.items():
        try:
            ltp = data.get('last_price', 0)
            uc = data.get('upper_circuit_limit', 0)
            ohlc = data.get('ohlc', {})
            prev_close = ohlc.get('close', 0)
            symbol = inst.replace('NSE:', '')

            if uc and ltp and ltp >= uc:
                change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else 0
                upper_circuit_hits.append({
                    'symbol': symbol,
                    'ltp': ltp,
                    'upper_circuit': uc,
                    'prev_close': prev_close,
                    'change_pct': change_pct,
                })
        except Exception as e:
            errors.append(f"{inst}: {e}")

    print(f"\n{'='*60}")
    print(f"NIFTY SMALLCAP 250 - UPPER CIRCUIT STOCKS")
    print(f"{'='*60}")

    if upper_circuit_hits:
        upper_circuit_hits.sort(key=lambda x: x['change_pct'], reverse=True)
        print(f"{'Symbol':<16} {'LTP':>10} {'UC Limit':>10} {'Change%':>10}")
        print(f"{'-'*50}")
        for s in upper_circuit_hits:
            print(f"{s['symbol']:<16} {s['ltp']:>10.2f} {s['upper_circuit']:>10.2f} {s['change_pct']:>9.2f}%")
        print(f"\nTotal: {len(upper_circuit_hits)} stocks at Upper Circuit out of {len(quotes)}")
    else:
        print(f"No stocks at Upper Circuit in NIFTY SMALLCAP 250")
        print(f"(Checked {len(quotes)} out of {len(instruments)} stocks)")

    if errors:
        print(f"\nErrors ({len(errors)}): {errors[:5]}")


if __name__ == '__main__':
    check_upper_circuit()
