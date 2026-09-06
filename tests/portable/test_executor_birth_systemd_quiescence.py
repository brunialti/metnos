"""Systemd observations used by the real RM-0008 quiescence gate."""
from __future__ import annotations

import pytest

from install import executor_birth_systemd_quiescence as quiescence


def _show(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("ascii")


def test_show_adapts_systemd_unit_type_properties() -> None:
    timer = quiescence._parse_show_v1(_show(
        "LoadState=loaded", "ActiveState=active", "UnitFileState=enabled",
    ), "system", "metnos-backup.timer")
    absent = quiescence._parse_show_v1(_show(
        "LoadState=not-found", "ActiveState=inactive",
    ), "user", "metnos-llm.service")

    assert timer.main_pid == 0
    assert absent.unit_file_state == "not-found"


def test_show_requires_main_pid_for_services() -> None:
    with pytest.raises(
        quiescence.SystemdQuiescenceError, match="birth_systemd_quiescence_invalid",
    ):
        quiescence._parse_show_v1(_show(
            "LoadState=loaded", "ActiveState=inactive", "UnitFileState=static",
        ), "user", "metnos-stack-ready.service")


def test_systemd_observation_requests_zero_and_empty_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {}

    def run(command, environment):
        observed["command"] = command
        return type("Completed", (), {"stdout": _show(
            "LoadState=loaded", "ActiveState=inactive", "UnitFileState=enabled",
        )})()

    monkeypatch.setattr(quiescence, "_run_command_v1", run)
    result = quiescence._SubprocessSystemdEffectsV1().observe(
        "system", "metnos-backup.timer", object(),
    )

    assert "--all" in observed["command"]
    assert result.main_pid == 0


def test_static_inactive_unit_is_quiescent_but_active_unit_is_not() -> None:
    stopped = quiescence.SystemdUnitObservationV1(
        "user", "metnos-stack-ready.service", "loaded", "inactive", "static", 0,
    )
    running = quiescence.SystemdUnitObservationV1(
        "user", "metnos-stack-ready.service", "loaded", "active", "static", 0,
    )

    assert quiescence._is_quiescent_v1((stopped,))
    assert not quiescence._is_quiescent_v1((running,))


def test_actions_exclude_static_and_missing_units_from_disable() -> None:
    batch = quiescence.SystemdQuiescenceBatchV1(
        "user", ("enabled.service", "missing.service", "static.service"),
    )
    observed = (
        quiescence.SystemdUnitObservationV1(
            "user", "enabled.service", "loaded", "inactive", "enabled", 0,
        ),
        quiescence.SystemdUnitObservationV1(
            "user", "missing.service", "not-found", "inactive", "not-found", 0,
        ),
        quiescence.SystemdUnitObservationV1(
            "user", "static.service", "loaded", "active", "static", 0,
        ),
    )

    assert quiescence._action_batch_v1("disable", batch, observed).units == (
        "enabled.service",
    )
    assert quiescence._action_batch_v1("stop", batch, observed).units == (
        "static.service",
    )


def test_systemd_effect_rejects_unsuccessful_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = type("Completed", (), {
        "returncode": 1, "stdout": b"", "stderr": b"failed",
    })()
    monkeypatch.setattr(quiescence, "_run_command_v1", lambda *_: completed)

    with pytest.raises(
        quiescence.SystemdQuiescenceError, match="birth_systemd_quiescence_invalid",
    ):
        quiescence._SubprocessSystemdEffectsV1().apply(
            "disable",
            quiescence.SystemdQuiescenceBatchV1("system", ("metnos-http.service",)),
            object(),
        )


def test_core_partitions_actions_and_reobserves_between_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = quiescence.SystemdQuiescencePlanV1((
        quiescence.SystemdQuiescenceBatchV1(
            "system", ("enabled.timer",),
        ),
        quiescence.SystemdQuiescenceBatchV1(
            "user", ("missing.service", "static.service"),
        ),
    ), "digest")

    class Effects:
        def __init__(self):
            self.states = {
                ("system", "enabled.timer"): ("loaded", "inactive", "enabled", 0),
                ("user", "missing.service"): (
                    "not-found", "inactive", "not-found", 0,
                ),
                ("user", "static.service"): ("loaded", "active", "static", 0),
            }
            self.actions = []

        def observe(self, scope, unit, _snapshot):
            return quiescence.SystemdUnitObservationV1(
                scope, unit, *self.states[(scope, unit)],
            )

        def apply(self, action, batch, _snapshot):
            self.actions.append((action, batch.scope, batch.units))
            if action == "disable":
                # Model a service becoming active while its timer is disabled.
                self.states[("system", "enabled.timer")] = (
                    "loaded", "active", "disabled", 0,
                )
            else:
                for unit in batch.units:
                    load, _active, state, _pid = self.states[(batch.scope, unit)]
                    self.states[(batch.scope, unit)] = (
                        load, "inactive", state, 0,
                    )

    effects = Effects()
    monkeypatch.setattr(
        quiescence, "_require_legacy_snapshot_v1", lambda value: value,
    )
    monkeypatch.setattr(
        quiescence, "plan_legacy_systemd_quiescence_v1", lambda: plan,
    )

    proof = quiescence._quiesce_core_v1(object(), effects)

    assert effects.actions == [
        ("disable", "system", ("enabled.timer",)),
        ("stop", "system", ("enabled.timer",)),
        ("stop", "user", ("static.service",)),
    ]
    assert quiescence._is_quiescent_v1(proof.observations)
