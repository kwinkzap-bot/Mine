"""Swing Momentum — the daily Invested / Current ledger behind the 📈 graphs.

The sheet is the only record: configs hold current holdings, not what they
were worth last Tuesday. These pin the properties that keep the graph honest —
upserts replace rather than duplicate, a missing card does not print a dip,
a removed config keeps its past, and nothing is ever written as zero.

No create_app() — the module is imported directly.
"""
from datetime import date

from trading_app.algo.swing_momentum import sm_value_history as vh


def cfg(cid, index='NIFTY 500', instance=7, name='Kavin (Kite)', entries=True):
    return {
        'id': cid, 'index': index,
        'broker': {'instance': instance, 'broker_type': 'zerodha', 'broker_name': name},
        'live_entries': [{'symbol': 'X', 'qty': 1, 'entry_price': 10}] if entries else [],
    }


def val(inv, cur):
    return {'invested': inv, 'current': cur, 'deployed': inv, 'cash': 0}


# ── the sheet ────────────────────────────────────────────────────────────

def test_upsert_replaces_the_same_day_rather_than_appending(tmp_path):
    p = str(tmp_path / 'v.csv')
    vh.upsert_rows([vh.snapshot_row(cfg('a'), val(100, 101), date(2026, 9, 15))], p)
    vh.upsert_rows([vh.snapshot_row(cfg('a'), val(100, 99), date(2026, 9, 15))], p)
    rows = vh.load_rows(p)
    assert len(rows) == 1
    assert rows[0]['current'] == 99.0          # the close overwrote the intraday point


def test_rows_survive_a_round_trip_with_their_broker_and_index(tmp_path):
    p = str(tmp_path / 'v.csv')
    vh.upsert_rows([vh.snapshot_row(cfg('a', 'NIFTY SMALLCAP 250', 6, 'Saranya (Dhan)'),
                                    val(100000, 108159.5), date(2026, 9, 15))], p)
    r = vh.load_rows(p)[0]
    assert r['broker_instance'] == '6'
    assert r['broker_name'] == 'Saranya (Dhan)'
    assert r['index'] == 'NIFTY SMALLCAP 250'
    assert r['current'] == 108159.5
    assert open(p).readline().strip() == ','.join(vh.COLUMNS)


def test_missing_sheet_is_empty_not_an_error(tmp_path):
    assert vh.load_rows(str(tmp_path / 'nope.csv')) == []
    assert vh.series([], 'all') == []


# ── recording ────────────────────────────────────────────────────────────

def test_record_skips_configs_the_pricer_cannot_value(tmp_path):
    """A quote outage must not write a zero row — that reads as money gone."""
    p = str(tmp_path / 'v.csv')
    seen = []
    def pricer(c):
        seen.append(c['id'])
        return val(100, 110) if c['id'] == 'a' else None
    rows = vh.record_snapshot([cfg('a'), cfg('b'), cfg('c', entries=False)],
                              pricer, date(2026, 9, 15), p)
    assert [r['config_id'] for r in rows] == ['a']
    assert seen == ['a', 'b']                   # the empty config was never priced
    assert [r['config_id'] for r in vh.load_rows(p)] == ['a']


def test_record_swallows_a_pricer_exception_per_config(tmp_path):
    p = str(tmp_path / 'v.csv')
    def pricer(c):
        if c['id'] == 'a':
            raise RuntimeError('fyers down')
        return val(1, 2)
    rows = vh.record_snapshot([cfg('a'), cfg('b')], pricer, date(2026, 9, 15), p)
    assert [r['config_id'] for r in rows] == ['b']


# ── the series ───────────────────────────────────────────────────────────

def rows_for(*specs):
    """specs: (cid, instance, day, invested, current)"""
    return [vh.snapshot_row(cfg(cid, instance=inst), val(inv, cur), date(2026, 9, d))
            for cid, inst, d, inv, cur in specs]


def test_all_scope_sums_every_config_per_day():
    rows = rows_for(('a', 7, 15, 100, 110), ('b', 6, 15, 200, 190),
                    ('a', 7, 16, 100, 120), ('b', 6, 16, 200, 200))
    s = vh.series(rows, 'all')
    assert s == [{'date': '2026-09-15', 'invested': 300.0, 'current': 300.0},
                 {'date': '2026-09-16', 'invested': 300.0, 'current': 320.0}]


def test_broker_scope_keeps_only_that_slot():
    rows = rows_for(('a', 7, 15, 100, 110), ('b', 6, 15, 200, 190))
    assert vh.series(rows, 'broker', '6') == [{'date': '2026-09-15', 'invested': 200.0, 'current': 190.0}]
    assert vh.series(rows, 'broker', 6)   == vh.series(rows, 'broker', '6')   # int slot works too


def test_config_scope_is_that_config_alone():
    rows = rows_for(('a', 7, 15, 100, 110), ('b', 7, 15, 200, 190))
    assert vh.series(rows, 'config', 'b') == [{'date': '2026-09-15', 'invested': 200.0, 'current': 190.0}]


def test_a_card_missing_one_day_is_carried_forward_not_dropped():
    """Three of four cards priced on the 16th must not print a dip."""
    rows = rows_for(('a', 7, 15, 100, 110), ('b', 6, 15, 200, 190),
                    ('a', 7, 16, 100, 120),
                    ('a', 7, 17, 100, 130), ('b', 6, 17, 200, 210))
    s = vh.series(rows, 'all')
    assert s[1] == {'date': '2026-09-16', 'invested': 300.0, 'current': 310.0}


def test_a_config_contributes_nothing_before_it_went_live_or_after_it_was_removed():
    rows = rows_for(('a', 7, 15, 100, 110), ('a', 7, 16, 100, 120), ('a', 7, 17, 100, 130),
                    ('b', 6, 16, 200, 200))               # b: live on the 16th, gone by the 17th
    s = vh.series(rows, 'all')
    assert [p['invested'] for p in s] == [100.0, 300.0, 100.0]


def test_unknown_scope_yields_nothing():
    rows = rows_for(('a', 7, 15, 100, 110))
    assert vh.series(rows, 'galaxy', 'x') == []
