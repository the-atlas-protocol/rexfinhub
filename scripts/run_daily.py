"""
Daily Pipeline Runner

Run this script daily at 8am via Windows Task Scheduler.
It refreshes all trust data, generates Excel files, and sends email digest.

Setup Task Scheduler (run PowerShell as Admin):
    schtasks /create /tn "ETP_Filing_Tracker" /tr "python C:\\Projects\\rexfinhub\\scripts\\run_daily.py" /sc daily /st 08:00 /f

To run manually:
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
from etp_tracker.email_alerts import send_digest_email


OUTPUT_DIR = PROJECT_ROOT / "outputs"
SINCE_DATE = None  # No date limit - process all filings (incremental manifest handles speed)
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"
RENDER_API_URL = "https://rex-etp-tracker.onrender.com/api/v1"


def export_excel(output_dir: Path) -> None:
    """Generate combined Excel files from all trust outputs."""
    import pandas as pd

    # Combine all fund status
    frames_status = []
    frames_names = []
    for folder in output_dir.iterdir():
        if not folder.is_dir():
            continue
        for f4 in folder.glob("*_4_Fund_Status.csv"):
            frames_status.append(pd.read_csv(f4, dtype=str))
        for f5 in folder.glob("*_5_Name_History.csv"):
            frames_names.append(pd.read_csv(f5, dtype=str))

    if frames_status:
        df = pd.concat(frames_status, ignore_index=True)
        df.to_excel(output_dir / "etp_tracker_summary.xlsx", index=False, engine="openpyxl")
        print(f"  Excel: etp_tracker_summary.xlsx ({len(df)} funds)")

    if frames_names:
        df = pd.concat(frames_names, ignore_index=True)
        df.to_excel(output_dir / "etp_name_history.xlsx", index=False, engine="openpyxl")
        print(f"  Excel: etp_name_history.xlsx ({len(df)} entries)")


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
                timeout=120,
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
    print("\n[1/5] Running pipeline...")
    ciks, overrides = load_ciks_from_db()
    n = run_pipeline(
        ciks=ciks,
        overrides=overrides,
        since=SINCE_DATE,
        refresh_submissions=True,
        user_agent=USER_AGENT,
    )
    print(f"  Processed {n} trusts")

    # Step 2: Export Excel
    print("\n[2/5] Exporting Excel...")
    export_excel(OUTPUT_DIR)

    # Step 3: Sync to database
    print("\n[3/5] Syncing to database...")
    try:
        from webapp.database import init_db, SessionLocal
        from webapp.services.sync_service import seed_trusts, sync_all
        init_db()
        db = SessionLocal()
        try:
            seed_trusts(db)
            sync_all(db, OUTPUT_DIR)
        finally:
            db.close()
        print("  Database synced.")
    except Exception as e:
        print(f"  DB sync failed (non-fatal): {e}")

    # Step 3.25: Sync market data to SQLite
    print("\n[3.25/5] Syncing market data...")
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

    # Step 3.5: Rescore screener
    print("\n[3.5/5] Rescoring screener...")
    try:
        from webapp.services.screener_3x_cache import compute_and_cache
        compute_and_cache()
        print("  Screener rescored.")
    except Exception as e:
        print(f"  Screener rescore failed (non-fatal): {e}")

    # Step 4: Upload DB to Render
    print("\n[4/5] Uploading database to Render...")
    upload_db_to_render()

    # Step 5: Save digest + send email if configured
    edition = "evening" if datetime.now().hour >= 14 else "morning"
    _label = "Evening Update" if edition == "evening" else "Morning Brief"
    print(f"\n[5/5] Building digest ({_label})...")
    from etp_tracker.email_alerts import build_digest_html_from_db
    from webapp.database import SessionLocal as _SL
    _db = _SL()
    try:
        html = build_digest_html_from_db(_db, DASHBOARD_URL, edition=edition)
    finally:
        _db.close()
    digest_path = OUTPUT_DIR / "daily_digest.html"
    digest_path.write_text(html, encoding="utf-8")
    print(f"  Saved: {digest_path}")

    from etp_tracker.email_alerts import send_digest_from_db
    _db2 = _SL()
    try:
        sent = send_digest_from_db(_db2, DASHBOARD_URL, edition=edition)
    finally:
        _db2.close()
    if not sent:
        print("  Email skipped (SMTP not configured). Opening digest in browser...")
        import webbrowser
        webbrowser.open(str(digest_path.resolve()))

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.0f}s ({elapsed/60:.1f}m) ===")


if __name__ == "__main__":
    main()
