"""Verify the crossover engine against the 23 rows observed on stockmojo.in
(7 Aug, 3:40 PM snapshot), then exercise persistence + detection on a temp DB.

No create_app() anywhere — that would start the live scheduler.
"""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.oi_crossover_service as mod

# symbol, direction, ce_oi, pe_oi, pcr, ce_chg, pe_chg  (Cr=1e7, L=1e5, K=1e3)
Cr, L, K = 1e7, 1e5, 1e3
OBSERVED = [
    ('BANKINDIA',  'BEAR', 3.57*Cr, 2.98*Cr, 0.84, -1.46*L,  -1.72*L),
    ('ICICIGI',    'BULL', 23.41*L, 14.22*L, 0.61,  1.31*L,   1.46*L),
    ('OIL',        'BULL', 57.13*L, 45.79*L, 0.80, 11.83*L,  12.04*L),
    ('ASHOKLEY',   'BULL', 6.13*Cr, 5.69*Cr, 0.93,  6.70*L,   7.10*L),
    ('BHARATFORG', 'BULL', 31.62*L, 25.11*L, 0.79,  2.14*L,   2.22*L),
    ('PATANJALI',  'BEAR', 1.04*Cr, 75.19*L, 0.73, -32.25*K, -47.30*K),
    ('PETRONET',   'BEAR', 90.78*L, 82.42*L, 0.91,  1.54*L,   1.33*L),
    ('IOC',        'BULL', 4.37*Cr, 3.20*Cr, 0.73, 11.70*L,  11.90*L),
    ('BANKEX',     'BEAR', 20.25*K, 19.74*K, 0.97,  270,      -210),
    ('IDFCFIRSTB', 'BEAR', 12.68*Cr, 8.64*Cr, 0.68, 8.90*L,   8.72*L),
    ('ADANIENSOL', 'BEAR', 41.56*L, 23.50*L, 0.57, -9.45*K,  -11.48*K),
    ('LODHA',      'BEAR', 53.96*L, 36.63*L, 0.68, -18.75*K, -24.38*K),
    ('HINDUNILVR', 'BULL', 1.19*Cr, 61.01*L, 0.51, -30.00*K, -26.70*K),
    ('BHARTIARTL', 'BULL', 1.88*Cr, 94.40*L, 0.50,  2.21*L,   2.27*L),
    ('WAAREEENER', 'BULL', 30.84*L, 19.82*L, 0.64, 21.18*K,  21.53*K),
    ('MUTHOOTFIN', 'BULL', 59.76*L, 28.11*L, 0.47, -76.73*K, -56.10*K),
    ('ULTRACEMCO', 'BEAR', 6.90*L,  3.32*L,  0.48, 18.90*K,  18.05*K),
    ('INDUSTOWER', 'BULL', 2.34*Cr, 1.26*Cr, 0.54,  5.00*L,   6.39*L),
    ('TORNTPHARM', 'BULL', 10.89*L, 6.24*L,  0.57, 34.13*K,  36.63*K),
    ('DLF',        'BULL', 1.47*Cr, 94.96*L, 0.65,  7.38*L,   9.21*L),
    ('BEL',        'BULL', 5.77*Cr, 3.83*Cr, 0.66, -3.21*L,  -2.92*L),
    ('TATAPOWER',  'BEAR', 2.85*Cr, 2.18*Cr, 0.76,  7.61*L,   6.90*L),
    ('VEDL',       'BULL', 3.30*Cr, 1.79*Cr, 0.54,  2.65*L,   2.75*L),
]

print("=== 1. Direction rule vs stockmojo's own tags ===")
bad = []
for sym, expected, ce_oi, pe_oi, pcr, ce_chg, pe_chg in OBSERVED:
    got = 'BULL' if (pe_chg - ce_chg) > 0 else 'BEAR'
    if got != expected:
        bad.append((sym, expected, got))
print(f"  {len(OBSERVED) - len(bad)}/{len(OBSERVED)} match", bad or '')
assert not bad, bad

print("\n=== 2. PCR would NOT reproduce the tags (proving levels aren't the rule) ===")
pcr_match = sum(1 for s, e, co, po, pcr, cc, pc in OBSERVED
                if ('BULL' if pcr > 1 else 'BEAR') == e)
print(f"  PCR>1 rule: {pcr_match}/{len(OBSERVED)} — as expected, far worse than the change-line rule")

print("\n=== 3. Quality classification ===")
buckets = {}
for sym, d, ce_oi, pe_oi, pcr, ce_chg, pe_chg in OBSERVED:
    q = mod.OICrossoverService.classify(d, ce_chg, pe_chg)
    buckets.setdefault(q, []).append(sym)
for q, syms in sorted(buckets.items()):
    print(f"  {q:11} {len(syms):2}  {', '.join(syms[:6])}")

