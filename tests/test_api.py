"""Machine endpoints: API key, POS sync into the store, and the original CSV insights endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import KEY

REC = {"bill_id": "B1", "item_name": "Eggs", "quantity": 12, "price": 6, "timestamp": "2025-01-03T10:30:00"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("DATA_DIR", "samples")
    return TestClient(create_app(f"sqlite:///{tmp_path}/s.db"))


def ask(c, body, key=KEY):
    return c.post("/generate_insights", json=body, headers={"x-api-key": key} if key else {})


SRC = {"type": "csv", "csv_path": "sample_sales.csv"}


def test_machine_endpoints_require_the_key(client):
    assert ask(client, {"source": SRC}, key=None).status_code == 401
    assert ask(client, {"source": SRC}, key="wrong").status_code == 401
    assert client.post("/sync-pos", json=[REC]).status_code == 401
    assert client.get("/api/stock").status_code == 401
    assert client.get("/debug_api_key").status_code in (404, 405)


def test_machine_endpoints_are_off_without_a_configured_key(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    c = TestClient(create_app(f"sqlite:///{tmp_path}/x.db"))
    assert c.post("/sync-pos", json=[REC], headers={"x-api-key": ""}).status_code == 503


def test_date_filters_the_figures(client):
    day = ask(client, {"source": SRC, "date": "2025-01-03"}).json()
    assert day["summary_raw"]["total"] == 474.0
    assert ask(client, {"source": SRC}).json()["summary_raw"]["total"] == 1911.0
    assert ask(client, {"source": SRC, "date": "2025-02-01"}).status_code == 404


@pytest.mark.parametrize("path", ["../README.md", "/etc/passwd", "../../../../etc/hosts"])
def test_csv_path_cannot_escape_the_data_dir(client, path):
    assert ask(client, {"source": {"type": "csv", "csv_path": path}}).status_code == 400


def test_pos_sync_records_sales_reduces_stock_and_ignores_resends(client):
    h = {"x-api-key": KEY}
    first = client.post("/sync-pos", json=[REC, {**REC, "bill_id": "B2", "quantity": 3}], headers=h).json()
    assert first == {"status": "stored", "added": 2, "duplicates": 0}
    again = client.post("/sync-pos", json=[REC], headers=h).json()  # agent retried after a timeout
    assert again["added"] == 0 and again["duplicates"] == 1
    eggs = next(r for r in client.get("/api/stock", headers=h).json() if r["product"] == "Eggs")
    assert eggs["stock"] == -15.0 and eggs["status"] == "not counted"


def test_pos_sync_validates_records(client):
    h = {"x-api-key": KEY}
    assert client.post("/sync-pos", json=[{**REC, "quantity": -1}], headers=h).status_code == 422
    assert client.post("/sync-pos", json=[{**REC, "timestamp": "yesterday"}], headers=h).status_code == 422
    assert client.post("/sync-pos", json=[], headers=h).status_code == 400
