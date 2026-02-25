"""
Email Alerts - Daily Brief

Email-client-compatible HTML digest (inline styles, table layout, no JS).
Works in Outlook, Gmail, Apple Mail, etc.

Focused on what's new: fund launches, new filings, and pending products.
Scannable in 30 seconds.

Sections:
  1. Header
  2. New Fund Launches (inception in last 7 days, from Bloomberg master data)
  3. New Filings (485 forms filed in last 24h, REX trusts first)
  4. Upcoming Launches (PENDING funds with expected effective dates)
  5. At a Glance (compact KPI strip)
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


def _render_daily_html(data: dict, dashboard_url: str = "") -> str:
    """Render the daily brief HTML from pre-gathered data."""
    today = datetime.now()
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    # --- Section 1: Header ---
    header = f"""
<tr><td style="background:{_NAVY};padding:24px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="color:{_WHITE};font-size:22px;font-weight:700;">REX ETP Daily Brief</td>
    <td align="right" style="color:rgba(255,255,255,0.7);font-size:13px;">{today.strftime('%A, %B %d, %Y')}</td>
  </tr></table>
</td></tr>"""

    # --- Section 2: New Fund Launches ---
    launches = data.get("launches", [])
    if launches:
        launch_rows = []
        for f in launches[:15]:
            ticker = _esc(f.get("ticker", ""))
            name = _esc(f.get("fund_name", ""))
            if len(name) > 40:
                name = name[:37] + "..."
            trust = _esc(f.get("trust_name", ""))
            if len(trust) > 25:
                trust = trust[:22] + "..."
            eff_date = _esc(f.get("effective_date", ""))
            is_rex = f.get("is_rex", False)
            name_html = name
            if is_rex:
                name_html = (
                    f'{name} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            launch_rows.append(
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;font-weight:600;white-space:nowrap;">{ticker}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{name_html}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;color:{_GRAY};">{trust}</td>'
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
        launches_section = f"""
<tr><td style="padding:20px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_GREEN};">
    New Fund Launches
  </div>
  <div style="font-size:12px;color:{_GRAY};margin-bottom:8px;">
    New ETF products launched in the past week
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Ticker</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Fund Name</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Issuer</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};text-align:right;">Launched</td>
    </tr>
    {''.join(launch_rows)}
  </table>
  {more_html}
</td></tr>"""
    else:
        launches_section = f"""
<tr><td style="padding:20px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_GREEN};">
    New Fund Launches
  </div>
  <div style="padding:12px;background:{_LIGHT};border-radius:6px;
    font-size:13px;color:{_GRAY};text-align:center;">
    No new launches this week.
  </div>
</td></tr>"""

    # --- Section 3: New Filings ---
    filing_groups = data.get("filing_groups", [])
    if filing_groups:
        filing_rows = []
        for fg in filing_groups[:10]:
            trust = _esc(fg.get("trust_name", ""))
            if len(trust) > 30:
                trust = trust[:27] + "..."
            form = _esc(fg.get("form", ""))
            count = fg.get("fund_count", 0)
            filing_date = _esc(fg.get("filing_date", ""))
            is_rex = fg.get("is_rex", False)
            trust_html = trust
            if is_rex:
                trust_html = (
                    f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            filing_rows.append(
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{trust_html}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;font-weight:600;text-align:center;">{form}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;text-align:center;">{count}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;color:{_GRAY};">{filing_date}</td>'
                f'</tr>'
            )
        more_html = ""
        if len(filing_groups) > 10:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {len(filing_groups) - 10} more trusts filed</div>'
            )
        filings_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_BLUE};">
    New Filings
  </div>
  <div style="font-size:12px;color:{_GRAY};margin-bottom:8px;">
    485 prospectus filings in the last 24 hours
    {f' | <a href="{dash_link}/dashboard?days=1" style="color:{_BLUE};font-size:11px;">View on dashboard</a>' if dash_link else ''}
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Trust</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};text-align:center;">Form</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};text-align:center;">Funds</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};text-align:right;">Filed</td>
    </tr>
    {''.join(filing_rows)}
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

    # --- Section 4: Upcoming Launches ---
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
            name_html = name
            if is_rex:
                name_html = (
                    f'{name} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            pending_rows.append(
                f'<tr>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{name_html}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;color:{_GRAY};">{trust}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;color:{_ORANGE};font-weight:600;">{eff_date}</td>'
                f'</tr>'
            )
        more_html = ""
        total_pending = data.get("total_pending", len(pending))
        if total_pending > 8:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {total_pending - 8} more on '
                f'{"<a href=\"" + dash_link + "/dashboard?status=PENDING\" style=\"color:" + _BLUE + ";\">dashboard</a>" if dash_link else "dashboard"}'
                f'</div>'
            )
        pending_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_ORANGE};">
    Upcoming Launches
  </div>
  <div style="font-size:12px;color:{_GRAY};margin-bottom:8px;">
    Funds with expected effective dates on file
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Fund Name</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};">Trust</td>
      <td style="padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;
        border-bottom:1px solid {_BORDER};text-align:right;">Expected Date</td>
    </tr>
    {''.join(pending_rows)}
  </table>
  {more_html}
</td></tr>"""

    # --- Section 5: At a Glance ---
    trust_count = data.get("trust_count", 0)
    total_funds = data.get("total_funds", 0)
    newly_effective_7d = data.get("newly_effective_7d", 0)
    total_pending = data.get("total_pending", 0)

    _kpi = f"padding:10px 6px;background:{_LIGHT};border-radius:6px;text-align:center;"
    _kpi_num = f"font-size:22px;font-weight:700;color:{_NAVY};"
    _kpi_lbl = f"font-size:9px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;"

    glance_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="23%" style="{_kpi}">
        <div style="{_kpi_num}">{trust_count}</div>
        <div style="{_kpi_lbl}">Trusts</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_kpi}">
        <div style="{_kpi_num}">{total_funds:,}</div>
        <div style="{_kpi_lbl}">Total Funds</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_kpi}">
        <div style="{_kpi_num}color:{_GREEN};">{newly_effective_7d}</div>
        <div style="{_kpi_lbl}">Effective (7d)</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_kpi}">
        <div style="{_kpi_num}color:{_ORANGE};">{total_pending}</div>
        <div style="{_kpi_lbl}">Pending</div>
      </td>
    </tr>
  </table>
