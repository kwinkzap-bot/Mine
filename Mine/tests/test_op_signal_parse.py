"""Reading a pasted tip.

Pure, so these are cheap and there are a lot of them. That is deliberate: the
parser is the one place where a wrong answer is silent. A refused parse shows
up on the screen immediately; a tip read as the wrong strike, or with a target
quietly dropped, arms a real order and looks fine doing it.

Every ladder spelling here is one that has actually turned up in a tip.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_app.app.order_placement.signal_text import parse_signal  # noqa: E402


TIP = """NIFTY 23500 CE (15-SEP-2026)
BUY : 193
SL : 175
Target :206,213,220"""


def test_the_tip_from_the_screenshot_parses_exactly():
    assert parse_signal(TIP) == {
        'symbol': 'NIFTY', 'strike': 23500, 'option_type': 'CE',
        'expiry': date(2026, 9, 15), 'action': 'BUY',
        'entry': 193.0, 'stop': 175.0, 'targets': [206.0, 213.0, 220.0],
    }


# ── the ladder, spelled every way it arrives ──────────────────────────────

def _ladder(text):
    return parse_signal('NIFTY 23500 CE\nBUY 193\nSL 175\n' + text)['targets']


def test_targets_separated_by_commas():
    assert _ladder('Target : 206,213,220') == [206.0, 213.0, 220.0]


def test_targets_separated_by_spaces():
    assert _ladder('TGT 206 285 300') == [206.0, 285.0, 300.0]


def test_targets_numbered_on_their_own_lines():
    assert _ladder('Target 1 : 206\nTarget 2 : 213\nTarget 3 : 220') == \
        [206.0, 213.0, 220.0]


def test_targets_numbered_inline():
    assert _ladder('T1: 206 T2: 213 T3: 220') == [206.0, 213.0, 220.0]


def test_a_numbered_first_target_does_not_swallow_the_rest():
    """"Target 1 : 206, 213, 220" numbers only the first — all three are real.

    Trusting the index here would drop two targets and arm a shorter ladder
    than the tip asked for, without saying so.
    """
    assert _ladder('Target 1 : 206, 213, 220') == [206.0, 213.0, 220.0]


def test_a_ladder_wrapped_onto_a_second_line():
    assert _ladder('Targets:\n206, 213\n220') == [206.0, 213.0, 220.0]


def test_a_bare_number_after_the_label_is_a_target_not_an_index():
    """"TGT 270 ..." — 270 is the first target, not "target number 270"."""
    assert _ladder('TGT 270 285 300') == [270.0, 285.0, 300.0]


def test_one_and_two_target_tips_are_read_as_given():
    assert _ladder('Target : 206') == [206.0]
    assert _ladder('Target : 206, 213') == [206.0, 213.0]


def test_more_than_three_targets_is_refused_rather_than_trimmed():
    """Which three were meant is not the parser's guess to make."""
    out = parse_signal('NIFTY 23500 CE\nBUY 193\nSL 175\nTgt 206 213 220 228')
    assert 'error' in out and '4 targets' in out['error']


# ── the contract ──────────────────────────────────────────────────────────

def test_a_put_tip():
    out = parse_signal('NIFTY 23500 PE\nBUY 193\nSL 175\nTarget 206,213,220')
    assert out['option_type'] == 'PE'


def test_banknifty_is_not_read_as_nifty():
    out = parse_signal('BANKNIFTY 52000 CE\nBUY 410\nSL 380\nTgt 430 450 470')
    assert out['symbol'] == 'BANKNIFTY' and out['strike'] == 52000


def test_bank_nifty_spelled_with_a_space():
    out = parse_signal('BANK NIFTY 52000 CE\nBUY 410\nSL 380\nTgt 430 450 470')
    assert out['symbol'] == 'BANKNIFTY'


