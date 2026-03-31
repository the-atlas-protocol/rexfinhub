"""
V3 Report emails: L&I and Income with unified segmented format.

Both emails share identical layout:
  1. Header + Date
  2. KPI Banner (Index/ETF/Basket row + Single Stock row)
  3. AUM Timeline (area chart with REX overlay + product count)
  4. REX Spotlight (top 8 flagship products)
  5. Index/ETF/Basket Section (charts + tables)
  6. Single Stock Section (charts + tables)
  7. Footer

Income adds a Yield column in fund tables and Avg Yield in KPIs.
No CID images or matplotlib -- pure HTML tables + CSS charts.
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
_HIGHLIGHT_BG = "#f4f5f6"
_REX_GREEN = "#27ae60"
_REX_GREEN_LIGHT = "rgba(39,174,96,0.25)"

# Suite brand colors (section title underline + segment divider)
_SUITE_COLORS = {
    "T-REX": "#A44A3F",
    "MicroSectors": "#00B050",
    "Growth & Income": "#4BB3FD",
    "Equity Premium Income": "#F3DFC1",
    "IncomeMax": _ORANGE,
    "Autocallable": "#F3DFC1",
    "Crypto": "#5227CA",
    "Thematic": "#0496FF",
}

_CHART_COLORS = ["#0984e3", "#00897B", "#e67e22", "#8e44ad", "#e74c3c",
                 "#2ecc71", "#f39c12", "#3498db", "#1abc9c", "#e91e63"]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _flow_color(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return _GRAY
    return _GREEN if val >= 0 else _RED


def _fmt_currency(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val == 0:
        return "--"
    sign = "-" if val < 0 else ""
    av = abs(val)
    if av >= 1_000:
        return f"{sign}${av / 1_000:,.1f}B"
    if av >= 1:
        return f"{sign}${av:,.1f}M"
    return f"{sign}${av:.2f}M"


def _fmt_flow(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val == 0:
        return "--"
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency(val)}"


def _fmt_pct(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val == 0:
        return "--"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _fmt_pct_nosign(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val == 0:
        return "--"
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


def _date_mm_dd(data: dict) -> str:
    """Return MM/DD/YYYY date string for report titles."""
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
    # Always show today's date in the report header (not the data sync date)
    date_str = datetime.now().strftime("%B %d, %Y")
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    cta = ""
    if dash_link:
        cta = f"""
<tr><td style="padding:20px 30px;text-align:center;">
  <a href="{dash_link}/" style="display:inline-block;padding:14px 32px;
    background:{_BLUE};color:{_WHITE};text-decoration:none;border-radius:8px;
    font-weight:600;font-size:14px;">Visit REX FinHub</a>
</td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(title)}</title></head>
<body style="margin:0;padding:0;background:{_LIGHT};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="640" cellpadding="0" cellspacing="0" border="0"
  style="background:{_WHITE};border-radius:8px;overflow:hidden;
  box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:640px;table-layout:fixed;">

<!-- Header -->
<tr><td style="background:{_NAVY};padding:24px 30px;">
  <div style="font-size:22px;font-weight:700;color:{_WHITE};letter-spacing:-0.5px;">{_esc(title)} | {_esc(date_str)}</div>
</td></tr>

{body}
{cta}

<!-- Footer -->
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};text-align:center;">
  <div style="font-size:11px;color:{_GRAY};">
    REX Financial Intelligence Hub &middot; Data sourced from Bloomberg L.P. and REX Shares, LLC
  </div>
  <div style="font-size:10px;color:{_GRAY};margin-top:4px;font-style:italic;">
    Note: ETN data reflects proprietary share/price data where available. Bloomberg-reported ETN figures may differ.
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


# ---------------------------------------------------------------------------
# Shared section renderers
# ---------------------------------------------------------------------------
def _kpi_row(kpis: list[tuple[str, str, str]], label: str = "") -> str:
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


def _flow_dual_kpi(market_row: list, rex_row: list | None = None) -> str:
    """Compact dual-row KPI box: Market row on top, REX row below, single border.

    Each item is (label, value) or (label, value, is_positive_flag).
    """
    def _cell(label: str, value: str, color: str = _NAVY) -> str:
        label_html = _esc(label).replace("\n", "<br>")
        return (
            f'<td style="padding:6px 4px;text-align:center;">'
            f'<div style="font-size:16px;font-weight:700;color:{color};">{_esc(value)}</div>'
            f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;'
            f'letter-spacing:0.4px;margin-top:1px;">{label_html}</div></td>'
        )

    def _build_cells(items: list) -> str:
        cells = []
        for item in items:
            if len(item) == 3:
                label, value, is_positive = item
                color = _GREEN if is_positive else _RED
            else:
                label, value = item[0], item[1]
                color = _NAVY
            cells.append(_cell(label, value, color))
        return "".join(cells)

    mkt_cells = _build_cells(market_row)
    rows = f'<tr style="background:{_LIGHT};">{mkt_cells}</tr>'
    if rex_row:
        rex_cells = _build_cells(rex_row)
        rows += (
            f'<tr><td colspan="{len(market_row)}" style="padding:0;">'
            f'<div style="border-top:1px solid {_BORDER};"></div></td></tr>'
            f'<tr style="background:{_REX_ROW_BG};">{rex_cells}</tr>'
        )

    return (
        f'<tr><td style="padding:10px 30px 5px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid {_BORDER};border-radius:8px;overflow:hidden;">'
        f'{rows}'
        f'</table></td></tr>'
    )


def _section_title(title: str, accent: str = _NAVY) -> str:
    return (
        f'<tr><td style="padding:18px 30px 5px;">'
        f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;'
        f'padding-bottom:6px;border-bottom:2px solid {accent};">{_esc(title)}</div>'
        f'</td></tr>'
    )


def _segment_block(content: str, accent: str = _NAVY) -> str:
    """Pass-through wrapper — colored bar is on the section title itself."""
    return content


def _sub_heading(title: str) -> str:
    return (
        f'<tr><td style="padding:10px 30px 2px;">'
        f'<div style="font-size:13px;font-weight:700;color:{_NAVY};">{_esc(title)}</div>'
        f'</td></tr>'
    )


def _table(headers: list[str], rows: list[list[str]], align: list[str] | None = None,
           highlight_col: int | None = None, bold_last_row: bool = False,
           rex_rows: set[int] | None = None,
           col_widths: list[str] | None = None,
           nowrap: bool = False) -> str:
    if not rows:
        return '<tr><td style="padding:10px 30px;color:#636e72;font-size:13px;">No data available.</td></tr>'

    n = len(headers)
    if align is None:
        align = ["left"] * n

    _th = (f"padding:8px 10px;background:{_LIGHT};font-size:10px;color:{_GRAY};"
           f"text-transform:uppercase;letter-spacing:0.5px;font-weight:600;"
           f"border-bottom:2px solid {_BORDER};")
    _td = f"padding:6px 10px;font-size:12px;color:{_NAVY};border-bottom:1px solid {_BORDER};"
    if nowrap:
        _td += "white-space:nowrap;"

    header_cells = ""
    for i, h in enumerate(headers):
        w = f"width:{col_widths[i]};" if col_widths and i < len(col_widths) else ""
        nw = "white-space:nowrap;" if nowrap else ""
        h_html = h if "<br>" in h or "<span" in h else _esc(h)
        header_cells += f'<th style="{_th}text-align:{align[i]};{w}{nw}">{h_html}</th>'

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
    """Transposed REX product table -- top 8 flagship products, no wrapping."""
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
    return _section_title("REX Spotlight", accent) + _table(headers, rows, aligns, nowrap=True)


