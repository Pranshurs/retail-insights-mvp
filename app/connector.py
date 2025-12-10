import pandas as pd
from pathlib import Path

def load_sales_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    df = pd.read_csv(p)
    df_columns = {c.lower(): c for c in df.columns}

    def getcol(name):
        return df_columns.get(name, None)

    renames = {}
    for expected in ['date','txn_id','product','quantity','price']:
        col = getcol(expected)
        if col and col != expected:
            renames[col] = expected
    if renames:
        df = df.rename(columns=renames)
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0).astype(int)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0.0)
    return df