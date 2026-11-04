"""NIFTY ATM Straddle live algo: the engine, the thread on paper, and the live order
path against a fake broker — nothing in this file can reach a real one."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_app.algo.straddle import straddle_algo as sa
from trading_app.Backtest import option_selling_engine as ose
from trading_app.service import straddle_live_engine as eng

IST = timezone(timedelta(hours=5, minutes=30))
LOT = 65
FORM = {'STR_CADENCE': 'weekly', 'STR_ENTRY_DAY': 'thursday', 'STR_ADJUST': 'none', 'STR_LOCK_AT': '0', 'STR_LOCK_TO': '0',
        'STR_COMPOUNDING': 'false', 'STR_TARGET_PCT': '50', 'STR_STOP_PCT': '25'}
THU, TUE = date(2026, 6, 11), date(2026, 6, 16)        # entry, its weekly expiry


def at(day, h, m, s=1):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=IST)


class Broker:
    """The Kite side: quotes, positions, orders; `refuse` = {(tsym, action)} rejected after acceptance."""

    def __init__(self):
        self.orders, self.net, self.refuse, self.down = [], {}, set(), set()

    def send(self, instance, tsym, action, qty):
        if instance in self.down:
            return {'success': False, 'error': 'broker says no'}
        oid = f'{len(self.orders) + 1}'
        self.orders.append({'id': oid, 'instance': instance, 'tsym': tsym, 'action': action, 'qty': qty})
        if (tsym, action) not in self.refuse:
            key = (instance, tsym)
            self.net[key] = self.net.get(key, 0) + (qty if action == 'BUY' else -qty)
        return {'success': True, 'order_id': oid}

    def state(self, instance, oid, px):
        o = self.orders[int(oid) - 1]
        if (o['tsym'], o['action']) in self.refuse:
            return 'REJECTED', 0, 0.0
        return 'FILLED', o['qty'], px.get(o['tsym'], 100.0)


def tsym(strike, right, expiry=TUE):
    return f'N{expiry:%m%d}{strike}{right}'


class Algo(sa.StraddleAlgo):
    """Everything that would touch Kite comes from the test."""

    def __init__(self, broker=None):
        super().__init__('T')
        self.broker = broker or Broker()
        self.spot, self.px, self.alerts = 23010.0, {}, []
        self.default_px = 150.0

    def _instruments(self):
        out = []
        for exp in (TUE, date(2026, 6, 23), date(2026, 6, 30)):
            for k in range(20000, 26000, 50):
                for r in ose.RIGHTS:
                    out.append({'tradingsymbol': tsym(k, r, exp), 'expiry': exp, 'strike': k, 'instrument_type': r, 'lot_size': LOT})
        return out

    def _contract(self, expiry, strike, right):
        return {'tradingsymbol': tsym(strike, right, expiry), 'lot_size': LOT}

    def _listed(self):
        return [TUE, date(2026, 6, 23), date(2026, 6, 30)]

    def _quotes(self, tsyms):
        if self.spot is None:
            return None, {}
        return self.spot, {t: self.px.get(t, self.default_px) for t in tsyms}

    def _send_order(self, instance, ts, action, qty):
        return self.broker.send(instance, ts, action, qty)

    def _order_state(self, instance, oid):
        return self.broker.state(instance, oid, self.px)

    def _broker_nets(self, instance):
        if instance in getattr(self, 'blind', ()):
            raise RuntimeError('positions unreadable')
        return {t: q for (i, t), q in self.broker.net.items() if i == instance}

    def _wait(self, secs):
        pass

    def _alert(self, category, title, summary='', once=None):
        self.alerts.append((category, title))


@pytest.fixture
def env(monkeypatch, tmp_path):
    values = dict(FORM)
    monkeypatch.setattr(sa, '_uvar', lambda u, n, d='': values.get(n, d))
    monkeypatch.setattr(sa, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(sa, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(sa, 'accounts', lambda u: [
        {'instance': 1, 'type': 'zerodha', 'lots': 2, 'tradable': True, 'why': None, 'name': 'Kite 1'}])
    return values


# ── the calendar ─────────────────────────────────────────────────────────────

def test_entry_days_and_expiries_follow_the_holiday_list():
    assert eng.is_entry_day(date(2026, 11, 9), 'monday', 'weekly')
    assert not eng.is_entry_day(date(2026, 11, 10), 'tuesday', 'weekly')        # a holiday
    assert eng.is_entry_day(date(2026, 11, 9), 'tuesday', 'weekly')             # moved to the session before
    assert not eng.is_entry_day(date(2026, 11, 16), 'tuesday', 'weekly')        # an ordinary Monday is not Tuesday's entry
    assert not eng.is_entry_day(date(2026, 11, 18), 'friday', 'weekly')
    # Tue 2026-11-10 is a holiday: Monday the 9th does not sell a contract expiring that afternoon.
    assert eng.expiry_for(date(2026, 11, 9), 'weekly') == date(2026, 11, 17)
    assert eng.expiry_for(date(2026, 11, 16), 'weekly') == date(2026, 11, 17)
    assert eng.is_one_night(date(2026, 11, 16), date(2026, 11, 17))
    assert not eng.is_one_night(date(2026, 11, 12), date(2026, 11, 17))
    assert eng.is_entry_day(date(2026, 11, 27), 'last_friday', 'monthly')
    assert not eng.is_entry_day(date(2026, 11, 20), 'last_friday', 'monthly')
    assert eng.expiry_for(date(2026, 11, 27), 'monthly') == date(2026, 12, 29)


def test_an_expiry_the_exchange_does_not_list_is_moved_or_refused():
    listed = [date(2026, 11, 16), date(2026, 11, 24)]
    assert eng.expiry_for(date(2026, 11, 12), 'weekly', listed) == date(2026, 11, 16)
    assert eng.expiry_for(date(2026, 11, 12), 'weekly', [date(2026, 11, 24)]) is None


# ── paper ────────────────────────────────────────────────────────────────────

def test_paper_enters_once_at_the_entry_time_on_the_entry_day_and_sends_nothing(env):
    a = Algo()
    a.tick(at(THU, 15, 14))
    assert not a.state.get('position')
    a.tick(at(THU - timedelta(days=1), 15, 15))                      # a Wednesday
    assert not a.state.get('position')
    a.tick(at(THU, 15, 15))
    pos = a.state['position']
    assert pos['mode'] == 'paper' and pos['strike'] == 23000 and pos['expiry'] == TUE.isoformat()
    assert pos['premium'] == 300.0 and pos['accounts']['paper']['held'] == {tsym(23000, 'CE'): -LOT, tsym(23000, 'PE'): -LOT}
    assert a.broker.orders == []
    pid = pos['id']
    a.tick(at(THU, 15, 16))
    assert a.state['position']['id'] == pid and a.broker.orders == []


def test_a_day_whose_entry_window_has_passed_is_missed_not_chased(env):
    a = Algo()
    a.tick(at(THU, 15, 26))
    assert not a.state.get('position') and a.state['last_entry_date'] == THU.isoformat()
    assert any('missed' in t for _, t in a.alerts)


def test_a_position_survives_a_restart_and_the_night(env):
    a = Algo()
    a.tick(at(THU, 15, 15))
    b = Algo()
    b.state = sa.load_state()
    assert b.state['position']['strike'] == 23000
    b.px = {tsym(23000, 'CE'): 70.0, tsym(23000, 'PE'): 70.0}        # +160 on 300 = 53 %
    b.tick(at(date(2026, 6, 12), 9, 16))
    assert not b.state.get('position')
    (row,) = sa.load_history()
    assert row['reason'] == 'Target' and row['points'] == 160.0
    # Two legs at ₹100, as the backtest books it.
    assert row['pnl'] == 160 * LOT - 200 and row['accounts']['paper']['brokerage'] == 200


def test_stop_and_lock_and_the_order_they_are_read_in(env):
    env.update(STR_LOCK_AT='40', STR_LOCK_TO='30')
    a = Algo()
    a.tick(at(THU, 15, 15))
    pos = a.state['position']
    assert eng.exit_reason(pos, 150.0) == 'Target'
    assert eng.exit_reason(pos, -75.0) == 'Stop loss'
    assert eng.exit_reason(pos, 80.0) is None
    assert not pos['lock_armed']
    # The lock arms only on a bar close at +40 %, and from then +30 % is the stop.
    a.px = {tsym(23000, 'CE'): 90.0, tsym(23000, 'PE'): 85.0}        # +125 = 41.7 %
    a.tick(at(date(2026, 6, 12), 10, 0))
    assert a.state['position']['lock_armed']
    a.px = {tsym(23000, 'CE'): 105.0, tsym(23000, 'PE'): 106.0}      # +89 = 29.7 %
    a.tick(at(date(2026, 6, 12), 10, 1))
    assert sa.load_history()[-1]['reason'] == 'Profit lock'


def test_skip_one_night_and_one_position_at_a_time(env):
    env.update(STR_ENTRY_DAY='monday', STR_SKIP_ONE_NIGHT='true')
    a = Algo()
    a.tick(at(date(2026, 6, 15), 15, 15))                            # Monday, Tuesday expiry
    assert not a.state.get('position') and a.state['last_entry_date'] == '2026-06-15'
    env.update(STR_ENTRY_DAY='last_thursday', STR_SKIP_ONE_NIGHT='false', STR_CADENCE='monthly')
    b = Algo()
    b.state['position'] = eng.new_position('x', 'paper', {k: 1 for k in sa.RULE_KEYS}, THU, TUE, 23000.0, 23000,
                                           [eng.make_leg('CE', 23000, -1, 150.0, 'C', 's')], 's', LOT)
    b._note_overlap(at(date(2026, 6, 25), 15, 15))                   # the last Thursday
    assert b.state['last_entry_date'] == '2026-06-25'


def test_compounding_sizes_from_this_books_balance(env):
    env.update(STR_COMPOUNDING='true', STR_START_CAPITAL='250000', STR_PER_LOT='250000')
    a = Algo()
    a.tick(at(THU, 15, 15))
    assert a.state['position']['accounts']['paper']['lots'] == 1
    sa._append_history({'accounts': {'paper': {'pnl': 250000.0}}})
    b = Algo()
    b.tick(at(THU, 15, 15))
    assert b.state['position']['accounts']['paper']['lots'] == 2


# ── the same decisions as the backtest ───────────────────────────────────────

SESS = [d for d in (date(2026, 6, 8) + timedelta(i) for i in range(10)) if d.weekday() < 5]
GRID = pd.DatetimeIndex([ts for d in SESS for ts in pd.date_range(datetime(d.year, d.month, d.day, 9, 15),
                                                                  datetime(d.year, d.month, d.day, 15, 25), freq='5min')])
RALLY = date(2026, 6, 12)


def _after(ts, hm=(10, 0)):
    return ts >= pd.Timestamp(datetime(RALLY.year, RALLY.month, RALLY.day, *hm))


SPOT5 = pd.Series([23260.0 if _after(ts) else 23010.0 for ts in GRID], index=GRID)


def _price(right, strike, ts):
    later = ts.date() >= date(2026, 6, 15)
    if not _after(ts):
        return 300.0
    if strike == 23000:
        return 400.0 if right == 'CE' else 220.0
    if strike == 23250:
        return (260.0 if later else 320.0) if right == 'PE' else (330.0 if later else 310.0)
    return 1.0


def _frames(expiry, strike, right):
    px = [_price(right, strike, ts) for ts in GRID]
    return pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px}, index=GRID)


@pytest.mark.parametrize('kind', ['none', 'roll_untested', 'recenter', 'hedge', 'hedge_roll'])
def test_every_adjustment_books_what_the_backtest_books(env, kind):
    env.update(STR_ADJUST=kind, STR_ADJUST_PTS='200', STR_TARGET_PCT='50', STR_STOP_PCT='50')
    params = {'cadence': 'weekly', 'entry_day': 'thursday', 'target_pct': 50, 'stop_pct': 50, 'adjust': kind,
              'adjust_pts': 200, 'max_adjust': 2}
    bt = ose.run_trade(THU, SESS, SPOT5, _frames, params)
    assert bt['expiry'] == TUE and bt['reason'] == 'Expiry'

    class Live(Algo):
        def _quotes(self, tsyms):
            def price(t):
                right, strike = t[-2:], int(t[5:-2])
                return _price(right, strike, self.bar)
            return float(SPOT5.loc[self.bar]), {t: price(t) for t in tsyms}
    a = Live()
    for ts in GRID[(GRID >= pd.Timestamp(datetime(THU.year, THU.month, THU.day, 15, 10)))]:
        a.bar = ts
        close = (ts + pd.Timedelta(minutes=5)).to_pydatetime().replace(tzinfo=IST, second=1)
        a.tick(close)
    (row,) = sa.load_history()
    assert row['reason'] == 'Expiry'
    assert row['points'] == pytest.approx(bt['pnl_points'])
    assert len(row['adjusts']) == len(bt['adjustments'])
    assert sum(1 for lg in row['legs']) == len(bt['legs'])
    assert row['accounts']['paper']['pnl'] == pytest.approx(bt['pnl_points'] * LOT - 100 * bt['legs_traded'])


def test_no_adjustment_on_the_expiry_day(env):
    env.update(STR_ADJUST='recenter', STR_ADJUST_PTS='100', STR_TARGET_PCT='500', STR_STOP_PCT='500')
    a = Algo()
    a.tick(at(THU, 15, 15))
    a.spot = 23500.0
    a.tick(at(TUE, 10, 0))
    assert a.state['position']['adjusts'] == []


# ── live, against the fake broker ────────────────────────────────────────────

def _live(env, **extra):
    env.update(STR_MODE='live', **extra)
    return Algo()


def test_live_sells_each_leg_on_its_exact_contract_at_the_account_size(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    assert [(o['instance'], o['tsym'], o['action'], o['qty']) for o in a.broker.orders] == [
        (1, tsym(23000, 'CE'), 'SELL', 130), (1, tsym(23000, 'PE'), 'SELL', 130)]
    assert a.state['position']['accounts']['1']['held'] == {tsym(23000, 'CE'): -130, tsym(23000, 'PE'): -130}


def test_wings_are_bought_before_anything_is_sold(env):
    a = _live(env, STR_WINGS='500')
    a.px = {tsym(23500, 'CE'): 75.0, tsym(22500, 'PE'): 75.0}
    a.tick(at(THU, 15, 15))
    assert [o['action'] for o in a.broker.orders] == ['BUY', 'BUY', 'SELL', 'SELL']
    assert a.state['position']['premium'] == 150.0


def test_a_short_refused_for_margin_unwinds_the_account(env):
    a = _live(env)
    a.broker.refuse = {(tsym(23000, 'PE'), 'SELL')}
    a.tick(at(THU, 15, 15))
    # CE sold, PE refused after acceptance → the CE is bought straight back.
    assert a.broker.net[(1, tsym(23000, 'CE'))] == 0
    assert a.broker.orders[-1]['action'] == 'BUY' and a.broker.orders[-1]['tsym'] == tsym(23000, 'CE')
    assert not a.state.get('position')
    assert sa.load_history()[-1]['reason'] == 'Entry failed'
    assert any('FAILED' in t for _, t in a.alerts)


def test_nothing_is_ordered_without_a_tradable_account(env, monkeypatch):
    monkeypatch.setattr(sa, 'accounts', lambda u: [
        {'instance': 6, 'type': 'dhan', 'lots': 1, 'tradable': False, 'why': 'dhan — not supported', 'name': 'Dhan'}])
    a = _live(env)
    a.tick(at(THU, 15, 15))
    assert a.broker.orders == [] and not a.state.get('position')


def test_an_exit_buys_back_and_is_retried_until_flat(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    a.px = {tsym(23000, 'CE'): 260.0, tsym(23000, 'PE'): 120.0}      # −80 on 300: past the 25 % stop
    a.broker.down = {1}
    a.tick(at(THU, 15, 20))
    assert a.state['position']['exit']['reason'] == 'Stop loss' and not sa.load_history()
    a.broker.down = set()
    a.px = {}                                                        # the decision is not re-taken
    a.tick(at(THU, 15, 21))
    assert not a.state.get('position')
    row = sa.load_history()[-1]
    assert row['reason'] == 'Stop loss' and row['points'] == -80.0
    assert all(q == 0 for q in a.broker.net.values())


def test_an_exit_is_capped_at_what_the_broker_still_holds(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    a.broker.net[(1, tsym(23000, 'CE'))] = 0                         # squared off by hand
    before = len(a.broker.orders)
    a.px = {tsym(23000, 'CE'): 260.0, tsym(23000, 'PE'): 120.0}
    a.tick(at(THU, 15, 20))
    sent = a.broker.orders[before:]
    assert [(o['tsym'], o['action']) for o in sent] == [(tsym(23000, 'PE'), 'BUY')]    # the CE is not bought into a long
    assert a.broker.net[(1, tsym(23000, 'CE'))] == 0 and any('differs' in t for _, t in a.alerts)
    assert sa.load_history()[-1]['accounts']['1']['unconfirmed']


def test_positions_unreadable_holds_the_exit_rather_than_sending_blind(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    before = len(a.broker.orders)
    a.blind = {1}
    a.px = {tsym(23000, 'CE'): 260.0, tsym(23000, 'PE'): 120.0}
    a.tick(at(THU, 15, 20))
    assert len(a.broker.orders) == before and a.state['position']['exit']
    a.blind = set()
    a.tick(at(THU, 15, 21))
    assert not a.state.get('position')


def test_the_leg_decides_a_live_position_is_closed_at_the_broker_after_a_paper_toggle(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    env.update(STR_MODE='paper', STR_ENABLED='false')
    assert a.state['position']['mode'] == 'live'
    a.request_square_off()
    a.tick(at(THU, 15, 20))
    assert not a.state.get('position') and sa.load_history()[-1]['reason'] == 'Manual'
    assert all(q == 0 for q in a.broker.net.values())


def test_a_live_adjustment_buys_back_before_it_sells(env):
    a = _live(env, STR_ADJUST='recenter', STR_ADJUST_PTS='200', STR_TARGET_PCT='500', STR_STOP_PCT='500')
    a.tick(at(THU, 15, 15))
    before = len(a.broker.orders)
    a.spot = 23260.0
    a.tick(at(date(2026, 6, 12), 10, 0))
    assert [o['action'] for o in a.broker.orders[before:]] == ['BUY', 'BUY', 'SELL', 'SELL']
    assert a.state['position']['centre'] == 23250


def test_a_failed_buy_back_stops_that_accounts_new_short(env):
    a = _live(env, STR_ADJUST='roll_untested', STR_ADJUST_PTS='200', STR_TARGET_PCT='500', STR_STOP_PCT='500')
    a.tick(at(THU, 15, 15))
    before = len(a.broker.orders)
    a.broker.refuse = {(tsym(23000, 'PE'), 'BUY')}
    a.spot = 23260.0
    a.tick(at(date(2026, 6, 12), 10, 0))
    assert [o['action'] for o in a.broker.orders[before:]] == ['BUY']
    assert a.state['position']['accounts']['1']['held'][tsym(23000, 'PE')] == -130


def test_no_quotes_on_an_open_position_alerts_and_the_expiry_still_closes_it(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    a.spot = None
    a.tick(at(date(2026, 6, 12), 10, 0))
    assert any('NO QUOTES' in t for _, t in a.alerts) and a.state['position'] and not a.state['position']['exit']
    a.tick(at(TUE, 15, 25))
    assert not a.state.get('position') and sa.load_history()[-1]['reason'] == 'Expiry'


def test_nothing_is_decided_before_the_open_or_after_the_close(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    before = len(a.broker.orders)
    a.px = {tsym(23000, 'CE'): 900.0, tsym(23000, 'PE'): 900.0}
    a.tick(at(date(2026, 6, 12), 9, 14))
    a.tick(at(THU, 15, 30))
    assert len(a.broker.orders) == before and a.state['position']


def test_a_contract_that_expired_unwatched_is_booked_and_flagged(env):
    a = _live(env)
    a.tick(at(THU, 15, 15))
    a.tick(at(date(2026, 6, 17), 9, 16))
    assert not a.state.get('position')
    row = sa.load_history()[-1]
    assert row['reason'] == 'Expired unattended' and row['accounts']['1']['unconfirmed']
    assert any('expired' in t for _, t in a.alerts)


# ── settings and the thread's start ──────────────────────────────────────────

def test_clean_settings_refuses_what_the_form_cannot_mean():
    cur = dict(sa.DEFAULTS)
    assert sa.clean_settings({'target_pct': 40}, cur) == {'STR_TARGET_PCT': '40.0'}
    assert sa.clean_settings({'cadence': 'monthly', 'entry_day': 'last_friday'}, cur) == {
        'STR_CADENCE': 'monthly', 'STR_ENTRY_DAY': 'last_friday'}
    for bad in ({'mode': 'yes'}, {'cadence': 'monthly'}, {'entry_day': 'last_friday'}, {'adjust': 'x'},
                {'lock_to': 45}, {'wings': 120}, {'lots': 0}, {'entry_time': '15:29'}, {'entry_time': 'soon'}):
        with pytest.raises(ValueError):
            sa.clean_settings(bad, cur)


def test_the_defaults_are_the_form_it_was_chosen_on(env):
    env.clear()
    s = sa.settings('T')
    assert (s['mode'], s['cadence'], s['entry_day'], s['skip_one_night'], s['entry_time']) == ('paper', 'weekly', 'monday', False, '15:15')
    assert (s['target_pct'], s['stop_pct'], s['adjust'], s['adjust_pts'], s['max_adjust']) == (50.0, 25.0, 'recenter', 200.0, 2)
    assert (s['wings'], s['lock_at'], s['lock_to'], s['lots']) == (0, 40.0, 30.0, 1)
    assert (s['compounding'], s['start_capital'], s['per_lot']) == (True, 250000.0, 250000.0)


def test_stop_does_not_leave_a_position_unwatched(env, monkeypatch):
    started = []
    monkeypatch.setattr(sa.StraddleAlgo, 'start', lambda self: started.append(self))
    monkeypatch.setattr(sa, '_now', lambda: at(THU, 10, 0))
    env['STR_ENABLED'] = 'false'
    assert sa.ensure_running('T') is False
    a = Algo()
    a.state['position'] = {'id': 'x'}
    a._save_state()
    assert sa.ensure_running('T') is True and started


def test_every_entry_rule_picks_the_backtests_own_dates():
    span = date(2026, 2, 1), date(2026, 10, 31)
    days = [span[0] + timedelta(i) for i in range((span[1] - span[0]).days + 1)]
    sessions = eng.known_sessions(date(2026, 6, 1), back=160, ahead=240)
    for cadence in ose.CADENCES:
        for rule in ose.entry_rules(cadence):
            want = set(ose.entry_days(sessions, *span, rule, cadence))
            assert {d for d in days if d in sessions and eng.is_entry_day(d, rule, cadence)} == want, (cadence, rule)
