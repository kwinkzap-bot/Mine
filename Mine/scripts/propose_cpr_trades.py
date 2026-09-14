"""Fill the trade columns of the CPR sheet by rule, for sessions that have none.

The rule is the one the hand-traded January rows follow, as read back
through the Trend page's reasons: trade the REJECTION, not the break.

    Zone       the daily CPR and the weekly (1-hour chart) CPR, read as one
               band when they overlap or nearly touch (cprs_merged); the
               daily one alone otherwise. Where the 09:15 close sits
               against it decides which setups are looked for.

    Open BELOW the CPR
      SELL     a strong red candle that pokes up into the CPR and closes
               back below it. Entry a buffer under that candle's low; SL a
               buffer over the first pivot above the candle's high (the
               candle's high itself when it already cleared the pivots).
      BUY      a candle that dips to PDL or S1 and closes back above it —
               strong, or closing on its high after the dip (16 Feb 2026).
               Entry 1 pt over its high; SL 0.02% under the lowest level it
               rejected. Target: the CPR — or 1:2 when the CPR is nearer
               than the risk ("CPR is very close to the entry, so 1:2").
    Open ABOVE the CPR — the mirror: BUY the rejection from the CPR,
               SELL the rejection from PDH / R1.
    INSIDE the CPR at the 09:15 close: the first strong candle closing out
               of it is the entry — over its high (under its low), SL at the
               09:15 candle's other end, target the nearer of PDH/R1
               (PDL/S1). 17 Feb 2026: opened inside the weekly CPR above
               the daily one, 09:15 closed inside the daily CPR, 09:40
               closed above it -> BUY 25,647, SL 25,591, target PDH 25,697.
    BETWEEN the two CPRs (not merged; above the weekly one, under the
               daily one): the daily CPR is the lid — BUY only on a strong
               close above it, entry over that candle's high, SL under the
               CPR. Nothing else counts (4 Feb 2026: never closed above
               25,815-25,991 -> no trade). Mirror when the daily CPR is the
               lower one: SELL on a close below it.
    NARROW CPR (under 0.13% of price — "Narrow", "Small box") with the
               open clear of both CPRs: a trend day is expected. If the
               09:15 candle rejects the other side (wick >= half its range,
               close at the far end), the first strong candle in the trend's
               direction is the entry: 1 pt over its high (under its low),
               stop 0.025% under its low, target 1:2 (10 Feb 2026: 09:30
               bull candle -> BUY 25,945, SL 25,903, target 26,029; SL).
               While PDH/R1 still sits above the 09:15 close, that is the
               wall: the strong candle must close through it, and a candle
               turned back from it (bigger wick on that side, close under
               it) is instead a SELL under its low, SL over the high so
               far, target the first CPR line (11 Feb 2026: 09:20 candle
               rejected from R1 -> SELL 25,974, SL 26,011, target 25,933).
               When the 09:15 candle is itself the strong break through
               PDL/S1 (PDH/R1), that is the entry, on whichever side of
               the CPRs price sits: under its low — or under a level lying
               just beneath it — SL over its high, target 1:2 (12 Feb 2026:
               SELL 25,844 under S2, SL 25,909; squared off at 15:15).
    GAP open  past both R1 and PDH (or both S1 and PDL): wait for price to
               come back to that R1/PDH zone. A strong close back above it
               is the reversal -> BUY; a close under it is the breakdown ->
               SELL; never reached -> no trade (3 Feb 2026: open 26,308,
               R1/PDH 25,108-25,238 never seen). Gap-down is the mirror.

    Target     the next level in the trade's direction (PDL/S1/S2/S3/Cam S3
               below, PDH/R1/R2/R3/Cam R3 above). If that is further than
               FAR_TARGET_X times the risk, a 1:1 target is used instead —
               "245 points is very long, keep 1:1".
    Window     the first qualifying candle up to SETUP_UNTIL; the entry must
               be traded by a later bar. Result is the session's bars:
               target or SL, whichever a later bar reaches first; neither
               by 15:15 -> squared off there ("EOD", P&L written in M).

2 Feb 2026 is the worked example: open 24,796 under the merged CPR
(24,886-25,015); the 09:55 candle spikes to 24,939 and closes 24,863 —
SELL under it at 24,845, SL over the pivot at 24,963, Cam S3 at 24,586 is
259 points away so the target is 1:1 at 24,727.

Rows are written only where H (Trade Type) is empty — or, with
--replace-rule-trades, also where column O says the trade came from this
script — so hand-entered trades are never touched.

    python scripts/propose_cpr_trades.py ~/Downloads/"Bactest Nifty CPR.xlsx" \\
        --sheet 2026 --from 2026-02-01 --to 2026-02-28 [--replace-rule-trades]
"""

