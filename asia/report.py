"""
REX Asia AUM Monthly Report -- PDF Generator v11
Structure: Cover > Overview Charts (2pp) > Tables+Flows+Exchange > T-REX > MicroSectors > Appendix
"""
from __future__ import annotations

import io
import os
from datetime import date
from decimal import Decimal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates

import psycopg2
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image,
    KeepTogether, CondPageBreak,
)

# ─── Colors ──────────────────────────────────────────────────────────────────

_NAVY = "#1a1a2e"
_BLUE = "#0984e3"
_GREEN = "#27ae60"
_ORANGE = "#e67e22"
_RED = "#e74c3c"
_LIGHT = "#f5f7fa"
_BG = "#ffffff"
_GRAY = "#b2bec3"

NAVY = colors.HexColor(_NAVY)
BLUE = colors.HexColor(_BLUE)
GREEN = colors.HexColor(_GREEN)
ORANGE = colors.HexColor(_ORANGE)
RED = colors.HexColor(_RED)
LIGHT_BG = colors.HexColor(_LIGHT)
LIGHT_GREEN = colors.HexColor("#e8f5e9")
BORDER = colors.HexColor("#cccccc")
DARK_BLUE = colors.HexColor("#2d3436")

FAM_COLORS = {"T-REX": "#0984e3", "MicroSectors": "#27ae60", "Other": "#e67e22"}
FAM_RL_COLORS = {"T-REX": BLUE, "MicroSectors": GREEN, "Other": ORANGE}
COUNTRY_COLORS = {
    "Korea": "#0984e3", "Hong Kong": "#e74c3c", "Japan": "#27ae60",
    "Singapore": "#e67e22", "Malaysia": "#8e44ad", "Thailand": "#f39c12",
    "Taiwan": "#2980b9",
}

# ─── Styles ──────────────────────────────────────────────────────────────────

def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CoverTitle", parent=styles["Title"],
        fontSize=30, textColor=colors.white, alignment=0, spaceAfter=6,
        fontName="Helvetica-Bold", leading=36))
    styles.add(ParagraphStyle("CoverSub", parent=styles["Normal"],
        fontSize=12, textColor=colors.HexColor("#ccd5e0"), alignment=0, spaceAfter=2))
    styles.add(ParagraphStyle("SectionHead", parent=styles["Heading2"],
        fontSize=13, textColor=colors.white, spaceBefore=0, spaceAfter=0,
        fontName="Helvetica-Bold", leading=18))
    styles.add(ParagraphStyle("SubHead", parent=styles["Heading3"],
        fontSize=9, textColor=NAVY, spaceBefore=4, spaceAfter=2,
        fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Body", parent=styles["Normal"],
        fontSize=9, leading=12, spaceAfter=4))
    styles.add(ParagraphStyle("ExecBullet", parent=styles["Normal"],
        fontSize=9, leading=13, spaceAfter=3, leftIndent=16,
        bulletIndent=0, bulletFontSize=9))
    styles.add(ParagraphStyle("SmallNote", parent=styles["Normal"],
        fontSize=7, textColor=colors.grey, leading=9))
    styles.add(ParagraphStyle("CellText", parent=styles["Normal"],
        fontSize=7, leading=9, wordWrap='CJK'))
    styles.add(ParagraphStyle("KPIValue", parent=styles["Normal"],
        fontSize=24, textColor=NAVY, alignment=1, fontName="Helvetica-Bold",
        leading=28))
    styles.add(ParagraphStyle("KPILabel", parent=styles["Normal"],
        fontSize=8, textColor=colors.grey, alignment=1, leading=10))
    styles.add(ParagraphStyle("AppendixCell", parent=styles["Normal"],
        fontSize=6.5, leading=8, wordWrap='CJK'))
    return styles


_RULE = colors.HexColor("#dde1e6")

def _table_style():
    """Minimal table: bold header with bottom rule, light horizontal lines only."""
    return TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, NAVY),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, _RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])


def _kpi_table_style():
    return TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])


# ─── Formatters ──────────────────────────────────────────────────────────────

def _fmt_money(val):
    if val is None or val == 0:
        return "-"
    v = float(val)
    if abs(v) >= 1e9:
        return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:,.1f}M"

def _fmt_pct(val):
    if val is None:
        return "-"
    return f"{float(val) * 100:.1f}%"

def _fmt_pct_change(val):
    if val is None:
        return "-"
    v = float(val) * 100
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"

def _color_for_change(val):
    if val is None or float(val) == 0:
        return colors.black
    return GREEN if float(val) > 0 else RED

def _arrow(val):
    """Return arrow string for positive/negative change."""
    if val is None or float(val) == 0:
        return ""
    return "+" if float(val) > 0 else ""

def _colored_money(val):
    """Return money string wrapped in green/red font tag."""
    if val is None or val == 0:
        return "-"
    v = float(val)
    c = _GREEN if v > 0 else _RED
    return f'<font color="{c}">{_fmt_money(val)}</font>'

def _colored_pct_change(val):
    """Return pct change string wrapped in green/red font tag."""
    if val is None:
        return "-"
    v = float(val)
    c = _GREEN if v > 0 else _RED
    return f'<font color="{c}">{_fmt_pct_change(val)}</font>'


# ─── Chart Helpers ───────────────────────────────────────────────────────────

def _style_ax(ax, fig):
    ax.set_facecolor(_BG)
    fig.set_facecolor(_BG)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("black")
        ax.spines[spine].set_linewidth(0.5)
    ax.tick_params(colors="black", labelsize=8)
    ax.yaxis.label.set_color("black")
    ax.xaxis.label.set_color("black")


def _fig_to_image(fig, width=6.8, height=None):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    w = width * inch
    from PIL import Image as PILImage
    pil = PILImage.open(buf)
    aspect = pil.height / pil.width
    buf.seek(0)
    h = w * aspect if height is None else height * inch
    return Image(buf, width=w, height=h)


