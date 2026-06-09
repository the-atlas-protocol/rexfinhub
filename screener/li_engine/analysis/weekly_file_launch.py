"""Weekly File / Launch report — v1.0.1.

Reads trailing-week averages from li_engine_daily (mentions averaged over the week
per the framework), joins ETP state + filings, splits into:

  * FILE list   — top whitespace candidates (gate-pass, no REX filing, ≤2 competitors)
  * LAUNCH list — underliers REX has filed on (race-timing + PEND radar)

Output:  reports/li_file_launch_weekly_YYYY-MM-DD.html
Run:     python -m screener.li_engine.analysis.weekly_file_launch
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "reports"


def trailing_week_scores(days: int = 7) -> pd.DataFrame:
    """Average each ticker's score over the last `days` days from li_engine_daily."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT ticker, run_date, final_score, signal_values_json, pillar_scores_json "
            "FROM li_engine_daily WHERE run_date >= ?", conn, params=(cutoff,))
    finally:
        conn.close()
    if df.empty:
        return df
    # weekly avg of final_score, latest signals (today's snapshot)
    avg = df.groupby("ticker")["final_score"].mean().rename("week_avg_score")
    latest_run = df["run_date"].max()
    latest = df[df.run_date == latest_run].set_index("ticker")
    out = latest.join(avg, how="left")
    # parse latest signals + pillars
    def parse(j):
        try: return json.loads(j) if j else {}
        except: return {}
    out["sig"] = out["signal_values_json"].apply(parse)
    out["pil"] = out["pillar_scores_json"].apply(parse)
    return out


def load_filings_and_competition() -> pd.DataFrame:
    """Per underlier: REX filed Y/N, competitor 485APOS in 180d, competitor PEND."""
    conn = sqlite3.connect(DB)
    try:
        # REX filings on this underlier
        rex_fil = pd.read_sql_query(
            "SELECT mmd.map_li_underlier u, COUNT(*) n_rex_filings "
            "FROM mkt_master_data mmd WHERE mmd.is_rex=1 AND mmd.primary_category='LI' "
            "AND mmd.map_li_underlier IS NOT NULL "
            "GROUP BY mmd.map_li_underlier", conn)
        # Competitor recent 485APOS
        comp485 = pd.read_sql_query(
            "SELECT mmd.map_li_underlier u, MAX(f.filing_date) last_comp_485, COUNT(*) n_comp_485_180d "
            "FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id "
            "JOIN mkt_master_data mmd ON mmd.ticker=fe.class_symbol||' US' "
            "WHERE f.form='485APOS' AND f.filing_date>=date('now','-180 day') "
            "AND mmd.is_rex=0 AND mmd.map_li_underlier IS NOT NULL "
            "GROUP BY mmd.map_li_underlier", conn)
        # PEND competitor products on this underlier
        pend = pd.read_sql_query(
            "SELECT map_li_underlier u, COUNT(*) n_pend_competitors "
            "FROM mkt_master_data WHERE market_status='PEND' AND is_rex=0 "
            "AND primary_category='LI' AND map_li_underlier IS NOT NULL "
            "GROUP BY map_li_underlier", conn)
        # Active long competitors (saturation, also pulled from current state)
        actv = pd.read_sql_query(
            "SELECT map_li_underlier u, COUNT(*) n_actv_competitors, MAX(aum) max_comp_aum "
            "FROM mkt_master_data WHERE market_status='ACTV' AND is_rex=0 "
            "AND primary_category='LI' AND map_li_underlier IS NOT NULL "
            "GROUP BY map_li_underlier", conn)
    finally:
        conn.close()
    def clean(s): return s.astype(str).str.replace(' US','',regex=False).str.replace(' EQUITY','',regex=False).str.upper().str.strip()
    for d in [rex_fil, comp485, pend, actv]:
        if not d.empty: d["u"] = clean(d["u"])
    out = pd.DataFrame({"u": pd.concat([rex_fil.get("u", pd.Series()), comp485.get("u", pd.Series()),
                                         pend.get("u", pd.Series()), actv.get("u", pd.Series())]).dropna().unique()})
    out = (out.merge(rex_fil, on="u", how="left")
              .merge(comp485, on="u", how="left")
              .merge(pend, on="u", how="left")
              .merge(actv, on="u", how="left"))
    for c in ["n_rex_filings", "n_comp_485_180d", "n_pend_competitors", "n_actv_competitors"]:
        out[c] = out[c].fillna(0).astype(int)
    out = out.set_index("u")
    return out


