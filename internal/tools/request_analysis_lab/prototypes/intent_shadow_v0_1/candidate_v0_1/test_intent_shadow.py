#!/usr/bin/env python3
"""Deterministic pre-GPU test matrix for candidate 0.1."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from intent_shadow_evaluate import evaluate_saved_batch
from intent_shadow_extract import envelope_status, extract_raw_json
from intent_shadow_io import canonical_json_bytes, strict_json_file
from intent_shadow_normalize import (
    normalize_document,
    project_normalized,
    verify_continuation,
)
from intent_shadow_types import BarrierRegion, OperationGraph
from intent_shadow_projection import build_prompt, build_schema
from intent_shadow_registry import load_frozen_registry, validate_registry_document
from intent_shadow_runner import (
    load_legacy_panel,
    load_query_suite,
    run_offline,
)


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE.parent / "intent_shadow_registry_v0_1.json"
ORACLE_PATH = HERE.parent / "intent_shadow_oracle_v0_1.json"


def _check(name: str, function: Callable[[], None], failures: list[dict[str, str]]) -> None:
    try:
        function()
    except Exception as exc:  # deterministic test report
        failures.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _raw(document: dict[str, Any]) -> bytes:
    return canonical_json_bytes(document)


def _round_trip_all(registry: dict[str, Any]) -> None:
    oracle = strict_json_file(ORACLE_PATH)
    expected = [case["expected"] for case in oracle["cases"]]
    expected.extend(control["expected"] for control in oracle["new_controls"])
    _assert(len(expected) == 124, "expected 124 typed records")
    counts: dict[str, int] = {}
    barrier_documents = 0
    for index, document in enumerate(expected):
        original = deepcopy(document)
        envelope = extract_raw_json(_raw(document), registry)
        _assert(envelope.validation_result.valid, f"invalid oracle case {index}")
        _assert(envelope.technical_failure is None, f"technical oracle case {index}")
        expected_projection = project_normalized(normalize_document(document, registry))
        _assert(
            envelope.projection_result is not None
            and envelope.projection_result.semantic_sha256 == expected_projection.semantic_sha256,
            f"round-trip mismatch {index}",
        )
        _assert(document == original, f"input mutated at {index}")
        if envelope.normalization_result and envelope.normalization_result.continuations:
            barrier_documents += 1
            for continuation in envelope.normalization_result.continuations:
                _assert(
                    verify_continuation(
                        continuation,
                        envelope.normalization_result.document,
                        envelope.registry_sha256,
                    ).valid,
                    f"continuation invalid at {index}",
                )
        counts[document["kind"]] = counts.get(document["kind"], 0) + 1
    _assert(
        counts == {"operation_graph": 87, "system_control": 2, "unrepresentable": 35},
        repr(counts),
    )
    _assert(barrier_documents == 3, f"barrier documents={barrier_documents}")


def _three_roots(registry: dict[str, Any]) -> None:
    documents = (
        ({"kind": "unrepresentable", "reason": "outside_registry"}, "valid_unrepresentable"),
        ({"kind": "system_control", "control": "undo_last_turn"}, "valid_representable"),
        (
            {
                "kind": "operation_graph",
                "body": [
                    {
                        "kind": "barrier",
                        "barrier": "get/approval",
                        "cases": [
                            {
                                "outcome": "approved",
                                "body": [{"kind": "operation", "route": "set/issues"}],
                            }
                        ],
                    }
                ],
            },
            "valid_representable",
        ),
    )
    envelopes = [extract_raw_json(_raw(document), registry) for document, _ in documents]
    for envelope, (_, expected_status) in zip(envelopes, documents, strict=True):
        _assert(envelope_status(envelope) == expected_status, expected_status)
    approval = envelopes[2]
    _assert(approval.normalization_result is not None, "approval not normalized")
    normalization = approval.normalization_result
    _assert(len(normalization.continuations) == 1, "continuation count")
    _assert(isinstance(normalization.document, OperationGraph), "approval root type")
    barrier = normalization.document.body[0]
    _assert(isinstance(barrier, BarrierRegion), "approval barrier type")
    _assert(
        [case.outcome for case in barrier.cases] == ["approved", "rejected"]
        and barrier.cases[1].body == (),
        "omitted rejection not materialized empty",
    )
    continuation = normalization.continuations[0]
    _assert(
        verify_continuation(continuation, normalization.document, approval.registry_sha256).valid,
        "continuation must verify",
    )
    _assert(
        not verify_continuation(
            replace(continuation, outcome_ref="rejected"),
            normalization.document,
            approval.registry_sha256,
        ).valid,
        "tampered continuation accepted",
    )
    for reason in registry["unrepresentable_reasons"]:
        _assert(
            envelope_status(
                extract_raw_json(
                    _raw({"kind": "unrepresentable", "reason": reason}), registry
                )
            )
            == "valid_unrepresentable",
            reason,
        )


def _technical_separation(registry: dict[str, Any]) -> None:
    for raw in (b"{", b'{"kind":"unrepresentable","reason":NaN}', b'{"x":1e999}'):
        envelope = extract_raw_json(raw, registry)
        _assert(envelope_status(envelope) == "technical_invalid", repr(raw))
        _assert(envelope.projection_result is None, "technical failure projected")
    semantically_invalid = extract_raw_json(
        b'{"kind":"unrepresentable","reason":"not_registered"}', registry
    )
    _assert(envelope_status(semantically_invalid) == "document_invalid", "semantic invalidity collapsed")


def _synthetic_registry(registry: dict[str, Any]) -> None:
    import copy

    synthetic = copy.deepcopy(registry)
    synthetic["operations"] = {
        "alpha/things": {
            **next(iter(registry["operations"].values())),
            "verb": "alpha",
            "object": "things",
        }
    }
    control_meta = synthetic["system_controls"].pop("undo_last_turn")
    synthetic["system_controls"] = {"rewind_state": control_meta}
    barrier_meta = synthetic["barriers"].pop("get/approval")
    barrier_meta["outcomes"] = ["yes_state", "no_state"]
    synthetic["barriers"] = {"ask_gate": barrier_meta}
    synthetic["unrepresentable_reasons"] = {"not_modeled": "Not modeled."}
    synthetic["integrity"]["registry_payload_sha256"] = ""
    from intent_shadow_registry import registry_payload_sha256

    synthetic["integrity"]["registry_payload_sha256"] = registry_payload_sha256(synthetic)
    validate_registry_document(synthetic)
    schema_text = json.dumps(build_schema(synthetic), ensure_ascii=False)
    prompt = build_prompt(synthetic)
    for forbidden in ("undo_last_turn", "get/approval", "outside_registry"):
        _assert(forbidden not in schema_text and forbidden not in prompt, forbidden)
    documents = (
        {"kind": "system_control", "control": "rewind_state"},
        {"kind": "unrepresentable", "reason": "not_modeled"},
        {
            "kind": "operation_graph",
            "body": [
                {
                    "kind": "barrier",
                    "barrier": "ask_gate",
                    "cases": [
                        {
                            "outcome": "yes_state",
                            "body": [{"kind": "operation", "route": "alpha/things"}],
                        }
                    ],
                }
            ],
        },
    )
    for document in documents:
        _assert(extract_raw_json(_raw(document), synthetic).validation_result.valid, repr(document))


def _runner_is_query_only() -> None:
    source = (HERE / "intent_shadow_runner.py").read_text(encoding="utf-8")
    forbidden = (
        "intent_shadow_oracle_v0_1.json",
        "metnos_phase1_typed_oracle_v1.overlay.json",
        "intent_shadow_evaluate",
        "build_query_suite",
    )
    for value in forbidden:
        _assert(value not in source, value)
    dry = run_offline(include_legacy=True)
    _assert(len(dry["records"]) == 158, "dry record count")
    _assert(all(record["status"] == "dry_run_no_transport" for record in dry["records"]), "dry transport")
    def walk(value: Any) -> None:
        if type(value) is dict:
            _assert("query" not in value and "original_request" not in value, "batch leaks query field")
            for item in value.values():
                walk(item)
        elif type(value) is list:
            for item in value:
                walk(item)
    walk(dry)


def _fake_batch_and_evaluator(registry: dict[str, Any]) -> None:
    oracle = strict_json_file(ORACLE_PATH)
    gold: dict[str, bytes] = {}
    for case in oracle["cases"]:
        gold[case["query_sha256"]] = _raw(case["expected"])
    for control in oracle["new_controls"]:
        gold[control["query_sha256"]] = _raw(control["expected"])

    def fake(request: dict[str, Any]) -> bytes:
        query = request["input"]["query"]
        return gold[sha256(query.encode("utf-8")).hexdigest()]

    batch = run_offline(fake_transport=fake)
    _assert(len(batch["records"]) == 124, "fake batch count")
    with tempfile.TemporaryDirectory(prefix="intent-shadow-eval-") as temp:
        path = Path(temp) / "batch.json"
        path.write_bytes(json.dumps(batch, ensure_ascii=False, indent=2).encode("utf-8"))
        report = evaluate_saved_batch(path)
        tampered = deepcopy(batch)
        tampered["records"][0]["extraction"]["projection_result"]["semantic_sha256"] = "0" * 64
        tampered_path = Path(temp) / "tampered.json"
        tampered_path.write_bytes(
            json.dumps(tampered, ensure_ascii=False, indent=2).encode("utf-8")
        )
        try:
            evaluate_saved_batch(tampered_path)
        except RuntimeError as exc:
            _assert("evidence mismatch" in str(exc), "tampering failed for wrong reason")
        else:
            raise AssertionError("tampered saved extraction accepted")
    _assert(report["panel_counts"]["canonical_120"]["semantic_exact"] == 120, "canonical exact")
    _assert(report["panel_counts"]["typed_controls_4"]["semantic_exact"] == 4, "typed exact")
    _assert(report["legacy_panel"]["evaluated_here"] is False, "legacy mixed into evaluator")


def main() -> int:
    failures: list[dict[str, str]] = []
    registry, _identity = load_frozen_registry(REGISTRY_PATH)
    tests = (
        ("query_suite", lambda: (load_query_suite(), load_legacy_panel())),
        ("round_trip_124", lambda: _round_trip_all(registry)),
        ("three_roots", lambda: _three_roots(registry)),
        ("technical_separation", lambda: _technical_separation(registry)),
        ("synthetic_registry", lambda: _synthetic_registry(registry)),
        ("runner_query_only", _runner_is_query_only),
        ("fake_batch_evaluator", lambda: _fake_batch_and_evaluator(registry)),
    )
    for name, function in tests:
        _check(name, function, failures)
    report = {
        "status": "ok" if not failures else "error",
        "error_count": len(failures),
        "test_count": len(tests),
        "passed": len(tests) - len(failures),
        "failures": failures,
        "round_trip_expected": 124,
        "panels": {"canonical": 120, "typed_controls": 4, "legacy_separate": 34},
        "gpu_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
