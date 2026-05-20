"""Phase 4 (ADR 0006) — Stage 4: heuristic-classify underliers into underlier_master.

Walks every distinct underlier string from:
    rex_products.underlier
    rex_products.underlying_ticker
    rex_products.underlying_name
    mkt_master_data.map_li_underlier
    mkt_master_data.map_cc_underlier
    mkt_master_data.map_crypto_underlier

Classifies each via heuristic into one of:
    crypto_pair  (BTC, ETH, BTCUSD, BITCOIN, XBTUSD, etc.)
    index        (BMAXATCL Index, SPX, etc.)
    equity       (^[A-Z]{1,5}$ — looks up in mkt_master_data for confirmation)
    etp          (^[A-Z]{2,5}$ with " US" suffix or matching an ETP ticker)
    basket       (comma/pipe-separated multi)
    unknown      (manual review — written to underlier_master with type='unknown')

OpenFIGI resolution deferred to a follow-up — these heuristics handle the
common cases; the unknown queue captures the edge cases for later.

Idempotent. Pre-requisite: migrate_canonical_id_schema.py.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

BACKFILL_SOURCE = "heuristic_2026-05-19"


# Crypto detection — exact patterns and base/quote inference
CRYPTO_PATTERNS = {
    # display_symbol: (base, quote, regex-of-source-strings)
    "XBTUSD": ("BTC", "USD", re.compile(r"^(BTC|BITCOIN|XBT)([- ]?USD)?$", re.I)),
    "XETUSD": ("ETH", "USD", re.compile(r"^(ETH|ETHEREUM|XET)([- ]?USD)?$", re.I)),
    "XSOLUSD": ("SOL", "USD", re.compile(r"^(SOL|SOLANA)([- ]?USD)?$", re.I)),
    "XDOGEUSD": ("DOGE", "USD", re.compile(r"^(DOGE|DOGECOIN)([- ]?USD)?$", re.I)),
    "XXRPUSD": ("XRP", "USD", re.compile(r"^(XRP|RIPPLE)([- ]?USD)?$", re.I)),
}

# Index detection — ends with " Index" or matches a known provider code
INDEX_RE = re.compile(r"^([A-Z0-9]+)\s+Index$", re.I)
INDEX_PROVIDERS = {
    "SPX": "S&P", "NDX": "NASDAQ", "DJI": "Dow Jones",
    "RUT": "Russell", "MID": "S&P", "SML": "S&P",
    "BMAXATCL": "Bloomberg", "BMAX": "Bloomberg",
}

# Equity / ETP ticker pattern (allows " US" Bloomberg suffix)
TICKER_RE = re.compile(r"^([A-Z]{1,5})(\s+US)?$")

# Basket — multiple tickers separated by /,&,+
BASKET_RE = re.compile(r"[,/&+]")


def classify(raw: str) -> dict:
    """Return a dict describing the underlier classification."""
    s = raw.strip()
    if not s:
        return {}

    # Crypto first (most specific)
    for sym, (base, quote, regex) in CRYPTO_PATTERNS.items():
        # Strip common decorations
        cleaned = re.sub(r"\s+", "", s).upper()
        if regex.fullmatch(cleaned) or s.upper().replace(" ", "").replace("-", "") == sym:
            return {
                "underlier_type": "crypto_pair",
                "crypto_base": base,
                "crypto_quote": quote,
                "display_symbol": sym,
            }

    # Index
    m = INDEX_RE.match(s)
    if m:
        code = m.group(1).upper()
        provider = INDEX_PROVIDERS.get(code, "Unknown")
        return {
            "underlier_type": "index",
            "index_provider": provider,
            "index_code": code,
            "display_symbol": code,
        }
    if s.upper() in INDEX_PROVIDERS:
        code = s.upper()
        return {
            "underlier_type": "index",
            "index_provider": INDEX_PROVIDERS[code],
            "index_code": code,
            "display_symbol": code,
        }

    # Basket
    if BASKET_RE.search(s):
        return {"underlier_type": "basket", "display_symbol": s}

    # Equity / ETP
    m = TICKER_RE.match(s)
    if m:
        ticker = m.group(1).upper()
        # Treat as equity by default; downstream can flip to etp via FIGI lookup
        return {
            "underlier_type": "equity",
            "ticker": ticker,
            "display_symbol": ticker,
        }

    # Long-form name without distinguishing markers
    return {"underlier_type": "unknown", "display_symbol": s}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Pull distinct strings from all underlier source columns
        sources_sql = []
        for col in ("underlying_ticker", "underlying_name"):
            sources_sql.append(f"SELECT DISTINCT {col} as v FROM rex_products WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
        for col in ("map_li_underlier", "map_cc_underlier", "map_crypto_underlier"):
            sources_sql.append(f"SELECT DISTINCT {col} as v FROM mkt_master_data WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
        union_sql = " UNION ".join(sources_sql)
        rows = conn.execute(union_sql).fetchall()
        distinct_strings = sorted({r["v"].strip() for r in rows if r["v"]})

        print(f"DB: {db_path}")
        print(f"Distinct underlier strings found: {len(distinct_strings)}")

        # Existing display_symbols (idempotency)
        existing = {row[0] for row in conn.execute(
            "SELECT display_symbol FROM underlier_master").fetchall()}

        type_counts = {}
        to_insert = []
        for s in distinct_strings:
            info = classify(s)
            if not info:
                continue
            disp = info["display_symbol"]
            type_counts[info["underlier_type"]] = type_counts.get(info["underlier_type"], 0) + 1
            if disp in existing:
                continue
            existing.add(disp)
            to_insert.append((
                str(uuid.uuid4()),
                info["underlier_type"],
                info.get("primary_figi"),
                info.get("ticker"),
                info.get("index_provider"),
                info.get("index_code"),
                info.get("crypto_base"),
                info.get("crypto_quote"),
                disp,
                datetime.utcnow().isoformat(),
                BACKFILL_SOURCE,
            ))

        print(f"\nClassification breakdown:")
        for ty in ("crypto_pair", "index", "equity", "etp", "basket", "unknown"):
            print(f"  {ty:14s} {type_counts.get(ty, 0)}")

        print(f"\nNew rows to insert into underlier_master: {len(to_insert)}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if to_insert:
            conn.executemany(
                "INSERT INTO underlier_master "
                "(underlier_id, underlier_type, primary_figi, ticker, "
                "index_provider, index_code, crypto_base, crypto_quote, "
                "display_symbol, created_at, resolved_via) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                to_insert,
            )
            conn.commit()
            print(f"\nCommitted {len(to_insert)} underlier_master rows.")

        total = conn.execute("SELECT COUNT(*) FROM underlier_master").fetchone()[0]
        unknown = conn.execute(
            "SELECT COUNT(*) FROM underlier_master WHERE underlier_type='unknown'"
        ).fetchone()[0]
        print(f"underlier_master total: {total} (unknowns: {unknown})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
