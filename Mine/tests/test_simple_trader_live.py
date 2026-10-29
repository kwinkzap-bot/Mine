"""Simple Trader 1-7 live algos: the decisions (service/simple_trader_live_engine.py) replayed
against the Backtest engines they wrap, and the paper-mode thread (algo/simple_trader/), with
no broker and no network."""
from datetime import timedelta, timezone

import functools

import numpy as np
import pandas as pd
import pytest

from trading_app.algo.simple_trader import simple_trader_algo as algo
from trading_app.service import simple_trader_live_engine as sim

IST = timezone(timedelta(hours=5, minutes=30))


@functools.lru_cache(maxsize=None)
def _bars(bar, days=10, seed=3, start='2025-03-03'):
    """A wavy synthetic index with enough swing for every rule to fire once its thresholds are loosened."""
    rng = np.random.default_rng(seed)
    rows, k = [], 0
    for d in pd.bdate_range(start, periods=days):
        t0 = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15)
        for j in range(int(375 / bar)):
            base = 22000 + 220 * np.sin(k / 23.0) + 90 * np.sin(k / 7.0)
            o = base + rng.normal(0, 6)
            c = base + 6 + rng.normal(0, 6)
            rows.append((t0 + pd.Timedelta(minutes=bar * j), o, max(o, c) + abs(rng.normal(0, 4)),
                         min(o, c) - abs(rng.normal(0, 4)), c, 1000))
            k += 1
    return pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume']).set_index('date')


# Thresholds loosened so the synthetic series produces trades; the RULE is untouched.
LOOSE = {
    1: dict(rsi_level=52, bb_mult=0.3, wr_level=-70),
    2: dict(ema_period=20, rsi_entry=48, rsi_exit=52, cci_level=-20, wr_level=-40, bb_mult=0.3, cpr_narrow=100),
    3: dict(rsi_level=52, bb_mult=0.3),
    4: dict(rsi_level=52, bb_mult=0.3),
    5: dict(rsi_level=52, bb_mult=0.3, cci_level=20, wr_level=-70),
    6: dict(rsi_level=52, bb_mult=0.3, cpr_wide=100),
    7: dict(rsi_level=52, rsi_cap=100, bb_mult=0.3, wr_level=-70, cpr_narrow=100),
}


def _s(n):
    sp = sim.SPECS[n]
    return dict(sp.params, **LOOSE[n], spec=n, max_trades=sp.max_trades, entry_start=sp.entry_start)


def _replay(n, bars, date):
    """Feed one session's candles one at a time, closing a trade on its exit event, as the thread does."""
    sp, s = sim.SPECS[n], _s(n)
    day = sim.new_day(date)
    out = []
    for i in [i for i, t in enumerate(bars.index) if str(t.date()) == date]:
        for e in sim.on_bar(day, bars.iloc[:i + 1], s):
            if e['event'] == 'entry':
                out.append(('entry', e['trade']['signal_bar'], e['trade']))
            else:
                e['trade']['status'] = 'closed'
                out.append(('exit', e['bar'], e['reason']))
        end = bars.index[i] + pd.Timedelta(minutes=sp.bar)
        for e in sim.time_exits(day, end.strftime('%H:%M'), '15:14'):
            e['trade']['status'] = 'closed'
            out.append(('exit', None, 'Time Exit'))
    return out


@pytest.mark.parametrize('n', range(1, 8))
def test_the_live_replay_takes_the_backtests_trades(n):
    sp = sim.SPECS[n]
    bars = _bars(sp.bar).copy()
    kw = sim.engine_kwargs(sp, _s(n))
    trades, _ = sp.cls(bars.copy(), **kw).run()
    dates = sorted({str(t.date()) for t in bars.index})[-5:]
    want = [t for t in trades if t['entry_time'][:10] in dates]
    got = []
    for d in dates:
        got += [x for x in _replay(n, bars, d) if x[0] == 'entry']
    assert want, f'strategy {n}: the synthetic series fired nothing — loosen LOOSE[{n}]'
    # a backtest entry is filled at the OPEN of the candle after the signal candle
    sig_to_fill = lambda ts: str(pd.Timestamp(ts) + pd.Timedelta(minutes=sp.bar))
    assert [sig_to_fill(g[1]) for g in got] == [t['entry_time'] for t in want]


