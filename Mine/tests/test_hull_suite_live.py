"""Hull Suite live (specs 16-18 of the Simple Trader chassis, the /algo Hull Suite tab): the live engine
takes the backtest's trades when it is fed the same minutes, and its single decisions are the backtest's."""
import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest.hull_suite_engine import HullSuiteEngine
from trading_app.service import hull_suite_live_engine as hsl
from trading_app.service import simple_trader_live_engine as sim

ANCHOR = pd.Timedelta(hours=9, minutes=15)


def _minutes(days=30, seed=7):
    rng = np.random.default_rng(seed)
    out, px = [], 20000.0
    for d in pd.bdate_range('2026-03-02', periods=days):
        for k in range(375):
            o = px
            px = px + rng.normal(0, 6) + 1.5 * np.sin(k / 40.0 + d.day)
            hi, lo = max(o, px) + abs(rng.normal(0, 2)), min(o, px) - abs(rng.normal(0, 2))
            out.append((d + ANCHOR + pd.Timedelta(minutes=k), o, hi, lo, px))
    m = pd.DataFrame(out, columns=['date', 'open', 'high', 'low', 'close']).set_index('date')
    m['volume'] = 0
    return m


def _bars(m, bar):
    key = (m.index - ANCHOR).floor(f'{bar}min') + ANCHOR
    b = m.groupby(key).agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                           close=('close', 'last'), volume=('volume', 'sum'))
    b.index.name = 'date'
    return b


def _replay(m, bars, s, bar, start):
    """The live algo, minute by minute: the poll sees an armed level as it is crossed, each closed minute
    is read, then the candle it closes."""
    live, busy_days = [], set()
    for d in sorted({x for x in m.index.normalize() if str(x.date()) >= start}):
        day = hsl.new_day(str(d.date()))
        dm = m[(m.index >= d) & (m.index < d + pd.Timedelta(days=1))]

        def act(evs, when):
            for ev in evs:
                t = ev['trade']
                if ev['event'] == 'entry':
                    t['entry_min'] = when
                if ev['event'] == 'exit' and t['status'] == 'open':
                    t.update(status='closed', result=ev['reason'], exit_min=when)
        for ts, row in dm.iterrows():
            tm = ts.strftime('%Y-%m-%d %H:%M:%S')
            for t in list(day['trades']):
                if t['status'] == 'armed':
                    lg = t['type'] == 'Long'
                    if (row.high >= t['level'] if lg else row.low <= t['level']) and not (
                            row.low <= t['stop'] if lg else row.high >= t['stop']):
                        px = max(row.open, t['level']) if lg else min(row.open, t['level'])
                        act(hsl.on_price(day, px, ts.strftime('%H:%M'), s, bar), tm)
            act(hsl.on_minute(day, {'ts': tm, 'open': row.open, 'high': row.high, 'low': row.low,
                                    'close': row.close}, s, bar), tm)
            close_at = ts + pd.Timedelta(minutes=1)
            if ((close_at - d - ANCHOR).total_seconds() / 60) % bar == 0:
                act(hsl.on_bar(day, bars[bars.index <= close_at - pd.Timedelta(minutes=bar)], s, bar), tm)
            if close_at.strftime('%H:%M') >= s['exit_cutoff']:
                for t in day['trades']:
                    if t['status'] == 'open':
                        t.update(status='closed', result='Time Exit', exit_min=close_at.strftime('%Y-%m-%d %H:%M:%S'))
        live += [t for t in day['trades'] if t['status'] == 'closed']
        if any('one at a time' in (t.get('error') or '') for t in day['trades']):
            busy_days.add(str(d.date()))
    return live, busy_days


@pytest.mark.parametrize('n', [16, 17, 18])
def test_live_takes_the_backtests_trades_on_the_same_minutes(n):
    """Each row's own rule (shorter lines so 30 synthetic sessions trade): every backtest trade is entered live
    at the same minute and side and leaves for the same reason at the same minute. The one place they may part
    is a day two setups waited at once — the backtest books them in signal order, which needs hindsight."""
    spec = sim.SPECS[n]
    s = dict(spec.fixed, **spec.params, entry_start='09:30', max_trades=spec.max_trades)
    s.update(slow_len=60, medium_len=30, fast_len=12, pullback_bars=12)
    bar = spec.bar
    m = _minutes()
    bars = _bars(m, bar)
    eng = hsl.engine_for(bars, s, bar)
    eng.minutes = eng._frame(m)
    start = '2026-03-12'
    bt = [t for t in eng.simulate() if t['entry_time'] >= start]
    live, busy = _replay(m, bars, s, bar, start)
    assert len(bt) >= 10
    key = lambda e, t: (e[:16], t['type'])
    bk = {key(t['entry_time'], t): t for t in bt if t['entry_time'][:10] not in busy}
    lk = {key(t['entry_min'], t): t for t in live if t['entry_min'][:10] not in busy}
    assert set(bk) == set(lk)
    for k in bk:
        assert bk[k]['exit_reason'] == lk[k]['result'], k
        assert bk[k]['exit_time'][:16] == lk[k]['exit_min'][:16], k


