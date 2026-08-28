from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_ownership_authorities as authority_module
import install.birth_ownership_authority_provisioner as provisioner_module
from executor_birth_distribution_manifest import DistributionRegistry
from executor_birth_ownership_authorities import (
    OwnershipAuthorityError, OwnershipPublicRegistriesV1,
    _PRIVATE_BASENAMES, _REGISTRY_BASENAMES, _load_private_at_v1,
    _load_public_at_v1,
    decode_ownership_registry_v1, encode_ownership_registry_v1,
)
from executor_birth_ownership_chain import OwnershipChainStore
from executor_birth_ownership_cutover import OwnershipCutoverRegistry
from install.birth_ownership_authority_provisioner import (
    _provision_ownership_authorities_at_v1,
)


linux_managed = pytest.mark.skipif(
    os.name == "nt", reason="managed root provisioning is Linux-only",
)


def _root(path: Path) -> Path:
    path.chmod(0o755)
    return path


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in sorted(path.iterdir())
        if item.is_file()
    }


def _subprocess_environment() -> dict[str, str]:
    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((
        str(repository), str(repository / "runtime"),
    ))
    return environment


def test_product_authority_surface_is_explicitly_linux_only(monkeypatch):
    monkeypatch.setattr(
        authority_module, "_managed_authority_platform_supported_v1",
        lambda: False,
    )
    monkeypatch.setattr(
        provisioner_module, "_managed_authority_platform_supported_v1",
        lambda: False,
    )
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        authority_module.load_ownership_public_registries_v1()
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        authority_module.load_root_ownership_authorities_v1()
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        provisioner_module.provision_root_ownership_authorities_v1()


@pytest.mark.parametrize("kind", ["distribution", "cutover", "head"])
def test_registry_codec_is_canonical_single_purpose_and_derived(kind):
    private = Ed25519PrivateKey.generate()
    encoded = encode_ownership_registry_v1(kind, private.public_key())
    parsed = json.loads(encoded)
    assert parsed["authority"] == kind
    assert len(parsed["purposes"]) == 1
    assert json.dumps(
        parsed, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii") == encoded
    registry = decode_ownership_registry_v1(encoded, expected_kind=kind)
    assert isinstance(
        registry,
        DistributionRegistry if kind == "distribution" else OwnershipCutoverRegistry,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra", "duplicate", "purpose", "key_id", "schema_bool",
        "schema_float", "base64_pad_bits",
    ],
)
def test_registry_codec_rejects_noncanonical_or_cross_purpose(mutation):
    private = Ed25519PrivateKey.generate()
    encoded = encode_ownership_registry_v1("cutover", private.public_key())
    value = json.loads(encoded)
    if mutation == "extra":
        value["extra"] = True
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    elif mutation == "duplicate":
        changed = encoded[:-1] + b',"schema_version":1}'
    elif mutation == "purpose":
        value["purposes"] = ["ownership_head_v1"]
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    elif mutation == "key_id":
        value["key_id"] = "birth-ed25519-v1-sha256-" + "0" * 64
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    elif mutation == "schema_bool":
        value["schema_version"] = True
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    elif mutation == "schema_float":
        value["schema_version"] = 1.0
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    else:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        raw = value["public_key"]
        index = alphabet.index(raw[-2])
        value["public_key"] = raw[:-2] + alphabet[index + 1] + "="
        changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(OwnershipAuthorityError, match="authority_invalid"):
        decode_ownership_registry_v1(changed, expected_kind="cutover")


@pytest.mark.parametrize("non_integer", [True, 1.0])
def test_distribution_registry_rejects_non_integer_first_sequence(non_integer):
    private = Ed25519PrivateKey.generate()
    value = json.loads(encode_ownership_registry_v1(
        "distribution", private.public_key(),
    ))
    value["first_release_sequence"] = non_integer
    changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(OwnershipAuthorityError, match="authority_invalid"):
        decode_ownership_registry_v1(changed, expected_kind="distribution")


@linux_managed
def test_provisioner_cold_loads_three_distinct_authorities_and_exact_retry(
    tmp_path, monkeypatch,
):
    root = _root(tmp_path)
    result = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    directory = root / "authorities-v1"
    before = _tree_bytes(directory)
    cold = _load_private_at_v1(directory, root_owned=False)
    cold_loads = []

    def load_cold():
        cold_loads.append(True)
        return cold.public

    monkeypatch.setattr(
        authority_module, "load_ownership_public_registries_v1", load_cold,
    )
    chain = OwnershipChainStore.initialize(root / "chain-v1")
    repeated = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    assert isinstance(cold.public, OwnershipPublicRegistriesV1)
    assert _tree_bytes(directory) == before
    raw = {
        result.distribution_private.public_key().public_bytes_raw(),
        result.cutover_private.public_key().public_bytes_raw(),
        result.head_private.public_key().public_bytes_raw(),
    }
    assert len(raw) == 3
    assert repeated.public == cold.public
    assert cold_loads == [True]
    assert chain.cutover_registry is cold.public.cutover
    assert chain.head_registry is cold.public.head
    for basename in _PRIVATE_BASENAMES.values():
        assert (directory / basename).stat().st_mode & 0o777 == 0o600
    for basename in _REGISTRY_BASENAMES.values():
        assert (directory / basename).stat().st_mode & 0o777 == 0o644


