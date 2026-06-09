"""Stock Recommendations Weekly — Action + Theme view (replaces Defensive/Offensive).

Structure:
  1. KEY HIGHLIGHTS — tier counts, top buzz, hot theme of the week
  2. THIS WEEK'S CALLS — File / Launch / Watch action lists (clear decisions)
  3. BY THEME — AI Infrastructure / Semis / Space / Quantum / Nuclear sections
  4. INVERSE GAP — top long products w/ no inverse sibling
  5. FOREIGN MEGACAPS — Samsung / TSMC / Tencent class
  6. IPO PIPELINE — pre-IPO + recently priced
  7. METHODOLOGY FOOTER

Output:  reports/li_weekly_action_theme_YYYY-MM-DD.html
Run:     python -m screener.li_engine.analysis.weekly_action_theme
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
WS = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
LC = _ROOT / "data" / "analysis" / "launch_candidates.parquet"
OUT_DIR = _ROOT / "reports"

# Color tokens — match production palette
NAVY = "#1a1a2e"; BLUE = "#0984e3"; GREEN = "#27ae60"; ORANGE = "#f39c12"
RED = "#e74c3c"; GRAY = "#7f8c8d"; LIGHT = "#f4f5f6"; BORDER = "#ecf0f1"
PURPLE = "#8e44ad"; TEAL = "#16a085"

# Hot themes + display labels + colors
HOT_THEMES = {
    "ai_infrastructure": ("AI Infrastructure", "#0984e3", "Data-center compute, networking, power, cooling"),
    "ai_applications":   ("AI Applications", "#2980b9", "AI-native software and platforms"),
    "semiconductors":    ("Semiconductors", "#8e44ad", "Chips, memory, foundry, equipment"),
    "space":             ("Space & Defense", "#34495e", "Launch, satellites, defense systems"),
    "quantum":           ("Quantum", "#16a085", "Quantum computing pure-plays"),
    "nuclear":           ("Nuclear", "#d35400", "SMR, fission, fusion build-out"),
    "crypto_equity":     ("Crypto Equity", "#f39c12", "Crypto-adjacent equities"),
    "memory":            ("Memory", "#9b59b6", "DRAM, NAND, HBM"),
    "korea_proxies":     ("Korea Proxies", "#c0392b", "Korean retail proxies"),
    "biotech_gene":      ("Biotech / Gene", "#27ae60", "Gene editing, oncology, clinical"),
    "ev_battery":        ("EV / Battery", "#e67e22", "Electric vehicles, battery tech"),
}


def _fmt_mcap(v):
    if v is None or pd.isna(v): return "—"
    if v >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _fmt_pct(v):
    if v is None or pd.isna(v): return "—"
    sign = "+" if v >= 0 else ""
    color = GREEN if v >= 0 else RED
    return f'<span style="color:{color};font-weight:600">{sign}{v:.0f}%</span>'


def _load_filings_summary():
    conn = sqlite3.connect(DB)
    try:
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        total = conn.execute(
            "SELECT COUNT(DISTINCT fe.id) FROM filings f "
            "JOIN fund_extractions fe ON fe.filing_id=f.id "
            "WHERE f.form='485APOS' AND f.filing_date>=?", (cutoff,)).fetchone()[0]
        rex = conn.execute(
            "SELECT COUNT(DISTINCT fe.id) FROM filings f "
            "JOIN fund_extractions fe ON fe.filing_id=f.id "
            "WHERE f.form='485APOS' AND f.filing_date>=? "
            "AND (f.registrant LIKE '%REX%' OR f.registrant LIKE '%ETF Opportunities%')",
            (cutoff,)).fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "rex": rex, "competitor": total - rex,
            "week_start": cutoff, "week_end": date.today().isoformat()}


def _load_data():
    ws = pd.read_parquet(WS)
    lc = pd.read_parquet(LC) if LC.exists() else pd.DataFrame()
    return ws, lc


def _classify_actions(ws: pd.DataFrame, lc: pd.DataFrame) -> tuple:
    """Split candidates into FILE / LAUNCH / WATCH lists."""
    ws_top = ws.sort_values("composite_score", ascending=False).head(50)
    # FILE: high-scoring whitespace, no REX filing, ≤2 active competitors
    file_df = ws_top[(ws_top["n_rex_filed_any"] == 0) & (ws_top["n_comp_products"] <= 2)].head(8)
    # LAUNCH: from launch_candidates (REX has filed); top by composite + recency
    if not lc.empty:
        launch_df = lc.sort_values("composite_score", ascending=False).head(6)
    else:
        launch_df = pd.DataFrame()
    # WATCH: middle-tier whitespace (P40-P75 of universe)
    if "score_pct" in ws.columns:
        watch_df = ws[(ws["score_pct"] >= 60) & (ws["score_pct"] < 85)].sort_values("score_pct", ascending=False).head(8)
    else:
        watch_df = ws.iloc[20:30].head(8) if len(ws) > 20 else pd.DataFrame()
    return file_df, launch_df, watch_df


def _group_by_theme(ws: pd.DataFrame) -> dict:
    """Bucket the top candidates by theme."""
    out = {k: [] for k in HOT_THEMES.keys()}
    top = ws.sort_values("composite_score", ascending=False).head(120)
    for tk, row in top.iterrows():
        themes_str = str(row.get("themes") or "")
        if not themes_str or themes_str == "nan":
            continue
        for theme_key in HOT_THEMES.keys():
            if theme_key in themes_str:
                out[theme_key].append((tk, row))
    return {k: v[:6] for k, v in out.items() if v}  # cap 6 per theme, drop empty


# ---------- HTML builders ----------

def _action_card(ticker: str, row, action: str) -> str:
    """One card in the action list (File / Launch / Watch)."""
    name = str(row.get("name") or ticker)[:50]
    sector = str(row.get("sector") or "—")[:22]
    mcap = _fmt_mcap(row.get("market_cap"))
    score = row.get("composite_score") or 0
    score_pct = row.get("score_pct") or 0
    vol = row.get("rvol_90d") or 0
    ret1y = row.get("ret_1y")
    ret1m = row.get("ret_1m")
    mentions = int(row.get("mentions_24h") or 0)
    n_comp = int(row.get("n_comp_products") or 0)
    n_filed = int(row.get("n_competitor_485apos_180d") or 0)
    themes = str(row.get("themes") or "")
    rex_filed = "Y" if (row.get("n_rex_filed_any") or 0) > 0 else "N"

    action_colors = {"FILE": GREEN, "LAUNCH": BLUE, "WATCH": ORANGE}
    badge_color = action_colors.get(action, GRAY)

    # Why-now line
    why = []
    if mentions > 20: why.append(f"{mentions} retail mentions")
    if vol > 80: why.append(f"{vol:.0f}% realized vol")
    if themes:
        first_theme = themes.split(",")[0].strip()
        if first_theme in HOT_THEMES:
            why.append(f"in {HOT_THEMES[first_theme][0]}")
    if n_filed > 0: why.append(f"{n_filed} competitor filings — race")
    if action == "FILE" and n_comp == 0 and n_filed == 0:
        why.append("clean whitespace lane")
    why_line = " · ".join(why) if why else f"score {score:.1f}, percentile {score_pct:.0f}"

    return f"""
