"""Shared bits of the Order Placement plan stores.

Once the store behind the pasted-tip signal mode (removed 2026-09-15). What
stays is what ``tg_call_store.py`` was built on: the stage vocabulary a
broker slot moves through, the terminal phases, and the atomic JSON write.

Writes are atomic (temp file, fsync, ``os.replace``), for the reason the
Second Candle algo's are: a torn write is answered by a parse error, a parse
error falls back to "no calls", and "no calls" means a live position with a
stop behind it stops being managed by anything.
"""

import json
import os
from datetime import timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

# Terminal phases. An engine skips these; nothing moves a call out of one.
DONE_PHASES = ('DONE', 'CANCELLED', 'FAILED', 'SKIPPED')

# Per-broker stages, in the order a broker passes through them.
STAGE_PENDING_ENTRY = 'PENDING_ENTRY'   # the entry stop is resting
STAGE_LIVE = 'LIVE'                     # filled; the stop is working
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
