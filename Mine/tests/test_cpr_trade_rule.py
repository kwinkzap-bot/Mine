"""cpr_trade_rule.propose — the 09:15 break with a long wick, confirmed by 09:20.

7 Sept 2026 is the worked example: Medium CPR, medium box, the 09:15
candle opened between PDL and S1 and closed under S1 with a 40-pt lower
wick; the 09:20 candle was a small red one inside that wick, so the entry
is under it, SL over the 09:15 high, target 1:2.
"""

import math

import pytest

from trading_app.service import cpr_backtest_service as svc
from trading_app.service.cpr_service import cpr_width_pct
from trading_app.service.cpr_trade_rule import propose

# 4 Sept 2026 (previous session) and the week of 31 Aug - 4 Sept (Fyers daily bars).
PREV = (24005.75, 23895.85, 23897.7)
WEEK = (24143.15, 23786.8, 23897.7)

C1 = {'time': '09:15', 'open': 23883.15, 'high': 23890.0, 'low': 23818.5, 'close': 23858.8}
C2 = {'time': '09:20', 'open': 23859.35, 'high': 23861.3, 'low': 23838.25, 'close': 23838.25}


def _chart(c1=C1, prev=PREV, week=WEEK):
    lv, w = svc.levels(*prev), svc.levels(*week)
    width = cpr_width_pct(*prev)
    return {
        'levels': lv, 'weekly': {k: w[k] for k in ('pp', 'bc', 'tc')},
        'width_pct': width, 'box_pts': round(abs(lv['r1'] - lv['pdh']), 1),
        'first_candle': c1, 'day': {'open': c1['open']},
    }


def _session(*bars):
    rest = [{'time': f'{h:02d}:{m:02d}', 'open': 23830.0, 'high': 23835.0, 'low': 23825.0, 'close': 23830.0}
            for h in range(10, 16) for m in range(0, 60, 5) if (h, m) < (15, 30)]
    return list(bars) + rest


def test_7_sept_2026_sells_under_the_0920_candle():
    chart = _chart()
    lv = chart['levels']
    assert lv['s1'] < C1['open'] < lv['pdl'] and C1['close'] < lv['s1']     # the described open and close
    assert chart['width_pct'] >= 0.13                                          # Medium, not the narrow-CPR case
    trade, why, i = propose(chart, _session(C1, C2))
    assert trade == {'trade': 'SELL', 'entry': 23837.0, 'sl': 23892.0, 'target': 23727.0}
    assert i == 1
    assert 'waited for 09:20' in why and 'SL over the 09:15 high' in why and 'target 1:2' in why
    assert trade['entry'] - trade['target'] == 2 * (trade['sl'] - trade['entry'])


def test_a_0920_candle_that_does_not_confirm_is_not_an_entry():
    chart = _chart()
    green = dict(C2, open=23840.0, close=23858.0)                              # green: the wick is being bought again
    assert propose(chart, _session(C1, green))[0] is None
    below = dict(C2, low=C1['low'] - 30, close=C1['low'] - 25)                # broke under the 09:15 low: not inside the wick
    assert propose(chart, _session(C1, below))[0] is None
    big = dict(C2, high=C1['close'], low=C1['close'] - 50, close=C1['close'] - 48)   # more than half the 09:15 range
    assert propose(chart, _session(C1, big))[0] is None


def test_a_short_wick_or_a_strong_body_is_not_this_case():
    # Same open/close, but the low just under the close: no wick to wait out.
    plain = dict(C1, low=23855.0)
    trade, _, i = propose(_chart(plain), _session(plain, C2))
    assert not (trade and i == 1 and trade['entry'] == 23837.0)
    # A strong body with a long tail cannot exist; a strong one without the
    # tail belongs to the narrow-CPR rule, which a Medium CPR does not enter.
    strong = dict(C1, open=23888.0, low=23850.0)
    trade, _, i = propose(_chart(strong), _session(strong, C2))
    assert not (trade and i == 1)


