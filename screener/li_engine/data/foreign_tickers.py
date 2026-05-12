"""Foreign equity ticker universe loader.

Pulls top tickers by market cap across the major non-US venues that REX has
filed leveraged ETFs against (SK Hynix, Samsung, etc.), enriches each with
name / sector / market-cap / ADR cross-reference, and writes per-market
parquet snapshots plus a combined `universe.parquet`.

Markets covered (per task spec, 2026-05-11):
    KS  KOSPI / KOSDAQ        (.KS / .KQ)   target 200
    T   Tokyo Stock Exchange  (.T)          target 200
    TW  Taiwan Stock Exchange (.TW / .TWO)  target 100
    HK  Hong Kong Exchange    (.HK)         target 100
    DE  XETRA / Frankfurt     (.DE)         target 100
    L   London Stock Exchange (.L)          target 100

Why a curated seed list instead of "scrape the whole exchange"?
    yfinance has no exchange-listing API. The only reliable way to get the
    top-N by market cap is to start from a known-good constituent list and
    enrich. We seed from major-index membership (KOSPI 200, Nikkei 225,
    TWSE 50, Hang Seng + HS Tech, DAX + MDAX, FTSE 100 + 250) — that already
    covers the largest names by definition. Smaller names that REX might
    file on (cf. SK Hynix tier) are added explicitly to the seed.

Output schema (per row):
    foreign_ticker_id    str   stable id, e.g. "KS:005930"
    local_ticker         str   yfinance symbol, e.g. "005930.KS"
    market               str   KS | T | TW | HK | DE | L
    name                 str   long name from yfinance
    sector               str   GICS sector (yfinance-reported)
    market_cap_usd       float USD-converted market cap
    adr_us_ticker        str   matching US ADR symbol if known, else ""

CLI:
    python -m screener.li_engine.data.foreign_tickers --market KS
    python -m screener.li_engine.data.foreign_tickers --all
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("yfinance required: pip install yfinance") from exc

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = _ROOT / "data" / "foreign"
CACHE_DIR = OUT_DIR / "_cache"

# yfinance is rate-limited; one ticker info call ~0.3s, batches of fast_info
# are faster. We keep a 24h cache so re-runs in the same day are instant.
CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Seed lists
# ---------------------------------------------------------------------------
# These are the *starting* universes per market. yfinance enrichment then
# fills name/sector/mcap; we sort by market_cap_usd desc and trim to top N.
#
# Sources (snapshotted 2026-Q2):
#   KOSPI 200 + KOSDAQ-150 large caps  (Korea Exchange index pages)
#   Nikkei 225 + TOPIX Core 30/Large70 (JPX index methodology)
#   TWSE 50 + TWSE Mid-Cap 100         (TWSE.com.tw)
#   Hang Seng + HSCEI + HS Tech 30     (HSI Services)
#   DAX 40 + MDAX 50                   (Deutsche Boerse)
#   FTSE 100 + select FTSE 250         (LSEG)
#
# Editing notes: if a ticker rolls off an index, leave it in the seed — the
# enrichment pass will keep it if it still has a market cap. Add new IPOs
# at the bottom; sort/trim handles the rest.
# ---------------------------------------------------------------------------

SEEDS: dict[str, list[str]] = {
    # ------------------------------------------------------------------ KS
    "KS": [
        # KOSPI top 50 by market cap (curated)
        "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS",
        "005935.KS", "000270.KS", "068270.KS", "005490.KS", "035420.KS",
        "012450.KS", "105560.KS", "055550.KS", "035720.KS", "028260.KS",
        "138040.KS", "086790.KS", "032830.KS", "066570.KS", "003670.KS",
        "015760.KS", "017670.KS", "316140.KS", "010130.KS", "034730.KS",
        "024110.KS", "018260.KS", "030200.KS", "009150.KS", "033780.KS",
        "402340.KS", "011200.KS", "010950.KS", "096770.KS", "267260.KS",
        "086280.KS", "004020.KS", "316140.KS", "047810.KS", "329180.KS",
        "267250.KS", "006400.KS", "051910.KS", "003550.KS", "009540.KS",
        "352820.KS", "377300.KS", "004990.KS", "180640.KS", "011170.KS",
        # KOSPI 50-100 tier
        "036570.KS", "000810.KS", "021240.KS", "002790.KS", "078930.KS",
        "071050.KS", "005830.KS", "010140.KS", "088350.KS", "001040.KS",
        "047050.KS", "000720.KS", "402340.KS", "375500.KS", "006800.KS",
        "139480.KS", "008770.KS", "010620.KS", "069960.KS", "323410.KS",
        "001570.KS", "012330.KS", "036460.KS", "000100.KS", "029780.KS",
        "035250.KS", "302440.KS", "004370.KS", "271560.KS", "020150.KS",
        "086520.KS", "047040.KS", "078930.KS", "097950.KS", "011780.KS",
        "025540.KS", "000990.KS", "012750.KS", "022100.KS", "192820.KS",
        "032640.KS", "161390.KS", "128940.KS", "051900.KS", "003490.KS",
        "001450.KS", "008560.KS", "016360.KS", "006260.KS", "079550.KS",
        # KOSDAQ flagships
        "247540.KQ", "091990.KQ", "086520.KQ", "196170.KQ", "263750.KQ",
        "066970.KQ", "035760.KQ", "041510.KQ", "112040.KQ", "058470.KQ",
        "240810.KQ", "067310.KQ", "095340.KQ", "196170.KQ", "048410.KQ",
        "078600.KQ", "357780.KQ", "035900.KQ", "085660.KQ", "214150.KQ",
        "036930.KQ", "039030.KQ", "086900.KQ", "108860.KQ", "418550.KQ",
        "298380.KQ", "950140.KQ", "067160.KQ", "131970.KQ", "950130.KQ",
        "214450.KQ", "298540.KQ", "141080.KQ", "950170.KQ", "025900.KQ",
        "028300.KQ", "278280.KQ", "317330.KQ", "086450.KQ", "031980.KQ",
        "204270.KQ", "237690.KQ", "950210.KQ", "183300.KQ", "393890.KQ",
        "950160.KQ", "195940.KQ", "041960.KQ", "352480.KQ", "036490.KQ",
    ],
    # ------------------------------------------------------------------ T
    "T": [
        # Nikkei 225 top tier
        "7203.T", "8306.T", "6758.T", "9984.T", "6861.T", "8035.T", "6098.T",
        "9432.T", "6501.T", "8316.T", "6594.T", "7974.T", "6367.T", "9433.T",
        "4063.T", "8001.T", "8058.T", "8031.T", "8411.T", "9434.T", "7267.T",
        "6981.T", "6902.T", "6273.T", "6503.T", "4502.T", "4661.T", "6178.T",
        "9020.T", "9022.T", "4519.T", "4543.T", "4523.T", "4578.T", "6701.T",
        "7741.T", "7011.T", "7751.T", "7733.T", "8002.T", "8053.T", "8267.T",
        "8591.T", "8725.T", "8750.T", "8766.T", "9101.T", "9104.T", "9107.T",
        "9301.T", "9501.T", "9502.T", "9503.T", "9531.T", "9532.T", "9613.T",
        "9735.T", "9766.T", "9983.T", "2502.T", "2503.T", "2802.T", "2914.T",
        "3382.T", "3402.T", "3405.T", "3407.T", "3436.T", "3659.T", "3938.T",
        "4005.T", "4021.T", "4042.T", "4061.T", "4151.T", "4183.T", "4188.T",
        "4208.T", "4324.T", "4452.T", "4503.T", "4506.T", "4507.T", "4519.T",
        "4523.T", "4528.T", "4536.T", "4568.T", "4612.T", "4631.T", "4689.T",
        "4704.T", "4751.T", "4755.T", "4901.T", "4911.T", "5019.T", "5020.T",
        "5101.T", "5108.T", "5201.T", "5214.T", "5232.T", "5233.T", "5301.T",
        "5332.T", "5333.T", "5401.T", "5406.T", "5411.T", "5541.T", "5631.T",
        "5703.T", "5706.T", "5707.T", "5711.T", "5713.T", "5714.T", "5801.T",
        "5802.T", "5803.T", "6103.T", "6113.T", "6301.T", "6302.T", "6305.T",
        "6326.T", "6361.T", "6471.T", "6472.T", "6473.T", "6479.T", "6502.T",
        "6504.T", "6506.T", "6645.T", "6674.T", "6701.T", "6702.T", "6703.T",
        "6724.T", "6752.T", "6753.T", "6770.T", "6796.T", "6841.T", "6857.T",
        "6902.T", "6920.T", "6952.T", "6954.T", "6971.T", "6976.T", "6988.T",
        "7003.T", "7004.T", "7012.T", "7013.T", "7186.T", "7201.T", "7202.T",
        "7211.T", "7261.T", "7269.T", "7270.T", "7272.T", "7731.T", "7735.T",
        "7752.T", "7762.T", "7832.T", "7912.T", "7951.T", "8015.T", "8028.T",
        "8233.T", "8252.T", "8253.T", "8254.T", "8267.T", "8303.T", "8304.T",
        "8308.T", "8309.T", "8331.T", "8354.T", "8355.T", "8369.T", "8385.T",
        "8418.T", "8473.T", "8601.T", "8604.T", "8628.T", "8630.T", "8697.T",
        "8801.T", "8802.T", "8804.T", "8830.T", "9005.T", "9007.T", "9008.T",
        "9009.T", "9021.T", "9062.T", "9064.T", "9147.T", "9202.T", "9412.T",
        "9602.T", "9681.T", "9684.T", "9697.T", "9706.T", "9719.T", "9737.T",
        "9783.T", "9831.T", "9843.T", "9861.T", "9962.T", "9989.T",
    ],
    # ------------------------------------------------------------------ TW
    "TW": [
        # TWSE 50 + select TWSE Mid-Cap 100 + OTC (.TWO)
        "2330.TW", "2317.TW", "2454.TW", "2382.TW", "2308.TW", "2412.TW",
        "2881.TW", "2882.TW", "1303.TW", "1301.TW", "2891.TW", "2303.TW",
        "3711.TW", "2002.TW", "2886.TW", "2884.TW", "2885.TW", "2892.TW",
        "1216.TW", "5871.TW", "2880.TW", "2887.TW", "5880.TW", "2890.TW",
        "2883.TW", "2207.TW", "2912.TW", "1101.TW", "1326.TW", "2603.TW",
        "3034.TW", "2357.TW", "2379.TW", "2395.TW", "3037.TW", "3008.TW",
        "3045.TW", "3231.TW", "2474.TW", "2618.TW", "2609.TW", "2610.TW",
        "1102.TW", "2105.TW", "1402.TW", "2801.TW", "2823.TW", "2834.TW",
        "5876.TW", "2801.TW", "1605.TW", "3702.TW", "2727.TW", "9910.TW",
        "8454.TW", "2049.TW", "2606.TW", "2615.TW", "2633.TW", "1722.TW",
        "1802.TW", "2027.TW", "2204.TW", "2301.TW", "2324.TW", "2347.TW",
        "2356.TW", "2360.TW", "2371.TW", "2376.TW", "2385.TW", "2408.TW",
        "2409.TW", "2448.TW", "2449.TW", "2455.TW", "2458.TW", "2467.TW",
        "2492.TW", "2498.TW", "2542.TW", "3231.TW", "3406.TW", "3443.TW",
        "3481.TW", "3533.TW", "3596.TW", "3653.TW", "3661.TW", "3673.TW",
        "3706.TW", "4904.TW", "4938.TW", "4958.TW", "5269.TW", "6116.TW",
        "6176.TW", "6239.TW", "6271.TW", "6285.TW", "6415.TW", "6505.TW",
        "8046.TW", "8454.TW", "9904.TW", "9910.TW", "9921.TW", "9933.TW",
        # TPEx (.TWO)
        "5483.TWO", "6488.TWO", "3260.TWO", "5347.TWO", "8299.TWO", "6515.TWO",
    ],
    # ------------------------------------------------------------------ HK
    "HK": [
        # Hang Seng + HSCEI + HS Tech 30
        "0700.HK", "9988.HK", "0941.HK", "0939.HK", "1299.HK", "0388.HK",
        "0005.HK", "1398.HK", "3690.HK", "0883.HK", "2318.HK", "9618.HK",
        "0003.HK", "0001.HK", "1810.HK", "2628.HK", "3988.HK", "0386.HK",
        "0857.HK", "0011.HK", "0016.HK", "1109.HK", "0066.HK", "0017.HK",
        "0027.HK", "0144.HK", "0175.HK", "0241.HK", "0267.HK", "0288.HK",
        "0291.HK", "0316.HK", "0322.HK", "0386.HK", "0688.HK", "0762.HK",
        "0823.HK", "0836.HK", "0857.HK", "0868.HK", "0960.HK", "0968.HK",
        "0992.HK", "1038.HK", "1044.HK", "1093.HK", "1113.HK", "1177.HK",
        "1209.HK", "1211.HK", "1378.HK", "1772.HK", "1776.HK", "1797.HK",
        "1816.HK", "1880.HK", "1928.HK", "1929.HK", "1972.HK", "1988.HK",
        "1997.HK", "2007.HK", "2015.HK", "2020.HK", "2196.HK", "2238.HK",
        "2269.HK", "2313.HK", "2319.HK", "2331.HK", "2333.HK", "2382.HK",
        "2388.HK", "2518.HK", "2600.HK", "2688.HK", "2899.HK", "3328.HK",
        "3690.HK", "3692.HK", "3888.HK", "3968.HK", "6098.HK", "6618.HK",
        "6862.HK", "9633.HK", "9888.HK", "9999.HK", "9961.HK", "9863.HK",
        "9626.HK", "9698.HK", "9868.HK", "9961.HK", "9988.HK", "1024.HK",
        "9866.HK", "1347.HK", "1359.HK", "1833.HK", "1606.HK", "0780.HK",
    ],
    # ------------------------------------------------------------------ DE
    "DE": [
        # DAX 40 + MDAX 50 + select TecDAX
        "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MUV2.DE", "MBG.DE", "BAS.DE",
        "BMW.DE", "ADS.DE", "DBK.DE", "DB1.DE", "DHL.DE", "BAYN.DE", "VOW3.DE",
        "RWE.DE", "HEN3.DE", "EOAN.DE", "BEI.DE", "FRE.DE", "IFX.DE", "MTX.DE",
        "VNA.DE", "1COV.DE", "CON.DE", "FME.DE", "HEI.DE", "MRK.DE", "PAH3.DE",
        "P911.DE", "PUM.DE", "QIA.DE", "RHM.DE", "SHL.DE", "SY1.DE", "ZAL.DE",
        "ENR.DE", "AIR.DE", "BNR.DE", "CBK.DE", "HFG.DE", "HNR1.DE", "LIN.DE",
        # MDAX
        "AFX.DE", "AOX.DE", "BC8.DE", "CEC.DE", "DBAN.DE", "DUE.DE", "EVD.DE",
        "EVK.DE", "EVT.DE", "FNTN.DE", "FPE3.DE", "FRA.DE", "G1A.DE", "GBF.DE",
        "GLJ.DE", "GXI.DE", "HBH.DE", "HLE.DE", "HOT.DE", "JEN.DE", "KGX.DE",
        "KRN.DE", "LEG.DE", "LXS.DE", "NDA.DE", "NEM.DE", "PFV.DE", "PSAN.DE",
        "PSM.DE", "RAA.DE", "RHK.DE", "RRTL.DE", "SAX.DE", "SAZ.DE", "SDF.DE",
        "SGL.DE", "SOW.DE", "SRT.DE", "SZG.DE", "TEG.DE", "TKA.DE", "TUI1.DE",
        "UN01.DE", "UTDI.DE", "VAR1.DE", "WAC.DE", "WAF.DE", "WCH.DE", "WUW.DE",
        "ZIL2.DE",
    ],
    # ------------------------------------------------------------------ L
    "L": [
        # FTSE 100 (LSE uses .L suffix)
        "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "RIO.L", "REL.L", "BP.L",
        "GLEN.L", "DGE.L", "BATS.L", "GSK.L", "CPG.L", "AAL.L", "LSEG.L",
        "NG.L", "BARC.L", "LLOY.L", "PRU.L", "TSCO.L", "VOD.L", "STAN.L",
        "AHT.L", "RR.L", "BA.L", "EXPN.L", "SSE.L", "IMB.L", "CRH.L",
        "FLTR.L", "IHG.L", "WTB.L", "WPP.L", "INF.L", "ANTO.L", "FRES.L",
        "CCH.L", "AV.L", "HLN.L", "SMT.L", "JD.L", "SGRO.L", "SBRY.L",
        "MNG.L", "ADM.L", "AUTO.L", "BDEV.L", "BKG.L", "BNZL.L", "BRBY.L",
        "BTRW.L", "CNA.L", "CRDA.L", "CTEC.L", "DCC.L", "EDV.L", "ENT.L",
        "EZJ.L", "FCIT.L", "FRAS.L", "GAW.L", "HIK.L", "HLMA.L", "HSX.L",
        "HWDN.L", "ICG.L", "ICP.L", "III.L", "IMI.L", "ITRK.L", "JMAT.L",
        "KGF.L", "LAND.L", "LGEN.L", "MKS.L", "MNDI.L", "MRO.L", "NWG.L",
        "OCDO.L", "PHNX.L", "PSH.L", "PSN.L", "PSON.L", "RTO.L", "SDR.L",
        "SGE.L", "SKG.L", "SMDS.L", "SMIN.L", "SN.L", "SPX.L", "SVT.L",
        "TW.L", "UTG.L", "UU.L", "WEIR.L",
        # Select FTSE 250 large caps
        "ABF.L", "MRO.L", "BME.L", "DPLM.L", "FERG.L", "MDC.L", "PNN.L",
        "SDR.L", "TPK.L", "VTY.L", "WIZZ.L",
    ],
}


# ---------------------------------------------------------------------------
# ADR cross-reference
# ---------------------------------------------------------------------------
# Curated mapping from local ticker → US ADR. yfinance does not expose
# ADR linkage; this is the only reliable source. Add aggressively as new
# ADRs come up (e.g. when REX files on another foreign name).
#
# Sources: NYSE ADR list, Bank of NY Mellon DR Directory, JPMorgan ADR.com.
# ---------------------------------------------------------------------------
ADR_MAP: dict[str, str] = {
    # Korea
    "005930.KS": "",          # Samsung Electronics — no US ADR (GDR in London only)
    "000660.KS": "",          # SK Hynix — no US ADR (GDR in Luxembourg)
    "035420.KS": "",          # NAVER — no US ADR
    "005380.KS": "HYMTF",     # Hyundai Motor (OTC)
    "051910.KS": "LGCLF",     # LG Chem (OTC)
    "068270.KS": "",          # Celltrion
    "207940.KS": "SMSDY",     # Samsung Biologics (OTC)
    "035720.KS": "",          # Kakao
    "373220.KS": "",          # LG Energy Solution
    # Japan
    "7203.T":  "TM",          # Toyota
    "8306.T":  "MUFG",        # Mitsubishi UFJ
    "6758.T":  "SONY",        # Sony
    "9984.T":  "SFTBY",       # SoftBank Group (OTC)
    "6861.T":  "KYCCF",       # Keyence (OTC)
    "8316.T":  "SMFG",        # Sumitomo Mitsui
    "8411.T":  "MFG",         # Mizuho
    "7267.T":  "HMC",         # Honda
    "9432.T":  "NTTYY",       # NTT (OTC)
    "9433.T":  "KDDIY",       # KDDI (OTC)
    "9434.T":  "",            # SoftBank Corp
    "8035.T":  "",            # Tokyo Electron
    "6098.T":  "",            # Recruit Holdings
    "4063.T":  "",            # Shin-Etsu Chemical
    "6981.T":  "",            # Murata Manufacturing
    "4502.T":  "TAK",         # Takeda
    "7974.T":  "NTDOY",       # Nintendo (OTC)
    "9983.T":  "FRCOY",       # Fast Retailing (OTC)
    # Taiwan
    "2330.TW": "TSM",         # TSMC
    "2317.TW": "HNHPF",       # Hon Hai (Foxconn, OTC)
    "2454.TW": "",            # MediaTek
    "2382.TW": "",            # Quanta
    "2308.TW": "",            # Delta Electronics
    "2412.TW": "CHT",         # Chunghwa Telecom
    "2881.TW": "",            # Fubon Financial
    "1301.TW": "",            # Formosa Plastics
    # Hong Kong
    "0700.HK": "TCEHY",       # Tencent (OTC)
    "9988.HK": "BABA",        # Alibaba (dual listing)
    "0941.HK": "CHL",         # China Mobile
    "0939.HK": "CICHY",       # CCB
    "1299.HK": "AIAGF",       # AIA (OTC)
    "0388.HK": "HKXCY",       # HKEX (OTC)
    "0005.HK": "HSBC",        # HSBC
    "1398.HK": "IDCBY",       # ICBC (OTC)
    "3690.HK": "MPNGY",       # Meituan (OTC)
    "0883.HK": "CEO",         # CNOOC
    "2318.HK": "PNGAY",       # Ping An (OTC)
    "9618.HK": "JD",          # JD.com (dual listing)
    "0857.HK": "PTR",         # PetroChina
    "0762.HK": "CHU",         # China Unicom
    "1810.HK": "XIACY",       # Xiaomi (OTC)
    # Germany
    "SAP.DE":  "SAP",         # SAP
    "SIE.DE":  "SIEGY",       # Siemens (OTC)
    "ALV.DE":  "ALIZY",       # Allianz (OTC)
    "DTE.DE":  "DTEGY",       # Deutsche Telekom (OTC)
    "BAS.DE":  "BASFY",       # BASF (OTC)
    "BMW.DE":  "BMWYY",       # BMW (OTC)
    "BAYN.DE": "BAYRY",       # Bayer (OTC)
    "VOW3.DE": "VWAGY",       # Volkswagen (OTC)
    "MBG.DE":  "MBGAF",       # Mercedes-Benz (OTC)
    "DBK.DE":  "DB",          # Deutsche Bank
    "RWE.DE":  "RWEOY",       # RWE (OTC)
    "MRK.DE":  "MKGAY",       # Merck KGaA (OTC)
    "ADS.DE":  "ADDYY",       # adidas (OTC)
    "MUV2.DE": "MURGY",       # Munich Re (OTC)
    "P911.DE": "POAHY",       # Porsche AG (OTC)
    "IFX.DE":  "IFNNY",       # Infineon (OTC)
    "AIR.DE":  "EADSY",       # Airbus (OTC)
    # United Kingdom
    "AZN.L":   "AZN",         # AstraZeneca
    "SHEL.L":  "SHEL",        # Shell
    "HSBA.L":  "HSBC",        # HSBC
    "ULVR.L":  "UL",          # Unilever
    "RIO.L":   "RIO",         # Rio Tinto
    "BP.L":    "BP",          # BP
    "GSK.L":   "GSK",         # GSK
    "BATS.L":  "BTI",         # British American Tobacco
    "DGE.L":   "DEO",         # Diageo
    "VOD.L":   "VOD",         # Vodafone
    "PRU.L":   "PUK",         # Prudential plc
    "BARC.L":  "BCS",         # Barclays
    "LLOY.L":  "LYG",         # Lloyds
    "STAN.L":  "SCBFF",       # Standard Chartered (OTC)
    "NG.L":    "NGG",         # National Grid
    "REL.L":   "RELX",        # RELX
    "GLEN.L":  "GLNCY",       # Glencore (OTC)
    "AAL.L":   "AAUKF",       # Anglo American (OTC)
    "WPP.L":   "WPP",         # WPP
    "BA.L":    "BAESY",       # BAE Systems (OTC)
    "RR.L":    "RYCEY",       # Rolls-Royce (OTC)
}


# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------
# yfinance market_cap is reported in the local listing currency. Convert to
# USD via spot FX. Cached in-memory per run.
# ---------------------------------------------------------------------------
MARKET_CCY: dict[str, str] = {
    "KS": "KRW",
    "T":  "JPY",
    "TW": "TWD",
    "HK": "HKD",
    "DE": "EUR",
    "L":  "GBp",   # LSE quote currency is pence (GBp). yfinance reports
                   # marketCap in pence too — _fx_to_usd applies a /100
                   # adjustment to convert to GBP before FX.
}


def _fx_to_usd(ccy: str) -> float:
    """Spot rate, units = USD per 1 unit of `ccy`. Falls back to 0 if unknown.

    Note: LSE main-board values come in GBp (pence). For that currency we
    divide by 100 first to get GBP, then apply the GBP→USD spot."""
    if ccy in ("USD", ""):
        return 1.0
    if ccy == "GBp":
        gbp_usd = _fx_to_usd("GBP")
        return gbp_usd / 100.0
    pair = f"{ccy}=X"  # yfinance convention: KRW=X = JPY per USD-style pair
    try:
        t = yf.Ticker(pair)
        rate = t.fast_info.get("lastPrice")
        if rate and rate > 0:
            return 1.0 / float(rate)
    except Exception as exc:
        log.warning("FX fetch failed for %s: %s", pair, exc)
    return 0.0


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
@dataclass
class Row:
    foreign_ticker_id: str
    local_ticker: str
    market: str
    name: str
    sector: str
    market_cap_usd: float
    adr_us_ticker: str


def _cache_path(market: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{market}_yfinfo.json"


def _load_cache(market: str) -> dict[str, dict]:
    p = _cache_path(market)
    if not p.exists():
        return {}
    age_h = (time.time() - p.stat().st_mtime) / 3600
    if age_h > CACHE_TTL_HOURS:
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(market: str, cache: dict[str, dict]) -> None:
    p = _cache_path(market)
    p.write_text(json.dumps(cache), encoding="utf-8")


def _local_to_id(local: str) -> str:
    """e.g. "005930.KS" -> "KS:005930"."""
    if "." in local:
        sym, suf = local.rsplit(".", 1)
        # KOSDAQ tickers carry .KQ but live under the KS market bucket
        bucket = "KS" if suf in ("KS", "KQ") else suf
        return f"{bucket}:{sym}"
    return f"?:{local}"


def fetch_market(market: str, target_n: int,
                 sleep_between: float = 0.15) -> pd.DataFrame:
    """Enrich seed list for `market`, sort by USD market cap, trim to top-N.

    Skips and warns on individual ticker failures; only raises if the seed
    is empty or the market is unknown.
    """
    if market not in SEEDS:
        raise KeyError(f"Unknown market '{market}'. Known: {sorted(SEEDS)}")

    seed = sorted(set(SEEDS[market]))  # de-dupe
    log.info("[%s] enriching %d seed tickers (target top %d)…",
             market, len(seed), target_n)

    cache = _load_cache(market)
    fx = _fx_to_usd(MARKET_CCY[market])
    if fx == 0.0:
        log.warning("[%s] FX rate lookup failed (%s) — market_cap_usd will be 0",
                    market, MARKET_CCY[market])

    rows: list[Row] = []
    for i, local in enumerate(seed, 1):
        info = cache.get(local)
        if info is None:
            try:
                t = yf.Ticker(local)
                # fast_info is much cheaper than .info; falls back to .info
                # for the few fields not in fast_info (sector, longName).
                fi = t.fast_info
                mcap_local = fi.get("marketCap") or 0
                # fetch slow info only if we got a market cap (i.e. ticker is real)
                if mcap_local:
                    full = t.info or {}
                    info = {
                        "name": full.get("longName") or full.get("shortName") or local,
                        "sector": full.get("sector") or "",
                        "market_cap_local": float(mcap_local),
                    }
                else:
                    info = {"name": "", "sector": "", "market_cap_local": 0.0}
            except Exception as exc:
                log.debug("[%s] %s fetch failed: %s", market, local, exc)
                info = {"name": "", "sector": "", "market_cap_local": 0.0}
            cache[local] = info
            time.sleep(sleep_between)
            if i % 25 == 0:
                _save_cache(market, cache)
                log.info("[%s] %d/%d enriched", market, i, len(seed))

        mcap_usd = info["market_cap_local"] * fx
        rows.append(Row(
            foreign_ticker_id=_local_to_id(local),
            local_ticker=local,
            market=market,
            name=str(info["name"] or "")[:120],
            sector=str(info["sector"] or ""),
            market_cap_usd=float(mcap_usd),
            adr_us_ticker=ADR_MAP.get(local, ""),
        ))

    _save_cache(market, cache)

    df = pd.DataFrame([r.__dict__ for r in rows])
    df = df[df["market_cap_usd"] > 0].copy()
    df = df.sort_values("market_cap_usd", ascending=False)
    df = df.drop_duplicates("local_ticker").head(target_n).reset_index(drop=True)
    log.info("[%s] kept %d rows after enrichment + filtering", market, len(df))
    return df


# ---------------------------------------------------------------------------
# Save / combine
# ---------------------------------------------------------------------------
TARGETS: dict[str, int] = {
    "KS": 200, "T": 200, "TW": 100, "HK": 100, "DE": 100, "L": 100,
}


def save_market(df: pd.DataFrame, market: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{market}.parquet"
    df.to_parquet(p, compression="snappy", index=False)
    log.info("[%s] wrote %s (%d rows, %.1f KB)",
             market, p, len(df), p.stat().st_size / 1024)
    return p


def combine_universe() -> Path:
    """Merge all per-market parquets into universe.parquet."""
    parts: list[pd.DataFrame] = []
    for m in TARGETS:
        p = OUT_DIR / f"{m}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    if not parts:
        raise RuntimeError("No per-market parquets found; run fetch first.")
    uni = pd.concat(parts, ignore_index=True)
    uni = uni.drop_duplicates("foreign_ticker_id").sort_values(
        "market_cap_usd", ascending=False).reset_index(drop=True)
    uni["snapshot_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = OUT_DIR / "universe.parquet"
    uni.to_parquet(out, compression="snappy", index=False)
    log.info("Wrote universe.parquet: %d rows, %.1f KB",
             len(uni), out.stat().st_size / 1024)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_sample(df: pd.DataFrame, market: str, n: int = 5) -> None:
    cols = ["local_ticker", "name", "sector", "market_cap_usd", "adr_us_ticker"]
    sample = df.head(n)[cols].copy()
    sample["market_cap_usd"] = (sample["market_cap_usd"] / 1e9).round(2)
    sample = sample.rename(columns={"market_cap_usd": "mcap_usd_b"})
    print(f"\n[{market}] sample (top {n} by USD market cap):")
    print(sample.to_string(index=False))


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--market", choices=sorted(TARGETS.keys()),
                   help="Single market to fetch (KS|T|TW|HK|DE|L)")
    p.add_argument("--all", action="store_true",
                   help="Fetch every market and rebuild universe.parquet")
    p.add_argument("--no-combine", action="store_true",
                   help="Skip writing universe.parquet (combined snapshot)")
    args = p.parse_args()

    if not args.market and not args.all:
        p.error("Specify --market <code> or --all")

    markets: Iterable[str] = TARGETS.keys() if args.all else [args.market]
    for m in markets:
        try:
            df = fetch_market(m, TARGETS[m])
            save_market(df, m)
            _print_sample(df, m)
        except Exception as exc:
            log.error("[%s] failed: %s — skipping", m, exc)

    if args.all and not args.no_combine:
        combine_universe()


if __name__ == "__main__":
    main()
