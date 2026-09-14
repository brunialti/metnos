from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from manifest_lint import (  # noqa: E402
    _load_catalog_names,
    _pattern_call_args,
    lint_manifest,
)


@pytest.mark.parametrize("name", ["get_location", "find_packages", "run_processes"])
@pytest.mark.parametrize("language", ["it", "en"])
def test_repaired_turn_manifests_pass_the_actual_birth_linter(name, language):
    from i18n_materializer import decode_language_state
    path = RUNTIME.parent / "executors" / name / "manifest.toml"
    manifest = tomllib.loads(path.read_text())
    decode_language_state(path.with_name("manifest.lang_state.json").read_bytes(),
                          manifest=manifest)
    failures = [item for item in lint_manifest(manifest, language=language)
                if item.severity == "error"]
    assert failures == []


def test_pattern_args_ignore_nested_callback_properties() -> None:
    description = (
        "SCOPO: chiede conferma. "
        "PATTERN: get_approval(prompt='ok?', "
        "on_approve={executor='delete_files', args={paths=['/tmp/a']}}). "
        "NON: eseguire. OUT: entries=[]."
    )

    assert _pattern_call_args(description, "get_approval") == [
        "on_approve", "prompt",
    ]


def test_pattern_args_handle_nested_commas_equals_and_multiple_calls() -> None:
    description = (
        "SCOPO: invia. PATTERN: send_messages("
        "messages=[{body='a,b=c', to='x@y'}], via_channel='email'); "
        "send_messages(messages=[]). NON: leggere. OUT: results=[]."
    )

    assert _pattern_call_args(description, "send_messages") == [
        "messages", "messages", "via_channel",
    ]


def test_catalog_names_include_builtin_executor_contracts() -> None:
    names = _load_catalog_names({"read_files": set()})

    assert "read_files" in names
    assert "extract_entries" in names
    assert "list_tasks" in names


def test_purpose_specific_output_schema_is_standard_compliant() -> None:
    manifest = {
        "name": "get_clock",
        "description": (
            "SCOPO: legge l'ora. PATTERN: get_clock(). "
            "NON: eventi. OUT: {ok,now}."
        ),
        "output": {"schema_inline": "{ ok: bool, now: str }"},
    }

    checks = {
        finding.check for finding in lint_manifest(
            manifest, language="it", allow_flat_description=True,
        )
    }

    assert "output_shape" not in checks


def test_requested_language_never_falls_back_to_another_description() -> None:
    manifest = {
        "name": "get_clock",
        "description": {
            "en": (
                "SCOPO: reads time. PATTERN: get_clock(). "
                "NON: events. OUT: {ok,now}."
            ),
        },
    }

    findings = lint_manifest(manifest, language="nl")

    assert [item.check for item in findings].count("language_missing") == 1
    assert findings[0].languages == ("nl",)


def test_runtime_resolved_rule_uses_code_shape_not_natural_words() -> None:
    base = {
        "name": "read_persons",
        "args": {
            "properties": {
                "actor": {"type": "string", "runtime_resolved": True},
            },
        },
    }
    natural = dict(base, description={
        "en": (
            "SCOPO: reads the current actor. PATTERN: read_persons(). "
            "NON: contacts. OUT: entries."
        ),
    })
    passed = dict(base, description={
        "en": (
            "SCOPO: reads a profile. PATTERN: read_persons(actor=\"current\"). "
            "NON: contacts. OUT: entries."
        ),
    })

    natural_checks = {item.check for item in lint_manifest(natural, language="en")}
    passed_checks = {item.check for item in lint_manifest(passed, language="en")}

    assert "runtime_arg_passed" not in natural_checks
    assert "runtime_arg_passed" in passed_checks
