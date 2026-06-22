"""MicroSectors L&I Industry Report v10.
Changes from v9:
- Page 4: Composition (single-stock vs basket split) + Top 10 Weekly Volume (volume table at top of movers).
- Page 5 NEW: Top 10 Weekly Inflows + Top 10 Weekly Outflows (split, no longer absolute value).
- Standardized row heights across every table (no 1-line vs 2-line variation).
- Pages locked at exactly 11in (height not min-height); fund-name truncation tightened to keep single line.
- Footer pagination updated to 5 pages.
- Writes /tmp/bmo_report.html for the email pipeline and /tmp/bmo_report_v10.html for QA.
"""
import sqlite3, sys, base64, io, urllib.request, os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import rcParams
from html import escape
from datetime import date

from matplotlib import font_manager
from matplotlib.font_manager import FontProperties
# MicroSectors brand fonts — kept on the VPS at assets/fonts/ (licensed, gitignored).
FONT_BLACK_PATH = str(PROJECT_ROOT / "assets" / "fonts" / "GothamBlack.ttf")
FONT_NARROW_PATH = str(PROJECT_ROOT / "assets" / "fonts" / "GothamNarrowBook.ttf")
if os.path.exists(FONT_BLACK_PATH) and os.path.exists(FONT_NARROW_PATH):
    font_manager.fontManager.addfont(FONT_BLACK_PATH)
    font_manager.fontManager.addfont(FONT_NARROW_PATH)
    BLACK_NAME = FontProperties(fname=FONT_BLACK_PATH).get_name()
    NARROW_NAME = FontProperties(fname=FONT_NARROW_PATH).get_name()
    rcParams['font.family'] = NARROW_NAME
    # base64 @font-face embedding so the HTML renders identically everywhere / survives email
    gotham_black_b64 = base64.b64encode(open(FONT_BLACK_PATH, "rb").read()).decode("ascii")
    gotham_narrow_b64 = base64.b64encode(open(FONT_NARROW_PATH, "rb").read()).decode("ascii")
else:
    # graceful fallback if brand fonts are absent (local dev) — report still builds
    BLACK_NAME = NARROW_NAME = "DejaVu Sans"
    gotham_black_b64 = gotham_narrow_b64 = ""

GREEN = "#00B050"
GREEN_DARK = "#007A38"
GRAY = "#808080"
GRAY_LIGHT = "#BFBFBF"
GRAY_FAINT = "#EEEEEE"
GRAY_ULTRA = "#F7F7F7"
BLACK = "#231F20"
LIGHT_GRAY = "#D9D9D9"
SOFT_GRAY = "#FAFAFA"
RED = "#A8201A"

# Standard fixed row height for ALL table body rows (px)
ROW_H = 28

LOGO_URL = "https://microsectors.com/wp-content/uploads/MicroSectors-by-REX-Shares-1-e1650374548472.png"
try:
    req = urllib.request.Request(LOGO_URL, headers={"User-Agent": "Mozilla/5.0"})
    logo_b64 = base64.b64encode(urllib.request.urlopen(req, timeout=20).read()).decode("ascii")
except Exception:
    logo_b64 = ""

