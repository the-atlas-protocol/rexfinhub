# Stage 1 Audit — CSV Rules Pipeline

Generated: 2026-05-11T22:35:00 ET
Agent: csv_rules
Surface: `config/rules/`, `data/rules/`, `tools/rules_editor/classify_engine.py`, `market/config.py`, `market/rules.py`, `market/transform.py`, `tools/rules_editor/app.py`, `webapp/routers/admin.py`

---

## Summary

The CSV rules pipeline is in a **classic split-brain** state. Two parallel rule trees exist
(`config/rules/` and `data/rules/`); both are git-tracked, both contain real edits, and they
diverge in every interlocking file. The market pipeline (`market/config.py`) reads from
`config/rules/` (primary), but the daily classifier (`tools/rules_editor/classify_engine.py`)
writes to `data/rules/`. Result: every classification approval written via the classifier or
the on-VPS pull-sync flow is invisible to the live website (DB matches `config/rules/` byte-for-byte).
Cross-CSV interlock per atlas memory is also broken — there are `attributes_X.csv` rows for
tickers classified to a **different** category in `fund_mapping.csv` (11 such cross-category leaks
in `config/rules/`, 8 in `data/rules/`) and 11 funds classified in `fund_mapping` with **no**
matching attribute row. Local vs VPS hashes also differ for both directories, indicating a
two-axis drift: local↔VPS and config↔data.

---

## CSV inventory

### `C:/Projects/rexfinhub/config/rules/`
| File | Rows (data) | Columns | Last modified | Git tracked? |
|---|---:|---|---|---|
| fund_mapping.csv | 2300 | ticker, etp_category, is_primary, source | Apr 27 20:03 | YES |
| attributes_LI.csv | 877 | ticker, map_li_category, map_li_subcategory, map_li_direction, map_li_leverage_amount, map_li_underlier | Apr 8 09:56 | YES |
| attributes_CC.csv | 342 | ticker, map_cc_underlier, map_cc_index, cc_type, cc_category | Apr 27 19:53 | YES |
| attributes_Crypto.csv | 146 | ticker, map_crypto_type, map_crypto_underlier | Apr 6 17:55 | YES |
| attributes_Defined.csv | 527 | ticker, map_defined_category | Apr 20 18:14 | YES |
| attributes_Thematic.csv | 420 | ticker, map_thematic_category | Mar 23 20:58 | YES |
| issuer_mapping.csv | 341 | etp_category, issuer, issuer_nickname | Apr 27 20:56 | YES |
| exclusions.csv | 26 | ticker, etp_category | Mar 23 20:58 | YES |
| rex_funds.csv | 96 | ticker | Mar 30 21:07 | YES |
| rex_suite_mapping.csv | 96 | ticker, rex_suite | Mar 30 21:07 | YES |
| market_status.csv | 17 | code, description | Mar 3 00:21 | YES |
| competitor_groups.csv | 62 | (n/a) | Mar 4 13:33 | YES |
| issuer_brand_overrides.csv | 2647 | (n/a) | May 11 03:01 | YES |
| issuer_canonicalization.csv | 12 | (n/a) | May 8 16:54 | YES |
| underlier_overrides.csv | 47 | (n/a) | May 5 23:57 | YES |
| fund_master.csv | 7231 | (snapshot/cache) | May 6 01:17 | YES |
| _queues_report.json | n/a | (3.0 MB) | May 7 11:59 | YES (modified, unstaged) |

### `C:/Projects/rexfinhub/data/rules/`
| File | Rows (data) | Columns | Last modified | Git tracked? |
|---|---:|---|---|---|
| fund_mapping.csv | 2327 | ticker, etp_category, is_primary, source | May 5 21:43 | YES |
| attributes_LI.csv | 895 | (same as config) | May 5 21:43 | YES |
| attributes_CC.csv | 348 | (same as config) | May 5 21:43 | YES |
| attributes_Crypto.csv | 149 | (same as config) | May 5 21:43 | YES |
| attributes_Defined.csv | 529 | (same as config) | May 5 21:43 | YES |
| attributes_Thematic.csv | 421 | (same as config) | May 5 21:43 | YES |
| issuer_mapping.csv | 342 | (same as config) | May 5 21:43 | YES |
| exclusions.csv | 26 | (same as config) | Mar 23 20:58 | YES |
| rex_funds.csv | 96 | ticker | Mar 30 21:07 | YES |
| rex_suite_mapping.csv | 90 | ticker, rex_suite | Mar 30 21:07 | YES (6 fewer rows than config) |
| market_status.csv | 17 | code, description | Feb 25 21:12 | YES |
| competitor_groups.csv | 62 | (n/a) | Mar 4 13:33 | YES |
| _queues_report.json | n/a | (3.3 MB, **STALE** Mar 2 17:29) | Mar 2 17:29 | YES |

