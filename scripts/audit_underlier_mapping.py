"""Audit L&I underlier mappings in mkt_master_data.

Pulls all ACTV L&I products, derives the expected underlier from the fund
name via regex + alias expansion, then compares against the current
map_li_underlier value stored in the DB.

Output: docs/underlier_audit_2026-05-05.csv

Columns:
    ticker        – Bloomberg ticker (e.g. 'DJTU US')
    fund_name     – Full fund name as loaded from Bloomberg
    current_map   – Current value of map_li_underlier (may be None)
    expected      – Ticker extracted / resolved from the fund name
    status        – OK | MISMATCH | UNCLEAR
    confidence    – HIGH | MEDIUM | LOW

Status semantics:
    OK        – current_map matches expected after normalisation.  Normalisation
                strips the trailing ' US' suffix before comparing so that
                Bloomberg format variants ('AAOI' vs 'AAOI US') are not
                reported as mismatches.  Only cases where the base ticker
                itself differs are flagged MISMATCH.
    MISMATCH  – regex confidently extracted a ticker and the base ticker
                differs from current (e.g. 'DJTU UA' vs expected 'DJT US',
                or 'CECL US' vs expected 'CRCL US').
    UNCLEAR   – regex could not extract a clear single-stock ticker (multi-
                underlier funds, broad index funds, MicroSectors ETNs, etc.)

Confidence semantics:
    HIGH      – single stock / crypto / commodity ticker found verbatim in
                the fund name (e.g. 'AVGO', 'MSTR')
    MEDIUM    – word resolved via the canonical alias dict
                (e.g. APPLE → AAPL US, ETHER → XETUSD Curncy)
    LOW       – extracted but uncertain (index / multi-name match)

This script is READ-ONLY — it never modifies the database.
Run apply_underlier_overrides.py to persist high-confidence fixes.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUTPUT_CSV = PROJECT_ROOT / "docs" / "underlier_audit_2026-05-05.csv"

# ---------------------------------------------------------------------------
# Alias table — full-word names that appear in fund names but whose canonical
# Bloomberg underlier ticker cannot be inferred from the word alone.
# Key: uppercase word as it appears in the fund name.
# Value: (canonical map_li_underlier string, confidence).
# ---------------------------------------------------------------------------
WORD_ALIASES: dict[str, tuple[str, str]] = {
    # Crypto — all Bloomberg Curncy format
    "BITCOIN":    ("XBTUSD Curncy", "MEDIUM"),
    "ETHER":      ("XETUSD Curncy", "MEDIUM"),
    "ETHEREUM":   ("XETUSD Curncy", "MEDIUM"),
    "SOLANA":     ("XSOUSD Curncy", "MEDIUM"),
    "SOL":        ("XSOUSD Curncy", "MEDIUM"),
    "XRP":        ("XRPUSD Curncy", "MEDIUM"),
    "DOGECOIN":   ("XDGUSD Curncy", "MEDIUM"),
    # Precious metals (LONG direction uses XAU/XAG; inverse / SHORT products
    # may use different Bloomberg tickers — we only alias the common LONG form
    # since SHORT funds are more likely to be UNCLEAR anyway)
    "GOLD":       ("XAU Curncy",    "MEDIUM"),
    "SILVER":     ("XAG Curncy",    "MEDIUM"),
    # Company full-names used by T-REX and others
    "APPLE":      ("AAPL US",       "MEDIUM"),
    "ALPHABET":   ("GOOG US",       "MEDIUM"),
    "MICROSOFT":  ("MSFT US",       "MEDIUM"),
    "AMAZON":     ("AMZN US",       "MEDIUM"),
    "NVIDIA":     ("NVDA US",       "MEDIUM"),
    "TESLA":      ("TSLA US",       "MEDIUM"),
    "META":       ("META US",       "MEDIUM"),
    "NETFLIX":    ("NFLX US",       "MEDIUM"),
}

# ---------------------------------------------------------------------------
# Ticker-string normalisation
# ---------------------------------------------------------------------------

def _base_ticker(value: str | None) -> str:
    """Return the base ticker without exchange suffix for loose comparison.

    Strips trailing ' US', ' UA', ' UW', etc. and common Bloomberg type
    suffixes (Curncy, Comdty, Equity) so that 'AAOI US' and 'AAOI' both
    normalise to 'AAOI', avoiding false-positive MISMATCH on format variants.

    Special handling for Berkshire: 'BRK/B US' → 'BRKB'.
    """
    if not value:
        return ""
    v = value.strip()
    # Berkshire special case
    v = v.replace("BRK/B", "BRKB")
    # Strip Bloomberg type suffix (Curncy, Comdty, Equity, Index)
    v = re.sub(r"\s+(Curncy|Comdty|Equity|Index)$", "", v, flags=re.IGNORECASE)
    # Strip trailing exchange code: ' US', ' UA', ' UW', ' LN', etc.
    v = re.sub(r"\s+[A-Z]{2,3}$", "", v)
    return v.upper()


def _tickers_equivalent(a: str | None, b: str | None) -> bool:
    """True when both tickers refer to the same underlier (loose match).

    Compares base tickers only — so 'AAOI US' == 'AAOI'.
    Does NOT normalise Bloomberg currency/commodity codes: 'XETUSD Curncy'
    and 'XETUSD' are considered different because they carry different
    Bloomberg type information.  For crypto underliers the DB typically
    stores the full 'XETUSD Curncy' form, and our aliases produce the
    same, so they compare equal.
    """
    if not a and not b:
        return True
    if not a or not b:
        return False
    # Direct match first
    if a.strip().upper() == b.strip().upper():
        return True
    # Base-ticker match (strips ' US', ' UA', etc.)
    return _base_ticker(a) == _base_ticker(b)


# ---------------------------------------------------------------------------
# Regex patterns — evaluated in order; first match wins.
# ---------------------------------------------------------------------------

def _build_patterns() -> list[tuple[re.Pattern, callable, str]]:
    patterns: list[tuple[re.Pattern, callable, str]] = []

    # T-REX: "T-REX 2X LONG NVDA DAILY TARGET ETF"
    #         "T-REX 2X INVERSE TESLA DAILY TARGET ETF"
    trex = re.compile(
        r"T-REX\s+\d+(?:\.\d+)?X\s+(?:LONG|INVERSE)\s+([A-Z0-9]+(?:\s+[A-Z0-9]+)?)\s+DAILY",
        re.IGNORECASE,
    )
    patterns.append((trex, lambda m: m.group(1).strip(), "HIGH"))

    # GraniteShares / Leverage Shares / Defiance / TRADR / 21Shares
    # "GRANITESHARES 2X LONG AVGO DAILY ETF"
    # "LEVERAGE SHARES 2X LONG AAL DAILY ETF"
    # "DEFIANCE DAILY TARGET 2X LONG ANET ETF"
    # "TRADR 2X LONG AAOI DAILY ETF"
    # "21SHARES 2X LONG DOGECOIN ETF"
    explicit = re.compile(
        r"""
        (?:GRANITESHARES|LEVERAGE\s+SHARES|DEFIANCE|TRADR|21SHARES)
        .*?
        \d+(?:\.\d+)?X\s+(?:LONG|SHORT|INVERSE)
        \s+([A-Z0-9]+)
        \s+DAILY
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    patterns.append((explicit, lambda m: m.group(1).strip(), "HIGH"))

    # Direxion "DAILY <TICKER> BULL/BEAR": single short ticker only
    # "DIREXION DAILY AAPL BEAR 1X ETF"
    # Skip multi-word: "DIREXION DAILY AI AND BIG DATA BEAR"
    direxion = re.compile(
        r"DIREXION\s+DAILY\s+([A-Z]{2,6})\s+(?:BULL|BEAR)\s+\d",
        re.IGNORECASE,
    )
    patterns.append((direxion, lambda m: m.group(1).strip(), "HIGH"))

    # ProShares "ULTRA <TICKER>" / "ULTRASHORT <TICKER>" single-name form
    # "PROSHARES ULTRA NVDA"  / "PROSHARES ULTRA TSLA"
    # Matches only when the token is a short ticker (1-8 chars), not a phrase
    proshares_single = re.compile(
        r"PROSHARES\s+ULTRA(?:SHORT|PRO)?\s+([A-Z]{2,8})(?:\s+ETF|\s*$)",
        re.IGNORECASE,
    )
    patterns.append((proshares_single, lambda m: m.group(1).strip(), "HIGH"))

    return patterns


