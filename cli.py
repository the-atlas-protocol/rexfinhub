"""CLI for structured notes extraction pipeline.

Usage:
    python cli.py discover [--cik CIK]           # Index 424B2 filings from EDGAR
    python cli.py extract [--cik CIK] [--year Y] [--reextract]  # Extract product data
    python cli.py status                          # Show extraction progress
    python cli.py validate [--n 30]               # Spot-check accuracy
    python cli.py dedup                           # Deduplicate by CUSIP
"""
from __future__ import annotations

import sys, io, argparse, re, random
from datetime import date, datetime

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import ISSUERS, SEC_ARCHIVES_BASE, FORM_TYPE
from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import get_parser


# ---------------------------------------------------------------------------
# Discovery: index 424B2 filings from EDGAR submissions JSON
# ---------------------------------------------------------------------------

def discover_filings(client: SECClient, cik_filter: str | None = None, year: int | None = None):
    """Find all 424B2 filings for registered issuers."""
    db = get_db()

    # Pre-load all known accession numbers for fast dedup (O(1) lookup vs O(N) query)
    existing_accs: set[str] = set(
        row[0] for row in db.query(Filing.accession_number).all()
    )
    print(f"  {len(existing_accs):,} filings already indexed")

    issuers_to_scan = {}
    for cik, (short, full) in ISSUERS.items():
        if cik_filter and str(int(cik)) != str(int(cik_filter)):
            continue
        issuers_to_scan[cik] = (short, full)

    def _process_filing_batch(forms, accessions, dates_list, docs, xbrl_flags, issuer, cik_int):
        """Process a batch of filings from submissions JSON."""
        count = 0
        for i, form in enumerate(forms):
            if form != FORM_TYPE:
                continue
            filing_date = dates_list[i] if i < len(dates_list) else None
            if year and filing_date and not filing_date.startswith(str(year)):
                continue
            acc = accessions[i] if i < len(accessions) else None
            if not acc or acc in existing_accs:
                continue
            doc = docs[i] if i < len(docs) else ""
            acc_path = acc.replace("-", "")
            url = f"{SEC_ARCHIVES_BASE}/{cik_int}/{acc_path}/{doc}"
            is_xbrl = bool(xbrl_flags[i]) if i < len(xbrl_flags) else False
            filing = Filing(
                issuer_id=issuer.id,
                accession_number=acc,
                filing_date=date.fromisoformat(filing_date) if filing_date else None,
                form_type=form,
                primary_doc_url=url,
                is_inline_xbrl=is_xbrl,
            )
            db.add(filing)
            existing_accs.add(acc)
            count += 1
        return count

    for cik, (short, full) in issuers_to_scan.items():
        cik_padded = f"{int(cik):010d}"

        # Upsert issuer
        issuer = db.query(Issuer).filter_by(cik=cik_padded).first()
        if not issuer:
            issuer = Issuer(cik=cik_padded, short_name=short, full_name=full)
            db.add(issuer)
            db.flush()

        # Load submissions
        subs = client.load_submissions(cik)
        recent = subs.get("filings", {}).get("recent", {})

        new_count = _process_filing_batch(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("filingDate", []),
            recent.get("primaryDocument", []),
            recent.get("isInlineXBRL", []),
            issuer, int(cik),
        )

        # Handle older filings (paginated)
        files_list = subs.get("filings", {}).get("files", [])
        for fi, file_ref in enumerate(files_list):
            file_url = f"https://data.sec.gov/submissions/{file_ref['name']}"
            old_data = client.fetch_json(file_url)
            new_count += _process_filing_batch(
                old_data.get("form", []),
                old_data.get("accessionNumber", []),
                old_data.get("filingDate", []),
                old_data.get("primaryDocument", []),
                old_data.get("isInlineXBRL", []),
                issuer, int(cik),
            )
            if (fi + 1) % 10 == 0:
                db.commit()
                print(f"    {short}: page {fi+1}/{len(files_list)} ({new_count:,} new so far)")

        issuer.total_filings = db.query(Filing).filter_by(issuer_id=issuer.id).count()
        issuer.last_updated = datetime.now()
        db.commit()
        print(f"  {short:<20s} {new_count:>6,} new filings ({issuer.total_filings:,} total)")

    db.close()
    print("Discovery complete.")


# ---------------------------------------------------------------------------
# Extraction: parse filing HTML and save product data
# ---------------------------------------------------------------------------

