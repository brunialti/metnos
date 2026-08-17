#!/usr/bin/env python3
"""Offline author self-test for the unfrozen V26.5.4 checkpoint."""
from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types


REPOSITORY = Path(__file__).resolve(strict=True).parents[5]
HERE = REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2654"
FACADE_PATH = HERE / "metnos_v2654_facade.py"
WORKER_PATH = HERE / "metnos_v2654_worker.py"
MANIFEST_PATH = HERE / "metnos_v2654_runtime_manifest.json"
FIXTURE_PATH = (
    REPOSITORY
    / "internal/tools/request_analysis_lab/candidates/v2651"
    / "metnos_v2651_compact_mutation_fixture.json"
)
EXPECTED_FIXTURE_SHA256 = (
    "8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e"
)
EXPECTED_WORKER_SHA256 = (
    "c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22"
)
EXPECTED_MANIFEST_SHA256 = (
    "88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391"
)
REQUEST_VERSION = "metnos.v26.5.4-worker-request/1.0"
RESPONSE_VERSION = "metnos.v26.5.4-worker-response/1.0"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None, "module spec unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_worker(envelope: dict) -> tuple[int, dict, bytes]:
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(WORKER_PATH)],
        input=json.dumps(
            envelope, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPOSITORY,
        env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin"},
        check=False,
        timeout=20.0,
    )
    check(completed.stdout.count(b"\n") == 1, "worker output not one line")
    response = json.loads(completed.stdout)
    return completed.returncode, response, completed.stderr


