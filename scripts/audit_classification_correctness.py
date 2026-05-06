"""
Audit Beta: Classification Correctness Sample Validator
Validates 500 ACTV funds (100 per primary_strategy bucket) using name-based heuristics.
READ-ONLY — never modifies the database.
"""

import sqlite3
import random
import csv
import re
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
DB_PATH = Path("C:/Projects/rexfinhub/data/etp_tracker.db")
OUT_CSV = Path("C:/Projects/rexfinhub/docs/classification_qa_residue.csv")
OUT_MD  = Path("C:/Projects/rexfinhub/docs/audit_beta_classification_correctness.md")
SAMPLE_PER_BUCKET = 100
RANDOM_SEED = 42

# Bucket mapping: brief label -> DB strategy value
BUCKET_MAP = {
    "Plain Beta":      "Broad Beta",
    "L&I":             "Leveraged & Inverse",
    "Defined Outcome": "Defined Outcome",
    "Income":          "Income / Covered Call",
    "Risk Mgmt":       "Alternative",
}

# ── Marker sets ─────────────────────────────────────────────────────────────
LI_MARKERS        = ["2X", "3X", "SHORT", "INVERSE", "BULL", "BEAR", "LEVERAG",
                     "ULTRA", "1.5X", "-2", "-3",
                     "PROSHARES ULTRA", "DIREXION DAILY", "LEVERAGED"]
INCOME_MARKERS    = ["COVERED CALL", "BUY-WRITE", "YIELDMAX", "INCOME", "PREMIUM", "0DTE",
                     "WEEKLYPAY", "YIELD", "OPTION INCOME", "AUTOCALLABLE"]
# NOTE: "CAP" removed — too broad (hits SMALL CAP, LARGE CAP, MID CAP equity styles)
# NOTE: "LONG" removed — hits LONG-TERM, LONG DURATION plain beta funds
DEFINED_MARKERS   = ["BUFFER", "FLOOR", "ACCELERATED", "OUTCOME", "DEFINED",
                     "STRUCTURED OUTCOME", "BARRIER", "POINT-TO-POINT", "DEFINED PROTECTION",
                     "WING", "STACKER", "POWER BUFFER"]
RISK_MARKERS      = ["HEDGED", "MANAGED FUTURES", "TAIL", "DEFENSE", "MANAGED RISK",
                     "MANAGED VOLATILITY", "LOW VOLATILITY", "MINIMUM VOLATILITY",
                     "MULTI-STRATEGY", "MERGER ARB", "RISK PARITY",
                     "LONG/SHORT", "LONG SHORT", "MARKET NEUTRAL", "SHORT STORES",
                     "SHORT DURATION", "ALTERNATIVE", "VIX", "CURRENCY",
                     "SHORT-TERM FUTURES", "MID-TERM FUTURES", "ABSOLUTE RETURN",
                     "HEDG", "HEDGE", "BLACKSWAN", "BLACK SWAN", "ALL WEATHER",
                     "ANTI-BETA", "MACRO", "EVENT-DRIVEN", "SYSTEMATIC",
                     "CURRENCYSHARES", "TACTICAL", "MARKET NEUTRAL",
                     "SHORT STRATEGIES", "UNCONSTRAINED", "NEUTRAL"]

# Markers that indicate NOT plain beta (any of the above)
NON_PLAIN_MARKERS = set(LI_MARKERS + INCOME_MARKERS + DEFINED_MARKERS + RISK_MARKERS)


def name_has(name: str, markers: list[str]) -> list[str]:
    """
    Return list of markers found in the uppercased fund name.
    Short tokens (<=4 chars) are matched as whole words to avoid false positives
    (e.g. 'CAP' in 'SMALLCAP', 'LONG' in 'PROLONGED').
    """
    upper = name.upper()
    hits = []
    for m in markers:
        if len(m) <= 4:
            # Word-boundary match for short tokens
            if re.search(r'\b' + re.escape(m) + r'\b', upper):
                hits.append(m)
        else:
            if m in upper:
                hits.append(m)
    return hits


