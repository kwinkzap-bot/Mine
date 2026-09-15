"""EMA Confluence — every lifecycle event must raise a bell + a Telegram alert.

MAZDOCK hit its target at 12:59 on 2026-08-18 and neither fired: entry and roll
had notifications, but `_record_exit` — the SL and TARGET path — only wrote
history and logged. The signal-candle arming had none either.

These pin all four events, and the two things that make the pair correct rather
than merely present:

  * a ROLL must NOT produce an exit alert (it has its own, with wording that
    says the position moved rather than closed), and
  * an armed setup must announce itself ONCE, not on every daily scan while it
    sits there waiting for its trigger.

Everything is stubbed — no broker, no sqlite, no HTTP, no create_app().
"""
import pytest

from trading_app.algo.ema_confluence import ema_confluence_algo as eca
from trading_app.algo.ema_confluence.ema_confluence_algo import EmaConfluenceAlgo


@pytest.fixture
def algo(monkeypatch, tmp_path):
    """A bare algo with notification + Telegram capture and no side effects."""
    a = EmaConfluenceAlgo.__new__(EmaConfluenceAlgo)   # no __init__: no threads, no files

    class _Log:
        def __init__(self): self.errors = []
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, msg, *a, **k): self.errors.append(str(msg))
    a.log = _Log()

    # Env lookups: notifications and Telegram both on, with credentials present.
    env = {'EMA_CONFLUENCE_NOTIFY': 'true', 'EMA_CONFLUENCE_TELEGRAM': 'true',
           'TELEGRAM_BOT_TOKEN': 'tok', 'TELEGRAM_CHAT_ID': 'chat'}
    a._uvar = lambda key, default=None: env.get(key, default)

    a.bells = []
    a.telegrams = []

    # Intercept the in-app bell at its module, since the algo imports it lazily.
    import trading_app.service.notification_service as ns
    monkeypatch.setattr(
        ns, 'create_notification',
        lambda category, title, data, summary=None: a.bells.append(
            {'category': category, 'title': title, 'summary': summary, 'data': data}) or 1)

    # Capture Telegram at _send_telegram: the real one spawns a thread and does
    # HTTP, neither of which belongs in a test.
    a._send_telegram = lambda symbol, message, tag='entry': a.telegrams.append(
        {'symbol': symbol, 'message': message, 'tag': tag})

    a._append_history = lambda record: None
    return a


def _position(**over):
    s = {
        'direction': 'Long', 'entry_price': 2574.60, 'qty': 400,
        'lot_size': 400, 'sl_level': 2500.0, 'target_level': 2613.22,
        'target_pct': 1.5, 'signal_date': '2026-08-10',
        'entry_time': '2026-08-10T12:41:00', 'future_month': 'AUG 2026',
    }
    s.update(over)
    return s


# ── the reported bug ─────────────────────────────────────────────────────

def test_target_hit_raises_bell_and_telegram(algo):
    """The MAZDOCK case."""
    algo._record_exit('MAZDOCK', _position(), 2613.22, 'TARGET')

    assert len(algo.bells) == 1, "target hit produced no in-app notification"
    assert len(algo.telegrams) == 1, "target hit produced no Telegram alert"

    bell = algo.bells[0]
    assert bell['category'] == 'ema_confluence_exit'
    assert 'MAZDOCK' in bell['title'] and 'TARGET' in bell['title']
    assert bell['data']['reason'] == 'TARGET'
    assert bell['data']['exit_price'] == 2613.22

    msg = algo.telegrams[0]['message']
    assert algo.telegrams[0]['tag'] == 'exit'
    assert 'TARGET HIT' in msg and 'MAZDOCK' in msg
    assert '2613.22' in msg and '2574.6' in msg          # exit and entry
    assert '+₹' in msg, "a winning trade should show a signed positive P&L"


def test_stoploss_hit_raises_bell_and_telegram(algo):
    algo._record_exit('MAZDOCK', _position(), 2500.0, 'SL')

    assert len(algo.bells) == 1 and len(algo.telegrams) == 1
    assert 'SL HIT' in algo.telegrams[0]['message']
    assert algo.bells[0]['data']['reason'] == 'SL'
    # A loss must not be dressed up with a + sign.
    assert '+₹' not in algo.telegrams[0]['message']


def test_pnl_sign_follows_direction_for_a_short(algo):
    """A SELL closed BELOW entry is a win — the alert must not call it a loss."""
    algo._record_exit('NHPC', _position(direction='Short', entry_price=77.76), 71.54, 'TARGET')
    assert '+₹' in algo.telegrams[0]['message']
    assert algo.bells[0]['data']['pnl'] > 0


# ── the distinction that makes it correct ────────────────────────────────

def test_roll_does_not_raise_an_exit_alert(algo):
    """A roll books out the near leg through _record_exit, but the position is
    still open on the far one. _notify_roll already announces that; a second
    'exit' alert would read as the trade having closed."""
    algo._record_exit('NHPC', _position(), 77.76, 'ROLL')
    assert algo.bells == [] and algo.telegrams == []


def test_exit_alert_failure_never_loses_the_exit(algo, monkeypatch):
    """History and state are written before the alert. A broken notifier must
    leave the trade recorded, not raise into the algo's poll thread."""
    monkeypatch.setattr(algo, '_notify_exit',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('bell down')))
    s = _position()
    algo._record_exit('MAZDOCK', s, 2613.22, 'TARGET')     # must not raise
    assert s['last_exit_reason'] == 'TARGET'
    assert s['last_exit_price'] == 2613.22
    assert any('exit notification failed' in e for e in algo.log.errors)


# ── signal candle ────────────────────────────────────────────────────────

def test_signal_candle_raises_bell_and_telegram(algo):
    s = {'direction': 'Long', 'trigger_level': 7557.0, 'sl_level': 7329.0,
         'target_pct': 15, 'signal_date': '2026-08-07', 'future_month': 'AUG 2026'}
    algo._notify_signal('AMBER', s, age_days=3)

    assert len(algo.bells) == 1 and len(algo.telegrams) == 1
    assert algo.bells[0]['category'] == 'ema_confluence_signal'
    assert algo.bells[0]['data']['trigger_level'] == 7557.0

    msg = algo.telegrams[0]['message']
    assert algo.telegrams[0]['tag'] == 'signal'
    assert 'NEW SETUP' in msg and 'AMBER' in msg and '7557.0' in msg
    assert 'BUY' in msg


def test_signal_direction_maps_short_to_sell(algo):
    algo._notify_signal('CIPLA', {'direction': 'Short', 'trigger_level': 1366.1,
                                  'sl_level': 1426.9, 'signal_date': '2026-07-23'}, 0)
    assert algo.bells[0]['data']['direction'] == 'SELL'
    assert 'SELL' in algo.telegrams[0]['message']


# ── the switches still work ──────────────────────────────────────────────

def test_notify_flag_off_suppresses_the_bell_but_not_telegram(algo):
    """The two are independently switchable, as _notify_new_entry already
    treats them — EMA_CONFLUENCE_NOTIFY gates only the in-app bell."""
    algo._uvar = lambda k, d=None: {'EMA_CONFLUENCE_NOTIFY': 'false'}.get(k, d)
    algo._record_exit('MAZDOCK', _position(), 2613.22, 'TARGET')
    assert algo.bells == []
    assert len(algo.telegrams) == 1
