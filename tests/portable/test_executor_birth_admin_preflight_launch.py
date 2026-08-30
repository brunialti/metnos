"""Compact launch oracles for the autonomous RM-0008 preflight."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as preflight


def D(character: str) -> str:
    return "sha256:" + character * 64


def _entry(
    *, execution_kind: str = "python_module", notify: bool = False,
) -> preflight._ServiceCatalogEntryV1:
    directives = [
        preflight._ServiceDirectiveV1(
            "Service", "Type", "scalar", ("notify" if notify else "simple",),
        ),
        preflight._ServiceDirectiveV1(
            "Service", "User", "scalar", ("metnos",),
        ),
        preflight._ServiceDirectiveV1(
            "Service", "Group", "scalar", ("991",),
        ),
        preflight._ServiceDirectiveV1(
            "Service", "SupplementaryGroups", "scalar", ("44 991",),
        ),
    ]
    if notify:
        directives.extend((
            preflight._ServiceDirectiveV1(
                "Service", "NotifyAccess", "scalar", ("main",),
            ),
            preflight._ServiceDirectiveV1(
                "Service", "WatchdogSec", "duration", ("45s",),
            ),
        ))
    spec = preflight._ServiceUnitSpecV1(D("1"), tuple(directives))
    return preflight._ServiceCatalogEntryV1(
        "service-probe", "probe.service", None, None,
        "gated_service", "system", execution_kind,
        "/usr/bin/python3" if execution_kind == "python_module" else "/bin/true",
        D("2"), "probe.main" if execution_kind == "python_module" else None,
        ("--probe",), "/release/runtime",
        (preflight._ServiceEnvironmentV1("PROBE_MODE", "signed"),),
        None, spec, True, False,
    )


def _operational(entry: preflight._ServiceCatalogEntryV1):
    descriptor = SimpleNamespace(
        installation_root="/release", service_user="metnos",
        service_uid=990, service_gid=991,
        service_supplementary_gids=(44, 991),
        service_home="/var/lib/metnos", service_shell="/usr/sbin/nologin",
        python_executable="/usr/bin/python3",
    )
    materials = SimpleNamespace(
        descriptor=descriptor, catalog=SimpleNamespace(entries=(entry,)),
    )
    observed = preflight._ObservedEffectiveSystemdV1(
        SimpleNamespace(
            materials=materials,
            capture=SimpleNamespace(executables=SimpleNamespace(
                python=SimpleNamespace(resolved=SimpleNamespace(
                    canonical_path="/usr/bin/python3",
                )),
            )),
        ), SimpleNamespace(),
        SimpleNamespace(),
    )
    return preflight._OperationalPreflightV1(
        SimpleNamespace(), SimpleNamespace(),
        preflight._ObservedEffectiveSystemdProductV1(observed),
    )


def _plan(entry: preflight._ServiceCatalogEntryV1) -> preflight._LaunchPlanV1:
    return preflight._LaunchPlanV1(
        entry, "/release", "metnos", 990, 991, (44, 991),
        "/var/lib/metnos", "/usr/sbin/nologin", entry.python_module,
        entry.target_args, entry.target_working_directory or "/release",
        (("HOME", "/var/lib/metnos"),),
        ("/release/runtime", "/usr/lib/python3"), 0o027,
    )


def test_launch_plan_uses_only_signed_identity_environment_and_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry()
    monkeypatch.setattr(
        preflight, "_trusted_python_path_v1",
        lambda root, working: (root, working),
    )
    monkeypatch.setattr(preflight.os, "readlink", lambda _path: "/usr/bin/python3")
    monkeypatch.setenv("ATTACKER_PATH", "/tmp/attacker")

    plan = preflight._make_launch_plan_v1(_operational(entry), entry)

    assert plan.service_uid == 990
    assert plan.service_gid == 991
    assert plan.service_supplementary_gids == (44, 991)
    assert dict(plan.environment) == {
        "HOME": "/var/lib/metnos", "LOGNAME": "metnos",
        "PROBE_MODE": "signed", "SHELL": "/usr/sbin/nologin",
        "USER": "metnos",
    }
    assert "ATTACKER_PATH" not in dict(plan.environment)
    assert plan.python_path == ("/release", "/release/runtime")
    assert plan.umask == 0o027


def test_notify_environment_is_closed_and_pid_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(notify=True)
    monkeypatch.setenv("NOTIFY_SOCKET", "@rm0008-probe")
    monkeypatch.setenv("WATCHDOG_USEC", "45000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    monkeypatch.setenv("UNSIGNED", "discard-me")
    assert preflight._launch_dynamic_environment_v1(entry) == (
        ("NOTIFY_SOCKET", "@rm0008-probe"),
        ("WATCHDOG_PID", str(os.getpid())),
        ("WATCHDOG_USEC", "45000000"),
    )

    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))
    with pytest.raises(preflight.PreflightError) as failure:
        preflight._launch_dynamic_environment_v1(entry)
    assert failure.value.code == preflight.CODE_INVALID


def test_python_bootstrap_has_one_exact_authenticated_runpy_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_entry())
    calls = []
    monkeypatch.setattr(preflight.sys, "path", ["attacker"])
    monkeypatch.setattr(preflight.sys, "argv", ["attacker"])
    monkeypatch.setattr(
        preflight.runpy, "run_module",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    preflight._launch_python_target_v1(plan)

    assert preflight.sys.path == list(plan.python_path)
    assert preflight.sys.argv == ["probe.main", "--probe"]
    assert calls == [(('probe.main',), {
        "run_name": "__main__", "alter_sys": False,
    })]


@pytest.mark.parametrize("mutated", (False, True))
def test_autonomous_import_closure_matches_exact_runpy_door(
    tmp_path, mutated: bool,
) -> None:
    path = "runtime/executor_birth_admin_preflight.py"
    scope = "rogue" if mutated else "_launch_python_target_v1"
    source = (
        "import runpy\n"
        f"def {scope}(plan):\n"
        "    runpy.run_module(plan.python_module, "
        'run_name="__main__", alter_sys=False)\n'
    ).encode("ascii")
    item = preflight.DistributionFileV1(
        path, len(source), D("f"), "runtime_code",
    )
    if not mutated:
        preflight._verify_local_import_closure_v1(
            tmp_path, (item,), {path: source},
        )
    else:
        with pytest.raises(preflight.PreflightError) as failure:
            preflight._verify_local_import_closure_v1(
                tmp_path, (item,), {path: source},
            )
        assert failure.value.code == preflight.CODE_INVALID
        assert failure.value.detail == "dynamic code loader"


def test_native_exec_failure_keeps_gate_for_caller_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(execution_kind="native_executable")
    plan = _plan(entry)
    lease = preflight._LaunchGateLeaseV1(9)
    events = []
    monkeypatch.setattr(
        preflight, "_drop_service_privileges_v1",
        lambda selected: events.append(("drop", selected)),
    )
    monkeypatch.setattr(
        preflight, "_close_launch_descriptors_v1",
        lambda keep: events.append(("close", keep)),
    )
    monkeypatch.setattr(
        preflight.os, "set_inheritable",
        lambda descriptor, value: events.append(
            ("cloexec", descriptor, value),
        ),
    )
    monkeypatch.setattr(
        preflight.os, "execve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("probe")),
    )

    with pytest.raises(preflight.PreflightError) as failure:
        preflight._launch_gated_service_v1(plan, lease)
    assert failure.value.code == preflight.CODE_INVALID
    assert lease.descriptor == 9
    assert events == [
        ("drop", plan), ("cloexec", 9, False), ("close", 9),
    ]


def test_python_launch_closes_gate_immediately_before_runpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(_entry())
    lease = preflight._LaunchGateLeaseV1(11)
    events = []
    monkeypatch.setattr(
        preflight, "_drop_service_privileges_v1",
        lambda selected: events.append(("drop", selected)),
    )
    monkeypatch.setattr(
        preflight, "_release_startup_gate_v1",
        lambda descriptor: events.append(("release", descriptor)),
    )
    monkeypatch.setattr(
        preflight, "_close_launch_descriptors_v1",
        lambda keep: events.append(("close", keep)),
    )
    monkeypatch.setattr(
        preflight, "_launch_python_target_v1",
        lambda selected: events.append(("runpy", selected)),
    )

    preflight._launch_gated_service_v1(plan, lease)

    assert lease.descriptor == -1
    assert events == [
        ("drop", plan), ("release", 11), ("close", None), ("runpy", plan),
    ]
