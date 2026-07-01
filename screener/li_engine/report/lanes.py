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
from screener.li_engine.analysis.formatters import (
    fmt_oi,
    fmt_pct,
    pretty_themes,
    resolve_company_line,
)

log = logging.getLogger(__name__)

# Reuse the report palette so page + PDF + email match exactly.
NAVY, BLUE, GREEN, ORANGE = tc.NAVY, tc.BLUE, tc.GREEN, tc.ORANGE
RED, GRAY, LIGHT, BORDER, PURPLE, TEAL = tc.RED, tc.GRAY, tc.LIGHT, tc.BORDER, tc.PURPLE, tc.TEAL


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


def _score_badge(score) -> str:
    s = _f(score)
    c = GREEN if s >= 40 else (ORANGE if s >= 20 else GRAY)
    return f'<b style="color:{c};">{s:.0f}</b>'


def _ret(v) -> str:
    v = _f(v)
    c = GREEN if v > 0 else (RED if v < 0 else GRAY)
    return f'<span style="color:{c};">{v * 100:+.0f}%</span>' if v else "—"


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
    generated_at: str = ""


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
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
    )


# --------------------------------------------------------------------------- #
# HTML section scaffolding (matches the foreign PDF / emailed report style)
# --------------------------------------------------------------------------- #
def _section(title: str, subtitle: str, headers, body_html: str,
             accent: str = NAVY, count: int | None = None, aligns=None) -> str:
    cnt = f' <span style="font-size:11px;color:{GRAY};font-weight:400;">({count})</span>' if count is not None else ""
    aligns = aligns or ["left"] * len(headers)
    thead = "".join(
        f'<th style="padding:5px 9px;text-align:{a};">{escape(str(h))}</th>'
        for h, a in zip(headers, aligns)
    )
    return (
        f'<section style="margin:22px 0;">'
        f'<h2 style="font-size:15px;color:{accent};margin:0 0 2px;">{escape(title)}{cnt}</h2>'
        f'<div style="font-size:11px;color:{GRAY};margin:0 0 6px;line-height:1.4;">{subtitle}</div>'
        f'<table style="border-collapse:collapse;width:100%;font-size:12px;">'
        f'<thead><tr style="background:{accent};color:#fff;text-align:left;font-size:10px;'
        f'text-transform:uppercase;letter-spacing:.03em;">{thead}</tr></thead>'
        f'<tbody>{body_html}</tbody></table></section>'
    )


def _empty(title: str, subtitle: str, msg: str, accent: str = NAVY) -> str:
    return (
        f'<section style="margin:22px 0;">'
        f'<h2 style="font-size:15px;color:{accent};margin:0 0 2px;">{escape(title)}</h2>'
        f'<div style="font-size:11px;color:{GRAY};margin:0 0 6px;">{subtitle}</div>'
        f'<div style="font-size:12px;color:{GRAY};font-style:italic;padding:10px 0;">{escape(msg)}</div>'
        f'</section>'
    )


