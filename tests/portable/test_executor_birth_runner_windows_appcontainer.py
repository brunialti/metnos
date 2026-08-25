from __future__ import annotations

import hashlib
import json
import os
import sys
import subprocess
from pathlib import Path

import pytest

import executor_birth_runner as runner
from executor_birth_runner_windows_v1 import helper_binary_hash


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows AppContainer required")
CANDIDATE = "sha256:" + "c" * 64


def _registry(tmp_path):
    helper = Path(os.environ["METNOS_BIRTH_SANDBOX_HELPER"]).resolve()
    runtime = Path(sys.executable).resolve()
    runtime_hash = helper_binary_hash(runtime)
    config = helper.parent / f"birth-helper-{tmp_path.name}.json"
    value = {
        "runtime_binary": str(runtime), "runtime_binary_hash": runtime_hash,
        "runtime_root": str(runtime.parent), "schema_version": 1,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    config.write_bytes(raw)
    user = os.environ["USERNAME"]
    subprocess.run(("icacls.exe", str(config), "/inheritance:r", "/grant:r",
                    "*S-1-5-18:(F)", "*S-1-5-32-544:(F)", f"{user}:(R)"),
                   check=True, capture_output=True, text=True)
    return runner.WindowsSandboxRegistry(
        helper, helper_binary_hash(helper), config,
        "sha256:" + hashlib.sha256(raw).hexdigest(), runtime_hash,
    )


def _run(tmp_path, source, *arguments):
    return runner.run_birth_phase(
        ("main.py", *arguments), candidate_id=CANDIDATE,
        windows_registry=_registry(tmp_path),
        candidate_files={"main.py": source.encode()}, fixture_ops=(),
    )


def test_real_appcontainer_denies_host_file_credentials_and_network_and_drains_descendant(tmp_path, monkeypatch):
    sentinel = tmp_path / "host-secret.txt"
    sentinel.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("METNOS_BIRTH_HOST_SECRET", "must-not-cross")
    source = r'''
import os,socket,subprocess,sys
if os.environ.get("METNOS_BIRTH_HOST_SECRET"): raise SystemExit(31)
try:
    open(sys.argv[1], "rb").read()
except OSError: pass
else: raise SystemExit(32)
s=socket.socket(); s.settimeout(1)
try:
    s.connect(("1.1.1.1", 53))
except OSError: pass
else: raise SystemExit(33)
subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])
print("isolated")
'''
    result = _run(tmp_path, source, str(sentinel))
    assert result.status is runner.RunnerStatus.PASSED, (result.error_code, result.stderr)
    assert result.stdout.strip() == "isolated"
    assert result.attestation.network_unshared is True
    assert result.attestation.tree_empty is True
    assert result.attestation.termination_attested is True


def test_real_appcontainer_kills_on_output_overflow(tmp_path):
    result = _run(tmp_path, "import sys\nsys.stdout.write('x'*(2*1024*1024))\n")
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "output_limit_exceeded"
    assert result.attestation.tree_empty is True


def test_real_appcontainer_kills_on_timeout(tmp_path):
    result = _run(tmp_path, "import time\ntime.sleep(60)\n")
    assert result.status is runner.RunnerStatus.FAILED
    assert result.error_code == "phase_timeout"
    assert result.attestation.tree_empty is True
