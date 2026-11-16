"""Smart Wheel engine (Backtest/smart_wheel_engine.py) on synthetic stocks, its
Breeze price mapping on fake fetchers, and the route end to end — no broker,
no create_app()."""
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import smart_wheel_engine as sw


def frame(closes, start='2018-01-01'):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({'open': c, 'high': c, 'low': c, 'close': c, 'volume': 1e6}, index=idx)


def path(*segments):
    """segments: (to_price, n_days) — a straight line to each price."""
    out = [segments[0][0]]
    for to, n in segments[1:]:
        a = out[-1]
        out.extend(a + (to - a) * (i + 1) / n for i in range(n))
    return out


class FakePrices:
    """Time value = 2 % of spot × the share of a month left; strikes on a 10 grid."""
    name = 'fake'

    def __init__(self, stocks):
        self.stocks = stocks

    def strike(self, sym, expiry, right, level, mode, day, spot):
        return math.floor(level / 10) * 10 if mode == 'below' else round(level / 10) * 10

    def close(self, sym, expiry, strike, right, day):
        spot = self.stocks[sym].close_on(day)
        if spot is None:
            return None
        intrinsic = max(spot - strike, 0) if right == 'CE' else max(strike - spot, 0)
        return intrinsic + spot * 0.02 * min(1.0, max((expiry - day).days, 0) / 30)


# Climb to an all-time high of 1,000, fall 18 % to 820 by mid-April 2019, then the scenario.
BASE = [(700, 0), (1000, 300), (820, 40), (820, 15)]
KW = dict(dd_years=1, support_days=60, min_dist_pct=5, max_positions=5, brokerage=0, carry_pct=0)


def run(closes, start='2019-05-01', end='2019-06-30', sector=None, **kw):
    st = {'AAA': sw.Stock('AAA', frame(closes), sector)}
    return sw.simulate(st, FakePrices(st), date.fromisoformat(start), date.fromisoformat(end), **{**KW, **kw}), st


def test_monthly_expiries_are_last_thursdays_then_tuesdays_and_holiday_adjusted():
    sessions = [d.date() for d in pd.bdate_range('2019-04-01', '2025-11-30')]
    sessions.remove(date(2019, 4, 25))                     # pretend the last Thursday is a holiday
    exp = sw.monthly_expiries(sessions, extra_months=0)
    assert date(2019, 4, 24) in exp and date(2019, 5, 30) in exp
    assert date(2025, 10, 28) in exp                        # the Tuesday regime


def test_a_put_that_stays_out_of_the_money_books_its_whole_premium():
    r, _ = run(path(*BASE, (830, 60)))
    t = r['trades'][0]
    assert t['reason'] == 'Put expired worthless' and not t['assigned']
    assert t['put_strike'] == 770 and t['put_exit'] == 0
    assert t['pnl_rupees'] == pytest.approx(t['put_premium'] * t['qty'], rel=1e-6)
    assert t['qty'] == round(500_000 / t['spot'])


def test_run_through_buys_the_future_sells_the_atm_call_and_a_breakout_closes_both():
    closes = path(*BASE, (820, 3), (740, 3), (740, 2), (900, 2), (900, 30))
    r, st = run(closes)
    t = r['trades'][0]
    assert t['assigned'] and t['reason'] == 'Breakout above the call — both closed'
    call = t['calls'][0]
    assert call['strike'] == round(t['fut_entry'] / 10) * 10        # ATM
    assert t['fut_exit'] >= call['strike'] + call['premium']
    # The put was bought back at a loss. Future + short ATM call earn at most the call's
    # premium plus (strike − future entry) — the covered call's cap — and the exit waits
    # until nearly all of that is in (≤ 10 % of the premium left as time value).
    cap = (call['premium'] + call['strike'] - t['fut_entry']) * t['qty']
    assert t['put_pnl'] < 0 and t['fut_pnl'] > 0
    assert 0.9 * cap - 1 <= t['fut_pnl'] + t['call_pnl'] <= cap + 1
    assert t['pnl_rupees'] == pytest.approx(t['put_pnl'] + t['fut_pnl'] + t['call_pnl'], abs=0.05)


def test_a_fall_through_the_futures_breakeven_is_a_stop():
    closes = path(*BASE, (820, 3), (740, 3), (740, 2), (650, 6), (650, 30))
    r, _ = run(closes)
    t = r['trades'][0]
    assert t['reason'] == 'Stop — future under its breakeven'
    stop = t['fut_entry'] - t['calls'][0]['premium']
    assert t['fut_exit'] <= stop and t['pnl_rupees'] < 0


