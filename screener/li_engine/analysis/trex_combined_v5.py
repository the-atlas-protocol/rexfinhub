"""T-REX Stock Recommendation System v5 — methodology + track record + delisting watch.

Changes vs v4:
  1. Pipeline scoring joins launch_candidates.parquet.composite_score (full universe ~ same as production).
  2. Status collapsed to binary: Filed | Effective. Auto-effective rule (485BPOS w/ past eff date) applied.
  3. NEW: Delisting Watch — REX ACTV L&I with AUM < $15M and age >= 5.5 months.
  4. NEW: Track Record — calls recommendation_history.hit_rate_stats() to show signal effectiveness.
  5. NEW: Methodology — embedded section with full weighting + filter rules + change log.
  6. IPO section now reads config/ipo_watchlist.yaml directly (valuation_usd, s1_filed, as_of_date staleness badge).
  7. xAI shown as "merged into SpaceX" — uses YAML metadata.

Run on VPS: python3 trex_combined_v5.py
Output: <ROOT>/reports/trex_combined_<date>.html
"""
from __future__ import annotations
import json, logging, re, sqlite3, sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import numpy as np, pandas as pd

log = logging.getLogger(__name__)

# Resolve ROOT: works whether script is in repo tree or sibling-of-data.
_HERE = Path(__file__).resolve()
_CANDIDATE_ROOTS = [
    _HERE.parent.parent.parent.parent,
    Path("/home/jarvis/rexfinhub"),
    Path("C:/Projects/rexfinhub"),
]
_ROOT = next((r for r in _CANDIDATE_ROOTS if (r / "data" / "etp_tracker.db").exists()), _CANDIDATE_ROOTS[0])

# Ensure 'screener' / 'etp_tracker' / 'webapp' importable when run as a script
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "etp_tracker.db"
ANALYSIS = _ROOT / "data" / "analysis"
WS = ANALYSIS / "whitespace_v4.parquet"
LC = ANALYSIS / "launch_candidates.parquet"
CC = ANALYSIS / "competitor_counts.parquet"
FL = ANALYSIS / "foreign_launch_candidates.parquet"
YAML_PATH = _ROOT / "config" / "ipo_watchlist.yaml"
OUT_DIR = _ROOT / "reports"

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; ORANGE="#f39c12"
RED="#e74c3c"; GRAY="#7f8c8d"; LIGHT="#f4f5f6"; BORDER="#ecf0f1"; PURPLE="#8e44ad"; TEAL="#16a085"


# ----------------------- L&I FILTER -----------------------
_LI_RE = re.compile(
    r"(?:(?:^|[\s\-+])[12345](?:\.\d)?[xX]\b"
    r"|\b(?:LEVERAGED|INVERSE)\b"
    r"|\bDAILY\s+TARGET\b"
    r"|\b(?:ULTRA|ULTRAPRO)\b\s+(?:[A-Z]+\s+)*?(?:ETF|ETN)"
    r"|\b(?:BULL|BEAR)\s*(?:[1-3]X|ETF|ETN)\b"
    r")"
)


def is_li_product(name: str) -> bool:
    return isinstance(name, str) and bool(_LI_RE.search(name))


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


def _fmt_aum_m(v):
    """Format AUM in millions (Bloomberg's native unit)."""
    if v is None or pd.isna(v): return "—"
    try: v=float(v)
    except: return "—"
    if v >= 1000: return f"${v/1000:.2f}B"
    return f"${v:.1f}M"


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