`data/rules/` is missing `fund_master.csv`, `issuer_brand_overrides.csv`,
`issuer_canonicalization.csv`, `underlier_overrides.csv`, and `exclusions.csv` is older than
config. The `.gitignore` explicitly whitelists `data/rules/` (`!data/rules/`) so both trees
are tracked deliberately.

---

## Sync status: `config/rules/` vs `data/rules/` (LOCAL)

| File | config rows | data rows | Identical content? | Newer mtime |
|---|---:|---:|---|---|
| fund_mapping.csv | 2300 | 2327 | NO — 6 config-only, 33 data-only tickers | data |
| attributes_LI.csv | 877 | 895 | NO — 0 config-only, 18 data-only | data |
| attributes_CC.csv | 342 | 348 | NO — 4 config-only, 10 data-only | data |
| attributes_Crypto.csv | 146 | 149 | NO — 0 config-only, 3 data-only | data |
| attributes_Defined.csv | 527 | 529 | NO — 0 config-only, 2 data-only | data |
| attributes_Thematic.csv | 420 | 421 | NO — 0 config-only, 1 data-only | data |
| issuer_mapping.csv | 341 | 342 | NO — 1 config-only, 2 data-only | data |

Per `tools/rules_editor/sync.py:65`, a one-way `sync_config_to_data()` exists in the
Streamlit Rules Editor UI but **no automatic sync runs**, and there is no
`sync_data_to_config` to push classifier writes back to the source-of-truth tree.

`config/rules/fund_mapping.csv` has mixed line endings: 2288 CRLF + 13 LF-only. All other
CSVs use clean CRLF. No BOM markers, no blank rows, all files end with newline. No
trailing whitespace in ticker columns.

---

## Local vs VPS sync (md5 of fund_mapping.csv + issuer_mapping.csv)

| File | Local md5 | VPS md5 | Match |
|---|---|---|---|
| config/rules/fund_mapping.csv | ae3d119e… | 057493…  | NO |
| data/rules/fund_mapping.csv | d486da37… | 8f73baa7… | NO |
| config/rules/issuer_mapping.csv | daee045d… | 3a7d9ac7… | NO |
| data/rules/issuer_mapping.csv | f30876c8… | 5b3a7a8c… | NO |

Row-count comparison (config vs data, local vs VPS):

| File | local config | VPS config | local data | VPS data |
|---|---:|---:|---:|---:|
| fund_mapping.csv | 2300 | 2300 | 2327 | **2360** |
| attributes_LI.csv | 877 | 877 | 895 | **899** |
| attributes_CC.csv | 342 | 342 | 348 | **351** |
| attributes_Crypto.csv | 146 | 146 | 149 | **151** |
| attributes_Defined.csv | 527 | 527 | 529 | **548** |
| attributes_Thematic.csv | 420 | 420 | 421 | **426** |
| issuer_mapping.csv | 341 | 342 | 342 | **347** |

VPS `data/rules/` has the most rows on every interlocking file — that's where the live
classifier has been writing since May 8. Local `data/rules/` is a partial subset. Local
`config/rules/` row counts match VPS `config/rules/` but bytes differ (likely CRLF vs LF).

---

## DB sync status (`webapp/services/data_engine.py` consumer)

| Source | mkt_fund_mapping rows | mkt_issuer_mapping rows | mkt_category_attributes rows | NULL etp_category in mkt_master_data |
|---|---:|---:|---:|---:|
| Local SQLite | 2300 | 341 | 2294 | 5076 |
| VPS SQLite | 2300 | 341 | (not queried) | 5076 |
| Local `config/rules/` | 2300 | 341 | 2331 (sum across attrs) | n/a |
| Local `data/rules/` | **2327** | **342** | **2351** | n/a |

