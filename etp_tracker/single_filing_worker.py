"""Tier 2 daemon: polls filing_alerts for enrichment_status=0 and enriches them.

Designed to run as a systemd Type=simple service next to atom_watcher.

    SEC_USER_AGENT="REX-ETP-Tracker/2.0 (you@example.com)" \
        /home/jarvis/venv/bin/python -m etp_tracker.single_filing_worker

Environment variables:
    SEC_USER_AGENT              required, passed to the SEC HTTP client
    SINGLE_FILING_BATCH_SIZE    default 20, max alerts per cycle
    SINGLE_FILING_POLL_INTERVAL default 30, seconds between cycles

Polling flow (per cycle):
    1. SELECT ... WHERE enrichment_status=0 ORDER BY detected_at ASC LIMIT N
    2. For each alert: call enrich_alert, commit after each (so a crash in
       the middle of a batch costs at most one filing's worth of progress).
    3. Log a one-line cycle summary.
    4. Sleep poll_interval, handle KeyboardInterrupt cleanly.
"""
from __future__ import annotations

import logging
import os
import sys
import time

from sqlalchemy import select

from .sec_client import SECClient
from .single_filing import enrich_alert

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (env-driven)
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = int(os.environ.get("SINGLE_FILING_BATCH_SIZE", "20"))
DEFAULT_POLL_INTERVAL = int(os.environ.get("SINGLE_FILING_POLL_INTERVAL", "30"))
USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
)


# ---------------------------------------------------------------------------
# One polling cycle
# ---------------------------------------------------------------------------

def run_cycle(batch_size: int, client: SECClient) -> dict:
    """Enrich up to batch_size pending alerts. Returns a summary dict."""
    from webapp.database import SessionLocal
    from webapp.models import FilingAlert

    summary = {
        "processed": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "new_trusts": 0,
    }

    db = SessionLocal()
    try:
        pending = db.execute(
            select(FilingAlert)
            .where(FilingAlert.enrichment_status == 0)
            .order_by(FilingAlert.detected_at.asc())
            .limit(batch_size)
        ).scalars().all()

        if not pending:
            return summary

        for alert in pending:
            summary["processed"] += 1
            try:
                result = enrich_alert(db, alert, client=client)
                # Commit after each alert so a crash mid-batch doesn't lose
                # progress on the alerts we already completed.
                db.commit()
                if result.ok:
                    summary["ok"] += 1
                    if result.trust_created:
                        summary["new_trusts"] += 1
                else:
                    if result.skipped_reason:
                        summary["skipped"] += 1
                    else:
                        summary["failed"] += 1
            except Exception as exc:
                # enrich_alert should catch its own errors and mark the
                # alert enrichment_status=2, but defensively handle the case
                # where something in the commit path itself blew up.
                log.exception(
                    "Unhandled error enriching %s: %s",
                    alert.accession_number, exc,
                )
                db.rollback()
                summary["failed"] += 1
                try:
                    # Re-fetch and mark failed so we don't loop on this row
                    bad = db.get(FilingAlert, alert.id)
                    if bad:
                        bad.enrichment_status = 2
                        bad.enrichment_error = (
                            f"worker: {type(exc).__name__}: {exc}"[:500]
                        )
                        db.commit()
                except Exception:
                    db.rollback()
    finally:
        db.close()

    return summary


# ---------------------------------------------------------------------------
# Long-running loop
# ---------------------------------------------------------------------------

def run_forever(batch_size: int = DEFAULT_BATCH_SIZE,
                 poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
    """Poll filing_alerts and enrich pending rows until interrupted."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from webapp.database import init_db
    init_db()

    log.info("Single-filing worker starting")
    log.info(
        "batch_size=%d poll_interval=%ds user_agent=%s",
        batch_size, poll_interval, USER_AGENT,
    )

    # Share one SECClient across cycles so we reuse the on-disk cache and
    # the connection pool (SEC rate limit is enforced inside sec_client).
    client = SECClient(user_agent=USER_AGENT)

    cycle = 0
    try:
        while True:
            cycle += 1
            start = time.time()
            try:
                summary = run_cycle(batch_size, client)
            except Exception as exc:
                log.exception("Cycle %d failed: %s", cycle, exc)
                summary = {"processed": 0, "ok": 0, "failed": 0,
                           "skipped": 0, "new_trusts": 0}
            elapsed = time.time() - start

            log.info(
                "cycle %d: %ds processed=%d ok=%d failed=%d skipped=%d new_trusts=%d",
                cycle, int(elapsed),
                summary["processed"], summary["ok"], summary["failed"],
                summary["skipped"], summary["new_trusts"],
            )

            # Adaptive sleep: if we hit a full batch, the queue probably has
            # more — go again immediately. Otherwise wait for new arrivals.
            if summary["processed"] >= batch_size:
                continue

            sleep_for = max(0, poll_interval - (time.time() - start))
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        log.info("Single-filing worker stopped by KeyboardInterrupt")
        sys.exit(0)


if __name__ == "__main__":
    run_forever()
