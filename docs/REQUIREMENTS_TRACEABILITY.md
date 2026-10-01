# Requirements traceability

Mapping of the Deloitte brief to the submitted implementation.

| Brief requirement | Type | Implementation | Evidence | Status |
|---|---|---|---|---|
| Analyst-facing airport investment assistant | Required | Web UI with presets, chat, scope controls, visual results, and explanations | [README](../README.md) | PASS |
| Use public aviation data | Required | BTS T-100, BTS On-Time, FAA, DataSF, curated public terminal evidence | [Architecture](ARCHITECTURE.md) | PASS |
| New England terminal-expansion candidates | Required | Deterministic traffic-pressure ranking with transparent 40/30/30 components and exclusions | `calculations/screen.py` | PASS |
| LAX vs SNA congestion | Required | Four side-by-side operational indicators with no arbitrary composite winner | `calculations/operations.py` | PASS |
| ANC long-haul share | Required | ≥3,000-mile eligible departures ÷ all eligible departures; threshold is adjustable | `calculations/long_haul.py` | PASS |
| SFO unmet demand / why | Required | Passenger-vs-seat growth pressure, occupancy, monthly trend, and delay-cause context; latent demand limitation stated explicitly | `calculations/sfo.py` | PASS |
| Clear methodology / deterministic scoring | Required | Calculations and ranking live in Python/DuckDB; model never computes numbers | [Architecture](ARCHITECTURE.md) | PASS |
| Reasoning and explainability | Required | Deterministic summaries, **Explain this result**, sources, exclusions, and limitations | [Validation](VALIDATION.md) | PASS |
| Natural-language questions and follow-ups | Required | One constrained Gemini intent call; signed prior-result context; clarification for ambiguous comparisons | `model_adapter.py`, `context_token.py` | PASS |
| Assumptions and uncertainty | Required | Missing data excluded; source populations disclosed; unsupported ROI/latent-demand claims refused | [README](../README.md#limits) | PASS |
| Design / architecture documentation | Required | Concise system, data, AI-boundary, security, and deployment diagrams | [Architecture](ARCHITECTURE.md) | PASS |
| Runnable source repository / demo | Required | FastAPI + static frontend + bundled accepted data; Vercel deployment | [Run instructions](../app/README.md) | PASS |
| Voice interaction | Bonus | Browser speech recognition for input and speech synthesis for read-aloud; typed fallback | [Architecture](ARCHITECTURE.md#voice) | PASS |

All required items are covered. The app intentionally answers unsupported business claims with explicit limitations rather than fabricating evidence.
