import pytest
from app.connector import load_sales_csv

def test_csv_loading():
    df = load_sales_csv("samples/sample_sales.csv")
    assert not df.empty
    assert "product" in df.columns