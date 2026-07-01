"""Print-ready HTML for the downloadable T-REX report variants.

Each variant wraps one or more lane sections (from :mod:`lanes`) in a landscape
A4 print document styled like the foreign-2x PDF — repeating table headers,
break-inside avoidance, a title block, and a methodology footer note. The
actual PDF bytes are produced by Playwright in ``scripts/bake_trex_pdfs.py``
(the VPS has Chromium; the Render web server does not), and the download
endpoint serves the baked file.
"""
from __future__ import annotations

from datetime import date

from screener.li_engine.report import lanes as _lanes

# variant key -> (document title, [lane keys in order])
VARIANTS: dict[str, tuple[str, list[str]]] = {
    "pipeline":      ("REX Pipeline — Filed, Awaiting Launch", ["pipeline"]),
    "whitespace":    ("Filing Whitespace — Scored Open Lanes", ["whitespace"]),
    "inverse":       ("Inverse Gap — Long-Without-Inverse", ["inverse"]),
    "launch_anyway": ("Launch-Anyway — Proven Demand, Room to Enter", ["launch_anyway"]),
    "foreign":       ("Foreign-Listed 2x Map", ["foreign"]),
    "ipo":           ("Pre-IPO 2x Targets", ["ipo"]),
    "all":           ("T-REX System — Full Candidate Map",
                      ["pipeline", "whitespace", "inverse", "launch_anyway", "foreign", "ipo"]),
}

_PRINT_CSS = """
@page { size: A4 landscape; margin: 9mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }
body { font-family:-apple-system,Segoe UI,Arial,sans-serif; color:#0f172a; margin:0; }
table { width:100%; border-collapse:collapse; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
h1, h2 { break-after: avoid; page-break-after: avoid; }
section { break-inside: auto; }
"""


def build_variant_html(variant: str, built: dict | None = None) -> str:
    """Full print-ready HTML document for one download variant."""
    if variant not in VARIANTS:
        raise KeyError(f"unknown variant {variant!r} (have {list(VARIANTS)})")
    title, lane_keys = VARIANTS[variant]
    built = built or _lanes.build_all()
    ctx = built.get("_ctx")
    generated_at = getattr(ctx, "generated_at", "") if ctx else ""
    sections = "".join(
        built[k]["html"] for k in lane_keys
        if isinstance(built.get(k), dict) and built[k].get("html")
    )
    intro = (
        "The full T-REX opportunity map." if variant == "all"
        else "One focused lane of the T-REX System."
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>{_PRINT_CSS}</style></head>
<body style="max-width:1180px;margin:14px auto;padding:0 18px;">
<h1 style="font-size:21px;margin:0 0 2px;">{title}</h1>
<div style="color:#7f8c8d;font-size:12px;margin-bottom:4px;">REX Financial Intelligence Hub · {intro}
 Scored on the frozen v1.0.1 model. Foreign / pre-IPO dates are the L&amp;I filer race's effective dates.</div>
<div style="color:#7f8c8d;font-size:11px;margin-bottom:12px;">Data refreshed: {generated_at} · Generated {date.today().isoformat()}</div>
{sections}
</body></html>"""


def build_all_variants_html(built: dict | None = None) -> dict[str, str]:
    """Render every variant's HTML from a single lane build (one DB pass)."""
    built = built or _lanes.build_all()
    return {v: build_variant_html(v, built) for v in VARIANTS}
