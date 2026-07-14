"""Asset class classifier for structured notes database.

Standalone post-processing script that classifies products into:
  structured_note, etn, mtn, shelf, other

Uses raw sqlite3 — no ORM imports — safe to run alongside extraction.
Idempotent: re-running overwrites previous classifications.
"""
import sqlite3
import time
from pathlib import Path

from config import DB_PATH
BATCH_SIZE = 1000

# ---------------------------------------------------------------------------
# Classification rules (applied in priority order)
# ---------------------------------------------------------------------------

ETN_KEYWORDS = [
    "etn",
    "exchange-traded note",
    "exchange traded note",
    "ipath",
    "velocityshares",
    "etracs",
    "exchange traded access",
]

SHELF_NAME_KEYWORDS = ["shelf", "program"]

MTN_NAME_KEYWORDS = [
    "medium-term note",
    "medium term note",
    "mtn program",
    "global medium-term",
]

STRUCTURED_PRODUCT_TYPES = {
    "autocallable",
    "buffered",
    "digital",
    "growth",
    "leveraged",
}

OTHER_NAME_KEYWORDS = ["preferred stock", "warrant"]


def classify_product(
    product_name: str | None,
    product_type: str | None,
    notional_amount: float | None,
    cusip: str | None,
    underlier_names: str | None,
) -> str:
    """Return the asset_class for a single product row."""
    name_lower = (product_name or "").lower()
    ptype_lower = (product_type or "").lower()

    # 1. ETN detection (highest priority)
    for kw in ETN_KEYWORDS:
        if kw in name_lower:
            return "etn"

    # 2. Shelf / program detection
    if notional_amount is not None and notional_amount > 500_000_000 and cusip is None:
        return "shelf"
    for kw in SHELF_NAME_KEYWORDS:
        if kw in name_lower:
            return "shelf"

    # 3. MTN detection — vanilla bonds only (no exotic underliers / structured types)
    if any(kw in name_lower for kw in MTN_NAME_KEYWORDS):
        has_underliers = underlier_names is not None and underlier_names.strip() != ""
        is_structured = ptype_lower in STRUCTURED_PRODUCT_TYPES
        if not has_underliers and not is_structured:
            return "mtn"

    # 4. Other (preferred, warrants)
    if ptype_lower == "preferred":
        return "other"
    for kw in OTHER_NAME_KEYWORDS:
        if kw in name_lower:
            return "other"

    # 5. Default
    return "structured_note"


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def ensure_column(conn: sqlite3.Connection) -> None:
    """Add asset_class column if it doesn't exist."""
    cur = conn.execute("PRAGMA table_info(products)")
    columns = {row[1] for row in cur.fetchall()}
    if "asset_class" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN asset_class VARCHAR(20)")
        print("Added column: asset_class")
    # Ensure index exists (CREATE IF NOT EXISTS is safe to repeat)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_asset_class ON products (asset_class)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Batch classification
# ---------------------------------------------------------------------------

MAX_RETRIES = 15
RETRY_DELAY = 1.0  # seconds (escalates: 1s, 2s, 3s, ...)


def _commit_batch(conn: sqlite3.Connection, batch: list[tuple[str, int]]) -> None:
    """Execute a batch update with retries for lock contention."""
    for attempt in range(MAX_RETRIES):
        try:
            conn.executemany(
                "UPDATE products SET asset_class = ? WHERE id = ?", batch
            )
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise


def run_classification(conn: sqlite3.Connection) -> dict[str, int]:
    """Classify all products in batches. Returns counts per class.

    Reads all rows first (closing cursor), then writes in batches.
    This avoids read-write deadlocks with the running extraction process.
    """
    cur = conn.execute("SELECT COUNT(*) FROM products")
    total = cur.fetchone()[0]
    print(f"Classifying {total:,} products...")

    # Read ALL rows into memory first (IDs + fields needed for classification)
    print(f"  Reading rows...", end=" ")
    rows = conn.execute(
        "SELECT id, product_name, product_type, notional_amount, cusip, underlier_names "
        "FROM products"
    ).fetchall()
    print(f"{len(rows):,} rows loaded.")

    # Classify in memory
    counts: dict[str, int] = {}
    updates: list[tuple[str, int]] = []
    for pid, name, ptype, notional, cusip, underliers in rows:
        asset_class = classify_product(name, ptype, notional, cusip, underliers)
        updates.append((asset_class, pid))
        counts[asset_class] = counts.get(asset_class, 0) + 1

    # Write in batches with retries
    processed = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]
        _commit_batch(conn, batch)
        processed += len(batch)
        print(f"  {processed:>8,} / {total:,}", end="\r")

    print(f"  {processed:>8,} / {total:,} -- done.")
    return counts


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def print_summary(conn: sqlite3.Connection, counts: dict[str, int]) -> None:
    """Print classification summary and top non-structured-note products."""
    print("\n--- Classification Summary ---")
    for cls in ("structured_note", "etn", "mtn", "shelf", "other"):
        print(f"  {cls:<20s} {counts.get(cls, 0):>8,}")
    print(f"  {'TOTAL':<20s} {sum(counts.values()):>8,}")

    # Top 10 largest notional per non-structured-note class
    for cls in ("etn", "mtn", "shelf", "other"):
        if counts.get(cls, 0) == 0:
            continue
        print(f"\n--- Top 10 by notional: {cls} ---")
        cur = conn.execute(
            "SELECT product_name, notional_amount, cusip, product_type "
            "FROM products WHERE asset_class = ? "
            "ORDER BY COALESCE(notional_amount, 0) DESC LIMIT 10",
            (cls,),
        )
        for i, row in enumerate(cur.fetchall(), 1):
            name = (row[0] or "(no name)")[:80]
            notional = f"${row[1]:,.0f}" if row[1] else "n/a"
            cusip = row[2] or "-"
            ptype = row[3] or "-"
            print(f"  {i:>2}. [{ptype}] {name}")
            print(f"      notional={notional}  cusip={cusip}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    print(f"Database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        print("Note: could not switch to WAL mode (database in use). Proceeding.")
    try:
        ensure_column(conn)
        t0 = time.perf_counter()
        counts = run_classification(conn)
        elapsed = time.perf_counter() - t0
        print(f"Classification completed in {elapsed:.1f}s")
        print_summary(conn, counts)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
