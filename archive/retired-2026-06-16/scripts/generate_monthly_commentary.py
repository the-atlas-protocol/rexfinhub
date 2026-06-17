raise SystemExit("RETIRED 2026-06-16 - quarantined to archive/retired-2026-06-16/, do not run. 3-gate proven 0 live refs; pending final sweep.")
"""
Monthly ETF commentary generator — reusable for any REX fund / month.

USAGE:
    1. Edit CONFIG below with this month's values for the target fund.
    2. Run: python scripts/generate_monthly_commentary.py
    3. Output: temp/report_previews/{ticker}_{YYYY-MM}_commentary.html
    4. Open in browser, Ctrl+A, Ctrl+C, paste into Outlook, send.

DESIGN NOTES (copy-paste survival):
    - Header uses light gray bg + dark text. Outlook strips backgrounds on paste;
      white text on dark would go invisible. Light+dark always survives.
    - Chart is base64 PNG embedded inline. Survives paste. (CID-style cid:chart
      only works for programmatic Graph API send, not paste.)
    - "For current standardized performance" uses plain-text URL auto-linkify
      by Outlook on receive. Do not wrap in <a href> — paste often strips it.
    - Since-Inception returns exclude day-1 daily return because it represents
      the prior-close-to-inception-close change, which is pre-inception.

BULLET PLACEHOLDERS (auto-substituted):
    {ticker}       -> CONFIG['ticker']
    {fund_name}    -> CONFIG['fund_name']
    {month_name}   -> CONFIG['month_name']
    {mar_atcl}     -> computed monthly return for the fund ("-3.15%")
    {mar_spxt}     -> computed monthly return for SPXT ("-4.98%")
    {si_atcl}      -> computed since-inception return for the fund
    {si_spxt}      -> computed since-inception return for SPXT
    {distribution} -> CONFIG['distribution_annualized']
    {roc}          -> CONFIG['distribution_roc_pct']
    {beta}         -> CONFIG['beta_si']
    {downside}     -> CONFIG['downside_capture']

EXCEL INPUT FORMAT:
    Bloomberg daily returns for fund (col B) + daily SPXT index levels (col F).
    Col A: fund dates | Col B: fund daily returns
    Col E: SPXT dates | Col F: SPXT index levels
    Row 0: header labels | Row 1: ticker names | Row 2+: data
"""

import base64
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════
# CONFIG — edit this section each month / per fund
# ══════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ─── Product (set once per fund) ───
    'ticker': 'ATCL',
    'fund_name': 'REX Autocallable Income ETF',
    'inception_date': '2026-02-18',
    'product_url': 'https://www.rexshares.com/atcl/',

    # ─── Period (update each month) ───
    'month_name': 'March',
    'month_num': 3,
    'year': 2026,
    'period_start': '2026-03-01',  # first day of reporting month
    'period_end': '2026-03-31',    # last day of reporting month

    # ─── Data source ───
    'excel_path': 'C:/Users/RyuEl-Asmar/Downloads/ATCL Commentary.xlsx',

    # ─── Key metrics (fill from factsheet / calc each month) ───
    'distribution_annualized': '13.81%',
    'distribution_roc_pct': '91.1%',
    'beta_si': '~0.80',
    'downside_capture': '~63%',

    # ─── Portfolio highlights (list of (label, value) tuples) ───
    'portfolio_highlights': [
        ('Live Autocallables',                 '288'),
        ('Weighted Avg. Coupon',               '14.27%'),
        ('Weighted Avg. MTM Discount',         '92.89%'),
        ('Autocallables Above Coupon Barrier', '100%'),
        ('Autocallables with Principal at Risk', '0'),
    ],

    # ─── Commentary bullets (edit each month; supports placeholders) ───
    'commentary_bullets': [
        'Structured product issuance remained elevated in Q1 2026, with equity-linked issuance reaching approximately $59B vs. $45B in Q1 2025 (+14% YoY). Autocallables remained the most widely used payoff structure, representing ~60% of issuance.',
        'Equity markets experienced broad-based weakness in {month_name}, with increased volatility driven by geopolitical tensions, rate uncertainty, and growth concerns. The S&amp;P 500 TR Index declined {mar_spxt} during the month.',
        '{ticker} returned {mar_atcl} in {month_name}, outperforming the S&amp;P 500 TR Index and capturing approximately 63% of the market downside. This relative performance was primarily driven by the portfolio\'s current positioning, with a weighted average mark-to-market level of 92.89%, contributing to lower equity sensitivity during the period, along with the ongoing accrual of coupon income within the portfolio.',
        'Portfolio positioning remains constructive, with all positions currently above their coupon barriers, supporting full coupon eligibility across the portfolio.',
        '{ticker} paid its first distribution in {month_name}, with an annualized distribution rate of {distribution}, of which {roc} was estimated as Return of Capital.',
    ],

    # ─── Output dir ───
    'output_dir': 'C:/Projects/rexfinhub/temp/report_previews',
}

