#!/usr/bin/env python3
"""Pure redacted prompt-contamination auditor for V26.5.1."""
from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


VERSION = "metnos.v26.5.1-contamination-auditor/0.1"
SOURCE_AUDITOR_SHA256 = "1cedfa7115744da79764c6b2fd31c2be67eada3fcc18d23bb46a5108eb190169"

WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)

SURFACE_MARKER_RE = re.compile(
    r"(?:\be\.g\.|\bfor example\b|\bexamples?\s*:|\besempio\b|"
    r"\bper esempio\b|"
    r"\b(?:user|utente)\s*:\s*['\"])",
    re.IGNORECASE,
)

TECHNICAL_MARKERS = (
    "schema", "predicate", "semantic", "canonical", "ontology", "route",
    "object", "carrier", "sink", "source_predicate", "token_id", "enum",
    "tagged union", "phase 1", "phase 2", "catalog", "binding", "graph",
    "resource_scope", "side_effect", "input_from", "qualifier",
)

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))

def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()

def tokens(value: str) -> list[str]:
    return WORD_RE.findall(normalize(value))

def classify_line(line: str) -> str:
    low = normalize(line)
    surface = bool(SURFACE_MARKER_RE.search(line))
    # Quoted natural-language fragments are surface evidence. Backtick-only
    # identifiers are intentionally not treated as natural-language quotes.
    quote_segments = re.findall(
        r'"([^"\n]+)"|“([^”\n]+)”|«([^»\n]+)»', line
    )
    quote_values = [next(value for value in group if value) for group in quote_segments]
    quoted_natural = any(
        len(tokens(value)) >= 2
        and not re.fullmatch(r"[A-Za-z0-9_./<>:=*+ -]+", value.strip())
        for value in quote_values
    )
    # ASCII-only natural examples are common, so code-shape alone is not a
    # sufficient discriminator. Treat multiword quotes without schema/code
    # punctuation as surface prose as well.
    quoted_natural = quoted_natural or any(
        len(tokens(value)) >= 2
        and not any(ch in value for ch in "_{}[]<>:=*")
        for value in quote_values
    )
    technical = any(marker in low for marker in TECHNICAL_MARKERS)
    # "es. identifier_name" is a technical example, not a surface trigger.
    technical_example_only = bool(
        re.search(r"\bes\.\s+[A-Za-z0-9_]+", line, re.IGNORECASE)
    ) and not quoted_natural and not SURFACE_MARKER_RE.search(
        re.sub(r"\bes\.\s+[A-Za-z0-9_]+", "", line, flags=re.IGNORECASE)
    )
    if (surface and not technical_example_only) or quoted_natural:
        return (
            "mixed_surface_and_technical"
            if technical
            else "surface_language_trigger_or_example"
        )
    if technical:
        return "technical_ontology"
    return "structural_or_common_prose"

def prompt_index(text: str, max_n: int = 8) -> tuple[list[str], list[int], dict[int, dict[tuple[str, ...], list[int]]]]:
    all_tokens: list[str] = []
    token_lines: list[int] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line_tokens = tokens(line)
        all_tokens.extend(line_tokens)
        token_lines.extend([line_no] * len(line_tokens))
    indexes: dict[int, dict[tuple[str, ...], list[int]]] = {}
    for n in range(3, max_n + 1):
        index: dict[tuple[str, ...], list[int]] = {}
        for start in range(0, len(all_tokens) - n + 1):
            gram = tuple(all_tokens[start:start+n])
            index.setdefault(gram, []).append(start)
        indexes[n] = index
    return all_tokens, token_lines, indexes

def maximal_overlaps(query_tokens: list[str], indexes: dict[int, dict[tuple[str, ...], list[int]]], max_n: int = 8) -> list[tuple[int, int, tuple[str, ...], int]]:
    candidates: list[tuple[int, int, tuple[str, ...], int]] = []
    upper = min(max_n, len(query_tokens))
    for n in range(upper, 2, -1):
        for qstart in range(0, len(query_tokens) - n + 1):
            gram = tuple(query_tokens[qstart:qstart+n])
            positions = indexes[n].get(gram)
            if positions:
                candidates.append((qstart, qstart+n, gram, positions[0]))
    selected: list[tuple[int, int, tuple[str, ...], int]] = []
    for item in sorted(candidates, key=lambda x: (-(x[1]-x[0]), x[0])):
        qstart, qend, _, _ = item
        if any(qstart >= a and qend <= b for a, b, _, _ in selected):
            continue
        selected.append(item)
    return sorted(selected)

