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


def _market_synced_today() -> bool:
    """Check if market data was already synced from Bloomberg today (e.g. by watcher)."""
    from webapp.database import init_db, SessionLocal
    from sqlalchemy import text
    from datetime import date

    init_db()
    db = SessionLocal()
    try:
        today_str = date.today().isoformat()
        row = db.execute(text(
            "SELECT id FROM mkt_pipeline_runs "
            "WHERE source_file != 'auto' AND started_at >= :today "
            "ORDER BY id DESC LIMIT 1"
        ), {"today": today_str}).fetchone()
        return row is not None
    finally:
        db.close()


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
    """Run unified auto-classification on full ETP data, then scan for NEW unmapped funds."""
    # Phase 1: Unified auto-classify on full ETP dataset
    try:
        from webapp.services.data_engine import build_all
        from market.auto_classify import classify_all
        from market.db_writer import write_classifications, create_pipeline_run
        from webapp.database import init_db, SessionLocal

        init_db()
        db = SessionLocal()
        try:
            result = build_all()
            etp = result.get("master", None)
            if etp is not None and not etp.empty:
                classifications = classify_all(etp)
                run_id = create_pipeline_run(db, source_file="daily_classify")
                n_written = write_classifications(db, classifications, run_id=run_id)
                db.commit()
                print(f"  Unified classify: {n_written} funds classified")
            else:
                print("  No ETP data for classification")
        finally:
            db.close()
    except ImportError as e:
        print(f"  Unified classify not available ({e}), skipping")
    except Exception as e:
        print(f"  Unified classify failed: {e}")

    # Phase 2: Scan for NEW unmapped funds and auto-approve HIGH/MEDIUM to CSVs
    try:
        from tools.rules_editor.classify_engine import scan_unmapped, apply_classifications
    except ImportError:
        print("  classify_engine not available, skipping CSV scan.")
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
        print(f"  {len(candidates)} candidates all LOW confidence -- skipped")

    return len(approved)


