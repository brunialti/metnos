from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import sign  # noqa: E402


def _write_executor(root: Path, *, declaration: str, lifecycle: str = "active") -> None:
    code = root / "read_files.py"
    code.write_text("def invoke(args):\n    return {'ok': True, 'entries': []}\n", encoding="utf-8")
    (root / "manifest.toml").write_text(
        f'''manifest_format = "1.0"
{declaration}
name = "read_files"
version = "1.0.0"
lifecycle = "{lifecycle}"

[description]
it = "SCOPO: legge file. PATTERN: read_files(). NON: scrivere. OUT: entries=[]."

[code]
files = ["read_files.py"]
digest = "sha256:placeholder"

[args]
type = "object"
required = []
''',
        encoding="utf-8",
    )


def test_sign_rejects_incomplete_active_standard_claim(tmp_path: Path, monkeypatch) -> None:
    _write_executor(tmp_path, declaration='executor_standard = "metnos.executor/1.0"')
    manifest_path = tmp_path / "manifest.toml"
    original_manifest = manifest_path.read_bytes()
    monkeypatch.setattr(sign, "load_private", lambda _name: pytest.fail("must not sign"))

    with pytest.raises(ValueError, match="executor standard admission failed"):
        sign.sign_executor(tmp_path)
    assert manifest_path.read_bytes() == original_manifest
    assert not (tmp_path / "manifest.toml.sig").exists()
    assert not (tmp_path / "manifest.lang_state.json").exists()


def test_sign_accepts_declared_candidate_profile(tmp_path: Path, monkeypatch) -> None:
    _write_executor(
        tmp_path,
        declaration='executor_standard = "metnos.executor/1.0"',
        lifecycle="synthesized",
    )

    class _PrivateKey:
        @staticmethod
        def sign(data: bytes) -> bytes:
            return b"signed:" + data[:8]

    monkeypatch.setattr(sign, "load_private", lambda _name: _PrivateKey())
    digest, signature_path = sign.sign_executor(tmp_path)

    assert digest.startswith("sha256:")
    assert signature_path.read_bytes().startswith(b"signed:")


def test_sign_preserves_legacy_migration_compatibility(tmp_path: Path, monkeypatch) -> None:
    _write_executor(tmp_path, declaration="")

    class _PrivateKey:
        @staticmethod
        def sign(_data: bytes) -> bytes:
            return b"legacy-signature"

    monkeypatch.setattr(sign, "load_private", lambda _name: _PrivateKey())
    sign.sign_executor(tmp_path)
    assert (tmp_path / "manifest.toml.sig").read_bytes() == b"legacy-signature"


