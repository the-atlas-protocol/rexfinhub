"""Issuer CIK registry and SEC configuration."""

USER_AGENT = "REX-StructuredNotes/1.0 (relasmar@rexfin.com)"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik_padded}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
FORM_TYPE = "424B2"  # Structured note pricing supplements
DB_PATH = "data/structured_notes.db"
CACHE_DIR = "http_cache"

# CIK -> (short_name, full_name)
ISSUERS: dict[str, tuple[str, str]] = {
    "1665650": ("JPMorgan", "JPMorgan Chase Financial Company LLC"),
    "1419828": ("Goldman Sachs", "GS Finance Corp"),
    "1666268": ("Morgan Stanley", "Morgan Stanley Finance LLC"),
    "1114446": ("UBS", "UBS AG"),
    "312070": ("Barclays", "Barclays Bank PLC"),
    "1682472": ("Bank of America", "BofA Finance LLC"),
    "200245": ("Citigroup", "Citigroup Global Markets Holdings Inc"),
    "83246": ("HSBC", "HSBC USA Inc"),
    "72971": ("Wells Fargo", "Wells Fargo & Company"),
    "1159508": ("Deutsche Bank", "Deutsche Bank AG"),
    "1000275": ("RBC", "Royal Bank of Canada"),
    "1163653": ("Nomura", "Nomura Securities International Inc"),
    "927971": ("BMO", "Bank of Montreal"),
    "1053092": ("Credit Suisse", "Credit Suisse AG"),
    "947263": ("TD Bank", "Toronto-Dominion Bank"),
    "9631": ("Scotiabank", "Bank of Nova Scotia"),
    "1045520": ("CIBC", "Canadian Imperial Bank of Commerce"),
    "1665340": ("Jefferies", "Jefferies Group Capital Finance Inc"),
    "1383951": ("Nomura Intl", "Nomura International Funding"),
}
