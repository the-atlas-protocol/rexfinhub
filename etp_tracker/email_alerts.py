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

import logging
log = logging.getLogger(__name__)

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
_TEAL = "#00897B"
_REX_ROW_BG = "#e8f5e9"
_HIGHLIGHT_BG = "#f4f5f6"


def _fmt_aum(val: float) -> str:
    """Format AUM value (in millions) for display."""
    if val is None or val != val:  # NaN
        return "$0"
    if abs(val) >= 1000:
        return f"${val / 1000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    if abs(val) >= 0.01:
        return f"${val:.2f}M"
    return "$0"


def _load_recipients(project_root: Path | None = None, list_type: str = "daily") -> list[str]:
    """Load recipients from DB (primary) or text file (fallback).

    Args:
        list_type: Which report's recipients to load (daily, weekly, li, income, flow, autocall).
    """
    # Primary: read from DB
    try:
        from webapp.database import SessionLocal
        from webapp.services.recipients import get_recipients
        db = SessionLocal()
        try:
            recipients = get_recipients(db, list_type)
            if recipients:
                return recipients
        finally:
            db.close()
    except Exception:
        pass  # DB not available, fall back to text file

    # Fallback: text file
    if project_root is None:
        project_root = Path(__file__).parent.parent
    recipients_file = project_root / "config" / "email_recipients.txt"
    if recipients_file.exists():
        lines = recipients_file.read_text().strip().splitlines()
        return [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    env_to = os.environ.get("SMTP_TO", "")
    return [e.strip() for e in env_to.split(",") if e.strip()]


def _load_private_recipients(project_root: Path | None = None) -> list[str]:
    """Load private (BCC) recipients from DB or text file."""
    try:
        from webapp.database import SessionLocal
        from webapp.services.recipients import get_private_recipients
        db = SessionLocal()
        try:
            recipients = get_private_recipients(db)
            if recipients:
                return recipients
        finally:
            db.close()
    except Exception:
        pass

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


def _dual_kpi_box(market_row: list, rex_row: list | None = None) -> str:
    """Compact dual-row KPI box: Market row on top, REX row below, single border.

    Each item is (label, value) or (label, value, is_positive_bool).
    """
    def _cell(label: str, value: str, color: str = _NAVY) -> str:
        return (
            f'<td style="padding:6px 4px;text-align:center;">'
            f'<div style="font-size:16px;font-weight:700;color:{color};">{_esc(value)}</div>'
            f'<div style="font-size:8px;color:{_GRAY};text-transform:uppercase;'
            f'letter-spacing:0.4px;margin-top:1px;">{_esc(label)}</div></td>'
        )

    def _build(items):
        cells = []
        for item in items:
            if len(item) == 3:
                lbl, val, pos = item
                c = _GREEN if pos else _RED
            else:
                lbl, val = item[0], item[1]
                c = _NAVY
            cells.append(_cell(lbl, val, c))
        return "".join(cells)

    mkt = _build(market_row)
    rows = f'<tr style="background:{_LIGHT};">{mkt}</tr>'
    if rex_row:
        rex = _build(rex_row)
        rows += (
            f'<tr><td colspan="{len(market_row)}" style="padding:0;">'
            f'<div style="border-top:1px solid {_BORDER};"></div></td></tr>'
            f'<tr style="background:{_REX_ROW_BG};">{rex}</tr>'
        )
    return (
        f'<tr><td style="padding:10px 30px 5px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid {_BORDER};border-radius:8px;overflow:hidden;">'
        f'{rows}'
        f'</table></td></tr>'
    )


def _gather_market_snapshot(db=None) -> dict | None:
    """Pull Bloomberg data for the daily brief market sections.

    Returns None if Bloomberg data is unavailable (graceful degradation).
    """
    try:
        from webapp.services.market_data import data_available, get_rex_summary, get_master_data, get_category_summary
        if db is None:
            from webapp.database import SessionLocal
            db = SessionLocal()
        if not data_available(db):
            return None

        # REX KPIs (ETFs + ETNs) -- with ETN overrides for internal reports
        rex = get_rex_summary(db, fund_structure="ETF,ETN", etn_overrides=True)
        master = get_master_data(db, etn_overrides=True)

        # Filter to active ETPs (ETFs + ETNs)
        ft_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
        if ft_col:
            master = master[master[ft_col].isin(["ETF", "ETN"])]
        mkt_col = next((c for c in master.columns if c.lower().strip() == "market_status"), None)
        if mkt_col:
            master = master[master[mkt_col] == "ACTV"]

        # 1D flow (sum across all REX ETPs)
        rex_df = master[master["is_rex"] == True].copy()
        if "ticker_clean" in rex_df.columns:
            rex_df = rex_df.drop_duplicates(subset=["ticker_clean"], keep="first")
        flow_1d = float(rex_df["t_w4.fund_flow_1day"].sum()) if "t_w4.fund_flow_1day" in rex_df.columns else 0.0

        def _fmt_flow_val(val: float) -> str:
            sign = "+" if val >= 0 else "-"
            av = abs(val)
            if av >= 1000:
                return f"{sign}${av/1000:,.1f}B"
            if av >= 1:
                return f"{sign}${av:.1f}M"
            return f"{sign}${av:.2f}M"

        rex_kpis = rex.get("kpis", {})
        kpis = {
            "aum": rex_kpis.get("total_aum_fmt", "$0"),
            "flow_1d": flow_1d,
            "flow_1d_fmt": _fmt_flow_val(flow_1d),
            "flow_1d_positive": flow_1d >= 0,
            "flow_1w": rex_kpis.get("flow_1w", 0),
            "flow_1w_fmt": rex_kpis.get("flow_1w_fmt", "$0"),
            "flow_1w_positive": rex_kpis.get("flow_1w_positive", True),
            "products": rex_kpis.get("num_products", 0),
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

        # Winners & Losers: top 5 / worst 5 by 1D return
        winners_losers = {"winners": [], "losers": []}
        if not rex_df.empty and "t_w3.total_return_1day" in rex_df.columns:
            valid_ret = rex_df[rex_df["t_w3.total_return_1day"].notna()].copy()
            _flow_col = "t_w4.fund_flow_1day" if "t_w4.fund_flow_1day" in valid_ret.columns else None
            for _, row in valid_ret.nlargest(5, "t_w3.total_return_1day").iterrows():
                ret = float(row.get("t_w3.total_return_1day", 0))
                flow = float(row.get(_flow_col, 0)) if _flow_col else 0.0
                winners_losers["winners"].append({
                    "ticker": str(row.get("ticker_clean", "")),
                    "name": str(row.get("fund_name", ""))[:55],
                    "return_1d": ret,
                    "return_1d_fmt": f"{ret:+.2f}%",
                    "flow_1d": flow,
                    "flow_1d_fmt": _fmt_flow_val(flow),
                })
            for _, row in valid_ret.nsmallest(5, "t_w3.total_return_1day").iterrows():
                ret = float(row.get("t_w3.total_return_1day", 0))
                flow = float(row.get(_flow_col, 0)) if _flow_col else 0.0
                winners_losers["losers"].append({
                    "ticker": str(row.get("ticker_clean", "")),
                    "name": str(row.get("fund_name", ""))[:55],
                    "return_1d": ret,
                    "return_1d_fmt": f"{ret:+.2f}%",
                    "flow_1d": flow,
                    "flow_1d_fmt": _fmt_flow_val(flow),
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
                cat_data = get_category_summary(db, cat, fund_structure="ETF,ETN", etn_overrides=True)
                cat_aum = cat_data.get("cat_kpis", {}).get("total_aum", 0)
                cat_flow_1w = cat_data.get("cat_kpis", {}).get("flow_1w", 0)
                rex_share = cat_data.get("market_share", 0)
                landscape.append({
                    "category": cat,
                    "aum": cat_aum,
                    "aum_fmt": f"${cat_aum/1000:,.1f}B" if cat_aum >= 1000 else f"${cat_aum:,.0f}M",
                    "flow_1w": cat_flow_1w,
                    "flow_1w_fmt": _fmt_flow_val(cat_flow_1w),
                    "flow_1w_positive": cat_flow_1w >= 0,
                    "rex_share": rex_share,
                    "rex_share_fmt": f"{rex_share:.1f}%",
                })
            except Exception:
                continue

        # Market pulse: index proxies (1D total returns)
        # Use yfinance for accurate daily returns (Bloomberg 1D can lag)
        market_pulse = {}
        try:
            _PULSE_TICKERS = [
                ("SPY US", "SPY", "S&P 500"), ("QQQ US", "QQQ", "NASDAQ"),
                ("DIA US", "DIA", "Dow"), ("IWM US", "IWM", "Russell 2000"),
                ("IBIT US", "IBIT", "Bitcoin"), ("GLD US", "GLD", "Gold"),
            ]
            # Try yfinance first for accurate 1D returns
            _yf_returns = {}
            try:
                import yfinance as yf
                from datetime import date as _date_cls, timedelta as _td
                _end = _date_cls.today() + _td(days=1)
                _start = _date_cls.today() - _td(days=5)
                for _ptk_bbg, _ptk_yf, _plbl in _PULSE_TICKERS:
                    try:
                        _hist = yf.download(_ptk_yf, start=str(_start), end=str(_end), progress=False, auto_adjust=False)
                        if len(_hist) >= 2:
                            _prev = float(_hist["Close"].values.flatten()[-2])
                            _last = float(_hist["Close"].values.flatten()[-1])
                            if _prev > 0:
                                _yf_returns[_plbl] = (_last - _prev) / _prev * 100
                    except Exception:
                        pass
                if _yf_returns:
                    pass  # yfinance returns loaded
            except ImportError:
                pass  # yfinance not installed, fall back to Bloomberg

            for _ptk_bbg, _ptk_yf, _plbl in _PULSE_TICKERS:
                if _plbl in _yf_returns:
                    _pret = _yf_returns[_plbl]
                else:
                    _prow = master[master["ticker"] == _ptk_bbg]
                    _pret = float(_prow.iloc[0].get("t_w5.price_return_1day", 0)) if not _prow.empty else 0
                market_pulse[_plbl] = {"return_1d": _pret, "return_1d_fmt": f"{_pret:+.2f}%"}
            # Industry totals (for ETP Market Overview, not Market Pulse)
            _all_dedup = master.drop_duplicates(subset=["ticker"], keep="first") if "ticker" in master.columns else master
            import pandas as _pd
            _ind_aum = float(_pd.to_numeric(_all_dedup["t_w4.aum"], errors="coerce").fillna(0).sum()) if "t_w4.aum" in _all_dedup.columns else 0
            _ind_flow_1d = float(_pd.to_numeric(_all_dedup["t_w4.fund_flow_1day"], errors="coerce").fillna(0).sum()) if "t_w4.fund_flow_1day" in _all_dedup.columns else 0
            _ind_flow_1w = float(_pd.to_numeric(_all_dedup["t_w4.fund_flow_1week"], errors="coerce").fillna(0).sum()) if "t_w4.fund_flow_1week" in _all_dedup.columns else 0
            _ind_count = len(_all_dedup)
            market_pulse["_industry"] = {
                "aum": _ind_aum, "aum_fmt": f"${_ind_aum/1000:,.1f}B" if _ind_aum >= 1000 else f"${_ind_aum:,.0f}M",
                "flow_1d": _ind_flow_1d, "flow_1d_fmt": _fmt_flow_val(_ind_flow_1d),
                "flow_1d_positive": _ind_flow_1d >= 0,
                "flow_1w": _ind_flow_1w, "flow_1w_fmt": _fmt_flow_val(_ind_flow_1w),
                "flow_1w_positive": _ind_flow_1w >= 0,
                "count": _ind_count,
            }
        except Exception as _pulse_err:
            import logging as _logging
            _logging.getLogger(__name__).error("Market pulse failed: %s", _pulse_err)

        # Daily movers: top 5 inflows + top 3 outflows by 1D flow
        daily_movers = {"inflows": [], "outflows": []}
        if not rex_df.empty and "t_w4.fund_flow_1day" in rex_df.columns:
            valid_1d = rex_df[rex_df["t_w4.fund_flow_1day"].notna()].copy()
            for _, row in valid_1d.nlargest(5, "t_w4.fund_flow_1day").iterrows():
                _f1d = float(row.get("t_w4.fund_flow_1day", 0))
                _aum = float(row.get("t_w4.aum", 0))
                daily_movers["inflows"].append({
                    "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                    "fund_name": str(row.get("fund_name", ""))[:55],
                    "aum_fmt": _fmt_aum(_aum),
                    "flow_1d": _f1d, "flow_1d_fmt": _fmt_flow_val(_f1d),
                })
            for _, row in valid_1d.nsmallest(3, "t_w4.fund_flow_1day").iterrows():
                _f1d = float(row.get("t_w4.fund_flow_1day", 0))
                if _f1d >= 0:
                    continue
                _aum = float(row.get("t_w4.aum", 0))
                daily_movers["outflows"].append({
                    "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                    "fund_name": str(row.get("fund_name", ""))[:55],
                    "aum_fmt": _fmt_aum(_aum),
                    "flow_1d": _f1d, "flow_1d_fmt": _fmt_flow_val(_f1d),
                })

        # Data date from market data
        data_as_of = ""
        try:
            from webapp.services.market_data import get_data_as_of
            data_as_of = get_data_as_of(db)
        except Exception:
            pass

        return {
            "kpis": kpis,
            "top_movers": top_movers,
            "daily_movers": daily_movers,
            "winners_losers": winners_losers,
            "landscape": landscape,
            "market_pulse": market_pulse,
            "data_as_of": data_as_of,
        }
    except Exception as _snap_err:
        import logging as _logging
        _logging.getLogger(__name__).error("Market snapshot failed: %s", _snap_err)
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


def _render_market_pulse(pulse: dict) -> str:
    """Render market pulse: 1D total returns for major index proxies."""
    if not pulse:
        return ""

    _cell = f"padding:8px 4px;background:{_LIGHT};border-radius:8px;text-align:center;"
    _val = f"font-size:16px;font-weight:700;"
    _lbl = f"font-size:8px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.4px;"

    _LABELS = ["S&P 500", "NASDAQ", "Dow", "Russell 2000", "Bitcoin", "Gold"]
    cells = []
    for label in _LABELS:
        info = pulse.get(label)
        if not info:
            continue
        ret = info["return_1d"]
        color = _GREEN if ret >= 0 else _RED
        cells.append(
            f'<td width="16%" style="{_cell}">'
            f'<div style="{_val}color:{color};">{info["return_1d_fmt"]}</div>'
            f'<div style="{_lbl}">{label}</div></td>'
            f'<td width="0.5%"></td>'
        )
    if not cells:
        return ""

    # Split into two rows of 3 for readability
    mid = len(cells) // 2
    row1 = "".join(cells[:mid])
    row2 = "".join(cells[mid:])

    _title = (
        f"font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;"
        f"padding-bottom:6px;border-bottom:2px solid {_BLUE};"
    )

    return f"""
<tr><td style="padding:15px 30px 5px;">
  <div style="{_title}">Market Pulse</div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>{row1}</tr>
    <tr><td colspan="{mid * 2}" style="padding:4px 0;"></td></tr>
    <tr>{row2}</tr>
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


def _render_daily_movers(movers: dict) -> str:
    """Compact daily flow movers: top 3 inflows + top 3 outflows by 1D flow."""
    inflows = movers.get("inflows", [])
    outflows = movers.get("outflows", [])
    if not inflows and not outflows:
        return ""

    _col = (
        f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"border-bottom:1px solid {_BORDER};"
    )
    _cell = f"padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;"
    rows = []
    for f in inflows:
        rows.append(
            f'<tr>'
            f'<td style="{_cell}font-weight:600;">{_esc(f["ticker"])}</td>'
            f'<td style="{_cell}color:{_GRAY};">{_esc(f.get("fund_name", ""))}</td>'
            f'<td style="{_cell}text-align:right;">{_esc(f["aum_fmt"])}</td>'
            f'<td style="{_cell}text-align:right;color:{_GREEN};font-weight:600;">{_esc(f["flow_1d_fmt"])}</td>'
            f'</tr>'
        )
    for f in outflows:
        rows.append(
            f'<tr>'
            f'<td style="{_cell}font-weight:600;">{_esc(f["ticker"])}</td>'
            f'<td style="{_cell}color:{_GRAY};">{_esc(f.get("fund_name", ""))}</td>'
            f'<td style="{_cell}text-align:right;">{_esc(f["aum_fmt"])}</td>'
            f'<td style="{_cell}text-align:right;color:{_RED};font-weight:600;">{_esc(f["flow_1d_fmt"])}</td>'
            f'</tr>'
        )

    return f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Today's REX Movers
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Ticker</td>
      <td style="{_col}">Fund</td>
      <td style="{_col}text-align:right;">AUM</td>
      <td style="{_col}text-align:right;">1D Flow</td>
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
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_NAVY};">
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
        f'<td style="background:{_BLUE};border-radius:8px;padding:14px 32px;">'
        f'<a href="{_esc(dash_link)}" style="color:{_WHITE};text-decoration:none;font-size:14px;font-weight:700;">Open Dashboard</a>'
        f'</td></tr></table>'
        f'<div style="font-size:12px;color:{_GRAY};margin-top:8px;">View full details, filings, and AI analysis</div>'
        f'</td></tr>'
    )


def _render_winners_losers(winners: list[dict], losers: list[dict]) -> str:
    """Render Winners & Losers by 1D return with 1D flow (like weekly report)."""
    if not winners and not losers:
        return ""

    _col_hdr = (
        f"padding:3px 6px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
        f"letter-spacing:0.5px;border-bottom:1px solid {_BORDER};"
    )

    def _section(title: str, items: list, color: str) -> str:
        if not items:
            return ""
        rows = ""
        for item in items[:5]:
            ticker = _esc(item.get("ticker", ""))
            name = _esc(item.get("name", ""))
            if len(name) > 50:
                name = name[:47] + "..."
            ret = _esc(item.get("return_1d_fmt", ""))
            ret_val = item.get("return_1d", 0)
            ret_clr = color if abs(ret_val) >= 0.005 else _NAVY
            flow = _esc(item.get("flow_1d_fmt", ""))
            flow_val = item.get("flow_1d", 0)
            flow_clr = _GREEN if flow_val > 0.005 else (_RED if flow_val < -0.005 else _NAVY)
            rows += (
                f'<tr>'
                f'<td style="padding:3px 6px;font-size:11px;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};white-space:nowrap;width:50px;">{ticker}</td>'
                f'<td style="padding:3px 6px;font-size:10px;color:{_GRAY};'
                f'border-bottom:1px solid {_BORDER};">{name}</td>'
                f'<td style="padding:3px 6px;font-size:11px;text-align:right;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};color:{ret_clr};width:65px;">{ret}</td>'
                f'<td style="padding:3px 6px;font-size:11px;text-align:right;font-weight:600;'
                f'border-bottom:1px solid {_BORDER};color:{flow_clr};width:65px;">{flow}</td>'
                f'</tr>'
            )
        return (
            f'<div style="font-size:13px;font-weight:700;color:{color};margin:10px 0 4px;">{title}</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
            f' style="border-collapse:collapse;">'
            f'<tr><td style="{_col_hdr}width:50px;">Ticker</td>'
            f'<td style="{_col_hdr}">Fund Name</td>'
            f'<td style="{_col_hdr}text-align:right;width:65px;">1D Return</td>'
            f'<td style="{_col_hdr}text-align:right;width:65px;">1D Flow</td></tr>'
            f'{rows}</table>'
        )

    winners_html = _section("Winners", winners, _GREEN)
    losers_html = _section("Losers", losers, _RED)

    return (
        f'<tr><td style="padding:15px 30px 10px;">'
        f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 4px 0;'
        f'padding-bottom:6px;border-bottom:2px solid {_BLUE};">'
        f'REX Winners & Losers</div>'
        f'{winners_html}{losers_html}'
        f'</td></tr>'
    )


def _daily_highlights_box(bullets: list[str]) -> str:
    """Render a key highlights callout box for the daily report."""
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


def _daily_highlights(data: dict) -> list[str]:
    """Generate 3-5 executive highlights for the daily filing report.

    Focused on market-wide ETP activity; REX-specific KPIs live on the REX dashboard.
    """
    bullets = []
    snapshot = data.get("market_snapshot")

    # 1. Market-wide ETP headline
    if snapshot:
        pulse = snapshot.get("market_pulse", {})
        ind = pulse.get("_industry", {}) if pulse else {}
        count = ind.get("count", 0)
        aum_fmt = ind.get("aum_fmt", "")
        flow_1d = ind.get("flow_1d_fmt", "")
        if aum_fmt:
            bullets.append(f"ETP market: {count:,} active products, {aum_fmt} AUM ({flow_1d} 1D net flow)")

    # 2. New launches (7d)
    launches = data.get("launches", [])
    if launches:
        tickers = ", ".join(l.get("ticker", "?") for l in launches[:4] if l.get("ticker"))
        bullets.append(f"{len(launches)} new ETP launches this week ({tickers})")

    # 3. Filing activity
    filing_groups = data.get("filing_groups", [])
    total_trusts = len(filing_groups)
    if total_trusts > 0:
        bullets.append(f"{total_trusts} trusts filed 485 forms today")

    # 4. Effective / Pending status
    newly_effective = data.get("newly_effective_1d", 0)
    total_pending = data.get("total_pending", 0)
    if newly_effective > 0 or total_pending > 0:
        parts = []
        if newly_effective > 0:
            parts.append(f"{newly_effective:,} fund(s) went effective today")
        if total_pending > 0:
            parts.append(f"{total_pending:,} pending effectiveness")
        bullets.append(" | ".join(parts))

    return bullets[:5]


def _render_daily_html(data: dict, dashboard_url: str = "", custom_message: str = "",
                       edition: str = "daily") -> str:
    """Render the daily brief HTML from pre-gathered data.

    edition: "daily" (5 PM brief), "morning" (legacy), or "evening" (legacy).
    """
    today = datetime.now()
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    _is_evening = edition == "evening"
    _titles = {"daily": "REX Daily ETP Report", "evening": "REX Daily ETP Report",
               "morning": "REX ETP Morning Brief"}
    _title = _titles.get(edition, "REX Daily ETP Report")
    _header_bg = _NAVY
    _accent = _BLUE

    # --- Data date: prefer Bloomberg data date over datetime.now() ---
    snapshot = data.get("market_snapshot")
    _data_date_str = ""
    if snapshot:
        _data_date_str = snapshot.get("data_as_of", "")
    if not _data_date_str:
        _data_date_str = today.strftime("%B %d, %Y")
    _date_short = today.strftime("%Y-%m-%d")

    # --- Custom message ---
    msg_html = ""
    if custom_message:
        msg_html = (
            f'<tr><td style="padding:12px 30px 0;">'
            f'<div style="padding:10px 14px;background:#eef3f8;border-left:3px solid {_BLUE};'
            f'border-radius:4px;font-size:13px;color:{_NAVY};">'
            f'{_esc(custom_message)}</div>'
            f'</td></tr>'
        )

    # --- Header ---
    header = f"""
<tr><td style="background:{_header_bg};padding:24px 30px;">
  <div style="color:{_WHITE};font-size:22px;font-weight:700;letter-spacing:-0.5px;">{_title} | {_data_date_str}</div>
</td></tr>"""

    # (Old 3-card scorecard removed — data now in highlights + REX Market Snapshot)

    # --- New Fund Launches ---
    launches = data.get("launches", [])
    if launches:
        launch_rows = []
        for f in launches:
            ticker = _esc(f.get("ticker", ""))
            name = _esc(f.get("fund_name", ""))
            if len(name) > 55:
                name = name[:52] + "..."
            eff_date = _esc(f.get("effective_date", ""))
            aum_val = f.get("aum", 0)
            aum_fmt = _fmt_aum(aum_val) if aum_val > 0 else "--"
            is_rex = f.get("is_rex", False)
            ticker_html = ticker
            if is_rex:
                ticker_html = (
                    f'{ticker} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 4px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            launch_rows.append(
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;font-weight:600;white-space:nowrap;">{ticker_html}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:11px;">{name}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;white-space:nowrap;">{aum_fmt}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid {_BORDER};'
                f'font-size:10px;text-align:right;color:{_GRAY};white-space:nowrap;">{eff_date}</td>'
                f'</tr>'
            )
        more_html = ""
        _col = (
            f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
            f"border-bottom:1px solid {_BORDER};"
        )
        launches_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_GREEN};">
    New Fund Launches (7d)
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Ticker</td>
      <td style="{_col}">Fund Name</td>
      <td style="{_col}text-align:right;">AUM</td>
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
    New Fund Launches (7d)
  </div>
  <div style="padding:12px;background:{_LIGHT};border-radius:6px;
    font-size:13px;color:{_GRAY};text-align:center;">
    No new launches in the last 7 days.
  </div>
</td></tr>"""

    # --- Today's 485 Filings: split into New Fund Filings vs Updated Fund Filings ---
    filing_groups = data.get("filing_groups", [])

    _row_base = (
        f"padding:8px 10px;border-bottom:1px solid {_BORDER};"
        f"font-size:12px;color:{_NAVY};"
    )
    _row_rex = (
        f"padding:8px 10px;border-bottom:1px solid {_BORDER};"
        f"font-size:12px;color:{_NAVY};"
        f"background:{_REX_ROW_BG};"
    )

    def _render_filing_group_row(fg: dict) -> str:
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

        trust_label = trust
        if is_rex:
            trust_label = (
                f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                f'padding:1px 6px;border-radius:3px;font-size:9px;'
                f'font-weight:700;vertical-align:middle;">REX</span>'
            )

        other_funds = fg.get("other_funds", [])
        # Concatenate ALL fund names for this trust — relevant first, then others.
        all_names = [_esc(f) for f in relevant] + [_esc(f) for f in other_funds]
        summary = ", ".join(all_names) if all_names else f"{total} funds filed"

        cat_tags = ""
        for cat, cnt in sorted(cats.items(), key=lambda x: x[1], reverse=True):
            cat_color = {"leveraged": "#e74c3c", "income": "#27ae60", "crypto": "#f39c12"}.get(cat, _GRAY)
            cat_tags += (
                f' <span style="display:inline-block;padding:1px 5px;border-radius:3px;'
                f'font-size:9px;color:{_WHITE};background:{cat_color};'
                f'margin-left:2px;">{cnt} {cat}</span>'
            )

        return (
            f'<tr><td style="{row_style}">'
            f'<div style="font-weight:600;margin-bottom:2px;">'
            f'{trust_label} <span style="font-weight:400;color:{_GRAY};font-size:10px;">{form}</span>'
            f'{cat_tags}</div>'
            f'<div style="font-size:11px;color:{_GRAY};">{summary}</div>'
            f'</td></tr>'
        )

    def _render_filings_block(title: str, groups: list, accent_color: str, empty_msg: str | None) -> str:
        if not groups:
            if empty_msg is None:
                return ""
            return f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {accent_color};">
    {title}
  </div>
  <div style="padding:12px;background:{_LIGHT};border-radius:6px;
    font-size:13px;color:{_GRAY};text-align:center;">
    {empty_msg}
  </div>
</td></tr>"""

        items = "".join(_render_filing_group_row(fg) for fg in groups)
        return f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {accent_color};">
    {title} <span style="font-weight:400;color:{_GRAY};font-size:11px;">({len(groups)})</span>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    {items}
  </table>
</td></tr>"""

    new_groups = [fg for fg in filing_groups if fg.get("is_new")]
    updated_groups = [fg for fg in filing_groups if not fg.get("is_new")]

    # Show empty state if NEITHER section has anything (all-quiet day).
    if not new_groups and not updated_groups:
        filings_section = _render_filings_block(
            "Today's 485 Filings", [], _BLUE, "No 485 filings today."
        )
    else:
        filings_section = (
            _render_filings_block("New Fund Filings", new_groups, _GREEN, None)
            + _render_filings_block("Updated Fund Filings", updated_groups, _BLUE, None)
        )

    # --- Upcoming Effectiveness ---
    pending = data.get("pending", [])
    pending_section = ""
    if pending:
        # Group by trust, REX first
        from collections import OrderedDict
        rex_trusts = OrderedDict()
        other_trusts = OrderedDict()
        for p in pending:
            trust = p.get("trust_name", "Unknown")
            is_rex = p.get("is_rex", False)
            target = rex_trusts if is_rex else other_trusts
            if trust not in target:
                target[trust] = {"is_rex": is_rex, "funds": []}
            target[trust]["funds"].append(p)

        pending_html_parts = []
        for trust_groups, section_label in [(rex_trusts, "REX"), (other_trusts, None)]:
            for trust_name, info in trust_groups.items():
                is_rex = info["is_rex"]
                funds = info["funds"]
                trust_disp = _esc(trust_name)
                if len(trust_disp) > 30:
                    trust_disp = trust_disp[:27] + "..."
                badge = ""
                if is_rex:
                    badge = (
                        f' <span style="background:{_BLUE};color:{_WHITE};'
                        f'padding:1px 5px;border-radius:3px;font-size:8px;'
                        f'font-weight:700;vertical-align:middle;">REX</span>'
                    )
                trust_header = (
                    f'<tr><td colspan="2" style="padding:6px 8px 2px;font-size:12px;'
                    f'font-weight:700;color:{_NAVY};">{trust_disp}{badge}</td></tr>'
                )
                pending_html_parts.append(trust_header)
                for fund in funds:
                    name = _esc(fund.get("fund_name", ""))
                    if len(name) > 45:
                        name = name[:42] + "..."
                    eff_date = _esc(fund.get("effective_date", ""))
                    pending_html_parts.append(
                        f'<tr>'
                        f'<td style="padding:2px 8px 2px 20px;border-bottom:1px solid {_BORDER};'
                        f'font-size:11px;">{name}</td>'
                        f'<td style="padding:2px 8px;border-bottom:1px solid {_BORDER};'
                        f'font-size:10px;text-align:right;color:{_ORANGE};font-weight:600;">{eff_date}</td>'
                        f'</tr>'
                    )

        pending_section = f"""
<tr><td style="padding:15px 30px 10px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_ORANGE};">
    Upcoming Effectiveness <span style="font-weight:400;color:{_GRAY};font-size:11px;">({len(pending)})</span>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    {''.join(pending_html_parts)}
  </table>
</td></tr>"""

    # --- Bloomberg-backed sections (graceful skip if unavailable) ---
    etp_overview_section = ""
    market_pulse_section = ""
    daily_movers_section = ""
    landscape_section = ""
    if snapshot:
        # Market Pulse (SPY, QQQ, BTC, industry flow)
        pulse = snapshot.get("market_pulse", {})
        if pulse:
            market_pulse_section = _render_market_pulse(pulse)
        # ETP Market Overview (market-wide KPIs only; REX metrics live on the REX dashboard)
        ind = pulse.get("_industry", {}) if pulse else {}
        etp_overview_section = (
            f'<tr><td style="padding:15px 30px 5px;">'
            f'<div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;'
            f'padding-bottom:6px;border-bottom:2px solid {_NAVY};">ETP Market Overview</div>'
            f'</td></tr>'
            + _dual_kpi_box(
                market_row=[
                    ("Active ETPs", f'{ind.get("count", 0):,}'),
                    ("Market AUM", ind.get("aum_fmt", "--")),
                    ("1D Flow", ind.get("flow_1d_fmt", "--"), ind.get("flow_1d_positive", True)),
                    ("1W Flow", ind.get("flow_1w_fmt", "--"), ind.get("flow_1w_positive", True)),
                ],
                rex_row=None,
            )
        )
        # Market Landscape (5 categories) — shown immediately after ETP Market Overview
        ls = snapshot.get("landscape", [])
        if ls:
            landscape_section = _render_landscape_compact(ls)

    # --- Dashboard CTA ---
    cta_section = _dashboard_cta(dash_link) if dash_link else ""

    # --- Footer ---
    footer = f"""
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <div style="font-size:11px;color:{_GRAY};text-align:center;">
    {_title} | {_data_date_str}
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    Data sourced from SEC EDGAR &amp; Bloomberg | To unsubscribe, contact relasmar@rexfin.com
  </div>
  <div style="font-size:9px;color:{_GRAY};text-align:center;margin-top:3px;font-style:italic;">
    Note: ETN data reflects proprietary share/price data where available. Bloomberg-reported ETN figures may differ.
  </div>
</td></tr>"""

    # --- Key Highlights ---
    highlights_html = _daily_highlights_box(_daily_highlights(data))

    # --- Assemble (executive order) ---
    # 1. Market Pulse (SPY, QQQ, BTC, ETP 1D Flow)
    # 2. ETP Market Overview (market-wide KPIs only)
    # 3. Market Landscape (5-category AUM/flow matrix — sits near the overview)
    # 4. New Fund Launches (newly-listed products from the last 7 days)
    # 5. Filing Activity (New vs Updated fund filings, today only)
    # 6. Upcoming Effectiveness, CTA, Footer
    body = (
        header + msg_html + highlights_html
        + market_pulse_section + etp_overview_section
        + landscape_section
        + launches_section
        + filings_section
        + pending_section
        + cta_section + footer
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_title} - {_data_date_str}</title>
</head>
<body style="margin:0;padding:0;background:{_LIGHT};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:{_NAVY};line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LIGHT};">
<tr><td align="center" style="padding:20px 10px;">
<table width="640" cellpadding="0" cellspacing="0" border="0"
       style="background:{_WHITE};border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:640px;table-layout:fixed;">
{body}
</table>
</td></tr></table>
</body></html>"""


def _gather_daily_data(db_session, since_date: str | None = None,
                       edition: str = "daily") -> dict:
    """Query DB + Bloomberg master data for daily brief.

    edition: "daily"/"morning" looks back 24h, "evening" looks at today only.
    """
    from sqlalchemy import distinct, func, select
    from datetime import date as date_type
    from webapp.models import Trust, FundStatus, Filing, FundExtraction

    today = datetime.now()
    if not since_date:
        # Daily report covers TODAY's filings only — matches user expectation that
        # "today's report" means filings dated today, not a rolling 24h window.
        since_date = today.strftime("%Y-%m-%d")
    since_dt = date_type.fromisoformat(since_date)
    yesterday = date_type.today() - timedelta(days=1)

    # --- New launches: Bloomberg inception_date in last 7 days ---
    launches = []
    try:
        from webapp.services.market_data import data_available, get_master_data
        if data_available(db_session):
            master = get_master_data(db_session, etn_overrides=True)
            ft_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
            if ft_col:
                master = master[master[ft_col].isin(["ETF", "ETN"])]
            if "market_status" in master.columns:
                master = master[master["market_status"].isin(["ACTV", "Active"])]
            if "inception_date" in master.columns and "ticker_clean" in master.columns:
                master = master.drop_duplicates(subset=["ticker_clean"], keep="first")
                inception = pd.to_datetime(master["inception_date"], errors="coerce")
                today_ts = pd.Timestamp.today().normalize()
                cutoff = today_ts - pd.Timedelta(days=7)
                recent = master[(inception >= cutoff) & (inception <= today_ts)].copy()
                recent["_inception"] = inception[recent.index]
                recent = recent.sort_values("_inception", ascending=False)
                is_rex_col = "is_rex" if "is_rex" in recent.columns else None
                for _, row in recent.iterrows():
                    ticker = str(row.get("ticker_clean", ""))
                    name = str(row.get("fund_name", row.get("name", "")))
                    issuer = str(row.get("issuer_display", row.get("issuer", "")))
                    inc_date = row["_inception"].strftime("%Y-%m-%d") if pd.notna(row["_inception"]) else ""
                    is_rex = bool(row.get("is_rex", False)) if is_rex_col else False
                    aum_col = next((c for c in ["t_w4.aum", "aum"] if c in row.index), None)
                    aum_val = float(row.get(aum_col, 0)) if aum_col else 0.0
                    if aum_val != aum_val:  # NaN check
                        aum_val = 0.0
                    launches.append({
                        "ticker": ticker if ticker else "--",
                        "fund_name": name,
                        "trust_name": issuer,
                        "effective_date": inc_date,
                        "is_rex": is_rex,
                        "aum": aum_val,
                    })
    except Exception:
        pass

    # Bloomberg-only: no DB fallback (SEC effective dates are not launch dates)

    # --- Subquery: trusts that have at least one ETF fund ---
    _etf_trust_ids = (
        select(FundStatus.trust_id)
        .where(FundStatus.fund_name.ilike("%ETF%"))
        .group_by(FundStatus.trust_id)
    ).subquery()

    # --- New filings: fund-level detail with relevance classification ---
    # Scoped to trusts that actually have ETF products
    filing_rows = db_session.execute(
        select(
            Trust.name.label("trust_name"), Trust.is_rex,
            Filing.id.label("filing_id"),
            Filing.form, Filing.filing_date,
            FundExtraction.series_id,
            FundExtraction.series_name,
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= since_dt)
        .where(Filing.form.ilike("485%"))
        .where(Trust.id.in_(select(_etf_trust_ids.c.trust_id)))
        .order_by(Trust.is_rex.desc(), Filing.filing_date.desc())
    ).all()

    # Build the set of series_ids that have ANY prior 485-series filing before
    # today. Anything in today's filings NOT in this set is a brand-new fund.
    todays_series_ids = {r.series_id for r in filing_rows if r.series_id}
    prior_series_ids: set[str] = set()
    if todays_series_ids:
        prior_rows = db_session.execute(
            select(FundExtraction.series_id)
            .join(Filing, Filing.id == FundExtraction.filing_id)
            .where(Filing.filing_date < since_dt)
            .where(FundExtraction.series_id.in_(todays_series_ids))
            .distinct()
        ).all()
        prior_series_ids = {r.series_id for r in prior_rows if r.series_id}

    # Group by (trust, is_new) so a trust that filed both a new fund AND an
    # update for an existing fund shows up in both sections with the right funds.
    from collections import defaultdict
    _fg_map: dict[tuple, dict] = {}
    for r in filing_rows:
        sid = r.series_id
        sname = (r.series_name or "").strip()
        # An extraction with no series_id is treated as an update (can't prove
        # it's new). Extractions with no series_name are skipped entirely.
        is_new = bool(sid) and sid not in prior_series_ids
        key = (r.trust_name or "", is_new)
        if key not in _fg_map:
            _fg_map[key] = {
                "trust_name": r.trust_name or "",
                "forms": set(),
                "filing_date": str(r.filing_date) if r.filing_date else "",
                "is_rex": r.is_rex,
                "is_new": is_new,
                "funds": [],
            }
        if r.form:
            _fg_map[key]["forms"].add(r.form)
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
        if not unique_funds:
            continue

        categories: dict[str, list[str]] = defaultdict(list)
        for f in unique_funds:
            categories[_classify_fund(f)].append(f)

        relevant = categories.get("leveraged", []) + categories.get("income", []) + categories.get("crypto", [])
        other_funds = categories.get("other", [])
        other_count = len(other_funds)
        cat_counts = {c: len(names) for c, names in categories.items() if names and c != "other"}

        # Sort score: NEW funds rank highest, then REX, then relevance count
        sort_score = (
            (2000 if g["is_new"] else 0)
            + (1000 if g["is_rex"] else 0)
            + len(relevant)
        )

        filing_groups.append({
            "trust_name": g["trust_name"],
            "form": ", ".join(sorted(g["forms"])) if isinstance(g.get("forms"), set) else g.get("form", ""),
            "filing_date": g["filing_date"],
            "is_rex": g["is_rex"],
            "is_new": g["is_new"],
            "total_funds": len(unique_funds),
            "relevant_funds": relevant,
            "relevant_overflow": 0,
            "other_funds": other_funds,
            "other_count": other_count,
            "categories": cat_counts,
            "_sort": sort_score,
        })

    filing_groups.sort(key=lambda x: x["_sort"], reverse=True)

    # --- Upcoming launches: PENDING with future expected date ---
    # Scoped to trusts that actually have ETF products
    pending_rows = db_session.execute(
        select(
            FundStatus.fund_name, FundStatus.effective_date,
            Trust.name.label("trust_name"), Trust.is_rex,
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status == "PENDING")
        .where(FundStatus.effective_date.isnot(None))
        .where(FundStatus.effective_date >= date_type.today())
        .where(Trust.id.in_(select(_etf_trust_ids.c.trust_id)))
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

    # --- KPI counts (scoped to trusts with ETF products) ---
    rex_aum = 0
    try:
        from webapp.services.market_data import data_available, get_master_data
        if data_available(db_session):
            _master_kpi = get_master_data(db_session, etn_overrides=True)
            _aum_col = "t_w4.aum" if "t_w4.aum" in _master_kpi.columns else "aum"
            if "is_rex" in _master_kpi.columns and _aum_col in _master_kpi.columns:
                _rex = _master_kpi[_master_kpi["is_rex"] == True]
                _mkt = next((c for c in _rex.columns if c.lower() == "market_status"), None)
                if _mkt:
                    _rex = _rex[_rex[_mkt] == "ACTV"]
                # Only count ETPs (ETF + ETN), exclude non-ETP Osprey products
                _ft = next((c for c in _rex.columns if c.lower() == "fund_type"), None)
                if _ft:
                    _rex = _rex[_rex[_ft].isin(["ETF", "ETN"])]
                rex_aum = _rex[_aum_col].sum()
    except Exception:
        pass

    newly_effective_1d = db_session.execute(
        select(func.count(FundStatus.id))
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status == "EFFECTIVE")
        .where(FundStatus.effective_date >= yesterday)
        .where(FundStatus.effective_date <= date_type.today())
        .where(Trust.id.in_(select(_etf_trust_ids.c.trust_id)))
    ).scalar() or 0

    total_pending = db_session.execute(
        select(func.count(FundStatus.id))
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status == "PENDING")
        .where(Trust.id.in_(select(_etf_trust_ids.c.trust_id)))
    ).scalar() or 0

    # Market snapshot (Bloomberg data — None if unavailable)
    market_snapshot = _gather_market_snapshot(db=db_session)

    return {
        "launches": launches,
        "filing_groups": filing_groups,
        "pending": pending,
        "rex_aum": rex_aum,
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


def _audit_send(subject: str, recipients: list[str], allowed: bool):
    """Log every send attempt to data/.send_audit.json."""
    import json as _json
    audit_path = Path(__file__).parent.parent / "data" / ".send_audit.json"
    try:
        entries = _json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else []
    except Exception:
        entries = []
    entries.append({
        "timestamp": datetime.now().isoformat(),
        "subject": subject,
        "recipient_count": len(recipients),
        "allowed": allowed,
    })
    # Keep last 200 entries
    entries = entries[-200:]
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(_json.dumps(entries, indent=2), encoding="utf-8")


_last_alert_time: float = 0
_ALERT_COOLDOWN = 3600  # 1 hour between alerts


def send_critical_alert(subject: str, message: str) -> bool:
    """Send a critical alert email to relasmar@rexfin.com.

    Bypasses the send gate and recipient files — alerts always go through.
    Uses Graph API directly (not SMTP). Rate limited to 1 per hour.
    """
    import time as _time
    global _last_alert_time
    now = _time.time()
    if _last_alert_time and (now - _last_alert_time) < _ALERT_COOLDOWN:
        log.warning("Alert throttled (last sent %.0f sec ago): %s", now - _last_alert_time, subject)
        return False
    _last_alert_time = now
    try:
        from webapp.services.graph_email import _load_env, _get_access_token, GRAPH_SEND_URL
        import requests as _req

        cfg = _load_env()
        if not all([cfg["tenant_id"], cfg["client_id"], cfg["client_secret"], cfg["sender"]]):
            log.error("Cannot send alert: Azure credentials not configured")
            return False

        token = _get_access_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
        if not token:
            log.error("Cannot send alert: token acquisition failed")
            return False

        html_body = f"""
        <div style="font-family:sans-serif; padding:20px;">
            <h2 style="color:#e74c3c; margin:0 0 12px;">REX FinHub Alert</h2>
            <p style="font-size:14px; color:#1a1a2e;">{message}</p>
            <hr style="border:none; border-top:1px solid #dee2e6; margin:16px 0;">
            <p style="font-size:11px; color:#636e72;">
                This is an automated alert from the REX FinHub pipeline.
                Check the <a href="https://rex-etp-tracker.onrender.com/admin/">Operations Center</a> for details.
            </p>
        </div>
        """

        url = GRAPH_SEND_URL.format(sender=cfg["sender"])
        payload = {
            "message": {
                "subject": f"[ALERT] {subject}",
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": "relasmar@rexfin.com"}}],
            },
            "saveToSentItems": "false",
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = _req.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 202:
            log.info("Critical alert sent: %s", subject)
            return True
        else:
            log.error("Alert send failed [%d]: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        log.error("Alert send error: %s", e)
        return False


def _send_html_digest(html_body: str, recipients: list[str],
                      edition: str = "daily",
                      subject_override: str = "",
                      images: list[tuple[str, bytes, str]] | None = None,
                      bypass_gate: bool = False) -> bool:
    """Send pre-built HTML digest via Azure Graph or SMTP.

    Args:
        images: Optional list of (content_id, png_bytes, filename) for inline CID images.
    """
    # --- SEND GATE (single chokepoint for ALL email in the codebase) ---
    # config/.send_enabled must exist and contain "true" or nothing sends.
    # bypass_gate=True for test sends (admin test to relasmar@rexfin.com only).
    _gate_file = Path(__file__).parent.parent / "config" / ".send_enabled"
    _gate_open = bypass_gate or (_gate_file.exists() and _gate_file.read_text().strip().lower() == "true")
    _subj_preview = subject_override or f"REX {edition}"
    _audit_send(_subj_preview, recipients, allowed=_gate_open)
    if not _gate_open:
        log.warning("SEND BLOCKED: config/.send_enabled is not 'true'. Subject: %s, Recipients: %d",
                     _subj_preview, len(recipients))
        return False
    # --- END SEND GATE ---

    if subject_override:
        subject = subject_override
    else:
        _labels = {"daily": "Daily ETP Report", "morning": "Morning Brief", "evening": "Daily ETP Report"}
        _label = _labels.get(edition, "Daily ETP Report")
        subject = f"REX {_label}: {datetime.now().strftime('%m/%d/%Y')}"

    # Azure Graph API only — no SMTP fallback (SMTP uses personal email)
    try:
        from webapp.services.graph_email import is_configured, send_email
        if is_configured():
            if send_email(subject=subject, html_body=html_body,
                          recipients=recipients, images=images):
                return True
            else:
                log.error("Graph API send failed for: %s", subject)
                return False
        else:
            log.error("Graph API not configured. SMTP fallback disabled. Email not sent: %s", subject)
            return False
    except ImportError:
        log.error("graph_email module not available. Email not sent: %s", subject)
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


def _render_morning_brief_html(data: dict, dashboard_url: str = "") -> str:
    """Render the executive morning brief HTML from pre-gathered data.

    Uses a teal accent (#00897B) to visually distinguish from the navy daily brief.
    Compact, phone-friendly layout for executive consumption.
    """
    today = datetime.now()
    dash_link = _esc(dashboard_url) if dashboard_url else ""

    _TEAL = "#00897B"
    _title = "REX Morning Brief"

    # --- Header ---
    header = f"""
<tr><td style="background:{_TEAL};padding:24px 30px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="color:{_WHITE};font-size:22px;font-weight:700;">{_title}</td>
    <td align="right" style="color:rgba(255,255,255,0.7);font-size:13px;">{today.strftime('%A, %B %d, %Y')}</td>
  </tr></table>
</td></tr>"""

    # --- Section 1: REX AUM Snapshot (from Bloomberg) ---
    market_scorecard = ""
    snapshot = data.get("market_snapshot")
    if snapshot:
        kpis = snapshot["kpis"]
        _cell = f"padding:12px 6px;background:{_LIGHT};border-radius:8px;text-align:center;"
        _val = f"font-size:20px;font-weight:700;color:{_NAVY};"
        _lbl = f"font-size:9px;color:{_GRAY};text-transform:uppercase;letter-spacing:0.5px;"

        flow_1d_color = _GREEN if kpis["flow_1d_positive"] else _RED
        flow_1w_color = _GREEN if kpis["flow_1w_positive"] else _RED

        market_scorecard = f"""
<tr><td style="padding:15px 30px 5px;">
  <div style="font-size:16px;font-weight:700;color:{_NAVY};margin:0 0 8px 0;
    padding-bottom:6px;border-bottom:2px solid {_TEAL};">
    REX AUM Snapshot
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="23%" style="{_cell}">
        <div style="{_val}">{_esc(kpis['aum'])}</div>
        <div style="{_lbl}">Total AUM</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}color:{flow_1d_color};">{_esc(kpis['flow_1d_fmt'])}</div>
        <div style="{_lbl}">1D Change</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}color:{flow_1w_color};">{_esc(kpis['flow_1w_fmt'])}</div>
        <div style="{_lbl}">1W Change</div>
      </td>
      <td width="2%"></td>
      <td width="23%" style="{_cell}">
        <div style="{_val}">{kpis['products']}</div>
        <div style="{_lbl}">Products</div>
      </td>
    </tr>
  </table>
</td></tr>"""

    # --- Section 2: Top 3 Flow Movers (inflows + outflows) ---
    top_movers_section = ""
    if snapshot:
        movers = snapshot.get("top_movers", {})
        inflows = movers.get("inflows", [])[:3]
        outflows = movers.get("outflows", [])[:3]
        if inflows or outflows:
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
            top_movers_section = f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Top Flow Movers
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Ticker</td>
      <td style="{_col}">Fund</td>
      <td style="{_col}text-align:right;">1W Flow</td>
      <td style="{_col}text-align:right;">1W Return</td>
    </tr>
    {''.join(rows)}
  </table>
</td></tr>"""

    # --- Section 3: New Competitor Filings (last 24h) ---
    filing_groups = data.get("filing_groups", [])
    if filing_groups:
        filing_items = []
        for fg in filing_groups[:6]:
            trust = _esc(fg.get("trust_name", ""))
            if len(trust) > 35:
                trust = trust[:32] + "..."
            form = _esc(fg.get("form", ""))
            is_rex = fg.get("is_rex", False)
            total = fg.get("total_funds", 0)
            cats = fg.get("categories", {})

            trust_label = trust
            if is_rex:
                trust_label = (
                    f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 6px;border-radius:3px;font-size:9px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )

            cat_tags = ""
            for cat, cnt in sorted(cats.items(), key=lambda x: x[1], reverse=True):
                cat_color = {"leveraged": "#e74c3c", "income": "#27ae60", "crypto": "#f39c12"}.get(cat, _GRAY)
                cat_tags += (
                    f' <span style="display:inline-block;padding:1px 5px;border-radius:3px;'
                    f'font-size:9px;color:{_WHITE};background:{cat_color};'
                    f'margin-left:2px;">{cnt} {cat}</span>'
                )

            _row_style = (
                f"padding:6px 10px;border-bottom:1px solid {_BORDER};"
                f"font-size:12px;color:{_NAVY};"
            )
            if is_rex:
                _row_style += f"background:{_REX_ROW_BG};"

            filing_items.append(
                f'<tr><td style="{_row_style}">'
                f'{trust_label} '
                f'<span style="color:{_GRAY};font-size:10px;">{form}</span> '
                f'<span style="color:{_GRAY};font-size:11px;">-- {total} funds</span>'
                f'{cat_tags}'
                f'</td></tr>'
            )

        more_html = ""
        if len(filing_groups) > 6:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {len(filing_groups) - 6} more on dashboard</div>'
            )
        filings_section = f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    New Competitor Filings (24h)
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    {''.join(filing_items)}
  </table>
  {more_html}
</td></tr>"""
    else:
        filings_section = f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    New Competitor Filings (24h)
  </div>
  <div style="padding:10px;background:{_LIGHT};border-radius:6px;
    font-size:12px;color:{_GRAY};text-align:center;">
    No new 485 filings in the last 24 hours.
  </div>
</td></tr>"""

    # --- Section 4: Market Share Deltas ---
    landscape_section = ""
    if snapshot:
        landscape_section = _render_landscape_compact(snapshot.get("landscape", []))

    # --- Section 5: Calendar (upcoming effective dates this week) ---
    calendar_items = data.get("calendar", [])
    calendar_section = ""
    if calendar_items:
        _col = (
            f"padding:4px 8px;font-size:9px;color:{_GRAY};text-transform:uppercase;"
            f"border-bottom:1px solid {_BORDER};"
        )
        cal_rows = []
        for c in calendar_items[:8]:
            fund = _esc(c.get("fund_name", ""))
            if len(fund) > 35:
                fund = fund[:32] + "..."
            trust = _esc(c.get("trust_name", ""))
            if len(trust) > 25:
                trust = trust[:22] + "..."
            eff = _esc(c.get("effective_date", ""))
            is_rex = c.get("is_rex", False)
            trust_html = trust
            if is_rex:
                trust_html = (
                    f'{trust} <span style="background:{_BLUE};color:{_WHITE};'
                    f'padding:1px 5px;border-radius:3px;font-size:8px;'
                    f'font-weight:700;vertical-align:middle;">REX</span>'
                )
            cal_rows.append(
                f'<tr>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:11px;">{fund}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:10px;color:{_GRAY};">{trust_html}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid {_BORDER};font-size:10px;text-align:right;color:{_ORANGE};font-weight:600;">{eff}</td>'
                f'</tr>'
            )
        more_html = ""
        if len(calendar_items) > 8:
            more_html = (
                f'<div style="font-size:10px;color:{_GRAY};margin-top:4px;">'
                f'+ {len(calendar_items) - 8} more on dashboard</div>'
            )
        calendar_section = f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Effective This Week
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <tr>
      <td style="{_col}">Fund</td>
      <td style="{_col}">Trust</td>
      <td style="{_col}text-align:right;">Effective Date</td>
    </tr>
    {''.join(cal_rows)}
  </table>
  {more_html}
</td></tr>"""

    # --- Section 6: Triggered Alerts Summary ---
    alert_counts = data.get("alert_counts", {})
    new_filings_count = alert_counts.get("new_filings", 0)
    pending_count = alert_counts.get("pending", 0)
    newly_effective_count = alert_counts.get("newly_effective", 0)

    alerts_section = f"""
<tr><td style="padding:10px 30px 5px;">
  <div style="font-size:14px;font-weight:600;color:{_NAVY};margin:0 0 6px 0;">
    Alerts Summary
  </div>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="31%" style="padding:10px 6px;background:{_LIGHT};border-radius:8px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:{_BLUE};">{new_filings_count}</div>
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">New Filings</div>
      </td>
      <td width="3%"></td>
      <td width="31%" style="padding:10px 6px;background:{_LIGHT};border-radius:8px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:{_ORANGE};">{pending_count}</div>
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Pending</div>
      </td>
      <td width="3%"></td>
      <td width="31%" style="padding:10px 6px;background:{_LIGHT};border-radius:8px;text-align:center;">
        <div style="font-size:18px;font-weight:700;color:{_GREEN};">{newly_effective_count}</div>
        <div style="font-size:9px;color:{_GRAY};text-transform:uppercase;">Newly Effective</div>
      </td>
    </tr>
  </table>
</td></tr>"""

    # --- Dashboard CTA ---
    cta_section = ""
    if dash_link:
        cta_section = (
            f'<tr><td style="padding:20px 30px;" align="center">'
            f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="background:{_TEAL};border-radius:8px;padding:14px 36px;">'
            f'<a href="{dash_link}" style="color:{_WHITE};text-decoration:none;font-size:15px;font-weight:700;">Open Dashboard</a>'
            f'</td></tr></table>'
            f'</td></tr>'
        )

    # --- Footer ---
    _data_source = "Data sourced from SEC EDGAR"
    if snapshot:
        _data_source += " &amp; Bloomberg"
    footer = f"""
<tr><td style="padding:16px 30px;border-top:1px solid {_BORDER};">
  <div style="font-size:11px;color:{_GRAY};text-align:center;">
    {_title} | {today.strftime('%Y-%m-%d %H:%M')}
  </div>
  <div style="font-size:10px;color:{_GRAY};text-align:center;margin-top:4px;">
    {_data_source} | To unsubscribe, contact relasmar@rexfin.com
  </div>
</td></tr>"""

    # --- Assemble ---
    body = (header + market_scorecard + top_movers_section + filings_section
            + landscape_section + calendar_section + alerts_section
            + cta_section + footer)

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
<table width="640" cellpadding="0" cellspacing="0" border="0"
       style="background:{_WHITE};border-radius:8px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:640px;table-layout:fixed;">
{body}
</table>
</td></tr></table>
</body></html>"""


def _gather_morning_brief_data(db_session) -> dict:
    """Gather data for the morning brief: daily data + calendar for the week."""
    from sqlalchemy import select
    from datetime import date as date_type
    from webapp.models import Trust, FundStatus

    # Reuse daily data gathering (filings, pending, market snapshot)
    data = _gather_daily_data(db_session, edition="daily")

    # Calendar: PENDING funds with effective dates this week (Mon-Fri)
    today = date_type.today()
    # Start of week (Monday)
    week_start = today - timedelta(days=today.weekday())
    # End of week (Sunday)
    week_end = week_start + timedelta(days=6)

    calendar_rows = db_session.execute(
        select(
            FundStatus.fund_name, FundStatus.effective_date,
            Trust.name.label("trust_name"), Trust.is_rex,
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status.in_(["PENDING", "EFFECTIVE"]))
        .where(FundStatus.effective_date >= week_start)
        .where(FundStatus.effective_date <= week_end)
        .order_by(Trust.is_rex.desc(), FundStatus.effective_date.asc())
    ).all()

    calendar = []
    for r in calendar_rows:
        calendar.append({
            "fund_name": r.fund_name or "",
            "trust_name": r.trust_name or "",
            "effective_date": str(r.effective_date) if r.effective_date else "",
            "is_rex": r.is_rex,
        })

    data["calendar"] = calendar

    # Alert counts for summary
    data["alert_counts"] = {
        "new_filings": len(data.get("filing_groups", [])),
        "pending": data.get("total_pending", 0),
        "newly_effective": data.get("newly_effective_1d", 0),
    }

    return data


def build_morning_brief_html(db_session, dashboard_url: str = "") -> str:
    """Build morning brief HTML (for preview/testing)."""
    data = _gather_morning_brief_data(db_session)
    return _render_morning_brief_html(data, dashboard_url)


def send_morning_brief(db_session, dashboard_url: str = "") -> bool:
    """Build and send executive morning brief."""
    recipients = _load_recipients()
    private = _load_private_recipients()
    if not recipients and not private:
        return False

    html_body = build_morning_brief_html(db_session, dashboard_url)

    ok = True
    if recipients:
        ok = _send_html_digest(html_body, recipients, edition="morning")
    if private:
        _send_html_digest(html_body, private, edition="morning")
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