# ---------------------------------------------------------------------------
# AUM Timeline — Chart.js rendered as image via QuickChart.io
# ---------------------------------------------------------------------------
def _fmt_aum_axis(val: float) -> str:
    """Y-axis label formatter matching catFmtMoney from market.js."""
    if abs(val) >= 1_000:
        return f"${val / 1_000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:.0f}M"
    return f"${val * 1_000:.0f}K"


def _aum_timeline_chart(timeline: dict, accent: str = _TEAL) -> str:
    """Render a proper Chart.js line/area chart as an image via QuickChart.io.

    Replicates the category view style: area fill for AUM series, line for
    product count on a second Y-axis.
    """
    import json
    from urllib.parse import quote

    labels = timeline.get("labels", [])
    total_aum = timeline.get("total_aum", [])
    rex_aum = timeline.get("rex_aum", [])
    product_count = timeline.get("product_count", [])

    if not labels or not total_aum:
        return ""

    # Colors matching category view style
    aum_border = accent
    aum_fill = (accent + "30")  # 19% opacity hex suffix
    rex_border = _REX_GREEN
    rex_fill = _REX_GREEN + "40"  # 25% opacity
    cnt_color = _ORANGE

    # Convert AUM from $M to $B for cleaner axis labels
    aum_b = [round(v / 1000, 2) for v in total_aum]
    rex_b = [round(v / 1000, 2) for v in rex_aum]

    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Total AUM ($B)",
                    "data": aum_b,
                    "borderColor": aum_border,
                    "backgroundColor": aum_fill,
                    "fill": True,
                    "tension": 0.3,
                    "pointRadius": 2,
                    "borderWidth": 2,
                    "yAxisID": "y",
                    "order": 2,
                },
                {
                    "label": "REX AUM ($B)",
                    "data": rex_b,
                    "borderColor": rex_border,
                    "backgroundColor": rex_fill,
                    "fill": True,
                    "tension": 0.3,
                    "pointRadius": 2,
                    "borderWidth": 2,
                    "yAxisID": "y",
                    "order": 3,
                },
                {
                    "label": "# Products",
                    "data": [int(c) for c in product_count],
                    "borderColor": cnt_color,
                    "backgroundColor": cnt_color,
                    "fill": False,
                    "tension": 0.3,
                    "pointRadius": 2,
                    "borderWidth": 2,
                    "borderDash": [5, 3],
                    "yAxisID": "y1",
                    "order": 1,
                },
            ],
        },
        "options": {
            "responsive": True,
            "interaction": {"mode": "index", "intersect": False},
            "scales": {
                "x": {
                    "ticks": {"maxRotation": 0, "font": {"size": 10}, "color": "#6b7280"},
                    "grid": {"display": False},
                },
                "y": {
                    "title": {"display": True, "text": "AUM ($B)", "font": {"size": 11}, "color": "#6b7280"},
                    "ticks": {"font": {"size": 10}, "color": "#6b7280"},
                    "grid": {"color": "#f3f4f6"},
                },
                "y1": {
                    "position": "right",
                    "title": {"display": True, "text": "# Products", "font": {"size": 11}, "color": cnt_color},
                    "ticks": {"font": {"size": 10}, "color": cnt_color},
                    "grid": {"drawOnChartArea": False},
                },
            },
            "plugins": {
                "legend": {
                    "display": True,
                    "labels": {"font": {"size": 11}, "usePointStyle": True, "color": "#6b7280"},
                },
            },
        },
    }

    chart_json = json.dumps(chart_config, separators=(",", ":"))
    chart_url = f"https://quickchart.io/chart?c={quote(chart_json)}&w=600&h=250&bkg=%23ffffff&v=4"

    return (
        f'<tr><td style="padding:12px 30px 8px;">'
        f'<div style="font-size:11px;font-weight:600;color:{_GRAY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:8px;">AUM Timeline (3 Years)</div>'
        f'<img src="{chart_url}" width="600" height="250" alt="AUM Timeline Chart" '
        f'style="display:block;width:100%;max-width:600px;height:auto;border-radius:6px;" />'
        f'</td></tr>'
    )


# ---------------------------------------------------------------------------
# Inline HTML charts (email-safe, no images)
# ---------------------------------------------------------------------------
def _horizontal_bar_chart(items: list[dict], value_key: str = "market_share",
                          label_key: str = "name", value_fmt_key: str = "aum_fmt",
                          title: str = "", max_bars: int = 8,
                          accent: str = _TEAL) -> str:
    """Render a horizontal bar chart using pure HTML tables."""
    if not items:
        return ""
    items = items[:max_bars]
    max_val = max(abs(b.get(value_key, 0)) for b in items) or 1

    bars_html = ""
    for i, b in enumerate(items):
        val = b.get(value_key, 0)
        pct = abs(val) / max_val * 100
        bar_width = max(pct, 2)
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


def _flow_bars(inflows: list[dict], outflows: list[dict], n: int = 10) -> str:
    """Bi-directional flow chart with dynamic zero-point.

    - All positive: 0 on left, largest inflow on right
    - All negative: largest outflow on left, 0 on right
    - Mixed: largest outflow on left, largest inflow on right, 0 in between
    """
    top_in = sorted([f for f in inflows[:n] if f.get("flow_1w", 0) > 0],
                    key=lambda f: f["flow_1w"], reverse=True)
    top_out = sorted([f for f in outflows[:n] if f.get("flow_1w", 0) < 0],
                     key=lambda f: abs(f["flow_1w"]), reverse=True)
    if not top_in and not top_out:
        return ""

    max_pos = max((f["flow_1w"] for f in top_in), default=0)
    max_neg = max((abs(f["flow_1w"]) for f in top_out), default=0)
    total_range = max_pos + max_neg
    if total_range == 0:
        total_range = 1

    # Bar area as percentage of the inner table width
    bar_area = 100  # use full width of the bar cell
    if max_neg == 0:
        zero_pct = 0  # all positive: zero at left edge
    elif max_pos == 0:
        zero_pct = bar_area  # all negative: zero at right edge
    else:
        zero_pct = max_neg / total_range * bar_area

    def _bar_row(f: dict) -> str:
        flow = f.get("flow_1w", 0)
        ticker = _esc(f["ticker"])
        fmt = _esc(f["flow_1w_fmt"])
        is_pos = flow >= 0
        bar_len = abs(flow) / total_range * bar_area
        bar_len = max(bar_len, 1.5)
        color = _GREEN if is_pos else _RED
        txt_color = _GREEN if is_pos else _RED

        if is_pos:
            # Bar goes right from zero: [left_pad][zero|green_bar][right_pad]
            left = zero_pct
            right = bar_area - zero_pct - bar_len
            bar = (f'<td style="width:{max(left, 0):.1f}%;font-size:0;">&nbsp;</td>'
                   f'<td style="width:{bar_len:.1f}%;background:{color};height:14px;'
                   f'border-radius:0 3px 3px 0;font-size:0;">&nbsp;</td>'
                   f'<td style="width:{max(right, 0):.1f}%;font-size:0;">&nbsp;</td>')
        else:
            # Bar goes left from zero: [left_pad][red_bar|zero][right_pad]
            left = zero_pct - bar_len
            right = bar_area - zero_pct
            bar = (f'<td style="width:{max(left, 0):.1f}%;font-size:0;">&nbsp;</td>'
                   f'<td style="width:{bar_len:.1f}%;background:{color};height:14px;'
                   f'border-radius:3px 0 0 3px;font-size:0;">&nbsp;</td>'
                   f'<td style="width:{max(right, 0):.1f}%;font-size:0;">&nbsp;</td>')

        return (f'<tr>'
                f'<td style="padding:2px 0;font-size:11px;color:{_NAVY};width:65px;'
                f'white-space:nowrap;overflow:hidden;">{ticker}</td>'
                f'<td style="padding:2px 4px;">'
                f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
                f'style="border-collapse:collapse;"><tr>{bar}</tr></table></td>'
                f'<td style="padding:2px 4px;font-size:11px;color:{txt_color};text-align:right;'
                f'white-space:nowrap;width:70px;font-weight:600;">{fmt}</td></tr>')

    rows = ""
    if top_in:
        rows += (f'<tr><td colspan="3" style="padding:4px 0 2px;font-size:10px;'
                 f'font-weight:600;color:{_GREEN};text-transform:uppercase;'
                 f'letter-spacing:0.5px;">Inflows</td></tr>')
        for f in top_in:
            rows += _bar_row(f)
    if top_out:
        rows += (f'<tr><td colspan="3" style="padding:6px 0 2px;font-size:10px;'
                 f'font-weight:600;color:{_RED};text-transform:uppercase;'
                 f'letter-spacing:0.5px;">Outflows</td></tr>')
        for f in top_out:
            rows += _bar_row(f)

    return (
        f'<tr><td style="padding:8px 30px 10px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;padding:10px 12px;">'
        f'<tr><td colspan="3" style="padding:0 0 6px;font-size:11px;font-weight:600;'
        f'color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Weekly Fund Flows</td></tr>'
        f'{rows}'
        f'</table></td></tr>'
    )


