"""Static checks on the Vercel packaging files (no network, no deploy)."""

import fnmatch
import json
import subprocess
from pathlib import Path

import tomllib

BACKEND = Path(__file__).resolve().parents[1]
VERCEL_ROOT = BACKEND.parent


def _ignored(relative: str, patterns: list[str]) -> bool:
    parts = relative.split("/")
    for pattern in patterns:
        if pattern.endswith("/"):
            name = pattern.rstrip("/").removeprefix("**/")
            if pattern.startswith("**/"):
                if name in parts[:-1]:
                    return True
            elif relative.startswith(name + "/"):
                return True
        elif pattern.startswith("**/"):
            if fnmatch.fnmatch(parts[-1], pattern[3:]):
                return True
        elif fnmatch.fnmatch(relative, pattern) or ("/" not in pattern and fnmatch.fnmatch(parts[-1], pattern)):
            return True
    return False


def _patterns() -> list[str]:
    lines = (VERCEL_ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_entrypoint_exposes_the_app():
    import index
    from app import main

    assert index.app is main.app


def test_vercel_config_pins_single_entrypoint_and_duration():
    config = json.loads((VERCEL_ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert [build["src"] for build in config["builds"]] == ["backend/index.py"]
    build = config["builds"][0]
    assert build["use"] == "@vercel/python"
    assert build["config"]["maxDuration"] == 60
    assert {"backend/app/**", "backend/data/**", "frontend/**"} <= set(build["config"]["includeFiles"])
    assert config["routes"] == [{"src": "/(.*)", "dest": "/backend/index.py"}]
    assert not (VERCEL_ROOT / "public").exists()


def test_vercelignore_never_excludes_runtime_code_or_data():
    patterns = _patterns()
    tracked = subprocess.run(
        [
            "git", "ls-files", "backend/app", "backend/data", "backend/index.py",
            "backend/requirements.txt", "frontend",
        ],
        cwd=VERCEL_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    tracked = [path for path in tracked if not path.startswith("frontend/tests/")]
    assert any(path.startswith("backend/data/raw/") for path in tracked)
    assert any(path.endswith("source.pdf") for path in tracked)
    assert "frontend/index.html" in tracked
    assert [path for path in tracked if _ignored(path, patterns)] == []
    assert _ignored("backend/tests/test_api.py", patterns)
    assert _ignored("frontend/tests/ui.test.cjs", patterns)
    assert _ignored("backend/app/__pycache__/main.cpython-312.pyc", patterns)
    assert _ignored(".env", patterns)
    assert _ignored("backend/.env", patterns)


def test_runtime_requirements_match_pyproject_and_exclude_test_tools():
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = [
        line for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert sorted(runtime) == sorted(project["dependencies"])
    assert not any(line.startswith("pytest") for line in runtime)
    dev = (BACKEND / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert "-r requirements.txt" in dev
    assert [line for line in dev if line.startswith("pytest")] == project["optional-dependencies"]["test"]


def test_python_version_pin_is_inside_supported_range():
    assert (VERCEL_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["requires-python"] == ">=3.11,<3.13"
