from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

import pytest

import executor_birth_snapshot as snapshot_module
from executor_birth_snapshot import (
    CandidateSnapshotError,
    acquire_candidate_snapshot,
    materialize_birth_candidate_from_authoring,
)
from manifest_code_digest import code_digest_of_payloads


def _candidate(root: Path, *, files: tuple[str, ...] = ("main.py", "pkg/helper.py")) -> Path:
    root.mkdir()
    rendered = ", ".join(f'"{path}"' for path in files)
    (root / "manifest.toml").write_text(
        f'name = "sample"\n[code]\nfiles = [{rendered}]\n', encoding="utf-8",
    )
    (root / "manifest.lang_state.json").write_bytes(b'{"version":1}\n')
    for path in files:
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"# {path}\n".encode())
    return root


def _error(root: Path) -> str:
    with pytest.raises(CandidateSnapshotError) as caught:
        acquire_candidate_snapshot(root)
    return caught.value.code


def test_snapshot_owns_exact_immutable_bytes_and_cleans_up(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    with acquire_candidate_snapshot(source, private_parent=tmp_path) as result:
        private = result.private_root
        assert private != source
        assert result.manifest_bytes == (source / "manifest.toml").read_bytes()
        assert result.language_state_bytes == b'{"version":1}\n'
        assert isinstance(result.code_files, MappingProxyType)
        assert tuple(result.code_files) == ("main.py", "pkg/helper.py")
        with pytest.raises(TypeError):
            result.code_files["main.py"] = b"changed"  # type: ignore[index]
        (source / "main.py").write_bytes(b"changed")
        assert result.code_files["main.py"] == b"# main.py\n"
        if os.name != "nt":
            assert private.stat().st_mode & 0o777 == 0o500
            assert (private / "main.py").stat().st_mode & 0o777 == 0o400
    assert not private.exists()


def test_signed_authoring_tree_becomes_exact_candidate_with_fresh_digest(
    tmp_path: Path,
) -> None:
    source = _candidate(tmp_path / "source")
    manifest = (source / "manifest.toml").read_text(encoding="utf-8")
    manifest = manifest.replace(
        '[code]\n',
        '[code]\ndigest = "sha256:' + ('0' * 64) + '"\n',
    )
    (source / "manifest.toml").write_text(manifest, encoding="utf-8")
    (source / "manifest.toml.sig").write_bytes(b"previous-signature")

    target = materialize_birth_candidate_from_authoring(
        source, tmp_path / "candidate",
    )

    expected_digest = code_digest_of_payloads(
        ("main.py", "pkg/helper.py"),
        {
            "main.py": b"# main.py\n",
            "pkg/helper.py": b"# pkg/helper.py\n",
        },
    )
    assert f'digest = "{expected_digest}"' in (
        target / "manifest.toml"
    ).read_text(encoding="utf-8")
    assert not (target / "manifest.toml.sig").exists()
    with acquire_candidate_snapshot(target) as captured:
        assert tuple(captured.code_files) == ("main.py", "pkg/helper.py")


@pytest.mark.parametrize("extra", ["extra.txt", "pkg/extra.py", "manifest.toml.sig"])
def test_extra_file_is_rejected(tmp_path: Path, extra: str) -> None:
    source = _candidate(tmp_path / "source")
    target = source.joinpath(*extra.split("/"))
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"extra")
    assert _error(source) == "candidate_file_extra"


def test_extra_directory_is_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    (source / "unused").mkdir()
    assert _error(source) == "candidate_file_extra"


@pytest.mark.parametrize("missing", ["manifest.toml", "manifest.lang_state.json", "main.py"])
def test_missing_required_file_is_rejected(tmp_path: Path, missing: str) -> None:
    source = _candidate(tmp_path / "source")
    (source / missing).unlink()
    assert _error(source) == "candidate_file_missing"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_file_and_directory_symlinks_are_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    (source / "main.py").unlink()
    (source / "main.py").symlink_to(tmp_path / "outside.py")
    assert _error(source) == "candidate_link_forbidden"

    source = _candidate(tmp_path / "source2")
    for child in (source / "pkg").iterdir():
        child.unlink()
    (source / "pkg").rmdir()
    (source / "pkg").symlink_to(tmp_path, target_is_directory=True)
    assert _error(source) == "candidate_link_forbidden"


@pytest.mark.skipif(not hasattr(os, "link"), reason="hardlinks unavailable")
def test_hardlink_is_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside")
    (source / "main.py").unlink()
    os.link(outside, source / "main.py")
    assert _error(source) == "candidate_link_forbidden"


@pytest.mark.skipif(os.name == "nt", reason="FIFO is POSIX-only")
def test_non_regular_file_is_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    (source / "main.py").unlink()
    os.mkfifo(source / "main.py")
    assert _error(source) == "candidate_file_nonregular"


@pytest.mark.parametrize(
    "declared",
    ["../escape.py", "/absolute.py", "pkg\\wrong.py", "pkg/../main.py"],
)
def test_noncanonical_declared_path_is_rejected(tmp_path: Path, declared: str) -> None:
    source = _candidate(tmp_path / "source", files=("main.py",))
    (source / "manifest.toml").write_text(
        f'name="sample"\n[code]\nfiles=["{declared.replace(chr(92), chr(92) * 2)}"]\n',
        encoding="utf-8",
    )
    assert _error(source) in {"candidate_path_invalid", "candidate_manifest_invalid"}


def test_case_collision_and_duplicates_are_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source", files=("A.py", "a.py"))
    assert _error(source) == "candidate_path_invalid"
    source = _candidate(tmp_path / "source2", files=("main.py",))
    (source / "manifest.toml").write_text(
        'name="sample"\n[code]\nfiles=["main.py", "main.py"]\n', encoding="utf-8",
    )
    assert _error(source) == "candidate_path_invalid"


def test_mutation_during_copy_is_rejected_and_private_copy_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _candidate(tmp_path / "source")
    original = snapshot_module._read_regular
    calls = 0

    def racing(root: Path, relative: str) -> bytes:
        nonlocal calls
        payload = original(root, relative)
        calls += 1
        if calls == 2:
            (source / "main.py").write_bytes(b"raced")
        return payload

    monkeypatch.setattr(snapshot_module, "_read_regular", racing)
    assert _error(source) == "candidate_changed"
    assert not tuple(tmp_path.glob("metnos-birth-*"))


def test_private_copy_tamper_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _candidate(tmp_path / "source")
    original = snapshot_module._tree_state
    calls = 0

    def tamper(root: Path):
        nonlocal calls
        calls += 1
        state = original(root)
        if calls == 3:
            (root / "main.py").write_bytes(b"tampered")
        return state

    monkeypatch.setattr(snapshot_module, "_tree_state", tamper)
    assert _error(source) == "candidate_changed"
    assert not tuple(tmp_path.glob("metnos-birth-*"))


def test_source_root_symlink_is_rejected(tmp_path: Path) -> None:
    source = _candidate(tmp_path / "source")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink unavailable")
    assert _error(alias) == "candidate_link_forbidden"
