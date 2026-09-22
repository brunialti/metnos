"""Release/deploy orchestration: isolated files and processes, no live cutover."""
from __future__ import annotations

import importlib.util
import contextlib
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "internal/tools/rm0008_release_cycle.py"
spec = importlib.util.spec_from_file_location("rm0008_release_cycle_tested", SOURCE)
cycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cycle)
ORIGINAL_RELEASE_PREVIEW = cycle._run_release_preview


def _retirement_catalog_pair():
    from dataclasses import replace
    fixture_path = SOURCE.parents[2] / "tests/portable/test_executor_birth_legacy_retirement.py"
    fixture_spec = importlib.util.spec_from_file_location("release_retirement_fixtures", fixture_path)
    fixtures = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixtures)
    current = fixtures._product_catalog()
    # This is the exact 63 -> candidate 64 delta found in the signed catalogs:
    # all 39 previous bindings are unchanged, one repository entry was added.
    previous = replace(current, legacy_bindings=tuple(
        binding for binding in current.legacy_bindings
        if binding.legacy_id != "legacy-install-operator-authority"))
    assert len(current.legacy_bindings) == len(previous.legacy_bindings) + 1
    return NS(catalog=previous), NS(catalog=current)


def test_unchanged_retirement_plan_can_pass_the_early_release_check():
    from executor_birth_legacy_retirement import plan_catalog_retirement_v1, require_successor_retirement_v1
    previous, current = _retirement_catalog_pair()
    for left, right in ((previous, previous), (current, current), (previous, current)):
        require_successor_retirement_v1(
            plan_catalog_retirement_v1(left.catalog).steps,
            plan_catalog_retirement_v1(right.catalog).steps,
        )


def test_real_retirement_plan_removal_is_refused_before_live_reads(monkeypatch):
    from install import birth_authority_provisioner as provisioner
    previous, current = _retirement_catalog_pair()
    monkeypatch.setattr(provisioner, "_resolve_legacy_service_identity_v2", lambda _: object())
    monkeypatch.setattr(provisioner, "_verify_completed_retirement_v2",
                        lambda *_: pytest.fail("read after unsupported change"))
    with pytest.raises(RuntimeError, match="birth_transition_legacy_plan_changed"):
        cycle._verify_retirement_transition(current, previous, "legacy-test", "previous-build")


@pytest.mark.parametrize("refused", (False, True))
def test_early_retirement_uses_the_authoritative_successor_verifier(monkeypatch, refused):
    from install import birth_authority_provisioner as provisioner

    previous, current = _retirement_catalog_pair()
    identity = object()
    monkeypatch.setattr(provisioner, "_resolve_legacy_service_identity_v2", lambda name: identity if name == "legacy-test" else None)
    calls = []
    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        if refused:
            raise provisioner.BirthProvisioningError("birth_transition_retirement_checkpoint_invalid")
    monkeypatch.setattr(provisioner, "_observe_successor_retirement_v2", observe)
    if refused:
        with pytest.raises(RuntimeError, match="retirement_checkpoint_invalid"):
            cycle._verify_retirement_transition(previous, current, "legacy-test", "previous-build")
    else:
        cycle._verify_retirement_transition(previous, current, "legacy-test", "previous-build")
    assert calls == [((current, previous, identity), {"expected_previous_build_id": "previous-build"})]


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
                      previous_closed_build_id="sha256:" + "a" * 64,
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


@pytest.mark.parametrize("plan", (False, True))
def test_named_child_keeps_installed_runtime_and_passes_authoring_as_data(monkeypatch, release, tmp_path, plan):
    calls = []
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    source = tmp_path / "authoring"
    cycle._release_edits_child(
        release.distribution, release.descriptor, release.catalog,
        plan_only=plan, executor_name="alpha", source_root=source)
    argv, kwargs = calls[0][0][0], calls[0][1]
    assert argv[-4:] == ["--executor", "alpha", "--source-root", str(source)]
    assert release.entry.target_executable in argv
    assert f"WorkingDirectory={release.entry.target_working_directory}" in argv
    assert f"METNOS_INSTALL_ROOT={release.distribution.installation_root}" in argv
    assert "--plan" in argv if plan else "--sign" in argv
    assert not any("PYTHONPATH" in item for item in argv)
    assert kwargs["stdin"] is subprocess.DEVNULL


@pytest.mark.parametrize("options", (
    {"source_root": Path("/authoring")},
    {"executor_name": "alpha"},
    {"executor_name": "alpha", "source_root": Path("/authoring"), "preview": True},
))
def test_named_child_refuses_incomplete_or_preview_scope(monkeypatch, release, options):
    monkeypatch.setattr(cycle.subprocess, "run", lambda *a, **kw: pytest.fail("launched"))
    with pytest.raises(RuntimeError):
        cycle._release_edits_child(
            release.distribution, release.descriptor, release.catalog, plan_only=True, **options)


@pytest.fixture
def primary_checkout(tmp_path, monkeypatch):
    root = tmp_path / "primary"
    root.mkdir()
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], env=environment, stderr=subprocess.DEVNULL)

    git("init")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Initial test checkout")
    monkeypatch.setattr(cycle, "WORKTREE", root)
    return root, git


def test_authoring_revision_requires_clean_primary_checkout(primary_checkout, tmp_path):
    root, git = primary_checkout
    assert cycle._authoring_revision(root) == git("rev-parse", "HEAD").decode().strip()
    linked = tmp_path / "linked"
    git("worktree", "add", "--detach", str(linked))
    with pytest.raises(RuntimeError, match="primary checkout"):
        cycle._authoring_revision(linked)
    (root / "pending.py").write_text("pass\n")
    with pytest.raises(RuntimeError, match="clean checkout"):
        cycle._authoring_revision(root)


def test_authoring_observation_never_refreshes_git_index(primary_checkout, monkeypatch):
    root, _git = primary_checkout
    original_run = subprocess.run
    calls = []

    def read_only(argv, **kwargs):
        assert argv[:2] == ["/usr/bin/git", "--no-optional-locks"]
        calls.append(argv)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(cycle.subprocess, "run", read_only)
    assert cycle._authoring_revision(root)
    assert len(calls) == 3


