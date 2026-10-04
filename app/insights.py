"""Sales calculations. Every number is computed from the rows given; nothing is assumed."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def total_revenue(df: pd.DataFrame) -> float:
    return round(float((df["quantity"] * df["price"]).sum()), 2)


def top_sellers(df: pd.DataFrame, n: int = 5) -> dict[str, int]:
    series = df.groupby("product")["quantity"].sum().sort_values(ascending=False, kind="stable").head(n)
    return {str(k): int(v) for k, v in series.items()}


def inventory_risks(df: pd.DataFrame, stock: dict[str, int] | None = None, end: date | None = None,
                    window_days: int = 7, horizon_days: int = 7) -> list[dict]:
    """Days until each product runs out, from average daily demand over the last ``window_days``.

    Days with no sales count as zero demand. A product without a stock level in ``stock``
    gets risk "unknown": the old code assumed 20 units for everything, which made the risk
    figures invented.
    """
    stock = stock or {}
    end = end or df["date"].max().date()
    start = end - timedelta(days=window_days - 1)
    window = df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]
    demand = window.groupby("product")["quantity"].sum() / window_days
    out = []
    for product in sorted(set(df["product"])):
        avg = float(demand.get(product, 0.0))
        units = stock.get(product)
        if units is None:
            days, risk = None, "unknown"
        elif avg == 0:
            days, risk = None, "ok"
        else:
            days = round(units / avg, 2)
            risk = "high" if days < horizon_days else "ok"
        out.append({"product": product, "avg_daily_demand": round(avg, 3), "stock": units,
                    "days_to_stockout": days, "risk": risk})
    return out