@pytest.mark.parametrize('n', (1, 3, 4, 5, 6, 7))
def test_supertrend_exits_land_on_the_candle_after_the_flip(n):
    sp = sim.SPECS[n]
    bars = _bars(sp.bar).copy()
    trades, _ = sp.cls(bars.copy(), **sim.engine_kwargs(sp, _s(n))).run()
    flips = {t['entry_time']: t for t in trades if t['exit_reason'].startswith('SuperTrend')}
    dates = sorted({str(t.date()) for t in bars.index})[-6:]
    seen = 0
    for d in dates:
        rep = _replay(n, bars, d)
        entries = [x for x in rep if x[0] == 'entry']
        exits = [x for x in rep if x[0] == 'exit' and x[1]]
        for en, ex in zip(entries, exits):
            bt = flips.get(str(pd.Timestamp(en[1]) + pd.Timedelta(minutes=sp.bar)))
            if bt:
                seen += 1
                assert bt['exit_time'] == str(pd.Timestamp(ex[1]) + pd.Timedelta(minutes=sp.bar))
    assert seen, f'strategy {n}: no SuperTrend exit to compare'


def test_strategy_2_exits_when_rsi_comes_back():
    sp = sim.SPECS[2]
    bars = _bars(sp.bar).copy()
    trades, _ = sp.cls(bars.copy(), **sim.engine_kwargs(sp, _s(2))).run()
    rsi_exits = {t['entry_time']: t for t in trades if t['exit_reason'] == 'RSI Exit'}
    assert rsi_exits
    seen = 0
    for d in sorted({str(t.date()) for t in bars.index})[-5:]:
        rep = _replay(2, bars, d)
        for en, ex in zip([x for x in rep if x[0] == 'entry'], [x for x in rep if x[0] == 'exit']):
            bt = rsi_exits.get(str(pd.Timestamp(en[1]) + pd.Timedelta(minutes=sp.bar)))
            if bt and ex[2] == 'RSI Exit':
                seen += 1
                assert bt['exit_time'] == str(pd.Timestamp(ex[1]) + pd.Timedelta(minutes=sp.bar))
    assert seen


def test_strategy_2_buys_a_put_the_others_a_call():
    assert [sim.SPECS[n].option_type for n in range(1, 8)] == ['CE', 'PE', 'CE', 'CE', 'CE', 'CE', 'CE']
    assert sim.option_type({'type': 'Short'}) == 'PE'


def test_the_backtest_forms_numbers_are_the_defaults():
    s = sim.SPECS
    assert (s[1].sl_pct, s[1].tsl_x, s[1].tsl_y, s[1].max_trades, s[1].params['bb_period']) == (13, 15, 10, 1, 15)
    assert (s[2].sl_pct, s[2].tsl_x, s[2].tsl_y) == (15, 5, 5) and s[2].params['wr_level'] == -85
    assert s[3].entry_start == '09:31' and s[3].params['st_mult'] == 2.3
    assert s[4].data == 'fut' and s[4].bar == 5 and s[4].fixed['require_green']
    assert s[5].data == 'fut' and s[5].bar == 3 and (s[5].tsl_x, s[5].tsl_y) == (0, 0)
    assert (s[6].symbol, s[6].premium, s[6].sl_pct) == ('SENSEX', 500, 17)
    assert (s[7].symbol, s[7].params['rsi_cap'], s[7].params['wr_level']) == ('SENSEX', 80, -10)


# ── the premium stop ─────────────────────────────────────────────────────────

