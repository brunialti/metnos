"""Differential oracles for the autonomous preflight material codecs.

The installed preflight must run under ``python -I -S`` and therefore cannot
import the canonical Metnos codecs.  These tests keep that independent clone
honest without repeating the canonical codec suites: one valid document per
format and one self-reidentified semantic mutant per format are sufficient to
distinguish a permissive clone from the existing oracle.
"""
from __future__ import annotations

import builtins
import dataclasses
import hashlib
import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Callable

import pytest

import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler
import executor_birth_service_catalog as catalog


_EXPECTED_ENABLEMENT_LINKS = (
    (
        "/etc/systemd/system/default.target.wants/metnos.target",
        "../metnos.target",
    ),
    (
        "/etc/systemd/system/metnos.target.requires/metnos-http.service",
        "../metnos-http.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/"
        "metnos-durable-worker.service",
        "../metnos-durable-worker.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/"
        "metnos-i18n-translator.timer",
        "../metnos-i18n-translator.timer",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-llm.service",
        "../metnos-llm.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-photon.service",
        "../metnos-photon.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-playwright.service",
        "../metnos-playwright.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/metnos-searxng.service",
        "../metnos-searxng.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/"
        "metnos-side-display.service",
        "../metnos-side-display.service",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/"
        "metnos-stack-watchdog.timer",
        "../metnos-stack-watchdog.timer",
    ),
    (
        "/etc/systemd/system/metnos.target.wants/"
        "metnos-telegram-daemon.service",
        "../metnos-telegram-daemon.service",
    ),
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _reidentify(encoded: bytes, field: str, domain: bytes) -> bytes:
    value = json.loads(encoded)
    unsigned = dict(value)
    unsigned.pop(field)
    value[field] = "sha256:" + hashlib.sha256(
        domain + _canonical(unsigned)
    ).hexdigest()
    return _canonical(value)


def _semantic(value: object) -> object:
    """Remove nominal type differences while preserving the whole value."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _semantic(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if hasattr(value, "_asdict"):
        return {
            key: _semantic(item)
            for key, item in value._asdict().items()
        }
    if isinstance(value, Mapping):
        return {key: _semantic(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_semantic(item) for item in value)
    return value


def _assert_record_fields_match(observed: object, expected: object) -> None:
    expected_value = _semantic(expected)
    assert isinstance(expected_value, dict)
    assert {
        key: _semantic(getattr(observed, key))
        for key in expected_value
    } == expected_value


def _catalog_bytes() -> bytes:
    installation_root = (
        "/var/lib/metnos/executor-birth/releases-v1/00000000000000000002"
    )
    target_hashes = tuple(
        (item.entry_id, D("a"))
        for item in catalog.SERVICE_SOURCE_V1
        if item.target_recipe.execution_kind != "none"
    )
    entries = catalog._compile_service_source_v1(
        catalog._SourceCompileContextV1(
            installation_root=installation_root,
            python_executable="/usr/bin/python3.12",
            service_user="metnos",
            service_gid=991,
            service_supplementary_gids=(44, 991),
            service_home="/var/lib/metnos",
            systemctl_executable="/usr/bin/systemctl",
            target_hashes=target_hashes,
        )
    )
    legacy = tuple(
        catalog.ServiceLegacyBindingV1(
            item["legacy_id"], item["entry_id"], item["kind"],
            item["scope"], item["locator"], item["disposition"],
        )
        for item in catalog.legacy_bindings_from_source_v1()
    )
    return catalog._encode_service_catalog_v1(entries, legacy)


def _deployment_record() -> assembler.DeploymentDescriptorV1:
    decoded_catalog = catalog.decode_service_catalog_v1(_catalog_bytes())
    artifacts = (
        assembler.DeploymentArtifactV1(
            "deployment/systemd/metnos.target",
            "/etc/systemd/system/metnos.target", "target_unit",
            "group7_cutover", 3, D("b"), 0o644, 0, 0,
        ),
        assembler.DeploymentArtifactV1(
            "deployment/admin/preflight.py",
            "/usr/libexec/metnos/executor-birth-v1/preflight.py",
            "administrative_program", "group6_admin", 3, D("c"),
            0o755, 0, 0,
        ),
    )
    return assembler.build_deployment_descriptor_v1(
        release_sequence=2,
        service_user="metnos",
        service_uid=991,
        service_gid=991,
        service_supplementary_gids=(44, 991),
        service_home="/var/lib/metnos",
        service_shell="/usr/sbin/nologin",
        artifacts=artifacts,
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        python_executable="/usr/bin/python3.12",
        openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )


def _prerequisite_record() -> assembler.StartupPrerequisiteV1:
    deployment = _deployment_record()
    return assembler.build_startup_prerequisite_v1(
        request_id=D("1"),
        closed_build_id=D("2"),
        release_sequence=2,
        deployment_descriptor_id=deployment.descriptor_id,
        predecessor_id=D("3"),
        administrative_bundle_hash=D("4"),
        python_binary_hash=D("5"),
        openssl_binary_hash=D("6"),
        openssl_tcb_hash=D("7"),
        systemctl_binary_hash=D("8"),
        systemd_analyze_binary_hash=D("9"),
        service_catalog_id=deployment.service_catalog_id,
        service_coverage_hash=deployment.service_coverage_hash,
        systemd_manager_version="255.4-1ubuntu8.17",
        candidate_units_hash=D("b"),
        effective_units_hash=D("c"),
    )


def _expected_candidate_document(
    decoded: catalog.DecodedServiceCatalogV1,
) -> bytes:
    entries = []
    for entry in decoded.entries:
        if entry.unit_spec is None:
            continue
        links = []
        for directive in entry.unit_spec.directives:
            if (
                directive.section != "Install"
                or directive.name not in {"WantedBy", "RequiredBy"}
            ):
                continue
            relation = (
                "wants" if directive.name == "WantedBy" else "requires"
            )
            for target_unit in directive.values:
                links.append({
                    "path": (
                        f"/etc/systemd/system/{target_unit}.{relation}/"
                        f"{entry.unit_name}"
                    ),
                    "target": f"../{entry.unit_name}",
                })
        links.sort(key=lambda item: item["path"].encode("utf-8"))
        entries.append({
            "entry_id": entry.entry_id,
            "unit_name": entry.unit_name,
            "fragment_hash": entry.unit_spec.fragment_hash,
            "directives": [{
                "section": directive.section,
                "name": directive.name,
                "value_type": directive.value_type,
                "values": list(directive.values),
            } for directive in entry.unit_spec.directives],
            "enablement_links": links,
        })
    return _canonical({"schema_version": 1, "entries": entries})


def _independent_administrative_bundle_hash(
    artifacts: tuple[assembler.DeploymentArtifactV1, ...],
) -> str:
    material = bytearray(len(artifacts).to_bytes(8, "big"))
    for artifact in artifacts:
        for value in (
            artifact.destination_path.encode("utf-8"),
            artifact.kind.encode("ascii"),
            artifact.install_phase.encode("ascii"),
        ):
            material.extend(len(value).to_bytes(8, "big"))
            material.extend(value)
        material.extend(artifact.mode.to_bytes(4, "big"))
        material.extend(artifact.size.to_bytes(8, "big"))
        material.extend(bytes.fromhex(artifact.content_hash.removeprefix("sha256:")))
    return "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.administrative-bundle/v1\0" + material
    ).hexdigest()


def _deployment_with_artifacts(
    artifacts: tuple[assembler.DeploymentArtifactV1, ...],
) -> assembler.DeploymentDescriptorV1:
    original = _deployment_record()
    return assembler.build_deployment_descriptor_v1(
        release_sequence=original.release_sequence,
        service_user=original.service_user,
        service_uid=original.service_uid,
        service_gid=original.service_gid,
        service_supplementary_gids=original.service_supplementary_gids,
        service_home=original.service_home,
        service_shell=original.service_shell,
        artifacts=artifacts,
        service_catalog_id=original.service_catalog_id,
        service_coverage_hash=original.service_coverage_hash,
        python_executable=original.python_executable,
        openssl_executable=original.openssl_executable,
        systemctl_executable=original.systemctl_executable,
        systemd_analyze_executable=original.systemd_analyze_executable,
    )


def _binder_installation_root(release_sequence: int) -> str:
    return (
        "/var/lib/metnos/executor-birth/releases-v1/"
        f"{release_sequence:020d}"
    )


def _binder_target_bytes(installation_root: str) -> dict[str, bytes]:
    return {
        "/usr/bin/python3.12": b"python-v1",
        "/usr/bin/systemctl": b"systemctl-v1",
        "/usr/bin/java": b"java-v1",
        "/usr/bin/Xvfb": b"xvfb-v1",
        f"{installation_root}/runtime/bin/llama-server": b"llama-v1",
    }


def _bound_catalog_bytes(
    *, installation_root: str,
    service_home: str = "/var/lib/metnos",
    extra_install_target: bool = False,
    recipe_mutation: str | None = None,
) -> bytes:
    target_bytes = _binder_target_bytes(installation_root)
    target_hashes = []
    for source in catalog.SERVICE_SOURCE_V1:
        executable = source.target_recipe.target_executable
        if executable is None:
            continue
        resolved = (
            executable.replace("@python@", "/usr/bin/python3.12")
            .replace("@systemctl@", "/usr/bin/systemctl")
            .replace("@installation_root@", installation_root)
        )
        target_hashes.append((
            source.entry_id,
            catalog.target_executable_hash_v1(
                resolved, target_bytes[resolved],
            ),
        ))
    entries = catalog._compile_service_source_v1(
        catalog._SourceCompileContextV1(
            installation_root=installation_root,
            python_executable="/usr/bin/python3.12",
            service_user="metnos",
            service_gid=991,
            service_supplementary_gids=(44, 991),
            service_home=service_home,
            systemctl_executable="/usr/bin/systemctl",
            target_hashes=tuple(target_hashes),
        )
    )
    legacy = tuple(
        catalog.ServiceLegacyBindingV1(
            item["legacy_id"], item["entry_id"], item["kind"],
            item["scope"], item["locator"], item["disposition"],
        )
        for item in catalog.legacy_bindings_from_source_v1()
    )
    encoded = catalog._encode_service_catalog_v1(entries, legacy)
    if not extra_install_target and recipe_mutation is None:
        return encoded

    value = json.loads(encoded)
    if recipe_mutation == "pre-normalized-marker":
        entry = next(
            item for item in value["entries"]
            if any(
                variable["name"] == "METNOS_USER_DATA"
                for variable in item["target_environment"]
            )
        )
        variable = next(
            variable for variable in entry["target_environment"]
            if variable["name"] == "METNOS_USER_DATA"
        )
        variable["value"] = "@service-home@/.local/share/metnos"
        return _reidentify(
            _canonical(value), "catalog_id", catalog.CATALOG_ID_DOMAIN,
        )

    wanted_unit = (
        "metnos-durable-worker.service" if extra_install_target
        else "metnos-http.service"
    )
    entry = next(item for item in value["entries"] if item["unit_name"] == wanted_unit)
    if extra_install_target:
        directive = next(
            item for item in entry["unit_spec"]["directives"]
            if item["section"] == "Install" and item["name"] == "WantedBy"
        )
        directive["values"].append("uncatalogued.target")
        directive["values"].sort(key=lambda item: item.encode("utf-8"))
    else:
        wanted_key = (
            ("Unit", "Description")
            if recipe_mutation == "description"
            else ("Service", "Restart")
        )
        directive = next(
            item for item in entry["unit_spec"]["directives"]
            if (item["section"], item["name"]) == wanted_key
        )
        directive["values"] = [
            "Metnos mutated description"
            if recipe_mutation == "description" else "always"
        ]
    spec = catalog.make_unit_spec_v1(
        entry["unit_name"], entry["unit_spec"]["directives"],
    )
    entry["unit_spec"]["fragment_hash"] = spec.fragment_hash
    return _reidentify(
        _canonical(value), "catalog_id", catalog.CATALOG_ID_DOMAIN,
    )


def _isolated_g6c_records(
    *, description: str = "isolated signed G6-C probe",
) -> tuple[bytes, assembler.DeploymentDescriptorV1]:
    namespace = "0123456789abcdef"
    release_sequence = 1
    installation_root = _binder_installation_root(release_sequence)
    marker_root = f"/run/metnos-g6c-{namespace}"
    marker_path = marker_root + "/marker.json"
    service_id = f"g6c-{namespace}-probe"
    service_name = f"metnos-g6c-{namespace}-probe.service"
    timer_id = service_id + "-timer"
    timer_name = f"metnos-g6c-{namespace}-probe.timer"
    python = "/usr/bin/python3"
    administrative = "!/usr/bin/python3"
    service_spec = catalog.make_unit_spec_v1(service_name, (
        catalog.ServiceDirectiveV1(
            "Unit", "Description", "scalar", (description,),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "CapabilityBoundingSet", "scalar",
            ("CAP_SETGID CAP_SETPCAP CAP_SETUID",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ExecStart", "argv",
            (
                administrative, "-I", "-S",
                catalog.ADMINISTRATIVE_ADAPTER_PATH_V1,
                "launch", "--entry-id", service_id,
            ),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ExecStartPre", "argv",
            (
                administrative, "-I", "-S",
                catalog.ADMINISTRATIVE_ADAPTER_PATH_V1,
                "check", "--entry-id", service_id,
            ),
        ),
        catalog.ServiceDirectiveV1("Service", "Group", "scalar", ("991",)),
        catalog.ServiceDirectiveV1(
            "Service", "KillMode", "scalar", ("control-group",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "NoNewPrivileges", "boolean", ("yes",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "PrivateTmp", "boolean", ("yes",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ProtectSystem", "scalar", ("strict",),
        ),
        catalog.ServiceDirectiveV1(
            # The runtime root too: the administrative program writes
            # openssl's temporaries there, and the shape check requires it.
            "Service", "ReadWritePaths", "path_list",
            tuple(sorted(
                (marker_root, preflight.RUNTIME_ROOT.as_posix()),
                key=lambda item: item.encode("utf-8"),
            )),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "SupplementaryGroups", "scalar", ("44 991",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "Type", "scalar", ("oneshot",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "User", "scalar", ("metnos",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "WorkingDirectory", "path_list", ("/",),
        ),
    ))
    timer_spec = catalog.make_unit_spec_v1(timer_name, (
        catalog.ServiceDirectiveV1(
            "Unit", "Description", "scalar",
            ("isolated signed G6-C timer",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "AccuracySec", "duration", ("1ms",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "OnActiveSec", "duration", ("100ms",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "Unit", "unit_list", (service_name,),
        ),
    ))
    entries = (
        catalog.ServiceCatalogEntryV1(
            service_id, service_name, None, None, "gated_service", "system",
            "python_module", python,
            catalog.target_executable_hash_v1(python, b"python-v1"),
            "runtime.executor_birth_activation_probe", (marker_path,),
            installation_root, (), None,
            service_spec, True, True,
        ),
        catalog.ServiceCatalogEntryV1(
            timer_id, timer_name, None, None, "gated_timer", "system",
            "none", None, None, None, (), None, (), service_id,
            timer_spec, False, False,
        ),
    )
    encoded = catalog._encode_service_catalog_v1(entries, ())
    decoded = catalog.decode_service_catalog_v1(encoded)
    fragments = {
        service_name: catalog.render_unit_spec_v1(service_name, service_spec),
        timer_name: catalog.render_unit_spec_v1(timer_name, timer_spec),
    }
    artifacts = [assembler.DeploymentArtifactV1(
        "deployment/admin/preflight.py",
        "/usr/libexec/metnos/executor-birth-v1/preflight.py",
        "administrative_program", "group6_admin", 3,
        preflight.distribution_file_hash_v1(
            "deployment/admin/preflight.py", b"app",
        ),
        0o755, 0, 0,
    )]
    for name, content in sorted(fragments.items()):
        path = "deployment/systemd/" + name
        artifacts.append(assembler.DeploymentArtifactV1(
            path, "/etc/systemd/system/" + name,
            "timer_unit" if name.endswith(".timer") else "service_unit",
            "group7_cutover", len(content),
            preflight.distribution_file_hash_v1(path, content),
            0o644, 0, 0,
        ))
    descriptor = assembler.build_deployment_descriptor_v1(
        release_sequence=release_sequence, service_user="metnos",
        service_uid=991, service_gid=991,
        service_supplementary_gids=(44, 991),
        service_home="/var/lib/metnos", service_shell="/usr/sbin/nologin",
        artifacts=tuple(artifacts), service_catalog_id=decoded.catalog_id,
        service_coverage_hash=decoded.service_coverage_hash,
        python_executable=python, openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )
    return encoded, descriptor


def _bound_graph(
    mutation: str | None = None,
) -> dict[str, object]:
    release_sequence = 1 if mutation == "release1-predecessor" else 2
    installation_root = _binder_installation_root(release_sequence)
    service_home = (
        installation_root
        if mutation == "service-home-inside-release"
        else "/var/lib/metnos"
    )
    target_bytes = _binder_target_bytes(installation_root)
    catalog_encoded = _bound_catalog_bytes(
        installation_root=installation_root,
        service_home=service_home,
        extra_install_target=mutation == "extra-install-target",
        recipe_mutation=(
            "description" if mutation == "recipe-description"
            else "restart" if mutation == "recipe-restart"
            else "pre-normalized-marker"
            if mutation == "pre-normalized-marker" else None
        ),
    )
    decoded_catalog = catalog.decode_service_catalog_v1(catalog_encoded)
    fragments = {
        entry.unit_name: catalog.render_unit_spec_v1(
            entry.unit_name, entry.unit_spec,
        )
        for entry in decoded_catalog.entries if entry.unit_spec is not None
    }
    if mutation == "fragment-drift":
        entry = next(
            item for item in decoded_catalog.entries
            if item.unit_name == "metnos-http.service"
        )
        directives = list(entry.unit_spec.directives)
        index = next(
            i for i, item in enumerate(directives)
            if item.section == "Unit" and item.name == "Description"
        )
        directives[index] = dataclasses.replace(
            directives[index], values=("Metnos signed but different",),
        )
        changed_spec = catalog.make_unit_spec_v1(entry.unit_name, directives)
        fragments[entry.unit_name] = catalog.render_unit_spec_v1(
            entry.unit_name, changed_spec,
        )

    extra_unit = "metnos-extra.service"
    if mutation == "extra-artifact":
        fragments[extra_unit] = b"[Unit]\nDescription=Extra signed unit\n"

    kind_by_class = {
        "gated_service": "service_unit",
        "gated_timer": "timer_unit",
        "stop_only": "stop_only_unit",
        "target": "target_unit",
    }
    artifacts = [assembler.DeploymentArtifactV1(
        "deployment/admin/preflight.py",
        "/usr/libexec/metnos/executor-birth-v1/preflight.py",
        "administrative_program", "group6_admin", len(b"preflight-v1"),
        preflight.distribution_file_hash_v1(
            "deployment/admin/preflight.py", b"preflight-v1",
        ),
        0o755, 0, 0,
    )]
    for entry in decoded_catalog.entries:
        if entry.unit_spec is None:
            continue
        if mutation == "missing-artifact" and entry.unit_name == "metnos.target":
            continue
        kind = kind_by_class[entry.class_name]
        if (
            mutation == "wrong-kind"
            and entry.unit_name == "metnos-stack-quarantine.service"
        ):
            kind = "service_unit"
        path = f"deployment/systemd/{entry.unit_name}"
        content = fragments[entry.unit_name]
        artifacts.append(assembler.DeploymentArtifactV1(
            path, f"/etc/systemd/system/{entry.unit_name}", kind,
            "group7_cutover", len(content),
            preflight.distribution_file_hash_v1(path, content),
            0o644, 0, 0,
        ))
    if mutation == "extra-artifact":
        path = f"deployment/systemd/{extra_unit}"
        artifacts.append(assembler.DeploymentArtifactV1(
            path, f"/etc/systemd/system/{extra_unit}", "service_unit",
            "group7_cutover", len(fragments[extra_unit]),
            preflight.distribution_file_hash_v1(path, fragments[extra_unit]),
            0o644, 0, 0,
        ))

    descriptor_record = assembler.build_deployment_descriptor_v1(
        release_sequence=release_sequence,
        service_user=("other" if mutation == "descriptor-account" else "metnos"),
        service_uid=991,
        service_gid=991,
        service_supplementary_gids=(44, 991),
        service_home=service_home,
        service_shell="/usr/sbin/nologin",
        artifacts=tuple(artifacts),
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        python_executable="/usr/bin/python3.12",
        openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )
    descriptor_encoded = assembler.encode_deployment_descriptor_v1(
        descriptor_record
    )
    decoded_descriptor = preflight._decode_deployment_descriptor_v1(
        descriptor_encoded
    )
    bundle_hash = preflight._administrative_bundle_hash_v1(
        decoded_descriptor
    )

    contents = {
        "deployment/admin/preflight.py": b"preflight-v1",
        "deployment/executor-birth-deployment-v1.json": descriptor_encoded,
        "deployment/executor-birth-service-catalog-v1.json": catalog_encoded,
        "internal/reports/boundary.json": b"{}",
        "requirements.lock": b"fixture==1\n",
        "runtime/__version__.py": b'__version__ = "1.2.3"\n',
        "runtime/contract_boundary_guard.py": b"VALUE = 1\n",
        "runtime/contract_store.py": b"VALUE = 1\n",
        "runtime/executor_birth.py": b"VALUE = 1\n",
        "runtime/executor_birth_distribution_manifest.py": b"VALUE = 1\n",
        "runtime/executor_birth_ownership_preflight.py": b"VALUE = 1\n",
        "runtime/sign.py": b"VALUE = 1\n",
        "runtime/bin/llama-server": target_bytes[
            f"{installation_root}/runtime/bin/llama-server"
        ],
        **{
            f"deployment/systemd/{name}": content
            for name, content in fragments.items()
        },
    }
    roles = {
        "deployment/admin/preflight.py": "preflight",
        "deployment/executor-birth-deployment-v1.json": "deployment_descriptor",
        "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
        "internal/reports/boundary.json": "boundary_inventory",
        "requirements.lock": "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
        **{
            f"deployment/systemd/{name}": "service_unit"
            for name in fragments
        },
    }
    manifest_files = [{
        "path": path,
        "size": len(contents[path]) + (
            1 if (
                mutation == "manifest-target-size"
                and path == "runtime/bin/llama-server"
            ) else 0
        ),
        "content_hash": preflight.distribution_file_hash_v1(
            path, contents[path],
        ),
        "role": roles.get(path, "runtime_code"),
    } for path in sorted(contents, key=lambda item: item.encode("utf-8"))]
    manifest = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": (
            None if release_sequence == 1 else D("d")
        ),
        "release_sequence": release_sequence,
        "product_version": "1.2.3",
        "platform": "linux",
        "architecture": "x86_64",
        "signing_key_id": "distribution-ed25519-v1-sha256-" + "e" * 64,
        "installation_root": installation_root,
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "boundary_inventory_path": "internal/reports/boundary.json",
        "boundary_inventory_hash": D("f"),
        "boundary_guard_version": "fixture-v1",
        "preflight_entrypoint": "deployment/admin/preflight.py",
        "files": manifest_files,
    }
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    manifest_encoded = preflight._canonical_json(manifest)
    manifest_value, distribution_files = preflight._parse_distribution_manifest_v1(
        manifest_encoded
    )
    distribution = preflight._AuthenticatedDistributionObjectV1(
        preflight._distribution_facts_v1(manifest_value),
        distribution_files, manifest_encoded, b"s" * 64,
    )

    predecessor_is_initially_different = mutation in {
        "release1-predecessor", "release2-initial-predecessor",
    }
    predecessor_record = assembler.build_predecessor_descriptor_v1(
        transaction_id=D("a"),
        installation_root="/opt/metnos",
        files=(assembler.PredecessorFileV1("runtime/legacy.py", 1, D("b")),),
        service_commands=(assembler.PredecessorServiceCommandV1(
            "legacy", "none", None, None, None, (), None, (),
        ),),
        administrative_bundle_hash=bundle_hash,
        service_catalog_id=(
            D("0") if predecessor_is_initially_different
            else decoded_catalog.catalog_id
        ),
        service_coverage_hash=(
            D("1") if predecessor_is_initially_different
            else decoded_catalog.service_coverage_hash
        ),
    )
    predecessor = preflight._decode_predecessor_descriptor_v1(
        assembler.encode_predecessor_descriptor_v1(predecessor_record)
    )
    candidate_encoded = _expected_candidate_document(decoded_catalog)
    candidate_hash = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.candidate-units/v1\0" + candidate_encoded
    ).hexdigest()
    manager_version = "255.5" if mutation == "manager-255.5" else (
        "255.4-1ubuntu8.17"
    )
    prerequisite_record = assembler.build_startup_prerequisite_v1(
        request_id=D("1"),
        closed_build_id=distribution.facts.closed_build_id,
        release_sequence=release_sequence,
        deployment_descriptor_id=descriptor_record.descriptor_id,
        predecessor_id=predecessor.predecessor_id,
        administrative_bundle_hash=bundle_hash,
        python_binary_hash=D("2"),
        openssl_binary_hash=D("3"),
        openssl_tcb_hash=D("4"),
        systemctl_binary_hash=D("5"),
        systemd_analyze_binary_hash=D("6"),
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        systemd_manager_version=manager_version,
        candidate_units_hash=candidate_hash,
        effective_units_hash=D("7"),
    )
    prerequisite_encoded = assembler.encode_startup_prerequisite_v1(
        prerequisite_record
    )
    installed_tree_hash = preflight._installed_tree_hash_v1(distribution_files)
    transaction = preflight._DecodedCoordinatorRecordV2(
        sequence=4,
        state="BUILD_VERIFIED",
        previous_record_sha256=D("8"),
        request_id=prerequisite_record.request_id,
        previous_closed_build_id=distribution.facts.previous_closed_build_id,
        previous_cutover_id=(None if release_sequence == 1 else D("9")),
        closed_build_id=distribution.facts.closed_build_id,
        distribution_payload_hash=(
            "sha256:" + hashlib.sha256(manifest_encoded).hexdigest()
        ),
        distribution_signature_hash=(
            "sha256:" + hashlib.sha256(b"s" * 64).hexdigest()
        ),
        boundary_inventory_hash=distribution.facts.boundary_inventory_hash,
        boundary_guard_version=distribution.facts.boundary_guard_version,
        current_receipts=(),
        maintenance_before_hash=D("a"),
        maintenance_after_hash=D("b"),
        maintenance_proof=b"maintenance-v1",
        startup_prerequisite_id=prerequisite_record.prerequisite_id,
        startup_prerequisite_digest=(
            "sha256:" + hashlib.sha256(prerequisite_encoded).hexdigest()
        ),
        cutover_id=D("c"),
        catalog_id=decoded_catalog.catalog_id,
        certificate_payload_hash=D("d"),
        certificate_signature_hash=D("e"),
        dominant_startup_receipt=D("0"),
        source_id=D("f"),
        successor_claim_id=D("1"),
        deployment_descriptor_id=descriptor_record.descriptor_id,
        install_transaction_id=predecessor.transaction_id,
        installed_tree_hash=installed_tree_hash,
        release_sequence=release_sequence,
        previous_head_id=(None if release_sequence == 1 else D("2")),
        head_id=None,
        head_payload_hash=None,
        head_signature_hash=None,
        required_head_frame_hash=None,
        verified_chain_head_id=None,
        preflight_attestation_hash=None,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        administrative_bundle_hash=bundle_hash,
        provisioning_transaction_id="0" * 32,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        target_set_id="4" * 64,
        target_admission_context_id=D("5"),
        target_context_epoch=D("6"),
        target_context_material_sha256="7" * 64,
        target_set_json_sha256="8" * 64,
        context_transition_id=D("9"),
        current_inventory_hash=(
            preflight._current_inventory_hash_from_receipts_v1(())
        ),
    )
    captured = {
        "deployment/admin/preflight.py": contents[
            "deployment/admin/preflight.py"
        ],
        "deployment/executor-birth-service-catalog-v1.json": catalog_encoded,
        "deployment/executor-birth-deployment-v1.json": descriptor_encoded,
        "runtime/bin/llama-server": contents["runtime/bin/llama-server"],
        **{
            f"deployment/systemd/{name}": content
            for name, content in fragments.items()
        },
    }
    return {
        "distribution": distribution,
        "transaction": transaction,
        "predecessor": predecessor,
        "captured": captured,
        "prerequisite_encoded": prerequisite_encoded,
        "decoded_catalog": decoded_catalog,
        "target_bytes": target_bytes,
    }


@pytest.mark.parametrize(
    ("encoded", "oracle", "autonomous_name"),
    (
        (
            _catalog_bytes,
            catalog.decode_service_catalog_v1,
            "_decode_service_catalog_v1",
        ),
        (
            lambda: assembler.encode_deployment_descriptor_v1(
                _deployment_record()
            ),
            assembler.decode_deployment_descriptor_v1,
            "_decode_deployment_descriptor_v1",
        ),
        (
            lambda: assembler.encode_startup_prerequisite_v1(
                _prerequisite_record()
            ),
            assembler.decode_startup_prerequisite_v1,
            "_decode_startup_prerequisite_v1",
        ),
    ),
    ids=("service-catalog", "deployment-descriptor", "startup-prerequisite"),
)
def test_autonomous_material_codecs_match_canonical_oracles(
    encoded: Callable[[], bytes],
    oracle: Callable[[bytes], object],
    autonomous_name: str,
) -> None:
    material = encoded()
    expected = oracle(material)
    autonomous = getattr(preflight, autonomous_name)
    observed = autonomous(material)
    _assert_record_fields_match(observed, expected)


def test_startup_prerequisite_digest_is_the_exact_canonical_byte_digest() -> None:
    encoded = assembler.encode_startup_prerequisite_v1(
        _prerequisite_record()
    )
    assert preflight._startup_prerequisite_digest_v1(encoded) == (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )


def test_candidate_units_match_independent_hash_and_exact_enablement_links() -> None:
    encoded_catalog = _catalog_bytes()
    canonical_catalog = catalog.decode_service_catalog_v1(encoded_catalog)
    autonomous_catalog = preflight._decode_service_catalog_v1(encoded_catalog)

    snapshot = preflight._compile_candidate_units_v1(autonomous_catalog)
    independent_document = _expected_candidate_document(canonical_catalog)
    independent_hash = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.candidate-units/v1\0"
        + independent_document
    ).hexdigest()

    assert snapshot.encoded == independent_document
    assert snapshot.candidate_units_hash == independent_hash
    assert tuple(sorted(
        (
            (link.path, link.target)
            for entry in snapshot.entries
            for link in entry.enablement_links
        ),
        key=lambda item: item[0].encode("utf-8"),
    )) == _EXPECTED_ENABLEMENT_LINKS


def test_signed_isolated_g6c_recipe_has_one_closed_namespace_and_no_links() -> None:
    encoded, descriptor = _isolated_g6c_records()
    autonomous_catalog = preflight._decode_service_catalog_v1(encoded)
    autonomous_descriptor = preflight._decode_deployment_descriptor_v1(
        assembler.encode_deployment_descriptor_v1(descriptor)
    )

    assert preflight._service_source_identity_v1(
        autonomous_catalog, autonomous_descriptor,
    ) == preflight._ISOLATED_G6C_SOURCE_IDENTITY_V1
    candidate = preflight._compile_candidate_units_v1(autonomous_catalog)
    assert len(candidate.entries) == 2
    assert not any(item.enablement_links for item in candidate.entries)


def test_signed_isolated_g6c_recipe_rejects_one_fully_rehashed_mutant() -> None:
    encoded, descriptor = _isolated_g6c_records(description="mutated probe")
    autonomous_catalog = preflight._decode_service_catalog_v1(encoded)
    autonomous_descriptor = preflight._decode_deployment_descriptor_v1(
        assembler.encode_deployment_descriptor_v1(descriptor)
    )

    with pytest.raises(preflight.PreflightError) as failure:
        preflight._service_source_identity_v1(
            autonomous_catalog, autonomous_descriptor,
        )
    assert failure.value.code == preflight.CODE_INVALID


def test_administrative_bundle_hash_matches_framing_and_changes_with_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_descriptor = _deployment_record()
    encoded = assembler.encode_deployment_descriptor_v1(canonical_descriptor)
    autonomous_descriptor = preflight._decode_deployment_descriptor_v1(encoded)

    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        if name == "executor_birth_distribution_assembler":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", isolated_import)

    with pytest.raises(preflight.PreflightError) as invalid:
        preflight._administrative_bundle_hash_v1(object())
    assert invalid.value.detail == "administrative bundle descriptor"

    observed = preflight._administrative_bundle_hash_v1(
        autonomous_descriptor
    )
    assert observed == _independent_administrative_bundle_hash(
        canonical_descriptor.artifacts
    )

    changed_artifacts = list(canonical_descriptor.artifacts)
    changed_artifacts[0] = dataclasses.replace(
        changed_artifacts[0], content_hash=D("d"),
    )
    changed_descriptor = _deployment_with_artifacts(tuple(changed_artifacts))
    changed_encoded = assembler.encode_deployment_descriptor_v1(
        changed_descriptor
    )
    changed_autonomous = preflight._decode_deployment_descriptor_v1(
        changed_encoded
    )
    changed_hash = preflight._administrative_bundle_hash_v1(
        changed_autonomous
    )

    assert changed_hash == _independent_administrative_bundle_hash(
        changed_descriptor.artifacts
    )
    assert changed_hash != observed


def test_structural_prerequisite_accepts_supported_255_minor_differentially() -> None:
    original = _prerequisite_record()
    record = assembler.build_startup_prerequisite_v1(
        request_id=original.request_id,
        closed_build_id=original.closed_build_id,
        release_sequence=original.release_sequence,
        deployment_descriptor_id=original.deployment_descriptor_id,
        predecessor_id=original.predecessor_id,
        administrative_bundle_hash=original.administrative_bundle_hash,
        python_binary_hash=original.python_binary_hash,
        openssl_binary_hash=original.openssl_binary_hash,
        openssl_tcb_hash=original.openssl_tcb_hash,
        systemctl_binary_hash=original.systemctl_binary_hash,
        systemd_analyze_binary_hash=original.systemd_analyze_binary_hash,
        service_catalog_id=original.service_catalog_id,
        service_coverage_hash=original.service_coverage_hash,
        systemd_manager_version="255.5",
        candidate_units_hash=original.candidate_units_hash,
        effective_units_hash=original.effective_units_hash,
    )
    encoded = assembler.encode_startup_prerequisite_v1(record)
    expected = assembler.decode_startup_prerequisite_v1(encoded)
    observed = preflight._decode_startup_prerequisite_v1(encoded)
    _assert_record_fields_match(observed, expected)


def _bind_graph(graph: Mapping[str, object]) -> object:
    return preflight._bind_preflight_materials_for_test_v1(
        graph["distribution"], graph["transaction"], graph["predecessor"],
        graph["captured"], graph["prerequisite_encoded"],
    )


def _receipts_complete_transaction(graph: Mapping[str, object]):
    return graph["transaction"]._replace(
        sequence=1,
        state="RECEIPTS_COMPLETE",
        startup_prerequisite_id=None,
        startup_prerequisite_digest=None,
        cutover_id=None,
        catalog_id=None,
        certificate_payload_hash=None,
        certificate_signature_hash=None,
        dominant_startup_receipt=None,
        installed_tree_hash=None,
    )


def test_pure_material_binder_accepts_one_fully_rebound_product_graph() -> None:
    graph = _bound_graph()
    decoded_catalog = graph["decoded_catalog"]
    target_bytes = graph["target_bytes"]
    assert isinstance(decoded_catalog, catalog.DecodedServiceCatalogV1)
    assert isinstance(target_bytes, Mapping)
    for entry in decoded_catalog.entries:
        if entry.target_executable is None:
            continue
        assert entry.target_executable_hash == catalog.target_executable_hash_v1(
            entry.target_executable, target_bytes[entry.target_executable],
        )

    result = _bind_graph(graph)
    assert type(result) is preflight._BoundPreflightMaterialsForTestV1
    materials = result.materials
    assert materials.catalog.catalog_id == decoded_catalog.catalog_id
    assert len(materials.unit_fragments) == sum(
        entry.unit_spec is not None for entry in decoded_catalog.entries
    )
    assert materials.prerequisite.candidate_units_hash == (
        materials.candidate_units.candidate_units_hash
    )


def test_candidate_binder_is_available_at_receipts_complete() -> None:
    graph = _bound_graph()
    transaction = _receipts_complete_transaction(graph)

    candidate = preflight._bind_candidate_cutover_materials_core_v1(
        graph["distribution"], transaction, graph["predecessor"],
        graph["captured"],
    )

    assert type(candidate) is preflight._CandidateCutoverMaterialsV1
    assert candidate.transaction is transaction
    assert candidate.predecessor is graph["predecessor"]
    assert candidate.candidate_units.candidate_units_hash == (
        assembler.decode_startup_prerequisite_v1(
            graph["prerequisite_encoded"],
        ).candidate_units_hash
    )


def test_pending_cutover_selection_uses_exact_authenticated_bytes() -> None:
    graph = _bound_graph()
    transaction = _receipts_complete_transaction(graph)
    encoded = preflight._canonical_json(transaction.as_value())
    prepared = transaction._replace(sequence=0, state="PREPARED")
    verified = transaction._replace(sequence=6, state="PREFLIGHT_VERIFIED")
    claim = preflight._DecodedSuccessorClaimV1(
        transaction.successor_claim_id,
        transaction.previous_head_id,
        transaction.release_sequence,
        transaction.request_id,
        transaction.source_id,
        transaction.closed_build_id,
    )
    authenticated_transaction = preflight._AuthenticatedTransactionSnapshotV2(
        claim,
        preflight._DecodedCoordinatorPrefixV2(
            (prepared, transaction, verified),
            (b"prepared", encoded, b"verified"),
        ),
    )
    snapshot = preflight._ReconciledFixedOwnershipSnapshotV1(
        (), None, None, (graph["distribution"],), (), (), (claim,),
        (authenticated_transaction,), (), None, None, graph["predecessor"],
    )

    build, selected, predecessor = (
        preflight._select_cutover_candidate_from_snapshot_v2(
            snapshot,
            complete_encoded=encoded,
            request_id=transaction.request_id,
            closed_build_id=transaction.closed_build_id,
            release_sequence=transaction.release_sequence,
            distribution_encoded=graph["distribution"].encoded,
            distribution_signature=graph["distribution"].signature,
        )
    )

    assert build is graph["distribution"]
    assert selected is transaction
    assert predecessor is graph["predecessor"]


def test_cutover_prerequisite_is_derived_from_captured_facts(monkeypatch) -> None:
    graph = _bound_graph()
    transaction = _receipts_complete_transaction(graph)
    candidate = preflight._bind_candidate_cutover_materials_core_v1(
        graph["distribution"], transaction, graph["predecessor"],
        graph["captured"],
    )
    tcb = preflight._CapturedAdministrativeTcbV1(
        SimpleNamespace(
            python_binary_hash=D("1"),
            openssl_binary_hash=D("2"),
            systemctl_binary_hash=D("3"),
            systemd_analyze_binary_hash=D("4"),
        ),
        SimpleNamespace(openssl_tcb_hash=D("5")),
    )
    prepared = preflight._PreparedCutoverCandidateV2(
        candidate, tcb, preflight._PREPARED_CUTOVER_CANDIDATE_SEAL_V2,
    )
    effective = preflight._CapturedEffectiveSystemdUnitsV1(
        "255.4-1ubuntu8.17",
        preflight._EffectiveSystemdUnitsSnapshotV1((), b"effective", D("6")),
        (), (),
    )
    revalidated = []
    monkeypatch.setattr(
        preflight, "_revalidate_captured_administrative_tcb_v1",
        lambda *args, **kwargs: revalidated.append("tcb"),
    )
    monkeypatch.setattr(
        preflight, "_revalidate_captured_effective_systemd_v1",
        lambda *args, **kwargs: revalidated.append("systemd"),
    )

    prerequisite = preflight._build_startup_prerequisite_for_cutover_v2(
        prepared, effective,
    )

    assert prerequisite.request_id == transaction.request_id
    assert prerequisite.predecessor_id == graph["predecessor"].predecessor_id
    assert prerequisite.candidate_units_hash == (
        candidate.candidate_units.candidate_units_hash
    )
    assert prerequisite.effective_units_hash == D("6")
    assert revalidated == ["tcb", "systemd", "tcb", "systemd"]


@pytest.mark.parametrize(
    ("mutation", "detail"),
    (
        ("missing-artifact", "preflight deployment artifact coverage"),
        ("extra-artifact", "preflight service unit coverage"),
        ("wrong-kind", "preflight unit artifact kind"),
        ("fragment-drift", "preflight unit fragment catalog binding"),
        ("extra-install-target", "candidate enablement target"),
        ("manager-255.5", "preflight material cross binding"),
        (
            "service-home-inside-release",
            "service home inside installation root",
        ),
        (
            "manifest-target-size",
            "preflight distribution target executable",
        ),
    ),
)
def test_pure_material_binder_rejects_fully_rebound_semantic_mutants(
    mutation: str, detail: str,
) -> None:
    graph = _bound_graph(mutation)
    with pytest.raises(preflight.PreflightError) as failure:
        _bind_graph(graph)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == detail


@pytest.mark.parametrize("mutation", ("missing", "altered"))
def test_pure_material_binder_requires_exact_captured_preflight_bytes(
    mutation: str,
) -> None:
    graph = _bound_graph()
    captured = dict(graph["captured"])
    if mutation == "missing":
        captured.pop("deployment/admin/preflight.py")
    else:
        captured["deployment/admin/preflight.py"] = b"preflight-v2"
    graph["captured"] = captured

    with pytest.raises(preflight.PreflightError) as failure:
        _bind_graph(graph)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == "preflight deployment artifact file binding"


@pytest.mark.parametrize(
    "mutation", (
        "recipe-description", "recipe-restart", "pre-normalized-marker",
    ),
)
def test_pure_material_binder_rejects_fully_rebound_source_recipe_mutants(
    mutation: str,
) -> None:
    graph = _bound_graph(mutation)

    with pytest.raises(preflight.PreflightError) as failure:
        _bind_graph(graph)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == "service source recipe"


def test_pure_material_binder_rejects_fully_rebound_descriptor_account() -> None:
    graph = _bound_graph("descriptor-account")

    with pytest.raises(preflight.PreflightError) as failure:
        _bind_graph(graph)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == "service user binding"


def test_pure_material_binder_rejects_release_one_predecessor_catalog_drift(
) -> None:
    graph = _bound_graph("release1-predecessor")

    with pytest.raises(preflight.PreflightError) as failure:
        _bind_graph(graph)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.detail == "preflight material cross binding"


def test_pure_material_binder_accepts_release_two_initial_predecessor_catalog(
) -> None:
    graph = _bound_graph("release2-initial-predecessor")
    predecessor = graph["predecessor"]
    decoded_catalog = graph["decoded_catalog"]
    assert predecessor.service_catalog_id != decoded_catalog.catalog_id
    assert (
        predecessor.service_coverage_hash
        != decoded_catalog.service_coverage_hash
    )

    result = _bind_graph(graph)
    assert type(result) is preflight._BoundPreflightMaterialsForTestV1


def _catalog_relational_mutant() -> bytes:
    value = json.loads(_catalog_bytes())
    timer = next(
        item for item in value["entries"] if item["class"] == "gated_timer"
    )
    target = next(
        item for item in value["entries"] if item["class"] == "target"
    )
    timer["timer_target"] = target["entry_id"]
    return _reidentify(
        _canonical(value), "catalog_id", catalog.CATALOG_ID_DOMAIN,
    )


def _deployment_binding_mutant() -> bytes:
    encoded = assembler.encode_deployment_descriptor_v1(_deployment_record())
    value = json.loads(encoded)
    value["installation_root"] = (
        "/var/lib/metnos/executor-birth/releases-v1/00000000000000000003"
    )
    return _reidentify(
        _canonical(value), "descriptor_id",
        assembler.DEPLOYMENT_DESCRIPTOR_ID_DOMAIN_V1,
    )


def _prerequisite_version_mutant() -> bytes:
    encoded = assembler.encode_startup_prerequisite_v1(
        _prerequisite_record()
    )
    value = json.loads(encoded)
    value["systemd_manager_version"] = "254.1"
    return _reidentify(
        _canonical(value), "prerequisite_id",
        assembler.STARTUP_PREREQUISITE_ID_DOMAIN_V1,
    )


@pytest.mark.parametrize(
    ("encoded", "oracle", "oracle_error", "autonomous_name"),
    (
        (
            _catalog_relational_mutant,
            catalog.decode_service_catalog_v1,
            catalog.ServiceCatalogError,
            "_decode_service_catalog_v1",
        ),
        (
            _deployment_binding_mutant,
            assembler.decode_deployment_descriptor_v1,
            assembler.DistributionAssemblerError,
            "_decode_deployment_descriptor_v1",
        ),
        (
            _prerequisite_version_mutant,
            assembler.decode_startup_prerequisite_v1,
            assembler.DistributionAssemblerError,
            "_decode_startup_prerequisite_v1",
        ),
    ),
    ids=("catalog-relation", "descriptor-root", "prerequisite-version"),
)
def test_autonomous_material_codecs_reject_self_reidentified_mutants(
    encoded: Callable[[], bytes],
    oracle: Callable[[bytes], object],
    oracle_error: type[Exception],
    autonomous_name: str,
) -> None:
    material = encoded()
    with pytest.raises(oracle_error):
        oracle(material)
    autonomous = getattr(preflight, autonomous_name)
    with pytest.raises(preflight.PreflightError) as failure:
        autonomous(material)
    assert failure.value.code == preflight.CODE_INVALID
