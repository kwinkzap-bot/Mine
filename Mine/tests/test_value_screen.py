"""The /scanners Value tab — service/value_screen.py and its three routes.

Nothing here reaches Yahoo: the scorer is pure, the cache is pointed at a
scratch sqlite file, and the refresh is handed a fake fetcher. No
create_app(): the bare route app carries the URL map and starts nothing.
"""

import os
import sys
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_app.service.value_screen as vs
from route_app import build_route_app


def rec(symbol, sector='Technology', **kw):
    base = {'symbol': symbol, 'sector': sector, 'pe': 20.0, 'eps': 10.0, 'pb': 3.0,
            'book_value': 60.0, 'price': 200.0, 'roe': 0.20, 'de': 0.2,
            'ev_ebitda': 12.0, 'fcf': 5e9, 'ocf': 9e9, 'net_income': 1e10,
            'earnings_growth': 0.10, 'market_cap': 1e11, 'own_pe_median': 20.0}
    base.update(kw)
    return base


# ── scoring ──────────────────────────────────────────────────────────────

def test_the_cheapest_on_every_reading_ranks_first():
    rows = vs.score([
        rec('DEAR', pe=40, ev_ebitda=25, fcf=1e9, own_pe_median=20),
        rec('MID', pe=20, ev_ebitda=12, fcf=4e9, own_pe_median=20),
        rec('CHEAP', pe=10, ev_ebitda=6, fcf=9e9, own_pe_median=20),
    ])
    assert [r['symbol'] for r in rows] == ['CHEAP', 'MID', 'DEAR']
    assert rows[0]['value_score'] == 100.0
    assert rows[-1]['value_score'] == 0.0


def test_a_sector_needs_three_peers_for_its_median():
    rows = vs.score([rec('A', pe=10), rec('B', pe=30)])
    assert all(r['pe_sector'] is None and r['factors']['pe_vs_sector'] is None for r in rows)


def test_peers_are_the_industry_when_it_has_three_else_the_sector():
    fs = 'Financial Services'
    recs = [rec(f'BANK{i}', sector=fs, industry='Banks - Regional', pe=6 + i) for i in range(3)]
    recs += [rec(f'INS{i}', sector=fs, industry='Insurance - Life', pe=60 + i) for i in range(2)]
    rows = {r['symbol']: r for r in vs.score(recs)}
    # Banks against banks, not against a sector median dragged up by insurers.
    assert rows['BANK0']['pe_sector'] == 7 and rows['BANK0']['peer_group'] == 'Banks - Regional'
    # Two insurers are too few: they fall back to the whole sector (6,7,8,60,61).
    assert rows['INS0']['pe_sector'] == 8 and rows['INS0']['peer_group'] == fs


def test_a_loss_maker_takes_no_pe_reading_and_fails_cash_conversion():
    row = vs.score([rec('LOSS', pe=None, eps=-5, net_income=-1e9)])[0]
    assert row['factors']['pe_vs_sector'] is None and row['factors']['pe_vs_own'] is None
    assert row['factors']['peg'] is None
    assert row['gates']['cash'] is False
    assert row['graham'] is None and row['margin'] is None


def test_financials_skip_debt_and_cash_and_use_book_value():
    banks = [rec(f'B{i}', sector='Financial Services', pb=1.0 + i, de=None,
                 ocf=-5e10, ev_ebitda=None, fcf=None) for i in range(3)]
    rows = {r['symbol']: r for r in vs.score(banks)}
    b0 = rows['B0']
    assert b0['gates']['de'] is None and b0['gates']['cash'] is None
    assert b0['factors']['pb_vs_sector'] == pytest.approx(1.0 / 2.0)
    assert b0['factors']['ev_vs_sector'] is None and b0['factors']['fcf_yield'] is None
    # A lender's negative operating cash flow is not held against it.
    assert b0['quality'] is True


