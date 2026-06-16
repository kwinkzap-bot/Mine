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
    ],
    'NIFTY 100': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'ADANIPOWER', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND',
        'TATAMOTORS', 'EICHERMOT', 'HEROMOTOCO', 'APOLLOHOSP', 'DIVISLAB', 'UPL', 'BAJAJ-AUTO', 'LTIM', 'SBILIFE', 'HDFCLIFE',
        'SIEMENS', 'HAL', 'BEL', 'PIDILITIND', 'GODREJCP', 'TATACONSUM', 'COLPAL', 'MARICO', 'VBL', 'DABUR',
        'DLF', 'PFC', 'RECLTD', 'SHREECEM', 'AMBUJACEM', 'IOC', 'GAIL', 'TATAPOWER', 'TORNTPHARM', 'ALKEM',
        'LUPIN', 'AUROPHARMA', 'ZOMATO', 'NAUKRI', 'IRCTC', 'SBICARD', 'CHOLAFIN', 'SHRIRAMFIN', 'M&MFIN', 'MUTHOOTFIN',
        'L&TFH', 'LICHSGFIN', 'ABB', 'CUMMINSIND', 'VOLTAS', 'HAVELLS', 'MAXHEALTH', 'FORTIS', 'OBEROIRLTY', 'GODREJPROP',
        'PRESTIGE', 'ACC', 'DALBHARAT', 'SRF', 'DEEPAKNTR', 'COFORGE', 'PERSISTENT', 'MPHASIS', 'LTTS', 'TATACOMM'
    ],
    'NIFTY 200': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS',
        'EICHERMOT', 'HEROMOTOCO', 'APOLLOHOSP', 'DIVISLAB', 'BAJAJ-AUTO', 'LTIM', 'SBILIFE', 'HDFCLIFE', 'SIEMENS', 'HAL',
        'BEL', 'PIDILITIND', 'GODREJCP', 'TATACONSUM', 'MARICO', 'VBL', 'DLF', 'PFC', 'RECLTD', 'IOC',
        'GAIL', 'TATAPOWER', 'LUPIN', 'ZOMATO', 'NAUKRI', 'CHOLAFIN', 'SHRIRAMFIN', 'MUTHOOTFIN', 'ABB', 'HAVELLS',
        'MAXHEALTH', 'FORTIS', 'GODREJPROP', 'ACC', 'DALBHARAT', 'SRF', 'DEEPAKNTR', 'COFORGE', 'PERSISTENT', 'MPHASIS',
        'DIXON', 'VOLTAS', 'BLUESTAR', 'CROMPTON', 'TATAELXSI', 'POLYCAB', 'SUNDARMFIN', 'HDFCAMC', 'ICICIGI', 'STARHEALTH'
    ],
    'NIFTY 500': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS',
        'DLF', 'PIDILITIND', 'GODREJCP', 'TATACONSUM', 'MARICO', 'PFC', 'RECLTD', 'IOC', 'GAIL', 'TATAPOWER'
    ],
    'NIFTY MIDCAP 50': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB',
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD',
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY', 'TORNTPOWER', 'PERSISTENT', 'COFORGE', 'MAXHEALTH', 'FORTIS',
        'NMDC', 'NATIONALUM', 'SAIL', 'APLAPOLLO', 'KEI', 'TATAELXSI', 'DIXON', 'CROMPTON', 'BLUESTAR', 'AMBER',
        'SUNDARMFIN', 'MFSL', 'HDFCAMC', 'ICICIGI', 'LALPATHLAB', 'METROPOLIS', 'KIMS', 'RAINBOW', 'SJVN', 'RVNL'
    ],
    'NIFTY CAPITAL GOODS': [
        'LT', 'SIEMENS', 'ABB', 'BEL', 'HAL', 'BHEL', 'CUMMINSIND', 'THERMAX', 'TIINDIA', 'SUZLON',
        'POLYCAB', 'HAVELLS', 'CGPOWER', 'GRINDWELL', 'AIAENG', 'ELGI', 'RVNL', 'KALPATPOWR', 'KEC', 'BHELDL'
    ],
    'NIFTY CEMENT': [
        'ULTRACEMCO', 'SHREECEM', 'AMBUJACEM', 'ACC', 'DALBHARAT', 'RAMCOCEM', 'JKCEMENT', 'JKLAKSHMI',
        'HEIDELBERG', 'BIRLACEM', 'NUVOCO', 'STARTCEMENT', 'PRISMCEMENT', 'SAURASHCEM'
    ],
    'NIFTY CHEMICALS': [
        'SRF', 'PIDILITIND', 'DEEPAKNTR', 'ATUL', 'CLEAN', 'NAVINFL', 'ALKYLAMINE', 'VINATIORGA', 'TATACHEM',
        'COROMANDEL', 'GODREJIND', 'GSFC', 'GNFC', 'FLUOROCHEM', 'NEOGEN', 'BALCHEMICALS', 'SOLARA'
    ],
    'NIFTY COMM SERVICES': [
        'CONCOR', 'IRCTC', 'TIINDIA', 'BLUEDART', 'MAHINDRALOG', 'VRLLOG', 'TCI', 'GATI',
        'INTERGLOBE', 'SPICEJET', 'INDIGO', 'CONTAINER', 'SICAL'
    ],
    'NIFTY CONSTRUCTION': [
        'LT', 'NCC', 'KEC', 'KALPATPOWR', 'GRINFRA', 'IRCON', 'HCC', 'PNCINFRA',
        'JKUMAR', 'CAPACITE', 'AHLUCONT', 'RVNL', 'KNRCON', 'PSP', 'GPPL'
    ],
    'NIFTY CONSUMER DURABLES': [
        'TITAN', 'VOLTAS', 'BLUESTAR', 'CROMPTON', 'HAVELLS', 'VBL', 'AMBER', 'VGUARD',
        'BAJAJCON', 'WHIRLPOOL', 'KAJARIACER', 'CERA', 'DIXON', 'ORIENTELEC', 'SYMPHONY'
    ],
    'NIFTY CONSUMER SERVICES': [
        'DMART', 'JUBLFOOD', 'IRCTC', 'NAUKRI', 'ZOMATO', 'NYKAA', 'INDIAMART', 'DEVYANI',
        'SAPPHIRE', 'BARBEQUE', 'TRENT', 'SHOPERSTOP', 'VMART', 'RELAXO', 'BATA'
    ],
    'NIFTY FINANCIAL SERVICES': [
        'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'BAJFINANCE', 'BAJAJFINSV',
        'PFC', 'RECLTD', 'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN', 'LICHSGFIN', 'HDFCLIFE', 'SBILIFE',
        'ICICIPRULI', 'ICICIGI', 'HDFCAMC', 'SBICARD', 'M&MFIN'
    ],
    'NIFTY FIN SERVICES 25 50': [
        'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'BAJFINANCE', 'SBIN', 'AXISBANK', 'BAJAJFINSV',
        'PFC', 'RECLTD', 'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN', 'LICHSGFIN', 'HDFCLIFE', 'SBILIFE',
        'ICICIPRULI', 'ICICIGI', 'HDFCAMC', 'SBICARD', 'M&MFIN', 'INDUSINDBK', 'IDFCFIRSTB', 'AUBANK', 'FEDERALBNK', 'L&TFH'
    ],
    'NIFTY FIN SERVICES EX BANK': [
        'BAJFINANCE', 'BAJAJFINSV', 'PFC', 'RECLTD', 'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN',
        'LICHSGFIN', 'M&MFIN', 'HDFCLIFE', 'SBILIFE', 'ICICIPRULI', 'ICICIGI', 'HDFCAMC', 'SBICARD', 'L&TFH'
    ],
    'NIFTY HEALTHCARE': [
        'SUNPHARMA', 'CIPLA', 'DRREDDY', 'DIVISLAB', 'LUPIN', 'APOLLOHOSP', 'MAXHEALTH', 'FORTIS',
        'TORNTPHARM', 'ALKEM', 'AUROPHARMA', 'GLENMARK', 'BIOCON', 'LALPATHLAB', 'METROPOLIS'
    ],
    'NIFTY HOSPITALS': [
        'APOLLOHOSP', 'MAXHEALTH', 'FORTIS', 'RAINBOW', 'NARAYANA', 'SHALBY', 'KIMS',
        'HEALTHWORLD', 'MEDANTA', 'YATHARTH', 'VIJAYA'
    ],
    'NIFTY HOUSING FINANCE': [
        'LICHSGFIN', 'PNBHOUSING', 'HOMEFIRST', 'CANFINHOME', 'BAJAJHFL', 'REPCO', 'APTUS', 'HUDCO',
        'SBFC', 'AAVAS', 'AADHAR', 'HFCL'
    ],
    'NIFTY INSURANCE': [
        'SBILIFE', 'HDFCLIFE', 'ICICIPRULI', 'ICICIGI', 'NIACL', 'STARHEALTH', 'GICRE',
        'LICI', 'ABCAPITAL', 'MAXFINSERV'
    ],
    'NIFTY MEDIA': [
        'ZEEL', 'PVRINOX', 'SUNTV', 'NETWORK18', 'DISHTV', 'SAREGAMA', 'TIPS', 'NAZARA',
        'TVTODAY', 'JAGRAN', 'DBCORP', 'INDIGRIDTRUST'
    ],
    'NIFTY NBFC': [
        'BAJFINANCE', 'BAJAJFINSV', 'M&MFIN', 'MUTHOOTFIN', 'CHOLAFIN', 'SHRIRAMFIN',
        'PFC', 'RECLTD', 'L&TFH', 'LICHSGFIN', 'MANAPPURAM', 'IIFL', 'PAISALO', 'POONAWALLA'
    ],
    'NIFTY OIL GAS': [
        'RELIANCE', 'ONGC', 'IOC', 'BPCL', 'HINDPETRO', 'GAIL', 'MGL', 'IGL', 'PETRONET',
        'GSPL', 'GUJGASLTD', 'CASTROLIND', 'CHENNPETRO', 'AEGISCHEM'
    ],
    'NIFTY POWER': [
        'NTPC', 'POWERGRID', 'TATAPOWER', 'ADANIPOWER', 'CESC', 'TORNTPOWER', 'JSWENERGY', 'NHPC',
        'SJVN', 'INOXWIND', 'RPOWER', 'JSPL', 'KPCL', 'MAHAGENCO'
    ],
    'NIFTY PRIVATE BANK': [
        'HDFCBANK', 'ICICIBANK', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'IDFCFIRSTB', 'AUBANK',
        'BANDHANBNK', 'FEDERALBNK', 'YESBANK', 'DCBBANK', 'RBLBANK', 'CSBBANK', 'KARNATAKA'
    ],
    'NIFTY REALTY': [
        'DLF', 'GODREJPROP', 'OBEROIRLTY', 'PRESTIGE', 'BRIGADE', 'MAHLIFE', 'SOBHA',
        'PHOENIXLTD', 'MACROTECH', 'EMBASSY', 'SUNTECK', 'KOLTEPATIL', 'ARIHANTSUPR'
    ],
    'NIFTY REITS INVITS': [
        'EMBASSY', 'MINDSPACE', 'BROOKFIELD', 'NEXUS', 'POWERGRID', 'INDIGRIDTRUST', 'STOVE', 'BHARAT'
    ],
    'NIFTY RETAIL': [
        'DMART', 'TRENT', 'ABFRL', 'TITAN', 'VMART', 'SHOPERSTOP', 'BATA', 'RELAXO',
        'VSTIND', 'PAISALO', 'JUSTDIAL', 'NYKAA', 'MEESHO'
    ],
    'NIFTY TELECOM': [
        'BHARTIARTL', 'INDUSTOWER', 'TATACOMM', 'HCLTECH', 'RAILTEL', 'STERLITE', 'GTLINFRA',
        'ITI', 'TEJAS', 'ROUTE'
    ],
    'NIFTY500 HEALTHCARE': [
        'SUNPHARMA', 'CIPLA', 'DRREDDY', 'DIVISLAB', 'LUPIN', 'APOLLOHOSP', 'MAXHEALTH', 'FORTIS',
        'TORNTPHARM', 'ALKEM', 'AUROPHARMA', 'GLENMARK', 'BIOCON', 'LALPATHLAB', 'METROPOLIS',
        'KIMS', 'RAINBOW', 'NARAYANA', 'SHALBY', 'IPCALAB', 'ASTRAZEN', 'PFIZER', 'ABBOT'
    ],
    'NIFTY MIDSMALL FIN SERVICES': [
        'MUTHOOTFIN', 'CHOLAFIN', 'SHRIRAMFIN', 'M&MFIN', 'L&TFH', 'LICHSGFIN', 'MANAPPURAM',
        'UJJIVANSFB', 'EQUITASBNK', 'AUBANK', 'IDFCFIRSTB', 'FEDERALBNK', 'BANDHANBNK', 'IIFL', 'POONAWALLA'
    ],
    'NIFTY MIDSMALL HEALTHCARE': [
        'LALPATHLAB', 'METROPOLIS', 'KIMS', 'RAINBOW', 'NARAYANA', 'SHALBY', 'STRIDES',
        'GLAND', 'IPCA', 'IPCALAB', 'BLISSGVS', 'SUVEN', 'MEDPLUS', 'VIJAYA', 'YATHARTH'
    ],
    'NIFTY MIDSMALL IT TELECOM': [
        'COFORGE', 'PERSISTENT', 'MPHASIS', 'LTTS', 'CYIENT', 'TATAELXSI', 'KPITTECH',
        'MASTEK', 'SONATSOFTW', 'RATEGAIN', 'BSOFT', 'NEWGEN', 'ROUTE', 'TEJAS', 'RAILTEL'
    ],
    'NIFTY NEXT 50': [
        'ADANIENT', 'AMBUJACEM', 'APOLLOHOSP', 'BAJAJHLDNG', 'BANKBARODA', 'BEL', 'BERGEPAINT', 'BOSCHLTD', 'CANBK', 'CHOLAFIN',
        'CIPLA', 'COLPAL', 'CONCOR', 'DABUR', 'DIVI', 'DLF', 'GICRE', 'HAVELLS', 'HINDPETRO', 'ICICIPRULI',
        'INDHOTEL', 'IRCTC', 'JSWINFRA', 'LODHA', 'MARICO', 'MFSL', 'MPHASIS', 'NAUKRI', 'NHPC', 'OBEROIRLTY',
        'OFSS', 'PAGEIND', 'PFC', 'PIIND', 'PNB', 'RECLTD', 'SAIL', 'SBICARD', 'SHREECEM', 'SIEMENS',
        'SJVN', 'TATACOMM', 'TATACONSUM', 'TATAPOWER', 'TIINDIA', 'TORNTPHARM', 'UBL', 'VBL', 'ZOMATO', 'CAMS'
    ],
    'NIFTY TOTAL MARKET': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS',
        'AMBUJACEM', 'APOLLOHOSP', 'BANKBARODA', 'BEL', 'BERGEPAINT', 'BOSCHLTD', 'CANBK', 'CHOLAFIN', 'DABUR', 'HAVELLS'
    ],
    'NIFTY500 MULTICAP 50 25 25': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'AUBANK', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB', 'ASHOKLEY', 'MRF', 'BALKRISIND',
        'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD', 'JINDALSTEL', 'L&TFH', 'LICHSGFIN'
    ],
    'NIFTY500 LARGEMIDSMALL EW': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'AUBANK', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB', 'ASHOKLEY', 'MRF', 'BALKRISIND',
        'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD', 'JINDALSTEL', 'L&TFH', 'LICHSGFIN'
    ],
    'NIFTY MIDCAP SELECT': [
        'ABCAPITAL', 'AUBANK', 'BALKRISIND', 'BHARATFORG', 'CAMS', 'CROMPTON', 'DELHIVERY', 'FEDERALBNK',
        'GODREJPROP', 'HDFCAMC', 'INDIANB', 'JSWINFRA', 'KALYANKJIL', 'KPITTECH', 'LICHSGFIN', 'LODHA',
        'MFSL', 'PIIND', 'POLYCAB', 'TATACOMM', 'TIINDIA', 'UNIONBANK', 'UPL', 'VOLTAS', 'BSOFT'
    ],
    'NIFTY MIDCAP 100': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB',
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD',
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY', 'APLAPOLLO', 'BATAINDIA', 'COFORGE', 'CROMPTON', 'EXIDEIND',
        'GLENMARK', 'GRANULES', 'INDIAMART', 'IRCTC', 'JKCEMENT', 'JSWINFRA', 'KANSAINER', 'MFSL', 'NMDC', 'PIIND',
        'RBLBANK', 'SHRIRAMFIN', 'SIEMENS', 'TATAELXSI', 'TIINDIA', 'TORNTPHARM', 'UNIONBANK', 'UPL', 'VOLTAS', 'ZOMATO'
    ],
    'NIFTY SMALLCAP 100': [
        'AARTIIND', 'AFFLE', 'AJANTPHARM', 'ANURAS', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA', 'AVANTIFEED', 'CERA',
        'CRAFTSMAN', 'DATAPATTNS', 'DCBBANK', 'EDELWEISS', 'ELGIEQUIP', 'EPL', 'EQUITAS', 'ESABINDIA', 'FIEMIND', 'GESHIP',
        'GREENPANEL', 'GRINDWELL', 'HAPPSTMNDS', 'HAWKINCOOK', 'INGERRAND', 'JINDALSAW', 'JKLAKSHMI', 'KIRLOSENG', 'KRBL', 'LALPATHLAB',
        'LATENTVIEW', 'MUTHOOTFIN', 'NUVAMA', 'NUVOCO', 'PNBHOUSING', 'POONAWALLA', 'POWERINDIA', 'RAMCOIND', 'RATNAMANI', 'REDINGTON',
        'RENUKA', 'SAPPHIRE', 'SHOPERSTOP', 'SKFINDIA', 'SOLARINDS', 'SPANDANA', 'TARSONS', 'THERMAX', 'TRIDENT', 'UFLEX'
    ],
    'NIFTY SMALLCAP 250': [
        'AARTIIND', 'ACRYSIL', 'AEGISLOG', 'AFFLE', 'AJANTPHARM', 'ANURAS', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA',
        'AVANTIFEED', 'CERA', 'CRAFTSMAN', 'DATAPATTNS', 'DCBBANK', 'EDELWEISS', 'ELGIEQUIP', 'EPL', 'EQUITAS', 'ESABINDIA',
        'FIEMIND', 'GESHIP', 'GREENPANEL', 'GRINDWELL', 'HAPPSTMNDS', 'INGERRAND', 'JINDALSAW', 'JKLAKSHMI', 'KIRLOSENG', 'KRBL',
        'LALPATHLAB', 'LATENTVIEW', 'MUTHOOTFIN', 'NUVAMA', 'NUVOCO', 'PNBHOUSING', 'POONAWALLA', 'POWERINDIA', 'RAMCOIND', 'RATNAMANI'
    ],
    'NIFTY SMALLCAP 500': [
        'AARTIIND', 'ACRYSIL', 'AEGISLOG', 'AFFLE', 'AJANTPHARM', 'ANURAS', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA',
        'AVANTIFEED', 'CERA', 'CRAFTSMAN', 'DATAPATTNS', 'DCBBANK', 'EDELWEISS', 'ELGIEQUIP', 'EPL', 'EQUITAS', 'ESABINDIA',
        'FIEMIND', 'GESHIP', 'GREENPANEL', 'GRINDWELL', 'HAPPSTMNDS', 'INGERRAND', 'JINDALSAW', 'JKLAKSHMI', 'KIRLOSENG', 'KRBL',
        'LALPATHLAB', 'LATENTVIEW', 'MUTHOOTFIN', 'NUVAMA', 'NUVOCO', 'PNBHOUSING', 'POONAWALLA', 'POWERINDIA', 'RAMCOIND', 'RATNAMANI',
        'REDINGTON', 'RENUKA', 'SAPPHIRE', 'SHOPERSTOP', 'SKFINDIA', 'SOLARINDS', 'SPANDANA', 'TARSONS', 'THERMAX', 'TRIDENT'
    ],
    'NIFTY SMALLCAP 50': [
        'AARTIIND', 'AFFLE', 'AJANTPHARM', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA', 'AVANTIFEED', 'CERA', 'CRAFTSMAN',
        'DATAPATTNS', 'DCBBANK', 'EDELWEISS', 'ELGIEQUIP', 'EPL', 'EQUITAS', 'ESABINDIA', 'FIEMIND', 'GESHIP', 'GREENPANEL',
        'GRINDWELL', 'HAPPSTMNDS', 'INGERRAND', 'JINDALSAW', 'JKLAKSHMI', 'KIRLOSENG', 'KRBL', 'LALPATHLAB', 'LATENTVIEW', 'MUTHOOTFIN',
        'NUVAMA', 'NUVOCO', 'PNBHOUSING', 'POONAWALLA', 'POWERINDIA', 'RAMCOIND', 'RATNAMANI', 'REDINGTON', 'RENUKA', 'SAPPHIRE',
        'SHOPERSTOP', 'SKFINDIA', 'SOLARINDS', 'SPANDANA', 'TARSONS', 'THERMAX', 'TRIDENT', 'UFLEX', 'VAIBHAVGBL', 'WINDMACH'
    ],
    'NIFTY MICROCAP 250': [
        'ACRYSIL', 'ADFFOODS', 'AKZOINDIA', 'ALEMBICLTD', 'ALLCARGO', 'AMRUTANJAN', 'ANANTRAJ', 'ANDHRAPET', 'ANDREWSY', 'ANMOL',
        'APOLLO', 'ARCHIDPLY', 'ARFIN', 'ARIHANTCAP', 'ARKADE', 'ARMANFIN', 'ARTSON', 'ASAL', 'ASCOM', 'ASHIMASYN',
        'ASTERDM', 'ATAM', 'ATLAS', 'AVTNPL', 'AXISCADES', 'AYMSYNTEX', 'BALAJITELE', 'BALMLAWRIE', 'BANARBEADS', 'BANARISUG'
    ],
    'NIFTY LARGEMIDCAP 250': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'AUBANK', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB', 'ASHOKLEY', 'MRF', 'BALKRISIND',
        'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD', 'JINDALSTEL', 'L&TFH', 'LICHSGFIN'
    ],
    'NIFTY MIDSMALLCAP 400': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB',
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD',
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY', 'APLAPOLLO', 'BATAINDIA', 'COFORGE', 'CROMPTON', 'EXIDEIND',
        'AARTIIND', 'AFFLE', 'AJANTPHARM', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA', 'AVANTIFEED', 'CERA', 'CRAFTSMAN'
    ],
    'NIFTY MIDSMALLCAP 400 50 50': [
        'AUBANK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB', 'VOLTAS', 'CUMMINSIND', 'TATACOMM', 'BHARATFORG', 'HINDPETRO', 'POLYCAB',
        'ASHOKLEY', 'MRF', 'BALKRISIND', 'CONCOR', 'DALBHARAT', 'DEEPAKNTR', 'ESCORTS', 'GLAND', 'GODREJPROP', 'GUJGASLTD',
        'JINDALSTEL', 'L&TFH', 'LICHSGFIN', 'M&MFIN', 'OBEROIRLTY', 'APLAPOLLO', 'BATAINDIA', 'COFORGE', 'CROMPTON', 'EXIDEIND',
        'AARTIIND', 'AFFLE', 'AJANTPHARM', 'APARINDS', 'APOLLOTYRE', 'APTUS', 'ASAHIINDIA', 'AVANTIFEED', 'CERA', 'CRAFTSMAN'
    ],
    'NIFTY INDIA FPI 150': [
        'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'KOTAKBANK', 'SBIN', 'AXISBANK', 'LT', 'ITC',
        'BHARTIARTL', 'BAJFINANCE', 'ASIANPAINT', 'MARUTI', 'TITAN', 'HCLTECH', 'WIPRO', 'SUNPHARMA', 'M&M', 'ADANIENT',
        'ADANIPORTS', 'ULTRACEMCO', 'POWERGRID', 'NTPC', 'INDUSINDBK', 'BAJAJFINSV', 'HINDALCO', 'JSWSTEEL', 'GRASIM', 'TATASTEEL',
        'ONGC', 'TECHM', 'DRREDDY', 'COALINDIA', 'CIPLA', 'BPCL', 'HINDUNILVR', 'BRITANNIA', 'NESTLEIND', 'TATAMOTORS',
        'AMBUJACEM', 'APOLLOHOSP', 'BANKBARODA', 'BEL', 'BERGEPAINT', 'BOSCHLTD', 'CANBK', 'CHOLAFIN', 'DABUR', 'HAVELLS'
    ],
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
    'NIFTY IT':                    'https://archives.nseindia.com/content/indices/ind_niftyitlist.csv',
    'NIFTY 100':                   'https://archives.nseindia.com/content/indices/ind_nifty100list.csv',
    'NIFTY 200':                   'https://archives.nseindia.com/content/indices/ind_nifty200list.csv',
    'NIFTY 500':                   'https://archives.nseindia.com/content/indices/ind_nifty500list.csv',
    'NIFTY MIDCAP 50':             'https://archives.nseindia.com/content/indices/ind_niftymidcap50list.csv',
    'NIFTY CAPITAL GOODS':         'https://archives.nseindia.com/content/indices/ind_niftycapgoods.csv',
    'NIFTY CEMENT':                'https://archives.nseindia.com/content/indices/ind_niftycement.csv',
    'NIFTY CHEMICALS':             'https://archives.nseindia.com/content/indices/ind_niftychemicals.csv',
    'NIFTY COMM SERVICES':         'https://archives.nseindia.com/content/indices/ind_niftycommtranspsvc.csv',
    'NIFTY CONSTRUCTION':          'https://archives.nseindia.com/content/indices/ind_niftyconstruction.csv',
    'NIFTY CONSUMER DURABLES':     'https://archives.nseindia.com/content/indices/ind_niftyconsumerdurables.csv',
    'NIFTY CONSUMER SERVICES':     'https://archives.nseindia.com/content/indices/ind_niftyconsumerservices.csv',
    'NIFTY FINANCIAL SERVICES':    'https://archives.nseindia.com/content/indices/ind_niftyfinancelist.csv',
    'NIFTY FIN SERVICES 25 50':    'https://archives.nseindia.com/content/indices/ind_niftyfinancialservices25_50list.csv',
    'NIFTY FIN SERVICES EX BANK':  'https://archives.nseindia.com/content/indices/ind_niftyfinservexbankindex.csv',
    'NIFTY HEALTHCARE':            'https://archives.nseindia.com/content/indices/ind_niftyhealthcare.csv',
    'NIFTY HOSPITALS':             'https://archives.nseindia.com/content/indices/ind_niftyhospitals.csv',
    'NIFTY HOUSING FINANCE':       'https://archives.nseindia.com/content/indices/ind_niftyhousingfinance.csv',
    'NIFTY INSURANCE':             'https://archives.nseindia.com/content/indices/ind_niftyindiainsurance.csv',
    'NIFTY MEDIA':                 'https://archives.nseindia.com/content/indices/ind_niftymedia.csv',
    'NIFTY NBFC':                  'https://archives.nseindia.com/content/indices/ind_niftynbfc.csv',
    'NIFTY OIL GAS':               'https://archives.nseindia.com/content/indices/ind_niftyoilgas.csv',
    'NIFTY POWER':                 'https://archives.nseindia.com/content/indices/ind_niftypower.csv',
    'NIFTY PRIVATE BANK':          'https://archives.nseindia.com/content/indices/ind_niftypvtbank.csv',
    'NIFTY REALTY':                'https://archives.nseindia.com/content/indices/ind_niftyrealty.csv',
    'NIFTY REITS INVITS':          'https://archives.nseindia.com/content/indices/ind_niftyreitsandinvits.csv',
    'NIFTY RETAIL':                'https://archives.nseindia.com/content/indices/ind_niftyindiaretail.csv',
    'NIFTY TELECOM':               'https://archives.nseindia.com/content/indices/ind_niftytelecommunication.csv',
    'NIFTY500 HEALTHCARE':         'https://archives.nseindia.com/content/indices/ind_nifty500healthcare.csv',
    'NIFTY MIDSMALL FIN SERVICES':   'https://archives.nseindia.com/content/indices/ind_niftymidsmallfinancialservices.csv',
    'NIFTY MIDSMALL HEALTHCARE':     'https://archives.nseindia.com/content/indices/ind_niftymidsmallhealthcare.csv',
    'NIFTY MIDSMALL IT TELECOM':     'https://archives.nseindia.com/content/indices/ind_niftymidsmallitandtelecom.csv',
    'NIFTY NEXT 50':                 'https://archives.nseindia.com/content/indices/ind_niftynext50list.csv',
    'NIFTY TOTAL MARKET':            'https://archives.nseindia.com/content/indices/ind_niftytotalmarketlist.csv',
    'NIFTY500 MULTICAP 50 25 25':    'https://archives.nseindia.com/content/indices/ind_nifty500multicap502525list.csv',
    'NIFTY500 LARGEMIDSMALL EW':     'https://archives.nseindia.com/content/indices/ind_nifty500largemidsmallequalcapweightedlist.csv',
    'NIFTY MIDCAP SELECT':           'https://archives.nseindia.com/content/indices/ind_niftymidcapselect.csv',
    'NIFTY MIDCAP 100':              'https://archives.nseindia.com/content/indices/ind_niftymidcap100list.csv',
    'NIFTY SMALLCAP 100':            'https://archives.nseindia.com/content/indices/ind_niftysmallcap100list.csv',
    'NIFTY SMALLCAP 250':            'https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv',
    'NIFTY SMALLCAP 500':            'https://archives.nseindia.com/content/indices/ind_niftysmallcap500list.csv',
    'NIFTY SMALLCAP 50':             'https://archives.nseindia.com/content/indices/ind_niftysmallcap50list.csv',
    'NIFTY MICROCAP 250':            'https://archives.nseindia.com/content/indices/ind_niftymicrocap250list.csv',
    'NIFTY LARGEMIDCAP 250':         'https://archives.nseindia.com/content/indices/ind_niftylargemidcap250list.csv',
    'NIFTY MIDSMALLCAP 400':         'https://archives.nseindia.com/content/indices/ind_niftymidsmallcap400list.csv',
    'NIFTY MIDSMALLCAP 400 50 50':   'https://archives.nseindia.com/content/indices/ind_niftymidsmallcap4005050list.csv',
    'NIFTY INDIA FPI 150':           'https://archives.nseindia.com/content/indices/ind_niftyindiafpi150list.csv',
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
