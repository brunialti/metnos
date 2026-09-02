"""Focused proofs for the productive RM-0008 transition composition."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from executor_birth_distribution_manifest import DistributionFile, file_content_hash
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_service_catalog import (
    DecodedServiceCatalogV1, ServiceCatalogEntryV1, ServiceLegacyBindingV1,
)
from install import birth_authority_provisioner as provisioner


LINUX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="the productive transition controls Linux processes and systemd",
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _gate_bytes(closed: bool = True) -> bytes:
    value = "True" if closed else "False"
    return (
        "\ndef closed_build_enforcement() -> bool:\n"
        "    \"\"\"Return the compiled transition state.\"\"\"\n"
        f"    return {value}\n"
    ).encode("ascii")


@LINUX_ONLY
def test_process_tree_observer_covers_open_and_mapped_paths(tmp_path: Path):
    observed_root = tmp_path / "previous"
    observed_root.mkdir()
    (observed_root / "entry.py").write_bytes(b"pass\n")
    proc_root = tmp_path / "proc"
    process = proc_root / "41"
    (process / "fd").mkdir(parents=True)
    (process / "cwd").symlink_to(tmp_path)
    (process / "exe").symlink_to("/usr/bin/python3")
    (process / "maps").write_text("", encoding="utf-8")

    assert not provisioner._process_tree_references_root_v2(
        observed_root, proc_root=proc_root,
    )
    (process / "fd" / "3").symlink_to(observed_root / "entry.py")
    assert provisioner._process_tree_references_root_v2(
        observed_root, proc_root=proc_root,
    )
    (process / "fd" / "3").unlink()
    (process / "maps").write_text(
        "00400000-00401000 r--p 00000000 00:00 0 "
        f"{observed_root / 'entry.py'}\n",
        encoding="utf-8",
    )
    assert provisioner._process_tree_references_root_v2(
        observed_root, proc_root=proc_root,
    )


def test_enforcement_observation_is_bound_to_the_signed_file(tmp_path: Path):
    relative = "runtime/executor_birth_legacy_gate.py"
    gate = tmp_path / relative
    gate.parent.mkdir()
    payload = _gate_bytes()
    gate.write_bytes(payload)
    signed = DistributionFile(
        relative, len(payload), file_content_hash(relative, payload),
        "runtime_code",
    )
    prepared = SimpleNamespace(materials=SimpleNamespace(
        distribution=SimpleNamespace(
            facts=SimpleNamespace(installation_root=tmp_path.as_posix()),
            files=(signed,),
        ),
    ))

    assert provisioner._observe_bound_enforcement_v2(prepared).startswith(
        "sha256:",
    )
    changed = replace(
        prepared.materials.distribution.files[0], size=len(payload) + 1,
    )
    prepared.materials.distribution.files = (changed,)
    with pytest.raises(
        provisioner.BirthProvisioningError,
        match="birth_transition_enforcement_invalid",
    ):
        provisioner._observe_bound_enforcement_v2(prepared)


def _minimal_catalog() -> DecodedServiceCatalogV1:
    entry = ServiceCatalogEntryV1(
        "service-http", "metnos-http.service", None, None,
        "gated_service", "system", "none", None, None, None, (), None,
        (), None, None, True, True,
    )
    bindings = (
        ServiceLegacyBindingV1(
            "legacy-http-system", "service-http", "system_unit", "system",
            "metnos-http.service", "retire_in_group7",
        ),
        ServiceLegacyBindingV1(
            "legacy-script", "service-http", "script", "repository",
            "legacy.sh", "retire_in_group7",
        ),
    )
    return DecodedServiceCatalogV1(D("1"), (entry,), bindings, b"catalog", D("2"))


class _Maintenance:
    def observe(self):
        return {
            "source": "inactive_http_and_inactive_sidecar",
            "units": [{
                "scope": scope,
                "unit": unit,
                "load_state": "loaded",
                "active_state": "inactive",
                "main_pid": 0,
            } for scope, unit in MAINTENANCE_TARGETS_V1],
        }


@LINUX_ONLY
def test_retirement_preserves_the_occupied_fragment_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    system = tmp_path / "system"
    user = tmp_path / "user"
    repository = tmp_path / "repository"
    for root in (system, user, repository):
        root.mkdir()
    old_fragment = b"[Service]\nExecStart=/bin/false\n"
    signed_fragment = b"[Service]\nExecStart=/bin/true\n"
    (system / "metnos-http.service").write_bytes(old_fragment)
    (repository / "legacy.sh").write_bytes(b"#!/bin/sh\n")
    loaded = SimpleNamespace(
        catalog=_minimal_catalog(),
        unit_fragments=(("metnos-http.service", signed_fragment),),
    )
    monkeypatch.setattr(
        provisioner, "_capture_bound_transition_catalog_v2",
        lambda *_args: loaded,
    )
    monkeypatch.setattr(
        provisioner, "_transition_roots_v2",
        lambda _prepared: MappingProxyType({
            "system": system, "user": user, "repository": repository,
        }),
    )
    monkeypatch.setattr(
        provisioner, "_process_tree_references_root_v2",
        lambda _root: False,
    )

    first = provisioner._retire_bound_catalog_v2(
        object(), object(), _Maintenance(),
    )
    from executor_birth_legacy_neutralizer import PRESERVED_EXTENSION_V1

    preserved = system / ("metnos-http.service" + PRESERVED_EXTENSION_V1)
    assert preserved.read_bytes() == old_fragment
    assert not (system / "metnos-http.service").exists()
    assert not (repository / "legacy.sh").exists()

    (system / "metnos-http.service").write_bytes(signed_fragment)
    second = provisioner._retire_bound_catalog_v2(
        object(), object(), _Maintenance(),
    )
    assert second == first
    assert preserved.read_bytes() == old_fragment
    assert (system / "metnos-http.service").read_bytes() == signed_fragment


@LINUX_ONLY
def test_topology_helper_installs_reloads_and_returns_the_live_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import executor_birth_admin_preflight as admin

    system = tmp_path / "system"
    system.mkdir()
    fragment = b"[Service]\nExecStart=/bin/true\n"
    loaded = SimpleNamespace(
        unit_fragments=(("metnos-http.service", fragment),),
    )
    prepared = SimpleNamespace(materials=SimpleNamespace(
        candidate_units=SimpleNamespace(entries=(SimpleNamespace(
            unit_name="metnos-http.service", enablement_links=(),
        ),)),
        descriptor=SimpleNamespace(systemctl_executable="/usr/bin/systemctl"),
    ))
    measurement = SimpleNamespace(
        snapshot=SimpleNamespace(effective_units_hash=D("8")),
    )
    calls = []
    monkeypatch.setattr(
        provisioner, "_capture_bound_transition_catalog_v2",
        lambda *_args: loaded,
    )
    monkeypatch.setattr(
        provisioner, "_transition_roots_v2",
        lambda _prepared: MappingProxyType({"system": system}),
    )
    monkeypatch.setattr(
        provisioner.subprocess, "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        admin, "_capture_cutover_effective_systemd_v2",
        lambda candidate: measurement if candidate is prepared else None,
    )

    assert provisioner._install_bound_topology_v2(
        object(), prepared,
    ) is measurement
    assert (system / "metnos-http.service").read_bytes() == fragment
    assert calls[0][0][0] == ["/usr/bin/systemctl", "daemon-reload"]


@LINUX_ONLY
def test_product_wrapper_keeps_the_crossing_inside_all_three_sessions(
    monkeypatch: pytest.MonkeyPatch,
):
    import config
    import contract_cutover_guard
    import executor_birth_bootstrap as bootstrap
    import executor_birth_admin_preflight as admin
    import executor_birth_distribution_manifest as manifest
    import executor_birth_dominant_startup as dominant
    import executor_birth_ownership_authorities as authorities_module
    import executor_birth_ownership_coordinator as coordinator
    import executor_birth_ownership_preflight as ownership_preflight
    import executor_birth_startup_gate as startup_gate
    import install.executor_birth_source_receiver as source_receiver
    import install.executor_birth_startup_prerequisite as prerequisite_module

    events: list[str] = []
    distribution = SimpleNamespace(
        encoded=b"distribution", signature=b"s" * 64, release_sequence=1,
    )
    complete = SimpleNamespace(name="complete", maintenance_proof=b"maintenance")
    prepared = SimpleNamespace(name="prepared")
    effective = SimpleNamespace(snapshot=SimpleNamespace(effective_units_hash=D("5")))
    final = SimpleNamespace(name="final")
    result = object()

    @contextmanager
    def deployment_lock():
        events.append("deployment-enter")
        yield "deployment"
        events.append("deployment-exit")

    @contextmanager
    def startup_lock():
        events.append("startup-enter")
        yield "startup"
        events.append("startup-exit")

    maintenance = SimpleNamespace(observe=lambda: _Maintenance().observe())

    @contextmanager
    def maintenance_guard(_service_user):
        events.append("maintenance-enter")
        yield maintenance, _Maintenance().observe()
        events.append("maintenance-exit")

    @contextmanager
    def inventory(_maintenance, _evidence):
        events.append("inventory-enter")
        yield (maintenance, object(), b"evidence")
        events.append("inventory-exit")

    descriptor = SimpleNamespace(
        service_user="metnos", service_uid=41, service_gid=42,
        service_home="/srv/metnos",
    )
    preparation = SimpleNamespace(descriptor=descriptor)
    service_state_root = Path("/srv/metnos/.local/state/metnos")
    monkeypatch.setattr(config, "PATH_USER_STATE", service_state_root)
    monkeypatch.setattr(manifest, "verify_current_installation_distribution_v1", lambda *_: distribution)
    monkeypatch.setattr(
        manifest, "capture_current_deployment_descriptor_v1",
        lambda candidate: (candidate, descriptor),
    )
    monkeypatch.setattr(coordinator, "_deployment_lock_v1", deployment_lock)
    monkeypatch.setattr(
        source_receiver, "_load_received_source_with_product_session_v1",
        lambda *_: SimpleNamespace(source_id=D("a")),
    )
    monkeypatch.setattr(
        coordinator, "_reserve_transition_edge_locked_v2", lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(coordinator, "_completed_transition_locked_v2", lambda *_: None)
    monkeypatch.setattr(startup_gate, "_exclusive_startup_gate_v1", startup_lock)
    monkeypatch.setattr(contract_cutover_guard, "_contract_cutover_guard_for_service_user_v1", maintenance_guard)
    monkeypatch.setattr(contract_cutover_guard, "_begin_topology_transition_v1", lambda *_: None)
    monkeypatch.setattr(contract_cutover_guard, "_maintenance_evidence_under_transition_v1", lambda *_: b"maintenance")
    monkeypatch.setattr(coordinator, "_transition_inventory_under_maintenance_v2", inventory)
    monkeypatch.setattr(
        bootstrap, "verify_initial_installer_store_v1",
        lambda *, prove_quiescent, authoring_owner:
        events.append("authoring-seed")
        if prove_quiescent is maintenance and authoring_owner == (41, 42)
        else pytest.fail("authoring seed lost its maintenance or owner binding"),
    )
    monkeypatch.setattr(provisioner, "_prepare_transition_receipt_material_locked_v2", lambda *_: preparation)
    monkeypatch.setattr(provisioner, "_complete_transition_receipts_locked_v2", lambda *_: complete)
    monkeypatch.setattr(admin, "_prepare_cutover_candidate_v2", lambda *_: prepared)
    monkeypatch.setattr(coordinator, "_observe_dominant_identity_locked_v2", lambda *_: (D("1"), D("2"), D("3")))
    monkeypatch.setattr(provisioner, "_capture_bound_transition_catalog_v2", lambda *_: SimpleNamespace(catalog=SimpleNamespace(catalog_id=D("4"))))
    monkeypatch.setattr(provisioner, "_observe_bound_enforcement_v2", lambda *_: D("6"))
    monkeypatch.setattr(provisioner, "_retire_bound_catalog_v2", lambda *_: D("7"))
    monkeypatch.setattr(provisioner, "_install_bound_topology_v2", lambda *_: effective)
    monkeypatch.setattr(admin, "_build_startup_prerequisite_for_cutover_v2", lambda *_: "prerequisite")
    monkeypatch.setattr(prerequisite_module, "_publish_startup_prerequisite_locked_v2", lambda *_: "sealed")
    monkeypatch.setattr(authorities_module, "load_root_ownership_authorities_v1", lambda: "authorities")
    monkeypatch.setattr(coordinator, "_certificate_ready_material_v2", lambda *_args, **_kwargs: "material")
    monkeypatch.setattr(coordinator, "_cross_certificate_boundary_locked_v2", lambda *_args, **_kwargs: "published")
    monkeypatch.setattr(coordinator, "_cross_head_boundary_locked_v2", lambda *_args, **_kwargs: "head")
    monkeypatch.setattr(coordinator, "_cross_preflight_boundary_locked_v2", lambda *_args, **_kwargs: final)
    monkeypatch.setattr(coordinator, "_result", lambda record: result if record is final else None)
    monkeypatch.setattr(ownership_preflight, "canonical_maintenance_proof", lambda **_kwargs: b"maintenance")

    def complete_startup(**observers):
        events.append("composition")
        for name in (
            "observe_identity", "observe_catalog", "observe_enforcement",
            "plan_retirement", "observe_topology",
        ):
            observers[name]()
        for name in (
            "observe_identity", "observe_catalog", "observe_enforcement",
            "plan_retirement", "observe_topology",
        ):
            observers[name]()
        observers["cross"](object())

    monkeypatch.setattr(dominant, "complete_dominant_startup_v1", complete_startup)

    assert provisioner.complete_transition_cutover_v2(
        distribution, D("a"), service_state_root=service_state_root,
    ) is result
    assert events == [
        "deployment-enter", "startup-enter", "maintenance-enter",
        "authoring-seed", "inventory-enter", "composition", "inventory-exit",
        "maintenance-exit", "startup-exit", "deployment-exit",
    ]


def test_product_wrapper_denies_before_lock_when_closed_policy_is_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    import executor_birth_legacy_gate as legacy_gate
    import executor_birth_ownership_coordinator as coordinator

    monkeypatch.setattr(legacy_gate, "closed_build_enforcement", lambda: False)
    monkeypatch.setattr(
        coordinator, "_deployment_lock_v1",
        lambda: pytest.fail("deployment lock must remain unopened"),
    )
    distribution = SimpleNamespace(encoded=b"distribution", signature=b"s" * 64)

    with pytest.raises(
        provisioner.BirthProvisioningError,
        match="birth_ownership_closed_enforcement_required",
    ):
        provisioner.complete_transition_cutover_v2(
            distribution, D("a"),
            service_state_root=Path("/srv/metnos/.local/state/metnos"),
        )


@LINUX_ONLY
def test_maintenance_session_retains_quiescence_across_named_load_states(
    monkeypatch: pytest.MonkeyPatch,
):
    import contract_cutover_guard
    import stack_reconcile
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    state = {"load": "loaded", "active": "inactive"}

    class Systemctl:
        @staticmethod
        def show(_unit: str, _scope: str):
            return {
                "LoadState": state["load"],
                "ActiveState": state["active"],
                "MainPID": "0",
            }

    reconciler = SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    )

    @contextmanager
    def catalog_lock(**_kwargs):
        yield object()

    monkeypatch.setattr(stack_reconcile, "catalog_reconcile_lock", catalog_lock)
    with contract_cutover_guard._contract_cutover_guard_core_v1(
        reconciler,
    ) as (session, evidence):
        initial = canonical_maintenance_proof(
            source=evidence["source"], units=evidence["units"],
        )
        contract_cutover_guard._begin_topology_transition_v1(
            session, initial,
        )
        state["load"] = "masked"
        assert (
            contract_cutover_guard._maintenance_evidence_under_transition_v1(
                session,
            )
            == initial
        )
        state["active"] = "active"
        with pytest.raises(
            contract_cutover_guard.ContractCutoverGuardError,
            match="cutover_blocked",
        ):
            contract_cutover_guard._require_maintenance_session_v1(session)
