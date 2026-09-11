"""Release/deploy orchestration: isolated files and processes, no live cutover."""
from __future__ import annotations

import importlib.util
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "internal/tools/rm0008_release_cycle.py"
spec = importlib.util.spec_from_file_location("rm0008_release_cycle_tested", SOURCE)
cycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cycle)


@pytest.fixture
def release(monkeypatch, tmp_path):
    import executor_birth_account_identity as accounts

    root = str(tmp_path / "release")
    record = accounts.PosixAccountRecordV1("service-test", 23100, 23101,
                                         str(tmp_path / "service"), "/bin/false")
    account = accounts.PosixAccountSnapshotV1(record, (23101, 23102))
    descriptor = NS(
        installation_root=root, service_user=record.name,
        service_uid=record.uid, service_gid=record.gid,
        service_home=record.home, service_shell=record.shell,
        service_supplementary_gids=account.supplementary_gids,
        python_executable=os.path.realpath("/usr/bin/python3"),
        system_unit_root=str(tmp_path / "units"),
    )
    distribution = NS(installation_root=root, release_sequence=42,
                      identity=NS(closed_build_id="sha256:test-release"),
                      encoded=b"distribution", signature=b"signature")
    environment = {"METNOS_USER_DATA": record.home + "/data",
                   "METNOS_USER_STATE": record.home + "/state",
                   "METNOS_USER_CONFIG": record.home + "/config",
                   "METNOS_WORKSPACE": record.home + "/workspace"}
    entry = NS(entry_id=cycle.RELEASE_EDITS_ENTRY, scope="system",
               execution_kind="python_module", python_module="stack_reconcile",
               target_executable=root + "/managed/bin/python3",
               target_working_directory=root + "/runtime",
               target_environment=tuple(NS(name=k, value=v)
                                        for k, v in environment.items()))
    catalog = NS(catalog=NS(entries=(entry,)), unit_fragments=())
    live = NS(distribution=NS(facts=NS(
        closed_build_id=distribution.identity.closed_build_id,
        installation_root=root, release_sequence=42)),
        transaction=NS(head_id="test-head"))
    monkeypatch.setattr(accounts, "resolve_posix_account_snapshot_v1", lambda name: account)
    monkeypatch.setattr(cycle.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cycle, "load_live_helper", lambda: NS(
        _attest_service_startup_v1=lambda entry_id: (live, entry)))
    monkeypatch.setattr(cycle, "_service_restart_granted", lambda user: True)
    return NS(distribution=distribution, descriptor=descriptor, catalog=catalog,
              entry=entry, live=live, account=account, environment=environment)


def success(*, restarted=True):
    row = {"name": "alpha", "outcome": "store_verified", "request_id": "r-1",
           "candidate_id": "c-1", "previous_generation_id": "g-1",
           "current_generation_id": "g-2"}
    if not restarted:
        return {"ok": True, "signed": [{"name": "alpha", "outcome": "unchanged"}],
                "restarted": False}
    return {"ok": True, "signed": [row], "restarted": True,
            "readiness": {"ok": True, "ready": True}, "activated": [row]}


def run_edits(release, *, plan=False):
    return cycle._run_release_edits(release.distribution, release.descriptor,
                                    release.catalog, plan_only=plan)


@pytest.mark.parametrize("plan", [False, True])
def test_child_uses_signed_identity_and_no_inherited_environment(
        monkeypatch, release, plan):
    monkeypatch.setenv("PYTHONPATH", "/untrusted/module")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted/library")
    monkeypatch.setenv("METNOS_SERVICE_USER", "root")
    calls = []
    monkeypatch.setattr(cycle.subprocess, "run",
                        lambda command, **kw: calls.append((command, kw)))
    cycle._release_edits_child(release.distribution, release.descriptor,
                              release.catalog, plan_only=plan)
    assert len(calls) == 1
    command, kw = calls[0]
    assert command == [release.entry.target_executable, "-E", "-s", "-B", "-m",
                       "stack_reconcile", "deploy", "--changed-only",
                       "--plan" if plan else "--sign"]
    record = release.account.record
    assert kw == dict(
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
        cwd=release.entry.target_working_directory,
        env={"HOME": record.home, "USER": record.name, "LOGNAME": record.name,
             "SHELL": record.shell,
             "METNOS_INSTALL_ROOT": release.distribution.installation_root,
             **release.environment},
        user=23100, group=23101, extra_groups=[23101, 23102],
        umask=0o027, close_fds=True, timeout=120 if plan else 600)


