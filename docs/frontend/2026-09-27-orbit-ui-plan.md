# ORBIT airport workstation implementation plan — 2026-09-27

Status: DRAFT FOR FRONTEND, UI-TESTER, AND ARCHITECT REVIEW. No implementation approval is implied.

The user's latest ORBIT brief replaces the composition in the earlier Earth plan. Preserve `2026-09-27-earth-ui-plan.md` unchanged as historical evidence. Deliver one local plain HTML/CSS/JS page with the existing deterministic query boundary. Luna implements; independent specialists review. Backend Python, contracts, data, processes, and the closed G-2025 handoff remain outside this change.

## Architecture and constraints

Use the existing Earth renderer and local assets unchanged. The environment is the central visual surface, with quiet analytical rails around it. No rectangular Earth card, giant working-state brand title, fake chat transcript, aircraft, invented indicators, new framework, service, endpoint, or dependency. Optional camera/arc/rank-marker refinements are deferred: `globe.js`, globe tests, assets, and credits are frozen.

DOM reading order is header → landing introduction → Earth → analyst rail → result panel → context strip. Preserve that order below 1000px; wide-grid placement changes geographic presentation only, never uses positive tabindex. The first interactive region after navigation is Earth shortcuts, then composer/presets, then results. Add skip links to analyst controls and results. Before any result, the result skip has no href and is absent from tab order. At 1000–1399px its activation opens the drawer then focuses the result heading; elsewhere it focuses that heading inline. It stays usable for retained previous results. Breakpoint changes must not strand focus in hidden drawer controls. A result arrival does not move focus or scroll.

Desktop ≥1400px: shell uses header (56px), workspace `minmax(0,1fr)`, contextual strip (56px), total `100dvh` with `100vh` fallback. Workspace fills viewport with 20px outer padding and two 20px gaps. Working grid: 280px rail, `minmax(0,1fr)` Earth, 400px result. At 1440px this leaves 680px Earth width, rather than imposing a 620px center minimum that overflows. At ≥1800px use 300px/440px rails. Each rail has `min-height:0`, its own vertical scrolling, visible focus, and reachable first/last content; the workspace/body do not scroll at this breakpoint. The Earth frame fits available height and width without CSS upscaling its backing canvas. Initial mode has no empty right rail: 380px analyst area plus flexible Earth; headline/composer lead, only four prompts visible.

1000–1399px: 280px analyst rail plus flexible Earth. Result is one nonmodal fixed drawer, width `min(420px,calc(100vw - 32px))`, bounded between header and bottom strip, independently scrollable. A real “View result” button opens it, `aria-expanded` reflects state, `aria-controls` names it, and a close button/Escape close it and restore focus to the opener. No modal role, inert background, or focus trap. Closed drawer is hidden and has no tab stops. Success updates data and announces availability without opening it automatically. Evidence navigation opens drawer before focusing its evidence heading. Breakpoint changes restore visible inline results ≥1400 or <1000, remove drawer-only controls, and never leave focus inside hidden content.

Below 1000px: normal document flow/scroll; no fixed height or sticky Earth; Earth header occupies at most 48dvh (minimum 240px where space permits), then controls and result. Context strip hides entirely (its information remains in result header). At 200% browser zoom the CSS viewport breakpoint governs; nothing depends on physical display width. At 320px horizontal page overflow is zero; wide technical tables scroll only in labeled keyboard-focusable regions.

Tokens live in existing `styles.css`: background #05090d, surfaces #09111a/#0d1722, primary #f2f5f7, secondary #93a3b2, accent #76b9ff, success #68d5a1. Muted text must still meet contrast for its role; #5f7180 is decorative only unless measured compliant. Fixed-rem app typography, regular/medium weights, 12px main surface radius, subtle separators. No nested generic cards, global blur, decorative gradients, animated numbers, or new ambient motion. Retain usable non-color marker states and ≥44px interaction targets.

## Existing contract mapped to presentation

Read against current `backend/app/contracts.py`, `backend/app/dispatch.py`, `backend/app/static/app.js`, and `backend/tests/ui.test.cjs`. These are existing files, not speculative paths. `validateResult` remains the admission boundary. Before specialized rendering, mirror only the existing backend invariants it relies on: supported metric key/unit pairs; nonempty supported unique scope airports within the contract bounds; optional integer rank 1–22; paired finite numerator/denominator, including required pairs for successful ratio metrics. Reuse the exact current contract enums/limits, not invented stricter rules. Malformed payload fixtures must fail admission before any UI commit. All displayed KPI values come from validated `rows[].metrics[]`; formatting and SVG coordinate scaling are presentation only. Do not compute differences, totals, comparative counts, scores, growth, or evidence classifications in the browser.

