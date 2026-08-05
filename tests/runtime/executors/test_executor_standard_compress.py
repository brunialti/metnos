"""Standard, atomic publication, undo, and sandbox gates for compress_files."""
from __future__ import annotations

import sys
import tomllib
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.compress_files import compress_files  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402
from reverse_patterns import _delete_created_paths  # noqa: E402


MANIFEST = ROOT / "executors" / "compress_files" / "manifest.toml"


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_manifest_is_cross_platform_and_declares_complete_contract() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["revertible"] is True
    assert manifest["reverse_pattern"] == "delete_created_paths"
    assert manifest["placement"] == {
        "scope": "any", "device_ok": True,
        "min_sandbox": "appcontainer",
    }
    assert manifest["platforms"] == ["linux", "windows"]
    assert manifest["args"]["requires_one_of"] == [
        ["paths", "entries", "from_step"]]
    assert manifest["args"]["properties"]["entries"][
        "runtime_resolved"] is True
    assert {item["name"] for item in manifest["capabilities"]} == {
        "fs:read", "fs:write"}
    assert "schema_inline" in manifest["output"]


@pytest.mark.parametrize("root", [None, [], "paths"])
def test_non_object_root_fails_closed(root) -> None:
    result = compress_files.invoke(root)

    assert result["ok"] is False
    assert result["error_class"] == "invalid_args"
    assert result["error_code"] == "ERR_ARG_INVALID"


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "archive.zip"
    source.write_bytes(b"source")
    destination.write_bytes(b"pre-existing")

    result = compress_files.invoke({
        "paths": [str(source)], "dest": str(destination)})

    assert result["ok"] is False
    assert result["error_code"] == "ERR_DST_EXISTS"
    assert destination.read_bytes() == b"pre-existing"
    assert source.read_bytes() == b"source"


def test_zip_is_atomic_pipeable_and_does_not_modify_sources(
        tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    destination = tmp_path / "archive.zip"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    result = compress_files.invoke({
        "paths": [str(first), str(second)], "dest": str(destination)})

    assert result["ok"] is True
    assert result["ok_count"] == 2
    assert result["fail_count"] == 0
    assert result["results"] == [{
        "path": str(destination), "created": True, "file_count": 2,
        "archive_bytes": destination.stat().st_size, "format": "zip",
    }]
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["first.txt", "second.txt"]
        assert archive.read("first.txt") == b"first"
        assert archive.read("second.txt") == b"second"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert not list(tmp_path.glob(".archive.zip.metnos-*"))


def test_runtime_entries_are_projected_to_paths(tmp_path: Path) -> None:
    source = tmp_path / "entry.txt"
    destination = tmp_path / "entry.zip"
    source.write_text("entry", encoding="utf-8")

    result = compress_files.invoke({
        "entries": [{"path": str(source), "other": "preserved-upstream"}],
        "dest": str(destination),
    })

    assert result["ok"] is True
    assert result["added"] == [str(source)]


def test_invalid_runtime_entry_is_typed_without_echoing_record(
        tmp_path: Path) -> None:
    destination = tmp_path / "entry.zip"
    secret_marker = "must-not-leak-from-entry"

    result = compress_files.invoke({
        "entries": [{"other": secret_marker}], "dest": str(destination)})

    assert result["ok"] is False
    assert result["failed"][0]["error_code"] == "ERR_ARG_INVALID"
    assert secret_marker not in str(result)
    assert not destination.exists()


def test_undo_removes_archive_and_only_new_empty_parents(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "created" / "nested" / "archive.zip"
    source.write_text("source", encoding="utf-8")

    result = compress_files.invoke({
        "paths": [str(source)], "dest": str(destination)})
    reversed_result = _delete_created_paths({}, result)

    assert result["ok"] is True
    assert result["dirs_created"] == [
        str(tmp_path / "created"), str(tmp_path / "created" / "nested")]
    assert reversed_result["ok"] is True
    assert not destination.exists()
    assert not (tmp_path / "created").exists()
    assert source.read_text(encoding="utf-8") == "source"


def test_writer_failure_leaves_no_archive_temp_or_new_parent(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "created" / "nested" / "archive.zip"
    source.write_text("source", encoding="utf-8")

    def fail_writer(*_args, **_kwargs):
        raise OSError("simulated writer failure")

    monkeypatch.setattr(compress_files, "_write_archive", fail_writer)
    result = compress_files.invoke({
        "paths": [str(source)], "dest": str(destination)})

    assert result["ok"] is False
    assert result["error_code"] == "ERR_OP_FAILED"
    assert not destination.exists()
    assert not (tmp_path / "created").exists()
    assert not list(tmp_path.rglob("*.metnos-*"))


def test_publication_race_preserves_competing_destination(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "archive.zip"
    source.write_text("source", encoding="utf-8")
    real_writer = compress_files._write_archive

    def race_writer(path, fmt, sources):
        real_writer(path, fmt, sources)
        destination.write_bytes(b"competitor")

    monkeypatch.setattr(compress_files, "_write_archive", race_writer)
    result = compress_files.invoke({
        "paths": [str(source)], "dest": str(destination)})

    assert result["ok"] is False
    assert result["error_code"] == "ERR_DST_EXISTS"
    assert destination.read_bytes() == b"competitor"
    assert not list(tmp_path.glob(".archive.zip.metnos-*"))


def test_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "comprimi questi file in un nuovo archivio zip",
        "create a tar archive containing these files",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "compress_files" in names, (query, names)


def test_real_bubblewrap_archive_and_undo_roundtrip(tmp_path: Path) -> None:
    import agent_runtime
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")

    source = tmp_path / "source.txt"
    destination = tmp_path / "new" / "archive.zip"
    source.write_text("sandbox source", encoding="utf-8")
    executor = _catalog().executors["compress_files"]

    result = agent_runtime.invoke_executor(
        executor,
        {"paths": [str(source)], "dest": str(destination)},
        timeout_s=15,
        actor="host",
        channel="test",
    )
    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")

    assert result["ok"] is True, result
    assert source.read_text(encoding="utf-8") == "sandbox source"
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("source.txt") == b"sandbox source"
    reversed_result = _delete_created_paths({}, result)
    assert reversed_result["ok"] is True
    assert not destination.exists()
    assert not (tmp_path / "new").exists()
    assert source.exists()
