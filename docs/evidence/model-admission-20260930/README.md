# Free-text model admission — 2026-09-30

**Admitted:** `gemini-3.8-flash` with adapter SHA-256
`7046f4504ec467191451525c9ccc0e21ef41ba7e481f6155e94cf88baa616124`
(prompt SHA-256 `e66f8a7c9d0ddb8a4c5a252477a2a30348589259db82bfd53d25ad861ab1659b`).

Free text turns on only where these are set (see `app/README.md`, "Enable free text"):

```
GEMINI_MODEL=gemini-3.8-flash
MODEL_RUNTIME_ENABLED=true
MODEL_ADMITTED_NAME=gemini-3.8-flash
MODEL_ADMITTED_ADAPTER_SHA256=7046f4504ec467191451525c9ccc0e21ef41ba7e481f6155e94cf88baa616124
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

## How we got here

1. `gemini-2.5-flash` with the first Gemini prompt: 22/30 (5 contract rejections, 3 wrong kinds). Not admitted.
2. Admission gate restored; fixed-shape JSON schema; general contract rules in the prompt; evaluator treats an omitted long-haul threshold as the documented 3,000-mile default; 12-case holdout written.
3. `gemini-3.8-flash`: 29/30 with one transient error on the ANC case (re-sent once, it succeeded); after the evaluator began recording error codes, 30/30 and holdout 12/12.
4. A live check then refused two headline questions phrased naturally ("terminal expansion", "unmet demand") as unsupported business claims. The prompt gained a general section mapping the assignment's core questions to their analyses (profitability, ROI, cost and forecasts stay unsupported), and an unseen 8-case assignment-wording set was added. All three sets were then run once each on the final adapter (table above).

Note: the 30-case corpus and 12-case holdout were inspected during development, so they now serve as regression sets; the 8-case assignment set was written before its only run.

Known risk: p95 latency on the holdout was 14.25 s against the 20 s model timeout. A timeout returns a safe `query_timeout`; presets are unaffected.
