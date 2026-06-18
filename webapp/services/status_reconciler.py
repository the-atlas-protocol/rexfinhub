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
STATUS_FILED = "filed"          # 485APOS on file, in SEC review (a.k.a. pending)
STATUS_DELAYED = "delayed"      # 485BXT pushed the effective date (not yet effective)
STATUS_EFFECTIVE = "effective"  # 485BPOS registration effective + date arrived; NOT trading
STATUS_LISTED = "listed"        # own ticker trading (Bloomberg ACTV) — actually live
STATUS_DELISTED = "delisted"
STATUS_SUSPENDED = "suspended"

# CapM-case mapping for the legacy rex_products.status column. Phase 5 Stage 5
# (Track 5A): the reconciler keeps this in sync so the pipeline page, flow
# report, and templates — which still read rex_products.status — stay
# consistent with status_history.
# Ryu (repeated): the ONLY statuses the DB may store / a report may show are
# Filed, Effective, Listed, Liquidated, Delisted, and Under Consideration (pipeline
# ideas). A 485BXT "delay" is still Filed; a target-list idea is Under Consideration;
# suspended collapses to Delisted. NO 'Delayed', 'Target List', 'Suspended', 'Pending'.
_CAPM_CASE_STATUS = {
    "under_consideration": "Under Consideration",
    "target_list": "Under Consideration",
    "filed": "Filed",
    "delayed": "Filed",
    "effective": "Effective",
    "listed": "Listed",
    "delisted": "Delisted",
    "suspended": "Delisted",
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

    # SEC evidence — corrected model (2026-06-09, effectiveness overhaul):
    #   485APOS = filed, in review (pending). NOT effective.
    #   485BXT  = a DELAY of a pending 485APOS; carries the new scheduled
    #             effective date. NOT effective — it means effectiveness moved.
    #   485BPOS = registration effective ON its effective date (immediate / <=30d).
    # A registration is "effective" ONLY when a 485BPOS has landed AND its
    # effective date has actually arrived (<= today). A future-dated 485BPOS is
    # filed-not-yet-effective. We no longer treat "estimated date passed" or the
    # mere presence of a 485BXT as effectiveness (that mislabeled 128 L&I rows).
    _today = datetime.utcnow().date().isoformat()
    # Effective = a 485BPOS landed and its effective date arrived, OR the latest
    # form is a 497/497K (definitive prospectus filed AFTER effectiveness — it
    # implies the registration is already effective).
    sec_effective = (latest_form == "485BPOS" and eff_date is not None and eff_date <= _today) \
                    or (latest_form in ("497", "497K"))
    # Delayed: the most recent 485 form is a 485BXT (extension) — effectiveness
    # has been pushed and a 485BPOS has not yet superseded it.
    sec_delayed = (latest_form == "485BXT")
    sec_filed = latest_form is not None and latest_form.startswith("485")

    # Bloomberg evidence: mkt_master_data row matching the fund's OWN
    # canonical ticker (rex_products.ticker). BUG-14 fix (2026-06-08): the
    # prior query joined identifier_xref.id_value -> mkt_master_data.ticker,
    # which picked up ANY mapped ticker — sibling fund, underlying stock,
    # recycled ticker — and false-promoted funds to Listed on unrelated
    # ACTV signals. Joining through rex_products.ticker gates on the fund's
    # own ticker only.
    bbg_actv = False
    bbg_liqu = False
    bbg_first_trade = False
    rows = conn.execute("""
        SELECT m.market_status, m.inception_date AS first_trade
        FROM rex_products rp
        JOIN mkt_master_data m
          ON (UPPER(TRIM(m.ticker)) = UPPER(TRIM(rp.ticker))
              OR UPPER(TRIM(m.ticker_clean)) = UPPER(TRIM(rp.ticker)))
        WHERE rp.canonical_id = ?
          AND rp.ticker IS NOT NULL
          AND TRIM(rp.ticker) != ''
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
        "sec_delayed_evidence": sec_delayed,
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

    # Not yet on the Bloomberg active feed — SEC evidence drives the pre-launch
    # lifecycle: filed (485APOS) -> delayed (485BXT) -> effective (485BPOS
    # arrived). "effective" means the REGISTRATION is effective; the fund is not
    # trading until it appears ACTV on Bloomberg with its own ticker (handled
    # above). Order matters: a confirmed-effective 485BPOS outranks an earlier
    # 485BXT delay.
    if evidence.get("sec_effective_evidence"):
        return STATUS_EFFECTIVE
    if evidence.get("sec_delayed_evidence"):
        return STATUS_DELAYED
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
    ap.add_argument("--assert-noop", action="store_true",
                    help="Stage A guard: derive status read-only and FAIL (exit 2) if any "
                         "transition would be applied — i.e. assert status was already "
                         "written correctly at the source. Escalates once. No writes.")
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

        # --- Stage A: assert-noop guard --------------------------------------
        # Status should already be correct as written at the source. Count the
        # transitions that WOULD actually be applied (promotions + narrow
        # correction-demotions; conservative skipped demotions don't count). Any
        # such transition means status was NOT correct at source -> fail loudly.
        if args.assert_noop:
            actionable = [
                (cid, cur, new, ev) for (cid, cur, new, ev) in transitions
                if cur is None or _is_promotion(cur, new) or _is_correction_demotion(cur, new, ev)
            ]
            if actionable:
                print(f"\nASSERT-NOOP FAILED: {len(actionable)} status transition(s) the "
                      f"source should already have produced:")
                for cid, cur, new, ev in actionable[:10]:
                    print(f"  {cid[:8]}... {(cur or '(none)'):20s} -> {new}")
                try:
                    from etp_tracker.email_alerts import send_critical_alert
                    body = "; ".join(f"{cid[:8]} {cur or '(none)'}->{new}"
                                     for cid, cur, new, _ in actionable[:20])
                    send_critical_alert(
                        subject=f"REX status not correct at source — {len(actionable)} drift(s)",
                        message=("status_reconciler --assert-noop found transitions the ingest "
                                 "should have produced. Fix status derivation at the source. " + body),
                        subject_prefix="[STATUS]")
                except Exception as e:
                    log.warning("assert-noop escalation failed: %s", e)
                return 2
            print("\nASSERT-NOOP OK: status already correct at source (0 transitions).")
            return 0

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
                # Permit a narrow correction of a prior over-promotion (the 128
                # never-effective + 85 ghost-listed rows); otherwise stay
                # conservative and skip the demotion.
                if not _is_correction_demotion(cur, new, ev):
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


_LIFECYCLE_ORDER = {
    STATUS_UNDER_CONSIDERATION: 0,
    STATUS_TARGET_LIST: 1,
    STATUS_FILED: 2,
    STATUS_DELAYED: 3,     # 485BXT delay — between filed and effective
    STATUS_EFFECTIVE: 4,   # registration effective (not trading)
    STATUS_LISTED: 5,
    STATUS_SUSPENDED: 6,
    STATUS_DELISTED: 7,
}


def _is_promotion(current: str, proposed: str) -> bool:
    """True if proposed is later in the lifecycle than current."""
    return _LIFECYCLE_ORDER.get(proposed, 0) > _LIFECYCLE_ORDER.get(current, 0)


def _is_correction_demotion(current: str, proposed: str, evidence: dict) -> bool:
    """Allow a NARROW downward correction of a prior OVER-promotion — only when
    the current (higher) status is no longer supported by evidence AND the fund
    is not actually trading. This corrects the 128 rows marked `effective` with
    no confirmed 485BPOS, and the 85 ghost-`listed` rows whose own ticker never
    appears ACTV on Bloomberg. It is NOT a market-driven demotion (a trading
    fund going dark) — that remains the job of the explicit LIQU/DLST path.

    Guards: never touch a row Bloomberg says is ACTV (it's genuinely trading),
    and only demote within the pre-trading band (listed->effective/delayed/filed,
    effective->delayed/filed, delayed->filed).
    """
    if evidence.get("bloomberg_actv_evidence"):
        return False  # genuinely trading — leave it
    if not _is_promotion(proposed, current):
        return False  # not actually a downward move
    # current must be an over-promotion: effective/listed without the evidence
    # that justifies it (no confirmed-effective BPOS; no own-ticker ACTV).
    if current == STATUS_LISTED:
        return True   # no ACTV (guarded above) => ghost-listed correction
    if current == STATUS_EFFECTIVE:
        return not evidence.get("sec_effective_evidence")  # no confirmed BPOS arrived
    return False


if __name__ == "__main__":
    sys.exit(main())
