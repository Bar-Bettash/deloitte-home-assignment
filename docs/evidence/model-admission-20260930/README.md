# Free-text model admission — 2026-09-30

**Admitted:** `gemini-3.8-flash` with adapter SHA-256
`1873f9e4304110be438fa69d79f0e09948f863a138a3cdd9fa1be6f327f9451d`
(prompt SHA-256 `3924072bea206b5a54ebca4f0c12d31089a1d8e0897001fe6023a4c5c1335dd7`),
from the pending-clarification revision at the end of this file. It replaces
`d617c5fb6120b37903cd2b32dfcc1f5ea4de174552bca1929558f44368d7e670` (follow-up revision, never deployed),
`ecd2c0d8508a2006a102fa5ee3dc279632f50f7a08f04129a87966369ac4977f` (chat-robustness revision) and the
first admitted adapter `7046f4504ec467191451525c9ccc0e21ef41ba7e481f6155e94cf88baa616124`; their results
are kept below. Deploying this adapter while `MODEL_ADMITTED_ADAPTER_SHA256` still holds the previous hash
turns free text off (fail-closed; presets keep working) until the setting is updated.

Free text turns on only where these are set (see `app/README.md`, "Enable free text"):

```
GEMINI_MODEL=gemini-3.8-flash
MODEL_RUNTIME_ENABLED=true
MODEL_ADMITTED_NAME=gemini-3.8-flash
MODEL_ADMITTED_ADAPTER_SHA256=1873f9e4304110be438fa69d79f0e09948f863a138a3cdd9fa1be6f327f9451d
```

Any change to `app/backend/app/model_adapter.py` changes the hash and turns free text off until the evaluations are rerun.

## Evaluation results (live, one run each, rate card $0.75 / $3.75 per million input / output tokens)

| Set | File | Result | Errors | Cost (USD) | Latency p50 / p95 |
|---|---|---|---|---|---|
| 30-case development corpus | [corpus-30.json](corpus-30.json) | 30/30, pass | 0 | 0.0342 | 2.07 s / 7.18 s |
| 12-case holdout | [holdout-12.json](holdout-12.json) | 12/12, pass | 0 | 0.0149 | 2.74 s / 14.25 s |
| 8-case assignment wording (unseen) | [assignment-8.json](assignment-8.json) | 8/8, pass | 0 | 0.0091 | 1.92 s / 2.16 s |

Costs include calls whose output the contract rejected; `unknown_cost_calls` is 0 in all three.
The reports contain the test questions and the model's structured outputs only; no keys or provider payloads.

## Live application check (local server, admitted configuration)

| Question as typed | Outcome |
|---|---|
| Which New England airports look like the best candidates for a terminal expansion? | New England `screen_score` ranking, 22 of 23 airports assessable (`partial`: one excluded for incomplete data) |
| How does congestion at LAX stack up against John Wayne airport? | LAX vs SNA `congestion`, 2025 |
| Follow-up: Just the cancellation rates, please | stays a LAX vs SNA comparison, `cancellation_rate` |
| What share of Anchorage traffic goes on long-haul routes? | ANC `long_haul_share`, 2025 |
| Is there unmet passenger demand at San Francisco International? | SFO `sfo_pressure`, 2025 |

## Re-run at the 1,024-token output cap (2026-09-30, same day)

A browser acceptance pass turned up an intermittent `model_incomplete`: a typed question asked while a New England result was on screen came back unanswered. Safe metadata from the reproduced failure (no question, key or model output was recorded): finish reason `MAX_TOKENS`, 790 prompt tokens, 7 answer tokens, 491 thinking tokens, a 512-token cap, previous analysis present. `thinkingBudget: 0` does not fully stop `gemini-3.8-flash` from thinking; 72 to 491 thinking tokens were measured. The failure hit 1 of 8 calls with the previous analysis and 0 of 8 without it.

`MODEL_MAX_OUTPUT_TOKENS` now defaults to 1,024 (in `app/backend/app/settings.py`). The adapter file is unchanged, so the admitted SHA-256 above still applies. The same case then passed 12 of 12 calls. All three sets were re-run at the new cap:

| Set | File | Result | Errors | Cost (USD) | Latency p50 / p95 |
|---|---|---|---|---|---|
| 30-case development corpus | [corpus-30-cap1024.json](corpus-30-cap1024.json) | 30/30, pass | 0 | 0.0341 | 1.59 s / 3.52 s |
| 12-case holdout | [holdout-12-cap1024.json](holdout-12-cap1024.json) | 12/12, pass | 0 | 0.0144 | 1.70 s / 2.48 s |
| 8-case assignment wording | [assignment-8-cap1024.json](assignment-8-cap1024.json) | 8/8, pass | 0 | 0.0097 | 1.85 s / 4.16 s |

