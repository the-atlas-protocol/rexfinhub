"""Daily Filing Intelligence Brief — restricted distribution.

Data-driven report covering ALL new filings across the industry.
Answers: What did competitors file today? What underliers are contested?
Where does REX stand in each race?

V1: Pure data and tables. No AI commentary.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"

# ---- Color palette (shared with email_alerts) ----
_NAVY = "#1a1a2e"
_GREEN = "#059669"
_RED = "#dc2626"
_ORANGE = "#d97706"
_BLUE = "#2563eb"
_GRAY = "#64748b"
_LIGHT = "#f8fafc"
_BORDER = "#e5e7eb"
_WHITE = "#ffffff"


def build_intelligence_brief(db: Session, lookback_days: int = 1) -> str:
    """Build the daily filing intelligence brief HTML.

    Args:
        db: Database session
        lookback_days: How many days back to scan (1 = today + yesterday)

    Returns:
        Complete HTML email string
    """
    from webapp.models import Trust, Filing, FundExtraction, FundStatus, RexProduct

    today = date.today()
    since = today - timedelta(days=lookback_days)

    # 1. New filings (all 485 forms since cutoff)
    new_filings = _gather_new_filings(db, since)

    # 2. Competitive races (underliers with multiple filers)
    races = _gather_races(db)

    # 3. Upcoming effectiveness (next 14 days)
    upcoming = _gather_upcoming(db, today)

    # 4. REX pipeline summary
    pipeline = _gather_rex_pipeline(db)

    # 5. Strategy watch (income/autocallable/thematic new entrants)
    strategy_watch = _gather_strategy_watch(db, since)

    # Build HTML
    html = _render_brief(
        new_filings=new_filings,
        races=races,
        upcoming=upcoming,
        pipeline=pipeline,
        strategy_watch=strategy_watch,
        since=since,
        today=today,
    )
    return html


def _gather_new_filings(db: Session, since: date) -> list[dict]:
    """All 485 filings since cutoff, with fund-level detail."""
    from webapp.models import Trust, Filing, FundExtraction

    rows = db.execute(
        select(
            Trust.name.label("trust_name"),
            Trust.is_rex,
            Trust.cik,
            Filing.form,
            Filing.filing_date,
            Filing.accession_number,
            FundExtraction.series_name,
            FundExtraction.effective_date,
            FundExtraction.class_symbol,
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= since)
        .where(Filing.form.ilike("485%"))
        .order_by(Filing.filing_date.desc(), Trust.is_rex.desc())
    ).all()

    # Group by trust + filing
    grouped = defaultdict(lambda: {
        "trust_name": "", "is_rex": False, "cik": "", "form": "",
        "filing_date": "", "accession": "", "funds": [], "effective_dates": set(),
    })
    for r in rows:
        key = r.accession_number or f"{r.trust_name}_{r.filing_date}"
        g = grouped[key]
        g["trust_name"] = r.trust_name or ""
        g["is_rex"] = r.is_rex
        g["cik"] = r.cik or ""
        g["form"] = r.form or ""
        g["filing_date"] = str(r.filing_date) if r.filing_date else ""
        g["accession"] = r.accession_number or ""
        if r.series_name:
            g["funds"].append({
                "name": r.series_name,
                "ticker": r.class_symbol or "",
                "effective_date": str(r.effective_date) if r.effective_date else "",
            })
        if r.effective_date:
            g["effective_dates"].add(str(r.effective_date))

    result = []
    for g in grouped.values():
        # Deduplicate funds by name
        seen = set()
        unique_funds = []
        for f in g["funds"]:
            key = f["name"].upper()
            if key not in seen:
                seen.add(key)
                unique_funds.append(f)
        g["funds"] = unique_funds
        g["fund_count"] = len(unique_funds)
        result.append(g)

    # Sort: REX first, then by fund count
    result.sort(key=lambda x: (-int(x["is_rex"]), -x["fund_count"]))
    return result


def _gather_races(db: Session) -> list[dict]:
    """Underliers with products from multiple issuers (competitive races)."""
    from webapp.models import FundStatus, Trust

    # All PENDING or recently EFFECTIVE funds with recognizable underlier patterns
    pending = db.execute(
        select(
            FundStatus.fund_name,
            FundStatus.ticker,
            FundStatus.status,
            FundStatus.effective_date,
            FundStatus.latest_form,
            FundStatus.latest_filing_date,
            Trust.name.label("trust_name"),
            Trust.is_rex,
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status.in_(["PENDING", "EFFECTIVE"]))
        .where(FundStatus.fund_name.ilike("%2X%"))
        .order_by(FundStatus.effective_date.asc())
    ).all()

    # Group by extracted underlier
    underlier_map = defaultdict(list)
    for r in pending:
        name = r.fund_name or ""
        # Extract underlier from fund name (e.g., "T-REX 2X Long TSEM Daily" -> "TSEM")
        underlier = _extract_underlier(name)
        if not underlier:
            continue
        underlier_map[underlier].append({
            "fund_name": name,
            "ticker": r.ticker or "",
            "trust": r.trust_name or "",
            "status": r.status,
            "effective_date": str(r.effective_date) if r.effective_date else "",
            "filing_date": str(r.latest_filing_date) if r.latest_filing_date else "",
            "is_rex": r.is_rex,
        })

    # Only return underliers with 2+ issuers
    races = []
    for underlier, entries in underlier_map.items():
        trusts = set(e["trust"] for e in entries)
        if len(trusts) >= 2:
            races.append({
                "underlier": underlier,
                "issuer_count": len(trusts),
                "entries": sorted(entries, key=lambda x: x.get("effective_date") or "9999"),
                "has_rex": any(e["is_rex"] for e in entries),
            })

    races.sort(key=lambda x: (-int(x["has_rex"]), -x["issuer_count"]))
    return races


def _extract_underlier(fund_name: str) -> str | None:
    """Extract stock ticker from a 2X fund name."""
    import re
    name = fund_name.upper()
    # Pattern: "2X LONG TSEM DAILY" or "2X INVERSE NVDA DAILY"
    m = re.search(r'2X\s+(?:LONG|INVERSE|SHORT)\s+([A-Z]{1,5})\s', name)
    if m:
        return m.group(1)
    # Pattern: "Leveraged ... TSEM Daily"
    m = re.search(r'(?:LEVERAGED?|LEVERAGE)\s+.*?([A-Z]{2,5})\s+DAILY', name)
    if m:
        return m.group(1)
    return None


def _gather_upcoming(db: Session, today: date) -> list[dict]:
    """Funds going effective in next 14 days."""
    from webapp.models import FundStatus, Trust

    cutoff = today + timedelta(days=14)
    rows = db.execute(
        select(
            FundStatus.fund_name,
            FundStatus.ticker,
            FundStatus.effective_date,
            FundStatus.latest_form,
            Trust.name.label("trust_name"),
            Trust.is_rex,
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(FundStatus.status == "PENDING")
        .where(FundStatus.effective_date.isnot(None))
        .where(FundStatus.effective_date >= today)
        .where(FundStatus.effective_date <= cutoff)
        .order_by(FundStatus.effective_date.asc())
        .limit(30)
    ).all()

    return [
        {
            "fund_name": r.fund_name,
            "ticker": r.ticker or "",
            "trust": r.trust_name,
            "effective_date": str(r.effective_date),
            "days_left": (r.effective_date - today).days,
            "is_rex": r.is_rex,
        }
        for r in rows
    ]


def _gather_rex_pipeline(db: Session) -> dict:
    """REX product pipeline counts from the rex_products table."""
    from webapp.models import RexProduct

    try:
        total = db.query(RexProduct).count()
        if total == 0:
            return {"available": False}

        statuses = {}
        for status, count in db.query(RexProduct.status, func.count(RexProduct.id)).group_by(RexProduct.status).all():
            statuses[status] = count

        return {
            "available": True,
            "total": total,
            "listed": statuses.get("Listed", 0),
            "filed": statuses.get("Filed", 0),
            "awaiting": statuses.get("Awaiting Effective", 0),
            "research": statuses.get("Research", 0),
            "target_list": statuses.get("Target List", 0),
            "delisted": statuses.get("Delisted", 0),
        }
    except Exception:
        return {"available": False}


def _gather_strategy_watch(db: Session, since: date) -> list[dict]:
    """New income/autocallable/ODTE/thematic filings."""
    from webapp.models import Trust, Filing, FundExtraction

    keywords = ["%income%", "%covered call%", "%autocall%", "%ODTE%",
                "%0DTE%", "%premium%", "%yield%", "%option%"]
    conditions = [FundExtraction.series_name.ilike(kw) for kw in keywords]

    from sqlalchemy import or_
    rows = db.execute(
        select(
            Trust.name.label("trust_name"),
            Trust.is_rex,
            Filing.form,
            Filing.filing_date,
            FundExtraction.series_name,
            FundExtraction.effective_date,
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .join(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= since)
        .where(Filing.form.ilike("485%"))
        .where(or_(*conditions))
        .order_by(Filing.filing_date.desc())
        .limit(20)
    ).all()

    return [
        {
            "trust": r.trust_name,
            "fund_name": r.series_name,
            "form": r.form,
            "filing_date": str(r.filing_date) if r.filing_date else "",
            "effective_date": str(r.effective_date) if r.effective_date else "",
            "is_rex": r.is_rex,
        }
        for r in rows
    ]


# ---- HTML Rendering ----

def _render_brief(*, new_filings, races, upcoming, pipeline, strategy_watch, since, today) -> str:
    """Render the full intelligence brief HTML."""
    sections = []

    # Header
    sections.append(f"""
    <div style="background:{_NAVY}; color:{_WHITE}; padding:20px 24px; border-radius:8px 8px 0 0;">
      <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.1em; opacity:0.7;">REX Financial — Restricted Distribution</div>
      <div style="font-size:22px; font-weight:700; margin:4px 0;">Filing Intelligence Brief</div>
      <div style="font-size:13px; opacity:0.8;">{today.strftime('%A, %B %d, %Y')} | Filings since {since.strftime('%b %d')}</div>
    </div>
    """)

    # Executive summary bullets
    bullets = []
    if new_filings:
        total_funds = sum(f["fund_count"] for f in new_filings)
        rex_filings = [f for f in new_filings if f["is_rex"]]
        bullets.append(f"<b>{len(new_filings)}</b> trusts filed ({total_funds} funds)")
        if rex_filings:
            bullets.append(f"REX filed: {', '.join(f['trust_name'][:30] for f in rex_filings)}")
    if races:
        bullets.append(f"<b>{len(races)}</b> competitive races active (2+ issuers on same underlier)")
    if upcoming:
        urgent = [u for u in upcoming if u["days_left"] <= 7]
        if urgent:
            bullets.append(f"<b>{len(urgent)}</b> funds going effective within 7 days")

    if bullets:
        sections.append(f"""
        <div style="background:{_LIGHT}; border-left:3px solid {_BLUE}; padding:14px 20px; margin:0;">
          <div style="font-size:12px; font-weight:600; color:{_NAVY}; margin-bottom:6px;">KEY HIGHLIGHTS</div>
          <ul style="margin:0; padding-left:18px; font-size:13px; color:#374151;">
            {''.join(f'<li style="margin-bottom:4px;">{b}</li>' for b in bullets)}
          </ul>
        </div>
        """)

    # New Filings section
    if new_filings:
        rows_html = ""
        for f in new_filings:
            rex_badge = f'<span style="background:{_GREEN}; color:{_WHITE}; padding:1px 6px; border-radius:3px; font-size:10px; margin-left:4px;">REX</span>' if f["is_rex"] else ""
            fund_names = ", ".join(fn["name"][:40] for fn in f["funds"][:3])
            if len(f["funds"]) > 3:
                fund_names += f" +{len(f['funds']) - 3} more"
            eff_dates = ", ".join(sorted(f["effective_dates"]))[:30] if f["effective_dates"] else "--"
            rows_html += f"""
            <tr>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-weight:600;">{f['trust_name'][:35]}{rex_badge}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">{f['form']}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">{f['filing_date']}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER}; font-size:12px;">{fund_names}</td>
              <td style="padding:8px 10px; border-bottom:1px solid {_BORDER};">{f['fund_count']}</td>
            </tr>"""

        sections.append(f"""
        <div style="padding:16px 20px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">NEW FILINGS ({len(new_filings)} trusts)</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
              <tr style="background:{_LIGHT};">
                <th style="padding:8px 10px; text-align:left; font-size:11px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Trust / Issuer</th>
                <th style="padding:8px 10px; text-align:left; font-size:11px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Form</th>
                <th style="padding:8px 10px; text-align:left; font-size:11px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Filed</th>
                <th style="padding:8px 10px; text-align:left; font-size:11px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Funds</th>
                <th style="padding:8px 10px; text-align:left; font-size:11px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">#</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """)

    # Competitive Races
    if races:
        race_html = ""
        for race in races[:10]:
            rex_mark = f' <span style="color:{_GREEN}; font-weight:700;">REX IN RACE</span>' if race["has_rex"] else f' <span style="color:{_RED}; font-weight:600;">REX GAP</span>'
            entries_html = ""
            for e in race["entries"]:
                rex_tag = f'<span style="color:{_GREEN}; font-size:10px;"> (REX)</span>' if e["is_rex"] else ""
                status_color = _GREEN if e["status"] == "EFFECTIVE" else _ORANGE
                entries_html += f"""
                <tr>
                  <td style="padding:4px 10px; font-size:12px; border-bottom:1px solid #f1f5f9;">{e['trust'][:30]}{rex_tag}</td>
                  <td style="padding:4px 10px; font-size:12px; border-bottom:1px solid #f1f5f9; color:{status_color};">{e['status']}</td>
                  <td style="padding:4px 10px; font-size:12px; border-bottom:1px solid #f1f5f9;">{e['effective_date'] or '--'}</td>
                  <td style="padding:4px 10px; font-size:12px; border-bottom:1px solid #f1f5f9;">{e['ticker'] or 'TBD'}</td>
                </tr>"""

            race_html += f"""
            <div style="border:1px solid {_BORDER}; border-radius:6px; padding:12px; margin-bottom:10px;">
              <div style="font-size:14px; font-weight:700; color:{_NAVY};">{race['underlier']} <span style="font-size:12px; color:{_GRAY}; font-weight:400;">({race['issuer_count']} issuers)</span>{rex_mark}</div>
              <table style="width:100%; border-collapse:collapse; margin-top:6px;">
                <tr style="font-size:10px; text-transform:uppercase; color:{_GRAY};">
                  <td style="padding:2px 10px;">Issuer</td><td style="padding:2px 10px;">Status</td><td style="padding:2px 10px;">Effective</td><td style="padding:2px 10px;">Ticker</td>
                </tr>
                {entries_html}
              </table>
            </div>"""

        sections.append(f"""
        <div style="padding:16px 20px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">COMPETITIVE RACES ({len(races)} underliers)</div>
          {race_html}
        </div>
        """)

    # Upcoming Effectiveness
    if upcoming:
        rows_html = ""
        for u in upcoming:
            urgency = ""
            if u["days_left"] <= 3:
                urgency = f' style="background:#fef2f2; font-weight:600;"'
            elif u["days_left"] <= 7:
                urgency = f' style="background:#fffbeb;"'
            rex_badge = f'<span style="background:{_GREEN}; color:{_WHITE}; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:3px;">REX</span>' if u["is_rex"] else ""
            rows_html += f"""
            <tr{urgency}>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{u['effective_date']}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-weight:600;">{u['days_left']}d</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{u['trust'][:25]}{rex_badge}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-size:12px;">{u['fund_name'][:45]}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{u['ticker'] or 'TBD'}</td>
            </tr>"""

        sections.append(f"""
        <div style="padding:16px 20px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">UPCOMING EFFECTIVENESS (next 14 days)</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead><tr style="background:{_LIGHT}; font-size:11px; text-transform:uppercase; color:{_GRAY};">
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Date</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">In</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Trust</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Fund</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Ticker</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """)

    # Strategy Watch
    if strategy_watch:
        rows_html = ""
        for s in strategy_watch:
            rex_badge = f'<span style="background:{_GREEN}; color:{_WHITE}; padding:1px 5px; border-radius:3px; font-size:10px; margin-left:3px;">REX</span>' if s["is_rex"] else ""
            rows_html += f"""
            <tr>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{s['trust'][:25]}{rex_badge}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-size:12px;">{s['fund_name'][:50]}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{s['form']}</td>
              <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{s['filing_date']}</td>
            </tr>"""

        sections.append(f"""
        <div style="padding:16px 20px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">STRATEGY WATCH (Income / Options / Autocallable)</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead><tr style="background:{_LIGHT}; font-size:11px; text-transform:uppercase; color:{_GRAY};">
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Trust</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Fund</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Form</th>
              <th style="padding:6px 10px; text-align:left; border-bottom:2px solid {_BORDER};">Filed</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """)

    # REX Pipeline Summary
    if pipeline.get("available"):
        sections.append(f"""
        <div style="padding:16px 20px;">
          <div style="font-size:14px; font-weight:700; color:{_NAVY}; margin-bottom:10px;">T-REX PIPELINE</div>
          <div style="display:flex; gap:12px; flex-wrap:wrap;">
            <div style="background:{_LIGHT}; border:1px solid {_BORDER}; border-radius:6px; padding:10px 16px; text-align:center; min-width:80px;">
              <div style="font-size:22px; font-weight:800; color:{_GREEN};">{pipeline['listed']}</div>
              <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase;">Listed</div>
            </div>
            <div style="background:{_LIGHT}; border:1px solid {_BORDER}; border-radius:6px; padding:10px 16px; text-align:center; min-width:80px;">
              <div style="font-size:22px; font-weight:800; color:{_BLUE};">{pipeline['filed']}</div>
              <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase;">Filed</div>
            </div>
            <div style="background:{_LIGHT}; border:1px solid {_BORDER}; border-radius:6px; padding:10px 16px; text-align:center; min-width:80px;">
              <div style="font-size:22px; font-weight:800; color:{_ORANGE};">{pipeline.get('research', 0) + pipeline.get('target_list', 0)}</div>
              <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase;">Pipeline</div>
            </div>
            <div style="background:{_LIGHT}; border:1px solid {_BORDER}; border-radius:6px; padding:10px 16px; text-align:center; min-width:80px;">
              <div style="font-size:22px; font-weight:800; color:{_NAVY};">{pipeline['total']}</div>
              <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase;">Total</div>
            </div>
          </div>
        </div>
        """)

    # Footer
    sections.append(f"""
    <div style="background:{_LIGHT}; padding:14px 20px; border-radius:0 0 8px 8px; border-top:1px solid {_BORDER};">
      <div style="font-size:11px; color:{_GRAY};">
        <a href="{DASHBOARD_URL}/dashboard" style="color:{_BLUE};">Dashboard</a> |
        <a href="{DASHBOARD_URL}/filings/" style="color:{_BLUE};">Filing Explorer</a> |
        <a href="{DASHBOARD_URL}/filings/evaluator" style="color:{_BLUE};">Stock Evaluator</a>
      </div>
      <div style="font-size:10px; color:#94a3b8; margin-top:4px;">
        Data sourced from SEC EDGAR. Generated {datetime.now().strftime('%Y-%m-%d %H:%M ET')}.
        Restricted distribution — do not forward.
      </div>
    </div>
    """)

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Filing Intelligence Brief</title></head>
<body style="margin:0; padding:20px; background:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:700px; margin:0 auto; background:{_WHITE}; border-radius:8px; border:1px solid {_BORDER}; overflow:hidden;">
{body}
</div></body></html>"""
