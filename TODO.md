# Remaining work

This list reflects the state at `fbd2342`. The deterministic workflows, the UI, the stateless follow-up context and the hosting guard are implemented and locally tested. For evidence, see [docs/DESIGN.md](docs/DESIGN.md) and [the integrated acceptance record](backend/docs/evidence/integrated-acceptance-20260929.md). The long step-by-step plan from earlier revisions is kept in Git history.

## Open

- [ ] **Live model admission.** This requires an OpenAI key and network access to `api.openai.com`.
  1. Create an OpenAI project and set a hard monthly budget on it.
  2. Run `python scripts/eval_intents.py --mode candidate --live --acceptance --input-usd-per-mtok <p> --output-usd-per-mtok <p>` from `backend/`. It passes at 29/30 or better.
  3. If it passes, set `MODEL_RUNTIME_ENABLED=true`, `MODEL_ADMITTED_NAME=<OPENAI_MODEL>` and `MODEL_ADMITTED_ADAPTER_SHA256=<sha256 of backend/app/model_adapter.py>`.
  4. Record the result, with no secrets, under `backend/docs/evidence/`.
  5. Demo the four questions as free text, plus one follow-up.
- [ ] **Vercel deploy.** The runbook is in the [README](README.md#deploying-to-vercel-not-yet-done).
  1. Link the project with Root Directory = `backend`.
  2. Set `APP_SIGNING_KEY`, plus `ALLOWED_HOSTS` if you use a custom domain. Set the model variables only after admission.
  3. Resolve the RE-VERIFY items in [vercel-package-check.md](backend/docs/evidence/vercel-package-check.md).
  4. Deploy a preview and run the smoke test: `/health`, the four presets, "Explain" through the cookie, and refusal of a cross-origin POST. Then deploy to production.
- [ ] **Freshness recheck before the demo.** Official sources were last live-checked on 2026-09-27. Re-run `backend/scripts/check_source_status.py` from a host that can reach BTS, DataSF and FAA.
- [ ] **Admission receipt deadline: 2026-10-04T17:17Z.** Re-promoting the bundle after this time needs a new live freshness receipt, because of the 7-day limit in `app/sources/source_check.py`. Serving the already-accepted bundle does not depend on the receipt.

## Known minor items

- Two tests skip in Linux containers (`test_aip.py`, `test_ontime.py`) because they read raw inputs that exist only on the original author's machine.
- Voice input (a bonus in the brief) is not implemented.
