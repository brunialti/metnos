#!/usr/bin/env python3
"""Audit whether the frozen sample has enough independent gold to build 0.1."""

from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


def find_repo_root() -> Path:
    for candidate in (HERE, *HERE.parents):
        if (candidate / "CLAUDE.md").is_file() and (
            candidate / "runtime"
        ).is_dir():
            return candidate
    raise RuntimeError("repository root not found")


ROOT = find_repo_root()
FROZEN_RUN = (
    ROOT
    / "internal/tools/request_analysis_lab/misure_11_8/"
    "prova_specchi_riparo_c11.json"
)
SHADOW_OVERLAY = (
    ROOT
    / "internal/tools/request_analysis_lab/"
    "intent_gold_adjudication_overlay_v1.1.shadow.json"
)
PROPOSED_OVERLAY = (
    ROOT
    / "internal/tools/request_analysis_lab/"
    "intent_gold_adjudication_overlay_v1.proposed.json"
)
QUESTION_CONTROLS = (
    ROOT / "internal/tools/request_analysis_lab/question_focus_controls_v1.json"
)
PHASE1_OVERLAY = (
    ROOT
    / "internal/tools/request_analysis_lab/oracles/phase1_v1/"
    "metnos_phase1_typed_oracle_v1.overlay.json"
)
BENCHMARK_SOURCES = (
    ROOT / "tests/benchmarks/intent_accuracy_bench.py",
    ROOT / "tests/benchmarks/intent_compound_bench.py",
)
GOLD_SYMBOLS = ("GOLD", "EDGE_GOLD", "COMPOUND_GOLD", "COMPOUND_XL_GOLD")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def literal_assignments(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                result[target.id] = value
    return result


def benchmark_gold() -> list[tuple[str, Any, str]]:
    result: list[tuple[str, Any, str]] = []
    for path in BENCHMARK_SOURCES:
        assignments = literal_assignments(path)
        for symbol in GOLD_SYMBOLS:
            values = assignments.get(symbol, [])
            for index, item in enumerate(values):
                if (
                    not isinstance(item, (list, tuple))
                    or len(item) != 2
                    or not isinstance(item[0], str)
                ):
                    raise ValueError(
                        f"{relative(path)}:{symbol}[{index}] is not (query, gold)"
                    )
                result.append((item[0], item[1], f"{relative(path)}:{symbol}"))
    return result


def typed_root_count(value: Any) -> int:
    result = 0
    if isinstance(value, dict):
        if value.get("root_kind") in {
            "operation_graph",
            "system_control",
            "unrepresentable",
        }:
            result += 1
        for child in value.values():
            result += typed_root_count(child)
    elif isinstance(value, list):
        for child in value:
            result += typed_root_count(child)
    return result


def overlay_hashes(document: dict[str, Any]) -> set[str]:
    result: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {
                    "query_sha256",
                    "query_sha256_utf8",
                    "case_fingerprint_sha256",
                } and isinstance(child, str) and len(child) == 64:
                    result.add(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return result


def cases_count(document: Any) -> int:
    if isinstance(document, dict) and isinstance(document.get("cases"), list):
        return len(document["cases"])
    raise ValueError("document has no root cases list")


def canonical_sample_sha256(queries: list[str]) -> str:
    payload = json.dumps(queries, ensure_ascii=False).encode("utf-8")
    return sha256(payload).hexdigest()


def audit() -> dict[str, Any]:
    frozen = read_json(FROZEN_RUN)
    queries = frozen.get("queries")
    if (
        not isinstance(queries, list)
        or len(queries) != 120
        or not all(isinstance(query, str) for query in queries)
    ):
        raise ValueError("frozen run does not contain exactly 120 string queries")
    sample_hash = canonical_sample_sha256(queries)
    if sample_hash != frozen.get("sample_sha256"):
        raise ValueError("frozen sample hash mismatch")

    current_gold = benchmark_gold()
    current_by_query: dict[str, list[tuple[Any, str]]] = {}
    for query, expected, source in current_gold:
        current_by_query.setdefault(query, []).append((expected, source))
    exact_current_matches = {
        index for index, query in enumerate(queries) if query in current_by_query
    }

    shadow = read_json(SHADOW_OVERLAY)
    proposed = read_json(PROPOSED_OVERLAY)
    frozen_hash_to_index = {
        text_sha256(query): index for index, query in enumerate(queries)
    }
    overlay_matches: set[int] = set()
    for digest in overlay_hashes(shadow) | overlay_hashes(proposed):
        if digest in frozen_hash_to_index:
            overlay_matches.add(frozen_hash_to_index[digest])

    reusable_indices = exact_current_matches | overlay_matches
    controls = read_json(QUESTION_CONTROLS)
    phase1 = read_json(PHASE1_OVERLAY)
    available_controls = cases_count(controls)
    phase1_cases = cases_count(phase1)
    requested_controls = 38
    source_documents = [
        FROZEN_RUN,
        SHADOW_OVERLAY,
        PROPOSED_OVERLAY,
        QUESTION_CONTROLS,
        PHASE1_OVERLAY,
        *BENCHMARK_SOURCES,
    ]

    result = {
        "audit_format": "metnos.intent-shadow-oracle-source-audit/0.1",
        "created_date": "2026-08-12",
        "status": "blocked_on_oracle_authority_choice",
        "sources": {
            relative(path): file_sha256(path) for path in source_documents
        },
        "frozen_sample": {
            "query_count": len(queries),
            "unique_query_count": len(set(queries)),
            "sample_sha256": sample_hash,
        },
        "current_benchmark_gold": {
            "case_count": len(current_gold),
            "exact_query_matches_with_frozen_sample": len(
                exact_current_matches
            ),
            "note": (
                "The live benchmark gold is not the frozen 120-case gold; "
                "exact text match is required before reuse."
            ),
        },
        "adjudication_overlays": {
            "frozen_sample_matches": len(overlay_matches),
            "union_with_current_exact_matches": len(reusable_indices),
            "typed_root_records": typed_root_count(shadow)
            + typed_root_count(proposed),
        },
        "coverage": {
            "base_required": 120,
            "base_with_reusable_independent_gold": len(reusable_indices),
            "base_missing_independent_gold": 120 - len(reusable_indices),
            "controls_required": requested_controls,
            "controls_available": available_controls,
            "controls_missing": requested_controls - available_controls,
            "phase1_overlay_cases": phase1_cases,
            "typed_root_records_across_all_sources": sum(
                typed_root_count(read_json(path))
                for path in (
                    SHADOW_OVERLAY,
                    PROPOSED_OVERLAY,
                    QUESTION_CONTROLS,
                    PHASE1_OVERLAY,
                )
            ),
        },
        "prohibited_shortcut": (
            "Model outputs or agreement among c10/c11/control arms must not "
            "be promoted to independent gold without Roberto's decision."
        ),
        "choice_required": {
            "strict_independent_adjudication": (
                "Create new reviewed gold for every uncovered frozen query "
                "and define four additional controls."
            ),
            "provisional_model_consensus": (
                "Use agreement among existing model arms as provisional "
                "labels; this is circular and cannot be called independent gold."
            ),
            "codex_recommendation": "strict_independent_adjudication",
        },
        "audit_errors": [],
        "audit_error_count": 0,
    }
    return result


def verify(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = audit()
    if document != expected:
        errors.append("audit_differs_from_sources")
    coverage = document.get("coverage", {})
    if coverage.get("base_required") != 120:
        errors.append("base_required")
    if coverage.get("controls_required") != 38:
        errors.append("controls_required")
    if coverage.get("base_with_reusable_independent_gold", 0) + coverage.get(
        "base_missing_independent_gold", 0
    ) != 120:
        errors.append("base_coverage_sum")
    if coverage.get("controls_available", 0) + coverage.get(
        "controls_missing", 0
    ) != 38:
        errors.append("control_coverage_sum")
    if document.get("status") != "blocked_on_oracle_authority_choice":
        errors.append("status")
    if document.get("audit_error_count") != 0 or document.get(
        "audit_errors"
    ) != []:
        errors.append("audit_error_fields")
    for rel, digest in document.get("sources", {}).items():
        path = ROOT / rel
        if not path.is_file() or file_sha256(path) != digest:
            errors.append(f"source_sha256:{rel}")
    return sorted(set(errors))


def main() -> int:
    output_path = HERE / "intent_shadow_oracle_source_audit_v0_1.json"
    if output_path.is_file():
        document = read_json(output_path)
        errors = verify(document)
        result = {
            "status": "ok" if not errors else "error",
            "error_count": len(errors),
            "errors": errors,
            "blocking_base_gold": document["coverage"][
                "base_missing_independent_gold"
            ],
            "blocking_controls": document["coverage"]["controls_missing"],
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if not errors else 1
    print(json.dumps(audit(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
