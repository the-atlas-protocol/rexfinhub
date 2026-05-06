"""
CEO Market Share Analysis — Outlook-pasteable HTML with inline charts.

4 categories x 2 charts + summary table.

Usage:
    python scripts/generate_market_share_charts.py
"""
from __future__ import annotations
import sys, io, base64
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.backends.backend_pdf import PdfPages

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.database import SessionLocal
from webapp.models import MktTimeSeries, MktMasterData
from sqlalchemy import select

CATEGORIES = [
    {"db": "Leverage & Inverse - Single Stock",           "label": "L&I Single Stock", "slug": "li_ss"},
    {"db": "Leverage & Inverse - Index/Basket/ETF Based", "label": "L&I Index/ETF",    "slug": "li_idx"},
    {"db": "Income - Single Stock",                       "label": "Income Single Stock", "slug": "inc_ss"},
    {"db": "Income - Index/Basket/ETF Based",             "label": "Income Index/ETF",  "slug": "inc_idx"},
]
TOP_N = 5

# Palette
_NAVY  = "#1a1a2e"
_BLUE  = "#0984e3"
_RED   = "#d63031"
_GRAY  = "#b2bec3"
_LIGHT = "#dfe6e9"
_BG    = "#ffffff"

_COMP_PAL = ["#4a6fa5", "#6b8f71", "#9b7ebd", "#d4915e", "#5ba0b2", "#7a8c6e", "#c47a5a"]
_FIXED_C  = {"Others": "#c8cfd4", "REX": _BLUE}


def _cc(cols):
    c, i = [], 0
    for col in cols:
        if col in _FIXED_C:
            c.append(_FIXED_C[col])
        else:
            c.append(_COMP_PAL[i % len(_COMP_PAL)])
            i += 1
    return c


def _style(ax, fig):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(_LIGHT)
    ax.spines["bottom"].set_color(_LIGHT)
    ax.tick_params(colors=_NAVY, labelsize=8)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)


def _fb(v, escape=False):
    """Format billions. escape=True for matplotlib text (avoids mathtext)."""
    d = r"\$" if escape else "$"
    if abs(v) >= 1:
        return f"{d}{v:.1f}B"
    if abs(v) >= 0.01:
        return f"{d}{v * 1000:.0f}M"
    return f"{d}0"


# Data
_MS_OVERRIDES = None  # cache microsectors overrides across categories

def _load_ms_overrides():
    """Load MicroSectors ETN proprietary overrides (cached)."""
    global _MS_OVERRIDES
    if _MS_OVERRIDES is not None:
        return _MS_OVERRIDES
    try:
        from market.microsectors import read_overrides
        from market.config import DATA_FILE
        if DATA_FILE.exists():
            xl = pd.ExcelFile(DATA_FILE, engine="openpyxl")
            if "microsector" in xl.sheet_names:
                _MS_OVERRIDES = read_overrides(xl)
                if _MS_OVERRIDES:
                    print(f"  MicroSectors: {len(_MS_OVERRIDES)} ETN overrides loaded")
                return _MS_OVERRIDES
    except Exception as e:
        print(f"  MicroSectors override load failed (non-fatal): {e}")
    _MS_OVERRIDES = {}
    return _MS_OVERRIDES


def _apply_ts_overrides(df):
    """Apply MicroSectors AUM overrides to time-series DataFrame.

    Overrides have 'aum' (months_ago=0), 'aum_1' (months_ago=1), etc.
    MktTimeSeries 'aum' column is in millions; overrides are also in millions.
    """
    ov = _load_ms_overrides()
    if not ov:
        return df
    count = 0
    for ticker_us, vals in ov.items():
        mask = df["ticker"] == ticker_us
        if not mask.any():
            continue
        for idx in df[mask].index:
            m = df.at[idx, "months_ago"]
            key = "aum" if m == 0 else f"aum_{m}"
            if key in vals:
                df.at[idx, "aum"] = vals[key]
                count += 1
    if count:
        print(f"  MicroSectors: overrode {count} AUM values")
    return df