def test_stop_is_sl_pct_under_entry_on_the_tick_rounded_up():
    st = sim.stop_level(251.35, 251.35, 13, 15, 10)
    assert st['stop'] == 218.7 and st['kind'] == 'Stop Loss'        # 251.35 × 0.87 = 218.6745 → up to the 0.05 tick


def test_trailing_stop_steps_up_tsl_y_of_entry_for_every_tsl_x_risen():
    assert sim.stop_level(100, 114.9, 13, 15, 10)['stop'] == 87.0          # not yet one 15 % step
    st = sim.stop_level(100, 131, 13, 15, 10)                              # two steps: 87 + 2 × 10
    assert st['stop'] == 107.0 and st['kind'] == 'Trailing SL'
    assert sim.stop_level(100, 300, 15, 0, 0)['stop'] == 85.0              # strategy 5 has no trail


def test_the_peak_only_ever_lifts_the_stop():
    assert sim.stop_level(100, 131, 13, 15, 10)['stop'] >= sim.stop_level(100, 100, 13, 15, 10)['stop']


# ── the thread, in paper mode ────────────────────────────────────────────────

class _FakeAlgo(algo.SimpleTraderAlgo):
    def __init__(self, username, profile, frame):
        super().__init__(username, profile)
        self.frame, self.px, self.alerts = frame, 250.0, []

    def _bars(self, today, now):
        cut = pd.Timestamp(now.replace(tzinfo=None)) - pd.Timedelta(minutes=self.spec.bar, seconds=5)
        return self.frame[self.frame.index <= cut]

    def _spot(self):
        return 23000.0

    def _premium(self, strike, option_type):
        return self.px

    def _chain_step(self):
        return 50

    def _alert(self, *a, **k):
        self.alerts.append(a)

    def _kite_premiums(self, strikes, option_type):
        return {}


@pytest.fixture()
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(algo, '_DIR', str(tmp_path))
    env = {f'ST3_{k.upper()}': str(v) for k, v in LOOSE[3].items()}
    env['ST3_MODE'] = 'paper'
    fake = lambda u, name, default='': env.get(name, default)
    monkeypatch.setattr(algo, '_uvar', fake)
    monkeypatch.setattr(algo.base, '_uvar', fake)
    monkeypatch.setattr(algo.base, 'accounts', lambda u, p=None: [{'instance': 1, 'type': 'zerodha', 'lots': 2,
                                                                   'name': 'Z1', 'stoppable': True}])
    monkeypatch.setattr(algo.base, '_flag', lambda u, name, default='false': False)
    return env


def _now_for(frame, i, bar):
    ts = frame.index[i] + pd.Timedelta(minutes=bar, seconds=10)
    return ts.to_pydatetime().replace(tzinfo=IST)


def _first_entry(n):
    sp = sim.SPECS[n]
    bars = _bars(sp.bar)
    trades, _ = sp.cls(bars.copy(), **sim.engine_kwargs(sp, _s(n))).run()
    t = next(t for t in trades if t['entry_time'][:10] == str(bars.index[-1].date()) or True)
    sig = pd.Timestamp(t['entry_time']) - pd.Timedelta(minutes=sp.bar)
    return bars, bars.index.get_loc(sig)


def test_paper_entry_then_the_premium_stop_exits_and_books_a_history_row(paper):
    bars, i = _first_entry(3)
    a = _FakeAlgo('u', algo.PROFILES[3], bars)
    a.tick(_now_for(bars, i, 3))
    t = a.state['day']['trades'][0]
    assert t['status'] == 'open' and t['mode'] == 'paper' and t['legs']['paper']['entry'] == 250.0
    a.tick(_now_for(bars, i, 3))                       # marks the leg and sets the stop: 15 % under 250
    assert t['stop'] == 212.5
    a.px = 230.0
    a.tick(_now_for(bars, i, 3))
    assert t['status'] == 'open'                       # above the stop: nothing happens
    a.px = 212.0
    a.tick(_now_for(bars, i, 3))
    assert t['status'] == 'closed' and t['result'] == 'Stop Loss'
    leg = t['legs']['paper']
    assert leg['exit'] == 212.0 and leg['open'] is False
    hist = algo.load_history(algo.PROFILES[3])
    assert len(hist) == 1 and hist[0]['result'] == 'Stop Loss' and hist[0]['pnl'] < 0


