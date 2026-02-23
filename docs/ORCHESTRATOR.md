# Dev Orchestrator - Usage Guide

## What Is It

A multi-agent development system that lets you plan work, spawn parallel AI agents, and merge results. You interact through VS Code's Chat panel by selecting custom agents from the dropdown.

**Repo**: `rexfinhub/dev-orchestrator`
**Install**: `pip install dev-orchestrator` (or `pip install -e D:\dev-orchestrator` for local dev)

---

## Setup (Already Done)

The project has been initialized with `orchestrate init`. These files exist:

```
.github/
  agents/
    orchestrator.agent.md    # Plans work, delegates to agents
    implementer.agent.md     # Codes and commits
    reviewer.agent.md        # Reviews changes
    researcher.agent.md      # Explores codebase (hidden, invoked by Orchestrator)
  copilot-instructions.md    # Project-wide Copilot instructions
.agents/
  BOUNDARIES.md              # File ownership matrix (auto-generated)
  tasks/                     # Active task files
  archive/                   # Completed tasks
orchestrate.toml             # Config (model, max_turns, cost caps)
```

---

## Daily Workflow

### Option A: VS Code Chat Panel (Recommended)

1. Open VS Code Chat panel (Ctrl+Alt+I)
2. Select **Claude Opus 4.6** from the model picker
3. Select **Orchestrator** from the agent dropdown
4. Type what you want:
   ```
   Fix the screener data path bug and add market intelligence pages
   ```
5. Orchestrator plans the work, creates task files, and either:
   - Hands off to Implementer (single task)
   - Runs `orchestrate run` in terminal (parallel tasks)
6. Check progress: ask "What's the status?"
7. When done: ask "Merge the completed work"

### Option B: CLI (Terminal)

```bash
# Plan tasks
orchestrate plan "Fix screener and build market intelligence"

# Spawn parallel workers
orchestrate run

# Check progress
orchestrate status

# Merge completed work
orchestrate merge
```

### Option C: Simple Tasks

For quick single-file changes, skip the Orchestrator entirely:
1. Select **Implementer** from the Chat agent dropdown
2. Describe what you need
3. It edits files, tests, and commits directly

---

## Custom Agents

| Agent | When to Use | What It Does |
|-------|-------------|--------------|
| **Orchestrator** | Multi-step work, parallel features | Plans tasks, assigns files, coordinates |
| **Implementer** | Direct coding tasks | Edits files, runs tests, commits |
| **Reviewer** | After implementation | Reviews code for bugs, security, style |
| **Researcher** | (Auto-invoked) | Gathers codebase context for planning |

### Agent Handoffs

The Orchestrator shows handoff buttons:
- **"Implement This Plan"** - Sends plan to Implementer
- **"Review Changes"** - Sends diff to Reviewer

The Implementer shows:
- **"Review My Changes"** - Sends to Reviewer for quality check

---

## How Parallel Work Happens

When the Orchestrator identifies multiple independent tasks:

1. **Plan**: `orchestrate plan` calls Claude API to break work into tasks with file assignments
2. **Worktrees**: Tasks that need branch isolation get a worktree in `.worktrees/`
3. **Spawn**: `orchestrate run` launches parallel `claude -p` CLI sessions
4. **Monitor**: Each worker reads `AGENT.md` for its task and file scope
5. **Complete**: Workers set Status to DONE in AGENT.md with commit hash
6. **Merge**: `orchestrate merge` merges worktree branches back to main

```
main branch (C:\Projects\rexfinhub)
  |
  |-- .worktrees/market/   (feature/market branch, Worker A)
  |-- .worktrees/fixes/    (feature/fixes branch, Worker B)
  |
  orchestrate merge --> clean main with both features
```

---

## Configuration

Edit `orchestrate.toml` in the project root:

```toml
[orchestrator]
model = "claude-opus-4-6"           # Worker model (always Opus for code)
planning_model = "claude-opus-4-6"  # Planner model
max_turns = 50                       # Max agent iterations
max_budget_usd = 5.0                 # Cost cap per agent session
max_parallel = 3                     # Max concurrent workers
pause_between_spawns = 2.0           # Seconds between launches
```

---

## File Ownership

When `orchestrate plan` runs, it generates `.agents/BOUNDARIES.md` with explicit file assignments:

```markdown
### MARKET (TASK-002)
- webapp/routers/market.py (CREATE)
- webapp/services/market_data.py (CREATE)
- webapp/templates/market/*.html (CREATE)

### FIXES (TASK-001)
- screener/data_loader.py (EDIT)
- webapp/services/screener_3x_cache.py (EDIT)
```

Workers can ONLY edit files assigned to them. Shared files (main.py, base.html, requirements.txt) are append-only.

---

## Cost Monitoring

- **Per session**: Type `/cost` in Claude Code
- **Per agent**: Capped at `max_budget_usd` in orchestrate.toml ($5 default)
- **Aggregate**: Check console.anthropic.com for billing
- **Turn limit**: `max_turns` prevents runaway agents (50 default)

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Agents don't appear in Chat dropdown | Reopen VS Code, check `.github/agents/` exists |
| `orchestrate` command not found | Run `pip install -e D:\dev-orchestrator` |
| Plan fails with "No API key" | Set `ANTHROPIC_API_KEY` in env or `config/.env` |
| Worker hangs | Check `max_turns` in orchestrate.toml, reduce if needed |
| Merge conflicts | Run `orchestrate merge`, resolve manually, then re-run |
| Wrong model in Chat | Select Claude Opus 4.6 from model picker dropdown |
