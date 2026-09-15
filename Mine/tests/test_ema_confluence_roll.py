"""EMA Confluence — monthly futures contract roll.

A monthly future expires but a multi-day swing doesn't, so three trading days
before expiry (12:00) the open leg is booked out on the near contract and the
same side reopened on the far one. These pin down both the pure selection rule
and the execution, including the ways it must REFUSE to roll.

Dates used throughout: the AUG 2026 contract expires Tue 2026-08-25, so the
roll day is Thu 2026-08-20 (back 3 sessions: Mon 24, Fri 21, Thu 20).
"""
import json
from datetime import date, datetime

import pytest

from trading_app.algo.ema_confluence import ema_confluence_algo as eca
from trading_app.algo.ema_confluence.ema_confluence_algo import (
    EmaConfluenceAlgo, roll_due_at, select_contract)

AUG = {'symbol': 'NSE:NHPC26AUGFUT', 'expiry': date(2026, 8, 25), 'lot_size': 5400}
SEP = {'symbol': 'NSE:NHPC26SEPFUT', 'expiry': date(2026, 9, 29), 'lot_size': 5400}
OCT = {'symbol': 'NSE:NHPC26OCTFUT', 'expiry': date(2026, 10, 27), 'lot_size': 6000}
CHAIN = [AUG, SEP, OCT]
# The UNDERLYING. Every trigger/SL/Target decision is judged on this, never on a
# contract above — so it has to be quoted on any tick that is expected to act.
SPOT = 'NSE:NHPC-EQ'


# ── the pure rule ────────────────────────────────────────────────────────

def test_roll_moment_is_noon_three_sessions_before_expiry():
    assert roll_due_at(date(2026, 8, 25)) == datetime(2026, 8, 20, 12, 0)


def test_roll_moment_counts_sessions_not_calendar_days():
    # Apr 8 expiry: Tue 7, Mon 6, then Thu 2 (Fri 3 is Good Friday, then the
    # weekend). Calendar-day counting would have said Sun 5 — not a session.
    assert roll_due_at(date(2026, 4, 8)) == datetime(2026, 4, 2, 12, 0)


def test_roll_moment_never_lands_after_expiry():
    # A contract expiring the day after a long weekend still rolls by expiry.
    assert roll_due_at(date(2026, 1, 2), sessions=0) <= datetime(2026, 1, 2, 12, 0)


def test_front_month_held_until_noon_on_the_roll_day():
    assert select_contract(datetime(2026, 8, 20, 11, 59), CHAIN) is AUG
    assert select_contract(datetime(2026, 8, 19, 15, 0), CHAIN) is AUG


def test_far_month_selected_from_noon_on_the_roll_day():
    assert select_contract(datetime(2026, 8, 20, 12, 0), CHAIN) is SEP
    assert select_contract(datetime(2026, 8, 21, 9, 20), CHAIN) is SEP


def test_a_missed_roll_moment_stays_due_so_the_next_session_takes_it():
    # Roll day passed with the market shut / the algo down — still SEP, not a
    # skipped roll. This is what makes "next trading session" free.
    assert select_contract(datetime(2026, 8, 24, 9, 20), CHAIN) is SEP


def test_position_opened_inside_the_window_does_not_roll_again():
    # Already on SEP at a moment when AUG is roll-due: the answer is SEP, so
    # the caller compares equal to what it holds and does nothing.
    now = datetime(2026, 8, 21, 12, 30)
    assert select_contract(now, CHAIN) is SEP
    assert select_contract(now, [SEP, OCT]) is SEP


def test_single_listed_contract_clamps_instead_of_going_dark():
    assert select_contract(datetime(2026, 8, 24, 12, 0), [AUG]) is AUG


def test_empty_chain_and_unknown_expiry():
    assert select_contract(datetime(2026, 8, 20, 12, 0), []) is None
    # A contract whose expiry didn't parse is skipped, never rolled onto.
    assert select_contract(datetime(2026, 8, 20, 12, 0),
                           [{'symbol': 'X', 'expiry': None}, SEP]) is SEP


