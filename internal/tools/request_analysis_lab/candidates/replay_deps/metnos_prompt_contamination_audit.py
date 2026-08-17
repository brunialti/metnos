#!/usr/bin/env python3
"""Offline, redacted prompt-contamination audit for request-analysis candidates.

The report never serializes query text, prompt excerpts, or matched n-grams.
It reports opaque SHA-256 fingerprints, line numbers, lengths and classes only.
No model/server call is made.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


OUT_JSON = Path("/tmp/metnos_prompt_contamination_audit.json")
OUT_MD = Path("/tmp/metnos_prompt_contamination_audit.md")

PROMPTS = {
    "v25": Path("/tmp/metnos_v25_live.prompt.txt"),
    "v25.1-none-first": Path("/tmp/metnos_v251_live.prompt.txt"),
    "v25.1-active-first": Path("/tmp/metnos_v251_active_live.prompt.txt"),
    # This is the only successor present at audit time. It is deliberately not
    # relabelled V26.
    "v25.2-relation-goal": Path("/tmp/metnos_v252_relation_goal.prompt.txt"),
}

DATASETS = {
    "full109": {
        "kind": "glob",
        "pattern": "/tmp/metnos_v23lite_full_*.json",
    },
    "question-controls34": {
        "kind": "cases",
        "path": "/tmp/metnos_question_focus_negative_controls_v1.json",
    },
    "adversarial70": {
        "kind": "cases",
        "path": "/tmp/metnos_request_analysis_adversarial_v1.json",
    },
}

WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)

# These markers identify source-language examples or lexical-to-ontology
# mappings. They are not used for routing; they classify prompt prose only.
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


def dataset_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_datasets() -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    loaded: dict[str, list[dict[str, str]]] = {}
    manifests: dict[str, Any] = {}
    for name, spec in DATASETS.items():
        if spec["kind"] == "glob":
            paths = [Path(p) for p in sorted(glob.glob(spec["pattern"]))]
            rows: list[dict[str, Any]] = []
            for path in paths:
                rows.extend(json.loads(path.read_text()))
        else:
            paths = [Path(spec["path"])]
            rows = json.loads(paths[0].read_text())["cases"]
        cases = []
        for index, row in enumerate(rows, 1):
            query = row["query"]
            cases.append({
                "opaque_case_id": sha_text(f"{name}\0{row.get('id', index)}"),
                "query_sha256_utf8": sha_text(query),
                "query": query,
            })
        loaded[name] = cases
        manifests[name] = {
            "records": len(cases),
            "source_sha256": dataset_digest(paths),
            "source_files": [
                {"basename": p.name, "sha256": sha_bytes(p.read_bytes())}
                for p in paths
            ],
        }
    return loaded, manifests


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


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Redacted prompt anti-contamination audit",
        "",
        "Offline only; no model/server call. Query text, prompt excerpts and matched n-grams are omitted. Fingerprints are SHA-256.",
        "",
        "| Prompt | Surface/mixed lines | 109 exact / >=4 | 34 exact / >=4 | 70 exact / >=4 | Max-objective credit |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, value in report["prompts"].items():
        if value.get("status") == "missing":
            lines.append(f"| {name} | N/A | N/A | N/A | N/A | no (missing) |")
            continue
        inv = value["line_inventory"]["surface_or_mixed_line_count"]
        cells = []
        for dataset in ("full109", "question-controls34", "adversarial70"):
            row = value["dataset_overlap"][dataset]
            cells.append(
                f"{row['whole_query_occurs_in_prompt']} / {row['cases_with_maximal_overlap_n_ge_4']}"
            )
        credit = "yes" if value["maximum_objective_acceptance_credit_eligible"] else "no"
        lines.append(f"| {name} | {inv} | {cells[0]} | {cells[1]} | {cells[2]} | {credit} |")
    eligible = [
        name for name, value in report["prompts"].items()
        if value.get("maximum_objective_acceptance_credit_eligible") is True
    ]
    lines.extend([
        "",
        "Decision: prompts with surface-language examples or lexical mappings remain useful experimental measurements but receive no maximum-objective acceptance credit. Technical ontology labels/definitions are reported separately and are not, by themselves, contamination.",
        "",
        "Per-candidate static eligibility: " + (", ".join(eligible) if eligible else "none") + ". This is an anti-contamination gate, not an accuracy or production-cutover decision.",
        "",
        (
            "V26 artifact(s) were discovered and audited under their own versioned paths."
            if report["v26"]["status"] == "audited"
            else "No V26 artifact was present. V25.2 relation-goal is audited under its real version and is not silently relabelled V26."
        ),
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    datasets, manifests = load_datasets()
    prompts: dict[str, Any] = {}
    for name, path in PROMPTS.items():
        prompts[name] = (
            audit_prompt(path, datasets)
            if path.exists()
            else {"status": "missing", "path": str(path)}
        )
    v26_paths = sorted(Path("/tmp").glob("metnos_v26*.prompt.txt"))
    if v26_paths:
        for index, path in enumerate(v26_paths, 1):
            prompts[f"v26-discovered-{index}"] = audit_prompt(path, datasets)
        v26_status: dict[str, Any] = {
            "status": "audited", "count": len(v26_paths),
            "paths": [str(p) for p in v26_paths],
        }
    else:
        prompts["v26"] = {"status": "missing", "path_pattern": "/tmp/metnos_v26*.prompt.txt"}
        v26_status = {"status": "missing_not_audited", "count": 0}
    report = {
        "audit_version": "metnos.prompt-contamination-redacted/1.0",
        "network_calls": 0,
        "redaction": {
            "raw_queries_persisted": False,
            "prompt_excerpts_persisted": False,
            "matched_ngrams_persisted": False,
            "opaque_fingerprints": "sha256",
        },
        "method": {
            "normalization": "Unicode NFKC + casefold + Unicode word tokens",
            "overlap": "maximal contiguous token n-grams, 3..8; >=4 highlighted",
            "classification": [
                "technical_ontology",
                "surface_language_trigger_or_example",
                "mixed_surface_and_technical",
                "structural_or_common_prose",
            ],
            "limitation": "Marker-based static classification is conservative and requires manual review of hashed line numbers before production acceptance.",
        },
        "datasets": manifests,
        "v26": v26_status,
        "prompts": prompts,
        "global_decision": {
            "maximum_objective_acceptance_credit": False,
            "reason": "At least V25/V25.1 contain hardcoded surface-language examples or lexical mappings. A 100% fixture score would not isolate structured language-neutral generalization.",
            "experimental_runs_still_informative": True,
        },
        "tool_sha256": sha_bytes(Path(__file__).read_bytes()),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(report))
    print(json.dumps({
        "json": str(OUT_JSON), "json_sha256": sha_bytes(OUT_JSON.read_bytes()),
        "md": str(OUT_MD), "md_sha256": sha_bytes(OUT_MD.read_bytes()),
        "v26": v26_status["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
