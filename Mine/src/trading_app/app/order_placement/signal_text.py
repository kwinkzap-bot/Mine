"""Turn a pasted tip into a plan.

The tips arrive as free text off a phone — a screenshot's worth of WhatsApp:

    NIFTY 23500 CE (15-SEP-2026)
    BUY : 193
    SL : 175
    Target :206,213,220

Nothing about that shape is a standard, so this parses generously and then
says exactly what it could not find. It is deliberately pure: no clock, no
broker, no config. Whether the contract is one this page trades, whether the
expiry is the front one, and whether the ladder makes sense against the live
premium are all questions for ``validate_plan`` in the engine, which can see
the chain. This module's only job is reading the words.

Parsing lives here, on the server, rather than in the page's JavaScript. The
page is one caller of the arm route, and a parser bug is not a wrong label —
it is a live order at the wrong strike.
"""

import re
from datetime import date

# The underlyings a tip may name, longest alias first so BANKNIFTY is never
# read as NIFTY with a stray "BANK" in front of it.
_SYMBOL_ALIASES = (
    ('BANKNIFTY', 'BANKNIFTY'),
    ('BANK NIFTY', 'BANKNIFTY'),
    ('NIFTY BANK', 'BANKNIFTY'),
    ('NIFTYBANK', 'BANKNIFTY'),
    ('FINNIFTY', 'FINNIFTY'),
    ('MIDCPNIFTY', 'MIDCPNIFTY'),
    ('SENSEX', 'SENSEX'),
    ('BANKEX', 'BANKEX'),
    ('NIFTY50', 'NIFTY'),
    ('NIFTY', 'NIFTY'),
)

