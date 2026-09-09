"""Successors preserve authenticated predecessor bytes, never an unknown file."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

import executor_birth_dominant_topology as topology


pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX topology")
_OLD = b"[Unit]\nDescription=previous\n[Service]\nExecStart=/bin/true\n"
_NEW = b"[Unit]\nDescription=successor\n[Service]\nExecStart=/bin/false\n"
_NAME = "a.service"


def _install(root: Path, fragments: dict[str, bytes], **kwargs: object):
    return topology.install_for_test_v1(
        topology._TestOnlyTopologyCapabilityV1(root), fragments, **kwargs,
    )


def _backup(root: Path, name: str = _NAME, previous: bytes = _OLD, candidate: bytes = _NEW):
    return topology._previous_fragment_path_v1(root, name, previous, candidate)


def _snapshot(root: Path):
    """Ignore read access times; include identities, contents and all write metadata."""
    result = {}
    for path in root.iterdir():
        info = path.lstat()
        content = path.read_bytes() if stat.S_ISREG(info.st_mode) else (
            str(path.readlink()) if path.is_symlink() else None
        )
        result[path.name] = (
            info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns, content,
        )
    return result


def test_successor_preserves_predecessor_inode_and_replays(tmp_path: Path) -> None:
    previous = {_NAME: _OLD, "z.timer": _OLD}
    candidate = {_NAME: _NEW, "z.timer": _OLD}
    _install(tmp_path, previous)
    original_inode = (tmp_path / _NAME).stat().st_ino
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"keep")

    first = _install(tmp_path, candidate, previous_fragments=previous)
    state = _snapshot(tmp_path)
    second = _install(tmp_path, candidate, previous_fragments=previous)

    assert [unit.repeated for unit in first] == [False, True]
    assert all(unit.repeated for unit in second)
    assert topology.topology_digest_v1(first) == topology.topology_digest_v1(second)
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _backup(tmp_path).read_bytes() == _OLD
    assert _backup(tmp_path).stat().st_ino == original_inode
    assert unrelated.read_bytes() == b"keep"
    assert _snapshot(tmp_path) == state


@pytest.mark.parametrize("stage", [
    "dominant_fragment_preserve_before", "dominant_fragment_preserved",
    "dominant_fragment_staged", "dominant_fragment_published",
])
def test_interruptions_before_and_after_each_move_resume(tmp_path: Path, stage: str) -> None:
    previous, candidate = {_NAME: _OLD}, {_NAME: _NEW}
    _install(tmp_path, previous)

    def interrupt(observed: str) -> None:
        if observed == stage:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        _install(tmp_path, candidate, previous_fragments=previous, _crash_seam=interrupt)
    resumed = _install(tmp_path, candidate, previous_fragments=previous)

    assert resumed[0].repeated is (stage == "dominant_fragment_published")
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _backup(tmp_path).read_bytes() == _OLD
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("move", [1, 2])
def test_crash_after_rename_before_directory_sync_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, move: int,
) -> None:
    import executor_birth_secure_fs as secure_fs

    _install(tmp_path, {_NAME: _OLD})
    original = secure_fs._renameat2_no_replace
    calls = 0

    def interrupt(*args: object) -> None:
        nonlocal calls
        original(*args)
        calls += 1
        if calls == move:
            raise RuntimeError("after rename")

    with monkeypatch.context() as patch:
        patch.setattr(secure_fs, "_renameat2_no_replace", interrupt)
        with pytest.raises(RuntimeError, match="after rename"):
            _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _backup(tmp_path).read_bytes() == _OLD


@pytest.mark.parametrize("partial", [b"", _NEW[:13], _NEW])
@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_interrupted_candidate_write_resumes(tmp_path: Path, partial: bytes, mode: int) -> None:
    _install(tmp_path, {_NAME: _OLD})
    (tmp_path / _NAME).rename(_backup(tmp_path))
    temporary = tmp_path / f".{_NAME}.installing"
    temporary.write_bytes(partial)
    temporary.chmod(mode)
    _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert (tmp_path / _NAME).read_bytes() == _NEW
    assert _backup(tmp_path).read_bytes() == _OLD


@pytest.mark.parametrize("case", [
    "addition", "removal", "invalid_previous", "invalid_candidate", "drift",
    "missing", "candidate_without_backup", "backup_collision", "backup_drift",
    "staging_with_previous", "staging_drift", "symlink", "hardlink", "directory",
    "fifo", "mode", "backup_symlink", "backup_hardlink", "backup_mode",
    "temporary_symlink", "temporary_hardlink", "temporary_mode", "unchanged_missing",
    "unchanged_drift",
])
def test_denials_preflight_the_whole_set_without_effects(tmp_path: Path, case: str) -> None:
    # The good first unit must not move when the last unit fails validation.
    name = "z.service"
    previous = {_NAME: _OLD, name: _OLD}
    candidate = {_NAME: _NEW, name: _NEW}
    _install(tmp_path, previous)
    final = tmp_path / name
    backup = _backup(tmp_path, name)
    temporary = tmp_path / f".{name}.installing"
    if case == "addition":
        candidate["extra.service"] = _NEW
    elif case == "removal":
        del candidate[name]
    elif case == "invalid_previous":
        previous[name] = b""
    elif case == "invalid_candidate":
        candidate[name] = b""
    elif case in {"missing", "unchanged_missing"}:
        final.unlink()
    elif case in {"drift", "unchanged_drift"}:
        final.write_bytes(b"not authenticated")
    elif case == "candidate_without_backup":
        final.write_bytes(_NEW)
    elif case == "backup_collision":
        backup.write_bytes(_OLD)
    elif case == "staging_with_previous":
        temporary.write_bytes(_NEW)
    elif case.startswith("backup_") or case.startswith("temporary_") or case == "staging_drift":
        final.rename(backup)
        if case == "backup_drift":
            backup.write_bytes(b"foreign")
        elif case == "staging_drift":
            temporary.write_bytes(b"not a candidate prefix")
        else:
            target = backup if case.startswith("backup_") else temporary
            if target.exists():
                target.rename(tmp_path / "foreign")
            else:
                (tmp_path / "foreign").write_bytes(_NEW)
            if case.endswith("symlink"):
                target.symlink_to(tmp_path / "foreign")
            elif case.endswith("hardlink"):
                os.link(tmp_path / "foreign", target)
            else:
                target.write_bytes(_OLD if target == backup else _NEW)
                target.chmod(0o666)
    elif case == "mode":
        final.chmod(0o600)
    else:
        final.rename(tmp_path / "foreign")
        if case == "symlink":
            final.symlink_to(tmp_path / "foreign")
        elif case == "hardlink":
            os.link(tmp_path / "foreign", final)
        elif case == "directory":
            final.mkdir()
        elif case == "fifo":
            os.mkfifo(final)
    if case.startswith("unchanged_"):
        candidate[name] = _OLD
    before = _snapshot(tmp_path)
    with pytest.raises(topology.DominantTopologyError):
        _install(tmp_path, candidate, previous_fragments=previous)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("field", ["st_uid", "st_gid", "st_nlink", "st_mode"])
def test_replacement_metadata_is_strict(tmp_path: Path, field: str) -> None:
    from types import SimpleNamespace

    _install(tmp_path, {_NAME: _OLD})
    info = (tmp_path / _NAME).stat()
    values = {key: getattr(info, key) for key in ("st_uid", "st_gid", "st_nlink", "st_mode")}
    values[field] += 1
    with pytest.raises(topology.DominantTopologyError) as denied:
        topology._require_replacement_metadata_v1(SimpleNamespace(**values))
    assert denied.value.code == "topology_replacement_metadata_invalid"


@pytest.mark.parametrize("change", ["prefix", "mode", "hardlink"])
def test_changed_staged_candidate_is_not_published(tmp_path: Path, change: str) -> None:
    _install(tmp_path, {_NAME: _OLD})

    def drift(stage: str) -> None:
        if stage == "dominant_fragment_staged":
            temporary = tmp_path / f".{_NAME}.installing"
            if change == "prefix":
                temporary.write_bytes(_NEW[:10])
            elif change == "mode":
                temporary.chmod(0o600)
            else:
                os.link(temporary, tmp_path / "foreign")

    with pytest.raises(topology.DominantTopologyError):
        _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD}, _crash_seam=drift)
    assert not (tmp_path / _NAME).exists()
    assert _backup(tmp_path).read_bytes() == _OLD


def test_later_successor_keeps_all_older_backups(tmp_path: Path) -> None:
    third = _NEW + b"# third\n"
    _install(tmp_path, {_NAME: _OLD})
    _install(tmp_path, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    original_backup = _backup(tmp_path).stat().st_ino
    _install(tmp_path, {_NAME: third}, previous_fragments={_NAME: _NEW})
    assert _backup(tmp_path).stat().st_ino == original_backup
    assert _backup(tmp_path).read_bytes() == _OLD
    assert _backup(tmp_path, previous=_NEW, candidate=third).read_bytes() == _NEW
    assert (tmp_path / _NAME).read_bytes() == third


def test_replacement_root_symlink_is_denied(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    _install(actual, {_NAME: _OLD})
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    before = _snapshot(actual)
    with pytest.raises(topology.DominantTopologyError) as denied:
        _install(alias, {_NAME: _NEW}, previous_fragments={_NAME: _OLD})
    assert denied.value.code == "topology_root_invalid"
    assert _snapshot(actual) == before
