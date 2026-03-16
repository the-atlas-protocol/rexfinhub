"""Monitor extraction progress and quality.

Usage:
    python monitor.py              # Show progress snapshot
    python monitor.py --quality    # Run quality report on extracted data
    python monitor.py --gaps       # Show fields with low fill rates and sample failures
"""
from __future__ import annotations

import sys, io, json, re, argparse
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import PROGRESS_FILE as _PF
PROGRESS_FILE = Path(_PF)


def show_progress():
    """Show current extraction progress from JSON file."""
    if not PROGRESS_FILE.exists():
        print("No extraction running. Start with: python run_extraction.py")
        return

    p = json.loads(PROGRESS_FILE.read_text())
    status = p.get("status", "unknown")
    processed = p.get("processed", 0)
    total = p.get("total_filings", 0)
    products = p.get("products_created", 0)
    errors = p.get("errors", 0)
    rate = p.get("rate_per_sec", 0)
    eta = p.get("eta_minutes", 0)
    current = p.get("current_issuer", "")
    elapsed = p.get("elapsed_minutes", 0)
    pct = processed / total * 100 if total else 0

    print(f"{'='*70}")
    print(f"  EXTRACTION STATUS: {status.upper()}")
    print(f"{'='*70}")

    # Progress bar
    bar_width = 40
    filled = int(bar_width * pct / 100)
    bar = "#" * filled + "-" * (bar_width - filled)
    print(f"  [{bar}] {pct:.1f}%")
    print(f"  Processed: {processed:>8,} / {total:,}")
    print(f"  Products:  {products:>8,}")
    print(f"  Errors:    {errors:>8}")

    if status == "running":
        print(f"  Rate:      {rate:>8.1f} filings/sec")
        print(f"  ETA:       {eta:>8.0f} minutes")
        print(f"  Current:   {current}")
    elif status == "complete":
        print(f"  Total time: {elapsed:.1f} minutes")

    # Per-issuer breakdown
    issuer_prog = p.get("issuer_progress", {})
    if issuer_prog:
        print(f"\n  {'Issuer':<20s} {'Done':>7s} {'Total':>7s} {'Prods':>7s} {'Errs':>5s} {'%':>6s}")
        print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*7} {'-'*5} {'-'*6}")
        for name, data in issuer_prog.items():
            done = data.get("done", 0)
            tot = data.get("total", 0)
            prods = data.get("products", 0)
            errs = data.get("errors", 0)
            ip = done / tot * 100 if tot else 0
            marker = " <--" if name == current and status == "running" else ""
            print(f"  {name:<20s} {done:>7,} {tot:>7,} {prods:>7,} {errs:>5} {ip:>5.0f}%{marker}")

    print(f"{'='*70}")


def show_quality():
    """Show extraction quality metrics per issuer."""
    from database import init_db, get_db
    from models import Issuer, Filing, Product
    from sqlalchemy import func

    init_db()
    db = get_db()

    total = db.query(Product).count()
    final = db.query(Product).filter_by(is_preliminary=False).count()
    prelim = db.query(Product).filter_by(is_preliminary=True).count()

    print(f"{'='*90}")
    print(f"  EXTRACTION QUALITY REPORT")
    print(f"  {total:,} products ({final:,} final, {prelim:,} preliminary)")
    print(f"{'='*90}")

    print(f"\n  {'Issuer':<20s} {'Total':>6s} {'Final':>6s} {'CUSIP':>7s} {'Notl':>7s} "
          f"{'Mat':>7s} {'Coupon':>8s} {'Barrier':>8s} {'Conf':>6s}")
    print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*6}")

    issuers = db.query(Issuer).order_by(Issuer.short_name).all()
    for iss in issuers:
        products = db.query(Product).join(Filing).filter(Filing.issuer_id == iss.id).all()
        if not products:
            continue
        finals = [p for p in products if not p.is_preliminary]
        if not finals:
            print(f"  {iss.short_name:<20s} {len(products):>6} {0:>6}")
            continue

        n = len(finals)
        cusip_pct = sum(1 for p in finals if p.cusip) / n * 100
        notl_pct = sum(1 for p in finals if p.notional_amount) / n * 100
        mat_pct = sum(1 for p in finals if p.maturity_date) / n * 100
        coup_pct = sum(1 for p in finals if p.coupon_rate) / n * 100
        barr_pct = sum(1 for p in finals if p.barrier_level) / n * 100
        avg_conf = sum(p.confidence or 0 for p in finals) / n

        # Flag low rates
        def flag(pct):
            if pct < 50: return f"{pct:>5.0f}%!"
            if pct < 80: return f"{pct:>5.0f}%?"
            return f"{pct:>5.0f}% "

        print(f"  {iss.short_name:<20s} {len(products):>6} {n:>6} "
              f"{flag(cusip_pct)} {flag(notl_pct)} {flag(mat_pct)} "
              f" {flag(coup_pct)}  {flag(barr_pct)} {avg_conf:>5.2f}")

    # Product type distribution
    print(f"\n  Product Types:")
    types = (db.query(Product.product_type, func.count())
             .group_by(Product.product_type)
             .order_by(func.count().desc()).all())
    for pt, cnt in types:
        pct = cnt / total * 100 if total else 0
        bar = "#" * int(pct / 2)
        print(f"    {pt or 'NULL':<25s} {cnt:>7,} ({pct:>5.1f}%) {bar}")

    # Total notional
    total_notl = db.query(func.sum(Product.notional_amount)).scalar() or 0
    print(f"\n  Total notional: ${total_notl:,.0f}")

    db.close()


