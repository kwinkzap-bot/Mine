"""CPR manual-vs-chart comparison for the Trend page.

A hand-made study ("Bactest Nifty CPR.xlsx", imported by
scripts/import_cpr_manual.py into Backtest/cpr_manual/<SYMBOL>.json) reads
each session off the 5-minute chart: where the open sits against the daily
and hourly-chart CPRs, whether the CPR is narrow or wide, ascending or
descending, what the first 5-minute candle did, and the trade taken. This
module computes the same readings from the session's own bars so the two
can sit side by side and disagree in the open.

Every rule below is written to the Pine script the chart runs
(`Pine script/Mine CPR`, ported to static/js/components/mine_cpr.js):

* Daily CPR — previous session's H/L/C, "Use daily-based values".
* "Hourly CPR" — the CPR drawn on the 1-hour chart. The script's pivot
  timeframe is AUTO, which is WEEKLY for anything over 15 minutes, so this
  is the previous week's H/L/C, not a 60-minute block.
* Narrow/Medium/Wide — the shared reading in cpr_service (width against the
  instrument's own 10-session average), the same one the OI Profile card
  and the scanners use.
* Boxes — the PDH↔R1 / PDL↔S1 bands. R1 - PDH is (2C - H - L)/3, which is
  exactly TC - BC, so the box is the CPR's own height in points.
* Direction — today's band against yesterday's: Inside when it fits within,
  otherwise Asc/Dec by pivot.

Pure functions over bar lists; the only broker call is in `compare`, which
goes through multichart_service's provider so it sits on Fyers like the
charts do.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trading_app.app.utils.logger import logger
from trading_app.service.cpr_service import classify_cpr_width, cpr_width_pct

MANUAL_DIR = os.path.join(os.path.dirname(__file__), '..', 'Backtest', 'cpr_manual')

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_CLOSE = (15, 30)      # a session's bars are complete from here

WIDTH_HISTORY_SESSIONS = 10   # sessions averaged for the Narrow/Medium/Wide reading
CANDLE_HISTORY_SESSIONS = 20  # sessions averaged for the first-candle Small/Big reading

# Box (= CPR height) as % of the close. Absolute, unlike the CPR-type
# reading: the manual sheet's Small/Medium/Big track the box's size on the
# price scale, and these cut-offs reproduce 17 of its 19 sessions.
BOX_SMALL_MAX_PCT = 0.13
BOX_BIG_MIN_PCT = 0.24

# First 5-minute candle.
DOJI_BODY_MAX = 0.25          # body under a quarter of the range is indecision
STRONG_BODY_MIN = 0.60        # body over 60% of the range is a strong candle
CANDLE_BIG_RATIO = 1.3        # range vs the 20-session average first-candle range
CANDLE_SMALL_RATIO = 0.7
LEVEL_TOUCH_PCT = 0.05        # a level within 0.05% of the candle's H/L counts as touched
LEVEL_NEAR_PCT = 0.25         # ... within 0.25% counts as "near"

# Trade reasons: how close a price has to sit to a level to be "at" it or
# "near" it, as % of the price (~20 / ~75 pts on NIFTY at 25,000).
LEVEL_AT_PCT = 0.08
LEVEL_REASON_NEAR_PCT = 0.3

SQUARE_OFF = '15:15'          # a trade still open is squared off here

# Fyers serves at most 99 days of intraday bars per request.
INTRADAY_CHUNK_DAYS = 90


class NoManualData(Exception):
    """No manual sheet has been imported for this symbol."""


# ── manual rows ───────────────────────────────────────────────────────────

def manual_path(symbol: str) -> str:
    return os.path.join(MANUAL_DIR, f'{symbol.upper()}.json')


def load_manual(symbol: str) -> Dict[str, Any]:
    path = manual_path(symbol)
    if not os.path.exists(path):
        raise NoManualData(f'No manual CPR sheet imported for {symbol.upper()} — '
                           f'run scripts/import_cpr_manual.py')
    with open(path) as fh:
        return json.load(fh)


# ── pivot maths ───────────────────────────────────────────────────────────

def levels(h: float, l: float, c: float) -> Dict[str, float]:
    """Floor pivots + CPR + Camarilla R3/S3, as mine_cpr.js pivotLevels."""
    pp = (h + l + c) / 3
    bc_raw = (h + l) / 2
    tc_raw = 2 * pp - bc_raw
    rng = h - l
    return {
        'pp': pp, 'bc': min(bc_raw, tc_raw), 'tc': max(bc_raw, tc_raw),
        'r1': 2 * pp - l, 's1': 2 * pp - h,
        'r2': pp + rng, 's2': pp - rng,
        'r3': 2 * pp + (h - 2 * l), 's3': 2 * pp - (2 * h - l),
        'pdh': h, 'pdl': l,
        'cr3': c + rng * 1.1 / 4, 'cs3': c - rng * 1.1 / 4,
    }


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(v, nd)


# ── per-session readings ──────────────────────────────────────────────────

def _week_key(d: date) -> tuple:
    iso = d.isocalendar()
    return (iso[0], iso[1])


def weekly_bars(daily: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, float]]:
    """ISO week -> that week's H/L/C, from daily bars (each has a date)."""
    weeks: Dict[tuple, Dict[str, float]] = {}
    for b in daily:
        k = _week_key(b['date'])
        w = weeks.get(k)
        if w is None:
            weeks[k] = {'high': b['high'], 'low': b['low'], 'close': b['close']}
        else:
            w['high'] = max(w['high'], b['high'])
            w['low'] = min(w['low'], b['low'])
            w['close'] = b['close']
    return weeks


def side_of(price: float, lv: Dict[str, float]) -> Dict[str, Any]:
    """Above/Below the pivot, plus whether the price is inside the band —
    the sheet only says Above or Below, so a close inside the CPR is
    reported by which half of it the close sits in."""
    return {
        'side': 'Above' if price > lv['pp'] else 'Below',
        'in_cpr': lv['bc'] <= price <= lv['tc'],
    }


def cpr_direction(today: Dict[str, float], yday: Dict[str, float]) -> str:
    if today['bc'] >= yday['bc'] and today['tc'] <= yday['tc']:
        return 'Inside'
    return 'Asc' if today['pp'] > yday['pp'] else 'Dec'


def box_size(width_pct: float) -> str:
    if width_pct < BOX_SMALL_MAX_PCT:
        return 'Small'
    if width_pct >= BOX_BIG_MIN_PCT:
        return 'Big'
    return 'Medium'


_LEVEL_NAMES = [('pdh', 'PDH'), ('pdl', 'PDL'), ('r1', 'R1'), ('s1', 'S1'),
                ('r2', 'R2'), ('s2', 'S2'), ('r3', 'R3'), ('s3', 'S3'),
                ('cr3', 'Cam R3'), ('cs3', 'Cam S3'),
                ('tc', 'TC'), ('pp', 'P'), ('bc', 'BC')]


def describe_first_candle(c: Dict[str, float], lv: Dict[str, float],
                          avg_range: Optional[float]) -> Dict[str, Any]:
    """Colour, body, size and the pivot levels the 09:15 candle touched."""
    o, h, l, cl = c['open'], c['high'], c['low'], c['close']
    rng = h - l
    body = abs(cl - o)
    body_ratio = (body / rng) if rng else 0.0
    if body_ratio < DOJI_BODY_MAX:
        colour = 'Doji'
    else:
        colour = 'Green' if cl > o else 'Red'
    strength = 'Strong' if body_ratio >= STRONG_BODY_MIN else ''
    if avg_range:
        ratio = rng / avg_range
        size = 'Big' if ratio > CANDLE_BIG_RATIO else 'Small' if ratio < CANDLE_SMALL_RATIO else ''
    else:
        ratio, size = None, ''

    tol_touch = cl * LEVEL_TOUCH_PCT / 100
    tol_near = cl * LEVEL_NEAR_PCT / 100
    touched, nearest = [], None
    for key, name in _LEVEL_NAMES:
        y = lv[key]
        if l - tol_touch <= y <= h + tol_touch:
            touched.append(name)
            continue
        dist = min(abs(y - h), abs(y - l))
        if dist <= tol_near and (nearest is None or dist < nearest[1]):
            nearest = (name, dist, y)

    # The CPR's three lines count once — "touched CPR", not "touched TC, P, BC".
    cpr_lines = [n for n in touched if n in ('TC', 'P', 'BC')]
    if cpr_lines:
        touched = [n for n in touched if n not in cpr_lines] + ['CPR']
    # A big candle after a tight session can sweep half the ladder; the
    # three nearest the close are what the eye reads off the chart.
    dist_of = {name: abs(lv[key] - cl) for key, name in _LEVEL_NAMES}
    dist_of['CPR'] = abs(lv['pp'] - cl)
    touched = sorted(touched, key=lambda n: dist_of[n])[:3]

    words = [w for w in (strength, size, colour) if w]
    text = ' '.join(words) if colour != 'Doji' else 'Indecision (doji)' + (f', {size.lower()}' if size else '')
    if touched:
        text += ' · touched ' + ', '.join(touched)
    elif nearest:
        text += f' · near {nearest[0]}'
    return {
        'text': text,
        'colour': colour,
        'open': _r(o), 'high': _r(h), 'low': _r(l), 'close': _r(cl),
        'range': _r(rng, 1),
        'body_pct': _r(body_ratio * 100, 0),
        'range_ratio': _r(ratio, 2),
        'touched': touched,
        'near': nearest[0] if nearest else None,
    }


