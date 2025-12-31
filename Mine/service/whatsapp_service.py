import os
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Sends WhatsApp messages via the WhatsApp Cloud API.

    Environment variables required:
    - WHATSAPP_TOKEN: Permanent/long-lived access token
    - WHATSAPP_PHONE_NUMBER_ID: Phone number ID from WhatsApp Cloud API
    - WHATSAPP_TO_NUMBER: Default recipient in international format (e.g., 91XXXXXXXXXX)
    """

    GRAPH_URL_TEMPLATE = "https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    def __init__(self):
        self.token = os.getenv("WHATSAPP_TOKEN", "").strip()
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        self.default_to = os.getenv("WHATSAPP_TO_NUMBER", "").strip()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def _validate(self) -> Optional[str]:
        if not self.token:
            return "WHATSAPP_TOKEN is missing"
        if not self.phone_number_id:
            return "WHATSAPP_PHONE_NUMBER_ID is missing"
        return None

    def send_text(self, message: str, to_number: Optional[str] = None) -> Dict[str, Any]:
        """Send a simple text message. Returns a dict with success/error."""
        error = self._validate()
        if error:
            logger.error(error)
            return {"success": False, "error": error}

        recipient = (to_number or self.default_to).strip()
        if not recipient:
            return {"success": False, "error": "Recipient number missing"}

        url = self.GRAPH_URL_TEMPLATE.format(phone_number_id=self.phone_number_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": message[:4096]}
        }

        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
            if resp.status_code >= 400:
                logger.error("WhatsApp API error %s: %s", resp.status_code, resp.text)
                return {"success": False, "error": f"API {resp.status_code}: {resp.text}"}
            return {"success": True}
        except requests.RequestException as exc:
            logger.error("WhatsApp send failed: %s", exc)
            return {"success": False, "error": str(exc)}
