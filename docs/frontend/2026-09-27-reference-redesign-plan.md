# Reference dashboard redesign — plan review

## VERDICT: APPROVED

Reviewer: Codex lead-architect (Astra), same system as the implementation team.
This verdict approves the amended execution plan below after the orchestrator adopted its named design gates, real composer request behavior, preserved context, airport-labeled comparison metrics and omission of unreturned SFO metrics. It is plan approval, not implementation or runtime approval.

capability_achieved: static
commands_run: yes — source inspection and `git status --short`, `git rev-parse HEAD`, `git rev-list --count 'HEAD..@{u}'` in `/Volumes/externalHDD/products/deloitte`; HEAD `57dd0117fb8c012d4a772f5180423a833b3b1cee`, behind 0 at inspection. The checkout has substantial uncommitted frontend and backend work; HEAD alone does not identify those bytes.

### Issues Found

### Adopted execution contract

The named audit chain below is part of final acceptance. The composer submits through the existing endpoint and renders safe `ai_unavailable` responses under default backend settings, without changing model admission or claiming live AI capability. The builder implements sequential shell/style/render substeps in at most two files at a time. The 150-changed-line limit applies to each dispatch slice, not to the completed stylesheet: subdivide larger work into independently checked token, layout, control and responsive chunks.

[Atomicity self-check: PASS — one frontend builder per sequential dispatch slice; at most two files; one verified outcome; local reversible tier; at most 150 changed lines per slice through subdivision.]

### Sources and precedence

Read the supplied implementation brief at `/Users/bar_bettash/.codex/attachments/85523b4e-3a16-4587-a47d-700f91147143/pasted-text-1.txt` and inspected its adjacent `image-1.png`. The brief governs over illustration. Reviewed README architecture/scope, `docs/API_UI_MAP.md`, contracts and frontend implementation. Existing token source is `backend/app/static/styles.css`; replace its presentation palette with the explicitly supplied navy/cream values. Keep the current vanilla HTML/JS and globe renderer. No assets, backend, sources, data, contracts, dependencies, deployment or publication changes.

### Architecture and decisions

Assumptions: this is the existing single-page loopback prototype; four structured demonstrations remain authoritative; backend work proceeds independently. Static assets being under `backend/` does not authorize Python changes. Re-read the relevant contract if the backend owner changes it during the work.

Accepted: 50px contact-only header; approximately 58/42 desktop composition; borderless enlarged Earth; four prominent initial prompts; result dashboard with at most four relevant KPI groups and two charts; collapsed evidence/source/methodology disclosures; optional bottom composer. Retain structured scope editing in a secondary disclosure so disabled AI never removes the supported analysis path.

Rejected alternatives: replacing the renderer, a framework/chart dependency, reproducing mockup numbers/global coverage, frontend analytics, or separate APIs per card. Existing renderer and formatters supply the needed behavior without new architecture.

Revision points: if a metric is not present, omit that KPI instead of deriving it. If selected airports have no mapped marker, retain truthful dashboard scope without invented coordinates. If model interpretation is unavailable, show the real server response and retain structured prompts. If the backend changes during verification, record that and repeat affected API/browser checks against the final files.

### Concrete execution slices

All implementation slices belong to the frontend builder. Preserve others' edits; do not reset, stage, commit or stash the shared checkout. Capture baseline test output and hashes of the four frontend files before editing. Sequential slices may revisit the same file; no concurrent frontend writers.

| Slice | Files (under backend/) | Single outcome | Verification command |
|---|---|---|---|
| 1 | `app/static/styles.css` | Supplied palette and spacing/type tokens | `node --test backend/tests/ui.test.cjs` |
| 2 | `app/static/index.html`, `app/static/styles.css` | Contact-only header and four-prompt idle composition | `node --test backend/tests/ui.test.cjs` |
| 3 | `app/static/index.html`, `app/static/globe.js` | Globe works without surrounding shortcut/zoom/reset UI | `node --check backend/app/static/globe.js` |
| 4 | `app/static/styles.css` | Desktop split and stacked narrow-screen layout | `node --test backend/tests/ui.test.cjs` |
| 5 | `app/static/app.js` | Returned row metrics render as labeled KPI groups | `node --check backend/app/static/app.js` |
| 6 | `app/static/app.js`, `app/static/styles.css` | Bounded charts render returned series/row values | `node --test backend/tests/ui.test.cjs` |
| 7 | `app/static/app.js` | Ranking and evidence retain backend meaning | `node --test backend/tests/ui.test.cjs` |
| 8 | `app/static/index.html`, `app/static/styles.css` | Accessible open/closed composer presentation | `node --test backend/tests/ui.test.cjs` |
| 9 | `app/static/app.js` | Composer sends the existing request contract | `node --test backend/tests/ui.test.cjs` |
| 10 | `tests/ui.test.cjs`, `tests/globe.test.cjs` | Regressions cover changed behavior | `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs` |

