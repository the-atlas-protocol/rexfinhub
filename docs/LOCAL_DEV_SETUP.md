# Local Dev Environment Setup

Named URLs + live reload for local development.

## What You Get

| URL | What |
|-----|------|
| `http://rexfinhub.local` | App with live reload (auto-refreshes on file save) |
| `http://rexfinhub-api.local` | Direct uvicorn (no live reload) |
| `http://localhost:3000` | Fallback if Caddy isn't running |

## One-Time Setup (per machine)

### 1. Install Caddy (reverse proxy)

```powershell
winget install CaddyServer.Caddy
```

### 2. Install browser-sync (live reload)

```bash
npm install -g browser-sync
```

### 3. Create Caddyfile

Create `C:\Users\<you>\.caddy\Caddyfile` with:

```
http://rexfinhub.local {
    reverse_proxy localhost:3000
}

http://rexfinhub-api.local {
    reverse_proxy localhost:8000
}
```

### 4. Add hosts entries (run PowerShell as Administrator)

```powershell
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "`n# Local dev projects`n127.0.0.1  rexfinhub.local`n127.0.0.1  rexfinhub-api.local"
```

## Daily Usage

From the project directory:

```powershell
.\scripts\dev.bat
```

This starts all three services (Caddy + uvicorn + browser-sync) and shuts them down together with Ctrl+C.

## Adding More Projects

1. Add a block to `~/.caddy/Caddyfile`:
   ```
   http://myproject.local {
       reverse_proxy localhost:5000
   }
   ```
2. Add hosts entry (admin PowerShell):
   ```powershell
   Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "127.0.0.1  myproject.local"
   ```
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