def extract_products(
    client: SECClient,
    cik_filter: str | None = None,
    year: int | None = None,
    reextract: bool = False,
    limit: int = 0,
):
    """Extract product details from indexed filings."""
    db = get_db()

    query = db.query(Issuer).filter_by(status="confirmed")
    if cik_filter:
        cik_padded = f"{int(cik_filter):010d}"
        query = query.filter_by(cik=cik_padded)
    issuers = query.order_by(Issuer.total_filings.desc()).all()

    if not issuers:
        print("No issuers found. Run 'discover' first.")
        db.close()
        return

    # Handle --reextract
    if reextract:
        for iss in issuers:
            fq = db.query(Filing).filter_by(issuer_id=iss.id)
            if year:
                fq = fq.filter(
                    Filing.filing_date >= date(year, 1, 1),
                    Filing.filing_date <= date(year, 12, 31),
                )
            filing_ids = [f.id for f in fq.all()]
            if filing_ids:
                deleted = db.query(Product).filter(
                    Product.filing_id.in_(filing_ids)
                ).delete(synchronize_session="fetch")
                # Reset extracted flag
                for fid in filing_ids:
                    f = db.get(Filing, fid)
                    if f:
                        f.extracted = False
                db.commit()
                print(f"  Deleted {deleted:,} products for {iss.short_name} (re-extract)")

    total_new = 0
    total_skip = 0

    for iss in issuers:
        query = db.query(Filing).filter_by(issuer_id=iss.id, extracted=False)
        if year:
            query = query.filter(
                Filing.filing_date >= date(year, 1, 1),
                Filing.filing_date <= date(year, 12, 31),
            )
        query = query.order_by(Filing.filing_date.desc())
        if limit:
            query = query.limit(limit)
        filings = query.all()

        if not filings:
            continue

        print(f"\n  {iss.short_name} ({len(filings)} filings)...")
        issuer_new = 0

        for i, filing in enumerate(filings):
            try:
                html = client.fetch_text(filing.primary_doc_url)
                if not html or len(html) < 500:
                    filing.extracted = True
                    total_skip += 1
                    continue

                parser = get_parser(iss.cik, html, iss.full_name)
                data = parser.extract_all()

                product = Product(
                    filing_id=filing.id,
                    parent_issuer=iss.short_name,
                    cusip=data.get("cusip"),
                    isin=data.get("isin"),
                    product_name=data.get("product_name"),
                    product_type=data.get("product_type"),
                    product_subtype=data.get("product_subtype"),
                    is_preliminary=data.get("is_preliminary", False),
                    underlier_count=data.get("underlier_count"),
                    underlier_names=data.get("underlier_names"),
                    underlier_tickers=data.get("underlier_tickers"),
                    underlier_type=data.get("underlier_type"),
                    notional_amount=data.get("notional_amount"),
                    denomination=data.get("denomination"),
                    pricing_date=data.get("pricing_date"),
                    settlement_date=data.get("settlement_date"),
                    maturity_date=data.get("maturity_date"),
                    coupon_rate=data.get("coupon_rate"),
                    coupon_type=data.get("coupon_type"),
                    coupon_frequency=data.get("coupon_frequency"),
                    barrier_level=data.get("barrier_level"),
                    barrier_type=data.get("barrier_type"),
                    call_premium=data.get("call_premium"),
                    call_level=data.get("call_level"),
                    first_call_date=data.get("first_call_date"),
                    call_frequency=data.get("call_frequency"),
                    is_memory_coupon=data.get("is_memory_coupon", False),
                    participation_rate=data.get("participation_rate"),
                    upside_cap=data.get("upside_cap"),
                    downside_leverage=data.get("downside_leverage"),
                    confidence=data.get("confidence"),
                    extraction_date=datetime.now(),
                )
                db.add(product)
                filing.extracted = True
                issuer_new += 1
                total_new += 1

                if (i + 1) % 50 == 0:
                    db.commit()
                    print(f"    [{i+1:>5}/{len(filings)}] new={issuer_new} conf={data.get('confidence', 0):.2f}")

            except Exception as e:
                filing.extraction_error = str(e)[:500]
                total_skip += 1
                if "rate limit" in str(e).lower() or "429" in str(e):
                    print(f"    Rate limited at {i+1}, pausing...")
                    import time
                    time.sleep(5)

        db.commit()
        iss.filings_extracted = db.query(Filing).filter_by(
            issuer_id=iss.id, extracted=True
        ).count()
        db.commit()
        print(f"    Done: {issuer_new} extracted")

    db.close()
    print(f"\nTotal: {total_new:,} products extracted, {total_skip:,} skipped")


# ---------------------------------------------------------------------------
# Status: show extraction progress
# ---------------------------------------------------------------------------

