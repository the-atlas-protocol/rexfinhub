"""Shared fund filtering patterns for excluding mutual fund share classes."""

# SQL LIKE patterns for mutual fund share classes.
# Mutual funds have share class designations (Class A, Class I, Institutional Class, etc.)
# ETFs never have share class designations - they trade as single ticker per fund.
MUTUAL_FUND_EXCLUSIONS = [
    # "Class X" anywhere in name - covers all share class letters
    "%Class A%",
    "%Class B%",
    "%Class C%",
    "%Class D%",
    "%Class I%",
    "%Class K%",
    "%Class N%",
    "%Class Q%",
    "%Class R%",
    "%Class T%",
    "%Class Y%",
    "%Class Z%",
    # Numbered share classes
    "%Class 1%",
    "%Class 2%",
    "%Class 3%",
    "%Class 4%",
    "%Class 5%",
    # Named share classes
    "%Investor Class%",
    "%Institutional Class%",
    "%Advisor Class%",
    "%Service Class%",
    "%Retail Class%",
    "%Founders Class%",
]
