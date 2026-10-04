"""CSV imports from any POS that can export a file ("store tracker" plug-in).

Sales CSV: a row per sale line. Column names are matched case-insensitively against the
aliases below, so most POS exports work as they are; ``COLUMN_MAP`` in the environment can
add more (e.g. ``COLUMN_MAP=product:Item Description,quantity:Qty Sold``).

Re-importing the same file, or overlapping exports, never double-counts: each line's
identity is a hash of its contents plus how many identical lines came before it in the file.

Stock CSV: product, stock, and optionally price and reorder_level. It sets absolute counts.
"""

from __future__ import annotations

import csv
import io
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.stock import get_or_create_product, record_sale, sale_ref, set_count

ALIASES = {
    "date": ["date", "sold_at", "timestamp", "datetime", "bill date", "invoice date", "time"],
    "product": ["product", "item", "item_name", "item name", "name", "description", "product name"],
    "quantity": ["quantity", "qty", "units", "qty sold"],
    "price": ["price", "rate", "unit price", "mrp", "selling price"],
    "bill_id": ["bill_id", "bill", "bill no", "invoice", "invoice no", "receipt", "txn_id"],
    "stock": ["stock", "on hand", "closing stock", "qty on hand", "current stock"],
    "reorder_level": ["reorder_level", "reorder level", "min stock", "minimum"],
}


@dataclass
class ImportResult:
    added: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _column_map(header: list[str]) -> dict[str, str]:
    extra = {}
    for pair in filter(None, os.getenv("COLUMN_MAP", "").split(",")):
        k, _, v = pair.partition(":")
        extra.setdefault(k.strip(), []).append(v.strip().lower())
    norm = {h.strip().lower(): h for h in header}
    out = {}
    for canonical, names in ALIASES.items():
        for name in extra.get(canonical, []) + names:
            if name in norm:
                out[canonical] = norm[name]
                break
    return out


def _parse_date(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(s)


def _reader(data: bytes) -> csv.DictReader:
    return csv.DictReader(io.StringIO(data.decode("utf-8-sig")))


def import_sales(db: Session, data: bytes, source: str = "csv") -> ImportResult:
    res = ImportResult()
    reader = _reader(data)
    cols = _column_map(reader.fieldnames or [])
    missing = [c for c in ("date", "product", "quantity", "price") if c not in cols]
    if missing:
        res.errors.append(f"missing columns: {missing} (found {reader.fieldnames})")
        return res
    seen: Counter = Counter()
    for n, row in enumerate(reader, start=2):
        try:
            name = row[cols["product"]].strip()
            qty, price = float(row[cols["quantity"]]), float(row[cols["price"]])
            when = _parse_date(row[cols["date"]])
            bill = row.get(cols.get("bill_id", ""), "") or None
            if not name or qty <= 0 or price < 0:
                raise ValueError("needs a product, quantity > 0 and price >= 0")
        except (ValueError, KeyError, TypeError) as exc:
            res.errors.append(f"line {n}: {exc}")
            continue
        content = (when.isoformat(), name.lower(), qty, price, bill or "")
        seen[content] += 1
        if record_sale(db, ref=sale_ref("csv", *content, seen[content]), name=name, quantity=qty, price=price,
                       sold_at=when, bill_id=bill, source=source):
            res.added += 1
        else:
            res.duplicates += 1
    db.commit()
    return res


def import_stock(db: Session, data: bytes) -> ImportResult:
    res = ImportResult()
    reader = _reader(data)
    cols = _column_map(reader.fieldnames or [])
    if "product" not in cols or "stock" not in cols:
        res.errors.append(f"needs product and stock columns (found {reader.fieldnames})")
        return res
    for n, row in enumerate(reader, start=2):
        try:
            name = row[cols["product"]].strip()
            counted = float(row[cols["stock"]])
            if not name or counted < 0:
                raise ValueError("needs a product and stock >= 0")
            p = get_or_create_product(db, name)
            if "price" in cols and row[cols["price"]].strip():
                p.price = float(row[cols["price"]])
            if "reorder_level" in cols and row[cols["reorder_level"]].strip():
                p.reorder_level = float(row[cols["reorder_level"]])
            set_count(db, p, counted, "stock CSV import")
            res.added += 1
        except (ValueError, KeyError) as exc:
            res.errors.append(f"line {n}: {exc}")
    db.commit()
    return res
