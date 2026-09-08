"""Publication privacy classification must not exempt new sensitive bytes."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "tested_public_gii_gate", ROOT / "internal/tools/public_gii_gate.py",
)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


@pytest.mark.parametrize("payload", [
    b"Roberto", b'author = "Roberto Brunialti"',
    b"https://github.com/brunialti/metnos", b"admin@metnos.com",
    b"User-Agent (+contact@metnos.com)", b"user@host.example.com",
])
def test_public_attribution_and_examples(payload):
    assert not GATE._check_blob("example.txt", payload)


@pytest.mark.parametrize("payload", [
    b"guest_iacopo", b"metnos_roberto", b"mykleos", b"pc-roberto",
    b"/home/roberto/a", b"roberto.br unialti@private.org".replace(b" ", b""),
    b"someone@private.org", b"10.0.0.5", b"192.168.1.2",
    b"chat.metnos.com", b"ghp_" + b"a" * 36,
    b"-----BEGIN PRIVATE KEY-----",
])
def test_new_private_values_are_rejected(payload):
    assert GATE._check_blob("example.txt", payload)


@pytest.mark.parametrize("relative", GATE.REVIEWED_EXAMPLES)
def test_signed_example_exception_is_exact_and_not_transferable(relative):
    content = (ROOT / relative).read_bytes()
    assert not GATE._check_blob(relative, content)
    assert GATE._check_blob(relative, content + b"\n")
    assert GATE._check_blob("different-file.txt", content)
    assert GATE._check_blob(relative, content + b"\nghp_" + b"a" * 36)


def test_active_token_is_checked_even_without_known_token_prefix(monkeypatch):
    monkeypatch.setenv("GHTOKEN", "opaque-test-publish-credential")
    assert GATE._check_blob("example.txt", b"opaque-test-publish-credential")


def test_sensitive_filenames_and_symlinks(tmp_path):
    (tmp_path / "author_priv.bin").write_bytes(b"x" * 32)
    (tmp_path / "alias").symlink_to(tmp_path / "author_priv.bin")
    _, findings = GATE._filesystem(tmp_path)
    assert {f.rule for f in findings} == {"sensitive-filename", "non-regular-path"}


def test_index_must_match_final_bytes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "sample.txt").write_text("public example")
    subprocess.run(["git", "-C", str(tmp_path), "add", "sample.txt"], check=True)
    indexed, findings = GATE._index(tmp_path)
    filesystem, _ = GATE._filesystem(tmp_path)
    assert not findings and indexed == filesystem
    (tmp_path / "sample.txt").write_text("changed after staging")
    filesystem, _ = GATE._filesystem(tmp_path)
    assert GATE._fingerprints(indexed) != GATE._fingerprints(filesystem)
