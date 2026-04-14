/**
 * REX LIVE FEED — Real-time filing notifications.
 *
 * Polls /api/v1/live/recent every 30 seconds and toasts any new filings.
 * Also maintains a bell icon with unread count in the header.
 *
 * State is kept in localStorage so the "unread" count persists across page
 * navigation. The "latest seen" timestamp is passed as ?since= so we only
 * fetch new items on each poll.
 *
 * Toast stack is bottom-right, max 4 visible at once, auto-dismiss 15s.
 * Click a toast to open the filing's SEC page. Click the bell to see the
 * full recent feed in a side panel.
 */
(function() {
  'use strict';

  var POLL_MS = 30000;           // 30s
  var MAX_TOASTS = 4;
  var TOAST_DURATION = 15000;    // 15s
  var STORAGE_KEY = 'rex_live_feed_latest';
  var SEEN_KEY = 'rex_live_feed_seen';

  function getSeenSet() {
    try {
      var raw = localStorage.getItem(SEEN_KEY);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveSeenSet(set) {
    try {
      var arr = Array.from(set).slice(-200); // cap
      localStorage.setItem(SEEN_KEY, JSON.stringify(arr));
    } catch (e) {}
  }

  function getLatest() {
    return localStorage.getItem(STORAGE_KEY) || '';
  }

  function setLatest(iso) {
    if (iso) localStorage.setItem(STORAGE_KEY, iso);
  }

  function formBadgeClass(form) {
    var f = (form || '').toUpperCase();
    if (f.indexOf('BXT') >= 0) return 'live-form-extension';
    if (f.indexOf('485B') === 0) return 'live-form-effective';
    if (f.indexOf('485A') === 0) return 'live-form-initial';
    if (f.indexOf('497') === 0) return 'live-form-supplement';
    if (f.indexOf('N-1A') === 0) return 'live-form-n1a';
    if (f.indexOf('N-2') === 0) return 'live-form-n2';
    return 'live-form-other';
  }

  function formatTime(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      var now = new Date();
      var diff = Math.floor((now - d) / 1000); // seconds
      if (diff < 60) return diff + 's ago';
      if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
      if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
      return Math.floor(diff / 86400) + 'd ago';
    } catch (e) {
      return '';
    }
  }

  function ensureContainers() {
    if (document.getElementById('rex-live-toasts')) return;

    // Toast stack (bottom right)
    var toasts = document.createElement('div');
    toasts.id = 'rex-live-toasts';
    toasts.className = 'rex-live-toasts';
    document.body.appendChild(toasts);

    // Bell icon in the nav (if nav exists)
    var nav = document.querySelector('.nav-right, .nav-links, nav');
    if (nav) {
      var bell = document.createElement('a');
      bell.id = 'rex-live-bell';
      bell.className = 'rex-live-bell';
      bell.href = '#';
      bell.title = 'Live filings feed';
      bell.innerHTML = '<span class="rex-live-bell-icon">\u2691</span><span class="rex-live-bell-count" style="display:none">0</span>';
      bell.addEventListener('click', function(e) {
        e.preventDefault();
        togglePanel();
      });
      nav.appendChild(bell);
    }

    // Side panel (hidden by default)
    var panel = document.createElement('div');
    panel.id = 'rex-live-panel';
    panel.className = 'rex-live-panel';
    panel.innerHTML =
      '<div class="rex-live-panel-header">' +
        '<strong>Recent filings</strong>' +
        '<button class="rex-live-panel-close" aria-label="Close">\u00d7</button>' +
      '</div>' +
      '<div class="rex-live-panel-body" id="rex-live-panel-body">' +
        '<div class="rex-live-empty">Loading\u2026</div>' +
      '</div>';
    document.body.appendChild(panel);
    panel.querySelector('.rex-live-panel-close').addEventListener('click', function() {
      panel.classList.remove('open');
    });
  }

  function togglePanel() {
    var panel = document.getElementById('rex-live-panel');
    if (!panel) return;
    if (panel.classList.contains('open')) {
      panel.classList.remove('open');
    } else {
      panel.classList.add('open');
      loadPanelContent();
      // Mark all visible as seen, zero the count
      var bell = document.getElementById('rex-live-bell');
      var count = bell ? bell.querySelector('.rex-live-bell-count') : null;
      if (count) {
        count.textContent = '0';
        count.style.display = 'none';
      }
    }
  }

  function loadPanelContent() {
    var body = document.getElementById('rex-live-panel-body');
    if (!body) return;
    fetch('/api/v1/live/recent?limit=50')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data.items || data.items.length === 0) {
          body.innerHTML = '<div class="rex-live-empty">No filings yet. The watcher checks SEC every 60 seconds.</div>';
          return;
        }
        var html = '';
        data.items.forEach(function(item) {
          html += renderFeedItem(item);
        });
        body.innerHTML = html;
      })
      .catch(function(e) {
        body.innerHTML = '<div class="rex-live-empty">Failed to load feed: ' + (e && e.message ? e.message : 'unknown error') + '</div>';
      });
  }

  function renderFeedItem(item) {
    var badge = formBadgeClass(item.form);
    var trustHref = item.trust_slug ? '/trusts/' + item.trust_slug : null;
    var sec = item.primary_doc_url;
    var title = (item.trust_name || item.company_name || 'CIK ' + (item.cik || '?'));
    return '<div class="rex-live-item">' +
      '<div class="rex-live-item-top">' +
        '<span class="rex-live-form-badge ' + badge + '">' + item.form + '</span>' +
        '<span class="rex-live-time">' + formatTime(item.detected_at) + '</span>' +
      '</div>' +
      '<div class="rex-live-item-title">' +
        (trustHref ? '<a href="' + trustHref + '">' + escapeHtml(title) + '</a>' : escapeHtml(title)) +
      '</div>' +
      '<div class="rex-live-item-meta">' +
        (item.filed_date ? 'Filed ' + item.filed_date + ' \u00b7 ' : '') +
        'CIK ' + (item.cik || '?') +
        (sec ? ' \u00b7 <a href="' + sec + '" target="_blank" rel="noopener">SEC</a>' : '') +
      '</div>' +
    '</div>';
  }

  function escapeHtml(s) {
    if (!s) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function addToast(item) {
    var stack = document.getElementById('rex-live-toasts');
    if (!stack) return;

    // Cap the stack
    while (stack.children.length >= MAX_TOASTS) {
      stack.removeChild(stack.firstChild);
    }

    var toast = document.createElement('div');
    toast.className = 'rex-live-toast ' + formBadgeClass(item.form);
    var title = (item.trust_name || item.company_name || 'CIK ' + (item.cik || '?'));
    var trustHref = item.trust_slug ? '/trusts/' + item.trust_slug : null;
    toast.innerHTML =
      '<div class="rex-live-toast-top">' +
        '<span class="rex-live-form-badge ' + formBadgeClass(item.form) + '">' + item.form + '</span>' +
        '<button class="rex-live-toast-close" aria-label="Dismiss">\u00d7</button>' +
      '</div>' +
      '<div class="rex-live-toast-title">New filing</div>' +
      '<div class="rex-live-toast-body">' + escapeHtml(title) + '</div>' +
      '<div class="rex-live-toast-foot">' +
        (item.filed_date ? item.filed_date + ' \u00b7 ' : '') +
        (trustHref ? '<a href="' + trustHref + '">View trust</a> \u00b7 ' : '') +
        (item.primary_doc_url ? '<a href="' + item.primary_doc_url + '" target="_blank" rel="noopener">SEC</a>' : '') +
      '</div>';
    toast.querySelector('.rex-live-toast-close').addEventListener('click', function(e) {
      e.stopPropagation();
      dismissToast(toast);
    });
    // Click toast body -> open trust
    if (trustHref) {
      toast.addEventListener('click', function(e) {
        if (e.target.tagName !== 'A' && !e.target.classList.contains('rex-live-toast-close')) {
          window.location.href = trustHref;
        }
      });
    }
    stack.appendChild(toast);
    // Slide in
    setTimeout(function() { toast.classList.add('show'); }, 10);
    // Auto dismiss
    setTimeout(function() { dismissToast(toast); }, TOAST_DURATION);
  }

  function dismissToast(toast) {
    if (!toast || !toast.parentNode) return;
    toast.classList.remove('show');
    setTimeout(function() {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  function updateBellCount(delta) {
    var bell = document.getElementById('rex-live-bell');
    if (!bell) return;
    var countEl = bell.querySelector('.rex-live-bell-count');
    if (!countEl) return;
    var current = parseInt(countEl.textContent, 10) || 0;
    current += delta;
    if (current < 0) current = 0;
    countEl.textContent = current > 99 ? '99+' : String(current);
    countEl.style.display = current > 0 ? 'inline-flex' : 'none';
  }

  var isFirstPoll = true;

  function poll() {
    var since = getLatest();
    var url = '/api/v1/live/recent?limit=20' + (since ? '&since=' + encodeURIComponent(since) : '');
    fetch(url)
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data || !data.items) return;

        // First poll after page load: just seed the latest timestamp, no toasts.
        // (Avoids toasting the same filing on every page navigation.)
        if (isFirstPoll) {
          isFirstPoll = false;
          if (data.latest) setLatest(data.latest);
          return;
        }

        if (data.items.length === 0) return;

        var seen = getSeenSet();
        var newItems = [];
        data.items.forEach(function(item) {
          if (!seen.has(item.accession_number)) {
            newItems.push(item);
            seen.add(item.accession_number);
          }
        });
        saveSeenSet(seen);

        // Toast newest-first (items come back newest-first from API)
        newItems.forEach(function(item) {
          addToast(item);
        });

        if (newItems.length > 0) {
          updateBellCount(newItems.length);
        }

        if (data.latest) setLatest(data.latest);
      })
      .catch(function() { /* silent */ });
  }

  function init() {
    ensureContainers();
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
