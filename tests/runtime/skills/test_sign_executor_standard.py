from __future__ import annotations

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
    monkeypatch.setattr(sign, "load_private", lambda _name: pytest.fail("must not sign"))

    with pytest.raises(ValueError, match="executor standard admission failed"):
        sign.sign_executor(tmp_path)
    assert not (tmp_path / "manifest.toml.sig").exists()


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
