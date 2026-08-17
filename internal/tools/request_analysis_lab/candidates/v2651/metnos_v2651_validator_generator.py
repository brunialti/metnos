#!/usr/bin/env python3
"""Mechanically extract the pure V26.4.1 schema/validator closure.

The generator reads only hash-pinned repository files and emits an
``apply_patch`` patch on stdout.  It never imports or executes the historical
runner, so its transport and model code cannot become a transitive dependency.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
PARENT = (
    REPOSITORY
    / "internal/tools/request_analysis_lab/candidates/v2641/"
      "metnos_v2641_typed_phase1_runner.py"
)
REGISTRY = (
    REPOSITORY
    / "internal/tools/request_analysis_lab/candidates/v2641/"
      "metnos_v2641_typed_registry.json"
)
TARGET = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_self_contained_validator.py"
)
EXPECTED_PARENT_SHA256 = (
    "6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459"
)
EXPECTED_REGISTRY_SHA256 = (
    "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
)
ROOTS = {"live_schema", "validate_frame", "registry_errors"}
BANNED_RUNTIME_TEXT = (
    "/tmp/", "urllib", "endpoint", "call_once", "run_controls",
    "chat/completions",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependency_closure(tree: ast.Module) -> list[ast.FunctionDef]:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    missing = ROOTS - functions.keys()
    if missing:
        raise RuntimeError(f"missing extraction roots: {sorted(missing)}")

    selected: set[str] = set()
    pending = list(ROOTS)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        calls = {
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        pending.extend(sorted((calls & functions.keys()) - selected))
    return sorted((functions[name] for name in selected), key=lambda node: node.lineno)


def build_module() -> str:
    if sha256(PARENT) != EXPECTED_PARENT_SHA256:
        raise RuntimeError("historical parent hash mismatch")
    if sha256(REGISTRY) != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("registry hash mismatch")

    source = PARENT.read_text()
    lines = source.splitlines()
    functions = dependency_closure(ast.parse(source))
    names = [node.name for node in functions]
    body = "\n\n".join(
        "\n".join(lines[node.lineno - 1:node.end_lineno])
        for node in functions
    )
    header = f'''#!/usr/bin/env python3
"""Self-contained technical schema and validator for V26.5.1.

Mechanically extracted from the hash-pinned V26.4.1 semantic runner.  This
module contains no prompt, model request, retry, or network code.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


VERSION = "metnos.v26.5.1-self-contained-validator/0.1"
SOURCE_PARENT_SHA256 = "{EXPECTED_PARENT_SHA256}"
EXPECTED_REGISTRY_SHA256 = "{EXPECTED_REGISTRY_SHA256}"
EXTRACTED_FUNCTIONS = {names!r}
REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "v2641/metnos_v2641_typed_registry.json"
)
_REGISTRY_BYTES = REGISTRY_PATH.read_bytes()
if hashlib.sha256(_REGISTRY_BYTES).hexdigest() != EXPECTED_REGISTRY_SHA256:
    raise RuntimeError("registry hash mismatch before validator initialization")
REGISTRY: dict[str, Any] = json.loads(_REGISTRY_BYTES)
RELATIONS: dict[str, Any] = REGISTRY["relations"]
REFERENCES: dict[str, Any] = REGISTRY["reference_registry"]
PROOF_FAMILIES: dict[str, list[str]] = REGISTRY["proof_families"]
LIMITS: dict[str, int] = REGISTRY["limits"]

'''
    result = header + body + "\n"
    found = [term for term in BANNED_RUNTIME_TEXT if term in result]
    if found:
        raise RuntimeError(f"forbidden runtime text in generated module: {found}")
    if len(functions) != 23:
        raise RuntimeError(f"unexpected extraction closure size: {len(functions)}")
    return result


def emit_patch(module: str) -> str:
    added = "\n".join("+" + line for line in module.splitlines())
    return (
        "*** Begin Patch\n"
        f"*** Add File: /opt/metnos/{TARGET}\n"
        f"{added}\n"
        "*** End Patch\n"
    )


if __name__ == "__main__":
    print(emit_patch(build_module()), end="")
