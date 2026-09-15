"""EMA Confluence Breakout — futures pricing / carry-forward.

The properties pinned here are the ones that make the backtest describe the
trade the live algo actually places: the money moves onto the contract, an open
position is carried forward at the same roll moment the live algo uses, and
every order — a roll's two legs included — is charged.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import ema_futures_pricing as fp

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope='module')
def spot():
    """A rising daily spot series across 2024, IST-normalised like the engine's."""
    days = pd.bdate_range('2024-01-01', '2024-12-31', tz='Asia/Kolkata')
    close = np.linspace(100.0, 200.0, len(days))
    return pd.DataFrame({'datetime': days, 'close': close})


def _fetcher(spot_df, basis_per_day=0.02, missing=()):
    """Futures closes for any contract: spot plus a basis that decays to zero
    at expiry — the shape a real monthly contract's premium has."""
    closes = {ts.date(): float(c) for ts, c in zip(spot_df['datetime'], spot_df['close'])}

    def fetch(expiry):
        if expiry in missing:
            return None
        rows = {d: c + max(0, (expiry - d).days) * basis_per_day
                for d, c in closes.items() if 0 <= (expiry - d).days <= 100}
        return pd.Series(rows) if rows else None
    return fetch


def _trade(entry, exit_, entry_price, exit_price, direction='Long', reason='TARGET'):
    return {'entry_time': f'{entry}T09:15:00+05:30', 'exit_time': f'{exit_}T00:00:00+05:30',
            'type': direction, 'entry_price': entry_price, 'exit_price': exit_price,
            'pnl': (exit_price - entry_price) if direction == 'Long' else (entry_price - exit_price),
            'pnl_pct': 0.0, 'sl_price': entry_price * 0.95,
            'target_price': exit_price, 'exit_reason': reason, 'result': 'WIN'}


# ── Contract calendar ────────────────────────────────────────────────────


def test_monthly_expiries_match_nse_2024(spot):
    """Last Thursday of each month, which is what NSE actually settled on."""
    got = fp.monthly_expiries([ts.date() for ts in spot['datetime']])
    for expected in (date(2024, 1, 25), date(2024, 2, 29), date(2024, 3, 28),
                     date(2024, 8, 29), date(2024, 12, 26)):
        assert expected in got


def test_expiry_weekday_regime_change_is_honoured():
    """NSE moved Thursday → Tuesday on 2025-09-01; a run spanning it must use
    both rules, not whichever one happens to be current."""
    days = [d.date() for d in pd.bdate_range('2025-06-01', '2025-12-31')]
    got = fp.monthly_expiries(days)
    assert date(2025, 7, 31) in got      # Thursday regime
    assert date(2025, 9, 30) in got      # Tuesday regime
    assert date(2025, 12, 30) in got     # Tuesday


def test_roll_day_is_three_sessions_before_expiry(spot):
    days = [ts.date() for ts in spot['datetime']]
    timeline = {c['expiry']: c['roll_day'] for c in fp.contract_timeline(days)}
    # 2024-01-25 was a Thursday; three sessions earlier is Monday the 22nd.
    assert timeline[date(2024, 1, 25)] == date(2024, 1, 22)


def test_select_contract_moves_to_the_far_month_inside_the_roll_window(spot):
    days = [ts.date() for ts in spot['datetime']]
    timeline = fp.contract_timeline(days)
    assert fp.select_contract(date(2024, 1, 19), timeline)['expiry'] == date(2024, 1, 25)
    # On the roll day itself the strategy is already on February.
    assert fp.select_contract(date(2024, 1, 22), timeline)['expiry'] == date(2024, 2, 29)


def test_roll_sessions_match_the_live_algo():
    """The backtest's roll window is the live algo's, or the backtest is
    describing a position the algo does not hold."""
    from trading_app.algo.ema_confluence.ema_confluence_algo import _ROLL_SESSIONS_BEFORE_EXPIRY
    assert fp.ROLL_SESSIONS_BEFORE_EXPIRY == _ROLL_SESSIONS_BEFORE_EXPIRY


# ── Pricing ──────────────────────────────────────────────────────────────


def test_short_trade_inside_one_month_never_rolls(spot):
    trades = [_trade('2024-01-03', '2024-01-15', 105.0, 110.0)]
    stats = fp.apply_futures_pricing(trades, spot, _fetcher(spot), lots=1, lot_size=50)
    t = trades[0]
    assert t['rolls'] == 0
    assert len(t['legs']) == 1
    assert t['contract'] == 'JAN 2024' == t['exit_contract']
    # Two orders, both at ₹1,000.
    assert t['orders'] == 2
    assert t['brokerage'] == 2 * fp.BROKERAGE_PER_ORDER == stats['brokerage']


