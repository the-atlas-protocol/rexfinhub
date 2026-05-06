"""Final weekly L&I report — assembles HTML with per-ticker research embedded.

Sections:
    1. Top 15 Whitespace Candidates (with research notes)
    2. Where money is flowing right now (active universe top 20)
    3. Sector rollup
    4. Methodology footer
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
OUT = _ROOT / "reports" / f"li_weekly_final_{date.today().isoformat()}.html"


# ---------------------------------------------------------------------------
# Per-ticker research — handwritten, informed by data
# ---------------------------------------------------------------------------
RESEARCH = {
    "AXTI": {
        "thesis": "AXT Inc makes compound-semiconductor substrates (indium phosphide, gallium arsenide) used in laser optics and AI data-center optical interconnect. The 1y +3,941% return is a textbook speculative blow-off driven by the AI photonics narrative.",
        "risk": "Mean-reversion risk is severe after a 40-bagger. The fundamentals haven't repriced this hard. Insider ownership at 5% is not strong enough to anchor.",
        "verdict": "FILE — preserves optionality on the photonics narrative. DON'T launch yet — wait for a 30-50% pullback or earnings catalyst confirmation.",
    },
    "AMPX": {
        "thesis": "Amprius Technologies makes silicon-anode lithium-ion batteries for EV / aviation / drone markets. Up +57% in the last month and +529% over a year — the rally is current, not cooling.",
        "risk": "Pre-revenue speculative tech. Customer adoption timing is unknowable. Energy-density claims are real but commercial scale unproven.",
        "verdict": "FILE — battery thematic remains hot. Watch DRAM-style breakouts in adjacent names; a 2x AMPX could front-run a battery-thematic rotation.",
    },
    "AEHR": {
        "thesis": "AEHR Test Systems builds wafer-level burn-in equipment for SiC (silicon-carbide) power devices. Was a 2023-24 EV-thematic darling on the SiC supercycle. Up 84% in the last 3 months suggests retail is reawakening.",
        "risk": "Severe customer concentration (one large SiC OEM). SiC demand growth has been re-rated downward across the value chain. Single bad earnings = 30%+ drawdown.",
        "verdict": "FILE — interesting if SiC demand re-accelerates with new EV cycle. Launch decision contingent on next earnings.",
    },
    "POET": {
        "thesis": "POET Technologies is a photonic integrated circuit company — same AI / optical interconnect narrative that drove AXTI. Critically, **106 retail mentions** today vs. AXTI's 2 — POET has the active retail attention right now.",
        "risk": "Pre-revenue commercialization risk. Stock is roughly flat over 1m and 3m despite the buzz, suggesting the retail crowd is hopeful but the institutional bid hasn't arrived.",
        "verdict": "FILE + LAUNCH — highest retail-attention conviction in the photonics theme. The 106 mentions make this a candidate for a fast-launch rather than a wait-and-see.",
    },
    "LWLG": {
        "thesis": "Lightwave Logic — development-stage electro-optic polymer story. Up +586% over the year and +59% last month, but **zero retail mentions today**.",
        "risk": "Classic 'narrative-cooled' meme. The pop already happened; retail has moved on. A 2x launch now would catch the descent, not the ascent.",
        "verdict": "FILE — preserves the shelf if the narrative re-ignites. SKIP launch — the run is over.",
    },
    "BW": {
        "thesis": "Babcock & Wilcox — small-modular-reactor (SMR) and clean-energy boiler exposure. The +2,085% 1y reflects the nuclear-renaissance narrative (OKLO, NNE et al.) lifting the entire sector.",
        "risk": "Heavy institutional ownership (75%) and zero retail mentions today. This is a beta play on the nuclear theme rather than a discrete retail-attention name.",
        "verdict": "WATCH — if the SMR theme stays hot we have OKLO, NNE, and BW competing for retail allocation. File only if we want a basket play; otherwise focus capital on the cleaner names.",
    },
    "DOCN": {
        "thesis": "DigitalOcean — cloud infrastructure mid-cap, classic AI-data-center beneficiary. +53% in the last month is a real fundamental move, not a meme spike. Sustainable mid-cap with options liquidity.",
        "risk": "Premium pricing relative to hyperscaler-adjacent comps. Margin compression if Azure/AWS price wars intensify.",
        "verdict": "FILE + LAUNCH — strongest 'real business' name in the top 10. Cleanest narrative for a 2x DigitalOcean product targeting retail wanting AI-cloud exposure without owning NVDA at $4T.",
    },
    "CC": {
        "thesis": "Chemours — fluoropolymer chemicals, refrigerant gas (Opteon). +88% in 3 months. **34 retail mentions** = active conversation. The story is HFC refrigerant phase-out tightening Opteon margins + PFAS litigation overhang resolving.",
        "risk": "PFAS legal exposure is the perpetual sword. Cyclical chemicals exposure to global growth.",
        "verdict": "FILE + LAUNCH — chemicals/materials whitespace is genuinely under-served by L&I. Strong retail pulse + real story.",
    },
    "SOC": {
        "thesis": "Sable Offshore — Santa Ynez offshore oil restart play. Up +100% in the last month on regulatory milestones. Speculative single-asset, single-event story.",
        "risk": "Heavy institutional ownership (94%) with retail mentions = 0 means this is being moved by funds, not retail. A 2x retail product wouldn't capture the right investor base.",
        "verdict": "SKIP — wrong investor mix for a leveraged retail product. Institutions don't buy 2x ETFs.",
    },
    "BKSY": {
        "thesis": "BlackSky Technology — satellite imagery, defense/space tagged in our themes file. Up +33% last month, +225% 1y. Space narrative is real (RKLB / ASTS already have leverage products).",
        "risk": "Sub-$1B mkt cap edge of our floor; small competitor pool already crowded with RKLB/ASTS/LUNR products. Coming late to space.",
        "verdict": "WATCH — space theme but late entrant. Better to wait for a clear catalyst or sector pullback.",
    },
    "IBRX": {
        "thesis": "ImmunityBio — immuno-oncology, FDA approval-driven. Up +287% in 3 months on Anktiva approval/expansion. Recent -22% 1m suggests the spike has cooled.",
        "risk": "Single-drug story — earnings or regulatory miss = 40% drawdown. Heavy institutional (68%) low retail.",
        "verdict": "WATCH — biotech is a different retail demographic. Probably not core L&I-retail audience.",
    },
    "DOW": {
        "thesis": "Dow Inc. — large-cap chemicals, +36% 1m on cyclical recovery. The whitespace here is real: no large-cap chemicals 2x exists despite huge OI.",
        "risk": "Mature cyclical. Retail doesn't typically leverage Dow Chemical. Better suited to CC (Chemours) for the materials thematic angle.",
        "verdict": "FILE — preserves shelf. Don't launch unless materials sector ETFs start showing retail flow.",
    },
    "TSEM": {
        "thesis": "Tower Semiconductor — Israeli analog/specialty foundry. Intel tried to buy them in 2023; deal blocked, stock has rallied since on independent strategic value. +41% 1m, +392% 1y.",
        "risk": "Geopolitical exposure (Israel). Single-foundry-customer concentration.",
        "verdict": "FILE — semis whitespace among non-mega-caps is real. Could work as a 'specialty semis' alternative to the saturated NVDA/AMD trade.",
    },
    "YOU": {
        "thesis": "Clear Secure — biometric airport security (CLEAR lanes). 19 retail mentions, +90% 1y. Travel + biometric narratives.",
        "risk": "Heavy institutional (105% — likely data quirk where institutional > shares outstanding due to overlapping reports). Not a typical retail-leverage stock.",
        "verdict": "WATCH — interesting but the retail story is thin.",
    },
    "QURE": {
        "thesis": "uniQure — gene therapy, hemophilia and Huntington's. Tagged in our biotech_gene theme. Vol 143% reflects binary trial outcomes.",
        "risk": "Pre-revenue clinical trial company. Single readout = make or break.",
        "verdict": "FILE — gene-editing thematic basket play (alongside CRSP/NTLA/BEAM). Don't launch standalone.",
    },
}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_top_whitespace(n: int = 15) -> pd.DataFrame:
    df = pd.read_parquet(WS)
    return df.sort_values("composite_score", ascending=False).head(n)


def load_active_flow_top(n: int = 20) -> pd.DataFrame:
    """Top underliers by trailing 4-week flow into existing leveraged products."""
    ts = pd.read_parquet(TS)
    flow = ts[ts["metric"] == "daily_flow"].copy()
    flow["date"] = pd.to_datetime(flow["date"])

    conn = sqlite3.connect(str(DB))
    try:
        m = pd.read_sql_query(
            "SELECT ticker, map_li_underlier, is_rex FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL",
            conn,
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
# HTML
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', Arial, sans-serif;
       max-width: 980px; margin: 30px auto; color: #1a1a2e; line-height: 1.55;
       padding: 0 24px; background: #fff; }
.cover { border-bottom: 3px solid #0984e3; padding-bottom: 20px; margin-bottom: 30px; }
h1 { color: #1a1a2e; font-size: 32px; margin: 0; font-weight: 700; }
.tagline { color: #0984e3; font-size: 14px; font-weight: 600; margin-top: 6px; letter-spacing: 0.5px; text-transform: uppercase; }
.date { color: #7f8c8d; font-size: 13px; margin-top: 8px; }
h2 { color: #0984e3; font-size: 22px; margin-top: 50px; margin-bottom: 6px;
     padding-bottom: 8px; border-bottom: 2px solid #0984e3; }
.section-intro { color: #566573; font-size: 13px; margin-bottom: 20px; font-style: italic; }
.kpi-row { display: flex; gap: 14px; margin: 24px 0; }
.kpi { flex: 1; background: #f5f7fa; padding: 14px 18px; border-radius: 6px; }
.kpi .label { font-size: 10px; text-transform: uppercase; color: #7f8c8d; letter-spacing: 0.6px; }
.kpi .val { font-size: 26px; font-weight: 700; color: #1a1a2e; margin-top: 4px; }
.pick-card { background: #fff; border: 1px solid #dce4e9; border-left: 5px solid #0984e3;
             padding: 18px 22px; margin: 18px 0; border-radius: 6px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.pick-card.high { border-left-color: #27ae60; }
.pick-card.medium { border-left-color: #0984e3; }
.pick-card.watch { border-left-color: #e67e22; }
.pick-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.pick-ticker { font-family: 'Courier New', monospace; font-weight: 700; color: #0984e3; font-size: 22px; }
.pick-meta { color: #7f8c8d; font-size: 12px; margin-left: 12px; }
.pick-score-pill { background: #0984e3; color: white; padding: 3px 12px; border-radius: 12px;
                   font-size: 12px; font-weight: 600; }
.pick-stats { font-size: 12px; color: #2c3e50; padding: 10px 14px; background: #f8f9fa;
              border-radius: 4px; margin: 10px 0; font-family: monospace; }
.pick-thesis { margin: 12px 0 8px; font-size: 14px; color: #2c3e50; }
.pick-thesis-label { font-size: 11px; text-transform: uppercase; color: #0984e3; font-weight: 600; letter-spacing: 0.4px; }
.pick-risk { margin: 8px 0; font-size: 13px; color: #7f8c8d; }
.pick-risk-label { font-size: 11px; text-transform: uppercase; color: #e67e22; font-weight: 600; letter-spacing: 0.4px; }
.pick-verdict { margin-top: 12px; padding: 10px 14px; background: #e3f2fd; border-radius: 4px;
                font-size: 13px; font-weight: 600; color: #1a1a2e; }
.pick-verdict.file-launch { background: #e8f5e9; }
.pick-verdict.skip { background: #ffebee; color: #c0392b; }
.pick-verdict.watch { background: #fff3e0; color: #d35400; }
.pick-verdict.file { background: #e3f2fd; }
.themes-pill { display: inline-block; background: #27ae60; color: white;
               padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-left: 4px; }
table { width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 12px; }
th { background: #1a1a2e; color: white; padding: 8px; text-align: left; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.3px; font-weight: 600; }
td { padding: 7px 8px; border-bottom: 1px solid #ecf0f1; vertical-align: middle; }
tr:nth-child(even) { background: #f9fafb; }
.ticker-cell { font-family: 'Courier New', monospace; font-weight: 700; color: #0984e3; }
.foot { margin-top: 60px; padding: 24px; background: #f5f7fa; border-radius: 6px;
        font-size: 11.5px; color: #566573; line-height: 1.6; }
.foot strong { color: #1a1a2e; }
.flow-pos { color: #27ae60; font-weight: 600; }
.flow-neg { color: #e74c3c; font-weight: 600; }
"""


