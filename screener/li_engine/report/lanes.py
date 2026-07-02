"""Shared lane-report builder — the one source for every T-REX System surface.

Each of the five candidate lanes is exposed as a :class:`Lane` with a loader and
a report-styled HTML renderer. ``build_all()`` returns all lanes at once, so the
public ``/tools/li/candidates`` page, the downloadable per-lane PDFs, and the
emailed T-REX report all render from the SAME builder and can never diverge.

Lanes:
  * ``whitespace``     — scored single-stock underliers with no live L&I product
  * ``inverse_gap``    — live long (>=$100M) but ZERO inverse anywhere
  * ``launch_anyway``  — 1-2 competitor longs, REX absent, top product >$100M
  * ``foreign``        — foreign-listed names + their L&I filer race
  * ``ipo``            — pre-IPO targets + their L&I filer race

Build-prove-retire: this module REUSES the proven loaders in
``trex_combined_v9`` (nothing there is moved or removed). The palette and the
filer-race sub-row renderers are reused too, so the styling matches the emailed
report and the foreign PDF exactly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any, Callable

import pandas as pd

from screener.li_engine.analysis import trex_combined_v9 as tc
from screener.li_engine.analysis.formatters import resolve_company_line

log = logging.getLogger(__name__)

# Reuse the report palette so page + PDF + email match exactly.
NAVY, BLUE, GREEN, ORANGE = tc.NAVY, tc.BLUE, tc.GREEN, tc.ORANGE
RED, GRAY, LIGHT, BORDER, PURPLE, TEAL = tc.RED, tc.GRAY, tc.LIGHT, tc.BORDER, tc.PURPLE, tc.TEAL

# On-brand tokens (STYLE_GUIDE.md T-B tables + palette) — used for the web page AND
# the PDF so the PDF is literally the same table look.
T_TEXT = "#0f172a"; T_BODY = "#374151"; T_MUTED = "#64748b"; T_CAP = "#94a3b8"
T_BORDER = "#e5e7eb"; T_ROWB = "#f1f5f9"; T_LINK = "#2563eb"; T_REXBG = "#f0fdf4"
POS = "#059669"; NEG = "#DC2626"; WARN = "#d97706"


# --------------------------------------------------------------------------- #
# Small safe helpers
# --------------------------------------------------------------------------- #
def _f(v, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        return float(v)
    except Exception:
        return default


def _s(v) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v)


def _cap_usd(v) -> str:
    """Format a raw-USD market cap → $1.38T / $299B / $820M."""
    v = _f(v)
    if v <= 0:
        return "—"
    b = v / 1e9
    if b >= 1000:
        return f"${b / 1000:.2f}T"
    if b >= 1:
        return f"${b:.0f}B"
    return f"${b * 1000:.0f}M"


def _aum_m(v) -> str:
    """Format an AUM already denominated in $millions → $1.24B / $305M."""
    v = _f(v)
    if v <= 0:
        return "—"
    if v >= 1000:
        return f"${v / 1000:.2f}B"
    return f"${v:.0f}M"


def _score_badge(score, found: bool = True) -> str:
    """Canonical 0-100 score, colored by band. '—' when the name isn't scored
    (e.g. a foreign / pre-IPO underlier absent from the US scoring universe)."""
    if not found:
        return f'<span style="color:{T_CAP};">—</span>'
    s = _f(score)
    c = POS if s >= 60 else (WARN if s >= 35 else T_MUTED)
    disp = "&lt;1" if 0 <= s < 1 else f"{s:.0f}"
    return f'<b style="color:{c};font-variant-numeric:tabular-nums;">{disp}</b>'


def _ul_link(code, text=None) -> str:
    """Underlier ticker as a click target → opens the lane-native filer modal (who
    has filed L&I on this name + effective dates), keyed by the canonical underlier."""
    c = _s(code).replace(" US", "").strip()
    label = escape(_s(text) if text is not None else c) or "—"
    if not c:
        return label
    ce = escape(tc._canon(c))
    return (f'<a href="#" onclick="return openUnderlierPanel(event,\'{ce}\')" '
            f'style="color:{T_LINK};text-decoration:none;font-weight:600;cursor:pointer;">{label}</a>')


def _ul_wrap(code, inner_html: str) -> str:
    """Wrap already-rendered cell HTML (a company/name cell) in the same filer-modal
    click target, keyed by the canonical name. Used by the foreign / pre-IPO lanes."""
    c = _s(code).strip()
    if not c:
        return inner_html
    ce = escape(tc._canon(c))
    return (f'<a href="#" onclick="return openUnderlierPanel(event,\'{ce}\')" '
            f'style="color:inherit;text-decoration:none;cursor:pointer;'
            f'border-bottom:1px dotted {T_LINK};">{inner_html}</a>')


def _status_pill(status) -> str:
    """Filed / Effective only, as an on-brand pill (STYLE_GUIDE status pills)."""
    s = str(status).strip().lower()
    if s == "effective":
        bg, fg, label = "#ecfdf5", POS, "Effective"
    else:
        bg, fg, label = "#eff6ff", T_LINK, "Filed"
    return (f'<span style="background:{bg};color:{fg};font-size:11px;font-weight:600;'
            f'border-radius:4px;padding:2px 8px;">{label}</span>')


def _rowget(row, key, default=None):
    try:
        if key in row.index:
            val = row[key]
            if isinstance(val, float) and pd.isna(val):
                return default
            return val
    except Exception:
        pass
    return default


# --------------------------------------------------------------------------- #
# Shared context — computed once, passed to every lane
# --------------------------------------------------------------------------- #
@dataclass
class LaneContext:
    single_stocks: set = field(default_factory=set)
    rex_pos: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    score_map: dict = field(default_factory=dict)  # canonical li_engine_daily.final_score by canon ticker
    generated_at: str = ""

    def score_of(self, ticker) -> float:
        """Canonical 0-100 score for a ticker/underlier (li_engine_daily.final_score)."""
        return float(self.score_map.get(tc._canon(ticker), 0.0) or 0.0)


def _load_score_map() -> dict:
    """Canonical score per underlier from the latest li_engine_daily run (0-100).

    This is the single source of truth for the displayed score (v1.0.1
    final_score) — NOT the whitespace parquet's internal composite_score, which
    is on a different, unbounded scale.
    """
    try:
        led = tc._df("SELECT ticker, final_score FROM li_engine_daily "
                     "WHERE run_date=(SELECT MAX(run_date) FROM li_engine_daily)")
        if led is None or led.empty:
            return {}
        led["t_canon"] = led["ticker"].apply(tc._canon)
        return led.groupby("t_canon")["final_score"].max().to_dict()
    except Exception as e:  # noqa: BLE001
        log.warning("score_map load failed: %s", e)
        return {}


def _compute_flags(counts: pd.DataFrame) -> dict:
    """Per-underlier has_live / has_rex_filing / has_comp_filing flags.

    Mirrors the derivation in ``trex_combined_v9.build`` so the whitespace lane
    surfaces exactly the same "no live product" universe as the emailed report.
    """
    if counts is None or counts.empty:
        return {}
    cc = counts.reset_index().rename(columns={"underlier": "u"})
    cc["u_canon"] = cc["u"].apply(tc._canon)
    cols = [
        "competitor_active_long", "competitor_active_short", "rex_active_long", "rex_active_short",
        "rex_filed_long", "rex_filed_short", "rex_extra_long", "rex_extra_short",
        "competitor_filed_long", "competitor_filed_short", "competitor_extra_long", "competitor_extra_short",
    ]
    for col in cols:
        if col not in cc.columns:
            cc[col] = 0
        cc[col] = cc[col].fillna(0)
    cc["has_live"] = (cc["competitor_active_long"] + cc["competitor_active_short"]
                      + cc["rex_active_long"] + cc["rex_active_short"]) > 0
    cc["has_rex_filing"] = (cc["rex_active_long"] + cc["rex_active_short"] + cc["rex_filed_long"]
                            + cc["rex_filed_short"] + cc["rex_extra_long"] + cc["rex_extra_short"]) > 0
    cc["has_comp_filing"] = (cc["competitor_filed_long"] + cc["competitor_filed_short"]
                             + cc["competitor_extra_long"] + cc["competitor_extra_short"]) > 0
    return cc.groupby("u_canon")[["has_live", "has_rex_filing", "has_comp_filing"]].any().to_dict("index")


def build_context() -> LaneContext:
    """Compute the shared inputs the lane loaders need, once."""
    try:
        single_stocks = tc._single_stock_set()
    except Exception as e:
        log.warning("single-stock set failed: %s", e)
        single_stocks = set()
    try:
        rex_pos = tc.load_rex_position()
    except Exception as e:
        log.warning("rex_position failed: %s", e)
        rex_pos = {}
    try:
        flags = _compute_flags(tc.load_counts())
    except Exception as e:
        log.warning("flags failed: %s", e)
        flags = {}
    return LaneContext(
        single_stocks=single_stocks,
        rex_pos=rex_pos,
        flags=flags,
        score_map=_load_score_map(),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
    )


# --------------------------------------------------------------------------- #
# HTML section scaffolding (matches the foreign PDF / emailed report style)
# --------------------------------------------------------------------------- #
def _section(title: str, subtitle: str, headers, body_html: str,
             accent: str = NAVY, count: int | None = None, aligns=None) -> str:
    cnt = f' <span style="font-size:12px;color:{T_CAP};font-weight:400;">{count}</span>' if count is not None else ""
    aligns = aligns or ["left"] * len(headers)
    thead = "".join(
        f'<th style="padding:9px 12px;text-align:{a};font-size:12px;font-weight:600;color:{T_BODY};'
        f'text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid {T_BORDER};white-space:nowrap;">'
        f'{escape(str(h))}</th>'
        for h, a in zip(headers, aligns)
    )
    cap = f'<div style="font-size:11px;color:{T_CAP};margin:0 0 8px;">{subtitle}</div>' if subtitle else ""
    dot = (f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
           f'background:{accent};margin-right:8px;vertical-align:middle;"></span>')
    return (
        f'<section style="margin:26px 0;">'
        f'<h2 style="font-size:16px;font-weight:600;color:{T_TEXT};margin:0 0 3px;">{dot}{escape(title)}{cnt}</h2>'
        f'{cap}'
        f'<div style="border:1px solid {T_BORDER};border-radius:6px;overflow:hidden;">'
        f'<table style="border-collapse:collapse;width:100%;background:#fff;">'
        f'<thead><tr style="background:#fff;text-align:left;">{thead}</tr></thead>'
        f'<tbody>{body_html}</tbody></table></div></section>'
    )


def _empty(title: str, subtitle: str, msg: str, accent: str = T_LINK) -> str:
    dot = (f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
           f'background:{accent};margin-right:8px;vertical-align:middle;"></span>')
    return (
        f'<section style="margin:26px 0;">'
        f'<h2 style="font-size:16px;font-weight:600;color:{T_TEXT};margin:0 0 3px;">{dot}{escape(title)}</h2>'
        f'<div style="font-size:12px;color:{T_MUTED};padding:10px 0;">{escape(msg)}</div>'
        f'</section>'
    )


def _tr(cells, aligns=None, rex=False) -> str:
    aligns = aligns or ["left"] * len(cells)
    bg = T_REXBG if rex else "#fff"
    tds = "".join(
        f'<td style="padding:10px 12px;text-align:{a};font-size:13px;color:{T_TEXT};'
        f'border-bottom:1px solid {T_ROWB};font-variant-numeric:tabular-nums;">{c}</td>'
        for c, a in zip(cells, aligns)
    )
    return f'<tr class="trex-row" style="background:{bg};">{tds}</tr>'


# --------------------------------------------------------------------------- #
# Lane loaders (thin wrappers over the proven trex_combined_v9 loaders)
# --------------------------------------------------------------------------- #
def load_whitespace(ctx: LaneContext) -> pd.DataFrame:
    scored = tc.load_scored()
    if scored is None or scored.empty:
        return pd.DataFrame()
    scored = scored.copy()
    scored["u_clean"] = scored["ticker"].apply(tc._canon)
    scored["has_live"] = scored["u_clean"].map(
        lambda t: ctx.flags.get(t, {}).get("has_live", False)).fillna(False)
    scored["has_comp_filing"] = scored["u_clean"].map(
        lambda t: ctx.flags.get(t, {}).get("has_comp_filing", False)).fillna(False)
    return scored[~scored["has_live"]].head(40)


def load_pipeline(ctx: LaneContext) -> pd.DataFrame:
    return tc.load_trex_2x_long_pipeline(ctx.single_stocks)


def load_inverse(ctx: LaneContext) -> pd.DataFrame:
    return tc.load_inverse_gap(ctx.single_stocks)


def load_launch_anyway(ctx: LaneContext) -> pd.DataFrame:
    return tc.load_launch_anyway(ctx.rex_pos, ctx.single_stocks)


def load_foreign(ctx: LaneContext) -> list:
    return tc.load_foreign_competition()


def load_ipo(ctx: LaneContext) -> list:
    return tc.load_preipo_competition()


# --------------------------------------------------------------------------- #
# Lane renderers
# --------------------------------------------------------------------------- #
def render_whitespace(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "Filing Whitespace"
    subtitle = ("Intent: single stocks with NO live L&amp;I product yet &mdash; pure first-mover "
                "whitespace, ranked by our engine score. File first. Click a name for the filer race.")
    headers = ["Ticker", "Company · Sector", "Mkt Cap", "Score", "Competitor"]
    aligns = ["left", "left", "right", "right", "left"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No whitespace candidates in the current scoring run.", T_LINK)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        tk = _s(_rowget(r, "ticker"))
        has_comp = bool(_rowget(r, "has_comp_filing", False))
        comp_c = (f'<span style="color:{ORANGE};font-weight:600;">competitor filed</span>'
                  if has_comp else f'<span style="color:{GRAY};">clean</span>')
        company = escape(resolve_company_line(tk, _s(_rowget(r, "sector"))))
        cells = [
            _ul_link(tk),
            f'<span style="font-size:11px;">{company}</span>',
            _aum_m(_rowget(r, "market_cap")),   # whitespace market_cap is in $millions
            _score_badge(ctx.score_of(tk)),     # canonical li_engine_daily.final_score (0-100)
            comp_c,
        ]
        body += _tr(cells, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=T_LINK, count=len(df), aligns=aligns)


def render_pipeline(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "REX 2X Queue"
    subtitle = ("Intent: our own T-REX 2X products already FILED but not yet launched &mdash; "
                "the live queue, with estimated effective dates. Click a name for the full filer race.")
    headers = ["Fund", "Underlier", "Dir", "Status", "Filed", "Est. Effective", "Score"]
    aligns = ["left", "left", "left", "left", "center", "center", "right"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No filed-not-launched T-REX 2X products right now.", POS)
    body = ""
    for _, r in df.iterrows():
        eff = _rowget(r, "eff_real")
        try:
            eff_s = eff.date().isoformat() if (eff is not None and pd.notna(eff)) else "—"
        except Exception:
            eff_s = "—"
        fund_name = _s(_rowget(r, "fund_name"))
        # rex_products.direction is NULL — derive Long/Inverse from the fund name.
        is_inv = bool(tc._INV_RE.search(fund_name))
        direction = "Inverse" if is_inv else "Long"
        underlier = _s(_rowget(r, "underlier_clean")) or _s(_rowget(r, "underlier"))
        # Score is '—' when the underlier isn't in the US scoring universe (foreign / pre-IPO).
        has_score = tc._canon(underlier) in ctx.score_map
        cells = [
            f'<span style="font-size:12px;">{escape(fund_name)}</span>',
            _ul_link(underlier),
            f'<span style="color:{WARN if is_inv else T_BODY};font-weight:600;">{direction}</span>',
            _status_pill(_rowget(r, "status_binary")),
            f'<span style="color:{T_MUTED};">{_s(_rowget(r, "initial_filing_date"))[:10] or "—"}</span>',
            f'<span style="color:{T_MUTED if eff_s == "—" else T_TEXT};">{eff_s}</span>',
            _score_badge(_rowget(r, "underlier_score"), found=has_score),
        ]
        body += _tr(cells, aligns=aligns, rex=True)
    return _section(title, subtitle, headers, body, accent=POS, count=len(df), aligns=aligns)


def render_inverse(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "Inverse Gap"
    subtitle = ("Intent: names with a live LONG L&amp;I (&ge;$100M) but NO inverse anywhere &mdash; "
                "the open short side to file. Click a name for the filer race.")
    headers = ["Underlier", "# Longs", "Top Long Fund", "Issuer", "Top Long AUM"]
    aligns = ["left", "center", "left", "left", "right"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No inverse gaps detected.", TEAL)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        cells = [
            _ul_link(_rowget(r, "underlier")),
            f'{int(_f(_rowget(r, "n_long")))}',
            f'<span style="font-size:11px;">{escape(_s(_rowget(r, "top_fund")))}</span>',
            escape(_s(_rowget(r, "top_issuer"))),
            _aum_m(_rowget(r, "top_aum")),
        ]
        body += _tr(cells, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=TEAL, count=len(df), aligns=aligns)


def render_launch_anyway(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "Launch Anyway"
    subtitle = ("Intent: contested names (1&ndash;2 competitor longs, REX absent, incumbent &gt;$100M) "
                "where the economics still justify a REX launch. Click a name for the filer race.")
    headers = ["Underlier", "Long Comp.", "Top Issuer", "Top Fund", "Top AUM", "First Launch", "Age (mo)"]
    aligns = ["left", "center", "left", "left", "right", "center", "right"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No launch-anyway candidates today.", PURPLE)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        mo = _f(_rowget(r, "months_old"))
        cells = [
            _ul_link(_rowget(r, "underlier")),
            f'{int(_f(_rowget(r, "n_long")))}',
            escape(_s(_rowget(r, "top_issuer"))),
            f'<span style="font-size:11px;">{escape(_s(_rowget(r, "top_fund")))}</span>',
            _aum_m(_rowget(r, "top_aum")),
            _s(_rowget(r, "first_launch")) or "—",
            f'{mo:.0f}' if mo else "—",
        ]
        body += _tr(cells, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=PURPLE, count=len(df), aligns=aligns)


def _comp_summary(race: list) -> str:
    """Collapsed competition cell: is a competitor LIVE? else the earliest effective date."""
    comps = [r for r in race if not r.get("rex")]
    if not comps:
        return f'<span style="color:{GRAY};">no competitor filings</span>'
    live = [r for r in comps if r.get("status") == "Effective"]
    if live:
        return f'<span style="color:{GREEN};font-weight:700;">&#9679; competitor live ({len(live)})</span>'
    dates = sorted(_s(r.get("date")) for r in comps if r.get("date"))
    if dates:
        return f'<span style="color:{BLUE};">earliest eff {escape(dates[0])}</span>'
    return f'<span style="color:{GRAY};">filed &middot; no eff date</span>'


def _race_section(items: list, title: str, subtitle: str, headers, aligns,
                  row_cells: Callable[[dict], list], accent: str, name_field: str) -> str:
    """Foreign + IPO lanes. The name cell is a click target → the lane-native filer
    modal (who has filed L&I on this name + effective dates), keyed by canonical name."""
    if not items:
        return _empty(title, subtitle, "No names in this lane right now.", accent)
    body = ""
    for it in items:
        rex_filed = any(r.get("rex") for r in (it.get("race") or []))
        cells = list(row_cells(it))
        cells[0] = _ul_wrap(it.get(name_field) or "", cells[0])
        body += _tr(cells, aligns=aligns, rex=rex_filed)
    return _section(title, subtitle, headers, body, accent=accent, count=len(items), aligns=aligns)


def render_foreign(items: list, ctx: LaneContext) -> str:
    title = "Foreign-Listed"
    subtitle = ("Intent: foreign-listed names (US-ADRs excluded) and who's racing to file 2X on them. "
                "REX-filed rows in green. Click a name for the full filer race + effective dates.")
    headers = ["Company", "Ticker", "Market", "Mkt Cap", "REX Status", "Competition"]
    aligns = ["left", "left", "left", "right", "left", "left"]

    def cells(it):
        return [
            f'<b>{escape(_s(it.get("name")))}</b>',
            escape(_s(it.get("ticker"))),
            f'<span style="font-size:11px;">{escape(_s(it.get("market")))}</span>',
            _cap_usd(it.get("cap")),
            tc._race_rex_badge(it.get("race") or []),
            _comp_summary(it.get("race") or []),
        ]

    return _race_section(items, title, subtitle, headers, aligns, cells, accent=BLUE, name_field="name")


def render_ipo(items: list, ctx: LaneContext) -> str:
    title = "Pre-IPO Targets"
    subtitle = ("Intent: private, pre-IPO names and the filer race forming before they list. "
                "REX-filed rows in green. Click a name for the full filer race + effective dates.")
    headers = ["Company", "Valuation", "As of", "S-1", "REX Status", "Competition"]
    aligns = ["left", "right", "center", "center", "left", "left"]

    def cells(it):
        val = it.get("valuation_usd")
        # ipo_watchlist.yaml stores valuation in $billions; _cap_usd expects raw USD.
        val_s = _cap_usd(_f(val) * 1e9) if val else "—"
        s1 = ('<span style="color:%s;font-weight:700;">S-1</span>' % GREEN) if it.get("s1") else "—"
        return [
            f'<b>{escape(_s(it.get("company")))}</b>',
            val_s,
            f'<span style="font-size:11px;color:{GRAY};">{escape(_s(it.get("as_of")))}</span>',
            s1,
            tc._race_rex_badge(it.get("race") or []),
            _comp_summary(it.get("race") or []),
        ]

    return _race_section(items, title, subtitle, headers, aligns, cells, accent=ORANGE, name_field="company")


# --------------------------------------------------------------------------- #
# Lane registry + public API
# --------------------------------------------------------------------------- #
@dataclass
class Lane:
    key: str
    title: str
    accent: str
    load: Callable[[LaneContext], Any]
    render: Callable[[Any, LaneContext], str]


LANES: list[Lane] = [
    Lane("pipeline", "REX 2X Queue", GREEN, load_pipeline, render_pipeline),
    Lane("whitespace", "Filing Whitespace", NAVY, load_whitespace, render_whitespace),
    Lane("inverse", "Inverse Gap", TEAL, load_inverse, render_inverse),
    Lane("launch_anyway", "Launch Anyway", PURPLE, load_launch_anyway, render_launch_anyway),
    Lane("foreign", "Foreign-Listed", BLUE, load_foreign, render_foreign),
    Lane("ipo", "Pre-IPO Targets", ORANGE, load_ipo, render_ipo),
]

LANE_BY_KEY = {ln.key: ln for ln in LANES}


def build_lane(key: str, ctx: LaneContext | None = None) -> dict:
    """Build a single lane → {key, title, accent, data, html}."""
    ctx = ctx or build_context()
    ln = LANE_BY_KEY.get(key)
    if ln is None:
        raise KeyError(f"unknown lane: {key!r} (have {list(LANE_BY_KEY)})")
    try:
        data = ln.load(ctx)
        html = ln.render(data, ctx)
    except Exception as e:  # never let one lane take down the surface
        log.warning("lane %s failed: %s", key, e)
        data, html = None, _empty(ln.title, "", f"lane failed to build: {e}", ln.accent)
    return {"key": ln.key, "title": ln.title, "accent": ln.accent, "data": data, "html": html}


def build_races(built: dict) -> dict:
    """Drill-down race map: canonical code -> filer race [{issuer,status,dir,date,rex}].

    US names come from the disciplined ``load_underlier_races()``; foreign / pre-IPO
    reuse the race already computed on their lane items, keyed by canonical name. This
    is what the on-page filer modal reads — the lane's own data (with effective dates),
    never the Bloomberg-live ``/operations`` modal.
    """
    races: dict = {}
    try:
        races.update(tc.load_underlier_races())
    except Exception as e:  # noqa: BLE001
        log.warning("underlier races failed: %s", e)
    for key in ("foreign", "ipo"):
        data = (built.get(key) or {}).get("data") or []
        for it in data:
            nm = it.get("name") or it.get("company") or it.get("ticker")
            race = it.get("race") or []
            if nm and race:
                races[tc._canon(nm)] = race
    return races


def build_all(ctx: LaneContext | None = None) -> dict:
    """Build every lane → {key: {key,title,accent,data,html}} + shared context + races."""
    ctx = ctx or build_context()
    out = {ln.key: build_lane(ln.key, ctx) for ln in LANES}
    out["_ctx"] = ctx
    out["_races"] = build_races(out)
    return out
