"""Autonomous tiered classification engine (engine session 2026-06-09).

Closes the loop the old flow left open: detection worked (scan + sweep),
but every decision waited for a human who never reviewed the queue
(1,669 pending proposals, 1 ever approved). Per Ryu's mandate the system
now classifies autonomously and only escalates true ambiguity.

Tiers (taxonomy contract: docs/CLASSIFICATION.md):
  0. STANDING RULINGS — ticker-level decisions Ryu has made in writing
     (e.g. OBTC out of scope). Applied first, never re-asked.
  1. RULES — classify_engine.scan_unmapped HIGH-confidence candidates
     (deterministic Bloomberg-field derivation). Auto-applied.
  2. LLM — ai_classify.classify_batch on the remainder, then a SECOND
     independent verification call (critic). Apply only when the critic
     AGREEs and confidence is HIGH or MEDIUM:
       - tracked category -> fund_mapping + attributes CSVs
       - Other            -> exclusions.csv full-ticker row (outside the
                             5 tracked categories; suppresses the gap nag)
  3. QUEUE — LOW confidence or critic DISAGREE -> ClassificationProposal
     (the queue is now small enough to mean something).

Every decision is journaled to logs/auto_classify_YYYYMMDD.jsonl and
printed for the systemd journal. Rules are written to RULES_DIR
(data/rules on the VPS) and mirrored to the git-tracked config/rules.

Usage:
    python scripts/auto_classify.py --dry-run          # decide, write nothing
    python scripts/auto_classify.py --apply            # full autonomous run
    python scripts/auto_classify.py --apply --since-days 730
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.rules_editor.classify_engine import (  # noqa: E402
    scan_unmapped, apply_classifications, RULES_DIR,
)
from tools.rules_editor import ai_classify  # noqa: E402
from scripts import fund_master_writer as fmw  # noqa: E402  (full-taxonomy feeder)

log = logging.getLogger("auto_classify")

# ---------------------------------------------------------------------------
# Tier 0 — standing rulings (Ryu's written decisions; never re-litigated)
# Format: ticker -> (action, reason). Actions: "exclude" | ("classify", cat, attrs)
# ---------------------------------------------------------------------------
STANDING_RULINGS: dict[str, tuple] = {
    # Ryu 2026-06-08: only REX-Osprey products count; Osprey-only funds are out
    # of scope for the tracker ("we shouldn't even be looking at OBTC").
    "OBTC US": ("exclude", "ryu-ruling-2026-06-08: Osprey-only product, out of scope"),
}

_MIRROR_FILES = (
    "fund_master.csv",  # FULL-SCALE 3-axis master — its 2026-05-01 desync must never repeat
    "fund_mapping.csv", "exclusions.csv", "issuer_mapping.csv",
    "attributes_LI.csv", "attributes_CC.csv",
    "attributes_Crypto.csv", "attributes_Defined.csv", "attributes_Thematic.csv",
)

_VERIFY_PROMPT = """You are auditing ETF classifications for REX Financial. Another
analyst proposed the classifications below. Your ONLY job is to catch mistakes.

For each fund output exactly one JSON object per line:
{"ticker": "ABC US", "verdict": "AGREE"}            -- proposal is correct
{"ticker": "ABC US", "verdict": "DISAGREE", "why": "..."}  -- category or key attribute is wrong

