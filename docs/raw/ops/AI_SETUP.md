# AI Development Setup — Claude Code

How to configure Claude Code's agent system for this project and any new project.

## Architecture

```
User-Level (~/.claude/)                    Applies to ALL projects
├── settings.json                          Global permissions, agent teams enabled
├── agents/                                Generic specialists
│   ├── researcher.md                      Read-only codebase explorer (sonnet)
│   ├── implementer.md                     Code writer with worktree isolation (opus)
│   ├── reviewer.md                        Quality gate, read-only (opus)
│   └── architect.md                       System designer, read-only (opus)
└── commands/
    └── spawn-team.md                      /spawn-team slash command

Project-Level (<project>/.claude/)         Applies to THIS project only
├── settings.local.json                    Project-specific permissions
└── agents/
    └── <domain-specialist>.md             Domain expert for this project
```

## How It Works

1. **You talk to the lead session** (your main Claude Code terminal)
2. **Lead spawns specialized teammates** via the Task tool — each runs as a subagent
3. **Teammates coordinate** through a shared task list and direct messages
4. **Implementers work in worktrees** (isolated git branches) to avoid conflicts
5. **Reviewer validates** before changes merge back

## Initializing a New Project

### Step 1: Create CLAUDE.md in the project root

This is the single most important file. Claude Code auto-loads it on every session. Include:

```markdown
# Project Name

## Project Overview
[What it does, who it's for, key URLs]

## Architecture
[Directory tree, module responsibilities, data flow]

## Conventions
[Code style, naming, patterns, off-limits areas]

## Agent Teams
This project supports Claude Code Agent Teams.

### Specialists
- [List project-specific agents from .claude/agents/]

### File Ownership Rules
- Each implementer teammate owns specific files — no overlapping edits
- [List shared/append-only files]
- [List off-limits files that should never be touched]
```

### Step 2: Initialize git (required for implementer worktrees)

```bash
cd <project-dir>
git init
git add .
git commit -m "init: project scaffold"
```

### Step 3: Create .claude/agents/ for domain specialists (optional)

Only create project-level agents for domain expertise that doesn't exist in the generic agents. Example:

```yaml
---
name: sec-pipeline-specialist
description: Domain expert for SEC filing pipeline
tools: Read, Grep, Glob, Bash, Edit, Write
model: opus
memory: project
---
[Domain-specific knowledge and rules]
```

### Step 4: Create .claude/settings.local.json (optional)

Project-specific permission overrides:

```json
{
  "permissions": {
    "allow": [
      "Bash(git add:*)",
      "Bash(python:*)"
    ]
  }
}
```

## Spawning Teams

Use the `/spawn-team` command followed by your task description:

```
/spawn-team Build the email notification system
```

Or ask directly:

```
Create a team with a researcher and 2 implementers to build the REST API
```

### Team Composition Guide

| Task Type | Team |
|-----------|------|
| Research only | 1 researcher + 1 architect |
| Bug fix | 1 researcher + 1 implementer + 1 reviewer |
| New feature | 1 researcher + 1-2 implementers + 1 reviewer |
| Refactoring | 1 architect + 1-2 implementers + 1 reviewer |
| Full feature | 1 architect + 1 researcher + 2 implementers + 1 reviewer |

### Cost Guide

| Role | Model | Cost | Use When |
|------|-------|------|----------|
| researcher | sonnet | Low | Always — cheap exploration |
| architect | opus | High | Complex features, system design |
| implementer | opus | High | Production code |
| reviewer | opus | High | Quality gates |

**Rule of thumb**: Start with 2-3 teammates. Scale up only if needed.

## Working Directory

Always run Claude Code from the project root (e.g., `C:\Projects\rexfinhub\`), not from a parent directory. This ensures:
- CLAUDE.md auto-loads
- Project-level agents are available
- .claude/settings.local.json applies
- Git operations work correctly

## Previous System (Archived)

This project previously used a file-based multi-agent system (`.agents/` directory with BOUNDARIES.md, MASTER.md, task files, and a `dev-orchestrator` CLI). That system has been replaced by Claude Code's native agent teams. Historical progress logs are preserved in `docs/archive/agent-progress/`.