def test_sensex():
    out = parse_signal('SENSEX 81000 CE\nEntry 250\nSL 220\nTGT 270 285 300')
    assert out['symbol'] == 'SENSEX' and out['strike'] == 81000


def test_a_strike_written_with_a_thousands_separator():
    """"23,500 CE" is one strike. The comma between targets is not."""
    out = parse_signal('NIFTY 23,500 CE\nBUY 193\nSL 175\nTarget 206,213,220')
    assert out['strike'] == 23500
    assert out['targets'] == [206.0, 213.0, 220.0]


def test_the_option_type_written_before_the_strike():
    out = parse_signal('NIFTY CE 23500\nBUY 193\nSL 175\nTgt 206 213 220')
    assert (out['strike'], out['option_type']) == (23500, 'CE')


# ── the entry and the stop ────────────────────────────────────────────────

def test_a_sell_tip_is_read_as_a_sell():
    out = parse_signal('NIFTY 23500 CE\nSELL BELOW 410.5\nSTOP LOSS : 455\n'
                       'T1: 380 T2: 350 T3: 310')
    assert out['action'] == 'SELL'
    assert out['entry'] == 410.5 and out['stop'] == 455.0


def test_a_fractional_premium_keeps_its_paise():
    out = parse_signal('NIFTY 23500 CE\nBUY 193.45\nSL 175.05\nTgt 206 213 220')
    assert out['entry'] == 193.45 and out['stop'] == 175.05


def test_buy_above_and_stoploss_spellings():
    out = parse_signal('NIFTY 23500 CE\nBUY ABOVE 193\nSTOPLOSS 175\n'
                       'Tgt 206 213 220')
    assert out['action'] == 'BUY' and out['entry'] == 193.0 and out['stop'] == 175.0


# ── the expiry ────────────────────────────────────────────────────────────

def test_an_expiry_written_several_ways():
    for text, want in (('(15-SEP-2026)', date(2026, 9, 15)),
                       ('(15 SEP 26)', date(2026, 9, 15)),
                       ('(2026-09-15)', date(2026, 9, 15)),
                       ('(15/09/2026)', date(2026, 9, 15))):
        out = parse_signal(f'NIFTY 23500 CE {text}\nBUY 193\nSL 175\n'
                           f'Tgt 206 213 220')
        assert out['expiry'] == want, text


def test_a_tip_without_an_expiry_is_not_an_error():
    """Most tips name none — the front weekly is implied."""
    out = parse_signal('NIFTY 23500 CE\nBUY 193\nSL 175\nTgt 206 213 220')
    assert 'error' not in out and out['expiry'] is None


# ── what it refuses ───────────────────────────────────────────────────────

def test_each_missing_piece_is_named():
    for text, want in (
        ('', 'Paste the signal'),
        ('good morning everyone', 'No underlying'),
        ('NIFTY tips today', 'No strike'),
        ('NIFTY 23500 CE\nSL 175\nTgt 206', 'No entry price'),
        ('NIFTY 23500 CE\nBUY 193\nTgt 206', 'No stop-loss'),
        ('NIFTY 23500 CE\nBUY 193\nSL 175', 'No targets'),
    ):
        out = parse_signal(text)
        assert 'error' in out and want in out['error'], (text, out)


def test_garbage_never_raises():
    for text in (None, '', '   ', '\x00\x01', 'CE PE CE PE', '।।।।',
                 'NIFTY ' * 5000, '23500', 'BUY SL TARGET'):
        out = parse_signal(text)
        assert isinstance(out, dict)


def test_surrounding_chatter_does_not_change_the_plan():
    out = parse_signal("""Good morning traders 🙏
    Today's call, small quantity only please.
    NIFTY 23500 CE (15-SEP-2026)
    BUY : 193
    SL : 175
    Target :206,213,220
    Book partial and trail. Not a recommendation.""")
    assert out['strike'] == 23500 and out['entry'] == 193.0
    assert out['targets'] == [206.0, 213.0, 220.0]
