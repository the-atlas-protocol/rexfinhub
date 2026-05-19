#!/bin/bash
# One-shot VPS migration for ADR 0005: rename sec-scrape -> intraday-refresh.
#
# Run on the VPS as a user with sudo:
#   bash /home/jarvis/rexfinhub/scripts/migrate_to_intraday_refresh.sh
#
# What it does:
#   1. Disables rexfinhub-sec-scrape.timer (keeps unit files for revert).
#   2. Copies the new rexfinhub-intraday-refresh.{service,timer} units into
#      /etc/systemd/system/.
#   3. Reloads systemd, enables + starts the new timer.
#   4. Rewrites rexfinhub-fresh-poller.service Conflicts= to point at the new
#      unit name.
#   5. Prints next-fire times for verification.
#
# Idempotent. Safe to re-run.

set -euo pipefail

REPO=/home/jarvis/rexfinhub
UNIT_DIR=/etc/systemd/system

echo "==> Disabling old rexfinhub-sec-scrape.timer..."
sudo systemctl disable --now rexfinhub-sec-scrape.timer 2>/dev/null || true

echo "==> Installing new intraday-refresh units..."
sudo cp "$REPO/deploy/systemd/rexfinhub-intraday-refresh.service" "$UNIT_DIR/"
sudo cp "$REPO/deploy/systemd/rexfinhub-intraday-refresh.timer"   "$UNIT_DIR/"

echo "==> Updating fresh-poller Conflicts= directive..."
# In-place rewrite of the on-VPS service unit (was installed by
# install_fresh_poller_timer.sh, so we edit the deployed copy directly).
if [ -f "$UNIT_DIR/rexfinhub-fresh-poller.service" ]; then
    sudo sed -i 's|^Conflicts=rexfinhub-sec-scrape.service|Conflicts=rexfinhub-intraday-refresh.service|' \
        "$UNIT_DIR/rexfinhub-fresh-poller.service"
fi

echo "==> Reloading systemd..."
sudo systemctl daemon-reload

echo "==> Enabling + starting new timer..."
sudo systemctl enable --now rexfinhub-intraday-refresh.timer

echo
echo "==> Old timer state:"
systemctl is-enabled rexfinhub-sec-scrape.timer 2>&1 || true
systemctl is-active  rexfinhub-sec-scrape.timer 2>&1 || true

echo
echo "==> New timer state:"
systemctl is-enabled rexfinhub-intraday-refresh.timer 2>&1
systemctl is-active  rexfinhub-intraday-refresh.timer 2>&1

echo
echo "==> Next 4 firings:"
systemctl list-timers rexfinhub-intraday-refresh.timer --no-pager | head -3

echo
echo "==> Dry-run the wrapper once to validate decision logic:"
/home/jarvis/venv/bin/python "$REPO/scripts/intraday_refresh.py" --dry-run

echo
echo "Done. Tail logs with:"
echo "  sudo journalctl -u rexfinhub-intraday-refresh.service -f"
echo "  tail -f $REPO/data/.intraday_refresh.log"