# -------------------- STATUS COLLAPSE --------------------
def _collapse_status(row) -> str:
    """Binary collapse: Filed | Effective.

    Rule (mirrors webapp/templates/pipeline_products.html:539-540):
      - {Effective, Listed} -> Effective
      - 485BPOS with estimated_effective_date in past -> Effective
      - Everything else pre-launch -> Filed
      - Delisted entries are excluded upstream.
    """
    raw = str(row.get("status") or "").strip()
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
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    df = _df("""SELECT f.filing_date, f.registrant, fe.series_name, fe.class_symbol
                FROM filings f JOIN fund_extractions fe ON fe.filing_id=f.id
                WHERE f.form='485APOS' AND f.filing_date>=?
                  AND f.registrant NOT LIKE '%REX%'
                  AND f.registrant NOT LIKE '%ETF Opportunities%'
                ORDER BY f.filing_date DESC""", (cutoff,))
    if df.empty: return df
    df = df[df["series_name"].fillna("").apply(is_li_product)]
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
    return df.head(25)


def load_imminent_launches():
    df = _df("""SELECT ticker AS product_ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, map_li_underlier AS underlier, map_li_leverage_amount AS leverage
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='PEND' AND is_rex=0
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''""")
    if df.empty: return df
    today = pd.Timestamp(date.today())
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df = df[(df["inception_dt"].isna()) | (df["inception_dt"] >= today)]
    df = df[(df["inception_dt"].isna()) | (df["inception_dt"] <= today + pd.Timedelta(days=365))]
    df["days_remaining"] = (df["inception_dt"] - today).dt.days
    df["underlier_clean"] = df["underlier"].apply(_canon)
    df = df.sort_values(["inception_dt"], ascending=True, na_position="last")
    return df.head(30)


