"""
Email Alerts - Daily Digest

Email-client-compatible HTML digest (inline styles, table layout, no JS).
Works in Outlook, Gmail, Apple Mail, etc.

Executive summary only: Dashboard link + KPIs + what-changed counts.
All detail lives on the dashboard.
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


def _render_digest_html(
    trust_count: int,
    total: int,
    eff_count: int,
    pend_count: int,
    delay_count: int,
    new_filings_count: int,
    newly_effective_count: int,
    changed_count: int,
    dashboard_url: str = "",
    since_date: str = "",
) -> str:
    """Render digest HTML with pre-computed KPI values."""
    today = datetime.now()
    if not since_date:
        since_date = today.strftime("%Y-%m-%d")
    dash_link = dashboard_url or ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETP Filing Tracker - {today.strftime('%Y-%m-%d')}</title>
</head>
<body style="margin:0;padding:0;background:{_LIGHT};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="background:{_WHITE};border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="background:{_NAVY};padding:24px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="color:{_WHITE};font-size:22px;font-weight:700;">ETP Filing Tracker</td>
    <td align="right" style="color:rgba(255,255,255,0.7);font-size:13px;">{today.strftime('%A, %B %d, %Y')}</td>
  </tr></table>
</td></tr>

<!-- Dashboard Button (TOP) -->
{_dashboard_cta(dash_link) if dash_link else ""}

<!-- KPI Row -->
<tr><td style="padding:20px 30px 10px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td width="20%" align="center" style="padding:12px 8px;background:{_LIGHT};border-radius:8px;">
      <div style="font-size:28px;font-weight:700;color:{_NAVY};">{trust_count}</div>
      <div style="font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Trusts</div>
    </td>
    <td width="4%"></td>
    <td width="20%" align="center" style="padding:12px 8px;background:{_LIGHT};border-radius:8px;">
      <div style="font-size:28px;font-weight:700;color:{_NAVY};">{total}</div>
      <div style="font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Total Funds</div>
    </td>
    <td width="4%"></td>
    <td width="16%" align="center" style="padding:12px 8px;background:{_LIGHT};border-radius:8px;">
      <div style="font-size:28px;font-weight:700;color:{_GREEN};">{eff_count}</div>
      <div style="font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Effective</div>
    </td>
    <td width="4%"></td>
    <td width="16%" align="center" style="padding:12px 8px;background:{_LIGHT};border-radius:8px;">
      <div style="font-size:28px;font-weight:700;color:{_ORANGE};">{pend_count}</div>
      <div style="font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Pending</div>
    </td>
    <td width="4%"></td>
    <td width="16%" align="center" style="padding:12px 8px;background:{_LIGHT};border-radius:8px;">
      <div style="font-size:28px;font-weight:700;color:{_RED};">{delay_count}</div>
      <div style="font-size:10px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;">Delayed</div>
    </td>
  </tr></table>
</td></tr>

<!-- What Changed -->
<tr><td style="padding:15px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#e3f2fd;border-left:4px solid {_BLUE};border-radius:0 8px 8px 0;">
  <tr><td style="padding:15px 20px;">
    <div style="font-size:16px;font-weight:700;color:{_NAVY};margin-bottom:6px;">What Changed ({since_date})</div>
    <table cellpadding="0" cellspacing="0" border="0"><tr>
      <td style="padding-right:24px;font-size:14px;"><span style="font-size:20px;font-weight:700;color:{_BLUE};">{new_filings_count}</span> new filings</td>
      <td style="padding-right:24px;font-size:14px;"><span style="font-size:20px;font-weight:700;color:{_GREEN};">{newly_effective_count}</span> newly effective</td>
      <td style="padding-right:24px;font-size:14px;"><span style="font-size:20px;font-weight:700;color:{_ORANGE};">{changed_count}</span> name changes</td>
    </tr></table>
  </td></tr></table>
</td></tr>

<!-- Dashboard Button (BOTTOM) -->
{_dashboard_cta(dash_link) if dash_link else ""}

<!-- Footer -->
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="font-size:11px;color:{_GRAY};">ETP Filing Tracker | {today.strftime('%Y-%m-%d %H:%M')}</td>
    <td align="right" style="font-size:11px;color:{_GRAY};">{trust_count} trusts | {total} funds</td>
  </tr></table>
</td></tr>

</table>
</td></tr></table>
</body></html>"""


