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
  var GRAY_COLOR = '#D1D5DB';

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

    // Shorten labels for display
    var shortLabels = labels.map(function(l) {
      return l.replace('Leverage & Inverse - ', 'L&I ')
              .replace('Income - ', 'Inc ')
              .replace('Index/Basket/ETF Based', 'Index/ETF')
              .replace('Single Stock', 'Single');
    });

    return new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: shortLabels,
        datasets: [{
          data: values,
          backgroundColor: SUITE_COLORS.slice(0, values.length),
          borderWidth: 2,
          borderColor: '#fff'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: {
            position: 'right',
            labels: {
              font: { size: 11, family: "'Inter', sans-serif" },
              padding: 12,
              usePointStyle: true,
              pointStyleWidth: 10
            }
          },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                var pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : '0';
                return ctx.label + ': ' + fmtMoney(ctx.parsed) + ' (' + pct + '%)';
              }
            }
          }
        }
      }
    });
  }

  function renderLineChart(canvasId, labels, datasets) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    // Show fewer labels if many data points
    var skipLabels = labels.length > 24 ? 3 : (labels.length > 12 ? 2 : 1);

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

    return new Chart(ctx, {
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
              font: { size: 10 }
            },
            grid: { display: false }
          },
          y: {
            ticks: {
              callback: function(val) { return fmtMoney(val); },
              font: { size: 10 }
            },
            grid: { color: '#f3f4f6' }
          }
        },
        plugins: {
          legend: {
            display: datasets.length > 1,
            labels: { font: { size: 11, family: "'Inter', sans-serif" }, usePointStyle: true }
          },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                return ctx.dataset.label + ': ' + fmtMoney(ctx.parsed.y);
              }
            }
          }
        }
      }
    });
  }

  function renderBarChart(canvasId, labels, values, isRex) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    var colors = labels.map(function(_, i) {
      return isRex[i] ? REX_COLOR : GRAY_COLOR;
    });

    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderWidth: 0,
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
              font: { size: 10 }
            },
            grid: { color: '#f3f4f6' }
          },
          y: {
            ticks: {
              font: { size: 10, family: "'Inter', sans-serif" }
            },
            grid: { display: false }
          }
        },
        plugins: {
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
  }

  function renderTreemap(canvasId, products) {
    var ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    var groupColors = {};
    var palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2','#65A30D'];
    var groups = [];
    products.forEach(function(p) {
      if (groups.indexOf(p.group) === -1) groups.push(p.group);
    });
    groups.forEach(function(g, i) { groupColors[g] = palette[i % palette.length]; });

    return new Chart(ctx, {
      type: 'treemap',
      data: {
        datasets: [{
          label: 'AUM by Product',
          tree: products,
          key: 'value',
          groups: ['group'],
          backgroundColor: function(c) {
            if (!c.raw || !c.raw.g) return '#6B7280';
            var base = groupColors[c.raw.g] || '#6B7280';
            return c.raw._data && c.raw._data.is_rex ? base : base + '99';
          },
          labels: {
            display: true,
            formatter: function(c) { return c.raw._data ? c.raw._data.label : ''; },
            color: '#ffffff',
            font: { size: 11 }
          }
        }]
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
              }
            }
          },
          legend: { display: false }
        }
      }
    });
  }

  function renderShareTimeline(canvasId, data) {
    var ctx = document.getElementById(canvasId);
    if (!ctx || !data || !data.series) return null;
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
    return new Chart(ctx, {
      type: 'line',
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: {
            title: { display: true, text: 'Market Share (%)' },
            ticks: { callback: function(v) { return v + '%'; } },
            min: 0
          }
        },
        plugins: {
          tooltip: { callbacks: { label: function(c) { return c.dataset.label + ': ' + c.parsed.y + '%'; } } },
          legend: { position: 'bottom' }
        }
      }
    });
  }

  function renderSparkline(canvasId, values) {
    var ctx = document.getElementById(canvasId);
    if (!ctx || !values || values.length === 0) return null;
    return new Chart(ctx, {
      type: 'line',
      data: {
        labels: values.map(function() { return ''; }),
        datasets: [{ data: values, borderColor: '#1E40AF', backgroundColor: 'rgba(30,64,175,0.1)', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5 }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { display: false }, y: { display: false } },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        animation: { duration: 0 }
      }
    });
  }

  return {
    renderPieChart: renderPieChart,
    renderLineChart: renderLineChart,
    renderBarChart: renderBarChart,
    renderTreemap: renderTreemap,
    renderShareTimeline: renderShareTimeline,
    renderSparkline: renderSparkline
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
    var catSelect = document.getElementById('categorySelect');
    var category = catSelect ? catSelect.value : 'All';
    var filters = getActiveFilters();
    var filterStr = Object.keys(filters).length > 0 ? JSON.stringify(filters) : '';

    var url = '/market/api/category-summary?category=' + encodeURIComponent(category);
    if (filterStr) {
      url += '&filters=' + encodeURIComponent(filterStr);
    }

    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.error) return;
        updateCategoryView(data);
      })
      .catch(function(err) {
        console.error('Filter update failed:', err);
      });
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
        return data.issuer_data.is_rex[i] ? '#1E40AF' : '#D1D5DB';
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
    filterProductTable: filterProductTable
  };
})();


// ---------------------------------------------------------------------------
// Table sorting utility
// ---------------------------------------------------------------------------
function sortTable(tableId, colIdx) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var asc = table.getAttribute('data-sort-col') == colIdx && table.getAttribute('data-sort-dir') !== 'asc';
  table.setAttribute('data-sort-col', colIdx);
  table.setAttribute('data-sort-dir', asc ? 'asc' : 'desc');

  rows.sort(function(a, b) {
    var aText = (a.cells[colIdx] ? a.cells[colIdx].textContent.trim() : '');
    var bText = (b.cells[colIdx] ? b.cells[colIdx].textContent.trim() : '');
    // Try numeric compare (strip $, %, +, commas)
    var aNum = parseFloat(aText.replace(/[$%+,BMK]/g, ''));
    var bNum = parseFloat(bText.replace(/[$%+,BMK]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return asc ? aNum - bNum : bNum - aNum;
    }
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  });

  rows.forEach(function(row) { tbody.appendChild(row); });
}
