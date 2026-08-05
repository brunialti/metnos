from __future__ import annotations

import copy
import json
import sys
import tomllib
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from executor_standard import (  # noqa: E402
    STANDARD_ID,
    uses_active_profile,
    validate_for_lifecycle,
    validate_manifest,
)
from loader import load_catalog  # noqa: E402


def _manifest() -> dict:
    description = {
        "it": "SCOPO: legge file. PATTERN: read_files(paths=[]). NON: scrivere. OUT: entries=[].",
        "en": "SCOPO: reads files. PATTERN: read_files(paths=[]). NON: write. OUT: entries=[].",
    }
    return {
        "executor_standard": STANDARD_ID,
        "manifest_format": "1.0",
        "name": "read_files",
        "version": "1.0.0",
        "description": description,
        "code": {
            "files": ["read_files.py"],
            "digest": "sha256:" + "a" * 64,
        },
        "args": {
            "type": "object",
            "required": ["paths"],
            "properties": {
                "paths": {
                    "type": "array",
                    "description": {"it": "Percorsi.", "en": "Paths."},
                },
                "_actor": {
                    "type": "string",
                    "runtime_resolved": True,
                    "description": {"it": "Attore runtime.", "en": "Runtime actor."},
                },
            },
        },
        "output": {"schema_inline": "{ok: bool, entries: list, error?: str}"},
        "presentation": {
            "default_view": "list",
            "list": {"columns": [{"key": "item", "source": "$entry"}]},
        },
        "capabilities": [{"name": "fs:read", "hint": ["*"]}],
        "tests": [{"name": "empty", "input": {"paths": []}, "expect": {"ok": True}}],
        "platforms": ["linux"],
        "placement": {
            "scope": "any", "device_ok": True,
            "min_sandbox": "appcontainer",
        },
    }


def _codes(manifest: dict, **kwargs) -> set[str]:
    return {finding.code for finding in validate_manifest(manifest, **kwargs)}


def test_conforming_manifest_passes() -> None:
    assert validate_manifest(_manifest()) == []


def test_execution_policy_is_optional_and_defaults_to_serial() -> None:
    manifest = _manifest()
    assert validate_manifest(manifest) == []
    manifest["execution"] = {
        "effect": "unknown",
        "parallelism_class": 0,
        "resource_class": "default",
        "concurrency_key": "none",
        "equivalence_gate": "unverified",
    }
    assert validate_manifest(manifest) == []


def test_intelligence_mode_is_closed_and_optional() -> None:
    for mode in ("deterministic", "llm", "agentic"):
        manifest = _manifest()
        manifest["intelligence"] = mode
        assert validate_manifest(manifest) == []
    manifest = _manifest()
    manifest["intelligence"] = "smart"
    assert "intelligence_kind" in _codes(manifest)


def test_presentation_contract_is_checked_when_declared() -> None:
    manifest = _manifest()
    manifest["presentation"] = {
        "default_view": "list",
        "list": {
            "mode": "table",
            "columns": [{"key": "path", "source": "path", "cell_max": 240}],
            "max_rows": 200,
            "max_chars": 16000,
            "overflow": "notice",
        },
    }
    assert validate_manifest(manifest) == []
    manifest["presentation"]["default_view"] = "grid"
    assert "presentation_invalid" in _codes(manifest)


def test_legacy_entries_output_remains_loadable_during_catalog_migration() -> None:
    manifest = _manifest()
    manifest.pop("presentation")
    assert validate_manifest(manifest) == []


def test_parallel_execution_requires_explicit_equivalence_gate() -> None:
    manifest = _manifest()
    manifest["execution"] = {
        "effect": "read_only",
        "parallelism_class": 2,
        "resource_class": "local_io",
        "concurrency_key": "path",
        "equivalence_gate": "unverified",
    }
    manifest["tests"][0]["equivalence_runs"] = 2
    assert "execution_equivalence_required" in _codes(manifest)
    manifest["execution"]["equivalence_gate"] = "verified"
    assert validate_manifest(manifest) == []


def test_parallel_execution_requires_hermetic_equivalence_case() -> None:
    manifest = _manifest()
    manifest["execution"] = {
        "effect": "read_only",
        "parallelism_class": 1,
        "resource_class": "local_io",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    }
    assert "execution_equivalence_test" in _codes(manifest)
    manifest["tests"][0]["equivalence_runs"] = 2
    assert validate_manifest(manifest) == []