_PATTERNS = _build_patterns()

# Words that indicate a multi-stock index underlier; if the regex extracts
# one of these we demote to UNCLEAR rather than claiming a single-stock match.
_KNOWN_INDEX_WORDS = frozenset({
    "QQQ", "SPY", "IWM", "DIA",
    "MIDCAP400", "RUSSELL2000", "NASDAQ", "DOW30", "SMALLCAP600",
    "MAGNIFICENT", "SEVEN", "HEALTHCARE", "FINANCIALS", "ENERGY",
    "MATERIALS", "UTILITIES", "TECHNOLOGY", "SEMICONDUCTORS",
    "INDUSTRIALS", "BIOTECHNOLOGY", "CYBERSECURITY", "CLOUD",
    "COMPUTING", "TREASURY", "MINERS", "JUNIOR",
    "MSCI", "FTSE", "EAFE", "EMERGING", "BRAZIL", "JAPAN", "CHINA",
    "EUROPE", "YEN", "EURO", "CRUDE", "OIL", "NATURAL", "GAS",
    "COMMODITY", "AGRICULTURE", "METALS", "COPPER", "DRONE",
    "AERIAL", "AUTOMATION", "QUANTUM", "AI", "BIG", "DATA",
    "INNOVATION", "PURE", "REGIONAL", "BANKS", "VIX",
    "RETAIL",  # RETL US is a sector ETF
})


