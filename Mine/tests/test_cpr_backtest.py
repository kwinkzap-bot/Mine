"""Trend page — CPR Manual vs Chart.

The service reads a session the way the Pine script draws it (daily CPR
from yesterday's H/L/C, the 1-hour chart's CPR being the WEEKLY one, the
PDH↔R1 box being the CPR's own height) and compares it to the manual
sheet. Pure functions over bar lists, so no broker and no create_app().
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.service import cpr_backtest_service as svc
from route_app import build_route_app

IST = timezone(timedelta(hours=5, minutes=30))


# ── pivot maths ───────────────────────────────────────────────────────────

def test_levels_match_the_pine_formulas():
    lv = svc.levels(100.0, 90.0, 97.0)
    pp = (100 + 90 + 97) / 3
    assert lv['pp'] == pytest.approx(pp)
    assert lv['bc'] == pytest.approx(min(95.0, 2 * pp - 95.0))
    assert lv['tc'] == pytest.approx(max(95.0, 2 * pp - 95.0))
    assert lv['r1'] == pytest.approx(2 * pp - 90)
    assert lv['s1'] == pytest.approx(2 * pp - 100)
    assert lv['cr3'] == pytest.approx(97 + 10 * 1.1 / 4)


def test_box_height_equals_cpr_height():
    """R1 - PDH reduces to (2C - H - L)/3 = TC - BC, which is why the sheet's
    'Boxes' column is measured off the CPR width."""
    lv = svc.levels(25400.0, 25100.0, 25350.0)
    assert lv['r1'] - lv['pdh'] == pytest.approx(lv['tc'] - lv['bc'])
    assert abs(lv['pdl'] - lv['s1']) == pytest.approx(lv['tc'] - lv['bc'])


def test_direction_inside_then_by_pivot():
    y = {'pp': 100.0, 'bc': 98.0, 'tc': 102.0}
    assert svc.cpr_direction({'pp': 100.5, 'bc': 99.0, 'tc': 101.0}, y) == 'Inside'
    assert svc.cpr_direction({'pp': 103.0, 'bc': 101.0, 'tc': 105.0}, y) == 'Asc'
    assert svc.cpr_direction({'pp': 97.0, 'bc': 95.0, 'tc': 99.0}, y) == 'Dec'


def test_side_reads_the_pivot_and_flags_the_band():
    lv = {'pp': 100.0, 'bc': 98.0, 'tc': 102.0}
    assert svc.side_of(101.0, lv) == {'side': 'Above', 'in_cpr': True}
    assert svc.side_of(97.0, lv) == {'side': 'Below', 'in_cpr': False}


@pytest.mark.parametrize('pct,label', [(0.05, 'Small'), (0.129, 'Small'), (0.13, 'Medium'),
                                       (0.2, 'Medium'), (0.24, 'Big'), (0.4, 'Big')])
def test_box_size_cutoffs(pct, label):
    assert svc.box_size(pct) == label


# ── first candle ──────────────────────────────────────────────────────────

def _lv():
    return svc.levels(25400.0, 25100.0, 25350.0)


def test_first_candle_doji_and_touch():
    lv = _lv()
    c = {'open': lv['pp'] + 1, 'high': lv['pp'] + 20, 'low': lv['pp'] - 20, 'close': lv['pp'] - 2}
    d = svc.describe_first_candle(c, lv, avg_range=40.0)
    assert d['colour'] == 'Doji'
    assert d['text'].startswith('Indecision (doji)')
    assert 'CPR' in d['touched']


def test_first_candle_strong_big_green_near_r1():
    lv = _lv()
    lo = lv['r1'] - 45                      # high 15 under R1: outside the 0.05% touch, inside "near"
    c = {'open': lo + 2, 'high': lo + 30, 'low': lo, 'close': lo + 28}
    d = svc.describe_first_candle(c, lv, avg_range=15.0)
    assert d['colour'] == 'Green'
    assert d['text'].startswith('Strong Big Green')
    assert d['near'] == 'R1'


def test_touched_list_is_capped_at_three_nearest():
    lv = _lv()
    c = {'open': lv['s2'], 'high': lv['r2'], 'low': lv['s2'], 'close': lv['r2'] - 1}
    d = svc.describe_first_candle(c, lv, avg_range=None)
    assert len(d['touched']) == 3


@pytest.mark.parametrize('note,colour', [
    ('Strong big candle green', 'Green'), ('Red candles', 'Red'),
    ('In decision candle(doji)', 'Doji'), ('Touched and below prev High', None)])
def test_manual_colour_keyword(note, colour):
    assert svc.manual_colour(note) == colour


# ── trade simulation ──────────────────────────────────────────────────────

def _bars(*ohlc):
    return [{'time': f'09:{15 + 5 * i:02d}', 'open': o, 'high': h, 'low': l, 'close': c}
            for i, (o, h, l, c) in enumerate(ohlc)]


def test_simulate_buy_fills_after_the_setup_bar_and_hits_target():
    bars = _bars((100, 105, 99, 104),      # 09:15 setup — never a fill
                 (104, 106, 103, 105),     # fills at 105
                 (105, 111, 104, 110))     # target 110
    r = svc.simulate_trade(bars, 'BUY', 105, 110, 101)
    assert r['result'] == 'Target' and r['pnl'] == 5.0
    assert r['entry_time'] == '09:20' and r['exit_time'] == '09:25'


def test_simulate_sell_stop_is_negative():
    bars = _bars((100, 101, 99, 100), (100, 101, 94, 95), (95, 99, 94, 98.5))
    r = svc.simulate_trade(bars, 'SELL', 95, 90, 99)
    assert r['result'] == 'SL' and r['pnl'] == -4.0


def test_simulate_both_in_one_bar_and_no_fill():
    bars = _bars((100, 101, 99, 100), (100, 112, 90, 100))
    assert svc.simulate_trade(bars, 'BUY', 100, 110, 95)['result'] == 'Both'
    assert svc.simulate_trade(bars, 'BUY', 200, 210, 195)['result'] == 'No fill'


def test_simulate_squares_off_at_1515():
    bars = _bars((100, 101, 99, 100), (100, 103, 99, 102))
    bars += [{'time': '15:10', 'open': 102, 'high': 103, 'low': 101, 'close': 102.5},
             {'time': '15:15', 'open': 104, 'high': 105, 'low': 103, 'close': 103.5},
             {'time': '15:25', 'open': 103, 'high': 103, 'low': 100, 'close': 100.5}]
    r = svc.simulate_trade(bars, 'BUY', 100, 110, 95)
    assert r['result'] == 'EOD' and r['pnl'] == 4.0 and r['exit_time'] == '15:15'


def test_simulate_falls_back_to_the_last_close_on_a_short_day():
    bars = _bars((100, 101, 99, 100), (100, 103, 99, 102))
    r = svc.simulate_trade(bars, 'BUY', 100, 110, 95)
    assert r['result'] == 'EOD' and r['pnl'] == 2.0


# ── analyse end to end ────────────────────────────────────────────────────

def _daily(start, n, base=25000.0):
    """n daily bars on consecutive weekdays, gently rising."""
    out, d, i = [], start, 0
    while len(out) < n:
        if d.weekday() < 5:
            px = base + 20 * i
            out.append({'date': d, 'open': px, 'high': px + 100, 'low': px - 100, 'close': px + 60})
            i += 1
        d += timedelta(days=1)
    return out


def test_analyse_builds_chart_side_and_matches():
    daily = _daily(date(2026, 1, 1), 40)
    target_day = daily[-1]['date']
    prev = daily[-2]
    lv = svc.levels(prev['high'], prev['low'], prev['close'])
    # 09:15 candle closing above the pivot, then bars that walk to a target.
    intraday = {target_day.isoformat(): [
        {'time': '09:15', 'open': lv['pp'] + 5, 'high': lv['pp'] + 30, 'low': lv['pp'] - 5, 'close': lv['pp'] + 25},
        {'time': '09:20', 'open': lv['pp'] + 25, 'high': lv['pp'] + 40, 'low': lv['pp'] + 20, 'close': lv['pp'] + 30},
        {'time': '09:25', 'open': lv['pp'] + 30, 'high': lv['pp'] + 90, 'low': lv['pp'] + 28, 'close': lv['pp'] + 85},
    ]}
    for b in daily[:-1]:
        intraday[b['date'].isoformat()] = [{'time': '09:15', 'open': b['open'], 'high': b['open'] + 30,
                                            'low': b['open'] - 30, 'close': b['open'] + 10}]
    manual = [{'date': target_day.isoformat(), 'price_vs_daily': 'Above', 'price_vs_hourly': 'Above',
               'cpr_type': 'Medium', 'cpr_direction': 'Asc', 'first_candle': 'Strong candle green',
               'boxes': 'Small', 'trade': 'BUY', 'entry': round(lv['pp'] + 30, 2),
               'target': round(lv['pp'] + 80, 2), 'sl': round(lv['pp'] - 20, 2),
               'result': 'Target', 'pnl': 50.0, 'reason': None}]

    res = svc.analyse(manual, daily, intraday)
    row = res['rows'][0]
    c = row['chart']
    assert c['price_vs_daily'] == 'Above'
    assert c['cpr_direction'] == 'Asc'            # every bar sits 20 above the last
    assert c['weekly'] is not None
    assert c['box_pts'] == pytest.approx(abs(lv['r1'] - lv['pdh']), abs=0.1)
    assert c['box_upper']['top'] >= c['box_upper']['bottom']
    assert row['match']['price_vs_daily'] is True
    assert row['match']['cpr_direction'] is True
    t = row['trades'][0]
    assert t['chart']['result'] == 'Target' and t['chart']['pnl'] == 50.0
    assert t['chart']['reasons']['entry'].startswith('BUY')
    assert t['match'] is True
    assert res['summary']['agreement']['price_vs_daily'] == {'match': 1, 'total': 1, 'pct': 100.0}
    assert res['summary']['agreement']['result'] == {'match': 1, 'total': 1, 'pct': 100.0}
    assert res['summary']['manual']['pnl'] == 50.0
    assert res['summary']['chart']['pnl'] == 50.0


def test_analyse_folds_a_multi_trade_day_into_one_row():
    daily = _daily(date(2026, 1, 1), 30)
    ds = daily[-1]['date'].isoformat()
    prev = daily[-2]
    lv = svc.levels(prev['high'], prev['low'], prev['close'])
    intraday = {ds: [{'time': '09:15', 'open': lv['pp'] + 5, 'high': lv['pp'] + 30,
                      'low': lv['pp'] - 5, 'close': lv['pp'] + 25}]}
    base = {'date': ds, 'price_vs_daily': 'Above', 'price_vs_hourly': None, 'cpr_type': None,
            'cpr_direction': None, 'first_candle': None, 'boxes': None, 'trade': 'BUY',
            'entry': lv['pp'] + 10, 'target': lv['pp'] + 60, 'sl': lv['pp'] - 20,
            'result': 'Target', 'pnl': 50.0, 'reason': None}
    res = svc.analyse([dict(base), dict(base, trade='SELL', result='SL', pnl=-30.0),
                       dict(base, trade=None, pnl=None, result=None)], daily, intraday)
    assert len(res['rows']) == 1                      # one grid row per date
    assert len(res['rows'][0]['trades']) == 2         # the untraded copy adds no trade
    assert [t['manual']['trade'] for t in res['rows'][0]['trades']] == ['BUY', 'SELL']
    assert res['summary']['rows'] == 2
    assert res['summary']['sessions'] == 1
    assert res['summary']['agreement']['price_vs_daily']['total'] == 1
    assert res['summary']['manual'] == {'trades': 2, 'wins': 1, 'pnl': 20.0}


def test_analyse_flags_missing_bars_instead_of_failing():
    daily = _daily(date(2026, 1, 1), 5)
    manual = [{'date': '2026-03-02', 'price_vs_daily': 'Above'},
              {'date': daily[-1]['date'].isoformat(), 'price_vs_daily': 'Above'}]
    res = svc.analyse(manual, daily, {})
    assert res['rows'][0]['chart'] is None and 'daily' in res['rows'][0]['error']
    assert res['rows'][0]['trades'] == []
    assert res['rows'][1]['chart'] is None and '5-minute' in res['rows'][1]['error']


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = build_route_app()
    app.secret_key = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_authenticated'] = True
            sess['username'] = 'test-user'
        yield c


def test_route_serves_the_comparison(client, monkeypatch):
    monkeypatch.setattr(svc, 'compare', lambda symbol: {'success': True, 'symbol': symbol, 'rows': []})
    body = client.get('/api/trend/cpr-backtest?symbol=nifty').get_json()
    assert body['success'] and body['symbol'] == 'NIFTY'


def test_route_404s_without_a_sheet(client, monkeypatch):
    def boom(symbol):
        raise svc.NoManualData('none')
    monkeypatch.setattr(svc, 'compare', boom)
    r = client.get('/api/trend/cpr-backtest?symbol=BANKNIFTY')
    assert r.status_code == 404 and r.get_json()['no_manual'] is True


def test_route_401s_without_a_provider(client, monkeypatch):
    import trading_app.service.multichart_service as mc

    def boom(symbol):
        raise mc.ProviderUnavailable('login')
    monkeypatch.setattr(svc, 'compare', boom)
    r = client.get('/api/trend/cpr-backtest')
    assert r.status_code == 401 and r.get_json()['auth_required'] is True


def test_route_rejects_a_bad_symbol(client):
    assert client.get('/api/trend/cpr-backtest?symbol=NSE:X').status_code == 400


def test_the_imported_nifty_sheet_loads():
    doc = svc.load_manual('NIFTY')
    assert doc['symbol'] == 'NIFTY' and len(doc['rows']) >= 19
    assert all(r['price_vs_daily'] in ('Above', 'Below') for r in doc['rows'])


# ── trade reasons ─────────────────────────────────────────────────────────

def _ladder():
    lv = svc.levels(25400.0, 25100.0, 25350.0)
    c1 = {'high': lv['pp'] + 30, 'low': lv['pp'] - 30}
    return lv, svc._reason_ladder(lv, {'pp': 25000.0, 'bc': 24950.0, 'tc': 25050.0}, c1, lv['pp'] + 5)


def test_nearest_level_fit_bands():
    lv, ladder = _ladder()
    assert svc.nearest_level(lv['r1'] + 5, ladder)['fit'] == 'at'
    near = svc.nearest_level(lv['r1'] + 40, ladder)
    assert near['name'] == 'R1' and near['fit'] == 'near' and near['delta'] == 40
    assert svc.nearest_level(lv['r3'] + 500, ladder)['fit'] == 'none'


def test_stop_reads_the_level_it_hides_behind():
    lv, ladder = _ladder()
    # A BUY stop 10 under S1 must be read against S1 (above it), not a
    # level below it, even if that one is closer.
    n = svc.nearest_level(lv['s1'] - 10, ladder, side='above')
    assert n['name'] == 'S1' and n['delta'] == -10


def test_explain_trade_phrases_by_direction():
    lv, ladder = _ladder()
    candle = {'colour': 'Green'}
    buy = svc.explain_trade({'trade': 'BUY', 'entry': lv['pdh'] + 3, 'target': lv['r2'] - 2, 'sl': lv['r1'] - 5},
                            ladder, candle, 'Above')
    assert 'breakout above PDH' in buy['entry'] and 'price above CPR' in buy['entry']
    assert buy['target'].startswith('Target') and 'next resistance R2' in buy['target']
    assert 'below R1' in buy['sl']
    assert buy['rr'] == round(abs(lv['r2'] - 2 - (lv['pdh'] + 3)) / abs(lv['pdh'] + 3 - (lv['r1'] - 5)), 2)

    sell = svc.explain_trade({'trade': 'SELL', 'entry': lv['pdh'] - 3, 'target': lv['s1'] + 2, 'sl': lv['r1'] + 5},
                             ladder, {'colour': 'Red'}, 'Below')
    assert 'rejection from PDH' in sell['entry']
    assert 'next support S1' in sell['target']
    assert 'above R1' in sell['sl']


def test_explain_trade_needs_a_full_trade():
    lv, ladder = _ladder()
    assert svc.explain_trade({'trade': None}, ladder, {'colour': 'Red'}, 'Above') is None
    assert svc.explain_trade({'trade': 'BUY', 'entry': 1.0, 'target': None, 'sl': 2.0}, ladder,
                             {'colour': 'Red'}, 'Above') is None


def test_target_on_merged_daily_and_weekly_cpr_is_named_as_one_zone():
    """1 Jan 2026: daily TC 26,113 and weekly TC 26,122 sit on top of each
    other — a SELL target at 26,112 is 'the merged CPR', not just 'TC'."""
    lv = svc.levels(26187.95, 25969.0, 26129.6)
    wlv = {'pp': 26095.77, 'bc': 26069.03, 'tc': 26122.5}
    c1 = {'high': 26195.35, 'low': 26163.1}
    ladder = svc._reason_ladder(lv, wlv, c1, 26173.3)
    assert svc.cprs_merged(ladder)
    r = svc.explain_trade({'trade': 'SELL', 'entry': 26160.0, 'target': 26112.0, 'sl': 26200.0},
                          ladder, {'colour': 'Doji'}, 'Above')
    assert 'merged daily + weekly CPR' in r['target'] and 'TC 26,113' in r['target']
    assert 'first line reached would be WTC 26,122' in r['target']
    assert svc.first_cpr_line(26160.0, False, ladder) == ('WTC', 26122.5)
    r2 = svc.explain_trade({'trade': 'SELL', 'entry': 26160.0, 'target': 26122.0, 'sl': 26200.0},
                           ladder, {'colour': 'Doji'}, 'Above')
    assert 'first line reached: WTC 26,122' in r2['target']


def test_cprs_apart_are_not_merged():
    lv = svc.levels(25400.0, 25100.0, 25350.0)
    ladder = svc._reason_ladder(lv, {'pp': 24000.0, 'bc': 23950.0, 'tc': 24050.0},
                                {'high': lv['pp'] + 30, 'low': lv['pp'] - 30}, lv['pp'])
    assert not svc.cprs_merged(ladder)


def test_simulate_starts_after_the_setup_candle():
    """20 Feb 2026 in miniature: the entry price trades BEFORE the setup
    candle; with the setup time given the replay must ignore that."""
    bars = _bars((100, 101, 99, 100),      # 09:15
                 (100, 106, 99, 105),      # 09:20 — trades through 105, then
                 (105, 106, 94, 95),       # 09:25 — would stop out a 09:20 fill
                 (95, 97, 94, 96),         # 09:30 — the setup candle
                 (99, 107, 99, 106),       # 09:35 — fills at 105 ...
                 (106, 112, 105, 111))     # 09:40 — ... and reaches 110
    naive = svc.simulate_trade(bars, 'BUY', 105, 110, 98)
    assert naive['result'] == 'SL' and naive['entry_time'] == '09:20'
    r = svc.simulate_trade(bars, 'BUY', 105, 110, 98, setup_time='09:30')
    assert r['result'] == 'Target' and r['entry_time'] == '09:35' and r['exit_time'] == '09:40'


# ── the Update button: extend the sheet from the chart ────────────────────

def test_last_complete_session_waits_for_the_close():
    before = datetime(2026, 9, 16, 15, 29, tzinfo=IST)
    after = datetime(2026, 9, 16, 15, 30, tzinfo=IST)
    assert svc.last_complete_session(before) == date(2026, 9, 15)
    assert svc.last_complete_session(after) == date(2026, 9, 16)


def test_candle_words_speak_the_sheet():
    assert svc.candle_words({'text': 'Strong small Red · near PDL', 'colour': 'Red'}) == 'Strong small candle red near Prev Low'
    assert svc.candle_words({'text': 'Doji · touched TC, BC', 'colour': 'Doji'}) == 'In decision candle(doji) near TC'
    assert svc.candle_words({'text': 'Green', 'colour': 'Green'}) == 'Candle green'


def _synthetic_session(n_daily=30):
    """Daily history plus one session of 5-minute bars that fills a rule trade."""
    daily = _daily(date(2026, 8, 1), n_daily)
    ds = daily[-1]['date'].isoformat()
    prev = daily[-2]
    lv = svc.levels(prev['high'], prev['low'], prev['close'])
    # Opens well above the CPR, then a strong red candle dips into it and
    # closes back above -> the rule's "rejection from the CPR" BUY.
    top = lv['tc']
    bars = [
        {'time': '09:15', 'open': top + 60, 'high': top + 70, 'low': top + 40, 'close': top + 50},
        {'time': '09:20', 'open': top + 50, 'high': top + 55, 'low': top - 5, 'close': top + 45},
        {'time': '09:25', 'open': top + 45, 'high': top + 70, 'low': top + 44, 'close': top + 65},
        {'time': '09:30', 'open': top + 65, 'high': top + 90, 'low': top + 60, 'close': top + 85},
    ]
    bars += [{'time': f'{h:02d}:{m:02d}', 'open': top + 85, 'high': top + 300, 'low': top + 80, 'close': top + 250}
             for h in range(10, 16) for m in range(0, 60, 5) if (h, m) < (15, 30)]
    intraday = {d['date'].isoformat(): [{'time': '09:15', 'open': d['open'], 'high': d['open'] + 30,
                                         'low': d['open'] - 30, 'close': d['open'] + 10}]
                for d in daily[:-1]}
    intraday[ds] = bars
    return ds, daily, intraday


def test_chart_row_is_shaped_like_an_imported_row():
    ds, daily, intraday = _synthetic_session()
    res = svc.analyse([{'date': ds}], daily, intraday)
    row = svc.chart_row(res['rows'][0], intraday[ds])
    imported = svc.load_manual('NIFTY')['rows'][0]
    assert set(imported) <= set(row)                       # every key the import writes
    assert row['date'] == ds
    assert row['price_vs_daily'] == 'Above' and row['cpr_direction'] == 'Asc'
    assert row['first_candle'].endswith('candle red') or 'candle' in row['first_candle']
    assert row['note'].startswith('Analysis added from the chart')
    assert 'Trade by rule' in row['note']
    if row['trade']:
        assert row['result'] in ('Target', 'SL', 'EOD', 'Both')
        assert row['setup_time'] is not None and row['reason']


def test_chart_row_is_none_without_bars():
    assert svc.chart_row({'date': '2026-09-16', 'chart': None, 'error': 'no bars'}, []) is None


def test_extend_appends_only_the_sessions_after_the_sheet(tmp_path, monkeypatch):
    ds, daily, intraday = _synthetic_session()
    last_have = daily[-3]['date']                          # the sheet ends two sessions back
    monkeypatch.setattr(svc, 'MANUAL_DIR', str(tmp_path))
    with open(tmp_path / 'NIFTY.json', 'w') as fh:
        json.dump({'symbol': 'NIFTY', 'source': 'test', 'rows': [
            {'date': last_have.isoformat(), 'price_vs_daily': 'Above', 'trade': None}]}, fh)
    calls = []

    def fake_fetch(symbol, first, last, fresh=False):
        calls.append((symbol, first, last, fresh))
        return daily, intraday
    monkeypatch.setattr(svc, 'fetch_bars', fake_fetch)

    out = svc.extend('NIFTY', upto=daily[-1]['date'])
    assert calls == [('NIFTY', last_have + timedelta(days=1), daily[-1]['date'], True)]
    assert out['added'] == [daily[-2]['date'].isoformat(), ds]
    doc = json.load(open(tmp_path / 'NIFTY.json'))
    assert [r['date'] for r in doc['rows']] == [last_have.isoformat()] + out['added']
    assert doc['extended_at']
    assert all('note' in r for r in doc['rows'][1:])

    # Idempotent: a second click has nothing after the new last date.
    again = svc.extend('NIFTY', upto=daily[-1]['date'])
    assert again['added'] == [] and again['last'] == ds
    assert len(calls) == 1                                  # no fetch when nothing to add


def test_analyse_reads_yesterdays_cpr_and_whether_it_was_virgin():
    ds, daily, intraday = _synthetic_session()
    res = svc.analyse([{'date': ds}], daily, intraday)
    c = res['rows'][0]['chart']
    prev2 = daily[-3]
    y = svc.levels(prev2['high'], prev2['low'], prev2['close'])
    assert c['prev_cpr']['bc'] == round(y['bc'], 2) and c['prev_cpr']['tc'] == round(y['tc'], 2)
    prev = daily[-2]
    assert c['prev_cpr']['virgin'] == (not (prev['low'] <= y['tc'] and prev['high'] >= y['bc']))


def test_chart_rows_writes_one_row_per_trade_and_extend_keeps_both(tmp_path, monkeypatch):
    ds, daily, intraday = _synthetic_session()
    bars = intraday[ds]
    two = [({'trade': 'SELL', 'entry': 100.0, 'target': 90.0, 'sl': 105.0}, 'first', 1,
            {'result': 'SL', 'pnl': -5.0, 'entry_time': '09:25', 'exit_time': '09:30'}),
           ({'trade': 'SELL', 'entry': 98.0, 'target': 80.0, 'sl': 101.0}, 'virgin CPR second', 3,
            {'result': 'Target', 'pnl': 18.0, 'entry_time': '09:35', 'exit_time': '10:00', 'after': 'SL'})]
    import trading_app.service.cpr_trade_rule as rule
    monkeypatch.setattr(rule, 'propose_all', lambda chart, bars: two)
    res = svc.analyse([{'date': ds}], daily, intraday)
    rows = svc.chart_rows(res['rows'][0], bars)
    assert [r['setup_time'] for r in rows] == [bars[1]['time'], bars[3]['time']]
    assert rows[0]['result'] == 'SL' and rows[1]['result'] == 'Target' and rows[1]['reason'] == 'virgin CPR second'
    assert rows[0]['first_candle'] == rows[1]['first_candle']            # the analysis repeats, as the sheet does
    assert rows[0]['note'].startswith('Analysis added from the chart') and 'Trade by rule:' in rows[0]['note']
    assert rows[1]['note'].startswith("Trade by rule (2nd, after the first trade's SL):")
    assert svc.chart_row(res['rows'][0], bars) == rows[0]

    monkeypatch.setattr(svc, 'MANUAL_DIR', str(tmp_path))
    with open(tmp_path / 'NIFTY.json', 'w') as fh:
        json.dump({'symbol': 'NIFTY', 'source': 'test', 'rows': [
            {'date': daily[-2]['date'].isoformat(), 'price_vs_daily': 'Above', 'trade': None}]}, fh)
    monkeypatch.setattr(svc, 'fetch_bars', lambda symbol, first, last, fresh=False: (daily, intraday))
    out = svc.extend('NIFTY', upto=daily[-1]['date'])
    assert out['added'] == [ds]                                           # one session ...
    doc = json.load(open(tmp_path / 'NIFTY.json'))
    assert [r['date'] for r in doc['rows']][1:] == [ds, ds]                # ... two rows
    assert len(out['trades']) == 2


def test_extend_route(client, monkeypatch):
    monkeypatch.setattr(svc, 'extend', lambda symbol: {'success': True, 'symbol': symbol, 'added': ['2026-09-16']})
    body = client.post('/api/trend/cpr-backtest/update', json={'symbol': 'nifty'}).get_json()
    assert body['success'] and body['symbol'] == 'NIFTY' and body['added'] == ['2026-09-16']
    assert client.get('/api/trend/cpr-backtest/update').status_code == 405


def test_simulate_fills_at_the_open_when_a_bar_opens_past_the_entry():
    """A stop entry: the 13:15 bar on 3 Jun 2026 opened at 23,303 over a
    BUY at 23,300 and never traded 23,300 itself — it is filled, at the open."""
    bars = [
        {'time': '13:10', 'open': 23287.0, 'high': 23298.35, 'low': 23275.7, 'close': 23298.35},
        {'time': '13:15', 'open': 23302.95, 'high': 23326.3, 'low': 23300.3, 'close': 23307.8},
        {'time': '13:20', 'open': 23309.95, 'high': 23400.0, 'low': 23308.65, 'close': 23395.0},
    ]
    sim = svc.simulate_trade(bars, 'BUY', 23300.0, 23393.0, 23274.0, '13:10')
    assert sim['result'] == 'Target' and sim['entry_time'] == '13:15' and sim['fill'] == 23302.95
    assert sim['pnl'] == pytest.approx(23393.0 - 23302.95)


def test_virgin_cprs_lists_the_untouched_ones_lowest_first():
    def bar(d, h, l, c):
        return {'date': date(2026, 4, d), 'open': c, 'high': h, 'low': l, 'close': c}
    daily = [bar(1, 110, 100, 105),   # -> CPR for the 2nd around 105
             bar(2, 130, 120, 125),   # gapped over it: the 2nd's CPR (from the 1st) untouched
             bar(3, 150, 140, 145),   # the 3rd's CPR (from the 2nd, ~125) untouched too
             bar(4, 146, 141, 143),   # the 4th's CPR (from the 3rd, ~145) — touched by the 4th itself
             bar(5, 160, 150, 155)]
    out = svc.virgin_cprs(daily, 4)   # as of the 5th
    assert [v['date'] for v in out] == ['2026-04-02', '2026-04-03']
    assert out[0]['bc'] < out[1]['bc']


def test_strategy_of_reads_the_rules_reasons():
    assert svc.strategy_of('09:15 closed inside the CPR 22,771-22,902 on a 125-pt candle — no stop at its far end; 10:00 candle closed above the CPR at 22,914; 10:05 cross candle came back to TC') == 'Inside CPR → cross candle'
    assert svc.strategy_of('09:15 closed inside the CPR 25,600-25,640; 09:40 candle closed above it') == 'Inside CPR → breakout candle'
    assert svc.strategy_of('09:15 candle opened at 24,152 inside PDL/S1 24,117-24,155, traded over and under it and closed back inside at 24,119, but a 70-pt candle is too big to enter under — waited for 09:20') == 'Big 09:15 break → 09:20 entry'
    assert svc.strategy_of('gap-down open 23,270 under S1/PDL, never reached; base of 7 small candles') == 'Gap day'
    assert svc.strategy_of('gap-up; 10:15 candle came to R1/PDH and reversed up on a 64-pt candle — too big to enter over; Cam R3 sits inside the box, so that close over the box is the breakout and the retest to Cam R3 is the trade') == 'Gap day → Cam R3 · S3 retest'
    assert svc.strategy_of('09:25 candle rejected from R1 (high 24,306, closed 24,287) -> SELL under it') == 'Rejection from PDH/R1 · PDL/S1'
    assert svc.strategy_of('09:15 candle rejected from the CPR (high 23,448 into 23,393-23,453)') == 'Rejection from the CPR'
    assert svc.strategy_of(None) == 'Other'
    assert svc.strategy_of('09:15 opened below the CPR and closed above it; 11:05 tested the CPR (low 23,214) and the 11:10 candle closed green on it -> BUY') == 'CPR retest after a failed box break'


def test_summarise_tallies_strategies():
    rows = [
        {'date': '2026-04-07', 'match': {}, 'trades': [
            {'manual': {'reason': '10:05 cross candle came back to TC', 'pnl': -38.0, 'result': 'SL'}, 'chart': None, 'match': None},
        ]},
        {'date': '2026-04-09', 'match': {}, 'trades': [
            {'manual': {'reason': '09:35 cross candle came back to BC', 'pnl': 53.0, 'result': 'Target'}, 'chart': None, 'match': None},
            {'manual': {'reason': 'gap-up; base', 'pnl': None, 'result': 'Both'}, 'chart': None, 'match': None},
        ]},
    ]
    st = {s['name']: s for s in svc.summarise(rows)['strategies']}
    cc = st['Inside CPR → cross candle']
    assert (cc['trades'], cc['wins'], cc['losses'], cc['pnl'], cc['win_pct'], cc['last']) == (2, 1, 1, 15.0, 50.0, '2026-04-09')
    assert cc['targets'] == 1 and cc['stops'] == 1
    assert st['Gap day']['flat'] == 1 and st['Gap day']['win_pct'] is None


def test_every_strategy_has_a_description():
    for _t, name, how in svc._STRATEGIES:
        assert svc.strategy_how(name) == how
        assert all(how[k] for k in ('setup', 'entry', 'stop', 'target', 'example'))
    assert 'no current setup matches' in svc.strategy_how('Other')['setup']
    rows = [{'date': '2026-04-07', 'match': {}, 'trades': [
        {'manual': {'reason': '10:05 cross candle came back to TC', 'pnl': -38.0, 'result': 'SL'}, 'chart': None, 'match': None}]}]
    st = svc.summarise(rows)['strategies'][0]
    assert st['name'] == 'Inside CPR → cross candle' and st['how']['setup'].startswith('09:15 closes inside the daily CPR')