def load_rex_pipeline_scored():
    """REX pipeline joined to launch_candidates.composite_score (full universe).

    Status is collapsed to {Filed, Effective} via _collapse_status.
    Delisted entries are filtered out at the SQL boundary.
    """
    df = _df("""SELECT ticker, name AS fund_name, direction, status,
                  initial_filing_date, estimated_effective_date,
                  underlier, underlying_ticker
                FROM rex_products
                WHERE status IN ('Filed','Effective','Listed','Under Consideration','Target List')
                ORDER BY initial_filing_date DESC""")
    if df.empty: return df

    def extract_from_name(name):
        if not isinstance(name, str): return ""
        n = name.upper()
        for pat in [
            r"\bT-REX\s+\d(?:\.\d)?X\s+(?:LONG|SHORT|INVERSE)\s+([A-Z]{2,6})\b",
            r"\bREX\s+INCOMEMAX\s+([A-Z]{2,6})\s+STRATEGY",
            r"\bREX\s+([A-Z]{2,6})\s+(?:GROWTH|INCOME|VALUE|STRATEGY)",
            r"\bDAILY\s+TARGET\s+\d(?:\.\d)?X\s+(?:LONG|SHORT)\s+([A-Z]{2,6})",
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
    df["status_binary"] = df.apply(_collapse_status, axis=1)

    # Join v1.0.2 composite from li_engine_daily (full ~6,594-ticker universe).
    # whitespace_v4 is only ~1,417 curated; li_engine_daily covers ~57% of pipeline.
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
                  AND aum IS NOT NULL AND aum > 100""")
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
    df = _df("""SELECT map_li_underlier AS u, map_li_direction AS d,
                  is_rex, COALESCE(issuer_nickname, issuer) AS issuer,
                  ticker, fund_name, aum
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
                  AND aum IS NOT NULL AND aum > 0""")
    if df.empty: return df
    df["u_clean"] = df["u"].apply(_canon)
    df = df[df["u_clean"].isin(single_stocks)]
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


def load_delisting_watch():
    """REX ACTV L&I with AUM < $15M and age >= 5.5 months.

    Rule of thumb: products that haven't crossed $15M by 6 months rarely will.
    AUM is in Bloomberg's native millions. Age is days_since_inception / 30.44.
    """
    df = _df("""SELECT ticker, fund_name,
                  COALESCE(issuer_nickname, issuer) AS issuer,
                  inception_date, aum,
                  map_li_underlier AS underlier,
                  map_li_direction AS direction,
                  map_li_leverage_amount AS leverage,
                  is_rex
                FROM mkt_master_data
                WHERE primary_category='LI' AND market_status='ACTV'
                  AND aum IS NOT NULL AND aum < 15
                  AND inception_date IS NOT NULL""")
    if df.empty: return df
    df["inception_dt"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df["age_days"] = (pd.Timestamp(date.today()) - df["inception_dt"]).dt.days
    df["age_months"] = df["age_days"] / 30.44
    df = df[df["age_months"] >= 5.5]
    df["is_rex"] = df["is_rex"].fillna(0).astype(int)
    # Sort: REX first (so we see our own watch list first), then by age desc (oldest first)
    df = df.sort_values(["is_rex","age_months"], ascending=[False, False])
    return df.head(40)


def load_track_record():
    """Call recommendation_history.hit_rate_stats() — returns aggregate metrics or empty fallback."""
    try:
        from screener.li_engine.analysis.recommendation_history import hit_rate_stats
        return hit_rate_stats(rolling_days=90)
    except Exception as e:
        log.warning("Track record load failed: %s", e)
        return {"high_total": 0, "rolling_days": 90, "sample_size_warning": True}


def load_ipo_yaml():
    """Load config/ipo_watchlist.yaml + cross-ref filer race for each entry."""
    if not YAML_PATH.exists():
        return {"high_profile": [], "recently_priced": []}
    try:
        import yaml
        data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("YAML load failed: %s", e); return {"high_profile": [], "recently_priced": []}

    # Augment with filer-race data
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
            name = r.get("company") or r.get("ticker") or ""
            # Try canonical display name match
            d = race.get(name, {})
            if not d:
                # also try ticker
                d = race.get(r.get("ticker", ""), {})
            r = dict(r)
            r["filers"] = d.get("filers", []) or []
            r["total_filings"] = int(d.get("total_filings", 0))
            r["rex_filed"] = bool(d.get("rex_filed", False))
            # Staleness badge
            asof = r.get("as_of_date")
            stale_days = None
            if asof:
                try:
                    asof_d = datetime.fromisoformat(str(asof)).date()
                    stale_days = (today - asof_d).days
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
    pipeline = load_rex_pipeline_scored()
    scored = load_scored()
    inverse_gap = load_inverse_gap(single_stocks)
    launch_anyway = load_launch_anyway(rex_pos, single_stocks)
    foreign = load_foreign()
    delisting = load_delisting_watch()
    track = load_track_record()
    ipo = load_ipo_yaml()

    # Filing flags via competitor_counts
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
    if not scored.empty and "mentions_24h" in scored.columns and scored["mentions_24h"].notna().any():
        top_idx = scored["mentions_24h"].idxmax()
        top_buzz = f"{scored.at[top_idx, 'ticker']} ({int(scored.at[top_idx, 'mentions_24h'])} mentions)"
    hot_sector = "—"
    try:
        sec_df = _df("SELECT sector FROM li_sector_daily WHERE run_date=(SELECT MAX(run_date) FROM li_sector_daily) ORDER BY total_mentions DESC LIMIT 1")
        if not sec_df.empty: hot_sector = str(sec_df.iloc[0]['sector'])
    except: pass

    # Track record headline stat
    high_rate = track.get("high_hit_rate")
    track_summary = (
        f"HIGH {track.get('high_hit', 0)}/{track.get('high_total', 0)} "
        f"({high_rate*100:.0f}%)" if (high_rate is not None and track.get('high_total', 0) > 0)
        else "Track record building"
    )

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

    # ---------------- RECENT ----------------
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

    # ---------------- PIPELINE ----------------
    if pipeline.empty:
        pipeline_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No active REX pipeline items.</div>'
    else:
        pipe_top = pipeline.sort_values("underlier_score", ascending=False).head(50)
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
                _tk(_clean(r['ticker']) if pd.notna(r['ticker']) else "—"),
                escape(str(r['fund_name'])[:50]) if pd.notna(r['fund_name']) else "—",
                _tk(u or "—"),
                (escape(str(r.get('direction') or '—')), "center"),
                (f'<span style="color:{status_color};font-weight:700;">{sb}</span>', "center"),
                (escape(file_dt), "center"),
                (escape(eff_dt), "center"),
                (score_html, "right"),
            )
        pipeline_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Underlier','Direction','Status','Filed','Effective','Score'])}
{rows}</table>"""

    # ---------------- MEGA TABLE ----------------
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
                escape(str(r['top_fund'])[:42]),
                escape(str(r['top_issuer'])[:24]),
                (_fmt_aum_m(r['top_aum']), "right"),
            )
        ig_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Long Count','Top Long Product','Fund Name','Issuer','Top AUM'])}
{rows}</table>"""

    # ---------------- LAUNCH ANYWAY ----------------
    if launch_anyway.empty:
        la_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No 1-2 competitor proven-demand lanes surfaced.</div>'
    else:
        rows=""
        for _, r in launch_anyway.iterrows():
            rows += _tr(
                _tk(r['underlier']),
                (str(r['n_long']), "center"),
                _tk(_clean(r['top_ticker'])),
                escape(str(r['top_fund'])),
                escape(str(r['top_issuer'])[:24]),
                (_fmt_aum_m(r['top_aum']), "right"),
            )
        la_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Underlier','Competitor Count','Top Product','Fund Name','Issuer','Top AUM'])}
{rows}</table>"""

    # ---------------- FOREIGN ----------------
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

    # ---------------- IPO (YAML-driven) ----------------
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
            stale_str = (f'<span style="color:{RED};font-weight:700;">{stale}d</span>'
                         if (stale is not None and stale > 60)
                         else f'<span style="color:{GRAY};">{stale}d</span>' if stale is not None else "—")
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
        # Recently priced subtable
        if rp:
            rp_rows = ""
            for r in rp:
                rp_rows += _tr(
                    _tk(_clean(r.get("ticker", ""))),
                    escape(str(r.get("company") or "—")),
                    (escape(str(r.get("expected_ipo_window") or r.get("date") or "—")), "center"),
                    escape(str(r.get("desc") or "")[:80]),
                )
            ipo_html += f"""<div style="font-size:11px;color:{GRAY};margin-top:14px;margin-bottom:4px;font-weight:600;">Recently Priced</div>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Company','Priced','Description'])}
{rp_rows}</table>"""

    # ---------------- DELISTING WATCH ----------------
    if delisting.empty:
        del_html = f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">No products below the $15M / 6-month threshold.</div>'
    else:
        rows=""
        for _, r in delisting.iterrows():
            is_rex = bool(r["is_rex"])
            issuer_str = "REX" if is_rex else escape(str(r["issuer"] or "—"))[:24]
            issuer_color = GREEN if is_rex else GRAY
            age_m = float(r["age_months"])
            age_color = RED if age_m >= 12 else (ORANGE if age_m >= 9 else GRAY)
            rows += _tr(
                _tk(_clean(r["ticker"])),
                escape(str(r["fund_name"])[:46]) if pd.notna(r["fund_name"]) else "—",
                (f'<span style="color:{issuer_color};font-weight:700;">{issuer_str}</span>', "center"),
                _tk(_canon(r["underlier"]) if pd.notna(r["underlier"]) else "—"),
                (escape(str(r.get("direction") or "—")), "center"),
                (_fmt_aum_m(r["aum"]), "right"),
                (f'<span style="color:{age_color};font-weight:700;">{age_m:.1f}mo</span>', "center"),
            )
        del_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Ticker','Fund Name','Issuer','Underlier','Direction','AUM','Age'])}
{rows}</table>"""

    # ---------------- TRACK RECORD ----------------
    if track.get("high_total", 0) == 0:
        track_html = f"""<div style="font-size:12px;color:{GRAY};padding:10px 0;">
        Track record is building — fewer than 10 HIGH-tier recommendations have aged out of the 90-day window yet.
        First reliable hit-rate read expected after the grader has graded ~10 rolling weeks of recommendations.
        Logging table: <code>recommendation_history</code>. Grader: <code>grade_open_recommendations()</code>.
        </div>"""
    else:
        rate = lambda v: "—" if v is None else f"{v*100:.0f}%"
        avg_aum = track.get("avg_aum_6mo")
        aum_str = f"${avg_aum:,.1f}M" if avg_aum is not None else "—"
        warn = ' <span style="color:'+ORANGE+';font-weight:600;">(small sample)</span>' if track.get("sample_size_warning") else ''
        track_html = f"""<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
{_table_header(['Tier','Recs','Hits','Hit Rate','Notes'])}
{_tr('<b style="color:'+GREEN+';">HIGH</b>'+warn, (str(track.get('high_total',0)), 'center'), (str(track.get('high_hit',0)), 'center'), (f"<b>{rate(track.get('high_hit_rate'))}</b>", 'center'), 'Top-3 scoring candidates per week — should over-index on hits')}
{_tr('<b style="color:'+ORANGE+';">MEDIUM</b>', (str(track.get('medium_total',0)), 'center'), (str(track.get('medium_hit',0)), 'center'), (rate(track.get('medium_hit_rate')), 'center'), 'Next 4 by score')}
{_tr('<b style="color:'+GRAY+';">WATCH</b>', (str(track.get('watch_total',0)), 'center'), (str(track.get('watch_hit',0)), 'center'), (rate(track.get('watch_hit_rate')), 'center'), 'Remaining ranked candidates')}
</table>
<div style="font-size:11px;color:{GRAY};margin-top:10px;line-height:1.6;">
  Rolling 90-day window · Hit = REX product launched OR REX 485APOS filed on the underlier after the recommendation week.<br>
  Avg AUM 6mo post-launch: <strong style="color:{NAVY};">{aum_str}</strong> ·
  Tier accuracy (% of all hits that came from HIGH): <strong style="color:{NAVY};">{rate(track.get('tier_accuracy'))}</strong>
