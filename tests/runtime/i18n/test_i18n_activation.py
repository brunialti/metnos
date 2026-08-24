from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from i18n_activation import (
    ActivationBlocked,
    activate_language,
    gate,
    validate_manifests,
)
from i18n_materializer import materialize
from i18n_pipeline import review_semantics, translate_pending
from i18n_registry import LocalizationRegistry

from test_i18n_materializer import _fixture
from test_i18n_pipeline import _translator


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
        manifest_validator=lambda _path: (True, ""),
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
            manifest_validator=lambda _path: (True, ""),
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
        manifest_validator=lambda _path: (True, ""),
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
    assert 'nl = "Lees een bestand"' in (
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
