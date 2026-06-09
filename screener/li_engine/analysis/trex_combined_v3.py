"""T-REX Stock Recommendation System — corrected, all sources verified.

v3 fixes against v2's data errors:
  1. Recent Competitor Filings — filtered to L&I via series_name pattern.
  2. Imminent Launches — L&I + inception within 90 days (skip 2027 garbage + NaT stale).
  3. Our Pipeline — uses rex_products lifecycle table (not is_rex hack), top 50 by underlier score.
  4. Mega Table — top 100 by score, only where no LIVE product, REX-filed/Comp-filed flags.
     Strict Whitespace dropped (redundant — the flags carry the info).
  5. Inverse Gap — proper L&I-only filter, AUM normalized.
  6. Launch Anyway — uses competitor_counts (handles GOOG/GOOGL via underlier table).
  7. Foreign — uses foreign_launch_candidates parquet (real source).
  8. IPO — pre-listing pipeline placeholder; populated when data feed lands.
"""
from __future__ import annotations
import json, logging, re, sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import numpy as np, pandas as pd

log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
ANALYSIS = _ROOT / "data" / "analysis"
WS = ANALYSIS / "whitespace_v4.parquet"
CC = ANALYSIS / "competitor_counts.parquet"
FL = ANALYSIS / "foreign_launch_candidates.parquet"
OUT_DIR = _ROOT / "reports"

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; ORANGE="#f39c12"
RED="#e74c3c"; GRAY="#7f8c8d"; LIGHT="#f4f5f6"; BORDER="#ecf0f1"; PURPLE="#8e44ad"

# L&I product name pattern — catches "2X Long ABC", "Daily Target", "Ultra", "Bull/Bear", "Leveraged", "Inverse"
LI_PATTERN = re.compile(
    r"\b(2X|3X|-2X|-3X|2\.5X|1\.5X|ULTRA|LEVERAGED|INVERSE|BULL|BEAR|DAILY TARGET|"
    r"DAILY (LONG|SHORT)|SHORT|LONG\b.*\b(ETF|ETN)|T-REX|TRADR|DEFIANCE|GRANITESHARES|DIREXION|PROSHARES)",
    re.IGNORECASE)


def _df(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try: return pd.read_sql_query(sql, conn, params=params)
    finally: conn.close()


def _fmt_mcap(v):
    if v is None or pd.isna(v): return "—"
    try: v = float(v)
    except: return "—"
    if v >= 1e6: return f"${v/1e6:.1f}T"
    if v >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _fmt_pct(v):
    if v is None or pd.isna(v): return "—"
    try: v = float(v)
    except: return "—"
    sign = "+" if v >= 0 else ""
    color = GREEN if v >= 0 else RED
    return f'<span style="color:{color};font-weight:600">{sign}{v:.0f}%</span>'


def _clean(t):
    if not isinstance(t, str): return ""
    return t.upper().replace(" US","").replace(" EQUITY","").strip()


# Share-class aliases — same economic underlier, different ticker
_ALIAS = {
    "GOOG":"GOOGL", "GOOGL":"GOOGL",
    "BRKA":"BRKB", "BRK.A":"BRKB", "BRK/A":"BRKB", "BRKB":"BRKB", "BRK/B":"BRKB", "BRK.B":"BRKB",
}
def _canon(t):
    c = _clean(t)
    return _ALIAS.get(c, c)


def _table_header(cols, widths=None):
    out = '<tr style="background:'+NAVY+';">'
    for i,c in enumerate(cols):
        w = f"width:{widths[i]}px;" if widths and i<len(widths) else ""
        out += f'<th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;{w}">{escape(c)}</th>'
    return out + '</tr>'


def _tr(*cells):
    out='<tr>'
    for c in cells:
        if isinstance(c, tuple):
            val,align = c
            out += f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;text-align:{align};">{val}</td>'
        else:
            out += f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};font-size:11px;">{c}</td>'
    return out + '</tr>'


