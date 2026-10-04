"""WhatsApp delivery through Meta's Cloud API, or a mock that records messages locally.

The real sender is used when WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID are both set:

    POST https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages
    Authorization: Bearer {WHATSAPP_TOKEN}

Deal messages are business-initiated, so WhatsApp requires a pre-approved *template*
(free text is only allowed within 24 hours of the customer messaging you). Create a
template in WhatsApp Manager whose body has three variables, for example:

    "Hi {{1}}! Today at our store: {{2}}. {{3}} Reply STOP to unsubscribe."

and give its name when creating a campaign: {{1}} = customer name, {{2}} = product,
{{3}} = the offer text.

Incoming messages arrive at /webhooks/whatsapp. Requests are verified with
WHATSAPP_VERIFY_TOKEN (subscription) and the X-Hub-Signature-256 HMAC (app secret,
WHATSAPP_APP_SECRET), and a "STOP" from a customer opts them out.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass

import httpx

OPT_OUT_WORDS = {"stop", "unsubscribe", "stop all", "cancel", "opt out", "optout"}


@dataclass
class SendResult:
    ok: bool
    provider_id: str = ""
    error: str = ""


class MockSender:
    """Records what would be sent. Used until WhatsApp credentials are configured."""

    name = "mock (nothing is actually sent)"

    def __init__(self):
        self.outbox: list[dict] = []

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> SendResult:
        self.outbox.append({"to": to, "template": template, "language": language, "params": params})
        return SendResult(True, provider_id=f"mock-{uuid.uuid4().hex[:12]}")


class CloudAPISender:
    name = "WhatsApp Cloud API"

    def __init__(self, token: str, phone_number_id: str, version: str = "v25.0", client: httpx.Client | None = None):
        self.url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = client or httpx.Client(timeout=15)

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> SendResult:
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
            },
        }
        try:
            r = self.client.post(self.url, json=body, headers=self.headers)
        except httpx.HTTPError as exc:
            return SendResult(False, error=f"network: {exc}"[:300])
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                detail = r.text
            return SendResult(False, error=f"HTTP {r.status_code}: {detail}"[:300])
        try:
            return SendResult(True, provider_id=r.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError):
            return SendResult(False, error="unexpected response from WhatsApp")


def sender_from_env():
    token, phone_id = os.getenv("WHATSAPP_TOKEN", "").strip(), os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if token and phone_id:
        return CloudAPISender(token, phone_id, os.getenv("WHATSAPP_API_VERSION", "v25.0"))
    return MockSender()


def normalize_phone(raw: str, default_country: str = "91") -> str:
    """To E.164. Ten-digit numbers get the default country code (India)."""
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+"):
        pass
    elif len(digits) == 10:
        digits = default_country + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = default_country + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValueError(f"not a valid phone number: {raw!r}")
    return "+" + digits


def signature_valid(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def inbound_messages(payload: dict) -> list[tuple[str, str]]:
    """(from_phone, text) pairs from a Cloud API webhook payload."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []) or []:
                text = (m.get("text") or {}).get("body", "") or (m.get("button") or {}).get("text", "")
                if m.get("from"):
                    out.append(("+" + m["from"].lstrip("+"), text))
    return out


def is_opt_out(text: str) -> bool:
    return text.strip().lower() in OPT_OUT_WORDS
