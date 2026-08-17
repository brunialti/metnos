#!/usr/bin/env python3
"""Offline verifier for the V26.5.6.4 K1/34 external live gate."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


HERE = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates/v26564")
REPOSITORY = Path("/opt/metnos")
BASE_VERIFIER = HERE / "metnos_v26564_transport_preflight_gate_verifier.py"
RUNNER = HERE / "metnos_v26564_k1_runner.py"
FREEZE = HERE / "metnos_v26564_author.freeze.json"
AUTHOR_GATE = HERE / "metnos_v26564_author_pre_gate.json"
STATIC_MD = HERE / "metnos_v26564_independent_static_review.md"
STATIC_JSON = HERE / "metnos_v26564_independent_static_review.json"
PREFLIGHT_GATE = HERE / "metnos_v26564_preflight_gate.lock.json"
PREFLIGHT_VERIFIER = HERE / "metnos_v26564_transport_preflight_gate_verifier.py"
PREFLIGHT_VERIFICATION = HERE / "metnos_v26564_transport_preflight_gate_verification.json"
PREFLIGHT_AUTH = HERE / "metnos_v26564_transport_preflight_authorization_review.json"
PREFLIGHT_ARCHIVE = HERE / "metnos_v26564_transport_preflight.json"
PREFLIGHT_SOURCE = Path("/tmp/metnos_v26564_transport_preflight.json")
AUTH = HERE / "metnos_v26564_external_live_authorization_review.json"
VERIFICATION = HERE / "metnos_v26564_external_live_gate_verification.json"
GATE = HERE / "metnos_v26564_external_gate.lock.json"
VERIFIER = Path(__file__).resolve(strict=True)
LIVE_OUTPUT = Path("/tmp/metnos_v26564_live_k1_34.json")
ENDPOINT = "http://127.0.0.1:8080"

HASHES = {
    "runner": "6fb5785d32b73f9d7dad4e917b211fdcdc12d9ca05088cc6d8ed9c4b167b146a",
    "freeze": "d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56",
    "author": "365bc9637fb3c1d1d09812968b5ad85de0875b0b09fc3f21834c89fbca178236",
    "static_md": "e4323dc28552768fcbbc88e952145f1abdd188b2172d02efb81c0171b1efdd06",
    "static_json": "394d851559ee8b13e50c25a45681d37c2f04c04eabbf36d12bcd892aaf4f0636",
    "preflight_gate": "dd27373ee3b8342d134971fc4fd6bd804e8be9930d7412b6e8a73860148fa5ad",
    "preflight_verifier": "de70f1610c3e6619e10fe267d6ff46199ed8d008317de59a67ca3dd5f501bbaa",
    "preflight_verification": "d687b3b72a39f1bf609afef3b1b795080a16aa08f2ae93268084073a9b012a81",
    "preflight_auth": "18181b5798755d961c8b4641d69e69dd2b5cc771b6fa4fee08e6432042b47ee0",
    "preflight": "7e85e2daca3bdd8b5daa9b2fe1d3e80eb76f566171ba98bd4038192c7b76735a",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _rel(path: Path) -> str:
    return str(path.relative_to(REPOSITORY))


def _dependencies(base) -> dict[str, str]:
    paths = {
        RUNNER: HASHES["runner"], FREEZE: HASHES["freeze"],
        AUTHOR_GATE: HASHES["author"], STATIC_MD: HASHES["static_md"],
        STATIC_JSON: HASHES["static_json"], PREFLIGHT_GATE: HASHES["preflight_gate"],
        PREFLIGHT_VERIFIER: HASHES["preflight_verifier"],
        PREFLIGHT_VERIFICATION: HASHES["preflight_verification"],
        PREFLIGHT_AUTH: HASHES["preflight_auth"], PREFLIGHT_ARCHIVE: HASHES["preflight"],
        VERIFIER: _sha(base._read_nofollow(VERIFIER)),
    }
    return {_rel(path): digest for path, digest in paths.items()}


def expected_auth(base) -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.6.4-external-live-authorization-review/1.0",
        "status": "AUTHORIZE_ONE_NATIVE_K1_34",
        "verdict": "STATIC_PASS",
        "live_authorization": False,
        "external_gate_authorization": True,
        "runtime_cutover_authorization": False,
        "network_calls_during_authorization": 0,
        "inference_calls_during_authorization": 0,
        "candidate_outputs_read": 0,
        "authorization": {
            "endpoint": ENDPOINT,
            "output_path": str(LIVE_OUTPUT),
            "output_must_be_absent": True,
            "authorized_case_count": 34,
            "repetitions": 1,
            "model_request_attempt_limit": 34,
            "retries": 0,
            "one_call_per_case": True,
            "inline_preflight_required": True,
            "inline_preflight_method": "GET",
            "external_preflight_required": True,
            "inference_calls_before_lock": 0,
            "transport_preflight_calls_before_lock": 1,
            "python_executable": "/usr/bin/python3",
            "python_flags": ["-I", "-B"],
        },
        "preflight": {
            "source_path": str(PREFLIGHT_SOURCE),
            "archive_path": _rel(PREFLIGHT_ARCHIVE),
            "sha256": HASHES["preflight"],
            "bytes": 1552,
            "completed_at_unix_ns": 1786291120256187643,
        },
        "dependencies": _dependencies(base),
    }


def expected_verification(base, auth_sha: str) -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.6.4-external-live-gate-verification/1.0",
        "status": "PASS",
        "authorizes_live": True,
        "authorizes_runtime_cutover": False,
        "network_calls": 0,
        "inference_calls": 0,
        "candidate_outputs_read": 0,
        "authorized_output_preexisting": False,
        "authorization": expected_auth(base)["authorization"],
        "preflight_sha256": HASHES["preflight"],
        "mutation_suite": {"mutations": 27, "runner_rejections": 27, "failed": 0},
        "verifier": {"path": _rel(VERIFIER), "sha256": _sha(base._read_nofollow(VERIFIER))},
        "authorization_review": {"path": _rel(AUTH), "sha256": auth_sha},
    }


def expected_gate(auth_sha: str, verification_sha: str) -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.6.4-external-gate/1.0",
        "status": "authorized_native_k1_34",
        "authority": "independent_v26564_infra_review_and_gate_verifier",
        "inference_allowed": True,
        "runner_sha256": HASHES["runner"], "freeze_sha256": HASHES["freeze"],
        "author_gate_sha256": HASHES["author"], "authorized_endpoint": ENDPOINT,
        "authorized_output_path": str(LIVE_OUTPUT), "authorized_case_count": 34,
        "model_request_attempt_limit": 34, "retries": 0, "one_call_per_case": True,
        "inline_preflight_required": True, "external_preflight_required": True,
        "inference_calls_before_lock": 0, "transport_preflight_calls_before_lock": 1,
        "independent_review": {"path": _rel(AUTH), "sha256": auth_sha},
        "gate_verification": {"path": _rel(VERIFICATION), "sha256": verification_sha},
        "transport_preflight": {"path": _rel(PREFLIGHT_ARCHIVE), "sha256": HASHES["preflight"]},
    }


def mutations() -> list[tuple[str, Any]]:
    return [
        ("version", "bad"), ("status", "bad"), ("authority", "bad"),
        ("inference_allowed", 1), ("runner_sha256", "0" * 64),
        ("freeze_sha256", "0" * 64), ("author_gate_sha256", "0" * 64),
        ("authorized_endpoint", "http://127.0.0.1:8081"),
        ("authorized_output_path", "/tmp/other.json"),
        ("authorized_case_count", 33), ("authorized_case_count", True),
        ("model_request_attempt_limit", 35), ("model_request_attempt_limit", True),
        ("retries", 1), ("retries", False), ("one_call_per_case", 1),
        ("inline_preflight_required", False), ("inline_preflight_required", 1),
        ("external_preflight_required", False), ("external_preflight_required", 1),
        ("inference_calls_before_lock", 1), ("inference_calls_before_lock", False),
        ("transport_preflight_calls_before_lock", 0),
        ("transport_preflight_calls_before_lock", True),
        ("independent_review", {"path": "README.md", "sha256": "0" * 64}),
        ("gate_verification", {"path": "README.md", "sha256": "0" * 64}),
        ("transport_preflight", {"path": "README.md", "sha256": "0" * 64}),
    ]


def main() -> int:
    if sys.executable != "/usr/bin/python3" or sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
        raise RuntimeError("external verifier requires /usr/bin/python3 -I -B")
    if LIVE_OUTPUT.exists():
        raise RuntimeError("authorized live output already exists")
    base = _load(BASE_VERIFIER, "v26564_preflight_verifier_base")
    auth_bytes = base._read_nofollow(AUTH)
    verification_bytes = base._read_nofollow(VERIFICATION)
    gate_bytes = base._read_nofollow(GATE)
    auth = base._strict_json(auth_bytes, "external authorization")
    verification = base._strict_json(verification_bytes, "external verification")
    gate = base._strict_json(gate_bytes, "external gate")
    auth_sha, verification_sha = _sha(auth_bytes), _sha(verification_bytes)
    expected_a = expected_auth(base)
    expected_v = expected_verification(base, auth_sha)
    expected_g = expected_gate(auth_sha, verification_sha)
    if not base._exact_equal(auth, expected_a) or not base._exact_equal(verification, expected_v) or not base._exact_equal(gate, expected_g):
        raise RuntimeError("external authorization chain mismatch")
    for path, digest in ((PREFLIGHT_SOURCE, HASHES["preflight"]), (PREFLIGHT_ARCHIVE, HASHES["preflight"])):
        if _sha(base._read_nofollow(path)) != digest:
            raise RuntimeError("preflight source/archive mismatch")
    runner = base._load_runner()
    checkpoint = runner._verify_author_bytes()
    preflight = base._strict_json(base._read_nofollow(PREFLIGHT_ARCHIVE), "preflight")
    runner._validate_external_preflight(preflight, ENDPOINT)
    observed, observed_sha = runner._verify_external_gate_snapshot(ENDPOINT, LIVE_OUTPUT)
    if not base._exact_equal(observed, gate) or observed_sha != _sha(gate_bytes):
        raise RuntimeError("runner external gate disagreement")
    original_snapshot, original_verify = runner._fixed_repo_snapshot, runner._verify_author_bytes
    rejected = 0
    try:
        runner._verify_author_bytes = lambda: checkpoint
        for key, value in mutations():
            changed = copy.deepcopy(gate)
            changed[key] = value
            payload = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            def snapshot(path, maximum, *, data=payload):
                return data if path == runner.EXTERNAL_GATE_PATH else original_snapshot(path, maximum)
            runner._fixed_repo_snapshot = snapshot
            try:
                runner._verify_external_gate_snapshot(ENDPOINT, LIVE_OUTPUT)
            except Exception:
                rejected += 1
            else:
                raise RuntimeError("runner accepted external gate mutation: " + key)
    finally:
        runner._fixed_repo_snapshot, runner._verify_author_bytes = original_snapshot, original_verify
    if rejected != len(mutations()) or LIVE_OUTPUT.exists():
        raise RuntimeError("external gate mutation/output invariant failed")
    print(json.dumps(expected_v, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
