"""The 30-Sec Option Breakout rule, pinned on hand-built premium candles.

No network and no broker: the engine is handed the leg candles the route
fetches, which is exactly the seam that lets the rule be tested at all — the
premiums it trades come from Breeze and only Breeze, so anything that reached
for real ones would be a broker test, not a rule test.
"""
from datetime import date, datetime, timedelta

import pytest

from trading_app.Backtest.option_breakout_engine import OptionBreakoutEngine

DAY = date(2026, 9, 1)


def bars(seq, day=DAY, start=(9, 15)):
    """30-second candles from the session open: seq is [(o, h, l, c), ...]."""
    stamp = datetime(day.year, day.month, day.day, *start)
    out = []
    for o, h, l, c in seq:
        out.append({'date': stamp, 'open': o, 'high': h, 'low': l, 'close': c})
        stamp += timedelta(seconds=30)
    return out


def leg(candles, option_type='CE', strike=23900, day=DAY):
    return {'day': day, 'option_type': option_type, 'strike': strike,
            'expiry': date(2026, 9, 1), 'spot_open': 23885.0, 'candles': candles}


def run(legs, **kw):
    kw.setdefault('rr_ratio', 2.0)
    return OptionBreakoutEngine(legs, **kw).run()


# ── The rule itself ───────────────────────────────────────────────────────────

def test_entry_is_the_range_candles_high_and_the_stop_is_its_low_minus_the_buffer():
    """The worked example: 2nd candle 100–110, so buy 110 with the stop at 99."""
    candles = bars([(100, 105, 95, 102),      # 1st — not the range
                    (102, 110, 100, 108),     # 2nd — the range candle
                    (108, 112, 107, 111)])    # breaks 110
    trades, _ = run([leg(candles)], sl_buffer=1.0)

    assert len(trades) == 1
    t = trades[0]
    assert t['entry_price'] == 110.0
    assert t['sl_price'] == 99.0               # range low 100 − ₹1
    assert t['range_high'] == 110.0 and t['range_low'] == 100.0
    # Target = entry + rr × risk, risk = 110 − 99 = 11
    assert t['target_price'] == pytest.approx(132.0)


def test_the_buffer_is_configurable_and_zero_puts_the_stop_on_the_low():
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)])
    assert run([leg(candles)], sl_buffer=0.0)[0][0]['sl_price'] == 100.0
    assert run([leg(candles)], sl_buffer=2.5)[0][0]['sl_price'] == 97.5


@pytest.mark.parametrize('rr, target', [(1.0, 121.0), (2.0, 132.0), (3.0, 143.0)])
def test_the_three_offered_targets_are_multiples_of_the_risk(rr, target):
    """1:1, 1:2 and 1:3 off an 11-point stop."""
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108), (108, 200, 107, 199)])
    trades, _ = run([leg(candles)], rr_ratio=rr, sl_buffer=1.0)
    assert trades[0]['target_price'] == pytest.approx(target)
    assert trades[0]['exit_price'] == pytest.approx(target)
    assert trades[0]['exit_reason'] == 'TG Hit'


def test_both_legs_of_the_day_are_taken_independently():
    """The pair is the point: one strike, two contracts, two separate trades."""
    ce = bars([(100, 105, 95, 102), (102, 110, 100, 108),
               (108, 140, 107, 139)])                      # target
    pe = bars([(85, 88, 82, 86), (86, 90, 80, 88),
               (88, 95, 87, 94), (94, 94, 70, 72)])        # stop
    trades, summary = run([leg(ce, 'CE'), leg(pe, 'PE')], sl_buffer=1.0)

    assert [t['option_type'] for t in trades] == ['CE', 'PE']
    assert summary['both_legs_days'] == 1
    assert summary['wins'] == 1 and summary['losses'] == 1
    # A bought PE is still a long position — the leg column, not the type, is
    # what says which way the trade was leaning.
    assert {t['type'] for t in trades} == {'Long'}


