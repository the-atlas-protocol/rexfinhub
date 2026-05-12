# data/rules/ — DEPRECATED (2026-05-11)

The single source of truth for all classification rule CSVs is now:

    config/rules/

This directory previously held a split-brain copy: `tools/rules_editor/classify_engine.py`
wrote here, but `market/config.py`, `webapp/routers/admin.py`, every `mkt_*` DB table,
and the live site all read from `config/rules/`. As a result, every classifier-approved
fund since the migration was invisible to the live site.

The split-brain was repaired in fix R6:
- `classify_engine.RULES_DIR` was repointed at `config/rules/`
- All orphan rows from `data/rules/` were merged into `config/rules/`
- The legacy CSVs in this folder were removed from git

See `docs/audit_2026-05-11/fix_R6.md` for the full diff counts, the merge strategy,
and the cross-category leakage findings that surfaced during the merge.

If you find code or scripts still pointing at `data/rules/`, update them to use
`market.config.RULES_DIR` instead.
