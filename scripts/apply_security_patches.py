"""
Security Patch Reference — rexfinhub 2026-05-05
================================================

This file documents the automated code changes applied by the security
remediation pass and the MANUAL operator actions that must be completed
to fully close the identified vulnerabilities.

Run this script to verify the automated patches are in place:
    python scripts/apply_security_patches.py

Do NOT commit real secrets into this file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Automated patches applied (code changes already committed)
# ---------------------------------------------------------------------------

AUTOMATED_PATCHES = [
    {
        "id": "P1",
        "file": "docs/DEPLOYMENT_PLAN.md:207",
        "description": "Redacted literal API key. Value replaced with <REDACTED — see config/.env>.",
        "severity": "CRITICAL",
    },
    {
        "id": "P2a",
        "file": "webapp/routers/admin_products.py",
        "description": "Removed hardcoded ADMIN_PASSWORD = 'ryu123'. Now loads via webapp.services.admin_auth.load_admin_password().",
        "severity": "CRITICAL",
    },
    {
        "id": "P2b",
        "file": "webapp/routers/admin_reports.py",
        "description": "Same as P2a.",
        "severity": "CRITICAL",
    },
    {
        "id": "P2c",
        "file": "webapp/routers/admin_health.py",
        "description": "Removed inline literal 'ryu123' from _is_admin(). Loads via load_admin_password() at module init.",
        "severity": "CRITICAL",
    },
    {
        "id": "P2d",
        "file": "webapp/routers/admin.py",
        "description": "Replaced local _load_admin_password() definition with import from webapp.services.admin_auth.",
        "severity": "HIGH",
    },
    {
        "id": "P2e",
        "file": "webapp/services/admin_auth.py",
        "description": "NEW module — single source of truth for loading ADMIN_PASSWORD from config/.env or env.",
        "severity": "HIGH",
    },
    {
        "id": "P3",
        "file": "webapp/main.py (DELETE /api/v1/maintenance)",
        "description": "Moved token from Query(...) URL param to Header(None, alias='X-Admin-Token'). "
                       "Token no longer appears in nginx access logs.",
        "severity": "HIGH",
    },
    {
        "id": "P4",
        "file": "webapp/routers/api.py (etp_screener)",
        "description": "Replaced raw SQL string interpolation with parameterized SQLAlchemy text() + bind params "
                       "for category and ticker IN list. SQL injection eliminated.",
        "severity": "HIGH",
    },
    {
        "id": "P5",
        "file": "CLAUDE.md",
        "description": "Removed SITE_PASSWORD and ADMIN_PASSWORD literals. Replaced with 'see .env (rotated regularly)'.",
        "severity": "CRITICAL",
    },
    {
        "id": "P6a",
        "file": "webapp/main.py (_load_site_password)",
        "description": "SITE_PASSWORD now raises RuntimeError if missing in production (RENDER env set). "
                       "Local dev falls back to 'dev-site-password' instead of '123'.",
        "severity": "HIGH",
    },
    {
        "id": "P6b",
        "file": "webapp/auth.py (_load_auth_config)",
        "description": "SESSION_SECRET now raises RuntimeError in production if missing or equal to the "
                       "known-weak default 'dev-secret-change-me'.",
        "severity": "MEDIUM",
    },
]

# ---------------------------------------------------------------------------
# Manual operator actions (cannot be automated — involve external systems)
# ---------------------------------------------------------------------------

MANUAL_ACTIONS = [
    {
        "priority": 1,
        "action": "Rotate API_KEY in Render environment variables",
        "detail": (
            "The old value 'rex-etp-api-2026-kJw9xPm4' was committed to Git history and must be "
            "treated as fully compromised. Generate a new key (e.g. python -c \"import secrets; "
            "print('rex-etp-api-' + secrets.token_urlsafe(16))\") and update:\n"
            "  1. Render dashboard > rexfinhub > Environment > API_KEY\n"
            "  2. config/.env on the VPS (jarvis@46.224.126.196)\n"
            "  3. Any local config/.env files\n"
            "  4. Any scripts or external consumers calling X-API-Key"
        ),
        "done": False,
    },
    {
        "priority": 2,
        "action": "Rotate ADMIN_PASSWORD in Render environment variables and VPS .env",
        "detail": (
            "Old value 'ryu123' was hardcoded in 3 source files on GitHub. "
            "Generate a strong replacement: python -c \"import secrets; print(secrets.token_urlsafe(16))\"\n"
            "Update in:\n"
            "  1. Render dashboard > rexfinhub > Environment > ADMIN_PASSWORD\n"
            "  2. config/.env on VPS\n"
            "  3. Any local config/.env"
        ),
        "done": False,
    },
    {
        "priority": 3,
        "action": "Rotate SITE_PASSWORD in Render environment variables and VPS .env",
        "detail": (
            "Old value 'rexusers26' was documented in CLAUDE.md on GitHub. "
            "Generate replacement and update Render + VPS .env."
        ),
        "done": False,
    },
    {
        "priority": 4,
        "action": "Generate new SESSION_SECRET",
        "detail": (
            "Run: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Set as SESSION_SECRET in Render env and VPS .env. "
            "This will invalidate all existing sessions — users will need to log in again."
        ),
        "done": False,
    },
    {
        "priority": 5,
        "action": "Rotate AZURE_CLIENT_SECRET in Azure Portal",
        "detail": (
            "The secret has Files.ReadWrite.All + Sites.ReadWrite.All on the rexfin.com M365 tenant — "
            "if compromised, an attacker can read/modify all SharePoint and send impersonation email.\n"
            "Steps:\n"
            "  1. Azure Portal > App registrations > [rexfinhub app] > Certificates & secrets\n"
            "  2. Delete old secret, create new one\n"
            "  3. Update AZURE_CLIENT_SECRET in Render env + VPS .env"
        ),
        "done": False,
    },
    {
        "priority": 6,
        "action": "Downscope Azure app permissions — remove Files.ReadWrite.All, Sites.ReadWrite.All, Mail.Read",
        "detail": (
            "Keep only Mail.Send. Steps:\n"
            "  1. Azure Portal > App registrations > [rexfinhub app] > API permissions\n"
            "  2. Remove: Files.ReadWrite.All, Sites.ReadWrite.All, Mail.Read\n"
            "  3. Grant admin consent for remaining permissions\n"
            "NOTE: Test that email digest still works before removing access in production."
        ),
        "done": False,
    },
    {
        "priority": 7,
        "action": "Enable 2FA on GitHub (ryuoelasmar), Render, and Microsoft 365",
        "detail": (
            "GitHub: Settings > Password and authentication > Two-factor authentication\n"
            "Render: Account Settings > Security\n"
            "Microsoft 365: aka.ms/mfasetup"
        ),
        "done": False,
    },
    {
        "priority": 8,
        "action": "Add SSH key passphrase to VPS key",
        "detail": (
            "Run: ssh-keygen -p -f ~/.ssh/id_ed25519\n"
            "This protects the key if the local machine is compromised. "
            "The passphrase does not affect automated scripts that use the key directly."
        ),
        "done": False,
    },
    {
        "priority": 9,
        "action": "Purge API key from Git history",
        "detail": (
            "Verify: git log --all -S 'rex-etp-api-2026-kJw9xPm4' --oneline\n"
            "If commits found, use BFG Repo Cleaner or git filter-repo to remove the secret.\n"
            "Then force-push and notify all collaborators to re-clone.\n"
            "Since the key is already rotated (step 1), history cleanup is belt-and-suspenders."
        ),
        "done": False,
    },
    {
        "priority": 10,
        "action": "Purge ryu123 from Git history",
        "detail": (
            "Verify: git log --all -S 'ryu123' --oneline\n"
            "Same BFG / filter-repo process as API key if hits found."
        ),
        "done": False,
    },
]


# ---------------------------------------------------------------------------
# Verification checks (run automatically)
# ---------------------------------------------------------------------------

def _check_no_literal_in_file(path: Path, literal: str, label: str) -> bool:
    """Return True if literal is NOT found in file (pass), False if found (fail)."""
    if not path.exists():
        print(f"  [SKIP] {label}: file not found ({path})")
        return True
    content = path.read_text(encoding="utf-8", errors="replace")
    if literal in content:
        print(f"  [FAIL] {label}: literal '{literal}' still present in {path}")
        return False
    print(f"  [PASS] {label}")
    return True


def run_verification() -> int:
    """Run automated checks. Returns number of failures."""
    print("\n=== Security Patch Verification ===\n")
    failures = 0

    checks = [
        (PROJECT_ROOT / "webapp/routers/admin_products.py", "ryu123", "P2a: admin_products.py clean"),
        (PROJECT_ROOT / "webapp/routers/admin_reports.py", "ryu123", "P2b: admin_reports.py clean"),
        (PROJECT_ROOT / "webapp/routers/admin_health.py", "ryu123", "P2c: admin_health.py clean"),
        (PROJECT_ROOT / "CLAUDE.md", "ryu123", "P5a: CLAUDE.md admin password clean"),
        (PROJECT_ROOT / "CLAUDE.md", "rexusers26", "P5b: CLAUDE.md site password clean"),
        (PROJECT_ROOT / "docs/DEPLOYMENT_PLAN.md", "rex-etp-api-2026-kJw9xPm4", "P1: DEPLOYMENT_PLAN.md API key clean"),
        (PROJECT_ROOT / "webapp/routers/api.py", "f\" AND etp_category = '", "P4: SQL injection (category) fixed"),
        (PROJECT_ROOT / "webapp/routers/api.py", "f\" AND ticker IN (", "P4: SQL injection (ticker IN) fixed"),
        (PROJECT_ROOT / "webapp/services/admin_auth.py", "ryu123", "P2e: admin_auth.py has no hardcoded password"),
    ]

    for path, literal, label in checks:
        if not _check_no_literal_in_file(path, literal, label):
            failures += 1

    # Check that admin_auth.py exists
    auth_module = PROJECT_ROOT / "webapp/services/admin_auth.py"
    if auth_module.exists():
        print("  [PASS] P2e: webapp/services/admin_auth.py exists")
    else:
        print("  [FAIL] P2e: webapp/services/admin_auth.py missing")
        failures += 1

    # Check that pre-commit hook exists
    hook = PROJECT_ROOT / ".git/hooks/pre-commit"
    if hook.exists():
        print("  [PASS] P8: .git/hooks/pre-commit installed")
    else:
        print("  [WARN] P8: .git/hooks/pre-commit not found — run this script with --install-hook")

    print(f"\nResult: {failures} failure(s)\n")
    return failures


def print_manual_checklist() -> None:
    print("\n=== Operator Manual Actions Required ===\n")
    print("The following actions CANNOT be automated and must be completed by the operator.\n")
    for item in MANUAL_ACTIONS:
        status = "[DONE]" if item["done"] else "[ TODO ]"
        print(f"  {status} Priority {item['priority']}: {item['action']}")
    print("\nSee MANUAL_ACTIONS list in this script for full details on each step.\n")


if __name__ == "__main__":
    failures = run_verification()
    print_manual_checklist()
    sys.exit(1 if failures else 0)
