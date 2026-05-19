"""Phase 4 — improved heuristic pass on underlier_master rows currently typed 'unknown'.

Adds pattern matchers for:
  - Bloomberg-suffixed equities (DJTU UA, EWY US Equity, MSOS US Equity)
  - Currency pairs (EURUSD Curncy, JPYUSD Curncy)
  - Commodities (CLA Comdty)
  - Long-form crypto names (Chainlink, Litecoin, etc.)
  - MSCI/proprietary index codes (M2US000$, M2USSNQ, MQUSTRAV)
  - Bloomberg ticker variants (BRK.B, BBG sector tickers)
  - Junk placeholders ('-', '0', empty) — marks them resolved_via='junk'

Anything still unmatched stays as 'unknown' with a descriptive `resolved_via`
marker so future OpenFIGI runs can target them.

Idempotent. Updates only underlier_master rows where underlier_type='unknown'.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


# Long-form crypto names -> (base, quote, display)
CRYPTO_LONG = {
    "CHAINLINK": ("LINK", "USD", "XLNKUSD"),
    "LITECOIN":  ("LTC",  "USD", "XLTCUSD"),
    "POLKADOT":  ("DOT",  "USD", "XDOTUSD"),
    "AVALANCHE": ("AVAX", "USD", "XAVAXUSD"),
    "CARDANO":   ("ADA",  "USD", "XADAUSD"),
    "POLYGON":   ("MATIC","USD", "XMATICUSD"),
}

# Known private companies (no FIGI; just tag for downstream)
PRIVATE_COMPANIES = {
    "ANDURIL", "ANTHROPIC", "DISCORD", "OPENAI", "SPACEX", "STRIPE", "BYTEDANCE",
    "X-CORP", "XAI", "EPIC GAMES",
}

# Bloomberg-suffixed equity patterns: "TICKER UA", "TICKER US Equity", "TICKER LN Equity"
BBG_EQUITY_RE = re.compile(r"^([A-Z0-9.\-]+)\s+(US|UA|LN|JP|HK|CN|FP|GR|IM|SE)(\s+Equity)?$", re.I)
BBG_CURNCY_RE = re.compile(r"^([A-Z]{6})\s+Curncy$", re.I)
BBG_COMDTY_RE = re.compile(r"^([A-Z0-9]+)\s+Comdty$", re.I)
# MSCI/proprietary codes: M2US000$, M2USSNQ, MQUSTRAV, DJUSDIVT, M00IMV$T
PROPRIETARY_INDEX_RE = re.compile(r"^[A-Z][A-Z0-9$]{5,}$")
# Brookfield-style dotted ticker: BRK.B
DOTTED_TICKER_RE = re.compile(r"^([A-Z]{1,5})\.([A-Z])$")

# Junk
JUNK_VALUES = {"-", "0", "", "None", "Basket"}


def reclassify(disp: str) -> dict | None:
    """Return a dict of fields to UPDATE, or None to leave as-is."""
    s = disp.strip()
    if not s:
        return None

    upper = s.upper()

    # Junk
    if upper in {x.upper() for x in JUNK_VALUES}:
        return {"resolved_via": "junk"}

    # Private companies
    if upper in PRIVATE_COMPANIES:
        return {
            "underlier_type": "equity",
            "ticker": upper,
            "resolved_via": "manual_private",
        }

    # Long-form crypto
    if upper in CRYPTO_LONG:
        base, quote, sym = CRYPTO_LONG[upper]
        return {
            "underlier_type": "crypto_pair",
            "crypto_base": base,
            "crypto_quote": quote,
            "display_symbol": sym,
            "resolved_via": "heuristic_v2",
        }

    # Bloomberg currency: EURUSD Curncy
    m = BBG_CURNCY_RE.match(s)
    if m:
        pair = m.group(1).upper()
        return {
            "underlier_type": "fx",
            "display_symbol": pair,
            "resolved_via": "heuristic_v2",
        }

    # Bloomberg commodity: CLA Comdty (CL=crude WTI generic future, CLA=front month)
    m = BBG_COMDTY_RE.match(s)
    if m:
        return {
            "underlier_type": "commodity",
            "ticker": m.group(1).upper(),
            "display_symbol": m.group(1).upper(),
            "resolved_via": "heuristic_v2",
        }

    # Bloomberg-suffixed equity: "DJTU UA", "EWY US Equity"
    m = BBG_EQUITY_RE.match(s)
    if m:
        ticker = m.group(1).upper()
        country_suffix = m.group(2).upper()
        return {
            "underlier_type": "equity" if country_suffix in {"US", "UA"} else "etp",
            "ticker": ticker,
            "display_symbol": ticker,
            "resolved_via": "heuristic_v2",
        }

    # Dotted equity: BRK.B (share class)
    m = DOTTED_TICKER_RE.match(s.upper())
    if m:
        return {
            "underlier_type": "equity",
            "ticker": upper,
            "display_symbol": upper,
            "resolved_via": "heuristic_v2",
        }

    # MSCI/proprietary index code (alphanumeric, mixed-case symbol w/ $/digits)
    if PROPRIETARY_INDEX_RE.match(upper) and any(c.isdigit() or c == "$" for c in upper):
        # Likely MSCI (M2*, MQ*) or DJ (DJ*) or BNP (BCAD*)
        if upper.startswith("M") and not upper.startswith("MICRO"):
            provider = "MSCI"
        elif upper.startswith("DJ"):
            provider = "Dow Jones"
        elif upper.startswith("BCAD"):
            provider = "BNP Paribas"
        elif upper.startswith("BAIL"):
            provider = "Barclays"
        else:
            provider = "Proprietary"
        return {
            "underlier_type": "index",
            "index_provider": provider,
            "index_code": upper,
            "display_symbol": upper,
            "resolved_via": "heuristic_v2",
        }

    # Theme-style basket tags (BIGOIL, MINERS, MSOS, etc.) — keep as unknown but tag
    if re.match(r"^[A-Z]{4,}$", upper):
        return {
            "underlier_type": "basket",
            "display_symbol": upper,
            "resolved_via": "heuristic_v2_basket",
        }

    return None


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
        rows = conn.execute(
            "SELECT underlier_id, display_symbol FROM underlier_master WHERE underlier_type='unknown'"
        ).fetchall()
        print(f"DB: {db_path}")
        print(f"Unknown underliers: {len(rows)}")

        type_counts = {}
        updates = []
        still_unknown = []

        for r in rows:
            change = reclassify(r["display_symbol"])
            if not change:
                still_unknown.append(r["display_symbol"])
                continue
            new_type = change.get("underlier_type", "unknown")
            type_counts[new_type] = type_counts.get(new_type, 0) + 1
            updates.append((r["underlier_id"], change))

        print(f"\nReclassification result:")
        for ty, n in sorted(type_counts.items()):
            print(f"  -> {ty:14s} {n}")
        print(f"  still unknown:  {len(still_unknown)}")
        if still_unknown:
            for s in still_unknown[:20]:
                print(f"    {s!r}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        for uid, change in updates:
            cols = list(change.keys())
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            params = list(change.values()) + [uid]
            conn.execute(f"UPDATE underlier_master SET {set_clause} WHERE underlier_id = ?", params)

        conn.commit()
        print(f"\nCommitted {len(updates)} updates.")

        remaining = conn.execute(
            "SELECT COUNT(*) FROM underlier_master WHERE underlier_type='unknown'"
        ).fetchone()[0]
        print(f"underlier_master 'unknown' count now: {remaining}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
