"""Run full extraction with progress tracking.

Writes progress to extraction_progress.json so monitor.py can check status.
Commits every N filings so partial progress is never lost.

STRATEGY: Newest filings first across ALL issuers. This gives broad market
coverage of recent years quickly, rather than deep-diving one issuer at a time.

Usage:
    python run_extraction.py                    # Extract all (newest first)
    python run_extraction.py --issuer JPMorgan  # Single issuer
    python run_extraction.py --reextract        # Delete all products and re-extract
    python run_extraction.py --since 2020       # Only filings from 2020+
    python run_extraction.py --since 2018 --group 1/2  # Parallel worker 1 of 2
"""
from __future__ import annotations

import sys, io, json, time, argparse
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import ISSUERS
from database import init_db, get_db
from models import Issuer, Filing, Product
from sec_client import SECClient
from parsers import get_parser

from config import PROGRESS_FILE as _PF
PROGRESS_FILE = Path(_PF)
COMMIT_EVERY = 25  # commit to DB every N filings

# Balanced issuer split for 2-worker parallel mode
_GROUPS = {
    1: {"UBS", "Goldman Sachs", "Morgan Stanley",
        "HSBC", "Bank of America", "CIBC", "Scotiabank",
        "Deutsche Bank", "Jefferies", "Nomura Intl"},
    2: {"JPMorgan", "Barclays", "Citigroup", "Credit Suisse",
        "TD Bank", "RBC", "Wells Fargo", "BMO", "Nomura"},
}


def save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, default=str))


def safe_commit(db, retries=10, base_wait=0.5):
    """Commit with retry for SQLite write contention in parallel mode."""
    for attempt in range(retries):
        try:
            db.commit()
            return
        except Exception as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                db.rollback()
                wait = base_wait * (1.5 ** attempt) + (time.time() % 1) * 0.5
                time.sleep(wait)
                continue
            raise


