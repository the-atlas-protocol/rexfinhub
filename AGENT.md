# Agent: Market-Frontend
# Branch: feature/market-frontend
# Worktree: .worktrees/market-frontend

## Your Files (ONLY touch these)
- webapp/templates/market/rex.html (EDIT)
- webapp/templates/market/category.html (EDIT)
- webapp/templates/market/treemap.html (CREATE)
- webapp/templates/market/issuer.html (CREATE)
- webapp/templates/market/share_timeline.html (CREATE)
- webapp/templates/market/underlier.html (CREATE)
- webapp/templates/market/_suite_card.html (EDIT)
- webapp/static/css/market.css (EDIT)
- webapp/static/js/market.js (EDIT)

## Task: TASK-004
### Market Intelligence Frontend — Templates, CSS, JS for New Pages

Implement the frontend (templates, CSS, JS) for 4 new market intelligence pages and enhance the REX View. The backend routes and service functions are already complete on main.

**CRITICAL FIRST STEP**: Before writing any code, read the following files to understand the existing patterns and the backend API contract:
1. `webapp/routers/market.py` — routes and context variables passed to each template
2. `webapp/services/market_data.py` — return shapes of `get_treemap_data()`, `get_issuer_summary()`, `get_market_share_timeline()`, `get_underlier_summary()`
3. `webapp/static/js/market.js` — existing `MarketCharts` object (renderBarChart, renderLineChart, renderPieChart)
4. `webapp/static/css/market.css` — existing CSS classes
5. `webapp/templates/market/base.html` — base template, nav pills
6. `webapp/templates/market/rex.html` — existing REX View template
7. `webapp/templates/market/category.html` — existing Category View template
8. `webapp/templates/market/_suite_card.html` — existing suite card partial
9. `webapp/templates/market/_kpi_cards.html` — existing KPI cards partial

---

## Backend API Contract (do NOT deviate from these)

### Route context variables (from market.py):

**`/market/treemap`** → `treemap.html`:
- `available: bool`
- `active_tab: str = "treemap"`
- `summary: {"products": [{label, value, group, is_rex, ticker, fund_name, issuer, aum_fmt}], "total_aum": float, "total_aum_fmt": str, "categories": [...]}`
- `categories: list[str]`
- `category: str` (current filter, "All" or category name)

**`/market/issuer`** → `issuer.html`:
- `available: bool`
- `active_tab: str = "issuer"`
- `summary: {"issuers": [{issuer_name, total_aum, aum_fmt, flow_1w, flow_1w_fmt, flow_1m, flow_1m_fmt, num_products, market_share_pct, is_rex}], "total_aum": float, "total_aum_fmt": str, "categories": [...]}`
- `categories: list[str]`
- `category: str`

**`/market/share`** → `share_timeline.html`:
- `available: bool`
- `active_tab: str = "share"`
- `timeline: {"labels": [...], "series": [{"name": str, "short_name": str, "values": [float, ...]}]}`

**`/market/underlier`** → `underlier.html`:
- `available: bool`
- `active_tab: str = "underlier"`
- `summary: {"underliers": [{name, aum_fmt, num_products, num_rex}], "products": [{ticker, fund_name, direction, leverage, aum_fmt, flow_1w_fmt, yield_fmt, is_rex}], "underlier_type": str, "selected": str|None}`
- `underlier_type: str` ("income" or "li")
- `selected_underlier: str|None`

**`/market/rex` enhancements** — context now also includes `product_type: str` ("All", "ETF", or "ETN") and suites have `sparkline_data: [float, float, float, float]` (last 4 months AUM, oldest to newest).

---

## Implementation Tasks

### Task 1 — treemap.html (CREATE)

