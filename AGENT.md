# AGENT: Advanced-Market
**Task**: TASK-G — Advanced Market Pages (Timeline, Calendar, Compare)
**Branch**: feature/advanced-market
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Advanced-Market.md`
Format: `## [HH:MM] Task description` then bullet details.

## Your New Files
- `webapp/routers/market_advanced.py` (NEW)
- `webapp/templates/market/timeline.html` (NEW)
- `webapp/templates/market/calendar.html` (NEW)
- `webapp/templates/market/compare.html` (NEW)

## Your Edited Files
- `webapp/main.py` (add router registration)
- `webapp/templates/market/base.html` (add new nav pills)

## CRITICAL: Read These First
Before writing anything, read:
- `webapp/main.py` (how other routers are registered)
- `webapp/routers/market.py` (existing pattern for market routes)
- `webapp/templates/market/base.html` (nav structure to extend)
- `webapp/templates/market/issuer.html` (example template pattern)
- `webapp/models.py` (understand Trust, Filing, FundExtraction models)
- `webapp/dependencies.py` (get_db pattern)
- `webapp/database.py` (DB setup)

## Page 1: Fund Lifecycle Timeline (/market/timeline)

### Router (market_advanced.py):
```python
from fastapi import APIRouter, Request, Depends, Query
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from pathlib import Path
from webapp.dependencies import get_db

router = APIRouter(prefix="/market", tags=["market-advanced"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/timeline")
def timeline_view(
    request: Request,
    trust_id: int = Query(default=None),
    db: Session = Depends(get_db)
):
    from webapp.models import Trust, Filing, FundExtraction

    # Get all trusts for selector
    trusts = db.execute(select(Trust).order_by(Trust.name)).scalars().all()

    timeline_items = []
    selected_trust = None

    if trust_id:
        selected_trust = db.get(Trust, trust_id)
        if selected_trust:
            # Get filings for this trust, sorted newest first
            filings = db.execute(
                select(Filing)
                .where(Filing.trust_id == trust_id)
                .where(Filing.form_type.in_(['485BPOS', '485BXT', '485APOS', 'N-14']))
                .order_by(desc(Filing.filing_date))
                .limit(200)
            ).scalars().all()

            for filing in filings:
                # Get fund extractions for this filing
                extractions = db.execute(
                    select(FundExtraction)
                    .where(FundExtraction.filing_id == filing.id)
                    .limit(10)
                ).scalars().all()

                timeline_items.append({
                    "filing": filing,
                    "extractions": extractions,
                    "fund_count": len(extractions),
                })

    return templates.TemplateResponse("market/timeline.html", {
        "request": request,
        "active_tab": "timeline",
        "available": True,
        "trusts": trusts,
        "selected_trust": selected_trust,
        "trust_id": trust_id,
        "timeline_items": timeline_items,
    })
```

### Template (timeline.html):
```html
{% set active_tab = 'timeline' %}
{% extends "market/base.html" %}

{% block title %}Fund Lifecycle Timeline — REX Financial Intelligence Hub{% endblock %}

{% block market_content %}
<h2 class="section-title">Fund Lifecycle Timeline</h2>

<div class="timeline-controls">
  <select class="select-sm" onchange="if(this.value) window.location='/market/timeline?trust_id='+this.value">
    <option value="">Select a trust...</option>
    {% for trust in trusts %}
    <option value="{{ trust.id }}" {{ 'selected' if trust.id == trust_id else '' }}>{{ trust.name }}</option>
    {% endfor %}
  </select>
</div>

{% if selected_trust %}
<h3 class="trust-timeline-title">{{ selected_trust.name }}</h3>

{% if timeline_items %}
<div class="timeline">
  {% for item in timeline_items %}
  <div class="timeline-entry timeline-{{ item.filing.form_type|lower|replace('/', '-') }}">
    <div class="timeline-date">{{ item.filing.filing_date.strftime('%b %d, %Y') if item.filing.filing_date else 'N/A' }}</div>
    <div class="timeline-content">
      <div class="timeline-form">
        <span class="badge badge-{{ 'primary' if item.filing.form_type == '485BPOS' else 'warning' if item.filing.form_type == '485BXT' else 'secondary' }}">
          {{ item.filing.form_type }}
        </span>
        {% if item.filing.effective_date %}
        <span class="timeline-effective">Effective: {{ item.filing.effective_date.strftime('%b %d, %Y') }}</span>
        {% endif %}
      </div>
      {% if item.fund_count > 0 %}
      <div class="timeline-funds">{{ item.fund_count }} fund{{ 's' if item.fund_count != 1 else '' }}</div>
      {% endif %}
      <div class="timeline-accession">
        <a href="https://www.sec.gov/Archives/edgar/data/{{ selected_trust.cik }}/{{ item.filing.accession_number|replace('-','') }}/{{ item.filing.accession_number }}-index.htm"
           target="_blank" class="text-link">{{ item.filing.accession_number }}</a>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<div class="alert alert-info">No 485 filings found for this trust.</div>
{% endif %}

{% elif trusts %}
<div class="alert alert-info">Select a trust above to view its filing timeline.</div>
{% endif %}

{% endblock %}

{% block market_scripts %}
{% endblock %}
```

