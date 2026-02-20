# AGENT: Dashboard-Pages
**Task**: TASK-D — Dashboard Real Pagination
**Branch**: feature/dashboard-pages
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Dashboard-Pages.md`
Format: `## [HH:MM] Task description` then bullet details.

## Your Files
- `webapp/routers/dashboard.py`
- `webapp/templates/dashboard.html`

## CRITICAL: Read These First
Read ALL of these before modifying:
- `webapp/routers/dashboard.py` (understand current limit logic, query structure, template context)
- `webapp/templates/dashboard.html` (understand current layout, where to insert pagination UI)

## Fix: Real Pagination in dashboard.py

### Current Problem
The dashboard currently uses `filing_limit = min(500, max(50, days * 4))` which hard-limits results. With `days=7`, you get at most 28 filings. Users can't see more.

### Solution

In `webapp/routers/dashboard.py`:

1. Add imports at top:
```python
import math
from sqlalchemy import func
```

2. Add `page` and `per_page` query parameters to the dashboard route function signature:
```python
page: int = Query(default=1, ge=1),
per_page: int = Query(default=50, ge=10, le=200),
```

3. Find the `filing_limit` block (something like `filing_limit = min(500, ...)` then `.limit(filing_limit)`) and replace it with:
```python
# Count total BEFORE applying limit
total_filings = db.execute(
    select(func.count()).select_from(filing_query.subquery())
).scalar() or 0
total_pages = max(1, math.ceil(total_filings / per_page))
page = min(page, total_pages)

# Apply pagination
filings = db.execute(
    filing_query.offset((page - 1) * per_page).limit(per_page)
).all()
```

Note: The existing code likely does `filings = db.execute(filing_query.limit(N)).all()` — find that pattern and split it into count + paginated fetch.

4. Build `base_qs` — a URL query string preserving all current filter params (days, form_type, filing_trust_id, per_page) WITHOUT page:
```python
import urllib.parse
qs_params = {}
if days != 30:  # or whatever the default is
    qs_params['days'] = days
if form_type:
    qs_params['form_type'] = form_type
if filing_trust_id:
    qs_params['filing_trust_id'] = filing_trust_id
if per_page != 50:
    qs_params['per_page'] = per_page
base_qs = urllib.parse.urlencode(qs_params)
```

5. Add to template context:
```python
"page": page,
"per_page": per_page,
"total_filings": total_filings,
"total_pages": total_pages,
"base_qs": base_qs,
```

## Pagination UI in dashboard.html

Find the area after the filings table (look for `</table>` or end of table section) and add:
```html
{% if total_pages > 1 %}
<div class="pagination-bar">
  <span class="pagination-info">{{ total_filings }} filings &middot; page {{ page }} of {{ total_pages }}</span>
  <div class="pagination-controls">
    {% if page > 1 %}
    <a href="?{{ base_qs }}{% if base_qs %}&{% endif %}page={{ page - 1 }}" class="btn btn-sm">Prev</a>
    {% endif %}
    {% set start_p = [1, page - 2]|max %}
    {% set end_p = [total_pages + 1, page + 3]|min %}
    {% for p in range(start_p, end_p) %}
    <a href="?{{ base_qs }}{% if base_qs %}&{% endif %}page={{ p }}" class="btn btn-sm {{ 'btn-primary' if p == page else '' }}">{{ p }}</a>
    {% endfor %}
    {% if page < total_pages %}
    <a href="?{{ base_qs }}{% if base_qs %}&{% endif %}page={{ page + 1 }}" class="btn btn-sm">Next</a>
    {% endif %}
  </div>
  <select class="select-sm" onchange="location='?{{ base_qs }}{% if base_qs %}&{% endif %}page=1&per_page='+this.value">
    {% for n in [25, 50, 100, 200] %}
    <option value="{{ n }}"{{ ' selected' if n == per_page else '' }}>{{ n }}/page</option>
    {% endfor %}
  </select>
</div>
{% endif %}
```

Note: The `.pagination-bar` and `.select-sm` CSS classes are being added by Agent A (Home-Design) in style.css. They will exist when merged. For now, add the HTML.

## Edge Cases
- If `total_filings == 0`, no pagination bar shown (already handled by `{% if total_pages > 1 %}`)
- If user requests `page=999` but only 5 pages exist, `page = min(page, total_pages)` clamps it
- Check that the existing `filing_query` is an ORM select() statement before applying count subquery. If it's already executed, restructure appropriately.

## Important: Check Existing Query Structure
The dashboard.py might structure queries differently. READ the file carefully. The key pattern to find:
- Where does it call `.limit()`?
- Is `filing_query` a select() statement or already executed?
- What does it return — `.all()`, `.scalars()`, `mappings()`?

Adapt the pagination code to match the existing pattern.

## Commit Convention
```
git add webapp/routers/dashboard.py webapp/templates/dashboard.html
git commit -m "feat: Dashboard real pagination - page/per_page params, count query, pagination UI"
```

## Log
- `1b7940e` feat: add real pagination to dashboard route with page/per_page params
- `34c4fdd` feat: add pagination UI to dashboard template with page controls and per_page selector

## Done Criteria
- [ ] Dashboard loads with pagination controls when filings exceed per_page
- [ ] `?page=2` works correctly
- [ ] `?per_page=100` works correctly
- [ ] Filter params preserved across page navigation
- [ ] Total filing count shown in pagination bar
- [ ] No hard limit — all filings accessible via pagination
