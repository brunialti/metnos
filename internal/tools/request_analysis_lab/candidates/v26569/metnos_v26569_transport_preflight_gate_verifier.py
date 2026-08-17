#!/usr/bin/env python3
"""Offline verifier for the single V26.5.6.9 transport preflight gate.

This verifier performs no network operation, reads no candidate output and
never creates the authorized output.  It validates the immutable author chain,
the independent STATIC PASS review, the authorization/result artifacts and the
runner's own frozen preflight-gate parser.  The only transport exercise uses an
in-memory fake for one GET response.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable


VERSION = "metnos.v26.5.6.9-transport-preflight-gate-verifier/1.0"
REPOSITORY = Path("/opt/metnos")
HERE = REPOSITORY / "internal/tools/request_analysis_lab/candidates/v26569"
RUNNER_PATH = HERE / "metnos_v26569_k1_runner.py"
FREEZE_PATH = HERE / "metnos_v26569_author.freeze.json"
AUTHOR_PRE_GATE_PATH = HERE / "metnos_v26569_author_pre_gate.json"
# NOT an independent review: the user authorized the author's own static
# review to stand in its place on 10 August. Every artifact says so.
STATIC_REVIEW_MD_PATH = HERE / "metnos_v26569_author_static_review.md"
STATIC_REVIEW_JSON_PATH = HERE / "metnos_v26569_author_static_review.json"
DEPENDENCY_TREE_PATH = (
    REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2656/"
    "metnos_v2656_python_dependency_tree.json"
)
AUTHORIZATION_PATH = HERE / "metnos_v26569_transport_preflight_authorization_review.json"
VERIFICATION_PATH = HERE / "metnos_v26569_transport_preflight_gate_verification.json"
GATE_PATH = HERE / "metnos_v26569_preflight_gate.lock.json"
EXTERNAL_GATE_PATH = HERE / "metnos_v26569_external_gate.lock.json"
VERIFIER_PATH = Path(__file__).resolve(strict=True)
OUTPUT_PATH = Path("/tmp/metnos_v26569_transport_preflight.json")
ENDPOINT = "http://127.0.0.1:8080"
REQUEST_PATH = "/v1/models"
AUTHORIZED_URL = ENDPOINT + REQUEST_PATH

EXPECTED_HASHES = {
    "runner": "27507e737039291547ddb0063a4673940fb04d3a2a5873c37f552283a1702deb",
    "freeze": "e3acf15bff0ed7584e48d5b659318c74b94aeb45817ca511ad62bdcda9958471",
    "author_pre_gate": "d49a097fff25299236dfcd938d1d74d4cdd6e62a696421e220b80673d80caf09",
    "static_review_md": "3cff8658ed006c20a0619d5e8d5fff288697c1bd7d3f82a96f3082b8a23c5282",
    "static_review_json": "634efd08dfa66ec483ab670544da035b414c06815db400e6b58b99ca0c3524af",
    "dependency_tree": "248b6a6877ad98d361890b50eb91664e3fec477ab6a8376f5dd48c0cb3e9b156",
    "python_target": "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118",
    "artifact_pins_canonical": "b85bbe4b60624f6adb051cec231f31c8318c562a918ee810f7af18a731ff75ae",
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_nofollow(path: Path, maximum: int = 4 * 1024 * 1024) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            raise RuntimeError(f"unbounded or non-regular verifier dependency: {path}")
        remaining = before.st_size
        chunks = []
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        value = b"".join(chunks)
        extra = os.read(descriptor, 1)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if remaining or extra or identity_before != identity_after:
            raise RuntimeError(f"verifier dependency changed during read: {path}")
        return value
    finally:
        os.close(descriptor)


def _strict_json(value: bytes, label: str) -> Any:
    def closed_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise RuntimeError(f"duplicate key in {label}: {key}")
            result[key] = item
        return result

    def reject_constant(token):
        raise RuntimeError(f"non-finite JSON in {label}: {token}")

    try:
        return json.loads(
            value, object_pairs_hook=closed_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError(f"invalid strict JSON: {label}") from error


def _exact_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            _exact_equal(actual[key], expected[key]) for key in expected
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _exact_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _relative(path: Path) -> str:
    return str(path.relative_to(REPOSITORY))


def _expected_dependencies() -> dict[str, str]:
    return {
        _relative(RUNNER_PATH): EXPECTED_HASHES["runner"],
        _relative(FREEZE_PATH): EXPECTED_HASHES["freeze"],
        _relative(AUTHOR_PRE_GATE_PATH): EXPECTED_HASHES["author_pre_gate"],
        _relative(STATIC_REVIEW_MD_PATH): EXPECTED_HASHES["static_review_md"],
        _relative(STATIC_REVIEW_JSON_PATH): EXPECTED_HASHES["static_review_json"],
        _relative(DEPENDENCY_TREE_PATH): EXPECTED_HASHES["dependency_tree"],
        _relative(VERIFIER_PATH): sha_bytes(_read_nofollow(VERIFIER_PATH)),
    }


def expected_authorization() -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.6.9-transport-preflight-authorization-review/1.0",
        "status": "AUTHORIZE_ONE_TRANSPORT_PREFLIGHT_ONLY",
        "verdict": "STATIC_PASS",
        "verdict_authority": "user",
        "verdict_basis": (
            "author static review, PASS after corrections, zero residual "
            "findings; the user authorized it to stand as STATIC_PASS on "
            "10 August 2026 because no independent agent was available"
        ),
        "independent_review_performed": False,
        "preflight_authorization": True,
        "live_authorization": False,
        "external_live_gate_authorization": False,
        "runtime_cutover_authorization": False,
        "network_calls_during_authorization": 0,
        "candidate_outputs_read": 0,
        "authorization": {
            "endpoint": ENDPOINT,
            "url": AUTHORIZED_URL,
            "method": "GET",
            "request_path": REQUEST_PATH,
            "request_body_bytes": 0,
            "transport_attempt_limit": 1,
            "post_calls": 0,
            "inference_calls": 0,
            "output_path": str(OUTPUT_PATH),
            "output_must_be_absent": True,
            "no_clobber_required": True,
            "python_executable": "/usr/bin/python3",
            "python_flags": ["-I", "-B"],
        },
        "dependency_contract": {
            "artifact_pin_count": 19,
            "artifact_pins_canonical_sha256": EXPECTED_HASHES["artifact_pins_canonical"],
            "python_dependency_tree_sha256": EXPECTED_HASHES["dependency_tree"],
            "python_target_sha256": EXPECTED_HASHES["python_target"],
            "python_version": "3.12.3",
            "unicode_version": "15.0.0",
        },
        "dependencies": _expected_dependencies(),
    }


def _gate_mutations() -> list[tuple[str, Callable[[dict[str, Any]], None]]]:
    def set_value(path: tuple[str, ...], value: Any):
        def mutate(payload):
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return mutate

    mutations = [
        ("version", set_value(("version",), "metnos.v26.5.6.9-preflight-gate/2.0")),
        ("status", set_value(("status",), "authorized_native_k1_34")),
        ("authority", set_value(("authority",), "author")),
        ("transport_bool_as_int", set_value(("transport_preflight_allowed",), 1)),
        ("inference_bool_as_int", set_value(("inference_allowed",), 0)),
        ("runner_hash", set_value(("runner_sha256",), "0" * 64)),
        ("freeze_hash", set_value(("freeze_sha256",), "0" * 64)),
        ("author_gate_hash", set_value(("author_gate_sha256",), "0" * 64)),
        ("endpoint", set_value(("authorized_endpoint",), "http://127.0.0.1:8081")),
        ("output", set_value(("authorized_output_path",), "/tmp/other_preflight.json")),
        ("method", set_value(("method",), "POST")),
        ("request_path", set_value(("request_path",), "/v1/chat/completions")),
        ("transport_zero", set_value(("transport_call_limit",), 0)),
        ("transport_two", set_value(("transport_call_limit",), 2)),
        ("transport_int_as_bool", set_value(("transport_call_limit",), True)),
        ("inference_one", set_value(("inference_call_limit",), 1)),
        ("inference_int_as_bool", set_value(("inference_call_limit",), False)),
        ("review_path", set_value(("independent_review", "path"), "README.md")),
        ("review_hash", set_value(("independent_review", "sha256"), "0" * 64)),
        ("verification_path", set_value(("gate_verification", "path"), "README.md")),
        ("verification_hash", set_value(("gate_verification", "sha256"), "0" * 64)),
    ]

    def missing(payload):
        payload.pop("status")

    def extra(payload):
        payload["unexpected"] = None

    def review_reference_extra(payload):
        payload["independent_review"]["unexpected"] = None

    def verification_reference_extra(payload):
        payload["gate_verification"]["unexpected"] = None

    mutations.extend([
        ("missing_key", missing),
        ("extra_key", extra),
        ("review_reference_extra", review_reference_extra),
        ("verification_reference_extra", verification_reference_extra),
    ])
    return mutations


def _authorization_mutations() -> list[tuple[str, Callable[[dict[str, Any]], None]]]:
    def set_value(path: tuple[str, ...], value: Any):
        def mutate(payload):
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return mutate

    mutations = [
        ("auth_status", set_value(("status",), "AUTHORIZE_LIVE")),
        ("auth_verdict", set_value(("verdict",), "STATIC_BLOCK")),
        ("auth_preflight_false", set_value(("preflight_authorization",), False)),
        ("auth_preflight_bool_as_int", set_value(("preflight_authorization",), 1)),
        ("auth_live_true", set_value(("live_authorization",), True)),
        ("auth_live_bool_as_int", set_value(("live_authorization",), 0)),
        ("auth_external_live", set_value(("external_live_gate_authorization",), True)),
        ("auth_cutover", set_value(("runtime_cutover_authorization",), True)),
        ("auth_network", set_value(("network_calls_during_authorization",), 1)),
        ("auth_outputs", set_value(("candidate_outputs_read",), 1)),
        ("auth_endpoint", set_value(("authorization", "endpoint"), "http://127.0.0.1:8081")),
        ("auth_output", set_value(("authorization", "output_path"), "/tmp/other_preflight.json")),
        ("auth_method", set_value(("authorization", "method"), "POST")),
        ("auth_path", set_value(("authorization", "request_path"), "/v1/chat/completions")),
        ("auth_body", set_value(("authorization", "request_body_bytes"), 1)),
        ("auth_body_int_as_bool", set_value(("authorization", "request_body_bytes"), False)),
        ("auth_attempt_zero", set_value(("authorization", "transport_attempt_limit"), 0)),
        ("auth_attempt_two", set_value(("authorization", "transport_attempt_limit"), 2)),
        ("auth_attempt_int_as_bool", set_value(("authorization", "transport_attempt_limit"), True)),
        ("auth_post", set_value(("authorization", "post_calls"), 1)),
        ("auth_inference", set_value(("authorization", "inference_calls"), 1)),
        ("auth_python", set_value(("authorization", "python_executable"), "/usr/bin/python")),
        ("auth_flags", set_value(("authorization", "python_flags"), ["-I"])),
        ("auth_dependency", set_value(("dependency_contract", "python_dependency_tree_sha256"), "0" * 64)),
        ("auth_artifact_pins", set_value(("dependency_contract", "artifact_pins_canonical_sha256"), "0" * 64)),
    ]

    def missing(payload):
        payload.pop("verdict")

    def extra(payload):
        payload["unexpected"] = None

    mutations.extend([("auth_missing", missing), ("auth_extra", extra)])
    return mutations


def _verification_mutations() -> list[tuple[str, Callable[[dict[str, Any]], None]]]:
    def set_value(path: tuple[str, ...], value: Any):
        def mutate(payload):
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return mutate

    mutations = [
        ("verification_status", set_value(("status",), "FAIL")),
        ("verification_preflight_false", set_value(("authorizes_preflight",), False)),
        ("verification_preflight_bool_as_int", set_value(("authorizes_preflight",), 1)),
        ("verification_live_true", set_value(("authorizes_live",), True)),
        ("verification_live_bool_as_int", set_value(("authorizes_live",), 0)),
        ("verification_external", set_value(("authorizes_external_gate",), True)),
        ("verification_network", set_value(("network_calls",), 1)),
        ("verification_server", set_value(("server_calls",), 1)),
        ("verification_model", set_value(("model_calls",), 1)),
        ("verification_outputs", set_value(("candidate_outputs_read",), 1)),
        ("verification_output_exists", set_value(("authorized_output_preexisting",), True)),
        ("verification_endpoint", set_value(("authorization", "endpoint"), "http://127.0.0.1:8081")),
        ("verification_method", set_value(("authorization", "method"), "POST")),
        ("verification_body", set_value(("authorization", "request_body_bytes"), 1)),
        ("verification_attempts", set_value(("authorization", "transport_attempt_limit"), 2)),
        ("verification_post", set_value(("authorization", "post_calls"), 1)),
        ("verification_inference", set_value(("authorization", "inference_calls"), 1)),
        ("verification_flags", set_value(("authorization", "python_flags"), ["-I"])),
        ("verification_verifier_hash", set_value(("verifier", "sha256"), "0" * 64)),
        ("verification_review_hash", set_value(("authorization_review", "sha256"), "0" * 64)),
        ("verification_mutation_count", set_value(("mutation_suite", "total"), 0)),
    ]

    def missing(payload):
        payload.pop("status")

    def extra(payload):
        payload["unexpected"] = None

    mutations.extend([("verification_missing", missing), ("verification_extra", extra)])
    return mutations


def expected_verification(authorization_sha256: str) -> dict[str, Any]:
    gate_count = len(_gate_mutations())
    auth_count = len(_authorization_mutations())
    verification_count = len(_verification_mutations())
    return {
        "version": "metnos.v26.5.6.9-transport-preflight-gate-verification/1.0",
        "status": "PASS",
        "authorizes_preflight": True,
        "authorizes_live": False,
        "authorizes_external_gate": False,
        "network_calls": 0,
        "server_calls": 0,
        "model_calls": 0,
        "candidate_outputs_read": 0,
        "authorized_output_preexisting": False,
        "authorization": {
            "endpoint": ENDPOINT,
            "url": AUTHORIZED_URL,
            "method": "GET",
            "request_path": REQUEST_PATH,
            "request_body_bytes": 0,
            "transport_attempt_limit": 1,
            "post_calls": 0,
            "inference_calls": 0,
            "output_path": str(OUTPUT_PATH),
            "python_executable": "/usr/bin/python3",
            "python_flags": ["-I", "-B"],
        },
        "fake_transport_probe": {
            "actual_opens": 1,
            "get_calls": 1,
            "post_calls": 0,
            "request_body_bytes": 0,
            "response_status": "PASS",
        },
        "mutation_suite": {
            "gate_mutations": gate_count,
            "runner_gate_rejections": gate_count,
            "authorization_mutations": auth_count,
            "verification_mutations": verification_count,
            "total": gate_count + auth_count + verification_count,
            "failed": 0,
        },
        "verifier": {
            "path": _relative(VERIFIER_PATH),
            "sha256": sha_bytes(_read_nofollow(VERIFIER_PATH)),
        },
        "authorization_review": {
            "path": _relative(AUTHORIZATION_PATH),
            "sha256": authorization_sha256,
        },
    }


def expected_gate(authorization_sha256: str, verification_sha256: str) -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.6.9-preflight-gate/1.0",
        "status": "authorized_transport_preflight_only",
        "authority": "user_authorized_v26569_preflight_gate_verifier",
        "transport_preflight_allowed": True,
        "inference_allowed": False,
        "runner_sha256": EXPECTED_HASHES["runner"],
        "freeze_sha256": EXPECTED_HASHES["freeze"],
        "author_gate_sha256": EXPECTED_HASHES["author_pre_gate"],
        "authorized_endpoint": ENDPOINT,
        "authorized_output_path": str(OUTPUT_PATH),
        "method": "GET",
        "request_path": REQUEST_PATH,
        "transport_call_limit": 1,
        "inference_call_limit": 0,
        "independent_review": {
            "path": _relative(AUTHORIZATION_PATH),
            "sha256": authorization_sha256,
        },
        "gate_verification": {
            "path": _relative(VERIFICATION_PATH),
            "sha256": verification_sha256,
        },
    }


def _require_absent(path: Path, label: str) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise RuntimeError(f"{label} must be absent: {path}")


def _verify_dependencies(freeze: dict[str, Any]) -> None:
    paths = {
        "runner": RUNNER_PATH,
        "freeze": FREEZE_PATH,
        "author_pre_gate": AUTHOR_PRE_GATE_PATH,
        "static_review_md": STATIC_REVIEW_MD_PATH,
        "static_review_json": STATIC_REVIEW_JSON_PATH,
        "dependency_tree": DEPENDENCY_TREE_PATH,
    }
    for identity, path in paths.items():
        if sha_bytes(_read_nofollow(path)) != EXPECTED_HASHES[identity]:
            raise RuntimeError(f"preflight dependency hash mismatch: {identity}")
    review = _strict_json(_read_nofollow(STATIC_REVIEW_JSON_PATH), "static review")
    if (
        type(review) is not dict
        or review.get("verdict_after_corrections") != "PASS"
        or review.get("residual_findings") != []
        or review.get("reviewer") != "author"
        or review.get("independent") is not False
        or review.get("authorizes_transport") is not False
        or review.get("authorizes_live") is not False
    ):
        raise RuntimeError("author static review is not the pinned offline PASS")
    pins = freeze.get("artifact_pins")
    if type(pins) is not dict or len(pins) != 19:
        raise RuntimeError("freeze artifact pin inventory changed")
    canonical = json.dumps(
        pins, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if sha_bytes(canonical) != EXPECTED_HASHES["artifact_pins_canonical"]:
        raise RuntimeError("freeze artifact pin canonical hash changed")


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "metnos_v26569_independent_preflight_runner", RUNNER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen V26.5.6.9 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, url: str) -> None:
        self.body = b'{"data":[]}'
        self.status = 200
        self.headers = {"Content-Length": str(len(self.body))}
        self.url = url

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, limit: int = -1):
        return self.body if limit < 0 else self.body[:limit]

    def close(self):
        return None


class _FakeTransport:
    def __init__(self) -> None:
        self.open_count = 0
        self.last_effective_url: str | None = None
        self.get_calls = 0
        self.post_calls = 0
        self.request_body_bytes = 0

    def open_once(self, request, timeout):
        del timeout
        self.open_count += 1
        self.last_effective_url = request.full_url
        method = request.get_method()
        body = request.data
        self.request_body_bytes += 0 if body is None else len(body)
        if method == "GET":
            self.get_calls += 1
        elif method == "POST":
            self.post_calls += 1
        else:
            raise RuntimeError("fake observed an unauthorized method")
        return _FakeResponse(request.full_url)


def _run_gate_mutations(
    runner: Any, gate: dict[str, Any], expected: dict[str, Any],
    checkpoint: tuple[dict[str, Any], dict[str, str]],
) -> tuple[int, int]:
    original_snapshot = runner._fixed_repo_snapshot
    original_verify = runner._verify_author_bytes
    independent_rejected = 0
    runner_rejected = 0
    try:
        runner._verify_author_bytes = lambda: checkpoint
        for mutation_id, mutate in _gate_mutations():
            changed = copy.deepcopy(gate)
            mutate(changed)
            if not _exact_equal(changed, expected):
                independent_rejected += 1
            else:
                raise RuntimeError(f"ineffective gate mutation: {mutation_id}")
            changed_bytes = json.dumps(
                changed, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")

            def snapshot(path, maximum, *, payload=changed_bytes):
                if path == runner.PREFLIGHT_GATE_PATH:
                    return payload
                return original_snapshot(path, maximum)

            runner._fixed_repo_snapshot = snapshot
            try:
                runner._verify_preflight_gate_snapshot(ENDPOINT, OUTPUT_PATH)
            except Exception:
                runner_rejected += 1
            else:
                raise RuntimeError(f"runner accepted gate mutation: {mutation_id}")
    finally:
        runner._fixed_repo_snapshot = original_snapshot
        runner._verify_author_bytes = original_verify
    return independent_rejected, runner_rejected


def _run_exact_mutations(
    source: dict[str, Any], expected: dict[str, Any],
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]], label: str,
) -> int:
    rejected = 0
    for mutation_id, mutate in mutations:
        changed = copy.deepcopy(source)
        mutate(changed)
        if _exact_equal(changed, expected):
            raise RuntimeError(f"ineffective {label} mutation: {mutation_id}")
        rejected += 1
    return rejected


def main() -> int:
    if (
        sys.executable != "/usr/bin/python3"
        or sys.version_info[:3] != (3, 12, 3)
        or sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
    ):
        raise RuntimeError("verifier requires /usr/bin/python3 -I -B")
    _require_absent(OUTPUT_PATH, "authorized preflight output")
    _require_absent(EXTERNAL_GATE_PATH, "external live gate")

    authorization_bytes = _read_nofollow(AUTHORIZATION_PATH)
    verification_bytes = _read_nofollow(VERIFICATION_PATH)
    gate_bytes = _read_nofollow(GATE_PATH)
    authorization = _strict_json(authorization_bytes, "preflight authorization")
    verification = _strict_json(verification_bytes, "preflight verification")
    gate = _strict_json(gate_bytes, "preflight gate")
    authorization_sha256 = sha_bytes(authorization_bytes)
    verification_sha256 = sha_bytes(verification_bytes)
    expected_auth = expected_authorization()
    expected_result = expected_verification(authorization_sha256)
    expected_gate_payload = expected_gate(authorization_sha256, verification_sha256)
    if not _exact_equal(authorization, expected_auth):
        raise RuntimeError("preflight authorization artifact mismatch")
    if not _exact_equal(verification, expected_result):
        raise RuntimeError("preflight verification artifact mismatch")
    if not _exact_equal(gate, expected_gate_payload):
        raise RuntimeError("preflight gate artifact mismatch")

    freeze = _strict_json(_read_nofollow(FREEZE_PATH), "author freeze")
    _verify_dependencies(freeze)
    runner = _load_runner()
    checkpoint = runner._verify_author_bytes()
    verified_gate, observed_gate_sha256 = runner._verify_preflight_gate_snapshot(
        ENDPOINT, OUTPUT_PATH,
    )
    if not _exact_equal(verified_gate, gate) or observed_gate_sha256 != sha_bytes(gate_bytes):
        raise RuntimeError("runner gate verification disagrees with independent verifier")

    fake = _FakeTransport()
    preflight = runner._transport_smoke(ENDPOINT, fake)
    if (
        preflight.get("status") != "PASS"
        or fake.open_count != 1
        or fake.get_calls != 1
        or fake.post_calls != 0
        or fake.request_body_bytes != 0
        or preflight.get("request_body_bytes") != 0
        or preflight.get("transport_preflight_calls") != 1
        or preflight.get("inference_calls") != 0
        or preflight.get("request_url") != AUTHORIZED_URL
    ):
        raise RuntimeError("fake transport preflight contract mismatch")

    independent_gate, runner_gate = _run_gate_mutations(
        runner, gate, expected_gate_payload, checkpoint,
    )
    authorization_rejected = _run_exact_mutations(
        authorization, expected_auth, _authorization_mutations(), "authorization",
    )
    verification_rejected = _run_exact_mutations(
        verification, expected_result, _verification_mutations(), "verification",
    )
    if (
        independent_gate != len(_gate_mutations())
        or runner_gate != len(_gate_mutations())
        or authorization_rejected != len(_authorization_mutations())
        or verification_rejected != len(_verification_mutations())
    ):
        raise RuntimeError("preflight mutation suite is incomplete")
    _require_absent(OUTPUT_PATH, "authorized preflight output after verifier")
    _require_absent(EXTERNAL_GATE_PATH, "external live gate after verifier")
    print(json.dumps(expected_result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
