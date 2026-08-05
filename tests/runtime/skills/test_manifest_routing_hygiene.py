"""Regression guards for the M6/M7 manifest-routing audit (22/7/2026)."""
from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _manifest(name: str) -> dict:
    with (ROOT / "executors" / name / "manifest.toml").open("rb") as handle:
        return tomllib.load(handle)


def _affinity(name: str) -> list[str]:
    return [str(item).casefold() for item in _manifest(name).get("affinity", [])]


def test_m6_named_affinity_contaminants_do_not_return() -> None:
    forbidden = {
        "login_urls": {"spaggiari", "scuola", "banca"},
        "find_persons_indices": {"carol", "bob"},
        "get_urls": {"leggi", "pagina", "page", "read"},
        "find_dirs": {"list", "elenca"},
        "find_contacts": {"persona", "person", "amici", "friends"},
        "group_entries": {"group by", "raggruppa per"},
        "get_persons": {"chi", "who"},
        "share_files": {"revoke", "revoca"},
        "set_messages": {"imap"},
    }
    for executor, words in forbidden.items():
        assert not (set(_affinity(executor)) & words), executor


def test_m6_qualified_delete_and_web_image_affinities() -> None:
    assert all(" " in item for item in _affinity("delete_events"))
    web_terms = _affinity("find_images_web")
    assert "immagine" not in web_terms
    assert "image" not in web_terms
    assert any("web" in item or "online" in item for item in web_terms)


def test_m6_metadata_affinity_and_reciprocal_boundaries() -> None:
    metadata = " ".join(_affinity("get_files"))
    for marker in ("size", "dimensione", "byte", "date", "data"):
        assert marker in metadata

    boundaries = {
        "get_persons": ("read_persons", "find_persons_indices", "set_persons"),
        "describe_numbers": ("compute_entries",),
        "read_files": ("read_files_spreadsheet", "read_files_xlsx", "read_files_ocr"),
        "sort_entries": ("persist", "compute_entries", "filter_entries"),
        "login_urls": ("login_sites",),
    }
    for executor, markers in boundaries.items():
        description = " ".join(
            str(value).casefold()
            for value in (_manifest(executor).get("description") or {}).values()
        )
        for marker in markers:
            assert marker in description, (executor, marker)


def test_m7_set_signatures_affinity_is_bilingual() -> None:
    affinity = set(_affinity("set_signatures"))
    assert {"authorize command", "allow command", "deny command"} <= affinity
