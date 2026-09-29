import hashlib
import json
from pathlib import Path

import pytest
from app.sources.bundle import (
    BundleContext,
    BundleError,
    SnapshotRef,
    _json_object,
    _parse_source,
    _validate_aip_scope,
    load_bundle,
    main,
)


def _snapshot(*, source="t100", years: tuple[int, ...] = (2023, 2024)) -> SnapshotRef:
    return SnapshotRef(
        source=source,
        snapshot_id=f"{source}-2024",
        manifest_sha256="a" * 64,
        content_sha256="b" * 64,
        manifest_path=Path("raw/t100/manifest.json"),
        data_path=Path("raw/t100/data.parquet"),
        years=years,
        vintage_id="2024-final",
        publication_status="published",
    )


def _context(*, sources=None, cohort=("BOS", "PVD"), baseline=2023, comparison=2024):
    return BundleContext(
        bundle_id="historical-2024",
        manifest_sha256="c" * 64,
        baseline_year=baseline,
        comparison_year=comparison,
        cohort=cohort,
        sources={"t100": _snapshot()} if sources is None else sources,
        evidence_path=Path("evidence.json"),
        evidence_sha256="d" * 64,
    )


def _source_value(source="t100", years=None, **changes):
    suffix = "source.pdf" if source == "faa" else "source.xlsx" if source == "aip" else "data.parquet"
    value = {
        "source": source,
        "snapshot_id": f"{source}-2024",
        "manifest_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "manifest_path": f"raw/{source}/manifest.json",
        "data_path": f"raw/{source}/{suffix}",
        "years": ([2023, 2024] if source in {"datasf", "t100"} else [2024]) if years is None else years,
        "vintage_id": "2024-final",
        "publication_status": "published",
    }
    value.update(changes)
    return value


def _core_sources():
    return [_source_value(source) for source in ("datasf", "t100", "ontime", "faa")]


def _bundle_value(**changes):
    value = {
        "schema_version": 1,
        "bundle_id": "historical-2024",
        "baseline_year": 2023,
        "comparison_year": 2024,
        "cohort": ["BOS", "PVD"],
        "sources": _core_sources(),
        "evidence_path": "evidence.json",
        "evidence_sha256": "d" * 64,
    }
    value.update(changes)
    return value


def _write_bundle(root, value, *, aip_fiscal_year=None) -> bytes:
    digest = lambda content: hashlib.sha256(content).hexdigest()
    for ref in value["sources"]:
        content = f'{ref["source"]}-content'.encode()
        data_path = root / ref["data_path"]
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_bytes(content)
        hash_key = "parquet_sha256" if ref["source"] in {"t100", "ontime"} else "content_sha256"
        source_manifest = {"snapshot_id": ref["snapshot_id"], hash_key: digest(content)}
        if ref["source"] == "aip":
            source_manifest["fiscal_year"] = (
                value["comparison_year"] if aip_fiscal_year is None else aip_fiscal_year
            )
        source_raw = json.dumps(source_manifest, sort_keys=True).encode()
        manifest_path = root / ref["manifest_path"]
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(source_raw)
        ref["manifest_sha256"] = digest(source_raw)
        ref["content_sha256"] = digest(content)
    evidence = b'{"reviewed":true}'
    evidence_path = root / value["evidence_path"]
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(evidence)
    value["evidence_sha256"] = digest(evidence)
    raw = json.dumps(value, sort_keys=True).encode()
    path = root / "bundles" / value["bundle_id"] / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return raw


def test_constructs_frozen_bundle_value_objects() -> None:
    context = _context()
    assert context.cohort == ("BOS", "PVD")
    assert context.sources["t100"].years == (2023, 2024)
    with pytest.raises(AttributeError):
        context.bundle_id = "changed"


@pytest.mark.parametrize("years", [[], (), (2024, 2023), (2024, 2024), (True,), (1899,), (2101,)])
def test_snapshot_rejects_invalid_year_tuples(years) -> None:
    with pytest.raises(BundleError, match="snapshot years"):
        _snapshot(years=years)


@pytest.mark.parametrize(
    ("baseline", "comparison"),
    [(2024, 2024), (2025, 2024), (True, 2024), (1899, 2024), (2024, 2101)],
)
def test_bundle_rejects_invalid_years(baseline, comparison) -> None:
    with pytest.raises(BundleError, match="bundle years"):
        _context(baseline=baseline, comparison=comparison)


def test_bundle_requires_tuple_cohort() -> None:
    with pytest.raises(BundleError, match="cohort must be a tuple"):
        _context(cohort=["BOS", "PVD"])


def test_source_mapping_is_copied_and_immutable() -> None:
    original = {"t100": _snapshot()}
    context = _context(sources=original)
    original.clear()
    assert tuple(context.sources) == ("t100",)
    with pytest.raises(TypeError):
        context.sources["faa"] = _snapshot(years=(2024,))


def test_parse_source_returns_confined_typed_reference(tmp_path) -> None:
    source = _parse_source(_source_value(), tmp_path, {2023, 2024})
    assert source.years == (2023, 2024)
    assert source.manifest_path == tmp_path / "raw/t100/manifest.json"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"years": (2023, 2024)}, "periods"),
        ({"years": [2022]}, "periods"),
        ({"manifest_path": "../manifest.json"}, "escapes"),
        ({"content_sha256": "A" * 64}, "checksum"),
        ({"source": 100}, "identity"),
    ],
)
def test_parse_source_rejects_invalid_fields(tmp_path, change, error) -> None:
    with pytest.raises(BundleError, match=error):
        _parse_source(_source_value(**change), tmp_path, {2023, 2024})


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b"\xff"])
def test_json_object_rejects_invalid_or_nonobject_values(raw) -> None:
    with pytest.raises(BundleError):
        _json_object(raw, "source manifest")


