"""
V3 Report emails: L&I and Income with unified segmented format.

Both emails share identical layout:
  1. Header + Date
  2. KPI Banner (Index/ETF/Basket row + Single Stock row)
  3. Key Highlights (narrative callouts)
  4. REX Spotlight (top 8 flagship products)
  5. Index/ETF/Basket Section (issuer table, top 10 inflows/outflows)
  6. Single Stock Section (issuer table, top 10 inflows/outflows)
  7. Footer

Income adds a Yield column in fund tables and Avg Yield in KPIs.
No CID images or matplotlib -- pure HTML tables.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Email-safe colors
# ---------------------------------------------------------------------------
_NAVY = "#1a1a2e"
_TEAL = "#00897B"
_GREEN = "#27ae60"
_RED = "#e74c3c"
_BLUE = "#0984e3"
_GRAY = "#636e72"
_LIGHT = "#f8f9fa"
_BORDER = "#dee2e6"
_WHITE = "#ffffff"
_ORANGE = "#e67e22"
_REX_ROW_BG = "#e8f5e9"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _flow_color(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return _GRAY
    return _GREEN if val >= 0 else _RED


def _fmt_currency(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    if abs(val) >= 1_000:
        return f"${val / 1_000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    return f"${val:.2f}M"


def _fmt_flow(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency(val)}"


def _fmt_pct(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "0.0%"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _fmt_pct_nosign(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "0.0%"
    return f"{val:.1f}%"


def _is_valid_date(date_str: str) -> bool:
    if not date_str:
        return False
    try:
        for fmt in ("%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.year >= 2020
            except ValueError:
                continue
    except Exception:
        pass
    return False


def _yesterday() -> datetime:
    return datetime.now() - timedelta(days=1)


def _data_date_str(data: dict, fmt: str = "%B %d, %Y") -> str:
    full = data.get("data_as_of", "")
    if _is_valid_date(full):
        return full
    short = data.get("data_as_of_short", "")
    if _is_valid_date(short):
        try:
            return datetime.strptime(short, "%m/%d/%Y").strftime(fmt)
        except ValueError:
            pass
    return _yesterday().strftime(fmt)


def _data_date_short(data: dict) -> str:
    short = data.get("data_as_of_short", "")
    if _is_valid_date(short):
        return short
    full = data.get("data_as_of", "")
    if _is_valid_date(full):
        try:
            return datetime.strptime(full, "%B %d, %Y").strftime("%m/%d/%Y")
        except ValueError:
            pass
    return _yesterday().strftime("%m/%d/%Y")


# ---------------------------------------------------------------------------
# Email envelope
# ---------------------------------------------------------------------------
def _wrap_email(title: str, accent: str, body: str,
                dashboard_url: str = "", date_str: str = "") -> str:
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%B %d, %Y")
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    cta = ""
    if dash_link:
        cta = f"""
<tr><td style="padding:20px 30px;text-align:center;">
  <a href="{dash_link}/reports/" style="display:inline-block;padding:12px 28px;
    background:{accent};color:{_WHITE};text-decoration:none;border-radius:6px;
    font-weight:600;font-size:14px;">View Interactive Report</a>
</td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(title)}</title></head>
<body style="margin:0;padding:0;background:{_LIGHT};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="660" cellpadding="0" cellspacing="0" border="0"
  style="background:{_WHITE};border-radius:12px;overflow:hidden;
  box-shadow:0 2px 8px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="background:{accent};padding:24px 30px;">
  <div style="font-size:22px;font-weight:700;color:{_WHITE};letter-spacing:-0.5px;">{_esc(title)}</div>
  <div style="font-size:12px;color:rgba(255,255,255,0.8);margin-top:4px;">{_esc(date_str)}</div>
</td></tr>

{body}
{cta}

<!-- Footer -->
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};text-align:center;">
  <div style="font-size:11px;color:{_GRAY};">
    REX Financial Intelligence Hub &middot; Data sourced from Bloomberg L.P. and REX Shares, LLC
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


