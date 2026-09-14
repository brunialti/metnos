"""Release/deploy orchestration: isolated files and processes, no live cutover."""
from __future__ import annotations

import importlib.util
import contextlib
import hashlib
import json
import os
import re
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
                      encoded=b"distribution", signature=b"signature",
                      files=(NS(path="runtime/stack_reconcile.py"),
                             NS(path="runtime/config.py")))
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


def _unit_parts(argv):
    """Split a transient-unit argv into flags, properties and service argv."""
    split = argv.index("--")
    head, service = argv[:split], argv[split + 1:]
    properties = [head[i + 1] for i, item in enumerate(head) if item == "-p"]
    flags = [item for i, item in enumerate(head)
             if item != "-p" and (i == 0 or head[i - 1] != "-p")]
    return flags, properties, service


def _capture_runs(monkeypatch, results):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        outcome = results.pop(0) if results else subprocess.CompletedProcess(argv, 0, b"{}", b"")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(cycle.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("plan", [False, True])
def test_child_runs_in_one_delegated_transient_unit(monkeypatch, release, plan):
    monkeypatch.setenv("PYTHONPATH", "/untrusted/module")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted/library")
    monkeypatch.setenv("METNOS_SERVICE_USER", "root")
    calls = _capture_runs(monkeypatch, [])
    cycle._release_edits_child(release.distribution, release.descriptor,
                              release.catalog, plan_only=plan)
    assert len(calls) == 1
    argv, kw = calls[0]
    assert kw == dict(stdin=subprocess.DEVNULL, capture_output=True, check=False,
                      env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                           "LANG": "C", "LC_ALL": "C"},
                      close_fds=True, timeout=120 if plan else 600)
    flags, properties, service = _unit_parts(argv)
    unit = flags[-1].removeprefix("--unit=")
    assert flags[:-1] == ["/usr/bin/systemd-run", "--wait", "--pipe", "--collect",
                          "--quiet", "--expand-environment=no"]
    assert re.fullmatch(r"metnos-release-edits-(plan|sign)-[0-9a-f]{16}\.service", unit)
    assert ("-plan-" in unit) is plan
    limit = 120 if plan else 600
    assert properties == [
        "Type=exec", "User=23100", "Group=23101", "SupplementaryGroups=23101 23102",
        "Delegate=yes", "DelegateSubgroup=metnos-birth-host", "UMask=0027",
        "KillMode=control-group", "TimeoutStopSec=5", f"RuntimeMaxSec={limit}",
        "NoNewPrivileges=yes", "CapabilityBoundingSet=CAP_SETGID CAP_SETPCAP CAP_SETUID",
        "MemoryAccounting=yes", "TasksAccounting=yes",
        f"WorkingDirectory={release.entry.target_working_directory}"]
    record = release.account.record
    environment = {"HOME": record.home, "USER": record.name, "LOGNAME": record.name,
                   "SHELL": record.shell,
                   "METNOS_INSTALL_ROOT": release.distribution.installation_root,
                   **release.environment}
    assert service == ["/usr/bin/env", "-i",
                       *(f"{k}={v}" for k, v in environment.items()),
                       release.entry.target_executable, "-E", "-s", "-B", "-m",
                       "stack_reconcile", "deploy", "--changed-only",
                       "--plan" if plan else "--sign"]


def test_each_execution_gets_a_fresh_unit_name(monkeypatch, release):
    calls = _capture_runs(monkeypatch, [])
    for _ in range(2):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False)
    assert len({_unit_parts(argv)[0][-1] for argv, _ in calls}) == 2


def test_signed_dollar_values_pass_literally(monkeypatch, release):
    release.entry.target_environment = (NS(name="METNOS_WORKSPACE",
                                           value="/srv/x$HOME/${USER}"),)
    calls = _capture_runs(monkeypatch, [])
    cycle._release_edits_child(release.distribution, release.descriptor,
                              release.catalog, plan_only=True)
    argv = calls[0][0]
    assert "--expand-environment=no" in argv
    assert "METNOS_WORKSPACE=/srv/x$HOME/${USER}" in _unit_parts(argv)[2]


def test_specifier_character_is_refused_before_launch(monkeypatch, release):
    release.entry.target_environment = (NS(name="METNOS_WORKSPACE", value="/srv/%n"),)
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(RuntimeError):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False)