</td></tr>"""

    # --- Section 6: Dashboard CTA ---
    cta_section = _dashboard_cta(dash_link) if dash_link else ""

    # --- Section 7: Footer ---
    footer = f"""
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <div style="font-size:11px;color:{_GRAY};text-align:center;">
    REX ETP Daily Brief | {today.strftime('%Y-%m-%d')}
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    Data sourced from SEC EDGAR | To unsubscribe, contact relasmar@rexfin.com
  </div>
</td></tr>"""

    # --- Assemble ---
    body = header + launches_section + filings_section + pending_section + glance_section + cta_section + footer

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>REX ETP Daily Brief - {today.strftime('%Y-%m-%d')}</title>
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


def _gather_daily_data(db_session, since_date: str | None = None) -> dict:
    """Query DB + Bloomberg master data for daily brief."""
    from sqlalchemy import func, select
    from datetime import date as date_type
    from webapp.models import Trust, FundStatus, Filing, FundExtraction

    today = datetime.now()
    if not since_date:
        since_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    since_dt = date_type.fromisoformat(since_date)
    week_ago = date_type.today() - timedelta(days=7)

    # --- New launches: prefer Bloomberg inception_date (more accurate) ---
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
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
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

    # Fallback to DB if no Bloomberg launches
    if not launches:
        launch_rows = db_session.execute(
            select(
                FundStatus.ticker, FundStatus.fund_name, FundStatus.effective_date,
                Trust.name.label("trust_name"), Trust.is_rex,
            )
            .join(Trust, Trust.id == FundStatus.trust_id)
            .where(FundStatus.status == "EFFECTIVE")
            .where(FundStatus.effective_date >= since_dt)
            .order_by(Trust.is_rex.desc(), FundStatus.effective_date.desc())
        ).all()
        for r in launch_rows:
            ticker = _clean_ticker(r.ticker)
            launches.append({
                "ticker": ticker if ticker else "--",
                "fund_name": r.fund_name or "",
                "trust_name": r.trust_name or "",
                "effective_date": str(r.effective_date) if r.effective_date else "",
                "is_rex": r.is_rex,
            })

    # --- New filings: 485 forms, REX trusts first, then by date ---
    filing_rows = db_session.execute(
        select(
            Trust.name.label("trust_name"), Trust.is_rex,
            Filing.form, Filing.filing_date,
            func.count(FundExtraction.id).label("fund_count"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= since_dt)
        .where(Filing.form.ilike("485%"))
        .group_by(Trust.name, Trust.is_rex, Filing.form, Filing.filing_date)
        .order_by(Trust.is_rex.desc(), Filing.filing_date.desc())
    ).all()

    filing_groups = []
    for r in filing_rows:
        filing_groups.append({
            "trust_name": r.trust_name or "",
            "form": r.form or "",
            "fund_count": r.fund_count or 0,
            "filing_date": str(r.filing_date) if r.filing_date else "",
            "is_rex": r.is_rex,
        })

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

    total_funds = db_session.execute(
        select(func.count(FundStatus.id))
    ).scalar() or 0

    newly_effective_7d = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "EFFECTIVE")
        .where(FundStatus.effective_date >= week_ago)
    ).scalar() or 0

    total_pending = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "PENDING")
    ).scalar() or 0

    return {
        "launches": launches,
        "filing_groups": filing_groups,
        "pending": pending,
        "trust_count": trust_count,
        "total_funds": total_funds,
        "newly_effective_7d": newly_effective_7d,
        "total_pending": total_pending,
    }


def build_digest_html_from_db(
    db_session,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> str:
    """Build daily brief from SQLite database."""
    data = _gather_daily_data(db_session, since_date)
    return _render_daily_html(data, dashboard_url)


def _send_html_digest(html_body: str, recipients: list[str]) -> bool:
    """Send pre-built HTML digest via Azure Graph or SMTP."""
    subject = f"REX ETP Daily Brief - {datetime.now().strftime('%Y-%m-%d')}"

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
) -> bool:
    """Build digest from database and send. Always works without CSV files."""
    recipients = _load_recipients()
    if not recipients:
        return False
    html_body = build_digest_html_from_db(db_session, dashboard_url, since_date)
    return _send_html_digest(html_body, recipients)


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
