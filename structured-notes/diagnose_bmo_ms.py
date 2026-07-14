"""Deep diagnostic for BMO CUSIP and MS coupon patterns."""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import clean_html

init_db()
db = get_db()
client = SECClient(pause=0.25)

# --- BMO CUSIP ---
print("=" * 90)
print("  BMO CUSIP INVESTIGATION")
print("=" * 90)

bmo_no_cusip = (
    db.query(Product)
    .filter(Product.parent_issuer == "BMO", Product.cusip.is_(None), Product.is_preliminary == False)
    .limit(3)
    .all()
)

for p in bmo_no_cusip:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)

    print(f"\n  Filing: {filing.filing_date} | {filing.accession_number}")

    # Find ALL 9-char alphanumeric sequences that look like CUSIPs
    cusip_candidates = []
    for m in re.finditer(r"\b([0-9][A-Z0-9]{8})\b", clean[:80000]):
        # CUSIPs start with a digit for US issuers (BMO starts with 06)
        candidate = m.group(1)
        if candidate.startswith("06"):
            cusip_candidates.append((m.start(), candidate))
    if cusip_candidates:
        print(f"  CUSIP candidates starting with '06': {cusip_candidates[:5]}")
    else:
        # Try any 9-char starting with digit
        for m in re.finditer(r"\b(\d[A-Z0-9]{8})\b", clean[:80000]):
            cusip_candidates.append((m.start(), m.group(1)))
        print(f"  Any CUSIP-like (digit start): {cusip_candidates[:5]}")

    # Show the area around "CUSIP" keyword
    idx = clean.upper().find("CUSIP")
    if idx >= 0:
        print(f"  Around CUSIP keyword (pos {idx}):")
        print(f"    {clean[max(0,idx-50):idx+300]}")

# --- BMO with CUSIP (successful) ---
print(f"\n  BMO WITH CUSIP (successful extraction):")
bmo_with = (
    db.query(Product)
    .filter(Product.parent_issuer == "BMO", Product.cusip.isnot(None), Product.is_preliminary == False)
    .limit(2)
    .all()
)
for p in bmo_with:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)
    idx = clean.upper().find("CUSIP")
    if idx >= 0:
        print(f"  {p.cusip} | Around CUSIP (pos {idx}):")
        print(f"    {clean[max(0,idx-20):idx+200]}")


# --- MS Autocallable coupon ---
print(f"\n{'='*90}")
print(f"  MORGAN STANLEY AUTOCALLABLE COUPON INVESTIGATION")
print(f"{'='*90}")

ms_no_coupon = (
    db.query(Product)
    .filter(
        Product.parent_issuer == "Morgan Stanley",
        Product.coupon_rate.is_(None),
        Product.is_preliminary == False,
        Product.product_type == "autocallable",
    )
    .limit(3)
    .all()
)

for p in ms_no_coupon:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)

    print(f"\n  {p.cusip} | {filing.filing_date}")

    # Search for any percentage that might be a coupon
    for pattern, label in [
        (r"(?:Call\s+(?:Premium|Return)|Callable\s+Return|Contingent\s+(?:Return|Payment|Coupon)).{0,100}", "Call Premium/Return"),
        (r"(?:coupon|payment)\s+(?:rate|amount)[:\s]+.{0,100}", "Coupon/Payment rate"),
        (r"(?:at\s+least|minimum)\s+[\d.]+\s*%\s*(?:per|p\.a\.|of).{0,60}", "at least X%"),
        (r"(?:annualized|annual)\s+(?:return|yield|rate).{0,80}", "Annualized return/yield"),
        (r"earn.{0,20}[\d.]+\s*%.{0,60}", "earn X%"),
    ]:
        hits = list(re.finditer(pattern, clean[:30000], re.IGNORECASE))
        if hits:
            for h in hits[:2]:
                print(f"    [{label}] pos {h.start()}: {h.group()[:130]}")

db.close()
