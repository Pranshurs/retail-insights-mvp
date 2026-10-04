"""Fill an empty database with clearly-labelled demo data so the dashboard has something to show.

    python -m app.demo            # uses DATABASE_URL (default sqlite:///data/store.db)

Products, two weeks of sales ending today, and three demo customers with fake numbers
(+91 90000 0000x). It refuses to run if the database already has products.
"""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import select

from app.db import Product, make_engine, make_sessionmaker, store_now
from app.services import campaigns, stock

CATALOGUE = [("Milk 1L", 52, 60, 15, 18), ("Bread", 40, 25, 8, 6), ("Eggs (6)", 42, 30, 10, 9),
             ("Butter 100g", 56, 12, 4, 2), ("Rice 5kg", 360, 15, 5, 1.2), ("Biscuits", 20, 80, 20, 7)]


def main() -> None:
    engine = make_engine()
    Session = make_sessionmaker(engine)
    rng = random.Random(7)
    with Session() as db:
        if db.scalar(select(Product.id).limit(1)) is not None:
            raise SystemExit("database already has products; demo data not added")
        now = store_now()
        for name, price, start_stock, reorder, per_day in CATALOGUE:
            p = stock.get_or_create_product(db, name, price)
            p.reorder_level = reorder
            stock.set_count(db, p, start_stock + per_day * 14, "demo opening stock")
        for day in range(13, -1, -1):
            for name, price, _, _, per_day in CATALOGUE:
                for i in range(max(0, round(rng.gauss(per_day, per_day * 0.3)))):
                    when = (now - timedelta(days=day)).replace(hour=rng.randint(8, 20), minute=rng.randint(0, 59))
                    if when > now:
                        continue
                    stock.record_sale(db, ref=stock.sale_ref("demo", name, day, i), name=name, quantity=1,
                                      price=price, sold_at=when, bill_id=f"DEMO-{day}-{i}", source="manual")
        db.commit()
        for n, who in enumerate(["Asha (demo)", "Ravi (demo)", "Meera (demo)"], start=1):
            campaigns.add_customer(db, who, f"+91900000000{n}", "demo data")
    print("Demo data added. Start the app and sign in.")


if __name__ == "__main__":
    main()
