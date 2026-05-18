import os
import io
import csv
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "index_constituents.json")

# Core hardcoded fallbacks in case network is down or rate-limited
HARDCODED_CONSTITUENTS = {
    'NIFTY': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC', 
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT', 
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL', 
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'ADANIPOWER', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 
        'TATAMOTORS', 'EICHERMOT', 'HEROMOTOCO', 'APOLLOHOSP', 'DIVISLAB', 'UPL', 'BAJAJ-AUTO', 'LTIM', 'SBILIFE', 'HDFCLIFE'
    ],
    'BANKNIFTY': [
        'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'AUBANK', 'FEDERALBNK', 'IDFCFIRSTB', 'PNB', 
        'BANDHANBNK', 'BANKBARODA'
    ],
    'FINNIFTY': [
        'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'AXISBANK', 'SBIN', 'BAJFINANCE', 'BAJAJFINSV', 'PFC', 'RECLTD', 'CHOLAFIN', 
        'MUTHOOTFIN', 'SHRIRAMFIN', 'LICHSGFIN', 'HDFCLIFE', 'SBILIFE', 'ICICIPRULI', 'ICICIGI', 'HDFCAMC', 'SBICARD', 'M&MFIN'
    ],
    'MIDCPNIFTY': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB', 
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD', 
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY'
    ],
    'SENSEX': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC', 
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'SUNPHARMA', 'M&M', 'ULTRACEMCO', 'POWERGRID', 
        'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'TATASTEEL', 'TECHM', 'HINDUNILVR', 'NESTLEIND', 'TATAMOTORS', 'JINDALSTEL', 'JSWSTEEL'
    ],
    'NIFTY MIDCAP 150': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB', 
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD', 
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY'
    ],
    'NIFTY AUTO': [
        'TATAMOTORS', 'M&M', 'MARUTI', 'BAJAJ-AUTO', 'EICHERMOT', 'HEROMOTOCO', 'TVSMOTOR', 'BALKRISIND', 'BHARATFORG', 'ASHOKLEY'
    ],
    'NIFTY Smallcap 100': [
        'ANGELONE', 'BSE', 'CDSL', 'CYIENT', 'EIDPARRY', 'HUDCO', 'IRB', 'KEI', 'MCX', 'PNBHOUSING'
    ],
    'NIFTY SMLCAP 100': [
        'ANGELONE', 'BSE', 'CDSL', 'CYIENT', 'EIDPARRY', 'HUDCO', 'IRB', 'KEI', 'MCX', 'PNBHOUSING'
    ],
    'NIFTY FMCG': [
        'ITC', 'HINDUNILVR', 'NESTLEIND', 'BRITANNIA', 'GODREJCP', 'DABUR', 'COLPAL', 'TATACONSUM', 'MARICO', 'VBL'
    ],
    'NIFTY METAL': [
        'TATASTEEL', 'HINDALCO', 'JSWSTEEL', 'COALINDIA', 'VEDL', 'NATIONALUM', 'SAIL', 'NMDC', 'APLAPOLLO', 'JINDALSTEL'
    ],
    'NIFTY PHARAMA': [
        'SUNPHARMA', 'CIPLA', 'DRREDDY', 'DIVISLAB', 'LUPIN', 'TORNTPHARM', 'ALKEM', 'AUROPHARMA', 'GLENMARK', 'BIOCON'
    ],
    'NIFTY PHARMA': [
        'SUNPHARMA', 'CIPLA', 'DRREDDY', 'DIVISLAB', 'LUPIN', 'TORNTPHARM', 'ALKEM', 'AUROPHARMA', 'GLENMARK', 'BIOCON'
    ],
    'NIFTY PSU BANK': [
        'SBIN', 'BANKBARODA', 'PNB', 'CANBK', 'UNIONBANK', 'IOB', 'INDIANB', 'MAHABANK', 'UCOBANK', 'CENTRALBK'
    ],
    'NIFTY IT': [
        'TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTIM', 'COFORGE', 'PERSISTENT', 'LTTS', 'MPHASIS'
    ]
}

