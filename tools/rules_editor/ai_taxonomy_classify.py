"""Universal AI fund classifier — writes to fund_taxonomy table.

Uses Claude Haiku 4.5 with prompt caching for the taxonomy definition.
Classifies every fund in Bloomberg into the 40-category universal taxonomy.
Safe to run across the full universe (~15k funds, ~$6 one-time cost).

Cost controls:
  1. Budget file at data/.ai_classification_budget.json
     - Monthly cap (default $25)
     - Hard stop if exceeded
     - Reset at the 1st of each month
  2. Usage log at logs/ai_classification.jsonl
     - Every API call logged with tokens + cost estimate
     - Resumable: skips tickers already in fund_taxonomy
  3. Dry-run mode prints cost estimate without calling API
  4. --limit flag caps calls per run

Usage:
    # Estimate cost without calling API
    python -m tools.rules_editor.ai_taxonomy_classify --dry-run

    # Classify 100 funds (~$0.04)
    python -m tools.rules_editor.ai_taxonomy_classify --limit 100

    # Classify everything (~$6, several minutes)
    python -m tools.rules_editor.ai_taxonomy_classify --limit 20000

    # Only new/unclassified funds
    python -m tools.rules_editor.ai_taxonomy_classify --new-only
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Claude Haiku 4.5 — cheapest high-quality model
MODEL = "claude-haiku-4-5-20251001"

# Pricing ($ per 1M tokens) — Claude Haiku 4.5 (May 2025)
PRICE_INPUT_PER_MTOK = 1.0
PRICE_OUTPUT_PER_MTOK = 5.0
PRICE_CACHED_READ_PER_MTOK = 0.10  # 90% discount on cached reads

# Budget controls
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUDGET_FILE = PROJECT_ROOT / "data" / ".ai_classification_budget.json"
USAGE_LOG = PROJECT_ROOT / "logs" / "ai_classification.jsonl"
DEFAULT_MONTHLY_CAP = 25.0  # dollars

BATCH_SIZE = 20  # funds per API call


@dataclass
class CallUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float


def _load_budget() -> dict:
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text())
        except Exception:
            pass
    return {
        "monthly_cap": DEFAULT_MONTHLY_CAP,
        "current_month": date.today().strftime("%Y-%m"),
        "spent_this_month": 0.0,
        "total_calls": 0,
    }


def _save_budget(b: dict) -> None:
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(b, indent=2))


def _reset_if_new_month(b: dict) -> dict:
    current = date.today().strftime("%Y-%m")
    if b.get("current_month") != current:
        b["current_month"] = current
        b["spent_this_month"] = 0.0
        b["total_calls"] = b.get("total_calls", 0)
    return b


def _compute_cost(u: CallUsage) -> float:
    """Calculate cost in USD from token counts."""
    raw_input = (u.input_tokens - u.cached_input_tokens) * PRICE_INPUT_PER_MTOK / 1_000_000
    cached_input = u.cached_input_tokens * PRICE_CACHED_READ_PER_MTOK / 1_000_000
    output = u.output_tokens * PRICE_OUTPUT_PER_MTOK / 1_000_000
    return raw_input + cached_input + output


def _log_usage(usage: CallUsage, batch_size: int, success_count: int) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.utcnow().isoformat(),
            "batch_size": batch_size,
            "success_count": success_count,
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd, 6),
        }) + "\n")


def is_available() -> bool:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _build_system_prompt() -> str:
    from tools.rules_editor.taxonomy import taxonomy_prompt_section
    return f"""You are classifying ETFs and ETNs for REX Financial's universal fund taxonomy.

For each fund you receive, output ONE JSON object on its own line with these keys:
  - ticker
  - primary_category (from the list below)
  - sub_category (free-text, short — e.g., "Large Cap Value", "7-10 Year Treasury")
  - asset_class (from the list below)
  - region (from the list below)
  - sector (GICS sector if sector-specific equity, else null)
  - style_tags (JSON array, zero or more from style tags)
  - factor_tags (JSON array)
  - thematic_tags (JSON array)
  - confidence (HIGH | MEDIUM | LOW)
  - rationale (ONE sentence explaining why)

