# Free-text model admission — 2026-09-30

**Admitted:** `gemini-3.8-flash` with adapter SHA-256
`ecd2c0d8508a2006a102fa5ee3dc279632f50f7a08f04129a87966369ac4977f`
(prompt SHA-256 `6dfc577628ba5458a089be0c7b8d222ffe7f6d7bbc253c206c891b0e1b5c9d98`),
from the chat-robustness revision below. It replaces the first admitted adapter
`7046f4504ec467191451525c9ccc0e21ef41ba7e481f6155e94cf88baa616124`, whose results are kept further down.

Free text turns on only where these are set (see `app/README.md`, "Enable free text"):

```
GEMINI_MODEL=gemini-3.8-flash
MODEL_RUNTIME_ENABLED=true
MODEL_ADMITTED_NAME=gemini-3.8-flash
MODEL_ADMITTED_ADAPTER_SHA256=ecd2c0d8508a2006a102fa5ee3dc279632f50f7a08f04129a87966369ac4977f
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

