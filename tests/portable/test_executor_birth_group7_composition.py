"""G7-G: the five pieces, composed through the wrapper, on real filesystems.

Each piece has its own proofs. None of them says the pieces FIT: a digest
produced by one module and consumed by another can differ in shape, in framing
or in when it is read, and every one of those mismatches survives a suite of
green unit tests. This is the cell that would catch them.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

import executor_birth_dominant_startup as startup
import executor_birth_dominant_topology as topology
import executor_birth_enforcement_evidence as enforcement
import executor_birth_legacy_neutralizer as neutralizer
import executor_birth_legacy_retirement as retirement


POSIX_ONLY = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="the topology and retirement cores deny on Windows first",
)

_UNIT = b"[Unit]\nDescription=metnos probe\n[Service]\nExecStart=/bin/true\n"
_CLOSED_GATE = '''
def closed_build_enforcement() -> bool:
    """Doc."""
    return True
'''


class _Session:
    __slots__ = ()

    def __reduce__(self):
        raise TypeError("sessions cannot be serialized")


def _digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def cell(tmp_path: Path):
    """One disposable world: unit root, repository root, gate artefact."""
    units = tmp_path / "units"
    units.mkdir()
    repository = tmp_path / "repo"
    (repository / "systemd").mkdir(parents=True)
    (repository / "scripts").mkdir(parents=True)
    (repository / "scripts" / "legacy.sh").write_bytes(b"#!/bin/sh\n")
    gate = tmp_path / "gate"
    gate.mkdir()
    gate_module = gate / enforcement.GATE_MODULE_BASENAME_V1
    gate_module.write_bytes(_CLOSED_GATE.encode("utf-8"))
    return units, repository, gate_module


def _observers(cell, *, bindings: dict[str, bytes] | None = None):
    units, repository, gate_module = cell
    fragments = bindings or {"metnos-probe.service": _UNIT}
    legacy = [{
        "legacy_id": "legacy-probe-script",
        "entry_id": "service-probe",
        "kind": "script",
        "scope": "repository",
        "locator": "scripts/legacy.sh",
        "disposition": "retire_in_group7",
    }]

    def observe_topology() -> str:
        installed = topology.install_for_test_v1(
            topology._TestOnlyTopologyCapabilityV1(units), fragments,
        )
        return topology.topology_digest_v1(installed)

    def observe_catalog() -> str:
        return _digest_of("catalog-of-the-cell")

    def observe_identity():
        return (
            _digest_of("request"),
            _digest_of("previous-head"),
            _digest_of("context-transition"),
        )

    def plan_and_retire() -> str:
        steps = retirement.plan_retirement_v1(legacy)
        retirement.require_no_legacy_in_flight_v1(
            steps, {
                (step.scope, step.locator): "inactive" for step in steps
            },
        )
        neutralizer.neutralize_for_test_v1(
            neutralizer._TestOnlyNeutralizationCapabilityV1(repository), steps,
            replacement_fragments={},
        )
        return retirement.plan_digest_v1(steps)

    def observe_enforcement() -> str:
        return enforcement.require_enforced_v1(
            enforcement.observe_enforcement_v1(gate_module),
        )

    return observe_identity, observe_topology, observe_catalog, plan_and_retire, observe_enforcement


@POSIX_ONLY
def test_the_five_pieces_compose_and_the_crossing_happens_once(cell) -> None:
    """Every digest the wrapper binds is produced by a real module.

    The wrapper reads each observer twice. Each observer here performs a real
    side effect the first time — installing units, masking, revoking — so the
    second reading only agrees if every one of those operations is idempotent
    AND reports the same identity when it finds its own work already done.
    That agreement is the property the individual suites cannot show.
    """
    units, repository, _gate = cell
    identity, unit_topology, catalog, retire, enforce = _observers(cell)
    crossed: list[startup.DominantStartupReceiptV1] = []

    receipt = startup._complete_dominant_startup_for_test_v1(
        sessions=(_Session(), _Session(), _Session()),
        observe_identity=identity,
        observe_topology=unit_topology,
        observe_catalog=catalog,
        plan_retirement=retire,
        observe_enforcement=enforce,
        cross=crossed.append,
    )

    assert crossed == [receipt]
    assert (units / "metnos-probe.service").read_bytes() == _UNIT
    retired = repository / "scripts" / (
        "legacy.sh" + neutralizer.RETIRED_EXTENSION_V1
    )
    assert retired.is_file() and not (repository / "scripts" / "legacy.sh").exists()


@POSIX_ONLY
def test_a_topology_that_changes_between_the_readings_stops_everything(
    cell,
) -> None:
    """The second reading is what makes the composition safe, not the first."""
    units, _repository, _gate = cell
    identity, unit_topology, catalog, retire, enforce = _observers(cell)
    calls = {"n": 0}

    def drifting_topology() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return unit_topology()
        # Someone installed another unit between the two readings.
        return topology.topology_digest_v1(topology.install_for_test_v1(
            topology._TestOnlyTopologyCapabilityV1(units),
            {"metnos-probe.service": _UNIT, "metnos-other.timer": b"[Timer]\n"},
        ))

    crossed: list[str] = []
    with pytest.raises(startup.DominantStartupError) as drifted:
        startup._complete_dominant_startup_for_test_v1(
            sessions=(_Session(), _Session(), _Session()),
            observe_identity=identity,
            observe_topology=drifting_topology,
            observe_catalog=catalog,
            plan_retirement=retire,
            observe_enforcement=enforce,
            cross=crossed.append,
        )
    assert drifted.value.code == "dominant_startup_binding_drift"
    assert crossed == []


@POSIX_ONLY
def test_an_open_gate_stops_the_composition_at_its_own_step(cell) -> None:
    """A build that does not carry the closed bit never reaches the crossing."""
    units, _repository, gate_module = cell
    gate_module.write_bytes(_CLOSED_GATE.replace("True", "False").encode("utf-8"))
    identity, unit_topology, catalog, retire, enforce = _observers(cell)

    crossed: list[str] = []
    with pytest.raises(enforcement.EnforcementEvidenceError) as denied:
        startup._complete_dominant_startup_for_test_v1(
            sessions=(_Session(), _Session(), _Session()),
            observe_identity=identity,
            observe_topology=unit_topology,
            observe_catalog=catalog,
            plan_retirement=retire,
            observe_enforcement=enforce,
            cross=crossed.append,
        )
    assert denied.value.code == "enforcement_not_closed"
    assert crossed == []
    assert not any(units.iterdir())
    assert (cell[1] / "scripts" / "legacy.sh").is_file()