def _load(cat_db):
    db = SessionLocal()
    rows = db.execute(
        select(MktTimeSeries.ticker, MktTimeSeries.months_ago, MktTimeSeries.aum_value,
               MktTimeSeries.is_rex, MktTimeSeries.issuer_display, MktTimeSeries.as_of_date)
        .where(MktTimeSeries.category_display == cat_db)
    ).all()
    # Load inception dates to zero out pre-inception AUM
    incep_rows = db.execute(
        select(MktMasterData.ticker, MktMasterData.inception_date)
    ).all()
    db.close()
    incep = {r[0]: r[1] for r in incep_rows if r[1] is not None}

    df = pd.DataFrame(rows, columns=["ticker", "months_ago", "aum", "is_rex", "issuer", "as_of_date"])
    df["aum"] = df["aum"].fillna(0)

    # Zero out AUM for months before inception (Bloomberg backfills stale data)
    dates = df["as_of_date"].dropna()
    as_of = dates.max() if not dates.empty else None
    if as_of is None or (isinstance(as_of, float) and pd.isna(as_of)):
        as_of = datetime.now().date()
    elif hasattr(as_of, 'date'):
        as_of = as_of.date()
    zeroed = 0
    for idx, row in df.iterrows():
        inc_raw = incep.get(row["ticker"])
        if inc_raw is None:
            continue
        # Parse inception date (stored as string in DB)
        if isinstance(inc_raw, str):
            if inc_raw.startswith("NaT") or inc_raw.strip() == "":
                continue
            inc_date = datetime.strptime(inc_raw[:10], "%Y-%m-%d").date()
        elif hasattr(inc_raw, 'date'):
            inc_date = inc_raw.date()
        else:
            inc_date = inc_raw
        month_date = as_of - relativedelta(months=int(row["months_ago"]))
        if month_date < inc_date:
            if df.at[idx, "aum"] > 0:
                zeroed += 1
            df.at[idx, "aum"] = 0
    if zeroed:
        print(f"  Zeroed {zeroed} pre-inception AUM values")

    # Apply MicroSectors ETN proprietary AUM overrides
    df = _apply_ts_overrides(df)
    return df


def _d(m, ref):
    return ref - relativedelta(months=m)


def _summary(df, as_of):
    a = df[df.aum > 0]
    rex = a[a.is_rex].groupby("months_ago").agg(
        rex_aum=("aum", "sum"), rex_products=("ticker", "nunique")).reset_index()
    tot = a.groupby("months_ago").agg(
        total_aum=("aum", "sum"), total_products=("ticker", "nunique")).reset_index()
    m = rex.merge(tot, on="months_ago")
    m["rex_share"] = m["rex_aum"] / m["total_aum"] * 100
    m = m.sort_values("months_ago", ascending=False)
    m["date"] = m["months_ago"].apply(lambda x: _d(x, as_of))
    return m


