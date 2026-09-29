# Frontend localhost assessment — 2026-09-27

Entry URL: `http://127.0.0.1:8000/`

Layer entered: actual static frontend served by this checkout's FastAPI application, using its real same-origin query route and packaged historical snapshots.

Determined by: explicit Uvicorn launch with `--app-dir backend`, PID 11773 listening on loopback port 8000, browser DOM, and server access log.

Also listening: unrelated services on 5000, 7000, 8080, 8085, 8088, 5173, 5174 and other development/system ports; none were used.

Flags in effect: `MODEL_RUNTIME_ENABLED=false` explicitly set for this run; no authentication bypass added. Other inherited configuration was not inspected.

Layers NOT exercised: live model/provider, recent 2025 bundle acceptance, source acquisition/refresh, hosted deployment/authentication, independent arithmetic reconciliation.

## Scope and artifact identity

Read, understand, plan and test the frontend. No application code was changed. The parallel session owns backend work. This session owns only the two documents under `docs/frontend/`.

Checkout HEAD: `57dd0117fb8c012d4a772f5180423a833b3b1cee`. Backend work was dirty before and after testing; this is a working-tree observation, not clean-commit certification. Uvicorn ran without auto-reload. Static file hashes were unchanged across the browser run:

| File | SHA-256 |
|---|---|
| `backend/app/static/index.html` | `d34ebade6578281db855788fb4a67593c2a6636add0d512fc8cf414bb50c41fa` |
| `backend/app/static/app.js` | `cd7a0b6cd6d0e16e15def88f66abd37a3f0dba44799b8daa6de23163e1ef21a6` |
| `backend/tests/ui.test.cjs` | `c2f183be9c247ba9afa78c72dcf48a7dff8fff4f9275196bcbb9410c7d223387` |

## Reproduction environment

The existing repository `.venv` fails during site initialization on `._distutils-precedence.pth`; bypassing site initialization also showed no importable Uvicorn there. No environment files were removed or repaired.

The already-existing Python environment below successfully launched the app. The execution sandbox initially denied port binding; the approved tool escalation allowed loopback startup.

```sh
MODEL_RUNTIME_ENABLED=false /private/tmp/deloitte-clean-final-20260927/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
node --test backend/tests/ui.test.cjs
node --check backend/app/static/app.js
```

This environment path is machine-local; it is not a replacement for the documented clean setup. Browser: Chrome via the available computer-use browser controls. Desktop viewport 1280×1189; responsive viewport 390×844, restored after inspection. Screenshots were inspected in-session, not saved as durable artifacts. No generated harness, dependency installation, index mutation or commit was made.

## Observed checks

The Node suite passed **8/8**. Its minimal DOM does not exercise real event listeners, native form validation, layout or browser focus. Syntax checking also passed.

The following are actual browser interactions, not mocked responses:

| Action | Visible observation |
|---|---|
| Open page | Reachability status displayed; server logged `/`, `/static/app.js`, `/health` as 200. |
| ANC preset | 2024, 3,000 miles, **2.37%**, **950 / 40,017**. |
| LAX/SNA preset | Eight metric rows; LAX cancellation **0.81%**, SNA **0.93%**; mixed-picture summary and denominators. |
| New England screening | Explicit **Partial result**; 21 assessed airports, 84 metric rows; PVD first, score **87.00 / 100**. Evidence/counterevidence visible. |
| SFO pressure | 11 metric rows plus 24 monthly points; explicitly says precise unmet demand is not identifiable. |
| SFO trend | **26,054,586** 2024 enplaned passengers; **4.25%** growth; January 2023–December 2024 series. |
| BOS/PVD | **6.58%** and **14.55%** growth respectively. |
| Raw-growth ranking | Partial result, 21 assessed airports; HYA first. Table still leads with screen score, making the selected ranking measure less prominent. |
| Explain latest result | Same growth scope and rows remained; explanation appeared. The server-provided prose is dense and visibly ends mid-word (`passenger_grow`). Backend explanation formatting is a handoff issue, not a frontend edit. |
| Send free text | 503 `ai_unavailable`, clear message, last result labeled **Previous result**, Explain remains available. No live provider claim. |
| Start new analysis | Context-dependent Explain disabled; prior result retained and labeled. |
| ANC scope change | 2023 and 2,500 miles returned **8.52%**, **3,033 / 35,597**, with the changed scope shown. |
| Invalid compare/screen score | 422 `invalid_request`; previous result preserved. Error is generic and not linked to a field. |
| Source disclosure | Snapshot ID, observation period, unknown retrieval time, cited link displayed. |
| Mobile | Document width equals 390px; table overflow stays inside its 346px container (content width 1132px). All measured buttons at least 44px high. |
| Keyboard table access | Region focused with visible focus; ArrowRight changed horizontal scroll. This is a narrow keyboard check, not full accessibility certification. |

