"""Per-file schema definitions for rules CSV editor."""

from dataclasses import dataclass, field


@dataclass
class ColumnDef:
    name: str
    required: bool = False
    choices: list[str] | None = None  # None = free-form text
    dtype: str = "str"  # str, float, int
    description: str = ""


@dataclass
class FileSchema:
    filename: str
    label: str
    group: str  # Core Mapping / Attributes / REX / Other
    primary_key: list[str]
    columns: list[ColumnDef]
    description: str = ""


# -- Valid values for enum columns --

# Expanded category list: legacy short codes + new strategy names
ETP_CATEGORIES = [
    "LI", "CC", "Crypto", "Defined", "Thematic",
    "Broad Beta", "Fixed Income", "Sector", "Commodity",
    "International", "Alternative", "Multi-Asset", "Currency",
]

CATEGORY_SOURCES = ["manual", "auto"]

LI_CATEGORIES = [
    "Commodity", "Crypto", "Currency", "Equity", "Fixed Income",
    "Stacked Returns", "Volatility",
]

LI_SUBCATEGORIES = [
    "Airlines", "Auto", "Broad Market", "Closed-End", "Dividend",
    "Energy", "Factor", "Financials", "Gold", "Index/Basket/ETF Based",
    "MLP", "Miscellaneous", "Preferred", "Real Estate", "Single Stock",
    "Tech", "Travel", "VIX",
]

LI_DIRECTIONS = ["Long", "Short", "Tactical"]

CC_TYPES = ["Synthetic", "Traditional"]

CC_CATEGORIES = [
    "Autocallable", "Broad Beta", "Commodity", "Crypto", "Energy",
    "Fixed Income", "Real Estate", "Sector", "Single Stock",
    "Small Caps", "Tech",
]

CRYPTO_IS_SPOT = [
    "Derivatives-based; defined outcome",
    "Derivatives-based; futures-based",
    "Derivatives-based; income",
    "Derivatives-based; leveraged",
    "Equity",
    "Hybrid (spot/multi + derivatives)",
    "Spot Multi-asset; active",
    "Spot Multi-asset; passive",
    "Spot Multi-asset; thematic",
    "Spot Single Asset",
]

DEFINED_CATEGORIES = [
    "Accelerator", "Autocallable", "Barrier", "Buffer", "Defined Risk",
    "Defined Volatility", "Dual Buffer", "Floor", "Hedged Equity",
    "Ladder", "Outcome", "Shield",
]

THEMATIC_CATEGORIES = [
    "5G", "Agriculture", "Artificial Intelligence", "Blockchain & Crypto",
    "Cannabis and Psychedelics", "Clean Energy", "Cloud Computing",
    "Consumer", "Corporate Culture", "Cybersecurity", "Defense", "Drones",
    "E-Commerce", "EM Tech", "Electric Car & Battery", "Environment",
    "FinTech", "Future of Food", "Healthcare", "Housing", "IPO & SPAC",
    "Inflation", "Infrastructure", "Innovation", "Low Carbon",
    "Metaverse & Video Gaming", "Natural Resources", "Nuclear",
    "Quantum Computing", "Robotics & Automation", "Space",
    "Sports & Esports", "Strategy", "Tech & Communications",
    "Transition Metals", "Travel, Vacation & Leisure", "Water",
]

REX_SUITES = [
    "Autocallable", "Crypto", "Equity Premium Income", "Growth & Income",
    "IncomeMax", "London", "MicroSectors", "Osprey", "T-Bill", "T-REX",
    "Thematic",
]

# -- Schema definitions for all 12 files --

SCHEMAS: dict[str, FileSchema] = {}


def _register(s: FileSchema):
    SCHEMAS[s.filename] = s


# --- Core Mapping ---

_register(FileSchema(
    filename="fund_mapping.csv",
    label="Fund Mapping",
    group="Core Mapping",
    primary_key=["ticker", "etp_category"],
    description="Maps every ETP ticker to its category. Multi-category tickers have multiple rows.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("etp_category", required=True, choices=ETP_CATEGORIES),
        ColumnDef("is_primary", dtype="int", description="1 = primary category for this ticker"),
        ColumnDef("source", choices=CATEGORY_SOURCES, description="manual or auto"),
    ],
))

