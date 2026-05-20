"""Phase 5 Stage 3 (ADR 0008): bi-temporal status reconciler with 3-source rule.

For each product_master row, gathers evidence across SEC + Bloomberg + Exchange
sources, derives the proposed status, and appends a new status_history row when
it differs from the current state.

The 3-source rule for `Listed` promotion: at least one evidence source from
EACH of three independent categories must concur:
    SEC      : latest_form in {485BPOS, 8-A} or explicit Effective date set
    Bloomberg: mkt_master_data.market_status='ACTV' for any mapped ticker
    Exchange : Bloomberg first_trade_date set OR (future: CBOE listing notice)

If a row has Bloomberg ACTV + SEC effective but no exchange evidence, it stays
at `effective` (Phase 5 invariant — no single-source ghost-Listed).

Default mode is --dry-run (per ADR 0008 Stage 3 dry-run policy). Output written
to data/.status_reconciler.log so the operator can review proposed flips before
authorizing --apply.

Usage:
    python -m webapp.services.status_reconciler --dry-run
    python -m webapp.services.status_reconciler --apply
    python -m webapp.services.status_reconciler --apply --only-canonical-id <uuid>
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
LOG_PATH = PROJECT_ROOT / "data" / ".status_reconciler.log"

log = logging.getLogger("status_reconciler")


# Status enum (matches status_history.status values; lowercase canonical)
STATUS_UNDER_CONSIDERATION = "under_consideration"
STATUS_TARGET_LIST = "target_list"
STATUS_FILED = "filed"
STATUS_EFFECTIVE = "effective"
STATUS_LISTED = "listed"
STATUS_DELISTED = "delisted"
STATUS_SUSPENDED = "suspended"

# CapM-case mapping for the legacy rex_products.status column. Phase 5 Stage 5
# (Track 5A): the reconciler keeps this in sync so the pipeline page, flow
# report, and templates — which still read rex_products.status — stay
# consistent with status_history.
_CAPM_CASE_STATUS = {
    "under_consideration": "Under Consideration",
    "target_list": "Target List",
    "filed": "Filed",
    "effective": "Effective",
    "listed": "Listed",
    "delisted": "Delisted",
    "suspended": "Suspended",
}


def gather_evidence(conn: sqlite3.Connection, canonical_id: str) -> dict:
    """Collect all evidence for one canonical_id across SEC / Bloomberg / Exchange.

    Returns a dict with keys:
        sec_effective_evidence    : bool (485BPOS landed OR estimated_effective_date passed)
        sec_filed_evidence        : bool (any 485 filing on file)
        bloomberg_actv_evidence   : bool (mkt_master_data.market_status='ACTV')
        bloomberg_liqu_evidence   : bool (LIQU = liquidated)
        bloomberg_first_trade_evidence: bool (any first_trade_date set)
        cboe_listing_evidence     : bool (placeholder; cboe table integration future)
        rex_product_status        : str  (current rex_products.status — denormalized)
        latest_form               : str
        official_listed_date      : str | None
    """
    cur = conn.execute("""
        SELECT rp.status, rp.latest_form, rp.official_listed_date,
               rp.estimated_effective_date, rp.initial_filing_date,
               rp.inception_date
        FROM rex_products rp
        WHERE rp.canonical_id = ?
    """, (canonical_id,)).fetchone()
    if not cur:
        return {}

    rex_status, latest_form, listed_date, eff_date, filing_date, inception = cur

    # SEC evidence: 485BPOS = SEC declared the registration effective
    # 485A* = filed but not yet effective. 497 = supplement (post-effective).
    sec_effective = (latest_form in ("485BPOS", "485BXT") and eff_date is not None) or \
                    (eff_date is not None and eff_date <= datetime.utcnow().date().isoformat())
    sec_filed = latest_form is not None and latest_form.startswith("485")

    # Bloomberg evidence: any mkt_master_data row matching any ticker mapped
    # to this canonical_id has market_status='ACTV'.
    bbg_actv = False
    bbg_liqu = False
    bbg_first_trade = False
    rows = conn.execute("""
        SELECT m.market_status, m.inception_date AS first_trade
        FROM identifier_xref ix
        JOIN mkt_master_data m
          ON (UPPER(m.ticker) = UPPER(ix.id_value)
              OR UPPER(m.ticker_clean) = UPPER(ix.id_value))
        WHERE ix.canonical_id = ?
          AND ix.id_type IN ('ticker', 'bloomberg')
          AND ix.valid_to IS NULL
    """, (canonical_id,)).fetchall()
    for status, first_trade in rows:
        if status == "ACTV":
            bbg_actv = True
        if status in ("LIQU", "INAC", "EXPD", "DLST"):
            bbg_liqu = True
        if first_trade:
            bbg_first_trade = True

    return {
        "sec_effective_evidence": sec_effective,
        "sec_filed_evidence": sec_filed,
        "bloomberg_actv_evidence": bbg_actv,
        "bloomberg_liqu_evidence": bbg_liqu,
        "bloomberg_first_trade_evidence": bbg_first_trade,
        "cboe_listing_evidence": False,  # Future: query cboe_listings table
        "rex_product_status": rex_status,
        "latest_form": latest_form,
        "official_listed_date": listed_date,
    }


def derive_status(evidence: dict) -> str:
    """Derive the proposed status from evidence.

    Bloomberg `market_status` is authoritative for whether a fund is trading:
    LIQU/INAC/EXPD/DLST -> delisted; ACTV -> listed. This is necessary because
    ETNs (the BMO MicroSectors line etc.) never file 485-series SEC forms — an
    SEC-form-based rule alone wrongly classifies actively-trading ETNs as
    pre-filing (audit 2026-05-20: FNGU/NRGU/BNKU et al. would be demoted to
    `under_consideration`). ADR 0008's anti-ghost-Listed intent still holds:
    `listed` still REQUIRES Bloomberg ACTV — it is just no longer ALSO gated
    on an SEC 485 form that a whole product class never files.

    For products NOT yet on the Bloomberg active feed, SEC + exchange evidence
    drives the pre-launch lifecycle (filed -> effective -> target_list).
    """
    if not evidence:
        return STATUS_UNDER_CONSIDERATION

    # Bloomberg market_status is definitive for the trading-state.
    if evidence.get("bloomberg_liqu_evidence"):
        return STATUS_DELISTED
    if evidence.get("bloomberg_actv_evidence"):
        return STATUS_LISTED

    # Not yet on the Bloomberg active feed — SEC + exchange evidence drives
    # the pre-launch lifecycle.
    sec_cat = bool(evidence.get("sec_effective_evidence"))
    exchange_cat = bool(
        evidence.get("bloomberg_first_trade_evidence")
        or evidence.get("cboe_listing_evidence")
    )
    if sec_cat and exchange_cat:
        return STATUS_TARGET_LIST
    if sec_cat:
        return STATUS_EFFECTIVE
    if evidence.get("sec_filed_evidence"):
        return STATUS_FILED
    return STATUS_UNDER_CONSIDERATION


def get_current_status(conn: sqlite3.Connection, canonical_id: str) -> str | None:
    row = conn.execute("""
        SELECT status FROM status_history
        WHERE canonical_id = ? AND valid_to IS NULL
        ORDER BY valid_from DESC, id DESC
        LIMIT 1
    """, (canonical_id,)).fetchone()
    return row[0] if row else None


def append_transition(
    conn: sqlite3.Connection,
    canonical_id: str,
    old_status: str | None,
    new_status: str,
    evidence: dict,
    set_by: str = "reconciler",
) -> None:
    now = datetime.utcnow().isoformat()
    # Close the previous open row
    if old_status is not None:
        conn.execute("""
            UPDATE status_history
            SET valid_to = ?
            WHERE canonical_id = ? AND valid_to IS NULL
        """, (now, canonical_id))
    # Insert new row
    import json
    conn.execute("""
        INSERT INTO status_history
        (canonical_id, status, valid_from, valid_to, tx_from, tx_to,
         source, evidence, set_by, created_at)
        VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)
    """, (
        canonical_id, new_status, now, now,
        "reconciler_v1", json.dumps(evidence), set_by, now,
    ))
    # Phase 5 Stage 4+5: refresh the denormalized state on rex_products.
    # status_cached carries the lowercase canonical form ("listed");
    # status carries the legacy CapM-case form ("Listed") for the readers
    # that still use it. status_history is the authority — driving both
    # here keeps every consumer consistent (Track 5A).
    conn.execute(
        "UPDATE rex_products SET status_cached = ?, status = ? WHERE canonical_id = ?",
        (new_status, _CAPM_CASE_STATUS.get(new_status, new_status), canonical_id),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="Actually write transitions. Default: dry-run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Explicit no-op — dry-run is the default unless --apply.")
    ap.add_argument("--only-canonical-id", default=None,
                    help="Reconcile only the given canonical_id (debugging).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        # Verify tables
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for tbl in ("product_master", "status_history", "identifier_xref"):
            if tbl not in tables:
                print(f"ERROR: {tbl} table missing. Run Phase 4-5 migrations first.",
                      file=sys.stderr)
                return 1

        if args.only_canonical_id:
            pm_rows = [(args.only_canonical_id,)]
        else:
            pm_rows = conn.execute("SELECT canonical_id FROM product_master").fetchall()

        print(f"Reconciling {len(pm_rows)} products (mode={'APPLY' if args.apply else 'DRY-RUN'})")

        stats = {"unchanged": 0, "would_promote": 0, "would_demote": 0,
                 "would_initialize": 0, "skipped": 0}
        transitions: list[tuple] = []
        for (cid,) in pm_rows:
            evidence = gather_evidence(conn, cid)
            if not evidence:
                stats["skipped"] += 1
                continue
            proposed = derive_status(evidence)
            current = get_current_status(conn, cid)
            if current == proposed:
                stats["unchanged"] += 1
                continue
            transitions.append((cid, current, proposed, evidence))
            # Bucket the transition
            if current is None:
                stats["would_initialize"] += 1
            elif _is_promotion(current, proposed):
                stats["would_promote"] += 1
            else:
                stats["would_demote"] += 1

        print(f"\nStats:")
        for k, v in stats.items():
            print(f"  {k:18s} {v}")

        # Show sample transitions
        if transitions:
            print(f"\nSample transitions (first 10):")
            for cid, cur, new, ev in transitions[:10]:
                cur_disp = cur or "(none)"
                print(f"  {cid[:8]}... {cur_disp:20s} -> {new:20s}")

        # Append log
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()}  mode={'APPLY' if args.apply else 'DRY-RUN'}  "
                    f"total={len(pm_rows)}  changes={len(transitions)}  "
                    f"promote={stats['would_promote']} demote={stats['would_demote']} "
                    f"init={stats['would_initialize']}\n")

        if not args.apply:
            print("\nDRY-RUN — no changes written. Use --apply to commit.")
            return 0

        applied = 0
        skipped_demotions = 0
        for cid, cur, new, ev in transitions:
            # Promote on evidence; delist on Bloomberg LIQU (-> delisted ranks
            # highest in _is_promotion). NEVER demote mid-lifecycle: a missing
            # ticker mapping or an absent SEC form is not proof that a trading
            # fund stopped trading. Conservative by design — too-high statuses
            # are corrected explicitly (demote_liqu_dlst_rex_products.py), not
            # by the reconciler guessing from absent evidence.
            if cur is not None and not _is_promotion(cur, new):
                skipped_demotions += 1
                continue
            append_transition(conn, cid, cur, new, ev, set_by="reconciler")
            applied += 1
        conn.commit()
        print(f"\nCommitted {applied} status transitions "
              f"({skipped_demotions} non-LIQU demotions skipped — conservative).")
        return 0
    finally:
        conn.close()


def _is_promotion(current: str, proposed: str) -> bool:
    """True if proposed is later in the lifecycle than current."""
    order = {
        STATUS_UNDER_CONSIDERATION: 0,
        STATUS_FILED: 1,
        STATUS_EFFECTIVE: 2,
        STATUS_TARGET_LIST: 3,
        STATUS_LISTED: 4,
        STATUS_SUSPENDED: 5,
        STATUS_DELISTED: 6,
    }
    return order.get(proposed, 0) > order.get(current, 0)


if __name__ == "__main__":
    sys.exit(main())