def show_status():
    db = get_db()

    total_filings = db.query(Filing).count()
    total_products = db.query(Product).count()
    final_products = db.query(Product).filter_by(is_preliminary=False).count()
    prelim_products = db.query(Product).filter_by(is_preliminary=True).count()

    print(f"{'='*90}")
    print(f"  STRUCTURED PRODUCT EXTRACTION STATUS")
    print(f"{'='*90}")
    print(f"  Total filings indexed:  {total_filings:>10,}")
    print(f"  Products extracted:     {total_products:>10,}")
    print(f"    Final:                {final_products:>10,}  ({final_products/total_products*100:.1f}%)" if total_products else "")
    print(f"    Preliminary:          {prelim_products:>10,}  ({prelim_products/total_products*100:.1f}%)" if total_products else "")

    # Per-issuer breakdown
    if total_products:
        from sqlalchemy import func
        print(f"\n  {'Issuer':<20s} {'Total':>6s} {'Final':>6s} {'Prelim':>6s} {'CUSIP%':>7s} {'Notl%':>7s} {'Mat%':>7s} {'Coupon%':>8s} {'Barrier%':>9s} {'Conf':>6s}")
        print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*9} {'-'*6}")

        issuers = db.query(Issuer).order_by(Issuer.short_name).all()
        for iss in issuers:
            products = db.query(Product).join(Filing).filter(Filing.issuer_id == iss.id).all()
            if not products:
                continue

            final = [p for p in products if not p.is_preliminary]
            prelim = [p for p in products if p.is_preliminary]

            if not final:
                print(f"  {iss.short_name:<20s} {len(products):>6} {len(final):>6} {len(prelim):>6}")
                continue

            cusip_pct = sum(1 for p in final if p.cusip) / len(final) * 100
            notl_pct = sum(1 for p in final if p.notional_amount) / len(final) * 100
            mat_pct = sum(1 for p in final if p.maturity_date) / len(final) * 100
            coup_pct = sum(1 for p in final if p.coupon_rate) / len(final) * 100
            barr_pct = sum(1 for p in final if p.barrier_level) / len(final) * 100
            avg_conf = sum(p.confidence or 0 for p in final) / len(final)

            print(f"  {iss.short_name:<20s} {len(products):>6} {len(final):>6} {len(prelim):>6} "
                  f"{cusip_pct:>6.0f}% {notl_pct:>6.0f}% {mat_pct:>6.0f}% {coup_pct:>7.0f}% {barr_pct:>8.0f}% {avg_conf:>6.2f}")

    # Product type distribution
    if total_products:
        print(f"\n  Product Type Distribution:")
        from sqlalchemy import func
        types = db.query(Product.product_type, func.count()).group_by(Product.product_type).order_by(func.count().desc()).all()
        for pt, cnt in types:
            print(f"    {pt or 'NULL':<25s} {cnt:>6,}  ({cnt/total_products*100:>5.1f}%)")

        # Total notional
        from sqlalchemy import func as sqf
        total_notional = db.query(sqf.sum(Product.notional_amount)).scalar() or 0
        print(f"\n  Total notional extracted: ${total_notional:>20,.0f}")

    print(f"{'='*90}")
    db.close()


# ---------------------------------------------------------------------------
# Validate: spot-check accuracy against raw filing HTML
# ---------------------------------------------------------------------------

