# app/main.py
import os
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from datetime import datetime
import pandas as pd

# Internal modules
from app.connector import load_sales_csv
from app.insights import total_revenue, top_sellers, inventory_risks
from app.prompts import render_summary_text

# -----------------------------------
# Load environment variables FIRST
# -----------------------------------
load_dotenv(dotenv_path="config/.env")  # Must be before reading env vars
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise EnvironmentError("API_KEY not found in config/.env")

# -----------------------------------
# Create FastAPI app
# -----------------------------------
app = FastAPI(title="Retail Insights Production")

# -----------------------------------
# Debug route to check API key loading
# -----------------------------------
@app.get("/debug_api_key")
def debug_api_key():
    return {"api_key_loaded": API_KEY}

# -----------------------------------
# Root route
# -----------------------------------
@app.get("/")
def home():
    return {"message": "Retail Insights API is running!"}

# -----------------------------------
# API Key verification dependency
# -----------------------------------
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return True

# -----------------------------------
# Request Models
# -----------------------------------
class SourceConfig(BaseModel):
    type: str
    csv_path: Optional[str] = None
    uri: Optional[str] = None

class InsightRequest(BaseModel):
    source: SourceConfig
    date: Optional[str] = None

class POSRecord(BaseModel):
    bill_id: str
    item_name: str
    quantity: float
    price: float
    timestamp: str

# -----------------------------------
# Health Check
# -----------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# -----------------------------------
# Generate Insights (CSV only)
# -----------------------------------
@app.post("/generate_insights")
def generate_insights(req: InsightRequest, valid: bool = Depends(verify_api_key)):
    if req.source.type != "csv":
        raise HTTPException(status_code=400, detail="Only CSV source supported")

    if not req.source.csv_path:
        raise HTTPException(status_code=400, detail="csv_path required")

    try:
        df_sales = load_sales_csv(req.source.csv_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))

    total = total_revenue(df_sales)
    top = top_sellers(df_sales)
    risks = inventory_risks(df_sales)

    text = render_summary_text(date=req.date, total=total, top=top, risks=risks)

    return {
        "summary_raw": {
            "total": total,
            "top": top,
            "inventory_risks": risks
        },
        "summary_text": text
    }

# -----------------------------------
# POS Sync Endpoint
# -----------------------------------
@app.post("/sync-pos")
def sync_pos(records: list[POSRecord]):
    if not records:
        raise HTTPException(status_code=400, detail="No records received")

    # Convert payload to DataFrame
    df = pd.DataFrame([r.dict() for r in records])

    # Save locally (or push to database in production)
    os.makedirs("data/pos_sync", exist_ok=True)
    filename = f"data/pos_sync/sync_{datetime.now().timestamp()}.csv"
    df.to_csv(filename, index=False)

    return {"status": "success", "stored_file": filename, "count": len(records)}