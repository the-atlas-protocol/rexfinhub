# Sys-J: Email Rendering Cross-Client Audit — 2026-05-05

## TL;DR
- **Outlook**: PARTIAL — table layout OK, but `box-shadow`/`border-radius`/`overflow:hidden`/`display:inline-block` stripped; base64 chart `<img>` missing `height=`
- **Gmail**: GOOD — viewport set, table-only, no `<style>` blocks; minor risk from sub-11px font sizes
- **Mobile**: PARTIAL — viewport correct but L&I stock recs uses 700px container; 8px sublabels unreadable
- **CAN-SPAM**: **FAIL** — no physical address in any builder; unsubscribe is plain text not link
- **Top risk**: QuickChart.io external image dependency — silently blank in corporate Outlook (RBC, CAIS recipients)

## Per-Builder Risks

| Builder | Gmail | Outlook | Apple | Mobile | Top Risk |
|---|---|---|---|---|---|
| daily_brief | LOW | LOW | MED | LOW | Dark mode navy header inversion |
| morning_brief | LOW | LOW | MED | LOW | 8px KPI sublabels |
| L&I weekly | LOW | MED | MED | MED | Missing `height=` on base64 charts; QuickChart blocked |
| Income/CC | LOW | MED | MED | MED | Same |
| **Flow report** | LOW | **HIGH** | MED | **HIGH** | 6-8 QuickChart requests; all silently fail if proxy blocks |
| Autocall | LOW | LOW | MED | MED | 8px labels, CAN-SPAM missing |
| L&I stock recs | LOW | MED | MED | **HIGH** | 700px container forces phone scroll/zoom |

## Outlook-Specific Issues

1. `box-shadow` stripped — cosmetic only
2. `border-radius` stripped — square corners throughout
3. `overflow:hidden` ignored — content may visually overflow border
4. `display:inline-block` on `<span>` ignored — rank badges, legend swatches, verdict tags lose styling
5. **Missing `height=` on base64 `<img>` tags** at `report_emails.py:1322-1323` — Outlook may collapse charts to 0 height
6. **Zero `<!--[if mso]-->` conditional comments** — no Outlook fallback path

## Mobile Responsiveness

| Container | Phone result |
|---|---|
| 640px (builders 1-6) | ✓ scales correctly |
| **700px (L&I stock recs)** | ✗ horizontal scroll on iPhone, text becomes ~6px effective |

| Font size | Where used | Verdict |
|---|---|---|
| 8px | KPI sublabels | FAIL — invisible |
| 9px | Some sublabels | FAIL — mobile unreadable |
| 10px | Headers, footnotes | Marginal |
| 11px | Flow bar tickers | Below 14px mobile threshold |
| 12px | Table body cells | Gmail mobile auto-boosts → column distortion |
| 13px | Body text | Borderline |
| 14px | CTA button | PASS |

## CAN-SPAM / Accessibility (FAIL)

| Check | Status |
|---|---|
| Unsubscribe present | PARTIAL (plain text email address, not link) |
| Unsubscribe clickable | **FAIL** |
| Physical postal address | **FAIL** — required by CAN-SPAM §7 |
| Sender ID | PASS |
| Subject lines accurate | PASS |
| `<img alt>` on all images | PASS |
| No `<script>`/`<iframe>`/`<object>` | PASS |
| Semantic headings (`<h1>`/`<h2>`) | FAIL — all `<div>` (screen readers lose structure) |
| Color-only status | PARTIAL (most also use sign-prefixed text) |
| Dark mode forcing | FAIL — no `!important` backgrounds, no `color-scheme` meta |
| WCAG AA contrast | FAIL — `#636e72` on `#f8f9fa` = ~3.9:1 (fails AA for normal text) |

## QuickChart.io Risk Analysis

URL: `https://quickchart.io/chart?c=<encoded_JSON>...`

**Risk factors**:
- **Corporate proxy blocking** (HIGH for RBC/CAIS) — Proofpoint, Mimecast, Cisco IronPort may block external image URLs from unknown domains. Failure is silent.
- URL length (MEDIUM) — encoded chart JSON can hit 1,800-3,000 chars; some proxies enforce 2,083-char IE limit
- Image-loading disabled by default (MEDIUM) — Outlook Desktop, Apple Mail enterprise MDM, Gmail mobile in some configs
- QuickChart.io free tier — no SLA, single point of failure

Image counts per email:
- Daily/Morning brief: 0
- Autocall: 0-1
- **Flow: 7-8 QuickChart requests**
- L&I/Income: 1 QuickChart + 2 base64 PNGs

## Top 3 Rendering Risks per Client

### Outlook (desktop, Word engine)
1. QuickChart images blocked → blank rectangles for RBC/CAIS
2. Missing `height=` on base64 `<img>` → may collapse to 0 height
3. `display:inline-block` on `<span>` stripped → unstyled rank badges/legend

### Gmail
1. 8-12px font sizes trigger mobile font-boosting → column distortion
2. No dark mode override → navy header may invert
3. 102KB clip threshold → multi-base64 emails approach limit

### Mobile
1. 700px container in L&I stock recs → forces horizontal scroll on every phone
2. 8px-9px sublabels → unreadable
3. QuickChart images disabled by default in iOS Mail / Gmail mobile MDM

## Recommendations (prioritized)

1. **[HIGH] Add physical postal address to all footers** — CAN-SPAM §7 requirement. One-line change to `_wrap_email()` + brief footers.
2. **[HIGH] Convert unsubscribe to link** — `<a href="mailto:...?subject=Unsubscribe">Unsubscribe</a>`. Long-term: `/unsubscribe?token=` endpoint.
3. **[HIGH] QuickChart.io fallback for Outlook** — wrap with `<!--[if !mso]>...<![endif]-->` and provide simple HTML table fallback for `<!--[if mso]>`.
4. **[MEDIUM] Add `height=` on base64 `<img>` tags** at `report_emails.py:1322-1323`.
5. **[MEDIUM] Reduce L&I stock recs container** from 700px → 640px.
6. **[MEDIUM] Add `<meta name="color-scheme" content="light">`** to all `<head>` blocks.
7. **[LOW] Raise minimum font sizes**: 8/9px → 10px; 12px body → 13px.
8. **[LOW] Fix subject line fallback** for li/cc/flow/autocall/weekly editions.

---

*Audit by Sys-J bot, 2026-05-05. Read-only.*