def build_digest_html(
    output_dir: Path,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> str:
    """Build digest from CSV files in outputs/ directory."""
    today = datetime.now()
    if not since_date:
        since_date = today.strftime("%Y-%m-%d")

    all_status = []
    all_names = []
    for folder in sorted(output_dir.iterdir()):
        if not folder.is_dir():
            continue
        for f4 in folder.glob("*_4_Fund_Status.csv"):
            all_status.append(pd.read_csv(f4, dtype=str))
        for f5 in folder.glob("*_5_Name_History.csv"):
            all_names.append(pd.read_csv(f5, dtype=str))

    df_all = pd.concat(all_status, ignore_index=True) if all_status else pd.DataFrame()
    df_names = pd.concat(all_names, ignore_index=True) if all_names else pd.DataFrame()

    new_filings_count = 0
    if not df_all.empty and "Latest Filing Date" in df_all.columns:
        date_mask = df_all["Latest Filing Date"].fillna("") >= since_date
        form_mask = df_all["Latest Form"].fillna("").str.upper().str.startswith("485")
        new_filings_count = int((date_mask & form_mask).sum())

    newly_effective_count = 0
    if not df_all.empty:
        eff_mask = (
            (df_all["Status"] == "EFFECTIVE")
            & (df_all["Effective Date"].fillna("") >= since_date)
        )
        newly_effective_count = int(eff_mask.sum())

    changed_count = 0
    if not df_names.empty:
        multi = df_names.groupby("Series ID").size()
        changed_count = int((multi > 1).sum())

    total = len(df_all) if not df_all.empty else 0
    eff_count = len(df_all[df_all["Status"] == "EFFECTIVE"]) if not df_all.empty else 0
    pend_count = len(df_all[df_all["Status"] == "PENDING"]) if not df_all.empty else 0
    delay_count = len(df_all[df_all["Status"] == "DELAYED"]) if not df_all.empty else 0
    trust_count = df_all["Trust"].nunique() if not df_all.empty else 0

    return _render_digest_html(
        trust_count, total, eff_count, pend_count, delay_count,
        new_filings_count, newly_effective_count, changed_count,
        dashboard_url, since_date,
    )


def build_digest_html_from_db(
    db_session,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> str:
    """Build digest from SQLite database (always available, no CSV dependency)."""
    from sqlalchemy import func, select
    from datetime import date as date_type

    today = datetime.now()
    if not since_date:
        since_date = today.strftime("%Y-%m-%d")
    since_dt = date_type.fromisoformat(since_date)

    # Import models inside function to avoid circular import
    from webapp.models import Trust, FundStatus, Filing, NameHistory

    trust_count = db_session.execute(
        select(func.count(Trust.id)).where(Trust.is_active == True)
    ).scalar() or 0

    total = db_session.execute(
        select(func.count(FundStatus.id))
    ).scalar() or 0

    eff_count = db_session.execute(
        select(func.count(FundStatus.id)).where(FundStatus.status == "EFFECTIVE")
    ).scalar() or 0

    pend_count = db_session.execute(
        select(func.count(FundStatus.id)).where(FundStatus.status == "PENDING")
    ).scalar() or 0

    delay_count = db_session.execute(
        select(func.count(FundStatus.id)).where(FundStatus.status == "DELAYED")
    ).scalar() or 0

    new_filings_count = db_session.execute(
        select(func.count(Filing.id))
        .where(Filing.filing_date >= since_dt)
        .where(Filing.form.ilike("485%"))
    ).scalar() or 0

    newly_effective_count = db_session.execute(
        select(func.count(FundStatus.id))
        .where(FundStatus.status == "EFFECTIVE")
        .where(FundStatus.effective_date >= since_dt)
    ).scalar() or 0

    changed_count = db_session.execute(
        select(func.count()).select_from(
            select(NameHistory.series_id)
            .group_by(NameHistory.series_id)
            .having(func.count(NameHistory.id) > 1)
            .subquery()
        )
    ).scalar() or 0

    return _render_digest_html(
        trust_count, total, eff_count, pend_count, delay_count,
        new_filings_count, newly_effective_count, changed_count,
        dashboard_url, since_date,
    )


def _send_html_digest(html_body: str, recipients: list[str]) -> bool:
    """Send pre-built HTML digest via Azure Graph or SMTP."""
    subject = f"ETP Filing Tracker - Daily Digest ({datetime.now().strftime('%Y-%m-%d')})"

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


def send_digest_email(
    output_dir: Path,
    dashboard_url: str = "",
    since_date: str | None = None,
) -> bool:
    """Build and send digest from CSV files. Legacy - prefer send_digest_from_db."""
    recipients = _load_recipients()
    if not recipients:
        return False
    html_body = build_digest_html(output_dir, dashboard_url, since_date)
    return _send_html_digest(html_body, recipients)
