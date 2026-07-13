"""
In-app notification store.

Persists notifications (e.g. hourly scanner results) to SQLite so the
frontend notification bell can list history and reopen any past
notification's full payload, even across server restarts. Follows the same
raw-sqlite3 pattern used by open_interest_service.py / fii_sector_service.py.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root: src/trading_app/service/../../../ = Mine/Mine
_basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
_DB_PATH = os.path.join(_basedir, 'oi_data.db')


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                data TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_notifications_created_at '
            'ON notifications(created_at DESC)'
        )


_init_db()


def create_notification(category: str, title: str, data: Dict[str, Any],
                         summary: Optional[str] = None) -> int:
    """Persist a new notification. `data` is the full payload shown in the
    detail popup; `summary` is the short line shown in the dropdown list."""
    with _connect() as conn:
        cur = conn.execute(
            'INSERT INTO notifications (category, title, summary, data) VALUES (?, ?, ?, ?)',
            (category, title, summary, json.dumps(data)),
        )
        return cur.lastrowid


def list_notifications(limit: int = 50) -> List[Dict[str, Any]]:
    """Latest notifications, newest first — without the (potentially large)
    `data` payload, for the dropdown list."""
    with _connect() as conn:
        rows = conn.execute(
            'SELECT id, category, title, summary, is_read, created_at '
            'FROM notifications ORDER BY created_at DESC, id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_notification(notification_id: int) -> Optional[Dict[str, Any]]:
    """Full notification including the parsed `data` payload, for the popup."""
    with _connect() as conn:
        row = conn.execute(
            'SELECT id, category, title, summary, data, is_read, created_at '
            'FROM notifications WHERE id = ?',
            (notification_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result['data'] = json.loads(result['data'])
    except (TypeError, ValueError):
        result['data'] = None
    return result


def mark_read(notification_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            'UPDATE notifications SET is_read = 1 WHERE id = ?',
            (notification_id,),
        )
        return cur.rowcount > 0


def unread_count() -> int:
    with _connect() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS c FROM notifications WHERE is_read = 0'
        ).fetchone()
    return int(row['c']) if row else 0