def chart_stacked_area(rollup, title="Asia AUM by Product Family"):
    dates = [r["month_end"] for r in rollup]
    trex = [float(r.get("trex_asian_aum", 0) or 0) / 1e6 for r in rollup]
    micro = [float(r.get("micro_asian_aum", 0) or 0) / 1e6 for r in rollup]
    other = [float(r.get("rex_nonll_asian_aum", 0) or 0) / 1e6 for r in rollup]

    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    _style_ax(ax, fig)
    ax.stackplot(dates, trex, micro, other,
                 labels=["T-REX", "MicroSectors", "Other"],
                 colors=[FAM_COLORS["T-REX"], FAM_COLORS["MicroSectors"],
                         FAM_COLORS["Other"]], alpha=0.85)
    ax.set_title(title, fontsize=11, fontweight="bold", color=_NAVY, loc='left', pad=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}M"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

    # Callout annotations for latest values
    if trex and micro and other:
        ax.annotate(f"T-REX: ${trex[-1]:,.0f}M",
                    xy=(dates[-1], trex[-1] / 2),
                    xytext=(10, 0), textcoords="offset points",
                    fontsize=7, fontweight="bold", color=FAM_COLORS["T-REX"])
        ax.annotate(f"Micro: ${micro[-1]:,.0f}M",
                    xy=(dates[-1], trex[-1] + micro[-1] / 2),
                    xytext=(10, 0), textcoords="offset points",
                    fontsize=7, fontweight="bold", color=FAM_COLORS["MicroSectors"])

    fig.tight_layout()
    return _fig_to_image(fig)


def chart_pct_line(rollup, key="pct_asia_aum", title="Asia as % of Total REX AUM"):
    dates = [r["month_end"] for r in rollup]
    vals = [float(r.get(key, 0) or 0) * 100 for r in rollup]

    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    _style_ax(ax, fig)
    ax.plot(dates, vals, color=_BLUE, linewidth=2.2, marker="o", markersize=4, zorder=3)
    ax.fill_between(dates, 0, vals, color=_BLUE, alpha=0.08)
    if vals:
        ax.annotate(f"{vals[-1]:.1f}%", xy=(dates[-1], vals[-1]),
                     xytext=(8, 4), textcoords="offset points",
                     fontsize=9, fontweight="bold", color=_NAVY)
    ax.set_title(title, fontsize=11, fontweight="bold", color=_NAVY, loc='left', pad=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    fig.tight_layout()
    return _fig_to_image(fig)


def chart_donut(labels, values, title="Country Breakdown", width=3.8):
    total = sum(values)
    filtered = [(l, v) for l, v in zip(labels, values) if v / total > 0.005]
    if not filtered:
        return None
    labels_f, values_f = zip(*filtered)
    chart_colors = [COUNTRY_COLORS.get(l, _GRAY) for l in labels_f]

    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    fig.set_facecolor(_BG)
    wedges, texts, autotexts = ax.pie(
        values_f, labels=None, autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
        colors=chart_colors, startangle=90,
        pctdistance=0.78, wedgeprops={"width": 0.4, "edgecolor": "white", "linewidth": 1.5})
    for t in autotexts:
        t.set_fontsize(7); t.set_fontweight("bold"); t.set_color("white")
    ax.text(0, 0, f"${total / 1e6:,.0f}M", ha="center", va="center",
            fontsize=13, fontweight="bold", color=_NAVY)
    # Legend with AUM values
    legend_labels = [f"{l}  ${v/1e6:,.1f}M" for l, v in zip(labels_f, values_f)]
    ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
              fontsize=8, frameon=False, handlelength=1.5)
    ax.set_title(title, fontsize=10, fontweight="bold", color=_NAVY, loc='left', pad=8)
    fig.subplots_adjust(right=0.58)
    return _fig_to_image(fig, width=width)


def chart_top_funds_bar(funds, n=15, title="Top Funds by Asia AUM", width=6.8):
    top = funds[:n]
    if not top:
        return None
    top = list(reversed(top))
    tickers = [f["fund"] for f in top]
    aums = [float(f["asia_aum"]) / 1e6 for f in top]

    fig_h = max(2.5, 0.28 * len(top))
    fig, ax = plt.subplots(figsize=(7.5, fig_h))
    _style_ax(ax, fig)
    ax.grid(False)
    bar_colors = [_BLUE if a > 50 else ("#6baed6" if a > 10 else _GRAY) for a in aums]
    bars = ax.barh(tickers, aums, color=bar_colors, edgecolor="none", height=0.65)
    for bar, val in zip(bars, aums):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}M", va="center", fontsize=7, color="black")
    ax.set_title(title, fontsize=11, fontweight="bold", color=_NAVY, loc='left', pad=10)
    ax.tick_params(axis="y", labelsize=8, colors='black')
    ax.tick_params(axis="x", labelsize=8, colors='black')
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}M"))
    fig.tight_layout()
    return _fig_to_image(fig, width=width)


def chart_rex_vs_asia_area(rollup, title="REX Total AUM vs Asia AUM"):
    dates = [r["month_end"] for r in rollup]
    rex_total = [float(r.get("total_rex_aum", 0) or 0) / 1e6 for r in rollup]
    asia_total = [float(r.get("total_asian_aum", 0) or 0) / 1e6 for r in rollup]

    fig, ax = plt.subplots(figsize=(7.5, 2.5))
    _style_ax(ax, fig)
    ax.fill_between(dates, 0, rex_total, color=_NAVY, alpha=0.15, label="REX Total AUM")
    ax.fill_between(dates, 0, asia_total, color=_BLUE, alpha=0.5, label="Asia AUM")
    ax.plot(dates, rex_total, color=_NAVY, linewidth=1.5, alpha=0.6)
    ax.plot(dates, asia_total, color=_BLUE, linewidth=2, zorder=3)

    if rex_total and asia_total:
        ax.annotate(f"${rex_total[-1]:,.0f}M", xy=(dates[-1], rex_total[-1]),
                     xytext=(8, 6), textcoords="offset points",
                     fontsize=8, fontweight="bold", color=_NAVY)
        ax.annotate(f"${asia_total[-1]:,.0f}M", xy=(dates[-1], asia_total[-1]),
                     xytext=(8, -12), textcoords="offset points",
                     fontsize=8, fontweight="bold", color=_BLUE)

    ax.set_title(title, fontsize=11, fontweight="bold", color=_NAVY, loc='left', pad=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}M"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    return _fig_to_image(fig)