# ---------------------------------------------------------------------------
# Shared section renderers
# ---------------------------------------------------------------------------
def _kpi_row(kpis: list[tuple[str, str, str]], label: str = "") -> str:
    """Render a single KPI row with optional segment label."""
    n = len(kpis)
    width = int(100 / n) if n else 25
    cells = []
    for kpi_label, value, color in kpis:
        cells.append(
            f'<td width="{width}%" style="padding:12px 6px;background:{_LIGHT};'
            f'border-radius:8px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{color};">{_esc(value)}</div>'
            f'<div style="font-size:9px;color:{_GRAY};text-transform:uppercase;'
            f'letter-spacing:0.5px;margin-top:2px;">{_esc(kpi_label)}</div></td>'
        )
    label_html = ""
    if label:
        label_html = (
            f'<div style="font-size:10px;font-weight:600;color:{_GRAY};'
            f'text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">'
            f'{_esc(label)}</div>'
        )
    return (
        f'<tr><td style="padding:15px 30px 5px;">'
        f'{label_html}'
        f'<table width="100%" cellpadding="0" cellspacing="6" border="0">'
        f'<tr>{"".join(cells)}</tr>'
        f'</table></td></tr>'
    )


def _section_title(title: str, accent: str = _TEAL) -> str:
    return (
        f'<tr><td style="padding:18px 30px 5px;">'
        f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;'
        f'padding-bottom:6px;border-bottom:2px solid {accent};">{_esc(title)}</div>'
        f'</td></tr>'
    )


def _sub_heading(title: str) -> str:
    return (
        f'<tr><td style="padding:10px 30px 2px;">'
        f'<div style="font-size:13px;font-weight:700;color:{_NAVY};">{_esc(title)}</div>'
        f'</td></tr>'
    )


def _table(headers: list[str], rows: list[list[str]], align: list[str] | None = None,
           highlight_col: int | None = None, bold_last_row: bool = False,
           rex_rows: set[int] | None = None,
           col_widths: list[str] | None = None) -> str:
    if not rows:
        return '<tr><td style="padding:10px 30px;color:#636e72;font-size:13px;">No data available.</td></tr>'

    n = len(headers)
    if align is None:
        align = ["left"] * n

    _th = (f"padding:8px 10px;background:{_LIGHT};font-size:10px;color:{_GRAY};"
           f"text-transform:uppercase;letter-spacing:0.5px;font-weight:600;"
           f"border-bottom:2px solid {_BORDER};")
    _td = f"padding:6px 10px;font-size:12px;color:{_NAVY};border-bottom:1px solid {_BORDER};"

    header_cells = ""
    for i, h in enumerate(headers):
        w = f"width:{col_widths[i]};" if col_widths and i < len(col_widths) else ""
        header_cells += f'<th style="{_th}text-align:{align[i]};{w}">{_esc(h)}</th>'

    body_rows = []
    for ri, row in enumerate(rows):
        is_bold = bold_last_row and ri == len(rows) - 1
        is_rex = rex_rows and ri in rex_rows
        cells = []
        for i, val in enumerate(row):
            w = f"width:{col_widths[i]};" if col_widths and i < len(col_widths) else ""
            style = _td + f"text-align:{align[i]};{w}"
            if is_bold:
                style += "font-weight:700;"
            if is_rex:
                style += f"background:{_REX_ROW_BG};"
            if highlight_col is not None and i == highlight_col:
                try:
                    fval = float(str(val).replace("$", "").replace(",", "")
                                 .replace("+", "").replace("B", "e3").replace("M", ""))
                except (ValueError, AttributeError):
                    fval = 0
                if fval > 0:
                    style += f"color:{_GREEN};"
                elif fval < 0:
                    style += f"color:{_RED};"
            cells.append(f'<td style="{style}">{_esc(str(val))}</td>')
        body_rows.append(f'<tr>{"".join(cells)}</tr>')

    return (
        '<tr><td style="padding:5px 30px 10px;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></td></tr>'
    )


