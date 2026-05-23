import logging
import threading
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_stop_event = threading.Event()
_monitor_thread = None


def start_monitor():
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name='mine-order-monitor')
    _monitor_thread.start()
    logger.info("[MineOrderMonitor] Started")


def stop_monitor():
    _stop_event.set()
    logger.info("[MineOrderMonitor] Stopped")


def _is_market_hours() -> bool:
    now = datetime.now(_IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 555 <= mins <= 930  # 9:15–15:30


def _monitor_loop():
    while not _stop_event.is_set():
        try:
            if _is_market_hours():
                _check_pending_orders()
        except Exception as e:
            logger.error(f"[MineOrderMonitor] Loop error: {e}", exc_info=True)
        _stop_event.wait(1)


def _check_pending_orders():
    from trading_app.app.utils.mine_order_store import MineOrderStore
    pending = MineOrderStore.get_pending_orders()
    if not pending:
        return

    for order in pending:
        try:
            ltp = _fetch_ltp(order)
            if not ltp:
                continue

            triggered = (order['action'] == 'BUY' and ltp <= order.get('price', 0)) or \
                        (order['action'] == 'SELL' and ltp >= order.get('price', 0))

            logger.debug(
                f"[MineOrderMonitor] {order['symbol']} {order['strike']} {order['option_type']} "
                f"LTP={ltp} limit={order.get('price')} action={order['action']}"
            )

            if triggered:
                _execute_order(order)
        except Exception as e:
            logger.error(f"[MineOrderMonitor] Error checking order {order.get('id')}: {e}")


def _fetch_ltp(order: dict):
    try:
        from trading_app.service.provider_logic import get_kite, get_data_provider
        from trading_app.service.kite_order_services import KiteService
        from trading_app.service.fyers_data_service import FyersDataServiceAdapter

        kite = get_kite(instance=1)
        data_provider = get_data_provider()
        effective = data_provider if data_provider else kite
        if not effective:
            return None

        kite_service = KiteService(kite_instance=effective)
        is_fyers = isinstance(data_provider, FyersDataServiceAdapter)

        from trading_app.app.routes.api import _get_cached_strike_token
        token, opt_sym = _get_cached_strike_token(
            kite_service, data_provider, is_fyers,
            order['symbol'], int(order['strike']), order['option_type']
        )
        if not opt_sym:
            return None

        if is_fyers:
            ltp_data = data_provider.ltp([opt_sym])
            return float(ltp_data.get(opt_sym, {}).get('last_price', 0) or 0)
        else:
            key = f'NFO:{opt_sym}'
            ltp_data = effective.ltp([key])
            return float(ltp_data.get(key, {}).get('last_price', 0) or 0)
    except Exception as e:
        logger.error(f"[MineOrderMonitor] LTP fetch error: {e}")
        return None


def _execute_order(order: dict):
    from trading_app.app.utils.mine_order_store import MineOrderStore
    MineOrderStore.update_order(order['id'], {'status': 'EXECUTING'})
    logger.info(
        f"[MineOrderMonitor] Executing {order['id']} "
        f"{order['symbol']} {order['strike']} {order['option_type']} {order['action']}"
    )

    try:
        from trading_app.app.routes.api import _dispatch_order_to_brokers
        result = _dispatch_order_to_brokers(
            symbol=order['symbol'],
            strike=int(order['strike']),
            option_type=order['option_type'],
            action=order['action'],
            strategy=order.get('strategy', 'intrinsic'),
            username=order.get('username', 'Mine'),
            session_data={},
            limit_price=None,
        )

        if result.get('success'):
            MineOrderStore.update_order(order['id'], {
                'status': 'EXECUTED',
                'executed_at': int(time.time() * 1000),
                'broker_order_ids': result.get('summary', []),
            })
            logger.info(f"[MineOrderMonitor] Order {order['id']} executed successfully")
        else:
            MineOrderStore.update_order(order['id'], {
                'status': 'REJECTED',
                'error': result.get('error', 'Unknown error'),
            })
            logger.warning(f"[MineOrderMonitor] Order {order['id']} rejected: {result.get('error')}")
    except Exception as e:
        logger.error(f"[MineOrderMonitor] Execute error for {order['id']}: {e}", exc_info=True)
        MineOrderStore.update_order(order['id'], {'status': 'REJECTED', 'error': str(e)})
