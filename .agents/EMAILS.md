# EMAILS Agent - Weekly Reports

## Status: WAITING (depends on MARKET agent)

## Mission
Build automated weekly email reports summarizing Market Intelligence data.
- Weekly emails for most recipients
- Daily emails for specific few
- HTML format with embedded charts
- Links back to /market/rex and /market/category

## My Files (I own these)
```
webapp/services/email_reports.py     # Create - Email generation logic
webapp/templates/emails/             # Create folder
  ├── weekly_report.html             # Weekly summary template
  ├── daily_report.html              # Daily summary template
  └── _components.html               # Reusable email components
scripts/send_weekly_report.py        # Create - CLI script to send
config/email_recipients.yaml         # Create - Recipient lists
```

## Dependencies (Read-only, created by MARKET agent)
```
webapp/services/market_data.py       # Uses: get_rex_summary(), get_category_summary()
```

**BLOCKED UNTIL**: MARKET agent completes `market_data.py` with these functions:
- `get_rex_summary()` - Returns REX totals and by-suite breakdown
- `get_category_summary(category, filters)` - Returns category data

## Do Not Touch
- `webapp/routers/market.py` (MARKET agent)
- `webapp/services/market_data.py` (MARKET agent - read only)
- `webapp/routers/screener.py` (FIXES agent)

---

## Email Specifications

### Weekly Report Content
1. **REX Summary Section**
   - Total AUM, Weekly/Monthly/3-Month flows
   - Performance by suite (6 suites)
   - Top movers (biggest flow changes)

2. **Category Highlights**
   - Brief summary of each category
   - REX market share in each
   - Notable competitor movements

3. **Charts (inline)**
   - AUM by suite pie chart
   - AUM trend line chart
   - Market share comparison

4. **Links**
   - "View full dashboard" → /market/rex
   - "View category details" → /market/category

### Daily Report Content (Subset)
- REX totals only
- Previous day flows
- Any significant movements (>$10M flow)

### Recipients
```yaml
# config/email_recipients.yaml
weekly:
  - exec1@rexshares.com
  - exec2@rexshares.com
  - product_team@rexshares.com

daily:
  - ceo@rexshares.com
  - head_of_product@rexshares.com
```

---

## Technical Approach

### Email Generation
```python
# webapp/services/email_reports.py
from webapp.services.market_data import get_rex_summary, get_category_summary

def generate_weekly_report() -> str:
    """Generate HTML for weekly email."""
    rex = get_rex_summary()
    categories = {cat: get_category_summary(cat) for cat in CATEGORIES}
    
    # Render template with data
    return render_template('emails/weekly_report.html', 
                          rex=rex, categories=categories)

def generate_daily_report() -> str:
    """Generate HTML for daily email."""
    rex = get_rex_summary()
    return render_template('emails/daily_report.html', rex=rex)
```

### Chart Generation for Email
Options:
1. **Static images**: Generate PNG with matplotlib, embed as base64
2. **QuickChart.io**: Generate chart images via URL
3. **Pre-rendered**: Cache chart images daily

Recommended: QuickChart.io for simplicity

### Sending
Use existing `webapp/services/graph_email.py` patterns for sending via Microsoft Graph API.

---

## Progress Log
- [ ] Wait for MARKET agent to complete market_data.py
- [ ] Create email_reports.py service
- [ ] Create email templates
- [ ] Create recipient config
- [ ] Create send script
- [ ] Test weekly email generation
- [ ] Test daily email generation
- [ ] Set up scheduling (cron or Windows Task Scheduler)

## Notes / Context for Next Session
- Check if MARKET agent has completed market_data.py
- Existing email code in `webapp/services/graph_email.py` for reference
- `etp_tracker/email_alerts.py` may also have useful patterns
- Email recipients in `email_recipients.txt` (existing file)

## Blockers
**BLOCKED**: Waiting for MARKET agent to create `webapp/services/market_data.py` with:
- `get_rex_summary()`
- `get_category_summary(category, filters)`