These numerical values verify rendered output on this historical snapshot, not independently recalculated aviation statistics. Example request IDs: ANC `02dc53e0-954f-4213-b3f8-da21b6e6f8fd`; LAX/SNA `e34fe492-2df1-413c-8eae-cdded885b95c`; changed ANC scope `eff86475-1af5-4de9-96c6-9b8820e69d2d`.

The core assessment's server access log recorded 13 query calls: 11 HTTP 200, one 503 AI refusal and one 422 invalid scope. A subsequent ANC preset was run successfully to leave the page on a usable result. The browser console inspection returned no captured warnings/errors; that is limited to the tool's capture window. This is server-side route evidence, not a complete browser network trace. Source expansion is local by code inspection; a dedicated network assertion was not captured.

## Reproduced frontend defects

1. **Results and feedback are out of view after a preset.** At desktop scroll position zero, the feedback begins at y=1753 and the result at y=1832, below the 1189px viewport. The result is appended after all forms; no focus/scroll handoff occurs. Reproduce: reload, click ANC, wait until the request ends, inspect the viewport. See `index.html:93` and `app.js:63`.
2. **An irrelevant threshold blocks unrelated analysis.** Choose ANC long haul, enter threshold `0`, switch Metric to Passengers, and click Run selected scope. Native validation focuses the threshold with “Value must be greater than or equal to 1.” No passenger query is submitted, although the hint says the field is used only for long haul. See `index.html:77` and `app.js:229`.
3. **Unsupported choices offer no field-level recovery.** Select Compare, LAX/SNA, Screening score, Run. UI permits the combination, then shows only “Request fields or analysis scope are invalid.” See `index.html:71` and `app.js:223`.
4. **Free text is visibly unavailable but still presented as an active action.** The enabled Send question control predictably returns an error in this configured state. The product should emphasize usable structured workflows while admission is unavailable. Model admission itself belongs to the backend session.

### Step 1 browser reproduction — inactive threshold

In the already-open `http://127.0.0.1:8000/` tab, select **Single-airport metric**, airport **ANC**, metric **Passengers**, set threshold to `0`, then click **Run selected scope**. Focus remains on the threshold spinbutton and the status remains “No analysis has run. Choose an example or submit a scope.” No result or request indicator becomes visible.

This is a browser-visible reproduction of the inactive threshold blocking the action. The request count was **not independently captured**, so the observation does not prove a source-side count of zero; prevention of submission is an inference from the retained focus and unchanged UI. The tab was opened from this checkout, but this browser-only observation does not independently establish the served application revision or server identity.

## Code findings and untested risks