def chart_family_area(data_rows, title="AUM Over Time", figsize=(7.5, 2.8), width=6.8):
    dates = [r["month_end"] for r in data_rows]
    aums = [float(r.get("asian_aum", 0) or 0) / 1e6 for r in data_rows]

    fig, ax = plt.subplots(figsize=figsize)
    _style_ax(ax, fig)
    ax.fill_between(dates, 0, aums, color=_BLUE, alpha=0.15)
    ax.plot(dates, aums, color=_BLUE, linewidth=2.2, zorder=3)
    if aums:
        ax.annotate(f"${aums[-1]:,.0f}M", xy=(dates[-1], aums[-1]),
                     xytext=(8, 4), textcoords="offset points",
                     fontsize=9, fontweight="bold", color=_NAVY)
    ax.set_title(title, fontsize=10, fontweight="bold", color=_NAVY, loc='left', pad=6)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}M"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    fig.tight_layout()
    return _fig_to_image(fig, width=width)


def chart_family_pct_line(data_rows, title="% of Family AUM in Asia", figsize=(7.5, 2.8), width=6.8):
    dates = [r["month_end"] for r in data_rows]
    pcts = [float(r.get("asian_fund_pct", 0) or 0) * 100 for r in data_rows]

    fig, ax = plt.subplots(figsize=figsize)
    _style_ax(ax, fig)
    ax.plot(dates, pcts, color=_RED, linewidth=2, linestyle="--", marker="o", markersize=4)
    if pcts:
        ax.annotate(f"{pcts[-1]:.1f}%", xy=(dates[-1], pcts[-1]),
                     xytext=(8, 4), textcoords="offset points",
                     fontsize=9, fontweight="bold", color=_NAVY)
    ax.set_title(title, fontsize=10, fontweight="bold", color=_NAVY, loc='left', pad=6)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    fig.tight_layout()
    return _fig_to_image(fig, width=width)


# ─── Database ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia")


def fetch_family_rollup(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT month_end, total_asian_aum, total_rex_aum, pct_asia_aum,
               trex_asian_aum, pct_trex_in_asia,
               micro_asian_aum, pct_micro_in_asia,
               rex_nonll_asian_aum, pct_rex_nonll_in_asia,
               total_asia_aum_mom_pct, market_move, flows
        FROM asia_family_rollup ORDER BY month_end
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_country_breakdown(conn, month_end):
    cur = conn.cursor()
    cur.execute("""
        SELECT country_name, total_country_aum_usd
        FROM country_monthly_total_aum
        WHERE month_end = %s ORDER BY total_country_aum_usd DESC
    """, (month_end,))
    return cur.fetchall()


def fetch_country_mom(conn, current_month_end, prior_month_end):
    """Fetch country AUM for current and prior month, return dict with MoM."""
    cur = conn.cursor()
    results = {}
    for me in [current_month_end, prior_month_end]:
        cur.execute("""
            SELECT country_name, total_country_aum_usd
            FROM country_monthly_total_aum WHERE month_end = %s
        """, (me,))
        results[me] = {r[0]: float(r[1]) for r in cur.fetchall()}
    return results[current_month_end], results[prior_month_end]


def fetch_fund_summary(conn, view_name="asia_fund_latest_summary"):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT fund, asia_pct_of_fund, asia_aum, total_aum_usd,
               asia_aum_mom_pct, market_move, flows
        FROM {view_name} ORDER BY asia_aum DESC
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_family_report(conn, view_name):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT month_end, asian_aum, total_rex_aum, asian_fund_pct,
               market_move, monthly_flows, flows_since_inception
        FROM {view_name} ORDER BY month_end
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_country_by_family(conn, view_name):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {view_name} ORDER BY month_end DESC LIMIT 1")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_exchange_breakdown(conn, month_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT ex.name as exchange, c.name as country,
               count(DISTINCT exa.etp_id) as etps,
               sum(exa.exchange_aum_usd) as aum
        FROM etp_exchange_monthly_aum exa
        JOIN exchange ex ON ex.exchange_id = exa.exchange_id
        JOIN country c ON c.country_id = ex.country_id
        WHERE exa.month_id = %s
        GROUP BY ex.name, c.name
        ORDER BY sum(exa.exchange_aum_usd) DESC
    """, (month_id,))
    return cur.fetchall()


def fetch_exchange_mom(conn, current_month_id, prior_month_id):
    """Return {(exchange_name, country): aum} for both months."""
    cur_data = {}
    prior_data = {}
    for mid, target in [(current_month_id, cur_data), (prior_month_id, prior_data)]:
        rows = fetch_exchange_breakdown(conn, mid)
        for name, country, etps, aum in rows:
            target[(name, country)] = float(aum)
    return cur_data, prior_data


def fetch_flows_leaderboard(conn, view_name="asia_fund_latest_summary", top_n=10):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT fund, flows FROM {view_name}
        WHERE flows IS NOT NULL AND flows > 0
        ORDER BY flows DESC LIMIT %s
    """, (top_n,))
    gainers = cur.fetchall()
    cur.execute(f"""
        SELECT fund, flows FROM {view_name}
        WHERE flows IS NOT NULL AND flows < 0
        ORDER BY flows ASC LIMIT %s
    """, (top_n,))
    losers = cur.fetchall()
    return gainers, losers


def fetch_data_quality(conn, month_id):
    """What % of AUM is from fresh reported data vs repriced/carried-forward estimates?"""
    cur = conn.cursor()
    cur.execute("""
        SELECT source_type,
               sum(exchange_aum_usd) as aum,
               count(*) as rows
        FROM etp_exchange_monthly_aum WHERE month_id = %s
        GROUP BY source_type
    """, (month_id,))
    by_type = {r[0]: {'aum': float(r[1]), 'rows': r[2]} for r in cur.fetchall()}
    total = sum(v['aum'] for v in by_type.values())
    reported = by_type.get('reported', {}).get('aum', 0)
    return {
        "reported": reported,
        "estimated": total - reported,
        "total": total,
        "pct_reported": reported / total if total else 0,
        "by_type": by_type,
    }


