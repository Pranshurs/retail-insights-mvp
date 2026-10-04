# Retail Store Manager

[![tests](https://github.com/Pranshurs/retail-insights-mvp/actions/workflows/ci.yml/badge.svg)](https://github.com/Pranshurs/retail-insights-mvp/actions/workflows/ci.yml)

A store manager for small shops, built to plug into the till they already use. It gives
the owner a dashboard of today's sales, best sellers and what to restock. It keeps stock
up to date automatically as sales come in, and it sends deals to customers on WhatsApp.

| Part | What it does |
|---|---|
| **Owner dashboard** | Sales today and over 7 days, best sellers, restock alerts with suggested order quantities, customers and deals. Password-protected and phone-friendly. |
| **Stock** | Every sale reduces stock. Restocks, counts and adjustments are recorded as movements, so stock always matches its history. Shows "days left" from recent demand. |
| **Store-tracker connection** | Import CSV exports from any POS (common column names are recognised, and re-imports never double-count), or run the sync agent next to a MySQL-based POS for automatic updates. |
| **WhatsApp deals** | Customers with recorded consent get deal messages through Meta's WhatsApp Cloud API. STOP replies unsubscribe them automatically, and nobody gets the same deal twice. |

> **This is the demo edition.** It works as described below: you can run it for a real
> shop today, single store, single owner. If you want a **fully customised version** for
> your business, I build those to order. Examples:
> - integration with your exact POS or billing software;
> - multiple branches and staff accounts;
> - your own branding;
> - richer WhatsApp automation (order updates, reminders, replies in the dashboard);
> - hosted and maintained for you.
>
> Contact **pranshu.rs08@gmail.com** with what you need.

It began in December 2025 as a small CSV-insights API. In October 2026 it was rebuilt
into this store manager; the history shows both.

## Start it

```bash
docker compose up -d          # then open http://localhost:8080
```

Or, without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

On first visit you set the owner password; nothing else is required.
- **Demo data:** to try it with sample data first, run `python -m app.demo` (or
  `docker compose exec store python -m app.demo`). It adds sample products, two weeks of
  sales and three demo customers with fake numbers.
- **Where data lives:** everything is stored in one SQLite file (`data/store.db`, a Docker
  volume under Compose).
- **Settings:** configuration goes in `config/.env` (copy `config/.env.example`). All of it
  is optional for a local start.

## Connect your store tracker

**CSV (works with almost any POS).** On the Import page, upload:
- a sales export with date, product, quantity and price (a bill number is optional);
- a stock list with product and stock (price and reorder level are optional).

Column names like `Item`, `Qty`, `Rate`, `Bill No`, `Closing Stock` and `MRP` are
recognised. Add your own with `COLUMN_MAP`. Importing overlapping exports never counts a
sale twice.

**Automatic sync (MySQL-based POS).** Run the agent on the shop PC that has the POS
database:

```bash
pip install -r requirements-agent.txt
POS_DB_HOST=localhost POS_DB_USER=... POS_DB_PASSWORD=... POS_DB_NAME=... \
POS_TABLE=bills POS_COL_BILL=invoice_no POS_COL_ITEM=item_desc POS_COL_QTY=qty \
POS_COL_PRICE=rate POS_COL_TIME=billed_at \
API_URL=https://your-server/sync-pos API_KEY=... python pos_sync_agent.py
```

The agent:
- polls every 10 minutes and sends only rows newer than the last confirmed sync;
- retries after a failure without losing or duplicating sales;
- validates table and column names before using them in SQL.

It's tested against a real MySQL 8.4 database in CI.

## WhatsApp deals

Until you add credentials, deals use a **mock sender**. The whole flow works, but nothing
is sent. To send for real:
1. **Set up the account.** Create a WhatsApp Business account in Meta's WhatsApp Manager,
   and note the phone number ID and an access token.
2. **Create the deal template.** Make a message template whose body has three variables,
   e.g. *"Hi {{1}}! Today at our store: {{2}}. {{3}} Reply STOP to unsubscribe."* Meta must
   approve it. WhatsApp only lets businesses start conversations with approved templates.
3. **Configure the app.** Set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` and restart.
   Use the template's name when you create a deal.
4. **Turn on automatic opt-outs.** Point the app's webhook at `https://your-server/webhooks/whatsapp`,
   and set `WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET`. Incoming requests are checked
   against Meta's `X-Hub-Signature-256` signature, and a STOP reply unsubscribes the
   customer.

**Consent.** Add only customers who agreed to receive messages. WhatsApp's rules and
India's DPDP Act both require it. The app makes you record how each customer agreed, and
never messages anyone who opted out.

## Security

- **Dashboard login:**
  - one owner password, stored as a salted scrypt hash;
  - signed sessions;
  - a CSRF token on every form;
  - a limit on failed logins.
- **Machine endpoints** (`/sync-pos`, `/api/stock`, `/generate_insights`): need
  `API_KEY`, compared in constant time. They're switched off if no key is set.
- **WhatsApp webhook:** a signature check and a verify token.
- **Uploads:** capped at 5 MB, with row-level validation.
- **Docker:** runs as a non-root user.

For anything beyond a local network, put it behind HTTPS (e.g. Caddy or nginx).

## Tests

```bash
pip install -r requirements-dev.txt && pytest -q
```

There are 32 tests, covering:
- setup, login, throttling and CSRF;
- stock movements adding up to the stock figure;
- CSV import with deduplication and different column names;
- consent rules;
- deals: at-most-once delivery, retrying failures, and never resending a message that may
  have gone out;
- the exact WhatsApp Cloud API request;
- webhook signatures and STOP handling;
- the POS agent against a real MySQL with custom column names.

Each of 16 safety and correctness rules was checked by breaking it on purpose, and every
broken version fails the tests.

## Limitations

- **One shop and one owner account.** There are no staff roles and no multiple branches.
- **POS connection:** direct sync supports MySQL. Other POS systems connect through CSV
  export.
- **WhatsApp:** only template messages are sent. Replies other than STOP aren't shown in
  the dashboard.
- **Restock suggestions:** based on the last 7 days of sales. There's no seasonality or
  supplier lead time.
- **Not tested with a real WhatsApp Business account.** The live WhatsApp sending path is
  tested against Meta's documented request and response format, not a real account.

## Custom version

The demo edition covers one shop, CSV and MySQL POS connections, and template deals on
WhatsApp. A custom build can add, for example:
- a direct connection to your POS or accounting software (Tally, Marg, Vyapar and others);
- several branches, with staff logins and permissions;
- supplier ordering from restock suggestions;
- two-way WhatsApp, for customer replies and order or delivery updates;
- GST-ready reports;
- your branding;
- hosting with backups and support.

Write to pranshu.rs08@gmail.com with your shop's setup and what you'd like it to do.

## License

Proprietary: © 2025 Pranshu Raj, all rights reserved (see [`LICENSE`](LICENSE)). The source
is public so it can be read and reviewed. You're welcome to use, copy, modify or build on
it **with my written permission**: email pranshu.rs08@gmail.com and say what you'd like
to do. Without that permission, no reuse rights are granted.
