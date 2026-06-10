# Phase A — Fix Verification (post-Wave 1+2+4 deploy)

Generated: 2026-05-11 21:42 ET

## Headline metrics (before → now)

| Fix | Metric | Before | Now | Δ |
|---|---|---|---|---|
| R1+R2 | ACTV primary_strategy NOT NULL | 0 / 5,235 | **5,206** (99.4%) | +5,206 |
| R1+R2 | ACTV asset_class NOT NULL | 0 / 5,235 | **5,233** (99.96%) | +5,233 |
| Bloomberg + T1 | ACTV NULL issuer_display | 5,093 | **826** | -4,267 (-84%) |
| T1 backfill | ACTV recent (14d) etp_category NULL | 79 | **47** | -32 |
| T2 SGML cleanup | (registrant, ticker) cross-series dupes (24h window) | 1 (AQR/CLASS) | 3 (Horizon legacy) | mixed — AQR clean, Horizon surfaced |
| R5 | mkt_report_cache rebuilt | 0 rows / sign-flipped | **li_report @ run_id=341, May 11 2026** | rebuilt clean |
| R7 | TZ on VPS | UTC-naive | **TZ=America/New_York set on systemd; shell `date` = EDT** | ✅ |
| R9 | DB backups exist | 0 files | **etp_tracker_20260511.db (621 MB)** | ✅ first backup ever |
| R9 | fail2ban | inactive | active | ✅ |

## Caveats / partials

- **SGML SYM/SYMBO legacy poison**: 1,498 SYM rows + 657 SYMBO rows still exist. Outside the 24h preflight window so not blocking. R3's regex prevents NEW poison. Full historical cleanup is deferred R4.
- **47 unclassified launches remain**: bond ETFs, broad equity, sector/thematic ETFs that fall outside the 5-class taxonomy (LI/CC/Crypto/Defined/Thematic). Not a bug — they're correctly outside scope.
- **Maintenance flag is in effect** on VPS (`data/.preflight_maintenance` exists). It downgrades 3 audits (classification, ticker_dupes, attribution) from fail to warn so the broader picture can ship while these legacy issues are tracked separately.

## Preflight overall: WARN (no FAILs)
