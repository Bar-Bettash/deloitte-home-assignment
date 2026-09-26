# Spec: Airport Investment Intelligence local demo

**Status: plan only.** No app, adapter, calculation or browser journey is built or runtime-proven. The root [README](../../../README.md) is canonical for formulas, source contracts, supported periods, evidence gates and atomic build steps. This spec records the product boundary without duplicating those rules.

## Outcome and architecture

Build a small local AI-powered analyst assistant for New England terminal-expansion diligence, LAX/SNA operational comparison, ANC long-haul scheduled passenger-service flight share, and SFO demand-pressure assessment.

Historical CY2023/24 data is used only within the README period table. Terminal evidence is checked as of its review date and separated from historical traffic. Profitability and quantitative unmet demand remain `not_identifiable`; the assistant names missing inputs and offers supported indicators. Prototype acceptance does not establish those business quantities.

One Python/FastAPI process serves the API and plain browser chat/results page. DuckDB reads Parquet; SQLite stores selected context and prior results. One constrained model call resolves language to validated workflow arguments. Backend code calculates and renders explanations. Deterministic parsing and quick prompts provide fallback. No external database, queue, distributed coordination, map or separate frontend build.

See the README [architecture diagrams](../../../README.md#small-runnable-architecture), [source contracts](../../../README.md#source-status-and-exact-runtime-contracts), [calculation rules](../../../README.md#calculations-and-answer-rules), [period table](../../../README.md#supported-periods-and-follow-ups), and [25 atomic steps](../../../README.md#atomic-build-sequence). Steps name owner, file targets, dependencies, outcome and completion check; no fixed schedule applies.

## Assignment traceability

| Requirement | Planned artifact / check |
|---|---|
| Working public API | Real DataSF CSV → validated snapshot → SFO calculation, steps 5–8. |
| Rank and compare | Deterministic formulas and independent arithmetic checks, steps 13–18. |
| Explain reasoning/uncertainty | Templates showing lineage, coverage, assumptions, limits and evidence, steps 16 and 23. |
| AI-powered agent | Real model response → validated intent → backend result, steps 20 and 25. |
| Conversational follow-ups | Stored context, allowed periods, supported and unsupported follow-up proof, steps 19 and 25. |
| Chat interface | One browser page and four workflows, steps 2, 8 and 23. |
| Source code and architecture note | Runnable app and short scoring/tradeoffs/AI guide, steps 1 and 24. |

## Acceptance and open evidence

Plan approval is separate from qualification and prototype acceptance. FAA/T-100 acquisition metadata, 23 on-time archives and all 22 terminal-evidence reviews remain open, alongside every application component. No placeholder or blocked result counts as a demonstrated calculation.

Prototype acceptance requires the README step 25 checks and required dependent source gates. Terminal review must cover all 22 airports with supported eligible/excluded conclusions and explicit unassessable rows where evidence is insufficient; it does not guarantee an eligible investment exists. Delivered code and guide must retain the profitability/unmet-demand limitation after the bounded prototype passes.
