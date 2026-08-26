"""Rule tests for the Camarilla-inside-CPR touch scanner.

Built on synthetic OHLC rather than broker data so the gate and the touch
can be moved one at a time. Nothing here constructs CPRFilterService (and
so nothing reaches a broker) — a stub supplies the handful of methods the
scanner actually calls on it.
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from trading_app.filters.cpr_camarilla_scanner import (
    _CAMARILLA_R3,
    filter_cpr_camarilla_touch,
    get_cpr_camarilla_signal,
)


class _StubService:
    """The slice of CPRFilterService the scanner uses."""

    MAX_WORKERS = 2

    class _Kite:
        pass

    def __init__(self, frame, fo_stocks=('ACME',)):
        self.frame = frame
        self.kite = self._Kite()
        self._fo_stocks = list(fo_stocks)

    def get_hist_data(self, symbol, days=300, interval='day', end_date=None, token=None):
        return self.frame

    def get_fo_stocks(self):
        return self._fo_stocks

    # Real implementations are pure date arithmetic; mirror them exactly.
    def get_prev_month_range(self, ref):
        last_prev = ref.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1), last_prev

    def get_prev_year_range(self, ref):
        last_prev = ref.replace(month=1, day=1) - timedelta(days=1)
        return last_prev.replace(month=1, day=1), last_prev


def _frame(rows):
    """rows: {'YYYY-MM-DD' -> (open, high, low, close)}"""
    data = list(rows.values())
    return pd.DataFrame(
        {'open':  [r[0] for r in data],
         'high':  [r[1] for r in data],
         'low':   [r[2] for r in data],
         'close': [r[3] for r in data]},
        index=pd.to_datetime(list(rows.keys())),
    )


def _levels(high, low, close):
    """PP / BC / TC / S3 / R3 from one period's OHLC — the formulas the
    scanner uses, restated here so a change to either side shows up."""
    pp = (high + low + close) / 3
    bc = (high + low) / 2
    tc = (2 * pp) - bc
    rng = high - low
    return pp, min(bc, tc), max(bc, tc), close - rng * _CAMARILLA_R3, close + rng * _CAMARILLA_R3


# The gate only opens when the PERIOD closed away from the middle of its
# range: high in the range puts S3 inside the band, low puts R3 inside.
# Two fixtures, one per side.
_BULL_PERIOD = {'-01': (100.0, 110.0, 90.0, 100.0), '-28': (105.0, 110.0, 90.0, 107.0)}
_BEAR_PERIOD = {'-01': (100.0, 110.0, 90.0, 100.0), '-28': (95.0, 110.0, 90.0, 93.0)}
_BULL = _levels(110.0, 90.0, 107.0)      # pp, bc, tc, s3, r3
_BEAR = _levels(110.0, 90.0, 93.0)


def _month(period, ym='2026-07'):
    return {ym + suffix: ohlc for suffix, ohlc in period.items()}


def _run(candle, period=None, levels=None, mode='daily', date='2026-08-14'):
    rows = _month(period or _BULL_PERIOD)
    rows[date] = candle
    svc = _StubService(_frame(rows))
    return get_cpr_camarilla_signal(svc, 'ACME', datetime.fromisoformat(date), mode)


class TestGate:
    def test_close_high_in_range_puts_s3_inside_the_band(self):
        pp, bc, tc, s3, r3 = _BULL
        assert bc <= s3 <= tc
        assert not (bc <= r3 <= tc)

    def test_close_low_in_range_puts_r3_inside_the_band(self):
        pp, bc, tc, s3, r3 = _BEAR
        assert bc <= r3 <= tc
        assert not (bc <= s3 <= tc)

    def test_mid_range_close_opens_neither_side(self):
        """The gate does most of the filtering: a period that closed in the
        middle of its range can never signal, whatever the candle does."""
        pp, bc, tc, s3, r3 = _levels(110.0, 90.0, 100.0)
        assert not (bc <= s3 <= tc) and not (bc <= r3 <= tc)
        rows = _month({'-01': (100.0, 110.0, 90.0, 100.0), '-28': (100.0, 110.0, 90.0, 100.0)})
        rows['2026-08-14'] = (pp, pp + 0.1, pp - 0.1, pp)   # parked on the pivot
        svc = _StubService(_frame(rows))
        assert get_cpr_camarilla_signal(svc, 'ACME', datetime(2026, 8, 14), 'daily') == []


class TestTouch:
    def test_candle_straddling_the_band_touches_pivot_and_camarilla(self):
        pp, bc, tc, s3, r3 = _BULL
        out = _run((100.0, tc + 1, bc - 1, 100.0))
        assert len(out) == 1 and out[0]['direction'] == 'BUY'
        assert out[0]['camarilla_line'] == 'S3'
        assert out[0]['touched'] == 'Pivot + S3'

    def test_wick_through_the_level_counts_with_no_close_condition(self):
        """The deliberate difference from detect_camarilla_cpr_reversal():
        the candle need not close back beyond the level."""
        pp, bc, tc, s3, r3 = _BULL
        out = _run((s3 - 2, s3 + 0.01, s3 - 2, s3 - 1.5))   # closed BELOW S3
        assert out and out[0]['touched'] == 'S3'

    def test_pivot_touch_alone_qualifies(self):
        pp, bc, tc, s3, r3 = _BULL
        out = _run((pp, pp + 0.05, pp - 0.05, pp))
        assert out and out[0]['touched'] == 'Pivot'

    def test_candle_clear_of_every_level_is_not_a_signal(self):
        pp, bc, tc, s3, r3 = _BULL
        assert _run((tc + 5, tc + 6, tc + 4, tc + 5)) == []

    def test_bearish_side_reports_r3_and_sell(self):
        pp, bc, tc, s3, r3 = _BEAR
        out = _run((r3 + 2, r3 + 2, r3 - 0.01, r3 + 1.5), period=_BEAR_PERIOD)
        assert len(out) == 1
        assert out[0]['direction'] == 'SELL' and out[0]['camarilla_line'] == 'R3'

    def test_missing_candle_on_the_selected_date_is_skipped(self):
        """A holiday has no bar; the scan must not fall back to a neighbouring
        day and report a touch that never happened on the date asked for."""
        svc = _StubService(_frame(_month(_BULL_PERIOD)))
        assert get_cpr_camarilla_signal(svc, 'ACME', datetime(2026, 8, 14), 'daily') == []


class TestWeeklyMode:
    def test_aggregates_the_week_and_uses_yearly_levels(self):
        pp, bc, tc, s3, r3 = _BULL
        rows = {'2025-01-02': (100.0, 110.0, 90.0, 100.0),
                '2025-12-31': (105.0, 110.0, 90.0, 107.0)}
        # Mon..Wed of one week. The pivot touch lives on Tuesday's low alone,
        # so it is only found if all three bars fold into one weekly candle.
        rows['2026-08-10'] = (pp + 3, pp + 4, pp + 3, pp + 3)
        rows['2026-08-11'] = (pp + 3, pp + 3, pp - 0.5, pp + 1)
        rows['2026-08-12'] = (pp + 1, pp + 2, pp + 1, pp + 2)
        svc = _StubService(_frame(rows))
        out = get_cpr_camarilla_signal(svc, 'ACME', datetime(2026, 8, 12), 'weekly')
        assert out, "aggregated weekly candle should straddle the pivot"
        sig = out[0]
        assert sig['level_timeframe'] == 'Yearly'
        assert sig['candle_timeframe'] == 'Weekly'
        assert sig['candle_date'] == '2026-08-10'          # week start, Monday
        assert sig['candle_high'] == pytest.approx(pp + 4, abs=0.01)
        assert sig['candle_low'] == pytest.approx(pp - 0.5, abs=0.01)
        assert sig['current_price'] == pytest.approx(pp + 2, abs=0.01)   # Wed close

    def test_a_day_outside_the_week_does_not_leak_in(self):
        pp, bc, tc, s3, r3 = _BULL
        rows = {'2025-01-02': (100.0, 110.0, 90.0, 100.0),
                '2025-12-31': (105.0, 110.0, 90.0, 107.0)}
        rows['2026-08-07'] = (pp, pp + 1, pp - 1, pp)       # PREVIOUS Friday
        rows['2026-08-10'] = (pp + 3, pp + 4, pp + 3, pp + 3)
        svc = _StubService(_frame(rows))
        out = get_cpr_camarilla_signal(svc, 'ACME', datetime(2026, 8, 10), 'weekly')
        assert out == [], "Friday's pivot touch belongs to the prior week"


class TestUniverse:
    def test_includes_indices_which_get_fo_stocks_excludes(self):
        pp, bc, tc, s3, r3 = _BULL
        rows = _month(_BULL_PERIOD)
        rows['2026-08-14'] = (100.0, tc + 1, bc - 1, 100.0)
        svc = _StubService(_frame(rows), fo_stocks=('ACME',))
        res = filter_cpr_camarilla_touch(svc, datetime(2026, 8, 14), 'daily')
        symbols = {s['symbol'] for s in res['buy']}
        assert 'ACME' in symbols
        assert {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'} <= symbols
        assert all(s['is_index'] for s in res['buy'] if s['symbol'] != 'ACME')
        assert res['buy'][0]['is_index'] is True, "indices sort ahead of stocks"

    def test_weekend_root_date_rolls_back_to_friday(self):
        pp, bc, tc, s3, r3 = _BULL
        rows = _month(_BULL_PERIOD)
        rows['2026-08-14'] = (100.0, tc + 1, bc - 1, 100.0)   # Friday
        svc = _StubService(_frame(rows))
        res = filter_cpr_camarilla_touch(svc, datetime(2026, 8, 16), 'daily')   # Sunday
        assert res['buy'] and res['buy'][0]['candle_date'] == '2026-08-14'

    def test_completed_week_is_not_flagged_partial(self):
        pp, bc, tc, s3, r3 = _BULL
        rows = {'2025-01-02': (100.0, 110.0, 90.0, 100.0),
                '2025-12-31': (105.0, 110.0, 90.0, 107.0)}
        for day in ('10', '11', '12', '13', '14'):        # Mon..Fri, all in
            rows[f'2026-08-{day}'] = (pp, pp + 1, pp - 1, pp)
        svc = _StubService(_frame(rows))
        out = get_cpr_camarilla_signal(svc, 'ACME', datetime(2026, 8, 14), 'weekly')
        assert out and out[0]['partial_candle'] is False

    def test_week_still_running_is_flagged_partial(self):
        """Mid-week today: the aggregate is three days of a five-day candle,
        and the row has to say so rather than read as a settled signal."""
        pp, bc, tc, s3, r3 = _BULL
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        prev_year = monday.year - 1
        rows = {f'{prev_year}-01-02': (100.0, 110.0, 90.0, 100.0),
                f'{prev_year}-12-31': (105.0, 110.0, 90.0, 107.0)}
        # Monday..Wednesday only, whatever weekday it actually is now.
        for offset in range(0, 3):
            rows[(monday + timedelta(days=offset)).strftime('%Y-%m-%d')] = (pp, pp + 1, pp - 1, pp)
        svc = _StubService(_frame(rows))
        out = get_cpr_camarilla_signal(svc, 'ACME', monday + timedelta(days=2), 'weekly')
        assert out and out[0]['partial_candle'] is True
