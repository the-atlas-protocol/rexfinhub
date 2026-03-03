// ETP Filing Tracker - REX Financial

// ---------------------------------------------------------------------------
// Theme management
// ---------------------------------------------------------------------------
(function() {
  'use strict';

  function getPreferredTheme() {
    var stored = localStorage.getItem('rex-theme');
    if (stored) return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('rex-theme', theme);
    // Update meta theme-color for mobile browsers
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0f1923' : '#0f1923');
    // Dispatch event for chart updates
    window.dispatchEvent(new CustomEvent('rex-theme-change', { detail: { theme: theme } }));
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  }

  // Expose for external use
  window.RexTheme = {
    get: getPreferredTheme,
    apply: applyTheme,
    toggle: toggleTheme,
    isDark: function() { return document.documentElement.getAttribute('data-theme') === 'dark'; }
  };

  // Theme toggle button
  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('themeToggle');
    if (btn) btn.addEventListener('click', toggleTheme);
  });

  // Listen for OS theme changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
    if (!localStorage.getItem('rex-theme')) {
      applyTheme(e.matches ? 'dark' : 'light');
    }
  });
})();

// ---------------------------------------------------------------------------
// Hamburger menu
// ---------------------------------------------------------------------------
(function() {
  document.addEventListener('DOMContentLoaded', function() {
    var hamburger = document.getElementById('hamburger');
    var navLinks = document.getElementById('navLinks');
    if (!hamburger || !navLinks) return;

    hamburger.addEventListener('click', function() {
      hamburger.classList.toggle('open');
      navLinks.classList.toggle('open');
    });

    // Close menu when a direct link (not dropdown trigger) is clicked
    navLinks.querySelectorAll('a:not(.nav-dropdown-trigger)').forEach(function(link) {
      link.addEventListener('click', function() {
        hamburger.classList.remove('open');
        navLinks.classList.remove('open');
      });
    });

    // Mobile: tap dropdown trigger to toggle submenu
    navLinks.querySelectorAll('.nav-dropdown-trigger').forEach(function(trigger) {
      trigger.addEventListener('click', function(e) {
        if (window.innerWidth <= 768) {
          e.preventDefault();
          var dd = trigger.closest('.nav-dropdown');
          dd.classList.toggle('nav-dd-open');
        }
      });
    });
  });
})();

// ---------------------------------------------------------------------------
// Kebab (triple-dot) menu
// ---------------------------------------------------------------------------
(function() {
  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('kebabBtn');
    var dropdown = document.getElementById('kebabDropdown');
    var themeBtn = document.getElementById('kebabThemeToggle');
    if (!btn || !dropdown) return;

    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      dropdown.classList.toggle('open');
    });

    // Close on outside click
    document.addEventListener('click', function(e) {
      if (!dropdown.contains(e.target) && e.target !== btn) {
        dropdown.classList.remove('open');
      }
    });

    // Theme toggle from kebab menu
    if (themeBtn && window.RexTheme) {
      themeBtn.addEventListener('click', function(e) {
        e.preventDefault();
        window.RexTheme.toggle();
      });
    }
  });
})();

// ---------------------------------------------------------------------------
// Trust/filing utilities
// ---------------------------------------------------------------------------

// Toggle trust accordion
function toggleTrust(el) {
  el.parentElement.classList.toggle('open');
}

// Toggle download group
function toggleDl(el) {
  el.parentElement.classList.toggle('open');
}

// Jump to trust from dropdown
function jumpToTrust(id) {
  if (!id) return;
  var el = document.getElementById('trust-' + id);
  if (el) {
    el.classList.add('open');
    el.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
}

// Filter table rows
function filterTable(tableId, query, statusFilter) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var rows = table.querySelectorAll('tbody tr');
  var q = (query || '').toLowerCase();
  var shown = 0;
  rows.forEach(function(row) {
    var name = (row.getAttribute('data-name') || '').toLowerCase();
    var ticker = (row.getAttribute('data-ticker') || '').toLowerCase();
    var status = row.getAttribute('data-status') || '';
    var matchText = !q || name.indexOf(q) >= 0 || ticker.indexOf(q) >= 0;
    var matchStatus = !statusFilter || statusFilter === 'ALL' || status === statusFilter;
    if (matchText && matchStatus) {
      row.style.display = '';
      shown++;
    } else {
      row.style.display = 'none';
    }
  });
  var countEl = table.parentElement.querySelector('.filter-count');
  if (countEl) countEl.textContent = shown + ' of ' + rows.length + ' funds';
}

// Status pill click
function setStatusFilter(btn, tableId) {
  var bar = btn.closest('.filter-bar');
  bar.querySelectorAll('.pill').forEach(function(p) { p.classList.remove('active'); });
  btn.classList.add('active');
  var search = bar.querySelector('input');
  filterTable(tableId, search ? search.value : '', btn.getAttribute('data-status'));
}

// Global search across all trust blocks
function globalSearch(query) {
  var q = query.toLowerCase();
  document.querySelectorAll('.trust-block').forEach(function(block) {
    var table = block.querySelector('table');
    if (!table) return;
    var rows = table.querySelectorAll('tbody tr');
    var anyMatch = false;
    rows.forEach(function(row) {
      var name = (row.getAttribute('data-name') || '').toLowerCase();
      var ticker = (row.getAttribute('data-ticker') || '').toLowerCase();
      if (!q || name.indexOf(q) >= 0 || ticker.indexOf(q) >= 0) {
        row.style.display = '';
        anyMatch = true;
      } else {
        row.style.display = 'none';
      }
    });
    if (q && anyMatch) {
      block.classList.add('open');
    }
  });
}

