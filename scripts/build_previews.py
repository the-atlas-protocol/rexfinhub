"""Build all report previews locally on FRESH data — one command.

Pipeline: refresh the daily file from prod -> sync the DB -> classification
enrichment -> build every report in send_all.REPORTS -> save HTML to
reports/preview_<key>.html. Always-fresh by construction, so the local preview
workflow can never silently run on a stale workbook (the worktree stale-Excel
problem, 2026-06-16).

    python scripts/build_previews.py              # refresh from prod, then build
    python scripts/build_previews.py --no-refresh # build on the local file as-is

Reports that depend on the screener pipeline (stock_recs, blue_ocean) need the
data/analysis/*.parquet artifacts; pull them from prod with
`scp jarvis@46.224.126.196:/home/jarvis/rexfinhub/data/analysis/*.parquet data/analysis/`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _run(label: str, cmd: list[str]) -> None:
    print(f"--- {label} ---")
    rc = subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode
    if rc != 0:
        print(f"  WARN: {label} exit={rc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build all report previews on fresh data")
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip pulling the current daily file from prod")
    args = ap.parse_args()

    if not args.no_refresh:
        from scripts.refresh_bloomberg import refresh
        if refresh() != 0:
            print("Refresh failed — building on the existing local file.", file=sys.stderr)

    # Sync DB from the (now fresh) daily file
    from webapp.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()
    print("--- sync_market_data ---")
    from webapp.services.market_sync import sync_market_data
    res = sync_market_data(db)
    print(f"  {res}")
    db.close()

    # Classification enrichment (curated taxonomy + issuer brands)
    py = sys.executable
    _run("apply_fund_master", [py, str(PROJECT_ROOT / "scripts" / "apply_fund_master.py")])
    _run("derive_issuer_brands", [py, str(PROJECT_ROOT / "scripts" / "derive_issuer_brands.py")])
    _run("apply_issuer_brands", [py, str(PROJECT_ROOT / "scripts" / "apply_issuer_brands.py")])

    # Build + save every report
    db = SessionLocal()
    from scripts.send_all import REPORTS
    out = PROJECT_ROOT / "reports"
    out.mkdir(exist_ok=True)
    today = date.today().isoformat()
    ok, err = [], []
    print("--- build reports ---")
    for key, (builder, _list, _crit) in REPORTS.items():
        try:
            subject, html = builder(db)
            p = out / f"preview_{key}_{today}.html"
            p.write_text(html, encoding="utf-8")
            ok.append(key)
            print(f"  OK   {key:16s} {len(html):>9,d} chars -> {p.name}")
        except Exception as e:
            err.append(key)
            print(f"  ERR  {key:16s} {type(e).__name__}: {str(e)[:100]}")
    db.close()

    print(f"\nBuilt {len(ok)}/{len(REPORTS)} previews. Errors: {err or 'none'}")
    return 0 if not err else 1


if __name__ == "__main__":
    sys.exit(main())