## Page 2: Compliance Calendar (/market/calendar)

### Router section:
```python
@router.get("/calendar")
def calendar_view(
    request: Request,
    db: Session = Depends(get_db)
):
    from webapp.models import Trust, Filing
    from datetime import date, timedelta

    today = date.today()

    # Upcoming 485BXT extensions (future effective dates)
    upcoming = db.execute(
        select(Filing, Trust)
        .join(Trust, Filing.trust_id == Trust.id)
        .where(Filing.form_type == '485BXT')
        .where(Filing.effective_date >= today)
        .order_by(Filing.effective_date.asc())
        .limit(100)
    ).all()

    # Recently effective 485BPOS (last 30 days)
    recent_cutoff = today - timedelta(days=30)
    recently_effective = db.execute(
        select(Filing, Trust)
        .join(Trust, Filing.trust_id == Trust.id)
        .where(Filing.form_type == '485BPOS')
        .where(Filing.effective_date >= recent_cutoff)
        .order_by(Filing.effective_date.desc())
        .limit(50)
    ).all()

    # Add urgency classification to upcoming
    upcoming_classified = []
    for filing, trust in upcoming:
        days_until = (filing.effective_date - today).days if filing.effective_date else None
        urgency = "green"
        if days_until is not None:
            if days_until < 30:
                urgency = "red"
            elif days_until < 60:
                urgency = "amber"
        upcoming_classified.append({
            "filing": filing,
            "trust": trust,
            "days_until": days_until,
            "urgency": urgency,
        })

    return templates.TemplateResponse("market/calendar.html", {
        "request": request,
        "active_tab": "calendar",
        "available": True,
        "today": today,
        "upcoming": upcoming_classified,
        "recently_effective": [{"filing": f, "trust": t} for f, t in recently_effective],
    })
```

### Template (calendar.html):
```html
{% set active_tab = 'calendar' %}
{% extends "market/base.html" %}

{% block title %}Compliance Calendar — REX Financial Intelligence Hub{% endblock %}

{% block market_content %}
<h2 class="section-title">Compliance Calendar</h2>

<div class="calendar-grid">
  <div class="calendar-section">
    <h3>Upcoming 485BXT Extensions</h3>
    {% if upcoming %}
    <table class="data-table">
      <thead>
        <tr>
          <th>Trust</th>
          <th>Effective Date</th>
          <th>Days Until</th>
          <th>Accession</th>
        </tr>
      </thead>
      <tbody>
        {% for item in upcoming %}
        <tr>
          <td>{{ item.trust.name }}</td>
          <td>{{ item.filing.effective_date.strftime('%b %d, %Y') if item.filing.effective_date else 'N/A' }}</td>
          <td>
            <span class="urgency-badge urgency-{{ item.urgency }}">
              {% if item.days_until is not none %}{{ item.days_until }}d{% else %}—{% endif %}
            </span>
          </td>
          <td class="text-mono" style="font-size:0.75rem">{{ item.filing.accession_number }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="alert alert-info">No upcoming extension events.</div>
    {% endif %}
  </div>

  <div class="calendar-section">
    <h3>Recently Effective (Last 30 Days)</h3>
    {% if recently_effective %}
    <table class="data-table">
      <thead>
        <tr>
          <th>Trust</th>
          <th>Effective Date</th>
          <th>Form</th>
        </tr>
      </thead>
      <tbody>
        {% for item in recently_effective %}
        <tr>
          <td>{{ item.trust.name }}</td>
          <td>{{ item.filing.effective_date.strftime('%b %d, %Y') if item.filing.effective_date else 'N/A' }}</td>
          <td><span class="badge badge-primary">{{ item.filing.form_type }}</span></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="alert alert-info">No recent effective filings.</div>
    {% endif %}
  </div>
</div>
{% endblock %}

{% block market_scripts %}
{% endblock %}
```

## Page 3: Fund Comparison (/market/compare)

