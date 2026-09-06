"""Focused proofs for the productive RM-0008 transition composition."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from executor_birth_account_identity import PosixAccountRecordV1
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


def test_legacy_identity_facade_uses_the_shared_account_owner(monkeypatch) -> None:
    account = PosixAccountRecordV1(
        name="legacy-metnos", uid=981, gid=982,
        home="/srv/legacy-metnos", shell="/usr/sbin/nologin",
    )
    monkeypatch.setattr(
        provisioner._account_identity, "resolve_posix_account_v1",
        lambda name: account if name == account.name else None,
    )

    resolved = provisioner._resolve_legacy_service_identity_v2(account.name)

    assert resolved.name == account.name
    assert resolved.uid == account.uid
    assert resolved.gid == account.gid
    assert resolved.home == Path(account.home)


def test_legacy_identity_facade_preserves_the_public_error_code(monkeypatch) -> None:
    account = PosixAccountRecordV1(
        name="different", uid=981, gid=982,
        home="/srv/legacy-metnos", shell="/usr/sbin/nologin",
    )
    monkeypatch.setattr(
        provisioner._account_identity, "resolve_posix_account_v1",
        lambda _name: account,
    )

    with pytest.raises(provisioner.BirthProvisioningError) as captured:
        provisioner._resolve_legacy_service_identity_v2("legacy-metnos")
    assert captured.value.code == "birth_transition_service_identity_changed"


def test_transition_child_environment_uses_the_shared_xdg_layout() -> None:
    descriptor = SimpleNamespace(
        installation_root="/var/lib/metnos/releases/1",
        service_user="metnos",
        service_uid=991,
        service_gid=992,
        service_home="/srv/metnos",
        service_shell="/usr/sbin/nologin",
    )

    assert provisioner._transition_service_environment_v2(descriptor) == {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "METNOS_INSTALL_ROOT": "/var/lib/metnos/releases/1",
        "HOME": "/srv/metnos",
        "LOGNAME": "metnos",
        "USER": "metnos",
        "METNOS_USER_DATA": "/srv/metnos/.local/share/metnos",
        "METNOS_USER_STATE": "/srv/metnos/.local/state/metnos",
        "METNOS_USER_CONFIG": "/srv/metnos/.config/metnos",
        "METNOS_USER_CACHE": "/srv/metnos/.cache/metnos",
        "METNOS_WORKSPACE": "/srv/metnos/.local/share/metnos/workspace",
    }


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


@LINUX_ONLY
def test_entrypoint_observer_ignores_unrelated_work_in_the_same_tree(
    tmp_path: Path,
):
    observed_root = tmp_path / "previous"
    observed_root.mkdir()
    entry = observed_root / "install" / "entry.py"
    entry.parent.mkdir()
    entry.write_bytes(b"pass\n")
    unrelated = observed_root / "internal" / "helper.py"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"pass\n")
    proc_root = tmp_path / "proc"
    process = proc_root / "41"
    (process / "fd").mkdir(parents=True)
    (process / "cwd").symlink_to(observed_root)
    (process / "exe").symlink_to("/usr/bin/python3")
    (process / "maps").write_text("", encoding="utf-8")
    (process / "cmdline").write_bytes(
        b"python3\0internal/helper.py\0",
    )

    assert not provisioner._process_tree_references_entries_v2(
        observed_root, ("install/entry.py",), proc_root=proc_root,
    )
    (process / "cmdline").write_bytes(
        b"python3\0-m\0install.entry\0",
    )
    assert provisioner._process_tree_references_entries_v2(
        observed_root, ("install/entry.py",), proc_root=proc_root,
    )


def test_enforcement_observation_is_bound_to_the_signed_file(tmp_path: Path):
    relative = "runtime/executor_birth_authority_gate.py"
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


@LINUX_ONLY
def test_initial_predecessor_is_published_from_the_bound_legacy_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import executor_birth_ownership_authorities as authorities
    import executor_birth_ownership_coordinator as coordinator
    import executor_birth_service_catalog as service_catalog
    from executor_birth_distribution_assembler import (
        decode_predecessor_descriptor_v1,
    )

    root = tmp_path / "legacy"
    entry = root / "legacy.sh"
    root.mkdir()
    entry.write_bytes(b"#!/bin/sh\nexit 0\n")
    catalog = _minimal_catalog()
    complete = SimpleNamespace(
        sequence=1,
        state=coordinator.OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        release_sequence=1,
        install_transaction_id=D("3"),
        administrative_bundle_hash=D("4"),
        service_coverage_hash=catalog.service_coverage_hash,
    )
    published = {}
    monkeypatch.setattr(
        coordinator, "OwnershipCoordinatorRecordV2", SimpleNamespace,
    )
    monkeypatch.setattr(authorities, "DEFAULT_OWNERSHIP_ROOT_V1", tmp_path)
    monkeypatch.setattr(
        provisioner, "_require_transition_directory_v2",
        lambda path, *, owner: path
        if path == root and owner == (0, 0)
        else pytest.fail("legacy root binding changed"),
    )
    monkeypatch.setattr(
        service_catalog, "capture_current_service_catalog_v1",
        lambda distribution: SimpleNamespace(catalog=catalog)
        if distribution == "distribution"
        else pytest.fail("distribution binding changed"),
    )
    monkeypatch.setattr(
        provisioner, "_capture_predecessor_file_v2",
        lambda selected_root, locator: __import__(
            "executor_birth_distribution_assembler"
        ).PredecessorFileV1(locator, len(entry.read_bytes()), D("5"))
        if selected_root == root and locator == "legacy.sh"
        else pytest.fail("legacy entry binding changed"),
    )
    monkeypatch.setattr(
        provisioner, "_predecessor_file_locators_v2",
        lambda selected_root, selected_catalog: ("legacy.sh",)
        if selected_root == root and selected_catalog is catalog
        else pytest.fail("legacy inventory binding changed"),
    )
    monkeypatch.setattr(
        coordinator, "_publish_control_no_replace_v2",
        lambda selected_root, name, encoded, **kwargs: published.update({
            "root": selected_root, "name": name, "encoded": encoded,
            "options": kwargs,
        }),
    )

    provisioner._publish_initial_predecessor_v2(
        "distribution", complete, root,
    )

    decoded = decode_predecessor_descriptor_v1(published["encoded"])
    assert published["root"] == tmp_path
    assert published["name"] == "predecessor-v1.json"
    assert decoded.transaction_id == D("3")
    assert decoded.installation_root == root.as_posix()
    assert decoded.files[0].path == "legacy.sh"
    assert decoded.service_catalog_id == catalog.catalog_id


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
        lambda _prepared, _legacy_identity: MappingProxyType({
            "system": system, "user": user, "repository": repository,
        }),
    )
    monkeypatch.setattr(
        provisioner, "_process_tree_references_entries_v2",
        lambda _root, _locators: False,
    )

    first = provisioner._retire_bound_catalog_v2(
        object(), object(), _Maintenance(), object(),
    )
    from executor_birth_legacy_neutralizer import PRESERVED_EXTENSION_V1

    preserved = system / ("metnos-http.service" + PRESERVED_EXTENSION_V1)
    assert preserved.read_bytes() == old_fragment
    assert not (system / "metnos-http.service").exists()
    assert not (repository / "legacy.sh").exists()

    (system / "metnos-http.service").write_bytes(signed_fragment)
    second = provisioner._retire_bound_catalog_v2(
        object(), object(), _Maintenance(), object(),
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
        descriptor=SimpleNamespace(
            systemctl_executable="/usr/bin/systemctl",
            system_unit_root=system.as_posix(),
        ),
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
        provisioner, "_require_transition_directory_v2",
        lambda path, *, owner: path
        if path == system and owner == (0, 0)
        else pytest.fail("system unit root binding changed"),
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
    import executor_birth_transition_gate as transition_gate
    import install.executor_birth_source_receiver as source_receiver
    import install.executor_birth_startup_gate as startup_gate_installer
    import install.executor_birth_startup_prerequisite as prerequisite_module
    import install.executor_birth_systemd as systemd_installer

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

    class Maintenance:
        observe = staticmethod(lambda: _Maintenance().observe())

        def __call__(self):
            events.append("maintenance-prove")
            return True

    maintenance = Maintenance()
    transition_current = object()

    @contextmanager
    def maintenance_guard(_service_user, *, catalog_trusted_owner):
        assert catalog_trusted_owner == (41, 42)
        events.append("maintenance-enter")
        yield maintenance, _Maintenance().observe()
        events.append("maintenance-exit")

    @contextmanager
    def inventory(
        _gate, _session, enumerator, _maintenance, _evidence, *,
        catalog_trusted_owner,
    ):
        assert enumerator is transition_current
        assert catalog_trusted_owner == (41, 42)
        events.append("inventory-enter")
        yield (maintenance, object(), b"evidence", transition_current)
        events.append("inventory-exit")

    descriptor = SimpleNamespace(
        service_user="metnos", service_uid=41, service_gid=42,
        service_home="/srv/metnos", service_shell="/usr/sbin/nologin",
        service_supplementary_gids=(42,),
    )
    preparation = SimpleNamespace(descriptor=descriptor)
    service_state_root = Path("/srv/metnos/.local/state/metnos")
    monkeypatch.setattr(config, "PATH_USER_STATE", service_state_root)
    legacy_identity = SimpleNamespace(name="legacy-metnos")
    monkeypatch.setattr(
        provisioner, "_resolve_legacy_service_identity_v2",
        lambda name: legacy_identity
        if name == "legacy-metnos"
        else pytest.fail("legacy service identity changed"),
    )
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
    monkeypatch.setattr(
        transition_gate, "_transition_gate_snapshot_locked_v2",
        lambda *_: events.append("gate-snapshot") or object(),
    )
    monkeypatch.setattr(
        transition_gate, "_transition_gate_phase_locked_v2", lambda *_: None,
    )
    @contextmanager
    def service_identity(candidate):
        assert candidate is descriptor
        events.append("service-enter")
        yield
        events.append("service-exit")

    monkeypatch.setattr(
        provisioner, "_service_owned_birth_identity_v2", service_identity,
    )
    monkeypatch.setattr(
        transition_gate, "_transition_current_enumerator_v2",
        lambda *_: events.append("current-enumerator") or transition_current,
    )
    monkeypatch.setattr(
        startup_gate_installer, "install_startup_gate_v1",
        lambda session: events.append("startup-install")
        if session == "deployment"
        else pytest.fail("startup gate lost the deployment lock"),
    )
    monkeypatch.setattr(startup_gate, "_exclusive_startup_gate_v1", startup_lock)
    monkeypatch.setattr(
        provisioner, "_initialize_transition_ownership_chain_v2",
        lambda candidate: events.append("chain-initialize")
        if candidate is descriptor
        else pytest.fail("chain initialization lost the signed identity"),
    )
    monkeypatch.setattr(contract_cutover_guard, "_contract_cutover_guard_for_service_user_v1", maintenance_guard)
    monkeypatch.setattr(contract_cutover_guard, "_begin_topology_transition_v1", lambda *_: None)
    monkeypatch.setattr(contract_cutover_guard, "_maintenance_evidence_under_transition_v1", lambda *_: b"maintenance")
    monkeypatch.setattr(
        transition_gate, "_transition_inventory_under_maintenance_v2", inventory,
    )
    def verify_initial(
        *, prove_quiescent, trusted_authoring_owner,
        defer_v1_receipts_to_transition_v2,
    ):
        if not (
            prove_quiescent is maintenance
            and trusted_authoring_owner == (41, 42)
            and defer_v1_receipts_to_transition_v2 is True
        ):
            pytest.fail("authoring seed lost its maintenance or owner binding")
        assert prove_quiescent() is True
        events.append("authoring-seed")
        assert prove_quiescent() is True

    monkeypatch.setattr(
        bootstrap, "verify_initial_installer_store_v1", verify_initial,
    )
    monkeypatch.setattr(
        provisioner, "_converge_transition_contracts_v2",
        lambda candidate: events.append("contract-convergence")
        if candidate is descriptor
        else pytest.fail("contract convergence lost the signed descriptor"),
    )
    def prepare_legacy(candidate, verified, proof, *, require_live_ready):
        if candidate is not descriptor or verified is not distribution or proof is not maintenance:
            pytest.fail("legacy adoption lost its ordered lock binding")
        assert require_live_ready is True
        events.append("legacy-state-adoption")
        return SimpleNamespace(record_sha256=D("7"), ready=False)

    def complete_legacy(candidate, verified, prepared_record, proof, *, live):
        if (
            candidate is not descriptor or verified is not distribution
            or prepared_record.record_sha256 != D("7")
            or proof is not maintenance or live is not True
        ):
            pytest.fail("legacy inspection lost its historical binding")
        events.append("legacy-state-inspection")
        return SimpleNamespace(record_sha256=D("8"))

    monkeypatch.setattr(
        provisioner, "_prepare_transition_legacy_state_v2", prepare_legacy,
    )
    monkeypatch.setattr(
        provisioner, "_complete_transition_legacy_state_v2", complete_legacy,
    )
    monkeypatch.setattr(provisioner, "_prepare_transition_receipt_material_locked_v2", lambda *_: preparation)
    def complete_receipts(*_args, **kwargs):
        assert kwargs["initial_legacy_state_record_sha256"] == D("8")
        return complete

    monkeypatch.setattr(
        provisioner, "_complete_transition_receipts_locked_v2",
        complete_receipts,
    )
    monkeypatch.setattr(
        provisioner, "_publish_initial_predecessor_v2",
        lambda candidate, record, root: events.append("predecessor")
        if (
            candidate is distribution and record is complete
            and root == "/opt/metnos"
        )
        else pytest.fail("predecessor binding changed"),
    )
    def install_administrative(candidate, session):
        if candidate is not distribution or session != "deployment":
            pytest.fail("administrative install lost its authenticated lock binding")
        events.append("administrative-install")

    def prepare_candidate(complete_record, candidate):
        if complete_record is not complete or candidate is not distribution:
            pytest.fail("candidate preparation lost its authenticated binding")
        events.append("candidate-prepare")
        return prepared

    monkeypatch.setattr(
        systemd_installer, "install_group6_administrative_v1",
        install_administrative,
    )
    monkeypatch.setattr(admin, "_prepare_cutover_candidate_v2", prepare_candidate)
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
        legacy_service_user="legacy-metnos",
        legacy_installation_root="/opt/metnos",
    ) is result
    assert events == [
        "deployment-enter", "startup-install", "startup-enter", "chain-initialize",
        "gate-snapshot",
        "maintenance-enter", "maintenance-prove", "legacy-state-adoption",
        "maintenance-exit", "contract-convergence",
        "service-enter", "current-enumerator", "service-exit",
        "maintenance-enter", "maintenance-prove", "authoring-seed",
        "maintenance-prove", "legacy-state-inspection", "inventory-enter",
        "predecessor",
        "administrative-install", "candidate-prepare", "composition",
        "inventory-exit",
        "maintenance-exit", "startup-exit", "deployment-exit",
    ]


def test_completed_cutover_only_reattests_and_skips_administrative_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    import executor_birth_admin_preflight as admin
    import executor_birth_distribution_manifest as manifest
    import executor_birth_authority_gate as authority_gate
    import executor_birth_ownership_coordinator as coordinator
    import install.executor_birth_source_receiver as source_receiver
    import install.executor_birth_systemd as systemd_installer

    events = []
    distribution = SimpleNamespace(
        encoded=b"distribution", signature=b"s" * 64, release_sequence=2,
    )
    descriptor = SimpleNamespace(
        service_user="metnos", service_uid=41, service_gid=42,
        service_home="/srv/metnos",
    )
    completed, result = object(), object()
    expected_distribution = distribution

    @contextmanager
    def deployment_lock():
        events.append("deployment-enter")
        yield "deployment"
        events.append("deployment-exit")

    def reserve(session, *, distribution, source_id):
        assert (session, distribution, source_id) == (
            "deployment", expected_distribution, D("a"),
        )
        events.append("reserve-observe")

    completed_calls = 0

    def observe_completed(session, candidate):
        nonlocal completed_calls
        assert session == "deployment" and candidate is distribution
        completed_calls += 1
        events.append("completed-observe")
        return completed

    service_state_root = Path("/srv/metnos/.local/state/metnos")
    monkeypatch.setattr(config, "PATH_USER_STATE", service_state_root)
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(
        provisioner, "_resolve_legacy_service_identity_v2",
        lambda _name: SimpleNamespace(name="legacy-metnos"),
    )
    monkeypatch.setattr(
        manifest, "verify_current_installation_distribution_v1",
        lambda *_args: distribution,
    )
    monkeypatch.setattr(
        manifest, "capture_current_deployment_descriptor_v1",
        lambda candidate: (candidate, descriptor),
    )
    monkeypatch.setattr(coordinator, "_deployment_lock_v1", deployment_lock)
    monkeypatch.setattr(
        source_receiver, "_load_received_source_with_product_session_v1",
        lambda source_id, session: SimpleNamespace(source_id=source_id)
        if session == "deployment" else pytest.fail("deployment lock lost"),
    )
    monkeypatch.setattr(
        coordinator, "_reserve_transition_edge_locked_v2", reserve,
    )
    monkeypatch.setattr(
        coordinator, "_completed_transition_locked_v2", observe_completed,
    )
    monkeypatch.setattr(
        admin, "_attest_operational_preflight_v1",
        lambda: events.append("operational-attestation"),
    )
    monkeypatch.setattr(
        coordinator, "_result",
        lambda record: result if record is completed
        else pytest.fail("completed result changed"),
    )
    monkeypatch.setattr(
        systemd_installer, "install_group6_administrative_v1",
        lambda *_args: pytest.fail("completed cutover rewrote administrative TCB"),
    )
    monkeypatch.setattr(
        admin, "_prepare_cutover_candidate_v2",
        lambda *_args: pytest.fail("completed cutover recaptured candidate TCB"),
    )

    assert provisioner.complete_transition_cutover_v2(
        distribution, D("a"), service_state_root=service_state_root,
        legacy_service_user="legacy-metnos",
        legacy_installation_root="/opt/metnos",
    ) is result
    assert completed_calls == 2
    assert events == [
        "deployment-enter", "reserve-observe", "completed-observe",
        "operational-attestation", "completed-observe", "deployment-exit",
    ]


def test_product_wrapper_denies_before_lock_when_closed_policy_is_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    import executor_birth_authority_gate as authority_gate
    import executor_birth_ownership_coordinator as coordinator

    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: False)
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
            legacy_service_user="legacy-metnos",
            legacy_installation_root="/opt/metnos",
        )


def test_contract_convergence_child_is_bound_to_the_signed_service_identity(
    monkeypatch,
) -> None:
    descriptor = SimpleNamespace(
        installation_root="/var/lib/metnos/executor-birth/releases-v1/1",
        python_executable="/var/lib/metnos/executor-birth/venv/bin/python",
        service_user="metnos-service", service_uid=991, service_gid=992,
        service_supplementary_gids=(44, 992),
        service_home="/var/lib/metnos-service",
        service_shell="/usr/sbin/nologin",
    )
    observed = {}

    def run(command, **options):
        observed["command"] = command
        observed.update(options)
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"changed":24,"current":98,"examined":122}\n',
            stderr=b"",
        )

    monkeypatch.setattr(provisioner.subprocess, "run", run)

    assert provisioner._converge_transition_contracts_v2(descriptor) == {
        "changed": 24, "current": 98, "examined": 122,
    }
    assert observed["command"] == [
        descriptor.python_executable, "-I", "-B",
        descriptor.installation_root
        + "/install/executor_birth_contract_convergence.py",
    ]
    assert observed["user"] == descriptor.service_uid
    assert observed["group"] == descriptor.service_gid
    assert observed["extra_groups"] == descriptor.service_supplementary_gids
    assert observed["umask"] == 0o077
    assert observed["env"]["HOME"] == descriptor.service_home
    assert observed["env"]["METNOS_INSTALL_ROOT"] == descriptor.installation_root


@pytest.mark.parametrize("changed", [True, False])
def test_legacy_preparation_helper_binds_signed_account_and_build(
    monkeypatch, changed,
) -> None:
    from contextlib import contextmanager
    from executor_birth_legacy_state_request import (
        require_canonical_legacy_state_request_v1,
    )
    from install import executor_birth_legacy_state_adoption as adoption
    from install import executor_birth_legacy_state_effect_posix as effect
    from install import executor_birth_legacy_state_inspection as inspection

    descriptor = SimpleNamespace(
        service_user="metnos", service_uid=991, service_gid=992,
        service_supplementary_gids=(992,),
        service_home="/var/lib/metnos-service",
        service_shell="/usr/sbin/nologin",
    )
    distribution = SimpleNamespace(
        identity=SimpleNamespace(closed_build_id=D("7")),
    )
    proof, raw_effects = lambda: True, object()
    expected = SimpleNamespace(changed=changed, record_sha256=D("8"), ready=True)
    observed = {}

    @contextmanager
    def locked(request, account, prove_quiescent):
        observed.update(
            request=request, account=account, proof=prove_quiescent,
        )
        yield raw_effects

    def prepare(request, effects):
        assert request is observed["request"] and effects is raw_effects
        return expected

    def inspect_live(request, account, record_sha256, maintenance_session):
        assert request is observed["request"] and account is observed["account"]
        assert record_sha256 == D("8") and maintenance_session is proof
        observed["live-inspection"] = True

    monkeypatch.setattr(effect, "locked_legacy_state_effects_v1", locked)
    monkeypatch.setattr(adoption, "prepare_legacy_state_authoring_v1", prepare)
    monkeypatch.setattr(
        inspection, "inspect_ready_legacy_state_live_v1", inspect_live,
    )
    assert provisioner._prepare_transition_legacy_state_v2(
        descriptor, distribution, proof, require_live_ready=True,
    ) is expected
    request = require_canonical_legacy_state_request_v1(observed["request"])
    assert request.distribution_sha256 == D("7")
    assert request.state_root.as_posix() == (
        "/var/lib/metnos-service/.local/state/metnos"
    )
    assert observed["account"].record.uid == 991
    assert observed["proof"] is proof
    assert observed.get("live-inspection") is True


@LINUX_ONLY
def test_transition_birth_mutation_enters_and_restores_signed_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "euid": 0, "egid": 0, "groups": (0,), "umask": 0o022,
    }
    calls = []

    monkeypatch.setattr(provisioner.os, "geteuid", lambda: state["euid"])
    monkeypatch.setattr(provisioner.os, "getegid", lambda: state["egid"])
    monkeypatch.setattr(provisioner.os, "getgroups", lambda: list(state["groups"]))

    def setgroups(values):
        state["groups"] = tuple(values)
        calls.append(("groups", tuple(values)))

    def setegid(value):
        state["egid"] = value
        calls.append(("egid", value))

    def seteuid(value):
        state["euid"] = value
        calls.append(("euid", value))

    def umask(value):
        previous = state["umask"]
        state["umask"] = value
        calls.append(("umask", value))
        return previous

    monkeypatch.setattr(provisioner.os, "setgroups", setgroups)
    monkeypatch.setattr(provisioner.os, "setegid", setegid)
    monkeypatch.setattr(provisioner.os, "seteuid", seteuid)
    monkeypatch.setattr(provisioner.os, "umask", umask)
    descriptor = SimpleNamespace(
        service_uid=41, service_gid=42,
        service_supplementary_gids=(42, 77),
    )

    with provisioner._service_owned_birth_identity_v2(descriptor):
        assert (state["euid"], state["egid"], state["groups"], state["umask"]) == (
            41, 42, (42, 77), 0o077,
        )

    assert (state["euid"], state["egid"], state["groups"], state["umask"]) == (
        0, 0, (0,), 0o022,
    )
    assert calls == [
        ("umask", 0o077), ("groups", (42, 77)), ("egid", 42),
        ("euid", 41), ("euid", 0), ("egid", 0), ("groups", (0,)),
        ("umask", 0o022),
    ]


def test_chain_initialization_loads_trust_as_service_then_creates_as_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_ownership_authorities as authority_module
    import executor_birth_ownership_chain as ownership_chain

    descriptor = object()
    snapshot = object()
    result = object()
    events = []

    @contextmanager
    def service_identity(candidate):
        assert candidate is descriptor
        events.append("service-enter")
        yield
        events.append("service-exit")

    def load_snapshot():
        events.append("snapshot-load")
        return snapshot

    def initialize(cls, candidate):
        assert cls is ownership_chain.OwnershipChainStore
        assert candidate is snapshot
        events.append("root-initialize")
        return result

    monkeypatch.setattr(
        provisioner, "_service_owned_birth_identity_v2", service_identity,
    )
    monkeypatch.setattr(
        authority_module, "_load_fixed_ownership_public_snapshot_v1",
        load_snapshot,
    )
    monkeypatch.setattr(
        ownership_chain.OwnershipChainStore,
        "_initialize_with_fixed_authority_snapshot_v1",
        classmethod(initialize),
    )

    assert provisioner._initialize_transition_ownership_chain_v2(
        descriptor,
    ) is result
    assert events == [
        "service-enter", "snapshot-load", "service-exit", "root-initialize",
    ]


def test_initial_transition_runtime_exposes_only_the_installer_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import executor_birth_bootstrap as bootstrap
    import executor_birth_authority_gate as authority_gate
    import executor_birth_operational as operational
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root
    from executor_birth_intent import BirthIntent, _INSTALLER
    from manifest_inventory import ContractId, ManifestOrigin

    class Initial:
        pass

    state = Initial()
    core = object()
    authority = object()
    request = object()
    result = object()
    intent = BirthIntent(
        candidate_source_root=Path("/candidate"),
        contract_id=ContractId(ManifestOrigin.EXPLICIT, "alpha/manifest.toml"),
        reason="initial convergence",
    )
    assembly = SimpleNamespace(
        core=core, authorities={_INSTALLER: authority}, registry=object(),
        producer_db=Path("/state/producers.sqlite"), ttl_seconds=60,
        now=lambda: None, context_builder=object(),
    )
    observed = {}

    monkeypatch.setattr(
        ownership_chain, "_InitialOwnershipChainStateV1", Initial,
    )
    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: state,
    )
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(bootstrap, "_runtime_bundle_snapshot", lambda: None)
    monkeypatch.setattr(prepared_root, "load_sealed_authorities_v1", object)
    monkeypatch.setattr(
        bootstrap, "_prepare_sealed_birth_assembly_v1", lambda *_args, **_kwargs: assembly,
    )

    def factory(*args):
        observed["factory_args"] = args
        return lambda value: request if value is intent else None

    monkeypatch.setattr(bootstrap, "_request_factory", factory)
    monkeypatch.setattr(operational, "_is_birth_core", lambda value: value is core)
    monkeypatch.setattr(
        operational, "_execute",
        lambda value, selected_core: result
        if value is request and selected_core is core else None,
    )

    runtime = bootstrap._build_initial_transition_installer_runtime_v1()

    assert runtime.submit(intent) is result
    assert observed["factory_args"] == (
        authority, assembly.registry, assembly.producer_db,
        assembly.ttl_seconds, assembly.now, assembly.context_builder,
    )
    assert not hasattr(runtime, "verify_receipt")
    assert not hasattr(runtime, "reattest")


@LINUX_ONLY
@pytest.mark.parametrize(("initial_load", "transition_load"), (
    ("loaded", "masked"),
    ("not-found", "masked"),
    ("loaded", "not-found"),
))
def test_maintenance_session_retains_quiescence_across_named_load_states(
    monkeypatch: pytest.MonkeyPatch, initial_load: str, transition_load: str,
):
    import contract_cutover_guard
    import stack_reconcile
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    state = {"load": initial_load, "active": "inactive"}

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
        state["load"] = transition_load
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
