raise SystemExit("RETIRED 2026-06-16 - quarantined to archive/retired-2026-06-16/, do not run. 3-gate proven 0 live refs; pending final sweep.")
"""T-REX Stock Recommendation System — combined weekly report.

Ordered by priority of attention (race timing first, then pipeline, then files,
then the full mega table, then other lanes, then system footer). One document.

Methodology v1.0.2 (no race bucket, no tier bands).

Output:  reports/trex_combined_YYYY-MM-DD.html
Run:     python -m screener.li_engine.analysis.trex_combined_v2
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
WS = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
LC = _ROOT / "data" / "analysis" / "launch_candidates.parquet"
OUT_DIR = _ROOT / "reports"

NAVY = "#1a1a2e"; BLUE = "#0984e3"; GREEN = "#27ae60"; ORANGE = "#f39c12"
RED = "#e74c3c"; GRAY = "#7f8c8d"; LIGHT = "#f4f5f6"; BORDER = "#ecf0f1"; PURPLE = "#8e44ad"


def _df(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _fmt_mcap(v):
    if v is None or pd.isna(v): return "—"
    try: v = float(v)
    except: return "—"
    if v >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _fmt_pct(v):
    if v is None or pd.isna(v): return "—"
    try: v = float(v)
    except: return "—"
    sign = "+" if v >= 0 else ""
    color = GREEN if v >= 0 else RED
    return f'<span style="color:{color};font-weight:600">{sign}{v:.0f}%</span>'


def _fmt_days(v):
    if v is None or pd.isna(v): return "—"
    try: return f"{int(v)}d"
    except: return "—"


# -------------------- DATA LOADERS --------------------

def _rex_position_per_underlier():
    """Map underlier -> 'Filed', 'PEND', 'Live', 'Not in' based on REX's products."""
    df = _df("SELECT map_li_underlier AS u, market_status FROM mkt_master_data "
             "WHERE is_rex=1 AND primary_category='LI' AND map_li_underlier IS NOT NULL")
    if df.empty: return {}
    df["u"] = df["u"].astype(str).str.replace(" US", "", regex=False).str.strip()
    pos = {}
    for u, g in df.groupby("u"):
        statuses = set(g["market_status"])
        if "ACTV" in statuses: pos[u] = ("Live", GREEN)
        elif "PEND" in statuses: pos[u] = ("PEND", ORANGE)
        else: pos[u] = ("Filed", BLUE)
    return pos


def load_imminent_launches():
    """Competitor PEND products with future inception dates within 90 days."""
    today = date.today()
    df = _df("SELECT ticker AS product_ticker, fund_name, "
             "COALESCE(issuer_nickname, issuer) AS issuer, "
             "inception_date, map_li_underlier AS underlier "
             "FROM mkt_master_data "
             "WHERE primary_category='LI' AND market_status='PEND' AND is_rex=0 "
             "AND map_li_underlier IS NOT NULL AND map_li_underlier!='' "
             "AND inception_date IS NOT NULL")
    if df.empty: return df, {}
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df = df.dropna(subset=["inception_dt"])
    cutoff = pd.Timestamp(today + timedelta(days=180))
    df = df[df["inception_dt"] >= pd.Timestamp(today - timedelta(days=30))]
    df = df[df["inception_dt"] <= cutoff]
    df["days_remaining"] = (df["inception_dt"] - pd.Timestamp(today)).dt.days
    df["underlier"] = df["underlier"].astype(str).str.replace(" US", "", regex=False).str.strip()
    return df.sort_values("days_remaining").head(20), _rex_position_per_underlier()


