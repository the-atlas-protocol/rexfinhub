"""Audit delta — Cross-referential integrity across mkt_master_data columns.

Identifies logical contradictions and data inconsistencies across columns.
Every check is READ-ONLY. The database is never modified.

Checks run:
    CX-01  etp_category='LI' vs strategy='Leveraged & Inverse' mismatch
    CX-02  etp_category='Crypto' funds where is_crypto != 'Cryptocurrency'
    CX-03  REX-brand funds (issuer_display IN ('REX','Rex Shares ETFs')) with is_rex != 1
    CX-04  is_rex=1 but issuer is not a known REX/MicroSectors brand
    CX-05  direction='Short' with positive leverage_amount (convention inconsistency)
    CX-06  etp_category='CC' with no entry in mkt_category_attributes
    CX-07  map_li_underlier values that don't match any ticker in the universe
    CX-08  inception_date recorded as 'NaT' on ACTV funds (missing date)
    CX-09  inception_date < 1990-01-01 on ACTV funds (suspiciously old)
    CX-10  ACTV funds with aum IS NULL and inception_date > 30 days ago

Severity guide:
    HIGH    — data inconsistency that actively corrupts reporting or routing
    MEDIUM  — likely data error; may cause silent mis-classification
    LOW     — anomaly worth investigating but not immediately harmful

Output:
    docs/cross_ref_integrity_report.md
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
REPORT_PATH = PROJECT_ROOT / "docs" / "cross_ref_integrity_report.md"

TODAY = date.today().isoformat()
THIRTY_DAYS_AGO = "2026-04-05"  # today - 30 days (hardcoded for reproducibility)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_query(cur: sqlite3.Cursor, sql: str) -> list[tuple]:
    cur.execute(sql)
    return cur.fetchall()


def scalar(cur: sqlite3.Cursor, sql: str) -> int:
    cur.execute(sql)
    result = cur.fetchone()
    return result[0] if result else 0


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def cx01_li_strategy_mismatch(cur: sqlite3.Cursor) -> dict:
    """CX-01: etp_category='LI' vs strategy='Leveraged & Inverse' disagreement.

    Both columns classify the same population. Any fund where they disagree
    is ambiguously routed — reports may include or exclude it based on which
    column the query filters on.
    """
    # LI category but not LI strategy
    cat_not_strat = run_query(cur, """
        SELECT ticker, fund_name, etp_category, strategy
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND etp_category='LI'
        AND strategy != 'Leveraged & Inverse'
        ORDER BY ticker""")

    # LI strategy but not LI category
    strat_not_cat = run_query(cur, """
        SELECT ticker, fund_name, etp_category, strategy
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND strategy='Leveraged & Inverse'
        AND (etp_category != 'LI' OR etp_category IS NULL)
        ORDER BY ticker""")

    total = len(cat_not_strat) + len(strat_not_cat)
    return {
        "id": "CX-01",
        "description": "etp_category='LI' vs strategy='Leveraged & Inverse' disagreement",
        "severity": "HIGH",
        "count": total,
        "sections": {
            f"etp_category=LI but strategy != 'Leveraged & Inverse' ({len(cat_not_strat)} funds)": cat_not_strat,
            f"strategy='Leveraged & Inverse' but etp_category != 'LI' ({len(strat_not_cat)} funds)": strat_not_cat,
        },
        "columns": ["Ticker", "Fund Name", "etp_category", "strategy"],
    }


def cx02_crypto_is_crypto_mismatch(cur: sqlite3.Cursor) -> dict:
    """CX-02: etp_category='Crypto' but is_crypto is not 'Cryptocurrency'.

    The is_crypto column stores Bloomberg's hedge-fund strategy string.
    For ETP crypto funds it should be 'Cryptocurrency'. A NULL or other
    value suggests the Bloomberg data row is missing or misrouted.
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, is_crypto
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND etp_category='Crypto'
        AND (is_crypto IS NULL OR is_crypto NOT IN ('Cryptocurrency', '1'))
        ORDER BY ticker""")

    return {
        "id": "CX-02",
        "description": "etp_category='Crypto' funds where is_crypto != 'Cryptocurrency'",
        "severity": "MEDIUM",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "is_crypto value"],
    }


def cx03_rex_brand_not_flagged(cur: sqlite3.Cursor) -> dict:
    """CX-03: Funds with REX/Rex Shares ETFs issuer_display but is_rex != 1.

    These funds will be excluded from REX-specific reports even though they
    are REX products.  Note: 'Direxion' also matches LIKE '%REX%' but is NOT
    a REX product; we exclude it by checking the exact brand strings.
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, issuer_display, is_rex
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND issuer_display IN ('REX', 'Rex Shares ETFs')
        AND (is_rex IS NULL OR is_rex = 0)
        ORDER BY ticker""")

    return {
        "id": "CX-03",
        "description": "REX-brand funds (issuer_display IN ('REX','Rex Shares ETFs')) with is_rex != 1",
        "severity": "HIGH",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "issuer_display", "is_rex"],
    }


def cx04_is_rex_wrong_brand(cur: sqlite3.Cursor) -> dict:
    """CX-04: is_rex=1 but issuer_display is not a known REX-family brand.

    REX family includes: REX, Rex Shares ETFs, MicroSectors (also REX).
    Any other brand with is_rex=1 is a mislabel — the flag should be 0.
    Known exception pattern: Osprey (OBTC) was historically associated with
    REX but is a separate trust. Surface for manual confirmation.
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, issuer_display, is_rex
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND is_rex = 1
        AND issuer_display NOT IN ('REX', 'Rex Shares ETFs', 'MicroSectors')
        ORDER BY issuer_display, ticker""")

    return {
        "id": "CX-04",
        "description": "is_rex=1 but issuer_display is not a known REX/MicroSectors brand",
        "severity": "MEDIUM",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "issuer_display", "is_rex"],
        "note": "MicroSectors is a REX brand. Osprey (OBTC) is an edge case — verify manually.",
    }


def cx05_short_positive_leverage(cur: sqlite3.Cursor) -> dict:
    """CX-05: Short direction with a positive leverage_amount stored.

    The legacy map_li_leverage_amount column stores leverage as an unsigned
    magnitude for all funds (e.g., 2.0 for both 2x long and 2x short).
    The new taxonomy schema intends leverage_ratio to be NEGATIVE for short
    funds (e.g., -2.0).  Since the new column is not yet populated, this
    check flags all short funds to confirm the convention mismatch so the
    Phase 6 backfill can apply the correct sign.
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, map_li_direction, map_li_leverage_amount
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND map_li_direction = 'Short'
        AND map_li_leverage_amount IS NOT NULL
        AND map_li_leverage_amount != ''
        AND CAST(map_li_leverage_amount AS FLOAT) > 0
        ORDER BY CAST(map_li_leverage_amount AS FLOAT) DESC, ticker
        LIMIT 30""")

    total_short = scalar(cur, """
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV'
        AND map_li_direction='Short'
        AND map_li_leverage_amount IS NOT NULL
        AND map_li_leverage_amount != ''
        AND CAST(map_li_leverage_amount AS FLOAT) > 0""")

    return {
        "id": "CX-05",
        "description": "direction='Short' with positive leverage amount (sign convention mismatch)",
        "severity": "MEDIUM",
        "count": total_short,
        "sections": {
            f"Short funds with positive leverage_amount — top 30 of {total_short}": rows,
        },
        "columns": ["Ticker", "Fund Name", "direction", "leverage_amount"],
        "note": (
            "This is a KNOWN CONVENTION ISSUE. The legacy system always stores leverage as "
            "a positive magnitude. The new taxonomy requires negative values for short funds. "
            "Phase 6 backfill must negate map_li_leverage_amount -> leverage_ratio for all "
            "short-direction funds."
        ),
    }


def cx06_cc_no_attributes_entry(cur: sqlite3.Cursor) -> dict:
    """CX-06: etp_category='CC' with no matching row in mkt_category_attributes.

    Every CC-categorised fund should have an attributes row driving display,
    underlier routing, and report inclusion. Missing rows cause silent gaps.
    """
    rows = run_query(cur, """
        SELECT m.ticker, m.fund_name, m.issuer_display, m.cc_type
        FROM mkt_master_data m
        WHERE m.market_status='ACTV'
        AND m.etp_category='CC'
        AND NOT EXISTS (
            SELECT 1 FROM mkt_category_attributes ca WHERE ca.ticker = m.ticker
        )
        ORDER BY m.ticker""")

    return {
        "id": "CX-06",
        "description": "etp_category='CC' funds missing a row in mkt_category_attributes",
        "severity": "HIGH",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "issuer_display", "cc_type"],
    }


def cx07_orphan_underliers(cur: sqlite3.Cursor) -> dict:
    """CX-07: map_li_underlier values that match no ticker in the universe.

    Note on expected behaviour: single-stock underliers are stored in Bloomberg
    format ('AAPL US', 'NVDA US') and WILL NOT match the ETF ticker universe
    because stock tickers are not in mkt_master_data. This is by design.

    This check identifies underliers that are NOT in Bloomberg 'X US' format
    and also do not match any ETF ticker — suggesting a typo, old ticker, or
    index code that may be wrong.
    """
    # Bloomberg-format stocks (X US / X Curncy / X Comdty / X Index) are expected orphans
    # Flag only the plain-format values (no Bloomberg suffix) that find no match
    rows = run_query(cur, """
        SELECT DISTINCT m.map_li_underlier,
               COUNT(*) OVER (PARTITION BY m.map_li_underlier) AS fund_count
        FROM mkt_master_data m
        WHERE m.market_status='ACTV'
        AND m.map_li_underlier IS NOT NULL AND m.map_li_underlier != ''
        AND m.map_li_underlier NOT LIKE '% US'
        AND m.map_li_underlier NOT LIKE '% Curncy'
        AND m.map_li_underlier NOT LIKE '% Comdty'
        AND m.map_li_underlier NOT LIKE '% Index'
        AND m.map_li_underlier NOT IN (
            SELECT DISTINCT ticker FROM mkt_master_data
        )
        ORDER BY m.map_li_underlier
        LIMIT 30""")

    total_orphan_plain = scalar(cur, """
        SELECT COUNT(DISTINCT m.map_li_underlier)
        FROM mkt_master_data m
        WHERE m.market_status='ACTV'
        AND m.map_li_underlier IS NOT NULL AND m.map_li_underlier != ''
        AND m.map_li_underlier NOT LIKE '% US'
        AND m.map_li_underlier NOT LIKE '% Curncy'
        AND m.map_li_underlier NOT LIKE '% Comdty'
        AND m.map_li_underlier NOT LIKE '% Index'
        AND m.map_li_underlier NOT IN (
            SELECT DISTINCT ticker FROM mkt_master_data
        )""")

    total_bloomberg_orphan = scalar(cur, """
        SELECT COUNT(DISTINCT m.map_li_underlier)
        FROM mkt_master_data m
        WHERE m.market_status='ACTV'
        AND m.map_li_underlier IS NOT NULL AND m.map_li_underlier != ''
        AND (m.map_li_underlier LIKE '% US'
             OR m.map_li_underlier LIKE '% Curncy'
             OR m.map_li_underlier LIKE '% Comdty'
             OR m.map_li_underlier LIKE '% Index')
        AND m.map_li_underlier NOT IN (
            SELECT DISTINCT ticker FROM mkt_master_data
        )""")

    return {
        "id": "CX-07",
        "description": "map_li_underlier plain-format values with no matching ETF ticker",
        "severity": "LOW",
        "count": total_orphan_plain,
        "sections": {
            f"Plain-format orphan underliers — top 30 of {total_orphan_plain}": rows,
        },
        "columns": ["map_li_underlier", "Fund count using this underlier"],
        "note": (
            f"{total_bloomberg_orphan} Bloomberg-format underliers (e.g., 'AAPL US') also "
            "have no ETF ticker match. This is EXPECTED — those are stock/commodity tickers. "
            "Only the plain-format values above are potentially problematic (typos, short "
            "ticker variants, or index codes that should reference an ETF underlier)."
        ),
    }


def cx08_nat_inception_actv(cur: sqlite3.Cursor) -> dict:
    """CX-08: ACTV funds with inception_date recorded as 'NaT'.

    'NaT' is a pandas null-timestamp sentinel that leaked into the DB string
    column during a pipeline run. These funds have no usable inception_date,
    which breaks the 30-day AUM check and any time-since-launch calculation.
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, issuer_display, etp_category
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND inception_date='NaT'
        ORDER BY ticker""")

    return {
        "id": "CX-08",
        "description": "ACTV funds with inception_date = 'NaT' (pandas null sentinel leaked to DB)",
        "severity": "MEDIUM",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "issuer_display", "etp_category"],
    }


def cx09_pre1990_inception(cur: sqlite3.Cursor) -> dict:
    """CX-09: ACTV funds with inception_date before 1990-01-01.

    ETFs did not exist before 1993 (SPY was the first US ETF, January 1993).
    Any ACTV fund with an inception date before 1990 is almost certainly a
    Bloomberg data error (fund_type mismatch, wrong CIK rollup, etc.).
    """
    rows = run_query(cur, """
        SELECT ticker, fund_name, inception_date, issuer_display, fund_type
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND inception_date IS NOT NULL
        AND inception_date NOT LIKE '%NaT%'
        AND date(inception_date) < '1990-01-01'
        ORDER BY date(inception_date)""")

    return {
        "id": "CX-09",
        "description": "ACTV funds with inception_date before 1990-01-01 (pre-ETF era)",
        "severity": "MEDIUM",
        "count": len(rows),
        "sections": {
            f"Affected funds ({len(rows)})": rows,
        },
        "columns": ["Ticker", "Fund Name", "inception_date", "issuer_display", "fund_type"],
    }


def cx10_actv_no_aum(cur: sqlite3.Cursor) -> dict:
    """CX-10: ACTV funds with aum IS NULL and inception > 30 days ago.

    A fund trading for more than 30 days should have an AUM figure from
    Bloomberg. A NULL AUM at that age suggests the Bloomberg row is not
    being matched correctly or the fund's ticker changed post-launch.
    """
    rows = run_query(cur, f"""
        SELECT ticker, fund_name, inception_date, issuer_display, aum
        FROM mkt_master_data
        WHERE market_status='ACTV'
        AND aum IS NULL
        AND inception_date IS NOT NULL
        AND inception_date NOT LIKE '%NaT%'
        AND date(inception_date) < '{THIRTY_DAYS_AGO}'
        ORDER BY ticker
        LIMIT 30""")

    total = scalar(cur, f"""
        SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV'
        AND aum IS NULL
        AND inception_date IS NOT NULL
        AND inception_date NOT LIKE '%NaT%'
        AND date(inception_date) < '{THIRTY_DAYS_AGO}'""")

    return {
        "id": "CX-10",
        "description": "ACTV funds with aum IS NULL and inception_date > 30 days ago",
        "severity": "LOW",
        "count": total,
        "sections": {
            f"Affected funds — top 30 of {total}": rows,
        },
        "columns": ["Ticker", "Fund Name", "inception_date", "issuer_display", "aum"],
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(checks: list[dict]) -> str:
    lines: list[str] = []
    a = lines.append

    a("# Cross-Referential Integrity Report — Audit delta")
    a("")
    a(f"**Generated**: {TODAY}  ")
    a(f"**Database**: `data/etp_tracker.db`  ")
    a(f"**Scope**: READ-ONLY — no database writes")
    a("")
    a("## Overview")
    a("")
    a("| Check | Description | Severity | Violations |")
    a("|---|---|---|---|")
    for c in checks:
        a(f"| {c['id']} | {c['description']} | {c['severity']} | {c['count']:,} |")
    a("")
    a("---")
    a("")

    for c in checks:
        a(f"## {c['id']} — {c['description']}")
        a("")
        a(f"**Severity**: {c['severity']}  ")
        a(f"**Violation count**: {c['count']:,}")
        a("")
        if "note" in c:
            a(f"> **Note**: {c['note']}")
            a("")

        for section_name, rows in c.get("sections", {}).items():
            if not rows:
                a(f"*{section_name}: none found.*")
                a("")
                continue
            a(f"### {section_name}")
            a("")
            cols = c.get("columns", [])
            if cols:
                a("| " + " | ".join(cols) + " |")
                a("|" + "---|" * len(cols))
                for row in rows:
                    cells = []
                    for i, val in enumerate(row):
                        cell = str(val or "NULL")
                        if i == 1:  # fund_name — truncate
                            cell = cell[:60]
                        cells.append(cell)
                    a("| " + " | ".join(f"`{c}`" if i == 0 else c for i, c in enumerate(cells)) + " |")
            else:
                for row in rows:
                    a(f"- {row}")
            a("")

        a("---")
        a("")

    # Severity summary
    high = [c for c in checks if c["severity"] == "HIGH" and c["count"] > 0]
    medium = [c for c in checks if c["severity"] == "MEDIUM" and c["count"] > 0]
    low = [c for c in checks if c["severity"] == "LOW" and c["count"] > 0]

    a("## Prioritised fix list")
    a("")
    a("### HIGH severity (fix before next pipeline run)")
    if high:
        for c in sorted(high, key=lambda x: x["count"], reverse=True):
            a(f"- **{c['id']}** ({c['count']:,} violations): {c['description']}")
    else:
        a("*None.*")

    a("")
    a("### MEDIUM severity (fix this week)")
    if medium:
        for c in sorted(medium, key=lambda x: x["count"], reverse=True):
            a(f"- **{c['id']}** ({c['count']:,} violations): {c['description']}")
    else:
        a("*None.*")

    a("")
    a("### LOW severity (track; fix in Phase 6)")
    if low:
        for c in sorted(low, key=lambda x: x["count"], reverse=True):
            a(f"- **{c['id']}** ({c['count']:,} violations): {c['description']}")
    else:
        a("*None.*")

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

    print(f"Connected to {DB_PATH}")
    print(f"Running {10} cross-reference integrity checks...")
    print()

    checks = [
        cx01_li_strategy_mismatch(cur),
        cx02_crypto_is_crypto_mismatch(cur),
        cx03_rex_brand_not_flagged(cur),
        cx04_is_rex_wrong_brand(cur),
        cx05_short_positive_leverage(cur),
        cx06_cc_no_attributes_entry(cur),
        cx07_orphan_underliers(cur),
        cx08_nat_inception_actv(cur),
        cx09_pre1990_inception(cur),
        cx10_actv_no_aum(cur),
    ]

    con.close()

    for c in checks:
        status = "PASS" if c["count"] == 0 else f"FAIL ({c['count']:,} violations)"
        print(f"  {c['id']} [{c['severity']:6s}] {status:30s} {c['description']}")

    report = render_report(checks)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print()
    print(f"Report written to: {REPORT_PATH}")

    # Console summary — top 3 by count
    print()
    print("=== TOP 3 violations by count ===")
    sorted_checks = sorted(checks, key=lambda x: x["count"], reverse=True)
    for c in sorted_checks[:3]:
        print(f"  {c['id']} [{c['severity']}]: {c['count']:,} violations — {c['description']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