def _day_with_signal(rr=2.0, be=0.0, max_wait=3):
    s = {'slow_len': 60, 'medium_len': 30, 'fast_len': 12, 'max_wait': max_wait, 'rr': rr, 'breakeven_rr': be,
         'exit_cutoff': '15:15', 'max_trades': 2, 'stop_mode': 'signal', 'direction': 'both'}
    day = hsl.new_day('2026-03-02')
    t = {'n': 1, 'type': 'Long', 'status': 'armed', 'signal_bar': '2026-03-02 10:00:00', 'break_start': 605,
         'break_end': 605 + 5 * max_wait, 'level': 100.0, 'stop': 90.0, 'risk': 10.0, 'rr': rr, 'be_rr': be,
         'signal': {}}
    day['trades'].append(t)
    return day, t, s


def test_a_poll_through_the_level_enters_and_the_target_exits():
    day, t, s = _day_with_signal(rr=2.0)
    ev = hsl.on_price(day, 100.5, '10:06', s)
    assert [e['event'] for e in ev] == ['entry'] and t['status'] == 'open' and t['target'] == 120.0
    ev = hsl.on_price(day, 120.0, '10:30', s)
    assert ev[0]['event'] == 'exit' and ev[0]['reason'] == 'Target'


def test_the_stop_first_drops_the_setup_and_a_run_past_the_level_is_not_chased():
    day, t, s = _day_with_signal()
    hsl.on_price(day, 89.0, '10:06', s)
    assert t['status'] == 'invalidated'
    day, t, s = _day_with_signal()
    hsl.on_price(day, 106.0, '10:06', s)                   # 6 pts past a 10-pt risk: more than half of it
    assert t['status'] == 'missed'


def test_breakeven_lifts_the_stop_to_the_fill_and_books_breakeven():
    day, t, s = _day_with_signal(rr=0, be=1.0)
    hsl.on_price(day, 100.0, '10:06', s)
    ev = hsl.on_price(day, 110.0, '10:20', s)
    assert ev and ev[0]['event'] == 'breakeven' and t['cur_stop'] == 100.0
    ev = hsl.on_price(day, 99.9, '10:40', s)
    assert ev[0]['reason'] == 'Breakeven'


def test_a_minute_that_opens_past_the_level_fills_at_its_open():
    day, t, s = _day_with_signal(rr=2.0)
    hsl.on_minute(day, {'ts': '2026-03-02 10:07:00', 'open': 102.0, 'high': 103, 'low': 101.5, 'close': 102.5}, s)
    assert t['status'] == 'open' and t['index_level_fill'] == 102.0 and t['target'] == 122.0


def test_the_window_runs_out():
    day, t, s = _day_with_signal(max_wait=1)
    assert hsl.on_price(day, 101.0, '10:10', s) == []      # past 10:05-10:10: the poll no longer enters
    assert t['status'] == 'armed'


def test_specs_16_to_18_are_the_three_backtest_rows():
    assert [n for n, _, _ in sim.HULL_TABS] == [16, 17, 18]
    a, b, c = (sim.SPECS[n] for n in (16, 17, 18))
    assert (a.bar, a.params['slow_len'], a.params['medium_len'], a.params['fast_len'], a.params['rr'],
            a.params['breakeven_rr'], a.fixed['stop_mode'], a.fixed['entry_end']) == (5, 300, 100, 20, 2.0, 1.0, 'two_bar', '13:30')
    assert (b.bar, b.params['medium_len'], b.params['max_wait'], b.params['rr'], b.fixed['slow_slope']) == (15, 50, 2, 0.0, False)
    assert c.params['max_wait'] == 5 and c.params['pullback_bars'] == 24 and c.entry_start == '09:45'
    assert all(sp.sl_pct == 0 and sp.premium == 250 and sp.fixed['direction'] == 'both' for sp in (a, b, c))
    # the slow HMA's run-in: 300 fifteen-minute candles is ~13 sessions
    assert hsl.warmup_days({'slow_len': 300}, 15) >= 20


