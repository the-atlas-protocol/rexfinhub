"""PDF Report Generator using ReportLab.

Generates a combined report:
  Part 1: Candidate Evaluation - per-ticker filing decision analysis
  Part 2: Universe Rankings - two top-50 tables (all opportunities + filed-only)
  Part 3: Methodology (always last)
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

# Colors
NAVY = colors.HexColor("#1a1a2e")
BLUE = colors.HexColor("#0984e3")
GREEN = colors.HexColor("#27ae60")
ORANGE = colors.HexColor("#e67e22")
RED = colors.HexColor("#e74c3c")
LIGHT_BG = colors.HexColor("#f5f7fa")
LIGHT_GREEN = colors.HexColor("#e8f5e9")
LIGHT_ORANGE = colors.HexColor("#fff3e0")
LIGHT_RED = colors.HexColor("#ffebee")
BORDER = colors.HexColor("#cccccc")

VERDICT_COLORS = {"RECOMMEND": GREEN, "NEUTRAL": ORANGE, "CAUTION": RED}
VERDICT_BG = {"RECOMMEND": LIGHT_GREEN, "NEUTRAL": LIGHT_ORANGE, "CAUTION": LIGHT_RED}

# Tickers to exclude from rankings (known bad data)
EXCLUDE_TICKERS = {"WBHC US"}

# Sector abbreviations for narrow table columns
SECTOR_ABBREV = {
    "Information Technology": "Info Tech",
    "Communication Services": "Comm Svcs",
    "Consumer Discretionary": "Cons Disc",
    "Consumer Staples": "Cons Staples",
    "Health Care": "Health Care",
    "Financials": "Financials",
    "Industrials": "Industrials",
    "Materials": "Materials",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


def _clean_sector(val) -> str:
    """Return clean sector string, abbreviating long names and handling nan."""
    s = str(val) if val is not None else ""
    if not s or s == "nan" or s == "None":
        return "-"
    return SECTOR_ABBREV.get(s, s[:14])


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("ReportTitle", parent=styles["Title"],
        fontSize=22, textColor=NAVY, spaceAfter=6))
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


def _detail_table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaf6")),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])


def _fmt_money(val, suffix="M"):
    if val is None or val == 0:
        return "-"
    if abs(val) >= 1000:
        return f"${val / 1000:,.1f}B"
    return f"${val:,.0f}{suffix}"


def _fmt_pctl(val):
    if val is None:
        return "-"
    return f"{val:.0f}p"


# =============================================================================
# CANDIDATE EVALUATION REPORT
# =============================================================================

def generate_candidate_report(
    candidates: list[dict],
    rankings: list[dict] | None = None,
    rex_funds: list[dict] | None = None,
    data_date: str | None = None,
) -> bytes:
    """Generate combined PDF: candidate evaluation + universe rankings + methodology."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch)
    styles = _build_styles()
    story = []
    report_date = datetime.now().strftime("%B %d, %Y")

    # ===== PAGE 1: EXECUTIVE SUMMARY =====
    story.append(Paragraph("ETF Launch Candidate Evaluation", styles["ReportTitle"]))
    story.append(Paragraph(
        f"Report Date: {report_date} | Data as of: {data_date or report_date}",
        styles["SmallNote"]))
    story.append(Spacer(1, 12))

    recs = sum(1 for c in candidates if c["verdict"] == "RECOMMEND")
    neutrals = sum(1 for c in candidates if c["verdict"] == "NEUTRAL")
    cautions = sum(1 for c in candidates if c["verdict"] == "CAUTION")

    story.append(Paragraph(
        f"<b>{len(candidates)}</b> candidates evaluated: "
        f"<font color='#27ae60'><b>{recs} RECOMMEND</b></font> | "
        f"<font color='#e67e22'><b>{neutrals} NEUTRAL</b></font> | "
        f"<font color='#e74c3c'><b>{cautions} CAUTION</b></font>",
        styles["ReportBody"]))
    story.append(Spacer(1, 12))

    # Sort candidates by demand weighted percentile (strongest signal first)
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c["demand"].get("weighted_pctl", 0),
        reverse=True,
    )

    # Summary table - use Paragraph for wrappable recommendation column
    story.append(Paragraph("Evaluation Summary", styles["SectionHead"]))
    story.append(Paragraph(
        "Sorted by demand strength (highest first). Priority rank reflects launch order recommendation.",
        styles["SmallNote"]))
    story.append(Spacer(1, 4))
    header = ["#", "Ticker", "Verdict", "Demand", "Score", "Recommendation"]
    data = [header]
    for i, c in enumerate(sorted_candidates):
        wpctl = c["demand"].get("weighted_pctl", 0)
        data.append([
            str(i + 1),
            c["ticker_clean"],
            c["verdict"],
            c["demand"]["verdict"],
            f"{wpctl:.0f}p" if wpctl else "-",
            Paragraph(c["reason"][:80], styles["CellWrap"]),
        ])

    t = Table(data, colWidths=[18, 38, 58, 42, 32, 288])
    ts = _table_style()
    for i, c in enumerate(sorted_candidates):
        vcolor = VERDICT_COLORS.get(c["verdict"], ORANGE)
        ts.add("TEXTCOLOR", (2, i + 1), (2, i + 1), vcolor)
        ts.add("FONTNAME", (2, i + 1), (2, i + 1), "Helvetica-Bold")
    t.setStyle(ts)
    story.append(t)
    story.append(PageBreak())

    # ===== PAGES 2-N: CANDIDATE DETAIL CARDS =====
    for c in candidates:
        _build_candidate_card(story, c, styles)

    # ===== PART 2: UNIVERSE RANKINGS (APPENDED) =====
    if rankings:
        story.append(PageBreak())
        _build_rankings_section(story, styles, rankings, report_date)

    # ===== LAST: METHODOLOGY =====
    story.append(PageBreak())
    _build_methodology_page(story, styles, candidates, rankings, report_date)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()

    log.info("Combined PDF generated: %d bytes, %d candidates, %d rankings",
             len(pdf_bytes), len(candidates), len(rankings or []))
    return pdf_bytes