def validate_accuracy(client: SECClient, n: int = 30, seed: int = 42):
    db = get_db()

    products = (
        db.query(Product)
        .filter(
            Product.is_preliminary == False,
            Product.cusip.isnot(None),
            Product.notional_amount.isnot(None),
        )
        .all()
    )

    if len(products) < n:
        print(f"Only {len(products)} eligible products. Validating all.")
        n = len(products)

    random.seed(seed)
    sample = random.sample(products, n)

    matches = {"cusip": 0, "notional": 0, "maturity": 0, "barrier": 0}
    checked = {"cusip": 0, "notional": 0, "maturity": 0, "barrier": 0}
    errors = []

    for i, p in enumerate(sample):
        filing = db.get(Filing, p.filing_id)
        text = client.fetch_text(filing.primary_doc_url)
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"&[a-z]+;|&#\d+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()

        print(f"[{i+1}/{n}] {p.parent_issuer} | {filing.filing_date} | {p.cusip}")

        # CUSIP
        idx = clean.upper().find("CUSIP")
        if idx >= 0:
            nearby = clean[idx:idx+200]
            cm = re.search(r"([A-Z0-9]{9})", nearby)
            if cm:
                checked["cusip"] += 1
                if cm.group(1) == p.cusip:
                    matches["cusip"] += 1
                else:
                    errors.append(f"  CUSIP: {p.parent_issuer} stored={p.cusip} found={cm.group(1)}")

        # Notional
        dollar_amounts = []
        for dm in re.finditer(r"\$([\d,]+(?:\.\d+)?)", clean[:50000]):
            val = float(dm.group(1).replace(",", ""))
            if val > 1000 and val != 1000:
                dollar_amounts.append(val)
        if dollar_amounts and p.notional_amount:
            checked["notional"] += 1
            if any(abs(d - p.notional_amount) / p.notional_amount < 0.01 for d in dollar_amounts):
                matches["notional"] += 1
            else:
                nearest = min(dollar_amounts, key=lambda d: abs(d - p.notional_amount))
                errors.append(f"  Notional: {p.parent_issuer} stored=${p.notional_amount:,.0f} nearest=${nearest:,.0f}")

        # Maturity
        mat_m = re.search(r"due\s+(\w+\s+\d{1,2},?\s+\d{4})", clean[:15000], re.IGNORECASE)
        if mat_m and p.maturity_date:
            checked["maturity"] += 1
            mat_text = mat_m.group(1).replace(",", "")
            if str(p.maturity_date.year) in mat_text:
                matches["maturity"] += 1
            else:
                errors.append(f"  Maturity: {p.parent_issuer} stored={p.maturity_date} found={mat_text}")

        # Barrier
        if p.barrier_level:
            checked["barrier"] += 1
            stored_pct = p.barrier_level * 100
            barrier_pcts = set()
            for bm in re.finditer(r"(?:barrier|threshold|trigger|knock|downside).{0,100}?([\d.]+)\s*%", clean[:80000], re.IGNORECASE):
                barrier_pcts.add(float(bm.group(1)))
            for bm in re.finditer(r"([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:initial|starting|hypothetical)", clean[:80000], re.IGNORECASE):
                val = float(bm.group(1))
                if 10 < val < 100:
                    barrier_pcts.add(val)

            if barrier_pcts:
                closest = min(barrier_pcts, key=lambda x: abs(x - stored_pct))
                if abs(closest - stored_pct) < 1.0:
                    matches["barrier"] += 1
                else:
                    errors.append(f"  Barrier: {p.parent_issuer} stored={stored_pct:.0f}% closest={closest:.0f}%")

    print(f"\n{'='*60}")
    print(f"  ACCURACY REPORT ({n} products)")
    print(f"{'='*60}")
    for field in ["cusip", "notional", "maturity", "barrier"]:
        rate = matches[field] / checked[field] * 100 if checked[field] else 0
        print(f"  {field:<12s}: {matches[field]:>3}/{checked[field]:>3} ({rate:.0f}%)")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors[:20]:
            print(e)
    else:
        print(f"\n  All fields verified correct!")

    db.close()


# ---------------------------------------------------------------------------
# Dedup: remove duplicate products by CUSIP (keep highest confidence)
# ---------------------------------------------------------------------------

def dedup_products():
    db = get_db()
    from sqlalchemy import func

    dupes = (
        db.query(Product.cusip, func.count())
        .filter(Product.cusip.isnot(None))
        .group_by(Product.cusip)
        .having(func.count() > 1)
        .all()
    )

    total_removed = 0
    for cusip, count in dupes:
        products = (
            db.query(Product)
            .filter_by(cusip=cusip)
            .order_by(Product.is_preliminary.asc(), Product.extraction_date.desc().nullslast(), Product.confidence.desc().nullslast())
            .all()
        )
        # Keep the first (best), delete the rest
        for p in products[1:]:
            db.delete(p)
            total_removed += 1

    db.commit()
    print(f"Removed {total_removed:,} duplicate products across {len(dupes):,} CUSIPs.")
    db.close()


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Structured Notes Extraction Pipeline")
    sub = parser.add_subparsers(dest="command")

    # discover
    disc = sub.add_parser("discover", help="Index 424B2 filings from EDGAR")
    disc.add_argument("--cik", help="Single issuer CIK")
    disc.add_argument("--year", type=int, help="Filter by year")

    # extract
    ext = sub.add_parser("extract", help="Extract product data from filings")
    ext.add_argument("--cik", help="Single issuer CIK")
    ext.add_argument("--year", type=int, help="Filter by year")
    ext.add_argument("--reextract", action="store_true", help="Delete and re-extract")
    ext.add_argument("--limit", type=int, default=0, help="Max filings per issuer")

    # status
    sub.add_parser("status", help="Show extraction progress")

    # validate
    val = sub.add_parser("validate", help="Spot-check accuracy")
    val.add_argument("--n", type=int, default=30, help="Number of products to check")
    val.add_argument("--seed", type=int, default=42, help="Random seed")

    # dedup
    sub.add_parser("dedup", help="Deduplicate products by CUSIP")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    init_db()
    client = SECClient(pause=0.25)

    if args.command == "discover":
        discover_filings(client, cik_filter=args.cik, year=args.year)
    elif args.command == "extract":
        extract_products(client, cik_filter=args.cik, year=args.year,
                        reextract=args.reextract, limit=args.limit)
    elif args.command == "status":
        show_status()
    elif args.command == "validate":
        validate_accuracy(client, n=args.n, seed=args.seed)
    elif args.command == "dedup":
        dedup_products()


if __name__ == "__main__":
    main()
