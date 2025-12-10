import pandas as pd
from app.insights import total_revenue, top_sellers

def test_total_revenue():
    df = pd.DataFrame({"quantity":[1,2],"price":[10,20]})
    assert total_revenue(df) == 50