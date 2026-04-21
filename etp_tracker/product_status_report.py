"""Weekly REX Product Pipeline — executive-first.

One question: what's new and what's coming next?

Sections:
  1. Top KPIs (Live incl ETNs, AUM from Bloomberg, New This Week, Launching 30d)
  2. Next Up — the immediate next filing/batch going effective
  3. New This Week — listings + new filings (always shown, empty state if none)
  4. Launching Next 30 Days — grouped by filing, not by fund

No suite-by-suite counter breakdown. Filings with multiple funds collapse to one row.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"

_MAX_WIDTH = "680px"
_NAVY = "#0f172a"
_GREEN = "#059669"
_RED = "#dc2626"
_AMBER = "#d97706"
_BLUE = "#2563eb"
_GRAY = "#64748b"
_LIGHT = "#f8fafc"
_BORDER = "#e5e7eb"
_WHITE = "#ffffff"


def build_product_status_report(db: Session) -> str:
    """Build the weekly product pipeline report."""
    from webapp.models import RexProduct

    total = db.query(RexProduct).count()
    if total == 0:
        return _render_empty_db()

    # KPIs
    listed_count = db.query(RexProduct).filter(RexProduct.status == "Listed").count()
    rex_aum = _total_rex_aum(db)

    today = date.today()
    seven_days_ago = today - timedelta(days=7)
    thirty_days_out = today + timedelta(days=30)

    # New this week
    new_listings = (
        db.query(RexProduct)
        .filter(RexProduct.status == "Listed")
        .filter(RexProduct.official_listed_date >= seven_days_ago)
        .filter(RexProduct.official_listed_date <= today)
        .order_by(RexProduct.official_listed_date.desc())
        .all()
    )

    new_filings = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.initial_filing_date >= seven_days_ago)
        .order_by(RexProduct.initial_filing_date.desc())
        .all()
    )

    # Launching next 30 days (grouped by filing)
    upcoming_grouped = _gather_upcoming_grouped(db, today, thirty_days_out)
    upcoming_count = sum(g["fund_count"] for g in upcoming_grouped)

    new_this_week_count = len(new_listings) + len(new_filings)

    return _render(
        total=total,
        listed_count=listed_count,
        rex_aum=rex_aum,
        new_this_week_count=new_this_week_count,
        upcoming_count=upcoming_count,
        new_listings=new_listings,
        new_filings_grouped=_group_filings(new_filings),
        upcoming_grouped=upcoming_grouped,
        today=today,
    )


def _total_rex_aum(db: Session) -> float:
    """Total REX AUM across all listed products from Bloomberg."""
    try:
        from webapp.services.market_data import get_master_data, data_available
        if not data_available(db):
            return 0.0
        master = get_master_data(db, etn_overrides=True)
        rex = master[master["is_rex"] == True] if "is_rex" in master.columns else None
        if rex is None or len(rex) == 0:
            return 0.0
        aum_col = "t_w4.aum" if "t_w4.aum" in rex.columns else "aum"
        return float(rex[aum_col].sum() or 0)
    except Exception:
        return 0.0


def _group_filings(products: list) -> list[dict]:
    """Group products by (trust, initial_filing_date) so one 485APOS = one row."""
    if not products:
        return []
    grouped = defaultdict(lambda: {
        "trust": "", "suite": "", "filing_date": None, "effective_date": None,
        "funds": [], "fund_count": 0, "form": "",
    })
    for p in products:
        key = (p.trust, str(p.initial_filing_date), p.latest_form or "")
        g = grouped[key]
        g["trust"] = p.trust or ""
        g["suite"] = p.product_suite or ""
        g["filing_date"] = p.initial_filing_date
        g["effective_date"] = p.estimated_effective_date
        g["form"] = p.latest_form or ""
        g["funds"].append({"name": p.name, "ticker": p.ticker or ""})
    for g in grouped.values():
        g["fund_count"] = len(g["funds"])
    return sorted(grouped.values(), key=lambda g: g["filing_date"] or date(1970, 1, 1), reverse=True)


def _gather_upcoming_grouped(db: Session, today: date, cutoff: date) -> list[dict]:
    """Upcoming effectives, grouped by filing."""
    from webapp.models import RexProduct

    products = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.isnot(None))
        .filter(RexProduct.estimated_effective_date >= today)
        .filter(RexProduct.estimated_effective_date <= cutoff)
        .order_by(RexProduct.estimated_effective_date.asc())
        .all()
    )

    grouped = defaultdict(lambda: {
        "trust": "", "suite": "", "effective_date": None, "filing_date": None,
        "funds": [], "fund_count": 0,
    })
    for p in products:
        key = (p.trust, str(p.estimated_effective_date), p.initial_filing_date.isoformat() if p.initial_filing_date else "")
        g = grouped[key]
        g["trust"] = p.trust or ""
        g["suite"] = p.product_suite or ""
        g["effective_date"] = p.estimated_effective_date
        g["filing_date"] = p.initial_filing_date
        g["funds"].append({"name": p.name, "ticker": p.ticker or ""})

    for g in grouped.values():
        g["fund_count"] = len(g["funds"])

    return sorted(grouped.values(), key=lambda g: g["effective_date"])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render(*, total, listed_count, rex_aum, new_this_week_count, upcoming_count,
            new_listings, new_filings_grouped, upcoming_grouped, today) -> str:
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    header = f"""
<div style="padding:20px 24px 16px; border-bottom:1px solid {_BORDER};">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:600;">REX Financial</div>
  <div style="font-size:22px; font-weight:700; color:{_NAVY}; margin-top:4px;">Product Pipeline</div>
  <div style="font-size:13px; color:{_GRAY}; margin-top:2px;">Week of {week_start.strftime('%B %d, %Y')}</div>
