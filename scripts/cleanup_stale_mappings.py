"""Cleanup stale mappings from fund_mapping.csv.

A "stale" mapping is a ticker in fund_mapping.csv that points to a fund
whose market_status is no longer ACTV (liquidated, delisted, etc).

Note: Per Ryu's feedback, non-ACTV funds ARE still allowed to be classified
— they matter for historical AUM. However, if a fund has become DELISTED
we may want to remove it from the active mapping to stop it from affecting
KPIs. This script is OPT-IN: it shows what would be removed and requires
--apply to actually write.

Usage:
    # Dry run — show what would be removed
    python scripts/cleanup_stale_mappings.py

    # Apply — actually remove from fund_mapping.csv
    python scripts/cleanup_stale_mappings.py --apply

    # Only remove a specific status
    python scripts/cleanup_stale_mappings.py --status LIQD --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser(description="Cleanup stale fund_mapping entries")
    parser.add_argument("--apply", action="store_true", help="Actually remove (default: dry run)")
    parser.add_argument("--status", default=None, help="Only remove tickers with this status (LIQD, DLIS, INAC)")
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N removals")
    args = parser.parse_args()

    from tools.rules_editor.classify_engine import scan_unmapped, remove_stale

    print("Scanning for stale mappings...")
    results = scan_unmapped(since_days=3650)  # 10-year window
    stale = results.get("stale", [])

    if args.status:
        stale = [s for s in stale if s.get("market_status") == args.status]

    if not stale:
        print("No stale mappings found.")
        return

    print(f"\nFound {len(stale)} stale mapping(s):")
    print(f"{'#':<4} {'Ticker':<14} {'Status':<8} {'Category':<10} Fund Name")
    print("-" * 80)

    status_counts = {}
    tickers_to_remove = []
    for i, s in enumerate(stale[:args.limit] if args.limit else stale, 1):
        tk = s.get("ticker", "")
        st = s.get("market_status", "")
        cat = s.get("etp_category", "")
        nm = s.get("fund_name", "")[:40]
        status_counts[st] = status_counts.get(st, 0) + 1
        tickers_to_remove.append(tk)
        if i <= 20:
            print(f"{i:<4} {tk:<14} {st:<8} {cat:<10} {nm}")

    if len(stale) > 20:
        print(f"... and {len(stale) - 20} more")

    print("\nBy status:")
    for st, c in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {st}: {c}")

    if not args.apply:
        print("\n[DRY RUN] Use --apply to actually remove these from fund_mapping.csv")
        return

    print(f"\nRemoving {len(tickers_to_remove)} tickers from fund_mapping.csv...")
    removed = remove_stale(tickers_to_remove)
    print(f"Removed {removed} rows. Done.")


if __name__ == "__main__":
    main()
