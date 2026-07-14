"""Structured products market analytics.

Queries the extraction database to produce market intelligence:
- Issuance volume and trends
- Issuer market share
- Product type mix
- Underlier popularity
- Coupon/barrier distributions

Usage:
    python analytics.py                  # Full dashboard
    python analytics.py --market-size    # Annual issuance volume
    python analytics.py --issuer-share   # Issuer market share
    python analytics.py --product-mix    # Product type breakdown
    python analytics.py --underliers     # Most popular underliers
    python analytics.py --terms          # Coupon/barrier statistics
    python analytics.py --year 2024      # Filter to specific year
    python analytics.py --csv            # Output as CSV
"""
from __future__ import annotations

import sys, io, argparse, csv
from collections import Counter, defaultdict
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from database import init_db, get_db
from models import Issuer, Filing, Product
from sqlalchemy import func, extract


def _get_products(db, year: int | None = None, finals_only: bool = True):
    """Get products with optional year filter."""
    q = db.query(Product).join(Filing)
    if finals_only:
        q = q.filter(Product.is_preliminary == False)
    if year:
        q = q.filter(extract("year", Filing.filing_date) == year)
    return q.all()


def _get_products_with_issuer(db, year: int | None = None, finals_only: bool = True):
    """Get (product, issuer_name) tuples."""
    q = (db.query(Product, Issuer.short_name)
         .join(Filing, Product.filing_id == Filing.id)
         .join(Issuer, Filing.issuer_id == Issuer.id))
    if finals_only:
        q = q.filter(Product.is_preliminary == False)
    if year:
        q = q.filter(extract("year", Filing.filing_date) == year)
    return q.all()


def market_size(db, year: int | None = None, as_csv: bool = False):
    """Annual issuance volume by year."""
    q = (db.query(
            extract("year", Filing.filing_date).label("yr"),
            func.count(Product.id),
            func.sum(Product.notional_amount),
        )
        .join(Filing, Product.filing_id == Filing.id)
        .filter(Product.is_preliminary == False))

    if year:
        q = q.filter(extract("year", Filing.filing_date) == year)

    rows = q.group_by("yr").order_by("yr").all()

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["year", "products", "notional_usd"])
        for yr, cnt, notl in rows:
            w.writerow([int(yr), cnt, f"{notl or 0:.0f}"])
        return

    print(f"\n{'='*70}")
    print(f"  ANNUAL ISSUANCE VOLUME")
    print(f"{'='*70}")
    print(f"  {'Year':>6s}  {'Products':>10s}  {'Notional ($B)':>14s}  {'Avg Size ($M)':>14s}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*14}  {'-'*14}")

    total_cnt, total_notl = 0, 0
    for yr, cnt, notl in rows:
        notl = notl or 0
        avg = notl / cnt / 1e6 if cnt else 0
        total_cnt += cnt
        total_notl += notl
        bar = "#" * min(int(cnt / 50), 40)
        print(f"  {int(yr):>6d}  {cnt:>10,}  {notl/1e9:>13.2f}  {avg:>13.2f}  {bar}")

    print(f"  {'-'*6}  {'-'*10}  {'-'*14}  {'-'*14}")
    avg_total = total_notl / total_cnt / 1e6 if total_cnt else 0
    print(f"  {'TOTAL':>6s}  {total_cnt:>10,}  {total_notl/1e9:>13.2f}  {avg_total:>13.2f}")


def issuer_share(db, year: int | None = None, as_csv: bool = False):
    """Issuer market share by notional volume."""
    q = (db.query(
            Issuer.short_name,
            func.count(Product.id),
            func.sum(Product.notional_amount),
        )
        .join(Filing, Product.filing_id == Filing.id)
        .join(Issuer, Filing.issuer_id == Issuer.id)
        .filter(Product.is_preliminary == False))

    if year:
        q = q.filter(extract("year", Filing.filing_date) == year)

    rows = q.group_by(Issuer.short_name).order_by(func.sum(Product.notional_amount).desc()).all()

    total_notl = sum(r[2] or 0 for r in rows)
    total_cnt = sum(r[1] for r in rows)

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["issuer", "products", "notional_usd", "share_pct"])
        for name, cnt, notl in rows:
            share = (notl or 0) / total_notl * 100 if total_notl else 0
            w.writerow([name, cnt, f"{notl or 0:.0f}", f"{share:.1f}"])
        return

    yr_label = f" ({year})" if year else ""
    print(f"\n{'='*70}")
    print(f"  ISSUER MARKET SHARE{yr_label}")
    print(f"{'='*70}")
    print(f"  {'Issuer':<20s}  {'Products':>10s}  {'Notional ($B)':>14s}  {'Share':>7s}")
    print(f"  {'-'*20}  {'-'*10}  {'-'*14}  {'-'*7}")

    for name, cnt, notl in rows:
        notl = notl or 0
        share = notl / total_notl * 100 if total_notl else 0
        bar = "#" * int(share / 2)
        print(f"  {name:<20s}  {cnt:>10,}  {notl/1e9:>13.2f}  {share:>5.1f}%  {bar}")

    print(f"  {'-'*20}  {'-'*10}  {'-'*14}  {'-'*7}")
    print(f"  {'TOTAL':<20s}  {total_cnt:>10,}  {total_notl/1e9:>13.2f}  100.0%")


