from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tests" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import release_gate as gate  # noqa: E402


def _minimal_release(root: Path) -> Path:
    files = {
        "requirements.txt": "",
        "docs/en/index.html": "<html lang='en'></html>",
        "runtime/metnos_http_server.py": "",
        "runtime/agent_runtime.py": "",
        "runtime/loader.py": "",
        "runtime/published_docs.py": "",
        "runtime/sign.py": "",
        "scripts/compile_tutor_catalog.py": "",
        "tutor/sources.toml": "",
        "install/data/i18n_seed.sqlite": "seed",
        "executors/read_files/manifest.toml": (
            'name="read_files"\nmanifest_format="1.0"\n'
            '[code]\nfiles=["read_files.py"]\n'
        ),
        "executors/read_files/read_files.py": "",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_validate_release_tree_is_deterministic_and_accepts_internal_symlink(
    tmp_path: Path,
) -> None:
    release = _minimal_release(tmp_path / "release")
    alias = release / "executors" / "read_files" / "alias.py"
    alias.symlink_to("../../runtime/loader.py")

    first = gate.validate_release_tree(release)
    second = gate.validate_release_tree(release)

    assert first == second
    assert first["file_count"] >= 9
    assert len(first["tree_sha256"]) == 64


def test_validate_release_tree_rejects_escaping_symlink(tmp_path: Path) -> None:
    release = _minimal_release(tmp_path / "release")
    (release / "escape").symlink_to("../../outside")

    with pytest.raises(gate.GateFailure) as exc:
        gate.validate_release_tree(release)

    assert exc.value.code == "unsafe_symlink"
    assert exc.value.details == {"path": "escape"}


def test_materialize_local_source_drops_repository_metadata(tmp_path: Path) -> None:
    source = _minimal_release(tmp_path / "source")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("secret-ish", encoding="utf-8")
    (source / "runtime" / "__pycache__").mkdir()
    (source / "runtime" / "__pycache__" / "x.pyc").write_bytes(b"x")
    destination = tmp_path / "destination"

    result = gate.materialize_source(
        gate.ReleaseSource(str(source)), destination,
        repo_root=ROOT, log_dir=tmp_path / "logs",
    )

    assert result["label"] == "directory:source"
    assert not (destination / ".git").exists()
    assert not (destination / "runtime" / "__pycache__").exists()


def test_source_public_label_never_contains_local_absolute_path(tmp_path: Path) -> None:
    source = gate.ReleaseSource(str(tmp_path / "private" / "checkout"))
    assert source.public_label == "directory:checkout"
    assert str(tmp_path) not in source.public_label


def test_preservation_probes_cover_all_persistent_domains(tmp_path: Path) -> None:
    layout = gate.PersistentLayout.create(tmp_path / "persistent")
    expected = gate.write_preservation_probes(layout)

    assert set(expected) == {"config", "data", "state", "workspace"}
    assert gate.verify_preservation_probes(layout, expected) == expected
    assert all(len(value) == 64 for value in expected.values())


def test_preservation_probe_change_is_detected_without_reporting_value(
    tmp_path: Path,
) -> None:
    layout = gate.PersistentLayout.create(tmp_path / "persistent")
    expected = gate.write_preservation_probes(layout)
    probe = layout.config / "release_gate" / "preservation.probe"
    probe.write_text("changed", encoding="ascii")

    with pytest.raises(gate.GateFailure) as exc:
        gate.verify_preservation_probes(layout, expected)

    assert exc.value.code == "persistent_probe_changed"
    assert exc.value.details == {"domain": "config"}
    assert "changed" not in str(exc.value.details)


def test_synthetic_credential_survives_release_boundary(tmp_path: Path) -> None:
    layout = gate.PersistentLayout.create(tmp_path / "persistent")
    (layout.config / "admin.key").write_text("a" * 64, encoding="ascii")
    logs = tmp_path / "logs"

    expected = gate.write_credential_probe(
        ROOT, layout, Path(sys.executable), log_dir=logs,
    )
    actual = gate.verify_credential_probe(
        ROOT, layout, Path(sys.executable), expected,
        label="same-release", log_dir=logs,
    )

    assert actual == expected
    assert actual["domain"] == gate.CREDENTIAL_PROBE_DOMAIN
    assert len(actual["fingerprint"]) == 16
    assert "password" not in json.dumps(actual)


def test_production_regression_is_blocking() -> None:
    before = {
        "health": {"ok": True, "status": 200},
        "worktree": {"available": True, "digest": "same", "entry_count": 2},
    }
    after = {
        "health": {"ok": False},
        "worktree": {"available": True, "digest": "same", "entry_count": 2},
    }

    with pytest.raises(gate.GateFailure) as exc:
        gate._production_unchanged(before, after)

    assert exc.value.code == "production_health_regressed"


def test_report_json_is_atomic_and_machine_readable(tmp_path: Path) -> None:
    report = gate.GateReport(
        gate_id="test-gate",
        profile=gate.PROFILE,
        baseline="directory:baseline",
        candidate="current-public-export",
        started_at="2026-07-16T00:00:00Z",
        finished_at="2026-07-16T00:00:01Z",
        ok=True,
        steps=[gate.StepResult("x", True, 0.1, {"loaded": 10})],
    )
    path = tmp_path / "report.json"

    gate._write_json_atomic(path, report.as_dict())
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == gate.SCHEMA_VERSION
    assert payload["ok"] is True
    assert payload["steps"][0]["details"] == {"loaded": 10}
    assert not any(p.name.startswith(".report.json.") for p in tmp_path.iterdir())


def test_runtime_environment_uses_explicit_cross_platform_workspace(
    tmp_path: Path,
) -> None:
    layout = gate.PersistentLayout.create(tmp_path / "persistent")
    env = gate._runtime_env(ROOT, layout, Path(sys.executable))

    assert env["METNOS_WORKSPACE"] == str(layout.workspace)
    assert os.defpath in env["PATH"]
    assert env["METNOS_VENV"]


def test_virtualenv_interpreter_follows_host_convention(tmp_path: Path) -> None:
    value = gate._venv_python(tmp_path / "venv")
    if os.name == "nt":
        assert value.parts[-2:] == ("Scripts", "python.exe")
    else:
        assert value.parts[-2:] == ("bin", "python")


def test_markdown_report_is_atomic(tmp_path: Path) -> None:
    report = gate.GateReport(
        gate_id="test-gate",
        profile=gate.PROFILE,
        baseline="directory:baseline",
        candidate="current-public-export",
        started_at="2026-07-16T00:00:00Z",
        finished_at="2026-07-16T00:00:01Z",
        ok=True,
    )
    path = tmp_path / "report.md"

    gate._write_markdown(path, report)

    assert "Result: `PASS`" in path.read_text(encoding="utf-8")
    assert not any(p.name.startswith(".report.md.") for p in tmp_path.iterdir())


def test_cleanup_removes_only_the_isolated_work_root(tmp_path: Path) -> None:
    work = tmp_path / "isolated"
    work.mkdir()
    (work / "artifact").write_text("x", encoding="ascii")
    sibling = tmp_path / "report.json"
    sibling.write_text("{}", encoding="ascii")

    assert gate._remove_work_root(work) == {"removed": True}
    assert not work.exists()
    assert sibling.is_file()


def test_socket_permission_error_has_stable_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeniedSocket:
        def __init__(self, *_args, **_kwargs):
            raise PermissionError(1, "blocked")

    monkeypatch.setattr(gate.socket, "socket", DeniedSocket)

    with pytest.raises(gate.GateFailure) as exc:
        gate._free_port()

    assert exc.value.code == "network_sandbox_blocked"


def test_public_export_includes_only_the_public_documentation_boundary() -> None:
    exporter = (ROOT / "scripts" / "export-public.sh").read_text(encoding="utf-8")
    assert "internal/|" in exporter
    assert "data/|" in exporter
    assert "docs/|" not in exporter
    assert "docs/([^/]+/)*internal/|" in exporter
    assert "docs/drafts/|" in exporter
    assert "runtime/static/[^/]+\\.html$|" in exporter
    assert "tutor/cards/retired/|" in exporter
    assert "runtime/published_docs.py validate" in exporter
    assert "runtime/published_docs.py files" in exporter


def test_orchestration_runs_fresh_upgrade_and_rollback_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def materialize(source, destination, **_kwargs):
        _minimal_release(destination)
        return {"label": source.public_label, "tree_sha256": "a" * 64}

    def prepare(_tree, _layout, _python, *, label, **_kwargs):
        calls.append(f"prepare:{label}")
        return {"expected": 1, "loaded": 1, "rejected_count": 0}

    def runtime(_tree, _layout, _python, *, label, **_kwargs):
        calls.append(f"runtime:{label}")
        return {"final_kind": "answer", "text_length": 2}

    monkeypatch.setattr(gate, "materialize_source", materialize)
    monkeypatch.setattr(gate, "ensure_venv", lambda *_a, **_kw: Path(sys.executable))
    monkeypatch.setattr(gate, "prepare_runtime", prepare)
    monkeypatch.setattr(gate, "verify_runtime", runtime)
    monkeypatch.setattr(
        gate, "write_credential_probe",
        lambda *_a, **_kw: {"domain": gate.CREDENTIAL_PROBE_DOMAIN, "fingerprint": "a" * 16},
    )
    monkeypatch.setattr(
        gate, "verify_credential_probe",
        lambda *_a, **_kw: {"domain": gate.CREDENTIAL_PROBE_DOMAIN, "fingerprint": "a" * 16},
    )
    monkeypatch.setattr(
        gate, "_production_snapshot",
        lambda *_a, **_kw: {
            "health": {"ok": True},
            "worktree": {"available": True, "digest": "same", "entry_count": 0},
        },
    )

    work = tmp_path / "work"
    work.mkdir()
    report_path = tmp_path / "report.json"
    runner = gate.ReleaseGate(
        repo_root=ROOT,
        baseline=gate.ReleaseSource("baseline"),
        candidate=gate.ReleaseSource("candidate"),
        work_root=work,
        report_path=report_path,
        keep=True,
        skip_dependencies=True,
        health_url="http://127.0.0.1:1/agent/health",
        query="test",
        ready_timeout_s=1,
        turn_timeout_s=1,
    )

    report = runner.run()

    assert report.ok is True
    assert report.dependency_mode == "current-python"
    assert calls == [
        "prepare:fresh-candidate",
        "runtime:fresh-candidate",
        "prepare:transition-baseline",
        "runtime:transition-baseline",
        "prepare:transition-candidate",
        "runtime:transition-candidate",
        "runtime:rollback-baseline",
    ]
    assert json.loads(report_path.read_text(encoding="utf-8"))["ok"] is True


def test_candidate_failure_still_checks_production_and_retains_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_calls = 0

    def materialize(source, destination, **_kwargs):
        _minimal_release(destination)
        return {"label": source.public_label, "tree_sha256": "b" * 64}

    def runtime(_tree, _layout, _python, *, label, **_kwargs):
        if label == "transition-candidate":
            raise gate.GateFailure("candidate_broken", "candidate failed")
        return {"final_kind": "answer", "text_length": 2}

    def production(*_args, **_kwargs):
        nonlocal production_calls
        production_calls += 1
        return {
            "health": {"ok": True},
            "worktree": {"available": True, "digest": "same", "entry_count": 0},
        }

    monkeypatch.setattr(gate, "materialize_source", materialize)
    monkeypatch.setattr(gate, "ensure_venv", lambda *_a, **_kw: Path(sys.executable))
    monkeypatch.setattr(
        gate, "prepare_runtime",
        lambda *_a, **_kw: {"expected": 1, "loaded": 1, "rejected_count": 0},
    )
    monkeypatch.setattr(gate, "verify_runtime", runtime)
    monkeypatch.setattr(
        gate, "write_credential_probe",
        lambda *_a, **_kw: {"domain": gate.CREDENTIAL_PROBE_DOMAIN, "fingerprint": "b" * 16},
    )
    monkeypatch.setattr(
        gate, "verify_credential_probe",
        lambda *_a, **_kw: {"domain": gate.CREDENTIAL_PROBE_DOMAIN, "fingerprint": "b" * 16},
    )
    monkeypatch.setattr(gate, "_production_snapshot", production)

    work = tmp_path / "failed-work"
    work.mkdir()
    report_path = tmp_path / "failed.json"
    runner = gate.ReleaseGate(
        repo_root=ROOT,
        baseline=gate.ReleaseSource("baseline"),
        candidate=gate.ReleaseSource("candidate"),
        work_root=work,
        report_path=report_path,
        keep=False,
        skip_dependencies=True,
        health_url="http://127.0.0.1:1/agent/health",
        query="test",
        ready_timeout_s=1,
        turn_timeout_s=1,
    )

    report = runner.run()

    assert report.ok is False
    assert report.first_error_code == "candidate_broken"
    assert production_calls == 2
    assert work.exists(), "failed gate must retain diagnostic logs"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["artifacts"]["retained_work_root"] == str(work)