# ══════════════════════════════════════════════════════════════════════════
# Data loading + return computation
# ══════════════════════════════════════════════════════════════════════════
xl = pd.read_excel(CONFIG['excel_path'], header=None)

fund_dates = pd.to_datetime(xl.iloc[2:, 0], errors='coerce')
fund_ret = pd.to_numeric(xl.iloc[2:, 1], errors='coerce')
spxt_dates = pd.to_datetime(xl.iloc[2:, 4], errors='coerce')
spxt_levels = pd.to_numeric(xl.iloc[2:, 5], errors='coerce')

spxt_df = pd.DataFrame({'Date': spxt_dates.values, 'level': spxt_levels.values}).dropna(subset=['level'])
spxt_df['SPXT'] = spxt_df['level'].pct_change()

df = pd.DataFrame({'Date': fund_dates.values, CONFIG['ticker']: fund_ret.values})
df = df.dropna(subset=['Date', CONFIG['ticker']]).reset_index(drop=True)
df = df.merge(spxt_df[['Date', 'SPXT']], on='Date', how='left')

period_mask = (df['Date'] >= CONFIG['period_start']) & (df['Date'] <= CONFIG['period_end'])
period_df = df[period_mask].reset_index(drop=True)


def compound(series):
    return (1 + series.dropna()).prod() - 1


# Returns: period (month) = all days in month; since-inception excludes day 1
ret = {}
for col in [CONFIG['ticker'], 'SPXT']:
    ret[col] = {
        'mar': compound(period_df[col]),
        'si': compound(df[col].iloc[1:]),
    }


def fp(val):
    return f'{val * 100:+.2f}%'


def fmt(val):
    pct = val * 100
    color = '#27ae60' if pct >= 0 else '#e74c3c'
    sign = '+' if pct >= 0 else ''
    return f'<span style="color:{color};font-weight:700;">{sign}{pct:.2f}%</span>'


# ══════════════════════════════════════════════════════════════════════════
# Chart generation (period cumulative return)
# ══════════════════════════════════════════════════════════════════════════
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Segoe UI', 'Arial', 'Helvetica'],
    'axes.unicode_minus': False,
})

fig, ax = plt.subplots(figsize=(7.5, 3.4), dpi=180)
fig.patch.set_facecolor('#ffffff')
ax.set_facecolor('#ffffff')

colors = {CONFIG['ticker']: '#1a1a2e', 'SPXT': '#787878'}

start_pt = period_df['Date'].iloc[0] - pd.Timedelta(days=1)
xdates = [start_pt] + period_df['Date'].tolist()

for col in ['SPXT', CONFIG['ticker']]:
    cum = (1 + period_df[col]).cumprod()
    vals = [0.0] + ((cum - 1) * 100).tolist()
    lw = 2.8 if col == CONFIG['ticker'] else 1.5
    alpha = 1.0 if col == CONFIG['ticker'] else 0.85
    ax.plot(xdates, vals, color=colors[col], linewidth=lw, alpha=alpha,
            zorder=4 if col == CONFIG['ticker'] else 2, solid_capstyle='round')
    ax.plot(xdates[-1], vals[-1], 'o', color=colors[col],
            markersize=4.5 if col == CONFIG['ticker'] else 3,
            zorder=5 if col == CONFIG['ticker'] else 3, alpha=alpha)