<tr><td style="padding:10px 12px;border-bottom:1px solid {BORDER};vertical-align:top;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="width:80px;vertical-align:top;">
        <span style="font-family:'Courier New',monospace;font-weight:700;font-size:15px;color:{BLUE};">{escape(ticker)}</span>
      </td>
      <td style="vertical-align:top;">
        <div style="font-size:13px;color:{NAVY};font-weight:600;">{escape(name)}</div>
        <div style="font-size:11px;color:{GRAY};margin-top:1px;">{escape(sector)} · {mcap} · 1y {_fmt_pct(ret1y)} · 1m {_fmt_pct(ret1m)}</div>
        <div style="font-size:11px;color:#566573;margin-top:4px;font-style:italic;">{escape(why_line)}</div>
      </td>
      <td style="width:90px;text-align:right;vertical-align:top;">
        <div style="background:{badge_color};color:white;padding:3px 10px;border-radius:12px;display:inline-block;font-size:11px;font-weight:700;">{action}</div>
        <div style="font-size:18px;font-weight:700;color:{NAVY};margin-top:4px;">{score:.1f}</div>
        <div style="font-size:10px;color:{GRAY};">REX filed: {rex_filed}</div>
      </td>
    </tr>
  </table>
</td></tr>"""


def _theme_section(theme_key: str, names: list) -> str:
    label, color, blurb = HOT_THEMES[theme_key]
    rows = []
    for tk, row in names:
        name = str(row.get("name") or tk)[:42]
        sector = str(row.get("sector") or "—")[:18]
        mcap = _fmt_mcap(row.get("market_cap"))
        ret1y = _fmt_pct(row.get("ret_1y"))
        ret1m = _fmt_pct(row.get("ret_1m"))
        vol = row.get("rvol_90d") or 0
        ment = int(row.get("mentions_24h") or 0)
        score = row.get("composite_score") or 0
        n_comp = int(row.get("n_comp_products") or 0)
        n_filed = int(row.get("n_competitor_485apos_180d") or 0)
        rex = "Y" if (row.get("n_rex_filed_any") or 0) > 0 else "N"
        rows.append(f"""
