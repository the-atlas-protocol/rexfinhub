#!/bin/bash
# Refresh local etp_tracker.db from VPS. Run on demand when local dev
# environment has drifted from production.
#
# VPS is the source of truth; the fresh-poller updates it every 15 min.
# Local copy is for development only and may not match — this script
# brings them back in sync.
#
# Backs up the local DB first.

set -euo pipefail

LOCAL=/c/Foundry/rexfinhub/data/etp_tracker.db
BACKUP_DIR=/c/Foundry/rexfinhub/data/backups
VPS=jarvis@46.224.126.196

if [ -f "$LOCAL" ]; then
    STAMP=$(date '+%Y%m%dT%H%M%S')
    cp -p "$LOCAL" "$BACKUP_DIR/etp_tracker.db.pre-vps-refresh-$STAMP.bak"
    echo "Backed up local DB to pre-vps-refresh-$STAMP.bak"
fi

echo "Fetching VPS etp_tracker.db..."
scp -p "$VPS:/home/jarvis/rexfinhub/data/etp_tracker.db" "$LOCAL"

# Quick sanity check
python -c "
import sqlite3
c = sqlite3.connect(r'C:\\Projects\\rexfinhub\\data\\etp_tracker.db')
print(f'  rex_products: {c.execute(\"SELECT COUNT(*) FROM rex_products\").fetchone()[0]}')
print(f'  filings:      {c.execute(\"SELECT COUNT(*) FROM filings\").fetchone()[0]:,}')
print(f'  latest filing date: {c.execute(\"SELECT MAX(filing_date) FROM filings\").fetchone()[0]}')
c.close()
"
echo "Done."
