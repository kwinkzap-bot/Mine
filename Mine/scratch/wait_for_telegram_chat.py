"""Poll getUpdates until you message the bot, then write TELEGRAM_CHAT_ID
into env/Mine.env automatically.

Run it, then press START in the bot chat. Exits as soon as it sees you.
The token is read from the env file and never printed in full.

    python3 scratch/wait_for_telegram_chat.py [username] [timeout_secs]
"""
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from trading_app.app.utils.user_env import UserEnvManager

USER = sys.argv[1] if len(sys.argv) > 1 else 'Mine'
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 300
ENV_PATH = os.path.join(ROOT, 'env', f'{USER}.env')

token = (UserEnvManager.get_user_var(USER, 'TELEGRAM_BOT_TOKEN') or '').strip()
if not token:
    print(f"TELEGRAM_BOT_TOKEN is empty in env/{USER}.env")
    sys.exit(1)


def find_chat():
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    if resp.status_code >= 400:
        print(f"API {resp.status_code}: {resp.text}")
        sys.exit(1)
    for upd in resp.json().get('result', []):
        for key in ('message', 'edited_message', 'channel_post', 'my_chat_member'):
            chat = (upd.get(key) or {}).get('chat')
            if chat:
                name = chat.get('title') or ' '.join(filter(None, [
                    chat.get('first_name'), chat.get('last_name')])) or '(no name)'
                return chat['id'], f"{name} [{chat.get('type')}]"
    return None, None


print(f"Waiting up to {TIMEOUT}s for a message to the bot...")
print("  -> open  t.me/Mine_Auto_Signal_Bot  and press START (or send any text)\n")

deadline = time.time() + TIMEOUT
chat_id = label = None
while time.time() < deadline:
    chat_id, label = find_chat()
    if chat_id is not None:
        break
    time.sleep(3)

if chat_id is None:
    print(f"Timed out after {TIMEOUT}s — no message reached the bot.")
    sys.exit(1)

print(f"Got it: chat id {chat_id}  <- {label}")

# Fill in the existing (blank) TELEGRAM_CHAT_ID line, leaving everything
# else in the env file byte-for-byte untouched.
with open(ENV_PATH, 'r') as f:
    content = f.read()

new_content, n = re.subn(r'^TELEGRAM_CHAT_ID=.*$',
                         f'TELEGRAM_CHAT_ID={chat_id}',
                         content, count=1, flags=re.MULTILINE)
if n != 1:
    print(f"Could not find a TELEGRAM_CHAT_ID line in env/{USER}.env — "
          f"add this manually:  TELEGRAM_CHAT_ID={chat_id}")
    sys.exit(1)

with open(ENV_PATH, 'w') as f:
    f.write(new_content)
print(f"Wrote TELEGRAM_CHAT_ID={chat_id} to env/{USER}.env")
print("\nNow run:  python3 scratch/verify_telegram.py")
