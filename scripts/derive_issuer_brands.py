"""Phase 3.4 — Derive issuer_display via regex brand matching (Layer 1).

Reads all ACTV funds where issuer_display IS NULL from mkt_master_data,
applies a prioritised regex dict against fund_name (uppercased), and writes:
  - config/rules/issuer_brand_overrides.csv  (ticker, issuer_display, source, notes)
  - docs/issuer_review_queue.csv             (ticker, fund_name, etp_category,
                                              current_issuer, suggested_brand)

Layer 1  — fund_name regex  (implemented)
Layer 2  — EDGAR sponsor lookup  (deferred; prints placeholder)
Layer 3  — Manual review queue   (residue written to issuer_review_queue.csv)

Run:
    python scripts/derive_issuer_brands.py [--dry-run]

After editing issuer_brand_overrides.csv manually (Layer 3 resolutions):
    python scripts/apply_issuer_brands.py
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "issuer_brand_overrides.csv"
REVIEW_QUEUE_CSV = PROJECT_ROOT / "docs" / "issuer_review_queue.csv"

# ---------------------------------------------------------------------------
# LAYER 1 — regex dict: pattern (matched against UPPER fund_name) -> brand
# Order matters: first match wins. More specific patterns go first.
# ---------------------------------------------------------------------------
BRAND_PATTERNS: list[tuple[str, str]] = [
    # --- REX / T-REX sub-brands (before generic BLACKROCK, etc.) ---
    (r"^T-?REX\b", "REX"),
    (r"^TRADR\b", "Tradr"),
    # --- Single-stock / Leveraged & Inverse boutiques ---
    (r"^DEFIANCE\b", "Defiance"),
    (r"^GRANITESHARES?\b", "GraniteShares"),
    (r"^YIELDMAX\b", "YieldMax"),
    (r"^ROUNDHILL\b", "Roundhill"),
    (r"^DIREXION\b", "Direxion"),
    (r"^MICROSECTORS?\b", "MicroSectors"),
    (r"^VOLATILITYSHARES?\b", "VolatilityShares"),
    (r"^VISTASHARES?\b", "VistaShares"),
    (r"\bMAX\s+ETF\b", "MAX"),
    # --- Active thematic boutiques ---
    (r"^AMPLIUS\b", "Amplius"),
    (r"^ADAPTIV\b", "Adaptiv"),
    (r"^TEUCRIUM\b", "Teucrium"),
    (r"^TUTTLE\b", "Tuttle"),
    (r"^TAPPEALPHA\b|^TAPPALPHA\b", "TappAlpha"),
    (r"^SIMPLIFY\b", "Simplify"),
    (r"^INNOVATOR\b", "Innovator"),
    (r"^CALAMOS\b", "Calamos"),
    (r"^NEOS\b", "Neos"),
    (r"^APTUS\b", "Aptus"),
    (r"^RAREVIEW\b", "Rareview"),
    (r"^CONVEXITYSHARES?\b", "ConvexityShares"),
    # --- Crypto specialists ---
    (r"^BITWISE\b", "Bitwise"),
    (r"^21SHARES?\b", "21Shares"),
    (r"^HASHDEX\b", "Hashdex"),
    (r"^OSPREY\b", "Osprey"),
    (r"^VALKYRIE\b", "Valkyrie"),
    (r"^GRAYSCALE\b", "Grayscale"),
    # --- Large-cap issuers ---
    (r"^BLACKROCK\b", "BlackRock"),
    (r"^ISHARES\b", "iShares"),
    (r"^IPATH\b", "iPath"),
    (r"^VANGUARD\b", "Vanguard"),
    (r"^INVESCO\b", "Invesco"),
    (r"^POWERSHARES\b", "Invesco"),
    (r"^STATE\s+STREET\b", "State Street"),
    (r"^SPDR\b", "State Street"),
    (r"^SSGA\b", "State Street"),
    (r"^FIDELITY\b", "Fidelity"),
    (r"^SCHWAB\b", "Schwab"),
    (r"^GLOBAL\s*X\b", "Global X"),
    (r"^WISDOMTREE\b", "WisdomTree"),
    (r"^VANECK\b", "VanEck"),
    (r"^DIMENSIONAL\b", "Dimensional"),
    (r"^FRANKLIN\b", "Franklin Templeton"),
    (r"^PROSHARES\b", "ProShares"),
    (r"^PROFUNDS\b", "ProFunds"),
    (r"^FIRST\s+TRUST\b", "First Trust"),
    (r"^FT\s+VEST\b", "First Trust"),
    (r"^GOLDMAN\b", "Goldman Sachs"),
    (r"^JPMORGAN\b", "JP Morgan"),
    (r"^JANUS\b", "Janus Henderson"),
    (r"^ALPHA\s+ARCHITECT\b", "Alpha Architect"),
    (r"^AVANTIS\b", "Avantis"),
    (r"^PACER\b", "Pacer"),
    (r"^AMPLIFY\b", "Amplify"),
    (r"^WISDOMTREE\b", "WisdomTree"),
    (r"^KRANESHARES?\b", "KraneShares"),
    (r"^BNY\s+MELLON\b|^BNY\b", "BNY Mellon"),
    (r"^NYLI\b", "New York Life"),
    (r"^NEUBERGER\b", "Neuberger Berman"),
    (r"^PIMCO\b", "PIMCO"),
    (r"^NUVEEN\b", "Nuveen"),
    (r"^COLUMBIA\b", "Columbia"),
    (r"^HARBOR\b", "Harbor"),
    (r"^NORTHERN\b", "Northern Trust"),
    (r"^FLEXSHARES\b", "FlexShares"),
    (r"^XTRACKERS\b", "Xtrackers"),
    (r"^DWS\b", "DWS"),
    (r"^VIRTUS\b", "Virtus"),
    (r"^CAMBRIA\b", "Cambria"),
    (r"^HARTFORD\b", "Hartford"),
    (r"^AMERICAN\s+CENTURY\b", "American Century"),
    (r"^AMERICAN\s+BEACON\b", "American Beacon"),
    (r"^PUTNAM\b", "Putnam"),
    (r"^AB\b", "AB (AllianceBernstein)"),
    (r"^T\s+ROWE\b|^T\.\s*ROWE\b", "T. Rowe Price"),
    (r"^PGIM\b", "PGIM"),
    (r"^BONDBLOXX\b", "BondBloxx"),
    (r"^VICTORYSHARES?\b", "VictoryShares"),
    (r"^ADVISORSHARES?\b", "AdvisorShares"),
    (r"^ALPS\b", "ALPS"),
    (r"^HORIZON\b", "Horizon Kinetics"),
    (r"^SPROTT\b", "Sprott"),
    (r"^TOUCHSTONE\b", "Touchstone"),
    (r"^TCW\b", "TCW"),
    (r"^STRIVE\b", "Strive"),
    (r"^BARON\b", "Baron"),
    (r"^MOTLEY\b", "Motley Fool"),
    (r"^EATON\b", "Eaton Vance"),
    (r"^MFS\b", "MFS"),
    (r"^SEI\b", "SEI"),
    (r"^GMO\b", "GMO"),
    (r"^MATTHEWS\b", "Matthews"),
    (r"^NOMURA\b", "Nomura"),
    (r"^TORTOISE\b", "Tortoise"),
    (r"^THEMES\b", "Themes"),
    (r"^USCF\b", "USCF"),
    (r"^TEUCRIUM\b", "Teucrium"),
    (r"^ABRDN\b", "abrdn"),
    (r"^DOUBLELINE\b", "DoubleLine"),
    (r"^FEDERATED\b", "Federated Hermes"),
    (r"^LAZARD\b", "Lazard"),
    (r"^COHEN\s*&?\s*STEERS\b", "Cohen & Steers"),
    (r"^JOHN\s+HANCOCK\b", "John Hancock"),
    (r"^OVERLAY\b", "Overlay"),
    (r"^ETRACS\b", "ETRACS"),
    (r"^MORGAN\s+STANLEY\b", "Morgan Stanley"),
    (r"^ROCKEFELLER\b", "Rockefeller"),
    (r"^PICTET\b", "Pictet"),
    (r"^RAYLIANT\b", "Rayliant"),
    (r"^RUSSELL\b", "Russell"),
    (r"^EVENTIDE\b", "Eventide"),
    (r"^INSPIRE\b", "Inspire"),
    (r"^TRINITY\b", "Trinity"),
    (r"^AAAA\s+US\b", "Amplius"),   # explicit: AAAA is Amplius
]

# Compile once for speed
_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(pat, re.IGNORECASE), brand)
    for pat, brand in BRAND_PATTERNS
]


def match_brand(fund_name: str) -> str | None:
    """Return the first matching brand or None."""
    name_upper = (fund_name or "").upper().strip()
    for pattern, brand in _COMPILED:
        if pattern.search(name_upper):
            return brand
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Derive issuer_display from fund_name regex (Layer 1)."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Print summary without writing CSVs.")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute(
        """
        SELECT ticker, fund_name, issuer, etp_category
        FROM mkt_master_data
        WHERE market_status IN ('ACTV', 'PEND') AND issuer_display IS NULL
        ORDER BY ticker
        """
    )
    rows = cur.fetchall()
    con.close()

    print(f"Found {len(rows):,} ACTV/PEND funds with NULL issuer_display")

    # --- Layer 1: regex matching ---
    l1_hits: list[dict] = []
    residue: list[dict] = []

    for ticker, fund_name, issuer, etp_category in rows:
        brand = match_brand(fund_name or "")
        if brand:
            l1_hits.append({
                "ticker": ticker,
                "issuer_display": brand,
                "source": "layer1_regex",
                "notes": f"Matched fund_name: {(fund_name or '').strip()}",
            })
        else:
            residue.append({
                "ticker": ticker,
                "fund_name": fund_name,
                "etp_category": etp_category,
                "current_issuer": issuer,
                "suggested_brand": "",
            })

    # --- Layer 2 placeholder ---
    print("TODO: Layer 2 - EDGAR sponsor lookup not yet implemented "
          f"({len(residue):,} tickers would be queried)")

    # --- Summary ---
    print(f"\nSummary:")
    print(f"  Total NULL issuer_display (ACTV/PEND): {len(rows):,}")
    print(f"  Resolved by Layer 1 (regex):      {len(l1_hits):,}")
    print(f"  In manual review queue (Layer 3): {len(residue):,}")

    if args.dry_run:
        print("\n[DRY-RUN] No files written. First 5 L1 hits:")
        for h in l1_hits[:5]:
            print(f"  {h['ticker']:15s} -> {h['issuer_display']}")
        return 0

    # --- Write issuer_brand_overrides.csv ---
    OVERRIDES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OVERRIDES_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["ticker", "issuer_display", "source", "notes"]
        )
        writer.writeheader()
        writer.writerows(l1_hits)
    print(f"\nWrote {len(l1_hits):,} rows -> {OVERRIDES_CSV}")

    # --- Write issuer_review_queue.csv ---
    REVIEW_QUEUE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_QUEUE_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "ticker", "fund_name", "etp_category",
                "current_issuer", "suggested_brand",
            ],
        )
        writer.writeheader()
        writer.writerows(residue)
    print(f"Wrote {len(residue):,} rows -> {REVIEW_QUEUE_CSV}")

    # --- Spot-check for AMA US ---
    ama_hit = next((h for h in l1_hits if h["ticker"] == "AMA US"), None)
    if ama_hit:
        print(f"\nSpot-check AMA US: issuer_display = \"{ama_hit['issuer_display']}\" [OK]")
    else:
        print("\nWARNING: AMA US not in L1 hits — check DEFIANCE pattern")

    return 0


if __name__ == "__main__":
    sys.exit(main())
