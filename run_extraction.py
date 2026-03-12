"""Run full extraction with progress tracking.

Writes progress to data/extraction_progress.json so monitor.py can check status.
Commits every N filings so partial progress is never lost.

Usage:
    python run_extraction.py                  # Extract all unextracted filings
    python run_extraction.py --issuer JPMorgan # Single issuer
    python run_extraction.py --reextract       # Delete all products and re-extract
"""
from __future__ import annotations

import sys, io, json, time, argparse
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import ISSUERS
from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import get_parser

PROGRESS_FILE = Path("data/extraction_progress.json")
COMMIT_EVERY = 25  # commit to DB every N filings


def save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, default=str))


def run_extraction(issuer_filter: str | None = None, reextract: bool = False):
    init_db()
    db = get_db()
    client = SECClient(pause=0.25)

    issuers = db.query(Issuer).filter_by(status="confirmed").order_by(Issuer.total_filings.desc()).all()

    if issuer_filter:
        issuers = [i for i in issuers if i.short_name.lower() == issuer_filter.lower()]
        if not issuers:
            print(f"No issuer found matching '{issuer_filter}'")
            return

    # Handle --reextract
    if reextract:
        for iss in issuers:
            filing_ids = [f.id for f in db.query(Filing).filter_by(issuer_id=iss.id).all()]
            if filing_ids:
                deleted = db.query(Product).filter(Product.filing_id.in_(filing_ids)).delete(synchronize_session="fetch")
                for fid in filing_ids:
                    f = db.get(Filing, fid)
                    if f:
                        f.extracted = False
                db.commit()
                print(f"  Reset {iss.short_name}: deleted {deleted:,} products")

    # Count total work
    total_remaining = 0
    issuer_work = []
    for iss in issuers:
        count = db.query(Filing).filter_by(issuer_id=iss.id, extracted=False).count()
        if count > 0:
            issuer_work.append((iss, count))
            total_remaining += count

    if total_remaining == 0:
        print("Nothing to extract. All filings already processed.")
        return

    print(f"\nExtraction plan: {total_remaining:,} filings across {len(issuer_work)} issuers")
    for iss, count in issuer_work:
        print(f"  {iss.short_name:<20s} {count:>6,} filings")

    # Global progress tracking
    progress = {
        "status": "running",
        "started": datetime.now().isoformat(),
        "total_filings": total_remaining,
        "processed": 0,
        "products_created": 0,
        "errors": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "current_issuer": None,
        "issuer_progress": {},
        "last_update": datetime.now().isoformat(),
    }
    save_progress(progress)

    global_processed = 0
    global_products = 0
    global_errors = 0
    start_time = time.time()

    for iss, total_count in issuer_work:
        filings = (
            db.query(Filing)
            .filter_by(issuer_id=iss.id, extracted=False)
            .order_by(Filing.filing_date.desc())
            .all()
        )

        progress["current_issuer"] = iss.short_name
        progress["issuer_progress"][iss.short_name] = {
            "total": len(filings), "done": 0, "products": 0, "errors": 0
        }
        save_progress(progress)

        print(f"\n{'='*60}")
        print(f"  {iss.short_name} ({len(filings):,} filings)")
        print(f"{'='*60}")

        issuer_products = 0
        issuer_errors = 0

        for i, filing in enumerate(filings):
            try:
                html = client.fetch_text(filing.primary_doc_url)

                # Track cache hits (if fetch was instant, it was cached)
                # We can't know for sure, but the cache check is in sec_client

                if not html or len(html) < 500:
                    filing.extracted = True
                    global_processed += 1
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
                issuer_products += 1
                global_products += 1

            except Exception as e:
                filing.extraction_error = str(e)[:500]
                issuer_errors += 1
                global_errors += 1
                if "429" in str(e) or "rate" in str(e).lower():
                    print(f"    Rate limited at {i+1}, waiting 10s...")
                    time.sleep(10)

            global_processed += 1

            # Commit periodically
            if (i + 1) % COMMIT_EVERY == 0:
                db.commit()
                elapsed = time.time() - start_time
                rate = global_processed / elapsed if elapsed > 0 else 0
                eta_sec = (total_remaining - global_processed) / rate if rate > 0 else 0
                eta_min = eta_sec / 60

                progress["processed"] = global_processed
                progress["products_created"] = global_products
                progress["errors"] = global_errors
                progress["last_update"] = datetime.now().isoformat()
                progress["rate_per_sec"] = round(rate, 1)
                progress["eta_minutes"] = round(eta_min, 1)
                progress["issuer_progress"][iss.short_name]["done"] = i + 1
                progress["issuer_progress"][iss.short_name]["products"] = issuer_products
                progress["issuer_progress"][iss.short_name]["errors"] = issuer_errors
                save_progress(progress)

                print(f"    [{i+1:>6}/{len(filings)}] products={issuer_products} "
                      f"rate={rate:.1f}/s ETA={eta_min:.0f}m")

        # Final commit for this issuer
        db.commit()
        progress["issuer_progress"][iss.short_name]["done"] = len(filings)
        progress["issuer_progress"][iss.short_name]["products"] = issuer_products
        progress["issuer_progress"][iss.short_name]["errors"] = issuer_errors
        save_progress(progress)

        print(f"    Done: {issuer_products:,} products, {issuer_errors} errors")

    # Final summary
    elapsed = time.time() - start_time
    progress["status"] = "complete"
    progress["processed"] = global_processed
    progress["products_created"] = global_products
    progress["errors"] = global_errors
    progress["elapsed_minutes"] = round(elapsed / 60, 1)
    progress["last_update"] = datetime.now().isoformat()
    save_progress(progress)

    db.close()

    print(f"\n{'='*60}")
    print(f"  EXTRACTION COMPLETE")
    print(f"  Filings processed: {global_processed:,}")
    print(f"  Products created:  {global_products:,}")
    print(f"  Errors:            {global_errors}")
    print(f"  Time:              {elapsed/60:.1f} minutes")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--issuer", help="Single issuer short name")
    parser.add_argument("--reextract", action="store_true", help="Delete and re-extract all")
    args = parser.parse_args()
    run_extraction(issuer_filter=args.issuer, reextract=args.reextract)
