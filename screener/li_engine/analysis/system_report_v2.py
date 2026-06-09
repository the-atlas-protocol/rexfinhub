"""T-REX Stock Recommendation System Report — weekly health check.

Same visual family as the stock-rec report. Tracks how the v1.0.1 scoring
system itself is performing — universe stats, score distribution, top movers,
sector rotation, and the recommendation outcome scorecard.

Output:  reports/li_system_report_YYYY-MM-DD.html
Run:     python -m screener.li_engine.analysis.system_report_v2
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "reports"

# Color tokens — same palette as stock-rec
NAVY = "#1a1a2e"; BLUE = "#0984e3"; GREEN = "#27ae60"; ORANGE = "#f39c12"
RED = "#e74c3c"; GRAY = "#7f8c8d"; LIGHT = "#f4f5f6"; BORDER = "#ecf0f1"
PURPLE = "#8e44ad"


def _q(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _df(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def universe_kpis():
    r = _q("SELECT COUNT(*),AVG(final_score),COUNT(DISTINCT run_date),MAX(run_date) FROM li_engine_daily")
    n, avg, dates, latest = (r[0] if r else (0, 0, 0, None))
    high = _q("SELECT COUNT(*) FROM li_engine_daily WHERE run_date=? AND final_score>=75",
              (latest,))[0][0] if latest else 0
    pass_gate = _q("SELECT COUNT(*) FROM li_engine_daily WHERE run_date=? AND json_extract(signal_values_json,'$.passes_gate')=1",
                   (latest,))[0][0] if latest else 0
    etp_rows = _q("SELECT COUNT(*) FROM li_etp_daily WHERE run_date=?", (latest,))[0][0] if latest else 0
    return {"n": n, "avg": avg or 0, "dates": dates, "latest": latest,
            "high": high, "pass_gate": pass_gate, "etp_rows": etp_rows}


def score_distribution():
    """Counts per tier band."""
    r = _q("SELECT MAX(run_date) FROM li_engine_daily"); latest = r[0][0] if r else None
    if not latest: return {}
    rows = _q("""SELECT
      SUM(CASE WHEN final_score>=90 THEN 1 ELSE 0 END),
      SUM(CASE WHEN final_score>=75 AND final_score<90 THEN 1 ELSE 0 END),
      SUM(CASE WHEN final_score>=50 AND final_score<75 THEN 1 ELSE 0 END),
      SUM(CASE WHEN final_score<50 THEN 1 ELSE 0 END)
      FROM li_engine_daily WHERE run_date=?""", (latest,))[0]
    return {"LAUNCH(≥90)": rows[0], "FILE(75-90)": rows[1], "WATCH(50-75)": rows[2], "low(<50)": rows[3]}


def top_by_score(n=15):
    r = _q("SELECT MAX(run_date) FROM li_engine_daily"); latest = r[0][0] if r else None
    if not latest: return []
    return _q("SELECT ticker, final_score, signal_values_json, pillar_scores_json "
              "FROM li_engine_daily WHERE run_date=? ORDER BY final_score DESC LIMIT ?",
              (latest, n))


def sector_aggregates():
    df = _df("SELECT * FROM li_sector_daily WHERE run_date=(SELECT MAX(run_date) FROM li_sector_daily) "
             "ORDER BY total_mentions DESC")
    return df


def etp_universe_status():
    r = _q("SELECT MAX(run_date) FROM li_etp_daily"); latest = r[0][0] if r else None
    if not latest: return {}
    rows = _q("SELECT market_status, COUNT(*), SUM(aum), SUM(CASE WHEN is_rex=1 THEN 1 ELSE 0 END) "
              "FROM li_etp_daily WHERE run_date=? GROUP BY market_status ORDER BY 2 DESC", (latest,))
    return rows


def recommendation_scorecard():
    df = _df("SELECT confidence_tier tier, COALESCE(outcome_status,'(open)') status, COUNT(*) n "
             "FROM recommendation_history GROUP BY 1,2")
    return df


# ---- HTML helpers ----

def _kpi(label, value, color=NAVY):
    return f"""
<td style="padding:10px 12px;background:white;border:1px solid {BORDER};border-radius:6px;vertical-align:top;">
  <div style="font-size:22px;font-weight:700;color:{color};">{value}</div>
  <div style="font-size:11px;color:{GRAY};margin-top:2px;">{escape(label)}</div>
