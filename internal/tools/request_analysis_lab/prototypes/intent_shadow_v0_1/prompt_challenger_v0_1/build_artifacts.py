"""Build/check the immutable challenger before any new holdout exists."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
from typing import Any

from ..candidate_v0_2.canonical import canonical_sha256, file_sha256, load_json_file, pretty_json_bytes
from ..candidate_v0_2.registry_projection import load_projection, validate_projection
from ..candidate_v0_3 import build_artifacts as current_build
from .projection import (
    BASE_PROMPT_SHA256,
    BASE_ROOT_LINE,
    CHALLENGER_PROMPT_SHA256,
    COVERAGE_ROOT_LINES,
    FINAL_CHECK_COVERAGE_BULLET,
    FINAL_CHECK_HEADER,
    build_prompt,
    build_schema,
    contract_hashes,
)


HERE = Path(__file__).resolve().parent
PROTOTYPE_DIR = HERE.parent
CURRENT_DIR = PROTOTYPE_DIR / "candidate_v0_3"
STYLE_DIR = PROTOTYPE_DIR / "prompt_style_v0_1"
PROMPT_FILE = HERE / "intent_ir_prompt_challenger_v0_1.txt"
SCHEMA_FILE = HERE / "intent_ir_prompt_challenger_v0_1.schema.json"
DIFF_FILE = HERE / "prompt_diff_inventory_v0_1.json"
CHRONOLOGY_FILE = HERE / "pre_holdout_chronology_v0_1.json"
FREEZE_FILE = HERE / "prompt_challenger_v0_1.freeze.json"
FREEZE_FORMAT = "metnos.intent-prompt-challenger-freeze/0.1"

CURRENT_CRITICAL_FILES = (
    "candidate_v0_3.freeze.json",
    "intent_ir_v0_3.prompt.txt",
    "intent_ir_v0_3.schema.json",
    "build_artifacts.py",
    "projection.py",
    "structured_client.py",
)


def _package_files() -> list[Path]:
    return sorted(
        path
        for path in HERE.rglob("*")
        if path.is_file()
        and path != FREEZE_FILE
        and "__pycache__" not in path.parts
        and path.suffix in {".py", ".json", ".txt", ".md"}
    )


def _payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    payload["integrity"].pop("payload_sha256", None)
    return canonical_sha256(payload)


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["integrity"] = {"algorithm": "sha256", "payload_sha256": ""}
    document["integrity"]["payload_sha256"] = _payload_sha256(document)
    return document


def diff_inventory_document() -> dict[str, Any]:
    return _seal({
        "inventory_format": "metnos.intent-prompt-exact-diff/0.1",
        "source": {
            "arm": "S0_CURRENT",
            "candidate_path": "../candidate_v0_3/intent_ir_v0_3.prompt.txt",
            "style_path": "../prompt_style_v0_1/prompt_s0_current.txt",
            "sha256": BASE_PROMPT_SHA256,
        },
        "result": {
            "path": PROMPT_FILE.name,
            "utf8_bytes": 23320,
            "sha256": CHALLENGER_PROMPT_SHA256,
        },
        "exact_delta": {
            "removed_lines": [BASE_ROOT_LINE],
            "replacement_lines": list(COVERAGE_ROOT_LINES),
            "insertion_anchor": FINAL_CHECK_HEADER,
            "inserted_first_bullet": FINAL_CHECK_COVERAGE_BULLET,
            "removed_line_count": 1,
            "added_line_count": 4,
        },
        "invariants": {
            "all_other_prompt_lines_byte_identical": True,
            "positive_examples_byte_identical": True,
            "registry_projection_byte_identical": True,
            "schema_byte_identical": True,
            "core_and_adapter_inherited_unchanged": True,
            "language_path_shared_for_every_valid_bcp47_tag": True,
            "benchmark_query_or_identifier_added": False,
        },
    })


def chronology_document() -> dict[str, Any]:
    return _seal({
        "chronology_format": "metnos.intent-challenger-pre-holdout/0.1",
        "phase": "challenger_frozen_before_new_holdout",
        "observation_scope": "complete intent_shadow_v0_1 prototype namespace",
        "absence_predicate": "no relative path component starts with holdout or run4, case-insensitive",
        "observed_matches": [],
        "sequence_contract": {
            "challenger_must_be_frozen_before_holdout_materialization": True,
            "future_holdout_must_bind_exact_challenger_freeze_sha256": True,
            "future_holdout_inputs_are_not_build_inputs_here": True,
        },
        "source_prompt_sha256": BASE_PROMPT_SHA256,
        "challenger_prompt_sha256": CHALLENGER_PROMPT_SHA256,
    })


def _is_future_holdout_path(parts: tuple[str, ...]) -> bool:
    return any(part.casefold().startswith(("holdout", "run4")) for part in parts)


def find_future_holdout_paths(root: Path = PROTOTYPE_DIR) -> list[str]:
    matches: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _is_future_holdout_path(relative.parts):
            matches.append(relative.as_posix())
    return sorted(matches)


def check_pre_holdout() -> list[str]:
    matches = find_future_holdout_paths()
    if matches:
        raise SystemExit("new holdout/run4 material already exists")
    return []


def expected_artifacts() -> dict[Path, bytes]:
    registry = validate_projection(load_projection())
    source = (CURRENT_DIR / "intent_ir_v0_3.prompt.txt").read_bytes()
    style_source = (STYLE_DIR / "prompt_s0_current.txt").read_bytes()
    if source != style_source or file_sha256(CURRENT_DIR / "intent_ir_v0_3.prompt.txt") != BASE_PROMPT_SHA256:
        raise SystemExit("frozen S0 source mismatch")
    prompt = build_prompt(registry).encode("utf-8")
    if len(prompt) != 23320 or file_sha256_bytes(prompt) != CHALLENGER_PROMPT_SHA256:
        raise SystemExit("challenger prompt contract mismatch")
    schema = pretty_json_bytes(build_schema(registry))
    if schema != (CURRENT_DIR / "intent_ir_v0_3.schema.json").read_bytes():
        raise SystemExit("challenger schema differs from S0")
    return {
        PROMPT_FILE: prompt,
        SCHEMA_FILE: schema,
        DIFF_FILE: pretty_json_bytes(diff_inventory_document()),
        CHRONOLOGY_FILE: pretty_json_bytes(chronology_document()),
    }


def file_sha256_bytes(content: bytes) -> str:
    from hashlib import sha256
    return sha256(content).hexdigest()


def build_freeze() -> dict[str, Any]:
    registry = validate_projection(load_projection())
    current_freeze = load_json_file(CURRENT_DIR / "candidate_v0_3.freeze.json")
    document = {
        "freeze_format": FREEZE_FORMAT,
        "status": "offline_challenger_frozen_pre_holdout_not_runtime",
        "current_candidate": {
            "freeze_sha256": file_sha256(CURRENT_DIR / "candidate_v0_3.freeze.json"),
            "contract": current_freeze["contract"],
            "critical_files": {
                name: file_sha256(CURRENT_DIR / name) for name in CURRENT_CRITICAL_FILES
            },
        },
        "source_s0": {
            "candidate_prompt_sha256": file_sha256(CURRENT_DIR / "intent_ir_v0_3.prompt.txt"),
            "style_prompt_sha256": file_sha256(STYLE_DIR / "prompt_s0_current.txt"),
            "expected_sha256": BASE_PROMPT_SHA256,
        },
        "contract": contract_hashes(registry),
        "files": {str(path.relative_to(HERE)): file_sha256(path) for path in _package_files()},
        "constraints": {
            "logical_change": ["exact_coverage_root_prompt_delta_only"],
            "removed_prompt_lines": 1,
            "added_prompt_lines": 4,
            "schema_changed": False,
            "registry_changed": False,
            "validator_changed": False,
            "compiler_changed": False,
            "adapter_semantics_changed": False,
            "positive_examples_changed": False,
            "holdout_present_at_freeze": False,
            "gpu_calls": 0,
            "network_calls": 0,
            "runtime_files_changed": False,
        },
        "chronology": {
            "proof_file": CHRONOLOGY_FILE.name,
            "proof_sha256": file_sha256(CHRONOLOGY_FILE),
            "future_holdout_must_bind_this_freeze": True,
        },
        "integrity": {"algorithm": "sha256", "payload_sha256": ""},
    }
    document["integrity"]["payload_sha256"] = _payload_sha256(document)
    return document


def _apply(path: Path, content: bytes) -> None:
    text = content.decode("utf-8").rstrip("\n")
    if path.is_file() and path.read_bytes() == content:
        return
    if path.exists():
        subprocess.run(
            ["apply_patch"],
            input=f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n",
            text=True,
            check=True,
        )
    additions = "\n".join("+" + line for line in text.split("\n"))
    subprocess.run(
        ["apply_patch"],
        input=f"*** Begin Patch\n*** Add File: {path}\n{additions}\n*** End Patch\n",
        text=True,
        check=True,
    )


def write() -> None:
    errors = current_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("S0 candidate verification failed")
    check_pre_holdout()
    for path, content in expected_artifacts().items():
        _apply(path, content)
    _apply(FREEZE_FILE, pretty_json_bytes(build_freeze()))


def check() -> list[str]:
    errors = current_build.check()
    if type(errors) is not list or errors != []:
        raise SystemExit("S0 candidate verification failed")
    for path, content in expected_artifacts().items():
        if not path.is_file() or path.read_bytes() != content:
            raise SystemExit(f"stale challenger artifact:{path.name}")
    freeze = load_json_file(FREEZE_FILE)
    if freeze != build_freeze():
        raise SystemExit("challenger freeze drift")
    if freeze.get("integrity", {}).get("payload_sha256") != _payload_sha256(freeze):
        raise SystemExit("challenger freeze payload mismatch")
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else write()


if __name__ == "__main__":
    main()
