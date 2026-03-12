"""Structured product parsers for SEC 424B2 filings.

Each issuer has a parser class that extracts product details from filing HTML.
The base parser handles ~80% of cases; issuer-specific parsers override
methods only where the issuer's template differs significantly.

All bug fixes from prior sessions are baked in:
- CUSIP validation (reject word fragments like "CONTINGEN")
- CIBC maturity (use "due" pattern, not "Maturity Date" header which grabs pricing date)
- JPMorgan barrier (handle embedded spaces in "65 .00%", prefer Trigger Value)
- HSBC coupon (annualize quarterly rates)
- Citi barrier (barrier level/value, table layout, buffer->barrier, conversion price)
"""
from __future__ import annotations

import re
from datetime import date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date_text(text: str) -> date | None:
    """Parse 'Month DD, YYYY' or 'Month DD YYYY' into a date."""
    if not text:
        return None
    text = text.strip().replace(",", "")
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.match(r"(\w+)\s+(\d{1,2})\s+(\d{4})", text)
    if not m:
        return None
    month_str, day_str, year_str = m.group(1).lower(), m.group(2), m.group(3)
    month = months.get(month_str)
    if not month:
        return None
    try:
        return date(int(year_str), month, int(day_str))
    except ValueError:
        return None


def clean_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Base parser
# ---------------------------------------------------------------------------

