"""Database: one SQLite file by default (DATABASE_URL to change), SQLAlchemy 2 models.

Stock is never edited directly: every change is a StockMovement (sale, restock, count,
adjustment) written in the same transaction as the new Product.stock value, so the
current stock always equals the sum of its movements and every change is explained.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def store_now() -> datetime:
    """Current time in the store's time zone (STORE_TZ, default Asia/Kolkata), naive.

    Sale times from POS exports are the shop's local wall-clock times, so "today" and
    "last 7 days" must be computed in that same zone, not in UTC."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(os.getenv("STORE_TZ", "Asia/Kolkata"))).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)  # normalised name for matching POS items
    price: Mapped[float] = mapped_column(Float, default=0.0)
    stock: Mapped[float] = mapped_column(Float, default=0.0)
    reorder_level: Mapped[float] = mapped_column(Float, default=0.0)
    stock_counted: Mapped[bool] = mapped_column(Boolean, default=False)  # False: created from a sale, never counted


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    ext_ref: Mapped[str] = mapped_column(String(64), unique=True)  # dedup key: re-imports never double count
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    bill_id: Mapped[Optional[str]] = mapped_column(String(64))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    sold_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(16))  # csv | pos | manual
    product: Mapped[Product] = relationship()


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(16))  # sale | restock | count | adjustment
    note: Mapped[str] = mapped_column(String(200), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), unique=True)  # E.164, e.g. +919812345678
    opted_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    consent_source: Mapped[str] = mapped_column(String(120), default="")  # how consent was given
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    @property
    def can_message(self) -> bool:
        return self.opted_in_at is not None and self.opted_out_at is None


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"))
    offer: Mapped[str] = mapped_column(String(200))
    template_name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(10), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    product: Mapped[Optional[Product]] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="campaign")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("campaign_id", "customer_id"),)  # one deal per customer per campaign
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | sent | failed
    provider_id: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    campaign: Mapped[Campaign] = relationship(back_populates="messages")
    customer: Mapped[Customer] = relationship()


def make_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///data/store.db")
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")) or ".", exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine


def make_sessionmaker(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
