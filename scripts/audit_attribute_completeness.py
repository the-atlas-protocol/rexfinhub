"""Audit gamma — Attribute completeness per primary_strategy bucket.

Per the taxonomy in docs/CLASSIFICATION_SYSTEM_PLAN.md, each primary_strategy
bucket requires a specific set of attributes.  This script measures how well
the current DB population satisfies those requirements.

Context:
    The new taxonomy columns (primary_strategy, leverage_ratio, direction, etc.)
    were added to mkt_master_data by the Phase 2 migration but have not yet been
    populated by the backfill pipeline (Phase 6 is pending).  This audit therefore
    works against the LEGACY columns that carry equivalent data:

    Bucket       Legacy proxy column(s)
    --------     --------------------------------------------------------
    L&I          etp_category='LI'
                 map_li_leverage_amount  -> leverage_ratio
                 map_li_direction        -> direction
                 uses_derivatives        -> reset_period (proxy)
                 uses_swaps              -> mechanism (proxy)
                 map_li_underlier        -> underlier_name

    Income       etp_category='CC'
                 map_cc_underlier        -> underlier_name
                 cc_type                 -> mechanism / sub_strategy

    Defined      etp_category='Defined'
                 map_defined_category    -> sub_strategy (cap/buffer type)
                 cap_pct / buffer_pct / outcome_period_months  (new cols, 0%)

    Plain Beta   etp_category='Thematic' + NULL-category plain strategies
                 map_thematic_category   -> sub_strategy (thematic bucket)
                 is_singlestock          -> concentration (proxy)

    Risk Mgmt    strategy='Alternative'  (no dedicated etp_category yet)
                 mechanism (new col, 0%)
                 sub_strategy (new col, 0%)

Severity guide for the fix queue:
    CRITICAL  — attribute is required for core business logic (direction on L&I,
                sub_strategy on Defined) and is missing
    HIGH      — attribute missing but a legacy column partially covers it
    LOW       — new taxonomy column not yet populated (expected; Phase 6 work)

This script is READ-ONLY — no writes to the database.

Output:
    docs/attribute_completeness_report.md
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
REPORT_PATH = PROJECT_ROOT / "docs" / "attribute_completeness_report.md"

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pct(count: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{100 * count / total:.1f}%"


def run_query(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def scalar(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    result = cur.fetchone()
    return result[0] if result else 0


# ---------------------------------------------------------------------------
# Per-bucket audits
# ---------------------------------------------------------------------------

def audit_li(cur: sqlite3.Cursor) -> dict:
    """L&I bucket — etp_category = 'LI'."""
    total = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='LI'")
    if total == 0:
        return {"total": 0}

    has_leverage = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND map_li_leverage_amount IS NOT NULL AND map_li_leverage_amount != ''""")

    has_direction = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND map_li_direction IS NOT NULL AND map_li_direction != ''""")

    # reset_period has no direct legacy column; uses_derivatives/uses_leverage
    # are the closest proxy (they confirm derivatives are in use)
    has_reset_proxy = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND (uses_derivatives='Y' OR uses_leverage='Y')""")

    # mechanism: uses_swaps is a partial proxy
    has_mechanism_proxy = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND uses_swaps='Y'""")

    has_underlier = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND map_li_underlier IS NOT NULL AND map_li_underlier != ''""")

    # --- critical-NULL fund lists ---
    missing_leverage = run_query(cur, """
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND (map_li_leverage_amount IS NULL OR map_li_leverage_amount = '')
        ORDER BY ticker LIMIT 30""")

    missing_underlier = run_query(cur, """
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='LI'
        AND (map_li_underlier IS NULL OR map_li_underlier = '')
        ORDER BY ticker LIMIT 30""")

    return {
        "total": total,
        "attributes": {
            "leverage_ratio (map_li_leverage_amount)": (has_leverage, total, "HIGH"),
            "direction (map_li_direction)": (has_direction, total, "CRITICAL"),
            "reset_period (proxy: uses_derivatives)": (has_reset_proxy, total, "HIGH"),
            "mechanism (proxy: uses_swaps)": (has_mechanism_proxy, total, "HIGH"),
            "underlier_name (map_li_underlier)": (has_underlier, total, "CRITICAL"),
        },
        "fix_queues": {
            "missing leverage_ratio": missing_leverage,
            "missing underlier_name": missing_underlier,
        },
    }


