"""REX product-suite AUM growth charts since inception.

Six charts:
  - EPI               : FEPI, AIPI, CEPI                              (US)
  - EPI Europe        : FEPI LN, FEGI LN, CEGI LN                     (UCITS)
  - Growth & Income   : NVII, TSII, COII, MSII, PLTI, LLII, WMTI,
                        CWII, HOII, GIF                               (10 products)
  - IncomeMax         : ULTI                                          (single)
  - Thematic          : DRNZ                                          (single)
  - Structured        : ATCL                                          (single)

Outputs:
  reports/aum_growth_<slug>_<date>.png        per-suite PNG
  reports/aum_growth_suite_<date>.pdf         multi-page PDF (all 6)
  reports/aum_growth_suite_<date>.html        Outlook-pasteable HTML

Usage:
    python scripts/generate_aum_growth_charts.py
"""
from __future__ import annotations
import sys, io, base64
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.database import SessionLocal
from webapp.models import MktTimeSeries, MktMasterData
from sqlalchemy import select

# ----------------------------------------------------------------------
# Suite definitions — DB ticker form (with ' US' / ' LN' suffix)
# ----------------------------------------------------------------------
SUITES = [
    {
        "slug": "epi",
        "label": "EPI Suite (US)",
        "subtitle": "Equity Premium Income — FEPI / AIPI / CEPI",
        "tickers": ["FEPI US", "AIPI US", "CEPI US"],
    },
    {
        "slug": "epi_eu",
        "label": "EPI Suite (Europe / UCITS)",
        "subtitle": "London-listed UCITS — FEPI LN / FEGI LN / CEGI LN",
        "tickers": ["FEPI LN", "FEGI LN", "CEGI LN"],
    },
    {
        "slug": "gi_suite",
        "label": "Growth & Income Suite",
        "subtitle": "10 products — NVII / TSII / COII / MSII / PLTI / LLII / WMTI / CWII / HOII / GIF",
        "tickers": ["NVII US","TSII US","COII US","MSII US","PLTI US",
                    "LLII US","WMTI US","CWII US","HOII US","GIF US"],
    },
    {
        "slug": "incomemax",
        "label": "IncomeMax",
        "subtitle": "ULTI",
        "tickers": ["ULTI US"],
    },
    {
        "slug": "thematic",
        "label": "Thematic",
        "subtitle": "DRNZ",
        "tickers": ["DRNZ US"],
    },
    {
        "slug": "structured",
        "label": "Structured",
        "subtitle": "ATCL",
        "tickers": ["ATCL US"],
    },
]

# ----------------------------------------------------------------------
# Palette (matches generate_market_share_charts.py)
# ----------------------------------------------------------------------
_NAVY  = "#1a1a2e"
_BLUE  = "#0984e3"
_RED   = "#d63031"
_GRAY  = "#b2bec3"
_LIGHT = "#dfe6e9"
_BG    = "#ffffff"

# Brighter palette for stacking — high-contrast, print-safe
_PAL = ["#0984e3", "#e8913a", "#5ea66b", "#9b6dc4", "#d15555",
        "#4db8a8", "#c47a5a", "#3d7ec7", "#7a8c6e", "#b87a3d"]


def _fb(v, escape=False):
    """Format $ in B / M / K. escape=True for matplotlib (no mathtext)."""
    d = r"\$" if escape else "$"
    if abs(v) >= 1000:
        return f"{d}{v / 1000:.2f}B"
    if abs(v) >= 1:
        return f"{d}{v:.0f}M"
    return f"{d}{v * 1000:.0f}K"


def _style(ax, fig):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(_LIGHT)
    ax.spines["bottom"].set_color(_LIGHT)
    ax.tick_params(colors=_NAVY, labelsize=8)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)