| Dispatch | Real returned fields | Presentation and boundary |
|---|---|---|
| `screen_score` | rows with backend `rank`, metrics `screen_score`, `passenger_growth`, `passengers`, `seat_occupancy` | Ordered visual ranking preserving backend row order/rank; label “Traffic pressure score”; no resort/re-score. Missing rank is unranked, unavailable metric includes its reason. |
| `congestion` | metric keys `cancellation_rate`, `diversion_rate`, `departure_delay_minutes`, `taxi_out_minutes`; each has unit/status/value, optional denominator/eligible_count/comparison_direction | Side-by-side aligned airport columns, one metric per row. Display supplied directions only, no invented “4 of 4” conclusion or combined congestion score. Use backend summary verbatim. |
| `long_haul_share` | value in percent, numerator = long-haul performed departures, denominator = eligible performed departures; scope.threshold_miles | Large share plus exact counts and threshold, all explicitly labeled. Missing values/reasons stay unavailable, never approximated. |
| `sfo_pressure` | optional `passenger_growth`, `seat_occupancy`, `passengers`, `seats`, `departures`, operational metrics, `sfo_enplaned_trend`; `sfo_pressure` value is growth gap in percentage points | Prioritize returned growth, occupancy, and growth gap. Contract has NO seat-growth metric: omit seat growth and never derive it by subtraction. Remaining indicators remain accessible in metric details. |
| Monthly series | `series[]`: period YYYYMM, unit, value/status | Small SVG line only when actual returned usable series exists, accessible caption and complete table. Unavailable points break segments; never connect across gaps or label interpolations as facts. No series means an honest no-series note, no dummy chart. |
| Evidence | source_id, date, claim, locator, limitation | Source name resolved from returned sources; show claim and counterevidence together. No supported/inconclusive/contradicted badges inferred from prose; contract has no classification field. |
| Sources | id, name, snapshot_id, period, retrieved_at, safe URL | Name/period in summary; IDs, snapshot, retrieval metadata inside details. Keep full lineage mapping from each metric in methodology. |
| Other metrics | Existing typed rows/series | Existing generic table fallback retained with all data, units, unavailable reasons and provenance. |

Result hierarchy: current/partial/previous status → airport(s), metric, year → backend summary → specialized metric view → evidence/counterevidence → limitations/exclusions → sources/methodology. Population stays visible as concise context. Technical details retain complete metrics/denominators/eligible counts/directions/source IDs and request ID, without placing IDs in the main heading. Error request IDs also move to expandable diagnostics; readable error/recovery remains immediately visible.

The analyst rail has a compact structured composer, not a free-text imitation. A 56px prominent “Choose an analysis” composer strip leads into four always-visible real preset buttons at scroll zero; it does not hide them behind a chooser; adjacent “Edit scope” expands native labeled controls. Existing disabled free-text capability remains accurately explained in advanced/follow-up details; no enabled “Ask anything” field. Scope chips are a read-only summary of the same native form draft; their “Edit” action expands/focuses that form. Avoid duplicate draft state. Main prompts are exactly the four required workflows; the other three live in “More analyses”. After success replace hero with “Analyst”, a “Requested analysis” scope description and returned backend summary; this is an activity record, not fabricated user/assistant messages. Keep Explain and Start new analysis; optional structured follow-ups use existing `runPreset`/`submitScope` paths only.

Context strip uses validated result scope plus at most two already-rendered returned metric values and sources length; labels distinguish current/previous/partial. Do not infer request action from result scope (scope has no action field); use metric plus airports/year. No fake method version. When no result exists, use supported-coverage copy without numeric activity statistics.

Preserve request-generation stale-response guard, abort deadline, manual retry, busy guard, input-change invalidation, picker-selection-no-invalidation-until-Add, one feedback region adjacent to the initiator, full field errors, retained previous result, Explain context lifecycle, Evidence navigation, and existing window globe events. Supported years remain 2023/2024. Start new leaves preserved output explicitly previous and clears explain context. Loading has one genuine pending state, no fabricated stages.

## Execution, commands, and review gates