URLS = {
    'NIFTY': 'https://archives.nseindia.com/content/indices/ind_nifty50list.csv',
    'BANKNIFTY': 'https://archives.nseindia.com/content/indices/ind_niftybanklist.csv',
    'FINNIFTY': 'https://archives.nseindia.com/content/indices/ind_niftyfinancelist.csv',
    'MIDCPNIFTY': 'https://archives.nseindia.com/content/indices/ind_niftymidcap50list.csv',
    'NIFTY MIDCAP 150': 'https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv',
    'NIFTY AUTO': 'https://archives.nseindia.com/content/indices/ind_niftyautolist.csv',
    'NIFTY Smallcap 100': 'https://archives.nseindia.com/content/indices/ind_niftysmallcap100list.csv',
    'NIFTY SMLCAP 100': 'https://archives.nseindia.com/content/indices/ind_niftysmallcap100list.csv',
    'NIFTY FMCG': 'https://archives.nseindia.com/content/indices/ind_niftyfmcglist.csv',
    'NIFTY METAL': 'https://archives.nseindia.com/content/indices/ind_niftymetallist.csv',
    'NIFTY PHARAMA': 'https://archives.nseindia.com/content/indices/ind_niftypharmalist.csv',
    'NIFTY PHARMA': 'https://archives.nseindia.com/content/indices/ind_niftypharmalist.csv',
    'NIFTY PSU BANK': 'https://archives.nseindia.com/content/indices/ind_niftypsubanklist.csv',
    'NIFTY IT': 'https://archives.nseindia.com/content/indices/ind_niftyitlist.csv'
}

class DynamicConstituentsService:
    _cache = {}
    _last_updated = None

    @classmethod
    def load_cache_from_disk(cls):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    data = json.load(f)
                    cls._cache = data.get('constituents', {})
                    last_updated_str = data.get('last_updated')
                    if last_updated_str:
                        cls._last_updated = datetime.fromisoformat(last_updated_str)
                logger.info(f"Loaded dynamic index constituents cache from disk (last updated: {cls._last_updated})")
            except Exception as e:
                logger.warning(f"Failed to load constituents cache from disk: {e}")

    @classmethod
    def save_cache_to_disk(cls):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump({
                    'constituents': cls._cache,
                    'last_updated': cls._last_updated.isoformat() if cls._last_updated else None
                }, f, indent=4)
        except Exception as e:
            logger.warning(f"Failed to save constituents cache to disk: {e}")

    @classmethod
    def get_constituents(cls, index_name: str) -> List[str]:
        index_name = index_name.upper().strip()
        
        # Load cache if not already loaded in memory
        if not cls._cache:
            cls.load_cache_from_disk()

        now = datetime.now()
        # Cache is valid for 24 hours
        cache_valid = cls._last_updated and (now - cls._last_updated) < timedelta(hours=24)
        
        if cache_valid and index_name in cls._cache and cls._cache[index_name]:
            return cls._cache[index_name]

        # If cache is stale or missing, try to fetch dynamically
        if index_name in URLS:
            try:
                logger.info(f"Fetching dynamic constituents for {index_name} from NSE...")
                url = URLS[index_name]
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                
                symbols = []
                csv_file = io.StringIO(response.text)
                reader = csv.reader(csv_file)
                header = next(reader)
                
                symbol_idx = -1
                for i, col in enumerate(header):
                    if 'symbol' in col.lower():
                        symbol_idx = i
                        break
                if symbol_idx == -1:
                    symbol_idx = 2
                    
                for row in reader:
                    if len(row) > symbol_idx:
                        sym = row[symbol_idx].strip()
                        if sym and not sym.startswith('Company Name') and not sym.startswith('Symbol'):
                            symbols.append(sym)
                
                if symbols:
                    cls._cache[index_name] = symbols
                    cls._last_updated = now
                    cls.save_cache_to_disk()
                    logger.info(f"Successfully updated dynamic constituents for {index_name} ({len(symbols)} symbols)")
                    return symbols
            except Exception as e:
                logger.warning(f"Failed to fetch constituents dynamically for {index_name}: {e}. Falling back to cache/defaults.")

        # Fallback to cached value even if stale
        if index_name in cls._cache and cls._cache[index_name]:
            return cls._cache[index_name]

        # Ultimate fallback to hardcoded list
        return HARDCODED_CONSTITUENTS.get(index_name, HARDCODED_CONSTITUENTS['NIFTY'])