xlsm = str(PROJECT_ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm")
bo = pd.read_excel(xlsm, sheet_name="BlueOcean")
bo = bo.rename(columns={"Ticker":"us_ticker","1W Traded Value":"us_1w_vol"})
bo["us_clean"] = bo["us_ticker"].astype(str).str.upper().str.strip()
bo_lookup = dict(zip(bo["us_clean"], pd.to_numeric(bo["us_1w_vol"], errors="coerce").fillna(0)))

conn = sqlite3.connect(str(PROJECT_ROOT / "data" / "etp_tracker.db"))
li = pd.read_sql_query("""
    SELECT ticker, fund_name, COALESCE(issuer_nickname, issuer) AS issuer,
           aum, fund_flow_1week, total_return_1week, is_singlestock, category_display
    FROM mkt_master_data
    WHERE primary_category='LI' AND market_status='ACTV'
""", conn)
li["aum"] = pd.to_numeric(li["aum"], errors="coerce").fillna(0)
for c in ["fund_flow_1week","total_return_1week"]:
    li[c] = pd.to_numeric(li[c], errors="coerce")
li["issuer"] = li["issuer"].str.strip().replace({"REX": "T-REX"})
li["is_ss"] = li["category_display"].astype(str).str.contains("Single Stock", case=False, na=False)  # our system, not Bloomberg is_singlestock
li["vol_1w"] = li["ticker"].apply(lambda t: bo_lookup.get(str(t).upper().strip(), 0))

# Override AUM for MicroSectors ETNs using microsector_aum sheet (authoritative source)
ms_sheet = pd.read_excel(xlsm, sheet_name="microsector_aum")
ms_latest = ms_sheet.iloc[-1]
# microsector_aum sheet values are in dollars; mkt_master_data.aum is in millions
ms_aum_lookup = {str(col).strip(): float(ms_latest[col]) / 1_000_000
                 for col in ms_sheet.columns[1:]
                 if pd.notna(ms_latest[col])}
ms_mask = li["issuer"] == "MicroSectors"
mapped = li.loc[ms_mask, "fund_name"].map(lambda fn: ms_aum_lookup.get(str(fn).strip()))
li.loc[ms_mask, "aum"] = mapped.fillna(li.loc[ms_mask, "aum"])

# Override weekly flow for MicroSectors ETNs using microsector_flow sheet (authoritative).
# Sheet is a daily time series in dollars; 1-week flow = sum of last 5 trading days, in millions.
flow_sheet = pd.read_excel(xlsm, sheet_name="microsector_flow")
flow_sheet["_d"] = pd.to_datetime(flow_sheet["Unnamed: 0"], errors="coerce", format="mixed")
flow_dated = flow_sheet[flow_sheet["_d"].notna()].sort_values("_d")
flow_last5 = flow_dated.tail(5)
ms_flow_lookup = {str(col).strip(): pd.to_numeric(flow_last5[col], errors="coerce").fillna(0).sum() / 1_000_000
                  for col in flow_sheet.columns
                  if str(col).startswith("MICROSECTORS")}
mapped_flow = li.loc[ms_mask, "fund_name"].map(lambda fn: ms_flow_lookup.get(str(fn).strip()))
li.loc[ms_mask, "fund_flow_1week"] = mapped_flow.fillna(li.loc[ms_mask, "fund_flow_1week"])

n_funds = len(li)
total_aum_m = li["aum"].sum()
weekly_flow_m = li["fund_flow_1week"].sum()
ms_aum = li[li["issuer"]=="MicroSectors"]["aum"].sum()
ms_share_pct = (ms_aum / total_aum_m * 100) if total_aum_m else 0

ss = li[li["is_ss"]]
bk = li[~li["is_ss"]]
split = {
    "ss": {"n": len(ss), "aum": ss["aum"].sum(), "flow": ss["fund_flow_1week"].sum(),
           "share": ss["aum"].sum()/total_aum_m*100 if total_aum_m else 0},
    "bk": {"n": len(bk), "aum": bk["aum"].sum(), "flow": bk["fund_flow_1week"].sum(),
           "share": bk["aum"].sum()/total_aum_m*100 if total_aum_m else 0},
}

def aum_w_mean(grp, col):
    w = grp["aum"].sum()
    if w == 0: return 0
    return (grp[col] * grp["aum"]).sum() / w
issuers = []
for issuer, grp in li.groupby("issuer"):
    aum_total = grp["aum"].sum()
    issuers.append({
        "issuer": issuer, "n_funds": len(grp), "aum": aum_total,
        "flow_1w": grp["fund_flow_1week"].sum(),
        "ret_1w": aum_w_mean(grp, "total_return_1week"),
        "share": aum_total/total_aum_m*100,
    })
iss_df = pd.DataFrame(issuers).sort_values("aum", ascending=False).reset_index(drop=True)

ticker_issuer = dict(zip(li["ticker"], li["issuer"]))
key_issuers = ["MicroSectors","Direxion","ProShares"]
def bucket(iss): return iss if iss in key_issuers else "Others"
ts = pd.read_sql_query("SELECT ticker, months_ago, aum_value FROM mkt_time_series WHERE months_ago <= 24", conn)
ts["aum_value"] = pd.to_numeric(ts["aum_value"], errors="coerce").fillna(0)
ts["issuer"] = ts["ticker"].map(ticker_issuer)
ts = ts.dropna(subset=["issuer"])
ts["bucket"] = ts["issuer"].apply(bucket)
aum_pivot = ts.groupby(["months_ago","bucket"])["aum_value"].sum().unstack(fill_value=0).sort_index()
ordered_cols = [c for c in ["MicroSectors","Direxion","ProShares","Others"] if c in aum_pivot.columns]
aum_pivot = aum_pivot[ordered_cols]
li["bucket"] = li["issuer"].apply(bucket)
current_buckets = li.groupby("bucket")["aum"].sum()
for col in aum_pivot.columns:
    if col in current_buckets.index:
        aum_pivot.at[0, col] = current_buckets[col]
aum_pivot = aum_pivot.sort_index(ascending=False)
aum_pivot.index = (-aum_pivot.index)

today = date.today()
def month_label(months_ago):
    y = today.year; m = today.month - months_ago
    while m <= 0: m += 12; y -= 1
    return f"{y}-{m:02d}"
labels = [month_label(int(i)) for i in (-aum_pivot.index).tolist()]

# ============ CHART 1: Industry AUM ============
fig, ax = plt.subplots(figsize=(13, 5.8), dpi=150); fig.patch.set_facecolor("white"); ax.set_facecolor("white")
x = list(range(len(labels)))
colors = {"MicroSectors": GREEN, "Direxion": BLACK, "ProShares": GRAY, "Others": GRAY_LIGHT}
stack_order = [c for c in ["Others","ProShares","Direxion","MicroSectors"] if c in aum_pivot.columns]
y_stack = [aum_pivot[c].values / 1000 for c in stack_order]
ax.stackplot(x, *y_stack, labels=stack_order, colors=[colors[c] for c in stack_order],
             edgecolor="white", linewidth=1.0, alpha=0.95)
ax.set_xlim(0, len(labels) + 3.2)
cumulative = 0; label_positions = []
for vals in y_stack: label_positions.append(cumulative + vals[-1]/2); cumulative += vals[-1]
total_height = sum(v[-1] for v in y_stack); min_gap = total_height * 0.07
sorted_labels = sorted(zip(stack_order, y_stack, label_positions), key=lambda t: t[2])
adjusted = []; last_y = -1e9
for label, vals, mid_y in sorted_labels:
    if vals[-1] < 1.5: adjusted.append((label, vals, None)); continue
    if mid_y < last_y + min_gap: mid_y = last_y + min_gap
    adjusted.append((label, vals, mid_y)); last_y = mid_y
x_label = len(labels) - 1 + 0.4
for label, vals, mid_y in adjusted:
    if mid_y is None: continue
    color = colors[label] if colors[label] != GRAY_LIGHT else BLACK
    ax.annotate(f"{label}: ${vals[-1]:.1f}B", xy=(x_label, mid_y), xytext=(0,0), textcoords="offset points",
                color=color, fontsize=15, va="center", ha="left", family=BLACK_NAME)
xticks_idx = list(range(0, len(labels), max(1, len(labels)//8)))
ax.set_xticks(xticks_idx); ax.set_xticklabels([labels[i] for i in xticks_idx], color=BLACK, fontsize=13, family=NARROW_NAME)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v,p: f"${v:.0f}B"))
ax.tick_params(axis='y', colors=BLACK, labelsize=13)
for t in ax.get_yticklabels(): t.set_family(NARROW_NAME)
for sp in ["top","right"]: ax.spines[sp].set_visible(False)
for sp in ["left","bottom"]: ax.spines[sp].set_color(BLACK); ax.spines[sp].set_linewidth(1.0)
ax.grid(axis='y', color=GRAY_FAINT, linewidth=0.9, zorder=0); ax.set_axisbelow(True)
start_total = sum(v[0] for v in y_stack); end_total = sum(v[-1] for v in y_stack)
yoy = (end_total - start_total) / start_total * 100 if start_total else 0
ax.text(0.5, 1.04, f"24-Mo Change: {'+' if yoy>=0 else ''}{yoy:.1f}%   |   Total: ${end_total:.0f}B",
        transform=ax.transAxes, color=BLACK, fontsize=14, ha="center", va="bottom",
        family=BLACK_NAME, bbox=dict(boxstyle="round,pad=0.6", facecolor="white", edgecolor=LIGHT_GRAY, linewidth=1))
plt.tight_layout()
buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight", dpi=180, facecolor="white"); plt.close(fig)
aum_chart_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

# ============ CHART 2: Bubble ============
top10 = iss_df.head(10).copy()
def color_for(i):
    if i == "MicroSectors": return GREEN
    if i == "Direxion": return BLACK
    if i == "ProShares": return GRAY
    return GRAY_LIGHT
top10["color"] = top10["issuer"].apply(color_for)
non_ms = top10[top10["issuer"] != "MicroSectors"]
ms = top10[top10["issuer"] == "MicroSectors"]
plot_order = pd.concat([non_ms, ms])
fig, ax = plt.subplots(figsize=(13, 8.5), dpi=150); fig.patch.set_facecolor("white"); ax.set_facecolor("white")
max_aum = plot_order["aum"].max()
def size_for(aum): return max(500, (aum/max_aum)*14000 + 400)
x_vals = plot_order["n_funds"].tolist(); y_vals = plot_order["flow_1w"].tolist()
x_min, x_max = min(x_vals), max(x_vals); y_min, y_max = min(y_vals), max(y_vals)
x_pad = (x_max-x_min)*0.18; y_range = y_max-y_min
ax.set_xlim(x_min - x_pad, x_max + x_pad)
ax.set_ylim(y_min - y_range*0.30, y_max + y_range*0.30)
sizes = plot_order["aum"].apply(size_for)
ax.scatter(plot_order["n_funds"], plot_order["flow_1w"], s=sizes, c=plot_order["color"],
           alpha=0.80, edgecolors=plot_order["color"], linewidths=2.5, zorder=3)
for _, r in plot_order.iterrows():
    is_ms = r["issuer"] == "MicroSectors"
    primary = GREEN_DARK if is_ms else BLACK
    fs = 14 if is_ms else 13
    radius_pt = (size_for(r["aum"]) ** 0.5) * 0.5
    ax.annotate(r["issuer"], (r["n_funds"], r["flow_1w"]), xytext=(0, radius_pt+6), textcoords="offset points",
                fontsize=fs, color=primary, ha="center", family=(BLACK_NAME if is_ms else NARROW_NAME))
    ax.annotate(f"${r['aum']/1000:.1f}B AUM", (r["n_funds"], r["flow_1w"]),
                xytext=(0, -radius_pt-18), textcoords="offset points",
                fontsize=11, color=BLACK, ha="center", style="italic", family=NARROW_NAME)
ax.axhline(0, color=GRAY_LIGHT, linewidth=1.5, linestyle="--", alpha=0.7, zorder=2)
ax.set_xlabel("Number of ETPs", fontsize=14, color=BLACK, labelpad=14, family=BLACK_NAME)
ax.set_ylabel("Weekly Net Flow ($M)", fontsize=14, color=BLACK, labelpad=14, family=BLACK_NAME)
ax.tick_params(axis='both', colors=BLACK, labelsize=12)
for t in ax.get_xticklabels() + ax.get_yticklabels(): t.set_family(NARROW_NAME)
for sp in ["top","right"]: ax.spines[sp].set_visible(False)
for sp in ["left","bottom"]: ax.spines[sp].set_color(BLACK); ax.spines[sp].set_linewidth(1.0)
ax.grid(True, color=GRAY_FAINT, linewidth=0.9, zorder=0); ax.set_axisbelow(True)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v,p: f"${v:+.0f}M" if v != 0 else "$0"))
plt.tight_layout()
buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight", dpi=180, facecolor="white"); plt.close(fig)
bubble_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

