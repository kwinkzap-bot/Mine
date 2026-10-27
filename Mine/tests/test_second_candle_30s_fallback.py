"""2nd 30s Candle reads its 30-second bars from ICICI when the provider refuses them."""
from datetime import date

import pandas as pd

from trading_app.algo.second_candle import second_candle_algo as sc


class _Src:
    def __init__(self, rows, err=None):
        self.rows, self.err, self.calls = rows, err, 0

    def historical_data(self, **kw):
        self.calls += 1
        return list(self.rows)

    def last_history_error(self):
        return self.err


def _rows():
    d = date.today().isoformat()
    return [{'date': f'{d} 09:15:00+05:30', 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5},
            {'date': f'{d} 09:15:30+05:30', 'open': 1.5, 'high': 3, 'low': 1, 'close': 2}]


PRIME = 'Fyers history error (code -50): Invalid input — resolution: seconds resolution is available only on the Prime plan'


def _algo(monkeypatch, icici):
    from trading_app.service import provider_logic
    monkeypatch.setattr(provider_logic, 'get_icici_adapter', lambda user=None: icici)
    return sc.SecondCandleAlgo('Mine')


def test_plan_refusal_falls_back_to_icici_and_stops_asking_fyers(monkeypatch):
    fyers, icici = _Src([], PRIME), _Src(_rows())
    algo = _algo(monkeypatch, icici)
    df = algo._fetch_today_30s_candles(fyers)
    assert df is not None and len(df) == 2 and icici.calls == 1
    algo._fetch_today_30s_candles(fyers)
    assert fyers.calls == 1 and icici.calls == 2          # Fyers not asked again today


def test_a_working_provider_is_used_and_icici_left_alone(monkeypatch):
    fyers, icici = _Src(_rows()), _Src(_rows())
    algo = _algo(monkeypatch, icici)
    assert len(algo._fetch_today_30s_candles(fyers)) == 2
    assert icici.calls == 0


def test_a_transient_empty_answer_does_not_latch(monkeypatch):
    fyers, icici = _Src([], 'Fyers rate limit — 5 attempts exhausted'), _Src(_rows())
    algo = _algo(monkeypatch, icici)
    algo._fetch_today_30s_candles(fyers)
    algo._fetch_today_30s_candles(fyers)
    assert fyers.calls == 2                                 # still tried first next time


def test_range_fetch_backs_off_after_an_empty_answer(monkeypatch):
    fyers, icici = _Src([], PRIME), _Src([])
    algo = _algo(monkeypatch, icici)
    now = pd.Timestamp.now(tz='Asia/Kolkata').normalize() + pd.Timedelta(hours=9, minutes=17)
    params = sc.normalise_params(None)
    assert algo._ensure_range(fyers, params, now) is False
    assert algo._ensure_range(fyers, params, now) is False
    assert icici.calls == 1                                 # second call inside the 5 s back-off
