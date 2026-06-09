"""Weekly System Report — how the scoring system itself is doing.

Three sections:
  1. Score movement — top risers / fallers week-over-week
  2. Recommendation scorecard — file/pass × success/fail (decision attribution)
  3. Sector rotation — mention-share movement (early-opportunity radar)

Output:  reports/li_system_weekly_YYYY-MM-DD.html
Run:     python -m screener.li_engine.analysis.weekly_system_report
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from screener.li_engine import persistence

log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "reports"


def universe_stats(run_date: str | None = None) -> dict:
    conn = sqlite3.connect(DB)
    try:
        if run_date is None:
            r = conn.execute("SELECT MAX(run_date) FROM li_engine_daily").fetchone()
            run_date = r[0] if r and r[0] else None
        if not run_date:
            return {}
        agg = conn.execute(
            "SELECT COUNT(*) n, AVG(final_score), "
            "SUM(CASE WHEN final_score>=75 THEN 1 ELSE 0 END) high_pct "
            "FROM li_engine_daily WHERE run_date=?", (run_date,)).fetchone()
        dates_count = conn.execute(
            "SELECT COUNT(DISTINCT run_date) FROM li_engine_daily").fetchone()[0]
        return {"run_date": run_date, "n_scored": agg[0], "avg_score": agg[1],
                "n_high": agg[2], "history_days": dates_count}
    finally:
        conn.close()


def score_movers(days: int = 7, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Top risers and fallers over last `days` days from li_engine_daily."""
    df = persistence.top_movers(days=days, n=1000)
    if df.empty:
        return df, df
    df = df.sort_values("delta", ascending=False)
    return df.head(n), df.tail(n)[::-1]


def recommendation_scorecard() -> pd.DataFrame:
    """recommendation_history by tier × outcome_status."""
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT confidence_tier tier, COALESCE(outcome_status,'(open)') status, COUNT(*) n "
            "FROM recommendation_history GROUP BY 1,2", conn)
    finally:
        conn.close()
    if df.empty:
        return df
    return df.pivot_table(index="tier", columns="status", values="n", fill_value=0)


def sector_rotation(days: int = 7) -> pd.DataFrame:
    """Sector mention-share movement over the window from li_sector_daily."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT sector, run_date, total_mentions, avg_score "
            "FROM li_sector_daily WHERE run_date>=? ORDER BY run_date", conn, params=(cutoff,))
    finally:
        conn.close()
    if df.empty:
        return df
    daily_total = df.groupby("run_date")["total_mentions"].sum()
    df["share_pct"] = df.apply(
        lambda r: (r.total_mentions / daily_total[r.run_date] * 100) if daily_total[r.run_date] else 0, axis=1)
    if df["run_date"].nunique() < 2:
        latest = df[df.run_date == df.run_date.max()].copy()
        latest["delta_share"] = 0.0
        return latest.sort_values("share_pct", ascending=False)
    first = df[df.run_date == df.run_date.min()].set_index("sector")["share_pct"]
    last = df[df.run_date == df.run_date.max()].set_index("sector")["share_pct"]
    j = pd.concat([first.rename("share_start"), last.rename("share_end")], axis=1).fillna(0)
    j["delta_share"] = j["share_end"] - j["share_start"]
    return j.sort_values("delta_share", ascending=False)


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = universe_stats()
    risers, fallers = score_movers()
    scorecard = recommendation_scorecard()
    rotation = sector_rotation()
    today = date.today().isoformat()

    def mover_rows(d):
        if d.empty: return "<tr><td colspan='4'><i>Need 2+ days of history — populating from today.</i></td></tr>"
        return "".join(
            f"<tr><td class='tk'>{idx}</td><td>{r['score_start']:.1f}</td><td>{r['score_end']:.1f}</td>"
            f"<td><b>{'+' if r['delta']>=0 else ''}{r['delta']:.1f}</b></td></tr>"
            for idx, r in d.iterrows())

    if scorecard.empty:
        sc_html = "<i>recommendation_history has no rows yet — populates once the weekly file/launch report logs recs.</i>"
    else:
        sc_html = scorecard.to_html(classes="sc")

    if rotation.empty:
        rot_html = "<i>No sector aggregates yet — populates as daily runs accumulate.</i>"
    else:
        cols = ["share_start", "share_end", "delta_share"] if "share_start" in rotation.columns else ["share_pct"]
        rot_show = rotation[cols].round(2).head(11)
        rot_html = rot_show.to_html(classes="sc")

    html = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>L&I System Report — {today}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial;color:#1a1a2e;max-width:1120px;margin:0 auto;padding:24px;background:#f8fafc}}
h1{{color:#0f172a;margin:0 0 4px}} .sub{{color:#2563eb;font-weight:600}} .meta{{color:#64748b;font-size:13px;margin-bottom:16px}}
h2{{color:#0f172a;border-bottom:2px solid #e5e7eb;padding-bottom:6px;margin-top:30px}}
.grid{{display:flex;gap:20px;flex-wrap:wrap}} .col{{flex:1;min-width:340px}}
table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
th{{background:#0f172a;color:#fff;text-align:left;padding:7px 10px}}
td{{padding:6px 10px;border-bottom:1px solid #eef1f4}}
.tk{{font-weight:700}}
.kpis{{display:flex;gap:14px;margin:14px 0}}
.kpi{{flex:1;background:#fff;padding:10px 14px;border-radius:6px;border:1px solid #e5e7eb}}
.kpi .v{{font-size:22px;font-weight:700;color:#0f172a}} .kpi .l{{font-size:12px;color:#64748b}}
</style></head><body>
<h1>L&amp;I System Report</h1>
<div class='sub'>How the scoring system itself is performing · methodology v1.0.1</div>
<div class='meta'>Week ending {today}</div>

<div class='kpis'>
  <div class='kpi'><div class='v'>{stats.get('n_scored',0):,}</div><div class='l'>stocks scored ({stats.get('run_date','—')})</div></div>
  <div class='kpi'><div class='v'>{stats.get('avg_score',0):.1f}</div><div class='l'>avg score</div></div>
  <div class='kpi'><div class='v'>{stats.get('n_high',0):,}</div><div class='l'>scoring ≥75</div></div>
  <div class='kpi'><div class='v'>{stats.get('history_days',0)}</div><div class='l'>days of history</div></div>
</div>

<h2>Score movement — week over week</h2>
<div class='grid'>
<div class='col'><b>Top risers</b>
<table><tr><th>Ticker</th><th>Start</th><th>End</th><th>Δ</th></tr>{mover_rows(risers)}</table>
</div>
<div class='col'><b>Top fallers</b>
<table><tr><th>Ticker</th><th>Start</th><th>End</th><th>Δ</th></tr>{mover_rows(fallers)}</table>
</div></div>

<h2>Recommendation scorecard — file / pass × success / fail</h2>
{sc_html}

<h2>Sector rotation — mention-share movement (early-opportunity radar)</h2>
{rot_html}

<div class='meta' style='margin-top:24px'>REX Financial — internal use only.</div>
</body></html>"""
    out_path = OUT_DIR / f"li_system_weekly_{today}.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Wrote %s", out_path)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = build()
    print(f"Wrote {p}")