# ============ Formatters ============
def money(v):
    if v is None or pd.isna(v): return "—"
    if abs(v) >= 1000: return f"${v/1000:,.1f}B"
    return f"${v:,.0f}M"
def signed_money(v):
    if v is None or pd.isna(v): return "—"
    sign = "+" if v >= 0 else "-"; av = abs(v)
    return f"{sign}${av/1000:.2f}B" if av >= 1000 else f"{sign}${av:,.0f}M"
def vol_fmt(v):
    if v is None or pd.isna(v) or v == 0: return "—"
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    return f"${v/1e3:.0f}K"

flow_sorted = iss_df.sort_values("flow_1w", ascending=False)
top_in = flow_sorted.iloc[0]; top_out = flow_sorted.iloc[-1]
ms_row = iss_df[iss_df["issuer"]=="MicroSectors"]
ms_flow = ms_row["flow_1w"].iloc[0] if not ms_row.empty else 0
hl = [
    f"Industry net flows of <strong>{signed_money(weekly_flow_m)}</strong> for the week. <strong>{top_in['issuer']}</strong> led inflows ({signed_money(top_in['flow_1w'])}); <strong>{top_out['issuer']}</strong> recorded the largest outflow ({signed_money(top_out['flow_1w'])}).",
    f"MicroSectors closed the week at <strong style='color:{GREEN_DARK};'>{money(ms_aum)}</strong> in assets &mdash; {ms_share_pct:.1f}% of the L&amp;I market &mdash; with net flow of {signed_money(ms_flow)}.",
    f"Single-underlier products (single stock, crypto, commodity or FX) represent <strong>{split['ss']['n']/n_funds*100:.0f}%</strong> of L&amp;I funds ({split['ss']['n']:,}/{n_funds:,}) but only <strong>{split['ss']['aum']/total_aum_m*100:.0f}%</strong> of AUM. Basket / index / sector products hold the AUM majority at <strong>{money(split['bk']['aum'])}</strong>.",
]

