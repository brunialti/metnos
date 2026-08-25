from __future__ import annotations

import json
import hashlib
import sqlite3
import tomllib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contract_store import current_manifest, publish_signed_source
from i18n_materializer import (
    LocalizationPaths,
    encode_language_state,
    inventory,
    manifest_language_selectors,
    materialize,
)
from i18n_registry import LocalizationRegistry
from manifest_inventory import (
    ManifestOrigin,
    ManifestSource,
    inventory_manifests,
)
from sign import sign_manifest_bytes


def _fixture(tmp_path: Path) -> LocalizationPaths:
    prompts = tmp_path / "prompts"
    (prompts / "en" / "planner").mkdir(parents=True)
    (prompts / "en" / "planner" / "core.j2").write_text("Rule {{ value }}\n", encoding="utf-8")
    (prompts / "en" / "shape.yaml").write_text("section: test\n", encoding="utf-8")

    executors = tmp_path / "executors" / "sample"
    executors.mkdir(parents=True)
    (executors / "manifest.toml").write_text(
        'name="sample"\n[description]\n'
        'en="SCOPO: Read a file. PATTERN: sample(path=\\"/tmp/example\\"). '
        'NON: other operations. OUT: {ok}."\n'
        '[args]\ntype="object"\n[args.properties.path]\ntype="string"\n'
        '[args.properties.path.description]\nen="File path {{ value }}"\n'
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


def _logical_text_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _versioned_fixture(tmp_path: Path):
    paths = _fixture(tmp_path)
    directory = tmp_path / "executors" / "sample"
    code = directory / "sample.py"
    code.write_text(
        "def invoke(args):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    code_digest = "sha256:" + hashlib.sha256(code.read_bytes()).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "read_files"
version = "1.0.0"

[description]
en = "SCOPO: Read a file. PATTERN: sample(path=\\\"/tmp/example\\\"). NON: other operations. OUT: ok=true."
it = "SCOPO: Leggere un file. PATTERN: sample(path=\\\"/tmp/example\\\"). NON: altre operazioni. OUT: ok=true."

[code]
files = ["sample.py"]
digest = "{code_digest}"

[args]
type = "object"
required = ["path"]

[args.properties.path]
type = "string"

[args.properties.path.description]
en = "File path {{ value }}"
it = "Percorso del file {{ value }}"

[output]
schema_inline = "{{ok: bool}}"

[[capabilities]]
name = "compute:pure"
hint = []

[[tests]]
name = "sample"
input = {{ path = "/tmp/example" }}
expect = {{ ok = true }}
''',
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    state = {
        "schema_version": 1,
        "selectors": {
            selector: {
                language: {
                    "version_hash": _logical_text_hash(text),
                    "source_lang": None,
                    "source_hash": None,
                }
                for language, text in languages.items()
            }
            for selector, languages in manifest_language_selectors(parsed).items()
        },
    }
    (directory / "manifest.lang_state.json").write_bytes(
        encode_language_state(state, manifest=parsed)
    )
    private_key = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest.read_bytes(), private_key=private_key)
    )
    source = ManifestSource(
        ManifestOrigin.EXPLICIT,
        paths.manifest_roots[0],
        min_depth=1,
        max_depth=1,
        allowed_code_roots=(paths.manifest_roots[0],),
    )
    manifest_inventory = inventory_manifests((source,))
    assert not manifest_inventory.problems
    ref = manifest_inventory.admitted()[0]
    trusted = (("test-author", private_key.public_key()),)
    store = tmp_path / "contract-store-shadow"
    publication = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=store,
    )

    def snapshot_provider(current_ref):
        return current_manifest(
            current_ref,
            trusted_publics=trusted,
            store_root=store,
        )

    return paths, ref, private_key, trusted, store, publication, snapshot_provider


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
    contract = next(item for item in items if item.resource_id == "contract:sample:description")
    assert contract.metadata["origin"] == "explicit"
    assert contract.metadata["contract_id"] == "explicit:sample/manifest.toml"


def test_contract_inventory_does_not_promote_unselected_imports(tmp_path):
    paths = _fixture(tmp_path)
    imported = tmp_path / "imports" / "mail" / "read_mail"
    imported.mkdir(parents=True)
    (imported / "manifest.toml").write_text(
        'name="read_mail"\n[description]\nen="Read mail"\n',
        encoding="utf-8",
    )

    ids = {item.resource_id for item in inventory(paths, source_lang="en")}

    assert "contract:sample:description" in ids
    assert "contract:read_mail:description" not in ids


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


def test_versioned_contract_materialization_uses_verified_generation_without_mirror(
    tmp_path: Path,
):
    (
        paths,
        ref,
        _private_key,
        _trusted,
        _store,
        publication,
        snapshot_provider,
    ) = _versioned_fixture(tmp_path)
    authoring = {
        name: (ref.manifest_dir / name).read_bytes()
        for name in (
            "manifest.toml",
            "manifest.toml.sig",
            "manifest.lang_state.json",
        )
    }
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")

    materialize(
        "nl",
        registry=registry,
        paths=paths,
        contract_snapshot_provider=snapshot_provider,
    )

    records = [row for row in registry.resources("nl") if row.layer == "contract"]
    assert records
    assert {row.basis_id for row in records} == {publication.current_generation_id}
    assert all("manifest_path" not in row.metadata for row in records)
    assert all(row.metadata["contract_id"] == str(ref.contract_id) for row in records)
    assert all("language_hashes" in row.metadata for row in records)
    items = [
        item
        for item in inventory(
            paths,
            source_lang="en",
            contract_snapshot_provider=snapshot_provider,
        )
        if item.layer == "contract"
    ]
    assert all(item.contract_ref == ref for item in items)
    assert all(item.basis_id == publication.current_generation_id for item in items)
    assert authoring == {
        name: (ref.manifest_dir / name).read_bytes()
        for name in authoring
    }
