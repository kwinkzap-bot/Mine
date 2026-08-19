"""FII sector snapshots must be keyed by CDSL period, not by download date.

The dashboard's Current tab showed a strip of dates holding identical data:
"June 16-30, 2026" was stored under ten separate dates. CDSL publishes
fortnightly, but scheduler.py's 4 PM job snapshots daily and called
save_snapshot() without a report_date — which used to default to TODAY. Every
run therefore filed the same fortnight under a new date.

Dating by the period makes a re-run of an already-stored fortnight collapse
onto its existing row through UNIQUE(date, sector).
"""
import sqlite3

import pytest

from trading_app.service.fii_sector_service import FIISectorService as SVC


# ── the period parser ────────────────────────────────────────────────────

@pytest.mark.parametrize('label,expected', [
    ('Net Investment June 16-30, 2026', '2026-06-30'),
    ('Net Investment July 01-15, 2026', '2026-07-15'),
    ('Net Investment July 16-31, 2026', '2026-07-31'),
    ('Net Investment February 16-28, 2026', '2026-02-28'),
    ('Net Investment February 16-29, 2024', '2024-02-29'),   # leap year
    ('Net Investment November 16-30, 2022', '2022-11-30'),
])
def test_period_end_date_parses_cdsl_labels(label, expected):
    assert SVC.period_end_date(label) == expected


@pytest.mark.parametrize('label', [
    None, '', 'garbage',
    'Net Investment February 16-30, 2026',   # Feb 30 does not exist
])
def test_unparsable_labels_return_none_rather_than_guessing(label):
    assert SVC.period_end_date(label) is None


def test_end_day_is_taken_not_the_start_day():
    """'01-15' must give the 15th. Taking the first number would file every
    period a fortnight early."""
    assert SVC.period_end_date('Net Investment March 01-15, 2026') == '2026-03-15'


# ── save_snapshot dating ─────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'test.db'
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE fii_sector_limits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, sector TEXT NOT NULL,
        allowed_limit_pct REAL, current_holding_pct REAL, headroom_pct REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP, auc_inr_cr REAL,
        net_invest_inr_cr REAL, net_invest_prev_inr_cr REAL,
        period_label TEXT, prev_period_label TEXT, UNIQUE(date, sector))""")
    conn.commit()
    conn.close()
    monkeypatch.setattr('trading_app.service.fii_sector_service._DB_PATH', str(path))
    return path


def _rows(period, ni=100.0):
    return [{'sector': s, 'auc_inr_cr': 1.0, 'net_invest_inr_cr': ni,
             'net_invest_prev_inr_cr': 0.0, 'period': period, 'prev_period': 'prev'}
            for s in ('Healthcare', 'Sovereign')]


def _dates(db):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute(
            'select distinct date from fii_sector_limits order by date')]
    finally:
        conn.close()


def test_snapshot_is_filed_under_the_period_end_not_today(db):
    SVC.save_snapshot(_rows('Net Investment June 16-30, 2026'))
    assert _dates(db) == ['2026-06-30']


def test_repeating_the_same_period_does_not_add_a_date(db):
    """The reported bug: the daily job re-storing an unchanged fortnight."""
    for _ in range(10):
        SVC.save_snapshot(_rows('Net Investment June 16-30, 2026'))
    assert _dates(db) == ['2026-06-30']

    conn = sqlite3.connect(db)
    try:
        assert conn.execute('select count(*) from fii_sector_limits').fetchone()[0] == 2
    finally:
        conn.close()


def test_a_new_fortnight_still_creates_its_own_date(db):
    SVC.save_snapshot(_rows('Net Investment June 16-30, 2026'))
    SVC.save_snapshot(_rows('Net Investment July 01-15, 2026'))
    assert _dates(db) == ['2026-06-30', '2026-07-15']


def test_revised_figures_for_a_period_overwrite_in_place(db):
    """CDSL restating a fortnight must update that row, not add a second date."""
    SVC.save_snapshot(_rows('Net Investment June 16-30, 2026', ni=100.0))
    SVC.save_snapshot(_rows('Net Investment June 16-30, 2026', ni=250.0))
    assert _dates(db) == ['2026-06-30']
    conn = sqlite3.connect(db)
    try:
        vals = {r[0] for r in conn.execute('select net_invest_inr_cr from fii_sector_limits')}
        assert vals == {250.0}
    finally:
        conn.close()


def test_explicit_report_date_still_wins(db):
    """The bulk/backfill paths pass the date themselves and must be untouched."""
    SVC.save_snapshot(_rows('Net Investment June 16-30, 2026'), report_date='2026-01-01')
    assert _dates(db) == ['2026-01-01']


def test_unparsable_label_falls_back_to_today(db):
    from datetime import datetime
    SVC.save_snapshot(_rows('mystery period'))
    assert _dates(db) == [datetime.now().date().isoformat()]
