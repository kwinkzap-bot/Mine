"""Durable state for the signals the Order Placement page has armed.

Its own file, not a shelf inside ``mine_orders.json``. The order book there is
one flat list rewritten in full on every mutation and already runs to a few
thousand rows; the engine writes this state on a three-second tick, and making
each of those a full rewrite of the order book would be a lot of churn on the
one file every screen reads.

The split has a second point. What lives here is only the *plan*: which stage
each broker has reached, what its entry actually filled at, and the record ids
of its legs. The legs themselves are ordinary ``MineOrderStore`` records with
``strategy='op'``, which is what keeps the page's edit, cancel, reconcile and
exit-all paths working on them unchanged. Prices are read back from those
records, never cached here — that is what makes a price a user has moved by
hand on the strip the price the engine goes on to work with.

Writes are atomic (temp file, fsync, ``os.replace``), for the reason the
Second Candle algo's are: a torn write is answered by a parse error, a parse
error falls back to "no signals", and "no signals" means an armed ladder with
a live position behind it stops being managed by anything.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from trading_app.app.utils.logger import logger

_STORAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'op_signals.json')
_lock = threading.RLock()
_IST = timezone(timedelta(hours=5, minutes=30))

# Signals older than this are dropped on the next write. A signal is an
# intraday thing — nothing here is squared off later than the same afternoon —
# and keeping a week of them would only give the engine dead plans to skip.
_KEEP_DAYS = 3

# Terminal phases. The engine skips these; nothing moves a signal out of one.
DONE_PHASES = ('DONE', 'CANCELLED', 'FAILED')

# Per-broker stages, in the order a broker passes through them.
STAGE_PENDING_ENTRY = 'PENDING_ENTRY'   # the entry stop is resting
STAGE_LIVE = 'LIVE'                     # filled; stop and targets are working
STAGE_T1_DONE = 'T1_DONE'               # first target booked, stop at entry
STAGE_T2_DONE = 'T2_DONE'               # second booked, stop at target 1
STAGE_FLAT = 'FLAT'                     # this broker is out
STAGE_NO_FILL = 'NO_FILL'               # the entry never traded here
STAGE_DEAD = 'DEAD'                     # the broker refused the entry outright

BROKER_DONE_STAGES = (STAGE_FLAT, STAGE_NO_FILL, STAGE_DEAD)


def _atomic_write_json(path: str, obj) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(obj, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class OpSignalStore:
    """Every method takes the file lock and re-reads. Call from any thread."""

    @staticmethod
    def _load() -> list:
        if not os.path.exists(_STORAGE_FILE):
            return []
        for attempt in (1, 2):
            try:
                with open(_STORAGE_FILE, 'r') as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except Exception as e:
                if attempt == 1:
                    # One retry before believing it: a reader can still catch
                    # the instant between os.replace and the new inode being
                    # visible on some filesystems, and answering that with
                    # "no signals" abandons a live ladder.
                    time.sleep(0.05)
                    continue
                logger.error(f"[OpSignalStore] Load failed, treating as empty: {e}")
                return []
        return []

    @staticmethod
    def _save(signals: list) -> None:
        cutoff = int(time.time() * 1000) - _KEEP_DAYS * 24 * 60 * 60 * 1000
        keep = [s for s in signals if s.get('created_at', 0) >= cutoff]
        try:
            _atomic_write_json(_STORAGE_FILE, keep)
        except Exception as e:
            logger.error(f"[OpSignalStore] Save failed: {e}")

    @staticmethod
    def create(plan: dict) -> dict:
        with _lock:
            signals = OpSignalStore._load()
            record = {**plan,
                      'id': f"sig-{uuid.uuid4().hex[:12]}",
                      'created_at': int(time.time() * 1000)}
            signals.append(record)
            OpSignalStore._save(signals)
            return record

    @staticmethod
    def get(signal_id: str) -> dict:
        with _lock:
            for s in OpSignalStore._load():
                if s.get('id') == signal_id:
                    return s
            return {}

    @staticmethod
    def get_today() -> list:
        start = int(datetime.now(_IST).replace(hour=0, minute=0, second=0,
                                               microsecond=0).timestamp() * 1000)
        with _lock:
            return [s for s in OpSignalStore._load() if s.get('created_at', 0) >= start]

    @staticmethod
    def get_active() -> list:
        """Signals the engine still has work to do on."""
        return [s for s in OpSignalStore.get_today()
                if s.get('phase') not in DONE_PHASES]

    @staticmethod
    def update(signal_id: str, updates: dict) -> dict:
        with _lock:
            signals = OpSignalStore._load()
            found = {}
            for s in signals:
                if s.get('id') == signal_id:
                    s.update(updates)
                    found = s
                    break
            if found:
                OpSignalStore._save(signals)
            return found

    @staticmethod
    def update_broker(signal_id: str, instance, updates: dict) -> dict:
        """Merge into one broker's slice of a signal.

        Read-modify-write under the file lock rather than mutating a record the
        caller is holding: the engine walks several brokers in one tick, and a
        whole-record write from broker 2 would otherwise drop what broker 1
        had just recorded about its own fill.
        """
        key = str(instance)
        with _lock:
            signals = OpSignalStore._load()
            found = {}
            for s in signals:
                if s.get('id') != signal_id:
                    continue
                slot = (s.setdefault('brokers', {})).setdefault(key, {})
                slot.update(updates)
                found = slot
                break
            if found:
                OpSignalStore._save(signals)
            return found

    @staticmethod
    def finish_if_all_brokers_done(signal_id: str) -> dict:
        """Close a signal once no broker has anything left to manage."""
        with _lock:
            signals = OpSignalStore._load()
            for s in signals:
                if s.get('id') != signal_id:
                    continue
                if s.get('phase') in DONE_PHASES:
                    return s
                slots = (s.get('brokers') or {}).values()
                if slots and all(b.get('stage') in BROKER_DONE_STAGES for b in slots):
                    s['phase'] = 'DONE'
                    s['finished_at'] = int(time.time() * 1000)
                    OpSignalStore._save(signals)
                return s
            return {}
