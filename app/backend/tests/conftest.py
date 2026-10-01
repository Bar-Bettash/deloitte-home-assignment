import os

import pytest

# Keep a developer's backend/.env (and its API key) out of every test run.
os.environ["APP_NO_DOTENV"] = "1"

_HOSTING_VARIABLES = (
    "VERCEL", "VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL",
    "ALLOWED_HOSTS", "APP_SIGNING_KEY", "APP_ACCESS_PASSWORD", "MAX_CONCURRENT_QUERIES",
    "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_THINKING_BUDGET",
    "MODEL_RUNTIME_ENABLED", "MODEL_ADMITTED_NAME", "MODEL_ADMITTED_ADAPTER_SHA256",
)


@pytest.fixture(autouse=True)
def _local_environment(monkeypatch):
    """Run every test as a plain loopback process unless the test opts into hosted mode."""
    for variable in _HOSTING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