def test_a_leg_that_never_breaks_its_range_high_does_not_trade():
    quiet = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                  (108, 109, 90, 95), (95, 99, 88, 90)])
    trades, summary = run([leg(quiet)])
    assert trades == []
    assert summary['legs_with_data'] == 1     # it had data; it had no signal


def test_only_the_first_breakout_of_a_session_is_taken():
    """A leg trades at most once a day, even after it is stopped out."""
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                    (108, 112, 107, 111),      # entry at 110
                    (111, 111, 90, 95),        # stopped at 99
                    (95, 130, 94, 129)])       # breaks 110 again — ignored
    trades, _ = run([leg(candles)], sl_buffer=1.0)
    assert len(trades) == 1 and trades[0]['exit_reason'] == 'SL Hit'


# ── Fills and exits ───────────────────────────────────────────────────────────

def test_a_bar_that_gaps_past_the_trigger_fills_at_its_open():
    """110 was never available once the bar opened at 118 — filling there would
    invent a price and hand the backtest 8 free points."""
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                    (118, 125, 117, 124)])
    t = run([leg(candles)], sl_buffer=1.0)[0][0]
    assert t['entry_price'] == 118.0
    assert t['target_price'] == pytest.approx(118.0 + 2.0 * (118.0 - 99.0))


def test_the_stop_is_checked_before_the_target_within_one_bar():
    """A bar spanning both is booked as the loss — the conservative read, and
    the same order second_candle_engine takes."""
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                    (108, 200, 80, 150)])
    t = run([leg(candles)], sl_buffer=1.0)[0][0]
    assert t['exit_reason'] == 'SL Hit' and t['pnl'] == pytest.approx(-11.0)


def test_the_cut_off_squares_off_at_the_bar_open_and_blocks_new_entries():
    entry_day = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                      (108, 112, 107, 111)])                       # 09:16:00 entry
    entry_day += bars([(120, 121, 119, 120)], start=(15, 25))       # cut-off bar
    t = run([leg(entry_day)], sl_buffer=1.0, exit_hour=15, exit_minute=25)[0][0]
    assert t['exit_reason'] == 'Time Exit' and t['exit_price'] == 120.0

    late = bars([(100, 105, 95, 102), (102, 110, 100, 108)])
    late += bars([(108, 130, 107, 129)], start=(15, 25))            # breakout, too late
    assert run([leg(late)])[0] == []


def test_an_unfinished_trade_closes_on_the_last_bar_of_the_day():
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                    (108, 112, 107, 111), (111, 115, 105, 113)])
    t = run([leg(candles)], sl_buffer=1.0)[0][0]
    assert t['exit_reason'] == 'EOD Exit' and t['exit_price'] == 113.0
    assert t['pnl'] == pytest.approx(3.0)


# ── Degenerate input ──────────────────────────────────────────────────────────

def test_a_flat_range_candle_is_skipped():
    """A strike nobody touched at 09:15 prints O=H=L=C; trading it would be an
    entry on any tick against a zero-width stop."""
    candles = bars([(2.0, 2.0, 2.0, 2.0), (2.0, 2.0, 2.0, 2.0), (2.0, 3.0, 2.0, 3.0)])
    assert run([leg(candles)])[0] == []


def test_a_leg_with_no_candles_is_counted_but_never_traded():
    trades, summary = run([leg([], 'CE'), leg(None, 'PE')])
    assert trades == []
    assert summary['legs_scanned'] == 2 and summary['legs_with_data'] == 0


def test_a_session_shorter_than_the_range_candle_is_skipped():
    assert run([leg(bars([(100, 105, 95, 102), (102, 110, 100, 108)]))])[0] == []


def test_a_duplicated_opening_bar_does_not_become_the_range_candle():
    """Breeze repeats a window's edge bar across two requests; kept, the
    duplicate would shift the 2nd candle onto the 1st one's range."""
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)])
    candles.insert(1, dict(candles[0]))          # same stamp, same bar
    t = run([leg(candles)], sl_buffer=1.0)[0][0]
    assert t['entry_price'] == 110.0             # the real 2nd candle's high


