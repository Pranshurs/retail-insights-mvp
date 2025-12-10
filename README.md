# Retail Insights MVP

Retail Insights MVP is a **FastAPI backend** that allows shop owners to generate sales insights from their CSV sales data. It supports automatic POS data syncing, calculates total revenue, top-selling products, and inventory risks, and generates human-readable summaries.

---

## Features

- Generate insights from CSV sales data
- Calculate total revenue and top-selling products
- Identify products at inventory risk
- Sync POS records from external systems
- API key-based authentication for security
- Ready for cloud deployment (GCP, AWS, etc.)

---

## Project Structure

/app
├── main.py          # FastAPI app and endpoints
├── connector.py     # CSV loader
├── insights.py      # Insight calculations
└── prompts.py       # Text summary generator
/config
└── .env.example     # Environment variables template
/samples
└── sample_sales.csv # Sample CSV file
/tests
├── test_connector.py
└── test_insights.py
requirements.txt
Dockerfile

---

## Prerequisites

- Python 3.10+
- pip
- Git
- (Optional) Docker if deploying containerized
- (Optional) GCP/AWS account for API deployment

---

## Setup

1. Clone the repository:

```bash
git clone https://github.com/Pranshurs/retail-insights-mvp.git
cd retail-insights-mvp

	2.	Create a virtual environment and activate it:

python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

	3.	Install dependencies:

pip install -r requirements.txt

	4.	Copy the environment file and set your API key:

cp config/.env.example config/.env

Edit config/.env:

API_KEY=changeme123


⸻

Running the Server

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

	•	Open http://localhost:8000￼ to check if the API is running.
	•	Health check: http://localhost:8000/health￼
	•	Debug API key: http://localhost:8000/debug_api_key￼

⸻

API Endpoints

1. Generate Insights

POST /generate_insights

Headers:

Content-Type: application/json
x-api-key: <YOUR_API_KEY>

Body (JSON):

{
    "source": {
        "type": "csv",
        "csv_path": "samples/sample_sales.csv"
    },
    "date": "2025-01-03"
}

Response:

{
    "summary_raw": {
        "total": 1911.0,
        "top": {
            "Eggs": 36,
            "Milk": 24,
            "Bread": 9,
            "Butter": 5
        },
        "inventory_risks": [
            {"product":"Bread", "avg_daily_demand":4.5, "stock_assumed":20, "days_to_stockout":4.44, "risk":"high"}
        ]
    },
    "summary_text": "2025-01-03: total revenue ₹1911.00. Top sellers: Eggs (36), Milk (24), Bread (9), Butter (5). Items at risk: Bread, Eggs, Milk"
}


⸻

2. POS Sync

POST /sync-pos

Headers:

Content-Type: application/json

Body (JSON):

[
    {
        "bill_id": "B001",
        "item_name": "Eggs",
        "quantity": 12,
        "price": 60.0,
        "timestamp": "2025-01-03T10:30:00"
    }
]

Response:

{
    "status": "success",
    "stored_file": "data/pos_sync/sync_1700000000.0.csv",
    "count": 1
}


⸻

Development Notes
	•	The backend currently works with CSV files only. In the future, it can be extended to fetch data directly from shop owners’ databases (optional).
	•	Data storage: Currently, synced POS data is stored locally in data/pos_sync/. For production, you can connect it to a proper database (PostgreSQL/MySQL).
	•	API security: All critical endpoints require the x-api-key header.

⸻

Deployment
	•	Deploy as an API service on GCP Cloud Run / AWS Lambda / EC2.
	•	You can also containerize the app using Docker:

docker build -t retail-insights-mvp .
docker run -p 8000:8000 retail-insights-mvp


⸻

License

MIT License. See LICENSE￼.

