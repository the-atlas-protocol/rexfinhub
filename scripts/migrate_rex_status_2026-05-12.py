"""REX Pipeline status enum collapse — 15 values → 6 (2026-05-12).

Background
----------
The rex_products.status column has accumulated 15 lifecycle states (Research,
Target List, Counsel Review, Counsel Approved, Counsel Withdrawn, Pending
Board, Board Approved, Not Approved by Board, Filed, Filed (485A), Filed
(485B), Awaiting Effective, Effective, Listed, Delisted) plus legacy short
codes (PEND, ACTV, LIQU, INAC, EXPD, DLST). In practice only 6 of those are
in use as of 2026-05-12, and the operations team (per Ryu) wants the enum
collapsed to a clean 6-value lifecycle:

    1. Under Consideration   (Research, Board, Counsel, Pending Board,
                              Board Approved, Counsel Review,
                              Counsel Approved, Counsel Withdrawn,
                              Not Approved by Board)
    2. Filed                 (Filed, Filed (485A), Filed (485B),
                              Awaiting Effective with effective date
                              NOT set OR pre-effective)
    3. Effective             (Effective, Awaiting Effective with date
                              already set / post-485BPOS / pre-launch)
    4. Target List           (Target List — formally targeted for build
                              but not yet under counsel review)
    5. Listed                (Listed, ACTV)
    6. Delisted              (Delisted, LIQU, INAC, EXPD, DLST)

Notes from Ryu
--------------
- "Research" maps to **Under Consideration** (NOT Target List).
- 485A / 485B distinction stays in ``rex_products.latest_form`` — only the
  STATUS field collapses.
- "Awaiting Effective" split: if the row's ``estimated_effective_date`` is
  already populated (i.e. the SEC effective date has been set), treat it
  as **Effective**; otherwise it's still **Filed**.

Audit
-----
Every mutation is written to ``capm_audit_log`` (existing generic audit
table) so the migration is fully reversible from the log. Schema:
    (action, table_name, row_id, field_name, old_value, new_value,
     row_label, changed_by, changed_at)

Usage
-----
    # Dry run (default) — prints mapping plan, no writes.
    python scripts/migrate_rex_status_2026-05-12.py

    # Apply against the test DB copy first.
    python scripts/migrate_rex_status_2026-05-12.py --apply \
        --db data/etp_tracker.test.db

    # Apply against production (only after coordinator review).
    python scripts/migrate_rex_status_2026-05-12.py --apply

The DEFAULT --db path is the production ``data/etp_tracker.db``. The script
will REFUSE to --apply against production unless the operator types the
string "I AGREE" at the interactive confirm. (Or passes
``--i-agree-prod`` non-interactively for CI.)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
PROD_DB_NAME = "etp_tracker.db"

CHANGED_BY = "migrate_rex_status_2026-05-12"

# --- Target lifecycle (6 statuses, ordered left-to-right) ---
TARGET_STATUSES = [
    "Under Consideration",
    "Target List",
    "Filed",
    "Effective",
    "Listed",
    "Delisted",
]

# --- Static (old -> new) mapping table ---
# Conditional cases (Awaiting Effective) are handled in
# ``_resolve_new_status`` because the new value depends on another column.
STATIC_MAPPING: dict[str, str] = {
    # Under Consideration bucket
    "Research":                 "Under Consideration",
    "Counsel Review":           "Under Consideration",
    "Counsel Approved":         "Under Consideration",
    "Counsel Withdrawn":        "Under Consideration",
    "Pending Board":            "Under Consideration",
    "Board Approved":           "Under Consideration",
    "Not Approved by Board":    "Under Consideration",
    "Board":                    "Under Consideration",  # legacy short
    "Counsel":                  "Under Consideration",  # legacy short
    "PEND":                     "Under Consideration",  # legacy code
    # Target List stays
    "Target List":              "Target List",
    "Target":                   "Target List",          # legacy short
    # Filed bucket — 485A/B distinction preserved in latest_form column
    "Filed":                    "Filed",
    "Filed (485A)":             "Filed",
    "Filed (485B)":             "Filed",
    # Effective bucket (literal "Effective" only — Awaiting handled below)
    "Effective":                "Effective",
    # Listed bucket
    "Listed":                   "Listed",
    "ACTV":                     "Listed",
    # Delisted bucket
    "Delisted":                 "Delisted",
    "LIQU":                     "Delisted",
    "INAC":                     "Delisted",
    "EXPD":                     "Delisted",
    "DLST":                     "Delisted",
}


def _resolve_new_status(old_status: str, estimated_effective_date) -> str:
    """Resolve the new status for one row.

    Handles the Awaiting Effective branch: if est_effective_date is set,
    the SEC has assigned an effective date so the product is logically
    Effective; otherwise it's still in Filed.
    """
    if old_status == "Awaiting Effective":
        return "Effective" if estimated_effective_date else "Filed"
    if old_status in STATIC_MAPPING:
        return STATIC_MAPPING[old_status]
    # Unknown / unmapped — return sentinel so the caller can surface it.
    return ""


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _before_distribution(con: sqlite3.Connection) -> list[tuple[str, int]]:
    cur = con.cursor()
    cur.execute(
        "SELECT status, COUNT(*) FROM rex_products "
        "GROUP BY status ORDER BY 2 DESC"
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _plan_migrations(con: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Return (planned_rows, unmapped_rows).

    Each planned_rows entry: {id, ticker, name, old_status, new_status,
                              estimated_effective_date}.
    unmapped_rows: rows whose old_status isn't covered by the mapping.
    """
    cur = con.cursor()
    cur.execute(
        "SELECT id, ticker, name, status, estimated_effective_date "
        "FROM rex_products"
    )
    planned: list[dict] = []
    unmapped: list[dict] = []
    for row in cur.fetchall():
        new = _resolve_new_status(row["status"], row["estimated_effective_date"])
        rec = {
            "id": row["id"],
            "ticker": row["ticker"],
            "name": row["name"],
            "old_status": row["status"],
            "new_status": new,
            "estimated_effective_date": row["estimated_effective_date"],
        }
        if not new:
            unmapped.append(rec)
            continue
        if new != row["status"]:
            planned.append(rec)
    return planned, unmapped