import argparse
import math
import os
import sys
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.styles import PatternFill

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.service import cpr_backtest_service as svc  # noqa: E402

BUFFER_PCT = 0.05        # entry / SL sit this far beyond the candle or pivot (~12 pts)
TOUCH_PCT = 0.05         # a candle within this of a level has touched it
FAR_TARGET_X = 1.5       # a level further than this x the risk -> 1:1 target instead
NEAR_TARGET_X = 0.6      # a level nearer than this x the risk -> 1:2 target instead
STRONG_BODY = 0.6        # the rejection candle must close with a body >= 60% of its range
GAP_MIN_PCT = 0.25       # an open this far past R1/PDH (S1/PDL) is a gap day, not an "above" day
NARROW_PCT = 0.13        # CPR under this % of price is Narrow on the sheet's scale -> a trend day is expected
WICK_MIN = 0.5           # the 09:15 candle's rejection wick must be at least half its range
TREND_SL_PCT = 0.025     # trend-day stop sits this close under the breakout candle (~6 pts)
TREND_RR = 2.0           # trend-day target is 1:2
SETUP_UNTIL = '11:00'    # last bar that may be the rejection candle
RULE_TAG = 'Trade by rule'
FILL = PatternFill('solid', fgColor='DDEBF7')   # light blue: trade written by rule


def bars_for(symbol: str, first: date, last: date):
    from trading_app.service.provider_logic import _get_fyers_adapter
    from trading_app.service import multichart_service as mc
    adapter = _get_fyers_adapter('Mine')
    if adapter is None:
        sys.exit('Fyers is not configured for user Mine (env/Mine.env)')
    fy = mc.resolve_symbol(symbol)
    daily = svc._daily_rows(adapter.historical_data(
        fy, (first - timedelta(days=120)).isoformat(), last.isoformat(), 'day', use_cache=False))
    intraday = svc._intraday_by_session(adapter.historical_data(
        fy, (first - timedelta(days=45)).isoformat(), last.isoformat(), '5minute', use_cache=False))
    return daily, intraday


