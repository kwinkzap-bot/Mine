"""Daily Invested / Current ledger for the Swing Momentum live configs.

One row per config per day in `sm_daily_values.csv` — a plain sheet, so it
opens in Numbers/Excel as-is. Written at 15:35 IST by the scheduler and
refreshed opportunistically whenever the Live Watch page prices a config, so
today's point tracks the market while the page is open and the close is
what survives.

The ledger is the only history there is: the configs themselves hold just
the current holdings, so nothing here can be rebuilt from them later. Rows
carry the config's broker slot and index so a config removed later still
counts in its broker's and the total's past — money that was there was there.
"""
from __future__ import annotations

import csv
import os
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional

SM_DAILY_VALUES_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), 'sm_daily_values.csv')
)

# Column order is the sheet's contract — append new columns at the end only.
COLUMNS = ['date', 'config_id', 'index', 'broker_instance', 'broker_name',
           'invested', 'current', 'deployed', 'cash']

_NUMERIC = ('invested', 'current', 'deployed', 'cash')


def _coerce(row: dict) -> dict:
    out = {k: (row.get(k) if row.get(k) is not None else '') for k in COLUMNS}
    for k in _NUMERIC:
        try:
            out[k] = round(float(out[k] or 0), 2)
        except (TypeError, ValueError):
            out[k] = 0.0
    out['broker_instance'] = str(out['broker_instance'] or '')
    return out


def _path(path: Optional[str]) -> str:
    # Resolved per call, not at def time, so a test can point the module at a
    # scratch file by monkeypatching SM_DAILY_VALUES_PATH.
    return path or SM_DAILY_VALUES_PATH


def load_rows(path: Optional[str] = None) -> List[dict]:
    path = _path(path)
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline='') as fh:
            return [_coerce(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


def _write_rows(rows: List[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r['date'], r['config_id'])):
            w.writerow(r)
    os.replace(tmp, path)


def upsert_rows(new_rows: Iterable[dict], path: Optional[str] = None) -> int:
    """Insert or replace by (date, config_id). The latest write of a day wins,
    which is what lets an intraday refresh be overwritten by the close."""
    path = _path(path)
    by_key: Dict[tuple, dict] = {(r['date'], r['config_id']): r for r in load_rows(path)}
    n = 0
    for r in new_rows:
        r = _coerce(r)
        if not r['date'] or not r['config_id']:
            continue
        by_key[(r['date'], r['config_id'])] = r
        n += 1
    if n:
        _write_rows(list(by_key.values()), path)
    return n


def snapshot_row(config: dict, valuation: dict, on: Optional[date] = None) -> dict:
    """Shape one config's valuation into a ledger row.

    `valuation` is what the pricing code already computes for the card:
    `invested` = capital + SIP − SWP (money in), `current` = holdings marked to
    market (idle cash excluded, same as the Current chip), `deployed` = cost of
    the stock held now, `cash` = idle balance.
    """
    b = config.get('broker') or {}
    return _coerce({
        'date':            str(on or date.today()),
        'config_id':       config.get('id'),
        'index':           config.get('index') or '',
        'broker_instance': b.get('instance') if b else '',
        'broker_name':     (b.get('broker_name') or b.get('broker_type') or '') if b else '',
        'invested':        valuation.get('invested', 0),
        'current':         valuation.get('current', 0),
        'deployed':        valuation.get('deployed', 0),
        'cash':            valuation.get('cash', 0),
    })


def record_snapshot(configs: List[dict], value_config: Callable[[dict], Optional[dict]],
                    on: Optional[date] = None, path: Optional[str] = None) -> List[dict]:
    """Value every config that holds stock and upsert today's rows.

    A config the pricer cannot value (returns None) is skipped rather than
    written as zero — a zero row would read as the money having left.
    """
    rows = []
    for c in configs:
        if not c.get('live_entries'):
            continue
        try:
            v = value_config(c)
        except Exception:
            v = None
        if v is None:
            continue
        rows.append(snapshot_row(c, v, on))
    if rows:
        upsert_rows(rows, path)
    return rows


def _in_scope(row: dict, scope: str, key: str) -> bool:
    if scope == 'all':
        return True
    if scope == 'broker':
        return row['broker_instance'] == str(key)
    if scope == 'config':
        return row['config_id'] == key
    return False


def series(rows: List[dict], scope: str = 'all', key: str = '') -> List[dict]:
    """Daily Invested / Current points for a scope: 'all', 'broker' (by slot
    number) or 'config' (by id). Sorted by date.

    Aggregate scopes sum across configs per day. A config with no row on a
    day that falls inside its own recorded span is carried forward from its
    last row, so a partial refresh (three of four cards priced) does not
    print a dip that never happened. Outside its span it contributes nothing:
    before it went live there was no money, and after its last row it is
    gone.
    """
    scoped = [r for r in rows if _in_scope(r, scope, key)]
    if not scoped:
        return []

    by_cfg: Dict[str, Dict[str, dict]] = {}
    for r in scoped:
        by_cfg.setdefault(r['config_id'], {})[r['date']] = r

    dates = sorted({r['date'] for r in scoped})
    out = []
    for d in dates:
        inv = cur = 0.0
        for cfg_rows in by_cfg.values():
            cfg_dates = sorted(cfg_rows)
            if d < cfg_dates[0] or d > cfg_dates[-1]:
                continue
            r = cfg_rows.get(d)
            if r is None:
                prev = max(x for x in cfg_dates if x < d)
                r = cfg_rows[prev]
            inv += r['invested']
            cur += r['current']
        out.append({'date': d, 'invested': round(inv, 2), 'current': round(cur, 2)})
    return out
