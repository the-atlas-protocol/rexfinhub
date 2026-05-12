"""Audit Covered Call (CC) underlier mappings in mkt_master_data.

Pulls all ACTV CC products with a non-null map_cc_underlier, derives the
expected underlier ticker from the fund name via regex + alias expansion,
then compares against the stored value.

Output: docs/cc_underlier_audit_2026-05-05.csv

Columns:
    ticker        -- Bloomberg ticker (e.g. 'TSLY US')
    fund_name     -- Full fund name as loaded from Bloomberg
    current_map   -- Current value of map_cc_underlier (may be None)
    expected      -- Ticker extracted / resolved from the fund name
    status        -- OK | MISMATCH | UNCLEAR
    confidence    -- HIGH | MEDIUM | LOW

Status semantics:
    OK        -- current_map matches expected after normalisation.  Normalisation
                strips the trailing ' US' / ' UA' suffix before comparing so
                that format variants are not reported as false MISMATCHes.
    MISMATCH  -- regex confidently extracted a ticker and the base ticker
                differs from what is stored.
    UNCLEAR   -- regex could not extract a clear single-stock ticker (broad
                 index funds, multi-underlier, non-single-stock CC, etc.)

Confidence semantics:
    HIGH      -- explicit ticker found verbatim in the fund name
                 (e.g. NVDY -> 'NVDA', TSLY -> 'TSLA')
    MEDIUM    -- word resolved via canonical alias dict
                 (e.g. 'APPLE' -> AAPL US, 'BITCOIN' -> IBIT US)
    LOW       -- extracted but uncertain (index / wrapper ETF underlier)

This script is READ-ONLY -- it never modifies the database.
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
OUTPUT_CSV = PROJECT_ROOT / "docs" / "cc_underlier_audit_2026-05-05.csv"

# ---------------------------------------------------------------------------
# Alias table -- full-word names appearing in CC fund names whose canonical
# map_cc_underlier cannot be inferred from the word alone.
# Key: uppercase word as it appears in the fund name.
# Value: (canonical map_cc_underlier string, confidence).
# ---------------------------------------------------------------------------
WORD_ALIASES: dict[str, tuple[str, str]] = {
    # Company full-names used by KURV and others
    "APPLE":      ("AAPL US",    "MEDIUM"),
    "AMAZON":     ("AMZN US",    "MEDIUM"),
    "MICROSOFT":  ("MSFT US",    "MEDIUM"),
    "GOOGLE":     ("GOOGL US",   "MEDIUM"),
    "NETFLIX":    ("NFLX US",    "MEDIUM"),
    "TESLA":      ("TSLA US",    "MEDIUM"),
    # Crypto spot ETF underliers -- CC funds writing options on BTC ETFs
    # The market standard for single-BTC CC is IBIT US (iShares Bitcoin Trust)
    "BITCOIN":    ("IBIT US",    "MEDIUM"),
    "ETHER":      ("ETHA US",    "MEDIUM"),
}

# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

def _base_ticker(value: str | None) -> str:
    """Return base ticker without exchange / Bloomberg type suffix."""
    if not value:
        return ""
    v = value.strip()
    v = v.replace("BRK/B", "BRKB")
    v = re.sub(r"\s+(Curncy|Comdty|Equity|Index)$", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\s+[A-Z]{2,3}$", "", v)
    return v.upper()


def _tickers_equivalent(a: str | None, b: str | None) -> bool:
    """True when both tickers refer to the same underlier (loose match)."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a.strip().upper() == b.strip().upper():
        return True
    return _base_ticker(a) == _base_ticker(b)


# ---------------------------------------------------------------------------
# Regex patterns -- evaluated in order; first match wins.
# ---------------------------------------------------------------------------

