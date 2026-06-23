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
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def should_block_promotion(result_file: Path) -> tuple[bool, str]:
    """Pure predicate (Stage C) — should previews be HELD (not promoted to live)?

    Mirrors send_all.preflight_blocks_send. Blocks on a preflight FAIL; an
    unreadable/missing result is treated as RED (fail-closed) — we never promote a
    build we can't prove green. Extracted + unit-tested because preview promotion is
    the other half of the no-wrong-data guarantee (the send block is the first half)."""
    try:
        overall = json.loads(result_file.read_text(encoding="utf-8")).get("overall_status", "fail")
    except Exception:
        return True, "fail"
    return (overall == "fail"), overall


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


def _log_stage(label, rc):
    """Append each step outcome to data/.pipeline_stages.jsonl so the daily heartbeat
    (scripts/healthcheck.py) can detect a step that silently failed or never ran."""
    try:
        f = ROOT / "data" / ".pipeline_stages.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "stage": label, "rc": rc,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }) + "\n")
    except OSError:
        pass


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
    _log_stage(label, rc)
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
        # unpushed commits at pull time, so this fast-forwards. Stage E: a non-ff
        # failure now HARD-ABORTS the chain (handled in main) and alerts — we must NOT
        # build reports on stale code/rules after a divergence (the old behavior
        # silently continued, risking a build on the wrong commit).
        return _sub(["git", "-C", str(ROOT), "pull", "--ff-only", "origin", "main"])

    def _alert(subject, body):
        try:
            from etp_tracker.email_alerts import send_critical_alert
            send_critical_alert(subject=subject, message=body, subject_prefix="[CHAIN]")
        except Exception as e:
            print(f"  (alert failed: {e})")

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
    # Manifest of baked reports. send_all reuses a baked report ONLY when this
    # manifest's data_date matches the date the send expects — the freshness guard.
    BAKED_MANIFEST = ".baked.json"

    def _refresh_data_date() -> str:
        """The date this refresh is FOR: REXFIN_ASOF_DATE if set, else today.
        Mirrors data_engine._resolve_as_of so the baked stamp equals the date the
        send path will check against (send_all uses the same resolution)."""
        raw = os.environ.get("REXFIN_ASOF_DATE", "").strip()
        if raw:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
            except ValueError:
                pass
        return date.today().isoformat()

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
        data_date = _refresh_data_date()
        built_at = datetime.now().astimezone().isoformat(timespec="seconds")
        manifest: dict = {}
        ok, err = [], []
        try:
            for key, (builder, _list, _crit) in REPORTS.items():
                try:
                    subj, html = builder(db)
                    (out / f"{key}.html").write_text(html, encoding="utf-8")
                    (out / f"{key}_{today}.html").write_text(html, encoding="utf-8")
                    # Record the subject + baked filename so the send can reuse it
                    # verbatim without rebuilding. Only entries written here are reusable.
                    manifest[key] = {
                        "subject": subj,
                        "file": f"{key}.html",
                        "built_at": built_at,
                        "data_date": data_date,
                    }
                    ok.append(key)
                    print(f"  OK   {key:16s} {len(html):>9,d} chars")
                except Exception as e:
                    err.append(key)
                    print(f"  ERR  {key:16s} {type(e).__name__}: {str(e)[:120]}")
        finally:
            db.close()
        # Write the manifest only after the loop. A report that ERRORed has no entry,
        # so the send can never reuse a half-built/missing bake — it falls back.
        (out / BAKED_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  built {len(ok)}/{len(REPORTS)} into staging (data_date={data_date}); "
              f"errors: {err or 'none'}")
        return 1 if err else 0

    def preflight():
        # Point preflight at the STAGING build so it gates what we're about to promote.
        env = dict(os.environ, REX_PREVIEW_DIR=str(STAGING_PREVIEWS))
        return subprocess.run([py, str(ROOT / "scripts" / "preflight_check.py")],
                              cwd=str(ROOT), env=env).returncode

    def promote():
        """Promote staging -> live only on a green preflight; otherwise hard-block."""
        import shutil
        # Read the structured result preflight just wrote (fail-closed via the pure
        # predicate — an unreadable result is treated as RED).
        result_file = ROOT / "data" / ".preflight_result.json"
        blocked, overall = should_block_promotion(result_file)
        if blocked:
            RED_MARKER.write_text(date.today().isoformat(), encoding="utf-8")
            print(f"  PREFLIGHT {overall.upper()} — previews NOT promoted; send is hard-blocked. "
                  "Last-good previews left intact.")
            return 1
        # green (or warn): promote
        LIVE_PREVIEWS.mkdir(parents=True, exist_ok=True)
        for f in STAGING_PREVIEWS.glob("*.html"):
            shutil.copy2(f, LIVE_PREVIEWS / f.name)
        # Promote the baked manifest too, but only after a green preflight — so the
        # live manifest send_all reads describes ONLY proven-green reports. If staging
        # has no manifest (older build), drop any stale live one so send falls back.
        _stage_manifest = STAGING_PREVIEWS / BAKED_MANIFEST
        _live_manifest = LIVE_PREVIEWS / BAKED_MANIFEST
        if _stage_manifest.exists():
            shutil.copy2(_stage_manifest, _live_manifest)
        elif _live_manifest.exists():
            _live_manifest.unlink()
        if RED_MARKER.exists():
            RED_MARKER.unlink()
        print(f"  PREFLIGHT {overall.upper()} — promoted "
              f"{len(list(STAGING_PREVIEWS.glob('*.html')))} previews to outputs/previews/.")
        return 0

    # Git self-heal (Stage E): pull FIRST and hard-abort on a non-ff divergence —
    # building reports on stale code/rules is worse than not building. Alert Ryu so
    # the divergence gets reconciled, then stand down (nothing partial ships).
    if not args.skip_pull:
        rc = _step("git_pull", git_pull)
        if rc != 0:
            _alert("REX chain stood down — git divergence",
                   "run_chain: `git pull --ff-only origin main` failed (non-ff divergence or "
                   "network). The chain ABORTED rather than build on stale code. Reconcile the "
                   "VPS working tree (git status / git log) and re-run.")
            print("\n=== run_chain ABORTED at git_pull (non-ff divergence) — see alert ===")
            return 1

    def _unresolved_items():
        """The ONLY thing that should reach Ryu (Stage D): facts the self-healing
        cascade could NOT resolve. Sourced from the issuer review queue (rows with no
        suggested brand) + today's cascade journal queued entries. Logged-but-fixed
        items never appear here."""
        items = []
        # issuer review queue — truly unresolved = blank suggested_brand
        q = ROOT / "docs" / "issuer_review_queue.csv"
        if q.exists():
            try:
                import csv
                with q.open(encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        if not (row.get("suggested_brand") or "").strip():
                            items.append(f"brand? {row.get('ticker','?')} — {row.get('fund_name','')}")
            except Exception:
                pass
        # cascade journal — entries that fell through to the human queue
        try:
            from market.resolve import _journal_path
            jp = _journal_path()
            if jp.exists():
                for line in jp.read_text(encoding="utf-8").splitlines():
                    try:
                        rec = json.loads(line)
                        if rec.get("queued"):
                            items.append(f"{rec.get('kind','?')}? {rec.get('ticker','?')} — {rec.get('fund_name','')}")
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        # de-dup, cap
        seen, out = set(), []
        for it in items:
            if it not in seen:
                seen.add(it); out.append(it)
        return out[:50]

    def notify():
        """Stage D — at most ONE message per refresh. Green: 'reports ready' (plus a
        'needs your call' section only if the cascade left something unresolved). Red:
        a single 'chain held' note. Everything the cascade auto-fixed is logged, not
        emailed. Best-effort; never fails the chain."""
        # Explain what changed since last run (launches/closures). A REX launch moves a
        # tracked number and trips the gate red — this is the WHY that accompanies it.
        delta_text = ""
        try:
            from scripts.analyze_daily_delta import analyze, format_delta
            delta_text = format_delta(analyze())
        except Exception:  # noqa: BLE001 — best-effort; never fail the chain on this
            delta_text = ""

        if RED_MARKER.exists():
            body = ("run_chain built reports but preflight is RED, so previews were NOT promoted and "
                    "send is hard-blocked. See data/.preflight_result.json for the failing check.")
            if delta_text:
                body += "\n\n" + delta_text
            _alert("REX reports HELD — preflight red", body)
            return 0
        unresolved = _unresolved_items()
        body = ("Today's REX reports are built and preflight is green. Review the previews, then "
                "run the send step when ready.")
        if delta_text:
            body += "\n\n" + delta_text
        if unresolved:
            body += ("\n\nNEEDS YOUR CALL — the self-healing cascade could not resolve these "
                     f"({len(unresolved)}); everything else was auto-fixed and logged:\n  - "
                     + "\n  - ".join(unresolved))
        subject = ("REX reports ready for review"
                   + (f" — {len(unresolved)} item(s) need your call" if unresolved else ""))
        _alert(subject, body)
        return 0

    steps = [
        ("bloomberg_pull_sync", bloomberg_sync),
        ("post_steps_classify_ai_enrich", post_steps),
        ("build_all_reports", build_reports),
        ("preflight", preflight),
        ("promote_or_block", promote),
        ("notify", lambda: notify()),
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