def _rex_spotlight(rex_funds: list[dict], accent: str = _GREEN) -> str:
    """Transposed REX product table -- top 8 flagship products only."""
    if not rex_funds:
        return ""
    sorted_rex = sorted(rex_funds, key=lambda f: f.get("aum", 0), reverse=True)[:8]
    headers = ["Metric"] + [f["ticker"] for f in sorted_rex]
    aligns = ["left"] + ["right"] * len(sorted_rex)
    aum_row = ["AUM"] + [f["aum_fmt"] for f in sorted_rex]
    flow_1w_row = ["1W Flow"] + [f.get("flow_1w_fmt", "--") for f in sorted_rex]
    flow_1m_row = ["1M Flow"] + [f.get("flow_1m_fmt", "--") for f in sorted_rex]
    rows = [aum_row, flow_1w_row, flow_1m_row]
    if any(f.get("yield_fmt") and f.get("yield_val", 0) for f in sorted_rex):
        yield_row = ["Yield"] + [f.get("yield_fmt", "--") for f in sorted_rex]
        rows.append(yield_row)
    return _section_title("REX Spotlight", accent) + _table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# Key Highlights (renamed from Notable Mentions)
# ---------------------------------------------------------------------------
def _key_highlights(data: dict, report_type: str) -> str:
    """Generate narrative callouts from report data."""
    bullets = []

    if report_type == "li":
        providers = data.get("providers", [])
        top10 = data.get("top10", [])
        bottom10 = data.get("bottom10", [])
        kpis = data.get("kpis", {})

        if top10:
            top = top10[0]
            bullets.append(
                f'<b>Top Weekly Inflow:</b> {_esc(top["ticker"])} '
                f'({_esc(top["fund_name"][:40])}) attracted '
                f'<span style="color:{_GREEN};font-weight:700;">{_esc(top["flow_1w_fmt"])}</span> this week'
            )

        if bottom10:
            bot = bottom10[0]
            bullets.append(
                f'<b>Top Weekly Outflow:</b> {_esc(bot["ticker"])} '
                f'({_esc(bot["fund_name"][:40])}) saw '
                f'<span style="color:{_RED};font-weight:700;">{_esc(bot["flow_1w_fmt"])}</span> this week'
            )

        if providers:
            top_issuer = max(providers, key=lambda p: p["flow_1w"])
            if top_issuer["flow_1w"] > 0:
                bullets.append(
                    f'<b>Leading Issuer:</b> {_esc(top_issuer["issuer"])} led with '
                    f'{_esc(top_issuer["flow_1w_fmt"])} in weekly net flows '
                    f'({_fmt_pct_nosign(top_issuer["market_share"])} market share)'
                )

        rex_funds = data.get("rex_funds", [])
        if rex_funds:
            rex_aum = sum(f["aum"] for f in rex_funds)
            bullets.append(
                f'<b>REX Position:</b> {len(rex_funds)} REX L&I products with '
                f'{_fmt_currency(rex_aum)} combined AUM'
            )

    elif report_type == "cc":
        issuers = data.get("issuers", [])
        top_flow = data.get("top_flow_segments", {}).get("All", [])
        top_yield = data.get("top_yield_segments", {}).get("All", [])
        kpis = data.get("kpis", {})

        if top_flow:
            top = top_flow[0]
            bullets.append(
                f'<b>Top Monthly Inflow:</b> {_esc(top["ticker"])} '
                f'({_esc(top["issuer"][:25])}) attracted '
                f'<span style="color:{_GREEN};font-weight:700;">{_esc(top["flow_1m_fmt"])}</span> over 1M'
            )

        if top_yield:
            top = top_yield[0]
            bullets.append(
                f'<b>Highest Yield:</b> {_esc(top["ticker"])} at '
                f'<span style="color:{_TEAL};font-weight:700;">{_esc(top["yield_fmt"])}</span> '
                f'({_esc(top["issuer"][:25])})'
            )

        if issuers:
            top_iss = issuers[0]
            bullets.append(
                f'<b>Market Leader:</b> {_esc(top_iss["issuer"])} holds '
                f'{_fmt_pct_nosign(top_iss["market_share"])} market share '
                f'({_esc(top_iss["aum_fmt"])} AUM, {top_iss["count"]} funds)'
            )

        rex_funds = data.get("rex_funds", [])
        if rex_funds:
            rex_aum = sum(f["aum"] for f in rex_funds)
            bullets.append(
                f'<b>REX Position:</b> {len(rex_funds)} REX income products with '
                f'{_fmt_currency(rex_aum)} combined AUM'
            )

    if not bullets:
        return ""

    bullet_html = "".join(
        f'<tr><td style="padding:5px 8px;font-size:12px;color:{_NAVY};line-height:1.6;">'
        f'<span style="color:{_TEAL};font-weight:700;margin-right:6px;">&#9679;</span>'
        f'{b}</td></tr>'
        for b in bullets
    )

    return (
        _section_title("Key Highlights", _TEAL) +
        f'<tr><td style="padding:5px 30px 15px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_LIGHT};border-radius:8px;padding:12px;">'
        f'{bullet_html}</table></td></tr>'
    )