def product_mix(db, year: int | None = None, as_csv: bool = False):
    """Product type distribution."""
    q = (db.query(
            Product.product_type,
            func.count(Product.id),
            func.sum(Product.notional_amount),
        )
        .join(Filing, Product.filing_id == Filing.id)
        .filter(Product.is_preliminary == False))

    if year:
        q = q.filter(extract("year", Filing.filing_date) == year)

    rows = q.group_by(Product.product_type).order_by(func.count(Product.id).desc()).all()

    total_cnt = sum(r[1] for r in rows)
    total_notl = sum(r[2] or 0 for r in rows)

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["product_type", "count", "notional_usd", "pct_count", "pct_notional"])
        for pt, cnt, notl in rows:
            w.writerow([pt, cnt, f"{notl or 0:.0f}",
                        f"{cnt/total_cnt*100:.1f}", f"{(notl or 0)/total_notl*100:.1f}" if total_notl else "0"])
        return

    yr_label = f" ({year})" if year else ""
    print(f"\n{'='*70}")
    print(f"  PRODUCT TYPE MIX{yr_label}")
    print(f"{'='*70}")
    print(f"  {'Type':<25s}  {'Count':>8s}  {'%':>6s}  {'Notional ($B)':>14s}  {'%':>6s}")
    print(f"  {'-'*25}  {'-'*8}  {'-'*6}  {'-'*14}  {'-'*6}")

    for pt, cnt, notl in rows:
        notl = notl or 0
        cnt_pct = cnt / total_cnt * 100 if total_cnt else 0
        notl_pct = notl / total_notl * 100 if total_notl else 0
        bar = "#" * int(cnt_pct / 2)
        print(f"  {pt or 'NULL':<25s}  {cnt:>8,}  {cnt_pct:>5.1f}%  {notl/1e9:>13.2f}  {notl_pct:>5.1f}%  {bar}")

    # Subtype breakdown for autocallables
    autocall_subtypes = (db.query(Product.product_subtype, func.count())
                         .join(Filing)
                         .filter(Product.is_preliminary == False,
                                 Product.product_type == "autocallable"))
    if year:
        autocall_subtypes = autocall_subtypes.filter(extract("year", Filing.filing_date) == year)
    subtypes = autocall_subtypes.group_by(Product.product_subtype).order_by(func.count().desc()).all()

    if subtypes:
        ac_total = sum(c for _, c in subtypes)
        print(f"\n  Autocallable subtypes:")
        for st, cnt in subtypes:
            pct = cnt / ac_total * 100
            print(f"    {st or 'plain':<20s}  {cnt:>6,}  ({pct:>5.1f}%)")


def underlier_ranking(db, year: int | None = None, top_n: int = 30, as_csv: bool = False):
    """Most popular underliers across all products."""
    products = _get_products(db, year)

    counter: Counter = Counter()
    # Noise words that aren't underliers
    noise = {"Inc.", "Inc", "Corp.", "Corp", "Co.", "Ltd.", "PLC", "LLC",
             "Class A", "Class B", "Class C", "N.A.", "Ltd", "Group",
             "Common Stock", "Ordinary Shares", "the", "and", "of"}
    for p in products:
        if not p.underlier_names:
            continue
        # If it's a short ticker list (all caps, <6 chars each), split by comma
        raw = p.underlier_names.strip()
        parts = [x.strip() for x in raw.split(",")]
        for name in parts:
            if not name or len(name) <= 1 or name in noise:
                continue
            counter[name] += 1

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["underlier", "count", "pct"])
        total = sum(counter.values())
        for name, cnt in counter.most_common(top_n):
            w.writerow([name, cnt, f"{cnt/total*100:.1f}"])
        return

    yr_label = f" ({year})" if year else ""
    print(f"\n{'='*70}")
    print(f"  TOP {top_n} UNDERLIERS{yr_label}")
    print(f"{'='*70}")
    total = sum(counter.values())
    print(f"  {'#':>3s}  {'Underlier':<40s}  {'Count':>8s}  {'%':>6s}")
    print(f"  {'-'*3}  {'-'*40}  {'-'*8}  {'-'*6}")

    for i, (name, cnt) in enumerate(counter.most_common(top_n), 1):
        pct = cnt / total * 100 if total else 0
        bar = "#" * int(pct)
        print(f"  {i:>3d}  {name:<40s}  {cnt:>8,}  {pct:>5.1f}%  {bar}")

    with_underlier = sum(1 for p in products if p.underlier_names)
    print(f"\n  Underlier extraction rate: {with_underlier}/{len(products)} "
          f"({with_underlier/len(products)*100:.0f}%)")


