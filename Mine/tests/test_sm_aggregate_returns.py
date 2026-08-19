"""Swing Momentum — pooled CAGR / XIRR for the broker-group and section TOTALs.

Rates do not aggregate by averaging: two configs at +20% and +90% are not a
+55% portfolio. The group figure has to weight each config by how much money sat
in it and for how long, which is what pooling every dated cash flow into a
single XIRR solve does.

These pin the properties that make the pooled number right, not just present.
No create_app() — the helpers are imported directly.
"""
from datetime import date, timedelta

import pytest

from trading_app.app.routes.api import (
    _sm_config_flows, _sm_pooled_returns, _xirr)

TODAY = date(2026, 8, 18)


def cfg(cid, invested, live_since, log=None):
    return {'id': cid, 'investment': invested, 'live_since': live_since,
            'monthly_investment_log': log or []}


# ── the shared flow builder ──────────────────────────────────────────────

def test_opening_investment_is_money_out_and_the_mark_is_money_in():
    flows = _sm_config_flows(cfg('a', 100000, '2026-07-02'), TODAY, 107498.0)
    assert flows[0] == (date(2026, 7, 2), -100000.0)
    assert flows[-1] == (TODAY, 107498.0)


def test_sip_is_negative_and_swp_is_positive():
    """A SWP is logged as a negative amount, so negating it makes it a positive
    flow — money coming back out of the config, which is what XIRR needs."""
    c = cfg('a', 100000, '2026-07-02', [
        {'date': '2026-07-15', 'amount': 5000},    # SIP in
        {'date': '2026-08-01', 'amount': -2000},   # SWP out
    ])
    flows = dict((d, a) for d, a in _sm_config_flows(c, TODAY, 110000.0))
    assert flows[date(2026, 7, 15)] == -5000.0
    assert flows[date(2026, 8, 1)] == 2000.0


def test_rebalances_are_not_cash_flows():
    """Rebalances log amount 0: they move money between holdings inside the
    config, never across its boundary. Counting them would invent flows."""
    c = cfg('a', 100000, '2026-07-02', [
        {'date': '2026-07-20', 'amount': 0},
        {'date': '2026-07-25', 'amount': None},
        {'amount': 5000},                          # no date — unusable
    ])
    flows = _sm_config_flows(c, TODAY, 110000.0)
    assert len(flows) == 2                          # opening + closing mark only


# ── the pooling properties ───────────────────────────────────────────────

def test_two_identical_configs_pool_to_the_same_rate_as_one():
    """XIRR is scale-invariant: doubling every flow cannot change the rate. This
    is the sharpest check that pooling is a real solve and not an average."""
    c = cfg('a', 100000, '2025-08-18')
    one = _sm_pooled_returns({'a': c}, {'a': 120000.0}, TODAY)
    two = _sm_pooled_returns({'a': c, 'b': dict(c, id='b')},
                             {'a': 120000.0, 'b': 120000.0}, TODAY)
    assert one['xirr_pct'] == pytest.approx(two['xirr_pct'], abs=0.05)
    assert one['cagr_pct'] == pytest.approx(two['cagr_pct'], abs=0.05)


def test_pooled_rate_lies_between_the_two_configs_it_pools():
    """A blend, not a sum and not either endpoint."""
    slow = cfg('slow', 100000, '2025-08-18')
    fast = cfg('fast', 100000, '2025-08-18')
    r_slow = _sm_pooled_returns({'slow': slow}, {'slow': 105000.0}, TODAY)['xirr_pct']
    r_fast = _sm_pooled_returns({'fast': fast}, {'fast': 190000.0}, TODAY)['xirr_pct']
    both = _sm_pooled_returns({'slow': slow, 'fast': fast},
                              {'slow': 105000.0, 'fast': 190000.0}, TODAY)['xirr_pct']
    assert r_slow < both < r_fast