# ---------------------------------------------------------------------------
# Inline HTML charts (email-safe, no images)
# ---------------------------------------------------------------------------
_CHART_COLORS = ["#0984e3", "#00897B", "#e67e22", "#8e44ad", "#e74c3c",
                 "#2ecc71", "#f39c12", "#3498db", "#1abc9c", "#e91e63"]


def _horizontal_bar_chart(items: list[dict], value_key: str = "market_share",
                          label_key: str = "name", value_fmt_key: str = "aum_fmt",
                          title: str = "", max_bars: int = 8,
                          accent: str = _TEAL) -> str:
    """Render a horizontal bar chart using pure HTML tables.

    Each bar is a colored <td> with percentage width inside a fixed-width cell.
    """
    if not items:
        return ""
    items = items[:max_bars]
    max_val = max(abs(b.get(value_key, 0)) for b in items) or 1

    bars_html = ""
    for i, b in enumerate(items):
        val = b.get(value_key, 0)
        pct = abs(val) / max_val * 100
        bar_width = max(pct, 2)  # minimum visible width
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        label = _esc(str(b.get(label_key, ""))[:22])
        val_display = _esc(str(b.get(value_fmt_key, "")))
        share = f'{b.get("market_share", 0):.1f}%' if "market_share" in b else ""

        bars_html += f"""<tr>
<td style="padding:3px 0;font-size:11px;color:{_NAVY};width:100px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;">{label}</td>
<td style="padding:3px 6px;width:100%;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
  <tr><td style="width:{bar_width:.0f}%;background:{color};height:16px;border-radius:3px;
    font-size:0;line-height:0;">&nbsp;</td>
  <td style="width:{100 - bar_width:.0f}%;font-size:0;">&nbsp;</td></tr>
  </table>
</td>
<td style="padding:3px 4px;font-size:11px;color:{_NAVY};text-align:right;white-space:nowrap;
  width:70px;">{val_display}</td>
<td style="padding:3px 0;font-size:10px;color:{_GRAY};text-align:right;white-space:nowrap;
  width:40px;">{share}</td>
</tr>"""

    title_html = ""
    if title:
        title_html = (f'<tr><td colspan="4" style="padding:0 0 6px;font-size:11px;'
                      f'font-weight:600;color:{_GRAY};text-transform:uppercase;'
                      f'letter-spacing:0.5px;">{_esc(title)}</td></tr>')

    return (
        f'<tr><td style="padding:8px 30px 10px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;padding:10px 12px;">'
        f'{title_html}{bars_html}'
        f'</table></td></tr>'
    )