# ── execution ────────────────────────────────────────────────────────────

class FakeProvider:
    """Minimal stand-in for the Fyers adapter — the `fyers` attribute is what
    the algo sniffs to pick its provider branch."""

    def __init__(self, chain=CHAIN, prices=None):
        self.fyers = object()
        self.chain = chain
        self.prices = prices or {}
        self.ltp_calls = []

    def list_future_contracts(self, symbol):
        return list(self.chain)

    def ltp(self, tokens):
        self.ltp_calls.append(list(tokens))
        return {t: {'last_price': p} for t, p in self.prices.items() if t in tokens}


@pytest.fixture
def algo(tmp_path, monkeypatch):
    monkeypatch.setattr(eca, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(eca, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(eca, 'ALL_HISTORY_FILE', str(tmp_path / 'all_history.json'))
    a = EmaConfluenceAlgo(username='test-user')
    # No candle store, no env file, no Telegram.
    monkeypatch.setattr(a, '_run_daily_scan', lambda *A, **K: None)
    monkeypatch.setattr(a, '_uvar', lambda key, default='': default)
    monkeypatch.setattr(a, '_notify_roll', lambda *A, **K: None)
    a._history_path = tmp_path / 'history.json'
    return a


def _open_position(**over):
    s = {
        'phase': 'in_position', 'direction': 'Long',
        'trigger_level': 77.0, 'sl_level': 71.5, 'target_level': 84.5,
        'target_pct': 5.0, 'signal_date': '2026-08-05',
        'entry_price': 77.5, 'spot_entry_price': 77.0,
        'entry_time': '2026-08-06T10:15:00', 'qty': 5400,
        'ltp': 79.0, 'future_token': AUG['symbol'], 'future_month': 'AUG 2026',
        'future_expiry': '2026-08-25', 'lot_size': 5400,
    }
    s.update(over)
    return s


def _state(stocks):
    return {'last_scan_date': date.today().isoformat(), 'stocks': stocks}


def _history(algo):
    try:
        with open(algo._history_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


ROLL_NOW = datetime(2026, 8, 20, 12, 0)     # a Thursday, a real session
BEFORE   = datetime(2026, 8, 20, 11, 59)


def test_open_position_rolls_to_the_next_month(algo):
    s = _open_position()
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    hist = _history(algo)
    assert len(hist) == 1
    assert hist[0]['reason'] == 'ROLL'
    assert hist[0]['exit_price'] == 80.0                 # booked at the near LTP
    assert hist[0]['pnl'] == round((80.0 - 77.5) * 5400, 2)

    assert s['phase'] == 'in_position'
    assert s['future_token'] == SEP['symbol']
    assert s['future_month'] == 'SEP 2026'
    assert s['future_expiry'] == '2026-09-29'
    assert s['entry_price'] == 80.9                      # re-entered at the far LTP
    assert s['roll_count'] == 1
    assert s['rolled_from'] == 'AUG 2026'
    assert 'roll_to_token' not in s


def test_the_rolled_leg_keeps_the_setup_it_was_armed_with(algo):
    s = _open_position()
    before = {k: s[k] for k in
              ('direction', 'sl_level', 'target_level', 'target_pct', 'signal_date',
               'trigger_level', 'spot_entry_price')}
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)
    assert {k: s[k] for k in before} == before


def test_both_legs_and_the_underlying_are_priced_in_one_batched_quote_call(algo):
    """Three instruments, one request — the app-wide budget is 8 req/s and this
    algo sweeps ~145 symbols, so the spot quote must not cost a round trip."""
    s = _open_position()
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)
    assert len(provider.ltp_calls) == 1
    assert set(provider.ltp_calls[0]) == {AUG['symbol'], SEP['symbol'], SPOT}


def test_no_far_month_price_means_no_roll_and_no_flat_position(algo):
    s = _open_position()
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SPOT: 79.6})   # far month missing
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert _history(algo) == []
    assert s['phase'] == 'in_position'
    assert s['future_token'] == AUG['symbol']
    assert s['roll_to_token'] == SEP['symbol']                 # still pending
    assert 'roll_count' not in s


