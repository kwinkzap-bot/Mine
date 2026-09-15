"""EMA Confluence — decisions on the STOCK, orders on the FUTURE.

The strategy is validated by a backtest that reads equity candles, so every
level it produces (trigger, SL, and the Target derived from the entry) lives on
the UNDERLYING's price scale. The paper order, though, is held in a monthly
FUTURES contract. These pin the split: the underlying decides WHETHER to act,
the contract decides at WHAT PRICE and for how much.

They exist because the two used to be one — equity-scale levels were compared
straight against the future's LTP. The basis is ~0.5% on a typical name in this
universe, which on a 3% target is a fifth of the trade, and it filled longs late
while stopping shorts out early.
"""
import json
from datetime import date, datetime

import pytest

from trading_app.algo.ema_confluence import ema_confluence_algo as eca
from trading_app.algo.ema_confluence.ema_confluence_algo import EmaConfluenceAlgo

# The `_tick` tests below never touch the state FILE, so their symbol is just a
# label. The two `_load_state` tests do, and _load_state now retires any symbol
# no longer in EMA_SYMBOL_DEFAULTS (see _retire_dropped_symbols) — so those take
# a symbol read out of the live universe rather than a hard-coded ticker, which
# would fail again the next time that table is edited.
from trading_app.Backtest.ema_symbol_universe import EMA_SYMBOL_DEFAULTS

IN_UNIVERSE = next(iter(EMA_SYMBOL_DEFAULTS))

SEP = {'symbol': 'NSE:NHPC26SEPFUT', 'expiry': date(2026, 9, 29), 'lot_size': 5400}
OCT = {'symbol': 'NSE:NHPC26OCTFUT', 'expiry': date(2026, 10, 27), 'lot_size': 5400}
SPOT = 'NSE:NHPC-EQ'
CHAIN = [SEP, OCT]

# A Tuesday, mid-session, nowhere near SEP's roll window (2026-09-24).
NOW = datetime(2026, 9, 8, 11, 0)


class FakeProvider:
    def __init__(self, prices):
        self.fyers = object()
        self.prices = prices
        self.ltp_calls = []

    def list_future_contracts(self, symbol):
        return list(CHAIN)

    def ltp(self, tokens):
        self.ltp_calls.append(list(tokens))
        return {t: {'last_price': p} for t, p in self.prices.items() if t in tokens}