Syntax/test commands are checkpoints, not visual acceptance. Do not weaken surviving contract tests to pass a redesign. Tests asserting intentionally removed controls may be replaced only with tests proving their replacements/preserved globe behavior. Before execution, the orchestrator must bound each slice to 150 changed lines; this document does not assert that an unmeasured implementation already meets that limit.

### Presentation contract

- Comparison cards must label both airport values (e.g. LAX and SNA cancellation), not silently promote the first row to an aggregate. Preserve returned units, year, rank and ordering. Display zero as zero, unavailable as a reason-bearing absence. Number formatting and chart coordinate scaling are presentation; growth, scores, ratios, ranks and analytical conclusions remain backend-owned.
- Render only actual `rows[].metrics` keys. The brief's SFO seat-growth/growth-gap examples are conditional: those keys are not declared in the inspected `MetricValue` literal. Do not calculate them or invent fields. ANC numerator/denominator and `scope.threshold_miles` can be shown with precise labels. Chart series uses `result.series`; preserve unavailable gaps and exact-value access. Never connect across missing periods or chart different units on one scale without explicit separate axes.
- Preserve safe DOM text insertion, response validation, source-link validation, `exclusions`, `limitations`, source IDs, denominators and evidence qualification. Preserve `context_result_id` for explain/follow-ups, omit it for a new preset, retain same-origin cookies, explicit retry, request-generation invalidation and timeout cleanup. Guard success and failure commits against stale responses.
- Composer is absent from the tab order while closed, opens from a visible semantic button outside the header or an analysis interaction, focuses the labeled input, closes with Escape/close button, and returns focus to its trigger. Closing retains unsent draft; starting fresh clears result context explicitly. Respect reduced motion and reserve space so a fixed composer cannot cover evidence or actions. Free text sends `message`; structured chips send valid `analysis`; do not fake a conversational answer.
- Remove obsolete globe instructions, including the runtime canvas ARIA label in `globe.js` that currently tells users to use removed zoom controls. Preserve drag, existing wheel behavior, keyboard rotation, selected-airport event wiring, poster fallback, context-loss recovery and usable analytics without WebGL. The globe remains geographic context, never a population filter or metric scale.

### Definition of done and verification

Require `.claude/knowledge/ui-ux-agent-linting.md` Level 1 checks before implementation/final acceptance. Apply `/a11y-audit` first, `/state-coverage-audit`, canvas Level 7 checks adapted to this existing raw WebGL renderer, `/motion-review`, `/responsive-check`, advisory `/impeccable optimize` for the existing Earth poster/load path, then `/impeccable audit`. These are required future checks, not a claim that skills ran in this review. No R3F migration. Confirm contrast rather than assuming low-opacity cream meets it. Validate title/header, prompt grid, KPI group, chart group and disclosures separately during composition; no component catalog installation.

Run from repository root:

```sh
node --check backend/app/static/app.js
node --check backend/app/static/globe.js
node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs
PYTHONPATH=backend python -m pytest backend/tests -q
git diff --check
```

Use the existing environment; do not install dependencies or refresh data. Launch the existing app on a free loopback port and inspect `/` (route verified in `docs/API_UI_MAP.md`). Independently exercise all four presets against real `POST /api/query`; compare displayed values/units/airports with network payloads. Exercise free text and its real unavailable error, prior-result labeling, composer open/close/send, keyboard-only access, missing/partial response fixture, stale response and network-failure fixture. Label injected fixtures separately from real API evidence.

Capture at least idle, LAX/SNA, SFO and composer screenshots at a wide desktop viewport. Also inspect 1280px, 1024px and 390px widths, reduced motion, focus restoration and WebGL fallback. Assert no horizontal page overflow and no composer obstruction. Record the exact frontend SHA-256 set served and final Git status alongside URL/screenshots; a dirty checkout requires file identity in addition to HEAD. Any subsequent file changes invalidate affected evidence.

Independent final architecture/visual review must inspect those artifacts. Builder self-review alone is insufficient. No specialist subagents were dispatched in this plan review because the assignment explicitly prohibited subagents; the orchestrator retains responsibility for required frontend and UI-test review coverage. Final completion is not inferred from this document.

### Unknowns and boundaries

Model runtime admission is not established by this UI task. Backend source expansion is concurrent and outside ownership. The unchanged `/health` proves reachability only. This plan does not claim novel upstream functionality or approval of an implementation diff. No GitHub writes, deployment, release, or global policy changes are authorized by this review.

## Approved amendment — expanded zoom space and idle rotation

