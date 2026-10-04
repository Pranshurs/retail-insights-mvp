"""Push new rows from a shop's MySQL POS database to the Retail Insights API.

Runs next to the POS database (e.g. on the shop's PC) and polls every SYNC_INTERVAL_S.

    POS_DB_HOST, POS_DB_USER, POS_DB_PASSWORD, POS_DB_NAME   database access
    API_URL                                                  e.g. https://insights.example.com/sync-pos
    API_KEY                                                  sent as x-api-key
    POS_TABLE, POS_COL_BILL, POS_COL_ITEM, POS_COL_QTY, POS_COL_PRICE, POS_COL_TIME
                                                             your POS's table/column names
                                                             (defaults: sales, bill_id, item_name,
                                                             quantity, price, timestamp)
    WATERMARK_FILE                                           default .pos_sync_watermark
    SYNC_INTERVAL_S                                          default 600

Each run sends rows newer than the last successfully synced timestamp (the "watermark"),
oldest first. The watermark only advances after the API confirms the batch, so a failed
request is retried on the next run instead of losing rows. Rows that share the watermark's
exact timestamp could be sent twice in an edge case; the API stores batches, it doesn't
deduplicate them.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
DEFAULT_COLUMNS = {"POS_TABLE": "sales", "POS_COL_BILL": "bill_id", "POS_COL_ITEM": "item_name",
                   "POS_COL_QTY": "quantity", "POS_COL_PRICE": "price", "POS_COL_TIME": "timestamp"}


def build_query(env=os.environ) -> str:
    """SELECT for the configured table/columns. Names are checked as plain identifiers
    (they can't be bound as SQL parameters), so configuration can't inject SQL."""
    n = {k: env.get(k, v) for k, v in DEFAULT_COLUMNS.items()}
    bad = [f"{k}={v!r}" for k, v in n.items() if not _IDENT.match(v)]
    if bad:
        raise ValueError(f"invalid table/column names: {bad}")
    t = n["POS_COL_TIME"]
    return (f"SELECT `{n['POS_COL_BILL']}`, `{n['POS_COL_ITEM']}`, `{n['POS_COL_QTY']}`, `{n['POS_COL_PRICE']}`, `{t}` "
            f"FROM `{n['POS_TABLE']}` WHERE `{t}` > %s ORDER BY `{t}` LIMIT 10000")
EPOCH = "1970-01-01 00:00:00"


def read_watermark(path: Path) -> str:
    return path.read_text().strip() if path.exists() else EPOCH


def write_watermark(path: Path, value: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value)
    tmp.replace(path)  # atomic on POSIX


def to_payload(rows) -> list[dict]:
    return [{"bill_id": str(r[0]), "item_name": str(r[1]), "quantity": float(r[2]), "price": float(r[3]),
             "timestamp": (r[4].isoformat() if isinstance(r[4], datetime) else str(r[4]))} for r in rows]


def sync_once(fetch_rows, post, watermark_file: Path) -> int:
    """One sync pass. ``fetch_rows(since)`` returns rows; ``post(payload)`` raises on failure."""
    since = read_watermark(watermark_file)
    rows = fetch_rows(since)
    if not rows:
        return 0
    payload = to_payload(rows)
    post(payload)  # raises -> watermark untouched -> retried next run
    write_watermark(watermark_file, payload[-1]["timestamp"].replace("T", " "))
    return len(payload)


def _mysql_fetcher():
    import pymysql  # only needed by the agent: pip install -r requirements-agent.txt

    query = build_query()

    def fetch(since: str):
        conn = pymysql.connect(host=os.environ["POS_DB_HOST"], user=os.environ["POS_DB_USER"],
                               password=os.environ["POS_DB_PASSWORD"], database=os.environ["POS_DB_NAME"],
                               connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(query, (since,))
                return cur.fetchall()
        finally:
            conn.close()
    return fetch


def _poster():
    url, key = os.environ["API_URL"], os.environ["API_KEY"]

    def post(payload: list[dict]) -> None:
        r = requests.post(url, json=payload, headers={"x-api-key": key}, timeout=30)
        r.raise_for_status()
    return post


def main() -> None:
    wm = Path(os.getenv("WATERMARK_FILE", ".pos_sync_watermark"))
    interval = int(os.getenv("SYNC_INTERVAL_S", "600"))
    fetch, post = _mysql_fetcher(), _poster()
    while True:
        try:
            n = sync_once(fetch, post, wm)
            print(f"synced {n} new row(s)" if n else "no new rows")
        except Exception as exc:  # keep running; the next pass retries from the same watermark
            print(f"sync failed, will retry: {type(exc).__name__}: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
