# File Ownership

## MARKET Agent
```
webapp/routers/market.py           # CREATE
webapp/services/market_data.py     # CREATE
webapp/templates/market/*          # CREATE folder + files
webapp/static/js/market.js         # CREATE
webapp/static/css/market.css       # CREATE
```

## FIXES Agent
```
webapp/routers/screener.py         # EDIT
webapp/routers/downloads.py        # EDIT
webapp/services/screener_*.py      # EDIT
webapp/templates/downloads.html    # EDIT
webapp/templates/screener_*.html   # EDIT
screener/*                         # EDIT
```

## EMAILS Agent
```
webapp/services/email_reports.py   # CREATE
webapp/templates/emails/*          # CREATE folder + files
scripts/send_weekly_report.py      # CREATE
```

## SHARED (Add only, don't remove existing code)
```
webapp/main.py                     # Add router registration
webapp/templates/base.html         # Add nav link
requirements.txt                   # Add dependencies
```

## MASTER Handles (Sub-agents don't touch)
```
.agents/*
config/*
docs/*
CLAUDE.md
render.yaml
.gitignore
```
