"""Owner dashboard: setup, login, CSRF, stock, imports, customers."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Product, StockMovement, store_now
from tests.conftest import PASSWORD, csrf_of

PAGES = ["/", "/stock", "/import", "/customers", "/campaigns", "/settings"]


def test_first_visit_goes_to_setup_and_setup_happens_once(tmp_path, monkeypatch):
    from app.main import create_app

    c = TestClient(create_app(f"sqlite:///{tmp_path}/a.db"))
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    tok = csrf_of(c.get("/setup").text)
    short = c.post("/setup", data={"password": "short", "confirm": "short", "csrf": tok})
    assert "at least 10" in short.text
    c.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD, "csrf": tok})
    assert c.get("/").status_code == 200
    other = TestClient(c.app)
    tok2 = csrf_of(other.get("/login").text)
    assert other.post("/setup", data={"password": "x" * 12, "confirm": "x" * 12, "csrf": tok2}).status_code == 403


def test_pages_need_login(store):
    c, app, _, _ = store
    anon = TestClient(app)
    for page in PAGES:
        assert anon.get(page, follow_redirects=False).headers["location"] == "/login"
        assert c.get(page).status_code == 200


def test_wrong_password_and_throttling(store):
    _, app, _, _ = store
    anon = TestClient(app)
    tok = csrf_of(anon.get("/login").text)
    for _ in range(5):
        assert "Wrong password" in anon.post("/login", data={"password": "nope", "csrf": tok}).text
    blocked = anon.post("/login", data={"password": PASSWORD, "csrf": tok})
    assert "Too many attempts" in blocked.text


def test_forms_need_the_csrf_token(store):
    c, _, _, _ = store
    r = c.post("/stock/add", data={"name": "Tea", "count": 5, "csrf": "forged"})
    assert r.status_code == 403


def test_stock_changes_are_movements_and_add_up(store):
    c, app, tok, _ = store
    c.post("/stock/add", data={"name": "Tea 250g", "price": 120, "count": 10, "reorder_level": 4, "csrf": tok})
    with app.state.Session() as db:
        pid = db.scalar(select(Product.id))
    c.post("/stock/update", data={"product_id": pid, "action": "restock", "amount": 5, "csrf": tok})
    c.post("/sync-pos", json=[{"bill_id": "B9", "item_name": "tea 250G", "quantity": 3, "price": 120,
                               "timestamp": datetime.now().isoformat()}], headers={"x-api-key": "test-key-123"})
    c.post("/stock/update", data={"product_id": pid, "action": "count", "amount": 11, "csrf": tok})
    with app.state.Session() as db:
        p = db.get(Product, pid)
        total = db.scalar(select(func.sum(StockMovement.delta)).where(StockMovement.product_id == pid))
        reasons = [m.reason for m in db.scalars(select(StockMovement).where(StockMovement.product_id == pid))]
    assert p.stock == 11 and total == 11      # stock always equals the sum of its movements
    assert reasons == ["count", "restock", "sale", "count"]  # POS item name matched case-insensitively


def test_low_stock_shows_on_the_overview_with_a_suggestion(store):
    c, _, tok, _ = store
    c.post("/stock/add", data={"name": "Milk", "price": 50, "count": 4, "reorder_level": 10, "csrf": tok})
    page = c.get("/").text
    assert "Milk" in page and "low" in page and "order ~16" in page   # at least 2x the reorder level


def test_sales_import_dedupes_overlapping_exports(store):
    c, app, tok, _ = store
    today = store_now().date()
    rows = [f"{today - timedelta(days=d)},Bread,2,30,B{d}" for d in range(3)]
    first = "Date,Item,Qty,Rate,Bill No\n" + "\n".join(rows[:2]) + "\n"
    overlap = "Date,Item,Qty,Rate,Bill No\n" + "\n".join(rows[1:]) + "\n"     # different POS column names
    r = c.post("/import/sales", data={"csrf": tok}, files={"file": ("a.csv", first.encode())})
    assert "Added <b>2</b>" in r.text
    page = c.post("/import/sales", data={"csrf": tok}, files={"file": ("b.csv", overlap.encode())}).text
    assert "Added <b>1</b>" in page and "skipped <b>1</b>" in page
    with app.state.Session() as db:
        assert db.scalar(select(Product.stock)) == -6.0


def test_import_reports_bad_rows_and_missing_columns(store):
    c, _, tok, _ = store
    bad = b"date,product,quantity,price\n2025-01-01,Milk,abc,50\n2025-01-01,,1,50\n2025-01-01,Tea,1,40\n"
    page = c.post("/import/sales", data={"csrf": tok}, files={"file": ("x.csv", bad)}).text
    assert "Added <b>1</b>" in page and "line 2" in page and "line 3" in page
    r = c.post("/import/sales", data={"csrf": tok}, files={"file": ("y.csv", b"when,what\n1,2\n")})
    assert "missing columns" in r.text


def test_stock_import_sets_counts(store):
    c, _, tok, _ = store
    c.post("/import/stock", data={"csrf": tok},
           files={"file": ("s.csv", b"Product,Closing Stock,MRP,Min Stock\nRice 5kg,12,360,5\n")})
    page = c.get("/stock").text
    assert "Rice 5kg" in page and "₹360.0" in page and "reorder at 5.0" in page


def test_customers_need_consent_and_valid_numbers(store):
    c, _, tok, _ = store
    no_consent = c.post("/customers/add", data={"name": "A", "phone": "9876543210", "consent_source": "x", "csrf": tok})
    assert "Only add customers who agreed" in no_consent.text and "+919876543210" not in no_consent.text
    bad = c.post("/customers/add", data={"name": "B", "phone": "12", "consent": "yes", "consent_source": "counter",
                                         "csrf": tok})
    assert "not a valid phone" in bad.text
    ok = c.post("/customers/add", data={"name": "Asha", "phone": "098765 43210", "consent": "yes",
                                        "consent_source": "counter", "csrf": tok})
    assert "+919876543210" in ok.text and "subscribed" in ok.text


def test_consent_source_is_required_by_the_service(store):
    import pytest

    from app.services.campaigns import add_customer

    _, app, _, _ = store
    with app.state.Session() as db, pytest.raises(ValueError, match="how the customer agreed"):
        add_customer(db, "Asha", "9876543210", "   ")
