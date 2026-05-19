# Sys-F: Data History + Retention + Recovery — 2026-05-05

## TL;DR

- **Bloomberg history**: 16 files spanning 2026-02-22 to 2026-05-05 (73 days). Major gaps: Feb 27–Mar 25 (27 days), Mar 27–Apr 12 (16 days), Apr 18–21, Apr 25–29.
- **Screener snapshots**: 7 unique dates (Mar 30–Apr 8). **Last snapshot was 27 days ago.** Apr 9–May 5: zero snapshots.
- **Reproducibility**: Can re-run past reports ONLY for dates with snapshots (~7 days). All others: irrecoverable.
- **Off-site copies**: GitHub (code + small configs only). D: drive (local USB, same physical location). NO cloud / third-site DB backup.
- **Top risk**: VPS disk dies → weeks to recover via SEC re-scrape.

## 1. History Inventory

### Bloomberg Daily Files
16 files, 2026-02-22 → 2026-05-05. Coverage gaps:
- Feb 27 → Mar 25 (27 days missing)
- Mar 27 → Apr 12 (16 days missing)
- Apr 18–21 (4 days)
- Apr 25–29 (5 days)

No retention policy. Files accumulate indefinitely on local. Not on VPS, not in GitHub, not in cloud.

### Screener Snapshots
7 unique dates: Mar 30 (3 partial), Mar 31, Apr 1, Apr 6, Apr 8 (last 4 are full with mkt_master_data.parquet).
**Apr 9 → May 5: NO snapshots produced.** The `archive_daily()` call exists in `run_daily.py` but stopped producing output. Investigate why.

### Pipeline Run Records
`mkt_pipeline_runs` table is append-only — every sync logged. But: linked data rows (mkt_master_data, mkt_time_series) are DELETED+reinserted each sync. So we know WHEN syncs ran but cannot reconstruct WHAT the data was at run N.

### Parquet Files
19 files in `data/analysis/`. No date in filenames — versioned ad-hoc by suffix (v2/v3/v4). Old versions overwritten silently. No archival.

## 2. Audit Log Durability

| Log | Format | Size | Growth | Rotation | Off-site |
|---|---|---|---|---|---|
| `.send_audit.json` | JSON array | ~6KB / 22 entries | 2-3/day | None | No |
| `.gate_state_log.jsonl` | JSONL | 2 entries (started Apr 28) | 2-10/week | None | No |
| `.send_log.json` | JSON dict | 25 days of entries | Per send | None | No |
| `.ticker_cleanup_log.jsonl` | — | Not present | — | — | — |
| `.classification_log.jsonl` | — | Not present | — | — | — |

**All audit logs are gitignored** (`data/` in `.gitignore`). Single copy on local disk, Syncthing-replicated to laptop at same location. **No off-site backup.**

## 3. Reproducibility Scorecard

The killer: `sync_market_data()` runs `DELETE FROM mkt_master_data` and `DELETE FROM mkt_time_series` before reinserting (`market_sync.py:242-244`). No history table, no versioning, no AS-OF query mechanism.

| Past Report | Re-runnable? | Why/Why not |
|---|---|---|
| May 4 daily filing report | Partial | Filings table is append-only, query as-of OK. Market data overwritten by today's sync. |
| Apr 8 screener report | YES | screener_snapshots/2026-04-08/ exists with full state. |
| Any date Apr 9–May 3 market | NO | No snapshot. Bloomberg snapshots exist for some dates but re-import would destroy current data. |
| Any date Feb 27–Mar 25 | NO | No Bloomberg snapshot. No screener snapshot. |
| Weekly report (any past Monday) | Partial | Filings reproducible. Market ranking/flow not reproducible. |
| "What did AUM look like Apr 15?" | NO | mkt_time_series stores `months_ago` offsets, not absolute dates. State replaced after Apr 15 sync. |

**Verdict**: Reproducibility limited to ~7 days of screener snapshots, all from March/early April.

## 4. DB Backup State

Local `data/*.bak*`:
- `etp_tracker.db.bak-224036` (NO date in name)
- `etp_tracker.db.bak_pre_vacuum_20260504_093445`
- `etp_tracker.db.fromvps` (NO date)

**Automated daily backup: NONE.** Largest gap between backups: undeterminable due to missing dates.

## 5. Render Data Redundancy

