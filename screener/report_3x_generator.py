"""PDF generator for 3x & 4x Leveraged ETF Filing Recommendation Report (V2).

Sections:
  Cover Page
  1. Executive Summary (3x KPIs, 2x KPIs, key findings, top 10)
  2. Market Landscape (Table 1: underlier popularity, Table 2: top 2x products)
  3. REX Track Record (Table 3: ALL T-REX products)
  4. Filing Recommendations (Tables 4a/b/c: 50/50/100 tiered candidates)
  5. 4x Filing Candidates (Table 5: low-vol 2x successes)
  6. Volatility & Blow-Up Risk (Table 6: scoped, AUM-sorted, exec-friendly odds)
  7. Methodology
"""
from __future__ import annotations

import io
import logging
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colors (brand consistency)
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#1a1a2e")
BLUE = colors.HexColor("#0984e3")
GREEN = colors.HexColor("#27ae60")
ORANGE = colors.HexColor("#e67e22")
RED = colors.HexColor("#e74c3c")
LIGHT_BG = colors.HexColor("#f5f7fa")
LIGHT_GREEN = colors.HexColor("#e8f5e9")
LIGHT_ORANGE = colors.HexColor("#fff3e0")
LIGHT_RED = colors.HexColor("#ffebee")
LIGHT_BLUE = colors.HexColor("#e3f2fd")
BORDER = colors.HexColor("#cccccc")
DARK_BLUE = colors.HexColor("#2d3436")
GOLD = colors.HexColor("#f39c12")
PURPLE = colors.HexColor("#8e44ad")

# Usable width: letter (612pt) - 2*0.6in margins (86.4pt) = ~526pt
# Target 518pt for all data tables (margin for grid lines)
TW = 518

RISK_COLORS = {
    "LOW": GREEN,
    "MEDIUM": ORANGE,
    "HIGH": RED,
    "EXTREME": PURPLE,
}

TIER_COLORS = {
    "tier_1": GREEN,
    "tier_2": BLUE,
    "tier_3": ORANGE,
}

REX_2X_COLORS = {
    "Yes": GREEN,
    "Filed": BLUE,
    "No": colors.grey,
}


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("ReportTitle", parent=styles["Title"],
        fontSize=22, textColor=NAVY, spaceAfter=6))
    styles.add(ParagraphStyle("CoverTitle", parent=styles["Title"],
        fontSize=28, textColor=NAVY, spaceAfter=4, alignment=1))
    styles.add(ParagraphStyle("CoverSub", parent=styles["Normal"],
        fontSize=14, textColor=BLUE, alignment=1, spaceAfter=4))
    styles.add(ParagraphStyle("SectionHead", parent=styles["Heading2"],
        fontSize=14, textColor=NAVY, spaceBefore=16, spaceAfter=8,
        borderWidth=1, borderColor=NAVY, borderPadding=4))
    styles.add(ParagraphStyle("SubHead", parent=styles["Heading3"],
        fontSize=11, textColor=BLUE, spaceBefore=10, spaceAfter=4))
    styles.add(ParagraphStyle("ReportBody", parent=styles["Normal"],
        fontSize=9, leading=12, spaceAfter=4))
    styles.add(ParagraphStyle("SmallNote", parent=styles["Normal"],
        fontSize=7, textColor=colors.grey, leading=9))
    styles.add(ParagraphStyle("CellWrap", parent=styles["Normal"],
        fontSize=7, leading=9, wordWrap="CJK"))
    styles.add(ParagraphStyle("KPI", parent=styles["Normal"],
        fontSize=18, textColor=NAVY, alignment=1, leading=22))
    styles.add(ParagraphStyle("KPILabel", parent=styles["Normal"],
        fontSize=7, textColor=colors.grey, alignment=1))
    styles.add(ParagraphStyle("BulletBody", parent=styles["Normal"],
        fontSize=9, leading=13, spaceAfter=2, leftIndent=12,
        bulletIndent=0, bulletFontSize=9))
    return styles


def _table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])


def _fmt_money(val, suffix="M"):
    if val is None or val == 0:
        return "-"
    if abs(val) >= 1000:
        return f"${val / 1000:,.1f}B"
    return f"${val:,.0f}{suffix}"


def _fmt_pct(val):
    if val is None:
        return "-"
    return f"{val:.1f}%"



# ===========================================================================
# Main entry point
# ===========================================================================

