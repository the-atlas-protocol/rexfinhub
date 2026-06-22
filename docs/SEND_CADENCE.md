# SEND_CADENCE — when each report goes out

> Recorded 2026-06-22 (Ryu). This codifies the **intended** send cadence, which until now
> lived only in Ryu's head — nothing in the system enforced or documented it. Sends are
> currently **manual / on-demand** via `/refreshdata send` (the daily/weekly/bloomberg/
> gate-open systemd timers are DISABLED; `gate-close` Mon–Fri 20:00 is the only safety).
> This doc is the source of truth for cadence until/unless it is automated.

## The cadence

| Report | Cadence | Bundle | Recipients (list_type) |
|---|---|---|---|
| **daily** | **Daily** (every market day) | `daily`, `all` | etfupdates@ (internal) |
| weekly | Weekly | `weekly`, `all` | etfupdates@ |
| li | Weekly | `weekly`, `all` | etfupdates@ |
| income | Weekly | `weekly`, `all` | etfupdates@ |
| flow | Weekly | `weekly`, `all` | etfupdates@ |
| autocall | Weekly | `autocall`, `all` | REX team + CAIS + RBC (external) |
| stock_recs (T-REX) | Weekly | `stock_recs`, `all` | REX team |
| portfolio_suite | Weekly | `portfolio_suite` | REX team |
| microsectors | Weekly | `microsectors`, `all` | REX team + BMO (external) |
| **blue_ocean** | **Monthly — 1st of the month** | `blue_ocean`, `all` | REX team + BMO (external) |

**Rule of thumb (Ryu's words):** *"blue_ocean is the 1st of the month. The rest is weekly,
outside of the Daily report"* (daily = daily).

## Current automation state (2026-06-22)

- **No automated send cadence exists.** `rexfinhub-daily.timer`, `gate-open.timer`,
  `bloomberg.timer` are **disabled**; there is **no** weekly or monthly send timer.
- The two Monday 07:00/07:05 crons (`weekly_file_launch`, `weekly_system_report`) only
  **build** li artifacts — they do not send.
- Sends happen when Ryu runs `/refreshdata send` (shadow to Ryu) or `/refreshdata send live`
  (real list). The send gate `config/.send_enabled` stays closed otherwise.

## known-gaps

- Cadence is documented here but **not enforced**. If we want it automated, add per-bundle
  send timers (daily; weekly bundle on a chosen weekday; blue_ocean on the 1st) that call
  `send_all.py --bundle <x> --send` behind the gate — a deliberate future step, gated on
  Ryu's go (it would make sends fire without a human in the loop).
- `blue_ocean` and `microsectors` now include **external BMO** recipients; `autocall`
  includes **CAIS/RBC**. A live send of those bundles leaves the building — confirm before
  every `send live`.
