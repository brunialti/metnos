from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from i18n_materializer import materialize
from i18n_pipeline import promote_candidates, review_semantics, translate_pending
from i18n_registry import LocalizationRegistry

from test_i18n_materializer import _fixture


def _translator(text: str, _source: str, target: str, _context: str) -> str:
    replacements = {
        "Read a file": "Lees een bestand",
        "File path": "Bestandspad",
        "Hello": "Hallo",
        "Guide": "Gids",
        "Rule": "Regel",
        "run": "uitvoeren",
    }
    out = text
    for source, translated in replacements.items():
        out = out.replace(source, translated)
    return out.replace("lang: en", f"lang: {target}")


def test_translate_validate_review_and_promote_all_nonmanual_layers(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    report = translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    assert report.failed == 0
    assert report.translated >= 7
    candidate = paths.prompts / "nl" / "_pending" / "planner/core.j2.candidate"
    assert "{{ value }}" in candidate.read_text()

    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )
    promoted = promote_candidates(
        "nl", registry=registry, paths=paths, signer=lambda _path: None,
    )
    assert promoted.errors == {}
    assert (paths.prompts / "nl" / "planner/core.j2").is_file()
    assert (paths.docs / "nl" / "guide.html").read_text() == "<h1>Gids</h1>"

    manifest = (tmp_path / "executors/sample/manifest.toml").read_text()
    assert 'nl = "Lees een bestand"' in manifest
    assert 'schema_inline="{ok: bool}"' in manifest
    conn = sqlite3.connect(paths.detection_db)
    mapping = json.loads(conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept='action.run' AND lang='nl'"
    ).fetchone()[0])
    assert mapping == ["uitvoeren"]
    assert conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept='confirm.yes' AND lang='nl'"
    ).fetchone()[0] is None
    conn.close()


def test_placeholder_loss_fails_and_never_creates_live_prompt(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("sv", registry=registry, paths=paths)

    def broken(text: str, *_args: str) -> str:
        return text.replace("{{ value }}", "värde")

    report = translate_pending(
        "sv", registry=registry, paths=paths, translator=broken,
    )
    assert "prompt:planner/core.j2" in report.errors
    assert not (paths.prompts / "sv" / "planner/core.j2").exists()


def test_prompt_loader_does_not_consume_pending_candidate(tmp_path: Path, monkeypatch):
    import prompt_loader
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "sample.j2").write_text("fallback", encoding="utf-8")
    (tmp_path / "nl" / "_pending").mkdir(parents=True)
    (tmp_path / "nl" / "_pending" / "sample.j2.candidate").write_text("candidate", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "_BASE", tmp_path)
    prompt_loader._envs.clear()
    assert prompt_loader.get("sample", "nl") == "fallback"


def test_manifest_promotion_rolls_back_when_signing_fails(tmp_path: Path):
    paths = _fixture(tmp_path)
    manifest = tmp_path / "executors/sample/manifest.toml"
    signature = manifest.with_name("manifest.toml.sig")
    before = manifest.read_bytes()
    signature.write_bytes(b"original-signature")
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )

    def broken_signer(_path):
        signature.write_bytes(b"partial")
        raise RuntimeError("signing failed")

    report = promote_candidates(
        "nl", registry=registry, paths=paths, signer=broken_signer,
    )
    assert any(key.startswith("contract:sample:") for key in report.errors)
    assert manifest.read_bytes() == before
    assert signature.read_bytes() == b"original-signature"
    assert all(
        row.status == "translated"
        for row in registry.resources("nl") if row.layer == "contract"
    )


def test_semantic_review_model_calls_are_bounded(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    calls: list[str] = []
    reviewed = review_semantics(
        "nl", registry=registry, paths=paths, limit=1,
        judge=lambda _source, _target, resource: not calls.append(resource),
    )
    assert len(calls) == 1
    assert sum(reviewed.values()) == 1
