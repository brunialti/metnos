from __future__ import annotations

from pathlib import Path

import test_runner


def test_parallel_equivalence_accepts_identical_structural_results(
        monkeypatch) -> None:
    monkeypatch.setattr(
        test_runner, "run_executor",
        lambda *_args, **_kwargs: (
            0, {"ok": True, "entries": [{"path": "a"}]}, ""),
    )
    assert test_runner.check_parallel_equivalence(
        "executor.py", {}, {},
        {"ok": True, "entries": [{"path": "a"}]}, 3,
    ) == []


def test_parallel_equivalence_rejects_output_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        test_runner, "run_executor",
        lambda *_args, **_kwargs: (0, {"ok": True, "value": 2}, ""),
    )
    failures = test_runner.check_parallel_equivalence(
        "executor.py", {}, {}, {"ok": True, "value": 1}, 2)
    assert failures == [
        "parallel_equivalence[1]: risultato diverso dal baseline",
        "parallel_equivalence[2]: risultato diverso dal baseline",
    ]


def test_parallel_equivalence_has_bounded_run_count() -> None:
    assert test_runner.check_parallel_equivalence(
        "executor.py", {}, {}, {"ok": True}, 9,
    ) == ["equivalence_runs: atteso intero 2..8"]


def test_reference_runner_executes_repository_pytest_node(tmp_path, monkeypatch) -> None:
    target = Path(test_runner.__file__).resolve().parents[1] / "tests" / \
        "runtime" / "executors" / "test_executor_parallel_equivalence.py"
    seen = {}

    class _Result:
        returncode = 0
        stdout = "1 passed"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(test_runner.subprocess, "run", fake_run)
    rc, stdout, stderr = test_runner.run_reference(
        "tests/runtime/executors/test_executor_parallel_equivalence.py::"
        "test_parallel_equivalence_has_bounded_run_count")

    assert (rc, stdout, stderr) == (0, "1 passed", "")
    assert seen["argv"][:4] == [
        test_runner.sys.executable, "-m", "pytest", "-q"]
    assert seen["argv"][4].startswith(str(target) + "::")
    assert seen["kwargs"]["cwd"] == test_runner._REPO_ROOT


def test_reference_runner_rejects_paths_outside_repository() -> None:
    rc, _stdout, stderr = test_runner.run_reference("../outside.py")
    assert rc == 2
    assert stderr == "reference outside repository: ../outside.py"
