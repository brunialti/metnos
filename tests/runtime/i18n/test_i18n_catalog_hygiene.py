"""Guardie strutturali sul catalogo i18n bundled di fresh install."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_SEED = _ROOT / "install" / "data" / "i18n_seed.sqlite"

_RETIRED_EXECUTORS = {
    "change_images", "compress_dirs_gz", "compress_files_gz",
    "compute_files", "describe_dirs", "extract_files_zip",
    "extract_lines_text", "fetch_urls", "get_file_dates",
    "get_files_metadata", "list_processes", "remove_dirs",
}

_REMOVED_EXACT = {
    "GREET", "TEST_KEY",
    "prompt.intent_extractor.system", "prompt.planner.system",
    "prompt.synt.stage1",
    "tool.request_location_from_user.description",
    "tool.request_new_executor.description",
    "MSG_ORCH_UNKNOWN_ERROR", "MSG_UNKNOWN_ERROR",
    "MSG_LOCATION_BUTTON_CANCEL", "UI_CHANGE_BTN_REJECT",
}


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_SEED}?mode=ro", uri=True)


def test_seed_is_symmetric_complete_and_has_no_pending_work():
    conn = _connect()
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        langs = {
            lang: count for lang, count in conn.execute(
                "SELECT lang, COUNT(*) FROM i18n GROUP BY lang"
            )
        }
        assert langs.get("it") == langs.get("en") and langs.get("it", 0) > 700
        assert conn.execute(
            "SELECT COUNT(*) FROM i18n WHERE text IS NULL OR trim(text)=''"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM i18n WHERE needs_translation=1"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_seed_has_no_retired_or_redundant_namespaces():
    conn = _connect()
    try:
        keys = {row[0] for row in conn.execute("SELECT DISTINCT key FROM i18n")}
    finally:
        conn.close()
    assert not (keys & _REMOVED_EXACT)
    assert not {key for key in keys if key.startswith("UI_PROP_TELOS_")}
    assert not {key for key in keys if key.startswith("UI_PROP_UNIFIED_")}
    retired = {
        key for key in keys
        if key.rsplit(".", 1)[0] in _RETIRED_EXECUTORS
        and key.endswith((".description", ".affinity"))
    }
    assert not retired


def test_seed_latest_wins_metadata_is_aligned(monkeypatch):
    """Use the actual provenance-aware alignment, not timestamp-only ordering."""
    import i18n_translator

    conn = _connect()
    try:
        assert not conn.execute(
            "SELECT key,lang FROM i18n WHERE version_hash IS NULL OR version_hash=''"
        ).fetchall()
        monkeypatch.setattr(i18n_translator.i18n, "_open", lambda: conn)
        # The seed is read-only and dry_run never calls a translator. Derived
        # rows must not become authoritative merely because their timestamp
        # equals or exceeds that of the editorial source (including EN).
        report = i18n_translator.align_messages(dry_run=True)
        assert report
        assert not [item for item in report if item.get("marked_for_retranslate")]
    finally:
        conn.close()
