"""A new instance records absence, never a fabricated predecessor tree."""
import hashlib
import json
import sys

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler


def _arguments():
    return {
        "transaction_id": "sha256:" + "1" * 64,
        "installation_root": None,
        "files": (),
        "service_commands": (),
        "administrative_bundle_hash": "sha256:" + "2" * 64,
        "service_catalog_id": "sha256:" + "3" * 64,
        "service_coverage_hash": "sha256:" + "4" * 64,
    }


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _reidentify(value):
    unsigned = {key: item for key, item in value.items() if key != "predecessor_id"}
    value["predecessor_id"] = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.predecessor-descriptor/v1\0" + _canonical(unsigned),
    ).hexdigest()
    return _canonical(value)


def test_explicit_absence_round_trips_through_both_independent_decoders():
    origin = assembler.build_predecessor_descriptor_v1(**_arguments())
    encoded = assembler.encode_predecessor_descriptor_v1(origin)
    assert assembler.decode_predecessor_descriptor_v1(encoded) == origin
    inspected = preflight._decode_predecessor_descriptor_v1(encoded)
    assert inspected.installation_root is None
    assert inspected.files == inspected.service_commands == ()
    assert inspected.as_value() == json.loads(encoded)


@pytest.mark.parametrize("change", [
    {"installation_root": "/invented/predecessor"},
    {"files": [{"path": "runtime/old.py", "size": 1, "content_hash": "sha256:" + "5" * 64}]},
    {"installation_root": ""},
    {"installation_root": False},
    {"service_commands": [{"entry_id": "invented"}]},
])
def test_absence_cannot_carry_predecessor_artifacts_even_with_correct_digest(change):
    value = json.loads(assembler.encode_predecessor_descriptor_v1(
        assembler.build_predecessor_descriptor_v1(**_arguments()),
    ))
    value.update(change)
    encoded = _reidentify(value)
    for decoder in (
        assembler.decode_predecessor_descriptor_v1,
        preflight._decode_predecessor_descriptor_v1,
    ):
        with pytest.raises(RuntimeError):
            decoder(encoded)


def test_builder_rejects_a_null_root_with_inventory():
    arguments = _arguments()
    arguments["files"] = (
        assembler.PredecessorFileV1("runtime/old.py", 1, "sha256:" + "5" * 64),
    )
    with pytest.raises(assembler.DistributionAssemblerError, match="predecessor artifacts"):
        assembler.build_predecessor_descriptor_v1(**arguments)


def test_origin_is_bound_to_the_transaction_and_catalog():
    origin = assembler.build_predecessor_descriptor_v1(**_arguments())
    for name in ("transaction_id", "service_catalog_id", "service_coverage_hash"):
        arguments = _arguments()
        arguments[name] = "sha256:" + "6" * 64
        changed = assembler.build_predecessor_descriptor_v1(**arguments)
        assert changed.predecessor_id != origin.predecessor_id


def test_independent_maintenance_verifier_accepts_absent_user_manager_only():
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
    from executor_birth_ownership_preflight import (
        canonical_maintenance_proof, maintenance_evidence_hash,
    )

    encoded = canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope, "unit": unit,
            "load_state": "manager-absent" if scope == "user" else "not-found",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )
    assert preflight._maintenance_evidence_hash_v1(encoded) == maintenance_evidence_hash(encoded)
    value = json.loads(encoded)
    next(item for item in value["units"] if item["scope"] == "system")["load_state"] = "manager-absent"
    with pytest.raises(preflight.PreflightError, match="maintenance unit state"):
        preflight._maintenance_evidence_hash_v1(_canonical(value))