def test_the_settings_and_label_of_a_hull_tab(monkeypatch):
    from trading_app.algo.simple_trader import simple_trader_algo as algo
    monkeypatch.setattr(algo, '_uvar', lambda u, name, default='': {'ST17_DIRECTION': 'short'}.get(name, default))
    monkeypatch.setattr(algo.base, '_uvar', lambda u, name, default='': default)
    s = algo.settings('u', algo.PROFILES[17])
    assert s['mode'] == 'paper' and s['direction'] == 'short' and s['bar'] == 15 and s['slow_slope'] is False
    assert s['slow_len'] == 300 and isinstance(s['slow_len'], int) and s['rr'] == 0
    assert algo.PROFILES[16].label == 'Hull Suite · 1. 5m · 300/100/20 · 1:2 · BE 1R'


# ── the thread, in paper mode (Simple Trader chassis, profile 16) ─────────────────────────────

from datetime import timedelta, timezone  # noqa: E402

from trading_app.algo.simple_trader import simple_trader_algo as algo  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


class _FakeAlgo(algo.SimpleTraderAlgo):
    def __init__(self, username, profile, minutes):
        super().__init__(username, profile)
        self.m, self.now, self.alerts = minutes, None, []

    def _closed(self):
        return self.m[self.m.index < pd.Timestamp(self.now.replace(tzinfo=None)).floor('min')]

    def _today_minutes(self, today):
        c = self._closed()
        c = c[c.index.date == today]
        return [{'date': ts.tz_localize(IST), 'open': r.open, 'high': r.high, 'low': r.low, 'close': r.close}
                for ts, r in c.iterrows()]

    def _bars(self, today, now):
        c = self._closed()
        b = _bars(c, self.spec.bar)
        return b[b.index + pd.Timedelta(minutes=self.spec.bar) <= pd.Timestamp(now.replace(tzinfo=None)).floor('min')]

    def _spot(self):
        c = self._closed()
        return float(c['close'].iloc[-1]) if len(c) else None

    def _premium(self, strike, option_type):
        return 250.0

    def _chain_step(self):
        return 50

    def _kite_premiums(self, strikes, option_type):
        return {}

    def _alert(self, *a, **k):
        self.alerts.append(a)


@pytest.fixture()
def paper16(tmp_path, monkeypatch):
    monkeypatch.setattr(algo, '_DIR', str(tmp_path))
    env = {'ST16_MODE': 'paper', 'ST16_SLOW_LEN': '60', 'ST16_MEDIUM_LEN': '30', 'ST16_FAST_LEN': '12'}
    fake = lambda u, name, default='': env.get(name, default)          # noqa: E731
    monkeypatch.setattr(algo, '_uvar', fake)
    monkeypatch.setattr(algo.base, '_uvar', fake)
    monkeypatch.setattr(algo.base, 'accounts', lambda u, p=None: [])
    monkeypatch.setattr(algo.base, '_flag', lambda u, name, default='false': False)
    return env


def test_the_paper_thread_takes_the_backtests_trades_minute_by_minute(paper16):
    """The real tick loop (candles, the minute reads, the poll, the paper legs, the history) over whole days,
    ticked a few seconds after every minute: every trade the backtest takes on those days is taken, in paper,
    at the same minute and side, and booked."""
    m = _minutes(days=16, seed=3)
    pr = algo.PROFILES[16]
    s = algo.settings('u', pr)
    s.update(entry_start=pr.spec.entry_start, max_trades=s['max_trades'])
    s = dict(pr.spec.fixed, **s)
    eng = hsl.engine_for(_bars(m, 5), s, 5)
    eng.minutes = eng._frame(m)
    days = sorted({str(d.date()) for d in m.index})[-4:]
    bt = [t for t in eng.simulate() if t['entry_time'][:10] in days]
    a = _FakeAlgo('u', pr, m)
    taken, busy = [], set()
    for d in days:
        for k in range(1, 376):
            a.now = (pd.Timestamp(f'{d} 09:15') + pd.Timedelta(minutes=k, seconds=3)).to_pydatetime().replace(tzinfo=IST)
            a.tick(a.now)
        taken += [t for t in a.state['day']['trades'] if t['status'] == 'closed']
        busy |= {d for t in a.state['day']['trades'] if 'one at a time' in (t.get('error') or '')}
    # which setup was traded and how it ended (this fake's price is the last CLOSED minute's, so an entry is
    # stamped the minute after the crossing; live polls inside the minute)
    want = {(t['signal']['time'][:16], t['type'], t['exit_reason']) for t in bt if t['entry_time'][:10] not in busy}
    got = {(t['signal_bar'][:16], t['type'], t['result']) for t in taken if t['signal_bar'][:10] not in busy}
    assert want and want == got
    assert all(t['legs']['paper']['entry'] == 250.0 and t['mode'] == 'paper' for t in taken)
    assert len(algo.load_history(pr)) >= len(want)
