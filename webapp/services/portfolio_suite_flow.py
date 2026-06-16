"""REX Portfolio Suite Weekly Flow Report builder.

Internal-only flow report for the Portfolio Solutions team. Universe is
the 20-fund REX Portfolio Suite (Equity Premium Income, Growth & Income,
Autocallable, IncomeMax, Thematic, T-Bill). Excludes T-REX leveraged,
MicroSectors ETNs, and Crypto-spot.

Source of truth is the Bloomberg daily file's `data_flow` sheet — daily
per-ticker flow time series. AUM is read from `mkt_master_data`. All
windowed flows (1W/1M/3M/6M/1Y/YTD) are computed by summing daily flows
so every number in the report ties out to the same source.

Public surface:
    build_html(db_path, xlsm_path) -> (subject, html)
"""
from __future__ import annotations

import sqlite3
import io
import base64
from collections import OrderedDict
from datetime import date
from html import escape

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import LinearSegmentedColormap


HEATMAP_CMAP = LinearSegmentedColormap.from_list(
    "rex_div",
    [(0.00, "#7a1f2b"),
     (0.25, "#c97a7e"),
     (0.42, "#ffffff"),
     (0.50, "#ffffff"),
     (0.58, "#ffffff"),
     (0.75, "#6b9f7e"),
     (1.00, "#1f4d2f")],
    N=256
)

NAVY = "#1a1a2e"
GREEN = "#2a7a4d"
RED = "#9c2a2a"
BLUE = "#335f88"
ACCENT = "#5a7fa3"
GRAY = "#636e72"
LIGHT = "#f8f9fa"
BORDER = "#dee2e6"
WHITE = "#ffffff"
MUTED = "#9aa3ad"

SUITE_COLORS = {
    "Equity Premium Income": "#0984e3",
    "Growth & Income":       "#e8913a",
    "Thematic":              "#d15555",
    "IncomeMax":             "#9b6dc4",
    "Autocallable":          "#5ea66b",
    "T-Bill":                "#636e72",
}

RPS_SUITES = {"Equity Premium Income", "Growth & Income",
              "Autocallable", "IncomeMax", "Thematic", "T-Bill"}
SUITE_ORDER = ["Equity Premium Income", "Growth & Income", "Thematic",
               "IncomeMax", "Autocallable", "T-Bill"]

TICKER_SUITE = {
    "FEPI US": "Equity Premium Income", "AIPI US": "Equity Premium Income",
    "CEPI US": "Equity Premium Income", "FEPI LN": "Equity Premium Income",
    "CEGI LN": "Equity Premium Income", "FEGI LN": "Equity Premium Income",
    "NVII US": "Growth & Income", "TSII US": "Growth & Income",
    "WMTI US": "Growth & Income", "MSII US": "Growth & Income",
    "LLII US": "Growth & Income", "COII US": "Growth & Income",
    "GIF US": "Growth & Income",  "CWII US": "Growth & Income",
    "PLTI US": "Growth & Income", "HOII US": "Growth & Income",
    "ATCL US": "Autocallable",    "ULTI US": "IncomeMax",
    "DRNZ US": "Thematic",        "TLDR US": "T-Bill",
}

SECTION_EYEBROW = (f"font-size:10px;font-weight:700;color:{ACCENT};"
                   "text-transform:uppercase;letter-spacing:1.6px;"
                   "margin:0 0 4px 0;")
SECTION_TITLE = (f"font-size:17px;font-weight:700;color:{NAVY};"
                 f"margin:0 0 14px 0;padding-bottom:10px;"
                 f"border-bottom:1px solid {BORDER};letter-spacing:-0.2px;")
TH = (f"padding:8px 10px;background:{NAVY};color:{WHITE};font-size:11px;"
      "font-weight:600;text-align:right;text-transform:uppercase;"
      "letter-spacing:0.3px;")
TH_L = TH.replace("text-align:right", "text-align:left")
TD = (f"padding:7px 10px;border-bottom:1px solid {BORDER};font-size:12px;"
      "text-align:right;font-variant-numeric:tabular-nums;")
TD_L = TD.replace("text-align:right",
                  "text-align:left").replace(
    "font-variant-numeric:tabular-nums;", "")