def run_extraction(issuer_filter: str | None = None, reextract: bool = False,
                   since_year: int | None = None, group: str | None = None):
    init_db()
    db = get_db()

    # Parallel mode config
    n_workers = 1
    group_num = None
    if group:
        parts = group.split("/")
        group_num, n_workers = int(parts[0]), int(parts[1])

    client = SECClient(pause=0.25)

    # Build issuer lookup
    all_issuers = db.query(Issuer).filter_by(status="confirmed").all()
    issuer_map = {iss.id: iss for iss in all_issuers}

    if issuer_filter:
        target_ids = {iss.id for iss in all_issuers
                      if iss.short_name.lower() == issuer_filter.lower()}
        if not target_ids:
            print(f"No issuer found matching '{issuer_filter}'")
            return
    elif group_num:
        group_names = _GROUPS.get(group_num, set())
        target_ids = {iss.id for iss in all_issuers
                      if iss.short_name in group_names}
        names = sorted(issuer_map[i].short_name for i in target_ids)
        print(f"Worker {group_num}/{n_workers} -- issuers: {', '.join(names)}")
    else:
        target_ids = {iss.id for iss in all_issuers}

    # Handle --reextract
    if reextract:
        for iss_id in target_ids:
            iss = issuer_map[iss_id]
            filing_ids = [f.id for f in db.query(Filing).filter_by(issuer_id=iss.id).all()]
            if filing_ids:
                deleted = 0
                BATCH = 500
                for i in range(0, len(filing_ids), BATCH):
                    batch = filing_ids[i:i+BATCH]
                    deleted += db.query(Product).filter(Product.filing_id.in_(batch)).delete(synchronize_session="fetch")
                for fid in filing_ids:
                    f = db.get(Filing, fid)
                    if f:
                        f.extracted = False
                        f.extraction_error = None
                safe_commit(db)
                print(f"  Reset {iss.short_name}: deleted {deleted:,} products")

    # Query ALL unextracted filings globally, newest first
    q = (db.query(Filing)
         .filter(Filing.extracted == False,
                 Filing.issuer_id.in_(target_ids)))

    if since_year:
        q = q.filter(Filing.filing_date >= date(since_year, 1, 1))

    filings = q.order_by(Filing.filing_date.desc()).all()

    if not filings:
        print("Nothing to extract. All filings already processed.")
        return

    # Show per-issuer breakdown
    issuer_counts = defaultdict(int)
    for f in filings:
        issuer_counts[issuer_map[f.issuer_id].short_name] += 1

    year_range = f"{filings[-1].filing_date.year}-{filings[0].filing_date.year}"
    print(f"\nExtraction plan: {len(filings):,} filings, newest first ({year_range})")
    for name in sorted(issuer_counts, key=issuer_counts.get, reverse=True):
        print(f"  {name:<20s} {issuer_counts[name]:>7,}")

    # Progress tracking
    suffix = f"_g{group_num}" if group_num else ""
    progress_file = PROGRESS_FILE.parent / f"extraction_progress{suffix}.json"
    issuer_progress = {name: {"total": cnt, "done": 0, "products": 0, "errors": 0}
                       for name, cnt in issuer_counts.items()}
    progress = {
        "status": "running",
        "started": datetime.now().isoformat(),
        "total_filings": len(filings),
        "processed": 0,
        "products_created": 0,
        "errors": 0,
        "current_issuer": "all (newest first)",
        "issuer_progress": issuer_progress,
        "last_update": datetime.now().isoformat(),
    }
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(progress, indent=2, default=str))

    processed = 0
    products_created = 0
    errors = 0
    start_time = time.time()
    last_year_logged = None

    for i, filing in enumerate(filings):
        iss = issuer_map[filing.issuer_id]

        # Log year transitions
        yr = filing.filing_date.year
        if yr != last_year_logged:
            elapsed = time.time() - start_time
            print(f"\n  --- {yr} --- ({processed:,} done, {elapsed/60:.0f}m elapsed)")
            last_year_logged = yr

        try:
            html = client.fetch_text(filing.primary_doc_url)

            if not html or len(html) < 500:
                filing.extracted = True
                processed += 1
                issuer_progress[iss.short_name]["done"] += 1
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
            products_created += 1
            issuer_progress[iss.short_name]["products"] += 1

        except Exception as e:
            filing.extraction_error = str(e)[:500]
            errors += 1
            issuer_progress[iss.short_name]["errors"] += 1
            if "429" in str(e) or "rate" in str(e).lower():
                print(f"    Rate limited, waiting 10s...")
                time.sleep(10)

        processed += 1
        issuer_progress[iss.short_name]["done"] += 1

        # Commit periodically
        if (i + 1) % COMMIT_EVERY == 0:
            safe_commit(db)
            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = len(filings) - processed
            eta_min = (remaining / rate / 60) if rate > 0 else 0

            progress["processed"] = processed
            progress["products_created"] = products_created
            progress["errors"] = errors
            progress["rate_per_sec"] = round(rate, 1)
            progress["eta_minutes"] = round(eta_min, 1)
            progress["last_update"] = datetime.now().isoformat()
            progress["issuer_progress"] = issuer_progress
            progress_file.write_text(json.dumps(progress, indent=2, default=str))

            # Log every 500 filings
            if (i + 1) % 500 == 0:
                print(f"    [{processed:>7,}/{len(filings):,}] {iss.short_name:<15s} "
                      f"{filing.filing_date}  prods={products_created:,}  "
                      f"rate={rate:.1f}/s  ETA={eta_min:.0f}m  "
                      f"({client.pacer.status_line()})")

            # Log adaptive rate every 2 minutes
            client.pacer.maybe_log(every=120)

    # Final commit
    safe_commit(db)

    elapsed = time.time() - start_time
    progress["status"] = "complete"
    progress["processed"] = processed
    progress["products_created"] = products_created
    progress["errors"] = errors
    progress["elapsed_minutes"] = round(elapsed / 60, 1)
    progress["last_update"] = datetime.now().isoformat()
    progress["issuer_progress"] = issuer_progress
    progress_file.write_text(json.dumps(progress, indent=2, default=str))

    db.close()

    print(f"\n{'='*60}")
    print(f"  EXTRACTION COMPLETE")
    print(f"  Filings processed: {processed:,}")
    print(f"  Products created:  {products_created:,}")
    print(f"  Errors:            {errors}")
    print(f"  Time:              {elapsed/60:.1f} minutes")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--issuer", help="Single issuer short name")
    parser.add_argument("--reextract", action="store_true", help="Delete and re-extract all")
    parser.add_argument("--since", type=int, help="Only extract filings from this year onward")
    parser.add_argument("--group", help="Worker group for parallel runs: '1/2' or '2/2'")
    args = parser.parse_args()
    run_extraction(issuer_filter=args.issuer, reextract=args.reextract,
                   since_year=args.since, group=args.group)