def audit_income(cur: sqlite3.Cursor) -> dict:
    """Income bucket — etp_category = 'CC'."""
    total = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='CC'")
    if total == 0:
        return {"total": 0}

    # underlier_name: map_cc_underlier
    has_underlier = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
        AND map_cc_underlier IS NOT NULL AND map_cc_underlier != ''""")

    # mechanism: cc_type (Traditional / Synthetic)
    has_mechanism = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
        AND cc_type IS NOT NULL AND cc_type != ''""")

    # sub_strategy: cc_category
    has_sub_strategy = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
        AND cc_category IS NOT NULL AND cc_category != ''""")

    # distribution_freq: no legacy column
    has_dist_freq = 0

    missing_underlier = run_query(cur, """
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
        AND (map_cc_underlier IS NULL OR map_cc_underlier = '')
        ORDER BY ticker LIMIT 30""")

    missing_mechanism = run_query(cur, """
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
        AND (cc_type IS NULL OR cc_type = '')
        ORDER BY ticker LIMIT 20""")

    return {
        "total": total,
        "attributes": {
            "underlier_name (map_cc_underlier)": (has_underlier, total, "CRITICAL"),
            "mechanism (cc_type)": (has_mechanism, total, "HIGH"),
            "sub_strategy (cc_category)": (has_sub_strategy, total, "HIGH"),
            "distribution_freq": (has_dist_freq, total, "LOW"),
        },
        "fix_queues": {
            "missing underlier_name": missing_underlier,
            "missing mechanism": missing_mechanism,
        },
    }


def audit_defined_outcome(cur: sqlite3.Cursor) -> dict:
    """Defined Outcome bucket — etp_category = 'Defined'."""
    total = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='Defined'")
    if total == 0:
        return {"total": 0}

    # sub_strategy / cap type: map_defined_category
    has_sub_strategy = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='Defined'
        AND map_defined_category IS NOT NULL AND map_defined_category != ''""")

    # mechanism: uses_derivatives
    has_mechanism = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='Defined'
        AND uses_derivatives='Y'""")

    # cap_pct, buffer_pct, outcome_period_months — new taxonomy columns, not yet populated
    has_cap = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='Defined' AND cap_pct IS NOT NULL")
    has_buffer = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='Defined' AND buffer_pct IS NOT NULL")
    has_period = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='Defined' AND outcome_period_months IS NOT NULL")

    missing_sub_strategy = run_query(cur, """
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='Defined'
        AND (map_defined_category IS NULL OR map_defined_category = '')
        ORDER BY ticker LIMIT 20""")

    return {
        "total": total,
        "attributes": {
            "sub_strategy (map_defined_category)": (has_sub_strategy, total, "CRITICAL"),
            "mechanism (proxy: uses_derivatives)": (has_mechanism, total, "HIGH"),
            "cap_pct (new col — Phase 6 pending)": (has_cap, total, "LOW"),
            "buffer_pct (new col — Phase 6 pending)": (has_buffer, total, "LOW"),
            "outcome_period_months (new col — Phase 6 pending)": (has_period, total, "LOW"),
        },
        "fix_queues": {
            "missing sub_strategy": missing_sub_strategy,
        },
    }


def audit_plain_beta(cur: sqlite3.Cursor) -> dict:
    """Plain Beta bucket — Thematic + plain-strategy funds.

    Two sub-populations:
    1. etp_category='Thematic' (has map_thematic_category)
    2. NULL etp_category with a non-LI/CC/Defined strategy (broad plain beta)
    """
    # Thematic sub-population
    thematic_total = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND etp_category='Thematic'")
    thematic_has_sub = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='Thematic'
        AND map_thematic_category IS NOT NULL AND map_thematic_category != ''""")
    thematic_has_mech = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='Thematic'
        AND uses_derivatives IS NOT NULL""")

    # Broad plain-beta (NULL etp_category, non-LI/CC/Defined strategy)
    plain_total = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV'
        AND (etp_category IS NULL OR etp_category = '')
        AND strategy NOT IN ('Leveraged & Inverse', 'Income / Covered Call', 'Defined Outcome')""")
    plain_has_mech = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV'
        AND (etp_category IS NULL OR etp_category = '')
        AND strategy NOT IN ('Leveraged & Inverse', 'Income / Covered Call', 'Defined Outcome')
        AND uses_derivatives IS NOT NULL""")
    plain_has_concentration = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV'
        AND (etp_category IS NULL OR etp_category = '')
        AND strategy NOT IN ('Leveraged & Inverse', 'Income / Covered Call', 'Defined Outcome')
        AND is_singlestock IS NOT NULL AND is_singlestock != ''""")

    total = thematic_total + plain_total

    return {
        "total": total,
        "thematic_total": thematic_total,
        "plain_total": plain_total,
        "attributes": {
            "sub_strategy / theme (map_thematic_category) [Thematic only]": (thematic_has_sub, thematic_total, "CRITICAL"),
            "mechanism (proxy: uses_derivatives) [Thematic]": (thematic_has_mech, thematic_total, "HIGH"),
            "mechanism (proxy: uses_derivatives) [Plain Beta]": (plain_has_mech, plain_total, "HIGH"),
            "concentration (proxy: is_singlestock) [Plain Beta]": (plain_has_concentration, plain_total, "LOW"),
        },
        "fix_queues": {},
    }


