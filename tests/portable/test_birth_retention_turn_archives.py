"""Actual native gzip output, whole-segment closure and original-intent recovery."""
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import os
import signal
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_turn_archives import _TurnArchiveOwner
from install.birth_retention_turns import _receipts
from log_lifecycle import archive_daily_logs
from test_birth_retention_artifacts import RUN, collection
from test_birth_retention_turns import DAY, OLD, native, receipt, record


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX archive custody")


@pytest.fixture
def archived(native, tmp_path):
    root = tmp_path / "turns_archive"
    root.mkdir(mode=0o700)
    owner = _TurnArchiveOwner(root=root, require_exclusion=lambda: None, owner=None)

    def create(records, *, day=DAY):
        payload = b"".join(native.append(value, day=day) for value in records)
        source = native.root / day
        if not source.exists():
            source.touch(mode=0o600)
        os.utime(source, (OLD, OLD))
        # The real installed writer uses UMask=0077; archives retain source
        # mtime. Pruning is disabled here so it cannot erase the test subject.
        previous_umask = os.umask(0o077)
        try:
            report = archive_daily_logs(native.root, root, now=OLD + 100 * 86400,
                                        archive_days=0, max_archive_bytes=0, patterns=(day,))
        finally:
            os.umask(previous_umask)
        assert report["archived_files"] == 1 and report["failures"] == []
        path = root / day[:4] / day[5:7] / (day + ".gz")
        assert not source.exists() and gzip.decompress(path.read_bytes()) == payload
        return path

    return SimpleNamespace(root=root, owner=owner, create=create, live=native)


