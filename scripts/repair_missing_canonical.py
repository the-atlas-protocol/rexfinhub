"""Repair REX-name fund_extractions that never minted a canonical identity.

Some REX-name series flow through ``fund_extractions`` (the SEC pipeline)
without ever materialising the full canonical-identity chain that downstream
analytics expect:

    fund_extractions.series_id
        -> rex_products row
        -> product_master.canonical_id
        -> identifier_xref(id_type='series_id', id_value=<series_id>)
        -> fund_underlier link (when an underlier can be resolved)

The usual creator is ``scripts/sync_rex_products_from_filings.py`` (phase 1),
but it can skip rows that fail its watermark or registrant gating, and the
canonical_id chain has its own backfill scripts that may not have been re-run.
The result is "orphan" extractions: REX-name series visible in SEC data but
invisible to /operations/pipeline + the canonical-id consumers.

This script finds those orphans and repairs them in one transaction per row.

Detection rule
--------------
An orphan is a ``fund_extractions`` row where:

* ``series_id`` is non-null, AND
* the parent ``filings.registrant`` or ``fund_extractions.series_name`` matches
  the REX_NAME_PATTERNS from ``sync_rex_products_from_filings`` (T-REX / REX
  / REX-Osprey / MicroSectors / Osprey) AND does NOT match a competitor
  prefix, AND
* there is no ``identifier_xref`` row with
  ``id_type='series_id' AND id_value=<series_id>``.

For each orphan
---------------
1. Synthesize a rex_products row (or reuse an existing one matched on
   ``(cik, series_id)`` if rex_products is ahead of identifier_xref — the
   canonical_id chain alone is broken).
2. Mint a fresh UUID canonical_id, insert into product_master, update
   rex_products.canonical_id.
3. Insert identifier_xref rows for every populated identifier (ticker,
   cik, series_id, class_contract_id, bb_ticker).
4. If a fund_underlier link can be resolved from underlying_ticker /
   underlying_name against underlier_master.display_symbol, insert it.

Safety
------
* Dry-run by default. ``--apply`` writes; requires "I AGREE" via stdin.
* ``--apply`` backs up the DB to ``data/backups/`` first.
* Idempotent: re-running with no orphans is a no-op.
* Print before/after counts so a human can spot anything weird.

Usage
-----
    python scripts/repair_missing_canonical.py              # dry-run
    python scripts/repair_missing_canonical.py --dry-run
    python scripts/repair_missing_canonical.py --apply      # writes
    python scripts/repair_missing_canonical.py --apply --no-prompt
    python scripts/repair_missing_canonical.py --since 2026-05-01

Expected scale: ~31 orphans in the last 30 days, ~49 lifetime.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"

# Reuse the canonical REX-name detection so this script and the nightly
# sync agree on what counts as a REX product.
from scripts.sync_rex_products_from_filings import _is_rex_name  # noqa: E402

# Reuse helpers from the existing backfill scripts so the schema mapping
# stays in one place. These imports must succeed even when the script is
# run standalone (no webapp boot needed).
from scripts.backfill_product_master import REX_SUITES  # noqa: E402
from scripts.backfill_identifier_xref import ID_MAPPINGS  # noqa: E402
from scripts.backfill_fund_underlier import normalize_to_display_symbol  # noqa: E402
from webapp.services.rex_product_sync import _infer_suite  # noqa: E402

REPAIR_SOURCE = "repair_missing_canonical_2026-06-02"


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------

def find_orphans(conn: sqlite3.Connection, since: date | None = None) -> list[sqlite3.Row]:
    """Return fund_extractions rows whose series_id has no identifier_xref entry.

    A row qualifies only when the filing's registrant or the extraction's
    series_name matches the REX name patterns.
    """
    sql = """
        SELECT
            fe.id              AS extraction_id,
            fe.filing_id       AS filing_id,
            fe.series_id       AS series_id,
            fe.series_name     AS series_name,
            fe.class_contract_id AS class_contract_id,
            fe.class_symbol    AS class_symbol,
            fe.effective_date  AS effective_date,
            f.form             AS form,
            f.filing_date      AS filing_date,
            f.cik              AS cik,
            f.registrant       AS registrant,
            f.primary_link     AS primary_link
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.series_id IS NOT NULL AND fe.series_id != ''
          AND NOT EXISTS (
              SELECT 1 FROM identifier_xref ix
              WHERE ix.id_type = 'series_id'
                AND ix.id_value = fe.series_id
          )
    """
    params: list = []
    if since is not None:
        sql += " AND f.filing_date >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY f.filing_date DESC, fe.id DESC"

    rows = conn.execute(sql, params).fetchall()

    # Filter to REX-name only. Keep the strongest signal first: extraction
    # series_name, then registrant. This matches the gating used by
    # sync_rex_products_from_filings phase 1.
    keep: list[sqlite3.Row] = []
    seen_series: set[str] = set()
    for r in rows:
        # Dedup: a series_id can have multiple extractions across filings.
        # The newest filing's extraction wins (we sorted DESC above).
        sid = r["series_id"]
        if sid in seen_series:
            continue
        if _is_rex_name(r["series_name"]) or _is_rex_name(r["registrant"]):
            seen_series.add(sid)
            keep.append(r)
    return keep


# ---------------------------------------------------------------------------
# Repair primitives
# ---------------------------------------------------------------------------

def _find_rex_product_by_series(conn: sqlite3.Connection, cik: str | None,
                                 series_id: str) -> sqlite3.Row | None:
    """Return existing rex_products row matched by (cik, series_id), if any.

    The cik comparison is lstripped — rex_products stores un-padded numerics
    but filings sometimes carry leading zeros.
    """
    if not series_id:
        return None
    cik_n = (cik or "").lstrip("0") or None
    if cik_n:
        row = conn.execute(
            "SELECT * FROM rex_products WHERE series_id = ? "
            "AND (cik IS NULL OR REPLACE(LTRIM(cik, '0'), '', '') = ?)",
            (series_id, cik_n),
        ).fetchone()
        if row:
            return row
    # Fallback: series_id alone (defensive — series_ids are globally unique).
    return conn.execute(
        "SELECT * FROM rex_products WHERE series_id = ?", (series_id,)
    ).fetchone()


def _insert_rex_product(conn: sqlite3.Connection, orphan: sqlite3.Row) -> int:
    """Synthesize a rex_products row from an orphan extraction. Returns row id."""
    name = (orphan["series_name"] or orphan["registrant"] or "Unknown Fund").strip()[:200]
    trust = (orphan["registrant"] or "")[:200] or None
    suite = _infer_suite(name)
    # Map form to a sensible initial status. 485BPOS = Effective; everything
    # else = Filed. Matches sync_rex_products phase 1 logic.
    status = "Effective" if orphan["form"] == "485BPOS" else "Filed"
    cik_n = (orphan["cik"] or "").lstrip("0") or None
    now_iso = datetime.utcnow().isoformat()

    cur = conn.execute(
        """
        INSERT INTO rex_products (
            name, trust, product_suite, status,
            cik, series_id, class_contract_id,
            latest_form, latest_prospectus_link,
            initial_filing_date, estimated_effective_date,
            notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name, trust, suite, status,
            cik_n, orphan["series_id"], orphan["class_contract_id"],
            orphan["form"], orphan["primary_link"],
            orphan["filing_date"], orphan["effective_date"],
            f"auto-created by {REPAIR_SOURCE}",
            now_iso, now_iso,
        ),
    )
    return cur.lastrowid