# ----------------------------------------------------------------------
# Data load — per-ticker, from inception, with pre-inception zeroed
# ----------------------------------------------------------------------
def _load_suite(tickers):
    """Return DataFrame[ticker, date, aum_m, fund_name] long-form, post-inception only."""
    db = SessionLocal()
    rows = db.execute(
        select(
            MktTimeSeries.ticker,
            MktTimeSeries.months_ago,
            MktTimeSeries.aum_value,
            MktTimeSeries.as_of_date,
        ).where(MktTimeSeries.ticker.in_(tickers))
    ).all()
    incep_rows = db.execute(
        select(MktMasterData.ticker, MktMasterData.inception_date, MktMasterData.fund_name)
    ).all()
    db.close()

    if not rows:
        return pd.DataFrame()

    # inception lookup — DB stores ticker without suffix, MktTimeSeries stores 'X US'
    incep = {}
    fund_name = {}
    for r in incep_rows:
        t = r[0]
        if r[1] is not None:
            incep[t] = r[1]
        if r[2]:
            fund_name[t] = r[2]

    df = pd.DataFrame(rows, columns=["ticker", "months_ago", "aum_m", "as_of_date"])
    df["aum_m"] = df["aum_m"].fillna(0)

    # Use latest as_of_date as anchor
    dates = df["as_of_date"].dropna()
    as_of = dates.max() if not dates.empty else datetime.now().date()
    if hasattr(as_of, "date") and callable(as_of.date):
        as_of = as_of.date()

    # Compute month_date and zero out pre-inception
    out = []
    for _, row in df.iterrows():
        full = row["ticker"]
        bare = full.split(" ")[0]  # 'FEPI US' -> 'FEPI'
        # MktMasterData.ticker stores the full suffixed form; fall back to bare just in case
        inc_raw = incep.get(full) or incep.get(bare)
        if inc_raw is None:
            inc_date = None
        elif isinstance(inc_raw, str):
            if inc_raw.startswith("NaT") or not inc_raw.strip():
                inc_date = None
            else:
                inc_date = datetime.strptime(inc_raw[:10], "%Y-%m-%d").date()
        elif hasattr(inc_raw, "date"):
            inc_date = inc_raw.date()
        else:
            inc_date = inc_raw

        month_date = as_of - relativedelta(months=int(row["months_ago"]))
        if inc_date and month_date < inc_date:
            continue  # drop pre-inception entries entirely

        out.append({
            "ticker": full,
            "bare": bare,
            "fund_name": fund_name.get(full) or fund_name.get(bare, bare),
            "date": month_date,
            "aum_m": float(row["aum_m"]),
            "inception": inc_date,
        })

    return pd.DataFrame(out)


# ----------------------------------------------------------------------
# Chart — stacked area for multi-ticker, single area for single-ticker
# ----------------------------------------------------------------------
def _chart_suite(df, suite):
    if df.empty:
        return None

    # Pivot: rows=date, cols=ticker, values=aum_m. Sort by inception date so
    # earliest fund is at the bottom of the stack.
    incep_order = (df.groupby("ticker")["inception"]
                   .min()
                   .sort_values()
                   .index.tolist())
    piv = (df.pivot_table(index="date", columns="ticker", values="aum_m", aggfunc="sum")
             .fillna(0)
             .sort_index())
    # Reorder columns by inception
    piv = piv[[c for c in incep_order if c in piv.columns]]

    def _as_date(v):
        if v is None:
            return None
        if isinstance(v, float) and pd.isna(v):
            return None
        if hasattr(v, "date") and callable(v.date):
            return v.date()
        return v
    incep_values = [d for d in (_as_date(v) for v in df["inception"].tolist()) if d is not None]
    earliest = min(incep_values) if incep_values else _as_date(piv.index.min())
    latest = _as_date(piv.index.max())

    fig, ax = plt.subplots(figsize=(10, 4.2))
    _style(ax, fig)

    cols = list(piv.columns)
    colors = [_PAL[i % len(_PAL)] for i in range(len(cols))]

    n_points = len(piv)
    use_markers = n_points <= 8  # short history — show every datapoint

    if len(cols) == 1:
        # Single-product — filled area
        col = cols[0]
        y = piv[col].values
        ax.fill_between(piv.index, 0, y, color=_BLUE, alpha=0.25, zorder=2)
        ax.plot(piv.index, y, color=_BLUE, linewidth=2.4, zorder=3,
                marker="o" if use_markers else None,
                markersize=6, markerfacecolor=_BLUE, markeredgecolor="white",
                markeredgewidth=1.2)
    else:
        # Stacked area
        ax.stackplot(piv.index, *[piv[c].values for c in cols],
                     colors=colors, alpha=0.85, zorder=2)
        # Bold top-edge so total pops
        total = piv.sum(axis=1)
        ax.plot(piv.index, total, color=_NAVY, linewidth=1.8, zorder=5,
                marker="o" if use_markers else None,
                markersize=5, markerfacecolor=_NAVY, markeredgecolor="white",
                markeredgewidth=1.0)

    # Y axis
    total_max = piv.sum(axis=1).max() if len(cols) > 1 else piv[cols[0]].max()
    y_top = total_max * 1.18
    ax.set_ylim(0, max(y_top, 1))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: (f"\\${v / 1000:.1f}B" if v >= 1000 else f"\\${v:.0f}M")))
    ax.set_ylabel("AUM", fontsize=9, fontweight="bold")
    ax.grid(axis="y", color=_LIGHT, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    # X axis: choose interval based on span (always show at least 3-4 ticks)
    span_months = (latest.year - earliest.year) * 12 + (latest.month - earliest.month)
    if span_months <= 4:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    elif span_months <= 9:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    elif span_months <= 14:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    elif span_months <= 30:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    # For very short series, anchor x-limits with a small pad so the plot doesn't degenerate
    if n_points <= 4:
        x_pad = pd.Timedelta(days=12)
        ax.set_xlim(piv.index.min() - x_pad, piv.index.max() + x_pad)

    # Right-margin labels — current AUM per ticker
    last = piv.iloc[-1]
    if len(cols) == 1:
        labels = [{"col": cols[0], "y": last.iloc[0], "v": last.iloc[0], "c": _BLUE}]
    else:
        # Centred per band, with anti-overlap
        cum = 0
        labels = []
        y_max_axis = ax.get_ylim()[1]
        gap = y_max_axis * 0.05
        for i, col in enumerate(cols):
            v = float(last[col])
            mid = cum + v / 2
            cum += v
            if v < y_max_axis * 0.012:  # too tiny
                continue
            labels.append({"col": col, "y": mid, "v": v, "c": colors[i]})
        labels.sort(key=lambda l: l["y"])
        for i in range(1, len(labels)):
            if labels[i]["y"] - labels[i - 1]["y"] < gap:
                labels[i]["y"] = labels[i - 1]["y"] + gap

    xr = piv.index[-1] + pd.Timedelta(days=10)
    for lb in labels:
        bare = lb["col"].split(" ")[0]
        ax.text(xr, lb["y"], f"{bare}  {_fb(lb['v'], escape=True)}",
                fontsize=9, fontweight="bold", color=lb["c"], va="center",
                clip_on=False, zorder=10)

    # Total label at top of stack
    if len(cols) > 1:
        total_now = float(last.sum())
        labels_sorted_y = sorted([l["y"] for l in labels])
        top_y = (labels_sorted_y[-1] + y_max_axis * 0.07) if labels_sorted_y else total_now
        ax.text(xr, min(top_y, y_max_axis * 0.97),
                f"Total  {_fb(total_now, escape=True)}",
                fontsize=9.5, fontweight="900", color=_NAVY, va="center",
                clip_on=False, zorder=10)

    # Title
    fig.text(0.06, 0.95, f"{suite['label']}  |  AUM Growth Since Inception",
             fontsize=12.5, fontweight="bold", color=_NAVY, ha="left")
    incep_str = earliest.strftime("%b %Y") if earliest else ""
    fig.text(0.06, 0.89,
             f"{suite['subtitle']}  |  Earliest inception: {incep_str}  |  Source: Bloomberg",
             fontsize=9, color="#636e72", ha="left")

    fig.subplots_adjust(top=0.84, bottom=0.12, left=0.07, right=0.78)
    return fig


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor=_BG)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return b64


