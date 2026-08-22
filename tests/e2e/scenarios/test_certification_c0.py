"""C0 contract tests for the independent RM-0006 coordinator."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from certification.coordinator import (
    CertificationError,
    evaluate_case,
    read_jsonl,
    run_batch,
    validate_record,
)
from certification.run_synthetic import HERE, build_manifest


def _fixture():
    cases = read_jsonl(HERE / "fixtures" / "cases.jsonl")
    observations = read_jsonl(HERE / "fixtures" / "observations.jsonl")
    manifest = build_manifest(cases, cycles=[1, 2])
    return manifest, cases, observations


def _artifact_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def test_schemas_and_synthetic_fixture_are_valid():
    manifest, cases, _ = _fixture()
    validate_record("CertificationManifest", manifest)
    for case in cases:
        validate_record("CaseSpec", case)


def test_pass_without_postcondition_evidence_is_impossible():
    manifest, cases, observations = _fixture()
    observation = copy.deepcopy(observations[0])
    observation["probes"] = []
    result = evaluate_case(manifest, cases[0], 1, observation)
    assert result["verdict"] == "fail"
    assert "postcondition_not_proven" in result["failure_reasons"]
    forged = copy.deepcopy(result)
    forged["verdict"] = "pass"
    forged["failure_reasons"] = []
    with pytest.raises(CertificationError, match="postcondition evidence"):
        validate_record("CaseResult", forged)


def test_resume_is_append_only_and_byte_identical_to_clean_run(tmp_path: Path):
    manifest, cases, observations = _fixture()
    resumed = tmp_path / "resumed"
    clean = tmp_path / "clean"

    partial = run_batch(
        output_dir=resumed, manifest=manifest, cases=cases,
        observations=observations, max_new_cases=2,
    )
    before = (resumed / "results.jsonl").read_bytes()
    assert partial["pending"] == 4

    final = run_batch(
        output_dir=resumed, manifest=manifest, cases=cases, observations=observations,
    )
    assert (resumed / "results.jsonl").read_bytes().startswith(before)
    assert final["objective_achieved"] is True

    clean_final = run_batch(
        output_dir=clean, manifest=manifest, cases=cases, observations=observations,
    )
    assert clean_final == final
    assert _artifact_bytes(clean) == _artifact_bytes(resumed)


def test_manifest_and_registry_tampering_fail_closed(tmp_path: Path):
    manifest, cases, observations = _fixture()
    output = tmp_path / "batch"
    run_batch(
        output_dir=output, manifest=manifest, cases=cases,
        observations=observations, max_new_cases=1,
    )
    changed = copy.deepcopy(manifest)
    changed["fixture"] = "changed"
    with pytest.raises(CertificationError, match="immutable artifact differs"):
        run_batch(
            output_dir=output, manifest=changed, cases=cases, observations=observations,
        )

    first = json.loads((output / "results.jsonl").read_text().splitlines()[0])
    with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(first) + "\n")
    with pytest.raises(CertificationError, match="duplicate result registry key"):
        run_batch(
            output_dir=output, manifest=manifest, cases=cases, observations=observations,
        )


def test_resume_repairs_event_after_result_first_crash(tmp_path: Path):
    manifest, cases, observations = _fixture()
    output = tmp_path / "batch"
    run_batch(
        output_dir=output, manifest=manifest, cases=cases,
        observations=observations, max_new_cases=1,
    )
    (output / "events.redacted.jsonl").write_text("", encoding="utf-8")
    summary = run_batch(
        output_dir=output, manifest=manifest, cases=cases, observations=observations,
    )
    assert summary["objective_achieved"] is True
    assert len(read_jsonl(output / "events.redacted.jsonl")) == 6