@pytest.mark.parametrize("field,value", [
    ("service_uid", 0), ("service_gid", 0), ("service_supplementary_gids", ()),
    ("service_home", "/root"), ("service_shell", "/bin/sh"),
    ("installation_root", "/different-release"),
])
def test_child_refuses_account_or_descriptor_drift(monkeypatch, release, field, value):
    setattr(release.descriptor, field, value)
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(RuntimeError):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False)


@pytest.mark.parametrize("change", ["missing", "duplicate", "module", "scope", "env"])
def test_child_refuses_invalid_signed_launch_projection(monkeypatch, release, change):
    if change == "missing":
        release.catalog.catalog.entries = ()
    elif change == "duplicate":
        release.catalog.catalog.entries *= 2
    elif change == "env":
        release.entry.target_environment += (NS(name="HOME", value="/root"),)
    else:
        setattr(release.entry, "python_module" if change == "module" else "scope", "other")
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(RuntimeError):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=True)


@pytest.mark.parametrize("restarted", [False, True])
def test_success_keeps_receipts_and_activation(monkeypatch, release, capsys, restarted):
    payload = success(restarted=restarted)
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, json.dumps(payload).encode()))
    assert run_edits(release) == 0
    line = capsys.readouterr().out
    assert line.startswith("RELEASE_EDITS_ADMITTED ")
    summary = json.loads(line.split(" ", 1)[1])
    assert summary["result"] == payload
    assert summary["cutover_completed"] is True
    assert summary["head_id"] == "test-head" and summary["timeout_s"] == 600


def test_plan_does_not_require_live_selection_or_restart_permission(monkeypatch, release, capsys):
    monkeypatch.setattr(cycle, "load_live_helper", lambda: pytest.fail("live proof"))
    monkeypatch.setattr(cycle, "_service_restart_granted", lambda u: pytest.fail("policy"))
    calls = []

    def child(*args, plan_only):
        calls.append(plan_only)
        return subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "plan": [{"name": "alpha", "outcome": "changed"}]}).encode())

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert run_edits(release, plan=True) == 0
    assert calls == [True]
    summary = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert summary["admission_attempted"] is False and summary["timeout_s"] == 120


@pytest.mark.parametrize("field,value", [
    ("closed_build_id", "different"), ("installation_root", "/other"),
    ("release_sequence", 43),
])
def test_changed_live_selection_never_reaches_child(monkeypatch, release, capsys, field, value):
    setattr(release.live.distribution.facts, field, value)
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k: pytest.fail("launched"))
    assert run_edits(release) == 78
    out = capsys.readouterr().out
    assert "RELEASE_EDITS_REFUSED release_selection_changed" in out
    assert '"effects": "no_admissions"' in out
    assert '"cutover_completed": true' in out


def test_rule_refusal_precedes_any_admission(monkeypatch, release, capsys):
    monkeypatch.setattr(cycle, "_service_restart_granted", lambda user: False)
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k: pytest.fail("launched"))
    assert run_edits(release) == 78
    assert "RELEASE_EDITS_REFUSED service_restart_not_granted" in capsys.readouterr().out


def test_partial_failure_preserves_all_outcomes_and_restart_owed(monkeypatch, release, capsys):
    admitted = success()["signed"][0]
    payload = {"ok": False, "error_code": "birth_admission_failed", "details": {
        "outcomes": [admitted, {"name": "beta", "outcome": "error"},
                     {"name": "gamma", "outcome": "not_attempted"}],
        "activation_pending": [admitted], "restart_owed": True}}
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1, json.dumps(payload).encode()))
    assert run_edits(release) == 78
    out = capsys.readouterr().out
    assert "RELEASE_EDITS_ADMITTED" not in out
    assert json.loads(out.split(" ", 2)[2])["result"] == payload


