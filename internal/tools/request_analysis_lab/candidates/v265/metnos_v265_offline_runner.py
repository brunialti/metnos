#!/usr/bin/env python3
"""Offline-only V26.5 adapter/validator runner. It has no inference mode."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates")
HERE = ROOT / "v265"
ADAPTER_PATH = HERE / "metnos_v265_compact_adapter.py"
SCHEMA_PATH = HERE / "metnos_v265_compact.schema.json"
PROMPT_PATH = HERE / "metnos_v265_compact.prompt.txt"
SCHEMA_AUDIT_PATH = HERE / "metnos_v265_compact_schema_audit_result.json"
PROMPT_AUDIT_PATH = HERE / "metnos_v265_compact_prompt_audit_result.json"
PARENT_RUNNER_PATH = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
EXISTING_PROBE_PATH = Path("/tmp/metnos_v264_independent_graph_probe.py")

EXPECTED = {
    ADAPTER_PATH: "3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1",
    SCHEMA_PATH: "76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921",
    PROMPT_PATH: "893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418",
    SCHEMA_AUDIT_PATH: "b3498f513fbfe5382fc89f4d12afc7f00e6311a649f7ca42aee3bd039ac86ff3",
    PROMPT_AUDIT_PATH: "8be621a34ed64a438cf2f299a5ba7f6dca9ca93f517be1d608fd6efae95b6101",
    PARENT_RUNNER_PATH: "6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459",
    EXISTING_PROBE_PATH: "9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a",
}

NETWORK_EVENTS: list[str] = []


def _deny_network(event: str, _args: tuple[Any, ...]) -> None:
    if event.startswith(("socket.", "http.client", "urllib.")):
        NETWORK_EVENTS.append(event)
        raise RuntimeError("V26.5 offline runner forbids network access")


sys.addaudithook(_deny_network)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hash_checks() -> list[dict[str, Any]]:
    return [
        {
            "id": f"hash:{path.name}",
            "pass": path.exists() and expected != "PENDING_ADAPTER_SHA256" and sha(path) == expected,
        }
        for path, expected in EXPECTED.items()
    ]


def self_test() -> dict[str, Any]:
    adapter = load("metnos_v265_adapter", ADAPTER_PATH)
    parent = load("metnos_v265_parent_validator", PARENT_RUNNER_PATH)
    probe = load("metnos_v265_runner_fixtures", EXISTING_PROBE_PATH)
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    decoder_validator = jsonschema.Draft202012Validator(schema)
    tests = _hash_checks()
    positives: list[dict[str, Any]] = []
    original_valid = probe.valid

    def audited_valid(test_id: str, value: dict[str, Any], check: Any = None) -> None:
        compact = adapter.compact_frame(value)
        schema_ok = not list(decoder_validator.iter_errors(compact))
        expanded = adapter.expand_frame(compact)
        synthetic_registry = test_id in {
            "P09_depth_exactly_four_synthetic_registry",
            "P10_diamond_dag_synthetic_registry",
        }
        target_validator = probe.v if synthetic_registry else parent
        parent_ok = target_validator.validate_frame(expanded, probe.SEGMENTS)["valid"]
        passed = schema_ok and expanded == value and parent_ok
        positives.append({
            "id": f"pipeline:{test_id}",
            "pass": passed,
            "validator": "synthetic_probe_registry" if synthetic_registry else "frozen_parent",
        })
        original_valid(test_id, value, check)

    probe.valid = audited_valid
    existing = probe.run()
    tests.extend(positives)

    dependency = probe.frame(probe.location_dependency(), probe.near_projection())
    compact_dependency = adapter.compact_frame(dependency)
    bad_future = copy.deepcopy(compact_dependency)
    bad_future["atoms"][1]["arguments"][1]["source_ordinal"] = 2
    bad_orphan = adapter.compact_frame(probe.frame(probe.location_dependency()))
    for test_id, value in (
        ("fail_closed:future_edge", bad_future),
        ("fail_closed:orphan_dependency", bad_orphan),
    ):
        rejected = False
        try:
            adapter.expand_frame(value)
        except adapter.UnsafeCompactGraph:
            rejected = True
        tests.append({"id": test_id, "pass": rejected})

    schema_audit = json.loads(SCHEMA_AUDIT_PATH.read_text())
    prompt_audit = json.loads(PROMPT_AUDIT_PATH.read_text())
    tests.extend([
        {"id": "schema_audit_green", "pass": schema_audit["summary"]["failed"] == 0},
        {"id": "prompt_audit_green", "pass": prompt_audit["summary"]["failed"] == 0},
        {"id": "existing_probe_125", "pass": existing["summary"]["passed"] == 125},
        {"id": "network_audit_hook_quiet", "pass": NETWORK_EVENTS == []},
        {
            "id": "runner_has_no_endpoint_cli",
            "pass": ("--" + "endpoint") not in inspect.getsource(sys.modules[__name__]),
        },
        {
            "id": "runner_has_no_inference_path",
            "pass": all(
                token not in inspect.getsource(sys.modules[__name__])
                for token in ("chat" + "/completions", "url" + "open(", "Req" + "uest(")
            ),
        },
    ])
    return {
        "version": "metnos.v26.5-offline-runner-self-test/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "summary": {
            "tests": len(tests),
            "passed": sum(item["pass"] for item in tests),
            "failed": sum(not item["pass"] for item in tests),
            "pipeline_positive_roundtrips": len(positives),
        },
        "network_events": NETWORK_EVENTS,
        "tests": tests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    args = parser.parse_args()
    if not args.self_test:
        return 2
    result = self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