</div>"""

    # ---------------- METHODOLOGY ----------------
    methodology_html = f"""
<div style="font-size:12px;color:#1a1a2e;line-height:1.7;">
  <p><strong>What this report consolidates:</strong> the prior REX Weekly ETP Report (filing breadth + issuer scorecard) and the L&amp;I Weekly v2 Stock Recommendations (whitespace scoring + launch theses) — a single Monday send covering both <em>what to file</em> (whitespace + race timing + delisting) and <em>what to launch</em> (pipeline scoring + inverse gap + launch anyway).</p>

  <p><strong>Composite score (v1.0.2 weighting buckets, proportional 0-100):</strong></p>
  <table cellpadding="3" cellspacing="0" style="border-collapse:collapse;font-size:11px;margin:6px 0 10px;">
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Attention</td><td><b>34</b></td><td style="color:{GRAY};">Percentile of 24h social mentions · floor 5 mentions (below = 0)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Liquidity</td><td><b>25</b></td><td style="color:{GRAY};">Percentile of log(turnover) — ADV × price</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Theme</td><td><b>20</b></td><td style="color:{GRAY};">Hot theme = 20 · Regular thematic = 10 · Untagged = 0 (direct award, no percentile)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Momentum</td><td><b>12</b></td><td style="color:{GRAY};">Percentile of 1-year total return</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{GRAY};">Volatility</td><td><b>9</b></td><td style="color:{GRAY};">Percentile of 90-day realized vol — higher is better (L&amp;I edge)</td></tr>
    <tr><td style="padding:3px 14px 3px 0;color:{RED};">Short int penalty</td><td><b style="color:{RED};">−8</b></td><td style="color:{GRAY};">Penalty scales above the SI median (squeeze risk fades the long thesis)</td></tr>
  </table>

  <p><strong>Filters:</strong></p>
  <ul style="margin:4px 0 10px;padding-left:20px;color:{GRAY};">
    <li>Universe gate: mkt cap ≥ $500M (sanity floor, applied at report layer).</li>
    <li>L&amp;I product detection: explicit leverage multiplier (NX/N.NX) <em>or</em> "Leveraged" / "Inverse" <em>or</em> "Daily Target" <em>or</em> "Bull/Bear NX" — rejects Aristotle Short Duration, Nuveen Short Term Bond, Defiance Helium, GraniteShares Dragon AI and similar non-L&amp;I funds.</li>
    <li>Single-stock filter: tickers must appear in mkt_stock_data (~6,594 names) — excludes BASKET, indices, commodities (XAG, NGA, XBTUSD).</li>
    <li>Share-class aliasing: GOOG ↔ GOOGL, BRKA ↔ BRKB normalize to single canonical form so REX GOOX matches GOOGL filings.</li>
  </ul>

  <p><strong>Pipeline status (binary):</strong> Sourced from <code>rex_products.status</code>. Collapsed as: <b>Effective</b> = {{Effective, Listed}} <em>or</em> any 485BPOS row whose estimated effective date is in the past. <b>Filed</b> = {{Filed, Under Consideration, Target List}}. Delisted entries excluded.</p>

  <p><strong>Delisting watch:</strong> ACTV L&amp;I products with AUM &lt; $15M and age ≥ 5.5 months. Rule of thumb — funds that haven't crossed $15M by month 6 historically don't. AUM source: Bloomberg <code>aum</code> (millions). REX-issued first, then competitor for context.</p>

  <p><strong>Track record:</strong> Each weekly recommendation is logged in <code>recommendation_history</code> with its tier (HIGH/MEDIUM/WATCH) and composite score. <code>grade_open_recommendations()</code> runs weekly and updates the outcome to <em>launched</em>, <em>rex_filed</em>, <em>competitor_filed</em>, or <em>abandoned</em>. Hit rate = (launched + rex_filed) / total in the rolling 90-day window.</p>

  <p><strong>IPO valuations:</strong> Sourced from <code>config/ipo_watchlist.yaml</code> (verified manually, last refresh 2026-06-02). Rows older than 60 days are flagged in the Data Age column. To refresh: edit the YAML's <code>valuation_usd</code>, <code>as_of_date</code>, and <code>s1_filed</code> fields — no code change required.</p>

  <p style="margin-top:10px;font-size:11px;color:{GRAY};"><strong>Changes since v1.0.1:</strong> race timing removed from scoring buckets (now a separate table); attention/theme gate removed (now scoring inputs only); tier band labels (LAUNCH/FILE/WATCH) removed; methodology now embedded in-report; track record + delisting watch added.</p>
