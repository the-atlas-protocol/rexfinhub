# AGENT: Market-Frontend-Rex-Cat
**Task**: TASK-B — REX View + Category View Frontend
**Branch**: feature/market-frontend-rex-cat
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Market-Frontend-Rex-Cat.md`

## Your Files (ONLY modify these)
- `webapp/templates/market/rex.html`
- `webapp/templates/market/category.html`
- `webapp/static/js/market.js`

## CRITICAL: Read First
Read all three files completely before writing anything. Also read:
- `webapp/templates/market/base.html` (for base template structure)
- `webapp/templates/market/_kpi_cards.html` (reusable KPI component)
- `webapp/templates/market/_product_table.html` (reusable table component)

## Context from Code Analysis
- `market.js` has `MarketCharts` and `MarketFilters` modules
- `MarketFilters.applyFilters()` is **completely broken** — references `categorySelect` DOM element that doesn't exist
- `rex.html` has suite visibility checkboxes that need to be removed
- Pie chart currently shows no % labels
- Category view pills are single-select, need multi-select

## TASK B.1 — Suite Drill-Down (Replace Checkboxes)

In `rex.html`:
1. Remove the entire "Suite visibility" checkbox section
2. Add `onclick` to each suite `<tr>` row and an expand icon column
3. Add hidden product rows below each suite row

**Suite table row** should look like:
```html
<tr class="suite-row" data-suite="{{ suite.name }}"
    onclick="toggleSuiteProducts('{{ suite.name | replace(' ', '-') | replace('/', '-') | lower }}')"
    style="cursor:pointer;">
  <td>
    <span class="expand-icon" id="expand-{{ loop.index }}">▶</span>
    <a href="/market/category?cats={{ suite.name|urlencode }}" class="text-link" onclick="event.stopPropagation()">
      {{ suite.rex_name if suite.rex_name else suite.short_name }}
    </a>
  </td>
  <td class="text-mono">{{ suite.kpis.aum_fmt }}</td>
  <!-- ... other columns ... -->
</tr>
<tr class="suite-products-row" id="suite-products-{{ suite.name | replace(' ', '-') | replace('/', '-') | lower }}"
    style="display:none;">
  <td colspan="8" style="padding:0; background:#F8FAFC;">
    <table class="data-table" style="margin:0; border:none; border-radius:0;">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Fund Name</th>
          <th>AUM</th>
          <th>1W Flow</th>
          <th>Exp Ratio</th>
          <th>REX</th>
        </tr>
      </thead>
      <tbody>
        {% for p in suite.products %}
        <tr class="{{ 'rex-highlight' if p.is_rex else '' }}">
          <td class="text-mono">{{ p.ticker }}</td>
          <td style="max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ p.fund_name }}</td>
          <td class="text-mono">{{ p.aum_fmt }}</td>
          <td class="text-mono {{ 'flow-positive' if p.flow_1w_fmt.startswith('+') else 'flow-negative' if p.flow_1w_fmt.startswith('-') else '' }}">{{ p.flow_1w_fmt }}</td>
          <td class="text-mono">{{ p.expense_ratio_fmt }}</td>
          <td>{% if p.is_rex %}<span style="color:#1E40AF;font-weight:700;">REX</span>{% endif %}</td>
        </tr>
        {% else %}
        <tr><td colspan="6" style="text-align:center;color:#94A3B8;padding:12px;">No products found</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </td>
