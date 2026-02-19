# File Ownership & Boundaries

This file defines which agent owns which files to prevent conflicts.

---

## MARKET Agent
**Mission**: Market Intelligence module (REX View + Category View)

**OWNS (can create/edit)**:
- `webapp/routers/market.py` (create)
- `webapp/services/market_data.py` (create)
- `webapp/templates/market/` (create folder and all files inside)
- `webapp/static/js/market.js` (create)
- `webapp/static/css/market.css` (create)

**READ-ONLY (can reference, not edit)**:
- `data/DASHBOARD/The Dashboard.xlsx`
- `PLAN.md`
- Existing templates for style reference

---

## FIXES Agent
**Mission**: Fix screener, improve downloads, identify 33 Act trusts

**OWNS (can create/edit)**:
- `webapp/routers/screener.py`
- `webapp/routers/downloads.py`
- `webapp/services/screener_3x_cache.py`
- `webapp/services/screener_service.py`
- `webapp/templates/downloads.html`
- `webapp/templates/screener_*.html`
- `screener/data_loader.py`
- `screener/config.py`
- `etp_tracker/trusts.py` (add 33 Act flags)
- `webapp/routers/dashboard.py` (update display for 33 Act)
- `webapp/templates/dashboard.html` (loading indicator)
- `webapp/static/css/style.css` (loading animations only)

**READ-ONLY**:
- `data/SCREENER/data.xlsx`
- `PLAN.md`

---

## EMAILS Agent
**Mission**: Automated weekly email reports

**OWNS (can create/edit)**:
- `webapp/services/email_reports.py` (create)
- `webapp/templates/emails/` (create folder and all files inside)
- `scripts/send_weekly_report.py` (create)
- `config/email_recipients.yaml` (create)

**READ-ONLY (depends on MARKET agent)**:
- `webapp/services/market_data.py` (uses get_rex_summary, get_category_summary)

---

## SHARED FILES (Coordinate Before Editing)

These files may need edits from multiple agents. Rules:

### `webapp/main.py`
- **Rule**: Add your router import and registration, don't remove others
- **Pattern**:
  ```python
  from webapp.routers import market  # MARKET agent adds
  app.include_router(market.router)
  ```

### `webapp/templates/base.html`
- **Rule**: Add your nav link in the designated section, don't remove others
- **Pattern**: Add link to nav bar, maintain existing structure

### `requirements.txt`
- **Rule**: Add your dependencies at the end, don't remove existing
- **Pattern**: Add with comment `# Added by MARKET agent`

---

## Conflict Resolution

If two agents need to edit the same file:
1. First agent to reach the file makes their changes
2. Second agent checks git diff before editing
3. If conflict, coordinate via agent file notes

---

## File Status Quick Reference

| File | Owner | Status |
|------|-------|--------|
| `webapp/routers/market.py` | MARKET | To Create |
| `webapp/services/market_data.py` | MARKET | To Create |
| `webapp/templates/market/*` | MARKET | To Create |
| `webapp/routers/screener.py` | FIXES | Existing - Edit |
| `webapp/routers/downloads.py` | FIXES | Existing - Edit |
| `webapp/templates/downloads.html` | FIXES | Existing - Edit |
| `webapp/services/email_reports.py` | EMAILS | To Create |
| `webapp/main.py` | SHARED | Add router only |
| `webapp/templates/base.html` | SHARED | Add nav link only |
