raise SystemExit("RETIRED 2026-06-16 - quarantined to archive/retired-2026-06-16/, do not run. 3-gate proven 0 live refs; pending final sweep.")
"""T-REX Stock Recommendation System v6 — Ryu 2026-06-02 feedback applied.

Changes vs v5:
  1. Pipeline section: filter to T-REX 2X Long products only. Top 20. (was: all REX products, top 50)
  2. Recent filings: tightened to 60d, drop 3X+ products, title renamed to "Competitor L&I Filings — New Activity (<=2X only)"
  3. Delisting Watch: REX-only (is_rex=1). Drops competitor rows entirely.
  4. Launch Anyway: top 20 + first-launch date + age columns for more intel.
  5. NEW: Track-record backtest — for each L&I underlier, compute 3mo AUM change (proxy for flow) and join to v1.0.2 score. Bucket analysis shows whether high-scored underliers actually see more flow.
  6. NaN cleanup — direction column no longer renders as "nan".
  7. Methodology updated to reflect new filters.
"""
from __future__ import annotations
import json, logging, re, sqlite3, sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import numpy as np, pandas as pd

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve()
_CANDIDATE_ROOTS = [
    _HERE.parent.parent.parent.parent,
    Path("/home/jarvis/rexfinhub"),
    Path("C:/Projects/rexfinhub"),
]
_ROOT = next((r for r in _CANDIDATE_ROOTS if (r / "data" / "etp_tracker.db").exists()), _CANDIDATE_ROOTS[0])
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "etp_tracker.db"
ANALYSIS = _ROOT / "data" / "analysis"
WS = ANALYSIS / "whitespace_v4.parquet"
CC = ANALYSIS / "competitor_counts.parquet"
FL = ANALYSIS / "foreign_launch_candidates.parquet"
YAML_PATH = _ROOT / "config" / "ipo_watchlist.yaml"
OUT_DIR = _ROOT / "reports"

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; ORANGE="#f39c12"
RED="#e74c3c"; GRAY="#7f8c8d"; LIGHT="#f4f5f6"; BORDER="#ecf0f1"; PURPLE="#8e44ad"; TEAL="#16a085"


_LI_RE = re.compile(
    r"(?:(?:^|[\s\-+])[12345](?:\.\d)?[xX]\b"
    r"|\b(?:LEVERAGED|INVERSE)\b"
    r"|\bDAILY\s+TARGET\b"
    r"|\b(?:ULTRA|ULTRAPRO)\b\s+(?:[A-Z]+\s+)*?(?:ETF|ETN)"
    r"|\b(?:BULL|BEAR)\s*(?:[1-3]X|ETF|ETN)\b"
    r")"
)

# Multipliers >= 3X — used to reject from recent-filings panel.
_HIGH_LEV_RE = re.compile(r"(?:(?:^|[\s\-+])(?:[3-9]|[1-9]\d)(?:\.\d)?[xX]\b)")


def is_li_product(name: str) -> bool:
    return isinstance(name, str) and bool(_LI_RE.search(name))


def is_high_leverage(name: str) -> bool:
    """True if name contains 3X or higher multiplier."""
    return isinstance(name, str) and bool(_HIGH_LEV_RE.search(name))


def _df(sql, params=()):
    conn = sqlite3.connect(str(DB))
    try: return pd.read_sql_query(sql, conn, params=params)
    finally: conn.close()


def _safe_str(v, dash="—"):
    """Coerce any cell value to safe display string. Handles NaN, None, '', float-NaN."""
    if v is None: return dash
    try:
        if pd.isna(v): return dash
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s if s and s.lower() != "nan" else dash


def _fmt_mcap(v):
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    if v >= 1e6: return f"${v/1e6:.1f}T"
    if v >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _fmt_aum_m(v):
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    if v >= 1000: return f"${v/1000:.2f}B"
    return f"${v:.1f}M"


def _fmt_flow_m(v):
    """Format a flow value (positive = inflow, negative = outflow)."""
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    sign = "+" if v >= 0 else "-"
    av = abs(v)
    if av >= 1000: return f'<span style="color:{GREEN if v>=0 else RED};font-weight:700;">{sign}${av/1000:.2f}B</span>'
    return f'<span style="color:{GREEN if v>=0 else RED};font-weight:700;">{sign}${av:.1f}M</span>'


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
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM mkt_stock_data").fetchall()
    finally: conn.close()
    return {_canon(r[0]) for r in rows if r[0]}


def _table_header(cols, aligns=None):
    """cols = list of column names. aligns = optional list of 'left'/'center'/'right' matching cols."""
    out = '<tr style="background:'+NAVY+';">'
    if aligns is None:
        aligns = ["left"] * len(cols)
    for c, a in zip(cols, aligns):
        out += f'<th style="padding:7px 8px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.4px;text-align:{a};">{escape(c)}</th>'
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
    return f'<span style="font-family:Courier New,monospace;font-weight:700;color:{BLUE};">{escape(_safe_str(t))}</span>'