def _ensure_audit_table(con: sqlite3.Connection) -> None:
    """capm_audit_log is created by SQLAlchemy at app boot. Defensive
    no-op if it's missing — we create the same shape so the script can
    run against a stripped DB in dev."""
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS capm_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action VARCHAR(20) NOT NULL,
            table_name VARCHAR(50) NOT NULL,
            row_id INTEGER,
            field_name VARCHAR(100),
            old_value TEXT,
            new_value TEXT,
            row_label VARCHAR(200),
            changed_by VARCHAR(100),
            changed_at DATETIME NOT NULL
        )
        """
    )


def _apply_migrations(
    con: sqlite3.Connection, planned: list[dict]
) -> int:
    cur = con.cursor()
    _ensure_audit_table(con)
    now = datetime.utcnow().isoformat()
    n = 0
    for rec in planned:
        cur.execute(
            "UPDATE rex_products SET status = ?, updated_at = ? WHERE id = ?",
            (rec["new_status"], now, rec["id"]),
        )
        label = f"{rec['ticker'] or '-'} | {rec['name']}"[:200]
        cur.execute(
            "INSERT INTO capm_audit_log "
            "(action, table_name, row_id, field_name, "
            " old_value, new_value, row_label, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "update",
                "rex_products",
                rec["id"],
                "status",
                rec["old_status"],
                rec["new_status"],
                label,
                CHANGED_BY,
                now,
            ),
        )
        n += 1
    con.commit()
    return n


def _print_distribution(label: str, dist: list[tuple[str, int]]) -> None:
    print(f"--- {label} ---")
    total = sum(c for _, c in dist)
    for status, count in dist:
        pct = 100.0 * count / total if total else 0.0
        print(f"  {status!r:32s} {count:5d}  ({pct:5.1f}%)")
    print(f"  {'TOTAL':32s} {total:5d}")
    print()


def _summarize_planned(planned: list[dict]) -> dict[tuple[str, str], int]:
    summary: dict[tuple[str, str], int] = {}
    for rec in planned:
        key = (rec["old_status"], rec["new_status"])
        summary[key] = summary.get(key, 0) + 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collapse rex_products.status enum from 15 → 6 values."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Default is a dry run.",
    )
    parser.add_argument(
        "--i-agree-prod",
        action="store_true",
        help=(
            "Non-interactive consent to mutate the production DB. Required "
            "with --apply when --db points at etp_tracker.db."
        ),
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 2

    is_prod = db_path.name == PROD_DB_NAME

    print(f"DB:        {db_path}")
    print(f"Mode:      {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Prod DB:   {'YES' if is_prod else 'no (test/dev copy)'}")
    print()

    con = _connect(db_path)
    try:
        before = _before_distribution(con)
        _print_distribution("BEFORE", before)

        planned, unmapped = _plan_migrations(con)

        if unmapped:
            print(f"!! {len(unmapped)} row(s) have an unmapped old_status:")
            for r in unmapped[:20]:
                print(f"   id={r['id']:5d}  ticker={r['ticker'] or '-':<8}  status={r['old_status']!r}")
            if len(unmapped) > 20:
                print(f"   ... and {len(unmapped) - 20} more")
            print()
            print("Aborting — add these to STATIC_MAPPING before re-running.")
            return 3

        # Synthesize projected after-distribution by simulation.
        sim: dict[str, int] = {}
        cur = con.cursor()
        cur.execute("SELECT status, estimated_effective_date FROM rex_products")
        for s, eed in cur.fetchall():
            new = _resolve_new_status(s, eed)
            sim[new] = sim.get(new, 0) + 1
        projected = sorted(sim.items(), key=lambda kv: -kv[1])
        _print_distribution("PROJECTED AFTER", projected)

        summary = _summarize_planned(planned)
        print(f"--- MAPPING PLAN  ({len(planned)} row updates) ---")
        for (old, new), n in sorted(summary.items(), key=lambda kv: -kv[1]):
            print(f"  {old!r:32s} -> {new!r:24s} : {n} rows")
        print()

        if not args.apply:
            print("Dry run — no writes. Re-run with --apply to mutate.")
            return 0

        if is_prod and not args.i_agree_prod:
            print("You are about to mutate the PRODUCTION DB.")
            print("Type 'I AGREE' to proceed: ", end="", flush=True)
            answer = sys.stdin.readline().strip()
            if answer != "I AGREE":
                print("Aborted.")
                return 4

        n_written = _apply_migrations(con, planned)
        print(f"OK — {n_written} row(s) updated, audit log written.")
        after = _before_distribution(con)
        _print_distribution("ACTUAL AFTER", after)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
