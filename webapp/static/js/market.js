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

  return {
    renderPieChart: renderPieChart,
    renderLineChart: renderLineChart,
    renderBarChart: renderBarChart
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
