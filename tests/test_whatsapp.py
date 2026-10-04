"""Deal campaigns, the WhatsApp Cloud API sender, and the opt-out webhook."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from sqlalchemy import select

from app.db import Campaign, Customer, Message
from app.services import campaigns as camp
from app.services.whatsapp import CloudAPISender, SendResult, normalize_phone


def add(c, tok, name, phone):
    c.post("/customers/add", data={"name": name, "phone": phone, "consent": "yes", "consent_source": "counter",
                                   "csrf": tok})


def new_campaign(c, tok, **kw) -> int:
    r = c.post("/campaigns/new", data={"name": "Milk offer", "offer": "10% off today", "template_name": "store_deal",
                                       "csrf": tok, **kw})
    return int(str(r.url).rsplit("/", 1)[1])


def test_deal_goes_only_to_subscribed_customers_once(store):
    c, app, tok, sender = store
    add(c, tok, "Asha", "9876543210")
    add(c, tok, "Ravi", "9876500000")
    add(c, tok, "Gone", "9800000000")
    with app.state.Session() as db:
        gone = db.scalar(select(Customer).where(Customer.name == "Gone"))
    c.post(f"/customers/{gone.id}/opt-out", data={"csrf": tok})
    cid = new_campaign(c, tok)
    r = c.post(f"/campaigns/{cid}/send", data={"csrf": tok})
    assert "Sent 2, failed 0" in r.text
    assert sorted(m["to"] for m in sender.outbox) == ["+919876500000", "+919876543210"]
    assert sender.outbox[0]["params"][1:] == ["our latest deals", "10% off today"]
    again = c.post(f"/campaigns/{cid}/send", data={"csrf": tok})   # double click / second visit
    assert "Sent 0, failed 0, already done 2" in again.text and len(sender.outbox) == 2


class FlakySender:
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def send_template(self, to, template, language, params):
        self.calls += 1
        return SendResult(False, error="HTTP 429: rate limited") if self.calls == 1 else SendResult(True, "wamid.X")


def test_failures_are_retried_and_sent_messages_are_not(store):
    c, app, tok, _ = store
    add(c, tok, "Asha", "9876543210")
    add(c, tok, "Ravi", "9876500000")
    cid = new_campaign(c, tok)
    flaky = FlakySender()
    with app.state.Session() as db:
        campaign = db.get(Campaign, cid)
        first = camp.send(db, campaign, flaky)
        second = camp.send(db, campaign, flaky)
        statuses = sorted(m.status for m in db.scalars(select(Message)))
    assert first == {"sent": 1, "failed": 1, "skipped": 0}
    assert second == {"sent": 1, "failed": 0, "skipped": 1} and flaky.calls == 3
    assert statuses == ["sent", "sent"]


def test_message_left_queued_by_a_crash_is_not_resent(store):
    c, app, tok, sender = store
    add(c, tok, "Asha", "9876543210")
    cid = new_campaign(c, tok)
    with app.state.Session() as db:
        db.add(Message(campaign_id=cid, customer_id=db.scalar(select(Customer.id)), status="queued"))
        db.commit()
        assert camp.send(db, db.get(Campaign, cid), sender) == {"sent": 0, "failed": 0, "skipped": 1}
    assert sender.outbox == []   # it may already have been delivered: never risk a duplicate


def test_cloud_api_request_shape_and_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        if seen["body"]["to"] == "910000000000":
            return httpx.Response(400, json={"error": {"message": "Template name does not exist"}})
        return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.ABC"}]})

    s = CloudAPISender("TOKEN", "12345", client=httpx.Client(transport=httpx.MockTransport(handler)))
    ok = s.send_template("+919876543210", "store_deal", "en", ["Asha", "Milk", "10% off"])
    assert ok == SendResult(True, provider_id="wamid.ABC")
    assert seen["url"] == "https://graph.facebook.com/v25.0/12345/messages" and seen["auth"] == "Bearer TOKEN"
    assert seen["body"] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "919876543210",
                            "type": "template", "template": {"name": "store_deal", "language": {"code": "en"},
                            "components": [{"type": "body", "parameters": [
                                {"type": "text", "text": "Asha"}, {"type": "text", "text": "Milk"},
                                {"type": "text", "text": "10% off"}]}]}}
    bad = s.send_template("+910000000000", "nope", "en", ["a", "b", "c"])
    assert not bad.ok and "Template name does not exist" in bad.error


def test_phone_normalisation():
    assert normalize_phone("98765 43210") == "+919876543210"
    assert normalize_phone("098765-43210") == "+919876543210"
    assert normalize_phone("+44 7700 900123") == "+447700900123"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_verification_signature_and_stop(store, monkeypatch):
    c, app, tok, _ = store
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
    q = {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "1158201444"}
    assert c.get("/webhooks/whatsapp", params=q).text == "1158201444"
    assert c.get("/webhooks/whatsapp", params={**q, "hub.verify_token": "no"}).status_code == 403
    add(c, tok, "Asha", "9876543210")
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": "919876543210", "type": "text", "text": {"body": " STOP "}}]}}]}]}).encode()
    assert c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 401
    assert c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": sign("secret", body)}).status_code == 200
    with app.state.Session() as db:
        assert db.scalar(select(Customer)).can_message is False
