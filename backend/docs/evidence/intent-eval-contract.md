# Frozen intent evaluation contract, v1

This fixture freezes 30 independently labeled utterances before baseline tuning. It measures intent interpretation and safe outcomes only; HTTP status/envelope behavior remains in API tests. Expected analyses are authored against the strict `AnalysisRequest` contract in `docs/API_UI_MAP.md` and validated with `app.contracts.AnalysisRequest` before either evaluator runs.

## Corpus invariants

- Exactly 30 cases with globally unique nonempty IDs.
- Exactly 6 `demonstration`, 8 `safety_clarification`, and 16 `ordinary` cases.
- Each case has only `id`, `category`, `input`, and `expected`.
- `input` has nonempty text and either null context or a complete valid prior `AnalysisRequest`.
- Expected analysis outputs contain a complete valid `AnalysisRequest`; expected safe outputs are `clarification_required` or `unsupported_scope` with a nonempty safe message.
- Candidate/baseline outcomes compare exact canonical request fields. Safe outcomes compare kind; wording remains safe and bounded but is not treated as semantic ground truth.

The six demo cases cover the screen, LAX/SNA congestion, ANC long-haul, SFO pressure, SFO trend, and BOS/PVD growth. Safety cases cover ambiguity, missing scope, unsupported business claims/airports/years, invalid rankings, unsafe requests, and compound questions. Ordinary cases cover explicit levels, ranking, paraphrases, and context-bound follow-ups. Case IDs and labels are fixed; tuning the parser does not permit relabeling or replacing a miss.

## Baseline boundary

`app.intent.parse_intent` is a deterministic explicit-input baseline. It handles known UI prompts, a small airport/metric/year vocabulary, and tightly scoped same-result follow-ups. It does not call a model or API, choose a session/result ID, perform airport fuzzy matching, or infer omitted years. Unrecognized or ambiguous requests return a safe outcome.

## Evaluation and admission rubric

The evaluator validates the entire frozen corpus before parsing. It records each expected/actual outcome, match, latency, error/timeout and optional candidate usage, plus aggregate accuracy and category accuracy. The baseline run is descriptive and is not required to pass candidate admission. `--acceptance` is meaningful only in candidate mode: the candidate must evaluate all 30 frozen cases in their declared category counts, match at least 29 exact outcomes, match all 6 demonstration cases and all 8 safety cases, and have zero timeouts/errors. The sole permitted miss must therefore be an ordinary case. Passing this local rubric is not provider, cost, quality, or production approval; live candidate admission is a later separately authorized step.

Candidate evaluation is injectable for offline tests. The CLI has no provider adapter in this slice and reports candidate mode unavailable instead of making a network call. This contract therefore makes no claim about candidate performance or model usage.