```html
{% set active_tab = 'treemap' %}
{% extends "market/base.html" %}

{% block title %}Product Treemap — REX Financial Intelligence Hub{% endblock %}

{% block head %}
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-treemap@3"></script>
{% endblock %}

{% block market_content %}
{# Category Filter #}
<div class="category-selector">
  <label for="catFilter">Category:</label>
  <select id="catFilter" onchange="window.location='/market/treemap?cat='+this.value">
    <option value="All" {{ 'selected' if category == 'All' else '' }}>All Categories</option>
    {% for cat in categories %}
    <option value="{{ cat }}" {{ 'selected' if category == cat else '' }}>{{ cat }}</option>
    {% endfor %}
  </select>
  <span class="kpi-value">{{ summary.total_aum_fmt }} total AUM</span>
</div>

{% if summary.products %}
<div class="treemap-container">
  <canvas id="treemapChart"></canvas>
</div>
{% else %}
<div class="alert alert-warning">No product data available for this category.</div>
{% endif %}
{% endblock %}

{% block market_scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
  {% if summary and summary.products %}
  var products = {{ summary.products|tojson }};
  MarketCharts.renderTreemap('treemapChart', products);
  {% endif %}
});
</script>
{% endblock %}
```

### Task 2 — issuer.html (CREATE)

Layout:
- Category filter dropdown (same as category.html pattern)
- KPI row: Total Market AUM, # Issuers
- Top-10 issuer bar chart (use `MarketCharts.renderBarChart()`)
- Sortable table with columns: #, Issuer, AUM, Market Share %, 1W Flow, 1M Flow, # Products — highlight REX rows
- `{% set active_tab = 'issuer' %}`
- Title: `{% block title %}Issuer Analysis — REX Financial Intelligence Hub{% endblock %}`

For the bar chart: extract labels and values from `summary.issuers[:10]`. Mark `is_rex` array for coloring.

For sorting: use `sortTable('issuerTable', N)`. Table must have `id="issuerTable"`.

### Task 3 — share_timeline.html (CREATE)

Layout:
- Heading: "Market Share by Category — Last 24 Months"
- Category toggle checkboxes (one per series) — JS hides/shows chart lines
- Canvas: `<canvas id="shareChart">`
- Call a NEW `MarketCharts.renderShareTimeline('shareChart', timelineData)` function

`renderShareTimeline` renders a multi-line chart with N series. Extend `market.js` to add this function to `MarketCharts`. It should accept `{labels: [...], series: [{name, short_name, values}]}` and render each series as a separate line with a distinct color. Use the color palette: `['#1E40AF', '#DC2626', '#059669', '#D97706', '#7C3AED', '#DB2777', '#0891B2']`.

- `{% set active_tab = 'share' %}`
- Title: `{% block title %}Market Share Timeline — REX Financial Intelligence Hub{% endblock %}`

### Task 4 — underlier.html (CREATE)

Two-panel layout:
- **Left panel** (`.underlier-list`): Type toggle (Income / L&I Single Stock) + scrollable list of underliers (each is a button/link that reloads with `?type=X&underlier=NAME`)
- **Right panel** (`.underlier-products`): When `selected_underlier` is set, show product table: Ticker, Fund Name, AUM, 1W Flow, Yield, REX badge. If L&I, also show Direction and Leverage columns.

URL patterns:
- `/market/underlier?type=income` — shows list, no products
- `/market/underlier?type=income&underlier=NVDA` — shows list + NVDA products
- `/market/underlier?type=li` — L&I single stock

AJAX enhancement (optional but preferred): clicking an underlier in the left panel fetches `/market/api/underlier?type=X&underlier=NAME` and updates the right panel without page reload.

- `{% set active_tab = 'underlier' %}`
- Title: `{% block title %}Underlier Deep-Dive — REX Financial Intelligence Hub{% endblock %}`

### Task 5 — REX View Enhancements (rex.html + _suite_card.html)