User steering authorizes genuinely larger zoomed Earth with no rectangular clipping, and slow self-rotation that pauses for manual manipulation and resumes five seconds after the last movement. This amendment supersedes the earlier no-ambient-motion assumption. Architecture plan approved after the orchestrator adopted the geometry tradeoff below; implementation remains subject to final verification.

### Geometry contract

The inspected shader and DOM marker projection both currently compute radius as `0.45 * min(canvasWidth, canvasHeight) * zoom`. Consequently any zoom greater than `1 / 0.9` clips at the canvas edge, regardless of how large that same canvas becomes. The temporary `MAX_ZOOM = 1.1` avoids clipping by removing meaningful zoom and does not satisfy this amendment.

Separate a stable baseline scene dimension `B` from the expanded viewport dimensions `Cw, Ch`. Compute baseline B from the default 58/42 composition; keep it stable through a zoom gesture and recompute it only when the actual viewport changes, not from a zoom-expanded column. Radius in CSS pixels is `R = 0.45 * B * zoom`. Send `uRadius = R * DPR` to the shader and use `R` in DOM marker projection. Both use the actual expanded canvas center `(Cw/2, Ch/2)`; the shader uses device pixels while DOM positions use CSS pixels. Keep canvas, marker layer and fallback poster alignment consistent.

Default composition remains 58/42. During zoom, allow the right scene allocation to grow up to 50% and use more of its genuinely available area. Allocate the expanded canvas inside that space and provide a small explicit limb/label gutter. After expanding the available space, derive the safe zoom bound from `2R + 2*gutter <= min(Cw, Ch)`, accounting for caption, header and parent clipping boundaries. Use the desired 1.5 zoom where this fits, and the actual geometric limit elsewhere. Do not stretch the sphere into an ellipse, cover dashboard controls, or change analytical scope.

The tradeoff is explicit: at 1672×941, a roughly 600px default Earth cannot become a 900px complete circle inside the existing 702px right column or the 891px content height. Enlarging the right allocation to approximately 836px permits a roughly 780–800px complete circle after gutters/caption. A 900px circle is available only on a viewport with enough measured width and height. Acceptance is meaningful visible growth after real space expansion plus a complete limb, not a universal 1.5 promise or an unchanged 1.1 clamp.

### Rotation and lifecycle contract

One time-based animation loop rotates longitude at one full turn per 180–240 seconds (choose one fixed value within this range). Manual rotation remains responsive. Track interaction state and a monotonic `idleUntil`; pause throughout a pointer gesture and update the five-second deadline on actual pointer movement, wheel zoom and keyboard rotation. Pointer release/cancel/lost capture must clear gesture state; use a fresh deadline on release so a long held gesture cannot resume unexpectedly. Keep longitude bounded and cap elapsed frame time so resuming a tab never catches up in one jump.

Reduced motion disables automatic rotation, including preference changes after load. Hidden pages cancel animation and idle timers; visibility return resets frame time before eligible rotation resumes. Context loss, load fallback and page teardown cancel the rAF and timer, remove relevant listeners and prevent late callbacks from restarting a released renderer. Permit only one active rAF chain. Pause while a globe marker has keyboard focus so the focused target cannot rotate behind the Earth. A compact semantic Pause rotation/Resume rotation affordance supplies user control for continuous automatic motion; place it unobtrusively within the scene, not in the contact header or a restored controls strip. It must remain paused until explicitly resumed, independent of the five-second gesture timer.

Do not turn existing per-draw layout reads into repeated layout thrashing: cache scene geometry on resize and marker label dimensions when content/font/layout changes. Use cached dimensions and projection math during animation. Keep DPR <=1.5 and the existing raw WebGL shader/texture architecture. No route/aircraft simulation or new dependency.

### Ownership, slices and verification

Luna owns these sequential local slices, each at most two files and 150 changed lines; subdivide before exceeding the cap. First update `globe.js`/`styles.css` for shared-radius expanded geometry; second update `globe.js` for the loop/gesture lifecycle; third update `index.html`/`globe.js` for the pause control; fourth update existing globe tests. Preserve concurrent frontend corrections and all backend work.

Commands: `node --check backend/app/static/globe.js`; `node --test backend/tests/globe.test.cjs`; final `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`. Tests must exercise the actual shared radius at default/maximum zoom and DPR1/1.5, resizing while zoomed, five-second idle timing, held gestures, wheel/key postponement, pause/resume, reduced-motion changes, visibility and cleanup. A paused renderer must schedule no perpetual frame work; repeated wakeups must not create multiple loops.

Live verification must record default and maximum-zoom canvas/parent rectangles and projected circle bounds at 1672×941, a taller wide viewport, 1024px and390px. Demonstrate real diameter growth, all limb extremes inside the visible scene, aligned labels and usable dashboard controls. Observe rotation before interaction, no rotation during a held gesture and the idle window, resumed rotation after five seconds, persistent Pause, reduced motion and hidden-tab behavior. Capture default/zoom screenshots and bind observations to final static file hashes. This amendment is not itself evidence that those tests passed.

