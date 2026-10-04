"""Retail Insights API: sales insights for a small shop from CSV or synced POS records.

    POST /generate_insights   (x-api-key) totals, top sellers and stock-out risk for a day or all days
    POST /sync-pos            (x-api-key) receive POS records from the sync agent
    GET  /health

Configuration comes from the environment (optionally ``config/.env``):
  API_KEY       required; every data endpoint checks it
  DATA_DIR      directory that ``csv_path`` is resolved inside (default: ``samples``)
  POS_SYNC_DIR  where synced POS batches are written (default: ``data/pos_sync``)
"""

from __future__ import annotations

import hmac
import os
import uuid
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.connector import load_sales_csv
from app.insights import inventory_risks, top_sellers, total_revenue
from app.prompts import render_summary_text

load_dotenv(dotenv_path="config/.env")


def _settings() -> dict:
    key = os.getenv("API_KEY", "").strip()
    if not key:
        raise RuntimeError("API_KEY is not set (environment or config/.env)")
    return {
        "api_key": key,
        "data_dir": Path(os.getenv("DATA_DIR", "samples")).resolve(),
        "pos_dir": Path(os.getenv("POS_SYNC_DIR", "data/pos_sync")).resolve(),
    }


SETTINGS = _settings()
MAX_POS_RECORDS = 10_000

app = FastAPI(title="Retail Insights")


def verify_api_key(x_api_key: str = Header(default="")) -> None:
    # constant-time comparison so response timing doesn't leak how much of a guess matched
    if not hmac.compare_digest(x_api_key.encode(), SETTINGS["api_key"].encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class SourceConfig(BaseModel):
    type: Literal["csv"] = "csv"
    csv_path: str = Field(min_length=1, description="path relative to DATA_DIR")


class InsightRequest(BaseModel):
    source: SourceConfig
    date: Optional[Date] = Field(default=None, description="report on this day only; omit for all data")
    stock: dict[str, int] = Field(default_factory=dict, description="current stock per product, for stock-out risk")
    window_days: int = Field(default=7, ge=1, le=90, description="days of history used for average demand")


class POSRecord(BaseModel):
    bill_id: str = Field(min_length=1, max_length=64)
    item_name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    timestamp: datetime


def _resolve_inside_data_dir(rel: str) -> Path:
    data_dir = SETTINGS["data_dir"]
    p = (data_dir / rel).resolve()
    if p != data_dir and data_dir not in p.parents:
        raise HTTPException(status_code=400, detail="csv_path must be inside DATA_DIR")
    return p


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/generate_insights", dependencies=[Depends(verify_api_key)])
def generate_insights(req: InsightRequest) -> dict:
    path = _resolve_inside_data_dir(req.source.csv_path)
    try:
        df = load_sales_csv(str(path))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="CSV not found in DATA_DIR") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if req.date is not None:
        day = df[df["date"].dt.date == req.date]
        if day.empty:
            raise HTTPException(status_code=404, detail=f"no sales recorded on {req.date}")
        period, end = req.date.isoformat(), req.date
    else:
        day = df
        period = f"{df['date'].min().date()} to {df['date'].max().date()}"
        end = df["date"].max().date()

    total = total_revenue(day)
    top = top_sellers(day)
    risks = inventory_risks(df, stock=req.stock, end=end, window_days=req.window_days)
    return {
        "period": period,
        "summary_raw": {"total": total, "top": top, "inventory_risks": risks},
        "summary_text": render_summary_text(period=period, total=total, top=top, risks=risks),
    }


@app.post("/sync-pos", dependencies=[Depends(verify_api_key)])
def sync_pos(records: list[POSRecord]) -> dict:
    if not records:
        raise HTTPException(status_code=400, detail="no records received")
    if len(records) > MAX_POS_RECORDS:
        raise HTTPException(status_code=413, detail=f"at most {MAX_POS_RECORDS} records per request")
    import pandas as pd

    batch_id = uuid.uuid4().hex
    SETTINGS["pos_dir"].mkdir(parents=True, exist_ok=True)
    pd.DataFrame([r.model_dump(mode="json") for r in records]).to_csv(
        SETTINGS["pos_dir"] / f"pos_{batch_id}.csv", index=False)
    return {"status": "stored", "batch_id": batch_id, "count": len(records)}
