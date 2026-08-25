from __future__ import annotations

import hashlib
import json
import os
import sys
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
from types import MappingProxyType, SimpleNamespace

import executor_birth_runner as runner
from executor_birth import ObservedCandidate
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_property_runner import ObservedPropertyRunner, PropertyCandidateProfile, run_applicable_properties
from executor_birth_properties import PropertyStatus
from executor_birth_snapshot import CandidateSnapshot
from executor_birth_runner_windows_v1 import helper_binary_hash
from manifest_inventory import ContractId, ManifestOrigin


pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows AppContainer required")
CANDIDATE = "sha256:" + "c" * 64


@lru_cache(maxsize=1)
def _helper_path():
    configured = os.environ.get("METNOS_BIRTH_SANDBOX_HELPER")
    if configured:
        return Path(configured).resolve()
    subprocess.run(
        ("cargo", "build", "--manifest-path", "client-rs/Cargo.toml",
         "--release", "--bin", "metnos-birth-sandbox"),
        check=True,
    )
    return Path("client-rs/target/release/metnos-birth-sandbox.exe").resolve()


def _registry(tmp_path):
    helper = _helper_path()
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
try:
    s=socket.socket(); s.settimeout(1)
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


def test_real_appcontainer_runs_property_harness_schema_and_cardinality(tmp_path):
    source = r'''
import json,sys
request=json.load(sys.stdin)
count=request.get("input",{}).get("fixture_count",0)
print(json.dumps({"entries":[{} for _ in range(count)]}))
'''
    snapshot = CandidateSnapshot(
        tmp_path,
        b'[code]\nfiles=["main.py"]\n[output.properties.entries]\ntype="array"\n',
        b"{}", MappingProxyType({"main.py": source.encode()}),
    )
    observed = ObservedCandidate(
        ContractId(ManifestOrigin.USER, "portable/manifest.toml"), snapshot,
        SimpleNamespace(candidate_id=CANDIDATE), ExecutorOrigin.HUMAN,
        RevisionAuthor.HUMAN, CANDIDATE,
    )
    try:
        evidence = run_applicable_properties(
            PropertyCandidateProfile(
                output_schema=(("entries", "array"),), collection_output=True,
            ),
            _runner=ObservedPropertyRunner(observed, windows_registry=_registry(tmp_path)),
        )
    finally:
        observed.close()
    assert {item.property_id for item in evidence} == {
        "output.schema.actual", "cardinality.zero_one_many",
    }
    assert all(item.status is PropertyStatus.PASSED for item in evidence)
    assert result.attestation.tree_empty is True
