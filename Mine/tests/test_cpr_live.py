"""CPR Logic live algo: the engine that runs the /cpr-logic rule one closed
candle at a time, the thread around it (paper only here — nothing in this file
can reach a broker), and the dispatcher's 'cpr' strategy."""

from datetime import datetime, timedelta, timezone

import pytest

from trading_app.service import cpr_live_engine as eng
from trading_app.service import cpr_trade_rule as rule

IST = timezone(timedelta(hours=5, minutes=30))


def _bar(t, o, h, l, c):
    return {'time': t, 'open': float(o), 'high': float(h), 'low': float(l), 'close': float(c)}


def _armed(entry=100.0, sl=90.0, target=120.0, side='BUY', extend=None):
    day = eng.new_day('2026-10-12')
    p = {'trade': side, 'entry': entry, 'sl': sl, 'target': target}
    if extend:
        p['extend'] = extend
    t = eng._arm(day, p, 'test setup', 0, [_bar('09:15', 95, 99, 94, 98)], 'first')
    day['first_done'] = True
    return day, t


def _close(day, bar):
    """One candle closing on an already-decided day (the rule is not asked)."""
    events = []
    for t in day['trades']:
        if t['status'] == 'armed' and bar['time'] > t['armed_after']:
            crossed = (bar['high'] >= t['entry']) if t['trade'] == 'BUY' else (bar['low'] <= t['entry'])
            if crossed:
                px = max(t['entry'], bar['open']) if t['trade'] == 'BUY' else min(t['entry'], bar['open'])
                events += eng._fill(t, px, bar['time'])
        if t['status'] == 'open':
            events += eng._settle_close(t, bar)
    return events


# ── the engine's exits are the sheet's ─────────────────────────────────────

def test_a_candle_closing_through_the_target_locks_and_rides_to_the_next_touch():
    day, t = _armed()
    _close(day, _bar('09:20', 98, 103, 97, 102))                 # fills at 100
    assert t['status'] == 'open' and t['fill'] == 100.0
    ev = _close(day, _bar('09:25', 102, 124, 101, 123))          # target 120, closed over it
    assert [e['event'] for e in ev] == ['locked'] and t['stop'] == 120.0
    _close(day, _bar('09:30', 123, 131, 122, 130))               # riding
    ev = _close(day, _bar('09:35', 130, 130, 118, 119))          # back to 120: out there
    assert ev[0]['event'] == 'exit' and t['result'] == 'Target' and t['pts'] == 20.0


def test_a_candle_that_tags_the_target_and_closes_back_is_out_at_its_close():
    day, t = _armed()
    _close(day, _bar('09:20', 98, 103, 97, 102))
    _close(day, _bar('09:25', 102, 121, 101, 117))
    assert t['status'] == 'closed' and t['exit'] == 117.0 and t['pts'] == 17.0


def test_the_stop_is_taken_on_the_price_poll_and_on_the_candle():
    day, t = _armed()
    eng.on_price(day, 101.0, '09:21')                            # a poll inside 09:20 crosses the entry
    assert t['status'] == 'open' and t['fill_src'] == 'tick'
    eng.on_price(day, 89.5, '09:23')                             # a market order fills at the price, not the level
    assert t['status'] == 'closed' and t['result'] == 'SL' and t['fill'] == 101.0 and t['pts'] == -11.5
    day2, t2 = _armed()
    _close(day2, _bar('09:20', 98, 103, 97, 102))
    _close(day2, _bar('09:25', 102, 104, 88, 95))                # a wick the polls missed
    assert t2['result'] == 'SL' and t2['exit'] == 90.0


def test_a_poll_fill_is_not_stopped_by_a_wick_from_before_it():
    day, t = _armed()
    eng.on_price(day, 100.5, '09:23')                            # filled late inside the 09:20 candle
    _close(day, _bar('09:20', 95, 101, 89, 100))                 # its 89 low came before the fill
    assert t['status'] == 'open'


def test_the_target_waits_for_the_close_not_the_poll():
    day, t = _armed()
    eng.on_price(day, 100.0, '09:21')
    assert eng.on_price(day, 125.0, '09:22') == [] and t['status'] == 'open'