def test_quality_needs_every_judged_gate_and_at_least_two():
    good = vs.score([rec('OK')])[0]
    assert good['quality'] is True
    high_debt = vs.score([rec('DEBT', de=1.3)])[0]
    assert high_debt['gates']['de'] is False and high_debt['quality'] is False
    looser = vs.score([rec('DEBT', de=1.3)], {'max_de': 1.5})[0]
    assert looser['quality'] is True
    blank = vs.score([rec('BLANK', roe=None, de=None, ocf=None, earnings_growth=None)])[0]
    assert blank['quality'] is False


def test_too_few_readings_leave_no_score():
    row = vs.score([rec('THIN', pe=None, ev_ebitda=None)])[0]
    assert row['n_factors'] < vs.MIN_FACTORS and row['value_score'] is None


def test_graham_number_and_margin():
    row = vs.score([rec('G', eps=4, book_value=10, price=24)])[0]
    assert row['graham'] == pytest.approx(30.0)        # sqrt(22.5 * 4 * 10)
    assert row['margin'] == pytest.approx(20.0)        # (30 - 24) / 30


def test_fair_value_uses_the_lower_pe_and_the_margin_sets_the_buy_price():
    recs = [rec(f'P{i}', pe=pe, eps=10, price=10 * pe, own_pe_median=own)
            for i, (pe, own) in enumerate([(10, 30), (20, 12), (30, 40)])]
    rows = {r['symbol']: r for r in vs.score(recs)}
    # Peer P/E = 20 for all three; P1's own 12 is lower and wins.
    assert rows['P0']['fair_value'] == pytest.approx(200.0)
    assert rows['P1']['fair_value'] == pytest.approx(120.0)
    assert rows['P0']['buy_below'] == pytest.approx(160.0)       # 20 % margin
    assert rows['P0']['in_buy_zone'] and not rows['P2']['in_buy_zone']
    assert rows['P0']['upside'] == pytest.approx(100.0)
    looser = {r['symbol']: r for r in vs.score(recs, {'mos': 0})}
    assert looser['P0']['buy_below'] == pytest.approx(200.0)
    loss = vs.score([rec('L', pe=None, eps=-2)])[0]
    assert loss['fair_value'] is None and loss['buy_below'] is None and not loss['in_buy_zone']


def _row(**kw):
    base = {'pe': 15.0, 'value_score': 80.0, 'n_factors': 5, 'quality': True, 'upside': 40.0,
            'in_buy_zone': True, 'gates': {'roe': True, 'de': True, 'cash': True, 'growth': True},
            'strength': 'Strong', 'strength_passed': 6, 'strength_readable': 6,
            'strength_checks': {k: True for k in vs.STRENGTH}}
    base.update(kw)
    return base


@pytest.mark.parametrize('kw, tier', [
    ({}, 5),                                                     # cheap, sound, under buy price
    ({'in_buy_zone': False, 'upside': 10.0}, 4),                 # good, not yet at the buy price
    ({'value_score': 65.0}, 4),
    ({'n_factors': 3}, 4),                                       # too few readings for the top
    ({'upside': 300.0}, 3),                                      # suspect upside is never Very Best
    ({'quality': False, 'gates': {'roe': False, 'de': True}}, 3),
    ({'value_score': 50.0}, 3),
    ({'upside': -5.0, 'in_buy_zone': False}, 3),
    ({'upside': -40.0, 'in_buy_zone': False}, 2),
    ({'value_score': 20.0}, 2),
    ({'pe': None}, 1),                                           # loss-maker
    ({'gates': {'roe': False, 'de': False}, 'quality': False}, 1),
    ({'value_score': None}, 0),
    ({'strength': 'Average'}, 4),                                # cheap but growth only average
    ({'strength': None}, 4),                                     # too little growth data for the top
    ({'strength': 'Weak'}, 3),                                   # cheap because it is shrinking
])
def test_rating_tiers(kw, tier):
    r = vs.rate(_row(**kw))
    assert r['rating_tier'] == tier and r['rating'] == vs.RATINGS[tier] and r['rating_why']


