"""Link preliminary pricing supplements to their corresponding final products.

Uses raw sqlite3 to avoid interfering with any running SQLAlchemy processes.
Idempotent: clears existing links before re-linking.

Matching strategies (priority order):
  1. CUSIP match: same CUSIP + same parent_issuer + prelim filed within 14 days before final
  2. Underlier + terms match: same parent_issuer + underlier_names + product_type + maturity_date
     (for prelims without CUSIP)
"""

import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from config import DB_PATH
BATCH_SIZE = 500
DATE_WINDOW_DAYS = 14


def ensure_column(conn: sqlite3.Connection) -> None:
    """Add linked_final_id column if it doesn't exist."""
    cursor = conn.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cursor.fetchall()}
    if "linked_final_id" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN linked_final_id INTEGER")
        conn.commit()
        print("Added linked_final_id column to products table.")
    else:
        print("Column linked_final_id already exists.")


def clear_links(conn: sqlite3.Connection) -> int:
    """Clear all existing links. Returns count of cleared links."""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM products WHERE linked_final_id IS NOT NULL"
    )
    existing = cursor.fetchone()[0]
    if existing > 0:
        conn.execute("UPDATE products SET linked_final_id = NULL")
        conn.commit()
        print(f"Cleared {existing:,} existing links.")
    return existing


def get_issuers(conn: sqlite3.Connection) -> list[str]:
    """Get distinct parent_issuer values."""
    cursor = conn.execute(
        "SELECT DISTINCT parent_issuer FROM products "
        "WHERE parent_issuer IS NOT NULL ORDER BY parent_issuer"
    )
    return [row[0] for row in cursor.fetchall()]


def _batch_update(conn: sqlite3.Connection, updates: list[tuple[int, int]]) -> None:
    """Write (final_id, prelim_id) pairs in batches."""
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]
        conn.executemany(
            "UPDATE products SET linked_final_id = ? WHERE id = ?", batch
        )
        conn.commit()


def link_by_cusip(conn: sqlite3.Connection, issuer: str) -> int:
    """Strategy 1: Match prelims to finals by CUSIP within 14-day window.

    Loads prelims and finals for this issuer into memory, builds a CUSIP
    lookup, and finds the closest final by filing date.

    Returns count of prelims linked.
    """
    # Load prelim products: (id, cusip, filing_date)
    prelims = conn.execute(
        """
        SELECT p.id, p.cusip, f.filing_date
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.is_preliminary = 1
          AND p.parent_issuer = ?
          AND p.cusip IS NOT NULL
          AND p.cusip != ''
        """,
        (issuer,),
    ).fetchall()

    if not prelims:
        return 0

    # Load final products: (id, cusip, filing_date)
    finals = conn.execute(
        """
        SELECT p.id, p.cusip, f.filing_date
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.is_preliminary = 0
          AND p.parent_issuer = ?
          AND p.cusip IS NOT NULL
          AND p.cusip != ''
        """,
        (issuer,),
    ).fetchall()

    if not finals:
        return 0

    # Build CUSIP -> list of (final_id, filing_date) lookup
    cusip_to_finals: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for final_id, cusip, filing_date in finals:
        cusip_to_finals[cusip].append((final_id, filing_date))

    updates = []
    for prelim_id, cusip, prelim_date in prelims:
        candidates = cusip_to_finals.get(cusip)
        if not candidates:
            continue

        best_final_id = None
        best_gap = DATE_WINDOW_DAYS + 1  # sentinel

        for final_id, final_date in candidates:
            # filing_date is stored as string 'YYYY-MM-DD', compare directly
            if final_date < prelim_date:
                continue
            # Calculate day gap using date arithmetic
            gap = _date_gap(prelim_date, final_date)
            if 0 <= gap <= DATE_WINDOW_DAYS and gap < best_gap:
                best_gap = gap
                best_final_id = final_id

        if best_final_id is not None:
            updates.append((best_final_id, prelim_id))

    _batch_update(conn, updates)
    return len(updates)