@pytest.mark.parametrize("plan", (False, True))
def test_publish_bridge_plans_and_launches_only_attested_release(monkeypatch, release, primary_checkout, plan):
    root, git = primary_checkout
    release.live.descriptor = release.descriptor
    release.live.catalog = release.catalog.catalog
    monkeypatch.setattr(cycle.sys, "path", list(sys.path))
    calls = []

    def child(distribution, descriptor, catalog, **options):
        assert distribution.installation_root == release.distribution.installation_root
        assert descriptor is release.descriptor and catalog.catalog is release.catalog.catalog
        assert options["source_root"] == root and options["executor_name"] == "alpha"
        assert str(root / "runtime") not in sys.path
        assert sys.path[0] == release.distribution.installation_root + "/runtime"
        calls.append(options["plan_only"])
        payload = ({"ok": True, "plan": [{"name": "alpha", "outcome": "changed"}]}
                   if options["plan_only"] else success())
        return NS(returncode=0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(cycle, "_release_edits_child", child)
    assert cycle.publish_executor("alpha", plan_only=plan) == 0
    assert calls == ([True] if plan else [True, False])


@pytest.mark.parametrize("cause", ("refused_plan", "wrong_scope", "checkout_changed", "release_changed"))
def test_publish_bridge_stops_before_admission_on_changed_inputs(monkeypatch, release, primary_checkout, cause):
    root, git = primary_checkout
    release.live.descriptor = release.descriptor
    release.live.catalog = release.catalog.catalog
    monkeypatch.setattr(cycle.sys, "path", list(sys.path))
    calls = []

    def child(*args, **options):
        assert options["plan_only"] is True
        calls.append(True)
        if cause == "checkout_changed":
            (root / "uncommitted").write_text("pending")
        payload = {"ok": cause != "refused_plan", "plan": [
            {"name": "other" if cause == "wrong_scope" else "alpha", "outcome": "changed"}]}
        return NS(returncode=0, stdout=json.dumps(payload).encode(), stderr=b"")

    # Resolve the helper again on each call without replacing the proof in place.
    original = cycle.load_live_helper()
    monkeypatch.setattr(cycle, "load_live_helper", lambda: original)
    if cause == "release_changed":
        changed = NS(**vars(release.live))
        changed.transaction = NS(head_id="next-head")
        proofs = iter((release.live, release.live, changed))
        original._attest_service_startup_v1 = lambda entry: (next(proofs), release.entry)
    monkeypatch.setattr(cycle, "_release_edits_child", child)
    if cause == "refused_plan":
        assert cycle.publish_executor("alpha") == 78
    else:
        with pytest.raises(RuntimeError):
            cycle.publish_executor("alpha")
    assert calls == [True]


@pytest.fixture
def retention_tree(monkeypatch, tmp_path):
    root = tmp_path / "ownership"
    releases = root / "releases-v1"
    for sequence in range(1, 6):
        deployment = releases / f"{sequence:020d}" / "deployment"
        deployment.mkdir(parents=True)
        (deployment / "executor-birth-deployment-v1.json").write_text(
            json.dumps({"release_sequence": sequence}),
        )
    for path in releases.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    history = root / "chain-v1"
    history.mkdir()
    (history / "proof").write_bytes(b"preserve signed history")
    monkeypatch.setattr(cycle, "ROOT", root)
    monkeypatch.setattr(cycle, "OWNERS", {(os.getuid(), os.getgid())})
    monkeypatch.setattr(cycle.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cycle, "acquire_locks", lambda stack: None)
    monkeypatch.setattr(cycle, "open_parent", lambda path: os.open(path, os.O_RDONLY | os.O_DIRECTORY))
    monkeypatch.setattr(cycle, "startup_fingerprint", lambda: ("attested", 4, "head"))
    monkeypatch.setattr(cycle, "referenced_release_sequences", lambda path: frozenset({1}))
    return releases


@pytest.mark.parametrize("apply", (False, True))
def test_release_retention_preserves_current_recovery_references_and_future(retention_tree, apply):
    result = cycle.prune_releases(apply=apply)
    assert result["candidates"] == [f"{2:020d}"]
    assert result["removed"] == ([f"{2:020d}"] if apply else [])
    for sequence in (1, 3, 4, 5):
        assert (retention_tree / f"{sequence:020d}").is_dir()
    assert (retention_tree / f"{2:020d}").exists() is not apply
    assert (retention_tree.parent / "chain-v1/proof").read_bytes() == b"preserve signed history"


@pytest.mark.parametrize("keep,expected", ((1, (1, 2, 3)), (2, (1, 2)), (5, ())))
def test_release_retention_limit_is_explicit(retention_tree, keep, expected):
    result = cycle.release_retention_candidates(retention_tree, 4, keep=keep)
    assert tuple(int(path.name) for path in result) == expected


@pytest.mark.parametrize("invalid", (0, -1, True, 1.5))
def test_release_retention_refuses_invalid_limits(retention_tree, invalid):
    with pytest.raises(RuntimeError, match="keep at least"):
        cycle.release_retention_candidates(retention_tree, 4, keep=invalid)


@pytest.mark.parametrize("mutation", ("descriptor", "symlink", "mode", "selection", "unverified"))
def test_release_retention_refuses_unsafe_plan_before_deletion(retention_tree, monkeypatch, mutation):
    target = retention_tree / f"{2:020d}"
    if mutation == "descriptor":
        (target / "deployment/executor-birth-deployment-v1.json").write_text('{"release_sequence":99}')
    elif mutation == "symlink":
        (target / "foreign").symlink_to(retention_tree.parent / "chain-v1")
    elif mutation == "mode":
        target.chmod(0o777)
    elif mutation == "selection":
        states = iter((("attested", 4, "head"), ("attested", 5, "new-head")))
        monkeypatch.setattr(cycle, "startup_fingerprint", lambda: next(states))
    else:
        monkeypatch.setattr(cycle, "startup_fingerprint", lambda: ("refused", "invalid"))
    with pytest.raises(RuntimeError):
        cycle.prune_releases(apply=True)
    assert target.is_dir()
    assert (retention_tree.parent / "chain-v1/proof").read_bytes() == b"preserve signed history"


def test_retention_failure_does_not_reverse_successful_publication(applying, monkeypatch, capsys):
    def refuse(**_kw):
        raise RuntimeError("retention deferred")

    monkeypatch.setattr(cycle, "prune_releases", refuse)
    assert cycle.apply_cycle(True) == 0
    assert "RELEASE_RETENTION_DEFERRED" in capsys.readouterr().out


def test_release_references_cover_processes_mounts_and_service_recipes(monkeypatch, tmp_path):
    releases = tmp_path / "releases"
    proc, units = tmp_path / "proc", tmp_path / "units"
    task = proc / "101"
    (task / "fd").mkdir(parents=True)
    units.mkdir()

    def target(sequence):
        return releases / f"{sequence:020d}"

    (task / "cwd").symlink_to(target(1) / "runtime")
    (task / "exe").symlink_to(target(2) / "managed/bin/python")
    (task / "fd/3").symlink_to(target(3) / "executor.py")
    (task / "maps").write_text(f"000-fff r-xp 0 0:0 0 {target(4)}/library.so\n")
    (task / "cmdline").write_bytes(os.fsencode(target(5)) + b"\0--flag\0")
    (proc / "102").mkdir()  # An exited process is not a fatal read error.
    (proc / "self").mkdir()
    (proc / "self/mountinfo").write_text(f"1 0 0:1 / {target(6)}/mount rw - tmpfs tmpfs rw\n")
    (units / "example.service").write_text(f"WorkingDirectory={target(7)}\n")
    (units / "linked.service").symlink_to(target(8) / "example.service")
    (units / "not-a-reference.service").write_text(f"Description={target(9)}-unrelated\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ignored.service").write_text(f"WorkingDirectory={target(10)}\n")
    (units / "external-directory").symlink_to(outside, target_is_directory=True)
    paths = {"/proc": proc, "/proc/self/mountinfo": proc / "self/mountinfo",
             "/etc/systemd/system": units}
    monkeypatch.setattr(cycle, "Path", lambda path: paths[path])
    assert cycle.referenced_release_sequences(releases) == frozenset(range(1, 9))


def test_release_reference_read_failure_defers_retention(retention_tree, monkeypatch):
    def unavailable(_root):
        raise PermissionError("process reference unavailable")

    monkeypatch.setattr(cycle, "referenced_release_sequences", unavailable)
    with pytest.raises(PermissionError):
        cycle.prune_releases(apply=True)
    assert len(tuple(retention_tree.iterdir())) == 5


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
        f"WorkingDirectory={release.entry.target_working_directory}"] + (
            ["ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=yes",
             "ReadWritePaths=/tmp /var/tmp"] if plan else [])
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
    monkeypatch.setattr(cycle, "_verify_autonomous_service_recipe", lambda *args: None)
    monkeypatch.setattr(cycle, "_verify_live_administrative_artifact", lambda *args: None)
    monkeypatch.setattr(cycle, "_verify_retirement_transition", lambda *args: None)
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
    monkeypatch.setattr(cycle, "_run_release_preview", lambda *a: events.append("preview"))
    monkeypatch.setattr(cycle, "_delegate_cutover_checks", lambda descriptor: None)
    return NS(events=events, control=control, release=release,
              run=lambda mode: cycle.cross(release.distribution.installation_root,
                                           "source-test", str(evidence), mode))


