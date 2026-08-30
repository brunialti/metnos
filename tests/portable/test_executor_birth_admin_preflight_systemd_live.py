"""Probative tests for the non-authorizing live systemd observation."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler


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


@LINUX_ONLY
def test_check_all_attestation_is_exact_idempotent_and_no_replace(
    tmp_path: Path,
) -> None:
    materials, _fragment = _target_materials()
    materials, _encoded = _with_effective_prerequisite(materials, D("e"))
    operational = _operational_attestation_fixture(materials)
    root = tmp_path / "attestations"
    root.mkdir(mode=0o755)

    encoded = preflight._publish_preflight_attestation_for_test_v1(
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
    destination = root / f"{materials.prerequisite.request_id}.json"
    assert destination.read_bytes() == encoded
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    assert destination.stat().st_nlink == 1
    assert preflight._publish_preflight_attestation_for_test_v1(
        operational, root,
    ) == encoded

    destination.write_bytes(encoded + b" ")
    with pytest.raises(preflight.PreflightError) as conflict:
        preflight._publish_preflight_attestation_for_test_v1(
            operational, root,
        )
    assert conflict.value.code == preflight.CODE_RECOVERY


@LINUX_ONLY
def test_check_all_attestation_retains_partial_state_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    materials, _fragment = _target_materials()
    materials, _encoded = _with_effective_prerequisite(materials, D("e"))
    operational = _operational_attestation_fixture(materials)
    root = tmp_path / "attestations"
    root.mkdir(mode=0o755)
    monkeypatch.setattr(
        preflight.os, "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("killpoint")),
    )

    with pytest.raises(preflight.PreflightError) as failure:
        preflight._publish_preflight_attestation_for_test_v1(
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
    ).detail == "systemd origin identity"


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