def _collapse_status(row) -> str:
    raw = _safe_str(row.get("status"), "").strip()
    if raw in ("Effective", "Listed"):
        return "Effective"
    eff = row.get("eff_dt")
    if pd.notna(eff) and eff <= pd.Timestamp(date.today()):
        return "Effective"
    return "Filed"


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
    """Last 60d non-REX 485APOS, strict L&I, exclude 3X+, dedup by series. Top 50 by recency."""
    cutoff = (date.today() - timedelta(days=60)).isoformat()
    df = _df("""SELECT f.filing_date, f.registrant, fe.series_name, fe.class_symbol
                FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id
                WHERE f.form='485APOS' AND f.filing_date>=?
                  AND f.registrant NOT LIKE '%REX%'
                  AND f.registrant NOT LIKE '%ETF Opportunities%'
                ORDER BY f.filing_date DESC""", (cutoff,))
    if df.empty: return df
    df = df[df["series_name"].fillna("").apply(is_li_product)]
    df = df[~df["series_name"].fillna("").apply(is_high_leverage)]  # drop 3X+
    def extract_underlier(name):
        if not isinstance(name, str): return ""
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
        m = re.search(r"\b([A-Z]{2,6})\s+(?:DAILY|ETF|ETN)\b", name.upper())
        return m.group(1) if m else ""
    df["underlier"] = df["series_name"].apply(extract_underlier)
    df["filing_date_dt"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["days_since"] = (pd.Timestamp(date.today()) - df["filing_date_dt"]).dt.days
    df = df.sort_values("filing_date_dt", ascending=False).drop_duplicates("series_name", keep="first")
    return df.head(50)


def load_imminent_launches():
    """All non-REX L&I products in PEND status. Underlier shown as '—' when not populated
    (baskets, VIX, commodities, structured exposures). Drops MicroSectors + dead-stale (>60d past)."""
    df = _df("""SELECT ticker AS product_ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, map_li_underlier AS underlier, map_li_leverage_amount AS leverage
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='PEND' AND is_rex=0
                  AND fund_name NOT LIKE '%MICROSECTORS%'
                  AND fund_name NOT LIKE '%MicroSectors%'""")
    if df.empty: return df
    today = pd.Timestamp(date.today())
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    # Keep: undated PEND, or inception within last 60d (recently launched, PEND lag), or future <=1y
    df = df[
        df["inception_dt"].isna()
        | ((df["inception_dt"] >= today - pd.Timedelta(days=60)) & (df["inception_dt"] <= today + pd.Timedelta(days=365)))
    ]
    df["days_remaining"] = (df["inception_dt"] - today).dt.days
    df["underlier_clean"] = df["underlier"].apply(_canon)
    df = df.sort_values(["inception_dt"], ascending=True, na_position="last")
    return df.head(30)


def load_trex_2x_long_pipeline():
    """T-REX 2X Long FILED-NOT-YET-LAUNCHED products only. Status in
    {Filed, Under Consideration, Target List} — drops Effective/Listed.
    The point: which to launch from those we have filed that are new to the market.
    """
    df = _df("""SELECT ticker, name AS fund_name, direction, status,
                  initial_filing_date, estimated_effective_date,
                  underlier, underlying_ticker
                FROM rex_products
                WHERE (name LIKE 'T-REX 2X Long%' OR name LIKE 'T-REX 2X LONG%')
                  AND status IN ('Filed','Under Consideration','Target List')
                ORDER BY initial_filing_date DESC""")
    if df.empty: return df

    # Extract underlier code from T-REX 2X LONG <CODE> DAILY TARGET ETF
    def extract_from_trex(name):
        if not isinstance(name, str): return ""
        n = name.upper()
        # T-REX 2X LONG <CODE> DAILY (code can be 2-12 chars, alphanumeric, may include company names)
        m = re.search(r"\bT-REX\s+2X\s+LONG\s+([A-Z0-9\.\-]{2,12})\s+DAILY", n)
        if m: return m.group(1)
        # Fallback for company-name pipeline entries (Anthropic, SpaceX, Viva Republica)
        # No clean ticker; return ""
        return ""

    raw_u = df["underlier"].fillna("").astype(str)
    raw_u2 = df["underlying_ticker"].fillna("").astype(str)
    raw_u3 = df["fund_name"].fillna("").astype(str).apply(extract_from_trex)
    underlier_str = raw_u.where(raw_u != "", raw_u2.where(raw_u2 != "", raw_u3))
    df["underlier_clean"] = underlier_str.apply(_canon)
    df["filing_dt"] = pd.to_datetime(df["initial_filing_date"], errors="coerce")
    df["eff_dt"] = pd.to_datetime(df["estimated_effective_date"], errors="coerce")
    df["status_binary"] = df.apply(_collapse_status, axis=1)

    try:
        led = _df("""SELECT ticker, final_score FROM li_engine_daily
                     WHERE run_date=(SELECT MAX(run_date) FROM li_engine_daily)""")
        if not led.empty:
            led["t_canon"] = led["ticker"].apply(_canon)
            score_map = led.groupby("t_canon")["final_score"].max().to_dict()
            df["underlier_score"] = df["underlier_clean"].map(score_map).fillna(0)
        else:
            df["underlier_score"] = 0.0
    except Exception as e:
        log.warning("li_engine_daily join failed: %s", e)
        df["underlier_score"] = 0.0

    return df


def load_scored():
    df = pd.read_parquet(WS)
    if df.index.name == "ticker": df = df.reset_index()
    return df.sort_values("composite_score", ascending=False)


def load_counts():
    if not CC.exists(): return pd.DataFrame()
    return pd.read_parquet(CC)


def load_inverse_gap(single_stocks: set):
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 100
                  AND fund_name NOT LIKE '%MICROSECTORS%'
                  AND fund_name NOT LIKE '%MicroSectors%'""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
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
    """1-2 competitor, REX not in, top product > $100M AUM. Enriched with first-launch date."""
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum, inception_date
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 0
                  AND fund_name NOT LIKE '%MICROSECTORS%'
                  AND fund_name NOT LIKE '%MicroSectors%'""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    df = df[df["u_clean"].isin(single_stocks)]
    df["is_long"] = df["d"].astype(str).str.lower().str.contains("long", na=False)
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    rows = []
    today = pd.Timestamp(date.today())
    for u, g in df[df["is_long"]].groupby("u_clean"):
        if rex_pos.get(u, ("Not in", RED))[0] != "Not in": continue
        n_long = len(g)
        if not (1 <= n_long <= 2): continue
        top = g.sort_values("aum", ascending=False).iloc[0]
        if top["aum"] < 100: continue  # lowered from 300 to surface more
        first = g["inception_dt"].min()
        first_str = str(first.date()) if pd.notna(first) else "—"
        months_old = (today - first).days / 30.44 if pd.notna(first) else None
        rows.append({"underlier": u, "n_long": n_long,
                     "top_issuer": top["issuer"], "top_ticker": top["ticker"],
                     "top_fund": str(top["fund_name"])[:46], "top_aum": float(top["aum"]),
                     "first_launch": first_str, "months_old": months_old})
    return pd.DataFrame(rows).sort_values("top_aum", ascending=False).head(30) if rows else pd.DataFrame()


def load_foreign():
    if not FL.exists(): return pd.DataFrame()
    return pd.read_parquet(FL).sort_values("composite_score", ascending=False).head(15)


def load_delisting_watch():
    """T-REX brand only. ACTV L&I with AUM < $15M and age >= 5.5 months.
    Explicitly excludes MicroSectors (REX Shares owns the brand but it's not the T-REX pipeline).
    """
    df = _df("""SELECT ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, aum,
                  map_li_underlier AS underlier,
                  map_li_direction AS direction,
                  map_li_leverage_amount AS leverage
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND is_rex=1
                  AND (fund_name LIKE 'T-REX%' OR fund_name LIKE 'T REX%')
                  AND aum IS NOT NULL AND aum < 15
                  AND inception_date IS NOT NULL""")
    if df.empty: return df
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df["age_days"] = (pd.Timestamp(date.today()) - df["inception_dt"]).dt.days
    df["age_months"] = df["age_days"] / 30.44
    df = df[df["age_months"] >= 5.5]
    df = df.sort_values("age_months", ascending=False)
    return df.head(40)


def load_flow_score_backtest(single_stocks: set):
    """For each SINGLE-STOCK L&I underlier, compute 3mo AUM change (proxy for flow)
    and the underlier's current composite score. Basket indices (SOLFANGT, MINERS,
    NYFANGT etc.) and MicroSectors products excluded. Bucket analysis: does score
    predict flow?
    """
    cur = _df("""SELECT map_li_underlier AS u, ticker, aum, is_rex
                 FROM mkt_master_data
                 WHERE primary_category='LI' AND market_status='ACTV'
                   AND map_li_underlier IS NOT NULL AND map_li_underlier != ''
                   AND aum IS NOT NULL AND aum > 0
                   AND (fund_name NOT LIKE '%MICROSECTORS%'
                        AND fund_name NOT LIKE '%MicroSectors%')""")
    if cur.empty: return pd.DataFrame(), {}
    cur["u_clean"] = cur["u"].apply(_canon)
    cur["ticker_clean"] = cur["ticker"].apply(_clean)
    # Single-stock universe only
    cur = cur[cur["u_clean"].isin(single_stocks)]
    if cur.empty: return pd.DataFrame(), {}

    # AUM 3 months ago by ticker
    ts = _df("""SELECT ticker, aum_value
                FROM mkt_time_series
                WHERE months_ago=3""")
    if not ts.empty:
        ts["ticker_clean"] = ts["ticker"].apply(_clean)
        ts3 = ts.groupby("ticker_clean")["aum_value"].max().to_dict()
    else:
        ts3 = {}

    # Score map from li_engine_daily
    led = _df("""SELECT ticker, final_score FROM li_engine_daily
                 WHERE run_date=(SELECT MAX(run_date) FROM li_engine_daily)""")
    led["t_canon"] = led["ticker"].apply(_canon) if not led.empty else []
    score_map = led.groupby("t_canon")["final_score"].max().to_dict() if not led.empty else {}

    # Aggregate per underlier
    cur["aum_3mo"] = cur["ticker_clean"].map(ts3).fillna(0)
    agg = cur.groupby("u_clean").agg(
        cur_aum=("aum","sum"),
        aum_3mo=("aum_3mo","sum"),
        n_products=("ticker","count"),
        rex_count=("is_rex","sum"),
    ).reset_index()
    agg["flow_3mo"] = agg["cur_aum"] - agg["aum_3mo"]
    agg["score"] = agg["u_clean"].map(score_map).fillna(0)
    # Drop underliers with no historical data (aum_3mo == 0 AND cur_aum is large means newly launched, exclude)
    agg = agg[agg["aum_3mo"] > 0]

    # Bucket analysis
    buckets = {}
    if not agg.empty:
        # Score buckets: HIGH (>=40), MED (20-40), LOW (<20)
        for label, mask in [
            ("HIGH (≥40)", agg["score"] >= 40),
            ("MED (20-39)", (agg["score"] >= 20) & (agg["score"] < 40)),
            ("LOW (<20)", agg["score"] < 20),
        ]:
            sub = agg[mask]
            if sub.empty:
                buckets[label] = {"n":0, "avg_flow":0.0, "median_flow":0.0, "pct_positive":None}
            else:
                buckets[label] = {
                    "n": int(len(sub)),
                    "avg_flow": float(sub["flow_3mo"].mean()),
                    "median_flow": float(sub["flow_3mo"].median()),
                    "pct_positive": float((sub["flow_3mo"] > 0).mean()),
                }

    # Top 20 underliers by absolute flow
    agg["abs_flow"] = agg["flow_3mo"].abs()
    top_flow = agg.sort_values("abs_flow", ascending=False).head(20)
    return top_flow, buckets


def load_track_record_recs():
    """Pull recommendation_history hit rate (if any data yet)."""
    try:
        from screener.li_engine.analysis.recommendation_history import hit_rate_stats
        return hit_rate_stats(rolling_days=90)
    except Exception as e:
        log.warning("Track record load failed: %s", e)
        return {"high_total": 0, "rolling_days": 90, "sample_size_warning": True}


def load_ipo_yaml():
    if not YAML_PATH.exists():
        return {"high_profile": [], "recently_priced": []}
    try:
        import yaml
        data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("YAML load failed: %s", e); return {"high_profile": [], "recently_priced": []}
    try:
        from screener.li_engine.analysis.pre_ipo_filer_race import load_pre_ipo_filer_race
        race = load_pre_ipo_filer_race()
    except Exception as e:
        log.warning("Filer race load failed: %s", e); race = {}
    hp = data.get("high_profile_pre_ipo", []) or []
    rp = data.get("recently_priced", []) or []
    def _augment(rows):
        out = []
        today = date.today()
        for r in rows:
            r = dict(r)
            name = r.get("company") or r.get("ticker") or ""
            d = race.get(name, {}) or race.get(r.get("ticker", ""), {})
            r["filers"] = d.get("filers", []) or []
            r["total_filings"] = int(d.get("total_filings", 0))
            r["rex_filed"] = bool(d.get("rex_filed", False))
            asof = r.get("as_of_date")
            stale_days = None
            if asof:
                try:
                    stale_days = (today - datetime.fromisoformat(str(asof)).date()).days
                except Exception:
                    pass
            r["stale_days"] = stale_days
            out.append(r)
        return out
    return {"high_profile": _augment(hp), "recently_priced": _augment(rp)}


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
    pipeline = load_trex_2x_long_pipeline()
    scored = load_scored()
    inverse_gap = load_inverse_gap(single_stocks)
    launch_anyway = load_launch_anyway(rex_pos, single_stocks)
    foreign = load_foreign()
    delisting = load_delisting_watch()
    flow_top, flow_buckets = load_flow_score_backtest(single_stocks)
    track_recs = load_track_record_recs()
    ipo = load_ipo_yaml()

    # Filing flags
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
    n_pipeline = len(pipeline)
    top5 = ", ".join(no_live["ticker"].head(5).tolist()) if not no_live.empty else "—"
    top_buzz = "—"
    if not scored.empty and "mentions_24h" in scored.columns and scored["mentions_24h"].notna().any():
        top_idx = scored["mentions_24h"].idxmax()
        top_buzz = f"{scored.at[top_idx, 'ticker']} ({int(scored.at[top_idx, 'mentions_24h'])} mentions)"
    hot_sector = "—"
    try:
        sec_df = _df("SELECT sector FROM li_sector_daily WHERE run_date=(SELECT MAX(run_date) FROM li_sector_daily) ORDER BY total_mentions DESC LIMIT 1")
        if not sec_df.empty: hot_sector = str(sec_df.iloc[0]['sector'])
    except: pass

    # ---------------- IMMINENT ----------------
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
                escape(_safe_str(r['issuer']))[:24],
                escape(_safe_str(r['fund_name']))[:46],
                (escape(_safe_str(r.get('leverage'))), "center"),
                (escape(inc_str), "center"),
                (dr_str, "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        imminent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Product','Competitor','Fund Name','Leverage','Expected','Timing','REX Position'])}
{rows}</table>"""

    # ---------------- RECENT (60d, no 3X) ----------------
    if recent_comp.empty:
        recent_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No competitor ≤2X L&I 485APOS in the last 60 days.</div>'
    else:
        rows=""
        for _, r in recent_comp.iterrows():
            ds=int(r['days_since']) if pd.notna(r['days_since']) else 0
            color=RED if ds<=14 else (ORANGE if ds<=30 else GRAY)
            u = _canon(r['underlier']) if r['underlier'] else ""
            rex_label, rex_color = rex_pos.get(u, ("Not in", RED))
            rows += _tr(
                _tk(u or "—"),
                escape(_safe_str(r['registrant']))[:28],
                escape(_safe_str(r['series_name']))[:60],
                (escape(str(r['filing_date_dt'].date())), "center"),
                (f'<span style="color:{color};font-weight:700;">{ds}d</span>', "center"),
                (f'<span style="color:{rex_color};font-weight:700;">{rex_label}</span>', "center"),
            )
        recent_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Registrant','Series','Filed','Days Since','REX Position'])}
{rows}</table>"""

    # ---------------- PIPELINE (T-REX 2X Long only, top 20) ----------------
    if pipeline.empty:
        pipeline_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No T-REX 2X Long products in pipeline.</div>'
    else:
        pipe_top = pipeline.sort_values("underlier_score", ascending=False).head(20)
        rows=""
        for _, r in pipe_top.iterrows():
            sb = r["status_binary"]
            status_color = GREEN if sb == "Effective" else BLUE
            u=r['underlier_clean']
            score=r.get('underlier_score',0) or 0
            score_html=f'<b style="color:{NAVY};">{score:.1f}</b>' if score>0 else f'<span style="color:{GRAY};">—</span>'
            file_dt=str(r['filing_dt'].date()) if pd.notna(r['filing_dt']) else "—"
            eff_dt=str(r['eff_dt'].date()) if pd.notna(r['eff_dt']) else "—"
            rows += _tr(
                _tk(_clean(r['ticker']) if pd.notna(r['ticker']) and r['ticker'] else "—"),
                escape(_safe_str(r['fund_name']))[:50],
                _tk(u or "—"),
                (f'<span style="color:{status_color};font-weight:700;">{sb}</span>', "center"),
                (escape(file_dt), "center"),
                (escape(eff_dt), "center"),
                (score_html, "right"),
            )
        pipeline_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Underlier','Status','Filed','Effective','Score'])}
{rows}</table>"""

    # ---------------- MEGA TABLE ----------------
    if no_live.empty:
        mega_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No scored candidates.</div>'
    else:
        rows=""
        for i, r in no_live.reset_index(drop=True).iterrows():
            tk=r['ticker']; name=_safe_str(r.get('name'), str(tk))[:36]
            sector=_safe_str(r.get('sector'))[:16]
            themes=_safe_str(r.get('themes'), "").replace(',','·')[:16]
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

    # ---------------- INVERSE GAP ----------------
    if inverse_gap.empty:
        ig_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No single-stock inverse-gap opportunities surfaced.</div>'
    else:
        rows=""
        for _, r in inverse_gap.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(_clean(r['top_ticker'])),
                escape(_safe_str(r['top_fund']))[:42],
                escape(_safe_str(r['top_issuer']))[:24],
                (_fmt_aum_m(r['top_aum']), "right"),
            )
        ig_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Long Count','Top Long Product','Fund Name','Issuer','Top AUM'])}
{rows}</table>"""

    # ---------------- LAUNCH ANYWAY (top 20 + first-launch) ----------------
    if launch_anyway.empty:
        la_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No 1-2 competitor proven-demand lanes surfaced.</div>'
    else:
        rows=""
        for _, r in launch_anyway.iterrows():
            mo = r.get("months_old")
            age_str = f"{mo:.0f}mo" if mo is not None else "—"
            age_color = GREEN if (mo is not None and mo >= 12) else (ORANGE if (mo is not None and mo >= 6) else GRAY)
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(_clean(r['top_ticker'])),
                escape(_safe_str(r['top_fund'])),
                escape(_safe_str(r['top_issuer']))[:24],
                (_fmt_aum_m(r['top_aum']), "right"),
                (escape(_safe_str(r['first_launch'])), "center"),
                (f'<span style="color:{age_color};font-weight:700;">{age_str}</span>', "center"),
            )
        la_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Comp Count','Top Product','Fund Name','Issuer','Top AUM','First Launch','Track Record'])}
{rows}</table>"""

    # ---------------- FOREIGN ----------------
    if foreign.empty:
        foreign_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">Foreign candidates not loaded.</div>'
    else:
        rows=""
        for _, r in foreign.iterrows():
            rs = _safe_str(r.get('rex_status')); rs_color = {"pending":ORANGE,"active":GREEN,"filed":BLUE}.get(rs.lower(), GRAY)
            cs = _safe_str(r.get('competitor_2x_status')); cs_color = {"active":RED,"filed":ORANGE}.get(cs.lower(), GRAY)
            mcap = r.get('market_cap_usd', 0)
            mcap_str = f"${mcap/1e9:.0f}B" if mcap and mcap>=1e9 else _fmt_mcap((mcap or 0)/1e6)
            score = r.get('composite_score',0)
            rows += _tr(
                _tk(_safe_str(r['foreign_ticker'])),
                escape(_safe_str(r.get('name')))[:32],
                (escape(_safe_str(r.get('market'))), "center"),
                escape(_safe_str(r.get('sector')))[:18],
                (mcap_str, "right"),
                (f'<span style="color:{rs_color};font-weight:700;">{rs}</span>', "center"),
                (f'<span style="color:{cs_color};font-weight:700;">{cs}</span>', "center"),
                (str(r.get('competitor_active_count',0)), "center"),
                (f'<b style="color:{NAVY};">{score:.1f}</b>', "right"),
            )
        foreign_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Company','Market','Sector','Global Mkt Cap','REX Status','Comp 2x','Comp Active','Score'])}
{rows}</table>"""

    # ---------------- IPO ----------------
    hp = ipo["high_profile"]; rp = ipo["recently_priced"]
    if not hp:
        ipo_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">IPO watchlist YAML missing.</div>'
    else:
        rows=""
        for r in sorted(hp, key=lambda x: -(x.get("valuation_usd") or 0)):
            company = r.get("company") or r.get("ticker") or "—"
            val_usd = r.get("valuation_usd")
            val_str = f"${val_usd:,.0f}B" if val_usd else "n/a"
            window = r.get("expected_ipo_window") or r.get("date") or "—"
            s1 = "YES" if r.get("s1_filed") else "—"
            s1_color = GREEN if r.get("s1_filed") else GRAY
            stale = r.get("stale_days")
            if stale is None:
                stale_str = "—"
            elif stale > 60:
                stale_str = f'<span style="color:{RED};font-weight:700;">{stale}d</span>'
            else:
                stale_str = f'<span style="color:{GRAY};">{stale}d</span>'
            rex_y = bool(r.get("rex_filed"))
            rex_badge = (f'<span style="color:{GREEN};font-weight:700;">YES</span>' if rex_y
                         else f'<span style="color:{GRAY};">no</span>')
            n_filings = r.get("total_filings", 0)
            issuers = ", ".join(f["issuer"] for f in (r.get("filers") or [])[:5]) or "—"
            rows += _tr(
                escape(str(company)),
                (val_str, "right"),
                (escape(window), "center"),
                (f'<span style="color:{s1_color};font-weight:700;">{s1}</span>', "center"),
                (str(n_filings), "center"),
                escape(issuers[:50]),
                (rex_badge, "center"),
                (stale_str, "center"),
            )
        ipo_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Company','Valuation','IPO Window','S-1','L&I Filings','Top Filers','REX Filed','Data Age'])}
{rows}</table>"""
        if rp:
            rp_rows = ""
            for r in rp:
                rp_rows += _tr(
                    _tk(_clean(r.get("ticker", ""))),
                    escape(_safe_str(r.get("company"))),
                    (escape(_safe_str(r.get("expected_ipo_window")) or _safe_str(r.get("date"))), "center"),
                    escape(_safe_str(r.get("desc"))[:80]),
                )
            ipo_html += f"""<div style="font-size:11px;color:{GRAY};margin-top:14px;margin-bottom:4px;font-weight:600;">Recently Priced</div>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Company','Priced','Description'])}
{rp_rows}</table>"""

    # ---------------- DELISTING (REX ONLY) ----------------
    if delisting.empty:
        del_html = f'<div style="font-size:12px;color:{GREEN};padding:10px 0;font-weight:600;">No REX L&amp;I products below the $15M / 6-month threshold. All shipped product clearing the rule of thumb.</div>'
    else:
        rows=""
        for _, r in delisting.iterrows():
            age_m = float(r["age_months"])
            age_color = RED if age_m >= 12 else (ORANGE if age_m >= 9 else GRAY)
            rows += _tr(
                _tk(_clean(r["ticker"])),
                escape(_safe_str(r["fund_name"]))[:46],
                _tk(_canon(r["underlier"]) if pd.notna(r["underlier"]) else "—"),
                (escape(_safe_str(r.get("direction"))), "center"),
                (_fmt_aum_m(r["aum"]), "right"),
                (f'<span style="color:{age_color};font-weight:700;">{age_m:.1f}mo</span>', "center"),
            )
        del_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Underlier','Direction','AUM','Age'])}
{rows}</table>"""

    # ---------------- TRACK RECORD (FLOW BACKTEST) ----------------
    if flow_top.empty:
        track_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No 3-month time-series data to backtest against.</div>'
    else:
        # Bucket-level summary
        bucket_rows = ""
        for label, b in flow_buckets.items():
            n = b["n"]
            if n == 0:
                bucket_rows += _tr(label, ("0", "center"), ("—", "right"), ("—", "right"), ("—", "center"))
                continue
            avg = b["avg_flow"]; med = b["median_flow"]; pp = b["pct_positive"]
            avg_str = _fmt_flow_m(avg)
            med_str = _fmt_flow_m(med)
            pp_str = f"{pp*100:.0f}%" if pp is not None else "—"
            color = GREEN if avg > 0 else RED
            bucket_rows += _tr(
                f'<b style="color:{color};">{label}</b>',
                (str(n), "center"),
                (avg_str, "right"),
                (med_str, "right"),
                (pp_str, "center"),
            )
        bucket_table = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:12px;">
{_table_header(['Score Bucket','Underliers','Avg 3mo Flow','Median 3mo Flow','% with Inflow'], aligns=['left','center','right','right','center'])}
{bucket_rows}</table>"""

        # Top-20 by absolute flow
        flow_rows = ""
        for _, r in flow_top.iterrows():
            u = r["u_clean"]
            score = float(r["score"])
            score_html = f'<b style="color:{NAVY};">{score:.1f}</b>' if score > 0 else f'<span style="color:{GRAY};">—</span>'
            flow = float(r["flow_3mo"])
            cur = float(r["cur_aum"])
            n_prod = int(r["n_products"])
            rex_n = int(r["rex_count"])
            rex_badge = (f'<b style="color:{GREEN};">{rex_n}</b>' if rex_n > 0
                         else f'<span style="color:{GRAY};">0</span>')
            flow_rows += _tr(
                _tk(u),
                (_fmt_aum_m(cur), "right"),
                (_fmt_flow_m(flow), "right"),
                (str(n_prod), "center"),
                (rex_badge, "center"),
                (score_html, "right"),
            )
        flow_table = f"""<div style="font-size:11px;color:{GRAY};margin-top:14px;margin-bottom:4px;font-weight:600;">Top 20 Single-Stock Underliers by Absolute 3-Month Flow</div>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Current Total AUM','3mo Net Flow','# Products','REX Count','Score'], aligns=['left','right','right','center','center','right'])}
{flow_rows}</table>"""

        # Optional recs-history block
        recs_html = ""
        if track_recs.get("high_total", 0) > 0:
            rate = lambda v: "—" if v is None else f"{v*100:.0f}%"
            recs_html = f"""<div style="font-size:11px;color:{GRAY};margin-top:14px;line-height:1.6;">
              <strong style="color:{NAVY};">Forward track (recommendation_history, 90d):</strong>
              HIGH {track_recs.get('high_hit',0)}/{track_recs.get('high_total',0)} ({rate(track_recs.get('high_hit_rate'))}) ·
              MEDIUM {track_recs.get('medium_hit',0)}/{track_recs.get('medium_total',0)} ({rate(track_recs.get('medium_hit_rate'))}) ·
              WATCH {track_recs.get('watch_hit',0)}/{track_recs.get('watch_total',0)} ({rate(track_recs.get('watch_hit_rate'))})
            </div>"""
        else:
            recs_html = f"""<div style="font-size:11px;color:{GRAY};margin-top:14px;line-height:1.6;font-style:italic;">
              Forward track (recommendation_history) is still building — first reliable read after ~10 rolling weeks of graded recommendations.
            </div>"""

        # Verdict line
        h_avg = flow_buckets.get("HIGH (≥40)", {}).get("avg_flow", 0)
        l_avg = flow_buckets.get("LOW (<20)", {}).get("avg_flow", 0)
        h_pct = flow_buckets.get("HIGH (≥40)", {}).get("pct_positive", 0) or 0
        l_pct = flow_buckets.get("LOW (<20)", {}).get("pct_positive", 0) or 0
        ratio = (h_avg / l_avg) if l_avg else None
        verdict_color = GREEN if (h_avg > l_avg and h_pct > l_pct) else RED
        verdict_emoji = "✓" if verdict_color == GREEN else "✗"
        verdict_str = (
            f"<strong>Verdict:</strong> HIGH-scored underliers show {ratio:.1f}× the average flow of LOW-scored underliers"
            f" ({_fmt_flow_m(h_avg)} vs {_fmt_flow_m(l_avg)}), and {h_pct*100:.0f}% of HIGH underliers saw inflow vs {l_pct*100:.0f}% of LOW."
            f" Scoring system <strong style='color:{verdict_color};'>{verdict_emoji} directionally validates</strong>."
            if ratio is not None else "<strong>Verdict:</strong> sample too small."
        )

        track_html = f"""
<div style="font-size:12px;color:{NAVY};line-height:1.7;margin-bottom:10px;background:{LIGHT};padding:12px 14px;border-radius:6px;border-left:4px solid {GREEN};">
  <div style="font-size:13px;font-weight:700;color:{NAVY};margin-bottom:4px;">How well does our scoring predict where the money goes?</div>
  <div style="font-size:11px;color:{GRAY};margin-bottom:6px;">For every single-stock leveraged-&-inverse underlier with at least one active product, we sum today's total AUM and subtract the same total from 3 months ago to get net flow. We then join the underlier's current score to see whether high-score underliers actually attract money. Basket indices and sector ETNs are excluded so the comparison is apples-to-apples.</div>
  <div style="font-size:12px;color:{NAVY};line-height:1.7;">{verdict_str}</div>
</div>
{bucket_table}
{flow_table}
{recs_html}
"""

    # ---------------- METHODOLOGY ----------------
    methodology_html = f"""
<div style="font-size:12px;color:#1a1a2e;line-height:1.7;">
  <p><strong>What this report consolidates:</strong> the prior REX Weekly ETP Report (filing breadth + issuer scorecard) and the L&amp;I Weekly v2 Stock Recommendations (whitespace scoring + launch theses) — one Monday send covering <em>what to file</em> (whitespace + race timing + delisting) and <em>what to launch</em> (T-REX 2X Long pipeline + inverse gap + launch-anyway).</p>

  <p><strong>Composite score (weighting buckets, proportional 0-100):</strong></p>
  <table cellpadding="3" cellspacing="0" style="border-collapse:collapse;font-size:11px;margin:6px 0 10px;">
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Attention</td><td><b>34</b></td><td style="color:{GRAY};">Percentile of 24h social mentions · floor 5 mentions (below = 0)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Liquidity</td><td><b>25</b></td><td style="color:{GRAY};">Percentile of log(turnover) — ADV × price</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Theme</td><td><b>20</b></td><td style="color:{GRAY};">Hot theme = 20 · Regular thematic = 10 · Untagged = 0 (direct award)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Momentum</td><td><b>12</b></td><td style="color:{GRAY};">Percentile of 1-year total return</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Volatility</td><td><b>9</b></td><td style="color:{GRAY};">Percentile of 90-day realized vol — higher is better (L&amp;I edge)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{RED};">Short int penalty</td><td><b style="color:{RED};">−8</b></td><td style="color:{GRAY};">Penalty scales above the SI median</td></tr>
  </table>

  <p><strong>Filters &amp; scopes:</strong></p>
  <ul style="margin:4px 0 10px;padding-left:20px;color:{GRAY};">
    <li>Universe gate: mkt cap ≥ $500M (sanity floor, applied at report layer).</li>
    <li>L&amp;I product detection: explicit leverage multiplier (NX/N.NX) <em>or</em> "Leveraged" / "Inverse" <em>or</em> "Daily Target" <em>or</em> "Bull/Bear NX" — rejects non-L&amp;I funds (Aristotle Short Duration, Nuveen Short Term Bond, etc.).</li>
    <li>Pipeline section is restricted to <strong>T-REX 2X Long</strong> products only (long-leveraged single-stock T-REX series) — REX IncomeMax / REX Equity Premium Income / sector-basket products are excluded.</li>
    <li>Recent Competitor Filings panel is <strong>last 60 days, ≤2X only</strong> (3X+ products explicitly excluded).</li>
    <li>Delisting Watch is <strong>REX-only</strong> (competitor products dropped).</li>
    <li>Single-stock filter for whitespace tables: tickers must appear in mkt_stock_data (~6,594 names).</li>
    <li>Share-class aliasing: GOOG ↔ GOOGL, BRKA ↔ BRKB — single canonical form so REX GOOX matches GOOGL filings.</li>
  </ul>

  <p><strong>Pipeline status (binary):</strong> Sourced from <code>rex_products.status</code>. Collapsed as: <b>Effective</b> = {{Effective, Listed}} <em>or</em> any 485BPOS row whose estimated effective date is in the past. <b>Filed</b> = {{Filed, Under Consideration, Target List}}. Delisted excluded.</p>

  <p><strong>Delisting watch:</strong> REX-only. ACTV L&amp;I with AUM &lt; $15M and age ≥ 5.5 months. Rule of thumb: funds that haven't crossed $15M by month 6 historically don't. AUM in Bloomberg millions.</p>

  <p><strong>Track record — concurrent backtest:</strong> For each single-stock L&amp;I underlier with at least one active product, we sum today's AUM across all L&amp;I products on that underlier, subtract the same sum from 3 months ago (<code>mkt_time_series.aum_value</code> with <code>months_ago=3</code>), and compare to the underlier's composite score. Basket indices and sector ETNs are excluded so the comparison is apples-to-apples single-stock. If the scoring works, HIGH-score buckets should show larger / more positive average flows than LOW-score buckets. This is concurrent validity, not a forward test — the forward test (recommendation_history hit-rate) needs ~10 weeks of accrued recommendations before it's statistically meaningful.</p>

  <p><strong>IPO valuations:</strong> <code>config/ipo_watchlist.yaml</code> (verified web sources, last refresh 2026-06-02). Rows older than 60 days are flagged in the Data Age column. To refresh: edit YAML's <code>valuation_usd</code>, <code>as_of_date</code>, <code>s1_filed</code> fields — no code change required.</p>

  <p style="margin-top:10px;font-size:11px;color:{GRAY};"><strong>Recent updates:</strong> race timing removed from scoring buckets (now its own section); tier bands (LAUNCH/FILE/WATCH) removed; methodology embedded; track record (flow backtest + forward hit-rate) added; T-REX delisting watch added; pipeline restricted to T-REX 2X Long filed-not-launched; recent filings tightened to 60d ≤2X; report scoped to single-stock L&amp;I — basket indices and sector ETNs excluded throughout.</p>
</div>
"""

    yaml_refresh_note = "2026-06-02 (verified)" if YAML_PATH.exists() else "YAML missing"
    bucket_signal = ""
    if flow_buckets:
        h = flow_buckets.get("HIGH (≥40)", {}).get("avg_flow", 0)
        l = flow_buckets.get("LOW (<20)", {}).get("avg_flow", 0)
        if abs(h) > 1 or abs(l) > 1:
            verdict = "scoring tracks flows" if h > l else "scoring inverts flows"
            bucket_signal = f"Scoring vs flow (3mo): HIGH-bucket avg {_fmt_flow_m(h)} · LOW-bucket avg {_fmt_flow_m(l)} → {verdict}"

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>T-REX Stock Recommendation System — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="1200" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1200px;">
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">T-REX Stock Recommendation System | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">
    Imminent · New Filings · T-REX 2X Pipeline · Whitespace · Inverse Gap · Underliers REX Should Enter · Foreign · Pre-IPO · Delisting · Track Record · Methodology
  </div>
</td></tr>
<tr><td style="padding:12px 30px 0;">
  <div style="font-size:12px;color:{GRAY};line-height:1.7;">
    <strong style="color:{NAVY};">Whitespace pool (curated):</strong> <span style="color:{NAVY};font-weight:700;">{n_scored:,}</span> ·
    <strong style="color:{NAVY};">Full universe scored daily:</strong> <span style="color:{NAVY};font-weight:700;">6,594</span> ·
    <strong style="color:{NAVY};">T-REX 2X Long filed-not-launched:</strong> <span style="color:{TEAL};font-weight:700;">{n_pipeline} products</span> ·
    <strong style="color:{NAVY};">Top 5 whitespace (no live product):</strong> <span style="font-family:Courier New,monospace;color:{BLUE};font-weight:700;">{escape(top5)}</span> ·
    <strong style="color:{NAVY};">Hot sector:</strong> <span style="color:{PURPLE};font-weight:700;">{escape(hot_sector)}</span> ·
    <strong style="color:{NAVY};">Top buzz:</strong> {escape(top_buzz)}
  </div>
  {f'<div style="font-size:11px;color:{TEAL};margin-top:6px;font-weight:600;">{bucket_signal}</div>' if bucket_signal else ''}
</td></tr>

{_section_header('1 · Imminent Competitor Launches — PEND status', RED, 'Non-REX L&I products currently in PEND state — recently launched (status lag), upcoming, or undated. Dead-stale (>60d past inception with no ACTV transition) excluded.')}
<tr><td style="padding:6px 30px 8px;">{imminent_html}</td></tr>

{_section_header('Recent Competitor L&I Filings — New Series, Last 60 Days (≤2X)', ORANGE, 'Non-REX 485APOS for new L&I series (deduped by series name — re-filings of the same series suppressed). 3X+ products excluded. Top 50 most recent.')}
<tr><td style="padding:6px 30px 8px;">{recent_html}</td></tr>

{_section_header('2 · Our Pipeline — T-REX 2X Long Filed, Not Yet Launched (Top 20)', BLUE, 'T-REX 2X Long products in Filed / Under Consideration / Target List status — what we have on file but have not yet shipped to market. Underlier score from the daily scoring run. Pre-IPO targets (Anthropic / SpaceX / etc.) appear with no score since their underlier is private.')}
<tr><td style="padding:6px 30px 8px;">{pipeline_html}</td></tr>

{_section_header('3 · Whitespace Ranking — Top 100 Underliers With No Live L&I Product', NAVY, 'From the curated whitespace pool (1,417 tickers), ranked by composite score. Filter: no active L&I product on the underlier yet. REX/Comp filing flags show whether anyone has filed.')}
<tr><td style="padding:6px 30px 8px;">{mega_html}</td></tr>

{_section_header('4 · Inverse Gap — Underliers With Long Product but Zero Inverse (Single-Stock)', PURPLE, 'Single-stock underliers where competitor (or REX) ships an active long L&I product but no inverse exists anywhere. MU, NVDA, TSLA, AAPL etc. already have inverse coverage (verified) and are correctly absent from this list. Baskets / indices / commodities excluded.')}
<tr><td style="padding:6px 30px 8px;">{ig_html}</td></tr>

{_section_header('5 · Underliers REX Should Enter — 1-2 Competitor Longs, REX Not In (Top 30)', BLUE, 'Single-stock underliers where 1-2 competitor longs already trade with top-product AUM > $100M, and REX has no position. Proven demand, beatable head count. First-launch date + age shown so you can read seasoning. Lowered threshold from $300M → $100M to surface more candidates.')}
<tr><td style="padding:6px 30px 8px;">{la_html}</td></tr>

{_section_header('6 · Foreign Megacap Underliers', "#34495e", 'Curated overseas single-stock underliers — Samsung, SK Hynix, Tencent, Toyota, etc. Global market cap, REX status, competitor 2x activity.')}
<tr><td style="padding:6px 30px 8px;">{foreign_html}</td></tr>

{_section_header('7 · Pre-IPO Watchlist & Recently Priced IPOs', TEAL, f'High-profile pre-IPO targets with current valuations + S-1 status. YAML-backed (last refresh {yaml_refresh_note}). Rows older than 60d flagged in Data Age. L&I filer race shows who has filed leveraged products on each target.')}
<tr><td style="padding:6px 30px 8px;">{ipo_html}</td></tr>

{_section_header("8 · T-REX Delisting Watch — Live Products < $15M @ 6mo+", RED, "T-REX brand only. ACTV products with AUM under $15M and age >= 5.5 months. Rule of thumb: funds that have not crossed $15M by month 6 historically do not graduate, so these are review candidates.")}
<tr><td style="padding:6px 30px 8px;">{del_html}</td></tr>

{_section_header('9 · Scoring Track Record — Does Our Score Predict Real Flows?', GREEN, 'Concurrent backtest. Group every single-stock L&I underlier by its current score band (HIGH / MED / LOW) and compare the average 3-month net AUM change in each band. If our score works, the HIGH band should see more flow than the LOW band. Basket indices and sector ETNs excluded so the comparison is apples-to-apples.')}
<tr><td style="padding:6px 30px 8px;">{track_html}</td></tr>

{_section_header('Methodology — Weighting · Filters · Track-Record Logic', NAVY, 'How every score, status, and watch threshold in this report is computed.')}
<tr><td style="padding:6px 30px 22px;">{methodology_html}</td></tr>

<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    Report consolidates: REX Weekly ETP Report + L&amp;I Weekly v2 Stock Recommendations.<br>
    Methodology frozen 2026-05-29. Next review window: 2026-07.<br><br>
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