@pytest.fixture
def algo(tmp_path, monkeypatch):
    monkeypatch.setattr(eca, 'STATE_FILE', str(tmp_path / 'state.json'))
    monkeypatch.setattr(eca, 'HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(eca, 'ALL_HISTORY_FILE', str(tmp_path / 'all_history.json'))
    a = EmaConfluenceAlgo(username='test-user')
    monkeypatch.setattr(a, '_run_daily_scan', lambda *A, **K: None)
    monkeypatch.setattr(a, '_uvar', lambda key, default='': default)
    monkeypatch.setattr(a, '_notify_new_entry', lambda *A, **K: None)
    monkeypatch.setattr(a, '_notify_exit', lambda *A, **K: None)
    a._history_path = tmp_path / 'history.json'
    return a


def _state(stocks):
    return {'last_scan_date': date.today().isoformat(), 'stocks': stocks}


def _history(algo):
    try:
        with open(algo._history_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def _watching(**over):
    s = {'phase': 'watching', 'direction': 'Long', 'trigger_level': 80.0,
         'sl_level': 74.0, 'target_pct': 8.0, 'signal_date': '2026-09-01',
         'future_token': SEP['symbol'], 'future_month': 'SEP 2026',
         'future_expiry': '2026-09-29', 'lot_size': 5400}
    s.update(over)
    return s


def _in_position(**over):
    s = {'phase': 'in_position', 'direction': 'Long', 'trigger_level': 80.0,
         'sl_level': 74.0, 'target_level': 86.4, 'target_pct': 8.0,
         'signal_date': '2026-09-01', 'spot_entry_price': 80.0,
         'entry_price': 80.4, 'entry_time': '2026-09-02T10:15:00', 'qty': 5400,
         'ltp': 80.4, 'future_token': SEP['symbol'], 'future_month': 'SEP 2026',
         'future_expiry': '2026-09-29', 'lot_size': 5400}
    s.update(over)
    return s


# ── the entry trigger ────────────────────────────────────────────────────

def test_a_future_through_the_trigger_does_not_fire_while_spot_is_short_of_it(algo):
    """The whole bug in one test. The future trades at 80.4, past the 80.0
    trigger; the stock is still at 79.6. The signal candle's High was a STOCK
    price, so nothing has broken out and no trade may open."""
    s = _watching()
    provider = FakeProvider({SEP['symbol']: 80.4, SPOT: 79.6})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'watching'
    assert 'entry_price' not in s
    assert _history(algo) == []


def test_spot_through_the_trigger_fires_and_fills_on_the_contract(algo):
    s = _watching()
    provider = FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'
    assert s['spot_entry_price'] == 80.1     # what broke the trigger
    assert s['entry_price'] == 80.55         # what the order actually filled at
    assert s['qty'] == 5400


def test_the_target_is_a_percentage_of_the_spot_fill_not_the_futures_fill(algo):
    """8% of the stock's 80.1 is 86.51. Off the future's 80.55 it would be
    86.99 — a level the stock might never print, and the number the backtest's
    swept target_pct would no longer mean."""
    s = _watching()
    provider = FakeProvider({SEP['symbol']: 80.55, SPOT: 80.1})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['target_level'] == round(80.1 * 1.08, 2) == 86.51


def test_a_short_trigger_is_also_judged_on_spot(algo):
    s = _watching(direction='Short', trigger_level=80.0, sl_level=86.0)
    # Future still above the trigger, stock already below it: this is a fill.
    provider = FakeProvider({SEP['symbol']: 80.3, SPOT: 79.8})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'
    assert s['spot_entry_price'] == 79.8
    assert s['target_level'] == round(79.8 * 0.92, 2)


# ── the exits ────────────────────────────────────────────────────────────

def test_an_sl_is_judged_on_spot_alone(algo):
    """Future below the 74.0 SL, stock above it — the stop has NOT been hit.
    This is the case that used to stop a position out early on the basis."""
    s = _in_position()
    provider = FakeProvider({SEP['symbol']: 73.8, SPOT: 74.4})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'
    assert _history(algo) == []


def test_an_exit_is_decided_on_spot_and_booked_on_the_contract(algo):
    s = _in_position()
    provider = FakeProvider({SEP['symbol']: 86.9, SPOT: 86.5})   # target 86.4
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    hist = _history(algo)
    assert len(hist) == 1
    rec = hist[0]
    assert rec['reason'] == 'TARGET'
    assert rec['spot_exit_price'] == 86.5           # the level that broke
    assert rec['exit_price'] == 86.9               # the contract's own quote
    assert rec['pnl'] == round((86.9 - 80.4) * 5400, 2)
    assert s['phase'] == 'pending_scan'


def test_the_pnl_never_comes_off_the_spot_scale(algo):
    """A short, so a P&L computed off the wrong scale would be obvious."""
    s = _in_position(direction='Short', sl_level=86.0, target_level=73.6,
                     spot_entry_price=80.0, entry_price=80.4)
    provider = FakeProvider({SEP['symbol']: 73.2, SPOT: 73.5})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    rec = _history(algo)[0]
    assert rec['reason'] == 'TARGET'
    assert rec['pnl'] == round((80.4 - 73.2) * 5400, 2)


# ── when a price is missing ──────────────────────────────────────────────

def test_no_spot_quote_means_nothing_is_evaluated(algo):
    """It must never silently fall back to the future's price — that is the
    substitution these tests exist to forbid. Skipping a 15s tick costs a
    multi-week swing nothing."""
    s = _in_position()
    provider = FakeProvider({SEP['symbol']: 60.0})     # far below the 74.0 SL
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'in_position'
    assert _history(algo) == []
    assert s['ltp'] == 60.0                             # still marked to market


def test_no_futures_quote_defers_the_entry_and_keeps_the_trigger_armed(algo):
    """The stock broke out but the contract did not quote. Filling at a stale
    mark would invent a trade at a price that never traded."""
    s = _watching()
    provider = FakeProvider({SPOT: 80.1})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    assert s['phase'] == 'watching'
    assert s['trigger_level'] == 80.0
    assert _history(algo) == []


def test_an_exit_with_no_futures_quote_books_at_the_last_mark(algo):
    """Unlike an entry this cannot wait — the strategy has said the trade is
    over, so it books at the last mark rather than carrying a closed position."""
    s = _in_position(ltp=86.75)
    provider = FakeProvider({SPOT: 86.5})
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, NOW)

    rec = _history(algo)[0]
    assert rec['reason'] == 'TARGET'
    assert rec['exit_price'] == 86.75


# ── the roll ─────────────────────────────────────────────────────────────

def test_a_roll_re_stamps_only_the_futures_side(algo):
    """The underlying has no contracts, so nothing on its scale can roll. This
    is what used to leave OIL on a 4.65% target configured as 8%."""
    s = _in_position()
    provider = FakeProvider({SEP['symbol']: 84.0, OCT['symbol']: 84.8, SPOT: 83.6})
    roll_now = datetime(2026, 9, 24, 12, 0)      # 3 sessions before SEP expiry
    algo._tick(provider, True, _state({'NHPC': s}), True, 1, roll_now)

    assert s['future_token'] == OCT['symbol']
    assert s['entry_price'] == 84.8              # futures fill re-stamped
    assert s['spot_entry_price'] == 80.0         # spot fill untouched
    assert s['target_level'] == 86.4             # still 8% of the spot entry
    assert s['sl_level'] == 74.0


# ── migrating what was already open ──────────────────────────────────────

def test_migration_rebuilds_a_futures_scale_target_from_the_trigger(algo, tmp_path):
    """OIL's real numbers: an 8% target that had drifted to 4.65% of its
    entry after one roll, because the level was re-derived off each contract."""
    stocks = {'OIL': {'phase': 'in_position', 'direction': 'Long',
                      'trigger_level': 458.0, 'sl_level': 435.4,
                      'target_level': 494.64, 'target_pct': 8,
                      'entry_price': 472.65, 'qty': 1400}}
    with open(eca.STATE_FILE, 'w') as f:
        json.dump({'last_scan_date': None, 'stocks': stocks}, f)

    loaded = algo._load_state()['stocks']['OIL']
    assert loaded['spot_entry_price'] == 458.0
    assert loaded['target_level'] == round(458.0 * 1.08, 2) == 494.64
    assert loaded['entry_price'] == 472.65        # the real futures fill stands
    assert loaded['sl_level'] == 435.4            # always came off the candle


def test_migration_repairs_a_target_that_actually_drifted(algo):
    """BPCL: entered at a futures 324.45 on a spot trigger of 319.75, so its
    15% target sat 1.5% too high."""
    stocks = {'BPCL': {'phase': 'in_position', 'direction': 'Long',
                       'trigger_level': 319.75, 'sl_level': 311.1,
                       'target_level': 373.12, 'target_pct': 15,
                       'entry_price': 324.45, 'qty': 1975}}
    with open(eca.STATE_FILE, 'w') as f:
        json.dump({'last_scan_date': None, 'stocks': stocks}, f)

    loaded = algo._load_state()['stocks']['BPCL']
    assert loaded['target_level'] == round(319.75 * 1.15, 2) == 367.71


def test_migration_is_idempotent_and_leaves_new_trades_alone(algo):
    stocks = {IN_UNIVERSE: _in_position()}
    with open(eca.STATE_FILE, 'w') as f:
        json.dump({'last_scan_date': None, 'stocks': stocks}, f)

    once = algo._load_state()['stocks'][IN_UNIVERSE]
    assert once['spot_entry_price'] == 80.0 and once['target_level'] == 86.4
    twice = algo._load_state()['stocks'][IN_UNIVERSE]
    assert twice['spot_entry_price'] == 80.0 and twice['target_level'] == 86.4


def test_a_broken_migration_never_costs_the_open_positions(algo, monkeypatch):
    """_load_state's bare except returns a FRESH state — every open position
    abandoned. The migration must not be able to reach it."""
    stocks = {IN_UNIVERSE: _in_position(), 'OIL': {'phase': 'watching'}}
    with open(eca.STATE_FILE, 'w') as f:
        json.dump({'last_scan_date': '2026-09-07', 'stocks': stocks}, f)

    def boom(_state):
        raise RuntimeError('migration exploded')
    monkeypatch.setattr(algo, '_migrate_levels_to_spot_scale', boom)

    loaded = algo._load_state()
    assert loaded['last_scan_date'] == '2026-09-07'
    assert loaded['stocks'][IN_UNIVERSE]['phase'] == 'in_position'
