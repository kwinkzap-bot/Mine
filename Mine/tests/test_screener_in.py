"""service/screener_in.py — parsing a Screener.in company page.

The page is synthetic but laid out like the real one (top-ratios list,
data-table sections, ranges-table boxes, the peers breadcrumb). Nothing
here reaches the network.
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.service import screener_in as si
import trading_app.service.value_screen as vs


def _ratios(**kw):
    lis = ''.join(f'<li class="flex"><span class="name">\n {k} \n</span>'
                  f'<span class="nowrap value">₹ <span class="number">{v}</span> Cr.</span></li>'
                  for k, v in kw.items())
    return f'<ul id="top-ratios">{lis}</ul>'


def _section(sid, cols, rows):
    head = '<thead><tr><th></th>' + ''.join(f'<th>\n {c} \n</th>' for c in cols) + '</tr></thead>'
    body = ''.join('<tr><td class="text">' + name + '</td>' + ''.join(f'<td>{v}</td>' for v in vals) + '</tr>'
                   for name, vals in rows.items())
    return (f'<section id="{sid}"><table class="data-table responsive-text-nowrap">'
            f'{head}<tbody>{body}</tbody></table></section>')


def _growth(title, cells):
    rows = ''.join(f'<tr><td>{k}:</td><td>{v}</td></tr>' for k, v in cells.items())
    return f'<table class="ranges-table"><tr><th colspan="2">{title}</th></tr>{rows}</table>'


def page(pe='29.1', eps_annual=('8.64', '-3.76', '-2.73'), borrow='203', bank=False):
    years = ['Mar 2024', 'Mar 2025', 'Mar 2026']
    cls = (['Financial Services', 'Financial Services', 'Banks', 'Private Sector Bank'] if bank else
           ['Consumer Discretionary', 'Consumer Durables', 'Consumer Durables', 'Household Appliances'])
    peers = ('<section id="peers"><p class="sub"><i></i>' +
             ''.join(f'<a href="/market/x/" title="t">{c}</a><i></i>' for c in cls) + '</p></section>')
    return (
        '<h1 class="margin-0">Crompton Greaves Consumer Electricals Ltd</h1>'
        + _ratios(**{'Market Cap': '14,174', 'Current Price': '220', 'High / Low': '311 / 200',
                     'Stock P/E': pe, 'Book Value': '47.1', 'Dividend Yield': '1.36',
                     'ROCE': '18.1', 'ROE': '10.6'})
        + _section('quarters', ['Dec 2025', 'Mar 2026', 'Jun 2026', 'Sep 2026'],
                   {'EPS in Rs': ['-8.29', '2.0', '2.18', '1.85']})
        + _section('profit-loss', years + ['TTM'],
                   {'Operating Profit': ['901', '827', '895', '882'],
                    'Net Profit +': ['564', '-231', '500', '-163'],
                    'EPS in Rs': list(eps_annual) + ['-2.73']})
        + _section('balance-sheet', years + ['Sep 2026'],
                   {'Equity Capital': ['129', '129', '129', '129'],
                    'Reserves': ['3,261', '2,838', '2,904', '2,904'],
                    ('Borrowing' if bank else 'Borrowings +'): ['479', '199', '203', borrow]})
        + _section('cash-flow', years,
                   {'Cash from Operating Activity +': ['843', '750', '723'],
                    'Free Cash Flow': ['763', '647', '644']})
        + _growth('Compounded Sales Growth', {'3 Years': '6%', 'TTM': '12%'})
        + _growth('Compounded Profit Growth', {'10 Years': '%', '3 Years': '-10%', 'TTM': '0%'})
        + peers
    )


def test_top_ratios_and_classification():
    r = si.parse_page('CROMPTON', page())
    assert r['source'] == 'screener' and r['company'].startswith('Crompton')
    assert (r['price'], r['pe'], r['book_value'], r['div_yield'], r['roce']) == (220, 29.1, 47.1, 1.36, 18.1)
    assert r['roe'] == pytest.approx(0.106)
    assert r['market_cap'] == pytest.approx(14174e7)
    assert (r['macro'], r['sector'], r['industry']) == ('Consumer Discretionary', 'Consumer Durables',
                                                         'Household Appliances')


def test_eps_is_the_one_behind_the_pe_not_the_reported_loss():
    # Reported TTM EPS is -2.73 (a write-off); Screener's P/E is on profit
    # before it, so EPS = price ÷ P/E and the stock keeps its P/E reading.
    r = si.parse_page('CROMPTON', page())
    assert r['eps'] == pytest.approx(220 / 29.1)
    loss = si.parse_page('X', page(pe=''))
    assert loss['pe'] is None and loss['eps'] == pytest.approx(-2.73)


def test_balance_sheet_cash_flow_and_growth():
    r = si.parse_page('CROMPTON', page())
    assert r['de'] == pytest.approx(203 / (129 + 2904))
    assert r['ev_ebitda'] == pytest.approx((14174 + 203) / 882)
    assert r['fcf'] == pytest.approx(644e7) and r['ocf'] == pytest.approx(723e7)
    # Cash conversion pairs the last FULL year: FY26 profit, not TTM.
    assert r['net_income'] == pytest.approx(500e7)
    assert r['earnings_growth'] == pytest.approx(-0.10) and r['growth_basis'] == '3y'


def test_a_bank_reads_borrowing_and_is_financial():
    r = si.parse_page('HDFCBANK', page(bank=True))
    assert r['sector'] == 'Financial Services' and r['industry'] == 'Private Sector Bank'
    assert vs.is_financial(r)
    assert vs.score([r])[0]['gates']['de'] is None


def test_growth_and_track_record():
    r = si.parse_page('CROMPTON', page())
    assert r['sales_g3'] == 6 and r['profit_g3'] == -10
    # Sep 2026 profit -163 against ... the quarters table has only EPS here, so no YoY
    assert r['q_profit_yoy'] is None
    # FY24 564 → FY25 -231 → FY26 500: one rise in two, and a loss
    assert r['profit_up_years'] is None and r['loss_5y'] is True


def test_a_consolidated_page_without_a_price_falls_back_to_standalone(monkeypatch):
    blank = page().replace('<span class="number">220</span>', '<span class="number"></span>')
    seen = []
    monkeypatch.setattr(si, '_get', lambda url: seen.append(url) or (blank if 'consolidated' in url else page()))
    assert si.top_ratios(si.fetch_page('BANDHANBNK'))['Current Price'] == 220
    assert len(seen) == 2 and not seen[1].endswith('consolidated/')


def test_quarter_yoy():
    q = [100, 110, 90, 120, 130, 99]
    assert si.yoy(q) == pytest.approx(-10.0)            # 99 against 110
    assert si.yoy(q, 1) == pytest.approx(30.0)          # 130 against 100
    assert si.yoy([-5, 1, 1, 1, 8]) == 100.0            # a loss turned to profit
    assert si.yoy([1, 1, 1]) is None


def test_eps_steps_annual_then_quarterly_ttm():
    steps = dict(si.eps_steps(page()))
    assert steps[datetime(2024, 5, 30)] == 8.64            # FY24 known 60 days after 31 Mar
    assert datetime(2026, 5, 30) in steps
    # Sep 2026 quarter: trailing four = -8.29 + 2.0 + 2.18 + 1.85, from 45 days after.
    assert steps[datetime(2026, 11, 14)] == pytest.approx(-2.26)
    assert all(d != datetime(2026, 1, 1) for d in steps)    # the TTM column is not a year


def test_eps_steps_drop_a_pre_merger_year():
    steps = dict(si.eps_steps(page(eps_annual=('8800', '60.73', '52.0'))))
    assert datetime(2024, 5, 30) not in steps
    assert steps[datetime(2025, 5, 30)] == 60.73


def test_numbers_and_month_ends():
    assert si._num('1,234.5') == 1234.5 and si._num('-12%') == -12 and si._num('') is None
    assert si._month_end('Feb 2024') == datetime(2024, 2, 29)
    assert si._month_end('Dec 2015 (S)') == datetime(2015, 12, 31)


def test_has_accounts_picks_standalone_when_consolidated_is_empty():
    assert si.has_accounts(page())
    empty = page(eps_annual=('', '', '')).replace('<td>564</td><td>-231</td><td>500</td><td>-163</td>',
                                                 '<td></td><td></td><td></td><td></td>')
    empty = empty.replace('<td>-2.73</td>', '<td></td>')
    assert not si.has_accounts(empty)


def test_fetch_one_falls_back_to_yahoo(monkeypatch):
    monkeypatch.setattr(vs, 'fetch_screener', lambda s: None)
    monkeypatch.setattr(vs, 'fetch_yahoo', lambda s: {'symbol': s, 'source': 'yahoo'})
    assert vs.fetch_one('NEWLIST')['source'] == 'yahoo'
    monkeypatch.setattr(vs, 'fetch_screener', lambda s: {'symbol': s, 'source': 'screener'})
    assert vs.fetch_one('TCS')['source'] == 'screener'


def test_a_throttled_screener_does_not_fall_back_to_yahoo(monkeypatch):
    def throttled(s):
        raise si.Throttled('429')
    monkeypatch.setattr(vs, 'fetch_screener', throttled)
    monkeypatch.setattr(vs, 'fetch_yahoo', lambda s: pytest.fail('fell back to Yahoo'))
    assert vs.fetch_one('TCS') is None


def test_a_throttle_pauses_every_request(monkeypatch):
    import requests

    class Resp:
        def __init__(self, code):
            self.status_code, self.text = code, 'ok'

        def raise_for_status(self):
            pass

    codes = iter([503, 200])
    slept = []
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Resp(next(codes)))
    monkeypatch.setattr(si.time, 'sleep', slept.append)
    monkeypatch.setattr(si, '_last_request', [0.0])
    monkeypatch.setattr(si, '_blocked_until', [0.0])
    assert si._get('https://x') == 'ok'
    assert slept and slept[-1] > si.COOL_DOWN - 1        # the retry waited out the cool-down


def test_maintenance_stops_a_refresh_at_once(tmp_path, monkeypatch):
    import time as _t
    monkeypatch.setattr(vs, '_db_path', lambda: str(tmp_path / 'v.db'))
    vs._tasks.clear()
    def down(s):
        raise si.Unavailable('Screener.in is down for maintenance')
    monkeypatch.setattr(vs, 'fetch_screener', down)
    monkeypatch.setattr(vs, 'fetch_yahoo', lambda s: pytest.fail('fell back to Yahoo'))
    t = vs.start_refresh('fno', force=True, symbols=['A', 'B', 'C'])
    for _ in range(200):
        if vs.task_status(t['id'])['status'] != 'running':
            break
        _t.sleep(0.01)
    st = vs.task_status(t['id'])
    assert st['status'] == 'error' and 'maintenance' in st['error']
    vs._tasks.clear()


def test_a_maintenance_page_is_not_retried(monkeypatch):
    import requests

    class Resp:
        status_code, text = 503, '<title>Screener is undergoing maintenance</title>'

    calls = []
    monkeypatch.setattr(requests, 'get', lambda *a, **k: calls.append(1) or Resp())
    monkeypatch.setattr(si.time, 'sleep', lambda s: None)
    with pytest.raises(si.Unavailable):
        si._get('https://x')
    assert len(calls) == 1


def test_a_stopped_refresh_does_not_wait_for_the_queue(tmp_path, monkeypatch):
    import time as _t
    monkeypatch.setattr(vs, '_db_path', lambda: str(tmp_path / 'v.db'))
    vs._tasks.clear()

    def down(s):
        _t.sleep(0.02)                      # a paced request, then the maintenance page
        raise si.Unavailable('Screener.in is down for maintenance')

    monkeypatch.setattr(vs, 'fetch_one', down)
    start = _t.time()
    t = vs.start_refresh('fno', force=True, symbols=[f'S{i}' for i in range(300)])
    while vs.task_status(t['id'])['status'] == 'running' and _t.time() - start < 5:
        _t.sleep(0.01)
    # 300 queued × 0.02 s ÷ 3 workers would be ~2 s had it waited for the queue.
    assert vs.task_status(t['id'])['status'] == 'error'
    assert _t.time() - start < 0.5
    vs._tasks.clear()