def test_a_strong_break_through_the_cpr_extends_the_target():
    day, t = _armed(extend={'level': 140.0, 'name': 'PDH', 'through': 110.0})
    _close(day, _bar('09:20', 98, 103, 97, 102))
    ev = _close(day, _bar('09:25', 102, 122, 101, 121))          # body 19/21, closed over 110
    assert ev[0]['event'] == 'extended' and t['target'] == 140.0
    _close(day, _bar('09:30', 121, 141, 120, 139))
    assert t['result'] == 'Target' and t['exit'] == 140.0


def test_1515_squares_off_and_expires():
    day, t = _armed()
    eng.on_price(day, 100.0, '09:21')
    _, t2 = _armed()
    day['trades'].append(t2)
    eng.square_off(day, 107.0, '15:15')
    assert t['result'] == 'EOD' and t['pts'] == 7.0 and t2['status'] == 'expired' and day['done']


def test_a_sell_mirrors():
    day, t = _armed(entry=100.0, sl=110.0, target=80.0, side='SELL')
    _close(day, _bar('09:20', 102, 103, 97, 98))
    _close(day, _bar('09:25', 98, 99, 78, 79))
    assert t['locked'] and t['stop'] == 80.0
    _close(day, _bar('09:30', 79, 81, 75, 80))
    assert t['result'] == 'Target' and t['pts'] == 20.0


# ── the engine decides what the rule decides ───────────────────────────────

def test_the_engine_takes_the_rules_trade_on_7_sept_2026():
    """The 09:15 long wick confirmed by 09:20, run candle by candle: nothing at
    the 09:15 close, then the same SELL the whole-day replay takes."""
    from tests.test_cpr_trade_rule import C1, C2, _chart, _session
    chart, session = _chart(), _session(C1, C2)
    whole = [(p['trade'], p['entry'], p['sl'], p['target']) for p, *_ in rule.propose_all(chart, session) if p]
    day = eng.new_day('2026-09-07')
    assert eng.on_bar(day, chart, session[:1]) == []
    for k in range(2, len(session) + 1):
        eng.on_bar(day, chart, session[:k])
        if day['done']:
            break
    assert [(t['trade'], t['entry'], t['sl'], t['target']) for t in day['trades']] == whole == [('SELL', 23837.0, 23892.0, 23727.0)]


def test_the_09_15_close_is_decided_on_one_candle(monkeypatch):
    """Live cannot see 09:20 at 09:15. On 20 Jan 2025 the one-candle reading
    is a different SELL from the whole-day replay's 09:20 one — kept on
    purpose: deciding at 09:15 earned +10,956 pts over 2018-26 replayed live
    against +10,302 waiting for 09:20 on a big 09:15 candle."""
    from tests.test_cpr_trade_rule import _chart, _session
    bars = [_bar('09:15', 23290.4, 23308.35, 23219.35, 23227.6), _bar('09:20', 23226.95, 23231.0, 23210.85, 23214.25)]
    chart = _chart(bars[0], (23292.1, 23100.35, 23203.2), (23400.0, 23050.0, 23210.0))
    chart['weekly'] = {'pp': 23214.03, 'bc': 23208.62, 'tc': 23219.45}
    day = eng.new_day('2025-01-20')
    ev = eng.on_bar(day, chart, (bars + _session()[-25:])[:1])
    assert ev and ev[0]['event'] == 'armed' and ev[0]['trade']['setup_time'] == '09:15'


# ── the option leg and the size ────────────────────────────────────────────

def test_pick_strike_walks_to_the_premium_nearest_200():
    prem = {24000: 260.0, 24050: 228.0, 24100: 199.0, 24150: 172.0, 24200: 148.0}
    asked = []

    def quote(k):
        asked.append(k)
        return prem.get(k)
    got = eng.pick_strike(quote, 24010.0, 50, 'CE', 200)
    assert got == {'strike': 24100, 'premium': 199.0} and 24200 not in asked
    pe = {24000: 150.0, 23950: 132.0, 24050: 176.0, 24100: 205.0, 24150: 236.0}
    assert eng.pick_strike(lambda k: pe.get(k), 24010.0, 50, 'PE', 200)['strike'] == 24100
    assert eng.pick_strike(lambda k: None, 24010.0, 50, 'CE') is None