</div>"""

    # KPIs — all same width (table layout, not flex)
    aum_display = _fmt_dollars(rex_aum) if rex_aum > 0 else "—"
    kpis = f"""
<div style="padding:20px 24px 8px;">
  <table style="width:100%; border-collapse:separate; border-spacing:8px 0;">
    <tr>
      <td style="background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_GREEN}; border-radius:4px; padding:12px 14px; width:25%;">
        <div style="font-size:22px; font-weight:800; color:{_NAVY}; line-height:1;">{listed_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Live Products</div>
      </td>
      <td style="background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_BLUE}; border-radius:4px; padding:12px 14px; width:25%;">
        <div style="font-size:22px; font-weight:800; color:{_NAVY}; line-height:1;">{aum_display}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Total AUM</div>
      </td>
      <td style="background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_AMBER}; border-radius:4px; padding:12px 14px; width:25%;">
        <div style="font-size:22px; font-weight:800; color:{_NAVY}; line-height:1;">{new_this_week_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">New This Week</div>
      </td>
      <td style="background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_NAVY}; border-radius:4px; padding:12px 14px; width:25%;">
        <div style="font-size:22px; font-weight:800; color:{_NAVY}; line-height:1;">{upcoming_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Launching 30d</div>
      </td>
    </tr>
  </table>
</div>"""

    next_up_section = _render_next_up(upcoming_grouped, today)
    new_section = _render_new_this_week(new_listings, new_filings_grouped)
    upcoming_section = _render_upcoming(upcoming_grouped, today)

    body = "\n".join([header, kpis, next_up_section, new_section, upcoming_section])

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>REX Product Pipeline — {today.strftime('%b %d')}</title></head>
<body style="margin:0; padding:20px; background:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:{_MAX_WIDTH}; margin:0 auto; background:{_WHITE}; border-radius:6px; border:1px solid {_BORDER}; overflow:hidden;">
{body}
<div style="padding:12px 20px; border-top:1px solid {_BORDER}; background:#f8fafc;">
  <div style="font-size:10px; color:{_GRAY}; text-align:center; font-style:italic;">
    Bloomberg AUM and fund-flow data is delivered on a 1 business day lag by design; figures reflect T-1 values and may be over- or under-stated for very recent launches, distributions, or corporate actions.
  </div>
</div>
</div></body></html>"""


def _render_next_up(upcoming_grouped: list[dict], today: date) -> str:
    if not upcoming_grouped:
        return ""
    g = upcoming_grouped[0]
    days_left = (g["effective_date"] - today).days
    fund_count_str = f"{g['fund_count']} fund{'s' if g['fund_count'] != 1 else ''}"

    # Sample fund names
    first_two = [f['name'][:40] for f in g["funds"][:2]]
    sample = ", ".join(first_two)
    if g["fund_count"] > 2:
        sample += f" +{g['fund_count'] - 2} more"

    return f"""
<div style="padding:16px 24px 8px;">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:700; margin-bottom:8px;">Next Up</div>
  <div style="border:1px solid {_BORDER}; border-left:3px solid {_BLUE}; border-radius:4px; padding:12px 14px; background:{_LIGHT};">
    <div style="display:flex; justify-content:space-between; align-items:baseline;">
      <div style="font-size:14px; font-weight:700; color:{_NAVY};">{g['effective_date'].strftime('%A, %b %d')}</div>
      <div style="font-size:12px; color:{_GRAY};">in {days_left} day{'s' if days_left != 1 else ''}</div>
    </div>
    <div style="font-size:12px; color:#374151; margin-top:4px;">
      <b>{fund_count_str}</b> from {g['trust']} ({g['suite']})
    </div>
    <div style="font-size:11px; color:{_GRAY}; margin-top:4px;">{sample}</div>
  </div>
</div>"""


def _render_new_this_week(new_listings: list, new_filings_grouped: list[dict]) -> str:
    rows = []

    if new_listings:
        for p in new_listings[:10]:
            rows.append(f"""
  <tr>
    <td style="padding:6px 0; font-size:11px; color:{_GREEN}; font-weight:700; text-transform:uppercase; white-space:nowrap;">Listed</td>
    <td style="padding:6px 10px; font-size:12px; color:{_NAVY}; font-weight:700; font-family:monospace; white-space:nowrap;">{p.ticker or 'TBD'}</td>
    <td style="padding:6px 0; font-size:12px; color:#374151;">{(p.name or '')[:50]}</td>
    <td style="padding:6px 0; font-size:11px; color:{_GRAY}; white-space:nowrap;">{p.official_listed_date.strftime('%b %d') if p.official_listed_date else ''}</td>
  </tr>""")

    if new_filings_grouped:
        for g in new_filings_grouped[:10]:
            if g["fund_count"] == 1:
                desc = g["funds"][0]["name"][:50]
            else:
                desc = f'<b>{g["fund_count"]} funds</b> &middot; {g["trust"]} &middot; {g["suite"]}'
            rows.append(f"""
  <tr>
    <td style="padding:6px 0; font-size:11px; color:{_BLUE}; font-weight:700; text-transform:uppercase; white-space:nowrap;">Filed</td>
    <td style="padding:6px 10px; font-size:12px; color:{_NAVY}; font-weight:700; font-family:monospace; white-space:nowrap;">{g.get('form') or '485'}</td>
    <td style="padding:6px 0; font-size:12px; color:#374151;">{desc}</td>
    <td style="padding:6px 0; font-size:11px; color:{_GRAY}; white-space:nowrap;">{g['filing_date'].strftime('%b %d') if g['filing_date'] else ''}</td>
  </tr>""")

    if not rows:
        return f"""
<div style="padding:18px 24px 8px;">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:700; margin-bottom:8px;">New This Week</div>
  <div style="font-size:13px; color:{_GRAY}; font-style:italic;">No new listings or filings this week.</div>
</div>"""

    return f"""
<div style="padding:18px 24px 8px;">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:700; margin-bottom:10px;">New This Week</div>
  <table style="width:100%; border-collapse:collapse;">
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</div>"""


def _render_upcoming(upcoming_grouped: list[dict], today: date) -> str:
    if not upcoming_grouped:
        return f"""
<div style="padding:18px 24px;">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:700; margin-bottom:8px;">Launching Next 30 Days</div>
  <div style="font-size:13px; color:{_GRAY}; font-style:italic;">No upcoming launches scheduled.</div>
</div>"""

    rows = []
    for g in upcoming_grouped[:15]:
        days_left = (g["effective_date"] - today).days
        if g["fund_count"] == 1:
            desc = g["funds"][0]["name"][:55]
        else:
            desc = f'<b>{g["fund_count"]} funds</b> &middot; {g["trust"]}'

        row_bg = "#fffbeb" if days_left <= 7 else ""
        rows.append(f"""
  <tr style="background:{row_bg};">
    <td style="padding:6px 0; font-size:12px; color:{_NAVY}; font-weight:600; white-space:nowrap;">{g['effective_date'].strftime('%b %d')}</td>
    <td style="padding:6px 10px; font-size:11px; color:{_GRAY}; white-space:nowrap;">{days_left}d</td>
    <td style="padding:6px 0; font-size:12px; color:#374151;">{desc}</td>
    <td style="padding:6px 0; font-size:11px; color:{_GRAY}; white-space:nowrap; text-align:right;">{g['suite']}</td>
  </tr>""")

    remainder = max(0, len(upcoming_grouped) - 15)
    more_line = ""
    if remainder > 0:
        more_line = f"""
  <tr><td colspan="4" style="padding:8px 0; text-align:center; font-size:11px; color:{_GRAY}; font-style:italic;">+ {remainder} more filing(s) &middot; <a href="{DASHBOARD_URL}/pipeline" style="color:{_BLUE};">see full calendar</a></td></tr>"""

    return f"""
<div style="padding:18px 24px;">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:700; margin-bottom:10px;">Launching Next 30 Days</div>
  <table style="width:100%; border-collapse:collapse;">
    <thead>
      <tr style="border-bottom:1px solid {_BORDER};">
        <th style="padding:6px 0; text-align:left; font-size:10px; color:{_GRAY}; text-transform:uppercase; font-weight:600;">Effective</th>
        <th style="padding:6px 10px; text-align:left; font-size:10px; color:{_GRAY}; text-transform:uppercase; font-weight:600;">In</th>
        <th style="padding:6px 0; text-align:left; font-size:10px; color:{_GRAY}; text-transform:uppercase; font-weight:600;">Filing</th>
        <th style="padding:6px 0; text-align:right; font-size:10px; color:{_GRAY}; text-transform:uppercase; font-weight:600;">Suite</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
      {more_line}
    </tbody>
  </table>
</div>"""


def _fmt_dollars(v: float) -> str:
    av = abs(v)
    if av >= 1e9:
        return f"${v/1e9:.1f}B"
    if av >= 1e6:
        return f"${v/1e6:.0f}M"
    if av >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _render_empty_db() -> str:
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif; padding:40px; background:{_LIGHT};">
<div style="max-width:{_MAX_WIDTH}; margin:0 auto; background:white; padding:24px; border-radius:6px; border-left:3px solid {_AMBER};">
<h2 style="margin:0 0 8px; color:{_NAVY};">No Products in Database</h2>
<p style="color:#374151;">Run the product tracker import to populate the rex_products table.</p>
</div></body></html>"""
