# Optional landing flight motion — 2026-09-27

Status: DRAFT FOR REVIEW. Optional finishing detail, executed only after core ORBIT and zoom acceptance. It must not delay their implementation or conceal unresolved checks. No application code changes are authorized by this document alone; the parent dispatches the approved rows.

## Design decision

A short flight-themed accent can help the initial scene feel intentional, but it adds no analytical information. Use two faint stylized aircraft traces in **screen space**, spatially separated from airport dots and without connecting endpoints. They are not geographic routes, flights in progress, or returned data. Render a visible “Illustrative animation” caption outside the aria-hidden visual layer. If final screenshots look busier or the caption becomes necessary clutter, omit the entire decoration; that is the preferred fallback, not a reason to expand the animation architecture.

The visual overlay is pointer-events:none, aria-hidden=true, absolutely positioned within the Earth viewport, and clipped to that viewport. It has no focusable children or interactive meaning. The caption remains readable by assistive technology. Use tiny original inline vector plane shapes and a restrained trail; no external assets, source data, dependencies, renderer changes, new network requests or geographic projection. Keep accent opacity low while preserving caption contrast. Do not cover labels, controls or analysis content.

## Exact lifecycle

- On the initial landing view only, play once: total duration 4 seconds, one iteration, no delay, transform/opacity properties only. No looping, animation restart, requestAnimationFrame, timer-driven frames, or animated filters. `animationend` removes/hides the visual layer and caption; a static CSS final state also makes them nonvisible if the event is missed.
- `prefers-reduced-motion: reduce` hides the decorative visual layer and caption entirely. It never replaces the motion with a misleading frozen aircraft. If the media preference changes to reduce, retire the decoration for this page lifetime; switching back does not replay it.
- Any genuine query start immediately retires the overlay and caption before fetch, regardless of request success. Current/partial/previous result views and Start new analysis never restart it. A local invalid scope that sends no query need not retire it. A boolean page-lifetime retirement state is sufficient; no storage or transcript.
- `document.hidden` pauses the CSS animation. Returning to visible resumes the same one-shot play unless query/reduced-motion/end has retired it. Hidden-page activity never restarts the animation. No pause control is added because the single run is shorter than five seconds; keyboard and analysis interaction remain usable throughout.

## Atomic execution

Only existing Luna-layout writes these files, serially after its ORBIT and zoom work. No backend, API, data, process, Git-write, or renderer changes. Each row is RECOMMEND, ≤2 files, ≤150 net text LOC, one outcome. H=`backend/app/static/index.html`; C=`backend/app/static/styles.css`; A=`backend/app/static/app.js`; T=`backend/tests/ui.test.cjs`.

| Step | Owner | Files | Cap | Dependency | Outcome | Verification | Reject if |
|---|---|---|---:|---|---|---|---|
| 1 | Luna-layout | H,C | 90 | Core+zoom accepted; this addendum reviewed | Add noninteractive one-shot landing decoration | `git diff --check -- backend/app/static/index.html backend/app/static/styles.css`; static check of two shapes, caption, 4s/one iteration, final-hidden and reduced-motion CSS | Geographic connections, overlay input interception, extra asset/request, perpetual motion |
| 2 | Luna-layout | A,T | 110 | 1 | Enforce page-lifetime retirement/pause lifecycle | `node --check backend/app/static/app.js`; `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`; cases query-start-before-fetch, end, current/previous, Start new, hidden/visible, reduce/back | Replay, stale overlay after query, reduced-motion animation, missed cleanup |
| 3 | UI tester | docs/frontend/2026-09-27-flight-motion-review.md (new) | 100 | 2 | Record visual acceptance or omission recommendation | Existing ORBIT C4/C6 exact-source checks and CUA at1440×900/390×844: initial screenshot, ended screenshot, immediate query removal, click-through controls | Label ambiguity, marker overlap, competing visual hierarchy, false live-motion claim |

[Atomicity self-check: PASS — each row has one owner/outcome/tier, ≤2 files, ≤150 net text LOC; shared implementation files run serially.]

Frontend review applies accessibility first, then state coverage, motion review, responsive overflow and visual-quality review. No canvas audit is rerun solely for this DOM decoration; renderer remains unchanged. Deterministic tests establish lifecycle and media-query reactions. CUA evidence establishes actual observed appearance/click-through behavior; do not claim a live reduced-motion or hidden-page test unless the tool exercised it. Parent independently reviews the tiny addendum and final evidence; approval cannot be solely the implementer's own assessment. Final architecture acceptance incorporates this delta if retained.

Definition of done: all three rows pass on identical served source hashes and the decoration improves the landing composition without confusing it with data. Otherwise omit/defer this optional surface and retain the accepted core+zoom product. No flight-data capability is delivered or implied.