def test_the_range_candle_is_selectable():
    candles = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                    (108, 120, 106, 118), (118, 125, 117, 124)])
    t = run([leg(candles)], candle_index=3, sl_buffer=1.0)[0][0]
    assert t['entry_price'] == 120.0 and t['sl_price'] == 105.0


# ── The sweep ─────────────────────────────────────────────────────────────────

from trading_app.Backtest.option_breakout_engine import (   # noqa: E402
    GRID_SIZE, optimise_option_breakout, prepare_legs,
)


def _sweep_legs(days=6):
    """One CE that reaches a 1:3 target and one PE that stops out, per day."""
    legs = []
    for n in range(days):
        d = date(2026, 9, 1) + timedelta(days=n)
        ce = bars([(100, 105, 95, 102), (102, 110, 100, 108),
                   (108, 200, 107, 199)], day=d)
        pe = bars([(85, 88, 82, 86), (86, 90, 80, 88),
                   (88, 95, 87, 94), (94, 94, 70, 72)], day=d)
        legs += [leg(ce, 'CE', day=d), leg(pe, 'PE', day=d)]
    return legs


def test_the_sweep_covers_every_combination_of_the_four_free_params():
    got = optimise_option_breakout(_sweep_legs(), min_trades=1)
    combos = {(r['candle_index'], r['rr_ratio'], r['sl_buffer'], r['legs']) for r in got}
    assert len(combos) == len(got)            # no combination reported twice
    assert len(got) <= GRID_SIZE
    assert {r['legs'] for r in got} == {'CE & PE', 'CE only', 'PE only'}
    assert {r['sl_buffer'] for r in got} == {0.0, 1.0, 2.0}


def test_the_sweep_ranks_best_first_and_a_thin_combo_is_left_off():
    got = optimise_option_breakout(_sweep_legs(), min_trades=1)
    assert [r['score'] for r in got] == sorted((r['score'] for r in got), reverse=True)
    # Every trade in this fixture is on candle 2; a candle-4 range never fires.
    assert all(r['candle_index'] != 4 for r in got)


def test_the_trade_floor_keeps_a_two_session_sample_off_the_board():
    """Six trades on three sessions, so a floor of eight empties it — the guard
    against ranking a combo that fired twice."""
    assert optimise_option_breakout(_sweep_legs(days=3), min_trades=8) == []


def test_the_leg_rows_are_the_same_simulation_split_three_ways():
    """CE-only + PE-only must reconcile to the pair, not re-simulate it — a leg
    does not trade differently for being watched alone."""
    got = optimise_option_breakout(_sweep_legs(), min_trades=1)
    same = lambda legs: next(r for r in got
                             if r['legs'] == legs and r['candle_index'] == 2
                             and r['rr_ratio'] == 2.0 and r['sl_buffer'] == 1.0)
    both, ce, pe = same('CE & PE'), same('CE only'), same('PE only')
    assert both['total_trades'] == ce['total_trades'] + pe['total_trades']
    assert both['total_pnl'] == pytest.approx(ce['total_pnl'] + pe['total_pnl'])


def test_the_sweep_survives_legs_with_no_candles():
    assert optimise_option_breakout([leg([], 'CE'), leg(None, 'PE')]) == []
    assert optimise_option_breakout([]) == []


def test_prepared_legs_drop_the_ones_that_came_back_empty():
    prepared = prepare_legs([leg(bars([(1, 2, 1, 2)]), 'CE'), leg([], 'PE')])
    assert len(prepared) == 1 and prepared[0]['meta']['option_type'] == 'CE'


# ── Buying only part of the session ───────────────────────────────────────────
# The optimisation that makes a run fast is also the one that could silently
# change its results, so these assert EQUALITY with a whole-day fetch rather
# than checking the walker in isolation — equality of every price, the reason
# and the P&L. The one licensed difference is `exit_time`: a close read off the
# 1-minute series is stamped with its minute, not its 30-second bar.

