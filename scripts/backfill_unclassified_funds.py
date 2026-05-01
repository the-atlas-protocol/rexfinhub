"""Phase 4 + 6 — Backfill NULL primary_strategy funds using keyword classifier.

For ACTV funds not yet in fund_master.csv (the 3,267 with NULL etp_category),
apply a keyword-based classifier that maps fund_name + BBG fields directly
to the new (asset_class, primary_strategy, sub_strategy) taxonomy.

Confident classifications (clear keyword match) → write to fund_master.csv
Ambiguous → leave NULL, surface to preflight + Phase 5 LLM routine for resolution

Output: appends new rows to config/rules/fund_master.csv (does NOT touch
existing rows). Idempotent — safe to re-run.

Keyword rules below are heuristic. Phase 5's scheduled routine will refine
the ambiguous tail.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
MASTER_CSV = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"

# Asset class keyword cues (in fund_name)
ASSET_KEYWORDS = [
    (r"\b(BITCOIN|ETHEREUM|CRYPTO|BTC|ETH|SOLANA|DOGECOIN|POLKADOT|SUI|CARDANO|XRP|CHAINLINK|STELLAR|AVALANCHE)\b", "Crypto"),
    (r"\b(GOLD|SILVER|COPPER|PLATINUM|PALLADIUM|OIL|GAS|COMMODIT|MINERALS|MINING)\b", "Commodity"),
    (r"\b(BOND|TREASUR|MUNI|FIXED INCOME|CREDIT|YIELD CURVE|AGGREGATE|TIPS|FLOAT(?:ING)?\s*RATE|HIGH YIELD|JUNK|INVESTMENT GRADE)\b", "Fixed Income"),
    (r"\b(VOLATILITY|VIX|VOL\s+TARGET)\b", "Volatility"),
    (r"\b(CURRENCY|DOLLAR|EURO|YEN|POUND|FX)\b", "Currency"),
    (r"\b(MULTI[\s-]ASSET|TARGET[\s-]DATE|RISK[\s-]PARITY|BALANCED|ALLOCATION|DIVERSIFIED)\b", "Multi-Asset"),
]

# Primary strategy + sub keyword cues
STRATEGY_KEYWORDS = [
    # L&I
    (r"\b(2X|3X|-1X|-2X|-3X)\s*(LONG|SHORT|INVERSE|BULL|BEAR)\b", ("L&I", None)),
    (r"\b(LEVERAG|INVERSE|BEAR\s+ETF|BULL\s+ETF)\b", ("L&I", None)),

    # Income
    (r"\b(AUTOCALL)\b", ("Income", "Structured Product Income > Autocallable")),
    (r"\b(COVERED\s+CALL|BUY[-\s]?WRITE|YIELDMAX|YIELDBOOST|PREMIUM\s+INCOME|0DTE|ODTE)\b", ("Income", "Derivative Income > Covered Call")),
    (r"\b(PUT[-\s]?WRITE)\b", ("Income", "Derivative Income > Put-Write")),
    (r"\b(WEEKLY\s*PAY|WEEKLY\s*DISTRIBUTION)\b", ("Income", "Derivative Income > 0DTE / Weekly-Pay")),
    (r"\b(EQUITY\s+LINKED\s+(?:HIGH|MODERATE|AGGRESSIVE)\s+INCOME)\b", ("Income", "Structured Product Income > Autocallable")),
    (r"\b(TARGET\s+\d+%?\s+INCOME)\b", ("Income", "Derivative Income > Covered Call")),

    # Defined Outcome
    (r"\b(BUFFER|BARRIER\s+OUTCOME)\b", ("Defined Outcome", "Buffer")),
    (r"\b(FLOOR\s+ETF|MAX\s+LOSS)\b", ("Defined Outcome", "Floor")),
    (r"\b(ACCELERAT|UNCAPPED\s+ACCELERAT)\b", ("Defined Outcome", "Growth")),
    (r"\b(DUAL\s+DIRECTIONAL)\b", ("Defined Outcome", "Dual Directional")),
    (r"\b(TAX[-\s]?AWARE\s+COLLATERAL|BOX\s+SPREAD)\b", ("Defined Outcome", "Box Spread")),

    # Risk Mgmt
    (r"\b(HEDGED\s+EQUITY)\b", ("Risk Mgmt", "Hedged Equity")),
    (r"\b(RISK[-\s]?ADAPTIVE|ADAPTIVE\s+RISK|RISK[-\s]?MANAGED)\b", ("Risk Mgmt", "Risk-Adaptive")),
    (r"\b(MANAGED\s+FUTURES|TREND\s+FOLLOWING|CTA\b)\b", ("Risk Mgmt", "Trend / Managed Futures")),

    # Plain Beta sub-types — checked LAST (broadest)
    (r"\b(SECTOR|TECHNOLOGY\s+SELECT|HEALTHCARE\s+SELECT|FINANCIAL\s+SELECT|ENERGY\s+SELECT|REIT|REAL\s+ESTATE)\b", ("Plain Beta", "Sector")),
    (r"\b(AI\b|ARTIFICIAL\s+INTELLIGENCE|ROBOTICS|SPACE|DEFENSE|DRONE|GENOMICS|CLEAN\s+ENERGY|EV\b)\b", ("Plain Beta", "Thematic")),
    (r"\b(VALUE|GROWTH|QUALITY|MOMENTUM|LOW[-\s]?VOL|DIVIDEND\s+ARISTOCRATS|HIGH\s+DIVIDEND)\b", ("Plain Beta", "Style")),
    (r"\b(EMERGING\s+MARKETS|DEVELOPED\s+MARKETS|EAFE|EUROPE|ASIA|JAPAN|CHINA|INDIA|EX[-\s]?US)\b", ("Plain Beta", "Broad")),
]


def classify_keyword(fund_name: str, asset_class_focus: str = "",
                     is_crypto: str = "", uses_leverage: str = "") -> dict:
    """Return (asset_class, primary_strategy, sub_strategy) by keyword match.
    Empty string for any field means 'unknown — leave NULL'."""
    name = (fund_name or "").upper()
    out = {"asset_class": "", "primary_strategy": "", "sub_strategy": ""}

    # Asset class — keyword first, then BBG fallback
    for pattern, ac in ASSET_KEYWORDS:
        if re.search(pattern, name):
            out["asset_class"] = ac
            break
    if not out["asset_class"]:
        # BBG fallback
        if str(is_crypto).strip().lower() in ("true", "yes", "1"):
            out["asset_class"] = "Crypto"
        else:
            focus_map = {
                "Equity": "Equity",
                "Fixed Income": "Fixed Income",
                "Commodity": "Commodity",
                "Currency": "Currency",
                "Mixed Allocation": "Multi-Asset",
                "Alternative": "Multi-Asset",
            }
            out["asset_class"] = focus_map.get((asset_class_focus or "").strip(), "Equity")

    # Primary strategy + sub — first matching pattern wins (order matters)
    for pattern, (primary, sub) in STRATEGY_KEYWORDS:
        if re.search(pattern, name):
            out["primary_strategy"] = primary
            if sub:
                out["sub_strategy"] = sub
            else:
                # L&I default: derive long/short from name
                if "SHORT" in name or "INVERSE" in name or "BEAR" in name or "-1X" in name or "-2X" in name or "-3X" in name:
                    out["sub_strategy"] = "Short"
                else:
                    out["sub_strategy"] = "Long"
            break

    if not out["primary_strategy"]:
        # Default — Plain Beta / Broad if asset class is Equity/FI/Commodity, else NULL
        if out["asset_class"] in ("Equity", "Fixed Income", "Commodity", "Currency", "Crypto"):
            out["primary_strategy"] = "Plain Beta"
            out["sub_strategy"] = "Broad"  # safe default
        # else leave NULL — needs LLM/manual review

    return out


def main():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    # Load existing master to know which tickers are already classified
    existing_tickers = set()
    if MASTER_CSV.exists():
        for r in csv.DictReader(MASTER_CSV.open(encoding="utf-8")):
            t = (r.get("ticker") or "").strip()
            if t:
                existing_tickers.add(t)
    print(f"Existing fund_master.csv tickers: {len(existing_tickers):,}")

    # Pull NULL primary_strategy ACTV funds
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, issuer, issuer_display,
               asset_class_focus, is_crypto, uses_leverage
        FROM mkt_master_data
        WHERE market_status='ACTV' AND primary_strategy IS NULL
    """)
    rows = cur.fetchall()
    con.close()

    print(f"NULL primary_strategy ACTV funds: {len(rows):,}")
    print()

    # Classify + append to CSV
    field_order = ["ticker","fund_name","issuer_brand","asset_class","primary_strategy",
                   "sub_strategy","concentration","underlier_name","underlier_is_wrapper",
                   "root_underlier_name","wrapper_type","mechanism","leverage_ratio",
                   "direction","reset_period","distribution_freq","outcome_period_months",
                   "cap_pct","buffer_pct","accelerator_multiplier","barrier_pct",
                   "region","duration_bucket","credit_quality","tax_structure",
                   "qualified_dividends","source","notes"]

    classified = 0
    unclassified = 0
    new_rows = []
    for r in rows:
        ticker = (r["ticker"] or "").strip()
        if not ticker or ticker in existing_tickers:
            continue
        c = classify_keyword(r["fund_name"], r["asset_class_focus"],
                             r["is_crypto"], r["uses_leverage"])
        if not c["primary_strategy"]:
            unclassified += 1
            continue
        new_rows.append({
            "ticker": ticker,
            "fund_name": r["fund_name"],
            "issuer_brand": r["issuer_display"] or r["issuer"] or "",
            "asset_class": c["asset_class"],
            "primary_strategy": c["primary_strategy"],
            "sub_strategy": c["sub_strategy"],
            "wrapper_type": "standalone",
            "mechanism": "physical",
            "source": "auto-keyword-2026-05-01",
            "notes": "Backfilled by keyword classifier (Phase 6)",
        })
        classified += 1

    if not new_rows:
        print("Nothing to append.")
        return 0

    # Append to CSV
    with MASTER_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=field_order)
        for row in new_rows:
            for k in field_order:
                row.setdefault(k, "")
            w.writerow(row)

    print(f"Appended {classified:,} new rows to {MASTER_CSV}")
    print(f"Unclassified (still NULL): {unclassified:,} — for Phase 5 LLM routine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