def build(days: int = 7) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scored = trailing_week_scores(days=days)
    if scored.empty:
        raise RuntimeError("No li_engine_daily data — has the daily scorer run?")
    comp = load_filings_and_competition()
    scored = scored.join(comp, how="left")
    for c in ["n_rex_filings", "n_comp_485_180d", "n_pend_competitors", "n_actv_competitors"]:
        scored[c] = scored[c].fillna(0).astype(int)
    scored["rex_filed"] = scored["n_rex_filings"] > 0

    # FILE list: passes_gate today, no REX filing, top by week_avg_score
    pass_today = scored["sig"].apply(lambda s: s.get("passes_gate", 0) == 1 if isinstance(s, dict) else False)
    file_df = scored[pass_today & (~scored["rex_filed"])].sort_values("week_avg_score", ascending=False).head(20)
    # LAUNCH list: REX filed, race-timing or imminent (sorted by score)
    launch_df = scored[scored["rex_filed"]].sort_values("week_avg_score", ascending=False).head(20)

    def driver(p):
        if not isinstance(p, dict): return ""
        items = sorted([(k.replace("_score", ""), v) for k, v in p.items() if isinstance(v, (int, float))], key=lambda x: -x[1])
        return " · ".join(f"{k} {v:.0f}" for k, v in items[:3] if v > 0)

    def card(row, ticker, is_launch):
        s = row.get("sig") or {}
        p = row.get("pil") or {}
        theme = s.get("theme_tier") or "—"
        sector = (s.get("sector") or "—")[:18]
        mcap_raw = s.get("mcap_num")
        mc = f"${mcap_raw/1000:.1f}B" if mcap_raw and mcap_raw >= 1000 else (f"${mcap_raw:.0f}M" if mcap_raw else "—")
        ment = int(s.get("mentions_24h") or 0)
        race = int(row.get("n_comp_485_180d") or 0) + int(row.get("n_pend_competitors") or 0)
        comp_actv = int(row.get("n_actv_competitors") or 0)
        rex_flag = "Y" if row.get("rex_filed") else "N"
        drivers = driver(p)
        bg = "#dcfce7" if is_launch else "#dbeafe"
        return f"""
<tr><td class='tk'>{ticker}</td><td>{sector}</td><td>{mc}</td>
<td><b>{row.get('week_avg_score',0):.1f}</b></td>
<td>{theme}</td><td>{ment}</td><td>REX:{rex_flag}</td>
<td>{race}</td><td>{comp_actv}</td><td class='drv'>{drivers}</td></tr>"""

    file_rows = "".join(card(r, t, False) for t, r in file_df.iterrows())
    launch_rows = "".join(card(r, t, True) for t, r in launch_df.iterrows())

    today = date.today().isoformat()
    html = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>L&I Weekly File / Launch — {today}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Arial;color:#1a1a2e;max-width:1240px;margin:0 auto;padding:24px;background:#f8fafc}}
h1{{color:#0f172a;margin:0 0 4px}} .sub{{color:#2563eb;font-weight:600}} .meta{{color:#64748b;font-size:13px;margin-bottom:20px}}
h2{{color:#0f172a;border-bottom:2px solid #e5e7eb;padding-bottom:6px;margin-top:30px}}
.lede{{background:#fff;border:1px solid #e5e7eb;padding:10px 14px;border-radius:6px;font-size:13px;color:#566573;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
th{{background:#0f172a;color:#fff;text-align:left;padding:8px 10px}}
td{{padding:7px 10px;border-bottom:1px solid #eef1f4}}
.tk{{font-weight:700}} .drv{{color:#566573;font-size:12px}}
</style></head><body>
<h1>L&amp;I Weekly File / Launch</h1>
<div class='sub'>Methodology v1.0.1 · trailing-week average of daily scores</div>
<div class='meta'>Week ending {today} · {len(file_df)} file candidates · {len(launch_df)} launch-side names</div>

<div class='lede'><b>Mentions are averaged over the week.</b> Daily scores reset; this report uses the 7-day mean so a one-day mention spike can't drive a recommendation. Read FILE = new opportunity (no REX filing yet); LAUNCH = act on existing REX filing (race timing).</div>

<h2>FILE — top whitespace candidates (no REX filing)</h2>
<table><tr><th>Ticker</th><th>Sector</th><th>Mkt cap</th><th>Wk-avg score</th><th>Theme</th><th>Mentions</th><th>REX</th><th>Race (filings/PEND)</th><th>Active competitors</th><th>Top drivers</th></tr>
{file_rows}
</table>

<h2>LAUNCH — REX has already filed (race + readiness)</h2>
<table><tr><th>Ticker</th><th>Sector</th><th>Mkt cap</th><th>Wk-avg score</th><th>Theme</th><th>Mentions</th><th>REX</th><th>Race (filings/PEND)</th><th>Active competitors</th><th>Top drivers</th></tr>
{launch_rows}
</table>

<div class='meta' style='margin-top:24px'>REX Financial — internal use only. Not investment advice.</div>
</body></html>"""
    out_path = OUT_DIR / f"li_file_launch_weekly_{today}.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Wrote %s", out_path)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = build()
    print(f"Wrote {p}")