@pytest.mark.parametrize(
    "target_name",
    ("manifest.toml", "manifest.toml.sig", "manifest.lang_state.json"),
)
def test_atomic_replace_failure_never_truncates_final(
    tmp_path: Path,
    monkeypatch,
    target_name: str,
) -> None:
    target = tmp_path / target_name
    target.write_bytes(b"complete-old-payload")
    target.chmod(0o640)

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(sign.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        sign._atomic_replace_bytes(target, b"complete-new-payload")

    assert target.read_bytes() == b"complete-old-payload"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(tmp_path.glob(f".{target_name}.*.tmp")) == []


def test_atomic_replace_fsyncs_file_and_linux_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(sign.os, "fsync", record_fsync)
    sign._atomic_replace_bytes(tmp_path / "manifest.toml.sig", b"signature")

    expected_calls = 2 if sys.platform.startswith("linux") else 1
    assert len(calls) == expected_calls


def test_keypair_public_failure_leaves_one_valid_private_component(
    tmp_path: Path,
    monkeypatch,
) -> None:
    keys = tmp_path / "keys"
    monkeypatch.setattr(sign, "KEYS_DIR", keys)
    real_replace = os.replace

    def interrupt_public(source, destination) -> None:
        if Path(destination).name == "author_pub.bin":
            raise OSError("simulated public-key interruption")
        real_replace(source, destination)

    monkeypatch.setattr(sign.os, "replace", interrupt_public)
    with pytest.raises(OSError, match="public-key interruption"):
        sign.generate_keypair("author")

    private_path = keys / "author_priv.bin"
    assert private_path.is_file()
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600
    assert sign.load_private("author").public_key() is not None
    assert not (keys / "author_pub.bin").exists()
    assert not any(keys.glob(".*.tmp"))


@pytest.mark.parametrize(
    "failure_target",
    ("manifest.lang_state.json", "manifest.toml", "manifest.toml.sig"),
)
def test_interrupted_legacy_sign_is_idempotently_recoverable(
    tmp_path: Path,
    monkeypatch,
    failure_target: str,
) -> None:
    _write_executor(tmp_path, declaration="")
    manifest_path = tmp_path / "manifest.toml"
    signature_path = tmp_path / "manifest.toml.sig"
    state_path = tmp_path / "manifest.lang_state.json"
    manifest_path.chmod(0o640)
    signature_path.write_bytes(b"complete-old-signature")
    signature_path.chmod(0o600)
    original_manifest = manifest_path.read_bytes()
    original_signature = signature_path.read_bytes()

    class _PrivateKey:
        @staticmethod
        def sign(data: bytes) -> bytes:
            return b"signed:" + hashlib.sha256(data).digest()

    private_key = _PrivateKey()
    monkeypatch.setattr(sign, "load_private", lambda _name: private_key)
    real_atomic_replace = sign._atomic_replace_bytes

    def interrupt(path: Path, payload: bytes, *, new_mode: int = 0o600) -> None:
        if Path(path).name == failure_target:
            raise RuntimeError(f"simulated interruption at {failure_target}")
        real_atomic_replace(path, payload, new_mode=new_mode)

    monkeypatch.setattr(sign, "_atomic_replace_bytes", interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        sign.sign_executor(tmp_path)

    if failure_target == "manifest.lang_state.json":
        assert not state_path.exists()
        assert manifest_path.read_bytes() == original_manifest
    else:
        assert state_path.is_file()
    if failure_target != "manifest.toml.sig":
        assert signature_path.read_bytes() == original_signature
    assert not any(tmp_path.glob(".*.tmp"))

    # A normal retry converges from every safe intermediate state.  Existing
    # files are not rewritten partially and retain their original modes.
    monkeypatch.setattr(sign, "_atomic_replace_bytes", real_atomic_replace)
    digest, returned_signature_path = sign.sign_executor(tmp_path)
    final_manifest = manifest_path.read_bytes()
    final_state = state_path.read_bytes()

    assert returned_signature_path == signature_path
    assert digest == sign.compute_code_digest(tmp_path, ["read_files.py"])
    assert digest.encode("ascii") in final_manifest
    assert signature_path.read_bytes() == private_key.sign(final_manifest)
    assert final_state
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o640
    assert stat.S_IMODE(signature_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o640

    before_retry = (final_manifest, final_state, signature_path.read_bytes())
    sign.sign_executor(tmp_path)
    assert (
        manifest_path.read_bytes(),
        state_path.read_bytes(),
        signature_path.read_bytes(),
    ) == before_retry
    assert not any(tmp_path.glob(".*.tmp"))


def test_installation_manifest_paths_include_subprocess_and_builtin(
        tmp_path: Path, monkeypatch) -> None:
    executors = tmp_path / "executors"
    builtins = tmp_path / "runtime" / "builtin_executor_contracts"
    subprocess_manifest = executors / "read_files" / "manifest.toml"
    builtin_manifest = builtins / "extract_entries" / "manifest.toml"
    subprocess_manifest.parent.mkdir(parents=True)
    builtin_manifest.parent.mkdir(parents=True)
    subprocess_manifest.write_text("name = 'read_files'\n", encoding="utf-8")
    builtin_manifest.write_text("name = 'extract_entries'\n", encoding="utf-8")

    monkeypatch.setattr(sign._C, "PATH_EXECUTORS", executors)
    monkeypatch.setattr(sign, "BUILTIN_CONTRACTS_DIR", builtins)

    assert sign.installation_manifest_paths() == [
        subprocess_manifest,
        builtin_manifest,
    ]
