import base64
import json
from uuid import uuid4

import pytest
from app.context_token import (
    MAX_TOKEN_BYTES,
    ContextSigner,
    ContextTokenError,
    result_digest,
)
from app.contracts import AnalysisRequest, AnalysisResult

KEY = b"k" * 32
OTHER_KEY = b"o" * 32
REQUEST = AnalysisRequest(action="metric", airports=["PVD"], metric="passengers", year=2024)


def _result(**overrides) -> AnalysisResult:
    values = {
        "result_id": uuid4(), "request_id": uuid4(), "status": "ok",
        "scope": {"airports": ["PVD"], "year": 2024, "metric": "passengers", "population": "fixture"},
        "rows": [{"airport": "PVD", "metrics": [{
            "key": "passengers", "value": 10, "unit": "count", "status": "ok", "source_ids": ["fixture"],
        }]}],
        "summary": "fixture", "series": [],
        "sources": [{"id": "fixture", "name": "Fixture", "url": None, "snapshot_id": "s",
                     "period": "CY2024", "retrieved_at": None}],
        "evidence": [], "exclusions": [], "limitations": [],
    }
    values.update(overrides)
    return AnalysisResult.model_validate(values)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _forge(signer: ContextSigner, payload: object, *, raw: bytes | None = None) -> str:
    """Sign arbitrary payload bytes with the real key to exercise post-MAC validation."""
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    signed = f"v1.{_b64(body)}"
    return f"{signed}.{signer._mac(signed.encode('ascii'))}"


def test_round_trip_returns_bound_claims():
    clock = Clock()
    signer = ContextSigner(KEY, clock=clock)
    result = _result()
    token = signer.sign(result, REQUEST)
    claims = signer.verify(token)
    assert claims.result_id == result.result_id
    assert claims.request == REQUEST
    assert claims.digest == result_digest(result)
    assert claims.expires_at == int(clock.now) + 3600
    assert token.startswith("v1.") and len(token) <= MAX_TOKEN_BYTES


def test_digest_ignores_response_identifiers_only():
    result = _result()
    same = result.model_copy(update={"result_id": uuid4(), "request_id": uuid4()})
    assert result_digest(result) == result_digest(same)
    changed = result.model_copy(update={"summary": "different"})
    assert result_digest(result) != result_digest(changed)


def test_verifier_constructed_separately_with_same_key_accepts():
    token = ContextSigner(KEY).sign(_result(), REQUEST)
    assert ContextSigner(KEY).verify(token).request == REQUEST


def test_wrong_key_is_rejected():
    token = ContextSigner(KEY).sign(_result(), REQUEST)
    with pytest.raises(ContextTokenError):
        ContextSigner(OTHER_KEY).verify(token)


@pytest.mark.parametrize("part", [1, 2])
def test_any_tampered_byte_is_rejected(part):
    token = ContextSigner(KEY).sign(_result(), REQUEST)
    pieces = token.split(".")
    for index in range(len(pieces[part])):
        flipped = pieces[part][index]
        replacement = "A" if flipped != "A" else "B"
        tampered = pieces.copy()
        tampered[part] = pieces[part][:index] + replacement + pieces[part][index + 1:]
        with pytest.raises(ContextTokenError):
            ContextSigner(KEY).verify(".".join(tampered))


def test_expiry_uses_injected_clock():
    clock = Clock()
    signer = ContextSigner(KEY, ttl_seconds=60, clock=clock)
    token = signer.sign(_result(), REQUEST)
    clock.now += 59
    assert signer.verify(token)
    clock.now += 1
    with pytest.raises(ContextTokenError):
        signer.verify(token)


@pytest.mark.parametrize("token", [
    None, "", 123, "v1", "v1.a", "v1.a.b.c", "v2.a.b", "é" * 10, "v1." + "A" * MAX_TOKEN_BYTES + ".x",
])
def test_malformed_version_or_oversized_tokens_are_rejected(token):
    with pytest.raises(ContextTokenError):
        ContextSigner(KEY).verify(token)


def test_other_version_prefix_is_rejected_even_with_valid_mac():
    signer = ContextSigner(KEY)
    token = signer.sign(_result(), REQUEST)
    _, payload, _ = token.split(".")
    signed = f"v2.{payload}"
    with pytest.raises(ContextTokenError):
        signer.verify(f"{signed}.{signer._mac(signed.encode('ascii'))}")


def _payload(**overrides):
    payload = {
        "rid": str(uuid4()),
        "req": REQUEST.model_dump(mode="json", exclude_none=True),
        "dig": "a" * 64,
        "exp": 2_000_000,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("payload", [
    _payload(req={"action": "metric", "airports": ["PVD"], "metric": "screen_score", "year": 2024}),
    _payload(req={"action": "explain"}),
    _payload(req={"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": "2024"}),
    _payload(rid="not-a-uuid"),
    _payload(rid=str(uuid4()).upper()),
    _payload(dig="A" * 64),
    _payload(dig="a" * 63),
    _payload(exp=True),
    _payload(exp=2_000_000.5),
    _payload(exp=999_999),
    {**_payload(), "extra": 1},
    {key: value for key, value in _payload().items() if key != "dig"},
    [1, 2, 3],
])
def test_authentic_but_invalid_payloads_are_rejected(payload):
    signer = ContextSigner(KEY, clock=Clock())
    with pytest.raises(ContextTokenError):
        signer.verify(_forge(signer, payload))


@pytest.mark.parametrize("raw", [
    b'{"rid":"x","rid":"y","req":{},"dig":"","exp":1}',
    b"\xff\xfe",
    b"not json",
])
def test_authentic_but_undecodable_payloads_are_rejected(raw):
    signer = ContextSigner(KEY, clock=Clock())
    with pytest.raises(ContextTokenError):
        signer.verify(_forge(signer, None, raw=raw))


def test_explain_request_and_short_keys_cannot_be_signed():
    with pytest.raises(ValueError):
        ContextSigner(KEY).sign(_result(), AnalysisRequest(action="explain"))
    with pytest.raises(ValueError):
        ContextSigner(b"short")
    with pytest.raises(ValueError):
        ContextSigner("k" * 32)  # type: ignore[arg-type]
