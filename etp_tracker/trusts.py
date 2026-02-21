"""
ETP Trust CIK Registry

Each trust we monitor for filings. CIKs sourced from SEC EDGAR.
"""
from __future__ import annotations

# CIK -> Trust Name (override SEC's name if needed)
# Verified against SEC EDGAR on 2026-02-06
# To add a new trust:
#   1. Search https://efts.sec.gov/LATEST/search-index?q="Trust+Name"&forms=485BPOS
#   2. Get the CIK from the result
#   3. Verify at https://data.sec.gov/submissions/CIK{padded_10_digits}.json
#   4. Add entry below and re-run pipeline
TRUST_CIKS = {
    "2043954": "REX ETF Trust",
    "1424958": "Direxion Shares ETF Trust",
    "1040587": "Direxion Funds",
    "1174610": "ProShares Trust",
    "1689873": "GraniteShares ETF Trust",
    "1884021": "Volatility Shares Trust",
    "1976517": "Roundhill ETF Trust",
    "1924868": "Tidal Trust II",
    "1540305": "ETF Series Solutions",
    "1976322": "Themes ETF Trust",
    "1771146": "ETF Opportunities Trust",  # Tuttle/T-REX products
    "1452937": "Exchange Traded Concepts Trust",
    "1587982": "Investment Managers Series Trust II",
    "1547950": "Exchange Listed Funds Trust",
    "1579881": "Calamos ETF Trust",
    "826732": "Calamos Investment Trust",
    # Added 2026-02-12 via EDGAR search for leveraged ETF issuers
    "1782952": "Kurv ETF Trust",
    "1722388": "Tidal Trust III",  # Battle Shares and other leveraged products
    "1683471": "Listed Funds Trust",  # Teucrium 2x crypto products
    "1396092": "World Funds Trust",  # T-REX 2x products
    # Added 2026-02-17 - Leveraged & Inverse ETFs (verified against SEC submissions JSON)
    "1415726": "Innovator ETFs Trust",  # Buffer/defined outcome ETFs
    "1329377": "First Trust Exchange-Traded Fund",
    "1364608": "First Trust Exchange-Traded Fund II",
    "1424212": "First Trust Exchange-Traded Fund III",
    "1517936": "First Trust Exchange-Traded Fund IV",
    "1561785": "First Trust Exchange-Traded Fund VII",
    "1667919": "First Trust Exchange-Traded Fund VIII",
    "1742912": "Tidal Trust I",  # YieldMax and other Tidal products
    "1592900": "EA Series Trust",  # ARK/21Shares digital asset strategy ETFs
    "1378872": "Invesco Exchange-Traded Fund Trust II",
    "1418144": "Invesco Actively Managed Exchange-Traded Fund Trust",
    "1067839": "Invesco QQQ Trust Series 1",  # QQQ - Nasdaq 100
    # Covered Call / Income ETFs
    "1432353": "Global X Funds",  # QYLD, XYLD, RYLD covered call ETFs
    "1485894": "J.P. Morgan Exchange-Traded Fund Trust",  # JEPI, JEPQ
    "1479026": "Goldman Sachs ETF Trust",
    "1882879": "Goldman Sachs ETF Trust II",
    "1848758": "NEOS ETF Trust",  # Enhanced income ETFs
    "1810747": "Simplify Exchange Traded Funds",  # Options-based income
    # Multi-Strategy Platforms
    "1137360": "VanEck ETF Trust",
    "1350487": "WisdomTree Trust",
    "1579982": "ARK ETF Trust",  # ARK Innovation, Genomics, etc.
    "1655589": "Franklin Templeton ETF Trust",
    "1657201": "Invesco Exchange-Traded Self-Indexed Fund Trust",
    "1419139": "Invesco India Exchange-Traded Fund Trust",
    "1595386": "Invesco Actively Managed Exchange-Traded Commodity Fund Trust",
    # Crypto (485-series filers)
    "1976672": "Grayscale Funds Trust",  # Multi-ETF trust (BTC Covered Call etc.)
    "1928561": "Bitwise Funds Trust",  # Multiple Bitwise 485 ETFs
    "1877493": "Valkyrie ETF Trust II",  # CoinShares digital asset ETFs
    # Crypto Commodity Trusts (S-1/10-K filers - no 485 forms, tracked for completeness)
    "1588489": "Grayscale Bitcoin Trust ETF",  # GBTC
    "2015034": "Grayscale Bitcoin Mini Trust ETF",
    "1980994": "iShares Bitcoin Trust ETF",  # IBIT
    "1852317": "Fidelity Wise Origin Bitcoin Fund",  # FBTC
    "1838028": "VanEck Bitcoin ETF",  # HODL
    "1763415": "Bitwise Bitcoin ETF",  # BITB
    "1992870": "Franklin Templeton Digital Holdings Trust",  # EZBC
    "1869699": "Ark 21Shares Bitcoin ETF",  # ARKB
    "1841175": "CoinShares Bitcoin ETF",  # BRRR
    "1850391": "WisdomTree Bitcoin Fund",  # BTCW
    "1725210": "Grayscale Ethereum Staking ETF",  # ETHE
    "2020455": "Grayscale Ethereum Staking Mini ETF",
    "2000638": "iShares Ethereum Trust ETF",  # ETHA
    "2000046": "Fidelity Ethereum Fund",  # FETH
    "1860788": "VanEck Ethereum ETF",  # ETHV
    "2011535": "Franklin Ethereum Trust",  # EZET
    "1732409": "Grayscale Bitcoin Cash Trust",  # BCH
    "1705181": "Grayscale Ethereum Classic Trust",  # ETC
    "1732406": "Grayscale Litecoin Trust",  # LTC
    "1896677": "Grayscale Solana Staking ETF",  # SOL
    "2037427": "Grayscale XRP Trust ETF",  # XRP
    "1723788": "Bitwise 10 Crypto Index ETF",  # BITW
    # Added 2026-02-18 - Bloomberg screener categories: LI, Crypto, CC, Defined
    # -- Leveraged & Inverse --
    "1415311": "ProShares Trust II",  # Commodity/currency leveraged (AGQ, BOIL, UVXY)
    "1039803": "ProFunds",  # ProFunds leveraged mutual funds
    # -- Covered Call / Income --
    "1761055": "BlackRock ETF Trust",  # iShares buy-write/covered call
    "1804196": "BlackRock ETF Trust II",
    "1501825": "Hartford Funds Exchange-Traded Trust",
    "1676326": "Morgan Stanley ETF Trust",
    "1506001": "Neuberger Berman ETF Trust",
    "1064641": "Select Sector SPDR Trust",  # SPDR sector covered call
    "836267": "SCM Trust",
    # -- Defined Outcome --
    "1797318": "AIM ETF Products Trust",  # Innovator/AIM buffer ETFs
    "1727074": "PGIM ETF Trust",  # PGIM buffer ETFs
    "1616668": "Pacer Funds Trust",  # Pacer defined outcome
    "1936157": "Elevation Series Trust",  # Buffer/defined outcome
    "1580843": "WEBs ETF Trust",
    "1898391": "Fidelity Greenwood Street Trust",
    "1581539": "Horizons ETF Trust",
    # -- Multi-Strategy Platforms --
    "1633061": "Amplify ETF Trust",
    "1408970": "AdvisorShares Trust",
    "1527428": "Arrow Investments Trust",
    "1719812": "Collaborative Investment Series Trust",
    "2078265": "Corgi ETF Trust I",
    "945908": "Fidelity Covington Trust",
    "1547576": "Krane Shares Trust",  # KraneShares
    "1040612": "Madison Funds",
    "1537140": "Northern Lights Fund Trust III",
    "1644419": "Northern Lights Fund Trust IV",
    "1454889": "Schwab Strategic Trust",
    "1650149": "Series Portfolios Trust",
    "1506213": "Strategy Shares",
    "1795351": "T. Rowe Price Exchange-Traded Funds, Inc.",
    "1496608": "AB Active ETFs, Inc.",
    "1970751": "Advisor Managed Portfolios",
    "1371571": "Invesco DB US Dollar Index Trust",
    "1516212": "SSGA Active Trust",  # State Street Global Advisors
    "768847": "VanEck Funds",
    # -- Commodity Trusts --
    "1529505": "United States Commodity Funds Trust I",  # USO, UNG etc.
    "1985840": "Tidal Commodities Trust I",  # DEFI commodity trust
    # -- Crypto S-1 filers (no 485 forms, tracked for completeness) --
    "2064314": "21Shares Dogecoin ETF",
    "2028834": "21Shares Solana ETF",
    "2082889": "Bitwise Chainlink ETF",
    "2053791": "Bitwise Dogecoin ETF",
    "2045872": "Bitwise Solana Staking ETF",  # BSOL
    "2039525": "Bitwise XRP ETF",
    "2063380": "Fidelity Solana Fund",
    "2033807": "Franklin Crypto Trust",  # EZPZ
    "2074409": "Invesco Galaxy Solana ETF",  # QSOL
    "1767057": "Osprey Bitcoin Trust",  # OBTC
    "1345125": "Cyber Hornet Trust",  # BBB, SSS, EEE, XXX
    "2039505": "Canary XRP ETF",
    "2039458": "Canary HBAR ETF",
    "2039461": "Canary Litecoin ETF",
    "2041869": "Canary Marinade Solana ETF",
    # Added 2026-02-20 - Trust Expansion: 72 new verified ETF trusts from Bloomberg issuer data
    # All CIKs verified via https://data.sec.gov/submissions/CIK{padded}.json
    # -- Defined Outcome --
    "1595106": "Innovator ETFs Trust II",  # Additional Innovator buffer ETFs
    "1992104": "PGIM Rock ETF Trust",  # PGIM buffer/defined outcome
    # -- Commodity / Thematic --
    "1728683": "Sprott Funds Trust",  # Precious metals & commodity ETFs
    "1597389": "USCF ETF Trust",  # United States Commodity Funds
    "1944285": "Tema ETF Trust",  # Thematic ETFs
    "1796383": "Siren ETF Trust",  # Thematic/alternative ETFs
    "1989916": "SP Funds Trust",  # Shariah-compliant ETFs
    "1604813": "Abacus FCF ETF Trust",  # Free cash flow focused
    "1040674": "Truth Social Funds",  # Thematic
    "1779306": "AltShares Trust",  # Alternative strategy ETFs
    "1573496": "Reality Shares ETF Trust",  # Thematic/dividend ETFs
    # -- First Trust (additional trusts) --
    "1552740": "First Trust Exchange-Traded Fund VI",
    "1549548": "First Trust Exchange-Traded Fund V",
    "1383496": "First Trust Exchange-Traded AlphaDEX Fund",
    # -- Large Platform ETF Trusts --
    "1168164": "SPDR Index Shares Funds",  # State Street SPDR ETFs
    "1100663": "iShares Trust",  # BlackRock iShares core ETFs
    "1524513": "iShares U.S. ETF Trust",  # iShares US-focused ETFs
    "1209466": "Invesco Exchange-Traded Fund Trust",  # Invesco ETFs
    "1748425": "Gabelli ETFs Trust",  # Gabelli active ETFs
    "1415845": "Columbia ETF Trust",  # Columbia Threadneedle ETFs
    "1551950": "Columbia ETF Trust I",
    "1450501": "Columbia ETF Trust II",
    "2034928": "Capital Group Equity ETF Trust I",  # Capital Group/American Funds ETFs
    "1500604": "Janus Detroit Street Trust",  # Janus Henderson ETFs
    "1845809": "Putnam ETF Trust",  # Putnam/Franklin ETFs
    "1710607": "American Century ETF Trust",  # American Century ETFs
    "1648403": "Virtus ETF Trust II",  # Virtus/Newfleet ETFs
    "1831313": "TCW ETF Trust",  # TCW active bond ETFs
    "1919700": "Touchstone ETF Trust",  # Touchstone active ETFs
    "1849998": "Federated Hermes ETF Trust",  # Federated Hermes ETFs
    "1807486": "Alger ETF Trust",  # Fred Alger ETFs
    "1981627": "GMO ETF Trust",  # GMO quality/value ETFs
    "2055464": "Wedbush Series Trust",  # Wedbush ETFs
    "2035827": "Harris Oakmark ETF Trust",  # Oakmark active ETFs
    "1318342": "Investment Managers Series Trust",  # Multi-manager ETF platform
    # -- Fixed Income ETF Trusts --
    "1879238": "BondBloxx ETF Trust",  # Sector-specific bond ETFs
    "1860434": "Harbor ETF Trust",  # Harbor active ETFs
    "1450011": "PIMCO ETF Trust",  # PIMCO bond ETFs
    "1886172": "DoubleLine ETF Trust",  # DoubleLine bond ETFs
    # -- Multi-Strategy Platforms --
    "1728860": "Natixis ETF Trust II",  # Natixis/Loomis ETFs
    "1526787": "Natixis ETF Trust",
    "2038383": "Thornburg ETF Trust",  # Thornburg active ETFs
    "1896670": "Thrivent ETF Trust",  # Thrivent ETFs
    "1558107": "ALPS Series Trust",  # ALPS advisor ETFs
    "1901443": "X-Square Series Trust",  # X-Square ETFs
    "1605803": "Lattice Strategies Trust",  # Hartford/Lattice ETFs
    "1415995": "New York Life Investments ETF Trust",  # IndexIQ ETFs
    "1426439": "New York Life Investments Active ETF Trust",
    "1478482": "John Hancock Exchange-Traded Fund Trust",  # Dimensional sub-advised
    "1414040": "ALPS ETF Trust",  # ALPS-managed ETFs
    "1572661": "Principal Exchange-Traded Funds",  # Principal ETFs
    "1479599": "AGF Investments Trust",  # AGF ETFs
    "1888997": "SEI Exchange Traded Funds",  # SEI ETFs
    "2042513": "Russell Investments Exchange Traded Funds",  # Russell ETFs
    "1645194": "Legg Mason ETF Investment Trust",  # Legg Mason/Franklin ETFs
    "1792795": "Legg Mason ETF Investment Trust II",
    "1551895": "Franklin ETF Trust",  # Franklin LibertyShares
    "2018846": "MFS Active Exchange Traded Funds Trust",  # MFS active ETFs
    "2051630": "Lazard Active ETF Trust",  # Lazard active ETFs
    "2043390": "Tidal Trust IV",  # Tidal platform trusts
    "1969674": "2023 ETF Series Trust",  # Multi-advisor ETF series
    "1484018": "Spinnaker ETF Series",  # Spinnaker platform ETFs
    "1532206": "Arrow ETF Trust",  # Arrow active ETFs
    "1670310": "Davis Fundamental ETF Trust",  # Davis value ETFs
    "2065379": "Man ETF Series Trust",  # Man Group ETFs
    "1924447": "THOR Financial Technologies Trust",  # THOR ETFs
    "2069687": "FIS Trust",  # FIS ETFs
    "1559109": "ETFis Series Trust I",  # Virtus ETFis platform
    "1875710": "Build Funds Trust",  # Build ETFs
    "1969995": "Nomura ETF Trust",  # Nomura ETFs
    "1727398": "Procure ETF Trust II",  # Procure thematic ETFs
    "1704174": "Procure ETF Trust I",
}

