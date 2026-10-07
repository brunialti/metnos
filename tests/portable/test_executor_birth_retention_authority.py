"""Optional F6 custody: closed public trust and resumable native preparation."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_retention_authority as authority
import install.birth_retention_authority_provisioner as provisioner
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_ownership_authorities import OwnershipAuthorityError


def test_retention_and_certification_registries_are_not_interchangeable():
    import executor_birth_certification_authority as certification

    public = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(OwnershipAuthorityError):
        authority.decode_retention_registry_v1(
            certification.encode_certification_registry_v1(public))
    with pytest.raises(OwnershipAuthorityError):
        certification.decode_certification_registry_v1(
            authority.encode_retention_registry_v1(public))


native = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="managed custody is Linux-only")


def _provision(root, **kwargs):
    root.chmod(0o755)
    return provisioner._provision_retention_at_v1(
        root, root_owned=False, forbidden_public_keys=frozenset(), **kwargs,
    )


def _directory(root):
    return root / authority.DIRECTORY_BASENAME_V1


@pytest.mark.parametrize("status", ("active", "revoked"))
def test_registry_roundtrip_is_public_and_single_purpose(status):
    public = Ed25519PrivateKey.generate().public_key()
    raw = authority.encode_retention_registry_v1(public, status=status)
    decoded = authority.decode_retention_registry_v1(raw)
    assert decoded.public_key.public_bytes_raw() == public.public_bytes_raw()
    assert decoded.status == status
    assert json.loads(raw)["purpose"] == "f6_retention_receipt_v1"
    assert not hasattr(decoded, "private_key")


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2),
    ("purpose", "ownership_head_v1"), ("status", "unknown"),
    ("status", []), ("key_id", "unbound-key"), ("public_key", "!"),
    ("public_key", base64.b64encode(b"x" * 31).decode("ascii")),
    ("additional_scope", "publish"),
], ids=["bool-version", "version", "purpose", "status", "status-type",
        "key-binding", "key-encoding", "key-size", "extra-field"])
def test_registry_rejects_ambiguous_or_extended_authority(field, value):
    doc = json.loads(authority.encode_retention_registry_v1(Ed25519PrivateKey.generate().public_key()))
    doc[field] = value
    with pytest.raises(OwnershipAuthorityError, match="retention_authority_invalid"):
        authority.decode_retention_registry_v1(canonical(doc))


def test_registry_rejects_duplicate_noncanonical_and_oversized_documents():
    raw = authority.encode_retention_registry_v1(Ed25519PrivateKey.generate().public_key())
    for invalid in (b'{"schema_version":1,' + raw[1:], raw + b"\n",
                    b"x" * (authority.MAX_REGISTRY_BYTES_V1 + 1), b"[" * 2000):
        with pytest.raises(OwnershipAuthorityError, match="retention_authority_invalid"):
            authority.decode_retention_registry_v1(invalid)


def test_public_reader_import_does_not_initialize_user_storage(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "runtime")
    roots = [tmp_path / name for name in ("data", "state", "config")]
    environment.update(zip(("METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG"), map(str, roots)))
    result = subprocess.run([
        sys.executable, "-B", "-c", "import sys; import executor_birth_retention_authority; assert 'config' not in sys.modules",
    ], env=environment, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert not any(path.exists() for path in roots)


@native
def test_real_pair_is_idempotent_and_public_reader_never_opens_private(tmp_path, monkeypatch):
    first = _provision(tmp_path)
    directory = _directory(tmp_path)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert _provision(tmp_path).key_id == first.key_id
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert (directory / authority.PRIVATE_BASENAME_V1).stat().st_mode & 0o777 == 0o600
    assert (directory / authority.REGISTRY_BASENAME_V1).stat().st_mode & 0o777 == 0o644
    observed = []
    real_read = authority._read_regular

    def public_only(path, **kwargs):
        observed.append(path.name)
        assert path.name == authority.REGISTRY_BASENAME_V1
        return real_read(path, **kwargs)

    monkeypatch.setattr(authority, "_read_regular", public_only)
    loaded = authority._load_retention_public_at_v1(directory, root_owned=False)
    assert loaded.key_id == first.key_id
    assert observed == [authority.REGISTRY_BASENAME_V1]


@native
@pytest.mark.parametrize("stage", [
    "after_private.bin_temp_prefix", "after_private.bin_temp_full_write",
    "after_private.bin_rename", "after_registry.json_temp_prefix",
    "after_registry.json_temp_full_write", "after_registry.json_rename",
    "after_retention_directory_rename",
])
def test_interrupted_publication_resumes_exact_complete_key(tmp_path, stage):
    class Interrupted(Exception):
        pass

    def crash(point):
        if point == stage:
            raise Interrupted

    with pytest.raises(Interrupted):
        _provision(tmp_path, crash=crash)
    pending = tmp_path / f".{authority.DIRECTORY_BASENAME_V1}.pending"
    directory = _directory(tmp_path) if _directory(tmp_path).exists() else pending
    private = directory / authority.PRIVATE_BASENAME_V1
    if not private.exists():
        private = directory / f".{authority.PRIVATE_BASENAME_V1}.tmp"
    before = private.read_bytes()
    result = _provision(tmp_path)
    after = (_directory(tmp_path) / authority.PRIVATE_BASENAME_V1).read_bytes()
    if len(before) == 32:
        assert after == before
    assert Ed25519PrivateKey.from_private_bytes(after).public_key().public_bytes_raw() == result.public_key.public_bytes_raw()
    assert not pending.exists()


@native
@pytest.mark.parametrize("damage", ("missing-private", "mismatch", "extra", "symlink", "permissions"))
def test_published_damage_is_not_repaired_or_silently_rotated(tmp_path, damage):
    original = _provision(tmp_path)
    directory = _directory(tmp_path)
    private = directory / authority.PRIVATE_BASENAME_V1
    registry = directory / authority.REGISTRY_BASENAME_V1
    if damage == "missing-private":
        private.unlink()
    elif damage == "mismatch":
        registry.write_bytes(authority.encode_retention_registry_v1(Ed25519PrivateKey.generate().public_key()))
    elif damage == "extra":
        (directory / "unexpected").write_bytes(b"")
    elif damage == "symlink":
        private.rename(directory / "moved-private")
        private.symlink_to(directory / "moved-private")
    else:
        private.chmod(0o644)
    before = registry.read_bytes()
    with pytest.raises(OwnershipAuthorityError):
        _provision(tmp_path)
    assert registry.read_bytes() == before
    if damage == "missing-private":
        assert not private.exists()
    assert original.status == "active"


@native
def test_existing_key_reuse_is_refused_and_revocation_is_preserved(tmp_path):
    public = _provision(tmp_path)
    with pytest.raises(OwnershipAuthorityError, match="key_reused"):
        provisioner._provision_retention_at_v1(
            tmp_path, root_owned=False,
            forbidden_public_keys=frozenset({public.public_key.public_bytes_raw()}),
        )
    path = _directory(tmp_path) / authority.REGISTRY_BASENAME_V1
    path.write_bytes(authority.encode_retention_registry_v1(public.public_key, status="revoked"))
    assert _provision(tmp_path).status == "revoked"


def _rotate(root, expected, **kwargs):
    return provisioner._rotate_retention_at_v1(
        root, root_owned=False, expected_key_id=expected,
        forbidden_public_keys=frozenset(), **kwargs,
    )


@native
def test_rotation_preserves_signed_receipts_without_retaining_old_private_keys(tmp_path):
    from executor_birth_retention import NodeKey, NodeType, _receipt, verify_minimal_receipt

    first = _provision(tmp_path)
    directory = _directory(tmp_path)
    old_private = (directory / authority.PRIVATE_BASENAME_V1).read_bytes()
    key = NodeKey(NodeType.BLOB, "sha256:" + "a" * 64)
    receipt = _receipt(key, "sha256:" + "b" * 64, 1, "2026-01-01T00:00:00Z",
                       first.key_id, Ed25519PrivateKey.from_private_bytes(old_private))
    second = _rotate(tmp_path, first.key_id)
    third = _rotate(tmp_path, second.key_id)
    assert len({first.key_id, second.key_id, third.key_id}) == 3
    assert _provision(tmp_path).key_id == third.key_id
    history = authority._load_retention_history_at_v1(directory, root_owned=False)
    assert {key.key_id for key in history} == {first.key_id, second.key_id, third.key_id}
    verify_minimal_receipt(key=key, run_id="sha256:" + "b" * 64, object_version=1,
                           deleted_at="2026-01-01T00:00:00Z", authentication=receipt,
                           public_keys={key.key_id: key.public_key for key in history})
    assert (directory / authority.PRIVATE_BASENAME_V1).read_bytes() != old_private
    assert not (directory / authority.ROTATION_BASENAME_V1).exists()
    assert all(path.suffix == ".json" for path in (directory / authority.HISTORY_BASENAME_V1).iterdir())


@native
@pytest.mark.parametrize("stage", [
    "after_private.bin_temp_prefix", "after_private.bin_temp_full_write", "after_private.bin_rename",
    "after_registry.json_temp_prefix", "after_registry.json_temp_full_write", "after_registry.json_rename",
    "rotation_history_directory_created", "rotation_history_directory_published",
    "rotation_history_saved", "rotation_replaced_private.bin", "rotation_replaced_registry.json",
    "rotation_cleaned_private.bin", "rotation_cleaned_registry.json", "rotation_cleaned_previous.json",
])
def test_rotation_process_death_resumes_same_complete_key(tmp_path, stage):
    first = _provision(tmp_path)
    code = (
        "import os,sys; os.umask(0o077); from pathlib import Path; "
        "from install.birth_retention_authority_provisioner import _rotate_retention_at_v1; "
        "_rotate_retention_at_v1(Path(sys.argv[1]), expected_key_id=sys.argv[2], root_owned=False, "
        "forbidden_public_keys=frozenset(), crash=lambda p: os._exit(73) if p == sys.argv[3] else None)"
    )
    killed = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path), first.key_id, stage],
                            capture_output=True, timeout=30)
    assert killed.returncode == 73, killed.stderr.decode()
    directory = _directory(tmp_path)
    with pytest.raises(OwnershipAuthorityError, match="recovery_required"):
        authority._load_retention_public_at_v1(directory, root_owned=False)
    with pytest.raises(OwnershipAuthorityError, match="recovery_required"):
        _provision(tmp_path)
    transaction = directory / authority.ROTATION_BASENAME_V1
    staged = transaction / authority.PRIVATE_BASENAME_V1
    if not staged.exists():
        staged = transaction / ("." + authority.PRIVATE_BASENAME_V1 + ".tmp")
    prior = staged.read_bytes() if staged.exists() else None
    current_before = (directory / authority.PRIVATE_BASENAME_V1).read_bytes()
    result = _rotate(tmp_path, first.key_id)
    assert result.key_id != first.key_id
    current_after = (directory / authority.PRIVATE_BASENAME_V1).read_bytes()
    if prior is not None and len(prior) == 32:
        assert current_after == prior
    if stage.startswith("rotation_cleaned"):
        assert current_after == current_before
    assert not transaction.exists()
    assert {key.key_id for key in authority._load_retention_history_at_v1(directory, root_owned=False)} == {
        first.key_id, result.key_id}


@native
def test_rotation_rejects_stale_expected_key_and_does_not_overwrite_history(tmp_path):
    first = _provision(tmp_path)
    second = _rotate(tmp_path, first.key_id)
    directory = _directory(tmp_path)
    private_before = (directory / authority.PRIVATE_BASENAME_V1).read_bytes()
    with pytest.raises(OwnershipAuthorityError, match="authority_changed"):
        _rotate(tmp_path, first.key_id)
    old_path = directory / authority.HISTORY_BASENAME_V1 / (first.key_id + ".json")
    old_path.write_bytes(authority.encode_retention_registry_v1(Ed25519PrivateKey.generate().public_key()))
    with pytest.raises(OwnershipAuthorityError, match="history identity"):
        _rotate(tmp_path, second.key_id)
    assert (directory / authority.PRIVATE_BASENAME_V1).read_bytes() == private_before


@native
def test_historical_key_reuse_and_revocation_cannot_hide_in_archive(tmp_path):
    first = _provision(tmp_path)
    second = _rotate(tmp_path, first.key_id)
    with pytest.raises(OwnershipAuthorityError, match="key_reused"):
        provisioner._provision_retention_at_v1(
            tmp_path, root_owned=False, forbidden_public_keys=frozenset({first.public_key.public_bytes_raw()}))
    directory = _directory(tmp_path)
    registry = directory / authority.REGISTRY_BASENAME_V1
    registry.write_bytes(authority.encode_retention_registry_v1(second.public_key, status="revoked"))
    keys = {key.key_id: key for key in authority._load_retention_history_at_v1(directory, root_owned=False)}
    assert keys[second.key_id].status == "revoked"
    with pytest.raises(OwnershipAuthorityError, match="authority_changed"):
        _rotate(tmp_path, second.key_id)


def test_product_entry_refuses_platform_and_privilege_before_io(monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")

    for module in (authority, provisioner):
        monkeypatch.setattr(module, "_root_owned_chain", no_io)
        monkeypatch.setattr(module, "_managed_authority_platform_supported_v1", lambda: False)
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        authority.load_retention_public_key_v1()
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        provisioner.provision_retention_authority_v1()
    monkeypatch.setattr(provisioner, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(OwnershipAuthorityError, match="root_required"):
        provisioner.provision_retention_authority_v1()


def test_product_public_entry_is_fixed_root_and_refuses_revocation(monkeypatch):
    public = authority.decode_retention_registry_v1(authority.encode_retention_registry_v1(
        Ed25519PrivateKey.generate().public_key(), status="revoked",
    ))
    observed = []
    monkeypatch.setattr(authority, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(authority, "_root_owned_chain", lambda path: observed.append(path))

    def read(path, *, root_owned):
        assert path == authority.DEFAULT_DIRECTORY_V1 and root_owned
        return public

    monkeypatch.setattr(authority, "_load_retention_public_at_v1", read)
    with pytest.raises(OwnershipAuthorityError, match="revoked"):
        authority.load_retention_public_key_v1()
    assert observed == [authority.DEFAULT_DIRECTORY_V1]
    with pytest.raises(TypeError):
        authority.load_retention_public_key_v1(directory="/caller-selected")


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires an explicitly delegated native administrator run",
)
def test_native_root_custody_process_death_and_unprivileged_reader():
    """Use real OS identities; never touch the productive authority root."""
    import pwd

    account = pwd.getpwnam("nobody")
    repository = Path(__file__).resolve().parents[2]
    environment = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": os.pathsep.join((str(repository), str(repository / "runtime")))}
    with tempfile.TemporaryDirectory(prefix="metnos-f6-custody-", dir="/var/lib") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        environment.update({
            "METNOS_USER_DATA": str(root / "test-data"),
            "METNOS_USER_STATE": str(root / "test-state"),
            "METNOS_USER_CONFIG": str(root / "test-config"),
        })
        killed = subprocess.run([
            sys.executable, "-B", "-c",
            "import os,sys; from pathlib import Path; "
            "from install.birth_retention_authority_provisioner import _provision_retention_at_v1; "
            "_provision_retention_at_v1(Path(sys.argv[1]), root_owned=True, "
            "forbidden_public_keys=frozenset(), "
            "crash=lambda stage: os._exit(73) if stage == 'after_private.bin_rename' else None)",
            str(root),
        ], env=environment, capture_output=True, timeout=30)
        assert killed.returncode == 73, killed.stderr.decode()
        pending = root / f".{authority.DIRECTORY_BASENAME_V1}.pending"
        original = (pending / authority.PRIVATE_BASENAME_V1).read_bytes()
        expected = provisioner._provision_retention_at_v1(
            root, root_owned=True, forbidden_public_keys=frozenset(),
        )
        directory = _directory(root)
        assert (directory / authority.PRIVATE_BASENAME_V1).read_bytes() == original
        authority._root_owned_chain(directory)
        previous = expected
        killed_rotation = subprocess.run([
            sys.executable, "-B", "-c",
            "import os,sys; from pathlib import Path; "
            "from install.birth_retention_authority_provisioner import _rotate_retention_at_v1; "
            "_rotate_retention_at_v1(Path(sys.argv[1]), root_owned=True, "
            "expected_key_id=sys.argv[2], forbidden_public_keys=frozenset(), "
            "crash=lambda stage: os._exit(74) if stage == 'rotation_replaced_private.bin' else None)",
            str(root), previous.key_id,
        ], env=environment, capture_output=True, timeout=30)
        assert killed_rotation.returncode == 74, killed_rotation.stderr.decode()
        with pytest.raises(OwnershipAuthorityError, match="recovery_required"):
            authority._load_retention_public_at_v1(directory, root_owned=True)
        expected = provisioner._rotate_retention_at_v1(
            root, root_owned=True, expected_key_id=previous.key_id, forbidden_public_keys=frozenset())
        assert expected.key_id != previous.key_id
        # The reader must not depend on access to the administrator's checkout.
        source = root / "public-reader-source"
        source.mkdir(mode=0o755)
        source.chmod(0o755)
        for name in ("executor_birth_retention_authority", "executor_birth_authority_files",
                     "executor_birth_canonical", "executor_birth_keystore"):
            copied = source / f"{name}.py"
            copied.write_bytes((repository / "runtime" / copied.name).read_bytes())
            copied.chmod(0o644)
        environment["PYTHONPATH"] = str(source)
        reader = subprocess.run([
            sys.executable, "-B", "-c",
            "import sys; from pathlib import Path; "
            "import executor_birth_retention_authority as a; "
            "assert 'config' not in sys.modules; "
            "a.DEFAULT_DIRECTORY_V1=Path(sys.argv[1]); "
            "assert a.load_retention_public_key_v1().key_id == sys.argv[2]; "
            "assert {k.key_id for k in a.load_retention_verification_keys_v1()} == {sys.argv[2], sys.argv[3]}; "
            "exec('try:\\n (a.DEFAULT_DIRECTORY_V1 / a.PRIVATE_BASENAME_V1).read_bytes()"
            "\\nexcept PermissionError:\\n pass\\nelse:\\n raise AssertionError(\"private key readable\")')",
            str(directory), expected.key_id, previous.key_id,
        ], env=environment, cwd=root, user=account.pw_uid, group=account.pw_gid,
            extra_groups=[], capture_output=True, timeout=30)
        assert reader.returncode == 0, reader.stderr.decode()
