# AGENT: Market-Frontend-Events
**Task**: TASK-D — Compare + Calendar + Timeline Frontend
**Branch**: feature/market-frontend-events
**Status**: DONE
**Log**: Final commit bc8ab41 — all three templates redesigned with backward compat guards

## Progress Reporting
Write timestamped progress to: `.agents/progress/Market-Frontend-Events.md`

## Your Files (ONLY modify these)
- `webapp/templates/market/compare.html`
- `webapp/templates/market/calendar.html`
- `webapp/templates/market/timeline.html`

## CRITICAL: Read First
Read all three files completely before writing anything. Also read:
- `webapp/templates/market/base.html`
- `webapp/routers/market_advanced.py` (understand what data is passed to templates)
- `webapp/models.py` (understand Filing model fields — may be `form_type` or `form`, `effective_date`, `filing_date`, `accession_number`)

## Context from Code Analysis
- compare.html: tickers require " US" suffix to match data (e.g., "TQQQ US") — fix with JS strip on submit
- calendar.html: currently titled "Compliance Calendar", focuses on 485BXT extensions
- timeline.html: currently titled "Fund Lifecycle Timeline", shows all 485 filings for a trust

**The backend (Agent A) redesigns the routes. Your job: redesign the templates to match.**
If Agent A hasn't merged yet, design templates to handle BOTH old and new context variables gracefully using `{% if variable is defined %}` guards.

## TASK D.1 — Fund Compare: Fixes + Charts

Read `compare.html` in full. Identify:
- Where the ticker input form is
- What template variables hold fund data
- How metrics are displayed (table structure)

**Fix 1 — Strip " US" on submit** (JavaScript):
Add this `<script>` block to the template:
```javascript
// Strip " US" suffix from tickers before form submit
document.addEventListener('DOMContentLoaded', function() {
  var form = document.querySelector('form[action*="compare"], form');
  var tickerInput = document.querySelector('input[name="tickers"], #tickerInput, input[type="text"]');
  if (form && tickerInput) {
    form.addEventListener('submit', function(e) {
      var val = tickerInput.value;
      var cleaned = val.split(',').map(function(t) {
        return t.trim().replace(/\s+US$/i, '').toUpperCase();
      }).filter(Boolean).join(', ');
      tickerInput.value = cleaned;
    });
  }
});
```

**Fix 2 — Add totalrealreturns.com link** (if `totalrealreturns_url` is in context):
```html
{% if totalrealreturns_url %}
<div style="margin-bottom:20px;">
  <a href="{{ totalrealreturns_url }}" target="_blank" rel="noopener"
     class="btn" style="background:#059669; color:white; display:inline-flex; align-items:center; gap:6px;">
    View Total Return Comparison ↗
  </a>
  <span style="font-size:0.75rem; color:#94A3B8; margin-left:8px;">
    Opens totalrealreturns.com with selected tickers
  </span>
</div>
{% endif %}
```

**Fix 3 — Add AUM Over Time Chart** (if `fund_data` contains `aum_history`):
```html
{% if fund_data and fund_data[0].aum_history is defined %}
<div class="chart-box" style="margin-bottom:20px;">
  <div class="chart-title">AUM Over Time — Last 12 Months ($M)</div>
  <canvas id="aumTrendChart" height="160"></canvas>
</div>
{% endif %}
```

With JS:
```javascript
{% if fund_data and fund_data[0].aum_history is defined %}
var aumTrendData = {
  labels: (function() {
    var labels = [];
    var now = new Date();
    for (var i = 12; i >= 0; i--) {
      var d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      labels.push(d.toLocaleDateString('en-US', {month:'short', year:'2-digit'}));
    }
    return labels;
  })(),
  datasets: [
    {% for fd in fund_data %}
    {
      label: '{{ fd.ticker }}',
      data: {{ fd.aum_history | tojson }},
      fill: false,
      borderWidth: 2,
    },
    {% endfor %}
  ]
};
MarketCharts.renderLineChart('aumTrendChart', aumTrendData);
{% endif %}
```