@pytest.mark.parametrize("shown,stopped", [
    (b"LoadState=not-found\nActiveState=inactive\n", True),
    (b"LoadState=loaded\nActiveState=failed\n", True),
    (b"LoadState=loaded\nActiveState=deactivating\n", False),
])
def test_timeout_stops_and_confirms_only_this_unit(monkeypatch, release, shown, stopped):
    expired = subprocess.TimeoutExpired("systemd-run", 600, output=b"partial")
    calls = _capture_runs(monkeypatch, [
        expired, subprocess.CompletedProcess([], 0, b"", b""),
        subprocess.CompletedProcess([], 0, shown, b"")])
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False)
    unit = _unit_parts(calls[0][0])[0][-1].removeprefix("--unit=")
    assert [argv for argv, _ in calls[1:]] == [
        ["/usr/bin/systemctl", "stop", unit],
        ["/usr/bin/systemctl", "show", "--property=LoadState,ActiveState", unit]]
    assert raised.value is expired and expired.release_unit_stopped is stopped
    assert expired.release_unit == unit and expired.output == b"partial"


def test_failed_stop_leaves_the_residue_unconfirmed(monkeypatch, release):
    expired = subprocess.TimeoutExpired("systemd-run", 120)
    _capture_runs(monkeypatch, [expired, OSError("stop unavailable")])
    with pytest.raises(subprocess.TimeoutExpired):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=True)
    assert expired.release_unit_stopped is False


@pytest.mark.parametrize("stop_outcomes,expected", [
    ([subprocess.CompletedProcess([], 0, b"", b""),
      subprocess.CompletedProcess([], 0, b"LoadState=not-found\n", b"")], "confirmed"),
    ([OSError("stop unavailable")], "unconfirmed"),
    ([subprocess.CompletedProcess([], 0, b"", b""),
      subprocess.CompletedProcess([], 0, b"LoadState=loaded\nActiveState=active\n", b"")],
     "unconfirmed"),
])
def test_interruption_records_the_stop_of_this_unit_and_propagates(
        monkeypatch, release, capsys, stop_outcomes, expected):
    interrupted = KeyboardInterrupt()
    calls = _capture_runs(monkeypatch, [interrupted, *stop_outcomes])
    with pytest.raises(KeyboardInterrupt) as raised:
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False)
    assert raised.value is interrupted
    unit = _unit_parts(calls[0][0])[0][-1].removeprefix("--unit=")
    assert calls[1][0] == ["/usr/bin/systemctl", "stop", unit]
    output = capsys.readouterr().out
    tag, record = output.rstrip("\n").split(" ", 1)
    assert tag == "RELEASE_UNIT_STOP" and output.count("\n") == 1
    assert json.loads(record) == {"cause": "interrupted", "stop": expected, "unit": unit}
    assert release.entry.target_executable not in output and "METNOS_" not in output


def test_unit_name_conflict_is_one_refused_attempt(monkeypatch, release, capsys):
    calls = _capture_runs(monkeypatch, [subprocess.CompletedProcess(
        [], 1, b"", b"Failed to start transient service unit: Unit already exists.")])
    assert run_edits(release) == 78
    assert len(calls) == 1
    assert "RELEASE_EDITS_ADMITTED" not in capsys.readouterr().out


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


def test_plan_rechecks_live_selection_but_not_restart_permission(monkeypatch, release, capsys):
    """Review C15: the plan runs after the cutover, on the release now selected."""
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
    assert summary["cutover_completed"] is True and summary["head_id"] == "test-head"


@pytest.mark.parametrize("plan", [False, True])
@pytest.mark.parametrize("field,value", [
    ("closed_build_id", "different"), ("installation_root", "/other"),
    ("release_sequence", 43),
])
def test_changed_live_selection_never_reaches_child(monkeypatch, release, capsys, field, value, plan):
    """A still-selected predecessor (N, not N+1) never reaches either child."""
    setattr(release.live.distribution.facts, field, value)
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k: pytest.fail("launched"))
    assert run_edits(release, plan=plan) == 78
    out = capsys.readouterr().out
    marker = "RELEASE_EDITS_PLAN_REFUSED" if plan else "RELEASE_EDITS_REFUSED"
    assert f"{marker} release_selection_changed" in out
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


