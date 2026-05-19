#!/bin/bash
# Daily sync: pull VPS historical artifacts to D: drive.
# Run from laptop. Requires D: drive mounted + ssh access to VPS.
#
# Targets:
#   - VPS screener_snapshots/   -> D:/sec-data/rexfinhub/screener_snapshots/  (additive)
#   - VPS data/total_returns/    -> D:/sec-data/rexfinhub/total_returns/       (additive)
#   - VPS data/backups/*.db      -> D:/sec-data/rexfinhub/db_backups/          (ALL pre/daily backups we don't have)
#
# Idempotent: scp -p preserves timestamps so already-copied files won't re-transfer.
# Schedule via Windows Task Scheduler at 23:30 ET daily (after VPS 23:00 backup).
# Phase 1 (ADR 0003): backup pull broadened from "latest only" to "everything we don't have"
# so D drive becomes a true long-term archive.

set -euo pipefail

D=/d/sec-data/rexfinhub
mkdir -p "$D/screener_snapshots" "$D/total_returns" "$D/db_backups"

VPS=jarvis@46.224.126.196
echo "[$(date '+%F %T')] D-drive sync starting"

# 1. Screener snapshots — pull every dated folder we do not already have.
ssh "$VPS" 'ls /home/jarvis/rexfinhub/data/DASHBOARD/exports/screener_snapshots/' | while read d; do
    if [ -z "$d" ]; then continue; fi
    if [ ! -d "$D/screener_snapshots/$d" ]; then
        echo "  fetching screener_snapshots/$d"
        scp -rp "$VPS:/home/jarvis/rexfinhub/data/DASHBOARD/exports/screener_snapshots/$d" "$D/screener_snapshots/"
    fi
done

# 2. total_returns — pull all (scp will skip if same).
scp -p "$VPS:/home/jarvis/rexfinhub/data/total_returns/*.json" "$D/total_returns/" 2>/dev/null || true
scp -p "$VPS:/home/jarvis/rexfinhub/data/total_returns/*.csv"  "$D/total_returns/" 2>/dev/null || true

# 3. DB backups — pull every nightly backup we don't already have.
# VPS keeps ~14 days; D drive is the long-term archive.
ssh "$VPS" 'ls /home/jarvis/rexfinhub/data/backups/*.db 2>/dev/null' | while read f; do
    if [ -z "$f" ]; then continue; fi
    base=$(basename "$f")
    if [ ! -f "$D/db_backups/$base" ]; then
        echo "  fetching $base"
        scp -p "$VPS:$f" "$D/db_backups/"
    fi
done

echo "[$(date '+%F %T')] D-drive sync complete"
du -sh "$D/screener_snapshots" "$D/total_returns" "$D/db_backups" 2>&1
