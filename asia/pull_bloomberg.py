"""Pull the bloomberg_daily_file.xlsm via Microsoft Graph API (SharePoint).

Delegates to rexfinhub/webapp/services/graph_files.py and bbg_file.py. No credentials
in this project — they live in ../rexfinhub/config/.env.

Usage:
    python pull_bloomberg.py
"""
from __future__ import annotations
import os, shutil, sys
from pathlib import Path
from datetime import datetime

# Make rexfinhub importable and cd into it so its config/.env is loaded.
REXFINHUB = Path(r"C:/Projects/rexfinhub")
if not REXFINHUB.exists():
    sys.exit(f"rexfinhub not found at {REXFINHUB} — cannot pull Bloomberg file.")

sys.path.insert(0, str(REXFINHUB))

# Load rexfinhub's .env so MSAL picks up AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET.
env_path = REXFINHUB / "config" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

os.chdir(REXFINHUB)  # graph_files resolves paths relative to cwd in some places

try:
    from webapp.services.bbg_file import get_bloomberg_file
except Exception as e:
    sys.exit(f"Cannot import rexfinhub bbg_file module: {e}")

print("Pulling Bloomberg daily file via Graph API...")
try:
    local_path = get_bloomberg_file()
except Exception as e:
    sys.exit(f"FAILED: {e}")

mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
size_mb = local_path.stat().st_size / 1024 / 1024
print(f"  rexfinhub cache: {local_path}")
print(f"  Modified:         {mtime}")
print(f"  Size:             {size_mb:.1f} MB")

# Mirror to the OneDrive path that rex-asia code reads from (so existing loaders pick it up).
ONEDRIVE_PATH = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")
if ONEDRIVE_PATH.parent.exists():
    try:
        shutil.copy2(local_path, ONEDRIVE_PATH)
        print(f"  Mirrored to:      {ONEDRIVE_PATH}")
    except Exception as e:
        print(f"  WARN: mirror to OneDrive path failed: {e}")

# Also mirror locally for reproducibility.
LOCAL_MIRROR = Path(r"C:/Projects/rex-asia/bloomberg_daily_file_live.xlsm")
shutil.copy2(local_path, LOCAL_MIRROR)
print(f"  Mirrored to:      {LOCAL_MIRROR}")

print(f"\nDone. Use this path in rex-asia loaders: {ONEDRIVE_PATH}")