def _traceback(root, secret):
    return (
        b"Traceback (most recent call last):\n"
        b'  File "/usr/lib/python3.12/runpy.py", line 198, in _run_module_as_main\n'
        + f'  File "{root}/runtime/stack_reconcile.py", line 31, in <module>\n'.encode()
        + f'  File "{root}/runtime/config.py", line 389, in read\n'.encode()
        + b"    if not target.exists():\n"
        + f"PermissionError: [Errno 13] Permission denied: '/home/x/{secret}'\n".encode()
        + b"\xff\x00\x1b[31m" + b"x" * 5000 + b"\n")


@pytest.mark.parametrize("plan", [True, False])
def test_child_failure_is_located_without_quoting_it(monkeypatch, release, capsys, plan):
    """Review C15: the refusal says where the child failed, never what it said."""
    secret = "sentinel-secret-91c2"
    root = release.distribution.installation_root
    stderr = _traceback(root, secret)
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1, b"", stderr))
    assert run_edits(release, plan=plan) == 78
    out = capsys.readouterr().out
    assert "release_edits_output_invalid" in out
    streams = json.loads(out.split(" ", 2)[2])["child_streams"]
    assert streams["stdout_bytes"] == 0
    assert streams["stderr_bytes"] == len(stderr)
    assert streams["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
    assert streams["exception_type"] == "PermissionError"
    assert streams["release_frames"] == ["runtime/stack_reconcile.py:31",
                                         "runtime/config.py:389"]
    for leaked in (secret, "/home/x", "runpy.py", "target.exists", "Errno"):
        assert leaked not in out


def test_import_failure_before_main_is_located(monkeypatch, release, capsys):
    root = release.distribution.installation_root
    stderr = (b"Traceback (most recent call last):\n"
              + f'  File "{root}/runtime/stack_reconcile.py", line 31, in <module>\n'.encode()
              + b"ModuleNotFoundError: No module named 'sentinel_module'\n")
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1, b"", stderr))
    assert run_edits(release, plan=True) == 78
    out = capsys.readouterr().out
    streams = json.loads(out.split(" ", 2)[2])["child_streams"]
    assert streams["exception_type"] == "ModuleNotFoundError"
    assert streams["release_frames"] == ["runtime/stack_reconcile.py:31"]
    assert "sentinel_module" not in out


def _located(monkeypatch, release, capsys, stderr):
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1, b"", stderr))
    assert run_edits(release, plan=True) == 78
    out = capsys.readouterr().out
    return out, json.loads(out.split(" ", 2)[2])["child_streams"]


def test_a_message_line_never_passes_as_the_exception_type(monkeypatch, release, capsys):
    """Review I-008 v2: an unindented line of the message was reported as a type."""
    root = release.distribution.installation_root
    stderr = (b"Traceback (most recent call last):\n"
              + f'  File "{root}/runtime/stack_reconcile.py", line 1444, in main\n'.encode()
              + b"ValueError: a message on\nPrivateTokenAbc123\n")
    out, streams = _located(monkeypatch, release, capsys, stderr)
    assert streams["exception_type"] == "ValueError"
    assert "PrivateTokenAbc123" not in out


def test_only_authenticated_release_files_are_frames(monkeypatch, release, capsys):
    """Review I-008 v2: a forged File line under the root was reported."""
    root = release.distribution.installation_root
    stderr = (b"Traceback (most recent call last):\n"
              + f'  File "{root}/runtime/stack_reconcile.py", line 1444, in main\n'.encode()
              + b"ValueError: user text follows:\n"
              + f'  File "{root}/runtime/PrivateFilenameAbc123.py", line 25, in x\n'.encode())
    out, streams = _located(monkeypatch, release, capsys, stderr)
    assert streams["release_frames"] == ["runtime/stack_reconcile.py:1444"]
    assert "PrivateFilenameAbc123" not in out


@pytest.mark.parametrize("type_line", [b"a" * 20000 + b".ValueError: x",
                                       b"project.module.CustomError: x",
                                       b"E" * 20000 + b": x"])
def test_unknown_or_unbounded_types_report_nothing(monkeypatch, release, capsys, type_line):
    stderr = b"Traceback (most recent call last):\n" + type_line + b"\n"
    out, streams = _located(monkeypatch, release, capsys, stderr)
    assert streams["exception_type"] is None
    assert len(out) < 4096


