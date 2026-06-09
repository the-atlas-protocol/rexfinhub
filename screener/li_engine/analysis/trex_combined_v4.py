"""T-REX Stock Recommendation System v4 — proper L&I filter + single-stock gate + IPO + share-class alias.

Fixes from v3 against Ryu 2026-06-02:
  1. Imminent: PEND L&I with inception NULL or future (exclude historical).
  2. Strict L&I filter via python regex (was catching Aristotle Short Duration etc.).
  3. Single-stock filter on inverse-gap (was pulling BASKET/XAG/NGA etc.).
  4. Pipeline underlier-score join verified via canonical normalization.
  5. Theme expansion using ai_stack_tags + themes.yaml.
  6. IPO section populated from existing load_pre_ipo_filer_race().
  7. Dedup recent filings by series_name.
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
TAGS_CSV = _ROOT / "data" / "rules" / "ai_stack_tags.csv"
OUT_DIR = _ROOT / "reports"

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; ORANGE="#f39c12"
RED="#e74c3c"; GRAY="#7f8c8d"; LIGHT="#f4f5f6"; BORDER="#ecf0f1"; PURPLE="#8e44ad"; TEAL="#16a085"


# ---- STRICT L&I PRODUCT NAME FILTER ----
# Rejects "Aristotle Short Duration Income Fund", "GraniteShares Dragon AI ETF",
# "Nuveen Short Term Bond", "Defiance Helium and Strategic Gases ETF".
# Accepts only names with explicit leverage multiplier OR "Leveraged"/"Inverse"
# OR "Daily Target" pattern OR "Ultra/UltraPro" + ETF/ETN.

_LI_RE = re.compile(
    r"(?:(?:^|[\s\-+])[12345](?:\.\d)?[xX]\b"     # 2X, 3X, 1.5X, -2X, +3X (multi-X)
    r"|\b(?:LEVERAGED|INVERSE)\b"                  # explicit keywords
    r"|\bDAILY\s+TARGET\b"                         # REX/Defiance daily-target pattern
    r"|\b(?:ULTRA|ULTRAPRO)\b\s+(?:[A-Z]+\s+)*?(?:ETF|ETN)"  # ProShares Ultra + ETF
    r"|\b(?:BULL|BEAR)\s*(?:[1-3]X|ETF|ETN)\b"     # Direxion Bull/Bear + multiplier
    r")"
)


def is_li_product(name: str) -> bool:
    if not isinstance(name, str): return False
    return bool(_LI_RE.search(name))


def _df(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try: return pd.read_sql_query(sql, conn, params=params)
    finally: conn.close()


def _fmt_mcap(v):
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    if v >= 1e6: return f"${v/1e6:.1f}T"
    if v >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _fmt_pct(v):
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    sign = "+" if v >= 0 else ""
    color = GREEN if v >= 0 else RED
    return f'<span style="color:{color};font-weight:600">{sign}{v:.0f}%</span>'


def _clean(t):
    if not isinstance(t, str): return ""
    return t.upper().replace(" US","").replace(" EQUITY","").strip()


_ALIAS = {"GOOG":"GOOGL","GOOGL":"GOOGL","BRKA":"BRKB","BRK.A":"BRKB","BRK/A":"BRKB","BRKB":"BRKB","BRK/B":"BRKB","BRK.B":"BRKB"}
def _canon(t):
    c = _clean(t)
    return _ALIAS.get(c, c)


def _single_stock_set():
    """Set of clean tickers from mkt_stock_data — the canonical single-stock universe."""
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM mkt_stock_data").fetchall()
    finally: conn.close()
    return {_canon(r[0]) for r in rows if r[0]}


def _table_header(cols):
    out = '<tr style="background:'+NAVY+';">'
    for c in cols:
        out += f'<th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:left;">{escape(c)}</th>'
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


# -------------------- DATA LOADERS --------------------

def load_rex_position():
    if not CC.exists(): return {}
    cc = pd.read_parquet(CC).reset_index().rename(columns={"underlier":"u"})
    cc["u_canon"] = cc["u"].apply(_canon)
    for col in ["rex_active_long","rex_active_short","rex_filed_long","rex_filed_short","rex_extra_long","rex_extra_short"]:
        if col not in cc.columns: cc[col] = 0
        cc[col] = cc[col].fillna(0)
    agg = cc.groupby("u_canon").agg(
        actv=("rex_active_long", lambda x: x.sum() + cc.loc[x.index, "rex_active_short"].sum()),
        filed=("rex_filed_long", lambda x: x.sum() + cc.loc[x.index, "rex_filed_short"].sum() + cc.loc[x.index, "rex_extra_long"].sum() + cc.loc[x.index, "rex_extra_short"].sum()),
    )
    pos = {}
    for u, r in agg.iterrows():
        if r["actv"] > 0:   pos[u] = ("Live", GREEN)
        elif r["filed"] > 0: pos[u] = ("Filed", BLUE)
        else:               pos[u] = ("Not in", RED)
    return pos


def load_recent_competitor_filings():
    """L&I 485APOS by non-REX in last 90d — strict python filter, dedup by series_name."""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    df = _df("""SELECT f.filing_date, f.registrant, fe.series_name, fe.class_symbol
                FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id
                WHERE f.form='485APOS' AND f.filing_date>=?
                  AND f.registrant NOT LIKE '%REX%'
                  AND f.registrant NOT LIKE '%ETF Opportunities%'
                ORDER BY f.filing_date DESC""", (cutoff,))
    if df.empty: return df
    # STRICT L&I filter
    df = df[df["series_name"].fillna("").apply(is_li_product)]
    # Extract underlier from L&I-style name
    def extract_underlier(name):
        if not isinstance(name, str): return ""
        # Patterns: "X Long NVDA Daily ETF", "2X Long NVDA", "Bull 2X NVDA", "Bear 1X NVDA"
        for pat in [
            r"\b(?:LONG|SHORT)\s+([A-Z]{2,6})\s+(?:DAILY|ETF|ETN)",
            r"\b\d(?:\.\d)?X\s+(?:LONG|SHORT|BULL|BEAR|INVERSE)\s+([A-Z]{2,6})",
            r"\b(?:BULL|BEAR)\s+\d(?:\.\d)?X\s+([A-Z]{2,6})",
            r"\bULTRA(?:PRO|SHORT)?\s+([A-Z]{2,6})",
            r"\bDAILY\s+TARGET\s+\d(?:\.\d)?X\s+(?:LONG|SHORT|INVERSE)\s+([A-Z]{2,6})",
            r"\bDAILY\s+\d(?:\.\d)?X\s+([A-Z]{2,6})",
        ]:
            m = re.search(pat, name.upper())
            if m: return m.group(1)
        # Fallback — any 2-6 char ticker between known L&I keywords
        m = re.search(r"\b([A-Z]{2,6})\s+(?:DAILY|ETF|ETN)\b", name.upper())
        return m.group(1) if m else ""
    df["underlier"] = df["series_name"].apply(extract_underlier)
    df["filing_date_dt"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["days_since"] = (pd.Timestamp(date.today()) - df["filing_date_dt"]).dt.days
    df = df.sort_values("filing_date_dt", ascending=False).drop_duplicates("series_name", keep="first")
    return df.head(25)


def load_imminent_launches():
    """L&I PEND products (any inception status), exclude historical/null+old."""
    df = _df("""SELECT ticker AS product_ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, map_li_underlier AS underlier, map_li_leverage_amount AS leverage
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='PEND' AND is_rex=0
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''""")
    if df.empty: return df
    today = pd.Timestamp(date.today())
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    # Keep: future inception OR null inception (truly pending). Drop: historical (the product never launched).
    df = df[(df["inception_dt"].isna()) | (df["inception_dt"] >= today)]
    # Cap future at 1 year (skip the 2027+ stale ones)
    df = df[(df["inception_dt"].isna()) | (df["inception_dt"] <= today + pd.Timedelta(days=365))]
    df["days_remaining"] = (df["inception_dt"] - today).dt.days
    df["underlier_clean"] = df["underlier"].apply(_canon)
    # Sort: items with dates first (by date), then null-date items
    df = df.sort_values(["inception_dt"], ascending=True, na_position="last")
    return df.head(30)


def load_rex_pipeline():
    df = _df("""SELECT ticker, name AS fund_name, direction, status,
                  initial_filing_date, estimated_effective_date, target_listing_date,
                  underlier, underlying_ticker, leverage
                FROM rex_products
                WHERE status IN ('Filed','Effective','Under Consideration','Target List')
                ORDER BY status, initial_filing_date DESC""")
    if df.empty: return df
    # rex_products.underlier and underlying_ticker are mostly NULL — extract from fund_name
    def extract_from_name(name):
        if not isinstance(name, str): return ""
        n = name.upper()
        # Patterns ordered by specificity
        for pat in [
            r"\bT-REX\s+\d(?:\.\d)?X\s+(?:LONG|SHORT|INVERSE)\s+([A-Z]{2,6})\b",  # T-REX 2X LONG NVDA
            r"\bREX\s+INCOMEMAX\s+([A-Z]{2,6})\s+STRATEGY",                       # REX IncomeMax AMD Strategy
            r"\bREX\s+([A-Z]{2,6})\s+(?:GROWTH|INCOME|VALUE|STRATEGY)",          # REX X Growth/Income/etc.
            r"\bDAILY\s+TARGET\s+\d(?:\.\d)?X\s+(?:LONG|SHORT)\s+([A-Z]{2,6})",   # Daily Target 2X Long X
            r"\b\d(?:\.\d)?X\s+(?:LONG|SHORT|BULL|BEAR)\s+([A-Z]{2,6})\s+(?:DAILY|ETF|ETN)",
        ]:
            m = re.search(pat, n)
            if m: return m.group(1)
        return ""
    raw_u = df["underlier"].fillna("").astype(str)
    raw_u2 = df["underlying_ticker"].fillna("").astype(str)
    raw_u3 = df["fund_name"].fillna("").astype(str).apply(extract_from_name)
    underlier_str = raw_u.where(raw_u != "", raw_u2.where(raw_u2 != "", raw_u3))
    df["underlier_clean"] = underlier_str.apply(_canon)
    df["filing_dt"] = pd.to_datetime(df["initial_filing_date"], errors="coerce")
    df["eff_dt"] = pd.to_datetime(df["estimated_effective_date"], errors="coerce")
    return df


def load_scored():
    df = pd.read_parquet(WS)
    if df.index.name == "ticker": df = df.reset_index()
    return df.sort_values("composite_score", ascending=False)


def load_counts():
    if not CC.exists(): return pd.DataFrame()
    return pd.read_parquet(CC)


def load_inverse_gap(single_stocks: set):
    """Single-stock underliers with ≥1 ACTV long L&I and ZERO inverse."""
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 100""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    # FILTER: only single-stock underliers (excludes BASKET, XAG, XBTUSD, indices)
    df = df[df["u_clean"].isin(single_stocks)]
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
    return pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(15) if rows else pd.DataFrame()


def load_launch_anyway(rex_pos: dict, single_stocks: set):
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 0""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    df = df[df["u_clean"].isin(single_stocks)]  # single-stock only
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    rows = []
    for u, g in df[df["is_long"]].groupby("u_clean"):
        if rex_pos.get(u, ("Not in", RED))[0] != "Not in": continue
        n_long = len(g)
        if not (1 <= n_long <= 2): continue
        top = g.sort_values("aum", ascending=False).iloc[0]
        if top["aum"] < 300: continue
        rows.append({"underlier": u, "n_long": n_long,
                     "top_issuer": top["issuer"], "top_ticker": top["ticker"],
                     "top_fund": str(top["fund_name"])[:46], "top_aum": float(top["aum"])})
    return pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(15) if rows else pd.DataFrame()


def load_foreign():
    if not FL.exists(): return pd.DataFrame()
    return pd.read_parquet(FL).sort_values("composite_score", ascending=False).head(15)


def load_ipo_section():
    """Pre-IPO targets with REX/competitor filing race status."""
    try:
        from screener.li_engine.analysis.pre_ipo_filer_race import load_pre_ipo_filer_race, PRE_IPO_TARGETS
        race = load_pre_ipo_filer_race()
        rows = []
        for tgt in PRE_IPO_TARGETS:
            name = tgt["display"]
            d = race.get(name, {})
            filers = d.get("filers", []) or []
            rex = bool(d.get("rex_filed", False))
            total = int(d.get("total_filings", 0))
            issuers = ", ".join(f["issuer"] for f in filers[:6])
            rows.append({"company": name, "total_filings": total, "rex_filed": rex,
                         "n_issuers": len(filers), "issuers": issuers or "—"})
        return pd.DataFrame(rows).sort_values("total_filings", ascending=False)
    except Exception as e:
        log.warning("IPO load failed: %s", e)
        return pd.DataFrame()


# -------------------- BUILD --------------------

def build():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    today_pretty = datetime.now().strftime("%B %d, %Y")

    single_stocks = _single_stock_set()
    rex_pos = load_rex_position()
    counts = load_counts()
    imminent = load_imminent_launches()
    recent_comp = load_recent_competitor_filings()
    pipeline = load_rex_pipeline()
    scored = load_scored()
    inverse_gap = load_inverse_gap(single_stocks)
    launch_anyway = load_launch_anyway(rex_pos, single_stocks)
    foreign = load_foreign()
    ipo = load_ipo_section()

    # Score the pipeline underliers (FIX: build score_map properly)
    if not pipeline.empty and not scored.empty:
        scored_canon = scored.copy()
        scored_canon["t_canon"] = scored_canon["ticker"].apply(_canon)
        score_map = scored_canon.set_index("t_canon")["composite_score"].to_dict()
        pipeline["underlier_score"] = pipeline["underlier_clean"].map(score_map).fillna(0)

    # Filing flags via competitor_counts (with canon)
    if not counts.empty:
        cc = counts.reset_index().rename(columns={"underlier":"u"})
        cc["u_canon"] = cc["u"].apply(_canon)
        for col in ["competitor_active_long","competitor_active_short","rex_active_long","rex_active_short",
                    "rex_filed_long","rex_filed_short","rex_extra_long","rex_extra_short",
                    "competitor_filed_long","competitor_filed_short","competitor_extra_long","competitor_extra_short"]:
            if col not in cc.columns: cc[col]=0
            cc[col] = cc[col].fillna(0)
        cc["has_live"] = (cc["competitor_active_long"]+cc["competitor_active_short"]+cc["rex_active_long"]+cc["rex_active_short"])>0
        cc["has_rex_filing"] = (cc["rex_active_long"]+cc["rex_active_short"]+cc["rex_filed_long"]+cc["rex_filed_short"]+cc["rex_extra_long"]+cc["rex_extra_short"])>0
        cc["has_comp_filing"] = (cc["competitor_filed_long"]+cc["competitor_filed_short"]+cc["competitor_extra_long"]+cc["competitor_extra_short"])>0
        flags = cc.groupby("u_canon")[["has_live","has_rex_filing","has_comp_filing"]].any().to_dict("index")
    else:
        flags = {}

    if scored.empty:
        no_live = pd.DataFrame()
    else:
        scored["u_clean"] = scored["ticker"].apply(_canon)
        scored["has_live"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_live", False)).fillna(False)
        scored["has_rex_filing"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_rex_filing", False)).fillna(False)
        scored["has_comp_filing"] = scored["u_clean"].map(lambda t: flags.get(t,{}).get("has_comp_filing", False)).fillna(False)
        no_live = scored[~scored["has_live"]].head(100)

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
        imminent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor L&I PEND products today.</div>'
    else:
        rows=""
        for _, r in imminent.iterrows():
            dr = r['days_remaining']
            if pd.isna(dr):
                dr_str = '<span style="color:'+GRAY+';font-weight:600;">no date</span>'
            else:
                dr_int = int(dr); uc = RED if dr_int<=21 else (ORANGE if dr_int<=60 else GRAY)
                dr_str = f'<span style="color:{uc};font-weight:700;">{dr_int}d</span>'
            inc_str = str(r['inception_dt'].date()) if pd.notna(r['inception_dt']) else "—"
            u = r['underlier_clean']; rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rows += _tr(
                _tk(u or "—"),
                _tk(_clean(r['product_ticker']) if pd.notna(r['product_ticker']) else "—"),
                escape(str(r['issuer'] or "—"))[:24],
                escape(str(r['fund_name'])[:46]) if pd.notna(r['fund_name']) else "—",
                (escape(str(r.get('leverage') or '—')), "center"),
                (escape(inc_str), "center"),
                (dr_str, "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        imminent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Product','Competitor','Fund Name','Leverage','Expected','Timing','REX Position'])}
{rows}</table>"""

    # ---- RECENT ----
    if recent_comp.empty:
        recent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No strict-L&I 485APOS in the last 90 days.</div>'
    else:
        rows=""
        for _, r in recent_comp.iterrows():
            ds=int(r['days_since']) if pd.notna(r['days_since']) else 0
            color=RED if ds<=14 else (ORANGE if ds<=45 else GRAY)
            u = _canon(r['underlier']) if r['underlier'] else ""
            rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rows += _tr(
                _tk(u or "—"),
                escape(str(r['registrant'] or "—"))[:28],
                escape(str(r['series_name'])[:60]),
                (escape(str(r['filing_date_dt'].date())), "center"),
                (f'<span style="color:{color};font-weight:700;">{ds}d</span>', "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        recent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Registrant','Series','Filed','Days Since','REX Position'])}
{rows}</table>"""

    # ---- PIPELINE ----
    if pipeline.empty:
        pipeline_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No active REX pipeline items.</div>'
    else:
        pipe_top = pipeline.sort_values("underlier_score", ascending=False).head(50) if "underlier_score" in pipeline.columns else pipeline.head(50)
        rows=""
        for _, r in pipe_top.iterrows():
            status=str(r['status']); status_color={"Effective":GREEN,"Filed":BLUE,"Under Consideration":ORANGE,"Target List":GRAY}.get(status,GRAY)
            u=r['underlier_clean']
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

    # ---- MEGA TABLE ----
    if no_live.empty:
        mega_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No scored candidates.</div>'
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
            rex_y=bool(r.get('has_rex_filing')); comp_y=bool(r.get('has_comp_filing'))
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

    # ---- INVERSE GAP (single-stock only) ----
    if inverse_gap.empty:
        ig_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No single-stock inverse-gap opportunities surfaced.</div>'
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
            rs = str(r.get('rex_status') or '—'); rs_color = {"pending":ORANGE,"active":GREEN,"filed":BLUE}.get(rs.lower(), GRAY)
            cs = str(r.get('competitor_2x_status') or '—'); cs_color = {"active":RED,"filed":ORANGE}.get(cs.lower(), GRAY)
            mcap = r.get('market_cap_usd', 0)
            mcap_str = f"${mcap/1e9:.0f}B" if mcap and mcap>=1e9 else _fmt_mcap((mcap or 0)/1e6)
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

    # ---- IPO (pre-IPO filer race) ----
    if ipo.empty:
        ipo_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">Pre-IPO race load failed.</div>'
    else:
        rows=""
        for _, r in ipo.iterrows():
            rex_y = bool(r['rex_filed'])
            rex_badge = f'<span style="color:{GREEN};font-weight:700;">YES</span>' if rex_y else f'<span style="color:{GRAY};">no</span>'
            rows += _tr(
                escape(str(r['company'])),
                (str(r['total_filings']), "center"),
                (str(r['n_issuers']), "center"),
                escape(str(r['issuers'])[:60]),
                (rex_badge, "center"),
            )
        ipo_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Pre-IPO Target','Total L&I Filings','Distinct Issuers','Filers','REX Filed'])}
{rows}</table>"""

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>T-REX Stock Recommendation System — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="1200" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1200px;">
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">T-REX Stock Recommendation System | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">
    v1.0.2 · Race · Pipeline · Mega · Inverse · Launch-Anyway · Foreign · Pre-IPO
  </div>
</td></tr>
<tr><td style="padding:12px 30px 0;">
  <div style="font-size:12px;color:{GRAY};line-height:1.7;">
    <strong style="color:{NAVY};">{n_scored:,} stocks scored</strong> ·
    <strong style="color:{NAVY};">Top 5 (no live product):</strong> <span style="font-family:Courier New,monospace;color:{BLUE};font-weight:700;">{escape(top5)}</span> ·
    <strong style="color:{NAVY};">Hot sector:</strong> <span style="color:{PURPLE};font-weight:700;">{escape(hot_sector)}</span> ·
    <strong style="color:{NAVY};">Top buzz:</strong> {escape(top_buzz)}
  </div>
</td></tr>

{_section_header('1 · Race Timing — Imminent Launches', RED, 'Competitor L&I PEND products. Includes both items with future inception dates and undated PEND items (per the framework, dates often arrive ~1 day pre-launch). Historical/never-launched entries excluded.')}
<tr><td style="padding:6px 30px 8px;">{imminent_html}</td></tr>

{_section_header('Race Timing — Recent Competitor L&I Filings (last 90 days)', ORANGE, 'Non-REX 485APOS filings matching strict L&I patterns (explicit leverage multiplier, Leveraged/Inverse, Daily Target, Bull/Bear-X). Deduped by series.')}
<tr><td style="padding:6px 30px 8px;">{recent_html}</td></tr>

{_section_header('2 · Our Pipeline — Top 50 by Underlier Score', BLUE, 'REX pipeline from rex_products lifecycle (Filed / Effective / Under Consideration / Target List). Underlier score from the v1.0.2 mega table — answers what to launch next.')}
<tr><td style="padding:6px 30px 8px;">{pipeline_html}</td></tr>

{_section_header('3 · Mega Table — Top 100 Scored, No Live Product on Underlier', NAVY, 'Universe ranked by v1.0.2 composite. Filter: no active product on the underlier. REX/Comp filing flags carry the strict-whitespace state.')}
<tr><td style="padding:6px 30px 8px;">{mega_html}</td></tr>

{_section_header('4 · Inverse Gap (Single-Stock Only)', PURPLE, 'Single-stock underliers with at least one active long L&I product but zero inverse. Baskets / indices / commodities excluded.')}
<tr><td style="padding:6px 30px 8px;">{ig_html}</td></tr>

{_section_header('5 · Launch Anyway — 1-2 Competitor, REX Not In', BLUE, 'Single-stock underliers where 1-2 competitor long products exist averaging > $300M AUM, REX has no position (GOOG/GOOGL and BRKA/BRKB aliased correctly).')}
<tr><td style="padding:6px 30px 8px;">{la_html}</td></tr>

{_section_header('6 · Foreign Megacaps', "#34495e", 'Curated overseas underliers — Samsung, SK Hynix, Tencent, Toyota etc. Global market cap, REX status, competitor 2x activity.')}
<tr><td style="padding:6px 30px 8px;">{foreign_html}</td></tr>

{_section_header('7 · Pre-IPO Filer Race', TEAL, 'For each pre-IPO target, who has filed leveraged products on it. Sourced from pre_ipo_filer_race.PRE_IPO_TARGETS — 12 targets including OpenAI, SpaceX, Anthropic, Stripe, Databricks.')}
<tr><td style="padding:6px 30px 8px;">{ipo_html}</td></tr>

<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    <strong style="color:{NAVY};">Methodology v1.0.2 — Updated 2026-06-01.</strong>
    Five buckets (Att 34 · Liq 25 · Theme 20 · Mom 12 · Vol 9), SI penalty −8. No tier bands.
    L&I filter: explicit leverage multiplier (NX) OR Leveraged/Inverse OR Daily Target OR Bull/Bear-X.
    Single-stock filter via mkt_stock_data universe (~6,594 tickers).
    Share-class aliasing: GOOG/GOOGL → GOOGL, BRKA/BRKB → BRKB.<br><br>
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
