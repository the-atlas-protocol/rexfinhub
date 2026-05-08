"""PR 1 smoke test — verify v3 URLs return 200 and old URLs 301 to expected targets.

Runs against a FastAPI TestClient (no live server needed). Authenticates via
SITE_PASSWORD from config/.env so middleware lets requests through.

Usage:
    cd C:/Projects/rexfinhub && python tests/smoke_v3_pr1.py

Expected: every line ends "OK" or "WARN" (warnings are acceptable degraded states
like 200-with-empty-body when DB is fresh on local dev).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add repo root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load SITE_PASSWORD from .env so the auth middleware lets us in
env_file = ROOT / "config" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Skip cache prewarm to avoid 30-60s startup. Patch BEFORE webapp.main loads.
import webapp.main as _main_mod
_main_mod._prewarm_caches = lambda: None

from fastapi.testclient import TestClient
from webapp.main import app

client = TestClient(app, follow_redirects=False)

# Authenticate
pw = os.environ.get("SITE_PASSWORD", "dev-site-password")
r = client.post("/login", data={"password": pw, "next": "/"})
assert r.status_code in (302, 303), f"Login failed: {r.status_code}"
print(f"AUTH    OK — logged in as site user")

# ============================================================
# v3 URLs — should return 200
# ============================================================
V3_URLS_200 = [
    # Operations pillar
    "/operations/products",
    "/operations/pipeline",
    "/operations/calendar",
    # Market pillar (unchanged in PR 1)
    "/market/rex",
    "/market/category",
    "/market/issuer",
    # SEC ETP pillar (new)
    "/sec/etp/",
    "/sec/etp/filings",
    "/sec/etp/leverageandinverse",
    # SEC Notes pillar (new)
    "/sec/notes/",
    "/sec/notes/filings",
    # SEC 13F (placeholder Coming Soon stubs)
    "/sec/13f/rex-report",
    "/sec/13f/market-report",
    "/sec/13f/institutions",
    "/sec/13f/country",
    # Tools pillar (new)
    "/tools/compare/etps",
    "/tools/compare/filings",
    "/tools/compare/notes",
    "/tools/compare/13f-inst",
    "/tools/compare/13f-products",
    "/tools/li/candidates",
    "/tools/simulators/autocall",
    "/tools/tickers",
    "/tools/calendar",
    # Detail surfaces (existing)
    "/funds/",
]

print("\n--- v3 URLs (expected 200) ---")
v3_fail = 0
for url in V3_URLS_200:
    r = client.get(url)
    status = r.status_code
    tag = "OK  " if status == 200 else f"FAIL"
    if status != 200:
        v3_fail += 1
    print(f"  [{tag}] {status} {url}")

# ============================================================
# Old URLs — should 301 to expected v3 targets
# ============================================================
LEGACY_REDIRECTS = [
    # filings
    ("/filings/", "/sec/etp/"),
    ("/filings/dashboard", "/sec/etp/"),
    ("/filings/explorer", "/sec/etp/filings"),
    ("/filings/landscape", "/sec/etp/leverageandinverse"),
    ("/filings/symbols", "/tools/tickers"),
    ("/filings/candidates", "/tools/li/candidates"),
    ("/filings/evaluator", "/tools/li/candidates"),
    ("/filings/hub", "/sec/etp/"),
    # notes
    ("/notes/", "/sec/notes/"),
    ("/notes/issuers", "/sec/notes/"),
    ("/notes/search", "/sec/notes/filings"),
    ("/notes/tools/autocall", "/tools/simulators/autocall"),
    # capm
    ("/capm/", "/operations/products"),
    # pipeline
    ("/pipeline/", "/operations/calendar"),
    ("/pipeline/products", "/operations/pipeline"),
    # calendar
    ("/calendar/", "/tools/calendar"),
    ("/market/calendar", "/tools/calendar"),
    # market/compare
    ("/market/compare", "/tools/compare/etps"),
    # screener
    ("/screener/", "/sec/etp/leverageandinverse"),
    ("/screener/3x-analysis", "/tools/li/candidates"),
    ("/screener/4x", "/tools/li/candidates"),
    ("/screener/evaluate", "/tools/li/candidates"),
    # analysis/filing rename
    ("/analysis/filing/628498", "/filings/628498"),
]

print("\n--- Legacy URLs (expected 301 to v3 target) ---")
redir_fail = 0
for old, expected_target in LEGACY_REDIRECTS:
    r = client.get(old)
    status = r.status_code
    location = r.headers.get("location", "")
    location_path = location.split("?")[0]
    if status in (301, 308) and location_path == expected_target:
        tag = "OK  "
    elif status in (301, 308) and location_path.startswith(expected_target):
        tag = "OK  "  # query-string preserved
    else:
        tag = "FAIL"
        redir_fail += 1
    print(f"  [{tag}] {status} {old:<40} -> {location}")

# ============================================================
# Summary
# ============================================================
print()
print(f"v3 200 fails:    {v3_fail} / {len(V3_URLS_200)}")
print(f"Legacy 301 fails: {redir_fail} / {len(LEGACY_REDIRECTS)}")
total_fail = v3_fail + redir_fail
if total_fail:
    print(f"\nTOTAL FAILS: {total_fail}")
    sys.exit(1)
print("\nALL PASS")