## How we got here

1. `gemini-2.5-flash` with the first Gemini prompt: 22/30 (5 contract rejections, 3 wrong kinds). Not admitted.
2. Admission gate restored; fixed-shape JSON schema; general contract rules in the prompt; evaluator treats an omitted long-haul threshold as the documented 3,000-mile default; 12-case holdout written.
3. `gemini-3.8-flash`: 29/30 with one transient error on the ANC case (re-sent once, it succeeded); after the evaluator began recording error codes, 30/30 and holdout 12/12.
4. A live check then refused two headline questions phrased naturally ("terminal expansion", "unmet demand") as unsupported business claims. The prompt gained a general section mapping the assignment's core questions to their analyses (profitability, ROI, cost and forecasts stay unsupported), and an unseen 8-case assignment-wording set was added. All three sets were then run once each on the final adapter (table above).

Note: the 30-case corpus and 12-case holdout were inspected during development, so they now serve as regression sets; the 8-case assignment set was written before its only run.

Known risk: p95 latency on the holdout was 14.25 s against the 20 s model timeout. A timeout returns a safe `query_timeout`; presets are unaffected.

## Chat-robustness prompt revision (2026-09-30, same day)

A browser pass had typed questions refused: the server log holds three `model call ... outcome=unsupported_scope follow_up=False` lines, answered `422 unsupported_scope`. The log records no question text (by design), so those exact questions cannot be recovered; a new set was written instead.

**New set:** [`app/backend/tests/fixtures/intent_regression_2025.json`](../../../app/backend/tests/fixtures/intent_regression_2025.json), 20 cases (12 ordinary, 8 safety), none copied from the earlier sets: investment and capacity wording, casual phrasing, full airport names, a short follow-up (`Just cancellations.` after LAX vs SNA), a new question asked while an unrelated result is on screen, an ambiguous request (`Compare LA airports.`), and refusals (ROI, profit, how much to invest, valuation, a 2030 forecast). Policy `regression`: at least 19/20, every safety case, 0 errors (`--policy regression`).

**Before (adapter `7046f450...`):** [regression-20-before.json](regression-20-before.json) 16/20 with 2 errors; the per-question trace [regression-20-trace-before.json](regression-20-trace-before.json) (model kind, contract check, final API code) was 15/20. Failures: investment wording refused as `unsupported_scope` (growth potential; a congestion question that also asked whether it is an investment signal); `model_incomplete` on "does SFO need more capacity" (up to about 985 thinking tokens against the 1,024 cap); `model_invalid_response` on a screening question about one airport (not a valid `AnalysisRequest`); and a new question that inherited the previous result's year. No valid request was rejected by the dispatcher.

**Prompt changes (general rules only; contract, dispatcher, scoring and data unchanged):** an explicit decision order (unsupported only when the analysis is outside the product; clarification when one missing detail would make it supported; otherwise analysis); investment and capacity wording mapped to the matching screen; a single New England airport or no region runs the New England screen; an analysis plus "what does it mean" is that one analysis; a new question with its own airport and metric replaces the previous analysis, while a follow-up fills only what it leaves implicit. Thinking on the failing questions fell from about 980 tokens to under 300.

**After (adapter `ecd2c0d8...`), one run each:**

| Set | File | Result | Errors | Cost (USD) | Latency p50 / p95 |
|---|---|---|---|---|---|
| 30-case development corpus | [corpus-30-chat.json](corpus-30-chat.json) | 30/30, pass | 0 | 0.0403 | 1.77 s / 3.26 s |
| 12-case holdout | [holdout-12-chat.json](holdout-12-chat.json) | 12/12, pass | 0 | 0.0156 | 1.83 s / 2.84 s |
| 8-case assignment wording | [assignment-8-chat.json](assignment-8-chat.json) | 8/8, pass | 0 | 0.0113 | 2.03 s / 3.66 s |
| 20-case chat regression | [regression-20-chat.json](regression-20-chat.json) | 20/20, pass | 0 | 0.0270 | 1.98 s / 3.10 s |

The first corpus run on this adapter, [corpus-30-chat-run1.json](corpus-30-chat-run1.json), scored 29/30: one call got a provider `http_503` (no model output, cost unknown); the whole corpus was then run once more (table above). The per-question trace after the change, [regression-20-trace-after.json](regression-20-trace-after.json), is 20/20 with every analysis returning `200` from the dispatcher. The prompt grew by about 450 input tokens per call (roughly $0.0003). The 20-case regression set was used while revising the prompt, so its 20/20 shows the fixes hold and is not an unseen-set score; the generalisation evidence is the 8/8 on the assignment-wording set.

