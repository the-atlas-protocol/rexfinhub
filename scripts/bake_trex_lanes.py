"""Bake the T-REX System lane HTML to a static artifact.

The lane build (esp. the foreign / pre-IPO filer race) is too slow to run
synchronously inside a web request. This bakes ``build_all()`` to
``outputs/trex_lanes.json`` off-request (fast loop / schedule / manual), and the
/tools/li/candidates page serves that artifact — decoupling serving from compute.

Usage:  python scripts/bake_trex_lanes.py
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = PROJECT_ROOT / "outputs" / "trex_lanes.json"
LANE_ORDER = ["pipeline", "whitespace", "inverse", "launch_anyway", "foreign", "ipo"]


def main() -> int:
    from screener.li_engine.report import lanes as _lanes
    built = _lanes.build_all()
    ctx = built.get("_ctx")
    payload = {
        "generated_at": getattr(ctx, "generated_at", "") if ctx else "",
        "lanes": {
            k: built[k]["html"]
            for k in LANE_ORDER
            if isinstance(built.get(k), dict) and built[k].get("html")
        },
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARTIFACT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(ARTIFACT)  # atomic
    print(f"baked {len(payload['lanes'])} lanes -> {ARTIFACT} (generated_at={payload['generated_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
