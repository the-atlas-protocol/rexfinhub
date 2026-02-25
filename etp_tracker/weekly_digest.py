"""
REX ETF Weekly Report - Executive Email Digest v5

Email-client-compatible HTML digest (inline styles, table layout, no JS).
Combines Bloomberg market data (ETF-only) and SEC filing activity into a
comprehensive executive-ready weekly email for REX Financial team members.

Sections:
  1. Header
  2. Filing Activity
  3. REX Scorecard (4 KPIs: AUM, 1W Flows, 1M Flows, Products)
  4. AUM by Suite (donut chart + legend)
  5. 1W Flows by Suite (diverging bar chart)
  6. Winners, Losers & Yielders (vertical, with 1W Flow)
  7. Market Landscape (5 categories: 3 KPIs + issuer table w/ share & launches)
  8. ETF Universe (total market donut chart by category)
  9. Dashboard CTA
  10. Footer

Supports format="full" (default). format="flash" reserved for future 1-screen summary.
"""
from __future__ import annotations

import logging
import math
import smtplib
from datetime import datetime, timedelta, date as date_type
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pandas as pd

from etp_tracker.email_alerts import (
    _NAVY, _GREEN, _ORANGE, _RED, _BLUE, _GRAY, _LIGHT, _BORDER, _WHITE,
    _esc, _load_recipients, _get_smtp_config,
)

log = logging.getLogger(__name__)

# Suites to exclude from the digest
_EXCLUDED_SUITES = {"MicroSector", "L&I Other"}

# Suite colors (v3 palette)
_SUITE_COLORS = {
    "T-REX": "#e74c3c",
    "Growth & Income": "#f39c12",
    "Premium Income": "#0984e3",
    "Crypto": "#8e44ad",
    "Thematic": "#27ae60",
    "Defined Outcome": "#00b894",
}

_SUITE_ABBREVS = {
    "T-REX": "T-REX",
    "Growth & Income": "G&I",
    "Premium Income": "Prem",
    "Crypto": "Crypto",
    "Thematic": "Thm",
    "Defined Outcome": "DO",
}

# Income categories for yield filtering
_INCOME_CATEGORIES = {"Income - Single Stock", "Income - Index/Basket/ETF Based"}

# Category landscape: (internal_name, display_name, border_color)
_LANDSCAPE_CATS = [
    ("Leverage & Inverse - Single Stock", "Leveraged Single Stock", "#e74c3c"),
    ("Income - Single Stock", "Covered Call (Single Stock)", "#f39c12"),
    ("Income - Index/Basket/ETF Based", "Covered Call (Index/ETF)", "#0984e3"),
    ("Crypto", "Crypto", "#8e44ad"),
    ("Thematic", "Thematic", "#27ae60"),
]

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------
_SECTION_TITLE = (
    f"font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 12px 0;"
    f"padding-bottom:8px;border-bottom:2px solid {_BLUE};"
)
_KPI_BOX = f"padding:12px 8px;background:{_LIGHT};border-radius:8px;text-align:center;"
_KPI_VALUE = f"font-size:24px;font-weight:700;color:{_NAVY};"
_KPI_LABEL = f"font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;"
_TABLE_HEADER = (
    f"padding:8px 12px;background:{_NAVY};color:{_WHITE};"
    f"font-size:12px;font-weight:600;text-align:left;"
)
_TABLE_HEADER_RIGHT = (
    f"padding:8px 12px;background:{_NAVY};color:{_WHITE};"
    f"font-size:12px;font-weight:600;text-align:right;"
)
_TABLE_CELL = f"padding:6px 12px;border-bottom:1px solid {_BORDER};font-size:12px;"
_TABLE_CELL_RIGHT = (
    f"padding:6px 12px;border-bottom:1px solid {_BORDER};"
    f"font-size:12px;text-align:right;"
)