# ============ Issuer table — fixed row height ============
def iss_row(r, alt=False):
    is_ms = r["issuer"]=="MicroSectors"
    if is_ms:
        bg = "#E8F5EB"; row_color = GREEN_DARK; weight = "700"
    else:
        bg = SOFT_GRAY if alt else "#ffffff"; row_color = BLACK; weight = "normal"
    flow_color = GREEN_DARK if r["flow_1w"] >= 0 else RED
    return f"""<tr style="background:{bg};height:{ROW_H}px;">
<td style="padding:0 18px;border-bottom:1px solid {LIGHT_GRAY};font-family:Georgia,serif;font-size:11pt;color:{row_color};font-weight:{weight};white-space:nowrap;height:{ROW_H}px;">{escape(r['issuer'])}</td>
<td style="padding:0 16px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;font-size:10.5pt;font-variant-numeric:tabular-nums;color:{row_color};font-weight:{weight};white-space:nowrap;height:{ROW_H}px;">{int(r['n_funds'])}</td>
<td style="padding:0 18px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;font-size:10.5pt;font-variant-numeric:tabular-nums;color:{row_color};font-weight:{weight};white-space:nowrap;height:{ROW_H}px;">{money(r['aum'])}</td>
<td style="padding:0 16px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;font-size:10.5pt;font-variant-numeric:tabular-nums;color:{row_color};font-weight:{weight};white-space:nowrap;height:{ROW_H}px;">{r['share']:.1f}%</td>
<td style="padding:0 20px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;color:{flow_color};font-weight:600;font-size:10.5pt;font-variant-numeric:tabular-nums;white-space:nowrap;height:{ROW_H}px;">{signed_money(r['flow_1w'])}</td>
</tr>"""
iss_rows = "".join(iss_row(r, i%2==1) for i, (_, r) in enumerate(iss_df.head(10).iterrows()))

