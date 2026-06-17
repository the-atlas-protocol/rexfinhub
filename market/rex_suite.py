"""Back-compat shim. The canonical REX suite classifier now lives in the full
definition library at market/definitions.py — import from there. This module
re-exports so existing call sites keep working with one underlying definition."""
from __future__ import annotations

from market.definitions import suite_for_name, suite_of  # noqa: F401
