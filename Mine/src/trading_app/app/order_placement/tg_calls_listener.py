"""The Telegram channel watcher that feeds the call engine.

The channel is a paid one the user is merely a member of, so a bot cannot see
it — the Bot API only delivers from chats the bot has been added to. This is
a *user* session instead: Telethon (MTProto) logged in as the user's own
account, which sees whatever the phone sees. The login is interactive and is
done once, by hand, with ``scripts/tg_login.py``; the session file it leaves
in ``env/`` is a credential in the same sense ``Mine.env`` is, and is
gitignored the same way.

Three things about the shape:

**Push, not poll.** Telethon holds an MTProto connection and hands over each
message the instant it lands — the "every second" watch the user asked for,
without a request budget. It runs on its own asyncio loop on a daemon thread;
the Flask app and the scheduler never touch that loop.

**A new message is the only way in; edits and deletions follow a call that
is already in.** ``NewMessage`` is what takes a call. Every id seen is
written to ``TgCallStore`` before anything else happens, so a reconnect's
catch-up cannot re-fire it; anything older than ``TG_CALLS_MAX_AGE_SECS`` at
receipt is dropped for the same reason. ``MessageEdited`` on a message that
became a call moves that call's numbers (``tg_call_engine.amend_call``) —
never a chat line edited into a call, which fires nothing. ``MessageDeleted``
on one withdraws it (``retract_call``): the entry is cancelled if resting,
the position squared off if held.

**The broker never sees this thread.** A message that parses as a call is
handed to ``tg_call_engine.take_call`` on a fresh worker thread. Everything
that thread does is blocking HTTP; doing it on the asyncio loop would stall
the connection for the length of the slowest broker.
"""

import asyncio
import os
import threading
import time
from datetime import datetime, timezone

from trading_app.app.order_placement import tg_call_engine as engine
from trading_app.app.order_placement.signal_text import parse_signal
from trading_app.app.order_placement.tg_call_store import TgCallStore
from trading_app.app.utils.logger import logger

_OPEN_MIN = 9 * 60 + 5          # the 09:10 start job lands inside this window
_CLOSE_MIN = 15 * 60 + 31
_DEFAULT_MAX_AGE_SECS = 90
_DEFAULT_SESSION = 'env/tg_calls'

_thread = None
_stop_event = threading.Event()
_lock = threading.Lock()
_status = {
    'connected': False, 'authorized': None, 'channel_id': None, 'channel_title': None,
    'last_message_at': None, 'last_error': None, 'seen_today': 0, 'fired_today': 0,
    'started_at': None, 'library': None,
}


def _repo_root() -> str:
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here)))))


def session_path(username) -> str:
    raw = str(engine._uvar(username, 'TG_CALLS_SESSION', _DEFAULT_SESSION)).strip() or _DEFAULT_SESSION
    return raw if os.path.isabs(raw) else os.path.join(_repo_root(), raw)


def credentials(username):
    """``(api_id, api_hash, channel_id)`` or ``(None, reason)``-shaped error."""
    try:
        api_id = int(str(engine._uvar(username, 'TG_API_ID')).strip())
    except (TypeError, ValueError):
        return None, 'TG_API_ID is not set'
    api_hash = str(engine._uvar(username, 'TG_API_HASH')).strip()
    if not api_hash:
        return None, 'TG_API_HASH is not set'
    try:
        raw = str(engine._uvar(username, 'TG_CALLS_CHANNEL_ID')).strip()
        channel_id = abs(int(raw))
        # web.telegram.org shows a channel as -1234 or -1001234; the bare id
        # is what PeerChannel wants.
        if str(channel_id).startswith('100') and len(str(channel_id)) > 12:
            channel_id = int(str(channel_id)[3:])
    except (TypeError, ValueError):
        return None, 'TG_CALLS_CHANNEL_ID is not set'
    return (api_id, api_hash, channel_id), None


def status() -> dict:
    return {**_status, 'running': is_running()}


def _now_mins() -> int:
    now = datetime.now(engine._ops._IST)
    return now.hour * 60 + now.minute


def _in_window() -> bool:
    now = datetime.now(engine._ops._IST)
    return now.weekday() < 5 and _OPEN_MIN <= (now.hour * 60 + now.minute) < _CLOSE_MIN


# ── the pure part ─────────────────────────────────────────────────────────

