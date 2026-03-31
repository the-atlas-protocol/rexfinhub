"""
Full Daily Sync — SEC filings, structured notes, market data, Render upload.

Scrapes to C: (fast SSD), archives to D: (cold storage) after completion.
Computes screener cache with all Bloomberg-dependent data (candidates, evaluator,
products) and uploads to Render so the live site has everything.

Usage:
    python scripts/run_daily.py          # full sync
    python scripts/run_daily.py --sec    # SEC filings only
    python scripts/run_daily.py --notes  # structured notes only
    python scripts/run_daily.py --market # market data + cache only
    python scripts/run_daily.py --upload # upload to Render only
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import time
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUTPUT_DIR = PROJECT_ROOT / "outputs"
SINCE_DATE = "2024-01-01"
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"
RENDER_API_URL = "https://rex-etp-tracker.onrender.com/api/v1"

# Cache paths: scrape to C: (fast SSD), archive to D: (cold USB storage)
CACHE_LOCAL = PROJECT_ROOT / "cache"          # C: SSD — fast writes during scrape
CACHE_ARCHIVE = Path("D:/sec-data/cache/rexfinhub")  # D: USB — cold storage, 276GB history

# Structured notes
NOTES_PROJECT = Path("C:/Projects/structured-notes")


def _load_env(key: str) -> str:
    """Load a value from config/.env or environment."""
    env_val = os.environ.get(key, "")
    if env_val:
        return env_val
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _load_api_key() -> str:
    return _load_env("API_KEY")


def _d_available() -> bool:
    return CACHE_ARCHIVE.parent.exists()


def _resolve_cache_dir() -> Path:
    """Scrape to C: always (fast). D: used for reads via symlink/fallback."""
    CACHE_LOCAL.mkdir(parents=True, exist_ok=True)
    # Copy submissions cache from D: to C: if D: has it and C: doesn't
    if _d_available():
        for subdir in ("submissions",):
            src = CACHE_ARCHIVE / subdir
            dst = CACHE_LOCAL / subdir
            if src.exists() and not dst.exists():
                print(f"  Linking {subdir} from D: cache...")
                # Use junction (symlink) so reads hit D:, writes go to C:
                # On Windows, shutil.copytree is safer than symlinks
                dst.mkdir(parents=True, exist_ok=True)
    return CACHE_LOCAL


def archive_cache_to_d():
    """After scrape: copy new files from C: cache to D: archive, then clean C:."""
    if not _d_available():
        print("  D: not available — cache stays on C:")
        return

    c_web = CACHE_LOCAL / "web"
    d_web = CACHE_ARCHIVE / "web"
    c_sub = CACHE_LOCAL / "submissions"
    d_sub = CACHE_ARCHIVE / "submissions"

    copied = 0
    for src_dir, dst_dir in [(c_web, d_web), (c_sub, d_sub)]:
        if not src_dir.exists():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src_file in src_dir.rglob("*"):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel
            if not dst_file.exists() or src_file.stat().st_size != dst_file.stat().st_size:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                copied += 1

    if copied:
        print(f"  Archived {copied} new files from C: to D:")

    # Validate: count files match
    c_count = sum(1 for _ in CACHE_LOCAL.rglob("*") if _.is_file()) if CACHE_LOCAL.exists() else 0
    d_count = sum(1 for _ in CACHE_ARCHIVE.rglob("*") if _.is_file()) if CACHE_ARCHIVE.exists() else 0
    print(f"  Cache files — C: {c_count}, D: {d_count}")

    # Clean C: if archive succeeded
    if copied > 0 and d_count >= c_count:
        shutil.rmtree(CACHE_LOCAL, ignore_errors=True)
        print(f"  C: cache cleaned ({c_count} files)")
    elif c_count > 0:
        print(f"  C: cache NOT cleaned (D: has fewer files)")


# ===================================================================
# Step 1: SEC Filing Pipeline
# ===================================================================
def run_sec_pipeline():
    """Scrape SEC filings for 2,475 trusts."""
    from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db

    cache_dir = _resolve_cache_dir()
    print(f"  Cache: {cache_dir}")

    ciks, overrides = load_ciks_from_db()
    n, changed_trusts = run_pipeline(
        ciks=ciks,
        overrides=overrides,
        since=SINCE_DATE,
        refresh_submissions=True,
        user_agent=USER_AGENT,
        etf_only=True,
        cache_dir=cache_dir,
    )
    print(f"  Processed {n} trusts ({len(changed_trusts) if changed_trusts else 0} with new filings)")
    return changed_trusts


# ===================================================================
# Step 2: DB Sync
# ===================================================================
def run_db_sync(changed_trusts=None):
    """Sync pipeline CSVs into SQLite database."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.sync_service import seed_trusts, sync_all

    init_db()
    db = SessionLocal()
    try:
        seed_trusts(db)
        sync_all(db, OUTPUT_DIR, only_trusts=changed_trusts if changed_trusts else None)
    finally:
        db.close()
    print("  Database synced.")