def test_compound_lots_is_the_popups_rule():
    assert eng.compound_lots(100000, 0) == 5
    assert eng.compound_lots(100000, 39999) == 6
    assert eng.compound_lots(100000, -95000) == 1
    assert eng.compound_lots(100000, 10_000_000) == 27
    assert eng.compound_lots(100000, 10_000_000, max_lots=50) == 50


# ── the thread, in paper, against fakes ────────────────────────────────────

class _FakeAlgo:
    """CPRAlgo with every market read replaced and every broker path booby-trapped."""

    @staticmethod
    def make(monkeypatch, tmp_path, env):
        from trading_app.algo.cpr_logic import cpr_algo as cpr
        monkeypatch.setattr(cpr, 'STATE_FILE', str(tmp_path / 'cpr_state.json'))
        monkeypatch.setattr(cpr, 'HISTORY_FILE', str(tmp_path / 'cpr_trades_history.json'))
        monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                            staticmethod(lambda u, k, d='': env.get(k, d)))
        monkeypatch.setattr(cpr.CPRAlgo, '_alert', lambda *a, **k: None)

        def boom(*a, **k):
            raise AssertionError('a paper trade reached the live order path')
        monkeypatch.setattr(cpr.CPRAlgo, '_enter_live', boom)
        monkeypatch.setattr(cpr.CPRAlgo, '_exit_live', boom)
        algo = cpr.CPRAlgo('test-user')
        algo._chain_step = lambda: 50
        algo._premium = lambda strike, ot: 200.0 if strike == 24100 else 150.0
        return cpr, algo


def _day_with_setup(monkeypatch, algo, bars_by_time):
    """The rule replaced by a fixed setup on the 09:20 candle — this tests the
    thread's plumbing, not the rule."""
    monkeypatch.setattr(algo, '_chart', lambda today, bars: {'levels': {}})
    monkeypatch.setattr(eng, '_first_setup', lambda chart, bars: (
        ({'trade': 'BUY', 'entry': 24120.0, 'sl': 24090.0, 'target': 24160.0}, 'the 09:15 candle closed inside the CPR', 1, False)
        if len(bars) >= 2 else None))
    state = {'bars': []}
    algo._today_bars = lambda today, now: [b for b in bars_by_time if b['time'] <= (now - timedelta(minutes=5, seconds=5)).strftime('%H:%M')]
    algo._spot = lambda: state.get('spot')
    return state


BARS = [_bar('09:15', 24080, 24110, 24070, 24100), _bar('09:20', 24100, 24112, 24095, 24110),
        _bar('09:25', 24110, 24125, 24105, 24122), _bar('09:30', 24122, 24170, 24120, 24165),
        _bar('09:35', 24165, 24175, 24150, 24158)]


def test_a_paper_day_is_recorded_and_sends_nothing(monkeypatch, tmp_path):
    cpr, algo = _FakeAlgo.make(monkeypatch, tmp_path, {'CPR_LOGIC_MODE': 'paper', 'CPR_LOTS': '2'})
    feed = _day_with_setup(monkeypatch, algo, BARS)
    d = datetime(2026, 10, 12, tzinfo=IST)
    at = lambda hh, mm, ss=10: d.replace(hour=hh, minute=mm, second=ss)
    algo.tick(at(9, 25))                                     # 09:15 and 09:20 closed: the setup arms
    t = algo.state['day']['trades'][0]
    assert t['status'] == 'armed'
    feed['spot'] = 24121.0
    algo.tick(at(9, 27))                                     # the poll crosses 24,120: bought in paper
    assert t['status'] == 'open' and t['mode'] == 'paper'
    assert t['option'] == {'symbol': 'NIFTY', 'strike': 24100, 'type': 'CE', 'quoted': 200.0}
    assert t['legs']['paper']['lots'] == 2 and t['legs']['paper']['entry'] == 200.0
    feed['spot'] = 24168.0
    algo.tick(at(9, 35, 10))                                 # 09:30 closed over 24,160: locked
    assert t['locked'] and t['stop'] == 24160.0
    algo._premium = lambda strike, ot: 236.0
    feed['spot'] = 24159.0
    algo.tick(at(9, 37))                                     # back to the locked stop: out
    assert t['status'] == 'closed' and t['result'] == 'Target'
    rows = cpr.load_history()
    assert len(rows) == 1 and rows[0]['mode'] == 'paper' and rows[0]['pnl'] == (236.0 - 200.0) * 2 * 65