## Approved amendment — Earth background with foreground charts

Latest explicit user steering supersedes the prior requirement that the zoomed sphere remain entirely inside a right-hand panel. Use the full main viewport as the globe drawing surface; the Earth can extend beneath foreground analytics. Natural clipping at the outer application viewport is permitted where geometry makes it unavoidable. A rectangular cutoff at the former right-column boundary is not permitted. The contact-only header remains unchanged.

### Layering and geometry

Create one positioned main scene with a full-width absolute background canvas/marker layer and a foreground dashboard layer. Preserve the default visual 58/42 relationship by anchoring the sphere near the right-hand composition center; this is a visual anchor, not a clipped right-panel canvas. Keep the default sphere radius derived from the intended Earth size, not from the full canvas width. Zoom increases that radius independently of the drawing surface.

Shader and marker projection share an explicit viewport-relative center and CSS-pixel radius; convert both to device pixels only for shader uniforms/backing resolution. Geographic orientation remains a separate quantity from the on-screen center. Do not reuse the geographic `uCenter` longitude/latitude uniform as the screen-space center. Derive pointer coordinates from the full canvas rectangle, including its actual page offset. Resize re-evaluates center and size without corrupting zoom or analytical scope.

Foreground charts use sufficiently opaque navy surfaces for readable cream text against the Earth. They intentionally occlude the background. Keep foreground controls above the canvas and markers in stacking order, and keep markers beneath analytics so labels never cover chart values or inputs. An occluded marker must not remain an invisible keyboard stop; exclude it from the tab sequence while obscured and preserve a visible focus destination when visibility changes. All analysis remains available through semantic DOM controls without globe interaction.

Exposed Earth remains draggable. Reject pointer events outside the projected sphere before pointer capture, and do not intercept chart clicks, textarea editing, native page scrolling or modified browser wheel gestures. Use no invisible full-screen foreground overlay that blocks the exposed globe. The existing bounded zoom and 180–240-second idle rotation lifecycle remain, including user pause, reduced-motion/visibility handling, five-second manual-idle resumption and teardown cleanup. The earlier allocation-up-to-50% geometry is superseded by the full-main drawing surface; no column expansion is necessary.

### Analysis navigation and information hierarchy

Idle retains exactly four prominent presets and the optional composer. An analysis presents chart-led content derived exclusively from returned `rows` and `series`, with at most two principal chart groupings. Exact values, unavailable reasons, units, airport labels and backend ranking remain accessible. Evidence, sources, coverage and limitations stay available through collapsed disclosures; preserve an unmistakable partial/previous-result label when applicable. Do not invent an ANC trend or a second series to fill space. Do not remove the qualification that traffic pressure does not establish investment returns or unmet demand.

Place a large semantic Back button at the top of the analysis area, outside the contact header. Give an icon-only arrow an accessible name, a >=44px target and visible focus. Back returns to the four-prompt idle view, hides the current-result presentation, clears the result-context reference, invalidates pending response commits and restores focus to a surviving idle control. It sends no request and does not replay analysis. A late request success, failure or finally handler must not reopen results or overwrite the new idle view after Back.

The bottom interaction surface contains the chat input and its essential submit/dismiss controls only. Remove the newly proposed contextual follow-up chips and redundant bottom control rows: this instruction supersedes the earlier brief's follow-up-chip requirement. Retain the subtle Ask entry affordance where needed to reopen a dismissed composer. Essential evidence disclosures and the unobtrusive scene-level pause control are not a bottom control strip.

### Delivery slices and acceptance

Luna owns sequential slices, at most two files and 150 changed lines each: full-main scene layout in `index.html`/`styles.css`; explicit shared screen center/radius in `globe.js`; foreground chart hierarchy and Back behavior in `app.js`/`index.html`; updated frontend/globe behavioral tests. Subdivide larger slices. Do not change backend, contracts, datasets, assets or dependencies.

Run `node --check backend/app/static/app.js`, `node --check backend/app/static/globe.js` and `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`. Test full-canvas projection at multiple DPRs and resizes, Back while a request is pending, late success/error cleanup, keyboard focus after Back and hidden/occluded marker focus. Preserve existing response-unit/data-integrity tests.

The orchestrator's live acceptance must demonstrate default and visibly enlarged Earth extending behind the dashboard with no old panel-edge crop, interactive charts/chat above it, drag on exposed Earth, chart/data fidelity for all four real workflows, Back to idle, error-after-success, partial data, 390px layout, reduced motion and the five-second rotation resume. Natural viewport clipping must be identified as such rather than presented as a complete-circle claim. Bind screenshots and browser checks to final static hashes. Plan approval does not assert those checks have run.
