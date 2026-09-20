"""Compact independent oracles for the RM-0008 B3 metadata codecs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

import executor_birth_distribution_assembler as assembler


def D(character: str) -> str:
    return "sha256:" + character * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def independent_id(encoded: bytes, field: str, domain: bytes) -> str:
    document = json.loads(encoded)
    del document[field]
    return "sha256:" + hashlib.sha256(domain + canonical(document)).hexdigest()


def artifact(
    name: str, kind: str, *, size: int = 3,
) -> assembler.DeploymentArtifactV1:
    if kind == "administrative_program":
        return assembler.DeploymentArtifactV1(
            f"deployment/admin/{name}",
            f"/usr/libexec/metnos/executor-birth-v1/{name}", kind,
            "group6_admin", size, D("a"), 0o755, 0, 0,
        )
    return assembler.DeploymentArtifactV1(
        f"deployment/systemd/{name}", f"/etc/systemd/system/{name}", kind,
        "group7_cutover", size, D("b"), 0o644, 0, 0,
    )


def deployment() -> assembler.DeploymentDescriptorV1:
    return assembler.build_deployment_descriptor_v1(
        release_sequence=2, service_user="metnos", service_uid=991,
        service_gid=991, service_supplementary_gids=(44, 991),
        service_home="/var/lib/metnos", service_shell="/usr/sbin/nologin",
        artifacts=(
            artifact("metnos.target", "target_unit"),
            artifact("preflight.py", "administrative_program"),
        ),
        service_catalog_id=D("c"), service_coverage_hash=D("d"),
        python_executable="/usr/bin/python3.12",
        openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )


def environment(
    name: str = "LC_ALL", value: str = "C",
) -> assembler.ServiceCommandEnvironmentV1:
    return assembler.ServiceCommandEnvironmentV1(name, value)


def command(
    entry_id: str = "worker",
) -> assembler.PredecessorServiceCommandV1:
    return assembler.PredecessorServiceCommandV1(
        entry_id, "python_module", "/usr/bin/python3.12", D("e"),
        "runtime.worker", ("--once", ""), "/opt/metnos", (environment(),),
    )


def predecessor() -> assembler.PredecessorDescriptorV1:
    return assembler.build_predecessor_descriptor_v1(
        transaction_id=D("f"), installation_root="/opt/metnos",
        files=(
            assembler.PredecessorFileV1("runtime/worker.py", 19, D("1")),
            assembler.PredecessorFileV1("pyproject.toml", 51, D("2")),
        ),
        service_commands=(
            command(),
            assembler.PredecessorServiceCommandV1(
                "metnos-target", "none", None, None, None, (), None, (),
            ),
        ),
        administrative_bundle_hash=D("3"), service_catalog_id=D("4"),
        service_coverage_hash=D("5"),
    )


def prerequisite() -> assembler.StartupPrerequisiteV1:
    return assembler.build_startup_prerequisite_v1(
        request_id=D("6"), closed_build_id=D("7"), release_sequence=2,
        deployment_descriptor_id=deployment().descriptor_id,
        predecessor_id=predecessor().predecessor_id,
        administrative_bundle_hash=D("3"), python_binary_hash=D("8"),
        openssl_binary_hash=D("9"), openssl_tcb_hash=D("a"),
        systemctl_binary_hash=D("b"), systemd_analyze_binary_hash=D("c"),
        service_catalog_id=D("4"), service_coverage_hash=D("5"),
        systemd_manager_version="255.4-1ubuntu8.17",
        candidate_units_hash=D("d"), effective_units_hash=D("e"),
    )


def test_deployment_codec_round_trip_and_independent_identity() -> None:
    record = deployment()
    encoded = assembler.encode_deployment_descriptor_v1(record)
    assert assembler.decode_deployment_descriptor_v1(encoded) == record
    assert record.installation_root == (
        "/var/lib/metnos/executor-birth/releases-v1/00000000000000000002"
    )
    assert [item.destination_path for item in record.artifacts] == sorted(
        item.destination_path for item in record.artifacts
    )
    assert record.descriptor_id == independent_id(
        encoded, "descriptor_id",
        b"metnos.executor-birth.deployment-descriptor/v1\0",
    )


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(release_sequence=True),
    lambda value: value.update(installation_root="/tmp/release"),
    lambda value: value["artifacts"][0].update(uid=1),
    lambda value: value["artifacts"][0].update(source_path="other"),
    lambda value: value.update(extra=True),
])
def test_deployment_decoder_fails_closed(mutate) -> None:
    value = json.loads(assembler.encode_deployment_descriptor_v1(deployment()))
    mutate(value)
    with pytest.raises(assembler.DistributionAssemblerError):
        assembler.decode_deployment_descriptor_v1(canonical(value))


@pytest.mark.parametrize("artifacts", [
    (
        artifact("attacker", "administrative_program"),
        artifact("metnos.target", "target_unit"),
    ),
    (artifact("preflight.py", "administrative_program"),),
    (artifact("metnos.target", "target_unit"),),
    (
        artifact("preflight.py", "administrative_program"),
        artifact("bad space.service", "service_unit"),
    ),
    (
        artifact("preflight.py", "administrative_program"),
        artifact("bad%.service", "service_unit"),
    ),
    (
        artifact("preflight.py", "administrative_program"),
        artifact("a" * 185 + ".service", "service_unit"),
    ),
])
def test_deployment_artifact_surface_is_closed(artifacts) -> None:
    with pytest.raises(assembler.DistributionAssemblerError):
        assembler.build_deployment_descriptor_v1(
            release_sequence=2, service_user="metnos", service_uid=991,
            service_gid=991, service_supplementary_gids=(44, 991),
            service_home="/var/lib/metnos", service_shell="/usr/sbin/nologin",
            artifacts=artifacts, service_catalog_id=D("c"),
            service_coverage_hash=D("d"),
            python_executable="/usr/bin/python3.12",
            openssl_executable="/usr/bin/openssl",
            systemctl_executable="/usr/bin/systemctl",
            systemd_analyze_executable="/usr/bin/systemd-analyze",
        )


def test_predecessor_codec_round_trip_and_independent_identity() -> None:
    record = predecessor()
    encoded = assembler.encode_predecessor_descriptor_v1(record)
    assert assembler.decode_predecessor_descriptor_v1(encoded) == record
    assert [item.path for item in record.files] == [
        "pyproject.toml", "runtime/worker.py",
    ]
    assert [item.entry_id for item in record.service_commands] == [
        "metnos-target", "worker",
    ]
    assert record.predecessor_id == independent_id(
        encoded, "predecessor_id",
        b"metnos.executor-birth.predecessor-descriptor/v1\0",
    )


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(transaction_id="not-a-digest"),
    lambda value: value["files"].reverse(),
    lambda value: value["service_commands"][1]["target_environment"][0].update(
        name="PYTHONPATH",
    ),
    lambda value: value["service_commands"][1].update(python_module=None),
    lambda value: value.update(predecessor_id=D("0")),
])
def test_predecessor_decoder_fails_closed(mutate) -> None:
    value = json.loads(assembler.encode_predecessor_descriptor_v1(predecessor()))
    mutate(value)
    with pytest.raises(assembler.DistributionAssemblerError):
        assembler.decode_predecessor_descriptor_v1(canonical(value))


def test_stop_command_is_closed_and_root_working_directory_is_valid() -> None:
    stop = assembler.PredecessorServiceCommandV1(
        "quarantine", "systemctl_stop", "/usr/bin/systemctl", D("f"), None,
        ("stop", "metnos-http.service", "metnos-worker.service"), "/", (),
    )
    record = assembler.build_predecessor_descriptor_v1(
        transaction_id=D("1"), installation_root="/opt/metnos",
        files=(assembler.PredecessorFileV1("runtime/a.py", 1, D("2")),),
        service_commands=(stop,), administrative_bundle_hash=D("3"),
        service_catalog_id=D("4"), service_coverage_hash=D("5"),
    )
    assert assembler.decode_predecessor_descriptor_v1(
        assembler.encode_predecessor_descriptor_v1(record)
    ) == record
    with pytest.raises(assembler.DistributionAssemblerError):
        assembler.build_predecessor_descriptor_v1(
            transaction_id=D("1"), installation_root="/opt/metnos",
            files=record.files,
            service_commands=(replace(
                stop, target_environment=(environment(),),
            ),), administrative_bundle_hash=D("3"),
            service_catalog_id=D("4"), service_coverage_hash=D("5"),
        )


def test_startup_codec_round_trip_and_independent_identity() -> None:
    record = prerequisite()
    encoded = assembler.encode_startup_prerequisite_v1(record)
    assert assembler.decode_startup_prerequisite_v1(encoded) == record
    assert record.prerequisite_id == independent_id(
        encoded, "prerequisite_id",
        b"metnos.executor-birth.startup-prerequisite/v1\0",
    )


@pytest.mark.parametrize("field,value", [
    ("release_sequence", False),
    ("systemd_manager_version", "254.1"),
    ("predecessor_id", None),
    ("candidate_units_hash", "SHA256:" + "a" * 64),
    ("prerequisite_id", D("0")),
])
def test_startup_decoder_fails_closed(field: str, value: object) -> None:
    document = json.loads(
        assembler.encode_startup_prerequisite_v1(prerequisite())
    )
    document[field] = value
    with pytest.raises(assembler.DistributionAssemblerError):
        assembler.decode_startup_prerequisite_v1(canonical(document))


def test_all_three_codecs_reject_noncanonical_and_duplicate_json() -> None:
    pairs = (
        (
            assembler.encode_deployment_descriptor_v1(deployment()),
            assembler.decode_deployment_descriptor_v1,
        ),
        (
            assembler.encode_predecessor_descriptor_v1(predecessor()),
            assembler.decode_predecessor_descriptor_v1,
        ),
        (
            assembler.encode_startup_prerequisite_v1(prerequisite()),
            assembler.decode_startup_prerequisite_v1,
        ),
    )
    for encoded, decoder in pairs:
        duplicate = b'{"schema_version":1,' + encoded[1:]
        for invalid in (encoded + b"\n", duplicate):
            with pytest.raises(assembler.DistributionAssemblerError):
                decoder(invalid)