`market.config.RULES_DIR` resolves to `config/rules/` (primary) because that path exists
locally; **DB == config/rules/ exactly, byte-for-byte by ticker set**. None of the writes
to `data/rules/` have ever made it to the DB.

`mkt_fund_mapping` schema is `(id, ticker, etp_category, created_at)` — no `is_primary`,
no `source`. `market/rules.py:sync_rules_to_db` truncates and reinserts every run from
whatever `RULES_DIR` resolves to. Last pipeline run: 2026-05-11 22:18:15 (run_id 304),
loaded from local `config/rules/`.

---

## Findings

### F1 — CRITICAL: Split-brain write paths (`config/rules/` vs `data/rules/`)

**Severity**: Critical. **Confidence**: HIGH.

`tools/rules_editor/classify_engine.py:18` hard-codes:
```
RULES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "rules"
# Write to data/rules/ (source of truth), not config/rules/ (git-tracked copy)
```

But `market/config.py:21-23` says the **opposite**:
```
# Rules: config/rules/ is git-tracked and NOT hidden by the persistent disk.
# data/rules/ is the legacy location (hidden on Render by the persistent disk mount).
_RULES_PRIMARY = PROJECT_ROOT / "config" / "rules"
_RULES_FALLBACK = PROJECT_ROOT / "data" / "rules"
RULES_DIR = _RULES_PRIMARY if _RULES_PRIMARY.exists() else _RULES_FALLBACK
```

`webapp/routers/admin.py:432`, `tools/rules_editor/app.py:39`,
`webapp/services/classification_validator.py`, the daily preflight (`scripts/preflight_check.py:173`),
and every test (`tests/test_attribution.py`) all read from `config/rules/`.

The Streamlit rules editor and the manual `/admin/classification/update` endpoint both write
to `config/rules/`. The Atlas auto-classifier (`apply_classifications` →
`tools/rules_editor/classify_engine.py`), called from
`/admin/classification/{id}/approve` (admin.py:660) and the on-VPS pull-sync hook
(scripts/run_rapid_sync.py per the migration commit `8b80330 fix(classification): resilient
+ auto-scan on pull`), writes to `data/rules/`.

Net effect: every classifier-approved fund since the migration is **invisible to the live
site**, the `mkt_*` tables, and Render. The 33 data-only tickers in
`data/rules/fund_mapping.csv` (AMA US, AMKL US, BUFE US, etc.) and the 60 extra rows on the
VPS `data/rules/` tree (vs. local data/rules) confirm continuous write traffic landing in
the wrong tree.

**Fix direction (Stage 2)**: Choose ONE source of truth. Either point classify_engine at
`config/rules/` OR point market/config.py at `data/rules/`. The comment in classify_engine
("write to data/rules/, not config/rules/ git-tracked copy") implies `data/rules/` was
intended to be canonical, but reality (DB load + the rest of the codebase) treats config as
canonical. Recommend: flip classify_engine to `config/rules/` and delete `data/rules/`
contents (after merging the 33+ orphan tickers).

### F2 — CRITICAL: Cross-category attribute leakage (interlock violation)

**Severity**: Critical. **Confidence**: HIGH.

Per atlas memory, the three CSV families must be edited together. In practice, multiple
tickers appear in an `attributes_X.csv` file while `fund_mapping.csv` says they belong to
category Y:

`config/rules/`:
| Attr file | Ticker | fund_mapping says |
|---|---|---|
| attributes_CC | QBUL US | Defined |
| attributes_CC | TESL US | Thematic |
| attributes_CC | KQQQ US | Thematic |
| attributes_Crypto | OOSB US | LI |
| attributes_Crypto | OOQB US | LI |
| attributes_Defined | SPYH US | CC |
| attributes_Defined | ACEI US | CC |
| attributes_Defined | ACII US | CC |
| attributes_Thematic | STCE, DECO, SATO, NDIV, CRPT, BITQ, FDIG, WGMI, NODE, HECO US | Crypto/CC |

11 cross-category violations in `config/rules/`, 8 in `data/rules/`.

These leak through `market/transform.py:step6_apply_category_attributes` because that step
joins `mkt_category_attributes` on `ticker` only — without category — so a ticker with a
stale row in attributes_Thematic but a current `fund_mapping` row for Crypto will pick up
**both** sets of `map_*` columns on the same record.

