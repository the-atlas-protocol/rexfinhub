from __future__ import annotations

# Default/fallback user agent (you should override in the notebook CONFIG cell)
USER_AGENT_DEFAULT = "REX-ETP-FilingTracker/1.0 (contact: set USER_AGENT)"

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
    "485BXT":  "header_only",
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