def generate_3x_report(
    snapshot: dict,
    top_2x: list[dict],
    underlier_pop: list[dict],
    rex_track: list[dict],
    tiers: dict[str, list[dict]],
    four_x_candidates: list[dict],
    risk_watchlist: list[dict],
    data_date: str | None = None,
) -> bytes:
    """Generate the full 3x & 4x Filing Recommendations PDF.

    Returns PDF bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch)
    styles = _build_styles()
    story = []
    report_date = datetime.now().strftime("%B %d, %Y")

    _build_cover_page(story, styles, report_date)
    story.append(PageBreak())

    _build_exec_summary(story, styles, snapshot, tiers, four_x_candidates, report_date, data_date)
    story.append(PageBreak())

    _build_market_landscape(story, styles, top_2x, underlier_pop)
    story.append(PageBreak())

    _build_rex_track_record(story, styles, rex_track)
    story.append(PageBreak())

    _build_recommendations(story, styles, tiers)
    story.append(PageBreak())

    _build_4x_candidates(story, styles, four_x_candidates)
    story.append(PageBreak())

    _build_risk_watchlist(story, styles, risk_watchlist)
    story.append(PageBreak())

    _build_methodology(story, styles, report_date)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()

    t1 = len(tiers.get("tier_1", []))
    t2 = len(tiers.get("tier_2", []))
    t3 = len(tiers.get("tier_3", []))
    log.info("3x/4x report generated: %d bytes, tiers: %d/%d/%d, 4x: %d",
             len(pdf_bytes), t1, t2, t3, len(four_x_candidates))
    return pdf_bytes


# ===========================================================================
# Cover Page
# ===========================================================================

def _build_cover_page(story, styles, report_date):
    story.append(Spacer(1, 2.5 * inch))
    story.append(Paragraph("3x &amp; 4x Leveraged ETF", styles["CoverTitle"]))
    story.append(Paragraph("Filing Recommendations", styles["CoverTitle"]))
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("REX Financial", styles["CoverSub"]))
    story.append(Paragraph(report_date, styles["CoverSub"]))
    story.append(Spacer(1, 0.5 * inch))

    # Divider line
    divider = Table([[""]],
        colWidths=[3 * inch])
    divider.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 2, NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    outer = Table([[divider]], colWidths=[7 * inch])
    outer.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(outer)

    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(
        "Prepared for: Scott Acheychek",
        ParagraphStyle("CoverPrepared", parent=styles["CoverSub"],
            fontSize=10, textColor=DARK_BLUE)))


# ===========================================================================
# Section 1: Executive Summary
# ===========================================================================

def _build_exec_summary(story, styles, snapshot, tiers, four_x_candidates, report_date, data_date=None):
    story.append(Paragraph("Executive Summary", styles["ReportTitle"]))
    date_line = f"Report Date: {report_date}"
    if data_date:
        date_line += f"  |  Bloomberg Data: {data_date}"
    story.append(Paragraph(date_line, styles["SmallNote"]))
    story.append(Spacer(1, 12))

    # KPI Row 1: 3x market
    story.append(Paragraph("3x Leveraged Market", styles["SubHead"]))
    kpi_3x = [[
        Paragraph(f"<b>{_fmt_money(snapshot['total_aum'])}</b>", styles["KPI"]),
        Paragraph(f"<b>{snapshot['product_count']}</b>", styles["KPI"]),
        Paragraph(f"<b>{snapshot['single_stock_count']}</b>", styles["KPI"]),
        Paragraph(f"<b>{_fmt_money(snapshot['rex_aum'])}</b>", styles["KPI"]),
    ], [
        Paragraph("Total 3x AUM", styles["KPILabel"]),
        Paragraph("3x Products", styles["KPILabel"]),
        Paragraph("3x Single Stock", styles["KPILabel"]),
        Paragraph("REX 3x AUM", styles["KPILabel"]),
    ]]
    kpi_table = Table(kpi_3x, colWidths=[TW // 4] * 4)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 1, BORDER),
        ("LINEBEFORE", (1, 0), (1, -1), 0.5, BORDER),
        ("LINEBEFORE", (2, 0), (2, -1), 0.5, BORDER),
        ("LINEBEFORE", (3, 0), (3, -1), 0.5, BORDER),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 8))

    # KPI Row 2: 2x market (context)
    story.append(Paragraph("2x Leveraged Market (Context)", styles["SubHead"]))
    kpi_2x = [[
        Paragraph(f"<b>{_fmt_money(snapshot.get('total_2x_aum', 0))}</b>", styles["KPI"]),
        Paragraph(f"<b>{snapshot.get('total_2x_count', 0)}</b>", styles["KPI"]),
        Paragraph(f"<b>{snapshot.get('ss_2x_count', 0)}</b>", styles["KPI"]),
        Paragraph(f"<b>{_fmt_money(snapshot.get('rex_2x_aum', 0))}</b>", styles["KPI"]),
    ], [
        Paragraph("Total 2x AUM", styles["KPILabel"]),
        Paragraph("2x Products", styles["KPILabel"]),
        Paragraph("2x Single Stock", styles["KPILabel"]),
        Paragraph("REX 2x AUM", styles["KPILabel"]),
    ]]
    kpi_2x_table = Table(kpi_2x, colWidths=[TW // 4] * 4)
    kpi_2x_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BLUE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("BOX", (0, 0), (-1, -1), 1, BORDER),
        ("LINEBEFORE", (1, 0), (1, -1), 0.5, BORDER),
        ("LINEBEFORE", (2, 0), (2, -1), 0.5, BORDER),
        ("LINEBEFORE", (3, 0), (3, -1), 0.5, BORDER),
    ]))
    story.append(kpi_2x_table)
    story.append(Spacer(1, 14))

    # Key findings
    t1 = tiers.get("tier_1", [])
    t2 = tiers.get("tier_2", [])
    t3 = tiers.get("tier_3", [])
    total_candidates = len(t1) + len(t2) + len(t3)

    story.append(Paragraph("Key Findings", styles["SubHead"]))

    findings = []

    if snapshot.get("single_stock_count", 0) == 0:
        findings.append(
            "<b>Zero 3x single-stock leveraged ETFs exist today.</b> "
            "The entire single-stock 3x market is greenfield. Every underlier is a first-mover opportunity.")

    findings.append(
        f"<b>{total_candidates}</b> stocks identified as 3x filing candidates: "
        f"<font color='#27ae60'><b>{len(t1)} Tier 1</b></font> (file now), "
        f"<font color='#0984e3'><b>{len(t2)} Tier 2</b></font> (file soon), "
        f"<font color='#e67e22'><b>{len(t3)} Tier 3</b></font> (monitor).",
    )

    if t1:
        total_2x_aum = sum(c.get("aum_2x", 0) for c in t1)
        findings.append(
            f"Tier 1 stocks have <b>{_fmt_money(total_2x_aum)}</b> combined 2x product AUM, "
            f"proving strong market demand for leveraged exposure to these names.")

    if four_x_candidates:
        findings.append(
            f"<b>{len(four_x_candidates)}</b> stocks with existing 2x products identified for "
            f"<font color='#8e44ad'><b>4x filing</b></font> (daily vol &lt;20%).")

    if snapshot.get("top_issuers"):
        top_issuer = snapshot["top_issuers"][0]
        findings.append(
            f"The largest 3x issuer is <b>{top_issuer['issuer']}</b> with "
            f"{_fmt_money(top_issuer['aum'])} AUM across {top_issuer['count']} products.")

    for f in findings:
        story.append(Paragraph(f"<bullet>&bull;</bullet> {f}", styles["BulletBody"]))

    story.append(Spacer(1, 14))

    # Top 10 Tier 1 mini-table (sorted by 3x filing score)
    if t1:
        story.append(Paragraph("Top Tier 1 Recommendations (by 3x Filing Score)", styles["SubHead"]))
        header = ["#", "Stock", "Sector", "3x Score", "2x Cnt", "2x AUM", "REX 2x", "Risk"]
        data = [header]
        for i, c in enumerate(t1[:10]):
            data.append([
                str(i + 1),
                c["ticker"],
                c["sector"],
                f"{c['score']:.0f}",
                str(c.get("count_2x", 0)),
                _fmt_money(c["aum_2x"]),
                c.get("rex_2x", "No"),
                c["risk"],
            ])
        t = Table(data, colWidths=[25, 68, 100, 58, 48, 82, 62, 75])
        ts = _table_style()
        for i, c in enumerate(t1[:10]):
            # Risk color
            rc = RISK_COLORS.get(c["risk"], GREEN)
            ts.add("TEXTCOLOR", (7, i + 1), (7, i + 1), rc)
            ts.add("FONTNAME", (7, i + 1), (7, i + 1), "Helvetica-Bold")
            # REX 2x color
            r2c = REX_2X_COLORS.get(c.get("rex_2x", "No"), colors.grey)
            ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), r2c)
            ts.add("FONTNAME", (6, i + 1), (6, i + 1), "Helvetica-Bold")
        t.setStyle(ts)
        story.append(t)


# ===========================================================================
# Section 2: Market Landscape
# ===========================================================================

def _build_market_landscape(story, styles, top_2x, underlier_pop):
    story.append(Paragraph("Market Landscape", styles["ReportTitle"]))
    story.append(Spacer(1, 8))

    # --- Table 1 (was Table 2): Most Popular Underliers - FIRST ---
    story.append(Paragraph("Table 1: Most Popular Underliers by 2x AUM", styles["SectionHead"]))
    story.append(Paragraph(
        "Stocks with the highest total 2x single-stock product AUM. "
        "Every underlier is a first-mover 3x opportunity (zero 3x single-stock products exist).",
        styles["SmallNote"]))
    story.append(Spacer(1, 4))

    _build_paginated_table(
        story, styles, underlier_pop,
        header=["#", "Stock", "Sector", "2x Cnt", "2x Total AUM", "REX 2x"],
        col_widths=[28, 72, 155, 58, 135, 70],
        row_fn=lambda i, r: [
            str(i + 1),
            r["underlier"],
            Paragraph(str(r["sector"]), styles["CellWrap"]),
            str(r["count_2x"]),
            _fmt_money(r["aum_2x"]),
            r.get("rex_2x", "No"),
        ],
        rows_per_page=30,
        color_fn=lambda i, r, ts: _color_rex_2x(ts, 5, i, r.get("rex_2x", "No")),
    )

    rex_count = sum(1 for r in underlier_pop if r.get("rex_2x") in ("Yes", "Filed"))
    story.append(Paragraph(
        f"REX has 2x products on <b>{rex_count}</b> of {len(underlier_pop)} top underliers.",
        styles["SmallNote"]))

    story.append(PageBreak())

    # --- Table 2 (was Table 1): Top 100 2x Single Stock ETFs ---
    story.append(Paragraph("Table 2: Top 2x Single Stock ETFs by AUM", styles["SectionHead"]))
    story.append(Paragraph(
        "The highest-AUM 2x single-stock leveraged ETFs. These represent proven market demand "
        "for leveraged exposure to specific stocks. REX 2x column indicates if REX has a 2x product on the underlier.",
        styles["SmallNote"]))
    story.append(Spacer(1, 4))

    _build_paginated_table(
        story, styles, top_2x,
        header=["#", "Ticker", "Issuer", "Underlier", "Dir", "AUM ($M)", "Flow 1M", "REX 2x"],
        col_widths=[25, 68, 118, 68, 45, 75, 65, 54],
        row_fn=lambda i, r: [
            str(i + 1),
            r["ticker"],
            Paragraph(r["issuer"][:18], styles["CellWrap"]),
            r["underlier"],
            r["direction"][:5] if r["direction"] else "-",
            _fmt_money(r["aum"]),
            _fmt_money(r["flow_1m"]),
            r.get("rex_2x", "No"),
        ],
        rows_per_page=30,
        color_fn=lambda i, r, ts: _color_rex_2x(ts, 7, i, r.get("rex_2x", "No")),
    )


# ===========================================================================
# Section 3: REX Track Record (ALL T-REX products)
# ===========================================================================

def _build_rex_track_record(story, styles, rex_track):
    story.append(Paragraph("REX Track Record", styles["ReportTitle"]))
    story.append(Paragraph(
        "All T-REX products sorted by AUM. 3x Filing Score blends stock fundamentals (40%) with "
        "2x AUM demand signal (60%). Category shows product type.",
        styles["SmallNote"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Table 3: All T-REX Products", styles["SectionHead"]))

    _build_paginated_table(
        story, styles, rex_track,
        header=["#", "Fund", "Underlier", "Lev", "Dir", "AUM ($M)", "3x Score", "Category"],
        col_widths=[28, 88, 78, 40, 48, 78, 60, 98],
        row_fn=lambda i, r: [
            str(i + 1),
            r["fund_ticker"],
            r["underlier"],
            r["leverage"],
            r["direction"][:5] if r["direction"] else "-",
            _fmt_money(r["aum"]),
            f"{r['score']:.0f}" if r.get("score") is not None else "-",
            r.get("category", "-"),
        ],
        rows_per_page=30,
        color_fn=_color_track_record_score,
    )

    # Summary
    if rex_track:
        scored = [r for r in rex_track if r.get("score") is not None]
        with_aum = [r for r in rex_track if r.get("aum", 0) > 0]
        categories = {}
        for r in rex_track:
            cat = r.get("category", "Other")
            categories[cat] = categories.get(cat, 0) + 1
        cat_str = ", ".join(f"{v} {k}" for k, v in sorted(categories.items(), key=lambda x: -x[1]))
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            f"<b>{len(rex_track)}</b> T-REX products total (<b>{len(with_aum)}</b> with AUM &gt; 0). "
            f"<b>{len(scored)}</b> have underlier scores. Breakdown: {cat_str}.",
            styles["SmallNote"]))


# ===========================================================================
# Section 4: Filing Recommendations (Tiered - 50/50/100)
# ===========================================================================

def _build_recommendations(story, styles, tiers):
    story.append(Paragraph("3x Filing Recommendations", styles["ReportTitle"]))
    story.append(Spacer(1, 4))

    t1 = tiers.get("tier_1", [])
    t2 = tiers.get("tier_2", [])
    t3 = tiers.get("tier_3", [])
    total = len(t1) + len(t2) + len(t3)

    story.append(Paragraph(
        f"<b>{total}</b> candidates across 3 tiers. Ranked by <b>3x Filing Score</b> "
        f"(40% stock fundamentals + 60% proven 2x market demand). "
        f"REX 2x column shows whether REX already has a 2x filing on that underlier.",
        styles["ReportBody"]))
    story.append(Spacer(1, 8))

    # Tier 1
    if t1:
        story.append(Paragraph(
            f"Tier 1: File Immediately ({len(t1)} stocks)", styles["SectionHead"]))
        story.append(Paragraph(
            "Proven 2x market demand (AUM > 0). Sorted by 3x Filing Score (40% fundamentals + 60% 2x AUM).",
            styles["SmallNote"]))
        story.append(Spacer(1, 4))
        _build_tier_table(story, styles, t1, "tier_1")

    # Tier 2
    if t2:
        if t1:
            story.append(PageBreak())
        story.append(Paragraph(
            f"Tier 2: File Soon ({len(t2)} stocks)", styles["SectionHead"]))
        story.append(Paragraph(
            "Strong fundamentals, may not have existing 2x products yet. Sorted by 3x Filing Score.",
            styles["SmallNote"]))
        story.append(Spacer(1, 4))
        _build_tier_table(story, styles, t2, "tier_2")

    # Tier 3
    if t3:
        if t1 or t2:
            story.append(PageBreak())
        story.append(Paragraph(
            f"Tier 3: Monitor ({len(t3)} stocks)", styles["SectionHead"]))
        story.append(Paragraph(
            "Passes threshold filters. Monitor for demand growth. Sorted by 3x Filing Score.",
            styles["SmallNote"]))
        story.append(Spacer(1, 4))
        _build_tier_table(story, styles, t3, "tier_3")


def _build_tier_table(story, styles, candidates, tier_key):
    """Build a single tier table, paginated at 30 rows."""
    header = ["#", "Stock", "Sector", "3x Score", "Mkt Cap", "2x Cnt", "2x AUM", "REX 2x", "Vol Risk"]
    col_widths = [25, 62, 82, 55, 68, 42, 68, 55, 61]

    for page_start in range(0, len(candidates), 30):
        chunk = candidates[page_start:page_start + 30]
        data = [header]
        for i, c in enumerate(chunk):
            row_num = page_start + i + 1
            data.append([
                str(row_num),
                c["ticker"],
                Paragraph(c["sector"], styles["CellWrap"]),
                f"{c['score']:.0f}",
                _fmt_money(c["mkt_cap"]),
                str(c.get("count_2x", 0)),
                _fmt_money(c["aum_2x"]),
                c.get("rex_2x", "No"),
                c["risk"],
            ])

        t = Table(data, colWidths=col_widths)
        ts = _table_style()
        tier_color = TIER_COLORS.get(tier_key, BLUE)
        ts.add("BACKGROUND", (0, 0), (-1, 0), tier_color)

        for i, c in enumerate(chunk):
            # Risk color
            rc = RISK_COLORS.get(c["risk"], GREEN)
            ts.add("TEXTCOLOR", (8, i + 1), (8, i + 1), rc)
            ts.add("FONTNAME", (8, i + 1), (8, i + 1), "Helvetica-Bold")
            # REX 2x color
            r2c = REX_2X_COLORS.get(c.get("rex_2x", "No"), colors.grey)
            ts.add("TEXTCOLOR", (7, i + 1), (7, i + 1), r2c)
            ts.add("FONTNAME", (7, i + 1), (7, i + 1), "Helvetica-Bold")

        t.setStyle(ts)
        story.append(t)

        if page_start + 30 < len(candidates):
            story.append(PageBreak())
            story.append(Paragraph("(continued)", styles["SubHead"]))
            story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))


# ===========================================================================
# Section 5: 4x Filing Candidates (NEW)
# ===========================================================================

def _build_4x_candidates(story, styles, four_x_candidates):
    story.append(Paragraph("4x Filing Candidates", styles["ReportTitle"]))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "4x leverage amplifies daily stock moves by 4. Candidates are stocks with existing 2x "
        "products and daily volatility under 20%. Risk level is shown for reference.",
        styles["ReportBody"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        f"Table 5: 4x Filing Candidates ({len(four_x_candidates)} stocks)", styles["SectionHead"]))

    if not four_x_candidates:
        story.append(Paragraph(
            "No stocks meet 4x criteria.",
            styles["ReportBody"]))
        return

    _build_paginated_table(
        story, styles, four_x_candidates,
        header=["#", "Stock", "Sector", "2x AUM", "Vol 30D", "Daily Vol", "2x Cnt", "REX 2x", "Risk"],
        col_widths=[25, 62, 88, 68, 60, 60, 42, 55, 58],
        row_fn=lambda i, c: [
            str(i + 1),
            c["ticker"],
            c["sector"],
            _fmt_money(c["aum_2x"]),
            _fmt_pct(c["vol_30d"]),
            _fmt_pct(c["daily_vol"]),
            str(c["count_2x"]),
            c.get("rex_2x", "No"),
            c.get("risk", "LOW"),
        ],
        rows_per_page=30,
        color_fn=_color_4x_row,
    )

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Sorted by 2x AUM descending. A 4x fund on a stock with 2% daily vol would see ~8% "
        "daily fund swings - comparable to a 3x fund on a 2.7% daily vol stock.",
        styles["SmallNote"]))


# ===========================================================================
# Section 6: Volatility & Blow-Up Risk (Scoped)
# ===========================================================================

def _build_risk_watchlist(story, styles, risk_data):
    story.append(Paragraph("Volatility &amp; Blow-Up Risk Watch", styles["ReportTitle"]))
    story.append(Spacer(1, 4))

    story.append(Paragraph(
        "A 3x fund amplifies daily stock moves by 3. A stock declining <b>-10%</b> in a day means "
        "the 3x fund loses <b>-30%</b>. A <b>-33.3%</b> stock decline takes a 3x bull fund's NAV to "
        "<b>zero</b>. This watchlist covers recommended stocks and top AUM underliers only.",
        styles["ReportBody"]))
    story.append(Spacer(1, 8))

    # Risk level legend
    story.append(Paragraph(
        "Risk Levels: "
        "<font color='#27ae60'><b>LOW</b></font> (&lt;3% daily vol) | "
        "<font color='#e67e22'><b>MEDIUM</b></font> (3-5%) | "
        "<font color='#e74c3c'><b>HIGH</b></font> (5-8%) | "
        "<font color='#8e44ad'><b>EXTREME</b></font> (&gt;8%)",
        styles["ReportBody"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        f"Table 6: Risk Watchlist ({len(risk_data)} stocks)", styles["SectionHead"]))

    if not risk_data:
        story.append(Paragraph("No stocks with MEDIUM or higher risk in recommendations.", styles["ReportBody"]))
        return

    _build_paginated_table(
        story, styles, risk_data,
        header=["#", "Stock", "2x AUM", "Vol 30D", "Daily Vol", "Extreme Day Odds", "Risk", "REX 2x"],
        col_widths=[25, 62, 72, 62, 62, 95, 62, 78],
        row_fn=lambda i, r: [
            str(i + 1),
            r["ticker_clean"],
            _fmt_money(r.get("aum_2x", 0)),
            _fmt_pct(r["vol_30d"]),
            _fmt_pct(r["daily_vol"]),
            r.get("extreme_day_odds", "-"),
            r["risk_level"],
            r.get("rex_2x", "No"),
        ],
        rows_per_page=30,
        color_fn=_color_risk_row,
    )

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "'Extreme Day Odds' estimates frequency of +-10% daily stock moves using 30-day annualized "
        "volatility. Real markets have fat tails - actual probability is higher than shown. "
        "Sorted by 2x AUM so executives see largest positions first.",
        styles["SmallNote"]))


# ===========================================================================
# Section 7: Methodology
# ===========================================================================

def _build_methodology(story, styles, report_date):
    story.append(Paragraph("Methodology", styles["ReportTitle"]))
    story.append(Spacer(1, 8))
    cw = styles["CellWrap"]

    # 3x Filing Score
    story.append(Paragraph("3x Filing Score (Primary Ranking Metric)", styles["SubHead"]))
    story.append(Paragraph(
        "The 3x Filing Score blends stock fundamentals with market-proven demand:",
        styles["ReportBody"]))
    from screener.config import FILING_SCORE_WEIGHTS
    score_data = [
        ["Component", "Weight", Paragraph("Description", cw)],
        ["Stock Composite", f"{FILING_SCORE_WEIGHTS['composite_pctl']:.0%}",
         Paragraph("Percentile rank of composite score (OI, turnover, mkt cap, vol, SI)", cw)],
        ["2x AUM Demand", f"{FILING_SCORE_WEIGHTS['aum_2x_pctl']:.0%}",
         Paragraph("Percentile rank of total 2x product AUM on the underlier", cw)],
    ]
    t = Table(score_data, colWidths=[110, 55, 353])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 10))

    # Composite Scoring Weights
    story.append(Paragraph("Stock Composite Scoring Weights", styles["SubHead"]))
    from screener.config import SCORING_WEIGHTS
    rationale = {
        "Turnover / Traded Value": "Strongest predictor of leveraged product AUM (r=0.74)",
        "Total OI": "Direct options demand signal (r=0.65)",
        "Mkt Cap": "Market viability, swap/derivative support (r=0.61)",
        "Volatility 30D": "Retail traders want vol, drives leveraged demand",
        "Short Interest Ratio": "Contrarian interest signal, inverted (r=-0.50)",
    }
    weights_data = [["Factor", "Weight", Paragraph("Rationale", cw)]]
    for factor, weight in SCORING_WEIGHTS.items():
        weights_data.append([
            factor, f"{weight:.0%}",
            Paragraph(rationale.get(factor, ""), cw),
        ])
    t = Table(weights_data, colWidths=[150, 50, 318])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 10))

    # Tiering criteria
    story.append(Paragraph("Tiering Criteria", styles["SubHead"]))
    from screener.config import TIER_CUTOFFS
    tier_data = [
        ["Tier", Paragraph("Criteria", cw), "Target"],
        [Paragraph("<font color='#27ae60'><b>Tier 1: File Now</b></font>", cw),
         Paragraph("Proven 2x AUM > 0, sorted by 3x Filing Score. "
                   "Risk shown for reference (not used as filter).", cw),
         str(TIER_CUTOFFS['tier_1_count'])],
        [Paragraph("<font color='#0984e3'><b>Tier 2: File Soon</b></font>", cw),
         Paragraph("Next best stocks by 3x Filing Score. "
                   "May lack 2x product history.", cw),
         str(TIER_CUTOFFS['tier_2_count'])],
        [Paragraph("<font color='#e67e22'><b>Tier 3: Monitor</b></font>", cw),
         Paragraph("Passes threshold filters, sorted by 3x Filing Score. "
                   "Monitor for demand growth.", cw),
         str(TIER_CUTOFFS['tier_3_count'])],
    ]
    t = Table(tier_data, colWidths=[108, 358, 52])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 10))

    # 4x Candidate Criteria
    story.append(Paragraph("4x Candidate Criteria", styles["SubHead"]))
    from screener.config import FOUR_X_CRITERIA, RISK_THRESHOLDS
    story.append(Paragraph(
        "<bullet>&bull;</bullet> Must have existing 2x product(s) (proven leveraged demand)",
        styles["BulletBody"]))
    story.append(Paragraph(
        f"<bullet>&bull;</bullet> Maximum daily vol: <b>{FOUR_X_CRITERIA['max_daily_vol']:.0f}%</b>",
        styles["BulletBody"]))
    story.append(Paragraph(
        "<bullet>&bull;</bullet> 4x leverage amplifies daily moves by 4. Risk level shown for reference.",
        styles["BulletBody"]))
    story.append(Spacer(1, 10))

    # Risk methodology
    story.append(Paragraph("Volatility Risk Methodology", styles["SubHead"]))
    risk_data = [
        ["Risk Level", Paragraph("Implied Daily Vol", cw), Paragraph("3x Fund Impact", cw)],
        ["LOW", Paragraph(f"< {RISK_THRESHOLDS['low_max_daily_vol']}%", cw),
         Paragraph(f"< {RISK_THRESHOLDS['low_max_daily_vol'] * 3}% daily fund swings", cw)],
        ["MEDIUM", Paragraph(f"{RISK_THRESHOLDS['low_max_daily_vol']}-{RISK_THRESHOLDS['medium_max_daily_vol']}%", cw),
         Paragraph(f"{RISK_THRESHOLDS['low_max_daily_vol'] * 3}-{RISK_THRESHOLDS['medium_max_daily_vol'] * 3}% daily fund swings", cw)],
        ["HIGH", Paragraph(f"{RISK_THRESHOLDS['medium_max_daily_vol']}-{RISK_THRESHOLDS['high_max_daily_vol']}%", cw),
         Paragraph(f"{RISK_THRESHOLDS['medium_max_daily_vol'] * 3}-{RISK_THRESHOLDS['high_max_daily_vol'] * 3}% daily fund swings", cw)],
        ["EXTREME", Paragraph(f"> {RISK_THRESHOLDS['high_max_daily_vol']}%", cw),
         Paragraph(f"> {RISK_THRESHOLDS['high_max_daily_vol'] * 3}% daily fund swings. NAV zero risk.", cw)],
    ]
    t = Table(risk_data, colWidths=[88, 165, 265])
    ts = _table_style()
    ts.add("TEXTCOLOR", (0, 1), (0, 1), GREEN)
    ts.add("TEXTCOLOR", (0, 2), (0, 2), ORANGE)
    ts.add("TEXTCOLOR", (0, 3), (0, 3), RED)
    ts.add("TEXTCOLOR", (0, 4), (0, 4), PURPLE)
    ts.add("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold")
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 10))

    # Data sources
    story.append(Paragraph("Data Sources", styles["SubHead"]))
    story.append(Paragraph(
        "<bullet>&bull;</bullet> <b>Stock Data</b>: Bloomberg US equity universe (~6,400 stocks). "
        "Market cap, options OI, volatility, turnover, short interest, sector classification.",
        styles["BulletBody"]))
    story.append(Paragraph(
        "<bullet>&bull;</bullet> <b>ETP Data</b>: Bloomberg US ETP universe (~5,000 products). "
        "AUM (current + 36-month history), fund flows, leverage type, underlier mapping, issuer.",
        styles["BulletBody"]))
    story.append(Paragraph(
        "<bullet>&bull;</bullet> <b>Filing Status</b>: SEC EDGAR pipeline. "
        "485APOS/485BPOS filing tracking across 16 monitored trusts.",
        styles["BulletBody"]))
    story.append(Spacer(1, 16))

    # Disclaimer
    story.append(Paragraph("Disclaimer", styles["SubHead"]))
    story.append(Paragraph(
        "This analysis is for internal decision support only. Scoring and tiering are rules-based "
        "assessments, not guarantees of product success. Risk estimates use simplified models "
        "(normal distribution) that underestimate true tail risk. All data sourced from Bloomberg "
        "and SEC EDGAR.",
        styles["SmallNote"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"REX Financial | Generated {report_date}", styles["SmallNote"]))


# ===========================================================================
# Utilities
# ===========================================================================

def _build_paginated_table(story, styles, rows, header, col_widths, row_fn,
                           rows_per_page=25, color_fn=None):
    """Build a table with pagination and optional per-row color function."""
    for page_start in range(0, len(rows), rows_per_page):
        chunk = rows[page_start:page_start + rows_per_page]
        data = [header]
        for i, r in enumerate(chunk):
            data.append(row_fn(page_start + i, r))

        t = Table(data, colWidths=col_widths)
        ts = _table_style()

        if color_fn:
            for i, r in enumerate(chunk):
                color_fn(i, r, ts)

        t.setStyle(ts)
        story.append(t)

        if page_start + rows_per_page < len(rows):
            story.append(PageBreak())
            story.append(Paragraph("(continued)", styles["SubHead"]))
            story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))


def _color_rex_2x(ts, col_idx, row_idx, rex_2x_val):
    """Apply REX 2x color to a cell."""
    r2c = REX_2X_COLORS.get(rex_2x_val, colors.grey)
    ts.add("TEXTCOLOR", (col_idx, row_idx + 1), (col_idx, row_idx + 1), r2c)
    ts.add("FONTNAME", (col_idx, row_idx + 1), (col_idx, row_idx + 1), "Helvetica-Bold")


def _color_track_record_score(i, r, ts):
    """Color-code track record score."""
    if r.get("score") is not None:
        if r["score"] >= 70:
            ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), GREEN)
        elif r["score"] >= 50:
            ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), BLUE)
        else:
            ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), ORANGE)
        ts.add("FONTNAME", (6, i + 1), (6, i + 1), "Helvetica-Bold")


def _color_4x_row(i, c, ts):
    """Color-code 4x candidate row."""
    # REX 2x color (col 7)
    r2c = REX_2X_COLORS.get(c.get("rex_2x", "No"), colors.grey)
    ts.add("TEXTCOLOR", (7, i + 1), (7, i + 1), r2c)
    ts.add("FONTNAME", (7, i + 1), (7, i + 1), "Helvetica-Bold")
    # Risk color (col 8)
    rc = RISK_COLORS.get(c.get("risk", "LOW"), GREEN)
    ts.add("TEXTCOLOR", (8, i + 1), (8, i + 1), rc)
    ts.add("FONTNAME", (8, i + 1), (8, i + 1), "Helvetica-Bold")


def _color_risk_row(i, r, ts):
    """Color-code risk watchlist row."""
    rc = RISK_COLORS.get(r["risk_level"], GREEN)
    ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), rc)
    ts.add("FONTNAME", (6, i + 1), (6, i + 1), "Helvetica-Bold")
    if r["risk_level"] == "EXTREME":
        ts.add("BACKGROUND", (0, i + 1), (-1, i + 1), LIGHT_RED)
    elif r["risk_level"] == "HIGH":
        ts.add("BACKGROUND", (0, i + 1), (-1, i + 1), LIGHT_ORANGE)
    # REX 2x color
    r2c = REX_2X_COLORS.get(r.get("rex_2x", "No"), colors.grey)
    ts.add("TEXTCOLOR", (7, i + 1), (7, i + 1), r2c)
    ts.add("FONTNAME", (7, i + 1), (7, i + 1), "Helvetica-Bold")
