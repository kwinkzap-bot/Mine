import logging
import threading
import time

logger = logging.getLogger(__name__)

_active_monitors: dict = {}  # key → threading.Event

class IntrinsicOrderManager:
    @staticmethod
    def start_monitoring(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data):
        stop_event = threading.Event()
        key = f"{username}_{broker_type}_{instance_id}_{symbol}_{strike}_{option_type}"
        _active_monitors[key] = stop_event
        thread = threading.Thread(
            target=IntrinsicOrderManager._monitor_trade,
            args=(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data, stop_event, key),
            daemon=True
        )
        thread.start()

    @classmethod
    def stop_all_for_user(cls, username):
        prefix = f"{username}_"
        keys = [k for k in list(_active_monitors.keys()) if k.startswith(prefix)]
        for k in keys:
            ev = _active_monitors.pop(k, None)
            if ev:
                ev.set()
        logger.info(f"[Intrinsic Monitor] Stopped {len(keys)} monitor(s) for user '{username}'")

    @staticmethod
    def _monitor_trade(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data, stop_event, key):
        from trading_app.service.provider_logic import get_kite, get_data_provider
        from trading_app.app.utils.user_env import UserEnvManager

        # Market-data source respects the user's configured DATA_PROVIDER (Kite or Fyers).
        # Forcing Kite instance 1 here fails with "Insufficient permission for that call"
        # when the Zerodha app has no quote-data subscription (e.g. DATA_PROVIDER=FYERS).
        data_provider = get_data_provider(user=username)
        if not data_provider:
            logger.error("[Intrinsic Monitor] Data provider not available")
            _active_monitors.pop(key, None)
            return
        # KiteConnect exposes set_access_token; the Fyers adapter does not — used to
        # gate the Kite-only proxy re-apply on connection errors below.
        _is_kite_provider = hasattr(data_provider, 'set_access_token')

        targets_reached = [False]
        target_prices = [entry_price + 10]
        qty_per_target = order_lots * lot_size

        logger.info(f"[Intrinsic Monitor] Started for {broker_type}_{instance_id} {kite_opt_sym} Entry: {entry_price}")

        def get_service():
            if broker_type == 'kite' or broker_type.startswith('zerodha_'):
                z_inst = 1 if broker_type == 'kite' else int(broker_type.split('_')[1])
                k = get_kite(user=username, instance=z_inst)
                from trading_app.service.kite_order_services import KiteService
                return KiteService(kite_instance=k)
            elif broker_type == 'kotak_neo':
                from trading_app.service.kotak_order_services import KotakOrderService
                trading_token = session_data.get(f'kotak_{instance_id}_trading_token') or UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_TRADING_TOKEN')
                trading_sid = session_data.get(f'kotak_{instance_id}_trading_sid') or UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_TRADING_SID')
                base_url = session_data.get(f'kotak_{instance_id}_base_url') or UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_BASE_URL')
                s = KotakOrderService(access_token=trading_token)
                s.trading_token = trading_token
                s.trading_sid = trading_sid
                s.base_url = base_url
                s.inject_trading_tokens()
                return s
            elif broker_type == 'dhan':
                from trading_app.service.dhan_order_services import DhanOrderService
                access_token = session_data.get(f'dhan_{instance_id}_access_token') or UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_ACCESS_TOKEN')
                client_id = UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_CLIENT_ID')
                return DhanOrderService(access_token=access_token, client_id=client_id)
            elif broker_type == 'fyers':
                from trading_app.service.fyers_order_services import FyersOrderService
                access_token = session_data.get(f'fyers_{instance_id}_access_token') or UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_ACCESS_TOKEN')
                app_id = UserEnvManager.get_user_var(username, f'BROKER_{instance_id}_APP_ID')
                return FyersOrderService(app_id=app_id, access_token=access_token)
            return None

        service = get_service()
        if not service:
            logger.error("[Intrinsic Monitor] Failed to initialize broker service")
            _active_monitors.pop(key, None)
            return

        try:
            while not all(targets_reached) and not stop_event.is_set():
                time.sleep(1)
                if stop_event.is_set():
                    break
                try:
                    prefix = 'BSE' if symbol.upper() == 'SENSEX' else 'NSE'
                    ltp_data = data_provider.ltp([f'{prefix}:{kite_opt_sym}'])
                    ltp = ltp_data.get(f'{prefix}:{kite_opt_sym}', {}).get('last_price', 0)
                    if ltp == 0:
                        continue

                    for i, target_price in enumerate(target_prices):
                        if not targets_reached[i] and ltp >= target_price:
                            targets_reached[i] = True
                            logger.info(f"[Intrinsic Monitor] Target {i+1} reached at {ltp} for {kite_opt_sym}")

                            service = get_service()

                            # 1. Cancel SL Order
                            if i < len(sl_orders) and sl_orders[i]:
                                sl_order_id = sl_orders[i]
                                logger.info(f"[Intrinsic Monitor] Cancelling SL order {sl_order_id}")
                                try:
                                    if hasattr(service, 'cancel_order'):
                                        service.cancel_order(sl_order_id)  # type: ignore[union-attr]
                                    elif broker_type == 'kite' or broker_type.startswith('zerodha_'):
                                        service.kite.cancel_order(variety='regular', order_id=sl_order_id)  # type: ignore[union-attr]
                                except Exception as e:
                                    logger.error(f"[Intrinsic Monitor] Failed to cancel SL {sl_order_id}: {e}")

                            # 2. Place Market SELL Order (split by freeze limit)
                            try:
                                from trading_app.app.routes.api import split_quantity_by_freeze_limit
                                qty_chunks = split_quantity_by_freeze_limit(kite_opt_sym, qty_per_target, service)
                                logger.info(f"[Intrinsic Monitor] Placing exit for {kite_opt_sym}: qty {qty_per_target} → chunks {qty_chunks}")

                                for chunk_qty in qty_chunks:
                                    if broker_type == 'kite' or broker_type.startswith('zerodha_'):
                                        service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type=service.kite.TRANSACTION_TYPE_SELL, quantity=chunk_qty)  # type: ignore[union-attr]
                                    elif broker_type == 'kotak_neo':
                                        service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type='SELL', quantity=chunk_qty)  # type: ignore[union-attr]
                                    elif broker_type == 'dhan':
                                        exchange_seg = 'BSE_FNO' if symbol.upper() == 'SENSEX' else 'NSE_FNO'
                                        service.place_order(security_id=sec_id, transaction_type='SELL', quantity=chunk_qty, order_type='MARKET', product_type='INTRADAY', exchange_segment=exchange_seg)  # type: ignore[union-attr]
                                    elif broker_type == 'fyers':
                                        fyers_prefix = 'BSE' if symbol.upper() == 'SENSEX' else 'NSE'
                                        service.place_order(symbol=f'{fyers_prefix}:{kite_opt_sym}', side=-1, quantity=chunk_qty, order_type=2, product_type='INTRADAY')  # type: ignore[union-attr]
                            except Exception as e:
                                logger.error(f"[Intrinsic Monitor] Failed to place target EXIT order: {e}")
                except Exception as e:
                    logger.error(f"[Intrinsic Monitor] Loop error: {e}")
                    _err = str(e).lower()
                    if _is_kite_provider and any(k in _err for k in ('connection refused', 'connectionerror', 'proxyerror', 'newconnectionerror')):
                        logger.warning("[Intrinsic Monitor] Proxy connection error — re-applying static IP proxy...")
                        try:
                            from trading_app.service.kite_order_services import apply_kite_proxy
                            apply_kite_proxy(data_provider)
                            logger.info("[Intrinsic Monitor] Proxy re-applied, resuming monitoring")
                        except Exception as _te:
                            logger.error(f"[Intrinsic Monitor] Proxy re-apply exception: {_te}")
                        time.sleep(10)
                    else:
                        time.sleep(5)
        finally:
            _active_monitors.pop(key, None)
            if stop_event.is_set():
                logger.info(f"[Intrinsic Monitor] Stopped by external signal for key: {key}")
            else:
                logger.info(f"[Intrinsic Monitor] Completed naturally for key: {key}")
