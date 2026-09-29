import pytest

_HOSTING_VARIABLES = (
    "VERCEL", "VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL",
    "ALLOWED_HOSTS", "APP_SIGNING_KEY", "MAX_CONCURRENT_QUERIES",
)


@pytest.fixture(autouse=True)
def _local_environment(monkeypatch):
    """Run every test as a plain loopback process unless the test opts into hosted mode."""
    for variable in _HOSTING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