def show_gaps():
    """Show specific gaps and sample failing products for parser improvement."""
    from database import init_db, get_db
    from models import Issuer, Filing, Product
    from sec_client import SECClient
    from parsers import clean_html
    from sqlalchemy import func

    init_db()
    db = get_db()
    client = SECClient(pause=0.25)

    print(f"{'='*90}")
    print(f"  GAP ANALYSIS - Fields below 80% for non-callable final products")
    print(f"{'='*90}")

    issuers = db.query(Issuer).order_by(Issuer.short_name).all()

    for iss in issuers:
        finals = (db.query(Product).join(Filing)
                  .filter(Filing.issuer_id == iss.id, Product.is_preliminary == False)
                  .all())
        if not finals:
            continue

        n = len(finals)
        # Only look at products that SHOULD have these fields (not callable/growth for coupon)
        coupon_eligible = [p for p in finals if p.product_type in
                          ("autocallable", "digital", "reverse_convertible", "range_accrual", "buffered")]
        barrier_eligible = [p for p in finals if p.product_type in
                           ("autocallable", "digital", "reverse_convertible", "buffered")]

        gaps = []
        coup_n = len(coupon_eligible)
        if coup_n > 0:
            coup_fill = sum(1 for p in coupon_eligible if p.coupon_rate) / coup_n * 100
            if coup_fill < 80:
                gaps.append(("coupon", coup_fill, [p for p in coupon_eligible if not p.coupon_rate]))

        barr_n = len(barrier_eligible)
        if barr_n > 0:
            barr_fill = sum(1 for p in barrier_eligible if p.barrier_level) / barr_n * 100
            if barr_fill < 80:
                gaps.append(("barrier", barr_fill, [p for p in barrier_eligible if not p.barrier_level]))

        cusip_fill = sum(1 for p in finals if p.cusip) / n * 100
        if cusip_fill < 95:
            gaps.append(("cusip", cusip_fill, [p for p in finals if not p.cusip]))

        mat_fill = sum(1 for p in finals if p.maturity_date) / n * 100
        if mat_fill < 90:
            gaps.append(("maturity", mat_fill, [p for p in finals if not p.maturity_date]))

        if not gaps:
            continue

        print(f"\n  --- {iss.short_name} ({n} final products) ---")
        for field, fill, failures in gaps:
            print(f"  {field}: {fill:.0f}% ({len(failures)} failures)")

            # Show 2 sample failures with text context
            for p in failures[:2]:
                filing = db.get(Filing, p.filing_id)
                html = client.fetch_text(filing.primary_doc_url)
                clean = clean_html(html)
                print(f"    {p.cusip or 'NO_CUSIP'} | type={p.product_type} | {filing.filing_date}")

                if field == "coupon":
                    # Search for percentage patterns that might be coupons
                    hits = []
                    for pat in [
                        r"(?:contingent|coupon|interest|payment).{0,80}[\d.]+\s*%",
                        r"[\d.]+\s*%\s*per\s+(?:annum|quarter|month|year)",
                        r"\$[\d.]+\s*per\s+(?:\$[\d,]+|Note|unit)",
                    ]:
                        for m in re.finditer(pat, clean[:40000], re.IGNORECASE):
                            hits.append(f"pos {m.start()}: {m.group()[:100]}")
                    if hits:
                        for h in hits[:2]:
                            print(f"      {h}")
                    else:
                        print(f"      No coupon patterns found in first 40K chars")

                elif field == "barrier":
                    hits = []
                    for pat in [
                        r"(?:barrier|trigger|knock|threshold|buffer|downside).{0,100}",
                        r"[\d.]+\s*%\s*of\s+(?:the\s+)?(?:initial|starting|hypothetical)",
                    ]:
                        for m in re.finditer(pat, clean[:60000], re.IGNORECASE):
                            hits.append(f"pos {m.start()}: {m.group()[:100]}")
                    if hits:
                        for h in hits[:2]:
                            print(f"      {h}")
                    else:
                        print(f"      No barrier patterns found")

    db.close()
    print(f"\n{'='*90}")
    print(f"  Use these patterns to improve parsers.py, then re-extract with:")
    print(f"  python run_extraction.py --reextract --issuer <name>")
    print(f"{'='*90}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor structured notes extraction")
    parser.add_argument("--quality", action="store_true", help="Show quality metrics")
    parser.add_argument("--gaps", action="store_true", help="Show gaps with sample text")
    args = parser.parse_args()

    if args.quality:
        show_quality()
    elif args.gaps:
        show_gaps()
    else:
        show_progress()