def test_the_trail_lifts_the_stop_with_the_peak(paper):
    bars, i = _first_entry(3)
    a = _FakeAlgo('u', algo.PROFILES[3], bars)
    a.tick(_now_for(bars, i, 3))
    t = a.state['day']['trades'][0]
    a.tick(_now_for(bars, i, 3))
    a.px = 300.0                                       # +20 %: one 15 % step → base 212.5 + 10 % of 250
    a.tick(_now_for(bars, i, 3))
    a.tick(_now_for(bars, i, 3))
    assert t['peak'] == 300.0 and t['stop'] == 237.5
    a.px = 237.0
    a.tick(_now_for(bars, i, 3))
    assert t['status'] == 'closed' and t['result'] == 'Trailing SL'


def test_after_a_stop_the_next_signal_re_enters_up_to_max_trades():
    sp = sim.SPECS[3]
    bars = _bars(sp.bar)
    s = _s(3)
    i = _first_entry(3)[1]
    day = sim.new_day(str(bars.index[i].date()))
    sim.on_bar(day, bars.iloc[:i + 1], s)
    day['trades'][0]['status'] = 'closed'              # the option's stop ended it, as the thread does
    later = [k for k in range(i + 1, len(bars)) if str(bars.index[k].date()) == day['date']]
    ev = []
    for k in later:
        ev += sim.on_bar(day, bars.iloc[:k + 1], s)
        for e in ev:
            if e['event'] == 'entry':
                e['trade']['status'] = 'closed'
        if len(day['trades']) > 1:
            break
    assert len(day['trades']) == 2 and day['trades'][1]['n'] == 2
    assert day['trades'][1]['signal_bar'] > day['trades'][0]['signal_bar']


def test_one_trade_a_day_for_strategy_1_and_five_for_the_rest():
    assert [sim.SPECS[n].max_trades for n in range(1, 8)] == [1, 5, 5, 5, 5, 5, 5]


def test_each_algo_has_its_own_flags_files_and_dispatch_route(monkeypatch):
    from trading_app.app.routes import api
    pr = algo.PROFILES
    assert [p.pfx for p in pr.values()] == [f'ST{n}' for n in range(1, 17)]      # 8-16 = ST Touch Break (Mine)
    assert len({p.state_file() for p in pr.values()}) == 16 and len({p.history_file() for p in pr.values()}) == 16
    assert pr[6].symbol == 'SENSEX' and pr[6].instrument == 'BFO' and pr[4].instrument == 'NFO'
    for n in range(1, 17):
        assert api._STBB_FLAGS[f'st{n}'] == f'ST{n}'
    assert 'stbb' not in api._STBB_FLAGS                  # the ST + BB + RSI tabs are gone


def test_settings_default_to_the_backtest_form_and_env_overrides(monkeypatch):
    env = {'ST4_SL_PCT': '9', 'ST4_RSI_LEVEL': '66'}
    monkeypatch.setattr(algo, '_uvar', lambda u, name, default='': env.get(name, default))
    monkeypatch.setattr(algo.base, '_uvar', lambda u, name, default='': env.get(name, default))
    s = algo.settings('u', algo.PROFILES[4])
    assert s['mode'] == 'paper' and s['sl_pct'] == 9 and s['tsl_x'] == 13 and s['tsl_y'] == 10
    assert s['rsi_level'] == 66 and s['rsi_period'] == 20 and isinstance(s['rsi_period'], int)
    assert s['backstop_pct'] == 9 and s['direction'] == 'long' and s['premium'] == 250
