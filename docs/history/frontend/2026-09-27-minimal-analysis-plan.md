# Minimal analysis workspace plan — 2026-09-27

## Decision

Treat the screen as an airport-modernization screening workspace: **choose a supported question → compare the returned signals → verify evidence and limitations → choose the next supported question**. The current runtime spends roughly the first 280 px of a 1440 × 900 result view repeating setup, analyst, status, scope, and result-state copy before the actual LAX/SNA or SFO analysis. The refinement should remove that repetition and improve the existing renderers, without widening the product.

This is a product UI, so the interface should disappear into the task: restrained color, compact fixed type, familiar disclosures and tables, no decorative motion or dashboard theatre.

## Locked boundaries

- Keep the current desktop allocation: approximately **60% analysis / 40% supporting Earth**. Keep the globe fully visible at its default framing and contained at every supported zoom level.
- Preserve the single `POST /api/query` path, existing request shapes, and bare `AnalysisResult`. Source and evidence expansion stays local. Do not add a fetch, endpoint, model call, KPI, forecast, normalization, or client-side calculation.
- The backend remains authoritative for values, units, status, rank, comparison direction, numerator/denominator, eligible counts, summary, evidence, exclusions, limitations, and sources. `40` percent remains `40%`; missing and unavailable never become zero.
- Preserve loading, empty, error, partial, stale-response protection, and labeled **Previous result** recovery. Preserve focus when a response arrives inside an open setup/scope control.
- Do not change `globe.js`, globe tests, backend Python, contracts, calculations, packaged data, or source snapshots.

## Target information order

### Before a result

1. Use one compact mission line: screen supported airport-modernization questions with packaged evidence and explicit limits.
2. Put the four supported analysis choices first. Keep scope and advanced controls in their existing disclosures.
3. Reduce the Earth explanation to one supporting sentence. Remove the duplicate long introduction and duplicate globe description.

### After a result

1. Replace the repeated analyst/status/result blocks with one compact result toolbar: semantic state (`Current`, `Partial`, or `Previous`), human-readable question, airport(s), and year.
2. Keep two immediate actions: **View evidence** and **Choose another question**. Keep **Explain this result** as the supported follow-up action, visually secondary.
3. Render one result heading, the backend summary once, and the primary values or chart. Use human labels such as “Passenger growth gap”; never expose raw keys such as `sfo_pressure`.
4. Put **Evidence and counterevidence** immediately after the primary analysis. Evidence cards remain open so the claim, locator, and counterevidence are visible without another request. Title each card with the matching returned source name plus date when `evidence.source_id` resolves to `sources[].id`; otherwise use “Reviewed source” plus date and retain the unresolved ID inside the card's technical detail. Never hard-code or infer a source title.
5. Show the decision boundary before technical detail. For SFO pressure, keep the existing product limitation visibly stated: the signals do not quantify or establish unmet demand. Preserve every returned limitation and exclusion under a **Limitations and coverage** disclosure; open it automatically for a partial result.
6. Keep sources and source-lineage details collapsed but reachable. Rename internal-sounding UI copy: “Metric values and source lineage” becomes “Values and sources”; “Other returned indicators” becomes “Supporting indicators”; “Technical request details” remains the final diagnostic disclosure.
7. End with a compact **Next supported question** action that reopens the existing choices. Do not generate recommendations or silently carry scope into an unrelated question.

## Renderer behavior

### LAX / SNA operational comparison

- Replace the dense first-view comparison table with four compact measure rows: cancellation rate, diversion rate, departure delay, and taxi out.
- Each row has two horizontal bars, one per airport, on a shared signed scale **within that measure only**. Its domain includes zero: `min(0, pair minimum)` to `max(0, pair maximum)`, with a visible zero axis. This preserves valid negative average departure delays. An all-zero pair renders two zero markers; a constant nonzero pair uses zero and that value as its finite domain. Never share a scale across percent and minute measures. Scaling is display geometry, not a new metric.
- Print the exact backend-formatted value beside every bar. Keep airport text labels and the backend `comparison_direction`; color is supplementary and must not be the only comparison cue.
- Unavailable or non-finite defensive values render as a labeled unavailable/not-plotted row with the backend reason or explicit presentation fallback and no bar. A valid negative value is never rejected. Keep numerator, denominator, eligible observations, direction, and source IDs in the existing disclosure.
- Retain an exact accessible table as the non-visual equivalent. It may sit under **Exact values and methodology**; no information is removed.

### New England screening

