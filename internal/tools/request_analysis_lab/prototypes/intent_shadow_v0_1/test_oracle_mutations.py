#!/usr/bin/env python3
"""Mutation tests for the fail-closed intent-shadow oracle verifier."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

import verify_oracle as verifier


HERE = Path(__file__).resolve().parent
ORACLE_PATH = HERE / "intent_shadow_oracle_v0_1.json"
FREEZE_PATH = HERE / "intent_shadow_oracle_v0_1.freeze.json"
EXPECTED_ORACLE_FILE_SHA256 = (
    "3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123"
)
EXPECTED_ORACLE_PAYLOAD_SHA256 = (
    "2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f"
)
SOURCE_LIST_SPECS = (
    {
        "name": "reviewer_a_cases",
        "path": verifier.A_ORACLE_REL,
        "list_key": "cases",
        "identity_key": "sample_index",
        "error_code": "SOURCE_CASE_LIST",
    },
    {
        "name": "reviewer_b_cases",
        "path": verifier.B_ORACLE_REL,
        "list_key": "cases",
        "identity_key": "index",
        "error_code": "SOURCE_CASE_LIST",
    },
    {
        "name": "adjudication_cases",
        "path": verifier.ADJUDICATION_REL,
        "list_key": "cases",
        "identity_key": "sample_index",
        "error_code": "SOURCE_CASE_LIST",
    },
    {
        "name": "proposal_a_controls",
        "path": verifier.A_CONTROLS_REL,
        "list_key": "controls",
        "identity_key": "control_id",
        "error_code": "SOURCE_CONTROL_LIST",
    },
    {
        "name": "proposal_b_controls",
        "path": verifier.B_CONTROLS_REL,
        "list_key": "controls",
        "identity_key": "control_id",
        "error_code": "SOURCE_CONTROL_LIST",
    },
    {
        "name": "baseline_cases",
        "path": verifier.BASE_CONTROLS_REL,
        "list_key": "cases",
        "identity_key": "id",
        "error_code": "BASE_CONTROL_LIST",
    },
)


def read_json(path: Path) -> Any:
    return verifier.read_json(path)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n",
        encoding="utf-8",
    )


def rendered_json(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def legacy_permissive_canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def reseal_semantic_mutation(
    oracle: dict[str, Any], freeze: dict[str, Any]
) -> None:
    oracle["integrity"]["oracle_payload_sha256"] = verifier.oracle_payload_sha256(
        oracle
    )
    freeze["oracle_payload_sha256"] = oracle["integrity"][
        "oracle_payload_sha256"
    ]
    freeze["files"][verifier.ORACLE_REL] = sha256(rendered_json(oracle)).hexdigest()
    freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)


def mutate_missing_case(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"].pop()


def mutate_duplicate_case(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"][1] = deepcopy(oracle["cases"][0])


def mutate_query_text(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"][0]["query_text"] += " alterata"


def mutate_query_hash(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"][0]["query_sha256"] = "0" * 64


def mutate_root(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"][0]["expected"]["kind"] = "unknown_root"


def mutate_route(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    oracle["cases"][0]["expected"]["body"][0]["route"] = "find/not_registered"


def mutate_reason(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    case = next(
        item for item in oracle["cases"] if item["expected"]["kind"] == "unrepresentable"
    )
    case["expected"]["reason"] = "not_registered"


def mutate_data_edge(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    target = next(
        node
        for case in oracle["cases"]
        if case["expected"]["kind"] == "operation_graph"
        for node in case["expected"]["body"]
        if node.get("kind") == "operation" and "data_from" in node
    )
    target["data_from"][0]["from"] = 999


def mutate_duplicate_control(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    duplicate = deepcopy(oracle["new_controls"][0])
    duplicate["ordinal"] = oracle["new_controls"][1]["ordinal"]
    duplicate["control_id"] = oracle["new_controls"][1]["control_id"]
    oracle["new_controls"][1] = duplicate


def mutate_freeze_file(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    freeze["files"][verifier.ORACLE_REL] = "0" * 64


def mutate_freeze_lock(oracle: dict[str, Any], freeze: dict[str, Any]) -> None:
    freeze["lock_payload_sha256"] = "0" * 64


MUTATIONS: tuple[
    tuple[str, Callable[[dict[str, Any], dict[str, Any]], None], bool], ...
] = (
    ("missing_case", mutate_missing_case, True),
    ("duplicate_case", mutate_duplicate_case, True),
    ("changed_query_text", mutate_query_text, True),
    ("changed_query_hash", mutate_query_hash, True),
    ("invalid_root", mutate_root, True),
    ("invalid_route", mutate_route, True),
    ("invalid_reason", mutate_reason, True),
    ("invalid_data_edge", mutate_data_edge, True),
    ("duplicate_control", mutate_duplicate_control, True),
    ("altered_freeze_file_hash", mutate_freeze_file, False),
    ("altered_freeze_lock_hash", mutate_freeze_lock, False),
)

DUPLICATE_JSON_MUTATIONS = (
    "duplicate_json_key_oracle",
    "duplicate_json_key_freeze",
)

NONFINITE_JSON_MUTATIONS = (
    "nonfinite_nan_oracle",
    "nonfinite_overflow_oracle",
    "nonfinite_nan_freeze",
    "nonfinite_overflow_freeze",
)


def metadata_schema_mutations() -> list[dict[str, Any]]:
    mutations: list[dict[str, Any]] = []

    def replace(
        name: str,
        path: tuple[str | int, ...],
        value: Any,
        code: str,
    ) -> None:
        mutations.append(
            {"name": name, "operation": "replace", "path": path, "value": value, "code": code}
        )

    def remove(name: str, path: tuple[str | int, ...], code: str) -> None:
        mutations.append(
            {"name": name, "operation": "remove", "path": path, "code": code}
        )

    def extra(name: str, path: tuple[str | int, ...], code: str) -> None:
        mutations.append(
            {
                "name": name,
                "operation": "extra",
                "path": path,
                "value": {"nested": [1, True, None]},
                "code": code,
            }
        )

    for field, value in (
        ("human_review", 0),
        ("blind_phase_completed", 1),
        ("independent_reviews", 1),
        ("reviewer_count", 2.0),
        ("exact_agreement_case_count", 102.0),
        ("adjudicated_case_count", 18.0),
    ):
        replace(
            f"schema_type_review_{field}",
            ("review", field),
            value,
            "ORACLE_REVIEW_META",
        )
    for field in ("query_count", "unique_query_count"):
        replace(
            f"schema_type_binding_{field}",
            ("binding", field),
            120.0,
            "ORACLE_BINDING_META",
        )
    count_values = {
        "sample_case_count": 120,
        "unique_sample_indices": 120,
        "unique_sample_queries": 120,
        "unique_sample_query_hashes": 120,
        "reviewer_exact_agreement_count": 102,
        "adjudicated_count": 18,
        "operation_graph_count": 84,
        "system_control_count": 2,
        "unrepresentable_count": 34,
        "existing_control_count": 34,
        "new_control_count": 4,
        "authorized_control_total": 38,
    }
    for field, value in count_values.items():
        replace(
            f"schema_type_counts_{field}",
            ("counts", field),
            float(value),
            "ORACLE_COUNTS_META",
        )
    for field in ("reviewed_against_existing_34", "reviewed_pairwise"):
        replace(
            f"schema_type_dedup_{field}",
            ("provenance", "semantic_control_deduplication", field),
            1,
            "ORACLE_PROVENANCE",
        )
    replace(
        "schema_type_catalog_executor_count",
        ("decisions", "catalog_scope", "catalog_snapshot_executor_count"),
        96.0,
        "ORACLE_DECISIONS",
    )
    for control_index, ordinal in enumerate((35, 36, 37, 38)):
        replace(
            f"schema_type_control_ordinal_{ordinal}",
            ("new_controls", control_index, "ordinal"),
            float(ordinal),
            "CONTROL_SOURCE",
        )

    replace(
        "schema_value_agreed_policy_array",
        ("provenance", "agreed_case_policy"),
        ["tampered\u0000policy"],
        "ORACLE_PROVENANCE",
    )
    replace(
        "schema_value_divergent_policy_object",
        ("provenance", "divergent_case_policy"),
        {"tampered": "policy"},
        "ORACLE_PROVENANCE",
    )
    replace(
        "schema_value_compound_rule_large_float",
        ("decisions", "compound_fail_closed", "rule"),
        1e308,
        "ORACLE_DECISIONS",
    )
    replace(
        "schema_value_case38_future_integer",
        ("decisions", "case_038", "future_change_is_not_retroactive"),
        int("1" * 301),
        "ORACLE_DECISIONS",
    )
    replace(
        "schema_value_catalog_path_array",
        ("decisions", "catalog_scope", "catalog_snapshot_path"),
        [],
        "ORACLE_DECISIONS",
    )
    replace(
        "schema_value_current_manifests_object",
        (
            "decisions",
            "catalog_scope",
            "current_manifests_are_observed_but_not_silently_substituted",
        ),
        {"tampered": True},
        "ORACLE_DECISIONS",
    )
    replace(
        "schema_value_nightly_reindex_string",
        ("decisions", "future_corpus_registry", "guides_nightly_reindexing"),
        "tampered\u0000value",
        "ORACLE_DECISIONS",
    )
    replace(
        "schema_value_integrity_convention_null",
        ("integrity", "convention"),
        None,
        "ORACLE_INTEGRITY",
    )

    removable = (
        ("compound_fail_closed", "rule"),
        ("case_038", "future_change_is_not_retroactive"),
        ("catalog_scope", "catalog_snapshot_path"),
        (
            "catalog_scope",
            "current_manifests_are_observed_but_not_silently_substituted",
        ),
        ("future_corpus_registry", "guides_nightly_reindexing"),
    )
    for parent, field in removable:
        remove(
            f"schema_missing_{parent}_{field}",
            ("decisions", parent, field),
            "ORACLE_DECISIONS",
        )
    for decision_object in (
        "compound_fail_closed",
        "case_038",
        "case_084",
        "case_113",
        "catalog_scope",
        "future_corpus_registry",
        "corpus_unavailability",
    ):
        extra(
            f"schema_extra_{decision_object}",
            ("decisions", decision_object),
            "ORACLE_DECISIONS",
        )
    if len(mutations) != 47:
        raise AssertionError(f"metadata mutation matrix size {len(mutations)} != 47")
    return mutations


def freeze_schema_mutations() -> tuple[tuple[str, str, Any], ...]:
    return (
        ("schema_freeze_format_boolean", "freeze_format", False),
        ("schema_freeze_created_date_array", "created_date", ["2026-08-12"]),
        ("schema_freeze_algorithm_integer", "algorithm", 256),
        (
            "schema_freeze_file_digest_boolean",
            "files",
            {
                "path": verifier.ORACLE_REL,
                "value": False,
            },
        ),
        (
            "schema_freeze_extra_top_level_key",
            "__extra__",
            {"nested": [1, True, None]},
        ),
    )


def duplicate_key_text(source: str, needle: str, duplicate: str) -> str:
    if source.count(needle) != 1:
        raise ValueError(f"duplicate-key mutation anchor count != 1: {needle!r}")
    return source.replace(needle, f"{duplicate}\n{needle}", 1)


def run_duplicate_json_mutations(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    results: dict[str, str],
    failures: list[str],
) -> None:
    oracle_text = ORACLE_PATH.read_text(encoding="utf-8")
    freeze_text = FREEZE_PATH.read_text(encoding="utf-8")
    duplicate_oracle = duplicate_key_text(
        oracle_text,
        '  "status": "canonical_frozen"',
        '  "status": "tampered_first_value",',
    )
    duplicate_freeze = duplicate_key_text(
        freeze_text,
        '  "created_date": "2026-08-12",',
        '  "created_date": "tampered_first_value",',
    )
    scenarios = (
        (
            "duplicate_json_key_oracle",
            duplicate_oracle,
            None,
            "ORACLE_READ: duplicate JSON key: 'status'",
        ),
        (
            "duplicate_json_key_freeze",
            None,
            duplicate_freeze,
            "FREEZE_READ: duplicate JSON key: 'created_date'",
        ),
    )
    for name, raw_oracle, raw_freeze, expected_error in scenarios:
        freeze = deepcopy(freeze_source)
        if raw_oracle is not None:
            freeze["files"][verifier.ORACLE_REL] = sha256(
                raw_oracle.encode("utf-8")
            ).hexdigest()
            freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)
        with TemporaryDirectory(prefix="intent-shadow-oracle-duplicate-json-") as temporary:
            temporary_path = Path(temporary)
            oracle_path = temporary_path / "oracle.json"
            freeze_path = temporary_path / "freeze.json"
            if raw_oracle is None:
                write_json(oracle_path, oracle_source)
            else:
                oracle_path.write_text(raw_oracle, encoding="utf-8")
            if raw_freeze is None:
                write_json(freeze_path, freeze)
            else:
                freeze_path.write_text(raw_freeze, encoding="utf-8")
            result = verifier.verify(oracle_path, freeze_path)
        if result["errors"] == [expected_error]:
            results[name] = "caught"
        else:
            results[name] = "missed"
            failures.append(name)


def render_nonfinite_oracle_mutation(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    lexeme: str,
) -> tuple[str, dict[str, Any]]:
    marker = "__NONFINITE_JSON_NUMBER__"
    parsed_number = float(lexeme)
    semantic = deepcopy(oracle_source)
    semantic["decisions"]["compound_fail_closed"]["rule"] = parsed_number
    semantic["integrity"].pop("oracle_payload_sha256", None)
    payload_digest = legacy_permissive_canonical_sha256(semantic)
    renderable = deepcopy(oracle_source)
    renderable["decisions"]["compound_fail_closed"]["rule"] = marker
    renderable["integrity"]["oracle_payload_sha256"] = payload_digest
    raw_oracle = rendered_json(renderable).decode("utf-8")
    encoded_marker = json.dumps(marker, ensure_ascii=False, allow_nan=False)
    if raw_oracle.count(encoded_marker) != 1:
        raise ValueError("non-finite oracle mutation marker count != 1")
    raw_oracle = raw_oracle.replace(encoded_marker, lexeme, 1)
    freeze = deepcopy(freeze_source)
    freeze["oracle_payload_sha256"] = payload_digest
    freeze["files"][verifier.ORACLE_REL] = sha256(
        raw_oracle.encode("utf-8")
    ).hexdigest()
    freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)
    return raw_oracle, freeze


def render_nonfinite_freeze_mutation(
    freeze_source: dict[str, Any], lexeme: str
) -> str:
    marker = "__NONFINITE_JSON_NUMBER__"
    semantic = deepcopy(freeze_source)
    semantic["created_date"] = float(lexeme)
    semantic.pop("lock_payload_sha256", None)
    lock_digest = legacy_permissive_canonical_sha256(semantic)
    renderable = deepcopy(freeze_source)
    renderable["created_date"] = marker
    renderable["lock_payload_sha256"] = lock_digest
    raw_freeze = rendered_json(renderable).decode("utf-8")
    encoded_marker = json.dumps(marker, ensure_ascii=False, allow_nan=False)
    if raw_freeze.count(encoded_marker) != 1:
        raise ValueError("non-finite freeze mutation marker count != 1")
    return raw_freeze.replace(encoded_marker, lexeme, 1)


def run_nonfinite_json_mutations(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    results: dict[str, str],
    failures: list[str],
) -> None:
    oracle_path_text = '$["decisions"]["compound_fail_closed"]["rule"]'
    scenarios: list[tuple[str, str | None, dict[str, Any] | None, str | None, str]] = []
    for name, lexeme in (
        ("nonfinite_nan_oracle", "NaN"),
        ("nonfinite_overflow_oracle", "1e999"),
    ):
        raw_oracle, freeze = render_nonfinite_oracle_mutation(
            oracle_source, freeze_source, lexeme
        )
        scenarios.append(
            (
                name,
                raw_oracle,
                freeze,
                None,
                f"ORACLE_READ: non-finite JSON number at {oracle_path_text}: {lexeme}",
            )
        )
    for name, lexeme in (
        ("nonfinite_nan_freeze", "NaN"),
        ("nonfinite_overflow_freeze", "1e999"),
    ):
        scenarios.append(
            (
                name,
                None,
                None,
                render_nonfinite_freeze_mutation(freeze_source, lexeme),
                f'FREEZE_READ: non-finite JSON number at $["created_date"]: {lexeme}',
            )
        )
    for name, raw_oracle, freeze, raw_freeze, expected_error in scenarios:
        with TemporaryDirectory(prefix="intent-shadow-oracle-nonfinite-") as temporary:
            temporary_path = Path(temporary)
            oracle_path = temporary_path / "oracle.json"
            freeze_path = temporary_path / "freeze.json"
            if raw_oracle is None:
                write_json(oracle_path, oracle_source)
            else:
                oracle_path.write_text(raw_oracle, encoding="utf-8")
            if raw_freeze is not None:
                freeze_path.write_text(raw_freeze, encoding="utf-8")
            elif freeze is not None:
                write_json(freeze_path, freeze)
            else:
                raise ValueError(f"missing freeze mutation for {name}")
            result = verifier.verify(oracle_path, freeze_path)
        if result["errors"] == [expected_error]:
            results[name] = "caught"
        else:
            results[name] = "missed"
            failures.append(name)


def mutate_at_path(oracle: dict[str, Any], mutation: dict[str, Any]) -> None:
    path = mutation["path"]
    parent: Any = oracle
    for component in path[:-1]:
        parent = parent[component]
    operation = mutation["operation"]
    final = path[-1]
    if operation == "replace":
        parent[final] = deepcopy(mutation["value"])
    elif operation == "remove":
        del parent[final]
    elif operation == "extra":
        target = parent[final]
        target["unexpected_cycle3_key"] = deepcopy(mutation["value"])
    else:
        raise ValueError(f"unknown metadata mutation operation: {operation}")


def run_metadata_schema_mutations(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    results: dict[str, str],
    failures: list[str],
) -> None:
    for mutation in metadata_schema_mutations():
        oracle = deepcopy(oracle_source)
        freeze = deepcopy(freeze_source)
        mutate_at_path(oracle, mutation)
        reseal_semantic_mutation(oracle, freeze)
        with TemporaryDirectory(prefix="intent-shadow-oracle-schema-") as temporary:
            temporary_path = Path(temporary)
            oracle_path = temporary_path / "oracle.json"
            freeze_path = temporary_path / "freeze.json"
            write_json(oracle_path, oracle)
            write_json(freeze_path, freeze)
            first = verifier.verify(oracle_path, freeze_path)
            second = verifier.verify(oracle_path, freeze_path)
        error_prefix = f'{mutation["code"]}:'
        deterministic = first["errors"] == second["errors"]
        caught_by_expected_code = any(
            error.startswith(error_prefix) for error in first["errors"]
        )
        if first["error_count"] > 0 and deterministic and caught_by_expected_code:
            results[mutation["name"]] = "caught"
        else:
            results[mutation["name"]] = "missed"
            failures.append(mutation["name"])


def run_freeze_schema_mutations(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    results: dict[str, str],
    failures: list[str],
) -> None:
    for name, field, value in freeze_schema_mutations():
        freeze = deepcopy(freeze_source)
        if field == "files":
            freeze["files"][value["path"]] = value["value"]
        elif field == "__extra__":
            freeze["unexpected_cycle3_key"] = deepcopy(value)
        else:
            freeze[field] = deepcopy(value)
        freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)
        with TemporaryDirectory(prefix="intent-shadow-freeze-schema-") as temporary:
            temporary_path = Path(temporary)
            oracle_path = temporary_path / "oracle.json"
            freeze_path = temporary_path / "freeze.json"
            write_json(oracle_path, oracle_source)
            write_json(freeze_path, freeze)
            first = verifier.verify(oracle_path, freeze_path)
            second = verifier.verify(oracle_path, freeze_path)
        caught = any(error.startswith("FREEZE_SCHEMA:") for error in first["errors"])
        if first["error_count"] > 0 and first["errors"] == second["errors"] and caught:
            results[name] = "caught"
        else:
            results[name] = "missed"
            failures.append(name)


def reseal_source_override(
    freeze_source: dict[str, Any],
    relative_path: str,
    source_path: Path,
) -> dict[str, Any]:
    freeze = deepcopy(freeze_source)
    freeze["files"][relative_path] = sha256(source_path.read_bytes()).hexdigest()
    freeze["lock_payload_sha256"] = verifier.freeze_payload_sha256(freeze)
    return freeze


def run_source_list_scenario(
    *,
    name: str,
    spec: dict[str, str],
    mutated_source: dict[str, Any],
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    expected_error_code: str | None,
    results: dict[str, str],
    failures: list[str],
) -> None:
    with TemporaryDirectory(prefix="intent-shadow-source-list-") as temporary:
        temporary_path = Path(temporary)
        source_path = temporary_path / "source.json"
        oracle_path = temporary_path / "oracle.json"
        freeze_path = temporary_path / "freeze.json"
        write_json(source_path, mutated_source)
        write_json(oracle_path, oracle_source)
        freeze = reseal_source_override(freeze_source, spec["path"], source_path)
        write_json(freeze_path, freeze)
        overrides = {spec["path"]: source_path}
        first = verifier.verify(oracle_path, freeze_path, overrides)
        second = verifier.verify(oracle_path, freeze_path, overrides)
    deterministic = first["errors"] == second["errors"]
    if expected_error_code is None:
        accepted = first["error_count"] == 0 and deterministic
    else:
        accepted = (
            first["error_count"] > 0
            and deterministic
            and any(
                error.startswith(f"{expected_error_code}:")
                for error in first["errors"]
            )
        )
    if accepted:
        results[name] = "accepted" if expected_error_code is None else "caught"
    else:
        results[name] = "missed"
        failures.append(name)


def run_source_list_mutations(
    oracle_source: dict[str, Any],
    freeze_source: dict[str, Any],
    results: dict[str, str],
    failures: list[str],
) -> tuple[int, int]:
    negative_count = 0
    positive_count = 0
    for spec in SOURCE_LIST_SPECS:
        source = verifier.read_json(verifier.ROOT / spec["path"])
        list_key = spec["list_key"]
        identity_key = spec["identity_key"]
        error_code = spec["error_code"]
        items = source[list_key]

        reordered = deepcopy(source)
        reordered[list_key] = list(reversed(reordered[list_key]))
        run_source_list_scenario(
            name=f"source_reorder_positive_{spec['name']}",
            spec=spec,
            mutated_source=reordered,
            oracle_source=oracle_source,
            freeze_source=freeze_source,
            expected_error_code=None,
            results=results,
            failures=failures,
        )
        positive_count += 1

        scenarios: list[tuple[str, dict[str, Any]]] = []
        duplicate_first = deepcopy(source)
        duplicate_first[list_key].insert(0, deepcopy(items[0]))
        scenarios.append(("duplicate_first", duplicate_first))

        duplicate_last = deepcopy(source)
        duplicate_last[list_key].append(deepcopy(items[0]))
        scenarios.append(("duplicate_last", duplicate_last))

        contradictory = deepcopy(source)
        contradictory_record = deepcopy(items[0])
        if "expected" in contradictory_record:
            contradictory_record["expected"] = {
                "kind": "unrepresentable",
                "reason": "outside_registry",
            }
        elif "query" in contradictory_record:
            contradictory_record["query"] += " contradicted"
        contradictory[list_key].insert(0, contradictory_record)
        scenarios.append(("duplicate_contradictory", contradictory))

        invalid_record = deepcopy(source)
        invalid_record[list_key].append({"junk": True})
        scenarios.append(("invalid_record", invalid_record))

        omission = deepcopy(source)
        omission[list_key].pop()
        scenarios.append(("omission", omission))

        substitution = deepcopy(source)
        replacement = deepcopy(items[-1])
        replacement[identity_key] = deepcopy(items[0][identity_key])
        substitution[list_key][-1] = replacement
        scenarios.append(("substitution", substitution))

        for suffix, mutated in scenarios:
            run_source_list_scenario(
                name=f"source_{suffix}_{spec['name']}",
                spec=spec,
                mutated_source=mutated,
                oracle_source=oracle_source,
                freeze_source=freeze_source,
                expected_error_code=error_code,
                results=results,
                failures=failures,
            )
            negative_count += 1

    baseline_spec = SOURCE_LIST_SPECS[-1]
    baseline = verifier.read_json(verifier.ROOT / baseline_spec["path"])
    for field in ("id", "query"):
        duplicate = deepcopy(baseline)
        duplicate["cases"][-1][field] = duplicate["cases"][0][field]
        run_source_list_scenario(
            name=f"source_baseline_duplicate_{field}",
            spec=baseline_spec,
            mutated_source=duplicate,
            oracle_source=oracle_source,
            freeze_source=freeze_source,
            expected_error_code="BASE_CONTROL_LIST",
            results=results,
            failures=failures,
        )
        negative_count += 1
    return negative_count, positive_count


def run() -> dict[str, Any]:
    oracle_source = read_json(ORACLE_PATH)
    freeze_source = read_json(FREEZE_PATH)
    baseline = verifier.verify(ORACLE_PATH, FREEZE_PATH)
    results: dict[str, str] = {}
    failures: list[str] = []
    if baseline["error_count"] != 0:
        failures.append("baseline_verification_failed")
    oracle_file_digest = sha256(ORACLE_PATH.read_bytes()).hexdigest()
    oracle_payload_digest = verifier.oracle_payload_sha256(oracle_source)
    oracle_identity = {
        "file_sha256": oracle_file_digest,
        "payload_sha256": oracle_payload_digest,
        "unchanged": (
            oracle_file_digest == EXPECTED_ORACLE_FILE_SHA256
            and oracle_payload_digest == EXPECTED_ORACLE_PAYLOAD_SHA256
        ),
    }
    if not oracle_identity["unchanged"]:
        failures.append("oracle_identity_changed")
    for name, mutation, reseal in MUTATIONS:
        oracle = deepcopy(oracle_source)
        freeze = deepcopy(freeze_source)
        mutation(oracle, freeze)
        if reseal:
            reseal_semantic_mutation(oracle, freeze)
        with TemporaryDirectory(prefix="intent-shadow-oracle-mutation-") as temporary:
            temporary_path = Path(temporary)
            oracle_path = temporary_path / "oracle.json"
            freeze_path = temporary_path / "freeze.json"
            write_json(oracle_path, oracle)
            write_json(freeze_path, freeze)
            result = verifier.verify(oracle_path, freeze_path)
        if result["error_count"] > 0:
            results[name] = "caught"
        else:
            results[name] = "missed"
            failures.append(name)
    run_duplicate_json_mutations(oracle_source, freeze_source, results, failures)
    run_nonfinite_json_mutations(oracle_source, freeze_source, results, failures)
    run_metadata_schema_mutations(oracle_source, freeze_source, results, failures)
    run_freeze_schema_mutations(oracle_source, freeze_source, results, failures)
    source_negative_count, source_positive_count = run_source_list_mutations(
        oracle_source, freeze_source, results, failures
    )
    return {
        "status": "ok" if not failures else "error",
        "error_count": len(failures),
        "errors": failures,
        "mutation_count": (
            len(MUTATIONS)
            + len(DUPLICATE_JSON_MUTATIONS)
            + len(NONFINITE_JSON_MUTATIONS)
            + len(metadata_schema_mutations())
            + len(freeze_schema_mutations())
            + source_negative_count
        ),
        "positive_control_count": source_positive_count,
        "mutation_results": results,
        "oracle_identity": oracle_identity,
    }


def main() -> int:
    result = run()
    print(
        json.dumps(
            result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    )
    return 0 if result["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
