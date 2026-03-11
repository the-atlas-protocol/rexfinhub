"""Critical audit of extraction quality.

Don't assume failures are correct. Verify every gap.
"""
import sys, io, re
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import clean_html

init_db()
db = get_db()
client = SECClient(pause=0.25)


# ============================================================
# 1. UNCLASSIFIED PRODUCTS — What are they actually?
# ============================================================
print("=" * 90)
print("  1. UNCLASSIFIED PRODUCTS (398 products)")
print("=" * 90)

unclassified = db.query(Product).filter(Product.product_type == "unclassified").limit(10).all()
for p in unclassified[:6]:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)
    # Get product description from first 500 chars
    print(f"\n  {p.parent_issuer} | {p.cusip} | prelim={p.is_preliminary}")
    # What kind of product is this?
    cover = clean[:600].lower()
    keywords = []
    for kw in ["auto callable", "autocallable", "callable", "buffered", "buffer",
               "digital", "leveraged", "enhanced", "principal protected",
               "accelerated", "reverse convertible", "range accrual",
               "growth", "participation", "return note", "market-linked",
               "contingent", "fixed rate", "step-up", "step up"]:
        if kw in cover:
            keywords.append(kw)
    print(f"    Cover keywords: {keywords or 'NONE'}")
    print(f"    Cover: {clean[:250]}")


# ============================================================
# 2. PRELIMINARY DETECTION — Are we over-counting?
# ============================================================
print(f"\n{'=' * 90}")
print(f"  2. PRELIMINARY DETECTION AUDIT")
print(f"{'=' * 90}")

prelims = db.query(Product).filter(Product.is_preliminary == True).limit(8).all()
false_prelim_count = 0
for p in prelims[:8]:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)
    cover = clean[:3000].lower()
    has_preliminary = "preliminary" in cover
    has_subject = "subject to completion" in cover
    has_cusip = p.cusip is not None
    has_notional = p.notional_amount is not None and p.notional_amount > 0

    # If it has a CUSIP AND notional, it's probably NOT preliminary
    if has_cusip and has_notional and not has_preliminary and not has_subject:
        false_prelim_count += 1
        print(f"  FALSE PRELIM? {p.parent_issuer} | {p.cusip} | ${p.notional_amount:,.0f}")
        print(f"    has 'preliminary': {has_preliminary}, has 'subject to completion': {has_subject}")
        print(f"    Cover: {clean[:200]}")
    elif has_preliminary or has_subject:
        # Verify it's truly preliminary
        print(f"  TRUE PRELIM: {p.parent_issuer} | cusip={p.cusip} | notl={p.notional_amount}")

# Count how many "preliminaries" have CUSIPs
prelim_with_cusip = db.query(Product).filter(
    Product.is_preliminary == True, Product.cusip.isnot(None)
).count()
total_prelim = db.query(Product).filter(Product.is_preliminary == True).count()
print(f"\n  Prelims with CUSIP: {prelim_with_cusip}/{total_prelim} ({prelim_with_cusip/total_prelim*100:.0f}%)")
print(f"  NOTE: Prelims shouldn't have CUSIPs (assigned at pricing). High count = misclassification.")


# ============================================================
# 3. COUPON AUDIT — Are "no coupon" claims actually true?
# ============================================================
print(f"\n{'=' * 90}")
print(f"  3. COUPON AUDIT — Verifying 'no coupon' products")
print(f"{'=' * 90}")

