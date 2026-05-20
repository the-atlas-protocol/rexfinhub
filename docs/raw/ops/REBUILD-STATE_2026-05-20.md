---
title: REX FinHub Rebuild — State & Handoff
created: 2026-05-20
owner: Ryu El-Asmar
status: definitive session record — past / present / future
---

# REX FinHub Rebuild — State & Handoff (2026-05-20)

> Definitive record of the seven-track structural rebuild executed 2026-05-19→20.
> Written as a pre-compaction handoff: a fresh session should be able to pick up
> from this doc + `docs/LOG.md` + `docs/raw/ops/REBUILD-COMPLETION-PLAN_2026-05-19.md`.

---

## 1. The system

**rexfinhub** — REX Shares' automated SEC + Bloomberg + CBOE + 13F intelligence
platform. FastAPI + Jinja2 + SQLAlchemy + SQLite.

- **Production source of truth:** VPS `jarvis@46.224.126.196:/home/jarvis/rexfinhub/`. SQLite DB at `data/etp_tracker.db` (~670 MB). systemd timers orchestrate everything.
- **Public webapp:** rexfinhub.com on Render — read-only DB replica, auto-deploys on push to `main`.
- **D: drive** (`D:\sec-data\`) — nightly backup/cache archive. Not queried live.
- **Docs:** six-doc canonical framework in `docs/` (INDEX, SYSTEM, TARGET, RUNBOOK, GLOSSARY, LOG + DECISIONS/ ADRs + raw/).
- **VPS services:** `rexfinhub-api.service` is a pipeline API on **port 8001** (not the full webapp). The full webapp is on Render. systemd timers: atom-watcher, fresh-poller, bloomberg-chain (17:15+21:00), preflight (18:30), gate open/close (19:00/20:00), daily send (19:30), intraday-refresh (08:05/12:05/16:05/20:05), morning-triage (08:00), cboe (03:00), 13f-quarterly, db-backup (23:00).

## 2. The architecture (target — now largely realized + live)

Seven design invariants (`docs/TARGET.md`):

1. **One canonical UUID per product** — `product_master.canonical_id`; tickers/CUSIPs/CIKs/FIGIs map in via bi-temporal `identifier_xref`.
2. **Polymorphic typed underliers** — `underlier_master` (8 types) + `fund_underlier` join; never a bare string.
3. **Bi-temporal lifecycle** — `status_history` appends a row per status change; reality-time + knowledge-time.
4. **Deterministic survivorship** — declared per-field source priority (`webapp/services/survivorship.py`).
5. **Ops-as-assertions** — 25 dbt-style checks (`scripts/run_assertions.py`) → `assertion_run` table → 08:00 triage email.
6. **One `classification_override` table** — admin-keyed override layer over the auto-classifier.
7. **Self-service admin** — `/admin/*` pages.

**DB tables added by the rebuild:** `product_master`, `identifier_xref`, `underlier_master`, `fund_underlier` (Phase 4); `status_history` (Phase 5); `classification_override`, `assertion_run` (Phase 6); `system_flags`, `preflight_run`, `system_event` (Phase 7B).

**Status state machine** (`status_history.status`): `under_consideration → filed → effective → target_list → listed`, + terminal `suspended / delisted / liquidated`. Bloomberg `market_status` is authoritative for the trading state (ACTV→listed, LIQU→delisted).

## 3. PAST — what this session delivered (18 PRs, #62–#79)

All merged to `main`, deployed to VPS + Render, 25/25 assertions maintained.

- **Track 1 — Documentation reconciliation (#62).** Fixed 17 doc defects; ADRs 0006–0010 → accepted; declared `rex_products.canonical_id` + `status_cached` on the `RexProduct` ORM model.
- **Track 2 — Phase 1 Cut 3 closeout (#63).** `upload_db_to_render` race fixed (per-process unique paths — concurrent runs were deleting each other's `.gz`); added retry to `upload_screener_cache_to_render`.
- **Track 3 — Phase 4b underlier completion (#64).** `underlier_master` unknowns 15→1; `fix_underlier_classification.py` repointed 5 MicroSectors ETNs off the junk `0` underlier onto Solactive/NYSE index underliers; `underlier_id_coverage` assertion strengthened.
- **Track 4a — retire `capm_products` (#65).** `import_capm.py` repointed to write `rex_products`; seed + `CapMProduct` ORM model removed; **`DROP TABLE capm_products`** behind a Gate-C equivalence proof (74 rows, 0 loss).
- **Track 4b — retire `rex_products.underlier` (#66).** Column converted to a `hybrid_property` resolving from `underlier_master` via `fund_underlier` (readers unchanged — works in Python attr + SQL filter/sort/group); **physical column dropped** (Gate C: 503 rows, 0 loss); `canonicalize_crypto_underliers` cron retired.
- **Track 4c — replanned/descoped.** `classification_override` is `canonical_id`-keyed (REX-only); it structurally cannot replace the universe-wide rule CSVs (~2,400 funds). GAP-08's real intent — a no-CSV override workflow — was already delivered by Phase 6. Physical CSV deletion descoped.
- **Track 4d — flag-file read path (#67).** Every `send_enabled` reader (`graph_email`, `admin` status+toggle, `weekly_digest`, `screener/email_report`) repointed through the DB-backed `system_flags` helper. Physical 14-file deletion descoped (files kept as the helper's fallback mirror).
- **Track 5A — status_history is the live authority (#69, #70).** Dry-run review caught two reconciler bugs (ETN blind spot would have demoted 21 trading funds; demote-on-absent-evidence). Fixed: Bloomberg `market_status` authoritative; never demote mid-lifecycle on absent evidence; `append_transition` also drives the legacy `rex_products.status`. Reconciler `--apply` run: **175 promotions**, 19 non-LIQU demotions skipped. Nightly bloomberg-chain now reconciles live.
- **Track 5 — canonical identity self-maintaining (#72, #73).** `ensure_canonical_identity.py` (new) runs the idempotent Phase 4 backfills nightly via the bloomberg-chain, so **new SEC-filed products auto-assign** `canonical_id` / `product_master` / `identifier_xref` / `fund_underlier`. Two latent bugs fixed: `backfill_underlier_master` + `backfill_fund_underlier` still `SELECT`ed the dropped `underlier` column; `backfill_fund_underlier` idempotency made per-product (a first run made 10 duplicate links — cleaned).
- **Track 5B — pre-filing product creation (#75).** `/admin/products` Add form creates `Under Consideration` products; the filing pipeline name-matches the eventual SEC filing — no duplicate. Also fixed `add_product` + `update_product`, latently broken by Track 4b's `underlier` hybrid (mapped the `underlier` form field to the real `underlying_ticker` column).
- **Track 6 — edgartools built ALONGSIDE the legacy (#76).** Per directive D1 (build everything; remove nothing until proven). `etp_tracker/edgar_client.py` = fully-defensive edgartools client; `scripts/edgar_shadow_compare.py` = read-only dual-run harness; `edgartools>=5.31.0` in requirements (verified additive-only install). **First shadow run: 100.0% filing-discovery parity** (edgartools and the in-house pipeline see the same 2,188 485-series filings over 30 days).
- **Track 0 — skipped** per operator directive (needs Render env access).
- **Fixes after the user returned:** `morning_triage_email.py` missing `sys.path` insert — the 08:00 triage email failed on its first fire (#78); `admin.py` dashboard 500 — Track 4d had dropped a `from pathlib import Path as _P` import still used later in the route (#79).
- **LOG docs:** #68, #71, #74, #77.

## 4. PRESENT — current production state (2026-05-20 ~09:45 ET)

- **The structural rebuild is complete and live.** Canonical identity, typed underliers, bi-temporal status (live authority), classification override, state consolidation — all in production and **self-maintaining** (new funds auto-onboard nightly).
- **25/25 assertions pass.** Last run 08:00 ET.
- **Two legacy artifacts physically retired:** `capm_products` table, `rex_products.underlier` column — both behind proven equivalence gates.
- **edgartools** runs in shadow alongside the legacy extractor — 100% filing-discovery parity proven; legacy stays authoritative.
- **In progress right now:** VPS `data/backups` (~9.3 GB) being archived to the D drive via `sync_vps_to_d_drive.sh`; VPS regenerable caches already cleared (`/home` 90%→89%).

## 5. FUTURE — what remains

| Item | Detail |
|---|---|
| **Track 6 cutover** (ADR 0010 Stage 4–5) | Extend `edgar_shadow_compare` from filing-*discovery* parity to *content-extraction* parity (series/class/fund fields). Once sustained zero divergence is proven, cut `fresh-poller`/`intraday-refresh` over to `edgar_client`, then retire the legacy stack. Operator-supervised. |
| **Track 0 — `ADMIN_PASSWORD` rotation** | The GitHub-exposed password is still live. `load_admin_password()` reads `config/.env` on the VPS and the `ADMIN_PASSWORD` env var on Render. Needs Render dashboard access. Belongs with the deferred Phase 0a security hardening. |
| **Calendar-gated retirements** | `capm_audit_log` retained; the 6 rule CSVs + 14 flag files were descoped from physical deletion (4c/4d) — the DB is already the authoritative path. |
| **VPS backup retention** | After the D-drive archive completes, prune the VPS `data/backups` (14 daily + 5 stale one-off `.bak`/`pre-*` snapshots from May 12–13). Keep recent dailies locally. |

## 6. Known operational issues

- **CBOE cookie EXPIRED.** `rexfinhub-cboe.service` fails 403. The cookie `oaik6f…` Ryu provided does NOT authenticate (probed twice, 403 both times — likely the wrong devtools field or already stale). Needs a fresh `sessionid` from CBOE devtools → then the `/cboe-cookie` skill rotates it + dispatches the recovery sweep.
- **VPS `/home` disk** was at 90%; caches cleared → 89%; backups being archived to D then pruned.
- **First live nightly run:** tonight's 17:15 ET bloomberg-chain is the FIRST with the new post-steps (`ensure_canonical_identity` + `status_reconciler --apply`). Worth checking the next morning-triage email + assertions.
- **Render** showed transient 502s during redeploys this session — they self-resolve in ~60s (cold-start / deploy).

## 7. Key gotchas for the next session

- `rex_products.underlier` is a **hybrid_property**, not a column — has no setter; write `underlying_ticker` instead.
- `capm_products` table and the `CapMProduct` model **no longer exist**.
- The reconciler **promotes on evidence, delists only on Bloomberg LIQU** — it never demotes mid-lifecycle on absent evidence (by design — ETNs have no 485 forms).
- `ensure_canonical_identity.py` is idempotent and wired into the bloomberg-chain — do NOT add `backfill_underlier_master` to it (it mints noisy heuristic rows).
- The repo's canonical GitHub name is `the-atlas-protocol/rexfinhub`; `ryuoelasmar/rexfinhub` redirects. `gh pr create` mis-routes — use `gh api repos/ryuoelasmar/rexfinhub/pulls`.
- VPS `rexfinhub-api.service` is port **8001** (pipeline API), not the full webapp.

## 8. What must remain in context after compaction

The minimum a post-compaction session needs:

1. **The rebuild is COMPLETE** — Tracks 1–6 delivered (18 PRs #62–#79), Track 0 skipped per operator directive. Production is live, 25/25 assertions.
2. **This doc + `docs/LOG.md` (2026-05-20 entries) + `docs/raw/ops/REBUILD-COMPLETION-PLAN_2026-05-19.md`** are the authoritative record — re-read them, don't reconstruct from memory.
3. **Open operator items:** (a) CBOE cookie needs a fresh valid `sessionid`; (b) VPS backup prune pending the D-drive archive finishing; (c) Track 6 cutover + Track 0 password are operator-supervised future work.
4. **Working rules:** never autonomously delete (surface + wait); never push unvalidated code to the live SEC-ingestion path; the repo PR flow is `gh api repos/ryuoelasmar/rexfinhub/pulls` then squash-merge; deploy = merge → `ssh jarvis@46.224.126.196 "cd ~/rexfinhub && git pull --ff-only"` (+ restart `rexfinhub-api.service` for webapp code).
5. **The Section 7 gotchas above** — especially the `underlier` hybrid and the dropped `capm_products`.