def _flow_bars(inflows: list[dict], outflows: list[dict], n: int = 5) -> str:
    """Render a bi-directional flow chart: green bars right for inflows, red bars left for outflows."""
    top_in = inflows[:n]
    top_out = outflows[:n]
    if not top_in and not top_out:
        return ""

    all_flows = [abs(f.get("flow_1w", 0)) for f in top_in + top_out]
    max_flow = max(all_flows) if all_flows else 1

    rows_html = ""
    for f in top_in:
        flow = f.get("flow_1w", 0)
        pct = abs(flow) / max_flow * 100
        bar_w = max(pct, 3)
        rows_html += f"""<tr>
<td style="padding:2px 0;font-size:11px;color:{_NAVY};width:65px;white-space:nowrap;">{_esc(f["ticker"])}</td>
<td style="padding:2px 4px;width:100%;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
  <tr><td style="width:{bar_w:.0f}%;background:{_GREEN};height:14px;border-radius:3px;
    font-size:0;">&nbsp;</td>
  <td style="width:{100 - bar_w:.0f}%;font-size:0;">&nbsp;</td></tr>
  </table>
</td>
<td style="padding:2px 0;font-size:11px;color:{_GREEN};text-align:right;white-space:nowrap;
  width:70px;font-weight:600;">{_esc(f["flow_1w_fmt"])}</td>
</tr>"""

    for f in top_out:
        flow = f.get("flow_1w", 0)
        pct = abs(flow) / max_flow * 100
        bar_w = max(pct, 3)
        rows_html += f"""<tr>
<td style="padding:2px 0;font-size:11px;color:{_NAVY};width:65px;white-space:nowrap;">{_esc(f["ticker"])}</td>
<td style="padding:2px 4px;width:100%;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
  <tr><td style="width:{bar_w:.0f}%;background:{_RED};height:14px;border-radius:3px;
    font-size:0;">&nbsp;</td>
  <td style="width:{100 - bar_w:.0f}%;font-size:0;">&nbsp;</td></tr>
  </table>
</td>
<td style="padding:2px 0;font-size:11px;color:{_RED};text-align:right;white-space:nowrap;
  width:70px;font-weight:600;">{_esc(f["flow_1w_fmt"])}</td>
</tr>"""

    return (
        f'<tr><td style="padding:8px 30px 10px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;padding:10px 12px;">'
        f'<tr><td colspan="3" style="padding:0 0 6px;font-size:11px;font-weight:600;'
        f'color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Weekly Fund Flows</td></tr>'
        f'{rows_html}'
        f'</table></td></tr>'
    )


def _issuer_share_bars(issuers: list[dict], n: int = 6) -> str:
    """Render a stacked market share bar for top issuers."""
    if not issuers:
        return ""
    top = issuers[:n]
    # Stacked horizontal bar
    segments = ""
    legend = ""
    for i, iss in enumerate(top):
        share = iss.get("market_share", 0)
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        name = _esc(iss["issuer"][:18])
        if share >= 1:
            segments += (f'<td style="width:{share:.1f}%;background:{color};height:22px;'
                         f'font-size:0;line-height:0;">&nbsp;</td>')
        legend += (f'<td style="padding:3px 6px 3px 0;font-size:10px;color:{_NAVY};'
                   f'white-space:nowrap;">'
                   f'<span style="display:inline-block;width:8px;height:8px;'
                   f'background:{color};border-radius:2px;margin-right:3px;'
                   f'vertical-align:middle;"></span>'
                   f'{name} ({share:.0f}%)</td>')

    other_share = 100 - sum(iss.get("market_share", 0) for iss in top)
    if other_share > 1:
        segments += (f'<td style="width:{other_share:.1f}%;background:{_BORDER};height:22px;'
                     f'font-size:0;line-height:0;">&nbsp;</td>')
        legend += (f'<td style="padding:3px 6px 3px 0;font-size:10px;color:{_GRAY};'
                   f'white-space:nowrap;">'
                   f'<span style="display:inline-block;width:8px;height:8px;'
                   f'background:{_BORDER};border-radius:2px;margin-right:3px;'
                   f'vertical-align:middle;"></span>'
                   f'Other ({other_share:.0f}%)</td>')

    return (
        f'<tr><td style="padding:8px 30px 4px;">'
        f'<div style="font-size:11px;font-weight:600;color:{_GRAY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:6px;">Market Share</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;border-radius:6px;overflow:hidden;">'
        f'<tr>{segments}</tr></table>'
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;">'
        f'<tr>{legend}</tr></table>'
        f'</td></tr>'
    )


