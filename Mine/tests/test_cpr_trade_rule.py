"""cpr_trade_rule.propose — the 09:15 break with a long wick, confirmed by 09:20.

7 Sept 2026 is the worked example: Medium CPR, medium box, the 09:15
candle opened between PDL and S1 and closed under S1 with a 40-pt lower
wick; the 09:20 candle was a small red one inside that wick, so the entry
is under it, SL over the 09:15 high, target 1:2.
"""

import math

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
    assert first[0]['trade'] == 'SELL' and first[3]['result'] == 'SL' and first[3]['exit_time'] == '11:05'
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


def test_the_second_trade_only_follows_a_stopped_or_unfilled_first():
    chart = _chart_01()
    bars = _bars_01()
    # Let the 09:15 trade reach its 23,858 target before the rejection: no second trade.
    bars[5] = dict(bars[5], low=23850.0, close=23860.0)
    trades = propose_all(chart, bars)
    assert trades[0][3]['result'] == 'Target' and len(trades) == 1
