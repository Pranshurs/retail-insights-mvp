from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.whatsapp import MockSender

KEY = "test-key-123"
PASSWORD = "a-long-owner-password"


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A fresh app + database with the owner signed in. Returns (client, app, csrf, sender)."""
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    sender = MockSender()
    app = create_app(f"sqlite:///{tmp_path}/store.db", sender=sender)
    c = TestClient(app)
    c.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD, "csrf": csrf_of(c.get("/setup").text)})
    return c, app, csrf_of(c.get("/").text), sender
