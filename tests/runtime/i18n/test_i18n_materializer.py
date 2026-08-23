from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from i18n_materializer import LocalizationPaths, inventory, materialize
from i18n_registry import LocalizationRegistry


def _fixture(tmp_path: Path) -> LocalizationPaths:
    prompts = tmp_path / "prompts"
    (prompts / "en" / "planner").mkdir(parents=True)
    (prompts / "en" / "planner" / "core.j2").write_text("Rule {{ value }}\n", encoding="utf-8")
    (prompts / "en" / "shape.yaml").write_text("section: test\n", encoding="utf-8")

    executors = tmp_path / "executors" / "sample"
    executors.mkdir(parents=True)
    (executors / "manifest.toml").write_text(
        'name="sample"\n[description]\nen="Read a file"\n'
        '[args]\ntype="object"\n[args.properties.path]\ntype="string"\n'
        '[args.properties.path.description]\nen="File path"\n'
        '[output]\nschema_inline="{ok: bool}"\n',
        encoding="utf-8",
    )

    messages = tmp_path / "messages.sqlite"
    conn = sqlite3.connect(messages)
    conn.executescript(
        """CREATE TABLE i18n(key TEXT,lang TEXT,text TEXT,needs_translation INTEGER DEFAULT 0,
        source_lang TEXT,source_text_hash TEXT,version_hash TEXT,updated_at TEXT,PRIMARY KEY(key,lang));
        INSERT INTO i18n(key,lang,text) VALUES('MSG_HELLO','en','Hello {name}');"""
    )
    conn.close()

    detection = tmp_path / "detection.sqlite"
    conn = sqlite3.connect(detection)
    conn.executescript(
        """CREATE TABLE detection_lexicon(concept TEXT,lang TEXT,kind TEXT,match_mode TEXT,
        payload TEXT,needs_translation INTEGER DEFAULT 0,source_lang TEXT,
        source_text_hash TEXT,version_hash TEXT,review_policy TEXT DEFAULT 'automatic',
        updated_at TEXT,PRIMARY KEY(concept,lang));
        INSERT INTO detection_lexicon VALUES('action.run','en','phrases','word',
        '["run"]',0,'en',NULL,NULL,'automatic',NULL);
        INSERT INTO detection_lexicon VALUES('confirm.yes','en','regex','word',
        '["^yes$"]',0,'en',NULL,NULL,'manual',NULL);"""
    )
    conn.close()

    docs = tmp_path / "docs" / "en"
    docs.mkdir(parents=True)
    (docs / "guide.html").write_text("<h1>Guide</h1>", encoding="utf-8")
    return LocalizationPaths(
        prompts=prompts, manifest_roots=(tmp_path / "executors",),
        messages_db=messages, detection_db=detection,
        public_messages_db=messages, docs=tmp_path / "docs",
        include_runtime_catalogs=False,
    )


def test_inventory_enumerates_all_supported_layers_without_schema_prose(tmp_path):
    paths = _fixture(tmp_path)
    items = inventory(paths, source_lang="en")
    ids = {item.resource_id for item in items}
    assert "prompt:planner/core.j2" in ids
    assert "prompt:shape.yaml" in ids
    assert "contract:sample:description" in ids
    assert "contract:sample:args.properties.path.description" in ids
    assert "message:MSG_HELLO" in ids
    assert "input:action.run" in ids
    assert "knowledge:guide.html" in ids
    assert "device:public-message-catalog" in ids
    assert "tutor:public-catalog" in ids
    assert not any("schema_inline" in resource_id for resource_id in ids)


def test_materialization_precedes_translation_and_is_idempotent(tmp_path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    first = materialize("nl", registry=registry, paths=paths)
    second = materialize("nl", registry=registry, paths=paths)
    assert first.resources == second.resources
    assert len(registry.resources("nl")) == first.resources
    assert registry.coverage("nl").by_status["pending"] >= 1
    state = json.loads((paths.prompts / "nl" / ".lang_state.json").read_text())
    assert state["planner/core.j2"]["source_lang"] == "en"
    assert (paths.prompts / "nl" / "_pending").is_dir()

    conn = sqlite3.connect(paths.messages_db)
    assert conn.execute(
        "SELECT needs_translation,source_lang FROM i18n WHERE key='MSG_HELLO' AND lang='nl'"
    ).fetchone() == (1, "en")
    conn.close()
    manual = next(row for row in registry.resources("nl") if row.resource_id == "input:confirm.yes")
    assert manual.status == "manual_review"


def test_materialization_migrates_legacy_detection_policy_schema(tmp_path):
    paths = _fixture(tmp_path)
    conn = sqlite3.connect(paths.detection_db)
    conn.executescript(
        """DROP TABLE detection_lexicon;
        CREATE TABLE detection_lexicon(concept TEXT,lang TEXT,kind TEXT,match_mode TEXT,
        payload TEXT,needs_translation INTEGER DEFAULT 0,source_lang TEXT,
        source_text_hash TEXT,version_hash TEXT,updated_at TEXT,
        PRIMARY KEY(concept,lang));
        INSERT INTO detection_lexicon VALUES('action.run','en','phrases','word',
        '["run"]',0,'en',NULL,NULL,NULL);"""
    )
    conn.close()

    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)

    conn = sqlite3.connect(paths.detection_db)
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(detection_lexicon)"
    )}
    target = conn.execute(
        "SELECT review_policy FROM detection_lexicon "
        "WHERE concept='action.run' AND lang='nl'"
    ).fetchone()
    conn.close()
    assert "review_policy" in columns
    assert target == ("automatic",)
