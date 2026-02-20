# AGENT: Market-Complete
**Task**: TASK-B — Market Intelligence Complete Fix + Enhancement
**Branch**: feature/market-complete
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Market-Complete.md`
Format: `## [HH:MM] Task description` then bullet details.
Update at every major step.

## Your Files (own completely)
- `webapp/services/market_data.py`
- `webapp/routers/market.py`
- `webapp/templates/market/*.html` (all market templates)
- `webapp/static/css/market.css`
- `webapp/static/js/market.js`

## CRITICAL: Read These First
Read ALL of these before touching anything:
- `webapp/services/market_data.py`
- `webapp/routers/market.py`
- `webapp/templates/market/rex.html`
- `webapp/templates/market/base.html`
- `webapp/templates/market/category.html`
- `webapp/templates/market/treemap.html`
- `webapp/templates/market/issuer.html`
- `webapp/templates/market/share_timeline.html`
- `webapp/templates/market/underlier.html`
- `webapp/static/css/market.css`
- `webapp/static/js/market.js`

## Fix 1: Market 500 Error (HIGHEST PRIORITY)

In `webapp/templates/market/rex.html`, the `{% block market_scripts %}` section has an UNGUARDED sparklines loop that causes Jinja2 UndefinedError when `summary` is not in context (data unavailable, or exception in get_rex_summary).

Find this pattern in rex.html:
```jinja2
  // Sparklines
  {% for suite in summary.suites %}
  {% if suite.sparkline_data %}
  MarketCharts.renderSparkline(...)
  {% endif %}
  {% endfor %}
```

Wrap it:
```jinja2
  {% if summary %}
  {% for suite in summary.suites %}
  {% if suite.sparkline_data %}
  MarketCharts.renderSparkline(...)
  {% endif %}
  {% endfor %}
  {% endif %}
```

## Fix 2: market_data.py — Data Path Auto-detect

In `webapp/services/market_data.py`, replace the hardcoded DATA_FILE line with auto-detect:

```python
from datetime import datetime as _dt

_LOCAL_DATA = Path(r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - Rex Financial LLC\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx")
_FALLBACK_DATA = Path("data/DASHBOARD/The Dashboard.xlsx")
DATA_FILE = _LOCAL_DATA if _LOCAL_DATA.exists() else _FALLBACK_DATA
```

Add `get_data_as_of()` function after `invalidate_cache()`:
```python
def get_data_as_of() -> str:
    """Return file modification date as 'Feb 20, 2026' or empty string."""
    try:
        return _dt.fromtimestamp(DATA_FILE.stat().st_mtime).strftime("%b %d, %Y")
    except Exception:
        return ""
```

## Fix 3: market.py — Pass data_as_of to all routes

In `webapp/routers/market.py`, every route that calls `templates.TemplateResponse()` must pass `"data_as_of": svc.get_data_as_of()` in the context dict. This applies to ALL routes: rex_view, category_view, treemap_view, issuer_view, share_timeline_view, underlier_view. For routes with `available=False` fallback, also pass data_as_of there.

## Fix 4: market/base.html — Show as-of date

After the nav pills `<div class="market-nav-pills">...</div>`, add:
```html
{% if data_as_of %}<div class="market-data-date">Data as of {{ data_as_of }}</div>{% endif %}
```

## Fix 5: market.css — Undefined CSS variables

Replace ALL occurrences of undefined CSS variables in market.css:
- `var(--text-primary)` → `var(--navy)` (or `#0F172A` if not resolving)
- `var(--text-secondary)` → `#374151`
- `var(--text-muted)` → `#94A3B8`
- `var(--accent)` → `var(--blue)`
- `var(--bg-card)` → `#FFFFFF`

Use replace_all=true on each substitution.

## Enhancement 1: Rex View — Executive Suite Table

Replace the `<div class="suite-cards">` loop in rex.html with a Bloomberg-style sortable table:

