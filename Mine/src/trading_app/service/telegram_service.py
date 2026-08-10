import os
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)


class TelegramService:
    """Sends messages via the Telegram Bot API.

    Environment variables required:
    - TELEGRAM_BOT_TOKEN: bot token issued by @BotFather
    - TELEGRAM_CHAT_ID: destination chat — your own user id for a DM, or a
      group/channel id (negative) to fan the alert out to several people

    Either may also be passed explicitly, for callers whose credentials live in
    a per-user env file (env/{username}.env) rather than in os.environ — the
    live algos read theirs through UserEnvManager, which never touches the
    process environment.

    A bot can message any chat that has ever started it, at any time, with no
    send window to keep open and no per-message billing — so an alert fired at
    an arbitrary point in the trading day needs no upkeep to get through.
    """

    API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

    MAX_LEN = 4096      # Telegram rejects anything longer outright

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = (token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    def _validate(self) -> Optional[str]:
        if not self.token:
            return "TELEGRAM_BOT_TOKEN is missing"
        return None

    def send_text(self, message: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a plain-text message. Returns a dict with success/error."""
        error = self._validate()
        if error:
            logger.error(error)
            return {"success": False, "error": error}

        recipient = (chat_id or self.chat_id).strip()
        if not recipient:
            return {"success": False, "error": "TELEGRAM_CHAT_ID is missing"}

        url = self.API_URL_TEMPLATE.format(token=self.token)
        payload = {
            "chat_id": recipient,
            "text": message[:self.MAX_LEN],
            # No parse_mode: alert text carries symbols like * and _ that would
            # otherwise be read as Markdown and get the whole send rejected.
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code >= 400:
                # resp.text only — the URL embeds the bot token, so it must
                # never reach the logs.
                logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
                return {"success": False, "error": f"API {resp.status_code}: {resp.text}"}
            return {"success": True}
        except requests.RequestException as exc:
            logger.error("Telegram send failed: %s", exc)
            return {"success": False, "error": str(exc)}