def validate(ticker: str, fund_name: str, primary_strategy: str) -> dict:
    """
    Validate a single fund.
    Returns a dict with validation_status, suspected_correct_strategy, why.
    """
    name = (fund_name or "").upper()

    li_hits      = name_has(name, LI_MARKERS)
    income_hits  = name_has(name, INCOME_MARKERS)
    defined_hits = name_has(name, DEFINED_MARKERS)
    risk_hits    = name_has(name, RISK_MARKERS)

    all_hits = {
        "L&I":             li_hits,
        "Income":          income_hits,
        "Defined Outcome": defined_hits,
        "Risk Mgmt":       risk_hits,
    }
    matched_strategies = [s for s, h in all_hits.items() if h]

    # ── AMBIGUOUS: name fires multiple strategy markers ───────────────────
    if len(matched_strategies) > 1:
        why = f"Name triggers multiple strategy markers: {matched_strategies}"
        return {
            "validation_status": "AMBIGUOUS",
            "suspected_correct_strategy": " / ".join(matched_strategies),
            "why": why,
        }

    # ── Per-bucket logic ─────────────────────────────────────────────────
    if primary_strategy == "Plain Beta":
        non_hits = [m for m in NON_PLAIN_MARKERS if m in name]
        if non_hits:
            # Guess actual strategy from which bucket fired
            guesses = matched_strategies if matched_strategies else []
            if li_hits:      guess = "L&I"
            elif income_hits: guess = "Income"
            elif defined_hits: guess = "Defined Outcome"
            elif risk_hits:  guess = "Risk Mgmt"
            else:            guess = "Unknown"
            return {
                "validation_status": "SUSPECT",
                "suspected_correct_strategy": guess,
                "why": f"Plain Beta fund name contains non-beta marker(s): {non_hits[:3]}",
            }
        return {
            "validation_status": "CONFIRMED",
            "suspected_correct_strategy": "Plain Beta",
            "why": "No L&I/Income/Defined/Risk markers found in name",
        }

    elif primary_strategy == "L&I":
        if li_hits:
            return {
                "validation_status": "CONFIRMED",
                "suspected_correct_strategy": "L&I",
                "why": f"Name contains L&I marker(s): {li_hits[:3]}",
            }
        # Could still be L&I via issuer (ProShares, Direxion, Leverage Shares etc.)
        # — flag as SUSPECT if no name signal
        return {
            "validation_status": "SUSPECT",
            "suspected_correct_strategy": "Unknown",
            "why": "L&I fund name missing expected leverage/direction marker",
        }

    elif primary_strategy == "Defined Outcome":
        if defined_hits:
            return {
                "validation_status": "CONFIRMED",
                "suspected_correct_strategy": "Defined Outcome",
                "why": f"Name contains Defined Outcome marker(s): {defined_hits[:3]}",
            }
        # TrueShares, Innovator, AllianzIM use "Structured Outcome" / product series names
        # that may lack explicit keywords — flag only as SUSPECT not WRONG
        return {
            "validation_status": "SUSPECT",
            "suspected_correct_strategy": "Unknown",
            "why": "Defined Outcome fund name missing expected buffer/outcome marker",
        }

    elif primary_strategy == "Income":
        if income_hits:
            return {
                "validation_status": "CONFIRMED",
                "suspected_correct_strategy": "Income",
                "why": f"Name contains Income marker(s): {income_hits[:3]}",
            }
        return {
            "validation_status": "SUSPECT",
            "suspected_correct_strategy": "Unknown",
            "why": "Income fund name missing expected covered-call/income marker",
        }

    elif primary_strategy == "Risk Mgmt":
        if risk_hits:
            return {
                "validation_status": "CONFIRMED",
                "suspected_correct_strategy": "Risk Mgmt",
                "why": f"Name contains Risk Mgmt marker(s): {risk_hits[:3]}",
            }
        return {
            "validation_status": "SUSPECT",
            "suspected_correct_strategy": "Unknown",
            "why": "Risk Mgmt fund name missing expected hedge/risk/tail marker",
        }

    # Should not reach here
    return {
        "validation_status": "AMBIGUOUS",
        "suspected_correct_strategy": "Unknown",
        "why": "Unexpected strategy label in audit",
    }


