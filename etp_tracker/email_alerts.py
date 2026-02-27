"""
Email Alerts - Daily Brief

Email-client-compatible HTML digest (inline styles, table layout, no JS).
Works in Outlook, Gmail, Apple Mail, etc.

Sections:
  1. Header
  2. KPI Scorecard (Trusts, Effective Today, Pending)
  3. New Fund Launches (inception in last 24h, from Bloomberg master data)
  4. New Filings (485 forms filed in last 24h, REX trusts first)
  5. Upcoming Effectiveness (PENDING funds with expected effective dates)
  6. Dashboard CTA
  7. Footer
"""
from __future__ import annotations
import smtplib
import os
import html as html_mod
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

_REX_TRUSTS = {"REX ETF Trust", "ETF Opportunities Trust"}

# Email-safe colors (no CSS variables)
_NAVY = "#1a1a2e"
_GREEN = "#27ae60"
_ORANGE = "#e67e22"
_RED = "#e74c3c"
_BLUE = "#0984e3"
_GRAY = "#636e72"
_LIGHT = "#f8f9fa"
_BORDER = "#dee2e6"
_WHITE = "#ffffff"


def _load_recipients(project_root: Path | None = None) -> list[str]:
    if project_root is None:
        project_root = Path(__file__).parent.parent
    recipients_file = project_root / "config" / "email_recipients.txt"
    if recipients_file.exists():
        lines = recipients_file.read_text().strip().splitlines()
        return [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    env_to = os.environ.get("SMTP_TO", "")
    return [e.strip() for e in env_to.split(",") if e.strip()]


def _load_private_recipients(project_root: Path | None = None) -> list[str]:
    """Load private recipient list (sent separately, not visible to main list)."""
    if project_root is None:
        project_root = Path(__file__).parent.parent
    private_file = project_root / "config" / "email_recipients_private.txt"
    if private_file.exists():
        lines = private_file.read_text().strip().splitlines()
        return [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return []


def _get_smtp_config() -> dict:
    project_root = Path(__file__).parent.parent
    env_file = project_root / "config" / ".env"
    env_vars = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")
    return {
        "host": env_vars.get("SMTP_HOST", os.environ.get("SMTP_HOST", "smtp.gmail.com")),
        "port": int(env_vars.get("SMTP_PORT", os.environ.get("SMTP_PORT", "587"))),
        "user": env_vars.get("SMTP_USER", os.environ.get("SMTP_USER", "")),
        "password": env_vars.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", "")),
        "from_addr": env_vars.get("SMTP_FROM", os.environ.get("SMTP_FROM", "")),
        "to_addrs": _load_recipients(project_root),
    }


def _clean_ticker(val) -> str:
    s = str(val).strip() if val is not None else ""
    if s.upper() in ("NAN", "SYMBOL", "N/A", "NA", "NONE", "TBD", ""):
        return ""
    if len(s) < 2:
        return ""
    return s


def _days_since(date_str: str, today: datetime) -> str:
    try:
        dt = pd.to_datetime(date_str, errors="coerce")
        if pd.isna(dt):
            return ""
        delta = (today - dt).days
        return str(delta)
    except Exception:
        return ""


def _expected_effective(form: str, filing_date: str, eff_date: str) -> str:
    if eff_date and str(eff_date).strip() and str(eff_date) != "nan":
        return str(eff_date).strip()
    form_upper = str(form).upper()
    if form_upper.startswith("485A"):
        try:
            dt = pd.to_datetime(filing_date, errors="coerce")
            if not pd.isna(dt):
                return (dt + timedelta(days=75)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def _esc(val) -> str:
    return html_mod.escape(str(val)) if val is not None else ""


def _status_color(status: str) -> str:
    return {
        "EFFECTIVE": _GREEN,
        "PENDING": _ORANGE,
        "DELAYED": _RED,
    }.get(status.upper(), _GRAY)


def _status_badge(status: str) -> str:
    color = _status_color(status)
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
        f'font-size:12px;font-weight:600;color:{_WHITE};background:{color};">'
        f'{_esc(status)}</span>'
    )


def _rex_badge() -> str:
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:12px;'
        f'font-size:11px;font-weight:600;color:{_WHITE};background:{_BLUE};'
        f'margin-left:6px;">REX</span>'
    )


import re as _re

_FUND_PATTERNS = {
    "leveraged": _re.compile(
        r"2X|3X|4X|LEVERAG|BULL|BEAR|INVERSE|T-REX|DAILY TARGET",
        _re.IGNORECASE,
    ),
    "income": _re.compile(
        r"INCOME|YIELD|DIVIDEND|COVERED.?CALL|OPTION.?INCOME|AUTOCALL|PREMIUM",
        _re.IGNORECASE,
    ),
    "crypto": _re.compile(
        r"BTC|BITCOIN|ETHER|ETH(?:EREUM)?|CRYPTO|BONK|TRUMP|SOLANA|DOGE|XRP",
        _re.IGNORECASE,
    ),
}


def _classify_fund(series_name: str) -> str:
    """Classify a fund by relevance to REX's business."""
    if not series_name:
        return "other"
    for category, pattern in _FUND_PATTERNS.items():
        if pattern.search(series_name):
            return category
    return "other"


def _gather_market_snapshot() -> dict | None:
    """Pull Bloomberg data for the daily brief market sections.

    Returns None if Bloomberg data is unavailable (graceful degradation).
    """
    try:
        from webapp.services.market_data import data_available, get_rex_summary, get_master_data, get_category_summary
        if not data_available():
            return None

        # REX KPIs (ETF only)
        rex = get_rex_summary(fund_structure="ETF")
        master = get_master_data()

        # Filter to ETFs
        ft_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
        if ft_col:
            master = master[master[ft_col] == "ETF"]

        # 1D flow (sum across all REX ETFs)
        rex_df = master[master["is_rex"] == True].copy()
        if "ticker_clean" in rex_df.columns:
            rex_df = rex_df.drop_duplicates(subset=["ticker_clean"], keep="first")
        flow_1d = float(rex_df["t_w4.fund_flow_1day"].sum()) if "t_w4.fund_flow_1day" in rex_df.columns else 0.0

        def _fmt_flow_val(val: float) -> str:
            sign = "+" if val >= 0 else "-"
            av = abs(val)
            if av >= 1000:
                return f"{sign}${av/1000:.1f}B"
            if av >= 1:
                return f"{sign}${av:.1f}M"
            return f"{sign}${av:.2f}M"

        kpis = {
            "aum": rex.get("total_aum_fmt", "$0"),
            "flow_1d": flow_1d,
            "flow_1d_fmt": _fmt_flow_val(flow_1d),
            "flow_1d_positive": flow_1d >= 0,
            "flow_1w": rex.get("flow_1w", 0),
            "flow_1w_fmt": rex.get("flow_1w_fmt", "$0"),
            "flow_1w_positive": rex.get("flow_1w_positive", True),
            "products": rex.get("num_products", 0),
        }

        # Top movers: top 5 inflows + top 3 outflows by 1W flow
        top_movers = {"inflows": [], "outflows": []}
        if not rex_df.empty and "t_w4.fund_flow_1week" in rex_df.columns:
            valid = rex_df[rex_df["t_w4.fund_flow_1week"].notna()].copy()
            for _, row in valid.nlargest(5, "t_w4.fund_flow_1week").iterrows():
                flow = float(row.get("t_w4.fund_flow_1week", 0))
                ret = float(row.get("t_w3.total_return_1week", 0)) if "t_w3.total_return_1week" in row.index else 0
                top_movers["inflows"].append({
                    "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                    "name": str(row.get("fund_name", ""))[:35],
                    "flow_1w": flow,
                    "flow_1w_fmt": _fmt_flow_val(flow),
                    "return_1w": ret,
                    "return_1w_fmt": f"{ret:+.2f}%",
                })
            for _, row in valid.nsmallest(3, "t_w4.fund_flow_1week").iterrows():
                flow = float(row.get("t_w4.fund_flow_1week", 0))
                if flow >= 0:
                    continue
                ret = float(row.get("t_w3.total_return_1week", 0)) if "t_w3.total_return_1week" in row.index else 0
                top_movers["outflows"].append({
                    "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                    "name": str(row.get("fund_name", ""))[:35],
                    "flow_1w": flow,
                    "flow_1w_fmt": _fmt_flow_val(flow),
                    "return_1w": ret,
                    "return_1w_fmt": f"{ret:+.2f}%",
                })

        # Landscape: 5 categories with AUM, 1W flow, REX market share
        _LANDSCAPE_CATS = [
            "Leverage & Inverse - Single Stock",
            "Leverage & Inverse - Index/Basket/ETF Based",
            "Income - Single Stock",
            "Income - Index/Basket/ETF Based",
            "Crypto",
        ]
        landscape = []
        for cat in _LANDSCAPE_CATS:
            try:
                cat_data = get_category_summary(cat, fund_structure="ETF")
                cat_aum = cat_data.get("cat_kpis", {}).get("total_aum", 0)
                cat_flow_1w = cat_data.get("cat_kpis", {}).get("flow_1w", 0)
                rex_share = cat_data.get("market_share", 0)
                landscape.append({
                    "category": cat,
                    "aum": cat_aum,
                    "aum_fmt": f"${cat_aum/1000:.1f}B" if cat_aum >= 1000 else f"${cat_aum:.0f}M",
                    "flow_1w": cat_flow_1w,
                    "flow_1w_fmt": _fmt_flow_val(cat_flow_1w),
                    "flow_1w_positive": cat_flow_1w >= 0,
                    "rex_share": rex_share,
                    "rex_share_fmt": f"{rex_share:.1f}%",
                })
            except Exception:
                continue

        return {
            "kpis": kpis,
            "top_movers": top_movers,
            "landscape": landscape,
        }
    except Exception:
        return None


def _render_market_scorecard(snapshot: dict) -> str:
    """Render 4 KPI cards for the REX market snapshot (email-safe HTML)."""
    kpis = snapshot["kpis"]
    _cell = f"padding:12px 6px;background:{_LIGHT};border-radius:8px;text-align:center;"
    _val = f"font-size:20px;font-weight:700;color:{_NAVY};"
    _lbl = f"font-size:9px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;"

    flow_1d_color = _GREEN if kpis["flow_1d_positive"] else _RED
    flow_1w_color = _GREEN if kpis["flow_1w_positive"] else _RED

    return f"""
<tr><td style="padding:15px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_BLUE};">
    REX Market Snapshot
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="23%" style="{_cell}">
        <div style="{_val}">{_esc(kpis['aum'])}</div>
        <div style="{_lbl}">REX AUM</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}color:{flow_1d_color};">{_esc(kpis['flow_1d_fmt'])}</div>
        <div style="{_lbl}">1D Flow</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}color:{flow_1w_color};">{_esc(kpis['flow_1w_fmt'])}</div>
        <div style="{_lbl}">1W Flow</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}">{kpis['products']}</div>
        <div style="{_lbl}">Products</div>
      </td>
    </tr>
  </table>
</td></tr>"""


def _render_top_movers(movers: dict) -> str:
    """Render top inflows/outflows table (email-safe HTML)."""
    inflows = movers.get("inflows", [])
    outflows = movers.get("outflows", [])
    if not inflows and not outflows:
        return ""

    _col = (
        f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"border-bottom:1px solid {_BORDER};"
    )

    rows = []
    for f in inflows:
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;font-weight:600;">{_esc(f["ticker"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;">{_esc(f["name"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;color:{_GREEN};font-weight:600;">{_esc(f["flow_1w_fmt"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;">{_esc(f["return_1w_fmt"])}</td>'
            f'</tr>'
        )
    for f in outflows:
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;font-weight:600;">{_esc(f["ticker"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;">{_esc(f["name"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;color:{_RED};font-weight:600;">{_esc(f["flow_1w_fmt"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;">{_esc(f["return_1w_fmt"])}</td>'
            f'</tr>'
        )

    return f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Top Movers
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Ticker</td>
      <td style="{_col}">Fund Name</td>
      <td style="{_col}text-align:right;">1W Flow</td>
      <td style="{_col}text-align:right;">1W Return</td>
    </tr>
    {''.join(rows)}
  </table>
</td></tr>"""


def _render_landscape_compact(landscape: list) -> str:
    """Render 5-row market landscape table (email-safe HTML)."""
    if not landscape:
        return ""

    _col = (
        f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"border-bottom:1px solid {_BORDER};"
    )

    # Short display names for categories
    _SHORT = {
        "Leverage & Inverse - Single Stock": "L&I Single Stock",
        "Leverage & Inverse - Index/Basket/ETF Based": "L&I Index/ETF",
        "Income - Single Stock": "Income Single Stock",
        "Income - Index/Basket/ETF Based": "Income Index/ETF",
        "Crypto": "Crypto",
    }

    rows = []
    for cat in landscape:
        name = _SHORT.get(cat["category"], cat["category"])
        flow_color = _GREEN if cat["flow_1w_positive"] else _RED
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;">{_esc(name)}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;">{_esc(cat["aum_fmt"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;color:{flow_color};">{_esc(cat["flow_1w_fmt"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;text-align:right;font-weight:600;">{_esc(cat["rex_share_fmt"])}</td>'
            f'</tr>'
        )

    return f"""
<tr><td style="padding:10px 30px 10px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Market Landscape
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Category</td>
      <td style="{_col}text-align:right;">AUM</td>
      <td style="{_col}text-align:right;">1W Flow</td>
      <td style="{_col}text-align:right;">REX Share</td>
    </tr>
    {''.join(rows)}
  </table>
</td></tr>"""


def _dashboard_cta(dash_link: str) -> str:
    return (
        f'<tr><td style="padding:20px 30px;" align="center">'
        f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="background:{_BLUE};border-radius:8px;padding:16px 40px;">'
        f'<a href="{_esc(dash_link)}" style="color:{_WHITE};text-decoration:none;font-size:16px;font-weight:700;">Open Dashboard</a>'
        f'</td></tr></table>'
        f'<div style="font-size:12px;color:{_GRAY};margin-top:8px;">View full details, filings, and AI analysis</div>'
        f'</td></tr>'
    )


def _render_daily_html(data: dict, dashboard_url: str = "", custom_message: str = "",
                       edition: str = "daily") -> str:
    """Render the daily brief HTML from pre-gathered data.

    edition: "daily" (5 PM brief), "morning" (legacy), or "evening" (legacy).
    """
    today = datetime.now()
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    _is_evening = edition == "evening"
    _titles = {"daily": "REX ETF Daily Brief", "evening": "REX ETF Evening Update",
               "morning": "REX ETF Morning Brief"}
    _title = _titles.get(edition, "REX ETF Daily Brief")
    _header_bg = "#2d3436" if _is_evening else _NAVY
    _accent = _ORANGE if _is_evening else _BLUE

    # --- Custom message ---
    msg_html = ""
    if custom_message:
        msg_html = (
            f'<tr><td style="padding:12px 30px 0;">'
            f'<div style="padding:10px 14px;background:#eef3f8;border-left:3px solid {_accent};'
            f'border-radius:4px;font-size:13px;color:{_NAVY};">'
            f'{_esc(custom_message)}</div>'
            f'</td></tr>'
        )

    # --- Header ---
    header = f"""
<tr><td style="background:{_header_bg};padding:24px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="color:{_WHITE};font-size:22px;font-weight:700;">{_title}</td>
    <td align="right" style="color:rgba(255,255,255,0.7);font-size:13px;">{today.strftime('%A, %B %d, %Y')}</td>
  </tr></table>
</td></tr>"""

    # --- KPI Scorecard (top of email) ---
    trust_count = data.get("trust_count", 0)
    newly_effective = data.get("newly_effective_1d", 0)
    total_pending = data.get("total_pending", 0)

    _kpi_cell = f"padding:12px 8px;background:{_LIGHT};border-radius:8px;text-align:center;"
    _kpi_val = f"font-size:24px;font-weight:700;color:{_NAVY};"
    _kpi_lbl = f"font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;"

    scorecard = f"""
<tr><td style="padding:20px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="31%" style="{_kpi_cell}">
        <div style="{_kpi_val}">{trust_count}</div>
        <div style="{_kpi_lbl}">Trusts Monitored</div>
      </td>
      <td width="3%"></td>
      <td width="31%" style="{_kpi_cell}">
        <div style="{_kpi_val}color:{_GREEN};">{newly_effective}</div>
        <div style="{_kpi_lbl}">Effective Today</div>
      </td>
      <td width="3%"></td>
      <td width="31%" style="{_kpi_cell}">
        <div style="{_kpi_val}color:{_ORANGE};">{total_pending}</div>
        <div style="{_kpi_lbl}">Pending</div>
      </td>
    </tr>
  </table>
</td></tr>"""

    # --- New Fund Launches ---
    launches = data.get("launches", [])
    if launches:
        launch_rows = []
        for f in launches[:15]:
            ticker = _esc(f.get("ticker", ""))
            name = _esc(f.get("fund_name", ""))
            if len(name) > 40:
                name = name[:37] + "..."
            issuer = _esc(f.get("trust_name", ""))
            if len(issuer) > 25:
                issuer = issuer[:22] + "..."
            eff_date = _esc(f.get("effective_date", ""))
            is_rex = f.get("is_rex", False)
            issuer_html = issuer
            if is_rex:
                issuer_html = (
                    f'{issuer} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            launch_rows.append(
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;font-weight:600;white-space:nowrap;">{ticker}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{name}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;color:{_GRAY};">{issuer_html}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;color:{_GRAY};">{eff_date}</td>'
                f'</tr>'
            )
        more_html = ""
        if len(launches) > 15:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {len(launches) - 15} more on dashboard</div>'
            )
        _col = (
            f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
            f"border-bottom:1px solid {_BORDER};"
        )
        launches_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_GREEN};">
    New Fund Launches
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Ticker</td>
      <td style="{_col}">Fund Name</td>
      <td style="{_col}">Issuer</td>
      <td style="{_col}text-align:right;">Launched</td>
    </tr>
    {''.join(launch_rows)}
  </table>
  {more_html}
</td></tr>"""
    else:
        launches_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_GREEN};">
    New Fund Launches
  </div>
  <div style="padding:12px;background:{_LIGHT};border-radius:6px;
    font-size:13px;color:{_GRAY};text-align:center;">
    No new launches today.
  </div>
</td></tr>"""

    # --- New Filings (relevance-sorted, fund-level detail) ---
    filing_groups = data.get("filing_groups", [])
    if filing_groups:
        _row_base = (
            f"padding:8px 10px;border-bottom:1px solid {_BORDER};"
            f"font-size:12px;color:{_NAVY};"
        )
        _row_rex = (
            f"padding:8px 10px;border-bottom:1px solid {_BORDER};"
            f"font-size:12px;color:{_NAVY};"
            f"background:#eef3f8;"
        )
        filing_items = []
        for fg in filing_groups[:8]:
            trust = _esc(fg.get("trust_name", ""))
            if len(trust) > 35:
                trust = trust[:32] + "..."
            form = _esc(fg.get("form", ""))
            is_rex = fg.get("is_rex", False)
            total = fg.get("total_funds", 0)
            relevant = fg.get("relevant_funds", [])
            overflow = fg.get("relevant_overflow", 0)
            other_count = fg.get("other_count", 0)
            cats = fg.get("categories", {})
            row_style = _row_rex if is_rex else _row_base

            # Build the trust label with optional REX badge
            trust_label = trust
            if is_rex:
                trust_label = (
                    f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 6px;border-radius:3px;font-size:9px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )

            # Build the summary line
            if is_rex or relevant:
                # Show fund names for REX / relevant trusts
                fund_names = [_esc(f) for f in relevant]
                summary_parts = []
                if fund_names:
                    summary_parts.append(", ".join(fund_names))
                if overflow > 0:
                    summary_parts.append(f"+{overflow} more relevant")
                if other_count > 0:
                    summary_parts.append(f"+{other_count} more")
                summary = "; ".join(summary_parts) if summary_parts else f"{total} funds filed"

                # Category tags
                cat_tags = ""
                for cat, cnt in sorted(cats.items(), key=lambda x: x[1], reverse=True):
                    cat_color = {"leveraged": "#e74c3c", "income": "#27ae60", "crypto": "#f39c12"}.get(cat, _GRAY)
                    cat_tags += (
                        f' <span style="display:inline-block;padding:1px 5px;border-radius:3px;'
                        f'font-size:9px;color:{_WHITE};background:{cat_color};'
                        f'margin-left:2px;">{cnt} {cat}</span>'
                    )

                filing_items.append(
                    f'<tr><td style="{row_style}">'
                    f'<div style="font-weight:600;margin-bottom:2px;">'
                    f'{trust_label} <span style="font-weight:400;color:{_GRAY};font-size:10px;">{form}</span>'
                    f'{cat_tags}</div>'
                    f'<div style="font-size:11px;color:#555;">{summary}</div>'
                    f'</td></tr>'
                )
            else:
                # Non-relevant trust — collapsed single line
                filing_items.append(
                    f'<tr><td style="{row_style}">'
                    f'{trust_label} '
                    f'<span style="color:{_GRAY};font-size:10px;">{form}</span> '
                    f'<span style="color:{_GRAY};font-size:11px;">'
                    f'&ndash; {total} funds (+{total} more)</span>'
                    f'</td></tr>'
                )

        more_html = ""
        if len(filing_groups) > 8:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {len(filing_groups) - 8} more trusts filed</div>'
            )
        filings_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_BLUE};">
    New Filings
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    {''.join(filing_items)}
  </table>
  {more_html}
</td></tr>"""
    else:
        filings_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_BLUE};">
    New Filings
  </div>
  <div style="padding:12px;background:{_LIGHT};border-radius:6px;
    font-size:13px;color:{_GRAY};text-align:center;">
    No new 485 filings today.
  </div>
</td></tr>"""

    # --- Upcoming Effectiveness ---
    pending = data.get("pending", [])
    pending_section = ""
    if pending:
        pending_rows = []
        for p in pending[:8]:
            name = _esc(p.get("fund_name", ""))
            if len(name) > 40:
                name = name[:37] + "..."
            trust = _esc(p.get("trust_name", ""))
            if len(trust) > 25:
                trust = trust[:22] + "..."
            eff_date = _esc(p.get("effective_date", ""))
            is_rex = p.get("is_rex", False)
            trust_html = trust
            if is_rex:
                trust_html = (
                    f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            pending_rows.append(
                f'<tr>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{name}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;color:{_GRAY};">{trust_html}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;color:{_ORANGE};font-weight:600;">{eff_date}</td>'
                f'</tr>'
            )
        more_html = ""
        total_p = data.get("total_pending", len(pending))
        if total_p > 8:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {total_p - 8} more on '
                f'{"<a href=\"" + dash_link + "/dashboard?status=PENDING\" style=\"color:" + _BLUE + ";\">dashboard</a>" if dash_link else "dashboard"}'
                f'</div>'
            )
        _col = (
            f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
            f"border-bottom:1px solid {_BORDER};"
        )
        pending_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_ORANGE};">
    Upcoming Effectiveness
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Fund Name</td>
      <td style="{_col}">Trust</td>
      <td style="{_col}text-align:right;">Expected Effectiveness</td>
    </tr>
    {''.join(pending_rows)}
  </table>
  {more_html}
</td></tr>"""

    # --- Market Snapshot (Bloomberg data — graceful skip if unavailable) ---
    market_section = ""
    snapshot = data.get("market_snapshot")
    if snapshot:
        market_section = (
            _render_market_scorecard(snapshot)
            + _render_top_movers(snapshot.get("top_movers", {}))
            + _render_landscape_compact(snapshot.get("landscape", []))
        )

    # --- Dashboard CTA ---
    cta_section = _dashboard_cta(dash_link) if dash_link else ""

    # --- Footer ---
    _data_source = "Data sourced from SEC EDGAR"
    if snapshot:
        _data_source += " &amp; Bloomberg"
    footer = f"""
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <div style="font-size:11px;color:{_GRAY};text-align:center;">
    {_title} | {today.strftime('%Y-%m-%d')}
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    {_data_source} | To unsubscribe, contact relasmar@rexfin.com
  </div>
</td></tr>"""

    # --- Assemble (KPIs at top) ---
    body = header + msg_html + scorecard + launches_section + filings_section + pending_section + market_section + cta_section + footer

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_title} - {today.strftime('%Y-%m-%d')}</title>
</head>
<body style="margin:0;padding:0;background:{_LIGHT};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="600" cellpadding="0" cellspacing="0" border="0"
       style="background:{_WHITE};border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);">
{body}
</table>
</td></tr></table>
</body></html>"""


def _gather_daily_data(db_session, since_date: str | None = None,
                       edition: str = "daily") -> dict:
    """Query DB + Bloomberg master data for daily brief.

    edition: "daily"/"morning" looks back 24h, "evening" looks at today only.
    """
    from sqlalchemy import func, select
    from datetime import date as date_type
    from webapp.models import Trust, FundStatus, Filing, FundExtraction

    today = datetime.now()
    if not since_date:
        if edition == "evening":
            since_date = today.strftime("%Y-%m-%d")  # today only
        else:  # "daily" or "morning"
            since_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")  # last 24h
    since_dt = date_type.fromisoformat(since_date)
    yesterday = date_type.today() - timedelta(days=1)

    # --- New launches: Bloomberg inception_date in last 24h ---
    launches = []
    try:
        from webapp.services.market_data import data_available, get_master_data
        if data_available():
            master = get_master_data()
            ft_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
            if ft_col:
                master = master[master[ft_col] == "ETF"]
            if "inception_date" in master.columns and "ticker_clean" in master.columns:
                master = master.drop_duplicates(subset=["ticker_clean"], keep="first")
                inception = pd.to_datetime(master["inception_date"], errors="coerce")
                cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
                recent = master[inception >= cutoff].copy()
                recent["_inception"] = inception[recent.index]
                recent = recent.sort_values("_inception", ascending=False)
                is_rex_col = "is_rex" if "is_rex" in recent.columns else None
                for _, row in recent.iterrows():
                    ticker = str(row.get("ticker_clean", ""))
                    name = str(row.get("fund_name", row.get("name", "")))
                    issuer = str(row.get("issuer_display", row.get("issuer", "")))
                    inc_date = row["_inception"].strftime("%Y-%m-%d") if pd.notna(row["_inception"]) else ""
                    is_rex = bool(row.get("is_rex", False)) if is_rex_col else False
                    launches.append({
                        "ticker": ticker if ticker else "--",
                        "fund_name": name,
                        "trust_name": issuer,
                        "effective_date": inc_date,
                        "is_rex": is_rex,
                    })
    except Exception:
        pass

    # Bloomberg-only: no DB fallback (SEC effective dates are not launch dates)

    # --- New filings: fund-level detail with relevance classification ---
    filing_rows = db_session.execute(
        select(
            Trust.name.label("trust_name"), Trust.is_rex,
            Filing.form, Filing.filing_date,
            FundExtraction.series_name,
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= since_dt)
        .where(Filing.form.ilike("485%"))
        .order_by(Trust.is_rex.desc(), Filing.filing_date.desc())
    ).all()

    # Group by trust/form/date, classify each fund
    from collections import defaultdict
    _fg_map: dict[tuple, dict] = {}
    for r in filing_rows:
        key = (r.trust_name or "", r.form or "", str(r.filing_date) if r.filing_date else "")
        if key not in _fg_map:
            _fg_map[key] = {
                "trust_name": key[0],
                "form": key[1],
                "filing_date": key[2],
                "is_rex": r.is_rex,
                "funds": [],
            }
        sname = (r.series_name or "").strip()
        if sname:
            _fg_map[key]["funds"].append(sname)

    filing_groups = []
    for g in _fg_map.values():
        funds = g["funds"]
        # Deduplicate fund names (same series can appear via multiple classes)
        seen = set()
        unique_funds = []
        for f in funds:
            fl = f.upper()
            if fl not in seen:
                seen.add(fl)
                unique_funds.append(f)

        categories: dict[str, list[str]] = defaultdict(list)
        for f in unique_funds:
            categories[_classify_fund(f)].append(f)

        relevant = categories.get("leveraged", []) + categories.get("income", []) + categories.get("crypto", [])
        other_count = len(categories.get("other", []))
        cat_counts = {c: len(names) for c, names in categories.items() if names and c != "other"}

        # Sort score: REX=1000, then count of relevant funds
        sort_score = (1000 if g["is_rex"] else 0) + len(relevant)

        filing_groups.append({
            "trust_name": g["trust_name"],
            "form": g["form"],
            "filing_date": g["filing_date"],
            "is_rex": g["is_rex"],
            "total_funds": len(unique_funds),
            "relevant_funds": relevant[:5],
            "relevant_overflow": max(0, len(relevant) - 5),
            "other_count": other_count,
            "categories": cat_counts,
            "_sort": sort_score,
        })

    filing_groups.sort(key=lambda x: x["_sort"], reverse=True)

    # --- Upcoming launches: PENDING with an actual expected date (no TBD) ---
    pending_rows = db_session.execute(
        select(
            FundStatus.fund_name, FundStatus.effective_date,
            Trust.name.label("trust_name"), Trust.is_rex,
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status == "PENDING")
        .where(FundStatus.effective_date.isnot(None))
        .order_by(Trust.is_rex.desc(), FundStatus.effective_date.asc())
    ).all()

    pending = []
    for r in pending_rows:
        pending.append({
            "fund_name": r.fund_name or "",
            "trust_name": r.trust_name or "",
            "effective_date": str(r.effective_date) if r.effective_date else "",
            "is_rex": r.is_rex,
        })

    # --- KPI counts ---
    trust_count = db_session.execute(
        select(func.count(Trust.id)).where(Trust.is_active == True)
    ).scalar() or 0

    newly_effective_1d = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "EFFECTIVE")
        .where(FundStatus.effective_date >= yesterday)
    ).scalar() or 0

    total_pending = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "PENDING")
    ).scalar() or 0

    # Market snapshot (Bloomberg data — None if unavailable)
    market_snapshot = _gather_market_snapshot()

    return {
        "launches": launches,
        "filing_groups": filing_groups,
        "pending": pending,
        "trust_count": trust_count,
        "newly_effective_1d": newly_effective_1d,
        "total_pending": total_pending,
        "market_snapshot": market_snapshot,
    }


def build_digest_html_from_db(
    db_session,
    dashboard_url: str = "",
    since_date: str | None = None,
    custom_message: str = "",
    edition: str = "daily",
) -> str:
    """Build daily brief from SQLite database."""
    data = _gather_daily_data(db_session, since_date, edition=edition)
    return _render_daily_html(data, dashboard_url, custom_message=custom_message,
                              edition=edition)


def _send_html_digest(html_body: str, recipients: list[str],
                      edition: str = "daily") -> bool:
    """Send pre-built HTML digest via Azure Graph or SMTP."""
    _labels = {"daily": "Daily Brief", "morning": "Morning Brief", "evening": "Evening Update"}
    _label = _labels.get(edition, "Daily Brief")
    subject = f"REX ETF {_label} - {datetime.now().strftime('%Y-%m-%d')}"

    # Try Azure Graph API first
    try:
        from webapp.services.graph_email import is_configured, send_email
        if is_configured():
            if send_email(subject=subject, html_body=html_body, recipients=recipients):
                return True
    except ImportError:
        pass

    # Fall back to SMTP
    config = _get_smtp_config()
    if not config["user"] or not config["password"] or not config["from_addr"]:
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
        return True
    except Exception:
        return False


def send_digest_from_db(
    db_session,
    dashboard_url: str = "",
    since_date: str | None = None,
    custom_message: str = "",
    edition: str = "daily",
) -> bool:
    """Build digest from database and send. Always works without CSV files."""
    recipients = _load_recipients()
    private = _load_private_recipients()
    if not recipients and not private:
        return False
    html_body = build_digest_html_from_db(db_session, dashboard_url, since_date,
                                           custom_message=custom_message,
                                           edition=edition)
    ok = True
    if recipients:
        ok = _send_html_digest(html_body, recipients, edition=edition)
    if private:
        _send_html_digest(html_body, private, edition=edition)
    return ok


def build_digest_html(
    output_dir: Path,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> str:
    """Legacy CSV entry point -- now delegates to DB-based builder."""
    from webapp.database import SessionLocal
    db = SessionLocal()
    try:
        return build_digest_html_from_db(db, dashboard_url, since_date)
    finally:
        db.close()


def send_digest_email(
    output_dir: Path,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> bool:
    """Legacy CSV entry point -- now delegates to DB-based sender."""
    from webapp.database import SessionLocal
    db = SessionLocal()
    try:
        return send_digest_from_db(db, dashboard_url, since_date)
    finally:
        db.close()