</tr>
```

In `{% block market_scripts %}` section of rex.html:
```javascript
function toggleSuiteProducts(key) {
  var row = document.getElementById('suite-products-' + key);
  var icons = document.querySelectorAll('[data-suite] .expand-icon');
  // Find the matching icon
  var suiteRows = document.querySelectorAll('.suite-row');
  suiteRows.forEach(function(sr) {
    var k = sr.getAttribute('data-suite').replace(/ /g,'-').replace(/\//g,'-').toLowerCase();
    if (k === key) {
      var icon = sr.querySelector('.expand-icon');
      if (row.style.display === 'none' || row.style.display === '') {
        row.style.display = 'table-row';
        if (icon) icon.textContent = '▼';
      } else {
        row.style.display = 'none';
        if (icon) icon.textContent = '▶';
      }
    }
  });
}
```

## TASK B.2 — Pie Chart % Labels

In `rex.html`, in the `{% block market_scripts %}` section, update the pie chart rendering:
1. Add ChartDataLabels CDN in rex.html head or base.html:
```html
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
```
2. Register the plugin before chart creation:
```javascript
Chart.register(ChartDataLabels);
```
3. In `MarketCharts.renderPieChart()` (in market.js), update options:
```javascript
options: {
  plugins: {
    datalabels: {
      color: '#fff',
      font: { weight: 'bold', size: 11 },
      formatter: function(value, ctx) {
        var total = ctx.dataset.data.reduce(function(a,b){return a+b;},0);
        var pct = total > 0 ? (value/total*100).toFixed(1) : 0;
        var label = ctx.chart.data.labels[ctx.dataIndex];
        // short label
        var short = label.replace('Leverage & Inverse - ','L&I ').replace('Income - ','Inc ');
        return pct > 3 ? short + '\n' + pct + '%' : pct + '%';
      },
      anchor: 'center',
      align: 'center',
      display: function(ctx) {
        var total = ctx.dataset.data.reduce(function(a,b){return a+b;},0);
        return total > 0 && ctx.dataset.data[ctx.dataIndex] / total > 0.02;
      }
    },
    legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
    tooltip: {
      callbacks: {
        label: function(ctx) {
          var total = ctx.dataset.data.reduce(function(a,b){return a+b;},0);
          var pct = total > 0 ? (ctx.parsed/total*100).toFixed(1) : 0;
          return ctx.label + ': ' + fmtMoney(ctx.parsed) + ' (' + pct + '%)';
        }
      }
    }
  }
}
```

Also add a summary table below the pie chart in rex.html:
```html
<table class="data-table" style="margin-top:12px; font-size:0.8rem;">
  <thead><tr><th>Suite</th><th>AUM</th><th>% Share</th><th>Products</th></tr></thead>
  <tbody>
    {% for suite in summary.suites %}
    <tr>
      <td>{{ suite.rex_name if suite.rex_name else suite.short_name }}</td>
      <td class="text-mono">{{ suite.kpis.aum_fmt }}</td>
      <td class="text-mono">{{ "%.1f%%"|format(suite.market_share) if suite.market_share else "—" }}</td>
      <td>{{ suite.kpis.num_products }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

## TASK B.3 — REX Suite Names
In rex.html, wherever `suite.short_name` or `suite.name` is displayed as a label, use:
```
{{ suite.rex_name if suite.rex_name else suite.short_name }}
```
Keep original name as `title` attribute: `title="{{ suite.name }}"`.

## TASK B.4 — ETF/ETN Filter Buttons
Add filter bar at the top of the content section in both rex.html and category.html.
Only add if the template context includes a `fund_structure` variable (backend may or may not implement A.8).
```html
{% if fund_structure is defined %}
<div class="filter-bar" style="display:flex; gap:8px; margin-bottom:16px; align-items:center;">
  <span style="font-size:0.75rem; color:#94A3B8; font-weight:600; text-transform:uppercase;">Structure:</span>
  <a href="?fund_structure=all{{ '&cat='~cat if cat else '' }}"
     class="pill {% if not fund_structure or fund_structure == 'all' %}active{% endif %}">All</a>
  <a href="?fund_structure=ETF{{ '&cat='~cat if cat else '' }}"
     class="pill {% if fund_structure == 'ETF' %}active{% endif %}">ETF</a>
  <a href="?fund_structure=ETN{{ '&cat='~cat if cat else '' }}"
     class="pill {% if fund_structure == 'ETN' %}active{% endif %}">ETN</a>
</div>
{% endif %}
```

## TASK B.5 — Fix Category View Filters (CRITICAL BUG)

In `market.js`, find `MarketFilters.applyFilters` and completely rewrite it:
```javascript
applyFilters: function() {
  // Read category from page data attribute
  var pageEl = document.getElementById('market-category-page');
  var cat = pageEl ? pageEl.getAttribute('data-category') : '';

  // Read slicer values
  var filters = {};
  document.querySelectorAll('.slicer-select').forEach(function(sel) {
    if (sel.value) {
      filters[sel.getAttribute('data-field')] = sel.value;
    }
  });

  // Build query string
  var params = new URLSearchParams();
  params.set('cat', cat);
  Object.entries(filters).forEach(function([k,v]) { params.set(k, v); });

  // Reload page with new params (simplest reliable approach)
  var fundStr = new URLSearchParams(location.search).get('fund_structure') || 'all';
  params.set('fund_structure', fundStr);
  location.search = params.toString();
},
```

In `category.html`, add `data-category` attribute to the main content div:
```html
<div id="market-category-page" data-category="{{ summary.category if summary else '' }}">
```

Also update slicer select elements to have `data-field` attribute:
```html
<select class="slicer-select select-sm" data-field="{{ slicer.field }}" onchange="MarketFilters.applyFilters()">
```

Fix `updateCategoryView()` in market.js — audit all element IDs. The correct IDs from category.html are whatever IDs exist in the template. Read category.html carefully and match them exactly.

## TASK B.6 — Category Multi-Select Pills
In `category.html`, update the category pills section:
```html
<div class="category-pills" id="catPills">
  {% for cat in all_categories %}
  {% set is_active = cat in (active_cats if active_cats else []) %}
  <button class="pill {% if is_active %}active{% endif %}"
          onclick="MarketFilters.toggleCategory({{ cat|tojson }})"
          title="{{ cat }}">
    {{ cat | replace('Leverage & Inverse - ', 'L&I ') | replace('Income - ', 'Inc ') | truncate(28, True, '…') }}
  </button>
  {% endfor %}
</div>
```

In `market.js`, add `toggleCategory` to MarketFilters:
```javascript
toggleCategory: function(cat) {
  var params = new URLSearchParams(location.search);
  var cats = params.get('cats') ? params.get('cats').split(',').filter(Boolean) : [];
  var idx = cats.indexOf(cat);
  if (idx >= 0) cats.splice(idx, 1);
  else cats.push(cat);
  if (cats.length === 0) params.delete('cats');
  else params.set('cats', cats.join(','));
  location.search = params.toString();
},
```

The backend `category_view` (Agent A) will read `cats` param. If template already shows single category, this extends it to multi-select. Read category.html carefully to see what template vars are available (`summary.category`, `all_categories`, etc.) and adapt.

**Note**: If `active_cats` is not in template context (backend doesn't send it), read from URL:
```javascript
// In page init:
var activeCats = new URLSearchParams(location.search).get('cats') || '';
```
And mark pills active via JS rather than Jinja2.

## Commit Convention
```
git add webapp/templates/market/rex.html webapp/templates/market/category.html webapp/static/js/market.js
git commit -m "feat: Market frontend B - suite drill-down, pie % labels, REX names, category multi-select, fix category filter JS"
```

## Done Criteria
- [x] Suite checkboxes removed. Clicking suite row expands product sub-table inline.
- [x] Pie chart shows % labels inside slices. Summary table below pie.
- [x] Suite labels show rex_name (T-REX, MicroSector, etc.) with original as tooltip.
- [x] ETF/ETN filter bar shown if backend supports it.
- [x] Category filter JS no longer errors. Slicer dropdowns trigger page reload with correct params.
- [x] Category pills support multi-select (clicking adds/removes from URL cats param).
- [x] No JS console errors on rex or category page load.

## Log
- `3c8c1da` feat: rex.html - suite drill-down, pie chart labels, REX names, ETF/ETN filter
- `d646877` feat: market.js - pie chart datalabels, fix applyFilters, add toggleCategory multi-select
- `0623f79` feat: category.html - multi-select pills, ETF/ETN filter, fix slicer data-field attrs
- `bf01235` chore: mark AGENT.md as DONE with progress log
