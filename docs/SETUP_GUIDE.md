# REX Intelligence Hub - Setup Guide

## Overview

You're setting up 2 parallel Claude Code instances working on separate git worktrees.

| Agent | Folder | Branch | Focus |
|-------|--------|--------|-------|
| Agent 1 | `D:\REX_MARKET` | `feature/market-intelligence` | Market Intelligence module |
| Agent 2 | `D:\REX_FIXES` | `feature/webapp-fixes` | Screener fix + UI improvements |

---

## Step 1: Prepare Your Main Repository

Open PowerShell and run:

```powershell
cd D:\REX_ETP_TRACKER

# Check status - should be clean
git status

# If you have uncommitted changes, stash them
git stash

# Make sure you're on main
git checkout main

# Pull latest (if applicable)
git pull origin main
```

---

## Step 2: Create the Data Folder

```powershell
# Create the DASHBOARD folder in main repo
mkdir D:\REX_ETP_TRACKER\data\DASHBOARD
```

Now manually copy `The_Dashboard.xlsx` into `D:\REX_ETP_TRACKER\data\DASHBOARD\`

You can do this via File Explorer or:
```powershell
# If the file is in Downloads:
copy "$env:USERPROFILE\Downloads\The_Dashboard.xlsx" "D:\REX_ETP_TRACKER\data\DASHBOARD\"
```

---

## Step 3: Create Git Branches

```powershell
cd D:\REX_ETP_TRACKER

# Create the two feature branches from main
git branch feature/market-intelligence
git branch feature/webapp-fixes

# Verify branches exist
git branch -a
```

You should see:
```
  feature/market-intelligence
  feature/webapp-fixes
* main
```

---

## Step 4: Create Git Worktrees

```powershell
# Create worktree for Agent 1 (Market Intelligence)
git worktree add D:\REX_MARKET feature/market-intelligence

# Create worktree for Agent 2 (Webapp Fixes)
git worktree add D:\REX_FIXES feature/webapp-fixes
```

You should see output like:
```
Preparing worktree (checking out 'feature/market-intelligence')
HEAD is now at abc1234 Your last commit message
```

---

## Step 5: Copy Data to Agent 1's Worktree

Agent 1 needs the Excel file in their worktree:

```powershell
# Create folder in Agent 1's worktree
mkdir D:\REX_MARKET\data\DASHBOARD

# Copy the Excel file
copy "D:\REX_ETP_TRACKER\data\DASHBOARD\The_Dashboard.xlsx" "D:\REX_MARKET\data\DASHBOARD\"
```

---

## Step 6: Verify Setup

```powershell
# Check worktrees are set up
git worktree list
```

Should show:
```
D:/REX_ETP_TRACKER        abc1234 [main]
D:/REX_MARKET             abc1234 [feature/market-intelligence]
D:/REX_FIXES              abc1234 [feature/webapp-fixes]
```

Verify data file:
```powershell
dir D:\REX_MARKET\data\DASHBOARD\
```

Should show `The_Dashboard.xlsx`

---

## Step 7: Open VS Code Windows

Open two separate VS Code windows:

```powershell
# Window 1 - Agent 1 (Market Intelligence)
code D:\REX_MARKET

# Window 2 - Agent 2 (Webapp Fixes)
code D:\REX_FIXES
```

**Important**: Each VS Code window should show its respective folder in the Explorer sidebar.

---

## Step 8: Start Claude Code in Each Window

In each VS Code window:
1. Open the Command Palette (`Ctrl+Shift+P`)
2. Type "Claude" and select "Claude: Open Chat" (or your Claude Code activation method)
3. Use the prompts below

---

## Agent 1 Prompt (Market Intelligence)

Copy and paste this entire prompt into Claude Code in the `D:\REX_MARKET` window:

```
Read PLAN.md carefully - it contains the complete specification for your work.

You are Agent 1, responsible for building the Market Intelligence module.

YOUR SCOPE:
- Build /market/rex (REX View) - Executive dashboard showing REX performance by suite
- Build /market/category (Category View) - Competitive landscape with dynamic filters
- Create data service, router, templates, JS, and CSS
- Register router in main.py and add navigation link

KEY DATA:
- Excel file is at data/DASHBOARD/The_Dashboard.xlsx
- Main sheet: q_master_data (5,078 funds, 102 columns)
- Time series: q_aum_time_series_labeled (for charts)
- REX products have is_rex == True (90 products)

IMPORTANT CONTEXT:
- This is for Product Team and Executives to view competitive intelligence
- Two views: REX View (how are we doing?) and Category View (how is the market?)
- 6 REX suites based on category_display values
- Dynamic slicers change based on selected category
- KPIs: Total AUM, Weekly/Monthly/3-Month Flows, # Products, Market Share %