def test_sideways_after_the_run_through_rolls_the_call_and_lowers_the_breakeven():
    closes = path(*BASE, (820, 3), (745, 3), (745, 70))
    r, _ = run(closes, end='2019-07-31')
    t = r['trades'][0]
    assert t['assigned'] and t['n_calls'] >= 2
    assert t['reason'] == 'Open at end'
    assert t['call_pnl'] > 0


def test_filters_near_high_and_deep_history_fall():
    near = path((700, 0), (1000, 300), (950, 40), (950, 60))          # only 5 % off the high
    r, _ = run(near)
    assert r['trades'] == [] and r['filtered']['near_high'] >= 1
    deep = path((700, 0), (1000, 200), (600, 30), (1000, 80), (820, 40), (820, 60))   # a 40 % fall within the year
    r, _ = run(deep)
    assert r['trades'] == [] and r['filtered']['drawdown'] >= 1
    r, _ = run(deep, max_dd_pct=45)
    assert len(r['trades']) >= 1


def test_small_premium_is_skipped():
    r, _ = run(path(*BASE, (830, 60)), min_prem_pct=20)
    assert r['trades'] == [] and r['skipped']['premium'] >= 1


def test_one_per_sector_and_slot_cap():
    closes = path(*BASE, (830, 60))
    stocks = {s: sw.Stock(s, frame(closes), 'Banks') for s in ('AAA', 'BBB')}
    stocks['CCC'] = sw.Stock('CCC', frame(closes), 'IT')
    r = sw.simulate(stocks, FakePrices(stocks), date(2019, 5, 1), date(2019, 5, 29), **KW)
    assert sorted(t['symbol'] for t in r['trades'])[:1] in (['AAA'], ['BBB'])
    assert len(r['trades']) == 2 and r['skipped']['sector'] == 1
    r = sw.simulate(stocks, FakePrices(stocks), date(2019, 5, 1), date(2019, 5, 29),
                    **{**KW, 'max_positions': 1, 'one_per_sector': False})
    assert len(r['trades']) == 1 and r['skipped']['slots'] == 2


def test_summary_and_daily_curve():
    r, _ = run(path(*BASE, (830, 60)))
    s = sw.summarise(r, date(2019, 5, 1), date(2019, 6, 30))
    assert s['total_trades'] == len(r['trades']) and s['capital'] == 1_000_000
    assert s['final_equity'] == pytest.approx(1_000_000 + s['total_pnl_rupees'], abs=1)
    assert s['by_year'][0]['year'] == '2019'


def test_model_prices_are_black_scholes_on_realised_vol():
    closes = path(*BASE, (830, 60))
    st = {'AAA': sw.Stock('AAA', frame(closes))}
    mp = sw.ModelPrices(st, iv_mult=1.0)
    day = date(2019, 5, 2)
    q = mp.close('AAA', date(2019, 5, 30), 770, 'PE', day)
    assert q is not None and q >= 0
    assert sw.bs_price(100, 90, 0.0, 0.3, 'PE') == 0 and sw.bs_price(100, 110, 0.0, 0.3, 'PE') == 10
    # Put-call parity holds for the model.
    c, p = sw.bs_price(100, 100, 0.1, 0.25, 'CE'), sw.bs_price(100, 100, 0.1, 0.25, 'PE')
    assert c - p == pytest.approx(100 - 100 * math.exp(-sw.RATE * 0.1), abs=1e-6)


def test_breeze_prices_map_adjusted_levels_onto_the_traded_strikes():
    """The files are bonus-adjusted (raw = 2 × file); strikes are asked for on the raw
    scale, a missing fine strike falls back to a coarser listed one, and premiums
    come back on the file's scale."""
    closes = path(*BASE, (830, 60))
    st = {'AAA': sw.Stock('AAA', frame(closes))}
    asked = []

    def fetch_cash(sym, a, b):
        return [{'date': pd.Timestamp(d), 'close': c * 2} for d, c in zip(st['AAA'].dates, st['AAA'].close)
                if a <= d <= b]

    def fetch_option(sym, expiry, strike, right, a, b):
        asked.append(strike)
        if strike % 50:                       # only 50-multiples were listed
            return []
        return [{'date': pd.Timestamp('2019-05-02'), 'close': 30.0}]

    bp = sw.BreezePrices(st, fetch_option, fetch_cash, expiries=[date(2019, 4, 25), date(2019, 5, 30)])
    day = date(2019, 5, 2)
    spot = st['AAA'].close_on(day)
    k = bp.strike('AAA', date(2019, 5, 30), 'PE', spot * 0.95, 'below', day, spot)
    assert asked[0] == sw.strike_ladder(spot * 0.95 * 2, spot * 2)[0] and asked[0] % 50
    assert k * 2 % 50 == 0
    assert bp.close('AAA', date(2019, 5, 30), k, 'PE', day) == 15.0
    assert bp.factor('AAA', day) == pytest.approx(2.0)


