/**
 * Market Intelligence - Charts and Filters
 * Uses Chart.js for pie, bar, and line charts.
 * Handles dynamic slicer loading and AJAX updates.
 */

// ---------------------------------------------------------------------------
// Chart rendering
// ---------------------------------------------------------------------------
var MarketCharts = (function() {
  // Suite colors
  var SUITE_COLORS = [
    '#1E40AF', '#3B82F6', '#0EA5E9', '#6366F1', '#8B5CF6', '#A855F7',
    '#EC4899', '#F43F5E', '#F97316', '#EAB308', '#22C55E', '#14B8A6'
  ];

  var REX_COLOR = '#1E40AF';
  var DEFAULT_BAR_COLOR = '#93C5FD';

  // Track chart instances for theme updates
  var _charts = {};

  function getCSSVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function getChartThemeColors() {
    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    return {
      gridColor: isDark ? 'rgba(255,255,255,0.08)' : '#f3f4f6',
      labelColor: isDark ? '#94a3b8' : '#6b7280',
      tooltipBg: isDark ? '#1e293b' : '#fff',
      tooltipText: isDark ? '#e2e8f0' : '#1e293b',
      borderColor: isDark ? 'rgba(255,255,255,0.15)' : '#fff',
      pieBorder: isDark ? '#1e293b' : '#fff'
    };
  }

  function fmtMoney(val) {
    if (val === null || val === undefined || isNaN(val)) return '$0';
    var abs = Math.abs(val);
    if (abs >= 1000) return '$' + (val / 1000).toFixed(1) + 'B';
    if (abs >= 1) return '$' + val.toFixed(1) + 'M';
    return '$' + (val * 1000).toFixed(0) + 'K';
  }

  function renderPieChart(canvasId, labels, values) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    var theme = getChartThemeColors();

    // Shorten labels for display
    var shortLabels = labels.map(function(l) {
      return l.replace('Leverage & Inverse - ', 'L&I ')
              .replace('Income - ', 'Inc ')
              .replace('Index/Basket/ETF Based', 'Index/ETF')
              .replace('Single Stock', 'Single');
    });

    var chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: shortLabels,
        datasets: [{
          data: values,
          backgroundColor: SUITE_COLORS.slice(0, values.length),
          borderWidth: 2,
          borderColor: theme.pieBorder
        }]
      },
      plugins: [ChartDataLabels],
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          datalabels: {
            color: '#fff',
            font: { weight: 'bold', size: 11 },
            formatter: function(value, ctx) {
              var total = ctx.dataset.data.reduce(function(a,b){return a+b;},0);
              var pct = total > 0 ? (value/total*100).toFixed(1) : 0;
              var label = ctx.chart.data.labels[ctx.dataIndex];
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
          legend: {
            position: 'bottom',
            labels: {
              boxWidth: 12,
              font: { size: 11 },
              color: theme.labelColor
            }
          },
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
    });
    _charts[canvasId] = chart;
    return chart;
  }

  function renderLineChart(canvasId, labels, datasets, opts) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    var theme = getChartThemeColors();
    var yFormat = (opts && opts.yFormat) || 'money'; // 'money' or 'pct'

    var fmtY = yFormat === 'pct'
      ? function(val) { return val.toFixed(1) + '%'; }
      : fmtMoney;

    var chartDatasets = datasets.map(function(ds) {
      return {
        label: ds.label,
        data: ds.data,
        borderColor: ds.color,
        backgroundColor: ds.color + '20',
        fill: datasets.length === 1,
        tension: 0.3,
        pointRadius: 2,
        pointHoverRadius: 5,
        borderWidth: 2
      };
    });

    var chart = new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: chartDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            ticks: {
              maxRotation: 45,
              autoSkip: true,
              maxTicksLimit: 12,
              font: { size: 10 },
              color: theme.labelColor
            },
            grid: { display: false }
          },
          y: {
            ticks: {
              callback: function(val) { return fmtY(val); },
              font: { size: 10 },
              color: theme.labelColor
            },
            grid: { color: theme.gridColor }
          }
        },
        plugins: {
          datalabels: { display: false },
          legend: {
            display: datasets.length > 1,
            labels: {
              font: { size: 11, family: "'Inter', sans-serif" },
              usePointStyle: true,
              color: theme.labelColor
            }
          },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                return ctx.dataset.label + ': ' + fmtY(ctx.parsed.y);
              }
            }
          }
        }
      }
    });
    _charts[canvasId] = chart;
    return chart;
  }

  function renderBarChart(canvasId, labels, values, isRex) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    var theme = getChartThemeColors();

    var colors = labels.map(function(_, i) {
      return isRex[i] ? REX_COLOR : DEFAULT_BAR_COLOR;
    });
    var borderWidths = labels.map(function(_, i) {
      return isRex[i] ? 2 : 0;
    });
    var borderColors = labels.map(function(_, i) {
      return isRex[i] ? '#1E3A8A' : 'transparent';
    });

    var chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderWidth: borderWidths,
          borderColor: borderColors,
          borderRadius: 3
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: true,
        scales: {
          x: {
            ticks: {
              callback: function(val) { return fmtMoney(val); },
              font: { size: 10 },
              color: theme.labelColor
            },
            grid: { color: theme.gridColor }
          },
          y: {
            ticks: {
              font: { size: 10, family: "'Inter', sans-serif" },
              color: theme.labelColor
            },
            grid: { display: false }
          }
        },
        plugins: {
          datalabels: { display: false },
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                return fmtMoney(ctx.parsed.x);
              }
            }
          }
        }
      }
    });
    _charts[canvasId] = chart;
    return chart;
  }

  function renderTreemap(canvasId, products) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    // Build color map by issuer (sorted by total AUM for consistent ordering)
    var issuerAum = {};
    products.forEach(function(p) {
      issuerAum[p.group] = (issuerAum[p.group] || 0) + p.value;
    });
    var sortedIssuers = Object.keys(issuerAum).sort(function(a, b) {
      return issuerAum[b] - issuerAum[a];
    });
    var palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2','#65A30D','#6366F1','#F43F5E','#0D9488','#A855F7'];
    var groupColors = {};
    sortedIssuers.forEach(function(g, i) { groupColors[g] = palette[i % palette.length]; });

    var chart = new Chart(ctx, {
      type: 'treemap',
      data: {
        datasets: [{
          label: 'AUM by Product',
          tree: products,
          key: 'value',
          groups: ['group', 'label'],
          borderColor: 'rgba(255,255,255,0.7)',
          borderWidth: 1,
          spacing: 0.5,
          backgroundColor: function(c) {
            if (!c.raw || !c.raw.g) return '#6B7280';
            var base = groupColors[c.raw.g] || '#6B7280';
            // Leaf nodes (individual products)
            if (c.raw._data) {
              return c.raw._data.is_rex ? base : base + 'DD';
            }
            // Group-level node (issuer header)
            return base;
          },
          captions: {
            display: true,
            color: '#ffffff',
            font: { size: 13, weight: 'bold' },
            align: 'left',
            padding: 4,
            formatter: function(c) {
              return c.raw && c.raw.g ? c.raw.g : '';
            }
          },
          labels: {
            display: true,
            formatter: function(c) {
              if (!c.raw || !c.raw._data) return '';
              var ticker = c.raw._data.label || '';
              var aum = c.raw._data.aum_fmt || '';
              return ticker && aum ? [ticker, aum] : ticker;
            },
            color: '#ffffff',
            font: { size: 10 },
            overflow: 'hidden',
            padding: 3
          }
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          datalabels: { display: false },
          tooltip: {
            callbacks: {
              title: function(items) {
                var raw = items[0].raw;
                if (raw._data) return raw._data.fund_name || raw._data.label || '';
                return raw.g || '';
              },
              label: function(item) {
                var raw = item.raw;
                if (raw._data) {
                  var lines = [];
                  if (raw._data.ticker) lines.push('Ticker: ' + raw._data.ticker);
                  if (raw._data.aum_fmt) lines.push('AUM: ' + raw._data.aum_fmt);
                  if (raw._data.issuer) lines.push('Issuer: ' + raw._data.issuer);
                  if (raw._data.is_rex) lines.push('REX Product');
                  return lines;
                }
                // Group-level: show issuer name and total
                var total = item.raw.v || 0;
                return 'Total AUM: $' + (total / 1e3).toFixed(1) + 'B';
              }
            }
          },
          legend: { display: false }
        }
      }
    });
    _charts[canvasId] = chart;
    return chart;
  }

  function renderShareTimeline(canvasId, data) {
    var ctx = document.getElementById(canvasId);
    if (!ctx || !data || !data.series) return null;
    var theme = getChartThemeColors();
    var palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2'];
    var datasets = data.series.map(function(s, i) {
      return {
        label: s.short_name || s.name,
        data: s.values,
        borderColor: palette[i % palette.length],
        backgroundColor: 'transparent',
        tension: 0.3,
        pointRadius: 2,
        borderWidth: 2
      };
    });
    var chart = new Chart(ctx, {
      type: 'line',
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            ticks: { color: theme.labelColor },
            grid: { display: false }
          },
          y: {
            title: { display: true, text: 'Market Share (%)', color: theme.labelColor },
            ticks: {
              callback: function(v) { return v + '%'; },
              color: theme.labelColor
            },
            grid: { color: theme.gridColor },
            min: 0
          }
        },
        plugins: {
          datalabels: { display: false },
          tooltip: { callbacks: { label: function(c) { return c.dataset.label + ': ' + c.parsed.y + '%'; } } },
          legend: {
            position: 'bottom',
            labels: { color: theme.labelColor }
          }
        }
      }
    });
    _charts[canvasId] = chart;
    return chart;
  }

  function renderSparkline(canvasId, values) {
    var ctx = document.getElementById(canvasId);
    if (!ctx || !values || values.length === 0) return null;
    var chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: values.map(function() { return ''; }),
        datasets: [{ data: values, borderColor: '#1E40AF', backgroundColor: 'rgba(30,64,175,0.1)', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5 }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { display: false }, y: { display: false } },
        plugins: { legend: { display: false }, tooltip: { enabled: false }, datalabels: { display: false } },
        animation: { duration: 0 }
      }
    });
    _charts[canvasId] = chart;
    return chart;
  }

  // Update all tracked charts when theme changes
  function updateChartsForTheme() {
    var theme = getChartThemeColors();
    Object.keys(_charts).forEach(function(id) {
      var chart = _charts[id];
      if (!chart || !chart.options) return;
      var opts = chart.options;

      // Update scale colors
      if (opts.scales) {
        ['x', 'y'].forEach(function(axis) {
          if (opts.scales[axis]) {
            if (opts.scales[axis].ticks) opts.scales[axis].ticks.color = theme.labelColor;
            if (opts.scales[axis].grid) opts.scales[axis].grid.color = theme.gridColor;
            if (opts.scales[axis].title) opts.scales[axis].title.color = theme.labelColor;
          }
        });
      }

      // Update legend label colors
      if (opts.plugins && opts.plugins.legend && opts.plugins.legend.labels) {
        opts.plugins.legend.labels.color = theme.labelColor;
      }

      // Update pie border colors
      if (chart.config.type === 'doughnut' || chart.config.type === 'pie') {
        chart.data.datasets.forEach(function(ds) { ds.borderColor = theme.pieBorder; });
      }

      chart.update('none');
    });
  }

  // Listen for theme changes
  window.addEventListener('rex-theme-change', updateChartsForTheme);

  return {
    renderPieChart: renderPieChart,
    renderLineChart: renderLineChart,
    renderBarChart: renderBarChart,
    renderTreemap: renderTreemap,
    renderShareTimeline: renderShareTimeline,
    renderSparkline: renderSparkline,
    updateChartsForTheme: updateChartsForTheme
  };
})();


