#!/usr/bin/env python3
"""Offline author regression and exact-budget tests for V26.5.5."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc
import types


REPOSITORY = Path(__file__).resolve(strict=True).parents[5]
HERE = REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2655"
V2654 = REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2654"
V2651 = REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2651"
FACADE_PATH = HERE / "metnos_v2655_facade.py"
WORKER_PATH = V2654 / "metnos_v2654_worker.py"
MANIFEST_PATH = V2654 / "metnos_v2654_runtime_manifest.json"
FIXTURE_PATH = V2651 / "metnos_v2651_compact_mutation_fixture.json"
BASELINE_SELFTEST_PATH = V2654 / "metnos_v2654_selftest.py"
MUTATION_SUITE_PATH = V2651 / "metnos_v2651_compact_mutation_suite.py"
CONTAMINATION_AUDIT_PATH = V2651 / "metnos_v2651_contamination_audit.py"

EXPECTED = {
    "facade": "fc34ffefc8c77c51a959a1fea5625ea896242afcfff0ed2a41e7f89c14d48d63",
    "worker": "c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22",
    "manifest": "88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391",
    "fixture": "8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e",
    "baseline_selftest": "bb0fcfb1df49d194b5d778fc968854e4163b4f60e03302ec0cf9c6835dac5082",
    "mutation_suite": "c5f0d940d3feac82162d408e10663cbc8fa2b6db20450cc29c32b4554cddf4cb",
    "contamination_audit": "a686fd95970b6cde4af2f29363d7f67bc78736e4c4b7c993c95301b219576779",
}
REQUEST_VERSION = "metnos.v26.5.4-worker-request/1.0"
ENCODED_LIMIT = 1_500_000


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None, "module spec unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_json_script(path: Path) -> dict:
    completed = subprocess.run(
        ["/usr/bin/python3", "-B", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPOSITORY,
        env={
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        timeout=60.0,
    )
    check(completed.returncode == 0, f"offline replay failed: {path.name}")
    check(not completed.stderr, f"offline replay wrote stderr: {path.name}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"offline replay was not JSON: {path.name}") from error
    check(type(result) is dict, f"offline replay root invalid: {path.name}")
    return result


def canonical_request_size(original_request: str, frame: dict) -> int:
    return len(json.dumps(
        {
            "version": REQUEST_VERSION,
            "original_request": original_request,
            "frame": frame,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def exact_boundary_frame(original_request: str, shared: str) -> tuple[dict, dict]:
    empty_size = canonical_request_size(original_request, {"x": []})
    shared_size = len(json.dumps(
        shared, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8"))
    repeated = (ENCODED_LIMIT - empty_size - 2) // (shared_size + 1)
    tail_size = ENCODED_LIMIT - empty_size - repeated * (shared_size + 1)
    if tail_size < 2:
        repeated -= 1
        tail_size += shared_size + 1
    check(2 <= tail_size <= 200_002, "boundary tail is outside string bounds")
    tail = "z" * (tail_size - 2)
    under = {"x": [shared] * repeated + [tail]}
    over = {"x": [shared] * repeated + [tail + "z"]}
    check(canonical_request_size(original_request, under) == ENCODED_LIMIT, "under boundary")
    check(canonical_request_size(original_request, over) == ENCODED_LIMIT + 1, "over boundary")
    check(all(item is shared for item in under["x"][:-1]), "shared strings copied by fixture")
    return under, over


def main() -> None:
    tests: list[str] = []
    paths = {
        "facade": FACADE_PATH,
        "worker": WORKER_PATH,
        "manifest": MANIFEST_PATH,
        "fixture": FIXTURE_PATH,
        "baseline_selftest": BASELINE_SELFTEST_PATH,
        "mutation_suite": MUTATION_SUITE_PATH,
        "contamination_audit": CONTAMINATION_AUDIT_PATH,
    }
    observed = {name: sha(path) for name, path in paths.items()}
    check(observed == EXPECTED, f"durable input hash drift: {observed}")
    tests.append("durable_inputs_and_unchanged_worker_manifest_pinned")

    facade = load_module(FACADE_PATH, "metnos_v2655_facade_selftest")
    public = tuple(
        name for name in facade.__dict__
        if not name.startswith("_") and name != "annotations"
    )
    check(facade.__all__ == ("evaluate",) and public == ("evaluate",), "API drift")
    facade_source = FACADE_PATH.read_text(encoding="utf-8")
    check(EXPECTED["worker"] in facade_source, "facade worker pin missing")
    check(
        "encoded_budget" in facade_source
        and "len(request_bytes) != encoded_budget" in facade_source,
        "exact budget and encoder equality guard missing",
    )
    tests.append("single_api_and_exact_worker_pin")

    baseline = run_json_script(BASELINE_SELFTEST_PATH)
    check(
        baseline.get("status") == "PASS"
        and baseline.get("test_count") == 24
        and baseline.get("network_calls") == baseline.get("model_calls") == 0,
        "V26.5.4 baseline 24/24 did not reproduce",
    )
    tests.append("baseline_v2654_24_of_24_reproduced")

    mutation = run_json_script(MUTATION_SUITE_PATH)
    mutation_summary = mutation.get("summary", {})
    check(
        mutation_summary.get("tests") == mutation_summary.get("passed") == 106
        and mutation_summary.get("failed") == 0
        and mutation.get("network_calls") == mutation.get("model_calls") == 0,
        "mutation replay 106/106 did not reproduce",
    )
    tests.append("mutation_suite_106_of_106_reproduced")

    contamination = run_json_script(CONTAMINATION_AUDIT_PATH)
    contamination_summary = contamination.get("summary", {})
    check(
        contamination_summary.get("tests") == contamination_summary.get("passed") == 15
        and contamination_summary.get("failed") == 0
        and contamination.get("network_calls") == contamination.get("model_calls") == 0,
        "contamination replay 15/15 did not reproduce",
    )
    tests.append("contamination_audit_15_of_15_reproduced")

    fixture = json.loads(FIXTURE_PATH.read_bytes())
    original = " ".join(
        f"s{ordinal:03d}" for ordinal in range(1, fixture["segment_count"] + 1)
    )
    positive_by_id = {item["id"]: item for item in fixture["positive_controls"]}
    for case in fixture["positive_controls"]:
        result = facade.evaluate(original, case["compact_frame"])
        check(
            result["status"] == "evaluated_valid"
            and result["stage"] == case["expected"]["stage"]
            and result["codes"] == case["expected"]["codes"],
            f"candidate positive failed: {case['id']}",
        )
    tests.append("candidate_semantics_positive_6_of_6")

    for case in fixture["native_negative_cases"]:
        result = facade.evaluate(original, case["compact_frame"])
        check(
            result["status"] == "evaluated_invalid"
            and result["stage"] == case["expected"]["stage"]
            and result["codes"] == case["expected"]["codes"],
            f"candidate negative failed: {case['id']}",
        )
    tests.append("candidate_semantics_native_negative_16_of_16")
    check(
        {"P04_fanout_multi_action", "P05_multi_domain", "P08_typed_ambiguity"}
        <= set(positive_by_id),
        "required structural positives absent",
    )
    tests.append("candidate_multi_action_multi_domain_ambiguity_present")

    escape_pattern = '"\\\x00\né😀\u2028'
    boundary_original = escape_pattern * 50
    shared = escape_pattern * 50
    under, over = exact_boundary_frame(boundary_original, shared)
    under_result = facade.evaluate(boundary_original, under)
    check(
        under_result == {
            "status": "evaluated_invalid", "stage": "schema", "codes": ["schema"],
        },
        "exact 1.5 MB request did not cross the facade",
    )
    tests.append("exact_boundary_under_accepted_with_shared_unicode_escapes")

    prior_json = facade._json
    poison_json = types.SimpleNamespace(
        dumps=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("json.dumps reached after over-budget snapshot")
        )
    )
    facade._json = poison_json
    tracemalloc.start()
    try:
        try:
            facade.evaluate(boundary_original, over)
        except ValueError as error:
            check("byte bound" in str(error), "wrong over-boundary error")
        else:
            raise AssertionError("1,500,001 byte request was accepted")
        _current, shared_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        facade._json = prior_json
    check(shared_peak < 500_000, f"over-budget shared peak too large: {shared_peak}")
    tests.append("exact_boundary_over_rejected_before_dump_with_bounded_peak")

    empty_object_size = canonical_request_size("", {})
    key_body = "\x00" * 95
    sample_key_size = len(json.dumps(
        key_body + "0000", ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))
    per_entry_after_first = sample_key_size + 1 + 4 + 1
    key_count = (ENCODED_LIMIT - empty_object_size + 1) // per_entry_after_first
    while key_count > 0:
        key_under = {key_body + f"{index:04d}": None for index in range(key_count)}
        if canonical_request_size("", key_under) <= ENCODED_LIMIT:
            break
        key_count -= 1
    key_over = dict(key_under)
    key_over[key_body + f"{key_count:04d}"] = None
    check(canonical_request_size("", key_over) > ENCODED_LIMIT, "key over-case not over")
    key_result = facade.evaluate("", key_under)
    check(key_result["stage"] == "schema", "under-budget key frame did not run")
    prior_json = facade._json
    facade._json = poison_json
    try:
        try:
            facade.evaluate("", key_over)
        except ValueError:
            pass
        else:
            raise AssertionError("over-budget escaped keys were accepted")
    finally:
        facade._json = prior_json
    tests.append("escaped_object_keys_counted_under_and_over")

    finite_scalars = {
        "x": [None, True, False, 0, -1, 2 ** 63 - 1, 1.0, -0.0, 1e100, 1e-100],
    }
    finite_result = facade.evaluate("é😀\u2028", finite_scalars)
    check(finite_result["stage"] == "schema", "finite scalar budget mismatch")
    tests.append("finite_scalars_and_multibyte_original_exact")

    class StringSubclass(str):
        pass

    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    cycle = {}
    cycle["self"] = cycle
    invalid_values = [
        ("surrogate_original", "\ud800", {}),
        ("surrogate_value", "", {"x": "\udfff"}),
        ("surrogate_key", "", {"\ud800": None}),
        ("nonfinite_nan", "", {"x": math.nan}),
        ("nonfinite_inf", "", {"x": math.inf}),
        ("dict_subclass", "", DictSubclass()),
        ("list_subclass", "", {"x": ListSubclass()}),
        ("cycle", "", cycle),
    ]
    prior_json = facade._json
    facade._json = poison_json
    try:
        for case_id, request_value, frame_value in invalid_values:
            try:
                facade.evaluate(request_value, frame_value)
            except (TypeError, ValueError):
                pass
            else:
                raise AssertionError(f"invalid exact JSON accepted: {case_id}")
        try:
            facade.evaluate(StringSubclass("x"), {})
        except TypeError:
            pass
        else:
            raise AssertionError("original_request string subclass accepted")
    finally:
        facade._json = prior_json
    tests.append("surrogate_nonfinite_subclass_cycle_rejected_before_dump")

    direct = positive_by_id["P01_direct"]["compact_frame"]
    timings_ms = []
    for _ in range(7):
        started = time.perf_counter_ns()
        result = facade.evaluate(original, copy.deepcopy(direct))
        timings_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        check(result["status"] == "evaluated_valid", "overhead result invalid")
    tests.append("candidate_one_shot_overhead_measured")

    report = {
        "version": "metnos.v26.5.5-author-selftest/1.0",
        "status": "PASS",
        "offline": True,
        "network_calls": 0,
        "model_calls": 0,
        "freeze_created": False,
        "gate_created": False,
        "replays": {
            "v2654_author_groups": {"passed": 24, "tests": 24},
            "mutation": {"passed": 106, "tests": 106},
            "contamination": {"passed": 15, "tests": 15},
            "candidate_positive": {"passed": 6, "tests": 6},
            "candidate_native_negative": {"passed": 16, "tests": 16},
        },
        "budget": {
            "encoded_limit": ENCODED_LIMIT,
            "under_bytes": canonical_request_size(boundary_original, under),
            "over_bytes": canonical_request_size(boundary_original, over),
            "shared_over_peak_bytes_before_rejection": shared_peak,
            "escaped_key_under_bytes": canonical_request_size("", key_under),
            "escaped_key_over_bytes": canonical_request_size("", key_over),
        },
        "process_overhead_ms": {
            "samples": len(timings_ms),
            "min": round(min(timings_ms), 3),
            "median": round(statistics.median(timings_ms), 3),
            "max": round(max(timings_ms), 3),
        },
        "test_count": len(tests),
        "tests": tests,
        "hashes": observed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
