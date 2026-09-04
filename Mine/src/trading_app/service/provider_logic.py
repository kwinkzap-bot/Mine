import os
import logging
from typing import Optional, Any
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

def _resolve_username(user: Optional[str] = None) -> str:
    """The user whose broker credentials a provider lookup should use."""
    if user:
        return user
    if has_request_context():
        return session.get('username') or ''
    return ''


def _provider_flag(var: str, username: str, default: str = '') -> str:
    """Read a provider-selection env flag (DATA_PROVIDER, CHART_DATA_PROVIDER).

    Returns '' when the flag is absent or blank, so a caller can tell "not set"
    from "set to something" and chain its own default. The value is taken up to
    the first space: these lines carry a trailing `# KITE, FYERS or ICICI`
    comment in env/Mine.env.
    """
    from trading_app.app.utils.user_env import UserEnvManager

    if username:
        UserEnvManager._user_env_cache.pop(username, None)
        raw = UserEnvManager.get_user_var(username, var, default)
    else:
        raw = os.getenv(var, default)
    parts = str(raw or '').upper().split()
    return parts[0] if parts else ''


def _provider_by_type(provider_type: str, username: str) -> Optional[Any]:
    """Build the adapter a provider flag names, with the standing fallbacks."""
    if provider_type in ('ICICI', 'BREEZE', 'ICICIDIRECT'):
        adapter = _get_icici_adapter(username)
        if adapter is not None:
            return adapter
        # Fyers before Kite: Kite has no historical-data subscription on
        # any of the configured apps, so falling straight through to it
        # leaves every chart and CPR calculation with nothing.
        adapter = _get_fyers_adapter(username)
        if adapter is not None:
            logger.warning(f"ICICI unavailable for {username} — serving Fyers instead.")
            return adapter
        logger.warning(f"ICICI and Fyers both unavailable for {username}. Falling back to Kite.")

    if provider_type == 'FYERS':
        adapter = _get_fyers_adapter(username)
        if adapter is not None:
            return adapter

    # Default fallback to Kite
    return get_kite(user=username, instance=1)


def get_data_provider(user: Optional[str] = None) -> Optional[Any]:
    """Returns the configured data provider (Kite, Fyers or ICICI Direct)."""
    try:
        username = _resolve_username(user)
        provider_type = _provider_flag('DATA_PROVIDER', username, 'KITE') or 'KITE'
        return _provider_by_type(provider_type, username or 'Mine')
    except Exception as e:
        logger.error(f"Error getting data provider: {e}")
        return None


def get_oi_chain_provider(user: Optional[str] = None) -> Optional[Any]:
    """The ICICI Direct (Breeze) adapter, whatever the provider flags say.

    The OI Profile and Replay pages read their option-chain numbers — OI, the
    OI change, PCR, ATM and max pain — off Breeze, while every other feed on
    those pages (candles, quotes, futures volume, CPR, VWAP) follows
    CHART_DATA_PROVIDER. The chain is the one number set worth a second broker
    session: Breeze answers it as a whole-expiry ladder in two calls. Nothing
    else there is better on Breeze, and its 100 req/min budget could not carry
    the 1 Hz candle polling those pages do anyway — which is why this one is
    fixed rather than flag-driven.

    Returns None when ICICI is unconfigured or its daily session is dead, so
    each caller falls back to the source it used before rather than losing the
    data outright.
    """
    try:
        return _get_icici_adapter(_resolve_username(user) or 'Mine')
    except Exception as e:
        logger.error(f"Error getting ICICI OI-chain provider: {e}")
        return None


def get_chart_provider(user: Optional[str] = None, route: Optional[str] = None) -> Optional[Any]:
    """The provider CHART_DATA_PROVIDER names, for everything on OI Profile and
    Replay that is NOT the OI chain — candles, quotes, futures volume, CPR,
    VWAP, strike tokens.

    Its own flag rather than DATA_PROVIDER so those two pages can sit on a
    different broker from the rest of the app: their charts poll at 1 Hz, which
    Breeze's 100 req/min budget cannot carry, while the chain above wants
    Breeze precisely. Unset, it IS DATA_PROVIDER — the split costs nothing
    until you ask for it.

    `route='replay'` checks REPLAY_CHART_DATA_PROVIDER first — Replay wants its
    own broker (typically ICICI, for historical accuracy) independent of what
    the live OI Profile chart is set to, even though both pages otherwise share
    this function and CHART_DATA_PROVIDER. Unset, Replay falls through to the
    same CHART_DATA_PROVIDER/DATA_PROVIDER chain as OI Profile.

    Same fallbacks as get_data_provider: a named broker that has no live
    session degrades to the next usable one rather than leaving the page blank.
    """
    try:
        username = _resolve_username(user)
        provider_type = ''
        if route:
            provider_type = _provider_flag(f'{route.strip().upper()}_CHART_DATA_PROVIDER', username)
        provider_type = (provider_type
                         or _provider_flag('CHART_DATA_PROVIDER', username)
                         or _provider_flag('DATA_PROVIDER', username, 'KITE')
                         or 'KITE')
        return _provider_by_type(provider_type, username or 'Mine')
    except Exception as e:
        logger.error(f"Error getting chart data provider: {e}")
        return None


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
