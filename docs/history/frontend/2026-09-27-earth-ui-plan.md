# Earth UI implementation plan — 2026-09-27

Status: **DRAFT FOR REREVIEW — IMPLEMENTATION NOT STARTED**.

Implement the supplied dark, Earth-led airport-analysis UI. Screenshots 1 and 3 lead the initial composition; screenshot 2 leads the result workspace. Their flight paths, aircraft, “live” labels, investment scores, market totals, accounts, and demo numbers are illustrative and must not enter this application.

Reference images are bound as design evidence:

| Reference | Source path | SHA-256 | Use |
|---|---|---|---|
| Screenshot 1 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-6PYRgu.png` | `c6f26bc00879ea84a7c86503e2251806035c68b32bd6a2996860c09c7bcddbbb` | Initial hero balance, slim header, prompt/preset density. |
| Screenshot 2 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-dQwHNo.png` | `fd2c0bb0592f208e90e86ffbc3856516d70feb3196373a60713069050b3db705` | Completed three-zone analysis workspace. |
| Screenshot 3 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-3j0Un0.png` | `c3a8a3f82d034564a47140339ae1f0789d39c14b3dd1ad3c59e8ed3362bea73b` | Earth scale, inline result hierarchy, restrained blue accents. |

This supersedes only the earlier no-3D/no-new-visual-dependency boundary. It retains one FastAPI process, plain HTML/JavaScript, current API/result validation, draft/error/previous-result behavior, accessibility, and the closed `G-2025` gate. The browser never calculates or invents analysis.

## Renderer and asset decisions

- Use [EARTH — The making of home](https://earth.ethanplus.ai/) and [earth-moon-solar](https://github.com/ethanplusai/earth-moon-solar#readme) as composition/interaction references. No iframe, hotlink, copied scene, aircraft, or route visualization.
- Implement an original raw-WebGL analytic globe in `globe.js`: a fullscreen quad (two triangles) with bounded ray/sphere intersection in the fragment shader. It samples local day, night-light, and cloud textures and computes one atmosphere rim in the same pass. A mathematically smooth limb needs no sphere geometry and complies with the mesh cap.
- Add no Three.js, React, package manager, vendored JavaScript, post-processing, particles, procedural landmass, or continuous render loop. There is no vendored-code LOC exception.
- Acquire NASA-derived imagery only after source/redistribution terms are verified. `ASSET_CREDITS.md` records source URL, checked date, transformation, dimensions, bytes, and hash. An unverifiable asset fails before code references it.
- Show six verified spatial shortcuts: `ANC`, `LAX`, `SNA`, `SFO`, `BOS`, `PVD`. A separate complete non-spatial picker covers all 26 supported codes. Copy says globe shortcuts are partial geographic coverage. Results highlight only returned codes with verified mapped coordinates.

## Visual architecture and states

1. **Header:** slim product name plus real Analyze, Scope, and Evidence anchors. Evidence is initially nonfocusable and `aria-disabled`; after a validated current or previous result it becomes one real link to the unique evidence heading. Start new analysis preserves that link while the previous result remains visible.
2. **Initial desktop ≥1200 px:** at scroll zero, a 38–42% copy/control column sits beside a 58–62% Earth stage. At 1440×900, product purpose, presets, local request status, and at least half the Earth are visible.
3. **Scope:** the structured form uses restrained glass treatment. A labeled all-airports picker adds any of the 26 supported codes to the same visible draft. Free text stays disabled and honestly explained.
4. **Local status:** the one `#feedback` region, including View result, sits beside initiating controls inside the first desktop viewport. On mobile it remains in flow directly after active controls. It never covers content; response arrival never moves focus/scroll.
5. **Completed desktop ≥1440 px:** compact left scope/preset rail, central Earth, and 34–38 rem result/evidence rail. At 900–1439 px, results span below Earth/scope. Wide tables retain keyboard horizontal scrolling.
6. **Mobile 390×844:** header, intro, 52–58dvh Earth, six-shortcut accessible list, presets, local status, scope, then result. Canvas gestures never trap vertical scroll.

Initial state faces North America and shows no metric/success signal. Loading retains prior output/orientation. Success/partial highlights validated mapped result airports. Error retains the previous result and Evidence link; empty/unavailable result states stay truthful. Start new analysis clears follow-up context and current-result emphasis, preserves the labeled previous result and its Evidence link, and emits its globe state with `previous: true` for subdued highlights.

## Bounded renderer and marker contract

