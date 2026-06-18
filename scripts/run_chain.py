"""ONE on-demand pipeline — the engine behind the `/refreshdata` command.

Fired by Ryu (via the /refreshdata skill) AFTER he updates the Bloomberg daily file,
instead of a fixed clock — data lands at variable times, so the command is the trigger.
Runs on the VPS (the single source of truth). Idempotent; safe to re-run.

Steps (a non-zero step is logged but does NOT abort — partial success still previews):
  1. git pull --ff-only origin main      — code current (also stops git divergence:
                                            we pull BEFORE the post-steps commit/push)
  2. Bloomberg pull from SharePoint + sync_market_data  (+ MicroSectors override)
  3. apply_bloomberg_post_steps.py        — classify (rules + AI middlemen:
                                            ai_classify_unmapped, ai_underlier_intel,
                                            ai_source_ipo) -> fund_master -> brands ->
                                            effective dates -> reconciler -> snapshot ->
                                            commit+push rule deltas
  4. build ALL 10 reports (send_all.REPORTS) -> outputs/previews/<key>.html
  5. preflight_check.py                    — dry-run KPI/tie-out audit

Build only (skip sec/send — those are separate): the SEC scrape stays on its own 4x/day
timer; sending is the explicit `/refreshdata send` step, gated by .send_enabled.

    python scripts/run_chain.py            # full chain, build previews, no send
    python scripts/run_chain.py --skip-pull  # don't touch git (e.g. mid-reconcile)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env():
    """Load config/.env into os.environ (so AI steps get ANTHROPIC_API_KEY even when
    invoked outside the systemd unit). Tolerates non KEY=VALUE lines."""
    envf = ROOT / "config" / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _step(label, fn):
    print(f"\n=== {label} START ===", flush=True)
    t0 = time.time()
    rc = 0
    try:
        rc = fn() or 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        rc = 1
    print(f"=== {label} END (rc={rc}, {time.time() - t0:.1f}s) ===", flush=True)
    return rc


def _sub(cmd):
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pull", action="store_true")
    args = ap.parse_args()
    _load_env()
    py = sys.executable
    worst = 0

    def git_pull():
        # --ff-only: never create a messy merge. After a clean reconcile the VPS has no
        # unpushed commits at pull time, so this fast-forwards. A non-ff failure is
        # logged (divergence to reconcile) but doesn't block the run.
        return _sub(["git", "-C", str(ROOT), "pull", "--ff-only", "origin", "main"])

    def bloomberg_sync():
        from webapp.services.graph_files import download_bloomberg_from_sharepoint
        from webapp.database import init_db, SessionLocal
        from webapp.services.market_sync import sync_market_data
        download_bloomberg_from_sharepoint()
        init_db()
        db = SessionLocal()
        try:
            res = sync_market_data(db)
            print(f"  sync_market_data: {res}")
        finally:
            db.close()
        return 0

    def post_steps():
        return _sub([py, str(ROOT / "scripts" / "apply_bloomberg_post_steps.py")])

    # Stage C: build into a STAGING dir, gate it with preflight, and promote to the
    # live previews dir ONLY when preflight is green. A red build never overwrites the
    # last-good previews and is hard-blocked from send (send_all reads the result file).
    LIVE_PREVIEWS = ROOT / "outputs" / "previews"
    STAGING_PREVIEWS = ROOT / "outputs" / "previews_staging"
    RED_MARKER = ROOT / "data" / ".preflight_red"

    def build_reports():
        from webapp.database import init_db, SessionLocal
        from scripts.send_all import REPORTS
        init_db()
        db = SessionLocal()
        out = STAGING_PREVIEWS
        # fresh staging dir so a build error can't leave a stale file that gates green
        import shutil
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        ok, err = [], []
        try:
            for key, (builder, _list, _crit) in REPORTS.items():
                try:
                    _subj, html = builder(db)
                    (out / f"{key}.html").write_text(html, encoding="utf-8")
                    (out / f"{key}_{today}.html").write_text(html, encoding="utf-8")
                    ok.append(key)
                    print(f"  OK   {key:16s} {len(html):>9,d} chars")
                except Exception as e:
                    err.append(key)
                    print(f"  ERR  {key:16s} {type(e).__name__}: {str(e)[:120]}")
        finally:
            db.close()
        print(f"  built {len(ok)}/{len(REPORTS)} into staging; errors: {err or 'none'}")
        return 1 if err else 0

    def preflight():
        # Point preflight at the STAGING build so it gates what we're about to promote.
        env = dict(os.environ, REX_PREVIEW_DIR=str(STAGING_PREVIEWS))
        return subprocess.run([py, str(ROOT / "scripts" / "preflight_check.py")],
                              cwd=str(ROOT), env=env).returncode

    def promote():
        """Promote staging -> live only on a green preflight; otherwise hard-block."""
        import shutil
        # Read the structured result preflight just wrote.
        result_file = ROOT / "data" / ".preflight_result.json"
        overall = "fail"
        try:
            overall = json.loads(result_file.read_text(encoding="utf-8")).get("overall_status", "fail")
        except Exception as e:
            print(f"  preflight result unreadable ({e}); treating as RED")
        if overall == "fail":
            RED_MARKER.write_text(date.today().isoformat(), encoding="utf-8")
            print("  PREFLIGHT RED — previews NOT promoted; send is hard-blocked. "
                  "Last-good previews left intact.")
            return 1
        # green (or warn): promote
        LIVE_PREVIEWS.mkdir(parents=True, exist_ok=True)
        for f in STAGING_PREVIEWS.glob("*.html"):
            shutil.copy2(f, LIVE_PREVIEWS / f.name)
        if RED_MARKER.exists():
            RED_MARKER.unlink()
        print(f"  PREFLIGHT {overall.upper()} — promoted "
              f"{len(list(STAGING_PREVIEWS.glob('*.html')))} previews to outputs/previews/.")
        return 0

    steps = [] if args.skip_pull else [("git_pull", git_pull)]
    steps += [
        ("bloomberg_pull_sync", bloomberg_sync),
        ("post_steps_classify_ai_enrich", post_steps),
        ("build_all_reports", build_reports),
        ("preflight", preflight),
        ("promote_or_block", promote),
    ]
    for label, fn in steps:
        rc = _step(label, fn)
        worst = max(worst, rc)

    print(f"\n=== run_chain COMPLETE (worst rc={worst}) ===")
    if RED_MARKER.exists():
        print("Gate RED — preview not promoted, send hard-blocked. Fix the data and re-run.")
    else:
        print("Previews promoted to outputs/previews/. Gate stays closed — send is the "
              "explicit `/refreshdata send` step.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
