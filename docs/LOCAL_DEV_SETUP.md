# Local Dev Environment Setup

Named URLs + live reload for local development.

## What You Get

| URL | What |
|-----|------|
| `http://rexfinhub.local` | App with live reload (auto-refreshes on file save) |
| `http://rexfinhub-api.local` | Direct uvicorn (no live reload) |
| `http://localhost:3000` | Fallback if Caddy isn't running |

## What Persists vs What Doesn't

| Component | Survives reboot? | Notes |
|-----------|-----------------|-------|
| Hosts file | Yes | URL mapping is permanent |
| Caddy, uvicorn, browser-sync | No | Run `dev.bat` each session |

## One-Time Setup (per machine)

### Step 1: Install Caddy

```powershell
winget install CaddyServer.Caddy
```

Close and reopen your terminal after install so `caddy` is on PATH.

### Step 2: Install browser-sync

```bash
npm install -g browser-sync
```

### Step 3: Create Caddyfile

Create the folder and file at `C:\Users\<you>\.caddy\Caddyfile`:

```powershell
mkdir "$env:USERPROFILE\.caddy" -Force
notepad "$env:USERPROFILE\.caddy\Caddyfile"
```

Paste this content and save:

```
http://rexfinhub.local {
    reverse_proxy localhost:3000
}

http://rexfinhub-api.local {
    reverse_proxy localhost:8000
}
```

### Step 4: Add hosts entries

Open Notepad **as Administrator**:

```powershell
Start-Process notepad "C:\Windows\System32\drivers\etc\hosts" -Verb RunAs
```

Add these lines at the bottom:

```
# Local dev projects
127.0.0.1  rexfinhub.local
127.0.0.1  rexfinhub-api.local
```

Save and close.

**IMPORTANT**: Do NOT use `Add-Content` in PowerShell for the hosts file - it can
split lines with encoding issues. Always use Notepad for this step.

### Step 5: Verify

```powershell
ping rexfinhub.local
```

Should show `Reply from 127.0.0.1`. If not, recheck the hosts file for broken lines.

## Daily Usage

From the project directory in PowerShell:

```powershell
.\scripts\dev.bat
```

This starts Caddy + uvicorn + browser-sync. Visit `http://rexfinhub.local`.

Press `Ctrl+C` to stop everything when done.

## Adding More Projects

1. Add a block to `~/.caddy/Caddyfile`:
   ```
   http://myproject.local {
       reverse_proxy localhost:5000
   }
   ```
2. Open hosts file in Notepad as admin, add: `127.0.0.1  myproject.local`
3. Restart Caddy (or run `dev.bat` which does it automatically)

## How It Works

```
Browser -> http://rexfinhub.local
  -> Caddy (port 80, routes by hostname)
    -> browser-sync (port 3000, injects live-reload script)
      -> uvicorn (port 8000, serves FastAPI app)
```

- **Caddy**: Maps named URLs to ports. Config at `~/.caddy/Caddyfile`.
- **browser-sync**: Watches HTML/CSS/JS files, auto-refreshes browser on change.
- **uvicorn --reload**: Watches Python files, auto-restarts server on change.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ping rexfinhub.local` fails | Hosts file has broken lines. Open in Notepad and fix. |
| `ERR_CONNECTION_REFUSED` | Caddy isn't running. Run `.\scripts\dev.bat` |
| `caddy` not found in dev.bat | Close and reopen terminal (PATH needs refresh after install) |
| CSS/HTML changes not refreshing | Check browser-sync is running (look for `:3000` in dev.bat output) |