# ---------------------------------------------------------------------------
# Segment section builder (shared by both emails)
# ---------------------------------------------------------------------------
def _breakdown_table(breakdown: list[dict], breakdown_label: str,
                     include_yield: bool = False,
                     include_direction: bool = False,
                     include_type: bool = False) -> str:
    """Render a compact attribute breakdown table (category or underlier)."""
    if not breakdown:
        return ""
    # Build columns dynamically: Name, [Long/Short], [Trad/Synth], # ETPs, AUM, 1W Flow, [Avg Yield], Share
    headers, aligns, col_widths = [breakdown_label], ["left"], ["120px"]
    if include_direction:
        headers.append("Long / Short")
        aligns.append("center")
        col_widths.append("70px")
    if include_type:
        headers.append("Trad / Synth")
        aligns.append("center")
        col_widths.append("70px")
    headers += ["# ETPs", "AUM", "1W Flow"]
    aligns += ["right", "right", "right"]
    col_widths += ["50px", "80px", "80px"]
    if include_yield:
        headers.append("Avg Yield")
        aligns.append("right")
        col_widths.append("65px")
    headers.append("Share")
    aligns.append("right")
    col_widths.append("55px")

    rows = []
    for b in breakdown[:10]:
        row = [b["name"][:25]]
        if include_direction:
            row.append(f'{b.get("num_long", 0)}L / {b.get("num_short", 0)}S')
        if include_type:
            row.append(f'{b.get("num_traditional", 0)}T / {b.get("num_synthetic", 0)}S')
        row += [str(b["count"]), b["aum_fmt"], b["flow_1w_fmt"]]
        if include_yield:
            row.append(b.get("avg_yield_fmt", "--"))
        row.append(f'{b.get("market_share", 0):.1f}%')
        rows.append(row)

    flow_idx = headers.index("1W Flow")
    return _sub_heading(f"{breakdown_label} Breakdown") + _table(
        headers, rows, aligns, highlight_col=flow_idx, col_widths=col_widths,
    )


