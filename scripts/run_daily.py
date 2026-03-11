"""
Daily SEC Pipeline Runner

Scrapes SEC filings, syncs to DB, refreshes market data, uploads to Render.
Emails are sent separately via `send daily` / `send weekly`.

Usage:
    run sec              # bash alias
    python scripts/run_daily.py
"""
from __future__ import annotations
import os
import time
import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on path and set working directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db


OUTPUT_DIR = PROJECT_ROOT / "outputs"
SINCE_DATE = "2024-01-01"  # 2-year window for daily runs (keeps it fast)
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"
RENDER_API_URL = "https://rex-etp-tracker.onrender.com/api/v1"



def _load_api_key() -> str:
    """Load API_KEY from .env."""
    env_file = Path(__file__).resolve().parent.parent / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def upload_db_to_render() -> None:
    """Upload the local SQLite DB to Render's /api/v1/db/upload endpoint."""
    import requests

    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    if not db_path.exists():
        print("  No local database found, skipping upload.")
        return

    api_key = _load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}

    try:
        with open(db_path, "rb") as f:
            resp = requests.post(
                f"{RENDER_API_URL}/db/upload",
                files={"file": ("etp_tracker.db", f, "application/octet-stream")},
                headers=headers,
                timeout=600,
            )
        if resp.status_code == 200:
            size_mb = db_path.stat().st_size / 1_000_000
            print(f"  Uploaded to Render ({size_mb:.1f} MB)")
        else:
            print(f"  Upload failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  Upload failed (non-fatal): {e}")


def main():
    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== ETP Filing Tracker - Daily Run ({today}) ===")

    # Step 1: Run pipeline
    print("\n[1/4] Running pipeline...")
    ciks, overrides = load_ciks_from_db()
    n, changed_trusts = run_pipeline(
        ciks=ciks,
        overrides=overrides,
        since=SINCE_DATE,
        refresh_submissions=True,
        user_agent=USER_AGENT,
        etf_only=True,
    )
    print(f"  Processed {n} trusts ({len(changed_trusts)} with new filings)")

    # Step 2: Sync to database
    print("\n[2/4] Syncing to database...")
    try:
        from webapp.database import init_db, SessionLocal
        from webapp.services.sync_service import seed_trusts, sync_all
        init_db()
        db = SessionLocal()
        try:
            seed_trusts(db)
            sync_all(db, OUTPUT_DIR, only_trusts=changed_trusts if changed_trusts is not None else None)
        finally:
            db.close()
        print("  Database synced.")
    except Exception as e:
        print(f"  DB sync failed (non-fatal): {e}")

    # Step 2b: Sync market data to SQLite
    print("\n[2b/4] Syncing market data...")
    try:
        from webapp.services.market_sync import sync_market_data
        db_mkt = SessionLocal()
        try:
            mkt_result = sync_market_data(db_mkt)
            print(f"  Market: {mkt_result['master_rows']} funds, {mkt_result['ts_rows']} TS rows, {len(mkt_result['report_keys'])} reports cached")
        finally:
            db_mkt.close()
    except Exception as e:
        print(f"  Market sync failed (non-fatal): {e}")

    # Step 3: Screener already computed + saved to DB by sync_market_data()
    print("\n[3/4] Rescoring screener...")
    print("  Screener rescored (via market sync).")

    # Checkpoint WAL + VACUUM so upload sends compact data (avoid OOM on Render)
    try:
        import sqlite3
        _db_path = str(PROJECT_ROOT / "data" / "etp_tracker.db")
        _conn = sqlite3.connect(_db_path)
        _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _size_before = Path(_db_path).stat().st_size / 1e6
        _conn.execute("VACUUM")
        _conn.close()
        _size_after = Path(_db_path).stat().st_size / 1e6
        print(f"  DB compacted: {_size_before:.0f}MB -> {_size_after:.0f}MB")
    except Exception as e:
        print(f"  DB compact failed (non-fatal): {e}")

    # Step 4: Upload DB to Render
    print("\n[4/4] Uploading database to Render...")
    upload_db_to_render()

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.0f}s ({elapsed/60:.1f}m) ===")


if __name__ == "__main__":
    main()
