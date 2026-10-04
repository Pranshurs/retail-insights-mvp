from __future__ import annotations

from datetime import datetime

import pytest

import pos_sync_agent as agent

ROWS = [("B1", "Milk", 2, 50, datetime(2025, 1, 3, 9, 0)), ("B2", "Eggs", 12, 6, datetime(2025, 1, 3, 10, 30))]


def test_sends_new_rows_and_advances_the_watermark(tmp_path):
    wm, sent, asked = tmp_path / "wm", [], []

    def fetch(since):
        asked.append(since)
        return ROWS if since == agent.EPOCH else []

    assert agent.sync_once(fetch, sent.append, wm) == 2
    assert sent[0][1] == {"bill_id": "B2", "item_name": "Eggs", "quantity": 12.0, "price": 6.0,
                          "timestamp": "2025-01-03T10:30:00"}
    assert wm.read_text() == "2025-01-03 10:30:00"
    assert agent.sync_once(fetch, sent.append, wm) == 0  # nothing re-sent
    assert asked == [agent.EPOCH, "2025-01-03 10:30:00"]


def test_failed_upload_keeps_the_watermark_so_rows_are_retried(tmp_path):
    wm = tmp_path / "wm"

    def failing(payload):
        raise ConnectionError("API down")

    with pytest.raises(ConnectionError):
        agent.sync_once(lambda since: ROWS, failing, wm)
    assert not wm.exists()
    assert agent.sync_once(lambda since: ROWS, lambda p: None, wm) == 2


def test_column_mapping_and_injection_guard():
    q = agent.build_query({"POS_TABLE": "bills", "POS_COL_ITEM": "item_desc"})
    assert "FROM `bills`" in q and "`item_desc`" in q and q.endswith("LIMIT 10000")
    with pytest.raises(ValueError, match="invalid"):
        agent.build_query({"POS_TABLE": "sales; DROP TABLE sales"})
