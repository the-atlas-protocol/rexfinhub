"""Phase 5 — Classify remaining NULL primary_strategy funds via enhanced rules.

Targets the 209 hard-case ACTV funds that the original keyword classifier
in backfill_unclassified_funds.py could not resolve.  Rules are ordered from
most-specific to broadest catch-all.

Output:
  - APPENDS new rows to config/rules/fund_master.csv  (idempotent — skips
    tickers already present in that file)
  - Writes docs/classification_residue_2026-05-05.csv for any fund that
    still could not be matched after all 8 rules

Run: python scripts/classify_remaining_funds.py

Apply step (NOT THIS SCRIPT): scripts/apply_fund_master.py will read the
updated fund_master.csv and sync it into mkt_master_data.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / "data" / "etp_tracker.db"
MASTER_CSV   = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"
RESIDUE_CSV  = PROJECT_ROOT / "docs" / "classification_residue_2026-05-05.csv"

SOURCE_TAG = "classify-remaining-2026-05-05"
NOTES_TAG  = "Backfilled by enhanced classifier (Phase 5 manual rules)"

# ---------------------------------------------------------------------------
# CSV field order — must match fund_master.csv header exactly
# ---------------------------------------------------------------------------
FIELD_ORDER = [
    "ticker", "fund_name", "issuer_brand", "asset_class", "primary_strategy",
    "sub_strategy", "concentration", "underlier_name", "underlier_is_wrapper",
    "root_underlier_name", "wrapper_type", "mechanism", "leverage_ratio",
    "direction", "reset_period", "distribution_freq", "outcome_period_months",
    "cap_pct", "buffer_pct", "accelerator_multiplier", "barrier_pct",
    "region", "duration_bucket", "credit_quality", "tax_structure",
    "qualified_dividends", "source", "notes",
]

# ---------------------------------------------------------------------------
# Compiled patterns (applied to fund_name.upper())
# ---------------------------------------------------------------------------

# RULE 1 — L&I single-stock 2X / 3X
# Matches: "2X LONG AMAT", "3X SHORT NVDA", "2X BULL TSLA", "DAILY TARGET 2X LONG AMAT"
# Also matches Direxion-style "DAILY AAPL BULL 2X" (word-boundary flexible)
_R1_LI = re.compile(
    r"\b(\d+)X\s+(LONG|SHORT|INVERSE|BULL|BEAR)\s+([A-Z]{1,5})\b"
    r"|"
    r"\b(LONG|SHORT|INVERSE|BULL|BEAR)\s+(\d+)X\b",
    re.IGNORECASE,
)
# Detect direction from fund name
_R1_SHORT_WORDS = re.compile(r"\b(SHORT|INVERSE|BEAR|-1X|-2X|-3X)\b", re.IGNORECASE)

# RULE 2 — Multi-asset allocation funds
# Covers iShares Core series (80/20, 60/40, 30/70, ESG aware), Amplius,
# LifePath target date, Trendpilot, risk-parity, HANDL, OCIO, all-weather, etc.
_R2_ALLOC = re.compile(
    r"\b(ALLOCATION|AGGRESSIVE|MODERATE|CONSERVATIVE"
    r"|TARGET[\s-]DATE|LIFEPATH|BALANCED"
    r"|ALL[\s-]WEATHER|RISK[\s-]PARITY|ENDOWMENT|TRINITY"
    r"|REAL RETURN|HANDL|ADAPTIVE CORE|OCIO"
    r"|MULTI[\s-]ASSET|TRENDPILOT|TRENDPILOT\s+\d+"
    r"|CORE BALANCED|FLEXIBLE INCOME)\b",
    re.IGNORECASE,
)
# Also trigger on fraction allocations like 80/20, 60/40, 30/70 in the name
_R2_FRACTION = re.compile(r"\b\d{2}/\d{2}\b")

# RULE 3 — Alternative income / hedged income
_R3_ALT_INCOME = re.compile(
    r"\b(ALTERNATIVE INCOME|ALT[\s-]INCOME|MULTI[\s-]INCOME"
    r"|HIGH INCOME|ALTERNATIVE[\s-]INCOME|YIELDBOOST"
    r"|ALTERNATIVE YIELD|CEF INCOME|HEDGED INCOME)\b",
    re.IGNORECASE,
)

# RULE 4 — Defined outcome residue (protected, max buffer, stop-loss variants)
_R4_DEFOUT = re.compile(
    r"\b(DOWNSIDE PROTECT|MAX BUFFER|RISK[\s-]DEFINED"
    r"|STOP[\s-]LOSS|DEFINED PROTECT|PROTECT[\s-]FLOOR"
    r"|EQUITY DEFINED PROTECT)\b",
    re.IGNORECASE,
)

# RULE 5 — Low-volatility / managed-volatility equity strategy (Plain Beta / Style)
# These are factor ETFs that filter for low vol stocks — not Volatility asset class
_R5_LOW_VOL = re.compile(
    r"\b(LOW[\s-]VOL|MINIMUM[\s-]VOL|MANAGED[\s-]VOL"
    r"|LOW VOLATILITY|MINIMUM VOLATILITY|MANAGED VOLATILITY)\b",
    re.IGNORECASE,
)

# RULE 6 — Alternative / hedge fund strategy (long/short, merger arb, macro, etc.)
_R6_ALTALT = re.compile(
    r"\b(LONG[\s/]SHORT|MERGER[\s-]ARBITRAGE|MERGER ARBITRAGE|MARKET[\s-]NEUTRAL"
    r"|GLOBAL MACRO|MULTI[\s-]STRATEGY|MULTI[\s-]QIS|HEDGE REPLICATION"
    r"|EVENT[\s-]DRIVEN|SHORT STRATEGIES|STYLE PREMIA|LONG\/SHORT"
    r"|ANTI[\s-]BETA|EQUITY HEDGE|LONG ONLINE|DORSEY WRIGHT SHORT"
    r"|SYSTEMATIC ALTERNATIVES|SYNTHEQUITY|EMERALD SPECIAL SITUATIONS"
    r"|HYPE ETF|130/30|FOURTH TURNING|ADAPTIVERISK|GLOBAL FACTOR EQUITY"
    r"|PORTFOLIO PLUS|OPTIONS INCOME|INCOME OPPORTUNITY"
    r"|DEFERRED INCOME|BOX ETF|DYNAMIC US INTEREST RATE)\b",
    re.IGNORECASE,
)

# RULE 7 — Currency / Forex (true currency funds)
_R7_CURRENCY = re.compile(
    r"\b(DOLLAR INDEX|EURO(?!PEAN)|YEN\b|POUND STERLING|POUND ETF|CURRENCY ETF)\b",
    re.IGNORECASE,
)

# RULE 8 — Volatility asset class (true VIX futures / ETP products)
# Only applies when fund_name contains VIX or asset_class_focus is "Specialty"
_R8_VIX = re.compile(r"\bVIX\b", re.IGNORECASE)

# RULE 9 — Residue catch-all patterns for funds that escaped all prior rules
# Covers: VictoryShares volatility-weighted, JPMorgan diversified return,
# INTECH diversified alpha, diversified income (FI), interest rate vol hedge,
# commodity pass-through, diversified factor equity.
_R9_VOL_WTD = re.compile(r"\bVOLATILITY\s+WTD\b", re.IGNORECASE)          # VOLATILITY WTD -> Style
_R9_INTEREST_RATE_VOL = re.compile(
    r"\b(INTEREST RATE VOLATILITY|RATE VOLATILITY|INFLATION HEDGE)\b", re.IGNORECASE
)                                                                             # IRVH, IVOL -> FI Risk Mgmt
_R9_DIVERSIFIED_RETURN = re.compile(
    r"\bDIVERSIFIED RETURN\b", re.IGNORECASE
)                                                                             # JP Morgan factor series -> Style
_R9_DIVERSIFIED_ALPHA = re.compile(
    r"\bDIVERSIFIED ALPHA\b", re.IGNORECASE
)                                                                             # INTECH series -> Style
_R9_DIVERSIFIED_INCOME_FI = re.compile(
    r"\b(DIVERSIFIED INCOME|LIMITED VOLATILITY|DIVERSIFIED DIVIDEND|FREE CASH FLOW)\b",
    re.IGNORECASE,
)                                                                             # BLUI/DUKZ/DVVY/FCFY/DANA
_R9_COMMODITY = re.compile(
    r"\b(COMMODITY|OPTIMUM YIELD)\b", re.IGNORECASE
)                                                                             # PDBC
_R9_TACTICAL_CATCH = re.compile(
    r"\b(TACTICAL|NAVIGATOR|DYNAMIC TACTICAL)\b", re.IGNORECASE
)                                                                             # DYTA (SGI Diversified Tactical)

# ---------------------------------------------------------------------------
# Helper: extract leverage multiplier from fund name
# ---------------------------------------------------------------------------

def _extract_leverage(fund_name: str) -> tuple[float, str]:
    """Return (ratio, direction) from patterns like '2X LONG', '3X SHORT', 'BULL 2X'."""
    name = fund_name.upper()
    m = re.search(r"\b(\d+)X\s+(LONG|SHORT|INVERSE|BULL|BEAR)\b", name)
    if not m:
        m = re.search(r"\b(LONG|SHORT|INVERSE|BULL|BEAR)\s+(\d+)X\b", name)
        if m:
            mult = float(m.group(2))
            word = m.group(1)
        else:
            mult = 1.0
            word = "LONG"
    else:
        mult = float(m.group(1))
        word = m.group(2)
    direction = "short" if word in ("SHORT", "INVERSE", "BEAR") else "long"
    return mult, direction


# ---------------------------------------------------------------------------
# Main classification logic
# ---------------------------------------------------------------------------

def classify(ticker: str, fund_name: str, asset_class_focus: str,
             is_crypto: str, uses_leverage: str,
             etp_category: str) -> dict | None:
    """Apply enhanced rules.  Returns a partial row dict or None (no match)."""
    name = (fund_name or "").upper()
    ac_focus = (asset_class_focus or "").strip()
    specialty = ac_focus == "Specialty"
    mixed_alloc = ac_focus == "Mixed Allocation"
    alternative = ac_focus == "Alternative"
    is_crypto_flag = str(is_crypto or "").strip().lower() in ("true", "yes", "1", "equity long/short")

    row: dict = {
        "wrapper_type": "standalone",
        "mechanism": "physical",
        "source": SOURCE_TAG,
        "notes": NOTES_TAG,
    }

    # --- RULE 1: L&I single-stock 2X / 3X ---------------------------------
    if _R1_LI.search(name):
        mult, direction = _extract_leverage(fund_name)
        row.update(
            asset_class="Equity",
            primary_strategy="L&I",
            sub_strategy="Long" if direction == "long" else "Short",
            leverage_ratio=str(mult),
            direction=direction,
            mechanism="swap",
            reset_period="daily",
            concentration="single",
        )
        return row

    # --- RULE 3: Alternative income / CEF income ---------------------------
    # Checked BEFORE Rule 2 so that "ALTERNATIVE INCOME" in a Mixed Allocation fund
    # is correctly classified as Income rather than falling into the broad Allocation bucket.
    if _R3_ALT_INCOME.search(name):
        # Determine if derivative (yieldboost) or structured product
        if re.search(r"\bYIELDBOOST\b", name, re.IGNORECASE):
            sub = "Derivative Income > Covered Call"
            primary = "Income"
        elif re.search(r"\bCEF\b", name, re.IGNORECASE):
            # CEF arbitrage funds are Risk Mgmt, not plain income
            sub = "CEF Arbitrage"
            primary = "Risk Mgmt"
            row["asset_class"] = "Multi-Asset"
            row["primary_strategy"] = primary
            row["sub_strategy"] = sub
            return row
        else:
            sub = "Alternative Income"
            primary = "Income"
        row.update(
            asset_class="Multi-Asset",
            primary_strategy=primary,
            sub_strategy=sub,
        )
        return row

    # --- RULE 2: Multi-asset allocation ------------------------------------
    if mixed_alloc or _R2_ALLOC.search(name) or _R2_FRACTION.search(name):
        row.update(
            asset_class="Multi-Asset",
            primary_strategy="Plain Beta",
            sub_strategy="Allocation",
            mechanism="physical",
        )
        return row

    # --- RULE 4: Defined outcome residue -----------------------------------
    if _R4_DEFOUT.search(name):
        if re.search(r"\bFLOOR\b", name, re.IGNORECASE):
            sub = "Floor"
        elif re.search(r"\bBUFFER\b", name, re.IGNORECASE):
            sub = "Buffer"
        else:
            sub = "Protected"
        row.update(
            asset_class="Equity",
            primary_strategy="Defined Outcome",
            sub_strategy=sub,
        )
        return row

    # --- RULE 5: Low-volatility equity (factor / style) -------------------
    if _R5_LOW_VOL.search(name):
        # Determine asset class from focus
        ac = {"Equity": "Equity", "Fixed Income": "Fixed Income"}.get(ac_focus, "Equity")
        row.update(
            asset_class=ac,
            primary_strategy="Plain Beta",
            sub_strategy="Style",
        )
        return row

    # --- RULE 8: True VIX / Volatility products ---------------------------
    # Place before Rule 6 so "VIX" names don't fall into Alt bucket
    if specialty or (etp_category or "").upper() in ("VIX", "VOLATILITY"):
        if _R8_VIX.search(name) or specialty:
            row.update(
                asset_class="Volatility",
                primary_strategy="Plain Beta",
                sub_strategy="VIX",
                mechanism="futures",
            )
            return row

    # --- RULE 6: Alternative strategies -----------------------------------
    if _R6_ALTALT.search(name) or alternative:
        # Special cases inside alternatives
        if re.search(r"\bBOX\s+ETF\b", name, re.IGNORECASE):
            row.update(
                asset_class="Fixed Income",
                primary_strategy="Defined Outcome",
                sub_strategy="Box Spread",
            )
            return row
        if re.search(r"\bCRYPTO\b|HYPE ETF|BNB CHAIN|BONK\b", name, re.IGNORECASE) \
                or (etp_category or "").upper() == "CRYPTO":
            row.update(
                asset_class="Crypto",
                primary_strategy="L&I",
                sub_strategy="Long",
                leverage_ratio="2.0",
                direction="long",
                mechanism="swap",
                reset_period="daily",
            )
            return row
        if re.search(r"\bCOMMODITY|OPTIMUM YIELD\b", name, re.IGNORECASE) \
                or ac_focus == "Commodity":
            row.update(
                asset_class="Commodity",
                primary_strategy="Plain Beta",
                sub_strategy="Broad",
            )
            return row
        row.update(
            asset_class="Multi-Asset",
            primary_strategy="Risk Mgmt",
            sub_strategy="Alternatives",
        )
        return row

    # --- RULE 7: Currency --------------------------------------------------
    if _R7_CURRENCY.search(name) or ac_focus == "Currency":
        row.update(
            asset_class="Currency",
            primary_strategy="Plain Beta",
            sub_strategy="Single-Currency",
        )
        return row

    # --- RULE 9: Residue catch-all patterns --------------------------------
    # Applied in specificity order; these handle the 20 hard cases that
    # escaped all prior rules.

    # 9a — True VIX-futures-style volatility-weighted equity (CDC/CDL/CFA series)
    # These use volatility weighting to build an equity portfolio — Plain Beta / Style
    if _R9_VOL_WTD.search(name):
        row.update(
            asset_class="Equity",
            primary_strategy="Plain Beta",
            sub_strategy="Style",
        )
        return row

    # 9b — Interest rate volatility + inflation hedge (IRVH, IVOL)
    # These are Fixed Income funds with options overlay as a hedge
    if _R9_INTEREST_RATE_VOL.search(name):
        row.update(
            asset_class="Fixed Income",
            primary_strategy="Risk Mgmt",
            sub_strategy="Hedged Equity",
        )
        return row

    # 9c — JPMorgan Diversified Return / INTECH Diversified Alpha
    # Factor-tilted equity index ETFs — Plain Beta / Style
    if _R9_DIVERSIFIED_RETURN.search(name) or _R9_DIVERSIFIED_ALPHA.search(name):
        row.update(
            asset_class="Equity",
            primary_strategy="Plain Beta",
            sub_strategy="Style",
        )
        return row

    # 9d — Commodity pass-through (PDBC and similar)
    if _R9_COMMODITY.search(name) or ac_focus == "Commodity":
        row.update(
            asset_class="Commodity",
            primary_strategy="Plain Beta",
            sub_strategy="Broad",
        )
        return row

    # 9e — Diversified income / dividend equity or fixed income plain beta
    if _R9_DIVERSIFIED_INCOME_FI.search(name):
        if ac_focus == "Fixed Income":
            row.update(
                asset_class="Fixed Income",
                primary_strategy="Plain Beta",
                sub_strategy="Broad",
            )
        else:
            # Equity income / dividend diversified
            row.update(
                asset_class="Equity",
                primary_strategy="Plain Beta",
                sub_strategy="Style",
            )
        return row

    # 9f — Tactical / dynamic tactical (SGI DYTA and similar)
    if _R9_TACTICAL_CATCH.search(name):
        row.update(
            asset_class="Multi-Asset",
            primary_strategy="Risk Mgmt",
            sub_strategy="Risk-Adaptive",
        )
        return row

    # --- No match ----------------------------------------------------------
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    # Load existing master tickers to avoid duplicating rows
    existing_tickers: set[str] = set()
    if MASTER_CSV.exists():
        with MASTER_CSV.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                t = (row.get("ticker") or "").strip()
                if t:
                    existing_tickers.add(t)
    print(f"Existing fund_master.csv tickers : {len(existing_tickers):,}")

    # Pull all ACTV funds with NULL primary_strategy from DB
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, issuer, issuer_display,
               asset_class_focus, is_crypto, uses_leverage,
               etp_category, inception_date
        FROM mkt_master_data
        WHERE market_status='ACTV' AND primary_strategy IS NULL
    """)
    db_rows = cur.fetchall()
    con.close()

    # Filter to those not yet in master CSV
    candidates = [r for r in db_rows if (r["ticker"] or "").strip() not in existing_tickers]
    print(f"NULL primary_strategy ACTV funds: {len(db_rows):,}")
    print(f"Not yet in fund_master.csv        : {len(candidates):,}")
    print()

    # Classify
    rule_counts: dict[str, int] = {
        "Rule1_LI": 0,
        "Rule2_Alloc": 0,
        "Rule3_AltIncome": 0,
        "Rule4_DefOut": 0,
        "Rule5_LowVol": 0,
        "Rule6_AltStrat": 0,
        "Rule7_Currency": 0,
        "Rule8_Vix": 0,
        "Rule9_Residue_Catch": 0,
        "Residue": 0,
    }

    classified_rows: list[dict] = []
    residue_rows: list[dict] = []

    for r in candidates:
        ticker = (r["ticker"] or "").strip()
        if not ticker:
            continue

        fund_name      = r["fund_name"] or ""
        issuer_display = r["issuer_display"] or r["issuer"] or ""
        ac_focus       = r["asset_class_focus"] or ""
        is_crypto      = r["is_crypto"] or ""
        uses_leverage  = r["uses_leverage"] or ""
        etp_category   = r["etp_category"] or ""

        result = classify(ticker, fund_name, ac_focus, is_crypto, uses_leverage, etp_category)

        if result is None:
            rule_counts["Residue"] += 1
            residue_rows.append({
                "ticker": ticker,
                "fund_name": fund_name,
                "etp_category": etp_category,
                "asset_class_focus": ac_focus,
                "why_skipped": "No pattern matched any of the 8 enhanced rules",
            })
            continue

        # Determine which rule fired based on primary_strategy + sub_strategy
        ps = result.get("primary_strategy", "")
        ss = result.get("sub_strategy", "")
        ac = result.get("asset_class", "")
        if ps == "L&I" and ac == "Equity":
            rule_counts["Rule1_LI"] += 1
        elif ps == "Plain Beta" and ss == "Allocation":
            rule_counts["Rule2_Alloc"] += 1
        elif ps in ("Income", "Risk Mgmt") and ss in ("Alternative Income", "Derivative Income > Covered Call", "CEF Arbitrage"):
            rule_counts["Rule3_AltIncome"] += 1
        elif ps == "Defined Outcome" and ac == "Equity":
            rule_counts["Rule4_DefOut"] += 1
        elif ps == "Plain Beta" and ss == "Style":
            rule_counts["Rule5_LowVol"] += 1
        elif ac == "Volatility":
            rule_counts["Rule8_Vix"] += 1
        elif ps == "Risk Mgmt" and ss == "Alternatives":
            rule_counts["Rule6_AltStrat"] += 1
        elif ps == "Currency" or ac == "Currency":
            rule_counts["Rule7_Currency"] += 1
        elif ps == "L&I" and ac == "Crypto":
            rule_counts["Rule6_AltStrat"] += 1  # crypto 2x counted in alt strat bucket
        elif ps == "Defined Outcome" and ss == "Box Spread":
            rule_counts["Rule6_AltStrat"] += 1
        elif ps == "Plain Beta" and ac == "Commodity":
            # Could be Rule 6 (alt strat) or Rule 9 (commodity catch)
            # Distinguish: Rule 6 only fires when ac_focus=Alternative; Rule 9 fires on commodity keywords
            rule_counts["Rule9_Residue_Catch"] += 1
        elif ps == "Risk Mgmt" and ss in ("Hedged Equity", "Risk-Adaptive") and ac in ("Fixed Income", "Multi-Asset"):
            rule_counts["Rule9_Residue_Catch"] += 1
        elif ps == "Plain Beta" and ss == "Broad" and ac == "Fixed Income":
            rule_counts["Rule9_Residue_Catch"] += 1
        else:
            rule_counts["Rule6_AltStrat"] += 1

        # Build full CSV row
        csv_row: dict = {}
        for field in FIELD_ORDER:
            csv_row[field] = ""
        csv_row["ticker"]      = ticker
        csv_row["fund_name"]   = fund_name
        csv_row["issuer_brand"] = issuer_display
        csv_row.update(result)
        classified_rows.append(csv_row)

    # Append classified rows to fund_master.csv
    if classified_rows:
        with MASTER_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELD_ORDER, extrasaction="ignore")
            for row in classified_rows:
                w.writerow(row)
        print(f"Appended {len(classified_rows):,} rows to {MASTER_CSV}")
    else:
        print("No new rows to append.")

    # Write residue CSV
    residue_fields = ["ticker", "fund_name", "etp_category", "asset_class_focus", "why_skipped"]
    with RESIDUE_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=residue_fields)
        w.writeheader()
        w.writerows(residue_rows)
    print(f"Residue written  : {len(residue_rows):,} rows -> {RESIDUE_CSV}")

    # Summary
    print()
    print("=== Classification Summary ===")
    total_resolved = sum(v for k, v in rule_counts.items() if k != "Residue")
    print(f"  Rule 1 — L&I single-stock 2X/3X   : {rule_counts['Rule1_LI']:4d}")
    print(f"  Rule 2 — Multi-asset allocation    : {rule_counts['Rule2_Alloc']:4d}")
    print(f"  Rule 3 — Alt income / CEF          : {rule_counts['Rule3_AltIncome']:4d}")
    print(f"  Rule 4 — Defined outcome residue   : {rule_counts['Rule4_DefOut']:4d}")
    print(f"  Rule 5 — Low-vol / managed-vol     : {rule_counts['Rule5_LowVol']:4d}")
    print(f"  Rule 6 — Alternative strategies    : {rule_counts['Rule6_AltStrat']:4d}")
    print(f"  Rule 7 — Currency / Forex          : {rule_counts['Rule7_Currency']:4d}")
    print(f"  Rule 8 — VIX / Volatility          : {rule_counts['Rule8_Vix']:4d}")
    print(f"  Rule 9 — Residue catch-all         : {rule_counts['Rule9_Residue_Catch']:4d}")
    print(f"  ----------------------------------")
    print(f"  Resolved                           : {total_resolved:4d}")
    print(f"  Residue (unmatched)                : {rule_counts['Residue']:4d}")
    print(f"  Total candidates                   : {len(candidates):4d}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
