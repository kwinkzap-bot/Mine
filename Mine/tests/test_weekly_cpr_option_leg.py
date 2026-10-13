"""Option leg of the Weekly CPR Breakout backtest — a fake Breeze, no network."""
from datetime import date

import pandas as pd

from trading_app.Backtest.weekly_cpr_option_leg import (
    OptionLegPricer, current_strike_steps, trading_days_of)

DAYS = [d.date() for d in pd.bdate_range('2026-10-01', '2026-11-30')]


class _FakeIcici:
    """Serves minute bars for the contracts in `book`: {(root, expiry, strike,
    right, day): {'HH:MM': (open, close)}}; anything else has no candles."""

    def __init__(self, book):
        self.book = book
        self.calls = []

    def historical_option_minutes(self, root, expiry, strike, right, day):
        self.calls.append((root, expiry, strike, right, day))
        bars = self.book.get((root, expiry, strike, right, day))
        if not bars:
            return []
        return [{'date': pd.Timestamp(f'{day} {hm}'), 'open': o, 'high': max(o, c),
                 'low': min(o, c), 'close': c} for hm, (o, c) in bars.items()]


def _trade(direction='BUY', entry=4553.7, entry_time='2026-10-14T11:00:00',
           exit_time='2026-10-14T13:05:00', reason='Target hit'):
    return {'direction': direction, 'entry_price': entry, 'entry_time': entry_time,
            'exit_time': exit_time, 'exit_reason': reason}


OCT_EXP = date(2026, 10, 27)          # last Tuesday of October 2026
NOV_EXP = date(2026, 11, 24)


def test_steps_from_the_nearest_expiry():
    rows = [{'name': 'TITAN', 'instrument_type': 'CE', 'strike': k, 'expiry': date(2026, 10, 27)}
            for k in (4400, 4450, 4500, 4550, 4600, 4700)]
    rows += [{'name': 'TITAN', 'instrument_type': 'CE', 'strike': k, 'expiry': date(2026, 11, 24)}
             for k in (4400, 4500, 4600)]
    assert current_strike_steps(rows) == {'TITAN': 50.0}


def test_buy_prices_the_atm_call_at_the_stock_minutes():
    icici = _FakeIcici({('TITAN', OCT_EXP, 4550, 'CE', date(2026, 10, 14)):
                        {'11:00': (60.0, 61.5), '13:05': (90.0, 92.0)}})
    leg = OptionLegPricer(icici, {'TITAN': 50}).price('TITAN', _trade(), DAYS)
    assert leg['option'] == 'TITAN 27-Oct-26 4550 CE'
    assert (leg['entry_premium'], leg['exit_premium']) == (61.5, 92.0)   # closes


def test_sell_buys_the_put_and_time_exit_reads_the_open():
    icici = _FakeIcici({('TITAN', OCT_EXP, 4550, 'PE', date(2026, 10, 14)):
                        {'11:00': (70.0, 71.0), '15:20': (80.0, 85.0)}})
    leg = OptionLegPricer(icici, {'TITAN': 50}).price(
        'TITAN', _trade('SELL', exit_time='2026-10-14T15:20:00', reason='Time exit'), DAYS)
    assert (leg['option_type'], leg['entry_premium'], leg['exit_premium']) == ('PE', 71.0, 80.0)


def test_missing_minute_uses_the_last_print_before_it():
    icici = _FakeIcici({('TITAN', OCT_EXP, 4550, 'CE', date(2026, 10, 14)):
                        {'10:50': (58.0, 59.0), '12:40': (88.0, 89.0)}})
    leg = OptionLegPricer(icici, {'TITAN': 50}).price('TITAN', _trade(), DAYS)
    assert (leg['entry_premium'], leg['exit_premium']) == (59.0, 89.0)


def test_expiry_day_rolls_to_next_month():
    day = date(2026, 10, 27)
    icici = _FakeIcici({('TITAN', NOV_EXP, 4550, 'CE', day): {'11:00': (100.0, 101.0),
                                                             '13:05': (120.0, 121.0)}})
    leg = OptionLegPricer(icici, {'TITAN': 50}).price(
        'TITAN', _trade(entry_time='2026-10-27T11:00:00', exit_time='2026-10-27T13:05:00'), DAYS)
    assert leg['expiry'] == '2026-11-24'


def test_strike_retried_on_half_the_step_then_remembered():
    day = date(2026, 10, 14)
    # Step 50 rounds 4570 to 4550, which has no candles; half the step finds 4575.
    icici = _FakeIcici({('TITAN', OCT_EXP, 4575, 'CE', day): {'11:00': (50.0, 51.0),
                                                             '13:05': (60.0, 61.0)}})
    pricer = OptionLegPricer(icici, {'TITAN': 50})
    leg = pricer.price('TITAN', _trade(entry=4570.0), DAYS)       # 50 -> 4550 (none), 25 -> 4575
    assert leg['strike'] == 4575
    icici.calls.clear()
    pricer.price('TITAN', _trade(entry=4570.0), DAYS)
    assert icici.calls[0][2] == 4575                              # straight to the step that worked


def test_no_candles_is_unpriced():
    assert OptionLegPricer(_FakeIcici({}), {'TITAN': 50}).price('TITAN', _trade(), DAYS) is None
    assert OptionLegPricer(_FakeIcici({}), {}).price('TITAN', _trade(), DAYS) is None


def test_trading_days_of():
    idx = pd.date_range('2026-10-14 09:15', periods=3, freq='1D')
    assert trading_days_of(pd.DataFrame({'close': [1, 2, 3]}, index=idx)) == \
        [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]


def test_each_strike_is_asked_once_and_a_month_without_contracts_is_remembered():
    icici = _FakeIcici({})
    pricer = OptionLegPricer(icici, {'CGPOWER': 10})
    t = _trade(entry=659.8)
    assert pricer.price('CGPOWER', t, DAYS) is None
    strikes = [c[2] for c in icici.calls]
    assert len(strikes) == len(set(strikes))          # 10, 5 and 20 all round to 660
    icici.calls.clear()
    assert pricer.price('CGPOWER', t, DAYS) is None
    assert icici.calls == []                          # no contracts that month: not re-asked


def test_two_and_a_half_times_the_step_is_tried():
    day = date(2026, 10, 14)
    icici = _FakeIcici({('ASHOKLEY', OCT_EXP, 105, 'CE', day): {'11:00': (3.0, 3.1),
                                                               '13:05': (4.0, 4.2)}})
    leg = OptionLegPricer(icici, {'ASHOKLEY': 1}).price('ASHOKLEY', _trade(entry=105.66), DAYS)
    assert leg['strike'] == 105                       # 2.5 step, not today's 1


def test_nifty_is_priced_on_its_weekly_expiry():
    day = date(2026, 10, 14)                     # Wednesday; next Tuesday is 20 Oct
    weekly = date(2026, 10, 20)
    icici = _FakeIcici({('NIFTY', weekly, 25300, 'CE', day): {'11:00': (100.0, 101.0),
                                                             '13:05': (130.0, 131.0)}})
    leg = OptionLegPricer(icici, {'NIFTY': 50}).price('NIFTY', _trade(entry=25310.0), DAYS)
    assert leg['expiry'] == '2026-10-20' and leg['strike'] == 25300
