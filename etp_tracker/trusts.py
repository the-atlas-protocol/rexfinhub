"""
ETP Trust CIK Registry

Each trust we monitor for filings. CIKs sourced from SEC EDGAR.
"""
from __future__ import annotations

# CIK -> Trust Name (override SEC's name if needed)
# Verified against SEC EDGAR on 2026-02-06
# To add a new trust:
#   1. Search https://efts.sec.gov/LATEST/search-index?q="Trust+Name"&forms=485BPOS
#   2. Get the CIK from the result
#   3. Verify at https://data.sec.gov/submissions/CIK{padded_10_digits}.json
#   4. Add entry below and re-run pipeline
TRUST_CIKS = {
    "2043954": "REX ETF Trust",
    "1424958": "Direxion Shares ETF Trust",
    "1040587": "Direxion Funds",
    "1174610": "ProShares Trust",
    "1689873": "GraniteShares ETF Trust",
    "1884021": "Volatility Shares Trust",
    "1976517": "Roundhill ETF Trust",
    "1924868": "Tidal Trust II",
    "1540305": "ETF Series Solutions",
    "1976322": "Themes ETF Trust",
    "1771146": "ETF Opportunities Trust",  # Tuttle/T-REX products
    "1452937": "Exchange Traded Concepts Trust",
    "1587982": "Investment Managers Series Trust II",
    "1547950": "Exchange Listed Funds Trust",
    "1579881": "Calamos ETF Trust",
    "826732": "Calamos Investment Trust",
}

def get_all_ciks() -> list[str]:
    """Return list of all CIKs to track."""
    return list(TRUST_CIKS.keys())

def get_overrides() -> dict[str, str]:
    """Return CIK -> Trust Name overrides."""
    return TRUST_CIKS.copy()


def add_trust(cik: str, name: str) -> bool:
    """Add a trust to the registry file. Returns True if added, False if already exists."""
    cik = str(cik).strip()
    if cik in TRUST_CIKS:
        return False

    # Update in-memory dict
    TRUST_CIKS[cik] = name

    # Write to the file so it persists across restarts
    import pathlib
    trusts_file = pathlib.Path(__file__)
    content = trusts_file.read_text(encoding="utf-8")

    # Insert new entry before the closing brace of TRUST_CIKS
    new_entry = f'    "{cik}": "{name}",\n'
    content = content.replace("\n}\n", f"\n{new_entry}}}\n", 1)

    trusts_file.write_text(content, encoding="utf-8")
    return True