def kpi_cell(label, value, sub, last=False):
    border = "" if last else f"border-right:1px solid {LIGHT_GRAY};"
    return f"""<td style="width:25%;padding:24px 18px;vertical-align:top;{border}">
<div style="font-family:Georgia,serif;font-size:8.5pt;color:{GRAY};text-transform:uppercase;letter-spacing:2.5px;font-weight:600;height:30px;line-height:1.35;display:flex;align-items:flex-start;">{label}</div>
<div style="font-family:Georgia,serif;font-size:30pt;font-weight:700;color:{BLACK};line-height:1;letter-spacing:-1px;font-variant-numeric:tabular-nums;margin-top:14px;">{value}</div>
<div style="font-size:9.5pt;color:{GRAY};margin-top:12px;line-height:1.5;">{sub}</div>
</td>"""

kpi_table = f"""<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-top:2px solid {BLACK};border-bottom:2px solid {BLACK};border-collapse:collapse;">
<tr>
{kpi_cell("L&amp;I Funds", f"{n_funds:,}", "Active products")}
{kpi_cell("L&amp;I AUM", money(total_aum_m), "Industry total")}
{kpi_cell("1W Net Flow", signed_money(weekly_flow_m), "Last 7 days")}
{kpi_cell("MicroSectors Share", f"{ms_share_pct:.1f}%", "of L&amp;I AUM", last=True)}
</tr>
</table>"""

# ============ Page 4 & 5: Volume on Page 4, Inflow/Outflow split on Page 5 ============
li_for_top = li.dropna(subset=["fund_flow_1week"]).copy()
top_inflows  = li_for_top.sort_values("fund_flow_1week", ascending=False).head(10)
top_outflows = li_for_top.sort_values("fund_flow_1week", ascending=True ).head(10)
top_vol = li[li["vol_1w"] > 0].sort_values("vol_1w", ascending=False).head(10)

def split_panel(title, n, aum_v, flow_v, share_v, accent_color):
    return f"""<td style="width:50%;padding:24px 22px;vertical-align:top;border:1px solid {LIGHT_GRAY};border-left:4px solid {accent_color};">
<div style="font-family:Georgia,serif;font-size:10pt;color:{GRAY};text-transform:uppercase;letter-spacing:2.5px;font-weight:600;margin-bottom:14px;">{title}</div>
<table cellpadding="0" cellspacing="0" border="0" style="width:100%;font-family:Georgia,serif;">
<tr>
<td style="padding:6px 0;font-size:10pt;color:{GRAY};width:42%;">Funds</td>
<td style="padding:6px 0;font-size:13pt;font-weight:700;color:{BLACK};text-align:right;font-variant-numeric:tabular-nums;">{n:,}</td>
</tr>
<tr>
<td style="padding:6px 0;font-size:10pt;color:{GRAY};">AUM</td>
<td style="padding:6px 0;font-size:13pt;font-weight:700;color:{BLACK};text-align:right;font-variant-numeric:tabular-nums;">{money(aum_v)}</td>
</tr>
<tr>
<td style="padding:6px 0;font-size:10pt;color:{GRAY};">1W Flow</td>
<td style="padding:6px 0;font-size:13pt;font-weight:700;color:{GREEN_DARK if flow_v >= 0 else RED};text-align:right;font-variant-numeric:tabular-nums;">{signed_money(flow_v)}</td>
</tr>
<tr>
<td style="padding:6px 0;font-size:10pt;color:{GRAY};">Share of L&amp;I AUM</td>
<td style="padding:6px 0;font-size:13pt;font-weight:700;color:{BLACK};text-align:right;font-variant-numeric:tabular-nums;">{share_v:.0f}%</td>
</tr>
</table>
</td>"""