def test_parallel_creation_requires_isolation_key_but_is_not_forbidden() -> None:
    manifest = _manifest()
    manifest["execution"] = {
        "effect": "create_only",
        "parallelism_class": 1,
        "resource_class": "network_io",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    }
    manifest["tests"][0]["equivalence_runs"] = 2
    assert "execution_mutation_key" in _codes(manifest)
    manifest["execution"]["concurrency_key"] = "account"
    assert validate_manifest(manifest) == []


def test_candidate_cannot_self_promote_to_parallel_execution() -> None:
    manifest = _manifest()
    manifest["lifecycle"] = "synthesized"
    manifest["execution"] = {
        "effect": "read_only",
        "parallelism_class": 1,
        "resource_class": "local_io",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    }
    assert "execution_candidate_parallel" in {
        finding.code for finding in validate_for_lifecycle(manifest)
    }


def test_execution_policy_rejects_unknown_values_and_unsafe_limits() -> None:
    manifest = _manifest()
    manifest["execution"] = {
        "effect": "magic",
        "parallelism_class": 9,
        "resource_class": "gpu_magic",
        "concurrency_key": "global_guess",
        "equivalence_gate": "claimed",
        "extra": "field",
    }
    assert {
        "execution_unknown", "execution_parallelism_class",
        "execution_effect",
        "execution_resource_class", "execution_concurrency_key",
        "execution_equivalence_gate",
    } <= _codes(manifest)


def test_missing_declaration_is_legacy_only() -> None:
    manifest = _manifest()
    manifest.pop("executor_standard")
    assert "standard_missing" in _codes(manifest)
    assert "standard_missing" not in _codes(manifest, require_declaration=False)


def test_unknown_standard_and_noncanonical_name_fail() -> None:
    manifest = _manifest()
    manifest["executor_standard"] = "metnos.executor/9.0"
    manifest["name"] = "download_stuff"
    assert {"standard_unknown", "canonical_name"} <= _codes(manifest)


def test_runtime_argument_marker_is_authority_and_cannot_be_required() -> None:
    manifest = _manifest()
    actor = manifest["args"]["properties"].pop("_actor")
    manifest["args"]["properties"]["actor"] = actor
    assert validate_manifest(manifest) == []
    manifest["args"]["required"].append("actor")
    assert "runtime_arg_required" in _codes(manifest)


def test_argument_type_accepts_closed_json_schema_unions() -> None:
    manifest = _manifest()
    manifest["args"]["properties"]["paths"]["type"] = ["string", "array"]
    assert "arg_type" not in _codes(manifest)

    manifest["args"]["properties"]["paths"]["type"] = []
    assert "arg_type" in _codes(manifest)

    manifest["args"]["properties"]["paths"]["type"] = ["array", "invented"]
    assert "arg_type" in _codes(manifest)


def test_active_profile_requires_i18n_output_tests_and_authority() -> None:
    manifest = _manifest()
    manifest["description"].pop("en")
    manifest["output"] = {}
    manifest["tests"] = []
    manifest["capabilities"] = []
    codes = _codes(manifest)
    assert {"description_language", "output_schema", "tests", "capabilities"} <= codes


def test_active_tests_require_executable_case_or_pytest_reference() -> None:
    manifest = _manifest()
    manifest["tests"] = [{"name": "ornamental"}]
    assert "test_evidence" in _codes(manifest)

    manifest["tests"] = [{
        "name": "domain_suite",
        "reference": "tests/runtime/executors/test_executor_standard.py",
    }]
    assert validate_manifest(manifest) == []

    manifest["tests"][0].update({"input": {}, "expect": {"ok": True}})
    assert "test_mode" in _codes(manifest)


def test_candidate_profile_allows_unfinished_activation_artifacts() -> None:
    manifest = _manifest()
    manifest["description"] = {"it": manifest["description"]["it"]}
    manifest["code"]["digest"] = "sha256:placeholder"
    manifest.pop("output")
    manifest.pop("tests")
    manifest.pop("capabilities")
    assert validate_manifest(manifest, active=False) == []


def test_lifecycle_profile_is_closed_and_cannot_weaken_history() -> None:
    assert uses_active_profile("proposed") is False
    assert uses_active_profile("synthesized") is False
    assert uses_active_profile("active") is True
    assert uses_active_profile("deprecated") is True
    assert uses_active_profile("archived") is True
    assert uses_active_profile("unexpected") is True


def test_validate_for_lifecycle_uses_candidate_profile_only_in_quarantine() -> None:
    manifest = _manifest()
    manifest["description"] = {"it": manifest["description"]["it"]}
    manifest.pop("output")
    manifest.pop("tests")
    manifest.pop("capabilities")
    manifest["lifecycle"] = "synthesized"
    assert validate_for_lifecycle(manifest) == []
    manifest["lifecycle"] = "active"
    assert {"description_language", "output_schema", "tests", "capabilities"} <= {
        finding.code for finding in validate_for_lifecycle(manifest)
    }


