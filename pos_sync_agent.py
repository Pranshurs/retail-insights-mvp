import time
import pymysql
import requests

API_URL = "http://YOUR_SERVER_IP/sync-pos"

# ---------------------------
# Database Connection
# ---------------------------
def get_db_connection():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="password",
        database="posdb"
    )

# ---------------------------
# Fetch new records
# ---------------------------
def fetch_latest_records():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT bill_id, item_name, quantity, price, timestamp
        FROM sales
        WHERE timestamp >= NOW() - INTERVAL 10 MINUTE
    """)

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows

# ---------------------------
# Send records to API
# ---------------------------
def send_to_api(records):
    payload = []

    for r in records:
        payload.append({
            "bill_id": r[0],
            "item_name": r[1],
            "quantity": float(r[2]),
            "price": float(r[3]),
            "timestamp": str(r[4])
        })

    requests.post(API_URL, json=payload)

# ---------------------------
# Loop forever
# ---------------------------
def main_loop():
    while True:
        print("Checking for new POS data...")
        records = fetch_latest_records()

        if records:
            print(f"Found {len(records)} new rows. Syncing...")
            send_to_api(records)
        else:
            print("No new data found.")

        time.sleep(600)  # check every 10 minutes


if __name__ == "__main__":
    main_loop()