def test_audit_runs_only_the_read_only_transition_preview(crossing, capsys):
    """C15: preview uses N's explicit reader, not N+1's live bootstrap."""
    assert crossing.run("audit") == 0
    assert crossing.events == ["preview"]
    assert "CUTOVER_OK" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ("audit", "complete"))
@pytest.mark.parametrize("check", ("_verify_autonomous_service_recipe", "_verify_live_administrative_artifact"))
def test_recipe_disagreement_is_refused_before_effects(crossing, monkeypatch, mode, check):
    def refuse(*args):
        raise RuntimeError("service source recipe")
    monkeypatch.setattr(cycle, check, refuse)
    with pytest.raises(RuntimeError, match="service source recipe"):
        crossing.run(mode)
    assert crossing.events == []


@pytest.mark.parametrize("mode", ("audit", "complete"))
def test_retirement_disagreement_is_refused_before_effects(crossing, monkeypatch, mode):
    def refuse(*args):
        raise RuntimeError("birth_transition_legacy_plan_changed")
    monkeypatch.setattr(cycle, "_verify_retirement_transition", refuse)
    with pytest.raises(RuntimeError, match="birth_transition_legacy_plan_changed"):
        crossing.run(mode)
    assert crossing.events == []


@pytest.mark.parametrize("stale_pin", (False, True))
def test_early_recipe_check_uses_real_canonical_and_independent_codecs(monkeypatch, stale_pin):
    fixture_path = SOURCE.parents[2] / "tests/portable/test_executor_birth_admin_preflight_materials.py"
    fixture_spec = importlib.util.spec_from_file_location("release_recipe_fixtures", fixture_path)
    fixtures = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixtures)
    catalog = NS(catalog=NS(encoded=fixtures._catalog_bytes()))
    descriptor = fixtures._deployment_record()
    if stale_pin:
        monkeypatch.setattr(fixtures.preflight, "_EXPECTED_SERVICE_SOURCE_IDENTITY_V1",
                            "sha256:" + "0" * 64)
        with pytest.raises(fixtures.preflight.PreflightError, match="service source recipe"):
            cycle._verify_autonomous_service_recipe(descriptor, catalog)
    else:
        cycle._verify_autonomous_service_recipe(descriptor, catalog)


def test_preview_child_cannot_select_the_admission_flag(monkeypatch, release, tmp_path):
    calls = _capture_runs(monkeypatch, [])
    cycle._release_edits_child(release.distribution, release.descriptor,
                              release.catalog, plan_only=True, preview=True)
    _, properties, service = _unit_parts(calls[0][0])
    assert "ProtectSystem=strict" in properties
    assert service[-2:] == ["--plan", "--preview"]
    assert json.loads(calls[0][1]["input"]) == {
        "distribution": "distribution", "signature": b"signature".hex()}
    assert "stdin" not in calls[0][1]
    with pytest.raises(RuntimeError, match="preview cannot admit"):
        cycle._release_edits_child(release.distribution, release.descriptor,
                                  release.catalog, plan_only=False, preview=True)
    assert len(calls) == 1


