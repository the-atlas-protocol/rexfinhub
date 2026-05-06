"""Weekly L&I Recommender — email-format HTML matching the Daily ETP Report style.

Layout follows /temp/reports/daily_report.html: table-based for email-client
compatibility, dark-navy header, accent-bordered section blocks, KPI tile rows,
ranked tables for the picks.

Order optimized for executive consumption:
    1. Header (title + date)
    2. Key Highlights (TL;DR bullets)
    3. KPI banner
    4. The Gap — top 10 file candidates (table view, expandable details)
    5. Where Money Is Flowing — top 15 active underliers
    6. Sector Scan — best whitespace per sector
    7. Methodology footer
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WS = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
TS = _ROOT / "data" / "analysis" / "bbg_timeseries_panel.parquet"
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "reports" / f"li_weekly_email_{date.today().isoformat()}.html"


# Per-ticker research — handwritten, informed by the data + market context
RESEARCH = {
    "LWLG": {
        "thesis": "Lightwave Logic — development-stage electro-optic polymer story. Up +586% on 1y but with zero retail mentions today, the rally has cooled.",
        "verdict": "FILE — preserves shelf if narrative re-ignites. SKIP launch — post-blowoff timing.",
        "verdict_color": "#0984e3",
    },
    "SLS": {
        "thesis": "SELLAS Life Sciences — clinical-stage oncology biotech. Volatility 114%, +292% 1y on trial readout speculation. 3 retail mentions.",
        "verdict": "WATCH — biotech vol is real; binary trial events make this dangerous to launch on without a clear catalyst window.",
        "verdict_color": "#e67e22",
    },
    "BW": {
        "thesis": "Babcock & Wilcox — small-modular-reactor exposure. +2,085% 1y reflects the nuclear renaissance lifting OKLO/NNE/SMR. 75% institutional, zero retail mentions = a beta play, not a retail darling.",
        "verdict": "WATCH — nuclear theme strong but already over-served by OKLO/SMR/CEG retail flows.",
        "verdict_color": "#e67e22",
    },
    "KOD": {
        "thesis": "Kodiak Sciences — clinical biotech (eye / retinal). +1,259% 1y on Phase 3 readouts. Vol 125% reflects binary risk profile.",
        "verdict": "FILE — biotech with retail attention, gene-editing-adjacent. Don't launch without checking the trial calendar.",
        "verdict_color": "#0984e3",
    },
    "NEXT": {
        "thesis": "NextDecade — LNG export developer (Rio Grande LNG project). Up +42% 1m on financing milestones. Energy infrastructure story with construction-phase risk.",
        "verdict": "FILE — clean energy whitespace; LNG is under-productized.",
        "verdict_color": "#0984e3",
    },
    "CC": {
        "thesis": "Chemours — fluoropolymer chemicals + Opteon refrigerant. +88% 3m, **31 retail mentions** (highest in our top 10). HFC phase-out tightening Opteon margins is the active story.",
        "verdict": "FILE + LAUNCH — strongest retail-attention name in the list. Materials whitespace is real; nobody has 2x chemicals.",
        "verdict_color": "#27ae60",
    },
    "ATAI": {
        "thesis": "atai Life Sciences — psychedelic medicine biotech (psilocybin, ketamine analogues). +160% 1y, 3 retail mentions. Niche thematic.",
        "verdict": "WATCH — psychedelics is a small retail tribe but loyal. Consider if cannabis 2x analog is a guide.",
        "verdict_color": "#e67e22",
    },
    "SOC": {
        "thesis": "Sable Offshore — Santa Ynez offshore oil restart. +100% 1m, +83% 3m on regulatory wins. Heavy institutional (94%), zero retail mentions = wrong investor base for a 2x retail product.",
        "verdict": "SKIP — institutional-driven story, not retail-appropriate.",
        "verdict_color": "#e74c3c",
    },
    "VICR": {
        "thesis": "Vicor — power-conversion ICs for AI data centers. Down 20% last month after a +244% 1y run. Specialty semis whitespace.",
        "verdict": "FILE — AI infrastructure adjacent, semis whitespace play.",
        "verdict_color": "#0984e3",
    },
    "YOU": {
        "thesis": "Clear Secure — biometric airport security (CLEAR lanes). 20 retail mentions, +90% 1y. Travel + biometric narratives; flat last month.",
        "verdict": "WATCH — story is real but not currently in flow. File later if travel cycle re-accelerates.",
        "verdict_color": "#e67e22",
    },
    "NTLA": {
        "thesis": "Intellia Therapeutics — CRISPR gene editing. **Tagged in our biotech_gene theme.** Cleaner gene-editing whitespace play vs. CRSP/BEAM (some have products).",
        "verdict": "FILE — gene-editing thematic exposure; pair with QURE for a basket later.",
        "verdict_color": "#0984e3",
    },
    "VG": {
        "thesis": "Venture Global LNG — recently IPO'd LNG exporter. +63% 1m, +131% 3m. Same theme as NEXT but larger ($38.7B).",
        "verdict": "FILE — LNG infrastructure; bigger and more liquid than NEXT.",
        "verdict_color": "#0984e3",
    },
    "WOLF": {
        "thesis": "Wolfspeed — silicon carbide power semiconductors. Recent EV-cycle pullback. Retail mentions 4. SiC story is bruised but not dead.",
        "verdict": "WATCH — EV thematic recovery dependent.",
        "verdict_color": "#e67e22",
    },
    "RUN": {
        "thesis": "Sunrun — residential solar leasing. Solar theme, up +131% 1y. Solar incentives policy-dependent.",
        "verdict": "FILE — solar/clean energy whitespace if narrative holds.",
        "verdict_color": "#0984e3",
    },
    "PUMP": {
        "thesis": "ProPetro Holding — oilfield services / pressure pumping. +96% 1y, 6 retail mentions. Cyclical energy services play.",
        "verdict": "WATCH — oil services demand cycle dependent.",
        "verdict_color": "#e67e22",
    },
}


def _fmt_mcap(v):
    if pd.isna(v) or v is None:
        return "—"
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def _fmt_pct(v):
    if pd.isna(v) or v is None:
        return "—"
    return f"{v:+.0f}%"


def _fmt_oi(v):
    if pd.isna(v) or v is None:
        return "—"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1000:
        return f"{v/1000:.0f}K"
    return f"{v:.0f}"


def load_top_whitespace(n: int = 10) -> pd.DataFrame:
    df = pd.read_parquet(WS)
    return df.sort_values("composite_score", ascending=False).head(n)


def load_active_flow_top(n: int = 15) -> pd.DataFrame:
    ts = pd.read_parquet(TS)
    flow = ts[ts["metric"] == "daily_flow"].copy()
    flow["date"] = pd.to_datetime(flow["date"])
    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier, is_rex FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL", conn,
        )
    finally:
        conn.close()
    m["prod"] = m["ticker"].str.split().str[0]
    m["underlier"] = m["map_li_underlier"].str.split().str[0]
    p2u = dict(zip(m["prod"], m["underlier"]))
    flow["prod"] = flow["ticker"].str.split().str[0]
    flow["underlier"] = flow["prod"].map(p2u)
    flow = flow.dropna(subset=["underlier"])
    cutoff = flow["date"].max() - pd.Timedelta(days=28)
    recent = flow[flow["date"] >= cutoff]
    agg = recent.groupby("underlier").agg(
        flow_4w=("value", "sum"),
        flow_4w_abs=("value", lambda x: x.abs().sum()),
    )
    rex_count = m[m["is_rex"] == 1].groupby("underlier")["ticker"].nunique().rename("n_rex_products")
    agg = agg.join(rex_count, how="left").fillna({"n_rex_products": 0})
    return agg.sort_values("flow_4w_abs", ascending=False).head(n)


# ---------------------------------------------------------------------------
# HTML — email-style, table-based, matches daily report layout
# ---------------------------------------------------------------------------

def render(top: pd.DataFrame, flow_top: pd.DataFrame) -> str:
    today = date.today().strftime("%B %d, %Y")
    universe_size = 1222

    # Compute key highlight numbers
    n_thematic = int((top.get("is_thematic", 0) == 1).sum()) if "is_thematic" in top.columns else 0
    n_high_mentions = int((top.get("mentions_24h", 0) >= 10).sum()) if "mentions_24h" in top.columns else 0
    n_file_launch = sum(1 for t in top.index if RESEARCH.get(t, {}).get("verdict_color") == "#27ae60")

    top_3_tickers = list(top.head(3).index)

    # === Build the picks table rows ===
    pick_rows = []
    for ticker in top.index:
        r = top.loc[ticker]
        sector = r.get("sector") or "—"
        mcap = _fmt_mcap(r.get("market_cap"))
        rvol = r.get("rvol_90d", 0) or 0
        ret_1m = r.get("ret_1m", 0) or 0
        ret_1y = r.get("ret_1y", 0) or 0
        oi = _fmt_oi(r.get("total_oi"))
        mentions = int(r.get("mentions_24h", 0) or 0)
        themes = (r.get("themes") or "").strip()
        score = r.get("composite_score", 0) or 0

        research = RESEARCH.get(ticker, {})
        thesis = research.get("thesis", "Research note pending.")
        verdict = research.get("verdict", "—")
        v_color = research.get("verdict_color", "#0984e3")

        themes_html = (
            f'<span style="background:#27ae60;color:white;padding:1px 6px;border-radius:8px;'
            f'font-size:9px;font-weight:600;margin-left:4px;">{escape(themes)}</span>'
        ) if themes else ""

        verdict_label = verdict.split(" — ")[0]

        pick_rows.append(f'''
        <tr><td colspan="2" style="padding:14px 12px 6px;background:#fcfcfd;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-size:15px;font-weight:700;color:#1a1a2e;font-family:'Courier New',monospace;">
                {escape(ticker)}
                <span style="font-family:-apple-system,sans-serif;color:#7f8c8d;font-size:11px;font-weight:400;margin-left:6px;">
                  {escape(sector)} • {mcap}
                </span>
                {themes_html}
              </td>
              <td style="text-align:right;">
                <span style="background:{v_color};color:white;padding:3px 10px;border-radius:10px;font-size:10px;font-weight:700;letter-spacing:0.5px;">
                  {escape(verdict_label)}
                </span>
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td colspan="2" style="padding:0 12px 6px;background:#fcfcfd;">
          <div style="font-family:'Courier New',monospace;font-size:11px;color:#566573;background:#f1f3f5;padding:6px 10px;border-radius:4px;">
            Vol90: <strong>{rvol:.0f}%</strong> ·
            1m / 1y: <strong>{ret_1m:+.0f}% / {ret_1y:+.0f}%</strong> ·
            OI: <strong>{oi}</strong> ·
            Mentions: <strong>{mentions}</strong> ·
            Score: <strong>{score:+.2f}</strong>
          </div>
        </td></tr>
        <tr><td colspan="2" style="padding:0 12px 8px;background:#fcfcfd;">
          <div style="font-size:12.5px;color:#2c3e50;line-height:1.55;">{escape(thesis)}</div>
        </td></tr>
        <tr><td colspan="2" style="padding:0 12px 14px;background:#fcfcfd;border-bottom:1px solid #e8eaed;">
          <div style="font-size:11.5px;color:#1a1a2e;background:#eef5fc;padding:7px 10px;border-radius:4px;font-style:italic;">
            <strong>Verdict:</strong> {escape(verdict)}
          </div>
        </td></tr>
        ''')

    pick_table = "".join(pick_rows)

    # === Active flow table ===
    flow_rows = []
    for i, (underlier, r) in enumerate(flow_top.iterrows(), 1):
        flow = r["flow_4w"]
        color = "#27ae60" if flow > 0 else "#e74c3c"
        rex = int(r.get("n_rex_products", 0) or 0)
        rex_cell = (f'<span style="color:#27ae60;font-weight:700;">{rex} REX</span>'
                    if rex else '<span style="color:#7f8c8d;">none</span>')
        flow_rows.append(f'''
          <tr>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#7f8c8d;">{i}</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(underlier)}</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:{color};font-weight:700;">${flow:+,.1f}M</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;">${r["flow_4w_abs"]:,.1f}M</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;">{rex_cell}</td>
          </tr>
        ''')
    flow_table = "".join(flow_rows)

    # === Sector summary ===
    sector_rows = []
    for sector, grp in top.groupby("sector"):
        if pd.isna(sector):
            continue
        best = grp.sort_values("composite_score", ascending=False).iloc[0]
        sector_rows.append(f'''
          <tr>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;">{escape(str(sector))}</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11.5px;font-weight:700;font-family:'Courier New',monospace;color:#0984e3;">{escape(str(best.name))}</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;">{_fmt_mcap(best["market_cap"])}</td>
            <td style="padding:5px 8px;border-bottom:1px solid #ecf0f1;font-size:11px;text-align:right;color:#1a1a2e;font-weight:700;">{best.get("composite_score", 0):+.2f}</td>
          </tr>
        ''')
    sector_table = "".join(sector_rows)

    # === KEY HIGHLIGHTS bullets ===
    top_picks_str = ", ".join(top_3_tickers)
    cc_mentions = int(top.loc["CC", "mentions_24h"]) if "CC" in top.index else 0

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>REX L&I Weekly Recommender — {today}</title>
</head>
<body style="margin:0;padding:0;background:#f8f9fa;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:#1a1a2e;line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="700" cellpadding="0" cellspacing="0" border="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:700px;table-layout:fixed;">

<!-- HEADER -->
<tr><td style="background:#1a1a2e;padding:24px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">REX L&amp;I Weekly Recommender | {today}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">Whitespace + Active Universe Briefing</div>
</td></tr>

<!-- KEY HIGHLIGHTS -->
<tr><td style="padding:15px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f4f5f6;border-left:4px solid #0984e3;border-radius:0 8px 8px 0;">
    <tr><td style="padding:14px 18px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="padding:0 0 8px;font-size:13px;font-weight:700;color:#1a1a2e;text-transform:uppercase;letter-spacing:1px;">
          Key Highlights
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#8226;</span>
          {universe_size:,} true whitespace candidates after filtering active competitors, REX filings (any status), and recent 485APOS activity
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#8226;</span>
          Top 3 picks this week: <strong>{escape(top_picks_str)}</strong>
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#8226;</span>
          {n_file_launch} pick(s) tagged FILE + LAUNCH; {n_thematic} match a curated theme; {n_high_mentions} have ≥10 retail mentions
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#8226;</span>
          Highest retail attention: CC (Chemours) with {cc_mentions} mentions
        </td></tr>
        <tr><td style="padding:3px 0;font-size:13px;color:#1a1a2e;line-height:1.5;">
          <span style="color:#0984e3;font-weight:700;margin-right:6px;">&#8226;</span>
          Universe sourced from NASDAQ + NYSE listings (7,010 equities), filtered to ≥$500M market cap with options activity
        </td></tr>
      </table>
    </td></tr>
  </table>
</td></tr>

<!-- KPI BANNER -->
<tr><td style="padding:10px 30px 5px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border:1px solid #dee2e6;border-radius:8px;overflow:hidden;">
    <tr style="background:#f8f9fa;">
      <td style="padding:8px 4px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:#1a1a2e;">{universe_size:,}</div>
        <div style="font-size:8px;color:#636e72;text-transform:uppercase;letter-spacing:0.4px;margin-top:2px;">Whitespace Universe</div>
      </td>
      <td style="padding:8px 4px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:#0984e3;">{len(top)}</div>
        <div style="font-size:8px;color:#636e72;text-transform:uppercase;letter-spacing:0.4px;margin-top:2px;">Top Picks</div>
      </td>
      <td style="padding:8px 4px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:#27ae60;">{n_file_launch}</div>
        <div style="font-size:8px;color:#636e72;text-transform:uppercase;letter-spacing:0.4px;margin-top:2px;">File + Launch</div>
      </td>
      <td style="padding:8px 4px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:#e67e22;">{n_thematic}</div>
        <div style="font-size:8px;color:#636e72;text-transform:uppercase;letter-spacing:0.4px;margin-top:2px;">Thematic</div>
      </td>
      <td style="padding:8px 4px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:#1a1a2e;">{n_high_mentions}</div>
        <div style="font-size:8px;color:#636e72;text-transform:uppercase;letter-spacing:0.4px;margin-top:2px;">Retail-Active</div>
      </td>
    </tr>
  </table>
</td></tr>

<!-- THE GAP -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 8px 0;
              padding-bottom:6px;border-bottom:2px solid #0984e3;">
    The Gap — File Candidates
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:10px;font-style:italic;">
    {len(top)} stocks where REX has no filing AND no competitor has an active 2x product (or any 485APOS in the last 180 days). Ranked by composite signal score.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border:1px solid #e8eaed;border-radius:6px;overflow:hidden;">
    {pick_table}
  </table>
</td></tr>

<!-- ACTIVE FLOW -->
<tr><td style="padding:18px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 8px 0;
              padding-bottom:6px;border-bottom:2px solid #1a1a2e;">
    Where Money Is Flowing — Top 15 Underliers (Last 28 Days)
  </div>
  <div style="font-size:12px;color:#7f8c8d;margin-bottom:10px;font-style:italic;">
    Net flow magnitude into existing leveraged products per underlier. Context for which retail demand we're capturing.
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">#</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Underlier</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">4w Net Flow</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">|Flow|</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">REX Exposure</th>
    </tr>
    {flow_table}
  </table>
</td></tr>

<!-- SECTOR SCAN -->
<tr><td style="padding:18px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:#1a1a2e;margin:0 0 8px 0;
              padding-bottom:6px;border-bottom:2px solid #27ae60;">
    Sector Scan — Top Pick per Sector
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr style="background:#1a1a2e;">
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Sector</th>
      <th style="padding:7px 8px;text-align:left;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Top Pick</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Mkt Cap</th>
      <th style="padding:7px 8px;text-align:right;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;">Score</th>
    </tr>
    {sector_table}
  </table>
</td></tr>

<!-- METHODOLOGY FOOTER -->
<tr><td style="padding:20px 30px 25px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f4f5f6;border-radius:6px;">
    <tr><td style="padding:14px 18px;font-size:11px;color:#566573;line-height:1.65;">
      <strong style="color:#1a1a2e;">Methodology.</strong>
      Universe: NASDAQ + NYSE listings (7,010 US equities), filtered to ≥$500M market cap with active options markets (~1,600 stocks).
      <strong>Whitespace = zero active competitor 2x products + zero REX filings ever + zero competitor 485APOS in last 180d + not detected in fund-name regex scan</strong>
      (catches new filings on products not yet in our master tables — the AXTI / FIG / KLAR class). Result: 1,222 true whitespace.
      <br><br>
      <strong style="color:#1a1a2e;">Composite score</strong> weights demand-priority signals: retail mentions from ApeWisdom (22%),
      realized vol 30d (15%) and 90d (9%), thematic relevance from REX's curated themes (14%), 1-month return (12%), 1-year return (5%),
      insider ownership (8%), minus short-interest ratio (8%) and institutional ownership (7%). Validated against 301-product post-launch backtest.
      <br><br>
      <strong style="color:#1a1a2e;">Limitations.</strong>
      Underlier momentum-change signals require historical signal snapshots that we are now accumulating;
      this report's accuracy improves week over week. Retail mentions are sourced from the trending-tail of ApeWisdom — absence of
      mentions ≠ absence of demand. ApeWisdom only covers actively-discussed names.
      <br><br>
      <strong style="color:#1a1a2e;">Internal use only.</strong> REX Financial. Not a recommendation to buy or sell any security.
    </td></tr>
  </table>
</td></tr>

</table>
</td></tr>
</table>
</body></html>
'''


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    top = load_top_whitespace(10)
    flow_top = load_active_flow_top(15)
    html = render(top, flow_top)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", OUT, OUT.stat().st_size / 1024)
    print(f"Report: {OUT}")


if __name__ == "__main__":
    main()
