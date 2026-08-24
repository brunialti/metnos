from __future__ import annotations

import pytest

from manifest_lint import (
    PatternCall,
    PatternSyntaxError,
    lint_contract_translation,
    main,
    scan_pattern,
)


def _description(pattern: str, *, scope: str = "does work") -> str:
    return (
        f"SCOPO: {scope}. PATTERN: {pattern}. "
        "NON: other operations. OUT: entries."
    )


def _checks(source: str, target: str, *, resource: str = "description") -> set[str]:
    return {
        finding.check
        for finding in lint_contract_translation(
            source, target, resource=resource,
            source_language="en", target_language="nl",
        )
    }


def test_scanner_handles_nested_values_strings_operators_and_assignments() -> None:
    atoms = scan_pattern(
        "find_packages(query='a,b=(x)', filters=[{kind='app'}]) -> "
        "run_processes(from_step=1); all=true; from_step + columns"
    )

    assert atoms.calls == (
        PatternCall("find_packages", ("filters", "query")),
        PatternCall("run_processes", ("from_step",)),
    )
    assert atoms.standalone_assignments == ("all",)
    assert atoms.operator_identifiers == ("from_step+columns",)


def test_scanner_preserves_duplicate_calls_and_ignores_non_python_values() -> None:
    atoms = scan_pattern(
        "find_files(pattern=..., recursive=true); "
        "find_files(recursive=false, pattern='a\\\'b')"
    )

    assert atoms.calls == (
        PatternCall("find_files", ("pattern", "recursive")),
        PatternCall("find_files", ("pattern", "recursive")),
    )


@pytest.mark.parametrize(
    "pattern",
    (
        "find_files(path='unterminated)",
        "find_files(paths=['x')",
        "find_files(path='x', path='y')",
    ),
)
def test_scanner_rejects_unclosed_or_duplicate_machine_syntax(pattern: str) -> None:
    with pytest.raises(PatternSyntaxError):
        scan_pattern(pattern)


def test_scanner_abstains_on_natural_pattern_without_calls() -> None:
    atoms = scan_pattern("a natural-language example without a technical call")

    assert atoms.calls == ()
    assert atoms.standalone_assignments == ()
    assert atoms.operator_identifiers == ()


def test_scanner_does_not_turn_translated_parenthetical_prose_into_calls() -> None:
    source = _description(
        "get_images_google_photos(picker=true) = YOU pick in the Google UI "
        "(link in chat, whole albums too)"
    )
    target = _description(
        "get_images_google_photos(picker=true) = scegli TU nella UI Google "
        "(link in chat, anche album interi)"
    )

    assert "pattern_atoms" not in _checks(source, target)


def test_scanner_does_not_interpret_keyword_values_as_operator_atoms() -> None:
    atoms = scan_pattern("run_processes(columns=from_step+columns)")

    assert atoms.calls == (PatternCall("run_processes", ("columns",)),)
    assert atoms.operator_identifiers == ()


@pytest.mark.parametrize(
    ("source_pattern", "target_pattern"),
    (
        ("read_files(paths=['x'])", "get_files(paths=['x'])"),
        ("read_files(paths=['x'])", "read_files(paths=['x'], reason='why')"),
        ("read_files(paths=['x'])", "read_files(paths=['x']); read_files(paths=['y'])"),
    ),
)
def test_translation_rejects_changed_calls_keywords_or_multiplicity(
    source_pattern: str, target_pattern: str,
) -> None:
    assert "pattern_atoms" in _checks(
        _description(source_pattern), _description(target_pattern),
    )


def test_translation_allows_reordered_examples_and_changed_values() -> None:
    source = _description(
        "read_files(paths=['a']); read_files(paths=['b'], parse='json')"
    )
    target = _description(
        "read_files(parse='tekst', paths=['c']); read_files(paths=['d'])"
    )

    assert "pattern_atoms" not in _checks(source, target)


def test_runtime_placeholders_are_compared_as_a_multiset() -> None:
    source = _description("read_persons(name='${RUNTIME:actor}')")
    changed = _description("read_persons(name='${RUNTIME:now}')")
    duplicated = _description(
        "read_persons(name='${RUNTIME:actor} ${RUNTIME:actor}')"
    )

    assert "runtime_placeholders" in _checks(source, changed)
    assert "runtime_placeholders" in _checks(source, duplicated)


def test_template_placeholder_trims_only_immediately_internal_spaces() -> None:
    source = "Value {{ value }} and {{step1.path}}"
    equivalent = "Waarde {{value}} en {{ step1.path }}"
    changed = "Waarde {{value|trim}} en {{ step1.path }}"

    assert "template_placeholders" not in _checks(
        source, equivalent, resource="args.properties.value.description",
    )
    assert "template_placeholders" in _checks(
        source, changed, resource="args.properties.value.description",
    )


@pytest.mark.parametrize(
    "target",
    (
        "PATTERN: read_files(). SCOPO: reads. NON: writes. OUT: entries.",
        "SCOPO: reads. PATTERN: read_files(). PATTERN: read_files(). NON: writes. OUT: entries.",
        "SCOPE: reads. PATTERN: read_files(). NON: writes. OUT: entries.",
    ),
)
def test_translation_rejects_reordered_duplicated_or_translated_chapters(
    target: str,
) -> None:
    source = _description("read_files()")

    assert "chapter_order" in _checks(source, target)


def test_unclosed_placeholders_are_local_errors() -> None:
    findings = lint_contract_translation(
        "Use ${RUNTIME:actor", "Gebruik {{ value",
        resource="args.properties.value.description",
        source_language="en", target_language="nl",
    )

    assert {(item.check, item.scope, item.languages) for item in findings} == {
        ("runtime_placeholders", "local", ("en",)),
        ("template_placeholders", "local", ("nl",)),
    }


def test_strict_gate_preserves_real_warning_counts(
    tmp_path, capsys,
) -> None:
    executor = tmp_path / "sample"
    executor.mkdir()
    description = (
        "SCOPO: " + ("long text " * 35)
        + "PATTERN: sample(). NON: no other operation. OUT: {ok}."
    )
    manifest = executor / "manifest.toml"
    manifest.write_text(
        'name="sample"\n'
        f'[description]\nen={description!r}\n'
        '[args]\ntype="object"\n'
        '[output]\nschema_inline="{ok: bool}"\n',
        encoding="utf-8",
    )

    assert main([str(manifest)]) == 0
    normal = capsys.readouterr().out.rsplit("\n", 2)[-2]
    assert main(["--strict", str(manifest)]) == 1
    strict = capsys.readouterr().out.rsplit("\n", 2)[-2]

    assert normal == strict
    assert "0 error" in strict
    assert "warn" in strict
