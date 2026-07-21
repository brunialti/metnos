"""Deterministic validator for the Metnos Executor Standard.

The normative text lives in ``EXECUTOR_STANDARD.md``.  This module checks only
claims that can be proven from a manifest.  Behavioral requirements such as a
real postcondition or safe retry semantics remain test and review gates; a
manifest boolean cannot prove them.

Legacy manifests are intentionally not rejected merely because they do not yet
declare the standard.  The CLI requires the declaration by default; inventory
runs opt into ``--report-legacy``.  Programmatic admission calls
``validate_manifest(..., require_declaration=True)`` before signing.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STANDARD_ID = "metnos.executor/1.0"
SUPPORTED_MANIFEST_FORMAT = "1.0"
CANDIDATE_LIFECYCLES = frozenset({"proposed", "synthesized"})
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DESCRIPTION_ANCHORS = ("SCOPO:", "PATTERN:", "NON:", "OUT:")
_PLATFORMS = frozenset({"linux", "windows", "macos"})
_PLACEMENT_SCOPES = frozenset({"server", "device", "any"})
_EXECUTION_EFFECTS = frozenset({
    "unknown", "read_only", "create_only", "reversible", "mutating",
    "interactive",
})
_EXECUTION_RESOURCE_CLASSES = frozenset({
    "default", "local_io", "network_io", "cpu", "llm", "browser", "device",
})
_EXECUTION_KEYS = frozenset({
    "none", "device", "account", "browser_session", "path",
})
_EQUIVALENCE_GATES = frozenset({"unverified", "verified"})
_EXECUTION_FIELDS = frozenset({
    "effect", "parallelism_class", "resource_class", "concurrency_key",
    "equivalence_gate",
})
_JSON_SCHEMA_TYPES = frozenset({
    "array", "boolean", "integer", "null", "number", "object", "string",
})
_TOP_LEVEL_AUTHORITY_KEYS = frozenset({
    "revertible", "reversible", "reverse_pattern", "placement", "platforms",
})


@dataclass(frozen=True)
class StandardFinding:
    code: str
    message: str


def uses_active_profile(lifecycle: object) -> bool:
    """Return whether standard validation must apply activation gates.

    ``proposed`` and ``synthesized`` are quarantined candidate states. Every
    other lifecycle represents an executor that is active now or was admitted
    previously, so weakening its checks would turn lifecycle into a bypass.
    """
    return str(lifecycle or "active") not in CANDIDATE_LIFECYCLES


def _add(findings: list[StandardFinding], code: str, message: str) -> None:
    findings.append(StandardFinding(code, message))


def _language_map(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _validate_description(findings: list[StandardFinding], manifest: dict,
                          *, active: bool) -> None:
    descriptions = _language_map(manifest.get("description"))
    if not descriptions:
        _add(findings, "description_map", "description must be a non-empty language map")
        return

    required_langs = ("it", "en") if active else ()
    for lang in required_langs:
        if not isinstance(descriptions.get(lang), str) or not descriptions[lang].strip():
            _add(findings, "description_language", f"description.{lang} is required")

    for lang, text in descriptions.items():
        if not isinstance(text, str) or not text.strip():
            _add(findings, "description_text", f"description.{lang} must be non-empty text")
            continue
        # These are protocol anchors, not prose. Keeping them identical across
        # languages makes linting and local-planner rendering deterministic.
        missing = [chapter for chapter in _DESCRIPTION_ANCHORS if chapter not in text]
        if missing:
            _add(
                findings, "description_chapters",
                f"description.{lang} is missing {', '.join(missing)}",
            )


def _validate_args(findings: list[StandardFinding], manifest: dict,
                   *, active: bool) -> None:
    args = manifest.get("args")
    if not isinstance(args, dict) or args.get("type") != "object":
        _add(findings, "args_object", "[args].type must be 'object'")
        return
    properties = args.get("properties") or {}
    if not isinstance(properties, dict):
        _add(findings, "args_properties", "args.properties must be an object")
        return
    required = args.get("required") or []
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        _add(findings, "args_required", "args.required must be a list of names")
        required = []
    unknown = sorted(set(required) - set(properties))
    if unknown:
        _add(findings, "args_required_unknown", f"required args are undeclared: {unknown}")

    for name, spec in properties.items():
        if not isinstance(spec, dict):
            _add(findings, "arg_schema", f"args.properties.{name} must be an object")
            continue
        declared_type = spec.get("type")
        valid_type = (
            isinstance(declared_type, str)
            and declared_type in _JSON_SCHEMA_TYPES
        ) or (
            isinstance(declared_type, list)
            and bool(declared_type)
            and len(declared_type) == len(set(declared_type))
            and all(
                isinstance(item, str) and item in _JSON_SCHEMA_TYPES
                for item in declared_type
            )
        )
        if not valid_type:
            _add(
                findings, "arg_type",
                f"args.properties.{name}.type must be a JSON Schema type "
                "or a non-empty list of unique JSON Schema types",
            )
        if spec.get("runtime_resolved"):
            if name in required:
                _add(findings, "runtime_arg_required", f"runtime arg {name!r} cannot be required")
        descriptions = _language_map(spec.get("description"))
        if active:
            for lang in ("it", "en"):
                if not isinstance(descriptions.get(lang), str) or not descriptions[lang].strip():
                    _add(
                        findings, "arg_description_language",
                        f"args.properties.{name}.description.{lang} is required",
                    )


def _validate_authority(findings: list[StandardFinding], manifest: dict) -> None:
    capabilities = manifest.get("capabilities")
    args_schema = manifest.get("args") if isinstance(manifest.get("args"), dict) else {}
    if not isinstance(capabilities, list) or not capabilities:
        _add(findings, "capabilities", "at least one [[capabilities]] entry is required")
    else:
        try:
            from policy import CAPABILITY_REGISTRY
        except ImportError:
            CAPABILITY_REGISTRY = None
            _add(
                findings, "capability_authority",
                "canonical capability registry is unavailable",
            )
        try:
            from capabilities import (
                CapabilityConditionError,
                effective_capabilities,
                parse_condition,
            )
            from vocab import PROVIDER_SKILLS
        except ImportError:
            CapabilityConditionError = None
            effective_capabilities = None
            parse_condition = None
            PROVIDER_SKILLS = {}
            _add(
                findings, "capability_authority",
                "conditional capability authority is unavailable",
            )
        provider_bindings = set(PROVIDER_SKILLS.values())
        for index, capability in enumerate(capabilities):
            name = capability.get("name") if isinstance(capability, dict) else None
            if not isinstance(name, str):
                _add(findings, "capability_name", f"capabilities[{index}].name is required")
            elif CAPABILITY_REGISTRY is not None and name not in CAPABILITY_REGISTRY:
                _add(
                    findings, "capability_unknown",
                    f"capabilities[{index}].name {name!r} is not in the canonical registry",
                )
            if isinstance(capability, dict) and not isinstance(capability.get("hint", []), list):
                _add(findings, "capability_hint", f"capabilities[{index}].hint must be a list")
            if isinstance(capability, dict):
                misplaced = sorted(_TOP_LEVEL_AUTHORITY_KEYS & capability.keys())
                if misplaced:
                    _add(
                        findings, "capability_misplaced_field",
                        f"capabilities[{index}] contains top-level fields {misplaced}",
                    )
                if parse_condition is not None:
                    try:
                        parse_condition(capability, args_schema)
                    except CapabilityConditionError as exc:
                        _add(
                            findings, exc.code,
                            f"capabilities[{index}].{exc}",
                        )
                if name == "provider:access":
                    hints = capability.get("hint")
                    if (not isinstance(hints, list) or not hints
                            or any(not isinstance(hint, str) or not hint.strip()
                                   for hint in hints)):
                        _add(
                            findings, "provider_hint",
                            f"capabilities[{index}].hint must contain provider bindings",
                        )
                    else:
                        unknown = sorted(set(hints) - provider_bindings)
                        if unknown:
                            _add(
                                findings, "provider_hint_unknown",
                                f"capabilities[{index}] has unknown provider bindings {unknown}",
                            )
                if name == "fs:read":
                    properties = args_schema.get("properties") or {}
                    for hint in capability.get("hint", []) or []:
                        if not isinstance(hint, str) or not hint.startswith("arg:"):
                            continue
                        arg_name = hint[4:]
                        spec = properties.get(arg_name) if isinstance(properties, dict) else None
                        declared_type = spec.get("type") if isinstance(spec, dict) else None
                        if not arg_name or not isinstance(spec, dict):
                            _add(
                                findings, "filesystem_authority_arg",
                                f"capabilities[{index}] references undeclared argument {arg_name!r}",
                            )
                        elif declared_type not in ("string", "array"):
                            _add(
                                findings, "filesystem_authority_type",
                                f"capabilities[{index}] arg:{arg_name} must be string or array",
                            )

        # A typed ``client`` selector is checked extensionally: for every enum
        # value, the effective provider bindings must equal the one canonical
        # binding required by that provider (or the empty set for local/non-
        # provider backends). This catches missing, excessive and unreachable
        # declarations without executor-name allowlists.
        client_schema = ((args_schema.get("properties") or {}).get("client")
                         if isinstance(args_schema.get("properties"), dict)
                         else None)
        client_enum = client_schema.get("enum") if isinstance(client_schema, dict) else None
        if effective_capabilities is not None and isinstance(client_enum, list):
            for client in client_enum:
                if not isinstance(client, str):
                    continue
                actual: set[str] = set()
                for capability in effective_capabilities(
                        capabilities, args_schema, {"client": client}):
                    if capability.get("name") != "provider:access":
                        continue
                    actual.update(
                        hint for hint in capability.get("hint", [])
                        if isinstance(hint, str) and hint in provider_bindings
                    )
                expected = (
                    {PROVIDER_SKILLS[client]} if client in PROVIDER_SKILLS else set()
                )
                if actual != expected:
                    _add(
                        findings, "provider_authority",
                        f"client={client!r} requires provider bindings "
                        f"{sorted(expected)}, got {sorted(actual)}",
                    )

    platforms = manifest.get("platforms")
    if platforms is not None:
        if (not isinstance(platforms, list) or not platforms
                or any(platform not in _PLATFORMS for platform in platforms)):
            _add(findings, "platforms", f"platforms must use only {sorted(_PLATFORMS)}")

    placement = manifest.get("placement")
    if placement is not None:
        if not isinstance(placement, dict):
            _add(findings, "placement", "[placement] must be an object")
        elif placement.get("scope", "any") not in _PLACEMENT_SCOPES:
            _add(
                findings, "placement_scope",
                f"placement.scope must be one of {sorted(_PLACEMENT_SCOPES)}",
            )

    if "reversible" in manifest:
        _add(
            findings, "reversibility_key",
            "legacy key 'reversible' is unsupported; review semantics and use 'revertible'",
        )
    revertible = bool(manifest.get("revertible", False))
    reverse_pattern = manifest.get("reverse_pattern")
    if revertible and not reverse_pattern:
        _add(findings, "reverse_pattern", "revertible executors require reverse_pattern")
    if not revertible and reverse_pattern:
        _add(findings, "reverse_claim", "reverse_pattern requires revertible = true")


def _validate_execution(findings: list[StandardFinding], manifest: dict,
                        *, active: bool) -> None:
    """Validate optional scheduler metadata without changing legacy behavior."""
    execution = manifest.get("execution")
    if execution is None:
        return  # normative default is serial
    if not isinstance(execution, dict):
        _add(findings, "execution_table", "[execution] must be a table")
        return
    unknown = sorted(set(execution) - _EXECUTION_FIELDS)
    if unknown:
        _add(findings, "execution_unknown", f"unknown [execution] fields: {unknown}")

    effect = execution.get("effect", "unknown")
    if effect not in _EXECUTION_EFFECTS:
        _add(
            findings, "execution_effect",
            f"execution.effect must be one of {sorted(_EXECUTION_EFFECTS)}",
        )
    resource_class = execution.get("resource_class", "default")
    if resource_class not in _EXECUTION_RESOURCE_CLASSES:
        _add(
            findings, "execution_resource_class",
            "execution.resource_class must be one of "
            f"{sorted(_EXECUTION_RESOURCE_CLASSES)}",
        )
    concurrency_key = execution.get("concurrency_key", "none")
    if concurrency_key not in _EXECUTION_KEYS:
        _add(
            findings, "execution_concurrency_key",
            f"execution.concurrency_key must be one of {sorted(_EXECUTION_KEYS)}",
        )
    parallelism_class = execution.get("parallelism_class", 0)
    if (not isinstance(parallelism_class, int)
            or isinstance(parallelism_class, bool)
            or not 0 <= parallelism_class <= 3):
        _add(
            findings, "execution_parallelism_class",
            "execution.parallelism_class must be an integer from 0 to 3",
        )
    gate = execution.get("equivalence_gate", "unverified")
    if gate not in _EQUIVALENCE_GATES:
        _add(
            findings, "execution_equivalence_gate",
            f"execution.equivalence_gate must be one of {sorted(_EQUIVALENCE_GATES)}",
        )

    if (isinstance(parallelism_class, int)
            and not isinstance(parallelism_class, bool)
            and parallelism_class > 0):
        if effect in {"unknown", "interactive"}:
            _add(
                findings, "execution_parallel_effect",
                "parallelism_class > 0 requires a non-interactive known effect",
            )
        if effect not in {"unknown", "interactive", "read_only"} \
                and concurrency_key == "none":
            _add(
                findings, "execution_mutation_key",
                "parallel mutation requires a non-'none' concurrency_key",
            )
        if not active:
            _add(
                findings, "execution_candidate_parallel",
                "candidate executors cannot claim parallel execution",
            )
        if gate != "verified":
            _add(
                findings, "execution_equivalence_required",
                "parallelism_class > 0 requires equivalence_gate = 'verified'",
            )
        equivalence_tests = [
            test for test in (manifest.get("tests") or [])
            if isinstance(test, dict)
            and isinstance(test.get("equivalence_runs"), int)
            and not isinstance(test.get("equivalence_runs"), bool)
            and 2 <= test["equivalence_runs"] <= 8
        ]
        if active and not equivalence_tests:
            _add(
                findings, "execution_equivalence_test",
                "parallelism_class > 0 requires a test with equivalence_runs = 2..8",
            )


def validate_manifest(manifest: dict, *, require_declaration: bool = True,
                      active: bool = True) -> list[StandardFinding]:
    """Return deterministic conformance findings for one parsed manifest.

    ``active=False`` is the candidate profile: translation, final digest,
    output and tests may still be incomplete while the draft is quarantined.
    """
    findings: list[StandardFinding] = []
    declaration = manifest.get("executor_standard")
    if declaration is None:
        if require_declaration:
            _add(findings, "standard_missing", f"executor_standard must be {STANDARD_ID!r}")
    elif declaration != STANDARD_ID:
        _add(findings, "standard_unknown", f"unsupported executor_standard {declaration!r}")

    if manifest.get("manifest_format") != SUPPORTED_MANIFEST_FORMAT:
        _add(
            findings, "manifest_format",
            f"manifest_format must be {SUPPORTED_MANIFEST_FORMAT!r}",
        )

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        _add(findings, "name", "name is required")
    else:
        try:
            from naming_grammar import validate_name
            result = validate_name(name)
            if not result.ok:
                _add(findings, "canonical_name", result.reason or "invalid canonical name")
        except ImportError:
            _add(findings, "naming_authority", "runtime naming authority is unavailable")

    version = manifest.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
        _add(findings, "version", "version must be semantic major.minor.patch")

    _validate_description(findings, manifest, active=active)
    _validate_args(findings, manifest, active=active)
    _validate_execution(findings, manifest, active=active)

    code = manifest.get("code")
    if not isinstance(code, dict) or not code.get("files"):
        _add(findings, "code_files", "[code].files must contain at least one file")
    if active:
        digest = code.get("digest") if isinstance(code, dict) else None
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            _add(findings, "code_digest", "active executor requires a final sha256 digest")

        output = manifest.get("output")
        schema = output.get("schema_inline") if isinstance(output, dict) else None
        if not isinstance(schema, str) or not schema.strip():
            _add(findings, "output_schema", "[output].schema_inline is required")
        elif not re.search(r"\bok\s*[:=]", schema):
            _add(findings, "output_ok", "output schema must declare ok")

        tests = manifest.get("tests")
        if not isinstance(tests, list) or not tests:
            _add(findings, "tests", "at least one [[tests]] case is required")
        else:
            for index, test in enumerate(tests):
                if not isinstance(test, dict):
                    _add(
                        findings, "test_shape",
                        f"tests[{index}] must be an object",
                    )
                    continue
                name = test.get("name")
                if not isinstance(name, str) or not name.strip():
                    _add(
                        findings, "test_name",
                        f"tests[{index}].name must be a non-empty string",
                    )
                has_reference = (
                    isinstance(test.get("reference"), str)
                    and bool(test["reference"].strip())
                )
                has_case = "input" in test and "expect" in test
                if has_reference and has_case:
                    _add(
                        findings, "test_mode",
                        f"tests[{index}] must use reference or input/expect, not both",
                    )
                elif not has_reference and not has_case:
                    _add(
                        findings, "test_evidence",
                        f"tests[{index}] requires a pytest reference or input/expect",
                    )

        _validate_authority(findings, manifest)

    return findings


def validate_for_lifecycle(manifest: dict, *,
                           require_declaration: bool = True) -> list[StandardFinding]:
    """Validate a manifest using the canonical profile for its lifecycle."""
    return validate_manifest(
        manifest,
        require_declaration=require_declaration,
        active=uses_active_profile(manifest.get("lifecycle", "active")),
    )


def validate_file(path: Path, *, require_declaration: bool = True,
                  active: bool = True) -> list[StandardFinding]:
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    return validate_manifest(
        manifest, require_declaration=require_declaration, active=active,
    )


def _manifest_paths(values: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*/manifest.toml")))
        else:
            paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Metnos executor manifests")
    parser.add_argument("paths", nargs="+", help="manifest file or executor root")
    parser.add_argument(
        "--report-legacy", action="store_true",
        help="do not treat a missing standard declaration as an error",
    )
    parser.add_argument(
        "--candidate", action="store_true",
        help="validate a quarantined draft instead of an active executor",
    )
    args = parser.parse_args(argv)

    errors = 0
    for path in _manifest_paths(args.paths):
        findings = validate_file(
            path,
            require_declaration=not args.report_legacy,
            active=not args.candidate,
        )
        if findings:
            errors += len(findings)
            print(f"{path}:")
            for finding in findings:
                print(f"  [{finding.code}] {finding.message}")
    print(f"executor_standard: {errors} finding(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
