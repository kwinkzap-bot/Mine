"""Print the chat id(s) that have messaged your Telegram bot.

Reads TELEGRAM_BOT_TOKEN from env/Mine.env and calls getUpdates for you, so the
token never has to go in a browser URL bar. The token itself is never printed.

    python3 scratch/get_telegram_chat_id.py
"""
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from trading_app.app.utils.user_env import UserEnvManager

USER = sys.argv[1] if len(sys.argv) > 1 else 'Mine'

token = (UserEnvManager.get_user_var(USER, 'TELEGRAM_BOT_TOKEN') or '').strip()
if not token:
    print(f"TELEGRAM_BOT_TOKEN is empty in env/{USER}.env — paste the token "
          f"from @BotFather there first, then re-run.")
    sys.exit(1)

print(f"Token found ({len(token)} chars, ...{token[-4:]}). Calling getUpdates...\n")

try:
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
except requests.RequestException as exc:
    print(f"Could not reach api.telegram.org: {exc}")
    sys.exit(1)

if resp.status_code == 401:
    print("401 Unauthorized — the token is wrong or has been revoked. "
          "Re-copy it from @BotFather (/mybots -> API Token).")
    sys.exit(1)
if resp.status_code >= 400:
    print(f"API {resp.status_code}: {resp.text}")
    sys.exit(1)

results = resp.json().get('result', [])
if not results:
    print("The bot has no messages yet, so Telegram has nothing to report.")
    print("Open  t.me/Mine_Auto_Signal_Bot  , press START (or send any text),")
    print("then run this script again.")
    sys.exit(1)

# Any update type can carry a chat — message, edited_message, channel_post, ...
seen = {}
for upd in results:
    for key in ('message', 'edited_message', 'channel_post', 'my_chat_member'):
        chat = (upd.get(key) or {}).get('chat')
        if chat:
            name = chat.get('title') or ' '.join(
                filter(None, [chat.get('first_name'), chat.get('last_name')])
            ) or chat.get('username') or '(no name)'
            seen[chat['id']] = f"{name} [{chat.get('type')}]"

print(f"Found {len(seen)} chat(s):\n")
for chat_id, label in seen.items():
    print(f"    TELEGRAM_CHAT_ID={chat_id}        <- {label}")

print(f"\nPut the line you want into env/{USER}.env, then run:")
print("    python3 scratch/verify_telegram.py")
