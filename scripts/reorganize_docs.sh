#!/bin/bash
# One-time reorganization of pre-2026-05-19-framework docs into docs/raw/{audits,design,ops,research,legacy}/
# per the layout defined in ADR 0001 + the cleanup audit findings.
#
# Uses git mv so history is preserved. No deletions.
#
# DRY RUN by default. Add --execute to actually run.

set -euo pipefail

EXECUTE=false
if [ "${1:-}" = "--execute" ]; then
    EXECUTE=true
fi

cd "$(dirname "$0")/.."

echo "=== Docs reorganization ==="
echo "Mode: $([ "$EXECUTE" = true ] && echo EXECUTE || echo DRY-RUN)"
echo

mkdir -p docs/raw/audits docs/raw/design docs/raw/ops docs/raw/research docs/raw/legacy

# Map: dest_subdir | files (space-separated)
move() {
    local dest=$1; shift
    for f in "$@"; do
        if [ -f "docs/$f" ]; then
            if [ "$EXECUTE" = true ]; then
                git mv "docs/$f" "docs/raw/$dest/$f"
                echo "  moved  docs/$f  ->  docs/raw/$dest/"
            else
                echo "  would move  docs/$f  ->  docs/raw/$dest/"
            fi
        fi
    done
}

# --- audits ---
move audits \
    audit_beta_classification_correctness.md \
    audit_beta_post_marker_recheck.md \
    audit_cross_pillar_walk_2026-05-08.md \
    audit_epsilon_mindmap_conformance.md \
    audit_eta_action_queue.md \
    audit_eta_whitespace_sunset.md \
    audit_filings_e2e_2026-05-08.md \
    audit_funds_e2e_2026-05-08.md \
    audit_issuers_e2e_2026-05-08.md \
    audit_stocks_e2e_2026-05-08.md \
    audit_trusts_e2e_2026-05-08.md \
    audit_zeta_category_deep_dives.md \
    attribute_completeness_report.md \
    cross_ref_integrity_report.md \
    classification_polish_review_2026-05-11.md \
    issuer_canonicalization_report.md \
    page_audit_filings_holdings_screener.md \
    page_audit_market_strategy.md \
    page_audit_notes_reports_admin.md \
    pipeline_phase2_audit_2026-05-11.md \
    rex_ops_audit_2026-05-11.md \
    system_audit_sys_d_2026-05-05.md \
    system_audit_sys_f_2026-05-05.md \
    system_audit_sys_g_2026-05-05.md \
    system_audit_sys_h_2026-05-05.md \
    system_audit_sys_i_2026-05-05.md \
    system_audit_sys_j_2026-05-05.md \
    system_audit_sys_k_2026-05-05.md \
    system_audit_sys_m_2026-05-05.md \
    webapp_taxonomy_audit.md

# --- design ---
move design \
    ARCHITECTURE.md \
    DATABASE_SCHEMA.md \
    LI_ENGINE_METHODOLOGY.md \
    MARKET_ATTRIBUTES.md \
    MARKET_PIPELINE.md \
    MARKET_PIPELINE_V2.md \
    MARKET_STRATEGIES.md \
    OWNERSHIP_PILLAR.md \
    PROJECT_STRUCTURE.md \
    SEC_IDENTIFIER_SYSTEM.md \
    website_architecture_v2_2026-05-07.md \
    website_architecture_v3_2026-05-08.md \
    website_mindmap_2026-05-06.md

# --- ops ---
move ops \
    AI_SETUP.md \
    AUTOMATION.md \
    DAILY_WORKFLOW.md \
    DEPLOYMENT_PLAN.md \
    EMAIL_PIPELINE.md \
    ENV_KEYS.md \
    LOCAL_DEV_SETUP.md \
    OPERATIONS.md \
    WORKFLOW.md

# --- research ---
move research \
    13f_activation_guide.md \
    BBG_RETURNS_INGEST_BUG.md \
    EFFECTIVE_DATES_AND_NAME_CHANGES.md \
    ETN_RESEARCH.md \
    SCRAPING_ANALYSIS.md

# --- legacy ---
move legacy \
    CLASSIFICATION_SYSTEM_PLAN.md \
    LOCKDOWN_2026-05-12.md \
    phase6_defined_outcome_backfill.md \
    PIPELINE_RETHINK.md \
    PLAN.md \
    POST_MORTEM_2026-05-05.md \
    PROJECT.md \
    ROADMAP.md \
    STATUS_2026-05-01.md \
    trex-strategy-system.md \
    website_FINAL_PLAN_2026-05-08.md

echo
if [ "$EXECUTE" = true ]; then
    echo "Done. Top of docs/ should now be only the six canonical files + DECISIONS/ + raw/."
    echo "Verify with:  ls docs/*.md"
    echo "Commit with:  git add -A docs/ && git commit -m 'docs: reorganize pre-framework artifacts into docs/raw/'"
else
    echo "Dry-run complete. To execute: bash scripts/reorganize_docs.sh --execute"
fi