DISAGREE whenever: the category is wrong; an LI direction/leverage/underlier is wrong;
a fund labeled Other actually fits LI/CC/Crypto/Defined/Thematic; or the fund name
contradicts the proposal. When genuinely uncertain, DISAGREE (uncertain = human review).
"""


def _journal(fh, **kv):
    kv["at"] = datetime.utcnow().isoformat()
    fh.write(json.dumps(kv, default=str) + "\n")


def _verify_proposals(proposals, source_funds, model=ai_classify.MODEL):
    """Second independent LLM pass; returns {ticker: True(agree)/False}."""
    if not proposals:
        return {}
    import anthropic
    client = anthropic.Anthropic()
    lines = []
    by_ticker = {f.get("ticker"): f for f in source_funds}
    for p in proposals:
        f = by_ticker.get(p.ticker, {})
        lines.append(
            f"fund: ticker={p.ticker}; name={f.get('fund_name','')}; "
            f"issuer={f.get('issuer','')} | proposed: category={p.category}"
            + (f", direction={p.direction}, leverage={p.leverage}, underlier={p.underlier}"
               if p.category == "LI" else "")
            + (f", rationale={p.rationale}")
        )
    try:
        resp = client.messages.create(
            model=model, max_tokens=2048,
            system=[{"type": "text", "text": _VERIFY_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": "Audit these:\n\n" + "\n".join(lines)}],
        )
        text = resp.content[0].text if resp.content else ""
    except Exception as e:
        log.error("verification call failed: %s — treating all as unverified", e)
        return {p.ticker: False for p in proposals}
    verdicts = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
            verdicts[str(o.get("ticker", "")).strip()] = (o.get("verdict") == "AGREE")
        except json.JSONDecodeError:
            continue
    return verdicts


def _proposal_to_candidate(p, source: dict) -> dict:
    """AIProposal -> the candidate dict apply_classifications expects."""
    d = ai_classify.proposal_to_db_dict(p)
    return {
        "ticker": p.ticker,
        "fund_name": source.get("fund_name", ""),
        "issuer": source.get("issuer", ""),
        "etp_category": p.category,
        "attributes": json.loads(d["attributes_json"]),
    }


def _append_exclusion(ticker: str, reason: str, dry_run: bool) -> bool:
    """Full-ticker exclusion row (etp_category empty = outside all 5 categories)."""
    import pandas as pd
    path = RULES_DIR / "exclusions.csv"
    df = pd.read_csv(path) if path.exists() else __import__("pandas").DataFrame(
        columns=["ticker", "etp_category", "reason"])
    if ticker in set(df["ticker"].astype(str)):
        return False
    if not dry_run:
        df.loc[len(df)] = {"ticker": ticker, "etp_category": None, "reason": reason[:200]}
        df.to_csv(path, index=False)
    return True


def _mirror_rules(dry_run: bool) -> None:
    """Keep data/rules (runtime truth) and config/rules (git) identical."""
    if dry_run:
        return
    data_dir = PROJECT_ROOT / "data" / "rules"
    cfg_dir = PROJECT_ROOT / "config" / "rules"
    if not (data_dir.exists() and cfg_dir.exists()):
        return
    src, dst = (RULES_DIR, cfg_dir if RULES_DIR == data_dir else data_dir)
    for name in _MIRROR_FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)


def _fix_issuer_gaps(fh, dry_run: bool) -> int:
    """Already-classified ACTV funds whose (category, issuer) pair is missing
    from issuer_mapping.csv show issuer_display=NULL on every page/report.
    Deterministic fix: register the pair; _update_issuer_mapping derives the
    display nickname (brand-overrides layer can refine later)."""
    import sqlite3
    from tools.rules_editor.classify_engine import _update_issuer_mapping
    con = sqlite3.connect(str(PROJECT_ROOT / "data" / "etp_tracker.db"))
    rows = con.execute("""
        SELECT ticker, etp_category, issuer FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category IS NOT NULL
          AND (issuer_display IS NULL OR issuer_display='')
          AND issuer IS NOT NULL AND issuer != ''
    """).fetchall()
    con.close()
    if not rows:
        return 0
    pseudo = [{"ticker": t, "etp_category": c, "issuer": i} for t, c, i in rows]
    for t, c, i in rows:
        _journal(fh, ticker=t, tier=1, decision="issuer-mapping",
                 category=c, issuer=i, dry_run=dry_run)
    if dry_run:
        return len(rows)
    return _update_issuer_mapping(pseudo)


def _fix_cc_attr_gaps(fh, dry_run: bool, max_funds: int = 30) -> tuple[int, int]:
    """ACTV CC funds missing from attributes_CC.csv (the autocall tool + CC
    analytics read that file). LLM derives underlier/cc_category; the critic
    pass is skipped — attribute fill on an already-confirmed category is
    lower-stakes than categorization. Returns (fixed, api_calls)."""
    import sqlite3
    import pandas as pd
    attr_path = RULES_DIR / "attributes_CC.csv"
    have = set()
    if attr_path.exists():
        have = set(pd.read_csv(attr_path)["ticker"].astype(str).str.strip())
    con = sqlite3.connect(str(PROJECT_ROOT / "data" / "etp_tracker.db"))
    rows = con.execute("""
        SELECT ticker, fund_name, issuer FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category='CC'
    """).fetchall()
    con.close()
    missing = [{"ticker": t, "fund_name": n_, "issuer": i}
               for t, n_, i in rows if t not in have][:max_funds]
    if not missing or not ai_classify.is_available():
        return (0, 0)
    proposals = ai_classify.classify_batch(missing)
    name_by_ticker = {m["ticker"]: m["fund_name"] for m in missing}
    fixed = 0
    if not dry_run and proposals:
        df = pd.read_csv(attr_path) if attr_path.exists() else pd.DataFrame(
            columns=["ticker", "map_cc_underlier", "map_cc_index", "cc_type", "cc_category"])
        for p in proposals:
            if not p.ticker or p.ticker in have:
                continue
            is_autocall = "AUTOCALL" in str(name_by_ticker.get(p.ticker, "")).upper()
            df.loc[len(df)] = {
                "ticker": p.ticker,
                "map_cc_underlier": p.underlier or "",
                "map_cc_index": "",
                "cc_type": "Autocallable" if is_autocall else "Traditional",
                "cc_category": p.cc_category or "Single Stock",
            }
            fixed += 1
            _journal(fh, ticker=p.ticker, tier=2, decision="cc-attrs",
                     underlier=p.underlier, cc_category=p.cc_category, dry_run=dry_run)
        df.to_csv(attr_path, index=False)
    elif proposals:
        fixed = len(proposals)
        for p in proposals:
            _journal(fh, ticker=p.ticker, tier=2, decision="cc-attrs",
                     underlier=p.underlier, dry_run=dry_run)
    return (fixed, 1)


def run(since_days: int, limit: int, dry_run: bool) -> dict:
    from webapp.database import init_db, SessionLocal
    from webapp.models import ClassificationProposal

    stats = {"rules_applied": 0, "ai_applied": 0, "ai_excluded": 0,
             "queued": 0, "rulings": 0, "api_calls": 0,
             "issuer_fixed": 0, "cc_attrs_fixed": 0,
             "master_rows_added": 0, "master_healed": 0}
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    jpath = log_dir / f"auto_classify_{date.today().strftime('%Y%m%d')}.jsonl"
    fh = jpath.open("a", encoding="utf-8")

    scan = scan_unmapped(since_days=since_days)
    candidates = scan.get("candidates", [])
    outside = scan.get("outside", [])

    # ---- Tier 0: standing rulings -----------------------------------------
    ruled_tickers = set()
    for pool in (candidates, outside):
        for f in list(pool):
            t = f.get("ticker", "")
            if t in STANDING_RULINGS:
                action = STANDING_RULINGS[t]
                if action[0] == "exclude":
                    if _append_exclusion(t, action[1], dry_run):
                        stats["rulings"] += 1
                        _journal(fh, ticker=t, tier=0, decision="exclude",
                                 reason=action[1], dry_run=dry_run)
                pool.remove(f)
                ruled_tickers.add(t)

    # ---- Tier 1: rule-engine HIGH candidates -> auto-apply ----------------
    if candidates:
        if not dry_run:
            apply_classifications(candidates)
        stats["rules_applied"] = len(candidates)
        # FULL-SCALE layer: every applied fund also gets a 28-column
        # fund_master.csv row (the missing feeder, Ryu 2026-06-10).
        stats["master_rows_added"] += fmw.append_rows(
            [fmw.candidate_to_master_row(c) for c in candidates], dry_run)
        for c in candidates:
            _journal(fh, ticker=c["ticker"], tier=1, decision="apply",
                     category=c["etp_category"], source="rules",
                     confidence=c.get("confidence", "HIGH"), dry_run=dry_run)

    # ---- Tier 2: LLM + independent verification ---------------------------
    # The outside bucket includes hundreds of LEGACY unclassified funds
    # (scan is status-agnostic for backfill). Newest launches are what the
    # gap audit nags about and what reports need first — process newest-first
    # so the daily limit always covers the funds that matter today.
    outside.sort(key=lambda f: str(f.get("inception_date", "")), reverse=True)
    outside = outside[:limit]
    proposals = []
    if outside and ai_classify.is_available():
        for i in range(0, len(outside), 20):
            batch = outside[i:i + 20]
            proposals.extend(ai_classify.classify_batch(batch))
            stats["api_calls"] += 1
    elif outside:
        log.warning("LLM tier unavailable (no API key/SDK) — %d funds go to queue",
                    len(outside))

    verdicts = {}
    if proposals:
        verdicts = _verify_proposals(proposals, outside)
        stats["api_calls"] += 1

    init_db()
    db = SessionLocal()
    try:
        existing_q = {p.ticker for p in db.query(ClassificationProposal.ticker).all()}
        by_ticker = {f.get("ticker"): f for f in outside}
        proposed_tickers = set()
        apply_batch = []
        for p in proposals:
            if not p.ticker or p.ticker in ruled_tickers:
                continue
            proposed_tickers.add(p.ticker)
            agreed = verdicts.get(p.ticker, False)
            confident = p.confidence in ("HIGH", "MEDIUM")
            src = by_ticker.get(p.ticker, {})
            if agreed and confident and p.category in ("LI", "CC", "Crypto", "Defined", "Thematic"):
                apply_batch.append(_proposal_to_candidate(p, src))
                stats["ai_applied"] += 1
                _journal(fh, ticker=p.ticker, tier=2, decision="apply",
                         category=p.category, source="ai+verified",
                         confidence=p.confidence, rationale=p.rationale, dry_run=dry_run)
            elif agreed and confident and p.category == "Other":
                if _append_exclusion(p.ticker, f"auto-llm-other: {p.rationale}", dry_run):
                    stats["ai_excluded"] += 1
                    _journal(fh, ticker=p.ticker, tier=2, decision="exclude-other",
                             rationale=p.rationale, confidence=p.confidence, dry_run=dry_run)
                # fund_master is FULL-UNIVERSE: 'Other' still earns a real
                # Plain-Beta/etc. row via the universal rule cascade.
                stats["master_rows_added"] += fmw.append_rows(
                    [fmw.other_to_master_row(src, p.rationale)], dry_run)
            else:
                if p.ticker not in existing_q and not dry_run:
                    d = ai_classify.proposal_to_db_dict(p)
                    db.add(ClassificationProposal(
                        ticker=p.ticker, fund_name=src.get("fund_name"),
                        issuer=src.get("issuer"), aum=src.get("aum"),
                        proposed_category=d["proposed_category"] or "Other",
                        proposed_strategy="AI",
                        confidence=p.confidence,
                        reason=("critic disagreed; " if not agreed else "low confidence; ")
                               + p.rationale,
                        attributes_json=d["attributes_json"], status="pending",
                    ))
                stats["queued"] += 1
                _journal(fh, ticker=p.ticker, tier=3, decision="queue",
                         agreed=agreed, confidence=p.confidence,
                         rationale=p.rationale, dry_run=dry_run)
        # funds the LLM never returned a line for -> queue visibility
        for t, f in by_ticker.items():
            if t and t not in proposed_tickers and t not in ruled_tickers:
                stats["queued"] += 1
                _journal(fh, ticker=t, tier=3, decision="queue",
                         reason="no LLM verdict returned", dry_run=dry_run)

        if apply_batch and not dry_run:
            apply_classifications(apply_batch)
        if apply_batch:
            stats["master_rows_added"] += fmw.append_rows(
                [fmw.candidate_to_master_row(c) for c in apply_batch], dry_run)
        if not dry_run:
            db.commit()
    finally:
        db.close()
        fh.close()

    # ---- FULL-LAYER drift heal: legacy-classified funds missing a
    # fund_master row (e.g. everything classified 2026-05-01..06-09 while the
    # feeder was frozen). Translate from the mkt row's own map_* columns.
    import sqlite3 as _sq
    _con = _sq.connect(str(PROJECT_ROOT / "data" / "etp_tracker.db"))
    _con.row_factory = _sq.Row
    _rows = _con.execute("""
        SELECT ticker, fund_name, etp_category, asset_class_focus,
               map_li_direction, map_li_leverage_amount, map_li_underlier,
               map_cc_underlier, map_crypto_underlier, cc_category
        FROM mkt_master_data
        WHERE market_status='ACTV' AND etp_category IS NOT NULL
    """).fetchall()
    _con.close()
    _missing = fmw.missing_from_master([r["ticker"] for r in _rows])
    fh3 = jpath.open("a", encoding="utf-8")
    heal_batch = []
    for r in _rows:
        if r["ticker"] not in _missing:
            continue
        heal_batch.append(fmw.candidate_to_master_row({
            "ticker": r["ticker"], "fund_name": r["fund_name"],
            "etp_category": r["etp_category"],
            "asset_class": r["asset_class_focus"] or "",
            "attributes": {
                "map_li_direction": r["map_li_direction"] or "",
                "map_li_leverage_amount": r["map_li_leverage_amount"] or "",
                "map_li_underlier": r["map_li_underlier"] or "",
                "map_cc_underlier": r["map_cc_underlier"] or "",
                "map_crypto_underlier": r["map_crypto_underlier"] or "",
                "cc_category": r["cc_category"] or "",
            },
        }))
        _journal(fh3, ticker=r["ticker"], tier=1, decision="master-heal",
                 category=r["etp_category"], dry_run=dry_run)
    fh3.close()
    if heal_batch:
        stats["master_healed"] = fmw.append_rows(heal_batch, dry_run)

    # ---- Issuer-display + CC-attribute gap fills ---------------------------
    fh2 = jpath.open("a", encoding="utf-8")
    try:
        stats["issuer_fixed"] = _fix_issuer_gaps(fh2, dry_run)
        cc_fixed, cc_calls = _fix_cc_attr_gaps(fh2, dry_run)
        stats["cc_attrs_fixed"] = cc_fixed
        stats["api_calls"] += cc_calls
    finally:
        fh2.close()

    _mirror_rules(dry_run)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since-days", type=int, default=60)
    ap.add_argument("--limit", type=int, default=80, help="max LLM-tier funds per run")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="default unless --apply is given")
    args = ap.parse_args()
    dry_run = not args.apply

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = run(since_days=args.since_days, limit=args.limit, dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"\n=== auto_classify ({mode}) ===")
    for k, v in stats.items():
        print(f"  {k:14s} {v}")
    # exit 0 always: queue depth is reported, not a failure (warn != fail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