def test_without_an_inventory_no_frame_is_reported():
    stderr = (b"Traceback (most recent call last):\n"
              b'  File "/r/runtime/stack_reconcile.py", line 1, in m\nValueError: x\n')
    streams = cycle._child_streams(b"", stderr, "/r")
    assert (streams["release_frames"], streams["exception_type"]) == ([], "ValueError")


@pytest.mark.parametrize("stderr", [b"", b"no traceback here: secret", b"\xff" * 64,
                                    b"Traceback (most recent call last):\nlowercase: secret\n"])
def test_streams_without_a_located_failure_report_nothing_more(monkeypatch, release, capsys, stderr):
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 1, b"not json", stderr))
    assert run_edits(release, plan=True) == 78
    out = capsys.readouterr().out
    streams = json.loads(out.split(" ", 2)[2])["child_streams"]
    assert (streams["exception_type"], streams["release_frames"]) == (None, [])
    assert "secret" not in out and "lowercase" not in out


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
        raise subprocess.TimeoutExpired("test", 120 if plan else 600,
                                        output=b"partial", stderr=b"secret-tail")

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert run_edits(release, plan=plan) == 78
    output = capsys.readouterr().out
    assert output.startswith("RELEASE_EDITS_PLAN_REFUSED " if plan else "RELEASE_EDITS_REFUSED ")
    summary = json.loads(output.split(" ", 2)[2])
    assert summary["error_code"] == "release_edits_timeout"
    assert summary["timeout_s"] == (120 if plan else 600)
    assert summary["effects"] == ("no_admissions" if plan else "unknown_check_pending_activation")
    assert summary["child_streams"]["stdout_bytes"] == len(b"partial")
    assert summary["unit_stop"] == "unconfirmed"
    assert "secret-tail" not in output


@pytest.mark.parametrize("stopped,expected", [(True, "confirmed"), (False, "unconfirmed")])
def test_timeout_summary_reports_whether_the_unit_stopped(monkeypatch, release, capsys,
                                                         stopped, expected):
    def child(*args, **kwargs):
        expired = subprocess.TimeoutExpired("test", 600)
        expired.release_unit_stopped = stopped
        raise expired

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert run_edits(release) == 78
    summary = json.loads(capsys.readouterr().out.split(" ", 2)[2])
    assert summary["unit_stop"] == expected and summary["error_code"] == "release_edits_timeout"
    assert "release_unit" not in summary


def test_unconfirmed_timeout_names_the_unit_to_check(monkeypatch, release, capsys):
    def child(*args, **kwargs):
        expired = subprocess.TimeoutExpired("test", 600)
        expired.release_unit = "metnos-release-edits-sign-0123456789abcdef.service"
        expired.release_unit_stopped = False
        raise expired

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert run_edits(release) == 78
    summary = json.loads(capsys.readouterr().out.split(" ", 2)[2])
    assert summary["unit_stop"] == "unconfirmed"
    assert summary["release_unit"] == "metnos-release-edits-sign-0123456789abcdef.service"


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


def test_audit_never_launches_the_successor_cli(crossing, capsys):
    """Review C15: N+1's CLI cannot verify the still-selected N before the crossing."""
    assert crossing.run("audit") == 0
    assert crossing.events == []
    assert "RELEASE_EDITS_PLAN_DEFERRED until_cutover" in capsys.readouterr().out


def test_plan_then_admission_strictly_after_successful_cutover(crossing, capsys):
    assert crossing.run("complete") == 0
    assert crossing.events == ["cutover", "plan", "admit"]
    assert "CUTOVER_OK" in capsys.readouterr().out


@pytest.mark.parametrize("plan_fails", [False, True])
def test_failed_cutover_never_launches_plan_or_admission(crossing, capsys, plan_fails):
    crossing.control.update(cutover_fails=True, plan_fails=plan_fails)
    with pytest.raises(RuntimeError, match="test cutover failure"):
        crossing.run("complete")
    assert crossing.events == ["cutover"]
    assert "CUTOVER_OK" not in capsys.readouterr().out