def _segment_section(segment_title: str, accent: str,
                     issuers: list[dict], top10: list[dict], bottom10: list[dict],
                     include_yield: bool = False,
                     breakdown: list[dict] | None = None,
                     breakdown_label: str = "Category",
                     breakdown_direction: bool = False,
                     breakdown_type: bool = False) -> str:
    """Build one segment section: charts + tables for a full segment."""
    body = ""
    body += _section_title(segment_title, accent)

    # --- Visual charts ---
    # 1. AUM distribution bar chart for category/underlier
    if breakdown:
        chart_title = f"AUM by {breakdown_label}"
        body += _horizontal_bar_chart(
            breakdown, value_key="aum", label_key="name", value_fmt_key="aum_fmt",
            title=chart_title, max_bars=8, accent=accent,
        )

    # 2. Issuer market share stacked bar
    if issuers:
        body += _issuer_share_bars(issuers, n=6)

    # 3. Weekly flow direction bars
    if top10 or bottom10:
        body += _flow_bars(top10, bottom10, n=5)

    # --- Data tables ---
    # Attribute breakdown table (category or underlier)
    if breakdown:
        body += _breakdown_table(
            breakdown, breakdown_label,
            include_yield=include_yield,
            include_direction=breakdown_direction,
            include_type=breakdown_type,
        )

    # Issuer Breakdown table
    if issuers:
        body += _sub_heading("Issuer Breakdown")
        if include_yield:
            headers = ["Issuer", "# ETPs", "AUM", "1W Flow", "1M Flow", "Avg Yield", "Share"]
            aligns = ["left", "right", "right", "right", "right", "right", "right"]
            col_widths = ["140px", "50px", "80px", "80px", "80px", "65px", "55px"]
        else:
            headers = ["Issuer", "# ETPs", "AUM", "1W Flow", "1M Flow", "YTD Flow", "Share"]
            aligns = ["left", "right", "right", "right", "right", "right", "right"]
            col_widths = ["140px", "50px", "80px", "80px", "80px", "80px", "55px"]
        rows = []
        rex_idxs = set()
        for iss in issuers[:15]:
            ri = len(rows)
            if "REX" in iss["issuer"].upper() or "rex" in iss["issuer"].lower():
                rex_idxs.add(ri)
            if include_yield:
                rows.append([
                    iss["issuer"][:28], str(iss["count"]), iss["aum_fmt"],
                    iss["flow_1w_fmt"], iss["flow_1m_fmt"],
                    iss.get("avg_yield_fmt", "--"),
                    f'{iss["market_share"]:.1f}%',
                ])
            else:
                rows.append([
                    iss["issuer"][:28], str(iss["count"]), iss["aum_fmt"],
                    iss["flow_1w_fmt"], iss["flow_1m_fmt"], iss["flow_ytd_fmt"],
                    f'{iss["market_share"]:.1f}%',
                ])
        body += _table(headers, rows, aligns, highlight_col=3,
                       rex_rows=rex_idxs, col_widths=col_widths)

    # Top 10 Weekly Inflows
    if top10:
        body += _sub_heading("Top 10 Weekly Inflows")
        if include_yield:
            headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1W Flow", "Yield", "1W Ret"]
            aligns = ["left", "left", "left", "right", "right", "right", "right"]
            col_widths = ["55px", "155px", "100px", "70px", "70px", "55px", "55px"]
        else:
            headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1W Flow", "1W Ret"]
            aligns = ["left", "left", "left", "right", "right", "right"]
            col_widths = ["60px", "175px", "110px", "75px", "75px", "60px"]
        rows = []
        for f in top10:
            row = [
                f["ticker"], f["fund_name"][:30], f["issuer"][:22],
                f["aum_fmt"], f["flow_1w_fmt"],
            ]
            if include_yield:
                row.append(f.get("yield_fmt", "--"))
            row.append(f.get("return_1w_fmt", ""))
            rows.append(row)
        body += _table(headers, rows, aligns, highlight_col=4, col_widths=col_widths)

    # Top 10 Weekly Outflows
    if bottom10:
        body += _sub_heading("Top 10 Weekly Outflows")
        if include_yield:
            headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1W Flow", "Yield", "1W Ret"]
            aligns = ["left", "left", "left", "right", "right", "right", "right"]
            col_widths = ["55px", "155px", "100px", "70px", "70px", "55px", "55px"]
        else:
            headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1W Flow", "1W Ret"]
            aligns = ["left", "left", "left", "right", "right", "right"]
            col_widths = ["60px", "175px", "110px", "75px", "75px", "60px"]
        rows = []
        for f in bottom10:
            row = [
                f["ticker"], f["fund_name"][:30], f["issuer"][:22],
                f["aum_fmt"], f["flow_1w_fmt"],
            ]
            if include_yield:
                row.append(f.get("yield_fmt", "--"))
            row.append(f.get("return_1w_fmt", ""))
            rows.append(row)
        body += _table(headers, rows, aligns, highlight_col=4, col_widths=col_widths)

    return body