- One quad, one draw call, two triangles, three ≤2048×1024 textures, DPR `min(devicePixelRatio, 1.5)`. Shader path: ray/sphere hit, three samples, light dot product, night mask, cloud mix, atmosphere edge. No loops, noise, secondary rays, collections, or framebuffer pass.
- Canvas dimensions exist before module load. CSS supplies a bounded starfield outside WebGL.
- Drag updates two orientation scalars and renders on pointer events; resize renders once. No idle/cloud rotation, wheel zoom, scroll camera, or perpetual `requestAnimationFrame`.
- Reduced motion uses the same static interactive WebGL and removes animated CSS transitions. Poster is for loading/failure only.
- Renderer states: `poster -> loading -> ready | fallback`. Loading has one eight-second deadline. Shader/link/texture/decode failure or `webglcontextlost` cancels timers/listeners and permanently selects fallback for that page load. Late callbacks carry a generation token and cannot revive fallback. No automatic retry.
- Shader and DOM projection consume the same JS orientation/camera parameters. At default orientation and after a 30-degree drag, at 1440 and 390 widths, every visible marker center must be within 4 CSS px of its CPU-projected geographic location. Separately, the rendered globe limb must match the projected sphere boundary within 6 CSS px; interior markers need not be near the limb.
- Projected dots are buttons. Collision lanes hide a lower-priority label, never the selected dot. Focused/draft/result labels win. Rear/occluded projected buttons are `hidden` or `tabindex=-1`, never invisible tab stops. A separate six-button “Globe shortcuts” list remains available; focus stays on the activated control.

All four custom events are dispatched and listened for on `window`, with `bubbles: false`; tests use this same target. `globe.js` dispatches `globerendererready`. `app.js` then replays current draft and validated result state. Early state events may be missed because the handshake always sends one current snapshot. Exact events:

| Event | Producer -> consumer | Contract |
|---|---|---|
| `airportdraftselect` `{code}` | globe -> app | Edit visible draft only; never submit. |
| `airportdraftchange` `{airports}` | app -> globe | Highlight current mapped draft codes. |
| `analysisresultchange` `{airports, previous}` | app -> globe | Highlight mapped validated-result codes; previous is subdued. |
| `globerendererready` | globe -> app | Request exactly one replay of current draft/result. |

Metric replaces one airport. Compare appends a unique code up to two, then replaces the second. Rank accepts every supported New England code from the complete picker; among the six spatial shortcuts only `BOS`/`PVD` are in that cohort. Non-New-England selections leave a rank draft unchanged and announce why. Accepted changes call `changedDraft()`, clear only the airport error, retain focus, and send zero requests.

## Assets and executable checks

| File | Hard cap | Reject when |
|---|---:|---|
| `assets/earth-day-2048.webp` | 650 KB, ≤2048×1024 | Unknown terms/hash, corrupt decode, wrong projection, or cap exceeded. |
| `assets/earth-night-2048.webp` | 450 KB, ≤2048×1024 | Same, or copy implies real-time lights. |
| `assets/earth-clouds-2048.webp` | 650 KB, ≤2048×1024 | Same, or geography is obscured. |
| `assets/earth-poster.webp` | 350 KB, ≤1600 px wide | WebGL-dependent, missing attribution chain, or cap exceeded. |
| `assets/airport-coordinates.json` | 4 KB | Not exactly the six named, verified, finite coordinates. |

Combined image cap is 2.1 MB. For each image run `file`, `wc -c`, `sips -g pixelWidth -g pixelHeight`, and `shasum -a 256`. Validate coordinate keys/bounds with a bounded `node -e` check. These prove file properties; CUA provides visual-open evidence only.

Deterministic suite:

```sh
node --check backend/app/static/app.js
node --check backend/app/static/globe.js
node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs
git diff --check -- backend/app/static/index.html backend/app/static/styles.css backend/app/static/app.js backend/app/static/globe.js backend/app/static/assets/airport-coordinates.json backend/app/static/ASSET_CREDITS.md backend/tests/ui.test.cjs backend/tests/globe.test.cjs docs/frontend/2026-09-27-localhost-review.md docs/frontend/2026-09-27-earth-ui-plan.md
```

## Ownership and ordered atomic plan

`3d-frontend` owns `globe.js`, `globe.test.cjs`, assets, credits, and its renderer-audit entry. `frontend-ux-engineer` owns `index.html`, `styles.css`, `app.js`, and `ui.test.cjs`. `ui-tester` owns browser evidence. Backend Python/data/contracts remain with the parallel session.

Per the user's routing direction, implementation rows are executed by a `gpt-6-luna` worker carrying the named owner's file/acceptance contract. `3d-frontend` reviews renderer work; `lead-architect` performs the final Astra architecture review. Model choice does not relax ownership, test, or evidence gates.

Each row has one owner/outcome/tier, ≤2 files, and ≤150 net text LOC; binary rows add zero text LOC. Shared files run serially. No backend edits, server management, stage/commit/stash/clean, deployment, or GitHub write.