def test_refused_plan_ships_the_release_but_admits_nothing(crossing, capsys):
    crossing.control["plan_fails"] = True
    assert crossing.run("complete") == 78
    assert crossing.events == ["cutover", "plan"]
    out = capsys.readouterr().out
    assert out.index("CUTOVER_OK") < out.index("RELEASE_EDITS_PLAN_REFUSED")


def test_structural_audit_refusal_is_not_a_preview_only_result(crossing):
    # A structural identity failure must stop before the optional preview.
    crossing.release.descriptor.python_executable = "/not-the-os-interpreter"
    with pytest.raises(RuntimeError, match="administrative interpreter"):
        crossing.run("audit")
    assert crossing.events == []


@pytest.mark.parametrize("mode", ["audit", "complete"])
def test_main_accepts_only_the_two_closed_crossing_modes(monkeypatch, mode):
    calls = []
    monkeypatch.setattr(cycle.sys, "argv", ["cycle", "_cross", "release", "source", "evidence", mode])
    monkeypatch.setattr(cycle, "cross", lambda *args: calls.append(args) or 17)
    assert cycle.main() == 17
    assert calls == [("release", "source", "evidence", mode)]


@pytest.mark.parametrize("mode", ["", "complete-unchecked", "skip-auth", "audit --sign",
                                  "complete-no-admission"])
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
    monkeypatch.setattr(cycle, "withdraw_unclaimed_release", lambda source_id: None)
    monkeypatch.setattr(cycle, "retire_orphan_journals", lambda withdrawn: [])
    monkeypatch.setattr(builder, "build_and_install_received_source_v1", lambda s: release.distribution)
    monkeypatch.setattr(cycle, "publish_evidence", lambda *args: None)
    calls = []
    control = {"audit": 0, "complete": 0}

    def child(command):
        calls.append(command)
        return control[command[-1]]

    monkeypatch.setattr(cycle, "run_child", child)
    return NS(calls=calls, control=control)


@pytest.mark.parametrize("cross", [False, True])
@pytest.mark.parametrize("audit_result", [0, 79, 78, 1, 124, -9])
def test_parent_stops_on_every_audit_refusal(applying, cross, audit_result):
    applying.control["audit"] = audit_result
    result = cycle.apply_cycle(cross)
    modes = [command[-1] for command in applying.calls]
    if cross and audit_result == 0:
        assert result == 0 and modes == ["audit", "complete"]
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


def test_prepare_derives_builtin_contracts_before_pins_and_export(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cycle.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cycle, "run_in_worktree", lambda *command: calls.append(command))
    monkeypatch.setattr(cycle, "reviewed_roots", lambda: ((1, "private"), (1, "public")))
    monkeypatch.setattr(cycle, "stage", lambda source, destination: None)
    monkeypatch.setattr(cycle, "census", lambda tree: (0, "census"))
    monkeypatch.setattr(cycle, "CYCLE_DIR", tmp_path / "cycle")
    monkeypatch.setattr(cycle, "STAGING", tmp_path / "cycle" / "export")
    monkeypatch.setattr(cycle, "HANDOFF", tmp_path / "cycle" / "handoff.json")

    assert cycle.prepare() == 0

    steps = [next(part for part in command[1:] if part.endswith((".py", ".sh")))
             for command in calls]
    assert steps[0] == "scripts/generate_builtin_executor_contracts.py"
    assert "--sign" not in calls[0]
    assert steps.index("internal/tools/rm0008_repin_source_roots.py") > 0
    assert steps.index("scripts/export-public.sh") > 0


@pytest.mark.parametrize("activated_builtin", [False, True])
def test_activation_is_proven_per_origin_for_same_named_contracts(
        monkeypatch, release, capsys, activated_builtin):
    core = {"name": "admin", "outcome": "store_verified", "request_id": "r-1",
            "candidate_id": "c-1", "previous_generation_id": "g-1",
            "current_generation_id": "g-2"}
    builtin = {**core, "origin": "builtin", "request_id": "r-2", "candidate_id": "c-2"}
    payload = {"ok": True, "signed": [core, builtin], "restarted": True,
               "readiness": {"ok": True, "ready": True},
               "activated": [core, builtin] if activated_builtin else [core]}
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, json.dumps(payload).encode()))
    result = run_edits(release)
    admitted = "RELEASE_EDITS_ADMITTED" in capsys.readouterr().out
    assert (result == 0) is activated_builtin
    assert admitted is activated_builtin