@linux_managed
def test_provisioner_rejects_reuse_against_birth_public_inventory(tmp_path):
    root = _root(tmp_path)
    first = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    forbidden = [first.head_private.public_key().public_bytes_raw()]
    with pytest.raises(OwnershipAuthorityError, match="key_reused"):
        _provision_ownership_authorities_at_v1(
            root, forbidden_public_keys=forbidden, root_owned=False,
        )


@linux_managed
def test_product_public_cold_load_rejects_a_reused_birth_key(
    tmp_path, monkeypatch,
):
    root = _root(tmp_path)
    deployed = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    cold_public = _load_public_at_v1(
        root / "authorities-v1", root_owned=False,
    )
    reused = deployed.cutover_private.public_key().public_bytes_raw()
    monkeypatch.setattr(authority_module, "_root_owned_chain", lambda _path: None)
    monkeypatch.setattr(
        authority_module, "_load_public_at_v1",
        lambda _path, *, root_owned: cold_public,
    )
    monkeypatch.setattr(
        authority_module, "_birth_public_keys_v1", lambda: frozenset({reused}),
    )
    with pytest.raises(OwnershipAuthorityError, match="key_reused"):
        authority_module.load_ownership_public_registries_v1()


@linux_managed
def test_filesystem_cold_bundle_rejects_reuse_between_ownership_roles(tmp_path):
    root = _root(tmp_path)
    deployed = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    directory = root / "authorities-v1"
    (directory / _REGISTRY_BASENAMES["head"]).write_bytes(
        encode_ownership_registry_v1(
            "head", deployed.cutover_private.public_key(),
        )
    )
    with pytest.raises(OwnershipAuthorityError, match="key_reused"):
        _load_public_at_v1(directory, root_owned=False)


@linux_managed
def test_public_cold_loader_rejects_an_exposed_private_key(tmp_path):
    root = _root(tmp_path)
    _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    directory = root / "authorities-v1"
    (directory / _PRIVATE_BASENAMES["head"]).chmod(0o644)
    with pytest.raises(OwnershipAuthorityError, match="authority_unsafe"):
        _load_public_at_v1(directory, root_owned=False)


@pytest.mark.parametrize(
    "mutation", ["extra-inventory", "private-hardlink", "registry-symlink"],
)
@linux_managed
def test_cold_loader_rejects_distinct_filesystem_substitutions(
    tmp_path, mutation,
):
    root = _root(tmp_path)
    _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    directory = root / "authorities-v1"
    if mutation == "extra-inventory":
        (directory / "extra").write_bytes(b"x")
    elif mutation == "private-hardlink":
        os.link(
            directory / _PRIVATE_BASENAMES["head"], root / "private-link",
        )
    else:
        registry = directory / _REGISTRY_BASENAMES["head"]
        registry.unlink()
        registry.symlink_to(root / "absent")
    with pytest.raises(OwnershipAuthorityError):
        _load_public_at_v1(directory, root_owned=False)


@pytest.mark.parametrize(
    "conflict", ["file", "file-symlink", "directory", "directory-symlink"],
)
@linux_managed
def test_publication_never_replaces_a_racing_object(tmp_path, conflict):
    root = _root(tmp_path)
    final = root / "authorities-v1"
    pending = root / ".authorities-v1.pending"

    def race(point: str) -> None:
        if conflict.startswith("file"):
            if point != "after_checkpoint-000-v1.json_temp_directory_fsync":
                return
            target = pending / "checkpoint-000-v1.json"
            if conflict == "file":
                target.write_bytes(b"racing")
            else:
                target.symlink_to(root / "absent")
        elif point == "before_publish":
            if conflict == "directory":
                final.mkdir()
            else:
                final.symlink_to(root / "absent")

    with pytest.raises(OwnershipAuthorityError, match="recovery_required"):
        _provision_ownership_authorities_at_v1(
            root, forbidden_public_keys=(), root_owned=False, crash=race,
        )
    if conflict == "file":
        assert (pending / "checkpoint-000-v1.json").read_bytes() == b"racing"
    elif conflict == "file-symlink":
        assert (pending / "checkpoint-000-v1.json").is_symlink()
    elif conflict == "directory":
        assert final.is_dir()
    else:
        assert final.is_symlink()
    assert pending.is_dir()


@pytest.mark.parametrize("mutation", ["symlink", "writable", "unknown"])
@linux_managed
def test_recovery_rejects_an_unauthenticated_pending_container(
    tmp_path, mutation,
):
    root = _root(tmp_path)
    pending = root / ".authorities-v1.pending"
    if mutation == "symlink":
        pending.symlink_to(root / "absent")
    else:
        pending.mkdir(mode=0o700)
        if mutation == "writable":
            pending.chmod(0o777)
        else:
            (pending / "unknown").write_bytes(b"x")
    with pytest.raises(OwnershipAuthorityError):
        _provision_ownership_authorities_at_v1(
            root, forbidden_public_keys=(), root_owned=False,
        )


