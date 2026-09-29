# Minimal analysis UI — final localhost QA

Candidate served at `http://127.0.0.1:8000/` in isolated Chrome tab `1084304156`. C6 matched filesystem and served bytes: `index.html` `516c602c…`, `styles.css` `c98b9f90…`, `app.js` `db0b2335…`, `globe.js` `55fc1a3f…`. Console warnings/errors: none. Temporary viewport overrides were reset.

## Live workflow observations

| Workflow | Visible result | Outcome |
|---|---|---|
| New England ranking | Honest `PARTIAL RESULT`; PVD 87.00, PWM 83.50, BOS 80.50; PVC exclusion and limitations visible | Pass |
| LAX/SNA comparison | 0.81/0.93%, 0.29/0.30%, 13.51/14.35 min, 18.66/15.44 min | Pass |
| ANC long haul | 2.37%; 950 long-haul / 40,017 eligible departures | Pass |
| SFO pressure | 3.96% growth, 83.27% occupancy, -0.04 pp gap; returned monthly chart | Pass |

The comparison's “Exact values and methodology” disclosure exposed a semantic table with Measure/LAX/SNA headers and all four rows. “View evidence” moved focus to `#analysis-evidence`. During the next request the retained output was explicitly `PREVIOUS RESULT`; the completed response became `CURRENT RESULT`. No enabled free-text chat, live-flight claim, derived seat-growth claim, or extra analytical endpoint was visible.

## Verification matrix

| Criterion | Route / viewport | Evidence | Result |
|---|---|---|---|
| Minimal 60/40 composition and contained globe | `/`, 1440×900 and 1280×720 | `qa-1440-sfo.png`, `qa-1280-sfo.png`: result hierarchy occupies the left, Earth remains contained on the right, chart and primary facts fit one fold | Pass |
| Four real workflows | `/`, 1440×900 | `qa-1440-ranking.png`, `qa-1440-comparison.png`, `qa-1440-anc.png`, `qa-1440-sfo.png`; visible values listed above | Pass |
| Exact table | `/`, 1440×900 | `qa-1440-comparison-exact.png`; AX exposed table `Exact operational values by airport`, header cells, four metric rows | Pass |
| Result/evidence keyboard path | `/`, 390×844 | Tab order traversed result actions, disclosures, Next question, View globe, canvas, then marker; View evidence focused the evidence heading | Pass |
| Partial/previous/limitations honesty | `/`, 1440×900 | Ranking labeled partial with exclusion/limitations; next request retained it as previous | Pass |
| Mobile flow and scope controls | `/`, 390×844 | `qa-390-sfo-top.png`, `qa-390-sfo-earth.png`, `qa-390-scope.png`; result precedes contained Earth; no observed page-width clipping; Edit scope is a full-row approximately 45-image-pixel target in the native 390px capture | Pass |
| Console | all tested states | Chrome captured zero warning/error entries | Pass |
| 200% zoom / live reduced motion | — | Explicitly excluded from this bounded run because the available CUA controls cannot prove them | Unverified |

## Findings

| Severity | Viewport | Finding |
|---|---|---|
| Cosmetic | 390×844 | Skip links are visibly rendered at rest below the header. They remain functional and do not obstruct content, but add visual clutter to the minimal mobile presentation. |

No Critical or Major browser issues were found. This was an emulated Chromium viewport run, not a physical-device test; touch latency, OS keyboard/autofill, GPU/thermal behavior, and radio conditions remain outside this evidence.

## Screenshot identities

- `qa-1440-landing.png` `3349d65e…`; `qa-1440-ranking.png` `f88ab92b…`; `qa-1440-comparison.png` `748ccfaf…`; `qa-1440-comparison-exact.png` `8c3d34c5…`; `qa-1440-anc.png` `161f23d9…`; `qa-1440-sfo.png` `1cf7d922…`.
- `qa-1280-sfo.png` `28e7a0e8…`; `qa-390-sfo-top.png` `822c9997…`; `qa-390-sfo-earth.png` `828de260…`; `qa-390-scope.png` `ef2c9a99…`.

VERDICT: APPROVED