def _build_patterns() -> list[tuple[re.Pattern, callable, str]]:
    patterns: list[tuple[re.Pattern, callable, str]] = []

    # YieldMax: "YIELDMAX <TICKER> OPTION INCOME STRATEGY ETF"
    #           "YIELDMAX <TICKER> PERFORMANCE & INCOME TARGET 25 ETF"
    #           "YIELDMAX SHORT <TICKER> OPTION INCOME STRATEGY ETF"  -> still the underlier
    #           "YIELDMAX INNOVATION OPTION INCOME" -> UNCLEAR (multi-underlier ARK basket)
    yieldmax = re.compile(
        r"YIELDMAX(?:\s+SHORT)?\s+([A-Z]{2,8})\s+(?:OPTION|PERFORMANCE)",
        re.IGNORECASE,
    )
    patterns.append((yieldmax, lambda m: m.group(1).strip(), "HIGH"))

    # YieldMaxTM (trademarked variant): "YIELDMAXTM <TICKER> OPTION INCOME"
    yieldmax_tm = re.compile(
        r"YIELDMAXTM\s+([A-Z]{2,8})\s+(?:OPTION|PERFORMANCE)",
        re.IGNORECASE,
    )
    patterns.append((yieldmax_tm, lambda m: m.group(1).strip(), "HIGH"))

    # GraniteShares YieldBoost: "GRANITESHARES YIELDBOOST <TICKER> ETF"
    # Note: some say "YIELDBOOST <WORD> ETF" where WORD is full company name
    gs_yieldboost = re.compile(
        r"GRANITESHARES\s+YIELDBOOST\s+([A-Z]{2,8})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((gs_yieldboost, lambda m: m.group(1).strip(), "HIGH"))

    # GraniteShares Autocallable: "GRANITESHARES AUTOCALLABLE <TICKER> ETF"
    gs_auto = re.compile(
        r"GRANITESHARES\s+AUTOCALLABLE\s+([A-Z]{2,8})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((gs_auto, lambda m: m.group(1).strip(), "HIGH"))

    # Roundhill WeeklyPay: "ROUNDHILL <TICKER> WEEKLYPAY ETF"
    roundhill = re.compile(
        r"ROUNDHILL\s+([A-Z]{2,8})\s+WEEKLYPAY",
        re.IGNORECASE,
    )
    patterns.append((roundhill, lambda m: m.group(1).strip(), "HIGH"))

    # KURV: "KURV YIELD PREMIUM STRATEGY <FULLNAME> <TICKER> ETF"
    # The ticker appears as the LAST word before ETF/DE
    kurv = re.compile(
        r"KURV\s+YIELD\s+PREMIUM\s+STRATEGY\s+(?:\w+\s+)+?([A-Z]{2,8})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((kurv, lambda m: m.group(1).strip(), "HIGH"))

    # REX <TICKER> GROWTH & INCOME ETF
    rex = re.compile(
        r"REX\s+([A-Z]{2,8})\s+GROWTH\s+&\s+INCOME",
        re.IGNORECASE,
    )
    patterns.append((rex, lambda m: m.group(1).strip(), "HIGH"))

    # Bitwise <TICKER> OPTION INCOME STRATEGY ETF
    bitwise_cc = re.compile(
        r"BITWISE\s+([A-Z]{2,8})\s+OPTION\s+INCOME",
        re.IGNORECASE,
    )
    patterns.append((bitwise_cc, lambda m: m.group(1).strip(), "HIGH"))

    # Defiance: "DEFIANCE <TICKER> OPTION INCOME ETF"
    defiance = re.compile(
        r"DEFIANCE\s+([A-Z]{2,8})\s+OPTION\s+INCOME",
        re.IGNORECASE,
    )
    patterns.append((defiance, lambda m: m.group(1).strip(), "HIGH"))

    # Defiance Leveraged Long + Income: "DEFIANCE LEVERAGED LONG + INCOME <TICKER> ETF"
    defiance_lev = re.compile(
        r"DEFIANCE\s+LEVERAGED\s+LONG\s+\+\s+INCOME\s+([A-Z]{2,8})\s+ETF",
        re.IGNORECASE,
    )
    patterns.append((defiance_lev, lambda m: m.group(1).strip(), "HIGH"))

    return patterns


_PATTERNS = _build_patterns()

# Words that indicate a broad / multi-stock underlier; if regex extracts one
# of these we demote to UNCLEAR rather than claiming a single-stock match.
_KNOWN_INDEX_WORDS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "INNOVATION", "AI", "BIG",
    "SEMICONDUCTOR", "GOLD", "SILVER", "MINERS", "JUNIOR",
    "BITCOIN", "ETHER", "ETHEREUM",
    "TREASURY", "BOND", "INCOME", "EQUITY", "INDEX",
    "NASDAQ", "RUSSELL", "DOW", "MSCI",
})

# CC funds where the underlier is intentionally a wrapper ETF (not the raw asset).
# Key: CC fund ticker, Value: correct map_cc_underlier
# These are validated correct mappings — do not flag as UNCLEAR/MISMATCH.
_KNOWN_WRAPPER_UNDERLIERS: dict[str, str] = {
    # YieldBoost funds use a leveraged ETF as the actual underlier instrument
    "SEMY US": "SOXL US",   # Semiconductor -> SOXL (3x Semi ETF)
    "TQQY US": "QQQ US",    # QQQ YieldBoost -> QQQ
    "YSPY US": "SPXL US",   # SPY YieldBoost -> SPXL (3x S&P ETF)
    "NUGY US": "NUGT US",   # Gold Miners YieldBoost -> NUGT (3x Gold Miners ETF)
    "XBTY US": "BITX US",   # Bitcoin YieldBoost -> BITX (2x Bitcoin ETF)
    # Options on BTC spot products -- IBIT is the largest; some use different
    "BCCC US": "IBIT US",   # Global X BTC CC -> should be IBIT; 'BTC' is not a Bloomberg ticker
    "YETH US": "ETHA US",   # Roundhill Ether CC -> should be ETHA; 'ETH' is not a Bloomberg ticker
    # KURV Yahoo Finance / block (SQ) -- XYZ is Block's ticker
    "XYZY US": "XYZ US",    # YIELDMAX XYZ OPTION INCOME -> XYZ US (Block), not SQ US
    # YieldMax RBLX is missing ' US' suffix
    "RBLY US": "RBLX US",   # RBLX, not RBLX (without suffix)
    # YieldMax CVNA is missing ' US' suffix
    "CVNY US": "CVNA US",
    # TLTX self-reference: TLTX UA is the fund's own ticker variant, not an underlier
    "TLTX US": "TLT US",    # Global X Treasury Bond Enhanced Income -> TLT US
}

# CC funds where OARK etc. are multi-underlier and are genuinely UNCLEAR
_KNOWN_MULTI_UNDERLIER: frozenset[str] = frozenset({
    "OARK US",   # YIELDMAX INNOVATION -- ARK basket, not single stock
    "ULTY US",   # YIELDMAX ULTRA -- multi-stock high-income basket; ULTRA is not a ticker
})


def _resolve(word: str) -> tuple[str, str]:
    """Return (resolved_ticker, confidence) for a word extracted by regex."""
    upper = word.strip().upper()

    if upper in WORD_ALIASES:
        return WORD_ALIASES[upper]

    if upper in _KNOWN_INDEX_WORDS:
        return (word, "LOW")

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
        SELECT ticker, fund_name, map_cc_underlier
        FROM mkt_master_data
        WHERE etp_category  = 'CC'
          AND market_status = 'ACTV'
        ORDER BY ticker
    """)
    rows = cur.fetchall()

    results: list[dict] = []
    for ticker, fund_name, current_map in rows:
        fund_name = fund_name or ""

        # Known wrapper / special-case overrides take priority
        if ticker in _KNOWN_WRAPPER_UNDERLIERS:
            expected = _KNOWN_WRAPPER_UNDERLIERS[ticker]
            confidence = "HIGH"
            status = "OK" if _tickers_equivalent(current_map, expected) else "MISMATCH"
        elif ticker in _KNOWN_MULTI_UNDERLIER:
            expected = None
            confidence = "LOW"
            status = "UNCLEAR"
        else:
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
    print(f"Auditing CC underlier mappings in {DB_PATH}")

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
    print(f"Total CC ACTV products  : {total}")
    print(f"  OK       : {ok}")
    print(f"  MISMATCH : {mismatch}  (HIGH={mismatch_high}, MEDIUM={mismatch_medium})")
    print(f"  UNCLEAR  : {unclear}")
    print()
    print("MISMATCH details:")
    for r in results:
        if r["status"] == "MISMATCH":
            print(
                f"  [{r['confidence']:6s}] {r['ticker']:14s}  "
                f"current={r['current_map'] or '(null)':22s}  "
                f"expected={r['expected']}"
            )
    print()
    print(f"Audit written to: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
