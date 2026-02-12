"""CLI entry point for generating screener PDF reports.

Usage:
    # Candidate evaluation + appended rankings (primary use case):
    python -m screener.generate_report SCCO SIL AMPX BHP ERO RIO HBM TECK ZETA

    # Standalone universe rankings report:
    python -m screener.generate_report --rankings

    # 3x leveraged filing recommendation report:
    python -m screener.generate_report --3x
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _compute_rankings(stock_df, etp_df):
    """Compute universe rankings and REX fund data. Shared by both report modes."""
    from screener.scoring import (
        compute_percentile_scores, derive_rex_benchmarks,
        apply_threshold_filters, apply_competitive_penalty,
    )
    from screener.competitive import compute_competitive_density
    from screener.filing_match import match_filings

    # Score
    benchmarks = derive_rex_benchmarks(etp_df, stock_df)
    scored = compute_percentile_scores(stock_df)
    scored = apply_threshold_filters(scored, benchmarks)

    # Competitive density + penalty
    density = compute_competitive_density(etp_df)
    scored = apply_competitive_penalty(scored, density)

    # Filing match
    scored = match_filings(scored, etp_df)

    # Build density lookup
    density_lookup = {}
    if not density.empty:
        for _, row in density.iterrows():
            underlier = str(row["underlier"]).replace(" US", "").replace(" Curncy", "")
            density_lookup[underlier] = row

    # Build results list
    results = []
    for _, row in scored.iterrows():
        ticker_clean = str(row.get("ticker_clean", row.get("Ticker", ""))).upper()
        d_info = density_lookup.get(ticker_clean, {})

        results.append({
            "ticker": str(row.get("Ticker", "")),
            "sector": str(row["GICS Sector"]) if pd.notna(row.get("GICS Sector")) else None,
            "composite_score": float(row.get("composite_score", 0)),
            "mkt_cap": float(row.get("Mkt Cap", 0)) if pd.notna(row.get("Mkt Cap")) else None,
            "total_oi_pctl": float(row.get("Total OI_pctl", 0)) if pd.notna(row.get("Total OI_pctl")) else None,
            "passes_filters": bool(row.get("passes_filters", False)),
            "filing_status": str(row.get("filing_status", "Not Filed")),
            "market_signal": row.get("market_signal"),
            "competitive_density": str(d_info.get("density_category", "")) if hasattr(d_info, "get") and d_info.get("density_category") else None,
        })

    # REX fund data
    rex_all = etp_df[etp_df.get("is_rex") == True].copy()
    rex_funds = []
    seen = set()
    for _, row in rex_all.iterrows():
        t = row.get("ticker", "")
        if t in seen:
            continue
        seen.add(t)
        rex_funds.append({
            "ticker": t,
            "underlier": row.get("q_category_attributes.map_li_underlier", ""),
            "aum": round(float(pd.to_numeric(row.get("t_w4.aum", 0), errors="coerce") or 0), 1),
            "flow_1m": round(float(pd.to_numeric(row.get("t_w4.fund_flow_1month", 0), errors="coerce") or 0), 1),
            "flow_3m": round(float(pd.to_numeric(row.get("t_w4.fund_flow_3month", 0), errors="coerce") or 0), 1),
            "flow_ytd": round(float(pd.to_numeric(row.get("t_w4.fund_flow_ytd", 0), errors="coerce") or 0), 1),
            "return_ytd": round(float(pd.to_numeric(row.get("t_w3.total_return_ytd", 0), errors="coerce") or 0), 1),
        })

    return results, rex_funds


def run_candidate_evaluation(tickers: list[str]) -> Path:
    """Run candidate evaluation + universe rankings combined PDF."""
    from screener.data_loader import load_all
    from screener.candidate_evaluator import evaluate_candidates
    from screener.report_generator import generate_candidate_report
    from screener.config import REPORTS_DIR

    REPORTS_DIR.mkdir(exist_ok=True)

    # Load data once (shared between candidate eval and rankings)
    log.info("Loading data...")
    data = load_all()
    stock_df = data["stock_data"]
    etp_df = data["etp_data"]

    # Candidate evaluation
    log.info("Evaluating %d candidates: %s", len(tickers), ", ".join(tickers))
    candidates = evaluate_candidates(tickers, stock_df=stock_df, etp_df=etp_df)

    # Universe rankings (appended to report)
    log.info("Computing universe rankings...")
    rankings, rex_funds = _compute_rankings(stock_df, etp_df)

    # Generate combined PDF
    pdf_bytes = generate_candidate_report(
        candidates,
        rankings=rankings,
        rex_funds=rex_funds,
    )

    today = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"Candidate_Evaluation_{today}.pdf"
    out_path.write_bytes(pdf_bytes)

    log.info("PDF saved: %s (%d bytes)", out_path, len(pdf_bytes))

    # Print summary
    for c in candidates:
        status = c["verdict"]
        print(f"  {c['ticker_clean']:8s} {status:10s} {c['reason']}")

    return out_path


def run_rankings_report() -> Path:
    """Run standalone universe rankings PDF."""
    from screener.data_loader import load_all
    from screener.report_generator import generate_rankings_report
    from screener.config import REPORTS_DIR

    REPORTS_DIR.mkdir(exist_ok=True)

    data = load_all()
    stock_df = data["stock_data"]
    etp_df = data["etp_data"]

    results, rex_funds = _compute_rankings(stock_df, etp_df)

    pdf_bytes = generate_rankings_report(results, rex_funds=rex_funds)

    today = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"ETF_Launch_Screener_{today}.pdf"
    out_path.write_bytes(pdf_bytes)

    log.info("PDF saved: %s (%d bytes)", out_path, len(pdf_bytes))
    return out_path


def run_3x_report() -> Path:
    """Generate 3x & 4x leveraged ETF filing recommendation report (V2)."""
    from screener.data_loader import load_all
    from screener.scoring import (
        compute_percentile_scores,
        apply_threshold_filters, apply_competitive_penalty,
    )
    from screener.competitive import compute_competitive_density
    from screener.analysis_3x import (
        get_3x_market_snapshot, get_top_2x_single_stock,
        get_underlier_popularity, get_rex_track_record,
        get_3x_candidates, get_4x_candidates,
        compute_blowup_risk, compute_3x_filing_score,
    )
    from screener.report_3x_generator import generate_3x_report
    from screener.config import REPORTS_DIR

    REPORTS_DIR.mkdir(exist_ok=True)

    log.info("Loading data...")
    data = load_all()
    stock_df = data["stock_data"]
    etp_df = data["etp_data"]

    # Get Bloomberg data date from file modification time
    from screener.config import DATA_FILE
    import os
    data_date = None
    if DATA_FILE.exists():
        mtime = os.path.getmtime(DATA_FILE)
        data_date = datetime.fromtimestamp(mtime).strftime("%B %d, %Y")

    # Score stocks (skip REX benchmark thresholds - only market cap filter)
    log.info("Scoring stocks...")
    scored = compute_percentile_scores(stock_df)
    scored = apply_threshold_filters(scored, benchmarks=None)
    density = compute_competitive_density(etp_df)
    scored = apply_competitive_penalty(scored, density)

    # Compute 3x filing score (40% fundamentals + 60% 2x AUM demand)
    log.info("Computing 3x filing score...")
    scored = compute_3x_filing_score(scored, etp_df)

    # Run analysis functions
    log.info("Computing 3x market snapshot...")
    snapshot = get_3x_market_snapshot(etp_df)

    log.info("Getting top 2x single-stock ETFs...")
    top_2x = get_top_2x_single_stock(etp_df, n=100)

    log.info("Computing underlier popularity...")
    underlier_pop = get_underlier_popularity(etp_df, stock_df, top_n=50)

    log.info("Building REX track record (all products)...")
    rex_track = get_rex_track_record(etp_df, scored)

    log.info("Computing 3x filing candidates (tiered 50/50/100)...")
    tiers = get_3x_candidates(scored, etp_df)

    log.info("Computing 4x filing candidates...")
    four_x = get_4x_candidates(etp_df, stock_df)

    log.info("Computing blow-up risk...")
    risk_df = compute_blowup_risk(stock_df)

    # Build scoped risk watchlist: Tier 1 + Tier 2 + top AUM underliers only
    # (skip Tier 3 "monitor" - they're low priority, keeps watchlist manageable)
    scope_tickers = set()
    for tier in ("tier_1", "tier_2"):
        for c in tiers.get(tier, []):
            scope_tickers.add(c["ticker"].upper())
    for r in underlier_pop:
        scope_tickers.add(r["underlier"].upper())

    # Build 2x AUM + REX 2x lookup for risk watchlist enrichment
    from screener.analysis_3x import _build_2x_aum_lookup, _build_rex_2x_status
    aum_lookup = _build_2x_aum_lookup(etp_df)
    rex_2x_status = _build_rex_2x_status(etp_df)

    risk_watchlist = []
    for _, row in risk_df.iterrows():
        tc = str(row.get("ticker_clean", "")).upper()
        if tc not in scope_tickers:
            continue
        entry = row.to_dict()
        # Enrich with 2x AUM and REX 2x status
        entry["aum_2x"] = aum_lookup.get(tc, {}).get("aum_2x", 0)
        entry["rex_2x"] = rex_2x_status.get(tc, "No")
        risk_watchlist.append(entry)

    # Sort by 2x AUM descending (execs see biggest positions first)
    risk_watchlist.sort(key=lambda x: x.get("aum_2x", 0), reverse=True)

    # Generate PDF
    log.info("Generating PDF...")
    pdf_bytes = generate_3x_report(
        snapshot=snapshot,
        top_2x=top_2x,
        underlier_pop=underlier_pop,
        rex_track=rex_track,
        tiers=tiers,
        four_x_candidates=four_x,
        risk_watchlist=risk_watchlist,
        data_date=data_date,
    )

    today = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"3x_4x_Filing_Recommendations_{today}.pdf"
    # If file is locked (open in viewer), try versioned name
    try:
        out_path.write_bytes(pdf_bytes)
    except PermissionError:
        for v in range(2, 20):
            alt = REPORTS_DIR / f"3x_4x_Filing_Recommendations_{today}_v{v}.pdf"
            try:
                alt.write_bytes(pdf_bytes)
                out_path = alt
                break
            except PermissionError:
                continue

    log.info("PDF saved: %s (%d bytes)", out_path, len(pdf_bytes))

    # Print summary
    t1 = len(tiers.get("tier_1", []))
    t2 = len(tiers.get("tier_2", []))
    t3 = len(tiers.get("tier_3", []))
    print(f"\n  3x Market: {snapshot['product_count']} products, {snapshot['total_aum']:.0f}M AUM")
    print(f"  2x Market: {snapshot.get('total_2x_count', 0)} products, {snapshot.get('total_2x_aum', 0):.0f}M AUM")
    print(f"  Candidates: {t1} Tier 1, {t2} Tier 2, {t3} Tier 3")
    print(f"  4x candidates: {len(four_x)} stocks")
    print(f"  Risk watchlist: {len(risk_watchlist)} stocks (scoped)")

    return out_path


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print("  python -m screener.generate_report SCCO AMPX ZETA   # Evaluate candidates")
        print("  python -m screener.generate_report --rankings        # Universe rankings")
        print("  python -m screener.generate_report --3x              # 3x filing recommendations")
        sys.exit(1)

    if args[0] == "--rankings":
        path = run_rankings_report()
    elif args[0] == "--3x":
        path = run_3x_report()
    else:
        path = run_candidate_evaluation(args)

    print(f"\nReport saved: {path}")


if __name__ == "__main__":
    main()