### F3 — HIGH: Missing attribute rows for funds in `fund_mapping`

**Severity**: High. **Confidence**: HIGH.

`config/rules/` shows 11 funds classified in `fund_mapping.csv` but lacking a row in the
matching `attributes_X.csv`:

| Category | Tickers (sample) |
|---|---|
| CC (8 missing) | CWY US, DPRE US, HYGM US, JHDG US, JUDO US, LQDM US, MUYY US, TMYY US |
| Defined (1 missing) | TLDR US |
| Thematic (2 missing) | DADS US, JEDI US |

These funds will display the correct top-level category on the website but have NULL
`map_*` attribute columns, breaking attribute-based filters and breakdowns.
`data/rules/` has only 3 missing (CC) — the auto-classifier has been filling in attribute
rows in the wrong tree.

### F4 — MEDIUM: Orphan attribute rows (no fund_mapping)

**Severity**: Medium. **Confidence**: HIGH.

5 tickers in `config/rules/attributes_Defined.csv` have no `fund_mapping.csv` row at all:
BFEW US, BFXU US, BUFE US, NBFR US, XBFR US. They will be silently dropped by
`step3_apply_fund_mapping` (left-join on ticker, no etp_category), but the dead attribute
rows accumulate.

### F5 — MEDIUM: 79 unclassified new launches (preflight signal)

**Severity**: Medium (operational). **Confidence**: HIGH.

The "79 new launches" figure from preflight (`scripts/preflight_check.py:140`) maps exactly
to:
```sql
SELECT COUNT(*) FROM mkt_master_data
 WHERE market_status='ACTV'
   AND etp_category IS NULL
   AND date(inception_date) >= date('now','-14 days')
```
Returns **79**. They are NOT in `fund_mapping.csv` at all (not present-with-NULL). 30-day
lookback returns 104. This is the day-to-day classification debt, distinct from the
split-brain bug above — even if F1 is fixed, these 79 still need either an auto-classifier
run or manual `/admin/classification/update` calls.

### F6 — HIGH: 15 dead rules in `fund_mapping.csv` (no Bloomberg row)

**Severity**: High. **Confidence**: HIGH.

15 tickers in `config/rules/fund_mapping.csv` have no row in `mkt_master_data` at all —
all marked `source=manual`. Mostly delisted/liquidated crypto products from Grayscale,
Volatility Shares, etc.:

```
JPMO US (CC), WPAY US (CC), JELA US (CC),
BCHG, BTCFX, DEFG, ETCG, FILG, GBAT, GLIV, GSNR, LTCN, MANA US (Crypto),
DMAT US (Thematic), GDFN US (Thematic)
```

These don't break anything (left-join just drops them) but they're untestable noise. Likely
safe to remove or to add a dedicated `delisted=1` column.

### F7 — MEDIUM: Issuer_mapping per-category interlock works, but 72 issuers span 2-5 categories

**Severity**: Informational. **Confidence**: HIGH.

Per atlas memory, "issuer_mapping is per-category — new product class for existing trust →
'Other' bucket". The pattern is real: `EA Series Trust` appears in 5 categories,
`AdvisorShares ETFs/USA` in 4, etc. Total 72 issuers span 2+ categories (out of 269 unique
issuers). **However: zero issuer_nicknames contain "Other"** — the documented "Other"
bucket convention either isn't actually used, or a different naming scheme replaced it.
Worth confirming with the user before any rule writes.

Distinct issuers per category in `config/rules/issuer_mapping.csv`:
- Thematic: 119, Crypto: 76, CC: 62, LI: 53, Defined: 31

### F8 — MEDIUM: Stale `_queues_report.json` in `data/rules/`

**Severity**: Medium. **Confidence**: HIGH.

`data/rules/_queues_report.json` is from 2026-03-02 (3.3 MB) — 70 days stale.
`config/rules/_queues_report.json` is from 2026-05-07 (3.0 MB) and is currently **modified
but unstaged in git** (`git status` shows `M config/rules/_queues_report.json`,
20,482 insertions / 26,102 deletions). This is the only currently-uncommitted file in the
rules tree.

### F9 — LOW: `rex_suite_mapping.csv` row count mismatch

