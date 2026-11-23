"""Portable grouped observations: invented units, PIDs and controlled effects."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import subprocess

import pytest

import contract_cutover_guard as guard
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from install import executor_birth_systemd_quiescence as quiescence
import stack_reconcile as stack

# Synthetic protocol data, unrelated to the private host captures.
SYNTHETIC_UNITS = ("s026-example-agent.service", "s026-example-clock.timer")
SYNTHETIC_OUTPUT = (
    b"Id=s026-example-agent.service\nLoadState=loaded\nActiveState=active\n"
    b"UnitFileState=enabled\nMainPID=42424242\n\n"
    b"Id=s026-example-clock.timer\nLoadState=loaded\nActiveState=inactive\n"
    b"UnitFileState=disabled\n"
)

@pytest.mark.parametrize("reader", ("install", "runtime"))
@pytest.mark.parametrize("duplicate", (False, True))
def test_group_input_validation_precedes_commands(monkeypatch, reader, duplicate):
    monkeypatch.setattr(stack.subprocess, "run", lambda *a, **k: pytest.fail("command ran"))
    unit = SYNTHETIC_UNITS[0]
    monkeypatch.setattr(stack, "MAINTENANCE_TARGETS_V1", (("system", unit),))
    units = (unit, unit) if duplicate else ()
    if reader == "install":
        call = lambda: quiescence._SubprocessSystemdEffectsV1().observe_many(
            "system", units, None,
        )
        error = quiescence.SystemdQuiescenceError
    else:
        call = lambda: stack.Systemctl().show_many(units, "system")
        error = stack.StackFailure
    if duplicate:
        with pytest.raises(error):
            call()
    else:
        assert not call()



def test_cutover_groups_each_scope_on_every_observation(monkeypatch):
    import executor_birth_maintenance_units as maintenance
    targets = tuple((scope, unit) for scope in ("system", "user") for unit in SYNTHETIC_UNITS)
    monkeypatch.setattr(maintenance, "MAINTENANCE_TARGETS_V1", targets)
    calls = []

    class Systemctl:
        def show(self, *_args):
            pytest.fail("maintenance issued an individual query")

        def show_many(self, units, scope):
            calls.append((scope, units))
            # Controlled effect-port data; no claim of a fresh systemd capture.
            return {unit: {"LoadState": "masked", "ActiveState": "inactive", "MainPID": "0"}
                    for unit in units}

    reconciler = SimpleNamespace(systemctl=Systemctl(), require_quiescent=lambda: {
        "source": "inactive_http_and_inactive_sidecar",
    })
    first = guard.prove_stack_stopped(reconciler)
    assert guard.prove_stack_stopped(reconciler) == first
    expected = [(scope, tuple(unit for selected, unit in targets if selected == scope))
                for scope in dict.fromkeys(scope for scope, _unit in targets)]
    assert calls == expected * 2
    assert tuple((row["scope"], row["unit"]) for row in first["units"]) == targets



@pytest.mark.parametrize("obstruction", (None, "bus", "active"))
def test_absent_user_manager_is_proved_once_per_group_and_again_next_time(monkeypatch, obstruction):
    units = SYNTHETIC_UNITS
    service_uid = 1000
    monkeypatch.setattr(stack, "MAINTENANCE_TARGETS_V1", tuple(("user", unit) for unit in units))
    calls, markers, phase = [], [], [0]
    original_lstat = Path.lstat
    monkeypatch.setattr(stack.os, "geteuid", lambda: 0)
    monkeypatch.setattr(stack.Systemctl, "_service_uid", lambda _s: service_uid)

    def lstat(path, *args, **kwargs):
        if str(path) in {"/run/user/1000/systemd", "/run/user/1000/bus"}:
            markers.append(str(path))
            if phase[0] and obstruction == "bus" and path.name == "bus":
                return object()
            raise FileNotFoundError(path)
        return original_lstat(path, *args, **kwargs)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "runuser":
            return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")
        active = "active" if phase[0] and obstruction == "active" else "inactive"
        # Controlled existing manager-probe schema, separate from the captures.
        output = (f"Id=user@{service_uid}.service\nLoadState=loaded\nActiveState={active}\n"
                  "SubState=dead\nMainPID=0\nControlPID=0\nControlGroup=\n")
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(stack.subprocess, "run", run)
    adapter = stack.Systemctl(service_user="metnos-test")
    first = adapter.show_many(units, "user")
    assert all(first[unit]["Id"] == unit and first[unit]["LoadState"] == "manager-absent" for unit in units)
    assert len(calls) == 2 and len(markers) == 2
    phase[0] = 1
    if obstruction:
        states = adapter.show_many(units, "user")
        assert states == {unit: {"LoadState": "error", "ManagerError": "Failed to connect to bus"}
                          for unit in units}
    else:
        assert adapter.show_many(units, "user") == first
        assert len(markers) == 4
    assert len(calls) == 4
    assert all(kwargs["timeout"] == 10 for _argv, kwargs in calls)



@pytest.mark.parametrize("reader", ("install", "runtime"))
@pytest.mark.parametrize("failure", ("stdout", "stderr", "timeout", "empty-error", "valid"))
def test_group_transport_preserves_bounds_and_failure_states(monkeypatch, reader, failure):
    units, raw = SYNTHETIC_UNITS, SYNTHETIC_OUTPUT
    monkeypatch.setattr(stack, "MAINTENANCE_TARGETS_V1", tuple(("system", unit) for unit in units))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        stdout = b"x" * (64 * 1024 + 1) if failure == "stdout" else raw
        stderr = b"x" * (64 * 1024 + 1) if failure == "stderr" else b""
        if failure == "empty-error":
            stdout = b""
        if kwargs.get("text"):
            stdout, stderr = stdout.decode(), stderr.decode()
        return subprocess.CompletedProcess(argv, int(failure == "empty-error"), stdout, stderr)

    monkeypatch.setattr(stack.subprocess, "run", run)
    call = (lambda: quiescence._SubprocessSystemdEffectsV1().observe_many("system", units, None)
            if reader == "install" else stack.Systemctl().show_many(units, "system"))
    if failure == "valid":
        states = call()
        if reader == "install":
            assert tuple(item.unit for item in states) == units
            assert tuple(item.main_pid for item in states) == (42424242, 0)
        else:
            assert tuple(states) == units
            assert states[units[0]]["MainPID"] == "42424242"
    elif failure == "empty-error" and reader == "runtime":
        assert call() == {unit: {"LoadState": "error", "ManagerError": ""} for unit in units}
    else:
        error = quiescence.SystemdQuiescenceError if reader == "install" else stack.StackFailure
        with pytest.raises(error):
            call()
    assert len(calls) == 1  # No retry or division after a bounded group failure.


@pytest.mark.parametrize("scope", ("system", "user"))
@pytest.mark.parametrize("stderr", ("", "controlled error " + "x" * 340))
def test_unresponsive_manager_group_matches_single_states(monkeypatch, scope, stderr):
    units = SYNTHETIC_UNITS
    monkeypatch.setattr(stack, "MAINTENANCE_TARGETS_V1", tuple((scope, unit) for unit in units))
    calls, probes = [], []

    def run(self, selected, *args, **kwargs):
        calls.append((selected, args, kwargs))
        return subprocess.CompletedProcess(args, 1, "controlled unavailable manager\n", stderr)

    def absent(self, unit):
        probes.append(unit)
        return None

    monkeypatch.setattr(stack.Systemctl, "run", run)
    monkeypatch.setattr(stack.Systemctl, "_absent_user_manager", absent)
    adapter = stack.Systemctl()
    expected = {unit: adapter.show(unit, scope) for unit in units}
    assert expected == {unit: {"LoadState": "error", "ManagerError": stderr[-300:]} for unit in units}
    probes.clear()
    assert adapter.show_many(units, scope) == expected
    assert adapter.show_many(units, scope) == expected
    assert probes == ([units[0], units[0]] if scope == "user" else [])
    assert len(calls) == 4 and all(options["timeout_s"] == 10 for _s, _a, options in calls)
