"""Bake the downloadable T-REX report variants to PDF (VPS-side).

Renders every variant in ``screener.li_engine.report.pdf.VARIANTS`` to
``outputs/trex/<variant>.pdf`` (stable name the download endpoint serves) plus a
dated archive copy. Runs on the VPS where Chromium/Playwright is available; the
Render web server only serves the baked files. Hooked into the fast/daily loop.

Usage:
    python scripts/bake_trex_pdfs.py            # all variants
    python scripts/bake_trex_pdfs.py foreign    # one variant
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "outputs" / "trex"


def main(argv: list[str]) -> int:
    from screener.li_engine.report import lanes as _lanes
    from screener.li_engine.report import pdf as _pdf
    from scripts.send_all import _render_report_pdf

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    want = [a.strip() for a in argv if a.strip()] or list(_pdf.VARIANTS)
    unknown = [v for v in want if v not in _pdf.VARIANTS]
    if unknown:
        print(f"unknown variant(s): {unknown}; valid: {list(_pdf.VARIANTS)}")
        return 2

    # One lane build feeds every variant (a single DB/parquet pass).
    built = _lanes.build_all()
    today = date.today().isoformat()
    ok, failed = 0, 0
    for variant in want:
        try:
            html = _pdf.build_variant_html(variant, built)
            data = _render_report_pdf(html)
            if not data:
                print(f"  FAIL  {variant}: render returned no bytes")
                failed += 1
                continue
            stable = OUT_DIR / f"{variant}.pdf"
            dated = OUT_DIR / f"{variant}_{today}.pdf"
            stable.write_bytes(data)
            dated.write_bytes(data)
            print(f"  OK    {variant:14s} {len(data):>8,} bytes -> {stable}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - one bad variant must not abort the rest
            print(f"  FAIL  {variant}: {exc}")
            failed += 1

    print(f"baked {ok} variant(s), {failed} failed")
    return 1 if failed and ok == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