def test_native_archives_keep_receipts_and_only_closed_segments_are_removed(archived, tmp_path):
    old = archived.create([record("old")])
    kept = archived.create([record("other", actor="another"), record("waiting", outcome="awaiting_input")],
                           day="2020-01-02.jsonl")
    before = kept.read_bytes()
    first, second = archived.owner.scan()
    assert first.object.state is NodeState.CLOSED and second.object.state is NodeState.OPEN
    assert _receipts(second.records[0]) == ((receipt("other"),), False)
    owners = {archived.owner.name: archived.owner}
    root, public, factory = collection(tmp_path, (first.object,), owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not old.exists() and old.parent.is_dir()
    assert kept.read_bytes() == before
    assert archived.owner.scan() == (second,)
    assert not (root / "active.json").exists()


@pytest.mark.parametrize("condition", ["open", "missing_receipt", "unknown_outcome", "wrong_turn", "recent"])
def test_one_unclosed_turn_or_unexpired_window_retains_the_whole_archive(archived, condition):
    value = record("kept")
    if condition == "open":
        value["ts_end"] = 0
    elif condition == "missing_receipt":
        value["steps"][0]["execution_receipt"] = None
    elif condition == "unknown_outcome":
        value["outcome"] = ""
    elif condition == "wrong_turn":
        value["turn_id"] = "somewhere-else"
    elif condition == "recent":
        value["ts_end"] = datetime.now(timezone.utc).timestamp()
    path = archived.create([record("old"), value])
    entry, = archived.owner.scan()
    before = path.read_bytes()
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        archived.owner.delete(entry.object.identity, entry.object.version)
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", [".jsonl", ".jsonl.bak"])
def test_native_live_backup_and_compressed_backup_have_distinct_storage_identity(archived, suffix):
    day = "2020-01-01" + suffix
    archived.live.append(record("turn"), day=day)
    live_entry, = archived.live.owner.scan()
    assert live_entry.object.identity.store.endswith(suffix)
    # The native backup is already present, so archive it without appending.
    source = archived.live.root / day
    payload = source.read_bytes()
    source.unlink()
    path = archived.create([record("turn")], day=day)
    entry, = archived.owner.scan()
    assert entry.object.identity != live_entry.object.identity
    assert gzip.decompress(path.read_bytes()) == payload
    assert entry.records == live_entry.records


@pytest.mark.parametrize("damage", ["trailer", "empty_gzip_file", "invalid_json", "unfinished_record",
                                   "expanded_budget", "symlink", "hardlink", "mode", "fifo",
                                   "directory_link", "unknown_entry", "wrong_date"])
def test_archive_inventory_refuses_corruption_unsafe_custody_and_unknown_entries(archived, tmp_path, monkeypatch, damage):
    value = record("old")
    if damage == "expanded_budget":
        value["user_query"] = "a" * 50_000
    path = archived.create([value])
    if damage == "trailer":
        path.write_bytes(path.read_bytes()[:-3])
    elif damage == "empty_gzip_file":
        path.write_bytes(b"")
    elif damage in {"invalid_json", "unfinished_record"}:
        payload = b'{"turn_id":"a", "turn_id":"b"}\n' if damage == "invalid_json" else gzip.decompress(path.read_bytes())[:-1]
        path.write_bytes(gzip.compress(payload))
    elif damage == "expanded_budget":
        monkeypatch.setattr("install.birth_retention_turn_archives._MAX_BYTES", 4096)
    elif damage == "symlink":
        target = tmp_path / "elsewhere"
        path.rename(target)
        path.symlink_to(target)
    elif damage == "hardlink":
        os.link(path, tmp_path / "alias")
    elif damage == "mode":
        path.chmod(0o666)
    elif damage == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif damage == "directory_link":
        target = tmp_path / "elsewhere"
        path.parent.rename(target)
        path.parent.symlink_to(target)
    elif damage == "unknown_entry":
        (archived.root / "unexpected").write_bytes(b"history")
    elif damage == "wrong_date":
        path.rename(path.with_name("2020-02-01.jsonl.gz"))
    with pytest.raises(RetentionError):
        archived.owner.scan()


@pytest.mark.parametrize("change", ["content", "mode", "foreign_identity"])
def test_archive_identity_version_and_custody_are_rechecked_before_effect(archived, change):
    path = archived.create([record("old")])
    entry, = archived.owner.scan()
    identity = entry.object.identity
    if change == "content":
        path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()) + b" \n"))
    elif change == "mode":
        path.chmod(0o644)
    elif change == "foreign_identity":
        identity = replace(identity, store=archived.live.root.as_uri())
    before = path.read_bytes()
    with pytest.raises(RetentionError):
        archived.owner.delete(identity, entry.object.version)
    assert path.read_bytes() == before


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SIGKILL and native directory fsync")
@pytest.mark.parametrize("point", ["before_effect", "after_unlink", "after_effect", "after_outcome"])
def test_interrupted_archive_removal_recovers_original_receipt_and_durable_absence(archived, tmp_path, monkeypatch, point):
    path = archived.create([record("old")])
    entry, = archived.owner.scan()
    owners = {archived.owner.name: archived.owner}
    root, public, factory = collection(tmp_path, (entry.object,), owners)
    journal = root / (RUN[7:] + ".sqlite")
    with sqlite3.connect(journal) as db:
        original = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    unlink = os.unlink
    child = os.fork()
    if child == 0:
        try:
            if point == "after_unlink":
                def interrupted(name, **kwargs):
                    result = unlink(name, **kwargs)
                    if name == path.name:
                        os.kill(os.getpid(), signal.SIGKILL)
                    return result
                os.unlink = interrupted
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                             crash=lambda stage: os.kill(os.getpid(), signal.SIGKILL) if stage == point else None)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (root / "active.json").exists()
    synchronized = []
    fsync = os.fsync
    def sync(fd):
        if os.fstat(fd).st_ino == path.parent.stat().st_ino:
            synchronized.append(True)
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", sync)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert synchronized and not path.exists()
    assert archived.owner.scan() == ()
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == original
        assert db.execute("SELECT status FROM intents").fetchall() == [("deleted",)]
