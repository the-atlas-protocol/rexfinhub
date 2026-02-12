"""Screener configuration: scoring weights, thresholds, file paths."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "SCREENER" / "datatest.xlsx"

# --- Scoring weights (data-driven from correlation analysis, n=64 underliers) ---
# De-duplicated factor groups removing collinear metrics.
# Short Interest Ratio is INVERTED (lower = better for product demand).
SCORING_WEIGHTS = {
    "Turnover / Traded Value": 0.25,  # r_log=0.742 - strongest predictor
    "Total OI": 0.20,                 # r_log=0.646 - options demand (both sides)
    "Mkt Cap": 0.20,                  # r_log=0.612 - market viability
    "Twitter Positive Sentiment Count": 0.15,  # r_log=0.537 - retail demand
    "Short Interest Ratio": 0.10,     # r_log=-0.499 - contrarian (inverted)
    "Last Price": 0.10,               # r_log=0.491 - institutional interest
}

# Factors where LOWER = BETTER (percentile ranked ascending)
INVERTED_FACTORS = {"Short Interest Ratio"}

# --- Threshold filters ---
THRESHOLD_FILTERS = {
    "min_mkt_cap": 10_000,  # $10B in millions
}

# --- Competitive density categories ---
DENSITY_UNCONTESTED = "Uncontested"
DENSITY_EARLY = "Early Stage"
DENSITY_COMPETITIVE = "Competitive"
DENSITY_CROWDED = "Crowded"

# --- Leverage types & directions ---
LEVERAGE_TYPES = ["2x", "3x", "1x", "4x"]
DIRECTIONS = ["Long", "Short", "Tactical"]

# --- PDF styling ---
PDF_COLORS = {
    "primary": "#1a1a2e",
    "secondary": "#0984e3",
    "text": "#000000",
    "light_bg": "#f5f7fa",
    "border": "#cccccc",
}
