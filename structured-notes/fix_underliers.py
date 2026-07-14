"""Fix noisy underlier_names in existing extracted products.

Applies the same truncation rules added to parsers._normalize_underlier_result()
but runs as a standalone sqlite3 script — safe alongside running extraction.

Idempotent: re-running is safe.
"""
import re
import sqlite3
import time
from pathlib import Path

from config import DB_PATH
BATCH_SIZE = 500

# Index name -> Bloomberg ticker normalization
INDEX_TO_TICKER = {
    "S&P 500 Index": "SPX", "S&P 500": "SPX",
    "Russell 2000 Index": "RTY", "Russell 2000": "RTY",
    "Nasdaq-100 Index": "NDX", "Nasdaq-100": "NDX",
    "Dow Jones Industrial Average": "INDU",
    "EURO STOXX 50 Index": "SX5E", "EURO STOXX 50": "SX5E",
    "Nikkei 225 Index": "NKY",
    "MSCI EAFE Index": "MXEA", "MSCI Emerging Markets Index": "MXEF",
    "Hang Seng Index": "HSI",
    "FTSE 100 Index": "UKX", "FTSE 100": "UKX",
    "KOSPI 200 Index": "KOSPI2",
    "STOXX Europe 600 Index": "SXXP",
    "Nasdaq-100 Technology Sector Index": "NDXT",
    "S&P/TSX 60 Index": "SPTSX60",
}


def clean_underlier(raw: str) -> str | None:
    """Apply truncation rules + normalization to a raw underlier_names value.

    Returns None if the result is clearly not an underlier name.
    """
    result = raw

    # Product-name boundaries (BofA pattern, including "with Memory Feature" variant)
    result = re.sub(
        r"\s+The\s+(?:Contingent|Auto-?Callable|Enhanced|Fixed|Capped|Buffered|Dual|"
        r"Callable|Income|Digital|Leveraged|Accelerated|Barrier)[\s\w()-]*"
        r"(?:Notes?|Securities|Yield)\b.*$",
        "", result, flags=re.IGNORECASE)

    # Terms table leak
    result = re.sub(r"\s*(?:Aggregate|Stated)\s+principal\s+amount.*$", "", result, flags=re.IGNORECASE)

    # Table description trailer
    result = re.sub(r"\s*,?\s*as\s+set\s+forth\s+in.*$", "", result, flags=re.IGNORECASE)

    # Table header leak
    result = re.sub(r"\s*(?:Initial|Final)\s+underlying\s+(?:value|level|price).*$", "", result, flags=re.IGNORECASE)

    # Generic product-description leak
    result = re.sub(r"\s*underlying\s+(?:with\s+the|that\s+has)\s+.*$", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*least\s+performing\s+underlying\s+among\s+the\s+", "", result, flags=re.IGNORECASE)

    # Stray sentences / boilerplate
    result = re.sub(r"\s*(?:If\s+the\s+Final|Neither\s+the).*$", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*As\s+specified\s+under\s+.*$", "", result, flags=re.IGNORECASE)

    # Bloomberg symbol descriptors
    result = re.sub(r"\s*\(Bloomberg\s+symbol:\s*[A-Z]+\s*\)", "", result, flags=re.IGNORECASE)

    # Normalize verbose index references: "S&P 500 Index (the SPX Index )" -> "S&P 500 Index"
    result = re.sub(r"\s*\(the\s+([A-Z]{2,6})\s+Index\s*\)", "", result)

    # Clean "and the" connectors between indices -> comma
    result = re.sub(r"\s+and\s+(?:the\s+)?", ", ", result, flags=re.IGNORECASE)

    # Remove TM marks
    result = re.sub(r"\s*TM\b", "", result)

    result = result.strip().rstrip(",").strip()

    # Normalize parts
    parts = [p.strip() for p in result.split(",")]
    normalized = []
    for part in parts:
        if not part or len(part) < 2:
            continue
        clean_part = re.sub(r"^the\s+", "", part, flags=re.IGNORECASE).strip()
        if re.match(r"^(?:Fully|Neither|Pricing|CUSIP|Maturity|Payment|for\s+each|per\s+security)\b",
                     clean_part, re.IGNORECASE):
            continue
        mapped = INDEX_TO_TICKER.get(clean_part)
        if mapped:
            normalized.append(mapped)
        else:
            normalized.append(clean_part)

    return ", ".join(normalized) if normalized else result


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA busy_timeout=30000")

    # Process ALL entries with underlier_names (normalization helps clean ones too)
    rows = conn.execute(
        "SELECT id, underlier_names FROM products "
        "WHERE underlier_names IS NOT NULL AND underlier_names != ''"
    ).fetchall()

    print(f"Processing {len(rows):,} products with underlier_names")

    updated = 0
    nulled = 0
    unchanged = 0
    batch_update: list[tuple[str, int]] = []
    batch_null: list[tuple[int,]] = []

    # Patterns that indicate the value is NOT an underlier name at all
    GARBAGE_PATTERNS = [
        r"^As\s+specified\s+under",
        r"^underlying\s+(?:that|with)\s+",
        r"^If\s+the\s+(?:Final|closing)",
        r"^(?:Fully|Neither)\s+",
        r"^For\s+each\s+underlying",
    ]

    for pid, raw in rows:
        cleaned = clean_underlier(raw)
        if cleaned is None or not cleaned or len(cleaned) < 2:
            batch_null.append((pid,))
            nulled += 1
        elif cleaned != raw:
            batch_update.append((cleaned, pid))
            updated += 1
        else:
            # Check if the unchanged result is garbage
            is_garbage = any(re.match(p, cleaned, re.IGNORECASE) for p in GARBAGE_PATTERNS)
            if is_garbage:
                batch_null.append((pid,))
                nulled += 1
            else:
                unchanged += 1

        if len(batch_update) + len(batch_null) >= BATCH_SIZE:
            if batch_update:
                conn.executemany("UPDATE products SET underlier_names = ? WHERE id = ?", batch_update)
                batch_update.clear()
            if batch_null:
                conn.executemany("UPDATE products SET underlier_names = NULL WHERE id = ?", batch_null)
                batch_null.clear()
            conn.commit()
            print(f"  Processed {updated + nulled + unchanged:,} / {len(rows):,}...", end="\r")

    # Flush remainder
    if batch_update:
        conn.executemany("UPDATE products SET underlier_names = ? WHERE id = ?", batch_update)
    if batch_null:
        conn.executemany("UPDATE products SET underlier_names = NULL WHERE id = ?", batch_null)
    conn.commit()

    print(f"\nDone: {updated:,} fixed, {nulled:,} nulled (garbage), {unchanged:,} unchanged")

    # Verify
    remaining = conn.execute(
        "SELECT COUNT(*) FROM products "
        "WHERE underlier_names IS NOT NULL AND LENGTH(underlier_names) > 60"
    ).fetchone()[0]
    print(f"Remaining noisy entries: {remaining:,}")

    # Show sample of cleaned results
    print("\nSample cleaned entries:")
    samples = conn.execute(
        "SELECT underlier_names, COUNT(*) as cnt FROM products "
        "WHERE underlier_names IS NOT NULL AND parent_issuer = 'Bank of America' "
        "GROUP BY underlier_names ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    for name, cnt in samples:
        print(f"  [{cnt:>5}x] {name}")

    conn.close()


if __name__ == "__main__":
    main()