def test_nothing_happens_before_the_roll_moment(algo):
    s = _open_position()
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, BEFORE)

    assert _history(algo) == []
    assert s['future_token'] == AUG['symbol']
    assert 'roll_to_token' not in s
    # The held contract and its underlying, and nothing else — the far leg is
    # not quoted until there is actually a roll to price.
    assert set(provider.ltp_calls[0]) == {AUG['symbol'], SPOT}
    assert len(provider.ltp_calls) == 1


def test_a_position_already_on_the_far_month_is_left_alone(algo):
    s = _open_position(future_token=SEP['symbol'], future_month='SEP 2026',
                       future_expiry='2026-09-29')
    provider = FakeProvider(prices={SEP['symbol']: 80.9, OCT['symbol']: 81.5, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert _history(algo) == []
    assert s['future_token'] == SEP['symbol']
    assert 'roll_to_token' not in s


def test_strategy_exit_wins_over_a_roll_on_the_same_tick(algo):
    """SL hit on the near leg at the roll moment: the trade is over, so it is
    booked as SL and NOT carried into the next month."""
    s = _open_position()
    # The SPOT is what breaks the 71.5 SL. The future is quoted below it too,
    # but that is not what decides — see test_an_sl_is_judged_on_spot_alone.
    provider = FakeProvider(prices={AUG['symbol']: 70.0, SEP['symbol']: 70.9, SPOT: 69.8})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    hist = _history(algo)
    assert [h['reason'] for h in hist] == ['SL']
    assert hist[0]['exit_price'] == 70.0        # booked on the CONTRACT it is held in
    assert hist[0]['spot_exit_price'] == 69.8   # decided on the UNDERLYING
    assert s['phase'] == 'pending_scan'


def test_watching_symbol_switches_contract_without_a_trade(algo):
    s = {'phase': 'watching', 'direction': 'Long', 'trigger_level': 90.0,
         'sl_level': 71.5, 'target_pct': 5.0, 'signal_date': '2026-08-05',
         'future_token': AUG['symbol'], 'future_month': 'AUG 2026',
         'future_expiry': '2026-08-25', 'lot_size': 5400}
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    assert _history(algo) == []
    assert s['phase'] == 'watching'
    assert s['future_token'] == SEP['symbol']
    assert s['trigger_level'] == 90.0                          # armed level untouched


def test_position_with_no_stored_expiry_is_backfilled_then_rolled(algo):
    """The five positions live when this shipped predate future_expiry — they
    must not stay pinned forever."""
    s = _open_position()
    s.pop('future_expiry')
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, BEFORE)
    assert s['future_expiry'] == '2026-08-25'                  # backfilled, not rolled
    assert s['future_token'] == AUG['symbol']

    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)
    assert s['future_token'] == SEP['symbol']


def test_delisted_contract_is_booked_out_at_its_last_mark(algo):
    """No quote is ever coming for a contract that has left the master, so the
    position must not be stranded on it."""
    s = _open_position(future_token='NSE:NHPC26JULFUT', future_month='JUL 2026',
                       ltp=79.25)
    s.pop('future_expiry')
    provider = FakeProvider(prices={SEP['symbol']: 80.9, SPOT: 79.6})  # near leg gone
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, ROLL_NOW)

    hist = _history(algo)
    assert len(hist) == 1 and hist[0]['reason'] == 'ROLL'
    assert hist[0]['exit_price'] == 79.25                       # last known mark
    assert s['future_token'] == SEP['symbol']
    assert 'future_delisted' not in s


def test_holiday_tick_does_nothing_at_all(algo):
    s = _open_position()
    provider = FakeProvider(prices={AUG['symbol']: 80.0, SEP['symbol']: 80.9, SPOT: 79.6})
    # 2026-09-14 is Ganesh Chaturthi — a Monday the market is shut.
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, datetime(2026, 9, 14, 12, 0))

    assert _history(algo) == []
    assert provider.ltp_calls == []
    assert s['future_token'] == AUG['symbol']