def line_inventory(text: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    fingerprints = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        kind = classify_line(line)
        counts[kind] += 1
        if kind in {
            "surface_language_trigger_or_example",
            "mixed_surface_and_technical",
        }:
            fingerprints.append({
                "line": line_no,
                "line_sha256_nfkc_casefold": sha_text(normalize(line.strip())),
                "class": kind,
                "token_count": len(tokens(line)),
            })
    return {
        "nonempty_lines_by_class": dict(sorted(counts.items())),
        "surface_or_mixed_line_count": len(fingerprints),
        "surface_or_mixed_line_fingerprints": fingerprints,
    }

def audit_prompt(path: Path, datasets: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    text = path.read_text()
    prompt_tokens, token_lines, indexes = prompt_index(text)
    prompt_normalized = " ".join(prompt_tokens)
    per_dataset: dict[str, Any] = {}
    all_problematic = 0
    for dataset_name, cases in datasets.items():
        matches = []
        cases_with_overlap: set[str] = set()
        cases_with_long_overlap: set[str] = set()
        exact_cases: set[str] = set()
        by_class: Counter[str] = Counter()
        by_n: Counter[int] = Counter()
        for case in cases:
            query_tokens = tokens(case["query"])
            exact = bool(query_tokens) and " ".join(query_tokens) in prompt_normalized
            if exact:
                exact_cases.add(case["opaque_case_id"])
            for qstart, qend, gram, prompt_start in maximal_overlaps(query_tokens, indexes):
                n = qend - qstart
                line_no = token_lines[prompt_start]
                line = text.splitlines()[line_no - 1]
                kind = classify_line(line)
                by_class[kind] += 1
                by_n[n] += 1
                cases_with_overlap.add(case["opaque_case_id"])
                if n >= 4:
                    cases_with_long_overlap.add(case["opaque_case_id"])
                if kind in {
                    "surface_language_trigger_or_example",
                    "mixed_surface_and_technical",
                }:
                    all_problematic += 1
                matches.append({
                    "opaque_case_id": case["opaque_case_id"],
                    "query_sha256_utf8": case["query_sha256_utf8"],
                    "ngram_sha256_nfkc_casefold": sha_text(" ".join(gram)),
                    "ngram_tokens": n,
                    "prompt_line": line_no,
                    "class": kind,
                    "whole_query_occurs_in_prompt": exact,
                })
        per_dataset[dataset_name] = {
            "records": len(cases),
            "whole_query_occurs_in_prompt": len(exact_cases),
            "cases_with_maximal_overlap_n_ge_3": len(cases_with_overlap),
            "cases_with_maximal_overlap_n_ge_4": len(cases_with_long_overlap),
            "overlaps_by_class": dict(sorted(by_class.items())),
            "overlaps_by_ngram_tokens": {
                str(k): v for k, v in sorted(by_n.items())
            },
            "redacted_matches": matches,
        }
    inventory = line_inventory(text)
    eligible = (
        inventory["surface_or_mixed_line_count"] == 0
        and all_problematic == 0
    )
    return {
        "path": str(path),
        "sha256": sha_bytes(path.read_bytes()),
        "bytes": len(path.read_bytes()),
        "tokens": len(prompt_tokens),
        "line_inventory": inventory,
        "dataset_overlap": per_dataset,
        "maximum_objective_acceptance_credit_eligible": eligible,
        "acceptance_reason": (
            "No detected source-language trigger/example prose."
            if eligible
            else "Prompt contains source-language examples/mappings or overlaps them; a perfect score cannot establish the no-hardcode maximum objective."
        ),
    }

def load_query_corpus(path: Path) -> dict[str, list[dict[str, str]]]:
    value = json.loads(path.read_text())
    if value.get("contains_expected_or_gold") is not False:
        raise RuntimeError("audit corpus must be query-only")
    datasets = value.get("datasets")
    if not isinstance(datasets, dict):
        raise RuntimeError("audit corpus datasets are missing")
    expected_keys = {"opaque_case_id", "query_sha256_utf8", "query"}
    for name, rows in datasets.items():
        if not isinstance(rows, list) or any(set(row) != expected_keys for row in rows):
            raise RuntimeError(f"invalid query-only dataset: {name}")
        if any(sha_text(row["query"]) != row["query_sha256_utf8"] for row in rows):
            raise RuntimeError(f"query hash mismatch: {name}")
    return datasets