def test_reversibility_claims_are_consistent() -> None:
    manifest = _manifest()
    manifest["revertible"] = True
    assert "reverse_pattern" in _codes(manifest)
    manifest["reverse_pattern"] = "swap_src_dst"
    assert "reverse_pattern" not in _codes(manifest)


def test_legacy_reversibility_key_never_grants_conformance() -> None:
    manifest = _manifest()
    manifest["reversible"] = True
    assert "reversibility_key" in _codes(manifest)


def test_top_level_authority_fields_nested_in_capability_are_rejected() -> None:
    manifest = _manifest()
    manifest["capabilities"][0]["reverse_pattern"] = "restore"
    assert "capability_misplaced_field" in _codes(manifest)


def test_filesystem_scope_annotations_are_typed_and_argument_bound() -> None:
    manifest = _manifest()
    capability = manifest["capabilities"][0]
    capability["path_args"] = ["paths"]
    capability["parent_path_args"] = ["paths"]
    assert validate_manifest(manifest) == []

    capability["path_args"] = ["missing"]
    assert "filesystem_scope_annotation_arg" in _codes(manifest)
    capability["path_args"] = "paths"
    assert "filesystem_scope_annotation_shape" in _codes(manifest)


def test_filesystem_scope_annotations_cannot_attach_to_other_authority() -> None:
    manifest = _manifest()
    manifest["capabilities"][0] = {
        "name": "compute:pure", "hint": [], "path_args": ["paths"],
    }

    assert "filesystem_scope_annotation_family" in _codes(manifest)


def test_outbound_args_are_typed_bound_and_external_only() -> None:
    manifest = _manifest()
    manifest["args"]["properties"]["local_context"] = {
        "type": "object",
        "description": {"it": "Contesto locale.", "en": "Local context."},
    }
    capability = manifest["capabilities"][0]
    capability.update({
        "name": "llm:online", "hint": [],
        "outbound_args": ["local_context"],
    })
    assert validate_manifest(manifest) == []

    capability["outbound_args"] = ["missing"]
    assert "outbound_args_arg" in _codes(manifest)
    capability["outbound_args"] = "local_context"
    assert "outbound_args_shape" in _codes(manifest)
    capability["outbound_args"] = ["local_context"]
    capability["name"] = "compute:pure"
    assert "outbound_args_family" in _codes(manifest)


def test_active_standard_claim_rejects_unknown_capability() -> None:
    manifest = _manifest()
    manifest["capabilities"][0]["name"] = "network"
    assert "capability_unknown" in _codes(manifest)


def test_capabilities_used_by_migrated_executors_are_canonical() -> None:
    from policy import CAPABILITY_REGISTRY

    assert {
        "compute:pure", "fs:read", "index:read", "metnos:read",
        "provider:access", "dialog.user_input", "network:sites",
        "auth.password_storage", "drive:permissions",
        "metnos:write", "metnos:create", "metnos:credentials_metadata_only",
        "mail:write", "system:undo",
    } <= set(CAPABILITY_REGISTRY)


def _provider_manifest() -> dict:
    manifest = _manifest()
    manifest["args"]["properties"]["client"] = {
        "type": "string",
        "enum": ["local", "google_workspace"],
        "default": "local",
        "description": {"it": "Backend.", "en": "Backend."},
    }
    manifest["capabilities"].append({
        "name": "provider:access",
        "hint": ["google-workspace"],
        "when": {"arg": "client", "values": ["google_workspace"]},
    })
    return manifest


def test_conditional_provider_authority_is_coherent() -> None:
    assert validate_manifest(_provider_manifest()) == []


def test_provider_backend_requires_declared_authority() -> None:
    manifest = _provider_manifest()
    manifest["capabilities"].pop()
    assert "provider_authority" in _codes(manifest)


def test_provider_authority_cannot_be_active_for_local_backend() -> None:
    manifest = _provider_manifest()
    manifest["capabilities"][-1].pop("when")
    assert "provider_authority" in _codes(manifest)


def test_provider_condition_rejects_malformed_unknown_and_outside_enum() -> None:
    manifest = _provider_manifest()
    capability = manifest["capabilities"][-1]

    capability["when"] = {"arg": "client"}
    assert "capability_when_shape" in _codes(manifest)

    capability["when"] = {"arg": "missing", "values": ["google_workspace"]}
    assert "capability_when_arg_unknown" in _codes(manifest)

    capability["when"] = {"arg": "client", "values": ["github"]}
    assert "capability_when_value_enum" in _codes(manifest)


