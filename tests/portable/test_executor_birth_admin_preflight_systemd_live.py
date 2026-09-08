"""Probative tests for the non-authorizing live systemd observation."""
from __future__ import annotations

import os
import subprocess
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_preflight_attestation_store as attestation_store
import executor_birth_preflight_store_authority as store_authority
import executor_birth_distribution_assembler as assembler
import executor_birth_service_catalog as service_catalog


LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="requires Linux no-follow filesystem APIs",
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _assert_invalid(call, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        call(*args, **kwargs)
    assert failure.value.code == preflight.CODE_INVALID
    return failure.value


def _harden_directories(root: Path) -> None:
    for directory, names, _files in os.walk(root):
        Path(directory).chmod(0o755 if Path(directory) != root else 0o700)
        for name in names:
            (Path(directory) / name).chmod(0o755)


def _target_materials(
    *, systemctl_executable: str = "/usr/bin/systemctl",
) -> tuple[preflight._BoundPreflightMaterialsV1, bytes]:
    unit_name = "probe.target"
    directives = (
        preflight._ServiceDirectiveV1(
            "Unit", "Description", "scalar", ("Probe target",),
        ),
        preflight._ServiceDirectiveV1(
            "Install", "WantedBy", "unit_list", ("default.target",),
        ),
    )
    fragment = preflight._render_service_directives_v1(directives)
    fragment_hash = preflight._service_fragment_hash_v1(unit_name, fragment)
    spec = preflight._ServiceUnitSpecV1(fragment_hash, directives)
    entry = preflight._ServiceCatalogEntryV1(
        "probe-target", unit_name, None, None, "target", "system", "none",
        None, None, None, (), None, (), None, spec, True, False,
    )
    link = preflight._EnablementLinkV1(
        "/etc/systemd/system/default.target.wants/probe.target",
        "../probe.target",
    )
    candidate = preflight._CandidateUnitV1(
        entry.entry_id, unit_name, fragment_hash, directives, (link,),
    )
    candidate_snapshot = preflight._CandidateUnitsSnapshotV1(
        (candidate,), b"candidate", D("a"),
    )
    materials = preflight._BoundPreflightMaterialsV1(
        SimpleNamespace(), SimpleNamespace(),
        SimpleNamespace(
            entries=(entry,), catalog_id=D("a"), service_coverage_hash=D("b"),
        ),
        SimpleNamespace(
            systemctl_executable=systemctl_executable, descriptor_id=D("3"),
        ),
        SimpleNamespace(), candidate_snapshot, ((unit_name, fragment),),
        D("b"), D("c"),
    )
    return materials, fragment


def _target_manager_observation(
    materials: preflight._BoundPreflightMaterialsV1,
) -> dict[str, tuple[str, ...]]:
    entry = materials.catalog.entries[0]
    plan = preflight._systemd_property_plan_v1(entry)
    values = {name: ("",) for name in plan.requested_properties}
    values.update({
        "FragmentPath": ("/etc/systemd/system/probe.target",),
        "LoadState": ("loaded",),
        "UnitFileState": ("enabled",),
        "NeedDaemonReload": ("no",),
        "Description": ("Probe target",),
        "Documentation": ("",),
        "DefaultDependencies": ("yes",),
        "StartLimitIntervalUSec": ("10s",),
        "StartLimitBurst": ("5",),
    })
    return values


def _with_effective_prerequisite(
    materials: preflight._BoundPreflightMaterialsV1,
    effective_hash: str,
) -> tuple[preflight._BoundPreflightMaterialsV1, bytes]:
    record = assembler.build_startup_prerequisite_v1(
        request_id=D("1"), closed_build_id=D("2"), release_sequence=1,
        deployment_descriptor_id=D("3"), predecessor_id=D("4"),
        administrative_bundle_hash=materials.administrative_bundle_hash,
        python_binary_hash=D("5"), openssl_binary_hash=D("6"),
        openssl_tcb_hash=D("7"), systemctl_binary_hash=D("8"),
        systemd_analyze_binary_hash=D("9"), service_catalog_id=D("a"),
        service_coverage_hash=D("b"),
        systemd_manager_version="255.4-1ubuntu8.17",
        candidate_units_hash=materials.candidate_units.candidate_units_hash,
        effective_units_hash=effective_hash,
    )
    encoded = assembler.encode_startup_prerequisite_v1(record)
    decoded = preflight._decode_startup_prerequisite_v1(encoded)
    transaction = SimpleNamespace(
        request_id=decoded.request_id,
        startup_prerequisite_id=decoded.prerequisite_id,
        startup_prerequisite_digest=preflight._startup_prerequisite_digest_v1(
            encoded,
        ),
        required_head_frame_hash=D("c"),
    )
    return materials._replace(
        prerequisite=decoded, transaction=transaction,
    ), encoded


def _operational_attestation_fixture(
    materials: preflight._BoundPreflightMaterialsV1,
) -> preflight._OperationalPreflightForTestV1:
    latest = materials.transaction
    selected = preflight._SelectedOwnershipEpochV1(
        (SimpleNamespace(), SimpleNamespace(), SimpleNamespace()), None,
        SimpleNamespace(
            closed_build_id=materials.prerequisite.closed_build_id,
            release_sequence=materials.prerequisite.release_sequence,
            head_id=D("d"),
        ),
        materials.distribution,
        SimpleNamespace(prefix=SimpleNamespace(records=(latest,))),
        SimpleNamespace(
            predecessor_id=materials.prerequisite.predecessor_id,
        ),
    )
    administrative = preflight._ObservedAdministrativeTcbV1(
        materials, SimpleNamespace(), (),
    )
    observed = preflight._ObservedEffectiveSystemdV1(
        administrative, SimpleNamespace(),
        SimpleNamespace(snapshot=SimpleNamespace(
            effective_units_hash=materials.prerequisite.effective_units_hash,
        )),
    )
    return preflight._OperationalPreflightForTestV1(
        selected, preflight._ObservedEffectiveSystemdForTestV1(observed),
    )


def _publish_for_test(operational, root: Path) -> bytes:
    encoded = preflight._preflight_attestation_bytes_v1(
        operational.selected, operational.observation.observation,
    )
    request_id = operational.selected.transaction.prefix.records[-1].request_id
    return attestation_store._publish_preflight_attestation_for_test_v1(
        encoded, request_id, root,
    )


@LINUX_ONLY
def test_attestation_store_publication_is_exact_idempotent_and_no_replace(
    tmp_path: Path,
) -> None:
    materials, _fragment = _target_materials()
    materials, _encoded = _with_effective_prerequisite(materials, D("e"))
    operational = _operational_attestation_fixture(materials)
    root = tmp_path / "attestations"
    root.mkdir(mode=0o755)

    encoded = _publish_for_test(
        operational, root,
    )
    value = preflight.decode_canonical_json_v1(
        encoded, preflight.MAX_PREFLIGHT_ATTESTATION_BYTES_V1,
    )
    assert set(value) == {
        "schema_version", "attestation_id", "request_id",
        "closed_build_id", "release_sequence", "head_id",
        "required_head_frame_hash", "deployment_descriptor_id",
        "service_catalog_id", "service_coverage_hash",
        "candidate_units_hash", "administrative_bundle_hash",
        "python_binary_hash", "openssl_binary_hash", "openssl_tcb_hash",
        "systemctl_binary_hash", "systemd_analyze_binary_hash",
        "effective_units_hash", "checked_entry_ids",
    }
    assert value["checked_entry_ids"] == ["probe-target"]
    unsigned = dict(value)
    unsigned.pop("attestation_id")
    assert value["attestation_id"] == preflight._digest(
        preflight.PREFLIGHT_ATTESTATION_DOMAIN_V1,
        preflight._canonical_json(unsigned),
    )
    decoded = preflight._decode_preflight_attestation_v1(encoded)
    assert decoded.request_id == materials.prerequisite.request_id
    assert preflight._preflight_attestation_record_hash_v1(encoded) == (
        preflight._digest(
            preflight.PREFLIGHT_ATTESTATION_RECORD_DOMAIN_V1, encoded,
        )
    )
    destination = root / f"{materials.prerequisite.request_id}.json"
    assert destination.read_bytes() == encoded
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    assert destination.stat().st_nlink == 1
    assert _publish_for_test(
        operational, root,
    ) == encoded

    destination.write_bytes(encoded + b" ")
    with pytest.raises(preflight.PreflightError) as conflict:
        _publish_for_test(
            operational, root,
        )
    assert conflict.value.code == preflight.CODE_RECOVERY

    for mutation in (
        {**value, "unexpected": None},
        {**value, "attestation_id": D("f")},
        {**value, "checked_entry_ids": ["probe-target", "probe-target"]},
    ):
        _assert_invalid(
            preflight._decode_preflight_attestation_v1,
            preflight._canonical_json(mutation),
        )


@LINUX_ONLY
def test_attestation_store_retains_partial_state_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    materials, _fragment = _target_materials()
    materials, _encoded = _with_effective_prerequisite(materials, D("e"))
    operational = _operational_attestation_fixture(materials)
    root = tmp_path / "attestations"
    root.mkdir(mode=0o755)

    def fail_at_promotion(_self, _temporary, _basename):
        raise preflight._recovery("killpoint")

    monkeypatch.setattr(
        store_authority._StoreMutationPortV1,
        "link_staging",
        fail_at_promotion,
    )

    with pytest.raises(preflight.PreflightError) as failure:
        _publish_for_test(
            operational, root,
        )
    assert failure.value.code == preflight.CODE_RECOVERY
    assert tuple(path.name for path in root.iterdir()) == (
        "." + materials.prerequisite.request_id.removeprefix("sha256:") + ".tmp",
    )


def _install_prerequisite(
    ownership_root: Path, materials: preflight._BoundPreflightMaterialsV1,
    encoded: bytes,
) -> Path:
    directory = ownership_root / "startup-prerequisites-v1"
    directory.mkdir(parents=True)
    path = directory / f"{materials.prerequisite.request_id}.json"
    path.write_bytes(encoded)
    path.chmod(0o644)
    _harden_directories(ownership_root)
    return path


def test_systemctl_show_requires_zero_exit_empty_stderr_and_exact_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(argv):
        calls.append(argv)
        return 0, b"Version=255.4-1ubuntu8.17\n", b""

    monkeypatch.setattr(preflight, "_run_systemctl_bounded_v1", run)
    observed = preflight._run_systemctl_show_v1(
        "/usr/bin/systemctl", None, ("Version",),
    )
    assert observed == {"Version": ("255.4-1ubuntu8.17",)}
    assert calls == [(
        "/usr/bin/systemctl", "--no-pager", "--plain", "--all", "show",
        "--property=Version",
    )]

    monkeypatch.setattr(
        preflight, "_run_systemctl_bounded_v1",
        lambda _argv: (0, b"Version=255.4-1ubuntu8.17\n", b"warning\n"),
    )
    assert _assert_invalid(
        preflight._run_systemctl_show_v1,
        "/usr/bin/systemctl", None, ("Version",),
    ).detail == "systemctl show command"


@LINUX_ONLY
def test_systemctl_runner_uses_closed_process_policy_and_stream_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_popen = preflight.subprocess.Popen
    observed = []

    def popen(*args, **kwargs):
        observed.append((args, kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(preflight.subprocess, "Popen", popen)
    result = preflight._run_systemctl_bounded_v1((
        "/usr/bin/printf", "systemd-ok\\n",
    ))
    assert result == (0, b"systemd-ok\n", b"")
    assert len(observed) == 1
    _args, policy = observed[0]
    assert policy == {
        "stdin": preflight.subprocess.DEVNULL,
        "stdout": preflight.subprocess.PIPE,
        "stderr": preflight.subprocess.PIPE,
        "env": {"LC_ALL": "C"},
        "shell": False,
        "close_fds": True,
    }

    monkeypatch.setattr(preflight, "MAX_SYSTEMCTL_STDOUT_BYTES_V1", 2)
    assert _assert_invalid(
        preflight._run_systemctl_bounded_v1,
        ("/usr/bin/printf", "abc"),
    ).detail == "systemctl output bound"


@LINUX_ONLY
def test_complete_target_snapshot_binds_manager_fragment_and_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "root"
    unit_root = live_root / "etc" / "systemd" / "system"
    wants = unit_root / "default.target.wants"
    wants.mkdir(parents=True)
    live_root.chmod(0o700)
    materials, fragment = _target_materials()
    fragment_path = unit_root / "probe.target"
    fragment_path.write_bytes(fragment)
    fragment_path.chmod(0o644)
    (wants / "probe.target").symlink_to("../probe.target")
    _harden_directories(live_root)
    manager = _target_manager_observation(materials)
    calls = []

    def show(executable, unit_name, properties):
        calls.append((executable, unit_name, properties))
        if unit_name is None:
            return {"Version": ("255.4-1ubuntu8.17",)}
        assert unit_name == "probe.target"
        assert properties == preflight._systemd_property_plan_v1(
            materials.catalog.entries[0],
        ).requested_properties
        return manager

    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", show)
    captured = preflight._capture_effective_systemd_units_core_v1(
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )
    assert captured.manager_version == "255.4-1ubuntu8.17"
    assert len(captured.snapshot.entries) == 1
    unit = captured.snapshot.entries[0]
    assert unit.entry_id == "probe-target"
    assert unit.fragment_path == "/etc/systemd/system/probe.target"
    assert unit.fragment_hash == materials.candidate_units.entries[0].fragment_hash
    assert unit.dropins == ()
    assert unit.enablement_links == materials.candidate_units.entries[0].enablement_links
    assert unit.manager_added_edges == ()
    assert stat.S_IMODE(unit.fragment_mode) == 0o644
    assert tuple(item.logical_path for item in captured.files) == (
        "/etc/systemd/system/probe.target",
    )
    assert tuple(item.logical_path for item in captured.links) == (
        "/etc/systemd/system/default.target.wants/probe.target",
    )
    assert len(calls) == 2
    preflight._revalidate_captured_effective_systemd_v1(
        captured, live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )


@LINUX_ONLY
def test_snapshot_denies_foreign_dropin_and_fragment_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "root"
    unit_root = live_root / "etc" / "systemd" / "system"
    wants = unit_root / "default.target.wants"
    wants.mkdir(parents=True)
    live_root.chmod(0o700)
    materials, fragment = _target_materials()
    fragment_path = unit_root / "probe.target"
    fragment_path.write_bytes(fragment)
    fragment_path.chmod(0o644)
    (wants / "probe.target").symlink_to("../probe.target")
    _harden_directories(live_root)
    manager = _target_manager_observation(materials)
    manager["DropInPaths"] = ("/etc/systemd/system/probe.target.d/x.conf",)
    monkeypatch.setattr(
        preflight, "_run_systemctl_show_v1",
        lambda _executable, unit_name, _properties: (
            {"Version": ("255.4-1ubuntu8.17",)}
            if unit_name is None else manager
        ),
    )
    assert _assert_invalid(
        preflight._capture_effective_systemd_units_core_v1,
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    ).detail == "effective systemd base properties"

    manager["DropInPaths"] = ("",)
    captured = preflight._capture_effective_systemd_units_core_v1(
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )
    fragment_path.write_bytes(fragment + b"\n")
    assert _assert_invalid(
        preflight._revalidate_captured_effective_systemd_v1,
        captured, live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    ).detail == "effective systemd file changed"


@LINUX_ONLY
def test_added_edge_origins_cover_root_generator_and_manager_virtual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "root"
    fragment = live_root / "usr" / "lib" / "systemd" / "system" / "a.service"
    generator = live_root / "run" / "systemd" / "generator" / "-.mount"
    source = (
        live_root / "usr" / "lib" / "systemd" / "system-generators"
        / "fstab-generator"
    )
    for path, content in (
        (fragment, b"[Unit]\nDescription=A\n"),
        (generator, b"[Mount]\nWhat=rootfs\n"),
        (source, b"generator-binary"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)
    _harden_directories(live_root)
    observations = {
        "a.service": {
            "FragmentPath": ("/usr/lib/systemd/system/a.service",),
            "Id": ("a.service",), "LoadState": ("loaded",),
            "SourcePath": ("",), "Transient": ("no",),
            "UnitFileState": ("static",),
        },
        "-.mount": {
            "FragmentPath": ("/run/systemd/generator/-.mount",),
            "Id": ("-.mount",), "LoadState": ("loaded",),
            "SourcePath": (
                "/usr/lib/systemd/system-generators/fstab-generator",
            ),
            "Transient": ("no",), "UnitFileState": ("generated",),
        },
        "system.slice": {
            "FragmentPath": ("",), "Id": ("system.slice",),
            "LoadState": ("loaded",), "SourcePath": ("",),
            "Transient": ("no",), "UnitFileState": ("",),
        },
    }
    monkeypatch.setattr(
        preflight, "_run_systemctl_show_v1",
        lambda _executable, unit_name, properties: (
            observations[unit_name]
            if properties == preflight._SYSTEMD_ORIGIN_PROPERTIES_V1
            else pytest.fail("unexpected property plan")
        ),
    )
    files = {}

    def capture_file(logical_path, maximum):
        captured = preflight._capture_exact_systemd_file_v1(
            logical_path, live_root=live_root, uid=os.getuid(), gid=os.getgid(),
            maximum=maximum,
        )
        files[logical_path] = captured
        return captured

    root = preflight._capture_systemd_origin_v1(
        "a.service", systemctl_executable="/usr/bin/systemctl",
        capture_file=capture_file,
    )
    generated = preflight._capture_systemd_origin_v1(
        "-.mount", systemctl_executable="/usr/bin/systemctl",
        capture_file=capture_file,
    )
    virtual = preflight._capture_systemd_origin_v1(
        "system.slice", systemctl_executable="/usr/bin/systemctl",
        capture_file=capture_file,
    )
    assert root.origin_kind == "root_fragment"
    assert root.source_path is None
    assert generated.origin_kind == "root_generator"
    assert generated.source_path == (
        "/usr/lib/systemd/system-generators/fstab-generator"
    )
    assert generated.source_content_hash == preflight._systemd_origin_source_hash_v1(
        generated.source_path, source.read_bytes(),
    )
    assert virtual.origin_kind == "manager_virtual"
    assert virtual.fragment_path is None
    assert set(files) == {
        "/usr/lib/systemd/system/a.service",
        "/run/systemd/generator/-.mount",
        "/usr/lib/systemd/system-generators/fstab-generator",
    }


def test_origin_denies_transient_or_unclassified_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = {
        "FragmentPath": ("",), "Id": ("init.scope",),
        "LoadState": ("loaded",), "SourcePath": ("",),
        "Transient": ("yes",), "UnitFileState": ("transient",),
    }
    monkeypatch.setattr(
        preflight, "_run_systemctl_show_v1",
        lambda *_args, **_kwargs: observed,
    )
    assert _assert_invalid(
        preflight._capture_systemd_origin_v1,
        "init.scope", systemctl_executable="/usr/bin/systemctl",
        capture_file=lambda *_args: pytest.fail("must not read files"),
    ).detail.startswith("systemd origin identity")


@LINUX_ONLY
def test_double_observation_runs_exact_sequence_and_returns_test_only_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "live"
    unit_root = live_root / "etc" / "systemd" / "system"
    wants = unit_root / "default.target.wants"
    wants.mkdir(parents=True)
    materials, fragment = _target_materials()
    (unit_root / "probe.target").write_bytes(fragment)
    (unit_root / "probe.target").chmod(0o644)
    (wants / "probe.target").symlink_to("../probe.target")
    _harden_directories(live_root)
    manager = _target_manager_observation(materials)

    def show(_executable, unit_name, _properties):
        return (
            {"Version": ("255.4-1ubuntu8.17",)}
            if unit_name is None else manager
        )

    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", show)
    first = preflight._capture_effective_systemd_units_core_v1(
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )
    materials, encoded = _with_effective_prerequisite(
        materials, first.snapshot.effective_units_hash,
    )
    ownership_root = tmp_path / "ownership"
    _install_prerequisite(ownership_root, materials, encoded)
    capture = SimpleNamespace(executables=SimpleNamespace(
        systemctl=SimpleNamespace(resolved=SimpleNamespace(
            canonical_path="/usr/bin/systemctl",
        )),
    ))
    administrative = preflight._ObservedAdministrativeTcbForTestV1(
        preflight._ObservedAdministrativeTcbV1(materials, capture, ()),
    )
    monkeypatch.setattr(
        preflight, "_revalidate_captured_administrative_tcb_v1",
        lambda *_args, **_kwargs: None,
    )
    killpoints = []
    result = preflight._observe_effective_systemd_for_test_v1(
        administrative, ownership_root=ownership_root, live_root=live_root,
        administrative_links=tuple(
            tmp_path / "admin" / name
            for name in ("python", "openssl", "systemctl", "analyze")
        ),
        administrative_root=tmp_path / "admin",
        between_for_test=lambda: killpoints.append("between-S0-P1"),
    )
    assert type(result) is preflight._ObservedEffectiveSystemdForTestV1
    assert result.observation.effective_systemd.snapshot == first.snapshot
    assert result.observation.prerequisite.content == encoded
    assert killpoints == ["between-S0-P1"]


@LINUX_ONLY
def test_double_observation_denies_prerequisite_or_manager_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = tmp_path / "live"
    unit_root = live_root / "etc" / "systemd" / "system"
    wants = unit_root / "default.target.wants"
    wants.mkdir(parents=True)
    materials, fragment = _target_materials()
    (unit_root / "probe.target").write_bytes(fragment)
    (unit_root / "probe.target").chmod(0o644)
    (wants / "probe.target").symlink_to("../probe.target")
    _harden_directories(live_root)
    manager = _target_manager_observation(materials)
    monkeypatch.setattr(
        preflight, "_run_systemctl_show_v1",
        lambda _executable, unit_name, _properties: (
            {"Version": ("255.4-1ubuntu8.17",)}
            if unit_name is None else manager
        ),
    )
    first = preflight._capture_effective_systemd_units_core_v1(
        materials, systemctl_executable="/usr/bin/systemctl",
        live_root=live_root, uid=os.getuid(), gid=os.getgid(),
    )
    materials, encoded = _with_effective_prerequisite(
        materials, first.snapshot.effective_units_hash,
    )
    ownership_root = tmp_path / "ownership"
    prerequisite_path = _install_prerequisite(
        ownership_root, materials, encoded,
    )
    administrative = preflight._ObservedAdministrativeTcbForTestV1(
        preflight._ObservedAdministrativeTcbV1(
            materials,
            SimpleNamespace(executables=SimpleNamespace(
                systemctl=SimpleNamespace(resolved=SimpleNamespace(
                    canonical_path="/usr/bin/systemctl",
                )),
            )),
            (),
        ),
    )
    monkeypatch.setattr(
        preflight, "_revalidate_captured_administrative_tcb_v1",
        lambda *_args, **_kwargs: None,
    )
    common = dict(
        ownership_root=ownership_root, live_root=live_root,
        administrative_links=tuple(
            tmp_path / "admin" / name
            for name in ("python", "openssl", "systemctl", "analyze")
        ),
        administrative_root=tmp_path / "admin",
    )

    def mutate_prerequisite():
        prerequisite_path.write_bytes(encoded + b" ")

    assert _assert_invalid(
        preflight._observe_effective_systemd_for_test_v1,
        administrative, **common, between_for_test=mutate_prerequisite,
    ).detail == "startup prerequisite capture"

    prerequisite_path.write_bytes(encoded)
    prerequisite_path.chmod(0o644)
    calls = 0

    def drifting_show(_executable, unit_name, _properties):
        nonlocal calls
        if unit_name is None:
            return {"Version": ("255.4-1ubuntu8.17",)}
        calls += 1
        observed = dict(manager)
        observed["UnitFileState"] = (("enabled" if calls == 1 else "static"),)
        return observed

    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", drifting_show)
    assert _assert_invalid(
        preflight._observe_effective_systemd_for_test_v1,
        administrative, **common,
    ).detail == "effective systemd A/B mismatch"


def _timer_materials(
    *, watchdog: str | None = None, explicit_timer_after: bool = False,
) -> preflight._BoundPreflightMaterialsV1:
    """Decode a real catalog; only the systemctl/TCB envelope is synthetic."""
    entries = []
    for label in ("other", "probe"):
        entry_id, unit_name = f"service-{label}", f"{label}.service"
        commands = tuple(service_catalog.ServiceDirectiveV1(
            "Service", name, "argv", (
                "!/usr/bin/python3.12", "-I", "-S",
                preflight.ADMINISTRATIVE_ADAPTER_PATH_V1,
                operation, "--entry-id", entry_id,
            ),
        ) for name, operation in (("ExecStart", "launch"), ("ExecStartPre", "check")))
        directives = (
            service_catalog.ServiceDirectiveV1("Unit", "Description", "scalar", (label,)),
            service_catalog.ServiceDirectiveV1(
                "Unit", "After", "unit_list",
                ("basic.target", "probe.timer")
                if label == "probe" and explicit_timer_after
                else ("basic.target",),
            ),
            service_catalog.ServiceDirectiveV1("Unit", "Requires", "unit_list", ("basic.target",)),
            *commands,
            *(service_catalog.ServiceDirectiveV1("Service", name, kind, (value,))
              for name, kind, value in (
                  ("CapabilityBoundingSet", "scalar", "CAP_SETGID CAP_SETPCAP CAP_SETUID"),
                  ("Group", "scalar", "1"), ("KillMode", "scalar", "control-group"),
                  ("NoNewPrivileges", "boolean", "yes"), ("Type", "scalar", "oneshot"),
                  ("User", "scalar", "daemon"), ("WorkingDirectory", "path_list", "/"),
              )),
            *((service_catalog.ServiceDirectiveV1(
                "Service", "WatchdogSec", "duration", (watchdog,),
            ),) if label == "probe" and watchdog is not None else ()),
        )
        entries.append(service_catalog.ServiceCatalogEntryV1(
            entry_id, unit_name, None, None, "gated_service", "system",
            "python_module", "/usr/bin/python3.12", D("1"),
            "runtime.executor_birth_activation_probe", (), "/release", (), None,
            service_catalog.make_unit_spec_v1(
                unit_name, sorted(directives, key=service_catalog._directive_sort_key),
            ), True, label == "probe",
        ))
    timer_spec = service_catalog.make_unit_spec_v1("probe.timer", (
        service_catalog.ServiceDirectiveV1("Unit", "Description", "scalar", ("probe timer",)),
        service_catalog.ServiceDirectiveV1("Timer", "OnActiveSec", "duration", ("30s",)),
        service_catalog.ServiceDirectiveV1("Timer", "Unit", "unit_list", ("probe.service",)),
    ))
    entries.append(service_catalog.ServiceCatalogEntryV1(
        "timer-probe", "probe.timer", None, None, "gated_timer", "system", "none",
        None, None, None, (), None, (), "service-probe", timer_spec, False, False,
    ))
    catalog = preflight._decode_service_catalog_v1(
        service_catalog._encode_service_catalog_v1(tuple(entries), ()),
    )
    candidates = tuple(preflight._CandidateUnitV1(
        entry.entry_id, entry.unit_name, entry.unit_spec.fragment_hash,
        entry.unit_spec.directives, (),
    ) for entry in catalog.entries)
    fragments = tuple((entry.unit_name, preflight._render_service_directives_v1(
        entry.unit_spec.directives,
    )) for entry in catalog.entries)
    return preflight._BoundPreflightMaterialsV1(
        SimpleNamespace(), SimpleNamespace(), catalog,
        SimpleNamespace(systemctl_executable="/usr/bin/systemctl"), SimpleNamespace(),
        preflight._CandidateUnitsSnapshotV1(candidates, b"candidate", D("a")),
        fragments, D("b"), D("c"),
    )


def _timer_manager_observation(entry, catalog) -> dict[str, tuple[str, ...]]:
    """Build typed show values against the real plan, without replacing it."""
    plan = preflight._systemd_property_plan_v1(entry)
    values = {name: ("",) * count for name, count in plan.cardinalities if count}
    values.update({name: (value,) for name, value in (
        ("ActiveState", "active"), ("SubState", "running"),
        ("MainPID", "123"), ("ControlPID", "0"),
        ("ExecMainStartTimestampMonotonic", "1"),
    ) if name in values})
    defaults = {"boolean": "no", "integer": "0", "duration": "0s"}
    for section, name, kind in preflight._systemd_applicable_directives_v1(entry.class_name):
        for property_name in preflight._systemd_manager_properties_for_directive_v1(section, name):
            if property_name in values:
                values[property_name] = (defaults.get(kind, ""),)
    if entry.class_name == "gated_service":
        values.update(KillSignal=("SIGTERM",), UMask=("0022",),
                      MemoryHigh=("infinity",), MemoryMax=("infinity",),
                      WatchdogUSec=("infinity",))
    for directive in entry.unit_spec.directives:
        section, name, kind = directive.section, directive.name, directive.value_type
        if kind == "argv":
            executable = directive.values[0].removeprefix("!")
            argv = " ".join((executable, *directive.values[1:]))
            for property_name, flags in ((name, "ignore_errors=no"), (name + "Ex", "flags=no-setuid")):
                values[property_name] = (
                    f"{{ path={executable} ; argv[]={argv} ; {flags} ; "
                    "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }",
                )
        elif name == "OnActiveSec":
            values["TimersMonotonic"] = ("{ OnActiveUSec=30s ; next_elapse=[n/a] }",)
        else:
            properties = preflight._systemd_manager_properties_for_directive_v1(section, name)
            assert len(properties) == 1
            values[properties[0]] = (" ".join(directive.values),)
    values.update(FragmentPath=(f"/etc/systemd/system/{entry.unit_name}",),
                  LoadState=("loaded",), UnitFileState=("static",), NeedDaemonReload=("no",))
    if entry.class_name == "gated_timer":
        values["Triggers"] = ("probe.service",)
    preflight._validate_systemd_property_cardinality_v1(plan, values)
    preflight._compile_systemd_manager_projection_v1(
        entry, values, catalog=catalog,
    )
    return values


@pytest.fixture
def timer_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    materials = _timer_materials()
    live_root = tmp_path / "live"
    unit_root = live_root / "etc/systemd/system"
    unit_root.mkdir(parents=True)
    for name, fragment in materials.unit_fragments:
        (unit_root / name).write_bytes(fragment)
        (unit_root / name).chmod(0o644)
    for name in ("outside.timer", "outside.service"):
        (unit_root / name).write_bytes(b"[Unit]\nDescription=foreign fixture\n")
        (unit_root / name).chmod(0o644)
    _harden_directories(live_root)
    entries = {entry.unit_name: entry for entry in materials.catalog.entries}
    observed = {
        name: _timer_manager_observation(entry, materials.catalog)
        for name, entry in entries.items()
    }

    def show(executable, unit_name, properties):
        assert executable == "/usr/bin/systemctl"
        if unit_name is None:
            assert properties == ("Version",)
            return {"Version": ("255.4-1ubuntu8.17",)}
        if properties == preflight._SYSTEMD_ORIGIN_PROPERTIES_V1:
            assert unit_name in entries or unit_name in {"outside.timer", "outside.service"}
            return {
                "FragmentPath": (f"/etc/systemd/system/{unit_name}",),
                "Id": (unit_name,), "LoadState": ("loaded",), "SourcePath": ("",),
                "Transient": ("no",), "UnitFileState": ("static",),
            }
        assert properties == preflight._systemd_property_plan_v1(entries[unit_name]).requested_properties
        return observed[unit_name]

    monkeypatch.setattr(preflight, "_run_systemctl_show_v1", show)

    def capture():
        return preflight._capture_effective_systemd_units_core_v1(
            materials, systemctl_executable="/usr/bin/systemctl", live_root=live_root,
            uid=os.getuid(), gid=os.getgid(),
        )

    return materials, observed, capture


@LINUX_ONLY
def test_declared_timer_inactive_active_has_identical_complete_snapshot(timer_capture) -> None:
    _materials, observed, capture = timer_capture
    inactive = capture()
    observed["probe.service"]["TriggeredBy"] = ("probe.timer",)
    observed["probe.service"]["After"] = ("basic.target probe.timer",)
    observed["probe.service"]["WatchdogUSec"] = ("0",)
    active = capture()
    assert inactive == active
    service = next(item for item in active.snapshot.entries if item.unit_name == "probe.service")
    timer = next(item for item in active.snapshot.entries if item.unit_name == "probe.timer")
    assert service.manager_added_edges == ()
    assert [(edge.relation, edge.unit_name) for edge in timer.manager_added_edges] == [("Triggers", "probe.service")]
    assert {item.logical_path for item in active.files} == {
        "/etc/systemd/system/other.service", "/etc/systemd/system/probe.service",
        "/etc/systemd/system/probe.timer",
    }
    observed["probe.service"]["TriggeredBy"] = ("",)
    observed["probe.service"]["After"] = ("basic.target",)
    assert capture() == inactive


@LINUX_ONLY
@pytest.mark.parametrize(("unit", "relation", "added"), [
    ("probe.service", "TriggeredBy", "outside.timer"),
    ("probe.service", "After", "outside.timer"),
    ("other.service", "TriggeredBy", "probe.timer"),
    ("other.service", "After", "probe.timer"),
    ("probe.service", "ConflictedBy", "outside.service"),
    ("probe.service", "ConsistsOf", "probe.timer"),
    ("probe.timer", "Triggers", "outside.service"),
])
def test_undeclared_timer_or_other_relation_changes_complete_hash(
    timer_capture, unit: str, relation: str, added: str,
) -> None:
    _materials, observed, capture = timer_capture
    before = capture()
    previous = observed[unit][relation][0]
    observed[unit][relation] = (" ".join(sorted(filter(None, (previous, added)))),)
    after = capture()
    entry = next(item for item in after.snapshot.entries if item.unit_name == unit)
    assert (relation, added) in {(edge.relation, edge.unit_name) for edge in entry.manager_added_edges}
    assert after.snapshot.effective_units_hash != before.snapshot.effective_units_hash


@LINUX_ONLY
def test_timer_observed_target_mismatch_still_denied(timer_capture) -> None:
    _materials, observed, capture = timer_capture
    observed["probe.timer"]["Unit"] = ("other.service",)
    assert _assert_invalid(capture).detail == "systemd configured directive"


@LINUX_ONLY
def test_explicit_after_declared_timer_remains_signed() -> None:
    materials = _timer_materials(explicit_timer_after=True)
    entry = next(
        item for item in materials.catalog.entries
        if item.unit_name == "probe.service"
    )
    observed = _timer_manager_observation(entry, materials.catalog)
    projection = preflight._compile_systemd_manager_projection_v1(
        entry, observed, catalog=materials.catalog,
    )
    after = next(item for item in projection.properties if item.name == "After")
    assert after.values == ("basic.target", "probe.timer")
    assert preflight._compile_systemd_added_edge_pairs_v1(
        entry, observed, catalog=materials.catalog,
    ) == ()


@LINUX_ONLY
def test_unconfigured_watchdog_disabled_forms_have_one_canonical_value() -> None:
    materials = _timer_materials()
    entry = next(
        item for item in materials.catalog.entries
        if item.unit_name == "probe.service"
    )
    observed = _timer_manager_observation(entry, materials.catalog)
    infinity = preflight._compile_systemd_manager_projection_v1(
        entry, observed, catalog=materials.catalog,
    )
    observed["WatchdogUSec"] = ("0",)
    zero = preflight._compile_systemd_manager_projection_v1(
        entry, observed, catalog=materials.catalog,
    )
    assert zero == infinity
    watchdog = next(item for item in zero.properties if item.name == "WatchdogSec")
    assert watchdog.values == ("0",)
    observed["WatchdogUSec"] = ("1s",)
    assert _assert_invalid(
        preflight._compile_systemd_manager_projection_v1,
        entry, observed, catalog=materials.catalog,
    ).detail == "systemd Watchdog default ('1000000',)"


@LINUX_ONLY
def test_configured_nonzero_watchdog_remains_exact() -> None:
    materials = _timer_materials(watchdog="5s")
    entry = next(
        item for item in materials.catalog.entries
        if item.unit_name == "probe.service"
    )
    observed = _timer_manager_observation(entry, materials.catalog)
    preflight._compile_systemd_manager_projection_v1(
        entry, observed, catalog=materials.catalog,
    )
    for disabled in ("0", "infinity"):
        changed = dict(observed, WatchdogUSec=(disabled,))
        assert _assert_invalid(
            preflight._compile_systemd_manager_projection_v1,
            entry, changed, catalog=materials.catalog,
        ).detail == "systemd configured directive"


@LINUX_ONLY
def test_configured_watchdog_initial_sentinel_has_stable_projection() -> None:
    """systemd 255 exposes infinity until the first start, not WatchdogSec."""
    materials = _timer_materials(watchdog="45s")
    entry = next(e for e in materials.catalog.entries if e.unit_name == "probe.service")
    running = _timer_manager_observation(entry, materials.catalog)
    expected = preflight._compile_systemd_manager_projection_v1(
        entry, running, catalog=materials.catalog,
    )
    initial = dict(running, WatchdogUSec=("infinity",), ActiveState=("inactive",),
                   SubState=("dead",), MainPID=("0",), ControlPID=("0",),
                   ExecMainStartTimestampMonotonic=("0",))
    assert preflight._compile_systemd_manager_projection_v1(
        entry, initial, catalog=materials.catalog,
    ) == expected
    # No disabling or stale override is admitted after a start or without
    # the complete never-started observation, even if the service is stopped.
    for key, value in (
        ("ActiveState", "active"), ("ActiveState", "activating"),
        ("SubState", "start-pre"), ("MainPID", "123"), ("ControlPID", "123"),
        ("ExecMainStartTimestampMonotonic", "1"),
        ("WatchdogUSec", "0"), ("WatchdogUSec", "1s"),
    ):
        _assert_invalid(preflight._compile_systemd_manager_projection_v1,
                        entry, dict(initial, **{key: (value,)}), catalog=materials.catalog)
    for key in ("ActiveState", "SubState", "MainPID", "ControlPID",
                "ExecMainStartTimestampMonotonic"):
        incomplete = dict(initial)
        incomplete.pop(key)
        _assert_invalid(preflight._compile_systemd_manager_projection_v1,
                        entry, incomplete, catalog=materials.catalog)
        _assert_invalid(preflight._compile_systemd_manager_projection_v1,
                        entry, dict(initial, **{key: initial[key] * 2}), catalog=materials.catalog)


@LINUX_ONLY
@pytest.mark.parametrize("relation", ["Requires", "After"])
def test_missing_explicit_relation_still_denied_with_declared_timer(
    timer_capture, relation: str,
) -> None:
    _materials, observed, capture = timer_capture
    observed["probe.service"]["TriggeredBy"] = ("probe.timer",)
    observed["probe.service"][relation] = ("",)
    assert _assert_invalid(capture).detail == "systemd direct relation"



def _live_manager_units() -> tuple[str, ...]:
    probe = subprocess.run(
        ["systemctl", "--user", "--no-pager", "--plain", "--no-legend",
         "list-units", "--type=service", "--state=loaded"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return ()
    return tuple(
        line.split()[0] for line in probe.stdout.splitlines() if line.split()
    )[:3]


@LINUX_ONLY
def test_every_applicable_directive_survives_real_manager_output() -> None:
    """Sweep the module's own normalizers against what systemd really renders.

    Every C3 denial so far was one unmeasured assumption about that rendering,
    and each cost a full CI round because the cell that would have caught it
    needs root. This cell needs none: it asks the live manager for the exact
    property set the module requests and runs each value through the very
    normalizer the productive path uses. A recorded observation cannot replace
    it, because a recorded observation carries the same assumption as the code
    and therefore always agrees with the defect.

    Directives that legitimately need the signed catalog to be interpreted are
    reported as such by the module itself and are not failures here.
    """
    units = _live_manager_units()
    if not units:
        pytest.skip("no live user manager on this host")

    def observe(unit: str, properties: list[str]) -> dict:
        shown = subprocess.run(
            ["systemctl", "--user", "--no-pager", "--plain", "--all", "show",
             "--property=" + ",".join(sorted(set(properties))), "--", unit],
            capture_output=True, text=True,
        )
        collected: dict[str, list[str]] = {}
        for line in shown.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                collected.setdefault(key, []).append(value)
        return {key: tuple(value) for key, value in collected.items()}

    refused: list[tuple[str, str, str]] = []
    applicable = preflight._systemd_applicable_directives_v1("gated_service")
    properties: list[str] = []
    for section, name, _value_type in applicable:
        properties.extend(
            preflight._systemd_manager_properties_for_directive_v1(
                section, name,
            )
        )
    for unit in units:
        observed = observe(unit, properties)
        if not observed:
            continue
        for section, name, value_type in applicable:
            try:
                preflight._normalize_manager_directive_v1(
                    section, name, value_type, observed,
                )
            except preflight.PreflightError as denial:
                if "signed context" in denial.detail:
                    continue
                if "grouped context" in denial.detail:
                    continue
                refused.append((unit, name, denial.detail))
    assert refused == [], refused
