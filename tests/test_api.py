from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

KEY = "test-key-123"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", "samples")
    monkeypatch.setenv("POS_SYNC_DIR", str(tmp_path / "pos"))
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app), tmp_path / "pos"


def ask(c, body, key=KEY):
    return c.post("/generate_insights", json=body, headers={"x-api-key": key} if key else {})


SRC = {"type": "csv", "csv_path": "sample_sales.csv"}


def test_insights_require_the_api_key(client):
    c, _ = client
    assert ask(c, {"source": SRC}, key=None).status_code == 401
    assert ask(c, {"source": SRC}, key="wrong").status_code == 401
    assert ask(c, {"source": SRC}).status_code == 200


def test_the_key_is_never_exposed(client):
    c, _ = client
    assert c.get("/debug_api_key").status_code == 404
    assert KEY not in c.get("/health").text


def test_date_filters_the_figures(client):
    c, _ = client
    day = ask(c, {"source": SRC, "date": "2025-01-03"}).json()
    assert day["summary_raw"]["total"] == 474.0          # 6*50 + 14*6 + 2*45, that day only
    assert day["summary_text"].startswith("2025-01-03: total revenue ₹474.00")
    everything = ask(c, {"source": SRC}).json()
    assert everything["summary_raw"]["total"] == 1911.0
    assert everything["period"] == "2025-01-01 to 2025-01-03"
    assert ask(c, {"source": SRC, "date": "2025-02-01"}).status_code == 404


def test_risk_needs_real_stock_levels(client):
    c, _ = client
    unknown = ask(c, {"source": SRC}).json()["summary_raw"]["inventory_risks"]
    assert {r["risk"] for r in unknown} == {"unknown"}
    given = ask(c, {"source": SRC, "stock": {"Eggs": 20, "Milk": 500}, "window_days": 3}).json()
    risks = {r["product"]: r for r in given["summary_raw"]["inventory_risks"]}
    assert risks["Eggs"]["avg_daily_demand"] == 12.0 and risks["Eggs"]["risk"] == "high"
    assert risks["Milk"]["risk"] == "ok"


@pytest.mark.parametrize("path", ["../README.md", "/etc/passwd", "../../../../etc/hosts", "nope/../../app/main.py"])
def test_csv_path_cannot_escape_the_data_dir(client, path):
    c, _ = client
    r = ask(c, {"source": {"type": "csv", "csv_path": path}})
    assert r.status_code == 400 and "DATA_DIR" in r.json()["detail"]


def test_missing_csv_is_404(client):
    c, _ = client
    assert ask(c, {"source": {"type": "csv", "csv_path": "missing.csv"}}).status_code == 404


REC = {"bill_id": "B1", "item_name": "Eggs", "quantity": 12, "price": 6, "timestamp": "2025-01-03T10:30:00"}


def test_pos_sync_requires_the_key_and_validates(client):
    c, pos = client
    assert c.post("/sync-pos", json=[REC]).status_code == 401
    assert c.post("/sync-pos", json=[{**REC, "quantity": -1}], headers={"x-api-key": KEY}).status_code == 422
    assert c.post("/sync-pos", json=[{**REC, "timestamp": "yesterday"}], headers={"x-api-key": KEY}).status_code == 422
    assert c.post("/sync-pos", json=[], headers={"x-api-key": KEY}).status_code == 400
    r = c.post("/sync-pos", json=[REC, {**REC, "bill_id": "B2"}], headers={"x-api-key": KEY})
    assert r.status_code == 200 and r.json()["count"] == 2
    files = list(pos.glob("pos_*.csv"))
    assert len(files) == 1 and "stored_file" not in r.json()


def test_app_refuses_to_start_without_a_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    import app.main as main

    with pytest.raises(RuntimeError, match="API_KEY"):
        importlib.reload(main)
