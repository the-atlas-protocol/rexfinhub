/* Autocallable note simulator — JS port of webapp/services/autocall_engine.py.
 *
 * This MUST stay in lockstep with the Python engine. Server-side distribution
 * sweeps run in Python; client-side scrub runs in JS. The two must produce
 * identical NoteResult records for the same inputs.
 *
 * Exposed globals (no module system in this app):
 *   AutocallEngine.LevelStore
 *   AutocallEngine.simulate(refs, issueDateISO, params, store) -> result
 *   AutocallEngine.observationDates(issueDate, params)
 *   AutocallEngine.Outcome / ObsStatus enums
 */
(function (global) {
  'use strict';

  const Outcome = Object.freeze({
    AUTOCALLED: 'autocalled',
    MATURED_ABOVE: 'matured_above',
    MATURED_BELOW: 'matured_below',
    IN_PROGRESS: 'in_progress',
    INVALID: 'invalid',
  });

  const ObsStatus = Object.freeze({
    COUPON_PAID: 'coupon_paid',
    COUPON_MISSED: 'coupon_missed',
    MEMORY_CATCHUP: 'memory_catchup',
    AUTOCALL: 'autocall',
    MATURITY_ABOVE: 'maturity_above',
    MATURITY_BELOW: 'maturity_below',
  });

  const DEFAULT_PARAMS = Object.freeze({
    tenor_months: 60,
    obs_freq_months: 1,
    coupon_rate_pa_pct: 10.0,
    coupon_barrier_pct: 60.0,
    ac_barrier_pct: 100.0,
    protection_barrier_pct: 50.0,
    memory: false,
    no_call_months: 12,
  });

  // ---- Date helpers --------------------------------------------------------
  // We work in ISO date strings (YYYY-MM-DD) for portability, and use a tiny
  // helper to add months Excel-EDATE-style (clamp to month-end on overflow).

  function parseISO(s) {
    // 'YYYY-MM-DD' -> Date (local tz, midnight)
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  function toISO(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
  function daysBetween(a, b) {
    return Math.round((b - a) / 86400000);
  }
  function lastDayOfMonth(year, monthIdx /* 0-11 */) {
    return new Date(year, monthIdx + 1, 0).getDate();
  }
  function edate(startISO, months) {
    // Excel EDATE: preserve day-of-month, clamp to last day of target month.
    const s = parseISO(startISO);
    const targetYear = s.getFullYear();
    const targetMonth0 = s.getMonth() + months;
    // Normalize month overflow.
    const ny = targetYear + Math.floor(targetMonth0 / 12);
    const nm = ((targetMonth0 % 12) + 12) % 12;
    const day = Math.min(s.getDate(), lastDayOfMonth(ny, nm));
    return toISO(new Date(ny, nm, day));
  }

  // ---- LevelStore ----------------------------------------------------------
  // Holds, per ticker, parallel arrays of sorted ISO dates and float levels.
  // LOCF lookup is O(log n) binary search (bisect_right - 1).

  class LevelStore {
    constructor() {
      this._dates = new Map();   // ticker -> [iso, ...]
      this._levels = new Map();  // ticker -> [float, ...]
      this._maxDate = null;
      this._minDate = new Map(); // ticker -> iso
    }

    /**
     * Bootstrap shape:
     * {
     *   max_date: 'YYYY-MM-DD',
     *   tickers: {
     *     'SPX Index': { dates: ['YYYY-MM-DD', ...], levels: [3214.5, ...] },
     *     ...
     *   }
     * }
     * Dates within each ticker MUST be ascending.
     */
    loadBootstrap(boot) {
      this._maxDate = boot.max_date;
      for (const [ticker, payload] of Object.entries(boot.tickers || {})) {
        if (!payload.dates || !payload.levels) continue;
        this._dates.set(ticker, payload.dates);
        this._levels.set(ticker, payload.levels);
        if (payload.dates.length) {
          this._minDate.set(ticker, payload.dates[0]);
          if (this._maxDate === null || payload.dates[payload.dates.length - 1] > this._maxDate) {
            this._maxDate = payload.dates[payload.dates.length - 1];
          }
        }
      }
    }

    get maxDate() { return this._maxDate; }
    inception(ticker) { return this._minDate.get(ticker) || null; }
    tickers() { return Array.from(this._dates.keys()); }

    // bisect_right on ISO-sorted strings (lex == chronological for ISO).
    _bisectRight(arr, target) {
      let lo = 0, hi = arr.length;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (arr[mid] <= target) lo = mid + 1;
        else hi = mid;
      }
      return lo;
    }

    /** LOCF: last available level on or before `onOrBefore` (ISO). */
    locf(ticker, onOrBefore) {
      const ds = this._dates.get(ticker);
      if (!ds) return null;
      const idx = this._bisectRight(ds, onOrBefore) - 1;
      if (idx < 0) return null;
      return this._levels.get(ticker)[idx];
    }

    /** Series clipped to [from, to] inclusive — for chart plotting. */
    seriesBetween(ticker, fromISO, toISO_) {
      const ds = this._dates.get(ticker);
      if (!ds) return { dates: [], levels: [] };
      const lvls = this._levels.get(ticker);
      const lo = this._bisectRight(ds, fromISO === '' ? '0000-00-00' : prevDay(fromISO));
      const hi = this._bisectRight(ds, toISO_);
      return { dates: ds.slice(lo, hi), levels: lvls.slice(lo, hi) };
    }
  }

  function prevDay(iso) {
    const d = parseISO(iso);
    d.setDate(d.getDate() - 1);
    return toISO(d);
  }

  // ---- Observation date generator ----------------------------------------

  function observationDates(issueISO, params) {
    const total = Math.floor(params.tenor_months / params.obs_freq_months);
    const out = new Array(total);
    for (let k = 1; k <= total; k++) {
      out[k - 1] = edate(issueISO, k * params.obs_freq_months);
    }
    return out;
  }

  // ---- Simulator ----------------------------------------------------------

  function perPeriodCoupon(params) {
    return params.coupon_rate_pa_pct * params.obs_freq_months / 12.0;
  }

  function emptyResult(refs, issueISO, params, outcome, error) {
    return {
      refs, issue_date: issueISO, params, initial_levels: {},
      outcome, outcome_date: null, observations: [],
      coupons_paid_pct: 0, n_coupons_paid: 0, n_coupons_missed: 0,
      final_principal_pct: 100, error: error || null,
      total_return_pct: 0, annualized_return_pct: 0,
    };
  }

  function annualize(totalReturnPct, issueISO, outcomeISO) {
    if (!outcomeISO) return 0;
    const days = daysBetween(parseISO(issueISO), parseISO(outcomeISO));
    if (days <= 0) return 0;
    const years = days / 365.25;
    const gross = 1.0 + totalReturnPct / 100.0;
    if (gross <= 0) return -100;
    return (Math.pow(gross, 1.0 / years) - 1.0) * 100.0;
  }

  /**
   * Run the autocall simulation.
   * @param {string[]} refs           1..5 tickers, in `store`.
   * @param {string}   issueISO       'YYYY-MM-DD'
   * @param {object}   params         shape of DEFAULT_PARAMS
   * @param {LevelStore} store
   * @returns {object} result
   */
  function simulate(refs, issueISO, params, store) {
    if (!refs || !refs.length) {
      return emptyResult([], issueISO, params, Outcome.INVALID, 'At least one reference required.');
    }
    if (!store.maxDate) {
      return emptyResult(refs, issueISO, params, Outcome.INVALID, 'No price data loaded.');
    }
    if (issueISO > store.maxDate) {
      return emptyResult(refs, issueISO, params, Outcome.INVALID, 'Issue date past data end.');
    }

    const initial = {};
    for (const r of refs) {
      const lvl = store.locf(r, issueISO);
      if (lvl == null || lvl <= 0) {
        return emptyResult(refs, issueISO, params, Outcome.INVALID,
          `No price data for '${r}' on or before ${issueISO}.`);
      }
      initial[r] = lvl;
    }

    const couponPct = perPeriodCoupon(params);
    const acBarrier = params.ac_barrier_pct / 100;
    const cpnBarrier = params.coupon_barrier_pct / 100;
    const protBarrier = params.protection_barrier_pct / 100;
    const totalObs = Math.floor(params.tenor_months / params.obs_freq_months);
    const obsDates = observationDates(issueISO, params);

    let bank = 0.0;
    let coupons = 0.0;
    let nPaid = 0, nMissed = 0;
    const log = [];

    for (let i = 0; i < obsDates.length; i++) {
      const k = i + 1;
      const obs = obsDates[i];

      if (obs > store.maxDate) {
        // In-progress: stop without an outcome.
        const inprog = {
          refs, issue_date: issueISO, params, initial_levels: initial,
          outcome: Outcome.IN_PROGRESS, outcome_date: store.maxDate,
          observations: log, coupons_paid_pct: coupons,
          n_coupons_paid: nPaid, n_coupons_missed: nMissed,
          final_principal_pct: 100,
          error: null,
        };
        inprog.total_return_pct = inprog.final_principal_pct - 100 + inprog.coupons_paid_pct;
        inprog.annualized_return_pct = annualize(inprog.total_return_pct, issueISO, store.maxDate);
        return inprog;
      }

      // Per-ref level + perf at this obs.
      const levels_t = {}, perfs_t = {};
      let worst = Infinity;
      for (const r of refs) {
        const lvl = store.locf(r, obs);
        if (lvl == null) {
          return emptyResult(refs, issueISO, params, Outcome.INVALID,
            `Missing level for ${r} at ${obs}.`);
        }
        levels_t[r] = lvl;
        const p = lvl / initial[r];
        perfs_t[r] = p;
        if (p < worst) worst = p;
      }

      const monthsElapsed = k * params.obs_freq_months;
      const isFinal = (k === totalObs);

      // Autocall
      const canAutocall = monthsElapsed > params.no_call_months;
      if (canAutocall && worst >= acBarrier) {
        const paid = couponPct + (params.memory ? bank : 0);
        coupons += paid;
        nPaid += 1;
        log.push({
          k, obs_date: obs, levels: levels_t, perfs: perfs_t,
          worst_perf: worst, status: ObsStatus.AUTOCALL,
          coupon_paid_pct: paid, memory_bank_pct: 0,
        });
        const out = {
          refs, issue_date: issueISO, params, initial_levels: initial,
          outcome: Outcome.AUTOCALLED, outcome_date: obs,
          observations: log, coupons_paid_pct: coupons,
          n_coupons_paid: nPaid, n_coupons_missed: nMissed,
          final_principal_pct: 100, error: null,
        };
        out.total_return_pct = out.final_principal_pct - 100 + out.coupons_paid_pct;
        out.annualized_return_pct = annualize(out.total_return_pct, issueISO, obs);
        return out;
      }

      // Coupon pay/miss
      let paid = 0, status;
      if (worst >= cpnBarrier) {
        const extra = params.memory ? bank : 0;
        paid = couponPct + extra;
        status = (params.memory && extra > 0) ? ObsStatus.MEMORY_CATCHUP : ObsStatus.COUPON_PAID;
        bank = 0;
        coupons += paid;
        nPaid += 1;
      } else {
        paid = 0;
        status = ObsStatus.COUPON_MISSED;
        if (params.memory) bank += couponPct;
        nMissed += 1;
      }

      // Final maturity
      if (isFinal) {
        let principal, finalStatus, finalOutcome;
        if (worst >= protBarrier) {
          principal = 100; finalStatus = ObsStatus.MATURITY_ABOVE; finalOutcome = Outcome.MATURED_ABOVE;
        } else {
          principal = worst * 100; finalStatus = ObsStatus.MATURITY_BELOW; finalOutcome = Outcome.MATURED_BELOW;
        }
        log.push({
          k, obs_date: obs, levels: levels_t, perfs: perfs_t,
          worst_perf: worst, status: finalStatus,
          coupon_paid_pct: paid,
          memory_bank_pct: params.memory ? bank : 0,
        });
        const out = {
          refs, issue_date: issueISO, params, initial_levels: initial,
          outcome: finalOutcome, outcome_date: obs,
          observations: log, coupons_paid_pct: coupons,
          n_coupons_paid: nPaid, n_coupons_missed: nMissed,
          final_principal_pct: principal, error: null,
        };
        out.total_return_pct = out.final_principal_pct - 100 + out.coupons_paid_pct;
        out.annualized_return_pct = annualize(out.total_return_pct, issueISO, obs);
        return out;
      }

      log.push({
        k, obs_date: obs, levels: levels_t, perfs: perfs_t,
        worst_perf: worst, status,
        coupon_paid_pct: paid,
        memory_bank_pct: params.memory ? bank : 0,
      });
    }

    // unreachable
    return emptyResult(refs, issueISO, params, Outcome.INVALID, 'Unreachable.');
  }

  // ---- Suggested coupon (heuristic) --------------------------------------
  // Mirror of webapp/services/autocall_engine.py:suggest_coupon. NOT a real
  // product price — surface with disclaimer.

  function realizedVol(ticker, onOrBeforeISO, store, lookbackDays) {
    lookbackDays = lookbackDays || 252;
    const ds = store._dates.get(ticker);
    const lvls = store._levels.get(ticker);
    if (!ds) return null;
    const idx = store._bisectRight(ds, onOrBeforeISO) - 1;
    if (idx < lookbackDays) return null;
    const rets = [];
    for (let i = idx - lookbackDays + 1; i <= idx; i++) {
      const prev = lvls[i - 1], cur = lvls[i];
      if (prev > 0 && cur > 0) rets.push(Math.log(cur / prev));
    }
    if (rets.length < 50) return null;
    const n = rets.length;
    let m = 0; for (let i = 0; i < n; i++) m += rets[i]; m /= n;
    let v = 0; for (let i = 0; i < n; i++) { const d = rets[i] - m; v += d * d; }
    v /= Math.max(1, n - 1);
    return Math.sqrt(v) * Math.sqrt(252);
  }

  function suggestCoupon(refs, issueISO, params, store) {
    const vols = [];
    for (const r of refs) {
      const v = realizedVol(r, issueISO, store, 252);
      if (v != null) vols.push(v);
    }
    if (!vols.length) return null;
    const avgVol = vols.reduce((a, b) => a + b, 0) / vols.length;
    const cbDist = Math.max(0, 1 - params.coupon_barrier_pct / 100);
    const protDist = Math.max(0, 1 - params.protection_barrier_pct / 100);
    const memoryPremium = params.memory ? 1.0 : 0;
    const basketPremium = Math.max(0, refs.length - 1) * 0.5;
    const acDiscount = Math.max(0, (params.ac_barrier_pct - 100) / 100) * 5.0;
    const c = 3 + 40 * avgVol * cbDist + 5 * protDist + memoryPremium + basketPremium - acDiscount;
    return Math.max(2, Math.min(20, c));
  }

  global.AutocallEngine = {
    Outcome, ObsStatus, DEFAULT_PARAMS,
    LevelStore, simulate, observationDates,
    parseISO, toISO, edate,
    realizedVol, suggestCoupon,
  };
})(typeof window !== 'undefined' ? window : globalThis);
