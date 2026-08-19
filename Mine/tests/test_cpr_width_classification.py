"""OI Profile CPR card — Narrow / Medium / Wide must actually vary.

The card read "Narrow" every day, for both Index and Future. Not a data problem:
the thresholds were arithmetically unreachable.

CPR width = |TC - BC| / close * 100, and TC - BC reduces to (2C - H - L)/3, so
|TC - BC| <= (H - L)/3 — the maximum reached only when the session closes exactly
on its high or low. Width is therefore capped at (H - L)/(3C)*100. NIFTY ranges
~0.5-1.2% on a normal day, capping its CPR width near 0.17-0.40%, while the old
Narrow cut-off was 0.5%. Nothing else was reachable.

Classification is now relative to the instrument's own recent CPR widths, which
self-scales across an index, a futures contract and a 3%-a-day midcap.
"""
import pytest

from trading_app.app.routes.api import (
    _CPR_WIDTH_HISTORY_DAYS, _CPR_WIDTH_MIN_HISTORY,
    _classify_cpr_width, _cpr_width_pct)


# ── the arithmetic that made the old thresholds impossible ───────────────

@pytest.mark.parametrize('rng_pct', [0.5, 0.8, 1.0, 1.2])
def test_index_cpr_width_cannot_reach_the_old_narrow_cutoff(rng_pct):
    """Even closing exactly on the high — the widest CPR a session can produce —
    a normal NIFTY day stays under the 0.5% that used to mean 'not Narrow'."""
    close = 24000.0
    high  = close
    low   = close * (1 - rng_pct / 100)
    widest = _cpr_width_pct(high, low, close)      # close == high
    assert widest == pytest.approx((high - low) / 3 / close * 100, rel=1e-9)
    assert widest < 0.5, 'old absolute threshold was unreachable for an index'


def test_width_is_zero_when_the_close_sits_mid_range():
    """(2C - H - L) = 0 exactly, so the CPR collapses to a line."""
    assert _cpr_width_pct(24200.0, 24000.0, 24100.0) == pytest.approx(0.0, abs=1e-12)


def test_width_grows_as_the_close_moves_away_from_mid_range():
    mid  = _cpr_width_pct(24200.0, 24000.0, 24100.0)
    part = _cpr_width_pct(24200.0, 24000.0, 24160.0)
    edge = _cpr_width_pct(24200.0, 24000.0, 24200.0)
    assert mid < part < edge


# ── the relative classification ──────────────────────────────────────────

def test_all_three_labels_are_reachable_at_index_scale():
    """The actual bug: on NIFTY-sized numbers the card must be able to say
    something other than Narrow."""
    avg = 0.20                                  # a typical NIFTY CPR width
    assert _classify_cpr_width(0.10, avg) == 'Narrow'   # 0.50x its own normal
    assert _classify_cpr_width(0.20, avg) == 'Medium'   # 1.00x
    assert _classify_cpr_width(0.40, avg) == 'Wide'     # 2.00x


def test_the_same_width_reads_differently_against_a_different_instrument():
    """A 0.30% CPR is wide for an index and narrow for a volatile midcap. This
    is the whole point of comparing against the instrument's own average."""
    assert _classify_cpr_width(0.30, 0.18) == 'Wide'
    assert _classify_cpr_width(0.30, 0.90) == 'Narrow'


def test_boundaries_land_on_the_configured_ratios():
    avg = 0.25
    assert _classify_cpr_width(avg * 0.79, avg) == 'Narrow'
    assert _classify_cpr_width(avg * 0.81, avg) == 'Medium'
    assert _classify_cpr_width(avg * 1.19, avg) == 'Medium'
    assert _classify_cpr_width(avg * 1.21, avg) == 'Wide'


def test_scale_invariance():
    """Doubling every width cannot change the verdict — it is a ratio."""
    for w, a in [(0.1, 0.2), (0.25, 0.25), (0.6, 0.3)]:
        assert _classify_cpr_width(w, a) == _classify_cpr_width(w * 100, a * 100)


# ── the fallback ─────────────────────────────────────────────────────────

def test_absolute_fallback_when_there_is_no_average():
    """A new contract with too little history still gets a sane label, on a
    scale sized to what the metric can actually reach."""
    assert _classify_cpr_width(0.05, None) == 'Narrow'
    assert _classify_cpr_width(0.20, None) == 'Medium'
    assert _classify_cpr_width(0.50, None) == 'Wide'


@pytest.mark.parametrize('avg', [0, 0.0, None])
def test_unusable_average_falls_back_rather_than_dividing_by_zero(avg):
    assert _classify_cpr_width(0.20, avg) == 'Medium'


# ── the band builder end to end ──────────────────────────────────────────

def _bar(d, high, low, close):
    from datetime import datetime as _dt
    return {'date': _dt(2026, 8, d), 'high': high, 'low': low, 'close': close}


def _band(bars, monkeypatch):
    """Drive the real _cpr_band_from_daily against a stub provider."""
    from datetime import date
    import trading_app.app.routes.api as api

    class _Svc:
        def _historical_with_retry(self, **kw):
            return list(bars)

    class _FakeDT(api.datetime):
        @classmethod
        def now(cls):
            return api.datetime(2026, 8, 19, 9, 30)

    monkeypatch.setattr(api, 'datetime', _FakeDT)
    return api._cpr_band_from_daily(_Svc(), token=256265)


def test_band_flags_a_wider_than_usual_cpr(monkeypatch):
    """Yesterday closed on its high (widest possible CPR); the ten sessions
    before it closed mid-range (near-zero CPR). That must not read Narrow."""
    bars = [_bar(18, 24200, 24000, 24200)]                       # prev session
    bars += [_bar(d, 24200, 24000, 24102) for d in range(7, 17)]  # quiet context
    b = _band(bars, monkeypatch)
    assert b['history_days'] == 10
    assert b['width_ratio'] > 1.2
    assert b['type'] == 'Wide'


def test_band_flags_a_narrower_than_usual_cpr(monkeypatch):
    bars = [_bar(18, 24200, 24000, 24100)]                        # CPR ~ 0
    bars += [_bar(d, 24200, 24000, 24200) for d in range(7, 17)]  # wide context
    b = _band(bars, monkeypatch)
    assert b['type'] == 'Narrow'
    assert b['width_ratio'] < 0.8


def test_band_excludes_today_and_reports_its_context_depth(monkeypatch):
    """Today's still-forming bar must never enter the calculation."""
    today_bar = _bar(19, 99999, 1, 99999)          # absurd, to be conspicuous
    bars = [today_bar, _bar(18, 24200, 24000, 24150)]
    bars += [_bar(d, 24200, 24000, 24150) for d in range(7, 17)]
    b = _band(bars, monkeypatch)
    assert b['pp'] < 25000, "today's forming bar leaked into the CPR"
    assert b['history_days'] == 10


def test_band_falls_back_when_history_is_too_thin(monkeypatch):
    bars = [_bar(18, 24200, 24000, 24150), _bar(17, 24200, 24000, 24150)]
    b = _band(bars, monkeypatch)
    assert b['history_days'] < _CPR_WIDTH_MIN_HISTORY
    assert b['avg_width_pct'] is None and b['width_ratio'] is None
    assert b['type'] in ('Narrow', 'Medium', 'Wide')


def test_history_is_capped_at_the_configured_window(monkeypatch):
    bars = [_bar(18, 24200, 24000, 24150)]
    bars += [_bar(d, 24200, 24000, 24150) for d in range(1, 18)]
    b = _band(bars, monkeypatch)
    assert b['history_days'] == _CPR_WIDTH_HISTORY_DAYS