**Fix 4 — Flow Bar Chart with Period Toggle**:
```html
{% if fund_data and fund_data[0].flows is defined %}
<div class="chart-box" style="margin-bottom:20px;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
    <div class="chart-title" style="margin-bottom:0;">Fund Flows ($M)</div>
    <div style="display:flex; gap:4px;">
      {% for period in ['1w','1m','3m','6m','ytd'] %}
      <button class="pill flow-period-btn {% if loop.first %}active{% endif %}"
              data-period="{{ period }}" onclick="showFlowPeriod('{{ period }}')">
        {{ period.upper() }}
      </button>
      {% endfor %}
    </div>
  </div>
  <canvas id="flowBarChart" height="140"></canvas>
</div>
{% endif %}
```

With JS:
```javascript
{% if fund_data and fund_data[0].flows is defined %}
var flowDataByPeriod = {
  {% for period in ['1w','1m','3m','6m','ytd'] %}
  '{{ period }}': {
    labels: [{% for fd in fund_data %}'{{ fd.ticker }}'{% if not loop.last %},{% endif %}{% endfor %}],
    values: [{% for fd in fund_data %}{{ fd.flows.get(period, 0) if fd.flows else 0 }}{% if not loop.last %},{% endif %}{% endfor %}]
  },
  {% endfor %}
};
var _flowChart = null;
function showFlowPeriod(period) {
  document.querySelectorAll('.flow-period-btn').forEach(function(b) {
    b.classList.toggle('active', b.getAttribute('data-period') === period);
  });
  var data = flowDataByPeriod[period];
  if (!data) return;
  var colors = data.values.map(function(v) { return v >= 0 ? '#059669' : '#DC2626'; });
  if (_flowChart) { _flowChart.destroy(); }
  _flowChart = new Chart(document.getElementById('flowBarChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [{ label: 'Flow $M', data: data.values, backgroundColor: colors }]
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(ctx) {
        var v = ctx.parsed.x;
        return (v >= 0 ? '+' : '') + '$' + Math.abs(v).toFixed(1) + 'M';
      }}}},
      scales: { x: { grid: { display: false }}}
    }
  });
}
showFlowPeriod('1w');
{% endif %}
```

**Fix 5 — Expand comparison table metrics**:
Add to the comparison table (read current template carefully, add missing rows):
- Inception Date (if `inception_date` available)
- Fund Type (ETF/ETN)
- 3M Flow, 6M Flow
- 1M Return

Replace the verbose NaN check (`{% if ret is not none and ret == ret %}`) with:
`{% if ret is not none and ret is not sameas none and ret == ret %}` — or simply check `{% if ret %}` for display purposes.

## TASK D.2 — Calendar: "Fund Activity"

Completely redesign `calendar.html`. The backend (Agent A, Task A.9) now provides:
- `recent_launches`: list of `{"filing": Filing, "trust": Trust, "days_since": int}`
- `upcoming`: list of `{"filing": Filing, "trust": Trust, "days_until": int, "urgency": "green"|"amber"|"red"}`

