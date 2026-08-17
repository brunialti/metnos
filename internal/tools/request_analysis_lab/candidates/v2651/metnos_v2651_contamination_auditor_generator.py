#!/usr/bin/env python3
"""Extract the pure redacted contamination auditor from its archived source."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
SOURCE = (
    REPOSITORY
    / "internal/tools/request_analysis_lab/candidates/replay_deps/"
      "metnos_prompt_contamination_audit.py"
)
TARGET = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_contamination_auditor.py"
)
EXPECTED_SOURCE_SHA256 = (
    "1cedfa7115744da79764c6b2fd31c2be67eada3fcc18d23bb46a5108eb190169"
)
ROOTS = {"audit_prompt", "classify_line"}
CONSTANTS = {"WORD_RE", "SURFACE_MARKER_RE", "TECHNICAL_MARKERS"}
BANNED = ("/tmp/", "urllib", "socket", "http.client", "glob.glob")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_nodes(tree: ast.Module) -> tuple[list[ast.stmt], list[ast.FunctionDef]]:
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    closure: set[str] = set()
    pending = list(ROOTS)
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure.add(name)
        calls = {
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        pending.extend(sorted((calls & functions.keys()) - closure))
    assignments = []
    found_constants: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if names & CONSTANTS:
            assignments.append(node)
            found_constants.update(names & CONSTANTS)
    if found_constants != CONSTANTS or len(closure) != 9:
        raise RuntimeError("unexpected pure auditor dependency closure")
    return assignments, sorted(
        (functions[name] for name in closure), key=lambda node: node.lineno,
    )


def source_slice(lines: list[str], node: ast.AST) -> str:
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def build_module() -> str:
    if sha256(SOURCE) != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("archived auditor hash mismatch")
    source = SOURCE.read_text()
    lines = source.splitlines()
    assignments, functions = selected_nodes(ast.parse(source))
    body_nodes = sorted([*assignments, *functions], key=lambda node: node.lineno)
    body = "\n\n".join(source_slice(lines, node) for node in body_nodes)
    header = f'''#!/usr/bin/env python3
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
SOURCE_AUDITOR_SHA256 = "{EXPECTED_SOURCE_SHA256}"

'''
    trailer = '''

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
'''
    result = header + body + trailer
    found = [value for value in BANNED if value in result]
    if found:
        raise RuntimeError(f"forbidden dependency in generated auditor: {found}")
    return result


def emit_patch(value: str) -> str:
    body = "\n".join("+" + line for line in value.splitlines())
    return (
        "*** Begin Patch\n"
        f"*** Add File: /opt/metnos/{TARGET}\n"
        f"{body}\n"
        "*** End Patch\n"
    )


if __name__ == "__main__":
    print(emit_patch(build_module()), end="")
