# REXFINHUB STYLE GUIDE
**Owner:** Ryu El-Asmar | **Aesthetic:** Minimal Professional + Modern Finance | **Last updated:** 2026-03-19

This file is the SINGLE SOURCE OF TRUTH for all visual decisions. Every agent creating mockups or implementing UI MUST read this file first and follow it exactly.

---

## Core Aesthetic

**Minimal Professional + Modern Finance.** Think Linear.app meets a Bloomberg web dashboard. Every element is intentional. If you can remove something and the page still works, remove it.

### What This Means
- Restrained color use — most of the page is grayscale, color is reserved for data and actions
- Clean typography creates hierarchy, not decorative elements
- Cards and containers are subtle — thin borders, no shadows, no gradients
- Data speaks for itself — don't wrap it in excessive chrome
- Whitespace is a design element, not wasted space
- Every component must look like it belongs on the same site

### What This Does NOT Mean
- Not "wireframe" — the page needs structure (cards, borders, spacing)
- Not "stripped bare" — approved components (KPI cards, tables, verdict blocks) provide visual structure
- Not "dark terminal" — light mode is primary, clean and professional

---

## Approved Components

### KPI Cards (K-A: Border-Top Accent)
```css
background: #ffffff;
border: 1px solid #e5e7eb;
border-radius: 6px;
border-top: 3px solid [pillar-color];
padding: 20px 24px;
/* NO shadow */
```
- Number: 28px, 700 weight, #0f172a, tabular-nums
- Label: 11px, 600 weight, uppercase, #64748b, letter-spacing 0.04em
- Delta: 13px, 600 weight, #059669 (up) / #DC2626 (down)
- Pillar colors: blue #2563eb (market), green #059669 (filing), purple #7c3aed (ownership), amber #d97706 (notes)

### Inline KPIs (Dashboard variant)
For dashboard pages, KPIs can be inline (numbers in a row with vertical dividers):
- Number: 24px, 700 weight
- Label: 10px, uppercase, #94a3b8
- Divider: 1px solid #e5e7eb between items
- Optional context line: 11px, #94a3b8

### Tables (T-B: Modern Finance)
```css
/* Container */
border: 1px solid #e5e7eb;
border-radius: 6px;
overflow: hidden;
/* Header */
background: #ffffff;
font-size: 12px;
font-weight: 600;
color: #374151;
text-transform: uppercase;
letter-spacing: 0.04em;
border-bottom: 2px solid #e5e7eb;
/* Rows */
font-size: 13px;
height: 40px;
border-bottom: 1px solid #f1f5f9;
/* Hover */
background: #f8fafc;
/* Numbers */
text-align: right;
font-variant-numeric: tabular-nums;
```
- Status pills: green bg #ecfdf5 + text #059669 (Effective), amber bg #fffbeb + text #d97706 (Pending), red bg #fef2f2 + text #dc2626 (Delayed)
- Positive values: #059669 with ▲ prefix (fixed 12px-wide arrow container)
- Negative values: #DC2626 with ▼ prefix (fixed 12px-wide arrow container)
- NO zebra striping
- Sticky headers

### Verdict Blocks
```css
background: #ffffff;
border-left: 2px solid #2563eb;
padding: 16px 20px;
font-size: 14px;
color: #334155;
line-height: 1.6;
```
- Label above: 10px, uppercase, #94a3b8, letter-spacing 0.06em (e.g., "TODAY'S BRIEF", "MARKET SUMMARY")
- Content: real data in sentences, not just numbers

### Cards (General)
```
background: #ffffff;
border: 1px solid #e5e7eb;
border-radius: 6px;
padding: varies (16-24px);
/* NO shadow */
/* NO hover translateY */
```
- Hover: border-color changes to #2563eb, subtle box-shadow 0 2px 8px rgba(0,0,0,0.06)
- NO lift/translate animation on hover