# ===================================================================
# Step 3: Structured Notes
# ===================================================================
def run_structured_notes():
    """Discover + extract structured notes from SEC."""
    if not NOTES_PROJECT.exists():
        print("  structured-notes project not found, skipping.")
        return

    import subprocess
    # Discovery
    print("  Discovering new filings...")
    result = subprocess.run(
        [sys.executable, "cli.py", "discover"],
        cwd=str(NOTES_PROJECT), capture_output=True, text=True, timeout=600
    )
    if result.returncode == 0:
        # Count new filings from output
        lines = [l for l in result.stdout.split('\n') if 'new filings' in l]
        for l in lines[-5:]:
            print(f"    {l.strip()}")
    else:
        print(f"  Discovery failed: {result.stderr[-200:]}")
        return

    # Extraction
    print("  Extracting products...")
    result = subprocess.run(
        [sys.executable, "run_extraction.py", "--since", "2024"],
        cwd=str(NOTES_PROJECT), capture_output=True, text=True, timeout=600
    )
    if result.returncode == 0:
        lines = [l for l in result.stdout.split('\n') if 'processed' in l.lower() or 'products' in l.lower() or 'errors' in l.lower()]
        for l in lines[-5:]:
            print(f"    {l.strip()}")
    else:
        print(f"  Extraction failed: {result.stderr[-200:]}")