def test_provider_authority_rejects_unknown_binding() -> None:
    manifest = _provider_manifest()
    manifest["capabilities"][-1]["hint"] = ["invented-provider"]
    assert "provider_hint_unknown" in _codes(manifest)


def test_two_provider_selector_grants_exactly_the_selected_binding() -> None:
    manifest = _provider_manifest()
    manifest["args"]["properties"]["client"]["enum"].append("github")
    manifest["capabilities"].append({
        "name": "provider:access",
        "hint": ["github"],
        "when": {"arg": "client", "values": ["github"]},
    })
    assert validate_manifest(manifest) == []


def test_validator_does_not_mutate_manifest() -> None:
    manifest = _manifest()
    before = copy.deepcopy(manifest)
    validate_manifest(manifest)
    assert manifest == before


def _write_loader_fixture(root: Path, *, declaration: str = "") -> None:
    executor = root / "read_files"
    executor.mkdir()
    (executor / "read_files.py").write_text(
        "def invoke(args):\n    return {'ok': True, 'entries': []}\n"
        "if __name__ == '__main__':\n    pass\n",
        encoding="utf-8",
    )
    (executor / "manifest.toml").write_text(
        f'''manifest_format = "1.0"
{declaration}
name = "read_files"
version = "1.0.0"
affinity = ["file"]

[description]
it = "SCOPO: legge file. PATTERN: read_files(paths=[]). NON: scrivere. OUT: entries=[]."
en = "SCOPO: reads files. PATTERN: read_files(paths=[]). NON: write. OUT: entries=[]."

[code]
files = ["read_files.py"]
digest = "sha256:{'a' * 64}"

[args]
type = "object"
required = []

[output]
schema_inline = "{{ok: bool, entries: list}}"

[presentation]
default_view = "list"

[presentation.list]
columns = [{{key = "item", source = "$entry"}}]

[[capabilities]]
name = "fs:read"
hint = ["*"]

[[tests]]
name = "empty"
input = {{}}
expect = {{ok = true}}
''',
        encoding="utf-8",
    )


def test_loader_accepts_legacy_without_claim(tmp_path: Path) -> None:
    _write_loader_fixture(tmp_path)
    catalog = load_catalog(
        executors_dir=tmp_path, verify=False, include_synth=False,
        include_verb_unique=False,
    )
    assert "read_files" in catalog.executors
    executor = catalog.executors["read_files"]
    assert executor.standard_state == "legacy"
    assert executor.executor_standard == ""
    assert executor.source == "handcrafted"
    assert executor.transport == "local-subprocess"
    assert executor.output_schema == "{ok: bool, entries: list}"


def test_loader_enforces_declared_standard(tmp_path: Path) -> None:
    _write_loader_fixture(
        tmp_path, declaration=f'executor_standard = "{STANDARD_ID}"',
    )
    catalog = load_catalog(
        executors_dir=tmp_path, verify=False, include_synth=False,
        include_verb_unique=False,
    )
    assert "read_files" in catalog.executors, catalog.rejected
    executor = catalog.executors["read_files"]
    assert executor.executor_standard == STANDARD_ID
    assert executor.standard_state == "declared"


def test_loader_rejects_false_standard_claim(tmp_path: Path) -> None:
    _write_loader_fixture(
        tmp_path, declaration='executor_standard = "metnos.executor/9.0"',
    )
    catalog = load_catalog(
        executors_dir=tmp_path, verify=False, include_synth=False,
        include_verb_unique=False,
    )
    assert "read_files" not in catalog.executors
    assert any("standard_unknown" in reason for _path, reason in catalog.rejected)


def test_core_legacy_inventory_is_frozen_and_exact() -> None:
    root = RUNTIME.parent
    inventory = json.loads(
        (RUNTIME / "executor_legacy_inventory.json").read_text(encoding="utf-8"),
    )
    assert inventory["schema_version"] == 1
    assert inventory["standard"] == STANDARD_ID
    names = inventory["legacy_names"]
    assert names == sorted(set(names))

    actual_legacy = []
    for path in sorted((root / "executors").glob("*/manifest.toml")):
        with path.open("rb") as handle:
            manifest = tomllib.load(handle)
        if manifest.get("executor_standard") is None:
            actual_legacy.append(str(manifest.get("name") or path.parent.name))

    assert actual_legacy == names, (
        "new core executors must declare the standard; migrated executors must "
        "be removed from executor_legacy_inventory.json"
    )
