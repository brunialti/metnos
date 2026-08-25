"""Anti-drift guards for the shared manifest contract authorities."""
from __future__ import annotations

from unittest import mock

import pytest

import manifest_lint
import manifest_normalize
import manifest_rules
from engine import coerce_args, validator
from i18n_materializer import manifest_language_selectors
from manifest_inventory import ManifestLayout


def _description(pattern: str) -> str:
    return (
        "SCOPO: legge dati strutturati richiesti dall'utente. "
        f"PATTERN: {pattern}. "
        "NON: modificare dati o impostazioni. OUT: entries=[]."
    )


def test_chapters_and_universal_args_have_one_runtime_authority() -> None:
    assert manifest_lint.CHAPTERS is manifest_rules.CHAPTERS
    assert manifest_normalize.CHAPTERS is manifest_rules.CHAPTERS
    assert manifest_lint.UNIVERSAL_ARGS is manifest_rules.UNIVERSAL_ARGS
    assert coerce_args.UNIVERSAL_ARGS is manifest_rules.UNIVERSAL_ARGS
    assert validator.UNIVERSAL_ARGS is manifest_rules.UNIVERSAL_ARGS


def test_lint_rejects_unknown_keyword_even_with_empty_schema() -> None:
    findings = manifest_lint.lint_manifest(
        {
            "name": "read_samples",
            "description": _description("read_samples(phantom=true)"),
            "args": {"type": "object", "properties": {}},
        },
        language="und",
        allow_flat_description=True,
    )

    assert [item.check for item in findings].count("pattern_unknown_arg") == 1


def test_normalizer_and_engine_share_the_universal_piping_args() -> None:
    description = _description(
        "read_samples(from_step=1, entries=[...])",
    )
    assert manifest_normalize.validate_head(
        "read_samples", description, {}, {"read_samples"},
        language="it",
    ) == []

    coerced, changed = coerce_args.coerce_step_args(
        {"from_step": 1, "entries": [], "phantom": True},
        {"type": "object", "properties": {"value": {"type": "string"}}},
    )
    assert coerced == {"from_step": 1, "entries": []}
    assert changed is True


def test_normalizer_uses_shared_pattern_and_chapter_checks() -> None:
    unknown = manifest_normalize.validate_head(
        "read_samples", _description("read_samples(phantom=true)"),
        {}, {"read_samples"}, language="it",
    )
    duplicated = manifest_normalize.validate_head(
        "read_samples",
        _description("read_samples()") + " PATTERN: read_samples().",
        {}, {"read_samples"}, language="en",
    )

    assert any("phantom" in error for error in unknown)
    assert any(error.startswith("chapter_order:") for error in duplicated)


def test_linter_visibility_is_the_canonical_pool_render() -> None:
    description = (
        "SCOPO: " + "contesto molto lungo " * 30
        + "PATTERN: read_samples(). NON: modificare. OUT: entries=[]."
    )

    assert manifest_lint._visible_to_llm(description) == (
        manifest_rules.render_head(description)
    )


def test_cli_requires_language_for_explicit_flat_legacy_text(
    tmp_path, capsys,
) -> None:
    executor = tmp_path / "read_samples"
    executor.mkdir()
    manifest_path = executor / "manifest.toml"
    manifest_path.write_text(
        'name = "read_samples"\n'
        f'description = {_description("read_samples()")!r}\n'
        '[args]\ntype = "object"\n',
        encoding="utf-8",
    )
    assert manifest_lint.main([str(manifest_path)]) == 1
    output = capsys.readouterr().out
    assert "language_missing" in output
    assert "lingua non attribuita" in output


def test_cli_uses_explicit_language_for_flat_legacy_text(tmp_path) -> None:
    executor = tmp_path / "read_samples"
    executor.mkdir()
    manifest_path = executor / "manifest.toml"
    manifest_path.write_text(
        'name = "read_samples"\n'
        f'description = {_description("read_samples()")!r}\n'
        '[args]\ntype = "object"\n',
        encoding="utf-8",
    )
    observed: list[str] = []
    real_lint = manifest_lint.lint_manifest

    def capture(manifest, *, language, **kwargs):
        observed.append(language)
        return real_lint(manifest, language=language, **kwargs)

    with mock.patch("manifest_lint.lint_manifest", side_effect=capture):
        assert manifest_lint.main([
            "--language", "IT-it", str(manifest_path),
        ]) == 0

    assert observed == ["it-it"]


def test_cli_all_never_attributes_flat_text_even_with_language(tmp_path) -> None:
    from manifest_inventory import (
        ContractId,
        ManifestInventory,
        ManifestOrigin,
        ManifestRef,
        ManifestStatus,
    )

    executor = tmp_path / "read_samples"
    executor.mkdir()
    manifest_path = executor / "manifest.toml"
    manifest_path.write_text(
        'name = "read_samples"\n'
        f'description = {_description("read_samples()")!r}\n'
        '[args]\ntype = "object"\n',
        encoding="utf-8",
    )
    contract_id = ContractId(ManifestOrigin.EXPLICIT, "manifest.toml")
    inventory = ManifestInventory((ManifestRef(
        contract_id=contract_id,
        origin=ManifestOrigin.EXPLICIT,
        status=ManifestStatus.ADMITTED,
        source_root=executor,
        manifest_path=manifest_path,
        manifest_relative="manifest.toml",
        allowed_code_roots=(executor,),
        name="read_samples",
    ),), ())

    with mock.patch(
        "manifest_inventory.inventory_authoring_manifests",
        return_value=inventory,
    ), mock.patch(
        "manifest_lint.lint_manifest",
        side_effect=AssertionError("flat --all text received a language"),
    ):
        assert manifest_lint.main(["--all", "--language", "it"]) == 1


def test_cli_language_restricts_multilingual_manifest(tmp_path) -> None:
    executor = tmp_path / "read_samples"
    executor.mkdir()
    manifest_path = executor / "manifest.toml"
    description = _description("read_samples()")
    manifest_path.write_text(
        'name = "read_samples"\n'
        '[description]\n'
        f'it = {description!r}\n'
        f'en = {description!r}\n'
        '[args]\ntype = "object"\n',
        encoding="utf-8",
    )
    observed: list[str] = []
    real_lint = manifest_lint.lint_manifest

    def capture(manifest, *, language, **kwargs):
        observed.append(language)
        return real_lint(manifest, language=language, **kwargs)

    with mock.patch("manifest_lint.lint_manifest", side_effect=capture):
        assert manifest_lint.main([
            "--language", "EN", str(manifest_path),
        ]) == 0

    assert observed == ["en"]


def test_linter_and_translator_use_canonical_language_selectors() -> None:
    manifest = {
        "description": {"it": "Top", "en": "Top"},
        "args": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": {"it": "Testo", "en": "Text"},
                },
            },
        },
    }
    expected = dict(manifest_language_selectors(manifest))

    assert dict(manifest_lint._localized_resource_tables(manifest)) == expected

    from i18n_translator import _enumerate_textual_resources

    assert dict(_enumerate_textual_resources(manifest)) == expected


def test_normalizer_refuses_authoring_mutation_after_cutover() -> None:
    with mock.patch(
        "manifest_inventory.resolve_manifest_layout",
        return_value=ManifestLayout.STORE_ONLY,
    ), mock.patch(
        "manifest_normalize._set_description_block",
        side_effect=AssertionError("authoring touched after cutover"),
    ):
        with pytest.raises(RuntimeError, match="versioned contract"):
            manifest_normalize.apply_one("read_samples", "it", "en")