**rex.html enhancements**:
1. Add ETF/ETN filter toggle above suite cards:
   ```html
   <div class="product-type-filter">
     <a href="/market/rex?product_type=All" class="filter-btn {{ 'active' if product_type == 'All' else '' }}">All</a>
     <a href="/market/rex?product_type=ETF" class="filter-btn {{ 'active' if product_type == 'ETF' else '' }}">ETF</a>
     <a href="/market/rex?product_type=ETN" class="filter-btn {{ 'active' if product_type == 'ETN' else '' }}">ETN</a>
   </div>
   ```
2. Add category show/hide checkboxes (one per suite card). Use a `<div class="suite-filter">` with checkboxes. On uncheck, hide the corresponding `.suite-card` div via JS. Use `data-suite="{{ suite.name }}"` on suite cards.

3. Update title: `{% block title %}REX View — REX Financial Intelligence Hub{% endblock %}`

**_suite_card.html enhancements**:
Add a sparkline chart at the bottom of each suite card showing the last 4 months of AUM:
```html
{% if suite.sparkline_data and suite.sparkline_data|sum > 0 %}
<div class="sparkline-wrapper">
  <canvas id="sparkline-{{ suite.short_name|replace(' ', '-') }}" class="sparkline-canvas"></canvas>
</div>
{% endif %}
```

In `rex.html` `{% block market_scripts %}`, initialize sparklines:
```javascript
{% for suite in summary.suites %}
{% if suite.sparkline_data %}
MarketCharts.renderSparkline('sparkline-{{ suite.short_name|replace(" ", "-") }}', {{ suite.sparkline_data|tojson }});
{% endif %}
{% endfor %}
```

**Category View title** (`category.html`): Add `{% block title %}Category View — REX Financial Intelligence Hub{% endblock %}`.

### Task 6 — market.js: New Chart Helpers

Add to the `MarketCharts` object:

```javascript
renderTreemap: function(canvasId, products) {
  // chartjs-chart-treemap requires Chart.js 4.x
  // Group products by 'group' field
  var ctx = document.getElementById(canvasId);
  if (!ctx) return;

  // Color palette for groups
  var groupColors = {};
  var palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2','#65A30D'];
  var groups = [...new Set(products.map(p => p.group))];
  groups.forEach(function(g, i) { groupColors[g] = palette[i % palette.length]; });

  new Chart(ctx, {
    type: 'treemap',
    data: {
      datasets: [{
        label: 'AUM by Product',
        tree: products,
        key: 'value',
        groups: ['group'],
        backgroundColor: function(ctx) {
          if (!ctx.raw || !ctx.raw.g) return '#6B7280';
          var base = groupColors[ctx.raw.g] || '#6B7280';
          return ctx.raw._data && ctx.raw._data.is_rex ? base : base + '99';
        },
        labels: {
          display: true,
          formatter: function(ctx) { return ctx.raw._data ? ctx.raw._data.label : ''; },
          color: '#ffffff',
          font: { size: 11 },
        },
      }],
    },
    options: {
      plugins: {
        tooltip: {
          callbacks: {
            title: function(items) { return items[0].raw._data ? items[0].raw._data.label : ''; },
            label: function(item) {
              var d = item.raw._data;
              if (!d) return '';
              return [d.fund_name || '', 'AUM: ' + (d.aum_fmt || ''), 'Issuer: ' + (d.issuer || ''), d.is_rex ? 'REX Product' : ''];
            },
          },
        },
        legend: { display: false },
      },
    },
  });
},

renderShareTimeline: function(canvasId, data) {
  var ctx = document.getElementById(canvasId);
  if (!ctx || !data || !data.series) return;
  var palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2'];
  var datasets = data.series.map(function(s, i) {
    return {
      label: s.short_name || s.name,
      data: s.values,
      borderColor: palette[i % palette.length],
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 2,
      borderWidth: 2,
    };
  });
  new Chart(ctx, {
    type: 'line',
    data: { labels: data.labels, datasets: datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: {
          title: { display: true, text: 'Market Share (%)' },
          ticks: { callback: function(v) { return v + '%'; } },
          min: 0,
        },
      },
      plugins: {
        tooltip: { callbacks: { label: function(c) { return c.dataset.label + ': ' + c.parsed.y + '%'; } } },
        legend: { position: 'bottom' },
      },
    },
  });
},

renderSparkline: function(canvasId, values) {
  var ctx = document.getElementById(canvasId);
  if (!ctx || !values || values.length === 0) return;
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: values.map(function(_, i) { return ''; }),
      datasets: [{ data: values, borderColor: '#1E40AF', backgroundColor: 'rgba(30,64,175,0.1)', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      animation: { duration: 0 },
    },
  });
},
```

