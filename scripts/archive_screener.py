"""
Archive screener data for quarterly backtesting and historical analysis.

Captures a full point-in-time snapshot of:
  - Screener 3x/4x/2x cache (computed results)
  - Stock evaluator pre-evaluated tickers
  - ETP data (w1-w4 master) as Parquet
  - Stock data (s1) as Parquet
  - Raw Bloomberg daily file copy
  - Metadata (timestamps, row counts, file info)

Dual-writes to local (primary) and D: drive (cold archive).

Usage:
    python scripts/archive_screener.py              # daily snapshot
    python scripts/archive_screener.py --label Q1   # labeled snapshot (e.g. quarter-end)
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOCAL_ARCHIVE = PROJECT_ROOT / "data" / "DASHBOARD" / "exports" / "screener_snapshots"
COLD_ARCHIVE = Path("D:/sec-data/archives/screener")


def archive_daily(label: str | None = None) -> dict:
    """Create a full screener snapshot. Returns metadata dict."""
    today = datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{today}_{label}" if label else today

    local_dir = LOCAL_ARCHIVE / folder_name
    cold_dir = COLD_ARCHIVE / folder_name

    local_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Archiving screener data: {folder_name} ===")

    metadata = {
        "date": today,
        "label": label,
        "archived_at": datetime.now().isoformat(),
        "files": {},
    }

    # 1. Bloomberg daily file copy
    from screener.config import DATA_FILE
    if DATA_FILE.exists():
        dst = local_dir / "bloomberg_daily_file.xlsm"
        shutil.copy2(str(DATA_FILE), str(dst))
        stat = DATA_FILE.stat()
        metadata["bloomberg_file"] = {
            "source": str(DATA_FILE),
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_mb": round(stat.st_size / 1_048_576, 1),
        }
        metadata["files"]["bloomberg_daily_file.xlsm"] = dst.stat().st_size
        print(f"  Bloomberg file: {metadata['bloomberg_file']['size_mb']} MB "
              f"(modified {metadata['bloomberg_file']['mtime'][:16]})")
    else:
        print(f"  WARNING: Bloomberg file not found: {DATA_FILE}")

    # 2. ETP data (w1-w4 master) as Parquet
    try:
        from screener.data_loader import load_etp_data
        etp_df = load_etp_data()
        # Coerce object columns with mixed types to string for Parquet safety
        for col in etp_df.columns:
            if etp_df[col].dtype == "object":
                etp_df[col] = etp_df[col].astype(str).replace("nan", "")
        etp_path = local_dir / "etp_data.parquet"
        etp_df.to_parquet(etp_path, index=False)
        metadata["etp_rows"] = len(etp_df)
        metadata["etp_cols"] = len(etp_df.columns)
        metadata["files"]["etp_data.parquet"] = etp_path.stat().st_size
        print(f"  ETP data: {len(etp_df)} rows x {len(etp_df.columns)} cols")
    except Exception as e:
        print(f"  ETP data failed: {e}")

    # 3. Stock data (s1) as Parquet
    try:
        from screener.data_loader import load_stock_data
        stock_df = load_stock_data()
        # Coerce object columns with mixed types to string for Parquet safety
        for col in stock_df.columns:
            if stock_df[col].dtype == "object":
                stock_df[col] = stock_df[col].astype(str).replace("nan", "")
        stock_path = local_dir / "stock_data.parquet"
        stock_df.to_parquet(stock_path, index=False)
        metadata["stock_rows"] = len(stock_df)
        metadata["stock_cols"] = len(stock_df.columns)
        metadata["files"]["stock_data.parquet"] = stock_path.stat().st_size
        print(f"  Stock data: {len(stock_df)} rows x {len(stock_df.columns)} cols")
    except Exception as e:
        print(f"  Stock data failed: {e}")

    # 4. Screener 3x cache (computed results)
    try:
        from webapp.services.screener_3x_cache import compute_and_cache, invalidate_cache
        invalidate_cache()  # Force fresh computation
        cache_data = compute_and_cache()
        cache_path = local_dir / "screener_3x_cache.json"
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, default=str)
        metadata["files"]["screener_3x_cache.json"] = cache_path.stat().st_size
        metadata["screener_cache"] = {
            "tier_1": len(cache_data.get("tiers", {}).get("tier_1", [])),
            "tier_2": len(cache_data.get("tiers", {}).get("tier_2", [])),
            "tier_3": len(cache_data.get("tiers", {}).get("tier_3", [])),
            "four_x": len(cache_data.get("four_x", [])),
            "two_x": len(cache_data.get("two_x_candidates", [])),
            "eval_cache": len(cache_data.get("eval_cache", {})),
            "li_products": len(cache_data.get("li_products", [])),
            "data_date": cache_data.get("data_date"),
            "computed_at": cache_data.get("computed_at"),
        }
        print(f"  Screener cache: {metadata['screener_cache']['tier_1']} T1, "
              f"{metadata['screener_cache']['tier_2']} T2, "
              f"{metadata['screener_cache']['four_x']} 4x, "
              f"{metadata['screener_cache']['eval_cache']} evaluated")

        # 4b. Eval cache as separate file for easy access
        eval_cache = cache_data.get("eval_cache", {})
        if eval_cache:
            eval_path = local_dir / "stock_evaluator_top100.json"
            with open(eval_path, "w") as f:
                json.dump(eval_cache, f, default=str, indent=2)
            metadata["files"]["stock_evaluator_top100.json"] = eval_path.stat().st_size
    except Exception as e:
        print(f"  Screener cache failed: {e}")

    # 5. Screener results from DB
    try:
        from webapp.database import init_db, SessionLocal
        from sqlalchemy import text
        init_db()
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT * FROM screener_results ORDER BY upload_id DESC, composite_score DESC"
            )).fetchall()
            if rows:
                import csv
                csv_path = local_dir / "screener_results.csv"
                columns = rows[0]._fields if hasattr(rows[0], "_fields") else rows[0].keys()
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(columns)
                    for row in rows:
                        writer.writerow(row)
                metadata["screener_results_rows"] = len(rows)
                metadata["files"]["screener_results.csv"] = csv_path.stat().st_size
                print(f"  Screener results: {len(rows)} rows")
            else:
                print("  Screener results: table empty")
        finally:
            db.close()
    except Exception as e:
        print(f"  Screener results export failed: {e}")

    # 5b. Autocall ranks (for week-over-week rank change tracking)
    try:
        from webapp.services.report_data import get_flow_report
        from webapp.database import init_db, SessionLocal
        init_db()
        _db = SessionLocal()
        try:
            flow = get_flow_report(_db)
            for s in flow.get("suites", []):
                if "autocall" in s.get("label", "").lower():
                    issuers = s.get("issuers", [])
                    by_share = sorted(issuers, key=lambda x: x.get("market_share", 0), reverse=True)
                    by_1w = sorted(issuers, key=lambda x: x.get("flow_1w", 0), reverse=True)
                    by_1m = sorted(issuers, key=lambda x: x.get("flow_1m", 0) or 0, reverse=True)
                    ranks = {}
                    for i, iss in enumerate(by_share, 1):
                        ranks.setdefault(iss["issuer"], {})["share_rank"] = i
                    for i, iss in enumerate(by_1w, 1):
                        ranks.setdefault(iss["issuer"], {})["flow_1w_rank"] = i
                    for i, iss in enumerate(by_1m, 1):
                        ranks.setdefault(iss["issuer"], {})["flow_1m_rank"] = i
                    for iss in issuers:
                        ranks[iss["issuer"]]["aum"] = iss.get("aum", 0)
                        ranks[iss["issuer"]]["market_share"] = iss.get("market_share", 0)
                        ranks[iss["issuer"]]["is_rex"] = iss.get("is_rex", False)
                    autocall_path = Path("data/DASHBOARD/exports/autocall_ranks")
                    autocall_path.mkdir(parents=True, exist_ok=True)
                    rank_file = autocall_path / f"{datetime.now().strftime('%Y-%m-%d')}.json"
                    with open(rank_file, "w") as f:
                        json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "issuers": ranks}, f, indent=2, default=str)
                    metadata["files"]["autocall_ranks.json"] = rank_file.stat().st_size
                    print(f"  Autocall ranks: {len(ranks)} issuers")
                    break
        finally:
            _db.close()
    except Exception as e:
        print(f"  Autocall ranks failed: {e}")

    # 6. Write metadata
    metadata_path = local_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"  Metadata written: {metadata_path}")

    # 7. Copy to cold storage (D: drive)
    if COLD_ARCHIVE.parent.exists():
        try:
            if cold_dir.exists():
                shutil.rmtree(cold_dir)
            shutil.copytree(str(local_dir), str(cold_dir))
            print(f"  Cold archive: {cold_dir}")
        except Exception as e:
            print(f"  Cold archive failed (non-fatal): {e}")
    else:
        print("  D: drive not available, skipping cold archive")

    total_mb = sum(metadata["files"].values()) / 1_048_576
    print(f"  Total snapshot: {total_mb:.1f} MB ({len(metadata['files'])} files)")
    print(f"  Local: {local_dir}")
    return metadata


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Archive screener data")
    parser.add_argument("--label", type=str, default=None,
                        help="Optional label (e.g. Q1, pre-release)")
    args = parser.parse_args()
    archive_daily(label=args.label)