def test_a_restart_does_not_trade_a_fill_it_was_not_watching(monkeypatch, tmp_path):
    cpr, algo = _FakeAlgo.make(monkeypatch, tmp_path, {'CPR_LOGIC_MODE': 'paper'})
    _day_with_setup(monkeypatch, algo, BARS)
    algo.tick(datetime(2026, 10, 12, 9, 40, 10, tzinfo=IST))   # first look at 09:40: the morning replays
    t = algo.state['day']['trades'][0]
    assert t.get('missed') and t['legs'] == {} and cpr.load_history() == []


def test_compound_sizing_reads_the_books_own_history(monkeypatch, tmp_path):
    cpr, _ = _FakeAlgo.make(monkeypatch, tmp_path, {'CPR_LOT_MODE': 'compound'})
    assert cpr.lots_for_trade('test-user', 'live', 1) == 1          # ₹20,000 to start: one lot
    cpr._append_history({'mode': 'paper', 'pnl': 41000})
    cpr._append_history({'mode': 'live', 'pnl': -5000})
    assert cpr.lots_for_trade('test-user', 'paper', 1) == 3         # 20,000 + 41,000
    assert cpr.lots_for_trade('test-user', 'live', 1) == 1          # never under one
    cpr2, _ = _FakeAlgo.make(monkeypatch, tmp_path, {'CPR_LOT_MODE': 'compound', 'CPR_START_CAPITAL': '100000'})
    assert cpr2.lots_for_trade('test-user', 'paper', 1) == 7


def test_the_mode_defaults_to_paper(monkeypatch, tmp_path):
    cpr, _ = _FakeAlgo.make(monkeypatch, tmp_path, {})
    assert cpr.mode('test-user') == 'paper' and cpr.lot_mode('test-user') == 'fixed'


# ── the dispatcher's 'cpr' strategy ────────────────────────────────────────

def test_cpr_orders_route_on_their_own_flag_and_carry_no_automatic_stop(monkeypatch):
    import inspect
    from trading_app.app.routes import api
    src = inspect.getsource(api._dispatch_order_to_brokers)
    assert "_auto_sl = strategy not in ('intrinsic', 'oix', 'op', 'cpr')" in src
    env = {'BROKER_1_TYPE': 'zerodha', 'BROKER_1_ACTIVE': 'true', 'BROKER_1_CPR_ACTIVE': 'true',
           'BROKER_2_TYPE': 'zerodha', 'BROKER_2_ACTIVE': 'true', 'BROKER_2_OP_ACTIVE': 'true'}
    monkeypatch.setattr('trading_app.app.utils.user_env.UserEnvManager.get_user_var',
                        staticmethod(lambda u, k, d='': env.get(k, d)))
    tried = []
    monkeypatch.setattr(api, 'get_kite', lambda instance=None, **k: tried.append(instance))
    res = api._dispatch_order_to_brokers(symbol='NIFTY', strike=24850, option_type='CE', action='BUY',
                                         strategy='cpr', username='u', session_data={},
                                         lots_for=lambda i: 1)
    assert res['brokers_targeted'] == 1 and tried == [1]
    unsized = api._dispatch_order_to_brokers(symbol='NIFTY', strike=24850, option_type='CE', action='BUY',
                                             strategy='cpr', username='u', session_data={})
    assert 'sized by the caller' in unsized['summary'][0]['result']['error']


