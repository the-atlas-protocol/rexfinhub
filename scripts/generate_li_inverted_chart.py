"""One-off: render L&I Single Stock competitive chart with REX at the bottom.

Produces a single PNG (and PDF) suitable for slide use.

Usage:
    python scripts/generate_li_inverted_chart.py
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_market_share_charts import _load, _chart_comp, _BG


CATEGORY_DB = "Leverage & Inverse - Single Stock"
LABEL = "L&I Single Stock"


def main():
    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)

    print(f"Loading {LABEL}...", flush=True)
    df = _load(CATEGORY_DB)
    if df.empty:
        print("No data."); return 1

    dates = df["as_of_date"].dropna()
    as_of = dates.max() if not dates.empty else datetime.now().date()
    if hasattr(as_of, "date"):
        as_of = as_of.date() if not callable(as_of.date) else as_of
    ds = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)

    fig = _chart_comp(df, as_of, LABEL, rex_bottom=True)
    out_png = out_dir / f"li_ss_competitive_REX-bottom_{ds}.png"
    out_pdf = out_dir / f"li_ss_competitive_REX-bottom_{ds}.pdf"
    fig.savefig(str(out_png), dpi=250, bbox_inches="tight", facecolor=_BG)
    fig.savefig(str(out_pdf), bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    print(f"  => {out_png.name}")
    print(f"  => {out_pdf.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
