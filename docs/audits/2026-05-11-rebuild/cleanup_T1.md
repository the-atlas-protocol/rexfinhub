# Cleanup T1 — Classify launches + CC backfill + issuer_display backfill

Branch: `audit-cleanup-T1-classify`
Status: completed by coordinator after agent stalled mid-issuer-mapping write

## Summary

Targeted preflight failures: 79 unclassified new launches + 17 NULL issuer_display + 3 missing CC attributes.

## What landed

### fund_mapping.csv — 32 new rows (auto-classify HIGH+MEDIUM)

Source: `tools.rules_editor.classify_engine::scan_unmapped(since_days=30)` → `apply_classifications` for HIGH+MEDIUM confidence.

Coverage: Innovator Buffer ETFs (DDFY/DDTY), Corgi suite (15 funds), Pacer SOS, Aptus DB suite, 21Shares (THYP/TDOT/TSUI), GraniteShares 2x.

```
DDFY US,Defined,1,atlas
DDTY US,Defined,1,atlas
RSIT US,LI,1,atlas
QQMY US,Defined,1,atlas
... (32 total)
```

### attributes_*.csv — 35 new rows total

- attributes_CC.csv: +6
- attributes_Crypto.csv: +2
- attributes_Defined.csv: +19
- attributes_LI.csv: +3
- attributes_Thematic.csv: +5

### issuer_brand_overrides.csv — 16 ticker-level brand additions

Backfill for the 17 NULL issuer_display ACTV ETPs flagged by preflight `audit_classification`. Each ticker mapped to its brand by inspection of the raw `issuer` field on mkt_master_data.

| ticker | brand | source raw issuer |
|---|---|---|
| BNDY US | Horizon | Horizon Funds |
| HBTA US | Horizon | Horizon Funds |
| QGRD US | Horizon | Horizon Funds |
| DPRE US | Virtus | Virtus ETF Trust II |
| GLDB US | IDX | ETF Opportunities Trust/IDX Adv |
| IVES US | Wedbush | Wedbush Series Trust |
| JEDI US | Defiance | ETF Series Solutions/Defiance |
| JHDG US | John Hancock | John Hancock Exchange-Traded F |
| OBTC US | Osprey | Osprey Funds LLC/USA |
| OEI US | Core Alternative | Listed Funds Trust/Core Altern |
| PBOT US | Pictet | 2023 ETF Series Trust/Pictet |
| TCAI US | Tortoise | Tortoise Capital Series Trust |
| TDOT US | 21Shares | 21Shares Polkadot ETF/Fund Par |
| TLDR US | REX | REX ETF Trust |
| TSUI US | 21Shares | 21Shares Sui ETF/Fund Parent |
| UPSD US | Aptus | ETF Series Solutions/Aptus Cap |

### issuer_mapping.csv — 5 new (1 dropped)

Added: Defined/Thematic/Crypto Corgi ETF Trust I (×3 categories), 21Shares Hyperliquid (Crypto).
Dropped: bad row `LI,nan,nan` (NaN issuer artifact from prior run).

## ACTION REQUIRED FROM RYU

**1 ticker deferred for manual review:**

- **PLGI US** (Collaborative Investment Series, CC). The "PL" prefix could mean Penn Mutual or another sub-advisor. Insufficient confidence to assign a brand without checking with you.

## Verification

After Wave 5 push + sweep on VPS, expect preflight `audit_classification` to drop:
- 79 unclassified → 79 - 32 - whatever the LOW-confidence remainder is (likely down to ~30)
- 17 NULL issuer_display → 17 - 16 = 1 (PLGI)
- 3 missing CC → likely 0 (auto-classify covers these)

## Rollback

```
git checkout main -- config/rules/
```

## Notes

- 47 LOW-confidence candidates remain in `scan_unmapped` output — these are genuinely ambiguous (one-word names, no clear category signal) and should not be auto-classified. They're the long tail.
- The `issuer_brand_overrides.csv` additions take effect when `apply_issuer_brands.py` runs — that's part of the chain service ExecStartPost (R1's deployment).