- `app.js:56` treats every 409 as requiring a new analysis. `busy` should instead preserve context and advise waiting; session expiry/mismatch require different recovery. This is code-confirmed, not a live concurrency reproduction.
- `app.js:147` and `app.js:169` hardcode 2023/2024 and at most 22 airports. The parallel backend plan introduces 2025 and a 23-airport cohort. This is a future integration incompatibility, not proof that an accepted 2025 response is currently available.
- `probeHealth()` has no bounded timeout. A hanging fetch can leave its initial status indefinitely; no hanging-network browser test was run.
- Client validation omits several typed-contract invariants. Limit follow-up work to fields needed for safe/correct rendering; do not duplicate the whole server schema in JavaScript.
- Empty `sources` gives a bare heading; empty metric rows have no direct recovery action. These are contract-reachable cases, not observed live dispatcher outputs.
- Full keyboard traversal, screen-reader announcements, 200% zoom, live busy/session-expiry errors, browser network failures/timeouts, and browser-level race tests remain unverified. Existing Node tests cover some state/race behavior only.

## Disposition

The historical structured frontend is usable and passed the exercised flows, with the defects above still present. This assessment does not approve release or complete the assignment's live AI/recent-data requirements. The implementation proposal is in [the frontend plan](2026-09-27-frontend-plan.md).

## Post-implementation localhost addendum

This addendum records a new CUA-only Chrome run against `http://127.0.0.1:8000/` after frontend Steps 2–16 were implemented. It is bound to checkout HEAD `57dd0117fb8c012d4a772f5180423a833b3b1cee` and these working-tree artifacts:

| File | SHA-256 |
|---|---|
| `backend/app/static/index.html` | `0d909da6b2e0c2400a4579a36cf2ca63926da64863b8280d09ceb7312d6c324e` |
| `backend/app/static/app.js` | `2c1ec9c8787b6e8fbc490b771037c100ea66806e318a4b992663e4211f1127b9` |
| `backend/tests/ui.test.cjs` | `1613c8eae44cc63e083222c5002218aa9a3fb343f98f7f68523b730d42145d35` |

The initial capture was 1280×1133. A temporary 390×844 viewport override and a 200% Chrome zoom pass were exercised; zoom and the explicit viewport override were reset afterward. Screenshots were inspected in-session and were not retained. The existing server was used without process or environment inspection.

### Live browser observations

All seven historical presets completed through the same-origin query route: New England screening (request `7806078d-6adf-4654-8343-19e4ee7c806d`), LAX/SNA congestion (`e3a63db0-1b6c-4659-8c7f-bcd129551878`), ANC long-haul (`bb21a77d-344f-40d8-b2a8-ec2fe9325c23`), SFO pressure (`a5fc157f-dd4b-4481-92f5-5233875b532f`), SFO trend (`f977f136-4c6c-4fe2-8f23-d350b4509043`), BOS/PVD growth (`a526f98a-35dc-423a-ae80-b410bfd18072`) and fastest New England growth (`579eb92e-19a1-44c7-bf41-70ea6e4f4e09`). Explain preserved the growth result and returned request `9fcb3928-586a-49a6-9740-dcc567e66559`.

The response did not move focus. Completion exposed a native **View result** button; pointer activation and keyboard `Enter` both moved focus deliberately to the unique `#result-title` heading. While a later preset was loading, the prior output stayed visible under **Previous result**. The free-text textarea and Send button remained disabled after success, field validation, Explain and Start new analysis.

For ANC long-haul, threshold `0` produced the native message “Value must be greater than or equal to 1” and kept focus on the threshold. Switching to Passengers disabled that retained threshold and submitted successfully: 2,660,772 passengers, request `16aac7a3-2770-44f6-868f-5f9c9008a58a`. Compare + LAX/SNA + Screening score was stopped before a request, focused Metric and exposed “This metric is not available for comparisons” in the control's accessible description. The previous request ID stayed unchanged. Changing Metric to Passengers cleared the error immediately and produced 38,192,617 versus 5,339,555, request `ac487682-f30b-489b-856c-c46bf76e74f8`.

