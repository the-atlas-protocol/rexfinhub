"""
CLI entry point for the market data pipeline.

Reads Bloomberg data from Excel, applies CSV rules, auto-classifies,
writes to SQLite + Excel export. Tracks file modification time to skip
re-processing when the data hasn't changed.

Usage:
    python scripts/run_market_pipeline.py
    python scripts/run_market_pipeline.py --data path/to/file.xlsx
    python scripts/run_market_pipeline.py --no-db         # skip DB write
    python scripts/run_market_pipeline.py --no-export     # skip Excel export
    python scripts/run_market_pipeline.py --force          # ignore change detection
"""
import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from market.config import DATA_FILE, RULES_DIR, HISTORY_DIR, LAST_RUN_FILE


# ---------------------------------------------------------------------------
# Change detection helpers
# ---------------------------------------------------------------------------

def _get_file_mtime(path: Path) -> str:
    """Get file modification time as ISO string."""
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()


def _load_last_run() -> dict | None:
    """Load the last run metadata, or None if no previous run."""
    if not LAST_RUN_FILE.exists():
        return None
    try:
        return json.loads(LAST_RUN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_last_run(data_file: Path, run_id: int | None, row_count: int) -> None:
    """Save run metadata for change detection."""
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "data_file": str(data_file),
        "file_mtime": _get_file_mtime(data_file),
        "file_size": data_file.stat().st_size,
        "run_at": datetime.now().isoformat(),
        "run_id": run_id,
        "row_count": row_count,
    }
    LAST_RUN_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _file_changed(data_file: Path) -> bool:
    """Check if the data file has changed since the last pipeline run."""
    last = _load_last_run()
    if last is None:
        return True  # no previous run -> always process

    current_mtime = _get_file_mtime(data_file)
    current_size = data_file.stat().st_size

    if str(data_file) != last.get("data_file"):
        return True  # different file
    if current_mtime != last.get("file_mtime"):
        return True  # modification time changed
    if current_size != last.get("file_size"):
        return True  # file size changed

    return False


