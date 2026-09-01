"""Focused certification of the fixed received-source release builder."""
from __future__ import annotations

import sys
import shutil
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
runtime_root = ROOT / "runtime"
if str(runtime_root) not in sys.path:
    sys.path.insert(0, str(runtime_root))

import executor_birth_distribution_assembler as assembler
import executor_birth_distribution_manifest as manifest
from install import executor_birth_distribution_release as release


def _source_tree(tmp_path: Path):
    inventory = b'{"birth_closed":{},"entries":[],"scan_roots":[],"schema":"x","source_census":"x"}'
    values = {
        release.BOUNDARY_INVENTORY_SOURCE_PATH_V1: inventory,
        "requirements.txt": b"fixture==1\n",
        "runtime/__version__.py": b'__version__ = "1.2.3"\n',
        "runtime/contract_boundary_guard.py": b"GUARD = 1\n",
        "runtime/contract_store.py": b"STORE = 1\n",
        "runtime/executor_birth.py": b"BIRTH = 1\n",
        "runtime/executor_birth_admin_preflight.py": b"PREFLIGHT = 1\n",
        "runtime/executor_birth_distribution_manifest.py": b"VERIFY = 1\n",
        "runtime/executor_birth_ownership_preflight.py": b"OWNERSHIP = 1\n",
        "runtime/sign.py": b"SIGN = 1\n",
        "runtime/bin/llama-server": b"#!/bin/sh\nexit 0\n",
        "docs/en/index.html": b"<!doctype html><title>Metnos</title>\n",
        "tutor/sources.toml": b"version = 1\n",
    }
    root = tmp_path / "source"
    root.mkdir(mode=0o755)
    files = []
    for relative, content in sorted(values.items()):
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        mode = 0o755 if relative == release.LLAMA_SOURCE_PATH_V1 else 0o644
        path.write_bytes(content)
        path.chmod(mode)
        files.append(assembler.ReceivedSourceFileV1(
            relative, len(content),
            assembler.received_source_file_hash_v1(
                relative, len(content), (content,),
            ),
            mode,
        ))
    for directory in (item for item in root.rglob("*") if item.is_dir()):
        directory.chmod(0o755)
    return root, assembler.build_received_source_v1("metnos", tuple(files))


def _account() -> release._ServiceAccountV1:
    return release._ServiceAccountV1(
        "metnos", 1001, 1001, (1001,), "/srv/metnos", "/usr/sbin/nologin",
    )


def test_assembly_derives_catalog_descriptor_manifest_and_exact_repetition(
    tmp_path, monkeypatch,
) -> None:
    source_root, source = _source_tree(tmp_path)
    releases = tmp_path / "releases-v1"
    releases.mkdir(mode=0o755)
    monkeypatch.setattr(
        assembler, "DEFAULT_RELEASE_ROOT_TEXT_V1", releases.as_posix(),
    )
    private = Ed25519PrivateKey.generate()
    key_id = manifest.distribution_key_id(private.public_key())
    values = dict(
        source=source, source_root=source_root, account=_account(),
        edge=release._ReleaseEdgeV1(1, None), signing_key_id=key_id,
        release_directory=releases, root_owned=False,
    )
    first = release._assemble_staging_v1(**values)
    second = release._assemble_staging_v1(**values)
    assert first == second
    document, files = manifest._parse(first.encoded)
    assert document["installation_root"] == (releases / f"{1:020d}").as_posix()
    assert files == first.files
    by_path = {item.path: item for item in files}
    assert by_path["requirements.lock"].role == "dependency_lock"
    assert by_path[release.ADMIN_PREFLIGHT_RELEASE_PATH_V1].role == "preflight"
    assert by_path[release.PUBLICATION_INDEX_SOURCE_PATH_V1].role == (
        "public_document"
    )
    assert by_path[release.TUTOR_SOURCES_SOURCE_PATH_V1].role == (
        "tutor_material"
    )
    assert by_path[assembler.DEPLOYMENT_DESCRIPTOR_PATH_V1].role == (
        "deployment_descriptor"
    )
    descriptor = assembler.decode_deployment_descriptor_v1(
        first.staging_root.joinpath(
            *assembler.DEPLOYMENT_DESCRIPTOR_PATH_V1.split("/"),
        ).read_bytes()
    )
    assert descriptor.installation_root == document["installation_root"]
    assert {item.source_path for item in descriptor.artifacts} == {
        release.ADMIN_PREFLIGHT_RELEASE_PATH_V1,
        *(item.path for item in files if item.role == "service_unit"),
    }