# The chart's level names in the sheet's words.
_LEVEL_WORDS = {'PDH': 'Prev High', 'PDL': 'Prev Low', 'Cam R3': 'R3(cam)', 'Cam S3': 'S3(cam)'}


def candle_words(fc: Dict[str, Any]) -> str:
    """The chart's first-candle reading in the sheet's vocabulary —
    'Strong small candle red near Prev Low', 'In decision candle(doji) near CPR'.
    What scripts/add_cpr_sessions.py writes into column F, and what `extend`
    puts on the manual side of a session the sheet has not reached."""
    text = fc['text']
    level = None
    if ' · touched ' in text:
        level = text.split(' · touched ')[1].split(', ')[0]
    elif ' · near ' in text:
        level = text.split(' · near ')[1]
    head = text.split(' · ')[0]
    if fc['colour'] == 'Doji':
        words = 'In decision candle(doji)'
    else:
        parts = head.split()               # e.g. ['Strong', 'small', 'Red']
        colour = parts[-1].lower()
        adj = ' '.join(p.lower() for p in parts[:-1])
        words = (adj.capitalize() + ' ' if adj else '') + f'candle {colour}'
        words = words[0].upper() + words[1:]
    if level:
        words += ' near ' + _LEVEL_WORDS.get(level, level)
    return words


def manual_colour(text: Optional[str]) -> Optional[str]:
    """Green/Red/Doji if the manual note names one, else None (no verdict)."""
    s = (text or '').lower()
    if 'doji' in s or 'decision' in s:
        return 'Doji'
    if 'green' in s:
        return 'Green'
    if 'red' in s:
        return 'Red'
    return None


def simulate_trade(bars: List[Dict[str, Any]], trade: Optional[str], entry: Optional[float],
                   target: Optional[float], sl: Optional[float],
                   setup_time: Optional[str] = None) -> Dict[str, Any]:
    """What the session's bars say the trade did: fill at the first bar after
    the setup candle (09:15 unless `setup_time` says otherwise) that trades
    through the entry — at the entry, or at the bar's open when it opened
    past the level, as a stop order would — then whichever of target / SL a
    later bar reaches first. Bar-level, so a bar reaching both is 'Both'
    rather than a guess. Still open at SQUARE_OFF -> 'EOD' at that price.
    `pnl` is from the fill, and `fill` says what that was."""
    if not trade or entry is None or target is None or sl is None:
        return {'result': None, 'pnl': None, 'entry_time': None, 'exit_time': None}
    is_buy = trade == 'BUY'
    filled_at = None
    fill = entry
    start = 1                                       # the 09:15 candle is the setup, not a fill
    if setup_time:
        start = next((i + 1 for i, b in enumerate(bars) if b['time'] >= setup_time), len(bars))
    for i, b in enumerate(bars[start:], start=start):
        if filled_at is None:
            # The entry is a stop order: it fills when the bar trades through
            # the level, and at the bar's open when the bar opened already
            # past it (3 Jun 2026: BUY 23,300, the 13:15 bar opened 23,303).
            if (b['high'] >= entry) if is_buy else (b['low'] <= entry):
                filled_at = i
                fill = max(entry, b['open']) if is_buy else min(entry, b['open'])
            else:
                continue
        hit_t = (b['high'] >= target) if is_buy else (b['low'] <= target)
        hit_s = (b['low'] <= sl) if is_buy else (b['high'] >= sl)
        if i == filled_at and ((b['open'] <= sl) if is_buy else (b['open'] >= sl)):
            # The fill bar OPENED beyond the stop: its far extreme came before
            # the entry triggered, not after (3 Mar 2025: the 09:40 bar opened
            # 22,184 over a 22,183 stop and filled a SELL at 22,151 on the
            # way down). The stop cannot have been hit yet.
            hit_s = False
        if hit_t and hit_s:
            return {'result': 'Both', 'pnl': None, 'fill': fill,
                    'entry_time': bars[filled_at]['time'], 'exit_time': b['time']}
        if hit_t:
            return {'result': 'Target', 'pnl': _r(target - fill if is_buy else fill - target), 'fill': fill,
                    'entry_time': bars[filled_at]['time'], 'exit_time': b['time']}
        if hit_s:
            return {'result': 'SL', 'pnl': _r(sl - fill if is_buy else fill - sl), 'fill': fill,
                    'entry_time': bars[filled_at]['time'], 'exit_time': b['time']}
    if filled_at is None:
        return {'result': 'No fill', 'pnl': None, 'fill': None, 'entry_time': None, 'exit_time': None}
    # Neither side reached: squared off at 15:15 — the price at that moment
    # is the 15:15 bar's open (the last close when the day ends earlier).
    sq = next((b for b in bars if b['time'] >= SQUARE_OFF), None)
    exit_px, exit_t = (sq['open'], sq['time']) if sq else (bars[-1]['close'], bars[-1]['time'])
    return {'result': 'EOD', 'pnl': _r(exit_px - fill if is_buy else fill - exit_px), 'fill': fill,
            'entry_time': bars[filled_at]['time'], 'exit_time': exit_t}


# ── why the trade was placed where it was ─────────────────────────────────
# Each of entry / target / SL is read against the ladder the chart shows:
# the daily CPR and floor pivots, Camarilla R3/S3, PDH/PDL, the weekly CPR,
# the 09:15 candle's high/low and the day's open. The nearest level names
# the reason, phrased by the trade's direction — a BUY at PDH is a breakout,
# a SELL at PDH a rejection.

_RESISTANCE = {'PDH', 'R1', 'R2', 'R3', 'Cam R3', 'TC', 'WTC', '1st-candle high'}
_SUPPORT = {'PDL', 'S1', 'S2', 'S3', 'Cam S3', 'BC', 'WBC', '1st-candle low'}


def _reason_ladder(lv: Dict[str, float], wlv: Optional[Dict[str, float]],
                   c1: Dict[str, float], day_open: Optional[float]) -> List[tuple]:
    ladder = [(name, lv[key]) for key, name in _LEVEL_NAMES]
    if wlv:
        ladder += [('WTC', wlv['tc']), ('WP', wlv['pp']), ('WBC', wlv['bc'])]
    ladder += [('1st-candle high', c1['high']), ('1st-candle low', c1['low'])]
    if day_open is not None:
        ladder.append(('Day open', day_open))
    return ladder


def nearest_level(price: float, ladder: List[tuple], side: Optional[str] = None) -> Dict[str, Any]:
    """The level closest to `price` and how it is to be read: 'at' within
    LEVEL_AT_PCT, 'near' within LEVEL_REASON_NEAR_PCT, else 'none'. `side`
    'above' / 'below' keeps only levels on that side of the price (a stop
    is read against the level it hides behind, not the one it sits on)."""
    pool = ladder
    if side == 'above':
        pool = [t for t in ladder if t[1] >= price] or ladder
    elif side == 'below':
        pool = [t for t in ladder if t[1] <= price] or ladder
    name, y = min(pool, key=lambda t: abs(t[1] - price))
    delta = price - y
    pct = abs(delta) / price * 100
    fit = 'at' if pct <= LEVEL_AT_PCT else 'near' if pct <= LEVEL_REASON_NEAR_PCT else 'none'
    return {'name': name, 'level': _r(y), 'delta': _r(delta, 0), 'fit': fit}


