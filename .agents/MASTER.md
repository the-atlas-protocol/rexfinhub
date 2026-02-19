# REX Intelligence Hub - Master Orchestration

## You Are The Master Agent

This Claude Code session is the master orchestrator. You:
- Plan and assign work
- Spawn sub-agents by telling the user which prompt to paste in new windows
- Track progress across all agents
- Handle cross-cutting concerns (shared files, conflicts)

---

## Current State (Post-Restructure)

### Folder Structure (Cleaned)
```
D:\REX_ETP_TRACKER\
├── .agents/              # Agent system
├── config/               # Configuration files
│   ├── .env
│   ├── .env.example
│   ├── email_recipients.txt
│   ├── digest_subscribers.txt
│   └── render.yaml (ALSO keep copy at root for Render)
├── data/                 # Data files
│   ├── DASHBOARD/
│   ├── SCREENER/
│   └── etp_tracker.db
├── docs/                 # Documentation
├── logs/                 # Log files
├── scripts/              # Runnable scripts
├── temp/                 # Temporary files (gitignored)
├── webapp/               # FastAPI app (STAYS AT ROOT)
├── etp_tracker/          # SEC pipeline (STAYS AT ROOT)
├── screener/             # Screener module (STAYS AT ROOT)
├── tests/
├── outputs/
├── reports/
├── http_cache/
├── CLAUDE.md             # MUST be at root (Claude Code reads this)
├── render.yaml           # MUST be at root (Render reads this)
├── requirements.txt
└── .gitignore
```

### DO NOT MOVE
- `webapp/`, `etp_tracker/`, `screener/` - Stay at root, not in src/
- Would break 173 imports, path resolution, templates, Render deployment

### Broken Things To Fix
| File | Issue |
|------|-------|
| `etp_tracker/email_alerts.py` | .env path -> config/.env |
| `webapp/auth.py` | .env path -> config/.env |
| `webapp/routers/api.py` | .env path -> config/.env |
| `webapp/routers/admin.py` | config file paths -> config/ |
| `webapp/services/graph_email.py` | .env path -> config/.env |
| `webapp/services/claude_service.py` | .env path -> config/.env |
| `scripts/run_daily.py` | PROJECT_ROOT needs .parent.parent |
| `CLAUDE.md` | Restore to root if not there |
| `render.yaml` | Restore to root if not there |

---

## Active Workstreams

### 1. RESTRUCTURE (You - Master)
**Status**: IN PROGRESS
**Task**: Fix broken paths from reorganization
**See**: The todo list in your current session

### 2. MARKET (Sub-Agent)
**Status**: NOT STARTED
**Task**: Build /market/rex and /market/category
**File**: `.agents/MARKET.md`
**Owns**: webapp/routers/market.py, webapp/services/market_data.py, webapp/templates/market/*

### 3. FIXES (Sub-Agent)
**Status**: NOT STARTED
**Task**: Fix screener "No Bloomberg Data", improve downloads
**File**: `.agents/FIXES.md`
**Owns**: webapp/routers/screener.py, webapp/routers/downloads.py, screener/*

### 4. EMAILS (Sub-Agent)
**Status**: BLOCKED (waiting on MARKET)
**Task**: Build weekly email reports
**File**: `.agents/EMAILS.md`
**Owns**: webapp/services/email_reports.py, webapp/templates/emails/*

---

## How To Spawn Sub-Agents

When ready to start a sub-agent:

1. Tell user: "Open a new VS Code window on this folder and start Claude Code"
2. Give them this prompt to paste:

```
You are a sub-agent working on the REX ETP Tracker project.

YOUR TASK FILE: .agents/{AGENT_NAME}.md

RULES:
1. Read your task file first
2. Read .agents/BOUNDARIES.md for file ownership
3. Only edit files you own
4. Update your task file with progress when done
5. If blocked, note it and stop

Start by reading your task file.
```

3. Replace `{AGENT_NAME}` with: MARKET, FIXES, or EMAILS

---

## File Ownership (Boundaries)

### MARKET Agent Owns
- webapp/routers/market.py (create)
- webapp/services/market_data.py (create)
- webapp/templates/market/* (create)
- webapp/static/js/market.js (create)
- webapp/static/css/market.css (create)

### FIXES Agent Owns
- webapp/routers/screener.py
- webapp/routers/downloads.py
- webapp/services/screener_*.py
- webapp/templates/downloads.html
- webapp/templates/screener_*.html
- screener/*

### EMAILS Agent Owns
- webapp/services/email_reports.py (create)
- webapp/templates/emails/* (create)
- scripts/send_weekly_report.py (create)

### Shared Files (Coordinate)
- webapp/main.py - Add router, don't remove
- webapp/templates/base.html - Add nav link, don't remove
- requirements.txt - Add deps, don't remove
- config/* - Master handles

### Master Handles
- .agents/*
- config/*
- docs/*
- CLAUDE.md
- render.yaml
- .gitignore
- Cross-cutting fixes

---

## Immediate Priority

1. **Finish restructure fixes** (you're doing this now)
2. **Commit clean state**
3. **Spawn MARKET agent** - Biggest new feature
4. **Spawn FIXES agent** - Broken functionality
5. **Wait for MARKET, then spawn EMAILS**

---

## Verification Commands

```powershell
# Test imports
python -c "from webapp.main import app; print('webapp OK')"
python -c "from etp_tracker.run_pipeline import main; print('etp_tracker OK')"
python -c "from screener.data_loader import load_all; print('screener OK')"

# Test server
uvicorn webapp.main:app --port 8000

# Test endpoints
# http://localhost:8000/ (dashboard)
# http://localhost:8000/screener/ (should show data after FIXES agent)
# http://localhost:8000/market/rex (after MARKET agent)
```

---

## Progress Tracking

Update this section as work completes:

- [ ] Restructure fixes committed
- [ ] MARKET agent spawned
- [ ] FIXES agent spawned
- [ ] /market/rex working
- [ ] /market/category working
- [ ] /screener/ showing data
- [ ] /downloads/ paginated
- [ ] EMAILS agent spawned
- [ ] Weekly email working