# ── the books are the broker's ─────────────────────────────────────────────

def _live_trade(algo, legs):
    algo.state = {'day': eng.new_day('2026-10-13'), 'log': [], 'last_bar': None}
    t = {'n': 1, 'trade': 'BUY', 'strategy': 'Inside CPR → cross candle', 'setup_time': '09:35',
         'entry': 22522.0, 'sl': 22463.0, 'target': 22610.0, 'fill': 22527.35, 'fill_time': '09:40',
         'status': 'open', 'mode': 'live', 'option': {'symbol': 'NIFTY', 'strike': 22350, 'type': 'CE', 'quoted': 196.35},
         'legs': legs}
    algo.state['day']['trades'].append(t)
    return t


def test_a_backstop_filled_at_the_broker_is_booked_at_its_fill(monkeypatch, tmp_path):
    """13 Oct 2026: the SL-M sold at 148.05 at 11:15; the app kept the trade
    open and booked a 352 exit at 15:15. Now the sweep sees the stop fill."""
    cpr, algo = _FakeAlgo.make(monkeypatch, tmp_path, {})
    leg = {'name': 'Devanai (Kite)', 'instance': 2, 'lots': 1, 'qty': 65, 'entry': 196.35, 'open': True,
           'entry_record': 'E', 'sl_record': 'S', 'backstop': 148.1}
    t = _live_trade(algo, {'2': leg})
    books = {'E': ('EXECUTED', 197.40, 65), 'S': ('OPEN', None, 0)}
    algo._fills = lambda rid: books.get(rid)
    algo._sync_live_legs(datetime(2026, 10, 13, 9, 41, tzinfo=IST))
    assert leg['entry'] == 197.40 and leg['entry_confirmed'] and leg['open']
    books['S'] = ('EXECUTED', 148.05, 65)
    algo.state['_synced_at'] = 0
    algo._sync_live_legs(datetime(2026, 10, 13, 11, 15, 20, tzinfo=IST))
    assert not leg['open'] and leg['exit'] == 148.05 and leg['pnl'] == round((148.05 - 197.40) * 65, 2)
    rows = cpr.load_history()
    assert len(rows) == 1 and rows[0]['pnl'] == -3207.75 and rows[0]['result'] == 'Backstop'
    # The index trade running on to its 15:15 exit books nothing a second time.
    t.update(status='closed', exit=22702.05, exit_time='15:15', result='Target', pts=174.7)
    algo._leave(t, datetime(2026, 10, 13, 15, 15, tzinfo=IST))
    assert len(cpr.load_history()) == 1


def test_an_exit_is_booked_at_its_real_fill_not_the_quote(monkeypatch, tmp_path):
    cpr, algo = _FakeAlgo.make(monkeypatch, tmp_path, {})
    leg = {'name': 'Saranya (Kite)', 'instance': 1, 'lots': 1, 'qty': 65, 'entry': 197.4, 'entry_confirmed': True,
           'open': False, 'exit': 352.0, 'exit_record': 'X', 'sl_record': 'S'}
    t = _live_trade(algo, {'1': leg})
    t.update(status='closed', exit=22702.05, exit_time='15:15', result='Target', pts=174.7, flat_at=1e18)
    algo._fills = lambda rid: {'X': ('EXECUTED', 349.5, 65), 'S': ('CANCELLED', None, 0)}.get(rid)
    algo._sync_live_legs(datetime(2026, 10, 13, 15, 15, 30, tzinfo=IST))
    assert leg['exit'] == 349.5 and cpr.load_history()[0]['pnl'] == round((349.5 - 197.4) * 65, 2)


def test_the_backstop_sits_1_5_risks_under_the_fill(monkeypatch, tmp_path):
    from trading_app.algo.cpr_logic import cpr_algo as cpr
    assert cpr.BACKSTOP_X == 1.5
    # 13 Oct: 197.40 fill, 64-pt index risk -> 100.9, well under the 148 that sold it at 0.75x.
    assert round(197.40 - cpr.BACKSTOP_X * (22527.35 - 22463.0), 1) == 100.9
