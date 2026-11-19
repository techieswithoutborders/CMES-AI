# Recommendation: split the TwB right pane into an Examples zone and an Answer zone

## Goal
After clicking Search, the answer + references in `#result` currently render below the full dev-set examples list in the same single scrolling column. Examples stay visible at the top and push the answer down. The right pane should visually separate dev-set options from the real search answer and references.

## Current state (index.html, single-file vanilla)
- `line 114`: `.right-pane{flex:1 1 auto;min-width:0;overflow:auto;background:var(--bg);padding:14px}` — one scrolling glass column.
- `lines 189–202`: inside `#tab-ask` — Ask card (textarea, mode radios, language, Search), then `#examples` (line 199, rendered from `/dev/examples` as "Try a dev-set question:" + category chips), then `#result` (line 201, where `renderResult()` injects answer, citations, source passages, feedback, timings).
- `#q`, `#go`, `#examples`, `#result`, `#histcard` IDs are all referenced by JS (lines 376–384, 514–517) and must stay valid. No JS behavior changes are required for a pure layout restructure.

## Options analyzed

### Option A — Vertical two-zone split (RECOMMENDED)
Restructure `#tab-ask` into a column of two zones inside the right pane:
- **Top zone (Ask + Examples):** Ask card + a separate Examples card ("Dev-set questions" with its own header).
- **Bottom zone (Answer):** `#result` only — answer text, citations, source passages, feedback, timings.

Each zone is its own scrolling region (`overflow:auto`, `min-height:0`, `overscroll-behavior:contain`), separated by a visible border/gap and a section label. The top zone sizes to content with a cap so a long examples list cannot push the answer off-screen; the answer zone gets `flex:1` to claim remaining height.

Pros:
- Clear physical separation: examples vs. answer are distinct regions with their own borders/backgrounds.
- Examples stay visible and clickable while the answer is shown — user can pick another chip without losing their place in the answer/references.
- Answer gets dedicated vertical space; long passage/citation lists scroll inside the answer zone.
- Thematic consistency with the app's existing left/right split metaphor (nested top/bottom split in the right pane).
- Zero JS changes; all existing IDs, event listeners, tab switching, history, theme, and pane-width localStorage behavior are preserved.
- WCAG AA: any new toggle reuses existing `--focus-ring`/`:focus-visible` styles; focus order follows DOM (ask → examples → answer).

Cons:
- Nested scroll areas (outer pane + two zones) — mitigate with `overscroll-behavior:contain` on zones.
- On short viewports the two zones compete for height; add a min-height breakpoint where zones stack normally and the pane scroller handles it.
- Slightly more CSS than Option C (still small, ~15–25 lines).

### Option B — Side-by-side grid inside the right pane
Make `.right-pane` a grid: examples column (~30–35%) | answer column (~65–70%), stacking to one column below a breakpoint (~700px).

Pros:
- Examples and answer visible simultaneously side by side; clear left/right separation.
- Answer gains horizontal width, helping wide tables/citations.

Cons:
- Answer content is inherently vertical (answer text → citations → stacked passage cards), so columns waste vertical space and still force vertical scrolling inside narrow columns.
- Example chips wrap awkwardly in a narrow column; category groups look cramped.
- Breaks the natural top-to-bottom flow (ask input/examples separated from Search output).
- On mobile/small panes it collapses to Option A anyway, adding complexity for marginal desktop gain.
- Would require moving the ask input into the examples column or duplicating it, complicating DOM and focus management.

### Option C — Collapsible examples + visual divider (minimal fallback)
Keep the single scrolling column; wrap `#examples` in its own card with a collapse toggle (auto-collapse after first Search or manual), give `#result` a distinct card/tint and a separator bar above it.

Pros:
- Smallest change (mostly CSS, one toggle button); preserves existing scroll semantics exactly; works at all sizes with no breakpoints.
- Clear visual separation via borders/background/gap.

Cons:
- Examples are hidden while the answer is shown unless the user re-expands them — defeats keeping dev-set options handy.
- Answer still shares one scroll zone with examples; an expanded examples list still pushes content down.
- Less decisive separation than physical zones.

## Selected option: Option A (vertical two-zone split)
Best satisfies the goal — clear visual separation between dev-set options and the real search answer + references — while keeping examples accessible and giving the answer dedicated room. Option C is the simpler fallback if nested scrolling proves undesirable in testing.

## Specific changes needed

### CSS (add after the `.right-pane` rule at line 114)
Leave `.right-pane` itself untouched (preserves existing scroll behavior and all other tabs). Add an internal flex wrapper and two zones:

```css
/* right-pane internal split: examples zone | answer zone */
#ask-deck{display:flex;flex-direction:column;min-height:0;gap:10px}
.ask-zone{min-height:0;overflow:auto;overscroll-behavior:contain}
.ask-zone .card{margin:0}                 /* gap replaces inter-card margin */
#examples{max-height:220px;overflow:auto}/* cap examples so they can't dominate */
.answer-zone{min-height:0;flex:1 1 auto;overflow:auto;overscroll-behavior:contain;
  border-top:1px solid var(--line);padding-top:10px;margin-top:6px}
.answer-zone::before{
  content:"Search results";display:block;font-size:12px;color:var(--mute);
  margin-bottom:6px;letter-spacing:.4px;text-transform:uppercase}
```

- Borders use `var(--line)`/`var(--card-border)`, which already adapt to light/dark/system themes — no new theme variables needed.
- If adding a collapse toggle for examples: `.zone-toggle:focus-visible{outline:var(--focus-ring);outline-offset:var(--focus-offset)}` (reuses existing WCAG AA focus style).
- Optional draggable horizontal divider (mirroring the main splitter): `role="separator" aria-orientation="horizontal" tabindex="0"` with ArrowUp/Down keyboard handling and brand hover color — recommended as a later enhancement, not for the initial ship.

### HTML (inside `<main class="right-pane">`, `#tab-ask` section, lines 189–202)
Restructure to:

```html
<section id="tab-ask" class="on">
  <div id="ask-deck">
    <div class="ask-zone">
      <div class="card">
        <h2>Ask a question</h2>
        <textarea id="q" ...></textarea>
        <div class="row"> ...lang, modes, Search... </div>
      </div>
      <div class="card" id="examples-card">
        <h3>Dev-set questions</h3>
        <div id="examples" class="small"></div>
      </div>
    </div>
    <div class="answer-zone">
      <div id="result"></div>
    </div>
  </div>
  <div class="card" id="histcard" style="display:none"><h2>Recent questions ...</h2><div id="hist"></div></div>
</section>
```

- `#examples` moves out of the ask card into its own card; the Ask card loses the examples div.
- IDs unchanged: `#q`, `#go`, `#examples`, `#result`, `#histcard` — all JS (`ask()`, `renderResult()`, examples fetch at line 516, chip click handlers) continues to work without modification.
- `renderResult()` output still targets `#result`, now inside `.answer-zone`.

### What does NOT change
- `.right-pane` padding/background/theme variables; `.card`, `.chip`, `.answer`, `.tblwrap`, `.banner` styles.
- Tab switching, history card, localStorage theme/pane width, left/right splitter.
- All JavaScript.

### Open risk to verify
Nested scrolling: outer pane `overflow:auto` plus two inner `overflow:auto` zones can chain scrolls. `overscroll-behavior:contain` on both zones is the mitigation; verify Tab order and that a long answer scrolls within the answer zone without moving the examples zone. If testing shows cramped/nested-scroll UX problems, fall back to Option C.