# ADR 001: Bounded local demo architecture

- **Status:** Accepted for the assignment prototype
- **Date:** 2026-09-26
- **Scope:** Design recorded by README step 0.1; implementation remains separate.

## Decision

Build one Python 3.11+ FastAPI process, bound to `127.0.0.1`, serving a static HTML/JavaScript page and the backend API. DuckDB reads locally acquired, validated Parquet snapshots. Keep only the latest successful result and opaque session mapping in bounded in-process memory; keep reviewed airport evidence in a small curated JSON file. Deterministic Python functions perform analysis. A single bounded model call may interpret free text into a validated request; quick prompts remain usable without it.

The browser uses same-origin HTTP with JSON: `GET /` and `/static/app.js`, a one-time `GET /health`, and one synchronous `POST /api/query` for presets, analysis, scope changes and explicit explanation. Success is a typed result; failures use a safe non-2xx error response. Calculation, validation and dispatch modules call one another as Python functions. Source details already in a result render locally. Bulk source refresh is a separate setup command while the server is stopped. These choices follow [API_UI_MAP.md](../API_UI_MAP.md); the wire contract there remains authoritative for fields and behavior.

## Alternatives considered

| Alternative | Decision |
|---|---|
| MongoDB, Supabase, hosted SQL or another external database | Rejected: the single-process demo has no shared or durable user data requirement. They add setup and credentials without serving a required workflow. |
| SQLite for session/result state | Not selected for this revision: bounded, latest-result-only context lives in memory and naturally resets on restart. Add local persistence only if a demonstrated assignment requirement needs it. |
| One service/endpoint per airport, metric or capability | Rejected: the browser needs one query contract; internal work is ordinary function dispatch. |
| Browser calls to public data/model providers, URL-fetch proxy, queue, or separate frontend build | Rejected: provider access and secrets stay server-side; refresh is offline to the running app; queries are bounded synchronous work; static files are sufficient. |
| A client SDK, broad framework, crawler, RAG system or extra runtime agent | Rejected: none is needed to answer the declared demo questions. |

## Tenancy and boundaries

This is a single-user local demo, not an authenticated multi-user service. Bind to loopback. Use the server-generated opaque, HttpOnly session cookie and server-side latest-result binding described in the API/UI contract to isolate browser sessions within that process; do not persist raw chat history. Public airport snapshots are shared read-only inputs, not tenant-owned data. A restart clears session context. Do not expose this configuration as a hosted service; hosting or multi-user access requires a separate design.

## Dependency policy

Keep the runtime dependency set small and pin direct dependencies in the planned backend requirements/config files. Add a package only for a named prototype capability with a concrete caller. Prefer Python standard-library functionality for simple tasks; no database server, browser SDK, job system or frontend build tool. Validate the chosen Python/package versions during step 1.0. Never place provider credentials in browser code, source control or logs.

## Reversibility

The choices are local and replaceable: Parquet snapshots have explicit schemas/metadata; analysis is separated into functions; the browser contract is documented independently; session state is process-local. If a later requirement proves persistence, hosting, or another transport is necessary, preserve the result/request contract and replace only the affected adapter after revising this decision. Do not add that infrastructure preemptively.

## Consistency note

This ADR follows the current README and API/UI contract. The older `docs/history/backend-specs/2026-09-25-airport-investment-plan.md` describes SQLite for selected context/results; that statement conflicts with this revision's in-memory session decision and should be treated as stale unless the master plan is deliberately changed.
