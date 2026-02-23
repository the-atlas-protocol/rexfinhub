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

    // Close menu when a link is clicked
    navLinks.querySelectorAll('a').forEach(function(link) {
      link.addEventListener('click', function() {
        hamburger.classList.remove('open');
        navLinks.classList.remove('open');
      });
    });
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
