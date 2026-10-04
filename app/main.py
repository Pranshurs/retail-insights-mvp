"""Retail store manager: owner dashboard, stock, sales import/sync, WhatsApp deal campaigns.

Run:  uvicorn app.main:app --port 8000   then open http://localhost:8000

Configuration (environment or config/.env), all optional for a local start:
  DATABASE_URL          default sqlite:///data/store.db
  SECRET_KEY            session signing key (generated and stored if unset)
  API_KEY               required by the machine endpoints (/sync-pos, /api/*, /generate_insights)
  DATA_DIR              where /generate_insights may read CSVs from (default samples)
  WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_API_VERSION   real sending (mock otherwise)
  WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET                       inbound webhook
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.db import (
    Campaign,
    Customer,
    Message,
    Product,
    Sale,
    make_engine,
    make_sessionmaker,
    store_now,
)
from app.services import campaigns as camp
from app.services import imports, stock
from app.services.whatsapp import (
    inbound_messages,
    is_opt_out,
    sender_from_env,
    signature_valid,
)

load_dotenv(dotenv_path="config/.env")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))
MAX_UPLOAD = 5 * 1024 * 1024
MAX_POS_RECORDS = 10_000


class POSRecord(BaseModel):
    bill_id: str = Field(min_length=1, max_length=64)
    item_name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    timestamp: datetime


class SourceConfig(BaseModel):
    type: Literal["csv"] = "csv"
    csv_path: str = Field(min_length=1)


class InsightRequest(BaseModel):
    source: SourceConfig
    date: Optional[Date] = None
    stock: dict[str, int] = Field(default_factory=dict)
    window_days: int = Field(default=7, ge=1, le=90)


def create_app(database_url: str | None = None, sender=None) -> FastAPI:
    engine = make_engine(database_url)
    Session = make_sessionmaker(engine)
    with Session() as db:
        secret = auth.session_secret(db)
    app = FastAPI(title="Retail Store Manager")
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="strict", https_only=False, max_age=12 * 3600)
    app.state.Session = Session
    app.state.sender = sender or sender_from_env()
    throttle = auth.LoginThrottle()

    @contextmanager
    def session():
        with Session() as db:
            yield db

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.update(request=request, csrf=auth.csrf_token(request), flash=request.session.pop("flash", None),
                   sender_name=app.state.sender.name)
        return TEMPLATES.TemplateResponse(request, name, ctx)

    def flash(request: Request, text: str) -> None:
        request.session["flash"] = text

    def owner(request: Request) -> Optional[RedirectResponse]:
        """Redirect to setup/login unless the owner is signed in."""
        with session() as db:
            if not auth.owner_exists(db):
                return RedirectResponse("/setup", 303)
        if not request.session.get("owner"):
            return RedirectResponse("/login", 303)
        return None

    async def read_upload(f: UploadFile) -> bytes:
        data = await f.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large (max 5 MB)")
        return data

    # ---------------- setup / login ----------------
    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request):
        with session() as db:
            if auth.owner_exists(db):
                return RedirectResponse("/login", 303)
        return render(request, "setup.html")

    @app.post("/setup")
    def setup(request: Request, password: str = Form(...), confirm: str = Form(...), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        with session() as db:
            if auth.owner_exists(db):
                raise HTTPException(403, "owner already set up")
            if password != confirm:
                flash(request, "Passwords don't match.")
                return RedirectResponse("/setup", 303)
            try:
                auth.set_owner_password(db, password)
            except ValueError as exc:
                flash(request, str(exc))
                return RedirectResponse("/setup", 303)
        request.session["owner"] = True
        return RedirectResponse("/", 303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    def login(request: Request, password: str = Form(...), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        client = request.client.host if request.client else "?"
        if throttle.blocked(client):
            flash(request, "Too many attempts. Wait a few minutes.")
            return RedirectResponse("/login", 303)
        with session() as db:
            ok = auth.check_owner_password(db, password)
        if not ok:
            throttle.failed(client)
            flash(request, "Wrong password.")
            return RedirectResponse("/login", 303)
        throttle.reset(client)
        request.session.clear()
        request.session["owner"] = True
        return RedirectResponse("/", 303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", 303)

    # ---------------- dashboard ----------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        if (r := owner(request)):
            return r
        now = store_now()
        today = now.date()
        with session() as db:
            days = []
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                start = datetime(d.year, d.month, d.day)
                total = db.scalar(select(func.coalesce(func.sum(Sale.quantity * Sale.price), 0.0))
                                  .where(Sale.sold_at >= start, Sale.sold_at < start + timedelta(days=1)))
                days.append((d, float(total)))
            week_start = datetime.combine(today - timedelta(days=6), datetime.min.time())
            top = db.execute(select(Product.name, func.sum(Sale.quantity)).join(Sale.product)
                             .where(Sale.sold_at >= week_start).group_by(Product.name)
                             .order_by(func.sum(Sale.quantity).desc()).limit(5)).all()
            report = stock.stock_report(db, now)
            alerts = [r for r in report if r["status"] in ("low", "out of stock")]
            uncounted = sum(1 for r in report if r["status"] == "not counted")
            customers = sum(1 for c in db.scalars(select(Customer)) if c.can_message)
            recent = db.scalars(select(Campaign).order_by(Campaign.created_at.desc()).limit(3)).all()
            last_sale = db.scalar(select(func.max(Sale.sold_at)))
            return render(request, "overview.html", days=days, peak=max((t for _, t in days), default=0) or 1,
                          top=top, alerts=alerts, uncounted=uncounted, customers=customers, recent=recent,
                          last_sale=last_sale)

    @app.get("/stock", response_class=HTMLResponse)
    def stock_page(request: Request):
        if (r := owner(request)):
            return r
        with session() as db:
            return render(request, "stock.html", report=stock.stock_report(db, store_now()))

    @app.post("/stock/update")
    def stock_update(request: Request, product_id: int = Form(...), action: str = Form(...),
                     amount: float = Form(...), note: str = Form(""), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        with session() as db:
            p = db.get(Product, product_id)
            if p is None:
                raise HTTPException(404)
            if action == "restock" and amount > 0:
                stock.move(db, p, amount, "restock", note or "restock")
            elif action == "count" and amount >= 0:
                stock.set_count(db, p, amount, note or "stock count")
            elif action == "reorder_level" and amount >= 0:
                p.reorder_level = amount
            else:
                raise HTTPException(400, "invalid update")
            db.commit()
            flash(request, f"Updated {p.name}.")
        return RedirectResponse("/stock", 303)

    @app.post("/stock/add")
    def stock_add(request: Request, name: str = Form(...), price: float = Form(0.0), count: float = Form(0.0),
                  reorder_level: float = Form(0.0), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        with session() as db:
            p = stock.get_or_create_product(db, name, price)
            p.price, p.reorder_level = price, reorder_level
            stock.set_count(db, p, count, "added in dashboard")
            db.commit()
            flash(request, f"Saved {p.name}.")
        return RedirectResponse("/stock", 303)

    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request):
        if (r := owner(request)):
            return r
        return render(request, "import.html", result=request.session.pop("import_result", None))

    @app.post("/import/{kind}")
    async def import_file(request: Request, kind: Literal["sales", "stock"], file: UploadFile = File(...),
                          csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        data = await read_upload(file)
        with session() as db:
            res = imports.import_sales(db, data) if kind == "sales" else imports.import_stock(db, data)
        request.session["import_result"] = {"kind": kind, "file": file.filename, "added": res.added,
                                            "duplicates": res.duplicates, "errors": res.errors[:20],
                                            "more_errors": max(0, len(res.errors) - 20)}
        return RedirectResponse("/import", 303)

    @app.get("/customers", response_class=HTMLResponse)
    def customers_page(request: Request):
        if (r := owner(request)):
            return r
        with session() as db:
            return render(request, "customers.html", customers=db.scalars(select(Customer).order_by(Customer.name)).all())

    @app.post("/customers/add")
    def customers_add(request: Request, name: str = Form(...), phone: str = Form(...),
                      consent: str = Form(""), consent_source: str = Form(""), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        if consent != "yes":
            flash(request, "Only add customers who agreed to receive WhatsApp messages.")
            return RedirectResponse("/customers", 303)
        with session() as db:
            try:
                c = camp.add_customer(db, name, phone, consent_source)
                flash(request, f"Added {c.name} ({c.phone}).")
            except ValueError as exc:
                flash(request, str(exc))
        return RedirectResponse("/customers", 303)

    @app.post("/customers/{customer_id}/opt-out")
    def customers_opt_out(request: Request, customer_id: int, csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        with session() as db:
            c = db.get(Customer, customer_id)
            if c:
                camp.opt_out(db, c.phone)
                flash(request, f"{c.name} won't receive messages any more.")
        return RedirectResponse("/customers", 303)

    @app.get("/campaigns", response_class=HTMLResponse)
    def campaigns_page(request: Request):
        if (r := owner(request)):
            return r
        with session() as db:
            rows = db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
            stats = {c.id: {s: sum(1 for m in c.messages if m.status == s) for s in ("sent", "failed", "queued")}
                     for c in rows}
            return render(request, "campaigns.html", campaigns=rows, stats=stats,
                          products=db.scalars(select(Product).order_by(Product.name)).all(),
                          eligible=len(camp.eligible(db)))

    @app.post("/campaigns/new")
    def campaigns_new(request: Request, name: str = Form(...), offer: str = Form(...),
                      template_name: str = Form(...), language: str = Form("en"),
                      product_id: str = Form(""), csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        with session() as db:
            c = Campaign(name=name.strip()[:120], offer=offer.strip()[:200], template_name=template_name.strip()[:120],
                         language=language.strip()[:10] or "en", product_id=int(product_id) if product_id else None)
            db.add(c)
            db.commit()
            return RedirectResponse(f"/campaigns/{c.id}", 303)

    @app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
    def campaign_detail(request: Request, campaign_id: int):
        if (r := owner(request)):
            return r
        with session() as db:
            c = db.get(Campaign, campaign_id)
            if c is None:
                raise HTTPException(404)
            recipients = camp.eligible(db)
            preview = camp.params_for(c, recipients[0]) if recipients else None
            msgs = db.scalars(select(Message).where(Message.campaign_id == c.id)).all()
            return render(request, "campaign.html", c=c, recipients=len(recipients), preview=preview,
                          messages=[(m, m.customer) for m in msgs])

    @app.post("/campaigns/{campaign_id}/send")
    def campaign_send(request: Request, campaign_id: int, csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        if (r := owner(request)):
            return r
        with session() as db:
            c = db.get(Campaign, campaign_id)
            if c is None:
                raise HTTPException(404)
            counts = camp.send(db, c, app.state.sender, pause_s=float(os.getenv("WHATSAPP_SEND_PAUSE_S", "0")))
        flash(request, f"Sent {counts['sent']}, failed {counts['failed']}, already done {counts['skipped']}.")
        return RedirectResponse(f"/campaigns/{campaign_id}", 303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        if (r := owner(request)):
            return r
        return render(request, "settings.html", api_key_set=bool(os.getenv("API_KEY", "").strip()),
                      webhook_ready=bool(os.getenv("WHATSAPP_VERIFY_TOKEN") and os.getenv("WHATSAPP_APP_SECRET")))

    # ---------------- machine API (x-api-key) ----------------
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/sync-pos")
    def sync_pos(records: list[POSRecord], x_api_key: str = Header(default="")):
        auth.check_api_key(x_api_key)
        if not records:
            raise HTTPException(400, "no records received")
        if len(records) > MAX_POS_RECORDS:
            raise HTTPException(413, f"at most {MAX_POS_RECORDS} records per request")
        added = 0
        with session() as db:
            for r in records:
                ref = stock.sale_ref("pos", r.bill_id, r.item_name.lower(), r.quantity, r.price, r.timestamp.isoformat())
                added += stock.record_sale(db, ref=ref, name=r.item_name, quantity=r.quantity, price=r.price,
                                           sold_at=r.timestamp.replace(tzinfo=None), bill_id=r.bill_id, source="pos")
            db.commit()
        return {"status": "stored", "added": added, "duplicates": len(records) - added}

    @app.get("/api/stock")
    def api_stock(x_api_key: str = Header(default="")):
        auth.check_api_key(x_api_key)
        with session() as db:
            return [{"product": r["product"].name, "stock": r["product"].stock, "status": r["status"],
                     "avg_daily_demand": r["demand"], "days_left": r["days_left"],
                     "suggested_reorder": r["suggested_reorder"]} for r in stock.stock_report(db, store_now())]

    @app.post("/generate_insights")
    def generate_insights(req: InsightRequest, x_api_key: str = Header(default="")):
        """Stateless figures from a CSV inside DATA_DIR (the original MVP endpoint, kept)."""
        auth.check_api_key(x_api_key)
        from app.connector import load_sales_csv
        from app.insights import inventory_risks, top_sellers, total_revenue
        from app.prompts import render_summary_text

        data_dir = Path(os.getenv("DATA_DIR", "samples")).resolve()
        path = (data_dir / req.source.csv_path).resolve()
        if path != data_dir and data_dir not in path.parents:
            raise HTTPException(400, "csv_path must be inside DATA_DIR")
        try:
            df = load_sales_csv(str(path))
        except FileNotFoundError:
            raise HTTPException(404, "CSV not found in DATA_DIR") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if req.date is not None:
            day = df[df["date"].dt.date == req.date]
            if day.empty:
                raise HTTPException(404, f"no sales recorded on {req.date}")
            period, end = req.date.isoformat(), req.date
        else:
            day, end = df, df["date"].max().date()
            period = f"{df['date'].min().date()} to {end}"
        total, top = total_revenue(day), top_sellers(day)
        risks = inventory_risks(df, stock=req.stock, end=end, window_days=req.window_days)
        return {"period": period, "summary_raw": {"total": total, "top": top, "inventory_risks": risks},
                "summary_text": render_summary_text(period=period, total=total, top=top, risks=risks)}

    # ---------------- WhatsApp webhook ----------------
    @app.get("/webhooks/whatsapp")
    def webhook_verify(request: Request):
        q = request.query_params
        token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        if token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403, "verification failed")

    @app.post("/webhooks/whatsapp")
    async def webhook_receive(request: Request, x_hub_signature_256: str = Header(default="")):
        body = await request.body()
        if not signature_valid(os.getenv("WHATSAPP_APP_SECRET", ""), body, x_hub_signature_256):
            raise HTTPException(401, "bad signature")
        try:
            payload = await request.json()
        except ValueError:
            return Response(status_code=200)
        with session() as db:
            for phone, text in inbound_messages(payload):
                if is_opt_out(text):
                    camp.opt_out(db, phone)
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