@pytest.mark.parametrize("stdout", [
    b"", b"not json", b"[]", b'{"ok":true}', b'{"ok":1,"signed":[]}',
    b'{"ok":true,"signed":[{}],"restarted":false}',
    b'{"ok":true,"signed":[],"restarted":true}', b"\xff",
])
def test_malformed_output_never_claims_success(monkeypatch, release, capsys, stdout):
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, stdout))
    assert run_edits(release) == 78
    out = capsys.readouterr().out
    assert "RELEASE_EDITS_REFUSED release_edits_output_invalid" in out
    assert '"effects": "unknown_check_pending_activation"' in out


@pytest.mark.parametrize("invalid", ["nonzero_exit", "missing_receipt", "not_activated",
                                    "malformed_activation", "no_restart", "not_ready"])
def test_success_requires_receipts_and_actual_activation(monkeypatch, release, capsys, invalid):
    payload = success()
    if invalid == "missing_receipt":
        payload["signed"][0].pop("current_generation_id")
    elif invalid == "not_activated":
        payload["activated"] = []
    elif invalid == "malformed_activation":
        payload["activated"] = ["not a receipt"]
    elif invalid == "no_restart":
        payload["restarted"] = False
    elif invalid == "not_ready":
        payload["readiness"]["ok"] = False
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1 if invalid == "nonzero_exit" else 0,
                                                    json.dumps(payload).encode()))
    assert run_edits(release) == 78
    assert "RELEASE_EDITS_ADMITTED" not in capsys.readouterr().out


@pytest.mark.parametrize("plan", [False, True])
def test_timeout_reports_declared_limit_and_uncertainty(monkeypatch, release, capsys, plan):
    def child(*args, **kwargs):
        raise subprocess.TimeoutExpired("test", 120 if plan else 600)

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert run_edits(release, plan=plan) == 78
    output = capsys.readouterr().out
    assert output.startswith("RELEASE_EDITS_PLAN_REFUSED " if plan else "RELEASE_EDITS_REFUSED ")
    summary = json.loads(output.split(" ", 2)[2])
    assert summary["error_code"] == "release_edits_timeout"
    assert summary["timeout_s"] == (120 if plan else 600)
    assert summary["effects"] == ("no_admissions" if plan else "unknown_check_pending_activation")


@pytest.mark.parametrize("content", [None, b"different", b"expected\n"])
def test_policy_requires_exact_existing_rule(monkeypatch, tmp_path, content):
    import services_registry

    path = tmp_path / "rule"
    if content is not None:
        path.write_bytes(content)
        path.chmod(0o644)
    monkeypatch.setattr(services_registry, "render_polkit_rule", lambda user: "expected\n")
    # Real file reads; only ownership is projected to root in this unprivileged
    # test. No access to the host's policy directory or its services.
    fstat = os.fstat

    def as_root(info):
        names = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                 "st_size", "st_mtime_ns", "st_ctime_ns")
        values = {name: getattr(info, name) for name in names}
        return NS(**{**values, "st_uid": 0})

    class PolicyPath:
        def __fspath__(self):
            return str(path)

        def lstat(self):
            return as_root(path.lstat())

    monkeypatch.setattr(cycle, "SERVICE_RESTART_POLICY", PolicyPath())
    monkeypatch.setattr(cycle.os, "fstat", lambda fd: as_root(fstat(fd)))
    assert cycle._service_restart_granted("service-test") is (content == b"expected\n")
    assert path.exists() is (content is not None)
    if content is not None:
        assert path.read_bytes() == content