_DEFAULT_DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_change(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return f'<span style="color:{_GRAY};">--</span>'
    color = _GREEN if val >= 0 else _RED
    sign = "+" if val >= 0 else ""
    return f'<span style="color:{color};font-weight:600;">{sign}{val:.1f}%</span>'


def _fmt_return(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return f'<span style="color:{_GRAY};">--</span>'
    color = _GREEN if val >= 0 else _RED
    sign = "+" if val >= 0 else ""
    return f'<span style="color:{color};font-weight:600;">{sign}{val:.2f}%</span>'


def _fmt_currency_safe(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    if abs(val) >= 1_000:
        return f"${val / 1_000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    return f"${val:.2f}M"


def _fmt_flow_safe(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency_safe(val)}"


def _flow_color(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return _GRAY
    return _GREEN if val >= 0 else _RED


def _filter_suites(suites: list[dict]) -> list[dict]:
    return [s for s in suites if s.get("rex_name", s.get("name", "")) not in _EXCLUDED_SUITES]


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------
def _gather_market_data() -> dict | None:
    """Gather Bloomberg data: ETF-only summary + raw DataFrame for category breakdowns."""
    try:
        from webapp.services.market_data import (
            data_available, get_rex_summary, get_data_as_of,
            get_category_summary, get_master_data,
        )
        if not data_available():
            return None

        summary = get_rex_summary(fund_structure="ETF")
        master = get_master_data()

        # Filter master to ETF-only for rex_df
        fund_type_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            etf_master = master[master[fund_type_col] == "ETF"].copy()
        else:
            etf_master = master.copy()

        rex_df = etf_master[etf_master["is_rex"] == True].copy()
        if "ticker_clean" in rex_df.columns:
            rex_df = rex_df.drop_duplicates(subset=["ticker_clean"], keep="first")

        # Gather category landscape data for the 5 categories
        landscape = {}
        for cat_name, display_name, color in _LANDSCAPE_CATS:
            try:
                cat_data = get_category_summary(cat_name)
                landscape[cat_name] = cat_data
            except Exception as exc:
                log.warning("Category summary failed for %s: %s", cat_name, exc)

        return {
            "kpis": summary.get("kpis", {}),
            "suites": summary.get("suites", []),
            "flow_chart": summary.get("flow_chart", {}),
            "perf_metrics": summary.get("perf_metrics", {}),
            "data_as_of": get_data_as_of(),
            "rex_df": rex_df,
            "master": master,
            "landscape": landscape,
        }
    except Exception as exc:
        log.warning("Weekly digest: Bloomberg data unavailable: %s", exc)
        return None


def _gather_filing_data(db_session, days: int = 7) -> dict:
    from sqlalchemy import func, select
    from webapp.models import Trust, Filing, FundStatus

    cutoff = date_type.today() - timedelta(days=days)

    # Fund filings: 485* forms only (prospectus-related)
    fund_filings = db_session.execute(
        select(func.count(Filing.id))
        .where(Filing.filing_date >= cutoff)
        .where(Filing.form.ilike("485%"))
    ).scalar() or 0

    newly_effective = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "EFFECTIVE")
        .where(FundStatus.effective_date >= cutoff)
    ).scalar() or 0

    # Pending funds: total count of PENDING status
    pending_funds = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "PENDING")
    ).scalar() or 0

    trust_count = db_session.execute(
        select(func.count(Trust.id)).where(Trust.is_active == True)
    ).scalar() or 0

    return {
        "fund_filings": fund_filings,
        "newly_effective": newly_effective,
        "pending_funds": pending_funds,
        "trust_count": trust_count,
        "cutoff": cutoff.isoformat(),
    }


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_header(week_ending: str, data_as_of: str = "") -> str:
    subtitle = f"Week ending {_esc(week_ending)}"
    return f"""
<tr><td style="background:{_NAVY};padding:28px 30px;">
  <div style="color:{_WHITE};font-size:24px;font-weight:700;margin-bottom:4px;">
    REX ETF Weekly Report
  </div>
  <div style="color:rgba(255,255,255,0.7);font-size:13px;">{subtitle}</div>
</td></tr>"""


def _render_filing_activity(filing_data: dict) -> str:
    filings = filing_data.get("fund_filings", 0)
    effective = filing_data.get("newly_effective", 0)
    pending = filing_data.get("pending_funds", 0)
    trust_count = filing_data.get("trust_count", 0)

    return f"""
<tr><td style="padding:20px 30px 10px;">
  <div style="{_SECTION_TITLE}">Filing Activity (Last 7 Days)</div>
  <div style="font-size:12px;color:{_GRAY};margin-bottom:10px;">
    Scanning {trust_count} trusts across the ETP landscape
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#e3f2fd;border-left:4px solid {_BLUE};border-radius:0 8px 8px 0;">
    <tr><td style="padding:15px 20px;">
      <table cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="padding-right:28px;font-size:14px;">
          <span style="font-size:22px;font-weight:700;color:{_BLUE};">{filings}</span> fund filings
        </td>
        <td style="padding-right:28px;font-size:14px;">
          <span style="font-size:22px;font-weight:700;color:{_GREEN};">{effective}</span> newly effective
        </td>
        <td style="font-size:14px;">
          <span style="font-size:22px;font-weight:700;color:{_ORANGE};">{pending}</span> pending funds
        </td>
      </tr></table>
    </td></tr>
  </table>
</td></tr>"""


def _render_scorecard(kpis: dict, rex_df: pd.DataFrame = None) -> str:
    total_aum = kpis.get("total_aum_fmt", "$0")
    flow_1w = kpis.get("flow_1w_fmt", "$0")
    flow_1w_val = kpis.get("flow_1w", 0)
    flow_1m = kpis.get("flow_1m_fmt", "$0")
    flow_1m_val = kpis.get("flow_1m", 0)
    num_products = kpis.get("num_products", kpis.get("count", 0))

    # AUM MoM sub-label
    aum_mom = kpis.get("aum_mom_pct", 0)
    aum_sub = ""
    if aum_mom and not (isinstance(aum_mom, float) and math.isnan(aum_mom)):
        mom_color = _GREEN if aum_mom >= 0 else _RED
        mom_sign = "+" if aum_mom >= 0 else ""
        aum_sub = (
            f'<div style="font-size:11px;color:{mom_color};font-weight:600;margin-top:2px;">'
            f'{mom_sign}{aum_mom:.1f}% MoM</div>'
        )

    # New products sub-label (inception_date in last 7 days)
    products_sub = ""
    if rex_df is not None and not rex_df.empty and "inception_date" in rex_df.columns:
        cutoff_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
        inception = pd.to_datetime(rex_df["inception_date"], errors="coerce")
        new_count = int((inception >= cutoff_7d).sum())
        if new_count > 0:
            products_sub = (
                f'<div style="font-size:11px;color:{_GREEN};font-weight:600;margin-top:2px;">'
                f'{new_count} launched this week</div>'
            )
        else:
            products_sub = (
                f'<div style="font-size:11px;color:{_GRAY};margin-top:2px;">'
                f'0 launched this week</div>'
            )

    def _card(value: str, label: str, color: str = _NAVY, sub_label: str = "") -> str:
        return (
            f'<td width="23%" align="center" style="{_KPI_BOX}">'
            f'<div style="font-size:24px;font-weight:700;color:{color};">{value}</div>'
            f'{sub_label}'
            f'<div style="{_KPI_LABEL}">{_esc(label)}</div>'
            f'</td>'
        )

    return f"""
<tr><td style="padding:20px 30px 10px;">
  <div style="{_SECTION_TITLE}">REX Scorecard</div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      {_card(total_aum, "Total AUM", sub_label=aum_sub)}
      <td width="2%"></td>
      {_card(flow_1w, "1W Net Flows", _flow_color(flow_1w_val))}
      <td width="2%"></td>
      {_card(flow_1m, "1M Net Flows", _flow_color(flow_1m_val))}
      <td width="2%"></td>
      {_card(str(num_products), "Products", sub_label=products_sub)}
    </tr>
  </table>
</td></tr>"""