# -----------------------------------------------------------------------
# Chart A: REX AUM + Market Share
# -----------------------------------------------------------------------
def _chart_rex(df, as_of, label):
    m = _summary(df, as_of)
    cur = m[m.months_ago == 0].iloc[0]

    fig, ax = plt.subplots(figsize=(9, 3.8))
    _style(ax, fig)

    # AUM area — stronger fill
    ax.fill_between(m["date"], 0, m["rex_aum"] / 1e3, color=_BLUE, alpha=0.20, zorder=2)
    ax.plot(m["date"], m["rex_aum"] / 1e3, color=_BLUE, linewidth=2.4, zorder=3)
    ax.set_ylabel("REX AUM", fontsize=9, fontweight="bold")
    y_max = m["rex_aum"].max() / 1e3 * 1.35
    ax.set_ylim(0, y_max)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: _fb(x, escape=True)))
    ax.grid(axis="y", color=_LIGHT, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    # Share on right axis — solid line, no spine
    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax2.spines["right"].set_color(_RED)
    ax2.spines["right"].set_linewidth(0.5)
    ax2.plot(m["date"], m["rex_share"], color=_RED, linewidth=1.5, zorder=4)
    ax2.tick_params(axis="y", labelsize=8, colors=_RED, length=3)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax2.set_ylim(0, max(m["rex_share"].max() * 1.4, 1))

    # End-point labels in right margin (outside axes, inside figure)
    aum_val = cur.rex_aum / 1e3
    share_val = cur.rex_share

    # Inline legend
    legend_elements = [
        Line2D([0], [0], color=_BLUE, linewidth=2.4, label="REX AUM"),
        Line2D([0], [0], color=_RED, linewidth=1.5, label="Market Share"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=8,
              frameon=True, fancybox=False, edgecolor=_LIGHT, framealpha=0.9)

    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))

    # Title line
    fig.text(0.06, 0.95, f"{label}  |  REX Position", fontsize=12, fontweight="bold", color=_NAVY, ha="left")
    fig.text(0.06, 0.89,
             f"REX: {_fb(cur.rex_aum / 1e3, escape=True)}  |  {int(cur.rex_products)} products  |  "
             f"{cur.rex_share:.1f}% share  |  Total market: {_fb(cur.total_aum / 1e3, escape=True)}",
             fontsize=9, color="#636e72", ha="left")

    fig.subplots_adjust(top=0.84, bottom=0.12, left=0.08, right=0.76)

    # Place endpoint labels in the right margin using figure coordinates
    ax_pos = ax.get_position()
    aum_ylim = ax.get_ylim()
    share_ylim = ax2.get_ylim()
    aum_frac = (aum_val - aum_ylim[0]) / (aum_ylim[1] - aum_ylim[0])
    share_frac = (share_val - share_ylim[0]) / (share_ylim[1] - share_ylim[0])
    # Nudge apart if too close
    min_gap = 0.10
    if abs(aum_frac - share_frac) < min_gap:
        mid = (aum_frac + share_frac) / 2
        aum_frac = mid + min_gap / 2
        share_frac = mid - min_gap / 2
    aum_fig_y = ax_pos.y0 + aum_frac * ax_pos.height
    share_fig_y = ax_pos.y0 + share_frac * ax_pos.height
    label_x = 0.80
    fig.text(label_x, aum_fig_y, f"{_fb(aum_val, escape=True)}",
             fontsize=11, fontweight="bold", color=_BLUE, va="center")
    fig.text(label_x, share_fig_y, f"{share_val:.1f}%",
             fontsize=10, fontweight="bold", color=_RED, va="center")

    return fig


# -----------------------------------------------------------------------
# Chart B: Competitive landscape — stacked area, REX on top
# -----------------------------------------------------------------------
_COMP_PAL_BRIGHT = ["#3d7ec7", "#e8913a", "#5ea66b", "#9b6dc4", "#d15555", "#4db8a8", "#c47a5a"]