- Use the backend order and backend rank unchanged. In the primary view, show compact horizontal `screen_score` bars for only the first three returned rows that have a backend rank and an available score; do not client-sort. Label the section neutrally **Leading traffic-pressure scores**, with airport, rank, and exact score printed in text. This is a screening order, not a recommendation.
- The bars may normalize their CSS width to the largest returned **screen score solely for display**. Do not recompute the score, rank, eligibility, or top-three set. A zero score remains an explicit zero; unavailable/unranked remains separate from the scale.
- Follow the three-row view with a collapsed **All ranked airports (N)** disclosure containing every returned row in backend order and its exact score/supporting metrics. `N` counts only rows with a backend rank and available score; preserve unavailable and unranked rows in the disclosure with explicit labels even though they are excluded from that count. Put passenger growth, passengers, seat occupancy, numerator/denominator where present, and source lineage under each row's details. The single collapsed control must not delay the following reviewed evidence. Keep exclusions and terminal evidence intact.
- For raw passenger-growth ranking, keep the exact existing table in this pass. Do not reuse screen-score bars for a different metric without a separate contract.

### SFO pressure and monthly trend

- Keep passenger growth, seat occupancy, and passenger growth gap as separate values because their units and meanings differ. Do not combine them into a bar chart or composite.
- Format percentage points compactly as `pp` visually, with the full phrase in accessible text. Preserve negative signs and backend precision.
- Render the existing monthly line only when `result.series` is present in the same response and every usable point declares the same `count` unit. Otherwise retain the exact table and state that the series is not plotted. Replace the tiny unlabeled sparkline with a legible SVG that uses a zero-inclusive passenger-count scale and sparse date ticks spanning the returned period. The display domain is `min(0, returned minimum)` to `max(0, returned maximum)` with a constant/all-zero guard; it must never emit invalid SVG geometry. This is presentation mapping of the returned passenger values, not a calculated trend. Put the exact 24-row month/value table in a collapsed **Exact monthly values** disclosure directly below the chart so it remains accessible without pushing evidence several screens down. Missing or unavailable months break the line and remain labeled in the table.
- Do not fetch a series, interpolate a missing month, calculate a trend, or add a forecast.

### Anchorage long-haul and generic results

- Keep the long-haul share as one prominent exact percentage with its returned numerator, denominator, threshold, population, and coverage. A donut would add decoration without a second decision variable, so do not add one.
- Keep the generic exact-value table for other supported metric, compare, and explain results. Do not manufacture a chart merely to make every response visual.

## Accessibility and state contract

- Charts are non-interactive. Each SVG has a unique accessible name and description; exact values remain in adjacent text and a semantic table. Use tabular numerals for compared values.
- State, airport, metric, direction, and availability are conveyed in text. Do not rely on bar length, arrow, or color alone. Maintain WCAG AA contrast and visible focus.
- All buttons and disclosure summaries remain at least 44 × 44 CSS px. The 390 px layout must not require horizontal page scrolling; wide exact tables keep their labeled scroll region.
- The result announcement remains a concise live status. Do not announce the same result through both the toolbar and result heading. Moving to evidence must focus the evidence heading; reopening question selection must focus its first control.
- Loading does not display old values as current. Error/timeout retains the labeled previous analysis. Partial results keep unavailable reasons and automatically expose limitations. An empty usable dataset shows the existing recovery action instead of an empty chart.
- Honor reduced motion by using no chart-entry animation. Bar widths and line geometry render directly from the current response.

## Atomic implementation plan

Every implementation row is owned by `frontend-ux-engineer`, uses the RECOMMEND tier, stays at or below 150 changed lines, and preserves concurrent work. **Atomicity:** each row produces one independently reviewable UI outcome; if it exceeds the cap or requires a second outcome, split it before continuing. The shared verification command after each of steps 1–7 is `node --test backend/tests/ui.test.cjs`; each row's named check adds the focused assertion or observation for that outcome.

[Atomicity self-check: PASS — each implementation step has one owner, at most two files, one outcome, one tier, and a 150-line cap.]