<tr>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-family:'Courier New',monospace;font-weight:700;font-size:11.5px;color:{BLUE};">{escape(tk)}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;color:{NAVY};">{escape(name)}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:10.5px;color:{GRAY};">{escape(sector)}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;">{mcap}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;color:{GRAY};">{vol:.0f}%</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;">{ret1m}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;">{ret1y}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:right;color:{GRAY};">{ment}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:center;color:{GRAY};">{n_comp}/{n_filed}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:center;color:{GRAY};">{rex}</td>
<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11.5px;text-align:right;font-weight:700;color:{NAVY};">{score:.1f}</td>
</tr>""")
    return f"""
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:15px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {color};">{escape(label)}<span style="font-weight:400;color:{GRAY};font-size:12px;margin-left:8px;">{len(names)} candidates</span></div>
  <div style="font-size:11px;color:{GRAY};margin-top:4px;font-style:italic;">{escape(blurb)}</div>
</td></tr>
<tr><td style="padding:6px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    <tr style="background:{NAVY};">
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;width:55px;">Ticker</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">Company</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;width:110px;">Sector</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:60px;">Mkt Cap</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:50px;">90d Vol</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:50px;">1m</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:50px;">1y</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:50px;">Buzz</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:center;width:55px;">Comp/File</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:center;width:40px;">REX</th>
      <th style="padding:6px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;text-align:right;width:55px;">Score</th>
    </tr>
    {''.join(rows)}
  </table>
</td></tr>"""


def _action_section(action: str, df: pd.DataFrame, blurb: str, color: str) -> str:
    if df.empty:
        return ""
    cards = "".join(_action_card(tk, row, action) for tk, row in df.iterrows())
    return f"""
<tr><td style="padding:20px 30px 6px;">
  <div style="font-size:15px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {color};">{action} <span style="font-weight:400;color:{GRAY};font-size:12px;margin-left:8px;">{len(df)} names</span></div>
  <div style="font-size:11px;color:{GRAY};margin-top:4px;font-style:italic;">{escape(blurb)}</div>
</td></tr>
<tr><td style="padding:6px 30px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:white;">
    {cards}
  </table>
