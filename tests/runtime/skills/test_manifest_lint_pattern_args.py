from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from manifest_lint import (  # noqa: E402
    _load_catalog_names,
    _pattern_call_args,
    lint_manifest,
)


def test_pattern_args_ignore_nested_callback_properties() -> None:
    description = (
        "SCOPO: chiede conferma. "
        "PATTERN: get_approval(prompt='ok?', "
        "on_approve={executor='delete_files', args={paths=['/tmp/a']}}). "
        "NON: eseguire. OUT: entries=[]."
    )

    assert _pattern_call_args(description, "get_approval") == [
        "prompt", "on_approve",
    ]


def test_pattern_args_handle_nested_commas_equals_and_multiple_calls() -> None:
    description = (
        "SCOPO: invia. PATTERN: send_messages("
        "messages=[{body='a,b=c', to='x@y'}], via_channel='email'); "
        "send_messages(messages=[]). NON: leggere. OUT: results=[]."
    )

    assert _pattern_call_args(description, "send_messages") == [
        "messages", "via_channel", "messages",
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

    checks = {finding.check for finding in lint_manifest(manifest)}

    assert "output_shape" not in checks
