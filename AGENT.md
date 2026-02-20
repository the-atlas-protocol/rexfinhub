# Agent: WebappFixes
# Branch: feature/webapp-fixes
# Worktree: .worktrees/webappfixes
## Your Files (ONLY touch these)
- webapp/routers/downloads.py (EDIT)
- webapp/templates/downloads.html (EDIT)
- etp_tracker/trusts.py (EDIT)
- webapp/routers/dashboard.py (EDIT)
- webapp/templates/dashboard.html (EDIT)

## Shared Files (append-only - never remove existing code)
- webapp/main.py (TASK-001 adds market router include, TASK-002 adds startup event. Append only, do not rewrite existing includes.)
- webapp/templates/base.html (TASK-001 adds Market nav link. Append to nav section only.)
- webapp/static/css/style.css (TASK-003 may add loading skeleton styles. Append only.)

## Task: TASK-003
### Downloads Pagination + 33 Act ID + Dashboard Loading

Add pagination/search to downloads page, identify 33 Act filers and update dashboard display, and add loading skeleton indicator to dashboard.

**Acceptance Criteria**:
- Downloads page has pagination (50/page) and client-side search by fund name/ticker/trust
- 33 Act trusts identified with act_type mapping in trusts.py; dashboard shows '33 Act Filer - N-1A' instead of 'No 485 filings'
- Dashboard shows loading skeleton cards while trust data loads
- All existing functionality preserved without regressions


## Status: DONE

## Log:
- Added ACT_33_CIKS set + get_act_type() to etp_tracker/trusts.py (41 filers: crypto S-1, commodity trusts)
- Updated dashboard.py to pass act_type per trust via CIK lookup
- Dashboard template now shows '33 Act Filer - N-1A (S-1 / 10-K registration)' in blue for 33 Act trusts
- Added loading skeleton cards to dashboard trust grid (12 placeholder cards with pulse animation, revealed on window.load)
- Added skeleton CSS styles to style.css (append-only)
- Downloads page: added client-side search input + pagination (50/page) for Trust Filing Exports section
- Commit: 4d40c78