def _verdict_class(verdict: str) -> str:
    v = verdict.lower()
    if "file + launch" in v or "file + launch" in v.replace(" ", " "):
        return "file-launch"
    if v.startswith("skip"):
        return "skip"
    if v.startswith("watch"):
        return "watch"
    return "file"


def _border_class(verdict: str) -> str:
    v = verdict.lower()
    if "file + launch" in v or "launch" in v:
        return "high"
    if v.startswith("skip"):
        return "watch"
    if v.startswith("watch"):
        return "watch"
    return "medium"


def _fmt_mcap(v):
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def _fmt_oi(v):
    if pd.isna(v):
        return "—"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1000:
        return f"{v/1000:.0f}K"
    return f"{v:.0f}"


def render_section_1(top: pd.DataFrame) -> str:
    out = ['<h2>The Gap — Top 15 Stocks We Should File On</h2>']
    out.append('<p class="section-intro">Whitespace candidates: U.S. equities ≥ $500M with options activity, '
               'zero existing competitor 2x products, zero REX filings ever, zero competitor 485APOS in last 180 days. '
               'Ranked by demand-priority composite score (retail mentions, realized volatility, thematic relevance, '
               '1m and 1y momentum, insider ownership, minus penalties for short interest and institutional density).</p>')

    for ticker in top.index:
        r = top.loc[ticker]
        sector = r.get("sector") or "—"
        mcap = _fmt_mcap(r["market_cap"])
        rvol = r.get("rvol_90d", 0) or 0
        ret_1m = r.get("ret_1m", 0) or 0
        ret_3m = r.get("ret_3m", 0) or 0
        ret_1y = r.get("ret_1y", 0) or 0
        oi = _fmt_oi(r.get("total_oi"))
        si = r.get("si_ratio", 0) or 0
        mentions = int(r.get("mentions_24h", 0) or 0)
        themes = (r.get("themes") or "").strip()
        score_pct = int(r.get("score_pct", 0) or 0)

        research = RESEARCH.get(ticker, {})
        thesis = research.get("thesis", "Research note pending.")
        risk = research.get("risk", "—")
        verdict = research.get("verdict", "—")

        themes_html = f'<span class="themes-pill">{escape(themes)}</span>' if themes else ""

        out.append(f'''
        <div class="pick-card {_border_class(verdict)}">
            <div class="pick-header">
                <div>
                    <span class="pick-ticker">{escape(ticker)}</span>
                    <span class="pick-meta">{escape(sector)} • {mcap}</span>
                    {themes_html}
                </div>
                <span class="pick-score-pill">Rank {score_pct}th %ile</span>
            </div>
            <div class="pick-stats">
                Vol 90d: <strong>{rvol:.0f}%</strong> ·
                1m / 3m / 1y: <strong>{ret_1m:+.0f}% / {ret_3m:+.0f}% / {ret_1y:+.0f}%</strong> ·
                Total OI: <strong>{oi}</strong> ·
                Short-Int Ratio: <strong>{si:.1f}</strong> ·
                Retail Mentions: <strong>{mentions}</strong>
            </div>
            <div class="pick-thesis-label">Thesis</div>
            <div class="pick-thesis">{escape(thesis)}</div>
            <div class="pick-risk-label">Risk</div>
            <div class="pick-risk">{escape(risk)}</div>
            <div class="pick-verdict {_verdict_class(verdict)}">{escape(verdict)}</div>
        </div>
        ''')
    return "\n".join(out)