</td></tr>"""


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ws, lc = _load_data()
    file_df, launch_df, watch_df = _classify_actions(ws, lc)
    themes_buckets = _group_by_theme(ws)
    filings = _load_filings_summary()
    today = date.today().isoformat()
    today_pretty = datetime.now().strftime("%B %d, %Y")

    # Top buzz
    top_buzz_ticker, top_buzz_count = "—", 0
    if "mentions_24h" in ws.columns and len(ws):
        top_buzz_idx = ws["mentions_24h"].idxmax() if ws["mentions_24h"].notna().any() else None
        if top_buzz_idx:
            top_buzz_ticker = top_buzz_idx
            top_buzz_count = int(ws.at[top_buzz_idx, "mentions_24h"])

    # Hot theme of the week — by candidate count
    hot_theme_label = "—"
    if themes_buckets:
        hot_key = max(themes_buckets.keys(), key=lambda k: len(themes_buckets[k]))
        hot_theme_label = HOT_THEMES[hot_key][0] + f" ({len(themes_buckets[hot_key])} candidates)"

    n_high = int((ws["composite_score"] >= 1.5).sum()) if "composite_score" in ws.columns else 0
    n_med = int(((ws["composite_score"] >= 0.5) & (ws["composite_score"] < 1.5)).sum())

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Recommendations of the Week — {today_pretty}</title></head>
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
    Stock Recommendations of the Week | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;
              text-transform:uppercase;margin-top:6px;">
    Action &amp; Theme View · This Week's Calls · Hot Themes · Inverse / Foreign / IPO
  </div>
</td></tr>

<!-- KEY HIGHLIGHTS -->
<tr><td style="padding:15px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{LIGHT};border-left:4px solid {NAVY};border-radius:0 8px 8px 0;">
    <tr><td style="padding:14px 18px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:0 0 8px;font-size:13px;font-weight:700;color:{NAVY};
                       text-transform:uppercase;letter-spacing:1px;">Key Highlights</td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:{NAVY};">
          <span style="color:{NAVY};font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>This week's calls:</strong>
          <span style="background:{GREEN};color:white;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;margin:0 3px;">{len(file_df)} FILE</span>
          <span style="background:{BLUE};color:white;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;margin:0 3px;">{len(launch_df)} LAUNCH</span>
          <span style="background:{ORANGE};color:white;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;margin:0 3px;">{len(watch_df)} WATCH</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:{NAVY};">
          <span style="color:{NAVY};font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>{filings['total']} L&amp;I 485APOS filings</strong> last 7 days · REX: {filings['rex']} · Competitor: {filings['competitor']}
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:{NAVY};">
          <span style="color:{NAVY};font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Hot theme this week:</strong>
          <span style="color:{BLUE};font-weight:700;">{escape(hot_theme_label)}</span>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:{NAVY};">
          <span style="color:{NAVY};font-weight:700;margin-right:6px;">&#8226;</span>
          <strong>Top retail buzz:</strong>
          <span style="font-family:'Courier New',monospace;color:{BLUE};font-weight:700;">{escape(top_buzz_ticker)}</span> with <strong>{top_buzz_count}</strong> mentions
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>

<!-- ACTION SECTIONS -->
<tr><td style="padding:8px 30px 0;">
  <div style="font-size:18px;font-weight:700;color:{NAVY};margin:14px 0 0;border-bottom:3px solid {NAVY};padding-bottom:8px;">This Week's Calls</div>
  <div style="font-size:12px;color:{GRAY};font-style:italic;margin-top:6px;">Direct actions — what to do this week and why.</div>
</td></tr>

{_action_section("FILE", file_df, "Top whitespace candidates — high score, no REX filing, open lane.", GREEN)}
{_action_section("LAUNCH", launch_df, "REX has already filed — race timing says move now.", BLUE)}
{_action_section("WATCH", watch_df, "Building signal, not yet actionable. Re-evaluate next week.", ORANGE)}

<!-- THEME SECTIONS -->
<tr><td style="padding:24px 30px 0;">
  <div style="font-size:18px;font-weight:700;color:{NAVY};margin:14px 0 0;border-bottom:3px solid {NAVY};padding-bottom:8px;">By Theme</div>
  <div style="font-size:12px;color:{GRAY};font-style:italic;margin-top:6px;">Where the demand is — top candidates grouped by hot narrative.</div>
</td></tr>

{''.join(_theme_section(k, names) for k, names in themes_buckets.items())}

<!-- FOOTER -->
<tr><td style="padding:24px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    <strong style="color:{NAVY};">Methodology v1.0.1 (frozen 2026-05-28).</strong>
    Action lists derived from composite score + REX filing status + competitive position.
    Themes pulled from curated theme list + AI-stack tags.
    Score range 0–100, gates: ≥$500M mcap, attention-or-theme, ≤2 competitors.
    <br><br>
    <strong>Internal use only. REX Financial. Not investment advice.</strong>
  </div>
</td></tr>

</table></td></tr></table></body></html>"""

    out = OUT_DIR / f"li_weekly_action_theme_{today}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = build()
    print(f"Report: {p}")