At 390×844, the document stayed within the viewport and the wide result remained in its bordered horizontal-scroll region. The visible primary/reset buttons were approximately 44 CSS pixels high in the capture. Focusing the table region showed a visible ring; repeated Right Arrow input shifted the visible columns from Rank/Airport/Metric to Numerator/denominator and Eligible observations. Tab then reached the Sources disclosure, and Space collapsed it. The disclosure had shown source `t100-e046736d0936743c3f6d`, snapshot `t100-09666a46b4108e6393c72af9423ac17913ca99206e75c0bc336d717355c318ac`, observation period 2023–2024 and an **Open cited source (new tab)** link. At 200% zoom, the observed result copy, large passenger counts, negative/percentage-capable table styles, limitations and source disclosure remained readable; the table stayed contained.

### Verification matrix

| Criterion | Route / control | Durable observation and reproduction recipe | Result |
|---|---|---|---|
| Seven historical presets | `/`, buttons under **Run an example** | Activate each preset and wait for status. All seven produced result headings, scope, metrics, limitations, sources and the request IDs listed above. | Pass |
| Threshold activation | `#metric`, `#threshold`, scope submit | Long-haul + `0` is natively rejected; switch Metric to Passengers and submit. The threshold becomes disabled and the request succeeds. | Pass |
| Invalid scope linkage | `#action`, `#airports`, `#metric` | Compare + `LAX, SNA` + Screening score sends no query, focuses Metric and exposes the field message through its accessible description. Directly selecting Passengers clears it; preset/reset clearing passed in the focused retest below. | Pass after focused retest |
| Previous result retention | preset and scope requests | Start another historical request after a success. The previous output remains rendered and is explicitly headed **Previous result** during loading. | Pass |
| Deliberate result navigation | feedback **View result**, `#result-title` | A response leaves focus on the page. Tab to View result and press Enter; focus moves to the result heading. | Pass |
| Disabled unavailable composer | `#question`, Send question | Inspect after success, validation failure, Explain and Start new analysis. Both controls remain disabled in every observed state. | Pass |
| Source disclosure | Sources details | Tab from the table to the source button; Space toggles it. Expanded content exposes identity, snapshot, period and cited link. | Pass |
| Narrow layout and table keyboard access | 390×844, result table | Apply the viewport override, focus the bordered table region and press Right Arrow. Page width stays contained while columns scroll horizontally. | Pass |
| 200% zoom | desktop result | Reset zoom, apply five Chrome zoom increments to 200%, inspect result/table/limitations/source copy, then reset zoom. No observed overlap or document-level horizontal spill. | Pass for observed surface |
| Reduced motion | page | CUA exposed viewport control but no motion-preference override. Static inspection found no animation/transition declarations and only a `prefers-reduced-motion` rule forcing auto scroll; live preference equivalence remains unverified. | Unverified live |
| Busy/session/transport/non-JSON/empty rows/empty sources | Node DOM suite | `node --test backend/tests/ui.test.cjs` passed 19/19, including no retry/context retention, bounded health, empty-row recovery and missing-source recovery. These were not injected into the live server. | Pass in Node only |
| Draft change during a slow request | live historical query | Historical responses completed before an editable mid-request state could be exercised; controls are disabled while busy. The Node generation-guard test passed. | Unverified live |

### Resolved post-implementation finding

**Major — stale field error survives valid preset/reset paths.** Reproduce Compare + `LAX, SNA` + Screening score, submit to show the Metric error, then choose **Start a new analysis** or click **Anchorage long-haul share**. The old visible error remains, and the valid Long-haul share select is still announced as “Metric This metric is not available for comparisons” even though the ANC request succeeds (`377ca4d3-faaa-4f31-84a8-6af7e43dfa18`). Directly changing the Metric select clears the error, but preset and reset paths do not. This violates the Step 4 correction-clears-error acceptance condition and can mislead sighted and assistive-technology users.

