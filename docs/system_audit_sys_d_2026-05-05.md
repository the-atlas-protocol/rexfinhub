# Sys-D: Report Builder Coupling Audit — 2026-05-05

## TL;DR
- Builders audited: 7 (daily, weekly, li, income, flow, autocall, stock_recs)
- 3 CRITICAL/HIGH bugs identified
- Top bug: `scripts/send_all.py:87` — `_build_stock_recs` ships stale cached HTML when file already exists, with freshly-stamped subject date

## Top 3 highest-severity bugs

### Bug #1 — CRITICAL: `scripts/send_all.py:87`

**Symptom**: `_build_stock_recs` checks `if not html_path.exists()` before calling the builder. If `reports/li_weekly_v2_<today>.html` exists from any prior run (preflight, manual test, etc.), today's send skips the rebuild and ships the cached HTML.

**Concrete failure**: 2026-05-04 — Stream C built stock_recs at 18:30 ET (pre-BBG-refresh at 21:00). Atlas's catch-up build at 22:01 read the cached 18:30 HTML. Subject said "Stock Recommendations of the Week - May 04, 2026" but body reflected pre-refresh data. Required manual `rm` + rebuild to get fresh content.

**Fix**:
```python
def _build_stock_recs(db) -> tuple[str, str]:
    html_path = PROJECT_ROOT / "reports" / f"li_weekly_v2_{date.today().isoformat()}.html"
    # Always rebuild OR enforce max age
    MAX_AGE_S = 3600  # 1 hour
    if html_path.exists():
        age = time.time() - html_path.stat().st_mtime
        if age > MAX_AGE_S:
            html_path.unlink()
    if not html_path.exists():
        from screener.li_engine.analysis.weekly_v2_report import main as build_main
        build_main()
    ...
```

### Bug #2 — HIGH: `scripts/send_email.py:156` `_data_date(db)`

**Symptom**: Every report's subject-line date is computed via `_data_date(db)` which reads from `get_li_report(db)` (L&I market cache). If that cache row is absent or stale, falls back silently to `datetime.now()` wall-clock.

**Failure mode**: On a Monday after a holiday weekend, if the L&I cache wasn't refreshed, all 6+ reports ship with today's date while Bloomberg data reflects Friday's close — no error, no alert.

**Fix**: Pass an explicit `as_of` date through each builder OR raise if cache missing rather than falling back.

### Bug #3 — HIGH: `webapp/services/report_emails.py:1478-1479` (also 1654-1655)

**Symptom**: Flow and autocall builders silently switch data sources when the cached format predates the `grand_kpis` key. They call `get_flow_report(None)` — the file-based in-process path — without logging.

**Render impact**: `_ON_RENDER=True` blocks the file path → returns empty → report body says "Flow report data not available" with no alert fired and no exit code change.

**Fix**: Either log the fallback or fail fast if cache is in legacy format on Render.

## Per-builder coupling notes

The 7 builders share inconsistent infrastructure:
- 3 different number formatters (`_fb`, `_fmt_aum`, inline `f"${x:.1f}M"`)
- 4 different date formatters
- HTML wrappers duplicated in each builder rather than centralized
- Color palettes overlap but don't match exactly

## Recommendations

1. **Patch Bug #1 immediately** — always rebuild or enforce max-age
2. **Patch Bug #2** — explicit `as_of` parameter
3. **Patch Bug #3** — log fallback or fail fast
4. Centralize formatters in `webapp/services/report_format.py`
5. Add unit tests covering the cache-miss / stale-cache paths

---

*Audit performed by Sys-D bot, 2026-05-05 22:25 ET*