// Back to top visibility
window.addEventListener('scroll', function() {
  var btn = document.getElementById('backTop');
  if (btn) btn.classList.toggle('visible', window.scrollY > 300);
});

// ---------------------------------------------------------------------------
// F1: Trust grid list/grid toggle
// ---------------------------------------------------------------------------
function setTrustView(mode) {
  var grid = document.getElementById('trust-grid');
  if (!grid) return;
  if (mode === 'list') {
    grid.classList.add('list-view');
  } else {
    grid.classList.remove('list-view');
  }
  // Update toggle button states
  document.querySelectorAll('.view-toggle-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-view') === mode);
  });
  try { localStorage.setItem('rex-trust-view', mode); } catch(e) {}
}

document.addEventListener('DOMContentLoaded', function() {
  var saved = null;
  try { saved = localStorage.getItem('rex-trust-view'); } catch(e) {}
  if (saved === 'list' || saved === 'grid') {
    setTrustView(saved);
  }
});

// ---------------------------------------------------------------------------
// F2: Global Search Palette (Ctrl+K)
// ---------------------------------------------------------------------------
(function() {
  var overlay, input, resultsDiv, debounceTimer;

  function openSearch() {
    overlay = overlay || document.getElementById('searchPalette');
    input = input || document.getElementById('searchPaletteInput');
    resultsDiv = resultsDiv || document.getElementById('searchPaletteResults');
    if (!overlay) return;
    overlay.style.display = '';
    if (input) { input.value = ''; input.focus(); }
    if (resultsDiv) resultsDiv.innerHTML = '';
    document.body.style.overflow = 'hidden';
  }

  function closeSearch() {
    overlay = overlay || document.getElementById('searchPalette');
    if (!overlay) return;
    overlay.style.display = 'none';
    document.body.style.overflow = '';
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function renderResults(data) {
    resultsDiv = resultsDiv || document.getElementById('searchPaletteResults');
    if (!resultsDiv) return;

    var html = '';

    if (!data.trusts.length && !data.funds.length && !data.filings.length) {
      html = '<div class="search-empty">No results found</div>';
      resultsDiv.innerHTML = html;
      return;
    }

    if (data.trusts.length) {
      html += '<div class="search-group"><div class="search-group-title">Trusts</div>';
      data.trusts.forEach(function(t) {
        html += '<a href="/trusts/' + escapeHtml(t.slug) + '" class="search-result-item">'
          + '<div class="sri-main">'
          + '<div class="sri-title">' + escapeHtml(t.name) + '</div>'
          + '<div class="sri-sub">CIK: ' + escapeHtml(t.cik) + '</div>'
          + '</div>';
        if (t.entity_type) {
          html += '<span class="sri-badge entity-badge entity-badge--' + escapeHtml(t.entity_type) + '">' + escapeHtml(t.entity_type) + '</span>';
        }
        html += '</a>';
      });
      html += '</div>';
    }

    if (data.funds.length) {
      html += '<div class="search-group"><div class="search-group-title">Funds</div>';
      data.funds.forEach(function(f) {
        var statusClass = f.status === 'EFFECTIVE' ? 'color:var(--green)' : f.status === 'PENDING' ? 'color:var(--orange)' : f.status === 'DELAYED' ? 'color:var(--red)' : '';
        html += '<a href="/trusts/' + escapeHtml(f.trust_slug) + '" class="search-result-item">'
          + '<div class="sri-main">'
          + '<div class="sri-title">' + escapeHtml(f.fund_name) + '</div>'
          + '<div class="sri-sub">' + escapeHtml(f.trust_name) + (f.ticker ? ' &middot; ' + escapeHtml(f.ticker) : '') + '</div>'
          + '</div>'
          + '<span class="sri-badge" style="' + statusClass + '">' + escapeHtml(f.status) + '</span>'
          + '</a>';
      });
      html += '</div>';
    }

    if (data.filings.length) {
      html += '<div class="search-group"><div class="search-group-title">Filings</div>';
      data.filings.forEach(function(fl) {
        html += '<a href="/analysis/filing/' + fl.id + '" class="search-result-item">'
          + '<div class="sri-main">'
          + '<div class="sri-title">' + escapeHtml(fl.form) + ' - ' + escapeHtml(fl.trust_name) + '</div>'
          + '<div class="sri-sub">' + escapeHtml(fl.accession) + (fl.filing_date ? ' &middot; ' + escapeHtml(fl.filing_date) : '') + '</div>'
          + '</div></a>';
      });
      html += '</div>';
    }

    resultsDiv.innerHTML = html;
  }

  function doSearch(q) {
    if (!q || q.length < 2) {
      resultsDiv = resultsDiv || document.getElementById('searchPaletteResults');
      if (resultsDiv) resultsDiv.innerHTML = '';
      return;
    }
    fetch('/api/v1/search?q=' + encodeURIComponent(q) + '&limit=10')
      .then(function(r) { return r.json(); })
      .then(renderResults)
      .catch(function() {});
  }

  // Expose globally
  window.openSearch = openSearch;
  window.closeSearch = closeSearch;

  document.addEventListener('DOMContentLoaded', function() {
    // Nav button
    var navBtn = document.getElementById('navSearchBtn');
    if (navBtn) navBtn.addEventListener('click', openSearch);

    // Input handler with debounce
    var palInput = document.getElementById('searchPaletteInput');
    if (palInput) {
      palInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        var val = palInput.value.trim();
        debounceTimer = setTimeout(function() { doSearch(val); }, 250);
      });
    }

    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
      // Ctrl+K or Cmd+K to open
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        openSearch();
      }
      // ESC to close
      if (e.key === 'Escape') {
        var ov = document.getElementById('searchPalette');
        if (ov && ov.style.display !== 'none') {
          closeSearch();
        }
      }
    });
  });
})();