def audit_risk_mgmt(cur: sqlite3.Cursor) -> dict:
    """Risk Mgmt bucket — strategy='Alternative'.

    No dedicated etp_category exists yet in the legacy system.
    Required new attributes: mechanism, sub_strategy — both Phase 6 pending.
    """
    total = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND strategy='Alternative'")
    if total == 0:
        return {"total": 0}

    has_mechanism = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND strategy='Alternative'
        AND mechanism IS NOT NULL""")
    has_sub_strategy = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND strategy='Alternative'
        AND sub_strategy IS NOT NULL""")
    has_uses_deriv = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND strategy='Alternative'
        AND uses_derivatives IS NOT NULL""")

    sample_funds = run_query(cur, """
        SELECT ticker, fund_name, uses_derivatives FROM mkt_master_data
        WHERE market_status='ACTV' AND strategy='Alternative'
        ORDER BY ticker LIMIT 15""")

    return {
        "total": total,
        "attributes": {
            "mechanism (new col — Phase 6 pending)": (has_mechanism, total, "LOW"),
            "sub_strategy (new col — Phase 6 pending)": (has_sub_strategy, total, "LOW"),
            "mechanism (proxy: uses_derivatives)": (has_uses_deriv, total, "HIGH"),
        },
        "fix_queues": {
            "sample funds (no sub_strategy)": sample_funds,
        },
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(buckets: dict) -> str:
    lines: list[str] = []
    a = lines.append

    a("# Attribute Completeness Report — Audit gamma")
    a(f"")
    a(f"**Generated**: {TODAY}  ")
    a(f"**Database**: `data/etp_tracker.db`  ")
    a(f"**Scope**: `mkt_master_data WHERE market_status='ACTV'` ({buckets['_meta']['total_actv']:,} funds)")
    a(f"")
    a("## Context")
    a("")
    a("The new taxonomy columns (`primary_strategy`, `leverage_ratio`, `direction`, etc.)")
    a("were added in Phase 2 of the classification plan but the backfill pipeline (Phase 6)")
    a("has not run yet. All new column counts are 0%. This audit therefore uses LEGACY")
    a("columns as proxies to measure real attribute completeness. Where a legacy proxy")
    a("exists, the finding is labelled HIGH or CRITICAL. Where only new columns apply,")
    a("the finding is labelled LOW (expected gap pending Phase 6).")
    a("")
    a("### Strategy bucket mapping (legacy → new taxonomy)")
    a("")
    a("| primary_strategy | Legacy filter |")
    a("|---|---|")
    a("| L&I | `etp_category = 'LI'` |")
    a("| Income | `etp_category = 'CC'` |")
    a("| Defined Outcome | `etp_category = 'Defined'` |")
    a("| Plain Beta | `etp_category = 'Thematic'` + NULL-category plain strategies |")
    a("| Risk Mgmt | `strategy = 'Alternative'` |")
    a("")
    a("---")
    a("")

    severities = {"CRITICAL": "HIGH", "HIGH": "MEDIUM", "LOW": "LOW"}
    emoji_map = {"CRITICAL": "***", "HIGH": "**", "LOW": ""}

    bucket_order = ["L&I", "Income", "Defined Outcome", "Plain Beta", "Risk Mgmt"]
    for bucket_name in bucket_order:
        data = buckets.get(bucket_name, {})
        total = data.get("total", 0)
        a(f"## {bucket_name}")
        a(f"")
        a(f"**Fund count**: {total:,}")

        if total == 0:
            a(f"")
            a("*No funds in this bucket.*")
            a("")
            continue

        if bucket_name == "Plain Beta":
            a(f" ({data.get('thematic_total',0):,} Thematic + {data.get('plain_total',0):,} broad plain beta)")

        a(f"")
        a("### Attribute population")
        a("")
        a("| Attribute | Populated | % | Severity |")
        a("|---|---|---|---|")
        for attr_name, (count, denom, severity) in data.get("attributes", {}).items():
            pct_val = pct(count, denom)
            a(f"| `{attr_name}` | {count:,} / {denom:,} | {pct_val} | {severity} |")

        a("")

        # Fix queues
        for queue_name, queue_rows in data.get("fix_queues", {}).items():
            if not queue_rows:
                continue
            a(f"### Fix queue — {queue_name} ({len(queue_rows)} shown)")
            a("")
            if queue_rows and len(queue_rows[0]) >= 2:
                a("| Ticker | Fund Name |")
                a("|---|---|")
                for row in queue_rows:
                    ticker = row[0] or ""
                    name = str(row[1] or "")[:70]
                    a(f"| `{ticker}` | {name} |")
            else:
                for row in queue_rows:
                    a(f"- `{row[0]}` {row[1] if len(row) > 1 else ''}")
            a("")

        a("---")
        a("")

    # Summary table
    a("## Summary — worst attributes per strategy")
    a("")
    a("| Strategy | Worst attribute | % populated |")
    a("|---|---|---|")
    for bucket_name in bucket_order:
        data = buckets.get(bucket_name, {})
        attrs = data.get("attributes", {})
        if not attrs:
            a(f"| {bucket_name} | N/A | N/A |")
            continue
        worst_attr = min(attrs.items(), key=lambda x: (x[1][0] / x[1][1]) if x[1][1] > 0 else 0)
        attr_label = worst_attr[0]
        count, denom, _ = worst_attr[1]
        a(f"| {bucket_name} | `{attr_label}` | {pct(count, denom)} |")

    a("")
    a("---")
    a("")
    a("## Notes")
    a("")
    a("- `reset_period` and `mechanism` have no direct legacy column for L&I. The proxy")
    a("  columns (`uses_derivatives`, `uses_swaps`) have 0% population for the LI bucket,")
    a("  which likely reflects a Bloomberg data gap rather than a true missing attribute.")
    a("- All 0% LOW-severity findings are expected: these are new Phase 2 columns that")
    a("  Phase 6 (LLM backfill) will populate. They appear here to establish the baseline.")
    a("- `distribution_freq` for Income has no legacy proxy — will require Phase 6.")
    a("- Risk Mgmt has no dedicated `etp_category` in the legacy system; the `strategy=")
    a("  'Alternative'` proxy undercounts (hedged equity, trend-following, etc. may be")
    a("  in NULL-category funds). True bucket size unknown until Phase 6 classifies them.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    total_actv = scalar(cur, "SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV'")
    print(f"Connected to {DB_PATH}")
    print(f"ACTV funds: {total_actv:,}")
    print()

    buckets: dict = {"_meta": {"total_actv": total_actv}}

    print("Auditing L&I ...")
    buckets["L&I"] = audit_li(cur)
    print(f"  total={buckets['L&I']['total']}")

    print("Auditing Income ...")
    buckets["Income"] = audit_income(cur)
    print(f"  total={buckets['Income']['total']}")

    print("Auditing Defined Outcome ...")
    buckets["Defined Outcome"] = audit_defined_outcome(cur)
    print(f"  total={buckets['Defined Outcome']['total']}")

    print("Auditing Plain Beta ...")
    buckets["Plain Beta"] = audit_plain_beta(cur)
    print(f"  total={buckets['Plain Beta']['total']}")

    print("Auditing Risk Mgmt ...")
    buckets["Risk Mgmt"] = audit_risk_mgmt(cur)
    print(f"  total={buckets['Risk Mgmt']['total']}")

    con.close()

    report = render_report(buckets)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print()
    print(f"Report written to: {REPORT_PATH}")

    # Console summary
    print()
    print("=== SUMMARY: Worst attribute per strategy ===")
    for bucket_name in ["L&I", "Income", "Defined Outcome", "Plain Beta", "Risk Mgmt"]:
        data = buckets.get(bucket_name, {})
        attrs = data.get("attributes", {})
        if not attrs or data.get("total", 0) == 0:
            print(f"  {bucket_name:20s}: no funds")
            continue
        worst = min(attrs.items(), key=lambda x: (x[1][0] / x[1][1]) if x[1][1] > 0 else 0)
        label, (count, denom, severity) = worst
        print(f"  {bucket_name:20s}: [{severity}] {label} = {pct(count, denom)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