# 33 Act (Securities Act of 1933) filers: S-1/10-K registration.
# All CIKs not listed here default to 40 Act (Investment Company Act of 1940, N-1A).
ACT_33_CIKS = {
    # Crypto commodity trusts (S-1/10-K filers - no 485 forms)
    "1588489",  # Grayscale Bitcoin Trust ETF (GBTC)
    "2015034",  # Grayscale Bitcoin Mini Trust ETF
    "1980994",  # iShares Bitcoin Trust ETF (IBIT)
    "1852317",  # Fidelity Wise Origin Bitcoin Fund (FBTC)
    "1838028",  # VanEck Bitcoin ETF (HODL)
    "1763415",  # Bitwise Bitcoin ETF (BITB)
    "1992870",  # Franklin Templeton Digital Holdings Trust (EZBC)
    "1869699",  # Ark 21Shares Bitcoin ETF (ARKB)
    "1841175",  # CoinShares Bitcoin ETF (BRRR)
    "1850391",  # WisdomTree Bitcoin Fund (BTCW)
    "1725210",  # Grayscale Ethereum Staking ETF (ETHE)
    "2020455",  # Grayscale Ethereum Staking Mini ETF
    "2000638",  # iShares Ethereum Trust ETF (ETHA)
    "2000046",  # Fidelity Ethereum Fund (FETH)
    "1860788",  # VanEck Ethereum ETF (ETHV)
    "2011535",  # Franklin Ethereum Trust (EZET)
    "1732409",  # Grayscale Bitcoin Cash Trust (BCH)
    "1705181",  # Grayscale Ethereum Classic Trust (ETC)
    "1732406",  # Grayscale Litecoin Trust (LTC)
    "1896677",  # Grayscale Solana Staking ETF (SOL)
    "2037427",  # Grayscale XRP Trust ETF (XRP)
    "1723788",  # Bitwise 10 Crypto Index ETF (BITW)
    # Crypto S-1 filers (no 485 forms)
    "2064314",  # 21Shares Dogecoin ETF
    "2028834",  # 21Shares Solana ETF
    "2082889",  # Bitwise Chainlink ETF
    "2053791",  # Bitwise Dogecoin ETF
    "2045872",  # Bitwise Solana Staking ETF (BSOL)
    "2039525",  # Bitwise XRP ETF
    "2063380",  # Fidelity Solana Fund
    "2033807",  # Franklin Crypto Trust (EZPZ)
    "2074409",  # Invesco Galaxy Solana ETF (QSOL)
    "1767057",  # Osprey Bitcoin Trust (OBTC)
    "1345125",  # Cyber Hornet Trust (BBB, SSS, EEE, XXX)
    "2039505",  # Canary XRP ETF
    "2039458",  # Canary HBAR ETF
    "2039461",  # Canary Litecoin ETF
    "2041869",  # Canary Marinade Solana ETF
    # Commodity trusts (S-1/10-K filers)
    "1529505",  # United States Commodity Funds Trust I (USO, UNG, etc.)
    "1985840",  # Tidal Commodities Trust I (DEFI commodity trust)
    "1415311",  # ProShares Trust II (commodity/currency leveraged)
    "1371571",  # Invesco DB US Dollar Index Trust
}


def get_act_type(cik: str) -> str:
    """Return '33' for Securities Act filers, '40' for Investment Company Act filers."""
    return "33" if str(cik).strip() in ACT_33_CIKS else "40"


def get_all_ciks() -> list[str]:
    """Return list of all CIKs to track."""
    return list(TRUST_CIKS.keys())

def get_overrides() -> dict[str, str]:
    """Return CIK -> Trust Name overrides."""
    return TRUST_CIKS.copy()


def add_trust(cik: str, name: str) -> bool:
    """Add a trust to the registry file. Returns True if added, False if already exists."""
    cik = str(cik).strip()
    if cik in TRUST_CIKS:
        return False

    # Update in-memory dict
    TRUST_CIKS[cik] = name

    # Write to the file so it persists across restarts
    import pathlib
    trusts_file = pathlib.Path(__file__)
    content = trusts_file.read_text(encoding="utf-8")

    # Insert new entry before the closing brace of TRUST_CIKS
    new_entry = f'    "{cik}": "{name}",\n'
    content = content.replace("\n}\n", f"\n{new_entry}}}\n", 1)

    trusts_file.write_text(content, encoding="utf-8")
    return True