def _resolve(word: str) -> tuple[str, str]:
    """Return (resolved_ticker, confidence) for a word extracted by regex.

    Resolution order:
    1. Alias dict (MEDIUM confidence)
    2. Known index word (LOW — demote to UNCLEAR in caller)
    3. Looks like a ticker → append ' US' (HIGH)
    """
    upper = word.strip().upper()

    if upper in WORD_ALIASES:
        return WORD_ALIASES[upper]

    if upper in _KNOWN_INDEX_WORDS:
        return (word, "LOW")

    # Plain ticker: 1-6 uppercase letters, optionally ending in a digit
    if re.fullmatch(r"[A-Z]{1,6}[0-9]?", upper):
        return (upper + " US", "HIGH")

    return (word, "HIGH")


def _extract_expected(fund_name: str) -> tuple[str | None, str]:
    """Return (expected_ticker, confidence) or (None, '') if no pattern matched."""
    fn_upper = fund_name.upper()
    for pattern, extractor_fn, _base_conf in _PATTERNS:
        m = pattern.search(fn_upper)
        if m:
            raw = extractor_fn(m)
            resolved, conf = _resolve(raw)
            return resolved, conf
    return None, ""


def _classify(
    current: str | None,
    expected: str | None,
    confidence: str,
) -> str:
    """Assign status: OK, MISMATCH, or UNCLEAR."""
    if expected is None or confidence in ("", "LOW"):
        return "UNCLEAR"
    if _tickers_equivalent(current, expected):
        return "OK"
    return "MISMATCH"


def audit(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT ticker, fund_name, map_li_underlier
        FROM mkt_master_data
        WHERE primary_category = 'LI'
          AND market_status    = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""
        expected, confidence = _extract_expected(fund_name)
        status = _classify(current_map, expected, confidence)
        results.append({
            "ticker":       ticker,
            "fund_name":    fund_name,
            "current_map":  current_map or "",
            "expected":     expected or "",
            "status":       status,
            "confidence":   confidence or "LOW",
        })
    return results


def write_csv(results: list[dict]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ticker", "fund_name", "current_map", "expected", "status", "confidence"]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Auditing L&I underlier mappings in {DB_PATH}")

    results = audit(con)
    con.close()

    total    = len(results)
    ok       = sum(1 for r in results if r["status"] == "OK")
    mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
    unclear  = sum(1 for r in results if r["status"] == "UNCLEAR")

    mismatch_high   = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "HIGH")
    mismatch_medium = sum(1 for r in results if r["status"] == "MISMATCH" and r["confidence"] == "MEDIUM")

    write_csv(results)

    print()
    print(f"Total L&I ACTV products : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print("MISMATCH details:")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:12s}  "
                f"current={r['current_map'] or '(null)':20s}  "
                f"expected={r['expected']}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