def _build_html(suite_data, as_of_str):
    blocks = ""
    for d in suite_data:
        blocks += f"""<tr><td style="padding:14px 16px 4px;">
  <div style="font-size:13px;font-weight:700;color:{_NAVY};border-left:3px solid {_BLUE};padding-left:8px;">{d['label']}</div>
</td></tr>
<tr><td style="padding:4px 16px 16px;"><img src="data:image/png;base64,{d['b64']}" style="width:100%;max-width:680px;" alt="{d['label']}"></td></tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>REX Product Suite AUM Growth</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;">
<tr><td align="center" style="padding:16px;">
<table width="720" cellpadding="0" cellspacing="0" style="background:{_BG};border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,0.05);">
<tr><td style="background:{_NAVY};padding:16px 20px;border-radius:6px 6px 0 0;">
  <div style="color:#fff;font-size:18px;font-weight:700;">REX Product Suite — AUM Growth Since Inception</div>
  <div style="color:{_GRAY};font-size:11px;margin-top:2px;">As of {as_of_str}  |  Source: Bloomberg</div>
</td></tr>
{blocks}
<tr><td style="padding:12px 16px;border-top:1px solid #e8e8e8;">
  <div style="font-size:9px;color:{_GRAY};text-align:center;">REX Product Suite Growth Analysis  |  {as_of_str}  |  Source: Bloomberg</div>
</td></tr>
</table>
</td></tr></table></body></html>"""


def main():
    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    suite_data = []
    as_of_str = datetime.now().strftime("%B %d, %Y")
    today_slug = datetime.now().strftime("%Y-%m-%d")

    for suite in SUITES:
        print(f"  {suite['label']}...", end=" ", flush=True)
        df = _load_suite(suite["tickers"])
        if df.empty:
            print("no data")
            continue

        latest = df["date"].max()
        latest_slice = df[df["date"] == latest]
        total = latest_slice["aum_m"].sum()
        n = latest_slice["ticker"].nunique()
        print(f"{n} products, total {_fb(total)}")

        fig = _chart_suite(df, suite)
        if fig is None:
            continue

        png_path = out_dir / f"aum_growth_{suite['slug']}_{today_slug}.png"
        fig.savefig(str(png_path), dpi=200, bbox_inches="tight", facecolor=_BG)
        b64 = _fig_to_b64(fig)
        suite_data.append({"label": suite["label"], "fig": fig, "b64": b64})

    # Multi-page PDF
    pdf_path = out_dir / f"aum_growth_suite_{today_slug}.pdf"
    with PdfPages(str(pdf_path)) as pdf:
        for d in suite_data:
            pdf.savefig(d["fig"], dpi=200, bbox_inches="tight", facecolor=_BG)
            plt.close(d["fig"])
    print(f"\n  => {pdf_path.name}")

    # HTML
    html_path = out_dir / f"aum_growth_suite_{today_slug}.html"
    html_path.write_text(_build_html(suite_data, as_of_str), encoding="utf-8")
    print(f"  => {html_path.name}")

    print(f"  => {len(suite_data)} per-suite PNGs in reports/")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