@pytest.fixture
def crossing(monkeypatch, release, tmp_path):
    import executor_birth_account_identity as accounts
    import executor_birth_distribution_manifest as distributions
    import executor_birth_prepared_root as prepared
    import executor_birth_service_catalog as catalogs
    import install.executor_birth_transition as transition
    import stack_reconcile

    # cross() intentionally replaces these in its dedicated real interpreter.
    # Preserve the enclosing pytest process when calling it in-process here.
    monkeypatch.setattr(cycle.os, "environ", dict(os.environ))
    monkeypatch.setattr(cycle.sys, "path", list(sys.path))
    monkeypatch.setattr(cycle.sys, "dont_write_bytecode", True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "distribution.json").write_bytes(b"distribution")
    (evidence / "distribution.sig").write_bytes(b"signature")
    monkeypatch.setattr(accounts, "resolve_posix_account_v1", lambda n: release.account.record)
    monkeypatch.setattr(distributions, "authenticate_distribution_record_v1", lambda *a: release.distribution)
    monkeypatch.setattr(distributions, "verify_installed_distribution_record_v1", lambda d: d)
    monkeypatch.setattr(distributions, "capture_current_deployment_descriptor_v1",
                        lambda d: (None, release.descriptor))
    monkeypatch.setattr(distributions, "capture_previous_release_artifacts_v1", lambda *a: None)
    monkeypatch.setattr(prepared, "load_previous_context_runtime_v1", lambda d: NS(
        selection=NS(distribution=release.distribution), required_head_id="old-head"))
    monkeypatch.setattr(catalogs, "load_previous_service_catalog_v1", lambda d: release.catalog)
    monkeypatch.setattr(catalogs, "load_service_catalog_v1", lambda d: release.catalog)
    monkeypatch.setattr(stack_reconcile, "StackReconciler", lambda **kw: NS(
        require_quiescent=lambda: {"ok": True, "source": "test-idle"}))
    monkeypatch.setattr(transition, "_handoff_frame_v1", lambda **kw: None)
    events = []
    control = {"cutover_fails": False, "plan_fails": False}

    def complete(**kwargs):
        events.append("cutover")
        if control["cutover_fails"]:
            raise RuntimeError("test cutover failure")
        return {"state": "test-completed"}

    def edits(*args, plan_only):
        events.append("plan" if plan_only else "admit")
        if plan_only and control["plan_fails"]:
            cycle.say("RELEASE_EDITS_PLAN_REFUSED", "test_preview_failed")
        return 78 if plan_only and control["plan_fails"] else 0

    monkeypatch.setattr(transition, "_complete_closed_v1", complete)
    monkeypatch.setattr(cycle, "_run_release_edits", edits)
    return NS(events=events, control=control, release=release,
              run=lambda mode: cycle.cross(release.distribution.installation_root,
                                           "source-test", str(evidence), mode))


def test_audit_only_previews(crossing):
    assert crossing.run("audit") == 0
    assert crossing.events == ["plan"]


def test_admission_is_strictly_after_successful_cutover(crossing, capsys):
    assert crossing.run("complete") == 0
    assert crossing.events == ["plan", "cutover", "admit"]
    assert "CUTOVER_OK" in capsys.readouterr().out


def test_failed_cutover_never_launches_admission(crossing, capsys):
    crossing.control["cutover_fails"] = True
    with pytest.raises(RuntimeError, match="test cutover failure"):
        crossing.run("complete")
    assert crossing.events == ["plan", "cutover"]
    assert "CUTOVER_OK" not in capsys.readouterr().out


def test_failed_preview_ships_the_release_but_admits_nothing(crossing, capsys):
    crossing.control["plan_fails"] = True
    assert crossing.run("complete") == 78
    assert crossing.events == ["plan", "cutover"]
    out = capsys.readouterr().out
    assert out.index("CUTOVER_OK") < out.index("RELEASE_EDITS_REFUSED preview_failed")
    summary = json.loads(out.split("RELEASE_EDITS_REFUSED preview_failed ", 1)[1])
    assert summary["cutover_completed"] is True
    assert summary["admission_attempted"] is False and summary["effects"] == "no_admissions"


def test_audit_preview_refusal_has_a_distinct_internal_result(crossing):
    crossing.control["plan_fails"] = True
    assert crossing.run("audit") == cycle.AUDIT_PREVIEW_REFUSED == 79
    assert crossing.events == ["plan"]


def test_complete_without_admission_never_launches_any_deploy_child(crossing, capsys):
    # Even a preview that would succeed cannot clear an earlier refusal.
    crossing.control["plan_fails"] = False
    assert crossing.run("complete-no-admission") == 78
    assert crossing.events == ["cutover"]
    out = capsys.readouterr().out
    assert out.index("CUTOVER_OK") < out.index("RELEASE_EDITS_REFUSED preview_failed")
    assert '"effects": "no_admissions"' in out


