#!/usr/bin/env python3
"""Policy-closed offline core for the V26.5.2 compact candidate.

The core keeps the original request untouched, creates Unicode segments,
builds the constrained model request, and validates a returned compact graph.
It contains no transport and cannot call a model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import sys
import types
import unicodedata
from typing import Any


VERSION = "metnos.v26.5.2-candidate-core/0.1"
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
MANIFEST_PATH = HERE / "metnos_v2652_durable_manifest.json"
SELF_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2652/"
    "metnos_v2652_candidate_core.py"
)
ADAPTER_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v265/"
    "metnos_v265_compact_adapter.py"
)
SCHEMA_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v265/"
    "metnos_v265_compact.schema.json"
)
PROMPT_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v265/"
    "metnos_v265_compact.prompt.txt"
)
VALIDATOR_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_self_contained_validator.py"
)
FIXTURE_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_compact_mutation_fixture.json"
)
REGISTRY_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2641/"
    "metnos_v2641_typed_registry.json"
)
AUDITOR_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_contamination_auditor.py"
)
VERIFIER_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2652/"
    "metnos_v2652_preimport_verifier.py"
)
EXPECTED_DISTRIBUTIONS = {"jsonschema": "4.10.3", "regex": "2026.3.32"}
EXPECTED_UNICODE_VERSION = "15.0.0"
ALLOWED_ROLES = {
    "runtime_input", "evidence", "historical_source_only",
    "audit_dataset", "audit_tool", "audit_source",
}
IMPORTABLE_ROLES = {"runtime_input", "audit_tool"}
REQUIRED_POLICIES = {
    SELF_RELATIVE: ("runtime_input", True),
    ADAPTER_RELATIVE: ("runtime_input", True),
    VALIDATOR_RELATIVE: ("runtime_input", True),
    REGISTRY_RELATIVE: ("runtime_input", False),
    SCHEMA_RELATIVE: ("runtime_input", False),
    PROMPT_RELATIVE: ("runtime_input", False),
    FIXTURE_RELATIVE: ("runtime_input", False),
    AUDITOR_RELATIVE: ("audit_tool", True),
    VERIFIER_RELATIVE: ("audit_tool", True),
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def _inside_repository(path: Path) -> bool:
    try:
        path.relative_to(REPOSITORY)
        return True
    except ValueError:
        return False


def _manifest_entries(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate manifest structure and consumer policy without executing code."""
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeError("durable manifest has no artifact array")
    entries: dict[str, dict[str, Any]] = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise RuntimeError("durable manifest contains a non-object artifact")
        if set(item) != {"path", "sha256", "role", "import_allowed"}:
            raise RuntimeError("durable manifest artifact fields are not closed")
        raw = item.get("path")
        digest = item.get("sha256")
        role = item.get("role")
        import_allowed = item.get("import_allowed")
        if (
            not isinstance(raw, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise RuntimeError("durable manifest artifact identity is incomplete")
        if role not in ALLOWED_ROLES:
            raise RuntimeError("durable manifest artifact role is unsupported")
        if type(import_allowed) is not bool:
            raise RuntimeError("import_allowed must be an exact boolean")
        if import_allowed and role not in IMPORTABLE_ROLES:
            raise RuntimeError("non-importable role cannot permit execution")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts or raw in entries:
            raise RuntimeError("durable manifest path is unsafe or duplicated")
        entries[raw] = item
    authorization = document.get("authorization")
    if (
        not isinstance(authorization, dict)
        or set(authorization) != {"freeze", "gate", "network", "inference"}
        or any(value is not False for value in authorization.values())
    ):
        raise RuntimeError("author authorization must remain closed")
    for relative, expected in REQUIRED_POLICIES.items():
        item = entries.get(relative)
        actual = None if item is None else (item["role"], item["import_allowed"])
        if actual != expected:
            raise RuntimeError(f"consumer policy mismatch: {relative}")
    return entries


def verified_manifest() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Verify policy and every artifact before candidate modules execute."""
    document = json.loads(MANIFEST_PATH.read_text())
    entries = _manifest_entries(document)
    for raw, item in entries.items():
        path = (REPOSITORY / raw).resolve()
        if not path.is_file() or not _inside_repository(path):
            raise RuntimeError(f"durable manifest artifact is unavailable: {raw}")
        if sha_path(path) != item["sha256"]:
            raise RuntimeError(f"durable manifest artifact changed: {raw}")
    return document, entries


def _artifact_bytes(
    entries: dict[str, dict[str, Any]], relative: str, *,
    expected_role: str, expected_import_allowed: bool,
) -> bytes:
    item = entries.get(relative)
    if item is None:
        raise RuntimeError(f"artifact is outside the verified manifest: {relative}")
    if (
        item["role"] != expected_role
        or item["import_allowed"] is not expected_import_allowed
    ):
        raise RuntimeError(f"artifact consumer policy changed: {relative}")
    value = (REPOSITORY / relative).read_bytes()
    if sha_bytes(value) != item["sha256"]:
        raise RuntimeError(f"artifact changed after manifest verification: {relative}")
    return value


def _source_module(
    entries: dict[str, dict[str, Any]], relative: str, name: str, *,
    expected_role: str,
) -> Any:
    source = _artifact_bytes(
        entries, relative,
        expected_role=expected_role, expected_import_allowed=True,
    )
    module = types.ModuleType(name)
    module.__file__ = str((REPOSITORY / relative).resolve())
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _verified_environment() -> dict[str, str]:
    versions = {
        name: importlib.metadata.version(name)
        for name in EXPECTED_DISTRIBUTIONS
    }
    if versions != EXPECTED_DISTRIBUTIONS:
        raise RuntimeError(f"candidate dependency version drift: {versions}")
    if unicodedata.unidata_version != EXPECTED_UNICODE_VERSION:
        raise RuntimeError("Unicode database version drift")
    return {**versions, "unicode": unicodedata.unidata_version}


class CandidateCore:
    """Verified compact request builder and fail-closed response validator."""

    def __init__(
        self, *, manifest: dict[str, Any], entries: dict[str, dict[str, Any]],
        adapter: Any, validator: Any, schema: dict[str, Any], prompt: str,
        schema_validator: Any, regex_module: Any, environment: dict[str, str],
    ) -> None:
        self.manifest = manifest
        self.entries = entries
        self.adapter = adapter
        self.validator = validator
        self.schema = schema
        self.prompt = prompt
        self.schema_validator = schema_validator
        self.regex = regex_module
        self.environment = environment

    @classmethod
    def load(cls) -> "CandidateCore":
        manifest, entries = verified_manifest()
        environment = _verified_environment()
        _artifact_bytes(
            entries, REGISTRY_RELATIVE,
            expected_role="runtime_input", expected_import_allowed=False,
        )
        adapter = _source_module(
            entries, ADAPTER_RELATIVE, "metnos_v2652_core_adapter",
            expected_role="runtime_input",
        )
        validator = _source_module(
            entries, VALIDATOR_RELATIVE, "metnos_v2652_core_validator",
            expected_role="runtime_input",
        )
        schema = json.loads(_artifact_bytes(
            entries, SCHEMA_RELATIVE,
            expected_role="runtime_input", expected_import_allowed=False,
        ))
        prompt = _artifact_bytes(
            entries, PROMPT_RELATIVE,
            expected_role="runtime_input", expected_import_allowed=False,
        ).decode("utf-8")
        jsonschema = importlib.import_module("jsonschema")
        regex_module = importlib.import_module("regex")
        jsonschema.Draft202012Validator.check_schema(schema)
        return cls(
            manifest=manifest,
            entries=entries,
            adapter=adapter,
            validator=validator,
            schema=schema,
            prompt=prompt,
            schema_validator=jsonschema.Draft202012Validator(schema),
            regex_module=regex_module,
            environment=environment,
        )

    def segments(self, text: str) -> list[dict[str, Any]]:
        """Unicode UAX #29 default word boundaries without language tables."""
        parts = self.regex.split(
            r"\b", text, flags=self.regex.WORD | self.regex.VERSION1,
        )
        result: list[dict[str, Any]] = []
        cursor = 0
        for part in parts:
            if not part:
                continue
            start = text.find(part, cursor)
            if start < 0:
                raise ValueError("segmentation lost source alignment")
            end = start + len(part)
            cursor = end
            if part.isspace():
                continue
            result.append({
                "id": len(result) + 1,
                "text": part,
                "start_char": start,
                "end_char": end,
            })
        return result

    def request_body(self, original_request: str, seed: int = 92) -> dict[str, Any]:
        payload = {
            "original_request": original_request,
            "segments": self.segments(original_request),
        }
        return {
            "model": "local",
            "temperature": 0,
            "seed": seed,
            "max_tokens": 2200,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "phase1_typed_relational_v2652_compact",
                    "schema": self.schema,
                    "strict": True,
                },
            },
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }

    def evaluate_segments(
        self, compact_frame: dict[str, Any], segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate a compact frame against already-grounded source segments."""
        schema_errors = list(self.schema_validator.iter_errors(compact_frame))
        if schema_errors:
            return {
                "status": "evaluated_invalid",
                "stage": "schema",
                "codes": ["schema"],
            }
        try:
            expanded = self.adapter.expand_frame(compact_frame)
        except self.adapter.UnsafeCompactGraph as error:
            return {
                "status": "evaluated_invalid",
                "stage": "adapter",
                "codes": [str(error)],
            }
        validation = self.validator.validate_frame(expanded, segments)
        if not validation["valid"]:
            return {
                "status": "evaluated_invalid",
                "stage": "validator",
                "codes": sorted({item["code"] for item in validation["errors"]}),
            }
        return {
            "status": "evaluated_valid",
            "stage": "accepted",
            "codes": [],
            "expanded_frame": expanded,
        }

    def evaluate(self, original_request: str, compact_frame: dict[str, Any]) -> dict[str, Any]:
        """Validate a compact frame while preserving the original request."""
        return self.evaluate_segments(compact_frame, self.segments(original_request))


def self_test() -> dict[str, Any]:
    network_events: list[str] = []

    def audit(event: str, _args: tuple[Any, ...]) -> None:
        if event.startswith(("socket.", "http.client", "urllib.")):
            network_events.append(event)

    sys.addaudithook(audit)
    core = CandidateCore.load()
    verifier = _source_module(
        core.entries, VERIFIER_RELATIVE, "metnos_v2652_core_verifier",
        expected_role="audit_tool",
    )
    fixture = json.loads(_artifact_bytes(
        core.entries, FIXTURE_RELATIVE,
        expected_role="runtime_input", expected_import_allowed=False,
    ))
    fixture_segments = [
        {"id": index}
        for index in range(1, fixture["segment_count"] + 1)
    ]
    checks: list[dict[str, Any]] = []

    def record(test_id: str, passed: bool, detail: Any = None) -> None:
        checks.append({"id": test_id, "pass": bool(passed), "detail": detail})

    policy_mutations = [
        ("adapter_false", ADAPTER_RELATIVE, "import_allowed", False),
        ("adapter_missing", ADAPTER_RELATIVE, "import_allowed", None),
        ("adapter_string", ADAPTER_RELATIVE, "import_allowed", "true"),
        ("adapter_role_swap", ADAPTER_RELATIVE, "role", "historical_source_only"),
        ("validator_false", VALIDATOR_RELATIVE, "import_allowed", False),
        ("validator_missing", VALIDATOR_RELATIVE, "import_allowed", None),
        ("validator_string", VALIDATOR_RELATIVE, "import_allowed", "true"),
        ("validator_role_swap", VALIDATOR_RELATIVE, "role", "audit_tool"),
        ("registry_executable", REGISTRY_RELATIVE, "import_allowed", True),
        ("schema_executable", SCHEMA_RELATIVE, "import_allowed", True),
        ("prompt_role_swap", PROMPT_RELATIVE, "role", "evidence"),
        ("core_not_importable", SELF_RELATIVE, "import_allowed", False),
        ("unknown_role", FIXTURE_RELATIVE, "role", "unknown"),
        ("evidence_executable", next(
            item["path"] for item in core.manifest["artifacts"]
            if item["role"] == "evidence"
        ), "import_allowed", True),
    ]
    for test_id, relative, field, value in policy_mutations:
        document = json.loads(json.dumps(core.manifest))
        item = next(entry for entry in document["artifacts"] if entry["path"] == relative)
        if value is None:
            item.pop(field)
        else:
            item[field] = value
        try:
            _manifest_entries(document)
        except RuntimeError as error:
            core_rejected = True
            core_detail = str(error)
        else:
            core_rejected = False
            core_detail = "mutation accepted"
        verifier_result = verifier.verify_document(document, check_files=False)
        verifier_rejected = verifier_result["summary"]["failed"] > 0
        record(
            f"policy:{test_id}", core_rejected and verifier_rejected,
            {
                "core": core_detail,
                "verifier_failed": verifier_result["summary"]["failed"],
            },
        )

    accessor_mutations = [
        ("adapter_false", ADAPTER_RELATIVE, "runtime_input", False),
        ("adapter_role_swap", ADAPTER_RELATIVE, "historical_source_only", False),
        ("adapter_string", ADAPTER_RELATIVE, "runtime_input", "true"),
        ("validator_false", VALIDATOR_RELATIVE, "runtime_input", False),
    ]
    for test_id, relative, role, import_allowed in accessor_mutations:
        entries = json.loads(json.dumps(core.entries))
        entries[relative]["role"] = role
        entries[relative]["import_allowed"] = import_allowed
        try:
            _source_module(
                entries, relative, f"metnos_v2652_forbidden_{test_id}",
                expected_role="runtime_input",
            )
        except RuntimeError as error:
            record(f"accessor:{test_id}", True, str(error))
        else:
            record(f"accessor:{test_id}", False, "forbidden source executed")

    for item in fixture["positive_controls"]:
        outcome = core.evaluate_segments(item["compact_frame"], fixture_segments)
        record(f"positive:{item['id']}", outcome["stage"] == "accepted", outcome["stage"])
    for family in ("native_negative_cases", "historical_cases"):
        for item in fixture[family]:
            outcome = core.evaluate_segments(item["compact_frame"], fixture_segments)
            actual = {"stage": outcome["stage"], "codes": outcome["codes"]}
            record(f"{family}:{item['id']}", actual == item["expected"], actual)

    body = core.request_body("opaque-native-probe")
    user_payload = json.loads(body["messages"][1]["content"])
    segments = user_payload["segments"]
    record("request_keeps_original", user_payload["original_request"] == "opaque-native-probe")
    record(
        "segments_align",
        all(
            user_payload["original_request"][item["start_char"]:item["end_char"]]
            == item["text"]
            for item in segments
        ),
    )
    record(
        "response_schema_is_verified_artifact",
        body["response_format"]["json_schema"]["schema"] == core.schema,
    )
    record("prompt_is_verified_artifact", body["messages"][0]["content"] == core.prompt)
    record("environment_pinned", core.environment == {
        "jsonschema": "4.10.3", "regex": "2026.3.32", "unicode": "15.0.0",
    })
    record("network_quiet", network_events == [], network_events)
    failed = [item for item in checks if not item["pass"]]
    return {
        "version": VERSION,
        "network_calls": 0,
        "model_calls": 0,
        "candidate_outputs_read": 0,
        "manifest_version": core.manifest["version"],
        "environment": core.environment,
        "summary": {
            "tests": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "positive_controls": len(fixture["positive_controls"]),
            "native_negative_cases": len(fixture["native_negative_cases"]),
            "historical_cases": len(fixture["historical_cases"]),
            "policy_mutations": len(policy_mutations),
            "accessor_mutations": len(accessor_mutations),
        },
        "failed": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    args = parser.parse_args()
    if not args.self_test:
        return 2
    result = self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
