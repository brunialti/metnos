"""Optional F5 custody: closed public trust and resumable native preparation."""
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

import executor_birth_certification_authority as authority
import install.birth_certification_authority_provisioner as provisioner
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_ownership_authorities import OwnershipAuthorityError


native = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="managed custody is Linux-only")


def _provision(root, **kwargs):
    root.chmod(0o755)
    return provisioner._provision_certification_at_v1(
        root, root_owned=False, forbidden_public_keys=frozenset(), **kwargs,
    )


def _directory(root):
    return root / authority.DIRECTORY_BASENAME_V1


@pytest.mark.parametrize("status", ("active", "revoked"))
def test_registry_roundtrip_is_public_and_single_purpose(status):
    public = Ed25519PrivateKey.generate().public_key()
    raw = authority.encode_certification_registry_v1(public, status=status)
    decoded = authority.decode_certification_registry_v1(raw)
    assert decoded.public_key.public_bytes_raw() == public.public_bytes_raw()
    assert decoded.status == status
    assert json.loads(raw)["purpose"] == "f5_certification_v1"
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
    doc = json.loads(authority.encode_certification_registry_v1(Ed25519PrivateKey.generate().public_key()))
    doc[field] = value
    with pytest.raises(OwnershipAuthorityError, match="certification_authority_invalid"):
        authority.decode_certification_registry_v1(canonical(doc))


def test_registry_rejects_duplicate_noncanonical_and_oversized_documents():
    raw = authority.encode_certification_registry_v1(Ed25519PrivateKey.generate().public_key())
    for invalid in (b'{"schema_version":1,' + raw[1:], raw + b"\n",
                    b"x" * (authority.MAX_REGISTRY_BYTES_V1 + 1), b"[" * 2000):
        with pytest.raises(OwnershipAuthorityError, match="certification_authority_invalid"):
            authority.decode_certification_registry_v1(invalid)


def test_public_reader_import_does_not_initialize_user_storage(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "runtime")
    roots = [tmp_path / name for name in ("data", "state", "config")]
    environment.update(zip(("METNOS_USER_DATA", "METNOS_USER_STATE", "METNOS_USER_CONFIG"), map(str, roots)))
    result = subprocess.run([
        sys.executable, "-B", "-c", "import sys; import executor_birth_certification_authority; assert 'config' not in sys.modules",
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
    loaded = authority._load_certification_public_at_v1(directory, root_owned=False)
    assert loaded.key_id == first.key_id
    assert observed == [authority.REGISTRY_BASENAME_V1]


@native
@pytest.mark.parametrize("stage", [
    "after_private.bin_temp_prefix", "after_private.bin_temp_full_write",
    "after_private.bin_rename", "after_registry.json_temp_prefix",
    "after_registry.json_temp_full_write", "after_registry.json_rename",
    "after_certification_directory_rename",
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
        registry.write_bytes(authority.encode_certification_registry_v1(Ed25519PrivateKey.generate().public_key()))
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
        provisioner._provision_certification_at_v1(
            tmp_path, root_owned=False,
            forbidden_public_keys=frozenset({public.public_key.public_bytes_raw()}),
        )
    path = _directory(tmp_path) / authority.REGISTRY_BASENAME_V1
    path.write_bytes(authority.encode_certification_registry_v1(public.public_key, status="revoked"))
    assert _provision(tmp_path).status == "revoked"


def test_product_entry_refuses_platform_and_privilege_before_io(monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")

    for module in (authority, provisioner):
        monkeypatch.setattr(module, "_root_owned_chain", no_io)
        monkeypatch.setattr(module, "_managed_authority_platform_supported_v1", lambda: False)
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        authority.load_certification_public_key_v1()
    with pytest.raises(OwnershipAuthorityError, match="platform_unsupported"):
        provisioner.provision_certification_authority_v1()
    monkeypatch.setattr(provisioner, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(OwnershipAuthorityError, match="root_required"):
        provisioner.provision_certification_authority_v1()


def test_product_public_entry_is_fixed_root_and_refuses_revocation(monkeypatch):
    public = authority.decode_certification_registry_v1(authority.encode_certification_registry_v1(
        Ed25519PrivateKey.generate().public_key(), status="revoked",
    ))
    observed = []
    monkeypatch.setattr(authority, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(authority, "_root_owned_chain", lambda path: observed.append(path))

    def read(path, *, root_owned):
        assert path == authority.DEFAULT_DIRECTORY_V1 and root_owned
        return public

    monkeypatch.setattr(authority, "_load_certification_public_at_v1", read)
    with pytest.raises(OwnershipAuthorityError, match="revoked"):
        authority.load_certification_public_key_v1()
    assert observed == [authority.DEFAULT_DIRECTORY_V1]
    with pytest.raises(TypeError):
        authority.load_certification_public_key_v1(directory="/caller-selected")


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
    with tempfile.TemporaryDirectory(prefix="metnos-f5-custody-", dir="/var/lib") as temporary:
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
            "from install.birth_certification_authority_provisioner import _provision_certification_at_v1; "
            "_provision_certification_at_v1(Path(sys.argv[1]), root_owned=True, "
            "forbidden_public_keys=frozenset(), "
            "crash=lambda stage: os._exit(73) if stage == 'after_private.bin_rename' else None)",
            str(root),
        ], env=environment, capture_output=True, timeout=30)
        assert killed.returncode == 73, killed.stderr.decode()
        pending = root / f".{authority.DIRECTORY_BASENAME_V1}.pending"
        original = (pending / authority.PRIVATE_BASENAME_V1).read_bytes()
        expected = provisioner._provision_certification_at_v1(
            root, root_owned=True, forbidden_public_keys=frozenset(),
        )
        directory = _directory(root)
        assert (directory / authority.PRIVATE_BASENAME_V1).read_bytes() == original
        authority._root_owned_chain(directory)
        # The reader must not depend on access to the administrator's checkout.
        source = root / "public-reader-source"
        source.mkdir(mode=0o755)
        source.chmod(0o755)
        for name in ("executor_birth_certification_authority", "executor_birth_authority_files",
                     "executor_birth_canonical", "executor_birth_keystore"):
            copied = source / f"{name}.py"
            copied.write_bytes((repository / "runtime" / copied.name).read_bytes())
            copied.chmod(0o644)
        environment["PYTHONPATH"] = str(source)
        reader = subprocess.run([
            sys.executable, "-B", "-c",
            "import sys; from pathlib import Path; "
            "import executor_birth_certification_authority as a; "
            "assert 'config' not in sys.modules; "
            "a.DEFAULT_DIRECTORY_V1=Path(sys.argv[1]); "
            "assert a.load_certification_public_key_v1().key_id == sys.argv[2]; "
            "exec('try:\\n (a.DEFAULT_DIRECTORY_V1 / a.PRIVATE_BASENAME_V1).read_bytes()"
            "\\nexcept PermissionError:\\n pass\\nelse:\\n raise AssertionError(\"private key readable\")')",
            str(directory), expected.key_id,
        ], env=environment, cwd=root, user=account.pw_uid, group=account.pw_gid,
            extra_groups=[], capture_output=True, timeout=30)
        assert reader.returncode == 0, reader.stderr.decode()
