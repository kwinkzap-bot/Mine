"""Durable state for the calls the Telegram listener has taken.

One record per call, one slot per broker inside it, the legs living in
``MineOrderStore`` as ordinary ``strategy='op'`` rows. Its own file rather
than a shelf inside ``mine_orders.json``: the order book there is one flat
list rewritten in full on every mutation, and the engine writes this state
on a three-second tick.

One thing lives here that a plan store would not otherwise need: the
``seen`` map of Telegram message ids. A restart replays nothing — Telethon
only pushes messages that arrive while it is connected — but a reconnect
after a drop can, and a call re-fired on a reconnect is a second position.
Every message id the listener has looked at is written here before it is
acted on, and a second look at the same id is refused.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime

from trading_app.app.order_placement.op_signal_store import (
    BROKER_DONE_STAGES, DONE_PHASES, _IST, _atomic_write_json)
from trading_app.app.utils.logger import logger

_STORAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tg_calls.json')
_lock = threading.RLock()
_KEEP_DAYS = 3


class TgCallStore:
    """Every method takes the file lock and re-reads. Call from any thread."""

    @staticmethod
    def _load() -> dict:
        empty = {'calls': [], 'seen': {}}
        if not os.path.exists(_STORAGE_FILE):
            return empty
        for attempt in (1, 2):
            try:
                with open(_STORAGE_FILE, 'r') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return empty
                data.setdefault('calls', [])
                data.setdefault('seen', {})
                return data
            except Exception as e:
                if attempt == 1:
                    time.sleep(0.05)
                    continue
                logger.error(f"[TgCallStore] Load failed, treating as empty: {e}")
                return empty
        return empty

    @staticmethod
    def _save(data: dict) -> None:
        cutoff_ms = int(time.time() * 1000) - _KEEP_DAYS * 24 * 60 * 60 * 1000
        data['calls'] = [c for c in data.get('calls', [])
                         if c.get('created_at', 0) >= cutoff_ms]
        data['seen'] = {k: v for k, v in data.get('seen', {}).items()
                        if int(v or 0) >= cutoff_ms}
        try:
            _atomic_write_json(_STORAGE_FILE, data)
        except Exception as e:
            logger.error(f"[TgCallStore] Save failed: {e}")

    # ── message dedupe ────────────────────────────────────────────────

    @staticmethod
    def mark_seen(chat_id, message_id) -> bool:
        """Record a message id. False if it was already there."""
        key = f'{chat_id}:{message_id}'
        with _lock:
            data = TgCallStore._load()
            if key in data['seen']:
                return False
            data['seen'][key] = int(time.time() * 1000)
            TgCallStore._save(data)
            return True

    # ── calls ─────────────────────────────────────────────────────────

    @staticmethod
    def create(plan: dict) -> dict:
        with _lock:
            data = TgCallStore._load()
            record = {**plan,
                      'id': f"tg-{uuid.uuid4().hex[:12]}",
                      'created_at': int(time.time() * 1000)}
            data['calls'].append(record)
            TgCallStore._save(data)
            return record

    @staticmethod
    def get(call_id: str) -> dict:
        with _lock:
            for c in TgCallStore._load()['calls']:
                if c.get('id') == call_id:
                    return c
            return {}

    @staticmethod
    def get_today() -> list:
        start = int(datetime.now(_IST).replace(hour=0, minute=0, second=0,
                                               microsecond=0).timestamp() * 1000)
        with _lock:
            return [c for c in TgCallStore._load()['calls']
                    if c.get('created_at', 0) >= start]

    @staticmethod
    def get_active() -> list:
        return [c for c in TgCallStore.get_today() if c.get('phase') not in DONE_PHASES]

    @staticmethod
    def update(call_id: str, updates: dict) -> dict:
        with _lock:
            data = TgCallStore._load()
            found = {}
            for c in data['calls']:
                if c.get('id') == call_id:
                    c.update(updates)
                    found = c
                    break
            if found:
                TgCallStore._save(data)
            return found

    @staticmethod
    def update_broker(call_id: str, instance, updates: dict) -> dict:
        key = str(instance)
        with _lock:
            data = TgCallStore._load()
            found = {}
            for c in data['calls']:
                if c.get('id') != call_id:
                    continue
                slot = (c.setdefault('brokers', {})).setdefault(key, {})
                slot.update(updates)
                found = slot
                break
            if found:
                TgCallStore._save(data)
            return found

    @staticmethod
    def finish_if_all_brokers_done(call_id: str) -> dict:
        with _lock:
            data = TgCallStore._load()
            for c in data['calls']:
                if c.get('id') != call_id:
                    continue
                if c.get('phase') in DONE_PHASES:
                    return c
                slots = (c.get('brokers') or {}).values()
                if slots and all(b.get('stage') in BROKER_DONE_STAGES for b in slots):
                    c['phase'] = 'DONE'
                    c['finished_at'] = int(time.time() * 1000)
                    TgCallStore._save(data)
                return c
            return {}

    @staticmethod
    def get_unbooked() -> list:
        """Calls with a slot that is flat but whose P&L is not yet written —
        the engine keeps ticking for these after the last position closes."""
        return [c for c in TgCallStore.get_today()
                if any(b.get('stage') == 'FLAT' and not b.get('booked')
                       for b in (c.get('brokers') or {}).values())]


# ── the P&L ledger ────────────────────────────────────────────────────────

_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tg_trades_history.json')


class TgTradeHistory:
    """One row per closed call per broker, kept for good.

    Tracked in git like the algos' ``*_trades_history*.json`` — it is the
    off-site record of what the auto-trader did, not runtime state, so a
    checkout can neither resurrect nor erase a position through it. Prices
    are what the broker reported filled; ``pnl_per_lot`` is the number the
    user reads across accounts of different size, ``pnl`` is that times the
    lots this account traded.
    """

    @staticmethod
    def _load() -> list:
        if not os.path.exists(_HISTORY_FILE):
            return []
        try:
            with open(_HISTORY_FILE, 'r') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"[TgTradeHistory] Load failed: {e}")
            return []

    @staticmethod
    def append(row: dict) -> dict:
        with _lock:
            rows = TgTradeHistory._load()
            record = {**row, 'id': f"tgt-{uuid.uuid4().hex[:12]}",
                      'booked_at': int(time.time() * 1000)}
            rows.append(record)
            try:
                _atomic_write_json(_HISTORY_FILE, rows)
            except Exception as e:
                logger.error(f"[TgTradeHistory] Save failed: {e}")
            return record

    @staticmethod
    def rows(days: int = 0) -> list:
        """Newest first. ``days=0`` is everything."""
        with _lock:
            rows = TgTradeHistory._load()
        if days > 0:
            cutoff = (datetime.now(_IST).replace(hour=0, minute=0, second=0, microsecond=0)
                      .timestamp() * 1000) - (days - 1) * 24 * 60 * 60 * 1000
            rows = [r for r in rows if r.get('booked_at', 0) >= cutoff]
        return sorted(rows, key=lambda r: r.get('booked_at', 0), reverse=True)
