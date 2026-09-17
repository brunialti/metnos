"""Native pools share CPU capacity without mutating a concurrent daemon."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import native_threads as native


@pytest.fixture(autouse=True)
def clean_native_environment(monkeypatch):
    for name in (*native._LIBRARIES, native._ENV):
        monkeypatch.delenv(name, raising=False)


def test_child_pools_share_cpu_tokens_and_item_fanout(monkeypatch):
    monkeypatch.setattr(native, "effective_cpus", lambda: 32)
    monkeypatch.setenv("MKL_NUM_THREADS", "2")
    before = dict(os.environ)
    env = native.child_environment(cpu_slots=4, claimed_cpu=1, item_workers=2)
    assert env[native._ENV] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "4"
    assert env["RAYON_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "2"
    assert env["OMP_MAX_ACTIVE_LEVELS"] == "1"
    assert os.environ == before
    # Tokens are weighted shares, not a literal single core per token.
    assert native.child_environment(cpu_slots=4, claimed_cpu=2)[native._ENV] == "16"


@pytest.mark.parametrize("value,expected", [("3", "3"), ("999", "8"), ("0", "1"), ("invalid", "1")])
def test_admin_cap_cannot_expand_host_share(monkeypatch, value, expected):
    monkeypatch.setattr(native, "effective_cpus", lambda: 32)
    monkeypatch.setenv(native._ENV, value)
    assert native.child_environment(cpu_slots=4)[native._ENV] == expected


def test_affinity_and_ancestor_quota_both_constrain_pool(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(16)))
    files = {"/proc/self/cgroup": "0::/parent/child\n",
             "/sys/fs/cgroup/cpu.max": "max 100000",
             "/sys/fs/cgroup/parent/cpu.max": "250000 100000",
             "/sys/fs/cgroup/parent/child/cpu.max": "800000 100000"}
    monkeypatch.setattr(Path, "read_text", lambda path: files[str(path)])
    assert native.effective_cpus() == 2


def test_real_onnx_sessions_use_budget_and_disable_idle_spinning(monkeypatch):
    import onnxruntime as ort

    options = ort.SessionOptions()
    original = options.intra_op_num_threads
    native.configure_onnx_threads(options)
    assert options.intra_op_num_threads == original
    monkeypatch.setenv(native._ENV, "2")
    monkeypatch.setattr(native, "effective_cpus", lambda: 8)
    native.configure_onnx_threads(options)
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1
    assert options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    options.intra_op_num_threads = 1
    native.configure_onnx_threads(options)
    assert options.intra_op_num_threads == 1


def test_actual_child_blas_pool_is_bounded_before_import(monkeypatch):
    monkeypatch.setattr(native, "effective_cpus", lambda: 8)
    env = dict(os.environ)
    env.update(native.child_environment(cpu_slots=4))
    code = """
import json, numpy as np
import os
matrix = np.ones((128,128))
assert float((matrix @ matrix)[0,0]) == 128
print(json.dumps(len(os.listdir('/proc/self/task'))))
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                               env=env, text=True, capture_output=True, timeout=20, check=True)
    threads = json.loads(completed.stdout)
    assert 1 <= threads <= 2