# ===================================================================
# Step 5b: Scrape Total Returns (TotalRealReturns.com)
# ===================================================================
def scrape_total_returns():
    """Fetch total return data for all REX products + key competitors."""
    try:
        from scripts.scrape_total_returns import _scrape_single, save_to_disk
        from webapp.database import init_db, SessionLocal
        from sqlalchemy import text
        import time

        init_db()
        db = SessionLocal()
        try:
            rex_rows = db.execute(text(
                "SELECT ticker FROM mkt_master_data "
                "WHERE is_rex = 1 AND market_status = 'ACTV' "
                "AND (fund_type = 'ETF' OR fund_type = 'ETN') "
                "ORDER BY aum DESC"
            )).fetchall()
        finally:
            db.close()

        tickers = [r[0].replace(" US", "") for r in rex_rows]
        # Add key competitors
        comps = ["JEPI", "JEPQ", "QYLD", "QQQI", "SPYI", "TSLL", "NVDL",
                 "TQQQ", "SOXL", "ARKK", "IBIT", "BITO", "SPY", "QQQ"]
        all_tickers = tickers + [c for c in comps if c not in tickers]

        all_growth = {}
        all_stats = {}
        all_dates_set = set()

        for i, sym in enumerate(all_tickers):
            try:
                result = _scrape_single(sym)
                if result.get("growth"):
                    all_growth[sym] = result["growth"]
                    all_dates_set.update(result["dates"])
                    all_stats[sym] = result.get("stats", {})
            except Exception:
                pass
            if i < len(all_tickers) - 1:
                time.sleep(0.3)

        combined_dates = sorted(all_dates_set)
        combined = {
            "symbols": list(all_growth.keys()),
            "dates": combined_dates,
            "growth_series": all_growth,
            "stats": all_stats,
            "data_points": len(combined_dates),
            "date_range": [combined_dates[0], combined_dates[-1]] if combined_dates else [],
        }

        save_to_disk(combined)
        print(f"  Scraped {len(all_growth)} symbols, {len(combined_dates)} dates")
    except Exception as e:
        print(f"  Total returns scrape failed (non-fatal): {e}")


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
        # === Step 0: Sync trust universe (skip if ZIP is fresh — ran at 3:45 PM) ===
        try:
            from scripts.sync_trust_universe import ZIP_CACHE, ZIP_FALLBACK
            zip_path = ZIP_CACHE if ZIP_CACHE.parent.exists() else ZIP_FALLBACK
            zip_age = (time.time() - zip_path.stat().st_mtime) / 3600 if zip_path.exists() else 999
            if zip_age < 4:
                print(f"\n[0/12] Trust universe: ZIP is {zip_age:.0f}h old (ran at 3:45), skipping")
            else:
                print(f"\n[0/12] Syncing trust universe from SEC...")
                from scripts.sync_trust_universe import sync_universe
                cache_dir = _resolve_cache_dir()
                universe = sync_universe(skip_download=False, prime_cache_dir=cache_dir)
                if universe["new_trusts"]:
                    print(f"  ** {universe['new_trusts']} new trust(s) added **")
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

        # === Bloomberg file freshness check ===
        print("\n[5/12] Checking Bloomberg file freshness...")
        try:
            from screener.config import DATA_FILE as _bbg
            _bbg_mtime = datetime.fromtimestamp(_bbg.stat().st_mtime)
            _bbg_age = (datetime.now() - _bbg_mtime).total_seconds() / 3600
            _bbg_is_today = _bbg_mtime.date() == datetime.now().date()
            print(f"  File: {_bbg.name}")
            print(f"  Modified: {_bbg_mtime.strftime('%Y-%m-%d %H:%M')} ({_bbg_age:.1f}h ago)")
            print(f"  Source: {'OneDrive' if 'MasterFiles' in str(_bbg) else 'Local fallback'}")
            if not _bbg_is_today:
                print(f"  WARNING: Bloomberg file is from {_bbg_mtime.strftime('%m/%d')} — not today!")
                print(f"  Reports will use stale data. Check OneDrive sync.")
            else:
                print(f"  OK: fresh data from today")
        except Exception as e:
            print(f"  Bloomberg check failed: {e}")

        # === Market data + screener cache ===
        print("\n[6/12] Market Data + Screener Cache...")
        try:
            if _market_synced_today():
                print("  Market data already synced today (by Bloomberg watcher), skipping")
            else:
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

        # === Total Returns scrape ===
        print("\n[8/12] Scraping total returns data...")
        try:
            scrape_total_returns()
        except Exception as e:
            print(f"  Total returns scrape failed: {e}")

        # === Upload phase ===
        print("\n[9/12] Compacting DB...")
        try:
            compact_db()
        except Exception as e:
            print(f"  Compact failed: {e}")

        print("\n[9/10] Uploading screener cache to Render...")
        upload_screener_cache_to_render()

        print("\n[10/12] Uploading DB to Render...")
        upload_db_to_render()

        print("\n[11/12] Uploading structured notes DB to Render...")
        try:
            notes_db = PROJECT_ROOT / "data" / "structured_notes.db"
            if notes_db.exists():
                import gzip as _gz
                import requests as _req
                _gz_path = str(notes_db) + ".upload.gz"
                with open(notes_db, "rb") as _fin:
                    with _gz.open(_gz_path, "wb", compresslevel=6) as _fout:
                        while True:
                            _chunk = _fin.read(1024 * 1024)
                            if not _chunk:
                                break
                            _fout.write(_chunk)
                _api_key = _load_api_key()
                _headers = {"X-API-Key": _api_key} if _api_key else {}
                with open(_gz_path, "rb") as _f:
                    _resp = _req.post(
                        f"{RENDER_API_URL}/db/upload-notes",
                        files={"file": ("structured_notes.db.gz", _f, "application/gzip")},
                        headers=_headers, timeout=600,
                    )
                if _resp.status_code == 200:
                    _mb = Path(_gz_path).stat().st_size / 1e6
                    print(f"  Uploaded notes DB ({_mb:.0f} MB)")
                else:
                    print(f"  Notes upload failed: {_resp.status_code}")
                Path(_gz_path).unlink(missing_ok=True)
            else:
                print("  No structured_notes.db found")
        except Exception as e:
            print(f"  Notes upload failed (non-fatal): {e}")

        # === Wait until 5:30 PM to send emails ===
        print("\n[12/12] Sending email reports...")
        try:
            import subprocess
            now_dt = datetime.now()
            target = now_dt.replace(hour=17, minute=30, second=0, microsecond=0)
            if now_dt < target:
                wait_secs = (target - now_dt).total_seconds()
                print(f"  Pipeline done. Waiting until 5:30 PM to send ({wait_secs / 60:.0f} min)...")
                time.sleep(wait_secs)
            day_of_week = datetime.now().strftime("%A")
            if day_of_week in ("Saturday", "Sunday"):
                print(f"  {day_of_week} -- skipping email reports")
            else:
                # Daily report Mon-Fri
                print("  Sending daily report...")
                subprocess.run(
                    [sys.executable, str(PROJECT_ROOT / "scripts" / "send_email.py"), "send", "daily", "--force"],
                    cwd=str(PROJECT_ROOT), timeout=300,
                )
                # Weekly + L&I + Income + Flow on Monday only
                if day_of_week == "Monday":
                    print("  Monday -- sending weekly bundle (Weekly + L&I + Income + Flow)...")
                    subprocess.run(
                        [sys.executable, str(PROJECT_ROOT / "scripts" / "send_email.py"), "send", "weekly", "--force"],
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