def test_release_edge_resumes_same_source_and_advances_only_after_completion() -> None:
    source_id = "sha256:" + "1" * 64
    pending = SimpleNamespace(
        source_id=source_id, release_sequence=1,
    )
    graph = SimpleNamespace(
        pending_claims=(pending,), transactions=(), claims=(pending,),
    )
    assert release._next_release_edge_v1(graph, source_id) == (
        release._ReleaseEdgeV1(1, None)
    )
    with pytest.raises(assembler.DistributionAssemblerError, match="successor edge"):
        release._next_release_edge_v1(graph, "sha256:" + "2" * 64)

    claim = SimpleNamespace(source_id=source_id, release_sequence=2)
    latest = SimpleNamespace(
        previous_closed_build_id="sha256:" + "a" * 64,
        closed_build_id="sha256:" + "b" * 64,
        release_sequence=2,
        state=release.OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED,
        sequence=6,
    )
    transaction = SimpleNamespace(claim=claim, latest=latest)
    completed = SimpleNamespace(
        pending_claims=(), transactions=(transaction,), claims=(claim,),
    )
    assert release._next_release_edge_v1(completed, source_id) == (
        release._ReleaseEdgeV1(2, latest.previous_closed_build_id)
    )
    assert release._next_release_edge_v1(
        completed, "sha256:" + "3" * 64,
    ) == release._ReleaseEdgeV1(3, latest.closed_build_id)


def test_product_entry_refuses_non_root_before_fixed_storage(monkeypatch) -> None:
    monkeypatch.setattr(release.os, "geteuid", lambda: 1000)
    with pytest.raises(assembler.DistributionAssemblerError, match="root required"):
        release.build_and_install_received_source_v1("sha256:" + "1" * 64)


def test_current_reviewed_source_assembles_and_passes_static_verification(
    tmp_path, monkeypatch,
) -> None:
    source_root = tmp_path / "reviewed-source"
    source_root.mkdir(mode=0o755)
    selected = []
    for base in sorted(release._SOURCE_ROOTS_V1):
        for original in sorted((ROOT / base).rglob("*")):
            if not original.is_file():
                continue
            relative = original.relative_to(ROOT).as_posix()
            if release._projected_source_path_v1(relative) is None:
                continue
            destination = source_root.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            destination.chmod(
                0o755 if original.stat().st_mode & 0o111 else 0o644
            )
            selected.append(relative)
    for relative in (
        release.BOUNDARY_INVENTORY_SOURCE_PATH_V1,
        release.DEPENDENCY_SOURCE_PATH_V1,
    ):
        original = ROOT.joinpath(*relative.split("/"))
        destination = source_root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        destination.chmod(0o644)
        selected.append(relative)
    llama = source_root.joinpath(*release.LLAMA_SOURCE_PATH_V1.split("/"))
    llama.parent.mkdir(parents=True, exist_ok=True)
    llama.write_bytes(b"#!/bin/sh\nexit 0\n")
    llama.chmod(0o755)
    selected.append(release.LLAMA_SOURCE_PATH_V1)
    for directory in (item for item in source_root.rglob("*") if item.is_dir()):
        directory.chmod(0o755)

    source_files = []
    for relative in sorted(set(selected), key=lambda item: item.encode("utf-8")):
        path = source_root.joinpath(*relative.split("/"))
        content = path.read_bytes()
        source_files.append(assembler.ReceivedSourceFileV1(
            relative, len(content),
            assembler.received_source_file_hash_v1(
                relative, len(content), (content,) if content else (),
            ),
            path.stat().st_mode & 0o777,
        ))
    source = assembler.build_received_source_v1(
        "metnos", tuple(source_files),
    )
    releases = tmp_path / "reviewed-releases-v1"
    releases.mkdir(mode=0o755)
    monkeypatch.setattr(
        assembler, "DEFAULT_RELEASE_ROOT_TEXT_V1", releases.as_posix(),
    )
    private = Ed25519PrivateKey.generate()
    key_id = manifest.distribution_key_id(private.public_key())
    staged = release._assemble_staging_v1(
        source=source, source_root=source_root, account=_account(),
        edge=release._ReleaseEdgeV1(1, None), signing_key_id=key_id,
        release_directory=releases, root_owned=False,
    )
    registry = manifest.DistributionRegistry({
        key_id: manifest.DistributionKey(
            key_id, private.public_key(), frozenset({manifest.PURPOSE}),
        ),
    })
    signature = private.sign(manifest.SIGNATURE_DOMAIN + staged.encoded)
    verified = manifest._verify_distribution_manifest_for_test(
        staged.encoded, signature, registry=registry,
        _environment=manifest._environment_for_test(
            "linux", platform.machine().replace("amd64", "x86_64"),
            staged.staging_root,
            claimed_installation_root=staged.final_root.as_posix(),
            verify_static_boundary=True,
        ),
    )
    assert verified.files == staged.files