_MONTHS = {m: i for i, m in enumerate(
    ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
     'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), start=1)}

# A number that could be a premium: 193, 193.5, 193.05.
_NUM = r'\d+(?:\.\d+)?'

# The same, but tolerating an Indian thousands separator: 1,750 and 23,500.
# Used only where a comma cannot mean anything else — a strike is followed by
# CE/PE, and an entry or stop is one number on its own line. It is deliberately
# NOT used for the target ladder, where the comma in "206,213,220" is the
# separator between three targets and stripping it yields the number 206213220.
_NUM_TH = r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?'


def _num(raw: str) -> float:
    return float(str(raw).replace(',', ''))

# "BUY 193", "BUY : 193", "BUY ABOVE 193", "ENTRY-193", "CMP 193".
# The word must start a line or follow a separator, so the BUY inside a broker
# name or a comment cannot supply an entry price.
_ENTRY_RE = re.compile(
    rf'(?:^|[\n,;|])\s*(BUY|SELL|ENTRY|BUY\s*ABOVE|SELL\s*BELOW|CMP)\b'
    rf'\s*(?:ABOVE|BELOW|AT|@)?\s*[:\-=]?\s*({_NUM_TH})', re.M)

_STOP_RE = re.compile(
    rf'\b(?:SL|S\.L|STOP\s*LOSS|STOPLOSS|STOP)\b\s*[:\-=]?\s*({_NUM_TH})')

# The label that opens the ladder. Everything after it on that line — and on
# the lines that follow, while they are still bare numbers — is a target.
# The trailing index in "Target 1 : 206" is part of the label, but the 270 in
# "TGT 270 285 300" is the first target — so an index is only swallowed when a
# separator follows it.
_TARGET_LABEL_RE = re.compile(
    r'\b(?:TARGETS?|TGTS?|TP)\b\s*(?:[123]\s*(?=[:\-=]))?\s*[:\-=]?')

# "T1 : 206  T2 : 213  T3 : 220", and its long form "Target 1 : 206" on three
# separate lines. _NUM and not _NUM_TH: in "T1 : 206,213" the comma separates
# two targets, and reading it as one thousands-separated number gives 206213.
_NUMBERED_TARGET_RE = re.compile(
    rf'\b(?:TARGETS?|TGTS?|TP|T)\s*([123])\s*[:\-=]\s*({_NUM})')

# 15-SEP-2026, 15 SEP 26, 15SEP2026, 15/09/2026, 2026-09-15.
_DATE_RES = (
    re.compile(r'\b(\d{1,2})[\s\-/.]*([A-Z]{3,9})[\s\-/.]*(\d{2,4})\b'),
    re.compile(r'\b(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})\b'),
    re.compile(r'\b(\d{1,2})[\-/](\d{1,2})[\-/](\d{2,4})\b'),
)


def _clean(text: str) -> str:
    """Uppercase, normalise the punctuation a phone keyboard produces."""
    s = (text or '').upper()
    s = s.replace(' ', ' ').replace('–', '-').replace('—', '-')
    s = s.replace('‘', "'").replace('’', "'")
    # Commas are left alone on purpose. In "23,500 CE" one is a thousands
    # separator; in "206,213,220" three targets are separated by them. Only the
    # patterns that can tell the two apart strip them — see _NUM_TH.
    # Collapse runs of spaces and tabs, but never newlines: the line breaks are
    # what keep "BUY : 193" from running into the line above it.
    return re.sub(r'[^\S\n]+', ' ', s)


def _find_symbol(s: str):
    """(symbol, index) for the first underlying named, or (None, -1)."""
    best, best_at = None, -1
    for alias, canonical in _SYMBOL_ALIASES:
        at = s.find(alias)
        if at < 0:
            continue
        if best_at < 0 or at < best_at:
            best, best_at = canonical, at
        elif at == best_at and len(alias) > len(best or ''):
            best = canonical
    return best, best_at


def _find_contract(s: str, sym_at: int):
    """(strike, CE|PE) read from the text right after the underlying.

    Both orders appear in the wild — "23500 CE" and "CE 23500" — and so does
    the run-together "23500CE".
    """
    tail = s[sym_at:sym_at + 120] if sym_at >= 0 else s
    m = re.search(r'(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(CE|PE)\b', tail) or \
        re.search(r'\b(CE|PE)\s*(\d{1,3}(?:,\d{3})+|\d{3,6})\b', tail)
    if not m:
        return None, None
    a, b = m.group(1), m.group(2)
    return (int(_num(a)), b) if a[0].isdigit() else (int(_num(b)), a)


def _find_expiry(s: str):
    """The date in the tip, as a ``date``, or None if it names none.

    A tip without an expiry is normal and not an error — most name the current
    weekly only implicitly. A tip that names one is checked against the live
    chain later, because the order path always trades the nearest expiry.
    """
    for rx in _DATE_RES:
        for m in rx.finditer(s):
            g = m.groups()
            try:
                if rx is _DATE_RES[0]:
                    day, mon, yr = int(g[0]), _MONTHS.get(g[1][:3]), int(g[2])
                    if not mon:
                        continue
                elif rx is _DATE_RES[1]:
                    yr, mon, day = int(g[0]), int(g[1]), int(g[2])
                else:
                    day, mon, yr = int(g[0]), int(g[1]), int(g[2])
                if yr < 100:
                    yr += 2000
                return date(yr, mon, day)
            except (TypeError, ValueError):
                continue
    return None


def _find_targets(s: str):
    """The ladder, in the order the tip gives it, de-duplicated."""
    # Two or more indices means the tip really is numbering its targets. One
    # alone does not: "Target 1 : 206, 213, 220" numbers only the first, and
    # trusting the index there would silently drop the other two.
    numbered = {n: v for n, v in _NUMBERED_TARGET_RE.findall(s)}
    if len(numbered) >= 2:
        return [_num(numbered[n]) for n in sorted(numbered)]

    m = _TARGET_LABEL_RE.search(s)
    if not m:
        return []

    # The rest of the label's line, plus any following line that is nothing
    # but numbers — tips wrap a long ladder onto a second line.
    rest = s[m.end():]
    lines = rest.split('\n')
    taken = [lines[0]]
    for line in lines[1:]:
        if line.strip() and re.fullmatch(rf'[\s,/|&+-]*(?:{_NUM}[\s,/|&+-]*)+', line):
            taken.append(line)
        else:
            break

    out = []
    for value in re.findall(_NUM, ' '.join(taken)):
        v = float(value)
        if v > 0 and v not in out:
            out.append(v)
    return out


def parse_signal(text: str) -> dict:
    """Read a tip. Returns a plan, or ``{'error': ...}``. Never raises.

    The plan is ``{symbol, strike, option_type, expiry, action, entry, stop,
    targets}``, with ``expiry`` a ``date`` or None. Values are what the tip
    said, unjudged: this reports that it read 193, not that 193 is a sensible
    place to buy.
    """
    try:
        if not (text or '').strip():
            return {'error': 'Paste the signal text first'}

        s = _clean(text)

        symbol, sym_at = _find_symbol(s)
        if not symbol:
            return {'error': 'No underlying in the text — expected NIFTY, '
                             'BANKNIFTY or SENSEX'}

        strike, option_type = _find_contract(s, sym_at)
        if not strike:
            return {'error': f'No strike and CE/PE found after {symbol}'}

        entry_m = _ENTRY_RE.search(s)
        if not entry_m:
            return {'error': 'No entry price — expected a line like "BUY : 193"'}
        word = re.sub(r'\s+', ' ', entry_m.group(1)).strip()
        action = 'SELL' if word.startswith('SELL') else 'BUY'
        entry = _num(entry_m.group(2))

        stop_m = _STOP_RE.search(s)
        if not stop_m:
            return {'error': 'No stop-loss — expected a line like "SL : 175"'}
        stop = _num(stop_m.group(1))

        targets = _find_targets(s)
        if not targets:
            return {'error': 'No targets — expected a line like '
                             '"Target : 206, 213, 220"'}
        if len(targets) > 3:
            # Trading the first three is a guess about which three were meant.
            return {'error': f'{len(targets)} targets found ({", ".join(str(t) for t in targets)}) '
                             f'— this page trades three. Trim the list.'}

        return {
            'symbol': symbol,
            'strike': strike,
            'option_type': option_type,
            'expiry': _find_expiry(s),
            'action': action,
            'entry': entry,
            'stop': stop,
            'targets': targets,
        }
    except Exception as e:                       # never raise at a paste
        return {'error': f'Could not read that text ({e})'}
