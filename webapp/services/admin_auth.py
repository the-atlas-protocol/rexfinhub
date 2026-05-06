"""Shared admin authentication helper.

Single source of truth for loading ADMIN_PASSWORD from config/.env or the
environment. All admin routers import from here so that rotating the
password in .env is sufficient — no source files contain the literal value.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_admin_password() -> str:
    """Load ADMIN_PASSWORD from config/.env, then from environment.

    Returns an empty string if neither source provides a value (callers
    treat empty string as "no valid password configured").
    """
    env_file = _PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key.strip() == "ADMIN_PASSWORD":
                    return val.strip().strip('"').strip("'")
    return os.environ.get("ADMIN_PASSWORD", "")
