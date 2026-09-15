"""Reset the EMA Confluence live algo to a freshly-deployed state.

Wipes every open paper position, every armed setup and the whole trade history,
so the next monitor thread starts from `_fresh_state()` — every symbol
`pending_scan`, `last_scan_date` None — and the first record it writes is a
brand-new entry.

    PYTHONPATH=src ../.venv/bin/python scripts/reset_ema_confluence.py

The running thread is the trap: `_monitor_loop` loads the state file ONCE and
then re-saves its in-memory copy every `_POLL_SECS` (15 s), so a reset written
while it runs is overwritten on the next tick and looks like it never happened.
The script therefore refuses when the state file has been touched within the
last two polls. Either click Stop on the EMA Confluence tab first (then Start
again afterwards — the daily scan only reads candles up to yesterday, so a
mid-session start is safe), or run it after the thread's 15:30 exit.

Does NOT build the Flask app: create_app() starts the scheduler and restarts
the live algos (see CLAUDE.md). It imports the algo module only for the file
paths and the fresh-state shape.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trading_app.algo.ema_confluence import ema_confluence_algo as eca  # noqa: E402


def _write(path: str, obj) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def thread_looks_alive() -> bool:
    try:
        age = time.time() - os.path.getmtime(eca.STATE_FILE)
    except OSError:
        return False
    return age < 2 * eca._POLL_SECS


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--force', action='store_true',
                    help='reset even if the state file was just written (the running '
                         'thread will overwrite it — only useful if you know it is stopping)')
    args = ap.parse_args()

    if thread_looks_alive() and not args.force:
        print("REFUSED — ema_confluence_state.json was written in the last "
              f"{2 * eca._POLL_SECS}s, so the monitor thread is alive and would overwrite "
              "the reset on its next tick.\nStop the algo from the EMA Confluence tab "
              "(or wait for its 15:30 exit) and run again.")
        return 2

    try:
        with open(eca.STATE_FILE) as f:
            old = json.load(f)
        stocks = old.get('stocks', {}) if isinstance(old, dict) else {}
        open_pos = sorted(s for s, v in stocks.items() if v.get('phase') == 'in_position')
        armed = sum(1 for v in stocks.values() if v.get('phase') == 'watching')
    except Exception:
        open_pos, armed = [], 0

    def _count(path):
        try:
            with open(path) as f:
                h = json.load(f)
            return len(h) if isinstance(h, list) else 0
        except Exception:
            return 0

    n_today, n_all = _count(eca.HISTORY_FILE), _count(eca.ALL_HISTORY_FILE)

    fresh = eca.EmaConfluenceAlgo.__new__(eca.EmaConfluenceAlgo)._fresh_state()
    _write(eca.STATE_FILE, fresh)
    _write(eca.HISTORY_FILE, [])
    _write(eca.ALL_HISTORY_FILE, [])

    print(f"reset: dropped {len(open_pos)} open paper position(s)"
          f"{' (' + ', '.join(open_pos) + ')' if open_pos else ''}, "
          f"{armed} armed setup(s), {n_today} today / {n_all} all-time history record(s); "
          f"{len(fresh['stocks'])} symbols now pending_scan")
    return 0


if __name__ == '__main__':
    sys.exit(main())