def _fmt_level(n: Dict[str, Any]) -> str:
    if n['fit'] == 'at':
        return f"{n['name']} {_fmt_px(n['level'])}"
    if n['fit'] == 'near':
        return f"{n['name']} {_fmt_px(n['level'])} ({n['delta']:+.0f})"
    return f"no level nearby (closest {n['name']} {_fmt_px(n['level'])}, {n['delta']:+.0f})"


def _fmt_px(v: Optional[float]) -> str:
    return '—' if v is None else f'{v:,.0f}'


_CPR_LINES = {'TC', 'P', 'BC', 'WTC', 'WP', 'WBC'}
CPR_MERGE_GAP_PCT = 0.1     # bands overlapping, or within 0.1% of price, read as one zone


def cprs_merged(ladder: List[tuple]) -> bool:
    """True when the daily CPR band and the weekly one overlap or nearly touch."""
    lv = dict(ladder)
    if not all(k in lv for k in ('BC', 'TC', 'WBC', 'WTC')):
        return False
    gap = max(lv['BC'], lv['WBC']) - min(lv['TC'], lv['WTC'])
    return gap <= lv['TC'] * CPR_MERGE_GAP_PCT / 100


def first_cpr_line(entry: float, is_buy: bool, ladder: List[tuple],
                   min_pts: float = 10.0) -> Optional[tuple]:
    """(name, price) of the CPR line — daily or weekly — price reaches first
    from `entry` in the trade's direction, at least `min_pts` away. When the
    two CPRs are merged this is the target: whichever line is hit first."""
    lines = [(n, y) for n, y in ladder if n in _CPR_LINES]
    if is_buy:
        ahead = [(n, y) for n, y in lines if y >= entry + min_pts]
        return min(ahead, key=lambda t: t[1]) if ahead else None
    ahead = [(n, y) for n, y in lines if y <= entry - min_pts]
    return max(ahead, key=lambda t: t[1]) if ahead else None


def explain_trade(m: Dict[str, Any], ladder: List[tuple], candle: Dict[str, Any],
                  price_side: str) -> Optional[Dict[str, Any]]:
    trade, entry, target, sl = m.get('trade'), m.get('entry'), m.get('target'), m.get('sl')
    if not trade or entry is None or target is None or sl is None:
        return None
    is_buy = trade == 'BUY'
    e = nearest_level(entry, ladder)
    t = nearest_level(target, ladder)
    x = nearest_level(sl, ladder, side='above' if is_buy else 'below')   # a BUY stop hides under a level

    # Entry — what the level is to the trade.
    if e['fit'] == 'none':
        entry_why = f"{trade} {_fmt_px(entry)} — {_fmt_level(e)}"
    elif e['name'] in _RESISTANCE:
        entry_why = (f"{trade} {_fmt_px(entry)} — breakout above {_fmt_level(e)}" if is_buy
                     else f"{trade} {_fmt_px(entry)} — rejection from {_fmt_level(e)}")
    elif e['name'] in _SUPPORT:
        entry_why = (f"{trade} {_fmt_px(entry)} — bounce off {_fmt_level(e)}" if is_buy
                     else f"{trade} {_fmt_px(entry)} — breakdown below {_fmt_level(e)}")
    else:
        entry_why = f"{trade} {_fmt_px(entry)} — at {_fmt_level(e)}"
    entry_why += f"; 1st candle {candle['colour'].lower()}, price {price_side.lower()} CPR"

    # Target — the next level in the trade's direction. When the daily and
    # the weekly (1-hour chart) CPRs sit on top of each other they are read
    # as one zone, and a target on either of them is "the merged CPR".
    expected = _RESISTANCE if is_buy else _SUPPORT
    merged = cprs_merged(ladder)
    if t['fit'] == 'none':
        target_why = f"Target {_fmt_px(target)} — {_fmt_level(t)}"
    elif merged and t['name'] in _CPR_LINES:
        # ... and the target is whichever of their lines price reaches first.
        first = first_cpr_line(entry, is_buy, ladder)
        if first and first[0] == t['name']:
            target_why = (f"Target {_fmt_px(target)} — the merged daily + weekly CPR, "
                          f"first line reached: {_fmt_level(t)}")
        else:
            target_why = (f"Target {_fmt_px(target)} — the merged daily + weekly CPR, at {_fmt_level(t)}"
                          + (f"; first line reached would be {first[0]} {_fmt_px(first[1])}" if first else ''))
    elif t['name'] in expected:
        target_why = f"Target {_fmt_px(target)} — next {'resistance' if is_buy else 'support'} {_fmt_level(t)}"
    else:
        target_why = f"Target {_fmt_px(target)} — at {_fmt_level(t)}"

    # SL — tucked beyond a level against the trade.
    side = 'below' if is_buy else 'above'
    sl_why = (f"SL {_fmt_px(sl)} — {side} {_fmt_level(x)}" if x['fit'] != 'none'
              else f"SL {_fmt_px(sl)} — {_fmt_level(x)}")

    risk, reward = abs(entry - sl), abs(target - entry)
    rr = round(reward / risk, 2) if risk else None
    return {
        'entry': entry_why, 'target': target_why, 'sl': sl_why,
        'entry_level': e, 'target_level': t, 'sl_level': x,
        'risk': _r(risk, 0), 'reward': _r(reward, 0), 'rr': rr,
        'text': f"{entry_why}. {target_why}. {sl_why}. Risk {risk:.0f} / reward {reward:.0f}"
                + (f" = 1:{rr:g}" if rr else ''),
    }


# ── the comparison ────────────────────────────────────────────────────────

_TRADE_KEYS = ('trade', 'entry', 'target', 'sl', 'result', 'pnl', 'reason', 'setup_time')
_ANALYSIS_KEYS = ('price_vs_daily', 'price_vs_hourly', 'cpr_type', 'cpr_direction',
                  'first_candle', 'boxes')