# ===================================================================
# Step 4: Market Data + Screener Cache
# ===================================================================
def run_market_sync():
    """Sync Bloomberg market data and compute full screener cache."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.market_sync import sync_market_data

    init_db()
    db = SessionLocal()
    try:
        result = sync_market_data(db)
        print(f"  Market: {result['master_rows']} funds, {result['ts_rows']} TS rows")
    finally:
        db.close()

    # Recompute screener cache (includes candidates, evaluator, li_products)
    print("  Computing screener cache...")
    from webapp.services.screener_3x_cache import compute_and_cache
    data = compute_and_cache()
    cache_path = PROJECT_ROOT / "temp" / "screener_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f, default=str)
    size_kb = cache_path.stat().st_size / 1024
    print(f"  Cache: {size_kb:.0f} KB — {len(data.get('two_x_candidates', []))} 2x, "
          f"{len(data.get('eval_cache', {}))} eval, {len(data.get('li_products', []))} products")


# ===================================================================
# Step 5: Classification (new funds, rex funds, exclusions)
# ===================================================================
def run_classification():
    """Scan for unmapped funds, auto-classify HIGH/MEDIUM confidence."""
    try:
        from tools.rules_editor.classify_engine import scan_unmapped, apply_classifications
    except ImportError:
        print("  classify_engine not available, skipping.")
        return 0

    result = scan_unmapped(since_days=30)
    candidates = result.get("candidates", [])
    outside = result.get("outside", [])

    if not candidates:
        print(f"  No new candidates ({len(outside)} outside-category funds)")
        return 0

    # Auto-approve HIGH and MEDIUM confidence
    approved = [c for c in candidates if c.get("confidence") in ("HIGH", "MEDIUM")]
    if approved:
        apply_classifications(approved)
        # Sync rules CSVs
        rules_dir = PROJECT_ROOT / "data" / "rules"
        config_dir = PROJECT_ROOT / "config" / "rules"
        for csv_name in ["fund_mapping.csv", "issuer_mapping.csv",
                         "attributes_LI.csv", "attributes_CC.csv",
                         "attributes_Crypto.csv", "attributes_Defined.csv",
                         "attributes_Thematic.csv"]:
            src = rules_dir / csv_name
            dst = config_dir / csv_name
            if src.exists():
                shutil.copy2(src, dst)
        print(f"  Classified {len(approved)} funds ({len(candidates) - len(approved)} LOW confidence skipped)")
    else:
        print(f"  {len(candidates)} candidates all LOW confidence — skipped")

    return len(approved)


# ===================================================================
# Step 6: Compact DB
# ===================================================================
def compact_db():
    """WAL checkpoint + VACUUM for clean upload."""
    import sqlite3
    db_path = str(PROJECT_ROOT / "data" / "etp_tracker.db")
    if not Path(db_path).exists():
        return
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size_before = Path(db_path).stat().st_size / 1e6
    conn.execute("VACUUM")
    conn.close()
    size_after = Path(db_path).stat().st_size / 1e6
    print(f"  DB compacted: {size_before:.0f}MB -> {size_after:.0f}MB")


# ===================================================================
# Step 6: Upload to Render
# ===================================================================
def upload_screener_cache_to_render():
    """Upload screener cache JSON (~500KB) to Render."""
    import requests

    cache_path = PROJECT_ROOT / "temp" / "screener_cache.json"
    if not cache_path.exists():
        # Try computing it
        try:
            from webapp.services.screener_3x_cache import get_3x_analysis
            data = get_3x_analysis()
            if data:
                with open(cache_path, "w") as f:
                    json.dump(data, f, default=str)
        except Exception:
            pass

    if not cache_path.exists():
        print("  No screener cache found, skipping.")
        return

    try:
        session = requests.Session()
        session.post(f"{DASHBOARD_URL}/login", data={"password": _load_env("ADMIN_PASSWORD"), "next": "/"})
        with open(cache_path, "rb") as f:
            resp = session.post(
                f"{DASHBOARD_URL}/admin/upload/screener-cache",
                files={"file": ("screener_cache.json", f, "application/json")},
                timeout=60,
            )
        if resp.status_code == 200:
            keys = resp.json().get("keys", [])
            print(f"  Uploaded {cache_path.stat().st_size / 1024:.0f} KB ({len(keys)} keys)")
        else:
            print(f"  Upload failed: {resp.status_code}")
    except Exception as e:
        print(f"  Screener cache upload failed (non-fatal): {e}")


def upload_db_to_render():
    """Upload stripped SQLite DB to Render (no 13F tables)."""
    import gzip
    import sqlite3
    import requests

    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    if not db_path.exists():
        print("  No local database found, skipping upload.")
        return

    api_key = _load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    render_db = PROJECT_ROOT / "data" / "etp_tracker_render.db"
    gz_path = str(render_db) + ".upload.gz"

    try:
        print("  Stripping 13F tables...", end=" ", flush=True)
        shutil.copy2(db_path, render_db)
        conn = sqlite3.connect(str(render_db))
        for table in ("holdings", "institutions", "cusip_mappings"):
            conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        conn.execute("VACUUM")
        conn.close()
        raw_mb = render_db.stat().st_size / 1e6
        print(f"{raw_mb:.0f} MB (was {db_path.stat().st_size / 1e6:.0f} MB)")

        print("  Compressing...", end=" ", flush=True)
        with open(render_db, "rb") as f_in:
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
            print(f"  Uploaded to Render ({gz_mb:.0f} MB compressed)")
        else:
            print(f"  Upload failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  Upload failed (non-fatal): {e}")
    finally:
        for p in (gz_path, str(render_db)):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


# ===================================================================
# Main
# ===================================================================
def main():
    parser = argparse.ArgumentParser(description="Full daily sync")
    parser.add_argument("--sec", action="store_true", help="SEC filings only")
    parser.add_argument("--notes", action="store_true", help="Structured notes only")
    parser.add_argument("--market", action="store_true", help="Market data + cache only")
    parser.add_argument("--upload", action="store_true", help="Upload to Render only")
    args = parser.parse_args()

    # If no flags, run everything
    run_all = not (args.sec or args.notes or args.market or args.upload)

    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== REX Full Sync ({today}) ===")

    changed_trusts = None

    if run_all:
        # === Step 0: Sync trust universe from SEC ===
        print("\n[0/10] Syncing trust universe from SEC...")
        try:
            from scripts.sync_trust_universe import sync_universe
            cache_dir = _resolve_cache_dir()
            universe = sync_universe(skip_download=False, prime_cache_dir=cache_dir)
            if universe["new_trusts"]:
                print(f"  ** {universe['new_trusts']} new trust(s) added — pipeline will scrape them **")
            if universe["cache_primed"]:
                print(f"  ** Primed {universe['cache_primed']} cache files for new trusts **")
        except Exception as e:
            print(f"  Universe sync failed (non-fatal): {e}")

        # === PARALLEL PHASE: SEC scrape + Structured Notes simultaneously ===
        # These use different SEC endpoints and different databases — safe to run together
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print("\n[1-2/10] SEC Pipeline + Structured Notes (parallel)...")

        sec_result = [None]  # mutable container for thread result
        notes_ok = [False]

        def _run_sec():
            try:
                sec_result[0] = run_sec_pipeline()
            except Exception as e:
                print(f"  SEC pipeline failed: {e}")

        def _run_notes():
            try:
                run_structured_notes()
                notes_ok[0] = True
            except Exception as e:
                print(f"  Structured notes failed: {e}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_run_sec), pool.submit(_run_notes)]
            for f in as_completed(futures):
                pass  # exceptions logged inside each function

        changed_trusts = sec_result[0]

        # === SEQUENTIAL PHASE: DB sync (needs SEC results) ===
        print("\n[3/8] Syncing filings to DB...")
        try:
            run_db_sync(changed_trusts)
        except Exception as e:
            print(f"  DB sync failed: {e}")

        # === Archive C: cache to D: ===
        print("\n[4/8] Archiving cache C: -> D:...")
        try:
            archive_cache_to_d()
        except Exception as e:
            print(f"  Archive failed: {e}")

        # === Market data + screener cache ===
        print("\n[5/10] Market Data + Screener Cache...")
        try:
            run_market_sync()
        except Exception as e:
            print(f"  Market sync failed: {e}")

        # === Archive screener snapshot ===
        print("\n[6/10] Archiving screener snapshot...")
        try:
            from scripts.archive_screener import archive_daily
            archive_daily()
        except Exception as e:
            print(f"  Screener archive failed (non-fatal): {e}")

        # === Classification ===
        print("\n[7/10] Classifying new funds...")
        try:
            run_classification()
        except Exception as e:
            print(f"  Classification failed: {e}")

        # === Upload phase ===
        print("\n[8/10] Compacting DB...")
        try:
            compact_db()
        except Exception as e:
            print(f"  Compact failed: {e}")

        print("\n[9/10] Uploading screener cache to Render...")
        upload_screener_cache_to_render()

        print("\n[10/11] Uploading DB to Render...")
        upload_db_to_render()

        # === Send email reports ===
        print("\n[11/11] Sending email reports...")
        try:
            import subprocess
            # Send all reports every day (daily + weekly + L&I + Income + Flow)
            print("  Sending all reports...")
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "send_email.py"), "send", "all", "--force"],
                cwd=str(PROJECT_ROOT), timeout=300,
            )
        except Exception as e:
            print(f"  Email send failed (non-fatal): {e}")

    else:
        # Partial runs (sequential)
        if args.sec:
            print("\n[1] SEC Filing Pipeline...")
            try:
                changed_trusts = run_sec_pipeline()
            except Exception as e:
                print(f"  Pipeline failed: {e}")
            print("\n[2] Syncing filings to DB...")
            try:
                run_db_sync(changed_trusts)
            except Exception as e:
                print(f"  DB sync failed: {e}")
            print("\n[3] Archiving cache...")
            try:
                archive_cache_to_d()
            except Exception as e:
                print(f"  Archive failed: {e}")

        if args.notes:
            print("\n[1] Structured Notes...")
            try:
                run_structured_notes()
            except Exception as e:
                print(f"  Structured notes failed: {e}")

        if args.market:
            print("\n[1] Market Data + Screener Cache...")
            try:
                run_market_sync()
            except Exception as e:
                print(f"  Market sync failed: {e}")
            print("\n[2] Archiving screener snapshot...")
            try:
                from scripts.archive_screener import archive_daily
                archive_daily()
            except Exception as e:
                print(f"  Archive failed (non-fatal): {e}")

        if args.upload:
            print("\n[1] Compacting DB...")
            try:
                compact_db()
            except Exception as e:
                print(f"  Compact failed: {e}")
            print("\n[2] Uploading screener cache...")
            upload_screener_cache_to_render()
            print("\n[3] Uploading DB...")
            upload_db_to_render()

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.0f}s ({elapsed / 60:.1f}m) ===")


if __name__ == "__main__":
    main()
