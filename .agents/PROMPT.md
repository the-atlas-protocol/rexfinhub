# Claude Code Setup Prompt

Copy everything below the line and paste it into a new Claude Code chat.

---

## MEGA PROMPT (Copy from here)

```
I am setting up a multi-agent workflow for the REX ETP Tracker project. You are being assigned as an agent to work on a specific part of this project.

## PROJECT OVERVIEW

REX ETP Tracker is being transformed into the REX Intelligence Hub - a unified platform for competitive intelligence. The webapp is built with FastAPI + Jinja2 templates.

**Current capabilities**: SEC filing tracking, fund details, daily digests
**What we're adding**: Market Intelligence views, fixing broken screener, automated emails

## YOUR FIRST TASK

Before doing any work, you need to understand the system:

1. **Read these files in order**:
   - `.agents/MASTER.md` - Overall orchestration and vision
   - `.agents/BOUNDARIES.md` - Which files each agent owns
   - `PLAN.md` - Detailed specifications for Market Intelligence
   - `CLAUDE.md` - Codebase documentation and patterns

2. **Inspect the codebase structure**:
   - `webapp/main.py` - How routers are registered
   - `webapp/routers/` - Existing route patterns
   - `webapp/services/` - Existing service patterns
   - `webapp/templates/base.html` - Template structure

3. **Check the data**:
   - `data/DASHBOARD/The Dashboard.xlsx` - Market intelligence data
   - `data/SCREENER/data.xlsx` - Bloomberg screener data

## AGENT ASSIGNMENT

After reading the files above, I will tell you which agent you are:
- **MARKET**: Build /market/rex and /market/category views
- **FIXES**: Fix screener bug, improve downloads page
- **EMAILS**: Build automated weekly reports (blocked until MARKET completes)

Your task file is at `.agents/{AGENT_NAME}.md`

## WORKFLOW RULES

1. **Only edit files you own** (see BOUNDARIES.md)
2. **Update your agent file** with progress after each session
3. **For shared files** (main.py, base.html): Add your code, don't remove existing
4. **If blocked**: Note it in your agent file and stop

## COORDINATION

Other Claude Code instances may be working on other agents simultaneously. Stay in your lane. If you need something from another agent, note it as a blocker.

---

**NOW**: Start by reading `.agents/MASTER.md`, then tell me what you understand about the project and ask which agent you should be.
```

---

## USAGE

1. Open VS Code on `D:\REX_ETP_TRACKER`
2. Start Claude Code
3. Paste the mega prompt above
4. When it asks which agent, tell it: "You are the MARKET agent" (or FIXES, or EMAILS)
5. It will read its task file and start working

Repeat for each agent in separate VS Code windows.