def _fmt_aum(v):
    if v is None or pd.isna(v): return "—"
    if v >= 1000: return f"${v/1000:,.2f}B"
    if v >= 1: return f"${v:,.1f}M"
    return f"${v*1000:,.0f}K"


def _fmt_flow(v):
    if v is None or pd.isna(v): return "—"
    if abs(v) < 0.05: return "0.0"
    sign = "+" if v > 0 else "−"
    return f"{sign}{abs(v):,.1f}"


def _fmt_flow_M(v): return _fmt_flow(v) + "M"


def _flow_color(v):
    if v is None or pd.isna(v) or abs(v) < 0.05: return GRAY
    return GREEN if v > 0 else RED


def _mfmt(v, _=None):
    if abs(v) >= 1000: return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _style(ax, grid=True):
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#dfe6e9")
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=GRAY, labelsize=9)
    if grid:
        ax.grid(axis="y", color="#eef1f4", linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)


def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor="white", pad_inches=0.25)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _load_master(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        f"""SELECT ticker, fund_name, rex_suite, COALESCE(aum,0) AS aum
            FROM mkt_master_data
            WHERE is_rex=1 AND market_status='ACTV'
              AND rex_suite IN ({','.join('?'*len(RPS_SUITES))})""",
        tuple(RPS_SUITES))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _load_flow_history(xlsm_path):
    cols = ["Dates"] + list(TICKER_SUITE)
    df = pd.read_excel(xlsm_path, sheet_name="data_flow", usecols=cols)
    df["Dates"] = pd.to_datetime(df["Dates"])
    df = df.set_index("Dates").sort_index()
    df.columns = [c.replace(" Equity", "") for c in df.columns]
    return df


def _by_suite(df):
    out = pd.DataFrame(index=df.index)
    for s in SUITE_ORDER:
        members = [t for t, sx in TICKER_SUITE.items()
                   if sx == s and t in df.columns]
        if members:
            # Coerce to numeric before summing — the Bloomberg data_flow sheet can
            # carry a stray string (e.g. '#N/A', a label) in a flow cell, which made
            # df.sum() raise "unsupported operand type(s) for +: 'float' and 'str'"
            # and crashed the whole Portfolio report build (preview went stale).
            out[s] = df[members].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    return out


def _compute_windows(flow_df):
    out = {}
    for label, n in [("f1w", 5), ("f1m", 21), ("f3m", 63),
                     ("f6m", 126), ("f1y", 252)]:
        out[label] = flow_df.tail(n).sum()
    jan1 = pd.Timestamp(flow_df.index.max().year, 1, 1)
    out["fytd"] = flow_df[flow_df.index >= jan1].sum()
    return out


def _chart_sparkline(series, color):
    fig, ax = plt.subplots(figsize=(2.4, 0.55))
    fig.patch.set_alpha(0)
    ax.fill_between(series.index, 0, series.values, color=color, alpha=0.22)
    ax.plot(series.index, series.values, color=color, linewidth=1.6)
    ax.axhline(0, color=BORDER, linewidth=0.6, zorder=0)
    ax.axis("off")
    return _png(fig)


def _chart_weekly_heatmap(flow_suite):
    weekly = flow_suite.resample("W-FRI").sum().iloc[-12:]
    suites = [s for s in SUITE_ORDER if s in weekly.columns]
    totals = weekly[suites].sum(axis=1)
    M = np.vstack([weekly[suites].T.values, totals.values[np.newaxis, :]])
    row_labels = suites + ["REX Total"]
    fig, ax = plt.subplots(figsize=(11.5, 0.50*len(row_labels) + 1.0))
    vmax = max(abs(M.min()), abs(M.max()), 1e-6)
    ax.imshow(M, cmap=HEATMAP_CMAP, vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=10, color=NAVY)
    for i, lbl in enumerate(ax.get_yticklabels()):
        if i == len(row_labels) - 1: lbl.set_fontweight("800")
    ax.set_xticks(range(len(weekly.index)))
    ax.set_xticklabels([f"{d.month}/{d.day}" for d in weekly.index],
                       rotation=0, fontsize=9.5, color=GRAY)
    from matplotlib.patches import Rectangle
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            v = M[i, j]
            ax.add_patch(Rectangle((j-0.5, i-0.5), 1, 1, fill=False,
                                   edgecolor=BORDER, linewidth=0.8, zorder=3))
            txt = f"{v:+.1f}" if abs(v) >= 0.05 else "—"
            color = "#1a1a2e" if abs(v) < vmax*0.55 else "white"
            ax.text(j, i, txt, ha="center", va="center",
                    fontsize=9 if i == len(row_labels)-1 else 8.5,
                    color=color if abs(v) >= 0.05 else GRAY,
                    fontweight="800" if i == len(row_labels)-1 else "600",
                    zorder=4)
    ax.axhline(len(suites)-0.5, color=NAVY, linewidth=1.6, zorder=5)
    ax.add_patch(Rectangle((-0.5, -0.5), M.shape[1], M.shape[0],
                           fill=False, edgecolor=NAVY, linewidth=1.2, zorder=6))
    for s in ax.spines.values(): s.set_visible(False)
    ax.tick_params(length=0)
    return _png(fig)