Rules:
  - Be decisive. LOW with a clear reason is better than refusing.
  - For a leveraged product, primary_category = "Leveraged & Inverse" even if it tracks equities — the leverage IS the strategy.
  - For a covered-call / options-income fund, primary_category = "Income: Covered Call/Options".
  - For a defined-outcome fund (buffer/floor/barrier), primary_category = "Defined Outcome".
  - For spot/futures crypto, primary_category = "Crypto".
  - For single-country equity ETFs (EWJ, INDA, etc.), primary_category = "Equity: Single Country", region = "Single Country".
  - For US sector ETFs (XLK, XLF, etc.), primary_category = "Equity: US Sector", sector = the GICS sector.
  - If nothing fits, use "Other / Unclassified" and explain why in rationale.

Output ONLY JSON objects, one per line. No preamble, no markdown.

{taxonomy_prompt_section()}
"""


def _format_fund_for_prompt(fund: dict) -> str:
    """Compact description of one fund for the batch user message."""
    parts = [f"ticker={fund.get('ticker', '')}", f"name={fund.get('fund_name', '')}"]
    if fund.get("issuer"):
        parts.append(f"issuer={fund['issuer']}")
    if fund.get("asset_class_focus"):
        parts.append(f"asset_focus={fund['asset_class_focus']}")
    if fund.get("underlying_index"):
        parts.append(f"index={fund['underlying_index']}")
    if fund.get("outcome_type"):
        parts.append(f"outcome={fund['outcome_type']}")
    if fund.get("is_singlestock"):
        parts.append(f"single_stock=true")
    if fund.get("leverage_amount"):
        parts.append(f"leverage={fund['leverage_amount']}")
    if fund.get("uses_derivatives"):
        parts.append(f"derivatives=true")
    if fund.get("market_status") and fund["market_status"] != "ACTV":
        parts.append(f"market_status={fund['market_status']}")
    return "; ".join(parts)


def _call_claude(funds: list[dict]) -> tuple[list[dict], CallUsage]:
    """Make one API call classifying a batch of funds. Returns (results, usage)."""
    import anthropic

    client = anthropic.Anthropic()
    system_prompt = _build_system_prompt()

    fund_lines = [f"{i+1}. {_format_fund_for_prompt(f)}" for i, f in enumerate(funds)]
    user_message = (
        f"Classify these {len(funds)} funds. Output one JSON object per line.\n\n"
        + "\n".join(fund_lines)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract usage from Anthropic response (API varies by SDK version)
    u = getattr(response, "usage", None)
    input_tokens = getattr(u, "input_tokens", 0) if u else 0
    cached = getattr(u, "cache_read_input_tokens", 0) if u else 0
    output_tokens = getattr(u, "output_tokens", 0) if u else 0
    usage = CallUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        cost_usd=0.0,
    )
    usage.cost_usd = _compute_cost(usage)

    # Parse response
    text = response.content[0].text if response.content else ""
    results = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            results.append(obj)
        except json.JSONDecodeError:
            continue

    return results, usage


def classify_and_save(
    limit: int = 100,
    new_only: bool = False,
    include_inactive: bool = True,
    dry_run: bool = False,
) -> dict:
    """Main entry point: classify funds and write to fund_taxonomy.

    Args:
        limit: Max funds to process this run
        new_only: Skip funds already in fund_taxonomy
        include_inactive: Include non-ACTV funds (liquidated, delisted)
        dry_run: Don't call API, just estimate cost

    Returns:
        dict with stats: funds_seen, classified, skipped, api_calls,
                         cost_usd, budget_remaining
    """
    from webapp.database import init_db, SessionLocal
    from webapp.models import FundTaxonomy
    from webapp.services.data_engine import build_master_data

    init_db()
    db = SessionLocal()

    # Budget check
    budget = _reset_if_new_month(_load_budget())
    remaining = budget["monthly_cap"] - budget["spent_this_month"]
    if remaining <= 0 and not dry_run:
        return {
            "error": "monthly budget exhausted",
            "spent_this_month": budget["spent_this_month"],
            "monthly_cap": budget["monthly_cap"],
        }

    # Load Bloomberg universe
    master = build_master_data()
    if master is None or len(master) == 0:
        return {"error": "no Bloomberg data"}

    # Filter to ETFs/ETNs
    ft_col = "fund_type" if "fund_type" in master.columns else None
    if ft_col:
        scope = master[master[ft_col].isin(["ETF", "ETN"])]
    else:
        scope = master

    # Status filter
    if not include_inactive and "market_status" in scope.columns:
        scope = scope[scope["market_status"] == "ACTV"]

    # Dedupe by ticker
    scope = scope.drop_duplicates(subset=["ticker"], keep="first")

    # Exclude already-classified tickers
    existing_tickers = set()
    if new_only or True:  # always skip already-classified
        existing_tickers = {t for (t,) in db.query(FundTaxonomy.ticker).all()}

    # Build work list
    work = []
    for _, row in scope.iterrows():
        ticker = str(row.get("ticker", "")).strip()
        if not ticker or ticker.lower() == "nan":
            continue
        if ticker in existing_tickers:
            continue
        work.append({
            "ticker": ticker,
            "fund_name": str(row.get("fund_name", "")),
            "issuer": str(row.get("issuer", "")),
            "asset_class_focus": str(row.get("asset_class_focus", "")),
            "underlying_index": str(row.get("underlying_index", "")),
            "outcome_type": str(row.get("outcome_type", "")),
            "is_singlestock": row.get("is_singlestock"),
            "leverage_amount": row.get("leverage_amount"),
            "uses_derivatives": row.get("uses_derivatives"),
            "market_status": str(row.get("market_status", "")),
        })

    funds_seen = len(work)
    work = work[:limit]
    batches = [work[i:i+BATCH_SIZE] for i in range(0, len(work), BATCH_SIZE)]

    # Estimate cost: ~$0.008 per batch based on empirical testing
    estimated_cost = len(batches) * 0.008

    if dry_run:
        db.close()
        return {
            "dry_run": True,
            "funds_seen": funds_seen,
            "would_process": len(work),
            "api_calls_needed": len(batches),
            "estimated_cost_usd": round(estimated_cost, 2),
            "budget_remaining": round(remaining, 2),
            "monthly_cap": budget["monthly_cap"],
            "already_classified": len(existing_tickers),
        }

    if not is_available():
        db.close()
        return {
            "error": "AI unavailable (missing ANTHROPIC_API_KEY or anthropic SDK)",
            "funds_seen": funds_seen,
        }

    # Real API calls
    classified = 0
    api_calls = 0
    total_cost = 0.0

    for batch in batches:
        # Per-call budget check
        if total_cost + 0.05 > remaining:  # safety margin
            log.warning("Stopping — budget would be exceeded")
            break

        try:
            results, usage = _call_claude(batch)
            api_calls += 1
            total_cost += usage.cost_usd
            _log_usage(usage, len(batch), len(results))
        except Exception as e:
            log.error("Claude API call failed: %s", e)
            continue

        # Build lookup so we can join results to source rows
        source_by_ticker = {f["ticker"]: f for f in batch}

        for obj in results:
            t = str(obj.get("ticker", "")).strip()
            src = source_by_ticker.get(t)
            if not src:
                continue

            primary = obj.get("primary_category", "Other / Unclassified")
            # Fall back to Other if the model returned something bogus
            from tools.rules_editor.taxonomy import is_valid_primary
            if not is_valid_primary(primary):
                primary = "Other / Unclassified"

            row = FundTaxonomy(
                ticker=t,
                fund_name=src.get("fund_name") or None,
                issuer=src.get("issuer") or None,
                market_status=src.get("market_status") or None,
                primary_category=primary,
                sub_category=obj.get("sub_category"),
                asset_class=obj.get("asset_class"),
                region=obj.get("region"),
                sector=obj.get("sector"),
                style_tags=json.dumps(obj.get("style_tags") or []),
                factor_tags=json.dumps(obj.get("factor_tags") or []),
                thematic_tags=json.dumps(obj.get("thematic_tags") or []),
                source="ai",
                model=MODEL,
                confidence=obj.get("confidence", "MEDIUM"),
                rationale=obj.get("rationale"),
            )
            db.add(row)
            classified += 1

        db.commit()

    # Update budget
    budget["spent_this_month"] += total_cost
    budget["total_calls"] += api_calls
    _save_budget(budget)

    db.close()

    return {
        "funds_seen": funds_seen,
        "classified": classified,
        "api_calls": api_calls,
        "cost_usd": round(total_cost, 4),
        "budget_spent_this_month": round(budget["spent_this_month"], 4),
        "budget_remaining": round(budget["monthly_cap"] - budget["spent_this_month"], 2),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Universal AI fund classifier")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--new-only", action="store_true", help="(default) skip already-classified")
    parser.add_argument("--include-inactive", action="store_true", default=True)
    parser.add_argument("--active-only", action="store_true", help="skip non-ACTV funds")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = classify_and_save(
        limit=args.limit,
        new_only=args.new_only,
        include_inactive=not args.active_only,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