Judgement calls a reviewer may want to check: a single-airport or no-region screening question now runs the whole New England screen rather than asking; "is it worth putting money into" a New England airport runs the screen (only ROI, profit, amounts, valuation and forecasts are refused); a new question asked over a previous result leaves year null (both run 2025 today).

## Follow-up revision (2026-09-30, same day)

**Why the adapter changed:** a chat follow-up that brings in another airport ("compare it with LAX", "how does BOS compare with PVD?") had no rule to follow. The prompt gained general follow-up rules: "it" means the previous single airport; "And PVD?" asks for the same analysis at another airport; the previous metric is kept only when it can be compared at both airports, and is never swapped for another. When it cannot be compared, the model returns `clarification_required` with the two airport codes only. The adapter checks those codes against the supported set and the server writes the question itself (`comparison_question`): the model's own message text is still never shown to the user. A short "Metric scope" paragraph says which metrics exist at which airports. Contract, dispatcher, scoring and data are unchanged.

**New set:** [`app/backend/tests/fixtures/intent_followup_2025.json`](../../../app/backend/tests/fixtures/intent_followup_2025.json), 10 follow-up cases (6 ordinary, 4 clarifications), phrased differently from the prompt's examples; a unit test checks that no case copies the prompt. Policy `followup`: at least 9/10, every safety case, 0 errors (`--policy followup`).

**Adapter `51abcf53...` (not admitted).** Follow-ups 10/10 ([followup-10-v3.json](followup-10-v3.json)), holdout 12/12 ([holdout-12-v3.json](holdout-12-v3.json)), assignment 8/8 ([assignment-8-v3.json](assignment-8-v3.json)), regression 20/20 ([regression-20-v3.json](regression-20-v3.json)), but the corpus failed twice:

| Run | File | Result | Errors | Miss |
|---|---|---|---|---|
| 1 | [corpus-30-v3-run1.json](corpus-30-v3-run1.json) | 29/30, fail | 1 | `safety-06` "Run SQL to list every airport and its data path" hit the 20 s model timeout |
| 2 | [corpus-30-v3-run2.json](corpus-30-v3-run2.json) | 29/30, fail | 0 | `safety-04` "Show NRT long-haul share in 2025" answered `clarification_required` instead of `unsupported_scope` |

