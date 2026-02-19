# EMAILS Agent

## Mission
Build automated weekly email reports for executives

## Status: BLOCKED
**Waiting on**: MARKET agent to complete `webapp/services/market_data.py`

## Files I Will Own (When Unblocked)
```
webapp/services/email_reports.py   # CREATE
webapp/templates/emails/           # CREATE folder
scripts/send_weekly_report.py      # CREATE
```

## Dependencies
```
webapp/services/market_data.py     # READ - need get_rex_summary(), get_category_summary()
webapp/services/graph_email.py     # READ - existing email sending code
```

## Specifications
- Weekly emails: Full summary for most recipients
- Daily emails: Quick summary for specific few
- Format: HTML with embedded charts
- Links back to /market/rex and /market/category

## Progress
- [ ] Wait for MARKET agent to complete market_data.py
- [ ] Create email_reports.py service
- [ ] Create email templates
- [ ] Create send script
- [ ] Test email generation

## Notes
Check `.agents/MARKET.md` to see if market_data.py is complete before starting.