def _chart_comp(df, as_of, label, rex_bottom=False):
    a = df[df.aum > 0].copy()

    non_rex = a[(a.months_ago == 0) & (~a.is_rex)].groupby("issuer")["aum"].sum().sort_values(ascending=False)
    top = non_rex.head(TOP_N).index.tolist()

    def grp(r):
        if r["is_rex"]: return "REX"
        if r["issuer"] in top: return r["issuer"]
        return "Others"

    a["g"] = a.apply(grp, axis=1)
    piv = a.groupby(["months_ago", "g"])["aum"].sum().unstack(fill_value=0)

    ordered = []
    if rex_bottom:
        # REX at bottom, then competitors large->small, Others on top
        if "REX" in piv.columns:
            ordered.append("REX")
        ordered.extend([c for c in top if c in piv.columns])
        if "Others" in piv.columns:
            ordered.append("Others")
    else:
        # Others at bottom, competitors small->large, REX on top
        if "Others" in piv.columns:
            ordered.append("Others")
        ordered.extend([c for c in reversed(top) if c in piv.columns])
        if "REX" in piv.columns:
            ordered.append("REX")
    piv = piv[[c for c in ordered if c in piv.columns]]
    piv = piv.sort_index(ascending=False)
    piv.index = [_d(m, as_of) for m in piv.index]
    piv_b = piv / 1e3

    # Colors — brighter palette, Others gray, REX blue
    colors = []
    ci = 0
    for col in piv_b.columns:
        if col == "Others":
            colors.append("#d5dbe0")
        elif col == "REX":
            colors.append(_BLUE)
        else:
            colors.append(_COMP_PAL_BRIGHT[ci % len(_COMP_PAL_BRIGHT)])
            ci += 1

    fig, ax = plt.subplots(figsize=(9, 3.8))
    _style(ax, fig)

    sp = ax.stackplot(piv_b.index, *[piv_b[c] for c in piv_b.columns],
                      colors=colors, alpha=0.85, zorder=2)

    # Bold edge line on REX band so it pops
    if "REX" in piv_b.columns:
        if rex_bottom:
            # REX is bottom band — outline its top edge (REX value itself)
            ax.plot(piv_b.index, piv_b["REX"], color=_BLUE, linewidth=2.2, zorder=5)
        else:
            # REX is top band — outline the total (top of stack)
            total = piv_b.sum(axis=1)
            ax.plot(piv_b.index, total, color=_BLUE, linewidth=2.2, zorder=5)

    ax.set_ylabel("AUM ($B)", fontsize=9, fontweight="bold")
    total_max = piv_b.sum(axis=1).max()
    ax.set_ylim(0, total_max * 1.12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"\\${x:.0f}B"))
    ax.grid(axis="y", color=_LIGHT, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))

    # Right-side labels
    col_c = dict(zip(piv_b.columns, colors))
    y_max = ax.get_ylim()[1]
    gap = y_max * 0.055

    labels = []
    cum = 0
    for col in piv_b.columns:
        v = piv_b[col].iloc[-1]
        mid = cum + v / 2
        cum += v
        if v < 0.03:
            continue
        labels.append({"col": col, "y": mid, "v": v, "c": col_c.get(col, _GRAY)})

    labels.sort(key=lambda l: l["y"])
    for i in range(1, len(labels)):
        if labels[i]["y"] - labels[i - 1]["y"] < gap:
            labels[i]["y"] = labels[i - 1]["y"] + gap

    xr = piv_b.index[-1] + relativedelta(days=8)
    for lb in labels:
        weight = "900" if lb["col"] == "REX" else "bold"
        size = 9.5 if lb["col"] == "REX" else 8.5
        ax.text(xr, lb["y"], f"{lb['col']}  {_fb(lb['v'], escape=True)}",
                fontsize=size, fontweight=weight, color=lb["c"], va="center",
                clip_on=False, zorder=10)

    top_y = (max(l["y"] for l in labels) + gap) if labels else cum
    total_now = piv_b.sum(axis=1).iloc[-1]
    ax.text(xr, top_y, f"Total: {_fb(total_now, escape=True)}",
            fontsize=9, fontweight="bold", color=_NAVY, va="bottom", clip_on=False, zorder=10)

    # Title line
    fig.text(0.06, 0.95, f"{label}  |  Competitive Landscape",
             fontsize=12, fontweight="bold", color=_NAVY, ha="left")
    fig.text(0.06, 0.89, "AUM by Issuer  |  3-Year History  |  Source: Bloomberg",
             fontsize=9, color="#636e72", ha="left")

    fig.subplots_adjust(top=0.84, bottom=0.12, left=0.08, right=0.76)
    return fig


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=250, bbox_inches="tight", facecolor=_BG)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    plt.close(fig)
    return b64