### Task 7 — market.css: New Styles

Add to `market.css`:

```css
/* Treemap */
.treemap-container {
  width: 100%;
  height: 500px;
  margin-top: var(--sp-4);
}

/* Underlier deep-dive */
.underlier-split {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--sp-4);
  margin-top: var(--sp-4);
}

.underlier-list {
  max-height: 600px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--sp-2);
}

.underlier-btn {
  display: block;
  width: 100%;
  text-align: left;
  padding: var(--sp-2) var(--sp-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  margin-bottom: var(--sp-1);
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.underlier-btn:hover, .underlier-btn.active {
  background: var(--accent);
  color: white;
}

.underlier-btn .aum-badge {
  float: right;
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.underlier-btn.active .aum-badge { color: rgba(255,255,255,0.8); }

/* Market share timeline */
.share-controls {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
  padding: var(--sp-3);
  background: var(--bg-card);
  border-radius: var(--radius);
  border: 1px solid var(--border);
}

.share-controls label {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--text-sm);
  cursor: pointer;
}

.share-controls input[type="checkbox"] { cursor: pointer; }

/* Sparkline in suite cards */
.sparkline-wrapper {
  margin-top: var(--sp-3);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--border);
  height: 40px;
}

.sparkline-canvas {
  width: 100% !important;
  height: 40px !important;
}

/* Product type filter toggle */
.product-type-filter {
  display: flex;
  gap: var(--sp-2);
  margin-bottom: var(--sp-4);
}

.filter-btn {
  padding: var(--sp-1) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  text-decoration: none;
  font-size: var(--text-sm);
  cursor: pointer;
  transition: all 0.15s;
}

.filter-btn:hover { border-color: var(--accent); color: var(--accent); }
.filter-btn.active { background: var(--accent); color: white; border-color: var(--accent); }

/* Suite filter checkboxes */
.suite-filter {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.suite-filter label {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  cursor: pointer;
}
```

---

**Acceptance Criteria**:
- [ ] `GET /market/treemap` renders a canvas treemap via chartjs-chart-treemap (falls back gracefully if data is unavailable)
- [ ] `GET /market/issuer` shows sortable issuer table + bar chart, category filter works
- [ ] `GET /market/share` shows multi-line chart with one line per category, checkboxes show/hide lines
- [ ] `GET /market/underlier` shows two-panel layout: underlier list (left) + product table (right)
- [ ] REX View has ETF/ETN filter toggle and category show/hide checkboxes
- [ ] Suite cards show 4-month AUM sparkline when sparkline_data is present
- [ ] All new pages have correct `<title>` tags with "REX Financial Intelligence Hub"
- [ ] market.js `MarketCharts` has renderTreemap, renderShareTimeline, renderSparkline helpers

---

## Status: DONE

## Log:
- ab740da: style: add CSS for treemap, underlier, share timeline, sparklines, and filter controls
- 0ef8fcb: feat: add renderTreemap, renderShareTimeline, renderSparkline, and sortTable to market.js
- 56f6769: feat: create treemap, issuer, share_timeline, and underlier templates
- 9ff6e9c: feat: enhance rex.html with ETF/ETN filter, suite checkboxes, sparklines; update _suite_card.html and category.html title
