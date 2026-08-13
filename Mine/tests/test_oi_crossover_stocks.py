"""End-to-end proof that the scanner detects crossovers on F&O *stocks*, not
just the three index chains.

The live path needs a Fyers access token (daily browser login), so this drives
the same code with a stub that returns realistic per-strike option chains and
walks the OI between scans. It exercises the real fetch_totals parsing, the
real crossover detection, and the real snapshot query.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.oi_crossover_service as mod

mod.DB_PATH = os.path.join(tempfile.mkdtemp(), 'stocks.db')

# This test is about universe coverage and detection, not the session gate
# (which test_oi_crossover_session.py owns), so pretend the market is always
# open — otherwise the suite only passes on a weekday between 9:15 and 3:40.
mod.market_session_state = lambda now=None: (True, '')

STOCKS = ['RELIANCE', 'TCS', 'SBIN', 'INFY', 'TATAPOWER']
INDEXES = ['NIFTY', 'BANKNIFTY']


class ChainStub:
    """Minimal stand-in for fyers.optionchain.

    ``step`` advances a scripted OI walk so consecutive scans differ, which is
    what a crossover needs. Deep-OTM strikes get small OI, ATM gets the bulk —
    enough shape that the CE/PE totals and the summed ``oich`` are realistic.
    """

    def __init__(self):
        self.step = 0
        self.calls = []
        # (ce_chg_per_strike, pe_chg_per_strike) per step, per symbol index.
        # Designed so RELIANCE and SBIN swap sides between step 1 and 2.
        self.walk = {
            'RELIANCE':  [(+400, +100), (+400, +900)],   # BEAR → BULL
            'TCS':       [(+300, +120), (+500, +150)],   # stays BEAR, no cross
            'SBIN':      [(-200, +600), (+800, +100)],   # BULL  → BEAR
            'INFY':      [(+100, +100), (+100, +100)],   # dead flat, never crosses
            'TATAPOWER': [(-500, -200), (-900, -150)],   # BULL, stays BULL
            'NIFTY':     [(+900, +200), (+150, +950)],   # BEAR  → BULL
            'BANKNIFTY': [(+250, +700), (+300, +800)],   # stays BULL
        }

    def optionchain(self, data):
        symbol = data['symbol']
        self.calls.append((symbol, data.get('strikecount')))
        root = symbol.split(':')[1].replace('-EQ', '').replace('-INDEX', '')
        root = {'NIFTY50': 'NIFTY', 'NIFTYBANK': 'BANKNIFTY'}.get(root, root)
        if root not in self.walk:
            return {'s': 'error', 'message': 'Bad request'}

        ce_d, pe_d = self.walk[root][min(self.step, len(self.walk[root]) - 1)]
        spot = 1000.0
        rows = [{'symbol': symbol, 'ltp': spot, 'option_type': ''}]
        ce_total = pe_total = 0
        for i in range(-10, 11):                      # 21 strikes, ATM in the middle
            base = 50_000 * (11 - abs(i))             # OI thins out away from ATM
            for opt, delta in (('CE', ce_d), ('PE', pe_d)):
                oi = base + delta * (11 - abs(i))
                rows.append({'symbol': f'{root}{i}{opt}', 'option_type': opt,
                             'oi': oi, 'oich': delta * (11 - abs(i))})
                if opt == 'CE':
                    ce_total += oi
                else:
                    pe_total += oi
        return {'s': 'ok', 'data': {'callOi': ce_total, 'putOi': pe_total,
                                    'optionsChain': rows}}


class Provider:
    def __init__(self, stub):
        self.fyers = stub


stub = ChainStub()
svc = mod.OICrossoverService(Provider(stub))
universe = INDEXES + STOCKS

print('=== scan 1 (seeds the day, must produce no crossovers) ===')
r1 = svc.scan(universe)
print(' ', {k: r1[k] for k in ('success', 'scanned', 'failed', 'crossovers')})
assert r1['success'], r1
assert r1['scanned'] == len(universe), r1
assert r1['crossovers'] == 0, 'first scan of a day cannot produce a cross'

print('\n=== underlying symbols requested ===')
for sym, sc in stub.calls:
    print(f'  {sym:24} strikecount={sc}')
assert ('NSE:RELIANCE-EQ', mod.STRIKE_COUNT) in stub.calls
assert ('NSE:NIFTY50-INDEX', mod.STRIKE_COUNT) in stub.calls

stub.step = 1
print('\n=== scan 2 (OI has moved) ===')
r2 = svc.scan(universe)
print(' ', {k: r2[k] for k in ('success', 'scanned', 'failed', 'crossovers')})
assert r2['success'], r2

snap = svc.snapshot()
print(f"\n=== snapshot: {snap['symbols']} symbols scanned, {len(snap['rows'])} crossed ===")
for row in snap['rows']:
    print(f"  {row['symbol']:11} {row['direction']:4} pcr={row['pcr']:.2f} "
          f"ce={row['ce_chg']:>10,} pe={row['pe_chg']:>10,} "
          f"{row['cross_seq']}/{row['cross_total']} {row['quality']} "
          f"sep={row['separation']:.0f}% {row['status']}")

# The rating travels with the cross rather than being re-derived per poll,
# which is what stops a row drifting out of the filter it arrived under.
for row in snap['rows']:
    assert row['quality'] is not None, row
    assert row['separation'] is not None, row
    assert row['status'] in ('LIVE', 'PENDING', 'FADED', 'FLIPPED', 'UNKNOWN'), row

crossed = {r['symbol']: r['direction'] for r in snap['rows']}
assert snap['symbols'] == len(universe), snap['symbols']

# The stocks that were scripted to swap sides must be present and pointing the
# right way; the ones that never swapped must be absent entirely.
assert crossed.get('RELIANCE') == 'BULL', crossed
assert crossed.get('SBIN') == 'BEAR', crossed
assert crossed.get('NIFTY') == 'BULL', crossed
for quiet in ('TCS', 'INFY', 'TATAPOWER', 'BANKNIFTY'):
    assert quiet not in crossed, f'{quiet} never swapped sides but appeared'

stock_crosses = [s for s in crossed if s in STOCKS]
print(f"\nstocks that crossed: {stock_crosses}")
assert stock_crosses, 'no stock crossovers detected — the stock path is broken'

# The drilldown must work for a stock, not just an index.
ser = svc.series('RELIANCE')
print(f"RELIANCE series: {len(ser['points'])} points, {len(ser['events'])} events")
assert len(ser['points']) == 2 and len(ser['events']) == 1

print('\n=== a dead access token must abort, not write a partial day ===')
class DeadToken:
    def optionchain(self, data):
        return {'s': 'error', 'message': 'Please provide valid token'}

dead = mod.OICrossoverService(Provider(DeadToken()))
r3 = dead.scan(universe)
assert not r3['success'] and 'token' in r3['error'].lower(), r3
print(' ', r3['error'])
assert dead.snapshot()['scans'] == 2, 'a failed scan must not add a scan point'

print('\n=== front-month contract selection across expiry rolls ===')
from datetime import date
CONTRACTS = {'NIFTY': [(date(2026, 8, 25), 'AUG'),
                       (date(2026, 9, 29), 'SEP'),
                       (date(2026, 10, 27), 'OCT')]}
# None where the contract that was front-month has since been delisted, or
# where the date is past everything still listed — those sessions keep spot.
EXPECTED = [
    ('2026-07-27', None),   # Jul was front month; delisted, unrecoverable
    ('2026-07-28', None),   # Jul expiry day — Aug was still the next month
    ('2026-07-29', 'AUG'),  # roll
    ('2026-08-05', 'AUG'),
    ('2026-08-25', 'AUG'),  # expiry day, still front month
    ('2026-08-26', 'SEP'),  # roll
    ('2026-09-29', 'SEP'),
    ('2026-09-30', 'OCT'),  # roll
    ('2026-10-27', 'OCT'),
    ('2026-10-28', None),   # past every listed contract
]
for day, want in EXPECTED:
    got = mod.OICrossoverService._front_month_for('NIFTY', day, CONTRACTS)
    print(f'  {day} -> {str(got):5} (expected {want})')
    assert got == want, f'{day}: got {got}, expected {want}'
assert mod.OICrossoverService._front_month_for('NOSUCH', '2026-08-05', CONTRACTS) is None

print('\nALL CHECKS PASSED')