def sessions_from(manual_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sheet's one-row-per-trade shape -> one entry per DATE.

    The analysis columns are the session's (the sheet repeats them on every
    trade of the day; the first row's copy is taken), the trade columns
    become a list — empty for a day that was analysed but not traded.
    """
    order: List[str] = []
    by_date: Dict[str, Dict[str, Any]] = {}
    for m in manual_rows:
        ds = m['date']
        if ds not in by_date:
            order.append(ds)
            by_date[ds] = {'date': ds, **{k: m.get(k) for k in _ANALYSIS_KEYS}, 'trades': []}
        if m.get('trade'):
            by_date[ds]['trades'].append({k: m.get(k) for k in _TRADE_KEYS})
    return [by_date[d] for d in order]


def analyse(manual_rows: List[Dict[str, Any]], daily: List[Dict[str, Any]],
            intraday: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Manual rows + bars -> one grid row per session, with the chart's
    reading, match flags, and every trade of the day replayed.

    `daily`: [{date: date, open, high, low, close}] ascending, complete
    sessions only. `intraday`: 'YYYY-MM-DD' -> that session's 5-minute bars
    ascending, each {time: 'HH:MM', open, high, low, close}.
    """
    daily = sorted(daily, key=lambda b: b['date'])
    by_date = {b['date'].isoformat(): i for i, b in enumerate(daily)}
    weeks = weekly_bars(daily)
    week_keys = sorted(weeks)

    # Average first-candle range per session, for the Small/Big reading.
    first_ranges: Dict[str, float] = {}
    for ds, bars in intraday.items():
        if bars:
            first_ranges[ds] = bars[0]['high'] - bars[0]['low']

    out_rows = []
    for m in sessions_from(manual_rows):
        trades = [{'manual': t, 'chart': None, 'match': None, 'strategy': strategy_of(t.get('reason'))} for t in m['trades']]
        row: Dict[str, Any] = {'manual': m, 'date': m['date'], 'chart': None,
                               'match': {}, 'trades': trades}
        out_rows.append(row)
        idx = by_date.get(m['date'])
        if idx is None or idx < 2:
            row['error'] = 'no daily bar for this session'
            continue
        bars = intraday.get(m['date']) or []
        if not bars:
            row['error'] = 'no 5-minute bars for this session'
            continue

        prev, prev2 = daily[idx - 1], daily[idx - 2]
        lv = levels(prev['high'], prev['low'], prev['close'])
        lv_y = levels(prev2['high'], prev2['low'], prev2['close'])
        width_pct = cpr_width_pct(prev['high'], prev['low'], prev['close']) or 0.0
        hist = [cpr_width_pct(b['high'], b['low'], b['close'])
                for b in daily[max(0, idx - 1 - WIDTH_HISTORY_SESSIONS):idx - 1]]
        hist = [w for w in hist if w is not None]
        avg_width = sum(hist) / len(hist) if len(hist) >= 4 else None

        this_week = _week_key(daily[idx]['date'])
        prev_weeks = [k for k in week_keys if k < this_week]
        wk = weeks[prev_weeks[-1]] if prev_weeks else None
        wlv = levels(wk['high'], wk['low'], wk['close']) if wk else None

        c1 = bars[0]
        prior_sessions = [d for d in daily[max(0, idx - CANDLE_HISTORY_SESSIONS):idx]]
        prior_ranges = [first_ranges[d['date'].isoformat()] for d in prior_sessions
                        if d['date'].isoformat() in first_ranges]
        avg_range = sum(prior_ranges) / len(prior_ranges) if len(prior_ranges) >= 5 else None

        daily_side = side_of(c1['close'], lv)
        weekly_side = side_of(c1['close'], wlv) if wlv else None
        cpr_type = classify_cpr_width(width_pct, avg_width)
        direction = cpr_direction(lv, lv_y)
        box = box_size(width_pct)
        candle = describe_first_candle(c1, lv, avg_range)
        ladder = _reason_ladder(lv, wlv, c1, daily[idx]['open'])

        chart = {
            'price_vs_daily': daily_side['side'],
            'price_in_daily_cpr': daily_side['in_cpr'],
            'price_vs_hourly': weekly_side['side'] if weekly_side else None,
            'price_in_hourly_cpr': weekly_side['in_cpr'] if weekly_side else None,
            'cpr_type': cpr_type,
            'width_pct': _r(width_pct, 3),
            'avg_width_pct': _r(avg_width, 3),
            'width_ratio': _r(width_pct / avg_width, 2) if avg_width else None,
            'cpr_direction': direction,
            'pivot_shift': _r(lv['pp'] - lv_y['pp'], 1),
            'boxes': box,
            'box_pts': _r(abs(lv['r1'] - lv['pdh']), 1),
            # The two shaded bands the chart draws — same height, one at each end of yesterday's range.
            'box_upper': {'top': _r(max(lv['r1'], lv['pdh'])), 'bottom': _r(min(lv['r1'], lv['pdh']))},
            'box_lower': {'top': _r(max(lv['s1'], lv['pdl'])), 'bottom': _r(min(lv['s1'], lv['pdl']))},
            'first_candle': candle,
            'levels': {k: _r(v) for k, v in lv.items()},
            'weekly': {k: _r(wlv[k]) for k in ('pp', 'bc', 'tc')} if wlv else None,
            # Yesterday's CPR, and whether yesterday's range left it untouched
            # — a virgin CPR, the level the trade rule's late rejection needs
            # stacked on PDH/R1 (1 Sept 2026: 31 Aug's 24,133-24,161 over
            # PDH 24,129 / R1 24,142).
            'prev_cpr': {'pp': _r(lv_y['pp']), 'bc': _r(lv_y['bc']), 'tc': _r(lv_y['tc']),
                         'virgin': not (prev['low'] <= lv_y['tc'] and prev['high'] >= lv_y['bc'])},
            # Every earlier session's CPR that no session since has traded
            # through — the virgin CPRs still standing at today's open, the
            # supports/resistances a trade's target stops at (30 Apr 2026:
            # 15 Apr's 23,732-23,806 under a SELL from 23,929).
            'virgin_cprs': virgin_cprs(daily, idx),
            'prev_session': {'date': prev['date'].isoformat(), 'high': _r(prev['high']),
                             'low': _r(prev['low']), 'close': _r(prev['close'])},
            'day': {'open': _r(daily[idx]['open']), 'high': _r(daily[idx]['high']),
                    'low': _r(daily[idx]['low']), 'close': _r(daily[idx]['close'])},
        }
        row['chart'] = chart

        for t in trades:
            tm = t['manual']
            t['strategy'] = strategy_of(tm.get('reason'))
            sim = simulate_trade(bars, tm['trade'], tm['entry'], tm['target'], tm['sl'], tm.get('setup_time'))
            t['chart'] = dict(sim, reasons=explain_trade(tm, ladder, candle, daily_side['side']))
            t['match'] = (_eq(tm.get('result'), sim['result'])
                          if tm.get('result') and sim['result'] else None)

        mc = manual_colour(m.get('first_candle'))
        row['match'] = {
            'price_vs_daily': _eq(m.get('price_vs_daily'), chart['price_vs_daily']),
            'price_vs_hourly': _eq(m.get('price_vs_hourly'), chart['price_vs_hourly']),
            'cpr_type': _eq(m.get('cpr_type'), chart['cpr_type']),
            'cpr_direction': _eq(m.get('cpr_direction'), chart['cpr_direction']),
            'boxes': _eq(m.get('boxes'), chart['boxes']),
            'first_candle': _eq(mc, candle['colour']) if mc else None,
        }

    return {'rows': out_rows, 'summary': summarise(out_rows)}


def virgin_cprs(daily: List[Dict[str, Any]], idx: int) -> List[Dict[str, Any]]:
    """The CPRs of sessions before `daily[idx]` that no session from their
    own day up to yesterday has traded through, lowest first. A CPR is
    touched when a session's range overlaps it."""
    out = []
    for j in range(1, idx):
        q = daily[j - 1]
        lv = levels(q['high'], q['low'], q['close'])
        if any(b['low'] <= lv['tc'] and b['high'] >= lv['bc'] for b in daily[j:idx]):
            continue
        out.append({'date': daily[j]['date'].isoformat(), 'bc': _r(lv['bc']), 'tc': _r(lv['tc'])})
    return sorted(out, key=lambda v: v['bc'])


def _eq(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    if a is None or b is None:
        return None
    return a.strip().lower() == b.strip().lower()


_MATCH_KEYS = ('price_vs_daily', 'price_vs_hourly', 'cpr_type', 'cpr_direction',
               'boxes', 'first_candle', 'result')


# The setups the trade rule takes, read back off the reason it writes —
# each (test, name, how) in order, first match wins. The phrases are the
# ones cpr_trade_rule.propose puts in `why`; `how` is the setup in words
# for the Strategy list: when it applies, the entry, the stop, the target
# and the worked example. A reason none of them fits is "Other".
def _how(setup, entry, stop, target, example):
    return {'setup': setup, 'entry': entry, 'stop': stop, 'target': target, 'example': example}


_STRATEGIES = (
    (lambda r: 'wicked out of it both ways' in r, 'Inside CPR, wicked both ways → the box decides', _how(
        "The 09:15 candle opens and closes inside the CPR but its high is over TC and its low under BC — both CPR lines are spent, "
        "so the day is decided at the box. The first close over the PDH/R1 box (under the PDL/S1 box) is the break candle.",
        "The candle after the break decides: through the break candle's high → BUY over it (continuation); a close back under the box "
        "is the failure → SELL under that candle. Mirror at the lower box. A continuation with a virgin CPR's edge inside its risk is refused "
        "(support/resistance too close), and the day then trades the rejection back across the whole box the other way.",
        "Continuation: under the break candle's low. Failure: over the break candle's high.",
        "Continuation: the next level (R2 / Cam R3 / R3) or 1:2, whichever is nearer beyond the risk. Failure: the nearest of the CPR lines / PDL / 1:2.",
        "26 May 2026: 10:35 closed 24,085 over R1 24,083; 10:40 closed 24,073 back under the box → SELL 24,070, SL 24,091 (the 10:35 high), target 1:2 24,028.")),
    (lambda r: r.startswith('the first trade was stopped by the'), 'Reversal after a stop → virgin CPR', _how(
        "A second trade on a volatile day (by 13:00): the bar that stopped the first trade is a strong candle the other way, and a virgin CPR lies ahead of it "
        "within 3× the risk. The virgin-CPR late rejection, when it applies, comes first.",
        "BUY over that candle's high (SELL under its low).",
        "The candle's other end: under its low for the BUY, over its high for the SELL.",
        "The nearest virgin CPR ahead — its near edge.",
        "20 Jan 2025: the 09:20-candle SELL was stopped by the 09:55 strong green → BUY 23,237, SL 23,204, target 17 Jan's virgin CPR 23,318 (+81).")),
    (lambda r: 'cross candle' in r, 'Inside CPR → cross candle', _how(
        "09:15 closes inside the daily CPR on a BIG candle (over 0.25% of price) — its far end is no stop. "
        "Any later close out of the CPR is the break; the next candle that comes back to that CPR line is the cross candle.",
        "Over the cross candle's high (BUY) / under its low (SELL). A close back inside the CPR voids the break and waits for a new one.",
        "Under the cross candle's low (BUY) / over its high (SELL).",
        "The next PDH/R1 (PDL/S1); 1:2 when that level is nearer than the risk.",
        "7 Apr 2026: 10:00 closed 22,914 over TC 22,902; 10:05 cross candle 22,929-22,894 → BUY 22,930, SL 22,892, target PDH 22,998.")),
    (lambda r: '09:15 closed inside the CPR' in r, 'Inside CPR → breakout candle', _how(
        "09:15 closes inside the daily CPR on a normal-sized candle. On a WIDE-CPR day the break needs room: if the next box "
        "(PDH/R1 above, PDL/S1 below) is closer to the entry than the risk, price is boxed in and the day is skipped.",
        "The first STRONG candle (body ≥ 60%) closing out of the CPR: over its high (BUY) / under its low (SELL).",
        "The 09:15 candle's other end (its low for a BUY, its high for a SELL).",
        "The nearer of PDH/R1 (PDL/S1); 1:2 if too near, 1:1 if further than 1.5× the risk.",
        "17 Feb 2026: 09:40 closed above TC → BUY 25,647, SL 25,630, target PDH 25,697.")),
    (lambda r: 'retested it' in r, 'Big 09:15 to the box → retest', _how(
        "A big red 09:15 candle (over 0.25%) opens above the PDL/S1 box and closes AT it, and 09:20 is not a small red close under the box — no sell. "
        "Price must first LOSE the box (a close under it) and then close back clearly over it.",
        "The retest: a small green candle (≤ half the 09:15 range) that dips to the box top and closes above it on its high → BUY over its high, by 14:00. Mirror at PDH/R1 → SELL.",
        "Under the retest candle's low (over its high for the SELL).",
        "The CPR's near line (BC for a BUY, TC for a SELL); 1:2 if the CPR is nearer than the risk.",
        "3 Jun 2026: lost PDL 23,229 by 10:00, reclaimed 12:35, 13:10 retest → BUY 23,300, SL 23,274, target BC 23,393 (+90).")),
    (lambda r: r.startswith('gap-') and 'retest to Cam' in r, 'Gap day → Cam R3 · S3 retest', _how(
        "Gap-up (gap-down) day whose zone reversal candle is BIG (over 0.25%) and Camarilla R3 (S3) sits inside the R1/PDH (S1/PDL) box. "
        "The reversal is not entered over its high — that close over the box is the breakout.",
        "The retest, by 14:00: a candle whose low comes to Cam R3 and that closes back over the box's top (R1) → BUY over its high. "
        "Mirror on a gap-down with Cam S3 → SELL under the retest candle.",
        "Under Cam R3 (over Cam S3). A close back under the box before the retest voids the trade.",
        "1:2.",
        "10 Mar 2026: 10:15 reversed on a 64-pt candle; 12:45 dipped to 24,143 by Cam R3 24,133 and closed 24,185 over R1 → BUY 24,186, SL 24,131, target 24,296 (+110).")),
    (lambda r: r.startswith('09:15 strong green candle opened') or r.startswith('09:15 strong red candle opened'), '09:15 box rejection', _how(
        "The 09:15 candle itself is strong (body ≥ 60%) and not oversized (≤ 0.3% of price), opens in or at the PDL/S1 box, touches it (PDL, S1 or the Cam S3 inside it) and closes "
        "back over the box. Mirror: a strong red 09:15 off the PDH/R1 box. It stands even when the CPRs and the box form one zone — it trades the zone's own edges.",
        "BUY over the 09:15 high (SELL under its low for the mirror).",
        "Under the 09:15 low (over its high).",
        "Cam R3 when it sits in or at the CPR — the zone's top — else the CPR's far line (TC); 1:2 if that is nearer than the risk. Mirror: Cam S3 / BC.",
        "18 Jun 2025: 09:15 O 24,788 in PDL/S1 24,784-24,814, low 24,777 on S1, closed 24,830 over the box → BUY 24,839, SL 24,775, target Cam R3 24,900 (+61).")),
    (lambda r: 'waited for the retracement' in r, 'Big box rejection → the retracement', _how(
        "A narrow-CPR, small-box day where no other setup fired: the 09:15 candle reaches PDH/R1 (PDL/S1 below) and closes back under it "
        "(over it), but is bigger than 0.15% of price — too big to enter off its own extreme.",
        "Price must come back and TOUCH the box again; the first small candle that then makes a LOWER high (higher low) and closes under the box "
        "(over it) with its close in the lower half of its range (upper half) is the entry: SELL under its low (BUY over its high).",
        "Over that candle's high (under its low).",
        "The opposite box's far edge — PDL/S1 below, PDH/R1 above.",
        "22 Sep 2026: the 55-pt 09:15 into R1 23,482 closed 23,446 under PDH 23,467; 10:05 touched PDH again; 10:15 made a lower high and closed "
        "23,459 under it → SELL 23,452, SL 23,468, target PDL 23,315 (+137).")),
    (lambda r: 'too big to enter' in r, 'Big 09:15 break → 09:20 entry', _how(
        "A big 09:15 candle (over 0.25%) that opens over PDL/S1 and closes under both, straddles the box (opens inside, trades both sides, closes inside), or runs from above down to the box. Too big to enter off its own low.",
        "The 09:20 candle decides: a small red candle (≤ half the 09:15 range) closing under the box → SELL under its low. Mirror: a big green break over PDH/R1, small green 09:20 → BUY over it.",
        "Over the 09:20 candle's high (under its low for the BUY).",
        "1:2, or a virgin CPR standing in between when that is nearer.",
        "31 Aug 2026: 09:15 O 24,118 over S1, C 24,065 (90 pts); 09:20 small 24,085-24,058 → SELL 24,056, SL 24,087, target 23,998 (+58). Also 19 Aug (straddle).")),
    (lambda r: 'waited for 09:20' in r, '09:15 long-wick break → 09:20 confirm', _how(
        "09:15 opens inside the PDL/S1 box and closes under it, but is not strong: a lower wick of half its range or more says the level was bought once already.",
        "Wait for 09:20: a small red candle holding inside that wick (under the 09:15 close, over its low) confirms → SELL under its low. Mirror over PDH/R1 → BUY.",
        "Over the 09:15 high (under the 09:15 low for the BUY).",
        "1:2.",
        "7 Sep 2026: 09:15 O 23,883 C 23,859 L 23,819; 09:20 red 23,861-23,838 → SELL 23,837, SL 23,892, target 23,750.")),
    (lambda r: 'virgin CPR' in r and 'rejected from it' in r, 'Virgin CPR late rejection', _how(
        "Narrow CPR only. Yesterday's CPR was never touched yesterday (virgin) and sits on today's PDH/R1 box, with price above today's CPR. Allowed until 13:00, and as a second trade after a stopped-out first.",
        "A strong red candle that pokes into the virgin CPR / PDH/R1 stack and closes back under PDH/R1 → SELL under its low. Mirror under PDL/S1 → BUY.",
        "Over the rejection candle's high.",
        "The CPR's far line (BC); if the bar reaching it closes strongly through the CPR, the target moves on to PDL.",
        "1 Sep 2026: 31 Aug's CPR 24,133-24,161 on PDH 24,129 / R1 24,142; 11:30 rejected → SELL 24,122, SL 24,145; CPR broke → PDL 23,994 (+128).")),
    (lambda r: 'inside PDH/R1' in r or 'inside PDL/S1' in r, '09:15 out of the PDH/R1 · PDL/S1 box', _how(
        "Narrow CPR, open above both CPRs, and the 09:15 candle opens INSIDE the PDH/R1 box and closes above it. Mirror under PDL/S1.",
        "Whichever comes first by 11:00: a bar breaking the 09:15 high → BUY over it — unless the 09:15 candle is big (over 0.25%) or the "
        "breaking candle is (over 0.15%): two big candles are a spike to fade, not a trend to join, so the day waits (until 13:00) for the "
        "reversal; or, once a close has fallen back under the box, the first red candle that climbs back to the box and closes under it → SELL under its low.",
        "BUY: under the 09:15 low. SELL: over the rejection candle's high.",
        "BUY: the next level or 1:2. SELL: the CPR (TC), and PDL instead if the bar reaching the CPR closes strongly through it.",
        "24 Aug 2026: 09:15 O 24,285 in 24,284-24,288, C 24,303; 09:30 closed under; 09:40 red to 24,286 → SELL 24,273, SL 24,288, CPR broke → PDL 24,207 (+66).")),
    (lambda r: r.startswith('gap-'), 'Gap day', _how(
        "The open sits past both R1 and PDH (or both S1 and PDL) by 0.25% or more — the CPR is out of reach.",
        "Wait for price to come back to the R1/PDH (S1/PDL) zone: a strong close back through it is the reversal (BUY on a gap-down), a close beyond it the breakdown (SELL). "
        "While the zone is unreached, a BASE — two or more small candles, at least two against the fill — followed by the first strong candle in the fill's direction: over its high AND the 09:15 high (under both lows).",
        "Past the zone for the zone trades; under the strong candle's low (over its high) for the base trade.",
        "The next level for the zone trades; the zone's near edge (the gap fill) for the base trade, 1:2 if too near.",
        "11 Sep 2026: gap-down 23,270; seven small candles 09:20-09:50; 09:55 strong green → BUY 23,279, SL 23,243, target PDL 23,380.")),
    (lambda r: 'trend day expected' in r, 'Narrow CPR trend day', _how(
        "Narrow CPR (under 0.13%) with the open clear of both CPRs — a trend day is expected — and the 09:15 candle rejects the other side (wick ≥ half its range, close at the far end).",
        "The first strong candle in the trend's direction by 11:00: over its high (under its low). While PDH/R1 (PDL/S1) still lies ahead it is the wall — the candle must close through it; a candle turned back from the wall is instead a fade → SELL under its low.",
        "0.025% beyond the breakout candle; for the fade, over the high so far.",
        "1:2; for the fade, the first CPR line.",
        "10 Feb 2026: 09:30 bull candle → BUY 25,945, SL 25,903, target 26,029. 11 Feb: 09:20 rejected from R1 → SELL 25,974, SL 26,011, target 25,933.")),
    (lambda r: 'strong red candle touched PDL/S1' in r or 'strong green candle touched PDH/R1' in r,
     '09:15 strong break (narrow CPR)', _how(
        "Narrow CPR and the 09:15 candle itself is a strong break (body ≥ 60%): it touches PDL/S1 and closes below it. Mirror: a strong green 09:15 through PDH/R1.",
        "SELL under the 09:15 low — or under a level lying just beneath it. BUY over the 09:15 high for the mirror.",
        "Over the 09:15 high (under its low for the BUY).",
        "1:2.",
        "12 Feb 2026: SELL 25,844 under S2, SL 25,909; squared off at 15:15.")),
    (lambda r: ('above the weekly CPR, under the daily one' in r
                or 'below the weekly CPR, above the daily one' in r), 'Between the CPRs', _how(
        "09:15 closes between the two CPRs — above the weekly one and under the daily one, or the reverse — and they are not merged. The daily CPR is the lid.",
        "Only a close through the daily CPR counts: BUY over that candle's high (SELL under its low on a close below it). Nothing else is taken.",
        "Under the daily CPR (over it for the SELL).",
        "The next level; 1:2 if too near, 1:1 if further than 1.5× the risk.",
        "4 Feb 2026: never closed above 25,815-25,991 → no trade.")),
    (lambda r: 'interlink into one zone' in r and 'closed over the zone' in r or 'closed under the zone' in r, 'One zone → break out of it', _how(
        "The daily CPR, the weekly CPR and a box (PDH/R1 or PDL/S1) interlink into one continuous band and the 09:15 candle closes inside it. "
        "Every line is part of the same zone, so nothing inside it is traded.",
        "The first candle by 14:00 that closes beyond the zone: over its top → BUY over that candle's high; under its bottom → SELL under its low.",
        "Under the breakout candle's low (over its high for the SELL).",
        "The next level beyond the zone (R2 / R3, or S2 / S3), or 1:2 when that is nearer than the risk.",
        "6 Mar 2025: CPR 22,231-22,302, weekly 22,212-22,386, PDH/R1 22,395-22,466 — one zone; the 09:20 PDH rejection inside it refused; "
        "13:05 closed 22,477 over R1 → BUY 22,479, SL 22,456.")),
    (lambda r: r.startswith('weekly CPR') and 'merged with PD' in r, 'Weekly CPR on the box → PDH/PDL rejection', _how(
        "The weekly CPR lies over the PDH/R1 box (or under the PDL/S1 box), so the two make one big zone, and the 09:15 candle opens inside it.",
        "A strong red candle that reaches PDH and closes through it — a big candle counts here — → SELL under its low. "
        "Mirror: a strong green candle off PDL inside the lower zone → BUY over its high.",
        "Over the candle's own high (under its low for the BUY).",
        "The nearest of the CPR lines / PDL (PDH) / 1:2 that lies at least 0.6× the risk away.",
        "13 Jan 2026: weekly CPR 25,788-25,998 on PDH 25,813 / R1 25,911, open 25,897; 09:25 strong red 25,837 → 25,763 through PDH → "
        "SELL 25,759, SL 25,839, target the CPR BC 25,643.")),
    (lambda r: r.startswith('interlinked CPRs'), 'Interlinked CPRs → small rejection', _how(
        "The daily and weekly CPRs overlap into one band. A big candle into the band is not entered; the rejection candle must be SMALL "
        "(at most 0.15% of price) and may come as late as 13:00.",
        "Open above the band: a small green candle that reaches the daily CPR (within 0.05% of TC) and closes back over the whole band → "
        "BUY over its high. Mirror below the band: a small red candle up to BC closing under both → SELL under its low.",
        "Under the candle's low — or under the session VWAP when it runs just beneath the low (within 0.1%), the line the candle leans on.",
        "The next level; 1:2 when that level is nearer than the risk.",
        "6 Apr 2026: CPRs 22,482-22,636 and 22,562-22,663; 09:30's 88-pt rejection skipped; 11:50 small candle low 22,645, closed 22,668 → "
        "BUY 22,677, SL 22,636 (VWAP), target PDH 22,782.")),
    (lambda r: 'tested the CPR' in r and ('closed green on it' in r or 'closed red on it' in r), 'CPR retest after a failed box break', _how(
        "The 09:15 candle opens on one side of a narrow CPR and closes on the other, so its own low/high is no entry. Price then breaks the "
        "PDH/R1 box (PDL/S1 below) and the break FAILS — it comes back through the box.",
        "The first test of the CPR from the far side: a green candle that dips to the CPR and closes on it → BUY over that candle "
        "(mirror: a red candle up to the CPR closing on it → SELL under it).",
        "Under the CPR (BC) for the BUY; over the CPR (TC) for the SELL.",
        "The day's high (low) made by the failed break — the level price has already shown it can reach.",
        "17 Sep 2026: 09:15 opened under the CPR 23,200-23,212 and closed 23,256; 10:05 closed over PDH/R1, broke back down; 11:05 tested "
        "the CPR (low 23,214), 11:10 closed green on it → BUY 23,224, SL 23,195, target the day high 23,325 (+101).")),
    (lambda r: 'rejected from the CPR' in r, 'Rejection from the CPR', _how(
        "Open below the CPR (the daily and weekly CPRs read as one band when they overlap or nearly touch). Mirror above the CPR. "
        "Off on a far-box day: a narrow CPR with both boxes 0.4% of price (and 5× its height) away is too thin to fade — such days trade the boxes only.",
        "A strong red candle by 11:00 that pokes up into the CPR and closes back below it → SELL a buffer (0.05%) under its low. "
        "A BIG rejection candle (over 0.15%) is not entered off: wait for a small candle back into the CPR that does not close above it → SELL under that. "
        "Above the CPR: BUY the rejection from it.",
        "A buffer over the first pivot above the candle's high (the candle's high when it already cleared the pivots); for the small retest candle, over its own high.",
        "The next support/resistance; 1:2 if nearer than the risk, 1:1 if further than 1.5× the risk. For the retest entry, the nearer of S1 / PDL. A stop over 100 pts is halved.",
        "16 Mar 2026: 09:55 candle up to 23,233 into the CPR 23,201-23,302, closed 23,168 → SELL 23,153, SL 23,208, target 23,043 (+110). "
        "1 Sep 2026: 09:15's 55-pt rejection skipped; 09:30 small candle to 24,077 → SELL 24,057, SL 24,078, target S1 24,006. "
        "When the daily and weekly CPRs interlink, the small-rejection rule applies instead.")),
    (lambda r: 'rejected from' in r, 'Rejection from PDH/R1 · PDL/S1', _how(
        "Open below the CPR. A candle by 11:00 that dips to PDL or S1 (its low at or through the level) and closes back above it — strong, "
        "or closing on its high after the dip — and no bigger than 0.15% of price: a big candle off the level is not entered. Mirror above the CPR at PDH/R1.",
        "BUY 1 pt over the rejection candle's high (SELL 1 pt under its low for the mirror).",
        "0.02% under the lowest level the candle rejected (over the highest for the SELL).",
        "The CPR, however far; 1:2 when the CPR is nearer than the risk.",
        "16 Feb 2026: 09:35 dipped under PDL and S1 and closed on its high → BUY over it, target the CPR.")),
)
_OTHER_HOW = _how("A trade written by hand or by an earlier wording of the rule, whose reason no current setup matches.",
                  "As written in the row's reason on the grid (the Days traded list below carries every such trade).",
                  "As written in the row's reason — the stop the trade was placed with is on the grid.",
                  "As written in the row's reason — the target the trade was placed with is on the grid.",
                  "Open the row on the CPR Logic grid for the full reason, entry, stop and target.")


def strategy_of(reason: Optional[str]) -> str:
    r = reason or ''
    for test, name, _how in _STRATEGIES:
        if test(r):
            return name
    return 'Other'


def strategy_how(name: str) -> Dict[str, str]:
    """The setup in words (setup / entry / stop / target / example), for the Strategy list."""
    return next((how for _t, n, how in _STRATEGIES if n == name), _OTHER_HOW)


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-column agreement (once per session), the two P&L tallies (once
    per trade), and the strategy table: how often each setup was used
    and how it did."""
    agree: Dict[str, Dict[str, int]] = {k: {'match': 0, 'total': 0} for k in _MATCH_KEYS}
    manual_pnl = chart_pnl = 0.0
    wins = trades = chart_wins = chart_trades = 0
    strategies: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        for k, v in r['match'].items():
            if v is None:
                continue
            agree[k]['total'] += 1
            agree[k]['match'] += bool(v)
        for t in r['trades']:
            tm, tc = t['manual'], t['chart']
            st = strategies.setdefault(strategy_of(tm.get('reason')), {
                'trades': 0, 'wins': 0, 'losses': 0, 'flat': 0, 'pnl': 0.0, 'last': None, 'targets': 0, 'stops': 0, 'eod': 0})
            st['trades'] += 1
            st['last'] = max(st['last'] or '', r['date'])
            pnl = tm.get('pnl')
            if pnl is None or pnl == 0:
                st['flat'] += 1
            elif pnl > 0:
                st['wins'] += 1
            else:
                st['losses'] += 1
            st['pnl'] += pnl or 0.0
            res = tm.get('result')
            if res == 'Target':
                st['targets'] += 1
            elif res == 'SL':
                st['stops'] += 1
            elif res == 'EOD':
                st['eod'] += 1
            if tm.get('pnl') is not None:
                trades += 1
                manual_pnl += tm['pnl']
                wins += tm['pnl'] > 0
            if tc and tc.get('pnl') is not None:
                chart_trades += 1
                chart_pnl += tc['pnl']
                chart_wins += tc['pnl'] > 0
            if t['match'] is not None:
                agree['result']['total'] += 1
                agree['result']['match'] += bool(t['match'])
    return {
        'sessions': len(rows),
        'rows': sum(len(r['trades']) for r in rows),
        'agreement': {k: dict(v, pct=_r(100 * v['match'] / v['total'], 0) if v['total'] else None)
                      for k, v in agree.items()},
        'manual': {'trades': trades, 'wins': wins, 'pnl': _r(manual_pnl)},
        'chart': {'trades': chart_trades, 'wins': chart_wins, 'pnl': _r(chart_pnl)},
        'strategies': sorted(
            [dict(v, name=k, how=strategy_how(k), pnl=_r(v['pnl']),
                  win_pct=_r(100 * v['wins'] / (v['wins'] + v['losses']), 0) if v['wins'] + v['losses'] else None)
             for k, v in strategies.items()],
            key=lambda v: (-v['trades'], v['name'])),
    }


# ── data fetch ────────────────────────────────────────────────────────────

def _daily_rows(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in raw or []:
        if any(c.get(k) is None for k in ('open', 'high', 'low', 'close')):
            continue
        d = c['date'].date() if hasattr(c['date'], 'date') else c['date']
        out.append({'date': d, 'open': float(c['open']), 'high': float(c['high']),
                    'low': float(c['low']), 'close': float(c['close'])})
    out.sort(key=lambda b: b['date'])
    return out


def _intraday_by_session(raw: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by: Dict[str, List[Dict[str, Any]]] = {}
    for c in raw or []:
        if any(c.get(k) is None for k in ('open', 'high', 'low', 'close')):
            continue
        stamp = c['date']
        mins = stamp.hour * 60 + stamp.minute
        if mins < 9 * 60 + 15 or mins >= 15 * 60 + 30:
            continue
        by.setdefault(stamp.strftime('%Y-%m-%d'), []).append({
            'time': stamp.strftime('%H:%M'),
            'open': float(c['open']), 'high': float(c['high']),
            'low': float(c['low']), 'close': float(c['close']),
            'volume': float(c.get('volume') or 0),          # for the session VWAP the trade rule's stops can sit under
        })
    for bars in by.values():
        bars.sort(key=lambda b: b['time'])
    return by


def fetch_bars(symbol: str, first: date, last: date, fresh: bool = False) -> tuple:
    """(daily, intraday-by-session) covering the manual sheet's dates, with
    enough daily history in front for the averages and the previous week.
    `fresh` skips the hour-long cache — for `extend`, whose whole point is
    the session that closed since the page last looked."""
    from trading_app.service import multichart_service as mc
    adapter = mc.provider()
    fy_symbol = mc.resolve_symbol(symbol)
    today = date.today()
    last = min(last, today)

    daily_from = first - timedelta(days=(WIDTH_HISTORY_SESSIONS + CANDLE_HISTORY_SESSIONS) * 2 + 14)
    daily_raw = adapter.historical_data(fy_symbol, daily_from.isoformat(), last.isoformat(),
                                        'day', use_cache=not fresh, cache_ttl=3600.0)
    daily = [b for b in _daily_rows(daily_raw) if b['date'] < today or b['date'] <= last]

    # Intraday from a month before the first sheet date, so the first-candle
    # average has its 20 sessions, in Fyers-sized chunks.
    intraday: Dict[str, List[Dict[str, Any]]] = {}
    start = first - timedelta(days=CANDLE_HISTORY_SESSIONS * 2)
    while start <= last:
        end = min(start + timedelta(days=INTRADAY_CHUNK_DAYS - 1), last)
        raw = adapter.historical_data(fy_symbol, start.isoformat(), end.isoformat(),
                                      '5minute', use_cache=not fresh, cache_ttl=3600.0)
        intraday.update(_intraday_by_session(raw))
        start = end + timedelta(days=1)
    return daily, intraday


def compare(symbol: str) -> Dict[str, Any]:
    """The Trend page's payload: manual rows with the chart's reading beside each."""
    doc = load_manual(symbol)
    rows = doc.get('rows') or []
    if not rows:
        return {'success': True, 'symbol': symbol.upper(), 'source': doc.get('source'),
                'rows': [], 'summary': summarise([])}
    dates = sorted(datetime.strptime(r['date'], '%Y-%m-%d').date() for r in rows)
    daily, intraday = fetch_bars(symbol, dates[0], dates[-1])
    result = analyse(rows, daily, intraday)
    logger.info(f"[CPR backtest] {symbol.upper()}: {len(rows)} manual rows, "
                f"{len(daily)} daily bars, {len(intraday)} intraday sessions")
    return {
        'success': True,
        'symbol': symbol.upper(),
        'source': doc.get('source'),
        'imported_at': doc.get('imported_at'),
        'extended_at': doc.get('extended_at'),
        'rules': {
            'cpr_type': f'width vs {WIDTH_HISTORY_SESSIONS}-session average: <0.8x Narrow, >1.2x Wide',
            'boxes': f'CPR height as % of close: <{BOX_SMALL_MAX_PCT}% Small, >={BOX_BIG_MIN_PCT}% Big',
            'first_candle': (f'body <{int(DOJI_BODY_MAX * 100)}% of range = doji; '
                             f'>={int(STRONG_BODY_MIN * 100)}% = strong; range vs '
                             f'{CANDLE_HISTORY_SESSIONS}-session average: >{CANDLE_BIG_RATIO}x big, '
                             f'<{CANDLE_SMALL_RATIO}x small'),
        },
        **result,
    }


# ── the Update button ─────────────────────────────────────────────────────

def last_complete_session(now: Optional[datetime] = None) -> date:
    """Today once its bars are all in (15:30 IST), else yesterday — the
    replay needs the 15:15 square-off, so a half-day is never analysed."""
    now = now.astimezone(IST) if now else datetime.now(IST)
    if (now.hour, now.minute) >= SESSION_CLOSE:
        return now.date()
    return now.date() - timedelta(days=1)


def rule_note(why: str, sim: Optional[Dict[str, Any]], second: bool = False) -> str:
    """The column-O note for a rule trade: the reason and the chart replay."""
    from trading_app.service.cpr_trade_rule import RULE_TAG
    after = sim and sim.get('after') or 'SL'
    tag = (f"{RULE_TAG} (2nd, after the first entry was refused)" if second and after == 'refused'
           else f"{RULE_TAG} (2nd, after the first trade's {after})" if second else RULE_TAG)
    if not sim:
        return f"{tag}: {why}"
    return (f"{tag}: {why}; chart replay {sim['result']}"
            + (f" {sim['entry_time']} -> {sim['exit_time']}" if sim['exit_time'] else '')
            + (f", squared off at {sim['exit_time']}, P&L {sim['pnl']:+.0f}" if sim['result'] == 'EOD' and sim['pnl'] is not None else ''))


def chart_rows(r: Dict[str, Any], bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The manual-side rows, in scripts/import_cpr_manual.py's shape, from
    the chart's reading of a session the sheet has not reached: the
    analysis columns as add_cpr_sessions.py would write them, the trades
    as propose_cpr_trades.py would — one row per trade, the analysis
    repeated on each as the sheet does, a single untraded row when the
    rule finds nothing. Empty when the session has no bars."""
    from trading_app.service.cpr_trade_rule import propose_all
    c = r.get('chart')
    if not c:
        return []
    fc = c['first_candle']
    base: Dict[str, Any] = {
        'date': r['date'],
        'price_vs_daily': c['price_vs_daily'],
        'price_vs_hourly': c['price_vs_hourly'],
        'cpr_type': c['cpr_type'],
        'cpr_direction': c['cpr_direction'],
        'first_candle': candle_words(fc),
        'boxes': c['boxes'],
        'trade': None, 'entry': None, 'target': None, 'sl': None,
        'result': None, 'pnl': None, 'reason': None, 'setup_time': None,
    }
    analysis = (f"Analysis added from the chart (Fyers 5-min): CPR {abs(c['levels']['tc'] - c['levels']['bc']):.1f} pts "
                f"({c['width_pct']}%), 09:15 candle O {fc['open']} H {fc['high']} L {fc['low']} C {fc['close']}")
    rows = []
    for k, (p, why, setup_i, sim) in enumerate(propose_all(c, bars)):
        row = dict(base)
        if p and sim['result'] != 'No fill':
            row.update(trade=p['trade'], entry=p['entry'], target=p['target'], sl=p['sl'],
                       result=sim['result'], pnl=sim['pnl'], reason=why,
                       setup_time=bars[setup_i]['time'])
        row['note'] = (analysis + ' | ' if k == 0 else '') + rule_note(why, sim if p else None, second=k > 0)
        rows.append(row)
    return rows


def chart_row(r: Dict[str, Any], bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The first of `chart_rows` — the session's row, or its first trade."""
    rows = chart_rows(r, bars)
    return rows[0] if rows else None


def backfill(symbol: str, since: date) -> Dict[str, Any]:
    """Prepend every complete session from `since` up to the day before the
    sheet's first date, read from the chart the way `extend` reads the
    sessions after its last — the same analysis columns and the same
    rule-written trades. Returns what was added; nothing is written when
    there is nothing to add."""
    doc = load_manual(symbol)
    rows = doc.get('rows') or []
    if not rows:
        raise NoManualData(f'The {symbol.upper()} sheet is empty — nothing to backfill from')
    first_have = min(datetime.strptime(r['date'], '%Y-%m-%d').date() for r in rows)
    last = first_have - timedelta(days=1)
    if since > last:
        return {'success': True, 'symbol': symbol.upper(), 'added': [], 'first': first_have.isoformat()}

    daily, intraday = fetch_bars(symbol, since, last)
    sessions = sorted(ds for ds in intraday if since <= date.fromisoformat(ds) <= last)
    added, skipped = [], []
    if sessions:
        res = analyse([{'date': ds} for ds in sessions], daily, intraday)
        for r in res['rows']:
            rows_for = chart_rows(r, intraday.get(r['date']) or [])
            if rows_for:
                added.extend(rows_for)
            else:
                skipped.append({'date': r['date'], 'error': r.get('error')})
    if added:
        doc['rows'] = added + rows
        doc['backfilled_from'] = since.isoformat()
        doc['extended_at'] = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        path = manual_path(symbol)
        tmp = path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, path)
    logger.info(f"[CPR backtest] {symbol.upper()}: backfilled {since} -> {last}, "
                f"{len(added)} rows added, {len(skipped)} skipped")
    return {'success': True, 'symbol': symbol.upper(), 'first': first_have.isoformat(), 'since': since.isoformat(),
            'added': list(dict.fromkeys(r['date'] for r in added)), 'skipped': skipped,
            'trades': [{'date': r['date'], 'trade': r['trade'], 'entry': r['entry'],
                        'target': r['target'], 'sl': r['sl'], 'result': r['result'], 'pnl': r['pnl']}
                       for r in added if r['trade']]}


def extend(symbol: str, upto: Optional[date] = None) -> Dict[str, Any]:
    """Append every complete session after the sheet's last date to
    Backtest/cpr_manual/<SYMBOL>.json, read the way the scripts would have
    written it into the workbook. Returns what was added; nothing is
    written when there is nothing to add."""
    doc = load_manual(symbol)
    rows = doc.get('rows') or []
    if not rows:
        raise NoManualData(f'The {symbol.upper()} sheet is empty — nothing to extend from')
    last_have = max(datetime.strptime(r['date'], '%Y-%m-%d').date() for r in rows)
    upto = upto or last_complete_session()
    first = last_have + timedelta(days=1)
    if first > upto:
        return {'success': True, 'symbol': symbol.upper(), 'added': [], 'last': last_have.isoformat()}

    daily, intraday = fetch_bars(symbol, first, upto, fresh=True)
    sessions = sorted(ds for ds in intraday if first <= date.fromisoformat(ds) <= upto)
    added, skipped = [], []
    if sessions:
        res = analyse([{'date': ds} for ds in sessions], daily, intraday)
        for r in res['rows']:
            rows_for = chart_rows(r, intraday.get(r['date']) or [])
            if rows_for:
                added.extend(rows_for)
            else:
                skipped.append({'date': r['date'], 'error': r.get('error')})
    if added:
        doc['rows'] = rows + added
        doc['extended_at'] = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
        path = manual_path(symbol)
        tmp = path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, path)
    logger.info(f"[CPR backtest] {symbol.upper()}: extended {last_have} -> {upto}, "
                f"{len(added)} sessions added, {len(skipped)} skipped")
    return {'success': True, 'symbol': symbol.upper(), 'last': last_have.isoformat(),
            'upto': upto.isoformat(), 'added': list(dict.fromkeys(r['date'] for r in added)), 'skipped': skipped,
            'trades': [{'date': r['date'], 'trade': r['trade'], 'entry': r['entry'],
                        'target': r['target'], 'sl': r['sl'], 'result': r['result'], 'pnl': r['pnl']}
                       for r in added if r['trade']]}
