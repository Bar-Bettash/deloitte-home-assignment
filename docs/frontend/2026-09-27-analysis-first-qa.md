# Analysis-first localhost QA — 2026-09-27

Status: APPROVED for the observed matrix after targeted frozen-build recheck. Browser evidence came from a dedicated Chrome/CUA tab at `http://127.0.0.1:8000/`; screenshots are visible in the CUA transcript and are not claimed as durable artifacts.

## Frozen artifact

| File | SHA-256 |
|---|---|
| `backend/app/static/index.html` | `056a997aa469ec9feab9c14276f980ffd30b47ac5df6394dc9e9ecba8761f01a` |
| `backend/app/static/styles.css` | `2115ff41b945ae81aec328a56b2dc2636cdfe7a1164640d0af8c6f2784c6300d` |
| `backend/app/static/app.js` | `b647e85fdbf4bd68bdee8b44ddc89fb255d4d1e103ff1dbcedee0ed015c5d0d5` |
| `backend/app/static/globe.js` | `55fc1a3f5c03b191108a51f718e2dd1d59ac9a8678d107a83ae6d58bfdfef33f` |
| `backend/tests/ui.test.cjs` | `c8cacbaf8dca5d1a080edb54176f777eaeef33793760c09f906a79367bfba2f6` |
| `backend/tests/globe.test.cjs` | `373e534c9d81eb693b3fa58ed56d82f6270779e7d3dbec61bd482613dbb4f681` |

Preflight: read-only probe returned HTTP 200. `lsof` showed `python3.1` PID 11773 listening on `127.0.0.1:8000`. No auth-bypass flag was observed or used. The UI tester did not start, restart, or alter the process.

## Verification Matrix

| Criterion | Route / viewport | Element / selector | Evidence and reproduction | Pass/Fail |
|---|---|---|---|---|
| Four real presets | `/`, 1440×900 | four preset buttons | CUA observations: New England returned 21 ranked assessable airports plus PVC exclusion; LAX/SNA returned four operational measures; ANC returned 2.37% from 950/40,017; SFO returned 3.96%, 83.27%, and -0.04 percentage points. No values were injected. | Pass |
| Analysis-first desktop split | `/`, 1440×900 | `#analysis-workspace`, `#earth-stage` | Frozen DOM geometry after reload: 828px analysis and 552px Earth, exactly 60/40 of the 1380px content width; screenshot shows result title, conclusion, and primary indicators above the fold. At 1280: 732/488; at 1920: 1116/744. | Pass |
| Desktop overflow | `/`, 1280/1440/1920 | document root | DOM observations: `scrollWidth === clientWidth` at each tested desktop width. | Pass |
| Mobile order and overflow | `/`, 390×844 | `#result-panel`, `#analysis-evidence`, `#view-globe`, `#earth-stage` | Frozen DOM order after SFO: result top 326, Evidence 2432, View globe 3731, Earth 3795; `scrollWidth=clientWidth=390`. CUA screenshot shows result before Earth. | Pass |
| Previous-result state | `/`, 1440×900 | `#result-panel`, scope controls | Starting LAX/SNA and SFO after a prior success visibly labelled the retained answer `PREVIOUS RESULT`; loading copy said the previous result remained available. Changing the scope draft preserved the result and kept Edit scope expanded. | Pass |
| Error recovery | `/`, 1440×900 | `#airports`, `#feedback` | Entering unsupported `ZZZ` and running showed `Use unique supported three-letter airport codes.`, retained the previous SFO result, kept scope open, focused the field, set `aria-invalid=true`, linked `aria-describedby=airports-error`, and exposed a polite alert. | Pass |
| Result/Evidence focus | `/`, 1440×900 | `#result-title`, `#analysis-evidence` | CUA focus observation: View result focused the visible `CURRENT RESULT` heading; Evidence focused the visible `Evidence and counterevidence` heading. | Pass |
| Globe containment and reset | `/`, 1440×900 and 390×844 | `#earth-canvas`, zoom controls | Real + control reached 120%, 144%, then 150%; screenshots at both widths show the complete limb with margin. + disables at 150%. Reset returned the live label to 100% and disabled reset/zoom-out. | Pass |
| Keyboard focus visibility | `/`, 390×844 and 1440×900 | zoom controls | Tab moved focus to Reset view; CUA screenshots show a visible high-contrast focus ring. Arrow-key rotation was exposed in the canvas accessible description but was not visually quantified. | Pass with limit |
| Touch targets | `/`, 390×844 | enabled buttons/links | Final frozen reload: primary-nav Analyze is 51×44, Scope is 44×44, and Evidence is 58×44 CSS px. The complete visible enabled-control sweep found no target below 44×44; `scrollWidth=clientWidth=390`. | Pass |
| Success control continuity | `/`, 390×844 | SFO preset and setup region | Keyboard activation began with the SFO preset focused. After success, the preset remained rendered inside the open setup region at 374×52 and the result appeared without horizontal overflow or forced result-heading focus. Chromium moved focus to `body` when the triggering preset became disabled during loading, so exact element-focus retention is not claimed. | Pass with limit |
| Contrast | `/`, 1440×900 | body, muted text, result buttons | Rendered computed pairs: body `rgb(242,245,247)` on `rgb(5,9,13)` = 18.24:1; muted `rgb(147,163,178)` on `rgb(5,9,13)` = 7.73:1; button text on `rgb(10,20,32)` = 16.92:1. | Pass for sampled critical text |
| Headings/landmarks/form semantics | `/`, result and error states | document, `#airports` | DOM observation: one H1, sequential H1→H2→H3 hierarchy, one main landmark, labelled controls, and connected error text. Console warning/error log was empty. | Pass |
| Reduced motion | `/` | globe/motion surface | Target browser reported `prefers-reduced-motion: reduce = false`; this harness did not expose a reduced-motion override. | **UNVERIFIED** |
| Browser zoom / 200% text | `/`, target Chrome tab | browser zoom | Five target-tab zoom shortcuts produced no viewport, DPR, or screenshot change. The harness did not expose a browser-zoom capability. | **UNVERIFIED** |

## Findings

No Critical, Major, Minor, or Cosmetic issue was found in the observed final matrix. The prior 42×44 Scope target is closed by the final 44×44 rendered measurement.

Totals: 0 Critical, 0 Major, 0 Minor, 0 Cosmetic.

Screenshot observations in the CUA transcript: initial 1440 state shows all four presets and the complete default globe; LAX/SNA shows all four measures above the fold; SFO shows the conclusion and three priority metrics; 150% desktop and mobile screenshots show the full globe silhouette; 390 mobile shows result → Evidence → View globe → Earth with no page-width overflow; invalid ZZZ shows inline recovery while preserving the previous result.

Targeted final recheck covered the corrected mobile navigation and success-state containing-control behavior. The separately reported final Node receipt was 68/68 passing; this browser-only UI tester did not rerun that suite.

Known boundary: this session ran against an emulated device viewport via Chrome/CUA, not a real physical device. Emulation does not reproduce real touch latency, GPU/thermal throttling, OS keyboard/autofill behavior, or radio conditions. A real-device walkthrough of the core flow remains required before launch.

VERDICT: APPROVED