Owner `Luna` means a gpt-6-luna implementation worker with frontend ownership only. Existing shared edits are preserved; no stage/commit/stash/clean, server management, backend mutation, publication, or deployment. All implementation rows run serially. Each row is RECOMMEND, one outcome, ≤2 named files, ≤150 net text LOC; if it cannot fit, split it into newly reviewed atomic rows before editing. Review/evidence rows are DRAFT, ≤150 LOC, one evidence file. HTML=`backend/app/static/index.html`, CSS=`backend/app/static/styles.css`, JS=`backend/app/static/app.js`, TEST=`backend/tests/ui.test.cjs`, EVID=`docs/frontend/2026-09-27-orbit-ui-review.md` (new). This plan is the only file authored during planning.

Commands from repository root:

- C1: `node --check backend/app/static/app.js`
- C2: `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`
- C3: `git diff --check -- backend/app/static/index.html backend/app/static/styles.css backend/app/static/app.js backend/tests/ui.test.cjs docs/frontend/2026-09-27-orbit-ui-plan.md docs/frontend/2026-09-27-orbit-ui-review.md`
- C4: `shasum -a 256 backend/app/static/index.html backend/app/static/styles.css backend/app/static/app.js backend/app/static/globe.js backend/tests/ui.test.cjs backend/tests/globe.test.cjs`
- C5: CUA browser interaction on the already running `http://127.0.0.1:8000/`; record C6 served-source identity, viewport, named scenario below, observation/screenshot, and actual outcome. CUA is the observable verification operation, not an invented shell screenshot command. No Playwright, injected fake live responses, server start/stop, or unverified physical-mobile claim. Failure cases use deterministic tests; browser failure observations are separately labeled.

C6 is a permitted read-only HTTP check outside CUA; it reads public localhost static bytes only, never browser internals. Run this exact command before/after the browser session (nonzero or network denial blocks identity acceptance):

```sh
python3 - <<'PY'
from pathlib import Path
from hashlib import sha256
from urllib.request import urlopen
for name in ('index.html', 'styles.css', 'app.js', 'globe.js'):
    route = '/' if name == 'index.html' else '/static/' + name
    local = Path('backend/app/static', name).read_bytes()
    with urlopen('http://127.0.0.1:8000' + route, timeout=10) as response:
        served = response.read()
    assert served == local, 'served source mismatch: ' + name
    print(name, sha256(served).hexdigest())
PY
```

