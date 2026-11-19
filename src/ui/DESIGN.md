# UI Design Specification — Split Pane Glass Theme

**Project:** twb — Offline Medical Q&A Console  
**Branch:** `uichanges`  
**Location:** `ui/index.html` (single-file vanilla JS/CSS, no framework)  
**Theme:** Apple-inspired glassmorphism (gloss + glass color palette)

---

## 1. Layout — Left Pane / Right Pane

```
┌─────────────────────────────────────────────────────────────┐
│  Header (sticky) — pack status, connection, disclaimer      │
├──────┬──────────────────────────────────────────────────────┤
│      │                                                       │
│ Left │  Right Pane (flex-1, scrollable)                     │
│ Pane │                                                       │
│ ~280 │  • Ask: question input, mode, examples, results       │
│ px   │  • Pipeline: run pipeline, stage log, pack status     │
│      │  • Eval: gates table, blockers, golden set info       │
│      │  • Update: device token, apply/rollback               │
│      │  • Settings: theme toggle, backend config             │
│      │  • About: version, safety notice, credits             │
│      │                                                       │
├──────┼──────────────────────────────────────────────────────┤
│ Splitter 8px (grab bar, draggable, keyboard-navigable)     │
│ Range: 200px – 800px | Default 280px | Collapsible <900px │
└──────┴──────────────────────────────────────────────────────┘
```

**Left pane** — navigation menu only. Fixed width, glass background, scrollable if needed. Contains:
- App logo/title (compact)
- Navigation list: Ask, Pipeline, Eval, Update, Settings, About
- Each item: icon glyph + label + active state
- Collapse toggle button (chevron)

**Right pane** — content area. Flex-1, scrollable. Each nav item maps to a section:
- Ask, Pipeline, Eval, Update (existing, preserved)
- Settings (new: theme, backend, safety toggle)
- About (new: version, safety notice, medical disclaimer)

**Splitter** — 8px vertical grab bar between panes:
- Draggable horizontally (mouse + touch)
- Keyboard: ArrowLeft/ArrowRight adjust width
- Min 200px / max 800px
- Visual hover state (wider on hover)
- Double-click to reset to default width
- `data-collapsed` attribute when left pane hidden (<900px)

---

## 2. Theme — Apple Glass / Gloss Color System

### CSS Custom Properties (light mode)

```css
--glass-bg:    rgba(255, 255, 255, 0.82);   /* semi-transparent base */
--glass-border: rgba(255, 255, 255, 0.55);  /* subtle border */
--glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);  /* soft depth */
--brand:       #0a84ff;   /* Apple blue */
--brand-glass: rgba(10, 132, 255, 0.18);   /* tinted glass accent */
--ink:         #1d1d1f;   /* near-black text */
--mute:        #8e8e93;   /* secondary text */
--bg:          #f5f5f7;   /* system background */
--ok:          #30d158;   /* green */
--warn:        #ff9f0a;   /* amber */
--bad:         #ff453a;   /* red */
--hl:          #fff9c4;   /* highlight */
```

### Dark mode (prefers-color-scheme or toggle)

```css
--glass-bg:    rgba(28, 28, 30, 0.82);
--glass-border: rgba(255, 255, 255, 0.12);
--glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.30);
--brand:       #0a84ff;
--ink:         #f5f5f7;
--mute:        #8e8e93;
--bg:          #000000;
```

### Visual treatment

- **Backdrop blur:** `backdrop-filter: blur(20px) saturate(180%)` on all glass surfaces
- **Border:** 1px hairline `var(--glass-border)` on card edges
- **Depth:** subtle shadow + 12px border-radius on cards
- **Active nav item:** brand-colored glass fill (`var(--brand-glass)`) + brand text
- **Inactive nav item:** transparent glass, mute text
- **Buttons:** brand fill with white text (primary), glass fill with brand text (secondary)
- **Focus ring:** 2px brand outline, 2px offset (WCAG AA)

---

## 3. Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| ≥901px | Split pane visible, left pane 200–800px, splitter draggable |
| ≤900px | Left pane collapses (hidden), hamburger toggle appears, right pane full width |
| Mobile (<480px) | Stacked sections within right pane, compact header |

- Splitter hidden at ≤900px
- Pane width persisted in `localStorage` (`twb_pane_width`)
- Collapse state persisted (`twb_pane_collapsed`)

---

## 4. Accessibility

- Splitter: `role="separator"`, `aria-orientation="vertical"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax`, `tabindex="0"`, keyboard (ArrowLeft/Right/Home/End)
- Nav: `role="navigation"`, `aria-label="Main menu"`, each item `role="button"` with `aria-pressed` for active
- Skip link to content
- Contrast: WCAG AA (4.5:1 normal, 3:1 large) on all text/background combos
- Reduced motion: respect `prefers-reduced-motion` (disable drag animations)

---

## 5. Functionality Preserved (from original ui/index.html)

| Feature | Status |
|---------|--------|
| Ask (question input, mode select, language, search button) | Preserved |
| Passages-only mode | Preserved |
| Grounded summary mode (disabled without NIM) | Preserved |
| Streaming answer with citations | Preserved |
| Table rendering with highlighted cells | Preserved |
| Source page viewer modal | Preserved |
| Recent questions history (localStorage) | Preserved |
| Pipeline run + stage log | Preserved |
| Eval gates table + blockers | Preserved |
| Update (device token, apply, rollback) | Preserved |
| Dev status polling (15s) | Preserved |
| Examples chips | Preserved |
| Feedback buttons (👍/👎 + category) | Preserved |

---

## 6. New Sections

| Section | Purpose |
|---------|---------|
| Settings | Theme toggle (light/dark/system), backend status, NVIDIA API key hint, safety mode toggle |
| About | Pack version, signature status, medical disclaimer, safety notice link, credits |

---

## 7. Implementation Notes

- Single file: `ui/index.html` — CSS in `<style>`, JS in `<script>`, HTML in `<body>`
- No external dependencies (no framework, no CSS library)
- Same API endpoints as before: `/health`, `/version`, `/search`, `/answer`, `/doc/`, `/feedback`, `/dev/*`, `/update/*`
- Glass effects use `backdrop-filter` (supported in modern browsers; graceful fallback to solid bg)