def _issuer_share_bars(issuers: list[dict], n: int = 6) -> str:
    """Render a stacked market share bar for top issuers."""
    if not issuers:
        return ""
    top = issuers[:n]
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
                   f'{name} ({share:.1f}%)</td>')

    other_share = 100 - sum(iss.get("market_share", 0) for iss in top)
    if other_share > 1:
        segments += (f'<td style="width:{other_share:.1f}%;background:{_BORDER};height:22px;'
                     f'font-size:0;line-height:0;">&nbsp;</td>')
        legend += (f'<td style="padding:3px 6px 3px 0;font-size:10px;color:{_GRAY};'
                   f'white-space:nowrap;">'
                   f'<span style="display:inline-block;width:8px;height:8px;'
                   f'background:{_BORDER};border-radius:2px;margin-right:3px;'
                   f'vertical-align:middle;"></span>'
                   f'Other ({other_share:.1f}%)</td>')

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


def _flow_share_bar(issuers: list[dict], n: int = 6) -> str:
    """Market share bar with REX highlighted in green, others in blue/gray."""
    if not issuers:
        return ""
    top = issuers[:n]
    segments_html = ""
    legend = ""
    for i, iss in enumerate(top):
        share = iss.get("market_share", 0)
        is_rex = iss.get("is_rex", False)
        color = _REX_GREEN if is_rex else _CHART_COLORS[i % len(_CHART_COLORS)]
        name = _esc(iss["issuer"][:18])
        label_style = f"font-weight:700;" if is_rex else ""
        if share >= 1:
            segments_html += (
                f'<td style="width:{share:.1f}%;background:{color};height:22px;'
                f'font-size:0;line-height:0;">&nbsp;</td>'
            )
        legend += (
            f'<td style="padding:3px 6px 3px 0;font-size:10px;color:{_NAVY};'
            f'white-space:nowrap;{label_style}">'
            f'<span style="display:inline-block;width:8px;height:8px;'
            f'background:{color};border-radius:2px;margin-right:3px;'
            f'vertical-align:middle;"></span>'
            f'{name} ({share:.1f}%)</td>'
        )

    other_share = 100 - sum(iss.get("market_share", 0) for iss in top)
    if other_share > 1:
        segments_html += (
            f'<td style="width:{other_share:.1f}%;background:{_BORDER};height:22px;'
            f'font-size:0;line-height:0;">&nbsp;</td>'
        )
        legend += (
            f'<td style="padding:3px 6px 3px 0;font-size:10px;color:{_GRAY};'
            f'white-space:nowrap;">'
            f'<span style="display:inline-block;width:8px;height:8px;'
            f'background:{_BORDER};border-radius:2px;margin-right:3px;'
            f'vertical-align:middle;"></span>'
            f'Other ({other_share:.1f}%)</td>'
        )

    return (
        f'<tr><td style="padding:8px 30px 4px;">'
        f'<div style="font-size:11px;font-weight:600;color:{_GRAY};text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-bottom:6px;">Market Share</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;border-radius:6px;overflow:hidden;">'
        f'<tr>{segments_html}</tr></table>'
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
            tac = b.get("num_tactical", 0)
            if tac:
                row.append(f'{b.get("num_long", 0)}L / {b.get("num_short", 0)}S / {tac}T')
            else:
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


def _segment_tables(issuers: list[dict], top10: list[dict], bottom10: list[dict],
                    include_yield: bool = False,
                    breakdown: list[dict] | None = None,
                    breakdown_label: str = "Category",
                    breakdown_direction: bool = False,
                    breakdown_type: bool = False,
                    accent: str = _TEAL) -> str:
    """Build segment tables: Issuer table, breakdown + flows.

    Market share stacked bars removed — covered by Market Position section
    with historical charts at the top of the report.
    """
    body = ""

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
            if iss.get("is_rex", False):
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

    # --- Attribute breakdown table ---
    if breakdown:
        body += _breakdown_table(
            breakdown, breakdown_label,
            include_yield=include_yield,
            include_direction=breakdown_direction,
            include_type=breakdown_type,
        )

    # --- Flow chart (replaces Top 10 tables) ---
    if top10 or bottom10:
        body += _flow_bars(top10, bottom10, n=10)

    return body


# ---------------------------------------------------------------------------
# Unified report email builder
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Key Highlights — Executive callout boxes
# ---------------------------------------------------------------------------
def _key_highlights_box(bullets: list[str]) -> str:
    """Prominent highlights callout box at the top of the report.

    Dark left border, light background, bullet points.
    Designed to be the first thing an executive sees after the header.
    """
    if not bullets:
        return ""
    bg = _HIGHLIGHT_BG
    items = ""
    for b in bullets:
        items += (
            f'<tr><td style="padding:3px 0;font-size:13px;color:{_NAVY};line-height:1.5;">'
            f'<span style="color:{_NAVY};font-weight:700;margin-right:6px;">&#8226;</span>'
            f'{_esc(b)}</td></tr>'
        )
    return (
        f'<tr><td style="padding:15px 30px 10px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{bg};border-left:4px solid {_NAVY};border-radius:0 8px 8px 0;">'
        f'<tr><td style="padding:14px 18px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="padding:0 0 8px;font-size:13px;font-weight:700;color:{_NAVY};'
        f'text-transform:uppercase;letter-spacing:1px;">Key Highlights</td></tr>'
        f'{items}'
        f'</table></td></tr>'
        f'</table></td></tr>'
    )


def _li_highlights(data: dict) -> list[str]:
    """Generate 3-5 executive highlights for the L&I report."""
    bullets = []
    kpis = data.get("kpis", {})
    if not kpis:
        return bullets

    # 1. Market direction
    total_aum = kpis.get("total_aum", "--")
    flow_1w = kpis.get("flow_1w", "--")
    count = kpis.get("count", 0)
    bullets.append(f"L&I market: {total_aum} AUM across {count:,} ETPs ({flow_1w} 1W net flow)")

    # 2. REX position
    rex = data.get("rex_kpis", {})
    if rex and rex.get("count", 0) > 0:
        bullets.append(
            f"REX: {rex['count']} funds, {rex['total_aum']} AUM ({rex['share']} market share)"
        )

    # 3. Top REX fund mover
    rex_funds = data.get("rex_funds", [])
    if not rex_funds:
        rex_funds = (data.get("ss_rex_funds", []) or []) + (data.get("index_rex_funds", []) or [])
    if rex_funds:
        top_rex = max(rex_funds, key=lambda f: abs(f.get("flow_1w", 0)))
        if abs(top_rex.get("flow_1w", 0)) > 0:
            bullets.append(
                f"{top_rex['ticker']}: {top_rex['flow_1w_fmt']} 1W flow -- top REX mover"
            )

    # 4. Top competitor issuer
    all_issuers = (data.get("ss_issuers", []) or []) + (data.get("index_issuers", []) or [])
    non_rex = [i for i in all_issuers if not i.get("is_rex", False)]
    if non_rex:
        top_iss = max(non_rex, key=lambda i: abs(i.get("flow_1w", 0)))
        if abs(top_iss.get("flow_1w", 0)) > 0:
            bullets.append(
                f"{top_iss['issuer']}: {top_iss['flow_1w_fmt']} 1W -- top competitor"
            )

    # 5. Segment comparison
    ss_kpis = data.get("ss_kpis", {})
    idx_kpis = data.get("index_kpis", {})
    if ss_kpis.get("count", 0) > 0 and idx_kpis.get("count", 0) > 0:
        ss_flow = ss_kpis.get("flow_1w", "--")
        idx_flow = idx_kpis.get("flow_1w", "--")
        bullets.append(f"Single stock: {ss_flow} 1W vs index/ETF: {idx_flow} 1W")

    return bullets[:5]


def _cc_highlights(data: dict) -> list[str]:
    """Generate 3-5 executive highlights for the Income report."""
    bullets = []
    kpis = data.get("kpis", {})
    if not kpis:
        return bullets

    # 1. Market direction
    total_aum = kpis.get("total_aum", "--")
    flow_1w = kpis.get("flow_1w", "--")
    count = kpis.get("count", 0)
    avg_yield = kpis.get("avg_yield", "--")
    bullets.append(f"Income market: {total_aum} AUM, {count:,} ETPs, {avg_yield} avg yield ({flow_1w} 1W)")

    # 2. REX position
    rex = data.get("rex_kpis", {})
    if rex and rex.get("count", 0) > 0:
        bullets.append(
            f"REX: {rex['count']} funds, {rex['total_aum']} AUM ({rex['share']} market share)"
        )

    # 3. Top yielding REX fund
    rex_funds = data.get("rex_funds", [])
    if not rex_funds:
        rex_funds = (data.get("ss_rex_funds", []) or []) + (data.get("index_rex_funds", []) or [])
    yielders = [f for f in rex_funds if f.get("yield_val", 0) > 0]
    if yielders:
        top_yield = max(yielders, key=lambda f: f.get("yield_val", 0))
        bullets.append(
            f"{top_yield['ticker']}: {top_yield['yield_fmt']} yield -- top REX yielder"
        )

    # 4. Top REX flow mover
    if rex_funds:
        top_rex = max(rex_funds, key=lambda f: abs(f.get("flow_1w", 0)))
        if abs(top_rex.get("flow_1w", 0)) > 0:
            bullets.append(
                f"{top_rex['ticker']}: {top_rex['flow_1w_fmt']} 1W flow -- top REX mover"
            )

    # 5. Top competitor issuer
    all_issuers = (data.get("ss_issuers", []) or []) + (data.get("index_issuers", []) or [])
    non_rex = [i for i in all_issuers if not i.get("is_rex", False)]
    if non_rex:
        top_iss = max(non_rex, key=lambda i: abs(i.get("flow_1w", 0)))
        if abs(top_iss.get("flow_1w", 0)) > 0:
            bullets.append(
                f"{top_iss['issuer']}: {top_iss['flow_1w_fmt']} 1W -- top competitor"
            )

    return bullets[:5]


def _flow_highlights(data: dict) -> list[str]:
    """Generate 3-5 executive highlights for the Flow report."""
    bullets = []

    # 1. Full market overview
    grand = data.get("grand_kpis", {})
    if grand:
        bullets.append(
            f"ETP Universe: {grand.get('total_aum', '$0')} AUM across "
            f"{grand.get('count', 0):,} active ETPs ({grand.get('flow_1w', '$0')} 1W net flow)"
        )

    # 2. REX Financial position
    rex = data.get("rex_kpis", {})
    if rex and rex.get("count", 0) > 0:
        bullets.append(
            f"REX Financial: {rex.get('count', 0)} funds, {rex.get('total_aum', '$0')} AUM "
            f"({rex.get('market_share', '0.0%')} market share)"
        )

    # 3. Top REX fund mover
    rex_funds = data.get("rex_funds", [])
    if rex_funds:
        top_rex = max(rex_funds, key=lambda f: abs(f.get("flow_1w", 0)))
        if abs(top_rex.get("flow_1w", 0)) > 0:
            bullets.append(
                f"{top_rex['ticker']}: {top_rex.get('flow_1w_fmt', '$0')} 1W -- top REX mover"
            )

    # 4. Biggest suite by flow
    suites = data.get("suites", [])
    if suites:
        best = max(suites, key=lambda s: abs(
            sum(f.get("flow_1w", 0) for f in s.get("top10", []) + s.get("bottom10", []))
        ))
        best_flow = best["kpis"].get("flow_1w", "--")
        bullets.append(
            f"{best['label']} category: {best_flow} 1W flow, "
            f"{best['kpis'].get('count', 0)} ETPs"
        )

    # 5. Top competitor fund mover
    all_movers = []
    for suite in suites:
        for f in suite.get("top10", []) + suite.get("bottom10", []):
            if not f.get("is_rex", False):
                all_movers.append(f)
    if all_movers:
        top_comp = max(all_movers, key=lambda f: abs(f.get("flow_1w", 0)))
        if abs(top_comp.get("flow_1w", 0)) > 0:
            issuer = top_comp.get("issuer", "")
            iss_tag = f" ({issuer})" if issuer else ""
            bullets.append(
                f"{top_comp['ticker']}{iss_tag}: {top_comp.get('flow_1w_fmt', '$0')} 1W -- top competitor"
            )

    return bullets[:5]



# ---------------------------------------------------------------------------
# Flow-specific metrics (differentiate from L&I/Income reports)
# ---------------------------------------------------------------------------
def _flow_metrics_row(cat_kpis: dict, rex_kpis: dict, issuers: list[dict]) -> str:
    """Compact flow metrics row: capture rate + flow rank + flow momentum.

    These are flow-specific insights not found in L&I/Income reports.
    """
    metrics = []

    # 1. Flow capture rate: REX 1W flow / category 1W flow
    cat_flow_raw = cat_kpis.get("flow_1w_raw", 0)
    rex_flow_raw = rex_kpis.get("flow_1w_raw", 0)
    if cat_flow_raw and abs(cat_flow_raw) > 0 and rex_kpis.get("count", 0) > 0:
        capture = rex_flow_raw / cat_flow_raw * 100
        if capture != 0:
            sign = "+" if capture >= 0 else ""
            cap_color = _GREEN if capture >= 0 else _RED
            metrics.append(
                f'<td style="padding:6px 10px;text-align:center;">'
                f'<div style="font-size:14px;font-weight:700;color:{cap_color};">{sign}{capture:.1f}%</div>'
                f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;">Flow Capture</div></td>'
            )

    # 2. Flow Rank and Share Rank are now shown as badges in the suite title.
    #    Removed from metrics row to avoid duplication.

    # 3. Flow momentum: 1W vs 1M direction
    rex_1w = rex_kpis.get("flow_1w_raw", 0)
    rex_1m = rex_kpis.get("flow_1m_raw", 0)
    if rex_kpis.get("count", 0) > 0 and (rex_1w or rex_1m):
        if rex_1w > 0 and rex_1m > 0:
            momentum = "Sustained"
            mom_color = _GREEN
        elif rex_1w > 0 and rex_1m <= 0:
            momentum = "Turning"
            mom_color = _GREEN
        elif rex_1w <= 0 and rex_1m > 0:
            momentum = "Fading"
            mom_color = _ORANGE
        else:
            momentum = "Weak"
            mom_color = _RED
        metrics.append(
            f'<td style="padding:6px 10px;text-align:center;">'
            f'<div style="font-size:14px;font-weight:700;color:{mom_color};">{momentum}</div>'
            f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;">Momentum</div></td>'
        )

    if not metrics:
        return ""

    return (
        f'<tr><td style="padding:6px 30px 4px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_LIGHT};border-radius:6px;border:1px solid {_BORDER};">'
        f'<tr>{"".join(metrics)}</tr>'
        f'</table></td></tr>'
    )


# ---------------------------------------------------------------------------
# Market Position: summary table + per-segment charts
# ---------------------------------------------------------------------------
def _market_position_card(cat_db: str, label: str,
                           kpis: dict | None = None) -> str:
    """Market Position KPI card for a single segment — dual-row style."""
    try:
        from scripts.generate_market_share_charts import generate_category_charts
    except Exception as e:
        log.warning("Market share charts unavailable: %s", e)
        return ""

    try:
        r = generate_category_charts(cat_db, label)
    except Exception as e:
        log.warning("Chart generation failed for %s: %s", label, e)
        return ""

    if not r:
        return ""

    cur = r["cur"]
    yr1 = r.get("yr1")
    yr1_aum = _fmt_currency(yr1["rex_aum"]) if yr1 else "--"
    yr1_share = f"{yr1['rex_share']:.1f}%" if yr1 else "--"
    peak = r.get("peak_share", 0)
    kpi = kpis or {}
    rex_flow = kpi.get("rex_flow_1w", "--")
    flow_positive = kpi.get("rex_flow_1w_positive", True)

    html = _section_title("Market Position", _BLUE)
    market_row = [
        ("Market AUM", _fmt_currency(cur["total_aum"])),
        ("REX AUM", _fmt_currency(cur["rex_aum"])),
        ("REX Products", str(cur["rex_products"])),
        ("Market Share", f"{cur['rex_share']:.1f}%"),
    ]
    rex_row = [
        ("1W Flow", rex_flow, flow_positive),
        ("1Y Ago", yr1_aum),
        ("1Y Share", yr1_share),
        ("Peak Share", f"{peak:.1f}%"),
    ]
    html += _flow_dual_kpi(market_row, rex_row)
    return html


def _segment_charts(cat_db: str, label: str) -> str:
    """Generate REX position + competitive landscape charts for one segment."""
    try:
        from scripts.generate_market_share_charts import generate_category_charts
    except Exception as e:
        log.warning("Market share charts unavailable: %s", e)
        return ""

    try:
        r = generate_category_charts(cat_db, label)
    except Exception as e:
        log.warning("Chart generation failed for %s: %s", label, e)
        return ""

    if not r:
        return ""

    return f"""<tr><td style="padding:2px 30px;"><img src="data:image/png;base64,{r['rex_b64']}" width="580" style="width:100%;max-width:580px;height:auto;" alt="REX Position"></td></tr>
<tr><td style="padding:2px 30px 8px;"><img src="data:image/png;base64,{r['comp_b64']}" width="580" style="width:100%;max-width:580px;height:auto;" alt="Competitive Landscape"></td></tr>"""


def _build_report_email(data: dict, report_type: str, title: str,
                        dashboard_url: str = "", include_yield: bool = False,
                        highlights: list[str] | None = None) -> str:
    """Unified email builder for both L&I and Income reports.

    Layout:
      1. Key Highlights
      2. Per segment (Single Stock, then Index/ETF/Basket):
         Market Position KPIs -> Charts -> REX Spotlight -> Issuer table -> Breakdown -> Flows
    """
    date_str = _data_date_str(data)

    if not data.get("available") or not data.get("kpis"):
        return _wrap_email(title, _NAVY,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url, date_str)

    is_li = report_type == "li"
    body = ""

    # Category DB names for chart generation
    if is_li:
        ss_cat = "Leverage & Inverse - Single Stock"
        idx_cat = "Leverage & Inverse - Index/Basket/ETF Based"
        ss_label, idx_label = "L&I Single Stock", "L&I Index/ETF"
    else:
        ss_cat = "Income - Single Stock"
        idx_cat = "Income - Index/Basket/ETF Based"
        ss_label, idx_label = "Income Single Stock", "Income Index/ETF"

    # Key Highlights box (right after header, before any sections)
    if highlights:
        body += _key_highlights_box(highlights)

    # ====================================================================
    # SINGLE STOCK SEGMENT
    # ====================================================================
    ss_seg = _section_title("Single Stock", _NAVY)
    ss_seg += _market_position_card(ss_cat, ss_label, data.get("ss_kpis"))
    ss_seg += _segment_charts(ss_cat, ss_label)
    ss_rex = data.get("ss_rex_funds", [])
    if ss_rex:
        ss_seg += _rex_spotlight(ss_rex, _GREEN)
    ss_seg += _segment_tables(
        data.get("ss_issuers", []),
        data.get("ss_top10", []),
        data.get("ss_bottom10", []),
        include_yield=include_yield,
        breakdown=data.get("ss_by_underlier", []),
        breakdown_label="Underlier",
        breakdown_direction=is_li,
        breakdown_type=not is_li,
    )
    body += _segment_block(ss_seg, _NAVY)

    # ====================================================================
    # INDEX / ETF / BASKET SEGMENT
    # ====================================================================
    idx_seg = _section_title("Index / ETF / Basket", _NAVY)
    idx_seg += _market_position_card(idx_cat, idx_label, data.get("index_kpis"))
    idx_seg += _segment_charts(idx_cat, idx_label)
    idx_rex = data.get("index_rex_funds", [])
    if idx_rex:
        idx_seg += _rex_spotlight(idx_rex, _GREEN)
    idx_seg += _segment_tables(
        data.get("index_issuers", []),
        data.get("index_top10", []),
        data.get("index_bottom10", []),
        include_yield=include_yield,
        breakdown=data.get("index_by_category", []),
        breakdown_label="Category",
        breakdown_direction=is_li,
        breakdown_type=not is_li,
    )
    body += _segment_block(idx_seg, _NAVY)

    return _wrap_email(title, _NAVY, body, dashboard_url, date_str)


# ---------------------------------------------------------------------------
# L&I Report Email
# ---------------------------------------------------------------------------
def build_li_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build executive-ready email for U.S. Leveraged & Inverse ETP Report.

    Returns (html, images) where images is always [] (no CID images in v3).
    """
    from webapp.services.report_data import get_li_report
    data = get_li_report(db)

    date_str = _data_date_str(data)
    date_mm_dd = _date_mm_dd(data)
    title = "REX ETP Leverage & Inverse Report"

    highlights = _li_highlights(data)
    html = _build_report_email(
        data, "li", title,
        dashboard_url=dashboard_url, include_yield=False,
        highlights=highlights,
    )
    return html, []


# ---------------------------------------------------------------------------
# Income (Covered Call) Report Email
# ---------------------------------------------------------------------------
def build_cc_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build executive-ready email for Income (Covered Call) ETPs report.

    Returns (html, images) where images is always [] (no CID images in v3).
    """
    from webapp.services.report_data import get_cc_report
    data = get_cc_report(db)

    date_str = _data_date_str(data)
    date_mm_dd = _date_mm_dd(data)
    title = "REX ETP Income Report"

    highlights = _cc_highlights(data)
    html = _build_report_email(
        data, "cc", title,
        dashboard_url=dashboard_url, include_yield=True,
        highlights=highlights,
    )
    return html, []


# ---------------------------------------------------------------------------
# Backward compat: cid_to_data_uri (no-op since v3 has no CID images)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Flow Report Email (REX vs Competitors)
# ---------------------------------------------------------------------------
def build_flow_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build REX ETP Competitive Flow Report email (suite-based v5).

    v5 layout -- CEO-driven, full market + REX suite peer comparison:
      1. Key Highlights (auto-generated bullets)
      2. Grand KPIs (ALL active ETPs)
      3. REX Financial KPIs
      4. Per Suite (T-REX, MicroSectors, Growth & Income,
                    Premium Income, Autocallable, Thematic):
         - Category KPIs
         - REX KPIs with market share
         - Market share bar (REX highlighted green)
         - Issuer comparison table
         - Top 10 / Bottom 10 flow bars
    """
    from webapp.services.report_data import get_flow_report
    data = get_flow_report(db)

    # If DB cache has old format (pre-v5), recompute locally
    if data.get("available") and "grand_kpis" not in data:
        data = get_flow_report(None)

    date_str = _data_date_str(data)
    title = "REX ETP Flow Report"

    if not data.get("available"):
        return _wrap_email(title, _NAVY,
                           '<tr><td style="padding:20px 30px;">Flow report data not available.</td></tr>',
                           dashboard_url, date_str), []

    grand_kpis = data.get("grand_kpis", {})
    rex_kpis = data.get("rex_kpis", {})
    suites = data.get("suites", [])

    body = ""

    # --- 1. Key Highlights ---
    highlights = _flow_highlights(data)
    body += _key_highlights_box(highlights)

    # --- 2. Market + REX snapshot (compact dual-row) ---
    body += _section_title("ETP Market Overview")
    body += _flow_dual_kpi(
        market_row=[
            ("Active ETPs", f'{grand_kpis.get("count", 0):,}'),
            ("Market AUM", grand_kpis.get("total_aum", "--")),
            ("1W Flow", grand_kpis.get("flow_1w", "--"),
             grand_kpis.get("flow_1w_positive", True)),
            ("1M Flow", grand_kpis.get("flow_1m", "--"),
             grand_kpis.get("flow_1m_positive", True)),
        ],
        rex_row=[
            ("REX Funds", str(rex_kpis.get("count", 0))),
            ("REX AUM", rex_kpis.get("total_aum", "--")),
            ("REX 1W Flow", rex_kpis.get("flow_1w", "--"),
             rex_kpis.get("flow_1w_positive", True)),
            ("REX 1M Flow", rex_kpis.get("flow_1m", "--"),
             rex_kpis.get("flow_1m_positive", True)),
        ],
    )

    # --- 4. Per-Suite Deep Dives ---
    for suite in suites:
        kpis = suite.get("kpis", {})
        rex_s = suite.get("rex_kpis", {})

        # Skip empty suites
        if kpis.get("count", 0) == 0:
            continue

        suite_label = suite["label"]
        suite_color = _SUITE_COLORS.get(suite_label, _NAVY)

        # Pre-compute ranks from FULL issuer list (REX may be outside top 8)
        all_issuers = suite.get("issuers", [])
        issuers = all_issuers[:8]
        _flow_rank = _share_rank = None
        if all_issuers and rex_s.get("count", 0) > 0:
            for i, iss in enumerate(sorted(all_issuers, key=lambda x: x.get("flow_1w", 0), reverse=True)):
                if iss.get("is_rex", False):
                    _flow_rank = i + 1
                    break
            for i, iss in enumerate(sorted(all_issuers, key=lambda x: x.get("market_share", 0), reverse=True)):
                if iss.get("is_rex", False):
                    _share_rank = i + 1
                    break

        # Section title with rank badges
        rank_badges = ""
        if _flow_rank is not None:
            fc = _GREEN if _flow_rank <= 3 else _GRAY
            rank_badges += (
                f'<span style="display:inline-block;margin-left:10px;padding:2px 8px;'
                f'border-radius:4px;font-size:11px;font-weight:700;color:{fc};'
                f'background:{_LIGHT};border:1px solid {_BORDER};">'
                f'Flow #{_flow_rank}</span>'
            )
        if _share_rank is not None:
            sc = _GREEN if _share_rank <= 3 else _GRAY
            rank_badges += (
                f'<span style="display:inline-block;margin-left:6px;padding:2px 8px;'
                f'border-radius:4px;font-size:11px;font-weight:700;color:{sc};'
                f'background:{_LIGHT};border:1px solid {_BORDER};">'
                f'Share #{_share_rank}</span>'
            )

        seg = (
            f'<tr><td style="padding:18px 30px 5px;">'
            f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;'
            f'padding-bottom:6px;border-bottom:2px solid {suite_color};">'
            f'{_esc(suite_label)}{rank_badges}</div>'
            f'</td></tr>'
        )

        # Peer label
        seg += (
            f'<tr><td style="padding:0 30px 8px;">'
            f'<div style="font-size:11px;color:{_GRAY};font-style:italic;">'
            f'Peer group: {_esc(suite.get("peer_label", ""))}'
            f'</div></td></tr>'
        )

        # Category + REX KPIs (compact dual-row)
        rex_row = None
        if rex_s.get("count", 0) > 0:
            rex_row = [
                ("REX Funds", str(rex_s.get("count", 0))),
                ("REX AUM", rex_s.get("total_aum", "--")),
                ("REX 1W Flow", rex_s.get("flow_1w", "--"),
                 rex_s.get("flow_1w_positive", True)),
                ("REX 1M Flow", rex_s.get("flow_1m", "--"),
                 rex_s.get("flow_1m_positive", True)),
            ]
        seg += _flow_dual_kpi(
            market_row=[
                ("ETPs", str(kpis.get("count", 0))),
                ("AUM", kpis.get("total_aum", "--")),
                ("1W Flow", kpis.get("flow_1w", "--"),
                 kpis.get("flow_1w_positive", True)),
                ("1M Flow", kpis.get("flow_1m", "--"),
                 kpis.get("flow_1m_positive", True)),
            ],
            rex_row=rex_row,
        )

        # Flow-specific metrics (capture rate + flow rank)
        seg += _flow_metrics_row(kpis, rex_s, issuers)

        # Market share bar (REX highlighted in green via is_rex flag)
        if issuers:
            seg += _flow_share_bar(issuers, n=6)

        # Issuer comparison table (REX rows highlighted)
        if issuers:
            headers = ["Issuer", "ETPs", "AUM", "1W Flow", "1M Flow", "Share"]
            aligns = ["left", "right", "right", "right", "right", "right"]
            widths = ["140px", "50px", "80px", "80px", "80px", "55px"]
            iss_rows = []
            rex_idxs = set()
            for ri, iss in enumerate(issuers):
                if iss.get("is_rex", False):
                    rex_idxs.add(ri)
                iss_rows.append([
                    _esc(iss["issuer"][:28]), str(iss["count"]), iss["aum_fmt"],
                    iss["flow_1w_fmt"], iss["flow_1m_fmt"],
                    f'{iss["market_share"]:.1f}%',
                ])
            seg += _table(headers, iss_rows, aligns, highlight_col=3,
                          rex_rows=rex_idxs, col_widths=widths)

        # Flow bars (top 10 inflows / bottom 10 outflows)
        top10 = suite.get("top10", [])[:10]
        bot10 = suite.get("bottom10", [])[:10]
        if top10 or bot10:
            seg += _flow_bars(top10, bot10, n=10)

        body += _segment_block(seg, suite_color)

    html = _wrap_email(title, _NAVY, body, dashboard_url, date_str)
    return html, []


def build_autocall_email(dashboard_url: str = "", db=None) -> tuple[str, list]:
    """Build REX Autocallable ETF Report — standalone extract from flow report.

    Shows the Autocallable category: KPIs, issuer comparison, market share,
    flow bars. Designed for weekly distribution to ATCL partners.
    External report — no internal dashboard links.
    """
    dashboard_url = ""  # External report — never link to internal site
    title = "Autocallable ETF Weekly Update"
    from webapp.services.report_data import get_flow_report
    data = get_flow_report(db)

    if data.get("available") and "grand_kpis" not in data:
        data = get_flow_report(None)

    date_str = _data_date_str(data)

    if not data.get("available"):
        return _wrap_email(title, _NAVY,
                           '<tr><td style="padding:20px 30px;">Report data not available.</td></tr>',
                           dashboard_url, date_str), []

    suites = data.get("suites", [])
    auto_suite = None
    for s in suites:
        if "autocall" in s.get("label", "").lower():
            auto_suite = s
            break

    if not auto_suite:
        return _wrap_email(title, _NAVY,
                           '<tr><td style="padding:20px 30px;">No autocallable data found.</td></tr>',
                           dashboard_url, date_str), []

    # Compute flow date ranges for labels (1-day lag from pull date)
    from dateutil.relativedelta import relativedelta as _rl
    _pull = datetime.now()
    _lag = _pull - timedelta(days=1)
    _d = _lag
    _td_count = 0
    while _td_count < 5:
        _d -= timedelta(days=1)
        if _d.weekday() < 5:
            _td_count += 1
    _1w_start = _d + timedelta(days=1)
    while _1w_start.weekday() >= 5:
        _1w_start += timedelta(days=1)
    _1m_start = _lag - _rl(months=1)
    _1w_label = f"{_1w_start.month}/{_1w_start.day}-{_lag.month}/{_lag.day}"
    _1m_label = f"{_1m_start.month}/{_1m_start.day}-{_lag.month}/{_lag.day}"

    kpis = auto_suite.get("kpis", {})
    rex_s = auto_suite.get("rex_kpis", {})
    issuers = auto_suite.get("issuers", [])
    suite_color = _SUITE_COLORS.get("Autocallable", "#F3DFC1")

    body = ""

    # Highlights
    bullets = []
    aum_str = kpis.get("total_aum", "--")
    flow_1w = kpis.get("flow_1w", "--")
    count = kpis.get("count", 0)
    bullets.append(f"Autocallable ETF landscape: {aum_str} AUM across {count} products ({flow_1w} 1W flow)")
    if rex_s.get("count", 0) > 0:
        bullets.append(f"REX ATCL: {rex_s.get('total_aum', '--')} AUM, {rex_s.get('flow_1w', '--')} 1W flow, {rex_s.get('market_share', '--')} market share")
    if issuers:
        leader = issuers[0]
        bullets.append(f"Landscape leader: {leader.get('issuer', '?')} ({leader.get('aum_fmt', '--')} AUM, {leader.get('flow_1w_fmt', '--')} 1W flow)")
    body += _key_highlights_box(bullets)

    # Compute REX ranks
    _share_rank = _flow_1w_rank = _flow_1m_rank = None
    if issuers and rex_s.get("count", 0) > 0:
        for i, iss in enumerate(sorted(issuers, key=lambda x: x.get("market_share", 0), reverse=True)):
            if iss.get("is_rex", False):
                _share_rank = i + 1
                break
        for i, iss in enumerate(sorted(issuers, key=lambda x: x.get("flow_1w", 0), reverse=True)):
            if iss.get("is_rex", False):
                _flow_1w_rank = i + 1
                break
        for i, iss in enumerate(sorted(issuers, key=lambda x: x.get("flow_1m", 0) if x.get("flow_1m") is not None else x.get("flow_ytd", 0), reverse=True)):
            if iss.get("is_rex", False):
                _flow_1m_rank = i + 1
                break

    # Landscape section title
    body += _section_title("Autocallable ETF Landscape", suite_color)

    # Load prior week's ranks for delta comparison
    _prior_ranks = {}
    try:
        import json as _json
        from pathlib import Path as _Path
        from datetime import timedelta as _td
        _rank_dir = _Path("data/DASHBOARD/exports/autocall_ranks")
        if _rank_dir.exists():
            _files = sorted(_rank_dir.glob("*.json"), reverse=True)
            # Find the most recent file that's at least 5 days old (last week)
            for _f in _files:
                _fdate = _f.stem  # YYYY-MM-DD
                try:
                    from datetime import date as _dclass
                    _fd = _dclass.fromisoformat(_fdate)
                    if (datetime.now().date() - _fd).days >= 5:
                        _prior = _json.loads(_f.read_text())
                        _prior_ranks = _prior.get("issuers", {})
                        break
                except Exception:
                    continue
    except Exception:
        pass

    def _rank_pill(current: int, prior_key: str) -> str:
        """Render Option C colored pill showing rank change."""
        prior = _prior_ranks.get("REX", {}).get(prior_key)
        if prior is None:
            return ""
        delta = prior - current  # positive = improved (went from #5 to #3 = +2)
        if delta > 0:
            return (f'<div style="display:inline-block;margin-top:4px;padding:1px 8px;border-radius:10px;'
                    f'font-size:9px;font-weight:700;background:#ecfdf5;color:{_GREEN};">&#x25B2; {delta}</div>')
        elif delta < 0:
            return (f'<div style="display:inline-block;margin-top:4px;padding:1px 8px;border-radius:10px;'
                    f'font-size:9px;font-weight:700;background:#fef2f2;color:{_RED};">&#x25BC; {abs(delta)}</div>')
        else:
            return (f'<div style="display:inline-block;margin-top:4px;padding:1px 8px;border-radius:10px;'
                    f'font-size:9px;font-weight:700;background:#f3f4f6;color:{_GRAY};">&#x2014;</div>')

    # REX positioning: Share Rank, 1W Flow Rank, 1M Flow Rank
    if rex_s.get("count", 0) > 0:
        _metrics = []
        if _share_rank is not None:
            sc = _GREEN if _share_rank <= 3 else _NAVY
            _metrics.append(
                f'<td style="padding:6px 10px;text-align:center;">'
                f'<div style="font-size:18px;font-weight:700;color:{sc};">#{_share_rank}</div>'
                f'{_rank_pill(_share_rank, "share_rank")}'
                f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;margin-top:3px;">Share Rank</div></td>'
            )
        if _flow_1w_rank is not None:
            fc = _GREEN if _flow_1w_rank <= 3 else _NAVY
            _metrics.append(
                f'<td style="padding:6px 10px;text-align:center;">'
                f'<div style="font-size:18px;font-weight:700;color:{fc};">#{_flow_1w_rank}</div>'
                f'{_rank_pill(_flow_1w_rank, "flow_1w_rank")}'
                f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;margin-top:3px;">1W Flow Rank<br>({_1w_label})</div></td>'
            )
        if _flow_1m_rank is not None:
            mc = _GREEN if _flow_1m_rank <= 3 else _NAVY
            _metrics.append(
                f'<td style="padding:6px 10px;text-align:center;">'
                f'<div style="font-size:18px;font-weight:700;color:{mc};">#{_flow_1m_rank}</div>'
                f'{_rank_pill(_flow_1m_rank, "flow_1m_rank")}'
                f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;margin-top:3px;">1M Flow Rank<br>({_1m_label})</div></td>'
            )
        if _metrics:
            body += (
                f'<tr><td style="padding:8px 30px;">'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'<tr>{"".join(_metrics)}</tr></table></td></tr>'
            )
    rex_row = None
    if rex_s.get("count", 0) > 0:
        rex_row = [
            ("REX Funds", str(rex_s.get("count", 0))),
            ("REX AUM", rex_s.get("total_aum", "--")),
            (f"REX 1W Flow\n({_1w_label})", rex_s.get("flow_1w", "--"),
             rex_s.get("flow_1w_positive", True)),
            ("Market Share", rex_s.get("market_share", "--")),
        ]
    body += _flow_dual_kpi(
        market_row=[
            ("ETPs", str(kpis.get("count", 0))),
            ("AUM", kpis.get("total_aum", "--")),
            (f"1W Flow\n({_1w_label})", kpis.get("flow_1w", "--"),
             kpis.get("flow_1w_positive", True)),
            (f"1M Flow\n({_1m_label})", kpis.get("flow_1m", "--"),
             kpis.get("flow_1m_positive", True)),
        ],
        rex_row=rex_row,
    )

    # Market share bar
    if issuers:
        body += _flow_share_bar(issuers, n=6)

    # Issuer comparison table
    if issuers:
        headers = ["Issuer", "ETPs", "AUM", f"1W Flow<br><span style='font-weight:400;'>({_1w_label})</span>", f"1M Flow<br><span style='font-weight:400;'>({_1m_label})</span>", "Share"]
        aligns = ["left", "right", "right", "right", "right", "right"]
        widths = ["140px", "50px", "80px", "80px", "80px", "55px"]
        iss_rows = []
        rex_idxs = set()
        for ri, iss in enumerate(issuers):
            if iss.get("is_rex", False):
                rex_idxs.add(ri)
            iss_rows.append([
                _esc(iss["issuer"][:28]), str(iss["count"]), iss["aum_fmt"],
                iss["flow_1w_fmt"], iss["flow_1m_fmt"],
                f'{iss["market_share"]:.1f}%',
            ])
        body += _table(headers, iss_rows, aligns, highlight_col=3,
                        rex_rows=rex_idxs, col_widths=widths)

    # Flow bars
    top10 = auto_suite.get("top10", [])[:10]
    bot10 = auto_suite.get("bottom10", [])[:10]
    if top10 or bot10:
        body += _flow_bars(top10, bot10, n=10)

    # Market share line chart — ON HOLD
    # To re-enable: see git commit e2b5ae1 for the full chart code.
    # Shows category AUM since CAIE launch (Jun 2025) + REX share from ATCL (Feb 2026).
    # Y1 axis capped at 5%. Uses QuickChart.io for email-safe rendering.

    # Bloomberg methodology note — compute dynamic example dates
    from dateutil.relativedelta import relativedelta
    _pull = datetime.now()
    _lag = _pull - timedelta(days=1)
    # Walk back to find 5 trading days before lag
    _d = _lag
    _tdays = 0
    while _tdays < 5:
        _d -= timedelta(days=1)
        if _d.weekday() < 5:
            _tdays += 1
    _1w_start = _d + timedelta(days=1)
    while _1w_start.weekday() >= 5:
        _1w_start += timedelta(days=1)
    _1m_start = _lag - relativedelta(months=1)

    _meth_pull_day = f"{_pull.strftime('%a')} {_pull.month}/{_pull.day}"
    _meth_lag_day = f"{_lag.strftime('%a')} {_lag.month}/{_lag.day}"
    _meth_1w_start = f"{_1w_start.strftime('%a')} {_1w_start.month}/{_1w_start.day}"
    _meth_1m_start = f"{_1m_start.month}/{_1m_start.day}"

    body += (
        f'<tr><td style="padding:16px 30px 12px;">'
        f'<div style="font-size:10px;color:{_GRAY};line-height:1.4;border-top:1px solid {_BORDER};padding-top:10px;">'
        f'<b>Methodology:</b> '
        f'Fund flows = [Shares Outstanding(t) - Shares Outstanding(t-1)] x NAV(t). '
        f'All US ETP flows carry a <b>one-day lag</b> (issuers report shares outstanding with a one-day delay). '
        f'1W sums daily flows over 5 trading days ending one day prior to the pull date '
        f'(e.g., pulled {_meth_pull_day} reflects {_meth_1w_start} through {_meth_lag_day}). '
        f'1M looks back one calendar month from one day prior to pull date '
        f'(e.g., pulled {_meth_pull_day} reflects {_meth_1m_start} through {_meth_lag_day}). '
        f'Values in USD millions. Source: Bloomberg.'
        f'</div></td></tr>'
    )

    html = _wrap_email(title, _NAVY, body, dashboard_url, date_str)
    # Strip footer — this is an external report
    html = html.replace(
        f'<!-- Footer -->\n<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};text-align:center;">\n'
        f'  <div style="font-size:11px;color:{_GRAY};">\n'
        f'    REX Financial Intelligence Hub &middot; Data sourced from Bloomberg L.P. and REX Shares, LLC\n'
        f'  </div>\n'
        f'  <div style="font-size:10px;color:{_GRAY};margin-top:4px;font-style:italic;">\n'
        f'    Note: ETN data reflects proprietary share/price data where available. Bloomberg-reported ETN figures may differ.\n'
        f'  </div>\n'
        f'</td></tr>', '')
    return html, []


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