split_html = f"""<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:14px 0;">
<tr>
{split_panel("Single-Underlier", split["ss"]["n"], split["ss"]["aum"], split["ss"]["flow"], split["ss"]["share"], BLACK)}
{split_panel("Basket / Index / Sector", split["bk"]["n"], split["bk"]["aum"], split["bk"]["flow"], split["bk"]["share"], GREEN)}
</tr>
</table>"""

def top_table(title, rows_html, value_label):
    return f"""<div style="width:100%;">
<div style="font-family:Georgia,serif;font-size:10pt;color:{GRAY};text-transform:uppercase;letter-spacing:2.5px;font-weight:600;margin-bottom:14px;">{title}</div>
<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:'Helvetica Neue',sans-serif;color:{BLACK};border-top:2px solid {BLACK};border-bottom:2px solid {BLACK};width:100%;table-layout:fixed;">
<thead><tr style="background:{SOFT_GRAY};height:30px;">
<th style="padding:0 14px;font-size:9pt;text-align:left;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:11%;white-space:nowrap;height:30px;">Ticker</th>
<th style="padding:0 12px;font-size:9pt;text-align:left;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:52%;white-space:nowrap;height:30px;">Fund Name</th>
<th style="padding:0 14px;font-size:9pt;text-align:right;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:14%;white-space:nowrap;height:30px;">AUM</th>
<th style="padding:0 14px;font-size:9pt;text-align:right;font-weight:700;letter-spacing:1px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:23%;white-space:nowrap;height:30px;">{value_label}</th>
</tr></thead>
<tbody>{rows_html}</tbody></table></div>"""

def product_row(r, value_str, value_color, alt=False):
    is_ms = r["issuer"]=="MicroSectors"
    bg = "#E8F5EB" if is_ms else (SOFT_GRAY if alt else "#ffffff")
    weight = "700" if is_ms else "400"
    row_color = GREEN_DARK if is_ms else BLACK
    tk = str(r["ticker"]).replace(" US","").strip()
    fname = str(r["fund_name"])
    # Safety cap only; with the Issuer column removed these names fit in full.
    if len(fname) > 52:
        fname = fname[:50].rstrip() + "…"
    return f"""<tr style="background:{bg};height:{ROW_H}px;">
<td style="padding:0 14px;border-bottom:1px solid {LIGHT_GRAY};font-family:'Consolas',Courier,monospace;font-size:10pt;color:{row_color};font-weight:700;white-space:nowrap;height:{ROW_H}px;">{escape(tk)}</td>
<td style="padding:0 12px;border-bottom:1px solid {LIGHT_GRAY};font-family:Georgia,serif;font-size:9pt;color:{row_color};font-weight:{weight};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;height:{ROW_H}px;">{escape(fname)}</td>
<td style="padding:0 14px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;font-size:10pt;font-variant-numeric:tabular-nums;color:{row_color};font-weight:{weight};white-space:nowrap;height:{ROW_H}px;">{money(r['aum'])}</td>
<td style="padding:0 16px;border-bottom:1px solid {LIGHT_GRAY};text-align:right;font-size:10pt;font-variant-numeric:tabular-nums;color:{value_color};font-weight:700;white-space:nowrap;height:{ROW_H}px;">{value_str}</td>
</tr>"""

vol_rows = "".join(
    product_row(r, vol_fmt(r["vol_1w"]), BLACK, i%2==1)
    for i, (_, r) in enumerate(top_vol.iterrows())
)
inflow_rows = "".join(
    product_row(r, signed_money(r["fund_flow_1week"]), GREEN_DARK, i%2==1)
    for i, (_, r) in enumerate(top_inflows.iterrows())
)
outflow_rows = "".join(
    product_row(r, signed_money(r["fund_flow_1week"]), RED, i%2==1)
    for i, (_, r) in enumerate(top_outflows.iterrows())
)

# ============ HTML ============
today_str = today.strftime("%B %d, %Y"); week_str = today.strftime("%B %d, %Y").upper()
logo_html = f'<img src="data:image/png;base64,{logo_b64}" style="height:42px;width:auto;display:block;" alt="MicroSectors" />' if logo_b64 else f'<div style="font-family:Georgia,serif;font-size:14pt;font-weight:700;letter-spacing:5px;text-transform:uppercase;color:{BLACK};">MicroSectors</div>'
logo_small = f'<img src="data:image/png;base64,{logo_b64}" style="height:28px;width:auto;display:block;" alt="MicroSectors" />' if logo_b64 else f'<div style="font-family:Georgia,serif;font-size:13pt;font-weight:700;color:{BLACK};">MicroSectors</div>'

TOTAL_PAGES = 5
def page_header():
    return f"""<div style="margin-bottom:32px;display:flex;justify-content:space-between;align-items:center;padding-bottom:14px;border-bottom:1.5px solid {GREEN};">
{logo_small}
<div style="font-family:Georgia,serif;font-size:9pt;color:{GRAY};letter-spacing:1.5px;text-transform:uppercase;">L&amp;I Industry Report  &middot;  {week_str}</div>
</div>"""