def _build_html(cat_data, as_of_str):
    tbl_rows = ""
    for d in cat_data:
        s = d["summary"]
        cur = s[s.months_ago == 0].iloc[0]
        yr1 = s[s.months_ago == 12]
        yr1_share = f"{yr1.iloc[0].rex_share:.1f}%" if not yr1.empty else "--"
        yr1_aum = _fb(yr1.iloc[0].rex_aum / 1e3) if not yr1.empty else "--"
        peak = s.loc[s.rex_share.idxmax()]

        tbl_rows += f"""<tr>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;font-weight:600;font-size:12px;">{d['label']}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-size:12px;">{_fb(cur.total_aum / 1e3)}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-weight:700;color:{_BLUE};font-size:12px;">{_fb(cur.rex_aum / 1e3)}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:center;font-size:12px;">{int(cur.rex_products)}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-weight:700;color:{_RED};font-size:12px;">{cur.rex_share:.1f}%</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-size:11px;color:#888;">{yr1_aum}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-size:11px;color:#888;">{yr1_share}</td>
  <td style="padding:6px 10px;border-bottom:1px solid #e8e8e8;text-align:right;font-size:11px;color:#888;">{peak.rex_share:.1f}%</td>
</tr>"""

    chart_html = ""
    for d in cat_data:
        chart_html += f"""<tr><td style="padding:16px 16px 4px;">
  <div style="font-size:13px;font-weight:700;color:{_NAVY};border-left:3px solid {_BLUE};padding-left:8px;">{d['label']}</div>
</td></tr>
<tr><td style="padding:4px 16px;"><img src="data:image/png;base64,{d['rex_b64']}" style="width:100%;max-width:660px;" alt="REX Position"></td></tr>
<tr><td style="padding:4px 16px 12px;"><img src="data:image/png;base64,{d['comp_b64']}" style="width:100%;max-width:660px;" alt="Competitive"></td></tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>REX Market Share Analysis</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;">
<tr><td align="center" style="padding:16px;">
<table width="700" cellpadding="0" cellspacing="0" style="background:{_BG};border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,0.05);">
<tr><td style="background:{_NAVY};padding:16px 20px;border-radius:6px 6px 0 0;">
  <div style="color:#fff;font-size:18px;font-weight:700;">REX Market Share Analysis</div>
  <div style="color:{_GRAY};font-size:11px;margin-top:2px;">As of {as_of_str}  |  Source: Bloomberg  |  ETN data reflects proprietary share/price data</div>
</td></tr>
<tr><td style="padding:14px 16px 8px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    <tr style="background:#f8f9fa;">
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};">Category</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">Market</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:{_BLUE};text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">REX AUM</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:center;">#</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:{_RED};text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">Share</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">1Y Ago</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">1Y Share</td>
      <td style="padding:5px 10px;font-size:9px;font-weight:700;color:#636e72;text-transform:uppercase;border-bottom:2px solid {_NAVY};text-align:right;">Peak</td>
    </tr>
    {tbl_rows}
  </table>
</td></tr>
{chart_html}
<tr><td style="padding:12px 16px;border-top:1px solid #e8e8e8;">
  <div style="font-size:9px;color:{_GRAY};text-align:center;">REX Market Share Analysis  |  {as_of_str}  |  Bloomberg  |  ETN data uses proprietary share/price data where available</div>
</td></tr>
</table>
</td></tr></table></body></html>"""


