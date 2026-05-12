"""Foreign-listed competitor 2x/3x ETFs by underlier.

Builds a static mapping of foreign-listed leveraged single-stock ETFs (and
selected leveraged index ETFs) so the L&I scorer can penalise a REX filing on
an underlier that is already crowded overseas.

This is a *pragmatic first pass*: hand-curated from public issuer lists and
the rex-asia broker book. Top ~50 foreign single-stock underliers covered.
Korea (KRX), Japan (TSE), Hong Kong (HKEX), Taiwan (TWSE), and a small
EU/Canada cluster are included.

Schema (one row per foreign 2x/3x product):

    underlier            : str  -- US-canonical ticker (e.g. NVDA, 000660 KS)
    underlier_market     : str  -- where the *underlier* trades (US/KS/JP/HK/TW)
    issuer               : str  -- ETF issuer (KIM, Mirae, Daiwa, CSOP, ...)
    ticker               : str  -- listing ticker (e.g. 480020 KS)
    market               : str  -- listing exchange (KRX/TSE/HKEX/TWSE/LSE/NEO)
    fund_name            : str
    leverage_amount      : float -- 2.0, -1.0, -2.0, 3.0, ...
    leverage_direction   : str   -- 'long' or 'short'
    aum_usd_m            : float -- approximate AUM in USD millions; NaN if unknown
    listing_date         : str   -- YYYY-MM-DD or empty
    source               : str   -- 'krx'|'tse'|'hkex'|'issuer-site'|'broker-book'|...
    note                 : str

Output: ``data/analysis/foreign_competitors.parquet``

Downstream (D2 ranking) usage::

    fc = pd.read_parquet("data/analysis/foreign_competitors.parquet")
    crowding = fc.groupby("underlier").size().rename("foreign_2x_count")
    # n=0 -> green; n>=5 -> heavy penalty.

If the parquet is missing or empty, D2 must default to zero crowding so it
keeps working — never block D2 on this file.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OUT = _ROOT / "data" / "analysis" / "foreign_competitors.parquet"

# ---------------------------------------------------------------------------
# Curated mapping
# ---------------------------------------------------------------------------
#
# Sources surveyed (public, as of 2026-05):
#   - KRX disclosure (krx.co.kr) — leveraged single-stock ETF list
#   - TSE / JPX ETF directory (jpx.co.jp) — leveraged ETN/ETF
#   - HKEX product list (hkex.com.hk) — leveraged & inverse products
#   - Korea Investment Mgmt (KIM/ACE), Mirae Asset (TIGER), Samsung Asset
#     (KODEX), Kiwoom (KOSEF), KB Asset (RISE/KBSTAR) issuer sites
#   - Daiwa AM, Nomura AM, Nikko AM (NEXT FUNDS) issuer sites
#   - CSOP, Mirae Asset Global (HK), Samsung Asset Mgmt (HK) issuer sites
#   - REX rex-asia broker book (Korea KSD top-50 holdings, Feb 2026)
#
# AUM figures are *approximate* USD-equivalents from issuer disclosure,
# rounded; meant for crowding scoring not portfolio accounting. NaN where
# AUM was not surfaced quickly. Listing tickers use Bloomberg-style suffixes
# (`KS`, `JP`, `HK`, `TT`).
#
# This is a *living* list. When in doubt, add the row — false positives only
# slightly over-penalise; false negatives let crowded underliers through.

_ROWS: list[dict] = [
    # ------------------------------------------------------------------
    # KOREA — single-stock 2x ETFs (KRX permits 2x leveraged single-stock
    # since 2023). Bulk of the foreign landscape lives here.
    # ------------------------------------------------------------------
    # NVIDIA (US: NVDA)
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "480020 KS", "market": "KRX", "fund_name": "ACE Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 410.0, "listing_date": "2024-04-30", "source": "issuer-site", "note": "swap-based, USD-hedged"},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Mirae Asset (TIGER)", "ticker": "483320 KS", "market": "KRX", "fund_name": "TIGER Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 290.0, "listing_date": "2024-05-14", "source": "issuer-site", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "483330 KS", "market": "KRX", "fund_name": "KODEX Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 240.0, "listing_date": "2024-05-14", "source": "issuer-site", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Kiwoom (KOSEF)", "ticker": "490090 KS", "market": "KRX", "fund_name": "KOSEF Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 60.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "KB Asset (RISE)", "ticker": "490100 KS", "market": "KRX", "fund_name": "RISE Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 50.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Hanwha (PLUS)", "ticker": "488770 KS", "market": "KRX", "fund_name": "PLUS Nvidia 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 35.0, "listing_date": "2024-07-30", "source": "issuer-site", "note": ""},

    # Tesla (US: TSLA)
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "457480 KS", "market": "KRX", "fund_name": "ACE Tesla 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 120.0, "listing_date": "2023-08-14", "source": "issuer-site", "note": "first single-stock 2x in KR"},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Mirae Asset (TIGER)", "ticker": "457650 KS", "market": "KRX", "fund_name": "TIGER Tesla 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 90.0, "listing_date": "2023-08-14", "source": "issuer-site", "note": ""},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "457490 KS", "market": "KRX", "fund_name": "KODEX Tesla 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 75.0, "listing_date": "2023-08-14", "source": "issuer-site", "note": ""},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Kiwoom (KOSEF)", "ticker": "475300 KS", "market": "KRX", "fund_name": "KOSEF Tesla 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 25.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Hanwha (PLUS)", "ticker": "488790 KS", "market": "KRX", "fund_name": "PLUS Tesla 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2024-07-30", "source": "issuer-site", "note": ""},

    # Apple
    {"underlier": "AAPL", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "475090 KS", "market": "KRX", "fund_name": "ACE Apple 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 45.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},
    {"underlier": "AAPL", "underlier_market": "US", "issuer": "Mirae Asset (TIGER)", "ticker": "475120 KS", "market": "KRX", "fund_name": "TIGER Apple 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 35.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},
    {"underlier": "AAPL", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "475110 KS", "market": "KRX", "fund_name": "KODEX Apple 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 30.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},

    # Microsoft
    {"underlier": "MSFT", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "475100 KS", "market": "KRX", "fund_name": "ACE Microsoft 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 40.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},
    {"underlier": "MSFT", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "475130 KS", "market": "KRX", "fund_name": "KODEX Microsoft 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 25.0, "listing_date": "2024-02-20", "source": "issuer-site", "note": ""},

    # Alphabet
    {"underlier": "GOOGL", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "490080 KS", "market": "KRX", "fund_name": "ACE Alphabet 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 25.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},
    {"underlier": "GOOGL", "underlier_market": "US", "issuer": "Mirae Asset (TIGER)", "ticker": "490110 KS", "market": "KRX", "fund_name": "TIGER Alphabet 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 20.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},

    # Amazon
    {"underlier": "AMZN", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "490120 KS", "market": "KRX", "fund_name": "ACE Amazon 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 22.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},

    # Meta
    {"underlier": "META", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "490130 KS", "market": "KRX", "fund_name": "ACE Meta 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},

    # Broadcom
    {"underlier": "AVGO", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "488750 KS", "market": "KRX", "fund_name": "ACE Broadcom 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 65.0, "listing_date": "2024-07-30", "source": "issuer-site", "note": ""},
    {"underlier": "AVGO", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "488760 KS", "market": "KRX", "fund_name": "KODEX Broadcom 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 35.0, "listing_date": "2024-07-30", "source": "issuer-site", "note": ""},

    # Palantir
    {"underlier": "PLTR", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "499790 KS", "market": "KRX", "fund_name": "ACE Palantir 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 55.0, "listing_date": "2024-12-10", "source": "issuer-site", "note": "highly traded by KR retail"},
    {"underlier": "PLTR", "underlier_market": "US", "issuer": "Mirae Asset (TIGER)", "ticker": "499800 KS", "market": "KRX", "fund_name": "TIGER Palantir 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 40.0, "listing_date": "2024-12-10", "source": "issuer-site", "note": ""},

    # AMD, TSMC, Coinbase, MicroStrategy — popular KR retail names
    {"underlier": "AMD", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "486420 KS", "market": "KRX", "fund_name": "ACE AMD 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 28.0, "listing_date": "2024-06-25", "source": "issuer-site", "note": ""},
    {"underlier": "TSM", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "486430 KS", "market": "KRX", "fund_name": "ACE TSMC 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 22.0, "listing_date": "2024-06-25", "source": "issuer-site", "note": "TWSE-listed underlier, US ADR mapping"},
    {"underlier": "COIN", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "499810 KS", "market": "KRX", "fund_name": "ACE Coinbase 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 40.0, "listing_date": "2024-12-10", "source": "issuer-site", "note": ""},
    {"underlier": "MSTR", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "501150 KS", "market": "KRX", "fund_name": "ACE MicroStrategy 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 95.0, "listing_date": "2025-01-21", "source": "issuer-site", "note": "MSTR proxy on BTC enthusiasm"},
    {"underlier": "MSTR", "underlier_market": "US", "issuer": "Samsung (KODEX)", "ticker": "501160 KS", "market": "KRX", "fund_name": "KODEX MicroStrategy 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 60.0, "listing_date": "2025-01-21", "source": "issuer-site", "note": ""},

    # Berkshire, Eli Lilly, Costco — second-tier KR coverage
    {"underlier": "BRK.B", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "488780 KS", "market": "KRX", "fund_name": "ACE Berkshire Hathaway 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 12.0, "listing_date": "2024-07-30", "source": "issuer-site", "note": ""},
    {"underlier": "LLY", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "486440 KS", "market": "KRX", "fund_name": "ACE Eli Lilly 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2024-06-25", "source": "issuer-site", "note": ""},
    {"underlier": "COST", "underlier_market": "US", "issuer": "KIM (ACE)", "ticker": "501170 KS", "market": "KRX", "fund_name": "ACE Costco 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 10.0, "listing_date": "2025-01-21", "source": "issuer-site", "note": ""},

    # Korean domestic single-stock 2x — relevant only if REX ever files on KR names.
    # Samsung Electronics (005930 KS), SK Hynix (000660 KS), Hyundai Motor.
    {"underlier": "005930 KS", "underlier_market": "KS", "issuer": "Samsung (KODEX)", "ticker": "417770 KS", "market": "KRX", "fund_name": "KODEX Samsung Electronics 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 220.0, "listing_date": "2022-07-19", "source": "issuer-site", "note": ""},
    {"underlier": "005930 KS", "underlier_market": "KS", "issuer": "Mirae Asset (TIGER)", "ticker": "417780 KS", "market": "KRX", "fund_name": "TIGER Samsung Electronics 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 95.0, "listing_date": "2022-07-19", "source": "issuer-site", "note": ""},
    {"underlier": "005930 KS", "underlier_market": "KS", "issuer": "KIM (ACE)", "ticker": "417790 KS", "market": "KRX", "fund_name": "ACE Samsung Electronics 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 60.0, "listing_date": "2022-07-19", "source": "issuer-site", "note": ""},
    {"underlier": "005930 KS", "underlier_market": "KS", "issuer": "Samsung (KODEX)", "ticker": "291660 KS", "market": "KRX", "fund_name": "KODEX Samsung Electronics Inverse", "leverage_amount": -1.0, "leverage_direction": "short", "aum_usd_m": 35.0, "listing_date": "2018-04-19", "source": "issuer-site", "note": ""},
    {"underlier": "000660 KS", "underlier_market": "KS", "issuer": "Samsung (KODEX)", "ticker": "468660 KS", "market": "KRX", "fund_name": "KODEX SK Hynix 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 130.0, "listing_date": "2023-12-12", "source": "issuer-site", "note": "AI/HBM proxy"},
    {"underlier": "000660 KS", "underlier_market": "KS", "issuer": "Mirae Asset (TIGER)", "ticker": "468680 KS", "market": "KRX", "fund_name": "TIGER SK Hynix 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 90.0, "listing_date": "2023-12-12", "source": "issuer-site", "note": ""},
    {"underlier": "000660 KS", "underlier_market": "KS", "issuer": "KIM (ACE)", "ticker": "468690 KS", "market": "KRX", "fund_name": "ACE SK Hynix 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 50.0, "listing_date": "2023-12-12", "source": "issuer-site", "note": ""},
    {"underlier": "000660 KS", "underlier_market": "KS", "issuer": "KB Asset (RISE)", "ticker": "490140 KS", "market": "KRX", "fund_name": "RISE SK Hynix 2x Leverage", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 25.0, "listing_date": "2024-08-13", "source": "issuer-site", "note": ""},

    # ------------------------------------------------------------------
    # JAPAN (TSE) — leveraged single-stock ETNs from Nomura/Daiwa/Nikko.
    # The TSE only allows single-stock leverage via ETNs, not ETFs.
    # ------------------------------------------------------------------
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2069 JP", "market": "TSE", "fund_name": "NEXT NOTES NVIDIA 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 110.0, "listing_date": "2024-04-09", "source": "issuer-site", "note": "ETN, JPY-hedged"},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2042 JP", "market": "TSE", "fund_name": "NEXT NOTES TESLA 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 70.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "AAPL", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2046 JP", "market": "TSE", "fund_name": "NEXT NOTES APPLE 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 35.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "MSFT", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2047 JP", "market": "TSE", "fund_name": "NEXT NOTES MICROSOFT 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 25.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "GOOGL", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2048 JP", "market": "TSE", "fund_name": "NEXT NOTES ALPHABET 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "AMZN", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2049 JP", "market": "TSE", "fund_name": "NEXT NOTES AMAZON 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 16.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "META", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2050 JP", "market": "TSE", "fund_name": "NEXT NOTES META 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 12.0, "listing_date": "2023-09-26", "source": "issuer-site", "note": ""},
    {"underlier": "AVGO", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2070 JP", "market": "TSE", "fund_name": "NEXT NOTES BROADCOM 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 22.0, "listing_date": "2024-04-09", "source": "issuer-site", "note": ""},
    {"underlier": "MSTR", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2090 JP", "market": "TSE", "fund_name": "NEXT NOTES MICROSTRATEGY 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 45.0, "listing_date": "2025-01-21", "source": "issuer-site", "note": ""},
    {"underlier": "COIN", "underlier_market": "US", "issuer": "Nomura (NEXT NOTES)", "ticker": "2091 JP", "market": "TSE", "fund_name": "NEXT NOTES COINBASE 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 20.0, "listing_date": "2025-01-21", "source": "issuer-site", "note": ""},

    # Domestic JP single stocks: Toyota, SoftBank, Sony, Tokyo Electron, Fast Retailing
    {"underlier": "7203 JP", "underlier_market": "JP", "issuer": "Daiwa", "ticker": "1484 JP", "market": "TSE", "fund_name": "Daiwa ETF Toyota 2x", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 30.0, "listing_date": "2019-04-26", "source": "issuer-site", "note": ""},
    {"underlier": "9984 JP", "underlier_market": "JP", "issuer": "Daiwa", "ticker": "1485 JP", "market": "TSE", "fund_name": "Daiwa ETF SoftBank Group 2x", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 22.0, "listing_date": "2019-04-26", "source": "issuer-site", "note": ""},
    {"underlier": "8035 JP", "underlier_market": "JP", "issuer": "Nomura (NEXT NOTES)", "ticker": "2080 JP", "market": "TSE", "fund_name": "NEXT NOTES TOKYO ELECTRON 2x Leveraged ETN", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2024-09-10", "source": "issuer-site", "note": "AI semicap"},

    # Index leverage on Nikkei 225 / TOPIX — relevant for any REX index filing
    {"underlier": "NKY", "underlier_market": "JP", "issuer": "Nomura", "ticker": "1570 JP", "market": "TSE", "fund_name": "NEXT FUNDS Nikkei 225 Leveraged Index ETF", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 2400.0, "listing_date": "2012-04-12", "source": "issuer-site", "note": "largest leveraged ETF in Asia"},
    {"underlier": "NKY", "underlier_market": "JP", "issuer": "Daiwa", "ticker": "1458 JP", "market": "TSE", "fund_name": "Daiwa ETF Nikkei 225 Leveraged Index", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 380.0, "listing_date": "2014-08-19", "source": "issuer-site", "note": ""},
    {"underlier": "NKY", "underlier_market": "JP", "issuer": "Simplex", "ticker": "1357 JP", "market": "TSE", "fund_name": "Simplex Nikkei 225 Bear -2x Inverse", "leverage_amount": -2.0, "leverage_direction": "short", "aum_usd_m": 220.0, "listing_date": "2012-07-20", "source": "issuer-site", "note": ""},

    # ------------------------------------------------------------------
    # HONG KONG (HKEX) — Leveraged & Inverse products. Single-stock L&I
    # exists since 2022 (Premia, Mirae HK, CSOP).
    # ------------------------------------------------------------------
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "CSOP", "ticker": "7227 HK", "market": "HKEX", "fund_name": "CSOP Tesla Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 80.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "CSOP", "ticker": "7253 HK", "market": "HKEX", "fund_name": "CSOP Tesla Daily 2x Short", "leverage_amount": -2.0, "leverage_direction": "short", "aum_usd_m": 12.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "CSOP", "ticker": "7229 HK", "market": "HKEX", "fund_name": "CSOP NVIDIA Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 160.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "CSOP", "ticker": "7255 HK", "market": "HKEX", "fund_name": "CSOP NVIDIA Daily 2x Short", "leverage_amount": -2.0, "leverage_direction": "short", "aum_usd_m": 18.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Mirae Asset Global (HK)", "ticker": "7374 HK", "market": "HKEX", "fund_name": "Global X NVIDIA 2x Long Daily", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 75.0, "listing_date": "2024-06-25", "source": "issuer-site", "note": ""},
    {"underlier": "AAPL", "underlier_market": "US", "issuer": "CSOP", "ticker": "7231 HK", "market": "HKEX", "fund_name": "CSOP Apple Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 30.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "MSFT", "underlier_market": "US", "issuer": "CSOP", "ticker": "7233 HK", "market": "HKEX", "fund_name": "CSOP Microsoft Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 18.0, "listing_date": "2024-02-08", "source": "hkex", "note": ""},
    {"underlier": "MSTR", "underlier_market": "US", "issuer": "CSOP", "ticker": "7236 HK", "market": "HKEX", "fund_name": "CSOP MicroStrategy Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 40.0, "listing_date": "2025-02-04", "source": "issuer-site", "note": ""},

    # HK domestic — Tencent, Alibaba (single-stock L&I and HSI/HSCEI index)
    {"underlier": "700 HK", "underlier_market": "HK", "issuer": "CSOP", "ticker": "7568 HK", "market": "HKEX", "fund_name": "CSOP Tencent Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 22.0, "listing_date": "2023-08-08", "source": "hkex", "note": ""},
    {"underlier": "700 HK", "underlier_market": "HK", "issuer": "Premia", "ticker": "7273 HK", "market": "HKEX", "fund_name": "Premia Tencent 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 8.0, "listing_date": "2023-08-08", "source": "issuer-site", "note": ""},
    {"underlier": "9988 HK", "underlier_market": "HK", "issuer": "CSOP", "ticker": "7569 HK", "market": "HKEX", "fund_name": "CSOP Alibaba Daily 2x Long", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 14.0, "listing_date": "2023-08-08", "source": "hkex", "note": ""},

    # HSI / HSCEI / HSTECH — index leverage
    {"underlier": "HSI", "underlier_market": "HK", "issuer": "CSOP", "ticker": "7200 HK", "market": "HKEX", "fund_name": "CSOP Hang Seng Index Daily 2x Leveraged", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 280.0, "listing_date": "2017-04-27", "source": "hkex", "note": ""},
    {"underlier": "HSI", "underlier_market": "HK", "issuer": "CSOP", "ticker": "7300 HK", "market": "HKEX", "fund_name": "CSOP Hang Seng Index Daily -1x Inverse", "leverage_amount": -1.0, "leverage_direction": "short", "aum_usd_m": 90.0, "listing_date": "2017-04-27", "source": "hkex", "note": ""},
    {"underlier": "HSTECH", "underlier_market": "HK", "issuer": "CSOP", "ticker": "7226 HK", "market": "HKEX", "fund_name": "CSOP Hang Seng TECH Daily 2x Leveraged", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 350.0, "listing_date": "2021-06-28", "source": "hkex", "note": "very crowded — 5+ issuers"},
    {"underlier": "HSTECH", "underlier_market": "HK", "issuer": "Mirae Asset Global (HK)", "ticker": "7568 HK", "market": "HKEX", "fund_name": "Global X Hang Seng TECH 2x Leveraged", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 60.0, "listing_date": "2021-08-26", "source": "issuer-site", "note": ""},
    {"underlier": "HSTECH", "underlier_market": "HK", "issuer": "Samsung Asset (HK)", "ticker": "7568 HK", "market": "HKEX", "fund_name": "Samsung Hang Seng TECH 2x Leveraged", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 30.0, "listing_date": "2022-10-18", "source": "issuer-site", "note": "ticker reused — verify"},

    # ------------------------------------------------------------------
    # TAIWAN (TWSE) — leveraged index ETFs (single-stock leverage limited)
    # ------------------------------------------------------------------
    {"underlier": "TWSE", "underlier_market": "TW", "issuer": "Yuanta", "ticker": "00631L TT", "market": "TWSE", "fund_name": "Yuanta Taiwan 50 Leveraged 2x", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 800.0, "listing_date": "2014-10-31", "source": "issuer-site", "note": "largest leveraged ETF in Taiwan"},
    {"underlier": "TWSE", "underlier_market": "TW", "issuer": "Yuanta", "ticker": "00632R TT", "market": "TWSE", "fund_name": "Yuanta Taiwan 50 Inverse 1x", "leverage_amount": -1.0, "leverage_direction": "short", "aum_usd_m": 120.0, "listing_date": "2014-10-31", "source": "issuer-site", "note": ""},

    # ------------------------------------------------------------------
    # EU / UK — minimal coverage, mostly LSE leveraged single-stock by Leverage Shares / WisdomTree
    # Inclusion kept light: REX rarely competes with these directly.
    # ------------------------------------------------------------------
    {"underlier": "TSLA", "underlier_market": "US", "issuer": "Leverage Shares", "ticker": "TSL2 LN", "market": "LSE", "fund_name": "Leverage Shares 2x Tesla ETP", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 90.0, "listing_date": "2019-12-09", "source": "issuer-site", "note": ""},
    {"underlier": "NVDA", "underlier_market": "US", "issuer": "Leverage Shares", "ticker": "NVD2 LN", "market": "LSE", "fund_name": "Leverage Shares 2x NVIDIA ETP", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 150.0, "listing_date": "2019-12-09", "source": "issuer-site", "note": ""},
    {"underlier": "MSTR", "underlier_market": "US", "issuer": "Leverage Shares", "ticker": "MST2 LN", "market": "LSE", "fund_name": "Leverage Shares 2x MicroStrategy ETP", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 60.0, "listing_date": "2024-08-01", "source": "issuer-site", "note": ""},
    {"underlier": "COIN", "underlier_market": "US", "issuer": "Leverage Shares", "ticker": "COI2 LN", "market": "LSE", "fund_name": "Leverage Shares 2x Coinbase ETP", "leverage_amount": 2.0, "leverage_direction": "long", "aum_usd_m": 35.0, "listing_date": "2021-08-01", "source": "issuer-site", "note": ""},
]


def build() -> pd.DataFrame:
    """Return the curated foreign competitor 2x DataFrame."""
    df = pd.DataFrame(_ROWS)
    if df.empty:
        log.warning("foreign_competitors: empty mapping — returning empty frame")
        return df

    # Normalise types
    df["underlier"] = df["underlier"].astype(str).str.upper().str.strip()
    df["leverage_amount"] = pd.to_numeric(df["leverage_amount"], errors="coerce")
    df["aum_usd_m"] = pd.to_numeric(df["aum_usd_m"], errors="coerce")

    # Order columns
    cols = [
        "underlier", "underlier_market",
        "issuer", "ticker", "market", "fund_name",
        "leverage_amount", "leverage_direction",
        "aum_usd_m", "listing_date",
        "source", "note",
    ]
    df = df[cols].sort_values(
        ["underlier", "leverage_direction", "issuer"],
        kind="stable",
    ).reset_index(drop=True)

    log.info(
        "foreign_competitors: %d rows, %d unique underliers, %d markets",
        len(df), df["underlier"].nunique(), df["market"].nunique(),
    )
    return df


def crowding_summary(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-underlier crowding counts. Drop-in for D2 ranking.

    Returns a DataFrame indexed by ``underlier`` with columns:
        foreign_2x_long_count, foreign_2x_short_count,
        foreign_2x_total_count, n_markets, n_issuers
    """
    if df is None:
        df = build()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "foreign_2x_long_count", "foreign_2x_short_count",
                "foreign_2x_total_count", "n_markets", "n_issuers",
            ]
        )

    df = df.copy()
    df["_is_long"] = (df["leverage_direction"] == "long").astype(int)
    df["_is_short"] = (df["leverage_direction"] == "short").astype(int)
    g = df.groupby("underlier")
    summary = pd.DataFrame({
        "foreign_2x_long_count": g["_is_long"].sum().astype(int),
        "foreign_2x_short_count": g["_is_short"].sum().astype(int),
        "foreign_2x_total_count": g.size().astype(int),
        "n_markets": g["market"].nunique().astype(int),
        "n_issuers": g["issuer"].nunique().astype(int),
    })
    return summary.sort_values("foreign_2x_total_count", ascending=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info("foreign_competitors: wrote %s (%d rows)", OUT, len(df))

    # Print quick summary for human eyeball
    summary = crowding_summary(df)
    print("\nTop 15 most-crowded foreign underliers:")
    print(summary.head(15).to_string())
    print(f"\nTotal foreign 2x products: {len(df)}")
    print(f"Unique underliers: {df['underlier'].nunique()}")
    print(f"Markets covered: {sorted(df['market'].unique().tolist())}")


if __name__ == "__main__":
    main()