Upload is one-way: local → Render via `POST /api/v1/db/upload`. No pull-back. If Render service deleted, `live_feed.db` (Render-only) is gone permanently. Render Starter plan offers no disk snapshots.

## 6. Source-of-Truth Ambiguity

| Data Point | VPS | Render | Bloomberg | D: cold |
|---|---|---|---|---|
| Fund AUM today | ✅ current | ✅ current (post-upload) | source | none |
| Fund AUM 30d ago | ❌ | ❌ | xlsm if archived | snapshot if exists |
| Email send history | ❌ | ❌ | n/a | local only |

No documented conflict resolution policy.

## 7. Time-Travel Capability

| Question | Answerable? |
|---|---|
| What did mkt_master_data look like Apr 15? | NO |
| What was AUM for ticker X 30 days ago? | NO (mkt_time_series uses months_ago offsets, not dates) |
| What filings landed Apr 15? | YES (filings append-only) |
| What was sent on Apr 28? | YES (audit log) |
| Audit my fund recommendations from Apr 8? | YES (screener snapshot) |
| Audit my fund recommendations from Apr 15? | NO |

## 8. Catastrophic Loss Scenarios

| Scenario | Lost Forever | Recoverable | Data Loss | RTO |
|---|---|---|---|---|
| VPS disk dies | live_feed.db since last upload | etp_tracker.db from local | Hours/days of feed | 2-4h |
| Render service deleted | live_feed.db all-time | DBs from local | Live feed gone, site down | 4-8h |
| Local machine dies | All audit logs, all snapshots, .bak files | Code (GitHub), Render serving copy (1d stale) | Audit history, Bloomberg snapshots, screener snapshots | Days |
| **Local + Render same day** | Everything except code + D: cache | SEC EDGAR (re-scrape weeks), Bloomberg (re-export hours) | All DB state, all audit history | **Weeks** |
| D: USB dies | Cold archives | Local primaries | Minor (D: is cold) | None |

## 9. Off-Site Copies

| Location | Contents | Currency | Risk |
|---|---|---|---|
| GitHub | Code + configs | Live | No data |
| Render persistent disk | Serving DB copy | ~1d stale | One-way, Render-controlled |
| D: USB | SEC cache, cold screener | Daily | Same physical location |
| Syncthing laptop | Mirror of project | Real-time | Same household |
| **S3/Dropbox/cloud** | **Nothing** | — | **No third-site backup** |

## 10. Compliance / Legal

- SEC filings: derived works, low retention risk
- `.send_audit.json`: marketing/comm log. **FINRA 17a-4 = 3-year retention** if institutional recipients. Currently 4 weeks of history, no rotation, single file, no archival.
- `digest_subscribers.txt`: 1 entry (consent record). Gitignored. If local dies, consent proof gone.

## Top 3 Risks

### 1. CRITICAL — No off-site DB backup
etp_tracker.db lives at one physical location (local + Syncthing laptop, co-located). Render is serving copy, not backup. Render Starter has no disk snapshots. Loss = weeks of re-scraping.

### 2. HIGH — mkt_master_data has no history; one sync destroys previous state
Every sync: full DELETE + reinsert. No temporal table. No safe time-travel. Reproducibility = whatever snapshots happen to exist.

### 3. HIGH — All audit logs gitignored, no off-site copy
Including consent records (CAN-SPAM/GDPR exposure as subscriber count grows).

## Recommendations (Prioritized)

1. **[CRITICAL] Nightly DB backup to remote**. systemd timer: `sqlite3 ... ".backup data/backups/etp_tracker_$(date +%Y%m%d).db"` + rclone to B2/S3. ~$0.50/month.
2. **[HIGH] Pre-sync snapshot before mkt_master_data wipe**. Shadow table or dated parquet in `data/DASHBOARD/history/`.
3. **[HIGH] Move audit logs out of gitignored data/**. Either commit nightly to private repo, or rclone to cloud, or carve-out from gitignore.
4. **[MEDIUM] Resume daily screener snapshots**. archive_daily() call exists but stopped producing output Apr 8. Investigate.
5. **[MEDIUM] Backup consent records (digest_subscribers.txt) to durable location**.
6. **[LOW] Document Bloomberg snapshot retention policy**. Daily check that today's xlsm was archived.
7. **[LOW] Audit log rotation**: archive entries >90 days to dated quarterly file.

---

*Audit performed by Sys-F bot, 2026-05-05. Read-only. No production data modified.*