@pytest.mark.parametrize("valid", [False, True])
def test_refused_preview_is_reported_without_blocking_the_audit(
        monkeypatch, release, crossing, capsys, tmp_path, valid):
    report = {"ok": False, "plan": [{"name": "alpha", "outcome": "error"}],
              "admission_attempted": False}
    monkeypatch.setattr(cycle, "_release_edits_child", lambda *a, **kw:
                        subprocess.CompletedProcess([], 1,
                            json.dumps(report).encode() if valid else b"private stdout",
                            b"private stderr --password=secret"))
    # The real preview returns normally for an unavailable report as well.
    preview = ORIGINAL_RELEASE_PREVIEW
    monkeypatch.setattr(cycle, "_run_release_preview", preview)
    assert crossing.run("audit") == 0
    assert crossing.events == []
    output = capsys.readouterr().out
    line = next(item for item in output.splitlines() if item.startswith("RELEASE_EDITS_PREVIEW "))
    summary = json.loads(line.split(" ", 1)[1])
    assert summary["status"] == ("evaluated" if valid else "not_evaluated")
    assert summary["admission_attempted"] is False and summary["cutover_completed"] is False
    assert "private stdout" not in output and "secret" not in output


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


@pytest.mark.parametrize("refused", (False, True))
def test_release_build_uses_public_modes_and_restores_private_umask(applying, monkeypatch, refused):
    from install import executor_birth_distribution_release as builder

    build = builder.build_and_install_received_source_v1
    masks = []
    original_umask = os.umask(0o077)

    def observed(source):
        current = os.umask(0o022)
        masks.append(current)
        if refused:
            raise RuntimeError("test build refusal")
        return build(source)

    monkeypatch.setattr(builder, "build_and_install_received_source_v1", observed)
    try:
        if refused:
            with pytest.raises(RuntimeError, match="test build refusal"):
                cycle.apply_cycle(False)
        else:
            assert cycle.apply_cycle(False) == 0
        assert os.umask(0o077) == 0o077
        assert masks == [0o022]
    finally:
        os.umask(original_umask)


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
    monkeypatch.setattr(cycle, "withdraw_superseded_prepared", lambda source: None)
    monkeypatch.setattr(cycle, "withdraw_superseded_claim", lambda source: None)
    monkeypatch.setattr(cycle, "withdraw_unclaimed_release", lambda source_id: None)
    monkeypatch.setattr(cycle, "retire_orphan_journals", lambda withdrawn: [])
    monkeypatch.setattr(builder, "build_and_install_received_source_v1", lambda s: release.distribution)
    monkeypatch.setattr(cycle, "publish_evidence", lambda *args: None)
    calls = []
    prunes = []
    monkeypatch.setattr(cycle, "prune_releases", lambda **kw: prunes.append(kw))
    control = {"audit": 0, "complete": 0}

    def child(command):
        calls.append(command)
        return control[command[-1]]

    monkeypatch.setattr(cycle, "run_child", child)
    monkeypatch.setattr(cycle, "_run_cutover_child", child)
    return NS(calls=calls, control=control, prunes=prunes)


def test_cutover_checks_run_before_stopping_services(crossing, monkeypatch):
    def unavailable(_descriptor):
        raise RuntimeError("native runner unavailable")
    monkeypatch.setattr(cycle, "_delegate_cutover_checks", unavailable)
    with pytest.raises(RuntimeError, match="native runner unavailable"):
        crossing.run("complete")
    assert crossing.events == []


@pytest.fixture
def unclaimed_withdrawal(monkeypatch, tmp_path):
    from install import executor_birth_distribution_release as builder
    root = tmp_path / "root"
    release = root / "releases-v1" / f"{64:020d}"
    (release / "deployment").mkdir(parents=True)
    (release / "deployment/executor-birth-deployment-v1.json").write_text(
        json.dumps({"release_sequence": 64}))
    (release / "payload.py").write_bytes(b"verified candidate bytes\n")
    preserved = tmp_path / "selected-history"
    preserved.write_bytes(b"selected head and signed history stay unchanged")
    for item in tmp_path.rglob("*"):
        item.chmod(0o755 if item.is_dir() else 0o644)
    for name, value in {
            "ROOT": root, "COORD": root / "coordinator-v1",
            "WITHDRAWN_ROOT": tmp_path / "archive",
            "OWNERS": {(os.getuid(), os.getgid())}}.items():
        monkeypatch.setattr(cycle, name, value)
    # Only the outer temporary parent differs from the root-owned deployment;
    # content, metadata checks, census and no-replace renames stay real.
    monkeypatch.setattr(cycle, "open_parent", lambda path, owners=None:
                        os.open(path, cycle.READ | os.O_DIRECTORY))
    monkeypatch.setattr(cycle, "preserved_paths", lambda: (preserved,))
    monkeypatch.setattr(cycle, "startup_fingerprint", lambda: ("attested", 63, "head"))
    monkeypatch.setattr(builder, "_resolve_ownership_coordinator_at_v2",
                        lambda *args, **kw: NS(pending_claims=(), claims=()))
    monkeypatch.setattr(builder, "_next_release_edge_v1", lambda *args: NS(sequence=64))
    return NS(release=release, preserved=preserved)


def test_rebuilt_identical_unclaimed_release_never_overwrites_an_archive(unclaimed_withdrawal):
    fixture = unclaimed_withdrawal
    preserved = cycle.snapshot(fixture.preserved)
    expected = cycle.census(fixture.release)
    first = Path(cycle.withdraw_unclaimed_release("test-source"))
    before = cycle.snapshot(first)
    for number in (1, 2):
        shutil.copytree(first / "unselected-release", fixture.release)
        other = Path(cycle.withdraw_unclaimed_release("test-source"))
        assert other.name == first.name + f".repeated-{number:02d}"
        assert cycle.census(other / "unselected-release") == expected
        assert cycle.snapshot(first) == before
        assert cycle.snapshot(fixture.preserved) == preserved
        assert not fixture.release.exists()
        assert cycle.withdraw_unclaimed_release("test-source") is None


@pytest.mark.parametrize("bad", ("content", "extra", "permissions", "symlink", "selected", "unattested"))
def test_repeated_archive_refuses_ambiguity_without_moving_current(unclaimed_withdrawal, monkeypatch, bad):
    fixture = unclaimed_withdrawal
    first = Path(cycle.withdraw_unclaimed_release("test-source"))
    parked = first / "unselected-release"
    shutil.copytree(parked, fixture.release)
    before = cycle.snapshot(fixture.release)
    if bad == "content":
        (parked / "payload.py").write_bytes(b"different bytes\n")
    elif bad == "extra":
        (first / "unexpected").write_bytes(b"not an archive member")
    elif bad == "permissions":
        first.chmod(0o755)
    elif bad == "symlink":
        saved = first.with_name(first.name + ".original")
        first.rename(saved)
        first.symlink_to(saved, target_is_directory=True)
    else:
        monkeypatch.setattr(cycle, "startup_fingerprint", lambda:
                            ("attested", 64, "new-head") if bad == "selected"
                            else ("refused", "test", "unobservable"))
    with pytest.raises(RuntimeError):
        cycle.withdraw_unclaimed_release("test-source")
    assert cycle.snapshot(fixture.release) == before


