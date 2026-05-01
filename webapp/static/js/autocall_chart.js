/* Autocallable note simulator - UI controller. Wires DOM controls to the
 * AutocallEngine and renders a Plotly chart. Calls Plotly.react on every
 * scrub event for diff-based redraws (60fps feel).
 *
 * Features beyond bare simulator:
 *   - Observation log table (sortable, sticky header, CSV export)
 *   - URL state sync (encode every input as query param, debounced writeback)
 *   - Compare mode (Scenario B with dual chart series + dual KPI panel)
 *   - Distribution sweep tab (POST /notes/tools/autocall/sweep)
 */
(function (global) {
  'use strict';

  if (!global.AutocallEngine) {
    console.error('AutocallEngine not loaded - autocall_engine.js must be included before autocall_chart.js');
    return;
  }

  var Engine = global.AutocallEngine;
  var Outcome = Engine.Outcome;
  var ObsStatus = Engine.ObsStatus;

  // Distinct, accessible palette for up to 5 references.
  // Scenario A uses cool/blue family; Scenario B uses warm/orange family for clear contrast.
  var REF_COLORS = ['#2563eb', '#0D9488', '#0891b2', '#7c3aed', '#1e40af'];
  var REF_COLORS_B = ['#ea580c', '#dc2626', '#d97706', '#be185d', '#a21caf'];
  var BARRIER_COLORS = {
    ac: '#2563eb',
    coupon: '#FF9800',
    protection: '#dc2626',
  };

  var STATUS_LABELS = {
    coupon_paid: 'Coupon Paid',
    coupon_missed: 'Coupon Missed',
    memory_catchup: 'Memory Catchup',
    autocall: 'Autocall',
    maturity_above: 'Matured Above',
    maturity_below: 'Matured Below',
  };

  var BUCKET_COLORS = {
    autocalled: '#2563eb',
    matured_above: '#0D9488',
    matured_below: '#dc2626',
    in_progress: '#94a3b8',
  };

  var BUCKET_LABELS = {
    autocalled: 'Autocalled',
    matured_above: 'Matured Above',
    matured_below: 'Matured Below',
    in_progress: 'In Progress',
  };

  var state = {
    bootstrap: null,
    store: null,
    metaByTicker: {},
    refs: [null, null, null, null, null],
    issueDateISO: null,
    legalDates: [],   // ascending ISO strings - slider domain (intersection of selected refs)
    chartReady: false,
    rafPending: false,

    // Compare mode
    compareOn: false,

    // Present mode
    presentOn: false,
    refsB: [null],
    issueDateBISO: null,

    // B follows A — when ON, B's issue date mirrors A on every change
    bFollowA: true,

    // Coupon mode: 'manual' | 'suggested' — default suggested (auto-fills coupon)
    couponMode: 'suggested',

    // Active tab
    activeTab: 'lifespan',

    // Last simulation results, kept for log + CSV export
    lastResultA: null,
    lastResultB: null,

    // Log sorting
    logSort: { key: 'k', dir: 'asc', type: 'num' },

    // URL writeback debounce
    urlTimer: null,

    // Suppress URL writes during initial decode
    urlInit: true,
  };

  // ---- DOM helpers --------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function showError(msg) {
    var b = $('ac-error-banner');
    if (!b) return;
    if (!msg) {
      b.style.display = 'none';
      b.textContent = '';
    } else {
      b.style.display = '';
      b.textContent = msg;
    }
  }

  // ---- Bootstrap load + UI population ------------------------------------

  function init(dataUrl) {
    fetch(dataUrl, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('Failed to load bootstrap data: HTTP ' + r.status);
        return r.json();
      })
      .then(function (boot) {
        state.bootstrap = boot;
        state.store = new Engine.LevelStore();
        state.store.loadBootstrap(boot);

        (boot.metadata || []).forEach(function (m) {
          state.metaByTicker[m.ticker] = m;
        });

        // Data-as-of badge from bootstrap.
        var asof = $('ac-data-asof-date');
        if (asof) asof.textContent = boot.max_date || '--';

        populateRefDropdowns();
        populateScenarioBRefDropdown();
        populatePresets();
        bindControls();
        applyDefaults();
        // Auto-suggest defaults ON; reflect in UI before first render.
        applyCouponMode();
        // Decode URL state AFTER defaults so URL takes precedence.
        decodeUrlState();
        recomputeLegalRange();
        scheduleRender();
        // Allow URL writes from now on.
        state.urlInit = false;
      })
      .catch(function (e) {
        showError(e.message || String(e));
      });
  }

  function populateRefDropdowns() {
    var meta = state.bootstrap.metadata || [];
    var underlyings = meta
      .filter(function (m) { return m.category === 'underlying'; })
      .sort(sortMeta);
    var strategies = meta
      .filter(function (m) { return m.category === 'strategy_underlying'; })
      .sort(sortMeta);

    for (var i = 0; i < 5; i++) {
      var sel = $('ac-ref-' + (i + 1));
      if (!sel) continue;

      var blank = document.createElement('option');
      blank.value = '';
      blank.textContent = i === 0 ? '-- select reference --' : '(none)';
      sel.appendChild(blank);

      if (underlyings.length) {
        var g1 = document.createElement('optgroup');
        g1.label = 'Underlyings';
        underlyings.forEach(function (m) { g1.appendChild(makeOpt(m)); });
        sel.appendChild(g1);
      }
      if (strategies.length) {
        var g2 = document.createElement('optgroup');
        g2.label = 'Strategy Underlyings';
        strategies.forEach(function (m) { g2.appendChild(makeOpt(m)); });
        sel.appendChild(g2);
      }
    }
  }

  function populateScenarioBRefDropdown() {
    var meta = state.bootstrap.metadata || [];
    var underlyings = meta
      .filter(function (m) { return m.category === 'underlying'; })
      .sort(sortMeta);
    var strategies = meta
      .filter(function (m) { return m.category === 'strategy_underlying'; })
      .sort(sortMeta);

    var sel = $('ac-b-ref-1');
    if (!sel) return;
    var blank = document.createElement('option');
    blank.value = '';
    blank.textContent = '-- select reference --';
    sel.appendChild(blank);
    if (underlyings.length) {
      var g1 = document.createElement('optgroup');
      g1.label = 'Underlyings';
      underlyings.forEach(function (m) { g1.appendChild(makeOpt(m)); });
      sel.appendChild(g1);
    }
    if (strategies.length) {
      var g2 = document.createElement('optgroup');
      g2.label = 'Strategy Underlyings';
      strategies.forEach(function (m) { g2.appendChild(makeOpt(m)); });
      sel.appendChild(g2);
    }
  }

  function sortMeta(a, b) {
    var ao = a.sort_order == null ? 9999 : a.sort_order;
    var bo = b.sort_order == null ? 9999 : b.sort_order;
    if (ao !== bo) return ao - bo;
    return a.ticker.localeCompare(b.ticker);
  }

  function makeOpt(m) {
    var o = document.createElement('option');
    o.value = m.ticker;
    o.textContent = m.ticker;
    return o;
  }

  function populatePresets() {
    var wrap = $('ac-presets');
    if (!wrap) return;
    var presets = (state.bootstrap.presets || []).slice().sort(function (a, b) {
      var ao = a.sort_order == null ? 9999 : a.sort_order;
      var bo = b.sort_order == null ? 9999 : b.sort_order;
      return ao - bo;
    });
    presets.forEach(function (p) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ac-preset-btn';
      btn.textContent = p.name;
      btn.addEventListener('click', function () {
        var snap = snapToLegalDate(p.start_date);
        if (snap == null) return;
        var slider = $('ac-issue-scrub');
        slider.value = String(snap);
        state.issueDateISO = state.legalDates[snap];
        updateIssueLabel();
        scheduleRender();
        scheduleUrlWrite();
      });
      wrap.appendChild(btn);
    });
  }

  function applyDefaults() {
    // Default Ref 1 = BMAXUS Index if available, else first underlying.
    var defaultRef = 'BMAXUS Index';
    if (!state.store.tickers().includes(defaultRef)) {
      var meta = state.bootstrap.metadata || [];
      var firstUnd = meta.find(function (m) { return m.category === 'underlying'; });
      defaultRef = firstUnd ? firstUnd.ticker : (meta[0] ? meta[0].ticker : '');
    }
    var sel1 = $('ac-ref-1');
    if (sel1) {
      sel1.value = defaultRef;
      state.refs[0] = defaultRef || null;
    }
    // Scenario B mirrors A's default ref.
    var selB = $('ac-b-ref-1');
    if (selB) {
      selB.value = defaultRef;
      state.refsB[0] = defaultRef || null;
    }
  }

  // ---- Legal range (intersection of inception of selected refs) ----------

  function recomputeLegalRange() {
    var selected = state.refs.filter(function (r) { return !!r; });
    if (!selected.length) {
      state.legalDates = [];
      updateScrubBounds();
      return;
    }

    var ref1 = selected[0];
    var ref1Dates = (state.bootstrap.tickers[ref1] || {}).dates || [];
    var maxDate = state.bootstrap.max_date;

    var earliest = '';
    selected.forEach(function (r) {
      var inc = state.store.inception(r);
      if (inc && inc > earliest) earliest = inc;
    });

    var legal = [];
    for (var i = 0; i < ref1Dates.length; i++) {
      var d = ref1Dates[i];
      if (d < earliest) continue;
      if (d > maxDate) break;
      legal.push(d);
    }
    state.legalDates = legal;

    var slider = $('ac-issue-scrub');
    if (!legal.length) {
      state.issueDateISO = null;
      slider.min = '0';
      slider.max = '0';
      slider.value = '0';
    } else {
      var idx;
      if (state.issueDateISO) {
        idx = bisectLeftStr(legal, state.issueDateISO);
        if (idx >= legal.length) idx = legal.length - 1;
      } else {
        var defaultIso = Engine.edate(maxDate, -63);
        idx = bisectLeftStr(legal, defaultIso);
        if (idx >= legal.length) idx = legal.length - 1;
        if (idx < 0) idx = 0;
      }
      // Slider step = 5 trading days reduces sensitivity. Typable date input
      // gives day-precision when the user wants it (handled by onIssueDateTyped).
      slider.min = '0';
      slider.max = String(legal.length - 1);
      slider.step = '5';
      slider.value = String(idx);
      state.issueDateISO = legal[idx];
    }
    updateScrubBounds();
    updateIssueLabel();
  }

  function updateScrubBounds() {
    var lo = $('ac-scrub-min');
    var hi = $('ac-scrub-max');
    if (state.legalDates.length) {
      lo.textContent = state.legalDates[0];
      hi.textContent = state.legalDates[state.legalDates.length - 1];
    } else {
      lo.textContent = '--';
      hi.textContent = '--';
    }
  }

  function updateIssueLabel() {
    var typed = $('ac-issue-typed');
    if (typed) {
      // Set min/max to the slider's legal range; value = current scrub.
      if (state.legalDates.length) {
        typed.min = state.legalDates[0];
        typed.max = state.legalDates[state.legalDates.length - 1];
      }
      typed.value = state.issueDateISO || '';
    }
  }

  function snapToLegalDate(iso) {
    if (!state.legalDates.length) return null;
    var i = bisectLeftStr(state.legalDates, iso);
    if (i >= state.legalDates.length) i = state.legalDates.length - 1;
    if (i > 0) {
      var prev = state.legalDates[i - 1];
      var cur = state.legalDates[i];
      if (Math.abs(diffDays(prev, iso)) < Math.abs(diffDays(cur, iso))) i = i - 1;
    }
    return i;
  }

  function bisectLeftStr(arr, target) {
    var lo = 0, hi = arr.length;
    while (lo < hi) {
      var mid = (lo + hi) >>> 1;
      if (arr[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function diffDays(a, b) {
    return (Engine.parseISO(b) - Engine.parseISO(a)) / 86400000;
  }

  // ---- Control bindings --------------------------------------------------

  function bindControls() {
    for (var i = 0; i < 5; i++) {
      var sel = $('ac-ref-' + (i + 1));
      if (!sel) continue;
      sel.addEventListener('change', onRefChange);
    }

    var slider = $('ac-issue-scrub');
    slider.addEventListener('input', onScrub);

    var typed = $('ac-issue-typed');
    if (typed) typed.addEventListener('change', onIssueTyped);

    var ids = ['ac-tenor', 'ac-obs-freq', 'ac-coupon-rate', 'ac-coupon-barrier',
      'ac-ac-barrier', 'ac-prot-barrier', 'ac-no-call', 'ac-pad-before', 'ac-pad-after'];
    ids.forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('input', onParamChange);
    });

    var memEl = $('ac-memory');
    if (memEl) memEl.addEventListener('change', onParamChange);

    // Scenario B controls
    var selB = $('ac-b-ref-1');
    if (selB) selB.addEventListener('change', onRefBChange);
    var bIds = ['ac-b-issue', 'ac-b-tenor', 'ac-b-obs-freq', 'ac-b-coupon-rate',
      'ac-b-coupon-barrier', 'ac-b-ac-barrier', 'ac-b-prot-barrier', 'ac-b-no-call'];
    bIds.forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('input', onParamChange);
    });
    var memB = $('ac-b-memory');
    if (memB) memB.addEventListener('change', onParamChange);

    // Compare toggle
    var cmp = $('ac-compare-toggle');
    if (cmp) cmp.addEventListener('click', onToggleCompare);

    // Present toggle
    var pres = $('ac-present-toggle');
    if (pres) pres.addEventListener('click', onTogglePresent);

    var sidebarExpand = $('ac-sidebar-expand');
    if (sidebarExpand) sidebarExpand.addEventListener('click', onSidebarExpand);

    var bfa = $('ac-b-follow-a');
    if (bfa) bfa.addEventListener('change', onToggleBFollowA);

    // Coupon mode toggle (Auto-suggest)
    var cmode = $('ac-coupon-mode');
    if (cmode) cmode.addEventListener('change', onToggleCouponMode);

    // Reset button
    var rst = $('ac-reset');
    if (rst) rst.addEventListener('click', resetToDefaults);

    // About modal
    var about = $('ac-about');
    if (about) about.addEventListener('click', function () {
      var m = $('ac-about-modal');
      if (m) m.style.display = 'flex';
    });
    var aboutClose = $('ac-about-close');
    if (aboutClose) aboutClose.addEventListener('click', function () {
      var m = $('ac-about-modal');
      if (m) m.style.display = 'none';
    });
    var aboutOverlay = $('ac-about-modal');
    if (aboutOverlay) aboutOverlay.addEventListener('click', function (e) {
      if (e.target.id === 'ac-about-modal') aboutOverlay.style.display = 'none';
    });

    // Copy link
    var copy = $('ac-copy-link');
    if (copy) copy.addEventListener('click', onCopyLink);

    // CSV export
    var csv = $('ac-log-csv');
    if (csv) csv.addEventListener('click', onCsvDownload);

    // Sortable log columns
    var headers = document.querySelectorAll('#ac-log-table thead th[data-sort]');
    headers.forEach(function (th) {
      th.addEventListener('click', function () {
        onSortClick(th.getAttribute('data-sort'), th.getAttribute('data-type'));
      });
    });

    // Tabs
    var tabs = document.querySelectorAll('.ac-tab');
    tabs.forEach(function (t) {
      t.addEventListener('click', function () {
        setActiveTab(t.getAttribute('data-tab'));
      });
    });

    // Sweep run
    var sweep = $('ac-sweep-run');
    if (sweep) sweep.addEventListener('click', runSweep);
  }

  function onRefChange(e) {
    var idx = parseInt(e.target.dataset.refIdx, 10);
    var val = e.target.value || null;
    state.refs[idx] = val;
    recomputeLegalRange();
    scheduleRender();
    scheduleUrlWrite();
  }

  function onRefBChange(e) {
    var idx = parseInt(e.target.dataset.bRefIdx, 10);
    var val = e.target.value || null;
    state.refsB[idx] = val;
    scheduleRender();
    scheduleUrlWrite();
  }

  function onScrub(e) {
    var idx = parseInt(e.target.value, 10);
    if (state.legalDates.length) {
      if (idx < 0) idx = 0;
      if (idx >= state.legalDates.length) idx = state.legalDates.length - 1;
      state.issueDateISO = state.legalDates[idx];
    }
    if (state.bFollowA && state.compareOn) {
      state.issueDateBISO = state.issueDateISO;
      var bIssue = $('ac-b-issue');
      if (bIssue) bIssue.value = state.issueDateISO || '';
    }
    updateIssueLabel();
    scheduleRender();
    scheduleUrlWrite();
  }

  function onIssueTyped(e) {
    var iso = e.target.value;
    if (!iso || !state.legalDates.length) return;
    // snapToLegalDate returns an index into legalDates (or null).
    var idx = snapToLegalDate(iso);
    if (idx == null) return;
    state.issueDateISO = state.legalDates[idx];
    if (state.bFollowA && state.compareOn) {
      state.issueDateBISO = state.issueDateISO;
      var bIssue = $('ac-b-issue');
      if (bIssue) bIssue.value = state.issueDateISO;
    }
    var slider = $('ac-issue-scrub');
    if (slider) slider.value = String(idx);
    updateIssueLabel();
    scheduleRender();
    scheduleUrlWrite();
  }

  function onParamChange() {
    scheduleRender();
    scheduleUrlWrite();
  }

  function resetToDefaults() {
    var defaults = [
      ['ac-tenor', '60'], ['ac-obs-freq', '1'], ['ac-coupon-rate', '10'],
      ['ac-coupon-barrier', '60'], ['ac-ac-barrier', '100'], ['ac-prot-barrier', '50'],
      ['ac-no-call', '12'], ['ac-pad-before', '3'], ['ac-pad-after', '3'],
    ];
    defaults.forEach(function (pair) { var e = $(pair[0]); if (e) e.value = pair[1]; });
    var mem = $('ac-memory'); if (mem) mem.checked = false;
    var cmode = $('ac-coupon-mode'); if (cmode) cmode.checked = true;
    state.couponMode = 'suggested';
    applyCouponMode();
    // Reset refs to BMAXUS only.
    var primary = state.store && state.store.tickers().includes('BMAXUS Index') ? 'BMAXUS Index' : null;
    state.refs = [primary, null, null, null, null];
    var sels = [$('ac-ref-1'), $('ac-ref-2'), $('ac-ref-3'), $('ac-ref-4'), $('ac-ref-5')];
    sels.forEach(function (s, i) { if (s) s.value = i === 0 && primary ? primary : ''; });
    recomputeLegalRange();
    scheduleRender();
    scheduleUrlWrite();
  }

  function onToggleCouponMode() {
    var el = $('ac-coupon-mode');
    state.couponMode = el && el.checked ? 'suggested' : 'manual';
    applyCouponMode();
    scheduleRender();
    scheduleUrlWrite();
  }

  function applyCouponMode() {
    var input = $('ac-coupon-rate');
    var inputB = $('ac-b-coupon-rate');
    var tag = $('ac-coupon-suggested-tag');
    var tagB = $('ac-b-coupon-suggested-tag');
    if (state.couponMode === 'suggested') {
      document.body.classList.add('ac-coupon-suggested');
      if (tag) tag.style.display = '';
      if (tagB) tagB.style.display = '';
      if (input) input.readOnly = true;
      if (inputB) inputB.readOnly = true;
    } else {
      document.body.classList.remove('ac-coupon-suggested');
      if (tag) tag.style.display = 'none';
      if (tagB) tagB.style.display = 'none';
      if (input) input.readOnly = false;
      if (inputB) inputB.readOnly = false;
    }
  }

  var _bsFetchTimer = null;
  var _bsFetchSeq = 0;

  function setBsLoading(on) {
    var el = $('ac-coupon-loading');
    if (el) el.style.display = on ? '' : 'none';
  }

  function scheduleBsCouponFetch() {
    if (state.couponMode !== 'suggested') return;
    if (_bsFetchTimer) clearTimeout(_bsFetchTimer);
    _bsFetchTimer = setTimeout(function () {
      _bsFetchTimer = null;
      var seq = ++_bsFetchSeq;
      var refs = state.refs.filter(function (r) { return !!r; });
      if (!refs.length || !state.issueDateISO) return;
      var p = readParamsRaw();
      setBsLoading(true);
      fetch('/notes/tools/autocall/suggest-coupon', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refs: refs, issue_date: state.issueDateISO, params: p }),
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (seq !== _bsFetchSeq) return;  // stale
          setBsLoading(false);
          if (!d) return;
          if (d.coupon_pa_pct != null) {
            var input = $('ac-coupon-rate');
            if (input) {
              input.value = d.coupon_pa_pct.toFixed(2);
              input.title = 'BS Monte Carlo par-coupon (' + (d.method || 'bs_mc') + ')';
            }
            scheduleRender();
          }
        })
        .catch(function () {
          if (seq === _bsFetchSeq) setBsLoading(false);
        });
    }, 450);
  }

  function maybeAutoSuggestCoupon() {
    if (state.couponMode !== 'suggested') return;
    if (!state.store || !state.issueDateISO) return;
    var refs = state.refs.filter(function (r) { return !!r; });
    if (refs.length) {
      var p = readParamsRaw();
      // Synchronous JS heuristic for instant feel; BS overwrites async on settle.
      var sc = Engine.suggestCoupon(refs, state.issueDateISO, p, state.store);
      if (sc != null) {
        var input = $('ac-coupon-rate');
        if (input) input.value = sc.toFixed(2);
      }
      scheduleBsCouponFetch();
    }
    // Scenario B (if compare mode active)
    if (state.compareOn) {
      var refsB = state.refsB.filter(function (r) { return !!r; });
      var issueB = state.issueDateBISO || ($('ac-b-issue') && $('ac-b-issue').value) || null;
      if (refsB.length && issueB) {
        var pB = readParamsRawB();
        var scB = Engine.suggestCoupon(refsB, issueB, pB, state.store);
        if (scB != null) {
          var inputB = $('ac-b-coupon-rate');
          if (inputB) inputB.value = scB.toFixed(2);
        }
      }
    }
  }

  function readParamsRawB() {
    return {
      tenor_months: clampInt('ac-b-tenor', 1, 600, 60),
      obs_freq_months: clampInt('ac-b-obs-freq', 1, 60, 1),
      coupon_rate_pa_pct: clampNum('ac-b-coupon-rate', 0, 1000, 10),
      coupon_barrier_pct: clampNum('ac-b-coupon-barrier', 0, 1000, 60),
      ac_barrier_pct: clampNum('ac-b-ac-barrier', 0, 1000, 100),
      protection_barrier_pct: clampNum('ac-b-prot-barrier', 0, 1000, 50),
      memory: !!($('ac-b-memory') && $('ac-b-memory').checked),
      no_call_months: clampInt('ac-b-no-call', 0, 600, 12),
    };
  }

  // Read params WITHOUT applying suggested-coupon override (used by suggester).
  function readParamsRaw() {
    return {
      tenor_months: clampInt('ac-tenor', 1, 600, 60),
      obs_freq_months: clampInt('ac-obs-freq', 1, 60, 1),
      coupon_rate_pa_pct: clampNum('ac-coupon-rate', 0, 1000, 10),
      coupon_barrier_pct: clampNum('ac-coupon-barrier', 0, 1000, 60),
      ac_barrier_pct: clampNum('ac-ac-barrier', 0, 1000, 100),
      protection_barrier_pct: clampNum('ac-prot-barrier', 0, 1000, 50),
      memory: !!($('ac-memory') && $('ac-memory').checked),
      no_call_months: clampInt('ac-no-call', 0, 600, 12),
    };
  }

  function onTogglePresent() {
    state.presentOn = !state.presentOn;
    document.body.classList.toggle('ac-present-on', state.presentOn);
    // Plotly chart needs to recompute layout after the surrounding DOM resizes.
    setTimeout(function () {
      var div = $('ac-chart');
      if (div && window.Plotly) {
        try { Plotly.Plots.resize(div); } catch (e) {}
      }
    }, 50);
    scheduleUrlWrite();
  }

  function onSidebarExpand() {
    if (!state.presentOn) return;
    state.presentOn = false;
    document.body.classList.remove('ac-present-on');
    setTimeout(function () {
      var div = $('ac-chart');
      if (div && window.Plotly) {
        try { Plotly.Plots.resize(div); } catch (e) {}
      }
    }, 50);
    scheduleUrlWrite();
  }

  function onToggleBFollowA() {
    var el = $('ac-b-follow-a');
    state.bFollowA = !!(el && el.checked);
    var bIssue = $('ac-b-issue');
    if (bIssue) bIssue.disabled = state.bFollowA;
    if (state.bFollowA && state.issueDateISO) {
      state.issueDateBISO = state.issueDateISO;
      if (bIssue) bIssue.value = state.issueDateISO;
    }
    scheduleRender();
    scheduleUrlWrite();
  }

  function onToggleCompare() {
    state.compareOn = !state.compareOn;
    var btn = $('ac-compare-toggle');
    if (btn) btn.classList.toggle('ac-tool-btn-active', state.compareOn);
    var panel = $('ac-scenario-b');
    if (panel) panel.style.display = state.compareOn ? '' : 'none';
    var colB = document.querySelector('.ac-results-col-b');
    if (colB) colB.style.display = state.compareOn ? '' : 'none';
    var pill = $('ac-vs-pill');
    if (pill) pill.style.display = state.compareOn ? '' : 'none';
    document.body.classList.toggle('ac-compare-on', state.compareOn);

    // Default scenario B issue date if blank: same as A.
    if (state.compareOn && !state.issueDateBISO) {
      state.issueDateBISO = state.issueDateISO || null;
      var bIssue = $('ac-b-issue');
      if (bIssue && state.issueDateBISO) bIssue.value = state.issueDateBISO;
    }
    // Default scenario B params from A.
    if (state.compareOn) {
      mirrorAParamsToBIfBlank();
    }
    scheduleRender();
    scheduleUrlWrite();
  }

  function mirrorAParamsToBIfBlank() {
    var pairs = [
      ['ac-tenor', 'ac-b-tenor'],
      ['ac-obs-freq', 'ac-b-obs-freq'],
      ['ac-coupon-rate', 'ac-b-coupon-rate'],
      ['ac-coupon-barrier', 'ac-b-coupon-barrier'],
      ['ac-ac-barrier', 'ac-b-ac-barrier'],
      ['ac-prot-barrier', 'ac-b-prot-barrier'],
      ['ac-no-call', 'ac-b-no-call'],
    ];
    pairs.forEach(function (p) {
      var a = $(p[0]); var b = $(p[1]);
      if (a && b && b.value === '') b.value = a.value;
    });
    var memA = $('ac-memory'); var memB = $('ac-b-memory');
    if (memA && memB) memB.checked = memA.checked;
  }

  // ---- Param collection --------------------------------------------------

  function readParams() {
    return {
      tenor_months: clampInt('ac-tenor', 1, 600, 60),
      obs_freq_months: clampInt('ac-obs-freq', 1, 60, 1),
      coupon_rate_pa_pct: clampNum('ac-coupon-rate', 0, 1000, 10),
      coupon_barrier_pct: clampNum('ac-coupon-barrier', 0, 500, 60),
      ac_barrier_pct: clampNum('ac-ac-barrier', 0, 500, 100),
      protection_barrier_pct: clampNum('ac-prot-barrier', 0, 500, 50),
      no_call_months: clampInt('ac-no-call', 0, 600, 12),
      memory: !!($('ac-memory') && $('ac-memory').checked),
    };
  }

  function readParamsB() {
    return {
      tenor_months: clampInt('ac-b-tenor', 1, 600, 60),
      obs_freq_months: clampInt('ac-b-obs-freq', 1, 60, 1),
      coupon_rate_pa_pct: clampNum('ac-b-coupon-rate', 0, 1000, 10),
      coupon_barrier_pct: clampNum('ac-b-coupon-barrier', 0, 500, 60),
      ac_barrier_pct: clampNum('ac-b-ac-barrier', 0, 500, 100),
      protection_barrier_pct: clampNum('ac-b-prot-barrier', 0, 500, 50),
      no_call_months: clampInt('ac-b-no-call', 0, 600, 12),
      memory: !!($('ac-b-memory') && $('ac-b-memory').checked),
    };
  }

  function clampInt(id, lo, hi, dflt) {
    var v = parseInt(($(id) || {}).value, 10);
    if (!isFinite(v)) return dflt;
    return Math.max(lo, Math.min(hi, v));
  }
  function clampNum(id, lo, hi, dflt) {
    var v = parseFloat(($(id) || {}).value);
    if (!isFinite(v)) return dflt;
    return Math.max(lo, Math.min(hi, v));
  }

  function readPad() {
    return {
      before: clampInt('ac-pad-before', 0, 60, 3),
      after: clampInt('ac-pad-after', 0, 60, 3),
    };
  }

  // ---- Render scheduling -------------------------------------------------

  function scheduleRender() {
    if (state.rafPending) return;
    state.rafPending = true;
    requestAnimationFrame(function () {
      state.rafPending = false;
      try {
        render();
      } catch (e) {
        showError(e.message || String(e));
        console.error(e);
      }
    });
  }

  // ---- Simulation + chart ------------------------------------------------

  function setEmptyState(on) {
    var es = $('ac-empty-state');
    var cw = $('ac-chart-wrap');
    if (es) es.style.display = on ? '' : 'none';
    if (cw) cw.style.display = on ? 'none' : '';
  }

  function render() {
    var refs = state.refs.filter(function (r) { return !!r; });
    if (!refs.length || !state.issueDateISO) {
      setEmptyState(true);
      updateResults(null);
      updateResultsB(null);
      state.lastResultA = null;
      state.lastResultB = null;
      renderObservationLog();
      return;
    }
    setEmptyState(false);
    maybeAutoSuggestCoupon();
    var params = readParams();
    var pad = readPad();
    var resultA = Engine.simulate(refs, state.issueDateISO, params, state.store);
    state.lastResultA = resultA;

    if (resultA.outcome === Outcome.INVALID) {
      showError(resultA.error || 'Invalid configuration.');
    } else {
      showError(null);
    }

    var maturityISO = Engine.edate(state.issueDateISO, params.tenor_months);
    var maxDate = state.store.maxDate;

    var xStartISO = shiftMonths(state.issueDateISO, -pad.before);
    var xEndISOTarget = shiftMonths(maturityISO, pad.after);
    if (xEndISOTarget > maxDate) xEndISOTarget = maxDate;
    var xEndISO = xEndISOTarget;

    var initialLevels = {};
    refs.forEach(function (r) {
      var lvl = state.store.locf(r, state.issueDateISO);
      initialLevels[r] = lvl && lvl > 0 ? lvl : null;
    });

    var worstRef = pickWorstRef(refs, resultA, initialLevels, xEndISO);

    var traces = [];

    refs.forEach(function (r, i) {
      var init = initialLevels[r];
      if (!init) return;
      var s = state.store.seriesBetween(r, xStartISO, xEndISO);
      var ys = new Array(s.levels.length);
      for (var k = 0; k < s.levels.length; k++) {
        ys[k] = (s.levels[k] / init) * 100;
      }
      var name = (state.metaByTicker[r] && state.metaByTicker[r].short_name) || r;
      var isWorst = r === worstRef;
      traces.push({
        type: 'scatter', mode: 'lines',
        x: s.dates, y: ys, name: name,
        line: {
          color: REF_COLORS[i % REF_COLORS.length],
          width: isWorst ? 2.5 : 1.5,
        },
        opacity: (worstRef && !isWorst) ? 0.55 : 1,
        hovertemplate: '%{x}<br>' + name + ': %{y:.2f}<extra></extra>',
      });
    });

    var xBarrier = [xStartISO, xEndISO];
    traces.push(barrierTrace(xBarrier, params.ac_barrier_pct, 'AC Barrier', BARRIER_COLORS.ac));
    traces.push(barrierTrace(xBarrier, params.coupon_barrier_pct, 'Coupon Barrier', BARRIER_COLORS.coupon));
    traces.push(barrierTrace(xBarrier, params.protection_barrier_pct, 'Protection Barrier', BARRIER_COLORS.protection));

    if (resultA.outcome !== Outcome.INVALID) {
      var ev = buildEventMarkers(resultA, params, '');
      Object.keys(ev).forEach(function (key) {
        var pts = ev[key];
        if (!pts.x.length) return;
        traces.push(pts);
      });
    }

    var shapes = [];
    shapes.push(vline(state.issueDateISO, '#475569'));
    if (resultA.outcome_date) shapes.push(vline(resultA.outcome_date, '#475569'));
    shapes.push(vline(maturityISO, '#94a3b8'));

    var annotations = [
      vAnno(state.issueDateISO, 0, 'Issue', false),
      vAnno(maturityISO, 0, 'Maturity', false),
    ];
    if (resultA.outcome_date && resultA.outcome_date !== state.issueDateISO &&
        resultA.outcome_date !== maturityISO) {
      annotations.push(vAnno(resultA.outcome_date, 0, outcomeShortLabel(resultA.outcome), false));
    }

    // ---- Scenario B -------------------------------------------------------
    var resultB = null;
    var xStartFinal = xStartISO;
    var xEndFinal = xEndISO;

    if (state.compareOn) {
      var refsB = state.refsB.filter(function (r) { return !!r; });
      var issueB = state.issueDateBISO || ($('ac-b-issue') && $('ac-b-issue').value) || null;
      if (refsB.length && issueB) {
        var paramsB = readParamsB();
        resultB = Engine.simulate(refsB, issueB, paramsB, state.store);
        state.lastResultB = resultB;

        var maturityBISO = Engine.edate(issueB, paramsB.tenor_months);
        var xStartBISO = shiftMonths(issueB, -pad.before);
        var xEndBTarget = shiftMonths(maturityBISO, pad.after);
        if (xEndBTarget > maxDate) xEndBTarget = maxDate;
        var xEndBISO = xEndBTarget;

        if (xStartBISO < xStartFinal) xStartFinal = xStartBISO;
        if (xEndBISO > xEndFinal) xEndFinal = xEndBISO;

        var initialB = {};
        refsB.forEach(function (r) {
          var lvl = state.store.locf(r, issueB);
          initialB[r] = lvl && lvl > 0 ? lvl : null;
        });

        refsB.forEach(function (r, i) {
          var init = initialB[r];
          if (!init) return;
          var s = state.store.seriesBetween(r, xStartBISO, xEndBISO);
          var ys = new Array(s.levels.length);
          for (var k = 0; k < s.levels.length; k++) {
            ys[k] = (s.levels[k] / init) * 100;
          }
          var name = ((state.metaByTicker[r] && state.metaByTicker[r].short_name) || r) + ' (B)';
          traces.push({
            type: 'scatter', mode: 'lines',
            x: s.dates, y: ys, name: name,
            line: {
              color: REF_COLORS_B[i % REF_COLORS_B.length],
              width: 2.2,
              dash: 'dot',
            },
            opacity: 0.95,
            hovertemplate: '%{x}<br>' + name + ': %{y:.2f}<extra></extra>',
          });
        });

        // Scenario B barriers (lighter, dashed dotted)
        var xBarrierB = [xStartBISO, xEndBISO];
        traces.push(barrierTraceB(xBarrierB, paramsB.ac_barrier_pct, 'AC Barrier (B)', BARRIER_COLORS.ac));
        traces.push(barrierTraceB(xBarrierB, paramsB.coupon_barrier_pct, 'Coupon Barrier (B)', BARRIER_COLORS.coupon));
        traces.push(barrierTraceB(xBarrierB, paramsB.protection_barrier_pct, 'Protection Barrier (B)', BARRIER_COLORS.protection));

        if (resultB.outcome !== Outcome.INVALID) {
          var evB = buildEventMarkers(resultB, paramsB, ' (B)');
          Object.keys(evB).forEach(function (key) {
            var pts = evB[key];
            if (!pts.x.length) return;
            // Distinguish scenario B markers visually.
            pts.marker.symbol = (pts.marker.symbol || 'circle') + (pts.marker.symbol && pts.marker.symbol.indexOf('open') >= 0 ? '' : '-open');
            pts.opacity = 0.85;
            traces.push(pts);
          });
        }

        shapes.push(vline(issueB, '#94a3b8'));
        if (resultB.outcome_date) shapes.push(vline(resultB.outcome_date, '#94a3b8'));
        shapes.push(vline(maturityBISO, '#cbd5e1'));

        annotations.push(vAnno(issueB, 0, 'Issue B', true));
        annotations.push(vAnno(maturityBISO, 0, 'Maturity B', true));
      } else {
        state.lastResultB = null;
      }
    } else {
      state.lastResultB = null;
    }

    // Y-axis ceiling
    var allY = [];
    traces.forEach(function (t) {
      if (t.y && t.y.length) {
        for (var i = 0; i < t.y.length; i++) {
          if (typeof t.y[i] === 'number' && isFinite(t.y[i])) allY.push(t.y[i]);
        }
      }
    });
    var maxY = allY.length ? Math.max.apply(null, allY) : 100;
    maxY = Math.max(maxY, params.ac_barrier_pct, 100);
    var yMax = Math.ceil(maxY / 5) * 5 + 5;

    // Lift annotations to yMax now that it's known.
    annotations.forEach(function (a) { a.y = yMax; });

    var layout = {
      margin: { l: 50, r: 20, t: 30, b: 40 },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      font: { family: 'Inter, system-ui, sans-serif', size: 12, color: '#0f1923' },
      xaxis: {
        type: 'date',
        range: [xStartFinal, xEndFinal],
        gridcolor: '#e2e8f0',
        zeroline: false,
      },
      yaxis: {
        range: [0, yMax],
        gridcolor: '#e2e8f0',
        zeroline: false,
        ticksuffix: '%',
      },
      legend: { orientation: 'h', y: -0.15, font: { size: 11 } },
      shapes: shapes,
      annotations: annotations,
      hovermode: 'closest',
    };

    var config = { responsive: true, displaylogo: false, displayModeBar: false, doubleClick: 'reset' };
    var div = $('ac-chart');
    if (!state.chartReady) {
      Plotly.newPlot(div, traces, layout, config);
      state.chartReady = true;
    } else {
      Plotly.react(div, traces, layout, config);
    }

    updateResults(resultA);
    updateResultsB(resultB);
    updateVsPill(resultA, resultB);
    renderObservationLog();
  }

  function drawEmpty() {
    var div = $('ac-chart');
    if (!div) return;
    Plotly.react(div, [], {
      margin: { l: 50, r: 20, t: 30, b: 40 },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      xaxis: { visible: false }, yaxis: { visible: false },
      annotations: [{
        text: 'Select at least one reference', showarrow: false,
        xref: 'paper', yref: 'paper', x: 0.5, y: 0.5,
        font: { size: 14, color: '#94A3B8' },
      }],
    }, { responsive: true, displaylogo: false, displayModeBar: false });
    state.chartReady = true;
  }

  function barrierTrace(xRange, levelPct, name, color) {
    return {
      type: 'scatter', mode: 'lines',
      x: xRange, y: [levelPct, levelPct],
      name: name + ' (' + levelPct + '%)',
      line: { color: color, width: 1.5, dash: 'dash' },
      hoverinfo: 'name',
      showlegend: true,
    };
  }

  function barrierTraceB(xRange, levelPct, name, color) {
    return {
      type: 'scatter', mode: 'lines',
      x: xRange, y: [levelPct, levelPct],
      name: name + ' (' + levelPct + '%)',
      line: { color: color, width: 1, dash: 'dot' },
      opacity: 0.6,
      hoverinfo: 'name',
      showlegend: true,
    };
  }

  function vline(iso, color) {
    return {
      type: 'line', xref: 'x', yref: 'paper',
      x0: iso, x1: iso, y0: 0, y1: 1,
      line: { color: color, width: 1, dash: 'dot' },
    };
  }

  function vAnno(iso, yMax, text, isB) {
    return {
      x: iso, y: yMax, xref: 'x', yref: 'y',
      text: text, showarrow: false,
      font: { size: 10, color: isB ? '#64748b' : '#475569' },
      align: 'center', yanchor: 'bottom',
      bgcolor: 'rgba(255,255,255,0.8)',
    };
  }

  function pickWorstRef(refs, result, initialLevels, xEndISO) {
    if (refs.length === 1) return refs[0];
    var anchor = (result && result.outcome_date) || xEndISO;
    var worst = null;
    var worstPerf = Infinity;
    refs.forEach(function (r) {
      var init = initialLevels[r];
      if (!init) return;
      var lvl = state.store.locf(r, anchor);
      if (lvl == null) return;
      var p = lvl / init;
      if (p < worstPerf) { worstPerf = p; worst = r; }
    });
    return worst;
  }

  function buildEventMarkers(result, params, suffix) {
    var s = suffix || '';
    var paid = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Coupon Paid' + s,
      marker: { color: '#16a34a', size: 10, symbol: 'triangle-up', line: { width: 1, color: '#15803d' } },
      hovertemplate: '%{x}<br>Coupon Paid' + s + ': %{y:.2f}%<extra></extra>' };
    var missed = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Coupon Missed' + s,
      marker: { color: '#eab308', size: 10, symbol: 'triangle-down', line: { width: 1, color: '#a16207' } },
      hovertemplate: '%{x}<br>Coupon Missed' + s + ': %{y:.2f}%<extra></extra>' };
    var memory = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Memory Catchup' + s,
      marker: { color: '#9333ea', size: 11, symbol: 'circle-open', line: { width: 2 } },
      hovertemplate: '%{x}<br>Memory Catchup' + s + ': %{y:.2f}%<extra></extra>' };
    var autocall = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Autocall' + s,
      marker: { color: '#2563eb', size: 13, symbol: 'diamond', line: { width: 1, color: '#1e40af' } },
      hovertemplate: '%{x}<br>%{text}<extra></extra>', text: [] };
    var matured = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Matured Above' + s,
      marker: { color: '#16a34a', size: 13, symbol: 'diamond', line: { width: 1, color: '#15803d' } },
      hovertemplate: '%{x}<br>%{text}<extra></extra>', text: [] };
    var breach = { type: 'scatter', mode: 'markers', x: [], y: [], name: 'Matured Below' + s,
      marker: { color: '#dc2626', size: 13, symbol: 'square', line: { width: 1, color: '#991b1b' } },
      hovertemplate: '%{x}<br>Matured Below' + s + '<extra></extra>' };

    (result.observations || []).forEach(function (o) {
      var y = o.worst_perf * 100;
      switch (o.status) {
        case ObsStatus.COUPON_PAID:
          paid.x.push(o.obs_date); paid.y.push(y); break;
        case ObsStatus.COUPON_MISSED:
          missed.x.push(o.obs_date); missed.y.push(y); break;
        case ObsStatus.MEMORY_CATCHUP:
          memory.x.push(o.obs_date); memory.y.push(y); break;
        case ObsStatus.AUTOCALL:
          autocall.x.push(o.obs_date); autocall.y.push(y);
          autocall.text.push('Autocalled' + s); break;
        case ObsStatus.MATURITY_ABOVE:
          matured.x.push(o.obs_date); matured.y.push(y);
          matured.text.push('Matured Above' + s); break;
        case ObsStatus.MATURITY_BELOW:
          breach.x.push(o.obs_date); breach.y.push(y); break;
      }
    });
    return { paid: paid, missed: missed, memory: memory, autocall: autocall, matured: matured, breach: breach };
  }

  // ---- Results panel -----------------------------------------------------

  function updateResults(result) {
    setResultsBlock({
      outcome: 'ac-outcome',
      date: 'ac-outcome-date',
      paid: 'ac-kpi-paid',
      paidPct: 'ac-kpi-paid-pct',
      missed: 'ac-kpi-missed',
      principal: 'ac-kpi-principal',
      total: 'ac-kpi-total',
      ann: 'ac-kpi-annualized',
      obs: 'ac-kpi-obs',
    }, result);
  }

  function updateResultsB(result) {
    setResultsBlock({
      outcome: 'ac-b-outcome',
      date: 'ac-b-outcome-date',
      paid: 'ac-b-kpi-paid',
      paidPct: 'ac-b-kpi-paid-pct',
      missed: 'ac-b-kpi-missed',
      principal: 'ac-b-kpi-principal',
      total: 'ac-b-kpi-total',
      ann: 'ac-b-kpi-annualized',
      obs: 'ac-b-kpi-obs',
    }, result);
  }

  function setResultsBlock(ids, result) {
    var outcomeEl = $(ids.outcome);
    var dateEl = $(ids.date);
    if (!outcomeEl) return;
    if (!result) {
      outcomeEl.textContent = '--';
      outcomeEl.className = 'ac-status-value';
      dateEl.textContent = '--';
      $(ids.paid).textContent = '0';
      $(ids.paidPct).textContent = '0.00%';
      $(ids.missed).textContent = '0';
      $(ids.principal).textContent = '100.00%';
      $(ids.total).textContent = '0.00%';
      $(ids.ann).textContent = '0.00%';
      $(ids.obs).textContent = '0';
      return;
    }
    outcomeEl.textContent = outcomeLabel(result.outcome);
    outcomeEl.className = 'ac-status-value ' + outcomeKlass(result.outcome);
    dateEl.textContent = result.outcome_date || '--';
    $(ids.paid).textContent = String(result.n_coupons_paid || 0);
    $(ids.paidPct).textContent = fmtPct(result.coupons_paid_pct);
    $(ids.missed).textContent = String(result.n_coupons_missed || 0);
    $(ids.principal).textContent = fmtPct(result.final_principal_pct);
    $(ids.total).textContent = fmtPct(result.total_return_pct);
    $(ids.ann).textContent = fmtPct(result.annualized_return_pct);
    $(ids.obs).textContent = String((result.observations || []).length);
  }

  function updateVsPill(a, b) {
    var pill = $('ac-vs-pill');
    if (!pill) return;
    if (!state.compareOn || !a || !b ||
        a.total_return_pct == null || b.total_return_pct == null) {
      pill.style.display = 'none';
      return;
    }
    var diff = (a.total_return_pct || 0) - (b.total_return_pct || 0);
    var sign = diff >= 0 ? '+' : '';
    var klass = diff >= 0 ? 'ac-vs-up' : 'ac-vs-down';
    pill.className = 'ac-vs-pill ' + klass;
    pill.textContent = 'A vs B (total return): ' + sign + diff.toFixed(2) + '%';
    pill.style.display = '';
  }

  function outcomeLabel(o) {
    switch (o) {
      case Outcome.AUTOCALLED: return 'CALLED EARLY';
      case Outcome.MATURED_ABOVE: return 'MATURED — FULL PRINCIPAL';
      case Outcome.MATURED_BELOW: return 'MATURED — CAPITAL LOSS';
      case Outcome.IN_PROGRESS: return 'IN PROGRESS';
      case Outcome.INVALID: return 'INVALID';
      default: return '--';
    }
  }

  function outcomeShortLabel(o) {
    switch (o) {
      case Outcome.AUTOCALLED: return 'Autocall';
      case Outcome.MATURED_ABOVE: return 'Matured';
      case Outcome.MATURED_BELOW: return 'Breach';
      case Outcome.IN_PROGRESS: return 'In Progress';
      default: return '';
    }
  }

  function outcomeKlass(o) {
    if (o === Outcome.AUTOCALLED || o === Outcome.MATURED_ABOVE) return 'ok';
    if (o === Outcome.IN_PROGRESS) return 'warn';
    if (o === Outcome.MATURED_BELOW || o === Outcome.INVALID) return 'bad';
    return '';
  }

  function fmtPct(n) {
    if (n == null || !isFinite(n)) return '--';
    return n.toFixed(2) + '%';
  }

  // ---- Observation log table --------------------------------------------

  function buildLogRows() {
    var rows = [];
    var a = state.lastResultA;
    if (a && a.observations && a.observations.length) {
      a.observations.forEach(function (o) {
        rows.push({
          k: o.k,
          obs_date: o.obs_date,
          scenario: 'A',
          worst_perf: (o.worst_perf == null) ? null : o.worst_perf * 100,
          status: o.status,
          coupon_paid_pct: o.coupon_paid_pct,
          memory_bank_pct: o.memory_bank_pct,
        });
      });
    }
    if (state.compareOn) {
      var b = state.lastResultB;
      if (b && b.observations && b.observations.length) {
        b.observations.forEach(function (o) {
          rows.push({
            k: o.k,
            obs_date: o.obs_date,
            scenario: 'B',
            worst_perf: (o.worst_perf == null) ? null : o.worst_perf * 100,
            status: o.status,
            coupon_paid_pct: o.coupon_paid_pct,
            memory_bank_pct: o.memory_bank_pct,
          });
        });
      }
    }
    return rows;
  }

  function renderObservationLog() {
    var tbody = $('ac-log-tbody');
    if (!tbody) return;
    // Update perf column label: single ref -> "Performance %"; multi -> "Worst-of perf %"
    var perfLabel = $('ac-log-perf-label');
    if (perfLabel) {
      var refsAll = state.refs.filter(function (r) { return !!r; });
      perfLabel.textContent = refsAll.length > 1 ? 'Worst-of perf %' : 'Performance %';
    }
    // Hide memory-bank column unless memory is on for either scenario.
    var memOn = !!($('ac-memory') && $('ac-memory').checked);
    var memOnB = !!(state.compareOn && $('ac-b-memory') && $('ac-b-memory').checked);
    var showMem = memOn || memOnB;
    document.body.classList.toggle('ac-hide-memory', !showMem);
    var rows = buildLogRows();

    // Sort
    var s = state.logSort;
    rows.sort(function (a, b) {
      var av = a[s.key];
      var bv = b[s.key];
      var cmp;
      if (av == null && bv == null) cmp = 0;
      else if (av == null) cmp = 1;
      else if (bv == null) cmp = -1;
      else if (s.type === 'num') cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      return s.dir === 'asc' ? cmp : -cmp;
    });

    // Toggle scenario column visibility on the header
    var scenTh = document.querySelector('#ac-log-table th[data-sort="scenario"]');
    if (scenTh) scenTh.style.display = state.compareOn ? '' : 'none';

    var html = '';
    rows.forEach(function (r) {
      var perfTxt = (r.worst_perf == null) ? '--' : r.worst_perf.toFixed(2) + '%';
      var statusLbl = STATUS_LABELS[r.status] || (r.status || '--');
      var chip = '<span class="ac-chip ac-chip-' + (r.status || 'na').replace(/_/g, '-') + '">' + escapeHtml(statusLbl) + '</span>';
      var coupon = (r.coupon_paid_pct == null) ? '--' : fmtPct(r.coupon_paid_pct);
      var mem = (r.memory_bank_pct == null) ? '--' : fmtPct(r.memory_bank_pct);
      var scenCell = state.compareOn ? '<td>' + r.scenario + '</td>' : '';
      html += '<tr>' +
        '<td>' + r.k + '</td>' +
        '<td>' + escapeHtml(r.obs_date || '--') + '</td>' +
        scenCell +
        '<td class="ac-num">' + perfTxt + '</td>' +
        '<td>' + chip + '</td>' +
        '<td class="ac-num">' + coupon + '</td>' +
        '<td class="ac-num">' + mem + '</td>' +
        '</tr>';
    });
    if (!rows.length) {
      var colspan = state.compareOn ? 7 : 6;
      html = '<tr><td class="ac-log-empty" colspan="' + colspan + '">No observations.</td></tr>';
    }
    tbody.innerHTML = html;

    // Reflect sort state on header classes
    var headers = document.querySelectorAll('#ac-log-table thead th[data-sort]');
    headers.forEach(function (th) {
      th.classList.remove('ac-sort-asc', 'ac-sort-desc');
      if (th.getAttribute('data-sort') === s.key) {
        th.classList.add(s.dir === 'asc' ? 'ac-sort-asc' : 'ac-sort-desc');
      }
    });
  }

  function onSortClick(key, type) {
    if (state.logSort.key === key) {
      state.logSort.dir = state.logSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      state.logSort.key = key;
      state.logSort.type = type || 'str';
      state.logSort.dir = 'asc';
    }
    renderObservationLog();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function onCsvDownload() {
    var rows = buildLogRows();
    var headers = state.compareOn
      ? ['#', 'Date', 'Scenario', 'Worst-of perf %', 'Status', 'Coupon paid %', 'Memory bank %']
      : ['#', 'Date', 'Worst-of perf %', 'Status', 'Coupon paid %', 'Memory bank %'];
    var lines = [headers.join(',')];
    rows.forEach(function (r) {
      var fields = [
        r.k,
        r.obs_date || '',
        state.compareOn ? r.scenario : null,
        r.worst_perf == null ? '' : r.worst_perf.toFixed(4),
        r.status || '',
        r.coupon_paid_pct == null ? '' : Number(r.coupon_paid_pct).toFixed(4),
        r.memory_bank_pct == null ? '' : Number(r.memory_bank_pct).toFixed(4),
      ].filter(function (v) { return v !== null; });
      lines.push(fields.map(csvEscape).join(','));
    });
    var blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'autocall_observations.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function csvEscape(v) {
    var s = String(v == null ? '' : v);
    if (/[",\n]/.test(s)) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  // ---- Tabs --------------------------------------------------------------

  function setActiveTab(tab) {
    state.activeTab = tab;
    var tabs = document.querySelectorAll('.ac-tab');
    tabs.forEach(function (t) {
      var on = t.getAttribute('data-tab') === tab;
      t.classList.toggle('ac-tab-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    var panels = document.querySelectorAll('.ac-tab-panel');
    panels.forEach(function (p) {
      p.style.display = (p.getAttribute('data-tab-panel') === tab) ? '' : 'none';
    });
  }

  // ---- URL state sync ----------------------------------------------------

  function buildUrlState() {
    var params = readParams();
    var pad = readPad();
    var refs = state.refs.filter(function (r) { return !!r; });
    var s = {
      refs: refs.join(','),
      issue: state.issueDateISO || '',
      tenor: params.tenor_months,
      freq: params.obs_freq_months,
      coupon: params.coupon_rate_pa_pct,
      cb: params.coupon_barrier_pct,
      ab: params.ac_barrier_pct,
      pb: params.protection_barrier_pct,
      memory: params.memory ? 1 : 0,
      nc: params.no_call_months,
      padb: pad.before,
      pada: pad.after,
    };
    if (state.presentOn) s.present = 1;
    if (state.couponMode === 'suggested') s.cmode = 's';
    if (state.compareOn) {
      var refsB = state.refsB.filter(function (r) { return !!r; });
      var pB = readParamsB();
      s.cmp = 1;
      s.b_refs = refsB.join(',');
      s.b_issue = state.issueDateBISO || ($('ac-b-issue') && $('ac-b-issue').value) || '';
      s.b_tenor = pB.tenor_months;
      s.b_freq = pB.obs_freq_months;
      s.b_coupon = pB.coupon_rate_pa_pct;
      s.b_cb = pB.coupon_barrier_pct;
      s.b_ab = pB.ac_barrier_pct;
      s.b_pb = pB.protection_barrier_pct;
      s.b_memory = pB.memory ? 1 : 0;
      s.b_nc = pB.no_call_months;
    }
    return s;
  }

  function scheduleUrlWrite() {
    if (state.urlInit) return;
    if (state.urlTimer) clearTimeout(state.urlTimer);
    state.urlTimer = setTimeout(writeUrl, 150);
  }

  function writeUrl() {
    state.urlTimer = null;
    try {
      var s = buildUrlState();
      var qs = new URLSearchParams();
      Object.keys(s).forEach(function (k) {
        var v = s[k];
        if (v === '' || v == null) return;
        qs.set(k, String(v));
      });
      history.replaceState(null, '', '?' + qs.toString());
    } catch (e) {
      // history may be locked in some browsers; ignore.
    }
  }

  function decodeUrlState() {
    var qs;
    try { qs = new URLSearchParams(window.location.search); }
    catch (e) { return; }
    if (!qs || !qs.toString()) return;

    function setVal(id, v) {
      var el = $(id);
      if (el && v != null && v !== '') el.value = String(v);
    }
    function setChk(id, v) {
      var el = $(id);
      if (el) el.checked = (v === '1' || v === 'true');
    }

    var refs = qs.get('refs');
    if (refs != null) {
      var arr = refs.split(',').filter(Boolean);
      for (var i = 0; i < 5; i++) {
        var v = arr[i] || null;
        state.refs[i] = v;
        var sel = $('ac-ref-' + (i + 1));
        if (sel) sel.value = v || '';
      }
    }
    var issue = qs.get('issue');
    if (issue) state.issueDateISO = issue;

    setVal('ac-tenor', qs.get('tenor'));
    setVal('ac-obs-freq', qs.get('freq'));
    setVal('ac-coupon-rate', qs.get('coupon'));
    setVal('ac-coupon-barrier', qs.get('cb'));
    setVal('ac-ac-barrier', qs.get('ab'));
    setVal('ac-prot-barrier', qs.get('pb'));
    setVal('ac-no-call', qs.get('nc'));
    setVal('ac-pad-before', qs.get('padb'));
    setVal('ac-pad-after', qs.get('pada'));
    setChk('ac-memory', qs.get('memory'));

    if (qs.get('present') === '1') {
      state.presentOn = true;
      var presBtn = $('ac-present-toggle');
      if (presBtn) presBtn.classList.add('ac-tool-btn-active');
      document.body.classList.add('ac-present-on');
    }
    if (qs.get('cmode') === 's') {
      state.couponMode = 'suggested';
      var cmEl = $('ac-coupon-mode');
      if (cmEl) cmEl.checked = true;
      applyCouponMode();
    }

    if (qs.get('cmp') === '1') {
      // Toggle compare on without invoking onToggleCompare's mirror logic.
      state.compareOn = true;
      var cmpBtn = $('ac-compare-toggle');
      if (cmpBtn) cmpBtn.classList.add('ac-tool-btn-active');
      var panel = $('ac-scenario-b');
      if (panel) panel.style.display = '';
      var colB = document.querySelector('.ac-results-col-b');
      if (colB) colB.style.display = '';
      document.body.classList.add('ac-compare-on');

      var bRefs = qs.get('b_refs');
      if (bRefs != null) {
        var bArr = bRefs.split(',').filter(Boolean);
        state.refsB[0] = bArr[0] || null;
        var bSel = $('ac-b-ref-1');
        if (bSel) bSel.value = state.refsB[0] || '';
      }
      var bIssue = qs.get('b_issue');
      if (bIssue) {
        state.issueDateBISO = bIssue;
        setVal('ac-b-issue', bIssue);
      }
      setVal('ac-b-tenor', qs.get('b_tenor'));
      setVal('ac-b-obs-freq', qs.get('b_freq'));
      setVal('ac-b-coupon-rate', qs.get('b_coupon'));
      setVal('ac-b-coupon-barrier', qs.get('b_cb'));
      setVal('ac-b-ac-barrier', qs.get('b_ab'));
      setVal('ac-b-prot-barrier', qs.get('b_pb'));
      setVal('ac-b-no-call', qs.get('b_nc'));
      setChk('ac-b-memory', qs.get('b_memory'));
    }
  }

  function onCopyLink() {
    var url = window.location.href;
    var msg = $('ac-copy-link-msg');
    function flash(text) {
      if (!msg) return;
      msg.textContent = text;
      setTimeout(function () { msg.textContent = ''; }, 1800);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url)
        .then(function () { flash('Copied'); })
        .catch(function () { flash('Failed'); });
    } else {
      try {
        var ta = document.createElement('textarea');
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        flash('Copied');
      } catch (e) {
        flash('Failed');
      }
    }
  }

  // ---- Distribution sweep -----------------------------------------------

  function runSweep() {
    var refs = state.refs.filter(function (r) { return !!r; });
    if (!refs.length) {
      var errEl = $('ac-sweep-err');
      if (errEl) errEl.textContent = 'Select at least one reference first.';
      return;
    }
    var params = readParams();

    var btn = $('ac-sweep-run');
    var spin = $('ac-sweep-spinner');
    var errSlot = $('ac-sweep-err');
    var resultBox = $('ac-sweep-result');
    var cachedBadge = $('ac-sweep-cached');

    if (btn) btn.disabled = true;
    if (spin) spin.style.display = '';
    if (errSlot) errSlot.textContent = '';
    if (cachedBadge) cachedBadge.style.display = 'none';

    fetch('/notes/tools/autocall/sweep', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refs: refs, params: params, coupon_mode: state.couponMode }),
    })
      .then(function (r) {
        return r.text().then(function (txt) {
          var data;
          try { data = JSON.parse(txt); } catch (e) { data = null; }
          if (!r.ok) {
            var msg = (data && (data.detail || data.error || data.message)) || ('HTTP ' + r.status);
            throw new Error(msg);
          }
          return data;
        });
      })
      .then(function (data) {
        renderSweep(data);
        if (resultBox) resultBox.style.display = '';
        if (cachedBadge) cachedBadge.style.display = data && data.cached ? '' : 'none';
      })
      .catch(function (e) {
        if (errSlot) errSlot.textContent = e.message || String(e);
        if (resultBox) resultBox.style.display = 'none';
      })
      .then(function () {
        if (btn) btn.disabled = false;
        if (spin) spin.style.display = 'none';
      });
  }

  function renderSweep(data) {
    if (!data) return;
    var stats = data.stats || {};
    $('ac-sweep-n').textContent = String(data.sample_size != null ? data.sample_size : '--');
    $('ac-sweep-mean').textContent = fmtPct(stats.mean_total_return_pct);
    $('ac-sweep-median').textContent = fmtPct(stats.median_total_return_pct);
    $('ac-sweep-p10').textContent = fmtPct(stats.p10_total_return_pct);
    $('ac-sweep-p90').textContent = fmtPct(stats.p90_total_return_pct);
    $('ac-sweep-mean-ann').textContent = fmtPct(stats.mean_annualized_return_pct);

    // Coupon summary panel (only populated when coupon_mode='suggested')
    var summaryWrap = $('ac-coupon-summary');
    var summaryVals = $('ac-coupon-summary-vals');
    if (data.coupon_mode === 'suggested' && data.coupon_summary && summaryWrap && summaryVals) {
      var cs = data.coupon_summary;
      summaryVals.textContent =
        'min ' + cs.min_pa_pct.toFixed(2) + '%, ' +
        'p10 ' + cs.p10_pa_pct.toFixed(2) + '%, ' +
        'median ' + cs.median_pa_pct.toFixed(2) + '%, ' +
        'p90 ' + cs.p90_pa_pct.toFixed(2) + '%, ' +
        'max ' + cs.max_pa_pct.toFixed(2) + '%';
      summaryWrap.style.display = '';
    } else if (summaryWrap) {
      summaryWrap.style.display = 'none';
    }

    renderBucketBars(data.buckets || {});
    renderHistogram(data.histogram || {});
  }

  function renderBucketBars(buckets) {
    var wrap = $('ac-bucket-bars');
    if (!wrap) return;
    var keys = ['autocalled', 'matured_above', 'matured_below', 'in_progress'];
    var html = '';
    keys.forEach(function (k) {
      var v = Number(buckets[k] || 0);
      var pct = isFinite(v) ? v : 0;
      html += '<div class="ac-bucket-row">' +
        '<div class="ac-bucket-label">' + BUCKET_LABELS[k] + '</div>' +
        '<div class="ac-bucket-track">' +
          '<div class="ac-bucket-fill" style="width:' + Math.max(0, Math.min(100, pct)) + '%; background:' + BUCKET_COLORS[k] + ';"></div>' +
        '</div>' +
        '<div class="ac-bucket-value">' + pct.toFixed(1) + '%</div>' +
        '</div>';
    });
    wrap.innerHTML = html;
  }

  function renderHistogram(hist) {
    var div = $('ac-hist-chart');
    if (!div) return;
    var edges = hist.edges || [];
    var counts = hist.counts || [];
    if (!edges.length || !counts.length) {
      Plotly.react(div, [], {
        margin: { l: 50, r: 20, t: 20, b: 40 },
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        annotations: [{
          text: 'No histogram data', showarrow: false,
          xref: 'paper', yref: 'paper', x: 0.5, y: 0.5,
          font: { size: 12, color: '#94A3B8' },
        }],
        xaxis: { visible: false }, yaxis: { visible: false },
      }, { responsive: true, displaylogo: false, displayModeBar: false });
      return;
    }
    var centers = [];
    var widths = [];
    for (var i = 0; i < counts.length; i++) {
      var lo = edges[i];
      var hi = edges[i + 1];
      centers.push((lo + hi) / 2);
      widths.push(hi - lo);
    }
    var trace = {
      type: 'bar',
      x: centers,
      y: counts,
      width: widths,
      marker: { color: '#2563eb' },
      hovertemplate: '%{x:.2f}%%: %{y}<extra></extra>',
    };
    var layout = {
      margin: { l: 50, r: 20, t: 20, b: 40 },
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      font: { family: 'Inter, system-ui, sans-serif', size: 12, color: '#0f1923' },
      xaxis: { title: 'Total return %', gridcolor: '#e2e8f0', zeroline: false, ticksuffix: '%' },
      yaxis: { title: 'Count', gridcolor: '#e2e8f0', zeroline: false },
      bargap: 0.05,
    };
    Plotly.react(div, [trace], layout, { responsive: true, displaylogo: false, displayModeBar: false });
  }

  // ---- Misc --------------------------------------------------------------

  function shiftMonths(iso, months) {
    if (!months) return iso;
    return Engine.edate(iso, months);
  }

  global.AutocallChart = { init: init };
})(typeof window !== 'undefined' ? window : globalThis);
