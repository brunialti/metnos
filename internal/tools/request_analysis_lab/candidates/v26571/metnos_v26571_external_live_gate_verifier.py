#!/usr/bin/env python3
"""Offline verifier for the V26.5.7.1 K1/34 external live gate."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


HERE = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates/v26571")
REPOSITORY = Path("/opt/metnos")
BASE_VERIFIER = HERE / "metnos_v26571_transport_preflight_gate_verifier.py"
RUNNER = HERE / "metnos_v26571_k1_runner.py"
FREEZE = HERE / "metnos_v26571_author.freeze.json"
AUTHOR_GATE = HERE / "metnos_v26571_author_pre_gate.json"
STATIC_MD = HERE / "metnos_v26571_author_static_review.md"
STATIC_JSON = HERE / "metnos_v26571_author_static_review.json"
PREFLIGHT_GATE = HERE / "metnos_v26571_preflight_gate.lock.json"
PREFLIGHT_VERIFIER = HERE / "metnos_v26571_transport_preflight_gate_verifier.py"
PREFLIGHT_VERIFICATION = HERE / "metnos_v26571_transport_preflight_gate_verification.json"
PREFLIGHT_AUTH = HERE / "metnos_v26571_transport_preflight_authorization_review.json"
PREFLIGHT_ARCHIVE = HERE / "metnos_v26571_transport_preflight.json"
PREFLIGHT_SOURCE = Path("/tmp/metnos_v26571_transport_preflight.json")
AUTH = HERE / "metnos_v26571_external_live_authorization_review.json"
VERIFICATION = HERE / "metnos_v26571_external_live_gate_verification.json"
GATE = HERE / "metnos_v26571_external_gate.lock.json"
VERIFIER = Path(__file__).resolve(strict=True)
LIVE_OUTPUT = Path("/tmp/metnos_v26571_live_k1_34.json")
ENDPOINT = "http://127.0.0.1:8080"

HASHES = {
    "runner": "4fb89a9aaeada188840e5f61c06ce22f68f5138e22463b8b8c1437080c8a21e1",
    "freeze": "f9fe647c191bc4da41a1d33ebd70827dcdc25b76d344c7911e1847c8b4d7c827",
    "author": "b94906ffbe93ca700ce3f5eaa7adf325852afcbea8f1fce4110249786a6b7542",
    "static_md": "ec68ea7e9bea1ef1dbc108afd18a5be5c092fd4987891011f43697eb3c1dbbdd",
    "static_json": "202cc632a7b33999a0bd628aa32ff669a96bcbb7f50d5c019a21a5f084cf6d9b",
    "preflight_gate": "fcf106bb7c91f46d788b392a35ddb45c21c4d2f2344ddb7a1f407e03f5b34934",
    "preflight_verifier": "95f1a2d0c58e09af27d620ccc9b59853ec4bf7fbf3e07827eb3d5ed33295b5c3",
    "preflight_verification": "11f21ff26f12542c9e4976276531bc3880fea401cdcd06645d445c79c4126b1d",
    "preflight_auth": "0d2a0b64a641d1d62ed1c4913173a01c9dd774f7afd5be5ead5df25aa2f5eb35",
    "preflight": "4eb2571aabffb022d0bbcf86a624128e9c9e1944df0b0b1deebb8f78e0164fa1",
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
        "version": "metnos.v26.5.7.1-external-live-authorization-review/1.0",
        "status": "AUTHORIZE_ONE_NATIVE_K1_34",
        "verdict": "STATIC_PASS",
        "verdict_authority": "user",
        "verdict_basis": (
            "author static review, PASS after corrections, zero residual "
            "findings; the user authorized it to stand as STATIC_PASS on "
            "10 August 2026 because no independent agent was available"
        ),
        "independent_review_performed": False,
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
            "bytes": 1631,
            "completed_at_unix_ns": 1786353465793292714,
        },
        "dependencies": _dependencies(base),
    }


def expected_verification(base, auth_sha: str) -> dict[str, Any]:
    return {
        "version": "metnos.v26.5.7.1-external-live-gate-verification/1.0",
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
        "version": "metnos.v26.5.7.1-external-gate/1.0",
        "status": "authorized_native_k1_34",
        "authority": "user_authorized_v26571_infra_review_and_gate_verifier",
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
    base = _load(BASE_VERIFIER, "v26571_preflight_verifier_base")
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
