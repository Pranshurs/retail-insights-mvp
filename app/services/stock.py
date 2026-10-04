"""Products, sales and stock. All stock changes go through ``move``."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Product, Sale, StockMovement


def product_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def get_or_create_product(db: Session, name: str, price: float | None = None) -> Product:
    key = product_key(name)
    p = db.scalar(select(Product).where(Product.key == key))
    if p is None:
        p = Product(name=name.strip(), key=key, price=price or 0.0, stock=0.0, stock_counted=False)
        db.add(p)
        db.flush()
    return p


def move(db: Session, product: Product, delta: float, reason: str, note: str = "") -> None:
    product.stock = round(product.stock + delta, 3)
    db.add(StockMovement(product_id=product.id, delta=delta, reason=reason, note=note[:200]))


def set_count(db: Session, product: Product, counted: float, note: str = "stock count") -> None:
    move(db, product, counted - product.stock, "count", note)
    product.stock_counted = True


def sale_ref(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:64]


def record_sale(db: Session, *, ref: str, name: str, quantity: float, price: float, sold_at: datetime,
                bill_id: str | None, source: str) -> bool:
    """Record one sale line and take it out of stock. Returns False if this ref was already recorded."""
    if db.scalar(select(Sale.id).where(Sale.ext_ref == ref)) is not None:
        return False
    p = get_or_create_product(db, name, price)
    db.add(Sale(ext_ref=ref, product_id=p.id, bill_id=bill_id, quantity=quantity, price=price,
                sold_at=sold_at, source=source))
    move(db, p, -quantity, "sale", f"{source} {bill_id or ''}".strip())
    return True


def daily_demand(db: Session, product_id: int, end: datetime, window_days: int = 7) -> float:
    start = end - timedelta(days=window_days)
    total = db.scalar(select(func.coalesce(func.sum(Sale.quantity), 0.0))
                      .where(Sale.product_id == product_id, Sale.sold_at > start, Sale.sold_at <= end))
    return float(total) / window_days


def stock_report(db: Session, now: datetime, window_days: int = 7, cover_days: int = 7) -> list[dict]:
    """Per product: stock, average daily demand, days left, status and a suggested reorder quantity."""
    rows = []
    for p in db.scalars(select(Product).order_by(Product.name)):
        demand = daily_demand(db, p.id, now, window_days)
        days_left = round(p.stock / demand, 1) if demand > 0 and p.stock > 0 else (0.0 if demand > 0 else None)
        if not p.stock_counted:
            status = "not counted"
        elif p.stock <= 0:
            status = "out of stock"
        elif p.stock <= p.reorder_level or (days_left is not None and days_left < cover_days):
            status = "low"
        else:
            status = "ok"
        # enough for two cover periods of current demand, and at least twice the reorder level
        target = max(demand * cover_days * 2, p.reorder_level * 2)
        reorder = max(0.0, round(target - p.stock)) if status in ("low", "out of stock") else 0.0
        rows.append({"product": p, "demand": round(demand, 2), "days_left": days_left, "status": status,
                     "suggested_reorder": reorder})
    return rows
