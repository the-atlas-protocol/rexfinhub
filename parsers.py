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
            "is_preliminary": self.extract_is_preliminary(),
            "underlier_count": self.extract_underlier_count(),
            "notional_amount": self.extract_notional(),
            "denomination": self.extract_denomination(),
            "maturity_date": self.extract_maturity_date(),
            "coupon_rate": self.extract_coupon_rate(),
            "coupon_type": self.extract_coupon_type(),
            "coupon_frequency": self.extract_coupon_frequency(),
            "barrier_level": self.extract_barrier_level(),
            "barrier_type": self.extract_barrier_type(),
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

        # Strategy 3: 9-char alphanumeric near "CUSIP" keyword
        idx = self.clean.upper().find("CUSIP")
        if idx >= 0:
            for cm in re.finditer(r"([A-Z0-9]{9})", self.clean[idx:idx+200]):
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
        if "auto callable" in text or "autocallable" in text or "auto-callable" in text:
            return "autocallable"
        if "reverse convertible" in text:
            return "reverse_convertible"
        if re.search(r"(?:callable|redeemable)\s+(?:contingent|barrier)", text):
            return "autocallable"
        if "range accrual" in text:
            return "range_accrual"
        if re.search(r"digital\s+(?:note|return|coupon)", text):
            return "digital"
        if re.search(r"(?:buffered|buffer)\s+(?:note|return|enhanced)", text):
            return "buffered"
        if "principal protected" in text or "principal-protected" in text:
            return "principal_protected"
        if re.search(r"accelerated\s+return", text):
            return "accelerated_return"
        if re.search(r"(?:leveraged|enhanced)\s+(?:note|return|upside)", text):
            return "leveraged"
        if "fixed-to-floating" in text or "fixed to floating" in text:
            return "fixed_to_floating"
        if re.search(r"callable\s+(?:note|fixed|step)", text):
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
            # Count underlier names in key terms
            tickers = re.findall(r"\b[A-Z]{1,5}\b", self.clean[:5000])
            # rough heuristic
            return None
        if "basket" in text:
            return None  # basket = multiple but unknown
        return 1

    # --- Maturity ---

    def extract_maturity_date(self) -> date | None:
        m = re.search(
            r"(?:due|Maturity\s+Date)[:\s]*(\w+\s+\d{1,2},?\s+\d{4})",
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

        # "$X per Note" with frequency conversion
        m = re.search(
            r"Contingent\s+(?:Coupon|Interest)\s+Payment[:\s]*\$([\d.]+)\s*per\s+(?:Note|\$1,?000)",
            self.clean[:30000], re.IGNORECASE,
        )
        if m:
            payment = float(m.group(1))
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
        # "Barrier/Trigger/Knock-in Level: X%"
        m = re.search(
            r"(?:Barrier|Trigger|Knock-?in)\s+(?:Level|Value|Price)[:\s]*([\d.]+)\s*%",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
            val = float(m.group(1))
            if 10 < val < 100:
                return val / 100.0

        # "X% of (the) Initial/Starting Value/Price/Level"
        m = re.search(
            r"([\d.]+)\s*%\s*of\s+(?:the\s+)?(?:its\s+)?(?:Initial|Starting|Hypothetical\s+Initial)",
            self.clean[:40000], re.IGNORECASE,
        )
        if m:
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
                   "coupon_rate", "barrier_level", "underlier_count", "denomination"]
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
    """

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
    """Morgan Stanley Finance LLC (CIK 1666268)."""

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
    """Barclays Bank PLC (CIK 312070)."""

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
    """BofA Finance LLC (CIK 1682472)."""

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
    """Royal Bank of Canada (CIK 1000275)."""

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