| # | Owner | Files | LOC cap | Depends | Single outcome | Check | Reject if |
|---:|---|---|---:|---|---|---|---|
| 0 | UI tester | EVID | 100 | plan approved | Establish actual zoom-test capability | C5: dedicated localhost browser Zoom menu → 200%; capture visible percentage and viewport screenshot; restore 100% and capture indicator | Percentage cannot be observed: record UNVERIFIED, continue implementation/other QA, withhold full acceptance |
| 1 | UI tester | EVID | 100 | 0 | Capture pre-change A/B/C baseline identity | C4, C5 at 1440×900 | Stale identity or missing baseline |
| 2 | Luna | CSS | 100 | 1 | Establish restrained tokens and primitives | C3; contrast/focus audit | Unreadable text or styling controls by color alone |
| 3 | Luna | HTML | 140 | 2 | Establish shell landmarks in mobile source order | C2, C3; inspect header/Earth/rail/result/strip targets | Duplicate IDs or broken existing selector |
| 4 | Luna | CSS | 130 | 3 | Fit desktop shell to ≥1400 viewport | C3, C5 1440/1920 A | Width sum overflows; globe card; empty right rail |
| 5 | Luna | HTML, CSS | 130 | 4 | Make structured composer the primary landing control | C2, C5 A keyboard | Unsupported text input looks enabled; primary action absent |
| 6 | Luna | HTML, CSS | 130 | 5 | Collapse manual scope behind explicit native disclosure | C2, C5 expand/edit | Existing fields/picker/error/help become inaccessible |
| 7 | Luna | JS, TEST | 140 | 6 | Render draft scope summary from existing form | C1, C2 | Chips maintain separate state or trigger query |
| 8 | Luna | JS, TEST | 140 | 7 | Replace hero with truthful active-analysis rail | C1, C2 | Fake transcript; previous result called current |
| 8a | Luna | JS, TEST | 150 | 8 | Mirror typed admission invariants needed by new views | C1, C2 malformed key/unit, rank, scope and ratio-pair fixtures | Contract-valid payload rejected or malformed payload commits |
| 9 | Luna | JS, TEST | 140 | 8a | Give result header conclusion-first hierarchy | C1, C2 | Frontend composes analytical conclusion/count |
| 10 | Luna | JS, TEST | 150 | 9 | Dispatch specialized views with generic fallback | C1, C2 with supported nonspecialized metric fallback | Existing metadata/rows discarded |
| 11 | Luna | JS, TEST | 150 | 10 | Render congestion comparison | C1, C2 complete/partial/zero fixtures | Wrong units, missing reason, derived comparison |
| 12 | Luna | JS, TEST | 150 | 11 | Render backend-ranked screening list | C1, C2 ties/unranked/unavailable fixtures | Client sorting/scoring or investment-score label |
| 13 | Luna | JS, TEST | 140 | 12 | Render long-haul share presentation | C1, C2 supplied counts/missing fixtures | Percent multiplied or fabricated denominator |
| 14 | Luna | JS, TEST | 150 | 13 | Render SFO pressure indicators | C1, C2 missing optional keys/gap fixtures | Seat growth derived; false capacity conclusion |
| 15 | Luna | JS, TEST | 120 | 14 | Prepare accessible series table and no-series state | C1, C2 unavailable/zero/one/no point fixtures | Missing values become zero; underlying table unavailable |
| 15a | Luna | JS, TEST | 150 | 15 | Plot actual series with gaps | C1, C2 segment gaps/zero/one-point fixtures | Gap interpolation or dummy trend |
| 16 | Luna | JS, TEST | 150 | 15a | Present source names with expandable technical lineage | C1, C2 hostile text/URLs/full metadata fixtures | Source IDs lost; unsafe DOM; evidence limitation hidden |
| 17 | Luna | JS, TEST | 150 | 16 | Present genuine request states with adjacent feedback | C1, C2 pending/error/previous/stale/context fixtures | Fake progress, auto retry, lost previous label or focus jump |
| 18 | Luna | JS, TEST | 140 | 17 | Populate context strip from validated result | C1, C2 current/partial/previous/no-result | Recalculated KPI or mismatch with visible result |
| 19 | Luna | CSS | 130 | 18 | Style specialized result hierarchy and rail scrolling | C3, C5 B/C at 1440×900 | KPI clipped; table-first hierarchy; unreachable content |
| 20 | Luna | HTML, CSS | 130 | 19 | Lay out intermediate-width nonmodal drawer | C2, C3 at 1000/1280/1399 | Drawer covers its close control or closed content tabbable |
| 21 | Luna | JS, TEST | 150 | 20 | Implement drawer interaction and breakpoint focus lifecycle | C1, C2; C5 open/Escape/Evidence/resize; result-skip pre-result no-href/no-tab and after-result open-then-focus at 1000–1399px | Focus lost/trapped, automatic focus steal, duplicate result DOM |
| 22 | Luna | CSS | 120 | 21 | Restore narrow-width source-order flow | C3, C5 320/390/768/999 | Sticky overlap, page overflow, fixed-height trap |
| 23 | UI tester | EVID | 150 | 22 | Record four workflow and state interaction evidence | C2, C4, C5 matrix below | Any required runtime case fails or called passed untested |
| 24 | Frontend reviewer | EVID | 150 | 23 | Record ordered design audit on frozen candidate | C1–C4; audit chain below | Level 1 issue; screenshot mismatch; missing contract data |
| 25 | UI tester | EVID | 150 | 24 | Record A/B/C final visual acceptance | C4, C5 screenshot criteria below | Generic dashboard composition or any measured criterion fails |
| 26 | Lead architect | EVID | 150 | 25 | Issue final exact-artifact architecture verdict | C1–C4 plus independent reports | Open finding, stale evidence, changed frozen renderer |

[Atomicity self-check: PASS — each row has one owner, one outcome, one tier, at most two files, and at most 150 net text LOC. Cumulative shared-file edits are sequential.]

Reviewers must first read `~/.claude/knowledge/ui-ux-agent-linting.md`. Design order: `/a11y-audit` → `/state-coverage-audit` → canvas integration Level 7/`3d-perf-audit` (frozen raw-WebGL scope, no unavailable tool claim) → `/motion-review` → `/responsive-check` for overflow/CLS → `/impeccable optimize` audit-only → `/impeccable audit`. Parallax/scroll-cinematic gates are not applicable; no such behavior is introduced. Per-section C5 checks above precede final composition review. No skill execution is claimed merely by listing it.

## Acceptance and evidence matrix

