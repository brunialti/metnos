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


@pytest.fixture
def release_catalog():
    import executor_birth_service_catalog as catalog

    python = "/var/lib/metnos/python-envs-v1/" + "0" * 64 + "/bin/python"
    executables = (python, "/usr/bin/systemctl", "/usr/bin/Xvfb")
    built = catalog._build_service_catalog_v1(
        installation_root="/srv/release", python_executable=python,
        service_user="metnos", service_gid=1000,
        service_supplementary_gids=(1000,), service_home="/srv/service",
        systemctl_executable="/usr/bin/systemctl",
        target_executables=tuple((path, path.encode()) for path in executables),
    )
    return catalog.LoadedServiceCatalogV1(
        catalog.decode_service_catalog_v1(built.encoded),
        built.unit_fragments, catalog._LOADED_CATALOG_SEAL,
    )


class ReleaseEffects:
    def __init__(self, loaded):
        self.states = {
            name: ("loaded", "active", "enabled", 17 if name.endswith(".service") else 0)
            for name, _content in loaded.unit_fragments
        }
        self.actions = []
        self.after_stop = None

    def observe(self, scope, unit, snapshot):
        assert scope == "system" and snapshot is None
        return quiescence.SystemdUnitObservationV1(scope, unit, *self.states[unit])

    def apply(self, action, batch, snapshot):
        assert action == "stop" and batch.scope == "system" and snapshot is None
        self.actions.append((action, batch.units))
        for unit in batch.units:
            load, _active, state, _pid = self.states[unit]
            self.states[unit] = load, "inactive", state, 0
        if self.after_stop:
            self.after_stop(self)


def test_release_quiescence_stops_exact_catalog_without_disabling(release_catalog):
    effects = ReleaseEffects(release_catalog)
    idle_checks = []

    def idle():
        assert effects.actions == []
        idle_checks.append(True)
        return True

    proof = quiescence._quiesce_release_systemd_core_v1(
        release_catalog, effects, idle,
    )
    expected = tuple(name for name, _content in release_catalog.unit_fragments)
    assert effects.actions == [("stop", expected)]
    assert idle_checks == [True]
    assert all(item.unit_file_state == "enabled" for item in proof.observations)
    assert all(item.active_state == "inactive" and item.main_pid == 0 for item in proof.observations)
    assert {item.unit for item in proof.observations} == set(expected)
    # The legacy projection omits system consumers; it cannot substitute this plan.
    assert set(expected) - {
        unit for scope, unit in quiescence._catalog_targets_v1() if scope == "system"
    }
    again = quiescence._quiesce_release_systemd_core_v1(
        release_catalog, effects, lambda: True,
    )
    assert again == proof
    assert effects.actions == [("stop", expected)]


@pytest.mark.parametrize("idle", [False, None, {"ok": True}])
def test_release_busy_or_ambiguous_idle_denies_before_any_stop(release_catalog, idle):
    effects = ReleaseEffects(release_catalog)
    with pytest.raises(quiescence.SystemdQuiescenceError):
        quiescence._quiesce_release_systemd_core_v1(
            release_catalog, effects, lambda: idle,
        )
    assert effects.actions == []


@pytest.mark.parametrize("stage", ["release_units_stopped", "release_quiescence_proven"])
def test_release_stop_interruption_replays_without_disabling(release_catalog, stage):
    effects = ReleaseEffects(release_catalog)

    def interrupt(observed):
        if observed == stage:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        quiescence._quiesce_release_systemd_core_v1(
            release_catalog, effects, lambda: True, interrupt,
        )
    proof = quiescence._quiesce_release_systemd_core_v1(
        release_catalog, effects, lambda: True,
    )
    assert len(effects.actions) == 1
    assert all(item.unit_file_state == "enabled" for item in proof.observations)


@pytest.mark.parametrize("state", [
    ("error", "inactive", "enabled", 0),
    ("not-found", "inactive", "not-found", 0),
    ("masked", "inactive", "masked", 0),
    ("loaded", "unknown", "enabled", 0),
    ("loaded", "inactive", "unknown", 0),
])
def test_release_unknown_state_denies_before_any_stop(release_catalog, state):
    effects = ReleaseEffects(release_catalog)
    effects.states[next(iter(effects.states))] = state
    with pytest.raises(quiescence.SystemdQuiescenceError):
        quiescence._quiesce_release_systemd_core_v1(
            release_catalog, effects, lambda: True,
        )
    assert effects.actions == []


@pytest.mark.parametrize("state", [
    ("loaded", "active", "enabled", 17),
    ("loaded", "inactive", "enabled", 17),
    ("loaded", "inactive", "disabled", 0),
    ("error", "inactive", "enabled", 0),
])
def test_release_stop_requires_final_state_and_unchanged_enablement(release_catalog, state):
    effects = ReleaseEffects(release_catalog)

    def interfere(port):
        port.states[next(iter(port.states))] = state

    effects.after_stop = interfere
    with pytest.raises(quiescence.SystemdQuiescenceError):
        quiescence._quiesce_release_systemd_core_v1(
            release_catalog, effects, lambda: True,
        )


def test_release_plan_rejects_unsealed_or_missing_catalog_fragments(release_catalog):
    from dataclasses import replace

    for candidate in (object(), replace(release_catalog, unit_fragments=())):
        effects = ReleaseEffects(release_catalog)
        with pytest.raises(quiescence.SystemdQuiescenceError):
            quiescence._quiesce_release_systemd_core_v1(
                candidate, effects, lambda: True,
            )
        assert effects.actions == []
