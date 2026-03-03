"""
Email-safe HTML builders for Bloomberg weekly reports (L&I, CC, SS).

These produce table-based, inline-styled HTML suitable for Outlook/Gmail.
Charts cannot be included in email - only tables and KPIs.
Data date comes from the report (not datetime.now()).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

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
_ORANGE = "#e67e22"
_REX_ROW_BG = "#e8f5e9"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _flow_color(val: float) -> str:
    return _GREEN if val >= 0 else _RED


def _is_valid_date(date_str: str) -> bool:
    """Check if a date string is valid and recent (after 2020)."""
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
    """Extract report data date. Falls back to yesterday."""
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
    """Extract short date (MM/DD/YYYY). Falls back to yesterday."""
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


def _wrap_email(title: str, accent: str, body: str,
                dashboard_url: str = "", date_str: str = "") -> str:
    """Wrap report body in email-safe HTML envelope."""
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
<table width="640" cellpadding="0" cellspacing="0" border="0"
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
           highlight_col: int | None = None, bold_last_row: bool = False,
           rex_rows: set[int] | None = None) -> str:
    """Render an email-safe table.

    Args:
        highlight_col: Color positive/negative values in this column.
        bold_last_row: Render the last row with bold font (for totals).
        rex_rows: Set of row indices to highlight with green background.
    """
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
    for ri, row in enumerate(rows):
        is_bold = bold_last_row and ri == len(rows) - 1
        is_rex = rex_rows and ri in rex_rows
        cells = []
        for i, val in enumerate(row):
            style = _td + f"text-align:{align[i]};"
            if is_bold:
                style += "font-weight:700;"
            if is_rex:
                style += f"background:{_REX_ROW_BG};"
            if highlight_col is not None and i == highlight_col:
                try:
                    fval = float(str(val).replace("$", "").replace(",", "").replace("+", "").replace("B", "e3").replace("M", ""))
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
def build_li_email(dashboard_url: str = "", db=None) -> str:
    """Build email-safe HTML for U.S. Leveraged & Inverse ETP Report.

    Matches the reference format with provider summary (leveraged/inverse split),
    top 10 inflows, and top 10 outflows.
    """
    from webapp.services.report_data import get_li_report
    data = get_li_report(db)

    date_str = _data_date_str(data)
    date_short = _data_date_short(data)
    title = f"U.S. Leveraged & Inverse ETF Report: {date_short}"

    if not data.get("available") or not data.get("kpis"):
        return _wrap_email(title, _TEAL,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url, date_str)

    kpis = data["kpis"]
    body = ""

    # KPIs
    kpi_items = [
        ("Total ETPs", str(kpis.get("count", 0)), _NAVY),
        ("Total AUM", kpis.get("total_aum", "$0"), _NAVY),
        ("1W Net Flow", kpis.get("flow_1w", "$0"), _GREEN if kpis.get("flow_1w_positive", True) else _RED),
        ("YTD Net Flow", kpis.get("flow_ytd", "$0"), _GREEN if kpis.get("flow_ytd_positive", True) else _RED),
    ]
    wow = kpis.get("aum_change_1w", "")
    if wow:
        kpi_items.insert(2, ("AUM WoW", wow, _GREEN if kpis.get("aum_change_positive", True) else _RED))
    body += _kpi_row(kpi_items)

    # Provider Summary with leveraged/inverse split
    body += _section_title("Provider Summary")
    providers = data.get("providers", [])
    total_row = data.get("total_row")
    has_split = total_row and "num_leveraged" in total_row

    if has_split:
        headers = ["Provider", "# Lev", "# Inv", "# Total",
                   "AUM Lev", "AUM Inv", "AUM Total",
                   "1W Flow", "Share"]
        aligns = ["left", "right", "right", "right",
                  "right", "right", "right", "right", "right"]
        rows = []
        rex_idxs = set()
        for p in providers[:20]:
            ri = len(rows)
            if p.get("is_rex"):
                rex_idxs.add(ri)
            rows.append([
                p["issuer"],
                str(p.get("num_leveraged", "")),
                str(p.get("num_inverse", "")),
                str(p["count"]),
                p.get("aum_leveraged_fmt", ""),
                p.get("aum_inverse_fmt", ""),
                p["aum_fmt"],
                p["flow_1w_fmt"],
                f'{p["market_share"]:.1f}%',
            ])
        if total_row:
            rows.append([
                "TOTAL",
                str(total_row.get("num_leveraged", "")),
                str(total_row.get("num_inverse", "")),
                str(total_row["count"]),
                total_row.get("aum_leveraged_fmt", ""),
                total_row.get("aum_inverse_fmt", ""),
                total_row["aum_fmt"],
                total_row["flow_1w_fmt"],
                "100.0%",
            ])
        body += _table(headers, rows, aligns, highlight_col=7,
                       bold_last_row=True, rex_rows=rex_idxs)
    else:
        # Fallback: no leveraged/inverse split
        headers = ["Provider", "# ETPs", "AUM", "1W Flow", "1M Flow", "YTD Flow", "Share"]
        aligns = ["left", "right", "right", "right", "right", "right", "right"]
        rows = []
        rex_idxs = set()
        for p in providers[:20]:
            ri = len(rows)
            if p.get("is_rex"):
                rex_idxs.add(ri)
            rows.append([
                p["issuer"], str(p["count"]), p["aum_fmt"],
                p["flow_1w_fmt"], p["flow_1m_fmt"], p["flow_ytd_fmt"],
                f'{p["market_share"]:.1f}%',
            ])
        if total_row:
            rows.append([
                "TOTAL", str(total_row["count"]), total_row["aum_fmt"],
                total_row["flow_1w_fmt"], total_row["flow_1m_fmt"], total_row["flow_ytd_fmt"],
                "100.0%",
            ])
        body += _table(headers, rows, aligns, highlight_col=3,
                       bold_last_row=True, rex_rows=rex_idxs)

    # Top 10 Weekly Inflows
    body += _section_title("Top 10 Weekly Inflows", _GREEN)
    headers = ["Ticker", "Fund Name", "Type", "Leverage", "AUM", "1W Flow"]
    aligns = ["left", "left", "left", "right", "right", "right"]
    rows = []
    for f in data.get("top10", []):
        rows.append([
            f["ticker"],
            f["fund_name"][:35],
            f.get("product_type", ""),
            f.get("leverage_factor", ""),
            f["aum_fmt"],
            f["flow_1w_fmt"],
        ])
    body += _table(headers, rows, aligns, highlight_col=5)

    # Top 10 Weekly Outflows
    body += _section_title("Top 10 Weekly Outflows", _RED)
    rows = []
    for f in data.get("bottom10", []):
        rows.append([
            f["ticker"],
            f["fund_name"][:35],
            f.get("product_type", ""),
            f.get("leverage_factor", ""),
            f["aum_fmt"],
            f["flow_1w_fmt"],
        ])
    body += _table(headers, rows, aligns, highlight_col=5)

    return _wrap_email(title, _TEAL, body, dashboard_url, date_str)


# ---------------------------------------------------------------------------
# Covered Call Report Email
# ---------------------------------------------------------------------------
def build_cc_email(dashboard_url: str = "", db=None) -> str:
    """Build email-safe HTML for Covered Call ETFs report.

    Matches the reference format with REX fund summary header,
    segment-level top 10s, and issuer rankings.
    """
    from webapp.services.report_data import get_cc_report
    data = get_cc_report(db)

    date_str = _data_date_str(data)
    date_short = _data_date_short(data)
    title = f"Covered Call ETF Report: {date_short}"

    if not data.get("available") or not data.get("kpis"):
        return _wrap_email(title, _BLUE,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url, date_str)

    kpis = data["kpis"]
    body = ""

    # KPIs
    kpi_items = [
        ("CC Funds", str(kpis.get("count", 0)), _NAVY),
        ("Total AUM", kpis.get("total_aum", "$0"), _NAVY),
        ("1W Net Flow", kpis.get("flow_1w", "$0"), _GREEN if kpis.get("flow_1w_positive", True) else _RED),
        ("Avg Yield", kpis.get("avg_yield", "0.0%"), _TEAL),
    ]
    wow = kpis.get("aum_change_1w", "")
    if wow:
        kpi_items.insert(2, ("AUM WoW", wow, _GREEN if kpis.get("aum_change_positive", True) else _RED))
    body += _kpi_row(kpi_items)

    # REX CC Fund Summary (horizontal card-style row for each REX fund)
    rex_funds = data.get("rex_funds", [])
    if rex_funds:
        body += _section_title("REX Covered Call Summary", _GREEN)
        headers = ["Metric"]
        # Sort by AUM descending, take up to 12 funds
        sorted_rex = sorted(rex_funds, key=lambda f: f.get("aum", 0), reverse=True)[:12]
        headers += [f["ticker"] for f in sorted_rex]
        aligns = ["left"] + ["right"] * len(sorted_rex)
        # AUM row
        aum_row = ["AUM"] + [f["aum_fmt"] for f in sorted_rex]
        # 1W Flow row
        flow_1w_row = ["1W Flow"] + [f["flow_1w_fmt"] for f in sorted_rex]
        # 1M Flow row
        flow_1m_row = ["1M Flow"] + [f["flow_1m_fmt"] for f in sorted_rex]
        # Yield row
        yield_row = ["Yield"] + [f["yield_fmt"] for f in sorted_rex]
        body += _table(headers, [aum_row, flow_1w_row, flow_1m_row, yield_row], aligns)

    # Segment Summary (AUM by cc_category)
    aum_by_cat = data.get("aum_by_category", [])
    if aum_by_cat:
        body += _section_title("AUM by Category")
        headers = ["Category", "# Funds", "AUM", "1W Flow", "1M Flow", "Share"]
        aligns = ["left", "right", "right", "right", "right", "right"]
        rows = []
        for c in aum_by_cat:
            rows.append([
                c["category"], str(c["count"]), c["aum_fmt"],
                c["flow_1w_fmt"], c["flow_1m_fmt"],
                f'{c["market_share"]:.1f}%',
            ])
        body += _table(headers, rows, aligns, highlight_col=3)

    # Top 10 by 1M Inflows (segmented)
    top_flow = data.get("top_flow_segments", {})
    for seg_name in ["All", "Traditional", "Synthetic", "Single Stock"]:
        seg_data = top_flow.get(seg_name, [])
        if not seg_data:
            continue
        label = f"Top 10 by 1M Inflows" if seg_name == "All" else f"Top 10 by 1M Inflows ({seg_name})"
        body += _section_title(label)
        headers = ["Ticker", "Fund Name", "Issuer", "AUM", "1M Flow"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in seg_data[:10]:
            rows.append([f["ticker"], f["fund_name"][:30], f["issuer"][:25],
                         f["aum_fmt"], f["flow_1m_fmt"]])
        body += _table(headers, rows, aligns, highlight_col=4)

    # Top 10 by Distribution Rate (segmented)
    top_yield = data.get("top_yield_segments", {})
    for seg_name in ["All", "Traditional", "Synthetic", "Single Stock"]:
        seg_data = top_yield.get(seg_name, [])
        if not seg_data:
            continue
        label = f"Top 10 by Distribution Rate" if seg_name == "All" else f"Top 10 by Distribution Rate ({seg_name})"
        body += _section_title(label, _ORANGE)
        headers = ["Ticker", "Fund Name", "Issuer", "AUM", "Yield"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in seg_data[:10]:
            rows.append([f["ticker"], f["fund_name"][:30], f["issuer"][:25],
                         f["aum_fmt"], f["yield_fmt"]])
        body += _table(headers, rows, aligns)

    # Issuer Ranking
    issuers = data.get("issuers", [])
    if issuers:
        body += _section_title("Issuer Ranking")
        headers = ["Rank", "Issuer", "AUM", "1W Flow", "Share"]
        aligns = ["right", "left", "right", "right", "right"]
        rows = []
        rex_idxs = set()
        for i, iss in enumerate(issuers[:25]):
            if iss.get("market_share", 0) < 0.05 and i > 15:
                break
            ri = len(rows)
            if "REX" in iss["issuer"].upper() or "rex" in iss["issuer"].lower():
                rex_idxs.add(ri)
            rows.append([
                str(i + 1),
                iss["issuer"][:30],
                iss["aum_fmt"],
                iss["flow_1w_fmt"],
                f'{iss["market_share"]:.1f}%',
            ])
        body += _table(headers, rows, aligns, highlight_col=3, rex_rows=rex_idxs)

    return _wrap_email(title, _BLUE, body, dashboard_url, date_str)


# ---------------------------------------------------------------------------
# Single-Stock Report Email
# ---------------------------------------------------------------------------
def build_ss_email(dashboard_url: str = "", db=None) -> str:
    """Build email-safe HTML for Single-Stock ETF Report.

    Covers both Single-Stock Leveraged and Single-Stock Covered Call ETFs.
    """
    from webapp.services.report_data import get_ss_report, _fmt_currency
    data = get_ss_report(db)

    date_str = _data_date_str(data)
    date_short = _data_date_short(data)
    title = f"Single-Stock ETF Report: {date_short}"

    if not data.get("available") or not data.get("kpis"):
        return _wrap_email(title, _NAVY,
                           '<tr><td style="padding:20px 30px;">Bloomberg data not available.</td></tr>',
                           dashboard_url, date_str)

    kpis = data["kpis"]
    body = ""

    # KPIs — show combined + segment breakdown
    kpi_items = [
        ("SS ETFs", str(kpis.get("count", 0)), _NAVY),
        ("Total AUM", kpis.get("total_aum", "$0"), _NAVY),
        ("Leveraged", f'{kpis.get("num_leveraged", 0)} / {kpis.get("aum_leveraged", "$0")}', _TEAL),
        ("Covered Call", f'{kpis.get("num_cc", 0)} / {kpis.get("aum_cc", "$0")}', _BLUE),
    ]
    wow = kpis.get("aum_change_1w", "")
    if wow:
        kpi_items.insert(2, ("AUM WoW", wow, _GREEN if kpis.get("aum_change_positive", True) else _RED))
    body += _kpi_row(kpi_items)

    # Provider Summary
    providers = data.get("providers", [])
    if providers:
        body += _section_title("Provider Summary")
        headers = ["Provider", "# ETFs", "AUM", "1W Flow", "1M Flow", "Share"]
        aligns = ["left", "right", "right", "right", "right", "right"]
        rows = []
        rex_idxs = set()
        for p in providers[:15]:
            ri = len(rows)
            if p.get("is_rex"):
                rex_idxs.add(ri)
            rows.append([
                p["issuer"], str(p["count"]), p["aum_fmt"],
                p["flow_1w_fmt"], p["flow_1m_fmt"],
                f'{p["market_share"]:.1f}%',
            ])
        body += _table(headers, rows, aligns, highlight_col=3, rex_rows=rex_idxs)

    # AUM by Issuer
    pie = data.get("aum_pie", {})
    if pie.get("labels"):
        body += _section_title("AUM Breakdown by Issuer")
        headers = ["Issuer", "AUM", "Share"]
        aligns = ["left", "right", "right"]
        rows = []
        total = sum(pie["values"])
        rex_idxs = set()
        for i, (label, val) in enumerate(zip(pie["labels"], pie["values"])):
            pct = (val / total * 100) if total > 0 else 0
            if "REX" in label.upper() or "T-REX" in label.upper():
                rex_idxs.add(i)
            rows.append([label, _fmt_currency(val), f"{pct:.1f}%"])
        body += _table(headers, rows, aligns, rex_rows=rex_idxs)

    # Top 10 Weekly Inflows
    if data.get("top10"):
        body += _section_title("Top 10 Weekly Inflows", _GREEN)
        headers = ["Ticker", "Fund Name", "Type", "AUM", "1W Flow"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in data["top10"]:
            rows.append([f["ticker"], f["fund_name"][:35],
                         f.get("product_type", ""),
                         f["aum_fmt"], f["flow_1w_fmt"]])
        body += _table(headers, rows, aligns, highlight_col=4)

    # Top 10 Weekly Outflows
    if data.get("bottom10"):
        body += _section_title("Top 10 Weekly Outflows", _RED)
        headers = ["Ticker", "Fund Name", "Type", "AUM", "1W Flow"]
        aligns = ["left", "left", "left", "right", "right"]
        rows = []
        for f in data["bottom10"]:
            rows.append([f["ticker"], f["fund_name"][:35],
                         f.get("product_type", ""),
                         f["aum_fmt"], f["flow_1w_fmt"]])
        body += _table(headers, rows, aligns, highlight_col=4)

    # Note about charts
    body += (
        '<tr><td style="padding:10px 30px;text-align:center;">'
        f'<div style="padding:10px;background:{_LIGHT};border-radius:6px;'
        f'font-size:12px;color:{_GRAY};">'
        'Time-series charts (cumulative flows, AUM trends, notional volume) '
        'are available in the interactive report.'
        '</div></td></tr>'
    )

    return _wrap_email(title, _NAVY, body, dashboard_url, date_str)
