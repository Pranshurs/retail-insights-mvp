# Retail Insights

[![tests](https://github.com/Pranshurs/retail-insights-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/Pranshurs/retail-insights-mvp/actions/workflows/ci.yml)

A small FastAPI service that turns a shop's sales records into a daily summary: revenue,
top sellers, and which products are likely to run out. A companion agent pushes new sales
from a shop's MySQL point-of-sale database to the API.

It began in December 2025 as a two-day MVP. In October 2026 it was reviewed before being
made public, and these were fixed:
- **Exposed API key.** A debug endpoint returned the key to anyone.
- **Unauthenticated POS sync.**
- **Arbitrary file reads.** The service read any `csv_path` it was given.
- **Wrong date reporting.** Reports for "a date" summed every date.
- **Invented stock levels.** Stock-out risk was computed from an assumed 20 units per
  product.
- **Repository clutter.** A committed virtualenv (12,500 files).

The original commits are kept in the history.

## What it computes

For `POST /generate_insights` with `{"source": {"type": "csv", "csv_path": "sample_sales.csv"}, "date": "2025-01-03"}`:

```
2025-01-03: total revenue ₹474.00. Top sellers: Eggs (14), Milk (6), Butter (2).
Likely to run out within a week: Eggs.
```

How each figure is calculated:
- **Revenue and top sellers** cover the requested day, or all the data if you give no date.
- **Stock-out risk** uses the stock levels you pass in, for example
  `"stock": {"Eggs": 20, "Milk": 40}`. It compares them with average daily demand over the
  last `window_days` (default 7), counting days without sales as zero demand.
  - A product without a stock level is reported as `unknown`; nothing is assumed.
  - A product is `high` risk if it would run out within 7 days.
- **The summary text** is a fixed template filled with these numbers. No LLM is involved.

## Run it

```bash
pip install -r requirements.txt
cp config/.env.example config/.env      # set API_KEY to a long random string
uvicorn app.main:app --port 8000
```

```bash
curl -X POST localhost:8000/generate_insights -H "x-api-key: $API_KEY" -H "content-type: application/json" \
  -d '{"source": {"type": "csv", "csv_path": "sample_sales.csv"}, "date": "2025-01-03", "stock": {"Eggs": 20, "Milk": 40}}'
```

To run it with Docker:

```bash
docker build -t retail-insights . && docker run -e API_KEY=... -p 8080:8080 retail-insights
```

The container runs as a non-root user.

## Endpoints

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /health` | none | liveness |
| `POST /generate_insights` | `x-api-key` | figures for one day or all days, from a CSV inside `DATA_DIR` (columns: `date, product, quantity, price`) |
| `POST /sync-pos` | `x-api-key` | stores a batch of POS records (validated: positive quantity, non-negative price, real timestamp; at most 10,000 per request) |

**Security model:**
- One shared API key, compared in constant time.
- The service won't start without a key.
- `csv_path` is resolved inside `DATA_DIR`, and anything outside it is refused.
- Synced batches are written to `POS_SYNC_DIR`; the response doesn't reveal server paths.

## POS sync agent

`pos_sync_agent.py` runs next to the shop's MySQL POS database:

```bash
pip install -r requirements-agent.txt
POS_DB_HOST=... POS_DB_USER=... POS_DB_PASSWORD=... POS_DB_NAME=... \
API_URL=https://your-host/sync-pos API_KEY=... python pos_sync_agent.py
```

Each pass sends rows newer than the last synced timestamp, oldest first. That timestamp
(the watermark) is stored in a file and only moves forward after the API accepts the
batch, so a failed upload is retried rather than lost.

## Tests

```bash
pip install -r requirements-dev.txt && pytest -q
```

The 16 tests cover:
- authentication on both data endpoints, and that the key is never exposed;
- path-traversal attempts;
- date filtering (₹474 for 3 January vs ₹1,911 overall);
- stock-out maths, including days without sales;
- POS record validation;
- the agent's watermark and retry behaviour.

Each of those rules was checked by breaking it on purpose: all 10 deliberately broken
versions fail the tests.

## Limitations

- CSV input only; synced POS batches are stored as CSV files, not in a database.
- A single shared API key. There are no per-shop accounts.
- Revenue is shown in ₹ regardless of the data's currency.
- It hasn't been deployed. The original Cloud Run workflow never ran successfully and was
  removed.

## License

Proprietary: © 2025 Pranshu Raj, all rights reserved (see [`LICENSE`](LICENSE)). The source
is public so it can be read and reviewed. Copying, modifying, distributing or building on
it requires written permission.