_CRASH_POINTS = (
    "after_directory", "after_checkpoint_0", "after_checkpoint_1",
    "after_checkpoint_2", "after_checkpoint_3",
    "after_verified_checkpoint", "before_publish", "after_publish",
)


@pytest.mark.parametrize("point", _CRASH_POINTS)
@linux_managed
def test_each_provisioning_boundary_recovers_to_one_cold_bundle(tmp_path, point):
    root = _root(tmp_path)

    def crash(observed: str) -> None:
        if observed == point:
            raise RuntimeError("injected stop")

    with pytest.raises(RuntimeError, match="injected stop"):
        _provision_ownership_authorities_at_v1(
            root, forbidden_public_keys=(), root_owned=False, crash=crash,
        )
    recovered = _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    cold = _load_private_at_v1(root / "authorities-v1", root_owned=False)
    assert recovered.public == cold.public


_REAL_DEATH_POINTS = (
    "after_checkpoint-000-v1.json_temp_open",
    "after_distribution-private-v1.bin_temp_create",
    "after_distribution-private-v1.bin_temp_prefix",
    "after_distribution-private-v1.bin_temp_full_write",
    "after_distribution-private-v1.bin_temp_fsync",
    "after_distribution-private-v1.bin_rename",
    "after_directory_metadata_fsync",
    "after_directory_rename",
)


@pytest.mark.parametrize("point", _REAL_DEATH_POINTS)
@linux_managed
def test_real_process_death_recovers_each_durability_boundary(
    tmp_path, point, monkeypatch,
):
    root = _root(tmp_path)
    script = """
import os, sys
from pathlib import Path
from install.birth_ownership_authority_provisioner import _provision_ownership_authorities_at_v1
os.umask(0o077)
def crash(point):
    if point == sys.argv[2]:
        os._exit(73)
_provision_ownership_authorities_at_v1(Path(sys.argv[1]), forbidden_public_keys=(), root_owned=False, crash=crash)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(root), point],
        env=_subprocess_environment(),
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert completed.returncode == 73, completed.stderr
    preserved = None
    if point in {
        "after_distribution-private-v1.bin_temp_full_write",
        "after_distribution-private-v1.bin_temp_fsync",
    }:
        preserved = (
            root / ".authorities-v1.pending"
            / ".distribution-private-v1.bin.tmp"
        ).read_bytes()
    elif point == "after_distribution-private-v1.bin_rename":
        preserved = (
            root / ".authorities-v1.pending" / "distribution-private-v1.bin"
        ).read_bytes()
    synced = []
    if point == "after_directory_rename":
        original_sync = provisioner_module._sync_directory

        def record_sync(path):
            synced.append(Path(path))
            return original_sync(path)

        monkeypatch.setattr(provisioner_module, "_sync_directory", record_sync)
    _provision_ownership_authorities_at_v1(
        root, forbidden_public_keys=(), root_owned=False,
    )
    if point == "after_directory_rename":
        assert root in synced
    directory = root / "authorities-v1"
    _load_private_at_v1(directory, root_owned=False)
    if preserved is not None:
        assert (directory / "distribution-private-v1.bin").read_bytes() == preserved


@linux_managed
def test_two_real_provisioners_serialize_the_shared_transaction(tmp_path):
    root = tmp_path / "admin"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    marker = tmp_path / "first-paused"
    started = tmp_path / "second-started"
    release = tmp_path / "release-first"
    script = """
import os, sys, time
from pathlib import Path
from install.birth_ownership_authority_provisioner import _provision_ownership_authorities_at_v1
root, role, marker, release = map(Path, sys.argv[1:])
def crash(point):
    if role == Path('first') and point == 'after_distribution-private-v1.bin_temp_prefix':
        marker.write_bytes(b'paused')
        while not release.exists():
            time.sleep(0.01)
if role == Path('second'):
    marker.write_bytes(b'started')
_provision_ownership_authorities_at_v1(root, forbidden_public_keys=(), root_owned=False, crash=crash)
"""
    environment = _subprocess_environment()
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(root), "first", str(marker), str(release)],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "first provisioner did not reach the barrier"
    partial = root / ".authorities-v1.pending" / ".distribution-private-v1.bin.tmp"
    before = partial.read_bytes()
    second = subprocess.Popen(
        [sys.executable, "-c", script, str(root), "second", str(started), str(release)],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 10
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.exists(), "second provisioner did not start"
    time.sleep(0.1)
    assert second.poll() is None
    assert partial.read_bytes() == before
    release.write_bytes(b"continue")
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)
    assert first.returncode == 0, first_stdout + first_stderr
    assert second.returncode == 0, second_stdout + second_stderr
    _load_private_at_v1(root / "authorities-v1", root_owned=False)