def test_aip_scope_accepts_matching_reference_and_manifest() -> None:
    context = _context(sources={"aip": _snapshot(source="aip", years=(2024,))})
    _validate_aip_scope(context, {"fiscal_year": 2024})


@pytest.mark.parametrize(
    ("years", "fiscal_year"),
    [((2023,), 2024), ((2024,), 2023)],
)
def test_aip_scope_rejects_contradictory_years(years, fiscal_year) -> None:
    context = _context(sources={"aip": _snapshot(source="aip", years=years)})
    with pytest.raises(BundleError, match="AIP period"):
        _validate_aip_scope(context, {"fiscal_year": fiscal_year})


def test_aip_scope_is_optional() -> None:
    _validate_aip_scope(_context(), {})


def test_load_bundle_parses_exact_manifest_and_binds_raw_hash(tmp_path) -> None:
    raw = _write_bundle(tmp_path, _bundle_value())
    context = load_bundle("historical-2024", data_root=tmp_path)
    assert context.manifest_sha256 == hashlib.sha256(raw).hexdigest()
    assert context.cohort == ("BOS", "PVD")
    assert tuple(context.sources) == ("datasf", "t100", "ontime", "faa")


@pytest.mark.parametrize(
    "kind",
    ["extra-key", "schema", "cohort", "duplicate-source", "unknown-source"],
)
def test_load_bundle_rejects_malformed_manifest(tmp_path, kind) -> None:
    value = _bundle_value()
    if kind == "extra-key":
        value["unexpected"] = True
    elif kind == "schema":
        value["schema_version"] = True
    elif kind == "cohort":
        value["cohort"] = ["BOS", 7]
    elif kind == "duplicate-source":
        value["sources"].append(value["sources"][0].copy())
    else:
        value["sources"][0]["source"] = "unknown"
    _write_bundle(tmp_path, value)
    with pytest.raises(BundleError):
        load_bundle("historical-2024", data_root=tmp_path)


def _aip_value(*, years=None):
    source = _source_value(
        source="aip",
        years=[2024] if years is None else years,
    )
    return _bundle_value(sources=[*_core_sources(), source])


def test_load_bundle_accepts_matching_aip_artifacts(tmp_path) -> None:
    _write_bundle(tmp_path, _aip_value())
    assert load_bundle("historical-2024", data_root=tmp_path).sources["aip"].years == (2024,)


@pytest.mark.parametrize(
    ("value", "fiscal_year"),
    [(_aip_value(years=[2023]), 2024), (_aip_value(), 2023)],
)
def test_load_bundle_rejects_aip_scope_mismatch(tmp_path, value, fiscal_year) -> None:
    _write_bundle(tmp_path, value, aip_fiscal_year=fiscal_year)
    with pytest.raises(BundleError, match="AIP period"):
        load_bundle("historical-2024", data_root=tmp_path)


@pytest.mark.parametrize("target", ["data", "evidence"])
def test_load_bundle_rejects_tampered_artifacts(tmp_path, target) -> None:
    value = _aip_value()
    _write_bundle(tmp_path, value)
    path = tmp_path / (value["sources"][0]["data_path"] if target == "data" else value["evidence_path"])
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(BundleError, match=f"{target} checksum"):
        load_bundle("historical-2024", data_root=tmp_path)


def test_load_bundle_accepts_historical_parquet_hash_fields(tmp_path) -> None:
    value = _bundle_value()
    _write_bundle(tmp_path, value)
    for source in ("t100", "ontime"):
        manifest = json.loads((tmp_path / f"raw/{source}/manifest.json").read_bytes())
        assert "parquet_sha256" in manifest
    assert load_bundle("historical-2024", data_root=tmp_path).comparison_year == 2024


def test_load_bundle_rejects_missing_required_source(tmp_path) -> None:
    value = _bundle_value(sources=_core_sources()[:-1])
    _write_bundle(tmp_path, value)
    with pytest.raises(BundleError, match="source set"):
        load_bundle("historical-2024", data_root=tmp_path)


def test_candidate_check_fails_closed_when_manifest_is_missing(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--check-candidate", "annual-2025-r1"], data_root=tmp_path)
    assert exc.value.code == 1
    assert "bundle manifest is unavailable" in capsys.readouterr().err


def test_candidate_check_validates_exact_candidate_without_promotion(tmp_path, capsys) -> None:
    value = _bundle_value(
        bundle_id="annual-2025-r1",
        baseline_year=2024,
        comparison_year=2025,
    )
    for source in value["sources"]:
        source["snapshot_id"] = f'{source["source"]}-2025'
        source["years"] = [2024, 2025] if source["source"] in {"datasf", "t100"} else [2025]
    raw = _write_bundle(tmp_path, value)

    assert main(["--check-candidate", "annual-2025-r1"], data_root=tmp_path) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "bundle_id": "annual-2025-r1",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "valid",
    }
    assert not (tmp_path / "bundles" / "current.json").exists()
