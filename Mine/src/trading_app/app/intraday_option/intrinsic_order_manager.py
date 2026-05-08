import logging
import threading
import time

logger = logging.getLogger(__name__)

class IntrinsicOrderManager:
    @staticmethod
    def start_monitoring(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data):
        thread = threading.Thread(
            target=IntrinsicOrderManager._monitor_trade,
            args=(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data),
            daemon=True
        )
        thread.start()

    @staticmethod
    def _monitor_trade(broker_type, instance_id, symbol, strike, option_type, entry_price, sl_orders, lot_size, order_lots, sec_id, kite_opt_sym, username, session_data):
        from trading_app.service.provider_logic import get_kite
        from trading_app.app.utils.user_env import UserEnvManager

        kite = get_kite(user=username, instance=1)
        if not kite:
            logger.error("[Intrinsic Monitor] Kite not available")
            return

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

        # Try to get service once to ensure it works
        service = get_service()
        if not service:
            logger.error("[Intrinsic Monitor] Failed to initialize broker service")
            return

        while not all(targets_reached):
            time.sleep(1)
            try:
                ltp_data = kite.ltp([f'NSE:{kite_opt_sym}'])
                ltp = ltp_data.get(f'NSE:{kite_opt_sym}', {}).get('last_price', 0)
                if ltp == 0:
                    continue

                for i, target_price in enumerate(target_prices):
                    if not targets_reached[i] and ltp >= target_price:
                        targets_reached[i] = True
                        logger.info(f"[Intrinsic Monitor] Target {i+1} reached at {ltp} for {kite_opt_sym}")
                        
                        # Re-init service just in case token refreshed or connection dropped
                        service = get_service()

                        # 1. Cancel SL Order
                        if i < len(sl_orders) and sl_orders[i]:
                            sl_order_id = sl_orders[i]
                            logger.info(f"[Intrinsic Monitor] Cancelling SL order {sl_order_id}")
                            try:
                                if hasattr(service, 'cancel_order'):
                                    service.cancel_order(sl_order_id)
                                elif broker_type == 'kite' or broker_type.startswith('zerodha_'):
                                    service.kite.cancel_order(variety='regular', order_id=sl_order_id)
                            except Exception as e:
                                logger.error(f"[Intrinsic Monitor] Failed to cancel SL {sl_order_id}: {e}")

                        # 2. Place Market SELL Order
                        try:
                            if broker_type == 'kite' or broker_type.startswith('zerodha_'):
                                service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type=service.kite.TRANSACTION_TYPE_SELL, quantity=qty_per_target)
                            elif broker_type == 'kotak_neo':
                                service.place_option_order(symbol=symbol, strike=strike, option_type=option_type, transaction_type='SELL', quantity=qty_per_target)
                            elif broker_type == 'dhan':
                                service.place_order(security_id=sec_id, transaction_type='SELL', quantity=qty_per_target, order_type='MARKET', product_type='INTRADAY', exchange_segment='NSE_FNO')
                            elif broker_type == 'fyers':
                                service.place_order(symbol=f'NSE:{kite_opt_sym}', side=-1, quantity=qty_per_target, order_type=2, product_type='INTRADAY')
                        except Exception as e:
                            logger.error(f"[Intrinsic Monitor] Failed to place target EXIT order: {e}")
            except Exception as e:
                logger.error(f"[Intrinsic Monitor] Loop error: {e}")
                time.sleep(5)