# ─── Section Header Drawing Helper ──────────────────────────────────────────

def _section_header_flowable(title, usable_width):
    """Full-width navy banner with white text — strong visual section break."""
    p = Paragraph(title, ParagraphStyle(
        "SHInner", fontName="Helvetica-Bold", fontSize=12,
        textColor=colors.white, leading=16))
    tbl = Table([[p]], colWidths=[usable_width], rowHeights=[28])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


# ─── Report Builder ──────────────────────────────────────────────────────────

MONTH_ID = 13
PRIOR_MONTH_ID = 12


class AsiaReport:
    def __init__(self, month_end: date, prior_month_end: date):
        self.month_end = month_end
        self.prior_month_end = prior_month_end
        self.styles = _build_styles()
        self.conn = get_conn()
        self.elements = []
        self.usable_width = letter[0] - 1.2 * inch

    def _section_header(self, title):
        """Add a navy bar section header."""
        self.elements.append(Spacer(1, 6))
        self.elements.append(_section_header_flowable(title, self.usable_width))
        self.elements.append(Spacer(1, 6))

    def build(self, output_path: str):
        doc = SimpleDocTemplate(
            output_path, pagesize=letter,
            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        )

        self._cover_page()
        self._asia_overview_charts_page1()
        self._asia_overview_charts_page2()
        self._asia_overview_tables_and_exchange()
        self._family_section("T-REX", "trex_asia_aum_report",
                             "trex_asian_aum_by_country",
                             "asia_trex_funds_latest_summary")
        self._family_section("MicroSectors", "ms_asia_aum_report",
                             "ms_asian_aum_by_country",
                             "asia_ms_funds_latest_summary")
        self._appendix_fund_detail()

        doc.build(self.elements, onFirstPage=self._first_page_cb,
                  onLaterPages=self._later_page_cb)
        self.conn.close()
        print(f"Report saved: {output_path}")

    # ── Canvas Callbacks ─────────────────────────────────────────────────────

    def _draw_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(0.6 * inch, 0.35 * inch,
            f"REX Asia AUM Report | {self.month_end.strftime('%B %Y')}")
        canvas.drawRightString(letter[0] - 0.6 * inch, 0.35 * inch,
            f"Page {doc.page}")
        canvas.restoreState()

    def _first_page_cb(self, canvas, doc):
        """Cover page: navy gradient header bar + footer."""
        canvas.saveState()
        page_w, page_h = letter

        # Draw navy header bar (top 2 inches) with gradient effect
        bar_height = 2.0 * inch
        bar_top = page_h
        bar_bottom = page_h - bar_height

        # Gradient: draw thin horizontal slices from dark navy to slightly lighter
        steps = 40
        slice_h = bar_height / steps
        for i in range(steps):
            # Interpolate from #1a1a2e (top) to #2a2a4e (bottom)
            frac = i / steps
            r = int(0x1a + (0x2a - 0x1a) * frac)
            g = int(0x1a + (0x2a - 0x1a) * frac)
            b = int(0x2e + (0x4e - 0x2e) * frac)
            canvas.setFillColor(colors.HexColor(f"#{r:02x}{g:02x}{b:02x}"))
            y = bar_top - (i + 1) * slice_h
            canvas.rect(0, y, page_w, slice_h, fill=1, stroke=0)

        # Accent line at bottom of header bar
        canvas.setStrokeColor(colors.HexColor(_BLUE))
        canvas.setLineWidth(3)
        canvas.line(0, bar_bottom, page_w, bar_bottom)

        canvas.restoreState()
        self._draw_footer(canvas, doc)

    def _later_page_cb(self, canvas, doc):
        self._draw_footer(canvas, doc)

    # For backward compat
    def _footer(self, canvas, doc):
        self._draw_footer(canvas, doc)

    # ── Cover Page ───────────────────────────────────────────────────────────

    def _cover_page(self):
        s = self.styles
        rollup = fetch_family_rollup(self.conn)
        latest = rollup[-1] if rollup else {}

        # Title in the navy header bar area (positioned via spacer)
        self.elements.append(Spacer(1, 0.15 * inch))
        self.elements.append(Paragraph("REX Asia AUM Report", s["CoverTitle"]))
        self.elements.append(Paragraph(
            self.month_end.strftime("%B %Y"), s["CoverSub"]))
        self.elements.append(Spacer(1, 0.85 * inch))

        # Fetch data needed for cover page
        prior = rollup[-2] if len(rollup) >= 2 else {}
        total_asia = latest.get("total_asian_aum", 0)
        pct_asia = latest.get("pct_asia_aum")
        mom_pct = latest.get("total_asia_aum_mom_pct")
        mom_dollar = float(total_asia or 0) - float(prior.get("total_asian_aum", 0) or 0)

        total = float(latest.get("total_asian_aum", 0))
        mkt = float(latest.get("market_move", 0) or 0)
        flows = float(latest.get("flows", 0) or 0)
        countries = fetch_country_breakdown(self.conn, self.month_end)
        top_country = countries[0] if countries else ("Korea", 0)
        top_pct = float(top_country[1]) / total * 100 if total else 0
        dq = fetch_data_quality(self.conn, MONTH_ID)
        pct_rpt = dq["pct_reported"] * 100
        funds = fetch_fund_summary(self.conn)
        gainer = next((f for f in funds if f.get("flows") and float(f["flows"]) > 0), None)
        loser = next((f for f in reversed(funds) if f.get("flows") and float(f["flows"]) < 0), None)

        # ── Executive Summary (TOP) ──────────────────────────────────────────
        self._section_header("Executive Summary")

        if abs(mkt) > abs(flows):
            driver = f"market movements ({_fmt_money(mkt)})"
        else:
            driver = f"net flows ({_fmt_money(flows)})"

        bullets = []
        mom_str = _fmt_pct_change(mom_pct) if mom_pct else "flat"
        color = _GREEN if mom_pct and float(mom_pct) > 0 else _RED
        bullets.append(
            f'Asia AUM: <b>{_fmt_money(total)}</b>, '
            f'<font color="{color}"><b>{mom_str}</b></font> MoM. '
            f'Driver: {driver}.')

        if flows > 0:
            bullets.append(
                f'Inflows of <font color="{_GREEN}"><b>{_fmt_money(flows)}</b></font> '
                f'partially offset market decline of {_fmt_money(mkt)}.')
        elif flows < 0:
            bullets.append(
                f'Outflows of <font color="{_RED}"><b>{_fmt_money(flows)}</b></font> '
                f'compounded market decline of {_fmt_money(mkt)}.')

        bullets.append(
            f'{top_country[0]}: <b>{top_pct:.0f}%</b> of Asia AUM. '
            + (f'Top inflow: <b>{gainer["fund"]}</b> ({_fmt_money(gainer["flows"])}). '
               f'Top outflow: <b>{loser["fund"]}</b> ({_fmt_money(loser["flows"])}).'
               if gainer and loser else ''))

        bullets.append(
            f'Data: <b>{pct_rpt:.0f}%</b> directly reported by brokers, '
            f'{100-pct_rpt:.0f}% estimated (quarterly reporters scaled via fund-proportional method).')

        for b in bullets:
            self.elements.append(Paragraph(f"<bullet>&bull;</bullet> {b}", s["ExecBullet"]))

        self.elements.append(Spacer(1, 0.15 * inch))

        # ── 2x4 KPI Table ────────────────────────────────────────────────────
        mom_color = _GREEN if mom_pct and float(mom_pct) > 0 else (_RED if mom_pct and float(mom_pct) < 0 else _NAVY)
        num_countries = len(countries)
        num_exchanges = len(fetch_exchange_breakdown(self.conn, MONTH_ID))
        num_funds = len(funds)

        kpi_data = [
            (_fmt_money(total_asia), "Total Asia AUM", _NAVY),
            (_fmt_pct(pct_asia), "% of REX AUM", _NAVY),
            (_fmt_pct_change(mom_pct), "MoM Change", mom_color),
            (_fmt_money(mom_dollar), "MoM $ Change", mom_color),
            (str(num_countries), "Markets", _NAVY),
            (str(num_exchanges), "Exchanges", _NAVY),
            (str(num_funds), "Funds Tracked", _NAVY),
            (f"{pct_rpt:.0f}%", "Broker Reported", _NAVY),
        ]

        kpi_w = self.usable_width / 4
        kpi_vals_row1 = []
        kpi_lbls_row1 = []
        kpi_vals_row2 = []
        kpi_lbls_row2 = []
        for i, (val, label, vc) in enumerate(kpi_data):
            val_p = Paragraph(
                f'<font color="{vc}"><b>{val}</b></font>',
                ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=16,
                               alignment=1, leading=20))
            lbl_p = Paragraph(label,
                ParagraphStyle("kl", fontSize=7, textColor=colors.grey,
                               alignment=1, leading=9))
            if i < 4:
                kpi_vals_row1.append(val_p)
                kpi_lbls_row1.append(lbl_p)
            else:
                kpi_vals_row2.append(val_p)
                kpi_lbls_row2.append(lbl_p)

        kpi_tbl = Table(
            [kpi_vals_row1, kpi_lbls_row1, kpi_vals_row2, kpi_lbls_row2],
            colWidths=[kpi_w] * 4,
            rowHeights=[26, 12, 26, 12])
        kpi_tbl.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
            ("TOPPADDING", (0, 2), (-1, 2), 6),
            ("BOTTOMPADDING", (0, 3), (-1, 3), 4),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, _RULE),
        ]))
        self.elements.append(kpi_tbl)
        self.elements.append(Spacer(1, 0.15 * inch))

        # ── Product Family Breakdown ─────────────────────────────────────────
        trex = latest.get("trex_asian_aum", 0)
        micro = latest.get("micro_asian_aum", 0)
        other = latest.get("rex_nonll_asian_aum", 0)
        total_f = float(total_asia) if total_asia else 1

        prior_trex = float(prior.get("trex_asian_aum", 0) or 0)
        prior_micro = float(prior.get("micro_asian_aum", 0) or 0)
        prior_other = float(prior.get("rex_nonll_asian_aum", 0) or 0)

        fam_header = ["Product Family", "Asia AUM", "% of Total", "MoM Change"]
        fam_rows_data = [
            (_BLUE, "T-REX", trex, float(trex or 0) - prior_trex),
            (_GREEN, "MicroSectors", micro, float(micro or 0) - prior_micro),
            (_ORANGE, "Other (Osprey/Income)", other, float(other or 0) - prior_other),
        ]

        fam_rows = []
        for accent_hex, name, aum, chg in fam_rows_data:
            label = Paragraph(
                f'<font color="{accent_hex}"><b>|</b></font>&nbsp;&nbsp;{name}',
                ParagraphStyle("fam", fontSize=8, leading=10))
            chg_color = _GREEN if chg >= 0 else _RED
            chg_cell = Paragraph(
                f'<font color="{chg_color}">{_fmt_money(chg)}</font>',
                ParagraphStyle("fc", fontSize=7, alignment=2, leading=9))
            fam_rows.append([label, _fmt_money(aum),
                            _fmt_pct(float(aum) / total_f if total_f else 0),
                            chg_cell])

        cw = [self.usable_width * 0.34, self.usable_width * 0.22,
              self.usable_width * 0.22, self.usable_width * 0.22]
        fam_tbl = Table([fam_header] + fam_rows, colWidths=cw)
        ts = _table_style()
        ts.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
        fam_tbl.setStyle(ts)
        self.elements.append(fam_tbl)
        self.elements.append(Spacer(1, 0.15 * inch))

        # ── REX vs Asia AUM area chart ───────────────────────────────────────
        self.elements.append(chart_rex_vs_asia_area(rollup))

        self.elements.append(PageBreak())

    # ── Asia Overview Charts — Page 1 (stacked area + % line + donut) ────────

    def _asia_overview_charts_page1(self):
        self._section_header("Asia Overview")

        rollup = fetch_family_rollup(self.conn)
        self.elements.append(chart_stacked_area(rollup))
        self.elements.append(Spacer(1, 4))
        self.elements.append(chart_pct_line(rollup))
        self.elements.append(Spacer(1, 4))

        # Country donut — larger, centered
        countries = fetch_country_breakdown(self.conn, self.month_end)
        donut_img = chart_donut(
            [c[0] for c in countries], [float(c[1]) for c in countries],
            title="Country Breakdown")
        if donut_img:
            # Center the donut
            center_tbl = Table([[donut_img]], colWidths=[self.usable_width])
            center_tbl.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            self.elements.append(center_tbl)

        self.elements.append(PageBreak())

    # ── Asia Overview Charts — Page 2 (top funds bar + monthly summary) ──────

    def _asia_overview_charts_page2(self):
        s = self.styles
        self._section_header("Asia Overview (continued)")

        funds = fetch_fund_summary(self.conn)
        bar_img = chart_top_funds_bar(funds, n=15, title="Top 15 Funds by Asia AUM",
                                       width=6.8)
        if bar_img:
            self.elements.append(bar_img)

        self.elements.append(Spacer(1, 8))

        # Monthly summary table
        self._section_header("Monthly Summary")
        rollup = fetch_family_rollup(self.conn)
        header = ["Month", "Asia AUM", "REX AUM", "% Asia",
                  "MoM Chg", "Market Move", "Flows"]
        rows = []
        for r in rollup:
            rows.append([
                r["month_end"].strftime("%b %Y"),
                _fmt_money(r["total_asian_aum"]),
                _fmt_money(r["total_rex_aum"]),
                _fmt_pct(r["pct_asia_aum"]),
                Paragraph(_colored_pct_change(r["total_asia_aum_mom_pct"]),
                    ParagraphStyle("cell", fontSize=7.5, alignment=2, leading=10)),
                Paragraph(_colored_money(r["market_move"]),
                    ParagraphStyle("cell", fontSize=7.5, alignment=2, leading=10)),
                Paragraph(_colored_money(r["flows"]),
                    ParagraphStyle("cell", fontSize=7.5, alignment=2, leading=10)),
            ])
        data = [header] + rows
        cw = [self.usable_width * w for w in [0.12, 0.15, 0.15, 0.10, 0.14, 0.17, 0.17]]
        tbl = Table(data, colWidths=cw)
        ts = _table_style()
        ts.add("ALIGN", (1, 0), (3, -1), "RIGHT")
        if rows:
            ts.add("FONTNAME", (0, len(rows)), (-1, len(rows)), "Helvetica-Bold")
            ts.add("LINEABOVE", (0, len(rows)), (-1, len(rows)), 1, NAVY)
        tbl.setStyle(ts)
        self.elements.append(tbl)

        self.elements.append(PageBreak())

    # ── Tables + Exchange (merged, flowing) ────────────────────────────────

    def _asia_overview_tables_and_exchange(self):
        s = self.styles

        # Country + Flows side by side
        cur_countries, prior_countries = fetch_country_mom(
            self.conn, self.month_end, self.prior_month_end)
        total_country = sum(cur_countries.values())

        self._section_header("Country & Flow Analysis")

        c_header = ["Country", "AUM", "MoM"]
        c_rows = []
        for name in sorted(cur_countries, key=lambda k: cur_countries[k], reverse=True):
            aum = cur_countries[name]
            prior = prior_countries.get(name, 0)
            chg = aum - prior
            c_rows.append([name, _fmt_money(aum),
                Paragraph(_colored_money(chg),
                    ParagraphStyle("cc", fontSize=7, alignment=2, leading=9))])
        total_chg = total_country - sum(prior_countries.values())
        c_rows.append(["Total", _fmt_money(total_country),
                        Paragraph(_colored_money(total_chg),
                            ParagraphStyle("cc", fontSize=7, alignment=2, leading=9))])

        gap = 0.2 * inch
        half_w = (self.usable_width - gap) / 2
        c_data = [c_header] + c_rows
        c_tbl = Table(c_data, colWidths=[half_w * 0.42, half_w * 0.33, half_w * 0.25])
        c_ts = _table_style()
        c_ts.add("ALIGN", (1, 0), (1, -1), "RIGHT")
        c_ts.add("FONTNAME", (0, len(c_rows)), (-1, len(c_rows)), "Helvetica-Bold")
        c_ts.add("LINEABOVE", (0, len(c_rows)), (-1, len(c_rows)), 1, NAVY)
        c_tbl.setStyle(c_ts)

        # Flows leaderboard (right side)
        gainers, losers = fetch_flows_leaderboard(self.conn, "asia_fund_latest_summary", top_n=6)
        fl_rows = [["Fund", "Flow"]]
        for g in gainers[:6]:
            fl_rows.append([g[0],
                Paragraph(f'<font color="{_GREEN}">{_fmt_money(g[1])}</font>',
                    ParagraphStyle("fc", fontSize=7, alignment=2, leading=9))])
        for l in losers[:4]:
            fl_rows.append([l[0],
                Paragraph(f'<font color="{_RED}">{_fmt_money(l[1])}</font>',
                    ParagraphStyle("fc", fontSize=7, alignment=2, leading=9))])
        fl_tbl = Table(fl_rows, colWidths=[half_w * 0.40, half_w * 0.60])
        fl_ts = _table_style()
        fl_tbl.setStyle(fl_ts)

        col_w = (self.usable_width - gap) / 2
        side = Table(
            [[Paragraph("<b>Country Breakdown</b>", s["SubHead"]),
              Paragraph("<b>Flows Leaderboard</b>", s["SubHead"])],
             [c_tbl, fl_tbl]],
            colWidths=[col_w, col_w])
        side.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), gap / 2),
            ("LEFTPADDING", (1, 0), (1, -1), gap / 2),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        self.elements.append(side)
        self.elements.append(Spacer(1, 10))

        # Exchange Breakdown (same page)
        self._section_header("Exchange Breakdown")
        exchanges = fetch_exchange_breakdown(self.conn, MONTH_ID)
        cur_ex, prior_ex = fetch_exchange_mom(self.conn, MONTH_ID, PRIOR_MONTH_ID)
        total_ex = sum(float(r[3]) for r in exchanges)

        ex_header = ["Exchange", "Country", "ETPs", "AUM", "% of Asia", "MoM Chg"]
        ex_rows = []
        cell_style = self.styles["CellText"]
        for name, country, etps, aum in exchanges:
            aum_f = float(aum)
            prior = prior_ex.get((name, country), 0)
            chg = aum_f - prior
            ex_rows.append([Paragraph(name, cell_style), country, str(etps),
                         _fmt_money(aum),
                         _fmt_pct(aum_f / total_ex if total_ex else 0),
                         Paragraph(_colored_money(chg),
                            ParagraphStyle("ec", fontSize=7, alignment=2, leading=9))])
        total_prior = sum(prior_ex.values())
        total_chg_ex = total_ex - total_prior
        ex_rows.append(["Total", "", "", _fmt_money(total_ex), "100.0%",
                      Paragraph(_colored_money(total_chg_ex),
                        ParagraphStyle("ec", fontSize=7, alignment=2, leading=9))])

        ex_data = [ex_header] + ex_rows
        cw = [self.usable_width * w for w in [0.32, 0.12, 0.07, 0.17, 0.15, 0.17]]
        ex_tbl = Table(ex_data, colWidths=cw)
        ex_ts = _table_style()
        ex_ts.add("ALIGN", (2, 0), (4, -1), "RIGHT")
        ex_ts.add("FONTNAME", (0, len(ex_rows)), (-1, len(ex_rows)), "Helvetica-Bold")
        ex_ts.add("LINEABOVE", (0, len(ex_rows)), (-1, len(ex_rows)), 1, NAVY)
        ex_tbl.setStyle(ex_ts)
        self.elements.append(ex_tbl)

    # ── Family Section ───────────────────────────────────────────────────────

    def _family_section(self, family_name, report_view, country_view, fund_view):
        s = self.styles

        # Force new page for each family section — clear visual break
        self.elements.append(PageBreak())
        self._section_header(family_name)

        data_rows = fetch_family_report(self.conn, report_view)
        country_data = fetch_country_by_family(self.conn, country_view)
        funds = fetch_fund_summary(self.conn, fund_view)
        half_w = self.usable_width * 0.49
        gap = self.usable_width * 0.02

        # Row 1: Area chart + % line chart side-by-side
        area_chart = chart_family_area(data_rows,
            title=f"{family_name} Asia AUM", figsize=(5, 2.4), width=3.3)
        pct_chart = chart_family_pct_line(data_rows,
            title=f"{family_name} % in Asia", figsize=(5, 2.4), width=3.3)

        row1 = Table([[area_chart, pct_chart]],
                      colWidths=[half_w, half_w])
        row1.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        self.elements.append(row1)
        self.elements.append(Spacer(1, 6))

        # Row 2: Country donut + top funds bar side-by-side
        donut_img = None
        bar_img = None

        if country_data:
            cd = country_data[0]
            country_cols = {k: v for k, v in cd.items()
                           if k.endswith("_aum") and "total" not in k.lower()
                           and v is not None and float(v) > 0}
            if country_cols:
                labels = [k.replace("_aum", "").replace("_", " ").title()
                          for k in sorted(country_cols, key=lambda x: float(country_cols[x]),
                                          reverse=True)]
                values = [float(country_cols[k]) for k in sorted(
                    country_cols, key=lambda x: float(country_cols[x]), reverse=True)]
                donut_img = chart_donut(labels, values,
                    title=f"{family_name} by Country", width=3.3)

        if funds:
            bar_img = chart_top_funds_bar(funds, n=10,
                title=f"Top {family_name} Funds", width=3.3)

        if donut_img and bar_img:
            row2 = Table([[donut_img, bar_img]],
                          colWidths=[half_w, half_w])
            row2.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            self.elements.append(row2)
        elif donut_img:
            self.elements.append(donut_img)
        elif bar_img:
            self.elements.append(bar_img)

        self.elements.append(Spacer(1, 8))

        # Monthly summary table (last 6 months) + country + flows leaderboard
        self._section_header(f"{family_name} Monthly Summary")
        header = ["Month", "Asia AUM", "Total AUM", "% in Asia",
                  "Mkt Move", "Flows", "Cum. Flows"]
        rows = []
        recent_data = data_rows[-6:] if len(data_rows) > 6 else data_rows
        for r in recent_data:
            mm = r.get("market_move")
            fl = r.get("monthly_flows", r.get("flows"))
            rows.append([
                r["month_end"].strftime("%b %Y"),
                _fmt_money(r["asian_aum"]),
                _fmt_money(r["total_rex_aum"]),
                _fmt_pct(r["asian_fund_pct"]),
                Paragraph(_colored_money(mm),
                    ParagraphStyle("mc", fontSize=7, alignment=2, leading=9)),
                Paragraph(_colored_money(fl),
                    ParagraphStyle("mc", fontSize=7, alignment=2, leading=9)),
                _fmt_money(r.get("flows_since_inception")),
            ])

        data = [header] + rows
        cw = [self.usable_width * w for w in [0.10, 0.15, 0.15, 0.10, 0.17, 0.17, 0.16]]
        tbl = Table(data, colWidths=cw)
        ts = _table_style()
        ts.add("ALIGN", (1, 0), (3, -1), "RIGHT")
        ts.add("ALIGN", (6, 0), (6, -1), "RIGHT")
        if rows:
            ts.add("FONTNAME", (0, len(rows)), (-1, len(rows)), "Helvetica-Bold")
            ts.add("LINEABOVE", (0, len(rows)), (-1, len(rows)), 1, NAVY)
        tbl.setStyle(ts)
        self.elements.append(tbl)

        # Country table + flows leaderboard — kept together to avoid orphaned headers
        country_block = []
        if country_data:
            cd = country_data[0]
            country_cols = {k: v for k, v in cd.items()
                           if k.endswith("_aum") and k != "total_asian_aum"
                           and v is not None and float(v) > 0}
            total_fam = sum(float(v) for v in country_cols.values())

            c_header = ["Country", "AUM", "% of Family"]
            c_rows = []
            for col_name, val in sorted(country_cols.items(),
                                        key=lambda x: float(x[1]), reverse=True):
                pretty = col_name.replace("_aum", "").replace("_", " ").title()
                c_rows.append([pretty, _fmt_money(val),
                               _fmt_pct(float(val) / total_fam if total_fam else 0)])

            c_tbl = Table([c_header] + c_rows,
                          colWidths=[self.usable_width * w for w in [0.40, 0.30, 0.30]])
            ts = _table_style()
            ts.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
            c_tbl.setStyle(ts)

            country_block = [
                Spacer(1, 6),
                Paragraph("<b>Country Distribution</b>", s["SubHead"]),
                c_tbl, Spacer(1, 6),
            ]

        flows_block = self._flows_inline_elements(family_name, fund_view)
        self.elements.append(KeepTogether(country_block + flows_block))

    # ── Flows Inline (for family sections) ─────────────────────────────────

    def _flows_inline_elements(self, section_name, view_name):
        """Return list of flowable elements for flows leaderboard."""
        s = self.styles
        gainers, losers = fetch_flows_leaderboard(self.conn, view_name, top_n=6)
        gap = 0.2 * inch
        half = (self.usable_width - gap) / 2
        cw = [half * 0.40, half * 0.60]

        g_data = [["Fund", "Inflows"]]
        for g in gainers:
            g_data.append([g[0],
                Paragraph(f'<font color="{_GREEN}">{_fmt_money(g[1])}</font>',
                    ParagraphStyle("gc", fontSize=7, alignment=2, leading=9))])
        g_tbl = Table(g_data, colWidths=cw)
        g_tbl.setStyle(_table_style())

        l_data = [["Fund", "Outflows"]]
        for l in losers:
            l_data.append([l[0],
                Paragraph(f'<font color="{_RED}">{_fmt_money(l[1])}</font>',
                    ParagraphStyle("lc", fontSize=7, alignment=2, leading=9))])
        l_tbl = Table(l_data, colWidths=cw)
        l_tbl.setStyle(_table_style())

        col_w = (self.usable_width - gap) / 2
        wrapper = Table(
            [[Paragraph(f"<b>{section_name} Gainers</b>", s["SubHead"]),
              Paragraph(f"<b>{section_name} Losers</b>", s["SubHead"])],
             [g_tbl, l_tbl]],
            colWidths=[col_w, col_w])
        wrapper.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), gap / 2),
            ("LEFTPADDING", (1, 0), (1, -1), gap / 2),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return [wrapper]

    # ── Appendix: Fund Detail ────────────────────────────────────────────────

    def _appendix_fund_detail(self):
        s = self.styles

        # Appendix starts on its own page
        self.elements.append(PageBreak())

        # Single "All Funds" appendix with family identification
        funds = fetch_fund_summary(self.conn, "asia_fund_latest_summary")
        if not funds:
            return

        # Determine family membership
        trex_funds = {f["fund"] for f in fetch_fund_summary(self.conn, "asia_trex_funds_latest_summary")}
        micro_funds = {f["fund"] for f in fetch_fund_summary(self.conn, "asia_ms_funds_latest_summary")}

        self._section_header("Appendix: All Funds")

        header = ["Fund", "Family", "Asia AUM", "Total AUM",
                  "Asia %", "MoM Chg", "Flows"]
        cw = [self.usable_width * w for w in [0.12, 0.14, 0.16, 0.16, 0.10, 0.14, 0.18]]

        # Family color mapping for row highlights
        fam_bg = {
            "T-REX": colors.HexColor("#e3f2fd"),
            "MicroSectors": colors.HexColor("#e8f5e9"),
            "Other": colors.HexColor("#fff3e0"),
        }

        rows = []
        row_families = []
        for f in funds:
            mom = f.get("asia_aum_mom_pct")
            fl = f.get("flows")
            ticker = f["fund"]
            if ticker in trex_funds:
                family = "T-REX"
            elif ticker in micro_funds:
                family = "MicroSectors"
            else:
                family = "Other"
            row_families.append(family)

            fam_label = Paragraph(
                f'<font color="{FAM_COLORS.get(family, _GRAY)}"><b>{family}</b></font>',
                ParagraphStyle("fl", fontSize=6.5, leading=8))
            rows.append([
                ticker,
                fam_label,
                _fmt_money(f["asia_aum"]),
                _fmt_money(f["total_aum_usd"]),
                _fmt_pct(f["asia_pct_of_fund"]),
                Paragraph(_colored_pct_change(mom) if mom else "-",
                    ParagraphStyle("ac", fontSize=6.5, alignment=2, leading=8)),
                Paragraph(_colored_money(fl) if fl else "-",
                    ParagraphStyle("ac", fontSize=6.5, alignment=2, leading=8)),
            ])

        data = [header] + rows
        tbl = Table(data, colWidths=cw, repeatRows=1)
        ts = _table_style()
        ts.add("ALIGN", (2, 0), (-1, -1), "RIGHT")
        ts.add("FONTSIZE", (0, 1), (-1, -1), 6.5)
        tbl.setStyle(ts)
        self.elements.append(tbl)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    month_end = date(2026, 2, 28)
    prior_month_end = date(2026, 1, 31)
    output_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(output_dir, exist_ok=True)

    filename = f"REX_Asia_AUM_{month_end.strftime('%Y_%m')}_v11.pdf"
    output_path = os.path.join(output_dir, filename)

    report = AsiaReport(month_end, prior_month_end)
    report.build(output_path)
