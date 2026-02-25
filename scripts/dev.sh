#!/usr/bin/env bash
# Live-reload dev server: uvicorn + browser-sync + Caddy reverse proxy
# Usage: bash scripts/dev.sh
#
# Visit http://rexfinhub.local (requires hosts file entry + Caddy)
# Falls back to http://localhost:3000 if Caddy isn't running

cd "$(dirname "$0")/.." || exit 1

CADDYFILE="$HOME/.caddy/Caddyfile"

# Kill background processes on exit
cleanup() {
  echo "Shutting down..."
  kill $UVICORN_PID 2>/dev/null
  kill $BSYNC_PID 2>/dev/null
  caddy stop --config "$CADDYFILE" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

# Start Caddy reverse proxy (named URLs)
if command -v caddy &>/dev/null && [ -f "$CADDYFILE" ]; then
  echo "Starting Caddy (named URLs)..."
  caddy stop --config "$CADDYFILE" 2>/dev/null
  caddy start --config "$CADDYFILE" 2>/dev/null
  CADDY_OK=$?
else
  echo "Caddy not found or no Caddyfile - skipping named URLs"
  CADDY_OK=1
fi

# Start uvicorn (auto-restarts on Python changes)
echo "Starting uvicorn on :8000..."
uvicorn webapp.main:app --reload --port 8000 &
UVICORN_PID=$!

# Wait for uvicorn to be ready
echo "Waiting for uvicorn..."
for i in $(seq 1 15); do
  curl -s http://localhost:8000/ > /dev/null 2>&1 && break
  sleep 1
done

# Start browser-sync proxy (auto-refreshes browser on template/static changes)
echo "Starting browser-sync on :3000..."
browser-sync start \
  --proxy "localhost:8000" \
  --port 3000 \
  --files "webapp/templates/**/*.html" \
  --files "webapp/static/**/*.css" \
  --files "webapp/static/**/*.js" \
  --no-open \
  --no-notify &
BSYNC_PID=$!

echo ""
echo "==================================="
echo "  Dev server ready!"
if [ $CADDY_OK -eq 0 ]; then
echo "  App:           http://rexfinhub.local"
echo "  API (direct):  http://rexfinhub-api.local"
fi
echo "  Fallback:      http://localhost:3000"
echo "  BrowserSync:   http://localhost:3001"
echo "==================================="
echo ""

# Wait for either process to exit
wait