Table file labels are exact aliases: `HTML` = `backend/app/static/index.html`; `CSS` = `backend/app/static/styles.css`; `app JS` = `backend/app/static/app.js`; `UI test` = `backend/tests/ui.test.cjs`; `globe JS` = `backend/app/static/globe.js`; `globe test` = `backend/tests/globe.test.cjs`; `review doc` = `docs/frontend/2026-09-27-localhost-review.md`; `JSON` = `backend/app/static/assets/airport-coordinates.json`; `credits` = `backend/app/static/ASSET_CREDITS.md`. Image labels resolve to the exact asset paths in the asset table.

| # | Owner | Tier | Budget | Outcome | Files | Depends | Check | Reject if |
|---:|---|---|---:|---|---|---|---|---|
| 1 | ui-tester | DRAFT | ≤60 | Bind baseline and three design references to exact identities. | review doc | — | Record route/viewports/frontend hashes plus table above; diff check. | Old evidence is called current or identity guessed. |
| 2 | frontend-ux-engineer | RECOMMEND | ≤80 | Move inline CSS unchanged to a stylesheet. | HTML, CSS | 1 | Current 20 tests and desktop comparison pass. | Redesign enters mechanical extraction. |
| 3 | 3d-frontend | RECOMMEND | 0 | Acquire verified day/night images. | 2 images | 1 | Per-asset file/byte/dimension/hash checks. | Any source/term/cap check fails. |
| 4 | 3d-frontend | RECOMMEND | 0 | Acquire verified cloud/poster images. | 2 images | 3 | Per-asset checks and visual open. | Poster needs WebGL or provenance is incomplete. |
| 5 | 3d-frontend | RECOMMEND | ≤100 | Add six verified coordinates and full provenance. | JSON, credits | 4 | Exact-set/bounds validator; sources/hashes recorded. | Guess, duplicate, omission, or complete-coverage implication. |
| 6 | 3d-frontend | RECOMMEND | ≤150 | Implement raw-WebGL init/fallback state machine without DOM integration. | globe JS, globe test | 5 | Syntax plus timeout/failure/late-callback/context-loss tests. | Retry loop, callback resurrection, perpetual RAF, or external request. |
| 7 | 3d-frontend | RECOMMEND | ≤150 | Implement one-pass analytic textured globe. | globe JS, globe test | 6 | Syntax and mocked GL tests assert one draw/two triangles/fixed path; real shader compilation checked in Step 10. | Mesh sphere, loop/noise/postprocess, extra pass, or cap failure. |
| 8 | 3d-frontend | RECOMMEND | ≤130 | Add drag/resize/visibility/cleanup/reduced-motion behavior. | globe JS, globe test | 7 | Pointer/resize/cleanup tests; zero idle RAF. | Scroll trap, wheel hijack, leak, or continuous motion. |
| 9 | frontend-ux-engineer | RECOMMEND | ≤120 | Add semantic header and real anchor lifecycle markup. | HTML | 2 | One h1; Analyze/Scope targets; initially nonfocusable disabled Evidence. | Fake product action or focusable dead link. |
| 10 | frontend-ux-engineer | RECOMMEND | ≤150 | Add fixed Earth stage, poster/canvas/status, marker/list containers, and module script after `globe.js` exists. | HTML, CSS | 8, 9 | Exact local module URL resolves; real shader compiles/renders in localhost; pre-load size/poster/status/list exist. | Any intermediate 404, CDN, layout shift, or blocked control. |
| 11 | 3d-frontend | RECOMMEND | ≤150 | Project six markers with shared math, collision, focus, and list parity. | globe JS, globe test | 10 | Default/drag projection tolerances; front/rear tab order; collision/event tests. | Misalignment, invisible tab stop, focused-label loss, or submit. |
| 12 | frontend-ux-engineer | RECOMMEND | ≤150 | Apply dark tokens/starfield/header/glass/focus styling. | CSS | 10 | Contrast/focus inspection; no blanket transitions. | AA failure, clipped focus, or clutter. |
| 13 | frontend-ux-engineer | RECOMMEND | ≤150 | Compose screenshot-1/3 initial desktop stage/presets. | HTML, CSS | 12 | 1440×900 measurable composition criteria pass. | Small-card Earth, lost preset meaning, invented metrics/routes. |
| 14 | frontend-ux-engineer | RECOMMEND | ≤140 | Restyle scope/disabled composer and add complete 26-airport picker. | HTML, CSS | 13 | Labels/descriptions/errors linked; all 26 options; composer remains disabled. | Spatial six are presented as full scope or payload semantics change. |
| 15 | frontend-ux-engineer | RECOMMEND | ≤150 | Integrate spatial/list/complete-picker draft rules. | app JS, UI test | 11, 14 | Tests cover metric/compare/rank, focus, error clearing, generation guard, zero fetch. | Submit/fabrication, unrelated overwrite, or validation bypass. |
| 16 | frontend-ux-engineer | RECOMMEND | ≤110 | Replay current app state after late renderer readiness. | app JS, UI test | 6, 15 | State-before-ready then ready yields exactly one current replay. | State lost/duplicated or stale result wins. |
| 17 | frontend-ux-engineer | RECOMMEND | ≤130 | Put single feedback/View result beside initiator. | HTML, app JS | 13, 16 | Node focus test; desktop scroll-zero browser check. | Offscreen/duplicate live region or focus/scroll theft. |
| 18 | frontend-ux-engineer | RECOMMEND | ≤120 | Implement Evidence anchor lifecycle. | app JS, UI test | 17 | Initial disabled/nonfocusable; success and retained previous enable unique heading href; fresh retains previous Evidence and subdued highlights. | Error/reset loses valid previous Evidence, empty link focuses, or duplicate target. |
| 19 | frontend-ux-engineer | RECOMMEND | ≤150 | Apply screenshot-2 result-workspace state/layout. | app JS, CSS | 18 | Valid/previous/fresh states switch correctly; table contained. | Health/timer drives layout or previous looks current. |
| 20 | frontend-ux-engineer | RECOMMEND | ≤130 | Polish metric/table/evidence/source/limitation hierarchy. | CSS | 19 | Values/order/units identical; keyboard table scroll works. | Investment implication, hidden caveat, or truncated ID. |
| 21 | frontend-ux-engineer | RECOMMEND | ≤150 | Implement tablet/mobile flow and unobscuring local status. | CSS | 20 | 1024/768/390: 44px targets, no page overflow, canvas scrolls page. | Control/result obscured or unreachable. |
| 22 | frontend-ux-engineer | RECOMMEND | ≤130 | Add final static/state/fallback guards. | HTML, UI test | 21 | Full deterministic suite; local assets/module, canvas/poster/list, anchors, disabled composer. | Source matching is sole behavior proof or Node requires WebGL. |
| 23 | 3d-frontend | DRAFT | ≤100 | Record renderer/load/asset evidence on exact hashes. | review doc | 22 | Record shader/draw/triangle/DPR/RAF/lifecycle and asset command results. | CUA is claimed to prove FPS/network/hash or any cap fails. |
| 24 | ui-tester | DRAFT | ≤150 | Run ordered audits and localhost QA. | review doc | 23 | Audits: a11y -> state coverage -> renderer perf -> motion -> responsive -> load optimize -> impeccable. CUA natural flows at 1440/1280/390; Node mocks exact fetch counts, context loss, timeout, error/empty; shell proves assets. | Synthetic failure is claimed as CUA, hidden marker is tabbable, projection tolerance/flow fails, or audit blocks. |
| 25 | CEO | DRAFT | ≤100 | Close exact-hash review-fix loop with mandatory lead-architect approval. | this plan, review doc | 24 | Deterministic checks and all reviewers APPROVED with zero comments on same hash. | `G-2025` called open, stale evidence, or any finding. |