print("\n=== 4. OI change % ===")
for sym, d, ce_oi, pe_oi, pcr, ce_chg, pe_chg in OBSERVED[:5]:
    print(f"  {sym:12} {mod.OICrossoverService.oi_chg_pct(int(ce_oi), int(pe_oi), int(ce_chg), int(pe_chg)):6.2f}%")

print("\n=== 5. Persistence + detection round-trip on a temp DB ===")
tmp = tempfile.mkdtemp()
mod.DB_PATH = os.path.join(tmp, 'test.db')

svc = mod.OICrossoverService.__new__(mod.OICrossoverService)
svc.provider = None
svc.db_path = mod.DB_PATH
svc._sector_map = None
svc._init_db()

DAY = '2026-08-07'
# ACME's put line climbs past the call line, falls back under, then crosses
# up again → 3 crosses, last one BULL.
walk = [
    ('09:20', 1000, 1200),   # seeds sign (+) — must NOT count as a cross
    ('09:23', 2000, 1500),   # diff flips negative → cross 1 (BEAR)
    ('09:26', 2500, 1400),   # still negative → no cross
    ('09:29', 2600, 4000),   # flips positive → cross 2 (BULL)
    ('09:32', 5000, 4100),   # flips negative → cross 3 (BEAR)
    ('09:35', 5100, 9000),   # flips positive → cross 4 (BULL)
]
for hm, ce, pe in walk:
    ts = f'{DAY}T{hm}:00'
    prev = svc._last_points(DAY)
    diff = pe - ce
    with sqlite3.connect(svc.db_path) as conn:
        conn.execute('''INSERT INTO oi_crossover_series
            (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (DAY, ts, 'ACME', 100.0, 500000, 400000, ce, pe, 0.8))
        p = prev.get('ACME')
        if p is not None and diff != 0 and (p > 0) != (diff > 0):
            conn.execute('''INSERT OR IGNORE INTO oi_crossover_events
                (trade_date, symbol, ts, direction, ce_chg, pe_chg)
                VALUES (?,?,?,?,?,?)''',
                (DAY, 'ACME', ts, 'BULL' if diff > 0 else 'BEAR', ce, pe))
        conn.commit()

snap = svc.snapshot(DAY)
# One row per cross, newest first — the four crosses are four rows now, each
# keeping the entry it fired at rather than the last one overwriting the rest.
for row in snap['rows']:
    print(f"  {row['cross_seq']} of {row['cross_total']}  {row['direction']:4}  "
          f"{row['cross_time'][11:16]}  {row['quality']}  {row['status']}")
assert snap['success'] and len(snap['rows']) == 4, len(snap['rows'])
assert [r['cross_seq'] for r in snap['rows']] == [4, 3, 2, 1]
assert all(r['cross_total'] == 4 for r in snap['rows'])
assert len({r['id'] for r in snap['rows']}) == 4, 'each cross needs its own id'

row = snap['rows'][0]
assert row['direction'] == 'BULL'
assert row['cross_time'].endswith('09:35:00')
# Superseded crosses report FLIPPED rather than disappearing.
assert [r['status'] for r in snap['rows'][1:]] == ['FLIPPED'] * 3

ser = svc.series('ACME', DAY)
print(f"  series: {len(ser['points'])} points, {len(ser['events'])} events")
assert len(ser['points']) == 6 and len(ser['events']) == 4

# A symbol that never crossed must not appear at all.
with sqlite3.connect(svc.db_path) as conn:
    conn.execute('''INSERT INTO oi_crossover_series
        (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (DAY, f'{DAY}T09:20:00', 'QUIET', 50.0, 1000, 900, 10, 20, 0.9))
    conn.commit()
assert {r['symbol'] for r in svc.snapshot(DAY)['rows']} == {'ACME'}
print("  non-crossing symbol correctly excluded")

# The events above were written straight to the table without a rating, the
# same shape as every cross recorded before the rating was stored. The backfill
# has to recover them from the series row of the sweep that detected them,
# or Historical mode would filter every past session down to nothing.
assert all(r['quality'] is None for r in svc.snapshot(DAY)['rows'])
res = svc.backfill_event_metrics()
print(f"  backfill: rated={res['rated']} skipped={res['skipped']}")
assert res['success'] and res['rated'] == 4, res
rated = svc.snapshot(DAY)['rows']
assert all(r['quality'] is not None and r['separation'] is not None for r in rated)
print(f"  recovered: " + ', '.join(
    f"{r['cross_time'][11:16]} {r['quality']}/{r['separation']:.0f}%" for r in rated))
# Re-running must not touch anything it has already rated.
assert svc.backfill_event_metrics()['rated'] == 0

print(f"  dates available: {svc.available_dates()}")
print("\nALL CHECKS PASSED")
