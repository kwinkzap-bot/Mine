"""Collapse duplicate FII-sector snapshots onto one row per CDSL period.

WHY THIS EXISTS
    CDSL publishes FII sector data fortnightly, but scheduler.py's 4 PM job
    called save_snapshot() without a report_date, which stamped TODAY onto
    whatever fortnight was current. Every daily run therefore filed the SAME
    data under a new date: "June 16-30, 2026" ended up under ten separate
    dates, and the dashboard's Current-tab date strip filled with identical
    entries.

    The write path is fixed (FIISectorService.save_snapshot now keys on the
    period label), so no NEW duplicates are created. This is the one-off
    cleanup of the rows written before that fix.

WHAT IT DOES
    Rewrites each period's rows under its period-END date — the natural key for
    a fortnight — and drops the duplicates. It also corrects two singles that
    were merely mis-dated rather than duplicated (July 16-31 filed under
    Aug 18, July 01-15 under Aug 5).

SAFETY
    * Refuses to run if any period holds DIFFERING data across its dates, so a
      genuine revision is never silently discarded.
    * Refuses if two periods would land on the same date.
    * --dry-run (the default) only reports. Pass --apply to write.
    * Writes inside one transaction and verifies the result before COMMIT.

    Take a backup first:
        sqlite3 oi_data.db ".dump fii_sector_limits" > fii_backup.sql

USAGE
        python scripts/dedupe_fii_sector.py            # report only
        python scripts/dedupe_fii_sector.py --apply    # perform the cleanup
"""
import argparse
import collections
import hashlib
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from trading_app.service.fii_sector_service import FIISectorService  # noqa: E402

DB = os.path.join(os.path.dirname(__file__), '..', 'oi_data.db')
COLS = ('date, sector, allowed_limit_pct, current_holding_pct, headroom_pct, '
        'auc_inr_cr, net_invest_inr_cr, net_invest_prev_inr_cr, '
        'period_label, prev_period_label')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write; default is a dry run')
    ap.add_argument('--db', default=DB)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    rows = conn.execute(f'SELECT {COLS} FROM fii_sector_limits').fetchall()
    if not rows:
        print('no rows — nothing to do')
        return 0

    by_label = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by_label[r[8]][r[0]].append(r)

    # ── refuse on anything that would lose information ──
    conflicts = []
    for lbl, dates in by_label.items():
        # r[1:] — the PAYLOAD without the date column. Including the date would
        # make every date differ from every other by construction and the check
        # would always "find" a conflict.
        sigs = {hashlib.sha1(repr(sorted(r[1:] for r in v)).encode()).hexdigest()
                for v in dates.values()}
        if len(sigs) > 1:
            conflicts.append((lbl, sorted(dates)))

    targets, unparsed = {}, []
    for lbl in by_label:
        t = FIISectorService.period_end_date(lbl)
        (targets.__setitem__(lbl, t) if t else unparsed.append(lbl))

    rev = collections.defaultdict(list)
    for lbl, t in targets.items():
        rev[t].append(lbl)
    collisions = {t: l for t, l in rev.items() if len(l) > 1}

    if conflicts or unparsed or collisions:
        print('ABORT — refusing to touch the data:')
        for lbl, ds in conflicts:
            print(f'  differing data across dates for {lbl}: {ds}')
        for lbl in unparsed:
            print(f'  unparsable period label: {lbl!r}')
        for t, l in collisions.items():
            print(f'  two periods want {t}: {l}')
        return 1

    final = []
    for lbl, dates in by_label.items():
        rep = dates[sorted(dates)[0]]
        final.extend((targets[lbl],) + r[1:] for r in rep)

    moved = {lbl: sorted(d) for lbl, d in by_label.items()
             if len(d) > 1 or sorted(d)[0] != targets[lbl]}
    print(f'rows  {len(rows)} -> {len(final)}')
    print(f'dates {len({r[0] for r in rows})} -> {len(set(targets.values()))}')
    print(f'periods needing work: {len(moved)}')
    for lbl, ds in sorted(moved.items(), key=lambda kv: targets[kv[0]], reverse=True):
        print(f'  {lbl:<36} {len(ds):>2} date(s) -> {targets[lbl]}')

    if not args.apply:
        print('\ndry run — nothing written. Re-run with --apply to perform it.')
        return 0

    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM fii_sector_limits')
        conn.executemany(
            f'INSERT INTO fii_sector_limits ({COLS}) VALUES ({",".join("?" * 10)})', final)
        n_rows = conn.execute('SELECT COUNT(*) FROM fii_sector_limits').fetchone()[0]
        n_dates = conn.execute('SELECT COUNT(DISTINCT date) FROM fii_sector_limits').fetchone()[0]
        dupes = conn.execute(
            'SELECT COUNT(*) FROM (SELECT period_label FROM fii_sector_limits '
            'GROUP BY period_label HAVING COUNT(DISTINCT date) > 1)').fetchone()[0]
        if not (n_rows == len(final) and n_dates == len(targets) and dupes == 0):
            raise AssertionError(f'verification failed: {n_rows=} {n_dates=} {dupes=}')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f'ROLLED BACK — database unchanged: {e}')
        return 1

    print(f'\ndone — {n_rows} rows across {n_dates} dates, 0 duplicate periods')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