</td>"""


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    kpis = universe_kpis()
    dist = score_distribution()
    top = top_by_score(15)
    sectors = sector_aggregates()
    etp_rows = etp_universe_status()
    scorecard = recommendation_scorecard()

    today = date.today().isoformat()
    today_pretty = datetime.now().strftime("%B %d, %Y")

    # Top board
    top_rows_html = ""
    for tk, sc, sig_j, pil_j in top:
        try:
            sig = json.loads(sig_j or "{}"); pil = json.loads(pil_j or "{}")
        except Exception:
            sig, pil = {}, {}
        theme = sig.get("theme_tier") or "—"
        sector = (sig.get("sector") or "—")[:18]
        mcap_raw = sig.get("mcap_num")
        mc = f"${mcap_raw/1000:.1f}B" if (mcap_raw and mcap_raw >= 1000) else (f"${mcap_raw:.0f}M" if mcap_raw else "—")
        ment = int(sig.get("mentions_24h") or 0)
        gate_str = "PASS" if sig.get("passes_gate") == 1 else "gated"
        gate_col = GREEN if sig.get("passes_gate") == 1 else GRAY
        drivers = sorted([(k.replace("_score", ""), v) for k, v in pil.items() if isinstance(v, (int, float)) and v > 0], key=lambda x: -x[1])[:3]
        drv_str = " · ".join(f"{k} {v:.0f}" for k, v in drivers)
        top_rows_html += f"""
<tr>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-family:'Courier New',monospace;font-weight:700;font-size:12px;color:{BLUE};">{escape(tk)}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;color:{GRAY};">{escape(sector)}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;">{mc}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:center;">{escape(theme) if theme!='None' else '—'}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;color:{GRAY};">{ment}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:center;color:{gate_col};font-weight:700;">{gate_str}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;color:#566573;">{escape(drv_str)}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:12.5px;text-align:right;font-weight:700;color:{NAVY};">{sc:.1f}</td>
</tr>"""

    # Sector rotation rows
    sector_rows_html = ""
    if not sectors.empty:
        for _, r in sectors.iterrows():
            sector_rows_html += f"""
<tr>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11.5px;color:{NAVY};font-weight:600;">{escape(str(r['sector']) or '—')}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;color:{GRAY};">{int(r['n_stocks'])}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;color:{GRAY};">{int(r['total_mentions'])}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;">{r['avg_score']:.1f}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-family:'Courier New',monospace;font-size:11px;color:{BLUE};font-weight:700;">{escape(str(r['top_ticker']) or '—')}</td>
<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;font-weight:700;color:{NAVY};">{r['top_score']:.1f}</td>
</tr>"""

    # Score distribution bar
    dist_html = ""
    total_dist = sum(dist.values()) or 1
    band_colors = {"LAUNCH(≥90)": GREEN, "FILE(75-90)": BLUE, "WATCH(50-75)": ORANGE, "low(<50)": GRAY}
    for label, count in dist.items():
        pct = (count / total_dist * 100)
        c = band_colors.get(label, GRAY)
        dist_html += f"""
<tr>
<td style="padding:6px 10px;font-size:12px;font-weight:600;color:{NAVY};width:140px;">{escape(label)}</td>
<td style="padding:6px 10px;font-size:11px;color:{GRAY};text-align:right;width:60px;">{count:,}</td>
<td style="padding:6px 10px;">
  <div style="background:{BORDER};border-radius:4px;height:14px;overflow:hidden;">
    <div style="background:{c};width:{pct:.1f}%;height:100%;"></div>
  </div>
</td>
<td style="padding:6px 10px;font-size:11px;text-align:right;color:{GRAY};width:50px;">{pct:.1f}%</td>
</tr>"""

    # ETP universe status
    etp_html = ""
    if etp_rows:
        for status, n, aum, n_rex in etp_rows:
            aum_str = f"${(aum or 0)/1000:.1f}B" if aum and aum >= 1000 else (f"${aum:.0f}M" if aum else "—")
            etp_html += f"""