def _chart_top_movers(funds):
    sorted_f = sorted(funds, key=lambda f: f["f1w"], reverse=True)
    inflows = [f for f in sorted_f if f["f1w"] > 0.05][:5]
    outflows = sorted([f for f in sorted_f if f["f1w"] < -0.05],
                      key=lambda f: f["f1w"])[:5]
    # Reserve the same vertical room on both sides so a lone outflow bar
    # doesn't balloon to fill the whole panel when inflows has 5 entries.
    n_slots = max(len(inflows), len(outflows), 1)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 3.4))
    for ax, data, color, title in [
        (axes[0], inflows, GREEN, "Top Inflows — 1W"),
        (axes[1], outflows, RED, "Top Outflows — 1W"),
    ]:
        _style(ax, grid=False)
        ax.grid(axis="x", color="#eef1f4", linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)
        if not data:
            ax.text(0.5, 0.5, "(none this week)", transform=ax.transAxes,
                    ha="center", color=GRAY, fontsize=10)
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_title(title, fontsize=10.5, fontweight="600",
                         color=GRAY, loc="left", pad=8)
            continue
        labels = [f["ticker"].replace(" US", "").replace(" LN", "·LN")
                  for f in data]
        vals = [f["f1w"] for f in data]
        # Center bars vertically when count < n_slots so a lone outflow
        # sits in the middle of its panel instead of pinned to the top.
        y_offset = (n_slots - len(labels)) / 2.0
        y = np.arange(len(labels)) + y_offset
        ax.barh(y, vals, color=color, edgecolor="none", height=0.55)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=10, color=NAVY, fontweight="600")
        ax.invert_yaxis()
        # Force both panels to the same y-extent so bar thickness matches
        ax.set_ylim(n_slots - 0.5, -0.5)
        ax.axvline(0, color=BORDER, linewidth=0.8)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(_mfmt))
        pad = max(abs(v) for v in vals) * 0.06
        for i, v in enumerate(vals):
            ax.text(v + (pad if v >= 0 else -pad), y[i], f"{v:+.1f}M",
                    va="center", ha="left" if v >= 0 else "right",
                    fontsize=9.5, fontweight="700", color=color)
        xmax = max(abs(v) for v in vals)
        if all(v >= 0 for v in vals):
            ax.set_xlim(0, xmax*1.35)
        elif all(v <= 0 for v in vals):
            ax.set_xlim(-xmax*1.35, 0)
        else:
            ax.set_xlim(-xmax*1.35, xmax*1.35)
        ax.set_title(title, fontsize=10.5, fontweight="600",
                     color=GRAY, loc="left", pad=8)
    fig.subplots_adjust(wspace=0.4)
    return _png(fig)


