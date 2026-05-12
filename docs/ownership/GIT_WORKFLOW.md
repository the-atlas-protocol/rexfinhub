# Git Workflow for Ownership Pillar

## Your branch

```
main                    ← Ryu's daily work, auto-deploys to Render
  └── ownership-pillar  ← Your branch — all your work goes here
```

## Daily workflow

```bash
# Start of day: get Ryu's latest changes
git fetch origin
git merge origin/main

# Work on your files
# ... edit, test, etc ...

# Commit your changes
git add webapp/routers/holdings.py webapp/templates/holdings*.html
git commit -m "feat: redesign institution list page"

# Push to your branch
git push origin ownership-pillar
```

## When a feature is ready

1. Push your latest to `ownership-pillar`
2. Go to GitHub: https://github.com/ryuoelasmar/rexfinhub/pull/new/ownership-pillar
3. Create a Pull Request targeting `main`
4. Ryu reviews and merges

## Rules

- **Never push to `main` directly** — branch protection will block it anyway
- **Never edit files outside your scope** — CODEOWNERS will flag it in the PR
- **Merge `origin/main` regularly** — keeps your branch current with classification updates
- **Commit often, push often** — Ryu uses git worktree to view your progress

## If you get merge conflicts

Most likely on shared files (models.py, base.html). Steps:

```bash
git fetch origin
git merge origin/main
# If conflicts appear:
# 1. Open the conflicted files
# 2. Look for <<<<<<< / ======= / >>>>>>> markers
# 3. Keep both changes (yours AND Ryu's)
# 4. git add <resolved-file>
# 5. git commit
```

When in doubt, ask Ryu before resolving a conflict in a file you don't own.

## Files you can freely commit

```
webapp/routers/holdings.py
webapp/routers/holdings_placeholder.py
webapp/routers/intel.py
webapp/routers/intel_competitors.py
webapp/routers/intel_insights.py
webapp/templates/holdings*.html
webapp/templates/crossover.html
webapp/templates/institution*.html
webapp/static/js/holdings*.js
webapp/static/js/intel*.js
etp_tracker/thirteen_f.py
scripts/run_13f.py
docs/ownership/*
```

## Files that need PR review

```
webapp/models.py          (adding new models/fields)
webapp/database.py        (DB connection changes)
webapp/main.py            (router registration)
webapp/dependencies.py    (new dependencies)
requirements.txt          (new packages)
```
