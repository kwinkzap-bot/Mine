import os
import logging
from typing import Optional, Any
from flask import has_request_context, session
from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.kite_order_services import apply_kite_proxy

logger = logging.getLogger(__name__)
_fyers_adapter_cache = {}

def get_kite(user: Optional[str] = None, instance: Optional[int] = None) -> Optional[Any]:
    """Get Zerodha Kite instance. Original logic moved from api.py."""
    try:
        from kiteconnect import KiteConnect
        from trading_app.app.utils.token_manager import get_access_token
        from trading_app.app.utils.user_env import UserEnvManager

        username = user
        if not username and has_request_context():
            username = session.get('username')

        if instance is None and has_request_context():
            instance = session.get('instance_num') or 1
        elif instance is None:
            instance = 1

        # Use Mine user if no user specified (for testing/CLI)
        if not username:
            username = 'Mine'

        api_key = UserEnvManager.get_user_var(username, f'BROKER_{instance}_API_KEY')
        access_token = UserEnvManager.get_user_var(username, f'BROKER_{instance}_ACCESS_TOKEN')

        if not api_key or not access_token:
            return None

        # kiteconnect's own default is 7s, which is far too tight for an order
        # POST at 09:15-09:16 — api.kite.trade routinely takes longer than that
        # under market-open load, and a read timeout there loses the entry
        # (see KiteService._safe_place_order for how a timed-out POST is
        # reconciled). Matches KiteService._create_kite_instance.
        kite = KiteConnect(api_key=api_key, timeout=30)
        apply_kite_proxy(kite)
        kite.set_access_token(access_token)
        return kite
    except Exception as e:
        logger.error(f"Error getting Kite instance: {e}")
        return None

def get_data_provider(user: Optional[str] = None) -> Optional[Any]:
    """Returns the configured data provider (Kite or Fyers)."""
    try:
        from trading_app.app.utils.user_env import UserEnvManager
        
        username = user
        if not username and has_request_context():
            username = session.get('username')
        
        if not username:
            provider_type = os.getenv('DATA_PROVIDER', 'KITE').upper().split()[0]
            username = 'Mine' # Fallback for env lookups
        else:
            UserEnvManager._user_env_cache.pop(username, None)
            raw_val = UserEnvManager.get_user_var(username, 'DATA_PROVIDER', 'KITE')
            provider_type = raw_val.upper().split()[0]
        
        if provider_type == 'FYERS':
            instance_num = None
            for i in range(1, 21):
                if UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').lower() == 'fyers':
                    instance_num = i
                    break
            
            if instance_num:
                app_id = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_APP_ID')
                access_token = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_ACCESS_TOKEN')
                secret = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_SECRET_KEY')
                
                if app_id and access_token:
                    cache_key = f"{username}_{app_id}_{access_token[-10:]}"
                    if cache_key in _fyers_adapter_cache:
                        return _fyers_adapter_cache[cache_key]
                    
                    logger.info(f"Initializing FyersDataServiceAdapter for {username} (app_id={app_id})")
                    adapter = FyersDataServiceAdapter(fyers_instance_or_app_id=app_id, access_token=access_token, secret=secret)
                    _fyers_adapter_cache[cache_key] = adapter
                    return adapter
                else:
                    logger.warning(f"Fyers configured but missing app_id or access_token for {username}. Falling back to Kite.")

        # Default fallback to Kite
        return get_kite(user=username, instance=1)
    except Exception as e:
        logger.error(f"Error getting data provider: {e}")
        return None