def link_by_terms(
    conn: sqlite3.Connection, issuer: str, linked_prelim_ids: set[int]
) -> int:
    """Strategy 2: Match unlinked prelims by underlier_names + product_type + maturity_date.

    Only processes prelims not already linked by Strategy 1.
    Returns count of prelims linked.
    """
    # Load unlinked prelim products
    prelims = conn.execute(
        """
        SELECT p.id, p.underlier_names, p.product_type, p.maturity_date, f.filing_date
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.is_preliminary = 1
          AND p.parent_issuer = ?
          AND p.linked_final_id IS NULL
          AND p.underlier_names IS NOT NULL
          AND p.underlier_names != ''
          AND p.product_type IS NOT NULL
        """,
        (issuer,),
    ).fetchall()

    # Filter out already-linked ones (in case clear didn't run between strategies)
    prelims = [p for p in prelims if p[0] not in linked_prelim_ids]

    if not prelims:
        return 0

    # Load final products with underlier_names
    finals = conn.execute(
        """
        SELECT p.id, p.underlier_names, p.product_type, p.maturity_date, f.filing_date
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.is_preliminary = 0
          AND p.parent_issuer = ?
          AND p.underlier_names IS NOT NULL
          AND p.underlier_names != ''
          AND p.product_type IS NOT NULL
        """,
        (issuer,),
    ).fetchall()

    if not finals:
        return 0

    # Build composite key -> list of (final_id, filing_date)
    # Key: (underlier_names, product_type, maturity_date)
    key_to_finals: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    for final_id, underlier_names, product_type, maturity_date, filing_date in finals:
        key = (underlier_names, product_type, maturity_date)
        key_to_finals[key].append((final_id, filing_date))

    updates = []
    for prelim_id, underlier_names, product_type, maturity_date, prelim_date in prelims:
        key = (underlier_names, product_type, maturity_date)
        candidates = key_to_finals.get(key)
        if not candidates:
            continue

        best_final_id = None
        best_gap = DATE_WINDOW_DAYS + 1

        for final_id, final_date in candidates:
            if final_date < prelim_date:
                continue
            gap = _date_gap(prelim_date, final_date)
            if 0 <= gap <= DATE_WINDOW_DAYS and gap < best_gap:
                best_gap = gap
                best_final_id = final_id

        if best_final_id is not None:
            updates.append((best_final_id, prelim_id))

    _batch_update(conn, updates)
    return len(updates)


def _date_gap(date_a: str, date_b: str) -> int:
    """Calculate integer day gap between two 'YYYY-MM-DD' date strings."""
    from datetime import date

    ya, ma, da = date_a.split("-")
    yb, mb, db = date_b.split("-")
    d_a = date(int(ya), int(ma), int(da))
    d_b = date(int(yb), int(mb), int(db))
    return (d_b - d_a).days


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    # Long busy timeout to coexist with any running extraction process
    conn.execute("PRAGMA busy_timeout=30000")

    start = time.time()

    print(f"Database: {DB_PATH}")
    print(f"Date window: {DATE_WINDOW_DAYS} days")
    print(f"Batch size: {BATCH_SIZE}")
    print()

    ensure_column(conn)
    clear_links(conn)
    print()

    # Get total prelim count
    total_prelims = conn.execute(
        "SELECT COUNT(*) FROM products WHERE is_preliminary = 1"
    ).fetchone()[0]
    print(f"Total preliminary products: {total_prelims:,}")
    print()

    issuers = get_issuers(conn)
    overall_cusip = 0
    overall_terms = 0
    overall_prelims = 0

    print(f"{'Issuer':<20} {'Prelims':>8} {'CUSIP':>8} {'Terms':>8} {'Total':>8} {'Rate':>7}")
    print("-" * 72)

    for issuer in issuers:
        issuer_prelims = conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_preliminary = 1 AND parent_issuer = ?",
            (issuer,),
        ).fetchone()[0]

        cusip_linked = link_by_cusip(conn, issuer)

        # Collect IDs of prelims already linked by CUSIP for Strategy 2 filtering
        linked_ids_rows = conn.execute(
            "SELECT id FROM products WHERE is_preliminary = 1 AND parent_issuer = ? AND linked_final_id IS NOT NULL",
            (issuer,),
        ).fetchall()
        linked_prelim_ids = {row[0] for row in linked_ids_rows}

        terms_linked = link_by_terms(conn, issuer, linked_prelim_ids)

        total_linked = cusip_linked + terms_linked
        rate = (total_linked / issuer_prelims * 100) if issuer_prelims > 0 else 0.0

        print(
            f"{issuer:<20} {issuer_prelims:>8,} {cusip_linked:>8,} "
            f"{terms_linked:>8,} {total_linked:>8,} {rate:>6.1f}%"
        )

        overall_cusip += cusip_linked
        overall_terms += terms_linked
        overall_prelims += issuer_prelims

    overall_total = overall_cusip + overall_terms
    overall_rate = (
        (overall_total / overall_prelims * 100) if overall_prelims > 0 else 0.0
    )

    print("-" * 72)
    print(
        f"{'TOTAL':<20} {overall_prelims:>8,} {overall_cusip:>8,} "
        f"{overall_terms:>8,} {overall_total:>8,} {overall_rate:>6.1f}%"
    )
    print()
    print(f"Unlinked prelims: {overall_prelims - overall_total:,}")
    print(f"Elapsed: {time.time() - start:.1f}s")

    conn.close()


if __name__ == "__main__":
    main()