def _tr(cells, bg="#fff", aligns=None, rex=False) -> str:
    aligns = aligns or ["left"] * len(cells)
    bdr = f"border-left:3px solid {GREEN};" if rex else ""
    tds = "".join(
        f'<td style="padding:4px 9px;text-align:{a};border-bottom:1px solid {BORDER};">{c}</td>'
        for c, a in zip(cells, aligns)
    )
    return f'<tr style="background:{bg};{bdr}">{tds}</tr>'


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
    subtitle = ("Scored single-stock underliers with <b>no live L&amp;I product</b> — REX's clearest "
                "open lanes, ranked by the frozen v1.0.1 composite score. "
                "&#9873; = a competitor has already filed here.")
    headers = ["Ticker", "Company · Sector", "Score", "Mkt Cap", "RVol 90d", "Ret 1Y", "Mentions 24h", "Comp?", "Themes"]
    aligns = ["left", "left", "right", "right", "right", "right", "right", "center", "left"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No whitespace candidates in the current scoring run.", NAVY)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        tk = _s(_rowget(r, "ticker"))
        comp = "&#9873;" if bool(_rowget(r, "has_comp_filing", False)) else ""
        comp_c = f'<span style="color:{ORANGE};font-weight:700;">{comp}</span>' if comp else "—"
        company = escape(resolve_company_line(tk, _s(_rowget(r, "sector"))))
        cells = [
            f'<b>{escape(tk.replace(" US", ""))}</b>',
            f'<span style="font-size:11px;">{company}</span>',
            _score_badge(_rowget(r, "composite_score")),
            _cap_usd(_rowget(r, "market_cap")),
            fmt_pct(_rowget(r, "rvol_90d")),
            _ret(_rowget(r, "ret_1y")),
            f'{int(_f(_rowget(r, "mentions_24h")))}' if _f(_rowget(r, "mentions_24h")) else "—",
            comp_c,
            f'<span style="font-size:10px;color:{GRAY};">{escape(pretty_themes(_rowget(r, "themes")))}</span>',
        ]
        body += _tr(cells, bg="#fff" if i % 2 == 0 else LIGHT, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=NAVY, count=len(df), aligns=aligns)


def render_inverse(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "Inverse Gap"
    subtitle = ("Single-stock underliers with a live long (&ge;$100M) and <b>zero inverse</b> of any "
                "size anywhere — the clearest inverse whitespace, ranked by the top long's AUM.")
    headers = ["Underlier", "# Longs", "Top Long Fund", "Issuer", "Top Long AUM"]
    aligns = ["left", "center", "left", "left", "right"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No inverse gaps detected.", TEAL)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        cells = [
            f'<b>{escape(_s(_rowget(r, "underlier")))}</b>',
            f'{int(_f(_rowget(r, "n_long")))}',
            f'<span style="font-size:11px;">{escape(_s(_rowget(r, "top_fund")))}</span>',
            escape(_s(_rowget(r, "top_issuer"))),
            _aum_m(_rowget(r, "top_aum")),
        ]
        body += _tr(cells, bg="#fff" if i % 2 == 0 else LIGHT, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=TEAL, count=len(df), aligns=aligns)


def render_launch_anyway(df: pd.DataFrame, ctx: LaneContext) -> str:
    title = "Launch Anyway"
    subtitle = ("Underliers with only <b>1&ndash;2 competitor longs</b>, REX absent, and a top product "
                "&gt;$100M &mdash; proven demand with room to enter. Ranked by the incumbent's AUM.")
    headers = ["Underlier", "Long Comp.", "Top Issuer", "Top Fund", "Top AUM", "First Launch", "Age (mo)"]
    aligns = ["left", "center", "left", "left", "right", "center", "right"]
    if df is None or df.empty:
        return _empty(title, subtitle, "No launch-anyway candidates today.", PURPLE)
    body = ""
    for i, (_, r) in enumerate(df.iterrows()):
        mo = _f(_rowget(r, "months_old"))
        cells = [
            f'<b>{escape(_s(_rowget(r, "underlier")))}</b>',
            f'{int(_f(_rowget(r, "n_long")))}',
            escape(_s(_rowget(r, "top_issuer"))),
            f'<span style="font-size:11px;">{escape(_s(_rowget(r, "top_fund")))}</span>',
            _aum_m(_rowget(r, "top_aum")),
            _s(_rowget(r, "first_launch")) or "—",
            f'{mo:.0f}' if mo else "—",
        ]
        body += _tr(cells, bg="#fff" if i % 2 == 0 else LIGHT, aligns=aligns)
    return _section(title, subtitle, headers, body, accent=PURPLE, count=len(df), aligns=aligns)


def _race_section(items: list, title: str, subtitle: str, headers, aligns,
                  row_cells: Callable[[dict], list], accent: str) -> str:
    """Shared renderer for the foreign + IPO lanes (row + indented filer race)."""
    ncols = len(headers)
    if not items:
        return _empty(title, subtitle, "No names in this lane right now.", accent)
    body = ""
    for i, it in enumerate(items):
        race = it.get("race") or []
        rex_filed = any(r.get("rex") for r in race)
        cells = row_cells(it)
        body += _tr(cells, bg="#f0fdf4" if rex_filed else ("#fff" if i % 2 == 0 else LIGHT),
                    aligns=aligns, rex=rex_filed)
        body += tc._race_subrows(race, ncols)
    return _section(title, subtitle, headers, body, accent=accent, count=len(items), aligns=aligns)


def render_foreign(items: list, ctx: LaneContext) -> str:
    title = "Foreign-Listed"
    subtitle = ("Foreign-<b>listed</b> single stocks (US-ADR names excluded &mdash; those can already be "
                "made into a US 2x) with their full L&amp;I filer race. Green = REX has filed. "
                "Filed names first, then open megacap whitespace by market cap.")
    headers = ["Company", "Ticker", "Market", "Sector", "Mkt Cap", "REX Status", "Comp."]
    aligns = ["left", "left", "left", "left", "right", "left", "center"]

    def cells(it):
        return [
            f'<b>{escape(_s(it.get("name")))}</b>',
            escape(_s(it.get("ticker"))),
            f'<span style="font-size:11px;">{escape(_s(it.get("market")))}</span>',
            f'<span style="font-size:11px;color:{GRAY};">{escape(_s(it.get("sector")))}</span>',
            _cap_usd(it.get("cap")),
            tc._race_rex_badge(it.get("race") or []),
            f'{int(it.get("ncomp", 0))}',
        ]

    return _race_section(items, title, subtitle, headers, aligns, cells, accent=BLUE)


def render_ipo(items: list, ctx: LaneContext) -> str:
    title = "Pre-IPO Targets"
    subtitle = ("Genuinely-private pre-IPO names from the watchlist with their L&amp;I filer race, sourced "
                "valuation and S-1 status. Green = REX has filed. Ranked by valuation.")
    headers = ["Company", "Valuation", "As of", "S-1", "REX Status", "Comp."]
    aligns = ["left", "right", "center", "center", "left", "center"]

    def cells(it):
        val = it.get("valuation_usd")
        val_s = _cap_usd(val) if val else "—"
        s1 = ('<span style="color:%s;font-weight:700;">S-1</span>' % GREEN) if it.get("s1") else "—"
        return [
            f'<b>{escape(_s(it.get("company")))}</b>',
            val_s,
            f'<span style="font-size:11px;color:{GRAY};">{escape(_s(it.get("as_of")))}</span>',
            s1,
            tc._race_rex_badge(it.get("race") or []),
            f'{int(it.get("ncomp", 0))}',
        ]

    return _race_section(items, title, subtitle, headers, aligns, cells, accent=ORANGE)


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


def build_all(ctx: LaneContext | None = None) -> dict:
    """Build every lane → {key: {key,title,accent,data,html}} + shared context."""
    ctx = ctx or build_context()
    out = {ln.key: build_lane(ln.key, ctx) for ln in LANES}
    out["_ctx"] = ctx
    return out
