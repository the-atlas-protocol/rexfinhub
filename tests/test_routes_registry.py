"""CI lint — every key in webapp/routes.py ROUTES has a matching FastAPI route,
and zero deprecated URL prefixes appear in templates or JS.

Adopted as part of PR 4 to prevent post-migration drift.

Usage:
    cd C:/Projects/rexfinhub && python tests/test_routes_registry.py

Exit code 0 = clean. Non-zero = drift detected.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Skip cache prewarm at import
import webapp.main as _main_mod
_main_mod._prewarm_caches = lambda: None

from webapp.main import app
from webapp.routes import ROUTES, url


def _normalise(p: str) -> str:
    """Replace {param} with {} so registry templates and FastAPI paths compare."""
    return re.sub(r"\{[^}]+\}", "{}", p)


# ============================================================
# Test 1: ROUTES registry parity with FastAPI
# ============================================================
def test_routes_registry_parity() -> tuple[int, list[str]]:
    fastapi_paths: set[str] = set()
    for r in app.routes:
        if hasattr(r, "path"):
            fastapi_paths.add(_normalise(r.path))

    fails: list[str] = []
    for name, template in ROUTES.items():
        canonical = _normalise(template)
        if canonical not in fastapi_paths:
            fails.append(f"  ROUTES[{name!r}] = {template!r} -> no FastAPI handler")

    return len(ROUTES), fails


# ============================================================
# Test 2: url() helper resolves all keys
# ============================================================
_PATH_PARAM_FIXTURES = {
    "ticker": "NVDX", "series_id": "S000074123", "name": "BlackRock",
    "slug": "schwab-strategic-trust", "filing_id": "628498", "cik": "0001234567",
}

def test_url_helper_resolves() -> tuple[int, list[str]]:
    fails: list[str] = []
    for route_name, template in ROUTES.items():
        param_names = re.findall(r"\{(\w+)\}", template)
        kwargs = {p: _PATH_PARAM_FIXTURES.get(p, "TEST") for p in param_names}
        try:
            # url(route_name, **kwargs); kwargs may include 'name' for /issuers/{name}
            # which collides with url's first positional. Pass route_name positionally
            # and route the kwargs straight through.
            resolved = url(route_name, **kwargs)
            if any(f"{{{p}}}" in resolved for p in param_names):
                fails.append(f"  url({route_name!r}, {kwargs}) -> {resolved} (unsubstituted)")
        except Exception as e:
            fails.append(f"  url({route_name!r}) raised {type(e).__name__}: {e}")
    return len(ROUTES), fails


# ============================================================
# Test 3: no hardcoded old URLs in templates
# ============================================================
DEPRECATED_PATTERNS = [
    "/filings/dashboard", "/filings/explorer", "/filings/landscape",
    "/filings/symbols", "/filings/candidates", "/filings/evaluator",
    "/filings/hub", "/notes/issuers", "/notes/search",
    "/notes/tools/autocall", "/market/compare", "/market/calendar",
    "/market/fund/", "/market/issuer/detail", "/calendar/",
    "/analysis/filing/", "/pipeline/products",
]
# /capm/ deliberately excluded — appears in admin.py redirects + admin endpoints
# that are PR 5 cleanup target

def test_no_hardcoded_old_urls() -> tuple[int, list[str]]:
    template_root = ROOT / "webapp" / "templates"
    js_root = ROOT / "webapp" / "static" / "js"

    fails: list[str] = []
    n_files = 0
    for tmpl in template_root.rglob("*.html"):
        n_files += 1
        text = tmpl.read_text(encoding="utf-8", errors="replace")
        for pattern in DEPRECATED_PATTERNS:
            if pattern in text:
                rel = tmpl.relative_to(ROOT)
                fails.append(f"  {rel} contains {pattern!r}")
    for js in js_root.rglob("*.js"):
        n_files += 1
        text = js.read_text(encoding="utf-8", errors="replace")
        for pattern in DEPRECATED_PATTERNS:
            if pattern in text:
                rel = js.relative_to(ROOT)
                fails.append(f"  {rel} contains {pattern!r}")
    return n_files, fails


# ============================================================
# Run all
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Route registry + drift tests")
    print("=" * 60)

    n1, fails1 = test_routes_registry_parity()
    print(f"\n[1] Routes registry parity: {n1} keys checked")
    if fails1:
        print(f"    FAIL ({len(fails1)} drift):")
        for f in fails1:
            print(f)
    else:
        print("    OK — every ROUTES key has a FastAPI handler")

    n2, fails2 = test_url_helper_resolves()
    print(f"\n[2] url() helper resolves: {n2} keys checked")
    if fails2:
        print(f"    FAIL ({len(fails2)}):")
        for f in fails2:
            print(f)
    else:
        print("    OK — all keys resolve with fixture params")

    n3, fails3 = test_no_hardcoded_old_urls()
    print(f"\n[3] No hardcoded old URLs: {n3} files scanned")
    if fails3:
        print(f"    FAIL ({len(fails3)} hits):")
        for f in fails3:
            print(f)
    else:
        print("    OK — no deprecated URL prefixes in templates / JS")

    total_fails = len(fails1) + len(fails2) + len(fails3)
    print("\n" + "=" * 60)
    if total_fails:
        print(f"FAILED: {total_fails} drift items")
        sys.exit(1)
    print("PASSED")