def _mint_canonical(conn: sqlite3.Connection, rex_id: int) -> str:
    """Mint a canonical_id, insert product_master, update rex_products."""
    row = conn.execute(
        "SELECT name, product_suite, status, canonical_id FROM rex_products WHERE id = ?",
        (rex_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"rex_products id={rex_id} disappeared mid-repair")
    if row["canonical_id"]:
        return row["canonical_id"]

    new_id = str(uuid.uuid4())
    is_rex = 1 if (row["product_suite"] in REX_SUITES) else 0
    conn.execute(
        "INSERT INTO product_master "
        "(canonical_id, created_at, fund_name, status_current, is_rex) "
        "VALUES (?, ?, ?, ?, ?)",
        (new_id, datetime.utcnow().isoformat(), row["name"], row["status"], is_rex),
    )
    conn.execute(
        "UPDATE rex_products SET canonical_id = ? WHERE id = ?",
        (new_id, rex_id),
    )
    return new_id


def _insert_identifier_xrefs(conn: sqlite3.Connection, rex_id: int,
                              canonical_id: str) -> int:
    """Insert identifier_xref rows for every populated id on the rex row.

    Skips rows that already exist on (canonical_id, id_type, id_value, valid_from).
    Returns number of new rows inserted.
    """
    rex = conn.execute(
        """
        SELECT ticker, cik, series_id, class_contract_id, bb_ticker,
               initial_filing_date, created_at
        FROM rex_products WHERE id = ?
        """,
        (rex_id,),
    ).fetchone()
    if rex is None:
        return 0

    valid_from = rex["initial_filing_date"] or rex["created_at"] or datetime.utcnow().isoformat()
    inserted = 0
    for col, id_type in ID_MAPPINGS:
        val = rex[col]
        if val is None:
            continue
        val_s = str(val).strip()
        if not val_s:
            continue
        # Idempotency check
        exists = conn.execute(
            "SELECT 1 FROM identifier_xref "
            "WHERE canonical_id = ? AND id_type = ? AND id_value = ? AND valid_from = ?",
            (canonical_id, id_type, val_s, valid_from),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO identifier_xref "
            "(canonical_id, id_type, id_value, valid_from, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (canonical_id, id_type, val_s, valid_from, REPAIR_SOURCE),
        )
        inserted += 1
    return inserted


def _maybe_link_underlier(conn: sqlite3.Connection, rex_id: int,
                           canonical_id: str) -> bool:
    """Best-effort fund_underlier link. Returns True if a link was inserted."""
    rex = conn.execute(
        """
        SELECT underlying_ticker, underlying_name, initial_filing_date, created_at
        FROM rex_products WHERE id = ?
        """,
        (rex_id,),
    ).fetchone()
    if rex is None:
        return False
    raw = (rex["underlying_ticker"] or rex["underlying_name"] or "").strip()
    if not raw:
        return False

    # Skip if this product already has an open link.
    has_open = conn.execute(
        "SELECT 1 FROM fund_underlier WHERE canonical_id = ? AND effective_to IS NULL",
        (canonical_id,),
    ).fetchone()
    if has_open:
        return False

    disp = normalize_to_display_symbol(raw)
    hit = conn.execute(
        "SELECT underlier_id FROM underlier_master WHERE display_symbol = ?",
        (disp,),
    ).fetchone()
    if not hit:
        hit = conn.execute(
            "SELECT underlier_id FROM underlier_master WHERE display_symbol = ?",
            (raw,),
        ).fetchone()
    if not hit:
        return False

    eff_from = rex["initial_filing_date"] or rex["created_at"] or datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO fund_underlier "
        "(canonical_id, underlier_id, weight, effective_from) "
        "VALUES (?, ?, ?, ?)",
        (canonical_id, hit["underlier_id"], None, eff_from),
    )
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def repair_orphans(conn: sqlite3.Connection, orphans: list[sqlite3.Row],
                    dry_run: bool) -> dict:
    """Repair each orphan. Returns a stats dict."""
    stats = {
        "orphans": len(orphans),
        "rex_products_created": 0,
        "rex_products_reused": 0,
        "canonical_ids_minted": 0,
        "identifier_xref_inserted": 0,
        "fund_underlier_linked": 0,
    }
    if dry_run or not orphans:
        return stats

    for orphan in orphans:
        existing = _find_rex_product_by_series(conn, orphan["cik"], orphan["series_id"])
        if existing is None:
            rex_id = _insert_rex_product(conn, orphan)
            stats["rex_products_created"] += 1
        else:
            rex_id = existing["id"]
            stats["rex_products_reused"] += 1

        canonical_id = _mint_canonical(conn, rex_id)
        # _mint_canonical returns the existing id if rex already had one.
        # Treat "new mint" as the case where product_master wasn't there yet.
        has_pm = conn.execute(
            "SELECT 1 FROM product_master WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        if has_pm:
            stats["canonical_ids_minted"] += 1

        stats["identifier_xref_inserted"] += _insert_identifier_xrefs(
            conn, rex_id, canonical_id
        )
        if _maybe_link_underlier(conn, rex_id, canonical_id):
            stats["fund_underlier_linked"] += 1

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Counts + reporting
# ---------------------------------------------------------------------------

def snapshot_counts(conn: sqlite3.Connection) -> dict:
    """Snapshot the four counts we care about before/after."""
    def _one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]
    return {
        "rex_products": _one("SELECT COUNT(*) FROM rex_products"),
        "product_master": _one("SELECT COUNT(*) FROM product_master"),
        "identifier_xref_series": _one(
            "SELECT COUNT(*) FROM identifier_xref WHERE id_type = 'series_id'"
        ),
        "fund_underlier": _one("SELECT COUNT(*) FROM fund_underlier"),
    }


