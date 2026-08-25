from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

import executor_birth_runner_windows_v1 as win


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
DH = "sha256:" + "a" * 64
DR = "sha256:" + "b" * 64


def _layout(root: Path):
    (root / "candidate").mkdir()
    (root / "work").mkdir()
    (root / "candidate" / "main.py").write_text("pass\n")


def _response(**changes):
    value = {
        "schema_version": 1, "request_id": D1, "candidate_id": D2,
        "status": "passed", "error_code": None, "exit_code": 0,
        "stdout_base64": base64.b64encode(b"ok").decode(), "stderr_base64": "",
        "stdout_bytes": 2, "stderr_bytes": 0, "stdout_truncated": False,
        "stderr_truncated": False, "elapsed_ms": 1,
        "attestation": {
            "backend": win.BACKEND, "helper_binary_hash": DH,
            "runtime_binary_hash": DR,
            "profile_name": win.PROFILE_NAME,
            "appcontainer_sid": "S-1-15-2-123", "network_capability": False,
            "assigned_before_resume": True, "active_processes": 0,
            "tree_empty": True, "termination_attested": True,
            "memory_limit_bytes": win.MEMORY_LIMIT_BYTES,
            "process_limit": win.PROCESS_LIMIT,
            "stdout_limit_bytes": win.STDOUT_LIMIT_BYTES,
            "stderr_limit_bytes": win.STDERR_LIMIT_BYTES,
        },
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_request_is_closed_canonical_and_does_not_accept_policy(tmp_path):
    _layout(tmp_path)
    raw = win.canonical_request(request_id=D1, candidate_id=D2, phase="candidate",
                                private_root=tmp_path.resolve(), entrypoint="main.py",
                                arguments=("--x", "à"))
    assert raw == json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()
    assert set(json.loads(raw)) == {"schema_version", "request_id", "candidate_id",
                                   "phase", "private_root", "entrypoint", "arguments"}
    assert not {"environment", "network", "grants", "limits", "interpreter"} & set(json.loads(raw))


@pytest.mark.parametrize("entry", ("../x", "/x", "a\\b", "a/./b", ""))
def test_request_rejects_escaping_entrypoint(tmp_path, entry):
    _layout(tmp_path)
    with pytest.raises(win.WindowsBirthHelperError, match="entrypoint_invalid"):
        win.canonical_request(request_id=D1, candidate_id=D2, phase="candidate",
                              private_root=tmp_path.resolve(), entrypoint=entry, arguments=())


def test_response_requires_every_attestation_and_binding():
    result = win.validate_response(_response(), request_id=D1, candidate_id=D2,
                                   expected_helper_hash=DH, expected_runtime_hash=DR)
    assert result.stdout == b"ok"
    bad = json.loads(_response())
    bad["attestation"]["tree_empty"] = False
    raw = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(win.WindowsBirthHelperError, match="attestation_mismatch"):
        win.validate_response(raw, request_id=D1, candidate_id=D2,
                              expected_helper_hash=DH, expected_runtime_hash=DR)


def test_duplicate_or_noncanonical_response_is_rejected():
    with pytest.raises(win.WindowsBirthHelperError, match="duplicate_key"):
        win.validate_response(b'{"schema_version":1,"schema_version":1}',
                              request_id=D1, candidate_id=D2, expected_helper_hash=DH,
                              expected_runtime_hash=DR)
    pretty = json.dumps(json.loads(_response()), indent=2).encode()
    with pytest.raises(win.WindowsBirthHelperError, match="not_canonical"):
        win.validate_response(pretty, request_id=D1, candidate_id=D2,
                              expected_helper_hash=DH, expected_runtime_hash=DR)


def test_output_counts_are_verified():
    with pytest.raises(win.WindowsBirthHelperError, match="output_length_mismatch"):
        win.validate_response(_response(stdout_bytes=3), request_id=D1,
                              candidate_id=D2, expected_helper_hash=DH,
                              expected_runtime_hash=DR)


def test_helper_hash_reads_exact_binary(tmp_path):
    helper = tmp_path / "helper.exe"
    helper.write_bytes(b"trusted helper")
    assert win.helper_binary_hash(helper) == "sha256:" + hashlib.sha256(b"trusted helper").hexdigest()