def main() -> None:
    tests: list[str] = []
    worker_bytes = WORKER_PATH.read_bytes()
    manifest_bytes = MANIFEST_PATH.read_bytes()
    fixture_bytes = FIXTURE_PATH.read_bytes()
    check(sha_bytes(worker_bytes) == EXPECTED_WORKER_SHA256, "worker hash drift")
    check(sha_bytes(manifest_bytes) == EXPECTED_MANIFEST_SHA256, "manifest hash drift")
    check(sha_bytes(fixture_bytes) == EXPECTED_FIXTURE_SHA256, "fixture hash drift")
    tests.append("durable_hash_pins")

    facade = load_module(FACADE_PATH, "metnos_v2654_facade_selftest")
    public = tuple(
        name for name in facade.__dict__
        if not name.startswith("_") and name != "annotations"
    )
    check(facade.__all__ == ("evaluate",), "facade __all__ is not closed")
    check(public == ("evaluate",), f"unexpected facade public surface: {public}")
    tests.append("facade_only_evaluate")

    fixture = json.loads(fixture_bytes)
    check(fixture["segment_count"] == 80, "unexpected synthetic segment count")
    original_request = " ".join(
        f"s{ordinal:03d}" for ordinal in range(1, fixture["segment_count"] + 1)
    )
    positive_by_id = {item["id"]: item for item in fixture["positive_controls"]}
    negative_by_id = {
        item["id"]: item for item in fixture["native_negative_cases"]
    }

    positive_results = {}
    for case_id, case in positive_by_id.items():
        result = facade.evaluate(original_request, case["compact_frame"])
        check(result["status"] == "evaluated_valid", f"{case_id} not valid")
        check(result["stage"] == case["expected"]["stage"], f"{case_id} stage")
        check(result["codes"] == case["expected"]["codes"], f"{case_id} codes")
        positive_results[case_id] = result["expanded_frame"]["status"]
    check("P04_fanout_multi_action" in positive_results, "multi-action absent")
    check("P05_multi_domain" in positive_results, "multi-domain absent")
    check("P08_typed_ambiguity" in positive_results, "ambiguity absent")
    check("P11_mixed_coverage" in positive_results, "mixed coverage absent")
    tests.extend([
        "fixture_positive_6_of_6",
        "multi_action_and_projection_reuse",
        "multi_domain",
        "typed_ambiguity",
        "mixed_supported_unsupported_coverage",
    ])

    for case_id, case in negative_by_id.items():
        result = facade.evaluate(original_request, case["compact_frame"])
        check(result["status"] == "evaluated_invalid", f"{case_id} not invalid")
        check(result["stage"] == case["expected"]["stage"], f"{case_id} stage")
        check(result["codes"] == case["expected"]["codes"], f"{case_id} codes")
    tests.append("fixture_native_negative_16_of_16")

    direct = positive_by_id["P01_direct"]["compact_frame"]
    prior_compile = builtins.compile
    prior_modules = {
        name: sys.modules.get(name) for name in ("jsonschema", "regex")
    }
    poison = types.ModuleType("caller_poison")
    try:
        builtins.compile = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("caller compile reached")
        )
        sys.modules["jsonschema"] = poison
        sys.modules["regex"] = poison
        facade.source = b"caller source"
        facade.injections = {"__builtins__": builtins}
        facade.path = "/tmp/caller"
        facade.entry = "caller"
        facade.module = poison
        isolated = facade.evaluate(original_request, direct)
        check(isolated["status"] == "evaluated_valid", "caller monkeypatch crossed")
    finally:
        builtins.compile = prior_compile
        for name, prior in prior_modules.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
        for name in ("source", "injections", "path", "entry", "module"):
            facade.__dict__.pop(name, None)
    tests.append("caller_monkeypatch_does_not_cross_process")

    os.environ["PYTHONPATH"] = "/definitely/not/a/trust/root"
    os.environ["PYTHONINSPECT"] = "1"
    try:
        isolated_env = facade.evaluate(original_request, direct)
        check(isolated_env["status"] == "evaluated_valid", "caller env crossed")
    finally:
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("PYTHONINSPECT", None)
    tests.append("isolated_minimal_environment")

    base_envelope = {
        "version": REQUEST_VERSION,
        "original_request": original_request,
        "frame": direct,
    }
    authority_attempts = {
        "source": "raise SystemExit(0)",
        "injections": {"__builtins__": "caller"},
        "path": "/tmp/absolute-outside-repository",
        "entry": "not-a-manifest-member",
        "module": "caller.module",
    }
    for forbidden, attempted_value in authority_attempts.items():
        envelope = copy.deepcopy(base_envelope)
        envelope[forbidden] = attempted_value
        returncode, response, stderr = raw_worker(envelope)
        check(returncode != 0 and not stderr, f"{forbidden}: process outcome")
        check(
            response == {
                "version": RESPONSE_VERSION,
                "ok": False,
                "error": {"code": "protocol", "type": "RuntimeError"},
            },
            f"{forbidden}: protocol was not closed",
        )
    tests.append("protocol_rejects_source_absolute_path_nonmember_module_injection")

    id_only = {
        "version": REQUEST_VERSION,
        "frame": direct,
        "segments": [{"id": ordinal} for ordinal in range(1, 81)],
    }
    returncode, response, stderr = raw_worker(id_only)
    check(returncode != 0 and not stderr and response["ok"] is False, "id-only accepted")
    try:
        facade.evaluate(frame=direct)
    except TypeError:
        pass
    else:
        raise AssertionError("facade accepted evaluation without original_request")
    tests.append("id_only_bypass_rejected")

    for forbidden in ("source", "path", "injections"):
        attempted_frame = copy.deepcopy(direct)
        attempted_frame[forbidden] = "caller-controlled"
        result = facade.evaluate(original_request, attempted_frame)
        check(
            result == {
                "status": "evaluated_invalid",
                "stage": "schema",
                "codes": ["schema"],
            },
            f"frame {forbidden} was not rejected before source compile",
        )
    tests.append("frame_source_path_injection_rejected_by_schema")

    class DictSubclass(dict):
        pass

    invalid_snapshots = [
        DictSubclass(direct),
        {"status": float("nan")},
        {"atoms": [object()]},
    ]
    cycle: dict = {}
    cycle["self"] = cycle
    invalid_snapshots.append(cycle)
    for value in invalid_snapshots:
        try:
            facade.evaluate(original_request, value)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("facade accepted a non-exact JSON snapshot")
    tests.append("exact_json_snapshot_rejects_subclass_nan_object_cycle")

    tree = ast.parse(worker_bytes, filename=str(WORKER_PATH))
    facade_tree = ast.parse(FACADE_PATH.read_bytes(), filename=str(FACADE_PATH))
    worker_imports = []
    facade_imports = []
    for candidate_tree, observed in (
        (tree, worker_imports), (facade_tree, facade_imports),
    ):
        for node in ast.walk(candidate_tree):
            if isinstance(node, ast.Import):
                observed.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                observed.append(node.module or "")
    check(
        set(worker_imports) == {
            "__future__", "ast", "builtins", "hashlib", "importlib.metadata",
            "json", "jsonschema", "math", "os", "pathlib", "pwd", "regex",
            "stat", "sys", "types", "unicodedata",
        },
        f"worker import allowlist drift: {sorted(set(worker_imports))}",
    )
    check(
        set(facade_imports) == {
            "__future__", "fcntl", "hashlib", "json", "math", "os",
            "pathlib", "stat", "subprocess",
        },
        f"facade import allowlist drift: {sorted(set(facade_imports))}",
    )
    forbidden_transport_tokens = {
        "aiohttp", "ctypes", "curl", "http", "httpx", "requests", "socket",
        "urllib", "urlopen",
    }
    for candidate_tree in (tree, facade_tree):
        for node in ast.walk(candidate_tree):
            if isinstance(node, ast.Name):
                check(
                    node.id not in forbidden_transport_tokens,
                    f"transport name entered executable AST: {node.id}",
                )
    subprocess_calls = [
        node for node in ast.walk(facade_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_subprocess"
    ]
    check(len(subprocess_calls) == 1, "facade subprocess surface is not singular")
    subprocess_call = subprocess_calls[0]
    check(subprocess_call.func.attr == "run", "facade subprocess call is not run")
    check(
        not any(keyword.arg == "shell" for keyword in subprocess_call.keywords),
        "facade enables a shell",
    )
    argv = subprocess_call.args[0]
    check(
        isinstance(argv, ast.List)
        and [item.value for item in argv.elts[:3] if isinstance(item, ast.Constant)]
        == ["/usr/bin/python3", "-I", "-B"],
        "facade subprocess argv lost isolated fixed interpreter prefix",
    )
    tests.append("transport_ast_allowlists_and_single_shell_free_spawn")

    top_functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    check(
        [node.name for node in top_functions] == ["_deny_external_effects", "run_once"],
        "worker exposes an unexpected module function",
    )
    run_once_node = next(node for node in top_functions if node.name == "run_once")
    check(
        not run_once_node.args.args
        and not run_once_node.args.posonlyargs
        and not run_once_node.args.kwonlyargs
        and run_once_node.args.vararg is None
        and run_once_node.args.kwarg is None,
        "run_once accepts caller authority",
    )
    forbidden_parameter_fragments = ("source", "inject", "path", "entry", "module")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = list(node.args.posonlyargs) + list(node.args.args)
            arguments += list(node.args.kwonlyargs)
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            check(
                not any(
                    fragment in argument.arg.lower()
                    for argument in arguments
                    for fragment in forbidden_parameter_fragments
                ),
                f"authority-bearing function parameter in {getattr(node, 'name', 'lambda')}",
            )
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def containing_function(node):
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return None

    sinks = {
        name: [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]
        for name in ("compile", "exec")
    }
    check(
        all(len(nodes) == 1 for nodes in sinks.values())
        and all(containing_function(nodes[0]) == "run_once" for nodes in sinks.values()),
        "compile/exec are not unique lexical sinks inside run_once",
    )
    schema_line = min(
        node.lineno for node in ast.walk(run_once_node)
        if isinstance(node, ast.Attribute) and node.attr == "iter_errors"
    )
    compile_line = sinks["compile"][0].lineno
    check(schema_line < compile_line, "compact schema validation follows compile")
    source_text = worker_bytes.decode("utf-8")
    check("_exec_verified_source" not in source_text, "old source sink remains")
    check(
        'namespace["__metnos_registry_bytes__"] = verified_fixed_bytes["registry"]'
        in source_text,
        "registry assignment is not fixed",
    )
    for guard in ("globals", "__builtins__", "ast.Subscript", "ast.Attribute"):
        check(guard in source_text, f"AST reflection guard absent: {guard}")
    tests.extend([
        "zero_arg_one_shot_worker_surface",
        "unique_lexical_compile_exec_sinks",
        "compact_schema_precedes_compile",
        "fixed_registry_assignment",
        "ast_reflection_guards_present",
    ])

    hook_node = next(node for node in top_functions if node.name == "_deny_external_effects")
    hook_module = ast.Module(body=[hook_node], type_ignores=[])
    ast.fix_missing_locations(hook_module)
    hook_namespace: dict = {}
    exec(compile(hook_module, "<v2654-audit-hook-test>", "exec"), hook_namespace)
    hook = hook_namespace["_deny_external_effects"]
    denied_events = (
        "socket.__new__", "http.client.connect", "urllib.request",
        "subprocess.Popen", "ctypes.dlopen", "os.system", "os.posix_spawn",
        "os.spawn", "os.spawnve", "os.exec", "os.fork", "os.forkpty",
    )
    for event in denied_events:
        try:
            hook(event, ())
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"audit hook allowed {event}")
    for allowed_event in ("open", "import", "os.listdir"):
        hook(allowed_event, ())
    tests.append("audit_hook_denies_network_subprocess_shell_ctypes_exec_fork")

    import_probe = (
        "import importlib.util;"
        f"p={str(WORKER_PATH)!r};"
        "s=importlib.util.spec_from_file_location('forbidden_worker_import',p);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)"
    )
    imported = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-c", import_probe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin"},
        check=False,
        timeout=10.0,
    )
    check(imported.returncode != 0, "worker was importable")
    check(b"worker is not importable" in imported.stderr, "wrong import refusal")
    tests.append("worker_import_refused")

    with tempfile.TemporaryDirectory(prefix="metnos-v2654-selftest-") as temp_name:
        temp_repository = Path(temp_name)
        relative_directory = Path(
            "internal/tools/request_analysis_lab/candidates/v2654"
        )
        target_directory = temp_repository / relative_directory
        target_directory.mkdir(parents=True)
        temp_facade = target_directory / FACADE_PATH.name
        temp_worker = target_directory / WORKER_PATH.name
        temp_facade.write_bytes(FACADE_PATH.read_bytes())
        mutated = bytearray(worker_bytes)
        mutated[-2] ^= 1
        temp_worker.write_bytes(mutated)
        forged = load_module(temp_facade, "metnos_v2654_forged_hash_test")
        try:
            forged.evaluate(original_request, direct)
        except RuntimeError as error:
            check("pin or TOCTOU" in str(error), "wrong forged-hash failure")
        else:
            raise AssertionError("facade accepted forged worker bytes")
        temp_worker.unlink()
        temp_worker.symlink_to(WORKER_PATH)
        try:
            forged.evaluate(original_request, direct)
        except OSError:
            pass
        else:
            raise AssertionError("facade followed a worker symlink")
    tests.append("forged_hash_and_symlink_rejected")

    timings_ms = []
    for _ in range(7):
        started = time.perf_counter_ns()
        result = facade.evaluate(original_request, direct)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        check(result["status"] == "evaluated_valid", "overhead probe invalid")
        timings_ms.append(elapsed)
    tests.append("one_shot_process_overhead_measured")

    report = {
        "version": "metnos.v26.5.4-author-selftest/1.0",
        "status": "PASS",
        "offline": True,
        "network_calls": 0,
        "model_calls": 0,
        "freeze_created": False,
        "gate_created": False,
        "fixture": {
            "positive": len(positive_by_id),
            "native_negative": len(negative_by_id),
            "synthetic_real_segments": fixture["segment_count"],
        },
        "process_overhead_ms": {
            "samples": len(timings_ms),
            "min": round(min(timings_ms), 3),
            "median": round(statistics.median(timings_ms), 3),
            "max": round(max(timings_ms), 3),
        },
        "test_count": len(tests),
        "tests": tests,
        "pins": {
            "worker_sha256": EXPECTED_WORKER_SHA256,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "fixture_sha256": EXPECTED_FIXTURE_SHA256,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