def render_section_2(flow_top: pd.DataFrame) -> str:
    out = ['<h2>Where Money Is Flowing — Top 20 Active Underliers (Last 28 Days)</h2>']
    out.append('<p class="section-intro">Net flow magnitude across all leveraged products on each underlier. '
               'Context: where retail money is currently going. REX product count tells us where we\'re capturing '
               'the flow vs. where competitors own the trade.</p>')

    rows = ['<tr><th>#</th><th>Underlier</th><th>4-week Flow ($M)</th>'
            '<th>4w |Flow|</th><th>REX Products</th></tr>']
    for i, (underlier, r) in enumerate(flow_top.iterrows(), 1):
        flow = r["flow_4w"]
        flow_class = "flow-pos" if flow > 0 else "flow-neg"
        rex = int(r.get("n_rex_products", 0) or 0)
        rex_str = f'<strong>{rex}</strong>' if rex > 0 else '<span style="color:#7f8c8d;">none</span>'
        rows.append(
            f'<tr><td>{i}</td>'
            f'<td><span class="ticker-cell">{escape(underlier)}</span></td>'
            f'<td class="{flow_class}">${flow:+,.1f}</td>'
            f'<td>${r["flow_4w_abs"]:,.1f}</td>'
            f'<td>{rex_str}</td></tr>'
        )
    out.append('<table>' + "".join(rows) + '</table>')
    return "\n".join(out)