def propose(chart, bars):
    """(trade, why, setup-bar index) from a session's bars, or (None, why, None)."""
    lv, w = chart['levels'], chart.get('weekly')
    named = svc._reason_ladder(lv, w, chart['first_candle'], chart['day']['open'])
    if w and svc.cprs_merged(named):
        band_lo, band_hi = min(lv['bc'], w['bc']), max(lv['tc'], w['tc'])
        pivots = sorted({lv['pp'], w['pp']})
    else:
        band_lo, band_hi, pivots = lv['bc'], lv['tc'], [lv['pp']]

    # The stop hides behind the pivot beyond the rejection candle. Two
    # pivots within 0.1% are one zone, so the stop goes past the outer one
    # (2 Feb: P 24,946 and WP 24,952 -> SL 24,964); further apart, only the
    # first pivot beyond the candle counts, not the far one.
    px0 = bars[0]['close']
    one_zone = len(pivots) > 1 and pivots[-1] - pivots[0] <= px0 * 0.1 / 100

    def pivot_above(y):
        beyond = [p for p in pivots if p >= y]
        return (max(beyond) if one_zone else min(beyond)) if beyond else y

    def pivot_below(y):
        beyond = [p for p in pivots if p <= y]
        return (min(beyond) if one_zone else max(beyond)) if beyond else y
    c1 = bars[0]
    px = c1['close']
    buf, tol = px * BUFFER_PCT / 100, px * TOUCH_PCT / 100
    narrow = chart['width_pct'] < NARROW_PCT

    # Narrow CPR and the 09:15 candle itself is the strong break through
    # PDL/S1 (or PDH/R1): the move has started — trade it off that candle,
    # whichever side of the CPRs price is on (12 Feb 2026).
    rng1 = c1['high'] - c1['low']
    if narrow and rng1 > 0 and abs(c1['close'] - c1['open']) / rng1 >= STRONG_BODY:
        lo_wall, hi_wall = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1'])), (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1']))
        if c1['close'] < c1['open'] and c1['open'] >= lo_wall[0] - tol and c1['close'] < lo_wall[0]:
            step = [(n, y) for n, y in named if c1['low'] - px * TOUCH_PCT / 100 <= y < c1['low']]
            entry = math.floor(min(step, key=lambda t: t[1])[1]) if step else math.floor(c1['low'] - 1)
            sl = math.ceil(c1['high'] + 1.5)
            target = entry - TREND_RR * (sl - entry)
            why = (f"narrow CPR ({chart['box_pts']} pts), small box — big move expected; 09:15 strong red candle touched "
                   f"PDL/S1 {lo_wall[0]:,.0f}-{lo_wall[1]:,.0f} and closed below it at {c1['close']:,.0f} -> SELL under it"
                   + (f" (under {step[0][0]} {step[0][1]:,.0f})" if step else '') + "; SL over its high; target 1:2")
            return ({'trade': 'SELL', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0)
        if c1['close'] > c1['open'] and c1['open'] <= hi_wall[1] + tol and c1['close'] > hi_wall[1]:
            step = [(n, y) for n, y in named if c1['high'] < y <= c1['high'] + px * TOUCH_PCT / 100]
            entry = math.ceil(max(step, key=lambda t: t[1])[1]) if step else math.ceil(c1['high'] + 1)
            sl = math.floor(c1['low'] - 1.5)
            target = entry + TREND_RR * (entry - sl)
            why = (f"narrow CPR ({chart['box_pts']} pts), small box — big move expected; 09:15 strong green candle touched "
                   f"PDH/R1 {hi_wall[0]:,.0f}-{hi_wall[1]:,.0f} and closed above it at {c1['close']:,.0f} -> BUY over it"
                   + (f" (over {step[0][0]} {step[0][1]:,.0f})" if step else '') + "; SL under its low; target 1:2")
            return ({'trade': 'BUY', 'entry': float(entry), 'target': float(round(target)), 'sl': float(sl)}, why, 0)

    # A gap beyond the first pivot pair is its own case: the open sits past
    # R1 AND PDH (or past S1 AND PDL), so the CPR is out of reach and the
    # R1/PDH zone (S1/PDL below) is what price has to come back to first.
    gap_hi, gap_lo = max(lv['r1'], lv['pdh']), min(lv['s1'], lv['pdl'])
    gap_min = c1['open'] * GAP_MIN_PCT / 100
    if c1['open'] > gap_hi + gap_min:
        side_open, zone = 'gap-up', (min(lv['r1'], lv['pdh']), gap_hi)
    elif c1['open'] < gap_lo - gap_min:
        side_open, zone = 'gap-down', (gap_lo, max(lv['s1'], lv['pdl']))
    elif w and not svc.cprs_merged(named) and w['tc'] < c1['close'] < lv['bc']:
        # Above the weekly CPR but under the daily one: the daily CPR is the
        # lid, and only a strong close above it is an entry (BUY).
        side_open = 'between-up'
    elif w and not svc.cprs_merged(named) and lv['tc'] < c1['close'] < w['bc']:
        side_open = 'between-down'
    elif lv['bc'] <= c1['close'] <= lv['tc']:
        side_open = 'inside'                 # inside the DAILY CPR: its own lines are the trigger
    elif c1['close'] < band_lo:
        side_open = 'below'
    elif c1['close'] > band_hi:
        side_open = 'above'
    else:
        side_open = 'inside'

    supports = [(n, lv[k]) for k, n in (('pdl', 'PDL'), ('s1', 'S1'), ('cs3', 'Cam S3'), ('s2', 'S2'), ('s3', 'S3'))]
    resistances = [(n, lv[k]) for k, n in (('pdh', 'PDH'), ('r1', 'R1'), ('cr3', 'Cam R3'), ('r2', 'R2'), ('r3', 'R3'))]

    def next_level(entry, is_buy, pool):
        ahead = [(n, y) for n, y in pool
                 if (y >= entry + 10 if is_buy else y <= entry - 10)]
        if not ahead:
            return None
        return min(ahead, key=lambda t: t[1]) if is_buy else max(ahead, key=lambda t: t[1])

    def build(side, entry, sl, level, why):
        """The trade. Its natural target is the next level — unless that
        level is nearer than the risk (then 1:2: "the CPR is very close
        to the entry, so 1:2") or further than FAR_TARGET_X x the risk
        (then 1:1: "245 points is very long, keep 1:1")."""
        risk = abs(entry - sl)
        is_buy = side == 'BUY'
        reward = ((level[1] - entry) if is_buy else (entry - level[1])) if level else None
        if reward is not None and reward < NEAR_TARGET_X * risk:
            target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
            twhy = f'1:{TREND_RR:g} ({level[0]} {level[1]:,.0f} is only {reward:.0f} pts away, inside the {risk:.0f}-pt risk)'
        elif reward is not None and reward <= FAR_TARGET_X * risk:
            target, twhy = level[1], f'{level[0]} {level[1]:,.0f}'
        else:
            target = entry + risk if is_buy else entry - risk
            twhy = '1:1' + (f' ({level[0]} {level[1]:,.0f} is {abs(level[1] - entry):.0f} pts away)' if level else '')
        return ({'trade': side, 'entry': float(round(entry)), 'target': float(round(target)),
                 'sl': float(round(sl))}, f'{why}; target {twhy}')

    # Narrow CPR with the open clear of both CPRs: a trend day is expected.
    # If the 09:15 candle rejects the other side (a long wick, close at the
    # far end), the first strong candle in the trend's direction is the
    # entry — over its high (under its low), stop tight, target 1:2.
    if narrow and side_open in ('above', 'below') and w:
        clear = c1['close'] > max(band_hi, w['tc']) if side_open == 'above' else c1['close'] < min(band_lo, w['bc'])
        rng1 = c1['high'] - c1['low']
        lower_wick = min(c1['open'], c1['close']) - c1['low']
        upper_wick = c1['high'] - max(c1['open'], c1['close'])
        up_reject = rng1 > 0 and lower_wick / rng1 >= WICK_MIN and (c1['close'] - c1['low']) / rng1 >= 0.6
        dn_reject = rng1 > 0 and upper_wick / rng1 >= WICK_MIN and (c1['high'] - c1['close']) / rng1 >= 0.6
        if clear and ((side_open == 'above' and up_reject) or (side_open == 'below' and dn_reject)):
            is_buy = side_open == 'above'
            wick = f"{lower_wick:.0f}" if is_buy else f"{upper_wick:.0f}"
            head = (f"narrow CPR ({chart['box_pts']} pts), open {'above' if is_buy else 'below'} both CPRs — trend day expected; "
                    f"09:15 candle rejected the {'down' if is_buy else 'up'}side (wick {wick} pts)")
            # The first pivot pair still ahead of the 09:15 close is the wall
            # the trend has to clear — PDH/R1 above, PDL/S1 below.
            if is_buy:
                wall = (min(lv['pdh'], lv['r1']), max(lv['pdh'], lv['r1'])) if c1['close'] < min(lv['pdh'], lv['r1']) else None
            else:
                wall = (min(lv['pdl'], lv['s1']), max(lv['pdl'], lv['s1'])) if c1['close'] > max(lv['pdl'], lv['s1']) else None
            extreme = c1['high'] if is_buy else c1['low']
            for i, b in enumerate(bars[1:], start=1):
                if b['time'] > SETUP_UNTIL:
                    break
                rng = b['high'] - b['low']
                up_w, dn_w = b['high'] - max(b['open'], b['close']), min(b['open'], b['close']) - b['low']
                # A candle that reaches the wall and is turned back — the
                # bigger wick on the wall's side, close short of it — is a
                # rejection: fade it back to the CPR (11 Feb 2026).
                if wall and rng > 0:
                    if is_buy and b['high'] >= wall[0] - tol and b['close'] < wall[0] and up_w > dn_w:
                        entry = math.floor(b['low'] - 1)
                        sl = math.ceil(max(extreme, wall[1]) + 1.5)
                        line = svc.first_cpr_line(entry, False, named)
                        target = round(line[1]) if line else entry - (sl - entry)
                        why = (f"{head}, still under PDH/R1 {wall[0]:,.0f}-{wall[1]:,.0f}; {b['time']} candle rejected from it "
                               f"(high {b['high']:,.0f}, top wick, closed {b['close']:,.0f}) -> SELL under it; SL over the high; "
                               f"target {'the CPR ' + line[0] if line else '1:1'}")
                        return ({'trade': 'SELL', 'entry': float(entry), 'target': float(target), 'sl': float(sl)}, why, i)
                    if not is_buy and b['low'] <= wall[1] + tol and b['close'] > wall[1] and dn_w > up_w:
                        entry = math.ceil(b['high'] + 1)
                        sl = math.floor(min(extreme, wall[0]) - 1.5)
                        line = svc.first_cpr_line(entry, True, named)
                        target = round(line[1]) if line else entry + (entry - sl)
                        why = (f"{head}, still over PDL/S1 {wall[0]:,.0f}-{wall[1]:,.0f}; {b['time']} candle rejected from it "
                               f"(low {b['low']:,.0f}, bottom wick, closed {b['close']:,.0f}) -> BUY over it; SL under the low; "
                               f"target {'the CPR ' + line[0] if line else '1:1'}")
                        return ({'trade': 'BUY', 'entry': float(entry), 'target': float(target), 'sl': float(sl)}, why, i)
                extreme = max(extreme, b['high']) if is_buy else min(extreme, b['low'])
                strong = rng > 0 and abs(b['close'] - b['open']) / rng >= STRONG_BODY
                if not strong or (b['close'] > b['open']) != is_buy:
                    continue
                if wall and ((is_buy and b['close'] <= wall[1]) or (not is_buy and b['close'] >= wall[0])):
                    continue                     # strong, but has not cleared the wall
                tight = px * TREND_SL_PCT / 100
                entry = math.ceil(b['high'] + 1) if is_buy else math.floor(b['low'] - 1)
                sl = math.floor(b['low'] - tight) if is_buy else math.ceil(b['high'] + tight)
                risk = abs(entry - sl)
                target = entry + TREND_RR * risk if is_buy else entry - TREND_RR * risk
                why = (f"{head}; {b['time']} strong {'bull' if is_buy else 'bear'} candle"
                       + (f" through PDH/R1 {wall[0]:,.0f}-{wall[1]:,.0f}" if wall and is_buy else f" through PDL/S1 {wall[0]:,.0f}-{wall[1]:,.0f}" if wall else '')
                       + f" -> {'BUY over' if is_buy else 'SELL under'} it; SL {'under' if is_buy else 'over'} it; target 1:{TREND_RR:g}")
                return ({'trade': 'BUY' if is_buy else 'SELL', 'entry': float(entry),
                         'target': float(round(target)), 'sl': float(sl)}, why, i)
            return None, (f"{head} — no strong {'bull' if is_buy else 'bear'} candle"
                          + (" through PDH/R1" if wall and is_buy else " through PDL/S1" if wall else '')
                          + f" and no rejection from it by {SETUP_UNTIL} — no trade"), None

    reached = False
    for i, b in enumerate(bars):
        if b['time'] > SETUP_UNTIL:
            break
        rng = b['high'] - b['low']
        strong = rng > 0 and abs(b['close'] - b['open']) / rng >= STRONG_BODY
        # A candle closing at its own extreme after a long wick the other way
        # is a reversal candle even with a modest body (16 Feb 2026: 49%
        # body, closed on its high after dipping under PDL and S1).
        at_top = rng > 0 and (b['close'] - b['low']) / rng >= 0.85
        at_bottom = rng > 0 and (b['high'] - b['close']) / rng >= 0.85
        red = b['close'] < b['open'] and (strong or at_bottom)
        green = b['close'] > b['open'] and (strong or at_top)
        if side_open == 'gap-up':
            # Wait for price to come down to R1/PDH: a strong close back
            # above the zone is the reversal (BUY), a close under it the
            # breakdown (SELL). The 09:15 candle itself never triggers.
            if i == 0 or b['low'] > zone[1]:
                continue
            reached = True
            if green and b['close'] > zone[1]:
                entry, sl = b['high'] + buf, min(zone[0], b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"gap-up; {b['time']} candle came to R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f} (low {b['low']:,.0f}) and reversed up, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            elif b['close'] < zone[0]:
                entry, sl = b['low'] - buf, zone[1] + buf
                made = build('SELL', entry, sl, ('CPR', band_hi),
                             f"gap-up; {b['time']} candle broke down through R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f}, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'gap-down':
            if i == 0 or b['high'] < zone[0]:
                continue
            reached = True
            if red and b['close'] < zone[0]:
                entry, sl = b['low'] - buf, max(zone[1], b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"gap-down; {b['time']} candle came up to S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f} (high {b['high']:,.0f}) and reversed down, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            elif b['close'] > zone[1]:
                entry, sl = b['high'] + buf, zone[0] - buf
                made = build('BUY', entry, sl, ('CPR', band_lo),
                             f"gap-down; {b['time']} candle broke up through S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f}, closed {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'inside':
            # 09:15 closed inside the CPR: the first strong close out of it
            # is the entry, stop at the 09:15 candle's other end, target the
            # first pivot pair that way (17 Feb 2026: 09:40 closed above TC
            # -> BUY 25,647, SL 25,591, target PDH 25,697).
            if i == 0:
                continue
            in_lo, in_hi = (lv['bc'], lv['tc']) if lv['bc'] <= c1['close'] <= lv['tc'] else (band_lo, band_hi)
            if strong and green and b['close'] > in_hi:
                entry, sl = math.ceil(b['high'] + 1), math.floor(c1['low'])
                made = build('BUY', entry, sl, next_level(entry, True, resistances[:2]) or next_level(entry, True, resistances),
                             f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed above it at {b['close']:,.0f} -> BUY over it; SL at the 09:15 low")
                if made:
                    return (*made, i)
            if strong and red and b['close'] < in_lo:
                entry, sl = math.floor(b['low'] - 1), math.ceil(c1['high'])
                made = build('SELL', entry, sl, next_level(entry, False, supports[:2]) or next_level(entry, False, supports),
                             f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}; {b['time']} candle closed below it at {b['close']:,.0f} -> SELL under it; SL at the 09:15 high")
                if made:
                    return (*made, i)
            continue
        if side_open == 'between-up':
            if green and b['close'] > lv['tc']:
                entry, sl = b['high'] + buf, min(lv['bc'], b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"above the weekly CPR, under the daily one; {b['time']} candle closed above the daily CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} at {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'between-down':
            if red and b['close'] < lv['bc']:
                entry, sl = b['low'] - buf, max(lv['tc'], b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"below the weekly CPR, above the daily one; {b['time']} candle closed below the daily CPR {lv['bc']:,.0f}-{lv['tc']:,.0f} at {b['close']:,.0f}")
                if made:
                    return (*made, i)
            continue
        if side_open == 'below':
            # SELL: rejection from the CPR
            if strong and red and b['high'] >= band_lo and b['close'] < band_lo:   # a full-bodied rejection, not just a close near the low
                entry = b['low'] - buf
                sl = pivot_above(b['high']) + buf
                made = build('SELL', entry, sl, next_level(entry, False, supports),
                             f"{b['time']} candle rejected from the CPR (high {b['high']:,.0f} into {band_lo:,.0f}-{band_hi:,.0f}, closed {b['close']:,.0f})")
                if made:
                    return (*made, i)
            # BUY: rejection from PDL / S1 — entry over the candle, stop just
            # under the lowest level it rejected (not under its whole wick).
            touched = [(n, y) for n, y in (('PDL', lv['pdl']), ('S1', lv['s1']))
                       if green and b['low'] <= y + tol and b['close'] > y]
            if touched:
                name, y = min(touched, key=lambda t: t[1])
                entry = math.ceil(b['high'] + 1)
                sl = math.floor(y - px * 0.02 / 100)
                made = build('BUY', entry, sl, ('CPR', band_lo),
                             f"{b['time']} candle rejected from {' and '.join(n for n, _ in touched)} "
                             f"(low {b['low']:,.0f}, closed {b['close']:,.0f}) -> BUY over it; SL under {name} {y:,.0f}")
                if made:
                    return (*made, i)
        else:
            if strong and green and b['low'] <= band_hi and b['close'] > band_hi:
                entry = b['high'] + buf
                sl = pivot_below(b['low']) - buf
                made = build('BUY', entry, sl, next_level(entry, True, resistances),
                             f"{b['time']} candle rejected from the CPR (low {b['low']:,.0f} into {band_lo:,.0f}-{band_hi:,.0f}, closed {b['close']:,.0f})")
                if made:
                    return (*made, i)
            touched = [(n, y) for n, y in (('PDH', lv['pdh']), ('R1', lv['r1']))
                       if red and b['high'] >= y - tol and b['close'] < y]
            if touched:
                name, y = max(touched, key=lambda t: t[1])
                entry = math.floor(b['low'] - 1)
                sl = math.ceil(y + px * 0.02 / 100)
                made = build('SELL', entry, sl, ('CPR', band_hi),
                             f"{b['time']} candle rejected from {' and '.join(n for n, _ in touched)} "
                             f"(high {b['high']:,.0f}, closed {b['close']:,.0f}) -> SELL under it; SL over {name} {y:,.0f}")
                if made:
                    return (*made, i)
    if side_open == 'inside':
        in_lo, in_hi = (lv['bc'], lv['tc']) if lv['bc'] <= c1['close'] <= lv['tc'] else (band_lo, band_hi)
        return None, (f"09:15 closed inside the CPR {in_lo:,.0f}-{in_hi:,.0f}: entry only on a strong close out of it — "
                      f"none by {SETUP_UNTIL} — no trade"), None
    if side_open == 'between-up':
        return None, (f"price above the weekly CPR ({w['bc']:,.0f}-{w['tc']:,.0f}) but below the daily CPR "
                      f"({lv['bc']:,.0f}-{lv['tc']:,.0f}): BUY only on a close above the daily CPR — "
                      f"never closed above it by {SETUP_UNTIL} — no trade"), None
    if side_open == 'between-down':
        return None, (f"price below the weekly CPR ({w['bc']:,.0f}-{w['tc']:,.0f}) but above the daily CPR "
                      f"({lv['bc']:,.0f}-{lv['tc']:,.0f}): SELL only on a close below the daily CPR — "
                      f"never closed below it by {SETUP_UNTIL} — no trade"), None
    if side_open == 'gap-up':
        return None, (f"gap-up open {c1['open']:,.0f}; R1/PDH {zone[0]:,.0f}-{zone[1]:,.0f} "
                      + ('reached but neither reversed nor broke by ' if reached else 'never reached by ') + SETUP_UNTIL
                      + ' — no trade'), None
    if side_open == 'gap-down':
        return None, (f"gap-down open {c1['open']:,.0f}; S1/PDL {zone[0]:,.0f}-{zone[1]:,.0f} "
                      + ('reached but neither reversed nor broke by ' if reached else 'never reached by ') + SETUP_UNTIL
                      + ' — no trade'), None
    return None, f'no rejection candle by {SETUP_UNTIL}', None


def _clear_rule_trade(ws, rn):
    """Blank H-L and N so a re-run never leaves half of an older proposal behind;
    M goes back to the sheet's P&L formula if an EOD number replaced it."""
    for col in (8, 9, 10, 11, 12, 14, 16):
        ws.cell(rn, col).value = None
        ws.cell(rn, col).fill = PatternFill(fill_type=None)
    if not str(ws.cell(rn, 13).value or '').startswith('='):
        ws.cell(rn, 13).value = (f'=IF(OR(I{rn}="", J{rn}="", L{rn}=""), "", IF(L{rn}="Target", IF(J{rn}>I{rn}, J{rn}-I{rn}, I{rn}-J{rn}), '
                                 f'IF(AND(L{rn}="SL", K{rn}<>""), IF(J{rn}>I{rn}, K{rn}-I{rn}, I{rn}-K{rn}), "")))')


def _note(ws, rn, text):
    """Column O with every earlier rule note dropped, the chart-analysis note kept."""
    parts = [p for p in str(ws.cell(rn, 15).value or '').split(' | ') if p and RULE_TAG not in p]
    return ' | '.join(parts + [text])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default=None)
    ap.add_argument('--symbol', default='NIFTY')
    ap.add_argument('--from', dest='first', required=True)
    ap.add_argument('--to', dest='last', required=True)
    ap.add_argument('--dry-run', action='store_true', help='print, do not write')
    ap.add_argument('--replace-rule-trades', action='store_true',
                    help='also rewrite rows this script filled earlier (column O says so)')
    ap.add_argument('--replace-hand-trades', action='store_true',
                    help='rewrite hand-entered trades too; the hand trade is kept in column O')
    args = ap.parse_args(argv)
    first, last = date.fromisoformat(args.first), date.fromisoformat(args.last)

    wb = openpyxl.load_workbook(args.xlsx)
    ws = wb[args.sheet] if args.sheet else wb.worksheets[0]
    targets = {}                     # date -> row number of the first untraded row
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        d = row[0].value
        if not isinstance(d, datetime) or not (first <= d.date() <= last):
            continue
        by_rule = RULE_TAG in str(row[14].value or '')
        if row[7].value and not ((args.replace_rule_trades and by_rule) or (args.replace_hand_trades and not by_rule)):
            continue                 # H: hand-entered trade — leave it
        if row[7].value and not by_rule:
            # A hand trade is about to be replaced: keep it in the note.
            hand = f"hand trade was {row[7].value} {row[8].value:.0f} / T {row[9].value:.0f} / SL {row[10].value:.0f} -> {row[11].value}"
            if hand not in str(row[14].value or ''):
                row[14].value = ((row[14].value + ' | ') if row[14].value else '') + hand
        ds = d.date().isoformat()
        if ds in targets and row[7].value:
            # A second hand trade on the same day: the rule writes one trade
            # per session, so this row's trade columns are cleared (kept in O).
            for col in (8, 9, 10, 11, 12, 14):
                row[col - 1].value = None
            continue
        targets.setdefault(ds, row[0].row)
    if not targets:
        print('nothing to do: every session in range already has a trade')
        return
    if not ws.cell(1, 16).value:
        ws.cell(1, 16).value = 'Setup'

    daily, intraday = bars_for(args.symbol, first, last)
    res = svc.analyse([{'date': d} for d in sorted(targets)], daily, intraday)
    written = 0
    for r in res['rows']:
        c = r['chart']
        if not c:
            print(f"{r['date']}: {r.get('error')}")
            continue
        bars = intraday[r['date']]
        p, why, setup_i = propose(c, bars)
        rn = targets[r['date']]
        if not p:
            print(f"{r['date']}: {why} — left untraded")
            if not args.dry_run:
                _clear_rule_trade(ws, rn)
                ws.cell(rn, 15).value = _note(ws, rn, f"{RULE_TAG}: {why}")
            continue
        sim = svc.simulate_trade(bars[setup_i:], p['trade'], p['entry'], p['target'], p['sl'])
        result = sim['result']
        line = (f"{r['date']} {p['trade']} E {p['entry']:.0f} T {p['target']:.0f} SL {p['sl']:.0f} "
                f"-> {result} ({sim['pnl']}) {sim['entry_time'] or ''}{' -> ' + sim['exit_time'] if sim['exit_time'] else ''}")
        print(line)
        if args.dry_run:
            continue
        _clear_rule_trade(ws, rn)
        vals = {} if result == 'No fill' else {
            8: p['trade'], 9: p['entry'], 10: p['target'], 11: p['sl'], 12: result}
        if result == 'EOD':
            vals[13] = sim['pnl']                # no formula for a square-off: the number itself
        if vals:
            vals[14] = why                       # N: the reason, as the hand-written rows carry
            vals[16] = bars[setup_i]['time']     # P: the setup candle, so the grid's replay starts after it
        for col, v in vals.items():
            cell = ws.cell(rn, col)
            cell.value = v
            cell.fill = FILL
        rule = (f"{RULE_TAG}: {why}; chart replay {result}"
                + (f" {sim['entry_time']} -> {sim['exit_time']}" if sim['exit_time'] else '')
                + (f", squared off at {sim['exit_time']}, P&L {sim['pnl']:+.0f}" if result == 'EOD' and sim['pnl'] is not None else ''))
        ws.cell(rn, 15).value = _note(ws, rn, rule)
        written += bool(vals)
    if not args.dry_run:
        wb.save(args.xlsx)
        print(f'wrote {written} trades to {os.path.basename(args.xlsx)}')


if __name__ == '__main__':
    main()