def _build_candidate_card(story: list, c: dict, styles) -> None:
    """Build a single candidate evaluation card using clean table layout."""
    ticker = c["ticker_clean"]
    verdict = c["verdict"]
    demand = c["demand"]
    comp = c["competition"]
    market = c["market_feedback"]
    filing = c["filing"]
    vcolor = VERDICT_COLORS.get(verdict, ORANGE)

    # --- Header bar ---
    header_data = [[ticker, verdict, Paragraph(c["reason"][:90], styles["CellWrap"])]]
    header_table = Table(header_data, colWidths=[55, 70, 351])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY),
        ("BACKGROUND", (2, 0), (2, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
        ("TEXTCOLOR", (2, 0), (2, 0), colors.white),
        ("FONTSIZE", (0, 0), (0, 0), 12),
        ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
        ("FONTSIZE", (2, 0), (2, 0), 7),
        ("FONTSIZE", (1, 0), (1, 0), 10),
        ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (1, 0), (1, 0), vcolor),
        ("TEXTCOLOR", (1, 0), (1, 0), colors.white),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)

    # --- FILING STATUS (most important - shown first) ---
    filing_rows = [["REX Filing Status", "Details"]]
    if filing["verdict"] == "NOT_FILED":
        filing_rows.append(["Status", "NOT FILED - No REX filing found"])
    elif filing["verdict"] == "ALREADY_TRADING":
        rex_t = filing.get("rex_ticker") or "?"
        filing_rows.append(["Status", f"EFFECTIVE - Trading as {rex_t}"])
        if filing.get("fund_name"):
            filing_rows.append(["Fund", str(filing["fund_name"])[:60]])
    else:
        status = filing.get("status", "?")
        fund_name = filing.get("fund_name", "")
        filing_rows.append(["Status", f"{status} (485APOS filed)"])
        if fund_name:
            filing_rows.append(["Fund Name", str(fund_name)[:60]])
        if filing.get("effective_date"):
            filing_rows.append(["Eff. Date", str(filing["effective_date"])])
        if filing.get("latest_form"):
            filing_rows.append(["Latest Form", str(filing["latest_form"])])

    filing_table = Table(filing_rows, colWidths=[100, 376])
    fts = _detail_table_style()
    if filing["verdict"] == "ALREADY_TRADING":
        fts.add("BACKGROUND", (1, 1), (1, 1), LIGHT_GREEN)
    elif filing["verdict"] == "NOT_FILED":
        pass
    else:
        fts.add("BACKGROUND", (1, 1), (1, 1), LIGHT_ORANGE)
    fts.add("FONTNAME", (1, 1), (1, 1), "Helvetica-Bold")
    filing_table.setStyle(fts)
    story.append(filing_table)

    # --- DEMAND SIGNAL ---
    demand_rows = [["Demand Signal", "Value", "Percentile"]]
    if demand["verdict"] == "DATA_UNAVAILABLE":
        demand_rows.append(["Status", "DATA UNAVAILABLE", "-"])
        demand_rows.append(["Note", demand.get("note", "Not in US equity universe"), ""])
    else:
        metrics = demand.get("metrics", {})
        mkt_cap = metrics.get("Mkt Cap", {})
        demand_rows.append(["Market Cap", _fmt_money(mkt_cap.get("value")), ""])
        oi = metrics.get("Total OI", {})
        demand_rows.append(["Total Options OI",
            f"{oi['value']:,.0f}" if oi.get("value") else "-",
            _fmt_pctl(oi.get("percentile"))])
        turnover = metrics.get("Turnover / Traded Value", {})
        tv_raw = turnover.get("value")
        tv_display = _fmt_money(tv_raw / 1_000_000) if tv_raw else "-"
        demand_rows.append(["Turnover", tv_display,
            _fmt_pctl(turnover.get("percentile"))])
        vol = metrics.get("Volatility 30D", {})
        demand_rows.append(["Volatility 30D",
            f"{vol['value']:.1f}%" if vol.get("value") else "-",
            _fmt_pctl(vol.get("percentile"))])
        si = metrics.get("Short Interest Ratio", {})
        demand_rows.append(["Short Interest Ratio",
            f"{si['value']:.2f}" if si.get("value") else "-",
            _fmt_pctl(si.get("percentile"))])
        demand_rows.append(["Weighted Score",
            f"{demand.get('weighted_pctl', 0):.0f}p", demand["verdict"]])

    demand_table = Table(demand_rows, colWidths=[160, 160, 156])
    dts = _detail_table_style()
    if demand["verdict"] != "DATA_UNAVAILABLE":
        last = len(demand_rows) - 1
        dts.add("FONTNAME", (0, last), (-1, last), "Helvetica-Bold")
        if demand["verdict"] == "HIGH":
            dts.add("TEXTCOLOR", (2, last), (2, last), GREEN)
        elif demand["verdict"] == "LOW":
            dts.add("TEXTCOLOR", (2, last), (2, last), RED)
    demand_table.setStyle(dts)
    story.append(demand_table)

    # --- COMPETITIVE LANDSCAPE ---
    comp_rows = [["Competitive Landscape", "Value"]]
    comp_rows.append(["Verdict", comp["verdict"].replace("_", " ")])
    comp_rows.append(["Total Products", str(comp.get("product_count", 0))])
    if comp["rex_count"]:
        comp_rows.append(["REX Products", f"{comp['rex_count']} ({_fmt_money(comp['rex_aum'])})"])
    if comp["competitor_count"]:
        comp_rows.append(["Competitor Products", f"{comp['competitor_count']} ({_fmt_money(comp['competitor_aum'])})"])
    elif comp["product_count"] == 0:
        comp_rows.append(["Competitors", "None - First mover opportunity"])
    if comp.get("leader"):
        rex_tag = " [REX]" if comp.get("leader_is_rex") else ""
        comp_rows.append(["Market Leader", f"{comp['leader']}{rex_tag} ({comp['leader_share']:.0%})"])

    comp_table = Table(comp_rows, colWidths=[160, 316])
    cts = _detail_table_style()
    if comp["verdict"] in ("FIRST_MOVER", "EARLY_STAGE"):
        cts.add("TEXTCOLOR", (1, 1), (1, 1), GREEN)
    elif comp["verdict"] == "CROWDED":
        cts.add("TEXTCOLOR", (1, 1), (1, 1), RED)
    cts.add("FONTNAME", (1, 1), (1, 1), "Helvetica-Bold")
    comp_table.setStyle(cts)
    story.append(comp_table)

    # --- MARKET FEEDBACK ---
    mkt_rows = [["Market Feedback", "Value"]]
    mkt_rows.append(["Verdict", market["verdict"].replace("_", " ")])
    if market["verdict"] == "NO_PRODUCTS":
        mkt_rows.append(["Note", "No existing leveraged products to assess"])
    else:
        mkt_rows.append(["Total AUM", _fmt_money(market.get("total_aum", 0))])
        if market.get("flow_direction"):
            mkt_rows.append(["Flow Direction", market["flow_direction"]])
        if market.get("aum_trend"):
            mkt_rows.append(["AUM Trend", market["aum_trend"]])
        for p in market.get("details", [])[:3]:
            rex_tag = " [REX]" if p.get("is_rex") else ""
            mkt_rows.append([f"  {p['ticker']}{rex_tag}", _fmt_money(p.get("aum", 0))])

    mkt_table = Table(mkt_rows, colWidths=[160, 316])
    mts = _detail_table_style()
    if market["verdict"] == "VALIDATED":
        mts.add("TEXTCOLOR", (1, 1), (1, 1), GREEN)
    elif market["verdict"] == "REJECTED":
        mts.add("TEXTCOLOR", (1, 1), (1, 1), RED)
    mts.add("FONTNAME", (1, 1), (1, 1), "Helvetica-Bold")
    mkt_table.setStyle(mts)
    story.append(mkt_table)

    if c.get("data_coverage") != "full":
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<i>Note: {ticker} not in US equity universe. "
            f"Demand Signal unavailable.</i>", styles["SmallNote"]))

    story.append(Spacer(1, 16))