1. **Compact the shell.** Files: `backend/app/static/index.html`, `backend/app/static/styles.css`. Dependencies: none. Shorten the landing copy and consolidate the result toolbar while keeping the 60/40 desktop split and current mobile/globe behavior. Check: static assertions plus screenshots at 1440 × 900, 1280 × 720, and 390 × 844 show the primary result above the fold and the globe contained.
2. **Reorder the result narrative.** Files: `backend/app/static/app.js`, `backend/tests/ui.test.cjs`. Dependency: step 1. Emit one scope heading and summary, then primary analysis, evidence, limitation/coverage disclosure, sources, diagnostics, and the next-question action. Check: focused Node DOM tests assert order, one summary/state label, every payload section, evidence focus, partial disclosure, and previous-result recovery.
3. **Add signed operational pair bars.** Files: `backend/app/static/app.js`, `backend/tests/ui.test.cjs`. Dependency: step 2. Build bars only from the four returned congestion metrics, use a signed zero-axis domain per measure, and retain the exact table/methodology. Check: fixtures cover positive pairs, valid negative delay, mixed-sign, tied, all-zero, constant, unavailable, and missing values; printed values equal the fixture and geometry stays finite.
4. **Add screening-score bars.** Files: `backend/app/static/app.js`, `backend/tests/ui.test.cjs`. Dependency: step 2. Plot the first three returned ranked/available `screen_score` rows without sorting, then preserve the complete backend-order result under **All ranked airports (N)**. Check: fixture mutation changes the exact score and display width without changing backend rank; the leading set follows returned order; `N` counts eligible ranked rows only; zero, unavailable, unranked, and exclusion cases remain distinct and the full disclosure loses no row.
5. **Make SFO values analyst-readable.** Files: `backend/app/static/app.js`, `backend/tests/ui.test.cjs`. Dependency: step 2. Keep the three SFO signals separate, show `pp` visually with a full accessible phrase, and resolve evidence headings through returned sources. Check: signed percentage points, unresolved source fallback, visible unmet-demand boundary, and preservation of source ID/details.
6. **Implement the count-series chart.** Files: `backend/app/static/app.js`, `backend/tests/ui.test.cjs`. Dependency: step 5. Render the labeled zero-inclusive SVG only for a same-response, uniform-`count` series; place the complete table in **Exact monthly values**. Check: 24 points, sparse date ticks, exact units, missing/unavailable gaps, constant/all-zero/negative defensive values, mixed/unsupported units, and no-series cases all produce finite truthful output without another fetch.
7. **Apply restrained presentation.** Files: `backend/app/static/styles.css`, `backend/tests/ui.test.cjs`. Dependencies: steps 3–6. Compact vertical rhythm, align exact values, distinguish bars and zero axes, style disclosures/focus, preserve responsive overflow, and keep 44 px targets. Check: CSS contract assertions plus keyboard/browser inspection; do not edit globe selectors or geometry.
8. **Run ordered gates and independent review.** Files: no product edits unless a gate finds a defect. Dependencies: steps 1–7. Check: `node --check backend/app/static/app.js`; `node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs`; Node fetch interception proves no extra request and one query per submit. Then run read-only accessibility → state coverage → motion → responsive → load-performance if triggered → Impeccable review, followed by independent architecture and browser QA. Capture keyboard and screenshots for the four presets at desktop and narrow widths; browser evidence owns visual acceptance, while Node interception owns request-count evidence.

## Acceptance tests

- At 1440 × 900, the result heading, backend summary, and primary comparison/value area are visible without scrolling. The page has one current/partial/previous label and one rendered backend summary.
- The desktop analysis/Earth allocation remains approximately 60/40; the full globe is visible at default framing, stays inside its panel through supported zoom, and mobile order remains analysis then Earth.
- LAX/SNA shows four paired measures. For every measure, exact displayed values equal the response; both bars share a signed, zero-inclusive scale only with each other; minute and percentage measures never share a scale. Negative delay values remain valid and visibly positioned across the zero axis. The exact accessible table and all denominator/source details remain available.
- New England's primary bars are the first three returned rows with backend rank and available score, in response order with no client sort, under the neutral **Leading traffic-pressure scores** label. **All ranked airports (N)** preserves every returned row and exact supporting value; `N` counts eligible ranked rows only while unavailable and unranked rows remain explicitly labeled rather than plotted as zero. Mutating a fixture's returned score changes only its exact label and display width; it does not change or recompute backend rank.
- SFO shows three separately labeled signals; `percentage_points` is visibly compact and fully spoken. A monthly chart appears only when that response contains usable series points; its zero-inclusive scale remains finite for constant, all-zero, and negative defensive fixtures; missing months break the line; its collapsed exact-values table preserves every returned period, value, unit, and unavailable state.
- ANC shows the exact returned share, numerator, denominator, threshold, population, and coverage with no derived visualization.
- Evidence is reachable in one action and appears before source diagnostics. Resolved evidence titles use their returned source names rather than developer-facing IDs; unresolved IDs remain available inside the evidence technical detail. SFO's unmet-demand boundary remains visible. Every returned evidence item, exclusion, limitation, source, snapshot, period, retrieval value, and request ID remains reachable without a network call.
- Keyboard-only use reaches every action and disclosure in DOM order, exposes visible focus, moves focus correctly for evidence/question actions, and retains focus in an open setup/scope form when a response rerenders.
- Loading, empty, non-2xx, network error, partial, late response, and error-after-success tests keep their existing truthful states. No chart appears for absent usable data; a failed request preserves the prior result as **Previous result**.
- Node fetch interception shows no new endpoint or external request: one initial health probe and one `POST /api/query` per submitted analysis or explicit explanation. Browser QA confirms the visible submit/recovery behavior but does not claim unexposed network-byte equality.

## Stop conditions

Stop this refinement if a proposed visual requires cross-unit normalization, a new calculated KPI, a new payload field, another request, or a globe/backend change. Keep the exact-value renderer instead. This pass is complete when the analyst can reach the supported comparison quickly, verify its evidence and limits, and select the next supported question without losing any contract data.