@pytest.mark.parametrize("during_build", (False, True))
def test_helper_baseline_follows_verified_recovery_not_the_builder(applying, monkeypatch, release, during_build):
    from install import executor_birth_distribution_release as builder
    def recover(_source):
        cycle.LIVE_HELPER.write_bytes(b"verified selected administrative helper")
    def build(_source):
        if during_build:
            cycle.LIVE_HELPER.write_bytes(b"unexpected mutation during build")
        return release.distribution
    monkeypatch.setattr(cycle, "withdraw_superseded_prepared", recover)
    monkeypatch.setattr(builder, "build_and_install_received_source_v1", build)
    if during_build:
        with pytest.raises(RuntimeError, match="live helper changed during the build"):
            cycle.apply_cycle(False)
        assert applying.calls == []
    else:
        assert cycle.apply_cycle(False) == 0
        assert [call[-1] for call in applying.calls] == ["audit"]


@pytest.fixture(params=(0, 1), ids=("prepared", "receipts_complete"))
def prepared_withdrawal(monkeypatch, tmp_path, request):
    import executor_birth_ownership_coordinator as coordinator
    from install import birth_authority_provisioner as provisioner
    fixture_path = SOURCE.parents[2] / "tests/portable/test_executor_birth_ownership_coordinator_v2.py"
    fixture_spec = importlib.util.spec_from_file_location("withdrawal_record_fixtures", fixture_path)
    fixtures = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixtures)
    root, birth, archive_root = (tmp_path / part for part in ("root", "birth", "archive"))
    coord = root / "coordinator-v1"
    values = {"schema_version": 1, "request_id": "sha256:" + "1" * 64,
              "source_id": "sha256:" + "2" * 64, "closed_build_id": "sha256:" + "3" * 64,
              "previous_head_id": "sha256:" + "4" * 64, "release_sequence": 42}
    claim = coordinator._decode_successor_claim_v1(coordinator._canonical({
        **values, "claim_id": coordinator._successor_claim_id_v1(values)}))
    records = fixtures.transaction_records(claim, end_sequence=request.param,
        previous_closed_build_id="sha256:" + "a" * 64,
        previous_cutover_id="sha256:" + "b" * 64,
        cutover_id="sha256:" + "c" * 64, head_id="sha256:" + "d" * 64)
    record = records[-1]
    nonce = record.provisioning_transaction_id
    tx = coord / "transactions-v2" / claim.request_id
    journal = birth / (cycle.JOURNAL_PREFIX + nonce)
    release = root / "releases-v1" / f"{claim.release_sequence:020d}"
    context_directory = root / "chain-v1/context-transitions-v1"
    claim_path = coord / "successor-claims-v1" / (claim.previous_head_id[7:] + ".json")
    for directory in (tx, journal, release / "deployment", claim_path.parent, context_directory):
        directory.mkdir(parents=True)
    for item in records:
        (tx / f"record-{item.sequence:03d}-v2.json").write_bytes(item.encode())
    header = provisioner.TransactionHeaderV2(nonce, "test-build", claim.request_id,
        claim.closed_build_id, "9" * 64, record.distribution_payload_hash,
        record.distribution_signature_hash, "sha256:" + "a" * 64)
    (journal / "transaction-v2.json").write_bytes(header.encode())
    (release / "deployment/executor-birth-deployment-v1.json").write_text(json.dumps({
        "release_sequence": 42, "descriptor_id": record.deployment_descriptor_id}))
    claim_path.write_bytes(claim.encode())
    preserved = tmp_path / "selected-history"
    preserved.write_bytes(b"must stay byte-identical")
    for item in tmp_path.rglob("*"):
        item.chmod(0o755 if item.is_dir() else 0o644)
    ownership = {(os.getuid(), os.getgid()), (0, 0)}
    for key, value in {"ROOT": root, "COORD": coord, "BIRTH": birth,
                       "WITHDRAWN_ROOT": archive_root, "OWNERS": ownership,
                       "BIRTH_OWNERS": ownership, "ROOT_OWNED": False}.items():
        monkeypatch.setattr(cycle, key, value)
    monkeypatch.setattr(cycle, "open_parent", lambda path, owners=None:
                        os.open(path, cycle.READ | os.O_DIRECTORY))
    monkeypatch.setattr(cycle, "preserved_paths", lambda: (preserved, tx) if tx.exists() else (preserved,))
    monkeypatch.setattr(cycle, "startup_fingerprint", lambda:
                        ("attested", 41, claim.previous_head_id))
    monkeypatch.setattr(cycle, "restore_unselected_administrative", lambda *args: None)
    def graph(*args, **kwargs):
        return NS(transactions=(NS(claim=claim, latest=record),) if tx.exists() else (),
                  pending_claims=(claim,) if not tx.exists() and claim_path.exists() else ())
    monkeypatch.setattr(coordinator, "_resolve_ownership_coordinator_at_v2", graph)
    archive = archive_root / claim.request_id[7:]
    origins = (tx, journal, release, claim_path)
    return NS(claim=claim, record=record, tx=tx, journal=journal, release=release,
              archive=archive, origins=origins, preserved=preserved, records=records,
              record_fixtures=fixtures, context_directory=context_directory)


