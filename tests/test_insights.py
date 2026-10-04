import pandas as pd

from app.insights import total_revenue


def test_total_revenue():
    df = pd.DataFrame({"quantity":[1,2],"price":[10,20]})
    assert total_revenue(df) == 50


def test_days_without_sales_count_as_zero_demand():
    from datetime import date

    from app.connector import load_sales_csv
    from app.insights import inventory_risks

    df = load_sales_csv("samples/sample_sales.csv")
    risks = {r["product"]: r for r in inventory_risks(df, stock={"Butter": 10}, end=date(2025, 1, 3), window_days=3)}
    # Butter sold 3 on Jan 2 and 2 on Jan 3, nothing on Jan 1: 5 units / 3 days, not 5 / 2
    assert risks["Butter"]["avg_daily_demand"] == round(5 / 3, 3)
    assert risks["Butter"]["days_to_stockout"] == round(10 / (5 / 3), 2)