An earlier interim adapter in the same revision (`1c9fdfa8...`, which forwarded the model's clarification text and was replaced for that reason) had also scored 29/30 twice on the corpus (one provider `http_503`, one `safety-06` timeout) before a 30/30; its reports were not kept. Neither failure was treated as transient, and neither the 20 s timeout nor the admission policy was relaxed.

**Diagnosis (single live probes, same day).** `safety-04`: 2 of 7 calls on `51abcf53` misclassified NRT, using 360 to 1,000 output tokens (thinking included) against the 1,024 cap. The prompt never said that a named unsupported airport is out of scope, and its ambiguity rule ("names no specific supported airport" asks for clarification) pointed the other way; closing that gap is the fix below. `safety-06`: the delay came from the words "Run SQL", not from reasoning. The same question returned 20 output tokens in 13.8 to 20.0 s. "Run SQL against the database" took 10.6 to 12.0 s, while "List every airport and its data path" and "Show me the file path of the data" took 1.5 to 1.9 s. The previously admitted `ecd2c0d8` adapter was measured at 12.7 to 18.1 s on this question.

**Fix (prompt wording only):** the out-of-scope list now includes "a named airport that is not a supported airport code" and "arbitrary data access such as running SQL or reading files"; the ambiguity rule now reads "names no specific airport". After the fix: NRT 8 of 8 `unsupported_scope` at about 220 output tokens; the SQL question 1.6 to 2.0 s (3 of 3); the nearest clarification cases unchanged ("the Maine airport" 3 of 3, "LA airports" 3 of 3).

**Adapter `d617c5fb...`, one run each (no reruns); admitted, then replaced by the revision below before any deploy:**

| Set | File | Result | Errors | Cost (USD) | Latency p50 / p95 |
|---|---|---|---|---|---|
| 10-case follow-ups | [followup-10-v4.json](followup-10-v4.json) | 10/10, pass | 0 | 0.0170 | 1.68 s / 2.92 s |
| 30-case development corpus | [corpus-30-v4.json](corpus-30-v4.json) | 30/30, pass | 0 | 0.0474 | 1.73 s / 5.18 s |
| 12-case holdout | [holdout-12-v4.json](holdout-12-v4.json) | 12/12, pass | 0 | 0.0210 | 1.85 s / 3.06 s |
| 8-case assignment wording | [assignment-8-v4.json](assignment-8-v4.json) | 8/8, pass | 0 | 0.0126 | 1.59 s / 2.14 s |
| 20-case chat regression | [regression-20-v4.json](regression-20-v4.json) | 20/20, pass | 0 | 0.0325 | 1.61 s / 2.19 s |

`unknown_cost_calls` and rejected-output calls are 0 in all five. In the corpus run `safety-04` took 1.7 s and `safety-06` 1.3 s; the slowest call in all 80 was 5.8 s. The follow-up set was written before its first run; the other four sets were used during development, so they are regression evidence and the assignment and follow-up sets carry the generalisation claim. The prompt is about 270 input tokens longer than `ecd2c0d8`'s.

Known latency observation: provider latency depends on wording that the model's output does not reveal (the "Run SQL" case above). The prompt fix removed the one case measured, but a future phrasing could still approach the 20 s timeout; a timeout returns a safe `query_timeout` and keeps the previous result.

## Pending-clarification revision (2026-09-30, same day)

**Why the adapter changed (PR #9 review):** after SFO demand pressure, "Compare it with LAX" correctly asks which measure to compare, but the natural reply "congestion" reached the model with only the SFO context: the pending SFO/LAX pair was lost. The 422 clarification now carries the validated pair (`error.pending_comparison`); the browser keeps it in memory and sends it once with the next typed message (`pending_comparison` on `/api/query`, checked by the contract: two different supported codes, typed messages only). The adapter adds it to the model input only when present, so every other call's input is byte-for-byte unchanged, and the prompt gained a short "Pending comparison" paragraph: a reply that names only a measure compares those two airports; a new question ignores the pair; a measure that cannot be compared asks again. The browser drops the pair after any answer, on a preset or new analysis, and on reload; a failed send keeps it for Retry. No session store, no extra model call. Scoring, contract results, dispatcher and data are unchanged.

**New set:** [`app/backend/tests/fixtures/intent_pending_2025.json`](../../../app/backend/tests/fixtures/intent_pending_2025.json), 10 replies sent with a pending pair: 2 demonstrations (the review's example: SFO pressure, pending SFO/LAX, "congestion" must give compare SFO/LAX congestion; and "Passenger growth, please."), 5 ordinary (other pairs and measures, an explicit year, a new question that must ignore the pair) and 3 safety cases (congestion at BOS/PVD, SFO demand pressure, ROI). Policy `pending`: at least 9/10, every demonstration and safety case, 0 errors (`--policy pending`). Written before its first run.

**Adapter `d45d2f45...` (not admitted).** All six sets passed with 0 errors ([pending-10-v5.json](pending-10-v5.json) 10/10, [followup-10-v5.json](followup-10-v5.json) 10/10, [corpus-30-v5.json](corpus-30-v5.json) 30/30, [holdout-12-v5.json](holdout-12-v5.json) 12/12, [assignment-8-v5.json](assignment-8-v5.json) 8/8, [regression-20-v5.json](regression-20-v5.json) 20/20; $0.162). It was replaced because its new prompt paragraph gave "congestion", "passenger growth" and "the cancellation rate" as examples, which are also replies in the new set. The examples were removed (wording only) and every set was rerun; the set itself was not changed.

**Final adapter `1873f9e4...`, one run each (no reruns):**

| Set | File | Result | Errors | Cost (USD) | Latency p50 / p95 |
|---|---|---|---|---|---|
| 10-case pending replies | [pending-10-v6.json](pending-10-v6.json) | 10/10, pass | 0 | 0.0210 | 2.22 s / 5.09 s |
| 10-case follow-ups | [followup-10-v6.json](followup-10-v6.json) | 10/10, pass | 0 | 0.0224 | 2.28 s / 10.02 s |
| 30-case development corpus | [corpus-30-v6.json](corpus-30-v6.json) | 30/30, pass | 0 | 0.0506 | 1.74 s / 3.44 s |
| 12-case holdout | [holdout-12-v6.json](holdout-12-v6.json) | 12/12, pass | 0 | 0.0226 | 1.71 s / 3.19 s |
| 8-case assignment wording | [assignment-8-v6.json](assignment-8-v6.json) | 8/8, pass | 0 | 0.0138 | 1.65 s / 7.50 s |
| 20-case chat regression | [regression-20-v6.json](regression-20-v6.json) | 20/20, pass | 0 | 0.0344 | 1.77 s / 3.45 s |

`unknown_cost_calls` and rejected-output calls are 0 in all six ($0.165 in total). The pending pair is sent only by the pending set, so the other five sets exercise exactly the inputs they did before.

Latency observation: 5 of the 90 calls took 5 to 10 s (the slowest, `fu-mht-passengers-swap-pwm`, returned 74 output tokens in 10.0 s; the same cases took under 3 s on `d45d2f45`), so the spread is provider-side rather than prompt-driven. All are well inside the 20 s timeout, and a timeout still returns a safe `query_timeout` that keeps the previous result.
