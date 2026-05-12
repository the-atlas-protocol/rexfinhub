"""Template health guard tests.

Two regression tests born of the 2026-05-12 incident where a 0-byte
`webapp/templates/pipeline_products.html` (introduced by a bad auto-merge)
silently served 200 OK with an empty body on /operations/pipeline for ~12
hours. Jinja2's default ``Undefined`` renders missing context keys as
empty strings, so an empty template renders an empty response and never
trips any monitor.

1. ``test_no_zero_byte_templates`` — walk ``webapp/templates/**/*.html``
   and fail the build if any template is 0 bytes. Cheap, definitive,
   catches the exact failure mode that hit prod.

2. ``test_strict_undefined_active`` — assert the global ``templates``
   Jinja environment uses ``jinja2.StrictUndefined``, so any future
   regression of the env config is caught by CI.
"""
from __future__ import annotations

from pathlib import Path

import jinja2
import pytest


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "webapp" / "templates"


def test_no_zero_byte_templates() -> None:
    """Every .html under webapp/templates/ must be > 0 bytes.

    A 0-byte template renders as an empty 200 OK response — silent failure
    that bypasses every monitor we have. Treat empty templates as a build
    break.
    """
    assert TEMPLATES_DIR.exists(), f"Templates dir missing: {TEMPLATES_DIR}"

    empty: list[str] = []
    for path in TEMPLATES_DIR.rglob("*.html"):
        try:
            size = path.stat().st_size
        except OSError as exc:
            pytest.fail(f"Could not stat template {path}: {exc}")
        if size == 0:
            empty.append(str(path.relative_to(ROOT)))

    assert not empty, (
        "Found 0-byte template(s) — these render as silent empty 200s in prod. "
        "Restore content or delete the file:\n  " + "\n  ".join(empty)
    )


def test_strict_undefined_active() -> None:
    """The shared ``templates`` env must use StrictUndefined.

    Guards against accidentally reverting the audit fix that turns silent
    missing-context-key bugs into loud UndefinedError 500s.
    """
    # Skip cache prewarm at import — we only need the templates object.
    import webapp.main as _main_mod
    _main_mod._prewarm_caches = lambda: None  # type: ignore[assignment]

    from webapp.main import templates

    assert templates.env.undefined is jinja2.StrictUndefined, (
        "webapp.main.templates is not using StrictUndefined. "
        "Missing context keys will silently render as ''. "
        "Restore the StrictUndefined config in webapp/templates_init.py."
    )