def _render_scorecard_unavailable() -> str:
    return f"""
<tr><td style="padding:20px 30px 10px;">
  <div style="{_SECTION_TITLE}">REX Scorecard</div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="{_KPI_BOX}padding:24px;">
      <div style="font-size:14px;color:{_GRAY};text-align:center;">
        Market data not available. Bloomberg data file has not been loaded.
      </div>
    </td></tr>
  </table>
</td></tr>"""


def _render_donut_svg(segments: list[tuple[str, float, str]], total_label: str = "",
                      size: int = 150, radius: int = 55, stroke: int = 25) -> str:
    """Render an inline SVG donut chart. segments = [(name, value, color), ...]."""
    total = sum(v for _, v, _ in segments) or 1
    circumference = 2 * math.pi * radius
    cx = cy = size // 2
    arcs = []
    offset = 0
    for name, val, color in segments:
        pct = val / total
        dash = pct * circumference
        gap = circumference - dash
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" '
            f'stroke-dasharray="{dash:.1f} {gap:.1f}" '
            f'stroke-dashoffset="{-offset:.1f}" '
            f'transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += dash
    center_text = ""
    if total_label:
        center_text = (
            f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" '
            f'font-size="18" font-weight="700" fill="{_NAVY}" '
            f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif">'
            f'{_esc(total_label)}</text>'
            f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" '
            f'font-size="9" fill="{_GRAY}" '
            f'font-family="-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif">'
            f'TOTAL AUM</text>'
        )
    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(arcs)}{center_text}</svg>'
    )


def _render_aum_stacked_bar(suites: list[dict], rex_df: pd.DataFrame = None) -> str:
    """AUM by Suite as an SVG donut chart with legend."""
    filtered = _filter_suites(suites)
    if not filtered:
        return ""
    sorted_suites = sorted(filtered, key=lambda s: s.get("kpis", {}).get("total_aum", 0), reverse=True)
    total = sum(s.get("kpis", {}).get("total_aum", 0) for s in sorted_suites)
    if total <= 0:
        return ""

    segments = []
    legend_rows = []
    for s in sorted_suites:
        name = s.get("rex_name", s.get("name", ""))
        aum = s.get("kpis", {}).get("total_aum", 0)
        pct = (aum / total * 100) if total > 0 else 0
        color = _SUITE_COLORS.get(name, _BLUE)
        if pct < 0.5:
            continue
        segments.append((name, aum, color))
        legend_rows.append(
            f'<tr>'
            f'<td style="padding:3px 6px;width:14px;">'
            f'<div style="width:10px;height:10px;background:{color};border-radius:2px;"></div></td>'
            f'<td style="padding:3px 6px;font-size:11px;font-weight:600;">{_esc(name)}</td>'
            f'<td style="padding:3px 6px;font-size:11px;text-align:right;">{_fmt_currency_safe(aum)}</td>'
            f'<td style="padding:3px 6px;font-size:10px;text-align:right;color:{_GRAY};">{pct:.0f}%</td>'
            f'</tr>'
        )

    if not segments:
        return ""

    donut = _render_donut_svg(segments, _fmt_currency_safe(total))

    return f"""
<tr><td style="padding:15px 30px;">
  <div style="{_SECTION_TITLE}">AUM by Suite</div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="45%" align="center" style="padding:8px;">{donut}</td>
      <td width="55%" valign="middle">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {''.join(legend_rows)}
        </table>
      </td>
    </tr>
  </table>
</td></tr>"""


def _render_bar_chart(title: str, items: list[tuple[str, float]], subtitle: str = "") -> str:
    """Render a horizontal bar chart. items = [(label, value), ...]"""
    if not items:
        return ""

    max_abs = max(abs(v) for _, v in items) if items else 1
    if max_abs == 0:
        max_abs = 1

    sub_html = f'<div style="font-size:12px;color:{_GRAY};margin-bottom:8px;">{_esc(subtitle)}</div>' if subtitle else ""
    rows = []
    for label, val in items:
        color = _SUITE_COLORS.get(label, _BLUE)
        bar_width = max(abs(val) / max_abs * 100, 2)
        val_fmt = _fmt_flow_safe(val)
        val_color = _flow_color(val)

        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;font-size:12px;font-weight:600;width:120px;'
            f'white-space:nowrap;">{_esc(label)}</td>'
            f'<td style="padding:4px 8px;">'
            f'<div style="background:{_LIGHT};border-radius:4px;overflow:hidden;">'
            f'<div style="background:{color};height:18px;width:{bar_width:.1f}%;'
            f'border-radius:4px;min-width:4px;"></div>'
            f'</div></td>'
            f'<td style="padding:4px 8px;font-size:12px;text-align:right;width:80px;'
            f'font-weight:600;color:{val_color};">{val_fmt}</td>'
            f'</tr>'
        )

    return f"""
<tr><td style="padding:15px 30px;">
  <div style="{_SECTION_TITLE}">{_esc(title)}</div>
  {sub_html}
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    {''.join(rows)}
  </table>
</td></tr>"""


