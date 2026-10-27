"""One-off maintenance for oi_data.db: index oi_history and switch to WAL.

Why: every "latest OI snapshot" read (/open-interest, Round Strike's OI strip,
the ATM CE OI lookup) runs

    SELECT * FROM oi_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1

and with no index on oi_history that is a full scan of a ~3 GB table plus a
sort — 3.3 s measured on 2026-10-27. And in the default rollback-journal mode
the once-a-minute OI persistence write locks every reader out while it
commits. The index makes the read a seek; WAL lets reads go on during writes.

Both need the database to themselves (the index build scans the whole table
under a write lock, and the WAL switch needs no other connection), so this
refuses to run while the app is up or during market hours. Run it AFTER 15:45
IST with the LaunchAgent booted out:

    launchctl bootout gui/$(id -u)/com.mine.livealgo
    PYTHONPATH=src ../.venv/bin/python scripts/oi_db_maintenance.py
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mine.livealgo.plist

Idempotent: a second run finds the index and WAL already in place.
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'oi_data.db'))
INDEX_SQL = 'CREATE INDEX IF NOT EXISTS idx_oi_history_symbol_ts ON oi_history(symbol, timestamp)'
PROBE_SQL = 'SELECT id, timestamp FROM oi_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1'
IST = timezone(timedelta(hours=5, minutes=30))


def _app_running() -> bool:
    """True if anything is listening on :5000 or a main.py is alive."""
    for cmd in (['lsof', '-tnP', '-iTCP:5000', '-sTCP:LISTEN'], ['pgrep', '-f', 'main.py']):
        try:
            if subprocess.run(cmd, capture_output=True, text=True).stdout.strip():
                return True
        except FileNotFoundError:
            pass
    return False


def _in_market_hours(now: datetime) -> bool:
    hm = now.strftime('%H:%M')
    return now.weekday() < 5 and '09:00' <= hm < '15:45'


def _probe(conn, symbol='NIFTY'):
    plan = ' / '.join(r[-1] for r in conn.execute('EXPLAIN QUERY PLAN ' + PROBE_SQL, (symbol,)))
    t0 = time.perf_counter()
    row = conn.execute(PROBE_SQL, (symbol,)).fetchone()
    return plan, time.perf_counter() - t0, row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    ap.add_argument('--db', default=DEFAULT_DB, help='path to oi_data.db')
    ap.add_argument('--force', action='store_true',
                    help='skip the app-running / market-hours checks (for a scratch copy only)')
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f'no database at {args.db}')
        return 2
    if not args.force:
        now = datetime.now(IST)
        if _in_market_hours(now):
            print(f'{now:%H:%M} IST is inside 09:00-15:45 — run this after hours.')
            return 3
        if _app_running():
            print('The app is running (port 5000 / main.py). Boot it out first:\n'
                  '  launchctl bootout gui/$(id -u)/com.mine.livealgo')
            return 3

    conn = sqlite3.connect(args.db, timeout=60)
    try:
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        plan, secs, row = _probe(conn)
        print(f'before: journal_mode={mode}; latest NIFTY read {secs:.2f}s via [{plan}] -> {row}')

        t0 = time.perf_counter()
        conn.execute(INDEX_SQL)
        conn.commit()
        print(f'index idx_oi_history_symbol_ts ready in {time.perf_counter() - t0:.1f}s')

        mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode.lower() != 'wal':
            print(f'WAL switch refused (journal_mode is still {mode}) — another connection open?')
            return 1

        plan, secs, row = _probe(conn)
        print(f'after:  journal_mode={mode}; latest NIFTY read {secs:.4f}s via [{plan}] -> {row}')
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
