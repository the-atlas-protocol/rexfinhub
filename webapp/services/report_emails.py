"""
Email-safe HTML builders for Bloomberg weekly reports (L&I, CC, SS).

These produce table-based, inline-styled HTML suitable for Outlook/Gmail.
Charts cannot be included in email - only tables and KPIs.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

# Email-safe colors
_NAVY = "#1a1a2e"
_TEAL = "#00897B"
_GREEN = "#27ae60"
_RED = "#e74c3c"
_BLUE = "#0984e3"
_GRAY = "#636e72"
_LIGHT = "#f8f9fa"
_BORDER = "#dee2e6"
_WHITE = "#ffffff"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _flow_color(val: float) -> str:
    return _GREEN if val >= 0 else _RED


def _wrap_email(title: str, accent: str, body: str, dashboard_url: str = "") -> str:
    """Wrap report body in email-safe HTML envelope."""
    today = datetime.now().strftime("%B %d, %Y")
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
<table width="640" cellpadding="0" cellspacing="0" border="0"
  style="background:{_WHITE};border-radius:12px;overflow:hidden;
  box-shadow:0 2px 8px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="background:{accent};padding:24px 30px;">
  <div style="font-size:22px;font-weight:700;color:{_WHITE};letter-spacing:-0.5px;">{_esc(title)}</div>
  <div style="font-size:12px;color:rgba(255,255,255,0.8);margin-top:4px;">{today}</div>
</td></tr>

{body}
{cta}

<!-- Footer -->
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};text-align:center;">
  <div style="font-size:11px;color:{_GRAY};">
    REX Financial Intelligence Hub &middot; Data sourced from Bloomberg
  </div>
</td></tr>

</table></td></tr></table></body></html>"""


def _kpi_row(kpis: list[tuple[str, str, str]]) -> str:
    """Render KPI row. Each kpi = (label, value, color)."""
    n = len(kpis)
    width = int(100 / n) if n else 25
    cells = []
    for label, value, color in kpis:
        cells.append(
            f'<td width="{width}%" style="padding:12px 6px;background:{_LIGHT};'
            f'border-radius:8px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{color};">{_esc(value)}</div>'
            f'<div style="font-size:9px;color:{_GRAY};text-transform:uppercase;'
            f'letter-spacing:0.5px;margin-top:2px;">{_esc(label)}</div></td>'
        )
    return (
        '<tr><td style="padding:15px 30px 5px;">'
        '<table width="100%" cellpadding="0" cellspacing="6" border="0">'
        f'<tr>{"".join(cells)}</tr>'
        '</table></td></tr>'
    )


def _table(headers: list[str], rows: list[list[str]], align: list[str] | None = None,
           highlight_col: int | None = None) -> str:
    """Render an email-safe table."""
    if not rows:
        return '<tr><td style="padding:10px 30px;color:#636e72;font-size:13px;">No data available.</td></tr>'

    n = len(headers)
    if align is None:
        align = ["left"] * n

    _th = f"padding:8px 10px;background:{_LIGHT};font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;font-weight:600;border-bottom:2px solid {_BORDER};"
    _td = f"padding:6px 10px;font-size:12px;color:{_NAVY};border-bottom:1px solid {_BORDER};"

    header_cells = "".join(
        f'<th style="{_th}text-align:{align[i]};">{_esc(h)}</th>'
        for i, h in enumerate(headers)
    )

    body_rows = []
    for row in rows:
        cells = []
        for i, val in enumerate(row):
            style = _td + f"text-align:{align[i]};"
            if highlight_col is not None and i == highlight_col:
                # Color positive/negative flow values
                try:
                    fval = float(val.replace("$", "").replace(",", "").replace("+", "").replace("B", "e3").replace("M", ""))
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


def _section_title(title: str, accent: str = _TEAL) -> str:
    return (
        f'<tr><td style="padding:15px 30px 5px;">'
        f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;'
        f'padding-bottom:6px;border-bottom:2px solid {accent};">{_esc(title)}</div>'
        f'</td></tr>'
    )


# ---------------------------------------------------------------------------
# L&I Report Email
# ---------------------------------------------------------------------------
def build_li_email(dashboard_url: str = "") -> str:
    """Build email-safe HTML for L&I report."""
    from webapp.services.report_data import get_li_report
    data = get_li_report()

    if not data.get("available"):
        return _wrap_email("U.S. Leveraged & Inverse ETP Report", _TEAL,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url)

    kpis = data["kpis"]
    body = ""

    # KPIs
    body += _kpi_row([
        ("Total ETPs", str(kpis["count"]), _NAVY),
        ("Total AUM", kpis["total_aum"], _NAVY),
        ("1W Net Flow", kpis["flow_1w"], _GREEN if kpis["flow_1w_positive"] else _RED),
        ("YTD Net Flow", kpis["flow_ytd"], _GREEN if kpis["flow_ytd_positive"] else _RED),
    ])

    # Provider Summary (top 15)
    body += _section_title("Provider Summary")
    headers = ["Provider", "# ETPs", "AUM", "1W Flow", "1M Flow", "YTD Flow", "Share"]
    aligns = ["left", "right", "right", "right", "right", "right", "right"]
    rows = []
    for p in data["providers"][:15]:
        rows.append([
            p["issuer"], str(p["count"]), p["aum_fmt"],
            p["flow_1w_fmt"], p["flow_1m_fmt"], p["flow_ytd_fmt"],
            f'{p["market_share"]:.1f}%',
        ])
    body += _table(headers, rows, aligns)

    # Top 10 Inflows
    body += _section_title("Top 10 Weekly Inflows", _GREEN)
    headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1W Flow"]
    aligns = ["left", "left", "left", "right", "right"]
    rows = []
    for f in data["top10"]:
        rows.append([f["ticker"], f["fund_name"][:35], f["issuer"][:25], f["aum_fmt"], f["flow_1w_fmt"]])
    body += _table(headers, rows, aligns)

    # Top 10 Outflows
    body += _section_title("Top 10 Weekly Outflows", _RED)
    rows = []
    for f in data["bottom10"]:
        rows.append([f["ticker"], f["fund_name"][:35], f["issuer"][:25], f["aum_fmt"], f["flow_1w_fmt"]])
    body += _table(headers, rows, aligns)

    return _wrap_email("U.S. Leveraged & Inverse ETP Report", _TEAL, body, dashboard_url)


