"""Phase 6 — Defined Outcome attribute backfill.

Extracts cap_pct, buffer_pct, outcome_period_months from fund_name
using Strategy A (regex) and Strategy B (issuer-specific knowledge),
then appends override rows to config/rules/fund_master.csv.

Run locally only. Idempotent — existing rows are updated (not duplicated).
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
FUND_MASTER_CSV = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"

# ---------------------------------------------------------------------------
# Strategy B — issuer-specific knowledge tables
# ---------------------------------------------------------------------------

# Innovator product line → buffer_pct
INNOVATOR_BUFFER_SERIES: dict[str, float] = {
    "POWER BUFFER": 9.0,
    "ULTRA BUFFER": 30.0,
    "MANAGED 100 BUFFER": 100.0,
    "DEFINED WEALTH SHIELD": 20.0,  # ~20% managed floor
    "LADDERED ALLOCATION POWER BUFFER": 9.0,
    "LADDERED ALLOCATION BUFFER": 9.0,
}

# FT Vest product line naming conventions
FT_VEST_BUFFER_SERIES: dict[str, float] = {
    "MODERATE BUFFER": 15.0,
    "DEEP BUFFER": 30.0,   # -5% to -35% range = 30% buffer
    "MAX BUFFER": 100.0,
    "CONSERVATIVE BUFFER": 10.0,
    "BUFFER & DIGITAL RETURN": 15.0,
    "BUFFER & PREMIUM INCOME": 15.0,
    "ENHANCE & MODERATE BUFFER": 15.0,
    "DUAL DIRECTIONAL BUFFER": 15.0,
    "QUARTERLY BUFFER": 10.0,
    " BUFFER ETF": 15.0,   # plain "BUFFER ETF" with no qualifier = standard = 15%
}
FT_VEST_FLOOR_SERIES: dict[str, float] = {
    "FLOOR15": 15.0,
}

# Allianz (AllianzIM) — buffer name includes the number directly (BUFFER10, BUFFER15, etc.)
# These are already caught by regex A; handled here as fallback for edge cases
ALLIANZ_SPECIAL: dict[str, float] = {
    "BUFFER100 PROTECTION": 100.0,
    "FLOOR5": 5.0,
    "6 MONTH FLOOR5": 5.0,
    "6 MONTH BUFFER10": 10.0,
}

# Calamos structured alt protection — buffer is 100% by design (100% downside protection)
# "80 SERIES" = 80% protection (20% buffer from top), "90 SERIES" = 90% protection
CALAMOS_PROTECTION: dict[str, float] = {
    "100% PROTECTION": 100.0,
    "90% PROTECTION": 90.0,
    "80% PROTECTION": 80.0,
    "90 SERIES": 90.0,
    "80 SERIES": 80.0,
    "STRUCTURED ALT PROTECTION ETF - ": 100.0,   # base Calamos = 100%
    "STRUCTURED ALT PROTECTION ETF": 100.0,
}

# PGIM (Prudential) series
PGIM_BUFFER: dict[str, float] = {
    "BUFFER 12 ETF": 12.0,
    "BUFFER 20 ETF": 20.0,
    "MAX BUFFER ETF": 100.0,
}
PGIM_OUTCOME_MONTHS: dict[str, int] = {
    "BUFFER 12 ETF": 12,
    "BUFFER 20 ETF": 12,
    "MAX BUFFER ETF": 12,
}

# Pacer SWAN SOS series — buffer depends on variant
PACER_BUFFER: dict[str, float] = {
    "CONSERVATIVE": 10.0,
    "MODERATE": 20.0,
    "FLEX": 30.0,
}

# iShares (BlackRock)
ISHARES_BUFFER: dict[str, float] = {
    "MAX BUFFER": 100.0,
    "DEEP QUARTERLY LADDERED": 30.0,
    "MODERATE QUARTERLY LADDERED": 15.0,
}

# Innovator dual-directional and special series
INNOVATOR_DUAL_BUFFER: dict[str, float] = {
    "DUAL DIRECTIONAL 5 BUFFER": 5.0,
    "DUAL DIRECTIONAL 10 BUFFER": 10.0,
    "DUAL DIRECTIONAL 15 BUFFER": 15.0,
    "10 BUFFER ETF": 10.0,
}

# Month name to 3-letter abbreviation for start_month
MONTH_NAMES: dict[str, str] = {
    "JANUARY": "JAN", "FEBRUARY": "FEB", "MARCH": "MAR",
    "APRIL": "APR", "MAY": "MAY", "JUNE": "JUN",
    "JULY": "JUL", "AUGUST": "AUG", "SEPTEMBER": "SEP",
    "OCTOBER": "OCT", "NOVEMBER": "NOV", "DECEMBER": "DEC",
}
MONTH_ABBR = {v: v for v in MONTH_NAMES.values()}  # already 3-letter
ALL_MONTHS = {**MONTH_NAMES, **MONTH_ABBR}

# Outcome period patterns
SIX_MONTH_KEYWORDS = ["6 MONTH", "6MO", "6-MONTH", "QUARTERLY", "SOS "]
TWO_YEAR_KEYWORDS = ["2 YR TO", "2 YEAR", "24 MONTH", "24-MONTH"]


# ---------------------------------------------------------------------------
# Strategy A — Regex extraction
# ---------------------------------------------------------------------------

def regex_extract_buffer(name_upper: str) -> Optional[float]:
    """Extract buffer percentage from fund name using regex patterns."""
    # Pattern: BUFFER<N> or BUFFER <N> or BUFFER-<N>
    m = re.search(r'BUFFER\s*[-]?\s*(\d+(?:\.\d+)?)\b', name_upper)
    if m:
        return float(m.group(1))
    # Pattern: <N> BUFFER (e.g., "9 BUFFER ETF")
    m = re.search(r'\b(\d+(?:\.\d+)?)\s+BUFFER\b', name_upper)
    if m:
        val = float(m.group(1))
        # Sanity check: buffer % should be between 1 and 100
        if 1 <= val <= 100:
            return val
    # Pattern: FLOOR<N> (floor products have a floor, not a buffer, but populate buffer_pct)
    m = re.search(r'FLOOR\s*(\d+(?:\.\d+)?)\b', name_upper)
    if m:
        return float(m.group(1))
    # Pattern: <N>% PROTECTION or PROTECTION <N>%
    m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*PROTECTION', name_upper)
    if m:
        return float(m.group(1))
    m = re.search(r'PROTECTION\s*[-]?\s*(\d+(?:\.\d+)?)', name_upper)
    if m:
        return float(m.group(1))
    return None


def regex_extract_cap(name_upper: str) -> Optional[float]:
    """Extract cap percentage from fund name using regex patterns."""
    m = re.search(r'CAP(?:PED)?(?:\s+AT)?\s*(\d+(?:\.\d+)?)\s*%?', name_upper)
    if m:
        return float(m.group(1))
    return None


def regex_extract_outcome_months(name_upper: str) -> Optional[int]:
    """Extract outcome period in months from fund name."""
    # 2-year / 24-month variants
    for kw in TWO_YEAR_KEYWORDS:
        if kw in name_upper:
            return 24
    # 6-month variants
    for kw in SIX_MONTH_KEYWORDS:
        if kw in name_upper:
            return 6
    # Explicit N MONTH
    m = re.search(r'\b(\d+)\s*MONTH\b', name_upper)
    if m:
        return int(m.group(1))
    # Default for most named-month series = 12 months
    # We signal this via the issuer-specific logic below
    return None


# ---------------------------------------------------------------------------
# Strategy B — Issuer-specific extraction
# ---------------------------------------------------------------------------

def issuer_extract(
    ticker: str,
    name_upper: str,
    issuer: Optional[str],
    category: Optional[str],
) -> dict:
    """Apply issuer-specific knowledge to extract attributes."""
    result: dict = {}

    issuer = (issuer or "").upper()
    category = (category or "").upper()

    # ---- Innovator --------------------------------------------------------
    if "INNOVATOR" in name_upper:
        # Dual directional series
        for pattern, buf in INNOVATOR_DUAL_BUFFER.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        # Power Buffer series
        for pattern, buf in INNOVATOR_BUFFER_SERIES.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        # DEFINED PROTECTION ETF = 100% downside protection (no buffer loss)
        if "DEFINED PROTECTION ETF" in name_upper and "buffer_pct" not in result:
            result.setdefault("buffer_pct", 100.0)
        # Barrier products — use barrier_pct not buffer_pct
        if "BARRIER ETF" in name_upper:
            m_bar = re.search(r'(\d+)\s*BARRIER', name_upper)
            if m_bar:
                result.setdefault("barrier_pct", float(m_bar.group(1)))
            # Barriers are not buffers; remove any buffer assignment
            result.pop("buffer_pct", None)
        # Floor products — populate buffer_pct from floor
        if "FLOOR ETF" in name_upper or "MANAGED FLOOR" in name_upper or "UNCAPPED BITCOIN" in name_upper:
            m_fl = re.search(r'(\d+)\s*FLOOR', name_upper)
            if m_fl:
                result.setdefault("buffer_pct", float(m_fl.group(1)))
            elif "MANAGED FLOOR" in name_upper:
                result.setdefault("buffer_pct", 10.0)  # Innovator managed floor typical
        # U.S. EQUITY BUFFER ETF / PREMIUM INCOME BUFFER = Power Buffer = 9%
        if "U.S. EQUITY BUFFER ETF" in name_upper and "buffer_pct" not in result:
            result.setdefault("buffer_pct", 9.0)
        if "PREMIUM INCOME" in name_upper and "BUFFER" in name_upper and "buffer_pct" not in result:
            result.setdefault("buffer_pct", 15.0)
        # Treasury Bond 9 Buffer / 5 Floor
        if "TREASURY BOND" in name_upper:
            m_tb = re.search(r'(\d+)\s*(?:BUFFER|FLOOR)', name_upper)
            if m_tb:
                result.setdefault("buffer_pct", float(m_tb.group(1)))
        # Outcome period
        if "6 MO " in name_upper or "6-MO" in name_upper or "6 MO\t" in name_upper:
            result.setdefault("outcome_period_months", 6)
        elif "QUARTERLY" in name_upper:
            result.setdefault("outcome_period_months", 3)
        elif "2 YR" in name_upper or "2YR" in name_upper:
            result.setdefault("outcome_period_months", 24)
        elif "1 YR" in name_upper:
            result.setdefault("outcome_period_months", 12)
        elif any(m in name_upper for m in ALL_MONTHS):
            result.setdefault("outcome_period_months", 12)
        # Accelerated products don't have a buffer — they have accelerator
        if "ACCELERATED" in name_upper and "BUFFER" not in name_upper:
            result.pop("buffer_pct", None)

    # ---- Allianz / AllianzIM -----------------------------------------------
    elif "ALLIANZ" in name_upper:
        for pattern, buf in ALLIANZ_SPECIAL.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        # 6-month products
        if "6 MONTH" in name_upper:
            result.setdefault("outcome_period_months", 6)
        else:
            result.setdefault("outcome_period_months", 12)

    # ---- FT Vest / First Trust ---------------------------------------------
    elif "FT VEST" in name_upper:
        # Floor products — floor % is explicit in name (FLOOR15)
        for pattern, floor in FT_VEST_FLOOR_SERIES.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", floor)
                break
        # Buffer products — check in order from most specific to least
        if "buffer_pct" not in result:
            for pattern, buf in sorted(FT_VEST_BUFFER_SERIES.items(), key=lambda x: -len(x[0])):
                if pattern in name_upper:
                    result.setdefault("buffer_pct", buf)
                    break
        # "FT VEST US EQUITY BUFFER ETF - MONTH" with no qualifier = standard 15%
        if "buffer_pct" not in result and "BUFFER" in name_upper:
            result.setdefault("buffer_pct", 15.0)
        # Accelerated / uncapped products — no buffer
        if "ACCELERATED" in name_upper or "UNCAPPED ACCELERATOR" in name_upper:
            result.pop("buffer_pct", None)
        # Outcome period
        if "QUARTERLY" in name_upper:
            result.setdefault("outcome_period_months", 3)
        elif "LADDERED" in name_upper:
            # Ladder funds span 12 months
            result.setdefault("outcome_period_months", 12)
        else:
            result.setdefault("outcome_period_months", 12)

    # ---- Calamos -----------------------------------------------------------
    elif "CALAMOS" in name_upper:
        # Protection level
        for pattern, buf in CALAMOS_PROTECTION.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        # Laddered = no single outcome period
        if "LADDERED" not in name_upper:
            result.setdefault("outcome_period_months", 12)

    # ---- PGIM (Prudential) -------------------------------------------------
    elif "PGIM" in name_upper:
        for pattern, buf in PGIM_BUFFER.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                result.setdefault("outcome_period_months", PGIM_OUTCOME_MONTHS[pattern])
                break
        if "LADDERED" in name_upper:
            result.pop("outcome_period_months", None)

    # ---- Pacer SWAN SOS ----------------------------------------------------
    elif "PACER" in name_upper and "SWAN" in name_upper:
        for variant, buf in PACER_BUFFER.items():
            if variant in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        if "FUND OF FUNDS" not in name_upper:
            result.setdefault("outcome_period_months", 12)

    # ---- iShares (BlackRock) -----------------------------------------------
    elif "ISHARES" in name_upper:
        for pattern, buf in ISHARES_BUFFER.items():
            if pattern in name_upper:
                result.setdefault("buffer_pct", buf)
                break
        if "QUARTERLY" in name_upper or "LADDERED" in name_upper:
            result.setdefault("outcome_period_months", 3)
        else:
            result.setdefault("outcome_period_months", 12)

    # ---- TrueShares --------------------------------------------------------
    elif "TRUESHARES" in name_upper or "ELEVATION SERIES" in name_upper:
        if "STRUCTURED OUTCOME" in name_upper:
            # TrueShares Structured Outcome = uncapped with buffer
            result.setdefault("buffer_pct", 10.0)
            result.setdefault("outcome_period_months", 12)
        elif "LADDERED" in name_upper or "SEASONALITY" in name_upper:
            result.setdefault("outcome_period_months", 12)

    # ---- Aptus -------------------------------------------------------------
    elif "APTUS" in name_upper:
        if "BUFFER" in name_upper:
            # Named-month Aptus Buffer products = ~10% buffer
            result.setdefault("buffer_pct", 10.0)
            result.setdefault("outcome_period_months", 12)
        elif "LADDERED" in name_upper:
            result.setdefault("buffer_pct", 10.0)

    # ---- AllianceBernstein -------------------------------------------------
    elif "ALLIANCEBERNSTEIN" in issuer or " AB " in name_upper or name_upper.startswith("AB "):
        if "CONSERVATIVE BUFFER" in name_upper:
            result.setdefault("buffer_pct", 10.0)
        elif "MODERATE BUFFER" in name_upper:
            result.setdefault("buffer_pct", 15.0)
        elif "BUFFER" in name_upper:
            result.setdefault("buffer_pct", 9.0)
        result.setdefault("outcome_period_months", 12)

    # ---- ARK DIET ----------------------------------------------------------
    elif "ARK" in name_upper and "DIET" in name_upper:
        # ARK DIET = defined outcome quarterly
        result.setdefault("buffer_pct", 10.0)
        result.setdefault("outcome_period_months", 3)

    # ---- KraneShares -------------------------------------------------------
    elif "KRANE" in name_upper or "KWEB" in name_upper:
        if "100%" in name_upper:
            result.setdefault("buffer_pct", 100.0)
        elif "90%" in name_upper or "90 SERIES" in name_upper:
            result.setdefault("buffer_pct", 90.0)
        if "2027" in name_upper or "2026" in name_upper:
            result.setdefault("outcome_period_months", 24)

    # ---- ProShares dynamic buffer -------------------------------------------
    elif "PROSHARES" in name_upper and "DYNAMIC BUFFER" in name_upper:
        # Dynamic = no fixed buffer; target ~15% but dynamic
        result.setdefault("buffer_pct", 15.0)
        result.setdefault("outcome_period_months", 12)

    # ---- Fidelity ----------------------------------------------------------
    elif "FIDELITY" in name_upper:
        if "BUFFERED" in name_upper or "BUFFER" in name_upper:
            result.setdefault("buffer_pct", 10.0)
            result.setdefault("outcome_period_months", 12)

    # ---- WEBs defined volatility -------------------------------------------
    elif "WEBS" in name_upper and "DEFINED VOLATILITY" in name_upper:
        # Defined volatility = no standard buffer; leave NULL (not a buffer product)
        pass

    return result


# ---------------------------------------------------------------------------
# Main extraction logic
# ---------------------------------------------------------------------------

def extract_attributes(
    ticker: str,
    name: str,
    issuer: Optional[str],
    category: Optional[str],
) -> dict:
    """Combine Strategy A and B to produce attribute dict."""
    name_upper = name.upper()
    attrs: dict = {}

    # Strategy A — regex
    buf = regex_extract_buffer(name_upper)
    if buf is not None:
        attrs["buffer_pct"] = buf

    cap = regex_extract_cap(name_upper)
    if cap is not None:
        attrs["cap_pct"] = cap

    months = regex_extract_outcome_months(name_upper)
    if months is not None:
        attrs["outcome_period_months"] = months

    # Strategy B — issuer-specific (fills gaps, doesn't override regex hits)
    b_attrs = issuer_extract(ticker, name_upper, issuer, category)
    for k, v in b_attrs.items():
        attrs.setdefault(k, v)

    # If outcome_period_months still missing but we have a named month → 12 months
    if "outcome_period_months" not in attrs:
        if any(m in name_upper for m in ALL_MONTHS):
            attrs["outcome_period_months"] = 12

    return attrs


# ---------------------------------------------------------------------------
# CSV merge: read existing + merge new rows
# ---------------------------------------------------------------------------

def load_existing_csv(csv_path: Path) -> tuple[list[str], list[dict]]:
    """Return (fieldnames, rows) from existing fund_master.csv."""
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def merge_rows(
    existing_rows: list[dict],
    new_rows: list[dict],
    fieldnames: list[str],
) -> tuple[list[dict], int, int]:
    """
    Merge new_rows into existing_rows (matched by ticker).
    Returns (merged_rows, updated_count, appended_count).
    """
    existing_by_ticker = {r["ticker"]: r for r in existing_rows}
    updated = 0
    appended = 0

    for new_r in new_rows:
        ticker = new_r["ticker"]
        if ticker in existing_by_ticker:
            # Update only the defined-outcome columns if they're currently empty
            existing = existing_by_ticker[ticker]
            changed = False
            for col in ("cap_pct", "buffer_pct", "outcome_period_months"):
                if col in new_r and new_r[col] is not None and not existing.get(col):
                    existing[col] = new_r[col]
                    changed = True
            # Also update notes
            if changed:
                existing["notes"] = (existing.get("notes") or "") + "; phase6-backfill"
                updated += 1
        else:
            # Append new row (fund not previously in fund_master.csv)
            row = {f: "" for f in fieldnames}
            row.update(new_r)
            row["notes"] = "phase6-backfill"
            existing_by_ticker[ticker] = row
            appended += 1

    merged = list(existing_by_ticker.values())
    return merged, updated, appended


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _strategy_label(buf_src: str, cap_src: str) -> str:
    return buf_src or cap_src or "none"


def main():
    print("Phase 6 — Defined Outcome attribute backfill")
    print("=" * 60)

    # --- 1. Load all Defined Outcome funds from DB ---------------------------
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, fund_name, issuer_nickname, map_defined_category,
               asset_class, primary_strategy, sub_strategy
        FROM mkt_master_data
        WHERE etp_category='Defined' AND market_status='ACTV'
        ORDER BY issuer_nickname, fund_name
    """)
    db_rows = cur.fetchall()
    conn.close()

    print(f"Defined Outcome funds: {len(db_rows)} total")
    print()

    # --- 2. Extract attributes for each fund ---------------------------------
    extracted: list[dict] = []
    strategy_a_hits: list[str] = []
    strategy_b_hits: list[str] = []
    strategy_b_issuer_only: list[str] = []
    null_residue: list[tuple] = []

    for ticker, name, issuer, category, asset_class, primary_strategy, sub_strategy in db_rows:
        attrs = extract_attributes(ticker, name, issuer, category)

        has_buffer = "buffer_pct" in attrs
        has_cap = "cap_pct" in attrs
        has_period = "outcome_period_months" in attrs

        # Track strategy used
        name_upper = name.upper()
        regex_buf = regex_extract_buffer(name_upper)
        regex_cap = regex_extract_cap(name_upper)

        if regex_buf is not None or regex_cap is not None:
            strategy_a_hits.append(ticker)
        if has_buffer and regex_buf is None:
            strategy_b_hits.append(ticker)
            strategy_b_issuer_only.append(f"{ticker} ({issuer}): {name}")

        if not has_buffer and not has_cap:
            null_residue.append((ticker, name, issuer, category))

        if has_buffer or has_cap or has_period:
            # Build override row matching fund_master.csv schema
            row = {
                "ticker": ticker,
                "fund_name": name,
                "issuer_brand": issuer or "",
                "asset_class": asset_class or "",
                "primary_strategy": primary_strategy or "",
                "sub_strategy": sub_strategy or "",
                "concentration": "",
                "underlier_name": "",
                "underlier_is_wrapper": "",
                "root_underlier_name": "",
                "wrapper_type": "",
                "mechanism": "",
                "leverage_ratio": "",
                "direction": "",
                "reset_period": "",
                "distribution_freq": "",
                "outcome_period_months": str(attrs.get("outcome_period_months", "")),
                "cap_pct": str(attrs.get("cap_pct", "")),
                "buffer_pct": str(attrs.get("buffer_pct", "")),
                "accelerator_multiplier": "",
                "barrier_pct": "",
                "region": "",
                "duration_bucket": "",
                "credit_quality": "",
                "tax_structure": "",
                "qualified_dividends": "",
                "source": "phase6-backfill",
                "notes": f"extracted: buf={attrs.get('buffer_pct','')}, cap={attrs.get('cap_pct','')}, mo={attrs.get('outcome_period_months','')}",
            }
            extracted.append(row)

    print(f"Extracted attributes for: {len(extracted)} funds")
    print(f"  Strategy A (regex) hits:        {len(strategy_a_hits)}")
    print(f"  Strategy B (issuer-specific):   {len(strategy_b_hits)}")
    print(f"  NULL residue (no attributes):   {len(null_residue)}")
    print()

    # --- 3. Load existing fund_master.csv and merge --------------------------
    fieldnames, existing_rows = load_existing_csv(FUND_MASTER_CSV)

    # Ensure our new source/notes fields exist in fieldnames
    for col in ("source", "notes"):
        if col not in fieldnames:
            fieldnames.append(col)

    merged_rows, updated_count, appended_count = merge_rows(
        existing_rows, extracted, fieldnames
    )

    print(f"CSV merge:")
    print(f"  Existing rows:  {len(existing_rows):,}")
    print(f"  Updated rows:   {updated_count}")
    print(f"  Appended rows:  {appended_count}")
    print(f"  Total output:   {len(merged_rows):,}")
    print()

    # --- 4. Write updated fund_master.csv ------------------------------------
    with FUND_MASTER_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged_rows)

    print(f"Written: {FUND_MASTER_CSV}")
    print()

    # --- 5. Print summary stats ----------------------------------------------
    has_buffer_after = sum(
        1 for r in extracted if r.get("buffer_pct")
    )
    has_cap_after = sum(
        1 for r in extracted if r.get("cap_pct")
    )
    has_period_after = sum(
        1 for r in extracted if r.get("outcome_period_months")
    )

    print("=== EXTRACTION SUMMARY ===")
    print(f"buffer_pct:           {has_buffer_after}/{len(db_rows)}")
    print(f"cap_pct:              {has_cap_after}/{len(db_rows)}")
    print(f"outcome_period_months:{has_period_after}/{len(db_rows)}")
    print()

    print("=== STRATEGY B ISSUER-ONLY EXTRACTIONS (sample) ===")
    for line in strategy_b_issuer_only[:20]:
        print(f"  {line}")
    if len(strategy_b_issuer_only) > 20:
        print(f"  ... and {len(strategy_b_issuer_only) - 20} more")
    print()

    print("=== NULL RESIDUE (no buffer or cap extracted) ===")
    for item in null_residue:
        print(f"  {item[0]:15s} {item[2] or '?':20s} [{item[3] or '?'}] {item[1]}")
    print(f"Total residue: {len(null_residue)}")
    print()

    # Success criteria check
    target = 300
    if has_buffer_after >= target:
        print(f"SUCCESS: {has_buffer_after} >= {target} target for buffer_pct coverage")
    else:
        print(f"WARNING: {has_buffer_after} < {target} target — may need Strategy C (iXBRL)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
