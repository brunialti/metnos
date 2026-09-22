"""Conformance, mutation, reverse and sandbox gates for ``move_files``."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.move_files import move_files  # noqa: E402
from backends.files import local  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402
import sandbox  # noqa: E402


MANIFEST = ROOT / "executors" / "move_files" / "manifest.toml"


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(
        ROOT / "executors", value, False, is_synthesized=False)
    return value


def test_manifest_declares_mutation_and_reverse_contract() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["revertible"] is True
    assert manifest["reverse_pattern"] == [
        "swap_src_dst", "restore_blob_backup", "delete_created_dirs"]
    assert manifest["platforms"] == ["linux", "windows"]
    assert manifest["placement"] == {
        "scope": "any", "device_ok": True,
        "min_sandbox": "appcontainer",
    }
    assert manifest["args"]["requires_one_of"] == [["from_step", "entries"]]
    assert manifest["capabilities"] == [{
        "name": "fs:write", "hint": ["~/notes/**", "/tmp/**"]}]
    assert "schema_inline" in manifest["output"]
    assert not {"ordina", "organizza", "riorganizza", "organize"} & set(
        manifest["affinity"])


@pytest.mark.parametrize("root", [None, [], "entries"])
def test_dispatcher_rejects_non_object_root(root) -> None:
    result = move_files.invoke(root)

    assert result["ok"] is False
    assert result["error_class"] == "invalid_args"
    assert result["error_code"] == "ERR_ARG_INVALID"
    assert result["results"] == []


def test_empty_and_invalid_input_are_distinct() -> None:
    empty = move_files.invoke({"entries": [], "dst_template": "/tmp/{name}"})
    invalid = move_files.invoke({
        "entries": "bad", "dst_template": "/tmp/{name}"})

    assert empty["ok"] is True
    assert empty["ok_count"] == 0
    assert invalid["ok"] is False
    assert invalid["error_class"] == "invalid_args"
    assert invalid["error_code"] == "ERR_ARG_INVALID"


def test_complete_and_mixed_failures_are_typed_and_partial(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    mixed = move_files.invoke({
        "entries": [{"path": str(source)}, {"path": str(missing)}],
        "dst_template": str(tmp_path / "moved_{name}"),
    })

    assert mixed["ok"] is False
    assert mixed["partial"] is True
    assert mixed["ok_count"] == 1
    assert mixed["fail_count"] == 1
    assert mixed["error_class"] == "not_found"
    assert mixed["error_code"] == "ERR_PATH_NOT_FOUND"
    assert not source.exists()
    assert (tmp_path / "moved_source.txt").read_text(encoding="utf-8") == "source"

    complete = move_files.invoke({
        "entries": [{"path": str(missing)}],
        "dst_template": str(tmp_path / "again_{name}"),
    })
    assert complete["ok"] is False
    assert "partial" not in complete
    assert complete["error_class"] == "not_found"
    assert complete["error_code"] == "ERR_PATH_NOT_FOUND"


def test_dependency_failure_does_not_claim_or_apply_move(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    def fail_move(*_args, **_kwargs):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(local.shutil, "move", fail_move)
    result = move_files.invoke({
        "entries": [{"path": str(source)}],
        "dst_template": str(tmp_path / "destination.txt"),
    })

    assert result["ok"] is False
    assert result["error_class"] == "operation_failed"
    assert result["error_code"] == "ERR_OP_FAILED"
    assert source.read_text(encoding="utf-8") == "source"
    assert not (tmp_path / "destination.txt").exists()


def test_ambiguous_path_failure_keeps_standard_envelope(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    monkeypatch.setattr(
        local,
        "_check_mutating_path_ambiguity",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error_code": "ERR_AMBIGUOUS_PATH",
            "error": "ambiguous fixture",
            "candidates": [{"path": "/tmp/a"}, {"path": "/tmp/b"}],
            "input_path": str(source),
        },
    )

    result = move_files.invoke({
        "entries": [{"path": str(source)}],
        "dst_template": str(tmp_path / "destination.txt"),
    })

    assert result["ok"] is False
    assert result["error_class"] == "ambiguous_input"
    assert result["error_code"] == "ERR_AMBIGUOUS_PATH"
    assert result["results"] == []
    assert result["candidates"] == [
        {"path": "/tmp/a"}, {"path": "/tmp/b"}]
    assert source.read_text(encoding="utf-8") == "source"


def test_interruption_never_returns_false_success(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(local.shutil, "move", interrupt)
    with pytest.raises(KeyboardInterrupt):
        move_files.invoke({
            "entries": [{"path": str(source)}],
            "dst_template": str(tmp_path / "destination.txt"),
        })
    assert source.read_text(encoding="utf-8") == "source"


def test_move_reverse_and_repeat_preserve_postcondition(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "nested" / "destination.txt"
    source.write_bytes(b"round-trip")

    result = move_files.invoke({
        "entries": [{"path": str(source)}],
        "dst_template": str(destination),
    })
    assert result["ok"] is True
    assert not source.exists()
    assert destination.read_bytes() == b"round-trip"

    repeated = move_files.invoke({
        "entries": [{"path": str(source)}],
        "dst_template": str(destination),
    })
    restored = move_files.reverse({"args": {"client": "local"}}, result)

    assert repeated["ok"] is False
    assert repeated["error_code"] == "ERR_PATH_NOT_FOUND"
    assert restored["ok"] is True
    assert source.read_bytes() == b"round-trip"
    assert not destination.exists()
    assert not (tmp_path / "nested").exists()


def test_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "sposta questi documenti nella cartella archivio",
        "rename and relocate these files into the archive folder",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "move_files" in names, (query, names)


def test_real_bubblewrap_move_and_reverse_roundtrip(tmp_path: Path) -> None:
    import agent_runtime

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")

    source = tmp_path / "sandbox-source.txt"
    destination = tmp_path / "sandbox-destination.txt"
    source.write_bytes(b"sandbox move")
    executor = _catalog().executors["move_files"]

    result = agent_runtime.invoke_executor(
        executor,
        {"entries": [{"path": str(source)}], "dst_template": str(destination)},
        timeout_s=15,
        actor="host",
        channel="test",
    )
    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")
    assert result["ok"] is True, result
    assert destination.read_bytes() == b"sandbox move"
    restored = move_files.reverse({"args": {"client": "local"}}, result)
    assert restored["ok"] is True
    assert source.read_bytes() == b"sandbox move"
