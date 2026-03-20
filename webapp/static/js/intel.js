/* ========================================================================
   Intel Hub — 13F Intelligence Helpers
   ======================================================================== */

// Format quarter string "2025-12-31" -> "Q4 2025"
function fmtQuarter(q) {
  if (!q) return '\u2014';
  var parts = q.split('-');
  var month = parseInt(parts[1], 10);
  var year = parts[0];
  var qNum = Math.ceil(month / 3);
  return 'Q' + qNum + ' ' + year;
}

// Format value as $1.2T, $3.4B, etc.
function fmtValue(n) {
  if (n == null) return '\u2014';
  n = Math.abs(Number(n));
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(1) + 'T';
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3)  return '$' + (n / 1e3).toFixed(0) + 'K';
  return '$' + n.toFixed(0);
}

// Format number with commas
function fmtNum(n) {
  if (n == null) return '\u2014';
  return Number(n).toLocaleString();
}

// Format percent "+5.2%" or "-3.1%"
function fmtPct(n) {
  if (n == null) return '\u2014';
  var v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}

// Get category color for Chart.js
function getCategoryColor(cat) {
  var map = {
    'Leverage & Inverse - Single Stock': '#e74c3c',
    'Leverage & Inverse - Index/Basket/ETF Based': '#c0392b',
    'Crypto': '#8e44ad',
    'Income - Single Stock': '#27ae60',
    'Income - Index/Basket/ETF Based': '#2ecc71',
    'Defined Outcome': '#f39c12',
    'Thematic': '#3498db'
  };
  return map[cat] || '#94a3b8';
}

// Chart.js theme-aware defaults
function getChartColors() {
  var isDark = window.RexTheme && window.RexTheme.isDark();
  return {
    grid: isDark ? 'rgba(255,255,255,0.06)' : '#e2e8f0',
    text: isDark ? '#94a3b8' : '#64748b',
    bg:   isDark ? '#1e293b' : '#ffffff'
  };
}

// Country code -> name
var COUNTRY_MAP = {
  F4: 'Netherlands', F5: 'United Kingdom', K3: 'Hong Kong', K7: 'Singapore',
  K8: 'South Korea', M0: 'China', M5: 'Japan', U0: 'Taiwan', W1: 'India',
  V7: 'South Africa', X2: 'Brazil', V3: 'Chile', L4: 'Israel', N6: 'Mexico',
  C3: 'Australia', J5: 'Norway', Y5: 'Cayman Islands'
};

// Country code -> flag emoji
var COUNTRY_FLAGS = {
  F4: '\u{1F1F3}\u{1F1F1}', F5: '\u{1F1EC}\u{1F1E7}', K3: '\u{1F1ED}\u{1F1F0}',
  K7: '\u{1F1F8}\u{1F1EC}', K8: '\u{1F1F0}\u{1F1F7}', M0: '\u{1F1E8}\u{1F1F3}',
  M5: '\u{1F1EF}\u{1F1F5}', U0: '\u{1F1F9}\u{1F1FC}', W1: '\u{1F1EE}\u{1F1F3}'
};

// US state names
var STATE_NAMES = {
  AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',
  CO:'Colorado',CT:'Connecticut',DE:'Delaware',FL:'Florida',GA:'Georgia',
  HI:'Hawaii',ID:'Idaho',IL:'Illinois',IN:'Indiana',IA:'Iowa',KS:'Kansas',
  KY:'Kentucky',LA:'Louisiana',ME:'Maine',MD:'Maryland',MA:'Massachusetts',
  MI:'Michigan',MN:'Minnesota',MS:'Mississippi',MO:'Missouri',MT:'Montana',
  NE:'Nebraska',NV:'Nevada',NH:'New Hampshire',NJ:'New Jersey',NM:'New Mexico',
  NY:'New York',NC:'North Carolina',ND:'North Dakota',OH:'Ohio',OK:'Oklahoma',
  OR:'Oregon',PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',
  SD:'South Dakota',TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',
  VA:'Virginia',WA:'Washington',WV:'West Virginia',WI:'Wisconsin',WY:'Wyoming',
  DC:'Washington D.C.'
};

// Initialize Chart.js with theme-aware defaults
function initIntelChart(canvasId, config) {
  var colors = getChartColors();
  if (config.options && config.options.scales) {
    var keys = Object.keys(config.options.scales);
    for (var i = 0; i < keys.length; i++) {
      var axis = config.options.scales[keys[i]];
      if (!axis.grid) axis.grid = {};
      axis.grid.color = colors.grid;
      if (!axis.ticks) axis.ticks = {};
      axis.ticks.color = colors.text;
    }
  }
  var ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  return new Chart(ctx, config);
}

// Listen for theme changes and rebuild charts
document.addEventListener('rex-theme-change', function() {
  if (window._intelCharts) {
    for (var i = 0; i < window._intelCharts.length; i++) {
      window._intelCharts[i].destroy();
    }
    window._intelCharts = [];
    if (window._initIntelCharts) window._initIntelCharts();
  }
});
window._intelCharts = [];