@pytest.mark.parametrize("fault", [None, "resume", "not-new", "unsigned", "manifest", "code", "language", "foreign", "unprivileged", "unsealed", "wrong-root"])
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sealed memfd authority handoff")
def test_initial_catalog_handoff_binds_authority_origin_and_exact_source(monkeypatch, tmp_path, fault):
    import fcntl
    import os
    import stat
    from pathlib import Path
    from types import SimpleNamespace
    import config
    import executor_birth_bootstrap as bootstrap
    import executor_birth_distribution_manifest as distribution_manifest
    import manifest_inventory as inventory_module
    from install import birth_authority_provisioner as provisioner
    from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus, ManifestInventory

    source_root = tmp_path / "release"
    candidate = source_root / "executors" / "sample"
    candidate.mkdir(parents=True)
    payloads = {
        "manifest.toml": b'name="sample"\n[code]\nfiles=["sample.py"]\ndigest="sha256:' + b"0" * 64 + b'"\n',
        "sample.py": b"pass\n", "manifest.lang_state.json": b"{}",
    }
    entries = []
    for name, data in payloads.items():
        (candidate / name).write_bytes(data)
        path = "executors/sample/" + name
        entries.append(assembler.ReceivedSourceFileV1(
            path, len(data), assembler.received_source_file_hash_v1(path, len(data), (data,)), 0o644,
        ))
    origin = ManifestOrigin.USER if fault == "foreign" else ManifestOrigin.BUILTIN
    ref = ManifestRef(ContractId(origin, "sample/manifest.toml"), origin, ManifestStatus.ADMITTED,
                      source_root / "executors", candidate / "manifest.toml", "sample/manifest.toml", (candidate,))
    monkeypatch.setattr(inventory_module, "inventory_authoring_manifests", lambda: ManifestInventory((ref,), ()))
    distribution = SimpleNamespace(release_sequence=1, previous_closed_build_id=None,
                                   identity=SimpleNamespace(closed_build_id="sha256:" + "1" * 64))
    monkeypatch.setattr(distribution_manifest, "is_verified_distribution", lambda value: value is distribution and fault != "unsigned")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    descriptor = SimpleNamespace(installation_root=str(source_root))
    source = assembler.ReceivedSourceV1("sha256:" + "2" * 64, "metnos", tuple(entries))
    state = SimpleNamespace(initially_empty=fault != "not-new", record_sha256="sha256:" + "3" * 64)
    if fault in {"manifest", "code", "language"}:
        name = {"manifest": "manifest.toml", "code": "sample.py", "language": "manifest.lang_state.json"}[fault]
        with (candidate / name).open("ab") as file:
            file.write(b"\n")
    parent_failures = {"not-new", "unsigned", "manifest", "code", "language", "foreign"}
    if fault in parent_failures:
        with pytest.raises(provisioner.BirthProvisioningError):
            with provisioner._initial_catalog_adoption_input_v1(descriptor, distribution, source, state):
                pytest.fail("unaccepted source or false origin reached the child")
        return

    with provisioner._initial_catalog_adoption_input_v1(descriptor, distribution, source, state) as fd:
        raw = os.pread(fd, 1024 * 1024, 0)
        with pytest.raises(OSError):
            os.write(fd, b"changed")
        metadata = os.fstat(fd)
        real_fstat, real_pread, real_fcntl = os.fstat, os.pread, fcntl.fcntl
        # Portable tests cannot become root. The native check covers ownership;
        # here the real sealed descriptor and all other kernel facts are used.
        metadata = SimpleNamespace(st_mode=metadata.st_mode, st_uid=1000 if fault == "unprivileged" else 0,
                                   st_nlink=metadata.st_nlink, st_size=metadata.st_size)
        monkeypatch.setattr(os, "fstat", lambda number: metadata if number == 0 else real_fstat(number))
        monkeypatch.setattr(os, "pread", lambda number, count, offset: real_pread(fd if number == 0 else number, count, offset))
        monkeypatch.setattr(fcntl, "fcntl", lambda number, op, *args: (
            0 if fault == "unsealed" and number == 0 else real_fcntl(fd if number == 0 else number, op, *args)))
        monkeypatch.setattr(config, "PATH_ROOT", source_root / "wrong" if fault == "wrong-root" else source_root)
        sealed = SimpleNamespace(prepared=SimpleNamespace(prepared_admission_context_id="sha256:" + "4" * 64))
        if fault in {"unprivileged", "unsealed", "wrong-root"}:
            with pytest.raises(bootstrap.BirthBootstrapError, match="initial_catalog_adoption_invalid"):
                bootstrap._read_initial_catalog_adoption_v1(sealed)
        else:
            adopted = bootstrap._read_initial_catalog_adoption_v1(sealed)
            assert tuple(adopted.candidates) == (ref.contract_id.value,)
            assert adopted.evidence_id == "sha256:" + hashlib.sha256(raw).hexdigest()
            if fault == "resume":
                assert bootstrap._read_initial_catalog_adoption_v1(sealed) == adopted