def _chart_concentration_pair(funds):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.0))
    for ax, key, label in [(axes[0], "f1w", "1W"), (axes[1], "f1m", "1M")]:
        inflows = sorted([f for f in funds if f[key] > 0.05],
                         key=lambda f: -f[key])
        if not inflows:
            ax.text(0.5, 0.5, "(no inflows)", ha="center", va="center",
                    transform=ax.transAxes, color=GRAY, fontsize=11)
            ax.set_title(f"{label} Inflow Concentration",
                         fontsize=11, fontweight="700", color=NAVY,
                         loc="left", pad=8)
            ax.axis("off"); continue
        total = sum(f[key] for f in inflows)
        threshold = total * 0.02
        named, small = [], []
        for f in inflows:
            (named if f[key] >= threshold else small).append(f)
        sizes = [f[key] for f in named]
        labels = [f["ticker"].replace(" US", "").replace(" LN", "·LN")
                  for f in named]
        colors = [SUITE_COLORS.get(TICKER_SUITE.get(f["ticker"]), BLUE)
                  for f in named]
        if small:
            sizes.append(sum(f[key] for f in small))
            labels.append(f"Rest ({len(small)})")
            colors.append(BORDER)
        wedges, _ = ax.pie(sizes, colors=colors, startangle=90,
                           wedgeprops=dict(width=0.40,
                                           edgecolor="white", linewidth=2))
        ax.text(0, 0.10, f"${total:,.1f}M", ha="center", va="center",
                fontsize=16, fontweight="800", color=NAVY)
        ax.text(0, -0.15, f"Total {label} inflows", ha="center", va="center",
                fontsize=9, color=GRAY)
        legend_labels = [
            f"{labels[i]}   +${sizes[i]:,.1f}M  ({sizes[i]/total*100:.0f}%)"
            for i in range(len(labels))
        ]
        ax.legend(wedges, legend_labels, loc="center left",
                  bbox_to_anchor=(1.0, 0.5), fontsize=9, frameon=False,
                  labelcolor=NAVY)
        ax.set_title(f"{label} Inflow Concentration",
                     fontsize=11, fontweight="700", color=NAVY,
                     loc="left", pad=8)
    fig.subplots_adjust(wspace=0.85)
    return _png(fig)


def _chart_3m_flow(funds):
    rows = [(f["ticker"], f["f3m"], TICKER_SUITE.get(f["ticker"]))
            for f in funds if abs(f["f3m"]) >= 0.05]
    rows.sort(key=lambda r: r[1], reverse=True)
    if not rows: return None
    fig, ax = plt.subplots(figsize=(11.5, max(2.4, 0.42*len(rows) + 1.0)))
    _style(ax, grid=False)
    ax.grid(axis="x", color="#eef1f4", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    labels = [r[0].replace(" US", "").replace(" LN", "·LN") for r in rows]
    vals = [r[1] for r in rows]
    colors = [SUITE_COLORS.get(r[2], NAVY) if r[1] >= 0 else RED
              for r in rows]
    y = np.arange(len(labels))
    ax.barh(y, vals, color=colors, edgecolor="none", height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9.5, color=NAVY, fontweight="600")
    ax.invert_yaxis()
    ax.axvline(0, color=BORDER, linewidth=0.8)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_mfmt))
    xmax = max(abs(min(vals)), abs(max(vals)))
    pad = xmax * 0.03
    for i, v in enumerate(vals):
        ax.text(v + (pad if v >= 0 else -pad), i, f"{v:+.1f}M",
                va="center", ha="left" if v >= 0 else "right",
                fontsize=9.5, fontweight="700",
                color=NAVY if v >= 0 else RED)
    if any(v < 0 for v in vals):
        ax.set_xlim(-xmax*1.20, xmax*1.20)
    else:
        ax.set_xlim(0, xmax*1.20)
    return _png(fig)


