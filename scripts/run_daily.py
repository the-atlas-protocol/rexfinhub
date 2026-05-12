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

# Perf fix (Sys-I): CSV staging dir for CSV-first xlsm path.
# export_sheets.py writes w1-w4 CSVs here; market_sync + classification read them,
# eliminating the second openpyxl parse (~30-45s saved per daily run).
_BLOOMBERG_CSV_STAGING = PROJECT_ROOT / "temp" / "bloomberg_sheets"


def _market_synced_today() -> bool:
    """Check if market data was already synced from Bloomberg today.

    Looks for any completed pipeline run started today, regardless of
    source_file. The old logic excluded source_file='auto' runs, which
    matched every Bloomberg timer run (all of which use source_file='auto'),
    making this always return False and forcing the daily pipeline to
    re-run market sync — the cascade that caused 2026-04-14's disk-full
    pipeline failure.
    """
    from webapp.database import init_db, SessionLocal
    from sqlalchemy import text
    from datetime import date

    init_db()
    db = SessionLocal()
    try:
        today_str = date.today().isoformat()
        row = db.execute(text(
            "SELECT id FROM mkt_pipeline_runs "
            "WHERE status = 'completed' AND started_at >= :today "
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
        sync_all(db, OUTPUT_DIR, only_trusts=changed_trusts if changed_trusts is not None else None)
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
def _export_bloomberg_sheets() -> Path | None:
    """Export bloomberg_daily_file.xlsm sheets to CSV staging dir.

    Perf fix (Sys-I): one openpyxl parse → CSVs, then market_sync + classify
    both read the CSVs.  Eliminates the second openpyxl parse in run_classification()
    (~30-45s saved per daily run).

    Returns the csv_dir Path on success, or None if export fails (callers fall back
    to the legacy build_all() xlsm path automatically).
    """
    try:
        from webapp.services.bbg_file import get_bloomberg_file
        xlsm = get_bloomberg_file()
    except Exception as e:
        print(f"  Bloomberg file not found, CSV export skipped: {e}")
        return None

    csv_dir = _BLOOMBERG_CSV_STAGING
    csv_dir.mkdir(parents=True, exist_ok=True)

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "export_sheets.py"),
             str(xlsm), str(csv_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  WARNING: export_sheets.py failed (rc={result.returncode}), "
                  f"falling back to xlsm: {result.stderr.strip()[:200]}")
            return None
        w1_csv = csv_dir / "w1.csv"
        if not w1_csv.exists():
            print("  WARNING: w1.csv not produced, falling back to xlsm")
            return None
        print(f"  Bloomberg sheets exported to {csv_dir.name}/")
        return csv_dir
    except Exception as e:
        print(f"  WARNING: Bloomberg sheet export failed, falling back to xlsm: {e}")
        return None


def run_market_sync():
    """Sync Bloomberg market data and compute full screener cache."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.market_sync import sync_market_data

    # Perf fix (Sys-I): export xlsm → CSVs once; both market_sync and
    # run_classification() will read the CSVs (eliminates double openpyxl parse).
    csv_dir = _export_bloomberg_sheets()

    init_db()
    db = SessionLocal()
    try:
        result = sync_market_data(db, csv_dir=csv_dir)
        print(f"  Market: {result['master_rows']} funds, {result['ts_rows']} TS rows")
    finally:
        db.close()

    # Brand application — Bloomberg sync clears issuer_display to NULL.
    # Re-derive (Layer 1 regex) + apply so iShares/SPDR/Invesco/etc don't
    # show as NULL in /issuers/, /market/issuer, etc. (~3,500 fund impact)
    print("  Deriving + applying issuer brands...")
    import subprocess as _sp
    try:
        _r1 = _sp.run([sys.executable, str(PROJECT_ROOT / "scripts" / "derive_issuer_brands.py")],
                      capture_output=True, text=True, timeout=120)
        if _r1.returncode != 0:
            print(f"  WARN: derive_issuer_brands exit={_r1.returncode}: {_r1.stderr[:200]}")
        _r2 = _sp.run([sys.executable, str(PROJECT_ROOT / "scripts" / "apply_issuer_brands.py")],
                      capture_output=True, text=True, timeout=120)
        if _r2.returncode != 0:
            print(f"  WARN: apply_issuer_brands exit={_r2.returncode}: {_r2.stderr[:200]}")
        # Echo summary lines from apply output
        for line in _r2.stdout.splitlines():
            if "Applied:" in line or "No-ops:" in line:
                print(f"  {line.strip()}")
    except Exception as e:
        print(f"  WARN: brand application skipped: {e}")

    # Classification sweep (3-axis taxonomy: asset_class x primary_strategy
    # x sub_strategy + 14 attribute columns). Bloomberg sync also clears
    # these to NULL on re-import. HIGH-confidence only auto-applies; MED/LOW
    # go to ClassificationProposal queue for admin review. Strict no-overwrite
    # safeguard protects all curated values. Audit log in classification_audit_log.
    print("  Applying classification sweep (HIGH-confidence only)...")
    try:
        # --apply-medium added 2026-05-11 per Ryu's batch approval of the MEDIUM
        # buckets (1,038 Plain Beta + 150 Income + 69 Defined Outcome). Without
        # this, the manual approvals get wiped on every daily sync. LOW
        # confidence proposals still queue for admin review (mostly Plain Beta
        # defaults for generic equity funds — not auto-applied).
        _r3 = _sp.run([sys.executable, str(PROJECT_ROOT / "scripts" / "apply_classification_sweep.py"),
                       "--apply", "--apply-medium"],
                      capture_output=True, text=True, timeout=600)
        if _r3.returncode != 0:
            print(f"  WARN: classification_sweep exit={_r3.returncode}: {_r3.stderr[:200]}")
        # Echo summary lines from sweep output
        for line in _r3.stdout.splitlines():
            if any(k in line for k in ["HIGH-conf fills:", "MED/LOW skipped:", "Conflicts:", "Overwrites:", "Proposals queued:"]):
                print(f"  {line.strip()}")
    except Exception as e:
        print(f"  WARN: classification sweep skipped: {e}")

    # Capital Markets product registry import — pulls from xlsx in user Downloads.
    # Currently file-based; future Phase 2 could derive as SQL view over rex_products.
    # On Render the source xlsx is absent — import_capm.py exits 0 with a "skipping"
    # message in that case, so this hook is safe to leave wired in everywhere.
    print("  Importing Capital Markets product registry...")
    try:
        _r4 = _sp.run([sys.executable, str(PROJECT_ROOT / "scripts" / "import_capm.py")],
                      capture_output=True, text=True, timeout=60)
        if _r4.returncode != 0:
            print(f"  WARN: import_capm exit={_r4.returncode}: {_r4.stderr[:200]}")
        for line in _r4.stdout.splitlines():
            if any(k in line.lower() for k in ("inserted:", "updated:", "trust & aps:", "skipping")):
                print(f"  {line.strip()}")
    except Exception as e:
        print(f"  WARN: capm import skipped: {e}")

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
        from webapp.services.data_engine import build_all, build_all_from_csvs
        from market.auto_classify import classify_all
        from market.db_writer import write_classifications, create_pipeline_run
        from webapp.database import init_db, SessionLocal

        init_db()
        db = SessionLocal()
        try:
            # Perf fix (Sys-I): reuse pre-exported CSVs from run_market_sync() when
            # available — eliminates the second openpyxl parse.  Fall back to build_all()
            # (xlsm) if CSVs are absent (e.g. classify-only runs).
            _csv_dir = _BLOOMBERG_CSV_STAGING
            _w1_ready = (_csv_dir / "w1.csv").exists()
            if _w1_ready:
                result = build_all_from_csvs(_csv_dir)
            else:
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
        msg = f"Unified classify import failed: {e}. New funds will go unclassified."
        print(f"  CRITICAL: {msg}")
        errors.append(msg)
        try:
            from etp_tracker.email_alerts import send_critical_alert
            send_critical_alert("Classification Engine Missing", msg)
        except Exception:
            pass
    except Exception as e:
        print(f"  Unified classify failed: {e}")

    # Phase 2: Scan for NEW unmapped funds and auto-approve HIGH/MEDIUM to CSVs
    try:
        from tools.rules_editor.classify_engine import scan_unmapped, apply_classifications
    except ImportError as e:
        msg = f"classify_engine import failed: {e}. New funds will go unclassified."
        print(f"  CRITICAL: {msg}")
        errors.append(msg)
        try:
            from etp_tracker.email_alerts import send_critical_alert
            send_critical_alert("Classification Engine Missing", msg)
        except Exception:
            pass
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
        # Rules CSVs are written directly to RULES_DIR (config/rules/) by classify_engine.
        # No copy needed — single source of truth.
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
    """Upload screener cache JSON (~500KB) to Render.

    Raises RuntimeError on failure so the caller can surface it in the
    pipeline critical alert (was silently swallowed before).
    """
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

    # Switched from session-cookie POST against /admin/upload/screener-cache
    # to bearer-token POST against /api/v1/uploads/screener-cache (2026-05-12).
    # The admin route became unreachable from machine-to-machine clients once
    # CSRF middleware (audit fix R8) started rejecting multipart POSTs that
    # lack an X-CSRF-Token header.
    token = _load_env("RENDER_UPLOAD_TOKEN")
    if not token:
        raise RuntimeError(
            "Screener cache upload to Render failed: RENDER_UPLOAD_TOKEN is not set "
            "(add it to config/.env on the VPS and to the Render service env)."
        )

    try:
        with open(cache_path, "rb") as f:
            resp = requests.post(
                f"{RENDER_API_URL}/uploads/screener-cache",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("screener_cache.json", f, "application/json")},
                timeout=60,
            )
        if resp.status_code == 200:
            keys = resp.json().get("keys", [])
            print(f"  Uploaded {cache_path.stat().st_size / 1024:.0f} KB ({len(keys)} keys)")
        else:
            raise RuntimeError(
                f"Screener cache upload to Render failed: HTTP {resp.status_code}"
            )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Screener cache upload error: {e}") from e


def upload_parquets_to_render():
    """Upload analysis parquets to Render persistent disk (/strategy/* pages depend on these).

    Uploads each parquet in data/analysis/ whose name is on the allowlist.
    Non-fatal: a missing file is skipped with a warning; a failed upload is
    logged but does not abort the rest of the upload phase.
    """
    import requests

    api_key = _load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}

    # Must match ALLOWED_PARQUET_NAMES in webapp/routers/api.py
    target_parquets = [
        "whitespace_v4.parquet",
        "whitespace_candidates.parquet",
        "filing_race.parquet",
        "issuer_cadence.parquet",
        "bbg_timeseries_panel.parquet",
        "competitor_counts.parquet",
        "filed_underliers.parquet",
        "launch_candidates.parquet",
    ]

    analysis_dir = PROJECT_ROOT / "data" / "analysis"
    uploaded = 0
    skipped = 0
    failed = 0

    for name in target_parquets:
        path = analysis_dir / name
        if not path.exists():
            print(f"  Parquet not found, skipping: {name}")
            skipped += 1
            continue
        size_kb = path.stat().st_size / 1024
        try:
            with open(path, "rb") as f:
                resp = requests.post(
                    f"{RENDER_API_URL}/parquets/upload",
                    params={"name": name},
                    files={"file": (name, f, "application/octet-stream")},
                    headers=headers,
                    timeout=120,
                )
            if resp.status_code == 200:
                print(f"  Parquet OK: {name} ({size_kb:.0f} KB)")
                uploaded += 1
            else:
                print(f"  Parquet FAILED: {name} — HTTP {resp.status_code} {resp.text[:120]}")
                failed += 1
        except Exception as e:
            print(f"  Parquet FAILED: {name} — {e}")
            failed += 1

    print(f"  Parquets: {uploaded} uploaded, {skipped} skipped, {failed} failed")


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
        print("  Preparing lean Render upload...", end=" ", flush=True)
        # WAL checkpoint first — the watcher daemons write continuously,
        # so .db-wal typically holds the most recent minutes of inserts.
        # Without TRUNCATE, shutil.copy2 grabs a stale main-file snapshot
        # and Render ends up missing all recently-added trusts/filings.
        src = sqlite3.connect(str(db_path), isolation_level=None)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            src.close()
        shutil.copy2(db_path, render_db)
        conn = sqlite3.connect(str(render_db), isolation_level=None)
        # MINIMAL drop list — only tables the live webapp NEVER queries.
        # Anything that any /webapp route touches stays. We keep the full row
        # history (no more 90-day filings trim, no more 12-month time-series
        # trim) so fund detail pages, filing explorer, and historical charts
        # all populate correctly on Render.
        #
        # Verified safe to drop:
        #   - analysis_results: empty, Claude analysis output (not yet wired in)
        #   - pipeline_runs:    local SEC pipeline run tracking, only queried locally
        #   - screener_uploads: local upload audit log
        # Note: holdings/institutions/cusip_mappings live in the SEPARATE
        # data/13f_holdings.db file, not in etp_tracker.db. The DROP IF EXISTS
        # statements below are no-ops here but kept defensively.
        drop_tables = [
            "holdings", "institutions", "cusip_mappings",  # 13F (separate DB, no-op here)
            "analysis_results",   # empty, not wired in yet
            "pipeline_runs",      # local SEC pipeline tracking
            "screener_uploads",   # local upload audit log
        ]
        for table in drop_tables:
            conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        # NO row trimming. Either the table is dropped entirely or it's kept
        # in full. Half-trimmed tables created the "missing data on Render" bugs.
        conn.execute("VACUUM")
        conn.close()
        raw_mb = render_db.stat().st_size / 1e6
        print(f"{raw_mb:.0f} MB (was {db_path.stat().st_size / 1e6:.0f} MB)")

        print("  Compressing...", end=" ", flush=True)
        with open(render_db, "rb") as f_in:
            with gzip.open(gz_path, "wb", compresslevel=9) as f_out:
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
            raise RuntimeError(
                f"Render DB upload failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Render DB upload error: {e}") from e
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
    parser.add_argument("--skip-sec", action="store_true",
                        help="Run everything EXCEPT SEC scrape (SEC runs 4x/day via dedicated timer)")
    parser.add_argument("--reports-only", action="store_true",
                        help="Market sync + upload + email reports. Skips SEC + notes + classification.")
    args = parser.parse_args()

    # If no flags, run everything
    run_all = not (args.sec or args.notes or args.market or args.upload or args.reports_only)
    skip_sec = args.skip_sec or args.reports_only
    reports_only = args.reports_only

    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== REX Full Sync ({today}) ===")

    # === Preflight: disk free check ===
    # 2026-04-14: cache/web silently grew to 18 GB, filled the 38 GB VPS
    # disk, and crashed market sync mid-INSERT with "database or disk is
    # full" — rolling back 7k rows and aborting the whole pipeline. Abort
    # NOW if disk is tight, before any writes. Cheap and saves an incident.
    try:
        import shutil as _shutil
        _total, _used, _free = _shutil.disk_usage(str(PROJECT_ROOT))
        _free_gb = _free / (1024**3)
        _used_pct = 100.0 * _used / _total
        print(f"Disk: {_free_gb:.1f} GB free ({_used_pct:.0f}% used)")
        if _free_gb < 2.0:
            msg = (
                f"DISK CRITICAL: only {_free_gb:.1f} GB free ({_used_pct:.0f}% used). "
                f"Aborting before any writes. Clear ~/rexfinhub/cache/web and retry."
            )
            print(msg)
            try:
                from etp_tracker.email_alerts import send_critical_alert
                send_critical_alert(
                    "Daily Pipeline Aborted: Disk Critical",
                    f"<strong>{msg}</strong><br><br>Run: <code>rm -rf ~/rexfinhub/cache/web/*</code> on VPS.",
                )
            except Exception:
                pass
            sys.exit(2)
    except Exception as _e:
        print(f"  Disk check failed (non-fatal): {_e}")

    changed_trusts = None
    critical_ok = True  # Tracks whether critical steps (SEC, DB sync, market) succeeded
    errors = []  # Collects error descriptions for final summary

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
        # SKIPPED when --skip-sec or --reports-only (SEC runs 4x/day via dedicated timer)
        if skip_sec:
            print("\n[1-2/10] SEC Pipeline + Structured Notes: SKIPPED (--skip-sec)")
            print("         SEC scrape runs independently via rexfinhub-sec-scrape.timer (8am/12pm/4pm/8pm)")
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            print("\n[1-2/10] SEC Pipeline + Structured Notes (parallel)...")

            sec_result = [None]  # mutable container for thread result
            notes_ok = [False]

            def _run_sec():
                nonlocal critical_ok
                try:
                    sec_result[0] = run_sec_pipeline()
                except Exception as e:
                    print(f"  CRITICAL: SEC pipeline failed: {e}")
                    critical_ok = False
                    errors.append(f"SEC pipeline: {e}")

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
        if skip_sec:
            print("\n[3/8] Filings DB sync: SKIPPED (no new SEC results to sync)")
        else:
            print("\n[3/8] Syncing filings to DB...")
            try:
                run_db_sync(changed_trusts)
            except Exception as e:
                print(f"  CRITICAL: DB sync failed: {e}")
                critical_ok = False
                errors.append(f"DB sync: {e}")

        # === Archive C: cache to D: ===
        print("\n[4/8] Archiving cache C: -> D:...")
        try:
            archive_cache_to_d()
        except Exception as e:
            print(f"  Archive failed: {e}")

        # === Bloomberg file freshness check + force-archive if missing ===
        print("\n[5/12] Checking Bloomberg file freshness + archiving today's snapshot...")
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

            # Force SharePoint pull + archive if today's history snapshot is missing.
            # Required for the L&I recommender backtest — snapshots are the time-series
            # ground truth. Independent of the market-sync skip below.
            today_str = datetime.now().strftime("%Y-%m-%d")
            history_file = _bbg.parent / "history" / f"bloomberg_daily_file_{today_str}.xlsm"
            if not history_file.exists():
                print(f"  No archive for {today_str} yet — pulling from SharePoint...")
                try:
                    from webapp.services.graph_files import download_bloomberg_from_sharepoint
                    pulled = download_bloomberg_from_sharepoint()
                    if pulled and history_file.exists():
                        print(f"  Archived: {history_file.name}")
                    else:
                        print(f"  WARNING: archive still missing after pull (Graph API issue?)")
                        errors.append("Bloomberg archive missing after forced pull")
                except Exception as pull_e:
                    print(f"  WARNING: forced pull failed: {pull_e}")
                    errors.append(f"Bloomberg forced pull: {pull_e}")
            else:
                print(f"  Archive for {today_str} already present")
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
            print(f"  CRITICAL: Market sync failed: {e}")
            critical_ok = False
            errors.append(f"Market sync: {e}")

        # === Archive screener snapshot ===
        print("\n[6/10] Archiving screener snapshot...")
        try:
            from scripts.archive_screener import archive_daily
            archive_daily()
        except Exception as e:
            print(f"  Screener archive failed (non-fatal): {e}")

        # === Classification ===
        if reports_only:
            print("\n[7/10] Classification: SKIPPED (--reports-only)")
        else:
            print("\n[7/10] Classifying new funds...")
            try:
                run_classification()
            except Exception as e:
                print(f"  Classification failed: {e}")

        # === Total Returns scrape ===
        if reports_only:
            print("\n[8/12] Total returns scrape: SKIPPED (--reports-only)")
        else:
            print("\n[8/12] Scraping total returns data...")
            try:
                scrape_total_returns()
            except Exception as e:
                print(f"  Total returns scrape failed: {e}")

        # === Prebake reports ===
        # All data is now fresh (market sync + classification + total returns).
        # Bake the 9 HTML reports from the current VPS DB state and push them
        # to Render's static store. Done BEFORE the DB upload so the reports
        # and the live data arrive on Render in a consistent snapshot, and
        # so preview = truth: what you preview on /admin/reports/preview is
        # literally the HTML that would ship in the email.
        print("\n[8.5/12] Pre-baking reports + uploading to Render...")
        try:
            import subprocess as _subp
            _res = _subp.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "prebake_reports.py")],
                cwd=str(PROJECT_ROOT),
                timeout=600,
                capture_output=True,
                text=True,
            )
            if _res.returncode == 0:
                # Tail the last few INFO lines so the daily log shows what baked
                out_lines = [l for l in _res.stdout.splitlines() if "INFO" in l and ("baked" in l.lower() or "summary" in l.lower() or "uploaded" in l.lower())]
                for line in out_lines[-12:]:
                    print(f"  {line}")
            else:
                print(f"  Prebake failed (non-fatal): exit={_res.returncode}")
                print(f"  stderr tail: {_res.stderr[-400:]}")
                errors.append("Prebake failed")
        except Exception as e:
            print(f"  Prebake failed (non-fatal): {e}")
            errors.append(f"Prebake: {e}")

        # === Upload phase ===
        print("\n[9/12] Compacting DB...")
        try:
            compact_db()
        except Exception as e:
            print(f"  Compact failed: {e}")

        print("\n[9/10] Uploading screener cache to Render...")
        try:
            upload_screener_cache_to_render()
        except Exception as e:
            errors.append(f"Screener cache upload: {e}")
            print(f"  FAILED: {e}")

        print("\n[9.5/12] Uploading analysis parquets to Render (/strategy/* pages)...")
        try:
            upload_parquets_to_render()
        except Exception as e:
            print(f"  Parquet upload failed (non-fatal): {e}")

        print("\n[10/12] Uploading DB to Render...")
        try:
            upload_db_to_render()
        except Exception as e:
            errors.append(f"Render DB upload: {e}")
            print(f"  FAILED: {e}")

        print("\n[11/12] Uploading structured notes DB to Render...")
        try:
            notes_db = PROJECT_ROOT / "data" / "structured_notes.db"
            if notes_db.exists():
                import gzip as _gz
                import requests as _req
                _gz_path = str(notes_db) + ".upload.gz"
                with open(notes_db, "rb") as _fin:
                    with _gz.open(_gz_path, "wb", compresslevel=9) as _fout:
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

        # === Wait until send window to send emails ===
        # 2026-04-30: changed target from 17:30 -> 19:30 ET to match the new
        # gate-open/daily-timer cadence (gate opens 19:00, daily-timer fires
        # 19:30). Pre-fix: an early-AM invocation (e.g., 04:11 reconciler) would
        # wait until 17:30 and try to send before the gate was open, getting
        # blocked silently while the 19:30 daily timer's separate run_daily
        # would correctly fire after gate-open. Two-path behavior was masking
        # the bug.
        print("\n[12/12] Sending email reports...")
        if not critical_ok:
            print(f"  SKIPPED: critical step(s) failed — not sending emails with stale/missing data")
            print(f"  Errors: {'; '.join(errors)}")
            # Send critical alert
            try:
                from etp_tracker.email_alerts import send_critical_alert
                send_critical_alert(
                    "Daily Pipeline Critical Failure",
                    f"The daily pipeline failed on critical steps. Emails were NOT sent.<br><br>"
                    f"<strong>Errors:</strong> {'; '.join(errors)}<br>"
                    f"<strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
            except Exception as _alert_err:
                print(f"  Alert send also failed: {_alert_err}")
        else:
            try:
                import subprocess
                now_dt = datetime.now()
                target = now_dt.replace(hour=19, minute=30, second=0, microsecond=0)
                if now_dt < target:
                    wait_secs = (target - now_dt).total_seconds()
                    print(f"  Pipeline done. Waiting until 7:30 PM to send ({wait_secs / 60:.0f} min)...")
                    time.sleep(wait_secs)
                day_of_week = datetime.now().strftime("%A")
                if day_of_week in ("Saturday", "Sunday"):
                    print(f"  {day_of_week} -- skipping email reports")
                else:
                    # 2026-04-28 wiring: this used to shell out to send_email.py
                    # send daily/weekly directly. Now routed through send_all.py
                    # with --use-decision so the autonomous send-day loop works:
                    #   1. preflight at 18:30 writes data/.preflight_token + posts summary
                    #   2. Ryu clicks GO on /admin/reports/dashboard -> writes decision file
                    #   3. this daily timer at 19:30 calls send_all --use-decision --send
                    #   4. send_all checks token+GO, then atomically opens gate, sends
                    #      all 7 reports (or just daily/weekly bundle on weekday/Monday),
                    #      and locks gate via try/finally.
                    # If decision is HOLD or token mismatch, send_all aborts cleanly
                    # without firing — pipeline still completes (sync + upload happened).
                    if day_of_week == "Monday":
                        bundle = "all"   # daily + weekly + li + income + flow + autocall + stock_recs
                        label = "all bundles (Monday: daily + weekly + autocall + stock_recs)"
                    else:
                        bundle = "daily"
                        label = "daily only"
                    print(f"  Sending via send_all.py --use-decision: {label}...")
                    result = subprocess.run(
                        [sys.executable, str(PROJECT_ROOT / "scripts" / "send_all.py"),
                         "--bundle", bundle, "--use-decision", "--send"],
                        cwd=str(PROJECT_ROOT), timeout=900,
                    )
                    if result.returncode == 3:
                        # Decision missing / token mismatch — common on send-days
                        # where Ryu hasn't clicked GO yet. Not a failure.
                        print("  send_all: standing down (no decision or token mismatch)")
                    elif result.returncode != 0:
                        errors.append(f"send_all.py exited {result.returncode}")
            except Exception as e:
                print(f"  Email send failed (non-fatal): {e}")
                errors.append(f"Email send error: {e}")

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
            try:
                upload_screener_cache_to_render()
            except Exception as e:
                errors.append(f"Screener cache upload: {e}")
                print(f"  FAILED: {e}")
            print("\n[2.5] Uploading analysis parquets...")
            try:
                upload_parquets_to_render()
            except Exception as e:
                print(f"  Parquet upload failed (non-fatal): {e}")
            print("\n[3] Uploading DB...")
            try:
                upload_db_to_render()
            except Exception as e:
                errors.append(f"Render DB upload: {e}")
                print(f"  FAILED: {e}")

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.0f}s ({elapsed / 60:.1f}m) ===")
    if errors:
        print(f"  Errors ({len(errors)}): {'; '.join(errors)}")
    if not critical_ok:
        print("  EXIT: 1 (critical failure)")
        sys.exit(1)
    elif errors:
        print("  EXIT: 2 (partial success — non-fatal errors)")
        sys.exit(2)
    else:
        print("  EXIT: 0 (success)")


if __name__ == "__main__":
    main()
