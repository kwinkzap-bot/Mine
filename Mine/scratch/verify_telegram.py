"""Verify Telegram alerts are configured, by sending a real test message.

Reads env/Mine.env directly and never prints the token. Safe to run any time —
it does NOT call create_app(), so no scheduler or live algo job is started.

    python3 scratch/verify_telegram.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from trading_app.app.utils.user_env import UserEnvManager
from trading_app.service.telegram_service import TelegramService

USER = sys.argv[1] if len(sys.argv) > 1 else 'Mine'

token   = (UserEnvManager.get_user_var(USER, 'TELEGRAM_BOT_TOKEN') or '').strip()
chat_id = (UserEnvManager.get_user_var(USER, 'TELEGRAM_CHAT_ID') or '').strip()
enabled = (UserEnvManager.get_user_var(USER, 'EMA_CONFLUENCE_TELEGRAM') or 'true').strip()

def mask(v):
    return f"set ({len(v)} chars, ...{v[-4:]})" if v else "MISSING"

print(f"env file : env/{USER}.env")
print(f"BOT_TOKEN: {mask(token)}")
print(f"CHAT_ID  : {chat_id or 'MISSING'}")
print(f"EMA_CONFLUENCE_TELEGRAM: {enabled}")

if not token or not chat_id:
    print("\nFAIL — fill both keys in env/%s.env, then re-run." % USER)
    sys.exit(1)
if enabled.lower() == 'false':
    print("\nNOTE — EMA_CONFLUENCE_TELEGRAM=false, so entry alerts stay off "
          "even though credentials are valid.")

print("\nSending test message...")
result = TelegramService(token=token, chat_id=chat_id).send_text(
    "✅ Trading app test — EMA Confluence entry alerts are wired up correctly."
)

if result.get('success'):
    print("PASS — check your Telegram; the message should be there.")
    sys.exit(0)

err = result.get('error', '')
print(f"FAIL — {err}")
if '401' in err:
    print("  -> Token is wrong. Re-copy it from @BotFather.")
elif '400' in err and 'chat not found' in err.lower():
    print("  -> Chat id is wrong, or you have not messaged the bot yet. "
          "Send it any message, then re-read the id from getUpdates.")
elif '403' in err:
    print("  -> You blocked the bot, or it was removed from the group.")
sys.exit(1)
