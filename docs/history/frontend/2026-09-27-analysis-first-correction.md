# Analysis-first correction — 2026-09-27

Status: DRAFT FOR QUICK REVIEW. Latest user direction supersedes ORBIT's three-column/globe-first composition, result drawer, mobile Earth-first order, zoom cropping and optional flight decoration. Preserve completed typed result renderers, source/evidence presentation, request guards and backend contracts. Decorative flights are deferred.

## Decision

Make the analytical answer the product. Desktop ≥1000px uses one **60% left analysis workspace / 40% right geographic context** split, calculated after the gap. Use `minmax(0,3fr) minmax(0,2fr)` with a 20px gap and existing outer padding; remove conflicting older three-column media rules rather than accumulating overrides. No fixed-width center minimum and no separate right result rail/drawer.

The left workspace contains a concise initial heading, primary structured composer, four presets, compact expandable scope, and the active typed result. After a result, shrink the introduction into navigation, collapse extra prompts/manual scope, and put request summary + result heading + backend conclusion + primary metrics at the top of the left workspace. Scope/“Try another analysis” remain explicit expandable controls. Preserve one DOM instance of each control and result. Do not automatically scroll or move focus on response; “View result”/skip links explicitly focus the visible inline result heading. Evidence explicitly focuses the visible evidence heading. Preserve truthful partial/previous/error behavior.

The analysis workspace has one vertical scroll region at desktop, not separate controls and result scroll panes. The right Earth section is a supporting unboxed surface. On <1000px use normal page flow: composer → result → Earth, with a direct “View globe” anchor; the answer must not sit behind a large globe. Header, context strip and globe can retain their restrained dark styling. No generic nested result cards, new state architecture or backend work.

At 1440×900 after a successful comparison, the left region should occupy 60% ±2% of available workspace width; result title, backend conclusion and all four comparison indicators are visible without scrolling. At 1920×1080 retain the same ratio. Initial state exposes all four examples. At 390×844 composer precedes result and Earth follows it, with zero page-width overflow and all evidence reachable. Exact existing IDs, draft semantics, typed values and provenance remain preserved.

## Fix the clipped zoom deliberately

Current renderer uses radius `min(width,height)×0.47×zoom`; zoom >1.06 necessarily crops the sphere in its square viewport. A fade or circular mask would conceal the edge while still cropping geography, so it does not satisfy the user's request. Use a **contained overview zoom** instead: shared base radius 0.30, zoom range [1,1.5], maximum radius 0.45×min dimension. The entire globe silhouette remains visible with a 5% margin at every zoom. Default/reset is zoom1; controls retain actual zoom percentage and existing 1.2 factor, clamped at1.5. Progressive metric detail threshold becomes1.25 instead of1.6. Shader, CPU marker projection and drag sensitivity use the same state; no CSS transform enlargement. Narrow screens retain the same containment rule.

This intentionally trades deep regional magnification for a clear complete globe. Do not introduce another camera mode, pan/viewport system, mask, fade, renderer library or animation. Existing bounded wheel rules, keyboard zoom controls, mobile scroll preservation, teardown/fallback and marker clipping stay intact. Update their boundary expectations for the new range rather than removing the tests. At maximum zoom, full limb must be visible; visible geographic marker dots remain within4 CSS px of their projected coordinates. Actual values still come only from the result event.

## Small ordered corrections

Owners remain existing Luna-layout (H/C/A/T) and Luna-globe (G/GT), with no concurrent edits to the same file. Start after each respective in-flight bounded step is finished. Each implementation step is RECOMMEND, ≤2 files, ≤150 net text LOC. H=index.html, C=styles.css, A=app.js under `backend/app/static/`; T=`backend/tests/ui.test.cjs`; G=`backend/app/static/globe.js`; GT=`backend/tests/globe.test.cjs`. Evidence E=`docs/frontend/2026-09-27-analysis-first-review.md` is new, DRAFT, ≤150 LOC per reviewer entry.

| Step | Owner | Files | Cap | Depends | One outcome | Check | Reject if |
|---|---|---|---:|---|---|---|---|
| 1 | Luna-layout | H,C | 150 | quick plan acceptance | Compose single 60/40 analysis-first workspace | Node UI suite; CUA1440/390; diff check | Three columns/drawer remain; answer follows globe on mobile; duplicate IDs |
| 2 | Luna-layout | A,T | 150 | 1 | Simplify inline result/anchor/focus lifecycle | Node UI suite: initial/current/previous, result skip, Evidence, breakpoint focus | Obsolete drawer logic hides result or strands focus |
| 3 | Luna-layout | H,C | 120 | 2 | Give current answer visual priority over setup | CUA1440 comparison/SFO/initial; keyboard expand scope/prompts | Four indicators below fold or controls/data become unavailable |
| 4 | Luna-globe | G,GT | 130 | approved plan; its prior step done | Contain globe at every zoom | Node globe suite: shared0.30 radius, range1–1.5, threshold1.25, wheel/button/reset/projected limb | Any allowed zoom crops full sphere or CPU/shader disagree |
| 5 | UI tester | E | 150 | 3,4 | Verify corrected visual priority and zoom on localhost | ORBIT C4/C6 served-byte checks; CUA matrix below; full Node suite | Screenshot misses60/40 goal or full-globe maximum zoom |
| 6 | Lead architect | E | 100 | 5 | Review preserved analytical contracts and final correction | Exact hashes, frontend/UI findings, full Node suite | Open finding or result semantics changed |

[Atomicity self-check: PASS — one owner/outcome/tier, ≤2 files, ≤150 net text LOC per row; shared-file steps are serial.]

Commands: `node --check backend/app/static/app.js`; `node --check backend/app/static/globe.js`; `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`; `git diff --check -- backend/app/static/index.html backend/app/static/styles.css backend/app/static/app.js backend/app/static/globe.js backend/tests/ui.test.cjs backend/tests/globe.test.cjs docs/frontend/2026-09-27-analysis-first-correction.md docs/frontend/2026-09-27-analysis-first-review.md`. Before/after screenshots use ORBIT's read-only C6 source-byte equality recipe and C4 source hashes; reload localhost without managing its process.

UI matrix: initial, LAX/SNA, SFO, New England, ANC, failed request after success; widths1440/1920/1280/390; keyboard controls/result/Evidence; minimum/default/maximum zoom via real controls; actual wheel if tool supports it. Capture full-limb screenshot at maximum zoom at1440 and390, not an unaided claim from code. Retain existing deterministic wheel/media/guards and mark unobserved live cases honestly. Audit accessibility → state → changed canvas integration → motion → responsive → visual hierarchy, scoped to this correction. Parent independently accepts architecture after brief specialist review; final architecture review remains independent. User intent is satisfied by readable airport analysis with supporting geography, not by completing deferred decorative work.