All three screenshots at 1440×900 and 1920×1080 use the same frozen source hashes. Save screenshot evidence under an identified local path. C6 must match the four served frontend files immediately before C5; repeat C4/C6 afterward to detect intervening edits. CUA explicitly reloads the page; this establishes server-byte plus reloaded-page evidence, not an instrumented browser-cache digest. Test result availability against the actual backend; missing datasets are reported as backend-blocked, never replaced with mock success. Synthetic contract fixtures can prove presentation states but are not localhost analytical workflow passes.

**A — Landing:** header ≤64px; globe diameter ≥min(600px, workspace height minus 80px) at 1440×900; no visible rectangular globe boundary. Composer is ≥52px tall, headline/prompt hierarchy precedes one concise disclaimer; four main prompts visible at scroll zero, extra three collapsed. No blank 400px result column. No debug IDs or fabricated counts. Most available workspace area belongs to Earth rather than boxed explanatory copy.

**B — Comparison:** run real LAX/SNA preset through POST /api/query. Header stays small, hero no longer occupies rail. At scroll zero see LAX/SNA heading, backend conclusion, and four comparable metric rows (or individually honest unavailable cells), with paired airport columns. Globe occupies center, mapped LAX/SNA selected state remains visible through real events. Bottom strip repeats same scope and returned values; all source/methodology/limitations content reachable by right-rail scrolling without moving Earth. At 1280 result drawer opens by explicit action, Escape returns focus, Evidence opens the same panel. No overlay can cover the active close button.

**C — SFO:** run real SFO pressure preset. Show SFO/year/partial-status if supplied, returned passenger growth/occupancy/gap prioritization with exact units and unavailable reasons. Actual series yields a small captioned chart and accessible table; absence yields no invented chart. Evidence and counterevidence share visible grouping; limitations remain directly discoverable. No seat-growth KPI unless contract is separately accepted later. Globe highlights SFO through existing event contract.

**Four workflows:** New England screen ranking; LAX/SNA operations; ANC long-haul share; SFO pressure. For each CUA workflow record visible request scope, result status/values, source lineage, limitations, and selected marker state. C2 independently asserts exact rendered-value agreement with injected typed responses and intercepts fetch calls to prove the application uses only /health and /api/query; this is deterministic contract evidence, not live browser-network inspection. Also check the other three presets and generic metric fallback remain usable.

**Interaction stress:** keyboard first-to-last rail controls, skip links and Evidence; 44px controls; focus-visible; 320×740, 390×844, 768×1024, 999×800, 1000×800, 1280×800, 1399×900, 1400×900, 1440×900, 1920×1080; actual 200% browser zoom with measured proof, otherwise UNVERIFIED. Independent rails must scroll by keyboard; closed drawer has no focusable descendants; resizing with drawer focus remains valid. Test draft changes while pending, picker selection versus Add, invalid field edits, duplicate submit suppression, successful then failed/partial query, Start new then Explain disabled, no-series/no-evidence/no-sources, unavailable versus numeric zero. Network failures and malformed-response cases use existing deterministic stubs unless naturally available in localhost; record mode accurately. C2 and static CSS audit verify reduced-motion rules plus the frozen renderer fallback contract without production mutation. Live reduced-motion/fallback checks run only if documented browser controls expose media emulation or WebGL disabling; otherwise those live observations are explicitly UNVERIFIED. They are not mandatory live gates for the unchanged renderer; deterministic coverage remains mandatory and no live-depth claim is made.

## Review loop and definition of done

Before implementation, frontend reviewer and UI tester review this architect-authored plan; parent independently inspects the revised architecture and records acceptance after their findings resolve. Parent owns dispatch, each reviewer returns concise cited findings, no nested helpers. The plan author cannot solely self-certify approval; another duplicate architect plan dispatch is unnecessary. Final implementation requires frontend, UI-tester and independent architect approval bound to identical C4 hashes; changed sources invalidate affected evidence. Existing loop budgets/STALL controls from the approved Earth plan and canonical CEO workflow apply unchanged, not a new repair allowance. Findings are returned to Luna in bounded owned-file fixes; rerun affected checks then full C2 outer gate.

Done means A/B/C visual criteria, four real supported workflows, required state/focus/responsive checks, C1–C6, and all final review findings are closed. Unverified zoom or backend-unavailable workflows remain explicit open acceptance items and block full acceptance, never a 100% claim. A failed zoom preflight does not halt authorized implementation or other achievable QA. No deployment or accepted 2025 backend capability is implied. User-requested optional camera behavior stays deferred without obstructing this delivery. Unknowns to resolve in steps 0–1: current backend workflow availability and actual browser zoom automation reliability.
