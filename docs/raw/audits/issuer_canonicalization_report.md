# Issuer Canonicalization Report

**Generated**: 2026-05-05
**Source**: `mkt_master_data` WHERE `market_status = 'ACTV'`

## Summary

| Metric | Value |
|--------|-------|
| Total distinct `issuer_display` values | 168 |
| Singleton clusters (no action) | 141 |
| Multi-member clusters | 12 |
| AUTO merge proposals | 12 |
| REVIEW items (human eyes required) | 3 |

## Confidence Thresholds

- **AUTO** (similarity >= 0.92 — safe to merge programmatically)
- **REVIEW** (similarity 0.75-0.92 or token-containment match — coordinator must decide)
- **DISTINCT** (similarity < 0.75, genuinely different issuers)

Similarity uses the max of two scores:
1. Levenshtein distance on normalised+sorted token strings
2. Token-containment: what fraction of the shorter name's tokens appear in the longer

---

## Multi-Member Clusters

### Cluster: iShares

- **iShares** (440 funds) <- CANONICAL
- 21Shares (4 funds) -> **REVIEW** (similarity 0.75)
- iShares Delaware Trust Sponsor (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 445*

### Cluster: GraniteShares

- **GraniteShares** (71 funds) <- CANONICAL
- KraneShares (32 funds) -> **REVIEW** (similarity 0.77)
- GraniteShares ETF Trust (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 104*

### Cluster: Simplify

- **Simplify** (41 funds) <- CANONICAL
- Amplify (41 funds) -> **REVIEW** (similarity 0.75)
  *Total cluster funds: 82*

### Cluster: Xtrackers

- **Xtrackers** (37 funds) <- CANONICAL
- DWS Xtrackers (7 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 44*

### Cluster: T. Rowe Price

- **T. Rowe Price** (29 funds) <- CANONICAL
- T Rowe (2 funds) -> merge [AUTO, similarity 1.00]
- T Rowe Price Exchange-Traded F (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 32*

### Cluster: Harbor

- **Harbor** (28 funds) <- CANONICAL
- Harbor ETF Trust (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 29*

### Cluster: AB (AllianceBernstein)

- **AB (AllianceBernstein)** (19 funds) <- CANONICAL
- AllianceBernstein (4 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 23*

### Cluster: Bitwise

- **Bitwise** (20 funds) <- CANONICAL
- Bitwise Avalanche ETF/Fund Par (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 21*

### Cluster: BNY Mellon

- **BNY Mellon** (17 funds) <- CANONICAL
- BNY (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 18*

### Cluster: Tuttle

- **Tuttle** (8 funds) <- CANONICAL
- ETF Opportunities Trust/Tuttle (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 9*

### Cluster: Lazard

- **Lazard** (5 funds) <- CANONICAL
- Lazard Active ETF Trust (2 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 7*

### Cluster: RBB Fund

- **RBB Fund** (1 funds) <- CANONICAL
- RBB ETFs/F/m Investments (1 funds) -> merge [AUTO, similarity 1.00]
  *Total cluster funds: 2*

---

## Singleton Clusters (no action needed)

`ALPS`, `ARK`, `Abacus`, `Adaptiv`, `AdvisorShares`, `Alger ETF Trust/The`, `Allianz`, `Alpha Architect`, `American Beacon`, `American Century`, `Amplius`, `Aptus`, `Arrow`, `Avantis`, `BMOMAX`, `Barclays`, `Baron`, `BlackRock`, `BondBloxx`, `CSC`, `Calamos`, `Cambria`, `Cohen & Steers`, `Columbia`, `Corgi`, `Cyber Hornet`, `DB AG ETNs`, `DayHagen`, `Defiance`, `Deutsche`, `Dimensional`, `Direxion`, `DoubleLine`, `EA`, `ETF Architect`, `ETF Series Solutions`, `ETRACS`, `Eaton Vance`, `Elevation`, `Emperial Finance`, `EntrepreneurShares`, `Equable`, `Eventide`, `Exchange-Traded Concepts (ETC)`, `Federated Hermes`, `Fidelity`, `First Trust`, `FlexShares`, `Franklin Templeton`, `GMO`, `Gabelli`, `Global X`, `Goldman Sachs`, `Grayscale`, `Guinness Atkinson`, `Hartford`, `Hashdex`, `Horizon Kinetics`, `Innovator`, `Inspire`, `Invesco`, `Investment Managers Series`, `JP Morgan`, `Janus Henderson`, `John Hancock`, `Kurv`, `LeverageShares`, `Listed Funds`, `MFS`, `MRBL`, `Madison`, `Matthews`, `Meridian`, `MicroSectors`, `Miller Value`, `Morgan Stanley`, `Motley Fool`, `Neos`, `NestYield`, `Neuberger Berman`, `New York Life`, `Nomura`, `Northern Lights`, `Northern Trust`, `Nuveen`, `Osprey`, `Overlay`, `PGIM`, `PIMCO`, `Pacer`, `Pictet`, `ProShares`, `Procure`, `Prudential`, `Putnam`, `REX`, `Rareview`, `Rayliant`, `Reckoner`, `Renaissance`, `Rockefeller`, `Roundhill`, `Russell`, `SCM`, `SEI`, `SRH`, `Schwab`, `Series Portfolio Trust`, `Siren`, `Spinnaker`, `Sprott`, `State Street`, `Strategas`, `Strive`, `Swan`, `TCW`, `TappAlpha`, `Tema`, `Teucrium`, `Themes`, `Tidal`, `Tortoise`, `Touchstone`, `Tradr`, `TrueShares`, `Two Roads / Hypatia`, `USCF`, `Ultimus Managers Trust/Q3 Asse`, `Valkyrie`, `VanEck`, `Vanguard`, `Vegashares ETF Trust`, `VictoryShares`, `Virtus`, `VistaShares`, `VolatilityShares`, `WEBs`, `WisdomTree`, `YieldMax`, `abrdn`, `iPath`

---

*Audit alpha -- read-only. Do not apply without coordinator sign-off.*