def test_carried_position_rolls_once_per_expiry(spot):
    """Held from January into April: three roll moments, four contracts."""
    trades = [_trade('2024-01-10', '2024-04-10', 110.0, 140.0)]
    fp.apply_futures_pricing(trades, spot, _fetcher(spot), lots=1, lot_size=50)
    t = trades[0]
    assert [leg['contract'] for leg in t['legs']] == [
        'JAN 2024', 'FEB 2024', 'MAR 2024', 'APR 2024']
    assert t['rolls'] == 3
    # Entry, exit, and two orders per roll.
    assert t['orders'] == 8
    assert t['brokerage'] == 8 * fp.BROKERAGE_PER_ORDER
    # Each roll's legs join up: the near leg is booked and the far one opened
    # on the SAME session.
    for near, far in zip(t['legs'], t['legs'][1:]):
        assert near['exit_date'] == far['entry_date']


def test_an_exit_on_the_roll_day_wins_over_the_roll(spot):
    """Same precedence as a live tick — SL/Target are evaluated before rolls,
    so a trade that ends that day ends; it does not roll and then end."""
    trades = [_trade('2024-01-10', '2024-01-22', 110.0, 118.0)]
    fp.apply_futures_pricing(trades, spot, _fetcher(spot), lots=1, lot_size=50)
    assert trades[0]['rolls'] == 0
    assert trades[0]['contract'] == 'JAN 2024'


def test_pnl_is_the_futures_move_and_the_spot_record_survives(spot):
    trades = [_trade('2024-01-03', '2024-01-15', 105.0, 110.0)]
    fp.apply_futures_pricing(trades, spot, _fetcher(spot), lots=2, lot_size=50)
    t = trades[0]
    assert t['spot_entry_price'] == 105.0 and t['spot_exit_price'] == 110.0
    assert t['spot_pnl'] == 5.0
    # Prices are on the contract's scale, and both carry a positive basis that
    # shrinks as expiry nears — so the future gains slightly less than spot.
    assert t['entry_price'] > 105.0 and t['exit_price'] > 110.0
    assert 0 < t['pnl'] < 5.0
    assert t['qty'] == 100
    assert t['gross_pnl_rupees'] == pytest.approx(t['pnl'] * 100, abs=0.01)
    assert t['pnl_rupees'] == pytest.approx(t['gross_pnl_rupees'] - t['brokerage'], abs=0.01)


def test_rolling_a_long_pays_the_roll_spread(spot):
    """Carrying a long through contango costs the spread every month — the
    whole reason a carry has to be modelled rather than assumed free."""
    short = [_trade('2024-01-03', '2024-01-15', 105.0, 110.0)]
    carry = [_trade('2024-01-10', '2024-04-10', 110.0, 140.0)]
    fp.apply_futures_pricing(short, spot, _fetcher(spot), lots=1, lot_size=50)
    fp.apply_futures_pricing(carry, spot, _fetcher(spot), lots=1, lot_size=50)
    assert carry[0]['pnl'] < carry[0]['spot_pnl']       # rolls ate points
    assert carry[0]['brokerage'] > short[0]['brokerage']


def test_short_side_pnl_is_signed_the_other_way(spot):
    trades = [_trade('2024-01-03', '2024-01-15', 110.0, 105.0, direction='Short', reason='TARGET')]
    fp.apply_futures_pricing(trades, spot, _fetcher(spot), lots=1, lot_size=50)
    assert trades[0]['pnl'] > 0
    assert trades[0]['pnl_rupees'] == pytest.approx(
        trades[0]['pnl'] * 50 - trades[0]['brokerage'], abs=0.01)


def test_a_contract_with_no_history_falls_back_to_the_spot_scale(spot):
    """A data hole must cost accuracy, never the trade itself."""
    trades = [_trade('2024-01-03', '2024-01-15', 105.0, 110.0)]
    stats = fp.apply_futures_pricing(
        trades, spot, _fetcher(spot, missing={date(2024, 1, 25)}), lots=1, lot_size=50)
    t = trades[0]
    assert t['entry_price'] == 105.0 and t['exit_price'] == 110.0   # spot scale
    assert t['pnl'] == 5.0
    assert t['futures_priced'] is False
    assert stats['unpriced_legs'] == 1 and stats['spot_priced_trades'] == 1
    assert stats['missing_contracts'] == ['2024-01-25']
    assert t['rolls'] == 0 and t['brokerage'] == 2 * fp.BROKERAGE_PER_ORDER


def test_a_partial_data_hole_never_mixes_the_two_scales(spot):
    """One end priced on the contract and the other on spot would invent a
    whole basis of P&L out of the gap — the trade falls back wholesale."""
    # A carry that crosses four contracts, with one month's history missing.
    trades = [_trade('2024-01-10', '2024-04-10', 110.0, 140.0)]
    fetch = _fetcher(spot, missing={date(2024, 3, 28)})
    stats = fp.apply_futures_pricing(trades, spot, fetch, lots=1, lot_size=50)
    t = trades[0]
    # Every leg is on the spot scale, so the P&L is exactly the spot move —
    # no phantom points from the priced months.
    assert t['futures_priced'] is False
    assert t['pnl'] == t['spot_pnl'] == 30.0
    assert stats['spot_priced_trades'] == 1
    assert stats['unpriced_legs'] == len(t['legs']) == 4
    # …and it is still the same trade, carried the same way.
    assert t['rolls'] == 3 and t['orders'] == 8


