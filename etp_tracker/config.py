from __future__ import annotations

# Default/fallback user agent (you should override in the notebook CONFIG cell)
USER_AGENT_DEFAULT = "REX-ETP-FilingTracker/2.0 (relasmar@rexfin.com)"

# SEC endpoints
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{CIK_PADDED}.json"
SEC_ARCHIVES_BASE   = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}"

# Forms we consider 'prospectus-related'
PROSPECTUS_EXACT    = {"EFFECT", "POS AM"}
PROSPECTUS_PREFIXES = ("485A", "485B", "497", "N-1A", "S-1", "S-3")

# Extraction strategy per form type (used by step3)
# "header_only" = parse SGML header only (fast, ~2KB read)
# "full"        = SGML header + body text analysis + optional iXBRL
EXTRACTION_STRATEGIES = {
    # ADR 0014: 485BXT designates a NEW effective date in the body; the SGML header
    # alone misses it. Parse the full body so the delay is honored (latest wins).
    "485BXT":  "full",
    "497J":    "header_only",
    "485BPOS": "full",
    "485APOS": "full",
    "497":     "full",
    "497K":    "full",
    "S-1":     "s1_metadata",
    "S-1/A":   "s1_metadata",
    "S-3":     "s1_metadata",
    "S-3/A":   "s1_metadata",
    "S-1MEF":  "s1_metadata",
    "POS AM":  "s1_metadata",
    "EFFECT":  "s1_metadata",
}
DEFAULT_EXTRACTION_STRATEGY = "full"
