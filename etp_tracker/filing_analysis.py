"""
Top Filings of the Day — LLM-powered analysis of new fund filings.

For a given date, queries new fund filings (485APOS, N-1A, N-2, S-1), selects
up to 3 most interesting via Haiku, writes structured analyses via Sonnet, and
caches results in the FilingAnalysis table so re-renders make zero LLM calls.
"""
from __future__ import annotations

import logging
import re
import traceback
from datetime import datetime

from sqlalchemy.orm import Session

from etp_tracker.sec_client import SECClient
from webapp.models import Filing, FilingAnalysis, FundExtraction, Trust
from webapp.services import claude_service

log = logging.getLogger(__name__)

NEW_FORM_TYPES = {"485APOS", "N-1A", "N-2", "S-1"}
SEC_USER_AGENT = "REX-ETP-FilingTracker/2.0 (relasmar@rexfin.com)"
MAX_PICKS = 3


def _query_candidates(db: Session, date_iso: str) -> list[dict]:
    rows = (
        db.query(Filing, Trust)
        .join(Trust, Filing.trust_id == Trust.id)
        .filter(Filing.filing_date == date_iso, Filing.form.in_(list(NEW_FORM_TYPES)))
        .all()
    )
    out = []
    for filing, trust in rows:
        funds = (
            db.query(FundExtraction)
            .filter(FundExtraction.filing_id == filing.id)
            .limit(20)
            .all()
        )
        seen = set()
        unique_names = []
        for f in funds:
            nm = f.series_name
            if nm and nm not in seen:
                seen.add(nm)
                unique_names.append(nm)
        out.append({
            "filing_id": filing.id,
            "accession": filing.accession_number,
            "form": filing.form,
            "primary_link": filing.primary_link or filing.submission_txt_link or "",
            "submission_txt_link": filing.submission_txt_link or "",
            "registrant": filing.registrant or "",
            "trust_name": trust.name,
            "is_rex": trust.is_rex,
            "fund_names": unique_names,
        })
    return out


def resolve_prospectus_url(sec_client: SECClient, url: str) -> tuple[str, str]:
    """If url points to an EDGAR -index.htm page, resolve it to the primary
    prospectus document URL. Returns (resolved_url, body_if_fetched_else_empty).
    """
    if not url.endswith("-index.htm") and not url.endswith("-index.html"):
        return url, ""
    try:
        idx = sec_client.fetch_text(url)
    except Exception:
        return url, ""
    if not idx:
        return url, ""

    acc_dir_m = re.search(r"/Archives/edgar/data/\d+/(\d+)/", url)
    if not acc_dir_m:
        return url, ""
    acc_dir = acc_dir_m.group(1)

    candidates = []
    for hm in re.finditer(r'href="([^"]+\.htm[l]?)"', idx):
        href = hm.group(1)
        if "-index.htm" in href:
            continue
        if acc_dir not in href:
            continue
        if href.startswith("/"):
            href = "https://www.sec.gov" + href
        elif not href.startswith("http"):
            continue
        candidates.append(href)

    if not candidates:
        return url, idx

    return candidates[0], ""


_STRIP_TAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_sections(raw: str) -> tuple[str, str]:
    """Pull investment objective + principal investment strategies from a
    raw prospectus body. Returns (objective, strategy)."""
    text = _STRIP_TAGS_RE.sub(" ", raw)
    text = _WS_RE.sub(" ", text)
    obj = ""
    m = re.search(r"investment\s+objective[s]?[\s:.\-]+", text, re.IGNORECASE)
    if m:
        obj = text[m.end(): m.end() + 4000]
    strat = ""
    m2 = re.search(r"principal\s+investment\s+strateg(?:y|ies)[\s:.\-]+", text, re.IGNORECASE)
    if m2:
        strat = text[m2.end(): m2.end() + 25000]
    if not obj and not strat:
        strat = text[:20000]
    return obj.strip(), strat.strip()


def _row_to_render_dict(row: FilingAnalysis, filing: Filing, trust: Trust,
                        fund_names: list[str]) -> dict:
    """Convert a cached FilingAnalysis row into the dict shape used by the
    email renderer."""
    return {
        "accession": filing.accession_number,
        "form": filing.form,
        "trust_name": trust.name,
        "primary_link": row.prospectus_url or filing.primary_link or "",
        "fund_names": fund_names,
        "filing_title": row.filing_title,
        "strategy_type": row.strategy_type,
        "underlying": row.underlying,
        "structure": row.structure,
        "portfolio_holding": row.portfolio_holding,
        "distribution": row.distribution,
        "narrative": row.narrative,
        "interestingness": row.interestingness or 0.0,
        "selector_reason": row.selector_reason,
    }


def _cost_usd(tokens_in: int, tokens_out: int) -> float:
    """Sonnet pricing: $3/M input, $15/M output."""
    return (tokens_in * 3 / 1_000_000) + (tokens_out * 15 / 1_000_000)


