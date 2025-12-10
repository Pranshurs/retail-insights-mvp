from typing import Dict, List, Optional

def render_summary_text(date: Optional[str], total: float, top: Dict[str,int], risks: List[Dict]) -> str:
    date_str = date or "yesterday"
    top_list = ", ".join([f"{k} ({v})" for k,v in top.items()]) if top else "no sales"
    high_risks = [r['product'] for r in risks if r['risk'] == 'high']
    if not high_risks:
        risk_text = "No immediate stockout risks detected."
    else:
        risk_text = "Items at risk: " + ", ".join(high_risks)
    return f"{date_str}: total revenue ₹{total:.2f}. Top sellers: {top_list}. {risk_text}"