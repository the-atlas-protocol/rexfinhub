#!/bin/bash
# install_fresh_poller_timer.sh -- one-shot installer for the 15-min fresh-filings poller.
#
# Run on the VPS as a user with sudo:
#   bash /home/jarvis/rexfinhub/scripts/install_fresh_poller_timer.sh
#
# Idempotent. Safe to re-run.
#
# ORCH-01 fix (2026-06-08): unit content lives in deploy/systemd/. This
# script just copies the static files into /etc/systemd/system/ and enables
# the timer. See deploy/systemd/NOTES_fresh_poller_unit_files_2026-06-08.md.

set -euo pipefail

UNIT_DIR=/etc/systemd/system
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/deploy/systemd"

for unit in rexfinhub-fresh-poller.service rexfinhub-fresh-poller.timer; do
    if [[ ! -f "${SRC_DIR}/${unit}" ]]; then
        echo "ERROR: ${SRC_DIR}/${unit} missing -- run from a checkout that has deploy/systemd/" >&2
        exit 1
    fi
done

echo "==> Installing unit files from ${SRC_DIR} ..."
sudo cp "${SRC_DIR}/rexfinhub-fresh-poller.service" "${UNIT_DIR}/"
sudo cp "${SRC_DIR}/rexfinhub-fresh-poller.timer"   "${UNIT_DIR}/"

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