from trading_app.Backtest.option_breakout_engine import (   # noqa: E402
    _simulate, walk_one, walk_session, walk_leg, DONE, NO_ENTRY_YET, OPEN,
)


def same_trade(walked, full):
    """The walked trade is the whole-day trade, to the minute."""
    if walked is None or full is None:
        return walked == full
    a, b = dict(walked), dict(full)
    ta, tb = a.pop('exit_time'), b.pop('exit_time')
    return a == b and ta[:16] == tb[:16]

WINDOW_BARS = 30            # 900-second slice ÷ 30-second bar
CUTOFF = 15 * 60 + 25


def session(seq, day=DAY):
    """A full 09:15–15:30 session of 30-second bars: `seq` up front, then flat
    filler at the last close so the day is the real 750 bars long."""
    full = list(seq)
    o = seq[-1][3]
    full += [(o, o, o, o)] * (750 - len(seq))
    return bars(full, day=day)


def minutes_from(day_bars):
    """The 1-minute series the oracle reads, folded from the 30-second one —
    exactly what Breeze would serve for the same session."""
    out = []
    for i in range(0, len(day_bars), 2):
        pair = day_bars[i:i + 2]
        out.append({'date': pair[0]['date'],
                    'open': pair[0]['open'],
                    'high': max(b['high'] for b in pair),
                    'low':  min(b['low'] for b in pair),
                    'close': pair[-1]['close']})
    return out


def windowed(day_bars):
    """(fetch_window, window_count, calls) over a session sliced the way the
    ICICI adapter slices it. `calls` records what was actually paid for."""
    slices = [day_bars[i:i + WINDOW_BARS] for i in range(0, len(day_bars), WINDOW_BARS)]
    calls = []

    def fetch(i):
        calls.append(i)
        return slices[i] if 0 <= i < len(slices) else []
    return fetch, len(slices), calls


def run_both(day_bars, **kw):
    """(whole-day trade, walked trade, slices bought)."""
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    full = _simulate(prepare_legs([{**meta, 'candles': day_bars}])[0],
                     kw.get('candle_index', 2), kw.get('rr', 2.0),
                     kw.get('sl_buffer', 1.0), CUTOFF)
    fetch, count, calls = windowed(day_bars)
    walked, _fetched = walk_one(
        meta, fetch, count, minutes_from(day_bars),
        kw.get('candle_index', 2), kw.get('rr', 2.0), kw.get('sl_buffer', 1.0), CUTOFF)
    return full, walked, calls


def test_a_trade_decided_in_the_first_slice_buys_only_that_slice():
    day = session([(100, 105, 95, 102), (102, 110, 100, 108),
                   (108, 112, 107, 111), (111, 135, 110, 133)])
    full, walked, calls = run_both(day)
    assert same_trade(walked, full)
    assert full['exit_reason'] == 'TG Hit'
    assert calls == [0]                    # 1 of 25


def test_a_leg_that_never_triggers_stops_after_the_oracle_clears_the_day():
    """The pathological case: without the minute index this one costs the whole
    session, and it is the only shape in the sample that did."""
    quiet = [(100, 105, 95, 102), (102, 110, 100, 108)] + [(99, 101, 98, 100)] * 400
    day = session(quiet)
    full, walked, calls = run_both(day)
    assert walked is None and full is None
    assert len(calls) == 1


def test_a_trade_that_resolves_late_skips_the_quiet_slices_between():
    """Entry at ~09:16, target not touched until ~13:00 — the slices in between
    are provably quiet, so they are never bought."""
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 440            # drifts, hits neither level
    seq += [(111, 140, 110, 139)]                  # target, ~12:56
    day = session(seq)
    full, walked, calls = run_both(day)
    assert same_trade(walked, full)
    assert full['exit_reason'] == 'TG Hit'
    assert len(calls) < 8                          # not the 16 slices it spans


def test_a_position_open_to_the_close_buys_the_last_slice_not_every_slice():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 600            # never resolves
    day = session(seq)
    full, walked, calls = run_both(day)
    assert same_trade(walked, full)
    assert full['exit_reason'] in ('Time Exit', 'EOD Exit')
    assert len(calls) < 10