def test_rating_reasons_measure_against_fair_value():
    # 100 % upside = the price is HALF the fair value, i.e. 50 % under it.
    assert 'price 50% under fair value (100% upside)' in vs.rate(_row(upside=100.0))['rating_why'][0]
    # Upside -20 % = fair is 80 % of the price, so the price is 25 % over fair value.
    assert 'Price 25% above fair value' in vs.rate(_row(upside=-20.0, in_buy_zone=False))['rating_why']
    assert 'Price 0.3% above fair value' in vs.rate(_row(upside=-0.3, in_buy_zone=False))['rating_why']


def test_strength_reads_growth_and_track_record():
    strong = {'sales_g3': 15, 'profit_g3': 20, 'roe_3y': 18, 'q_sales_yoy': 8, 'q_profit_yoy': 12,
              'profit_up_years': 4, 'profit_years': 5, 'loss_5y': False}
    st = vs.strength(strong)
    assert st['label'] == 'Strong' and st['passed'] == 6
    # one miss is still Strong; shrinking sales and profit is not
    assert vs.strength({**strong, 'q_profit_yoy': -22})['label'] == 'Strong'
    weak = vs.strength({**strong, 'sales_g3': 2, 'profit_g3': -5, 'q_sales_yoy': -3, 'q_profit_yoy': -40})
    assert weak['label'] == 'Weak'
    assert vs.strength({**strong, 'q_sales_yoy': -1, 'q_profit_yoy': -1})['label'] == 'Average'
    # a loss in the last five years fails "steady" however many years grew
    assert vs.strength({**strong, 'loss_5y': True})['checks']['steady'] is False
    # a Yahoo record has only the quarter: too little to judge
    assert vs.strength({'q_sales_yoy': 5, 'q_profit_yoy': 5})['label'] is None


def test_a_weak_stock_says_which_growth_checks_failed():
    row = _row(strength='Weak', strength_passed=2, strength_readable=6,
               strength_checks={**{k: True for k in vs.STRENGTH}, 'sales_3y': False, 'profit_3y': False,
                                'q_sales': False, 'q_profit': False})
    why = ' '.join(vs.rate(row)['rating_why'])
    assert 'Fundamentals weak: 2 of 6' in why and 'Sales growth, 3-yr CAGR ≥ 10.0%' in why


def test_returns_from_the_daily_file():
    days = pd.date_range('2022-11-01', '2026-11-19', freq='B')
    closes = pd.Series(100.0, index=days)
    closes[days >= pd.Timestamp('2025-11-19')] = 150.0
    closes.iloc[-1] = 120.0
    out = vs._returns(closes, 120.0)
    assert out['ret_1y'] == pytest.approx(-20.0)       # 150 a year ago → 120
    assert out['ret_3y'] == pytest.approx((1.2 ** (1 / 3) - 1) * 100)
    short = vs._returns(closes[-100:], 120.0)
    assert short['ret_1y'] is None and short['ret_3y'] is None


def test_rows_are_ranked_best_tier_first():
    rows = vs.score([
        rec('DEAR', pe=40, ev_ebitda=25, fcf=1e9, own_pe_median=20, price=400, eps=10),
        rec('MID', pe=20, ev_ebitda=12, fcf=4e9, own_pe_median=20),
        rec('CHEAP', pe=10, ev_ebitda=6, fcf=9e9, own_pe_median=20, price=100, eps=10),
    ])
    assert [r['rank'] for r in rows] == [1, 2, 3]
    assert rows[0]['symbol'] == 'CHEAP' and rows[0]['rating_tier'] >= rows[-1]['rating_tier']


def test_percentile_ties_share_a_rank():
    p = vs._percentiles({'a': 1, 'b': 1, 'c': 5}, higher_is_cheaper=False)
    assert p['a'] == p['b'] == 75.0 and p['c'] == 0.0


