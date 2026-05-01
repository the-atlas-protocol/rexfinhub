"""Phase 3.1 — Build initial fund_master.csv seed from existing classified funds.

Reads currently-classified funds from mkt_master_data + mkt_category_attributes
and maps the old 5-cat etp_category taxonomy onto the new 3-axis taxonomy
(asset_class × primary_strategy × sub_strategy + attributes).

Old → New mapping rules:
  etp_category=LI        → primary_strategy=L&I, sub_strategy=long/short
                           asset_class=Equity (default; refine for crypto/commodity later)
  etp_category=CC + cc_category=Autocallable
                         → primary_strategy=Income, sub_strategy=Structured Product > Autocallable
  etp_category=CC (other) → primary_strategy=Income, sub_strategy=Derivative Income > Covered Call
  etp_category=Crypto    → asset_class=Crypto, primary_strategy=Plain Beta, sub_strategy=Single-Access
  etp_category=Defined   → primary_strategy=Defined Outcome, sub_strategy=Buffer (default)
  etp_category=Thematic  → primary_strategy=Plain Beta, sub_strategy=Thematic

Asset class refinements use:
  - is_crypto=true     → Crypto
  - asset_class_focus  → bridge from BBG taxonomy (Equity, Fixed Income, Commodity, etc.)

Attributes derived from existing fields:
  - leverage from leverage_amount + uses_leverage
  - direction from map_li_direction
  - underlier from map_li_underlier or map_cc_underlier
  - mechanism inferred from uses_swaps / uses_derivatives

Output: config/rules/fund_master.csv
       (one row per ACTV ticker, full new-schema column set)

This is a SEED — Atlas + Ryu refine post-creation. Subsequent edits to
fund_master.csv take precedence over the auto-classifier on next pipeline run.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUTPUT_CSV = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"

# Columns matching the new mkt_master_data schema (Phase 2 migration)
COLUMNS = [
    "ticker",
    "fund_name",
    "issuer_brand",
    "asset_class",
    "primary_strategy",
    "sub_strategy",
    "concentration",
    "underlier_name",
    "underlier_is_wrapper",
    "root_underlier_name",
    "wrapper_type",
    "mechanism",
    "leverage_ratio",
    "direction",
    "reset_period",
    "distribution_freq",
    "outcome_period_months",
    "cap_pct",
    "buffer_pct",
    "accelerator_multiplier",
    "barrier_pct",
    "region",
    "duration_bucket",
    "credit_quality",
    "tax_structure",
    "qualified_dividends",
    "source",  # 'seed-2026-05-01' for this initial backfill
    "notes",
]

# Mapping: BBG asset_class_focus → our asset_class
ASSET_CLASS_MAP = {
    "Equity": "Equity",
    "Fixed Income": "Fixed Income",
    "Commodity": "Commodity",
    "Currency": "Currency",
    "Mixed Allocation": "Multi-Asset",
    "Alternative": "Multi-Asset",  # Alternative often = mixed/managed-futures
    "Specialty": "Equity",  # default-ish
}


def derive_asset_class(row: dict) -> str:
    """Map BBG fields to new asset_class taxonomy."""
    is_crypto = str(row.get("is_crypto", "")).strip().lower()
    if is_crypto in ("true", "yes", "1"):
        return "Crypto"
    focus = (row.get("asset_class_focus") or "").strip()
    return ASSET_CLASS_MAP.get(focus, "Equity")


def derive_primary_and_sub(row: dict) -> tuple[str, str]:
    """Map old etp_category + attributes → (primary_strategy, sub_strategy)."""
    cat = (row.get("etp_category") or "").strip()
    if cat == "LI":
        direction = (row.get("map_li_direction") or "").strip().lower()
        sub = "Long" if direction.startswith("long") else "Short" if direction else "Long"
        return ("L&I", sub)
    if cat == "CC":
        cc_cat = (row.get("cc_category") or "").strip().lower()
        if "autocall" in cc_cat:
            return ("Income", "Structured Product Income > Autocallable")
        return ("Income", "Derivative Income > Covered Call")
    if cat == "Crypto":
        return ("Plain Beta", "Single-Access")
    if cat == "Defined":
        outcome = (row.get("outcome_type") or "").strip().lower()
        if "buffer" in outcome:
            return ("Defined Outcome", "Buffer")
        if "floor" in outcome:
            return ("Defined Outcome", "Floor")
        if "accelerator" in outcome or "growth" in outcome:
            return ("Defined Outcome", "Growth")
        if "dual" in outcome:
            return ("Defined Outcome", "Dual Directional")
        return ("Defined Outcome", "Buffer")  # default — most common
    if cat == "Thematic":
        return ("Plain Beta", "Thematic")
    return ("", "")  # leaves NULL


def derive_attributes(row: dict, primary: str, sub: str) -> dict:
    """Derive the orthogonal attribute columns from existing fields."""
    attrs: dict = {}

    # Leverage
    lev_amt = (row.get("leverage_amount") or "").strip()
    if lev_amt:
        try:
            # "2x" or "200%" or "2.0" → numeric
            cleaned = lev_amt.lower().replace("x", "").replace("%", "").strip()
            n = float(cleaned)
            if n > 10:  # 200 means 200% = 2.0
                n = n / 100.0
            attrs["leverage_ratio"] = round(n, 2)
        except (ValueError, AttributeError):
            pass

    # Direction
    direction = (row.get("map_li_direction") or "").strip().lower()
    if direction:
        attrs["direction"] = "long" if direction.startswith("long") else "short"

    # Underlier — pick whichever exists
    li_underlier = (row.get("map_li_underlier") or "").strip()
    cc_underlier = (row.get("map_cc_underlier") or "").strip()
    crypto_underlier = (row.get("map_crypto_underlier") or "").strip()
    underlier = li_underlier or cc_underlier or crypto_underlier
    if underlier:
        attrs["underlier_name"] = underlier
        attrs["concentration"] = "single"  # most mappings are single

    # Mechanism inferred from BBG flags
    uses_swaps = str(row.get("uses_swaps", "")).strip().lower() in ("true", "yes", "1")
    uses_derivatives = str(row.get("uses_derivatives", "")).strip().lower() in ("true", "yes", "1")
    if uses_swaps:
        attrs["mechanism"] = "swap"
    elif uses_derivatives:
        if primary == "Income":
            attrs["mechanism"] = "options"
        elif primary == "Defined Outcome":
            attrs["mechanism"] = "options"
        else:
            attrs["mechanism"] = "derivatives"
    else:
        attrs["mechanism"] = "physical"

    # L&I products use daily reset by default
    if primary == "L&I":
        attrs["reset_period"] = "daily"

    # Wrapper default
    attrs["wrapper_type"] = "standalone"

    # Tax structure from regulatory_structure
    reg = (row.get("regulatory_structure") or "").strip().lower()
    if "40 act" in reg or "investment company" in reg:
        attrs["tax_structure"] = "40_act"
    elif "mlp" in reg:
        attrs["tax_structure"] = "mlp_k1"
    elif "grantor" in reg or "trust" in reg:
        attrs["tax_structure"] = "grantor_trust"

    return attrs


def build_seed():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT m.ticker, m.fund_name, m.issuer, m.issuer_display, m.etp_category,
               m.asset_class_focus, m.is_singlestock, m.is_crypto,
               m.uses_leverage, m.leverage_amount, m.uses_swaps, m.uses_derivatives,
               m.regulatory_structure, m.outcome_type, m.fund_type, m.market_status,
               a.cc_type, a.cc_category,
               a.map_cc_underlier, a.map_cc_index,
               a.map_li_category, a.map_li_subcategory,
               a.map_li_direction, a.map_li_underlier, a.map_li_leverage_amount,
               a.map_crypto_is_spot, a.map_crypto_underlier, a.map_crypto_type,
               a.map_defined_category, a.map_thematic_category
        FROM mkt_master_data m
        LEFT JOIN mkt_category_attributes a ON a.ticker = m.ticker
        WHERE m.market_status = 'ACTV'
    """)
    rows = cur.fetchall()
    con.close()

    print(f"ACTV funds in DB: {len(rows)}")
    print(f"  with etp_category set:   {sum(1 for r in rows if r['etp_category'])}")
    print(f"  with NULL etp_category:  {sum(1 for r in rows if not r['etp_category'])}")
    print()

    # Build seed rows
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()

        seeded = 0
        skipped = 0
        for r in rows:
            row_dict = dict(r)
            primary, sub = derive_primary_and_sub(row_dict)
            if not primary:
                # No mapping — leave for Phase 6 backfill (auto-classifier or LLM)
                skipped += 1
                continue
            asset_class = derive_asset_class(row_dict)
            attrs = derive_attributes(row_dict, primary, sub)

            seed_row = {
                "ticker": r["ticker"],
                "fund_name": r["fund_name"],
                "issuer_brand": r["issuer_display"] or r["issuer"] or "",
                "asset_class": asset_class,
                "primary_strategy": primary,
                "sub_strategy": sub,
                "source": "seed-2026-05-01",
                "notes": f"Migrated from etp_category={r['etp_category']}",
                **attrs,
            }
            # Fill missing columns with empty
            for c in COLUMNS:
                seed_row.setdefault(c, "")
            w.writerow(seed_row)
            seeded += 1

    print(f"Wrote {seeded:,} seeded rows to {OUTPUT_CSV}")
    print(f"  Skipped {skipped:,} unclassified funds (Phase 6 will handle these)")
    return 0


if __name__ == "__main__":
    sys.exit(build_seed())