def test_the_bigger_config_pulls_the_pooled_rate_towards_itself():
    """Money-weighted: a ₹10L config at +50% must dominate a ₹10k one at 0%."""
    big   = cfg('big', 1000000, '2025-08-18')
    small = cfg('small', 10000, '2025-08-18')
    pooled = _sm_pooled_returns({'big': big, 'small': small},
                                {'big': 1500000.0, 'small': 10000.0}, TODAY)
    solo = _sm_pooled_returns({'big': big}, {'big': 1500000.0}, TODAY)
    assert abs(pooled['xirr_pct'] - solo['xirr_pct']) < 2.0


def test_pooled_xirr_matches_a_direct_solve_over_the_same_flows():
    """The endpoint must not be doing anything the raw solver wouldn't."""
    a = cfg('a', 100000, '2025-08-18', [{'date': '2026-01-15', 'amount': 25000}])
    b = cfg('b', 50000, '2025-11-01')
    marks = {'a': 140000.0, 'b': 58000.0}
    pooled = _sm_pooled_returns({'a': a, 'b': b}, marks, TODAY)
    direct = _xirr(_sm_config_flows(a, TODAY, marks['a'])
                   + _sm_config_flows(b, TODAY, marks['b']))
    assert pooled['xirr_pct'] == pytest.approx(round(direct * 100, 2), abs=0.01)


# ── CAGR span ────────────────────────────────────────────────────────────

def test_cagr_spans_from_the_earliest_go_live():
    """The group has only been compounding since its first rupee went in, so a
    newer config is credited with less than its own age."""
    old = cfg('old', 100000, '2024-08-18')
    new = cfg('new', 100000, '2026-08-01')
    marks = {'old': 150000.0, 'new': 101000.0}
    r = _sm_pooled_returns({'old': old, 'new': new}, marks, TODAY)

    # ₹2.00L in, ₹2.51L out = 1.255x. Spread over the 2 years since the OLDER
    # config went live that is ~12%/yr; over the newer config's 17 days it would
    # be an absurd ~460%. The span is what this test is really pinning.
    assert r['cagr_pct'] == pytest.approx(12.04, abs=0.2)

    same_money_short_span = _sm_pooled_returns(
        {'new': new, 'new2': cfg('new2', 100000, '2026-08-01')},
        {'new': 150000.0, 'new2': 101000.0}, TODAY)
    assert same_money_short_span['cagr_pct'] > r['cagr_pct'] * 10


def test_swp_reduces_the_cost_basis():
    """Money taken back out is not still invested."""
    plain = cfg('a', 100000, '2025-08-18')
    with_swp = cfg('b', 100000, '2025-08-18', [{'date': '2026-01-10', 'amount': -40000}])
    r_plain = _sm_pooled_returns({'a': plain}, {'a': 120000.0}, TODAY)
    r_swp   = _sm_pooled_returns({'b': with_swp}, {'b': 80000.0}, TODAY)
    # 60k basis -> 80k reads better than 100k -> 120k, despite the smaller mark.
    assert r_swp['cagr_pct'] > r_plain['cagr_pct']


# ── degenerate input ─────────────────────────────────────────────────────

def test_unknown_config_ids_are_ignored_not_counted():
    r = _sm_pooled_returns({}, {'ghost': 50000.0}, TODAY)
    assert r == {'cagr_pct': None, 'xirr_pct': None, 'xirr_annualised': True, 'configs': 0}


def test_a_days_old_group_falls_back_to_an_unannualised_return():
    """Under a week there is no span to project a yearly rate from; show the
    period return and flag it, rather than a number that explodes."""
    since = (TODAY - timedelta(days=3)).isoformat()
    r = _sm_pooled_returns({'a': cfg('a', 100000, since)}, {'a': 103000.0}, TODAY)
    assert r['xirr_annualised'] is False
    assert r['xirr_pct'] == pytest.approx(3.0, abs=0.01)
    assert r['cagr_pct'] == pytest.approx(3.0, abs=0.01)   # not annualised either


def test_non_numeric_mark_is_skipped_without_raising():
    r = _sm_pooled_returns({'a': cfg('a', 100000, '2025-08-18')}, {'a': 'oops'}, TODAY)
    assert r['configs'] == 0
