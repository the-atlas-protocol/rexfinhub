"""T-REX Filing Candidates — the week's top picks.

One question: which stocks should REX file on this week?

Not a ranked list of 100 names. Not a methodology doc. Not pass/consider/recommend
buckets. Just the top picks with clear action and reasoning.

V1: HTML email. 680px fixed width. Executive-first.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime

log = logging.getLogger(__name__)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"

_MAX_WIDTH = "680px"
_NAVY = "#0f172a"
_RED = "#dc2626"
_GREEN = "#059669"
_AMBER = "#d97706"
_BLUE = "#2563eb"
_GRAY = "#64748b"
_LIGHT = "#f8fafc"
_BORDER = "#e5e7eb"
_WHITE = "#ffffff"


def build_filing_screener_report(max_picks: int = 5) -> str:
    """Build the weekly filing candidates HTML.

    Args:
        max_picks: Number of top picks to show (default 5). NOT a top-N list —
                   only RECOMMEND-tier candidates pass the bar.
    """
    from screener.foundation_scorer import score_full_universe

    try:
        # Score the universe — top 100 passing floor — then filter to RECOMMEND only
        all_scored = score_full_universe(top_n=100)
    except FileNotFoundError:
        return _render_error("Bloomberg data file not found. Run Bloomberg sync first.")
    except Exception as e:
        log.error("Screener report generation failed: %s", e)
        return _render_error(f"Error loading screener data: {e}")

    if not all_scored:
        return _render_empty_picks()

    # Only RECOMMEND candidates get into this report
    recommend = [c for c in all_scored if c.recommendation == "RECOMMEND" and c.floor_pass]

    # Top N by composite score
    picks = recommend[:max_picks]

    if not picks:
        return _render_empty_picks()

    return _render_picks(picks)


def _pick_action(c) -> tuple[str, str]:
    """Determine filing action label and one-line rationale for a candidate."""
    # No competition = file both (long + inverse) — white space
    if c.competition_count == 0:
        return ("File Long + Inverse", "White space — no existing leveraged product on this underlier")

    # REX already in the race
    if c.rex_position > 0:
        return ("File Long + Inverse", f"REX is already #{c.rex_position} on this underlier — extend product line")

    # Strong inflows in existing products = validated demand
    if c.competition_flow_ytd > 50:
        return ("File Long", f"${c.competition_flow_ytd:.0f}M YTD inflows validate demand — compete on the long side")

    # Few competitors, positive flows
    if c.competition_count <= 2 and c.competition_flow_ytd > 0:
        return ("File Long + Inverse", f"Only {c.competition_count} issuer(s) in the race and they're taking inflows")

    # Many competitors but flows negative = crowded and declining
    if c.competition_count >= 4 and c.competition_flow_ytd < 0:
        return ("Skip", f"{c.competition_count} issuers and ${abs(c.competition_flow_ytd):.0f}M YTD outflows — crowded and cooling")

    # Default: file long
    return ("File Long", f"{c.competition_count} competitor(s), demand rank {c.demand_rank:.0f}")


def _render_picks(picks: list) -> str:
    today = date.today()

    cards = []
    for i, c in enumerate(picks, 1):
        action, rationale = _pick_action(c)
        cards.append(_render_pick_card(c, i, action, rationale))

    body = f"""
{_render_header(today, len(picks))}
<div style="padding:20px 24px;">
  {''.join(cards)}
</div>
"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Filing Candidates — {today.strftime('%b %d')}</title></head>
<body style="margin:0; padding:20px; background:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:{_MAX_WIDTH}; margin:0 auto; background:{_WHITE}; border-radius:6px; border:1px solid {_BORDER}; overflow:hidden;">
{body}
<div style="padding:12px 20px; border-top:1px solid {_BORDER}; background:#f8fafc;">
  <div style="font-size:10px; color:{_GRAY}; text-align:center; font-style:italic;">
    Bloomberg AUM and fund-flow data is delivered on a 1 business day lag by design; figures reflect T-1 values and may be over- or under-stated for very recent launches, distributions, or corporate actions.
  </div>
</div>
</div></body></html>"""


def _render_header(today: date, pick_count: int) -> str:
    return f"""
<div style="padding:20px 24px 16px; border-bottom:1px solid {_BORDER};">
  <div style="font-size:11px; text-transform:uppercase; letter-spacing:0.08em; color:{_GRAY}; font-weight:600;">REX Financial</div>
  <div style="font-size:22px; font-weight:700; color:{_NAVY}; margin-top:4px;">This Week's Filing Candidates</div>
  <div style="font-size:13px; color:{_GRAY}; margin-top:2px;">{today.strftime('%B %d, %Y')}</div>
