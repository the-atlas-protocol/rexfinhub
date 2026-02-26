"""Market data pipeline configuration."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Data file resolution
# ---------------------------------------------------------------------------
# Primary: OneDrive MASTER Data folder (synced, Ryu updates here)
_ONEDRIVE_BBG = Path(
    r"C:\Users\RyuEl-Asmar\REX Financial LLC"
    r"\REX Financial LLC - Rex Financial LLC"
    r"\Product Development\MasterFiles\MASTER Data\bbg_data.xlsx"
)
_FALLBACK_BBG = PROJECT_ROOT / "data" / "DASHBOARD" / "bbg_data.xlsx"
_LEGACY_ONEDRIVE = Path(
    r"C:\Users\RyuEl-Asmar\REX Financial LLC"
    r"\REX Financial LLC - Rex Financial LLC"
    r"\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx"
)
_LEGACY_FALLBACK = PROJECT_ROOT / "data" / "DASHBOARD" / "The Dashboard.xlsx"

# Resolution order: OneDrive bbg_data -> local fallback -> legacy OneDrive -> legacy local
if _ONEDRIVE_BBG.exists():
    DATA_FILE = _ONEDRIVE_BBG
elif _FALLBACK_BBG.exists():
    DATA_FILE = _FALLBACK_BBG
elif _LEGACY_ONEDRIVE.exists():
    DATA_FILE = _LEGACY_ONEDRIVE
else:
    DATA_FILE = _LEGACY_FALLBACK

RULES_DIR = PROJECT_ROOT / "data" / "rules"
EXPORT_DIR = PROJECT_ROOT / "data" / "DASHBOARD" / "exports"
HISTORY_DIR = PROJECT_ROOT / "data" / "DASHBOARD" / "history"
LAST_RUN_FILE = PROJECT_ROOT / "data" / "DASHBOARD" / ".last_market_run.json"

# ---------------------------------------------------------------------------
# Top-N issuers for timeseries include rules (auto-derived)
# ---------------------------------------------------------------------------
TOP_N_ISSUERS = 8

# ---------------------------------------------------------------------------
# Input Excel sheet names
# ---------------------------------------------------------------------------
# New canonical format (bbg_data.xlsx)
SHEET_W1 = "w1"            # base data (22 cols)
SHEET_W2 = "w2"            # metrics (11 cols, includes Fund Name to drop)
SHEET_W3 = "w3"            # returns (11 cols, includes Fund Name to drop)
SHEET_W4 = "w4"            # flows + AUM history (47 cols)
SHEET_S1 = "s1"            # stock data (29 cols)
SHEET_MKT_STATUS = "mkt_status"  # reference (16 rows)

BBG_SHEETS = [SHEET_W1, SHEET_W2, SHEET_W3, SHEET_W4, SHEET_S1, SHEET_MKT_STATUS]

# Legacy 5-sheet format
SHEET_ETP_BASE = "etp_base"
SHEET_ETP_METRICS = "etp_metrics"
SHEET_ETP_RETURNS = "etp_returns"
SHEET_ETP_FLOWS = "etp_flows"
SHEET_STOCK_DATA = "stock_data"

INPUT_SHEETS = [SHEET_ETP_BASE, SHEET_ETP_METRICS, SHEET_ETP_RETURNS, SHEET_ETP_FLOWS, SHEET_STOCK_DATA]

# ---------------------------------------------------------------------------
# Column rename maps (BBG abbreviated -> canonical snake_case)
# ---------------------------------------------------------------------------
W1_COL_MAP = {
    "Ticker": "ticker", "Fund Name": "fund_name", "Issuer": "issuer",
    "Exchange": "listed_exchange", "Inception Dt": "inception_date",
    "Fund Type": "fund_type", "Asset Class": "asset_class_focus",
    "Reg Structure": "regulatory_structure",
    "Idx Wt Mthd": "index_weighting_methodology",
    "Underlying Index": "underlying_index", "Single Stock": "is_singlestock",
    "Is Active": "is_active", "Deriv Based": "uses_derivatives",
    "Swaps Based": "uses_swaps", "40 Act": "is_40act",
    "Use Leverage": "uses_leverage", "Leverage %": "leverage_amount",
    "Def Outcome Typ": "outcome_type", "Is Crypto": "is_crypto",
    "CUSIP": "cusip", "Market Status": "market_status",
    "Des": "fund_description",
}

W2_COL_MAP = {
    "Ticker": "ticker",
    "Exp Ratio": "expense_ratio", "Mgmt Fee": "management_fee",
    "Avg Bid Ask Sprd": "average_bidask_spread",
    "NAV Track Err": "nav_tracking_error", "% Prem": "percentage_premium",
    "52W Avg % Prem": "average_percent_premium_52week",
    "Avg Vol 30D": "average_vol_30day",
    "% Short Interest": "percent_short_interest",
    "Open Interest": "open_interest",
}

W3_COL_MAP = {
    "Ticker": "ticker",
    "1D TR": "total_return_1day", "1W TR": "total_return_1week",
    "1M TR": "total_return_1month", "3M TR": "total_return_3month",
    "6M TR": "total_return_6month", "YTD TR": "total_return_ytd",
    "1Y TR": "total_return_1year", "3Y TR": "total_return_3year",
    "Ann Yield": "annualized_yield",
}

W4_FLOW_COL_MAP = {
    "Ticker": "ticker",
    "1D Flow": "fund_flow_1day", "1W Flow": "fund_flow_1week",
    "1M Flow": "fund_flow_1month", "3M Flow": "fund_flow_3month",
    "6M Flow": "fund_flow_6month", "YTD Flow": "fund_flow_ytd",
    "1Y Flow": "fund_flow_1year", "3Y Flow": "fund_flow_3year",
}
# W4 AUM columns: positional (indices 10-46 after ticker+name+8 flows)
# Col 10 = aum (current), cols 11-46 = aum_1 through aum_36

# ---------------------------------------------------------------------------
# Column definitions (canonical snake_case names -- post-rename)
# ---------------------------------------------------------------------------
BASE_FIELDS = [
    "ticker", "fund_name", "issuer", "listed_exchange", "inception_date",
    "fund_type", "asset_class_focus", "regulatory_structure",
    "index_weighting_methodology", "underlying_index", "is_singlestock",
    "is_active", "uses_derivatives", "uses_swaps", "is_40act",
    "uses_leverage", "leverage_amount", "outcome_type", "is_crypto",
    "cusip", "market_status", "fund_description",
]

W2_FIELDS = [
    "expense_ratio", "management_fee", "average_bidask_spread",
    "nav_tracking_error", "percentage_premium",
    "average_percent_premium_52week", "average_vol_30day",
    "percent_short_interest", "open_interest",
]

W3_FIELDS = [
    "total_return_1day", "total_return_1week", "total_return_1month",
    "total_return_3month", "total_return_6month", "total_return_ytd",
    "total_return_1year", "total_return_3year", "annualized_yield",
]

W4_FIELDS = [
    "fund_flow_1day", "fund_flow_1week", "fund_flow_1month",
    "fund_flow_3month", "fund_flow_6month", "fund_flow_ytd",
    "fund_flow_1year", "fund_flow_3year",
    "aum",
] + [f"aum_{i}" for i in range(1, 37)]

# All ETP fields combined (flat, no prefix)
ALL_ETP_FIELDS = BASE_FIELDS + W2_FIELDS + W3_FIELDS + W4_FIELDS

# ---------------------------------------------------------------------------
# Category display names
# ---------------------------------------------------------------------------
CAT_LI_SS = "Leverage & Inverse - Single Stock"
CAT_LI_INDEX = "Leverage & Inverse - Index/Basket/ETF Based"
CAT_LI_OTHER = "Leverage & Inverse - Unknown/Miscellaneous"
CAT_CC_SS = "Income - Single Stock"
CAT_CC_INDEX = "Income - Index/Basket/ETF Based"
CAT_CC_OTHER = "Income - Unknown/Miscellaneous"
CAT_CRYPTO = "Crypto"
CAT_DEFINED = "Defined Outcome"
CAT_THEMATIC = "Thematic"

ALL_CATEGORIES = [
    CAT_LI_SS, CAT_LI_INDEX, CAT_CRYPTO,
    CAT_CC_SS, CAT_CC_INDEX, CAT_DEFINED, CAT_THEMATIC,
]

# etp_category -> set of possible category_display values
ETP_TO_CATS = {
    "LI": {CAT_LI_SS, CAT_LI_INDEX, CAT_LI_OTHER},
    "CC": {CAT_CC_SS, CAT_CC_INDEX, CAT_CC_OTHER},
    "Crypto": {CAT_CRYPTO},
    "Defined": {CAT_DEFINED},
    "Thematic": {CAT_THEMATIC},
}

# Reverse: category_display -> etp_category
CAT_TO_ETP = {}
for _etp, _cats in ETP_TO_CATS.items():
    for _cat in _cats:
        CAT_TO_ETP[_cat] = _etp

# ---------------------------------------------------------------------------
# Category attribute columns per etp_category
# ---------------------------------------------------------------------------
LI_ATTR_COLS = [
    "map_li_category", "map_li_subcategory", "map_li_direction",
    "map_li_leverage_amount", "map_li_underlier",
]

CC_ATTR_COLS = ["map_cc_underlier", "map_cc_index"]

CRYPTO_ATTR_COLS = ["map_crypto_is_spot", "map_crypto_underlier"]

DEFINED_ATTR_COLS = ["map_defined_category"]

THEMATIC_ATTR_COLS = ["map_thematic_category"]

ALL_ATTR_COLS = LI_ATTR_COLS + CC_ATTR_COLS + CRYPTO_ATTR_COLS + DEFINED_ATTR_COLS + THEMATIC_ATTR_COLS

# Map etp_category -> its attribute columns
CATEGORY_ATTR_MAP = {
    "LI": LI_ATTR_COLS,
    "CC": CC_ATTR_COLS,
    "Crypto": CRYPTO_ATTR_COLS,
    "Defined": DEFINED_ATTR_COLS,
    "Thematic": THEMATIC_ATTR_COLS,
}

# ---------------------------------------------------------------------------
# Rule CSV file names
# ---------------------------------------------------------------------------
RULE_FILES = {
    "fund_mapping": "fund_mapping.csv",
    "issuer_mapping": "issuer_mapping.csv",
    "exclusions": "exclusions.csv",
    "rex_funds": "rex_funds.csv",
    "market_status": "market_status.csv",
    "attributes_LI": "attributes_LI.csv",
    "attributes_CC": "attributes_CC.csv",
    "attributes_Crypto": "attributes_Crypto.csv",
    "attributes_Defined": "attributes_Defined.csv",
    "attributes_Thematic": "attributes_Thematic.csv",
}

# ---------------------------------------------------------------------------
# Output column prefixes (for q_master_data compatibility)
# ---------------------------------------------------------------------------
W2_PREFIX = "t_w2."
W3_PREFIX = "t_w3."
W4_PREFIX = "t_w4."
ATTR_PREFIX = "q_category_attributes."

# Enrichment columns added by pipeline
ENRICHMENT_COLS = [
    "etp_category", "issuer_nickname", "category_display",
    "issuer_display", "is_rex", "fund_category_key",
]

# ---------------------------------------------------------------------------
# Expanded strategy taxonomy (replaces 5-category etp_category system)
# ---------------------------------------------------------------------------
STRATEGIES = [
    "Leveraged & Inverse",
    "Income / Covered Call",
    "Crypto",
    "Defined Outcome",
    "Thematic",
    "Broad Beta",
    "Fixed Income",
    "Sector",
    "Commodity",
    "Currency",
    "International",
    "Alternative",
    "Multi-Asset",
    "Unclassified",
]

# Map old etp_category values to new strategy names
ETP_CATEGORY_TO_STRATEGY = {
    "LI": "Leveraged & Inverse",
    "CC": "Income / Covered Call",
    "Crypto": "Crypto",
    "Defined": "Defined Outcome",
    "Thematic": "Thematic",
}

# Universal attribute keys (replace per-category map_* columns)
ATTRIBUTE_KEYS = [
    "direction",           # Bull, Bear, Neutral
    "leverage_amount",     # 1x, 1.5x, 2x, 3x, -1x, -2x, -3x
    "underlier",           # TSLA, SPY, Gold, Bitcoin, etc.
    "income_strategy",     # Covered Call, 0DTE, Dividend, Premium, Buy-Write, Autocallable
    "duration",            # Ultra Short, Short, Intermediate, Long
    "credit_quality",      # Treasury, IG, HY, Municipal, Corporate, Convertible, MBS, TIPS
    "geography",           # US, Japan, China, Europe, EM, International Developed, Global
    "sector",              # Technology, Healthcare, Financials, Energy, etc.
    "market_cap",          # Large Cap, Mid Cap, Small Cap, All Cap
    "theme",               # AI & Robotics, Clean Energy, Cybersecurity, etc.
    "outcome_type",        # Buffer, Floor, Accelerator, Ladder
    "commodity_type",      # Gold, Silver, Oil, Natural Gas, Agriculture, Base Metals
    "crypto_type",         # Spot, Index/Basket
    "sub_category",        # Free-form refinement
]

# Confidence levels for auto-classification
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