@pytest.fixture
def context_withdrawal(prepared_withdrawal, monkeypatch):
    fixture = prepared_withdrawal
    record = fixture.record
    from executor_birth_context_transition import issue_context_transition_v1
    payload, transition = issue_context_transition_v1(
        request_id=record.request_id, closed_build_id=record.closed_build_id,
        previous_cutover_id=record.previous_cutover_id,
        previous_set_id=record.previous_set_id,
        previous_admission_context_id=record.previous_admission_context_id,
        previous_context_epoch=record.previous_context_epoch,
        set_id=record.target_set_id,
        prepared_admission_context_id=record.target_admission_context_id,
        prepared_context_epoch=record.target_context_epoch,
        context_material_sha256=record.target_context_material_sha256,
        set_json_sha256=record.target_set_json_sha256,
        current_inventory=fixture.record_fixtures.proof().inventory)
    records = fixture.record_fixtures.transaction_records(
        fixture.claim, end_sequence=record.sequence,
        previous_closed_build_id=record.previous_closed_build_id,
        previous_cutover_id=record.previous_cutover_id,
        cutover_id="sha256:" + "c" * 64, head_id="sha256:" + "d" * 64,
        context_transition_id=transition.transition_id)
    for item in records:
        (fixture.tx / f"record-{item.sequence:03d}-v2.json").write_bytes(item.encode())
    context = fixture.context_directory / (transition.transition_id[7:] + ".json")
    context.write_bytes(payload)
    context.chmod(0o644)
    fixture.context = context
    fixture.records, fixture.record = records, records[-1]
    def startup():
        if context.exists() and not fixture.tx.exists():
            return ("refused", "PreflightError", "orphan context transition")
        return ("attested", 41, fixture.claim.previous_head_id)
    monkeypatch.setattr(cycle, "startup_fingerprint", startup)
    return fixture


@pytest.mark.parametrize("crash_after", [None, 0, 1, 2, 3])
def test_prepared_archive_resumes_each_prefix_without_resetting_history(
        prepared_withdrawal, monkeypatch, crash_after):
    fixture = prepared_withdrawal
    before = cycle.snapshot(fixture.preserved)
    expected = [cycle.snapshot(path) for path in fixture.origins]
    actual_rename = cycle.rename_no_replace
    moved = []
    def move(origin, target, owners=None):
        actual_rename(origin, target, owners)
        moved.append(origin)
        if len(moved) - 1 == crash_after:
            raise RuntimeError("interrupted after rename")
    monkeypatch.setattr(cycle, "rename_no_replace", move)
    if crash_after is not None:
        with pytest.raises(RuntimeError, match="interrupted after rename"):
            cycle.withdraw_superseded_prepared("new-source")
        monkeypatch.setattr(cycle, "rename_no_replace", actual_rename)
    cycle.withdraw_superseded_prepared("new-source")
    assert all(not path.exists() for path in fixture.origins)
    targets = ("prepared-transaction", "unselected-birth-journal",
               "unselected-release", "successor-claim.json")
    assert [cycle.snapshot(fixture.archive / name) for name in targets] == expected
    assert cycle.snapshot(fixture.preserved) == before
    assert cycle.withdraw_superseded_prepared("new-source") is None


@pytest.mark.parametrize("bad", ["advanced", "same_source", "selected", "wrong_release"])
def test_prepared_withdrawal_never_retries_or_moves_selected_state(
        prepared_withdrawal, monkeypatch, bad):
    fixture = prepared_withdrawal
    source = "new-source"
    if bad == "advanced":
        extra = fixture.tx / f"record-{len(fixture.records):03d}-v2.json"
        extra.write_bytes(b"advanced")
        extra.chmod(0o644)
    elif bad == "same_source":
        source = fixture.claim.source_id
    elif bad == "selected":
        monkeypatch.setattr(cycle, "startup_fingerprint", lambda: ("attested", 42, "new-head"))
    else:
        descriptor = fixture.release / "deployment/executor-birth-deployment-v1.json"
        payload = json.loads(descriptor.read_bytes())
        payload["descriptor_id"] = "different-descriptor"
        descriptor.write_text(json.dumps(payload))
    before = [cycle.snapshot(path) for path in fixture.origins]
    if bad == "same_source":
        assert cycle.withdraw_superseded_prepared(source) is None
    else:
        with pytest.raises(RuntimeError):
            cycle.withdraw_superseded_prepared(source)
    assert [cycle.snapshot(path) for path in fixture.origins] == before
    assert not fixture.archive.exists()


@pytest.mark.parametrize("crash_after", (None, 0, 1, 2, 3, 4))
def test_context_withdrawal_preserves_startup_at_every_interrupted_prefix(
        context_withdrawal, monkeypatch, crash_after):
    fixture = context_withdrawal
    before = cycle.snapshot(fixture.context)
    test_prepared_archive_resumes_each_prefix_without_resetting_history(
        fixture, monkeypatch, crash_after)
    assert not fixture.context.exists()
    assert cycle.snapshot(fixture.archive / "unselected-context-transition.json") == before


def test_context_withdrawal_rejects_tampered_context_before_any_move(context_withdrawal):
    fixture = context_withdrawal
    fixture.context.write_bytes(b"invalid context")
    before = [cycle.snapshot(path) for path in (*fixture.origins, fixture.context)]
    with pytest.raises(RuntimeError):
        cycle.withdraw_superseded_prepared("new-source")
    assert [cycle.snapshot(path) for path in (*fixture.origins, fixture.context)] == before
    assert not fixture.archive.exists()


@pytest.mark.parametrize("crash_after", (None, "exchange", "archive"))
def test_administrative_recovery_preserves_both_trees_and_resumes(tmp_path, monkeypatch, crash_after):
    live = tmp_path / "executor-birth-v1"
    backup = tmp_path / (".executor-birth-v1." + "a" * 64 + ".previous")
    archive = tmp_path / "archive"
    for path, content in ((live, b"candidate"), (backup, b"previous")):
        path.mkdir(mode=0o755)
        (path / "preflight.py").write_bytes(content)
        (path / "preflight.py").chmod(0o755)
    archive.mkdir(mode=0o700)
    monkeypatch.setattr(cycle, "LIVE_HELPER", live / "preflight.py")
    monkeypatch.setattr(cycle, "ROOT_OWNED", False)
    monkeypatch.setattr(cycle, "OWNERS", {(os.getuid(), os.getgid())})
    monkeypatch.setattr(cycle, "open_parent", lambda path, owners=None:
                        os.open(path, cycle.READ | os.O_DIRECTORY))
    original = {name: cycle.snapshot(path) for name, path in (("old", backup), ("new", live))}
    exchange, rename = cycle.exchange_names, cycle.rename_no_replace
    observed = []
    def checkpoint(operation, implementation, source, target):
        implementation(source, target)
        assert (live / "preflight.py").read_bytes() == b"previous"
        observed.append(operation)
        if operation == crash_after:
            raise RuntimeError("expected administrative interruption")
    monkeypatch.setattr(cycle, "exchange_names", lambda a, b:
                        checkpoint("exchange", exchange, a, b))
    monkeypatch.setattr(cycle, "rename_no_replace", lambda a, b:
                        checkpoint("archive", rename, a, b))
    if crash_after:
        with pytest.raises(RuntimeError, match="expected administrative interruption"):
            cycle._restore_administrative_tree(b"previous", b"candidate", "sha256:" + "a" * 64, archive)
        monkeypatch.setattr(cycle, "exchange_names", exchange)
        monkeypatch.setattr(cycle, "rename_no_replace", rename)
    cycle._restore_administrative_tree(b"previous", b"candidate", "sha256:" + "a" * 64, archive)
    assert cycle.snapshot(live) == original["old"]
    assert cycle.snapshot(archive / "unselected-administrative") == original["new"]
    assert not backup.exists()
    cycle._restore_administrative_tree(b"previous", b"candidate", "sha256:" + "a" * 64, archive)