def test_usd_filers_drop_the_mixed_currency_ratios():
    info = {'financialCurrency': 'USD', 'currency': 'INR', 'enterpriseToEbitda': 916.3,
            'freeCashflow': 3.3e9, 'debtToEquity': 45.0, 'returnOnEquity': 0.3}
    r = vs._record_from_info('INFY', info)
    assert r['ev_ebitda'] is None and r['fcf'] is None and r['mixed_currency'] == 'USD'
    assert r['de'] == pytest.approx(0.45) and r['roe'] == 0.3


# ── own P/E history ──────────────────────────────────────────────────────

def test_own_pe_uses_the_eps_in_force_each_day_and_dedupes_bars():
    days = pd.date_range('2023-01-02', periods=400, freq='B')
    closes = pd.Series(100.0, index=days)
    # A second stamp on the first day (the stock files mix 00:00 and 05:30).
    dup = pd.Series([100.0], index=[days[0] + pd.Timedelta(hours=5, minutes=30)])
    closes = pd.concat([closes, dup]).sort_index()
    steps = [(days[0].to_pydatetime(), 10.0), (days[200].to_pydatetime(), 5.0)]
    out = vs.own_pe_median(closes, steps)
    # 200 days at P/E 10, 200 at P/E 20 -> median 15.
    assert out['median'] == pytest.approx(15.0)
    assert out['years'] > 1


def test_own_pe_needs_a_year():
    days = pd.date_range('2025-01-01', periods=100, freq='B')
    assert vs.own_pe_median(pd.Series(100.0, index=days), [(days[0].to_pydatetime(), 10.0)]) is None


# ── current price ────────────────────────────────────────────────────────

def test_reprice_moves_the_price_ratios_but_not_the_statements():
    r = {**rec('R', price=200.0, pe=20.0, pb=2.0, eps=10.0, div_yield=1.0, market_cap=1000e7),
         'borrowings_cr': 100.0, 'op_profit_cr': 110.0, 'fetched_at': time.time() - 3 * 86400}
    today = time.strftime('%Y-%m-%d')
    out = vs.reprice(r, {'close': 220.0, 'date': today})
    assert out['price'] == 220 and out['price_date'] == today
    assert out['pe'] == pytest.approx(22.0) and out['pb'] == pytest.approx(2.2)
    assert out['div_yield'] == pytest.approx(1 / 1.1) and out['market_cap'] == pytest.approx(1100e7)
    assert out['ev_ebitda'] == pytest.approx((1100 + 100) / 110)
    assert out['eps'] == 10.0 and r['price'] == 200.0          # the cached record is untouched


def test_reprice_ignores_a_close_older_than_the_fetch():
    r = {**rec('R', price=200.0), 'fetched_at': time.time()}
    assert vs.reprice(r, {'close': 150.0, 'date': '2020-01-01'})['price'] == 200.0
    assert vs.reprice(r, None)['price'] == 200.0


def test_all_reads_the_nse_equity_list(tmp_path, monkeypatch):
    import json
    from trading_app.filters import stock_list_store
    f = tmp_path / 'eq.json'
    f.write_text(json.dumps({'symbols': ['tcs', 'INFY ', 'TCS', '']}))
    monkeypatch.setattr(stock_list_store, 'EQUITY_LIST_FILE', str(f))
    assert vs.universe('all') == ['INFY', 'TCS']


# ── cache, refresh and routes ────────────────────────────────────────────

@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, '_db_path', lambda: str(tmp_path / 'value.db'))
    vs._tasks.clear()
    yield
    vs._tasks.clear()


def _wait(task_id):
    for _ in range(200):
        if vs.task_status(task_id)['status'] != 'running':
            return vs.task_status(task_id)
        time.sleep(0.01)
    raise AssertionError('refresh never finished')


