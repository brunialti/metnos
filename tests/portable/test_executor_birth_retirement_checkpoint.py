"""Completed retirement evidence is reusable; live authority never is."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import executor_birth_admin_preflight as admin
from executor_birth_distribution_manifest import DistributionFile, file_content_hash
from executor_birth_dominant_startup import (
    DominantStartupBindingsV1, bindings_digest_v1, dominant_startup_receipt_v1,
)
from executor_birth_legacy_retirement import plan_catalog_retirement_v1, plan_digest_v1
from install import birth_authority_provisioner as provisioner
from test_executor_birth_legacy_retirement import _product_catalog


def D(character):
    return "sha256:" + character * 64


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    """Mock only the authentication owner; use real catalog and receipt codecs."""
    catalog = _product_catalog()
    previous = NS(catalog=catalog, unit_fragments=(("sample.service", b"signed"),))
    relative = "runtime/executor_birth_authority_gate.py"
    gate = tmp_path / "release" / relative
    gate.parent.mkdir(parents=True)
    payload = b"\ndef closed_build_enforcement() -> bool:\n    return True\n"
    gate.write_bytes(payload)
    distribution = NS(
        facts=NS(installation_root=str(tmp_path / "release")),
        files=(DistributionFile(relative, len(payload), file_content_hash(relative, payload),
                                "runtime_code"),),
    )
    record = NS(
        sequence=6, state="PREFLIGHT_VERIFIED", closed_build_id=D("a"),
        request_id=D("1"), previous_head_id=D("2"), previous_set_id="b" * 64,
        context_transition_id=D("3"), catalog_id=D("4"),
    )
    materials = NS(
        transaction=record, catalog=catalog, unit_fragments=previous.unit_fragments,
        distribution=distribution, prerequisite=NS(effective_units_hash=D("5")),
        descriptor=object(),
    )
    enforcement = provisioner._observe_bound_enforcement_v2(NS(materials=materials))
    bindings = DominantStartupBindingsV1(
        record.request_id, record.previous_head_id, record.context_transition_id,
        record.catalog_id, materials.prerequisite.effective_units_hash, enforcement,
    )
    record.dominant_startup_receipt = dominant_startup_receipt_v1(
        bindings_digest_v1(bindings),
        plan_digest_v1(plan_catalog_retirement_v1(catalog).steps), enforcement,
    )
    # The historical checkout deliberately does not exist.
    selected = NS(predecessor=NS(installation_root=str(tmp_path / "old-checkout"), files=()))
    authenticated = NS(snapshot=selected)
    monkeypatch.setattr(admin, "_authenticate_fixed_ownership_snapshot_v1", lambda: authenticated)
    monkeypatch.setattr(admin, "_select_ownership_epoch_v1", lambda snapshot: snapshot)
    def load(value, *, review_sources):
        assert value is authenticated and review_sources is False
        return selected, materials
    monkeypatch.setattr(admin, "_load_installed_preflight_materials_v1", load)
    return NS(previous=previous, selected=selected, materials=materials,
              record=record, gate=gate, authenticated=authenticated)


def test_completed_receipt_reuses_exact_authenticated_material(checkpoint):
    observed = provisioner._verify_completed_retirement_v2(checkpoint.previous, D("a"))
    assert observed == (checkpoint.selected, checkpoint.materials)
    assert not Path(checkpoint.selected.predecessor.installation_root).exists()


@pytest.mark.parametrize("field", (
    "request_id", "previous_head_id", "context_transition_id", "catalog_id",
    "dominant_startup_receipt", "closed_build_id",
))
def test_changed_checkpoint_binding_refuses(checkpoint, field):
    setattr(checkpoint.record, field, D("e"))
    with pytest.raises(provisioner.BirthProvisioningError, match="retirement_checkpoint_invalid"):
        provisioner._verify_completed_retirement_v2(checkpoint.previous, D("a"))


@pytest.mark.parametrize("sequence,state", (
    (0, "RECEIPTS_COMPLETE"), (5, "HEAD_REQUIRED"), (6, "ABANDONED"),
))
def test_unfinished_or_abandoned_checkpoint_refuses(checkpoint, sequence, state):
    checkpoint.record.sequence, checkpoint.record.state = sequence, state
    with pytest.raises(provisioner.BirthProvisioningError, match="retirement_checkpoint_invalid"):
        provisioner._verify_completed_retirement_v2(checkpoint.previous, D("a"))


@pytest.mark.parametrize("change", ("catalog", "fragments", "topology", "enforcement", "plan"))
def test_changed_retirement_material_refuses(checkpoint, change):
    if change == "catalog":
        checkpoint.previous.catalog = replace(checkpoint.previous.catalog, catalog_id=D("e"))
    elif change == "fragments":
        checkpoint.previous.unit_fragments = (("sample.service", b"different"),)
    elif change == "topology":
        checkpoint.materials.prerequisite.effective_units_hash = D("e")
    elif change == "enforcement":
        checkpoint.gate.write_bytes(checkpoint.gate.read_bytes() + b"\n")
    else:
        original = checkpoint.previous.catalog
        checkpoint.previous.catalog = replace(original, legacy_bindings=original.legacy_bindings[:-1])
    with pytest.raises(provisioner.BirthProvisioningError):
        provisioner._verify_completed_retirement_v2(checkpoint.previous, D("a"))


def test_authentication_refusal_is_not_replaced_with_a_checkpoint(checkpoint, monkeypatch):
    def refuse():
        raise admin._invalid("test invalid signature")
    monkeypatch.setattr(admin, "_authenticate_fixed_ownership_snapshot_v1", refuse)
    with pytest.raises(admin.PreflightError, match="test invalid signature"):
        provisioner._verify_completed_retirement_v2(checkpoint.previous, D("a"))


@pytest.mark.parametrize("drift", (False, True))
def test_successor_does_not_open_unchanged_repository(checkpoint, monkeypatch, drift):
    import executor_birth_legacy_neutralizer as neutralizer

    observed = []
    def roots(prepared, identity, *, include_repository):
        assert include_repository is False
        assert prepared.materials.predecessor is checkpoint.selected.predecessor
        return {"system": Path("/test/system"), "user": Path("/test/user")}
    monkeypatch.setattr(provisioner, "_transition_roots_v2", roots)
    def processes(root, locators, *, historical):
        assert historical is True
        assert root == Path(checkpoint.selected.predecessor.installation_root)
        assert locators
        return False
    monkeypatch.setattr(provisioner, "_process_tree_references_entries_v2", processes)
    def observe(root, steps, **kwargs):
        assert all(step.scope != "repository" for step in steps)
        observed.append(root)
    monkeypatch.setattr(neutralizer, "_observe_retired_core_v1", observe)
    if drift:
        monkeypatch.setattr(admin, "_select_ownership_epoch_v1", lambda _: object())
        with pytest.raises(provisioner.BirthProvisioningError, match="checkpoint_changed"):
            provisioner._observe_successor_retirement_v2(
                checkpoint.previous, checkpoint.previous, object(), expected_previous_build_id=D("a"))
    else:
        expected = plan_digest_v1(plan_catalog_retirement_v1(checkpoint.previous.catalog).steps)
        for _ in range(2):
            assert provisioner._observe_successor_retirement_v2(
                checkpoint.previous, checkpoint.previous, object(),
                expected_previous_build_id=D("a")) == expected
    assert set(observed) == {Path("/test/system"), Path("/test/user")}


def test_successor_checks_for_conflicting_legacy_processes(checkpoint, monkeypatch):
    monkeypatch.setattr(provisioner, "_transition_roots_v2", lambda *_a, **_k: {})
    monkeypatch.setattr(provisioner, "_process_tree_references_entries_v2", lambda *_a, **_k: True)
    with pytest.raises(provisioner.BirthProvisioningError, match="repository_in_use"):
        provisioner._observe_successor_retirement_v2(
            checkpoint.previous, checkpoint.previous, object(), expected_previous_build_id=D("a"))


@pytest.mark.skipif(os.name == "nt", reason="Linux process observation adapter")
@pytest.mark.parametrize("running", (False, True))
def test_historical_process_observer_needs_no_old_directory(tmp_path, running):
    old = tmp_path / "removed-checkout"
    process = tmp_path / "proc" / "42"
    (process / "fd").mkdir(parents=True)
    (process / "cwd").symlink_to(tmp_path)
    (process / "exe").symlink_to("/usr/bin/python3")
    (process / "maps").write_text("", encoding="utf-8")
    script = str(old / "install" / "old.py") if running else "unrelated.py"
    (process / "cmdline").write_bytes(b"python3\0" + script.encode() + b"\0")
    assert provisioner._process_tree_references_entries_v2(
        old, ("install/old.py",), proc_root=tmp_path / "proc", historical=True,
    ) is running