</div>"""


def _fmt_bignum(v: float, prefix: str = "") -> str:
    """Format a number with B/M suffix."""
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1e9:
        return f"{prefix}{v/1e9:.1f}B"
    if av >= 1e6:
        return f"{prefix}{v/1e6:.1f}M"
    if av >= 1e3:
        return f"{prefix}{v/1e3:.1f}K"
    return f"{prefix}{v:.0f}"


def _render_pick_card(c, idx: int, action: str, rationale: str) -> str:
    ticker = c.ticker.replace(" US", "")
    name = (c.company_name or ticker)[:45]
    sector = c.sector or ""

    # Action color
    action_color = _GREEN if "File" in action else _GRAY
    action_bg = "#ecfdf5" if "File" in action else "#f3f4f6"

    # Score (capped 0-100 in scorer)
    score = int(round(c.composite_score))
    score_color = _GREEN if score >= 80 else _AMBER if score >= 60 else _GRAY

    # Key numbers: turnover, options OI, avg volume
    turnover = _fmt_bignum(c.floor_turnover, "$")
    oi = _fmt_bignum(c.floor_oi)
    vol = _fmt_bignum(c.floor_volume)

    # Competition section
    if c.competition_count == 0:
        comp_html = f'<div style="font-size:11px; color:{_GREEN}; font-weight:600;">White space — no existing leveraged products</div>'
    else:
        aum_str = _fmt_bignum(c.competition_aum, "$")
        flow_color = _GREEN if c.competition_flow_ytd >= 0 else _RED
        flow_str = _fmt_bignum(abs(c.competition_flow_ytd), "$")
        flow_sign = "+" if c.competition_flow_ytd >= 0 else "-"
        rex_line = ""
        if c.rex_position > 0:
            rex_line = f' <span style="color:{_GREEN}; font-weight:700;">&middot; REX is in the race (#{c.rex_position})</span>'
        comp_html = f"""
<div style="font-size:11px; color:#374151;">
  <span style="color:{_GRAY};">Competition:</span> <b>{c.competition_count}</b> issuer{"s" if c.competition_count != 1 else ""}
  <span style="color:{_GRAY};"> &middot; AUM</span> <b>{aum_str}</b>
  <span style="color:{_GRAY};"> &middot; YTD flows</span> <b style="color:{flow_color};">{flow_sign}{flow_str}</b>{rex_line}
</div>"""

    return f"""
<div style="border:1px solid {_BORDER}; border-left:3px solid {score_color}; border-radius:4px; padding:14px 16px; margin-bottom:12px;">
  <div style="display:flex; justify-content:space-between; align-items:flex-start;">
    <div>
      <div style="font-size:11px; color:{_GRAY};">#{idx}</div>
      <div style="font-size:18px; font-weight:800; color:{_NAVY}; font-family:monospace; line-height:1.1;">{ticker}</div>
      <div style="font-size:12px; color:#374151; margin-top:2px;">{name}</div>
      {f'<div style="font-size:11px; color:{_GRAY};">{sector}</div>' if sector else ''}
    </div>
    <div style="text-align:right;">
      <div style="font-size:24px; font-weight:800; color:{score_color}; line-height:1;">{score}</div>
      <div style="font-size:10px; color:{_GRAY}; text-transform:uppercase; letter-spacing:0.04em;">Score</div>
    </div>
  </div>

  <div style="margin-top:12px; padding:8px 12px; background:{action_bg}; border-radius:3px;">
    <div style="font-size:11px; font-weight:700; color:{action_color}; text-transform:uppercase; letter-spacing:0.05em;">{action}</div>
    <div style="font-size:12px; color:#374151; margin-top:2px; line-height:1.4;">{rationale}</div>
  </div>

  <table style="width:100%; border-collapse:collapse; margin-top:10px;">
    <tr>
      <td style="padding:4px 0; font-size:11px; color:{_GRAY};">Turnover</td>
      <td style="padding:4px 0; font-size:11px; color:{_GRAY};">Options OI</td>
      <td style="padding:4px 0; font-size:11px; color:{_GRAY};">Avg Vol 30D</td>
    </tr>
    <tr>
      <td style="padding:0; font-size:13px; font-weight:700; color:{_NAVY};">{turnover}</td>
      <td style="padding:0; font-size:13px; font-weight:700; color:{_NAVY};">{oi}</td>
      <td style="padding:0; font-size:13px; font-weight:700; color:{_NAVY};">{vol}</td>
    </tr>
  </table>

  <div style="margin-top:10px; padding-top:10px; border-top:1px solid {_BORDER};">
    {comp_html}
  </div>
</div>"""


def _render_empty_picks() -> str:
    today = date.today()
    return f"""<!DOCTYPE html>
<html><body style="margin:0; padding:20px; background:#f1f5f9; font-family:sans-serif;">
<div style="max-width:{_MAX_WIDTH}; margin:0 auto; background:{_WHITE}; border-radius:6px; border:1px solid {_BORDER}; padding:24px;">
<div style="font-size:22px; font-weight:700; color:{_NAVY};">This Week's Filing Candidates</div>
<div style="font-size:13px; color:{_GRAY}; margin-top:4px;">{today.strftime('%B %d, %Y')}</div>
<div style="margin-top:20px; padding:14px; background:{_LIGHT}; border-radius:4px; font-size:13px; color:#374151;">
No candidates meet this week's criteria. Check back after the next Bloomberg sync.
</div>
</div></body></html>"""


def _render_error(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif; padding:40px; background:{_LIGHT};">
<div style="max-width:{_MAX_WIDTH}; margin:0 auto; background:white; padding:24px; border-radius:6px; border-left:3px solid {_RED};">
<h2 style="margin:0 0 8px; color:{_NAVY}; font-size:16px;">Screener Unavailable</h2>
<p style="color:#374151; font-size:13px;">{message}</p>
</div></body></html>"""
