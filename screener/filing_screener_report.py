"""Filing Candidate Screener Report — executive-ready HTML/email.

Shows top filing candidates from foundation_scorer ranked by composite score.
Executive format: priority queue summary + per-ticker cards.

V1: HTML email. PDF generation via ReportLab can be added in V2 if needed.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"

# Shared color palette
_NAVY = "#1a1a2e"
_GREEN = "#059669"
_RED = "#dc2626"
_ORANGE = "#d97706"
_BLUE = "#2563eb"
_GRAY = "#64748b"
_LIGHT = "#f8fafc"
_BORDER = "#e5e7eb"
_WHITE = "#ffffff"


def build_filing_screener_report(top_n: int = 30, min_score: float = 50.0) -> str:
    """Build the filing candidate screener HTML email.

    Args:
        top_n: Number of top candidates to show in the summary table
        min_score: Minimum composite score to include in detailed cards

    Returns:
        Complete HTML email string
    """
    from screener.foundation_scorer import score_full_universe

    try:
        # Score the full universe, return top 100 passing the floor
        all_candidates = score_full_universe(top_n=100)
    except FileNotFoundError:
        return _render_error("Bloomberg data file not found. Run Bloomberg sync first.")
    except Exception as e:
        log.error("Screener report generation failed: %s", e)
        return _render_error(f"Error loading screener data: {e}")

    if not all_candidates:
        return _render_error("No candidates scored. Check foundation_scorer output.")

    # Bucket by recommendation
    recommends = [c for c in all_candidates if c.recommendation == "RECOMMEND"]
    considers = [c for c in all_candidates if c.recommendation == "CONSIDER"]
    passes = [c for c in all_candidates if c.recommendation == "PASS"]

    # Top N by composite score for summary table
    top_candidates = all_candidates[:top_n]

    # Detailed cards: only top 15 with score >= min_score
    card_candidates = [c for c in all_candidates[:15] if c.composite_score >= min_score]

    html = _render_report(
        top_candidates=top_candidates,
        card_candidates=card_candidates,
        recommend_count=len(recommends),
        consider_count=len(considers),
        pass_count=len(passes),
        total_scored=len(all_candidates),
    )
    return html


def _render_report(*, top_candidates, card_candidates, recommend_count,
                   consider_count, pass_count, total_scored) -> str:
    """Render the full screener report HTML."""
    today = date.today()

    sections = []

    # --- Cover / Header ---
    sections.append(f"""
    <div style="background:{_NAVY}; color:{_WHITE}; padding:24px 28px; border-radius:8px 8px 0 0;">
      <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.12em; opacity:0.7;">REX Financial — Filing Intelligence</div>
      <div style="font-size:24px; font-weight:800; margin:6px 0;">T-REX Filing Candidate Screener</div>
      <div style="font-size:13px; opacity:0.8;">{today.strftime('%B %d, %Y')} | Foundation Scorer v2</div>
    </div>
    """)

    # --- Verdict Summary KPIs ---
    sections.append(f"""
    <div style="padding:20px 28px 8px; display:flex; gap:12px; flex-wrap:wrap;">
      <div style="flex:1; min-width:140px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_NAVY}; border-radius:6px; padding:12px 16px;">
        <div style="font-size:28px; font-weight:800; color:{_NAVY};">{total_scored}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em;">Total Scored</div>
      </div>
      <div style="flex:1; min-width:140px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_GREEN}; border-radius:6px; padding:12px 16px;">
        <div style="font-size:28px; font-weight:800; color:{_GREEN};">{recommend_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em;">Recommend</div>
      </div>
      <div style="flex:1; min-width:140px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_ORANGE}; border-radius:6px; padding:12px 16px;">
        <div style="font-size:28px; font-weight:800; color:{_ORANGE};">{consider_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em;">Consider</div>
      </div>
      <div style="flex:1; min-width:140px; background:{_LIGHT}; border:1px solid {_BORDER}; border-left:3px solid {_GRAY}; border-radius:6px; padding:12px 16px;">
        <div style="font-size:28px; font-weight:800; color:{_GRAY};">{pass_count}</div>
        <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em;">Pass</div>
      </div>
    </div>
    """)

    # --- Methodology note ---
    sections.append(f"""
    <div style="padding:4px 28px 16px;">
      <div style="font-size:11px; color:{_GRAY}; font-style:italic;">
        Scoring: Turnover percentile (demand rank) + additive adjustments for competition, volume surge, and short interest.
        Floor thresholds eliminate illiquid stocks. Composite score = demand rank ± context adjustments (range: 0–110).
      </div>
    </div>
    """)

    # --- Priority Queue Table (top 30) ---
    sections.append(f"""
    <div style="padding:16px 28px 8px;">
      <div style="font-size:15px; font-weight:700; color:{_NAVY}; margin-bottom:12px;">PRIORITY QUEUE (Top {len(top_candidates)})</div>
      <table style="width:100%; border-collapse:collapse; font-size:12px;">
        <thead>
          <tr style="background:{_LIGHT};">
            <th style="padding:8px 10px; text-align:left; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">#</th>
            <th style="padding:8px 10px; text-align:left; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Ticker</th>
            <th style="padding:8px 10px; text-align:left; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Company</th>
            <th style="padding:8px 10px; text-align:left; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Sector</th>
            <th style="padding:8px 10px; text-align:right; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Score</th>
            <th style="padding:8px 10px; text-align:center; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Competition</th>
            <th style="padding:8px 10px; text-align:center; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">REX</th>
            <th style="padding:8px 10px; text-align:center; font-size:10px; text-transform:uppercase; color:{_GRAY}; border-bottom:2px solid {_BORDER};">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {_render_queue_rows(top_candidates)}
        </tbody>
      </table>
    </div>
    """)

    # --- Detailed Cards (top 15 RECOMMEND/CONSIDER) ---
    if card_candidates:
        sections.append(f"""
        <div style="padding:24px 28px 8px;">
          <div style="font-size:15px; font-weight:700; color:{_NAVY}; margin-bottom:12px;">DETAILED ANALYSIS ({len(card_candidates)} candidates)</div>
        </div>
        """)
        for i, c in enumerate(card_candidates, 1):
            sections.append(_render_card(c, i))

    # --- Footer ---
    sections.append(f"""
    <div style="background:{_LIGHT}; padding:16px 28px; border-radius:0 0 8px 8px; border-top:1px solid {_BORDER};">
      <div style="font-size:11px; color:{_GRAY};">
        <a href="{DASHBOARD_URL}/filings/evaluator" style="color:{_BLUE};">Live Evaluator</a> |
        <a href="{DASHBOARD_URL}/filings/" style="color:{_BLUE};">Filing Explorer</a> |
        <a href="{DASHBOARD_URL}/dashboard" style="color:{_BLUE};">Dashboard</a>
      </div>
      <div style="font-size:10px; color:#94a3b8; margin-top:6px;">
        Data sourced from Bloomberg + SEC EDGAR. Generated {datetime.now().strftime('%Y-%m-%d %H:%M ET')}.
        Prepared for Scott Acheychek | Restricted distribution.
      </div>
    </div>
    """)

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>T-REX Filing Candidate Screener</title></head>
<body style="margin:0; padding:20px; background:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:820px; margin:0 auto; background:{_WHITE}; border-radius:8px; border:1px solid {_BORDER}; overflow:hidden;">
{body}
</div></body></html>"""


def _render_queue_rows(candidates) -> str:
    """Render the priority queue table rows."""
    rows = []
    for i, c in enumerate(candidates, 1):
        ticker = c.ticker.replace(" US", "")
        verdict_color = _GREEN if c.recommendation == "RECOMMEND" else _ORANGE if c.recommendation == "CONSIDER" else _GRAY
        verdict_bg = "#ecfdf5" if c.recommendation == "RECOMMEND" else "#fffbeb" if c.recommendation == "CONSIDER" else "#f3f4f6"

        comp_display = f"{c.competition_count} prod" if c.competition_count > 0 else "WHITE"
        comp_color = _NAVY if c.competition_count > 0 else _GREEN

        rex_status = ""
        if c.rex_position > 0:
            rex_status = f'<span style="color:{_GREEN}; font-weight:600;">YES</span>'
        elif c.competition_count > 0:
            rex_status = f'<span style="color:{_RED};">GAP</span>'
        else:
            rex_status = f'<span style="color:{_GRAY};">—</span>'

        score = c.composite_score
        score_color = _GREEN if score >= 80 else _ORANGE if score >= 50 else _GRAY

        rows.append(f"""
        <tr>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-weight:600; color:{_GRAY};">{i}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-weight:700; font-family:monospace;">{ticker}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER};">{(c.company_name or ticker)[:30]}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; font-size:11px; color:{_GRAY};">{(c.sector or '')[:20]}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; text-align:right; font-weight:700; color:{score_color};">{score:.0f}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; text-align:center; font-size:11px; color:{comp_color};">{comp_display}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; text-align:center; font-size:11px;">{rex_status}</td>
          <td style="padding:6px 10px; border-bottom:1px solid {_BORDER}; text-align:center;"><span style="background:{verdict_bg}; color:{verdict_color}; padding:2px 8px; border-radius:3px; font-size:10px; font-weight:700; text-transform:uppercase;">{c.recommendation}</span></td>
        </tr>""")
    return "\n".join(rows)


def _render_card(c, idx: int) -> str:
    """Render a detailed per-ticker card."""
    ticker = c.ticker.replace(" US", "")
    rec_color = _GREEN if c.recommendation == "RECOMMEND" else _ORANGE if c.recommendation == "CONSIDER" else _GRAY
    rec_bg = "#ecfdf5" if c.recommendation == "RECOMMEND" else "#fffbeb" if c.recommendation == "CONSIDER" else "#f3f4f6"
    card_border = _GREEN if c.recommendation == "RECOMMEND" else _ORANGE if c.recommendation == "CONSIDER" else _GRAY

    # Floor status
    floor_badge = (
        f'<span style="color:{_GREEN}; font-weight:700;">PASS</span>' if c.floor_pass
        else f'<span style="color:{_RED}; font-weight:700;">FAIL</span>'
    )

    # Adjustment pills
    def _pill(label, val):
        if val == 0:
            return f'<span style="display:inline-block; padding:2px 8px; border-radius:3px; font-size:11px; font-weight:600; margin:2px 4px 2px 0; background:#f3f4f6; color:{_GRAY};">{label}: 0</span>'
        color_fg = _GREEN if val > 0 else _RED
        color_bg = "#ecfdf5" if val > 0 else "#fef2f2"
        sign = "+" if val > 0 else ""
        return f'<span style="display:inline-block; padding:2px 8px; border-radius:3px; font-size:11px; font-weight:600; margin:2px 4px 2px 0; background:{color_bg}; color:{color_fg};">{label}: {sign}{val}</span>'

    adj_pills = _pill("Comp", c.adj_competition) + _pill("Surge", c.adj_volume_surge) + _pill("Short", c.adj_short_interest)

    # Competition display
    if c.competition_count == 0:
        comp_html = f'<div style="font-size:11px; color:{_GRAY}; font-style:italic;">White space — no existing L&I single-stock products</div>'
    else:
        comp_html = f"""
        <div style="font-size:11px;">
          <div><span style="color:{_GRAY};">Products:</span> <b>{c.competition_count}</b></div>
          <div><span style="color:{_GRAY};">Total AUM:</span> <b>${c.competition_aum:,.0f}M</b></div>
          <div><span style="color:{_GRAY};">YTD Flows:</span> <b style="color:{_GREEN if c.competition_flow_ytd >= 0 else _RED};">${c.competition_flow_ytd:+,.1f}M</b></div>
          {f'<div><span style="color:{_GRAY};">REX position:</span> <b style="color:{_GREEN};">#{c.rex_position} entrant</b></div>' if c.rex_position > 0 else f'<div><span style="color:{_RED};">REX not in race</span></div>'}
          {f'<div><span style="color:{_GRAY};">Expense benchmark:</span> {c.expense_ratio_benchmark:.2f}%</div>' if c.expense_ratio_benchmark > 0 else ''}
        </div>
        """

    # Rank bar
    rank = c.demand_rank
    rank_color = _GREEN if rank >= 80 else _ORANGE if rank >= 50 else _RED

    return f"""
    <div style="margin:0 28px 16px; border:1px solid {_BORDER}; border-left:4px solid {card_border}; border-radius:6px; overflow:hidden;">
      <div style="background:{_LIGHT}; padding:12px 16px; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span style="font-size:11px; color:{_GRAY};">#{idx}</span>
          <span style="font-size:18px; font-weight:800; font-family:monospace; margin-left:6px;">{ticker}</span>
          <span style="font-size:13px; color:{_GRAY}; margin-left:10px;">{(c.company_name or ticker)[:35]}</span>
          <div style="font-size:11px; color:{_GRAY}; margin-top:2px;">{c.sector or ''}</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:26px; font-weight:800; color:{_NAVY}; line-height:1;">{c.composite_score:.0f}</div>
          <span style="display:inline-block; background:{rec_bg}; color:{rec_color}; padding:3px 10px; border-radius:3px; font-size:10px; font-weight:700; text-transform:uppercase; margin-top:4px;">{c.recommendation}</span>
        </div>
      </div>
      <div style="padding:14px 16px;">
        <div style="font-size:12px; color:#374151; font-style:italic; margin-bottom:12px;">{c.reasoning}</div>
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px;">
          <div style="background:{_WHITE}; border:1px solid {_BORDER}; border-radius:4px; padding:10px;">
            <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; margin-bottom:4px;">Demand Floor {floor_badge}</div>
            <div style="font-size:11px;"><span style="color:{_GRAY};">Turnover:</span> ${c.floor_turnover/1e9:.1f}B</div>
            <div style="font-size:11px;"><span style="color:{_GRAY};">Options OI:</span> {c.floor_oi/1e6:.1f}M</div>
            <div style="font-size:11px;"><span style="color:{_GRAY};">Avg Vol 30D:</span> {c.floor_volume/1e6:.1f}M</div>
          </div>
          <div style="background:{_WHITE}; border:1px solid {_BORDER}; border-radius:4px; padding:10px;">
            <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; margin-bottom:4px;">Demand Rank</div>
            <div style="font-size:22px; font-weight:800; color:{rank_color};">{rank:.0f}<span style="font-size:12px; color:{_GRAY}; font-weight:400;">p</span></div>
            <div style="height:6px; background:#e5e7eb; border-radius:3px; overflow:hidden; margin-top:4px;">
              <div style="height:100%; width:{rank}%; background:{rank_color};"></div>
            </div>
            <div style="font-size:10px; color:{_GRAY}; margin-top:4px;">Adjustments:</div>
            {adj_pills}
          </div>
          <div style="background:{_WHITE}; border:1px solid {_BORDER}; border-radius:4px; padding:10px;">
            <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; margin-bottom:4px;">Competition</div>
            {comp_html}
          </div>
        </div>
      </div>
    </div>
    """


def _render_error(message: str) -> str:
    """Render an error page when scorer fails."""
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif; padding:40px; background:{_LIGHT};">
<div style="max-width:600px; margin:0 auto; background:white; padding:24px; border-radius:8px; border-left:3px solid {_RED};">
<h2 style="margin:0 0 8px; color:{_NAVY};">Screener Report Unavailable</h2>
<p style="color:#374151;">{message}</p>
</div></body></html>"""