def test_route_runs_on_model_prices(monkeypatch):
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    files = {'AAA': {'df': frame(path(*BASE, (830, 60)))}}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr('trading_app.filters.candle_store._load_disk', lambda sym: files.get(sym))
    app = build_route_app()
    body = {'start_date': '2019-05-01', 'end_date': '2019-06-30', 'prices': 'model',
            'symbols': 'AAA, NOFILE', 'dd_years': 1, 'min_prem_pct': 0}
    with app.test_request_context(json=body):
        task_id = api_mod.run_smart_wheel_backtest_api.__wrapped__().get_json()['task_id']
    for _ in range(300):
        task = api_mod._sw_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    s = task['payload']['summary']
    assert s['symbols_missing'] == ['NOFILE'] and s['prices'] == 'model'
    assert s['total_trades'] >= 1
    with app.test_request_context(json={**body, 'prices': 'guess'}):
        assert api_mod.run_smart_wheel_backtest_api.__wrapped__()[1] == 400
    # Breeze without an ICICI login is refused up front, not run on nothing.
    monkeypatch.setattr(api_mod, 'get_icici_adapter', lambda: None)
    with app.test_request_context(json={**body, 'prices': 'breeze'}):
        assert api_mod.run_smart_wheel_backtest_api.__wrapped__()[1] == 503


SMALL_GRID = {'max_dd_pct': [30.0, 45.0], 'min_off_high_pct': [10.0, 15.0], 'min_dist_pct': [5.0, 7.0],
              'support_days': [0, 60], 'min_prem_pct': [0.5], 'up_tv_pct': [10.0, 50.0]}


def test_every_sweep_row_equals_a_direct_run_of_its_own_settings():
    closes = path(*BASE, (820, 3), (740, 3), (740, 2), (900, 2), (900, 30), (780, 20), (780, 60))
    deep = path((700, 0), (1000, 200), (600, 30), (1000, 80), (820, 40), (820, 60))
    stocks = {'AAA': sw.Stock('AAA', frame(closes), 'IT'), 'BBB': sw.Stock('BBB', frame(deep[:len(closes)]), 'Banks')}
    prices = FakePrices(stocks)
    start, end = date(2019, 5, 1), date(2019, 9, 30)
    rows = sw.sweep(stocks, prices, start, end, base=KW, grid=SMALL_GRID)
    assert len(rows) == len(sw.sweep_combos(SMALL_GRID)) == 32
    for row in rows[::5]:
        combo = {k: row[k] for k in sw.SWEEP_KEYS}
        direct = sw.score(sw.simulate(stocks, prices, start, end, **{**KW, **combo}), start, end)
        assert {k: row[k] for k in direct} == direct
    b = sw.board(rows, min_trades=1, size=3)
    assert b and all(r['total_trades'] >= 1 for r in b)
    assert [r['cagr_dd'] for r in b] == sorted((r['cagr_dd'] for r in b), reverse=True)
    assert sw.board(rows, min_trades=10_000) == []


def test_the_full_grid_is_the_documented_size():
    assert len(sw.sweep_combos()) == 2304


def test_optimise_route_on_model_prices(monkeypatch):
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    files = {'AAA': {'df': frame(path(*BASE, (830, 60)))}}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr('trading_app.filters.candle_store._load_disk', lambda sym: files.get(sym))
    monkeypatch.setattr(sw, 'SWEEP_GRID', {**SMALL_GRID, 'min_prem_pct': [0.0]})   # a flat fake stock has ~no model premium
    monkeypatch.setattr(sw, 'SWEEP_MIN_TRADES', 1)
    app = build_route_app()
    body = {'start_date': '2019-05-01', 'end_date': '2019-06-30', 'prices': 'model', 'symbols': 'AAA', 'dd_years': 1}
    with app.test_request_context(json=body):
        task_id = api_mod.run_smart_wheel_optimise.__wrapped__().get_json()['task_id']
    for _ in range(500):
        task = api_mod._sw_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    pl = task['payload']
    assert pl['total_combos_tested'] == 32 and pl['results'] and pl['prices'] == 'model'


def test_breeze_sweep_fetchers_never_make_a_request(monkeypatch):
    from trading_app.app.routes import api as api_mod
    from trading_app.service import icici_data_service as ids
    monkeypatch.setattr(ids, '_slice_cache_get', lambda key: None)
    fetch_option, fetch_cash = api_mod._sw_cached_fetchers()          # no adapter, no budget: cache only
    assert fetch_option('LT', date(2020, 1, 30), 1200, 'PE', date(2019, 12, 1), date(2020, 1, 30)) == []
    assert fetch_cash('LT', date(2020, 1, 1), date(2020, 12, 31)) == []