<tr>
<td style="padding:6px 10px;font-size:12px;font-weight:600;color:{NAVY};">{escape(status or '—')}</td>
<td style="padding:6px 10px;font-size:11px;text-align:right;">{n}</td>
<td style="padding:6px 10px;font-size:11px;text-align:right;color:{GRAY};">{aum_str}</td>
<td style="padding:6px 10px;font-size:11px;text-align:right;">{n_rex or 0}</td>
</tr>"""

    # Recommendation scorecard
    if scorecard.empty:
        scorecard_html = f'<div style="font-size:13px;color:{GRAY};font-style:italic;">No recommendations logged yet — populates as the weekly file/launch report writes recs (Monday 07:00 ET cron).</div>'
    else:
        pivot = scorecard.pivot_table(index="tier", columns="status", values="n", fill_value=0)
        scorecard_html = pivot.to_html(classes="sc", border=0)

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T-REX Stock Rec System Report — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="900" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:900px;">

<!-- HEADER -->
<tr><td style="background:{NAVY};padding:24px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">
    T-REX Stock Recommendation System Report | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;
              text-transform:uppercase;margin-top:6px;">
    Methodology v1.0.1 · Universe Health · Score Distribution · Sector Rotation · Outcome Scorecard
  </div>
</td></tr>

<!-- KPI BAR -->
<tr><td style="padding:15px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      {_kpi(f"stocks scored ({kpis.get('latest','—')})", f"{kpis.get('n',0):,}")}
      <td style="width:10px"></td>
      {_kpi("gate-pass (eligible)", f"{kpis.get('pass_gate',0):,}", GREEN)}
      <td style="width:10px"></td>
      {_kpi("scoring ≥75 (FILE/LAUNCH)", f"{kpis.get('high',0):,}", BLUE)}
      <td style="width:10px"></td>
      {_kpi("days of history", f"{kpis.get('dates',0)}", PURPLE)}
      <td style="width:10px"></td>
      {_kpi("L&I products tracked", f"{kpis.get('etp_rows',0):,}", ORANGE)}
    </tr>
  </table>
</td></tr>

<!-- SCORE DISTRIBUTION -->
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {BLUE};">Score Distribution</div>
  <div style="font-size:12px;color:{GRAY};margin-top:4px;font-style:italic;">How the universe sits across action tiers today.</div>
</td></tr>
<tr><td style="padding:8px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    {dist_html}
  </table>
</td></tr>

<!-- TOP BY SCORE -->
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {GREEN};">Top 15 by Score Today</div>
  <div style="font-size:12px;color:{GRAY};margin-top:4px;font-style:italic;">The cleanest leveraged-product candidates per v1.0.1.</div>
</td></tr>
<tr><td style="padding:8px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:{NAVY};">
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Ticker</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Sector</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Mkt Cap</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:center;">Theme</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Buzz</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:center;">Gate</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Top Drivers</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Score</th>
    </tr>
    {top_rows_html}
  </table>
</td></tr>

<!-- SECTOR ROTATION -->
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {PURPLE};">Sector Rotation Radar</div>
  <div style="font-size:12px;color:{GRAY};margin-top:4px;font-style:italic;">Mention shares and average score by GICS sector — track which sectors retail is rotating into. Trajectories build as days accumulate.</div>
</td></tr>
<tr><td style="padding:8px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:{NAVY};">
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Sector</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Stocks</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Mentions</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Avg Score</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Top Ticker</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Top Score</th>
    </tr>
    {sector_rows_html}
  </table>
</td></tr>

<!-- ETP UNIVERSE STATUS -->
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {ORANGE};">L&amp;I Product Universe (Population 2)</div>
  <div style="font-size:12px;color:{GRAY};margin-top:4px;font-style:italic;">Status of every leveraged/inverse product we track. Outcome truth accumulates here.</div>
</td></tr>
<tr><td style="padding:8px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:{NAVY};">
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Status</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Count</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">Total AUM</th>
      <th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;">REX</th>
    </tr>
    {etp_html}
  </table>
</td></tr>

<!-- RECOMMENDATION SCORECARD -->
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {RED};">Recommendation Outcome Scorecard</div>
  <div style="font-size:12px;color:{GRAY};margin-top:4px;font-style:italic;">Did our recommendations actually work? File/Pass × Success/Fail — the self-improvement loop.</div>
</td></tr>
<tr><td style="padding:8px 30px 18px;">
  {scorecard_html}
</td></tr>

<!-- FOOTER -->
<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    <strong style="color:{NAVY};">Methodology v1.0.1 (frozen 2026-05-28).</strong>
    The system improves on a monthly cycle informed by the outcome scorecard.
    Daily scoring 22:30 ET Mon-Fri · Grader Sunday 23:00 · Reports Monday 07:00.
    <br><br>
    <strong>Internal use only. REX Financial. Not investment advice.</strong>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""

    out = OUT_DIR / f"li_system_report_{today}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = build()
    print(f"Report: {p}")
