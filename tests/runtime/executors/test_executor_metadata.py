from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from executor_metadata import (  # noqa: E402
    intelligence_kind, membership_kind, output_schema, source_kind, standard_state,
    transport_kind,
)
from executor_standard import STANDARD_ID  # noqa: E402


def test_standard_state_does_not_overclaim_behavioral_conformance() -> None:
    assert standard_state(None) == "legacy"
    assert standard_state(STANDARD_ID) == "declared"
    assert standard_state("metnos.executor/9.0") == "invalid"
    assert standard_state(STANDARD_ID, "proposed") == "candidate"
    assert standard_state(None, "synthesized") == "candidate"


def test_source_kind_keeps_executor_origin_axes_distinct() -> None:
    imported = {"provenance": {"imported_from": "https://example.test/skill"}}
    github_builtin = {"origin": "handcrafted"}
    assert source_kind(imported, synthesized=True) == "imported"
    assert source_kind(github_builtin, synthesized=True) == "handcrafted"
    assert source_kind({}, synthesized=True) == "synthesized"
    assert source_kind({}, builtin=True) == "builtin"
    assert source_kind({}) == "handcrafted"


def test_membership_is_independent_from_origin_and_install_path() -> None:
    assert membership_kind({"origin": "handcrafted"}) == "builtin"
    assert membership_kind({}, builtin=True) == "builtin"
    assert membership_kind({
        "provenance": {"imported_from": "https://example.test/skill"},
    }) == "third-party"


def test_intelligence_is_general_manifest_metadata() -> None:
    assert intelligence_kind({}) == "deterministic"
    assert intelligence_kind({
        "capabilities": [{"name": "llm:local"}],
    }) == "llm"
    assert intelligence_kind({
        "intelligence": "agentic",
        "capabilities": [{"name": "llm:local"}],
    }) == "agentic"
    assert intelligence_kind({"intelligence": "unknown"}) == "deterministic"


def test_transport_kind_uses_placement_without_inventing_actual_target() -> None:
    assert transport_kind({}) == "local-subprocess"
    assert transport_kind({"placement": {"scope": "device"}}) == "remote-device"
    assert transport_kind({
        "placement": {"scope": "any", "device_ok": True},
    }) == "local-or-remote"
    assert transport_kind({}, in_process=True) == "in-process"


def test_output_schema_is_normalized_from_declared_data_only() -> None:
    assert output_schema({"output": {"schema_inline": "  {ok: bool}  "}}) == "{ok: bool}"
    assert output_schema({}) == ""
