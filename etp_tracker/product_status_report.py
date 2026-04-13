"""Monday Product Status Report — REX Product Pipeline for ETFUpdates.

Separate weekly Monday email showing where each REX product stands in its
lifecycle. Data sourced from the rex_products table (imported from the
Excel Master Product Development Tracker).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"

# Color palette
_NAVY = "#1a1a2e"
_GREEN = "#059669"
_RED = "#dc2626"
_ORANGE = "#d97706"
_BLUE = "#2563eb"
_GRAY = "#64748b"
_LIGHT = "#f8fafc"
_BORDER = "#e5e7eb"
_WHITE = "#ffffff"

# Suite display order + colors
SUITE_ORDER = [
    "T-REX",
    "Premium Income",
    "Growth & Income",
    "IncomeMax",
    "Crypto",
    "Thematic",
    "Autocallable",
    "T-Bill",
]

SUITE_COLORS = {
    "T-REX": "#1a1a2e",
    "Premium Income": "#2563eb",
    "Growth & Income": "#059669",
    "IncomeMax": "#d97706",
    "Crypto": "#8b5cf6",
    "Thematic": "#0891b2",
    "Autocallable": "#dc2626",
    "T-Bill": "#64748b",
}


def build_product_status_report(db: Session) -> str:
    """Build the Monday product status email HTML.

    Returns:
        Complete HTML email string
    """
    from webapp.models import RexProduct

    total = db.query(RexProduct).count()
    if total == 0:
        return _render_empty()

    # Pipeline counts by status
    status_counts = dict(
        db.query(RexProduct.status, func.count(RexProduct.id))
        .group_by(RexProduct.status)
        .all()
    )

    # Pipeline by suite + status
    suite_status = defaultdict(lambda: defaultdict(int))
    for suite, status, count in (
        db.query(RexProduct.product_suite, RexProduct.status, func.count(RexProduct.id))
        .group_by(RexProduct.product_suite, RexProduct.status)
        .all()
    ):
        suite_status[suite][status] = count

    # Products changing status this week (using updated_at)
    week_ago = datetime.now() - timedelta(days=7)
    recent_changes = (
        db.query(RexProduct)
        .filter(RexProduct.updated_at >= week_ago)
        .order_by(RexProduct.updated_at.desc())
        .limit(15)
        .all()
    )

    # Awaiting effectiveness (Filed status with estimated_effective_date in future)
    today = date.today()
    awaiting = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.isnot(None))
        .filter(RexProduct.estimated_effective_date >= today)
        .order_by(RexProduct.estimated_effective_date.asc())
        .limit(30)
        .all()
    )

    # Recently listed (last 30 days)
    thirty_days_ago = today - timedelta(days=30)
    recently_listed = (
        db.query(RexProduct)
        .filter(RexProduct.status == "Listed")
        .filter(RexProduct.official_listed_date.isnot(None))
        .filter(RexProduct.official_listed_date >= thirty_days_ago)
        .order_by(RexProduct.official_listed_date.desc())
        .all()
    )

    # Listed products by suite (for the pipeline section)
    listed_by_suite = defaultdict(list)
    for p in (
        db.query(RexProduct)
        .filter(RexProduct.status == "Listed")
        .order_by(RexProduct.product_suite, RexProduct.name)
        .all()
    ):
        listed_by_suite[p.product_suite].append(p)

    # Filed/pending by suite
    filed_by_suite = defaultdict(int)
    for suite, count in (
        db.query(RexProduct.product_suite, func.count(RexProduct.id))
        .filter(RexProduct.status == "Filed")
        .group_by(RexProduct.product_suite)
        .all()
    ):
        filed_by_suite[suite] = count

    html = _render_report(
        total=total,
        status_counts=status_counts,
        suite_status=suite_status,
        recent_changes=recent_changes,
        awaiting=awaiting,
        recently_listed=recently_listed,
        listed_by_suite=listed_by_suite,
        filed_by_suite=filed_by_suite,
    )
    return html


def _render_report(*, total, status_counts, suite_status, recent_changes,
                   awaiting, recently_listed, listed_by_suite, filed_by_suite) -> str:
    """Render the full product status report HTML."""
    today = date.today()
    # Monday of current week
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    sections = []

    # --- Header ---
    sections.append(f"""
    <div style="background:{_NAVY}; color:{_WHITE}; padding:24px 28px; border-radius:8px 8px 0 0;">
      <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.12em; opacity:0.7;">REX Financial — Product Pipeline</div>
      <div style="font-size:24px; font-weight:800; margin:6px 0;">REX Product Pipeline</div>
      <div style="font-size:13px; opacity:0.8;">Week of {week_start.strftime('%B %d, %Y')}</div>
    </div>
    """)

    # --- KPI Banner ---
    listed = status_counts.get("Listed", 0)
    filed = status_counts.get("Filed", 0)
    awaiting_count = status_counts.get("Awaiting Effective", 0)
    research = status_counts.get("Research", 0) + status_counts.get("Target List", 0)
    delisted = status_counts.get("Delisted", 0)

    sections.append(f"""
    <div style="padding:20px 28px 4px; display:flex; gap:12px; flex-wrap:wrap;">
      <div style="flex:1; min-width:120px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_GREEN}; border-radius:6px; padding:14px 16px; text-align:center;">
        <div style="font-size:30px; font-weight:800; color:{_GREEN}; line-height:1;">{listed}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Listed</div>
      </div>
      <div style="flex:1; min-width:120px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_BLUE}; border-radius:6px; padding:14px 16px; text-align:center;">
        <div style="font-size:30px; font-weight:800; color:{_BLUE}; line-height:1;">{filed}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Filed</div>
      </div>
      <div style="flex:1; min-width:120px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_ORANGE}; border-radius:6px; padding:14px 16px; text-align:center;">
        <div style="font-size:30px; font-weight:800; color:{_ORANGE}; line-height:1;">{research}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">In Research</div>
      </div>
      <div style="flex:1; min-width:120px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_NAVY}; border-radius:6px; padding:14px 16px; text-align:center;">
        <div style="font-size:30px; font-weight:800; color:{_NAVY}; line-height:1;">{total}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em; margin-top:4px;">Total Products</div>
      </div>
    </div>
    """)

    # --- Recently Listed ---
    if recently_listed:
        rows = []
        for p in recently_listed[:15]:
            suite_color = SUITE_COLORS.get(p.product_suite, _GRAY)
            rows.append(f"""
            <tr>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">{p.official_listed_date.strftime('%b %d') if p.official_listed_date else '--'}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-weight:700; font-family:monospace;">{p.ticker or 'TBD'}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-size:12px;">{(p.name or '')[:50]}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">
                <span style="background:{suite_color}; color:{_WHITE}; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:600;">{p.product_suite}</span>
              </td>
            </tr>""")

        sections.append(f"""
        <div style="padding:20px 28px 8px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">RECENTLY LISTED (last 30 days)</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead><tr style="background:{_LIGHT}; font-size:10px; text-transform:uppercase; color:{_GRAY};">
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Listed</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Ticker</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Fund Name</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Suite</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """)

    # --- Awaiting Effectiveness ---
    if awaiting:
        rows = []
        for p in awaiting[:20]:
            days_left = (p.estimated_effective_date - today).days if p.estimated_effective_date else 0
            urgency = ""
            if days_left <= 7:
                urgency = f' style="background:#fef2f2;"'
            elif days_left <= 30:
                urgency = f' style="background:#fffbeb;"'
            suite_color = SUITE_COLORS.get(p.product_suite, _GRAY)
            rows.append(f"""
            <tr{urgency}>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">{p.estimated_effective_date.strftime('%b %d')}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-weight:700;">{days_left}d</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-size:12px;">{(p.name or '')[:55]}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">
                <span style="background:{suite_color}; color:{_WHITE}; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:600;">{p.product_suite}</span>
              </td>
            </tr>""")

        sections.append(f"""
        <div style="padding:16px 28px 8px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">AWAITING EFFECTIVENESS ({len(awaiting)} products)</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead><tr style="background:{_LIGHT}; font-size:10px; text-transform:uppercase; color:{_GRAY};">
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Expected</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">In</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Fund Name</th>
              <th style="padding:8px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Suite</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """)

    # --- Pipeline by Suite ---
    sections.append(f"""
    <div style="padding:16px 28px 8px;">
      <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">PIPELINE BY SUITE</div>
    """)

    for suite in SUITE_ORDER:
        if suite not in suite_status:
            continue
        stats = suite_status[suite]
        suite_total = sum(stats.values())
        listed_count = stats.get("Listed", 0)
        filed_count = stats.get("Filed", 0)
        awaiting_c = stats.get("Awaiting Effective", 0)
        delisted_c = stats.get("Delisted", 0)
        suite_color = SUITE_COLORS.get(suite, _GRAY)

        sections.append(f"""
        <div style="border:1px solid {_BORDER}; border-left:3px solid {suite_color}; border-radius:6px; padding:12px 16px; margin-bottom:8px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div style="font-size:14px; font-weight:700; color:{_NAVY};">{suite}</div>
            <div style="font-size:11px; color:{_GRAY};">Total: <b>{suite_total}</b></div>
          </div>
          <div style="display:flex; gap:16px; margin-top:6px; font-size:12px;">
            <span><span style="color:{_GRAY};">Listed:</span> <b style="color:{_GREEN};">{listed_count}</b></span>
            <span><span style="color:{_GRAY};">Filed:</span> <b style="color:{_BLUE};">{filed_count}</b></span>
            {f'<span><span style="color:{_GRAY};">Awaiting:</span> <b style="color:{_ORANGE};">{awaiting_c}</b></span>' if awaiting_c else ''}
            {f'<span><span style="color:{_GRAY};">Delisted:</span> <b style="color:{_RED};">{delisted_c}</b></span>' if delisted_c else ''}
          </div>
        </div>
        """)

    sections.append("</div>")

    # --- Footer ---
    sections.append(f"""
    <div style="background:{_LIGHT}; padding:16px 28px; border-radius:0 0 8px 8px; border-top:1px solid {_BORDER};">
      <div style="font-size:11px; color:{_GRAY};">
        <a href="{DASHBOARD_URL}/dashboard" style="color:{_BLUE};">Dashboard</a> |
        <a href="{DASHBOARD_URL}/filings/" style="color:{_BLUE};">Filing Explorer</a>
      </div>
      <div style="font-size:10px; color:#94a3b8; margin-top:6px;">
        Source: REX Master Product Development Tracker. Generated {datetime.now().strftime('%Y-%m-%d %H:%M ET')}.
      </div>
    </div>
    """)

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>REX Product Pipeline</title></head>
<body style="margin:0; padding:20px; background:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:760px; margin:0 auto; background:{_WHITE}; border-radius:8px; border:1px solid {_BORDER}; overflow:hidden;">
{body}
</div></body></html>"""


def _render_empty() -> str:
    """Render an empty-state message when no products are in DB."""
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif; padding:40px; background:{_LIGHT};">
<div style="max-width:600px; margin:0 auto; background:white; padding:24px; border-radius:8px; border-left:3px solid {_ORANGE};">
<h2 style="margin:0 0 8px; color:{_NAVY};">No Products in Database</h2>
<p style="color:#374151;">Run <code>python scripts/import_product_tracker.py</code> to populate the rex_products table.</p>
</div></body></html>"""