@pytest.mark.parametrize("mode", ["complete", "complete-no-admission"])
def test_failed_crossing_never_claims_completed_release_after_preview_refusal(crossing, capsys, mode):
    crossing.control.update(plan_fails=True, cutover_fails=True)
    with pytest.raises(RuntimeError, match="test cutover failure"):
        crossing.run(mode)
    assert crossing.events == (["plan", "cutover"] if mode == "complete" else ["cutover"])
    out = capsys.readouterr().out
    assert "CUTOVER_OK" not in out and "RELEASE_EDITS_REFUSED preview_failed" not in out


def test_structural_audit_refusal_is_not_a_preview_only_result(crossing):
    # A structural identity failure must stop before the optional preview.
    crossing.release.descriptor.python_executable = "/not-the-os-interpreter"
    with pytest.raises(RuntimeError, match="administrative interpreter"):
        crossing.run("audit")
    assert crossing.events == []


@pytest.mark.parametrize("mode", ["audit", "complete", "complete-no-admission"])
def test_main_accepts_only_the_three_closed_crossing_modes(monkeypatch, mode):
    calls = []
    monkeypatch.setattr(cycle.sys, "argv", ["cycle", "_cross", "release", "source", "evidence", mode])
    monkeypatch.setattr(cycle, "cross", lambda *args: calls.append(args) or 17)
    assert cycle.main() == 17
    assert calls == [("release", "source", "evidence", mode)]


@pytest.mark.parametrize("mode", ["", "complete-unchecked", "skip-auth", "audit --sign"])
def test_main_refuses_every_unrecognised_crossing_mode(monkeypatch, mode):
    monkeypatch.setattr(cycle.sys, "argv", ["cycle", "_cross", "release", "source", "evidence", mode])
    monkeypatch.setattr(cycle, "cross", lambda *args: pytest.fail("unrecognised mode crossed"))
    with pytest.raises(RuntimeError, match="internal crossing arguments"):
        cycle.main()