def _render_diverging_bar_chart(title: str, items: list[tuple[str, float]], subtitle: str = "") -> str:
    """Diverging horizontal bar chart: bars grow left/right from a center line."""
    if not items:
        return ""

    max_abs = max(abs(v) for _, v in items) if items else 1
    if max_abs == 0:
        max_abs = 1

    sub_html = (
        f'<div style="font-size:12px;color:{_GRAY};margin-bottom:8px;">{_esc(subtitle)}</div>'
        if subtitle else ""
    )

    rows = []
    for label, val in items:
        bar_pct = abs(val) / max_abs * 100
        val_fmt = _fmt_flow_safe(val)
        val_color = _flow_color(val)
        bar_color = _BLUE if val >= 0 else _RED

        if val < 0:
            # Negative: bar in left half, right-aligned (grows leftward from center)
            left_cell = (
                f'<td style="padding:0;">'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'<tr><td width="{100 - bar_pct:.1f}%"></td>'
                f'<td width="{bar_pct:.1f}%" style="background:{bar_color};'
                f'height:16px;border-radius:3px 0 0 3px;"></td></tr>'
                f'</table></td>'
            )
            right_cell = '<td style="padding:0;"></td>'
        elif val > 0:
            # Positive: bar in right half, left-aligned (grows rightward from center)
            left_cell = '<td style="padding:0;"></td>'
            right_cell = (
                f'<td style="padding:0;">'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'<tr><td width="{bar_pct:.1f}%" style="background:{bar_color};'
                f'height:16px;border-radius:0 3px 3px 0;"></td>'
                f'<td width="{100 - bar_pct:.1f}%"></td></tr>'
                f'</table></td>'
            )
        else:
            left_cell = '<td style="padding:0;"></td>'
            right_cell = '<td style="padding:0;"></td>'

        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;font-size:12px;font-weight:600;width:110px;'
            f'white-space:nowrap;">{_esc(label)}</td>'
            f'<td width="40%" style="padding:4px 0;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr>{left_cell}</tr></table></td>'
            f'<td width="2px" style="background:{_BORDER};"></td>'
            f'<td width="40%" style="padding:4px 0;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr>{right_cell}</tr></table></td>'
            f'<td style="padding:4px 8px;font-size:12px;text-align:right;width:80px;'
            f'font-weight:600;color:{val_color};">{val_fmt}</td>'
            f'</tr>'
        )

    return f"""
<tr><td style="padding:15px 30px;">
  <div style="{_SECTION_TITLE}">{_esc(title)}</div>
  {sub_html}
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    {''.join(rows)}
  </table>
</td></tr>"""


def _render_flow_chart(suites: list[dict], flow_chart: dict) -> str:
    """1W Flows by suite as a diverging bar chart."""
    suite_names = flow_chart.get("suites", [])
    flow_1w = flow_chart.get("flow_1w", [])
    if not suite_names or not flow_1w:
        # Fallback to 1M if 1W not available
        flow_1w = flow_chart.get("flow_1m", [])
    if not suite_names or not flow_1w:
        return ""

    items = []
    for name, val in zip(suite_names, flow_1w):
        if name in _EXCLUDED_SUITES:
            continue
        items.append((name, val))

    # Sort by value descending (most positive at top)
    items.sort(key=lambda x: x[1], reverse=True)
    total_flow = sum(v for _, v in items)

    return _render_diverging_bar_chart("1W Net Flows by Suite", items,
                                       subtitle=f"Total: {_fmt_flow_safe(total_flow)}")