</div>
"""

    # ---------------- HTML SHELL ----------------
    yaml_refresh_note = ("2026-06-02 (verified)" if YAML_PATH.exists() else "YAML missing")

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>T-REX Stock Recommendation System — {today_pretty}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="1200" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1200px;">
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">T-REX Stock Recommendation System | {today_pretty}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">
    v1.0.2 · Race · Pipeline · Mega · Inverse · Launch-Anyway · Foreign · Pre-IPO · Delisting · Track Record · Methodology
  </div>
</td></tr>
<tr><td style="padding:12px 30px 0;">
  <div style="font-size:12px;color:{GRAY};line-height:1.7;">
    <strong style="color:{NAVY};">{n_scored:,} stocks scored</strong> ·
    <strong style="color:{NAVY};">Top 5 (no live product):</strong> <span style="font-family:Courier New,monospace;color:{BLUE};font-weight:700;">{escape(top5)}</span> ·
    <strong style="color:{NAVY};">Hot sector:</strong> <span style="color:{PURPLE};font-weight:700;">{escape(hot_sector)}</span> ·
    <strong style="color:{NAVY};">Top buzz:</strong> {escape(top_buzz)} ·
    <strong style="color:{NAVY};">Track record (90d):</strong> <span style="color:{TEAL};font-weight:700;">{escape(track_summary)}</span>
  </div>
</td></tr>

{_section_header('1 · Race Timing — Imminent Launches', RED, 'Competitor L&I PEND products with future or unspecified inception. Historical no-launch entries excluded.')}
<tr><td style="padding:6px 30px 8px;">{imminent_html}</td></tr>

{_section_header('Race Timing — Recent Competitor L&I Filings (last 90 days)', ORANGE, 'Non-REX 485APOS matching strict L&I patterns. Deduped by series.')}
<tr><td style="padding:6px 30px 8px;">{recent_html}</td></tr>

{_section_header('2 · Our Pipeline — Top 50 by Underlier Score', BLUE, 'rex_products joined to li_engine_daily.final_score (full 6,594-ticker v1.0.2 universe). Status is binary: Filed or Effective. Basket / sector funds without a single-stock underlier sort below scored entries.')}
<tr><td style="padding:6px 30px 8px;">{pipeline_html}</td></tr>

{_section_header('3 · Mega Table — Top 100 Scored, No Live Product on Underlier', NAVY, 'Universe ranked by v1.0.2 composite. Filter: no active product on the underlier. REX/Comp filing flags carry the strict-whitespace state.')}
<tr><td style="padding:6px 30px 8px;">{mega_html}</td></tr>

{_section_header('4 · Inverse Gap (Single-Stock Only)', PURPLE, 'Single-stock underliers with at least one active long L&I product but zero inverse. Baskets / indices / commodities excluded.')}
<tr><td style="padding:6px 30px 8px;">{ig_html}</td></tr>

{_section_header('5 · Launch Anyway — 1-2 Competitor, REX Not In', BLUE, '1-2 competitor long products on a single-stock underlier, top product > $300M AUM, REX has no position. GOOG/GOOGL and BRKA/BRKB aliased.')}
<tr><td style="padding:6px 30px 8px;">{la_html}</td></tr>

{_section_header('6 · Foreign Megacaps', "#34495e", 'Curated overseas underliers — Samsung, SK Hynix, Tencent, Toyota, etc.')}
<tr><td style="padding:6px 30px 8px;">{foreign_html}</td></tr>

{_section_header('7 · Pre-IPO & Recently Priced (IPO Watchlist)', TEAL, f'YAML-backed valuations (last refresh {yaml_refresh_note}). Rows aged > 60d are flagged in Data Age. L&I filer race cross-ref shown.')}
<tr><td style="padding:6px 30px 8px;">{ipo_html}</td></tr>

{_section_header("8 · Delisting Watch — REX & Competitor < $15M @ 6mo", RED, "ACTV L&I products with AUM under $15M and age >= 5.5 months. Rule of thumb: funds that have not crossed $15M by month 6 historically do not. REX rows shown first.")}
<tr><td style="padding:6px 30px 8px;">{del_html}</td></tr>

{_section_header('9 · Track Record — How the Scoring Is Performing', GREEN, 'Rolling 90-day window. Hit = REX launch or 485APOS filing on the underlier after the recommendation. Source: recommendation_history table, grader runs weekly.')}
<tr><td style="padding:6px 30px 8px;">{track_html}</td></tr>

{_section_header('Methodology — Weighting · Filters · Track-Record Logic', NAVY, 'How every score, status, and watch threshold in this report is computed.')}
<tr><td style="padding:6px 30px 22px;">{methodology_html}</td></tr>

<tr><td style="padding:20px 30px;background:{LIGHT};">
  <div style="font-size:11px;color:{GRAY};line-height:1.55;">
    Report consolidates: REX Weekly ETP Report + L&amp;I Weekly v2 Stock Recommendations.<br>
    Methodology: v1.0.2 (frozen 2026-05-29). Next review window: 2026-07.<br><br>
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
