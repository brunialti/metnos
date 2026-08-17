#!/usr/bin/env python3
"""Redacted offline audit for the mechanically-derived V26.5 prompt."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates")
PROMPT = ROOT / "v265/metnos_v265_compact.prompt.txt"
PARENT = ROOT / "v2641/metnos_v2641_typed_phase1.prompt.txt"
PARENT_AUDIT = ROOT / "v2641/metnos_v2641_typed_phase1_contamination_audit.json"
GENERATOR = ROOT / "v265/metnos_v265_compact_prompt_generator.py"
AUDITOR = Path("/tmp/metnos_prompt_contamination_audit.py")
EXPECTED_PARENT_SHA256 = "2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run() -> dict[str, Any]:
    if sha(PARENT) != EXPECTED_PARENT_SHA256:
        raise RuntimeError("parent prompt hash mismatch")
    auditor = load("metnos_v265_redacted_auditor", AUDITOR)
    datasets, manifests = auditor.load_datasets()
    audit = auditor.audit_prompt(PROMPT, datasets)
    parent_lines = PARENT.read_text().splitlines()
    prompt_lines = PROMPT.read_text().splitlines()
    if len(parent_lines) != len(prompt_lines):
        raise RuntimeError("prompt derivation changed line count")
    changed_lines = [
        index for index, (old, new) in enumerate(zip(parent_lines, prompt_lines), 1)
        if old != new
    ]
    changed_fingerprints = [
        {
            "line": index,
            "parent_sha256": sha_bytes(parent_lines[index - 1].encode()),
            "compact_sha256": sha_bytes(prompt_lines[index - 1].encode()),
            "compact_class": auditor.classify_line(prompt_lines[index - 1]),
        }
        for index in changed_lines
    ]
    overlap_zero = all(
        value["whole_query_occurs_in_prompt"] == 0
        and value["cases_with_maximal_overlap_n_ge_3"] == 0
        for value in audit["dataset_overlap"].values()
    )
    removed = ("atom_id", "clause_id", "output_index", "from_atom_output")
    text = PROMPT.read_text()
    checks = [
        {"id": "exact_three_line_substitution", "pass": changed_lines == [4, 6, 59]},
        {"id": "unchanged_line_count", "pass": len(parent_lines) - len(changed_lines) == 56},
        {"id": "removed_identifiers_absent", "pass": all(item not in text for item in removed)},
        {"id": "compact_edge_terms_present", "pass": text.count("source_ordinal") == 3 and text.count("from_prior_atom") == 2},
        {"id": "surface_or_mixed_lines_zero", "pass": audit["line_inventory"]["surface_or_mixed_line_count"] == 0},
        {"id": "whole_query_and_ngram_overlap_zero", "pass": overlap_zero},
        {"id": "maximum_objective_eligible", "pass": audit["maximum_objective_acceptance_credit_eligible"] is True},
    ]
    return {
        "version": "metnos.v26.5-compact-prompt-redacted-audit/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "prompt_sha256": sha(PROMPT),
        "parent_prompt_sha256": sha(PARENT),
        "parent_audit_sha256": sha(PARENT_AUDIT),
        "generator_sha256": sha(GENERATOR),
        "auditor_sha256": sha(AUDITOR),
        "datasets": manifests,
        "derivation": {
            "parent_lines": len(parent_lines),
            "changed_lines": changed_lines,
            "unchanged_lines": len(parent_lines) - len(changed_lines),
            "changed_line_fingerprints": changed_fingerprints,
            "prompt_excerpts_persisted": False,
        },
        "prompt_audit": audit,
        "summary": {
            "tests": len(checks),
            "passed": sum(item["pass"] for item in checks),
            "failed": sum(not item["pass"] for item in checks),
            "bytes": len(PROMPT.read_bytes()),
            "parent_bytes": len(PARENT.read_bytes()),
        },
        "checks": checks,
        "redaction": {
            "raw_queries_persisted": False,
            "prompt_excerpts_persisted": False,
            "matched_ngrams_persisted": False,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
