"""Administrative vision startup inputs never become ordinary user settings."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from virt import startup

PROFILE = b'''[default]
model = "/srv/models/vision.gguf"
mmproj = "/srv/models/projection.gguf"
llama_bin = "/srv/llama/bin/llama-server"
'''


def test_only_existing_artifact_variables_are_projected():
    assert startup._profile_environment(PROFILE, "default") == {
        "METNOS_VLM_MODEL": "/srv/models/vision.gguf",
        "METNOS_VLM_MMPROJ": "/srv/models/projection.gguf",
        "METNOS_VLM_LLAMA_BIN": "/srv/llama/bin/llama-server",
    }
    assert startup._profile_environment(PROFILE, "other") == {}


@pytest.mark.parametrize("content", [
    b"", b"invalid TOML", PROFILE + b'launcher = "/tmp/run"\n',
    PROFILE + b'LD_PRELOAD = "/tmp/library"\n',
    PROFILE.replace(b'"/srv/models/vision.gguf"', b'"../vision.gguf"'),
    PROFILE.replace(b'"/srv/models/vision.gguf"', b'"/srv/../vision.gguf"'),
    PROFILE.replace(b'"/srv/models/vision.gguf"', b'"/srv/model\\nfile"'),
    PROFILE.replace(b'"/srv/models/vision.gguf"', b'123'),
    PROFILE.replace(b'model = "/srv/models/vision.gguf"\n', b''),
    b'\xff',
])
def test_invalid_or_expansive_profiles_fail_closed(content):
    with pytest.raises(startup.StartupProfileError):
        startup._profile_environment(content, "default")


def test_environment_has_precedence_without_mutating_process(monkeypatch):
    for name in startup._VARIABLES.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("METNOS_VLM_MODEL", "/explicit/model.gguf")
    monkeypatch.setattr(startup, "_read_admin_file", lambda _path: PROFILE)
    before = dict(os.environ)
    child = startup.vlm_startup_environment()
    assert child["METNOS_VLM_MODEL"] == "/explicit/model.gguf"
    assert child["METNOS_VLM_MMPROJ"] == "/srv/models/projection.gguf"
    assert dict(os.environ) == before


def test_absent_profile_preserves_existing_legacy_environment(monkeypatch):
    monkeypatch.setattr(startup, "_read_admin_file", lambda _path: None)
    assert startup.vlm_startup_environment() == dict(os.environ)


def test_complete_environment_never_requires_a_system_profile(monkeypatch):
    for name in startup._VARIABLES.values():
        monkeypatch.setenv(name, "/explicit/artifact")
    monkeypatch.setattr(startup, "_read_admin_file", lambda _path: pytest.fail("unneeded profile read"))
    assert startup.vlm_startup_environment() == dict(os.environ)


@pytest.fixture
def administrative_tree(tmp_path, monkeypatch):
    """Use real file descriptors, with ownership projected for an unprivileged test."""
    path = tmp_path / "startup.toml"
    path.write_bytes(PROFILE)
    path.chmod(0o644)
    real_fstat = os.fstat

    def trusted_metadata(descriptor):
        info = real_fstat(descriptor)
        # The real /tmp and pytest parents are not administrative directories.
        # Ownership and permission rejection get separate negative tests below.
        return SimpleNamespace(st_mode=info.st_mode & ~0o022, st_uid=0, st_size=info.st_size)

    monkeypatch.setattr(startup.os, "fstat", trusted_metadata)
    return path, real_fstat


def test_read_is_bounded_and_descriptor_based(administrative_tree):
    path, _real_fstat = administrative_tree
    assert startup._read_admin_file(path) == PROFILE
    assert startup._read_admin_file(path.with_name("absent.toml")) is None
    path.write_bytes(b"x" * (startup._LIMIT + 1))
    with pytest.raises(startup.StartupProfileError):
        startup._read_admin_file(path)


@pytest.mark.parametrize("component", ["file", "parent"])
@pytest.mark.parametrize("unsafe", ["owner", "permissions"])
def test_mutable_file_or_parent_is_rejected(administrative_tree, monkeypatch, component, unsafe):
    path, real_fstat = administrative_tree

    def untrusted_metadata(descriptor):
        info = real_fstat(descriptor)
        is_file = stat.S_ISREG(info.st_mode)
        reject = is_file if component == "file" else not is_file
        return SimpleNamespace(
            st_mode=(info.st_mode & ~0o022) | (0o020 if reject and unsafe == "permissions" else 0),
            st_uid=1000 if reject and unsafe == "owner" else 0, st_size=info.st_size,
        )

    monkeypatch.setattr(startup.os, "fstat", untrusted_metadata)
    with pytest.raises(startup.StartupProfileError):
        startup._read_admin_file(path)


def test_symlink_and_nonregular_file_are_not_followed(administrative_tree):
    path, _real_fstat = administrative_tree
    link = path.with_name("linked.toml")
    link.symlink_to(path)
    with pytest.raises(OSError):
        startup._read_admin_file(link)
    fifo = path.with_name("fifo.toml")
    os.mkfifo(fifo)
    with pytest.raises(startup.StartupProfileError):
        startup._read_admin_file(fifo)