def term_statistics(db, year: int | None = None, as_csv: bool = False):
    """Coupon rate, barrier level, and maturity statistics."""
    products = _get_products(db, year)

    coupons = [p.coupon_rate for p in products if p.coupon_rate is not None and p.coupon_rate > 0]
    barriers = [p.barrier_level for p in products if p.barrier_level is not None]
    call_prems = [p.call_premium for p in products if p.call_premium is not None and p.call_premium > 0]
    caps = [p.upside_cap for p in products if p.upside_cap is not None and p.upside_cap > 0]
    participations = [p.participation_rate for p in products if p.participation_rate is not None]

    # Maturity tenor in years
    tenors = []
    for p in products:
        if p.maturity_date and hasattr(p, 'filing') and p.filing:
            pass  # Would need filing date
    # Use pricing_date -> maturity_date if available
    for p in products:
        if p.maturity_date and p.pricing_date:
            tenor = (p.maturity_date - p.pricing_date).days / 365.25
            if 0 < tenor < 30:
                tenors.append(tenor)

    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["metric", "count", "mean", "median", "p25", "p75", "min", "max"])
        for label, vals in [("coupon_rate", coupons), ("barrier_level", barriers),
                            ("call_premium", call_prems), ("upside_cap", caps),
                            ("tenor_years", tenors)]:
            if vals:
                vals_sorted = sorted(vals)
                n = len(vals_sorted)
                w.writerow([label, n,
                            f"{sum(vals)/n:.4f}", f"{vals_sorted[n//2]:.4f}",
                            f"{vals_sorted[n//4]:.4f}", f"{vals_sorted[3*n//4]:.4f}",
                            f"{min(vals):.4f}", f"{max(vals):.4f}"])
        return

    yr_label = f" ({year})" if year else ""
    print(f"\n{'='*70}")
    print(f"  TERM STATISTICS{yr_label}")
    print(f"{'='*70}")

    def _stats(label, vals, fmt=".1f", mult=100):
        if not vals:
            print(f"\n  {label}: No data")
            return
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mean = sum(vals) / n
        median = vals_sorted[n // 2]
        p25 = vals_sorted[n // 4]
        p75 = vals_sorted[3 * n // 4]

        print(f"\n  {label} (n={n:,}):")
        print(f"    Mean:   {mean*mult:{fmt}}{'%' if mult==100 else ''}")
        print(f"    Median: {median*mult:{fmt}}{'%' if mult==100 else ''}")
        print(f"    P25:    {p25*mult:{fmt}}{'%' if mult==100 else ''}")
        print(f"    P75:    {p75*mult:{fmt}}{'%' if mult==100 else ''}")
        print(f"    Range:  {min(vals)*mult:{fmt}} - {max(vals)*mult:{fmt}}{'%' if mult==100 else ''}")

        # Distribution histogram
        if mult == 100:
            buckets = defaultdict(int)
            for v in vals:
                bucket = int(v * mult / 5) * 5  # 5% buckets
                buckets[bucket] += 1
            print(f"    Distribution:")
            for b in sorted(buckets.keys()):
                cnt = buckets[b]
                bar = "#" * int(cnt / max(buckets.values()) * 30)
                print(f"      {b:>5d}-{b+5:>3d}%: {cnt:>5,} {bar}")

    _stats("Coupon Rate (annualized)", coupons)
    _stats("Barrier Level (% of initial)", barriers)
    _stats("Call Premium", call_prems)
    _stats("Upside Cap", caps)

    if tenors:
        _stats("Tenor (years)", tenors, fmt=".2f", mult=1)


def full_dashboard(db, year: int | None = None, as_csv: bool = False):
    """Run all analytics."""
    market_size(db, year, as_csv)
    issuer_share(db, year, as_csv)
    product_mix(db, year, as_csv)
    underlier_ranking(db, year, as_csv=as_csv)
    term_statistics(db, year, as_csv)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Structured products market analytics")
    parser.add_argument("--market-size", action="store_true", help="Annual issuance volume")
    parser.add_argument("--issuer-share", action="store_true", help="Issuer market share")
    parser.add_argument("--product-mix", action="store_true", help="Product type breakdown")
    parser.add_argument("--underliers", action="store_true", help="Most popular underliers")
    parser.add_argument("--terms", action="store_true", help="Coupon/barrier statistics")
    parser.add_argument("--year", type=int, help="Filter to specific year")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    args = parser.parse_args()

    init_db()
    db = get_db()

    try:
        if args.market_size:
            market_size(db, args.year, args.csv)
        elif args.issuer_share:
            issuer_share(db, args.year, args.csv)
        elif args.product_mix:
            product_mix(db, args.year, args.csv)
        elif args.underliers:
            underlier_ranking(db, args.year, as_csv=args.csv)
        elif args.terms:
            term_statistics(db, args.year, args.csv)
        else:
            full_dashboard(db, args.year, args.csv)
    finally:
        db.close()