# ---------------------------------------------------------------------------
# Covered Call Report Email
# ---------------------------------------------------------------------------
def build_cc_email(dashboard_url: str = "") -> str:
    """Build email-safe HTML for Covered Call report."""
    from webapp.services.report_data import get_cc_report
    data = get_cc_report()

    if not data.get("available"):
        return _wrap_email("Covered Call ETFs AUM and Flows", _TEAL,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url)

    kpis = data["kpis"]
    body = ""

    # KPIs
    body += _kpi_row([
        ("CC Funds", str(kpis["count"]), _NAVY),
        ("Total AUM", kpis["total_aum"], _NAVY),
        ("1W Net Flow", kpis["flow_1w"], _GREEN if kpis["flow_1w_positive"] else _RED),
        ("Avg Yield", kpis["avg_yield"], _TEAL),
    ])

    # REX CC Funds
    if data["rex_funds"]:
        body += _section_title("REX Covered Call Funds", _GREEN)
        headers = ["Ticker", "Fund Name", "AUM", "1W Flow", "1M Flow", "Yield"]
        aligns = ["left", "left", "right", "right", "right", "right"]
        rows = []
        for f in data["rex_funds"]:
            rows.append([f["ticker"], f["fund_name"][:35], f["aum_fmt"],
                         f["flow_1w_fmt"], f["flow_1m_fmt"], f["yield_fmt"]])
        body += _table(headers, rows, aligns)

    # Top 10 by 1M Flow (All segment)
    top_all = data.get("top_flow_segments", {}).get("All", [])
    if top_all:
        body += _section_title("Top 10 by 1-Month Flow")
        headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1M Flow"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in top_all[:10]:
            rows.append([f["ticker"], f["fund_name"][:30], f["issuer"][:25], f["aum_fmt"], f["flow_1m_fmt"]])
        body += _table(headers, rows, aligns)

    # Top 10 by Yield (All segment)
    top_yield = data.get("top_yield_segments", {}).get("All", [])
    if top_yield:
        body += _section_title("Top 10 by Yield", _BLUE)
        headers = ["Ticker", "Fund Name", "Issuer", "AUM", "Yield"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in top_yield[:10]:
            rows.append([f["ticker"], f["fund_name"][:30], f["issuer"][:25], f["aum_fmt"], f["yield_fmt"]])
        body += _table(headers, rows, aligns)

    # Issuer Ranking (top 15)
    if data["issuers"]:
        body += _section_title("Issuer Ranking")
        headers = ["Issuer", "# Funds", "AUM", "1W Flow", "1M Flow", "Share"]
        aligns = ["left", "right", "right", "right", "right", "right"]
        rows = []
        for iss in data["issuers"][:15]:
            rows.append([iss["issuer"][:30], str(iss["count"]), iss["aum_fmt"],
                         iss["flow_1w_fmt"], iss["flow_1m_fmt"], f'{iss["market_share"]:.1f}%'])
        body += _table(headers, rows, aligns)

    return _wrap_email("Covered Call ETFs AUM and Flows", _TEAL, body, dashboard_url)


# ---------------------------------------------------------------------------
# Single-Stock Report Email
# ---------------------------------------------------------------------------
def build_ss_email(dashboard_url: str = "") -> str:
    """Build email-safe HTML for Single-Stock report."""
    from webapp.services.report_data import get_ss_report, _get_cache, _fmt_currency
    data = get_ss_report()

    if not data.get("available"):
        return _wrap_email("Single-Stock Leveraged ETFs", _TEAL,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url)

    kpis = data["kpis"]
    body = ""

    # KPIs
    body += _kpi_row([
        ("SS ETFs", str(kpis["count"]), _NAVY),
        ("Total AUM", kpis["total_aum"], _NAVY),
        ("Issuers", str(kpis["issuers"]), _NAVY),
        ("Top Underlying", kpis["top_underlier"], _TEAL),
    ])

    # AUM by Issuer (table instead of pie)
    pie = data.get("aum_pie", {})
    if pie.get("labels"):
        body += _section_title("AUM by Issuer")
        headers = ["Issuer", "AUM ($M)"]
        aligns = ["left", "right"]
        rows = []
        total = sum(pie["values"])
        for label, val in zip(pie["labels"], pie["values"]):
            pct = (val / total * 100) if total > 0 else 0
            rows.append([label, f"${val:,.0f}M ({pct:.1f}%)"])
        body += _table(headers, rows, aligns)

    # Note about charts
    body += (
        '<tr><td style="padding:10px 30px;text-align:center;">'
        f'<div style="padding:10px;background:{_LIGHT};border-radius:6px;'
        f'font-size:12px;color:{_GRAY};">'
        'Time-series charts (flows, AUM, volume) available in the interactive report.'
        '</div></td></tr>'
    )

    return _wrap_email("Single-Stock Leveraged ETFs", _TEAL, body, dashboard_url)
