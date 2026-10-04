"""Customers and deal campaigns.

Rules enforced here, not left to the UI:
- only customers with recorded consent who haven't opted out are messaged;
- a customer gets each campaign at most once (a unique (campaign, customer) row is
  written before sending, so a crash or a second click can't double-send);
- re-running a campaign retries only messages that failed.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Campaign, Customer, Message, utcnow
from app.services.whatsapp import normalize_phone


def add_customer(db: Session, name: str, phone: str, consent_source: str) -> Customer:
    if not consent_source.strip():
        raise ValueError("record how the customer agreed to WhatsApp messages (e.g. 'signed up at counter')")
    e164 = normalize_phone(phone)
    c = db.scalar(select(Customer).where(Customer.phone == e164))
    if c is None:
        c = Customer(name=name.strip() or "Customer", phone=e164)
        db.add(c)
    c.opted_in_at, c.opted_out_at, c.consent_source = utcnow(), None, consent_source.strip()[:120]
    db.commit()
    return c


def opt_out(db: Session, phone: str) -> bool:
    try:
        e164 = normalize_phone(phone)
    except ValueError:
        return False
    c = db.scalar(select(Customer).where(Customer.phone == e164))
    if c is None or c.opted_out_at is not None:
        return False
    c.opted_out_at = utcnow()
    db.commit()
    return True


def eligible(db: Session) -> list[Customer]:
    return [c for c in db.scalars(select(Customer).order_by(Customer.name)) if c.can_message]


def params_for(campaign: Campaign, customer: Customer) -> list[str]:
    product = campaign.product.name if campaign.product else "our latest deals"
    return [customer.name, product, campaign.offer]


def send(db: Session, campaign: Campaign, sender, pause_s: float = 0.0) -> dict:
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    for customer in eligible(db):
        msg = db.scalar(select(Message).where(Message.campaign_id == campaign.id, Message.customer_id == customer.id))
        if msg is not None and msg.status in ("sent", "queued"):
            counts["skipped"] += 1  # already delivered, or an earlier run may have sent it before crashing
            continue
        if msg is None:
            msg = Message(campaign_id=campaign.id, customer_id=customer.id, status="queued")
            db.add(msg)
            try:
                db.commit()  # claim (campaign, customer) before sending
            except IntegrityError:
                db.rollback()
                counts["skipped"] += 1
                continue
        else:
            msg.status = "queued"
            db.commit()
        res = sender.send_template(customer.phone, campaign.template_name, campaign.language,
                                   params_for(campaign, customer))
        msg.status, msg.provider_id, msg.error = ("sent", res.provider_id, "") if res.ok else ("failed", "", res.error)
        db.commit()
        counts["sent" if res.ok else "failed"] += 1
        if pause_s:
            time.sleep(pause_s)
    return counts
