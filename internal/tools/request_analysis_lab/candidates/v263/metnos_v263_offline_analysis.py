#!/usr/bin/env python3
"""Redacted offline V25.3/V26.2 delta and V26.3 freeze review."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import metnos_v253_phase1_relation_runner as v253
import metnos_v262_minimal_phase1_runner as v262
import metnos_v263_phase1_runner as v263


V253_RESULT = Path("/tmp/metnos_v253_phase1_relation_controls_k1.json")
V262_RESULT = Path("/tmp/metnos_v262_minimal_phase1_controls_k1.json")
V253_PROMPT = Path("/tmp/metnos_v253_phase1_relation.prompt.txt")
V262_PROMPT = Path("/tmp/metnos_v262_minimal_phase1.prompt.txt")
OUT_JSON = Path("/tmp/metnos_v263_offline_delta.json")
OUT_MD = Path("/tmp/metnos_v263_offline_review.md")
FIELDS = v263.SEMANTIC_FIELDS


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected(case: dict[str, Any]) -> dict[str, Any]:
    result = v253.expected_signature(case)
    result["interpretation"] = "supported"
    return {field: result[field] for field in FIELDS}


def first_clause(record: dict[str, Any]) -> dict[str, Any]:
    clauses = record["result"]["attempts"][0]["frame"].get("clauses", [])
    active = [clause for clause in clauses if clause.get("relation") != "none"]
    return active[0] if active else (clauses or [{}])[0]


def v262_clause(record: dict[str, Any]) -> dict[str, Any]:
    clauses = (record["result"].get("frame") or {}).get("clauses", [])
    active = [clause for clause in clauses if clause.get("relation") != "none"]
    return active[0] if active else (clauses or [{}])[0]


def v253_evidence_complete(clause: dict[str, Any]) -> bool:
    return all(clause.get(f"{claim}_evidence_kind") not in {None, "none"} for claim in ("focus", "subject", "time"))


def v253_binding(clause: dict[str, Any]) -> bool:
    signature = {
        "role": "request", "interpretation": "supported",
        "speech_act": "open_question", "relation": "spatial.located_at",
        "subject_ref": "current_actor", "grammatical_person": "first",
        "time_scope": "current",
    }
    return all(clause.get(key) == value for key, value in signature.items()) and v253_evidence_complete(clause)


def count_prompt_contract(prompt: str) -> dict[str, Any]:
    enums = {
        "role": v263.ROLES,
        "interpretation": v263.INTERPRETATIONS,
        "speech_act": v263.SPEECH_ACTS,
        "relation": v263.RELATIONS,
        "subject_ref": v263.SUBJECT_REFS,
        "grammatical_person": v263.GRAMMATICAL_PERSONS,
        "time_scope": v263.TIME_SCOPES,
        "evidence_kind": v263.EVIDENCE_KINDS,
    }
    return {
        "bytes": len(prompt.encode("utf-8")),
        "enum_mentions": {
            field: {"mentioned": sum(value in prompt for value in values), "total": len(values)}
            for field, values in enums.items()
        },
        "relation_signatures_with_arity": len(set(re.findall(
            r"(?:spatial|identity|filesystem|runtime|acl|movement|workflow|document)\.[a-z_]+\([^\n)]*\)",
            prompt,
        ))),
    }


def build() -> dict[str, Any]:
    controls = v253.controls()
    old = json.loads(V253_RESULT.read_text())["records"]
    current = json.loads(V262_RESULT.read_text())["records"]
    assert len(controls) == len(old) == len(current) == 34
    field_delta: dict[str, Counter[str]] = {field: Counter() for field in FIELDS}
    field_exact = {
        "v253_first": Counter(), "v262_native": Counter(),
    }
    distributions: dict[str, dict[str, Counter[str]]] = {
        "v253_first": {field: Counter() for field in FIELDS},
        "v262_native": {field: Counter() for field in FIELDS},
    }
    relation_confusions: dict[str, dict[str, Counter[str]]] = {
        "v253_first": defaultdict(Counter), "v262_native": defaultdict(Counter),
    }
    rows = []
    for index, case in enumerate(controls):
        old_clause = first_clause(old[index])
        current_clause = v262_clause(current[index])
        gold = expected(case)
        if current[index]["query_sha256_utf8"] != v263.sha_text(case["query"]):
            raise AssertionError(f"record order/hash mismatch at {index}")
        mismatch_old = []
        mismatch_current = []
        for field in FIELDS:
            old_ok = old_clause.get(field) == gold[field]
            current_ok = current_clause.get(field) == gold[field]
            field_exact["v253_first"][field] += old_ok
            field_exact["v262_native"][field] += current_ok
            state = (
                "stable_correct" if old_ok and current_ok else
                "regressed" if old_ok and not current_ok else
                "recovered" if not old_ok and current_ok else
                "stable_wrong"
            )
            field_delta[field][state] += 1
            distributions["v253_first"][field][str(old_clause.get(field, "<missing>"))] += 1
            distributions["v262_native"][field][str(current_clause.get(field, "<missing>"))] += 1
            if not old_ok:
                mismatch_old.append(field)
            if not current_ok:
                mismatch_current.append(field)
        relation_confusions["v253_first"][gold["relation"]][str(old_clause.get("relation", "<missing>"))] += 1
        relation_confusions["v262_native"][gold["relation"]][str(current_clause.get("relation", "<missing>"))] += 1
        rows.append({
            "opaque_case_id": v263.sha_text(case["id"]),
            "language": case["lang"],
            "expected_binding": bool(case["expect_binding"]),
            "v253_first_binding": v253_binding(old_clause),
            "v262_binding_v263_policy": v263.matches_binding(current_clause),
            "v253_first_mismatch_fields": mismatch_old,
            "v262_mismatch_fields": mismatch_current,
            "v262_coverage": current_clause.get("interpretation", "invalid"),
        })
    contracts = {
        "v253": count_prompt_contract(V253_PROMPT.read_text()),
        "v262": count_prompt_contract(V262_PROMPT.read_text()),
        "v263": count_prompt_contract(v263.PROMPT_PATH.read_text()),
    }
    contamination = json.loads(v263.CONTAMINATION_PATH.read_text())
    gate = json.loads(v263.GATE_PATH.read_text())
    result = {
        "analysis_version": "metnos.v26.3-offline-causal-delta/1.0",
        "network_calls": 0,
        "acceptance_credit": False,
        "sources": {
            "v253_result_sha256": sha(V253_RESULT),
            "v262_result_sha256": sha(V262_RESULT),
            "v253_prompt_sha256": sha(V253_PROMPT),
            "v262_prompt_sha256": sha(V262_PROMPT),
            "v263_freeze_sha256": sha(v263.FREEZE_PATH),
        },
        "controlled_comparison": {
            "same_cases": 34,
            "same_first_attempt_seed": 92,
            "same_temperature": 0,
            "same_unicode_segmenter_family": "UAX#29 regex.WORD|VERSION1",
            "differences": [
                "prompt technical-contract completeness",
                "schema redundant analysis fields",
                "evidence reference representation",
                "maximum output token ceiling",
            ],
            "causal_limit": "The prompt-regression hypothesis is strongly supported but not isolated until the frozen V26.3 native run; V25.3 also exposed redundant semantic fields.",
        },
        "field_exact": {
            version: {field: count for field, count in counts.items()}
            for version, counts in field_exact.items()
        },
        "field_delta_v253_to_v262": {
            field: dict(sorted(counts.items())) for field, counts in field_delta.items()
        },
        "tuple_exact": {
            "v253_first": sum(not row["v253_first_mismatch_fields"] for row in rows),
            "v262_native": sum(not row["v262_mismatch_fields"] for row in rows),
        },
        "binary_binding": {
            "v253_first_exact": sum(row["v253_first_binding"] == row["expected_binding"] for row in rows),
            "v262_v263_policy_exact_supported_only": sum(
                row["v262_binding_v263_policy"] == row["expected_binding"]
                for row in rows if row["v262_coverage"] == "supported"
            ),
            "v262_v263_policy_evaluable": sum(row["v262_coverage"] == "supported" for row in rows),
            "v262_not_evaluated": sum(row["v262_coverage"] != "supported" for row in rows),
        },
        "distributions": {
            version: {field: dict(sorted(counts.items())) for field, counts in fields.items()}
            for version, fields in distributions.items()
        },
        "relation_confusions": {
            version: {gold: dict(sorted(counts.items())) for gold, counts in sorted(mapping.items())}
            for version, mapping in relation_confusions.items()
        },
        "prompt_contract": contracts,
        "structural_attribution": {
            "prompt_expected_effect": [
                "restore symmetric enum boundaries and nine relation arities",
                "reduce located_at/current/open_question target attraction",
                "reduce ambiguous and unsupported caused only by implicit arguments",
            ],
            "symbolic_evidence_expected_effect": [
                "remove duplicated clause/predicate anchors",
                "remove the observed clause_semantics equality invalid",
                "reduce output tokens without changing semantic claims",
            ],
            "symbolic_evidence_cannot_explain": [
                "relation selection recovery",
                "interpretation supported recovery",
                "role or speech-act recovery",
            ],
        },
        "alternatives": {
            "A_minimal_atom_full_contract": {
                "decision": "freeze_and_native_test",
                "reason": "smallest evidence-backed delta; retains V26.2 semantic fields and restores only missing technical definitions",
            },
            "B_two_sections_one_output": {
                "decision": "reserve_if_A_has_syntax_errors",
                "shape": "clause_facts plus relation_projection in one JSON response and one model call",
                "risk": "duplicate clause identity, larger output, and cross-section inconsistency",
            },
            "C_relation_candidate_algebra": {
                "decision": "reserve_if_A_overuses_ambiguity_or_forces_one_relation",
                "shape": "zero candidates means unsupported, one resolved, multiple ambiguous",
                "risk": "larger schema/output and new candidate-set oracle required",
            },
            "D_fact_table_plus_unifier": {
                "decision": "longer_term_if_relation_choice_remains_unstable",
                "shape": "model emits typed syntactic-semantic facts; deterministic catalog solver chooses relation",
                "risk": "new type ontology, catalog metadata and adjudicated per-clause gold required",
            },
            "closed_world_policy": {
                "decision": "included_in_A",
                "rule": "unique registry analysis is supported; multiple registry analyses are ambiguous with relation none; no registry analysis is unsupported with relation none",
            },
        },
        "v263_static_gate": gate,
        "v263_contamination_summary": {
            "eligible": contamination["prompt"]["maximum_objective_acceptance_credit_eligible"],
            "surface_or_mixed_lines": contamination["prompt"]["line_inventory"]["surface_or_mixed_line_count"],
            "dataset_overlap": {
                name: {
                    "whole_query": value["whole_query_occurs_in_prompt"],
                    "ngram_ge_3": value["cases_with_maximal_overlap_n_ge_3"],
                    "ngram_ge_4": value["cases_with_maximal_overlap_n_ge_4"],
                }
                for name, value in contamination["prompt"]["dataset_overlap"].items()
            },
        },
        "native_predictions_not_acceptance_claims": {
            "valid": "34/34",
            "coverage_supported": "must recover materially from V26.2 19/34; target 34/34",
            "binding": "target 34/34 with 9/9 positives and zero leakage",
            "semantic_tuple": "report separately; V25.3 first attempt was 24/34, so 34/34 full tuple is not established by prior data",
        },
        "records_redacted": rows,
    }
    return result


def render(report: dict[str, Any]) -> str:
    exact = report["field_exact"]
    delta = report["field_delta_v253_to_v262"]
    lines = [
        "# V26.3 — causal delta and frozen-candidate review",
        "",
        "Status: offline analysis only; zero server/model calls; no acceptance credit.",
        "",
        "## Decision",
        "",
        "Freeze and test V26.3 natively. The observed failure does not justify adding source-language lists or free rewriting. It justifies restoring the complete technical relation contract that V26.2 removed while retaining the minimal semantic output.",
        "",
        "This is a justified experiment, not a predicted pass. V25.3 and V26.2 also differ in output scaffolding, so only the frozen native run can test the recovery.",
        "",
        "## Measured delta",
        "",
        "| Field | V25.3 first exact | V26.2 exact | Correct→wrong | Wrong→correct |",
        "|---|---:|---:|---:|---:|",
    ]
    for field in FIELDS:
        lines.append(
            f"| `{field}` | {exact['v253_first'][field]}/34 | {exact['v262_native'][field]}/34 | "
            f"{delta[field].get('regressed', 0)} | {delta[field].get('recovered', 0)} |"
        )
    lines.extend([
        "",
        f"Full seven-field tuple: V25.3 first attempt {report['tuple_exact']['v253_first']}/34; V26.2 {report['tuple_exact']['v262_native']}/34.",
        "",
        f"Binary current-location gate: V25.3 first attempt {report['binary_binding']['v253_first_exact']}/34. V26.2 has only {report['binary_binding']['v262_v263_policy_evaluable']}/34 honestly supported frames; unsupported and ambiguous are abstentions, not successful negatives.",
        "",
        "The largest regressions are relation and coverage state. V26.2 documented zero relation signatures with arity, while V25.3 and V26.3 document nine. The one validator-invalid V26.2 record is a separate duplicated-anchor problem.",
        "",
        "## Causal split",
        "",
        "- Prompt change: expected to recover relation, supported/ambiguous boundaries, role and speech act.",
        "- Symbolic proof references: expected only to remove duplicated local anchors, token cost and structural invalids.",
        "- Minimal schema: remains a live uncertainty because V25.3 exposed redundant focus/type fields. If V26.3 still loses relation choice, those fields were acting as model scaffolding even though they were not needed by the consumer.",
        "",
        "## Candidate contract",
        "",
        "V26.3 retains role, interpretation, speech act, relation, subject reference, grammatical person, time scope, and three typed proofs. It retains UAX #29 segmentation, one call, temperature zero, seed 92 and zero retries. Grammatical person is diagnostic output but is not duplicated in the route predicate because current_actor already carries the useful identity claim.",
        "",
        "The prompt defines every retained enum and every relation signature symmetrically. It includes no source-language trigger, synonym list or example. The closed-world rule makes ambiguity and unsupported mechanically distinct.",
        "",
        "## Static gates",
        "",
        f"- Mutation tests: {json.loads(v263.MUTATIONS_PATH.read_text())['summary']['passed']}/{json.loads(v263.MUTATIONS_PATH.read_text())['summary']['tests']};",
        f"- surface/mixed prompt lines: {report['v263_contamination_summary']['surface_or_mixed_lines']};",
        "- whole-query and contiguous >=3-token overlap: zero on 109 + 34 + 70 datasets;",
        "- inference calls before freeze: zero;",
        "- native run required; post-hoc projection receives no credit.",
        "",
        "## Alternative order",
        "",
        "1. Run A, the frozen minimal atom with complete technical contract.",
        "2. If errors are primarily clause/scope errors, try B: syntax facts and semantic projection as two sections of the same JSON response and same model call.",
        "3. If ambiguity remains self-declared or a single relation is forced, try C: relation candidate algebra whose cardinality determines coverage.",
        "4. If relation choice remains unstable across domains, move to D: typed fact table plus deterministic catalog unifier.",
        "",
        "Do not run B, C and D opportunistically on the same 34-case design set. Freeze one hypothesis at a time, preserve failed artifacts and report coverage separately from accuracy.",
        "",
        "## Frozen artifacts",
        "",
    ])
    for path in (
        v263.RUNNER_PATH, v263.PROMPT_PATH, v263.SCHEMA_PATH,
        v263.FIXTURE_PATH, v263.MUTATIONS_PATH, v263.CONTAMINATION_PATH,
        v263.FREEZE_PATH, v263.GATE_PATH,
    ):
        lines.append(f"- `{path}` — SHA-256 `{sha(path)}`")
    lines.extend([
        "",
        "Native gate: 34/34 valid and supported, 34/34 binding, 9/9 positives, zero negative leakage. Full semantic tuple is reported separately: the prior evidence establishes only 24/34 for V25.3 first attempt, not 34/34.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    report = build()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render(report))
    print(json.dumps({
        "json": str(OUT_JSON), "json_sha256": sha(OUT_JSON),
        "md": str(OUT_MD), "md_sha256": sha(OUT_MD),
        "field_exact": report["field_exact"],
        "tuple_exact": report["tuple_exact"],
        "binding": report["binary_binding"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