_register(FileSchema(
    filename="issuer_mapping.csv",
    label="Issuer Mapping",
    group="Core Mapping",
    primary_key=["etp_category", "issuer"],
    description="Maps Bloomberg issuer names to display nicknames per category.",
    columns=[
        ColumnDef("etp_category", required=True, choices=ETP_CATEGORIES),
        ColumnDef("issuer", required=True),
        ColumnDef("issuer_nickname", required=True),
    ],
))

_register(FileSchema(
    filename="exclusions.csv",
    label="Exclusions",
    group="Core Mapping",
    primary_key=["ticker", "etp_category"],
    description="Tickers to exclude from a specific category's analysis.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("etp_category", required=True, choices=ETP_CATEGORIES),
    ],
))

_register(FileSchema(
    filename="market_status.csv",
    label="Market Status Codes",
    group="Core Mapping",
    primary_key=["code"],
    description="Reference table of Bloomberg market status codes.",
    columns=[
        ColumnDef("code", required=True),
        ColumnDef("description", required=True),
    ],
))

# --- Attributes ---

_register(FileSchema(
    filename="attributes_LI.csv",
    label="Attributes: Leveraged & Inverse",
    group="Attributes",
    primary_key=["ticker"],
    description="Category/subcategory/direction/leverage for Leveraged & Inverse ETPs.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("map_li_category", choices=LI_CATEGORIES),
        ColumnDef("map_li_subcategory", choices=LI_SUBCATEGORIES),
        ColumnDef("map_li_direction", choices=LI_DIRECTIONS),
        ColumnDef("map_li_leverage_amount", dtype="float"),
        ColumnDef("map_li_underlier"),
    ],
))

_register(FileSchema(
    filename="attributes_CC.csv",
    label="Attributes: Covered Call",
    group="Attributes",
    primary_key=["ticker"],
    description="Underlier/index/type/category for Covered Call ETPs.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("map_cc_underlier"),
        ColumnDef("map_cc_index"),
        ColumnDef("cc_type", choices=CC_TYPES),
        ColumnDef("cc_category", choices=CC_CATEGORIES),
    ],
))

_register(FileSchema(
    filename="attributes_Crypto.csv",
    label="Attributes: Crypto",
    group="Attributes",
    primary_key=["ticker"],
    description="Spot/derivatives classification and underlier for Crypto ETPs.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("map_crypto_is_spot", choices=CRYPTO_IS_SPOT),
        ColumnDef("map_crypto_underlier"),
    ],
))

_register(FileSchema(
    filename="attributes_Defined.csv",
    label="Attributes: Defined Outcome",
    group="Attributes",
    primary_key=["ticker"],
    description="Category classification for Defined Outcome ETPs.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("map_defined_category", choices=DEFINED_CATEGORIES),
    ],
))

_register(FileSchema(
    filename="attributes_Thematic.csv",
    label="Attributes: Thematic",
    group="Attributes",
    primary_key=["ticker"],
    description="Thematic category for Thematic ETPs.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("map_thematic_category", choices=THEMATIC_CATEGORIES),
    ],
))

# --- REX ---

_register(FileSchema(
    filename="rex_funds.csv",
    label="REX Funds",
    group="REX",
    primary_key=["ticker"],
    description="List of REX-managed fund tickers.",
    columns=[
        ColumnDef("ticker", required=True),
    ],
))

_register(FileSchema(
    filename="rex_suite_mapping.csv",
    label="REX Suite Mapping",
    group="REX",
    primary_key=["ticker"],
    description="Maps REX fund tickers to their product suite.",
    columns=[
        ColumnDef("ticker", required=True),
        ColumnDef("rex_suite", required=True, choices=REX_SUITES),
    ],
))

# --- Other ---

_register(FileSchema(
    filename="competitor_groups.csv",
    label="Competitor Groups",
    group="Other",
    primary_key=["group_name", "rex_ticker", "peer_ticker"],
    description="Peer groupings for competitive flow analysis.",
    columns=[
        ColumnDef("group_name", required=True),
        ColumnDef("rex_ticker", required=True),
        ColumnDef("peer_ticker", required=True),
    ],
))


# -- Helpers --

def get_grouped_files() -> dict[str, list[FileSchema]]:
    """Return schemas grouped by category, in display order."""
    order = ["Core Mapping", "Attributes", "REX", "Other"]
    grouped: dict[str, list[FileSchema]] = {g: [] for g in order}
    for schema in SCHEMAS.values():
        grouped[schema.group].append(schema)
    return grouped


def get_schema(filename: str) -> FileSchema:
    return SCHEMAS[filename]