def load_recent_competitor_filings():
    """Competitor 485APOS in the last 90 days."""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    df = _df("SELECT f.filing_date, f.registrant, fe.series_name, fe.class_symbol, "
             "mmd.map_li_underlier AS underlier "
             "FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id "
             "LEFT JOIN mkt_master_data mmd ON mmd.ticker=fe.class_symbol||' US' "
             "WHERE f.form='485APOS' AND f.filing_date>=? "
             "AND (f.registrant NOT LIKE '%REX%' AND f.registrant NOT LIKE '%ETF Opportunities%') "
             "ORDER BY f.filing_date DESC LIMIT 40", (cutoff,))
    if df.empty: return df
    df["filing_date_dt"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["days_since"] = (pd.Timestamp(date.today()) - df["filing_date_dt"]).dt.days
    df["underlier"] = df["underlier"].fillna("").astype(str).str.replace(" US", "", regex=False).str.strip()
    return df.head(20)


def load_rex_pipeline():
    """REX products IN FLIGHT — PEND (about to launch) + ACTV launched in the last 180d."""
    cutoff = (date.today() - timedelta(days=180)).isoformat()
    df = _df("SELECT ticker AS product_ticker, fund_name, "
             "market_status, inception_date, map_li_underlier AS underlier, "
             "map_li_direction AS direction, map_li_leverage_amount AS leverage, aum "
             "FROM mkt_master_data WHERE is_rex=1 AND primary_category='LI' "
             "AND (market_status='PEND' OR (market_status='ACTV' AND inception_date>=?)) "
             "ORDER BY market_status DESC, inception_date DESC", (cutoff,))
    if df.empty: return df
    df["underlier"] = df["underlier"].fillna("").astype(str).str.replace(" US", "", regex=False).str.strip()
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    today = pd.Timestamp(date.today())
    df["days"] = (df["inception_dt"] - today).dt.days
    return df


def load_scored():
    """The scored universe from whitespace_v4 parquet."""
    df = pd.read_parquet(WS)
    df = df.reset_index().rename(columns={"index": "ticker"}) if df.index.name == "ticker" else df
    if "ticker" not in df.columns: df["ticker"] = df.index
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    return df


def load_inverse_gap():
    """Top long products with no inverse sibling on the same underlier."""
    df = _df("SELECT map_li_underlier AS u, map_li_direction AS d, is_rex, "
             "COALESCE(issuer_nickname, issuer) AS issuer, ticker, "
             "fund_name, aum, fund_flow_1month AS flow_1m "
             "FROM mkt_master_data "
             "WHERE primary_category='LI' AND market_status='ACTV' "
             "AND map_li_underlier IS NOT NULL AND map_li_underlier!='' AND aum > 50")
    if df.empty: return df
    df["u"] = df["u"].astype(str).str.replace(" US", "", regex=False).str.strip()
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    df["is_inv"] = df["d"].astype(str).str.lower().str.contains("short|inv", na=False)
    by_u = df.groupby("u").agg(
        n_long=("is_long", "sum"),
        n_inv=("is_inv", "sum"),
        max_long_aum=("aum", lambda x: df.loc[x.index][df.loc[x.index, "is_long"]]["aum"].max() if df.loc[x.index, "is_long"].any() else 0),
    ).reset_index()
    gap = by_u[(by_u["n_long"] >= 1) & (by_u["n_inv"] == 0)].sort_values("max_long_aum", ascending=False).head(15)
    # Look up the top long product per underlier for display
    rows = []
    for _, r in gap.iterrows():
        u = r["u"]; longs = df[(df["u"] == u) & (df["is_long"])].sort_values("aum", ascending=False)
        if longs.empty: continue
        top = longs.iloc[0]
        rows.append({"underlier": u, "n_long": int(r["n_long"]),
                     "top_long_ticker": top["ticker"], "top_long_name": top["fund_name"],
                     "top_issuer": top["issuer"], "top_aum": float(top["aum"])})
    return pd.DataFrame(rows)


def load_launch_anyway():
    """Underliers with 1-2 long competitors AND a competitor product with large AUM."""
    df = _df("SELECT map_li_underlier AS u, map_li_direction AS d, is_rex, "
             "COALESCE(issuer_nickname, issuer) AS issuer, ticker, fund_name, aum "
             "FROM mkt_master_data WHERE primary_category='LI' AND market_status='ACTV' "
             "AND map_li_underlier IS NOT NULL AND map_li_underlier!='' AND aum>0")
    if df.empty: return df
    df["u"] = df["u"].astype(str).str.replace(" US", "", regex=False).str.strip()
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    rows = []
    for u, g in df[df["is_long"]].groupby("u"):
        n_long = len(g)
        rex_in = bool(g["is_rex"].any())
        if rex_in or not (1 <= n_long <= 2):
            continue
        top = g.sort_values("aum", ascending=False).iloc[0]
        if top["aum"] < 300:  # only show meaningful proven demand
            continue
        rows.append({"underlier": u, "n_long_competitors": n_long,
                     "top_competitor": top["issuer"], "top_product": top["ticker"],
                     "top_aum": float(top["aum"]),
                     "top_product_name": str(top["fund_name"])[:40]})
    out = pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(15) if rows else pd.DataFrame()
    return out


# -------------------- HTML BUILDERS --------------------

def _table_header(cols, widths=None):
    out = '<tr style="background:'+NAVY+';">'
    for i, c in enumerate(cols):
        w = f"width:{widths[i]}px;" if widths and i < len(widths) else ""
        out += f'<th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;{w}">{escape(c)}</th>'
    out += '</tr>'
    return out


def _tr(*cells):
    out = '<tr>'
    for c in cells:
        if isinstance(c, tuple):
            val, align = c
            out += f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:{align};">{val}</td>'
        else:
            out += f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;">{c}</td>'
    out += '</tr>'
    return out


def _section_header(title, color, blurb=""):
    return f"""
<tr><td style="padding:20px 30px 4px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {color};">{escape(title)}</div>
  {f'<div style="font-size:11px;color:{GRAY};margin-top:4px;font-style:italic;">{escape(blurb)}</div>' if blurb else ''}
</td></tr>"""


def _tk(t):
    return f'<span style="font-family:Courier New,monospace;font-weight:700;color:{BLUE};">{escape(str(t))}</span>'


# -------------------- BUILD --------------------

def build():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    today_pretty = datetime.now().strftime("%B %d, %Y")

    # ---- Data ----
    imminent, rex_pos = load_imminent_launches()
    recent_comp = load_recent_competitor_filings()
    pipeline = load_rex_pipeline()
    scored = load_scored()
    inverse_gap = load_inverse_gap()
    launch_anyway = load_launch_anyway()

    # Hot sector from li_sector_daily
    hot_sector = "—"
    try:
        sec_df = _df("SELECT sector, total_mentions FROM li_sector_daily "
                     "WHERE run_date=(SELECT MAX(run_date) FROM li_sector_daily) "
                     "ORDER BY total_mentions DESC LIMIT 1")
        if not sec_df.empty: hot_sector = str(sec_df.iloc[0]['sector'])
    except Exception: pass

    # Top pulse
    n_scored = len(scored)
    top5 = ", ".join(scored["ticker"].head(5).tolist()) if not scored.empty else "—"
    top_buzz = "—"
    if "mentions_24h" in scored.columns and scored["mentions_24h"].notna().any():
        top_idx = scored["mentions_24h"].idxmax()
        top_buzz = f"{scored.at[top_idx, 'ticker']} ({int(scored.at[top_idx, 'mentions_24h'])} mentions)"

    # ---- Race Timing — Imminent launches ----
    if imminent.empty:
        imminent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor PEND products with imminent inception dates.</div>'
    else:
        rows = ""
        for _, r in imminent.iterrows():
            dr = int(r['days_remaining']) if pd.notna(r['days_remaining']) else 0
            urgency_color = RED if dr <= 21 else (ORANGE if dr <= 60 else GRAY)
            u = r['underlier']
            rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rex_html = f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>'
            rows += _tr(
                _tk(u or "—"),
                _tk(r['product_ticker'].replace(' US','') if pd.notna(r['product_ticker']) else "—"),
                escape(str(r['issuer'] or "—"))[:24],
                escape(str(r['fund_name'])[:40]) if pd.notna(r['fund_name']) else "—",
                (escape(str(r['inception_dt'].date()) if pd.notna(r['inception_dt']) else "—"), "center"),
                (f'<span style="color:{urgency_color};font-weight:700;">{dr}d</span>', "center"),
                (rex_html, "center"),
            )
        imminent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier', 'Product', 'Competitor', 'Fund Name', 'Expected Launch', 'Days Remaining', 'REX Position'], [80, 70, 130, 260, 100, 90, 80])}
{rows}
</table>"""

    # ---- Race Timing — Recent competitor filings ----
    if recent_comp.empty:
        recent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor 485APOS in the last 90 days.</div>'
    else:
        rows = ""
        for _, r in recent_comp.iterrows():
            ds = int(r['days_since']) if pd.notna(r['days_since']) else 0
            color = RED if ds <= 14 else (ORANGE if ds <= 45 else GRAY)
            u = r['underlier']
            rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rex_html = f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>'
            rows += _tr(
                _tk(u or "—"),
                escape(str(r['registrant'] or "—"))[:30],
                escape(str(r['series_name'])[:45]) if pd.notna(r['series_name']) else "—",
                (escape(str(r['filing_date_dt'].date())) if pd.notna(r['filing_date_dt']) else "—", "center"),
                (f'<span style="color:{color};font-weight:700;">{ds}d ago</span>', "center"),
                (rex_html, "center"),
            )
        recent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier', 'Registrant', 'Series', 'Filing Date', 'Days Since', 'REX Position'], [80, 150, 250, 100, 90, 80])}
{rows}
</table>"""

    # ---- Our Pipeline ----
    if pipeline.empty:
        pipeline_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No REX products currently in flight.</div>'
    else:
        rows = ""
        for _, r in pipeline.iterrows():
            status = str(r['market_status'])
            status_color = {"ACTV": GREEN, "PEND": ORANGE, "TKCH": GRAY}.get(status, GRAY)
            aum_str = _fmt_mcap(r['aum']) if pd.notna(r['aum']) and r['aum'] > 0 else "—"
            days_str = ""
            if pd.notna(r['days']):
                d = int(r['days'])
                if d > 0: days_str = f'<span style="color:{ORANGE};">launches in {d}d</span>'
                elif d > -30: days_str = f'<span style="color:{GRAY};">live {abs(d)}d</span>'
                else: days_str = f'<span style="color:{GRAY};">live {abs(d)}d</span>'
            inc_str = str(r['inception_dt'].date()) if pd.notna(r['inception_dt']) else "—"
            rows += _tr(
                _tk(r['product_ticker'].replace(' US','') if pd.notna(r['product_ticker']) else "—"),
                escape(str(r['fund_name'])[:50]) if pd.notna(r['fund_name']) else "—",
                _tk(r['underlier'] or "—"),
                (escape(str(r['direction'] or "—")), "center"),
                (escape(str(r['leverage'] or "—")), "center"),
                (f'<span style="color:{status_color};font-weight:700;">{status}</span>', "center"),
                (escape(inc_str), "center"),
                (days_str, "center"),
                (aum_str, "right"),
            )
        pipeline_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker', 'Fund Name', 'Underlier', 'Direction', 'Leverage', 'Status', 'Inception', 'Timing', 'AUM'])}
{rows}
</table>"""

    # ---- Strict Whitespace ----
    sw_filter = (scored.get("n_comp_products", 0).fillna(0) == 0) & \
                (scored.get("n_rex_filed_any", 0).fillna(0) == 0) & \
                (scored.get("n_competitor_485apos_180d", 0).fillna(0) == 0)
    strict_ws = scored[sw_filter].head(20)
    if strict_ws.empty:
        sw_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No names meeting strict whitespace criteria today.</div>'
    else:
        rows = ""
        for _, r in strict_ws.iterrows():
            tk = r['ticker']; name = str(r.get('name') or tk)[:42]
            sector = str(r.get('sector') or '—')[:18]
            themes = str(r.get('themes') or '').replace(',', ' · ')[:32]
            mcap = _fmt_mcap(r.get('market_cap'))
            vol = r.get('rvol_90d') or 0
            ret1m = _fmt_pct(r.get('ret_1m')); ret1y = _fmt_pct(r.get('ret_1y'))
            ment = int(r.get('mentions_24h') or 0)
            score = r.get('composite_score') or 0
            rows += _tr(
                _tk(tk), escape(name), escape(sector), escape(themes) or "—",
                (mcap, "right"), (f"{vol:.0f}%", "right"),
                (ret1m, "right"), (ret1y, "right"),
                (str(ment), "right"),
                (f'<b style="color:{NAVY};">{score:.2f}</b>', "right"),
            )
        sw_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker', 'Company', 'Sector', 'Theme', 'Mkt Cap', '90d Vol', '1m', '1y', 'Buzz', 'Score'])}
{rows}
</table>"""

    # ---- Mega Table — full universe (or top 300 for readability) ----
    mega = scored.head(300)
    rows = ""
    for i, r in mega.reset_index(drop=True).iterrows():
        tk = r['ticker']; name = str(r.get('name') or tk)[:36]
        sector = str(r.get('sector') or '—')[:16]
        themes = str(r.get('themes') or '').replace(',', '·')[:18]
        mcap = _fmt_mcap(r.get('market_cap'))
        vol = r.get('rvol_90d') or 0
        ret1m = _fmt_pct(r.get('ret_1m')); ret1y = _fmt_pct(r.get('ret_1y'))
        ment = int(r.get('mentions_24h') or 0)
        score = r.get('composite_score') or 0
        n_comp = int(r.get('n_comp_products', 0) or 0)
        n_rex = int(r.get('n_rex_filed_any', 0) or 0)
        is_sw = (n_comp == 0 and n_rex == 0 and int(r.get('n_competitor_485apos_180d', 0) or 0) == 0)
        sw_badge = f'<span style="color:{GREEN};font-weight:700;">SW</span>' if is_sw else f'<span style="color:{GRAY};">—</span>'
        rex_badge = f'<span style="color:{BLUE};font-weight:700;">Y</span>' if n_rex > 0 else f'<span style="color:{GRAY};">N</span>'
        rows += _tr(
            (str(i+1), "center"),
            _tk(tk), escape(name), escape(sector), escape(themes),
            (mcap, "right"), (f"{vol:.0f}%", "right"),
            (ret1m, "right"), (ret1y, "right"),
            (str(ment), "right"),
            (str(n_comp), "center"), (rex_badge, "center"), (sw_badge, "center"),
            (f'<b style="color:{NAVY};">{score:.2f}</b>', "right"),
        )
    mega_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['#', 'Ticker', 'Company', 'Sector', 'Theme', 'Mkt Cap', '90d Vol', '1m', '1y', 'Buzz', 'Comp', 'REX', 'SW', 'Score'])}
{rows}
</table>"""

    # ---- Inverse Gap ----
    if inverse_gap.empty:
        ig_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No inverse-gap opportunities surfaced today.</div>'
    else:
        rows = ""
        for _, r in inverse_gap.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(r['top_long_ticker'].replace(' US','') if isinstance(r['top_long_ticker'], str) else "—"),
                escape(str(r['top_long_name'])[:38]),
                escape(str(r['top_issuer'])[:24]),
                (_fmt_mcap(r['top_aum']), "right"),
            )
        ig_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier', 'Long Products', 'Top Long', 'Fund Name', 'Issuer', 'AUM'])}
{rows}
</table>"""

    # ---- Launch Anyway ----
    if launch_anyway.empty:
        la_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No proven-demand 1-2 competitor lanes surfaced today.</div>'
    else:
        rows = ""
        for _, r in launch_anyway.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long_competitors']), "center"),
                _tk(r['top_product'].replace(' US','') if isinstance(r['top_product'], str) else "—"),
                escape(str(r['top_product_name'])),
                escape(str(r['top_competitor'])[:24]),
                (_fmt_mcap(r['top_aum']), "right"),
            )
        la_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier', 'Competitors', 'Top Product', 'Fund Name', 'Top Issuer', 'AUM'])}
{rows}
</table>"""

    # ---- Assemble ----
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>T-REX Stock Recommendation System — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="1100" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1100px;">

<!-- HEADER -->
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">
    T-REX Stock Recommendation System | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;
              text-transform:uppercase;margin-top:6px;">
    Methodology v1.0.2 · Race · Pipeline · Whitespace · Mega Table · Inverse · Launch-Anyway
  </div>
</td></tr>

<!-- PULSE -->
<tr><td style="padding:12px 30px 0;">
  <div style="font-size:12px;color:{GRAY};line-height:1.7;">
    <strong style="color:{NAVY};">{n_scored:,} stocks scored</strong> ·
    <strong style="color:{NAVY};">Top 5:</strong> <span style="font-family:Courier New,monospace;color:{BLUE};font-weight:700;">{escape(top5)}</span> ·
    <strong style="color:{NAVY};">Hot sector:</strong> <span style="color:{PURPLE};font-weight:700;">{escape(hot_sector)}</span> ·
    <strong style="color:{NAVY};">Top buzz:</strong> {escape(top_buzz)}
  </div>
</td></tr>

{_section_header('1 · Race Timing — Imminent Launches', RED, 'Competitor products in PEND with expected inception within 6 months. Closest to launch first.')}
<tr><td style="padding:6px 30px 8px;">{imminent_html}</td></tr>

{_section_header('Race Timing — Recent Competitor Filings (90 days)', ORANGE, 'Competitor 485APOS filings since the start of the audit window. The race may still be winnable.')}
<tr><td style="padding:6px 30px 8px;">{recent_html}</td></tr>

{_section_header('2 · Our Pipeline', BLUE, 'Every REX-filed leveraged/inverse product currently in flight — what we should be focused on launching.')}
<tr><td style="padding:6px 30px 8px;">{pipeline_html}</td></tr>

{_section_header('3 · Strict Whitespace — Clean Files', GREEN, 'Names that clear all three: zero active competitors, zero REX filings ever, zero competitor 485APOS in 180 days. Ranked by score.')}
<tr><td style="padding:6px 30px 8px;">{sw_html}</td></tr>

{_section_header('4 · Mega Table — Long US/ADR Scored Universe', NAVY, 'Every Long US/ADR candidate scored under v1.0.2. SW = strict whitespace. Sortable in spreadsheet form.')}
<tr><td style="padding:6px 30px 8px;">{mega_html}</td></tr>

{_section_header('5 · Inverse Gap', PURPLE, 'Top long products by AUM with no inverse sibling on the same underlier. Deterministic gap list — no scoring.')}
<tr><td style="padding:6px 30px 8px;">{ig_html}</td></tr>

{_section_header('6 · Launch Anyway — Proven Demand, 1-2 Competitor Lanes', BLUE, 'Underliers where REX would be second or third but the existing product has real AUM. Worth chasing despite not being first.')}
<tr><td style="padding:6px 30px 8px;">{la_html}</td></tr>

<!-- FOOTER -->
<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    <strong style="color:{NAVY};">Methodology v1.0.2 — Updated 2026-06-01.</strong>
    Five buckets (Att 34 · Liq 25 · Theme 20 · Mom 12 · Vol 9), SI penalty −8.
    No tier bands. Race timing is a separate overlay, not a score input. Strict whitespace = 3-condition filter.
    Output is a ranked mega table; readers apply their own cutoff.<br><br>
    <strong>REX Financial — Internal use only. Not investment advice.</strong>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""

    out = OUT_DIR / f"trex_combined_{today}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = build()
    print(f"Report: {p}")