def handle_text(username, chat_id, message_id, text, msg_date=None, edit_date=None,
                now=None, spawn=True):
    """Decide what one message means. Returns a short reason string for the
    log, and starts the engine on a worker thread when it is a call.

    Pure apart from the seen-ledger write, so the tests drive it directly
    without Telethon: the listener's event handler is a five-line shim over
    this.
    """
    if edit_date:
        return 'edited'
    if not (text or '').strip():
        return 'no text'

    try:
        max_age = int(str(engine._uvar(username, 'TG_CALLS_MAX_AGE_SECS', _DEFAULT_MAX_AGE_SECS)))
    except (TypeError, ValueError):
        max_age = _DEFAULT_MAX_AGE_SECS
    if msg_date is not None:
        now = now or datetime.now(timezone.utc)
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        if (now - msg_date).total_seconds() > max_age:
            return 'stale'

    if not TgCallStore.mark_seen(chat_id, message_id):
        return 'duplicate'
    _status['seen_today'] += 1
    _status['last_message_at'] = time.time()

    plan = parse_signal(text)
    if 'error' in plan:
        logger.debug(f"[TgCalls] {chat_id}/{message_id} not a call: {plan['error']}")
        return 'not a call'

    plan['source_text'] = text[:2000]
    meta = {'message_id': message_id, 'chat_id': chat_id, 'text': text[:2000],
            'date': msg_date.isoformat() if msg_date else None}
    _status['fired_today'] += 1
    logger.info(f"[TgCalls] call in {chat_id}/{message_id}: {plan['symbol']} {plan['strike']} "
                f"{plan['option_type']} entry={plan['entry']} sl={plan['stop']} "
                f"targets={plan['targets']}")
    if spawn:
        threading.Thread(target=engine.take_call, args=(username, plan, meta),
                         name=f'TgCall-{message_id}', daemon=True).start()
    return 'call'


# The channel posts a call and edits it seconds later; the entry from the
# first version may still be on its way to the brokers when the edit lands.
# So an edit or a deletion waits this long for the call to appear in the
# store before giving up on it.
_FOLLOW_UP_WAIT_SECS = 20


def _wait_for_call(chat_id, message_id):
    deadline = time.time() + _FOLLOW_UP_WAIT_SECS
    while True:
        call = TgCallStore.find_by_message(chat_id, message_id)
        if call or time.time() >= deadline:
            return call
        time.sleep(1)


def handle_edited(username, chat_id, message_id, text, spawn=True):
    """An edited message: if it is one of our calls, move the trade to the
    new numbers. A message that was never a call stays that way."""
    def _run():
        call = _wait_for_call(chat_id, message_id)
        if not call:
            logger.info(f"[TgCalls] edit on {chat_id}/{message_id}: not one of our calls — ignored")
            return
        plan = parse_signal(text or '')
        plan['source_text'] = (text or '')[:2000]
        logger.info(f"[TgCalls] edit on {chat_id}/{message_id} → call {call['id']}")
        engine.amend_call(username, call['id'], plan,
                          {'message_id': message_id, 'chat_id': chat_id, 'text': (text or '')[:2000]})

    if spawn:
        threading.Thread(target=_run, name=f'TgEdit-{message_id}', daemon=True).start()
        return 'edit queued'
    _run()
    return 'edit handled'


def handle_deleted(username, chat_id, message_ids, spawn=True):
    """Deleted messages: withdraw every call among them. Telegram does not
    always say which chat a deletion came from, so ``chat_id`` may be None —
    the message id alone is matched then."""
    def _run():
        for mid in message_ids or []:
            call = _wait_for_call(chat_id, mid)
            if not call:
                continue
            logger.info(f"[TgCalls] message {chat_id}/{mid} deleted → withdrawing call {call['id']}")
            engine.retract_call(username, call['id'], reason='message deleted')

    if spawn:
        threading.Thread(target=_run, name='TgDelete', daemon=True).start()
        return 'delete queued'
    _run()
    return 'delete handled'


# ── the Telethon side ─────────────────────────────────────────────────────