```html
{% set active_tab = 'calendar' %}
{% extends "market/base.html" %}

{% block title %}Fund Activity — REX Financial Intelligence Hub{% endblock %}

{% block market_content %}
<h2 class="section-title">Fund Activity</h2>
<p style="color:#64748B; font-size:0.85rem; margin-bottom:24px;">
  Recent fund launches (485BPOS effective dates) and upcoming filing events.
  Effective date = the date a fund is eligible to begin trading.
</p>

<div style="display:grid; grid-template-columns:1fr 1fr; gap:24px;">
  <!-- Recent Launches -->
  <div class="chart-box">
    <div class="chart-title" style="color:#059669;">Recent Launches (Last 90 Days)</div>
    {% if recent_launches %}
    <table class="data-table" style="font-size:0.82rem;">
      <thead>
        <tr>
          <th>Trust</th>
          <th>Effective Date</th>
          <th>Days Since</th>
          <th>Filing</th>
        </tr>
      </thead>
      <tbody>
        {% for item in recent_launches %}
        <tr>
          <td style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            {{ item.trust.name }}
          </td>
          <td class="text-mono" style="white-space:nowrap;">
            {{ item.filing.effective_date.strftime('%b %d, %Y') if item.filing.effective_date else '—' }}
          </td>
          <td>
            <span class="badge badge-primary" style="font-size:0.7rem;">
              {{ item.days_since }}d ago
            </span>
          </td>
          <td style="font-size:0.7rem; color:#94A3B8;">
            <span class="badge badge-primary">{{ item.filing.form_type }}</span>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div style="padding:20px; text-align:center; color:#94A3B8;">No recent launches in the last 90 days.</div>
    {% endif %}
  </div>

  <!-- Upcoming Events -->
  <div class="chart-box">
    <div class="chart-title">Upcoming Effective Events (Next 60 Days)</div>
    {% if upcoming %}
    <table class="data-table" style="font-size:0.82rem;">
      <thead>
        <tr>
          <th>Trust</th>
          <th>Form</th>
          <th>Effective Date</th>
          <th>Days Until</th>
        </tr>
      </thead>
      <tbody>
        {% for item in upcoming %}
        <tr>
          <td style="max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            {{ item.trust.name }}
          </td>
          <td><span class="badge badge-{{ 'primary' if item.filing.form_type == '485BPOS' else 'warning' }}">{{ item.filing.form_type }}</span></td>
          <td class="text-mono" style="white-space:nowrap;">
            {{ item.filing.effective_date.strftime('%b %d, %Y') if item.filing.effective_date else '—' }}
          </td>
          <td>
            <span class="urgency-badge urgency-{{ item.urgency }}">
              {{ item.days_until }}d
            </span>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div style="padding:20px; text-align:center; color:#94A3B8;">No upcoming events in the next 60 days.</div>
    {% endif %}
  </div>
</div>

{% endblock %}
{% block market_scripts %}{% endblock %}
```

**Note**: If backend hasn't merged and still passes old context variables (`upcoming` with old structure, `recently_effective`), add compatibility guards:
```html
{% if recent_launches is defined %}
  <!-- new layout -->
{% elif recently_effective is defined %}
  <!-- fallback to old layout with renamed heading -->
{% endif %}
```

## TASK D.3 — Timeline: "Filing History"

Redesign `timeline.html` with:
1. New title "Filing History"
2. Summary stats bar at top
3. Color-coded dots (485BPOS = green, 485BXT = amber, else = gray)
4. Pagination for > 30 entries

