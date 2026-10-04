"""Plain-language summary of the computed figures (a template, not an LLM)."""

from __future__ import annotations


def render_summary_text(period: str, total: float, top: dict[str, int], risks: list[dict]) -> str:
    top_list = ", ".join(f"{k} ({v})" for k, v in top.items()) if top else "no sales"
    high = [r["product"] for r in risks if r["risk"] == "high"]
    unknown = [r["product"] for r in risks if r["risk"] == "unknown"]
    if high:
        risk_text = "Likely to run out within a week: " + ", ".join(high) + "."
    elif unknown and len(unknown) == len(risks):
        risk_text = "Stock-out risk not assessed (no stock levels provided)."
    else:
        risk_text = "No stock-outs expected within a week."
    return f"{period}: total revenue ₹{total:.2f}. Top sellers: {top_list}. {risk_text}"
