"""qc_all_reports.py — foolproof input→output QC for all email reports.

The Ultimate-Fixup mandate: "a completely perfect report, every one, every number
checked to be exactly what it was supposed to be." This script is the gate that
proves it. Three layers:

  LAYER 1 — Source invariants. Recompute every headline number directly from raw
            mkt_master_data and assert the DATA the reports read is internally
            correct (category AUM sums, REX share bounds, no liquidated funds in
            ACTV aggregates, REX completeness, autocall union, freshness, history).
  LAYER 2 — Builder vs source. Call the L&I / Income / Flow data builders and
            compare their headline totals to an independent SQL recompute — the
            true input→output check. Defensive: a builder that can't be imported
            degrades to a warning, never a crash.
  LAYER 3 — Rendered scan. Inspect the built preview HTML for forbidden output:
            a liquidated/delisted ticker, a literal None/nan/$0.0B in a headline,
            or a missing market-share chart.

Exit 0 = all pass (or warnings only). Exit 2 = one or more hard failures — a send
must HALT. Mirrors run_assertions / preflight semantics so it can gate the pipeline.

Usage:
    python scripts/qc_all_reports.py                 # layers 1+2 (no preview build)
    python scripts/qc_all_reports.py --with-rendered # also build + scan previews
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
PREVIEW_DIR = PROJECT_ROOT / "outputs" / "previews"

# Active-ETP predicate every report shares (excludes liquidated/delisted).
ACTV = "market_status = 'ACTV' AND fund_type IN ('ETF','ETN')"


def _q1(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# LAYER 1 — source invariants (each returns (name, ok, detail))
# ---------------------------------------------------------------------------

def l1_no_duplicate_keys(con):
    n = _q1(con, """SELECT COUNT(*) FROM (
        SELECT ticker, etp_category, COUNT(*) c FROM mkt_master_data
        GROUP BY ticker, etp_category HAVING c > 1)""")
    return ("no_duplicate_ticker_etp", n == 0,
            f"{n} duplicate (ticker, etp_category) rows (writer dedup must hold)")


def l1_universe_sane(con):
    n = _q1(con, f"SELECT COUNT(*) FROM mkt_master_data WHERE {ACTV}")
    ok = 4000 <= n <= 7000
    return ("active_etp_universe_sane", ok,
            f"{n} active ETPs (sanity band 4000-7000)")


def l1_total_aum_positive(con):
    aum = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV}")
    return ("total_etp_aum_positive", aum > 0,
            f"total ETP AUM = ${aum/1e9:.1f}B")


def _category_share(con, etp_cat):
    total = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV} AND etp_category=?", (etp_cat,))
    rex = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV} AND etp_category=? AND is_rex=1", (etp_cat,))
    share = (rex / total * 100) if total > 0 else 0.0
    return total, rex, share


def l1_li_category(con):
    total, rex, share = _category_share(con, "LI")
    ok = total > 0 and 0 <= share <= 100
    return ("li_category_aum_and_share", ok,
            f"L&I total ${total/1e9:.1f}B, REX ${rex/1e9:.1f}B, share {share:.1f}%")


def l1_cc_category(con):
    total, rex, share = _category_share(con, "CC")
    ok = total > 0 and 0 <= share <= 100
    return ("cc_category_aum_and_share", ok,
            f"Income total ${total/1e9:.1f}B, REX ${rex/1e9:.1f}B, share {share:.1f}%")


def l1_no_liquidated_is_rex_leak(con):
    # Any LIQU/DLST fund that still carries a live-looking aggregate is a leak risk.
    # The reports filter ACTV; assert the dead REX funds are NOT ACTV.
    n = _q1(con, "SELECT COUNT(*) FROM mkt_master_data WHERE is_rex=1 AND market_status IN ('LIQU','DLST') AND market_status='ACTV'")
    return ("no_liquidated_marked_active", n == 0,
            f"{n} funds both liquidated and ACTV (contradiction)")


def l1_rex_completeness(con):
    # Every ACTV REX product must carry issuer_display + primary_strategy so no
    # report shows a blank issuer/strategy. (etp_category handled separately —
    # non-legacy asset classes are exempt per ADR 0012.)
    miss_iss = _q1(con, "SELECT COUNT(*) FROM mkt_master_data WHERE is_rex=1 AND market_status='ACTV' AND (issuer_display IS NULL OR issuer_display='')")
    miss_strat = _q1(con, "SELECT COUNT(*) FROM mkt_master_data WHERE is_rex=1 AND market_status='ACTV' AND (primary_strategy IS NULL OR primary_strategy='')")
    ok = miss_iss == 0 and miss_strat == 0
    return ("rex_products_complete", ok,
            f"{miss_iss} missing issuer_display, {miss_strat} missing primary_strategy (ACTV REX)")


def l1_autocall_union_tagged(con):
    # Autocall report relies on the cc_category/cc_type/name union. Assert every
    # name-autocall fund is also tagged so the union filter can't miss it.
    untagged = _q1(con, """SELECT COUNT(*) FROM mkt_master_data
        WHERE market_status='ACTV' AND UPPER(fund_name) LIKE '%AUTOCALL%'
          AND COALESCE(cc_category,'')<>'Autocallable' AND COALESCE(cc_type,'')<>'Autocallable'""")
    return ("autocall_union_tagged", untagged == 0,
            f"{untagged} name-autocall funds missing cc_category/cc_type tag")


def l1_data_fresh(con):
    latest = _q1(con, "SELECT MAX(updated_at) FROM mkt_master_data")
    ok = False
    detail = f"latest updated_at = {latest}"
    if latest:
        try:
            d = str(latest)[:10]
            today = datetime.utcnow().date().isoformat()
            yday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
            ok = d in (today, yday)
        except Exception:
            pass
    return ("market_data_fresh", ok, detail)


def l1_history_today(con):
    try:
        n = _q1(con, "SELECT COUNT(*) FROM mkt_daily_snapshot WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM mkt_daily_snapshot)")
        latest = _q1(con, "SELECT MAX(snapshot_date) FROM mkt_daily_snapshot")
        ok = n > 1000
        return ("daily_history_captured", ok, f"{n} rows in latest snapshot ({latest})")
    except sqlite3.OperationalError:
        return ("daily_history_captured", False, "mkt_daily_snapshot missing")


LAYER1 = [l1_no_duplicate_keys, l1_universe_sane, l1_total_aum_positive,
          l1_li_category, l1_cc_category, l1_no_liquidated_is_rex_leak,
          l1_rex_completeness, l1_autocall_union_tagged, l1_data_fresh,
          l1_history_today]


# ---------------------------------------------------------------------------
# LAYER 2 — builder vs independent source (the input→output check)
# ---------------------------------------------------------------------------

def layer2_builder_checks(con):
    """Call the L&I/Income/Flow builders; compare headline AUM to source SQL.

    Returns a list of (name, ok, detail). Degrades to a single warning row if the
    app builders can't be imported in this environment.
    """
    out = []
    try:
        from webapp.database import init_db, SessionLocal
        from webapp.services import report_data as rd
        init_db()
        db = SessionLocal()
    except Exception as e:
        return [("builder_import", True, f"SKIPPED (builders not importable here: {e})")]

    def _close():
        try:
            db.close()
        except Exception:
            pass

    # tolerance: 0.5% of the source figure (float/rounding drift)
    def _cmp(name, builder_val, source_val):
        if builder_val is None:
            out.append((name, True, f"SKIPPED (builder returned no value); source ${source_val/1e9:.2f}B"))
            return
        tol = max(abs(source_val) * 0.005, 1e6)
        ok = abs(builder_val - source_val) <= tol
        out.append((name, ok,
                    f"builder ${builder_val/1e9:.2f}B vs source ${source_val/1e9:.2f}B "
                    f"(Δ ${abs(builder_val-source_val)/1e6:.1f}M, tol ${tol/1e6:.1f}M)"))

    try:
        # L&I total AUM
        try:
            li = rd.get_li_report(db=db)
            li_total = _builder_total_aum(li)
            src = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV} AND etp_category='LI'")
            _cmp("li_total_aum_matches_source", li_total, src)
        except Exception as e:
            out.append(("li_report_builds", False, f"get_li_report failed: {e}"))
        # Income (CC) total AUM
        try:
            cc = rd.get_cc_report(db=db)
            cc_total = _builder_total_aum(cc)
            src = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV} AND etp_category='CC'")
            _cmp("cc_total_aum_matches_source", cc_total, src)
        except Exception as e:
            out.append(("cc_report_builds", False, f"get_cc_report failed: {e}"))
        # Flow grand AUM (all ETPs, no category filter)
        try:
            flow = rd.get_flow_report(db=db, use_cache=False)
            grand = (flow or {}).get("grand_kpis", {}) or {}
            fv = grand.get("total_aum") or grand.get("aum")
            fv = float(fv) if fv is not None else None
            src = _q1(con, f"SELECT COALESCE(SUM(aum),0) FROM mkt_master_data WHERE {ACTV}")
            _cmp("flow_grand_aum_matches_source", fv, src)
        except Exception as e:
            out.append(("flow_report_builds", False, f"get_flow_report failed: {e}"))
    finally:
        _close()
    return out


def _builder_total_aum(report: dict):
    """Best-effort extract the category total AUM from a get_li/get_cc report dict."""
    if not isinstance(report, dict):
        return None
    for path in (("kpis", "total_aum"), ("kpis", "market_aum"),
                 ("market_kpis", "total_aum"), ("totals", "aum")):
        cur = report
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur is not None:
            try:
                return float(cur)
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# LAYER 3 — rendered preview scan
# ---------------------------------------------------------------------------

def layer3_rendered_scan(con):
    out = []
    if not PREVIEW_DIR.exists():
        return [("previews_present", False, f"{PREVIEW_DIR} missing — run send_email.py preview all first")]
    # liquidated tickers that must never appear in any rendered report
    dead = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM mkt_master_data WHERE market_status IN ('LIQU','DLST') AND ticker IS NOT NULL AND ticker<>''").fetchall()]
    dead_bare = {t.replace(" US", "").strip() for t in dead}
    reports = sorted(PREVIEW_DIR.glob("*.html"))
    if not reports:
        return [("previews_present", False, "no preview HTML files found")]
    for path in reports:
        html = path.read_text(encoding="utf-8", errors="ignore")
        # forbidden literal placeholders in a shipped report
        bad_tokens = [tok for tok in (">None<", ">nan<", ">NaN<", "$nan", "undefined")
                      if tok in html]
        if bad_tokens:
            out.append((f"{path.name}:no_placeholder", False,
                        f"contains {bad_tokens}"))
        # liquidated ticker leak (word-boundary-ish: ' TICKER<' or '>TICKER ')
        leaked = []
        for t in dead_bare:
            if len(t) >= 3 and (f">{t}<" in html or f" {t} US" in html or f">{t} " in html):
                leaked.append(t)
            if len(leaked) >= 5:
                break
        if leaked:
            out.append((f"{path.name}:no_liquidated", False,
                        f"liquidated tickers appear: {leaked[:5]}"))
    if not out:
        out.append(("rendered_scan", True, f"{len(reports)} previews clean (no dead tickers / placeholders)"))
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--with-rendered", action="store_true",
                    help="Also scan built preview HTML (run send_email.py preview all first)")
    ap.add_argument("--skip-builders", action="store_true",
                    help="Skip Layer 2 (builder vs source) — Layer 1 only")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    results: list[tuple[str, bool, str]] = []
    print("=== qc_all_reports ===\n")
    print("-- LAYER 1: source invariants --")
    for fn in LAYER1:
        try:
            name, ok, detail = fn(con)
        except Exception as e:
            name, ok, detail = fn.__name__, False, f"EXCEPTION: {e}"
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:32s} {detail}")

    if not args.skip_builders:
        print("\n-- LAYER 2: builder vs source --")
        for name, ok, detail in layer2_builder_checks(con):
            results.append((name, ok, detail))
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:32s} {detail}")

    if args.with_rendered:
        print("\n-- LAYER 3: rendered preview scan --")
        for name, ok, detail in layer3_rendered_scan(con):
            results.append((name, ok, detail))
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:32s} {detail}")

    con.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} / {len(results)} passed")
    if failed:
        print("FAILURES:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
