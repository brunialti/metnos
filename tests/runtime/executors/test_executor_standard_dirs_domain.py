"""Domain gate for the standardized ``dirs`` executor family."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.create_dirs import create_dirs  # noqa: E402
from executors.delete_dirs import delete_dirs  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


def _manifest(name: str) -> dict:
    path = ROOT / "executors" / name / "manifest.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_domain_members_declare_standard_and_closed_authority() -> None:
    for name in ("create_dirs", "delete_dirs", "find_dirs", "list_dirs"):
        manifest = _manifest(name)
        assert manifest["executor_standard"] == "metnos.executor/1.0"
        assert "schema_inline" in manifest["output"]
    for name in ("create_dirs", "delete_dirs"):
        manifest = _manifest(name)
        assert manifest["placement"] == {
            "scope": "any", "device_ok": True,
            "min_sandbox": "appcontainer",
        }
        assert manifest["platforms"] == ["linux", "windows"]


def test_dispatchers_reject_invalid_root_and_client_types() -> None:
    for invoke in (create_dirs.invoke, delete_dirs.invoke):
        root = invoke([])
        client = invoke({"paths": [], "client": 42})
        assert root["error_class"] == "invalid_input"
        assert root["error_code"] == "args_not_object"
        assert client["error_code"] == "client_not_string"


def test_create_repeat_and_reverse_only_remove_created_dir(tmp_path: Path) -> None:
    target = tmp_path / "created"
    first = create_dirs.invoke({"paths": [str(target)], "client": "local"})
    second = create_dirs.invoke({"paths": [str(target)], "client": "local"})

    assert first["ok"] is True and first["results"][0]["created"] is True
    assert second["ok"] is True and second["results"][0]["created"] is False
    reversed_result = create_dirs.reverse(
        {"args": {"client": "local"}}, first)
    assert reversed_result["ok"] is True
    assert not target.exists()


def test_delete_is_idempotent_for_empty_and_preserves_nonempty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    full = tmp_path / "full"
    empty.mkdir()
    full.mkdir()
    child = full / "keep.txt"
    child.write_text("keep", encoding="utf-8")

    result = delete_dirs.invoke({
        "paths": [str(empty), str(full)], "client": "local",
    })

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == 1 and result["fail_count"] == 1
    assert not empty.exists()
    assert child.read_text(encoding="utf-8") == "keep"


def test_dirs_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    cases = {
        "crea due nuove cartelle in questa directory": "create_dirs",
        "cancella queste directory vuote": "delete_dirs",
        "trova le sottocartelle chiamate archivio": "find_dirs",
        "elenca le cartelle presenti qui": "list_dirs",
    }
    for query, expected in cases.items():
        names = [item.name for item in rank(query, entries, k=10, min_score=1)]
        assert expected in names, (query, names)