@pytest.mark.parametrize('rr', [1.0, 2.0, 3.0])
@pytest.mark.parametrize('buf', [0.0, 1.0, 2.0])
@pytest.mark.parametrize('ci', [1, 2, 3])
def test_the_walk_matches_a_whole_day_fetch_across_the_grid(rr, buf, ci):
    """Every combination the optimiser sweeps has to agree with the slow path —
    the skip logic must not depend on which parameters are in play."""
    day = session([(100, 105, 95, 102), (102, 110, 100, 108),
                   (108, 118, 99, 117), (117, 121, 96, 100),
                   (100, 145, 99, 144), (144, 146, 80, 82)])
    full, walked, _ = run_both(day, rr=rr, sl_buffer=buf, candle_index=ci)
    assert same_trade(walked, full)


def test_the_walk_still_stops_early_with_no_minute_oracle():
    """A failed 1-minute fetch must not cost correctness — only speed."""
    day = session([(100, 105, 95, 102), (102, 110, 100, 108), (108, 135, 107, 133)])
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _fetched = walk_one(meta, fetch, count, None, 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert same_trade(walked, full)
    assert calls == [0]


def test_walk_leg_reports_why_a_partial_session_is_undecided():
    quiet = prepare_legs([leg(bars([(100, 105, 95, 102), (102, 110, 100, 108),
                                    (105, 106, 104, 105)]))])[0]
    assert walk_leg(quiet, 2, 2.0, 1.0, CUTOFF, whole_session=False).state == NO_ENTRY_YET
    assert walk_leg(quiet, 2, 2.0, 1.0, CUTOFF, whole_session=True).state == DONE

    open_pos = prepare_legs([leg(bars([(100, 105, 95, 102), (102, 110, 100, 108),
                                       (108, 112, 107, 111)]))])[0]
    w = walk_leg(open_pos, 2, 2.0, 1.0, CUTOFF, whole_session=False)
    assert w.state == OPEN and w.trade is None
    assert w.sl == 99.0 and w.tp == pytest.approx(132.0)


def test_the_sweeps_combos_share_one_walk_and_still_match_a_whole_day_fetch():
    """The optimiser reads 48 parameter sets off the same slices. Each must get
    the trade a whole-day fetch would have given it, and the shared walk must
    cost less than running them one at a time."""
    day = session([(100, 105, 95, 102), (102, 110, 100, 108),
                   (108, 118, 99, 117), (117, 121, 96, 100),
                   (100, 145, 99, 144), (144, 146, 80, 82)])
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    combos = [(ci, rr, buf) for ci in (1, 2, 3) for rr in (1.0, 2.0, 3.0)
              for buf in (0.0, 1.0, 2.0)]

    fetch, count, calls = windowed(day)
    trades, fetched = walk_session(meta, fetch, count, minutes_from(day), combos, CUTOFF)

    prepared = prepare_legs([{**meta, 'candles': day}])[0]
    for ci, rr, buf in combos:
        assert same_trade(trades[(ci, rr, buf)], _simulate(prepared, ci, rr, buf, CUTOFF))

    # One shared walk, not one per combo.
    assert len(set(calls)) == len(fetched)
    assert len(fetched) < count


# ── The oracle is bought only when a slice leaves something undecided ─────────
# The first slice always has to be read; on the days it settles the trade the
# 1-minute series is a request spent on nothing. So the walk takes the oracle
# as a callable and must not call it on those days — and must call it at most
# once on the others.

def oracle_for(day_bars):
    calls = []

    def fetch():
        calls.append(1)
        return minutes_from(day_bars)
    return fetch, calls


def test_a_day_settled_in_the_first_slice_never_buys_the_oracle():
    day = session([(100, 105, 95, 102), (102, 110, 100, 108), (108, 135, 107, 133)])
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    oracle, asked = oracle_for(day)
    walked, _ = walk_one(meta, fetch, count, oracle, 2, 2.0, 1.0, CUTOFF)
    assert walked['exit_reason'] == 'TG Hit'
    assert calls == [0] and asked == []


def test_an_undecided_day_buys_the_oracle_once_and_still_matches():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 400            # open well past the first slice
    seq += [(111, 140, 110, 139)]                  # target, hours later
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    oracle, asked = oracle_for(day)
    walked, _ = walk_one(meta, fetch, count, oracle, 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert same_trade(walked, full) and full['exit_reason'] == 'TG Hit'
    assert asked == [1]                            # once, not once a slice
    assert len(calls) < 8


def test_an_oracle_that_returns_nothing_degrades_to_a_slice_walk():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 40 + [(111, 140, 110, 139)]
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, lambda: None, 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert same_trade(walked, full)


# ── Closing a position off the minute series ──────────────────────────────────
# The exit is where a sweep's requests went: 48 combos close in ~5 different
# slices a contract-day. A minute that touches only one of stop/target fixes
# the close without its slice; one that touches both does not.

def test_an_exit_the_minutes_settle_costs_no_extra_slice():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 400
    seq += [(111, 140, 110, 139)]                  # target alone, hours later
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert same_trade(walked, full) and walked['exit_reason'] == 'TG Hit'
    assert walked['exit_price'] == full['exit_price'] == full['target_price']
    assert calls == [0]                            # the exit slice was never bought


def test_a_minute_touching_both_levels_buys_its_slice_and_lets_the_bars_decide():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 401            # odd: the next pair shares a minute
    seq += [(111, 140, 110, 139), (139, 139, 90, 95)]   # target, then stop, one minute
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert walked == full and walked['exit_reason'] == 'TG Hit'   # exact, bars bought
    assert len(calls) == 2


def test_a_dip_before_the_entry_bar_in_the_same_minute_is_not_a_stop():
    # 09:16:00 dips to 90 (under the stop), 09:16:30 breaks out; the rule
    # enters at 09:16:30 and only then watches the stop. The entry minute's
    # low is 90, so reading that minute off the oracle would call it a stop.
    seq = [(100, 105, 95, 102), (102, 110, 100, 108),
           (105, 106, 90, 104), (104, 112, 103, 111)]
    seq += [(111, 112, 105, 111)] * 400 + [(111, 140, 110, 139)]
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert full['exit_reason'] == 'TG Hit'
    assert same_trade(walked, full)


def test_a_time_exit_is_read_off_the_cutoff_minute_without_its_slice():
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 800            # never resolves
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert full['exit_reason'] == 'Time Exit'
    assert same_trade(walked, full)
    assert calls == [0]


def test_an_unsettled_day_does_not_call_a_quiet_tail_the_close():
    """Today's session: the minute series stops where the day has got to, so a
    position still open must not be booked as a time exit off it."""
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 100
    day = bars(seq)                                # a partial day: 103 bars
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF,
                         settled=False)
    assert walked is None or walked['exit_reason'] not in ('Time Exit',)


def test_a_touch_by_a_single_tick_is_left_to_the_bars():
    """Breeze's minute and second series disagree by a tick now and then, so a
    minute that only just reaches a level buys its slice rather than closing
    the trade on a print the bars may never have seen."""
    seq = [(100, 105, 95, 102), (102, 110, 100, 108), (108, 112, 107, 111)]
    seq += [(111, 112, 105, 111)] * 401
    seq += [(111, 132.0, 110, 131), (131, 131, 130, 130)]   # target 132.0, touched exactly
    day = session(seq)
    meta = {k: v for k, v in leg(None).items() if k != 'candles'}
    fetch, count, calls = windowed(day)
    walked, _ = walk_one(meta, fetch, count, minutes_from(day), 2, 2.0, 1.0, CUTOFF)
    full = _simulate(prepare_legs([{**meta, 'candles': day}])[0], 2, 2.0, 1.0, CUTOFF)
    assert walked == full and full['exit_reason'] == 'TG Hit' and full['target_price'] == 132.0
    assert len(calls) == 2                         # the bars were consulted
