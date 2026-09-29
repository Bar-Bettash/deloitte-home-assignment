"""Recomputing a signed request must reproduce the same result digest.

Follow-ups on another instance rebuild the referenced result from the request in
the context token, so every workflow must be byte-stable across recomputation,
request resolution and interpreter hash seeds.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app import main
from app.context_token import result_digest
from app.contracts import AnalysisRequest
from app.dispatch import dispatch_analysis

WORKFLOWS = {
    "new_england_screen": {"action": "rank", "region": "new_england", "metric": "screen_score"},
    "lax_sna_operations": {"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion"},
    "anc_long_haul": {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share"},
    "sfo_pressure": {"action": "metric", "airports": ["SFO"], "metric": "sfo_pressure"},
    "historical_2024_growth": {
        "action": "rank", "region": "new_england", "metric": "passenger_growth", "year": 2024,
    },
}
BACKEND = Path(__file__).resolve().parents[1]


def _digests() -> dict[str, str]:
    return {
        name: result_digest(dispatch_analysis(AnalysisRequest.model_validate(body), uuid4()))
        for name, body in WORKFLOWS.items()
    }


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_resolved_request_recomputes_identical_digest(name):
    request = AnalysisRequest.model_validate(WORKFLOWS[name])
    first = dispatch_analysis(request, uuid4())
    resolved = main._resolved_request(request, first)
    assert resolved.year == first.scope.year
    assert resolved.bundle_id == first.scope.bundle_id
    if "year" not in WORKFLOWS[name]:
        assert (resolved.year, resolved.bundle_id) == (2025, "annual-2025-r1")
    again = dispatch_analysis(resolved, uuid4())
    assert result_digest(again) == result_digest(first)
    assert again.model_dump(exclude={"result_id", "request_id"}) == first.model_dump(
        exclude={"result_id", "request_id"}
    )


def test_digests_are_stable_across_interpreter_hash_seeds():
    script = (
        "import json; from tests.test_context_determinism import _digests; "
        "print(json.dumps(_digests(), sort_keys=True))"
    )
    outputs = []
    for seed in ("1", "2"):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(BACKEND),
            "PYTHONHASHSEED": seed,
        }
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=BACKEND, env=environment,
            capture_output=True, text=True, timeout=120, check=True,
        )
        outputs.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert outputs[0] == outputs[1] == _digests()
