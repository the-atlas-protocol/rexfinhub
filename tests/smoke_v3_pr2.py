"""PR 2 smoke test — verify all 5 detail surfaces + index pages work end-to-end.

Surfaces tested:
  /funds/{ticker}        — merged DES screen + SEC breadcrumb
  /funds/series/{id}     — filed-only fallback
  /issuers/{name}        — canonical issuer rollup (with read-side canon)
  /issuers/              — browse-all index
  /stocks/{ticker}       — Bloomberg DES-style data dump
  /market/stocks/        — browse-all index
  /trusts/{slug}         — SEC trust entity (existing, verified)
  /trusts/               — browse-all index (NEW)
  /filings/{filing_id}   — single filing (renamed from /analysis/filing/{id})

Plus the legacy redirects from PR 2:
  /funds/{series_id}        -> /funds/{ticker} or /funds/series/{id}
  /market/fund/{ticker}     -> /funds/{ticker}
  /market/issuer/detail?... -> /issuers/{name}
  /analysis/filing/{id}     -> /filings/{id}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

env_file = ROOT / "config" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Skip cache prewarm for fast startup
import webapp.main as _main_mod
_main_mod._prewarm_caches = lambda: None

from fastapi.testclient import TestClient
from webapp.main import app

client = TestClient(app, follow_redirects=False)
pw = os.environ.get("SITE_PASSWORD", "dev-site-password")
r = client.post("/login", data={"password": pw, "next": "/"})
assert r.status_code in (302, 303), f"Login failed: {r.status_code}"
print("AUTH    OK")

# ============================================================
# Detail surfaces — should return 200 (or 301 if redirected to canonical)
# ============================================================
DETAIL_SURFACES = [
    # /funds/{ticker} — live tickers
    ("/funds/NVDX",    [200]),
    ("/funds/DJTU",    [200]),
    ("/funds/JEPI",    [200]),
    ("/funds/NVDL",    [200]),  # competitor, bbg-only
    # /funds/series/{id} — filed-only fallback
    ("/funds/series/S000074123",  [200, 301]),  # may 301 to ticker if assigned
    # /issuers/
    ("/issuers/",         [200]),
    ("/issuers/BlackRock", [200]),
    ("/issuers/REX",      [200]),
    # /stocks/
    ("/stocks/NVDA",      [200]),
    ("/stocks/MSTR",      [200]),
    ("/stocks/UNKNOWN_TICKER_XYZ", [200]),  # graceful "no data" page
    ("/market/stocks/",   [200]),
    # /trusts/
    ("/trusts/",                     [200]),
    ("/trusts/schwab-strategic-trust", [200]),
    # /filings/{id}
    ("/filings/628498",   [200]),
]

print("\n--- Detail surfaces (expected 200 / acceptable 301) ---")
ds_fail = 0
for url, ok_codes in DETAIL_SURFACES:
    r = client.get(url)
    status = r.status_code
    tag = "OK  " if status in ok_codes else "FAIL"
    if status not in ok_codes:
        ds_fail += 1
    extra = ""
    if status == 301:
        extra = f" -> {r.headers.get('location', '')}"
    print(f"  [{tag}] {status} {url}{extra}")

# ============================================================
# Legacy redirects (should 301 to v3 detail surfaces)
# ============================================================
LEGACY = [
    # /funds/{series_id} -> /funds/{ticker} or /funds/series/{id}
    ("/funds/S000074123", "/funds/"),  # accept any /funds/ target (ticker or series)
    # /market/fund/{ticker} -> /funds/{ticker}
    ("/market/fund/NVDX", "/funds/NVDX"),
    # /market/issuer/detail -> /issuers/{name}
    ("/market/issuer/detail?issuer=BlackRock", "/issuers/BlackRock"),
    ("/market/issuer/detail", "/issuers/"),
    # /analysis/filing/{id} -> /filings/{id}
    ("/analysis/filing/628498", "/filings/628498"),
]

print("\n--- Legacy redirects (expected 301 to v3 target) ---")
redir_fail = 0
for old, expected_prefix in LEGACY:
    r = client.get(old)
    status = r.status_code
    location = r.headers.get("location", "")
    location_path = location.split("?")[0]
    if status in (301, 308) and location_path.startswith(expected_prefix.rstrip("/")):
        tag = "OK  "
    else:
        tag = "FAIL"
        redir_fail += 1
    print(f"  [{tag}] {status} {old:<55} -> {location}")

# ============================================================
# Issuer canonicalization deep check
# ============================================================
print("\n--- Issuer canonicalization sanity ---")
from webapp.database import SessionLocal
from webapp.services.market_data import get_master_data, _get_issuer_canon_map
db = SessionLocal()
try:
    canon_map = _get_issuer_canon_map()
    print(f"  canon map loaded: {len(canon_map)} variant->canonical entries")
    df = get_master_data(db)
    if not df.empty and "issuer_display" in df.columns:
        n_blackrock = (df["issuer_display"] == "BlackRock").sum()
        n_ishares = (df["issuer_display"] == "iShares").sum()
        n_rex = (df["issuer_display"] == "REX").sum()
        print(f"  BlackRock funds (canonicalized): {n_blackrock}")
        print(f"  iShares funds (canonicalized):   {n_ishares}")
        print(f"  REX funds (canonicalized):       {n_rex}")
finally:
    db.close()

# ============================================================
# Summary
# ============================================================
total_fail = ds_fail + redir_fail
print(f"\nDetail surfaces: {len(DETAIL_SURFACES) - ds_fail}/{len(DETAIL_SURFACES)} OK")
print(f"Legacy redirects: {len(LEGACY) - redir_fail}/{len(LEGACY)} OK")
if total_fail:
    print(f"\nTOTAL FAILS: {total_fail}")
    sys.exit(1)
print("\nALL PASS")
