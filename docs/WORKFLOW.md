# Development Workflow

How this project is set up across machines, synced, and developed.

---

## Machines

| Machine | Role | Path |
|---------|------|------|
| Desktop (RyuPC) | Development + pipeline runs | `C:\Projects\rexfinhub` |
| Work Laptop | Development + pipeline runs | `C:\Projects\rexfinhub` |

Both machines are equal. Either can run the pipeline, server, or Claude Code sessions.

**Rule**: Never run Claude Code on both machines at the same time.

---

## Syncthing

Syncthing keeps two folders in sync between desktop and laptop:

| Folder | Path | What it contains |
|--------|------|------------------|
| Projects | `C:\Projects\` | All project code |
| Claude Code Data | `C:\Users\<username>\.claude\` | Claude memory, settings, session history |

Syncthing runs in the background via **SyncTrayzor** (desktop) or a startup script (laptop). You never need to touch it after initial setup.

### SyncTrayzor Config Tip
Deny Syncthing auto-upgrades to prevent the `-n` flag crash:
- In `%APPDATA%\SyncTrayzor\config.xml`, set `<SyncthingDenyUpgrade>true</SyncthingDenyUpgrade>`
- If syncthing already auto-updated to v2.x, replace `%APPDATA%\SyncTrayzor\syncthing.exe` with the one from `C:\Program Files\SyncTrayzor\syncthing.exe`

### What's NOT synced
- `http_cache/` (13GB+ SEC response cache) — each machine maintains its own
- `__pycache__/` — rebuilt locally

These are excluded via `C:\Projects\.stignore`.

---

## New Machine Setup

1. **Install Syncthing**: `winget install Syncthing.Syncthing`
2. **Start Syncthing**: Run `syncthing`, opens web UI at `http://127.0.0.1:8384`
3. **Add remote device**: Paste the other machine's Device ID, save
4. **Accept on other machine**: Open its Syncthing UI, approve the new device
5. **Share folders**: On the existing machine, share "Projects" and "Claude Code Data" with the new device
6. **Accept folders on new machine**:
   - Projects → `C:\Projects`
   - Claude Code Data → `C:\Users\<username>\.claude`
7. **Auto-start**: Create `syncthing.vbs` in `shell:startup`:
   ```vbs
   Set WshShell = CreateObject("WScript.Shell")
   WshShell.Run "syncthing --no-browser", 0, False
   ```
8. **Verify**: Create a test file on one machine, check it appears on the other within 30 seconds

### http_cache (optional)
The SEC HTTP cache (`http_cache/`) is ~13GB and not synced. To bootstrap a new machine:
- Copy `http_cache/` from USB drive or the other machine via external drive
- Or just let the pipeline rebuild it over time (it caches as it fetches)

---

## Daily Development

1. Open a terminal on whichever machine you're using
2. `cd C:\Projects\rexfinhub`
3. Start Claude Code: `claude`
4. Work normally — all changes sync to the other machine automatically

### Pipeline runs
```powershell
cd C:\Projects\rexfinhub
python scripts/run_daily.py
```
Run on whichever machine is available. Pipeline is incremental — seconds if nothing new.

### Local server
```powershell
cd C:\Projects\rexfinhub
uvicorn webapp.main:app --reload --port 8000
```

---

## Git + GitHub

- **Remote**: https://github.com/ryuoelasmar/rexfinhub.git
- **Branch**: `main`
- Auto-deploys to Render on push

Git state syncs between machines via Syncthing (the `.git/` directory is included in the sync). Push from whichever machine you're working on.

---

## Claude Code

Claude Code stores its memory and settings in `~/.claude/`, which syncs between machines.

This means:
- Your Claude memory persists across machines
- Project-specific context (CLAUDE.md at project root) is always available
- Settings and permissions sync automatically

The project's `CLAUDE.md` is the primary context file — Claude auto-loads it when you start a session in the project directory.