def test_the_mirror_buys_over_the_0920_candle():
    # Previous session 23,700-23,900 close 23,880: PDH 23,900, R1 above it.
    prev = (23900.0, 23700.0, 23880.0)
    lv = svc.levels(*prev)
    hi = (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1']))
    c1 = {'time': '09:15', 'open': hi[1] - 5, 'high': hi[1] + 120, 'low': hi[1] - 10, 'close': hi[1] + 30}
    assert (c1['high'] - c1['close']) / (c1['high'] - c1['low']) >= 0.5           # long upper wick
    c2 = {'time': '09:20', 'open': c1['close'], 'high': c1['close'] + 15, 'low': c1['close'] - 2, 'close': c1['close'] + 12}
    trade, why, i = propose(_chart(c1, prev, (23950.0, 23600.0, 23880.0)), _session(c1, c2))
    assert i == 1 and trade['trade'] == 'BUY'
    assert trade['entry'] == math.ceil(c2['high'] + 1) and trade['sl'] == math.floor(c1['low'] - 1.5)
    assert trade['target'] - trade['entry'] == 2 * (trade['entry'] - trade['sl'])
    assert 'inside PDH/R1' in why and 'BUY over it' in why


# ── gap day: a base of small candles, then the first strong candle ────────

# 11 Sept 2026: 10 Sept H/L/C, the same previous week as 7 Sept, and the first nine bars.
PREV_11 = (23494.95, 23380.1, 23477.8)
WEEK_11 = (24143.15, 23786.8, 23897.7)
BARS_11 = [
    {'time': '09:15', 'open': 23270.3, 'high': 23277.3, 'low': 23231.4, 'close': 23258.6},
    {'time': '09:20', 'open': 23259.0, 'high': 23263.95, 'low': 23248.35, 'close': 23258.95},
    {'time': '09:25', 'open': 23258.9, 'high': 23266.0, 'low': 23251.7, 'close': 23258.25},
    {'time': '09:30', 'open': 23258.4, 'high': 23267.15, 'low': 23238.25, 'close': 23256.05},
    {'time': '09:35', 'open': 23257.9, 'high': 23267.25, 'low': 23254.95, 'close': 23259.15},
    {'time': '09:40', 'open': 23259.6, 'high': 23259.6, 'low': 23239.15, 'close': 23248.2},
    {'time': '09:45', 'open': 23249.95, 'high': 23254.1, 'low': 23241.25, 'close': 23246.55},
    {'time': '09:50', 'open': 23247.8, 'high': 23252.3, 'low': 23242.3, 'close': 23246.25},
    {'time': '09:55', 'open': 23247.05, 'high': 23263.35, 'low': 23244.75, 'close': 23259.25},
]


def _rest(px):
    return [{'time': f'{h:02d}:{m:02d}', 'open': px, 'high': px + 5, 'low': px - 5, 'close': px}
            for h in range(10, 16) for m in range(0, 60, 5) if (h, m) < (15, 30)]


def test_11_sept_2026_buys_over_the_first_strong_candle_after_the_base():
    chart = _chart(BARS_11[0], PREV_11, WEEK_11)
    lv = chart['levels']
    assert BARS_11[0]['open'] < min(lv['s1'], lv['pdl']) * (1 - 0.25 / 100)          # a gap-down open
    trade, why, i = propose(chart, BARS_11 + _rest(23259.0))
    # Entry over the 09:15 high (23,277.3), not just the 09:55 high (23,263); target PDL, not 1:1.
    assert trade == {'trade': 'BUY', 'entry': 23279.0, 'sl': 23243.0, 'target': 23380.0}
    assert i == 8 and BARS_11[i]['time'] == '09:55'
    assert 'base of 7 small candles (6 red)' in why and 'and the 09:15 high 23,277' in why and 'target PDL 23,380' in why


def test_a_strong_candle_already_over_the_0915_high_enters_over_its_own_high():
    chart = _chart(BARS_11[0], PREV_11, WEEK_11)
    tall = dict(BARS_11[8], high=23290.0, close=23288.0)
    trade, why, _ = propose(chart, BARS_11[:8] + [tall] + _rest(23288.0))
    assert trade['entry'] == 23291.0 and '09:15 high' not in why


def test_the_base_needs_small_candles_with_a_few_against_the_fill():
    chart = _chart(BARS_11[0], PREV_11, WEEK_11)
    # Only one small candle before the strong one: no base yet.
    assert propose(chart, [BARS_11[0], BARS_11[1], BARS_11[8]] + _rest(23259.0))[0] is None
    # A base of green candles only: nothing against the fill.
    greens = [dict(b, open=b['low'] + 1, close=b['low'] + 4) for b in BARS_11[1:8]]
    assert propose(chart, [BARS_11[0]] + greens + [BARS_11[8]] + _rest(23259.0))[0] is None
    # A strong red candle inside the base ends it: the next strong green is no entry.
    strong_red = dict(BARS_11[4], open=23268.0, high=23268.5, low=23240.0, close=23241.0)
    bars = BARS_11[:4] + [strong_red] + BARS_11[5:] + _rest(23259.0)
    assert propose(chart, bars)[0] is None


def test_the_base_entry_stops_once_the_zone_is_reached():
    # A bar that reaches S1/PDL hands over to the gap-down rule proper.
    chart = _chart(BARS_11[0], PREV_11, WEEK_11)
    into_zone = dict(BARS_11[4], high=chart['levels']['pdl'] + 1)
    bars = BARS_11[:4] + [into_zone] + BARS_11[5:] + _rest(23259.0)
    trade, why, i = propose(chart, bars)
    assert not (trade and trade['trade'] == 'BUY' and i == 8)
    assert 'base of' not in why


# ── virgin CPR on PDH/R1: the late rejection, and the second trade after an SL ──

from trading_app.service.cpr_trade_rule import propose_all, simulate, virgin_cpr_rejection

# 1 Sept 2026: 31 Aug H/L/C, 28 Aug H/L/C (31 Aug's CPR — never touched on
# 31 Aug, so virgin), the week of 24-28 Aug, and the bars to 14:00.
PREV_01 = (24128.7, 23993.6, 24080.4)
PREV2_01 = (24188.3, 24076.85, 24175.65)   # 28 Aug: 31 Aug's CPR 24,133-24,161
WEEK_01 = (24378.6, 24076.85, 24175.65)
BARS_01 = [
    ('09:15', 24077.55, 24082.6, 24027.8, 24041.15), ('09:20', 24042.55, 24051.05, 24031.7, 24039.1),
    ('09:25', 24039.4, 24065.6, 24032.95, 24063.95), ('09:30', 24064.75, 24076.65, 24058.7, 24066.2),
    ('09:35', 24066.35, 24067.75, 24039.95, 24040.85), ('09:40', 24041.25, 24047.75, 24025.2, 24028.35),
    ('09:45', 24030.15, 24030.15, 24009.5, 24021.2), ('09:50', 24021.35, 24031.35, 24014.35, 24022.05),
    ('09:55', 24023.0, 24027.7, 24014.7, 24018.25), ('10:00', 24019.0, 24036.1, 24015.05, 24032.95),
    ('10:05', 24033.95, 24036.5, 24023.3, 24033.0), ('10:10', 24034.0, 24040.95, 24031.05, 24036.25),
    ('10:15', 24037.55, 24056.95, 24035.3, 24050.2), ('10:20', 24052.3, 24064.05, 24045.2, 24062.5),
    ('10:25', 24062.95, 24066.25, 24055.1, 24061.05), ('10:30', 24062.8, 24073.2, 24060.2, 24064.75),
    ('10:35', 24067.3, 24073.05, 24058.8, 24068.55), ('10:40', 24069.1, 24070.95, 24058.6, 24064.95),
    ('10:45', 24066.0, 24071.45, 24062.1, 24069.7), ('10:50', 24069.6, 24092.65, 24066.2, 24087.2),
    ('10:55', 24088.65, 24091.4, 24074.45, 24082.0), ('11:00', 24083.65, 24088.2, 24072.4, 24077.4),
    ('11:05', 24078.75, 24096.3, 24072.95, 24092.85), ('11:10', 24094.75, 24111.75, 24091.0, 24107.05),
    ('11:15', 24108.75, 24122.7, 24107.0, 24121.65), ('11:20', 24123.95, 24127.45, 24117.55, 24120.35),
    ('11:25', 24121.7, 24130.4, 24116.5, 24126.3), ('11:30', 24127.9, 24143.15, 24123.55, 24124.55),
    ('11:35', 24126.65, 24127.45, 24110.7, 24112.6), ('11:40', 24113.4, 24121.95, 24104.0, 24119.0),
    ('11:45', 24120.0, 24125.4, 24109.45, 24110.6), ('11:50', 24111.55, 24119.5, 24108.2, 24117.65),
    ('11:55', 24119.65, 24125.4, 24114.9, 24116.7), ('12:00', 24118.65, 24123.1, 24110.6, 24121.3),
    ('12:05', 24124.2, 24128.65, 24121.95, 24127.9), ('12:10', 24129.1, 24132.2, 24114.5, 24117.75),
    ('12:15', 24118.5, 24120.75, 24104.85, 24110.1), ('12:20', 24111.65, 24113.7, 24097.0, 24097.65),
    ('12:25', 24098.75, 24112.75, 24094.65, 24109.65), ('12:30', 24110.9, 24115.7, 24102.75, 24108.4),
    ('12:35', 24109.85, 24113.05, 24105.05, 24111.05), ('12:40', 24111.8, 24117.35, 24108.7, 24115.15),
    ('12:45', 24116.5, 24118.7, 24106.55, 24107.45), ('12:50', 24109.3, 24109.8, 24092.3, 24093.5),
    ('12:55', 24095.1, 24097.35, 24087.45, 24092.25), ('13:00', 24094.05, 24094.1, 24081.7, 24084.15),
    ('13:05', 24085.05, 24085.2, 24070.7, 24074.95), ('13:10', 24075.8, 24075.8, 24049.8, 24050.2),
    ('13:15', 24050.75, 24051.0, 24041.8, 24049.05), ('13:20', 24050.35, 24059.25, 24046.65, 24056.9),
    ('13:25', 24058.25, 24060.25, 24045.6, 24049.8), ('13:30', 24051.45, 24062.45, 24049.9, 24059.95),
    ('13:35', 24060.6, 24065.65, 24033.3, 24036.9), ('13:40', 24038.0, 24038.15, 24021.2, 24034.2),
    ('13:45', 24034.9, 24034.95, 24017.2, 24019.25), ('13:50', 24019.65, 24019.65, 23987.85, 23993.8),
    ('13:55', 23995.0, 24010.95, 23988.05, 24000.15), ('14:00', 24001.8, 24001.8, 23976.9, 23978.55),
]


def _bars_01():
    bars = [{'time': t, 'open': o, 'high': h, 'low': l, 'close': c} for t, o, h, l, c in BARS_01]
    return bars + [{'time': f'{h:02d}:{m:02d}', 'open': 23990.0, 'high': 23995.0, 'low': 23985.0, 'close': 23990.0}
                   for h in range(14, 16) for m in range(0, 60, 5) if (14, 5) <= (h, m) < (15, 30)]


def _chart_01(virgin=True):
    chart = _chart(_bars_01()[0], PREV_01, WEEK_01)
    y = svc.levels(*PREV2_01)
    chart['prev_cpr'] = {'pp': y['pp'], 'bc': y['bc'], 'tc': y['tc'], 'virgin': virgin}
    return chart


def test_1_sept_2026_second_trade_is_the_virgin_cpr_rejection_run_to_pdl():
    chart = _chart_01()
    lv, y = chart['levels'], chart['prev_cpr']
    assert y['bc'] < lv['r1'] < y['tc']                       # the virgin CPR sits on PDH/R1
    trades = propose_all(chart, _bars_01())
    assert len(trades) == 2
    first, second = trades
    # The 09:15 rejection was a 55-pt candle: the first trade is the 09:30
    # small-candle retest under it, stopped at 10:50.
    assert first[0] == {'trade': 'SELL', 'entry': 24057.0, 'target': 24006.0, 'sl': 24079.0}
    assert 'too big to enter under' in first[1] and '09:30 small candle came back into the CPR' in first[1]
    assert first[3]['result'] == 'SL' and first[3]['exit_time'] == '10:50'
    p, why, i, sim = second
    assert _bars_01()[i]['time'] == '11:30'
    assert p['trade'] == 'SELL' and p['entry'] == 24122.0 and p['sl'] == 24145.0     # under its low, over its own high
    assert p['target'] == 23994.0                              # PDL: the 13:10 strong red candle broke the CPR
    assert sim['result'] == 'Target' and sim['pnl'] == 128.0 and sim['exit_time'] == '13:50'
    assert 'virgin CPR' in why and 'PDL 23,994 if a strong red candle breaks the CPR' in why


def test_without_the_cpr_break_the_target_stays_at_the_cpr():
    chart = _chart_01()
    bars = _bars_01()
    # Soften the 13:10 candle: reaches BC but closes back inside the CPR.
    bars[47] = dict(bars[47], close=24065.0)
    assert bars[47]['time'] == '13:10'
    p, why, i = virgin_cpr_rejection(chart, bars, 1)
    sim, used = simulate(bars[i:], p)
    assert used['target'] == round(chart['levels']['bc']) and sim['result'] == 'Target' and sim['exit_time'] == '13:10'


def test_no_virgin_cpr_no_late_rejection():
    chart = _chart_01(virgin=False)
    assert virgin_cpr_rejection(chart, _bars_01(), 1) is None
    assert len(propose_all(chart, _bars_01())) == 1
    # A virgin CPR that is not on PDH/R1 does not count either.
    chart = _chart_01()
    chart['prev_cpr'].update(bc=24300.0, tc=24330.0)
    assert virgin_cpr_rejection(chart, _bars_01(), 1) is None


def test_wide_cpr_or_a_very_large_candle_is_excluded():
    # 29 Jun 2026: wide CPR, big box, a 60-pt rejection candle -> no trade.
    chart = _chart_01()
    chart['width_pct'] = 0.25
    assert virgin_cpr_rejection(chart, _bars_01(), 1) is None
    chart = _chart_01()
    bars = _bars_01()
    bars[27] = dict(bars[27], high=bars[27]['low'] + 60.0, open=bars[27]['low'] + 58.0)     # the 11:30 candle, 60 pts tall
    late = virgin_cpr_rejection(chart, bars, 1)
    assert late is None or bars[late[2]]['time'] != '11:30'


def test_the_second_trade_also_follows_a_first_that_hit_its_target():
    chart = _chart_01()
    bars = _bars_01()
    # Let the first trade reach S1 24,006 before the 11:30 rejection: the second is still taken.
    bars[6] = dict(bars[6], low=24000.0, close=24004.0)
    trades = propose_all(chart, bars)
    assert trades[0][3]['result'] == 'Target' and len(trades) == 2 and trades[1][3]['after'] == 'Target'


# ── 31 Aug 2026: a big 09:15 break, entered off the small 09:20 candle ──

# 28 Aug 2026 (previous session) and 27 Aug (for yesterday's CPR), Fyers daily bars.
PREV_31 = (24188.3, 24076.85, 24175.65)
PREV2_31 = (24297.45, 24090.85, 24090.85)
C1_31 = {'time': '09:15', 'open': 24117.55, 'high': 24128.7, 'low': 24038.4, 'close': 24065.05}
C2_31 = {'time': '09:20', 'open': 24067.2, 'high': 24085.1, 'low': 24057.6, 'close': 24066.7}


def _chart_31(c1=C1_31, virgin=False):
    chart = _chart(c1, PREV_31, (24297.45, 24076.85, 24175.65))
    y = svc.levels(*PREV2_31)
    chart['prev_cpr'] = {'pp': y['pp'], 'bc': y['bc'], 'tc': y['tc'], 'virgin': virgin}
    return chart


def test_31_aug_2026_sells_under_the_0920_candle_with_the_stop_over_it():
    """The 09:15 candle opened over S1/PDL and closed under both, but at 90
    pts it is too big to enter under; the small 09:20 candle is the entry,
    its own high the stop, 1:2 the target (which lands on S3)."""
    p, why, i = propose(_chart_31(), _session(C1_31, C2_31))
    assert p == {'trade': 'SELL', 'entry': 24056.0, 'target': 23994.0, 'sl': 24087.0} and i == 1
    assert 'too big to enter under' in why and 'SL over its high' in why and 'target 1:2' in why


def test_a_0920_candle_that_is_not_small_or_not_under_the_box_is_no_entry():
    big2 = dict(C2_31, high=24110.0, low=24040.0)                     # 70 pts: not small
    p, _, _ = propose(_chart_31(), _session(C1_31, big2))
    assert not (p and p['entry'] == 24039.0)
    over2 = dict(C2_31, high=24120.0, close=24112.0, open=24108.0)    # back over S1: the break failed
    p, why, _ = propose(_chart_31(), _session(C1_31, over2))
    assert not (p and 'too big to enter' in why)


def test_a_09_15_candle_under_the_size_bar_takes_the_other_cases():
    small1 = dict(C1_31, high=24128.7, low=24090.0, open=24117.55, close=24095.0)   # 39 pts
    p, why, _ = propose(_chart_31(small1), _session(small1, C2_31))
    assert not (p and 'too big to enter' in why)


def test_a_virgin_cpr_between_entry_and_1_to_2_is_the_target():
    chart = _chart_31(virgin=True)
    chart['prev_cpr'].update(bc=24010.0, tc=24020.0)                  # sits between 24,056 and 23,994
    p, why, _ = propose(chart, _session(C1_31, C2_31))
    assert p['target'] == 24020.0 and 'virgin CPR' in why


def test_the_mirror_buys_over_a_small_0920_after_a_big_green_break():
    """Flip 31 Aug around PDH/R1: a big green 09:15 opening under the box
    and closing over both, a small 09:20 holding above -> BUY over it."""
    lv = svc.levels(*PREV_31)
    top = max(lv['pdh'], lv['r1'])
    c1 = {'time': '09:15', 'open': lv['pdh'] - 5, 'high': top + 85, 'low': lv['pdh'] - 15, 'close': top + 60}
    c2 = {'time': '09:20', 'open': top + 58, 'high': top + 70, 'low': top + 52, 'close': top + 59}
    p, why, i = propose(_chart_31(c1), _session(c1, c2))
    assert p['trade'] == 'BUY' and p['entry'] == math.ceil(c2['high'] + 1) and p['sl'] == math.floor(c2['low'] - 1.5)
    assert p['target'] == round(p['entry'] + 2 * (p['entry'] - p['sl'])) and 'too big to enter over' in why and i == 1


# ── 24 Aug 2026: 09:15 opened inside PDH/R1 and closed out above it ───────

# 21 Aug 2026 (previous session) and the week of 17-21 Aug (Fyers daily bars).
PREV_24 = (24284.05, 24206.8, 24252.0)
WEEK_24 = (24291.55, 24134.2, 24252.0)
BARS_24 = [
    {'time': '09:15', 'open': 24285.05, 'high': 24313.0, 'low': 24271.1, 'close': 24303.4},
    {'time': '09:20', 'open': 24303.9, 'high': 24311.9, 'low': 24298.75, 'close': 24303.0},
    {'time': '09:25', 'open': 24304.65, 'high': 24306.3, 'low': 24287.15, 'close': 24287.15},
    {'time': '09:30', 'open': 24290.3, 'high': 24291.05, 'low': 24272.55, 'close': 24276.7},
    {'time': '09:35', 'open': 24278.45, 'high': 24288.6, 'low': 24273.4, 'close': 24283.1},   # green: not the rejection
    {'time': '09:40', 'open': 24284.7, 'high': 24286.2, 'low': 24274.25, 'close': 24281.65},  # red, back to the box, closed under it
    {'time': '09:45', 'open': 24282.55, 'high': 24285.15, 'low': 24276.15, 'close': 24281.35},
    {'time': '09:50', 'open': 24281.2, 'high': 24283.95, 'low': 24272.1, 'close': 24272.65},  # fills 24,273
]


def _chart_24(c1=BARS_24[0]):
    chart = _chart(c1, PREV_24, WEEK_24)
    chart['prev_cpr'] = {'pp': 24227.18, 'bc': 24224.85, 'tc': 24229.52, 'virgin': False}
    return chart


def test_24_aug_2026_sells_under_the_red_candle_back_at_the_box():
    """Never broke the 09:15 high; 09:30 closed back under PDH/R1, the 09:40
    red candle climbed to the box and closed under it -> SELL under its
    low, SL over its own high, target the CPR and PDL past it."""
    p, why, i = propose(_chart_24(), _session(*BARS_24))
    assert p['trade'] == 'SELL' and p['entry'] == 24273.0 and p['sl'] == 24288.0 and p['target'] == 24250.0 and i == 5
    assert p['extend'] == {'level': 24207.0, 'name': 'PDL', 'through': pytest.approx(24245.42, abs=0.01)}
    assert 'inside PDH/R1' in why and '09:40 red candle' in why


def test_24_aug_2026_run_to_pdl_when_the_cpr_breaks():
    """A strong red bar through the CPR at the target moves it on to PDL."""
    from trading_app.service.cpr_trade_rule import propose_all
    tail = [{'time': '11:15', 'open': 24262.0, 'high': 24263.0, 'low': 24238.0, 'close': 24240.0},   # reaches TC, closes under BC
            {'time': '11:20', 'open': 24238.0, 'high': 24240.0, 'low': 24200.0, 'close': 24205.0}]   # PDL
    filler = [{'time': f'{h:02d}:{m:02d}', 'open': 24275.0, 'high': 24280.0, 'low': 24270.0, 'close': 24275.0}
              for h in range(9, 12) for m in range(0, 60, 5) if (9, 55) <= (h, m) < (11, 15)]
    bars = BARS_24 + filler + tail + _session()[:0] + [
        {'time': f'{h:02d}:{m:02d}', 'open': 24210.0, 'high': 24215.0, 'low': 24205.0, 'close': 24210.0}
        for h in range(11, 16) for m in range(0, 60, 5) if (11, 25) <= (h, m) < (15, 30)]
    (p, why, i, sim), = propose_all(_chart_24(), bars)
    assert p['target'] == 24207.0 and sim['result'] == 'Target' and sim['pnl'] == 66.0 and sim['exit_time'] == '11:20'


def test_a_break_of_the_0915_high_is_the_buy_instead():
    up = [BARS_24[0], {'time': '09:20', 'open': 24304.0, 'high': 24320.0, 'low': 24300.0, 'close': 24318.0}]
    p, why, i = propose(_chart_24(), _session(*up))
    assert p['trade'] == 'BUY' and p['entry'] == 24314.0 and p['sl'] == 24269.0 and i == 0
    assert 'broke the 09:15 high' in why


def test_the_rejection_needs_a_close_under_the_box_first():
    """09:35's green touch of R1 is not the signal, and a red candle before
    any close under the box is not either."""
    early = [BARS_24[0], BARS_24[1], dict(BARS_24[5], time='09:25')]     # red at the box, but never under it yet
    p, why, _ = propose(_chart_24(), _session(*early))
    assert not (p and p['entry'] == 24273.0)


def test_a_wide_cpr_or_an_open_outside_the_box_is_not_this_case():
    c1 = dict(BARS_24[0], open=24320.0, high=24330.0)                    # opened over the box, not inside it
    p, why, _ = propose(_chart_24(c1), _session(c1, *BARS_24[1:]))
    assert 'inside PDH/R1' not in why


# ── 19 Aug 2026: a big 09:15 candle straddling PDL/S1, the small red 09:20 is the entry ──

# 18 Aug 2026 (previous session) and the week of 10-14 Aug (Fyers daily bars).
PREV_19 = (24269.65, 24154.9, 24154.9)
WEEK_19 = (24496.05, 24339.2, 24417.65)
C1_19 = {'time': '09:15', 'open': 24152.05, 'high': 24172.85, 'low': 24102.5, 'close': 24118.7}
C2_19 = {'time': '09:20', 'open': 24119.75, 'high': 24125.3, 'low': 24105.3, 'close': 24106.05}


def _chart_19(c1=C1_19):
    chart = _chart(c1, PREV_19, WEEK_19)
    chart['prev_cpr'] = {'pp': 24291.57, 'bc': 24289.61, 'tc': 24293.53, 'virgin': True}   # above: not a target
    return chart


def test_19_aug_2026_sells_under_the_small_red_0920_after_a_straddle():
    """09:15 opened inside PDL/S1 24,117-24,155, traded over and under it
    and closed back inside — a 70-pt candle, undecided. The small red
    09:20 closing under the box is the entry: under its low, SL over its
    high, target 1:2 (the virgin CPR sits above, so it is not the target)."""
    p, why, i = propose(_chart_19(), _session(C1_19, C2_19))
    assert p == {'trade': 'SELL', 'entry': 24104.0, 'target': 24058.0, 'sl': 24127.0} and i == 1
    assert 'traded over and under it' in why and 'small red candle closed under PDL/S1' in why and 'target 1:2' in why


def test_the_straddle_needs_a_red_0920_closing_under_the_box():
    green2 = dict(C2_19, open=24106.0, close=24112.0)                  # green, still under the box
    p, why, _ = propose(_chart_19(), _session(C1_19, green2))
    assert not (p and 'traded over and under' in why)
    inside2 = dict(C2_19, close=24118.0, low=24115.0)                  # red but closed back inside the box
    p, why, _ = propose(_chart_19(), _session(C1_19, inside2))
    assert not (p and 'traded over and under' in why)


def test_a_straddle_that_does_not_cover_the_box_is_not_this_case():
    c1 = dict(C1_19, high=24150.0)                                      # never traded over the box
    p, why, _ = propose(_chart_19(c1), _session(c1, C2_19))
    assert not (p and 'traded over and under' in why)


# ── 3 Jun 2026: a big 09:15 candle down to PDL/S1, the trade is the retest from above ──

# 2 Jun 2026 (previous session) and the week of 25-29 May (Fyers daily bars).
PREV_3 = (23556.95, 23229.15, 23483.55)
WEEK_3 = (23866.5, 23548.35, 23707.45)
BARS_3 = [
    {'time': '09:15', 'open': 23415.95, 'high': 23447.65, 'low': 23288.2, 'close': 23299.3},    # 160 pts, closed on S1
    {'time': '09:20', 'open': 23299.9, 'high': 23311.85, 'low': 23285.95, 'close': 23304.25},   # green: no sell (and a first break over S1)
    {'time': '11:00', 'open': 23200.0, 'high': 23205.0, 'low': 23151.5, 'close': 23160.0},      # closed under PDL: that break is void
    {'time': '12:30', 'open': 23236.35, 'high': 23290.8, 'low': 23236.2, 'close': 23289.9},     # a graze of S1, not the break
    {'time': '12:35', 'open': 23290.55, 'high': 23335.9, 'low': 23283.85, 'close': 23324.5},    # the break
    {'time': '12:40', 'open': 23325.8, 'high': 23347.55, 'low': 23320.7, 'close': 23323.0},
    {'time': '12:45', 'open': 23325.15, 'high': 23331.55, 'low': 23291.85, 'close': 23300.45},
    {'time': '12:50', 'open': 23301.15, 'high': 23315.15, 'low': 23274.6, 'close': 23275.2},    # back inside the box, not under it
    {'time': '12:55', 'open': 23275.7, 'high': 23298.75, 'low': 23271.95, 'close': 23283.3},
    {'time': '13:00', 'open': 23284.2, 'high': 23305.95, 'low': 23279.95, 'close': 23292.2},    # green over S1, but closed mid-range
    {'time': '13:05', 'open': 23293.3, 'high': 23296.75, 'low': 23275.05, 'close': 23286.95},
    {'time': '13:10', 'open': 23287.0, 'high': 23298.35, 'low': 23275.7, 'close': 23298.35},    # the retest: small, green, closed on its high
    {'time': '13:15', 'open': 23302.95, 'high': 23326.3, 'low': 23300.3, 'close': 23307.8},
]


def _chart_3(c1=BARS_3[0]):
    chart = _chart(c1, PREV_3, WEEK_3)
    chart['prev_cpr'] = {'pp': 23491.42, 'bc': 23437.01, 'tc': 23545.83, 'virgin': False}
    return chart


def test_3_jun_2026_buys_over_the_1310_retest_of_the_box():
    p, why, i = propose(_chart_3(), BARS_3 + _session()[-25:])
    assert p == {'trade': 'BUY', 'entry': 23300.0, 'target': 23393.0, 'sl': 23274.0} and BARS_3[i]['time'] == '13:10'
    assert 'too big to enter under, and 09:20 green — no sell' in why and 'lost the box first, then 12:35 closed back over it' in why
    assert '13:10 small green candle retested it' in why and 'the CPR (BC 23,393)' in why


def test_the_retest_waits_for_a_clear_break_and_a_candle_closing_on_its_high():
    """Without the 13:10 candle, 13:00 (mid-range close) is not the retest,
    and the 12:30 graze of S1 never counted as the break."""
    p, why, _ = propose(_chart_3(), BARS_3[:11] + _session()[-25:])
    assert p is None and 'no retest' in why


def test_a_small_red_0920_under_the_box_is_the_break_case_not_the_retest():
    c2 = {'time': '09:20', 'open': 23290.0, 'high': 23292.0, 'low': 23220.0, 'close': 23222.0}   # closed under PDL
    p, why, _ = propose(_chart_3(), [BARS_3[0], c2] + _session()[-25:])
    assert p['trade'] == 'SELL' and 'retest' not in why


def test_a_box_never_lost_has_no_break_to_retest():
    """Without the 11:00 close under PDL the 12:35 close over S1 is a bounce,
    not a reclaim — no trade."""
    bars = [b for b in BARS_3 if b['time'] != '11:00']
    p, why, _ = propose(_chart_3(), bars + _session()[-25:])
    assert p is None and 'never lost PDL/S1 first' in why


def test_no_retest_by_1400_is_no_trade():
    late = [dict(b, time='14:%02d' % (5 + 5 * k)) for k, b in enumerate(BARS_3[3:])]   # the whole move after 14:00
    p, why, _ = propose(_chart_3(), BARS_3[:3] + late)
    assert p is None and 'no retest' in why


# ── the stop cap ──────────────────────────────────────────────────────────

def test_a_stop_over_100_points_is_halved():
    from trading_app.service.cpr_trade_rule import cap_risk
    t, why = cap_risk({'trade': 'BUY', 'entry': 25349.0, 'target': 25658.0, 'sl': 25195.0}, 'x')
    assert t['sl'] == 25272.0 and t['target'] == 25503.0 and 'SL halved to 25,272 (154-pt stop is over 100)' in why
    assert 'target 1:2 re-struck to 25,503' in why                          # 25,658 was 1:2 of the 154-pt risk
    t, why = cap_risk({'trade': 'BUY', 'entry': 22954.0, 'target': 22998.0, 'sl': 22719.0}, 'x')
    assert t['sl'] == 22836.0 and t['target'] == 22998.0 and 're-struck' not in why   # a level target stays
    t, why = cap_risk({'trade': 'SELL', 'entry': 25214.0, 'target': 24922.0, 'sl': 25360.0}, 'x')
    assert t['sl'] == 25287.0
    t, why = cap_risk({'trade': 'SELL', 'entry': 24273.0, 'target': 24207.0, 'sl': 24288.0}, 'x')
    assert t['sl'] == 24288.0 and why == 'x'                               # 15 pts: untouched


def test_propose_all_caps_the_stop_and_replays_with_it():
    """3 Jun's 09:15 rejection used to carry a 183-pt stop; the cap applies
    to every trade propose_all hands back."""
    from trading_app.service.cpr_trade_rule import propose_all
    c1 = {'time': '09:15', 'open': 25259.0, 'high': 25360.0, 'low': 25190.0, 'close': 25336.0}
    chart = _chart(c1, (25304.0, 25040.0, 25247.0), (25400.0, 24900.0, 25247.0))
    chart['prev_cpr'] = {'pp': 25200.0, 'bc': 25190.0, 'tc': 25210.0, 'virgin': False}
    out = propose_all(chart, _session(c1))
    for p, why, i, sim in out:
        if p:
            assert abs(p['entry'] - p['sl']) <= 100


# ── 7 Apr 2026: inside the CPR on a big 09:15 candle — the cross candle after the break ──

PREV_7 = (22998.35, 22542.95, 22968.25)
WEEK_7 = (22759.45, 22344.6, 22515.3)
BARS_7 = [
    {'time': '09:15', 'open': 22838.7, 'high': 22843.95, 'low': 22719.3, 'close': 22773.05},   # 125 pts, closed inside the CPR
    {'time': '09:20', 'open': 22773.75, 'high': 22825.8, 'low': 22758.75, 'close': 22802.15},
    {'time': '09:25', 'open': 22804.65, 'high': 22893.4, 'low': 22801.3, 'close': 22889.85},
    {'time': '09:30', 'open': 22891.45, 'high': 22901.05, 'low': 22856.75, 'close': 22858.35},
    {'time': '09:55', 'open': 22843.95, 'high': 22892.65, 'low': 22842.45, 'close': 22886.15},
    {'time': '10:00', 'open': 22889.35, 'high': 22923.5, 'low': 22879.3, 'close': 22913.9},    # the break: closed over TC 22,902
    {'time': '10:05', 'open': 22914.45, 'high': 22928.7, 'low': 22893.75, 'close': 22897.5},   # the cross candle: back to TC
    {'time': '10:10', 'open': 22899.5, 'high': 22923.55, 'low': 22887.0, 'close': 22920.0},
    {'time': '10:15', 'open': 22921.8, 'high': 22952.2, 'low': 22917.45, 'close': 22943.2},    # fills 22,930
]


def _chart_7(c1=BARS_7[0]):
    chart = _chart(c1, PREV_7, WEEK_7)
    chart['prev_cpr'] = {'pp': 22559.32, 'bc': 22482.42, 'tc': 22636.21, 'virgin': False}
    return chart


def test_7_apr_2026_buys_over_the_cross_candle_after_the_cpr_break():
    p, why, i = propose(_chart_7(), BARS_7 + _session()[-25:])
    assert p['trade'] == 'BUY' and p['entry'] == 22930.0 and p['sl'] == 22892.0 and BARS_7[i]['time'] == '10:05'
    assert '10:00 candle closed above the CPR at 22,914' in why and '10:05 cross candle came back to TC' in why


def test_a_small_0915_inside_the_cpr_still_enters_on_the_breakout_candle():
    c1 = dict(BARS_7[0], low=22800.0, high=22843.95, close=22820.0)                    # 44 pts
    bars = [c1] + BARS_7[1:]
    p, why, i = propose(_chart_7(c1), bars + _session()[-25:])
    assert p['trade'] == 'BUY' and 'cross candle' not in why and 'SL at the 09:15 low' in why


def test_a_close_back_inside_the_cpr_voids_the_break():
    fail = dict(BARS_7[6], close=22880.0, low=22870.0)                                   # 10:05 closed well back inside
    p, why, i = propose(_chart_7(), BARS_7[:6] + [fail] + _session()[-25:])
    assert not (p and BARS_7[6]['time'] == (BARS_7 + [fail])[i]['time'] and 'cross candle' in why)


# ── a virgin CPR between the entry and the target is the target ───────────

def test_virgin_cpr_between_entry_and_target_is_the_target():
    from trading_app.service.cpr_trade_rule import virgin_target
    chart = {'virgin_cprs': [{'date': '2026-04-15', 'bc': 23731.5, 'tc': 23805.6},
                             {'date': '2026-04-23', 'bc': 24396.88, 'tc': 24434.43}]}
    t, why = virgin_target({'trade': 'SELL', 'entry': 23929.0, 'target': 23741.0, 'sl': 24023.0}, 'x', chart)
    assert t['target'] == 23806.0 and 'virgin CPR 23,732-23,806 (2026-04-15' in why
    # a BUY towards the upper one takes its BC; nothing between -> unchanged
    t, _ = virgin_target({'trade': 'BUY', 'entry': 24300.0, 'target': 24500.0, 'sl': 24250.0}, 'x', chart)
    assert t['target'] == 24397.0
    t, why = virgin_target({'trade': 'BUY', 'entry': 24300.0, 'target': 24380.0, 'sl': 24250.0}, 'x', chart)
    assert t['target'] == 24380.0 and why == 'x'


def test_a_virgin_cpr_too_near_the_entry_is_ignored():
    from trading_app.service.cpr_trade_rule import virgin_target
    chart = {'virgin_cprs': [{'date': '2026-04-15', 'bc': 23900.0, 'tc': 23915.0}]}   # 14 pts under a 94-pt risk
    t, why = virgin_target({'trade': 'SELL', 'entry': 23929.0, 'target': 23741.0, 'sl': 24023.0}, 'x', chart)
    assert t['target'] == 23741.0 and why == 'x'


# ── a level rejection has to reach the level ──────────────────────────────

def test_a_candle_that_stops_short_of_s1_is_not_a_rejection():
    """6 Mar 2026: S1 24,579; the 09:20 candle's low was 24,587 — 8 points
    short. Near is not a touch: no BUY off it."""
    prev = (24864.5, 24592.0, 24850.0)              # PDL 24,592 under S1 ~24,673 for the day
    lv = svc.levels(*prev)
    c1 = {'time': '09:15', 'open': lv['s1'] + 60, 'high': lv['s1'] + 75, 'low': lv['s1'] + 20, 'close': lv['s1'] + 30}
    short = {'time': '09:20', 'open': lv['s1'] + 12, 'high': lv['s1'] + 34, 'low': lv['s1'] + 8, 'close': lv['s1'] + 33}     # small, closes on its high, low 8 pts over S1
    touch = dict(short, time='09:25', low=lv['s1'] - 1)                                                                     # the same candle reaching S1
    chart = _chart(c1, prev, (25300.0, 25000.0, 25200.0))          # weekly CPR well above: the open is below both
    p, why, _ = propose(chart, _session(c1, short))
    assert not (p and 'rejected from S1' in why)
    p, why, _ = propose(chart, _session(c1, short, touch))
    assert p and p['trade'] == 'BUY' and 'rejected from S1' in why and '09:25' in why


# ── 10 Mar 2026: gap-up, a big reversal candle with Cam R3 in the box — the retest is the trade ──

PREV_10 = (24078.15, 23697.8, 24028.05)      # 9 Mar: R1 24,172 / PDH 24,078 box, Cam R3 24,133 inside it
WEEK_10 = (24750.0, 24420.0, 24600.0)
BARS_10 = [
    {'time': '09:15', 'open': 24280.8, 'high': 24303.8, 'low': 24149.0, 'close': 24150.4},
    {'time': '09:20', 'open': 24150.7, 'high': 24174.65, 'low': 24138.6, 'close': 24152.6},
    {'time': '10:10', 'open': 24139.15, 'high': 24160.95, 'low': 24131.1, 'close': 24151.05},
    {'time': '10:15', 'open': 24151.5, 'high': 24213.7, 'low': 24149.3, 'close': 24204.55},   # the 64-pt reversal: the breakout
    {'time': '10:20', 'open': 24205.0, 'high': 24208.2, 'low': 24151.9, 'close': 24169.0},
    {'time': '12:40', 'open': 24156.65, 'high': 24163.35, 'low': 24142.45, 'close': 24147.05},
    {'time': '12:45', 'open': 24147.6, 'high': 24184.5, 'low': 24142.75, 'close': 24184.5},   # retest of Cam R3, closed back over R1
    {'time': '12:50', 'open': 24184.45, 'high': 24220.05, 'low': 24181.9, 'close': 24218.4},
]


def _chart_10(c1=BARS_10[0]):
    chart = _chart(c1, PREV_10, WEEK_10)
    chart['prev_cpr'] = {'pp': 23900.0, 'bc': 23880.0, 'tc': 23920.0, 'virgin': False}
    return chart


def test_10_mar_2026_buys_the_cam_r3_retest_not_the_big_reversal():
    p, why, i = propose(_chart_10(), BARS_10 + _session()[-25:])
    assert p == {'trade': 'BUY', 'entry': 24186.0, 'target': 24296.0, 'sl': 24131.0} and BARS_10[i]['time'] == '12:45'
    assert 'too big to enter over' in why and 'Cam R3 24,133 sits inside the box' in why and '12:45 candle retested Cam R3' in why


def test_a_small_reversal_candle_is_still_entered_over_its_high():
    small = dict(BARS_10[3], high=24190.0, low=24160.0, open=24162.0, close=24188.0)         # 30 pts
    p, why, _ = propose(_chart_10(), BARS_10[:3] + [small] + _session()[-25:])
    assert p['trade'] == 'BUY' and 'reversed up, closed' in why and 'retest' not in why


def test_a_close_back_under_the_box_voids_the_gap_retest():
    fail = {'time': '10:25', 'open': 24169.0, 'high': 24170.0, 'low': 24060.0, 'close': 24065.0}
    p, why, _ = propose(_chart_10(), BARS_10[:5] + [fail] + BARS_10[5:] + _session()[-25:])
    assert p is None and 'the break failed' in why


def test_a_cpr_rejection_has_to_reach_the_daily_cpr_not_the_weekly_band():
    """28 Jan 2026: daily CPR 25,090-25,147 merged with the weekly 25,128-25,287.
    With the CPRs interlinked the rejection candle must be small and reach
    the daily CPR: the real 111-pt 09:15 (low 25,226, weekly band only) is
    neither; a small candle 20 pts over TC is not a touch; the same candle
    5 pts over TC is."""
    prev = (25246.65, 24932.55, 25175.4)
    lv = svc.levels(*prev)
    c1 = {'time': '09:15', 'open': 25258.85, 'high': 25340.0, 'low': 25225.7, 'close': 25336.0}
    chart = _chart(c1, prev, (25286.55, 25127.95, 25207.25))
    chart['weekly'] = {'pp': 25207.25, 'bc': 25127.95, 'tc': 25286.55}
    p, why, _ = propose(chart, _session(c1))
    assert not (p and 'rejected from' in why and 'CPR' in why)
    short = {'time': '11:50', 'open': lv['tc'] + 22, 'high': lv['tc'] + 40, 'low': lv['tc'] + 20, 'close': 25300.0}   # green, small, 20 pts short of TC
    p, why, _ = propose(chart, _session(c1, short))
    assert not (p and 'interlinked' in why)
    touch = dict(short, low=lv['tc'] + 5)
    p, why, i = propose(chart, _session(c1, touch))
    assert p and p['trade'] == 'BUY' and 'interlinked CPRs' in why and '11:50 small candle' in why



# ── 6 Apr 2026: interlinked CPRs, the small 11:50 rejection with the stop on the VWAP ──

PREV_6 = (22782.3, 22182.55, 22713.1)
WEEK_6 = (22733.55, 22491.1, 22612.7)          # weekly CPR ~22,562-22,663 over the daily 22,482-22,636
BARS_6 = [
    {'time': '09:15', 'open': 22780.3, 'high': 22798.25, 'low': 22591.7, 'close': 22666.05, 'volume': 22052859},
    {'time': '09:30', 'open': 22604.85, 'high': 22684.85, 'low': 22596.5, 'close': 22681.05, 'volume': 9000000},   # the 88-pt rejection: skipped
] + [
    # the morning's chop around 22,615 that pulls the session VWAP down to ~22,635
    {'time': f'{h:02d}:{m:02d}', 'open': 22615.0, 'high': 22625.0, 'low': 22605.0, 'close': 22615.0, 'volume': 6000000}
    for h in range(9, 12) for m in range(0, 60, 5) if (9, 35) <= (h, m) <= (11, 40)
] + [
    {'time': '11:45', 'open': 22644.3, 'high': 22663.3, 'low': 22634.55, 'close': 22645.7, 'volume': 3000000},
    {'time': '11:50', 'open': 22646.2, 'high': 22675.25, 'low': 22644.8, 'close': 22668.2, 'volume': 3000000},    # the small rejection
    {'time': '11:55', 'open': 22669.0, 'high': 22707.8, 'low': 22665.7, 'close': 22676.4, 'volume': 3000000},
]


def test_6_apr_2026_buys_over_the_small_1150_rejection_with_the_stop_on_the_vwap():
    from trading_app.service.cpr_trade_rule import vwap_at
    chart = _chart(BARS_6[0], PREV_6, WEEK_6)
    chart['weekly'] = {'pp': 22612.32, 'bc': 22561.92, 'tc': 22662.71}
    k = next(i for i, b in enumerate(BARS_6) if b['time'] == '11:50')
    vw = vwap_at(BARS_6, k)
    assert BARS_6[k]['low'] - 22.7 <= vw < BARS_6[k]['low']                        # the VWAP runs just under the 11:50 low
    p, why, i = propose(chart, BARS_6 + _session()[-25:])
    assert p['trade'] == 'BUY' and p['entry'] == 22677.0 and p['sl'] == math.floor(vw) and BARS_6[i]['time'] == '11:50'
    assert 'under the VWAP' in why and '11:50 small candle' in why and 'Cam R3 (inside the PDH/R1 box) 22,878' in why   # PDH's box holds Cam R3


def test_without_a_vwap_under_the_low_the_stop_is_the_low():
    bars = [dict(b, volume=0) for b in BARS_6]                                    # no volumes: no VWAP
    chart = _chart(bars[0], PREV_6, WEEK_6)
    chart['weekly'] = {'pp': 22612.32, 'bc': 22561.92, 'tc': 22662.71}
    p, why, _ = propose(chart, bars + _session()[-25:])
    assert p['sl'] == math.floor(22644.8 - 1.5) and 'under its low' in why



def test_a_big_candle_off_s1_is_not_entered():
    """14 Jul 2026: the 68-pt 09:15 candle touched S1 and closed at the CPR — no entry."""
    prev = (24259.8, 24000.2, 24211.0)
    lv = svc.levels(*prev)
    c1 = {'time': '09:15', 'open': 24068.0, 'high': 24118.8, 'low': 24050.55, 'close': 24109.75}
    chart = _chart(c1, prev, (24300.0, 24050.0, 24200.0))
    chart['weekly'] = {'pp': 24181.0, 'bc': 24168.05, 'tc': 24193.95}
    p, why, _ = propose(chart, _session(c1))
    assert not (p and 'rejected from S1' in why)


# ── 13 Jan 2026: the weekly CPR on the PDH/R1 box, a strong rejection from PDH ──

PREV_13 = (25813.15, 25473.4, 25790.25)
WEEK_13 = (26071.55, 25757.15, 25851.15)        # weekly CPR ~25,788-25,998 over PDH 25,813 / R1 25,911
BARS_13 = [
    {'time': '09:15', 'open': 25897.35, 'high': 25899.8, 'low': 25836.4, 'close': 25846.25},
    {'time': '09:20', 'open': 25845.75, 'high': 25852.65, 'low': 25813.75, 'close': 25836.3},
    {'time': '09:25', 'open': 25836.75, 'high': 25836.75, 'low': 25760.05, 'close': 25762.85},   # strong red through PDH, 77 pts
    {'time': '09:30', 'open': 25761.4, 'high': 25778.0, 'low': 25731.55, 'close': 25745.35},
]


def _chart_13(c1=BARS_13[0]):
    chart = _chart(c1, PREV_13, WEEK_13)
    chart['weekly'] = {'pp': 25893.17, 'bc': 25788.23, 'tc': 25998.1}
    return chart


def test_13_jan_2026_sells_the_strong_pdh_rejection_inside_the_weekly_zone():
    p, why, i = propose(_chart_13(), BARS_13 + _session()[-25:])
    assert p == {'trade': 'SELL', 'entry': 25759.0, 'target': 25643.0, 'sl': 25839.0} and BARS_13[i]['time'] == '09:25'
    assert why.startswith('weekly CPR') and 'rejected from PDH 25,813' in why and 'target CPR BC 25,643' in why


def test_without_the_weekly_cpr_on_the_box_the_big_candle_is_not_a_rejection_entry():
    chart = _chart_13()
    chart['weekly'] = {'pp': 26300.0, 'bc': 26250.0, 'tc': 26350.0}            # weekly CPR far above the box
    p, why, _ = propose(chart, BARS_13 + _session()[-25:])
    assert not (p and why.startswith('weekly CPR'))


def test_nearest_target_skips_a_level_inside_the_risk():
    """TC 25,741 is 18 pts under the 25,759 entry (risk 80): skipped for BC."""
    p, why, _ = propose(_chart_13(), BARS_13 + _session()[-25:])
    assert p['target'] == 25643.0 and 'CPR TC' not in why.split('target')[-1]


# ── 4 Sep 2026: wide CPR, the break has no room before the PDH/R1 box ──

PREV_4S = (24025.4, 23873.45, 23897.7)          # CPR 23,899-23,949, R1 23,975 / PDH 24,025
BARS_4S = [
    {'time': '09:15', 'open': 23910.9, 'high': 23948.0, 'low': 23916.0, 'close': 23927.2},    # inside the CPR, no wicks out of it
    {'time': '10:00', 'open': 23935.0, 'high': 23963.0, 'low': 23932.0, 'close': 23959.0},     # strong close over TC
]


def _chart_4s(cpr_type):
    chart = _chart(BARS_4S[0], PREV_4S, (24260.0, 24080.0, 24210.0))
    chart['weekly'] = {'pp': 24210.37, 'bc': 24193.01, 'tc': 24227.72}
    chart['cpr_type'] = cpr_type
    return chart


def test_4_sep_2026_a_wide_cpr_break_with_the_box_inside_the_risk_is_skipped():
    p, why, _ = propose(_chart_4s('Wide'), BARS_4S + _session()[-25:])
    assert p is None and 'no room' in why and 'over the 23,964 entry' in why


def test_the_same_break_on_a_medium_cpr_day_is_still_taken():
    p, why, _ = propose(_chart_4s('Medium'), BARS_4S + _session()[-25:])
    assert p and p['trade'] == 'BUY' and p['entry'] == 23964.0 and 'no room' not in why



# ── 26 May 2026: 09:15 inside the CPR but wicked both ways — the box decides ──

PREV_26 = (24054.45, 23922.85, 24031.7)         # CPR 23,989-24,017, box PDH 24,054 / R1 24,083
BARS_26 = [
    {'time': '09:15', 'open': 24004.1, 'high': 24020.75, 'low': 23965.7, 'close': 24012.55},   # inside, wicks over TC and under BC
    {'time': '09:20', 'open': 24011.55, 'high': 24026.45, 'low': 24011.4, 'close': 24020.6},
    {'time': '10:30', 'open': 24070.0, 'high': 24079.25, 'low': 24066.85, 'close': 24078.3},   # inside the box
    {'time': '10:35', 'open': 24078.65, 'high': 24089.8, 'low': 24077.25, 'close': 24085.0},   # the break: closed over R1
    {'time': '10:40', 'open': 24084.85, 'high': 24086.05, 'low': 24071.55, 'close': 24073.15}, # weakness: back under the box
]


def _chart_26(c1=BARS_26[0]):
    chart = _chart(c1, PREV_26, (23760.0, 23500.0, 23640.0))
    chart['weekly'] = {'pp': 23632.1, 'bc': 23588.5, 'tc': 23675.7}
    return chart


def test_26_may_2026_sells_the_failed_box_break_under_the_weak_candle():
    p, why, i = propose(_chart_26(), BARS_26 + _session()[-25:])
    assert p['trade'] == 'SELL' and p['entry'] == 24070.0 and p['sl'] == 24092.0 and BARS_26[i]['time'] == '10:40'
    assert 'wicked out of it both ways' in why and '10:35 candle closed over PDH/R1' in why and '10:40 showed weakness' in why


def test_a_break_of_the_break_candles_high_is_the_buy_instead():
    strong = {'time': '10:40', 'open': 24085.0, 'high': 24098.0, 'low': 24083.0, 'close': 24096.0}
    p, why, _ = propose(_chart_26(), BARS_26[:4] + [strong] + _session()[-25:])
    assert p['trade'] == 'BUY' and p['entry'] == 24091.0 and p['sl'] == math.floor(24077.25 - 1.5) and 'broke its high' in why


def test_a_0915_that_stays_inside_the_cpr_is_the_ordinary_inside_case():
    c1 = dict(BARS_26[0], high=24016.0, low=23992.0)
    p, why, _ = propose(_chart_26(c1), [c1] + BARS_26[1:] + _session()[-25:])
    assert 'wicked' not in why


# ── 29 Jan 2026: the downside continuation refused for a virgin CPR, the rejection back over the box bought ──

PREV_29 = (25372.1, 25187.65, 25342.75)         # CPR 25,280-25,322, lower box PDL 25,188 / S1 25,230
BARS_29 = [
    {'time': '09:15', 'open': 25345.0, 'high': 25359.35, 'low': 25248.55, 'close': 25280.55},   # inside, wicked both ways
    {'time': '09:50', 'open': 25227.45, 'high': 25238.35, 'low': 25175.75, 'close': 25180.7},   # closed under the box: the break
    {'time': '09:55', 'open': 25181.45, 'high': 25192.15, 'low': 25159.8, 'close': 25184.85},   # broke its low — refused (virgin CPR under)
    {'time': '10:00', 'open': 25185.3, 'high': 25197.9, 'low': 25167.05, 'close': 25193.0},
    {'time': '10:50', 'open': 25184.35, 'high': 25227.95, 'low': 25183.55, 'close': 25224.05},  # back inside the box, not over it
    {'time': '10:55', 'open': 25223.95, 'high': 25240.55, 'low': 25218.6, 'close': 25234.0},    # over S1: the rejection
]


def _chart_29(virgin=True):
    chart = _chart(BARS_29[0], PREV_29, (25500.0, 25100.0, 25300.0))
    chart['weekly'] = {'pp': 25207.25, 'bc': 25127.95, 'tc': 25286.55}
    chart['virgin_cprs'] = [{'date': '2026-01-28', 'bc': 25089.6, 'tc': 25146.8}] if virgin else []
    return chart


def test_29_jan_2026_refuses_the_sell_for_the_virgin_cpr_and_buys_the_rejection_back():
    p, why, i = propose(_chart_29(), BARS_29 + _session()[-25:])
    assert p['trade'] == 'BUY' and p['entry'] == 25242.0 and p['sl'] == math.floor(25159.8 - 1.5) and BARS_29[i]['time'] == '10:55'
    assert 'virgin CPR 25,090-25,147' in why and 'no sell' in why and '10:55 candle rejected back over the box' in why and 'CPR TC 25,322' in why


def test_without_the_virgin_cpr_the_continuation_sell_is_taken():
    p, why, _ = propose(_chart_29(virgin=False), BARS_29 + _session()[-25:])
    assert p['trade'] == 'SELL' and p['entry'] == 25174.0 and 'broke its low' in why


# ── 14 Jan 2026: a narrow CPR far from the boxes is not faded ──

PREV_14 = (25899.8, 25603.3, 25732.3)           # CPR 25,739-25,752 (13 pts), boxes 135-148 pts away
BARS_14 = [
    {'time': '09:15', 'open': 25648.55, 'high': 25719.35, 'low': 25638.35, 'close': 25683.25},
    {'time': '09:40', 'open': 25732.4, 'high': 25739.6, 'low': 25703.15, 'close': 25706.65},   # a strong red fade of the CPR
]


def _chart_14():
    chart = _chart(BARS_14[0], PREV_14, (26071.55, 25757.15, 25851.15))
    chart['weekly'] = {'pp': 25893.17, 'bc': 25788.23, 'tc': 25998.1}
    return chart


def test_14_jan_2026_the_cpr_fade_is_off_when_the_boxes_are_far():
    p, why, _ = propose(_chart_14(), BARS_14 + _session()[-25:])
    assert p is None and 'too thin to trade against' in why and 'entries only on a break of a box or a rejection from one' in why


def test_a_box_rejection_is_still_taken_on_a_far_box_day():
    lv = svc.levels(*PREV_14)
    touch = {'time': '10:30', 'open': lv['pdl'] + 6, 'high': lv['pdl'] + 30, 'low': lv['pdl'] - 1, 'close': lv['pdl'] + 29}   # small green off PDL
    p, why, _ = propose(_chart_14(), BARS_14 + [touch] + _session()[-25:])
    assert p and p['trade'] == 'BUY' and 'rejected from PDL' in why


def test_the_same_fade_with_the_boxes_close_is_still_a_cpr_rejection():
    prev = (25790.0, 25700.0, 25745.0)           # boxes ~30 pts from a 15-pt CPR
    lv = svc.levels(*prev)
    c1 = {'time': '09:15', 'open': lv['bc'] - 60, 'high': lv['bc'] - 20, 'low': lv['bc'] - 70, 'close': lv['bc'] - 30}
    fade = {'time': '09:40', 'open': lv['bc'] - 5, 'high': lv['bc'] + 2, 'low': lv['bc'] - 28, 'close': lv['bc'] - 26}   # 30 pts: small enough to enter off
    chart = _chart(c1, prev, (26071.55, 25957.15, 26051.15))
    chart['weekly'] = {'pp': 26000.0, 'bc': 25990.0, 'tc': 26010.0}
    p, why, _ = propose(chart, _session(c1, fade))
    assert p and 'rejected from the CPR' in why



def test_a_big_cpr_rejection_waits_for_a_small_sell_candle_back_into_the_cpr():
    """1 Sep 2026: the 09:25 strong green candle into the CPR is not the
    retest (it closed on its high); 09:30, closing in its lower half, is."""
    chart = _chart_01()
    bars = _bars_01()
    p, why, i = propose(chart, bars)
    assert bars[i]['time'] == '09:30' and p['entry'] == 24057.0 and 'target S1 24,006' in why


# ── 20 May 2026: the VWAP just ahead of the entry refuses it ──

def test_a_vwap_between_the_candle_and_the_entry_refuses_it():
    from trading_app.service.cpr_trade_rule import vwap_in_the_way
    bars = [{'time': '09:15', 'open': 23480.0, 'high': 23490.0, 'low': 23484.0, 'close': 23487.0, 'volume': 100},   # VWAP = 23,487
            {'time': '10:00', 'open': 23541.8, 'high': 23546.65, 'low': 23495.75, 'close': 23503.9, 'volume': 0}]
    sell = {'trade': 'SELL', 'entry': 23484.0, 'target': 23368.0, 'sl': 23542.0}
    p, why = vwap_in_the_way(sell, 'x', bars, 1)
    assert p is None and 'VWAP 23,487 sits between the 10:00 candle and the 23,484 entry' in why
    straddle = [dict(bars[0]), {'time': '10:00', 'open': 23480.0, 'high': 23500.0, 'low': 23470.0, 'close': 23475.0, 'volume': 0}]
    p, why = vwap_in_the_way({'trade': 'SELL', 'entry': 23469.0, 'target': 23400.0, 'sl': 23502.0}, 'x', straddle, 1)
    assert p and why == 'x'                                                   # the candle already straddles the VWAP
    buy = {'trade': 'BUY', 'entry': 23520.0, 'target': 23600.0, 'sl': 23480.0}   # VWAP well below a BUY entry
    p, why = vwap_in_the_way(buy, 'x', bars, 1)
    assert p and why == 'x'


def test_20_may_2026_gap_down_reversal_is_refused_for_the_vwap():
    from trading_app.service.cpr_trade_rule import propose_all
    prev = (23782.3, 23587.2, 23618.0)           # 19 May: S1 23,543 / PDL 23,587
    bars = [   # the real morning, so the session VWAP is the real 23,487 at 10:00
        {'time': '09:15', 'open': 23457.25, 'high': 23469.9, 'low': 23397.3, 'close': 23459.9, 'volume': 18537180},
        {'time': '09:20', 'open': 23461.45, 'high': 23464.85, 'low': 23442.45, 'close': 23458.7, 'volume': 9844695},
        {'time': '09:25', 'open': 23458.4, 'high': 23481.95, 'low': 23453.75, 'close': 23476.95, 'volume': 6254990},
        {'time': '09:30', 'open': 23477.85, 'high': 23524.9, 'low': 23473.5, 'close': 23520.15, 'volume': 4564720},
        {'time': '09:35', 'open': 23521.65, 'high': 23535.75, 'low': 23506.1, 'close': 23533.2, 'volume': 5834414},
        {'time': '09:40', 'open': 23534.8, 'high': 23536.9, 'low': 23498.55, 'close': 23524.25, 'volume': 4581031},
        {'time': '09:45', 'open': 23523.25, 'high': 23536.45, 'low': 23511.95, 'close': 23522.25, 'volume': 5758885},
        {'time': '09:50', 'open': 23521.25, 'high': 23541.45, 'low': 23520.9, 'close': 23526.55, 'volume': 3129589},
        {'time': '09:55', 'open': 23526.5, 'high': 23548.6, 'low': 23516.5, 'close': 23541.9, 'volume': 2822006},
        {'time': '10:00', 'open': 23541.8, 'high': 23546.65, 'low': 23495.75, 'close': 23503.9, 'volume': 3546189},   # the reversal at S1/PDL
    ]
    chart = _chart(bars[0], prev, (23760.0, 23500.0, 23634.5))
    chart['weekly'] = {'pp': 23634.5, 'bc': 23630.0, 'tc': 23639.0}
    chart['cpr_type'] = 'Narrow'
    out = propose_all(chart, bars + _session()[-25:])
    assert out[0][0] is None and 'the trigger would have to cross it' in out[0][1]


# ── 23 Feb 2026: two big candles out of the box are not joined — the reversal is sold ──

PREV_23 = (25663.55, 25379.75, 25571.25)        # 20 Feb: box PDH 25,664 / R1 25,697, CPR 25,522-25,555
BARS_23 = [
    {'time': '09:15', 'open': 25678.4, 'high': 25707.1, 'low': 25626.5, 'close': 25697.4},    # 81 pts, opened in the box, closed over it
    {'time': '09:20', 'open': 25698.15, 'high': 25741.2, 'low': 25697.05, 'close': 25736.3},  # 44 pts, broke the 09:15 high
    {'time': '10:40', 'open': 25690.05, 'high': 25690.5, 'low': 25655.15, 'close': 25656.95}, # closed under the box
    {'time': '11:15', 'open': 25697.0, 'high': 25699.2, 'low': 25673.45, 'close': 25673.45},  # red into the box, closed inside it
    {'time': '11:20', 'open': 25673.45, 'high': 25678.4, 'low': 25660.2, 'close': 25663.2},   # red back to the box, closed under it
]


def _chart_23():
    chart = _chart(BARS_23[0], PREV_23, (25760.0, 25460.0, 25610.0))
    chart['weekly'] = {'pp': 25609.75, 'bc': 25590.5, 'tc': 25629.0}
    return chart


def test_23_feb_2026_skips_the_big_continuation_and_sells_the_rejection_from_the_box():
    p, why, i = propose(_chart_23(), BARS_23 + _session()[-25:])
    assert p['trade'] == 'SELL' and p['entry'] == 25659.0 and p['sl'] == 25680.0 and BARS_23[i]['time'] == '11:20'
    assert 'too big to join' in why and '11:20 red candle climbed to it' in why and 'target the CPR (TC 25,555)' in why


def test_a_small_break_of_the_0915_high_is_still_joined():
    c1 = dict(BARS_23[0], low=25660.0)                                              # 47 pts: not big
    b2 = dict(BARS_23[1], high=25720.0, close=25715.0)                              # 23 pts
    p, why, _ = propose(_chart_23() | {'first_candle': c1}, [c1, b2] + _session()[-25:])
    assert p and p['trade'] == 'BUY' and 'broke the 09:15 high' in why and 'too big' not in why



# ── 3 Mar 2025: the target runs through the box to Cam S3, and a fill bar that opened over the stop is not stopped ──

def test_a_target_at_the_box_edge_moves_to_the_cam_line_inside_it():
    prev = (22450.35, 22104.85, 22125.7)          # 28 Feb 2025: PDL 22,105 / S1 22,003 with Cam S3 22,030 inside
    lv = svc.levels(*prev)
    assert lv['s1'] < lv['cs3'] < lv['pdl']
    c1 = {'time': '09:15', 'open': 22194.55, 'high': 22261.55, 'low': 22194.2, 'close': 22253.75}   # inside the CPR, 67 pts
    bars = [c1,
            {'time': '09:25', 'open': 22186.65, 'high': 22199.1, 'low': 22154.85, 'close': 22174.55},   # closed under BC
            {'time': '09:30', 'open': 22173.2, 'high': 22180.55, 'low': 22152.5, 'close': 22170.1}]     # the cross candle
    chart = _chart(c1, prev, (22610.0, 22120.0, 22330.0))
    chart['weekly'] = {'pp': 22299.2, 'bc': 22211.95, 'tc': 22386.45}
    p, why, _ = propose(chart, bars + _session()[-25:])
    assert p['trade'] == 'SELL' and p['entry'] == 22151.0 and p['target'] == round(lv['cs3']) and 'Cam S3 (inside the PDL/S1 box)' in why


def test_a_fill_bar_that_opened_beyond_the_stop_is_not_stopped_in_that_bar():
    bars = [{'time': '09:35', 'open': 22171.45, 'high': 22186.05, 'low': 22153.7, 'close': 22184.5},
            {'time': '09:40', 'open': 22184.15, 'high': 22189.25, 'low': 22127.5, 'close': 22130.35},   # opened over the 22,183 stop, filled 22,151 on the way down
            {'time': '09:45', 'open': 22130.1, 'high': 22133.0, 'low': 22080.1, 'close': 22086.2}]
    sim = svc.simulate_trade(bars, 'SELL', 22151.0, 22105.0, 22183.0, '09:35')
    assert sim['result'] == 'Target' and sim['entry_time'] == '09:40' and sim['exit_time'] == '09:45'
    inside = [bars[0], dict(bars[1], open=22175.0)]                      # opened between entry and stop: ambiguous, stays SL
    sim = svc.simulate_trade(inside + bars[2:], 'SELL', 22151.0, 22105.0, 22183.0, '09:35')
    assert sim['result'] == 'SL'


# ── 4 Aug 2025: CPRs and the upper box in one zone — a CPR-line entry inside it is refused ──

def test_4_aug_2025_cross_candle_inside_the_one_zone_is_refused():
    from trading_app.service.cpr_trade_rule import propose_all, one_zone
    prev = (24784.15, 24535.05, 24565.35)          # 1 Aug 2025: CPR 24,597-24,660, R1 24,721 / PDH 24,784
    bars = [
        {'time': '09:15', 'open': 24596.05, 'high': 24642.9, 'low': 24562.85, 'close': 24639.75},   # 80 pts, closed inside the CPR
        {'time': '09:25', 'open': 24647.35, 'high': 24666.4, 'low': 24640.65, 'close': 24660.5},    # closed over TC
        {'time': '09:30', 'open': 24659.65, 'high': 24669.95, 'low': 24641.75, 'close': 24656.55},  # the cross candle
    ]
    chart = _chart(bars[0], prev, (24900.0, 24560.0, 24700.0))
    chart['weekly'] = {'pp': 24685.63, 'bc': 24625.49, 'tc': 24745.78}                         # merged with the CPR and the box
    assert one_zone(chart, bars) is not None
    inside = [{'time': f'{h:02d}:{m:02d}', 'open': 24700.0, 'high': 24705.0, 'low': 24695.0, 'close': 24700.0}
              for h in range(10, 16) for m in range(0, 60, 5) if (h, m) < (15, 30)]           # the day stays in the zone
    out = propose_all(chart, bars + inside)
    assert out[0][0] is None and 'interlink into one zone' in out[0][1] and 'only a break out of the zone is traded' in out[0][1]
    # ... and a close over the zone is the day's trade
    brk = [{'time': '13:05', 'open': 24770.0, 'high': 24800.0, 'low': 24765.0, 'close': 24795.0}]
    out = propose_all(chart, bars + inside[:33] + brk + inside[34:])
    assert out[0][0]['trade'] == 'BUY' and out[0][0]['entry'] == 24801.0 and 'closed over the zone' in out[0][1]


def test_an_entry_outside_the_one_zone_stands():
    """3 Mar 2025: the same shape, but the cross-candle SELL at 22,151 sits under the whole zone."""
    from trading_app.service.cpr_trade_rule import one_zone
    prev = (22450.35, 22104.85, 22125.7)
    c1 = {'time': '09:15', 'open': 22194.55, 'high': 22261.55, 'low': 22194.2, 'close': 22253.75}
    bars = [c1,
            {'time': '09:25', 'open': 22186.65, 'high': 22199.1, 'low': 22154.85, 'close': 22174.55},
            {'time': '09:30', 'open': 22173.2, 'high': 22180.55, 'low': 22152.5, 'close': 22170.1}]
    chart = _chart(c1, prev, (22610.0, 22120.0, 22330.0))
    chart['weekly'] = {'pp': 22299.2, 'bc': 22211.95, 'tc': 22386.45}
    assert one_zone(chart, bars) is not None
    p, why, _ = propose(chart, bars + _session()[-25:])
    assert p and p['entry'] == 22151.0



def test_6_mar_2025_the_pdh_rejection_inside_the_zone_is_refused_and_the_r1_break_bought():
    from trading_app.service.cpr_trade_rule import propose_all
    prev = (22394.9, 22067.8, 22337.3)             # 5 Mar 2025: CPR 22,231-22,302, PDH 22,395 / R1 22,466
    bars = [
        {'time': '09:15', 'open': 22476.35, 'high': 22491.3, 'low': 22417.45, 'close': 22432.75},
        {'time': '09:20', 'open': 22433.1, 'high': 22446.45, 'low': 22357.7, 'close': 22366.45},   # strong red off PDH: inside the zone
    ] + [{'time': f'{h:02d}:{m:02d}', 'open': 22370.0, 'high': 22380.0, 'low': 22360.0, 'close': 22370.0}
         for h in range(9, 14) for m in range(0, 60, 5) if (9, 25) <= (h, m) < (13, 5)] + [
        {'time': '13:05', 'open': 22464.4, 'high': 22477.3, 'low': 22457.5, 'close': 22476.8},     # closed over R1: the break
        {'time': '13:10', 'open': 22478.4, 'high': 22540.0, 'low': 22463.1, 'close': 22530.0},
    ]
    chart = _chart(bars[0], prev, (22633.0, 22050.0, 22300.0))
    chart['weekly'] = {'pp': 22299.2, 'bc': 22211.95, 'tc': 22386.45}
    out = propose_all(chart, bars)
    p, why = out[0][0], out[0][1]
    assert p == {'trade': 'BUY', 'entry': 22479.0, 'target': 22525.0, 'sl': 22456.0} and '13:05 candle closed over the zone' in why


# ── 18 Jun 2025: the 09:15 candle rejects the lower box, target Cam R3 at the CPR ──

def test_18_jun_2025_buys_over_the_0915_box_rejection_with_cam_r3_as_target():
    from trading_app.service.cpr_trade_rule import propose_all
    prev = (24982.05, 24813.7, 24853.4)            # 17 Jun 2025: CPR 24,868-24,898, S1 24,784 / PDL 24,814, Cam S3 24,807, Cam R3 24,900
    lv = svc.levels(*prev)
    c1 = {'time': '09:15', 'open': 24788.35, 'high': 24837.8, 'low': 24776.9, 'close': 24830.35}
    bars = [c1, {'time': '09:20', 'open': 24831.45, 'high': 24882.25, 'low': 24829.8, 'close': 24879.75},
                {'time': '09:25', 'open': 24880.25, 'high': 24900.3, 'low': 24872.6, 'close': 24897.2}]
    chart = _chart(c1, prev, (24900.0, 24700.0, 24805.0))
    chart['weekly'] = {'pp': 24804.67, 'bc': 24761.63, 'tc': 24847.7}            # merged with the CPR and over the lower box: one zone
    (p, why, i, sim), = propose_all(chart, bars + _session()[-25:])
    assert p['trade'] == 'BUY' and p['entry'] == 24839.0 and p['sl'] == math.floor(24776.9 - 1.5) and p['target'] == round(lv['cr3'])
    assert why.startswith('09:15 strong green candle opened') and 'touched PDL and S1 and Cam S3' in why and 'Cam R3 sits at the CPR' in why
    assert sim['result'] == 'Target' and sim['entry_time'] == '09:20' and sim['exit_time'] == '09:25'


def test_a_0915_that_does_not_close_over_the_box_is_not_the_rejection():
    prev = (24982.05, 24813.7, 24853.4)
    c1 = {'time': '09:15', 'open': 24788.35, 'high': 24812.0, 'low': 24776.9, 'close': 24810.0}   # closed inside the box
    chart = _chart(c1, prev, (24900.0, 24700.0, 24805.0))
    chart['weekly'] = {'pp': 24804.67, 'bc': 24761.63, 'tc': 24847.7}
    p, why, _ = propose(chart, _session(c1))
    assert not (p and why.startswith('09:15 strong green candle opened'))


# ── 20 Jan 2025: a big 09:15 down through the upper box, the small 09:20 sold, then the reversal bought ──

def test_20_jan_2025_sells_the_small_0920_and_buys_the_reversal_to_the_virgin_cpr():
    from trading_app.service.cpr_trade_rule import propose_all
    prev = (23292.1, 23100.35, 23203.2)            # 17 Jan 2025: CPR 23,196-23,201, PDH 23,292 / R1 23,297
    bars = [
        {'time': '09:15', 'open': 23290.4, 'high': 23308.35, 'low': 23219.35, 'close': 23227.6},   # 89 pts down through the box, clear of the CPRs
        {'time': '09:20', 'open': 23226.95, 'high': 23231.0, 'low': 23210.85, 'close': 23214.25},  # small red under the box
        {'time': '09:25', 'open': 23211.1, 'high': 23220.75, 'low': 23189.7, 'close': 23209.25},   # fills the SELL
        {'time': '09:30', 'open': 23208.9, 'high': 23228.55, 'low': 23185.95, 'close': 23186.7},
        {'time': '09:55', 'open': 23205.3, 'high': 23236.1, 'low': 23205.3, 'close': 23226.65},    # strong green: stops the SELL — the reversal
        {'time': '10:05', 'open': 23223.65, 'high': 23240.0, 'low': 23211.45, 'close': 23214.65},  # fills the BUY
        {'time': '11:15', 'open': 23300.0, 'high': 23330.0, 'low': 23295.0, 'close': 23325.0},     # the virgin CPR
    ]
    chart = _chart(bars[0], prev, (23400.0, 23050.0, 23210.0))
    chart['weekly'] = {'pp': 23214.03, 'bc': 23208.62, 'tc': 23219.45}
    chart['virgin_cprs'] = [{'date': '2025-01-17', 'bc': 23318.48, 'tc': 23331.85}]
    out = propose_all(chart, bars + _session()[-25:])
    assert len(out) == 2
    (p1, why1, _, sim1), (p2, why2, i2, sim2) = out
    assert p1 == {'trade': 'SELL', 'entry': 23209.0, 'target': 23161.0, 'sl': 23233.0} and 'PDH/R1' in why1 and sim1['result'] == 'SL'
    assert p2 == {'trade': 'BUY', 'entry': 23238.0, 'target': 23318.0, 'sl': 23203.0} and bars[i2]['time'] == '09:55'
    assert 'the reversal' in why2 and 'virgin CPR 23,318-23,332' in why2 and sim2['result'] == 'Target'


def test_a_big_0915_that_ran_into_the_cprs_is_not_the_upper_box_break():
    """6 Apr 2026: opened at PDH and closed 116 pts under the box, deep in the CPRs — the day is the retest/small-rejection shape, not the 09:20 sell."""
    prev = (22782.3, 22182.55, 22713.1)
    c1 = {'time': '09:15', 'open': 22780.3, 'high': 22798.25, 'low': 22591.7, 'close': 22666.05}
    c2 = {'time': '09:20', 'open': 22671.3, 'high': 22714.35, 'low': 22646.7, 'close': 22653.55}
    chart = _chart(c1, prev, (22733.55, 22491.1, 22612.7))
    chart['weekly'] = {'pp': 22612.32, 'bc': 22561.92, 'tc': 22662.71}
    p, why, _ = propose(chart, _session(c1, c2))
    assert not (p and p['trade'] == 'SELL' and 'PDH/R1' in why and 'too big to enter under' in why)