`node --check backend/app/static/app.js`, all 19 Node tests and the scoped `git diff --check` passed. Empty-result and missing-source browser states, reduced-motion emulation, live busy/session-expiry/network/timeout failures and a live changed-draft race remain outside this browser evidence. The accepted 2025 backend handoff is still required before Steps 17–22.

### Focused stale-error retest

The Major stale-error finding above was fixed and rechecked after reloading the same localhost route. Fixed artifact hashes: `app.js` `f9f19fe171d5f211fd45163b3bb0dc4fe935d3531571b282a80f074ef4061d35`; `ui.test.cjs` `86017381ba31e7509d0eac71d9da066e876322477c5c0e54b6c3828ea5918474`.

Two independent browser reproductions passed. First, Compare + `LAX, SNA` + Screening score produced the expected field-linked Metric error; **Start a new analysis** then removed both the visible error node and the stale accessible description. Second, the same invalid scope was recreated and **Anchorage long-haul share** was activated; the error nodes disappeared immediately, Metric was announced simply as Long-haul share, and the valid request completed with 2.37% / 950 of 40,017 under request `fafc021f-67d9-4d37-83ce-05303a74a983`. The prior Major finding is **resolved for this artifact**.

`node --check backend/app/static/app.js`, `node --test backend/tests/ui.test.cjs` (**20/20**) and scoped `git diff --check` all passed after the fix. The remaining unverified-live boundaries in the matrix are unchanged.

## Earth redesign Step 1 baseline binding

This is a **pre-Earth-layout baseline receipt**, captured before Steps 2–25. It does not relabel the historical run or focused retest above as new evidence. Approved plan identity: `docs/frontend/2026-09-27-earth-ui-plan.md` SHA-256 `ce12e88c94bb544e7a6795663479c6fe3ac16bd20476c6366de6ab9925749747`.

Route: `http://127.0.0.1:8000/`, opened in a fresh Chrome tab. The normal-window capture was 2048×1289 image pixels; the responsive capture used an explicit 390×844 CSS-pixel viewport override, which was reset immediately afterward.

| Pre-layout frontend artifact | SHA-256 |
|---|---|
| `backend/app/static/index.html` | `0d909da6b2e0c2400a4579a36cf2ca63926da64863b8280d09ceb7312d6c324e` |
| `backend/app/static/app.js` | `f9f19fe171d5f211fd45163b3bb0dc4fe935d3531571b282a80f074ef4061d35` |
| `backend/tests/ui.test.cjs` | `86017381ba31e7509d0eac71d9da066e876322477c5c0e54b6c3828ea5918474` |
| `backend/app/static/styles.css` | Absent at baseline |

Visible desktop baseline: light gray page, centered single-column shell, white bordered cards, two-column preset grid, blue primary scope button, no Earth stage, and initial feedback “No analysis has run.” The first scope card begins below the preset card in the same capture. Visible 390×844 baseline: title wraps to two lines, status and preset cards remain one column, the first three preset controls are readable without horizontal clipping, and no Earth stage exists. These screenshots were inspected in-session and were not saved as durable artifacts.

| Approved design reference | Source identity | SHA-256 |
|---|---|---|
| Screenshot 1 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-6PYRgu.png` | `c6f26bc00879ea84a7c86503e2251806035c68b32bd6a2996860c09c7bcddbbb` |
| Screenshot 2 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-dQwHNo.png` | `fd2c0bb0592f208e90e86ffbc3856516d70feb3196373a60713069050b3db705` |
| Screenshot 3 | `/var/folders/2f/jfz4p2251cj_z1jwwk43x6pm0000gn/T/codex-clipboard-3j0Un0.png` | `c3a8a3f82d034564a47140339ae1f0789d39c14b3dd1ad3c59e8ed3362bea73b` |

The existing focused retest remains the applicable behavior baseline: JavaScript syntax passed and the Node suite passed **20/20** on the hashes above. Step 1 adds identity and visual-comparison anchors only; it makes no new application-behavior claim.
