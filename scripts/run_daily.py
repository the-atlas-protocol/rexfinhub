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
    """Upload the local SQLite DB to Render (gzipped to avoid OOM).

    Raw DB is ~450MB but compresses to ~63MB with gzip.
    Render decompresses in streaming chunks on arrival.
    """
    import gzip
    import requests
    import tempfile

    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    if not db_path.exists():
        print("  No local database found, skipping upload.")
        return

    api_key = _load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}

    # Compress to temp file (450MB -> ~63MB)
    gz_path = str(db_path) + ".upload.gz"
    try:
        raw_mb = db_path.stat().st_size / 1e6
        print(f"  Compressing {raw_mb:.0f} MB...", end=" ", flush=True)
        with open(db_path, "rb") as f_in:
            with gzip.open(gz_path, "wb", compresslevel=6) as f_out:
                while True:
                    chunk = f_in.read(1024 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
        gz_mb = Path(gz_path).stat().st_size / 1e6
        print(f"{gz_mb:.0f} MB")

        with open(gz_path, "rb") as f:
            resp = requests.post(
                f"{RENDER_API_URL}/db/upload",
                files={"file": ("etp_tracker.db.gz", f, "application/gzip")},
                headers=headers,
                timeout=600,
            )
        if resp.status_code == 200:
            print(f"  Uploaded to Render ({gz_mb:.0f} MB compressed, {raw_mb:.0f} MB raw)")
        else:
            print(f"  Upload failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  Upload failed (non-fatal): {e}")
    finally:
        try:
            Path(gz_path).unlink(missing_ok=True)
        except Exception:
            pass


def upload_screener_cache_to_render() -> None:
    """Upload pre-computed screener cache JSON to Render (237KB, instant).

    This ensures the candidates/evaluator pages show data on the live site
    without needing the full 1GB+ DB upload to succeed.
    """
    import json
    import requests

    try:
        from webapp.services.screener_3x_cache import get_3x_analysis
        data = get_3x_analysis()
        if not data:
            print("  No screener cache in memory, skipping.")
            return

        # Write to temp file
        cache_path = PROJECT_ROOT / "temp" / "screener_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f, default=str)
        size_kb = cache_path.stat().st_size / 1024

        # Login as admin and upload
        session = requests.Session()
        session.post(f"{DASHBOARD_URL}/login", data={"password": "***REDACTED***", "next": "/"})
        with open(cache_path, "rb") as f:
            resp = session.post(
                f"{DASHBOARD_URL}/admin/upload/screener-cache",
                files={"file": ("screener_cache.json", f, "application/json")},
                timeout=30,
            )
        if resp.status_code == 200:
            keys = resp.json().get("keys", [])
            print(f"  Uploaded {size_kb:.0f} KB ({len(keys)} keys: {', '.join(keys[:5])}...)")
        else:
            print(f"  Upload failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  Screener cache upload failed (non-fatal): {e}")


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

    # Step 4: Upload screener cache to Render (237KB, always works)
    print("\n[4/5] Uploading screener cache to Render...")
    upload_screener_cache_to_render()

    # Step 5: Upload DB to Render (large, may fail on Starter plan)
    print("\n[5/5] Uploading database to Render...")
    upload_db_to_render()

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.0f}s ({elapsed/60:.1f}m) ===")


if __name__ == "__main__":
    main()