@pytest.mark.parametrize("bad", ("unknown_live", "wrong_backup", "extra_file"))
def test_administrative_recovery_never_replaces_unknown_artifacts(tmp_path, monkeypatch, bad):
    live = tmp_path / "executor-birth-v1"
    backup = tmp_path / (".executor-birth-v1." + "a" * 64 + ".previous")
    archive = tmp_path / "archive"
    for path, content in ((live, b"candidate"), (backup, b"previous")):
        path.mkdir(mode=0o755)
        (path / "preflight.py").write_bytes(content)
        (path / "preflight.py").chmod(0o755)
    archive.mkdir(mode=0o700)
    if bad == "unknown_live":
        (live / "preflight.py").write_bytes(b"unknown")
    elif bad == "wrong_backup":
        (backup / "preflight.py").write_bytes(b"wrong")
    else:
        (live / "unexpected").write_bytes(b"extra")
        (live / "unexpected").chmod(0o644)
    monkeypatch.setattr(cycle, "LIVE_HELPER", live / "preflight.py")
    monkeypatch.setattr(cycle, "ROOT_OWNED", False)
    monkeypatch.setattr(cycle, "OWNERS", {(os.getuid(), os.getgid())})
    monkeypatch.setattr(cycle, "open_parent", lambda path, owners=None:
                        os.open(path, cycle.READ | os.O_DIRECTORY))
    before = [cycle.snapshot(path) for path in (live, backup, archive)]
    with pytest.raises(RuntimeError):
        cycle._restore_administrative_tree(b"previous", b"candidate", "sha256:" + "a" * 64, archive)
    assert [cycle.snapshot(path) for path in (live, backup, archive)] == before


def test_interrupted_withdrawal_revalidates_the_complete_archived_journal(
        prepared_withdrawal, monkeypatch):
    import executor_birth_ownership_coordinator as coordinator
    fixture = prepared_withdrawal
    original_rename = cycle.rename_no_replace
    def interrupt(origin, target, owners=None):
        original_rename(origin, target, owners)
        raise RuntimeError("interrupted after first rename")
    monkeypatch.setattr(cycle, "rename_no_replace", interrupt)
    with pytest.raises(RuntimeError, match="interrupted after first rename"):
        cycle.withdraw_superseded_prepared("new-source")
    monkeypatch.setattr(cycle, "rename_no_replace", original_rename)
    archived = fixture.archive / "prepared-transaction"
    last = archived / f"record-{fixture.record.sequence:03d}-v2.json"
    payload = json.loads(last.read_bytes())
    payload["previous_record_sha256"] = "sha256:" + "f" * 64
    last.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    remaining = [cycle.snapshot(path) for path in fixture.origins[1:]]
    with pytest.raises(coordinator.OwnershipCoordinatorError):
        cycle.withdraw_superseded_prepared("new-source")
    assert [cycle.snapshot(path) for path in fixture.origins[1:]] == remaining


@pytest.mark.parametrize("state", (
    "CERTIFICATE_READY", "CERTIFICATE_PUBLISHED", "BUILD_VERIFIED",
    "HEAD_REQUIRED", "PREFLIGHT_VERIFIED",
))
def test_pre_certificate_withdrawal_never_selects_a_later_frontier(
        prepared_withdrawal, monkeypatch, state):
    import executor_birth_ownership_coordinator as coordinator
    fixture = prepared_withdrawal
    graph = NS(transactions=(NS(claim=fixture.claim, latest=NS(
        state=coordinator.OwnershipCoordinatorStateV1(state))),), pending_claims=())
    monkeypatch.setattr(coordinator, "_resolve_ownership_coordinator_at_v2", lambda *a, **k: graph)
    before = [cycle.snapshot(path) for path in fixture.origins]
    assert cycle.withdraw_superseded_prepared("new-source") is None
    assert [cycle.snapshot(path) for path in fixture.origins] == before
    assert not fixture.archive.exists()


def test_cutover_uses_bounded_root_delegate_without_inherited_environment(monkeypatch):
    calls = _capture_runs(monkeypatch, [subprocess.CompletedProcess([], 17)])
    command = ["/usr/bin/python3.12", "/controller.py", "_cross", "release",
               "source", "evidence", "complete"]
    assert cycle._run_cutover_child(command) == 17
    flags, properties, service = _unit_parts(calls[0][0])
    assert "User=0" in properties and "Group=0" in properties
    assert "Delegate=yes" in properties
    assert "DelegateSubgroup=metnos-birth-host" in properties
    assert f"RuntimeMaxSec={cycle.CUTOVER_TIMEOUT_S}" in properties
    assert service[-len(command):] == command
    assert service[:2] == ["/usr/bin/env", "-i"]
    assert calls[0][1]["env"] == cycle.CONTROLLER_ENVIRONMENT
    assert re.fullmatch(r"--unit=metnos-rm0008-cutover-[0-9a-f]{16}\.service", flags[-1])


def test_cutover_timeout_confirms_its_own_unit_stopped(monkeypatch):
    _capture_runs(monkeypatch, [subprocess.TimeoutExpired([], 1200)])
    stopped = []
    monkeypatch.setattr(cycle, "_stop_release_unit", lambda u: stopped.append(u) or True)
    with pytest.raises(subprocess.TimeoutExpired):
        cycle._run_cutover_child(["/controller", "complete"])
    assert len(stopped) == 1
    assert stopped[0].startswith(cycle.CUTOVER_UNIT_PREFIX)


