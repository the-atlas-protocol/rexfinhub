"""
Centralized Jinja2 environment factory.

Single source of truth for how `Jinja2Templates` is configured across the
webapp. Created in response to the 2026-05-12 incident where a 0-byte
`pipeline_products.html` (introduced by a bad auto-merge) silently served
200 OK with an empty body for ~12 hours on /operations/pipeline.

The default Jinja2 ``Undefined`` class renders missing context keys as
empty strings, which means an empty template renders an empty response —
no exception, no log entry, no monitoring trip.

Switching to ``StrictUndefined`` raises ``UndefinedError`` the moment a
template references a key the route handler did not pass, turning a class
of silent rendering bugs into loud 500s that surface in logs and on
``/admin/health``.

Usage:
    from webapp.templates_init import build_templates
    templates = build_templates()

Notes:
    - ``StrictUndefined`` does NOT change the meaning of ``{% if foo %}``
      when ``foo`` is passed-but-falsy (None, "", 0, []). It only fires
      when ``foo`` was never passed to the context. Routes that always
      pass the key — even as None — keep working unchanged.
    - Routers that instantiate their own ``Jinja2Templates`` directly do
      NOT receive these settings. The canonical pattern going forward is
      ``from webapp.main import templates``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined


WEBAPP_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = WEBAPP_DIR / "templates"


def build_templates(directory: str | Path | None = None) -> Jinja2Templates:
    """Return a ``Jinja2Templates`` instance configured for the rexfinhub webapp.

    Args:
        directory: Optional override for the template directory. Defaults to
            ``webapp/templates`` resolved from this file's location.

    Returns:
        A ``Jinja2Templates`` whose underlying environment has:
            - ``undefined=StrictUndefined`` — missing context keys raise
              ``jinja2.UndefinedError`` instead of rendering as "".
            - ``auto_reload=False`` in production (when ``RENDER`` env var
              is set). Left at Starlette's default elsewhere so local dev
              still picks up template edits without a server restart.
            - ``trim_blocks=True`` and ``lstrip_blocks=True`` for cleaner
              rendered HTML (no stray newlines around block tags).
    """
    import os

    target_dir = Path(directory) if directory is not None else _TEMPLATES_DIR

    templates = Jinja2Templates(directory=str(target_dir))
    env = templates.env
    env.undefined = StrictUndefined
    env.trim_blocks = True
    env.lstrip_blocks = True
    if os.environ.get("RENDER"):
        env.auto_reload = False
    return templates
