from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Mapping

import pytest

from i18n_activation import (
    ActivationBlocked,
    activate_language,
    gate,
    synchronize_public_hreflang,
    validate_manifests,
)
from i18n_materializer import LocalizationPaths, materialize
from i18n_pipeline import review_semantics, translate_pending
from i18n_registry import LocalizationRegistry

from test_i18n_materializer import _fixture
from test_i18n_pipeline import _translator


def _public_page(
    *, lang: str, canonical: str, alternates: Mapping[str, str], title: str,
) -> str:
    links = "\n".join(
        f'<link href="{href}" hreflang="{language}" rel="alternate">'
        for language, href in alternates.items()
    )
    return (
        '<!doctype html><html lang="' + lang + '"><head>'
        '<meta name="robots" content="index, follow">'
        '<link href="' + canonical + '" rel="canonical">'
        + links + '</head><body><h1>' + title + '</h1></body></html>'
    )


def _prepared(tmp_path: Path):
    paths = replace(
        _fixture(tmp_path),
        device_catalog=tmp_path / "device" / "messages_i18n.json",
    )
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending("nl", registry=registry, paths=paths, translator=_translator)
    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )
    return paths, registry


def _signed_target_only_lint_defect(tmp_path: Path, monkeypatch):
    """Build a real signed contract whose defect exists only in ``nl``."""
    import sign

    paths = replace(
        _fixture(tmp_path),
        device_catalog=tmp_path / "device" / "messages_i18n.json",
    )
    executor_dir = tmp_path / "executors" / "sample"
    (executor_dir / "sample.py").write_text(
        "def invoke(path):\n    return {'ok': bool(path)}\n",
        encoding="utf-8",
    )
    (executor_dir / "manifest.toml").write_text(
        'name="sample"\nversion="1.0.0"\n'
        '[description]\n'
        'en="SCOPO: reads. PATTERN: sample(path=\\\"x\\\"). '
        'NON: other tools. OUT: {ok}."\n'
        'nl="SCOPO: leest. PATTERN: sample(unknown=\\\"x\\\"). '
        'NON: andere tools. OUT: {ok}."\n'
        '[args]\ntype="object"\nrequired=["path"]\n'
        '[args.properties.path]\ntype="string"\n'
        '[args.properties.path.description]\n'
        'en="File path"\nnl="Bestandspad"\n'
        '[output]\nschema_inline="{ok: bool}"\n'
        '[code]\nfiles=["sample.py"]\n'
        'digest="sha256:placeholder"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "keys")
    sign.generate_keypair("author")
    sign.sign_executor(executor_dir)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    return registry


def test_real_activation_validator_checks_the_exact_target_language(
    tmp_path: Path, monkeypatch,
):
    registry = _signed_target_only_lint_defect(tmp_path, monkeypatch)

    with pytest.raises(ActivationBlocked, match="pattern_unknown_arg"):
        validate_manifests("nl", registry=registry)


def test_activation_passes_target_language_to_injected_validator(tmp_path: Path):
    paths = _fixture(tmp_path)
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    calls: list[tuple[Path, str]] = []

    validate_manifests(
        "nl",
        registry=registry,
        validator=lambda path, language: (calls.append((path, language)) or True, ""),
    )

    assert calls
    assert {language for _path, language in calls} == {"nl"}


def test_hreflang_synchronization_uses_declared_family_when_paths_differ(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    en_url = "https://metnos.com/en/Metnos_Dialogue_Executors_v1"
    it_url = "https://metnos.com/it/Metnos_Dialogo_Executor_v1"
    nl_url = "https://metnos.com/nl/Metnos_Dialogue_Executors_v1"
    unrelated_url = "https://metnos.com/fr/unrelated"
    family = {"en": en_url, "it": it_url}
    pages = {
        docs / "en" / "Metnos_Dialogue_Executors_v1.html": _public_page(
            lang="en", canonical=en_url, alternates=family, title="Dialogue",
        ),
        docs / "it" / "Metnos_Dialogo_Executor_v1.html": _public_page(
            lang="it", canonical=it_url, alternates=family, title="Dialogo",
        ),
        docs / "nl" / "Metnos_Dialogue_Executors_v1.html": _public_page(
            lang="nl", canonical=nl_url, alternates=family, title="Dialoog",
        ),
        # The old path join would have modified this unrelated page merely
        # because its relative filename happens to equal the Dutch one.
        docs / "fr" / "Metnos_Dialogue_Executors_v1.html": _public_page(
            lang="fr", canonical=unrelated_url,
            alternates={"fr": unrelated_url}, title="Sans rapport",
        ),
    }
    for path, text in pages.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    unrelated_before = next(
        text for path, text in pages.items() if path.parts[-2] == "fr"
    )

    paths = LocalizationPaths(docs=docs)
    assert synchronize_public_hreflang("nl", paths) == 3
    assert synchronize_public_hreflang("nl", paths) == 0

    for language, relative in (
        ("en", "Metnos_Dialogue_Executors_v1.html"),
        ("it", "Metnos_Dialogo_Executor_v1.html"),
        ("nl", "Metnos_Dialogue_Executors_v1.html"),
    ):
        text = (docs / language / relative).read_text(encoding="utf-8")
        assert f'hreflang="nl" href="{nl_url}"' in text
    assert (
        docs / "fr" / "Metnos_Dialogue_Executors_v1.html"
    ).read_text(encoding="utf-8") == unrelated_before

    from published_docs import catalog

    documents = catalog(docs)
    family_documents = [
        document for document in documents if document.lang in {"en", "it", "nl"}
    ]
    assert len(family_documents) == 3
    assert len({document.concept_key for document in family_documents}) == 1
    assert next(
        document for document in documents if document.lang == "fr"
    ).concept_key not in {document.concept_key for document in family_documents}


def test_hreflang_synchronization_fails_before_writing_an_invalid_family(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    en_url = "https://metnos.com/en/guide"
    nl_url = "https://metnos.com/nl/guide"
    missing_url = "https://metnos.com/it/guida-mancante"
    source = docs / "en" / "guide.html"
    target = docs / "nl" / "guide.html"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text(_public_page(
        lang="en", canonical=en_url, alternates={"en": en_url}, title="Guide",
    ), encoding="utf-8")
    target.write_text(_public_page(
        lang="nl", canonical=nl_url,
        alternates={"en": en_url, "it": missing_url}, title="Gids",
    ), encoding="utf-8")
    before = {path: path.read_bytes() for path in (source, target)}

    with pytest.raises(ActivationBlocked, match="not a published document"):
        synchronize_public_hreflang("nl", LocalizationPaths(docs=docs))

    assert {path: path.read_bytes() for path in (source, target)} == before


def test_gate_blocks_before_semantic_review(tmp_path: Path):
    paths = replace(
        _fixture(tmp_path),
        device_catalog=tmp_path / "device" / "messages_i18n.json",
    )
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translate_pending("nl", registry=registry, paths=paths, translator=_translator)
    report = gate("nl", registry=registry, paths=paths)
    assert not report.ok
    assert any("required check" in error for error in report.errors)


def test_activation_promotes_all_layers_then_flips_signed_authority(tmp_path: Path):
    paths, registry = _prepared(tmp_path)
    writes: list[dict] = []
    restarts: list[bool] = []

    def writer(**kwargs):
        writes.append(kwargs)
        return object(), True

    digest = hashlib.sha256(b"tutor").hexdigest()
    result = activate_language(
        "nl", registry=registry, paths=paths,
        signer=lambda _path: None,
        manifest_validator=lambda _path, _language: (True, ""),
        tutor_compiler=lambda: (digest, {"en", "nl"}),
        request_writer=writer,
        restart=lambda: restarts.append(True),
    )
    assert result.configuration_changed
    assert result.restarted
    assert writes[0]["instance_lang"] == "nl"
    assert writes[0]["state"] == "active"
    assert restarts == [True]
    assert paths.device_catalog.is_file()
    final = gate(
        "nl", registry=registry, paths=paths, require_admitted=True,
    )
    assert final.ok
    assert "input:confirm.yes" in final.exceptions


def test_activation_does_not_flip_config_when_device_catalog_is_incomplete(tmp_path: Path):
    paths, registry = _prepared(tmp_path)
    # This message is public in the source corpus but lacks a reviewed target.
    import sqlite3
    conn = sqlite3.connect(paths.public_messages_db)
    conn.execute(
        "INSERT INTO i18n(key,lang,text) VALUES('ERR_NEW','en','New error')"
    )
    conn.commit()
    conn.close()
    writes: list[dict] = []
    with pytest.raises(ActivationBlocked):
        activate_language(
            "nl", registry=registry, paths=paths,
            signer=lambda _path: None,
            manifest_validator=lambda _path, _language: (True, ""),
            tutor_compiler=lambda: ("x", {"nl"}),
            request_writer=lambda **kwargs: (writes.append(kwargs), True),
        )
    assert writes == []


def test_full_acceptance_is_idempotent_and_runtime_surfaces_share_locale(
    tmp_path: Path, monkeypatch,
):
    paths = replace(
        _fixture(tmp_path),
        device_catalog=tmp_path / "device" / "messages_i18n.json",
    )
    public = paths.docs / "en" / "page.html"
    public.write_text(
        '<!doctype html><html lang="en"><head>'
        '<link rel="canonical" href="https://example.test/en/page.html">'
        '<link rel="alternate" hreflang="en" '
        'href="https://example.test/en/page.html"></head>'
        '<body><h1>Guide</h1></body></html>',
        encoding="utf-8",
    )
    registry = LocalizationRegistry(tmp_path / "registry.sqlite")
    materialize("nl", registry=registry, paths=paths)
    translated = translate_pending(
        "nl", registry=registry, paths=paths, translator=_translator,
    )
    assert translated.failed == 0
    review_semantics(
        "nl", registry=registry, paths=paths,
        judge=lambda source, target, resource: bool(source and target and resource),
    )

    authority: dict[str, str] = {}

    def writer(**kwargs):
        changed = authority != kwargs
        authority.clear()
        authority.update(kwargs)
        return object(), changed

    digest = hashlib.sha256(b"public-tutor-catalog").hexdigest()
    arguments = dict(
        registry=registry,
        paths=paths,
        signer=lambda _path: None,
        manifest_validator=lambda _path, _language: (True, ""),
        tutor_compiler=lambda: (digest, {"en", "nl"}),
        request_writer=writer,
    )
    first = activate_language("nl", **arguments)
    second = activate_language("nl", **arguments)
    assert first.configuration_changed
    assert not second.configuration_changed
    assert second.promoted == 0
    assert authority["instance_lang"] == "nl"
    assert authority["state"] == "active"

    final = gate("nl", registry=registry, paths=paths, require_admitted=True)
    assert final.ok, final.errors
    assert final.total > 0
    assert final.admitted + len(final.exceptions) == final.total

    # Planner/proposer prompts, manifest prose, messages and input mappings
    # all resolve from the same admitted target corpus.
    prompt = paths.prompts / "nl" / "planner" / "core.j2"
    assert "Regel" in prompt.read_text(encoding="utf-8")
    assert 'nl = "SCOPO: Lees een bestand.' in (
        tmp_path / "executors" / "sample" / "manifest.toml"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(paths.messages_db)
    assert connection.execute(
        "SELECT text FROM i18n WHERE key='MSG_HELLO' AND lang='nl'"
    ).fetchone()[0] == "Hallo {name}"
    connection.close()
    connection = sqlite3.connect(paths.detection_db)
    assert json.loads(connection.execute(
        "SELECT payload FROM detection_lexicon "
        "WHERE concept='action.run' AND lang='nl'"
    ).fetchone()[0]) == ["uitvoeren"]
    connection.close()

    target_page = (paths.docs / "nl" / "page.html").read_text(encoding="utf-8")
    source_page = public.read_text(encoding="utf-8")
    assert '<html lang="nl">' in target_page
    assert 'hreflang="nl"' in target_page
    assert 'hreflang="nl"' in source_page

    # The remote device reads only the generated public bundle.
    shim_source = Path(__file__).parents[3] / "runtime" / "device_shim" / "messages.py"
    shim_copy = paths.device_catalog.parent / "messages.py"
    shutil.copy2(shim_source, shim_copy)
    monkeypatch.setenv("METNOS_LANG", "nl")
    monkeypatch.setenv("METNOS_SOURCE_LANG", "en")
    spec = importlib.util.spec_from_file_location("fixture_device_messages", shim_copy)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.get("MSG_HELLO", name="Ada") == "Hallo Ada"

    # A single absent target prompt falls back to the declared bootstrap
    # corpus; pending candidates remain invisible.
    import prompt_loader
    original = prompt.read_text(encoding="utf-8")
    prompt.unlink()
    monkeypatch.setattr(prompt_loader, "_BASE", paths.prompts)
    prompt_loader._envs.clear()
    assert prompt_loader.get("planner/core", "nl", value="x").startswith("Rule")
    prompt.write_text(original, encoding="utf-8")
