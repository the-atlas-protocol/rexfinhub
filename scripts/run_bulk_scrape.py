"""Bulk scrape runner for 3-year filing history across all trusts.

Usage examples:
    # Full 3-year scrape, all trusts, 6 workers
    python scripts/run_bulk_scrape.py --since 2023-01-01 --workers 6

    # Split for 2 machines
    python scripts/run_bulk_scrape.py --since 2023-01-01 --chunk 1/2  # local
    python scripts/run_bulk_scrape.py --since 2023-01-01 --chunk 2/2  # VPS

    # Test with 5 trusts first
    python scripts/run_bulk_scrape.py --since 2023-01-01 --limit 5

    # Only newly discovered trusts (skip cached curated ones)
    python scripts/run_bulk_scrape.py --since 2023-01-01 --universe discovered

    # Dry run -- print what would be processed
    python scripts/run_bulk_scrape.py --since 2023-01-01 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from etp_tracker.run_pipeline import load_ciks_from_db, run_pipeline


def parse_chunk(chunk_str: str) -> tuple[int, int]:
    """Parse 'N/M' chunk spec into (chunk_number, total_chunks)."""
    try:
        n, m = chunk_str.split("/")
        n, m = int(n), int(m)
        if n < 1 or n > m:
            raise ValueError
        return n, m
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(
            f"Invalid chunk format '{chunk_str}'. Use N/M (e.g., 1/2, 2/3)."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Bulk scrape SEC filings for all trusts in the database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--since", default="2023-01-01",
        help="Only process filings on or after this date (default: 2023-01-01)",
    )
    parser.add_argument(
        "--until", default=None,
        help="Only process filings on or before this date (default: no limit)",
    )
    parser.add_argument(
        "--workers", type=int, default=3,
        help="Number of parallel workers (default: 3). Auto-adjusts pause for SEC rate limit.",
    )
    parser.add_argument(
        "--chunk", type=parse_chunk, default=None, metavar="N/M",
        help="Process chunk N of M total chunks (e.g., 1/2 = first half). For multi-machine splitting.",
    )
    parser.add_argument(
        "--universe", choices=["all", "curated", "discovered"], default="all",
        help="Which trusts to process (default: all)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N trusts (for testing)",
    )
    parser.add_argument(
        "--etf-only", action=argparse.BooleanOptionalAction, default=True,
        help="Skip non-ETF trusts via header triage (default: enabled). Use --no-etf-only to disable.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be processed without making any HTTP requests",
    )
    parser.add_argument(
        "--user-agent", default=None,
        help="SEC User-Agent string (default: from env or built-in)",
    )
    args = parser.parse_args()

    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("bulk_scrape")

    # Load CIKs from database
    ciks, overrides = load_ciks_from_db(universe=args.universe)
    log.info("Loaded %d trusts (universe=%s)", len(ciks), args.universe)

    # Apply chunk splitting
    if args.chunk:
        n, m = args.chunk
        chunk_size = len(ciks) // m
        remainder = len(ciks) % m
        # Distribute remainder across first `remainder` chunks
        start = sum(chunk_size + (1 if i < remainder else 0) for i in range(n - 1))
        end = start + chunk_size + (1 if (n - 1) < remainder else 0)
        ciks = ciks[start:end]
        log.info("Chunk %d/%d: trusts %d-%d (%d trusts)", n, m, start, end - 1, len(ciks))

    # Apply limit
    if args.limit:
        ciks = ciks[:args.limit]
        log.info("Limited to first %d trusts", len(ciks))

    if not ciks:
        log.warning("No trusts to process. Exiting.")
        return

    # Dry run
    if args.dry_run:
        print(f"\n=== DRY RUN ===")
        print(f"Universe: {args.universe}")
        print(f"Since: {args.since}")
        print(f"Until: {args.until or '(no limit)'}")
        print(f"Workers: {args.workers}")
        print(f"ETF-only triage: {'enabled' if args.etf_only else 'disabled'}")
        print(f"Trusts to process: {len(ciks)}")
        if args.chunk:
            print(f"Chunk: {args.chunk[0]}/{args.chunk[1]}")
        print(f"\nFirst 10 CIKs:")
        for cik in ciks[:10]:
            name = overrides.get(cik, "(no override)")
            print(f"  CIK {cik:>10s} - {name}")
        if len(ciks) > 10:
            print(f"  ... and {len(ciks) - 10} more")
        print(f"\nNo HTTP requests made.")
        return

    # Run the pipeline
    user_agent = args.user_agent or os.environ.get(
        "SEC_USER_AGENT", "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
    )

    log.info("Starting bulk scrape: %d trusts, since=%s, workers=%d, etf_only=%s",
             len(ciks), args.since, args.workers, args.etf_only)

    processed = run_pipeline(
        ciks=ciks,
        overrides=overrides,
        since=args.since,
        until=args.until,
        user_agent=user_agent,
        max_workers=args.workers,
        triggered_by="bulk_scrape",
        etf_only=args.etf_only,
    )

    log.info("Bulk scrape complete. Processed %d trusts.", processed)


if __name__ == "__main__":
    main()
