"""Load a sales CSV into a normalised DataFrame (columns: date, product, quantity, price)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED = ("date", "product", "quantity", "price")


def load_sales_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")
    df = pd.read_csv(p)
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing columns: {missing}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    bad = df[list(REQUIRED)].isna().any(axis=1)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} row(s) have an unreadable date, quantity or price")
    if (df["quantity"] < 0).any() or (df["price"] < 0).any():
        raise ValueError("quantity and price must not be negative")
    return df