def main():
    random.seed(RANDOM_SEED)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    records = []

    for brief_label, db_strategy in BUCKET_MAP.items():
        cur.execute(
            """
            SELECT ticker, fund_name, strategy, sub_strategy
            FROM mkt_master_data
            WHERE strategy = ?
              AND market_status = 'ACTV'
            """,
            (db_strategy,),
        )
        bucket_rows = cur.fetchall()
        sample_size = min(SAMPLE_PER_BUCKET, len(bucket_rows))
        sampled = random.sample(bucket_rows, sample_size)

        print(f"  Bucket '{brief_label}' ({db_strategy}): {len(bucket_rows)} ACTV funds, sampled {sample_size}")

        for row in sampled:
            result = validate(row["ticker"], row["fund_name"], brief_label)
            records.append({
                "ticker":                    row["ticker"],
                "fund_name":                 row["fund_name"],
                "primary_strategy":          brief_label,
                "sub_strategy":              row["sub_strategy"] or "",
                "validation_status":         result["validation_status"],
                "suspected_correct_strategy": result["suspected_correct_strategy"],
                "why":                       result["why"],
            })

    conn.close()

    # ── Write CSV ────────────────────────────────────────────────────────
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ticker", "fund_name", "primary_strategy", "sub_strategy",
                  "validation_status", "suspected_correct_strategy", "why"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"\nCSV written: {OUT_CSV} ({len(records)} rows)")

    # ── Build summary stats ──────────────────────────────────────────────
    from collections import Counter

    bucket_stats = {}
    for brief_label in BUCKET_MAP:
        bucket_records = [r for r in records if r["primary_strategy"] == brief_label]
        status_counts  = Counter(r["validation_status"] for r in bucket_records)
        total          = len(bucket_records)
        suspect_count  = status_counts.get("SUSPECT", 0)
        suspect_pct    = round(100 * suspect_count / total, 1) if total else 0
        bucket_stats[brief_label] = {
            "total":        total,
            "confirmed":    status_counts.get("CONFIRMED", 0),
            "suspect":      suspect_count,
            "ambiguous":    status_counts.get("AMBIGUOUS", 0),
            "suspect_pct":  suspect_pct,
        }

    # Top 20 most-suspect (SUSPECT first, then AMBIGUOUS, sorted by name length desc)
    suspects = [r for r in records if r["validation_status"] == "SUSPECT"]
    ambiguous = [r for r in records if r["validation_status"] == "AMBIGUOUS"]
    top20 = (suspects + ambiguous)[:20]

    # ── Write Markdown ───────────────────────────────────────────────────
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    md_lines = [
        "# Audit Beta — Classification Correctness Report",
        "",
        f"**Generated**: {now}  ",
        f"**Sample**: {len(records)} funds (up to 100 per bucket)  ",
        f"**Method**: Rule-based name-marker validation  ",
        "",
        "---",
        "",
        "## Per-Bucket Summary",
        "",
        "| Bucket | Sampled | CONFIRMED | SUSPECT | AMBIGUOUS | SUSPECT % |",
        "|--------|---------|-----------|---------|-----------|-----------|",
    ]

    for label, stats in bucket_stats.items():
        md_lines.append(
            f"| {label} | {stats['total']} | {stats['confirmed']} "
            f"| {stats['suspect']} | {stats['ambiguous']} | {stats['suspect_pct']}% |"
        )

    total_suspects   = sum(s["suspect"]   for s in bucket_stats.values())
    total_ambiguous  = sum(s["ambiguous"] for s in bucket_stats.values())
    total_confirmed  = sum(s["confirmed"] for s in bucket_stats.values())
    overall_pct      = round(100 * total_suspects / len(records), 1)

    md_lines += [
        "",
        f"**Overall SUSPECT**: {total_suspects} / {len(records)} ({overall_pct}%)  ",
        f"**Overall AMBIGUOUS**: {total_ambiguous}  ",
        f"**Overall CONFIRMED**: {total_confirmed}  ",
        "",
        "---",
        "",
        "## Top 20 Most-Suspect Funds",
        "",
        "| # | Ticker | Fund Name | Strategy | Suspected | Why |",
        "|---|--------|-----------|----------|-----------|-----|",
    ]

    for i, r in enumerate(top20, 1):
        name_short = r["fund_name"][:55] + "..." if len(r["fund_name"]) > 55 else r["fund_name"]
        why_short  = r["why"][:80] + "..." if len(r["why"]) > 80 else r["why"]
        md_lines.append(
            f"| {i} | {r['ticker']} | {name_short} | {r['primary_strategy']} "
            f"| {r['suspected_correct_strategy']} | {why_short} |"
        )

    md_lines += [
        "",
        "---",
        "",
        "## Methodology",
        "",
        "Each fund name is uppercased and scanned for keyword markers per strategy:",
        "",
        "- **L&I**: `2X`, `3X`, `LONG`, `SHORT`, `INVERSE`, `BULL`, `BEAR`, `LEVERAG`, `ULTRA`, `DIREXION`, `LEVERAGED`",
        "- **Income**: `COVERED CALL`, `BUY-WRITE`, `YIELDMAX`, `INCOME`, `PREMIUM`, `0DTE`, `WEEKLYPAY`, `YIELD`, `OPTION INCOME`, `AUTOCALLABLE`",
        "- **Defined Outcome**: `BUFFER`, `FLOOR`, `ACCELERATED`, `CAP`, `OUTCOME`, `DEFINED`, `STRUCTURED OUTCOME`, `BARRIER`, `DEFINED PROTECTION`",
        "- **Risk Mgmt**: `HEDGED`, `RISK`, `MANAGED FUTURES`, `TAIL`, `DEFENSE`, `MANAGED RISK`, `MANAGED VOLATILITY`, `LOW VOLATILITY`, `MINIMUM VOLATILITY`, `ALTERNATIVE`",
        "- **Plain Beta**: CONFIRMED if none of the above markers fire; SUSPECT if any fire",
        "",
        "### Caveats",
        "",
        "- Name-only heuristic — some funds (e.g., ProShares, Direxion) are L&I without encoding it in their product name",
        "- Defined Outcome funds (TrueShares, AllianzIM) sometimes use series date suffixes — may be under-detected",
        "- SUSPECT does not mean wrong — it is a flag for human review",
        "",
        "---",
        "",
        f"*Output file*: `docs/classification_qa_residue.csv`  ",
        f"*Script*: `scripts/audit_classification_correctness.py`  ",
    ]

    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Markdown written: {OUT_MD}")

    # ── Console summary ──────────────────────────────────────────────────
    print("\n=== PER-BUCKET SUSPECT % ===")
    for label, stats in bucket_stats.items():
        print(f"  {label:20s}: {stats['suspect_pct']:5.1f}%  ({stats['suspect']}/{stats['total']} SUSPECT)")
    print(f"\n  Overall SUSPECT: {overall_pct}%")

    print("\n=== TOP 5 WORST MISCLASSIFICATIONS ===")
    for r in top20[:5]:
        print(f"  [{r['validation_status']}] {r['ticker']} | {r['primary_strategy']} | {r['fund_name'][:60]}")
        print(f"    Suspected: {r['suspected_correct_strategy']} | {r['why']}")


if __name__ == "__main__":
    print("=== Audit Beta: Classification Correctness ===")
    main()