def build_html(db_path: str, xlsm_path: str) -> tuple[str, str]:
    """Build the Portfolio Suite Flow Report. Returns (subject, html).

    Subject uses today's date in M/D/YYYY format. Data freshness label
    in the report header reflects the most recent date in the Bloomberg
    data_flow sheet (not today's date).
    """
    funds = _load_master(db_path)
    flow_raw = _load_flow_history(xlsm_path)
    last_date = flow_raw.index.max()
    flow_suite_full = _by_suite(flow_raw)

    win = _compute_windows(flow_raw)
    for f in funds:
        t = f["ticker"]
        for k in ("f1w", "f1m", "f3m", "f6m", "f1y", "fytd"):
            f[k] = float(win[k].get(t, 0.0))

    def agg_suite(suite):
        items = [f for f in funds if f["rex_suite"] == suite]
        if not items: return None
        return {
            "count": len(items),
            "aum":  sum(i["aum"] for i in items),
            "f1w":  sum(i["f1w"] for i in items),
            "f1m":  sum(i["f1m"] for i in items),
            "f3m":  sum(i["f3m"] for i in items),
            "f6m":  sum(i["f6m"] for i in items),
            "f1y":  sum(i["f1y"] for i in items),
            "fytd": sum(i["fytd"] for i in items),
        }
    suite_rows = OrderedDict()
    for s in SUITE_ORDER:
        r = agg_suite(s)
        if r: suite_rows[s] = r

    total = {k: sum(r[k] for r in suite_rows.values())
             for k in ("count", "aum", "f1w", "f1m", "f3m",
                       "f6m", "f1y", "fytd")}

    spark_pngs = {}
    cum90 = flow_suite_full.iloc[-63:].cumsum()
    for s in SUITE_ORDER:
        if s in cum90.columns:
            spark_pngs[s] = _chart_sparkline(cum90[s], SUITE_COLORS[s])

    png_weeks = _chart_weekly_heatmap(flow_suite_full)
    png_movers = _chart_top_movers(funds)
    png_conc = _chart_concentration_pair(funds)
    png_3m = _chart_3m_flow(funds)

    def build_narrative():
        def tk(f):
            return f["ticker"].replace(" US", "").replace(" LN", "·LN")
        sorted_funds = sorted(funds, key=lambda f: f["f1w"], reverse=True)
        top_in = [f for f in sorted_funds if f["f1w"] > 0.05][:2]
        top_out = sorted([f for f in sorted_funds if f["f1w"] < -0.05],
                         key=lambda f: f["f1w"])[:1]
        direction = ("net inflows" if total["f1w"] > 0
                     else "net outflows" if total["f1w"] < 0
                     else "flat flows")
        sentence_1 = (f"REX saw <b>{_fmt_flow_M(total['f1w'])}</b> in "
                      f"{direction} this week")
        clauses = []
        if top_in:
            led = " and ".join(f"<b>{tk(f)}</b> ({_fmt_flow_M(f['f1w'])})"
                               for f in top_in)
            clauses.append(f"led by {led}")
        if top_out:
            dragged = " and ".join(f"<b>{tk(f)}</b> ({_fmt_flow_M(f['f1w'])})"
                                   for f in top_out)
            clauses.append(f"offset by drag from {dragged}")
        if clauses:
            sentence_1 += ", " + "; ".join(clauses)
        sentence_1 += "."
        by_1m = sorted(suite_rows.items(), key=lambda kv: -kv[1]["f1m"])
        sentence_2 = ""
        if by_1m:
            top_suite_name, top_suite = by_1m[0]
            sentence_2 = (f"On a 1-month basis, <b>{escape(top_suite_name)}"
                          f"</b> leads with {_fmt_flow_M(top_suite['f1m'])} "
                          "of net inflows.")
        return sentence_1 + (" " + sentence_2 if sentence_2 else "")

    narrative_html = build_narrative()

    parts = []
    data_as_of = last_date.strftime("%Y-%m-%d")

    def chart_block(title, png, note="", eyebrow=""):
        return f"""
<tr><td style="padding:32px 28px 8px;">
  {f'<div style="{SECTION_EYEBROW}">{escape(eyebrow)}</div>' if eyebrow else ''}
  <div style="{SECTION_TITLE}">{escape(title)}</div>
  {f'<div style="font-size:12px;color:{GRAY};margin:-6px 0 14px 0;line-height:1.55;">{escape(note)}</div>' if note else ''}
  <img src="data:image/png;base64,{png}" style="width:100%;display:block;border-radius:4px;" alt="{escape(title)}">
</td></tr>"""

    parts.append(f"""
<tr><td style="background:{NAVY};padding:24px 28px;">
  <div style="color:{WHITE};font-size:22px;font-weight:700;letter-spacing:-0.5px;">
    REX Portfolio Suite Flow Report
  </div>
  <div style="color:rgba(255,255,255,0.75);font-size:12.5px;margin-top:8px;">
    Data as of {data_as_of}
  </div>
</td></tr>

<tr><td style="padding:18px 28px 4px;background:{LIGHT};">
  <div style="background:{WHITE};border-left:3px solid {ACCENT};padding:14px 18px;border-radius:0 4px 4px 0;font-size:13.5px;line-height:1.55;color:{NAVY};">
    {narrative_html}
  </div>
</td></tr>""")

    def hero_tile(value, label, color=NAVY):
        return f"""
    <td width="25%" valign="top" style="padding:0 6px;">
      <div style="padding:18px 16px;background:{WHITE};border:1px solid {BORDER};border-radius:6px;text-align:left;">
        <div style="font-size:10px;color:{GRAY};text-transform:uppercase;letter-spacing:1.2px;font-weight:700;">{escape(label)}</div>
        <div style="font-size:22px;font-weight:700;color:{color};line-height:1.15;letter-spacing:-0.5px;margin-top:8px;font-variant-numeric:tabular-nums;">{value}</div>
      </div>
    </td>"""

    parts.append(f"""
<tr><td style="padding:24px 22px 4px;background:{LIGHT};">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
    <tr>
    {hero_tile(_fmt_aum(total['aum']), 'Total AUM')}
    {hero_tile(_fmt_flow_M(total['f1w']), '1-Week Net Flow', _flow_color(total['f1w']))}
    {hero_tile(_fmt_flow_M(total['f1m']), '1-Month Net Flow', _flow_color(total['f1m']))}
    {hero_tile(_fmt_flow_M(total['fytd']), 'Year-to-Date Net Flow', _flow_color(total['fytd']))}
    </tr>
  </table>
</td></tr>""")

    cards = []
    for s in SUITE_ORDER:
        if s not in suite_rows: continue
        r = suite_rows[s]
        color = SUITE_COLORS[s]
        spark = spark_pngs.get(s, "")
        label = s.replace("Equity Premium Income", "EPI").replace(
            "Growth & Income", "G&I")
        cards.append(f"""
    <div style="border:1px solid {BORDER};border-radius:6px;background:{WHITE};overflow:hidden;">
      <div style="padding:12px 14px 8px;border-left:3px solid {color};">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td align="left" valign="bottom" style="padding:0;font-size:10.5px;color:{GRAY};text-transform:uppercase;letter-spacing:0.6px;font-weight:700;">{escape(label)}</td>
          <td align="right" valign="bottom" style="padding:0;font-size:10px;color:{MUTED};font-weight:500;">{r['count']} fund{'s' if r['count'] != 1 else ''}</td>
        </tr></table>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:8px;"><tr>
          <td align="left" valign="bottom" style="padding:8px 0 0 0;font-size:18px;font-weight:700;color:{NAVY};letter-spacing:-0.3px;font-variant-numeric:tabular-nums;">{_fmt_aum(r['aum'])}</td>
          <td align="right" valign="bottom" style="padding:8px 0 0 0;font-size:13px;font-weight:700;color:{_flow_color(r['f1w'])};font-variant-numeric:tabular-nums;">{_fmt_flow_M(r['f1w'])} <span style="font-size:9.5px;color:{GRAY};font-weight:500;">1W</span></td>
        </tr></table>
      </div>
      <div style="padding:6px 12px 4px;border-top:1px solid {LIGHT};">
        <div style="font-size:9.5px;color:{GRAY};letter-spacing:0.6px;font-weight:700;text-transform:uppercase;">90-Day Cumulative Flow</div>
      </div>
      <div style="padding:0 8px 8px;">
        <img src="data:image/png;base64,{spark}" style="width:100%;display:block;" alt="">
      </div>
    </div>""")

    # Outlook-safe 3-col layout: render cards in table rows of 3
    SUITE_COLS = 3
    suite_row_chunks = []
    for i in range(0, len(cards), SUITE_COLS):
        chunk = cards[i:i+SUITE_COLS]
        while len(chunk) < SUITE_COLS:
            chunk.append('')
        cells = ''.join(
            f'<td width="33.33%" valign="top" style="padding:5px;">{c}</td>'
            for c in chunk
        )
        suite_row_chunks.append(f'<tr>{cells}</tr>')

    parts.append(f"""
<tr><td style="padding:32px 23px 8px;">
  <div style="{SECTION_EYEBROW}">By Suite</div>
  <div style="{SECTION_TITLE}">Suite Snapshot</div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;">
    {''.join(suite_row_chunks)}
  </table>
</td></tr>""")

    parts.append(chart_block("This Week's Top Movers", png_movers,
                             eyebrow="This Week"))
    if png_3m:
        parts.append(chart_block("90-Day Cumulative Flow", png_3m,
                                 eyebrow="Last 90 Days"))
    parts.append(chart_block("Weekly Flow by Suite — Last 12 Weeks",
                             png_weeks, eyebrow="Last 12 Weeks"))
    parts.append(chart_block("Inflow Concentration", png_conc,
                             eyebrow="Distribution"))

    parts.append(f"""
<tr><td style="padding:44px 28px 12px;">
  <div style="border-top:1px solid {BORDER};padding-top:24px;">
    <div style="{SECTION_EYEBROW}">Appendix</div>
    <div style="{SECTION_TITLE}">All Funds — Full Detail</div>
  </div>
</td></tr>""")

    app_rows = []
    for s in SUITE_ORDER:
        items = [f for f in funds if (f["rex_suite"] or "") == s]
        if not items: continue
        items.sort(key=lambda x: -x["aum"])
        color = SUITE_COLORS.get(s, GRAY)
        app_rows.append(
            f'<tr><td colspan="9" style="background:{LIGHT};padding:7px 10px;'
            f'border-left:3px solid {color};font-size:11px;font-weight:700;'
            f'color:{NAVY};text-transform:uppercase;letter-spacing:0.5px;">'
            f'{escape(s)} &nbsp;·&nbsp; {len(items)} funds &nbsp;·&nbsp; '
            f'{_fmt_aum(sum(i["aum"] for i in items))} AUM</td></tr>'
        )
        for f in items:
            name = (f["fund_name"] or "").replace(" ETF", "").replace("REX ", "")
            if len(name) > 48: name = name[:45]+"..."
            ticker = (f["ticker"] or "").replace(" US", "")
            app_rows.append(
                f'<tr>'
                f'<td style="{TD_L}padding-left:18px;color:{GRAY};font-size:11.5px;">{escape(name)}</td>'
                f'<td style="{TD_L}font-weight:600;font-size:11.5px;">{escape(ticker)}</td>'
                f'<td style="{TD}font-size:11.5px;">{_fmt_aum(f["aum"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["f1w"])};">{_fmt_flow(f["f1w"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["f1m"])};">{_fmt_flow(f["f1m"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["f3m"])};">{_fmt_flow(f["f3m"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["f6m"])};">{_fmt_flow(f["f6m"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["f1y"])};">{_fmt_flow(f["f1y"])}</td>'
                f'<td style="{TD}font-size:11.5px;color:{_flow_color(f["fytd"])};">{_fmt_flow(f["fytd"])}</td>'
                f'</tr>'
            )
    parts.append(f"""
<tr><td style="padding:14px 24px 28px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
    <thead><tr>
      <th style="{TH_L}">Name</th><th style="{TH_L}">Ticker</th><th style="{TH}">AUM</th>
      <th style="{TH}">1W ($M)</th><th style="{TH}">1M ($M)</th><th style="{TH}">3M ($M)</th>
      <th style="{TH}">6M ($M)</th><th style="{TH}">1Y ($M)</th><th style="{TH}">YTD ($M)</th>
    </tr></thead>
    <tbody>{''.join(app_rows)}</tbody>
  </table>
</td></tr>""")

    body = "\n".join(parts)
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>REX Portfolio Suite Flow Report — {data_as_of}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  body {{ margin:0; padding:0; background:{LIGHT}; color:{NAVY}; line-height:1.55;
         font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         font-feature-settings:'cv11','ss01','ss03'; -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:920px; margin:0 auto; background:{WHITE};
          box-shadow:0 1px 3px rgba(15,23,42,0.04), 0 8px 24px rgba(15,23,42,0.06); }}
  table.report {{ width:100%; border-collapse:collapse; }}
  img {{ max-width:100%; height:auto; }}
  @media (max-width:720px) {{
    .suite-grid {{ grid-template-columns:repeat(2,1fr) !important; }}
  }}
  @media (max-width:600px) {{
    .wrap {{ box-shadow:none; }}
    .suite-grid {{ grid-template-columns:repeat(1,1fr) !important; }}
    table[cellpadding] th, table[cellpadding] td {{ padding:5px 6px !important; font-size:10.5px !important; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<table class="report" cellpadding="0" cellspacing="0" border="0">
{body}
</table>
</div>
</body></html>"""

    today = date.today()
    subject = (f"REX Portfolio Suite Flow Report: "
               f"{today.month}/{today.day}/{today.year}")
    return subject, html