@pytest.mark.parametrize("status", ["passed", "failed", "test_environment_unavailable"])
def test_cutover_probe_requires_real_phase_success(monkeypatch, status):
    import executor_birth_runner as runner
    import executor_birth_sandbox_registry_v1 as registry
    backend = NS(interpreter_path=Path("/registered/python"))
    monkeypatch.setattr(registry, "measure_sandbox_backend_v1", lambda: b"diagnostic")
    monkeypatch.setattr(registry, "decode_sandbox_registry_v1", lambda value: backend)
    calls = []

    def phase(command, *, linux_registry):
        calls.append((command, linux_registry))
        return NS(status=runner.RunnerStatus(status), error_code="sandbox_setup_unattested")

    monkeypatch.setattr(runner, "run_birth_phase", phase)
    if status == "passed":
        cycle._probe_cutover_runner()
    else:
        with pytest.raises(RuntimeError, match="sandbox_setup_unattested"):
            cycle._probe_cutover_runner()
    assert calls == [(("/registered/python", "-I", "-c", "pass"), backend)]


@pytest.mark.parametrize("failure", [None, "wrong_unit", "not_delegated", "wrong_owner"])
def test_cutover_delegates_only_its_owned_systemd_boundary(release, monkeypatch, tmp_path, failure):
    import executor_birth_runner as runner
    from install import birth_authority_provisioner as provisioner
    unit = cycle.CUTOVER_UNIT_PREFIX + "a" * 16 + ".service"
    current = Path("/system.slice") / unit / "metnos-birth-host"
    delegate = tmp_path / "system.slice" / unit
    subgroup = delegate / current.name
    subgroup.mkdir(parents=True)
    for directory in (delegate, subgroup):
        for name in ("cgroup.procs", "cgroup.threads", "cgroup.subtree_control"):
            (directory / name).touch()
    monkeypatch.setattr(runner, "_CGROUP_V2_MOUNT", tmp_path)
    monkeypatch.setattr(runner, "_current_unified_cgroup", lambda: (
        Path("/system.slice/unrelated.service/metnos-birth-host")
        if failure == "wrong_unit" else current))
    monkeypatch.setattr(cycle.os, "getxattr", lambda fd, name: (
        b"0" if failure == "not_delegated" else b"1"))
    monkeypatch.setattr(cycle.os, "fstat", lambda fd: NS(
        st_uid=1000 if failure == "wrong_owner" else 0))
    changed = []
    monkeypatch.setattr(cycle.os, "fchown", lambda fd, uid, gid: changed.append((uid, gid)))
    monkeypatch.setattr(provisioner, "_service_owned_birth_identity_v2",
                        lambda d: contextlib.nullcontext())
    monkeypatch.setattr(runner, "_cgroup_v2_delegate", lambda: (delegate, None))
    monkeypatch.setattr(cycle, "_probe_cutover_runner", lambda: None)
    if failure:
        with pytest.raises(RuntimeError):
            cycle._delegate_cutover_checks(release.descriptor)
        assert changed == []
    else:
        cycle._delegate_cutover_checks(release.descriptor)
        assert changed == [(release.descriptor.service_uid,
                            release.descriptor.service_gid)] * 8


@pytest.mark.parametrize("cross", [False, True])
@pytest.mark.parametrize("audit_result", [0, 79, 78, 1, 124, -9])
def test_parent_stops_on_every_audit_refusal(applying, cross, audit_result):
    applying.control["audit"] = audit_result
    result = cycle.apply_cycle(cross)
    modes = [command[-1] for command in applying.calls]
    if cross and audit_result == 0:
        assert result == 0 and modes == ["audit", "complete"]
        assert applying.prunes == [{"apply": True}]
    else:
        assert result == (0 if audit_result == 0 else 78) and modes == ["audit"]
        assert applying.prunes == []
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
        read_required_window_v1=lambda: ownership.VerifiedOwnershipWindowV1(
            (), (), selected["distribution"], ())))
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
    monkeypatch.setattr(cycle, "builtin_module_map", lambda python: {})
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


def test_builtin_copy_drift_names_each_copy_unlike_its_own_module(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/a.py").write_bytes(b"A")
    (tmp_path / "runtime/b.py").write_bytes(b"B")
    for name in ("same", "other_module"):
        copy = tmp_path / "runtime/builtin_executor_contracts" / name
        copy.mkdir(parents=True)
        (copy / "implementation.py.src").write_bytes(b"A")
    modules = {"same": "runtime/a.py", "other_module": "runtime/b.py",
               "missing": "runtime/a.py"}
    # Equal to some other module is still drift: only its own module counts.
    assert cycle.builtin_copy_drift(tmp_path, modules) == ["missing", "other_module"]


@pytest.mark.parametrize(("modules", "drift", "expected"), [
    ({"admin": "runtime/system/admin.py"}, ["admin"], "differs from its module: admin"),
    (None, None, "module map unavailable"),
])
def test_copy_drift_is_reported_without_stopping_prepare(
        monkeypatch, tmp_path, capsys, modules, drift, expected):
    monkeypatch.setattr(cycle.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cycle, "run_in_worktree", lambda *command: None)
    monkeypatch.setattr(cycle, "builtin_module_map", lambda python: modules)
    monkeypatch.setattr(cycle, "builtin_copy_drift", lambda tree, found: drift)
    monkeypatch.setattr(cycle, "reviewed_roots", lambda: ((1, "private"), (1, "public")))
    monkeypatch.setattr(cycle, "stage", lambda source, destination: None)
    monkeypatch.setattr(cycle, "census", lambda tree: (0, "census"))
    monkeypatch.setattr(cycle, "CYCLE_DIR", tmp_path / "cycle")
    monkeypatch.setattr(cycle, "STAGING", tmp_path / "cycle" / "export")
    monkeypatch.setattr(cycle, "HANDOFF", tmp_path / "cycle" / "handoff.json")

    assert cycle.prepare() == 0
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("probe", 10), OSError("exec failed")])
def test_module_map_probe_failure_is_bounded_and_advisory(monkeypatch, failure):
    seen = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        raise failure

    monkeypatch.setattr(cycle.subprocess, "run", run)
    assert cycle.builtin_module_map("python") is None
    assert seen["timeout"] == cycle._MODULE_MAP_TIMEOUT_S