# -----------------------------------------------------------------------
# Public API: generate charts for a single category (used by report_emails)
# -----------------------------------------------------------------------
def generate_category_charts(cat_db: str, label: str) -> dict | None:
    """Generate market share charts for a single category.

    Returns dict with keys: label, summary, rex_b64, comp_b64, cur
    or None if no data.
    """
    df = _load(cat_db)
    if df.empty:
        return None
    dates = df["as_of_date"].dropna()
    as_of = dates.max() if not dates.empty else None
    if as_of is None or (isinstance(as_of, float) and pd.isna(as_of)):
        as_of = datetime.now().date()

    m = _summary(df, as_of)
    if m.empty:
        return None
    cur = m[m.months_ago == 0]
    if cur.empty:
        return None
    cur = cur.iloc[0]

    fig_r = _chart_rex(df, as_of, label)
    fig_c = _chart_comp(df, as_of, label)

    result = {
        "label": label,
        "summary": m,
        "rex_b64": _fig_to_b64(fig_r),
        "comp_b64": _fig_to_b64(fig_c),
        "cur": {
            "total_aum": cur.total_aum,
            "rex_aum": cur.rex_aum,
            "rex_products": int(cur.rex_products),
            "rex_share": cur.rex_share,
        },
    }
    # 1-year ago
    yr1 = m[m.months_ago == 12]
    if not yr1.empty:
        yr1 = yr1.iloc[0]
        result["yr1"] = {"rex_aum": yr1.rex_aum, "rex_share": yr1.rex_share}
    # Peak share
    peak = m.loc[m.rex_share.idxmax()]
    result["peak_share"] = peak.rex_share

    return result


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    cat_data = []
    as_of_str = ""

    for cat in CATEGORIES:
        print(f"  {cat['label']}...", end=" ", flush=True)
        df = _load(cat["db"])
        if df.empty:
            print("no data")
            continue
        dates = df["as_of_date"].dropna()
        as_of = dates.max() if not dates.empty else None
        if as_of is None or (isinstance(as_of, float) and pd.isna(as_of)):
            as_of = datetime.now().date()
        as_of_str = as_of.strftime("%B %d, %Y")

        m = _summary(df, as_of)
        cur = m[m.months_ago == 0].iloc[0]
        print(f"REX {_fb(cur.rex_aum / 1e3)} / {cur.rex_share:.1f}%")

        fig_r = _chart_rex(df, as_of, cat["label"])
        fig_c = _chart_comp(df, as_of, cat["label"])

        ds = as_of.strftime("%Y-%m-%d")
        fig_r.savefig(str(out_dir / f"rex_mkt_{cat['slug']}_{ds}.png"), dpi=160, bbox_inches="tight", facecolor=_BG)
        fig_c.savefig(str(out_dir / f"rex_comp_{cat['slug']}_{ds}.png"), dpi=160, bbox_inches="tight", facecolor=_BG)

        cat_data.append({
            "label": cat["label"],
            "summary": m,
            "rex_b64": _fig_to_b64(fig_r),
            "comp_b64": _fig_to_b64(fig_c),
        })

    html = _build_html(cat_data, as_of_str)
    html_path = out_dir / f"rex_market_share_analysis_{datetime.now().strftime('%Y-%m-%d')}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n  => {html_path.name}  (open in browser, Ctrl+A, copy, paste into Outlook)")

    # PDF
    pdf_path = out_dir / f"rex_market_share_analysis_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    figs = []
    for cat in CATEGORIES:
        df = _load(cat["db"])
        if df.empty: continue
        dates = df["as_of_date"].dropna()
        as_of = dates.max() if not dates.empty else datetime.now().date()
        if isinstance(as_of, float) and pd.isna(as_of):
            as_of = datetime.now().date()
        figs.append(_chart_rex(df, as_of, cat["label"]))
        figs.append(_chart_comp(df, as_of, cat["label"]))
    with PdfPages(str(pdf_path)) as pdf:
        for f in figs:
            pdf.savefig(f, dpi=160, bbox_inches="tight", facecolor=_BG)
            plt.close(f)
    print(f"  => {pdf_path.name}")
    print("Done.")


if __name__ == "__main__":
    main()