class BaseProductParser:
    """Generic structured product parser. Works for ~80% of filings."""

    def __init__(self, html: str, issuer_name: str = ""):
        self.html = html
        self.issuer_name = issuer_name
        self.clean = clean_html(html)
        self.clean_lower = self.clean.lower()

    def extract_all(self) -> dict:
        """Extract all fields into a dict."""
        data = {
            "cusip": self.extract_cusip(),
            "isin": self.extract_isin(),
            "product_name": self.extract_product_name(),
            "product_type": self.extract_product_type(),
            "product_subtype": self.extract_product_subtype(),
            "is_preliminary": self.extract_is_preliminary(),
            "underlier_count": self.extract_underlier_count(),
            "underlier_names": self.extract_underlier_names(),
            "underlier_tickers": None,  # Derived from underlier_names later
            "underlier_type": self.extract_underlier_type(),
            "notional_amount": self.extract_notional(),
            "denomination": self.extract_denomination(),
            "pricing_date": self.extract_pricing_date(),
            "settlement_date": self.extract_settlement_date(),
            "maturity_date": self.extract_maturity_date(),
            "coupon_rate": self.extract_coupon_rate(),
            "coupon_type": self.extract_coupon_type(),
            "coupon_frequency": self.extract_coupon_frequency(),
            "barrier_level": self.extract_barrier_level(),
            "barrier_type": self.extract_barrier_type(),
            "call_premium": self.extract_call_premium(),
            "call_level": self.extract_call_level(),
            "first_call_date": self.extract_first_call_date(),
            "call_frequency": self.extract_call_frequency(),
            "is_memory_coupon": self.extract_is_memory_coupon(),
            "participation_rate": self.extract_participation_rate(),
            "upside_cap": self.extract_upside_cap(),
            "downside_leverage": None,  # Rare, implement later
        }
        data["confidence"] = self._calc_confidence(data)
        return data

    # --- Identifiers ---

    @staticmethod
    def _is_valid_cusip(s: str) -> bool:
        """Reject word fragments that happen to be 9 alphanumeric chars."""
        if not re.match(r"^[A-Z0-9]{9}$", s):
            return False
        if not re.search(r"\d", s):
            return False  # all letters = likely a word
        if re.match(r"^[A-Z]+$", s[:6]):
            return False  # first 6 chars all letters = word fragment
        return True

    def extract_cusip(self) -> str | None:
        # Strategy 1: CUSIP in HTML (handles tags between label and value)
        m = re.search(
            r"CUSIP[/\s:]*(?:ISIN)?[/\s:]*(?:<[^>]*>\s*)*([A-Z0-9]{9})",
            self.html, re.IGNORECASE,
        )
        if m and self._is_valid_cusip(m.group(1).upper()):
            return m.group(1).upper()

        # Strategy 2: CUSIP in clean text
        m = re.search(
            r"CUSIP[/\s:]*(?:ISIN)?[/\s:]*([A-Z0-9]{7,9})",
            self.clean, re.IGNORECASE,
        )
        if m and self._is_valid_cusip(m.group(1).upper().ljust(9, "0")):
            return m.group(1).upper()

        # Strategy 3: 9-char alphanumeric near "CUSIP" keyword (400 char window for table layouts)
        idx = self.clean.upper().find("CUSIP")
        if idx >= 0:
            for cm in re.finditer(r"([A-Z0-9]{9})", self.clean[idx:idx+400]):
                if self._is_valid_cusip(cm.group(1).upper()):
                    return cm.group(1).upper()

        return None

    def extract_isin(self) -> str | None:
        m = re.search(r"ISIN[:\s]*([A-Z]{2}[A-Z0-9]{10})", self.clean, re.IGNORECASE)
        return m.group(1).upper() if m else None

    # --- Product name & type ---

    def extract_product_name(self) -> str | None:
        m = re.search(
            r"Structured\s+Investments?\s+(.*?)(?:Fully\s+and\s+Unconditionally|"
            r"Neither\s+the\s+Securities|The\s+notes\s+are\s+(?:designed|unsecured))",
            self.clean, re.IGNORECASE | re.DOTALL,
        )
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            return name[:500] if name else None
        return None

    def extract_product_type(self) -> str | None:
        text = self.clean_lower[:15000]

        # --- Autocallable family (most common ~57%) ---
        if "auto callable" in text or "autocallable" in text or "auto-callable" in text:
            return "autocallable"
        if "reverse convertible" in text:
            return "reverse_convertible"
        if re.search(r"(?:callable|redeemable)\s+(?:\w+\s+)?(?:contingent|barrier)", text):
            return "autocallable"
        if "contingent coupon" in text or "contingent interest" in text:
            return "autocallable"
        # "Contingent Income Auto-Callable Securities" (various issuers)
        if "contingent income" in text and ("callable" in text or "call" in text):
            return "autocallable"
        # "Trigger" + callable/redeemable pattern (JPMorgan "Trigger Notes")
        if "trigger" in text and re.search(r"(?:callable|auto|early\s+redemption)", text):
            return "autocallable"
        # "Review Notes" = JPMorgan autocallable brand
        if "review note" in text:
            return "autocallable"
        # MS "Trigger Jump Securities/Notes", "Jump Securities", "Callable Jump Notes"
        if "jump" in text and ("trigger" in text or "callable" in text or "securities" in text):
            return "autocallable"
        # MS "Dual Directional" notes
        if "dual directional" in text:
            return "buffered"

        # --- Branded product families ---
        # Morgan Stanley PLUS/Trigger PLUS/Buffered PLUS
        if re.search(r"(?:trigger\s+)?PLUS\b", self.clean[:15000]):
            return "leveraged"
        # Goldman STEP (Strategic Equity-Linked)
        if re.search(r"\bSTEP\b", self.clean[:5000]):
            return "leveraged"
        # Barclays/Citi "Accelerated Return Notes" (ARN), JPMorgan "Accelerated Barrier Notes"
        if re.search(r"accelerated\s+(?:return|barrier)", text):
            return "accelerated_return"
        # "Buffered Participation Securities" (MS)
        if "buffered participation" in text or "buffer participation" in text:
            return "buffered"

        # --- Other structured types ---
        if "range accrual" in text:
            return "range_accrual"
        if re.search(r"digital\s+(?:note|return|coupon)", text):
            return "digital"
        if re.search(r"(?:buffered|buffer)\s+\S+.{0,60}?(?:note|return|securit)", text):
            return "buffered"
        if "principal protected" in text or "principal-protected" in text:
            return "principal_protected"
        if re.search(r"(?:capped\s+)?(?:gears|growth|participation)\s+(?:note|linked)", text):
            return "growth"
        if re.search(r"(?:leveraged|enhanced)\s+\S+.{0,60}?(?:note|return|securit)", text):
            return "leveraged"
        if re.search(r"(?:market|index)[- ]linked\s+(?:note|investment|securit)", text):
            return "growth"
        if "fixed-to-floating" in text or "fixed to floating" in text:
            return "fixed_to_floating"
        if re.search(r"callable\s+(?:note|fixed|step|contingent)", text):
            return "callable"
        if re.search(r"(?:step[- ]?up|fixed\s+rate)\s+(?:note|callable)", text):
            return "callable"
        return "unclassified"

    def extract_is_preliminary(self) -> bool:
        text = self.clean_lower[:5000]
        if "preliminary" in text and "pricing supplement" in text:
            return True
        if "subject to completion" in text:
            return True
        return False

    def extract_underlier_count(self) -> int | None:
        text = self.clean_lower[:8000]
        if "worst of" in text or "least performing" in text:
            # Count unique ticker-like patterns in the underlier section
            # Look for patterns like "Common Stock of X (TICKER)" or "the TICKER"
            underlier_section = self.clean[:8000]
            tickers = set(re.findall(r'\(([A-Z]{1,5})\)', underlier_section))
            if len(tickers) >= 2:
                return len(tickers)
            return None  # can't determine count
        if "basket" in text:
            return None  # basket = multiple but unknown
        return 1

    # --- Maturity ---

    def extract_maturity_date(self) -> date | None:
        # Skip optional footnote markers (single digits like "1" or "2") between label and date
        m = re.search(
            r"(?:due|Maturity\s+Date)[:\s]*(?:\d{1,2}\s+)?(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:10000], re.IGNORECASE,
        )
        return parse_date_text(m.group(1)) if m else None

    def extract_final_valuation_date(self) -> date | None:
        m = re.search(
            r"Final\s+(?:Valuation|Observation|Review)\s+Date[:\s]*(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:15000], re.IGNORECASE,
        )
        return parse_date_text(m.group(1)) if m else None

    # --- Notional ---

    def extract_notional(self) -> float | None:
        # Strategy 1: "Structured Investments $X"
        m = re.search(
            r"Structured\s+Investments?\s+\$\s*([\d,]+(?:\.\d+)?)",
            self.clean[:5000], re.IGNORECASE,
        )
        if m:
            return self._parse_dollar(m.group(1))

        # Strategy 2: First $ amount > $10K on cover
        for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)", self.clean[:5000]):
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val

        # Strategy 3: "Aggregate principal/notional amount: $X"
        m = re.search(
            r"(?:Aggregate\s+(?:principal|notional|face)\s+amount|Total)[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return self._parse_dollar(m.group(1))

        return None

    def extract_denomination(self) -> float | None:
        m = re.search(
            r"(?:Minimum\s+)?(?:denominations?\s+of|Stated\s+principal\s+amount)[:\s]*\$\s*([\d,]+)",
            self.clean[:10000], re.IGNORECASE,
        )
        return self._parse_dollar(m.group(1)) if m else 1000.0

    # --- Coupon ---

    def extract_coupon_rate(self) -> float | None:
        """Extract annualized coupon rate as decimal (0.08 = 8%)."""
        # "X% per quarter (approximately Y% per annum)" - prefer annualized
        m = re.search(
            r"([\d.]+)\s*%\s*per\s+(?:quarter|month|semi-?annual\s+period).*?"
            r"(?:approximately|equivalent\s+to|equal\s+to)\s+([\d.]+)\s*%\s*per\s+annum",
            self.clean[:30000], re.IGNORECASE,
        )
        if m:
            return float(m.group(2)) / 100.0

        # "Contingent Interest Rate: X%" or "Coupon Rate: X%"
        m = re.search(
            r"(?:Contingent\s+(?:Interest|Coupon)\s+Rate|(?:Fixed\s+)?Coupon\s+Rate)[:\s]*(?:at\s+least\s+)?([\d.]+)\s*%",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # "X% per annum"
        m = re.search(r"([\d.]+)\s*%\s*per\s+annum", self.clean[:20000], re.IGNORECASE)
        if m:
            return float(m.group(1)) / 100.0

        # "contingent quarterly/monthly/semi-annual payment equal to X% of the stated principal"
        # Barclays/Goldman/RBC pattern: "contingent quarterly payment equal to 2.8125%"
        m = re.search(
            r"contingent\s+(?:quarterly|monthly|semi-?annual|annual)\s+(?:coupon\s+)?payment\s+"
            r"(?:equal\s+to\s+|of\s+)?([\d.]+)\s*%\s*(?:of\s+(?:the\s+)?(?:stated\s+)?(?:principal|notional|face))?",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            rate = float(m.group(1))
            # This is a periodic rate - annualize based on the frequency word
            period_match = re.search(r"contingent\s+(quarterly|monthly|semi-?annual|annual)", m.group(0), re.IGNORECASE)
            if period_match:
                period = period_match.group(1).lower()
                mult = {"quarterly": 4, "monthly": 12, "annual": 1}.get(period, 4)
                if "semi" in period:
                    mult = 2
                return round(rate * mult / 100.0, 4)
            return rate / 100.0

        # "contingent coupon/interest payment... X% of the stated principal amount per annum"
        m = re.search(
            r"contingent\s+(?:coupon|interest)\s+payment.{0,80}?([\d.]+)\s*%\s*(?:of\s+(?:the\s+)?(?:stated\s+)?(?:principal|notional|face)\s+amount\s+)?per\s+annum",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # "contingent coupon/interest payment... X% of stated principal amount" (no period specified)
        m = re.search(
            r"contingent\s+(?:coupon|interest)\s+payment.{0,80}?([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:stated\s+)?(?:principal|notional|face)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # "pay a contingent coupon at a rate of X% per annum"
        m = re.search(
            r"(?:pay|receive)\s+a\s+contingent\s+(?:coupon|interest).{0,60}?(?:rate\s+of\s+|equal\s+to\s+)?([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # "$X per Note/$1,000/$10,000 principal amount" with frequency conversion
        # Handles: "Contingent Coupon Payment: $X per Note"
        #          "Contingent Coupon: If payable, $283.50 per $10,000 principal amount"
        m = re.search(
            r"Contingent\s+(?:Coupon|Interest)(?:\s+Payment)?[:\s]*(?:If\s+payable,?\s*)?\$([\d.]+)\s*per\s+(?:Note|\$([\d,]+)\s*(?:principal\s+amount)?)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            payment = float(m.group(1))
            denom = float(m.group(2).replace(",", "")) if m.group(2) else 1000.0
            freq = self.extract_coupon_frequency()
            mult = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}.get(freq, 4)
            return round((payment / denom) * mult, 4)

        # Goldman-style: "$X.XX (the number of coupon observation dates)" per $1,000
        # This is a per-observation-date payment in a memory coupon structure
        m = re.search(
            r"\$([\d.]+)\s*(?:\*\s*)?(?:the\s+number\s+of|multiplied\s+by)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            payment = float(m.group(1))
            if 1 < payment < 200:  # sanity check - $1-$200 per period per $1,000
                freq = self.extract_coupon_frequency()
                mult = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}.get(freq, 4)
                return round((payment / 1000.0) * mult, 4)

        return None

    def extract_coupon_type(self) -> str | None:
        text = self.clean_lower[:15000]
        if "contingent coupon" in text or "contingent interest" in text:
            return "contingent"
        if re.search(r"fixed\s+(?:coupon|rate|interest)", text):
            return "fixed"
        if re.search(r"floating\s+(?:coupon|rate|interest)", text):
            return "floating"
        if "no interest" in text or "will not pay interest" in text:
            return "none"
        return None

    def extract_coupon_frequency(self) -> str | None:
        text = self.clean_lower[:15000]
        if "per month" in text or "monthly" in text:
            return "monthly"
        if "per quarter" in text or "quarterly" in text:
            return "quarterly"
        if "semi-annual" in text or "semiannual" in text:
            return "semiannual"
        if "per annum" in text or "annual" in text:
            return "annual"
        return None

    # --- Barrier ---

    def extract_barrier_level(self) -> float | None:
        """Extract knock-in/trigger barrier as decimal (0.70 = 70%)."""
        # "Barrier/Trigger/Knock-in Level: X%" (skip footnote markers like "2" before $amounts)
        m = re.search(
            r"(?:Barrier|Trigger|Knock-?in)\s+(?:Level|Value|Price)[:\s]*(?:\d{1,2}\s+)?(?:\$[\d,. ]+,?\s*)?(?:which\s+is\s+)?([\d.]+)\s*%\s*of",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # Simple: "Barrier/Trigger/Knock-in Level: X%" (no dollar amount)
        m = re.search(
            r"(?:Barrier|Trigger|Knock-?in)\s+(?:Level|Value|Price)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Barrier/Buffer Level: $X, which is X% of the Initial Level"
        m = re.search(
            r"(?:Barrier|Buffer|Trigger)\s+Level[:\s]*[\d$,. ]*(?:which\s+is|equal\s+to)\s+([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:Initial|Starting)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "(equal to X% of the Initial Level)" after Barrier/Buffer Level
        m = re.search(
            r"(?:Barrier|Buffer)\s+Level\s*\(equal\s+to\s+([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:Initial|Starting)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "X% of (the) (respective) Initial/Starting Value/Price/Level"
        # Use finditer because the first match may be 100% (Call Level) — need to find one in (10,100)
        for m in re.finditer(
            r"([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:respective\s+)?(?:Initial|Starting|Hypothetical\s+Initial)",
            self.clean[:40000], re.IGNORECASE,
        ):
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Downside Threshold (Level): X%"
        m = re.search(
            r"Downside\s+Threshold(?:\s+Level)?[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Buffer (Amount/Level): X%" -> barrier = (100 - buffer) / 100
        m = re.search(
            r"Buffer\s+(?:Amount|Level|Percentage)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            buf = float(m.group(1))
            if 1 < buf < 50:
                return (100 - buf) / 100.0

        return None

    def extract_barrier_type(self) -> str | None:
        text = self.clean_lower[:15000]
        if "european barrier" in text or "at maturity" in text:
            return "european"
        if "american barrier" in text or "daily" in text or "continuous" in text:
            return "american"
        return None

    # --- Underlier details ---

    def extract_underlier_names(self) -> str | None:
        """Extract underlier names/tickers from the filing.

        Returns comma-separated string: "AAPL, MSFT, AMZN" for equities,
        "S&P 500 Index (SPX), Russell 2000 Index (RTY)" for indices, etc.
        """
        result = self._extract_underlier_names_raw()
        if result:
            return self._normalize_underlier_result(result)
        return None

    def _extract_underlier_names_raw(self) -> str | None:
        """Internal: extract underlier names before normalization."""
        text = self.clean[:20000]
        text_lower = self.clean_lower[:20000]
        # Raw HTML preserves &amp; entities (S&P stays as S&P, not "S P")
        raw_text = re.sub(r"<[^>]+>", " ", self.html[:25000])
        raw_text = re.sub(r"&#\d+;", " ", raw_text)
        raw_text = re.sub(r"\s+", " ", raw_text).strip()

        # ---- Strategy 1: Bloomberg ticker labels in Key Terms ----
        # JPMorgan: "(Bloomberg ticker: NDXT)" / Goldman: "(Ticker: AAPL)"
        tickers = re.findall(
            r"(?:Bloomberg\s+)?[Tt]icker(?:\s+symbol)?[:\s]+([A-Z]{2,6})",
            text[:20000],
        )
        # Filter out non-ticker abbreviations
        noise1 = {"SM", "LLC", "INC", "PLC", "USA", "THE", "SEC", "NYSE", "OTC", "CUSIP", "ISIN"}
        tickers = [t for t in tickers if t not in noise1]
        if tickers:
            seen: set[str] = set()
            unique = [t for t in tickers if t not in seen and not seen.add(t)]
            return ", ".join(unique)

        # ---- Strategy 2: Title extraction — "Linked to ... due Month DD, YYYY" ----
        # The cover/title line ends at "due" or "Fully and Unconditionally"
        m = re.search(
            r"(?:Linked|Relating|Related)\s+to\s+(?:the\s+)?(?:(?:Least|Worst|Best|Lesser)\s+Performing\s+of\s+)?(?:the\s+)?"
            r"(.+?)\s+due\s+\w+\s+\d",
            text[:3000], re.IGNORECASE | re.DOTALL,
        )
        if m:
            block = re.sub(r"\s+", " ", m.group(1)).strip()
            # Extract tickers in parentheses
            tickers = re.findall(r'\(([A-Z]{1,5})\)', block)
            noise = {"LLC", "INC", "PLC", "USA", "THE", "SEC", "SM"}
            tickers = [t for t in tickers if t not in noise]
            if tickers:
                return ", ".join(tickers)
            # Clean up index/stock names from title
            if len(block) < 300:
                block = re.sub(r"(?:the\s+)?Common\s+Stock\s+of\s+", "", block, flags=re.IGNORECASE)
                block = re.sub(r"(?:the\s+)?Shares\s+of\s+", "", block, flags=re.IGNORECASE)
                block = re.sub(r"\s*,\s*(?:the\s+)?", ", ", block)
                block = re.sub(r"\s+and\s+(?:the\s+)?", ", ", block, flags=re.IGNORECASE)
                block = re.sub(r"\s+SM\b", "", block)  # Remove SM trademark
                block = block.strip().rstrip(",").strip()
                if block and len(block) > 3:
                    return block

        # ---- Strategy 3: "Underlying: X (TICKER)" in Key Terms ----
        # Require colon to distinguish labels from running text ("the underlier return")
        for m in re.finditer(
            r"(?:Underlying|Underlier|Reference\s+(?:Asset|Stock|Index|Security))"
            r"(?:s|\(s\))?\s*:\s*(.+?)(?:Contingent|Pricing|Settlement|Denomination|CUSIP|Maturity|Initial\s+(?:Value|Level|Price))",
            text, re.IGNORECASE | re.DOTALL,
        ):
            block = re.sub(r"\s+", " ", m.group(1)).strip()
            # Skip boilerplate matches
            if re.search(r"supplement|prospectus|registration|discontinued|successor|determination|payment|underlier\s+level|underlier\s+return|cash", block, re.IGNORECASE):
                continue
            if len(block) < 3:
                continue
            tickers = re.findall(r'\(([A-Z]{2,5})\)', block)
            noise = {"LLC", "INC", "PLC", "USA", "THE", "SEC", "SM"}
            tickers = [t for t in tickers if t not in noise]
            if tickers:
                return ", ".join(tickers)
            if 5 < len(block) < 200:
                name = re.sub(r"(?:the\s+)?(?:common\s+stock|shares|ordinary\s+shares)\s+of\s+", "", block, flags=re.IGNORECASE)
                name = re.sub(r"(?:the\s+)?(?:closing\s+)?(?:price|level|value)\s+of\s+", "", name, flags=re.IGNORECASE)
                name = name.strip().rstrip(",").strip()
                if name and 5 < len(name) < 150 and not re.search(r"supplement|prospectus|discontinued|successor|return|payment|buffer|barrier", name, re.IGNORECASE):
                    return name

        # ---- Strategy 4: "based on [the worst performing of] X (TICK), Y (TICK)" ----
        m = re.search(
            r"(?:based|contingent)\s+(?:on|upon)\s+(?:the\s+)?(?:least|worst|best)\s+"
            r"performing\s+of\s+(.+?)(?:\.\s|Fully|Neither|The\s+notes|Pricing|CUSIP)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if m:
            block = re.sub(r"\s+", " ", m.group(1)).strip()
            tickers = re.findall(r'\(([A-Z]{1,5})\)', block)
            noise = {"LLC", "INC", "PLC", "USA", "THE", "SEC", "SM"}
            tickers = [t for t in tickers if t not in noise]
            if tickers:
                return ", ".join(tickers)

        # ---- Strategy 5: Well-known index names -> Bloomberg tickers ----
        known_indices = {
            "s&p 500": "SPX",
            "s p 500": "SPX",
            "russell 2000": "RTY",
            "nasdaq-100": "NDX",
            "nasdaq 100": "NDX",
            "dow jones industrial": "INDU",
            "euro stoxx 50": "SX5E",
            "nikkei 225": "NKY",
            "msci eafe": "MXEA",
            "msci emerging": "MXEF",
            "hang seng": "HSI",
            "ftse 100": "UKX",
            "kospi 200": "KOSPI2",
            "stoxx europe 600": "SXXP",
        }
        indices_found = []
        combined_lower = text_lower + " " + raw_text.lower()
        for pattern, display in known_indices.items():
            if pattern in combined_lower and display not in indices_found:
                indices_found.append(display)
        if indices_found:
            return ", ".join(indices_found)

        # ---- Strategy 6: Ticker-slash pattern "AAPL / MSFT / AMZN" ----
        m = re.search(
            r"(?:Notes?\s+(?:Linked|Based|Relating)\s+(?:to|on)\s+)"
            r"([A-Z]{1,5}(?:\s*/\s*[A-Z]{1,5})+)",
            text,
        )
        if m:
            tickers = [t.strip() for t in m.group(1).split("/")]
            return ", ".join(tickers)

        # ---- Strategy 7: Broad "(TICKER)" scan in cover area ----
        cover = text[:5000]
        tickers = re.findall(r'\(([A-Z]{2,5})\)', cover)
        noise = {
            "LLC", "INC", "PLC", "USA", "THE", "SEC", "NYSE", "OTC",
            "CUSIP", "ISIN", "USD", "EUR", "GBP", "JPY", "CAD",
            "PDF", "HTML", "NOTE", "PLUS", "DUE", "FOR", "AND",
            "NOT", "NOR", "PER", "ARE", "ETF", "CDN", "REG", "SM",
        }
        tickers = [t for t in tickers if t not in noise]
        if tickers:
            seen2: set[str] = set()
            unique2 = [t for t in tickers if t not in seen2 and not seen2.add(t)]
            if len(unique2) >= 1:
                return ", ".join(unique2)

        return None

    # Canonical ticker mapping: normalize full index names to Bloomberg tickers
    _INDEX_TO_TICKER = {
        "S&P 500 Index": "SPX",
        "S&P 500": "SPX",
        "Russell 2000 Index": "RTY",
        "Russell 2000": "RTY",
        "Nasdaq-100 Index": "NDX",
        "Nasdaq-100": "NDX",
        "Dow Jones Industrial Average": "INDU",
        "EURO STOXX 50 Index": "SX5E",
        "EURO STOXX 50": "SX5E",
        "Nikkei 225 Index": "NKY",
        "Nikkei 225": "NKY",
        "MSCI EAFE Index": "MXEA",
        "MSCI Emerging Markets Index": "MXEF",
        "Hang Seng Index": "HSI",
        "FTSE 100 Index": "UKX",
        "FTSE 100": "UKX",
        "KOSPI 200 Index": "KOSPI2",
        "STOXX Europe 600 Index": "SXXP",
        "Nasdaq-100 Technology Sector Index": "NDXT",
        "S&P/TSX 60 Index": "SPTSX60",
    }

    @staticmethod
    def _normalize_underlier_result(result: str) -> str:
        """Post-process underlier names: fix S P -> S&P, normalize to tickers, trim noise."""
        result = result.replace("S P 500", "S&P 500")
        result = result.replace("S P/TSX", "S&P/TSX")
        result = re.sub(r"\s+SM\b", "", result)
        # Remove HTML entities that leaked through
        result = re.sub(r"&#x[0-9a-fA-F]+;", "", result)
        result = re.sub(r"&[a-z]+;", "", result)
        # Trim trailing noise (dates, labels, parenthetical descriptions)
        result = re.sub(r"\s*(?:Strike|Pricing|Settlement|Maturity|Initial|Current)\s+(?:date|level|value|price).*$", "", result, flags=re.IGNORECASE)
        result = re.sub(r"\s*\(the\s+underlying\s+(?:index|stock|asset)\s*\).*$", "", result, flags=re.IGNORECASE)
        result = re.sub(r"\s*\(current\s+Bloomberg\s+symbol.*$", "", result, flags=re.IGNORECASE)
        result = re.sub(r"\s*(?:Max|Minimum|Maximum)\s.*$", "", result, flags=re.IGNORECASE)
        # Remove "Lowest/Least/Worst Performing of the" prefix
        result = re.sub(r"^(?:Lowest|Least|Worst|Best|Lesser)\s+Performing\s+of\s+(?:the\s+)?", "", result, flags=re.IGNORECASE)

        result = result.strip().rstrip(",").strip()

        # Normalize full index names to Bloomberg tickers
        # Split by comma, normalize each part, rejoin
        parts = [p.strip() for p in result.split(",")]
        normalized = []
        for part in parts:
            if not part:
                continue
            # Strip leading "the" (e.g., "the S&P 500 Index" -> "S&P 500 Index")
            clean_part = re.sub(r"^the\s+", "", part, flags=re.IGNORECASE).strip()
            mapped = BaseProductParser._INDEX_TO_TICKER.get(clean_part)
            if mapped:
                normalized.append(mapped)
            else:
                normalized.append(clean_part)
        return ", ".join(normalized) if normalized else result

    def extract_underlier_type(self) -> str | None:
        """Classify the underlier as equity, index, etf, commodity, rate, or mixed."""
        text = self.clean_lower[:15000]
        # Also check raw HTML for entity-encoded terms like S&amp;P
        raw_lower = re.sub(r"<[^>]+>", " ", self.html[:20000]).lower()

        has_index = any(idx in text or idx in raw_lower for idx in [
            "s&p 500", "s p 500", "russell 2000", "nasdaq", "dow jones",
            "euro stoxx", "nikkei", "index",
        ])
        has_equity = any(kw in text for kw in [
            "common stock", "shares of", "ordinary shares",
        ])
        has_etf = any(kw in text for kw in [
            "exchange-traded fund", "exchange traded fund", "etf",
        ])
        has_commodity = any(kw in text for kw in [
            "gold", "silver", "crude oil", "natural gas", "commodity", "wti",
        ])
        has_rate = any(kw in text for kw in [
            "libor", "sofr", "interest rate", "cms rate", "swap rate",
        ])

        types = []
        if has_index:
            types.append("index")
        if has_equity:
            types.append("equity")
        if has_etf:
            types.append("etf")
        if has_commodity:
            types.append("commodity")
        if has_rate:
            types.append("rate")

        if len(types) == 0:
            return None
        if len(types) == 1:
            return types[0]
        return "mixed"

    # --- Dates ---

    def extract_pricing_date(self) -> date | None:
        m = re.search(
            r"(?:Pricing|Trade)\s+Date[:\s]*(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:15000], re.IGNORECASE,
        )
        return parse_date_text(m.group(1)) if m else None

    def extract_settlement_date(self) -> date | None:
        m = re.search(
            r"(?:Settlement|Issue)\s+Date[:\s]*(?:\d{1,2}\s+)?(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:15000], re.IGNORECASE,
        )
        return parse_date_text(m.group(1)) if m else None

    # --- Product subtype ---

    def extract_product_subtype(self) -> str | None:
        text = self.clean_lower[:15000]
        if "phoenix" in text:
            return "phoenix"
        if "worst of" in text or "least performing" in text:
            return "worst-of"
        if "best of" in text:
            return "best-of"
        if "rainbow" in text:
            return "rainbow"
        if "snowball" in text:
            return "snowball"
        if "memory" in text and ("coupon" in text or "interest" in text):
            return "memory"
        if "airbag" in text:
            return "airbag"
        if re.search(r"PLUS|Performance\s+Leveraged\s+Upside", text, re.IGNORECASE):
            return "PLUS"
        return None

    # --- Call structure ---

    def extract_call_premium(self) -> float | None:
        """Extract per-call-period premium as decimal (0.10 = 10% above par).

        Call premium is the one-time premium paid above par when a note is
        auto-called. Distinct from coupon_rate (periodic interest payment).
        """
        # "Call Premium: X%" or "Call Premium: X% of the face/principal amount"
        m = re.search(
            r"Call\s+Premium[:\s]*([\d.]+)\s*%\s*(?:of\s+(?:the\s+)?(?:face|principal|stated)\s+amount)?",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 0 < val < 100:
                return val / 100.0

        # "Callable Return: X%" or "Call Return: X%"
        m = re.search(
            r"(?:Callable?\s+Return|Call\s+Return)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 0 < val < 100:
                return val / 100.0

        # "Early/Automatic Redemption Amount: $X per $1,000/$10,000" -> premium = (X / denom) - 1
        m = re.search(
            r"(?:Early|Automatic)\s+(?:Redemption|Call\s+Settlement)\s+Amount[:\s]*\$([\d,]+(?:\.\d+)?)\s*per\s+(?:\$([\d,]+)\s*(?:principal)?|Note)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            amount = float(m.group(1).replace(",", ""))
            denom = float(m.group(2).replace(",", "")) if m.group(2) else 1000.0
            if amount > denom:
                return round((amount - denom) / denom, 4)

        # "equal to $1,110" or "equal to $1,366" in auto-call context
        for m in re.finditer(
            r"equal\s+to\s+\$\s*(1,[\d]+(?:\.\d+)?)",
            self.clean[:40000], re.IGNORECASE,
        ):
            amount = float(m.group(1).replace(",", ""))
            if 1000 < amount < 3000:
                ctx = self.clean[max(0, m.start()-300):m.end()]
                if re.search(r"call|automatically|per\s+\$1,?000", ctx, re.IGNORECASE):
                    return round((amount - 1000) / 1000, 4)

        # "call premium of [at least] [approximately] X% of the face/principal amount"
        m = re.search(
            r"call\s+premium\s+of\s+(?:at\s+least\s+)?(?:approximately\s+)?([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:face|principal|stated)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 0 < val < 200:
                return val / 100.0

        # "call premium ... based on ... return of X% per annum"
        m = re.search(
            r"call\s+premium.{0,250}?(?:return|rate)\s+of\s+([\d.]+)\s*%\s*per\s+annum",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # "Call Price/Amount: X% of face/par"
        m = re.search(
            r"Call\s+(?:Price|Amount)[:\s]*([\d.]+)\s*%\s*(?:of\s+(?:the\s+)?(?:face|par|principal|stated))?",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 100 < val < 200:  # e.g., 110% of face = 10% premium
                return round((val - 100) / 100, 4)

        return None

    def extract_call_level(self) -> float | None:
        m = re.search(
            r"(?:Call|Autocall|Automatic\s+Call)\s+(?:Level|Trigger|Price)[:\s]*(?:\$[\d,. ]+,?\s*(?:which\s+is\s+)?)?([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 50 <= val <= 150:
                return val / 100.0
        return None

    def extract_first_call_date(self) -> date | None:
        m = re.search(
            r"(?:First|Initial|Earliest)\s+(?:Call|Review|Observation)\s+Date[:\s]*(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:20000], re.IGNORECASE,
        )
        return parse_date_text(m.group(1)) if m else None

    def extract_call_frequency(self) -> str | None:
        text = self.clean_lower[:15000]
        if not any(kw in text for kw in ["auto", "call", "callable", "redeemable"]):
            return None
        if "monthly" in text and ("call" in text or "review" in text):
            return "monthly"
        if "quarterly" in text and ("call" in text or "review" in text):
            return "quarterly"
        if "semi-annual" in text or "semiannual" in text:
            return "semiannual"
        if "annual" in text and ("call" in text or "review" in text):
            return "annual"
        return None

    def extract_is_memory_coupon(self) -> bool:
        text = self.clean_lower[:20000]
        return "memory" in text and ("coupon" in text or "interest" in text or "payment" in text)

    # --- Participation structure ---

    def extract_participation_rate(self) -> float | None:
        m = re.search(
            r"(?:Participation|Upside\s+Participation|Leverage\s+Factor)\s*(?:Rate)?[:\s]*([\d.]+)\s*%",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 100 < val < 1000:
                return val / 100.0
        # "X times" or "Xx" leverage
        m = re.search(
            r"(?:Participation|Upside\s+Leverage)[:\s]*([\d.]+)\s*(?:x|times)",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1))
        return None

    def extract_upside_cap(self) -> float | None:
        m = re.search(
            r"(?:Maximum|Cap|Capped)\s+(?:Return|Gain|Payment|Level)[:\s]*([\d.]+)\s*%",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 1 < val < 500:
                return val / 100.0
        return None

    # --- Helpers ---

    @staticmethod
    def _parse_dollar(s: str) -> float | None:
        if not s:
            return None
        try:
            return float(s.replace(",", "").replace(" ", ""))
        except ValueError:
            return None

    @staticmethod
    def _calc_confidence(data: dict) -> float:
        """Score 0-1 based on how many fields were extracted."""
        fields = ["cusip", "notional_amount", "maturity_date", "product_type",
                   "coupon_rate", "barrier_level", "underlier_count", "underlier_names"]
        filled = sum(1 for f in fields if data.get(f) is not None)
        return round(filled / len(fields), 2)


# ---------------------------------------------------------------------------
# Issuer-specific parsers
# ---------------------------------------------------------------------------

class JPMorganParser(BaseProductParser):
    """JPMorgan Chase Financial Company LLC (CIK 1665650).

    Notional: "Structured Investments $X" on cover.
    Barrier: "Trigger Value: X%" (principal protection) vs "Interest Barrier: X%" (coupon trigger).
    HTML stripping produces embedded spaces in numbers (e.g., "65 .00%").
    """

    def extract_barrier_level(self) -> float | None:
        # Trigger Value (principal protection) - preferred
        # Handle embedded spaces: "65 .00%" from HTML stripping
        m = re.search(
            r"Trigger\s+Value[:\s]*(?:\$[\d,. ]+,?\s*(?:which\s+is\s+)?)?"
            r"([\d]+(?:\s*\.\s*[\d]+)?)\s*%\s*of\s+(?:the\s+)?(?:Initial|Hypothetical)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1).replace(" ", ""))
            if 10 < val < 100:
                return val / 100.0

        # "Interest Barrier / Trigger Value: X%"
        m = re.search(
            r"Interest\s+Barrier\s*/?\s*Trigger\s+Value[:\s]*([\d]+(?:\s*\.\s*[\d]+)?)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1).replace(" ", ""))
            if 10 < val < 100:
                return val / 100.0

        # Interest Barrier fallback (coupon trigger - less preferred)
        m = re.search(
            r"Interest\s+Barrier[:\s]*([\d]+(?:\s*\.\s*[\d]+)?)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1).replace(" ", ""))
            if 10 < val < 100:
                return val / 100.0

        return super().extract_barrier_level()

    def extract_coupon_rate(self) -> float | None:
        m = re.search(
            r"Contingent\s+Interest\s+Rate[:\s]*(?:at\s+least\s+)?([\d.]+)\s*%",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0
        return super().extract_coupon_rate()


class GoldmanSachsParser(BaseProductParser):
    """GS Finance Corp (CIK 1419828).

    Notional: "Face amount: $X in the aggregate" or "Aggregate face amount: $X"
    Maturity: "Stated maturity date:"
    Coupon: Goldman uses "Contingent Coupon Equity-Linked Notes" with payment descriptions
           buried deeper in the document (often past char 20000).
    """

    def extract_product_name(self) -> str | None:
        # Goldman: "$X [Type] Notes due YYYY" or "$ [Type] Notes due YYYY" (prelim has no amount)
        m = re.search(
            r"GS\s+Finance\s+Corp\.?\s*\$?\s*[\d,]*\s*(.*?Notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?\d{4})",
            self.clean[:3000], re.IGNORECASE,
        )
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            return name[:500] if name else None
        return super().extract_product_name()

    def extract_coupon_rate(self) -> float | None:
        # Goldman often puts coupon info in "Key Terms" section, sometimes deep in doc
        # "Contingent coupon rate: X% per annum" or "contingent coupon... X%"
        m = re.search(
            r"(?:contingent\s+)?coupon\s+rate[:\s]*(?:at\s+least\s+)?([\d.]+)\s*%\s*(?:per\s+annum)?",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # Try base parser with extended search window
        result = super().extract_coupon_rate()
        if result:
            return result

        # "annualized rate of X%" or "annual rate of X%"
        m = re.search(
            r"(?:annualized|annual)\s+(?:coupon\s+)?rate\s+(?:of\s+|equal\s+to\s+)?([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        return None

    def extract_notional(self) -> float | None:
        # "Face amount: $X in the aggregate" or "Principal amount: $X in the aggregate"
        m = re.search(
            r"(?:Face|Principal)\s+amount:\s*\$([\d,]+(?:\.\d+)?)\s+in\s+the\s+aggregate",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 1_000:
                return val

        # "Aggregate face amount: $X"
        m = re.search(
            r"Aggregate\s+face\s+amount[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 1_000:
                return val

        # Search HTML (tags between label and value)
        m = re.search(
            r"(?:Face|Principal)\s+amount(?:</[^>]*>|\s|:)*\$\s*([\d,]+(?:\.\d+)?)",
            self.html[:80000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 1_000:
                return val

        # "Total $X" in pricing table
        for m in re.finditer(r"Total\s+\$([\d,]+(?:\.\d+)?)", self.clean[:30000]):
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val

        return super().extract_notional()

    def extract_maturity_date(self) -> date | None:
        # Goldman uses "Stated maturity date:"
        m = re.search(
            r"Stated\s+maturity\s+date[:\s]*(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return parse_date_text(m.group(1))
        return super().extract_maturity_date()


class MorganStanleyParser(BaseProductParser):
    """Morgan Stanley Finance LLC (CIK 1666268).

    Coupon: MS uses "Call Premium/Return: X%" or "Callable Return: X%" for autocallables.
    Also "Contingent Return: X%" patterns.
    """

    def extract_coupon_rate(self) -> float | None:
        # "Contingent Return/Payment: X%" (periodic coupon, NOT call premium)
        m = re.search(
            r"Contingent\s+(?:Return|Payment|Interest)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # Try base parser (includes contingent payment patterns)
        result = super().extract_coupon_rate()
        if result:
            return result

        # "earn X% (annualized)" or "earn X% per annum"
        m = re.search(
            r"earn\s+([\d.]+)\s*%\s*(?:\(annualized\)|per\s+annum)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        return None

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Morgan\s+Stanley\s+Finance[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class UBSParser(BaseProductParser):
    """UBS AG (CIK 1114446)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"UBS\s+AG[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class BarclaysParser(BaseProductParser):
    """Barclays Bank PLC (CIK 312070).

    Coupon: Barclays uses "contingent quarterly payment equal to X% of the stated
    principal amount" pattern. The payment rate is periodic, must annualize.
    """

    def extract_coupon_rate(self) -> float | None:
        # Try base parser first (handles most patterns including new contingent payment ones)
        result = super().extract_coupon_rate()
        if result:
            return result

        # Barclays-specific: "a contingent payment ... equal to X%" in description text
        m = re.search(
            r"contingent\s+(?:coupon\s+)?payment.{0,120}?equal\s+to\s+([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            rate = float(m.group(1))
            # Determine frequency from nearby text
            freq_text = self.clean_lower[:15000]
            if "quarterly" in freq_text:
                return round(rate * 4 / 100.0, 4)
            elif "monthly" in freq_text:
                return round(rate * 12 / 100.0, 4)
            elif "semi-annual" in freq_text or "semiannual" in freq_text:
                return round(rate * 2 / 100.0, 4)
            return rate / 100.0  # assume annual if unclear

        return None

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Barclays\s+Bank[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()

    def extract_barrier_level(self) -> float | None:
        # Barclays: "Barrier Level: X% of the Initial Underlying Value"
        m = re.search(
            r"(?:Knock-?in|Barrier)\s+(?:Level|Value|Price)[:\s]*[\d,.]*,?\s*([\d.]+)\s*%\s*of",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0
        return super().extract_barrier_level()


class BofAParser(BaseProductParser):
    """BofA Finance LLC (CIK 1682472).

    CUSIP: Many BofA filings omit the "CUSIP" keyword entirely.
    BofA CUSIPs start with "09709" or "09711" (BofA Finance LLC prefix).
    """

    # Known BofA CUSIP prefixes (BofA Finance LLC, Bank of America Corp)
    BOFA_CUSIP_PREFIXES = ("09709", "09711", "06051", "06048")

    def extract_cusip(self) -> str | None:
        # Try normal extraction first
        result = super().extract_cusip()
        if result:
            return result

        # Strategy 4: Search for known BofA CUSIP prefixes anywhere in text
        for prefix in self.BOFA_CUSIP_PREFIXES:
            for m in re.finditer(
                rf"\b({prefix}[A-Z0-9]{{4}})\b",
                self.clean[:80000],
            ):
                candidate = m.group(1).upper()
                if self._is_valid_cusip(candidate):
                    return candidate

        return None

    def extract_notional(self) -> float | None:
        m = re.search(
            r"BofA\s+Finance\s+LLC\s+\$([\d,]+(?:\.\d+)?)",
            self.clean[:5000],
        )
        if m:
            return self._parse_dollar(m.group(1))

        m = re.search(
            r"Aggregate\s+principal\s+amount[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
            self.clean[:30000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val

        return super().extract_notional()


class CitigroupParser(BaseProductParser):
    """Citigroup Global Markets Holdings Inc (CIK 200245).

    Barrier patterns: "barrier value/level", table layouts with parenthesized %,
    "downside threshold price", "buffer amount/percentage", "conversion price".
    """

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Citigroup\s+Global\s+Markets[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000],
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val

        m = re.search(
            r"Aggregate\s+(?:principal|face)\s+amount[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
            self.clean[:30000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val

        return super().extract_notional()

    def extract_coupon_rate(self) -> float | None:
        m = re.search(
            r"(?:Contingent\s+)?(?:Coupon|Interest)\s+Rate[:\s]*([\d.]+)\s*%",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0
        return super().extract_coupon_rate()

    def extract_barrier_level(self) -> float | None:
        # "Final barrier value/level: X, Y% of the initial underlying value"
        m = re.search(
            r"Final\s+barrier\s+(?:value|level)[:\s]*[\d,.]*,?\s*([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:initial|hypothetical)",
            self.clean[:60000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # Table layout: "barrier value/level ... (X% of its hypothetical initial)"
        m = re.search(
            r"(?:Hypothetical\s+)?(?:final\s+)?barrier\s+(?:value|level)\b.{0,200}?"
            r"\(([\d.]+)\s*%\s*of\s+(?:its|the)\s+(?:hypothetical\s+)?initial",
            self.clean[:80000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # Coupon barrier level/value
        m = re.search(
            r"Coupon\s+barrier\s+(?:value|level)[:\s]*[\d,.]*,?\s*\(?([\d.]+)\s*%\s*of\s+(?:its|the)\s+(?:hypothetical\s+)?initial",
            self.clean[:60000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Downside threshold price: $X, Y% of the initial share price"
        m = re.search(
            r"(?:Downside\s+)?[Tt]hreshold\s+price[:\s]*\$[\d,.]+,?\s*([\d.]+)\s*%\s*of",
            self.clean[:60000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Buffer amount/percentage: X%" -> barrier = (100 - buffer) / 100
        m = re.search(
            r"Buffer\s+(?:amount|percentage)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            buf = float(m.group(1))
            if 1 <= buf <= 50:
                return (100 - buf) / 100.0

        # Generic barrier value/level in table
        m = re.search(
            r"(?<!coupon\s)barrier\s+(?:value|level)\b.{0,200}?\(([\d.]+)\s*%\s*of\s+(?:its|the)",
            self.clean[:80000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Final buffer value: X, Y% of initial"
        m = re.search(
            r"final\s+buffer\s+value[:\s]*[\d,.]*,?\s*([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:initial|hypothetical)",
            self.clean[:80000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # Buffer value with parens
        m = re.search(
            r"final\s+buffer\s+value\b.{0,200}?\(([\d.]+)\s*%\s*of\s+(?:its|the)",
            self.clean[:80000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Conversion Price: $X, Y% of Initial" (airbag notes)
        m = re.search(
            r"Conversion\s+Price[:\s]*\$[\d,.]+,?\s*([\d.]+)\s*%\s*of\s+(?:the\s+)?Initial",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        return super().extract_barrier_level()


class HSBCParser(BaseProductParser):
    """HSBC USA Inc (CIK 83246).

    Coupon: may use "X% per quarter" - must annualize.
    """

    def extract_coupon_rate(self) -> float | None:
        # "X% per quarter (equivalent to Y% per annum)" - prefer annualized
        m = re.search(
            r"([\d.]+)\s*%\s*per\s+(?:quarter|month|semi-?annual\s+period).*?"
            r"(?:equivalent\s+to\s+|equal\s+to\s+)([\d.]+)\s*%\s*per\s+annum",
            self.clean[:30000], re.IGNORECASE,
        )
        if m:
            return float(m.group(2)) / 100.0

        # "X% per quarter" without explicit annual equivalent
        m = re.search(
            r"(?:Contingent\s+(?:Interest|Coupon)\s+Rate|(?:Fixed\s+)?Coupon\s+Rate)[:\s]*([\d.]+)\s*%\s*per\s+(quarter|month|annum|semi-?annual)",
            self.clean[:20000], re.IGNORECASE,
        )
        if m:
            rate = float(m.group(1))
            period = m.group(2).lower()
            mult = 4  # default quarterly
            if "month" in period:
                mult = 12
            elif "semi" in period:
                mult = 2
            elif "annum" in period:
                mult = 1
            return round(rate * mult / 100.0, 4)

        return super().extract_coupon_rate()

    def extract_notional(self) -> float | None:
        m = re.search(
            r"HSBC\s+(?:USA|Bank\s+USA)[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class WellsFargoParser(BaseProductParser):
    """Wells Fargo & Company (CIK 72971)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Wells\s+Fargo[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()

    def extract_barrier_level(self) -> float | None:
        # "Buffer Amount/Level: X%"
        m = re.search(
            r"Buffer\s+(?:Amount|Level)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "Trigger/Knock-in Level: X%"
        m = re.search(
            r"(?:Trigger|Knock-?in)\s+Level[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        return super().extract_barrier_level()


class DeutscheBankParser(BaseProductParser):
    """Deutsche Bank AG (CIK 1159508)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Deutsche\s+Bank[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class RBCParser(BaseProductParser):
    """Royal Bank of Canada (CIK 1000275).

    Coupon: RBC uses "Contingent Coupon Geared Buffer Notes" with rate info
    in key terms section.
    """

    def extract_coupon_rate(self) -> float | None:
        # RBC: "Contingent Coupon Rate (per annum): X%"
        m = re.search(
            r"Contingent\s+Coupon\s+Rate\s*\(per\s+annum\)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        # Try base parser (includes new contingent payment patterns)
        result = super().extract_coupon_rate()
        if result:
            return result

        # "contingent coupon... at a rate of X% per annum"
        m = re.search(
            r"contingent\s+coupon.{0,80}?(?:rate\s+of\s+|at\s+)([\d.]+)\s*%\s*(?:per\s+annum)?",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            return float(m.group(1)) / 100.0

        return None

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Royal\s+Bank\s+of\s+Canada[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class NomuraParser(BaseProductParser):
    """Nomura (CIK 1163653 / 1383951)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Nomura[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class BMOParser(BaseProductParser):
    """Bank of Montreal (CIK 927971)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Bank\s+of\s+Montreal[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class CreditSuisseParser(BaseProductParser):
    """Credit Suisse AG (CIK 1053092)."""
    pass


class TDBankParser(BaseProductParser):
    """Toronto-Dominion Bank (CIK 947263)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"(?:Toronto-Dominion|TD\s+Bank)[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class ScotiabankParser(BaseProductParser):
    """Bank of Nova Scotia (CIK 9631)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"(?:Bank\s+of\s+Nova\s+Scotia|Scotiabank)[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class CIBCParser(BaseProductParser):
    """Canadian Imperial Bank of Commerce (CIK 1045520).

    Maturity: Header table flattens to "Maturity Date [pricing_date] [settlement_date] [maturity_date]".
    Base parser grabs the first date (pricing date). Use "due" pattern which is always correct.
    """

    def extract_maturity_date(self) -> date | None:
        m = re.search(
            r"due\s+(\w+\s+\d{1,2},?\s+\d{4})",
            self.clean[:15000], re.IGNORECASE,
        )
        if m:
            return parse_date_text(m.group(1))
        return super().extract_maturity_date()

    def extract_notional(self) -> float | None:
        m = re.search(
            r"(?:Canadian\s+Imperial|CIBC)[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


class JefferiesParser(BaseProductParser):
    """Jefferies Group Capital Finance Inc (CIK 1665340)."""

    def extract_notional(self) -> float | None:
        m = re.search(
            r"Jefferies[^$]*\$([\d,]+(?:\.\d+)?)",
            self.clean[:10000], re.IGNORECASE,
        )
        if m:
            val = self._parse_dollar(m.group(1))
            if val and val > 10_000:
                return val
        return super().extract_notional()


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------

PARSER_REGISTRY: dict[str, type[BaseProductParser]] = {
    "1665650": JPMorganParser,
    "1419828": GoldmanSachsParser,
    "1666268": MorganStanleyParser,
    "1114446": UBSParser,
    "312070": BarclaysParser,
    "1682472": BofAParser,
    "200245": CitigroupParser,
    "83246": HSBCParser,
    "72971": WellsFargoParser,
    "1159508": DeutscheBankParser,
    "1000275": RBCParser,
    "1163653": NomuraParser,
    "927971": BMOParser,
    "1053092": CreditSuisseParser,
    "947263": TDBankParser,
    "9631": ScotiabankParser,
    "1045520": CIBCParser,
    "1665340": JefferiesParser,
    "1383951": NomuraParser,
}


def get_parser(cik: str, html: str, issuer_name: str = "") -> BaseProductParser:
    """Return the appropriate parser for a CIK."""
    cik_stripped = str(int(cik))
    parser_cls = PARSER_REGISTRY.get(cik_stripped, BaseProductParser)
    return parser_cls(html, issuer_name)
