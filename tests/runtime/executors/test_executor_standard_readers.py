"""Behavioral and natural-language gates for standardized file readers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.read_files_csv.read_files_csv import invoke as read_csv  # noqa: E402
from executors.read_files_xlsx import read_files_xlsx as xlsx_module  # noqa: E402
from executors.read_files_xlsx.read_files_xlsx import invoke as read_xlsx  # noqa: E402
from executors.get_images_indices import get_images_indices as index_status_module  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


def test_csv_mixed_result_is_explicitly_partial(tmp_path: Path) -> None:
    valid = tmp_path / "valid.csv"
    valid.write_text("name,age\nAda,36\n", encoding="utf-8")
    missing = tmp_path / "missing.csv"

    result = read_csv({"paths": [str(valid), str(missing)]})

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == 1
    assert result["fail_count"] == 1
    assert result["entries"][0]["rows"] == [{"name": "Ada", "age": "36"}]
    assert result["failed"][0]["error_class"] == "not_found"
    assert result["failed"][0]["error_code"] == "path_not_found"


def test_csv_invalid_root_and_content_fail_honestly(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.csv"
    invalid.write_bytes(b"\xff\xfe")

    root_result = read_csv([])
    content_result = read_csv({"paths": [str(invalid)]})

    assert root_result["error_code"] == "args_not_object"
    assert root_result["error_class"] == "invalid_input"
    assert content_result["ok"] is False
    assert content_result["failed"][0]["error_class"] == "invalid_content"
    assert content_result["failed"][0]["error_code"] == "csv_encoding_invalid"


def test_xlsx_mixed_result_is_explicitly_partial(tmp_path: Path) -> None:
    if xlsx_module.openpyxl is None:
        pytest.skip("openpyxl is not installed")
    valid = tmp_path / "valid.xlsx"
    workbook = xlsx_module.openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["name", "age"])
    sheet.append(["Ada", 36])
    workbook.save(valid)
    workbook.close()

    result = read_xlsx({"paths": [str(valid), str(tmp_path / "missing.xlsx")]})

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == 1
    assert result["fail_count"] == 1
    assert result["entries"][0]["rows"] == [{"name": "Ada", "age": 36}]
    assert result["failed"][0]["error_class"] == "not_found"
    assert result["failed"][0]["error_code"] == "path_not_found"


def test_xlsx_numeric_string_selects_sheet_by_index(tmp_path: Path) -> None:
    if xlsx_module.openpyxl is None:
        pytest.skip("openpyxl is not installed")
    path = tmp_path / "two-sheets.xlsx"
    workbook = xlsx_module.openpyxl.Workbook()
    first = workbook.active
    first.title = "First"
    first.append(["value"])
    first.append(["wrong"])
    second = workbook.create_sheet("Second")
    second.append(["value"])
    second.append(["selected"])
    workbook.save(path)
    workbook.close()

    result = read_xlsx({"paths": [str(path)], "sheet": "1"})

    assert result["ok"] is True
    assert result["entries"][0]["sheet"] == "Second"
    assert result["entries"][0]["rows"] == [{"value": "selected"}]


def test_xlsx_invalid_root_and_missing_dependency_fail_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_result = read_xlsx([])
    monkeypatch.setattr(xlsx_module, "openpyxl", None)
    dependency_result = read_xlsx({"paths": ["/tmp/any.xlsx"]})

    assert root_result["error_code"] == "args_not_object"
    assert root_result["error_class"] == "invalid_input"
    assert dependency_result["ok"] is False
    assert dependency_result["failed"][0]["error_class"] == "dependency_missing"
    assert dependency_result["failed"][0]["error_code"] == "openpyxl_missing"


def test_image_index_status_distinguishes_empty_and_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path / "data"))

    empty = index_status_module.invoke({"base_path": str(tmp_path / "photos")})
    invalid = index_status_module.invoke([])

    assert empty["ok"] is True
    assert empty["ok_count"] == 1
    assert all(entry["exists"] is False for entry in empty["entries"])
    assert invalid["error_class"] == "invalid_input"
    assert invalid["error_code"] == "args_not_object"


def test_image_index_status_reports_corrupt_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path / "data"))
    base = tmp_path / "photos"
    index_dir = index_status_module._index_root_for_base(base) / "unified"
    index_dir.mkdir(parents=True)
    (index_dir / "meta.json").write_text("{invalid", encoding="utf-8")

    result = index_status_module.invoke({"base_path": str(base), "idx": "all"})

    assert result["ok"] is False
    assert "partial" not in result
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
    assert result["failed"][0]["error_class"] == "invalid_content"
    assert result["failed"][0]["error_code"] == "index_metadata_invalid"


@pytest.fixture(scope="module")
def catalog(standard_catalog):
    return standard_catalog


@pytest.mark.parametrize(
    "query",
    [
        "leggi questa tabella csv con separatore punto e virgola",
        "importa le righe dai file di dati delimitati",
    ],
)
def test_csv_natural_paraphrases_remain_routable(query: str, catalog) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert "read_files_csv" in names, (query, names)


@pytest.mark.parametrize(
    "query",
    [
        "apri il foglio Excel chiamato Dati",
        "leggi le righe di questa cartella di lavoro xlsx",
    ],
)
def test_xlsx_natural_paraphrases_remain_routable(query: str, catalog) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert "read_files_xlsx" in names, (query, names)


@pytest.mark.parametrize(
    "query",
    [
        "mostrami lo stato dell'indice delle foto",
        "quanto e grande l'indice immagini e quando e stato aggiornato?",
    ],
)
def test_image_index_status_natural_paraphrases_remain_routable(
    query: str, catalog,
) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert "get_images_indices" in names, (query, names)


@pytest.mark.parametrize(
    "query",
    [
        "chi risulta registrato nel riconoscimento persone?",
        "elencami le persone per cui hai esempi del volto",
    ],
)
def test_person_registry_natural_paraphrases_remain_routable(
    query: str, catalog,
) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert "get_persons" in names, (query, names)