# ---------------------------------------------------------------------------
# Unified report email builder
# ---------------------------------------------------------------------------
def _build_report_email(data: dict, report_type: str, title: str, accent: str,
                        dashboard_url: str = "", include_yield: bool = False) -> str:
    """Unified email builder for both L&I and Income reports.

    Layout:
      1. KPI Banner (Index/ETF/Basket row + Single Stock row)
      2. Key Highlights
      3. REX Spotlight
      4. Index/ETF/Basket Section
      5. Single Stock Section
      6. Footer (via _wrap_email)
    """
    date_str = _data_date_str(data)

    if not data.get("available") or not data.get("kpis"):
        return _wrap_email(title, accent,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url, date_str)

    kpis = data["kpis"]
    index_kpis = data.get("index_kpis", {})
    ss_kpis = data.get("ss_kpis", {})
    body = ""

    # --- 1. KPI Banner: two rows ---
    # Row 1: Index / ETF / Basket
    idx_items = [
        ("Total ETPs", str(index_kpis.get("count", 0)), _NAVY),
        ("Total AUM", index_kpis.get("total_aum", "$0"), _NAVY),
        ("1W Net Flow", index_kpis.get("flow_1w", "$0"),
         _GREEN if index_kpis.get("flow_1w_positive", True) else _RED),
    ]
    if include_yield:
        idx_items.append(("Avg Yield", index_kpis.get("avg_yield", "0.0%"), _TEAL))
    wow = index_kpis.get("aum_change_1w", "")
    if wow:
        idx_items.insert(2, ("AUM WoW", wow,
                             _GREEN if index_kpis.get("aum_change_positive", True) else _RED))
    body += _kpi_row(idx_items, label="Index / ETF / Basket")

    # Row 2: Single Stock
    ss_items = [
        ("SS ETPs", str(ss_kpis.get("count", 0)), _NAVY),
        ("SS AUM", ss_kpis.get("total_aum", "$0"), _NAVY),
        ("SS 1W Flow", ss_kpis.get("flow_1w", "$0"),
         _GREEN if ss_kpis.get("flow_1w_positive", True) else _RED),
    ]
    if include_yield:
        ss_items.append(("SS Avg Yield", ss_kpis.get("avg_yield", "0.0%"), _TEAL))
    wow_ss = ss_kpis.get("aum_change_1w", "")
    if wow_ss:
        ss_items.insert(2, ("SS AUM WoW", wow_ss,
                            _GREEN if ss_kpis.get("aum_change_positive", True) else _RED))
    body += _kpi_row(ss_items, label="Single Stock")

    # --- 2. Key Highlights ---
    body += _key_highlights(data, report_type)

    # --- 3. REX Spotlight ---
    body += _rex_spotlight(data.get("rex_funds", []), _GREEN)

    # --- 4. Index / ETF / Basket Section ---
    is_li = report_type == "li"
    body += _segment_section(
        "Index / ETF / Basket",
        accent,
        data.get("index_issuers", []),
        data.get("index_top10", []),
        data.get("index_bottom10", []),
        include_yield=include_yield,
        breakdown=data.get("index_by_category", []),
        breakdown_label="Category",
        breakdown_direction=is_li,
        breakdown_type=not is_li,
    )

    # --- 5. Single Stock Section ---
    body += _segment_section(
        "Single Stock",
        _NAVY,
        data.get("ss_issuers", []),
        data.get("ss_top10", []),
        data.get("ss_bottom10", []),
        include_yield=include_yield,
        breakdown=data.get("ss_by_underlier", []),
        breakdown_label="Underlier",
        breakdown_direction=is_li,
        breakdown_type=not is_li,
    )

    return _wrap_email(title, accent, body, dashboard_url, date_str)


# ---------------------------------------------------------------------------
# L&I Report Email
# ---------------------------------------------------------------------------
def build_li_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build executive-ready email for U.S. Leveraged & Inverse ETP Report.

    Returns (html, images) where images is always [] (no CID images in v3).
    """
    from webapp.services.report_data import get_li_report
    data = get_li_report(db)

    date_short = _data_date_short(data)
    title = f"U.S. Leveraged & Inverse ETF Report: {date_short}"

    html = _build_report_email(
        data, "li", title, _TEAL,
        dashboard_url=dashboard_url, include_yield=False,
    )
    return html, []


# ---------------------------------------------------------------------------
# Income (Covered Call) Report Email
# ---------------------------------------------------------------------------
def build_cc_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build executive-ready email for Income (Covered Call) ETFs report.

    Returns (html, images) where images is always [] (no CID images in v3).
    """
    from webapp.services.report_data import get_cc_report
    data = get_cc_report(db)

    date_short = _data_date_short(data)
    title = f"Income ETF Report: {date_short}"

    html = _build_report_email(
        data, "cc", title, _BLUE,
        dashboard_url=dashboard_url, include_yield=True,
    )
    return html, []


# ---------------------------------------------------------------------------
# Backward compat: cid_to_data_uri (no-op since v3 has no CID images)
# ---------------------------------------------------------------------------
def cid_to_data_uri(html: str, images: list[tuple[str, bytes, str]]) -> str:
    """Replace cid: references with data: URIs for browser preview.

    In v3 this is a no-op since there are no CID images.
    """
    if not images:
        return html
    import base64
    for cid, png_bytes, _ in images:
        b64 = base64.b64encode(png_bytes).decode()
        html = html.replace(f"cid:{cid}", f"data:image/png;base64,{b64}")
    return html