def _render_winners_losers_yielders(perf_metrics: dict, rex_df: pd.DataFrame) -> str:
    """Winners, Losers & Yielders stacked vertically with column headers and 1W Flow."""
    ret_data = perf_metrics.get("return_1w", {})
    winners = ret_data.get("best5", []) if ret_data else []
    losers = ret_data.get("worst5", []) if ret_data else []

    yield_data = perf_metrics.get("yield", {})
    all_yielders = yield_data.get("best5", []) if yield_data else []

    # Filter yielders to income-suite tickers
    if all_yielders and not rex_df.empty and "category_display" in rex_df.columns:
        income_tickers = set(
            rex_df[rex_df["category_display"].isin(_INCOME_CATEGORIES)]["ticker_clean"]
        ) if "ticker_clean" in rex_df.columns else set()
        yielders = [y for y in all_yielders if y.get("ticker", "") in income_tickers]
    else:
        yielders = all_yielders

    if not winners and not losers and not yielders:
        return ""

    # Build 1W flow lookup from rex_df
    flow_lookup: dict[str, float] = {}
    if not rex_df.empty and "ticker_clean" in rex_df.columns and "t_w4.fund_flow_1week" in rex_df.columns:
        for _, row in rex_df.iterrows():
            ticker = str(row.get("ticker_clean", ""))
            flow = float(row.get("t_w4.fund_flow_1week", 0) or 0)
            if ticker:
                flow_lookup[ticker] = flow

    _col_header = (
        f"padding:3px 6px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"letter-spacing:0.5px;border-bottom:1px solid {_BORDER};"
    )

    def _section(title: str, items: list, title_color: str, metric_label: str = "1W Return") -> str:
        if not items:
            return ""
        header = (
            f'<div style="font-size:13px;font-weight:700;color:{title_color};'
            f'margin-bottom:4px;">{_esc(title)}</div>'
        )
        col_headers = (
            f'<tr>'
            f'<td style="{_col_header}width:60px;">Ticker</td>'
            f'<td style="{_col_header}">Fund Name</td>'
            f'<td style="{_col_header}text-align:right;width:80px;">{_esc(metric_label)}</td>'
            f'<td style="{_col_header}text-align:right;width:80px;">1W Flow</td>'
            f'</tr>'
        )
        rows = []
        for item in items[:5]:
            ticker = _esc(item.get("ticker", ""))
            name = _esc(item.get("fund_name", ""))
            if len(name) > 35:
                name = name[:32] + "..."
            value = _esc(item.get("value_fmt", ""))
            flow = flow_lookup.get(item.get("ticker", ""), 0)
            flow_fmt = _fmt_flow_safe(flow) if flow != 0 else f'<span style="color:{_GRAY};">--</span>'
            flow_clr = _flow_color(flow) if flow != 0 else _GRAY
            rows.append(
                f'<tr>'
                f'<td style="padding:3px 6px;font-size:11px;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};white-space:nowrap;width:60px;">{ticker}</td>'
                f'<td style="padding:3px 6px;font-size:10px;color:{_GRAY};'
                f'border-bottom:1px solid {_BORDER};">{name}</td>'
                f'<td style="padding:3px 6px;font-size:11px;text-align:right;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};color:{title_color};width:80px;">{value}</td>'
                f'<td style="padding:3px 6px;font-size:11px;text-align:right;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};color:{flow_clr};width:80px;">{flow_fmt}</td>'
                f'</tr>'
            )
        return (
            f'<div style="margin-bottom:14px;">'
            f'{header}'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
            f' style="border-collapse:collapse;">'
            f'{col_headers}'
            f'{"".join(rows)}'
            f'</table>'
            f'</div>'
        )

    winners_html = _section("Winners", winners, _GREEN)
    losers_html = _section("Losers", losers, _RED)
    yielders_html = _section("Yielders", yielders, _GREEN, metric_label="Yield")

    return f"""
<tr><td style="padding:15px 30px;">
  <div style="{_SECTION_TITLE}">Winners, Losers & Yielders</div>
  {winners_html}
  {losers_html}
  {yielders_html}
</td></tr>"""


def _render_landscape_header() -> str:
    """Part 3 section divider."""
    return f"""
<tr><td style="padding:20px 30px 5px;">
  <div style="font-size:18px;font-weight:700;color:{_NAVY};margin:0;
    padding-bottom:8px;border-bottom:3px solid {_NAVY};">
    Market Landscape
  </div>
  <div style="font-size:12px;color:{_GRAY};margin-top:6px;">
    Full competitive picture across REX-relevant ETP categories
  </div>
</td></tr>"""


