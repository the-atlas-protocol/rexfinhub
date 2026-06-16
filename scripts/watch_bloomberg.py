"""
Bloomberg file watcher -- detects fresh Bloomberg data and triggers market sync.

Runs as scheduled task every 5 min (4:00-5:00 PM weekdays).
If Bloomberg file is from today and newer than last sync, triggers:
  1. Market data sync
  2. Full classification
  3. Screener cache rebuild
  4. Upload to Render

Usage:
    python scripts/watch_bloomberg.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MARKER = PROJECT_ROOT / "data" / "DASHBOARD" / ".bloomberg_watch_marker.json"


def _file_is_fresh() -> tuple[bool, Path | None]:
    """Check if Bloomberg file was modified today and is newer than last watch trigger."""
    from screener.config import DATA_FILE as bbg

    if not bbg or not bbg.exists():
        return False, None

    file_date = datetime.fromtimestamp(bbg.stat().st_mtime).date()
    if file_date != date.today():
        return False, bbg

    # Check marker -- did we already trigger today for this file version?
    if MARKER.exists():
        try:
            with open(MARKER) as f:
                marker = json.load(f)
            if marker.get("date") == date.today().isoformat():
                last_mtime = marker.get("file_mtime", 0)
                if bbg.stat().st_mtime <= last_mtime:
                    return False, bbg  # Already triggered for this version
        except (json.JSONDecodeError, KeyError):
            pass

    return True, bbg


def _trigger_sync(bbg_path: Path):
    """Run market sync + classify + screener + upload."""
    print(f"  Fresh Bloomberg detected: {bbg_path.name} "
          f"(mtime: {datetime.fromtimestamp(bbg_path.stat().st_mtime):%H:%M})")

    from webapp.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()

    try:
        # Market sync
        print("  Syncing market data...")
        from webapp.services.market_sync import sync_market_data
        result = sync_market_data(db)
        print(f"  Market: {result['master_rows']} funds, {result['ts_rows']} TS rows")

        # Classification
        print("  Running classification...")
        try:
            from webapp.services.data_engine import build_all
            from market.auto_classify import classify_all
            from market.db_writer import write_classifications, create_pipeline_run

            data = build_all()
            etp = data.get("master", None)
            if etp is not None and not etp.empty:
                classifications = classify_all(etp)
                run_id = create_pipeline_run(db, source_file="bloomberg_watch")
                write_classifications(db, classifications, run_id=run_id)
                db.commit()
                print(f"  Classified {len(classifications)} funds")
        except Exception as e:
            print(f"  Classification failed (non-fatal): {e}")

        # Curated enrichment restamp — sync_market_data full-replaces
        # mkt_master_data and clears the curated 3-axis taxonomy + issuer_display
        # to NULL. Without restamping here, every fresh-file trigger left REX's
        # primary_strategy/issuer NULL until the nightly chain (the recurring
        # 'auto'-sync taxonomy wipe, 2026-06-16). Mirror run_daily.run_market_sync:
        # apply_fund_master (curated) + derive/apply issuer brands. Idempotent.
        print("  Restamping curated taxonomy + issuer brands...")
        import subprocess as _sp
        for _name, _args in (
            ("apply_fund_master", ["apply_fund_master.py"]),
            ("derive_issuer_brands", ["derive_issuer_brands.py"]),
            ("apply_issuer_brands", ["apply_issuer_brands.py"]),
        ):
            try:
                _r = _sp.run([sys.executable, str(PROJECT_ROOT / "scripts" / _args[0])],
                             capture_output=True, text=True, timeout=300)
                if _r.returncode != 0:
                    print(f"  WARN: {_name} exit={_r.returncode}: {_r.stderr[:160]}")
            except Exception as _e:
                print(f"  WARN: {_name} skipped: {_e}")

        # Screener cache
        print("  Rebuilding screener cache...")
        try:
            from webapp.services.screener_3x_cache import compute_and_cache
            cache_data = compute_and_cache()
            cache_path = PROJECT_ROOT / "temp" / "screener_cache.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(cache_data, f, default=str)
            print(f"  Cache: {cache_path.stat().st_size / 1024:.0f} KB")
        except Exception as e:
            print(f"  Screener cache failed (non-fatal): {e}")

        # Upload to Render
        print("  Uploading to Render...")
        try:
            import subprocess
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py"), "--upload"],
                cwd=str(PROJECT_ROOT), timeout=600,
            )
        except Exception as e:
            print(f"  Upload failed (non-fatal): {e}")

    finally:
        db.close()

    # Write marker
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKER, "w") as f:
        json.dump({
            "date": date.today().isoformat(),
            "file_mtime": bbg_path.stat().st_mtime,
            "triggered_at": datetime.now().isoformat(),
        }, f)

    print("  Bloomberg watch: sync complete")


if __name__ == "__main__":
    print(f"Bloomberg watch: {datetime.now():%Y-%m-%d %H:%M}")
    is_fresh, bbg_path = _file_is_fresh()
    if is_fresh and bbg_path:
        _trigger_sync(bbg_path)
    else:
        reason = "no file found" if not bbg_path else "not modified today or already triggered"
        print(f"  No fresh Bloomberg data detected ({reason})")
