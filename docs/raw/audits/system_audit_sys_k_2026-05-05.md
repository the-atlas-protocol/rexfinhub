# Sys-K: Dependencies + Cost + Monitoring + Code Sync — 2026-05-05

## TL;DR

| Dimension | Finding |
|---|---|
| Total deps | 27 (24 range-pinned, 1 unpinned, 0 exact pins) |
| Public CVEs in deps | 4 confirmed (Jinja2 ×3, Starlette ×1), 1 probable (requests) |
| Daily Anthropic spend (est.) | $0.26/day normal; up to $1,200/month if key leaked |
| Local ↔ origin sync | ✅ Matched |
| VPS sync | ⚠️ Unverifiable read-only |
| **Top risk** | **Jinja2 `<3.1.7` upper bound BLOCKS the patch for sandbox RCE (CVE-2025-27516)** |

## CVE Exposure

| Package | CVE | Severity | Fixed In | Status |
|---|---|---|---|---|
| jinja2 | CVE-2024-22195 | Medium XSS | 3.1.3 | Blocked by `<3.1.7` cap |
| jinja2 | CVE-2024-56201 | High RCE | 3.1.5 | Blocked by `<3.1.7` cap |
| **jinja2** | **CVE-2025-27516** | **CRITICAL sandbox breakout** | **3.1.6** | **BLOCKED by `<3.1.7` cap** |
| starlette | CVE-2025-62727 | High ReDoS via Range header | 0.49.1 | Allowed — could install <0.49.1 |
| requests | CVE-2024-47081 | Medium .netrc leak | 2.32.4 | Allowed — could install <2.32.4 |

**Critical**: `jinja2>=3.1.0,<3.1.7` is the worst constraint in `requirements.txt`. Even 3.1.6 (which fixes CVE-2025-27516 sandbox RCE) is excluded by the upper bound. **One-line fix**: change to `jinja2>=3.1.6,<4.0.0`.

## Cost Exposure (Anthropic)

| Path | Daily | Monthly | Cap? |
|---|---|---|---|
| On-demand filing analysis (Sonnet 4.6, ~2 calls/day avg) | $0.08 | $2.40 | DAILY_ANALYSIS_LIMIT=10 in-app |
| "Top Filings" daily pipeline (Haiku selector + 3× Sonnet writer) | $0.18 | $5.40 | Cache-first |
| AI classification (Haiku, on-demand) | $0 | $0 | — |
| **Total normal** | **$0.26** | **~$7.80** | |
| **Worst case (leaked key)** | uncapped | **$1,200+/mo** | **No API-level cap** |

Other services: Render Starter $7/mo + 1GB disk $0.25 = $7.25/mo. No Bloomberg/Quandl. yfinance is free. Azure/Graph included in M365.

## Error Monitoring

**Exists**: 342 log statements + `send_critical_alert()` (email-only) with 1h cooldown.

**Absent**: Sentry/Rollbar, structured log shipping, dead-letter queue, Render log retention >7 days, exception telemetry on 5xx errors.

**Critical alert path gaps**:
1. Cooldown is in-process state → resets on Render restart → burst-fail-restart-fail loop sends one alert per restart, not per hour
2. No burst buffering — 50 failures in 30s → only 1 alert, 49 lost
3. Graph API dependency for alerts → if Azure expires, alerting itself fails silently
4. **`send_critical_alert` called only from local pipeline scripts — Render webapp has zero alerting path**

## Code Sync

| Surface | Branch | SHA | Status |
|---|---|---|---|
| Local | main | 5d3ef065 | HEAD |
| GitHub origin | main | 5d3ef065 | ✅ Match |
| VPS | unknown | — | Cannot verify (no SSH read in audit) |
| Render | main (auto-deploy on push) | Should match GitHub if deploy succeeded | Assumed current |

## CI/CD + Tests

- **2 GitHub Actions workflows**: `pr-checks.yml` runs syntax check + CSV validation + `pytest tests/` on PR; `notify-push.yml` writes audit summary on push.
- **No linting** (no flake8/ruff/mypy)
- **No pip-audit / safety scan** in CI
- **No Render preview deploys**
- **Push to main = live deploy with zero test gate**
- **11 test files** vs 50+ source modules. Integration-light. No pipeline step tests, no SEC client tests, no email delivery tests.

## Top 5 Risks

1. **Jinja2 `<3.1.7` cap blocks sandbox RCE patch (CVE-2025-27516)** — one-line fix
2. **No Anthropic API-level spend cap** — leaked key = unbound $$, in-app limit only covers Path 1
3. **Zero runtime error telemetry on Render** — 5xx/OOM/crash invisible after 7 days
4. **Starlette `<0.49.1` allowed** — Range header ReDoS DoS risk
5. **yfinance fully unpinned** — breaking releases silently corrupt price data, no test coverage to catch

## Recommendations (Priority-Ordered)

| P | Action |
|---|---|
| **P0** | `requirements.txt`: `jinja2>=3.1.6,<4.0.0` (one-line) |
| **P0** | Set Anthropic console: alert at $20/mo, hard cap at $50/mo |
| P1 | `requirements.txt`: `starlette>=0.49.1,<0.53.0` |
| P1 | `requirements.txt`: `requests>=2.32.4` |
| P1 | `requirements.txt`: pin `yfinance>=0.2.50` |
| P2 | Add Sentry free tier to `webapp/main.py` (5K errors/mo free, 1-line setup) |
| P2 | Add `pip-audit` step to `pr-checks.yml` |
| P2 | Anthropic API key rotation reminder in `.env.example` |
| P3 | FastAPI exception handler → `send_critical_alert` for Render 5xx |
| P3 | Verify VPS SHA matches GitHub via `git -C /home/jarvis/rexfinhub log -1 --oneline` |
| P3 | Cleanup 13 stale `.claude/worktrees/agent-*` directories |

---

*Audit by Sys-K bot, 2026-05-05. Read-only.*