def _render_category_card(
    cat_name: str,
    display_name: str,
    border_color: str,
    cat_data: dict,
    master: pd.DataFrame = None,
) -> str:
    """Render a single category landscape card with 3 KPIs + issuer table."""
    cat_kpis = cat_data.get("cat_kpis", {})

    cat_aum = cat_kpis.get("total_aum", 0)
    flow_1w = cat_kpis.get("flow_1w", 0)
    num_products = cat_kpis.get("num_products", cat_kpis.get("count", 0))

    # Growth computations from master DataFrame
    aum_growth_sub = ""
    products_new_sub = ""
    cat_df = pd.DataFrame()
    launch_by_issuer: dict[str, int] = {}

    if master is not None and not master.empty and "category_display" in master.columns:
        cat_df = master[master["category_display"] == cat_name].copy()

        if not cat_df.empty:
            # AUM MoM growth
            if "t_w4.aum" in cat_df.columns and "t_w4.aum_1" in cat_df.columns:
                aum_curr = float(cat_df["t_w4.aum"].sum())
                aum_prev = float(cat_df["t_w4.aum_1"].sum())
                if aum_prev > 0:
                    aum_growth = (aum_curr - aum_prev) / aum_prev * 100
                    g_color = _GREEN if aum_growth >= 0 else _RED
                    g_sign = "+" if aum_growth >= 0 else ""
                    aum_growth_sub = (
                        f'<div style="font-size:9px;color:{g_color};font-weight:600;">'
                        f'{g_sign}{aum_growth:.1f}% MoM</div>'
                    )

            # New products (inception in last 7 days) and per-issuer launch counts
            if "inception_date" in cat_df.columns:
                cutoff_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
                inception = pd.to_datetime(cat_df["inception_date"], errors="coerce")
                new_mask = inception >= cutoff_7d
                new_count = int(new_mask.sum())
                if new_count > 0:
                    products_new_sub = (
                        f'<div style="font-size:9px;color:{_GREEN};font-weight:600;">'
                        f'+{new_count} this week</div>'
                    )
                # Per-issuer launch counts
                if "issuer_display" in cat_df.columns:
                    new_df = cat_df[new_mask]
                    if not new_df.empty:
                        launch_by_issuer = dict(
                            new_df.groupby("issuer_display").size()
                        )

    # Flow color
    flow_color = _flow_color(flow_1w)

    # Light gray column header style (matches WLY section)
    _cat_col_header = (
        f"padding:3px 6px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"letter-spacing:0.5px;border-bottom:1px solid {_BORDER};"
    )

    # 3 KPI row (AUM, 1W Flow, Products)
    _kpi_cell = f"padding:6px 4px;background:{_LIGHT};border-radius:6px;text-align:center;"
    kpi_html = f"""
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
    <tr>
      <td width="32%" style="{_kpi_cell}">
        <div style="font-size:15px;font-weight:700;color:{_NAVY};">{_fmt_currency_safe(cat_aum)}</div>
        {aum_growth_sub}
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Total AUM</div>
      </td>
      <td width="2%"></td>
      <td width="32%" style="{_kpi_cell}">
        <div style="font-size:15px;font-weight:700;color:{flow_color};">{_fmt_flow_safe(flow_1w)}</div>
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">1W Flows</div>
      </td>
      <td width="2%"></td>
      <td width="32%" style="{_kpi_cell}">
        <div style="font-size:15px;font-weight:700;color:{_NAVY};">{num_products}</div>
        {products_new_sub}
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Products</div>
      </td>
    </tr>
  </table>"""

    # Top 5 issuers table with REX share column, 1W flow, launch indicators
    issuer_table = ""
    if not cat_df.empty and "issuer_display" in cat_df.columns:
        # Identify REX issuers
        rex_issuers = set()
        rex_rows = cat_df[cat_df["is_rex"] == True]
        if not rex_rows.empty and "issuer_display" in rex_rows.columns:
            rex_issuers = set(rex_rows["issuer_display"].dropna().unique())

        agg_cols = {"aum": ("t_w4.aum", "sum"), "count": ("t_w4.aum", "size")}
        if "t_w4.fund_flow_1week" in cat_df.columns:
            agg_cols["flow_1w"] = ("t_w4.fund_flow_1week", "sum")
        else:
            agg_cols["flow_1w"] = ("t_w4.fund_flow_1month", "sum")

        issuer_agg = cat_df.groupby("issuer_display").agg(**agg_cols).sort_values("aum", ascending=False).head(5)

        # Category total AUM for share calculation
        total_cat_aum = float(cat_df["t_w4.aum"].sum()) if "t_w4.aum" in cat_df.columns else 0

        issuer_rows = []
        for rank, (issuer_name, row) in enumerate(issuer_agg.iterrows(), 1):
            i_name = _esc(str(issuer_name))
            if len(i_name) > 22:
                i_name = i_name[:19] + "..."
            i_aum = float(row["aum"])
            i_flow = float(row["flow_1w"])
            i_count = int(row["count"])
            is_rex_issuer = str(issuer_name) in rex_issuers

            # Market share percentage
            i_share = (i_aum / total_cat_aum * 100) if total_cat_aum > 0 else 0

            # Launch count indicator
            launches = launch_by_issuer.get(str(issuer_name), 0)
            launch_badge = ""
            if launches > 0:
                launch_badge = (
                    f' <span style="color:{_GREEN};font-size:9px;font-weight:700;">'
                    f'+{launches}</span>'
                )

            # REX issuer: bold name, no badge
            if is_rex_issuer:
                name_cell = f'<td style="{_TABLE_CELL}font-weight:700;">{i_name}</td>'
            else:
                name_cell = f'<td style="{_TABLE_CELL}font-weight:600;">{i_name}</td>'

            issuer_rows.append(
                f'<tr>'
                f'<td style="{_TABLE_CELL}text-align:center;width:26px;color:{_GRAY};">{rank}</td>'
                f'{name_cell}'
                f'<td style="{_TABLE_CELL_RIGHT}">{_fmt_currency_safe(i_aum)}</td>'
                f'<td style="{_TABLE_CELL_RIGHT}color:{_GRAY};">{i_share:.1f}%</td>'
                f'<td style="{_TABLE_CELL_RIGHT}color:{_flow_color(i_flow)};">'
                f'{_fmt_flow_safe(i_flow)}</td>'
                f'<td style="{_TABLE_CELL_RIGHT}">{i_count}{launch_badge}</td>'
                f'</tr>'
            )

        if issuer_rows:
            issuer_table = (
                f'<div style="font-size:11px;color:{_GRAY};margin-top:6px;margin-bottom:2px;">'
                f'Top issuers by AUM</div>'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
                f' style="border-collapse:collapse;">'
                f'<tr>'
                f'<th style="{_cat_col_header}text-align:center;width:26px;">#</th>'
                f'<th style="{_cat_col_header}">Issuer</th>'
                f'<th style="{_cat_col_header}text-align:right;">AUM</th>'
                f'<th style="{_cat_col_header}text-align:right;">Share</th>'
                f'<th style="{_cat_col_header}text-align:right;">1W Flow</th>'
                f'<th style="{_cat_col_header}text-align:right;">Products</th>'
                f'</tr>'
                f'{"".join(issuer_rows)}'
                f'</table>'
            )

    return f"""
<tr><td style="padding:12px 30px 5px;">
  <div style="font-size:15px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:3px solid {border_color};">
    {_esc(display_name)}
  </div>
  {kpi_html}
  {issuer_table}
</td></tr>"""


