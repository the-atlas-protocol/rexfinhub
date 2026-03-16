"""
13F Holdings Pipeline Runner

Modes:
    seed          - Refresh CUSIP mappings from market data
    bulk 2025q4   - Ingest a specific quarterly dataset
    incremental   - Parse recent 13F-HR filings (last 7 days)
    auto          - Seed + latest quarter (if not yet ingested) + incremental

Scheduling (Windows Task Scheduler - weekly on Monday):
    schtasks /create /tn "ETP_13F_Weekly" /tr "python C:\\Projects\\rexfinhub\\scripts\\run_13f.py incremental" /sc weekly /d MON /st 10:00 /f

Quarterly bulk (run manually after each quarter ends):
    python scripts/run_13f.py bulk 2025q4
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

USER_AGENT = "REX-ETP-FilingTracker/2.0 (contact: relasmar@rexfin.com)"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("run_13f")

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/run_13f.py seed")
        print("  python scripts/run_13f.py bulk 2025q4")
        print("  python scripts/run_13f.py incremental")
        print("  python scripts/run_13f.py auto")
        print("  python scripts/run_13f.py local <path-to-tsv-dir>")
        print("  python scripts/run_13f.py health")
        sys.exit(1)

    mode = sys.argv[1]

    from webapp.database import init_db
    from etp_tracker.thirteen_f import (
        seed_cusip_mappings,
        ingest_13f_dataset,
        ingest_13f_incremental,
        ingest_13f_local,
        get_latest_available_quarter,
        enrich_cusip_mappings_from_holdings,
        data_health_report,
    )

    init_db()
    start = time.time()

    if mode == "seed":
        n = seed_cusip_mappings()
        print(f"Seeded {n} CUSIP mappings.")

    elif mode == "bulk":
        if len(sys.argv) < 3:
            print("Error: provide quarter, e.g. 2025q4")
            sys.exit(1)
        quarter = sys.argv[2]
        result = ingest_13f_dataset(quarter, USER_AGENT)
        print(f"Quarter {result['quarter']}:")
        print(f"  Institutions: {result['institutions_upserted']}")
        print(f"  Holdings:     {result['holdings_inserted']}")
        print(f"  CUSIPs:       {result['cusips_matched']}")
        if result["errors"]:
            print(f"  Errors:       {len(result['errors'])}")

    elif mode == "incremental":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        stats = ingest_13f_incremental(USER_AGENT, days_back=days)
        print(f"Found {stats['filings_found']}, parsed {stats['filings_parsed']}, "
              f"{stats['holdings_inserted']} holdings, {stats['cusips_matched']} CUSIP matches")

    elif mode == "auto":
        # Step 1: Seed CUSIP mappings
        log.info("[1/4] Seeding CUSIP mappings...")
        n_seed = seed_cusip_mappings()
        print(f"[1/4] Seeded {n_seed} CUSIPs")

        # Step 2: Check for latest quarterly dataset
        log.info("[2/4] Checking for latest quarterly dataset...")
        latest_q = get_latest_available_quarter()
        if latest_q:
            print(f"[2/4] Latest quarter: {latest_q}")
            result = ingest_13f_dataset(latest_q, USER_AGENT)
            print(f"  Institutions: {result['institutions_upserted']}, "
                  f"Holdings: {result['holdings_inserted']}")
        else:
            print("[2/4] No quarterly dataset found, skipping bulk")

        # Step 3: Incremental (last 7 days)
        log.info("[3/4] Running incremental (last 7 days)...")
        stats = ingest_13f_incremental(USER_AGENT, days_back=7)
        print(f"[3/4] Incremental: {stats['filings_parsed']} parsed, "
              f"{stats['holdings_inserted']} holdings")

        # Step 4: Enrich CUSIP mappings from new holdings
        log.info("[4/4] Enriching CUSIP mappings...")
        n_enrich = enrich_cusip_mappings_from_holdings()
        print(f"[4/4] Enriched {n_enrich} CUSIPs from holdings")

    elif mode == "local":
        if len(sys.argv) < 3:
            print("Error: provide path to directory with SUBMISSION.tsv, COVERPAGE.tsv, INFOTABLE.tsv")
            sys.exit(1)
        tsv_dir = sys.argv[2]
        result = ingest_13f_local(tsv_dir)
        print(f"Institutions: {result['institutions_upserted']}")
        print(f"Holdings:     {result['holdings_inserted']}")
        print(f"Skipped:      {result['holdings_skipped']}")
        print(f"CUSIPs:       {result['cusips_matched']}")
        if result["errors"]:
            print(f"Errors:       {len(result['errors'])}")
            for e in result["errors"]:
                print(f"  - {e}")

    elif mode == "health":
        data_health_report()

    else:
        print(f"Unknown mode: {mode}")
        print("Valid modes: seed, bulk, incremental, auto, local, health")
        sys.exit(1)

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