@pytest.fixture
def applying(monkeypatch, release, tmp_path):
    """Run the actual parent while isolating its earlier build/provision steps."""
    import contract_boundary_guard as guard
    import executor_birth_account_identity as accounts
    from install import executor_birth_source_receiver as receiver
    from install import executor_birth_distribution_release as builder

    staging = tmp_path / "candidate"
    staging.mkdir()
    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps({
        "staging": str(staging), "files": 1, "census": "test-census",
        "reviewed_root": guard.BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
    }))
    helper = tmp_path / "unchanged-live-helper"
    helper.write_bytes(b"test: not executed")
    monkeypatch.setattr(cycle, "HANDOFF", handoff)
    monkeypatch.setattr(cycle, "LIVE_HELPER", helper)
    monkeypatch.setattr(cycle, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(cycle.os, "environ", dict(os.environ))
    monkeypatch.setattr(cycle.sys, "path", list(sys.path))
    monkeypatch.setattr(cycle.sys, "dont_write_bytecode", True)
    monkeypatch.setattr(cycle, "census", lambda p: (1, "test-census"))
    monkeypatch.setattr(cycle, "adopt_candidate", lambda *args: staging)
    monkeypatch.setattr(guard, "load_inventory", lambda p: {})
    monkeypatch.setattr(guard, "discover", lambda p: [])
    monkeypatch.setattr(guard, "birth_closed_findings", lambda *args: [])
    monkeypatch.setattr(guard, "closed_python_source_review_finding", lambda p: None)
    # A synthetic record satisfies the pre-existing installation profile; no
    # account lookup, access to its home or actual provisioning is performed.
    account = accounts.PosixAccountRecordV1(
        "metnos", 995, 985, "/var/lib/metnos-service", "/bin/false")
    monkeypatch.setattr(accounts, "resolve_posix_account_v1", lambda n: account)
    monkeypatch.setattr(receiver, "_receive_source_v1", lambda *args: "test-source")
    monkeypatch.setattr(cycle, "acquire_locks", lambda locks: None)
    monkeypatch.setattr(cycle, "withdraw_superseded_claim", lambda source: None)
    monkeypatch.setattr(cycle, "retire_orphan_journals", lambda withdrawn: [])
    monkeypatch.setattr(builder, "build_and_install_received_source_v1", lambda s: release.distribution)
    monkeypatch.setattr(cycle, "publish_evidence", lambda *args: None)
    calls = []
    control = {"audit": 0, "complete": 0, "complete-no-admission": 78}

    def child(command):
        calls.append(command)
        return control[command[-1]]

    monkeypatch.setattr(cycle, "run_child", child)
    return NS(calls=calls, control=control)


@pytest.mark.parametrize("cross", [False, True])
@pytest.mark.parametrize("audit_result", [0, 79, 78, 1, 124, -9])
def test_parent_preserves_audit_refusal_across_processes(applying, cross, audit_result):
    applying.control["audit"] = audit_result
    result = cycle.apply_cycle(cross)
    modes = [command[-1] for command in applying.calls]
    if cross and audit_result == 0:
        assert result == 0 and modes == ["audit", "complete"]
    elif cross and audit_result == 79:
        assert result == 78 and modes == ["audit", "complete-no-admission"]
    else:
        assert result == (0 if audit_result == 0 else 78) and modes == ["audit"]
    if len(applying.calls) == 2:
        assert applying.calls[0][:-1] == applying.calls[1][:-1]


@pytest.fixture
def cutover_layout(monkeypatch, tmp_path):
    import pwd
    import contract_cutover_guard as guard
    import contract_store
    import executor_birth_account_identity as accounts
    import executor_birth_service_catalog as catalogs
    import install.executor_birth_systemd_quiescence as quiescence
    import services_registry

    account = accounts.PosixAccountRecordV1(
        "service-test", os.getuid() or 23100, os.getgid(),
        str(tmp_path / "service"), "/bin/false")
    layout = accounts.metnos_xdg_layout_v1(account)
    python = str(tmp_path / "managed/bin/python")
    built = catalogs._build_service_catalog_v1(
        installation_root=str(tmp_path / "previous-release"), python_executable=python,
        service_user=account.name, service_gid=account.gid,
        service_supplementary_gids=(account.gid,), service_home=account.home,
        systemctl_executable="/usr/bin/systemctl",
        target_executables=tuple((path, path.encode()) for path in (
            python, "/usr/bin/systemctl", "/usr/bin/Xvfb")))
    catalog = catalogs.LoadedServiceCatalogV1(
        catalogs.decode_service_catalog_v1(built.encoded), built.unit_fragments,
        catalogs._LOADED_CATALOG_SEAL)
    watchdog = next(item for item in catalog.catalog.entries
                    if item.entry_id == cycle.RELEASE_EDITS_ENTRY)
    environment = {item.name: item.value for item in watchdog.target_environment}
    assert Path(environment["METNOS_USER_STATE"]) == layout.state
    monkeypatch.setattr(services_registry._C, "PATH_USER_STATE",
                        Path(environment["METNOS_USER_STATE"]))
    monkeypatch.setattr(services_registry._C, "PATH_USER_DATA",
                        Path(environment["METNOS_USER_DATA"]))
    monkeypatch.setattr(services_registry, "service_user", lambda: account.name)
    getpwuid, getpwnam = pwd.getpwuid, pwd.getpwnam
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: NS(pw_name=account.name)
                        if uid == account.uid else getpwuid(uid))
    monkeypatch.setattr(pwd, "getpwnam", lambda name: NS(pw_uid=account.uid)
                        if name == account.name else getpwnam(name))
    monkeypatch.setattr(accounts, "resolve_posix_account_v1", lambda name: account)
    monkeypatch.setattr(catalogs, "capture_current_service_catalog_v1", lambda d: catalog)
    monkeypatch.setattr(contract_store, "catalog_admission_lock",
                        lambda **kw: contextlib.nullcontext())
    events = []
    monkeypatch.setattr(quiescence, "_quiesce_release_systemd_core_v1",
                        lambda *args: events.append("quiesce"))
    monkeypatch.setattr(quiescence, "_SubprocessSystemdEffectsV1", lambda: NS())
    monkeypatch.setattr(guard._MaintenanceProofV1, "observe", lambda self: {"test": "observed"})
    return NS(account=account, layout=layout, catalog=catalog, events=events)


