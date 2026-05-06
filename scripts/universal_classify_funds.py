"""Universal Fund Classifier — classify ALL market_status values.

Extends classify_remaining_funds.py (Phase 5 enhanced rules) to cover every
fund in mkt_master_data regardless of market_status (ACTV, PEND, LIQU, INAC,
ACQU, DLST, EXPD, TKCH, UNLS, PRNA, HANP, None).

This enables "Funds in Pipeline" reporting on PEND/DELAYED funds before
they start trading — the pre-launch alpha window.

Output:
  - APPENDS new rows to config/rules/fund_master.csv  (idempotent — skips
    tickers already present in that file)
  - Writes docs/classification_residue_2026-05-06.csv for unmatched funds

Run: python scripts/universal_classify_funds.py

Apply step: python scripts/apply_fund_master.py
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / "data" / "etp_tracker.db"
MASTER_CSV   = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"
RESIDUE_CSV  = PROJECT_ROOT / "docs" / "classification_residue_2026-05-06.csv"

SOURCE_TAG = "universal-classify-2026-05-06"
NOTES_TAG  = "Backfilled by universal classifier (all market_status)"

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
# Same rules as classify_remaining_funds.py (Phase 5) — kept in sync.
# ---------------------------------------------------------------------------

# RULE 1 — L&I single-stock 2X / 3X
_R1_LI = re.compile(
    r"\b(\d+)X\s+(LONG|SHORT|INVERSE|BULL|BEAR)\s+([A-Z]{1,5})\b"
    r"|"
    r"\b(LONG|SHORT|INVERSE|BULL|BEAR)\s+(\d+)X\b",
    re.IGNORECASE,
)
_R1_SHORT_WORDS = re.compile(r"\b(SHORT|INVERSE|BEAR|-1X|-2X|-3X)\b", re.IGNORECASE)

# RULE 2 — Multi-asset allocation
_R2_ALLOC = re.compile(
    r"\b(ALLOCATION|AGGRESSIVE|MODERATE|CONSERVATIVE"
    r"|TARGET[\s-]DATE|LIFEPATH|BALANCED"
    r"|ALL[\s-]WEATHER|RISK[\s-]PARITY|ENDOWMENT|TRINITY"
    r"|REAL RETURN|HANDL|ADAPTIVE CORE|OCIO"
    r"|MULTI[\s-]ASSET|TRENDPILOT|TRENDPILOT\s+\d+"
    r"|CORE BALANCED|FLEXIBLE INCOME)\b",
    re.IGNORECASE,
)
_R2_FRACTION = re.compile(r"\b\d{2}/\d{2}\b")

# RULE 3 — Alternative income / hedged income
_R3_ALT_INCOME = re.compile(
    r"\b(ALTERNATIVE INCOME|ALT[\s-]INCOME|MULTI[\s-]INCOME"
    r"|HIGH INCOME|ALTERNATIVE[\s-]INCOME|YIELDBOOST"
    r"|ALTERNATIVE YIELD|CEF INCOME|HEDGED INCOME)\b",
    re.IGNORECASE,
)

# RULE 4 — Defined outcome residue
_R4_DEFOUT = re.compile(
    r"\b(DOWNSIDE PROTECT|MAX BUFFER|RISK[\s-]DEFINED"
    r"|STOP[\s-]LOSS|DEFINED PROTECT|PROTECT[\s-]FLOOR"
    r"|EQUITY DEFINED PROTECT)\b",
    re.IGNORECASE,
)

# RULE 5 — Low-volatility / managed-volatility equity
_R5_LOW_VOL = re.compile(
    r"\b(LOW[\s-]VOL|MINIMUM[\s-]VOL|MANAGED[\s-]VOL"
    r"|LOW VOLATILITY|MINIMUM VOLATILITY|MANAGED VOLATILITY)\b",
    re.IGNORECASE,
)

# RULE 6 — Alternative strategies
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

# RULE 7 — Currency / Forex
_R7_CURRENCY = re.compile(
    r"\b(DOLLAR INDEX|EURO(?!PEAN)|YEN\b|POUND STERLING|POUND ETF|CURRENCY ETF)\b",
    re.IGNORECASE,
)

# RULE 8 — Volatility asset class (true VIX futures)
_R8_VIX = re.compile(r"\bVIX\b", re.IGNORECASE)

# RULE 9 — Residue catch-all patterns
_R9_VOL_WTD = re.compile(r"\bVOLATILITY\s+WTD\b", re.IGNORECASE)
_R9_INTEREST_RATE_VOL = re.compile(
    r"\b(INTEREST RATE VOLATILITY|RATE VOLATILITY|INFLATION HEDGE)\b", re.IGNORECASE
)
_R9_DIVERSIFIED_RETURN = re.compile(r"\bDIVERSIFIED RETURN\b", re.IGNORECASE)
_R9_DIVERSIFIED_ALPHA = re.compile(r"\bDIVERSIFIED ALPHA\b", re.IGNORECASE)
_R9_DIVERSIFIED_INCOME_FI = re.compile(
    r"\b(DIVERSIFIED INCOME|LIMITED VOLATILITY|DIVERSIFIED DIVIDEND|FREE CASH FLOW)\b",
    re.IGNORECASE,
)
_R9_COMMODITY = re.compile(r"\b(COMMODITY|OPTIMUM YIELD)\b", re.IGNORECASE)
_R9_TACTICAL_CATCH = re.compile(
    r"\b(TACTICAL|NAVIGATOR|DYNAMIC TACTICAL)\b", re.IGNORECASE
)

# RULE 10 — Keyword fallback (backfill_unclassified_funds.py patterns)
# Applied last — catches funds with no name-pattern match but clear BBG signals.
_ASSET_KEYWORDS = [
    (re.compile(r"\b(BITCOIN|ETHEREUM|CRYPTO|BTC|ETH|SOLANA|DOGECOIN|POLKADOT|SUI|CARDANO|XRP|CHAINLINK|STELLAR|AVALANCHE)\b", re.IGNORECASE), "Crypto"),
    (re.compile(r"\b(GOLD|SILVER|COPPER|PLATINUM|PALLADIUM|OIL|GAS|COMMODIT|MINERALS|MINING)\b", re.IGNORECASE), "Commodity"),
    (re.compile(r"\b(BOND|TREASUR|MUNI|FIXED INCOME|CREDIT|YIELD CURVE|AGGREGATE|TIPS|FLOAT(?:ING)?\s*RATE|HIGH YIELD|JUNK|INVESTMENT GRADE)\b", re.IGNORECASE), "Fixed Income"),
    (re.compile(r"\b(VOLATILITY|VIX|VOL\s+TARGET)\b", re.IGNORECASE), "Volatility"),
    (re.compile(r"\b(CURRENCY|DOLLAR|EURO|YEN|POUND|FX)\b", re.IGNORECASE), "Currency"),
    (re.compile(r"\b(MULTI[\s-]ASSET|TARGET[\s-]DATE|RISK[\s-]PARITY|BALANCED|ALLOCATION|DIVERSIFIED)\b", re.IGNORECASE), "Multi-Asset"),
]
_STRATEGY_KEYWORDS = [
    (re.compile(r"\b(2X|3X|-1X|-2X|-3X)\s*(LONG|SHORT|INVERSE|BULL|BEAR)\b", re.IGNORECASE), ("L&I", None)),
    (re.compile(r"\b(LEVERAG|INVERSE|BEAR\s+ETF|BULL\s+ETF)\b", re.IGNORECASE), ("L&I", None)),
    (re.compile(r"\b(AUTOCALL)\b", re.IGNORECASE), ("Income", "Structured Product Income > Autocallable")),
    (re.compile(r"\b(COVERED\s+CALL|BUY[-\s]?WRITE|YIELDMAX|YIELDBOOST|PREMIUM\s+INCOME|0DTE|ODTE)\b", re.IGNORECASE), ("Income", "Derivative Income > Covered Call")),
    (re.compile(r"\b(PUT[-\s]?WRITE)\b", re.IGNORECASE), ("Income", "Derivative Income > Put-Write")),
    (re.compile(r"\b(WEEKLY\s*PAY|WEEKLY\s*DISTRIBUTION)\b", re.IGNORECASE), ("Income", "Derivative Income > 0DTE / Weekly-Pay")),
    (re.compile(r"\b(EQUITY\s+LINKED\s+(?:HIGH|MODERATE|AGGRESSIVE)\s+INCOME)\b", re.IGNORECASE), ("Income", "Structured Product Income > Autocallable")),
    (re.compile(r"\b(TARGET\s+\d+%?\s+INCOME)\b", re.IGNORECASE), ("Income", "Derivative Income > Covered Call")),
    (re.compile(r"\b(BUFFER|BARRIER\s+OUTCOME)\b", re.IGNORECASE), ("Defined Outcome", "Buffer")),
    (re.compile(r"\b(FLOOR\s+ETF|MAX\s+LOSS)\b", re.IGNORECASE), ("Defined Outcome", "Floor")),
    (re.compile(r"\b(ACCELERAT|UNCAPPED\s+ACCELERAT)\b", re.IGNORECASE), ("Defined Outcome", "Growth")),
    (re.compile(r"\b(DUAL\s+DIRECTIONAL)\b", re.IGNORECASE), ("Defined Outcome", "Dual Directional")),
    (re.compile(r"\b(TAX[-\s]?AWARE\s+COLLATERAL|BOX\s+SPREAD)\b", re.IGNORECASE), ("Defined Outcome", "Box Spread")),
    (re.compile(r"\b(HEDGED\s+EQUITY)\b", re.IGNORECASE), ("Risk Mgmt", "Hedged Equity")),
    (re.compile(r"\b(RISK[-\s]?ADAPTIVE|ADAPTIVE\s+RISK|RISK[-\s]?MANAGED)\b", re.IGNORECASE), ("Risk Mgmt", "Risk-Adaptive")),
    (re.compile(r"\b(MANAGED\s+FUTURES|TREND\s+FOLLOWING|CTA\b)\b", re.IGNORECASE), ("Risk Mgmt", "Trend / Managed Futures")),
    (re.compile(r"\b(SECTOR|TECHNOLOGY\s+SELECT|HEALTHCARE\s+SELECT|FINANCIAL\s+SELECT|ENERGY\s+SELECT|REIT|REAL\s+ESTATE)\b", re.IGNORECASE), ("Plain Beta", "Sector")),
    (re.compile(r"\b(AI\b|ARTIFICIAL\s+INTELLIGENCE|ROBOTICS|SPACE|DEFENSE|DRONE|GENOMICS|CLEAN\s+ENERGY|EV\b)\b", re.IGNORECASE), ("Plain Beta", "Thematic")),
    (re.compile(r"\b(VALUE|GROWTH|QUALITY|MOMENTUM|LOW[-\s]?VOL|DIVIDEND\s+ARISTOCRATS|HIGH\s+DIVIDEND)\b", re.IGNORECASE), ("Plain Beta", "Style")),
    (re.compile(r"\b(EMERGING\s+MARKETS|DEVELOPED\s+MARKETS|EAFE|EUROPE|ASIA|JAPAN|CHINA|INDIA|EX[-\s]?US)\b", re.IGNORECASE), ("Plain Beta", "Broad")),
]


# ---------------------------------------------------------------------------
# Helper: extract leverage multiplier from fund name
# ---------------------------------------------------------------------------

def _extract_leverage(fund_name: str) -> tuple[float, str]:
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
# Main classification logic (Phase 5 enhanced rules + keyword fallback)
# ---------------------------------------------------------------------------

def classify(ticker: str, fund_name: str, asset_class_focus: str,
             is_crypto: str, uses_leverage: str,
             etp_category: str) -> dict | None:
    """Apply enhanced rules then keyword fallback. Returns partial row dict or None."""
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
    if _R3_ALT_INCOME.search(name):
        if re.search(r"\bYIELDBOOST\b", name, re.IGNORECASE):
            sub = "Derivative Income > Covered Call"
            primary = "Income"
        elif re.search(r"\bCEF\b", name, re.IGNORECASE):
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

    # --- RULE 5: Low-volatility equity ------------------------------------
    if _R5_LOW_VOL.search(name):
        ac = {"Equity": "Equity", "Fixed Income": "Fixed Income"}.get(ac_focus, "Equity")
        row.update(
            asset_class=ac,
            primary_strategy="Plain Beta",
            sub_strategy="Style",
        )
        return row

    # --- RULE 8: True VIX / Volatility products ---------------------------
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
    if _R9_VOL_WTD.search(name):
        row.update(asset_class="Equity", primary_strategy="Plain Beta", sub_strategy="Style")
        return row

    if _R9_INTEREST_RATE_VOL.search(name):
        row.update(asset_class="Fixed Income", primary_strategy="Risk Mgmt", sub_strategy="Hedged Equity")
        return row

    if _R9_DIVERSIFIED_RETURN.search(name) or _R9_DIVERSIFIED_ALPHA.search(name):
        row.update(asset_class="Equity", primary_strategy="Plain Beta", sub_strategy="Style")
        return row

    if _R9_COMMODITY.search(name) or ac_focus == "Commodity":
        row.update(asset_class="Commodity", primary_strategy="Plain Beta", sub_strategy="Broad")
        return row

    if _R9_DIVERSIFIED_INCOME_FI.search(name):
        if ac_focus == "Fixed Income":
            row.update(asset_class="Fixed Income", primary_strategy="Plain Beta", sub_strategy="Broad")
        else:
            row.update(asset_class="Equity", primary_strategy="Plain Beta", sub_strategy="Style")
        return row

    if _R9_TACTICAL_CATCH.search(name):
        row.update(asset_class="Multi-Asset", primary_strategy="Risk Mgmt", sub_strategy="Risk-Adaptive")
        return row

    # --- RULE 10: Keyword fallback (handles funds with broad/generic names) ---
    # Determine asset class from keywords then BBG fallback
    detected_ac = ""
    for pat, ac in _ASSET_KEYWORDS:
        if pat.search(name):
            detected_ac = ac
            break
    if not detected_ac:
        if is_crypto_flag:
            detected_ac = "Crypto"
        else:
            focus_map = {
                "Equity": "Equity", "Fixed Income": "Fixed Income",
                "Commodity": "Commodity", "Currency": "Currency",
                "Mixed Allocation": "Multi-Asset", "Alternative": "Multi-Asset",
            }
            detected_ac = focus_map.get(ac_focus, "")

    if detected_ac:
        # Strategy from keywords
        for pat, (primary, sub) in _STRATEGY_KEYWORDS:
            if pat.search(name):
                if sub is None:
                    if "SHORT" in name or "INVERSE" in name or "BEAR" in name:
                        sub = "Short"
                    else:
                        sub = "Long"
                row.update(asset_class=detected_ac, primary_strategy=primary, sub_strategy=sub)
                return row
        # No strategy keyword — default Plain Beta / Broad for well-known asset classes
        if detected_ac in ("Equity", "Fixed Income", "Commodity", "Currency", "Crypto", "Multi-Asset"):
            sub = "Broad"
            row.update(asset_class=detected_ac, primary_strategy="Plain Beta", sub_strategy=sub)
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

    # Pull ALL funds with NULL primary_strategy (no market_status filter)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, issuer, issuer_display,
               asset_class_focus, is_crypto, uses_leverage,
               etp_category, inception_date, market_status
        FROM mkt_master_data
        WHERE primary_strategy IS NULL
    """)
    db_rows = cur.fetchall()
    con.close()

    # Count by status BEFORE filtering
    by_status_total: Counter = Counter()
    for r in db_rows:
        by_status_total[r["market_status"] or "NULL"] += 1

    # Filter to those not yet in master CSV
    candidates = [r for r in db_rows if (r["ticker"] or "").strip() not in existing_tickers]
    print(f"NULL primary_strategy (all statuses): {len(db_rows):,}")
    print(f"Not yet in fund_master.csv           : {len(candidates):,}")
    print()
    print("Breakdown by market_status (candidates):")
    by_status_cands: Counter = Counter()
    for r in candidates:
        by_status_cands[r["market_status"] or "NULL"] += 1
    for status in sorted(by_status_cands.keys(), key=lambda x: by_status_cands[x], reverse=True):
        print(f"  {status:<8}: {by_status_cands[status]:,}")
    print()

    # Classify
    classified_rows: list[dict] = []
    residue_rows: list[dict] = []
    by_status_classified: Counter = Counter()
    by_status_residue: Counter = Counter()

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
        mkt_status     = r["market_status"] or "NULL"

        result = classify(ticker, fund_name, ac_focus, is_crypto, uses_leverage, etp_category)

        if result is None:
            by_status_residue[mkt_status] += 1
            residue_rows.append({
                "ticker": ticker,
                "fund_name": fund_name,
                "market_status": mkt_status,
                "etp_category": etp_category,
                "asset_class_focus": ac_focus,
                "why_skipped": "No pattern matched any rule",
            })
            continue

        by_status_classified[mkt_status] += 1

        # Build full CSV row
        csv_row: dict = {field: "" for field in FIELD_ORDER}
        csv_row["ticker"]       = ticker
        csv_row["fund_name"]    = fund_name
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
    residue_fields = ["ticker", "fund_name", "market_status", "etp_category", "asset_class_focus", "why_skipped"]
    RESIDUE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESIDUE_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=residue_fields)
        w.writeheader()
        w.writerows(residue_rows)
    print(f"Residue written  : {len(residue_rows):,} rows -> {RESIDUE_CSV}")

    # Summary
    print()
    print("=== Per-Status Classification Results ===")
    print(f"{'Status':<10} {'Classified':>12} {'Residue':>10} {'Total':>8}")
    print("-" * 45)
    all_statuses = sorted(
        set(list(by_status_classified.keys()) + list(by_status_residue.keys())),
        key=lambda x: by_status_classified[x], reverse=True
    )
    total_classified = 0
    total_residue = 0
    for s in all_statuses:
        c = by_status_classified[s]
        res = by_status_residue[s]
        total_classified += c
        total_residue += res
        print(f"  {s:<10} {c:>10,} {res:>10,} {c+res:>8,}")
    print("-" * 45)
    print(f"  {'TOTAL':<10} {total_classified:>10,} {total_residue:>10,} {total_classified+total_residue:>8,}")
    print()
    print(f"Total new rows appended to fund_master.csv: {len(classified_rows):,}")
    print(f"Total unmatched (residue)                 : {len(residue_rows):,}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