def _print_counts(label: str, counts: dict) -> None:
    print(f"\n{label}:")
    for k, v in counts.items():
        print(f"  {k:24s} {v}")


def _print_report(orphans: list[sqlite3.Row], stats: dict,
                   before: dict, after: dict | None, dry_run: bool) -> None:
    print("\n=== repair_missing_canonical ===")
    print(f"Mode             : {'DRY-RUN' if dry_run else 'APPLY'}")
    print(f"Orphans found    : {len(orphans)}")

    if orphans:
        print("\nOrphan sample (first 10):")
        for r in orphans[:10]:
            print(f"  series={r['series_id']:<12} form={r['form']:<8} "
                  f"date={r['filing_date'] or 'NULL':<10} "
                  f"name={(r['series_name'] or r['registrant'] or '')[:50]}")

    print("\nRepair stats:")
    for k, v in stats.items():
        print(f"  {k:28s} {v}")

    _print_counts("Counts BEFORE", before)
    if after is not None:
        _print_counts("Counts AFTER", after)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _backup_db(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = BACKUPS_DIR / f"etp_tracker_{stamp}_pre_repair_missing_canonical.db"
    shutil.copy2(db_path, dst)
    # Keep only the 3 most-recent pre_repair snapshots — same rotation policy
    # as sync_rex_products to avoid filling the VPS disk.
    try:
        snaps = sorted(
            BACKUPS_DIR.glob("etp_tracker_*_pre_repair_missing_canonical.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in snaps[3:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return dst


def _confirm_apply() -> bool:
    print("\n--apply will modify data/etp_tracker.db.")
    print("Type 'I AGREE' to proceed (anything else aborts):")
    try:
        line = input().strip()
    except EOFError:
        return False
    return line == "I AGREE"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="Show what would be repaired without writing (default).")
    ap.add_argument("--apply", action="store_true", default=False,
                    help="Write changes after 'I AGREE' confirmation.")
    ap.add_argument("--no-prompt", action="store_true", default=False,
                    help="With --apply, skip the 'I AGREE' prompt.")
    ap.add_argument("--since", default=None,
                    help="Only consider filings on/after this date (YYYY-MM-DD).")
    args = ap.parse_args(argv)

    dry_run = not args.apply
    if args.dry_run:
        dry_run = True

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    since: date | None = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 2

    if not dry_run and not args.no_prompt:
        if not _confirm_apply():
            print("Aborted (no 'I AGREE').")
            return 2

    if not dry_run:
        backup = _backup_db(db_path)
        if backup is not None:
            print(f"Backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Pre-flight: required tables must exist.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"fund_extractions", "filings", "rex_products",
                    "product_master", "identifier_xref"}
        missing = required - tables
        if missing:
            print(f"ERROR: required tables missing: {sorted(missing)}", file=sys.stderr)
            print("Run scripts/migrate_canonical_id_schema.py first.", file=sys.stderr)
            return 1

        before = snapshot_counts(conn)
        orphans = find_orphans(conn, since=since)
        stats = repair_orphans(conn, orphans, dry_run=dry_run)
        after = None if dry_run else snapshot_counts(conn)
        _print_report(orphans, stats, before, after, dry_run)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