```html
<table class="data-table suite-table" id="suiteTable">
  <thead>
    <tr>
      <th>Suite</th>
      <th class="sortable" data-col="1">AUM</th>
      <th class="sortable" data-col="2">1W Flow</th>
      <th class="sortable" data-col="3">1M Flow</th>
      <th class="sortable" data-col="4">Mkt Share</th>
      <th class="sortable" data-col="5"># Funds</th>
      <th>4-mo Trend</th>
      <th>Top Movers</th>
    </tr>
  </thead>
  <tbody>
    {% for suite in summary.suites %}
    <tr data-suite="{{ suite.name }}">
      <td><a href="/market/category?cat={{ suite.name|urlencode }}" class="text-link">{{ suite.short_name }}</a></td>
      <td class="text-mono" data-sort="{{ suite.kpis.aum_raw|default(0) }}">{{ suite.kpis.aum_fmt }}</td>
      <td class="text-mono {{ 'flow-positive' if suite.kpis.flow_1w > 0 else 'flow-negative' if suite.kpis.flow_1w < 0 else '' }}" data-sort="{{ suite.kpis.flow_1w|default(0) }}">{{ suite.kpis.flow_1w_fmt }}</td>
      <td class="text-mono {{ 'flow-positive' if suite.kpis.flow_1m > 0 else 'flow-negative' if suite.kpis.flow_1m < 0 else '' }}" data-sort="{{ suite.kpis.flow_1m|default(0) }}">{{ suite.kpis.flow_1m_fmt }}</td>
      <td data-sort="{{ suite.market_share|default(0) }}">{{ suite.market_share }}%</td>
      <td data-sort="{{ suite.kpis.num_products|default(0) }}">{{ suite.kpis.num_products }}</td>
      <td style="min-width:90px;"><canvas id="sparkline-{{ suite.short_name|replace(' ', '-') }}" width="88" height="30" class="sparkline-canvas"></canvas></td>
      <td class="movers-cell">{% for m in suite.top_movers[:3] %}<span class="{{ 'flow-positive' if m.flow_raw > 0 else 'flow-negative' if m.flow_raw < 0 else '' }}">{{ m.ticker }} {{ m.flow }}</span>{% if not loop.last %} &middot; {% endif %}{% endfor %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

Also: update `toggleSuite()` JS to toggle `<tr data-suite="...">` rows instead of `.suite-card`.

Check what fields exist on suite.kpis by reading market_data.py's `get_rex_summary()` function. If `flow_1m`, `aum_raw`, etc. don't exist, use what does exist. Adapt the template to match actual data fields.

## Enhancement 2: Category View — Pill Selector

In `webapp/templates/market/category.html`, replace any `<select>` dropdown with pill buttons:
```html
<div class="category-pills">
  <a href="/market/category?cat=All" class="pill {{ 'active' if cat == 'All' else '' }}">All</a>
  {% for c in all_categories %}
  <a href="/market/category?cat={{ c|urlencode }}" class="pill {{ 'active' if cat == c else '' }}">{{ c }}</a>
  {% endfor %}
</div>
```

In market.py's category_view route, pass `all_categories` (list of unique category names from get_category_summary() or get_all_categories()).

## Enhancement 3: All 4 Tabs — Verify and Fix

For treemap.html, issuer.html, share_timeline.html, underlier.html:
1. Read each template carefully
2. Check the `{% block market_scripts %}` section for unguarded variable access
3. Wrap ALL data access in `{% if varname %}` guards
4. Check market.py routes — ensure ALL template context vars needed by the template are passed
5. Fix any CSS issues (undefined variables, broken layout)

## Enhancement 4: market.css New Styles

Add:
```css
.market-data-date { font-size: 0.72rem; color: #94A3B8; text-align: right; margin-top: 4px; }
.suite-table td { vertical-align: middle; padding: 10px 12px; }
.suite-table .movers-cell { font-size: 0.77rem; }
.suite-table .mover { white-space: nowrap; }
.category-pills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.text-link { color: var(--blue); text-decoration: none; font-weight: 600; }
.text-link:hover { text-decoration: underline; }
```

## Enhancement 5: market.js — Table Sorting

Add `sortTable(tableId, colIndex)` if not already present. Check `webapp/static/js/app.js` first — if sortTable exists there (lines 81-110), don't duplicate it. If not in market.js, add:
```js
function sortTable(tableId, colIndex) {
  var table = document.getElementById(tableId);
  var tbody = table.tBodies[0];
  var rows = Array.from(tbody.rows);
  var asc = table.dataset.sortCol == colIndex && table.dataset.sortDir == 'asc' ? false : true;
  table.dataset.sortCol = colIndex;
  table.dataset.sortDir = asc ? 'asc' : 'desc';
  rows.sort(function(a, b) {
    var av = parseFloat(a.cells[colIndex].dataset.sort || a.cells[colIndex].textContent) || 0;
    var bv = parseFloat(b.cells[colIndex].dataset.sort || b.cells[colIndex].textContent) || 0;
    return asc ? av - bv : bv - av;
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
```

## Commit Convention
```
git add webapp/services/market_data.py webapp/routers/market.py webapp/templates/market/ webapp/static/css/market.css webapp/static/js/market.js
git commit -m "feat: Market intelligence complete overhaul - fix 500 error, executive suite table, category pills, data-as-of"
```

## Done Criteria
- [x] `/market/rex` loads without 500 (even with no data file)
- [x] Data path auto-detects OneDrive file
- [x] "Data as of DATE" shown in market/base.html
- [x] Suite table replaces cramped cards
- [x] Category pills replace dropdown
- [x] All 4 other tabs load without errors
- [x] market.css has no undefined CSS variable references

## Log
- f3a2bff: fix: market data path auto-detect, data-as-of date, CSS variable fixes
- 3065f8a: feat: replace category dropdown with pill selector
