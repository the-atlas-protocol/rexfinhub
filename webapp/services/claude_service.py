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

MODEL = "claude-sonnet-4-6"
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


def resolve_fact(
    kind: str,
    ticker: str,
    fund_name: str,
    fund_description: str = "",
    allowed_values: list[str] | None = None,
    use_web: bool = False,
    model: str = MODEL,
    citation_sink: dict | None = None,
) -> tuple[str, str, str] | None:
    """Resolve ONE fact about ONE fund (powers market.resolve rungs 2 & 3).

    rung 2 (use_web=False): place the fact from the Bloomberg `fund_description`
    (the issuer's own words) — used when the deterministic rules miss.
    rung 3 (use_web=True): enable the Anthropic web_search server tool so the model
    can look up names even the description can't place (new underlier, obscure
    strategy, unknown sponsor). The first source URL is written to citation_sink
    under "_last_citation" for audit.

    Returns (value, confidence, reason) or None on any failure (so the caller can
    fall through the cascade). confidence is HIGH | MEDIUM | LOW; value is "" when
    the model genuinely cannot decide.
    """
    api_key = _load_api_key()
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    constraint = ""
    if allowed_values:
        constraint = (
            "\nThe answer MUST be exactly one of these allowed values: "
            + ", ".join(allowed_values)
            + '. If none fit, return value "".'
        )
    # For category facts, inject the authoritative per-category MEANING from the
    # report-rules contract so the cascade resolves toward intent (e.g. "bonds are not
    # income"), not a bare label — the same source the batch classifier reads. Empty
    # for other fact kinds or when the contract is unavailable.
    intent = ""
    if kind in ("etp_category", "category"):
        try:
            from market.contracts import category_intent_text
            intent = category_intent_text()
        except Exception:  # noqa: BLE001 — intent is best-effort, never fatal
            intent = ""
    system = (
        "You are a data-quality resolver for REX Financial's ETP database. "
        f"Determine the '{kind}' for the fund below using the evidence provided"
        + (" and a web search where the evidence is insufficient." if use_web else ".")
        + constraint
        + intent
        + '\nReturn ONLY valid JSON, no preamble: '
        '{"value":"<answer or empty string>","confidence":"HIGH|MEDIUM|LOW",'
        '"reason":"<=15 words"}. '
        "Use HIGH only when the evidence is explicit; MEDIUM when inferred; "
        "LOW when guessing — a LOW answer will be sent to a human, so prefer LOW "
        "over a confident guess."
    )
    user = f"ticker: {ticker}\nfund_name: {fund_name}\n"
    if fund_description:
        user += f"bloomberg_description: {fund_description}\n"

    client = anthropic.Anthropic(api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 600,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if use_web:
        # Anthropic web_search server tool. Capped low to bound cost/latency.
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        }]

    try:
        resp = client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001 — any API error -> decline, cascade falls through
        log.warning("resolve_fact API call failed: %s", e)
        return None

    import json
    import re

    text_parts: list[str] = []
    first_citation = ""
    for block in (resp.content or []):
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
            # web_search results attach citations to text blocks
            for cit in (getattr(block, "citations", None) or []):
                url = getattr(cit, "url", None)
                if url and not first_citation:
                    first_citation = url
    if citation_sink is not None and first_citation:
        citation_sink["_last_citation"] = first_citation

    blob = "\n".join(text_parts)
    match = re.search(r"\{.*\}", blob, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    value = str(parsed.get("value", "") or "").strip()
    conf = str(parsed.get("confidence", "LOW") or "LOW").strip().upper()
    reason = str(parsed.get("reason", "") or "").strip()
    if conf not in ("HIGH", "MEDIUM", "LOW"):
        conf = "LOW"
    return value, conf, reason


def review_report_facts(facts: dict) -> list[dict]:
    """AI semantic review (preflight Stage C). Given the assembled report facts (REX
    lineup count, issuer_display sample, ...), return a list of defects:
        [{"issue": "...", "severity": "HIGH|MEDIUM|LOW"}, ...]
    A HIGH defect blocks the build. Returns [] when the data looks correct. Raises on
    a hard API failure so the caller can decide (it treats failure as non-blocking)."""
    api_key = _load_api_key()
    if not api_key:
        return []
    import anthropic
    import json
    import re

    client = anthropic.Anthropic(api_key=api_key)
    system = (
        "You are a data-quality reviewer for REX Financial's ETP reports. You are given "
        "FACTS extracted from the freshly-built reports. Find SEMANTIC defects a string "
        "check would miss:\n"
        "  - an issuer_display that is a legal/trust entity (e.g. 'Tidal Trust II', "
        "'... ETF Trust', '... Series Trust') rather than a real brand;\n"
        "  - a KPI/count that is implausible for the actual REX product lineup;\n"
        "  - any value that does not make sense in context.\n"
        'Return ONLY valid JSON: {"defects":[{"issue":"<=20 words","severity":"HIGH|MEDIUM|LOW"}]}. '
        "Empty list if everything looks correct. Use HIGH only for a clear, shippable error. "
        "Legitimate brands containing 'Trust' (First Trust, Northern Trust) are NOT defects."
    )
    try:
        resp = client.messages.create(
            model=SELECTOR_MODEL, max_tokens=1200, system=system,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)[:20000]}],
        )
    except Exception as e:
        raise RuntimeError(f"review_report_facts API call failed: {e}") from e
    text = resp.content[0].text if resp.content else ""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group()).get("defects", []) or []
    except json.JSONDecodeError:
        return []


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