def page_footer(n, total=TOTAL_PAGES):
    return f"""<div style="margin-top:auto;padding-top:24px;border-top:1px solid {LIGHT_GRAY};display:flex;justify-content:space-between;align-items:center;font-size:8.5pt;color:{GRAY};letter-spacing:0.5px;font-family:Georgia,serif;">
<div>MicroSectors by REX Shares  &middot;  L&amp;I Industry Report</div>
<div style="font-variant-numeric:tabular-nums;">{n} / {total}</div>
</div>"""

def section_header(num, title):
    return f"""<div style="margin-bottom:22px;">
<div style="display:flex;align-items:center;gap:18px;margin-bottom:10px;">
<div style="font-family:Georgia,serif;font-size:12pt;color:{GREEN};font-weight:700;letter-spacing:1px;">{num}</div>
<div style="height:1px;background:{LIGHT_GRAY};flex:1;"></div>
</div>
<div style="font-family:Georgia,serif;font-size:22pt;font-weight:700;color:{BLACK};letter-spacing:-0.5px;line-height:1.15;">{title}</div>
</div>"""

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>MicroSectors L&amp;I Industry Report &mdash; {today_str}</title>
<style>
@font-face {{ font-family:'Gotham Black'; src:url(data:font/ttf;base64,{gotham_black_b64}) format('truetype'); font-weight:normal; font-style:normal; }}
@font-face {{ font-family:'Gotham Narrow'; src:url(data:font/ttf;base64,{gotham_narrow_b64}) format('truetype'); font-weight:normal; font-style:normal; }}
@page {{ size: letter; margin: 0; }}
@media print {{
  body {{ background: #fff; }}
  .page {{ page-break-after: always; }}
  .page:last-child {{ page-break-after: auto; }}
}}
body {{ margin:0;padding:0;background:{GRAY_ULTRA};font-family:'Gotham Narrow',Arial,sans-serif;color:{BLACK};line-height:1.55; }}
.page {{ width:8.5in;height:11in;margin:0 auto 24px;padding:0.7in 0.75in;background:#ffffff;box-sizing:border-box;display:flex;flex-direction:column;box-shadow:0 2px 8px rgba(0,0,0,0.06);overflow:hidden; }}
</style></head>
<body>

<!-- PAGE 1 -->
<div class="page">
<div style="display:flex;justify-content:space-between;align-items:center;padding-bottom:22px;border-bottom:2px solid {GREEN};">
{logo_html}
<div style="font-family:Georgia,serif;font-size:16pt;color:{BLACK};font-weight:700;letter-spacing:-0.3px;">{today_str}</div>
</div>
<div style="margin-top:32px;margin-bottom:36px;">
<div style="font-family:Georgia,serif;font-size:42pt;font-weight:700;color:{BLACK};line-height:1.05;letter-spacing:-1.5px;">
Leveraged &amp; Inverse<br/><span style="color:{GREEN};">Industry Report</span>
</div>
<div style="font-family:Georgia,serif;font-size:12pt;color:{GRAY};margin-top:14px;font-style:italic;">A weekly view of the L&amp;I ETP industry &mdash; assets, flows, performance and competitive position.</div>
</div>
{kpi_table}
<div style="margin-top:36px;">
<div style="font-family:Georgia,serif;font-size:14pt;font-weight:700;color:{BLACK};margin-bottom:18px;letter-spacing:-0.3px;">Key Highlights</div>
<ol style="margin:0;padding-left:0;list-style:none;">
{"".join(f'''<li style="margin-bottom:18px;display:flex;gap:18px;align-items:start;">
<div style="font-family:Georgia,serif;font-size:16pt;color:{GREEN};font-weight:700;line-height:1.1;min-width:28px;">0{i+1}</div>
<div style="font-family:Georgia,serif;font-size:11.5pt;color:{BLACK};line-height:1.7;flex:1;padding-top:1px;">{l}</div>
</li>''' for i, l in enumerate(hl))}
</ol>
</div>
{page_footer(1)}
</div>

<!-- PAGE 2 -->
<div class="page">
{page_header()}
{section_header("01", "Industry Landscape")}
<div style="font-family:Georgia,serif;font-size:10.5pt;color:{GRAY};margin-bottom:20px;line-height:1.65;font-style:italic;">Total L&amp;I industry assets over the trailing 24 months, stacked by issuer.</div>
<div style="margin-bottom:36px;"><img src="data:image/png;base64,{aum_chart_b64}" style="width:100%;display:block;" alt="Industry AUM" /></div>
{section_header("02", "Issuer Breakdown")}
<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:{BLACK};border-top:2px solid {BLACK};border-bottom:2px solid {BLACK};width:100%;table-layout:fixed;">
<thead><tr style="background:{SOFT_GRAY};height:36px;">
<th style="padding:0 18px;color:{BLACK};font-size:9pt;text-align:left;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:26%;white-space:nowrap;height:36px;">Issuer</th>
<th style="padding:0 16px;color:{BLACK};font-size:9pt;text-align:right;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:12%;white-space:nowrap;height:36px;">Funds</th>
<th style="padding:0 18px;color:{BLACK};font-size:9pt;text-align:right;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:18%;white-space:nowrap;height:36px;">AUM</th>
<th style="padding:0 16px;color:{BLACK};font-size:9pt;text-align:right;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:17%;white-space:nowrap;height:36px;">Share %</th>
<th style="padding:0 20px;color:{BLACK};font-size:9pt;text-align:right;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-family:Georgia,serif;border-bottom:1px solid {LIGHT_GRAY};width:27%;white-space:nowrap;height:36px;">1W Flow</th>
</tr></thead>
<tbody>{iss_rows}</tbody></table>
{page_footer(2)}
</div>

<!-- PAGE 3 -->
<div class="page">
{page_header()}
{section_header("03", "Competitive Position")}
<div style="font-family:Georgia,serif;font-size:10.5pt;color:{GRAY};margin-bottom:24px;line-height:1.65;font-style:italic;">Top ten issuers by AUM. X axis: number of ETPs. Y axis: weekly net flow. Bubble size: AUM.</div>
<div><img src="data:image/png;base64,{bubble_b64}" style="width:100%;display:block;" alt="Competitive position" /></div>
{page_footer(3)}
</div>

<!-- PAGE 4: COMPOSITION + WEEKLY VOLUME -->
<div class="page">
{page_header()}
{section_header("04", "Composition &amp; Weekly Volume")}

<div style="font-family:Georgia,serif;font-size:10pt;color:{GRAY};margin-bottom:14px;text-transform:uppercase;letter-spacing:2.5px;font-weight:600;">Single-Underlier vs Basket Split</div>
{split_html}

<div style="margin-top:30px;">{top_table("Top 10 Weekly Traded Value", vol_rows, "1W Traded Val")}</div>

{page_footer(4)}
</div>

<!-- PAGE 5: TOP INFLOWS / TOP OUTFLOWS -->
<div class="page">
{page_header()}
{section_header("05", "Weekly Flow Movers")}

<div style="margin-bottom:26px;">{top_table("Top 10 Weekly Inflows", inflow_rows, "1W Flow")}</div>

<div>{top_table("Top 10 Weekly Outflows", outflow_rows, "1W Flow")}</div>

<div style="font-family:Georgia,serif;font-size:9pt;color:{GRAY};margin-top:16px;font-style:italic;line-height:1.5;">
Ranked by weekly net flow. Inflows highest first, outflows largest negative first.
</div>

{page_footer(5)}
</div>

</body></html>"""

import re
FONT_DISPLAY = "'Gotham Black',Arial,sans-serif"
FONT_BODY = "'Gotham Narrow',Arial,sans-serif"
def restyle_fonts(doc):
    """Normalize every inline font-family: bold (>=600) -> Gotham Black, else Gotham Narrow.
    Skips the <style> block (which carries the @font-face data URIs)."""
    head, _, rest = doc.partition("</style>")
    def repl(m):
        style = m.group(1)
        style = re.sub(r"font-family:[^;\"]*;?", "", style)
        is_bold = bool(re.search(r"font-weight:\s*(600|700|800|900|bold)", style))
        fam = FONT_DISPLAY if is_bold else FONT_BODY
        return f'style="font-family:{fam};{style}"'
    rest = re.sub(r'style="([^"]*)"', repl, rest)
    return head + "</style>" + rest
html = restyle_fonts(html)

_OUT = PROJECT_ROOT / "reports" / f"microsectors_industry_{date.today().isoformat()}.html"
_OUT.parent.mkdir(parents=True, exist_ok=True)
_OUT.write_text(html, encoding="utf-8")
print(f"wrote {_OUT} ({len(html):,} bytes) · {n_funds} L&I funds · ${total_aum_m/1000:.1f}B AUM")
print(f"\nSplit: SS={split['ss']['n']:,} funds ${split['ss']['aum']/1000:.1f}B / Basket={split['bk']['n']:,} funds ${split['bk']['aum']/1000:.1f}B")
print(f"\nTop 10 inflows (head 3):")
print(top_inflows.head(3)[["ticker","fund_name","issuer","aum","fund_flow_1week"]].to_string())
print(f"\nTop 10 outflows (head 3):")
print(top_outflows.head(3)[["ticker","fund_name","issuer","aum","fund_flow_1week"]].to_string())
print(f"\nTop 10 volume (head 3):")
print(top_vol.head(3)[["ticker","fund_name","issuer","aum","vol_1w"]].to_string())
conn.close()