# =============================================================================
# UNIVERSE RANKINGS SECTION
# =============================================================================

def _build_rankings_section(story, styles, results, report_date):
    """Build two top-50 tables: all opportunities + filed-only.

    Both exclude underliers where REX has already launched (EFFECTIVE) products.
    """
    # Get launched underliers to exclude
    from screener.filing_match import get_launched_underliers
    from screener.data_loader import load_etp_data
    try:
        etp_df = load_etp_data()
        launched = get_launched_underliers(etp_df)
    except Exception:
        launched = set()

    log.info("Launched underliers to exclude from rankings: %d", len(launched))

    # Filter out launched underliers and bad data
    clean_results = []
    for r in results:
        ticker = str(r.get("ticker", ""))
        ticker_clean = ticker.replace(" US", "").upper()
        if ticker in EXCLUDE_TICKERS:
            continue
        if ticker_clean in launched:
            continue
        clean_results.append(r)

    # Split: filed vs not filed
    filed_results = [r for r in clean_results
                     if str(r.get("filing_status", "")).startswith("REX Filed")]
    unfiled_results = clean_results  # All results (including filed) for the general table

    # ===== TABLE 1: TOP 50 OPPORTUNITIES (all, excluding launched) =====
    story.append(Paragraph("Universe Screening Results", styles["ReportTitle"]))
    story.append(Paragraph(
        "Top opportunities by composite score. Excludes underliers where REX has already launched products. "
        "REX Filed column shows filing pipeline status and effective date where available.",
        styles["SmallNote"]))
    story.append(Spacer(1, 8))

    total = len(results)
    filed_count = len(filed_results)
    story.append(Paragraph(
        f"<b>{total:,}</b> stocks screened | "
        f"<b>{len(launched)}</b> already launched (excluded) | "
        f"<b>{filed_count}</b> have REX filings",
        styles["ReportBody"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Top 50 - All Opportunities", styles["SectionHead"]))
    _build_ranking_table(story, styles, unfiled_results[:50])

    # ===== TABLE 2: TOP 50 FILED ONLY (excluding launched) =====
    if filed_results:
        story.append(PageBreak())
        story.append(Paragraph("REX Filed - Pending Launch", styles["SectionHead"]))
        story.append(Paragraph(
            "Only underliers where REX has filed (485APOS/485BPOS) but NOT yet launched. "
            "These are the launch candidates with active filings.",
            styles["SmallNote"]))
        story.append(Spacer(1, 6))
        _build_ranking_table(story, styles, filed_results[:50])


def _build_ranking_table(story, styles, rows):
    """Build a ranking table split into pages of 25."""
    for page_start in range(0, len(rows), 25):
        chunk = rows[page_start:page_start + 25]
        header = ["#", "Ticker", "Sector", "Score", "Mkt Cap", "OI %", "REX Filing"]
        data = [header]
        for i, r in enumerate(chunk):
            row_num = page_start + i + 1
            filing_status = str(r.get("filing_status", "Not Filed"))
            data.append([
                str(row_num),
                str(r.get("ticker", "")),
                _clean_sector(r.get("sector")),
                f"{r.get('composite_score', 0):.1f}",
                _fmt_money(r.get("mkt_cap")) if r.get("mkt_cap") else "-",
                f"{r.get('total_oi_pctl', 0):.0f}" if r.get("total_oi_pctl") else "-",
                Paragraph(filing_status[:35], styles["CellWrap"]),
            ])

        t = Table(data, colWidths=[22, 55, 75, 38, 55, 35, 196])
        ts = _table_style()
        for i, r in enumerate(chunk):
            filing_status = str(r.get("filing_status", ""))
            if filing_status.startswith("REX Filed"):
                ts.add("FONTNAME", (6, i + 1), (6, i + 1), "Helvetica-Bold")
                ts.add("TEXTCOLOR", (6, i + 1), (6, i + 1), BLUE)
                ts.add("BACKGROUND", (0, i + 1), (-1, i + 1), LIGHT_GREEN)
        t.setStyle(ts)
        story.append(t)

        if page_start + 25 < len(rows):
            story.append(PageBreak())
            story.append(Paragraph("(continued)", styles["SubHead"]))
            story.append(Spacer(1, 6))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Green rows = REX has filed for this underlier. "
        "REX Filing column shows pipeline status (Effective/Pending/Delayed) and effective date.",
        styles["SmallNote"]))


# =============================================================================
# METHODOLOGY (always last page)
# =============================================================================

def _build_methodology_page(story, styles, candidates, rankings, report_date):
    """Methodology page - always at the very end."""
    story.append(Paragraph("Methodology", styles["ReportTitle"]))

    # Evaluation pillars
    story.append(Paragraph("Candidate Evaluation Pillars", styles["SubHead"]))
    cw = styles["CellWrap"]
    method_data = [
        ["Pillar", "Source", "Verdicts"],
        [Paragraph("Demand Signal", cw),
         Paragraph("US equity universe. Turnover 30%, OI 30%, Mkt Cap 20%, Vol 10%, SI 10%.", cw),
         Paragraph("HIGH / MEDIUM / LOW", cw)],
        [Paragraph("Competition", cw),
         Paragraph("ETP universe. REX products separated from competitors.", cw),
         Paragraph("FIRST MOVER / EARLY / COMPETITIVE / CROWDED", cw)],
        [Paragraph("Market Feedback", cw),
         Paragraph("Existing product AUM and 3-month fund flows.", cw),
         Paragraph("VALIDATED / MIXED / REJECTED / NO PRODUCTS", cw)],
        [Paragraph("Filing Status", cw),
         Paragraph("SEC EDGAR pipeline DB. Matches by fund name pattern for PENDING filings.", cw),
         Paragraph("ALREADY TRADING / FILED / NOT FILED", cw)],
    ]
    t = Table(method_data, colWidths=[70, 260, 146])
    ts = _table_style()
    ts.add("FONTSIZE", (0, 1), (-1, -1), 7)
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Overall Verdict Logic", styles["SubHead"]))
    verdict_data = [
        ["Verdict", Paragraph("Criteria", cw)],
        ["RECOMMEND", Paragraph("Demand >= MEDIUM + (FIRST MOVER or EARLY STAGE) + not market REJECTED", cw)],
        ["CAUTION", Paragraph("CROWDED market, or market REJECTED, or LOW demand", cw)],
        ["NEUTRAL", Paragraph("Everything else (already trading, mixed signals, pending filings)", cw)],
    ]
    t = Table(verdict_data, colWidths=[80, 396])
    ts2 = _table_style()
    ts2.add("TEXTCOLOR", (0, 1), (0, 1), GREEN)
    ts2.add("TEXTCOLOR", (0, 2), (0, 2), RED)
    ts2.add("TEXTCOLOR", (0, 3), (0, 3), ORANGE)
    ts2.add("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold")
    t.setStyle(ts2)
    story.append(t)
    story.append(Spacer(1, 8))

    # Scoring weights
    if rankings:
        story.append(Paragraph("Universe Scoring Weights", styles["SubHead"]))
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
            weights_data.append([factor, f"{weight:.0%}", Paragraph(rationale.get(factor, ""), cw)])
        t = Table(weights_data, colWidths=[140, 50, 286])
        t.setStyle(_detail_table_style())
        story.append(t)
        story.append(Spacer(1, 8))

    # Data coverage warnings
    partial = [c for c in candidates if c["data_coverage"] != "full"]
    if partial:
        story.append(Paragraph("Data Coverage Warnings", styles["SubHead"]))
        for c in partial:
            story.append(Paragraph(
                f"<b>{c['ticker_clean']}</b>: Not in US equity universe. "
                f"Demand Signal unavailable.",
                styles["SmallNote"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph("Disclaimer", styles["SubHead"]))
    story.append(Paragraph(
        "This analysis is for internal decision support only. Verdicts are rules-based assessments, "
        "not guarantees. All data sourced from SEC EDGAR and licensed market data.",
        styles["SmallNote"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"REX Financial | Generated {report_date}", styles["SmallNote"]))


# =============================================================================
# STANDALONE RANKINGS REPORT (for --rankings mode)
# =============================================================================

def generate_rankings_report(
    results: list[dict],
    rex_funds: list[dict] | None = None,
    data_date: str | None = None,
) -> bytes:
    """Generate standalone universe rankings PDF report."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch)
    styles = _build_styles()
    story = []
    report_date = datetime.now().strftime("%B %d, %Y")

    _build_rankings_section(story, styles, results, report_date)

    # Methodology at end
    story.append(PageBreak())
    _build_methodology_page(story, styles, [], results, report_date)

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()

    log.info("Rankings PDF generated: %d bytes, %d results", len(pdf_bytes), len(results))
    return pdf_bytes