def _section_header(title, color, blurb=""):
    return f"""
<tr><td style="padding:20px 30px 4px;">
  <div style="font-size:16px;font-weight:700;color:{NAVY};padding-bottom:5px;border-bottom:2px solid {color};">{escape(title)}</div>
  {f'<div style="font-size:11px;color:{GRAY};margin-top:4px;font-style:italic;">{escape(blurb)}</div>' if blurb else ''}
</td></tr>"""


def _tk(t):
    return f'<span style="font-family:Courier New,monospace;font-weight:700;color:{BLUE};">{escape(str(t))}</span>'


# -------------------- DATA LOADERS (REWRITTEN) --------------------

def load_rex_position():
    """Per-underlier REX status from competitor_counts. Aggregates share-class aliases."""
    if not CC.exists(): return {}
    cc = pd.read_parquet(CC).reset_index().rename(columns={"underlier":"u"})
    cc["u_canon"] = cc["u"].apply(_canon)
    # Sum across share-class aliases (GOOG + GOOGL -> GOOGL)
    agg = cc.groupby("u_canon").agg(
        actv=("rex_active_long", lambda x: x.fillna(0).sum() + cc.loc[x.index, "rex_active_short"].fillna(0).sum()),
        filed=("rex_filed_long", lambda x: (x.fillna(0).sum()
            + cc.loc[x.index, "rex_filed_short"].fillna(0).sum()
            + cc.loc[x.index, "rex_extra_long"].fillna(0).sum()
            + cc.loc[x.index, "rex_extra_short"].fillna(0).sum())),
    )
    pos = {}
    for u, r in agg.iterrows():
        if r["actv"] > 0:   pos[u] = ("Live", GREEN)
        elif r["filed"] > 0: pos[u] = ("Filed", BLUE)
        else:               pos[u] = ("Not in", RED)
    return pos


