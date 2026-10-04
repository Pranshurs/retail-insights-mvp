"""The POS agent against a real MySQL and a real running server (not mocks).

Uses MYSQL_HOST/MYSQL_PORT if set (CI provides a MySQL service), otherwise starts a
mysql:8.4 container with Docker. Skipped when neither is available.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn

import pos_sync_agent as agent
from app.main import create_app

KEY = "integration-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mysql():
    pymysql = pytest.importorskip("pymysql")
    if os.getenv("MYSQL_HOST"):
        host, port, container = os.environ["MYSQL_HOST"], int(os.getenv("MYSQL_PORT", "3306")), None
    elif shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        port = _free_port()
        container = subprocess.run(
            ["docker", "run", "-d", "--rm", "-e", "MYSQL_ROOT_PASSWORD=rootpw", "-e", "MYSQL_DATABASE=posdb",
             "-p", f"127.0.0.1:{port}:3306", "mysql:8.4"], capture_output=True, text=True, check=True).stdout.strip()
        host = "127.0.0.1"
    else:
        pytest.skip("needs Docker or MYSQL_HOST")
    deadline = time.time() + 120
    while True:
        try:
            conn = pymysql.connect(host=host, port=port, user="root", password=os.getenv("MYSQL_PASSWORD", "rootpw"),
                                   database="posdb", connect_timeout=3)
            break
        except pymysql.err.OperationalError:
            if time.time() > deadline:
                raise
            time.sleep(2)
    yield conn, host, port
    conn.close()
    if container:
        subprocess.run(["docker", "stop", container], capture_output=True)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    app = create_app(f"sqlite:///{tmp_path}/store.db")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)


def test_agent_syncs_a_real_pos_table_with_custom_column_names(mysql, server, tmp_path, monkeypatch):
    conn, host, port = mysql
    app, url = server
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS bills")
        cur.execute("""CREATE TABLE bills (invoice_no VARCHAR(20), item_desc VARCHAR(100), qty DECIMAL(10,2),
                       rate DECIMAL(10,2), billed_at DATETIME)""")
        cur.executemany("INSERT INTO bills VALUES (%s,%s,%s,%s,%s)", [
            ("INV1", "Milk 1L", 2, 52, "2026-10-01 09:00:00"),
            ("INV1", "Bread", 1, 40, "2026-10-01 09:00:00"),
            ("INV2", "Milk 1L", 1, 52, "2026-10-01 11:30:00")])
    conn.commit()
    for k, v in {"POS_DB_HOST": host, "POS_DB_USER": "root", "POS_DB_PASSWORD": os.getenv("MYSQL_PASSWORD", "rootpw"),
                 "POS_DB_NAME": "posdb", "POS_TABLE": "bills", "POS_COL_BILL": "invoice_no",
                 "POS_COL_ITEM": "item_desc", "POS_COL_QTY": "qty", "POS_COL_PRICE": "rate",
                 "POS_COL_TIME": "billed_at", "API_URL": f"{url}/sync-pos", "API_KEY": KEY}.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setattr("pymysql.connect", _with_port(port))
    wm = tmp_path / "wm"
    fetch, post = agent._mysql_fetcher(), agent._poster()

    assert agent.sync_once(fetch, post, wm) == 3
    assert wm.read_text() == "2026-10-01 11:30:00"
    assert agent.sync_once(fetch, post, wm) == 0           # nothing new
    with conn.cursor() as cur:
        cur.execute("INSERT INTO bills VALUES ('INV3','Bread',2,40,'2026-10-01 18:00:00')")
    conn.commit()
    assert agent.sync_once(fetch, post, wm) == 1           # only the new row

    import httpx

    stock = {r["product"]: r["stock"] for r in httpx.get(f"{url}/api/stock", headers={"x-api-key": KEY}).json()}
    assert stock == {"Bread": -3.0, "Milk 1L": -3.0}       # sales reduced stock (never counted yet)


def _with_port(port):
    import pymysql

    real = pymysql.connect

    def connect(**kw):
        return real(port=port, **kw)
    return connect
