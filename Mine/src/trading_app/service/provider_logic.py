import os
import logging
from typing import Optional, Any, Sequence, Union
from flask import has_request_context, session
from trading_app.service.fyers_data_service import FyersDataServiceAdapter
from trading_app.service.kite_order_services import apply_kite_proxy

logger = logging.getLogger(__name__)
_fyers_adapter_cache = {}
_icici_adapter_cache = {}

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

def get_data_provider(user: Optional[str] = None,
                     context: Optional[Union[str, Sequence[str]]] = None) -> Optional[Any]:
    """Returns the configured data provider (Kite, Fyers or ICICI Direct).

    `context` (e.g. 'replay', 'backtest', 'algo_rtp') looks up a
    `{CONTEXT}_DATA_PROVIDER` override first (e.g. REPLAY_DATA_PROVIDER,
    ALGO_RTP_DATA_PROVIDER) — one per route/algo that needs to sit on a
    different broker than the rest of the app. If that override is unset, or
    its broker isn't reachable (not logged in / no adapter), resolution falls
    through to the global DATA_PROVIDER, then to Kite. Callers that pass no
    context (the majority — login status, orders, positions, scanners, etc.)
    keep using DATA_PROVIDER only, unchanged from before this existed.

    A SEQUENCE of contexts is tried most-specific-first, so one block of a page
    can sit on its own broker while the rest of that page keeps the page-wide
    flag: ('replay_round_strike', 'replay') reads
    REPLAY_ROUND_STRIKE_DATA_PROVIDER, then REPLAY_DATA_PROVIDER, then
    DATA_PROVIDER. An unset override is skipped, not treated as a dead end.
    """
    try:
        from trading_app.app.utils.user_env import UserEnvManager

        username = user
        if not username and has_request_context():
            username = session.get('username')

        if not username:
            username = 'Mine'  # Fallback for env lookups
            def _read(name: str, default: str = '') -> str:
                return os.getenv(name, default)
        else:
            UserEnvManager._user_env_cache.pop(username, None)
            def _read(name: str, default: str = '') -> str:
                return UserEnvManager.get_user_var(username, name, default)

        contexts = [context] if isinstance(context, str) else list(context or ())
        candidates = [_read(f'{c.upper()}_DATA_PROVIDER') for c in contexts if c]
        candidates.append(_read('DATA_PROVIDER', 'KITE'))

        tried = set()
        for raw_val in candidates:
            if not raw_val:
                continue
            provider_type = raw_val.upper().split()[0]
            if provider_type in tried:
                continue
            tried.add(provider_type)

            if provider_type in ('ICICI', 'BREEZE', 'ICICIDIRECT'):
                adapter = _get_icici_adapter(username)
                if adapter is not None:
                    return adapter
                # Fyers before Kite: Kite has no historical-data subscription
                # on any of the configured apps, so falling straight through
                # to it leaves every chart and CPR calculation with nothing.
                adapter = _get_fyers_adapter(username)
                if adapter is not None:
                    logger.warning(f"ICICI unavailable for {username} — serving Fyers instead.")
                    return adapter
                logger.warning(f"ICICI and Fyers both unavailable for {username}.")
                continue

            if provider_type == 'FYERS':
                adapter = _get_fyers_adapter(username)
                if adapter is not None:
                    return adapter
                continue

            if provider_type == 'KITE':
                kite = get_kite(user=username, instance=1)
                if kite is not None:
                    return kite
                continue

        # Nothing in the chain resolved — final hardcoded fallback.
        return get_kite(user=username, instance=1)
    except Exception as e:
        logger.error(f"Error getting data provider: {e}")
        return None


def get_icici_adapter(user: Optional[str] = None) -> Optional[Any]:
    """The Breeze adapter specifically, whatever DATA_PROVIDER happens to be.

    Unlike get_data_provider this never falls back to another broker, because
    the callers are asking for something only Breeze can do: history for an
    already-EXPIRED contract, which it will serve because it addresses a
    contract by (stock_code, expiry, strike, right) rather than by an
    instrument token. Kite and Fyers both resolve through masters that drop
    expired rows, so a fallback here would silently answer a past-expiry
    request with the wrong contract or with nothing.
    """
    username = user
    if not username and has_request_context():
        username = session.get('username')
    return _get_icici_adapter(username or 'Mine')


def _get_icici_adapter(username: str) -> Optional[Any]:
    """Build (and cache) the Breeze adapter for a user, or None if unconfigured.

    Cached on the session token so the daily login swapping it in produces a
    fresh adapter rather than one still holding yesterday's dead session.
    """
    from trading_app.app.utils.user_env import UserEnvManager

    instance_num = None
    for i in range(1, 21):
        if UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').strip().lower() in ('icici', 'icicidirect', 'breeze'):
            instance_num = i
            break
    if not instance_num:
        logger.warning(f"No ICICI broker instance (BROKER_N_TYPE=icici) configured for {username}")
        return None

    api_key = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_API_KEY')
    api_secret = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_SECRET_KEY')
    session_token = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_SESSION_TOKEN')

    if not api_key or not api_secret or not session_token:
        return None

    cache_key = f"{username}_{api_key}_{session_token}"
    if cache_key in _icici_adapter_cache:
        return _icici_adapter_cache[cache_key]

    from trading_app.service.icici_data_service import IciciDataServiceAdapter
    logger.info(f"Initializing IciciDataServiceAdapter for {username} (instance {instance_num})")
    adapter = IciciDataServiceAdapter(api_key=api_key, api_secret=api_secret,
                                      session_token=session_token)
    if not adapter.session_ok:
        # No SDK, or the daily token is dead. Returning it anyway would answer
        # every fetch with [], which reads downstream as "no candles" rather
        # than "wrong provider" — that is how a missing breeze-connect took out
        # the CPR-width endpoint instead of quietly falling back.
        logger.warning(f"ICICI session not live for {username} — not using it as the data provider.")
        return None
    # One stale entry per rotated token would otherwise pin a dead Breeze
    # session in memory for the life of the process.
    for stale in [k for k in _icici_adapter_cache if k.startswith(f"{username}_")]:
        _icici_adapter_cache.pop(stale, None)
    _icici_adapter_cache[cache_key] = adapter
    return adapter


def _get_fyers_adapter(username: str) -> Optional[Any]:
    """Build (and cache) the Fyers adapter for a user, or None if unconfigured."""
    from trading_app.app.utils.user_env import UserEnvManager

    instance_num = None
    for i in range(1, 21):
        if UserEnvManager.get_user_var(username, f'BROKER_{i}_TYPE', '').lower() == 'fyers':
            instance_num = i
            break
    if not instance_num:
        return None

    app_id = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_APP_ID')
    access_token = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_ACCESS_TOKEN')
    secret = UserEnvManager.get_user_var(username, f'BROKER_{instance_num}_SECRET_KEY')

    if not app_id or not access_token:
        logger.warning(f"Fyers configured but missing app_id or access_token for {username}.")
        return None

    cache_key = f"{username}_{app_id}_{access_token[-10:]}"
    if cache_key in _fyers_adapter_cache:
        return _fyers_adapter_cache[cache_key]

    logger.info(f"Initializing FyersDataServiceAdapter for {username} (app_id={app_id})")
    adapter = FyersDataServiceAdapter(fyers_instance_or_app_id=app_id,
                                      access_token=access_token, secret=secret)
    _fyers_adapter_cache[cache_key] = adapter
    return adapter
