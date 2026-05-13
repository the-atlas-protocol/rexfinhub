"""IPO Intel page — pre-IPO + recently-priced IPO watchlist.

Renders ``config/ipo_watchlist.yaml`` as a sortable table at /intel/ipo.
The YAML is the single source of truth (also consumed by the Stock Recs
weekly report). No DB writes; refresh by editing the YAML and reloading.

Mounted under /intel/ipo.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/intel", tags=["intel"])
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_YAML_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "ipo_watchlist.yaml"


def _load_yaml() -> dict:
    if not _YAML_PATH.exists():
        return {"high_profile_pre_ipo": [], "recent_ipos": [], "filed_s1": []}
    with open(_YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _file_age_hours() -> float:
    if not _YAML_PATH.exists():
        return -1.0
    import time
    return (time.time() - _YAML_PATH.stat().st_mtime) / 3600.0


@router.get("/ipo", response_class=HTMLResponse)
def ipo_intel(request: Request):
    data = _load_yaml()
    sections = []
    for key, label in [
        ("high_profile_pre_ipo", "High-profile pre-IPO"),
        ("recent_ipos", "Recently priced"),
        ("filed_s1", "S-1 filed"),
    ]:
        rows = data.get(key) or []
        if rows:
            # Sort by valuation_usd desc, then by company
            rows = sorted(
                rows,
                key=lambda r: (-(r.get("valuation_usd") or 0), (r.get("company") or "")),
            )
            sections.append({"key": key, "label": label, "rows": rows})

    return _TEMPLATES.TemplateResponse("ipo_intel.html", {
        "request": request,
        "sections": sections,
        "data_age_hours": f"{_file_age_hours():.1f}",
        "yaml_path": str(_YAML_PATH.relative_to(_YAML_PATH.parent.parent.parent)),
    })