def render_section_3(top: pd.DataFrame) -> str:
    out = ['<h2>Sector Rollup</h2>']
    rows = ['<tr><th>Sector</th><th>Top Pick</th><th>Mkt Cap</th><th>Score %ile</th></tr>']
    by_sector = top.groupby("sector")
    for sector, grp in by_sector:
        if pd.isna(sector):
            continue
        best = grp.sort_values("composite_score", ascending=False).iloc[0]
        rows.append(
            f'<tr><td>{escape(str(sector))}</td>'
            f'<td><span class="ticker-cell">{escape(str(best.name))}</span></td>'
            f'<td>{_fmt_mcap(best["market_cap"])}</td>'
            f'<td>{int(best.get("score_pct", 0))}th</td></tr>'
        )
    out.append('<table>' + "".join(rows) + '</table>')
    return "\n".join(out)


def render(top: pd.DataFrame, flow_top: pd.DataFrame) -> str:
    today = date.today().strftime("%A, %B %d, %Y")
    n_thematic = int((top.get("is_thematic", 0) == 1).sum()) if "is_thematic" in top.columns else 0
    n_high_mentions = int((top.get("mentions_24h", 0) >= 10).sum()) if "mentions_24h" in top.columns else 0

    return f'''<!doctype html>
<html><head><meta charset="utf-8">
<title>L&amp;I Weekly Recommender — {today}</title>
<style>{CSS}</style></head><body>
<div class="cover">
<h1>L&amp;I Weekly Recommender</h1>
<div class="tagline">REX Financial · Whitespace + Active Universe Briefing</div>
<div class="date">{today}</div>
</div>

<div class="kpi-row">
  <div class="kpi"><div class="label">Whitespace Universe</div><div class="val">1,432</div></div>
  <div class="kpi"><div class="label">Top Picks This Week</div><div class="val">{len(top)}</div></div>
  <div class="kpi"><div class="label">Thematic Picks</div><div class="val">{n_thematic}</div></div>
  <div class="kpi"><div class="label">Retail-Active (≥10 mentions)</div><div class="val">{n_high_mentions}</div></div>
</div>

{render_section_1(top)}
{render_section_2(flow_top)}
{render_section_3(top)}

<div class="foot">
<strong>Methodology.</strong> Universe drawn from NASDAQ + NYSE listings filtered to U.S. equities ≥ $500M with options activity (~1,600 stocks).
Whitespace filter: zero active competitor 2x products + zero REX filings ever + zero competitor 485APOS in last 180 days (~1,432 stocks).
Composite score is a weighted blend of demand-priority signals: retail mention volume from ApeWisdom (22%), realized volatility 30d (15%) and 90d (9%),
thematic relevance from REX's curated theme map (14%), 1-month return (12%), 1-year return (5%), insider ownership (8%) — minus
short-interest ratio penalty (8%) and institutional ownership penalty (7%). Weights validated against a 301-product post-launch success backtest.
Per-ticker theses use general market knowledge plus the underlying signal data; not investment advice.
<br><br>
<strong>Limitations.</strong> Underlier momentum-change signals (week-over-week deltas) require historical signal snapshots that we are now accumulating;
weekly reports will get more accurate over time. The competitor 485APOS detection currently misses filings on not-yet-named products (fix in backlog).
ApeWisdom only surfaces tickers in retail-trending tail; absence of mentions ≠ absence of demand for non-meme names.
<br><br>
<strong>Internal use only.</strong> REX Financial. Not a recommendation to buy or sell any security.
</div>
</body></html>
'''


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    top = load_top_whitespace(15)
    flow_top = load_active_flow_top(20)

    html = render(top, flow_top)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB)", OUT, OUT.stat().st_size / 1024)
    print(f"Report: {OUT}")


if __name__ == "__main__":
    main()
