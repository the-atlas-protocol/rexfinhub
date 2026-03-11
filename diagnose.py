"""Diagnose parser failures across all weak issuers.

For each issuer with low fill rates, fetch 5 filings with NULL values
and show what text patterns exist in the HTML.
"""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import clean_html

init_db()
db = get_db()
client = SECClient(pause=0.25)

# Define what to investigate: (issuer_name, field, null_check)
INVESTIGATIONS = [
    # CUSIP gaps
    ("BMO", "cusip", lambda p: p.cusip is None and not p.is_preliminary),
    # Coupon gaps
    ("Goldman Sachs", "coupon", lambda p: p.coupon_rate is None and not p.is_preliminary and p.product_type != "callable"),
    ("Barclays", "coupon", lambda p: p.coupon_rate is None and not p.is_preliminary and p.product_type != "callable"),
    ("RBC", "coupon", lambda p: p.coupon_rate is None and not p.is_preliminary and p.product_type != "callable"),
    ("Morgan Stanley", "coupon", lambda p: p.coupon_rate is None and not p.is_preliminary and p.product_type != "callable"),
    ("BMO", "coupon", lambda p: p.coupon_rate is None and not p.is_preliminary and p.product_type != "callable"),
    # Barrier gaps
    ("Deutsche Bank", "barrier", lambda p: p.barrier_level is None and not p.is_preliminary),
    ("Wells Fargo", "barrier", lambda p: p.barrier_level is None and not p.is_preliminary),
    ("BMO", "barrier", lambda p: p.barrier_level is None and not p.is_preliminary and p.product_type == "autocallable"),
    ("CIBC", "barrier", lambda p: p.barrier_level is None and not p.is_preliminary and p.product_type == "autocallable"),
]

FIELD_PATTERNS = {
    "cusip": [
        (r"CUSIP.{0,100}", "CUSIP context"),
    ],
    "coupon": [
        (r"(?:Contingent\s+)?(?:Interest|Coupon)\s+(?:Rate|Payment).{0,120}", "Coupon/Interest Rate"),
        (r"[\d.]+\s*%\s*per\s+(?:annum|quarter|month|year).{0,60}", "X% per period"),
        (r"(?:Fixed|Floating)\s+(?:Rate|Coupon).{0,80}", "Fixed/Floating rate"),
    ],
    "barrier": [
        (r"(?:Barrier|Trigger|Knock|Threshold|Downside|Buffer).{0,120}", "Barrier/Trigger/Buffer"),
        (r"[\d.]+\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:Initial|Starting|Hypothetical).{0,60}", "X% of Initial"),
    ],
}


for issuer_name, field, check_fn in INVESTIGATIONS:
    # Get products matching the check
    products = db.query(Product).filter(Product.parent_issuer == issuer_name).all()
    failures = [p for p in products if check_fn(p)]

    if not failures:
        continue

    print(f"\n{'='*90}")
    print(f"  {issuer_name} | {field} | {len(failures)} failures out of {len([p for p in products if not p.is_preliminary])} final products")
    print(f"{'='*90}")

    # Sample up to 3 failures
    for p in failures[:3]:
        filing = db.get(Filing, p.filing_id)
        html = client.fetch_text(filing.primary_doc_url)
        clean = clean_html(html)

        print(f"\n  --- {p.cusip or 'NO_CUSIP'} | {filing.filing_date} | type={p.product_type} ---")

        patterns = FIELD_PATTERNS.get(field, [])
        for regex, label in patterns:
            hits = list(re.finditer(regex, clean[:60000], re.IGNORECASE))
            if hits:
                print(f"  [{label}] {len(hits)} hits:")
                for h in hits[:3]:
                    snippet = h.group()[:140].replace('\n', ' ')
                    print(f"    pos {h.start():>6}: {snippet}")
            else:
                print(f"  [{label}] NO HITS")

        # For CUSIP: show the first 300 chars of clean text
        if field == "cusip":
            print(f"  [Cover]: {clean[:300]}")

db.close()
print("\n\nDiagnostics complete.")