for issuer_name in ["Goldman Sachs", "Barclays", "RBC"]:
    no_coupon = (
        db.query(Product)
        .filter(
            Product.parent_issuer == issuer_name,
            Product.is_preliminary == False,
            Product.coupon_rate.is_(None),
            Product.product_type.in_(["autocallable", "digital"]),
        )
        .limit(3)
        .all()
    )
    if not no_coupon:
        # Check unclassified too
        no_coupon = (
            db.query(Product)
            .filter(
                Product.parent_issuer == issuer_name,
                Product.is_preliminary == False,
                Product.coupon_rate.is_(None),
            )
            .limit(3)
            .all()
        )

    print(f"\n  --- {issuer_name} (no coupon, non-callable final products) ---")
    for p in no_coupon[:2]:
        filing = db.get(Filing, p.filing_id)
        html = client.fetch_text(filing.primary_doc_url)
        clean = clean_html(html)

        # Search for ANY percentage that could be a coupon
        coupon_hits = []
        for pattern, label in [
            (r"(?:contingent|conditional|variable)\s+(?:coupon|interest|payment).{0,120}", "contingent coupon/interest"),
            (r"[\d.]+\s*%\s*(?:per\s+(?:annum|quarter|month|year)|p\.a\.)", "X% per period"),
            (r"(?:coupon|interest)\s+(?:rate|payment|amount)[:\s]+[\d.]+\s*%", "coupon/interest rate: X%"),
            (r"(?:annual|annualized)\s+(?:return|rate|yield|coupon).{0,60}[\d.]+\s*%", "annual return/rate X%"),
            (r"(?:premium|return|payment).{0,40}[\d.]+\s*%\s*(?:of|per)", "premium/return X%"),
        ]:
            for m in re.finditer(pattern, clean[:30000], re.IGNORECASE):
                coupon_hits.append(f"[{label}] pos {m.start()}: {m.group()[:120]}")

        print(f"  {p.cusip} | type={p.product_type}")
        if coupon_hits:
            print(f"    MISSED COUPON? {len(coupon_hits)} hits:")
            for h in coupon_hits[:3]:
                print(f"      {h}")
        else:
            print(f"    Genuinely no coupon.")


# ============================================================
# 4. CIBC MATURITY — Why 80%?
# ============================================================
print(f"\n{'=' * 90}")
print(f"  4. CIBC MATURITY FAILURES")
print(f"{'=' * 90}")

cibc_no_mat = (
    db.query(Product)
    .filter(
        Product.parent_issuer == "CIBC",
        Product.is_preliminary == False,
        Product.maturity_date.is_(None),
    )
    .limit(5)
    .all()
)
print(f"  {len(cibc_no_mat)} CIBC products with no maturity date")
for p in cibc_no_mat[:3]:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)
    print(f"\n  {p.cusip} | {filing.filing_date}")
    # Find "due" patterns
    for m in re.finditer(r"due\s+\w+\s+\d{1,2},?\s+\d{4}", clean[:15000], re.IGNORECASE):
        print(f"    DUE: {m.group()}")
    # Find maturity date patterns
    for m in re.finditer(r"[Mm]aturity\s+[Dd]ate.{0,100}", clean[:15000]):
        print(f"    MAT: {m.group()[:120]}")
    if not list(re.finditer(r"due\s+\w+\s+\d{1,2}", clean[:15000], re.IGNORECASE)):
        print(f"    NO 'due' PATTERN FOUND. Cover: {clean[:300]}")


# ============================================================
# 5. BofA CUSIP — Why 95%?
# ============================================================
print(f"\n{'=' * 90}")
print(f"  5. BofA CUSIP FAILURES")
print(f"{'=' * 90}")

bofa_no_cusip = (
    db.query(Product)
    .filter(Product.parent_issuer == "Bank of America", Product.cusip.is_(None), Product.is_preliminary == False)
    .limit(3)
    .all()
)
for p in bofa_no_cusip:
    filing = db.get(Filing, p.filing_id)
    html = client.fetch_text(filing.primary_doc_url)
    clean = clean_html(html)
    print(f"\n  {filing.filing_date} | type={p.product_type}")
    idx = clean.upper().find("CUSIP")
    if idx >= 0:
        print(f"    CUSIP area: {clean[idx:idx+300]}")
    else:
        print(f"    NO 'CUSIP' keyword found in entire document")
        # Try finding CUSIP-like patterns
        for m in re.finditer(r"\b(09[A-Z0-9]{7})\b", clean[:80000]):
            print(f"    BofA CUSIP candidate (09...): {m.group(1)} at pos {m.start()}")
            break

db.close()
print(f"\n{'=' * 90}")
print("  AUDIT COMPLETE")
print(f"{'=' * 90}")
