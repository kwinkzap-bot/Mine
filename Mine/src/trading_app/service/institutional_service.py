import requests
import logging
import threading
import json
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

class InstitutionalService:
    """Service to fetch FII/DII institutional activity data."""
    
    _cache = {}
    _cache_lock = threading.Lock()
    _CACHE_TTL = 3600  # 1 hour
    
    @classmethod
    def get_latest_data(cls) -> Dict[str, Any]:
        """Get the latest FII/DII data, using cache if available."""
        with cls._cache_lock:
            if 'data' in cls._cache:
                ts = cls._cache.get('timestamp', 0)
                if datetime.now().timestamp() - ts < cls._CACHE_TTL:
                    return cls._cache['data']
        
        # Try fetching from NiftyTrader first (more up-to-date and robust)
        data = cls._fetch_from_niftytrader()
        
        if not data:
            logger.info("NiftyTrader fetch returned None or failed. Falling back to Moneycontrol.")
            data = cls._fetch_from_moneycontrol()
        
        with cls._cache_lock:
            cls._cache['data'] = data
            cls._cache['timestamp'] = datetime.now().timestamp()
            
        return data

    @classmethod
    def _fetch_from_niftytrader(cls) -> Dict[str, Any]:
        """Scrape FII/DII data (Cash & F&O) from NiftyTrader Next.js JSON payload."""
        url = "https://niftytrader.in/fii-dii-data"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch from NiftyTrader: Status {resp.status_code}")
                return None
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            
            if not script or not script.string:
                logger.error("Could not find __NEXT_DATA__ script on NiftyTrader page")
                return None
                
            json_data = json.loads(script.string)
            props = json_data.get('props', {}).get('pageProps', {})
            
            table_data_container = props.get('initialTableData', {})
            fii_dii_list = table_data_container.get('data', {}).get('fii_dii_data', [])
            
            if not fii_dii_list:
                logger.warning("FII/DII list empty in NiftyTrader data")
                return None
                
            # Latest cash entry
            latest_cash = fii_dii_list[0]
            created_at = latest_cash.get('created_at', '')
            if not created_at:
                logger.warning("No created_at field in NiftyTrader cash data")
                return None
                
            # Format date like Moneycontrol: e.g. "Mon 18 May, 2026"
            try:
                date_obj = datetime.strptime(created_at[:10], '%Y-%m-%d')
                formatted_date = date_obj.strftime('%a %d %b, %Y')
            except Exception as ex:
                logger.warning(f"Error parsing date {created_at}: {ex}")
                formatted_date = datetime.now().strftime('%a %d %b, %Y')
                
            # Helper to parse clean floats/ints
            def parse_val(v):
                if not v or v == '-': return 0.0
                try:
                    return float(str(v).replace(',', '').strip())
                except ValueError:
                    return 0.0
            
            # Get F&O data
            fno_list = props.get('initialFnoData', {}).get('data', [])
            fii_idx_fut = 0.0
            fii_idx_opt = 0.0
            fii_stk_fut = 0.0
            fii_stk_opt = 0.0
            
            for item in fno_list:
                if item.get('reporting_date') == created_at:
                    t = item.get('type', '').upper()
                    val = parse_val(item.get('net_amount', 0.0))
                    if t == 'INDEX FUTURES':
                        fii_idx_fut = val
                    elif t == 'INDEX OPTIONS':
                        fii_idx_opt = val
                    elif t == 'STOCK FUTURES':
                        fii_stk_fut = val
                    elif t == 'STOCK OPTIONS':
                        fii_stk_opt = val
                        
            return {
                'date': formatted_date,
                'data': {
                    'fii-cash': parse_val(latest_cash.get('fii_net_value', 0.0)),
                    'dii-cash': parse_val(latest_cash.get('dii_net_value', 0.0)),
                    'fii-idx-fut': fii_idx_fut,
                    'fii-idx-opt': fii_idx_opt,
                    'fii-stk-fut': fii_stk_fut,
                    'fii-stk-opt': fii_stk_opt
                }
            }
            
        except Exception as e:
            logger.error(f"Error extracting FII/DII from NiftyTrader: {e}", exc_info=True)
            return None

    @classmethod
    def _fetch_from_moneycontrol(cls) -> Dict[str, Any]:
        """Scrape FII/DII data (Cash & F&O) from Moneycontrol Next.js JSON payload."""
        url = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        default_data = {
            'date': datetime.now().strftime('%a, %d %b %Y'),
            'data': {
                'fii-cash': 0.0,
                'dii-cash': 0.0,
                'fii-idx-fut': 0.0,
                'fii-idx-opt': 0.0,
                'fii-stk-fut': 0.0,
                'fii-stk-opt': 0.0
            }
        }
        
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch from Moneycontrol: Status {resp.status_code}")
                return default_data
                
            from bs4 import BeautifulSoup
            import json
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            
            if not script or not script.string:
                logger.error("Could not find __NEXT_DATA__ script on Moneycontrol page")
                return default_data
                
            json_data = json.loads(script.string)
            fii_data_container = json_data.get('props', {}).get('pageProps', {}).get('FiiDiiData', {})
            fii_dii_list = fii_data_container.get('fiiDiiData', fii_data_container.get('list', []))
            
            if not fii_dii_list:
                logger.warning("FII/DII list empty in Moneycontrol data")
                return default_data
                
            # Get latest entry
            latest = fii_dii_list[0]
            
            def parse_val(v):
                if not v or v == '-': return 0.0
                try:
                    return float(str(v).replace(',', '').strip())
                except ValueError:
                    return 0.0
                    
            return {
                'date': latest.get('fDate', default_data['date']),
                'data': {
                    'fii-cash': parse_val(latest.get('fiiCM')),
                    'dii-cash': parse_val(latest.get('diiCM')),
                    'fii-idx-fut': parse_val(latest.get('fiiIdxFut')),
                    'fii-idx-opt': parse_val(latest.get('fiiIdxOpt')),
                    'fii-stk-fut': parse_val(latest.get('fiiStkFut')),
                    'fii-stk-opt': parse_val(latest.get('fiiStkOpt'))
                }
            }
            
        except Exception as e:
            logger.error(f"Error extracting FII/DII from Moneycontrol: {e}", exc_info=True)
            return default_data
