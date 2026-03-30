from __future__ import annotations

import json
import logging

from etp_tracker.sec_client import SECClient
from webapp.models import TrustCandidate

log = logging.getLogger(__name__)


def enrich_candidate(client: SECClient, candidate: TrustCandidate) -> dict | None:
    try:
        data = client.load_submissions_json(candidate.cik)
    except Exception as e:
        log.warning("Failed to fetch submissions for CIK %s: %s", candidate.cik, e)
        return None

    entity_type = data.get("entityType", "")
    sic = data.get("sic", "")
    name = data.get("name", "")
    recent = data.get("filings", {}).get("recent", {})
    forms = list(set(recent.get("form", [])))

    # Update candidate name from SEC if we only had "Unknown"
    if name and (not candidate.company_name or candidate.company_name == "Unknown"):
        candidate.company_name = name

    score = score_etf_trust_likelihood(entity_type, sic, forms, name or candidate.company_name)

    return {
        "entity_type": entity_type,
        "sic_code": sic,
        "name": name,
        "recent_forms": forms,
        "etf_trust_score": score,
    }


def score_etf_trust_likelihood(
    entity_type: str,
    sic_code: str,
    recent_forms: list[str],
    company_name: str,
) -> float:
    score = 0.0

    # Entity type signals
    et_lower = entity_type.lower() if entity_type else ""
    if "investment company" in et_lower:
        score += 0.35
    elif et_lower in ("other",) and sic_code in ("6221", "6199"):
        # Commodity trusts (crypto, gold) often have entityType=other + SIC 6221
        score += 0.20

    # SIC codes for investment vehicles
    if sic_code == "6726":  # Investment trusts (NEC)
        score += 0.25
    elif sic_code == "6221":  # Commodity contracts dealers
        score += 0.15
    elif sic_code in ("6199", "6211"):  # Other finance services
        score += 0.05

    # 40-Act prospectus forms (strongest signal)
    prospectus_forms = {"485BPOS", "485APOS", "485BXT", "N-1A"}
    if any(f in prospectus_forms for f in recent_forms):
        score += 0.20

    # 33-Act forms (S-1/S-3) indicate potential crypto ETF or ETN filer
    sec_act_33_forms = {"S-1", "S-1/A", "S-3", "S-3/A"}
    if any(f in sec_act_33_forms for f in recent_forms):
        score += 0.15

    # Name-based signals
    name_lower = company_name.lower()
    if "trust" in name_lower:
        score += 0.10
    if "etf" in name_lower or "fund" in name_lower:
        score += 0.10

    # Crypto / digital asset keywords (strong signal for 33-Act filers)
    crypto_keywords = ("bitcoin", "ethereum", "crypto", "digital asset",
                       "solana", "xrp", "avalanche", "litecoin", "bnb")
    if any(kw in name_lower for kw in crypto_keywords):
        score += 0.25

    return min(score, 1.0)


def batch_enrich(client: SECClient, db, status: str = "new", max_batch: int = 50) -> int:
    candidates = db.query(TrustCandidate).filter_by(status=status).limit(max_batch).all()
    enriched = 0
    for c in candidates:
        result = enrich_candidate(client, c)
        if result:
            c.etf_trust_score = result["etf_trust_score"]
            enriched += 1
    db.commit()
    return enriched