def _snapshot_to_history(data_file: Path) -> Path | None:
    """Copy the input file to data/DASHBOARD/history/ with date suffix."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    stem = data_file.stem
    ext = data_file.suffix
    dest = HISTORY_DIR / f"{stem}_{date_str}{ext}"

    # Don't overwrite if same-day snapshot already exists
    if dest.exists():
        existing_size = dest.stat().st_size
        current_size = data_file.stat().st_size
        if existing_size == current_size:
            return None  # identical snapshot already exists

    shutil.copy2(data_file, dest)
    return dest


def main():
    parser = argparse.ArgumentParser(description="Market data pipeline")
    parser.add_argument("--data", type=str, help="Path to input Excel file")
    parser.add_argument("--rules", type=str, help="Path to rules directory")
    parser.add_argument("--no-db", action="store_true", help="Skip database write")
    parser.add_argument("--no-export", action="store_true", help="Skip Excel export")
    parser.add_argument("--force", action="store_true", help="Force run even if data unchanged")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)-20s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("market")

    data_file = Path(args.data) if args.data else DATA_FILE
    rules_dir = Path(args.rules) if args.rules else RULES_DIR

    print(f"[1/9] Data file: {data_file}")
    print(f"       Rules dir: {rules_dir}")

    if not data_file.exists():
        print(f"ERROR: Data file not found: {data_file}")
        sys.exit(1)

    # --- Change detection ---
    if not args.force and not _file_changed(data_file):
        last = _load_last_run()
        print(f"  Data unchanged since last run ({last.get('run_at', '?')})")
        print("  Use --force to re-process. Exiting.")
        sys.exit(0)

    print(f"  File modified: {_get_file_mtime(data_file)}")

    # --- Step 2: Load rules ---
    print("[2/9] Loading rules...")
    from market.rules import load_all_rules, validate_rules

    rules = load_all_rules(rules_dir)

    warnings = validate_rules(rules)
    for w in warnings:
        print(f"  WARNING: {w}")

    fm = rules["fund_mapping"]
    im = rules["issuer_mapping"]
    mkt_status_rule = rules.get("market_status", None)
    print(f"  fund_mapping: {len(fm)} rows")
    print(f"  issuer_mapping: {len(im)} rows")
    print(f"  exclusions: {len(rules['exclusions'])} rows")
    print(f"  rex_funds: {len(rules['rex_funds'])} rows")
    print(f"  market_status: {len(mkt_status_rule) if mkt_status_rule is not None else 0} rows")
    print(f"  category_attributes: {len(rules['category_attributes'])} rows")

    # --- Step 3: Read input ---
    print("[3/9] Reading input Excel...")
    from market.ingest import read_input

    data = read_input(data_file)
    etp = data["etp_combined"]
    stock = data["stock_data"]
    print(f"  ETP combined: {etp.shape[0]} rows x {etp.shape[1]} cols")
    print(f"  Stock data: {stock.shape[0]} rows")

    # --- Historical snapshot ---
    snapshot = _snapshot_to_history(data_file)
    if snapshot:
        print(f"  Snapshot saved: {snapshot.name}")

    # --- Step 4: Derive dim_fund_category ---
    print("[4/9] Deriving dim_fund_category...")
    from market.derive import derive_dim_fund_category

    dim = derive_dim_fund_category(
        fund_mapping=fm,
        issuer_mapping=im,
        rex_funds=rules["rex_funds"],
        category_attributes=rules["category_attributes"],
        etp_combined=etp,
    )
    print(f"  dim_fund_category: {len(dim)} rows")
    if "category_display" in dim.columns:
        for cat, cnt in dim["category_display"].value_counts().items():
            print(f"    {cat}: {cnt}")

    # --- Step 5: Run transform ---
    print("[5/9] Running 12-step transform...")
    from market.transform import run_transform

    result = run_transform(etp, rules, dim)
    master = result["master"]
    ts = result["ts"]
    print(f"  q_master_data: {master.shape[0]} rows x {master.shape[1]} cols")
    print(f"  q_aum_time_series_labeled: {ts.shape[0]} rows")

    # --- Step 6: Auto-classify ---
    print("[6/9] Auto-classifying funds...")
    from market.auto_classify import classify_all, classify_to_dataframe

    classifications = classify_all(etp)
    class_df = classify_to_dataframe(etp)

    # Print strategy distribution
    strategy_counts = {}
    for c in classifications:
        strategy_counts[c.strategy] = strategy_counts.get(c.strategy, 0) + 1
    for strat, cnt in sorted(strategy_counts.items(), key=lambda x: -x[1]):
        print(f"    {strat}: {cnt}")

    # Merge strategy, confidence, underlier_type into master
    class_merge = class_df[["ticker", "strategy", "confidence", "underlier_type"]].copy()
    class_merge = class_merge.rename(columns={"confidence": "strategy_confidence"})
    for col in ["strategy", "strategy_confidence", "underlier_type"]:
        if col in master.columns:
            master = master.drop(columns=[col])
    master = master.merge(class_merge, on="ticker", how="left")
    print(f"  Classified: {len(classifications)} funds")

    # --- Step 7: Queues report ---
    print("[7/9] Building queues report...")
    from market.queues import build_queues_report

    queues = build_queues_report(etp, fm, im)
    unmapped = queues["summary"]["unmapped_count"]
    new_issuers = queues["summary"]["new_issuer_count"]
    print(f"  Unmapped funds: {unmapped}")
    print(f"  New issuers: {new_issuers}")

    if unmapped > 0:
        print("  Top unmapped funds:")
        for item in queues["unmapped_funds"][:10]:
            ticker = item.get("ticker", "?")
            name = item.get("fund_name", "")[:50]
            aum = item.get("aum", 0)
            suggested = item.get("suggested_category", "")
            aum_str = f"${aum/1e6:.0f}M" if aum and aum > 0 else "N/A"
            print(f"    {ticker:<12} {aum_str:>10}  {suggested:<10} {name}")

    # --- Step 8: Write to DB ---
    run_id = None
    if args.no_db:
        print("[8/9] Database write skipped (--no-db)")
    else:
        print("[8/9] Writing to database...")
        from webapp.database import SessionLocal, init_db
        from market.db_writer import (
            create_pipeline_run, finish_pipeline_run,
            write_master_data, write_time_series, write_stock_data,
            write_classifications, write_market_statuses,
        )
        from market.rules import sync_rules_to_db

        init_db()
        session = SessionLocal()
        try:
            run_id = create_pipeline_run(session, str(data_file))

            # Sync rules to DB
            sync_rules_to_db(rules, session)

            # Write output tables
            master_count = write_master_data(session, master, run_id)
            ts_count = write_time_series(session, ts, run_id)
            stock_count = write_stock_data(session, stock, run_id)

            # Write classifications
            class_count = write_classifications(session, classifications, run_id)

            # Write market status from CSV rule (canonical source)
            mkt_status_count = 0
            if mkt_status_rule is not None and not mkt_status_rule.empty:
                mkt_status_count = write_market_statuses(session, mkt_status_rule)

            finish_pipeline_run(
                session, run_id,
                status="completed",
                etp_rows_read=len(etp),
                master_rows_written=master_count,
                ts_rows_written=ts_count,
                stock_rows_written=stock_count,
                unmapped_count=unmapped,
                new_issuer_count=new_issuers,
            )
            session.commit()
            print(f"  Pipeline run ID: {run_id}")
            print(f"  master: {master_count}, ts: {ts_count}, stock: {stock_count}")
            print(f"  classifications: {class_count}, market_statuses: {mkt_status_count}")
        except Exception as e:
            session.rollback()
            if run_id:
                try:
                    finish_pipeline_run(session, run_id, status="failed",
                                        error_message=str(e))
                    session.commit()
                except Exception:
                    pass
            print(f"  ERROR: {e}")
            raise
        finally:
            session.close()

    # --- Step 9: Export ---
    if args.no_export:
        print("[9/9] Excel export skipped (--no-export)")
    else:
        print("[9/9] Exporting to Excel...")
        from market.export import export_to_excel

        out_path = export_to_excel(master, ts, stock)
        print(f"  Exported: {out_path}")

    # Save run metadata for change detection
    _save_last_run(data_file, run_id, len(etp))

    print("\nDone.")


if __name__ == "__main__":
    main()
