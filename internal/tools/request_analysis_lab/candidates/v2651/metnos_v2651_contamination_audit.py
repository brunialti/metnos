#!/usr/bin/env python3
"""Reproducible redacted anti-contamination audit for V26.5.1."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import types
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
PATHS = {
    "prompt": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.prompt.txt",
    "corpus": HERE / "metnos_v2651_contamination_query_corpus.json",
    "auditor": HERE / "metnos_v2651_contamination_auditor.py",
    "historical_result": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_prompt_audit_result.json",
}
EXPECTED = {
    "prompt": "893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418",
    "corpus": "237aae0006fd6926fc20e5f31aa50a4aa0bd8a2d10e7162a9ebe8424c6558149",
    "auditor": "a1e7f7babc6dcce1f4e1de0040c41b5ed212db0054a0f3e5d8f46511b12c4b1d",
    "historical_result": "8be621a34ed64a438cf2f299a5ba7f6dca9ca93f517be1d608fd6efae95b6101",
}
EXPECTED_COUNTS = {
    "full109": 109, "question-controls34": 34, "adversarial70": 70,
}
EXPECTED_SEQUENCE_SHA256 = {
    "full109": "420cace8d56babcace02fede1dcc64d92310a3e293cebe9567b5303a1d4abd20",
    "question-controls34": "81b31348e3e9f077b3e289a3e16938e2eec9bc882c3866b2f8f40c1aace18813",
    "adversarial70": "0aec548d942b321401591ba431ef4f700537c1b2af8fc91e28559e6ad7eb55aa",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_verified_auditor() -> Any:
    path = PATHS["auditor"]
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED["auditor"]:
        raise RuntimeError("auditor changed before execution")
    module = types.ModuleType("metnos_v2651_redacted_auditor")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def query_sequence_sha256(rows: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row["opaque_case_id"].encode())
        digest.update(b"\0")
        digest.update(row["query"].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(test_id: str, passed: bool, detail: Any = None) -> None:
        checks.append({"id": test_id, "pass": bool(passed), "detail": detail})

    for name, path in PATHS.items():
        actual = sha256(path) if path.is_file() else None
        record(f"preimport_hash:{name}", actual == EXPECTED[name], actual)
    if not all(item["pass"] for item in checks):
        raise RuntimeError("pre-import anti-contamination hash check failed")

    auditor = load_verified_auditor()
    corpus_document = json.loads(PATHS["corpus"].read_text())
    datasets = auditor.load_query_corpus(PATHS["corpus"])
    audit = auditor.audit_prompt(PATHS["prompt"], datasets)
    historical = json.loads(PATHS["historical_result"].read_text())["prompt_audit"]

    counts = {name: len(rows) for name, rows in datasets.items()}
    record("extended_213_counts", counts == EXPECTED_COUNTS, counts)
    sequence_evidence = {
        name: {
            "computed": query_sequence_sha256(datasets[name]),
            "declared": corpus_document["dataset_manifest"][name]["query_sequence_sha256"],
            "expected": EXPECTED_SEQUENCE_SHA256[name],
        }
        for name in EXPECTED_SEQUENCE_SHA256
    }
    record(
        "corpus_sequence_digests_pinned",
        all(
            item["computed"] == item["declared"] == item["expected"]
            for item in sequence_evidence.values()
        ),
        sequence_evidence,
    )
    record("query_only_corpus", corpus_document["contains_expected_or_gold"] is False)
    record(
        "corpus_keys_are_query_only",
        all(
            set(row) == {"opaque_case_id", "query_sha256_utf8", "query"}
            for rows in datasets.values() for row in rows
        ),
    )
    record("historical_audit_exact_replay", audit == historical)
    record(
        "surface_or_mixed_lines_zero",
        audit["line_inventory"]["surface_or_mixed_line_count"] == 0,
    )
    overlap = audit["dataset_overlap"]
    record(
        "whole_query_overlap_zero_all_213",
        all(value["whole_query_occurs_in_prompt"] == 0 for value in overlap.values()),
    )
    record(
        "ngram_overlap_ge_3_zero_all_213",
        all(value["cases_with_maximal_overlap_n_ge_3"] == 0 for value in overlap.values()),
    )
    record(
        "maximum_objective_static_eligibility",
        audit["maximum_objective_acceptance_credit_eligible"] is True,
    )
    record(
        "redacted_matches_empty",
        all(value["redacted_matches"] == [] for value in overlap.values()),
    )

    provisional = {
        "version": "metnos.v26.5.1-contamination-audit/0.2",
        "network_calls": 0,
        "model_calls": 0,
        "candidate_outputs_read": 0,
        "dynamic_imports_after_hash_verification": 1,
        "prompt_sha256": sha256(PATHS["prompt"]),
        "corpus_sha256": sha256(PATHS["corpus"]),
        "auditor_sha256": sha256(PATHS["auditor"]),
        "dataset_manifest": corpus_document["dataset_manifest"],
        "prompt_audit": audit,
    }
    serialized = json.dumps(provisional, ensure_ascii=False, sort_keys=True)
    leaked_queries = [
        row["opaque_case_id"]
        for rows in datasets.values() for row in rows
        if row["query"] in serialized
    ]
    record("report_contains_no_raw_query", leaked_queries == [], leaked_queries)

    failed = [item for item in checks if not item["pass"]]
    return {
        **provisional,
        "summary": {
            "tests": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "datasets": counts,
            "total_queries": sum(counts.values()),
            "surface_or_mixed_lines": audit["line_inventory"]["surface_or_mixed_line_count"],
            "whole_query_overlaps": sum(value["whole_query_occurs_in_prompt"] for value in overlap.values()),
            "ngram_overlaps_ge_3": sum(value["cases_with_maximal_overlap_n_ge_3"] for value in overlap.values()),
        },
        "redaction": {
            "raw_queries_persisted_in_report": False,
            "prompt_excerpts_persisted": False,
            "matched_ngrams_persisted": False,
        },
        "failed": failed,
        "checks": checks,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