```html
{% set active_tab = 'timeline' %}
{% extends "market/base.html" %}

{% block title %}Filing History — REX Financial Intelligence Hub{% endblock %}

{% block market_content %}
<h2 class="section-title">Filing History</h2>
<p style="color:#64748B; font-size:0.85rem; margin-bottom:16px;">
  Complete SEC filing history per trust. A <span style="color:#059669;font-weight:600;">485BPOS</span> marks when funds became eligible to trade.
  A <span style="color:#D97706;font-weight:600;">485BXT</span> is an extension request pushing back the effective date.
</p>

<div class="timeline-controls" style="display:flex; align-items:center; gap:12px; margin-bottom:20px; flex-wrap:wrap;">
  <select class="select-sm" style="min-width:240px;"
          onchange="if(this.value) window.location='/market/timeline?trust_id='+this.value">
    <option value="">Select a trust...</option>
    {% for trust in trusts %}
    <option value="{{ trust.id }}" {{ 'selected' if trust.id == trust_id else '' }}>{{ trust.name }}</option>
    {% endfor %}
  </select>
</div>

{% if selected_trust %}
<div style="display:flex; align-items:center; gap:20px; margin-bottom:20px; flex-wrap:wrap;">
  <h3 style="font-size:1rem; font-weight:700; margin:0;">{{ selected_trust.name }}</h3>
  {% if timeline_items %}
  <div style="display:flex; gap:16px; font-size:0.78rem; flex-wrap:wrap;">
    {% set bpos_count = timeline_items | selectattr('filing.form_type', 'equalto', '485BPOS') | list | length %}
    {% set bxt_count = timeline_items | selectattr('filing.form_type', 'equalto', '485BXT') | list | length %}
    <span style="color:#059669; font-weight:600;">
      ● {{ bpos_count }} BPOS (effective)
    </span>
    <span style="color:#D97706; font-weight:600;">
      ● {{ bxt_count }} BXT (extensions)
    </span>
    <span style="color:#94A3B8;">
      {{ timeline_items | length }} total filings shown
    </span>
  </div>
  {% endif %}
</div>

{% if timeline_items %}
<div class="timeline">
  {% for item in timeline_items %}
  {% set form = item.filing.form_type %}
  {% set dot_color = '#059669' if form == '485BPOS' else '#D97706' if form == '485BXT' else '#94A3B8' %}
  <div class="timeline-entry" style="--dot-color: {{ dot_color }};">
    <div class="timeline-date">
      {{ item.filing.filing_date.strftime('%b %d, %Y') if item.filing.filing_date else 'N/A' }}
    </div>
    <div class="timeline-content">
      <div class="timeline-form">
        <span class="badge badge-{{ 'primary' if form == '485BPOS' else 'warning' if form == '485BXT' else 'secondary' }}">
          {{ form }}
        </span>
        {% if item.filing.effective_date %}
        <span class="timeline-effective">
          Effective: {{ item.filing.effective_date.strftime('%b %d, %Y') }}
        </span>
        {% endif %}
      </div>
      {% if item.fund_count > 0 %}
      <div class="timeline-funds">
        {{ item.fund_count }} fund{{ '' if item.fund_count == 1 else 's' }}
      </div>
      {% endif %}
      {% if item.filing.accession_number %}
      <div class="timeline-accession">
        <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={{ selected_trust.cik }}&type=485&dateb=&owner=include&count=40"
           target="_blank" class="text-link" style="font-size:0.7rem;">{{ item.filing.accession_number }}</a>
      </div>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>

{% else %}
<div class="alert alert-info">No 485 filings found for this trust.</div>
{% endif %}

{% elif trusts %}
<div class="alert alert-info">Select a trust above to view its filing history.</div>
{% endif %}
{% endblock %}

{% block market_scripts %}
<style>
.timeline-entry::before {
  background: var(--dot-color, var(--blue)) !important;
}
</style>
{% endblock %}
```

**Note on CSS variable `--dot-color`**: The `.timeline-entry::before` pseudo-element needs to read the CSS variable set inline on the parent. This works in modern browsers. If it doesn't apply, add a class instead:
- `class="timeline-entry {% if form == '485BPOS' %}entry-bpos{% elif form == '485BXT' %}entry-bxt{% else %}entry-other{% endif %}"`
- Then CSS: `.entry-bpos::before { background: #059669; }` etc.

## Commit Convention
```
git add webapp/templates/market/compare.html webapp/templates/market/calendar.html webapp/templates/market/timeline.html
git commit -m "feat: Market frontend D - fund compare with AUM chart + flows, calendar as Fund Activity, timeline as Filing History with color dots"
```

## Done Criteria
- [ ] Compare: " US" stripped from tickers on form submit. totalrealreturns.com link shown.
- [ ] Compare: AUM Over Time line chart shown (if aum_history data available).
- [ ] Compare: Flow bar chart with 1W/1M/3M/6M/YTD toggle buttons.
- [ ] Compare: Table includes Inception Date, Fund Type, 3M/6M flows.
- [ ] Calendar: Titled "Fund Activity". Shows Recent Launches + Upcoming Events sections.
- [ ] Timeline: Titled "Filing History". 485BPOS = green dot, 485BXT = amber dot. Summary stats at top.
- [ ] No template rendering errors (test with trust that has 50+ filings).