### Router section:
```python
@router.get("/compare")
def compare_view(
    request: Request,
    tickers: str = Query(default=""),
):
    from webapp.services.market_data import get_master_data, data_available

    available = data_available()
    ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()][:4]

    fund_data = []
    if available and ticker_list:
        try:
            master = get_master_data()
            ticker_col = next((c for c in master.columns if c.lower() == 'ticker'), None)
            if ticker_col:
                for ticker in ticker_list:
                    row = master[master[ticker_col].str.upper() == ticker]
                    if not row.empty:
                        r = row.iloc[0]
                        fund_data.append({
                            "ticker": ticker,
                            "row": r.to_dict(),
                        })
        except Exception:
            pass

    return templates.TemplateResponse("market/compare.html", {
        "request": request,
        "active_tab": "compare",
        "available": available,
        "tickers": tickers,
        "ticker_list": ticker_list,
        "fund_data": fund_data,
    })
```

### Template (compare.html):
Build a side-by-side comparison table. The template should gracefully handle missing data fields.

## Register in main.py

In `webapp/main.py`, after the existing market router include, add:
```python
from webapp.routers.market_advanced import router as market_advanced_router
app.include_router(market_advanced_router)
```

Read main.py first to find the correct location.

## Update market/base.html Nav Pills

In `webapp/templates/market/base.html`, add 3 more pills after the existing ones:
```html
<a href="/market/timeline" class="pill {% if active_tab == 'timeline' %}active{% endif %}">Timeline</a>
<a href="/market/calendar" class="pill {% if active_tab == 'calendar' %}active{% endif %}">Calendar</a>
<a href="/market/compare" class="pill {% if active_tab == 'compare' %}active{% endif %}">Compare</a>
```

## Add CSS for New Pages (market.css or inline)

Add to market.css (or use the worktree's market.css if Agent B hasn't merged yet — add inline styles if needed):
```css
.timeline { position: relative; padding-left: 20px; }
.timeline::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; background: #E2E8F0; }
.timeline-entry { position: relative; padding: 12px 0 12px 20px; border-bottom: 1px solid #F1F5F9; }
.timeline-entry::before { content: ''; position: absolute; left: -5px; top: 20px; width: 10px; height: 10px; border-radius: 50%; background: var(--blue); }
.timeline-date { font-size: 0.75rem; color: #94A3B8; font-weight: 600; }
.timeline-content { margin-top: 4px; }
.timeline-effective { font-size: 0.8rem; color: #374151; margin-left: 8px; }
.timeline-funds { font-size: 0.77rem; color: #6B7280; margin-top: 2px; }
.timeline-accession { font-size: 0.7rem; margin-top: 2px; }
.timeline-controls { margin-bottom: 20px; }
.trust-timeline-title { font-size: 1.1rem; font-weight: 700; margin-bottom: 16px; }
.urgency-badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
.urgency-green { background: #D1FAE5; color: #065F46; }
.urgency-amber { background: #FEF3C7; color: #92400E; }
.urgency-red { background: #FEE2E2; color: #991B1B; }
.calendar-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.calendar-section h3 { font-size: 0.9rem; font-weight: 700; margin-bottom: 12px; }
@media (max-width: 768px) { .calendar-grid { grid-template-columns: 1fr; } }
```

## Important Notes

1. **Check model fields carefully** — read `webapp/models.py` first. The `Filing` model may have different field names than assumed (e.g., `effective_date` may be stored differently, `accession_number` may be formatted differently).

2. **Import the right get_master_data function** — in compare_view, check what functions `market_data.py` actually exposes. May be `get_rex_summary()` or something else. Read the file.

3. **market_advanced.py MUST NOT conflict with Agent B's market.py** — Agent B owns `webapp/routers/market.py`. You create a SEPARATE file `webapp/routers/market_advanced.py`.

4. **market/base.html conflict** — Both you and Agent B modify market/base.html. YOU add nav pills (timeline, calendar, compare). Agent B adds data_as_of. These will conflict at merge time. That's OK — the merge step resolves it. Just do your change cleanly.

## Commit Convention
```
git add webapp/routers/market_advanced.py webapp/templates/market/timeline.html webapp/templates/market/calendar.html webapp/templates/market/compare.html webapp/main.py webapp/templates/market/base.html
git commit -m "feat: Advanced market pages - Fund Lifecycle Timeline, Compliance Calendar, Fund Comparison"
```

## Done Criteria
- [ ] `/market/timeline` loads, shows trust selector
- [ ] `/market/calendar` loads, shows upcoming extensions + recent effectivities
- [ ] `/market/compare?tickers=X,Y` loads (may show no data without master file)
- [ ] Routes registered in main.py
- [ ] Nav pills added to market/base.html
- [ ] No import errors