fund_vals = [0.0] + ((1 + period_df[CONFIG['ticker']]).cumprod().sub(1).mul(100)).tolist()
ax.fill_between(xdates, fund_vals, 0, alpha=0.10, color='#1a1a2e', zorder=1)
ax.axhline(y=0, color='#1a1a2e', linewidth=0.8, zorder=1)

ax.yaxis.set_major_formatter(mticker.FuncFormatter(
    lambda x, _: f'{x:+.2f}%' if x != 0 else '0.00%'))
ax.yaxis.set_label_position('left')
ax.yaxis.tick_left()
ax.tick_params(axis='y', labelsize=8, colors='#1a1a2e', length=0, pad=6)

n = len(period_df)
tick_indices = [0, n // 3, 2 * n // 3, n - 1]
ax.set_xticks([period_df['Date'].iloc[i] for i in tick_indices])
ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
ax.tick_params(axis='x', labelsize=8, colors='#1a1a2e', length=0, pad=6)

ax.set_xlim(start_pt - pd.Timedelta(hours=12),
            period_df['Date'].iloc[-1] + pd.Timedelta(days=4.5))

for spine in ax.spines.values():
    spine.set_visible(False)
ax.grid(axis='y', alpha=0.25, color='#d0d4d8', linewidth=0.6)

end_vals = {col: ((1 + period_df[col]).cumprod().iloc[-1] - 1) * 100
            for col in [CONFIG['ticker'], 'SPXT']}
label_offsets = {CONFIG['ticker']: 0, 'SPXT': 0}
sorted_cols = sorted(end_vals.keys(), key=lambda k: end_vals[k])
for i in range(1, len(sorted_cols)):
    prev, curr = sorted_cols[i - 1], sorted_cols[i]
    if abs(end_vals[curr] - end_vals[prev]) < 0.5:
        label_offsets[prev] = -6
        label_offsets[curr] = 6

for col in [CONFIG['ticker'], 'SPXT']:
    ax.annotate(f'{col}  {end_vals[col]:+.2f}%',
                xy=(period_df['Date'].iloc[-1], end_vals[col]),
                xytext=(10, label_offsets[col]), textcoords='offset points',
                fontsize=8.5, fontweight='700' if col == CONFIG['ticker'] else '500',
                color=colors[col], va='center')

fig.subplots_adjust(right=0.78, left=0.10, top=0.96, bottom=0.13)

buf = BytesIO()
fig.savefig(buf, format='png', bbox_inches='tight', facecolor='#ffffff', pad_inches=0.12)
plt.close(fig)
chart_png = buf.getvalue()
chart_b64 = base64.b64encode(chart_png).decode()

# ══════════════════════════════════════════════════════════════════════════
# HTML building
# ══════════════════════════════════════════════════════════════════════════
SEC_HDR = 'font-size:15px;font-weight:700;color:#1a1a2e;margin:0 0 10px 0;padding-bottom:6px;border-bottom:2px solid #1a1a2e;'

# Substitute placeholders in bullet templates
placeholders = {
    'ticker': CONFIG['ticker'],
    'fund_name': CONFIG['fund_name'],
    'month_name': CONFIG['month_name'],
    'mar_atcl': fp(ret[CONFIG['ticker']]['mar']),
    'mar_spxt': fp(ret['SPXT']['mar']),
    'si_atcl': fp(ret[CONFIG['ticker']]['si']),
    'si_spxt': fp(ret['SPXT']['si']),
    'distribution': CONFIG['distribution_annualized'],
    'roc': CONFIG['distribution_roc_pct'],
    'beta': CONFIG['beta_si'],
    'downside': CONFIG['downside_capture'],
}

commentary_bullets = [b.format(**placeholders) for b in CONFIG['commentary_bullets']]


def bullet_row(text):
    return f'''    <tr><td style="padding:4px 0;"><table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
      <td style="width:14px;vertical-align:top;padding-top:1px;font-size:13px;font-weight:700;color:#1a1a2e;">&#8226;</td>
      <td style="font-size:13px;color:#1a1a2e;line-height:1.6;">{text}</td>
    </tr></table></td></tr>'''


commentary_rows = '\n'.join(bullet_row(b) for b in commentary_bullets)

# Key Highlights bullets (auto-built from metrics)
period_label = f'{CONFIG["period_start"][5:].replace("-", "/")}/{str(CONFIG["year"])[2:]} - {CONFIG["period_end"][5:].replace("-", "/")}/{str(CONFIG["year"])[2:]}'
si_label = f'{CONFIG["inception_date"][5:].replace("-", "/")} - {CONFIG["period_end"][5:].replace("-", "/")}/{str(CONFIG["year"])[2:]}'

key_highlights = [
    f'<strong>{CONFIG["month_name"]} Distribution:</strong> {CONFIG["distribution_annualized"]} annualized ({CONFIG["distribution_roc_pct"]} estimated Return of Capital)',
    f'<strong>Performance ({period_label}):</strong> {CONFIG["ticker"]}: {fp(ret[CONFIG["ticker"]]["mar"])} &nbsp;|&nbsp; SPXT: {fp(ret["SPXT"]["mar"])}',
    f'<strong>Since Inception ({si_label}):</strong> {CONFIG["ticker"]}: {fp(ret[CONFIG["ticker"]]["si"])} &nbsp;|&nbsp; SPXT: {fp(ret["SPXT"]["si"])}',
    f'<strong>Since Inception Beta to S&amp;P 500 TR:</strong> {CONFIG["beta_si"]}',
    f'<strong>Downside Capture ({CONFIG["month_name"]}):</strong> {CONFIG["downside_capture"]} of S&amp;P 500 TR Index',
]
highlights_rows = '\n'.join(bullet_row(b) for b in key_highlights)

# Portfolio highlights rows
ph_rows = ''
for i, (label, value) in enumerate(CONFIG['portfolio_highlights']):
    bdr = 'border-bottom:1px solid #eaeced;' if i < len(CONFIG['portfolio_highlights']) - 1 else ''
    ph_rows += f'''  <tr>
    <td style="padding:10px 0;{bdr}">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="font-size:13px;color:#4a4a5a;">{label}</td>
        <td style="font-size:13px;font-weight:700;color:#1a1a2e;text-align:right;">{value}</td>
      </tr></table>
    </td>
  </tr>
'''


def table_row(ticker, name, bg, border=True):
    bdr = 'border-bottom:1px solid #e0e4e8;' if border else ''
    return f'''<tr style="background:{bg};">
  <td style="padding:10px 12px 10px 0;font-size:13px;color:#1a1a2e;{bdr}white-space:nowrap;width:65px;">{ticker}</td>
  <td style="padding:10px 12px;font-size:13px;color:#1a1a2e;{bdr}">{name}</td>
  <td style="padding:10px 12px;font-size:13px;{bdr}text-align:right;width:90px;">{fmt(ret[ticker]['mar'])}</td>
  <td style="padding:10px 12px;font-size:13px;{bdr}text-align:right;width:120px;">{fmt(ret[ticker]['si'])}</td>
</tr>'''


# Format inception date for footer: "2026-02-18" -> "February 18, 2026"
incep_dt = pd.to_datetime(CONFIG['inception_date'])
incep_str = incep_dt.strftime('%B %d, %Y')
end_dt = pd.to_datetime(CONFIG['period_end'])
end_str = f'{end_dt.month}/{end_dt.day}/{str(end_dt.year)[2:]}'
period_display = f'{CONFIG["month_name"]}: {period_df["Date"].iloc[0].month}/{period_df["Date"].iloc[0].day} - {end_dt.month}/{end_dt.day}/{str(end_dt.year)[2:]}'
si_display = f'Since Inception: {incep_dt.month}/{incep_dt.day} - {end_dt.month}/{end_dt.day}/{str(end_dt.year)[2:]}'

html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{CONFIG['ticker']} {CONFIG['month_name']} {CONFIG['year']} Commentary</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:#1a1a2e;line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:20px 10px;">
<table width="780" cellpadding="0" cellspacing="0" border="0"
  style="background:#ffffff;border-radius:8px;overflow:hidden;
  box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:780px;table-layout:fixed;">

<!-- Header (light bg + dark text for copy-paste survival) -->
<tr><td style="background:#f4f5f6;padding:18px 30px;border-bottom:1px solid #d0d4d8;">
  <div style="font-size:20px;font-weight:700;color:#1a1a2e;letter-spacing:-0.3px;">{CONFIG['fund_name']} ({CONFIG['ticker']})</div>
  <div style="font-size:16px;color:#888;margin-top:4px;font-weight:400;letter-spacing:0.3px;">{CONFIG['month_name']} {CONFIG['year']} Commentary</div>
</td></tr>

<!-- Key Highlights -->
<tr><td style="padding:22px 30px 12px;">
  <div style="{SEC_HDR}">Key Highlights</div>
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:4px;">
{highlights_rows}
  </table>
</td></tr>

<!-- Commentary -->
<tr><td style="padding:22px 30px 12px;">
  <div style="{SEC_HDR}">Commentary</div>
  <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:4px;">
{commentary_rows}
  </table>
</td></tr>

<!-- Portfolio Highlights -->
<tr><td style="padding:18px 30px 5px;">
  <div style="{SEC_HDR}">Portfolio Highlights</div>
</td></tr>
<tr><td style="padding:0 30px 18px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
{ph_rows}</table>
</td></tr>

<!-- Performance Summary -->
<tr><td style="padding:18px 30px 2px;">
  <div style="{SEC_HDR}margin-bottom:2px;">Performance Summary</div>
  <div style="font-size:10px;color:#636e72;margin-bottom:12px;">{period_display} &nbsp;&bull;&nbsp; {si_display}</div>
</td></tr>
<tr><td style="padding:0 30px 18px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
<tr style="border-bottom:1px solid #dee2e6;">
  <td style="padding:10px 12px 10px 0;font-size:10px;color:#1a1a2e;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;width:65px;">Ticker</td>
  <td style="padding:10px 12px;font-size:10px;color:#1a1a2e;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;">Fund Name</td>
  <td style="padding:10px 12px;font-size:10px;color:#1a1a2e;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;text-align:right;width:90px;">{CONFIG['month_name']}</td>
  <td style="padding:10px 12px;font-size:10px;color:#1a1a2e;text-transform:uppercase;letter-spacing:0.5px;font-weight:700;text-align:right;width:120px;">Since Inception</td>
</tr>
{table_row(CONFIG['ticker'], CONFIG['fund_name'], '#ffffff', True)}
{table_row('SPXT', 'S&P 500 Total Return Index', '#ffffff', False)}
</table>
<div style="font-size:11px;color:#636e72;margin-top:12px;font-style:italic;">For current standardized performance, <a href="{CONFIG['product_url']}" style="color:#1a1a2e;text-decoration:underline;">click here</a>.</div>
</td></tr>

<!-- Chart -->
<tr><td style="padding:6px 30px 5px;">
  <div style="{SEC_HDR}">{CONFIG['month_name']} {CONFIG['year']} Cumulative Return</div>
</td></tr>
<tr><td style="padding:0 30px 18px;">
  <img src="data:image/png;base64,{chart_b64}" width="720" style="display:block;max-width:100%;height:auto;border:1px solid #e0e4e8;" alt="Cumulative return chart" />
</td></tr>

<!-- Footer -->
<tr><td style="padding:14px 30px 18px;">
  <div style="font-size:9.5px;color:#8a8a9a;line-height:1.5;border-top:1px solid #dee2e6;padding-top:10px;">
    Data as of {end_str}. Source: Bloomberg. Daily total returns. Inception: {incep_str}. Past performance is not indicative of future results.
  </div>
</td></tr>

</table></td></tr></table></body></html>"""

# ══════════════════════════════════════════════════════════════════════════
# Write output
# ══════════════════════════════════════════════════════════════════════════
out_dir = Path(CONFIG['output_dir'])
out_dir.mkdir(parents=True, exist_ok=True)

tag = f'{CONFIG["ticker"]}_{CONFIG["year"]}-{CONFIG["month_num"]:02d}'
html_path = out_dir / f'{tag}_commentary.html'
chart_path = out_dir / f'{tag}_chart.png'

html_path.write_text(html, encoding='utf-8')
chart_path.write_bytes(chart_png)

print(f'Done: {html_path}')
print(f'Chart: {chart_path}')
print()
print('Open HTML in browser -> Ctrl+A, Ctrl+C -> paste into Outlook.')