def test_successor_cutover_lock_uses_bound_previous_catalog(monkeypatch, tmp_path, cutover_layout):
    """The successor runs before the required head names that successor.

    Keep the actual readiness root check and actual lifecycle lock: mocking
    either would hide a release cycle that cannot cross its own first lock.
    """
    import contract_cutover_guard as guard
    import executor_birth_ownership_chain as ownership
    import services_registry
    import stack_reconcile

    root = tmp_path / "chain"
    root.mkdir()
    (root / ownership.REQUIRED_HEAD_BASENAME).write_bytes(b"test-required")
    old = NS(installation_root=str(tmp_path / "previous-release"))
    selected = {"distribution": old}
    monkeypatch.setattr(ownership, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", root)
    monkeypatch.setattr(ownership, "OwnershipChainStore", lambda: NS(
        read_required_chain_cold_v1=lambda: ownership.VerifiedOwnershipChain(
            "test-anchor", (), required_distribution=selected["distribution"])))
    monkeypatch.setattr(services_registry._C, "PATH_ROOT", tmp_path / "successor-release")
    # This refusal must remain intact for ordinary consumers; cutover instead
    # already has the authenticated previous catalog bound by its caller.
    with pytest.raises(ValueError, match="readiness distribution root mismatch"):
        services_registry.stack_scope()
    account = cutover_layout.account
    with guard._contract_cutover_guard_core_v1(
            NS(), catalog_trusted_owner=(account.uid, account.gid),
            release_catalog=cutover_layout.catalog) as (_proof, evidence):
        assert evidence == {"test": "observed"}
        path = cutover_layout.layout.state / "metnos-stack-reconcile.lock"
        assert path.is_file() and path.stat().st_uid == account.uid
        # Simulate selection advancing inside the same held cutover, then
        # compare the actual watchdog default derived from its signed env.
        selected["distribution"] = NS(installation_root=str(services_registry._C.PATH_ROOT))
        watchdog_lock = stack_reconcile.ReconcileLock()
        assert watchdog_lock.path == path and watchdog_lock.owner_uid == account.uid
        with pytest.raises(stack_reconcile.StackFailure) as caught:
            watchdog_lock.acquire()
        assert caught.value.code == "reconcile_busy"
    with watchdog_lock:
        pass
    assert cutover_layout.events == ["quiesce"]


@pytest.mark.parametrize("invalid", ["missing_owner", "boolean_uid", "root_uid",
                                    "wrong_gid", "wrong_state", "unsealed_catalog"])
def test_cutover_rejects_incoherent_lifecycle_binding(monkeypatch, cutover_layout, invalid):
    import contract_cutover_guard as guard
    import services_registry

    account = cutover_layout.account
    owner = (account.uid, account.gid)
    catalog = cutover_layout.catalog
    if invalid == "missing_owner":
        owner = None
    elif invalid == "boolean_uid":
        owner = (True, account.gid)
    elif invalid == "root_uid":
        owner = (0, account.gid)
    elif invalid == "wrong_gid":
        owner = (account.uid, account.gid + 1)
    elif invalid == "wrong_state":
        monkeypatch.setattr(services_registry._C, "PATH_USER_STATE", "/different-state")
    else:
        catalog = NS(catalog=catalog.catalog, unit_fragments=catalog.unit_fragments)
    with pytest.raises(guard.ContractCutoverGuardError) as caught:
        with guard._contract_cutover_guard_core_v1(
                NS(), catalog_trusted_owner=owner, release_catalog=catalog):
            pytest.fail("incoherent binding accepted")
    assert caught.value.code == "cutover_lock_unavailable"
    assert cutover_layout.events == []


def test_initial_cutover_keeps_user_profile_without_previous_catalog(monkeypatch, tmp_path):
    import contract_cutover_guard as guard
    import contract_store
    import executor_birth_account_identity as accounts

    runtime = tmp_path / "user-runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(accounts, "resolve_posix_account_v1",
                        lambda name: pytest.fail("previous release account lookup"))
    monkeypatch.setattr(contract_store, "catalog_admission_lock",
                        lambda **kw: contextlib.nullcontext())
    monkeypatch.setattr(guard._MaintenanceProofV1, "observe", lambda self: {})
    with guard._contract_cutover_guard_core_v1(NS()):
        assert (runtime / "metnos-stack-reconcile.lock").is_file()