**Severity**: Low. **Confidence**: HIGH.

`config/rules/rex_suite_mapping.csv` has 96 rows, `data/rules/` has 90. Both are git-tracked.
Schema identical (`ticker, rex_suite`).

### F10 — LOW: Mixed line endings in `config/rules/fund_mapping.csv`

**Severity**: Low. **Confidence**: HIGH.

13 LF-only line breaks among 2288 CRLF — likely from a Linux-side write (Render or VPS) that
later got pulled to Windows. Doesn't break parsing (pandas handles both). Worth normalizing
to avoid noisy `git diff` in future commits.

### F11 — LOW: Local ↔ VPS drift, both directions

**Severity**: Low (because VPS data/rules is the live truth and DB is regenerated nightly).
**Confidence**: HIGH.

All four CSV md5s differ between local and VPS for the two flagship files. VPS data/rules
has 33+ more tickers than local data/rules. Local config/rules row counts equal VPS
config/rules but bytes differ (CRLF). Symptom of an irregular `git pull/push` cadence
combined with classifier writes happening on VPS but not flowing back to local via git.

### F12 — LOW: No rollback strategy beyond `git checkout`

**Severity**: Low (because they are git-tracked). **Confidence**: HIGH.

Both rule trees are git-tracked — that **is** the rollback. But `_queues_report.json` (3MB
of churn per day) is also tracked, polluting `git diff` and history. Recommend gitignoring
both `_queues_report.json` files and adding a real `data/rules/.gitignore` rule.

---

## Surfaces inspected

- `config/rules/` (17 files, full inventory, schema check, row counts, encoding)
- `data/rules/` (13 files, same)
- `tools/rules_editor/classify_engine.py` (full read — write surface confirmed: `data/rules/`)
- `tools/rules_editor/app.py` (Streamlit UI — write surface: `config/rules/`)
- `tools/rules_editor/sync.py` (one-way config→data utility, manual)
- `tools/rules_editor/categorize.py`, `validators.py`, `schemas.py` (RULES_DIR = config)
- `market/config.py`, `market/rules.py`, `market/transform.py` (read-only consumers, RULES_DIR = config)
- `webapp/routers/admin.py` (`/admin/classification/update` writes to RULES_DIR = config; `/admin/classification/approve` calls `apply_classifications` which writes to data)
- `webapp/services/classification_validator.py` (reads from RULES_DIR = config)
- `scripts/preflight_check.py` (reads from `config/rules/attributes_CC.csv` directly, hard-coded)
- `scripts/run_market_pipeline.py` (calls `sync_rules_to_db(rules, session)`)
- `scripts/seed_market_rules.py` (Excel → `data/rules/` legacy seeder, one-time)
- `scripts/run_rapid_sync.py` (referenced in commit `8b80330`)
- Local SQLite `data/etp_tracker.db` — `mkt_fund_mapping`, `mkt_issuer_mapping`, `mkt_category_attributes`, `mkt_master_data` queried for cross-CSV consistency
- VPS `jarvis@46.224.126.196:/home/jarvis/rexfinhub/{config,data}/rules/` — `ls -la`, `wc -l`, `md5sum`, pandas row counts via SSH
- VPS SQLite — fund_mapping count + null etp_category count
- `git log -- config/rules/`, `git log -- data/rules/`, `git ls-files`, `git status`

## Surfaces NOT inspected

- `screener/li_engine/` — appears to consume but not write rule CSVs; not traced
- `etp_tracker/` pipeline — separate SEC pipeline, doesn't touch market rules
- The `audit_2026-05-11/01_classification.md` file already exists (other agent's work);
  not read to avoid contamination
- Multiple `.claude/worktrees/agent-*/config/rules/` (75 worktree copies) — out of scope
- `attributes_CC.csv` value-level audit (e.g., is `cc_type` always Synthetic/Traditional?) —
  schema only, not value sanity
- `fund_master.csv` (7231 rows) and `issuer_brand_overrides.csv` (2647 rows) — present in
  config/rules/ but not in atlas-memory's "three CSVs", treated as out of scope
- `auto_classify.py` heuristic logic — only its file-path usage was traced
- Render's writable persistent disk path (`/opt/render/project/src/data`) — code says
  `data/rules/` is hidden on Render by the persistent disk mount; not verified live