def _render_landscape(landscape: dict, master: pd.DataFrame = None) -> str:
    """Render all category landscape cards."""
    if not landscape:
        return ""

    cards = []
    for cat_name, display_name, color in _LANDSCAPE_CATS:
        cat_data = landscape.get(cat_name)
        if not cat_data:
            continue
        cards.append(_render_category_card(cat_name, display_name, color, cat_data, master))

    if not cards:
        return ""

    return _render_landscape_header() + "\n".join(cards)


def _render_etf_universe(master: pd.DataFrame) -> str:
    """ETF Universe section: total market overview with AUM donut chart by category."""
    if master is None or master.empty:
        return ""

    # Deduplicate by ticker
    if "ticker_clean" in master.columns:
        deduped = master.drop_duplicates(subset=["ticker_clean"], keep="first").copy()
    elif "ticker" in master.columns:
        deduped = master.drop_duplicates(subset=["ticker"], keep="first").copy()
    else:
        deduped = master.copy()

    # Filter to ETFs only
    fund_type_col = next((c for c in deduped.columns if c.lower().strip() == "fund_type"), None)
    if fund_type_col:
        deduped = deduped[deduped[fund_type_col] == "ETF"].copy()

    if deduped.empty:
        return ""

    total_aum = float(deduped["t_w4.aum"].sum()) if "t_w4.aum" in deduped.columns else 0
    total_products = len(deduped)
    total_flow_1w = float(deduped["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in deduped.columns else 0
    total_issuers = deduped["issuer_display"].nunique() if "issuer_display" in deduped.columns else 0

    # New launches this week
    launches_sub = ""
    if "inception_date" in deduped.columns:
        cutoff_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
        inception = pd.to_datetime(deduped["inception_date"], errors="coerce")
        new_count = int((inception >= cutoff_7d).sum())
        if new_count > 0:
            launches_sub = (
                f'<div style="font-size:9px;color:{_GREEN};font-weight:600;margin-top:2px;">'
                f'+{new_count} this week</div>'
            )

    # AUM by category for donut chart
    segments = []
    cat_colors = {
        "Leverage & Inverse - Single Stock": "#e74c3c",
        "Income - Single Stock": "#f39c12",
        "Income - Index/Basket/ETF Based": "#0984e3",
        "Crypto": "#8e44ad",
        "Thematic": "#27ae60",
        "Leverage & Inverse - Index/Basket/ETF Based": "#2d3436",
    }
    if "category_display" in deduped.columns and "t_w4.aum" in deduped.columns:
        cat_aums = deduped.groupby("category_display")["t_w4.aum"].sum().sort_values(ascending=False)
        other_aum = 0.0
        for cat, aum in cat_aums.items():
            cat_str = str(cat)
            pct = (aum / total_aum * 100) if total_aum > 0 else 0
            if cat_str in cat_colors and pct >= 2:
                segments.append((cat_str, float(aum), cat_colors[cat_str]))
            else:
                other_aum += float(aum)
        if other_aum > 0:
            segments.append(("Other", other_aum, _GRAY))

    # Donut + legend layout
    donut_html = ""
    if segments:
        donut = _render_donut_svg(segments, _fmt_currency_safe(total_aum))
        legend_rows = []
        for name, val, color in segments:
            pct = (val / total_aum * 100) if total_aum > 0 else 0
            # Short display names for legend
            short_names = {
                "Leverage & Inverse - Single Stock": "Lev Single Stock",
                "Income - Single Stock": "CC Single Stock",
                "Income - Index/Basket/ETF Based": "CC Index/ETF",
                "Leverage & Inverse - Index/Basket/ETF Based": "Lev Index/ETF",
            }
            display = short_names.get(name, name)
            legend_rows.append(
                f'<tr>'
                f'<td style="padding:2px 4px;width:10px;">'
                f'<div style="width:8px;height:8px;background:{color};border-radius:2px;"></div></td>'
                f'<td style="padding:2px 4px;font-size:10px;">{_esc(display)}</td>'
                f'<td style="padding:2px 4px;font-size:10px;text-align:right;color:{_GRAY};">{pct:.0f}%</td>'
                f'</tr>'
            )
        donut_html = f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
      <tr>
        <td width="40%" align="center" style="padding:6px;">{donut}</td>
        <td width="60%" valign="middle">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            {''.join(legend_rows)}
          </table>
        </td>
      </tr>
    </table>"""

    # KPI cards
    _kpi_cell = f"padding:6px 4px;background:{_LIGHT};border-radius:6px;text-align:center;"
    flow_color = _flow_color(total_flow_1w)
    kpi_html = f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
      <tr>
        <td width="24%" style="{_kpi_cell}">
          <div style="font-size:15px;font-weight:700;color:{_NAVY};">{_fmt_currency_safe(total_aum)}</div>
          <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Total AUM</div>
        </td>
        <td width="1%"></td>
        <td width="24%" style="{_kpi_cell}">
          <div style="font-size:15px;font-weight:700;color:{flow_color};">{_fmt_flow_safe(total_flow_1w)}</div>
          <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">1W Flows</div>
        </td>
        <td width="1%"></td>
        <td width="24%" style="{_kpi_cell}">
          <div style="font-size:15px;font-weight:700;color:{_NAVY};">{total_products}</div>
          {launches_sub}
          <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Products</div>
        </td>
        <td width="1%"></td>
        <td width="24%" style="{_kpi_cell}">
          <div style="font-size:15px;font-weight:700;color:{_NAVY};">{total_issuers}</div>
          <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Issuers</div>
        </td>
      </tr>
    </table>"""

    return f"""
<tr><td style="padding:20px 30px 5px;">
  <div style="font-size:18px;font-weight:700;color:{_NAVY};margin:0;
    padding-bottom:8px;border-bottom:3px solid {_NAVY};">
    ETF Universe
  </div>
  <div style="font-size:12px;color:{_GRAY};margin-top:6px;margin-bottom:12px;">
    Total leveraged, income, crypto & thematic ETF market (deduplicated)
  </div>
  {kpi_html}
  {donut_html}
</td></tr>"""


def _render_dashboard_cta(dashboard_url: str) -> str:
    url = _esc(dashboard_url)
    return f"""
<tr><td style="padding:20px 30px;" align="center">
  <table cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="background:{_BLUE};border-radius:8px;padding:16px 40px;">
      <a href="{url}" style="color:{_WHITE};text-decoration:none;
         font-size:16px;font-weight:700;">Open Dashboard</a>
    </td>
  </tr></table>
  <div style="font-size:12px;color:{_GRAY};margin-top:8px;">
    View full details, filings, and market intelligence
  </div>
</td></tr>"""


def _render_footer(week_ending: str) -> str:
    return f"""
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <div style="font-size:11px;color:{_GRAY};text-align:center;">
    REX ETF Weekly Report | Week of {_esc(week_ending)}
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    Data sourced from Bloomberg and SEC EDGAR
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    To unsubscribe, contact relasmar@rexfin.com
  </div>
</td></tr>"""


def _render_market_unavailable() -> str:
    return f"""
<tr><td style="padding:15px 30px;">
  <div style="padding:16px;background:{_LIGHT};border-radius:8px;text-align:center;
              font-size:13px;color:{_GRAY};">
    Market data not available. Bloomberg data file has not been loaded.
  </div>
</td></tr>"""


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
def build_weekly_digest_html(
    db_session,
    dashboard_url: str = "",
    format: str = "full",
) -> str:
    # format="full" is the only implemented format.
    # format="flash" reserved for future 1-screen executive summary.
    today = datetime.now()
    week_ending = today.strftime("%B %d, %Y")
    dash_url = dashboard_url or _DEFAULT_DASHBOARD_URL

    market = _gather_market_data()
    filing = _gather_filing_data(db_session, days=7)

    data_as_of = market["data_as_of"] if market else ""

    sections = []

    # --- PART 1: Overview ---
    # 1. Header
    sections.append(_render_header(week_ending, data_as_of))

    # 2. Filing Activity (top of email)
    sections.append(_render_filing_activity(filing))

    if market:
        rex_df = market.get("rex_df", pd.DataFrame())

        # --- PART 2: REX Products ---
        # 3. Scorecard (with growth sub-labels)
        sections.append(_render_scorecard(market["kpis"], rex_df))

        # 4. AUM by Suite (donut chart)
        aum_chart = _render_aum_stacked_bar(market["suites"], rex_df)
        if aum_chart:
            sections.append(aum_chart)

        # 5. 1W Flows by Suite
        flow_chart = _render_flow_chart(market["suites"], market["flow_chart"])
        if flow_chart:
            sections.append(flow_chart)

        # 6. Winners, Losers & Yielders (combined)
        wly = _render_winners_losers_yielders(market["perf_metrics"], rex_df)
        if wly:
            sections.append(wly)

        # --- PART 3: Category Landscape ---
        landscape = market.get("landscape", {})
        master_df = market.get("master", pd.DataFrame())
        landscape_html = _render_landscape(landscape, master_df)
        if landscape_html:
            sections.append(landscape_html)

        # --- PART 4: ETF Universe ---
        etf_universe = _render_etf_universe(master_df)
        if etf_universe:
            sections.append(etf_universe)
    else:
        sections.append(_render_scorecard_unavailable())
        sections.append(_render_market_unavailable())

    # --- PART 5: Close ---
    # Dashboard CTA
    sections.append(_render_dashboard_cta(dash_url))

    # 11. Footer
    sections.append(_render_footer(week_ending))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>REX ETF Weekly Report - {_esc(week_ending)}</title>
</head>
<body style="margin:0;padding:0;background:{_LIGHT};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="600" cellpadding="0" cellspacing="0" border="0"
       style="background:{_WHITE};border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);">
{body}
</table>
</td></tr></table>
</body></html>"""


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------
def send_weekly_digest(
    db_session,
    dashboard_url: str = "",
    format: str = "full",
) -> bool:
    recipients = _load_recipients()
    if not recipients:
        log.warning("Weekly digest: no recipients configured")
        return False

    html_body = build_weekly_digest_html(db_session, dashboard_url, format=format)
    today = datetime.now()
    week_ending = today.strftime("%B %d, %Y")
    subject = f"REX ETF Weekly Report - Week of {week_ending}"

    # Try Azure Graph API first
    try:
        from webapp.services.graph_email import is_configured, send_email
        if is_configured():
            if send_email(subject=subject, html_body=html_body, recipients=recipients):
                log.info("Weekly digest sent via Graph API to %d recipients", len(recipients))
                return True
    except ImportError:
        pass

    # Fall back to SMTP
    config = _get_smtp_config()
    if not config["user"] or not config["password"] or not config["from_addr"]:
        log.warning("Weekly digest: SMTP not configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(config["host"], config["port"]) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config["user"], config["password"])
            server.sendmail(config["from_addr"], recipients, msg.as_string())
        log.info("Weekly digest sent via SMTP to %d recipients", len(recipients))
        return True
    except Exception as exc:
        log.error("Weekly digest send failed: %s", exc)
        return False