[Atomicity self-check: PASS — 25 steps; each declares one owner, one outcome, one tier, at most two exact files, a budget at most 150 net authored text lines, explicit dependencies, a verification check, and rejection criteria. Binary acquisition rows have zero authored text lines. Shared-file edits run serially.]

## Terminal gates and loop limits

Complete means both reference-led compositions meet their measurable layout criteria; the globe stays geographic and claim-free; spatial/list/picker actions edit drafts only; completion status is visible at the initiating viewport; and failure/reduced motion/keyboard/200% zoom/390×844 preserve the workflow. Existing 2023/2024 validation, error, previous-result, sources, and disabled-free-text behavior remain green. No live-flight/latest-data/global-coverage/investment-score/2025 claim appears.

`G-2025` stays closed until the separate backend handoff supplies an accepted fixture, period/bundle provenance, cohort, errors, and backend revision. This work can earn **HISTORICAL VISUAL SLICE APPROVED** only.

The loop inherits at most 15 atomic fix turns and 6 full review rounds. Reopen the smallest causal row and rerun Steps 22–25; approvals never carry. Hard-halt NOT APPROVED on **STALL-A** (same reviewer rejects unchanged artifact three times), **STALL-B** (same reviewer rejects five times with two shared key phrases), or **STALL-C** (from round 3, Critical count fails to decrease for three consecutive same-class rounds, or two consecutive rounds flag a defect introduced by the preceding fix). Unavailable review/browser evidence, exhausted limits, or closed `G-2025` cannot become approval by timeout or prose waiver.
