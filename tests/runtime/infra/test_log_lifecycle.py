from __future__ import annotations

import gzip
import os
from pathlib import Path

from log_lifecycle import archive_daily_logs, rotate_plain_log


DAY = 86400


def _dated_file(directory: Path, name: str, payload: bytes, mtime: float) -> Path:
    path = directory / name
    path.write_bytes(payload)
    os.utime(path, (mtime, mtime))
    return path


def test_old_turn_logs_are_verified_archived_and_removed_from_live(tmp_path):
    now = 2_000_000_000.0
    live = tmp_path / "turns"
    archive = tmp_path / "turns_archive"
    live.mkdir()
    old_payload = (b'{"turn":"old"}\n' * 1000)
    old = _dated_file(
        live, "2025-01-02.jsonl", old_payload, now - 61 * DAY)
    backup = _dated_file(
        live, "2025-01-03.jsonl.bak", b"backup\n", now - 8 * DAY)
    recent = _dated_file(
        live, "2025-03-01.jsonl", b"recent\n", now - 10 * DAY)

    report = archive_daily_logs(live, archive, now=now)

    assert report["failures"] == []
    assert report["archived_files"] == 2
    assert report["archived_source_bytes"] == len(old_payload) + 7
    assert not old.exists()
    assert not backup.exists()
    assert recent.read_bytes() == b"recent\n"
    with gzip.open(archive / "2025/01/2025-01-02.jsonl.gz", "rb") as stream:
        assert stream.read() == old_payload
    with gzip.open(
            archive / "2025/01/2025-01-03.jsonl.bak.gz", "rb") as stream:
        assert stream.read() == b"backup\n"


def test_existing_verified_archive_makes_retry_idempotent(tmp_path):
    now = 2_000_000_000.0
    live = tmp_path / "turns"
    archive = tmp_path / "archive"
    live.mkdir()
    source = _dated_file(
        live, "2025-02-03.jsonl", b"same bytes\n", now - 90 * DAY)
    first = archive_daily_logs(live, archive, now=now)
    assert first["archived_files"] == 1

    # Simulate a crash after archive commit but before source unlink.
    source.write_bytes(b"same bytes\n")
    os.utime(source, (now - 90 * DAY, now - 90 * DAY))
    second = archive_daily_logs(live, archive, now=now)

    assert second["archived_files"] == 1
    assert second["failures"] == []
    assert not source.exists()


def test_archive_age_and_byte_caps_prevent_a_second_unbounded_store(tmp_path):
    now = 2_000_000_000.0
    live = tmp_path / "turns"
    archive = tmp_path / "archive"
    live.mkdir()
    (archive / "2024/01").mkdir(parents=True)
    expired = archive / "2024/01/expired.jsonl.gz"
    with gzip.open(expired, "wb") as stream:
        stream.write(b"expired")
    os.utime(expired, (now - 400 * DAY, now - 400 * DAY))

    report = archive_daily_logs(
        live, archive, now=now, archive_days=365, max_archive_bytes=1)

    assert report["pruned_archives"] == 1
    assert not expired.exists()


def test_plain_log_rotates_only_over_limit_and_keeps_bounded_history(tmp_path):
    log = tmp_path / "vlm_server.log"
    archive = tmp_path / "archive"
    log.write_bytes(b"informational noise\n" * 100)

    report = rotate_plain_log(
        log, archive, max_bytes=100, keep=2, now=2_000_000_000.0)

    assert report["rotated"] is True
    assert log.exists() and log.stat().st_size == 0
    rotated = list(archive.glob("vlm_server.log.*.gz"))
    assert len(rotated) == 1
    with gzip.open(rotated[0], "rb") as stream:
        assert stream.read() == b"informational noise\n" * 100

    below = rotate_plain_log(
        log, archive, max_bytes=100, keep=2, now=2_000_000_001.0)
    assert below == {"rotated": False, "reason": "below_limit_or_missing"}
