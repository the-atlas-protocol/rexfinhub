"""AI-powered classification using Claude API.

For the 547+ funds that the rule-based scanner can't confidently classify
(the "outside" bucket), use Claude Haiku to propose a category + attributes.

Design:
  - Opt-in: requires ANTHROPIC_API_KEY in env
  - Cheap: Haiku 4.5 model, batches of 20 funds per call
  - Prompt caching: system prompt + taxonomy definition cached
  - Structured output: returns JSON with category, subcategory, rationale
  - Safe default: returns proposals, does NOT auto-write to rules
  - Human-reviewed: all AI proposals go to ClassificationProposal queue

Usage:
    # Dry run
    python -m tools.rules_editor.ai_classify --dry-run

    # Scan up to 50 unmapped funds
    python -m tools.rules_editor.ai_classify --limit 50

    # From Python
    from tools.rules_editor.ai_classify import classify_batch
    proposals = classify_batch(funds_list)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Current Anthropic models (May 2025 knowledge cutoff):
# - claude-haiku-4-5-20251001 — fastest, cheapest, good for structured classification
# - claude-sonnet-4-6 — stronger reasoning, higher cost
MODEL = "claude-haiku-4-5-20251001"

TRACKED_CATEGORIES = {
    "LI": "Leverage & Inverse — leveraged or inverse exposure to an underlier (2x, 3x long/short)",
    "CC": "Covered Call / Income — options income strategies, covered call writing, synthetic income",
    "Crypto": "Crypto — Bitcoin, Ethereum, Solana, XRP, or other cryptocurrency exposure (spot, futures, staked)",
    "Defined": "Defined Outcome — buffer, floor, accelerator, barrier, or other structured outcome strategies",
    "Thematic": "Thematic — focused on a specific theme like AI, robotics, clean energy, cybersecurity, quantum, nuclear",
    "Other": "Does NOT fit any of the 5 tracked categories (traditional broad-market, sector, style-factor, bond, commodity)",
}

SYSTEM_PROMPT = """You are classifying ETFs and ETNs for REX Financial's product tracker.

You will receive a list of funds. For each fund, decide which category it belongs to and explain your reasoning briefly.

Output exactly one JSON object per fund, one per line, like:
{"ticker": "ABC US", "category": "LI", "subcategory": "Single Stock", "direction": "Long", "leverage": 2, "underlier": "NVDA", "confidence": "HIGH", "rationale": "2X leveraged long NVDA daily target"}

Rules:
- `category` must be one of: LI, CC, Crypto, Defined, Thematic, Other
- `confidence` must be one of: HIGH (very sure), MEDIUM (likely but ambiguous), LOW (uncertain)
- For LI: include `direction` (Long/Short), `leverage` (number), and `underlier` (ticker or index)
- For CC: include `underlier` (stock/index) and `cc_category` (Single Stock, Broad Beta, Tech, etc.)
- For Crypto: include `underlier` (Bitcoin, Ethereum, Solana, XRP, Litecoin, Dogecoin, Multi-Crypto)
- For Defined: include `defined_category` (Buffer, Floor, Accelerator, Barrier, Ladder, Hedged Equity)
- For Thematic: include `theme` (AI, Robotics, Clean Energy, Cybersecurity, Nuclear, Quantum, etc.)
- For Other: just category and rationale — no extra fields
- `rationale` should be ONE sentence explaining WHY you picked that category
- If the fund could fit multiple categories, pick the PRIMARY one and note alternatives in rationale

Be decisive. Don't hedge. A LOW confidence with a clear rationale is better than refusing to classify."""


@dataclass
class AIProposal:
    """AI-generated classification proposal."""
    ticker: str
    category: str  # LI, CC, Crypto, Defined, Thematic, Other
    confidence: str  # HIGH, MEDIUM, LOW
    rationale: str
    subcategory: Optional[str] = None
    direction: Optional[str] = None
    leverage: Optional[float] = None
    underlier: Optional[str] = None
    theme: Optional[str] = None
    defined_category: Optional[str] = None
    cc_category: Optional[str] = None
    raw: dict = field(default_factory=dict)


