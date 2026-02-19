"""
Claude AI Prospectus Analysis Service

On-demand analysis of SEC filings using the Anthropic Claude API.
Supports multiple analysis types: summary, competitive intel, change detection, risk review.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"
MAX_INPUT_TOKENS = 25000  # Truncate filing text beyond this (rough char estimate)
MAX_OUTPUT_TOKENS = 1500  # Keep responses concise to lower cost

ANALYSIS_TYPES = {
    "summary": {
        "label": "Summary",
        "description": "Key facts: fund type, fees, strategy, dates",
        "prompt": (
            "You are an SEC ETP filing analyst at REX Financial. "
            "Extract ONLY these facts from the prospectus. Use short bullet points. "
            "Skip any section where data is not in the filing.\n\n"
            "1. **Fund(s)**: Name, ticker, investment objective (one line each)\n"
            "2. **Fees**: Expense ratio, management fee, other key costs\n"
            "3. **Strategy**: Core investment approach in 1-2 sentences\n"
            "4. **Dates**: Effective date, amendment date, any status changes\n"
            "5. **Notable**: Only if something unusual stands out\n\n"
            "MAX 300 words. No preamble. No disclaimers. Facts only."
        ),
    },
    "competitive": {
        "label": "Competitive Intel",
        "description": "Market positioning, fees vs peers, threat level",
        "prompt": (
            "You are a competitive intelligence analyst at REX Financial (an ETP issuer). "
            "Analyze this competitor prospectus for product development leadership.\n\n"
            "1. **Segment**: What market/asset class does this target?\n"
            "2. **Fees vs Peers**: Are fees above/below/at category average? Cite numbers.\n"
            "3. **Edge**: What differentiates this from existing ETFs? Be specific.\n"
            "4. **Threat**: Low/Medium/High to REX product lineup and why (one sentence)\n\n"
            "MAX 250 words. Be direct and actionable. No filler."
        ),
    },
    "changes": {
        "label": "Change Detection",
        "description": "What changed in this amendment vs prior version",
        "prompt": (
            "You are a legal analyst at REX Financial reviewing an SEC filing amendment. "
            "Identify ONLY what changed. Do NOT summarize the whole fund.\n\n"
            "1. **Filing Type**: New / Amendment / Supplement\n"
            "2. **Changes Made**: List each specific change (fee changes, name changes, strategy changes, etc.)\n"
            "3. **Effective Date**: When do changes take effect?\n"
            "4. **Impact**: One sentence on practical significance\n\n"
            "If this is a new filing (not an amendment), say so and list the key terms instead.\n"
            "MAX 200 words. Only facts from the document."
        ),
    },
    "risk": {
        "label": "Risk Review",
        "description": "Risk factors, leverage, derivatives, rating",
        "prompt": (
            "You are a risk analyst at REX Financial reviewing a prospectus.\n\n"
            "1. **Top 5 Risks**: The most significant risk factors, one line each\n"
            "2. **Leverage/Derivatives**: Yes/No. If yes, what kind and how much?\n"
            "3. **Liquidity**: Any redemption restrictions or liquidity concerns?\n"
            "4. **Risk Rating**: Conservative / Moderate / Aggressive / Speculative\n\n"
            "MAX 200 words. Bullet points only."
        ),
    },
}


def _load_api_key() -> str:
    """Load Anthropic API key from .env or environment."""
    env_file = Path(__file__).resolve().parent.parent.parent / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ANTHROPIC_API_KEY", "")


def is_configured() -> bool:
    """Check if Claude API key is available."""
    key = _load_api_key()
    return bool(key and key.startswith("sk-ant-"))


def analyze_filing(
    filing_text: str,
    analysis_type: str,
    fund_name: str = "",
    trust_name: str = "",
) -> dict[str, Any]:
    """Analyze a filing using Claude API.

    Args:
        filing_text: The raw text content of the filing
        analysis_type: One of: summary, competitive, changes, risk
        fund_name: Optional fund name for context
        trust_name: Optional trust name for context

    Returns:
        Dict with: result_text, model_used, input_tokens, output_tokens, analysis_type
    """
    api_key = _load_api_key()
    if not api_key:
        return {"error": "Anthropic API key not configured"}

    if analysis_type not in ANALYSIS_TYPES:
        return {"error": f"Unknown analysis type: {analysis_type}"}

    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not installed. Run: pip install anthropic"}

    type_config = ANALYSIS_TYPES[analysis_type]

    # Build context prefix
    context = ""
    if trust_name:
        context += f"Trust: {trust_name}\n"
    if fund_name:
        context += f"Fund: {fund_name}\n"

    # Truncate filing text if too long
    if len(filing_text) > MAX_INPUT_TOKENS * 4:
        filing_text = filing_text[: MAX_INPUT_TOKENS * 4] + "\n\n[... truncated for length ...]"

    user_message = f"{context}\n--- FILING TEXT ---\n{filing_text}"

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=type_config["prompt"],
            messages=[{"role": "user", "content": user_message}],
        )

        result_text = response.content[0].text if response.content else ""

        return {
            "result_text": result_text,
            "model_used": MODEL,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "analysis_type": analysis_type,
        }

    except anthropic.AuthenticationError:
        log.error("Claude API authentication failed - invalid API key")
        return {"error": "API key is invalid. Please check the ANTHROPIC_API_KEY in your environment settings."}

    except anthropic.BadRequestError as e:
        log.error("Claude API bad request: %s", e)
        if "credit balance" in str(e).lower():
            return {"error": "Anthropic account issue: credit balance too low or billing not active. Top up at console.anthropic.com."}
        return {"error": f"Claude API request error: {e}"}

    except anthropic.RateLimitError:
        log.error("Claude API rate limit exceeded")
        return {"error": "API rate limit exceeded. Please try again in a few minutes."}

    except anthropic.APIError as e:
        log.error("Claude API error: %s", e)
        return {"error": f"Claude API error: {e}"}


def estimate_cost(text_length: int) -> dict[str, float]:
    """Estimate the cost of analyzing a filing.

    Returns dict with input_tokens_est, output_tokens_est, cost_est_usd.
    """
    # Rough estimate: 1 token ~= 4 characters
    input_tokens = min(text_length // 4, MAX_INPUT_TOKENS) + 200  # prompt overhead
    output_tokens = MAX_OUTPUT_TOKENS

    # Sonnet pricing: $3/M input, $15/M output
    cost = (input_tokens * 3 / 1_000_000) + (output_tokens * 15 / 1_000_000)

    return {
        "input_tokens_est": input_tokens,
        "output_tokens_est": output_tokens,
        "cost_est_usd": round(cost, 4),
    }