def run_analysis_for_day(db_session: Session, date_iso: str) -> list[dict]:
    """Return up to 3 render-ready analyses for the given date.

    Cache-first: any filing with an existing FilingAnalysis row is used as-is.
    LLM calls are made only for uncached filings. If every new filing is
    already cached, this function performs ZERO network I/O to Anthropic.
    """
    candidates = _query_candidates(db_session, date_iso)
    if not candidates:
        return []

    filing_ids = [c["filing_id"] for c in candidates]
    cached_rows = (
        db_session.query(FilingAnalysis)
        .filter(FilingAnalysis.filing_id.in_(filing_ids))
        .all()
    )
    cached_by_fid = {r.filing_id: r for r in cached_rows}

    # Hydrate cached rows back into render dicts (no LLM calls).
    rendered: list[dict] = []
    for c in candidates:
        row = cached_by_fid.get(c["filing_id"])
        if not row:
            continue
        filing = db_session.query(Filing).filter(Filing.id == c["filing_id"]).one()
        trust = db_session.query(Trust).filter(Trust.id == filing.trust_id).one()
        rendered.append(_row_to_render_dict(row, filing, trust, c["fund_names"]))

    uncached = [c for c in candidates if c["filing_id"] not in cached_by_fid]
    # Canonical-set semantics: once we have MAX_PICKS cached entries for a
    # date, further calls for that date do NOT re-pick or re-analyze. The
    # first batch of analyses is the authoritative set. This is what makes
    # re-renders of the daily free — zero LLM calls after the first run.
    if len(rendered) >= MAX_PICKS or not uncached:
        rendered.sort(key=lambda d: d.get("interestingness", 0.0), reverse=True)
        return rendered[:MAX_PICKS]

    # --- LLM path: selector -> fetch/extract -> writer -> cache ---
    try:
        picks, sel_usage = claude_service.select_top_filings(uncached)
    except Exception as e:
        log.error("Selector failed: %s", e)
        rendered.sort(key=lambda d: d.get("interestingness", 0.0), reverse=True)
        return rendered[:MAX_PICKS]

    picks = picks[:MAX_PICKS]
    uncached_by_acc = {c["accession"]: c for c in uncached}
    sec_client = SECClient(pause=0.25, user_agent=SEC_USER_AGENT)

    for pick in picks:
        cand = uncached_by_acc.get(pick.get("accession"))
        if not cand:
            continue
        score = float(pick.get("score", 0.0))
        reason = pick.get("reason", "")

        original_link = cand["primary_link"]
        resolved_link, _ = resolve_prospectus_url(sec_client, original_link)
        if resolved_link != original_link:
            cand["primary_link"] = resolved_link

        try:
            raw = sec_client.fetch_text(resolved_link)
        except Exception as e:
            log.warning("fetch failed for %s: %s", cand["accession"], e)
            raw = ""
            if cand["submission_txt_link"]:
                try:
                    raw = sec_client.fetch_text(cand["submission_txt_link"])
                except Exception as e2:
                    log.warning("fallback fetch failed for %s: %s", cand["accession"], e2)
                    continue
            else:
                continue
        if not raw:
            continue

        obj, strat = extract_sections(raw)

        try:
            analysis, wr_usage = claude_service.analyze_top_filing(cand, obj, strat)
        except Exception as e:
            log.error("Writer failed for %s: %s", cand["accession"], e)
            traceback.print_exc()
            continue

        tokens_in = int(wr_usage.get("input_tokens", 0))
        tokens_out = int(wr_usage.get("output_tokens", 0))

        row = FilingAnalysis(
            filing_id=cand["filing_id"],
            analyzed_at=datetime.utcnow(),
            prospectus_url=cand["primary_link"],
            objective_excerpt=obj[:8000],
            strategy_excerpt=strat[:40000],
            filing_title=analysis.get("filing_title"),
            strategy_type=analysis.get("strategy_type"),
            underlying=analysis.get("underlying"),
            structure=analysis.get("structure"),
            portfolio_holding=analysis.get("portfolio_holding"),
            distribution=analysis.get("distribution"),
            narrative=analysis.get("narrative"),
            interestingness=score,
            selector_reason=reason,
            selector_model=sel_usage.get("model"),
            writer_model=wr_usage.get("model"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_cost_usd(tokens_in, tokens_out),
        )
        db_session.add(row)
        try:
            db_session.commit()
        except Exception as e:
            log.error("Failed to persist FilingAnalysis for %s: %s", cand["accession"], e)
            db_session.rollback()
            continue

        rendered.append({
            "accession": cand["accession"],
            "form": cand["form"],
            "trust_name": cand["trust_name"],
            "primary_link": cand["primary_link"],
            "fund_names": cand["fund_names"],
            "filing_title": row.filing_title,
            "strategy_type": row.strategy_type,
            "underlying": row.underlying,
            "structure": row.structure,
            "portfolio_holding": row.portfolio_holding,
            "distribution": row.distribution,
            "narrative": row.narrative,
            "interestingness": score,
            "selector_reason": reason,
        })

    rendered.sort(key=lambda d: d.get("interestingness", 0.0), reverse=True)
    return rendered[:MAX_PICKS]
