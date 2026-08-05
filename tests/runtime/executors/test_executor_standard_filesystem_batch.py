"""Behavioural, routing and remote-payload gates for filesystem readers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.find_dirs.find_dirs import invoke as find_dirs  # noqa: E402
from executors.list_dirs.list_dirs import invoke as list_dirs  # noqa: E402
from executors.read_files.read_files import invoke as read_files  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


@pytest.mark.parametrize("invoke", [list_dirs, find_dirs, read_files])
def test_non_object_root_fails_honestly(invoke) -> None:
    result = invoke([])
    assert result["ok"] is False
    assert result["error_code"] == "ERR_ARG_INVALID"


def test_list_dirs_contract_is_stable(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    result = list_dirs({"path": str(tmp_path)})

    assert result["ok"] is True
    assert result["metadata"]["count"] == 2
    assert [entry["name"] for entry in result["entries"]] == ["a.txt", "sub"]
    assert result["entries"][0]["kind"] == "text"


def test_find_dirs_local_contract_is_stable(tmp_path: Path) -> None:
    (tmp_path / "a" / "nested").mkdir(parents=True)
    (tmp_path / "a" / "one.txt").write_text("1", encoding="utf-8")

    result = find_dirs({"base_path": str(tmp_path), "client": "local"})

    assert result["ok"] is True
    assert result["metadata"]["count_dirs"] == 2
    assert result["metadata"]["file_count_total"] == 1
    assert set(result["matches"]) == {
        str(tmp_path / "a"), str(tmp_path / "a" / "nested"),
    }


def test_read_files_scalar_and_vector_contracts_are_distinct(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")

    scalar = read_files({"path": str(a), "client": "local"})
    vector = read_files({"paths": [str(a), str(b)], "client": "local"})

    assert scalar["ok"] is True
    assert scalar["content"] == "A"
    assert scalar["metadata"]["path"] == str(a)
    assert vector["ok"] is True
    assert vector["ok_count"] == 2
    assert [entry["content"] for entry in vector["entries"]] == ["A", "B"]


@pytest.mark.parametrize(
    ("name", "query"),
    [
        ("list_dirs", "mostrami il contenuto immediato di questa cartella"),
        ("find_dirs", "trova tutte le sottocartelle e le loro dimensioni"),
        ("read_files", "leggi il contenuto di questi file di testo"),
    ],
)
def test_natural_paraphrases_remain_routable(name: str, query: str,
                                              catalog) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert name in names, (query, names)


@pytest.fixture(scope="module")
def catalog(standard_catalog):
    return standard_catalog


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "entries": [{"path": "/x", "name": "x"}],
         "metadata": {"count": 1}},
        {"ok": True, "entries": [{"path": "/x/a", "file_count": 2}],
         "matches": ["/x/a"], "metadata": {"count_dirs": 1}},
        {"ok": True, "content": "hello",
         "metadata": {"path": "/x/a.txt", "bytes": 5}},
    ],
)
def test_remote_transport_preserves_full_domain_payload(payload, monkeypatch) -> None:
    import remote_exec

    class Executor:
        name = "filesystem_reader"
        revertible = False

    wire = {
        "invocation_id": "inv-files",
        "device_id": "device-files",
        "ok": payload["ok"],
        "entries": payload.get("entries", []),
        "n_processed": len(payload.get("entries", [])),
        "elapsed_ms": 4,
        "sandbox": "job-object",
        "payload": payload,
    }
    monkeypatch.setattr(remote_exec.invocations, "enqueue_invocation",
                        lambda *_a, **_kw: "inv-files")
    monkeypatch.setattr(remote_exec.invocations, "wait_result",
                        lambda *_a, **_kw: wire)
    monkeypatch.setattr("devices.get_device", lambda _device: None)

    result = remote_exec.invoke_remote(
        Executor(), {}, "device-files", timeout_s=1,
    )

    for key, value in payload.items():
        assert result[key] == value
    assert result["_remote"]["device_id"] == "device-files"