def test_pricing_never_changes_which_trades_happened(spot):
    before = [_trade('2024-01-03', '2024-01-15', 105.0, 110.0),
              _trade('2024-02-05', '2024-05-20', 120.0, 150.0),
              _trade('2024-06-03', '2024-06-20', 155.0, 150.0, reason='SL')]
    times = [(t['entry_time'], t['exit_time'], t['type'], t['exit_reason']) for t in before]
    fp.apply_futures_pricing(before, spot, _fetcher(spot), lots=1, lot_size=25)
    assert [(t['entry_time'], t['exit_time'], t['type'], t['exit_reason']) for t in before] == times


def test_rupee_view_reclassifies_a_win_that_brokerage_turned_into_a_loss():
    trade = {'pnl': 4.0, 'pnl_rupees': -1200.0, 'result': 'WIN'}
    view = fp.rupee_view(trade)
    assert view['pnl'] == -1200.0
    assert view['result'] == 'LOSS'


# ── Contract store ───────────────────────────────────────────────────────
# The store is what makes futures pricing affordable at all: Breeze allows 100
# requests a minute against a budget the live algos share, so a contract must
# be paid for once and a run must be able to stop spending.


class _StubBreeze:
    """Minimal stand-in for IciciDataServiceAdapter.historical_future."""

    def __init__(self):
        self.calls = []

    def historical_future(self, root, expiry, from_date, to_date, interval,
                          exchange_code='NFO'):
        from datetime import datetime, timedelta
        self.calls.append((root, expiry, exchange_code))
        start = expiry - timedelta(days=20)
        return [{'date': datetime.combine(start + timedelta(days=i), datetime.min.time()),
                 'open': 100 + i, 'high': 101 + i, 'low': 99 + i,
                 'close': 100.5 + i, 'volume': 10} for i in range(15)]

    def last_history_error(self):
        return None


@pytest.fixture
def store(tmp_path, monkeypatch):
    from trading_app.filters import futures_candle_store as fcs
    monkeypatch.setattr(fcs, 'DATA_DIR', str(tmp_path))
    fcs._mem.clear()
    return fcs


def test_settled_contract_is_paid_for_once(store):
    broker = _StubBreeze()
    fetch = store.Fetcher(broker, 'SBIN', 'NFO', store.Budget(5))
    first = fetch(date(2024, 1, 25))
    assert first is not None and len(first) == 15
    fetch(date(2024, 1, 25))
    store._mem.clear()                                    # force the disk path
    store.Fetcher(broker, 'SBIN', 'NFO', store.Budget(5))(date(2024, 1, 25))
    assert len(broker.calls) == 1
    assert broker.calls[0][2] == 'NFO'


def test_budget_stops_new_fetches_but_still_serves_the_store(store):
    broker = _StubBreeze()
    store.Fetcher(broker, 'SBIN', 'NFO', store.Budget(1))(date(2024, 1, 25))

    spent = store.Budget(0)
    fetch = store.Fetcher(broker, 'SBIN', 'NFO', spent)
    assert fetch(date(2024, 1, 25)) is not None            # already on disk — free
    assert fetch(date(2024, 2, 29)) is None                # not on disk — skipped
    assert spent.skipped == 1
    assert len(broker.calls) == 1


def test_budget_is_shared_across_symbols(store):
    """An All-Stocks scan rations ONE app-wide Breeze budget, not a per-symbol
    one — every worker thread draws from the same allowance."""
    broker = _StubBreeze()
    shared = store.Budget(2)
    for symbol in ('SBIN', 'INFY', 'TCS'):
        store.Fetcher(broker, symbol, 'NFO', shared)(date(2024, 1, 25))
    assert len(broker.calls) == 2
    assert shared.spent == 2 and shared.skipped == 1


def test_a_corporate_action_basis_is_rejected_not_traded_on(spot):
    """The daily spot store is split/bonus-ADJUSTED and the broker's futures
    history is not, so across a corporate action the two series disagree by the
    ratio itself. Taken as a basis that fabricates P&L — RELIANCE's 1:1 bonus
    turned a 204-point trade into 1,602 — so an implausible basis is refused
    and the trade priced on spot."""
    closes = {ts.date(): float(c) for ts, c in zip(spot['datetime'], spot['close'])}

    def bonus_fetch(expiry):
        # Contracts up to Feb quote UNADJUSTED (double); later ones agree.
        factor = 2.0 if expiry < date(2024, 3, 1) else 1.0
        rows = {d: c * factor for d, c in closes.items() if 0 <= (expiry - d).days <= 100}
        return pd.Series(rows)

    trades = [_trade('2024-01-10', '2024-04-10', 110.0, 140.0)]
    stats = fp.apply_futures_pricing(trades, spot, bonus_fetch, lots=1, lot_size=50)
    t = trades[0]
    assert t['futures_priced'] is False
    assert t['pnl'] == t['spot_pnl'] == 30.0        # no fabricated points
    assert stats['spot_priced_trades'] == 1
    assert stats['implausible_basis']               # and it says which contracts