DO NOT TOUCH:
- webapp/routers/screener.py (Agent 2's scope)
- etp_tracker/* (pipeline code)
- screener/* (screener module)
- data/SCREENER/ (screener data)

Start by reading PLAN.md, then begin implementation. Create the data service first, then router, then templates.
```

---

## Agent 2 Prompt (Webapp Fixes)

Copy and paste this entire prompt into Claude Code in the `D:\REX_FIXES` window:

```
Read PLAN.md carefully - it contains the complete specification for your work.

You are Agent 2, responsible for fixing existing issues and improving UX.

YOUR SCOPE (in priority order):

1. FIX SCREENER "No Bloomberg Data" ISSUE (HIGHEST PRIORITY)
   - File exists at data/SCREENER/data.xlsx (verified: 5MB)
   - Path resolves correctly in screener.config.DATA_FILE
   - Sheets are correct: stock_data, etp_data
   - Problem is likely in cache logic or silent exception handling
   - Check: webapp/routers/screener.py, webapp/services/screener_3x_cache.py
   - Add logging, find where it fails, fix it

2. DOWNLOADS PAGE UI IMPROVEMENTS
   - Too many funds (7,000+) displayed at once
   - Add pagination (50 per page)
   - Add search/filter box
   - Files: webapp/routers/downloads.py, webapp/templates/downloads.html

3. IDENTIFY 33 ACT PRODUCTS (identification only)
   - Some trusts file N-1A instead of 485 forms
   - Query SEC EDGAR to identify which trusts are 33 Act
   - Update dashboard to show "33 Act Filer" instead of "No 485 filings"
   - Files: etp_tracker/trusts.py, webapp/routers/dashboard.py

4. LOADING INDICATOR (polish)
   - Dashboard loads 122 trust cards with no visual feedback
   - Add skeleton loading animation
   - Files: webapp/templates/dashboard.html, webapp/static/css/style.css

DO NOT TOUCH:
- webapp/routers/market.py (Agent 1 will create this)
- webapp/services/market_data.py (Agent 1 will create this)
- webapp/templates/market/* (Agent 1 will create this)
- data/DASHBOARD/ (Agent 1's data)

Start by reading PLAN.md, then tackle Task 1 (Screener fix) first - it's the highest priority broken feature.
```

---

## Step 9: Monitor Progress

Let each agent work. They will:
- Read PLAN.md
- Examine relevant code
- Implement changes
- Test their work

You can check on each window periodically. If an agent gets stuck, you can provide guidance.

---

## Step 10: Merge When Complete

Once both agents finish, merge their work:

```powershell
cd D:\REX_ETP_TRACKER

# Make sure main is current
git checkout main

# Merge Agent 1's work
git merge feature/market-intelligence --no-ff -m "Add Market Intelligence module (REX View + Category View)"

# Merge Agent 2's work  
git merge feature/webapp-fixes --no-ff -m "Fix screener data loading, improve downloads UI, identify 33 Act trusts"

# If there are merge conflicts, resolve them manually
```

---

## Step 11: Clean Up

After successful merge:

```powershell
cd D:\REX_ETP_TRACKER

# Remove worktrees
git worktree remove D:\REX_MARKET
git worktree remove D:\REX_FIXES

# Delete branches (optional, but recommended)
git branch -d feature/market-intelligence
git branch -d feature/webapp-fixes

# Push to remote
git push origin main
```

---

## Step 12: Test the Application

```powershell
cd D:\REX_ETP_TRACKER

# Activate virtual environment
.\.venv\Scripts\Activate

# Run the webapp
python -m webapp.main
```

Then test:
- http://localhost:8000/market/rex (new REX View)
- http://localhost:8000/market/category (new Category View)
- http://localhost:8000/screener/ (should show data now)
- http://localhost:8000/downloads/ (should have pagination)

---

## Troubleshooting

### "Branch already exists" error
```powershell
# Delete and recreate
git branch -D feature/market-intelligence
git branch feature/market-intelligence
```

### "Worktree already exists" error
```powershell
# Remove existing worktree
git worktree remove D:\REX_MARKET --force
```

### Merge conflicts
If you get conflicts during merge:
1. Open the conflicted files
2. Look for `<<<<<<<`, `=======`, `>>>>>>>` markers
3. Manually resolve by keeping the code you want
4. `git add .` and `git commit`

### Agent gets stuck
- Check PLAN.md for guidance
- Review existing code patterns in the codebase
- Ask the agent to explain what's blocking them

---

## Quick Reference

| Item | Path |
|------|------|
| Main repo | `D:\REX_ETP_TRACKER` |
| Agent 1 worktree | `D:\REX_MARKET` |
| Agent 2 worktree | `D:\REX_FIXES` |
| Dashboard data | `data\DASHBOARD\The_Dashboard.xlsx` |
| Screener data | `data\SCREENER\data.xlsx` |
| Plan file | `PLAN.md` |

| Agent | Branch | Primary Files |
|-------|--------|---------------|
| Agent 1 | `feature/market-intelligence` | `webapp/routers/market.py`, `webapp/services/market_data.py`, `webapp/templates/market/*` |
| Agent 2 | `feature/webapp-fixes` | `webapp/routers/screener.py`, `webapp/routers/downloads.py`, `etp_tracker/trusts.py` |