### Mega-Menu (A3: Professional SVG Icons)
- Panel: 920px width, 12px radius, layered 4-stop shadow, white background
- Hover-triggered with 300ms close grace period
- 3 columns per pillar
- Each link: 20px SVG icon (Lucide-style, stroke-width 1.5) + title (14px/500) + description (12px/400 #64748b)
- Active: blue left border + #eff6ff background
- Coming soon: 50% opacity + violet "SOON" badge
- Positioned absolute under trigger, right-aligned for Ownership/Notes panels

### Footer (F1: 5-Column Dark Navy)
- Background: #0f1923
- 5 columns: Brand | Market | Filings | Ownership | Notes & Tools
- Headings: 11px, 600, uppercase, white
- Links: 13px, rgba(255,255,255,0.6), hover to white
- Bottom: copyright + data sources in rgba(255,255,255,0.35)

### Form/Filing Badges
- 485BPOS: green pill (#ecfdf5 bg, #059669 text)
- 485BXT: blue pill (#dbeafe bg, #1e40af text)
- 485APOS: orange pill (#fffbeb bg, #d97706 text)
- 497/497K/497J: gray pill (#f3f4f6 bg, #6b7280 text)

---

## Color Palette

### Primary
| Token | Hex | Usage |
|-------|-----|-------|
| text-primary | #0f172a | Headings, primary text |
| text-secondary | #374151 | Body text |
| text-muted | #64748b | Descriptions, secondary info |
| text-caption | #94a3b8 | Labels, captions, section headers |
| link | #2563eb | All clickable links and primary actions |
| page-bg | #f8fafc | Page background |
| card-bg | #ffffff | Cards, tables, panels |
| border | #e5e7eb | All borders |
| border-light | #f1f5f9 | Table row separators |
| divider | #e5e7eb | Section dividers |

### Data Colors
| Token | Hex | Usage |
|-------|-----|-------|
| positive | #059669 | Up arrows, gains, effective status |
| negative | #DC2626 | Down arrows, losses, delayed status |
| warning | #d97706 | Pending, caution |
| info | #2563eb | Links, active states |
| coming-soon | #7c3aed | SOON badges (violet) |

### Pillar Identity
| Pillar | Color | Usage |
|--------|-------|-------|
| Market | #2563eb | KPI top borders, dots |
| Filings | #059669 | KPI top borders, dots |
| Ownership | #7c3aed | KPI top borders, dots |
| Notes | #d97706 | KPI top borders, dots |

---

## Typography

| Element | Size | Weight | Color | Other |
|---------|------|--------|-------|-------|
| Page title | 20px | 600 | #0f172a | |
| Section title | 16px | 600 | #0f172a | |
| Section label | 11px | 600 | #94a3b8 | uppercase, letter-spacing 0.04em |
| Body text | 14px | 400 | #374151 | line-height 1.6 |
| Table header | 12px | 600 | #374151 | uppercase, letter-spacing 0.04em |
| Table cell | 13px | 400 | #0f172a | line-height 1.3 |
| KPI number | 28px | 700 | #0f172a | tabular-nums |
| KPI label | 11px | 600 | #64748b | uppercase, letter-spacing 0.04em |
| Link text | inherit | 500 | #2563eb | |
| Caption | 11px | 400 | #94a3b8 | |
| Badge | 11px | 500 | varies | pill: 4px radius, 2px 8px padding |

**Font:** Inter (Google Fonts, display=optional)
**Numbers:** Always use `font-variant-numeric: tabular-nums`
**Rendering:** `text-rendering: optimizeSpeed` on body, `optimizeLegibility` on headings only

---

## Spacing

Strict 8px grid: 4, 8, 12, 16, 24, 32, 48px.
- Card padding: 20px 24px
- Section gap: 32px
- Column gap: 24px
- Item margin: 8px
- Container: max-width 1280px, padding 0 32px

---

## REJECTED — Do NOT Use

| Element | Why |
|---------|-----|
| Gradients | Looks "vibe coded" |
| Heavy box-shadows | Not minimal |
| Emoji icons | Unprofessional |
| backdrop-filter blur | Looks blurry and cheap |
| Hover translateY / scale | Consumer fintech, not institutional |
| Zebra striping | Conflicts with semantic row colors |
| Dark theme | Not ready yet (future: VS Code Dark Modern #1e1e1e) |
| Click-only dropdowns | Frustrating — use hover with click fallback |
| Full-screen mega-menus | Block the page |
| Pure decorative color | Every color must encode meaning |
| AI-generated / "vibe coded" aesthetic | Must look designed by a real human |
| Cards without content | No empty containers just for visual balance |
| Rounded-everything (>8px radius) | Max radius: 12px on panels, 6px on cards, 4px on inputs |

---

## Layout Patterns

### Dashboard Pages (pillar landing pages)
1. Title + date (right-aligned)
2. KPIs (inline or K-A cards)
3. Verdict block (intelligence brief)
4. Main content (two-column or three-column)
5. Navigate links to deeper tools

### Data Pages (search, lists, detail)
1. Breadcrumb
2. Title + filters
3. Result count
4. T-B table with pagination
5. Cross-pillar context links

### Home Page (H-A: Clean Grid)
1. Title + subtitle
2. K-A KPI cards (4 in a row)
3. Verdict block (morning brief)
4. 2x2 pillar grid (cards with colored dots + section badges)
5. Data freshness line

---

## Process for UI Changes

1. **Read this file first** — every decision is already made
2. **Use approved components** — don't invent new ones
3. **Create mockups for NEW layouts** — save to `temp/` as HTML, open in Chrome
4. **Self-review** — take screenshots, compare to this guide, iterate before showing
5. **Implement once** — after mockup approval, build the real page
