"""One-time Telegram login for the call listener.

    PYTHONPATH=src ../.venv/bin/python scripts/tg_login.py

Reads TG_API_ID / TG_API_HASH / TG_CALLS_CHANNEL_ID / TG_CALLS_SESSION from
env/<MONITORING_USERNAME>.env, walks Telethon's interactive login (phone,
the code Telegram sends, the 2FA password if set), then proves the session
works: it resolves the channel, prints its title, and runs the last few
messages through the same parser the listener uses so a bad channel id or a
format the parser does not read shows up here, tonight, and not at 09:10
tomorrow.

Run it while the app is booted out — Telethon's session is a SQLite file and
two processes on it corrupt it:

    launchctl bootout gui/$(id -u)/com.mine.livealgo
    ... this script ...
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mine.livealgo.plist

Does NOT build the Flask app (create_app() starts the scheduler and the live
algos — see CLAUDE.md).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trading_app.app.order_placement import tg_calls_listener as listener  # noqa: E402
from trading_app.app.order_placement.signal_text import parse_signal  # noqa: E402


async def main() -> int:
    username = os.getenv('MONITORING_USERNAME', 'Mine')
    creds, err = listener.credentials(username)
    if err:
        print(f"✗ {err} — add it to env/{username}.env first")
        return 1
    api_id, api_hash, channel_id = creds

    try:
        from telethon import TelegramClient
        from telethon.tl.types import PeerChannel
    except ImportError:
        print('✗ telethon is not installed: ../.venv/bin/pip install "telethon>=1.36"')
        return 1

    path = listener.session_path(username)
    print(f"session file: {path}.session")
    client = TelegramClient(path, api_id, api_hash)
    await client.start()                       # interactive: phone → code → 2FA
    me = await client.get_me()
    print(f"✓ logged in as {me.first_name or ''} {me.last_name or ''} (@{me.username or '-'})")

    try:
        entity = await client.get_entity(PeerChannel(channel_id))
    except Exception as e:
        print(f"✗ could not open channel {channel_id}: {e}")
        print("  The id is the number after '#-' in web.telegram.org/k/#-<id>, without the sign.")
        await client.disconnect()
        return 1
    print(f"✓ channel {channel_id}: {getattr(entity, 'title', '?')}")

    print("\nlast 5 messages through the parser:")
    async for m in client.iter_messages(entity, limit=5):
        text = (m.message or '').strip().replace('\n', ' | ')
        plan = parse_signal(m.message or '')
        verdict = (f"CALL {plan['symbol']} {plan['strike']} {plan['option_type']} "
                   f"entry={plan['entry']} sl={plan['stop']} targets={plan['targets']}"
                   if 'error' not in plan else f"skip ({plan['error'][:50]})")
        print(f"  #{m.id} {m.date:%d-%b %H:%M} {'[edited] ' if m.edit_date else ''}{text[:60]!r}\n"
              f"      → {verdict}")

    await client.disconnect()
    print("\nDone. Back the session file up with Mine.env, then bootstrap the app.")
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