async def _main(username, api_id, api_hash, channel_id):
    from telethon import TelegramClient, events
    from telethon.tl.types import PeerChannel

    backoff = 5
    while not _stop_event.is_set() and _now_mins() < _CLOSE_MIN:
        client = TelegramClient(session_path(username), api_id, api_hash,
                                auto_reconnect=True, retry_delay=5, connection_retries=None)
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
            _status['authorized'] = authorized
            if not authorized:
                msg = ('The Telegram session is not logged in. Boot the app out and run '
                       'scripts/tg_login.py once, then bring it back.')
                _status['last_error'] = msg
                logger.error(f"[TgCalls] {msg}")
                engine._alert(username, 'tg_call_listener', 'Telegram calls: not logged in',
                              msg, once_key='unauthorized')
                return

            entity = await client.get_entity(PeerChannel(channel_id))
            title = getattr(entity, 'title', None) or str(channel_id)
            _status.update({'channel_id': channel_id, 'channel_title': title,
                            'connected': True, 'last_error': None})
            logger.info(f"[TgCalls] listener connected ({title})")
            # Off the loop: the first prewarm downloads the symbol master.
            threading.Thread(target=engine.prewarm, args=(username,),
                             name='TgCallPrewarm', daemon=True).start()

            @client.on(events.NewMessage(chats=[entity]))
            async def _on_new_message(event):
                m = event.message
                try:
                    verdict = handle_text(username, event.chat_id, m.id, m.message or '',
                                          msg_date=m.date, edit_date=getattr(m, 'edit_date', None))
                    logger.info(f"[TgCalls] message {m.id}: {verdict}")
                except Exception as e:
                    logger.error(f"[TgCalls] handler failed on {m.id}: {e}", exc_info=True)

            @client.on(events.MessageEdited(chats=[entity]))
            async def _on_edited(event):
                m = event.message
                try:
                    logger.info(f"[TgCalls] message {m.id} edited: "
                                f"{handle_edited(username, event.chat_id, m.id, m.message or '')}")
                except Exception as e:
                    logger.error(f"[TgCalls] edit handler failed on {m.id}: {e}", exc_info=True)

            @client.on(events.MessageDeleted(chats=[entity]))
            async def _on_deleted(event):
                try:
                    logger.info(f"[TgCalls] messages deleted {list(event.deleted_ids)}: "
                                f"{handle_deleted(username, event.chat_id, list(event.deleted_ids))}")
                except Exception as e:
                    logger.error(f"[TgCalls] delete handler failed: {e}", exc_info=True)

            backoff = 5
            # Telethon's own run_until_disconnected would block past the
            # session; this polls so the stop event and the clock are honoured.
            while client.is_connected() and not _stop_event.is_set() and _now_mins() < _CLOSE_MIN:
                await asyncio.sleep(5)
            if not client.is_connected():
                raise ConnectionError('disconnected')
        except Exception as e:
            _status.update({'connected': False, 'last_error': str(e)})
            logger.warning(f"[TgCalls] listener dropped: {e} — retrying in {backoff}s")
            await _sleep_unless_stopped(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            _status['connected'] = False
    logger.info('[TgCalls] listener stopped')


async def _sleep_unless_stopped(secs):
    for _ in range(int(secs)):
        if _stop_event.is_set():
            return
        await asyncio.sleep(1)


def _run(username, api_id, api_hash, channel_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_main(username, api_id, api_hash, channel_id))
    except Exception as e:
        _status['last_error'] = str(e)
        logger.error(f"[TgCalls] listener thread died: {e}", exc_info=True)
    finally:
        loop.close()


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def ensure_running(username, source: str = '') -> bool:
    """Start the listener if it should be up. Idempotent: the start job, the
    watchdog and the startup recovery all call this."""
    global _thread
    with _lock:
        if is_running():
            return True
        if not _in_window():
            return False
        creds, err = credentials(username)
        if err:
            _status['last_error'] = err
            engine._alert(username, 'tg_call_listener', 'Telegram calls: not configured', err,
                          once_key='config')
            return False
        try:
            import telethon  # noqa: F401
            _status['library'] = getattr(telethon, '__version__', 'ok')
        except ImportError:
            err = ('telethon is not installed — ../.venv/bin/pip install "telethon>=1.36" '
                   'and restart after hours')
            _status['last_error'] = err
            engine._alert(username, 'tg_call_listener', 'Telegram calls: telethon missing', err,
                          once_key='telethon')
            return False
        _stop_event.clear()
        _status.update({'started_at': time.time(), 'seen_today': 0, 'fired_today': 0,
                        'last_error': None})
        _thread = threading.Thread(target=_run, args=(username, *creds),
                                   name='tg-calls-listener', daemon=True)
        _thread.start()
        logger.info(f"[TgCalls] listener started ({source or 'manual'})")
        return True


def stop() -> None:
    _stop_event.set()
