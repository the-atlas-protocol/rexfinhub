#!/bin/bash
# install_fresh_poller_timer.sh -- one-shot installer for the 15-min fresh-filings poller.
#
# Run on the VPS as a user with sudo:
#   bash /home/jarvis/rexfinhub/scripts/install_fresh_poller_timer.sh
#
# Idempotent. Safe to re-run.

set -euo pipefail

UNIT_DIR=/etc/systemd/system

echo "==> Writing service unit..."
sudo tee "${UNIT_DIR}/rexfinhub-fresh-poller.service" > /dev/null <<'EOF'
[Unit]
Description=REX FinHub fresh-filings poller (lightweight 15-min cadence)
After=network-online.target
# NOTE: No Conflicts= directive. A Conflicts= would let this LIGHT job
# SIGTERM-kill the HEAVY intraday-refresh job (observed 2026-05-19 20:00).
# Mutual exclusion is handled correctly inside poll_fresh_filings.py via
# _heavy_scrape_active() — the light job checks + SKIPS ITSELF when
# intraday-refresh is active. intraday-refresh runs at :05 past the hour
# so it never starts in the same second as this :00/:15/:30/:45 poller.

[Service]
Type=oneshot
User=jarvis
WorkingDirectory=/home/jarvis/rexfinhub
EnvironmentFile=/home/jarvis/rexfinhub/config/.env
Environment=TZ=America/New_York
Environment=SEC_CACHE_DIR=/home/jarvis/rexfinhub/cache/sec
ExecStart=/home/jarvis/venv/bin/python scripts/poll_fresh_filings.py
# A single pass shouldn't take more than 5 min (scrape+sync). If it does,
# something is wrong -- kill it so the next 15-min slot starts clean.
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal
EOF

echo "==> Writing timer unit..."
sudo tee "${UNIT_DIR}/rexfinhub-fresh-poller.timer" > /dev/null <<'EOF'
[Unit]
Description=Run REX FinHub fresh-filings poller every 15 min during US business hours
After=network-online.target

[Timer]
# Mon-Fri, 08:00-20:45 ET, every 15 minutes. The 4x/day heavy sweep fires at
# the top of 08/12/16/20 -- those slots are skipped here via Conflicts= in the
# service unit, so no overlap and no wasted run.
OnCalendar=Mon..Fri *-*-* 08..20:00/15:00 America/New_York
# Catch missed runs if the VPS was rebooted
Persistent=true
# Randomize by 30s so it doesn't always start exactly on the minute
RandomizedDelaySec=30s
Unit=rexfinhub-fresh-poller.service

[Install]
WantedBy=timers.target
EOF

echo "==> Reloading systemd..."
sudo systemctl daemon-reload

echo "==> Enabling + starting timer..."
sudo systemctl enable --now rexfinhub-fresh-poller.timer

echo "==> Status:"
systemctl status rexfinhub-fresh-poller.timer --no-pager | head -12
echo
echo "==> Next 5 scheduled firings:"
systemctl list-timers rexfinhub-fresh-poller.timer --no-pager
echo
echo "Done. View logs with: sudo journalctl -u rexfinhub-fresh-poller.service -f"