# --- Top Filings of the Day helpers ---

SELECTOR_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL = "claude-sonnet-4-6"


def select_top_filings(candidates: list[dict]) -> tuple[list[dict], dict]:
    """Haiku-powered selector: rank today's new fund filings by interestingness.

    Returns (picks, usage_dict). picks is a list of dicts with keys
    accession, score, reason (already sorted desc by score).
    """
    import json
    import re
    import anthropic

    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You are screening today's new SEC fund filings for REX Financial.\n"
        "From the candidates below, pick up to 3 that would most interest an ETP issuer executive.\n\n"
        "FOR EACH CANDIDATE, THINK ABOUT:\n"
        "  - What IS the underlying? A well-known stock (AAPL, NVDA), an index (SPX, QQQ), a pre-IPO\n"
        "    private company (OpenAI, Anthropic, Lambda, Scale AI), a commodity, a thematic basket, or\n"
        "    a derivative construct? Fund names often use [brackets] or parentheses for the underlying —\n"
        "    but brackets alone don't make a fund interesting. '2x Long [Apple]' is boring. '2x Long\n"
        "    [Scale AI]' is potentially interesting because Scale AI is pre-IPO and rarely accessible.\n"
        "    If the underlying is unclear from the name (e.g., [Lambda], [Croq]), treat the unknown\n"
        "    as a reason for modest curiosity, not high confidence.\n"
        "  - Is the STRUCTURE novel (buffer, autocall, worst-of, accelerator, defined outcome) or\n"
        "    standard (plain 2x daily reset, vanilla index tracker)?\n"
        "  - Is this part of a SUITE from one trust filed the same day? If yes, the suite is the\n"
        "    story — pick ONE accession as entry point but call out that it's a suite in the reason.\n\n"
        "INTERESTING = novel structure, novel/private underlying, new issuer entering a segment,\n"
        "or suite-level themes worth naming.\n"
        "BORING = plain-vanilla index trackers, routine amendments, one-off 2x leveraged products\n"
        "on household-name large caps.\n\n"
        'Return ONLY valid JSON: {"picks":[{"accession":"...","score":0.0-1.0,"reason":"<=20 words"}, ...]}\n'
        "Sorted by score desc. Fewer than 3 OK if the field is thin. No preamble, no markdown."
    )
    user_payload = {
        "candidates": [
            {
                "accession": c["accession"],
                "trust": c["trust_name"],
                "form": c["form"],
                "fund_names": c["fund_names"][:8],
                "is_rex": c["is_rex"],
            }
            for c in candidates
        ]
    }
    resp = client.messages.create(
        model=SELECTOR_MODEL,
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": json.dumps(user_payload, indent=2)}],
    )
    text = resp.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Selector non-JSON: {text[:200]}")
    picks = json.loads(match.group())["picks"]
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "model": SELECTOR_MODEL,
    }
    return picks, usage


def analyze_top_filing(candidate: dict, objective: str, strategy: str) -> tuple[dict, dict]:
    """Sonnet writer: structured analysis of a single new fund filing.

    Returns (structured_output, usage_dict). Output dict has keys:
    filing_title, strategy_type, underlying, structure, portfolio_holding,
    distribution, narrative.
    """
    import json
    import re
    import anthropic

    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You are an ETP filing analyst at REX Financial. Analyze the prospectus excerpts below.\n\n"
        "Return ONLY valid JSON, no preamble, no markdown. Exact keys:\n"
        '  "filing_title": human-friendly title for this block. MUST fit on ONE LINE '
        "(<=55 characters). If the filing contains MULTIPLE related funds, compress — "
        'e.g., "GraniteShares Pre-IPO AI / Space 2x Suite" rather than listing every name. '
        'For long/short pairs: "Fund Name 2x Long/Short Pair". '
        'For country suites: "Issuer Country Suite" plus up to 3 country names. '
        'If single fund, use its name verbatim as long as it is <=55 chars.\n'
        '  "strategy_type": short label, <=6 words\n'
        '  "underlying": what the strategy is on (specific names/indices/baskets)\n'
        '  "structure": mechanics of payoff / exposure\n'
        '  "portfolio_holding": what the fund actually holds (swap / direct securities / options / etc.)\n'
        '  "distribution": cadence + source, or "None"\n'
        '  "narrative": ONE paragraph, <=110 words, blending strategy summary with a '
        "LIGHT observational note on positioning. Do NOT attempt full competitive landscape analysis. "
        "Note only structural novelty visible in the filing.\n\n"
        "Do not invent facts not present in the excerpts. If a field cannot be determined from the text, "
        'use "unclear from filing".'
    )
    fund_list = (
        ", ".join(candidate["fund_names"][:10])
        if candidate.get("fund_names") else candidate.get("trust_name", "")
    )
    user = (
        f"FUNDS IN THIS FILING: {fund_list}\n"
        f"TRUST: {candidate.get('trust_name', '')}\nFORM: {candidate.get('form', '')}\n\n"
        f"--- INVESTMENT OBJECTIVE ---\n{objective[:8000]}\n\n"
        f"--- PRINCIPAL INVESTMENT STRATEGIES ---\n{strategy[:40000]}"
    )
    resp = client.messages.create(
        model=WRITER_MODEL, max_tokens=1500, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Writer non-JSON: {text[:200]}")
    parsed = json.loads(match.group())
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "model": WRITER_MODEL,
    }
    return parsed, usage
