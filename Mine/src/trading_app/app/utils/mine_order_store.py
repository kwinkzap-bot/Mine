import json
import os
import uuid
import threading
import time
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

_STORAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mine_orders.json')
_lock = threading.Lock()
_IST = timezone(timedelta(hours=5, minutes=30))


class MineOrderStore:
    @staticmethod
    def _load() -> list:
        if not os.path.exists(_STORAGE_FILE):
            return []
        try:
            with open(_STORAGE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[MineOrderStore] Load error: {e}")
            return []

    @staticmethod
    def _save(orders: list):
        try:
            with open(_STORAGE_FILE, 'w') as f:
                json.dump(orders, f, indent=2)
        except Exception as e:
            logger.error(f"[MineOrderStore] Save error: {e}")

    @staticmethod
    def add_order(order_data: dict) -> dict:
        with _lock:
            orders = MineOrderStore._load()
            record = {**order_data, 'id': str(uuid.uuid4()), 'created_at': int(time.time() * 1000)}
            orders.append(record)
            MineOrderStore._save(orders)
            return record

    @staticmethod
    def update_order(order_id: str, updates: dict):
        with _lock:
            orders = MineOrderStore._load()
            for o in orders:
                if o['id'] == order_id:
                    o.update(updates)
                    break
            MineOrderStore._save(orders)

    @staticmethod
    def get_pending_orders() -> list:
        with _lock:
            orders = MineOrderStore._load()
            return [o for o in orders if o.get('status') == 'PENDING']

    @staticmethod
    def get_today_orders() -> list:
        today_start = MineOrderStore._today_start_ms()
        with _lock:
            orders = MineOrderStore._load()
            return [o for o in orders if o.get('created_at', 0) >= today_start]

    @staticmethod
    def get_all_orders() -> list:
        cutoff = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
        with _lock:
            orders = MineOrderStore._load()
            return [o for o in orders if o.get('created_at', 0) >= cutoff]

    @staticmethod
    def cancel_order(order_id: str) -> bool:
        with _lock:
            orders = MineOrderStore._load()
            found = False
            for o in orders:
                if o['id'] == order_id:
                    o['status'] = 'CANCELLED'
                    found = True
                    break
            if found:
                MineOrderStore._save(orders)
            return found

    @staticmethod
    def update_price(order_id: str, new_price: float) -> bool:
        with _lock:
            orders = MineOrderStore._load()
            found = False
            for o in orders:
                if o['id'] == order_id and o.get('status') == 'PENDING':
                    o['price'] = new_price
                    found = True
                    break
            if found:
                MineOrderStore._save(orders)
            return found

    @staticmethod
    def _today_start_ms() -> int:
        today_ist = datetime.now(_IST).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(today_ist.timestamp() * 1000)