// ---------------------------------------------------------------------------
// Category filters and AJAX updates
// ---------------------------------------------------------------------------
var MarketFilters = (function() {

  function onCategoryChange(category) {
    // Navigate to category view with selected category
    window.location.href = '/market/category?cat=' + encodeURIComponent(category);
  }

  function getActiveFilters() {
    var filters = {};
    var slicerPanel = document.getElementById('slicerPanel');
    if (!slicerPanel) return filters;

    var groups = slicerPanel.querySelectorAll('.slicer-group');
    groups.forEach(function(group) {
      var field = group.getAttribute('data-field');
      var select = group.querySelector('select');
      if (!select || !field) return;

      if (select.multiple) {
        var selected = Array.from(select.selectedOptions).map(function(o) { return o.value; });
        if (selected.length > 0) {
          filters[field] = selected;
        }
      } else {
        if (select.value) {
          filters[field] = select.value;
        }
      }
    });
    return filters;
  }

  function applyFilters() {
    // Read category from page data attribute
    var pageEl = document.getElementById('market-category-page');
    var cat = pageEl ? pageEl.getAttribute('data-category') : '';

    // Read slicer values
    var params = new URLSearchParams();
    if (cat) params.set('cat', encodeURIComponent(cat));
    document.querySelectorAll('.slicer-select').forEach(function(sel) {
      if (sel.value) {
        params.set(sel.getAttribute('data-field'), sel.value);
      }
    });

    // Preserve fund_structure param
    var fundStr = new URLSearchParams(location.search).get('fund_structure') || 'ETF';
    params.set('fund_structure', fundStr);
    location.search = params.toString();
  }

  function toggleCategory(cat) {
    var params = new URLSearchParams(location.search);
    params.set('cat', cat);
    // Preserve fund_structure
    var fundStr = params.get('fund_structure') || 'ETF';
    params.set('fund_structure', fundStr);
    location.search = params.toString();
  }

  function updateCategoryView(data) {
    // Update category KPIs
    _setText('cat-kpi-aum', data.cat_kpis.aum_fmt);
    _setText('cat-kpi-flow1w', data.cat_kpis.flow_1w_fmt);
    _setFlow('cat-kpi-flow1w', data.cat_kpis.flow_1w);
    _setText('cat-kpi-flow1m', data.cat_kpis.flow_1m_fmt);
    _setFlow('cat-kpi-flow1m', data.cat_kpis.flow_1m);
    _setText('cat-kpi-flow3m', data.cat_kpis.flow_3m_fmt);
    _setFlow('cat-kpi-flow3m', data.cat_kpis.flow_3m);
    _setText('cat-kpi-count', data.cat_kpis.num_products);

    // Update REX KPIs
    _setText('cat-rex-aum', data.rex_kpis.aum_fmt);
    _setText('cat-rex-share', data.rex_share + '%');
    _setText('cat-rex-count', data.rex_kpis.num_products);
    _setText('cat-rex-flow1w', data.rex_kpis.flow_1w_fmt);
    _setFlow('cat-rex-flow1w', data.rex_kpis.flow_1w);

    // Update issuer bar chart
    if (data.issuer_data && window._issuerChart) {
      window._issuerChart.data.labels = data.issuer_data.labels;
      window._issuerChart.data.datasets[0].data = data.issuer_data.values;
      window._issuerChart.data.datasets[0].backgroundColor = data.issuer_data.labels.map(function(_, i) {
        return data.issuer_data.is_rex[i] ? '#1E40AF' : '#93C5FD';
      });
      window._issuerChart.update();
    }

    // Update product table
    _updateProductTable('topProducts', data.top_products, true);
    _updateProductTable('rexProducts', data.rex_products, false);
  }

  function _setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function _setFlow(id, val) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('positive', 'negative');
    if (val > 0) el.classList.add('positive');
    else if (val < 0) el.classList.add('negative');
  }

  function _updateProductTable(tableId, products, showIssuer) {
    var table = document.getElementById(tableId);
    if (!table || !products) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;

    var html = '';
    products.forEach(function(p) {
      var rowClass = p.is_rex ? ' class="rex-highlight"' : '';
      var flowClass1w = p.flow_1w > 0 ? ' positive' : (p.flow_1w < 0 ? ' negative' : '');
      var flowClass1m = p.flow_1m > 0 ? ' positive' : (p.flow_1m < 0 ? ' negative' : '');
      var yieldVal = (p.yield_val !== null && p.yield_val !== undefined && !isNaN(p.yield_val))
        ? p.yield_val.toFixed(1) + '%' : '-';

      html += '<tr' + rowClass + '>';
      html += '<td>' + (p.rank || '-') + '</td>';
      html += '<td class="ticker-cell">' + _esc(p.ticker) + '</td>';
      html += '<td class="name-cell" title="' + _esc(p.fund_name) + '">' + _esc(_trunc(p.fund_name, 45)) + '</td>';
      if (showIssuer) html += '<td>' + _esc(p.issuer || '') + '</td>';
      html += '<td class="num-cell">' + _esc(p.aum_fmt) + '</td>';
      html += '<td class="num-cell' + flowClass1w + '">' + _esc(p.flow_1w_fmt) + '</td>';
      html += '<td class="num-cell' + flowClass1m + '">' + _esc(p.flow_1m_fmt) + '</td>';
      html += '<td class="num-cell">' + yieldVal + '</td>';
      html += '</tr>';
    });

    tbody.innerHTML = html;
  }

  function _esc(str) {
    if (!str) return '';
    var d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function _trunc(str, len) {
    if (!str) return '';
    return str.length > len ? str.substring(0, len - 3) + '...' : str;
  }

  function filterProductTable(query) {
    var table = document.getElementById('topProducts');
    if (!table) return;
    var rows = table.querySelectorAll('tbody tr');
    var q = (query || '').toLowerCase();
    var shown = 0;

    rows.forEach(function(row) {
      var ticker = row.querySelector('.ticker-cell');
      var name = row.querySelector('.name-cell');
      var tickerText = ticker ? ticker.textContent.toLowerCase() : '';
      var nameText = name ? name.textContent.toLowerCase() : '';

      if (!q || tickerText.indexOf(q) >= 0 || nameText.indexOf(q) >= 0) {
        row.style.display = '';
        shown++;
      } else {
        row.style.display = 'none';
      }
    });

    var countEl = document.getElementById('productCount');
    if (countEl) countEl.textContent = shown + ' of ' + rows.length + ' products';
  }

  return {
    onCategoryChange: onCategoryChange,
    applyFilters: applyFilters,
    toggleCategory: toggleCategory,
    filterProductTable: filterProductTable
  };
})();
