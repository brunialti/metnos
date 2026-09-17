"""Restart real worker processes, optionally across two distinct runtime trees.

METNOS_LRE_TEST_PREVIOUS_ROOT selects a read-only previous checkout for the
first process. Without it the test covers same-release process recovery.
All inputs, outputs, source authority, logs, configuration and databases are
isolated under pytest's temporary directory; no model or production call.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest


def _snapshot(root):
    with sqlite3.connect(f"file:{root / 'state.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        return {
            "plan": db.execute("SELECT plan_json FROM revisions").fetchone()[0],
            "revision": db.execute("SELECT active_revision_id FROM workloads").fetchone()[0],
            "state": db.execute("SELECT state FROM workloads").fetchone()[0],
            "saved": db.execute("SELECT id,committed_result_id FROM units WHERE state='committed' ORDER BY id").fetchall(),
            "active": db.execute("SELECT COUNT(*) FROM attempts WHERE state IN ('leased','running')").fetchone()[0],
            "results": db.execute("SELECT COUNT(*) FROM results").fetchone()[0],
        }


@pytest.mark.skipif(sys.platform != "linux", reason="real SIGKILL recovery proof")
@pytest.mark.parametrize("mode", ["queued", "interrupted"])
def test_real_process_resumes_saved_work_and_four_pending_writers(tmp_path, mode):
    current = Path(__file__).resolve().parents[3]
    previous = Path(os.environ.get("METNOS_LRE_TEST_PREVIOUS_ROOT", current)).resolve()
    assert (previous / "runtime/durable_workloads/runtime_bindings.py").is_file()
    helper = Path(__file__).with_name("release_resume_worker.py")

    def launch(tree, phase):
        # Deliberately do not inherit METNOS_*, PYTHONPATH or user state.
        environment = {
            "PATH": os.defpath, "LANG": "C.UTF-8", "METNOS_INSTALL_ROOT": str(tree),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "METNOS_USER_CONFIG": str(tmp_path / "config"),
            "METNOS_USER_DATA": str(tmp_path / "data"),
            "METNOS_USER_STATE": str(tmp_path / "state"),
        }
        return subprocess.Popen(
            [sys.executable, "-I", str(helper), str(tree), str(tmp_path), phase],
            env=environment, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )

    first = launch(previous, mode)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "ready.json").exists():
            if first.poll() is not None:
                pytest.fail(first.communicate(timeout=2)[0][-12000:])
            assert time.monotonic() < deadline, "old worker did not reach the boundary"
            time.sleep(0.02)
        before = _snapshot(tmp_path)
        assert len(before["saved"]) == 2  # inventory was sealed during admission
        assert before["active"] == int(mode == "interrupted")
        first.send_signal(signal.SIGTERM if mode == "queued" else signal.SIGKILL)
        first.communicate(timeout=5)
        assert first.returncode == (0 if mode == "queued" else -signal.SIGKILL)
    finally:
        if first.poll() is None:
            first.kill()
        first.communicate(timeout=5)

    second = launch(current, "resume")
    try:
        output, _ = second.communicate(timeout=40)
        assert second.returncode == 0, output[-12000:]
    finally:
        if second.poll() is None:
            second.kill()
        second.communicate(timeout=5)
    after = _snapshot(tmp_path)
    assert after["state"] == "completed"
    assert (after["plan"], after["revision"]) == (before["plan"], before["revision"])
    assert set(before["saved"]) <= set(after["saved"])
    assert len(after["saved"]) == after["results"] == 6
    assert after["active"] == 0
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == len({call["unit"] for call in calls}) == 6
    assert sum(call["mode"] == "resume" for call in calls) == 4
    assert sorted(path.read_text() for path in (tmp_path / "outputs").iterdir()) == [
        f"synthetic payload {index}" for index in range(6)]
    assert json.loads((tmp_path / "resumed.json").read_text())["peak"] == 4