def test_refresh_fetches_only_what_is_stale(scratch_db):
    calls = []

    def fake(sym):
        calls.append(sym)
        return None if sym == 'BAD' else {**rec(sym), 'fetched_at': time.time(), 'q_sales_yoy': None}

    t = _wait(vs.start_refresh('fno', fetch=fake, symbols=['A', 'B', 'BAD'])['id'])
    assert t['done'] == 3 and t['failed'] == 1
    assert set(vs.read_cache(['A', 'B', 'BAD'])) == {'A', 'B'}

    calls.clear()
    t = vs.start_refresh('fno', fetch=fake, symbols=['A', 'B', 'BAD'])
    if t['status'] == 'running':
        t = _wait(t['id'])
    assert calls == ['BAD']

    calls.clear()
    _wait(vs.start_refresh('fno', force=True, fetch=fake, symbols=['A', 'B'])['id'])
    assert sorted(calls) == ['A', 'B']


def test_a_record_without_growth_fields_is_stale():
    now = time.time()
    fresh = {**rec('A'), 'fetched_at': now, 'q_sales_yoy': None}
    assert not vs.is_stale(fresh, now)
    assert vs.is_stale({**rec('A'), 'fetched_at': now}, now)          # cached before the Strength check
    assert vs.is_stale({**fresh, 'fetched_at': now - vs.CACHE_TTL - 1}, now)
    assert vs.is_stale(None, now)


def test_screen_scores_the_cache_and_never_fetches(scratch_db, monkeypatch):
    for s in ('A', 'B', 'C'):
        vs.store({**rec(s), 'fetched_at': time.time()})
    monkeypatch.setattr(vs, 'fetch_one', lambda s: pytest.fail('screen fetched'))
    out = vs.screen('fno', symbols=['A', 'B', 'C', 'MISSING'],
                    quotes=lambda s: {'close': 210.0, 'date': '2099-01-01'})
    assert out['total'] == 4 and out['cached'] == 3 and len(out['rows']) == 3
    assert all(r['price'] == 210.0 for r in out['rows'])


@pytest.fixture
def client(scratch_db, monkeypatch):
    monkeypatch.setattr(vs, 'universe', lambda kind: ['A', 'B', 'C'])
    for s in ('A', 'B', 'C'):
        vs.store({**rec(s, de=0.4 if s == 'A' else 2.0), 'fetched_at': time.time()})
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def test_route_scores_with_the_page_gates(client):
    strict = client.get('/api/scanners/value?universe=fno&max_de=0.5').get_json()
    assert strict['success'] and strict['cached'] == 3
    assert {r['symbol'] for r in strict['rows'] if r['quality']} == {'A'}
    loose = client.get('/api/scanners/value?max_de=3').get_json()
    assert all(r['quality'] for r in loose['rows'])
    # Garbage falls back to the defaults rather than erroring.
    assert client.get('/api/scanners/value?universe=x&max_de=abc').get_json()['gates']['max_de'] == 0.5
    assert client.get('/api/scanners/value?universe=all').get_json()['universe'] == 'all'


def test_route_refresh_and_status(client, monkeypatch):
    monkeypatch.setattr(vs, 'fetch_one', lambda s: {**rec(s), 'fetched_at': time.time()})
    body = client.post('/api/scanners/value/refresh', json={'universe': 'fno', 'force': True}).get_json()
    assert body['success']
    task = _wait(body['task']['id'])
    assert task['done'] == 3
    st = client.get(f"/api/scanners/value/status/{task['id']}").get_json()
    assert st['task']['status'] == 'done'
    assert client.get('/api/scanners/value/status/nope').status_code == 404


def test_the_page_script_parses():
    # A stray apostrophe in a single-quoted string once stopped the whole tab
    # loading (no grid, a dead Fetch button). node is on the dev machine; skip
    # where it is not.
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    js = os.path.join(os.path.dirname(__file__), '..', 'static', 'js', 'value_screen.js')
    out = subprocess.run([node, '--check', js], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
