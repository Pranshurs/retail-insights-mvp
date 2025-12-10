import pandas as pd
from typing import Dict, List

def total_revenue(df: pd.DataFrame) -> float:
    return float((df['quantity'] * df['price']).sum())

def top_sellers(df: pd.DataFrame, n=5) -> Dict[str, int]:
    series = df.groupby('product')['quantity'].sum().sort_values(ascending=False).head(n)
    return series.to_dict()

def inventory_risks(df: pd.DataFrame, stock_mapping: dict = None) -> List[Dict]:
    df2 = df.copy()
    df2['date_only'] = df2['date'].dt.date
    daily = df2.groupby(['product','date_only'])['quantity'].sum().reset_index()
    avg7 = daily.groupby('product')['quantity'].mean().to_dict()
    risks = []

    for product, avg in avg7.items():
        stock = stock_mapping.get(product, 20) if stock_mapping else 20
        days_to_stockout = stock / (avg if avg > 0 else 0.001)
        risk = "high" if days_to_stockout < 7 else "ok"
        risks.append({
            "product": product,
            "avg_daily_demand": float(avg),
            "stock_assumed": stock,
            "days_to_stockout": float(days_to_stockout),
            "risk": risk
        })
    return risks