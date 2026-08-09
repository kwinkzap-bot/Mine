"""The scanner must only record real trading sessions.

Covers the two gates: the clock (weekday + 9:15-15:40) and the frozen-feed
check that stands in for the NSE holiday calendar the app doesn't have.
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.oi_crossover_service as mod

mod.DB_PATH = os.path.join(tempfile.mkdtemp(), 'session.db')

SYMBOLS = ['NIFTY', 'RELIANCE', 'TCS', 'SBIN', 'INFY', 'TATAPOWER', 'WIPRO']


class Chain:
    """Returns a chain whose OI can be frozen or nudged between scans."""

    def __init__(self):
        self.bump = 0

    def optionchain(self, data):
        sym = data['symbol']
        rows = [{'symbol': sym, 'ltp': 100.0, 'option_type': ''}]
        for i in range(-6, 7):
            for opt in ('CE', 'PE'):
                rows.append({'symbol': f'{sym}{i}{opt}', 'option_type': opt,
                             'oi': 100_000 + i * 10, 'oich': 500 + self.bump})
        return {'s': 'ok', 'data': {'optionsChain': rows}}


class Provider:
    def __init__(self, chain):
        self.fyers = chain


chain = Chain()
svc = mod.OICrossoverService(Provider(chain))

print('=== clock gate ===')
for label, when in [('Sat 11:00', datetime(2026, 8, 15, 11, 0)),
                    ('Sun 11:00', datetime(2026, 8, 16, 11, 0)),
                    ('Mon 08:00', datetime(2026, 8, 10, 8, 0)),
                    ('Mon 16:30', datetime(2026, 8, 10, 16, 30)),
                    ('Mon 10:00', datetime(2026, 8, 10, 10, 0))]:
    live, why = mod.market_session_state(when)
    print(f'  {label:10} live={live!s:5} {why}')

# Pretend it is a Saturday: scan must refuse and write nothing.
real_state = mod.market_session_state
mod.market_session_state = lambda now=None: (False, 'weekend')
r = svc.scan(SYMBOLS)
print('\n=== scan on a weekend ===')
print(' ', r)
assert r['skipped'] and not r['success'], r
assert svc.snapshot()['scans'] == 0, 'a skipped scan must write nothing'
assert svc.last_run() is None, 'a skip is not a run and must not be logged'

# Back inside a session.
mod.market_session_state = lambda now=None: (True, '')

print('\n=== first scan of a live session ===')
r1 = svc.scan(SYMBOLS)
print(' ', {k: r1[k] for k in ('success', 'scanned', 'crossovers')})
assert r1['success'] and r1['scanned'] == len(SYMBOLS)

print('\n=== second scan, feed has not moved (holiday / stale feed) ===')
r2 = svc.scan(SYMBOLS)
print(' ', r2.get('error'))
assert not r2['success'], 'an unchanged feed must not be recorded'
assert 'frozen' in r2['error'].lower()
assert svc.snapshot()['scans'] == 1, 'the frozen scan must not add a scan point'
assert svc.last_run()['error'], 'a frozen scan IS logged, so it is visible'

print('\n=== third scan, OI has actually moved ===')
chain.bump = 250
r3 = svc.scan(SYMBOLS)
print(' ', {k: r3[k] for k in ('success', 'scanned', 'crossovers')})
assert r3['success'], r3
assert svc.snapshot()['scans'] == 2

print('\n=== force overrides the clock but NOT the frozen feed ===')
mod.market_session_state = lambda now=None: (False, 'weekend')
forced = svc.scan(SYMBOLS, force=True)          # feed still at bump=250
print(' ', forced.get('error'))
assert not forced['success'] and 'frozen' in forced['error'].lower(), forced
assert svc.snapshot()['scans'] == 2
mod.market_session_state = real_state

print('\n=== purge_non_sessions clears weekend rows ===')
import sqlite3
with sqlite3.connect(svc.db_path) as conn:
    conn.execute('''INSERT INTO oi_crossover_series
        (trade_date, ts, symbol, spot, ce_oi, pe_oi, ce_chg, pe_chg, pcr)
        VALUES ('2026-08-15','2026-08-15T11:00:00','NIFTY',1.0,1,1,1,1,1.0)''')
    conn.execute('''INSERT INTO oi_crossover_events
        (trade_date, symbol, ts, direction, ce_chg, pe_chg)
        VALUES ('2026-08-15','NIFTY','2026-08-15T11:00:00','BULL',1,1)''')
    conn.commit()
before = svc.available_dates()
removed = svc.purge_non_sessions()
after = svc.available_dates()
print('  dates before:', before, '\n  removed:', removed, '\n  dates after:', after)
assert '2026-08-15' in before and '2026-08-15' not in after
assert removed['series'] >= 1 and removed['events'] == 1
# Nothing left may fall on a weekend. Asserted as a property rather than a
# count because the scans above are stamped with *today's* date, so on a
# weekend run they are legitimately swept up by this purge too.
assert not [d for d in after
            if datetime.strptime(d, '%Y-%m-%d').weekday() >= 5], after

print('\nALL CHECKS PASSED')