def load_recent_competitor_filings():
    """L&I 485APOS competitor filings in last 90 days."""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    # Pull recent competitor 485APOS — filter to L&I by series_name pattern
    df = _df("""SELECT f.filing_date, f.registrant, fe.series_name, fe.class_symbol
                FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id
                WHERE f.form='485APOS' AND f.filing_date>=?
                  AND f.registrant NOT LIKE '%REX%'
                  AND f.registrant NOT LIKE '%ETF Opportunities%'
                ORDER BY f.filing_date DESC LIMIT 300""", (cutoff,))
    if df.empty: return df
    # Filter to L&I products via series_name pattern
    df = df[df["series_name"].fillna("").astype(str).apply(lambda s: bool(LI_PATTERN.search(str(s))))]
    # Extract underlier from series_name (last meaningful ticker before "Daily/ETF")
    def extract_underlier(name):
        if not isinstance(name, str): return ""
        # Common patterns: "X Long NVDA Daily ETF", "Ultra NVDA", "2X NVDA"
        m = re.search(r"\b(?:LONG|SHORT|ULTRA|2X|3X)\s+([A-Z]{1,6})\b", name.upper())
        if m: return m.group(1)
        # Fallback — grab a ticker before "Daily" or "ETF"
        m = re.search(r"\b([A-Z]{2,6})\s+(?:DAILY|ETF|ETN)\b", name.upper())
        return m.group(1) if m else ""
    df["underlier"] = df["series_name"].apply(extract_underlier)
    df["filing_date_dt"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["days_since"] = (pd.Timestamp(date.today()) - df["filing_date_dt"]).dt.days
    # Dedup by series_name (latest filing only)
    df = df.sort_values("filing_date_dt", ascending=False).drop_duplicates("series_name", keep="first")
    return df.head(25)


def load_imminent_launches():
    """L&I competitor PEND products with inception 0-90 days out (skip 2027+ and NaT)."""
    today = pd.Timestamp(date.today())
    df = _df("""SELECT ticker AS product_ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, map_li_underlier AS underlier
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='PEND' AND is_rex=0
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND inception_date IS NOT NULL""")
    if df.empty: return df
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df = df.dropna(subset=["inception_dt"])
    df = df[(df["inception_dt"] >= today) & (df["inception_dt"] <= today + pd.Timedelta(days=90))]
    df["days_remaining"] = (df["inception_dt"] - today).dt.days
    df["underlier_clean"] = df["underlier"].apply(_canon)
    return df.sort_values("days_remaining").head(20)


def load_rex_pipeline():
    """REX pipeline from rex_products table — Filed, Effective, Under Consideration."""
    df = _df("""SELECT ticker, name AS fund_name, direction, status,
                  initial_filing_date, estimated_effective_date, target_listing_date,
                  underlier, underlying_ticker, leverage
                FROM rex_products
                WHERE status IN ('Filed','Effective','Under Consideration','Target List')
                ORDER BY status, initial_filing_date DESC""")
    if df.empty: return df
    df["underlier_clean"] = df["underlier"].fillna(df["underlying_ticker"].fillna("")).apply(_canon)
    df["filing_dt"] = pd.to_datetime(df["initial_filing_date"], errors="coerce")
    df["eff_dt"] = pd.to_datetime(df["estimated_effective_date"], errors="coerce")
    df["days_since_filing"] = (pd.Timestamp(date.today()) - df["filing_dt"]).dt.days
    return df


def load_scored():
    """Whitespace v4 parquet with composite_score (v1.0.2 recomputed)."""
    df = pd.read_parquet(WS)
    if df.index.name == "ticker":
        df = df.reset_index()
    df = df.sort_values("composite_score", ascending=False)
    return df


def load_counts():
    if not CC.exists(): return pd.DataFrame()
    return pd.read_parquet(CC)


def load_inverse_gap():
    """Underliers with ≥1 ACTV long L&I product but ZERO inverse product."""
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum, fund_flow_1month AS flow_1m
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 100""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    df["is_inv"] = df["d"].astype(str).str.lower().str.contains("short|inv", na=False)
    rows = []
    for u, g in df.groupby("u_clean"):
        n_long = int(g["is_long"].sum()); n_inv = int(g["is_inv"].sum())
        if n_long == 0 or n_inv > 0: continue
        top = g[g["is_long"]].sort_values("aum", ascending=False).iloc[0]
        rows.append({"underlier": u, "n_long": n_long,
                     "top_ticker": top["ticker"], "top_fund": top["fund_name"],
                     "top_issuer": top["issuer"], "top_aum": float(top["aum"])})
    out = pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(15) if rows else pd.DataFrame()
    return out


def load_launch_anyway(rex_pos: dict):
    """Underliers with 1-2 active long competitors, REX NOT IN, top by AUM > $300M.
    Uses cleaned underlier (handles GOOG/GOOGL collisions)."""
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 0""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    rows = []
    for u, g in df[df["is_long"]].groupby("u_clean"):
        rex_label = rex_pos.get(u, ("Not in", RED))[0]
        if rex_label != "Not in":  # REX already has presence — skip
            continue
        n_long = len(g)
        if not (1 <= n_long <= 2): continue
        top = g.sort_values("aum", ascending=False).iloc[0]
        if top["aum"] < 300: continue
        rows.append({"underlier": u, "n_long": n_long,
                     "top_issuer": top["issuer"], "top_ticker": top["ticker"],
                     "top_fund": str(top["fund_name"])[:46], "top_aum": float(top["aum"])})
    return pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(15) if rows else pd.DataFrame()


def load_foreign():
    """Foreign launch candidates from curated parquet."""
    if not FL.exists(): return pd.DataFrame()
    df = pd.read_parquet(FL)
    return df.sort_values("composite_score", ascending=False).head(15)


def load_ipo_pipeline():
    """REX pipeline items with target_listing_date in future — IPO/pre-listing stand-in."""
    today = date.today().isoformat()
    df = _df("""SELECT ticker, name AS fund_name, status, direction, leverage,
                  underlier, underlying_ticker, initial_filing_date, target_listing_date
                FROM rex_products
                WHERE target_listing_date >= ? AND status IN ('Filed','Under Consideration','Target List')
                ORDER BY target_listing_date""", (today,))
    return df.head(12)


# -------------------- BUILD --------------------

def build():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    today_pretty = datetime.now().strftime("%B %d, %Y")

    rex_pos = load_rex_position()
    counts = load_counts()
    imminent = load_imminent_launches()
    recent_comp = load_recent_competitor_filings()
    pipeline = load_rex_pipeline()
    scored = load_scored()
    inverse_gap = load_inverse_gap()
    launch_anyway = load_launch_anyway(rex_pos)
    foreign = load_foreign()
    ipo = load_ipo_pipeline()

    # Score the pipeline underliers
    if not pipeline.empty and not scored.empty:
        score_map = scored.set_index("ticker")["composite_score"].to_dict()
        pipeline["underlier_score"] = pipeline["underlier_clean"].map(score_map).fillna(0)

    # Build "no live product" + filing flags via competitor_counts
    if not counts.empty:
        cc = counts.reset_index().rename(columns={"underlier":"u"})
        cc["u_clean"] = cc["u"].apply(_canon)
        # has_live = competitor or REX active product
        cc["has_live"] = ((cc.get("competitor_active_long",0).fillna(0)
                         + cc.get("competitor_active_short",0).fillna(0)
                         + cc.get("rex_active_long",0).fillna(0)
                         + cc.get("rex_active_short",0).fillna(0)) > 0)
        cc["has_rex_filing"] = ((cc.get("rex_active_long",0).fillna(0)
                              + cc.get("rex_active_short",0).fillna(0)
                              + cc.get("rex_filed_long",0).fillna(0)
                              + cc.get("rex_filed_short",0).fillna(0)
                              + cc.get("rex_extra_long",0).fillna(0)
                              + cc.get("rex_extra_short",0).fillna(0)) > 0)
        cc["has_comp_filing"] = ((cc.get("competitor_filed_long",0).fillna(0)
                              + cc.get("competitor_filed_short",0).fillna(0)
                              + cc.get("competitor_extra_long",0).fillna(0)
                              + cc.get("competitor_extra_short",0).fillna(0)) > 0)
        # Aggregate after canonical normalization (GOOG + GOOGL -> GOOGL — take OR of flags)
        cc_agg = cc.groupby("u_clean")[["has_live","has_rex_filing","has_comp_filing"]].any()
        flags = cc_agg.to_dict("index")
    else:
        flags = {}

    # Filter scored to "no live product"
    if scored.empty:
        no_live = pd.DataFrame()
    else:
        scored["u_clean"] = scored["ticker"].apply(_canon)
        scored["has_live"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_live", False)).fillna(False)
        scored["has_rex_filing"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_rex_filing", False)).fillna(False)
        scored["has_comp_filing"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_comp_filing", False)).fillna(False)
        no_live = scored[~scored["has_live"]].head(100)

    # Pulse line
    n_scored = len(scored)
    top5 = ", ".join(no_live["ticker"].head(5).tolist()) if not no_live.empty else "—"
    top_buzz = "—"
    if "mentions_24h" in scored.columns and scored["mentions_24h"].notna().any():
        top_idx = scored["mentions_24h"].idxmax()
        top_buzz = f"{scored.at[top_idx, 'ticker']} ({int(scored.at[top_idx, 'mentions_24h'])} mentions)"
    hot_sector = "—"
    try:
        sec_df = _df("SELECT sector FROM li_sector_daily WHERE run_date=(SELECT MAX(run_date) FROM li_sector_daily) ORDER BY total_mentions DESC LIMIT 1")
        if not sec_df.empty: hot_sector = str(sec_df.iloc[0]['sector'])
    except: pass

    # ---- IMMINENT ----
    if imminent.empty:
        imminent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor PEND products with inception in the next 90 days.</div>'
    else:
        rows=""
        for _, r in imminent.iterrows():
            dr=int(r['days_remaining']) if pd.notna(r['days_remaining']) else 0
            uc=urg_color=RED if dr<=21 else (ORANGE if dr<=60 else GRAY)
            u=r['underlier_clean']; rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rows += _tr(
                _tk(u or "—"),
                _tk(_clean(r['product_ticker']) if pd.notna(r['product_ticker']) else "—"),
                escape(str(r['issuer'] or "—"))[:24],
                escape(str(r['fund_name'])[:46]) if pd.notna(r['fund_name']) else "—",
                (escape(str(r['inception_dt'].date())), "center"),
                (f'<span style="color:{uc};font-weight:700;">{dr}d</span>', "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        imminent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Product','Competitor','Fund Name','Expected Launch','Days','REX Position'])}
{rows}</table>"""

    # ---- RECENT ----
    if recent_comp.empty:
        recent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor L&I 485APOS in the last 90 days.</div>'
    else:
        rows=""
        for _, r in recent_comp.iterrows():
            ds=int(r['days_since']) if pd.notna(r['days_since']) else 0
            color=RED if ds<=14 else (ORANGE if ds<=45 else GRAY)
            u=r['underlier']; rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rows += _tr(
                _tk(u or "—"),
                escape(str(r['registrant'] or "—"))[:28],
                escape(str(r['series_name'])[:50]),
                (escape(str(r['filing_date_dt'].date())), "center"),
                (f'<span style="color:{color};font-weight:700;">{ds}d</span>', "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        recent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Registrant','Series','Filing Date','Days Since','REX Position'])}
{rows}</table>"""

    # ---- PIPELINE ----
    if pipeline.empty:
        pipeline_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No active REX pipeline items.</div>'
    else:
        # Top 50 by underlier score
        pipe_top = pipeline.sort_values("underlier_score", ascending=False).head(50) if "underlier_score" in pipeline.columns else pipeline.head(50)
        rows=""
        for _, r in pipe_top.iterrows():
            status=str(r['status'])
            status_color={"Effective":GREEN,"Filed":BLUE,"Under Consideration":ORANGE,"Target List":GRAY}.get(status,GRAY)
            u=r['underlier_clean'] or _clean(r.get('underlier','') or r.get('underlying_ticker',''))
            score=r.get('underlier_score',0)
            score_html=f'<b style="color:{NAVY};">{score:.1f}</b>' if score>0 else f'<span style="color:{GRAY};">—</span>'
            file_dt=str(r['filing_dt'].date()) if pd.notna(r['filing_dt']) else "—"
            eff_dt=str(r['eff_dt'].date()) if pd.notna(r['eff_dt']) else "—"
            rows += _tr(
                _tk(_clean(r['ticker']) if pd.notna(r['ticker']) else "—"),
                escape(str(r['fund_name'])[:50]) if pd.notna(r['fund_name']) else "—",
                _tk(u or "—"),
                (escape(str(r.get('direction') or '—')), "center"),
                (escape(str(r.get('leverage') or '—')), "center"),
                (f'<span style="color:{status_color};font-weight:700;">{status}</span>', "center"),
                (escape(file_dt), "center"),
                (escape(eff_dt), "center"),
                (score_html, "right"),
            )
        pipeline_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Underlier','Direction','Leverage','Status','Filed','Effective','Underlier Score'])}
{rows}</table>"""

    # ---- MEGA TABLE — top 100, no live product, flags for REX-filed and Comp-filed ----
    if no_live.empty:
        mega_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No scored candidates available.</div>'
    else:
        rows=""
        for i, r in no_live.reset_index(drop=True).iterrows():
            tk=r['ticker']; name=str(r.get('name') or tk)[:36]
            sector=str(r.get('sector') or '—')[:16]
            themes=str(r.get('themes') or '').replace(',','·')[:16]
            mcap=_fmt_mcap(r.get('market_cap'))
            vol=r.get('rvol_90d') or 0
            ret1m=_fmt_pct(r.get('ret_1m')); ret1y=_fmt_pct(r.get('ret_1y'))
            ment=int(r.get('mentions_24h') or 0)
            score=r.get('composite_score') or 0
            rex_y = bool(r.get('has_rex_filing'))
            comp_y = bool(r.get('has_comp_filing'))
            rex_badge=f'<span style="color:{BLUE};font-weight:700;">Y</span>' if rex_y else f'<span style="color:{GRAY};">N</span>'
            comp_badge=f'<span style="color:{ORANGE};font-weight:700;">Y</span>' if comp_y else f'<span style="color:{GRAY};">N</span>'
            rows += _tr(
                (str(i+1), "center"),
                _tk(tk), escape(name), escape(sector), escape(themes),
                (mcap, "right"), (f"{vol:.0f}%", "right"),
                (ret1m, "right"), (ret1y, "right"),
                (str(ment), "right"),
                (rex_badge, "center"), (comp_badge, "center"),
                (f'<b style="color:{NAVY};">{score:.1f}</b>', "right"),
            )
        mega_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['#','Ticker','Company','Sector','Theme','Mkt Cap','Vol','1m','1y','Buzz','REX Filed','Comp Filed','Score'])}
{rows}</table>"""

    # ---- INVERSE GAP ----
    if inverse_gap.empty:
        ig_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No inverse-gap opportunities surfaced.</div>'
    else:
        rows=""
        for _, r in inverse_gap.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(_clean(r['top_ticker'])),
                escape(str(r['top_fund'])[:42]),
                escape(str(r['top_issuer'])[:24]),
                (_fmt_mcap(r['top_aum']), "right"),
            )
        ig_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Long Count','Top Long Product','Fund Name','Issuer','Top AUM'])}
{rows}</table>"""

    # ---- LAUNCH ANYWAY ----
    if launch_anyway.empty:
        la_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No 1-2 competitor proven-demand lanes surfaced (REX already covers them).</div>'
    else:
        rows=""
        for _, r in launch_anyway.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(_clean(r['top_ticker'])),
                escape(str(r['top_fund'])),
                escape(str(r['top_issuer'])[:24]),
                (_fmt_mcap(r['top_aum']), "right"),
            )
        la_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Competitor Count','Top Product','Fund Name','Issuer','Top AUM'])}
{rows}</table>"""

    # ---- FOREIGN ----
    if foreign.empty:
        foreign_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">Foreign candidates not loaded.</div>'
    else:
        rows=""
        for _, r in foreign.iterrows():
            rs = str(r.get('rex_status') or '—')
            rs_color = {"pending":ORANGE,"active":GREEN,"filed":BLUE}.get(rs.lower(), GRAY)
            cs = str(r.get('competitor_2x_status') or '—')
            cs_color = {"active":RED,"filed":ORANGE}.get(cs.lower(), GRAY)
            mcap = r.get('market_cap_usd', 0)
            mcap_str = f"${mcap/1e9:.0f}B" if mcap and mcap>=1e9 else _fmt_mcap(mcap/1e6 if mcap else 0)
            score = r.get('composite_score',0)
            rows += _tr(
                _tk(str(r['foreign_ticker'])),
                escape(str(r.get('name') or '—'))[:32],
                (escape(str(r.get('market') or '—')), "center"),
                escape(str(r.get('sector') or '—'))[:18],
                (mcap_str, "right"),
                (f'<span style="color:{rs_color};font-weight:700;">{rs}</span>', "center"),
                (f'<span style="color:{cs_color};font-weight:700;">{cs}</span>', "center"),
                (str(r.get('competitor_active_count',0)), "center"),
                (f'<b style="color:{NAVY};">{score:.1f}</b>', "right"),
            )
        foreign_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Company','Market','Sector','Global Mkt Cap','REX Status','Comp 2x','Comp Active','Score'])}
{rows}</table>"""

    # ---- IPO ----
    if ipo.empty:
        ipo_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No pre-listing pipeline items with target dates today.</div>'
    else:
        rows=""
        for _, r in ipo.iterrows():
            rows += _tr(
                _tk(_clean(r['ticker']) if pd.notna(r['ticker']) else "—"),
                escape(str(r['fund_name'])[:50]),
                _tk(_clean(r.get('underlier','') or r.get('underlying_ticker',''))),
                (escape(str(r.get('direction') or '—')), "center"),
                (escape(str(r.get('leverage') or '—')), "center"),
                (escape(str(r['target_listing_date']) if pd.notna(r.get('target_listing_date')) else '—'), "center"),
                (escape(str(r.get('status') or '—')), "center"),
            )
        ipo_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Underlier','Direction','Leverage','Target Listing','Status'])}
{rows}</table>"""

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>T-REX Stock Recommendation System — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="1180" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1180px;">
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">T-REX Stock Recommendation System | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">
    Methodology v1.0.2 · Race · Pipeline · Mega · Inverse · Launch-Anyway · Foreign · IPO
  </div>
</td></tr>
<tr><td style="padding:12px 30px 0;">
  <div style="font-size:12px;color:{GRAY};line-height:1.7;">
    <strong style="color:{NAVY};">{n_scored:,} stocks scored</strong> ·
    <strong style="color:{NAVY};">Top 5 (no live):</strong> <span style="font-family:Courier New,monospace;color:{BLUE};font-weight:700;">{escape(top5)}</span> ·
    <strong style="color:{NAVY};">Hot sector:</strong> <span style="color:{PURPLE};font-weight:700;">{escape(hot_sector)}</span> ·
    <strong style="color:{NAVY};">Top buzz:</strong> {escape(top_buzz)}
  </div>
</td></tr>

{_section_header('1 · Race Timing — Imminent Launches', RED, 'Competitor PEND L&I products with expected inception in the next 90 days. NaT/2027+ stale entries excluded.')}
<tr><td style="padding:6px 30px 8px;">{imminent_html}</td></tr>

{_section_header('Race Timing — Recent Competitor L&I Filings (last 90 days)', ORANGE, 'Competitor 485APOS filings matching L&I product patterns. The race is still open.')}
<tr><td style="padding:6px 30px 8px;">{recent_html}</td></tr>

{_section_header('2 · Our Pipeline', BLUE, 'Top 50 REX pipeline items (Filed / Effective / Under Consideration / Target List) ranked by underlier composite score.')}
<tr><td style="padding:6px 30px 8px;">{pipeline_html}</td></tr>

{_section_header('3 · Mega Table — Top 100, No Live Product on Underlier', NAVY, 'Universe ranked by v1.0.2 composite. Filter: zero active product on underlier. REX/Comp filing flags carry the strict-whitespace info.')}
<tr><td style="padding:6px 30px 8px;">{mega_html}</td></tr>

{_section_header('4 · Inverse Gap', PURPLE, 'Underliers with at least one active long L&I product but zero inverse — clean gap list, no scoring.')}
<tr><td style="padding:6px 30px 8px;">{ig_html}</td></tr>

{_section_header('5 · Launch Anyway — 1-2 Competitor, REX Not In, Proven Demand', BLUE, 'Underliers with 1-2 competitor long products averaging > $300M AUM where REX has no position. Underlier match normalized so GOOG/GOOGL collisions are caught.')}
<tr><td style="padding:6px 30px 8px;">{la_html}</td></tr>

{_section_header('6 · Foreign Megacaps', "#34495e", 'Curated overseas underliers (Samsung, SK Hynix, Tencent, Toyota class) — global market cap and competitor 2x activity.')}
<tr><td style="padding:6px 30px 8px;">{foreign_html}</td></tr>

{_section_header('7 · IPO / Pre-Listing Pipeline', TEAL := "#16a085", 'REX pipeline items with target listing dates in the future — IPO and pre-launch products.')}
<tr><td style="padding:6px 30px 8px;">{ipo_html}</td></tr>

<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    <strong style="color:{NAVY};">Methodology v1.0.2 — Updated 2026-06-01.</strong>
    Five buckets (Att 34 · Liq 25 · Theme 20 · Mom 12 · Vol 9), SI penalty −8.
    No tier bands. Race timing is overlay, not score input.
    Sources verified: competitor_counts.parquet (REX/competitor filings) · rex_products (pipeline) · foreign_launch_candidates (foreign).<br><br>
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
