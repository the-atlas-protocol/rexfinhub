"""Screener configuration: scoring weights, thresholds, file paths."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "SCREENER" / "data.xlsx"
REPORTS_DIR = PROJECT_ROOT / "reports"

# --- Scoring weights (data-driven from correlation analysis, n=64 underliers) ---
# Dropped: Last Price (irrelevant to 2x product success), Twitter Sentiment (mostly zeros).
# Added: Volatility 30D (retail traders want vol, drives leveraged product demand).
# Short Interest Ratio is INVERTED (lower = better for product demand).
SCORING_WEIGHTS = {
    "Turnover / Traded Value": 0.30,  # r_log=0.742 - strongest predictor
    "Total OI": 0.30,                 # r_log=0.646 - direct options demand signal
    "Mkt Cap": 0.20,                  # r_log=0.612 - market viability / swap support
    "Volatility 30D": 0.10,           # retail traders want vol = leveraged demand
    "Short Interest Ratio": 0.10,     # r_log=-0.499 - contrarian interest (inverted)
}

# Factors where LOWER = BETTER (percentile ranked ascending)
INVERTED_FACTORS = {"Short Interest Ratio"}

# --- Threshold filters ---
THRESHOLD_FILTERS = {
    "min_mkt_cap": 10_000,  # $10B in millions
}

# --- Competitive penalty (applied after base scoring) ---
# Penalize stocks where existing leveraged products have low AUM (market rejection signal).
COMPETITIVE_PENALTY = {
    "rejected_max_aum": 10,       # $10M total AUM
    "rejected_min_age_days": 180,  # 6 months old
    "rejected_penalty": -25,       # points off composite score
    "low_traction_max_aum": 50,   # $50M total AUM
    "low_traction_min_age_days": 365,  # 12 months old
    "low_traction_penalty": -15,   # points off composite score
}

# --- Competitive density categories ---
DENSITY_UNCONTESTED = "Uncontested"
DENSITY_EARLY = "Early Stage"
DENSITY_COMPETITIVE = "Competitive"
DENSITY_CROWDED = "Crowded"

# --- Candidate evaluation pillar thresholds ---
DEMAND_THRESHOLDS = {
    "high_pctl": 75,    # 75th percentile = HIGH demand
    "medium_pctl": 40,  # 40th percentile = MEDIUM demand
}

# --- Leverage types & directions ---
LEVERAGE_TYPES = ["2x", "3x", "1x", "4x"]
DIRECTIONS = ["Long", "Short", "Tactical"]

# --- 3x Report: volatility risk thresholds (implied daily vol %) ---
RISK_THRESHOLDS = {
    "low_max_daily_vol": 3.0,       # < 3% daily vol = LOW risk
    "medium_max_daily_vol": 5.0,    # 3-5% = MEDIUM risk
    "high_max_daily_vol": 8.0,      # 5-8% = HIGH risk, >8% = EXTREME
}

# --- 3x Report: filing score weights (40% fundamentals + 60% market-proven demand) ---
# The 3x filing score rewards stocks with high 2x AUM (proven demand) over pure fundamentals.
# This ensures TSLA ($6.9B 2x AUM) ranks above BABA ($168M).
FILING_SCORE_WEIGHTS = {
    "composite_pctl": 0.40,   # stock fundamentals (OI, turnover, mkt cap, vol, SI)
    "aum_2x_pctl": 0.60,     # market-proven demand from existing 2x products
}

# --- 3x Report: tiering cutoffs for filing recommendations ---
# Targets: 50 Tier 1, 50 Tier 2, 100 Tier 3 = 200 total
TIER_CUTOFFS = {
    "tier_1_min_score": 55,
    "tier_2_min_score": 45,
    "tier_3_min_score": 35,
    "tier_1_count": 50,
    "tier_2_count": 50,
    "tier_3_count": 100,
}

# --- 4x Report: candidate criteria ---
# 4x amplifies daily moves by 4. Candidates must have existing 2x products
# and manageable volatility (daily vol < 20%). Capped at 100 names.
FOUR_X_CRITERIA = {
    "min_2x_aum": 0,           # any stock with existing 2x product(s)
    "max_daily_vol": 20.0,     # daily vol < 20% cap
    "max_candidates": 100,     # cap at 100 names (sorted by 2x AUM)
}

# --- PDF styling ---
PDF_COLORS = {
    "primary": "#1a1a2e",
    "secondary": "#0984e3",
    "text": "#000000",
    "light_bg": "#f5f7fa",
    "border": "#cccccc",
}
