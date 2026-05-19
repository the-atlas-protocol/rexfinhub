"""One-shot data fix for Bug 1: Bitcoin underlier shows 0 competitors.

REX products use ``rex_products.underlier='XBTUSD'`` (the Bloomberg index
symbol). Competitor crypto-spot ETFs in mkt_master_data have
``map_li_underlier`` or ``map_cc_underlier`` set to 'Bitcoin' or 'BTC'.
String comparison in pipeline_calendar race-density aggregation fails to
match these → 0 competitors shown.

Quick fix: canonicalize all Bitcoin-related underlier strings in
mkt_master_data to 'XBTUSD' (matching what rex_products uses).

Future fix (Phase 4): polymorphic underlier_master table with
underlier_type='crypto_pair' + OpenFIGI resolution.

Idempotent — safe to re-run.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

log = logging.getLogger("canonicalize_crypto_underliers")

# (variant pattern, canonical) — applied case-insensitive, exact match
CANONICAL_MAP = {
    "BITCOIN":   "XBTUSD",
    "BTC":       "XBTUSD",
    "BTC/USD":   "XBTUSD",
    "BTC-USD":   "XBTUSD",
    "XBT":       "XBTUSD",
    "ETHEREUM":  "XETUSD",
    "ETH":       "XETUSD",
    "ETH/USD":   "XETUSD",
    "ETH-USD":   "XETUSD",
}


def main(dry_run: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not DB_PATH.exists():
        log.error("DB not found at %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    affected_total = 0
    for col in ("map_li_underlier", "map_cc_underlier", "map_crypto_underlier"):
        for variant, canonical in CANONICAL_MAP.items():
            cur.execute(
                f"SELECT COUNT(*) FROM mkt_master_data "
                f"WHERE UPPER(TRIM({col})) = ? AND ({col} IS NOT ?)",
                (variant, canonical),
            )
            n = cur.fetchone()[0]
            if n == 0:
                continue
            log.info("Found %d rows where %s='%s' -> would canonicalise to '%s'",
                     n, col, variant, canonical)
            affected_total += n
            if not dry_run:
                cur.execute(
                    f"UPDATE mkt_master_data SET {col} = ? "
                    f"WHERE UPPER(TRIM({col})) = ?",
                    (canonical, variant),
                )

    if dry_run:
        print(f"[DRY-RUN] Would canonicalize {affected_total} rows. "
              f"Re-run without --dry-run to apply.")
    else:
        conn.commit()
        print(f"Canonicalized {affected_total} rows across map_li/cc/crypto underlier columns.")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run=("--dry-run" in sys.argv)))