def is_available() -> bool:
    """Check if AI classification is usable (has API key + SDK)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _format_fund_for_prompt(fund: dict) -> str:
    """Compact one-line fund description for the batch prompt."""
    parts = [
        f"ticker={fund.get('ticker', '')}",
        f"name={fund.get('fund_name', '')}",
    ]
    if fund.get("issuer"):
        parts.append(f"issuer={fund['issuer']}")
    if fund.get("asset_class"):
        parts.append(f"asset_class={fund['asset_class']}")
    if fund.get("underlying_index"):
        parts.append(f"index={fund['underlying_index']}")
    if fund.get("outcome_type"):
        parts.append(f"outcome={fund['outcome_type']}")
    if fund.get("is_singlestock"):
        parts.append(f"single_stock={fund['is_singlestock']}")
    if fund.get("leverage_amount"):
        parts.append(f"leverage={fund['leverage_amount']}")
    return "; ".join(parts)


def classify_batch(funds: list[dict], model: str = MODEL) -> list[AIProposal]:
    """Classify a batch of unmapped funds via Claude API.

    Args:
        funds: List of dicts with keys: ticker, fund_name, issuer, asset_class,
               underlying_index, outcome_type, is_singlestock, leverage_amount
        model: Anthropic model ID (default: Haiku 4.5)

    Returns:
        List of AIProposal objects. Empty list if API unavailable or error.
    """
    if not funds:
        return []
    if not is_available():
        log.warning("AI classification unavailable (missing API key or anthropic SDK)")
        return []

    import anthropic

    client = anthropic.Anthropic()

    # Build user message — one line per fund
    fund_lines = [f"{i+1}. {_format_fund_for_prompt(f)}" for i, f in enumerate(funds)]
    user_message = (
        f"Classify the following {len(funds)} funds. "
        f"Output one JSON object per line, nothing else.\n\n"
        + "\n".join(fund_lines)
    )

    try:
        # System prompt is cached — taxonomy definition doesn't change between calls
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        log.error("Anthropic API call failed: %s", e)
        return []

    # Parse response — expect one JSON object per line
    text = response.content[0].text if response.content else ""
    proposals = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        category = obj.get("category", "Other")
        if category not in ("LI", "CC", "Crypto", "Defined", "Thematic", "Other"):
            category = "Other"

        proposals.append(AIProposal(
            ticker=str(obj.get("ticker", "")).strip(),
            category=category,
            confidence=obj.get("confidence", "LOW"),
            rationale=obj.get("rationale", ""),
            subcategory=obj.get("subcategory"),
            direction=obj.get("direction"),
            leverage=float(obj["leverage"]) if obj.get("leverage") is not None else None,
            underlier=obj.get("underlier"),
            theme=obj.get("theme"),
            defined_category=obj.get("defined_category"),
            cc_category=obj.get("cc_category"),
            raw=obj,
        ))

    log.info("AI classified %d/%d funds", len(proposals), len(funds))
    return proposals


def proposal_to_db_dict(p: AIProposal) -> dict:
    """Convert AIProposal to the dict format expected by ClassificationProposal rows."""
    # Build attributes dict based on category
    attrs = {}
    if p.category == "LI":
        if p.direction:
            attrs["map_li_direction"] = p.direction
        if p.leverage:
            attrs["map_li_leverage_amount"] = p.leverage
        if p.underlier:
            attrs["map_li_underlier"] = p.underlier
        if p.subcategory:
            attrs["map_li_subcategory"] = p.subcategory
    elif p.category == "CC":
        if p.underlier:
            attrs["map_cc_underlier"] = p.underlier
        if p.cc_category:
            attrs["cc_category"] = p.cc_category
    elif p.category == "Crypto":
        if p.underlier:
            attrs["map_crypto_underlier"] = p.underlier
    elif p.category == "Defined":
        if p.defined_category:
            attrs["map_defined_category"] = p.defined_category
    elif p.category == "Thematic":
        if p.theme:
            attrs["map_thematic_category"] = p.theme

    return {
        "ticker": p.ticker,
        "proposed_category": p.category if p.category != "Other" else None,
        "proposed_strategy": "AI",
        "confidence": p.confidence,
        "reason": f"[AI] {p.rationale}",
        "attributes_json": json.dumps(attrs),
    }


def scan_and_classify(limit: int = 50, since_days: int = 365, batch_size: int = 20) -> dict:
    """Run the full flow: find LOW-confidence unmapped funds and classify with AI.

    Args:
        limit: Max funds to classify in this run (cost control)
        since_days: Only classify funds launched in the last N days
        batch_size: Funds per Claude API call

    Returns:
        {inserted: int, skipped: int, api_calls: int, error: str | None}
    """
    import json as _json
    from webapp.database import init_db, SessionLocal
    from webapp.models import ClassificationProposal
    from tools.rules_editor.classify_engine import scan_unmapped

    if not is_available():
        return {"inserted": 0, "skipped": 0, "api_calls": 0, "error": "API key not set"}

    init_db()
    db = SessionLocal()

    try:
        # Get funds in the "outside" bucket (LOW confidence from rule-based scanner)
        scan = scan_unmapped(since_days=since_days)
        outside = scan.get("outside", [])[:limit]

        if not outside:
            return {"inserted": 0, "skipped": 0, "api_calls": 0, "error": None,
                    "note": "No unclassified funds to process"}

        # Check which tickers are already in the proposal queue
        existing = {
            p.ticker for p in db.query(ClassificationProposal.ticker).all()
        }

        # Filter to truly new ones
        pending = [f for f in outside if f.get("ticker") and f["ticker"] not in existing]
        skipped = len(outside) - len(pending)

        if not pending:
            return {"inserted": 0, "skipped": skipped, "api_calls": 0, "error": None,
                    "note": "All funds already in queue"}

        # Batch and call Claude
        inserted = 0
        api_calls = 0
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            proposals = classify_batch(batch)
            api_calls += 1

            for p in proposals:
                if not p.ticker:
                    continue
                db_dict = proposal_to_db_dict(p)
                # Only insert if it fits a tracked category (skip Other for now)
                if not db_dict["proposed_category"]:
                    continue

                # Find matching source fund for fund_name/issuer/aum
                source = next((f for f in batch if f.get("ticker") == p.ticker), {})

                db.add(ClassificationProposal(
                    ticker=p.ticker,
                    fund_name=source.get("fund_name"),
                    issuer=source.get("issuer"),
                    aum=source.get("aum"),
                    proposed_category=db_dict["proposed_category"],
                    proposed_strategy=db_dict["proposed_strategy"],
                    confidence=db_dict["confidence"],
                    reason=db_dict["reason"],
                    attributes_json=db_dict["attributes_json"],
                    status="pending",
                ))
                inserted += 1

        db.commit()
        return {
            "inserted": inserted,
            "skipped": skipped,
            "api_calls": api_calls,
            "error": None,
        }
    except Exception as e:
        log.error("AI scan failed: %s", e, exc_info=True)
        return {"inserted": 0, "skipped": 0, "api_calls": 0, "error": str(e)}
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI classification scan")
    parser.add_argument("--limit", type=int, default=50, help="Max funds to classify")
    parser.add_argument("--since-days", type=int, default=365, help="Window for recent funds")
    parser.add_argument("--batch-size", type=int, default=20, help="Funds per API call")
    parser.add_argument("--dry-run", action="store_true", help="Show available without calling API")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        print(f"AI available: {is_available()}")
        print(f"Model: {MODEL}")
        print(f"Would process up to {args.limit} funds in batches of {args.batch_size}")
        import sys
        sys.exit(0)

    result = scan_and_classify(
        limit=args.limit,
        since_days=args.since_days,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, indent=2))
