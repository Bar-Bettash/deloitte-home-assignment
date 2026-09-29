# Vercel package check (offline, 2026-09-29)

**Scope.** Local measurement of what a Vercel Python deployment of `backend/` would ship, and a
read-only-filesystem run of the four assignment workflows from that staged bundle. No Vercel,
OpenAI or BTS host was reachable from this container, so nothing here is deploy evidence.
Every platform fact below that is not a local measurement is marked **RE-VERIFY** and must be
checked against Vercel's current documentation (or a preview deploy) before step V3.

## Packaging files

| File | Purpose |
|---|---|
| `backend/index.py` | `from app.main import app` — the only entrypoint; the host and origin checks are inside `app`. |
| `backend/vercel.json` | Pins `index.py` as the single `@vercel/python` build, `maxDuration: 60`, `includeFiles: ["app/**", "data/**"]`, and routes `/(.*)` to it. No `public/` directory, so no file is served around those checks. |
| `backend/.vercelignore` | Drops `tests/`, `docs/`, `scripts/`, `requirements-dev.txt`, env files and caches. Never matches `app/` or `data/` (`tests/test_packaging.py` checks every tracked file). |
| `backend/requirements.txt` | Runtime pins only (duckdb, fastapi, httpx, pydantic, uvicorn); pytest moved to `requirements-dev.txt`. |
| `backend/.python-version` | `3.12`; `pyproject.toml` allows `>=3.11,<3.13`. The suite passes on 3.11.15 and 3.12.3. |

Vercel project settings (manual, H3): Root Directory = `backend`.

## Measured size

Dependencies were resolved for the Vercel target, not this host:

```
pip install --target /tmp/claude-0/pkgcheck/site --only-binary=:all: \
  --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp \
  -r backend/requirements.txt          # pip 24.0 under CPython 3.12.3
```

The deploy tree was staged from `git ls-files` in `backend/` minus `.vercelignore` matches.

| Component | Files | Bytes (uncompressed) |
|---|---:|---:|
| Dependencies (`site/`, cp312 manylinux wheels) | 530 | 72,863,527 |
| — of which `duckdb` | | 62,824,623 |
| Application tree (`app/`, `data/`, `index.py`, config) | 65 | 13,578,280 |
| — of which `data/` (hash-verified snapshots, FAA PDF, AIP xlsx, evidence) | | 12,278,869 |
| — of which `app/` (code + static UI assets) | | 1,297,699 |
| **Total** | 595 | **86,441,807 (≈ 82.4 MiB)** |
| Same tree, zip (deflate) | | 35,643,607 (≈ 34.0 MiB) |

Python function size limit: 250 MB uncompressed **[RE-VERIFY]** — measured total is about a
third of it.

## Read-only filesystem run

The staged bundle and `site/` were bind-mounted read-only in a private mount namespace (so the
check also holds for root), with `env -i`, `HOME=/nonexistent`, only `TMPDIR` writable, and fake
hosted-mode settings (`APP_SIGNING_KEY`, `ALLOWED_HOSTS=demo.example.com`). The run predates the
removal of the sign-in step; its rows are omitted.
CPython 3.12.3 imported `index` from the bundle and drove it through Starlette's TestClient:

| Check | Result |
|---|---|
| Bundle writable? | no (`os.access(".", W_OK)` false; `touch` refused) |
| `GET /health` | 200 |
| New England screen (year omitted) | 200, `bundle_id` annual-2025-r1, 22 rows |
| LAX/SNA congestion (year omitted) | 200, annual-2025-r1, 2 rows |
| ANC long-haul (year omitted) | 200, annual-2025-r1, ANC 999/36040 |
| ANC explain via `airport_context` cookie (recomputed + digest-checked) | 200 |
| SFO pressure (year omitted) | 200, annual-2025-r1 |
| Files written to `TMPDIR` | none |

DuckDB reads Parquet with an in-memory connection and needed neither `HOME` nor a writable
directory, so the optional `app/duck.py` workaround (V2b) was **not** added.

## Runtime behaviour relevant to Vercel

- **State.** No per-user server state. Follow-up context is the HMAC-signed `airport_context`
  cookie; any instance with the same `APP_SIGNING_KEY` verifies it and recomputes the result
  (digest-checked; `tests/test_context_determinism.py` also checks two hash seeds).
- **Concurrency.** `MAX_CONCURRENT_QUERIES` (default 4 hosted, 1 local) is per instance. A
  timed-out or cancelled analysis keeps running in its worker thread and holds its slot until it
  finishes; on Vercel such a worker is bounded by the function's `maxDuration` (60 s) **[RE-VERIFY
  that the instance is frozen/reclaimed after the response]**, so an abandoned slot can reduce
  that instance's capacity until then. Callers see `409 busy` and can retry.
- **Spend.** No process-local budget. The OpenAI project must have a hard monthly budget (H2).
- **Hosting.** Hosted mode (on Vercel, or with `ALLOWED_HOSTS` set) fails closed with 503 on
  everything except `/health` unless `APP_SIGNING_KEY` (32–512 bytes) is set. Generate a key with
  `python -c "import secrets;print(secrets.token_urlsafe(48))"`. Rotating the key invalidates
  every follow-up context cookie; there is no key list.

## RE-VERIFY before deploy (network was blocked)

1. `builds` + `routes` in `vercel.json` is still accepted for `@vercel/python`, and
   `config.maxDuration` / `config.includeFiles` are honoured there. Fallback if not: move the
   entrypoint to `backend/api/index.py` (add `backend/` to `sys.path`), use
   `"functions": {"api/index.py": {"maxDuration": 60, "includeFiles": "{app,data}/**"}}` and
   `"rewrites": [{"source": "/(.*)", "destination": "/api/index"}]`.
2. Python 3.12 is a supported Vercel runtime and is selected from `.python-version` (or
   `pyproject.toml`) under the chosen config style; otherwise 3.11 is also supported by the code.
3. Function size limit (250 MB uncompressed assumed) and `maxDuration` ceiling for the plan.
4. System variables `VERCEL`, `VERCEL_URL`, `VERCEL_BRANCH_URL`,
   `VERCEL_PROJECT_PRODUCTION_URL` are exposed to the function at runtime and hold bare
   hostnames (the app also tolerates a scheme prefix). Custom domains must be listed in
   `ALLOWED_HOSTS`.
5. The `Host` header seen by the function is the public hostname and `Origin` is `https://…`
   (hosted POSTs require an exact same-origin match, HTTPS on Vercel).
6. Deployment Protection decision (H3) and that `Cache-Control: no-store` responses are not
   cached at the edge.
7. The function filesystem is read-only apart from `/tmp` (the local run above assumes this).
